"""Agent-safe LLM advisory schema that can never directly accept a result."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
import json
from typing import Any, Protocol


class AdvisoryOwner(str, Enum):
    CANDIDATE = "candidate"
    TESTBENCH = "testbench"
    TOOLCHAIN = "toolchain"
    CONFIGURATION = "configuration"
    UNKNOWN = "unknown"


class AdvisoryRepairScope(str, Enum):
    CANDIDATE_ONLY = "candidate_only"
    TESTBENCH_ONLY = "testbench_only"
    NONE = "none"


class AdvisoryConfidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class DiagnosticAdvisoryRequest:
    stage: str
    evidence_ids: tuple[str, ...]
    evidence_summary: Mapping[str, Any]
    evidence_view: str = "agent_safe"
    hidden_input_count: int = 0
    secret_present: bool = False
    private_reasoning_present: bool = False
    run_identity_complete: bool = False
    physical_tool_launched: bool = False

    def __post_init__(self) -> None:
        if self.evidence_view != "agent_safe":
            raise ValueError("advisory requires agent_safe evidence")
        ids = _ids(self.evidence_ids)
        if not ids:
            raise ValueError("advisory requires evidence IDs")
        if self.hidden_input_count != 0:
            raise ValueError("Hidden evidence is forbidden from advisory")
        if self.secret_present or self.private_reasoning_present:
            raise ValueError("secret/private reasoning is forbidden")
        if not isinstance(self.evidence_summary, Mapping):
            raise TypeError("evidence_summary must be a mapping")
        copied = json.loads(json.dumps(dict(self.evidence_summary), allow_nan=False))
        object.__setattr__(self, "evidence_ids", ids)
        object.__setattr__(self, "evidence_summary", copied)


@dataclass(frozen=True, slots=True)
class DiagnosticAdvisory:
    suspected_owner: AdvisoryOwner
    suspected_failure_class: str
    evidence_refs: tuple[str, ...]
    repair_scope: AdvisoryRepairScope
    confidence: AdvisoryConfidence
    abstain_reason: str | None = None
    owner_authority: str = "llm_advisory"
    accepted: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "suspected_owner",
            _enum(self.suspected_owner, AdvisoryOwner),
        )
        object.__setattr__(
            self,
            "repair_scope",
            _enum(self.repair_scope, AdvisoryRepairScope),
        )
        object.__setattr__(
            self,
            "confidence",
            _enum(self.confidence, AdvisoryConfidence),
        )
        if self.owner_authority != "llm_advisory":
            raise ValueError("owner_authority must be llm_advisory")
        if self.accepted:
            raise ValueError("advisory must never directly accept")
        refs = _ids(self.evidence_refs)
        if not isinstance(self.suspected_failure_class, str) or not (
            self.suspected_failure_class.strip()
        ):
            raise ValueError("suspected_failure_class must not be empty")
        if self.abstain_reason is not None and not self.abstain_reason.strip():
            raise ValueError("abstain_reason must be null or non-empty")
        copied = json.loads(json.dumps(dict(self.metadata), allow_nan=False))
        object.__setattr__(self, "evidence_refs", refs)
        object.__setattr__(self, "metadata", copied)

        if self.abstain_reason is not None:
            if self.repair_scope is not AdvisoryRepairScope.NONE:
                raise ValueError("abstaining advisory must use repair_scope=none")
        elif not refs:
            raise ValueError("non-abstaining advisory requires evidence refs")

    @property
    def exploratory_repair_eligible(self) -> bool:
        return (
            self.abstain_reason is None
            and self.confidence is AdvisoryConfidence.HIGH
            and self.repair_scope is AdvisoryRepairScope.CANDIDATE_ONLY
            and self.suspected_owner is AdvisoryOwner.CANDIDATE
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "suspected_owner": self.suspected_owner.value,
            "suspected_failure_class": self.suspected_failure_class,
            "evidence_refs": list(self.evidence_refs),
            "repair_scope": self.repair_scope.value,
            "confidence": self.confidence.value,
            "abstain_reason": self.abstain_reason,
            "owner_authority": "llm_advisory",
            "accepted": False,
            "metadata": dict(self.metadata),
        }


class DiagnosticAdvisor(Protocol):
    def diagnose(self, request: DiagnosticAdvisoryRequest) -> DiagnosticAdvisory:
        ...


def validate_advisory_result(
    request: DiagnosticAdvisoryRequest,
    result: DiagnosticAdvisory,
) -> DiagnosticAdvisory:
    if not isinstance(request, DiagnosticAdvisoryRequest):
        raise TypeError("request must be DiagnosticAdvisoryRequest")
    if not isinstance(result, DiagnosticAdvisory):
        raise TypeError("advisor must return DiagnosticAdvisory")
    if not set(result.evidence_refs).issubset(set(request.evidence_ids)):
        raise ValueError("advisory referenced evidence outside the request")
    if not request.run_identity_complete:
        raise ValueError("advisory requires complete run identity")
    if not request.physical_tool_launched:
        raise ValueError("advisory requires a physical tool launch")
    return result


def _ids(value: Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise TypeError("evidence IDs must be a sequence")
    result = tuple(str(item).strip() for item in value)
    if any(not item for item in result):
        raise ValueError("evidence IDs must not be empty")
    if len(result) != len(set(result)):
        raise ValueError("evidence IDs must be unique")
    return result


def _enum(value: Any, enum_type):
    if isinstance(value, enum_type):
        return value
    return enum_type(str(value))
