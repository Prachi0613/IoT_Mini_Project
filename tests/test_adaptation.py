from __future__ import annotations

import numpy as np

from pmnode.contracts import SourceType
from pmnode.ml.adaptation import AdaptationPolicy, ControlledAdapter
from pmnode.ml.features import MODEL_FEATURES, WindowQuality
from pmnode.ml.health import HealthState
from pmnode.ml.registry import MLRegistry

from test_ml_model import make_window, trained_bundle


def shifted_window(bundle, fraction: float, index: int, source="SIMULATED"):
    vector = bundle.reference.center + fraction * bundle.reference.spread
    values = {name: float(value) for name, value in zip(MODEL_FEATURES, vector)}
    return make_window(1.0, index, "adapt", source).model_copy(update=values)


def make_adapter(storage, **policy_values):
    bundle = trained_bundle(); registry = MLRegistry(storage)
    adapter = ControlledAdapter(bundle, registry, AdaptationPolicy(**policy_values))
    return bundle, registry, adapter


def test_adaptation_disabled_by_default(storage):
    bundle, registry, adapter = make_adapter(storage)
    decision = adapter.consider(shifted_window(bundle, 0.05, 1), 0.0, 0.0,
                                HealthState.NORMAL, True)
    assert decision.decision == "REJECTED"
    assert decision.reason == "adaptation disabled"


def test_healthy_accumulation_changes_reference_and_version(storage):
    bundle, registry, adapter = make_adapter(
        storage, enabled=True, minimum_candidates=3, learning_rate=0.5)
    original = adapter.reference.center.copy()
    decisions = [adapter.consider(shifted_window(bundle, 0.1, i), 0.0, 0.0,
                                  HealthState.NORMAL, approve=(i == 2))
                 for i in range(3)]
    assert [d.decision for d in decisions] == ["ACCUMULATING", "ACCUMULATING", "ACTIVATED"]
    assert adapter.version == 2
    assert not np.array_equal(adapter.reference.center, original)
    assert registry.active_reference(bundle.model_version)["reference_version"] == 2


def test_anomaly_sensor_error_and_source_mismatch_block_updates(storage):
    bundle, _, adapter = make_adapter(storage, enabled=True, minimum_candidates=2)
    healthy = shifted_window(bundle, 0.05, 1)
    assert "isolation score" in adapter.consider(
        healthy, bundle.isolation_threshold, 0.0, HealthState.NORMAL).reason
    invalid = healthy.model_copy(update={"window_quality": WindowQuality.INVALID})
    assert adapter.consider(invalid, 0.0, 0.0, HealthState.SENSOR_ERROR).decision == "REJECTED"
    hardware = healthy.model_copy(update={"source_type": SourceType.HARDWARE})
    assert "source type" in adapter.consider(
        hardware, 0.0, 0.0, HealthState.NORMAL).reason


def test_minimum_duration_and_drift_limits_enforced(storage):
    bundle, _, adapter = make_adapter(
        storage, enabled=True, minimum_candidates=3, learning_rate=1.0,
        maximum_update_fraction=0.1)
    assert adapter.consider(shifted_window(bundle, 2.0, 1), 0.0, 0.0,
                            HealthState.NORMAL).decision == "ACCUMULATING"
    adapter.consider(shifted_window(bundle, 2.0, 2), 0.0, 0.0, HealthState.NORMAL)
    decision = adapter.consider(shifted_window(bundle, 2.0, 3), 0.0, 0.0,
                                HealthState.NORMAL, True)
    assert decision.decision == "REJECTED"
    assert "drift limit" in decision.reason


def test_adaptation_event_logging_and_real_rollback(storage):
    bundle, registry, adapter = make_adapter(
        storage, enabled=True, minimum_candidates=2, learning_rate=0.5)
    original = adapter.reference.center.copy()
    adapter.consider(shifted_window(bundle, 0.1, 1), 0.0, 0.0, HealthState.NORMAL)
    adapter.consider(shifted_window(bundle, 0.1, 2), 0.0, 0.0,
                     HealthState.NORMAL, True)
    assert len(registry.recent_adaptation_events(bundle.model_version)) >= 2
    adapter.rollback(1)
    assert adapter.version == 1
    assert np.array_equal(adapter.reference.center, original)
    assert registry.list_references(bundle.model_version)[0]["active"] == 1
