"""Deterministic software-only MPU6050/DHT11 telemetry simulator."""

from __future__ import annotations

import math
import random
import uuid
from dataclasses import dataclass
from enum import StrEnum

from .contracts import (
    AcquisitionQuality,
    EnvironmentSample,
    MotionSample,
    SourceType,
    TelemetryBatch,
)


class Scenario(StrEnum):
    HEALTHY = "healthy"
    VIBRATION_ANOMALY = "vibration_anomaly"
    TRANSIENT_DISTURBANCE = "transient_disturbance"
    SENSOR_DROPOUT = "sensor_dropout"


@dataclass(frozen=True)
class SimulationConfig:
    scenario: Scenario = Scenario.HEALTHY
    duration_s: float = 10.0
    sample_hz: int = 500
    seed: int = 42
    device_id: str = "sim-node-01"
    batch_duration_s: float = 1.0
    dht_interval_s: float = 2.0
    accel_range_g: float = 4.0
    gyro_range_dps: float = 250.0
    dlpf_config: int = 1

    def __post_init__(self) -> None:
        if self.duration_s <= 0 or self.batch_duration_s <= 0:
            raise ValueError("duration and batch duration must be positive")
        if self.sample_hz <= 0 or self.sample_hz > 4000:
            raise ValueError("sample_hz must be between 1 and 4000")
        if self.dht_interval_s < 2.0:
            raise ValueError("DHT11 interval must be at least 2 seconds")
        if self.sample_hz * self.batch_duration_s > 1000:
            raise ValueError("batch exceeds the configured 1000-motion-sample limit")


@dataclass(frozen=True)
class SimulationRun:
    boot_id: str
    batches: list[TelemetryBatch]
    labels: list[dict[str, object]]


