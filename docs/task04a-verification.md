# Task 04A verification evidence

This record is software-only. No ESP32 was flashed and no sensor or motor was
connected.

## Host and firmware compiler diagnosis

- Host: 64-bit Windows NT 10.0.26300.0 on AMD64.
- Python: 3.13.2.
- PlatformIO Core: 6.2.0.
- Platform: `espressif32@6.9.0`.
- Board: `esp32dev` (Espressif ESP32 Dev Module).
- Framework: Arduino ESP32 3.20017.241212 / IDF 4.4.7.
- Toolchain package: `toolchain-xtensa-esp32@8.4.0+2021r2-patch5`.
- Compiler:
  `firmware/.pio-core/packages/toolchain-xtensa-esp32/bin/xtensa-esp32-elf-g++.exe`.
- Compiler SHA-256 after isolated reinstall:
  `ADAEE963C4FF7607C44A30EDF2E906BEF99E9BE6E204F1DA5D0608AEBE098C14`.

Direct execution:

```text
xtensa-esp32-elf-g++.exe --version
The system cannot execute the specified program.
COMPILER_EXIT=9020
```

The verbose build reached its first compiler invocation and failed before C++
parsing:

```text
platformio.exe run --project-dir firmware -v -j 1
xtensa-esp32-elf-g++ @...tmpxl4m70ob.tmp
The system cannot execute the specified program.
*** [.pio/build/esp32dev/src/dht_sensor.cpp.o] Error 1
```

Correction attempt 1 reinstalled only the exact pinned package with PlatformIO
`--force`; the same OS process-creation error remained. Correction attempt 2
executed the independently installed Arduino ESP32 3.2.0 compiler at
`AppData/Local/Arduino15/packages/esp32/tools/esp-x32/2411/bin/xtensa-esp32-elf-g++.exe`;
Windows returned the same `Illegal System DLL Relocation` error. WSL was present
but unavailable with `WSL/E_ACCESSDENIED`. No security setting or OS DLL was
changed.

**HOST TOOLCHAIN BLOCKER — FIRMWARE SOURCE NOT BUILD-VERIFIED**

No ELF or BIN artifact was generated.

## DLPF decision

The firmware default is now DLPF configuration 1. The register specification
gives nominal bandwidths of 184 Hz for acceleration and 188 Hz for gyro, with a
1 kHz internal output. `SMPLRT_DIV=1` requests 500 Hz output, whose Nyquist
frequency is 250 Hz. This keeps the 30–100 Hz software band inside the nominal
sensor passband. The wider filter also passes more noise and provides no claim
of brick-wall alias rejection; physical response remains unverified.

## Anomaly diagnostic

The complete per-window trace is in
`experiment-output/task04a-verification/anomaly-diagnostic.json`.

| Session | Valid | Invalid | Scored | Max isolation | Threshold | Above | Health states | Events |
|---|---:|---:|---:|---:|---:|---:|---|---|
| healthy-live | 6 | 0 | 6 | 0.541693 | 0.572721 | 0 | NORMAL 6 | none |
| anomaly-live | 10 | 0 | 10 | 0.595055 | 0.572721 | 3 | NORMAL 10 | none |
| transient-live | 6 | 0 | 6 | 0.596545 | 0.572721 | 1 | NORMAL 6 | none |
| dropout-live | 5 | 1 | 5 | 0.549902 | 0.572721 | 0 | NORMAL 3, SENSOR_ERROR 3 | SENSOR_DATA_ERROR, HEALTH_RECOVERED |

The sustained synthetic anomaly was injected from 4 seconds onward. Its RMS
rose to 0.082345 g versus a 0.018194 g healthy maximum, and high-band energy
rose to 5.8056e-05 g² versus 7.9071e-06 g². Isolation scores crossed threshold
at windows starting 4, 7, and 9 seconds. The warning policy requires three
anomalies within the latest five windows; the maximum rolling count was two.
The absence of WARNING is therefore the configured temporal policy, not a
scoring direction, model compatibility, persistence, or injection defect.

The robust-reference comparison also reacted strongly (anomaly maximum
1831.42 against threshold 2.40229). Health classification intentionally uses
the frozen Isolation Forest threshold; the reference score is retained as a
separate comparison and controlled-adaptation guard.

## Final regression

```text
python -m pytest -q
100 passed in 11.66s

python scripts/export_schemas.py --check
6 schemas are up to date

python -m compileall -q src scripts
success (no output)

npm test -- --run
1 test file passed; 8 tests passed

npm run build
619 modules transformed; build completed in 3.78s
```

The frontend build retains a non-fatal warning for the 608.96 kB JavaScript
chunk. Browser smoke testing is not verified in this environment.
