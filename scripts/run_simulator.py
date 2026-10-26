"""Generate explicitly simulated telemetry offline or send it to the API."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

from pmnode.simulator import Scenario, SensorSimulator, SimulationConfig


def write_offline(run, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        for batch in run.batches:
            stream.write(batch.model_dump_json() + "\n")
    labels_path = output.with_suffix(output.suffix + ".labels.json")
    labels_path.write_text(
        json.dumps({"source_type": "SIMULATED", "boot_id": run.boot_id,
                    "labels": run.labels}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(run.batches)} SIMULATED batches to {output}")
    print(f"Wrote software-test labels to {labels_path}")


def send_live(run, endpoint: str, retries: int, timeout_s: float,
              pace_s: float) -> bool:
    sent = 0
    with httpx.Client(timeout=timeout_s) as client:
        for batch in run.batches:
            success = False
            for attempt in range(retries + 1):
                try:
                    response = client.post(endpoint, content=batch.canonical_payload(),
                                           headers={"content-type": "application/json"})
                    if response.status_code in (200, 201):
                        success = True
                        break
                    print(f"Batch {batch.batch_seq} rejected: HTTP {response.status_code} "
                          f"{response.text}", file=sys.stderr)
                except httpx.HTTPError as exc:
                    print(f"Batch {batch.batch_seq} attempt {attempt + 1} failed: {exc}",
                          file=sys.stderr)
                if attempt < retries:
                    time.sleep(min(0.5 * (attempt + 1), 2.0))
            if not success:
                print(f"Stopped: batch {batch.batch_seq} was not accepted; "
                      "later batches were not discarded or claimed sent", file=sys.stderr)
                return False
            sent += 1
            print(f"Accepted SIMULATED batch {batch.batch_seq} ({sent}/{len(run.batches)})")
            if pace_s > 0 and sent < len(run.batches):
                time.sleep(pace_s)
    print(f"Transmission complete: {sent}/{len(run.batches)} batches accepted")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=[s.value for s in Scenario], default="healthy")
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--sample-rate", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device-id", default="sim-node-01")
    parser.add_argument("--batch-duration", type=float, default=1.0)
    parser.add_argument("--mode", choices=("offline", "live"), default="offline")
    parser.add_argument("--output", type=Path,
                        default=Path("simulation-output/simulated-telemetry.jsonl"))
    parser.add_argument("--endpoint", default="http://127.0.0.1:8000/api/v1/batches")
    parser.add_argument("--retries", type=int, choices=range(0, 6), default=2)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--pace", type=float, default=0.0,
                        help="seconds between batches; use 1 for real-time-like pacing")
    args = parser.parse_args()
    try:
        config = SimulationConfig(
            scenario=Scenario(args.scenario), duration_s=args.duration,
            sample_hz=args.sample_rate, seed=args.seed, device_id=args.device_id,
            batch_duration_s=args.batch_duration,
        )
        run = SensorSimulator(config).generate()
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Generated explicitly SIMULATED session {run.boot_id} ({args.scenario})")
    if args.mode == "offline":
        write_offline(run, args.output)
        return 0
    try:
        return 0 if send_live(run, args.endpoint, args.retries, args.timeout, args.pace) else 2
    except KeyboardInterrupt:
        print("Simulator stopped by user", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
