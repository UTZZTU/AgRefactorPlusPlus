"""Typed, deterministic state records for the Stage 3 safe optimizer."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import PurePosixPath
import re
from typing import Any, TypeVar


SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CANDIDATE_RE = re.compile(r"^cand-([0-9]+)$")
_AGENT_UNSAFE_RE = re.compile(
    r"(?:operator[_ -]?full|source[_ -]?report[_ -]?id|"
    r"hidden[_ -]?(?:testbench|diagnostic|report|state|plaintext|secret)|"
    r"private[_ -]?testbench)",
    re.IGNORECASE,
)
_SECRET_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "bearer",
        "credential",
        "credentials",
        "password",
        "refresh_token",
        "secret",
        "token",
    }
)
_VERIFICATION_ORDER = {
    "preflight": 0,
    "public": 1,
    "csynth": 2,
    "hidden": 3,
}


class OptimizationLevel(str, Enum):
    """Frozen Stage 3 optimization levels."""

    STRUCTURAL = "structural"
    BOTTLENECK = "bottleneck"
    PRAGMA = "pragma"


class HypothesisRisk(str, Enum):
    """Normalized risk estimate for one optimization hypothesis."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CandidateStatus(str, Enum):
    """Monotonic lifecycle state for a baseline or generated candidate."""

    GENERATED = "generated"
    VALIDATING = "validating"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    ERROR = "error"

    @property
    def terminal(self) -> bool:
        return self in {
            CandidateStatus.ACCEPTED,
            CandidateStatus.REJECTED,
            CandidateStatus.BLOCKED,
            CandidateStatus.ERROR,
        }


