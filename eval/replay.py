"""
EdgeHealth eval harness.

Replays one PPG-DaLiA subject's HR series at accelerated cadence into:
  (a) the Pi gateway  -> measures edge alert latency
  (b) the cloud baseline endpoint -> measures cloud RTT alert latency

Injects synthetic anomalies at known indices to compute precision/recall.
Logs Pi /status every 10 s for resource utilization.
Emits results.json + plots.

Usage:
  python replay.py --subject S1 \
                   --root "C:/Users/nikhi/Desktop/PPG_DaLiA/PPG_FieldStudy" \
                   --pi   http://10.0.0.153:8000 \
                   --cloud https://edgehealth-cloud.onrender.com \
                   --duration 600   # seconds of accelerated replay
"""

from __future__ import annotations

import argparse
import json
import pickle
import statistics
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests


def load_hr(root: Path, subject: str) -> np.ndarray:
    pkl = root / subject / f"{subject}.pkl"
    with open(pkl, "rb") as f:
        d = pickle.load(f, encoding="latin1")
    hr = np.asarray(d["label"], dtype=float).ravel()
    return hr[(hr > 30) & (hr < 220)]


def inject_anomalies(hr: np.ndarray, n: int, seed: int = 42) -> tuple[np.ndarray, set[int]]:
    """Replace n random indices with values < 40 or > 180 to force threshold trips."""
    rng = np.random.default_rng(seed)
    idx = set(rng.choice(len(hr), size=n, replace=False).tolist())
    out = hr.copy()
    for i in idx:
        out[i] = 35.0 if rng.random() < 0.5 else 185.0
    return out, idx


