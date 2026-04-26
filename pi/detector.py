"""
Hybrid anomaly detector: clinical thresholds (Layer 1) + Isolation Forest (Layer 2).

Thresholds always evaluated. IForest used only if model.pkl is present;
falls back to thresholds-only otherwise.
"""

from __future__ import annotations

import pickle
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

MODEL_PATH = Path(__file__).parent / "model.pkl"


@dataclass
class Anomaly:
    severity: str          # "critical" | "warning"
    kind: str              # "threshold_hr_low", "iforest", etc.
    metric: str
    value: float
    message: str
    latency_ms: float


# Clinical hard limits
HR_LOW = 40
HR_HIGH = 180
SPO2_LOW = 90
SILENCE_SEC = 600  # 10 minutes


class Detector:
    def __init__(self) -> None:
        self.model = None
        if MODEL_PATH.exists():
            with open(MODEL_PATH, "rb") as f:
                self.model = pickle.load(f)
            print(f"[detector] Loaded IForest from {MODEL_PATH}")
        else:
            print("[detector] No model.pkl — thresholds only")

    def check_thresholds(self, metric: str, value: float) -> Optional[Anomaly]:
        t0 = time.perf_counter()
        if metric == "hr":
            if value < HR_LOW:
                return Anomaly("critical", "threshold_hr_low", metric, value,
                               f"HR {value:.0f} below {HR_LOW} BPM",
                               (time.perf_counter() - t0) * 1000)
            if value > HR_HIGH:
                return Anomaly("critical", "threshold_hr_high", metric, value,
                               f"HR {value:.0f} above {HR_HIGH} BPM",
                               (time.perf_counter() - t0) * 1000)
        elif metric == "spo2":
            if value < SPO2_LOW:
                return Anomaly("critical", "threshold_spo2_low", metric, value,
                               f"SpO2 {value:.0f}% below {SPO2_LOW}%",
                               (time.perf_counter() - t0) * 1000)
        return None

    def check_silence(self, last_ts_age_sec: float) -> Optional[Anomaly]:
        if last_ts_age_sec > SILENCE_SEC:
            return Anomaly("warning", "silence", "stream", last_ts_age_sec,
                           f"No data for {last_ts_age_sec:.0f}s",
                           0.0)
        return None

    def check_iforest(self, features: np.ndarray) -> Optional[Anomaly]:
        """features: shape (1, n_features) — [hr, dhr, step_rate, hour_of_day]."""
        if self.model is None:
            return None
        t0 = time.perf_counter()
        pred = self.model.predict(features)[0]
        score = self.model.decision_function(features)[0]
        elapsed = (time.perf_counter() - t0) * 1000
        if pred == -1:
            return Anomaly(
                "warning", "iforest", "hr", float(features[0, 0]),
                f"IForest anomaly score={score:.3f}",
                elapsed,
            )
        return None
