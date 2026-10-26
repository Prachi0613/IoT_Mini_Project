"""Generate or verify deterministic JSON schemas from Pydantic models."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pmnode.contracts import (
    AcquisitionQuality,
    EnvironmentSample,
    IngestAcknowledgement,
    MotionSample,
    TelemetryBatch,
)
from pmnode.ml.features import FeatureWindow

MODELS = {
    "acquisition_quality.schema.json": AcquisitionQuality,
    "environment_sample.schema.json": EnvironmentSample,
    "ingest_acknowledgement.schema.json": IngestAcknowledgement,
    "motion_sample.schema.json": MotionSample,
    "telemetry_batch.schema.json": TelemetryBatch,
    "feature_window.schema.json": FeatureWindow,
}


def rendered_schemas() -> dict[str, str]:
    return {
        name: json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
        for name, model in MODELS.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=Path("contracts"))
    args = parser.parse_args()
    rendered = rendered_schemas()
    if args.check:
        stale = [name for name, content in rendered.items()
                 if not (args.output_dir / name).is_file()
                 or (args.output_dir / name).read_text(encoding="utf-8") != content]
        if stale:
            print("Stale or missing schemas: " + ", ".join(stale), file=sys.stderr)
            return 1
        print(f"{len(rendered)} schemas are up to date")
        return 0
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, content in rendered.items():
        (args.output_dir / name).write_text(content, encoding="utf-8")
    print(f"Wrote {len(rendered)} schemas to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
