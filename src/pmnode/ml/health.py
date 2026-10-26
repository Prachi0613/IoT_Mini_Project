"""Session-isolated temporal health classification with hysteresis."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import StrEnum
import json

import numpy as np


class HealthState(StrEnum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    DEGRADING = "DEGRADING"
    MAINTENANCE = "MAINTENANCE"
    SENSOR_ERROR = "SENSOR_ERROR"
    UNKNOWN_CONDITION = "UNKNOWN_CONDITION"


@dataclass(frozen=True)
class HealthConfig:
    warning_count: int = 3
    warning_window: int = 5
    degrading_count: int = 6
    degrading_window: int = 8
    maintenance_count: int = 9
    maintenance_window: int = 10
    severe_multiplier: float = 1.5
    recovery_windows: int = 3


class HealthEngine:
    def __init__(self, threshold: float, config: HealthConfig = HealthConfig()):
        self.threshold = threshold
        self.config = config
        self._session: tuple[str, str] | None = None
        self._scores: deque[float] = deque(maxlen=config.maintenance_window)
        self._anomalies: deque[bool] = deque(maxlen=config.maintenance_window)
        self._normal_streak = 0
        self.state = HealthState.NORMAL
        self.transitions: list[dict] = []

    def snapshot(self) -> str:
        return json.dumps({
            "session": list(self._session) if self._session else None,
            "scores": list(self._scores), "anomalies": list(self._anomalies),
            "normal_streak": self._normal_streak, "state": self.state.value,
        }, sort_keys=True)

    def restore(self, payload: str) -> None:
        value = json.loads(payload)
        self._session = tuple(value["session"]) if value["session"] else None
        self._scores = deque(value["scores"], maxlen=self.config.maintenance_window)
        self._anomalies = deque(value["anomalies"], maxlen=self.config.maintenance_window)
        self._normal_streak = value["normal_streak"]
        self.state = HealthState(value["state"])

    def update(self, device_id: str, boot_id: str, window_start_us: int,
               score: float | None, valid: bool = True) -> HealthState:
        session = (device_id, boot_id)
        if session != self._session:
            self._session = session
            self._scores.clear(); self._anomalies.clear(); self._normal_streak = 0
            self.state = HealthState.NORMAL
        previous = self.state
        if not valid or score is None:
            self.state = HealthState.SENSOR_ERROR
            self._normal_streak = 0
        else:
            anomalous = score > self.threshold
            self._scores.append(score); self._anomalies.append(anomalous)
            self._normal_streak = 0 if anomalous else self._normal_streak + 1
            recent_warning = list(self._anomalies)[-self.config.warning_window:]
            recent_degrading = list(self._anomalies)[-self.config.degrading_window:]
            recent_maintenance = list(self._anomalies)[-self.config.maintenance_window:]
            severe = [value > self.threshold * self.config.severe_multiplier
                      for value in list(self._scores)[-self.config.maintenance_window:]]
            trend = (len(recent_degrading) >= self.config.degrading_window and
                     np.polyfit(range(len(recent_degrading)),
                                list(self._scores)[-len(recent_degrading):], 1)[0] > 0)
            if (len(recent_maintenance) >= self.config.maintenance_window and
                    sum(recent_maintenance) >= self.config.maintenance_count and
                    sum(severe) >= self.config.maintenance_count):
                self.state = HealthState.MAINTENANCE
            elif (len(recent_degrading) >= self.config.degrading_window and
                  sum(recent_degrading) >= self.config.degrading_count and trend):
                self.state = HealthState.DEGRADING
            elif (len(recent_warning) >= self.config.warning_window and
                  sum(recent_warning) >= self.config.warning_count):
                self.state = HealthState.WARNING
            elif self.state != HealthState.NORMAL and self._normal_streak < self.config.recovery_windows:
                pass
            else:
                self.state = HealthState.NORMAL
        if self.state != previous:
            self.transitions.append({"window_start_us": window_start_us,
                                     "from": previous.value, "to": self.state.value})
        return self.state
