"""Export one stored session without filling missing samples."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path

from pmnode.storage import SessionNotFoundError, Storage

MOTION_COLUMNS = ["source_type", "sample_seq", "device_monotonic_time_us",
                  "ax_g", "ay_g", "az_g", "gx_dps", "gy_dps", "gz_dps",
                  "backend_received_at_utc"]
ENV_COLUMNS = ["source_type", "env_seq", "device_monotonic_time_us",
               "ambient_temperature_c", "ambient_humidity_pct", "valid",
               "error_code", "backend_received_at_utc"]


def export_session(database: str | Path, device_id: str, boot_id: str,
                   output_dir: str | Path, selection: str = "both") -> list[Path]:
    storage = Storage(database)
    session = storage.require_session(device_id, boot_id)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{device_id}_{boot_id}"
    written: list[Path] = []
    specs = []
    if selection in ("motion", "both"):
        specs.append(("motion", MOTION_COLUMNS))
    if selection in ("environment", "both"):
        specs.append(("environment", ENV_COLUMNS))
    for sensor, columns in specs:
        rows = storage.telemetry(device_id, boot_id, sensor, 1_000_000, 0)
        path = output_dir / f"{prefix}_{sensor}.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                record = {"source_type": session["source_type"]}
                if sensor == "motion":
                    record.update({"sample_seq": row["sample_seq"],
                                   "device_monotonic_time_us": row["t_mono_us"],
                                   "ax_g": row["ax_g"], "ay_g": row["ay_g"],
                                   "az_g": row["az_g"], "gx_dps": row["gx_dps"],
                                   "gy_dps": row["gy_dps"], "gz_dps": row["gz_dps"],
                                   "backend_received_at_utc": row["received_at_utc"]})
                else:
                    record.update({"env_seq": row["env_seq"],
                                   "device_monotonic_time_us": row["t_mono_us"],
                                   "ambient_temperature_c": row["ambient_temperature_c"],
                                   "ambient_humidity_pct": row["ambient_humidity_pct"],
                                   "valid": row["valid"], "error_code": row["error_code"],
                                   "backend_received_at_utc": row["received_at_utc"]})
                writer.writerow(record)
        written.append(path)
    metadata_path = output_dir / f"{prefix}_metadata.json"
    metadata_path.write_text(json.dumps({"session": session,
                                         "batches": storage.batch_metadata(device_id, boot_id)},
                                        indent=2, sort_keys=True) + "\n", encoding="utf-8")
    written.append(metadata_path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--device", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--select", choices=("motion", "environment", "both"), default="both")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        paths = export_session(args.database, args.device, args.session,
                               args.output_dir, args.select)
    except (SessionNotFoundError, sqlite3.Error, OSError) as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        return 2
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
