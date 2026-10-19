"""
contracts.py — Pydantic v2 data-contract definitions for the pmnode system.

This module is the single source of truth for all inter-component data
schemas: raw sensor samples, extracted feature windows, and health
decisions.  It has zero runtime dependencies beyond Pydantic.

Hardware context
----------------
- ESP32 DevKit V1 (microcontroller / acquisition unit)
- MPU6050  : three-axis accelerometer providing vibration data in g
- ACS712   : current-sense module (requires external calibration to A)
- DHT11    : ambient temperature and relative humidity sensor
- DC motor with PWM motor-driver

Critical physical constraints embedded in these contracts
---------------------------------------------------------
1. DHT11 measures *ambient* air conditions.  It does NOT measure motor
   housing temperature or winding temperature.  All DHT11-derived fields
   are named ``ambient_*`` accordingly.
2. MPU6050 yields raw acceleration values (g).  No gravity-compensation
   is performed here; these are *measurements*, not bearing-failure labels.
3. ACS712 requires offset and sensitivity calibration.  Fields that carry
   current values represent *calibrated amperes*, not raw ADC counts.
4. No RPM sensor is confirmed in the current hardware revision.
   ``pwm_duty`` is the *commanded* operating input; it is NOT a verified
   RPM measurement.  ``measured_rpm`` is always optional/nullable.
5. Nominal acquisition targets (200 Hz for accel/current, one DHT11
   reading per 2 s) are *design targets*, not verified hardware
   performance figures.
6. DHT11 values are never modelled as sampled at 200 Hz.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Annotated, Optional
from datetime import datetime, timezone

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
# Sentinel / shared constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class OperatingRegime(str, Enum):
    """Commanded operating regime derived from PWM duty band or external label.

    Values
    ------
    LOW        : Motor running at low duty / speed band.
    MEDIUM     : Motor running at medium duty / speed band.
    HIGH       : Motor running at high duty / speed band.
    TRANSITION : Motor is actively changing speed; steady-state features
                 computed during this period are unreliable.
    UNKNOWN    : Regime could not be determined from available signals.
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    TRANSITION = "TRANSITION"
    UNKNOWN = "UNKNOWN"


class WindowQuality(str, Enum):
    """Top-level quality classification of a feature window.

    VALID   : All required features are present and plausible.
    INVALID : One or more required features are absent, stale, or
              logically inconsistent.  Consumers must not use INVALID
              windows for inference without explicit handling.
    """

    VALID = "VALID"
    INVALID = "INVALID"


class QualityFlag(str, Enum):
    """Detailed reasons why a feature window may be INVALID or degraded.

    Multiple flags may be set simultaneously.

    Values
    ------
    ACCEL_MISSING        : Accelerometer data absent for this window.
    CURRENT_MISSING      : Current-sensor data absent for this window.
    ENV_STALE            : Environmental sample is older than the
                           configured staleness threshold.
    TIMESTAMP_GAP        : A discontinuity was detected in the sample
                           timestamps within the window.
    INSUFFICIENT_SAMPLES : Fewer samples than the minimum required for
                           reliable feature extraction were available.
    UNKNOWN_REGIME       : Operating regime could not be determined,
                           making regime-conditioned features unreliable.
    """

    ACCEL_MISSING = "ACCEL_MISSING"
    CURRENT_MISSING = "CURRENT_MISSING"
    ENV_STALE = "ENV_STALE"
    TIMESTAMP_GAP = "TIMESTAMP_GAP"
    INSUFFICIENT_SAMPLES = "INSUFFICIENT_SAMPLES"
    UNKNOWN_REGIME = "UNKNOWN_REGIME"


