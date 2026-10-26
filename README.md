# Self-Learning IoT Predictive Maintenance Node — Tasks 01–02

An IoT-based machine-condition monitoring and anomaly detection prototype
within a predictive-maintenance framework.

The current implementation is software-only: a deterministic, explicitly
labelled simulator sends versioned MPU6050/DHT11-shaped telemetry to FastAPI,
which validates and stores exact values. Task 02 adds offline signal processing,
anomaly detection, temporal health classification, and guarded reference
adaptation. No dashboard or hardware integration is implemented.

## Confirmed hardware for later integration

- ESP32 DevKit
- MPU6050 three-axis accelerometer and three-axis gyroscope
- DHT11 ambient temperature and relative-humidity sensor
- Independently powered DC motor or small fan being monitored
- Laptop for backend, future training, database, and dashboard

No current, voltage, power, RPM, motor-temperature, or motor-control sensor is
available. DHT11 values describe ambient air, not motor temperature. No
hardware has been connected or tested during this task.

## Current architecture

```text
Deterministic SIMULATED producer → JSON telemetry 1.0.0 → FastAPI
→ transactional SQLite → retrieval API / CSV export
```

Every session is identified as `SIMULATED` or `HARDWARE`; the Task 01 simulator
always emits `SIMULATED`. A future ESP32 producer can send the same contract
without changing the backend or database. There is no firmware in this task.

## Install on Windows

Python 3.11 or newer is required. From the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Portable activation alternative: `source .venv/bin/activate` on macOS/Linux.
The project uses FastAPI, Pydantic v2, Uvicorn, sqlite3, pytest, and httpx; it
does not require NumPy, SciPy, Docker, MQTT, or physical hardware.

## Run the backend

```powershell
python -m uvicorn pmnode.api:app --host 127.0.0.1 --port 8000
```

The default database is `data/telemetry.sqlite3`. Override it before startup:

```powershell
$env:PMNODE_DATABASE = "data\my-recording.sqlite3"
```

Uvicorn controls bind host and port. Optional resource limits are
`PMNODE_MAX_MOTION_SAMPLES`, `PMNODE_MAX_ENVIRONMENT_SAMPLES`,
`PMNODE_MAX_REQUEST_BYTES`, and `PMNODE_MAX_QUERY_LIMIT`.

## Run the simulator

All examples below generate mathematical test signals—not actual machine data.

Offline healthy recording (JSON Lines plus separate test-ground-truth labels):

```powershell
python scripts\run_simulator.py --mode offline --scenario healthy `
  --duration 10 --sample-rate 500 --seed 42 `
  --output simulation-output\healthy.jsonl
```

Live healthy, vibration-anomaly, and dropout examples:

```powershell
python scripts\run_simulator.py --mode live --scenario healthy --duration 5 --pace 1
python scripts\run_simulator.py --mode live --scenario vibration_anomaly --duration 5
python scripts\run_simulator.py --mode live --scenario sensor_dropout --duration 5
```

The simulator also supports `transient_disturbance`, `--device-id`,
`--batch-duration`, `--endpoint`, bounded `--timeout`, and zero-to-five
`--retries`. Its deterministic acquisition clock is independent of live HTTP
pacing. A failed batch stops later transmission and is never reported as sent.

## View stored data

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
Invoke-RestMethod http://127.0.0.1:8000/api/v1/sessions
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/latest?device_id=sim-node-01"
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/telemetry?device_id=sim-node-01&boot_id=BOOT_ID&sensor=motion&limit=100&offset=0"
```

`/telemetry` accepts `sensor=motion` or `sensor=environment`, has deterministic
sequence ordering, and uses bounded offset pagination. It does not interpolate
missing samples. `/health` reports service/database health, not motor health.

Identical retries of `(device_id, boot_id, batch_seq)` are acknowledged without
reinsertion. Reusing that identity for different canonical content returns HTTP
409 rather than overwriting raw records.

## Export CSV

```powershell
python scripts\export_csv.py --database data\telemetry.sqlite3 `
  --device sim-node-01 --session BOOT_ID --select both `
  --output-dir exports\session-01
