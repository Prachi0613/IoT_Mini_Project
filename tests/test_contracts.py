from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from pmnode.contracts import EnvironmentSample, MotionSample, TelemetryBatch


def test_valid_six_axis_motion_sample():
    sample = MotionSample(sample_seq=0, t_mono_us=0, ax_g=0.0, ay_g=0.0,
                          az_g=1.0, gx_dps=0.1, gy_dps=-0.1, gz_dps=0.0)
    assert sample.az_g == 1.0


@pytest.mark.parametrize("field", ["ax_g", "gx_dps"])
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_motion_rejected(field, value):
    data = dict(sample_seq=0, t_mono_us=0, ax_g=0.0, ay_g=0.0, az_g=1.0,
                gx_dps=0.0, gy_dps=0.0, gz_dps=0.0)
    data[field] = value
    with pytest.raises(ValidationError):
        MotionSample.model_validate(data)


def test_negative_timestamp_rejected():
    with pytest.raises(ValidationError):
        MotionSample(sample_seq=0, t_mono_us=-1, ax_g=0.0, ay_g=0.0,
                     az_g=1.0, gx_dps=0.0, gy_dps=0.0, gz_dps=0.0)


def test_invalid_humidity_rejected():
    with pytest.raises(ValidationError):
        EnvironmentSample(env_seq=0, t_mono_us=0, ambient_temperature_c=25.0,
                          ambient_humidity_pct=101.0, valid=True, error_code=None)


def test_missing_environment_representation():
    value = EnvironmentSample(env_seq=0, t_mono_us=0,
                              ambient_temperature_c=None,
                              ambient_humidity_pct=None, valid=False,
                              error_code="SIMULATED_DHT_DROPOUT")
    assert value.ambient_temperature_c is None


def test_invalid_environment_state_rejected():
    with pytest.raises(ValidationError):
        EnvironmentSample(env_seq=0, t_mono_us=0,
                          ambient_temperature_c=None,
                          ambient_humidity_pct=50.0, valid=False,
                          error_code="DROPOUT")


def test_unsupported_schema_version_rejected(batch_dict):
    batch_dict["schema_version"] = "2.0.0"
    with pytest.raises(ValidationError):
        TelemetryBatch.model_validate(batch_dict)


def test_unknown_field_rejected(batch_dict):
    batch_dict["motor_current_a"] = 1.0
    with pytest.raises(ValidationError):
        TelemetryBatch.model_validate(batch_dict)


@pytest.mark.parametrize("field", ["sample_seq", "t_mono_us"])
def test_invalid_motion_order_rejected(batch_dict, field):
    batch_dict["motion_samples"][1][field] = batch_dict["motion_samples"][0][field]
    with pytest.raises(ValidationError):
        TelemetryBatch.model_validate(batch_dict)


def test_json_round_trip_and_hash_stable(valid_batch):
    restored = TelemetryBatch.model_validate_json(valid_batch.model_dump_json())
    assert restored == valid_batch
    assert restored.payload_hash() == valid_batch.payload_hash()


def test_empty_batch_rejected(batch_dict):
    batch_dict["motion_samples"] = []
    batch_dict["environment_samples"] = []
    with pytest.raises(ValidationError):
        TelemetryBatch.model_validate(batch_dict)
