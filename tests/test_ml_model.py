from __future__ import annotations

import numpy as np
import pytest

from pmnode.ml.features import MODEL_FEATURES, FeatureWindow, WindowQuality
from pmnode.ml.model import (ModelCompatibilityError, load_model, save_model,
                             train_model, validate_session_splits)


def make_window(value: float, index: int, session="train", source="SIMULATED"):
    values = {name: float(value + offset * 0.001)
              for offset, name in enumerate(MODEL_FEATURES)}
    return FeatureWindow(
        feature_schema_version="1.0.0", device_id="node", boot_id=session,
        source_type=source, window_start_us=index * 1_000_000,
        window_end_us=(index + 1) * 1_000_000, sample_count=500,
        observed_sampling_hz=500.0, median_interval_us=2000.0,
        timestamp_jitter_us=0.0, max_gap_us=2000.0,
        window_quality=WindowQuality.VALID, quality_flags=[], **values)


def trained_bundle():
    train = [make_window(1 + 0.01 * np.sin(i), i) for i in range(30)]
    calibration = [make_window(1 + 0.008 * np.cos(i), i, "cal") for i in range(10)]
    return train_model(train, calibration, training_sessions=["node/train"],
                       calibration_sessions=["node/cal"], source_type="SIMULATED")


def test_valid_training_is_reproducible():
    first, second = trained_bundle(), trained_bundle()
    windows = [make_window(1.0, 50, "test")]
    assert first.score(windows)[0] == pytest.approx(second.score(windows)[0])


def test_insufficient_training_rejected():
    with pytest.raises(ValueError, match="insufficient healthy training"):
        train_model([make_window(1, i) for i in range(3)],
                    [make_window(1, i, "cal") for i in range(5)],
                    training_sessions=["node/train"], calibration_sessions=["node/cal"],
                    source_type="SIMULATED")


def test_session_overlap_rejected():
    with pytest.raises(ValueError, match="must not overlap"):
        validate_session_splits(["a"], ["a"], ["b"])


def test_scaler_fitted_only_on_training_and_feature_order_persisted():
    bundle = trained_bundle()
    train_matrix = np.asarray([make_window(1 + 0.01 * np.sin(i), i).model_vector()
                               for i in range(30)])
    assert bundle.scaler.center_ == pytest.approx(np.median(train_matrix, axis=0))
    assert bundle.feature_order == MODEL_FEATURES


def test_model_save_load_agreement(tmp_path):
    bundle = trained_bundle(); path = save_model(bundle, tmp_path / "model.joblib")
    loaded = load_model(path); windows = [make_window(1.02, 80, "test")]
    assert loaded.score(windows)[0] == pytest.approx(bundle.score(windows)[0])


def test_lower_forest_score_becomes_higher_anomaly_score():
    bundle = trained_bundle()
    matrix = bundle.scaler.transform(np.asarray([
        make_window(1.0, 1, "test").model_vector(),
        make_window(10.0, 2, "test").model_vector()]))
    raw = bundle.forest.score_samples(matrix)
    anomaly, _ = bundle.score([make_window(1.0, 1, "test"),
                               make_window(10.0, 2, "test")])
    assert np.allclose(anomaly, -raw)
    assert anomaly[1] > anomaly[0]


def test_invalid_window_not_scored():
    invalid = make_window(1.0, 1).model_copy(
        update={"window_quality": WindowQuality.INVALID})
    with pytest.raises(ValueError, match="invalid feature"):
        trained_bundle().score([invalid])


def test_source_compatibility_enforced():
    with pytest.raises(ModelCompatibilityError):
        trained_bundle().score([make_window(1.0, 1, source="HARDWARE")])