```

`--select` may be `motion`, `environment`, or `both`. Motion and environmental
measurements are exported separately, with a JSON metadata file containing
source type, configuration, cumulative quality counters, and backend receipt
times. Export never fills timestamp gaps or changes stored records.

## Contracts and tests

Generate deterministic schemas from Pydantic and verify they are current:

```powershell
python scripts\export_schemas.py
python scripts\export_schemas.py --check
python -m pytest -q
```

## Dataset integrity and units

- `t_mono_us` is producer/session elapsed microseconds, not wall-clock UTC.
- `received_at_utc` is a timezone-aware server receipt timestamp, not an
  acquisition timestamp.
- Acceleration is in g and includes simulated gravity; angular velocity is in
  degrees/second. Ambient temperature is °C and humidity is percent.
- Motion defaults to a 500 Hz simulation target. Environment observations are
  separate and no more frequent than every two seconds.
- Quality counters are cumulative since the boot/session began.
- Scenario labels live in the separate offline labels file, never in sensor
  measurement fields.
- The 500 Hz setting is a simulation configuration, not verified ESP32
  performance.

Synthetic anomaly and dropout scenarios test software behavior only. They do
not represent validated bearing faults and cannot establish real-world failure
prediction performance.

## Limitations

- No ESP32 or sensor has been connected.
- No actual motor measurement has been acquired.
- The anomaly model and adaptive reference are validated only against synthetic
  software scenarios; no dashboard or physical failure metric exists.
- HTTP has no authentication or transport security and is intended only for
  local development.
- Offset pagination is sufficient for Task 01 but not optimized for very large
  future datasets.

## Task 02: signal processing and anomaly detection

Task 02 adds an offline, laptop-hosted condition-analysis pipeline:

```text
SQLite motion data → timestamp-based windows → validated DSP features
→ robust scaling → frozen Isolation Forest + robust statistical reference
→ temporal health state → guarded adaptive reference
```

### Feature windows and units

Windows default to one second with zero overlap and are formed from producer
monotonic timestamps—not fixed groups of 500 rows. Observed sampling rate is
`1 / median(timestamp interval)`. A window is invalid when it has fewer than
80% of expected samples, fewer than 64 spectral samples, a gap above three
median intervals, timestamp jitter above 20% of the median interval, or cannot
support the configured spectral bands. Missing samples are never interpolated.

For a rigid, stationary mounting, each acceleration and gyroscope axis has its
window mean removed. This separates local DC/gravity/offset components from
dynamic motion; it is not claimed to solve gravity separation on arbitrarily
moving machines. Acceleration features use the dynamic three-axis vector:

- RMS, standard deviation, peak, peak-to-peak, crest factor, variance and mean
  squared energy in g-based units;
- dominant frequency and Welch PSD band energy for 5–30 Hz and 30–100 Hz;
- gyroscope vector RMS, standard deviation and peak in degrees/second.

Welch uses the timestamp-derived effective sampling rate, density scaling and
constant detrending. Bands must fit below Nyquist. Unsupported or severely
irregular windows remain invalid with null features; zero-RMS crest factor is
defined as zero. Gyroscope features are angular-velocity context, not direct
bearing-vibration measurements.

The latest valid DHT11 observation at or before each window end is attached as
ambient metadata. Its age is retained and values older than four seconds are
marked stale. Environmental context is not a required model input.

### Training and scoring

First ingest data and extract each explicitly selected session:

```powershell
python -m pmnode.ml.cli extract --database data\telemetry.sqlite3 `
  --session sim-node-01:HEALTHY_TRAIN_BOOT
```

Train only with explicitly designated healthy sessions and a separate healthy
calibration recording:

```powershell
python -m pmnode.ml.cli train --database data\telemetry.sqlite3 `
  --train sim-node-01:HEALTHY_TRAIN_A `
  --train sim-node-01:HEALTHY_TRAIN_B `
  --calibration sim-node-01:HEALTHY_CALIBRATION `
  --artifact artifacts\isolation-forest-v1.joblib `
  --model-version isolation-forest-v1 --seed 42 --quantile 0.99
