"""Append-only, agent-safe R3 episode memory and shadow applicability gate.

This module is deliberately adjacent to the existing recovery/advisory code.
It records evidence and computes an auditable shadow decision; it never edits
source, calls a provider, changes the validation FSM, or authorizes repair.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN = frozenset({
    "hidden", "secret", "oracle", "private_reasoning", "raw_provider_response",
    "raw_exception", "future_outcome", "hidden_path", "source_content",
})
_SAFE_META_KEYS = frozenset({
    "identity_complete", "hidden_input_count", "secret_present", "private_reasoning_present",
    "evidence_predicates", "avoid_when_match", "conflict", "ood", "sparse", "calibrated_risk_ok",
})
_DECISIONS = frozenset({"accept", "reject", "abstain"})


class EpisodeOutcome(str, Enum):
    VERIFIED_POSITIVE = "verified_positive"
    VERIFIED_NEGATIVE = "verified_negative"
    ABSTAINED = "abstained"
    INCONCLUSIVE = "inconclusive"
    INVALID_EVIDENCE = "invalid_evidence"


class PatternLifecycle(str, Enum):
    QUARANTINED = "Quarantined"
    PROVISIONAL = "Provisional"
    TRUSTED = "Trusted"
    DEPRECATED = "Deprecated"
    REJECTED = "Rejected"


class GateDecision(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    ABSTAIN = "abstain"


class MemoryContractError(ValueError):
    """Raised when an R3 record violates its typed or firewall contract."""


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MemoryContractError(f"{name} must be non-empty text")
    return value.strip()


def _digest(value: Any, name: str) -> str:
    value = _text(value, name).lower()
    if not _SHA256.fullmatch(value):
        raise MemoryContractError(f"{name} must be a SHA-256 digest")
    return value


def _freeze(value: Any, *, path: str = "root") -> Any:
    if isinstance(value, Mapping):
        items = {}
        for key, item in value.items():
            key_text = _text(key, f"{path}.key").casefold()
            if key_text not in _SAFE_META_KEYS and any(token in key_text for token in _FORBIDDEN):
                raise MemoryContractError(f"forbidden feature: {path}.{key}")
            items[key_text] = _freeze(item, path=f"{path}.{key_text}")
        return MappingProxyType(items)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item, path=f"{path}[]") for item in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise MemoryContractError(f"unsupported value at {path}")


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(_plain(value), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _ids(values: Sequence[str], name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise MemoryContractError(f"{name} must be a sequence")
    result = tuple(_text(v, name) for v in values)
    if len(result) != len(set(result)):
        raise MemoryContractError(f"{name} must be unique")
    return result


@dataclass(frozen=True, slots=True)
class GateResult:
    decision: GateDecision
    reasons: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    checked_order: tuple[str, ...]
    contract_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision", GateDecision(self.decision))
        object.__setattr__(self, "reasons", _ids(self.reasons, "reasons"))
        object.__setattr__(self, "evidence_refs", _ids(self.evidence_refs, "evidence_refs"))
        object.__setattr__(self, "checked_order", _ids(self.checked_order, "checked_order"))
        object.__setattr__(self, "contract_hash", _digest(self.contract_hash, "contract_hash"))

    def to_dict(self) -> dict[str, Any]:
        return {"decision": self.decision.value, "reasons": list(self.reasons), "evidence_refs": list(self.evidence_refs), "checked_order": list(self.checked_order), "contract_hash": self.contract_hash}


@dataclass(frozen=True, slots=True)
class DiagnosticEpisode:
    episode_id: str
    created_at: str
    parent_episode_id: str | None
    lineage: tuple[str, ...]
    event_ref: str
    execution_identity: Mapping[str, Any]
    request: Mapping[str, Any]
    context_signature: str
    deterministic_diagnosis: Mapping[str, Any]
    advisory: Mapping[str, Any]
    retrieved_revision_ids: tuple[str, ...]
    gate: GateResult
    repair_authorization: str
    before_hash: str
    after_hash: str | None
    full_revalidation_ref: str | None
    budget_delta: Mapping[str, Any]
    outcome: EpisodeOutcome
    outcome_refs: tuple[str, ...]
    writer_version: str = "r3-v1"
    episode_hash: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "episode_id", _text(self.episode_id, "episode_id"))
        object.__setattr__(self, "created_at", _text(self.created_at, "created_at"))
        object.__setattr__(self, "event_ref", _text(self.event_ref, "event_ref"))
        object.__setattr__(self, "context_signature", _digest(self.context_signature, "context_signature"))
        object.__setattr__(self, "before_hash", _digest(self.before_hash, "before_hash"))
        if self.after_hash is not None: object.__setattr__(self, "after_hash", _digest(self.after_hash, "after_hash"))
        if self.full_revalidation_ref is not None: object.__setattr__(self, "full_revalidation_ref", _text(self.full_revalidation_ref, "full_revalidation_ref"))
        if self.parent_episode_id is not None: object.__setattr__(self, "parent_episode_id", _text(self.parent_episode_id, "parent_episode_id"))
        object.__setattr__(self, "lineage", _ids(self.lineage, "lineage"))
        object.__setattr__(self, "retrieved_revision_ids", _ids(self.retrieved_revision_ids, "retrieved_revision_ids"))
        object.__setattr__(self, "outcome_refs", _ids(self.outcome_refs, "outcome_refs"))
        object.__setattr__(self, "outcome", EpisodeOutcome(self.outcome))
        object.__setattr__(self, "execution_identity", _freeze(self.execution_identity, path="execution_identity"))
        object.__setattr__(self, "request", _freeze(self.request, path="request"))
        object.__setattr__(self, "deterministic_diagnosis", _freeze(self.deterministic_diagnosis, path="deterministic_diagnosis"))
        object.__setattr__(self, "advisory", _freeze(self.advisory, path="advisory"))
        object.__setattr__(self, "budget_delta", _freeze(self.budget_delta, path="budget_delta"))
        if self.repair_authorization != "not_requested": raise MemoryContractError("R3 repair authorization must be not_requested")
        if self.after_hash is not None: raise MemoryContractError("R3 shadow episode cannot contain an after hash")
        if self.episode_hash:
            expected = self._compute_hash()
            if self.episode_hash != expected: raise MemoryContractError("episode_hash mismatch")
        else: object.__setattr__(self, "episode_hash", self._compute_hash())

    def _compute_hash(self) -> str:
        payload = self.to_dict(include_hash=False)
        return hashlib.sha256(_canonical(payload)).hexdigest()

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {"schema_version": 1, "episode_id": self.episode_id, "created_at": self.created_at, "parent_episode_id": self.parent_episode_id, "lineage": list(self.lineage), "event_ref": self.event_ref, "execution_identity": _plain(self.execution_identity), "request": _plain(self.request), "context_signature": self.context_signature, "deterministic_diagnosis": _plain(self.deterministic_diagnosis), "advisory": _plain(self.advisory), "retrieved_revision_ids": list(self.retrieved_revision_ids), "gate": self.gate.to_dict(), "repair_authorization": self.repair_authorization, "before_hash": self.before_hash, "after_hash": self.after_hash, "full_revalidation_ref": self.full_revalidation_ref, "budget_delta": _plain(self.budget_delta), "outcome": self.outcome.value, "outcome_refs": list(self.outcome_refs), "writer_version": self.writer_version}
        if include_hash: value["episode_hash"] = self.episode_hash
        return value


@dataclass(frozen=True, slots=True)
class RepairPatternRevision:
    revision_id: str
    parent_revision_id: str | None
    supported_when: Mapping[str, Any]
    avoid_when: Mapping[str, Any]
    exclusions: Mapping[str, Any]
    required_evidence: tuple[str, ...]
    positive_episode_refs: tuple[str, ...]
    negative_episode_refs: tuple[str, ...]
    calibration_refs: tuple[str, ...]
    lifecycle: PatternLifecycle = PatternLifecycle.QUARANTINED
    revision_hash: str = ""
    threshold_source: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "revision_id", _text(self.revision_id, "revision_id"))
        if self.parent_revision_id is not None: object.__setattr__(self, "parent_revision_id", _text(self.parent_revision_id, "parent_revision_id"))
        object.__setattr__(self, "lifecycle", PatternLifecycle(self.lifecycle))
        object.__setattr__(self, "required_evidence", _ids(self.required_evidence, "required_evidence"))
        object.__setattr__(self, "positive_episode_refs", _ids(self.positive_episode_refs, "positive_episode_refs"))
        object.__setattr__(self, "negative_episode_refs", _ids(self.negative_episode_refs, "negative_episode_refs"))
        object.__setattr__(self, "calibration_refs", _ids(self.calibration_refs, "calibration_refs"))
        object.__setattr__(self, "supported_when", _freeze(self.supported_when, path="supported_when")); object.__setattr__(self, "avoid_when", _freeze(self.avoid_when, path="avoid_when")); object.__setattr__(self, "exclusions", _freeze(self.exclusions, path="exclusions"))
        if self.lifecycle is PatternLifecycle.TRUSTED and not self.threshold_source: raise MemoryContractError("Trusted revision requires threshold_source")
        expected = self._compute_hash()
        if self.revision_hash and self.revision_hash != expected: raise MemoryContractError("revision_hash mismatch")
        object.__setattr__(self, "revision_hash", expected)

    def _compute_hash(self) -> str:
        return hashlib.sha256(_canonical(self.to_dict(include_hash=False))).hexdigest()

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {"schema_version": 1, "revision_id": self.revision_id, "parent_revision_id": self.parent_revision_id, "supported_when": _plain(self.supported_when), "avoid_when": _plain(self.avoid_when), "exclusions": _plain(self.exclusions), "required_evidence": list(self.required_evidence), "positive_episode_refs": list(self.positive_episode_refs), "negative_episode_refs": list(self.negative_episode_refs), "calibration_refs": list(self.calibration_refs), "lifecycle": self.lifecycle.value, "threshold_source": self.threshold_source}
        if include_hash: value["revision_hash"] = self.revision_hash
        return value


def classify_outcome(*, authorized: bool, full_revalidation: bool, semantic_preserved: bool, identity_complete: bool, auditor_clean: bool, attributable: bool, environment_excluded: bool, invalid_evidence: bool = False) -> EpisodeOutcome:
    if invalid_evidence or not identity_complete: return EpisodeOutcome.INVALID_EVIDENCE
    if not authorized:
        return EpisodeOutcome.ABSTAINED if not full_revalidation else EpisodeOutcome.INCONCLUSIVE
    if full_revalidation and semantic_preserved and auditor_clean: return EpisodeOutcome.VERIFIED_POSITIVE
    if attributable and environment_excluded: return EpisodeOutcome.VERIFIED_NEGATIVE
    return EpisodeOutcome.INCONCLUSIVE


class EpisodeStore:
    """Small append-only store; no update/delete operation is exposed."""
    def __init__(self) -> None:
        self._episodes: dict[str, DiagnosticEpisode] = {}

    def append(self, episode: DiagnosticEpisode) -> None:
        if not isinstance(episode, DiagnosticEpisode): raise TypeError("episode must be DiagnosticEpisode")
        if episode.episode_id in self._episodes: raise MemoryContractError("episode_id already exists")
        self._episodes[episode.episode_id] = episode

    def get(self, episode_id: str) -> DiagnosticEpisode | None:
        return self._episodes.get(episode_id)

    def snapshot(self) -> tuple[DiagnosticEpisode, ...]:
        return tuple(self._episodes.values())


class ApplicabilityGate:
    """Deterministic ordered gate. Its accept is shadow-only, never authority."""
    ORDER = ("identity_hard_reject", "role_stage_scope", "exact_exclusions", "evidence_completeness", "avoid_when", "support", "conflict_sparsity_ood", "calibrated_risk", "decision_reasons_refs")

    def evaluate(self, *, context: Mapping[str, Any], revision: RepairPatternRevision, evidence_refs: Sequence[str]) -> GateResult:
        if not isinstance(revision, RepairPatternRevision): raise TypeError("revision must be RepairPatternRevision")
        _freeze(context, path="context")
        refs = _ids(evidence_refs, "evidence_refs")
        reasons: list[str] = []
        decision = GateDecision.ACCEPT
        if context.get("identity_complete") is not True or context.get("hidden_input_count", 0) != 0 or context.get("secret_present", False):
            decision, reasons = GateDecision.REJECT, ["identity_or_visibility_incomplete"]
        elif revision.lifecycle in {PatternLifecycle.REJECTED, PatternLifecycle.DEPRECATED}:
            decision, reasons = GateDecision.REJECT, ["revision_not_active"]
        elif revision.lifecycle is PatternLifecycle.QUARANTINED:
            decision, reasons = GateDecision.ABSTAIN, ["revision_quarantined"]
        elif not refs or any(req not in context.get("evidence_predicates", ()) for req in revision.required_evidence):
            decision, reasons = GateDecision.ABSTAIN, ["evidence_incomplete"]
        elif context.get("avoid_when_match") is True:
            decision, reasons = GateDecision.REJECT, ["avoid_when"]
        elif context.get("conflict") is True or context.get("ood") is True or context.get("sparse") is True:
            decision, reasons = GateDecision.ABSTAIN, ["conflict_sparsity_or_ood"]
        elif context.get("calibrated_risk_ok") is not True:
            decision, reasons = GateDecision.ABSTAIN, ["calibration_threshold_unmet"]
        result_payload = {"decision": decision.value, "reasons": reasons, "evidence_refs": list(refs), "checked_order": list(self.ORDER)}
        contract_hash = hashlib.sha256(_canonical(result_payload)).hexdigest()
        return GateResult(decision, tuple(reasons), refs, self.ORDER, contract_hash)