class SensorSimulator:
    """Generate plausible synthetic signals; labels are test ground truth only."""

    def __init__(self, config: SimulationConfig, boot_id: str | None = None):
        self.config = config
        self.boot_id = boot_id or f"sim-{uuid.uuid4().hex}"

    def generate(self) -> SimulationRun:
        rng = random.Random(self.config.seed)
        period_us = round(1_000_000 / self.config.sample_hz)
        total_slots = int(self.config.duration_s * self.config.sample_hz)
        batch_us = round(self.config.batch_duration_s * 1_000_000)
        batch_count = max(1, math.ceil(self.config.duration_s / self.config.batch_duration_s))
        motion_by_batch: list[list[MotionSample]] = [[] for _ in range(batch_count)]
        environment_by_batch: list[list[EnvironmentSample]] = [[] for _ in range(batch_count)]
        labels: list[dict[str, object]] = []
        missed_by_batch = [0] * batch_count
        dht_failures_by_batch = [0] * batch_count

        anomaly_start = self.config.duration_s * 0.4
        transient_start = self.config.duration_s * 0.45
        transient_end = self.config.duration_s * 0.55
        dropout_start = self.config.duration_s * 0.35
        dropout_end = self.config.duration_s * 0.50
        if self.config.scenario == Scenario.VIBRATION_ANOMALY:
            labels.append({"label": "synthetic_vibration_anomaly",
                           "start_mono_us": round(anomaly_start * 1_000_000)})
        elif self.config.scenario == Scenario.TRANSIENT_DISTURBANCE:
            labels.append({"label": "synthetic_transient",
                           "start_mono_us": round(transient_start * 1_000_000),
                           "end_mono_us": round(transient_end * 1_000_000)})
        elif self.config.scenario == Scenario.SENSOR_DROPOUT:
            labels.append({"label": "synthetic_motion_dropout",
                           "start_mono_us": round(dropout_start * 1_000_000),
                           "end_mono_us": round(dropout_end * 1_000_000)})

        sequence = 0
        for slot in range(total_slots):
            t_s = slot / self.config.sample_hz
            t_us = slot * period_us
            batch_index = min(t_us // batch_us, batch_count - 1)
            dropped = (
                self.config.scenario == Scenario.SENSOR_DROPOUT
                and dropout_start <= t_s < dropout_end
                and slot % 3 != 0
            )
            if dropped:
                missed_by_batch[batch_index] += 1
                continue
            base = 0.018 * math.sin(2 * math.pi * 23 * t_s)
            amplitude = 1.0
            if self.config.scenario == Scenario.VIBRATION_ANOMALY and t_s >= anomaly_start:
                amplitude += 4.0 * (t_s - anomaly_start) / max(
                    self.config.duration_s - anomaly_start, 1e-9
                )
            disturbance = 0.0
            if self.config.scenario == Scenario.TRANSIENT_DISTURBANCE and transient_start <= t_s <= transient_end:
                center = (transient_start + transient_end) / 2
                half_width = (transient_end - transient_start) / 2
                disturbance = 0.35 * (1 - abs(t_s - center) / half_width)
            noise = lambda scale: rng.uniform(-scale, scale)
            ax = amplitude * base + disturbance + noise(0.004)
            ay = amplitude * 0.014 * math.sin(2 * math.pi * 17 * t_s + 0.7) + noise(0.004)
            az = 1.0 + amplitude * 0.010 * math.sin(2 * math.pi * 29 * t_s) + noise(0.004)
            gx = 3.0 * math.sin(2 * math.pi * 2.0 * t_s) + noise(0.15)
            gy = 2.0 * math.sin(2 * math.pi * 1.3 * t_s + 0.4) + noise(0.15)
            gz = 1.5 * math.sin(2 * math.pi * 0.8 * t_s + 1.1) + noise(0.15)
            values = (ax, ay, az, gx, gy, gz)
            if max(map(abs, values[:3])) >= self.config.accel_range_g:
                raise ValueError("simulated acceleration contradicts configured range")
            if max(map(abs, values[3:])) >= self.config.gyro_range_dps:
                raise ValueError("simulated gyroscope contradicts configured range")
            motion_by_batch[batch_index].append(MotionSample(
                sample_seq=sequence, t_mono_us=t_us, ax_g=ax, ay_g=ay, az_g=az,
                gx_dps=gx, gy_dps=gy, gz_dps=gz,
            ))
            sequence += 1

        env_count = math.ceil(self.config.duration_s / self.config.dht_interval_s)
        for env_seq in range(env_count):
            t_s = env_seq * self.config.dht_interval_s
            t_us = round(t_s * 1_000_000)
            batch_index = min(t_us // batch_us, batch_count - 1)
            invalid = self.config.scenario == Scenario.SENSOR_DROPOUT and env_seq % 3 == 1
            if invalid:
                environment_by_batch[batch_index].append(EnvironmentSample(
                    env_seq=env_seq, t_mono_us=t_us,
                    ambient_temperature_c=None, ambient_humidity_pct=None,
                    valid=False, error_code="SIMULATED_DHT_DROPOUT",
                ))
                dht_failures_by_batch[batch_index] += 1
            else:
                environment_by_batch[batch_index].append(EnvironmentSample(
                    env_seq=env_seq, t_mono_us=t_us,
                    ambient_temperature_c=24.0 + 0.2 * math.sin(2 * math.pi * t_s / 60) + rng.uniform(-0.03, 0.03),
                    ambient_humidity_pct=50.0 + 0.5 * math.sin(2 * math.pi * t_s / 90) + rng.uniform(-0.08, 0.08),
                    valid=True, error_code=None,
                ))

        batches: list[TelemetryBatch] = []
        cumulative_missed = 0
        cumulative_dht_failures = 0
        for batch_seq, (motion, environment) in enumerate(
            zip(motion_by_batch, environment_by_batch)
        ):
            cumulative_missed += missed_by_batch[batch_seq]
            cumulative_dht_failures += dht_failures_by_batch[batch_seq]
            if not motion and not environment:
                continue
            batches.append(TelemetryBatch(
                schema_version="1.0.0", device_id=self.config.device_id,
                boot_id=self.boot_id, batch_seq=batch_seq,
                source_type=SourceType.SIMULATED,
                requested_mpu_hz=self.config.sample_hz,
                accel_range_g=self.config.accel_range_g,
                gyro_range_dps=self.config.gyro_range_dps,
                dlpf_config=self.config.dlpf_config,
                motion_samples=motion, environment_samples=environment,
                quality=AcquisitionQuality(
                    i2c_read_errors=0, missed_deadlines=cumulative_missed,
                    buffer_overruns=0, dht_failures=cumulative_dht_failures,
                ),
            ))
        return SimulationRun(self.boot_id, batches, labels)
