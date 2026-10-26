"""Run a bounded Task 03 persisted-data-to-dashboard-API demonstration."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pmnode.api import create_app
from pmnode.live.monitor import MonitorService
from pmnode.live.processor import LiveProcessor
from pmnode.ml.adaptation import ControlledAdapter
from pmnode.ml.pipeline import extract_session, train_from_storage
from pmnode.ml.registry import MLRegistry
from pmnode.simulator import Scenario, SensorSimulator, SimulationConfig
from pmnode.contracts import TelemetryBatch
from pmnode.storage import BatchConflictError, Storage


LIVE_SESSIONS = (
    ("healthy-live", Scenario.HEALTHY, 6.0, 301),
    ("anomaly-live", Scenario.VIBRATION_ANOMALY, 10.0, 302),
    ("transient-live", Scenario.TRANSIENT_DISTURBANCE, 6.0, 303),
    ("dropout-live", Scenario.SENSOR_DROPOUT, 6.0, 304),
)


def generate(storage, boot_id, scenario, duration, seed, sample_rate):
    run = SensorSimulator(SimulationConfig(
        scenario=scenario, duration_s=duration, sample_hz=sample_rate,
        seed=seed, device_id="task03-sim-node", batch_duration_s=1.0,
    ), boot_id=boot_id).generate()
    for batch in run.batches:
        storage.insert_batch(batch)
    return run


def table_counts(storage):
    with storage.connect() as connection:
        names = ("ingested_batches", "motion_samples", "environment_samples",
                 "feature_windows", "model_predictions", "health_decisions",
                 "health_events", "processing_issues")
        result = {name: connection.execute(
            f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in names}
        result["health_states"] = {row[0]: row[1] for row in connection.execute(
            "SELECT health_state,COUNT(*) FROM health_decisions GROUP BY health_state")}
        result["events_by_type"] = {row[0]: row[1] for row in connection.execute(
            "SELECT event_type,COUNT(*) FROM health_events GROUP BY event_type")}
        result["checkpoint_states"] = {row[0]: row[1] for row in connection.execute(
            "SELECT processing_status,COUNT(*) FROM processing_checkpoints "
            "GROUP BY processing_status")}
        result["invalid_environment_records"] = connection.execute(
            "SELECT COUNT(*) FROM environment_samples WHERE valid=0").fetchone()[0]
    return result


def session_diagnostics(storage, sessions):
    """Return auditable scores and temporal decisions without re-scoring data."""
    scenario_by_boot = {row["boot_id"]: row["scenario"] for row in sessions}
    diagnostics = []
    with storage.connect() as connection:
        for boot_id, scenario in scenario_by_boot.items():
            rows = connection.execute(
                """SELECT f.window_start_us,f.window_end_us,f.payload_json,
                p.model_version,p.reference_version,p.isolation_score,
                p.isolation_threshold,p.reference_score,p.reference_threshold,
                h.health_state
                FROM feature_windows f
                LEFT JOIN model_predictions p ON p.device_id=f.device_id
                  AND p.boot_id=f.boot_id
                  AND p.window_start_us=f.window_start_us
                LEFT JOIN health_decisions h ON h.device_id=f.device_id
                  AND h.boot_id=f.boot_id
                  AND h.window_start_us=f.window_start_us
                WHERE f.device_id=? AND f.boot_id=?
                ORDER BY f.window_start_us""",
                ("task03-sim-node", boot_id),
            ).fetchall()
            events = [dict(row) for row in connection.execute(
                "SELECT event_type,health_state,window_start_us FROM health_events "
                "WHERE device_id=? AND boot_id=? ORDER BY event_id",
                ("task03-sim-node", boot_id),
            ).fetchall()]
            checkpoint = connection.execute(
                "SELECT model_version,reference_version FROM processing_checkpoints "
                "WHERE device_id=? AND boot_id=?",
                ("task03-sim-node", boot_id),
            ).fetchone()
            trace = []
            previous_state = None
            transitions = []
            for row in rows:
                feature = json.loads(row["payload_json"])
                isolation_score = row["isolation_score"]
                isolation_threshold = row["isolation_threshold"]
                above = (isolation_score > isolation_threshold
                         if isolation_score is not None else None)
                state = row["health_state"]
                if previous_state is not None and state != previous_state:
                    transitions.append({"window_start_us": row["window_start_us"],
                                        "from": previous_state, "to": state})
                previous_state = state
                trace.append({
                    "window_start_us": row["window_start_us"],
                    "window_end_us": row["window_end_us"],
                    "quality": feature["window_quality"],
                    "sample_count": feature["sample_count"],
                    "vibration_rms_g": feature["vibration_rms_g"],
                    "vibration_high_band_energy_g2": (
                        feature["vibration_high_band_energy_g2"]),
                    "isolation_score": isolation_score,
                    "isolation_threshold": isolation_threshold,
                    "reference_score": row["reference_score"],
                    "reference_threshold": row["reference_threshold"],
                    "above_isolation_threshold": above,
                    "health_state": state,
                })
            scored = [row for row in trace if row["isolation_score"] is not None]
            valid_count = sum(row["quality"] == "VALID" for row in trace)
            states = {}
            for row in trace:
                states[row["health_state"]] = states.get(row["health_state"], 0) + 1
            diagnostics.append({
                "boot_id": boot_id,
                "scenario": scenario,
                "model_version": checkpoint["model_version"] if checkpoint else None,
                "baseline_version": checkpoint["reference_version"] if checkpoint else None,
                "valid_windows": valid_count,
                "invalid_windows": len(trace) - valid_count,
                "scored_windows": len(scored),
                "maximum_anomaly_score": max(
                    (row["isolation_score"] for row in scored), default=None),
                "calibrated_anomaly_threshold": (
                    scored[0]["isolation_threshold"] if scored else None),
                "maximum_reference_score": max(
                    (row["reference_score"] for row in scored), default=None),
                "reference_threshold": (
                    scored[0]["reference_threshold"] if scored else None),
                "above_threshold_windows": sum(
                    row["above_isolation_threshold"] is True for row in scored),
                "maximum_vibration_rms_g": max(
                    (row["vibration_rms_g"] for row in trace
                     if row["vibration_rms_g"] is not None), default=None),
                "maximum_high_band_energy_g2": max(
                    (row["vibration_high_band_energy_g2"] for row in trace
                     if row["vibration_high_band_energy_g2"] is not None),
                    default=None),
                "health_states": states,
                "temporal_transitions": transitions,
                "events": events,
                "window_trace": trace,
            })
    return diagnostics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=Path("experiment-output/task03-live"))
    parser.add_argument("--sample-rate", type=int, default=500)
    args = parser.parse_args()
    output = args.output_dir
    if output.exists():
        parser.error(f"refusing to overwrite existing demonstration directory: {output}")
    output.mkdir(parents=True)

    training = Storage(output / "training.sqlite3")
    for boot, duration, seed in (("healthy-train", 22.0, 101),
                                 ("healthy-calibration", 7.0, 102)):
        generate(training, boot, Scenario.HEALTHY, duration, seed, args.sample_rate)
        extract_session(training, ("task03-sim-node", boot))
    artifact = output / "models" / "isolation-forest-live-v1.joblib"
    bundle = train_from_storage(
        training, [("task03-sim-node", "healthy-train")],
        [("task03-sim-node", "healthy-calibration")], artifact,
        model_version="isolation-forest-live-v1", random_state=42,
    )

    storage = Storage(output / "live.sqlite3")
    registry = MLRegistry(storage)
    registry.register_model(bundle, artifact)
    registry.activate_model(bundle.model_version)
    ControlledAdapter(bundle, registry)
    started = time.perf_counter()
    sessions = []
    generated_runs = []
    for boot, scenario, duration, seed in LIVE_SESSIONS:
        run = generate(storage, boot, scenario, duration, seed, args.sample_rate)
        generated_runs.append(run)
        sessions.append({"boot_id": boot, "scenario": scenario.value,
                         "duration_s": duration, "seed": seed,
                         "batches": len(run.batches),
                         "motion_samples": sum(len(batch.motion_samples)
                                               for batch in run.batches),
                         "environment_records": sum(len(batch.environment_samples)
                                                    for batch in run.batches)})
    ingestion_seconds = time.perf_counter() - started

    replay_batch = generated_runs[0].batches[0]
    duplicate_prevented, _ = storage.insert_batch(replay_batch)
    conflicting_payload = replay_batch.model_dump(mode="json")
    conflicting_payload["quality"]["missed_deadlines"] += 1
    conflict_rejected = False
    try:
        storage.insert_batch(TelemetryBatch.model_validate(conflicting_payload))
    except BatchConflictError:
        conflict_rejected = True

    worker = LiveProcessor(storage, owner_id="demo-worker-a")
    process_started = time.perf_counter()
    poll = worker.poll_once(datetime.now(timezone.utc) + timedelta(seconds=5))
    processing_seconds = time.perf_counter() - process_started
    before_restart = table_counts(storage)
    worker.release_lease()
    replay = LiveProcessor(storage, owner_id="demo-worker-b")
    replay_result = replay.poll_once(datetime.now(timezone.utc) + timedelta(seconds=5))
    replay.release_lease()
    after_restart = table_counts(storage)
    anomaly_diagnostics = session_diagnostics(storage, sessions)

    # API request behavior is covered by tests/test_monitor_api.py. Keep this
    # standalone demonstration compatible with Starlette releases where the
    # deprecated synchronous TestClient can hang outside pytest: validate the
    # actual route inventory and execute the same read-only service used by the
    # endpoint handlers against the persisted demonstration database.
    app = create_app(output / "live.sqlite3")
    route_paths = {route.path for route in app.routes}
    required_routes = {"/api/v1/health", "/api/v1/monitor/devices",
                       "/api/v1/monitor/models", "/api/v1/monitor/status"}
    monitor = MonitorService(storage)
    monitor_devices = monitor.devices()
    monitor_models = monitor.models()
    monitor_status = monitor.status()
    api_routes_present = required_routes <= route_paths
    total_attempted_motion = sum(
        int(session["duration_s"] * args.sample_rate) for session in sessions
    )
    database_bytes = sum(
        candidate.stat().st_size for candidate in (
            output / "live.sqlite3", output / "live.sqlite3-wal",
            output / "live.sqlite3-shm") if candidate.exists()
    )
    summary = {
        "declaration": "SIMULATED DATA — SOFTWARE DEMONSTRATION; NOT REAL MACHINE EVIDENCE",
        "device_id": "task03-sim-node", "sessions": sessions,
        "sample_rate_hz": args.sample_rate, "model_version": bundle.model_version,
        "model_source_type": bundle.source_type, "baseline_version": 1,
        "worker_first_poll": poll, "counts": before_restart,
        "worker_restart_poll": replay_result,
        "restart_idempotent": before_restart == after_restart,
        "anomaly_diagnostics": anomaly_diagnostics,
        "network_replay": {"duplicates_prevented": int(duplicate_prevented),
                           "conflicting_replay_rejected": conflict_rejected},
        "api": {"required_routes_present": api_routes_present,
                "database_available": storage.available(),
                "device_count": len(monitor_devices),
                "model_count": len(monitor_models),
                "worker_status": monitor_status.get("worker")},
        "timing_seconds": {"ingestion": ingestion_seconds,
                           "feature_and_inference": processing_seconds},
        "performance": {
            "synthetic_batches_per_second": (
                before_restart["ingested_batches"] / ingestion_seconds),
            "synthetic_motion_samples_per_second": (
                before_restart["motion_samples"] / ingestion_seconds),
            "processed_windows_per_second": (
                before_restart["feature_windows"] / processing_seconds),
            "p95_inference_seconds": None,
            "p95_note": "not instrumented; aggregate feature-and-inference timing reported",
            "missing_synthetic_motion_attempts": (
                total_attempted_motion - before_restart["motion_samples"]),
            "database_bytes_including_wal_and_shm": database_bytes,
        },
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "anomaly-diagnostic.json").write_text(
        json.dumps(anomaly_diagnostics, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    success = (summary["restart_idempotent"] and duplicate_prevented and
               conflict_rejected and api_routes_present and storage.available())
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
