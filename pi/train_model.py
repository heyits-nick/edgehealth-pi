"""
Train an Isolation Forest on PPG-DaLiA HR labels.

PPG-DaLiA pkl format (per subject):
  data['label']     1-D array of ground-truth HR (BPM), 2 Hz
  data['activity']  activity-class array (we use to filter to "rest"-ish)

We treat all normal physiological readings as inliers and let IForest learn
the joint distribution of (HR, dHR, hour-of-day-proxy).

Usage:
  python train_model.py --root "C:/Users/nikhi/Desktop/PPG_DaLiA/PPG_FieldStudy"
                        --out  ./model.pkl
"""

from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

import numpy as np
from sklearn.ensemble import IsolationForest


def load_subject(pkl_path: Path) -> np.ndarray:
    """Return HR series (BPM) for one subject."""
    with open(pkl_path, "rb") as f:
        data = pickle.load(f, encoding="latin1")
    hr = np.asarray(data["label"], dtype=float).ravel()
    # Drop NaN / sentinel readings
    hr = hr[(hr > 30) & (hr < 220)]
    return hr


def build_features(hr: np.ndarray) -> np.ndarray:
    """Features: [hr, dhr, step_rate_proxy, hour_of_day_proxy]."""
    if len(hr) < 2:
        return np.empty((0, 4))
    dhr = np.diff(hr, prepend=hr[0])
    # No step data in PPG-DaLiA labels; use 0 placeholder (matches inference time).
    step_rate = np.zeros_like(hr)
    # Hour-of-day proxy: fake a 24h cycle across the recording.
    hours = (np.arange(len(hr)) / len(hr)) * 24.0
    return np.column_stack([hr, dhr, step_rate, hours])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True,
                    help="Path to PPG_FieldStudy directory containing S1..S15.")
    ap.add_argument("--out", default="model.pkl",
                    help="Output pickle path.")
    ap.add_argument("--contamination", type=float, default=0.05)
    args = ap.parse_args()

    root = Path(args.root)
    pkls = sorted(root.glob("S*/S*.pkl"))
    if not pkls:
        print(f"No S*/S*.pkl under {root}", file=sys.stderr)
        return 1

    feats_all = []
    for pkl in pkls:
        hr = load_subject(pkl)
        feats = build_features(hr)
        print(f"  {pkl.parent.name}: {len(feats)} samples")
        feats_all.append(feats)

    X = np.concatenate(feats_all, axis=0)
    print(f"Total training samples: {len(X)}")
    print(f"HR mean={X[:,0].mean():.1f}  std={X[:,0].std():.1f}  "
          f"min={X[:,0].min():.1f}  max={X[:,0].max():.1f}")

    model = IsolationForest(
        n_estimators=100,
        contamination=args.contamination,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X)

    with open(args.out, "wb") as f:
        pickle.dump(model, f)
    print(f"Saved IForest -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
