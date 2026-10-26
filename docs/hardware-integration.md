# Hardware Integration Preparation Guide

This guide describes the controlled physical-integration phase. The software
path is tested, but no ESP32 has been flashed and no physical sensor, Wi-Fi
link, sampling cadence, or motor measurement has been verified.

## A. Physical connections

Disconnect power while wiring and verify the labels printed on each actual
module; breakout-board pin order is not standardized.

| Module | Module pin | ESP32 connection |
|---|---|---|
| MPU6050 | VCC | 3V3 |
| MPU6050 | GND | GND |
| MPU6050 | SDA | GPIO21 |
| MPU6050 | SCL | GPIO22 |
| DHT11 | VCC | 3V3 |
| DHT11 | GND | GND |
| DHT11 | DATA | GPIO27 |

A bare DHT11 may require a data-line pull-up; inspect whether the available
module already contains one. The motor or fan must use its own suitable power
supply. Never power it from an ESP32 GPIO. The firmware monitors only and has no
motor-control path.

## B. Start the laptop services on a trusted LAN

Use the laptop and ESP32 on the same trusted, non-guest Wi-Fi network. The API
has no authentication or TLS, so never expose port 8000 to the public internet.

1. Run `ipconfig` and find the IPv4 address of the active Wi-Fi adapter. Use an
   address such as `192.168.x.x` or `10.x.x.x`; never put `127.0.0.1`,
   `localhost`, or `0.0.0.0` in the firmware configuration.
2. From the repository root, start these in separate PowerShell terminals:

```powershell
# Terminal 1: API, reachable from the ESP32 on the LAN
.\.venv\Scripts\Activate.ps1
$env:PMNODE_DATABASE = "data\telemetry.sqlite3"
python -m uvicorn pmnode.api:app --host 0.0.0.0 --port 8000

# Terminal 2: incremental feature/model worker
.\.venv\Scripts\Activate.ps1
python -m pmnode.live.cli run --database data\telemetry.sqlite3 --poll-seconds 1

# Terminal 3: dashboard
cd frontend
npm run dev
```

3. Verify the API locally, then through the actual Wi-Fi address:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
Invoke-RestMethod http://<LAPTOP-LAN-IP>:8000/api/v1/health
Test-NetConnection <LAPTOP-LAN-IP> -Port 8000
```

Replace the angle-bracket placeholder before running these commands. If
Windows prompts for firewall access, allow Python only on **Private networks**.
Do not disable the firewall and do not enable Public-network access. If the LAN
check fails, confirm that Windows marks the network Private and that the router
is not using guest-network/client isolation. Binding Uvicorn to `0.0.0.0` makes
it reachable; CORS settings do not control ESP32 HTTP access.

## C. Configure and build the firmware

```powershell
Copy-Item firmware\include\config.example.h firmware\include\config.h
# Edit config.h: WIFI_SSID, WIFI_PASSWORD, BACKEND_HOST, BACKEND_PORT, DEVICE_ID.
pio device list
$env:PLATFORMIO_CORE_DIR = (Resolve-Path firmware\.pio-core).Path
.\.venv\Scripts\platformio.exe run --project-dir firmware
```

`BACKEND_HOST` must be the laptop's active LAN IPv4 address. Keep `config.h`
local because it contains credentials. Building does not modify a board. Upload
only after the correct serial port and wiring have been checked and explicit
approval has been given:

```powershell
.\.venv\Scripts\platformio.exe run --project-dir firmware --target upload
.\.venv\Scripts\platformio.exe device monitor --project-dir firmware
```

## D. Initial bring-up sequence

1. Inspect the modules, pin labels, wiring, supply voltage, and common ground.
2. Identify the ESP32 serial port in Device Manager or `pio device list`.
3. Confirm `config.h` contains the local Wi-Fi, laptop LAN IP, backend port, and
   a stable unique device ID.
4. Confirm both health checks in section B succeed.
5. Build, then upload only through the explicitly selected ESP32 serial port.
6. Inspect startup output using `pio device monitor`; do not enter a reboot loop.
7. Confirm the reported MPU6050 address and WHO_AM_I identity.
8. Confirm plausible raw acceleration and gyroscope readings while stationary.
9. Confirm DHT11 ambient temperature/humidity and explicit failed-read records.
10. Inspect monotonic timestamp intervals and all cumulative quality counters.
11. Capture and validate real HTTP payloads and acknowledgement identities.
12. Query SQLite and confirm `source_type=HARDWARE`, the new boot ID, original
    timestamps, and raw sample counts. Confirm there are no synthetic scores.

Use these read-only checks while the node is running:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/monitor/devices
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/telemetry?device_id=<DEVICE-ID>&limit=10"
```

