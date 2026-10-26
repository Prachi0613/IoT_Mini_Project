from __future__ import annotations

from pmnode.ml.health import HealthConfig, HealthEngine, HealthState


def feed(engine, scores, boot="boot"):
    return [engine.update("node", boot, i * 1_000_000, score, True)
            for i, score in enumerate(scores)]


def test_normal_and_transient_behavior():
    engine = HealthEngine(1.0)
    states = feed(engine, [0.2, 0.3, 1.2, 0.2, 0.3])
    assert states[-1] == HealthState.NORMAL
    assert HealthState.MAINTENANCE not in states


def test_sustained_warning_transition():
    states = feed(HealthEngine(1.0), [0.2, 1.1, 1.2, 0.3, 1.3])
    assert states[-1] == HealthState.WARNING


def test_degrading_positive_trend():
    engine = HealthEngine(1.0)
    states = feed(engine, [1.01, 1.05, 1.1, 1.15, 1.2, 1.25, 1.3, 1.35])
    assert states[-1] == HealthState.DEGRADING


def test_severe_sustained_escalates_maintenance():
    states = feed(HealthEngine(1.0), [1.7] * 10)
    assert states[-1] == HealthState.MAINTENANCE


def test_invalid_window_is_sensor_error():
    engine = HealthEngine(1.0)
    assert engine.update("node", "boot", 0, None, False) == HealthState.SENSOR_ERROR


def test_hysteresis_requires_recovery_streak():
    engine = HealthEngine(1.0, HealthConfig(recovery_windows=3))
    feed(engine, [1.2, 1.2, 1.2, 1.2, 1.2])
    assert engine.update("node", "boot", 6, 0.2) == HealthState.WARNING
    assert engine.update("node", "boot", 7, 0.2) == HealthState.WARNING
    assert engine.update("node", "boot", 8, 0.2) == HealthState.NORMAL


def test_state_resets_between_sessions():
    engine = HealthEngine(1.0)
    feed(engine, [1.2] * 5, "first")
    assert engine.state == HealthState.WARNING
    assert engine.update("node", "second", 0, 0.2) == HealthState.NORMAL
