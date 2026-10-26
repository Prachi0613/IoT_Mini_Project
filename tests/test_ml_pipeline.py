from __future__ import annotations

import json
import sqlite3

from pmnode.ml.evaluation import evaluate_results
from pmnode.ml.pipeline import extract_session, score_sessions, train_from_storage
from pmnode.ml.registry import MLRegistry
from pmnode.simulator import Scenario, SensorSimulator, SimulationConfig
from pmnode.storage import Storage


def ingest_simulation(storage, boot_id, scenario, duration, seed):
    run = SensorSimulator(
        SimulationConfig(scenario=scenario, duration_s=duration, sample_hz=250,
                         seed=seed, device_id="ml-node", batch_duration_s=1.0),
        boot_id=boot_id,
    ).generate()
    for batch in run.batches:
        storage.insert_batch(batch)
    return run


def test_task01_database_migrates_additively(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE marker(value TEXT)")
        connection.execute("INSERT INTO marker VALUES('raw-preserved')")
    Storage(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value FROM marker").fetchone()[0] == "raw-preserved"
        names = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"feature_windows", "model_predictions", "reference_versions",
            "processing_checkpoints", "health_events", "worker_lease"} <= names


def test_extraction_persistence_and_no_duplicates(storage):
    ingest_simulation(storage, "extract-boot", Scenario.HEALTHY, 3, 1)
    windows = extract_session(storage, ("ml-node", "extract-boot"))
    assert len(windows) == 3 and all(w.source_type.value == "SIMULATED" for w in windows)
    assert all(w.window_quality.value == "VALID" for w in windows)
    extract_session(storage, ("ml-node", "extract-boot"))
    with storage.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM feature_windows").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM motion_samples").fetchone()[0] == 750


def prepared_pipeline(storage, tmp_path):
    sessions = [
        ("train-a", Scenario.HEALTHY, 24, 1),
        ("cal-a", Scenario.HEALTHY, 10, 2),
        ("test-anomaly", Scenario.VIBRATION_ANOMALY, 10, 3),
    ]
    runs = {}
    for boot, scenario, duration, seed in sessions:
        runs[boot] = ingest_simulation(storage, boot, scenario, duration, seed)
        extract_session(storage, ("ml-node", boot))
    model_path = tmp_path / "models" / "model-1.joblib"
    bundle = train_from_storage(storage, [("ml-node", "train-a")],
                                [("ml-node", "cal-a")], model_path)
    return bundle, model_path, runs


def test_training_scoring_prediction_persistence_and_compatibility(storage, tmp_path):
    bundle, model_path, _ = prepared_pipeline(storage, tmp_path)
    results = score_sessions(storage, [("ml-node", "test-anomaly")], model_path)
    assert len(results) == 10
    with storage.connect() as connection:
        predictions = connection.execute("SELECT COUNT(*) FROM model_predictions").fetchone()[0]
        raw_before = connection.execute("SELECT COUNT(*) FROM motion_samples").fetchone()[0]
    score_sessions(storage, [("ml-node", "test-anomaly")], model_path)
    with storage.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM model_predictions").fetchone()[0] == predictions
        assert connection.execute("SELECT COUNT(*) FROM motion_samples").fetchone()[0] == raw_before
    assert bundle.source_type == "SIMULATED"


def test_deterministic_labeled_evaluation_artifacts(storage, tmp_path):
    bundle, model_path, runs = prepared_pipeline(storage, tmp_path)
    results = score_sessions(storage, [("ml-node", "test-anomaly")], model_path)
    onset = int(runs["test-anomaly"].labels[0]["start_mono_us"])
    labels = [row["window"].window_end_us > onset for row in results]
    first = evaluate_results(bundle, results, tmp_path / "report-a", labels, onset)
    second = evaluate_results(bundle, results, tmp_path / "report-b", labels, onset)
    assert first == second
    assert first["declaration"].startswith("SYNTHETIC SOFTWARE VALIDATION")
    assert "metrics" in first["isolation_forest"]
    assert (tmp_path / "report-a" / "anomaly_scores.png").is_file()
    assert json.loads((tmp_path / "report-a" / "evaluation_report.json").read_text()) == first


def test_invalid_window_persists_sensor_error_without_prediction(storage, tmp_path):
    ingest_simulation(storage, "dropout", Scenario.SENSOR_DROPOUT, 4, 7)
    extract_session(storage, ("ml-node", "dropout"))
    bundle, model_path, _ = prepared_pipeline(storage, tmp_path)
    results = score_sessions(storage, [("ml-node", "dropout")], model_path)
    assert any(row["health_state"] == "SENSOR_ERROR" and
               row["isolation_score"] is None for row in results)
    with storage.connect() as connection:
        errors = connection.execute(
            "SELECT COUNT(*) FROM health_decisions WHERE health_state='SENSOR_ERROR'"
        ).fetchone()[0]
    assert errors > 0
