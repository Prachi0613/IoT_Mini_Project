# Task 05 controlled ESP32 bring-up

No step in this checklist has been performed. The current firmware build is
blocked on the available Windows host. **Do not flash until a Linux PlatformIO
build completes and the exact intended board and serial port are identified.**

## Build gate

The optional workflow at `.github/workflows/firmware-build.yml` builds on
GitHub's Ubuntu runner using PlatformIO Core 6.2.0 and the repository's pinned
`espressif32@6.9.0` configuration. It does not run locally or publish anything
by itself. A human must review it, commit/push it to the configured repository,
and manually authorize or open the relevant pull request.

Expected successful outputs are:

- `firmware/.pio/build/esp32dev/firmware.bin`
- `firmware/.pio/build/esp32dev/firmware.elf`
- `firmware/.pio/build/esp32dev/bootloader.bin`
- `firmware/.pio/build/esp32dev/partitions.bin`

A workflow file is not build evidence. Require a successful compile/link log,
reported RAM/flash use, and downloaded artifacts before continuing.

## Wiring checklist

Power must be disconnected while wiring. Verify the labels printed on each
actual module; breakout pin order is not universal.

MPU6050:

- VCC to ESP32 3V3
- GND to ESP32 GND
- SDA to GPIO21
- SCL to GPIO22

DHT11:

- VCC to ESP32 3V3
- GND to ESP32 GND
- DATA to GPIO27

A bare DHT11 may require a pull-up resistor; many modules include one. If its
presence cannot be confirmed, stop and report the limitation. Keep the motor
disconnected/off during sensor bring-up. Never connect motor power to ESP32
GPIO or 3V3. Secure loose wires away from moving parts.

## Operator sequence

1. Obtain and review a successful Linux build log and all required binaries.
2. Connect only the ESP32 over USB.
3. List ports; identify the board from observed USB metadata. Do not guess.
4. Copy `firmware/include/config.example.h` to ignored `config.h` and configure
   the local Wi-Fi SSID, password, laptop address, port, and device ID before
   building the image that will actually be flashed.
5. With explicit operator approval, upload from the project rather than
   inventing raw flash offsets:

   ```powershell
   .\.venv\Scripts\platformio.exe device list
   .\.venv\Scripts\platformio.exe run --project-dir firmware `
     --target upload --upload-port REPLACE_WITH_OBSERVED_PORT
   ```

6. Open the serial monitor:

   ```powershell
   .\.venv\Scripts\platformio.exe device monitor --project-dir firmware `
     --port REPLACE_WITH_OBSERVED_PORT --baud 115200
   ```

7. Confirm startup identity and absence of reboot loops, then power down.
8. Wire only MPU6050 and DHT11 as listed above; keep the motor off.
9. Power up and confirm address/identity, six-axis values, ambient readings,
   quality counters, Wi-Fi, HTTP acknowledgements, and HARDWARE rows in SQLite.

## Acquisition verification

The current firmware polls sensor registers but does not check MPU6050
`DATA_RDY` or FIFO state. Therefore, scheduled 500 Hz reads do not by themselves
prove 500 unique internal sensor updates; duplicated or skipped sensor updates
remain a bring-up risk.

For a short stationary recording, calculate from preserved `t_mono_us` and
`sample_seq` values:

- sample count and sequence gaps;
- mean and median interval/rate;
- interval standard deviation and percentile spread;
- maximum timing gap;
- identical consecutive six-axis frames as a freshness diagnostic;
- I2C errors, missed deadlines, DHT failures, and buffer overruns;
- accepted, rejected, duplicate, and abandoned batches;
- observed acceleration, gyro, ambient temperature, and humidity ranges.

If 500 Hz is unstable, perform one explicitly documented 250 Hz test by
changing `REQUESTED_MPU_HZ`; do not silently change the rate. Freshness should
ultimately be strengthened with a reviewed data-ready interrupt/poll or FIFO
design after initial measurements, not assumed from task scheduling.

## Acceptance criteria

- Linux compile and link succeed with recorded compiler/framework versions.
- BIN and ELF artifacts exist and match the reviewed build.
- Board and serial port are observed, not guessed.
- No uncontrolled reset loop or declining heap/stack watermark is observed.
- MPU6050 identity is correct and six axes vary plausibly without saturation.
- DHT11 updates no faster than two seconds and failures remain null/error-coded.
- Timestamp statistics and freshness evidence support the claimed rate.
- Quality counters remain visible and any loss is reported.
- Backend accepts HARDWARE telemetry without applying a SIMULATED model.
- Dashboard shows HARDWARE provenance and MODEL_UNAVAILABLE/NOT_ANALYZED until
  a separately approved hardware model exists.

Do not activate the motor or begin hardware-model training during this gate.
