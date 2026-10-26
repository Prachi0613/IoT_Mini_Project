"""SQLite persistence with atomic ingestion and conflict-safe replay handling."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import TelemetryBatch


class BatchConflictError(RuntimeError):
    """Raised when a batch identity is reused with different content."""


class SessionNotFoundError(LookupError):
    """Raised when a requested device/boot session does not exist."""


class Storage:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS device_sessions (
                    device_id TEXT NOT NULL, boot_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    first_received_at_utc TEXT NOT NULL,
                    last_received_at_utc TEXT NOT NULL,
                    PRIMARY KEY(device_id, boot_id)
                );
                CREATE TABLE IF NOT EXISTS ingested_batches (
                    device_id TEXT NOT NULL, boot_id TEXT NOT NULL,
                    batch_seq INTEGER NOT NULL, payload_hash TEXT NOT NULL,
                    received_at_utc TEXT NOT NULL, requested_mpu_hz INTEGER NOT NULL,
                    accel_range_g REAL NOT NULL, gyro_range_dps REAL NOT NULL,
                    dlpf_config INTEGER NOT NULL,
                    i2c_read_errors INTEGER NOT NULL,
                    missed_deadlines INTEGER NOT NULL,
                    buffer_overruns INTEGER NOT NULL,
                    dht_failures INTEGER NOT NULL,
                    PRIMARY KEY(device_id, boot_id, batch_seq),
                    FOREIGN KEY(device_id, boot_id)
                      REFERENCES device_sessions(device_id, boot_id)
                );
                CREATE TABLE IF NOT EXISTS motion_samples (
                    device_id TEXT NOT NULL, boot_id TEXT NOT NULL,
                    batch_seq INTEGER NOT NULL, sample_seq INTEGER NOT NULL,
                    t_mono_us INTEGER NOT NULL,
                    ax_g REAL NOT NULL, ay_g REAL NOT NULL, az_g REAL NOT NULL,
                    gx_dps REAL NOT NULL, gy_dps REAL NOT NULL, gz_dps REAL NOT NULL,
                    received_at_utc TEXT NOT NULL,
                    PRIMARY KEY(device_id, boot_id, sample_seq),
                    FOREIGN KEY(device_id, boot_id, batch_seq)
                      REFERENCES ingested_batches(device_id, boot_id, batch_seq)
                );
                CREATE TABLE IF NOT EXISTS environment_samples (
                    device_id TEXT NOT NULL, boot_id TEXT NOT NULL,
                    batch_seq INTEGER NOT NULL, env_seq INTEGER NOT NULL,
                    t_mono_us INTEGER NOT NULL,
                    ambient_temperature_c REAL, ambient_humidity_pct REAL,
                    valid INTEGER NOT NULL CHECK(valid IN (0,1)), error_code TEXT,
                    received_at_utc TEXT NOT NULL,
                    PRIMARY KEY(device_id, boot_id, env_seq),
                    FOREIGN KEY(device_id, boot_id, batch_seq)
                      REFERENCES ingested_batches(device_id, boot_id, batch_seq)
                );
                CREATE INDEX IF NOT EXISTS idx_motion_session_time
                  ON motion_samples(device_id, boot_id, sample_seq);
                CREATE INDEX IF NOT EXISTS idx_environment_session_time
                  ON environment_samples(device_id, boot_id, env_seq);
                CREATE INDEX IF NOT EXISTS idx_batches_received
                  ON ingested_batches(device_id, received_at_utc DESC);
                CREATE TABLE IF NOT EXISTS feature_windows (
                    feature_schema_version TEXT NOT NULL,
                    device_id TEXT NOT NULL, boot_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    window_start_us INTEGER NOT NULL, window_end_us INTEGER NOT NULL,
                    quality_status TEXT NOT NULL, payload_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    PRIMARY KEY(feature_schema_version,device_id,boot_id,window_start_us,window_end_us),
                    FOREIGN KEY(device_id,boot_id)
                      REFERENCES device_sessions(device_id,boot_id)
                );
                CREATE TABLE IF NOT EXISTS model_predictions (
                    device_id TEXT NOT NULL, boot_id TEXT NOT NULL,
                    window_start_us INTEGER NOT NULL, window_end_us INTEGER NOT NULL,
                    model_version TEXT NOT NULL, reference_version INTEGER NOT NULL,
                    isolation_score REAL NOT NULL, isolation_threshold REAL NOT NULL,
                    reference_score REAL NOT NULL, reference_threshold REAL NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    PRIMARY KEY(device_id,boot_id,window_start_us,model_version,reference_version)
                );
                CREATE TABLE IF NOT EXISTS health_decisions (
                    device_id TEXT NOT NULL, boot_id TEXT NOT NULL,
                    window_start_us INTEGER NOT NULL, model_version TEXT NOT NULL,
                    reference_version INTEGER NOT NULL, health_state TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    PRIMARY KEY(device_id,boot_id,window_start_us,model_version,reference_version)
                );
                CREATE TABLE IF NOT EXISTS model_versions (
                    model_version TEXT PRIMARY KEY, source_type TEXT NOT NULL,
                    feature_schema_version TEXT NOT NULL, artifact_path TEXT NOT NULL,
                    status TEXT NOT NULL, metadata_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reference_versions (
                    model_version TEXT NOT NULL, reference_version INTEGER NOT NULL,
                    center_json TEXT NOT NULL, spread_json TEXT NOT NULL,
                    threshold REAL NOT NULL, approved INTEGER NOT NULL,
                    active INTEGER NOT NULL, previous_version INTEGER,
                    reason TEXT NOT NULL, eligible_windows INTEGER NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    PRIMARY KEY(model_version,reference_version)
                );
                CREATE TABLE IF NOT EXISTS adaptation_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_version TEXT NOT NULL, reference_version INTEGER NOT NULL,
                    device_id TEXT, boot_id TEXT, window_start_us INTEGER,
                    decision TEXT NOT NULL, reason TEXT NOT NULL,
                    eligible_windows INTEGER NOT NULL, created_at_utc TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_features_session
                  ON feature_windows(device_id,boot_id,window_start_us);
                CREATE INDEX IF NOT EXISTS idx_predictions_session
                  ON model_predictions(device_id,boot_id,window_start_us);
                CREATE TABLE IF NOT EXISTS processing_checkpoints (
                    device_id TEXT NOT NULL, boot_id TEXT NOT NULL,
                    next_window_start_us INTEGER NOT NULL,
                    last_seen_t_mono_us INTEGER NOT NULL,
                    processing_status TEXT NOT NULL,
                    model_version TEXT,
                    reference_version INTEGER,
                    health_state_json TEXT NOT NULL,
                    last_error TEXT,
                    updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY(device_id,boot_id),
                    FOREIGN KEY(device_id,boot_id)
                      REFERENCES device_sessions(device_id,boot_id)
                );
                CREATE TABLE IF NOT EXISTS health_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL UNIQUE,
                    device_id TEXT NOT NULL, boot_id TEXT NOT NULL,
                    source_type TEXT NOT NULL, event_type TEXT NOT NULL,
                    health_state TEXT NOT NULL, severity TEXT NOT NULL,
                    window_start_us INTEGER, window_end_us INTEGER,
                    model_version TEXT, baseline_version INTEGER,
                    detected_at_utc TEXT NOT NULL,
                    acknowledged INTEGER NOT NULL DEFAULT 0 CHECK(acknowledged IN (0,1)),
                    acknowledged_at_utc TEXT, acknowledgement_note TEXT
                );
                CREATE TABLE IF NOT EXISTS processing_issues (
                    issue_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    issue_key TEXT NOT NULL UNIQUE,
                    device_id TEXT NOT NULL, boot_id TEXT NOT NULL,
                    issue_type TEXT NOT NULL, details TEXT NOT NULL,
                    detected_at_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS worker_lease (
                    lease_name TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                    status TEXT NOT NULL, heartbeat_at_utc TEXT NOT NULL,
                    expires_at_utc TEXT NOT NULL, last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_events_device_time
                  ON health_events(device_id,detected_at_utc DESC,event_id DESC);
                CREATE INDEX IF NOT EXISTS idx_events_unacknowledged
                  ON health_events(device_id,acknowledged,severity);
                CREATE INDEX IF NOT EXISTS idx_checkpoints_updated
                  ON processing_checkpoints(updated_at_utc DESC);
                """
            )
            # Task 01 databases created before source provenance was added have
            # the same table name, so CREATE TABLE IF NOT EXISTS cannot upgrade
            # them. Existing Task 01 recordings were simulator-only; preserve
            # those rows and mark their provenance explicitly.
            session_columns = {
                row["name"] for row in connection.execute(
                    "PRAGMA table_info(device_sessions)"
                ).fetchall()
            }
            if "source_type" not in session_columns:
                connection.execute(
                    "ALTER TABLE device_sessions ADD COLUMN source_type TEXT "
                    "NOT NULL DEFAULT 'SIMULATED'"
                )

    def available(self) -> bool:
        try:
            with self.connect() as connection:
                return connection.execute("SELECT 1").fetchone()[0] == 1
        except sqlite3.Error:
            return False

    def insert_batch(self, batch: TelemetryBatch) -> tuple[bool, str]:
        payload_hash = batch.payload_hash()
        received_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload_hash,received_at_utc FROM ingested_batches "
                "WHERE device_id=? AND boot_id=? AND batch_seq=?",
                (batch.device_id, batch.boot_id, batch.batch_seq),
            ).fetchone()
            if existing:
                if existing["payload_hash"] != payload_hash:
                    raise BatchConflictError("batch identity already exists with different payload")
                return True, existing["received_at_utc"]
            session = connection.execute(
                "SELECT source_type FROM device_sessions WHERE device_id=? AND boot_id=?",
                (batch.device_id, batch.boot_id),
            ).fetchone()
            if session and session["source_type"] != batch.source_type.value:
                raise BatchConflictError("session source_type cannot change")
            connection.execute(
                "INSERT INTO device_sessions(device_id,boot_id,source_type,"
                "first_received_at_utc,last_received_at_utc) VALUES(?,?,?,?,?) "
                "ON CONFLICT(device_id,boot_id) DO UPDATE SET "
                "last_received_at_utc=excluded.last_received_at_utc",
                (batch.device_id, batch.boot_id, batch.source_type.value,
                 received_at, received_at),
            )
            q = batch.quality
            connection.execute(
                "INSERT INTO ingested_batches VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (batch.device_id, batch.boot_id, batch.batch_seq, payload_hash,
                 received_at, batch.requested_mpu_hz, batch.accel_range_g,
                 batch.gyro_range_dps, batch.dlpf_config, q.i2c_read_errors,
                 q.missed_deadlines, q.buffer_overruns, q.dht_failures),
            )
            # Early Task 01 motion tables still reference the legacy parent
            # name. Mirror batch metadata solely to satisfy that preserved FK;
            # current reads and replay checks use ingested_batches.
            motion_parents = {
                row["table"] for row in connection.execute(
                    "PRAGMA foreign_key_list(motion_samples)"
                ).fetchall()
            }
            if "telemetry_batches" in motion_parents:
                connection.execute(
                    "INSERT INTO telemetry_batches(device_id,boot_id,batch_seq,"
                    "received_at_utc,requested_mpu_hz,accel_range_g,gyro_range_dps,"
                    "dlpf_config,i2c_read_errors,missed_deadlines,buffer_overruns,"
                    "dht_failures) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (batch.device_id, batch.boot_id, batch.batch_seq, received_at,
                     batch.requested_mpu_hz, batch.accel_range_g,
                     batch.gyro_range_dps, batch.dlpf_config, q.i2c_read_errors,
                     q.missed_deadlines, q.buffer_overruns, q.dht_failures),
                )
            connection.executemany(
                "INSERT INTO motion_samples VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                [(batch.device_id, batch.boot_id, batch.batch_seq, s.sample_seq,
                  s.t_mono_us, s.ax_g, s.ay_g, s.az_g, s.gx_dps, s.gy_dps,
                  s.gz_dps, received_at) for s in batch.motion_samples],
            )
            connection.executemany(
                "INSERT INTO environment_samples VALUES(?,?,?,?,?,?,?,?,?,?)",
                [(batch.device_id, batch.boot_id, batch.batch_seq, s.env_seq,
                  s.t_mono_us, s.ambient_temperature_c, s.ambient_humidity_pct,
                  int(s.valid), s.error_code, received_at)
                 for s in batch.environment_samples],
            )
        return False, received_at

    def sessions(self, device_id: str | None = None) -> list[dict[str, Any]]:
        where = "WHERE s.device_id=?" if device_id else ""
        params = (device_id,) if device_id else ()
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT s.*,
                (SELECT COUNT(*) FROM motion_samples m WHERE m.device_id=s.device_id
                  AND m.boot_id=s.boot_id) AS motion_sample_count,
                (SELECT COUNT(*) FROM environment_samples e WHERE e.device_id=s.device_id
                  AND e.boot_id=s.boot_id) AS environment_sample_count,
                (SELECT COUNT(*) FROM ingested_batches b WHERE b.device_id=s.device_id
                  AND b.boot_id=s.boot_id) AS batch_count
                FROM device_sessions s {where}
                ORDER BY s.last_received_at_utc DESC, s.device_id, s.boot_id""",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def require_session(self, device_id: str, boot_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM device_sessions WHERE device_id=? AND boot_id=?",
                (device_id, boot_id),
            ).fetchone()
        if not row:
            raise SessionNotFoundError(f"session not found: {device_id}/{boot_id}")
        return dict(row)

    def latest(self, device_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
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
                "ORDER BY sample_seq DESC LIMIT 1", key,
            ).fetchone()
            environment = connection.execute(
                "SELECT env_seq,t_mono_us,ambient_temperature_c,ambient_humidity_pct,"
                "valid,error_code,received_at_utc FROM environment_samples "
                "WHERE device_id=? AND boot_id=? ORDER BY env_seq DESC LIMIT 1", key,
            ).fetchone()
        return {"device_id": device_id, "boot_id": session["boot_id"],
                "source_type": session["source_type"],
                "latest_motion": dict(motion) if motion else None,
                "latest_environment": dict(environment) if environment else None}

    def telemetry(self, device_id: str, boot_id: str, sensor: str,
                  limit: int, offset: int) -> list[dict[str, Any]]:
        self.require_session(device_id, boot_id)
        if sensor == "motion":
            table, order = "motion_samples", "sample_seq"
        elif sensor == "environment":
            table, order = "environment_samples", "env_seq"
        else:
            raise ValueError("sensor must be motion or environment")
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM {table} WHERE device_id=? AND boot_id=? "
                f"ORDER BY {order} LIMIT ? OFFSET ?",
                (device_id, boot_id, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def batch_metadata(self, device_id: str, boot_id: str) -> list[dict[str, Any]]:
        self.require_session(device_id, boot_id)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT batch_seq,received_at_utc,requested_mpu_hz,accel_range_g,"
                "gyro_range_dps,dlpf_config,i2c_read_errors,missed_deadlines,"
                "buffer_overruns,dht_failures FROM ingested_batches "
                "WHERE device_id=? AND boot_id=? ORDER BY batch_seq",
                (device_id, boot_id),
            ).fetchall()
        return [dict(row) for row in rows]
