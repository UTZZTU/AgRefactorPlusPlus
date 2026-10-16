"""Agent-safe case and experience source records for diagnostic recovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import re
from typing import Any
from collections.abc import Mapping


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_CODE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,159}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ABSOLUTE_PATH = re.compile(
    r"(?:^|\s)(?:/[A-Za-z0-9_.-]+/|[A-Za-z]:\\)",
)


class CaseStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class CaseOutcome(str, Enum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"


class EvidenceCheckState(str, Enum):
    SATISFIED = "satisfied"
    MISSING = "missing"
    CONTRADICTED = "contradicted"


class ExperiencePolarity(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


def _enum(value: Any, enum_type):
    if isinstance(value, enum_type):
        return value
    return enum_type(str(value))


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    cleaned = value.strip()
    if _IDENTIFIER.fullmatch(cleaned) is None:
        raise ValueError(f"{name} must be a bounded stable identifier")
    return cleaned


def _code(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    cleaned = value.strip().casefold()
    if _CODE.fullmatch(cleaned) is None:
        raise ValueError(f"{name} must be a short normalized code")
    return cleaned


def _text(value: object, name: str, *, max_length: int = 512) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    cleaned = value.strip()
    if (
        not cleaned
        or len(cleaned) > max_length
        or any(char in cleaned for char in "\r\n\x00")
        or _ABSOLUTE_PATH.search(cleaned)
        or any(
            tag in cleaned.casefold()
            for tag in ("<think", "</think", "<reasoning", "</reasoning")
        )
    ):
        raise ValueError(f"{name} must be bounded agent-safe text")
    return cleaned


def _refs(values: object, name: str, *, required: bool = False) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{name} must be a sequence of references")
    try:
        result = tuple(_identifier(item, name) for item in values)
    except TypeError as exc:
        raise TypeError(f"{name} must be a sequence of references") from exc
    if required and not result:
        raise ValueError(f"{name} must not be empty")
    if len(result) != len(set(result)):
        raise ValueError(f"{name} must not contain duplicates")
    return result


def _codes(values: object, name: str, *, required: bool = False) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{name} must be a sequence of codes")
    try:
        result = tuple(_code(item, name) for item in values)
    except TypeError as exc:
        raise TypeError(f"{name} must be a sequence of codes") from exc
    if required and not result:
        raise ValueError(f"{name} must not be empty")
    if len(result) != len(set(result)):
        raise ValueError(f"{name} must not contain duplicates")
    return result


def _hash(value: object, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _timestamp(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("created_at must be an ISO-8601 timestamp")
    cleaned = value.strip()
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("created_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("created_at must include a timezone")
    return cleaned


@dataclass(frozen=True, slots=True)
class EvidenceCheckResult:
    check_id: str
    result: EvidenceCheckState | str
    evidence_refs: tuple[str, ...]
    summary: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "check_id", _identifier(self.check_id, "check_id"))
        object.__setattr__(self, "result", _enum(self.result, EvidenceCheckState))
        object.__setattr__(
            self,
            "evidence_refs",
            _refs(self.evidence_refs, "evidence_refs", required=True),
        )
        object.__setattr__(self, "summary", _text(self.summary, "summary"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "result": self.result.value,
            "evidence_refs": list(self.evidence_refs),
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceCheckResult":
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class OwnerAssessment:
    diagnostic_event_ref: str
    owner: str
    candidate_clues: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "diagnostic_event_ref",
            _identifier(self.diagnostic_event_ref, "diagnostic_event_ref"),
        )
        object.__setattr__(self, "owner", _code(self.owner, "owner"))
        object.__setattr__(
            self,
            "candidate_clues",
            _codes(self.candidate_clues, "candidate_clues"),
        )
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs, "evidence_refs"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "diagnostic_event_ref": self.diagnostic_event_ref,
            "owner": self.owner,
            "candidate_clues": list(self.candidate_clues),
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "OwnerAssessment":
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class RepairEligibilitySnapshot:
    decision_ref: str
    eligible: bool
    authorization_ref: str | None
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_ref", _identifier(self.decision_ref, "decision_ref"))
        if not isinstance(self.eligible, bool):
            raise TypeError("eligible must be boolean")
        if self.authorization_ref is not None:
            object.__setattr__(
                self,
                "authorization_ref",
                _identifier(self.authorization_ref, "authorization_ref"),
            )
        object.__setattr__(
            self,
            "evidence_refs",
            _refs(self.evidence_refs, "evidence_refs", required=True),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_ref": self.decision_ref,
            "eligible": self.eligible,
            "authorization_ref": self.authorization_ref,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RepairEligibilitySnapshot":
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class CaseAttempt:
    attempt_id: str
    sequence_index: int
    result: str
    pre_action_event_refs: tuple[str, ...]
    post_validation_event_refs: tuple[str, ...]
    authorization_ref: str
    quota_buckets: tuple[str, ...]
    changed: bool | None = None
    ledger_event_ref: str | None = None
    repair_record_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "attempt_id", _identifier(self.attempt_id, "attempt_id"))
        if (
            isinstance(self.sequence_index, bool)
            or not isinstance(self.sequence_index, int)
            or self.sequence_index < 0
        ):
            raise ValueError("sequence_index must be a non-negative integer")
        object.__setattr__(self, "result", _code(self.result, "result"))
        object.__setattr__(
            self,
            "pre_action_event_refs",
            _refs(self.pre_action_event_refs, "pre_action_event_refs", required=True),
        )
        object.__setattr__(
            self,
            "post_validation_event_refs",
            _refs(self.post_validation_event_refs, "post_validation_event_refs"),
        )
        object.__setattr__(
            self,
            "authorization_ref",
            _identifier(self.authorization_ref, "authorization_ref"),
        )
        object.__setattr__(
            self,
            "quota_buckets",
            _codes(self.quota_buckets, "quota_buckets", required=True),
        )
        if self.changed is not None and not isinstance(self.changed, bool):
            raise TypeError("changed must be boolean or None")
        for name in ("ledger_event_ref", "repair_record_ref"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _identifier(value, name))

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "sequence_index": self.sequence_index,
            "result": self.result,
            "pre_action_event_refs": list(self.pre_action_event_refs),
            "post_validation_event_refs": list(self.post_validation_event_refs),
            "authorization_ref": self.authorization_ref,
            "quota_buckets": list(self.quota_buckets),
            "changed": self.changed,
            "ledger_event_ref": self.ledger_event_ref,
            "repair_record_ref": self.repair_record_ref,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CaseAttempt":
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class DiagnosticCase:
    case_id: str
    created_at: str
    status: CaseStatus | str
    diagnostic_event_refs: tuple[str, ...]
    attempts: tuple[CaseAttempt, ...] = ()
    outcome: CaseOutcome | str | None = None
    closure_reason: str | None = None
    owner_assessments: tuple[OwnerAssessment, ...] = ()
    eligibility_snapshots: tuple[RepairEligibilitySnapshot, ...] = ()
    evidence_checks: tuple[EvidenceCheckResult, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _identifier(self.case_id, "case_id"))
        object.__setattr__(self, "created_at", _timestamp(self.created_at))
        status = _enum(self.status, CaseStatus)
        outcome = None if self.outcome is None else _enum(self.outcome, CaseOutcome)
        if status is CaseStatus.OPEN and outcome is not None:
            raise ValueError("open cases must not have a final outcome")
        if status is CaseStatus.CLOSED and outcome is None:
            raise ValueError("closed cases require an outcome")
        event_refs = _refs(
            self.diagnostic_event_refs,
            "diagnostic_event_refs",
            required=True,
        )
        attempts = tuple(self.attempts)
        if not all(isinstance(item, CaseAttempt) for item in attempts):
            raise TypeError("attempts must contain CaseAttempt")
        attempt_ids = [item.attempt_id for item in attempts]
        sequence_ids = [item.sequence_index for item in attempts]
        if len(attempt_ids) != len(set(attempt_ids)):
            raise ValueError("attempt IDs must be unique")
        if len(sequence_ids) != len(set(sequence_ids)):
            raise ValueError("attempt sequence indices must be unique")
        if sequence_ids != sorted(sequence_ids):
            raise ValueError("attempts must be ordered by sequence_index")
        event_ref_set = set(event_refs)
        for attempt in attempts:
            referenced = set(attempt.pre_action_event_refs)
            referenced.update(attempt.post_validation_event_refs)
            if not referenced.issubset(event_ref_set):
                raise ValueError("attempt event references must belong to the case")
        owner_assessments = tuple(self.owner_assessments)
        eligibility = tuple(self.eligibility_snapshots)
        evidence_checks = tuple(self.evidence_checks)
        if not all(isinstance(item, OwnerAssessment) for item in owner_assessments):
            raise TypeError("owner_assessments must contain OwnerAssessment")
        if not all(
            isinstance(item, RepairEligibilitySnapshot) for item in eligibility
        ):
            raise TypeError("eligibility_snapshots must contain RepairEligibilitySnapshot")
        if not all(isinstance(item, EvidenceCheckResult) for item in evidence_checks):
            raise TypeError("evidence_checks must contain EvidenceCheckResult")
        if any(item.diagnostic_event_ref not in event_ref_set for item in owner_assessments):
            raise ValueError("owner assessments must reference case diagnostic events")
        closure_reason = (
            None
            if self.closure_reason is None
            else _code(self.closure_reason, "closure_reason")
        )
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != 1
        ):
            raise ValueError("unsupported case schema_version")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "diagnostic_event_refs", event_refs)
        object.__setattr__(self, "attempts", attempts)
        object.__setattr__(self, "owner_assessments", owner_assessments)
        object.__setattr__(self, "eligibility_snapshots", eligibility)
        object.__setattr__(self, "evidence_checks", evidence_checks)
        object.__setattr__(self, "closure_reason", closure_reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "created_at": self.created_at,
            "status": self.status.value,
            "outcome": None if self.outcome is None else self.outcome.value,
            "closure_reason": self.closure_reason,
            "diagnostic_event_refs": list(self.diagnostic_event_refs),
            "attempts": [item.to_dict() for item in self.attempts],
            "owner_assessments": [item.to_dict() for item in self.owner_assessments],
            "eligibility_snapshots": [item.to_dict() for item in self.eligibility_snapshots],
            "evidence_checks": [item.to_dict() for item in self.evidence_checks],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DiagnosticCase":
        if not isinstance(value, Mapping):
            raise TypeError("case must be a mapping")
        data = dict(value)
        data["attempts"] = tuple(CaseAttempt.from_dict(item) for item in data.get("attempts", ()))
        data["owner_assessments"] = tuple(
            OwnerAssessment.from_dict(item)
            for item in data.get("owner_assessments", ())
        )
        data["eligibility_snapshots"] = tuple(
            RepairEligibilitySnapshot.from_dict(item)
            for item in data.get("eligibility_snapshots", ())
        )
        data["evidence_checks"] = tuple(
            EvidenceCheckResult.from_dict(item)
            for item in data.get("evidence_checks", ())
        )
        return cls(**data)


@dataclass(frozen=True, slots=True)
class ExperienceRecord:
    experience_id: str
    source_case_id: str
    source_case_outcome: CaseOutcome | str
    source_attempt_ids: tuple[str, ...]
    polarity: ExperiencePolarity | str
    applicability_conditions: tuple[str, ...]
    action_summary: str
    validation_scope: str
    validation_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    source_version_hash: str
    change_fingerprint: str | None = None
    schema_version: int = 1
    stage: str | None = None
    owner: str | None = None
    components: tuple[str, ...] = ()
    visibility: str | None = None
    diagnostic_terms: tuple[str, ...] = ()
    source_terms: tuple[str, ...] = ()
    evidence_terms: tuple[str, ...] = ()
    validation_contract: str | None = None
    toolchain: str | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "experience_id", _identifier(self.experience_id, "experience_id"))
        object.__setattr__(self, "source_case_id", _identifier(self.source_case_id, "source_case_id"))
        outcome = _enum(self.source_case_outcome, CaseOutcome)
        if outcome is not CaseOutcome.RESOLVED:
            raise ValueError("unresolved cases cannot produce retrievable experiences")
        object.__setattr__(self, "source_case_outcome", outcome)
        object.__setattr__(
            self,
            "source_attempt_ids",
            _refs(self.source_attempt_ids, "source_attempt_ids", required=True),
        )
        object.__setattr__(self, "polarity", _enum(self.polarity, ExperiencePolarity))
        object.__setattr__(
            self,
            "applicability_conditions",
            _codes(self.applicability_conditions, "applicability_conditions", required=True),
        )
        object.__setattr__(self, "action_summary", _text(self.action_summary, "action_summary"))
        object.__setattr__(self, "validation_scope", _code(self.validation_scope, "validation_scope"))
        object.__setattr__(
            self,
            "validation_refs",
            _refs(self.validation_refs, "validation_refs", required=True),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            _refs(self.evidence_refs, "evidence_refs", required=True),
        )
        object.__setattr__(self, "source_version_hash", _hash(self.source_version_hash, "source_version_hash"))
        if self.change_fingerprint is not None:
            object.__setattr__(
                self,
                "change_fingerprint",
                _hash(self.change_fingerprint, "change_fingerprint"),
            )
        if (
            self.polarity is ExperiencePolarity.NEGATIVE
            and self.change_fingerprint is None
        ):
            raise ValueError("negative experiences require a change fingerprint")
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version not in {1, 2}
        ):
            raise ValueError("unsupported experience schema_version")
        if self.schema_version == 2:
            for name in ("stage", "owner", "visibility", "validation_contract"):
                object.__setattr__(self, name, _code(getattr(self, name), name))
            object.__setattr__(self, "components", _codes(self.components, "components", required=True))
            terms = _codes(self.diagnostic_terms, "diagnostic_terms", required=True)
            if len(terms) > 32:
                raise ValueError("diagnostic_terms exceeds the item limit")
            object.__setattr__(self, "diagnostic_terms", terms)
            object.__setattr__(self, "source_terms", _codes(self.source_terms, "source_terms"))
            object.__setattr__(self, "evidence_terms", _codes(self.evidence_terms, "evidence_terms"))
            if self.visibility not in {"public", "public_generation"}:
                raise ValueError("schema v2 experiences must have a public visibility")
            if self.toolchain is not None:
                object.__setattr__(self, "toolchain", _code(self.toolchain, "toolchain"))
            if self.polarity is ExperiencePolarity.NEGATIVE:
                object.__setattr__(self, "failure_code", _code(self.failure_code, "failure_code"))

    def to_dict(self) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "experience_id": self.experience_id,
            "source_case_id": self.source_case_id,
            "source_case_outcome": self.source_case_outcome.value,
            "source_attempt_ids": list(self.source_attempt_ids),
            "polarity": self.polarity.value,
            "applicability_conditions": list(self.applicability_conditions),
            "action_summary": self.action_summary,
            "validation_scope": self.validation_scope,
            "validation_refs": list(self.validation_refs),
            "evidence_refs": list(self.evidence_refs),
            "source_version_hash": self.source_version_hash,
            "change_fingerprint": self.change_fingerprint,
        }
        if self.schema_version == 2:
            value.update(
                stage=self.stage,
                owner=self.owner,
                components=list(self.components),
                visibility=self.visibility,
                diagnostic_terms=list(self.diagnostic_terms),
                source_terms=list(self.source_terms),
                evidence_terms=list(self.evidence_terms),
                validation_contract=self.validation_contract,
                toolchain=self.toolchain,
                failure_code=self.failure_code,
            )
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExperienceRecord":
        return cls(**dict(value))

    @classmethod
    def from_case(
        cls,
        case: DiagnosticCase,
        *,
        experience_id: str,
        source_attempt_ids: tuple[str, ...],
        polarity: ExperiencePolarity | str,
        applicability_conditions: tuple[str, ...],
        action_summary: str,
        validation_scope: str,
        validation_refs: tuple[str, ...],
        evidence_refs: tuple[str, ...],
        source_version_hash: str,
        change_fingerprint: str | None = None,
        schema_version: int = 1,
        stage: str | None = None,
        owner: str | None = None,
        components: tuple[str, ...] = (),
        visibility: str | None = None,
        diagnostic_terms: tuple[str, ...] = (),
        source_terms: tuple[str, ...] = (),
        evidence_terms: tuple[str, ...] = (),
        validation_contract: str | None = None,
        toolchain: str | None = None,
        failure_code: str | None = None,
    ) -> "ExperienceRecord":
        if not isinstance(case, DiagnosticCase):
            raise TypeError("case must be DiagnosticCase")
        if case.status is not CaseStatus.CLOSED or case.outcome is not CaseOutcome.RESOLVED:
            raise ValueError("experiences require a resolved, closed source case")
        selected_ids = _refs(source_attempt_ids, "source_attempt_ids", required=True)
        attempts = {item.attempt_id: item for item in case.attempts}
        if any(item not in attempts for item in selected_ids):
            raise ValueError("experience attempts must belong to the source case")
        selected = tuple(attempts[item] for item in selected_ids)
        normalized_polarity = _enum(polarity, ExperiencePolarity)
        normalized_validation_refs = _refs(
            validation_refs,
            "validation_refs",
            required=True,
        )
        validation_ref_set = set(normalized_validation_refs)
        linked_validation_refs = {
            ref
            for attempt in selected
            for ref in attempt.post_validation_event_refs
        }
        if not validation_ref_set.intersection(linked_validation_refs):
            raise ValueError("experience validation refs must link to source attempts")
        if normalized_polarity is ExperiencePolarity.NEGATIVE:
            if any(
                item.result != "validation_failed" or item.changed is not True
                for item in selected
            ):
                raise ValueError(
                    "negative experiences require changed attempts with explicit validation failure"
                )
            if change_fingerprint is None:
                raise ValueError("negative experiences require a change fingerprint")
        elif not any(
            item.result == "validated" and item.changed is True
            for item in selected
        ):
            raise ValueError("positive experiences require a validated change")
        return cls(
            experience_id=experience_id,
            source_case_id=case.case_id,
            source_case_outcome=case.outcome,
            source_attempt_ids=selected_ids,
            polarity=normalized_polarity,
            applicability_conditions=applicability_conditions,
            action_summary=action_summary,
            validation_scope=validation_scope,
            validation_refs=normalized_validation_refs,
            evidence_refs=evidence_refs,
            source_version_hash=source_version_hash,
            change_fingerprint=change_fingerprint,
            schema_version=schema_version,
            stage=stage,
            owner=owner,
            components=components,
            visibility=visibility,
            diagnostic_terms=diagnostic_terms,
            source_terms=source_terms,
            evidence_terms=evidence_terms,
            validation_contract=validation_contract,
            toolchain=toolchain,
            failure_code=failure_code,
        )
