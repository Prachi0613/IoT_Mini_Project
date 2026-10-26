"""Persistence for derived windows, predictions, models, and references."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from pmnode.storage import Storage

from .features import FeatureWindow
from .model import ModelBundle


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class MLRegistry:
    def __init__(self, storage: Storage):
        self.storage = storage

    def save_features(self, windows: list[FeatureWindow], recompute: bool = False) -> int:
        sql = ("INSERT OR REPLACE" if recompute else "INSERT OR IGNORE") + " INTO feature_windows VALUES(?,?,?,?,?,?,?,?,?)"
        rows = [(w.feature_schema_version, w.device_id, w.boot_id,
                 w.source_type.value, w.window_start_us, w.window_end_us,
                 w.window_quality.value, w.model_dump_json(), utc_now()) for w in windows]
        with self.storage.connect() as connection:
            before = connection.total_changes
            connection.executemany(sql, rows)
            return connection.total_changes - before

    def load_features(self, sessions: list[tuple[str, str]], valid_only: bool = True) -> list[FeatureWindow]:
        windows: list[FeatureWindow] = []
        with self.storage.connect() as connection:
            for device_id, boot_id in sessions:
                condition = " AND quality_status='VALID'" if valid_only else ""
                rows = connection.execute(
                    "SELECT payload_json FROM feature_windows WHERE device_id=? AND boot_id=?"
                    + condition + " ORDER BY window_start_us", (device_id, boot_id),
                ).fetchall()
                windows.extend(FeatureWindow.model_validate_json(row[0]) for row in rows)
        return windows

    def register_model(self, bundle: ModelBundle, artifact_path: str | Path,
                       status: str = "CANDIDATE") -> None:
        with self.storage.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO model_versions VALUES(?,?,?,?,?,?,?)",
                (bundle.model_version, bundle.source_type, bundle.feature_schema_version,
                 str(Path(artifact_path).resolve()), status,
                 json.dumps(bundle.metadata, sort_keys=True), utc_now()),
            )

    def list_models(self) -> list[dict]:
        with self.storage.connect() as connection:
            rows = connection.execute(
                "SELECT model_version,source_type,feature_schema_version,artifact_path,"
                "status,metadata_json,created_at_utc FROM model_versions "
                "ORDER BY created_at_utc,model_version"
            ).fetchall()
        return [{**dict(row), "metadata": json.loads(row["metadata_json"])}
                for row in rows]

    def activate_model(self, model_version: str) -> str:
        with self.storage.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT source_type FROM model_versions WHERE model_version=?",
                (model_version,),
            ).fetchone()
            if not row:
                raise ValueError("model version does not exist")
            status = ("ACTIVE_SYNTHETIC_DEVELOPMENT" if row["source_type"] == "SIMULATED"
                      else "ACTIVE_HARDWARE")
            connection.execute("UPDATE model_versions SET status='CANDIDATE' WHERE status LIKE 'ACTIVE_%'")
            connection.execute("UPDATE model_versions SET status=? WHERE model_version=?",
                               (status, model_version))
        return status

    def save_reference(self, model_version: str, reference_version: int,
                       center: np.ndarray, spread: np.ndarray, threshold: float,
                       *, approved: bool, active: bool, previous_version: int | None,
                       reason: str, eligible_windows: int) -> None:
        with self.storage.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if active:
                connection.execute("UPDATE reference_versions SET active=0 WHERE model_version=?",
                                   (model_version,))
            connection.execute(
                "INSERT INTO reference_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (model_version, reference_version, json.dumps(center.tolist()),
                 json.dumps(spread.tolist()), threshold, int(approved), int(active),
                 previous_version, reason, eligible_windows, utc_now()),
            )

    def active_reference(self, model_version: str) -> dict | None:
        with self.storage.connect() as connection:
            row = connection.execute(
                "SELECT * FROM reference_versions WHERE model_version=? AND active=1",
                (model_version,),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["center"] = np.asarray(json.loads(result.pop("center_json")), dtype=float)
        result["spread"] = np.asarray(json.loads(result.pop("spread_json")), dtype=float)
        return result

    def list_references(self, model_version: str) -> list[dict]:
        with self.storage.connect() as connection:
            rows = connection.execute(
                "SELECT model_version,reference_version,threshold,approved,active,"
                "previous_version,reason,eligible_windows,created_at_utc "
                "FROM reference_versions WHERE model_version=? ORDER BY reference_version",
                (model_version,),
            ).fetchall()
        return [dict(row) for row in rows]

    def activate_reference(self, model_version: str, version: int) -> dict:
        with self.storage.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT approved FROM reference_versions WHERE model_version=? AND reference_version=?",
                (model_version, version),
            ).fetchone()
            if not row or not row["approved"]:
                raise ValueError("rollback/activation target is missing or unapproved")
            connection.execute("UPDATE reference_versions SET active=0 WHERE model_version=?",
                               (model_version,))
            connection.execute(
                "UPDATE reference_versions SET active=1 WHERE model_version=? AND reference_version=?",
                (model_version, version),
            )
        return self.active_reference(model_version)

    def log_adaptation(self, model_version: str, reference_version: int,
                       decision: str, reason: str, eligible_windows: int,
                       window: FeatureWindow | None = None) -> None:
        with self.storage.connect() as connection:
            connection.execute(
                "INSERT INTO adaptation_events(model_version,reference_version,device_id,"
                "boot_id,window_start_us,decision,reason,eligible_windows,created_at_utc) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (model_version, reference_version,
                 window.device_id if window else None, window.boot_id if window else None,
                 window.window_start_us if window else None, decision, reason,
                 eligible_windows, utc_now()),
            )

    def recent_adaptation_events(self, model_version: str, limit: int = 50) -> list[dict]:
        with self.storage.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM adaptation_events WHERE model_version=? "
                "ORDER BY event_id DESC LIMIT ?", (model_version, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_prediction(self, window: FeatureWindow, bundle: ModelBundle,
                        reference_version: int, isolation_score: float,
                        reference_score: float, health_state: str) -> bool:
        with self.storage.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            before = connection.total_changes
            connection.execute(
                "INSERT OR IGNORE INTO model_predictions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (window.device_id, window.boot_id, window.window_start_us,
                 window.window_end_us, bundle.model_version, reference_version,
                 isolation_score, bundle.isolation_threshold, reference_score,
                 bundle.reference.threshold, utc_now()),
            )
            connection.execute(
                "INSERT OR IGNORE INTO health_decisions VALUES(?,?,?,?,?,?,?)",
                (window.device_id, window.boot_id, window.window_start_us,
                 bundle.model_version, reference_version, health_state, utc_now()),
            )
            return connection.total_changes > before

    def save_health_decision(self, window: FeatureWindow, model_version: str,
                             reference_version: int, health_state: str) -> bool:
        with self.storage.connect() as connection:
            before = connection.total_changes
            connection.execute(
                "INSERT OR IGNORE INTO health_decisions VALUES(?,?,?,?,?,?,?)",
                (window.device_id, window.boot_id, window.window_start_us,
                 model_version, reference_version, health_state, utc_now()),
            )
            return connection.total_changes > before
