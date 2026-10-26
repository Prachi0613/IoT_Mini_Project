from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from pmnode.contracts import SourceType
from pmnode.live.events import EventStore
from pmnode.live.monitor import MonitorService
from pmnode.live.processor import LiveProcessor, LiveProcessorConfig, WorkerLeaseError
from pmnode.ml.pipeline import extract_session, train_from_storage
from pmnode.ml.registry import MLRegistry
from pmnode.simulator import Scenario, SensorSimulator, SimulationConfig
from pmnode.storage import Storage


def ingest(storage, boot, scenario=Scenario.HEALTHY, duration=2.0,
           sample_hz=250, batch_duration=1.0, device="live-node", seed=1):
    run = SensorSimulator(SimulationConfig(
        scenario=scenario, duration_s=duration, sample_hz=sample_hz,
        batch_duration_s=batch_duration, device_id=device, seed=seed,
    ), boot_id=boot).generate()
    for batch in run.batches:
        storage.insert_batch(batch)
    return run


def active_model(storage, tmp_path):
    ingest(storage, "train", duration=22, seed=11)
    ingest(storage, "cal", duration=7, seed=12)
    extract_session(storage, ("live-node", "train"))
    extract_session(storage, ("live-node", "cal"))
    path = tmp_path / "model.joblib"
    bundle = train_from_storage(storage, [("live-node", "train")],
                                [("live-node", "cal")], path,
                                model_version="live-model-1")
    MLRegistry(storage).activate_model(bundle.model_version)
    return bundle, path


def future(seconds=5):
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def counts(storage):
    with storage.connect() as connection:
        return {name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                for name in ("motion_samples", "feature_windows", "model_predictions",
                             "health_decisions", "health_events")}


def test_legacy_session_schema_migrates_without_losing_rows(tmp_path):
    path = tmp_path / "legacy-session.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE device_sessions(device_id TEXT NOT NULL,boot_id TEXT NOT NULL,"
            "first_received_at_utc TEXT NOT NULL,last_received_at_utc TEXT NOT NULL,"
            "PRIMARY KEY(device_id,boot_id))"
        )
        connection.execute(
            "INSERT INTO device_sessions VALUES('old-node','old-boot','before','after')"
        )
    storage = Storage(path)
    session = storage.require_session("old-node", "old-boot")
    assert session["source_type"] == "SIMULATED"
    assert session["first_received_at_utc"] == "before"


