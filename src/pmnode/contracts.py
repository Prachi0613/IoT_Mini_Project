"""Canonical Pydantic v2 telemetry contract shared by every producer."""

from __future__ import annotations

import json
from enum import StrEnum
from hashlib import sha256
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import DEFAULT_SETTINGS

SCHEMA_VERSION = "1.0.0"


class SourceType(StrEnum):
    SIMULATED = "SIMULATED"
    HARDWARE = "HARDWARE"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class MotionSample(StrictModel):
    sample_seq: int = Field(ge=0)
    t_mono_us: int = Field(ge=0)
    ax_g: float
    ay_g: float
    az_g: float
    gx_dps: float
    gy_dps: float
    gz_dps: float


class EnvironmentSample(StrictModel):
    env_seq: int = Field(ge=0)
    t_mono_us: int = Field(ge=0)
    ambient_temperature_c: float | None
    ambient_humidity_pct: float | None = Field(ge=0, le=100)
    valid: bool
    error_code: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.valid:
            if (
                self.ambient_temperature_c is None
                or self.ambient_humidity_pct is None
                or self.error_code is not None
            ):
                raise ValueError("valid environment samples require both values and no error_code")
        elif (
            self.ambient_temperature_c is not None
            or self.ambient_humidity_pct is not None
            or self.error_code is None
        ):
            raise ValueError("invalid environment samples require null values and an error_code")
        return self


class AcquisitionQuality(StrictModel):
    """Acquisition counters cumulative since this boot/session began."""

    i2c_read_errors: int = Field(ge=0)
    missed_deadlines: int = Field(ge=0)
    buffer_overruns: int = Field(ge=0)
    dht_failures: int = Field(ge=0)


class TelemetryBatch(StrictModel):
    schema_version: Literal["1.0.0"]
    device_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    boot_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    batch_seq: int = Field(ge=0)
    # JSON transports encode enums as strings; all other fields remain strict.
    source_type: SourceType = Field(strict=False)
    requested_mpu_hz: int = Field(gt=0, le=4000)
    accel_range_g: float = Field(gt=0, le=32)
    gyro_range_dps: float = Field(gt=0, le=4000)
    dlpf_config: int = Field(ge=0, le=6)
    motion_samples: list[MotionSample] = Field(
        max_length=DEFAULT_SETTINGS.max_motion_samples
    )
    environment_samples: list[EnvironmentSample] = Field(
        max_length=DEFAULT_SETTINGS.max_environment_samples
    )
    quality: AcquisitionQuality

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        if not self.motion_samples and not self.environment_samples:
            raise ValueError("a telemetry batch cannot be empty")
        self._strictly_ordered(self.motion_samples, "sample_seq", "motion sequence")
        self._strictly_ordered(self.motion_samples, "t_mono_us", "motion timestamps")
        self._strictly_ordered(self.environment_samples, "env_seq", "environment sequence")
        self._strictly_ordered(
            self.environment_samples, "t_mono_us", "environment timestamps"
        )
        return self

    @staticmethod
    def _strictly_ordered(records: list[object], field: str, label: str) -> None:
        values = [getattr(record, field) for record in records]
        if any(current <= previous for previous, current in zip(values, values[1:])):
            raise ValueError(f"{label} must be strictly increasing within a batch")

    def canonical_payload(self) -> bytes:
        value = self.model_dump(mode="json")
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def payload_hash(self) -> str:
        return sha256(self.canonical_payload()).hexdigest()


class IngestAcknowledgement(StrictModel):
    device_id: str
    boot_id: str
    batch_seq: int
    accepted_motion_samples: int = Field(ge=0)
    accepted_environment_samples: int = Field(ge=0)
    duplicate: bool
