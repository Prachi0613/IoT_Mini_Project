from __future__ import annotations

import copy
import json
import math


def post_run(client, run):
    return [client.post("/api/v1/batches", json=batch.model_dump(mode="json"))
            for batch in run.batches]


def test_health_endpoint(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "api_version": "v1",
                               "schema_version": "1.0.0", "database_available": True}


def test_successful_post_acknowledges_identity(client, valid_batch):
    response = client.post("/api/v1/batches", json=valid_batch.model_dump(mode="json"))
    assert response.status_code == 201
    body = response.json()
    assert (body["device_id"], body["boot_id"], body["batch_seq"]) == (
        valid_batch.device_id, valid_batch.boot_id, valid_batch.batch_seq)
    assert body["accepted_motion_samples"] == len(valid_batch.motion_samples)
    assert body["duplicate"] is False


def test_identical_replay_api(client, valid_batch):
    payload = valid_batch.model_dump(mode="json")
    client.post("/api/v1/batches", json=payload)
    response = client.post("/api/v1/batches", json=payload)
    assert response.status_code == 201
    assert response.json()["duplicate"] is True
    assert response.json()["accepted_motion_samples"] == 0


def test_conflicting_replay_api_returns_409(client, valid_batch):
    payload = valid_batch.model_dump(mode="json")
    client.post("/api/v1/batches", json=payload)
    changed = copy.deepcopy(payload)
    changed["motion_samples"][0]["ax_g"] += 0.1
    assert client.post("/api/v1/batches", json=changed).status_code == 409


def test_malformed_json_rejected(client):
    response = client.post("/api/v1/batches", content=b'{"broken":',
                           headers={"content-type": "application/json"})
    assert response.status_code == 422


def test_unsupported_schema_rejected(client, batch_dict):
    batch_dict["schema_version"] = "9.0.0"
    assert client.post("/api/v1/batches", json=batch_dict).status_code == 422


def test_invalid_sensor_data_rejected(client, batch_dict):
    batch_dict["motion_samples"][0]["az_g"] = None
    assert client.post("/api/v1/batches", json=batch_dict).status_code == 422


def test_non_finite_raw_json_rejected(client, batch_dict):
    batch_dict["motion_samples"][0]["az_g"] = math.nan
    response = client.post("/api/v1/batches",
                           content=json.dumps(batch_dict, allow_nan=True),
                           headers={"content-type": "application/json"})
    assert response.status_code == 422


def test_session_listing_and_filter(client, healthy_run):
    post_run(client, healthy_run)
    sessions = client.get("/api/v1/sessions", params={"device_id": "test-node"})
    assert sessions.status_code == 200
    assert sessions.json()["sessions"][0]["source_type"] == "SIMULATED"
    assert client.get("/api/v1/sessions", params={"device_id": "absent"}).json() == {
        "sessions": []}


def test_latest_retrieval(client, healthy_run):
    post_run(client, healthy_run)
    response = client.get("/api/v1/latest", params={"device_id": "test-node"})
    assert response.status_code == 200
    assert response.json()["latest_motion"]["sample_seq"] == 43
    assert "t_mono_us" in response.json()["latest_environment"]


def test_telemetry_pagination(client, healthy_run):
    post_run(client, healthy_run)
    first = client.get("/api/v1/telemetry", params={
        "device_id": "test-node", "boot_id": "sim-test-boot",
        "sensor": "motion", "limit": 3, "offset": 0,
    }).json()
    second = client.get("/api/v1/telemetry", params={
        "device_id": "test-node", "boot_id": "sim-test-boot",
        "sensor": "motion", "limit": 3, "offset": 3,
    }).json()
    assert [r["sample_seq"] for r in first["samples"]] == [0, 1, 2]
    assert [r["sample_seq"] for r in second["samples"]] == [3, 4, 5]
    assert first["source_type"] == "SIMULATED"


def test_missing_device_responses(client):
    assert client.get("/api/v1/latest", params={"device_id": "missing"}).status_code == 404
    assert client.get("/api/v1/telemetry", params={
        "device_id": "missing", "boot_id": "missing", "sensor": "motion"
    }).status_code == 404


def test_pagination_limit_bounded(client):
    response = client.get("/api/v1/telemetry", params={
        "device_id": "x", "boot_id": "y", "sensor": "motion", "limit": 1001})
    assert response.status_code == 422
