"""Restart-safe polling worker that finalizes and scores new time windows."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pmnode.ml.features import (FeatureConfig, WindowQuality,
                                extract_single_window)
from pmnode.ml.health import HealthEngine
from pmnode.ml.model import ModelBundle, load_model
from pmnode.ml.registry import MLRegistry, utc_now
from pmnode.storage import Storage

from .events import EventStore


@dataclass(frozen=True)
class LiveProcessorConfig:
    window_duration_s: float = 1.0
    allowed_lateness_s: float = 0.25
    idle_flush_s: float = 3.0
    max_windows_per_poll: int = 100
    lease_seconds: float = 15.0
    sqlite_retries: int = 3


class WorkerLeaseError(RuntimeError):
    pass


class LiveProcessor:
    def __init__(self, storage: Storage,
                 config: LiveProcessorConfig = LiveProcessorConfig(),
                 owner_id: str | None = None):
        self.storage = storage
        self.config = config
        self.owner_id = owner_id or f"worker-{uuid.uuid4().hex}"
        self.registry = MLRegistry(storage)
        self.events = EventStore(storage)
        self._model_cache: dict[str, tuple[str, ModelBundle]] = {}

    @property
    def duration_us(self) -> int:
        return round(self.config.window_duration_s * 1_000_000)

    def acquire_lease(self) -> None:
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=self.config.lease_seconds)
        with self.storage.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT owner_id,expires_at_utc FROM worker_lease WHERE lease_name='live-processor'"
            ).fetchone()
            if row and row["owner_id"] != self.owner_id and datetime.fromisoformat(
                    row["expires_at_utc"]) > now:
                raise WorkerLeaseError(f"live worker lease is held by {row['owner_id']}")
            connection.execute(
                "INSERT INTO worker_lease VALUES('live-processor',?,'RUNNING',?,?,NULL) "
                "ON CONFLICT(lease_name) DO UPDATE SET owner_id=excluded.owner_id,"
                "status='RUNNING',heartbeat_at_utc=excluded.heartbeat_at_utc,"
                "expires_at_utc=excluded.expires_at_utc,last_error=NULL",
                (self.owner_id, now.isoformat(), expires.isoformat()),
            )

    def heartbeat(self, status: str = "RUNNING", error: str | None = None) -> None:
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=self.config.lease_seconds)
        with self.storage.connect() as connection:
            connection.execute(
                "UPDATE worker_lease SET status=?,heartbeat_at_utc=?,expires_at_utc=?,"
                "last_error=? WHERE lease_name='live-processor' AND owner_id=?",
                (status, now.isoformat(), expires.isoformat(), error, self.owner_id),
            )

    def release_lease(self) -> None:
        """Make an orderly worker stop immediately available to a successor."""
        now = datetime.now(timezone.utc).isoformat()
        with self.storage.connect() as connection:
            connection.execute(
                "UPDATE worker_lease SET status='STOPPED',heartbeat_at_utc=?,"
                "expires_at_utc=? WHERE lease_name='live-processor' AND owner_id=?",
                (now, now, self.owner_id),
            )

    def poll_once(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        self.acquire_lease()
        totals = {"sessions": 0, "windows": 0, "predictions": 0,
                  "invalid": 0, "events": 0, "errors": 0}
        for attempt in range(self.config.sqlite_retries):
            try:
                for session in self._sessions_with_motion():
                    totals["sessions"] += 1
                    result = self._process_session(session, now)
                    for key in totals:
                        if key in result:
                            totals[key] += result[key]
                self.heartbeat("IDLE")
                return totals
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt + 1 == self.config.sqlite_retries:
                    self.heartbeat("ERROR", str(exc)); raise
                time.sleep(0.05 * (attempt + 1))
        return totals

    def _sessions_with_motion(self) -> list[dict]:
        with self.storage.connect() as connection:
            rows = connection.execute(
                """SELECT s.device_id,s.boot_id,s.source_type,
                m.min_t,m.max_t,
                (SELECT MAX(b.received_at_utc) FROM ingested_batches b
                 WHERE b.device_id=s.device_id AND b.boot_id=s.boot_id) last_received_at_utc,
                (SELECT MIN(b.requested_mpu_hz) FROM ingested_batches b
                 WHERE b.device_id=s.device_id AND b.boot_id=s.boot_id) requested_hz_min,
                (SELECT MAX(b.requested_mpu_hz) FROM ingested_batches b
                 WHERE b.device_id=s.device_id AND b.boot_id=s.boot_id) requested_hz_max
                FROM device_sessions s JOIN (
                  SELECT device_id,boot_id,MIN(t_mono_us) min_t,MAX(t_mono_us) max_t
                  FROM motion_samples GROUP BY device_id,boot_id
                ) m USING(device_id,boot_id)
                ORDER BY s.device_id,s.boot_id"""
            ).fetchall()
        return [dict(row) for row in rows]

    def _process_session(self, session: dict, now: datetime) -> dict:
        result = {"windows": 0, "predictions": 0, "invalid": 0,
                  "events": 0, "errors": 0}
        if session["requested_hz_min"] != session["requested_hz_max"]:
            self._checkpoint_error(session, "requested sampling rate changed")
            result["errors"] += 1; return result
        checkpoint = self._checkpoint(session)
        self._record_late_data(session, checkpoint)
        next_start = checkpoint["next_window_start_us"]
        last_received = datetime.fromisoformat(session["last_received_at_utc"])
        idle = (now - last_received).total_seconds() >= self.config.idle_flush_s
        watermark = session["max_t"] - round(self.config.allowed_lateness_s * 1_000_000)
        processed = 0
        while processed < self.config.max_windows_per_poll:
            end = next_start + self.duration_us
            complete = end <= watermark
            idle_candidate = idle and next_start <= session["max_t"] < end
            if not complete and not idle_candidate:
                break
            feature = self._feature(session, next_start, end)
            self.registry.save_features([feature])
            status, prediction, event_count, health_snapshot = self._score_feature(
                feature, checkpoint["health_state_json"])
            result["windows"] += 1
            result["predictions"] += int(prediction)
            result["invalid"] += int(feature.window_quality == WindowQuality.INVALID)
            result["events"] += event_count
            next_start = end; processed += 1
            checkpoint["health_state_json"] = health_snapshot
            self._save_checkpoint(session, next_start, status, health_snapshot)
            if idle_candidate:
                break
        if processed == 0:
            self._save_checkpoint(session, next_start,
                                  checkpoint.get("processing_status", "PENDING"),
                                  checkpoint["health_state_json"])
        return result

    def _checkpoint(self, session: dict) -> dict:
        with self.storage.connect() as connection:
            row = connection.execute(
                "SELECT * FROM processing_checkpoints WHERE device_id=? AND boot_id=?",
                (session["device_id"], session["boot_id"]),
            ).fetchone()
        if row:
            return dict(row)
        origin = (session["min_t"] // self.duration_us) * self.duration_us
        engine = HealthEngine(1.0)
        return {"next_window_start_us": origin, "last_seen_t_mono_us": session["max_t"],
                "processing_status": "PENDING",
                "health_state_json": engine.snapshot(), "updated_at_utc": "1970-01-01T00:00:00+00:00"}

    def _feature(self, session: dict, start: int, end: int):
        with self.storage.connect() as connection:
            motion = [dict(row) for row in connection.execute(
                "SELECT * FROM motion_samples WHERE device_id=? AND boot_id=? "
                "AND t_mono_us>=? AND t_mono_us<? ORDER BY t_mono_us",
                (session["device_id"], session["boot_id"], start, end),
            ).fetchall()]
            environment = [dict(row) for row in connection.execute(
                "SELECT * FROM environment_samples WHERE device_id=? AND boot_id=? "
                "AND t_mono_us<=? ORDER BY t_mono_us DESC LIMIT 1",
                (session["device_id"], session["boot_id"], end),
            ).fetchall()][::-1]
        config = replace(FeatureConfig(), window_duration_s=self.config.window_duration_s,
                         expected_sampling_hz=float(session["requested_hz_min"]))
        return extract_single_window(
            motion, environment, device_id=session["device_id"],
            boot_id=session["boot_id"], source_type=session["source_type"],
            window_start_us=start, window_end_us=end, config=config)

    def _active_model(self, source_type: str) -> tuple[ModelBundle | None, str]:
        with self.storage.connect() as connection:
            row = connection.execute(
                "SELECT model_version,artifact_path,feature_schema_version FROM model_versions "
                "WHERE source_type=? AND status LIKE 'ACTIVE_%' ORDER BY created_at_utc DESC LIMIT 1",
                (source_type,),
            ).fetchone()
        if not row:
            return None, "MODEL_UNAVAILABLE"
        cached = self._model_cache.get(source_type)
        if cached and cached[0] == row["model_version"]:
            return cached[1], "READY"
        try:
            bundle = load_model(Path(row["artifact_path"]))
            if bundle.source_type != source_type or bundle.feature_schema_version != row["feature_schema_version"]:
                raise ModelCompatibilityError("active model metadata is incompatible")
        except Exception:
            return None, "MODEL_INCOMPATIBLE"
        self._model_cache[source_type] = (row["model_version"], bundle)
        return bundle, "READY"

    def _score_feature(self, feature, health_snapshot: str):
        bundle, status = self._active_model(feature.source_type.value)
        if bundle is None:
            created = self.events.create_processing_event(
                device_id=feature.device_id, boot_id=feature.boot_id,
                source_type=feature.source_type.value, event_type=status,
                window_start_us=feature.window_start_us,
                window_end_us=feature.window_end_us)
            return status, False, int(created), health_snapshot
        active = self.registry.active_reference(bundle.model_version)
        reference_version = active["reference_version"] if active else 1
        if active:
            bundle.reference.center = active["center"]; bundle.reference.spread = active["spread"]
            bundle.reference.threshold = active["threshold"]
        engine = HealthEngine(bundle.isolation_threshold)
        try:
            engine.restore(health_snapshot)
        except (ValueError, json.JSONDecodeError):
            pass
        previous = engine.state
        if feature.window_quality == WindowQuality.VALID:
            isolation, reference = bundle.score([feature])
            isolation_score, reference_score = float(isolation[0]), float(reference[0])
            state = engine.update(feature.device_id, feature.boot_id,
                                  feature.window_start_us, isolation_score, True)
            self.registry.save_prediction(feature, bundle, reference_version,
                                         isolation_score, reference_score, state.value)
            prediction = True
        else:
            state = engine.update(feature.device_id, feature.boot_id,
                                  feature.window_start_us, None, False)
            self.registry.save_health_decision(feature, bundle.model_version,
                                              reference_version, state.value)
            prediction = False
        created = self.events.create_transition(
            device_id=feature.device_id, boot_id=feature.boot_id,
            source_type=feature.source_type.value, previous_state=previous.value,
            health_state=state.value, window_start_us=feature.window_start_us,
            window_end_us=feature.window_end_us, model_version=bundle.model_version,
            baseline_version=reference_version)
        return "READY", prediction, int(created), engine.snapshot()

    def _save_checkpoint(self, session, next_start, status, health_snapshot):
        with self.storage.connect() as connection:
            model, _ = self._active_model(session["source_type"])
            active = self.registry.active_reference(model.model_version) if model else None
            connection.execute(
                "INSERT INTO processing_checkpoints VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(device_id,boot_id) DO UPDATE SET "
                "next_window_start_us=excluded.next_window_start_us,"
                "last_seen_t_mono_us=excluded.last_seen_t_mono_us,"
                "processing_status=excluded.processing_status,model_version=excluded.model_version,"
                "reference_version=excluded.reference_version,health_state_json=excluded.health_state_json,"
                "last_error=NULL,updated_at_utc=excluded.updated_at_utc",
                (session["device_id"], session["boot_id"], next_start,
                 session["max_t"], status, model.model_version if model else None,
                 active["reference_version"] if active else None,
                 health_snapshot, None, utc_now()),
            )

    def _checkpoint_error(self, session, message):
        checkpoint = self._checkpoint(session)
        with self.storage.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO processing_checkpoints VALUES(?,?,?,?,?,?,?,?,?,?)",
                (session["device_id"], session["boot_id"],
                 checkpoint["next_window_start_us"], session["max_t"], "ERROR",
                 None, None, checkpoint["health_state_json"], message, utc_now()),
            )

    def _record_late_data(self, session, checkpoint):
        if checkpoint["updated_at_utc"].startswith("1970-"):
            return
        with self.storage.connect() as connection:
            row = connection.execute(
                "SELECT MIN(t_mono_us) late_t FROM motion_samples WHERE device_id=? "
                "AND boot_id=? AND t_mono_us<? AND received_at_utc>?",
                (session["device_id"], session["boot_id"],
                 checkpoint["next_window_start_us"], checkpoint["updated_at_utc"]),
            ).fetchone()
            if row["late_t"] is not None:
                key = f"{session['device_id']}|{session['boot_id']}|LATE|{row['late_t']}"
                connection.execute(
                    "INSERT OR IGNORE INTO processing_issues(issue_key,device_id,boot_id,"
                    "issue_type,details,detected_at_utc) VALUES(?,?,?,?,?,?)",
                    (key, session["device_id"], session["boot_id"], "LATE_DATA",
                     "record arrived after its feature window was finalized; historical result unchanged",
                     utc_now()),
                )
