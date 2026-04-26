"""Plot eval results: latency comparison, resource utilization."""

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="results.json")
    ap.add_argument("--outdir", default=".")
    args = ap.parse_args()

    r = json.load(open(args.inp))
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # 1. Latency histogram
    fig, ax = plt.subplots(figsize=(7, 4))
    edge = r["edge"]["latencies_ms"]
    cloud = r["cloud"]["latencies_ms"]
    bins = np.linspace(0, max(max(edge or [1]), max(cloud or [1])), 30)
    ax.hist(edge, bins=bins, alpha=0.6, label=f"Edge (n={len(edge)})", color="tab:blue")
    ax.hist(cloud, bins=bins, alpha=0.6, label=f"Cloud (n={len(cloud)})", color="tab:orange")
    ax.set_xlabel("Alert latency (ms)")
    ax.set_ylabel("Count")
    ax.set_title("Alert latency: Edge vs. Cloud baseline")
    ax.legend()
    fig.tight_layout()
    fig.savefig(outdir / "latency_hist.png", dpi=120)

    # 2. Latency bar (mean / p95)
    fig, ax = plt.subplots(figsize=(5, 4))
    cats = ["mean", "p95"]
    edge_v = [r["edge"]["lat_mean_ms"] or 0, r["edge"]["lat_p95_ms"] or 0]
    cloud_v = [r["cloud"]["lat_mean_ms"] or 0, r["cloud"]["lat_p95_ms"] or 0]
    x = np.arange(len(cats))
    w = 0.35
    ax.bar(x - w/2, edge_v, w, label="Edge", color="tab:blue")
    ax.bar(x + w/2, cloud_v, w, label="Cloud", color="tab:orange")
    ax.set_xticks(x); ax.set_xticklabels(cats)
    ax.set_ylabel("Latency (ms)")
    ax.set_title("Alert latency summary")
    for i, (e, c) in enumerate(zip(edge_v, cloud_v)):
        ax.text(i - w/2, e, f"{e:.0f}", ha="center", va="bottom", fontsize=9)
        ax.text(i + w/2, c, f"{c:.0f}", ha="center", va="bottom", fontsize=9)
    ax.legend()
    fig.tight_layout()
    fig.savefig(outdir / "latency_bar.png", dpi=120)

    # 3. Resource utilization timeline
    res = r["resources"]
    if res:
        t0 = res[0]["t"]
        ts = [(s["t"] - t0) for s in res]
        cpu = [s["cpu"] for s in res]
        rss = [s["rss"] for s in res]
        fig, ax1 = plt.subplots(figsize=(7, 4))
        ax1.plot(ts, cpu, "tab:red", label="CPU %")
        ax1.set_xlabel("Time (s)")
        ax1.set_ylabel("CPU %", color="tab:red")
        ax2 = ax1.twinx()
        ax2.plot(ts, rss, "tab:green", label="RSS (MB)")
        ax2.set_ylabel("RSS (MB)", color="tab:green")
        ax1.set_title("Pi resource utilization during edge replay")
        fig.tight_layout()
        fig.savefig(outdir / "resources.png", dpi=120)

    print(f"Plots written to {outdir}")
    print("Summary:")
    for k in ("edge", "cloud"):
        m = r[k]
        print(f"  {k:5s} lat mean={m['lat_mean_ms']}ms p95={m['lat_p95_ms']}ms  "
              f"P/R/F1={m['precision']}/{m['recall']}/{m['f1']}")
    print(f"  bandwidth reduction: {r['bandwidth']['reduction_pct']}%")


if __name__ == "__main__":
    main()
