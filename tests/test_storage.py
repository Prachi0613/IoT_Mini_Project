from __future__ import annotations

import copy

import pytest

from pmnode.contracts import SourceType, TelemetryBatch
from pmnode.storage import BatchConflictError


def test_session_and_atomic_batch_created(storage, valid_batch):
    duplicate, _ = storage.insert_batch(valid_batch)
    assert duplicate is False
    session = storage.sessions()[0]
    assert session["batch_count"] == 1
    assert session["motion_sample_count"] == len(valid_batch.motion_samples)
    assert session["environment_sample_count"] == len(valid_batch.environment_samples)


def test_identical_retry_does_not_duplicate(storage, valid_batch):
    storage.insert_batch(valid_batch)
    duplicate, first_received = storage.insert_batch(valid_batch)
    assert duplicate is True
    assert storage.sessions()[0]["motion_sample_count"] == len(valid_batch.motion_samples)
    assert first_received


def test_conflicting_replay_rejected(storage, valid_batch):
    storage.insert_batch(valid_batch)
    changed = valid_batch.model_copy(deep=True)
    changed.motion_samples[0].ax_g += 0.01
    with pytest.raises(BatchConflictError):
        storage.insert_batch(changed)


def test_device_separation(storage, valid_batch):
    storage.insert_batch(valid_batch)
    second = valid_batch.model_copy(update={"device_id": "other-node"}, deep=True)
    storage.insert_batch(second)
    assert {s["device_id"] for s in storage.sessions()} == {"test-node", "other-node"}


def test_boot_separation(storage, valid_batch):
    storage.insert_batch(valid_batch)
    second = valid_batch.model_copy(update={"boot_id": "sim-other-boot"}, deep=True)
    storage.insert_batch(second)
    assert len(storage.sessions("test-node")) == 2


def test_invalid_environment_nulls_persist(storage, healthy_run):
    from pmnode.simulator import Scenario, SensorSimulator, SimulationConfig
    run = SensorSimulator(SimulationConfig(scenario=Scenario.SENSOR_DROPOUT,
                          duration_s=3, sample_hz=10), boot_id="drop-boot").generate()
    for batch in run.batches:
        storage.insert_batch(batch)
    rows = storage.telemetry("sim-node-01", "drop-boot", "environment", 100, 0)
    invalid = next(row for row in rows if not row["valid"])
    assert invalid["ambient_temperature_c"] is None
    assert invalid["ambient_humidity_pct"] is None


def test_source_type_and_sample_order_preserved(storage, healthy_run):
    for batch in healthy_run.batches:
        storage.insert_batch(batch)
    assert storage.sessions()[0]["source_type"] == SourceType.SIMULATED.value
    rows = storage.telemetry("test-node", "sim-test-boot", "motion", 1000, 0)
    assert [row["sample_seq"] for row in rows] == list(range(len(rows)))


def test_latest_sensors_keep_independent_timestamps(storage, healthy_run):
    for batch in healthy_run.batches:
        storage.insert_batch(batch)
    latest = storage.latest("test-node")
    assert latest["latest_motion"]["t_mono_us"] != latest["latest_environment"]["t_mono_us"]