def resource_poller(pi: str, samples: list, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            r = requests.get(f"{pi}/status", timeout=3).json()
            samples.append({
                "t": time.time(),
                "cpu": r.get("cpu_percent", 0),
                "rss": r.get("rss_mb", 0),
            })
        except Exception:
            pass
        stop.wait(10.0)


def run_edge(pi: str, hr: np.ndarray, anom_idx: set[int]) -> dict:
    """POST one reading at a time to Pi, force /detect, scrape /alerts."""
    print(f"[edge] starting, {len(hr)} readings")
    # baseline alert id watermark
    pre = requests.get(f"{pi}/alerts", timeout=5).json()
    watermark = max((a["id"] for a in pre), default=0)

    latencies = []
    detected = set()
    t0 = time.time()
    for i, v in enumerate(hr):
        sent_at = time.time()
        ts_iso = datetime.fromtimestamp(sent_at, tz=timezone.utc).isoformat()
        body = {"readings": [{"ts": ts_iso, "metric": "hr", "value": float(v)}]}
        try:
            requests.post(f"{pi}/ingest", json=body, timeout=5)
            requests.post(f"{pi}/detect", timeout=5)
        except requests.RequestException as e:
            print(f"  [edge] error at {i}: {e}")
            continue
        # Scrape new alerts
        alerts = requests.get(f"{pi}/alerts", timeout=5).json()
        for a in alerts:
            if a["id"] <= watermark:
                continue
            watermark = max(watermark, a["id"])
            recv_at = time.time()
            lat = (recv_at - sent_at) * 1000
            latencies.append(lat)
            detected.add(i)
            print(f"  [edge] alert i={i} v={v:.0f} lat={lat:.0f}ms")
        if i % 50 == 0:
            print(f"  [edge] {i}/{len(hr)}")
    dt = time.time() - t0
    return {
        "mode": "edge",
        "n": len(hr),
        "duration_sec": dt,
        "latencies_ms": latencies,
        "detected": sorted(detected),
        "ground_truth": sorted(anom_idx),
    }


def run_cloud(cloud: str, hr: np.ndarray, anom_idx: set[int]) -> dict:
    """POST each reading directly to cloud /cloud_detect, time RTT for alert."""
    print(f"[cloud] starting, {len(hr)} readings")
    # warm up cold start
    try:
        requests.get(f"{cloud}/health", timeout=90)
    except Exception:
        pass

    latencies = []
    detected = set()
    t0 = time.time()
    for i, v in enumerate(hr):
        sent_at = time.time()
        try:
            r = requests.post(f"{cloud}/cloud_detect",
                              json={"metric": "hr", "value": float(v)},
                              timeout=10).json()
        except requests.RequestException as e:
            print(f"  [cloud] error at {i}: {e}")
            continue
        recv_at = time.time()
        if r.get("alert"):
            lat = (recv_at - sent_at) * 1000
            latencies.append(lat)
            detected.add(i)
            print(f"  [cloud] alert i={i} v={v:.0f} lat={lat:.0f}ms")
        if i % 50 == 0:
            print(f"  [cloud] {i}/{len(hr)}")
    dt = time.time() - t0
    return {
        "mode": "cloud",
        "n": len(hr),
        "duration_sec": dt,
        "latencies_ms": latencies,
        "detected": sorted(detected),
        "ground_truth": sorted(anom_idx),
    }


def metrics(result: dict) -> dict:
    gt = set(result["ground_truth"])
    det = set(result["detected"])
    tp = len(gt & det)
    fp = len(det - gt)
    fn = len(gt - det)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    lats = result["latencies_ms"]
    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "lat_mean_ms": round(statistics.mean(lats), 1) if lats else None,
        "lat_p50_ms": round(statistics.median(lats), 1) if lats else None,
        "lat_p95_ms": round(np.percentile(lats, 95), 1) if lats else None,
        "lat_max_ms": round(max(lats), 1) if lats else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--subject", default="S1")
    ap.add_argument("--pi", default="http://10.0.0.153:8000")
    ap.add_argument("--cloud", default="https://edgehealth-cloud.onrender.com")
    ap.add_argument("--n_readings", type=int, default=200,
                    help="How many HR samples to replay (each = one Pi POST).")
    ap.add_argument("--n_anomalies", type=int, default=20)
    ap.add_argument("--out", default="results.json")
    args = ap.parse_args()

    hr_full = load_hr(Path(args.root), args.subject)
    hr = hr_full[:args.n_readings]
    hr_inj, anom_idx = inject_anomalies(hr, args.n_anomalies)

    print(f"Replaying subject={args.subject} n={len(hr_inj)} anomalies={len(anom_idx)}")
    print(f"HR stats: mean={hr_inj.mean():.1f} min={hr_inj.min():.1f} max={hr_inj.max():.1f}")

    # Resource poller during edge run
    res_samples: list = []
    stop = threading.Event()
    t = threading.Thread(target=resource_poller,
                         args=(args.pi, res_samples, stop), daemon=True)
    t.start()

    edge_result = run_edge(args.pi, hr_inj, anom_idx)

    stop.set()
    t.join(timeout=2)

    cloud_result = run_cloud(args.cloud, hr_inj, anom_idx)

    out = {
        "config": vars(args),
        "edge": {**edge_result, **metrics(edge_result)},
        "cloud": {**cloud_result, **metrics(cloud_result)},
        "resources": res_samples,
        "bandwidth": {
            "raw_per_day_bytes": int(86400 / 60 * 90),  # 1 reading / 60s, ~90B JSON
            "summary_per_day_bytes": 220,
            "reduction_pct": round((1 - 220 / (86400 / 60 * 90)) * 100, 2),
        },
    }
    with open(args.out, "w") as f:
        json.dump(out, f, indent=2, default=str)

    print("\n=== RESULTS ===")
    for k in ("edge", "cloud"):
        m = out[k]
        print(f"{k}: lat mean={m['lat_mean_ms']}ms p95={m['lat_p95_ms']}ms  "
              f"P/R/F1={m['precision']}/{m['recall']}/{m['f1']}  "
              f"detected={len(m['detected'])}/{len(anom_idx)}")
    if res_samples:
        cpus = [r["cpu"] for r in res_samples]
        rss = [r["rss"] for r in res_samples]
        print(f"Pi resources: CPU mean={statistics.mean(cpus):.1f}% "
              f"RSS mean={statistics.mean(rss):.1f}MB peak={max(rss):.1f}MB")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