class HealthState(str, Enum):
    """Machine health state as classified by the anomaly-detection model.

    These are *condition states*, not probability-of-failure labels.

    Values
    ------
    NORMAL            : No anomaly detected; operation within baseline.
    WARNING           : Mild anomaly detected; monitor closely.
    DEGRADING         : Progressive anomaly trend; maintenance advisable.
    MAINTENANCE       : Anomaly exceeds actionable threshold; schedule
                        immediate maintenance.
    SENSOR_ERROR      : Sensor pipeline failure; inference not performed.
    UNKNOWN_CONDITION : Model output is indeterminate or unavailable.
    """

    NORMAL = "NORMAL"
    WARNING = "WARNING"
    DEGRADING = "DEGRADING"
    MAINTENANCE = "MAINTENANCE"
    SENSOR_ERROR = "SENSOR_ERROR"
    UNKNOWN_CONDITION = "UNKNOWN_CONDITION"


# ---------------------------------------------------------------------------
# Shared annotated types and validator helpers
# ---------------------------------------------------------------------------

NonEmptyStr = Annotated[str, Field(min_length=1)]


def _is_finite(value: float) -> bool:
    """Return True only when *value* is a real, finite number.

    Rejects NaN and ±Infinity which cannot represent a physical measurement.
    """
    return math.isfinite(value)


def _assert_finite(value: float, field_name: str) -> float:
    if not _is_finite(value):
        raise ValueError(
            f"{field_name} must be a finite number; got {value!r}. "
            "NaN and ±Infinity are not valid physical measurements."
        )
    return value


# ---------------------------------------------------------------------------
# A. RunMetadata
# ---------------------------------------------------------------------------


class RunMetadata(BaseModel):
    """Metadata describing a single acquisition run.

    A *run* is a contiguous period during which the ESP32 collects sensor
    data from the attached hardware.  This record is written once at the
    start of each run and is referenced by all sample and feature records
    via ``run_id``.

    Nominal frequency fields capture the *intended* acquisition rate, not
    any measured or verified throughput.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = Field(
        default=SCHEMA_VERSION,
        description="Contract schema version.  Must equal '1.0.0'.",
    )
    device_id: NonEmptyStr = Field(
        description="Unique identifier of the ESP32 device."
    )
    run_id: NonEmptyStr = Field(
        description=(
            "Unique identifier for this acquisition run.  "
            "Typically a UUID or a timestamp-based token."
        )
    )
    started_at_utc: datetime = Field(
        description=(
            "UTC timestamp at which data acquisition began.  "
            "Must be timezone-aware (tzinfo=UTC)."
        )
    )
    nominal_accel_hz: float = Field(
        gt=0,
        description=(
            "Design-target sampling rate for the MPU6050 accelerometer "
            "and ACS712 current sensor, in Hz.  "
            "This is an engineering target, not a verified acquisition rate."
        ),
    )
    nominal_current_hz: float = Field(
        gt=0,
        description=(
            "Design-target sampling rate for the ACS712 current sensor, "
            "in Hz.  This is an engineering target, not a verified rate."
        ),
    )
    nominal_environment_interval_s: float = Field(
        gt=0,
        description=(
            "Design-target interval between DHT11 ambient readings, "
            "in seconds.  DHT11 is a slow sensor; values more frequent "
            "than ~0.5 s are not physically meaningful."
        ),
    )

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, v: str) -> str:
        if v != SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be '{SCHEMA_VERSION}'; got {v!r}."
            )
        return v

    @field_validator("started_at_utc")
    @classmethod
    def _require_utc_aware(cls, v: datetime) -> datetime:
        """Reject naive datetimes and non-UTC timezone-aware datetimes.

        Naive datetimes (tzinfo=None) are ambiguous and must never be
        stored in this field.  Non-UTC aware datetimes are also rejected
        to enforce a single canonical representation.
        """
        if v.tzinfo is None:
            raise ValueError(
                "started_at_utc must be timezone-aware (UTC). "
                "Received a naive datetime with no tzinfo."
            )
        if v.utcoffset().total_seconds() != 0:
            raise ValueError(
                "started_at_utc must have UTC offset of zero. "
                f"Received offset: {v.utcoffset()}."
            )
        return v

    @field_validator("nominal_accel_hz", "nominal_current_hz", "nominal_environment_interval_s")
    @classmethod
    def _validate_finite_positives(cls, v: float, info) -> float:
        return _assert_finite(v, info.field_name)


# ---------------------------------------------------------------------------
# B. AccelerationSample
# ---------------------------------------------------------------------------


class AccelerationSample(BaseModel):
    """A single three-axis acceleration reading from the MPU6050.

    Timestamp is microseconds elapsed since the start of the acquisition
    run (i.e., since ``RunMetadata.started_at_utc``), not a wall-clock
    epoch timestamp.

    Acceleration values are in units of g (standard gravity ≈ 9.81 m/s²)
    as reported by the MPU6050 after applying the configured full-scale
    range and sensitivity factor.  They are NOT gravity-compensated;
    static gravity components remain in the signal.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = Field(default=SCHEMA_VERSION)
    run_id: NonEmptyStr = Field(description="Run identifier this sample belongs to.")
    timestamp_us: int = Field(
        ge=0,
        description=(
            "Microseconds elapsed since the start of the acquisition run. "
            "Must be nonnegative.  Monotonically increases within a run."
        ),
    )
    accel_x_g: float = Field(
        description="X-axis acceleration in g (not gravity-compensated)."
    )
    accel_y_g: float = Field(
        description="Y-axis acceleration in g (not gravity-compensated)."
    )
    accel_z_g: float = Field(
        description="Z-axis acceleration in g (not gravity-compensated)."
    )

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, v: str) -> str:
        if v != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be '{SCHEMA_VERSION}'; got {v!r}.")
        return v

    @field_validator("accel_x_g", "accel_y_g", "accel_z_g")
    @classmethod
    def _validate_finite(cls, v: float, info) -> float:
        return _assert_finite(v, info.field_name)


