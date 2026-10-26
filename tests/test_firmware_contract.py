from __future__ import annotations

import json
import re
from pathlib import Path

from pmnode.contracts import SourceType, TelemetryBatch


ROOT = Path(__file__).parents[1]
FIRMWARE = ROOT / "firmware"
FIXTURE = FIRMWARE / "tests" / "fixtures" / "hardware_batch.json"


def source(name: str) -> str:
    return (FIRMWARE / name).read_text(encoding="utf-8")


def constant(text: str, name: str) -> int:
    match = re.search(rf"\b{name}\s*=\s*(\d+)", text)
    assert match, f"missing firmware constant {name}"
    return int(match.group(1))


def test_representative_firmware_payload_matches_authoritative_contract():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    batch = TelemetryBatch.model_validate(payload)
    assert batch.source_type is SourceType.HARDWARE
    assert batch.requested_mpu_hz == 500
    assert batch.accel_range_g == 4.0 and batch.gyro_range_dps == 250.0
    assert batch.dlpf_config == 1
    assert [sample.sample_seq for sample in batch.motion_samples] == [100, 101]
    assert [sample.t_mono_us for sample in batch.motion_samples] == [200000, 202007]
    failed = batch.environment_samples[0]
    assert not failed.valid and failed.ambient_temperature_c is None
    assert failed.ambient_humidity_pct is None and failed.error_code == "DHT11_READ_FAILED"
    assert batch.payload_hash() == TelemetryBatch.model_validate(payload).payload_hash()


def test_firmware_batch_and_retry_policies_are_bounded():
    config = source("include/config.example.h")
    policy = source("include/firmware_policy.h")
    main = source("src/main.cpp")
    batch_size = constant(config, "MOTION_SAMPLES_PER_BATCH")
    assert 0 < batch_size <= constant(policy, "BACKEND_MAX_MOTION_SAMPLES")
    assert constant(config, "BATCH_QUEUE_LENGTH") == 4
    assert constant(config, "HTTP_MAX_ATTEMPTS") == 3
    assert batch_size * 256 < constant(policy, "BACKEND_MAX_REQUEST_BYTES")
    serialize_position = main.index("serializeTelemetryBatch(*batch, stable_payload)")
    retry_position = main.index("for (uint8_t attempt")
    assert serialize_position < retry_position
    assert "deliverBatch(*batch, stable_payload)" in main
    assert "recordAbandonedBatch(*batch)" in main
    assert "g_lost_motion_samples" in main
    assert "g_lost_environment_samples" in main


def test_sensor_units_timing_and_identity_design_are_explicit():
    config = source("include/config.example.h")
    policy = source("include/firmware_policy.h")
    main = source("src/main.cpp")
    telemetry = source("src/telemetry.cpp")
    assert constant(config, "REQUESTED_MPU_HZ") == 500
    assert constant(config, "DLPF_CONFIG") == 1
    assert "ACCEL_RANGE_G = 4.0F" in config
    assert "GYRO_RANGE_DPS = 250.0F" in config
    assert "MPU6050_ACCEL_LSB_PER_G_4G = 8192.0F" in policy
    assert "MPU6050_GYRO_LSB_PER_DPS_250 = 131.0F" in policy
    assert "esp_timer_get_time()" in main
    assert "attempt_sequence += missed" in main
    assert telemetry.count("esp_random()") == 4
    assert 'TELEMETRY_SOURCE_TYPE[] = "HARDWARE"' in policy


def test_network_configuration_and_acknowledgements_fail_closed():
    network = source("src/network.cpp")
    main = source("src/main.cpp")
    assert 'host.equalsIgnoreCase("localhost")' in network
    assert 'host == "127.0.0.1"' in network
    assert "validateNetworkConfiguration()" in main
    for field in (
        "device_id", "boot_id", "batch_seq", "duplicate",
        "accepted_motion_samples", "accepted_environment_samples",
    ):
        assert f'response["{field}"].is<' in network


def test_firmware_has_no_prohibited_sensor_or_motor_control_dependencies():
    code = "\n".join(path.read_text(encoding="utf-8")
                     for path in (FIRMWARE / "src").glob("*.cpp"))
    for prohibited in ("ACS712", "INA219", "ADXL345", "analogWrite(", "ledcWrite("):
        assert prohibited not in code
    assert "bytes 6..7 are MPU die temperature and are deliberately not transmitted" in code