```

Training uses a `RobustScaler` fitted only on healthy training windows. The
frozen Isolation Forest uses 64 trees, at most 256 training windows per tree,
`contamination="auto"`, and a deterministic seed. Higher anomaly means
`-IsolationForest.score_samples`. The threshold is the configured quantile
(default 99th percentile) of separate healthy-calibration scores. Scores are
not probabilities and are not forced into a 0–1 range.

```powershell
python -m pmnode.ml.cli model-list --database data\telemetry.sqlite3
python -m pmnode.ml.cli model-activate --database data\telemetry.sqlite3 `
  --model-version isolation-forest-v1
python -m pmnode.ml.cli score --database data\telemetry.sqlite3 `
  --session sim-node-01:HELD_OUT_TEST --model artifacts\isolation-forest-v1.joblib
python -m pmnode.ml.cli evaluate --database data\telemetry.sqlite3 `
  --session sim-node-01:HELD_OUT_TEST --model artifacts\isolation-forest-v1.joblib `
  --output-dir experiment-output\evaluation
```

Whole sessions—not adjacent windows—must be separated across training,
calibration and test partitions. Overlap is rejected. `SIMULATED` and
`HARDWARE` features cannot be mixed, and a synthetic model is activated only
as `ACTIVE_SYNTHETIC_DEVELOPMENT`.

### Health states

The state engine processes chronological windows independently per device boot:

- `NORMAL`: scores remain inside calibrated limits;
- `WARNING`: at least three of the latest five valid windows are anomalous;
- `DEGRADING`: at least six of eight are anomalous with an increasing trend;
- `MAINTENANCE`: at least nine of ten exceed a conservative severe threshold;
- `SENSOR_ERROR`: a window is invalid or unavailable;
- `UNKNOWN_CONDITION`: reserved for unsupported/untrained regimes.

Three healthy windows are required for recovery hysteresis. These are initial
software policies, not validated physical failure criteria.

### Controlled adaptation and rollback

Isolation Forest is trained offline. Controlled online reference adaptation is
implemented separately. The frozen forest does not automatically retrain on
each incoming sample.

Adaptation is disabled by default. When explicitly enabled, a window must be
valid, source/schema compatible, safely inside both detector thresholds,
currently `NORMAL`, and part of a consecutive bounded healthy candidate set.
Updates use bounded exponential blending of robust center and MAD-derived
spread, with per-update and cumulative drift limits. Scoring occurs against the
previously approved reference before eligibility is decided.

```powershell
python -m pmnode.ml.cli adapt --database data\telemetry.sqlite3 `
  --session sim-node-01:APPROVED_HEALTHY_SESSION `
  --model artifacts\isolation-forest-v1.joblib --enable --approve
python -m pmnode.ml.cli baseline-status --database data\telemetry.sqlite3 `
  --model-version isolation-forest-v1 --events
python -m pmnode.ml.cli rollback --database data\telemetry.sqlite3 `
  --model-version isolation-forest-v1 --reference-version 1
```

Historical references are immutable. Rollback reactivates the exact stored
center, spread and threshold—not merely a version number.

### Reproducible synthetic demonstration

```powershell
python scripts\run_ml_demo.py `
  --output-dir experiment-output\task02-synthetic --sample-rate 500
```

The bounded demonstration creates independent training, calibration, healthy
test, synthetic-anomaly test and adaptation sessions; persists raw/features/
predictions; compares the robust statistical detector with Isolation Forest;
generates JSON/PNG evaluation artifacts; exercises guarded adaptation and
abnormal rejection; and verifies exact rollback.

Simulator scenario timing is software-test ground truth only. Reports and
artifacts are prominently marked **SYNTHETIC SOFTWARE VALIDATION — NOT REAL
MACHINE PERFORMANCE.** They cannot establish physical fault-detection accuracy,
failure probability, remaining useful life, or ESP32 sampling capability. A
future ESP32 will enter through the unchanged Task 01 telemetry contract.

## Task 03 live monitoring application

### Application architecture

The Task 01 simulator posts validated batches to FastAPI and SQLite. An explicit
single-owner worker reads new raw rows in device/session order, finalizes
one-second feature windows, calls the Task 02 frozen Isolation Forest and
temporal health engine, then persists predictions, decisions and deduplicated
events. Versioned monitoring APIs are the only data source for the React +
TypeScript dashboard in `frontend/`.

