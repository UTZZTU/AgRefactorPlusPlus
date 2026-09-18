"""Deterministic R5 pattern lifecycle reducer.

Lifecycle changes are derived from immutable episode envelopes.  No provider
output participates in a transition, and a parent revision is never edited;
the reducer emits a new content-addressed revision instead.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Any

from .episode_ledger import R5EpisodeEnvelope, R5EpisodeOutcome, canonical_sha256


class Lifecycle(str, Enum):
    QUARANTINED = "Quarantined"
    PROVISIONAL = "Provisional"
    TRUSTED = "Trusted"
    DEPRECATED = "Deprecated"


@dataclass(frozen=True, slots=True)
class R5LifecyclePolicy:
    policy_id: str = "r5-lifecycle-v1"
    provisional_min_positive: int = 1
    provisional_min_sources: int = 1
    provisional_min_contexts: int = 1
    trusted_min_positive: int = 2
    trusted_min_sources: int = 2
    trusted_min_contexts: int = 2
    trusted_max_negative_rate: float = 0.20
    quarantine_negative_window: int = 5
    quarantine_negative_count: int = 2
    quarantine_rate: float = 0.25
    quarantine_min_applications: int = 3
    deprecate_min_post_quarantine: int = 3
    deprecate_min_sources: int = 2
    deprecate_rate: float = 0.40

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "provisional_min_positive": self.provisional_min_positive,
            "provisional_min_sources": self.provisional_min_sources,
            "provisional_min_contexts": self.provisional_min_contexts,
            "trusted_min_positive": self.trusted_min_positive,
            "trusted_min_sources": self.trusted_min_sources,
            "trusted_min_contexts": self.trusted_min_contexts,
            "trusted_max_negative_rate": self.trusted_max_negative_rate,
            "quarantine_negative_window": self.quarantine_negative_window,
            "quarantine_negative_count": self.quarantine_negative_count,
            "quarantine_rate": self.quarantine_rate,
            "quarantine_min_applications": self.quarantine_min_applications,
            "deprecate_min_post_quarantine": self.deprecate_min_post_quarantine,
            "deprecate_min_sources": self.deprecate_min_sources,
            "deprecate_rate": self.deprecate_rate,
        }

    @property
    def policy_sha256(self) -> str:
        return canonical_sha256(self.to_dict())


@dataclass(frozen=True, slots=True)
class R5PatternRevision:
    revision_id: str
    parent_revision_id: str | None
    failure_family: str
    stage: str
    owner: str
    supported_when: Mapping[str, Any]
    avoid_when: Mapping[str, Any]
    exact_exclusions: Mapping[str, Any]
    required_evidence: tuple[str, ...]
    positive_episode_refs: tuple[str, ...]
    negative_episode_refs: tuple[str, ...]
    calibration_refs: tuple[str, ...]
    memory_payload_manifest_sha256: str
    lifecycle: Lifecycle
    transition_reason: str
    threshold_source: str
    created_at: str
    revision_sha256: str = ""

    def __post_init__(self) -> None:
        for name in ("revision_id", "failure_family", "stage", "owner", "transition_reason", "threshold_source", "created_at"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if self.parent_revision_id is not None and not self.parent_revision_id.strip():
            raise ValueError("parent_revision_id must be non-empty when present")
        if not isinstance(self.lifecycle, Lifecycle):
            object.__setattr__(self, "lifecycle", Lifecycle(self.lifecycle))
        for name in ("required_evidence", "positive_episode_refs", "negative_episode_refs", "calibration_refs"):
            value = tuple(getattr(self, name))
            if len(value) != len(set(value)) or any(not isinstance(item, str) or not item.strip() for item in value):
                raise ValueError(f"{name} must contain unique non-empty values")
            object.__setattr__(self, name, value)
        for name in ("supported_when", "avoid_when", "exact_exclusions"):
            if not isinstance(getattr(self, name), Mapping):
                raise TypeError(f"{name} must be a mapping")
        if len(self.memory_payload_manifest_sha256) != 64:
            raise ValueError("memory_payload_manifest_sha256 must be a SHA-256 digest")
        expected = canonical_sha256(self.to_dict(include_hash=False))
        if self.revision_sha256 and self.revision_sha256 != expected:
            raise ValueError("revision_sha256 mismatch")
        object.__setattr__(self, "revision_sha256", expected)

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "schema_version": 1,
            "revision_id": self.revision_id,
            "parent_revision_id": self.parent_revision_id,
            "failure_family": self.failure_family,
            "stage": self.stage,
            "owner": self.owner,
            "supported_when": dict(self.supported_when),
            "avoid_when": dict(self.avoid_when),
            "exact_exclusions": dict(self.exact_exclusions),
            "required_evidence": list(self.required_evidence),
            "positive_episode_refs": list(self.positive_episode_refs),
            "negative_episode_refs": list(self.negative_episode_refs),
            "calibration_refs": list(self.calibration_refs),
            "memory_payload_manifest_sha256": self.memory_payload_manifest_sha256,
            "lifecycle": self.lifecycle.value,
            "transition_reason": self.transition_reason,
            "threshold_source": self.threshold_source,
            "created_at": self.created_at,
        }
        if include_hash:
            value["revision_sha256"] = self.revision_sha256
        return value


@dataclass(frozen=True, slots=True)
class LifecycleReduction:
    revision: R5PatternRevision
    eligible_episode_ids: tuple[str, ...]
    rejected_episode_ids: tuple[str, ...]
    positive_count: int
    negative_count: int
    independent_sources: int
    independent_contexts: int
    negative_rate: float
    false_or_unsafe_count: int


def _episode_matches(episode: R5EpisodeEnvelope, *, family: str, stage: str, owner: str) -> bool:
    summary = episode.agent_safe_summary
    payload = episode.payload
    values = {str(summary.get("failure_family", payload.get("failure_family", ""))), str(summary.get("stage", payload.get("stage", ""))), str(summary.get("owner", payload.get("owner", "")))}
    return family in values or stage in values or owner in values


class R5LifecycleReducer:
    """Reduce an append-only history partition to one immutable revision."""

    def __init__(self, policy: R5LifecyclePolicy | None = None):
        self.policy = policy or R5LifecyclePolicy()

    def reduce(
        self,
        episodes: Sequence[R5EpisodeEnvelope],
        *,
        revision_id: str,
        failure_family: str,
        stage: str,
        owner: str,
        supported_when: Mapping[str, Any],
        avoid_when: Mapping[str, Any],
        exact_exclusions: Mapping[str, Any],
        required_evidence: Sequence[str],
        calibration_refs: Sequence[str],
        memory_payload_manifest_sha256: str,
        created_at: str,
        parent: R5PatternRevision | None = None,
    ) -> LifecycleReduction:
        matching = [item for item in episodes if _episode_matches(item, family=failure_family, stage=stage, owner=owner)]
        eligible = [item for item in matching if item.eligible_for_reduction]
        rejected = [item for item in matching if not item.eligible_for_reduction]
        positives = [item for item in eligible if item.outcome is R5EpisodeOutcome.VERIFIED_POSITIVE]
        negatives = [item for item in eligible if item.outcome is R5EpisodeOutcome.VERIFIED_NEGATIVE]
        source_count = len({item.source_sha256 for item in eligible})
        context_count = len({item.context_signature for item in eligible})
        applications = len(eligible)
        negative_rate = len(negatives) / applications if applications else 0.0
        unsafe_count = sum(1 for item in eligible if item.agent_safe_summary.get("unsafe_scope") is True or item.agent_safe_summary.get("false_repair") is True)
        critical = any(item.agent_safe_summary.get("critical_safety_violation") is True for item in matching)
        recent = eligible[-self.policy.quarantine_negative_window:]
        recent_negatives = sum(item.outcome is R5EpisodeOutcome.VERIFIED_NEGATIVE for item in recent)
        post_quarantine = sum(1 for item in eligible if item.agent_safe_summary.get("after_quarantine") is True)
        post_sources = len({item.source_sha256 for item in eligible if item.agent_safe_summary.get("after_quarantine") is True})
        lifecycle = Lifecycle.QUARANTINED
        reason = "insufficient_verified_support"
        if post_quarantine >= self.policy.deprecate_min_post_quarantine and post_sources >= self.policy.deprecate_min_sources and negative_rate > self.policy.deprecate_rate:
            lifecycle, reason = Lifecycle.DEPRECATED, "post_quarantine_negative_threshold"
        elif critical or recent_negatives >= self.policy.quarantine_negative_count or (applications >= self.policy.quarantine_min_applications and negative_rate > self.policy.quarantine_rate):
            lifecycle, reason = Lifecycle.QUARANTINED, "critical_or_negative_transfer_threshold"
        elif (len(positives) >= self.policy.trusted_min_positive and source_count >= self.policy.trusted_min_sources and context_count >= self.policy.trusted_min_contexts and bool(calibration_refs) and negative_rate <= self.policy.trusted_max_negative_rate and unsafe_count == 0):
            lifecycle, reason = Lifecycle.TRUSTED, "trusted_thresholds_met"
        elif len(positives) >= self.policy.provisional_min_positive and source_count >= self.policy.provisional_min_sources and context_count >= self.policy.provisional_min_contexts and unsafe_count == 0:
            lifecycle, reason = Lifecycle.PROVISIONAL, "provisional_thresholds_met"
        revision = R5PatternRevision(
            revision_id=revision_id,
            parent_revision_id=None if parent is None else parent.revision_id,
            failure_family=failure_family,
            stage=stage,
            owner=owner,
            supported_when=supported_when,
            avoid_when=avoid_when,
            exact_exclusions=exact_exclusions,
            required_evidence=tuple(required_evidence),
            positive_episode_refs=tuple(item.episode_id for item in positives),
            negative_episode_refs=tuple(item.episode_id for item in negatives),
            calibration_refs=tuple(calibration_refs),
            memory_payload_manifest_sha256=memory_payload_manifest_sha256,
            lifecycle=lifecycle,
            transition_reason=reason,
            threshold_source=self.policy.policy_id,
            created_at=created_at,
        )
        return LifecycleReduction(revision, tuple(item.episode_id for item in eligible), tuple(item.episode_id for item in rejected), len(positives), len(negatives), source_count, context_count, negative_rate, unsafe_count)


__all__ = ["Lifecycle", "LifecycleReduction", "R5LifecyclePolicy", "R5LifecycleReducer", "R5PatternRevision"]
