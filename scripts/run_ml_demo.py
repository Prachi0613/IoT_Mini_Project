"""Run one bounded, reproducible Task 02 synthetic software demonstration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from pmnode.ml.adaptation import AdaptationPolicy, ControlledAdapter
from pmnode.ml.evaluation import DECLARATION, evaluate_results
from pmnode.ml.health import HealthState
from pmnode.ml.pipeline import extract_session, score_sessions, train_from_storage
from pmnode.ml.registry import MLRegistry
from pmnode.simulator import Scenario, SensorSimulator, SimulationConfig
from pmnode.storage import Storage


SESSION_SPECS = [
    ("healthy-train-a", Scenario.HEALTHY, 60.0, 101),
    ("healthy-train-b", Scenario.HEALTHY, 60.0, 102),
    ("healthy-calibration", Scenario.HEALTHY, 40.0, 201),
    ("healthy-test", Scenario.HEALTHY, 30.0, 301),
    ("anomaly-test", Scenario.VIBRATION_ANOMALY, 30.0, 302),
    ("healthy-adaptation", Scenario.HEALTHY, 60.0, 401),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=Path("experiment-output/task02-synthetic"))
    parser.add_argument("--sample-rate", type=int, default=500)
    args = parser.parse_args()
    output = args.output_dir
    database = output / "demo.sqlite3"
    if database.exists():
        parser.error(f"refusing to overwrite existing demonstration database: {database}")
    output.mkdir(parents=True, exist_ok=True)
    storage = Storage(database); runs = {}
    raw_count = 0
    for boot_id, scenario, duration, seed in SESSION_SPECS:
        run = SensorSimulator(
            SimulationConfig(scenario=scenario, duration_s=duration,
                             sample_hz=args.sample_rate, seed=seed,
                             device_id="task02-sim-node", batch_duration_s=1.0),
            boot_id=boot_id,
        ).generate()
        runs[boot_id] = run
        for batch in run.batches:
            storage.insert_batch(batch)
            raw_count += len(batch.motion_samples)
        extract_session(storage, ("task02-sim-node", boot_id))

    model_path = output / "models" / "isolation-forest-v1.joblib"
    training = [("task02-sim-node", "healthy-train-a"),
                ("task02-sim-node", "healthy-train-b")]
    calibration = [("task02-sim-node", "healthy-calibration")]
    bundle = train_from_storage(storage, training, calibration, model_path,
                                model_version="isolation-forest-v1", random_state=42)
    registry = MLRegistry(storage)
    model_status = registry.activate_model(bundle.model_version)
    test_sessions = [("task02-sim-node", "healthy-test"),
                     ("task02-sim-node", "anomaly-test")]
    results = score_sessions(storage, test_sessions, model_path)
    onset = int(runs["anomaly-test"].labels[0]["start_mono_us"])
    labels = [row["window"].boot_id == "anomaly-test" and
              row["window"].window_end_us > onset for row in results]
    report = evaluate_results(bundle, results, output / "evaluation", labels, onset)

    adapter = ControlledAdapter(
        bundle, registry,
        AdaptationPolicy(enabled=True, minimum_candidates=10,
                         isolation_margin=0.98, reference_margin=0.98,
                         learning_rate=0.05),
    )
    before = adapter.reference.center.copy(); activated = None
    adaptation_results = score_sessions(
        storage, [("task02-sim-node", "healthy-adaptation")], model_path,
        persist=False)
    for result in adaptation_results:
        decision = adapter.consider(
            result["window"], result["isolation_score"], result["reference_score"],
            HealthState(result["health_state"]), approve=True)
        if decision.decision == "ACTIVATED":
            activated = decision
            break
    abnormal_results = score_sessions(
        storage, [("task02-sim-node", "anomaly-test")], model_path, persist=False)
    blocked = [adapter.consider(row["window"], row["isolation_score"],
                               row["reference_score"], HealthState(row["health_state"]),
                               approve=True)
               for row in abnormal_results]
    version_after_update = adapter.version
    changed = not np.array_equal(before, adapter.reference.center)
    adapter.rollback(1)
    rollback_exact = np.array_equal(before, adapter.reference.center)
    with storage.connect() as connection:
        counts = {
            "raw_motion_samples": connection.execute("SELECT COUNT(*) FROM motion_samples").fetchone()[0],
            "feature_windows": connection.execute("SELECT COUNT(*) FROM feature_windows").fetchone()[0],
            "predictions": connection.execute("SELECT COUNT(*) FROM model_predictions").fetchone()[0],
            "health_decisions": connection.execute("SELECT COUNT(*) FROM health_decisions").fetchone()[0],
        }
    summary = {
        "declaration": DECLARATION,
        "sessions": len(SESSION_SPECS), "session_specs": [
            {"boot_id": boot, "scenario": scenario.value, "duration_s": duration,
             "seed": seed} for boot, scenario, duration, seed in SESSION_SPECS],
        "sample_rate_hz": args.sample_rate, "raw_count_expected": raw_count,
        "counts": counts, "model_path": str(model_path),
        "model_status": model_status,
        "isolation_threshold": bundle.isolation_threshold,
        "reference_threshold": bundle.reference.threshold,
        "evaluation": report,
        "adaptation": {
            "activated": activated is not None,
            "activated_decision": activated.__dict__ if activated else None,
            "reference_parameters_changed": changed,
            "version_after_update": version_after_update,
            "abnormal_rejections": sum(item.decision == "REJECTED" for item in blocked),
            "rollback_target": 1, "rollback_exact": rollback_exact,
        },
    }
    (output / "demo_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if activated and changed and rollback_exact else 2


if __name__ == "__main__":
    raise SystemExit(main())
