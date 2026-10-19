"""
test_contracts.py — pytest unit tests for pmnode data contracts.

Covers all 20 acceptance criteria specified in TASK 01.

Test numbering follows the specification:
  TC-01  Valid RunMetadata creation
  TC-02  Rejection of naive/non-UTC datetime
  TC-03  Valid AccelerationSample creation
  TC-04  Rejection of negative elapsed timestamps
  TC-05  Rejection of NaN and infinite sensor values
  TC-06  Acceptance of signed current measurements
  TC-07  Rejection of out-of-range humidity
  TC-08  Rejection of PWM duty outside [0, 1]
  TC-09  Acceptance of missing measured_rpm (OperatingContext)
  TC-10  Rejection of invalid operating-regime values
  TC-11  Valid FeatureWindow creation
  TC-12  Rejection of reversed/equal window timestamps
  TC-13  Rejection of negative sample counts
  TC-14  Rejection of missing required features in VALID window
  TC-15  Acceptance of null feature values in INVALID window
  TC-16  Rejection of unsupported health states
  TC-17  Acceptance of null anomaly scores for SENSOR_ERROR
  TC-18  Rejection of undeclared extra fields
  TC-19  JSON serialization / deserialization round-trip
  TC-20  Generated JSON schemas are synchronized with source models
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from pmnode.contracts import (
    SCHEMA_VERSION,
    AccelerationSample,
    CurrentSample,
    EnvironmentSample,
    FeatureWindow,
    HealthDecision,
    HealthState,
    OperatingContext,
    OperatingRegime,
    QualityFlag,
    RunMetadata,
    WindowQuality,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

UTC = timezone.utc


@pytest.fixture()
def valid_run_metadata() -> dict:
    return {
        "device_id": "esp32-dev-01",
        "run_id": "run-2024-001",
        "started_at_utc": datetime(2024, 6, 1, 8, 0, 0, tzinfo=UTC),
        "nominal_accel_hz": 200.0,
        "nominal_current_hz": 200.0,
        "nominal_environment_interval_s": 2.0,
    }


@pytest.fixture()
def valid_accel_sample() -> dict:
    return {
        "run_id": "run-2024-001",
        "timestamp_us": 5000,
        "accel_x_g": 0.01,
        "accel_y_g": -0.02,
        "accel_z_g": 1.00,
    }


@pytest.fixture()
def valid_current_sample() -> dict:
    return {
        "run_id": "run-2024-001",
        "timestamp_us": 5000,
        "motor_current_a": 1.5,
    }


@pytest.fixture()
def valid_env_sample() -> dict:
    return {
        "run_id": "run-2024-001",
        "timestamp_us": 2_000_000,
        "ambient_temperature_c": 25.3,
        "ambient_humidity_pct": 55.0,
    }


@pytest.fixture()
def valid_operating_context() -> dict:
    return {
        "run_id": "run-2024-001",
        "timestamp_us": 5000,
        "pwm_duty": 0.6,
        "operating_regime": OperatingRegime.MEDIUM,
        "measured_rpm": None,
    }


@pytest.fixture()
def valid_feature_window() -> dict:
    """Minimal VALID FeatureWindow with all required fields populated."""
    return {
        "device_id": "esp32-dev-01",
        "run_id": "run-2024-001",
        "window_start_us": 0,
        "window_end_us": 1_000_000,
        "accel_sample_count": 200,
        "current_sample_count": 200,
        "vibration_rms_g": 0.95,
        "vibration_std_g": 0.12,
        "vibration_peak_g": 1.40,
        "vibration_crest_factor": 1.47,
        "vibration_low_band_energy_g2": 0.30,
        "vibration_high_band_energy_g2": 0.15,
        "current_mean_a": 1.50,
        "current_std_a": 0.05,
        "current_peak_a": 1.70,
        "pwm_duty": 0.6,
        "operating_regime": OperatingRegime.MEDIUM,
        "window_quality": WindowQuality.VALID,
        "quality_flags": [],
    }


@pytest.fixture()
def valid_health_decision() -> dict:
    return {
        "device_id": "esp32-dev-01",
        "run_id": "run-2024-001",
        "window_end_us": 1_000_000,
        "health_state": HealthState.NORMAL,
        "anomaly_score": -0.12,
        "anomaly_threshold": 0.0,
        "model_version": "iforest-v0.1",
    }


# ---------------------------------------------------------------------------
# TC-01: Valid RunMetadata creation
# ---------------------------------------------------------------------------


class TestRunMetadata:
    def test_tc01_valid_creation(self, valid_run_metadata):
        """TC-01: A fully specified, valid RunMetadata must be accepted."""
        meta = RunMetadata(**valid_run_metadata)
        assert meta.device_id == "esp32-dev-01"
        assert meta.run_id == "run-2024-001"
        assert meta.schema_version == SCHEMA_VERSION
        assert meta.nominal_accel_hz == 200.0

    # TC-02: Rejection of naive/non-UTC datetime
    def test_tc02_naive_datetime_rejected(self, valid_run_metadata):
        """TC-02: A naive (tz-unaware) started_at_utc must be rejected."""
        valid_run_metadata["started_at_utc"] = datetime(2024, 6, 1, 8, 0, 0)
        with pytest.raises(ValidationError, match="timezone-aware"):
            RunMetadata(**valid_run_metadata)

    def test_tc02_non_utc_aware_rejected(self, valid_run_metadata):
        """TC-02: A non-UTC timezone-aware datetime must be rejected."""
        ist = timezone(timedelta(hours=5, minutes=30))
        valid_run_metadata["started_at_utc"] = datetime(2024, 6, 1, 8, 0, 0, tzinfo=ist)
        with pytest.raises(ValidationError, match="UTC offset"):
            RunMetadata(**valid_run_metadata)

    def test_empty_device_id_rejected(self, valid_run_metadata):
        """Empty device_id must be rejected."""
        valid_run_metadata["device_id"] = ""
        with pytest.raises(ValidationError):
            RunMetadata(**valid_run_metadata)

    def test_empty_run_id_rejected(self, valid_run_metadata):
        """Empty run_id must be rejected."""
        valid_run_metadata["run_id"] = ""
        with pytest.raises(ValidationError):
            RunMetadata(**valid_run_metadata)

    def test_zero_nominal_hz_rejected(self, valid_run_metadata):
        """Non-positive nominal frequency must be rejected."""
        valid_run_metadata["nominal_accel_hz"] = 0.0
        with pytest.raises(ValidationError):
            RunMetadata(**valid_run_metadata)


# ---------------------------------------------------------------------------
# TC-03 / TC-04 / TC-05: AccelerationSample
# ---------------------------------------------------------------------------


class TestAccelerationSample:
    def test_tc03_valid_creation(self, valid_accel_sample):
        """TC-03: A valid AccelerationSample must be accepted."""
        sample = AccelerationSample(**valid_accel_sample)
        assert sample.timestamp_us == 5000
        assert sample.accel_z_g == pytest.approx(1.00)

    def test_tc04_negative_timestamp_rejected(self, valid_accel_sample):
        """TC-04: A negative timestamp_us must be rejected."""
        valid_accel_sample["timestamp_us"] = -1
        with pytest.raises(ValidationError):
            AccelerationSample(**valid_accel_sample)

    def test_tc04_zero_timestamp_accepted(self, valid_accel_sample):
        """TC-04: Timestamp of zero (run start) must be accepted."""
        valid_accel_sample["timestamp_us"] = 0
        sample = AccelerationSample(**valid_accel_sample)
        assert sample.timestamp_us == 0

    def test_tc05_nan_accel_rejected(self, valid_accel_sample):
        """TC-05: NaN in any acceleration axis must be rejected."""
        valid_accel_sample["accel_x_g"] = math.nan
        with pytest.raises(ValidationError, match="finite"):
            AccelerationSample(**valid_accel_sample)

    def test_tc05_inf_accel_rejected(self, valid_accel_sample):
        """TC-05: Infinity in any acceleration axis must be rejected."""
        valid_accel_sample["accel_y_g"] = math.inf
        with pytest.raises(ValidationError, match="finite"):
            AccelerationSample(**valid_accel_sample)

    def test_tc05_neg_inf_accel_rejected(self, valid_accel_sample):
        """TC-05: Negative infinity must also be rejected."""
        valid_accel_sample["accel_z_g"] = -math.inf
        with pytest.raises(ValidationError, match="finite"):
            AccelerationSample(**valid_accel_sample)


# ---------------------------------------------------------------------------
# TC-06: Signed current / TC-05 on CurrentSample
# ---------------------------------------------------------------------------


class TestCurrentSample:
    def test_tc06_positive_current_accepted(self, valid_current_sample):
        """TC-06: Positive current is accepted."""
        sample = CurrentSample(**valid_current_sample)
        assert sample.motor_current_a == pytest.approx(1.5)

    def test_tc06_negative_current_accepted(self, valid_current_sample):
        """TC-06: Small negative current (calibration noise) must be accepted."""
        valid_current_sample["motor_current_a"] = -0.03
        sample = CurrentSample(**valid_current_sample)
        assert sample.motor_current_a == pytest.approx(-0.03)

    def test_tc06_zero_current_accepted(self, valid_current_sample):
        """TC-06: Zero current (motor off) must be accepted."""
        valid_current_sample["motor_current_a"] = 0.0
        sample = CurrentSample(**valid_current_sample)
        assert sample.motor_current_a == 0.0

    def test_tc05_nan_current_rejected(self, valid_current_sample):
        """TC-05: NaN current must be rejected."""
        valid_current_sample["motor_current_a"] = math.nan
        with pytest.raises(ValidationError, match="finite"):
            CurrentSample(**valid_current_sample)

    def test_tc05_inf_current_rejected(self, valid_current_sample):
        """TC-05: Infinite current must be rejected."""
        valid_current_sample["motor_current_a"] = math.inf
        with pytest.raises(ValidationError, match="finite"):
            CurrentSample(**valid_current_sample)


# ---------------------------------------------------------------------------
# TC-07: Humidity bounds / EnvironmentSample
# ---------------------------------------------------------------------------


class TestEnvironmentSample:
    def test_tc07_valid_environment_sample(self, valid_env_sample):
        """TC-07: A valid EnvironmentSample must be accepted."""
        sample = EnvironmentSample(**valid_env_sample)
        assert sample.ambient_humidity_pct == pytest.approx(55.0)

    def test_tc07_zero_humidity_accepted(self, valid_env_sample):
        """TC-07: 0% humidity is on the valid boundary and must be accepted."""
        valid_env_sample["ambient_humidity_pct"] = 0.0
        sample = EnvironmentSample(**valid_env_sample)
        assert sample.ambient_humidity_pct == 0.0

    def test_tc07_100_humidity_accepted(self, valid_env_sample):
        """TC-07: 100% humidity is on the valid boundary and must be accepted."""
        valid_env_sample["ambient_humidity_pct"] = 100.0
        sample = EnvironmentSample(**valid_env_sample)
        assert sample.ambient_humidity_pct == 100.0

    def test_tc07_negative_humidity_rejected(self, valid_env_sample):
        """TC-07: Humidity below 0% must be rejected."""
        valid_env_sample["ambient_humidity_pct"] = -1.0
        with pytest.raises(ValidationError):
            EnvironmentSample(**valid_env_sample)

    def test_tc07_over_100_humidity_rejected(self, valid_env_sample):
        """TC-07: Humidity above 100% must be rejected."""
        valid_env_sample["ambient_humidity_pct"] = 100.1
        with pytest.raises(ValidationError):
            EnvironmentSample(**valid_env_sample)

    def test_ambient_field_naming(self, valid_env_sample):
        """Fields must use 'ambient_' prefix, never 'motor_temperature'."""
        sample = EnvironmentSample(**valid_env_sample)
        assert hasattr(sample, "ambient_temperature_c")
        assert hasattr(sample, "ambient_humidity_pct")
        assert not hasattr(sample, "motor_temperature")


# ---------------------------------------------------------------------------
# TC-08 / TC-09 / TC-10: OperatingContext
# ---------------------------------------------------------------------------


class TestOperatingContext:
    def test_tc08_valid_pwm(self, valid_operating_context):
        """TC-08: PWM duty in [0, 1] must be accepted."""
        ctx = OperatingContext(**valid_operating_context)
        assert ctx.pwm_duty == pytest.approx(0.6)

    def test_tc08_pwm_below_zero_rejected(self, valid_operating_context):
        """TC-08: PWM duty < 0 must be rejected."""
        valid_operating_context["pwm_duty"] = -0.01
        with pytest.raises(ValidationError):
            OperatingContext(**valid_operating_context)

    def test_tc08_pwm_above_one_rejected(self, valid_operating_context):
        """TC-08: PWM duty > 1 must be rejected."""
        valid_operating_context["pwm_duty"] = 1.001
        with pytest.raises(ValidationError):
            OperatingContext(**valid_operating_context)

    def test_tc08_pwm_boundaries_accepted(self, valid_operating_context):
        """TC-08: Boundary values 0.0 and 1.0 must be accepted."""
        for duty in (0.0, 1.0):
            valid_operating_context["pwm_duty"] = duty
            ctx = OperatingContext(**valid_operating_context)
            assert ctx.pwm_duty == pytest.approx(duty)

    def test_tc09_none_rpm_accepted(self, valid_operating_context):
        """TC-09: measured_rpm=None (no RPM sensor) must be accepted."""
        valid_operating_context["measured_rpm"] = None
        ctx = OperatingContext(**valid_operating_context)
        assert ctx.measured_rpm is None

    def test_tc09_rpm_absent_accepted(self, valid_operating_context):
        """TC-09: OperatingContext with no measured_rpm key must be accepted."""
        valid_operating_context.pop("measured_rpm", None)
        ctx = OperatingContext(**valid_operating_context)
        assert ctx.measured_rpm is None

    def test_tc09_valid_rpm_accepted(self, valid_operating_context):
        """TC-09: A nonnegative finite RPM must be accepted."""
        valid_operating_context["measured_rpm"] = 1500.0
        ctx = OperatingContext(**valid_operating_context)
        assert ctx.measured_rpm == pytest.approx(1500.0)

    def test_tc09_negative_rpm_rejected(self, valid_operating_context):
        """TC-09: Negative RPM must be rejected when a value is provided."""
        valid_operating_context["measured_rpm"] = -100.0
        with pytest.raises(ValidationError, match="nonnegative"):
            OperatingContext(**valid_operating_context)

    def test_tc10_valid_regime_enum(self, valid_operating_context):
        """TC-10: All valid OperatingRegime values must be accepted."""
        for regime in OperatingRegime:
            valid_operating_context["operating_regime"] = regime
            ctx = OperatingContext(**valid_operating_context)
            assert ctx.operating_regime == regime

    def test_tc10_invalid_regime_rejected(self, valid_operating_context):
        """TC-10: An unrecognised regime string must be rejected."""
        valid_operating_context["operating_regime"] = "TURBO"
        with pytest.raises(ValidationError):
            OperatingContext(**valid_operating_context)


# ---------------------------------------------------------------------------
# TC-11 through TC-15: FeatureWindow
# ---------------------------------------------------------------------------


class TestFeatureWindow:
    def test_tc11_valid_feature_window(self, valid_feature_window):
        """TC-11: A fully-populated VALID FeatureWindow must be accepted."""
        fw = FeatureWindow(**valid_feature_window)
        assert fw.window_quality == WindowQuality.VALID
        assert fw.vibration_rms_g == pytest.approx(0.95)
        assert fw.quality_flags == []

    def test_tc12_reversed_timestamps_rejected(self, valid_feature_window):
        """TC-12: window_end_us <= window_start_us must be rejected."""
        valid_feature_window["window_start_us"] = 1_000_000
        valid_feature_window["window_end_us"] = 500_000
        with pytest.raises(ValidationError, match="strictly greater"):
            FeatureWindow(**valid_feature_window)

    def test_tc12_equal_timestamps_rejected(self, valid_feature_window):
        """TC-12: Equal window start and end timestamps must be rejected."""
        valid_feature_window["window_start_us"] = 1_000_000
        valid_feature_window["window_end_us"] = 1_000_000
        with pytest.raises(ValidationError, match="strictly greater"):
            FeatureWindow(**valid_feature_window)

    def test_tc13_negative_accel_count_rejected(self, valid_feature_window):
        """TC-13: Negative accel_sample_count must be rejected."""
        valid_feature_window["accel_sample_count"] = -1
        with pytest.raises(ValidationError):
            FeatureWindow(**valid_feature_window)

    def test_tc13_negative_current_count_rejected(self, valid_feature_window):
        """TC-13: Negative current_sample_count must be rejected."""
        valid_feature_window["current_sample_count"] = -1
        with pytest.raises(ValidationError):
            FeatureWindow(**valid_feature_window)

    def test_tc13_negative_vibration_feature_rejected(self, valid_feature_window):
        """TC-13: Negative vibration_rms_g must be rejected."""
        valid_feature_window["vibration_rms_g"] = -0.01
        with pytest.raises(ValidationError, match="non-negative"):
            FeatureWindow(**valid_feature_window)

    def test_tc14_valid_window_with_null_vibration_rejected(self, valid_feature_window):
        """TC-14: VALID window with a missing required vibration feature must be rejected."""
        valid_feature_window["vibration_rms_g"] = None
        with pytest.raises(ValidationError, match="VALID window must have all vibration features"):
            FeatureWindow(**valid_feature_window)

    def test_tc14_valid_window_with_zero_accel_count_rejected(self, valid_feature_window):
        """TC-14: VALID window with accel_sample_count=0 must be rejected."""
        valid_feature_window["accel_sample_count"] = 0
        with pytest.raises(ValidationError, match="accel_sample_count > 0"):
            FeatureWindow(**valid_feature_window)

    def test_tc14_valid_window_with_null_current_feature_rejected(self, valid_feature_window):
        """TC-14: VALID window missing current_mean_a must be rejected."""
        valid_feature_window["current_mean_a"] = None
        with pytest.raises(ValidationError, match="VALID window must have all current features"):
            FeatureWindow(**valid_feature_window)

    def test_tc15_invalid_window_with_null_features_accepted(self):
        """TC-15: INVALID window may have null feature values."""
        fw = FeatureWindow(
            device_id="esp32-dev-01",
            run_id="run-2024-001",
            window_start_us=0,
            window_end_us=1_000_000,
            accel_sample_count=0,
            current_sample_count=0,
            pwm_duty=0.6,
            operating_regime=OperatingRegime.MEDIUM,
            window_quality=WindowQuality.INVALID,
            quality_flags=[QualityFlag.ACCEL_MISSING, QualityFlag.CURRENT_MISSING],
        )
        assert fw.window_quality == WindowQuality.INVALID
        assert fw.vibration_rms_g is None
        assert fw.current_mean_a is None
        assert QualityFlag.ACCEL_MISSING in fw.quality_flags

    def test_signed_current_mean_accepted(self, valid_feature_window):
        """current_mean_a may be negative in a VALID window."""
        valid_feature_window["current_mean_a"] = -0.05
        fw = FeatureWindow(**valid_feature_window)
        assert fw.current_mean_a == pytest.approx(-0.05)

    def test_env_fields_optional(self, valid_feature_window):
        """Environmental fields may be absent in a VALID window."""
        # Remove all optional env fields — window should still be VALID
        fw = FeatureWindow(**valid_feature_window)
        assert fw.ambient_temperature_c is None
        assert fw.ambient_humidity_pct is None
        assert fw.environment_sample_age_ms is None


# ---------------------------------------------------------------------------
# TC-16 / TC-17: HealthDecision
# ---------------------------------------------------------------------------


class TestHealthDecision:
    def test_valid_health_decision(self, valid_health_decision):
        """A fully-specified NORMAL decision must be accepted."""
        hd = HealthDecision(**valid_health_decision)
        assert hd.health_state == HealthState.NORMAL

    def test_tc16_invalid_health_state_rejected(self, valid_health_decision):
        """TC-16: An unsupported health state string must be rejected."""
        valid_health_decision["health_state"] = "CRITICAL"
        with pytest.raises(ValidationError):
            HealthDecision(**valid_health_decision)

    def test_tc17_null_scores_for_sensor_error(self, valid_health_decision):
        """TC-17: SENSOR_ERROR with null anomaly_score and threshold must be accepted."""
        valid_health_decision["health_state"] = HealthState.SENSOR_ERROR
        valid_health_decision["anomaly_score"] = None
        valid_health_decision["anomaly_threshold"] = None
        valid_health_decision["model_version"] = None
        hd = HealthDecision(**valid_health_decision)
        assert hd.anomaly_score is None
        assert hd.anomaly_threshold is None

    def test_nan_anomaly_score_rejected(self, valid_health_decision):
        """NaN anomaly_score must be rejected."""
        valid_health_decision["anomaly_score"] = math.nan
        with pytest.raises(ValidationError, match="finite"):
            HealthDecision(**valid_health_decision)

    def test_inf_anomaly_score_rejected(self, valid_health_decision):
        """Infinite anomaly_score must be rejected."""
        valid_health_decision["anomaly_score"] = math.inf
        with pytest.raises(ValidationError, match="finite"):
            HealthDecision(**valid_health_decision)

    def test_negative_anomaly_score_accepted(self, valid_health_decision):
        """Isolation Forest scores may be negative; must not be clamped to [0, 1]."""
        valid_health_decision["anomaly_score"] = -0.5
        hd = HealthDecision(**valid_health_decision)
        assert hd.anomaly_score == pytest.approx(-0.5)

    def test_null_model_version_accepted(self, valid_health_decision):
        """model_version may be null when no model was applied."""
        valid_health_decision["model_version"] = None
        hd = HealthDecision(**valid_health_decision)
        assert hd.model_version is None


# ---------------------------------------------------------------------------
# TC-18: Rejection of undeclared extra fields
# ---------------------------------------------------------------------------


class TestExtraFieldRejection:
    def test_tc18_run_metadata_extra_rejected(self, valid_run_metadata):
        """TC-18: Extra field in RunMetadata must be rejected."""
        valid_run_metadata["spurious_field"] = "should_fail"
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            RunMetadata(**valid_run_metadata)

    def test_tc18_accel_sample_extra_rejected(self, valid_accel_sample):
        """TC-18: Extra field in AccelerationSample must be rejected."""
        valid_accel_sample["bearing_temp_c"] = 75.0
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            AccelerationSample(**valid_accel_sample)

    def test_tc18_current_sample_extra_rejected(self, valid_current_sample):
        """TC-18: Extra field in CurrentSample must be rejected."""
        valid_current_sample["raw_adc"] = 2048
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            CurrentSample(**valid_current_sample)

    def test_tc18_environment_sample_extra_rejected(self, valid_env_sample):
        """TC-18: Extra field in EnvironmentSample must be rejected."""
        valid_env_sample["motor_temperature"] = 80.0
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            EnvironmentSample(**valid_env_sample)

    def test_tc18_health_decision_extra_rejected(self, valid_health_decision):
        """TC-18: Extra field in HealthDecision must be rejected."""
        valid_health_decision["rul_hours"] = 500
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            HealthDecision(**valid_health_decision)

    def test_tc18_feature_window_extra_rejected(self, valid_feature_window):
        """TC-18: Extra field in FeatureWindow must be rejected."""
        valid_feature_window["fabricated_rul"] = 999
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            FeatureWindow(**valid_feature_window)


# ---------------------------------------------------------------------------
# TC-19: JSON serialization / deserialization round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def _round_trip(self, model_instance):
        """Serialize to JSON, deserialize, compare."""
        cls = type(model_instance)
        json_str = model_instance.model_dump_json()
        parsed = json.loads(json_str)
        restored = cls.model_validate(parsed)
        assert restored == model_instance
        return restored

    def test_tc19_run_metadata_round_trip(self, valid_run_metadata):
        """TC-19: RunMetadata round-trip via JSON."""
        original = RunMetadata(**valid_run_metadata)
        self._round_trip(original)

    def test_tc19_accel_sample_round_trip(self, valid_accel_sample):
        """TC-19: AccelerationSample round-trip via JSON."""
        original = AccelerationSample(**valid_accel_sample)
        self._round_trip(original)

    def test_tc19_current_sample_round_trip(self, valid_current_sample):
        """TC-19: CurrentSample round-trip via JSON."""
        original = CurrentSample(**valid_current_sample)
        self._round_trip(original)

    def test_tc19_env_sample_round_trip(self, valid_env_sample):
        """TC-19: EnvironmentSample round-trip via JSON."""
        original = EnvironmentSample(**valid_env_sample)
        self._round_trip(original)

    def test_tc19_operating_context_round_trip(self, valid_operating_context):
        """TC-19: OperatingContext round-trip via JSON."""
        original = OperatingContext(**valid_operating_context)
        self._round_trip(original)

    def test_tc19_feature_window_round_trip(self, valid_feature_window):
        """TC-19: FeatureWindow round-trip via JSON."""
        original = FeatureWindow(**valid_feature_window)
        self._round_trip(original)

    def test_tc19_health_decision_round_trip(self, valid_health_decision):
        """TC-19: HealthDecision round-trip via JSON."""
        original = HealthDecision(**valid_health_decision)
        self._round_trip(original)

    def test_tc19_invalid_window_round_trip(self):
        """TC-19: INVALID FeatureWindow with null features round-trips correctly."""
        original = FeatureWindow(
            device_id="esp32-dev-01",
            run_id="run-2024-001",
            window_start_us=0,
            window_end_us=500_000,
            accel_sample_count=0,
            current_sample_count=0,
            pwm_duty=0.0,
            operating_regime=OperatingRegime.UNKNOWN,
            window_quality=WindowQuality.INVALID,
            quality_flags=[QualityFlag.ACCEL_MISSING],
        )
        json_str = original.model_dump_json()
        parsed = json.loads(json_str)
        restored = FeatureWindow.model_validate(parsed)
        assert restored.vibration_rms_g is None
        assert restored.window_quality == WindowQuality.INVALID


# ---------------------------------------------------------------------------
# TC-20: JSON schemas synchronized with source models
# ---------------------------------------------------------------------------


class TestSchemaSync:
    """TC-20: The generated JSON schema files must match the current Pydantic models."""

    # Locate the contracts/ directory relative to this test file.
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent
    _CONTRACTS_DIR = _PROJECT_ROOT / "contracts"

    def _expected_filename(self, model_class) -> str:
        import re
        name = model_class.__name__
        s1 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
        return f"{s1.lower()}.schema.json"

    def _generate_schema(self, model_class) -> str:
        raw = model_class.model_json_schema()
        return json.dumps(raw, indent=2, sort_keys=True) + "\n"

    @pytest.mark.parametrize("model_class", [
        RunMetadata,
        AccelerationSample,
        CurrentSample,
        EnvironmentSample,
        OperatingContext,
        FeatureWindow,
        HealthDecision,
    ])
    def test_tc20_schema_file_exists_and_matches(self, model_class):
        """TC-20: Each model must have a schema file that matches the current definition."""
        filename = self._expected_filename(model_class)
        schema_path = self._CONTRACTS_DIR / filename
        assert schema_path.exists(), (
            f"Schema file missing: {schema_path}.  "
            f"Run `python scripts/export_schemas.py` to generate."
        )
        on_disk = schema_path.read_text(encoding="utf-8")
        expected = self._generate_schema(model_class)
        assert on_disk == expected, (
            f"Schema file {filename} is stale.  "
            f"Run `python scripts/export_schemas.py` to regenerate."
        )