Raw telemetry is immutable. Derived windows, predictions, health decisions,
worker checkpoints and events live in separate additive tables. `source_type`
is preserved throughout; a synthetic-development model is never selected for
`HARDWARE` sessions.

### Installation

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
cd frontend
npm install
cd ..
```

Python 3.11+ and Node.js 20+ are required. `frontend/package-lock.json` records
the resolved frontend versions.

### Running the system

Open four PowerShell terminals from the repository root, activate `.venv`, then:

```powershell
# 1. API — localhost only
$env:PMNODE_DATABASE = "data\telemetry.sqlite3"
python -m uvicorn pmnode.api:app --host 127.0.0.1 --port 8000

# 2. Incremental processor
python -m pmnode.live.cli run --database data\telemetry.sqlite3 --poll-seconds 1

# 3. Dashboard
cd frontend
npm run dev

# 4. Existing simulator (repeat with each documented scenario)
.\.venv\Scripts\python.exe scripts\run_simulator.py --mode live `
  --scenario healthy --duration 10 --sample-rate 500 --seed 42 `
  --device-id demo-node --batch-duration 1 `
  --endpoint http://127.0.0.1:8000/api/v1/batches
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` to the local backend. CORS is
restricted by `PMNODE_CORS_ORIGINS` (comma separated) and defaults to local
Vite origins. Do not expose this unauthenticated prototype publicly.

### Preparing a model and demonstration

Use the Task 02 `extract`, `train`, and `model-activate` commands above with
independent healthy training and calibration sessions. An
`ACTIVE_SYNTHETIC_DEVELOPMENT` model scores only `SIMULATED` sessions. Without
a compatible model, valid features are retained and `MODEL_UNAVAILABLE` is
reported; scores and `NORMAL` health are never invented.

To register an already trained, trusted PMNode artifact in a live database:

```powershell
python -m pmnode.ml.cli model-import --database data\telemetry.sqlite3 `
  --artifact experiment-output\task03-live-final\models\isolation-forest-live-v1.joblib `
  --activate --requeue-unscored
```

Importing verifies the model type, feature schema and feature order, creates
the immutable initial reference when absent, and activates it only for its
recorded source type. The explicit requeue option lets the worker revisit
sessions that were feature-extracted while no model was available. Do not
import untrusted joblib files.

```powershell
python scripts\run_live_demo.py --output-dir experiment-output\task03-live `
  --sample-rate 500
```

This isolated bounded run uses the existing simulator and Task 02 training
code, processes healthy, anomaly, transient and dropout sessions, checks worker
restart idempotency and monitoring APIs, and writes `summary.json`. It refuses
to overwrite an existing directory.

### Live processing

Windows align to session monotonic time. A 250 ms lateness watermark requires
later observations before normal finalization. After three seconds without a
receipt, the final candidate is flushed, but normal quality rules still reject
truncated or undersampled data. Samples are never interpolated. Rows arriving
behind a checkpoint are recorded in `processing_issues` and do not rewrite
historical predictions.

Checkpoints persist the next boundary and temporal health state. Unique keys
make derived writes and events restart-safe. A renewable SQLite lease prevents
simultaneous workers; a clean stop releases it and a crashed lease expires in
15 seconds. SQLite lock retries are bounded. This prototype intentionally uses
one laptop worker.

### Dashboard and model handling

- **Overview** polls bounded overview/event responses every two seconds and
  shows only API-returned measurements.
- **History** switches sessions without mixing responses, with bounded feature
  and anomaly series plus separate original DHT observation times.
- **Models** is read-only and exposes reference/adaptation metadata, never
  artifact paths or joblib contents.
- **Events** shows persisted transitions and processing failures; acknowledgement
  does not alter predictions.

Isolation Forest inference uses a frozen trained model. Controlled reference
adaptation is separate and disabled by default; viewing data is not learning.

### Known Task 03 limitations

- No hardware is connected and there are no real machine-failure recordings.
- Synthetic results do not establish physical predictive-maintenance accuracy.
- Actual 500 Hz ESP32/MPU6050 acquisition remains unverified.
- DHT11 is ambient-only; motor temperature is not measured.
- RPM, current, power and remaining useful life are not measured or predicted.
- Inference runs on the laptop over local HTTP development transport.
- There is no authentication; localhost binding is required.

## Task 04 hardware-ready software

The `firmware/` PlatformIO project targets an ESP32 DevKit with an MPU6050 on
GPIO21/GPIO22 and a DHT11 on GPIO27. It emits the existing `1.0.0` telemetry
contract with `source_type=HARDWARE`; ML inference remains on the laptop. No
physical acquisition, Wi-Fi performance, or 500 Hz cadence is claimed until the
next hardware phase. See `docs/hardware-integration.md` before wiring anything.

### Exact Windows PowerShell commands

Run these from the repository root. Each long-running service needs its own
PowerShell terminal.

```powershell
# 1. Create/activate the environment and install Python dependencies
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"

