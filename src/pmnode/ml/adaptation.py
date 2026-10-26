"""Guarded, versioned adaptive reference updates; the forest stays frozen."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .features import FEATURE_SCHEMA_VERSION, FeatureWindow, WindowQuality
from .health import HealthState
from .model import ModelBundle, RobustReference
from .registry import MLRegistry


@dataclass(frozen=True)
class AdaptationPolicy:
    enabled: bool = False
    minimum_candidates: int = 30
    maximum_candidates: int = 100
    learning_rate: float = 0.05
    isolation_margin: float = 0.80
    reference_margin: float = 0.80
    maximum_update_fraction: float = 0.25
    maximum_cumulative_drift: float = 1.0
    minimum_spread: float = 1e-9


@dataclass(frozen=True)
class AdaptationDecision:
    decision: str
    reason: str
    reference_version: int
    eligible_windows: int


class ControlledAdapter:
    def __init__(self, bundle: ModelBundle, registry: MLRegistry,
                 policy: AdaptationPolicy = AdaptationPolicy()):
        self.bundle = bundle
        self.registry = registry
        self.policy = policy
        active = registry.active_reference(bundle.model_version)
        if active:
            self.version = active["reference_version"]
            self.reference = RobustReference(active["center"], active["spread"], active["threshold"])
        else:
            self.version = 1
            self.reference = bundle.reference
            registry.save_reference(bundle.model_version, 1, self.reference.center,
                                    self.reference.spread, self.reference.threshold,
                                    approved=True, active=True, previous_version=None,
                                    reason="initial healthy training reference", eligible_windows=0)
        self.origin_center = self.reference.center.copy()
        self._candidates: list[np.ndarray] = []

    def consider(self, window: FeatureWindow, isolation_score: float,
                 reference_score: float, health_state: HealthState,
                 approve: bool = False) -> AdaptationDecision:
        reason = self._eligibility_reason(window, isolation_score, reference_score,
                                          health_state)
        if reason:
            self._candidates.clear()
            return self._record("REJECTED", reason, window)
        vector = np.asarray(window.model_vector(), dtype=float)
        self._candidates.append(vector)
        self._candidates = self._candidates[-self.policy.maximum_candidates:]
        if len(self._candidates) < self.policy.minimum_candidates:
            return self._record("ACCUMULATING", "minimum healthy duration unmet", window)
        candidate = np.asarray(self._candidates)
        candidate_center = np.median(candidate, axis=0)
        candidate_spread = np.maximum(
            np.median(np.abs(candidate - candidate_center), axis=0) * 1.4826,
            self.policy.minimum_spread,
        )
        proposed_center = ((1 - self.policy.learning_rate) * self.reference.center
                           + self.policy.learning_rate * candidate_center)
        proposed_spread = np.maximum(
            (1 - self.policy.learning_rate) * self.reference.spread
            + self.policy.learning_rate * candidate_spread,
            self.policy.minimum_spread,
        )
        if np.any(np.abs(proposed_center - self.reference.center) >
                  self.policy.maximum_update_fraction * self.reference.spread):
            self._candidates.clear()
            return self._record("REJECTED", "per-update drift limit exceeded", window)
        if np.any(np.abs(proposed_center - self.origin_center) >
                  self.policy.maximum_cumulative_drift * self.reference.spread):
            self._candidates.clear()
            return self._record("REJECTED", "cumulative drift limit exceeded", window)
        if not approve:
            return self._record("CANDIDATE_READY", "explicit approval required", window)
        previous = self.version
        self.version += 1
        self.reference = RobustReference(proposed_center, proposed_spread,
                                         self.reference.threshold)
        self.registry.save_reference(
            self.bundle.model_version, self.version, proposed_center, proposed_spread,
            self.reference.threshold, approved=True, active=True,
            previous_version=previous, reason="approved guarded healthy adaptation",
            eligible_windows=len(self._candidates),
        )
        count = len(self._candidates)
        self._candidates.clear()
        return self._record("ACTIVATED", "approved guarded healthy adaptation", window,
                            eligible=count)

    def _eligibility_reason(self, window, isolation_score, reference_score, health_state):
        if not self.policy.enabled:
            return "adaptation disabled"
        if window.source_type.value != self.bundle.source_type:
            return "source type incompatible"
        if window.feature_schema_version != FEATURE_SCHEMA_VERSION:
            return "feature schema incompatible"
        if window.window_quality != WindowQuality.VALID:
            return "invalid sensor window"
        if health_state != HealthState.NORMAL:
            return "health state is not NORMAL"
        if isolation_score >= self.bundle.isolation_threshold * self.policy.isolation_margin:
            return "isolation score outside healthy safety margin"
        if reference_score >= self.reference.threshold * self.policy.reference_margin:
            return "reference score outside healthy safety margin"
        return None

    def _record(self, decision, reason, window, eligible=None):
        count = len(self._candidates) if eligible is None else eligible
        self.registry.log_adaptation(self.bundle.model_version, self.version,
                                     decision, reason, count, window)
        return AdaptationDecision(decision, reason, self.version, count)

    def rollback(self, target_version: int) -> None:
        restored = self.registry.activate_reference(self.bundle.model_version, target_version)
        self.version = target_version
        self.reference = RobustReference(restored["center"], restored["spread"],
                                         restored["threshold"])
        self._candidates.clear()
        self.registry.log_adaptation(self.bundle.model_version, self.version,
                                     "ROLLBACK", f"restored reference {target_version}", 0)
