from __future__ import annotations

import copy
import asyncio

import httpx
import pytest

from pmnode.api import create_app
from pmnode.simulator import Scenario, SensorSimulator, SimulationConfig
from pmnode.storage import Storage


class ASGITestClient:
    """Small synchronous facade over HTTPX's supported async ASGI transport."""

    def __init__(self, app):
        self.app = app

    def request(self, method: str, url: str, **kwargs):
        async def send():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(
                    transport=transport, base_url="http://testserver") as client:
                return await client.request(method, url, **kwargs)

        return asyncio.run(send())

    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.request("POST", url, **kwargs)


@pytest.fixture
def healthy_run():
    return SensorSimulator(
        SimulationConfig(duration_s=2.2, sample_hz=20, batch_duration_s=1.0,
                         seed=42, device_id="test-node"),
        boot_id="sim-test-boot",
    ).generate()


@pytest.fixture
def valid_batch(healthy_run):
    return healthy_run.batches[0]


@pytest.fixture
def batch_dict(valid_batch):
    return copy.deepcopy(valid_batch.model_dump(mode="json"))


@pytest.fixture
def storage(tmp_path):
    return Storage(tmp_path / "test.sqlite3")


@pytest.fixture
def client(tmp_path):
    yield ASGITestClient(create_app(tmp_path / "api.sqlite3"))
