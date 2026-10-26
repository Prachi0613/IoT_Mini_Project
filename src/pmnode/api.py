"""FastAPI application for simulated and future hardware telemetry."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from .config import DEFAULT_SETTINGS, Settings
from .contracts import SCHEMA_VERSION, IngestAcknowledgement, TelemetryBatch
from .storage import BatchConflictError, SessionNotFoundError, Storage
from .live.monitor import MonitorService

API_VERSION = "v1"


class Acknowledgement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    note: str | None = Field(default=None, max_length=500)


def create_app(database_path: str | Path | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or DEFAULT_SETTINGS
    storage = Storage(database_path or settings.database_path)
    app = FastAPI(title="PMNode Telemetry Ingestion", version=SCHEMA_VERSION)
    app.state.storage = storage
    app.state.settings = settings
    monitor = MonitorService(storage)
    app.add_middleware(
        CORSMiddleware, allow_origins=list(settings.cors_origins),
        allow_credentials=False, allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def request_size_limit(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > settings.max_request_bytes:
            return JSONResponse(status_code=413, content={"error": "request_too_large"})
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        del request
        details = [{"type": e["type"], "location": list(e.get("loc", ())),
                    "message": e["msg"]} for e in exc.errors()]
        return JSONResponse(status_code=422,
                            content={"error": "validation_error", "details": details})

    @app.get("/api/v1/health")
    async def health():
        available = storage.available()
        return {"status": "ok" if available else "degraded",
                "api_version": API_VERSION, "schema_version": SCHEMA_VERSION,
                "database_available": available}

    @app.post("/api/v1/batches", response_model=IngestAcknowledgement, status_code=201)
    async def ingest(batch: TelemetryBatch):
        try:
            duplicate, _ = storage.insert_batch(batch)
        except BatchConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return IngestAcknowledgement(
            device_id=batch.device_id, boot_id=batch.boot_id, batch_seq=batch.batch_seq,
            accepted_motion_samples=0 if duplicate else len(batch.motion_samples),
            accepted_environment_samples=0 if duplicate else len(batch.environment_samples),
            duplicate=duplicate,
        )

    @app.get("/api/v1/sessions")
    async def sessions(device_id: str | None = None):
        return {"sessions": storage.sessions(device_id)}

    @app.get("/api/v1/latest")
    async def latest(device_id: str):
        result = storage.latest(device_id)
        if result is None:
            raise HTTPException(status_code=404, detail="device has no recorded telemetry")
        return result

    @app.get("/api/v1/telemetry")
    async def telemetry(
        device_id: str, boot_id: str,
        sensor: str = Query(pattern="^(motion|environment)$"),
        limit: int = Query(default=100, ge=1, le=settings.max_query_limit),
        offset: int = Query(default=0, ge=0),
    ):
        try:
            rows = storage.telemetry(device_id, boot_id, sensor, limit, offset)
            session = storage.require_session(device_id, boot_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"device_id": device_id, "boot_id": boot_id,
                "source_type": session["source_type"], "sensor": sensor,
                "limit": limit, "offset": offset, "samples": rows}

    @app.get("/api/v1/monitor/devices")
    async def monitor_devices():
        return {"devices": monitor.devices()}

    @app.get("/api/v1/monitor/devices/{device_id}/overview")
    async def monitor_overview(device_id: str):
        result = monitor.overview(device_id)
        if result is None:
            raise HTTPException(status_code=404, detail="device not found")
        return result

    @app.get("/api/v1/monitor/devices/{device_id}/sessions")
    async def monitor_sessions(device_id: str):
        return {"sessions": monitor.sessions(device_id)}

    @app.get("/api/v1/monitor/devices/{device_id}/history")
    async def monitor_history(
        device_id: str, boot_id: str,
        start_us: int | None = Query(default=None, ge=0),
        end_us: int | None = Query(default=None, ge=1),
        limit: int = Query(default=300, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
    ):
        if start_us is not None and end_us is not None and end_us <= start_us:
            raise HTTPException(status_code=422, detail="end_us must exceed start_us")
        try:
            return monitor.history(device_id, boot_id, start_us, end_us, limit, offset)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/monitor/devices/{device_id}/events")
    async def monitor_device_events(
        device_id: str, severity: str | None = None,
        event_type: str | None = None, health_state: str | None = None,
        acknowledged: bool | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ):
        return monitor.event_history(device_id, severity, event_type, health_state,
                                     acknowledged, limit, offset)

    @app.get("/api/v1/monitor/events")
    async def monitor_events(
        device_id: str | None = None, severity: str | None = None,
        event_type: str | None = None, health_state: str | None = None,
        acknowledged: bool | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ):
        return monitor.event_history(device_id, severity, event_type, health_state,
                                     acknowledged, limit, offset)

    @app.get("/api/v1/monitor/models")
    async def monitor_models():
        return {"models": monitor.models()}

    @app.get("/api/v1/monitor/models/{model_version}/baseline")
    async def monitor_baseline(model_version: str):
        result = monitor.baseline(model_version)
        if result is None:
            raise HTTPException(status_code=404, detail="model not found")
        return result

    @app.get("/api/v1/monitor/status")
    async def monitor_status():
        return monitor.status()

    @app.post("/api/v1/monitor/events/{event_id}/acknowledge")
    async def acknowledge_event(event_id: int, body: Acknowledgement):
        result = monitor.events.acknowledge(event_id, body.note)
        if result is None:
            raise HTTPException(status_code=404, detail="event not found")
        return result

    return app


app = create_app()