class OptimizerTerminalStatus(str, Enum):
    """Terminal statuses allowed by the frozen Stage 3 contract."""

    ACCEPTED_IMPROVED = "accepted_improved"
    ACCEPTED_NO_IMPROVEMENT = "accepted_no_improvement"
    BUDGET_EXHAUSTED_WITH_BEST_CORRECT = (
        "budget_exhausted_with_best_correct"
    )
    NO_FEASIBLE_CANDIDATE = "no_feasible_candidate"
    BASELINE_REJECTED = "baseline_rejected"
    BLOCKED = "blocked"
    REVIEW_REQUIRED = "review_required"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class HypothesisRecord:
    """One agent-safe, evidence-linked optimization hypothesis."""

    hypothesis_id: str
    level: OptimizationLevel
    parent_candidate_id: str
    claim: str
    supporting_evidence_ids: tuple[str, ...]
    expected_benefit: Mapping[str, Any]
    risk: HypothesisRisk
    modification_scope: tuple[str, ...]
    verification_plan: tuple[str, ...]
    model_identity: Mapping[str, Any]
    prompt_identity_sha256: str

    schema_version = SCHEMA_VERSION

    def __post_init__(self) -> None:
        hypothesis_id = _required_id(self.hypothesis_id, "hypothesis_id")
        if not hypothesis_id.startswith("hyp-"):
            raise ValueError("hypothesis_id must start with 'hyp-'")
        level = _enum(self.level, OptimizationLevel, "level")
        parent = _candidate_reference(
            self.parent_candidate_id,
            "parent_candidate_id",
        )
        claim = _agent_safe_text(self.claim, "claim")
        evidence_ids = _id_tuple(
            self.supporting_evidence_ids,
            "supporting_evidence_ids",
            allow_empty=True,
        )
        if level is OptimizationLevel.BOTTLENECK and not evidence_ids:
            raise ValueError(
                "bottleneck hypotheses require supporting evidence"
            )
        expected_benefit = _json_mapping(
            self.expected_benefit,
            "expected_benefit",
            reject_secrets=True,
            agent_safe=True,
        )
        if set(expected_benefit) != {"metric", "direction"}:
            raise ValueError(
                "expected_benefit must contain exactly metric and direction"
            )
        expected_benefit["metric"] = _required_id(
            expected_benefit["metric"],
            "expected_benefit.metric",
        )
        expected_benefit["direction"] = _required_id(
            expected_benefit["direction"],
            "expected_benefit.direction",
        )
        risk = _enum(self.risk, HypothesisRisk, "risk")
        scope = _text_tuple(
            self.modification_scope,
            "modification_scope",
            agent_safe=True,
        )
        plan = _verification_plan(self.verification_plan)
        model_identity = _json_mapping(
            self.model_identity,
            "model_identity",
            reject_secrets=True,
            agent_safe=True,
        )
        prompt_sha = _sha256(
            self.prompt_identity_sha256,
            "prompt_identity_sha256",
        )

        object.__setattr__(self, "hypothesis_id", hypothesis_id)
        object.__setattr__(self, "level", level)
        object.__setattr__(self, "parent_candidate_id", parent)
        object.__setattr__(self, "claim", claim)
        object.__setattr__(self, "supporting_evidence_ids", evidence_ids)
        object.__setattr__(self, "expected_benefit", expected_benefit)
        object.__setattr__(self, "risk", risk)
        object.__setattr__(self, "modification_scope", scope)
        object.__setattr__(self, "verification_plan", plan)
        object.__setattr__(self, "model_identity", model_identity)
        object.__setattr__(self, "prompt_identity_sha256", prompt_sha)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "hypothesis_id": self.hypothesis_id,
            "level": self.level.value,
            "parent_candidate_id": self.parent_candidate_id,
            "claim": self.claim,
            "supporting_evidence_ids": list(
                self.supporting_evidence_ids
            ),
            "expected_benefit": _json_mapping(
                self.expected_benefit,
                "expected_benefit",
            ),
            "risk": self.risk.value,
            "modification_scope": list(self.modification_scope),
            "verification_plan": list(self.verification_plan),
            "model_identity": _json_mapping(
                self.model_identity,
                "model_identity",
            ),
            "prompt_identity_sha256": self.prompt_identity_sha256,
        }

    def to_json(self) -> str:
        return _stable_json(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "HypothesisRecord":
        value = _strict_payload(
            payload,
            {
                "schema_version",
                "hypothesis_id",
                "level",
                "parent_candidate_id",
                "claim",
                "supporting_evidence_ids",
                "expected_benefit",
                "risk",
                "modification_scope",
                "verification_plan",
                "model_identity",
                "prompt_identity_sha256",
            },
            "hypothesis",
        )
        return cls(
            hypothesis_id=value["hypothesis_id"],
            level=value["level"],
            parent_candidate_id=value["parent_candidate_id"],
            claim=value["claim"],
            supporting_evidence_ids=tuple(
                value["supporting_evidence_ids"]
            ),
            expected_benefit=value["expected_benefit"],
            risk=value["risk"],
            modification_scope=tuple(value["modification_scope"]),
            verification_plan=tuple(value["verification_plan"]),
            model_identity=value["model_identity"],
            prompt_identity_sha256=value["prompt_identity_sha256"],
        )

    @classmethod
    def from_json(cls, payload: str) -> "HypothesisRecord":
        return cls.from_dict(_json_object(payload, "hypothesis JSON"))


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    """Typed record for the baseline or one executed candidate."""

    candidate_id: str
    sequence: int
    parent_candidate_id: str | None
    hypothesis_id: str | None
    level: OptimizationLevel | None
    source_sha256: str
    source_artifact: str
    status: CandidateStatus
    correctness: Mapping[str, Any] = field(default_factory=dict)
    synthesis: Mapping[str, Any] = field(default_factory=dict)
    ppa: Mapping[str, Any] = field(default_factory=dict)
    budget_before: Mapping[str, Any] = field(default_factory=dict)
    budget_after: Mapping[str, Any] = field(default_factory=dict)
    decision: Mapping[str, Any] = field(default_factory=dict)
    created_at_utc: str = "1970-01-01T00:00:00Z"

    schema_version = SCHEMA_VERSION

    def __post_init__(self) -> None:
        candidate_id = _candidate_reference(self.candidate_id, "candidate_id")
        sequence = _nonnegative_int(self.sequence, "sequence")
        status = _enum(self.status, CandidateStatus, "status")
        source_sha = _sha256(self.source_sha256, "source_sha256")
        source_artifact = _artifact_path(
            self.source_artifact,
            "source_artifact",
        )

        if candidate_id == "baseline":
            if sequence != 0:
                raise ValueError("baseline sequence must be 0")
            if self.parent_candidate_id is not None:
                raise ValueError("baseline must not have a parent")
            if self.hypothesis_id is not None:
                raise ValueError("baseline must not have a hypothesis")
            if self.level is not None:
                raise ValueError("baseline must not have a level")
            expected_path = "candidates/baseline/source.cpp"
            parent = None
            hypothesis_id = None
            level = None
        else:
            match = _CANDIDATE_RE.fullmatch(candidate_id)
            if match is None:
                raise ValueError(
                    "generated candidate_id must match cand-<sequence>"
                )
            if sequence < 1:
                raise ValueError("generated candidate sequence must be positive")
            if int(match.group(1)) != sequence:
                raise ValueError(
                    "candidate_id numeric suffix must match sequence"
                )
            parent = _candidate_reference(
                self.parent_candidate_id,
                "parent_candidate_id",
            )
            if parent == candidate_id:
                raise ValueError("candidate cannot be its own parent")
            hypothesis_id = _required_id(
                self.hypothesis_id,
                "hypothesis_id",
            )
            if not hypothesis_id.startswith("hyp-"):
                raise ValueError("hypothesis_id must start with 'hyp-'")
            level = _enum(self.level, OptimizationLevel, "level")
            expected_path = f"candidates/{candidate_id}/source.cpp"

        if source_artifact != expected_path:
            raise ValueError(
                f"source_artifact must be {expected_path!r}"
            )

        correctness = _json_mapping(
            self.correctness,
            "correctness",
            reject_secrets=True,
        )
        synthesis = _json_mapping(
            self.synthesis,
            "synthesis",
            reject_secrets=True,
        )
        ppa = _json_mapping(self.ppa, "ppa", reject_secrets=True)
        budget_before = _json_mapping(
            self.budget_before,
            "budget_before",
            reject_secrets=True,
        )
        budget_after = _json_mapping(
            self.budget_after,
            "budget_after",
            reject_secrets=True,
        )
        decision = _json_mapping(
            self.decision,
            "decision",
            reject_secrets=True,
        )
        created_at = _utc_timestamp(self.created_at_utc)

        if status.terminal and status is not CandidateStatus.ACCEPTED:
            if not decision:
                raise ValueError(
                    "rejected, blocked, and error candidates require decision"
                )
        if (
            candidate_id != "baseline"
            and status is CandidateStatus.ACCEPTED
            and (not correctness or not synthesis or not decision)
        ):
            raise ValueError(
                "accepted generated candidates require correctness, synthesis, "
                "and decision evidence"
            )

        object.__setattr__(self, "candidate_id", candidate_id)
        object.__setattr__(self, "sequence", sequence)
        object.__setattr__(self, "parent_candidate_id", parent)
        object.__setattr__(self, "hypothesis_id", hypothesis_id)
        object.__setattr__(self, "level", level)
        object.__setattr__(self, "source_sha256", source_sha)
        object.__setattr__(self, "source_artifact", source_artifact)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "correctness", correctness)
        object.__setattr__(self, "synthesis", synthesis)
        object.__setattr__(self, "ppa", ppa)
        object.__setattr__(self, "budget_before", budget_before)
        object.__setattr__(self, "budget_after", budget_after)
        object.__setattr__(self, "decision", decision)
        object.__setattr__(self, "created_at_utc", created_at)

    @property
    def is_baseline(self) -> bool:
        return self.candidate_id == "baseline"

    def transition_to(
        self,
        status: CandidateStatus | str,
        *,
        correctness: Mapping[str, Any] | None = None,
        synthesis: Mapping[str, Any] | None = None,
        ppa: Mapping[str, Any] | None = None,
        budget_after: Mapping[str, Any] | None = None,
        decision: Mapping[str, Any] | None = None,
    ) -> "CandidateRecord":
        next_status = _enum(status, CandidateStatus, "status")
        allowed = {
            CandidateStatus.GENERATED: {
                CandidateStatus.VALIDATING,
                CandidateStatus.REJECTED,
                CandidateStatus.BLOCKED,
                CandidateStatus.ERROR,
            },
            CandidateStatus.VALIDATING: {
                CandidateStatus.ACCEPTED,
                CandidateStatus.REJECTED,
                CandidateStatus.BLOCKED,
                CandidateStatus.ERROR,
            },
        }
        if next_status not in allowed.get(self.status, set()):
            raise ValueError(
                f"illegal candidate status transition: "
                f"{self.status.value} -> {next_status.value}"
            )
        return replace(
            self,
            status=next_status,
            correctness=(
                self.correctness if correctness is None else correctness
            ),
            synthesis=self.synthesis if synthesis is None else synthesis,
            ppa=self.ppa if ppa is None else ppa,
            budget_after=(
                self.budget_after if budget_after is None else budget_after
            ),
            decision=self.decision if decision is None else decision,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "sequence": self.sequence,
            "parent_candidate_id": self.parent_candidate_id,
            "hypothesis_id": self.hypothesis_id,
            "level": None if self.level is None else self.level.value,
            "source_sha256": self.source_sha256,
            "source_artifact": self.source_artifact,
            "status": self.status.value,
            "correctness": _json_mapping(self.correctness, "correctness"),
            "synthesis": _json_mapping(self.synthesis, "synthesis"),
            "ppa": _json_mapping(self.ppa, "ppa"),
            "budget_before": _json_mapping(
                self.budget_before,
                "budget_before",
            ),
            "budget_after": _json_mapping(
                self.budget_after,
                "budget_after",
            ),
            "decision": _json_mapping(self.decision, "decision"),
            "created_at_utc": self.created_at_utc,
        }

    def to_json(self) -> str:
        return _stable_json(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CandidateRecord":
        value = _strict_payload(
            payload,
            {
                "schema_version",
                "candidate_id",
                "sequence",
                "parent_candidate_id",
                "hypothesis_id",
                "level",
                "source_sha256",
                "source_artifact",
                "status",
                "correctness",
                "synthesis",
                "ppa",
                "budget_before",
                "budget_after",
                "decision",
                "created_at_utc",
            },
            "candidate",
        )
        return cls(
            candidate_id=value["candidate_id"],
            sequence=value["sequence"],
            parent_candidate_id=value["parent_candidate_id"],
            hypothesis_id=value["hypothesis_id"],
            level=value["level"],
            source_sha256=value["source_sha256"],
            source_artifact=value["source_artifact"],
            status=value["status"],
            correctness=value["correctness"],
            synthesis=value["synthesis"],
            ppa=value["ppa"],
            budget_before=value["budget_before"],
            budget_after=value["budget_after"],
            decision=value["decision"],
            created_at_utc=value["created_at_utc"],
        )

    @classmethod
    def from_json(cls, payload: str) -> "CandidateRecord":
        return cls.from_dict(_json_object(payload, "candidate JSON"))


@dataclass(frozen=True, slots=True)
class OptimizerState:
    """Persisted optimizer pointers and strategy counters."""

    run_id: str
    policy_profile: str = "safe-v1"
    objective: str = "latency"
    baseline_candidate_id: str = "baseline"
    current_candidate_id: str = "baseline"
    best_correct_candidate_id: str | None = None
    best_ppa_candidate_id: str | None = None
    current_level: OptimizationLevel = OptimizationLevel.STRUCTURAL
    current_round: int = 1
    executed_candidate_count: int = 0
    terminal_status: OptimizerTerminalStatus | None = None
    checkpoint_sequence: int = 0

    schema_version = SCHEMA_VERSION

    def __post_init__(self) -> None:
        run_id = _required_id(self.run_id, "run_id")
        policy = _required_id(self.policy_profile, "policy_profile")
        if policy != "safe-v1":
            raise ValueError("S3.1 only supports policy_profile='safe-v1'")
        objective = _required_id(self.objective, "objective")
        if objective != "latency":
            raise ValueError("S3.1 only supports objective='latency'")
        baseline_id = _candidate_reference(
            self.baseline_candidate_id,
            "baseline_candidate_id",
        )
        if baseline_id != "baseline":
            raise ValueError("baseline_candidate_id must be 'baseline'")
        current_id = _candidate_reference(
            self.current_candidate_id,
            "current_candidate_id",
        )
        best_correct = _optional_candidate_reference(
            self.best_correct_candidate_id,
            "best_correct_candidate_id",
        )
        best_ppa = _optional_candidate_reference(
            self.best_ppa_candidate_id,
            "best_ppa_candidate_id",
        )
        level = _enum(self.current_level, OptimizationLevel, "current_level")
        current_round = _positive_int(self.current_round, "current_round")
        executed = _nonnegative_int(
            self.executed_candidate_count,
            "executed_candidate_count",
        )
        terminal = (
            None
            if self.terminal_status is None
            else _enum(
                self.terminal_status,
                OptimizerTerminalStatus,
                "terminal_status",
            )
        )
        checkpoint = _nonnegative_int(
            self.checkpoint_sequence,
            "checkpoint_sequence",
        )
        if (
            terminal
            in {
                OptimizerTerminalStatus.ACCEPTED_IMPROVED,
                OptimizerTerminalStatus.ACCEPTED_NO_IMPROVEMENT,
                OptimizerTerminalStatus.BUDGET_EXHAUSTED_WITH_BEST_CORRECT,
                OptimizerTerminalStatus.NO_FEASIBLE_CANDIDATE,
            }
            and best_correct is None
        ):
            raise ValueError(
                f"terminal_status={terminal.value} requires best_correct"
            )

        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "policy_profile", policy)
        object.__setattr__(self, "objective", objective)
        object.__setattr__(self, "baseline_candidate_id", baseline_id)
        object.__setattr__(self, "current_candidate_id", current_id)
        object.__setattr__(self, "best_correct_candidate_id", best_correct)
        object.__setattr__(self, "best_ppa_candidate_id", best_ppa)
        object.__setattr__(self, "current_level", level)
        object.__setattr__(self, "current_round", current_round)
        object.__setattr__(self, "executed_candidate_count", executed)
        object.__setattr__(self, "terminal_status", terminal)
        object.__setattr__(self, "checkpoint_sequence", checkpoint)

    @classmethod
    def initial(cls, *, run_id: str) -> "OptimizerState":
        """Create the pre-qualification state for a known baseline record."""

        return cls(run_id=run_id)

    def with_qualified_baseline(
        self,
        baseline: CandidateRecord,
    ) -> "OptimizerState":
        if not isinstance(baseline, CandidateRecord):
            raise TypeError("baseline must be a CandidateRecord")
        if not baseline.is_baseline:
            raise ValueError("qualified baseline candidate_id must be 'baseline'")
        if baseline.status is not CandidateStatus.ACCEPTED:
            raise ValueError("qualified baseline must be accepted")
        if self.best_correct_candidate_id is not None:
            raise ValueError("best_correct is already initialized")
        return replace(
            self,
            current_candidate_id=baseline.candidate_id,
            best_correct_candidate_id=baseline.candidate_id,
        )

    def with_checkpoint_sequence(self, sequence: int) -> "OptimizerState":
        next_sequence = _nonnegative_int(sequence, "checkpoint_sequence")
        if next_sequence != self.checkpoint_sequence + 1:
            raise ValueError(
                "checkpoint_sequence must advance by exactly one"
            )
        return replace(self, checkpoint_sequence=next_sequence)

    def validate_against_candidates(
        self,
        candidates: Mapping[str, CandidateRecord],
    ) -> None:
        index = normalize_candidate_index(candidates)
        baseline = index.get(self.baseline_candidate_id)
        if baseline is None or not baseline.is_baseline:
            raise ValueError("candidate index must contain baseline")
        if self.current_candidate_id not in index:
            raise ValueError("current_candidate_id is missing from candidate index")

        generated = sorted(
            item.sequence for item in index.values() if not item.is_baseline
        )
        if generated != list(range(1, len(generated) + 1)):
            raise ValueError(
                "generated candidate sequences must be unique and contiguous"
            )
        if self.executed_candidate_count != len(generated):
            raise ValueError(
                "executed_candidate_count does not match candidate index"
            )

        for item in index.values():
            if item.is_baseline:
                continue
            parent = index.get(item.parent_candidate_id or "")
            if parent is None:
                raise ValueError(
                    f"candidate parent is missing: {item.candidate_id}"
                )
            if parent.sequence >= item.sequence:
                raise ValueError(
                    "candidate parent sequence must be lower than child sequence"
                )

        if baseline.status is CandidateStatus.ACCEPTED:
            if self.best_correct_candidate_id is None:
                raise ValueError(
                    "accepted baseline requires initial best_correct"
                )
        elif self.best_correct_candidate_id is not None:
            raise ValueError(
                "best_correct cannot exist before baseline qualification"
            )

        if self.best_correct_candidate_id is not None:
            best_correct = index.get(self.best_correct_candidate_id)
            if best_correct is None:
                raise ValueError(
                    "best_correct_candidate_id is missing from candidate index"
                )
            if best_correct.status is not CandidateStatus.ACCEPTED:
                raise ValueError("best_correct candidate must be accepted")

        if self.best_ppa_candidate_id is not None:
            best_ppa = index.get(self.best_ppa_candidate_id)
            if best_ppa is None:
                raise ValueError(
                    "best_ppa_candidate_id is missing from candidate index"
                )
            if best_ppa.status is not CandidateStatus.ACCEPTED:
                raise ValueError("best_ppa candidate must be accepted")
            if self.best_correct_candidate_id is None:
                raise ValueError("best_ppa requires best_correct")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "policy_profile": self.policy_profile,
            "objective": self.objective,
            "baseline_candidate_id": self.baseline_candidate_id,
            "current_candidate_id": self.current_candidate_id,
            "best_correct_candidate_id": self.best_correct_candidate_id,
            "best_ppa_candidate_id": self.best_ppa_candidate_id,
            "current_level": self.current_level.value,
            "current_round": self.current_round,
            "executed_candidate_count": self.executed_candidate_count,
            "terminal_status": (
                None
                if self.terminal_status is None
                else self.terminal_status.value
            ),
            "checkpoint_sequence": self.checkpoint_sequence,
        }

    def to_json(self) -> str:
        return _stable_json(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OptimizerState":
        value = _strict_payload(
            payload,
            {
                "schema_version",
                "run_id",
                "policy_profile",
                "objective",
                "baseline_candidate_id",
                "current_candidate_id",
                "best_correct_candidate_id",
                "best_ppa_candidate_id",
                "current_level",
                "current_round",
                "executed_candidate_count",
                "terminal_status",
                "checkpoint_sequence",
            },
            "optimizer state",
        )
        return cls(
            run_id=value["run_id"],
            policy_profile=value["policy_profile"],
            objective=value["objective"],
            baseline_candidate_id=value["baseline_candidate_id"],
            current_candidate_id=value["current_candidate_id"],
            best_correct_candidate_id=value["best_correct_candidate_id"],
            best_ppa_candidate_id=value["best_ppa_candidate_id"],
            current_level=value["current_level"],
            current_round=value["current_round"],
            executed_candidate_count=value["executed_candidate_count"],
            terminal_status=value["terminal_status"],
            checkpoint_sequence=value["checkpoint_sequence"],
        )

    @classmethod
    def from_json(cls, payload: str) -> "OptimizerState":
        return cls.from_dict(_json_object(payload, "optimizer state JSON"))


def candidate_index_to_dict(
    candidates: Mapping[str, CandidateRecord],
) -> dict[str, Any]:
    index = normalize_candidate_index(candidates)
    ordered = sorted(
        index.values(),
        key=lambda item: (item.sequence, item.candidate_id),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "candidates": [item.to_dict() for item in ordered],
    }


def candidate_index_from_dict(
    payload: Mapping[str, Any],
) -> dict[str, CandidateRecord]:
    value = _strict_payload(
        payload,
        {"schema_version", "candidates"},
        "candidate index",
    )
    raw_candidates = value["candidates"]
    if isinstance(raw_candidates, (str, bytes)) or not isinstance(
        raw_candidates,
        Sequence,
    ):
        raise TypeError("candidate index candidates must be a sequence")
    records = [CandidateRecord.from_dict(item) for item in raw_candidates]
    indexed: dict[str, CandidateRecord] = {}
    for record in records:
        if record.candidate_id in indexed:
            raise ValueError("candidate index contains duplicate candidate_id")
        indexed[record.candidate_id] = record
    return normalize_candidate_index(indexed)


def normalize_candidate_index(
    candidates: Mapping[str, CandidateRecord],
) -> dict[str, CandidateRecord]:
    if not isinstance(candidates, Mapping):
        raise TypeError("candidates must be a mapping")
    normalized: dict[str, CandidateRecord] = {}
    for key, record in candidates.items():
        candidate_id = _candidate_reference(key, "candidate index key")
        if not isinstance(record, CandidateRecord):
            raise TypeError("candidate index values must be CandidateRecord")
        if candidate_id != record.candidate_id:
            raise ValueError("candidate index key must match candidate_id")
        if candidate_id in normalized:
            raise ValueError("candidate ids must be unique")
        normalized[candidate_id] = record
    return normalized


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def _strict_payload(
    payload: Mapping[str, Any],
    allowed: set[str],
    name: str,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TypeError(f"{name} payload must be a mapping")
    value = dict(payload)
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(
            f"Unknown {name} fields: " + ", ".join(sorted(unknown))
        )
    missing = allowed - set(value)
    if missing:
        raise ValueError(
            f"Missing {name} fields: " + ", ".join(sorted(missing))
        )
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported {name} schema_version")
    return value


def _json_object(payload: str, name: str) -> Mapping[str, Any]:
    if not isinstance(payload, str):
        raise TypeError(f"{name} must be a string")
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must contain a JSON object")
    return value


def _json_mapping(
    value: Mapping[str, Any],
    name: str,
    *,
    reject_secrets: bool = False,
    agent_safe: bool = False,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be finite JSON") from exc
    copied = json.loads(encoded)
    if reject_secrets:
        _reject_secret_keys(copied, name)
    if agent_safe:
        _reject_agent_unsafe_value(copied, name)
    return copied


def _reject_secret_keys(value: Any, name: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in _SECRET_KEYS or any(
                token in normalized
                for token in ("api_key", "password", "secret", "token")
            ):
                raise ValueError(f"{name} contains a secret-like key: {key}")
            _reject_secret_keys(item, name)
    elif isinstance(value, list):
        for item in value:
            _reject_secret_keys(item, name)


def _reject_agent_unsafe_value(value: Any, name: str) -> None:
    if isinstance(value, str):
        _agent_safe_text(value, name)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _agent_safe_text(str(key), name)
            _reject_agent_unsafe_value(item, name)
    elif isinstance(value, list):
        for item in value:
            _reject_agent_unsafe_value(item, name)


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    if "\x00" in cleaned:
        raise ValueError(f"{name} must not contain NUL")
    return cleaned


def _agent_safe_text(value: Any, name: str) -> str:
    cleaned = _required_text(value, name)
    if _AGENT_UNSAFE_RE.search(cleaned):
        raise ValueError(f"{name} contains operator-only or Hidden material")
    return cleaned


def _required_id(value: Any, name: str) -> str:
    cleaned = _required_text(value, name)
    if _SAFE_ID_RE.fullmatch(cleaned) is None:
        raise ValueError(f"{name} must be a stable safe identifier")
    return cleaned


def _candidate_reference(value: Any, name: str) -> str:
    cleaned = _required_id(value, name)
    if cleaned != "baseline" and _CANDIDATE_RE.fullmatch(cleaned) is None:
        raise ValueError(f"{name} must be 'baseline' or cand-<sequence>")
    return cleaned


def _optional_candidate_reference(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _candidate_reference(value, name)


def _sha256(value: Any, name: str) -> str:
    cleaned = _required_text(value, name).lower()
    if _SHA256_RE.fullmatch(cleaned) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return cleaned


def _artifact_path(value: Any, name: str) -> str:
    cleaned = _required_text(value, name)
    if "\\" in cleaned:
        raise ValueError(f"{name} must use POSIX separators")
    path = PurePosixPath(cleaned)
    if path.is_absolute() or not path.parts:
        raise ValueError(f"{name} must be a relative path")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{name} contains unsafe path traversal")
    return path.as_posix()


def _text_tuple(
    values: Sequence[Any],
    name: str,
    *,
    agent_safe: bool = False,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError(f"{name} must be a sequence")
    cleaner = _agent_safe_text if agent_safe else _required_text
    cleaned = tuple(cleaner(value, name) for value in values)
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    if len(cleaned) != len(set(cleaned)):
        raise ValueError(f"{name} must contain unique values")
    return cleaned


def _id_tuple(
    values: Sequence[Any],
    name: str,
    *,
    allow_empty: bool,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError(f"{name} must be a sequence")
    cleaned = tuple(_required_id(value, name) for value in values)
    if not allow_empty and not cleaned:
        raise ValueError(f"{name} must not be empty")
    if len(cleaned) != len(set(cleaned)):
        raise ValueError(f"{name} must contain unique values")
    return cleaned


def _verification_plan(values: Sequence[Any]) -> tuple[str, ...]:
    cleaned = _text_tuple(values, "verification_plan")
    unknown = set(cleaned) - set(_VERIFICATION_ORDER)
    if unknown:
        raise ValueError(
            "verification_plan contains unsupported stages: "
            + ", ".join(sorted(unknown))
        )
    positions = [_VERIFICATION_ORDER[item] for item in cleaned]
    if positions != sorted(positions):
        raise ValueError("verification_plan must follow gate order")
    return cleaned


E = TypeVar("E", bound=Enum)


def _enum(value: Any, enum_type: type[E], name: str) -> E:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unsupported {name}: {value!r}") from exc


def _nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be nonnegative")
    return value


def _positive_int(value: Any, name: str) -> int:
    cleaned = _nonnegative_int(value, name)
    if cleaned < 1:
        raise ValueError(f"{name} must be positive")
    return cleaned


def _utc_timestamp(value: Any) -> str:
    cleaned = _required_text(value, "created_at_utc")
    normalized = cleaned[:-1] + "+00:00" if cleaned.endswith("Z") else cleaned
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("created_at_utc must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("created_at_utc must use UTC")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
