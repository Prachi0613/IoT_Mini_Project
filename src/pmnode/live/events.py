"""Persistent, deduplicated health and processing events."""

from __future__ import annotations

from datetime import datetime, timezone

from pmnode.storage import Storage

EVENT_MAPPING = {
    "WARNING": ("HEALTH_WARNING", "WARNING"),
    "DEGRADING": ("DEGRADATION_DETECTED", "HIGH"),
    "MAINTENANCE": ("MAINTENANCE_RECOMMENDED", "CRITICAL"),
    "SENSOR_ERROR": ("SENSOR_DATA_ERROR", "HIGH"),
    "NORMAL": ("HEALTH_RECOVERED", "INFO"),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class EventStore:
    def __init__(self, storage: Storage):
        self.storage = storage

    def create_transition(self, *, device_id: str, boot_id: str, source_type: str,
                          previous_state: str, health_state: str,
                          window_start_us: int, window_end_us: int,
                          model_version: str | None,
                          baseline_version: int | None) -> bool:
        if health_state == previous_state or health_state not in EVENT_MAPPING:
            return False
        if health_state == "NORMAL" and previous_state == "NORMAL":
            return False
        event_type, severity = EVENT_MAPPING[health_state]
        key = f"{device_id}|{boot_id}|{event_type}|{window_start_us}|{model_version or 'none'}"
        return self._insert(key, device_id, boot_id, source_type, event_type,
                            health_state, severity, window_start_us, window_end_us,
                            model_version, baseline_version)

    def create_processing_event(self, *, device_id: str, boot_id: str,
                                source_type: str, event_type: str,
                                window_start_us: int | None = None,
                                window_end_us: int | None = None,
                                model_version: str | None = None) -> bool:
        severity = "HIGH" if event_type == "MODEL_INCOMPATIBLE" else "WARNING"
        key = f"{device_id}|{boot_id}|{event_type}|{model_version or 'none'}"
        return self._insert(key, device_id, boot_id, source_type, event_type,
                            "UNKNOWN_CONDITION", severity, window_start_us,
                            window_end_us, model_version, None)

    def _insert(self, key, device_id, boot_id, source_type, event_type,
                health_state, severity, start, end, model, baseline) -> bool:
        with self.storage.connect() as connection:
            before = connection.total_changes
            connection.execute(
                "INSERT OR IGNORE INTO health_events(event_key,device_id,boot_id,"
                "source_type,event_type,health_state,severity,window_start_us,"
                "window_end_us,model_version,baseline_version,detected_at_utc) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, device_id, boot_id, source_type, event_type, health_state,
                 severity, start, end, model, baseline, utc_now()),
            )
            return connection.total_changes > before

    def acknowledge(self, event_id: int, note: str | None) -> dict | None:
        with self.storage.connect() as connection:
            row = connection.execute(
                "SELECT * FROM health_events WHERE event_id=?", (event_id,)
            ).fetchone()
            if not row:
                return None
            if not row["acknowledged"]:
                connection.execute(
                    "UPDATE health_events SET acknowledged=1,acknowledged_at_utc=?,"
                    "acknowledgement_note=? WHERE event_id=?",
                    (utc_now(), note, event_id),
                )
            return dict(connection.execute(
                "SELECT * FROM health_events WHERE event_id=?", (event_id,)
            ).fetchone())
