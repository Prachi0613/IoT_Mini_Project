"""
export_schemas.py — Generate JSON Schema files from pmnode Pydantic contracts.

Usage
-----
Generate (or regenerate) all schemas:
    python scripts/export_schemas.py

Verify schemas are up-to-date without writing files:
    python scripts/export_schemas.py --check

Exit codes
----------
0  : Success (or --check passed with no drift).
1  : --check detected missing or stale schemas.
2  : Unexpected runtime error.

Design notes
------------
- Schemas are written to the ``contracts/`` directory at the project root.
- Filenames are derived deterministically from model class names using
  snake_case conversion: ``RunMetadata`` → ``run_metadata.schema.json``.
- Output is deterministically sorted JSON (indent=2, sort_keys=True) to
  produce stable diffs when schemas are regenerated.
- No network access or external service dependency.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve project root so this script can be run from any working directory.
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
_CONTRACTS_DIR = _PROJECT_ROOT / "contracts"

# Add src/ to path so that pmnode is importable without installation.
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from pmnode.contracts import (  # noqa: E402  (after sys.path manipulation)
    AccelerationSample,
    CurrentSample,
    EnvironmentSample,
    FeatureWindow,
    HealthDecision,
    OperatingContext,
    RunMetadata,
)

from pydantic import BaseModel  # noqa: E402

# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------

_MODELS: list[type[BaseModel]] = [
    RunMetadata,
    AccelerationSample,
    CurrentSample,
    EnvironmentSample,
    OperatingContext,
    FeatureWindow,
    HealthDecision,
]


def _to_snake_case(name: str) -> str:
    """Convert a CamelCase class name to snake_case.

    Examples
    --------
    >>> _to_snake_case("RunMetadata")
    'run_metadata'
    >>> _to_snake_case("FeatureWindow")
    'feature_window'
    """
    # Insert underscore before each uppercase letter that follows a lowercase
    # letter or digit, then lowercase the whole string.
    s1 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return s1.lower()


def _schema_filename(model: type[BaseModel]) -> str:
    """Return the deterministic schema filename for a model class."""
    return f"{_to_snake_case(model.__name__)}.schema.json"


def _generate_schema(model: type[BaseModel]) -> str:
    """Return a deterministically formatted JSON Schema string for *model*."""
    raw_schema = model.model_json_schema()
    # sort_keys=True produces a stable, diff-friendly output
    return json.dumps(raw_schema, indent=2, sort_keys=True) + "\n"


def generate_all(*, check: bool = False) -> int:
    """Generate or verify all JSON schemas.

    Parameters
    ----------
    check:
        When True, compare generated schemas against the files on disk
        without modifying any files.  Returns 1 if any schema is missing
        or stale; 0 if all schemas are current.

    Returns
    -------
    int
        Exit code: 0 on success, 1 on check failure.
    """
    if not check:
        _CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)

    all_ok = True

    for model in _MODELS:
        filename = _schema_filename(model)
        target = _CONTRACTS_DIR / filename
        generated = _generate_schema(model)

        if check:
            if not target.exists():
                print(f"  MISSING   {filename}")
                all_ok = False
            else:
                on_disk = target.read_text(encoding="utf-8")
                if on_disk != generated:
                    print(f"  STALE     {filename}")
                    all_ok = False
                else:
                    print(f"  OK        {filename}")
        else:
            target.write_text(generated, encoding="utf-8")
            print(f"  WROTE     {filename}")

    if check:
        if all_ok:
            print("\nAll schemas are up-to-date.")
        else:
            print(
                "\nOne or more schemas are missing or stale.  "
                "Run `python scripts/export_schemas.py` to regenerate."
            )
        return 0 if all_ok else 1

    print(f"\nSchemas written to: {_CONTRACTS_DIR}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate or verify pmnode JSON schemas.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Verify that generated schemas match the current Pydantic source "
            "without writing any files.  Exits with code 1 if schemas are "
            "missing or stale."
        ),
    )
    args = parser.parse_args()

    try:
        exit_code = generate_all(check=args.check)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
