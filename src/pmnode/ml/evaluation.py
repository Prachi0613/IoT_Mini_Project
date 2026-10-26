"""Reproducible statistical-baseline versus Isolation-Forest evaluation."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score, roc_auc_score

from .model import ModelBundle

DECLARATION = "SYNTHETIC SOFTWARE VALIDATION — NOT REAL MACHINE PERFORMANCE."


def _metrics(labels: np.ndarray, scores: np.ndarray, threshold: float) -> dict:
    predicted = scores > threshold
    result = {
        "precision": float(precision_score(labels, predicted, zero_division=0)),
        "recall": float(recall_score(labels, predicted, zero_division=0)),
        "f1": float(f1_score(labels, predicted, zero_division=0)),
        "confusion_matrix": confusion_matrix(labels, predicted, labels=[False, True]).tolist(),
        "window_false_positive_rate": float(np.mean(predicted[~labels])) if np.any(~labels) else None,
        "alert_count": int(np.sum(predicted)),
    }
    result["roc_auc"] = float(roc_auc_score(labels, scores)) if len(np.unique(labels)) == 2 else None
    return result


def evaluate_results(bundle: ModelBundle, results: list[dict], output_dir: str | Path,
                     labels: list[bool] | None = None,
                     event_onset_us: int | None = None) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    isolation = np.asarray([row["isolation_score"] for row in results])
    reference = np.asarray([row["reference_score"] for row in results])
    starts = np.asarray([row["window"].window_start_us for row in results])
    report = {
        "declaration": DECLARATION,
        "model_version": bundle.model_version,
        "source_type": bundle.source_type,
        "feature_schema_version": bundle.feature_schema_version,
        "feature_order": bundle.feature_order,
        "model_parameters": {"n_estimators": bundle.metadata["n_estimators"],
                             "max_samples": bundle.metadata["max_samples"],
                             "random_state": bundle.metadata["random_state"]},
        "training_sessions": bundle.training_sessions,
        "calibration_sessions": bundle.calibration_sessions,
        "threshold_calibration": f"healthy calibration quantile {bundle.metadata['threshold_quantile']}",
        "window_count": len(results),
        "accepted_feature_windows": len(results),
        "isolation_forest": {"threshold": bundle.isolation_threshold,
                             "score_min": float(np.min(isolation)),
                             "score_max": float(np.max(isolation))},
        "robust_statistical_baseline": {"threshold": bundle.reference.threshold,
                                        "score_min": float(np.min(reference)),
                                        "score_max": float(np.max(reference))},
    }
    if labels is not None:
        label_array = np.asarray(labels, dtype=bool)
        if len(label_array) != len(results):
            raise ValueError("label count must match scored windows")
        report["isolation_forest"]["metrics"] = _metrics(
            label_array, isolation, bundle.isolation_threshold)
        report["robust_statistical_baseline"]["metrics"] = _metrics(
            label_array, reference, bundle.reference.threshold)
        if event_onset_us is not None:
            detected = starts[(starts >= event_onset_us) & (isolation > bundle.isolation_threshold)]
            report["isolation_forest"]["event_detection_delay_us"] = (
                int(detected[0] - event_onset_us) if len(detected) else None)
        report["labels"] = "simulator scenario timing used as synthetic software-test ground truth"
    else:
        report["labels"] = "unavailable; classification metrics intentionally omitted"

    (output_dir / "evaluation_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    plt.figure(figsize=(8, 3.5))
    plt.plot(starts / 1e6, isolation, label="Isolation Forest score")
    plt.axhline(bundle.isolation_threshold, color="red", linestyle="--", label="threshold")
    plt.xlabel("Producer monotonic time (s)"); plt.ylabel("Anomaly score (not probability)")
    plt.legend(); plt.tight_layout(); plt.savefig(output_dir / "anomaly_scores.png", dpi=120); plt.close()
    plt.figure(figsize=(8, 3.5))
    trend = [row["window"].vibration_rms_g for row in results]
    plt.plot(starts / 1e6, trend)
    plt.xlabel("Producer monotonic time (s)"); plt.ylabel("Vibration RMS (g)")
    plt.tight_layout(); plt.savefig(output_dir / "vibration_rms_trend.png", dpi=120); plt.close()
    if labels is not None:
        plt.figure(figsize=(5, 3.5))
        plt.boxplot([isolation[~label_array], isolation[label_array]],
                    tick_labels=["synthetic healthy", "synthetic anomaly"])
        plt.ylabel("Isolation Forest anomaly score"); plt.tight_layout()
        plt.savefig(output_dir / "model_comparison.png", dpi=120); plt.close()
    return report
