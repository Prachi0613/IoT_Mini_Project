"""Storage-driven extraction, training, scoring, and persistence orchestration."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np

from pmnode.contracts import SourceType
from pmnode.storage import Storage

from .adaptation import AdaptationPolicy, ControlledAdapter
from .features import FeatureConfig, FeatureWindow, WindowQuality, extract_feature_windows
from .health import HealthEngine
from .model import ModelBundle, load_model, save_model, train_model
from .registry import MLRegistry

Session = tuple[str, str]


def session_key(session: Session) -> str:
    return f"{session[0]}/{session[1]}"


def extract_session(storage: Storage, session: Session,
                    config: FeatureConfig = FeatureConfig(),
                    recompute: bool = False) -> list[FeatureWindow]:
    device_id, boot_id = session
    metadata = storage.require_session(device_id, boot_id)
    batches = storage.batch_metadata(device_id, boot_id)
    if not batches:
        raise ValueError(f"session contains no ingested batches: {session_key(session)}")
    requested_rates = {row["requested_mpu_hz"] for row in batches}
    if len(requested_rates) != 1:
        raise ValueError("requested sampling rate changes within a session")
    effective_config = replace(config, expected_sampling_hz=float(next(iter(requested_rates))))
    motion = storage.telemetry(device_id, boot_id, "motion", 10_000_000, 0)
    environment = storage.telemetry(device_id, boot_id, "environment", 1_000_000, 0)
    windows = extract_feature_windows(
        motion, environment, device_id=device_id, boot_id=boot_id,
        source_type=SourceType(metadata["source_type"]), config=effective_config,
    )
    MLRegistry(storage).save_features(windows, recompute=recompute)
    return windows


def extract_sessions(storage: Storage, sessions: list[Session],
                     config: FeatureConfig = FeatureConfig(),
                     recompute: bool = False) -> list[FeatureWindow]:
    result: list[FeatureWindow] = []
    for session in sessions:
        result.extend(extract_session(storage, session, config, recompute))
    return result


def train_from_storage(storage: Storage, training_sessions: list[Session],
                       calibration_sessions: list[Session], artifact_path: str | Path,
                       *, model_version: str = "model-1", random_state: int = 42,
                       threshold_quantile: float = 0.99) -> ModelBundle:
    registry = MLRegistry(storage)
    train = registry.load_features(training_sessions)
    calibration = registry.load_features(calibration_sessions)
    source_types = {window.source_type.value for window in train + calibration}
    if len(source_types) != 1:
        raise ValueError("training and calibration must use one source type")
    bundle = train_model(
        train, calibration,
        training_sessions=[session_key(value) for value in training_sessions],
        calibration_sessions=[session_key(value) for value in calibration_sessions],
        source_type=source_types.pop(), model_version=model_version,
        random_state=random_state, threshold_quantile=threshold_quantile,
    )
    path = save_model(bundle, artifact_path)
    registry.register_model(bundle, path)
    ControlledAdapter(bundle, registry)  # Persist immutable reference version 1.
    return bundle


def score_sessions(storage: Storage, sessions: list[Session], model_path: str | Path,
                   persist: bool = True) -> list[dict]:
    bundle = load_model(model_path)
    registry = MLRegistry(storage)
    active = registry.active_reference(bundle.model_version)
    reference_version = active["reference_version"] if active else 1
    if active:
        bundle.reference.center = active["center"]
        bundle.reference.spread = active["spread"]
        bundle.reference.threshold = active["threshold"]
    results: list[dict] = []
    for session in sessions:
        windows = registry.load_features([session], valid_only=False)
        if not windows:
            raise ValueError(f"no features for {session_key(session)}")
        valid_windows = [w for w in windows if w.window_quality == WindowQuality.VALID]
        if valid_windows:
            isolation, reference = bundle.score(valid_windows)
            scores = {w.window_start_us: (float(i), float(r))
                      for w, i, r in zip(valid_windows, isolation, reference)}
        else:
            scores = {}
        engine = HealthEngine(bundle.isolation_threshold)
        for window in windows:
            pair = scores.get(window.window_start_us)
            isolation_score, reference_score = pair if pair else (None, None)
            state = engine.update(window.device_id, window.boot_id, window.window_start_us,
                                  isolation_score, pair is not None)
            result = {
                "window": window, "isolation_score": isolation_score,
                "reference_score": reference_score, "health_state": state.value,
                "reference_version": reference_version,
            }
            results.append(result)
            if persist and pair is not None:
                registry.save_prediction(window, bundle, reference_version,
                                         isolation_score, reference_score,
                                         state.value)
            elif persist:
                registry.save_health_decision(window, bundle.model_version,
                                              reference_version, state.value)
    return results
