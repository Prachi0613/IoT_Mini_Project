from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pmnode.live.processor import LiveProcessor


ROOT = Path(__file__).parents[1]
FIRMWARE_FIXTURE = ROOT / "firmware" / "tests" / "fixtures" / "hardware_batch.json"


def hardware_payload(batch_seq: int) -> dict:
    """Build contract-test data shaped exactly like the firmware serializer output."""
    payload = json.loads(FIRMWARE_FIXTURE.read_text(encoding="utf-8"))
    payload["device_id"] = "esp32-http-contract-test"
    payload["boot_id"] = "boot-http-contract-test"
    payload["batch_seq"] = batch_seq
    first_sequence = batch_seq * 100
    payload["motion_samples"] = [
        {
            "sample_seq": sequence,
            "t_mono_us": sequence * 2_000,
            "ax_g": 0.01 + sequence * 0.00001,
            "ay_g": -0.008,
            "az_g": 1.002,
            "gx_dps": 0.25,
            "gy_dps": -0.12,
            "gz_dps": 0.04,
        }
        for sequence in range(first_sequence, first_sequence + 100)
    ]
    payload["environment_samples"] = payload["environment_samples"] if batch_seq == 0 else []
    payload["quality"] = {
        "i2c_read_errors": 0,
        "missed_deadlines": 0,
        "buffer_overruns": 0,
        "dht_failures": 1,
    }
    return payload


def test_hardware_http_ingestion_processing_and_restart_are_honest(client):
    first = hardware_payload(0)
    accepted = client.post("/api/v1/batches", json=first)
    assert accepted.status_code == 201
    assert accepted.json() == {
        "device_id": first["device_id"],
        "boot_id": first["boot_id"],
        "batch_seq": 0,
        "accepted_motion_samples": 100,
        "accepted_environment_samples": 1,
        "duplicate": False,
    }

    duplicate = client.post("/api/v1/batches", json=first)
    assert duplicate.status_code == 201
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["accepted_motion_samples"] == 0

    conflict = copy.deepcopy(first)
    conflict["motion_samples"][0]["ax_g"] += 0.1
    assert client.post("/api/v1/batches", json=conflict).status_code == 409

    # Seven firmware-sized batches advance the 250 ms watermark beyond the
    # first complete one-second feature window.
    for batch_seq in range(1, 7):
        assert client.post("/api/v1/batches", json=hardware_payload(batch_seq)).status_code == 201

    raw = client.get("/api/v1/telemetry", params={
        "device_id": first["device_id"], "boot_id": first["boot_id"],
        "sensor": "motion", "limit": 1000,
    })
    assert raw.status_code == 200
    assert raw.json()["source_type"] == "HARDWARE"
    assert len(raw.json()["samples"]) == 700
    assert raw.json()["samples"][0]["t_mono_us"] == 0
    assert raw.json()["samples"][-1]["sample_seq"] == 699

    environment = client.get("/api/v1/telemetry", params={
        "device_id": first["device_id"], "boot_id": first["boot_id"],
        "sensor": "environment", "limit": 20,
    }).json()["samples"][0]
    assert environment["valid"] == 0
    assert environment["ambient_temperature_c"] is None
    assert environment["ambient_humidity_pct"] is None
    assert environment["error_code"] == "DHT11_READ_FAILED"
    assert environment["received_at_utc"]

    future = datetime.now(timezone.utc) + timedelta(seconds=5)
    worker = LiveProcessor(client.app.state.storage, owner_id="hardware-http-worker")
    first_poll = worker.poll_once(future)
    worker.release_lease()
    assert first_poll["windows"] >= 1
    assert first_poll["predictions"] == 0

    restarted = LiveProcessor(client.app.state.storage, owner_id="hardware-http-restart")
    second_poll = restarted.poll_once(future + timedelta(seconds=1))
    restarted.release_lease()
    assert second_poll["windows"] == 0
    assert second_poll["predictions"] == 0

    overview = client.get(
        f"/api/v1/monitor/devices/{first['device_id']}/overview"
    ).json()
    assert overview["source_type"] == "HARDWARE"
    assert overview["latest_feature"] is not None
    assert overview["latest_prediction"] is None
    assert overview["current_health_state"] is None
    assert overview["processing_status"] == "MODEL_UNAVAILABLE"

