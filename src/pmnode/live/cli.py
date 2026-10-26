"""Explicit single-worker command for incremental ML processing."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from pmnode.storage import Storage

from .processor import LiveProcessor, LiveProcessorConfig, WorkerLeaseError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", nargs="?")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--idle-flush-seconds", type=float, default=3.0)
    parser.add_argument("--allowed-lateness-seconds", type=float, default=0.25)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.poll_seconds <= 0:
        parser.error("poll interval must be positive")
    processor = LiveProcessor(
        Storage(args.database),
        LiveProcessorConfig(idle_flush_s=args.idle_flush_seconds,
                            allowed_lateness_s=args.allowed_lateness_seconds),
    )
    try:
        while True:
            print(processor.poll_once(), flush=True)
            if args.once:
                processor.release_lease()
                return 0
            time.sleep(args.poll_seconds)
    except WorkerLeaseError as exc:
        print(f"Worker not started: {exc}")
        return 2
    except KeyboardInterrupt:
        processor.release_lease()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
