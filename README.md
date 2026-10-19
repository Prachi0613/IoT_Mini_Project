# Self-Learning IoT Predictive Maintenance Node

> **Status**: TASK 01 — Project Foundation and Data Contracts  
> **Phase**: Data-contract layer only. No ML model, no firmware, no backend.

---

## 1. Project Goal

Build an ESP32-based machine condition-monitoring and early-degradation
detection system for an industrial DC motor.  The system will collect
vibration, current, and ambient environment data, extract statistical and
spectral features on-edge, run an Isolation Forest anomaly detector
(initially trained offline, exported to C++ inference for the ESP32),
and forward health decisions to a FastAPI backend via MQTT.

This repository currently contains only the **data-contract layer**: the
Pydantic v2 models that define the shape, units, validation rules, and
JSON schemas for every inter-component data message.

---

## 2. Confirmed Hardware

| Component | Role |
|-----------|------|
| ESP32 DevKit V1 | Microcontroller, acquisition, edge inference |
| MPU6050 | Three-axis MEMS accelerometer — vibration data |
| ACS712 | Hall-effect current sensor — motor supply current |
| DHT11 | Ambient temperature and relative humidity |
| DC motor + driver | Device under monitoring |

---

## 3. Why DHT11 Is Ambient-Only

The DHT11 measures **ambient air temperature and relative humidity** at
the sensor's physical location.  It does **not** measure:

- Motor winding temperature
- Motor housing / case temperature
- Bearing temperature

Motor thermal behaviour requires a sensor mounted directly on the motor
body (e.g., a thermistor or thermocouple).  All DHT11-derived fields in
this contract are named with the `ambient_` prefix to make this
distinction explicit.  The field name `motor_temperature` is intentionally
absent.

---

## 4. Data-Acquisition Design Targets

These are **engineering design targets**, not verified hardware
performance figures.  Actual throughput depends on ESP32 I²C/ADC timing,
task scheduling, and sensor characteristics.

| Signal | Target Rate | Sensor |
|--------|-------------|--------|
| Acceleration (X, Y, Z) | 200 Hz | MPU6050 |
| Motor current | 200 Hz | ACS712 |
| Ambient temperature & humidity | 1 per 2 s | DHT11 |

> The DHT11 minimum sampling interval is approximately 1 s (recommended
> 2 s).  It is **not** capable of 200 Hz operation and must never be
> modelled at that rate.

---

## 5. Raw Samples vs. Feature Windows

| Concept | Description |
|---------|-------------|
| **Raw sample** | A single time-stamped sensor reading: one acceleration vector, one current value, or one DHT11 reading. |
| **Feature window** | A pre-extracted statistical and spectral summary computed over a contiguous block of raw samples (e.g., 1 second = 200 acceleration samples).  This is the input to the anomaly-detection model. |

Signal processing (windowing, FFT, RMS extraction) is performed upstream
of this contract layer.  `FeatureWindow` represents the *result* of that
processing, not the raw samples.

---

## 6. Timestamp Semantics

All `timestamp_us` fields in sample and feature contracts represent
**microseconds elapsed since the start of the acquisition run** — they
are *relative* timestamps, not Unix epoch values.

- Run start time is stored as an absolute UTC datetime in `RunMetadata.started_at_utc`.
- `started_at_utc` must be timezone-aware and in UTC.  Naive datetimes
  are rejected.
- Timestamps within a run monotonically increase (not enforced here;
  enforced by the acquisition firmware).

---

## 7. Why RPM Cannot Currently Be Assumed

No RPM sensor (encoder, Hall-effect, optical) is confirmed in the current
hardware revision.  `pwm_duty` is the **commanded** duty cycle sent to
the motor driver.  The relationship between PWM duty and actual shaft RPM
depends on load, supply voltage, motor characteristics, and losses that
are not modelled here.

`OperatingContext.measured_rpm` and `FeatureWindow`'s operating fields
are intentionally kept separate from `pwm_duty`.  `measured_rpm` is
optional and nullable; it must **not** be derived from `pwm_duty`.

---

## 8. Purpose of the Contract Layer

The contract layer serves as the **single source of truth** for all
inter-component interfaces.  It:

1. Enforces physical validity constraints (finite values, valid ranges,
   correct units) at data-entry time.
2. Prevents physically meaningless fields (e.g., `motor_temperature` from
   DHT11, RPM fabricated from PWM).
3. Generates JSON Schema for schema validation across languages / services.
4. Documents hardware constraints and design decisions in code via
   docstrings and field descriptions.
5. Decouples sensor acquisition, signal processing, ML inference, and
   backend storage — each subsystem can validate messages independently.

---

## 9. Environment Installation

> Requires Python ≥ 3.11.

```bash
# Create and activate a virtual environment (Windows PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1

# Install the package in editable mode with development dependencies
pip install -e ".[dev]"
```

---

## 10. Running Tests

```bash
python -m pytest -q
```

Expected: **all tests pass**.  TC-20 requires that schema files exist
(run schema generation first, or run tests after generation).

---

## 11. Schema Generation and Verification

Generate all JSON schemas (writes to `contracts/`):

```bash
python scripts/export_schemas.py
```

Verify schemas are up-to-date with the current Pydantic source (no writes):

```bash
python scripts/export_schemas.py --check
```

Exit code is `0` when all schemas are current, `1` when any schema is
missing or stale.

---

## 12. What Has Been Implemented (TASK 01)

- [x] `src/pmnode/__init__.py` — package init
- [x] `src/pmnode/contracts.py` — seven Pydantic v2 data-contract models
  - `RunMetadata`
  - `AccelerationSample`
  - `CurrentSample`
  - `EnvironmentSample`
  - `OperatingContext`
  - `FeatureWindow`
  - `HealthDecision`
- [x] `scripts/export_schemas.py` — JSON Schema generation with `--check` mode
- [x] `contracts/` — generated JSON Schema files (one per model)
- [x] `tests/test_contracts.py` — 40+ pytest tests covering all 20 acceptance criteria
- [x] `pyproject.toml` — project configuration (Pydantic v2, pytest, editable install)
- [x] `.gitignore` — standard Python ignore rules
- [x] `README.md` — this document

---

## 13. What Has NOT Been Implemented

The following subsystems are explicitly deferred to future tasks and are
**not** present in this repository:

- [ ] ESP32 firmware (C++ / MicroPython acquisition code)
- [ ] MQTT publisher / subscriber
- [ ] Signal processing / feature extraction pipeline
- [ ] Isolation Forest model training (Python / scikit-learn)
- [ ] C++ Isolation Forest inference export for ESP32
- [ ] FastAPI backend server
- [ ] PostgreSQL database schema
- [ ] React dashboard
- [ ] Docker / container configuration
- [ ] Synthetic data generator / simulator
- [ ] Any real sensor recordings or validated failure datasets

---

## 14. Project Architecture (Target — Not Yet Implemented)

```
Industrial motor
    └─► Sensors (MPU6050, ACS712, DHT11)
            └─► ESP32 DevKit V1
                    ├─► Signal processing (windowing, FFT, RMS)
                    ├─► Edge Isolation Forest inference (exported C++)
                    └─► MQTT publish
                            └─► FastAPI backend
                                    ├─► PostgreSQL storage
                                    └─► React dashboard
```

---

## License

MIT — see project metadata in `pyproject.toml`.
