from __future__ import annotations

import csv
import json

import pytest

from pmnode.storage import SessionNotFoundError
from scripts.export_csv import export_session


def store_run(storage, run):
    for batch in run.batches:
        storage.insert_batch(batch)


def test_motion_and_environment_csv(storage, healthy_run, tmp_path):
    store_run(storage, healthy_run)
    paths = export_session(storage.database_path, "test-node", "sim-test-boot",
                           tmp_path / "export")
    motion_path = next(path for path in paths if path.name.endswith("_motion.csv"))
    env_path = next(path for path in paths if path.name.endswith("_environment.csv"))
    with motion_path.open(newline="", encoding="utf-8") as stream:
        motion = list(csv.DictReader(stream))
    with env_path.open(newline="", encoding="utf-8") as stream:
        environment = list(csv.DictReader(stream))
    original_motion_count = sum(len(batch.motion_samples) for batch in healthy_run.batches)
    original_env_count = sum(len(batch.environment_samples) for batch in healthy_run.batches)
    assert len(motion) == original_motion_count
    assert len(environment) == original_env_count
    assert motion[0]["source_type"] == "SIMULATED"
    assert motion[0]["device_monotonic_time_us"] == "0"
    assert "backend_received_at_utc" in motion[0]
    assert "ax_g" in motion[0] and "gx_dps" in motion[0]


def test_export_selection_and_metadata(storage, healthy_run, tmp_path):
    store_run(storage, healthy_run)
    paths = export_session(storage.database_path, "test-node", "sim-test-boot",
                           tmp_path / "export", "motion")
    assert not any(path.name.endswith("_environment.csv") for path in paths)
    metadata_path = next(path for path in paths if path.name.endswith("_metadata.json"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["session"]["source_type"] == "SIMULATED"
    assert metadata["batches"][0]["requested_mpu_hz"] == 20
    assert "missed_deadlines" in metadata["batches"][0]


def test_export_missing_session_error(storage, tmp_path):
    with pytest.raises(SessionNotFoundError, match="session not found"):
        export_session(storage.database_path, "missing", "missing", tmp_path)
