"""Frozen Isolation Forest and robust statistical comparison baseline."""

from __future__ import annotations

import platform
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import scipy
import sklearn
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import RobustScaler

from .features import FEATURE_SCHEMA_VERSION, MODEL_FEATURES, FeatureWindow


class ModelCompatibilityError(ValueError):
    pass


@dataclass
class RobustReference:
    center: np.ndarray
    spread: np.ndarray
    threshold: float

    @classmethod
    def fit(cls, train: np.ndarray, calibration: np.ndarray,
            quantile: float = 0.99) -> "RobustReference":
        center = np.median(train, axis=0)
        spread = np.maximum(np.median(np.abs(train - center), axis=0) * 1.4826, 1e-9)
        temporary = cls(center, spread, 0.0)
        threshold = float(np.quantile(temporary.score(calibration), quantile))
        return cls(center, spread, threshold)

    def score(self, matrix: np.ndarray) -> np.ndarray:
        return np.max(np.abs((matrix - self.center) / self.spread), axis=1)


@dataclass
class ModelBundle:
    forest: IsolationForest
    scaler: RobustScaler
    reference: RobustReference
    isolation_threshold: float
    model_version: str
    source_type: str
    training_sessions: list[str]
    calibration_sessions: list[str]
    feature_order: list[str]
    feature_schema_version: str
    metadata: dict

    def score(self, windows: list[FeatureWindow]) -> tuple[np.ndarray, np.ndarray]:
        matrix = feature_matrix(windows, self.source_type)
        scaled = self.scaler.transform(matrix)
        return -self.forest.score_samples(scaled), self.reference.score(matrix)


def feature_matrix(windows: list[FeatureWindow], source_type: str | None = None) -> np.ndarray:
    if not windows:
        raise ValueError("no feature windows supplied")
    if source_type and any(window.source_type.value != source_type for window in windows):
        raise ModelCompatibilityError("window source type is incompatible with model")
    if any(window.feature_schema_version != FEATURE_SCHEMA_VERSION for window in windows):
        raise ModelCompatibilityError("feature schema version is incompatible")
    return np.asarray([window.model_vector() for window in windows], dtype=float)


def validate_session_splits(training_sessions: list[str], calibration_sessions: list[str],
                            test_sessions: list[str] | None = None) -> None:
    groups = [set(training_sessions), set(calibration_sessions), set(test_sessions or [])]
    if not groups[0] or not groups[1]:
        raise ValueError("explicit training and calibration sessions are required")
    if groups[0] & groups[1] or groups[0] & groups[2] or groups[1] & groups[2]:
        raise ValueError("training, calibration, and test sessions must not overlap")


def train_model(training_windows: list[FeatureWindow], calibration_windows: list[FeatureWindow],
                *, training_sessions: list[str], calibration_sessions: list[str],
                source_type: str, model_version: str = "model-1",
                random_state: int = 42, n_estimators: int = 64,
                threshold_quantile: float = 0.99,
                minimum_training_windows: int = 20,
                minimum_calibration_windows: int = 5) -> ModelBundle:
    validate_session_splits(training_sessions, calibration_sessions)
    if len(training_windows) < minimum_training_windows:
        raise ValueError("insufficient healthy training windows")
    if len(calibration_windows) < minimum_calibration_windows:
        raise ValueError("insufficient healthy calibration windows")
    train = feature_matrix(training_windows, source_type)
    calibration = feature_matrix(calibration_windows, source_type)
    scaler = RobustScaler().fit(train)
    forest = IsolationForest(
        n_estimators=n_estimators, max_samples=min(256, len(train)),
        contamination="auto", random_state=random_state,
    ).fit(scaler.transform(train))
    isolation_scores = -forest.score_samples(scaler.transform(calibration))
    isolation_threshold = float(np.quantile(isolation_scores, threshold_quantile))
    reference = RobustReference.fit(train, calibration, threshold_quantile)
    metadata = {
        "declaration": "SYNTHETIC SOFTWARE VALIDATION ONLY" if source_type == "SIMULATED" else "HARDWARE_SOURCE",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "random_state": random_state, "n_estimators": n_estimators,
        "max_samples": min(256, len(train)), "threshold_quantile": threshold_quantile,
        "python": platform.python_version(), "numpy": np.__version__,
        "scipy": scipy.__version__, "scikit_learn": sklearn.__version__,
        "dataset_provenance": {"training_sessions": training_sessions,
                               "calibration_sessions": calibration_sessions},
    }
    return ModelBundle(forest, scaler, reference, isolation_threshold,
                       model_version, source_type, training_sessions,
                       calibration_sessions, list(MODEL_FEATURES),
                       FEATURE_SCHEMA_VERSION, metadata)


def save_model(bundle: ModelBundle, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, path)
    return path


def load_model(path: str | Path) -> ModelBundle:
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    bundle = joblib.load(path)
    if not isinstance(bundle, ModelBundle):
        raise ModelCompatibilityError("artifact is not a PMNode ModelBundle")
    if bundle.feature_schema_version != FEATURE_SCHEMA_VERSION or bundle.feature_order != MODEL_FEATURES:
        raise ModelCompatibilityError("artifact feature schema/order is incompatible")
    return bundle
