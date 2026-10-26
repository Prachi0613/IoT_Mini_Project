"""Timestamp-aware feature extraction for rigidly mounted MPU6050 data."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Self

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy.signal import welch

from pmnode.contracts import SourceType

FEATURE_SCHEMA_VERSION = "1.0.0"
MODEL_FEATURES = [
    "vibration_rms_g", "vibration_std_g", "vibration_peak_g",
    "vibration_peak_to_peak_g", "vibration_crest_factor",
    "vibration_variance_g2", "vibration_energy_g2", "dominant_frequency_hz",
    "vibration_low_band_energy_g2", "vibration_high_band_energy_g2",
    "gyro_rms_dps", "gyro_std_dps", "gyro_peak_dps",
]


class WindowQuality(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"


class FeatureWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    feature_schema_version: str = FEATURE_SCHEMA_VERSION
    device_id: str
    boot_id: str
    source_type: SourceType = Field(strict=False)
    window_start_us: int = Field(ge=0)
    window_end_us: int = Field(gt=0)
    sample_count: int = Field(ge=0)
    observed_sampling_hz: float | None = None
    median_interval_us: float | None = None
    timestamp_jitter_us: float | None = None
    max_gap_us: float | None = None
    vibration_rms_g: float | None = None
    vibration_std_g: float | None = None
    vibration_peak_g: float | None = None
    vibration_peak_to_peak_g: float | None = None
    vibration_crest_factor: float | None = None
    vibration_variance_g2: float | None = None
    vibration_energy_g2: float | None = None
    dominant_frequency_hz: float | None = None
    vibration_low_band_energy_g2: float | None = None
    vibration_high_band_energy_g2: float | None = None
    gyro_rms_dps: float | None = None
    gyro_std_dps: float | None = None
    gyro_peak_dps: float | None = None
    ambient_temperature_c: float | None = None
    ambient_humidity_pct: float | None = None
    environment_age_us: int | None = Field(default=None, ge=0)
    window_quality: WindowQuality
    quality_flags: list[str]

    @model_validator(mode="after")
    def validate_valid_window(self) -> Self:
        if self.window_end_us <= self.window_start_us:
            raise ValueError("window_end_us must be after window_start_us")
        if self.window_quality == WindowQuality.VALID:
            missing = [name for name in MODEL_FEATURES if getattr(self, name) is None]
            if missing:
                raise ValueError(f"valid window lacks model features: {missing}")
        return self

    def model_vector(self) -> list[float]:
        if self.window_quality != WindowQuality.VALID:
            raise ValueError("invalid feature windows cannot be passed to a model")
        values = [getattr(self, name) for name in MODEL_FEATURES]
        if any(value is None or not math.isfinite(value) for value in values):
            raise ValueError("model feature vector contains unavailable/non-finite values")
        return [float(value) for value in values]


@dataclass(frozen=True)
class FeatureConfig:
    window_duration_s: float = 1.0
    overlap_fraction: float = 0.0
    expected_sampling_hz: float = 500.0
    minimum_sample_fraction: float = 0.80
    minimum_spectral_samples: int = 64
    maximum_gap_factor: float = 3.0
    maximum_jitter_ratio: float = 0.20
    low_band_hz: tuple[float, float] = (5.0, 30.0)
    high_band_hz: tuple[float, float] = (30.0, 100.0)
    environment_stale_s: float = 4.0

    def __post_init__(self) -> None:
        if self.window_duration_s <= 0 or not 0 <= self.overlap_fraction < 1:
            raise ValueError("window duration must be positive and overlap in [0,1)")
        if not 0 < self.minimum_sample_fraction <= 1:
            raise ValueError("minimum sample fraction must be in (0,1]")


def _environment_context(environment: list[dict], end_us: int, stale_us: int):
    prior = [row for row in environment if row["t_mono_us"] <= end_us]
    if not prior:
        return None, None, None, ["ENVIRONMENT_MISSING"]
    latest = prior[-1]
    age = end_us - latest["t_mono_us"]
    flags: list[str] = []
    if not latest["valid"]:
        flags.append("ENVIRONMENT_INVALID")
        return None, None, age, flags
    if age > stale_us:
        flags.append("ENVIRONMENT_STALE")
    return latest["ambient_temperature_c"], latest["ambient_humidity_pct"], age, flags


def extract_feature_windows(
    motion: list[dict], environment: list[dict], *, device_id: str,
    boot_id: str, source_type: SourceType | str,
    config: FeatureConfig = FeatureConfig(),
) -> list[FeatureWindow]:
    if not motion:
        return []
    motion = sorted(motion, key=lambda row: row["t_mono_us"])
    environment = sorted(environment, key=lambda row: row["t_mono_us"])
    duration_us = round(config.window_duration_s * 1_000_000)
    step_us = round(duration_us * (1 - config.overlap_fraction))
    first_start = (motion[0]["t_mono_us"] // step_us) * step_us
    last_timestamp = motion[-1]["t_mono_us"]
    windows: list[FeatureWindow] = []
    start = first_start
    while start <= last_timestamp:
        end = start + duration_us
        rows = [row for row in motion if start <= row["t_mono_us"] < end]
        windows.append(_extract_one(rows, environment, device_id, boot_id,
                                    source_type, start, end, config))
        start += step_us
    return windows


def extract_single_window(
    motion: list[dict], environment: list[dict], *, device_id: str,
    boot_id: str, source_type: SourceType | str, window_start_us: int,
    window_end_us: int, config: FeatureConfig = FeatureConfig(),
) -> FeatureWindow:
    """Extract one explicitly aligned live window without interpolation."""
    rows = sorted(
        [row for row in motion if window_start_us <= row["t_mono_us"] < window_end_us],
        key=lambda row: row["t_mono_us"],
    )
    return _extract_one(rows, sorted(environment, key=lambda row: row["t_mono_us"]),
                        device_id, boot_id, source_type, window_start_us,
                        window_end_us, config)


def _extract_one(rows, environment, device_id, boot_id, source_type, start, end, config):
    flags: list[str] = []
    count = len(rows)
    min_count = max(config.minimum_spectral_samples,
                    math.ceil(config.expected_sampling_hz * config.window_duration_s
                              * config.minimum_sample_fraction))
    timestamps = np.asarray([row["t_mono_us"] for row in rows], dtype=float)
    observed_hz = median_interval = jitter = max_gap = None
    if count < min_count:
        flags.append("INSUFFICIENT_SAMPLES")
    if count >= 2:
        intervals = np.diff(timestamps)
        median_interval = float(np.median(intervals))
        if median_interval <= 0:
            flags.append("NON_INCREASING_TIMESTAMPS")
        else:
            observed_hz = 1_000_000.0 / median_interval
            jitter = float(np.std(intervals))
            max_gap = float(np.max(intervals))
            if max_gap > config.maximum_gap_factor * median_interval:
                flags.append("EXCESSIVE_GAP")
            if jitter > config.maximum_jitter_ratio * median_interval:
                flags.append("EXCESSIVE_JITTER")
    else:
        flags.append("SAMPLING_RATE_UNAVAILABLE")

    temp, humidity, env_age, env_flags = _environment_context(
        environment, end, round(config.environment_stale_s * 1_000_000)
    )
    flags.extend(env_flags)
    features: dict[str, float | None] = {name: None for name in MODEL_FEATURES}
    sampling_invalid = any(flag in flags for flag in (
        "INSUFFICIENT_SAMPLES", "NON_INCREASING_TIMESTAMPS",
        "EXCESSIVE_GAP", "EXCESSIVE_JITTER", "SAMPLING_RATE_UNAVAILABLE"))
    if not sampling_invalid and observed_hz is not None:
        nyquist = observed_hz / 2
        if nyquist < config.low_band_hz[1] or nyquist < config.high_band_hz[1]:
            flags.append("SPECTRAL_BAND_UNSUPPORTED")
        else:
            acceleration = np.asarray(
                [[r["ax_g"], r["ay_g"], r["az_g"]] for r in rows], dtype=float)
            gyro = np.asarray(
                [[r["gx_dps"], r["gy_dps"], r["gz_dps"]] for r in rows], dtype=float)
            dynamic_acceleration = acceleration - np.mean(acceleration, axis=0)
            dynamic_gyro = gyro - np.mean(gyro, axis=0)
            acc_magnitude = np.linalg.norm(dynamic_acceleration, axis=1)
            gyro_magnitude = np.linalg.norm(dynamic_gyro, axis=1)
            rms = float(np.sqrt(np.mean(acc_magnitude ** 2)))
            peak = float(np.max(acc_magnitude))
            frequencies, psd_axes = welch(
                dynamic_acceleration, fs=observed_hz, axis=0,
                nperseg=min(256, count), detrend="constant", scaling="density",
            )
            psd = np.sum(psd_axes, axis=1)
            positive = frequencies > 0
            dominant = float(frequencies[positive][np.argmax(psd[positive])])

            def band_energy(bounds):
                mask = (frequencies >= bounds[0]) & (frequencies < bounds[1])
                return float(np.trapezoid(psd[mask], frequencies[mask])) if np.count_nonzero(mask) >= 2 else 0.0

            features.update(
                vibration_rms_g=rms,
                vibration_std_g=float(np.std(acc_magnitude)),
                vibration_peak_g=peak,
                vibration_peak_to_peak_g=float(np.ptp(acc_magnitude)),
                vibration_crest_factor=peak / rms if rms > 0 else 0.0,
                vibration_variance_g2=float(np.var(acc_magnitude)),
                vibration_energy_g2=float(np.mean(acc_magnitude ** 2)),
                dominant_frequency_hz=dominant,
                vibration_low_band_energy_g2=band_energy(config.low_band_hz),
                vibration_high_band_energy_g2=band_energy(config.high_band_hz),
                gyro_rms_dps=float(np.sqrt(np.mean(gyro_magnitude ** 2))),
                gyro_std_dps=float(np.std(gyro_magnitude)),
                gyro_peak_dps=float(np.max(gyro_magnitude)),
            )
    invalid_flags = [flag for flag in flags if not flag.startswith("ENVIRONMENT_")]
    return FeatureWindow(
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        device_id=device_id, boot_id=boot_id, source_type=source_type,
        window_start_us=start, window_end_us=end, sample_count=count,
        observed_sampling_hz=observed_hz, median_interval_us=median_interval,
        timestamp_jitter_us=jitter, max_gap_us=max_gap,
        ambient_temperature_c=temp, ambient_humidity_pct=humidity,
        environment_age_us=env_age,
        window_quality=WindowQuality.INVALID if invalid_flags else WindowQuality.VALID,
        quality_flags=sorted(set(flags)), **features,
    )