def test_legacy_motion_foreign_key_accepts_current_ingestion(tmp_path):
    path = tmp_path / "legacy-motion.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE device_sessions(
              device_id TEXT NOT NULL,boot_id TEXT NOT NULL,
              first_received_at_utc TEXT NOT NULL,last_received_at_utc TEXT NOT NULL,
              PRIMARY KEY(device_id,boot_id));
            CREATE TABLE telemetry_batches(
              device_id TEXT NOT NULL,boot_id TEXT NOT NULL,batch_seq INTEGER NOT NULL,
              received_at_utc TEXT NOT NULL,requested_mpu_hz INTEGER NOT NULL,
              accel_range_g INTEGER NOT NULL,gyro_range_dps INTEGER NOT NULL,
              dlpf_config INTEGER NOT NULL,i2c_read_errors INTEGER NOT NULL,
              missed_deadlines INTEGER NOT NULL,buffer_overruns INTEGER NOT NULL,
              dht_failures INTEGER NOT NULL,PRIMARY KEY(device_id,boot_id,batch_seq),
              FOREIGN KEY(device_id,boot_id) REFERENCES device_sessions(device_id,boot_id));
            CREATE TABLE motion_samples(
              device_id TEXT NOT NULL,boot_id TEXT NOT NULL,batch_seq INTEGER NOT NULL,
              sample_seq INTEGER NOT NULL,t_mono_us INTEGER NOT NULL,
              ax_g REAL NOT NULL,ay_g REAL NOT NULL,az_g REAL NOT NULL,
              gx_dps REAL NOT NULL,gy_dps REAL NOT NULL,gz_dps REAL NOT NULL,
              received_at_utc TEXT NOT NULL,
              PRIMARY KEY(device_id,boot_id,sample_seq),
              FOREIGN KEY(device_id,boot_id,batch_seq)
                REFERENCES telemetry_batches(device_id,boot_id,batch_seq));
            """
        )
    storage = Storage(path)
    run = ingest(storage, "legacy-ingest", duration=1)
    assert len(run.batches[0].motion_samples) == 250
    assert storage.sessions("live-node")[0]["motion_sample_count"] == 250
    with storage.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM telemetry_batches").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM ingested_batches").fetchone()[0] == 1


def test_incomplete_window_is_pending_and_missing_model_is_honest(tmp_path):
    storage = Storage(tmp_path / "incomplete.sqlite3")
    ingest(storage, "partial", duration=.5)
    worker = LiveProcessor(storage)
    result = worker.poll_once()
    assert result["windows"] == 0 and counts(storage)["model_predictions"] == 0
    ingest(storage, "complete", duration=2)
    result = worker.poll_once(future())
    assert result["windows"] >= 2 and result["predictions"] == 0
    with storage.connect() as connection:
        status = connection.execute("SELECT processing_status FROM processing_checkpoints "
                                    "WHERE boot_id='complete'").fetchone()[0]
        event_count = connection.execute("SELECT COUNT(*) FROM health_events WHERE "
                                         "boot_id='complete' AND event_type='MODEL_UNAVAILABLE'").fetchone()[0]
    worker.poll_once(future())
    assert status == "MODEL_UNAVAILABLE" and event_count == 1


def test_split_batches_real_inference_restart_and_raw_preservation(tmp_path):
    storage = Storage(tmp_path / "live.sqlite3")
    bundle, _ = active_model(storage, tmp_path)
    ingest(storage, "split", duration=3, batch_duration=.5, seed=20)
    raw_before = counts(storage)["motion_samples"]
    worker = LiveProcessor(storage, owner_id="first")
    first = worker.poll_once(future())
    before = counts(storage)
    assert first["predictions"] >= 3
    assert before["motion_samples"] == raw_before
    with storage.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM model_predictions WHERE "
                                  "boot_id='split'").fetchone()[0] == 3
        row = connection.execute("SELECT model_version,reference_version,isolation_score "
                                 "FROM model_predictions WHERE boot_id='split' LIMIT 1").fetchone()
        checkpoint = connection.execute("SELECT next_window_start_us FROM processing_checkpoints "
                                        "WHERE boot_id='split'").fetchone()[0]
    assert row["model_version"] == bundle.model_version and row["reference_version"] == 1
    assert isinstance(row["isolation_score"], float) and checkpoint == 3_000_000
    worker.release_lease()
    restarted = LiveProcessor(storage, owner_id="second")
    restarted.poll_once(future())
    assert counts(storage) == before


def test_dropout_has_sensor_error_without_fake_prediction(tmp_path):
    storage = Storage(tmp_path / "dropout.sqlite3")
    active_model(storage, tmp_path)
    ingest(storage, "dropout", Scenario.SENSOR_DROPOUT, duration=4, seed=30)
    worker = LiveProcessor(storage)
    worker.poll_once(future())
    with storage.connect() as connection:
        invalid = connection.execute("SELECT COUNT(*) FROM feature_windows WHERE "
                                     "boot_id='dropout' AND quality_status='INVALID'").fetchone()[0]
        errors = connection.execute("SELECT COUNT(*) FROM health_decisions WHERE "
                                    "boot_id='dropout' AND health_state='SENSOR_ERROR'").fetchone()[0]
        predictions = connection.execute("SELECT COUNT(*) FROM model_predictions WHERE "
                                         "boot_id='dropout'").fetchone()[0]
        invalid_predictions = connection.execute(
            "SELECT COUNT(*) FROM feature_windows f JOIN model_predictions p "
            "ON p.device_id=f.device_id AND p.boot_id=f.boot_id "
            "AND p.window_start_us=f.window_start_us WHERE f.boot_id='dropout' "
            "AND f.quality_status='INVALID'").fetchone()[0]
    assert invalid > 0 and errors >= invalid and predictions < 4
    assert invalid_predictions == 0


def test_source_separation_worker_lease_and_event_acknowledgement(tmp_path):
    storage = Storage(tmp_path / "safety.sqlite3")
    active_model(storage, tmp_path)
    ingest(storage, "session-a", duration=2, device="device-a")
    worker = LiveProcessor(storage, owner_id="owner-a")
    worker.poll_once(future())
    with pytest.raises(WorkerLeaseError):
        LiveProcessor(storage, owner_id="owner-b").poll_once(future())
    created = EventStore(storage).create_processing_event(
        device_id="device-a", boot_id="session-a", source_type="SIMULATED",
        event_type="MODEL_UNAVAILABLE")
    assert created
    monitor = MonitorService(storage)
    event = monitor.event_history("device-a", None, "MODEL_UNAVAILABLE", None,
                                  False, 10, 0)["events"][0]
    acknowledged = monitor.events.acknowledge(event["event_id"], "reviewed")
    assert acknowledged["acknowledged"] == 1
    assert acknowledged["acknowledgement_note"] == "reviewed"


def test_incompatible_hardware_model_never_produces_scores(tmp_path):
    storage = Storage(tmp_path / "incompatible.sqlite3")
    _, artifact = active_model(storage, tmp_path)
    with storage.connect() as connection:
        connection.execute(
            "INSERT INTO model_versions VALUES(?,?,?,?,?,?,?)",
            ("bad-hardware-model", "HARDWARE", "1.0.0", str(artifact.resolve()),
             "ACTIVE_HARDWARE", "{}", datetime.now(timezone.utc).isoformat()),
        )
    run = SensorSimulator(SimulationConfig(
        duration_s=2, sample_hz=250, seed=35, device_id="hardware-node",
    ), boot_id="hardware-boot").generate()
    for batch in run.batches:
        storage.insert_batch(batch.model_copy(update={"source_type": SourceType.HARDWARE}))
    worker = LiveProcessor(storage)
    worker.poll_once(future())
    with storage.connect() as connection:
        features = connection.execute("SELECT COUNT(*) FROM feature_windows WHERE "
                                      "boot_id='hardware-boot'").fetchone()[0]
        predictions = connection.execute("SELECT COUNT(*) FROM model_predictions WHERE "
                                         "boot_id='hardware-boot'").fetchone()[0]
        incompatible = connection.execute("SELECT COUNT(*) FROM health_events WHERE "
                                          "boot_id='hardware-boot' AND "
                                          "event_type='MODEL_INCOMPATIBLE'").fetchone()[0]
    assert features == 2 and predictions == 0 and incompatible == 1


def test_monitor_history_models_are_bounded_and_paths_hidden(tmp_path):
    storage = Storage(tmp_path / "monitor.sqlite3")
    bundle, path = active_model(storage, tmp_path)
    ingest(storage, "observed", duration=3, seed=40)
    worker = LiveProcessor(storage)
    worker.poll_once(future())
    monitor = MonitorService(storage)
    overview = monitor.overview("live-node")
    history = monitor.history("live-node", "observed", 1_000_000, 3_000_000, 1, 0)
    models = monitor.models()
    baseline = monitor.baseline(bundle.model_version)
    assert overview["source_type"] == "SIMULATED" and overview["latest_feature"]
    assert len(history["points"]) == 1 and history["points"][0]["window_start_us"] >= 1_000_000
    assert "artifact_path" not in models[0] and str(path) not in str(models)
    assert baseline["active_reference"]["reference_version"] == 1
    assert baseline["adaptation_enabled"] is False