Open `http://127.0.0.1:5173/` for the dashboard. A hardware session remains
visible even when its model status is `MODEL_UNAVAILABLE`. This is expected
until a compatible, approved `HARDWARE` model exists; never substitute the
synthetic model or invent an anomaly score.

## E. Sampling validation

The configured target is 500 Hz, not a measured result. For each recorded
session, calculate consecutive `t_mono_us` differences and report median
interval, observed rate (`1,000,000 / median_interval`), interval standard
deviation, and maximum gap. Compare these with cumulative `missed_deadlines`,
`buffer_overruns`, and `i2c_read_errors`. Inspect both short stationary runs and
longer motor-on runs. If 500 Hz is unstable, lower `REQUESTED_MPU_HZ` to a
divisor supported by the 1 kHz DLPF-enabled output and record the sustainable
measured rate. Never hide errors to make the target appear successful.

DLPF configuration 1 has nominal 184 Hz accelerometer and 188 Hz gyro bandwidth
with a 1 kHz internal output. `SMPLRT_DIV=1` therefore requests 500 Hz. This
keeps the planned 30–100 Hz software feature band inside the nominal passband,
unlike configuration 2's 94 Hz accelerometer bandwidth. The wider setting also
passes more noise and is not a brick-wall anti-alias filter, so actual response
and alias rejection must be characterized on the physical installation.

## F. Physical mounting and safety

- Attach the MPU6050 rigidly to the motor/fan housing or repeatable mounting
  structure; loose mounting measures cable and board motion.
- Record and preserve sensor-axis orientation across experiments.
- Secure wires away from fan blades, shafts, and other moving components.
- Guard exposed rotating parts and keep hands, clothing, and cables clear.
- Keep supply and operating conditions repeatable.
- Do not create bearing damage, imbalance, obstruction, electrical faults, or
  other dangerous fault injection merely to obtain anomaly data.

## G. Real-data experiments

Record separate, explicitly named sessions for:

1. Motor-off stationary reference.
2. Multiple healthy motor-on runs under repeatable conditions.
3. Independent healthy training sessions.
4. Independent healthy calibration sessions.
5. Held-out healthy test sessions.
6. Safe, reversible, repeatable vibration disturbances that do not damage the
   equipment or expose moving components.

Store run metadata and independently observed event timestamps. Do not treat
scenario names as ground-truth failure diagnoses. These recordings are planned,
not completed.

## H. Hardware model preparation

After sufficient genuine data exist, inspect acquisition quality and exclude or
retain sessions by an explicit documented rule. Extract features with the
existing pipeline. Select separate `HARDWARE` healthy training and calibration
sessions, train a hardware-source model, validate held-out sessions, and only
then explicitly activate the approved model. Evaluate anomaly behavior against
actual experimental observations. Never relabel or promote the synthetic model
as a hardware model, and keep automatic reference adaptation disabled until
guarded healthy evidence is reviewed and approved.

## I. Troubleshooting

- **Connection refused or timeout:** verify the API is running, the firmware
  uses the laptop LAN IP, both devices share the same SSID, and
  `Test-NetConnection` succeeds. Guest Wi-Fi commonly blocks peer devices.
- **HTTP 422:** check required IDs, `source_type=HARDWARE`, increasing sequences
  and timestamps, finite readings, and explicit DHT validity/error fields.
- **HTTP 413:** the payload exceeds `PMNODE_MAX_REQUEST_BYTES`. Keep the bounded
  firmware batch at 100 motion samples rather than increasing it.
- **HTTP 409:** the same `(device_id, boot_id, batch_seq)` already exists with
  different content. Use a fresh boot ID after reboot and never reuse a sequence
  for changed data. An identical replay is accepted as a duplicate.
- **MPU6050 startup failure:** power down, recheck 3.3 V, common ground,
  GPIO21/GPIO22, address, and WHO_AM_I. Do not bypass the identity check.
- **DHT11 invalid records:** check GPIO27 and the module pull-up. The sensor is
  intentionally sampled slowly; failed reads are transmitted explicitly.
- **Missed deadlines, queue overflow, or abandoned batches:** inspect cumulative
  serial counters, Wi-Fi signal, and API latency. Lost samples remain counted
  rather than being silently backfilled.
- **`MODEL_UNAVAILABLE`:** ingestion works but no compatible active hardware
  model exists. Collect and review real healthy sessions before training one.

Physical upload, device communication, sensor identity, measured sample rate,
and real machine behavior remain pending until an operator performs this
controlled procedure with the actual hardware.