# ---------------------------------------------------------------------------
# C. CurrentSample
# ---------------------------------------------------------------------------


class CurrentSample(BaseModel):
    """A single calibrated motor current reading from the ACS712.

    Timestamp is microseconds elapsed since the start of the acquisition
    run (same epoch as ``AccelerationSample.timestamp_us``).

    ``motor_current_a`` is in calibrated amperes.  Signed values are
    accepted because:
    - ACS712 calibration may produce a small negative offset near zero.
    - Bidirectional measurement modes may yield genuine negative readings.

    This field must NOT carry raw ADC counts; the caller is responsible
    for applying offset and sensitivity calibration before populating this
    contract.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = Field(default=SCHEMA_VERSION)
    run_id: NonEmptyStr = Field(description="Run identifier this sample belongs to.")
    timestamp_us: int = Field(
        ge=0,
        description="Microseconds elapsed since the start of the acquisition run.",
    )
    motor_current_a: float = Field(
        description=(
            "Calibrated motor supply current in amperes.  "
            "Signed; small negative values due to noise or calibration "
            "offset are valid."
        )
    )

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, v: str) -> str:
        if v != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be '{SCHEMA_VERSION}'; got {v!r}.")
        return v

    @field_validator("motor_current_a")
    @classmethod
    def _validate_finite(cls, v: float, info) -> float:
        return _assert_finite(v, info.field_name)


# ---------------------------------------------------------------------------
# D. EnvironmentSample
# ---------------------------------------------------------------------------


class EnvironmentSample(BaseModel):
    """A single ambient environment reading from the DHT11 sensor.

    DHT11 IMPORTANT: This sensor measures *ambient air* temperature and
    humidity at the sensor mounting location.  It does NOT measure:
    - Motor winding temperature
    - Motor housing / case temperature
    - Bearing temperature

    Field names use the ``ambient_`` prefix to make this distinction
    explicit.  Do not rename these fields to ``motor_temperature`` or
    similar.

    DHT11 typical characteristics:
    - Temperature accuracy: ±2 °C
    - Humidity accuracy: ±5 % RH
    - Minimum sampling interval: ~1 s (recommended 2 s)
    - NOT suitable for high-frequency sampling; do not model at 200 Hz.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = Field(default=SCHEMA_VERSION)
    run_id: NonEmptyStr = Field(description="Run identifier this sample belongs to.")
    timestamp_us: int = Field(
        ge=0,
        description="Microseconds elapsed since the start of the acquisition run.",
    )
    ambient_temperature_c: float = Field(
        description=(
            "Ambient air temperature in degrees Celsius as measured by the "
            "DHT11.  This is NOT motor winding or housing temperature."
        )
    )
    ambient_humidity_pct: float = Field(
        ge=0.0,
        le=100.0,
        description=(
            "Ambient relative humidity in percent (0–100 inclusive) as "
            "measured by the DHT11."
        ),
    )

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, v: str) -> str:
        if v != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be '{SCHEMA_VERSION}'; got {v!r}.")
        return v

    @field_validator("ambient_temperature_c")
    @classmethod
    def _validate_finite_temp(cls, v: float) -> float:
        return _assert_finite(v, "ambient_temperature_c")

    @field_validator("ambient_humidity_pct")
    @classmethod
    def _validate_finite_humidity(cls, v: float) -> float:
        return _assert_finite(v, "ambient_humidity_pct")


