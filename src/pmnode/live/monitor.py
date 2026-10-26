"""Read-only monitoring queries and narrowly scoped event acknowledgement."""

from __future__ import annotations

import json

from pmnode.ml.registry import MLRegistry
from pmnode.storage import SessionNotFoundError, Storage

from .events import EventStore


class MonitorService:
    def __init__(self, storage: Storage):
        self.storage = storage
        self.registry = MLRegistry(storage)
        self.events = EventStore(storage)

    def devices(self) -> list[dict]:
        with self.storage.connect() as connection:
            rows = connection.execute(
                """SELECT s.device_id,COUNT(*) session_count,
                MAX(s.last_received_at_utc) last_received_at_utc,
                (SELECT ds.boot_id FROM device_sessions ds WHERE ds.device_id=s.device_id
                 ORDER BY ds.last_received_at_utc DESC LIMIT 1) latest_boot_id,
                (SELECT ds.source_type FROM device_sessions ds WHERE ds.device_id=s.device_id
                 ORDER BY ds.last_received_at_utc DESC LIMIT 1) source_type
                FROM device_sessions s GROUP BY s.device_id
                ORDER BY last_received_at_utc DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def overview(self, device_id: str) -> dict | None:
        with self.storage.connect() as connection:
            session = connection.execute(
                "SELECT * FROM device_sessions WHERE device_id=? "
                "ORDER BY last_received_at_utc DESC LIMIT 1", (device_id,),
            ).fetchone()
            if not session:
                return None
            key = (device_id, session["boot_id"])
            motion = connection.execute(
                "SELECT sample_seq,t_mono_us,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps,"
                "received_at_utc FROM motion_samples WHERE device_id=? AND boot_id=? "
                "ORDER BY sample_seq DESC LIMIT 1", key).fetchone()
            environment = connection.execute(
                "SELECT env_seq,t_mono_us,ambient_temperature_c,ambient_humidity_pct,"
                "valid,error_code,received_at_utc FROM environment_samples "
                "WHERE device_id=? AND boot_id=? ORDER BY env_seq DESC LIMIT 1", key).fetchone()
            feature = connection.execute(
                "SELECT payload_json FROM feature_windows WHERE device_id=? AND boot_id=? "
                "ORDER BY window_start_us DESC LIMIT 1", key).fetchone()
            prediction = connection.execute(
                """SELECT p.*,h.health_state FROM model_predictions p
                LEFT JOIN health_decisions h ON h.device_id=p.device_id AND h.boot_id=p.boot_id
                  AND h.window_start_us=p.window_start_us AND h.model_version=p.model_version
                  AND h.reference_version=p.reference_version
                WHERE p.device_id=? AND p.boot_id=?
                ORDER BY p.window_start_us DESC,p.created_at_utc DESC LIMIT 1""", key).fetchone()
            decision = connection.execute(
                "SELECT * FROM health_decisions WHERE device_id=? AND boot_id=? "
                "ORDER BY window_start_us DESC,created_at_utc DESC LIMIT 1", key).fetchone()
            checkpoint = connection.execute(
                "SELECT * FROM processing_checkpoints WHERE device_id=? AND boot_id=?", key
            ).fetchone()
            active_events = connection.execute(
                "SELECT COUNT(*) FROM health_events WHERE device_id=? AND boot_id=? "
                "AND acknowledged=0 AND severity!='INFO'", key).fetchone()[0]
        return {
            "device_id": device_id, "boot_id": session["boot_id"],
            "source_type": session["source_type"],
            "last_received_at_utc": session["last_received_at_utc"],
            "latest_motion": dict(motion) if motion else None,
            "latest_environment": dict(environment) if environment else None,
            "latest_feature": json.loads(feature["payload_json"]) if feature else None,
            "latest_prediction": dict(prediction) if prediction else None,
            "current_health_state": decision["health_state"] if decision else None,
            "processing_status": checkpoint["processing_status"] if checkpoint else "PENDING",
            "model_version": checkpoint["model_version"] if checkpoint else None,
            "reference_version": checkpoint["reference_version"] if checkpoint else None,
            "active_event_count": active_events,
        }

    def sessions(self, device_id: str) -> list[dict]:
        return self.storage.sessions(device_id)

    def history(self, device_id: str, boot_id: str, start_us: int | None,
                end_us: int | None, limit: int, offset: int) -> dict:
        session = self.storage.require_session(device_id, boot_id)
        conditions = ["f.device_id=?", "f.boot_id=?"]
        params: list = [device_id, boot_id]
        if start_us is not None:
            conditions.append("f.window_start_us>=?"); params.append(start_us)
        if end_us is not None:
            conditions.append("f.window_start_us<?"); params.append(end_us)
        params.extend([limit, offset])
        with self.storage.connect() as connection:
            features = connection.execute(
                f"SELECT f.payload_json FROM feature_windows f WHERE {' AND '.join(conditions)} "
                "ORDER BY f.window_start_us LIMIT ? OFFSET ?", params,
            ).fetchall()
            points = []
            for row in features:
                feature = json.loads(row["payload_json"])
                prediction = connection.execute(
                    """SELECT p.*,h.health_state FROM model_predictions p
                    LEFT JOIN health_decisions h ON h.device_id=p.device_id AND h.boot_id=p.boot_id
                      AND h.window_start_us=p.window_start_us AND h.model_version=p.model_version
                      AND h.reference_version=p.reference_version
                    WHERE p.device_id=? AND p.boot_id=? AND p.window_start_us=?
                    ORDER BY p.created_at_utc DESC LIMIT 1""",
                    (device_id, boot_id, feature["window_start_us"]),
                ).fetchone()
                decision = connection.execute(
                    "SELECT health_state FROM health_decisions WHERE device_id=? AND boot_id=? "
                    "AND window_start_us=? ORDER BY created_at_utc DESC LIMIT 1",
                    (device_id, boot_id, feature["window_start_us"]),
                ).fetchone()
                points.append({**feature,
                               "isolation_score": prediction["isolation_score"] if prediction else None,
                               "isolation_threshold": prediction["isolation_threshold"] if prediction else None,
                               "model_version": prediction["model_version"] if prediction else None,
                               "reference_version": prediction["reference_version"] if prediction else None,
                               "health_state": decision["health_state"] if decision else None})
            env_conditions = ["device_id=?", "boot_id=?"]
            env_params: list = [device_id, boot_id]
            if start_us is not None:
                env_conditions.append("t_mono_us>=?"); env_params.append(start_us)
            if end_us is not None:
                env_conditions.append("t_mono_us<?"); env_params.append(end_us)
            environment = [dict(row) for row in connection.execute(
                f"SELECT env_seq,t_mono_us,ambient_temperature_c,ambient_humidity_pct,"
                f"valid,error_code,received_at_utc FROM environment_samples WHERE {' AND '.join(env_conditions)} "
                "ORDER BY t_mono_us LIMIT 1000", env_params).fetchall()]
        return {"device_id": device_id, "boot_id": boot_id,
                "source_type": session["source_type"], "limit": limit,
                "offset": offset, "points": points,
                "environment_observations": environment,
                "decimation": "bounded ordered feature windows; no interpolation or smoothing"}

    def event_history(self, device_id: str | None, severity: str | None,
                      event_type: str | None, health_state: str | None,
                      acknowledged: bool | None, limit: int, offset: int) -> dict:
        clauses, params = [], []
        for column, value in (("device_id", device_id), ("severity", severity),
                              ("event_type", event_type), ("health_state", health_state)):
            if value is not None:
                clauses.append(f"{column}=?"); params.append(value)
        if acknowledged is not None:
            clauses.append("acknowledged=?"); params.append(int(acknowledged))
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self.storage.connect() as connection:
            total = connection.execute(f"SELECT COUNT(*) FROM health_events {where}", params).fetchone()[0]
            rows = connection.execute(
                f"SELECT * FROM health_events {where} ORDER BY detected_at_utc DESC,event_id DESC "
                "LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
        return {"events": [dict(row) for row in rows], "total": total,
                "limit": limit, "offset": offset}

    def models(self) -> list[dict]:
        result = []
        for model in self.registry.list_models():
            result.append({key: model[key] for key in (
                "model_version", "source_type", "feature_schema_version",
                "status", "created_at_utc", "metadata")})
        return result

    def baseline(self, model_version: str) -> dict | None:
        models = [m for m in self.models() if m["model_version"] == model_version]
        if not models:
            return None
        events = self.registry.recent_adaptation_events(model_version, 100)
        return {"model": models[0], "references": self.registry.list_references(model_version),
                "active_reference": next((r for r in self.registry.list_references(model_version)
                                          if r["active"]), None),
                "adaptation_enabled": False,
                "adaptation_summary": {
                    "approved": sum(e["decision"] == "ACTIVATED" for e in events),
                    "rejected": sum(e["decision"] == "REJECTED" for e in events),
                }, "adaptation_events": events}

    def status(self) -> dict:
        with self.storage.connect() as connection:
            worker = connection.execute(
                "SELECT lease_name,owner_id,status,heartbeat_at_utc,expires_at_utc,last_error "
                "FROM worker_lease WHERE lease_name='live-processor'").fetchone()
            checkpoint = connection.execute(
                "SELECT device_id,boot_id,next_window_start_us,processing_status,"
                "updated_at_utc,last_error FROM processing_checkpoints "
                "ORDER BY updated_at_utc DESC LIMIT 1").fetchone()
            backlog = connection.execute(
                """SELECT COUNT(*) FROM device_sessions s WHERE EXISTS(
                SELECT 1 FROM motion_samples m WHERE m.device_id=s.device_id AND m.boot_id=s.boot_id)
                AND NOT EXISTS(SELECT 1 FROM processing_checkpoints p
                  WHERE p.device_id=s.device_id AND p.boot_id=s.boot_id)""").fetchone()[0]
        return {"api_available": True, "database_available": self.storage.available(),
                "worker": dict(worker) if worker else None,
                "last_checkpoint": dict(checkpoint) if checkpoint else None,
                "sessions_without_checkpoint": backlog}
