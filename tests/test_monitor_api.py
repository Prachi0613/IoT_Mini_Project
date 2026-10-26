from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pmnode.live.processor import LiveProcessor
from pmnode.simulator import Scenario, SensorSimulator, SimulationConfig


def seed_live_client(client):
    run = SensorSimulator(SimulationConfig(
        scenario=Scenario.HEALTHY, duration_s=2.5, sample_hz=250,
        batch_duration_s=.5, seed=81, device_id="api-live-node",
    ), boot_id="api-live-boot").generate()
    for batch in run.batches:
        response = client.post("/api/v1/batches", json=batch.model_dump(mode="json"))
        assert response.status_code == 201
    worker = LiveProcessor(client.app.state.storage, owner_id="api-test-worker")
    worker.poll_once(datetime.now(timezone.utc) + timedelta(seconds=5))
    worker.release_lease()


def test_monitor_endpoints_return_persisted_bounded_data(client):
    seed_live_client(client)
    devices = client.get("/api/v1/monitor/devices")
    overview = client.get("/api/v1/monitor/devices/api-live-node/overview")
    sessions = client.get("/api/v1/monitor/devices/api-live-node/sessions")
    history = client.get(
        "/api/v1/monitor/devices/api-live-node/history",
        params={"boot_id": "api-live-boot", "start_us": 0,
                "end_us": 2_000_000, "limit": 1},
    )
    status = client.get("/api/v1/monitor/status")
    assert devices.status_code == overview.status_code == sessions.status_code == 200
    assert devices.json()["devices"][0]["source_type"] == "SIMULATED"
    assert overview.json()["processing_status"] == "MODEL_UNAVAILABLE"
    assert overview.json()["latest_prediction"] is None
    assert len(history.json()["points"]) == 1
    assert history.json()["points"][0]["isolation_score"] is None
    assert status.json()["database_available"] is True
    assert status.json()["last_checkpoint"]["boot_id"] == "api-live-boot"


def test_monitor_event_acknowledgement_and_validation(client):
    seed_live_client(client)
    response = client.get("/api/v1/monitor/events", params={"limit": 1, "offset": 0})
    assert response.status_code == 200 and response.json()["total"] == 1
    event = response.json()["events"][0]
    acknowledged = client.post(
        f"/api/v1/monitor/events/{event['event_id']}/acknowledge",
        json={"note": "reviewed by test"},
    )
    assert acknowledged.status_code == 200
    assert acknowledged.json()["acknowledged"] == 1
    assert acknowledged.json()["acknowledgement_note"] == "reviewed by test"
    assert client.post(
        f"/api/v1/monitor/events/{event['event_id']}/acknowledge",
        json={"note": "x" * 501},
    ).status_code == 422
    assert client.get("/api/v1/monitor/events", params={"limit": 501}).status_code == 422


def test_monitor_empty_models_and_unknown_resources(client):
    assert client.get("/api/v1/monitor/models").json() == {"models": []}
    assert client.get("/api/v1/monitor/models/missing/baseline").status_code == 404
    assert client.get("/api/v1/monitor/devices/missing/overview").status_code == 404
    assert client.post("/api/v1/monitor/events/999/acknowledge",
                       json={"note": None}).status_code == 404
