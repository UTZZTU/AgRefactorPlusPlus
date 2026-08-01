"""Injected deterministic candidate execution contracts for Stage 3.3.

No real compiler, model, CSIM, CSYNTH, or Vitis integration lives here.  The
fake executor produces S3.2-compatible typed qualification/PPA outcomes so the
state machine can be tested independently from external systems.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from typing import Any, Protocol, runtime_checkable

from .policy import BudgetIncrement
from .ppa import PpaEvidence, PpaReportFormat, PpaResourceUsage
from .provider import HypothesisRequest
from .qualification import (
    CandidateQualificationResult,
    QualificationStage,
    QualificationStatus,
    QualificationStepOutcome,
    QualificationStepRecord,
)
from .state import CandidateRecord, HypothesisRecord, OptimizationLevel


EXECUTION_SCHEMA_VERSION = 1


class FakeExecutionStatus(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    REVIEW_REQUIRED = "review_required"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class FakeExecutionOutcome:
    """One deterministic fake candidate outcome."""

    status: FakeExecutionStatus = FakeExecutionStatus.ACCEPTED
    latency_cycles_max: int = 100
    initiation_interval_max: int | None = 1
    max_resource_utilization_ratio: float | None = 0.10
    achieved_clock_period_ns: float | None = 4.0
    objective_feasible: bool | None = True
    reason_code: str = "fixture_outcome"
    source_suffix: str = ""
    comparison_context_identity_sha256: str = "a" * 64

    def __post_init__(self) -> None:
        if not isinstance(self.status, FakeExecutionStatus):
            object.__setattr__(self, "status", FakeExecutionStatus(self.status))
        if isinstance(self.latency_cycles_max, bool) or self.latency_cycles_max < 0:
            raise ValueError("latency_cycles_max must be non-negative")
        if (
            self.initiation_interval_max is not None
            and (
                isinstance(self.initiation_interval_max, bool)
                or self.initiation_interval_max < 0
            )
        ):
            raise ValueError("initiation_interval_max must be non-negative or null")
        if self.objective_feasible is not None and not isinstance(
            self.objective_feasible, bool
        ):
            raise TypeError("objective_feasible must be boolean or null")
        if self.status is not FakeExecutionStatus.ACCEPTED:
            object.__setattr__(self, "objective_feasible", None)
        if not isinstance(self.reason_code, str) or not self.reason_code.strip():
            raise ValueError("reason_code must not be empty")
        if not isinstance(self.source_suffix, str):
            raise TypeError("source_suffix must be a string")
        context = self.comparison_context_identity_sha256
        if (
            not isinstance(context, str)
            or len(context) != 64
            or any(char not in "0123456789abcdef" for char in context)
        ):
            raise ValueError(
                "comparison_context_identity_sha256 must be lowercase SHA-256"
            )


@dataclass(frozen=True, slots=True)
class CandidateExecutionRequest:
    """One selected branch passed to an injected executor."""

    run_id: str
    sequence: int
    candidate_id: str
    level: OptimizationLevel
    round_number: int
    parent_candidate: CandidateRecord
    parent_source: bytes
    hypothesis: HypothesisRecord
    budget_before: Mapping[str, Any] = field(default_factory=dict)

    schema_version = EXECUTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("run_id must not be empty")
        if isinstance(self.sequence, bool) or self.sequence < 1:
            raise ValueError("sequence must be positive")
        if self.candidate_id != f"cand-{self.sequence}":
            raise ValueError("candidate_id must match sequence")
        if not isinstance(self.level, OptimizationLevel):
            object.__setattr__(self, "level", OptimizationLevel(self.level))
        if isinstance(self.round_number, bool) or self.round_number < 1:
            raise ValueError("round_number must be positive")
        if not isinstance(self.parent_candidate, CandidateRecord):
            raise TypeError("parent_candidate must be CandidateRecord")
        if not isinstance(self.parent_source, bytes):
            raise TypeError("parent_source must be bytes")
        if not isinstance(self.hypothesis, HypothesisRecord):
            raise TypeError("hypothesis must be HypothesisRecord")
        if self.hypothesis.parent_candidate_id != self.parent_candidate.candidate_id:
            raise ValueError("hypothesis parent does not match execution parent")
        if self.hypothesis.level is not self.level:
            raise ValueError("hypothesis level does not match execution level")
        object.__setattr__(self, "run_id", self.run_id.strip())
        object.__setattr__(self, "budget_before", dict(self.budget_before))


@dataclass(frozen=True, slots=True)
class CandidateExecutionResult:
    """Source bytes plus one typed S3.2-compatible qualification result."""

    source: bytes
    qualification: CandidateQualificationResult

    def __post_init__(self) -> None:
        if not isinstance(self.source, bytes):
            raise TypeError("source must be bytes")
        if not self.source:
            raise ValueError("source must not be empty")
        if not isinstance(self.qualification, CandidateQualificationResult):
            raise TypeError("qualification must be CandidateQualificationResult")


@runtime_checkable
class CandidateExecutor(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def budget_increment(self) -> BudgetIncrement: ...

    @property
    def uses_network(self) -> bool: ...

    @property
    def uses_vitis(self) -> bool: ...

    def execute(self, request: CandidateExecutionRequest) -> CandidateExecutionResult: ...


class FakeCandidateExecutor:
    """Deterministic executor that never launches external tools."""

    def __init__(
        self,
        outcomes: Mapping[int | str, FakeExecutionOutcome] | None = None,
        *,
        default_outcome: FakeExecutionOutcome | None = None,
        budget_increment: BudgetIncrement | None = None,
        name: str = "fake-candidate-executor",
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must not be empty")
        normalized: dict[str, FakeExecutionOutcome] = {}
        for key, outcome in dict(outcomes or {}).items():
            candidate_id = f"cand-{key}" if isinstance(key, int) else str(key)
            if not isinstance(outcome, FakeExecutionOutcome):
                raise TypeError("outcomes values must be FakeExecutionOutcome")
            normalized[candidate_id] = outcome
        self._outcomes = normalized
        self._default = default_outcome or FakeExecutionOutcome()
        self._budget_increment = budget_increment or BudgetIncrement()
        self._name = name.strip()
        self._requests: list[CandidateExecutionRequest] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def budget_increment(self) -> BudgetIncrement:
        return self._budget_increment

    @property
    def uses_network(self) -> bool:
        return False

    @property
    def uses_vitis(self) -> bool:
        return False

    @property
    def requests(self) -> tuple[CandidateExecutionRequest, ...]:
        return tuple(self._requests)

    @property
    def call_count(self) -> int:
        return len(self._requests)

    def execute(self, request: CandidateExecutionRequest) -> CandidateExecutionResult:
        if not isinstance(request, CandidateExecutionRequest):
            raise TypeError("request must be CandidateExecutionRequest")
        self._requests.append(request)
        outcome = self._outcomes.get(request.candidate_id, self._default)
        source = self._candidate_source(request, outcome)
        qualification = self._qualification(request, outcome)
        return CandidateExecutionResult(source=source, qualification=qualification)

    def _candidate_source(
        self,
        request: CandidateExecutionRequest,
        outcome: FakeExecutionOutcome,
    ) -> bytes:
        suffix = outcome.source_suffix or (
            f"\n// S3.3 deterministic fixture {request.candidate_id} "
            f"from {request.hypothesis.hypothesis_id}\n"
        )
        return request.parent_source.rstrip(b"\n") + suffix.encode("utf-8")

    def _qualification(
        self,
        request: CandidateExecutionRequest,
        outcome: FakeExecutionOutcome,
    ) -> CandidateQualificationResult:
        status = QualificationStatus(outcome.status.value)
        accepted = status is QualificationStatus.ACCEPTED
        ppa = self._ppa(request, outcome) if accepted else None
        steps = self._steps(status, outcome.reason_code)
        cache_key = sha256(
            f"fake-cache:{request.run_id}:{request.candidate_id}".encode("utf-8")
        ).hexdigest()
        return CandidateQualificationResult(
            qualification_id=f"qual-{request.candidate_id}",
            candidate_id=request.candidate_id,
            status=status,
            steps=steps,
            correctness_passed=accepted,
            synthesis_passed=accepted,
            objective_feasible=(outcome.objective_feasible if accepted else None),
            ppa=ppa,
            cache_key_sha256=cache_key,
            cache_hit=False,
            budget_before=request.budget_before,
            budget_after=request.budget_before,
            decision={
                "action": f"qualification_{status.value}",
                "reason_code": outcome.reason_code,
                "executor": self.name,
                "physical_execution": False,
            },
        )

    def _ppa(
        self,
        request: CandidateExecutionRequest,
        outcome: FakeExecutionOutcome,
    ) -> PpaEvidence:
        report_payload = (
            f"{request.candidate_id}:{outcome.latency_cycles_max}:"
            f"{outcome.initiation_interval_max}:"
            f"{outcome.objective_feasible}"
        ).encode("utf-8")
        violations = (
            ()
            if outcome.objective_feasible is not False
            else ("fixture_objective_constraint_violation",)
        )
        return PpaEvidence(
            evidence_id=f"ppa-{request.candidate_id}",
            parser_profile="fake-s3.3",
            report_format=PpaReportFormat.XML,
            report_relative_path=f"fake_reports/{request.candidate_id}_csynth.xml",
            report_sha256=sha256(report_payload).hexdigest(),
            comparison_context_identity_sha256=(
                outcome.comparison_context_identity_sha256
            ),
            latency_cycles_min=outcome.latency_cycles_max,
            latency_cycles_max=outcome.latency_cycles_max,
            initiation_interval_min=outcome.initiation_interval_max,
            initiation_interval_max=outcome.initiation_interval_max,
            target_clock_period_ns=5.0,
            achieved_clock_period_ns=outcome.achieved_clock_period_ns,
            resources_used=PpaResourceUsage(lut=10, ff=10, dsp=1, bram_18k=1, uram=0),
            resources_available=PpaResourceUsage(
                lut=1000, ff=1000, dsp=100, bram_18k=100, uram=10
            ),
            max_resource_utilization_ratio=(
                outcome.max_resource_utilization_ratio
            ),
            objective_feasible=outcome.objective_feasible,
            constraint_violations=violations,
            parser_warnings=("deterministic_fixture_only",),
        )

    @staticmethod
    def _steps(
        status: QualificationStatus,
        reason_code: str,
    ) -> tuple[QualificationStepRecord, ...]:
        accepted = status is QualificationStatus.ACCEPTED
        terminal_outcome = {
            QualificationStatus.ACCEPTED: QualificationStepOutcome.PASSED,
            QualificationStatus.REJECTED: QualificationStepOutcome.FAILED,
            QualificationStatus.BLOCKED: QualificationStepOutcome.BLOCKED,
            QualificationStatus.REVIEW_REQUIRED: (
                QualificationStepOutcome.REVIEW_REQUIRED
            ),
            QualificationStatus.ERROR: QualificationStepOutcome.ERROR,
        }[status]
        stages = (
            QualificationStage.SOURCE,
            QualificationStage.PREFLIGHT,
            QualificationStage.PUBLIC,
            QualificationStage.CSYNTH,
            QualificationStage.HIDDEN,
            QualificationStage.PPA,
            QualificationStage.FEASIBILITY,
        )
        records: list[QualificationStepRecord] = []
        for index, stage in enumerate(stages):
            if accepted:
                outcome = QualificationStepOutcome.PASSED
                reasons: tuple[str, ...] = ("fixture_passed",)
            elif index == 0:
                outcome = terminal_outcome
                reasons = (reason_code,)
            else:
                outcome = QualificationStepOutcome.SKIPPED
                reasons = ("prior_stage_not_accepted",)
            records.append(
                QualificationStepRecord(
                    stage=stage,
                    outcome=outcome,
                    evidence_view=(
                        "operator_full"
                        if stage is QualificationStage.HIDDEN
                        else "internal_safe"
                    ),
                    route_action=None,
                    source="fake_s3_3_executor",
                    source_report_id=(
                        None
                        if stage is QualificationStage.HIDDEN
                        else f"fake-{stage.value}"
                    ),
                    source_item_count=0,
                    source_blocking=not accepted and index == 0,
                    reason_codes=reasons,
                    metadata={
                        "physical_execution": False,
                        "fixture": True,
                    },
                )
            )
        return tuple(records)
