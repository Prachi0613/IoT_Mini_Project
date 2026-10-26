from __future__ import annotations

import math

import numpy as np

from pmnode.ml.features import FeatureConfig, WindowQuality, extract_feature_windows


def sine_rows(frequency=20.0, amplitude=1.0, hz=500, duration=1.0,
              gyro_amplitude=10.0):
    rows = []
    for index in range(int(hz * duration)):
        t = index / hz
        rows.append({"sample_seq": index, "t_mono_us": round(t * 1e6),
                     "ax_g": amplitude * math.sin(2 * math.pi * frequency * t),
                     "ay_g": 0.0, "az_g": 1.0,
                     "gx_dps": gyro_amplitude * math.sin(2 * math.pi * 5 * t),
                     "gy_dps": 0.0, "gz_dps": 0.0})
    return rows


def extract(rows, environment=None, config=FeatureConfig()):
    return extract_feature_windows(
        rows, environment or [], device_id="node", boot_id="boot",
        source_type="SIMULATED", config=config,
    )[0]


def test_known_sine_rms_and_dominant_frequency():
    feature = extract(sine_rows())
    assert feature.window_quality == WindowQuality.VALID
    assert feature.vibration_rms_g == pytest.approx(1 / math.sqrt(2), rel=0.01)
    assert feature.dominant_frequency_hz == pytest.approx(20.0, abs=1.0)


def test_sinusoid_energy_is_in_low_band():
    feature = extract(sine_rows(frequency=20))
    assert feature.vibration_low_band_energy_g2 > feature.vibration_high_band_energy_g2 * 100


def test_gravity_and_constant_offset_removed_with_safe_crest_factor():
    rows = sine_rows(amplitude=0, gyro_amplitude=0)
    feature = extract(rows)
    assert feature.vibration_rms_g == pytest.approx(0.0, abs=1e-12)
    assert feature.vibration_crest_factor == 0.0
    assert feature.vibration_energy_g2 == pytest.approx(0.0, abs=1e-12)


def test_gyroscope_statistics_are_angular_velocity_statistics():
    feature = extract(sine_rows(gyro_amplitude=10))
    assert feature.gyro_rms_dps == pytest.approx(10 / math.sqrt(2), rel=0.01)
    assert feature.gyro_peak_dps == pytest.approx(10.0, rel=0.01)


def test_sampling_frequency_comes_from_timestamps():
    feature = extract(sine_rows(hz=400), config=FeatureConfig(expected_sampling_hz=400))
    assert feature.observed_sampling_hz == pytest.approx(400.0, rel=1e-6)
    assert feature.median_interval_us == pytest.approx(2500.0)


def test_dropout_is_not_interpolated_and_invalidates_window():
    rows = sine_rows()
    del rows[100:104]
    feature = extract(rows)
    assert feature.sample_count == 496
    assert "EXCESSIVE_GAP" in feature.quality_flags
    assert feature.window_quality == WindowQuality.INVALID
    assert feature.vibration_rms_g is None


def test_insufficient_samples_rejected():
    feature = extract(sine_rows(hz=50), config=FeatureConfig(expected_sampling_hz=500))
    assert feature.window_quality == WindowQuality.INVALID
    assert "INSUFFICIENT_SAMPLES" in feature.quality_flags


def test_environment_context_matching_and_staleness():
    environment = [{"env_seq": 0, "t_mono_us": 100_000,
                    "ambient_temperature_c": 24.5, "ambient_humidity_pct": 50.0,
                    "valid": 1, "error_code": None}]
    current = extract(sine_rows(), environment)
    assert current.ambient_temperature_c == 24.5
    assert current.environment_age_us == 900_000
    stale = extract(sine_rows(), environment,
                    FeatureConfig(environment_stale_s=0.5))
    assert "ENVIRONMENT_STALE" in stale.quality_flags
    assert stale.window_quality == WindowQuality.VALID


def test_environment_from_other_session_is_not_supplied():
    feature = extract(sine_rows(), [])
    assert feature.ambient_temperature_c is None
    assert "ENVIRONMENT_MISSING" in feature.quality_flags


import pytest