# 2. Start FastAPI (terminal 1)
$env:PMNODE_DATABASE = "data\telemetry.sqlite3"
python -m uvicorn pmnode.api:app --host 127.0.0.1 --port 8000

# 3. Start the incremental ML worker (terminal 2)
.\.venv\Scripts\Activate.ps1
python -m pmnode.live.cli run --database data\telemetry.sqlite3 --poll-seconds 1

# 4. Start the React dashboard (terminal 3)
cd frontend
npm install
npm run dev

# 5. Send a clearly labelled simulated session (terminal 4)
.\.venv\Scripts\python.exe scripts\run_simulator.py --mode live `
  --scenario healthy --duration 10 --sample-rate 500 --seed 42 `
  --device-id demo-node --batch-duration 1 `
  --endpoint http://127.0.0.1:8000/api/v1/batches --retries 1

# 6. View stored sessions through the monitoring API
Invoke-RestMethod http://127.0.0.1:8000/api/v1/monitor/devices | ConvertTo-Json -Depth 8

# 7. Export a chosen session without filling gaps
.\.venv\Scripts\python.exe scripts\export_csv.py `
  --database data\telemetry.sqlite3 --device demo-node `
  --session REPLACE_WITH_BOOT_ID --select both --output-dir data\exports

# 8. Run Python, schema, and bytecode checks
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\export_schemas.py --check
.\.venv\Scripts\python.exe -m compileall -q src scripts

# 9. Run frontend tests and production build
cd frontend
npm test -- --run
npm run build
cd ..

# 10. Prepare local firmware configuration and compile (never commit config.h)
Copy-Item firmware\include\config.example.h firmware\include\config.h
# Edit firmware\include\config.h with the local SSID/backend/device values.
$env:PLATFORMIO_CORE_DIR = (Resolve-Path firmware\.pio-core).Path
.\.venv\Scripts\platformio.exe run --project-dir firmware
```

The firmware holds at most four queued batches of 100 motion samples. A batch
is also closed after 250 ms, so network work stays outside the sampling loop.
Payloads are serialized once and reused for at most three HTTP attempts with
bounded timeouts/backoff. Overflowed or ultimately abandoned batches are
counted/logged; delivery is not guaranteed. DLPF setting 1 is selected because
its nominal accelerometer bandwidth is about 184 Hz and gyro bandwidth about
188 Hz, keeping the planned 30–100 Hz band inside the nominal sensor passband
at the 500 Hz output rate. Its higher noise and non-brick-wall alias rejection,
and the actual physical response, still require hardware validation.

For a bounded software-only end-to-end run:

```powershell
.\.venv\Scripts\python.exe scripts\run_live_demo.py `
  --output-dir experiment-output\task04-software-demo --sample-rate 500
```

The demonstration is synthetic software evidence only. A synthetic-trained
model is deliberately incompatible with future `HARDWARE` sessions; raw data
and valid features may be retained, but no hardware anomaly score or NORMAL
state is invented until an approved hardware-source model exists.

For ESP32 operation, bind the API to `0.0.0.0`, configure the firmware with the
laptop's active Wi-Fi IPv4 address, and restrict firewall access to the trusted
Private network. The exact LAN checks, firmware build/upload boundary,
acknowledgement behavior, and troubleshooting procedure are in
[`docs/hardware-integration.md`](docs/hardware-integration.md). Physical upload
and hardware verification are intentionally still pending.
