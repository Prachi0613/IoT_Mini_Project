"""Small environment-based configuration surface."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_path: Path
    max_motion_samples: int = 1000
    max_environment_samples: int = 20
    max_request_bytes: int = 2_000_000
    max_query_limit: int = 1000
    cors_origins: tuple[str, ...] = ("http://127.0.0.1:5173", "http://localhost:5173")

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            database_path=Path(os.getenv("PMNODE_DATABASE", "data/telemetry.sqlite3")),
            max_motion_samples=int(os.getenv("PMNODE_MAX_MOTION_SAMPLES", "1000")),
            max_environment_samples=int(os.getenv("PMNODE_MAX_ENVIRONMENT_SAMPLES", "20")),
            max_request_bytes=int(os.getenv("PMNODE_MAX_REQUEST_BYTES", "2000000")),
            max_query_limit=int(os.getenv("PMNODE_MAX_QUERY_LIMIT", "1000")),
            cors_origins=tuple(origin.strip() for origin in os.getenv(
                "PMNODE_CORS_ORIGINS",
                "http://127.0.0.1:5173,http://localhost:5173"
            ).split(",") if origin.strip()),
        )


DEFAULT_SETTINGS = Settings.from_environment()