# ---------------------------------------------------------------------------
# E. OperatingContext
# ---------------------------------------------------------------------------


class OperatingContext(BaseModel):
    """Operating state of the motor drive at a point in time.

    PWM duty is the *commanded* input to the motor driver, not a verified
    speed or RPM measurement.  The relationship between PWM duty and actual
    motor speed depends on load, supply voltage, motor characteristics, and
    other factors that are not modelled here.

    ``measured_rpm`` is optional because:
    - The current hardware design does not include a confirmed RPM sensor
      (e.g., encoder or Hall-effect sensor).
    - Do not infer or fabricate RPM from PWM duty alone.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = Field(default=SCHEMA_VERSION)
    run_id: NonEmptyStr = Field(description="Run identifier this context belongs to.")
    timestamp_us: int = Field(
        ge=0,
        description="Microseconds elapsed since the start of the acquisition run.",
    )
    pwm_duty: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Normalized PWM duty cycle in [0.0, 1.0].  "
            "This is the *commanded* motor driver input, "
            "NOT a verified RPM or speed measurement."
        ),
    )
    operating_regime: OperatingRegime = Field(
        description=(
            "Qualitative operating regime derived from the PWM duty band "
            "or an external label.  See OperatingRegime for definitions."
        )
    )
    measured_rpm: Optional[float] = Field(
        default=None,
        description=(
            "Measured motor shaft speed in RPM, if an RPM sensor is "
            "available and produces a reliable reading.  "
            "NULL when no RPM sensor is present or the reading is invalid.  "
            "Must NOT be calculated or approximated from pwm_duty."
        ),
    )

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, v: str) -> str:
        if v != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be '{SCHEMA_VERSION}'; got {v!r}.")
        return v

    @field_validator("pwm_duty")
    @classmethod
    def _validate_finite_pwm(cls, v: float) -> float:
        return _assert_finite(v, "pwm_duty")

    @field_validator("measured_rpm")
    @classmethod
    def _validate_rpm(cls, v: Optional[float]) -> Optional[float]:
        """Validate RPM only when a value is provided.

        When present: must be nonnegative and finite.
        Negative RPM is not physically meaningful for a single-direction
        DC motor drive.
        """
        if v is None:
            return None
        if not _is_finite(v):
            raise ValueError(
                f"measured_rpm must be finite when provided; got {v!r}."
            )
        if v < 0:
            raise ValueError(
                f"measured_rpm must be nonnegative when provided; got {v!r}."
            )
        return v


# ---------------------------------------------------------------------------
# F. FeatureWindow
# ---------------------------------------------------------------------------


class FeatureWindow(BaseModel):
    """Pre-extracted feature vector over a contiguous time window.

    This is the primary interface between the signal-processing pipeline
    and the anomaly-detection / ML components.  It contains *already-
    extracted* statistical and spectral features; no signal processing is
    performed within this model.

    Window quality
    --------------
    A window is VALID only when:
    - ``accel_sample_count`` > 0 and all vibration features are present.
    - ``current_sample_count`` > 0 and all current features are present.

    A window is INVALID when any mandatory feature is absent.  Missing
    values are represented as None; they must NOT be replaced with
    fabricated zeros.

    Environmental features
    ----------------------
    Ambient temperature and humidity are *optional* in every window
    because the DHT11 sampling interval (≥2 s) means environmental data
    may be absent or stale for any given window.  ``environment_sample_age_ms``
    indicates how old the most recent DHT11 reading was at window close.

    Frequency-domain features
    -------------------------
    Band energies (low / high) represent the integral of the power
    spectral density over defined frequency sub-bands, in g².  The exact
    band boundaries are defined by the signal-processing component and
    are documented there; this contract only validates the numeric
    constraints.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    # -- Identification ----------------------------------------------------
    schema_version: str = Field(default=SCHEMA_VERSION)
    device_id: NonEmptyStr = Field(description="Originating device identifier.")
    run_id: NonEmptyStr = Field(description="Acquisition run identifier.")

    # -- Timing ------------------------------------------------------------
    window_start_us: int = Field(
        ge=0,
        description="Window start time in microseconds since run start.",
    )
    window_end_us: int = Field(
        ge=0,
        description=(
            "Window end time in microseconds since run start.  "
            "Must be strictly greater than window_start_us."
        ),
    )

    # -- Acquisition counts ------------------------------------------------
    accel_sample_count: int = Field(
        ge=0,
        description="Number of acceleration samples included in this window.",
    )
    current_sample_count: int = Field(
        ge=0,
        description="Number of current samples included in this window.",
    )

    # -- Vibration features ------------------------------------------------
    vibration_rms_g: Optional[float] = Field(
        default=None,
        description="RMS of the acceleration magnitude vector, in g.  Non-negative.",
    )
    vibration_std_g: Optional[float] = Field(
        default=None,
        description=(
            "Standard deviation of the acceleration magnitude, in g.  "
            "Non-negative."
        ),
    )
    vibration_peak_g: Optional[float] = Field(
        default=None,
        description="Peak acceleration magnitude in the window, in g.  Non-negative.",
    )
    vibration_crest_factor: Optional[float] = Field(
        default=None,
        description=(
            "Crest factor = peak / RMS.  Non-negative.  "
            "A high crest factor may indicate impulsive events."
        ),
    )
    vibration_low_band_energy_g2: Optional[float] = Field(
        default=None,
        description=(
            "Spectral energy in the low-frequency band, in g².  "
            "Non-negative.  Band boundaries defined by signal-processing layer."
        ),
    )
    vibration_high_band_energy_g2: Optional[float] = Field(
        default=None,
        description=(
            "Spectral energy in the high-frequency band, in g².  "
            "Non-negative.  Band boundaries defined by signal-processing layer."
        ),
    )

    # -- Current features --------------------------------------------------
    current_mean_a: Optional[float] = Field(
        default=None,
        description=(
            "Mean motor current over the window, in amperes.  "
            "May be signed (see CurrentSample.motor_current_a)."
        ),
    )
    current_std_a: Optional[float] = Field(
        default=None,
        description=(
            "Standard deviation of motor current over the window, in amperes.  "
            "Non-negative."
        ),
    )
    current_peak_a: Optional[float] = Field(
        default=None,
        description=(
            "Peak absolute motor current in the window, in amperes.  "
            "Non-negative."
        ),
    )

    # -- Environmental context ---------------------------------------------
    ambient_temperature_c: Optional[float] = Field(
        default=None,
        description=(
            "Most recent ambient temperature reading from the DHT11, in °C.  "
            "Null when no environmental sample is available for this window.  "
            "This is NOT motor winding or housing temperature."
        ),
    )
    ambient_humidity_pct: Optional[float] = Field(
        default=None,
        description=(
            "Most recent ambient relative humidity from the DHT11, in %.  "
            "Null when no environmental sample is available."
        ),
    )
    environment_sample_age_ms: Optional[float] = Field(
        default=None,
        ge=0.0,
        description=(
            "Age of the most recent DHT11 reading at window close, in ms.  "
            "Null when no environmental sample is available.  "
            "Large values indicate a potentially stale environmental context."
        ),
    )

    # -- Operating context -------------------------------------------------
    pwm_duty: float = Field(
        ge=0.0,
        le=1.0,
        description="Commanded PWM duty cycle in [0.0, 1.0] at window close.",
    )
    operating_regime: OperatingRegime = Field(
        description="Operating regime classification at window close."
    )

    # -- Quality -----------------------------------------------------------
    window_quality: WindowQuality = Field(
        description=(
            "Top-level quality classification.  VALID windows must have "
            "all required features populated with positive sample counts.  "
            "INVALID windows may have null feature values."
        )
    )
    quality_flags: list[QualityFlag] = Field(
        default_factory=list,
        description=(
            "Detailed quality flags.  Empty list for fully VALID windows.  "
            "May contain multiple flags for INVALID or degraded windows."
        ),
    )

    # -- Validators --------------------------------------------------------

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, v: str) -> str:
        if v != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be '{SCHEMA_VERSION}'; got {v!r}.")
        return v

    @field_validator("pwm_duty")
    @classmethod
    def _validate_finite_pwm(cls, v: float) -> float:
        return _assert_finite(v, "pwm_duty")

    @field_validator(
        "vibration_rms_g",
        "vibration_std_g",
        "vibration_peak_g",
        "vibration_crest_factor",
        "vibration_low_band_energy_g2",
        "vibration_high_band_energy_g2",
        "current_std_a",
        "current_peak_a",
    )
    @classmethod
    def _validate_nonneg_optional(cls, v: Optional[float], info) -> Optional[float]:
        """Non-negative physical magnitudes that may be absent."""
        if v is None:
            return None
        _assert_finite(v, info.field_name)
        if v < 0:
            raise ValueError(
                f"{info.field_name} must be non-negative; got {v!r}."
            )
        return v

    @field_validator("current_mean_a")
    @classmethod
    def _validate_finite_current_mean(cls, v: Optional[float]) -> Optional[float]:
        """Mean current may be signed; only finiteness is required."""
        if v is None:
            return None
        return _assert_finite(v, "current_mean_a")

    @field_validator("ambient_temperature_c")
    @classmethod
    def _validate_finite_temp(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return None
        return _assert_finite(v, "ambient_temperature_c")

    @field_validator("ambient_humidity_pct")
    @classmethod
    def _validate_humidity(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return None
        _assert_finite(v, "ambient_humidity_pct")
        if not (0.0 <= v <= 100.0):
            raise ValueError(
                f"ambient_humidity_pct must be in [0, 100]; got {v!r}."
            )
        return v

    @model_validator(mode="after")
    def _validate_window_consistency(self) -> "FeatureWindow":
        """Enforce logical consistency across window fields.

        Rules
        -----
        1. window_end_us must be strictly greater than window_start_us.
        2. A VALID window must have:
           - accel_sample_count > 0
           - all six vibration features populated (not None)
           - current_sample_count > 0
           - all three current features populated (not None)
        3. Reject logically inconsistent combinations.
        """
        if self.window_end_us <= self.window_start_us:
            raise ValueError(
                "window_end_us must be strictly greater than window_start_us. "
                f"Got start={self.window_start_us}, end={self.window_end_us}."
            )

        if self.window_quality == WindowQuality.VALID:
            required_vibration = {
                "vibration_rms_g": self.vibration_rms_g,
                "vibration_std_g": self.vibration_std_g,
                "vibration_peak_g": self.vibration_peak_g,
                "vibration_crest_factor": self.vibration_crest_factor,
                "vibration_low_band_energy_g2": self.vibration_low_band_energy_g2,
                "vibration_high_band_energy_g2": self.vibration_high_band_energy_g2,
            }
            required_current = {
                "current_mean_a": self.current_mean_a,
                "current_std_a": self.current_std_a,
                "current_peak_a": self.current_peak_a,
            }

            missing_vibration = [k for k, v in required_vibration.items() if v is None]
            missing_current = [k for k, v in required_current.items() if v is None]

            if self.accel_sample_count == 0:
                raise ValueError(
                    "A VALID window must have accel_sample_count > 0."
                )
            if missing_vibration:
                raise ValueError(
                    f"A VALID window must have all vibration features populated. "
                    f"Missing: {missing_vibration}."
                )
            if self.current_sample_count == 0:
                raise ValueError(
                    "A VALID window must have current_sample_count > 0."
                )
            if missing_current:
                raise ValueError(
                    f"A VALID window must have all current features populated. "
                    f"Missing: {missing_current}."
                )

        return self


# ---------------------------------------------------------------------------
# G. HealthDecision
# ---------------------------------------------------------------------------


class HealthDecision(BaseModel):
    """Health classification output from the anomaly-detection model.

    This record is produced by the inference component after evaluating a
    ``FeatureWindow``.  It captures the model's assessment along with
    enough provenance information for traceability.

    Anomaly scores
    --------------
    ``anomaly_score`` carries the raw Isolation Forest score (or equivalent
    from another model).  Isolation Forest scores are NOT probabilities of
    failure and do NOT naturally reside in [0, 1].  No artificial clamping
    is applied.

    Null scores
    -----------
    Both ``anomaly_score`` and ``anomaly_threshold`` are nullable for
    health states in which inference was not or could not be performed
    (e.g., SENSOR_ERROR, UNKNOWN_CONDITION).  Do not fill with fabricated
    values.

    Remaining useful life
    ---------------------
    No RUL estimate or failure-probability field is included.  These
    require validated failure datasets that do not currently exist for
    this project.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = Field(default=SCHEMA_VERSION)
    device_id: NonEmptyStr = Field(description="Originating device identifier.")
    run_id: NonEmptyStr = Field(description="Acquisition run identifier.")
    window_end_us: int = Field(
        ge=0,
        description="window_end_us of the FeatureWindow this decision covers.",
    )
    health_state: HealthState = Field(
        description="Machine health classification. See HealthState for definitions."
    )
    anomaly_score: Optional[float] = Field(
        default=None,
        description=(
            "Raw anomaly score from the detection model.  "
            "NOT a probability of failure.  "
            "Null when inference was not performed."
        ),
    )
    anomaly_threshold: Optional[float] = Field(
        default=None,
        description=(
            "Decision threshold used to classify this window.  "
            "Null when inference was not performed."
        ),
    )
    model_version: Optional[str] = Field(
        default=None,
        description=(
            "Version identifier of the anomaly-detection model that produced "
            "this decision.  Null when no model was applied."
        ),
    )

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, v: str) -> str:
        if v != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be '{SCHEMA_VERSION}'; got {v!r}.")
        return v

    @field_validator("anomaly_score", "anomaly_threshold")
    @classmethod
    def _validate_finite_scores(cls, v: Optional[float], info) -> Optional[float]:
        """Scores must be finite when provided; NaN/Inf are rejected."""
        if v is None:
            return None
        if not _is_finite(v):
            raise ValueError(
                f"{info.field_name} must be finite when provided; got {v!r}."
            )
        return v


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "SCHEMA_VERSION",
    "OperatingRegime",
    "WindowQuality",
    "QualityFlag",
    "HealthState",
    "RunMetadata",
    "AccelerationSample",
    "CurrentSample",
    "EnvironmentSample",
    "OperatingContext",
    "FeatureWindow",
    "HealthDecision",
]
