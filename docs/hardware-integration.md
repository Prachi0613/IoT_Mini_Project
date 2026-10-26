# Hardware Integration Preparation Guide

This guide describes the next physical-integration phase. None of these steps
has been performed or verified during Task 04.

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

## B. Initial bring-up sequence

1. Inspect the modules, pin labels, wiring, supply voltage, and common ground.
2. Identify the ESP32 serial port in Device Manager or `pio device list`.
3. Copy `firmware/include/config.example.h` to `config.h`; configure local Wi-Fi,
   the laptop's LAN IP (not `localhost`), backend port, and device ID.
4. Start FastAPI on a LAN-reachable interface only for the controlled test, with
   an appropriate firewall rule. Do not expose the unauthenticated API publicly.
5. Build with `pio run`, then upload explicitly with `pio run --target upload`.
6. Inspect startup output using `pio device monitor`; do not enter a reboot loop.
7. Confirm the reported MPU6050 address and WHO_AM_I identity.
8. Confirm plausible raw acceleration and gyroscope readings while stationary.
9. Confirm DHT11 ambient temperature/humidity and explicit failed-read records.
10. Inspect monotonic timestamp intervals and all cumulative quality counters.
11. Capture and validate real HTTP payloads and acknowledgement identities.
12. Query SQLite and confirm `source_type=HARDWARE`, the new boot ID, original
    timestamps, and raw sample counts. Confirm there are no synthetic scores.

## C. Sampling validation

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

## D. Physical mounting and safety

- Attach the MPU6050 rigidly to the motor/fan housing or repeatable mounting
  structure; loose mounting measures cable and board motion.
- Record and preserve sensor-axis orientation across experiments.
- Secure wires away from fan blades, shafts, and other moving components.
- Guard exposed rotating parts and keep hands, clothing, and cables clear.
- Keep supply and operating conditions repeatable.
- Do not create bearing damage, imbalance, obstruction, electrical faults, or
  other dangerous fault injection merely to obtain anomaly data.

## E. Real-data experiments

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

## F. Hardware model preparation

After sufficient genuine data exist, inspect acquisition quality and exclude or
retain sessions by an explicit documented rule. Extract features with the
existing pipeline. Select separate `HARDWARE` healthy training and calibration
sessions, train a hardware-source model, validate held-out sessions, and only
then explicitly activate the approved model. Evaluate anomaly behavior against
actual experimental observations. Never relabel or promote the synthetic model
as a hardware model, and keep automatic reference adaptation disabled until
guarded healthy evidence is reviewed and approved.
