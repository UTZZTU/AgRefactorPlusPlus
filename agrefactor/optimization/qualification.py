"""Independent Stage 3 qualification in the frozen cheap-to-expensive order."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
import json
import os
import re
from typing import Any

from agrefactor.evaluation.feedback_routing import (
    FeedbackRouteAction,
    FeedbackRouter,
)
from agrefactor.evidence import FeedbackReport
from agrefactor.runtime.runner import RunContext

from .cache import QualificationEvidenceCache, ValidationCacheIdentity
from .ppa import PpaEvidence, PpaParseError, VitisHlsPpaReportAdapter
from .state import (
    CandidateRecord,
    CandidateStatus,
    OptimizerState,
    OptimizerTerminalStatus,
)


QUALIFICATION_SCHEMA_VERSION = 1
QUALIFICATION_PIPELINE_VERSION = "stage3-qualification-v1"
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SAFE_METADATA_FIELDS = frozenset(
    {
        "physical_execution",
        "shared_budget",
        "stage_handler_version",
        "semantics_version",
        "tool_attempt_counted",
        "target_profile_name",
        "requested_toolchain_version",
        "parser_profile",
        "declared_suite_ids",
        "attempted_suite_ids",
        "declared_suite_count",
        "attempted_suite_count",
        "stopped_early",
        "stop_reason",
        "execution_policy",
        "category_counts",
        "severity_counts",
        "owner_counts",
        "preflight_status",
        "failure_kind",
        "failure_owner",
        "next_action",
        "preflight_reason_code",
        "preflight_reason_codes",
        "failed_component",
        "substep_count",
        "legacy_status",
        "execution_exception_type",
        "operator_invocation_available",
        "target_resource_limits",
    }
)


class QualificationStage(str, Enum):
    SOURCE = "source"
    PREFLIGHT = "preflight"
    PUBLIC = "public"
    CSYNTH = "csynth"
    HIDDEN = "hidden"
    PPA = "ppa"
    FEASIBILITY = "feasibility"


class QualificationStepOutcome(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    REVIEW_REQUIRED = "review_required"
    ERROR = "error"
    SKIPPED = "skipped"
    CACHE_HIT = "cache_hit"


class QualificationStatus(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    REVIEW_REQUIRED = "review_required"
    ERROR = "error"


QualificationStageHandler = Callable[[RunContext], FeedbackReport]


@dataclass(frozen=True, slots=True)
class QualificationStepRecord:
    """A safe aggregate record of one qualification stage."""

    stage: QualificationStage
    outcome: QualificationStepOutcome
    evidence_view: str
    route_action: FeedbackRouteAction | None
    source: str
    source_report_id: str | None
    source_item_count: int
    source_blocking: bool
    reason_codes: tuple[str, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    schema_version = QUALIFICATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        stage = _enum(self.stage, QualificationStage, "stage")
        outcome = _enum(
            self.outcome,
            QualificationStepOutcome,
            "outcome",
        )
        view = _required_id(self.evidence_view, "evidence_view")
        if view not in {"agent_safe", "operator_full", "internal_safe"}:
            raise ValueError("unsupported qualification evidence_view")
        action = (
            None
            if self.route_action is None
            else _enum(
                self.route_action,
                FeedbackRouteAction,
                "route_action",
            )
        )
        source = _required_id(self.source, "source")
        report_id = _optional_id(self.source_report_id, "source_report_id")
        item_count = _nonnegative_int(
            self.source_item_count,
            "source_item_count",
        )
        if not isinstance(self.source_blocking, bool):
            raise TypeError("source_blocking must be boolean")
        reasons = _id_tuple(self.reason_codes, "reason_codes")
        metadata = _json_mapping(self.metadata, "metadata")
        if stage is QualificationStage.HIDDEN:
            if view != "operator_full":
                raise ValueError("Hidden qualification stage must be operator_full")
            if report_id is not None:
                raise ValueError("Hidden source_report_id must be suppressed")
        elif view == "operator_full":
            raise ValueError("non-Hidden qualification stages cannot be operator_full")
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "evidence_view", view)
        object.__setattr__(self, "route_action", action)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "source_report_id", report_id)
        object.__setattr__(self, "source_item_count", item_count)
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(self, "metadata", metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "stage": self.stage.value,
            "outcome": self.outcome.value,
            "evidence_view": self.evidence_view,
            "route_action": (
                None if self.route_action is None else self.route_action.value
            ),
            "source": self.source,
            "source_report_id": self.source_report_id,
            "source_item_count": self.source_item_count,
            "source_blocking": self.source_blocking,
            "reason_codes": list(self.reason_codes),
            "metadata": _json_mapping(self.metadata, "metadata"),
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "QualificationStepRecord":
        value = _strict_payload(
            payload,
            {
                "schema_version",
                "stage",
                "outcome",
                "evidence_view",
                "route_action",
                "source",
                "source_report_id",
                "source_item_count",
                "source_blocking",
                "reason_codes",
                "metadata",
            },
            "qualification step",
        )
        return cls(
            stage=value["stage"],
            outcome=value["outcome"],
            evidence_view=value["evidence_view"],
            route_action=value["route_action"],
            source=value["source"],
            source_report_id=value["source_report_id"],
            source_item_count=value["source_item_count"],
            source_blocking=value["source_blocking"],
            reason_codes=tuple(value["reason_codes"]),
            metadata=value["metadata"],
        )


@dataclass(frozen=True, slots=True)
class CandidateQualificationRequest:
    qualification_id: str
    candidate: CandidateRecord
    source_path: str | os.PathLike[str]
    ppa_work_dir: str | os.PathLike[str]
    top_function: str
    cache_identity: ValidationCacheIdentity
    resource_limits: Mapping[str, int | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        qualification_id = _required_id(
            self.qualification_id,
            "qualification_id",
        )
        if not isinstance(self.candidate, CandidateRecord):
            raise TypeError("candidate must be CandidateRecord")
        if self.candidate.status is not CandidateStatus.GENERATED:
            raise ValueError("qualification candidate must start generated")
        source = _regular_file(self.source_path, "source_path")
        ppa_root = _directory_path(self.ppa_work_dir, "ppa_work_dir")
        top = _required_id(self.top_function, "top_function")
        if not isinstance(self.cache_identity, ValidationCacheIdentity):
            raise TypeError("cache_identity must be ValidationCacheIdentity")
        if self.cache_identity.source_sha256 != self.candidate.source_sha256:
            raise ValueError("cache source identity must match candidate source")
        limits = _json_mapping(self.resource_limits, "resource_limits")
        object.__setattr__(self, "qualification_id", qualification_id)
        object.__setattr__(self, "source_path", source)
        object.__setattr__(self, "ppa_work_dir", ppa_root)
        object.__setattr__(self, "top_function", top)
        object.__setattr__(self, "resource_limits", limits)


@dataclass(frozen=True, slots=True)
class CandidateQualificationResult:
    qualification_id: str
    candidate_id: str
    status: QualificationStatus
    steps: tuple[QualificationStepRecord, ...]
    correctness_passed: bool
    synthesis_passed: bool
    objective_feasible: bool | None
    ppa: PpaEvidence | None
    cache_key_sha256: str
    cache_hit: bool
    budget_before: Mapping[str, Any]
    budget_after: Mapping[str, Any]
    decision: Mapping[str, Any]

    schema_version = QUALIFICATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        qualification_id = _required_id(
            self.qualification_id,
            "qualification_id",
        )
        candidate_id = _required_id(self.candidate_id, "candidate_id")
        status = _enum(self.status, QualificationStatus, "status")
        steps = tuple(self.steps)
        if not steps or not all(
            isinstance(item, QualificationStepRecord) for item in steps
        ):
            raise ValueError("qualification result requires typed steps")
        if not isinstance(self.correctness_passed, bool):
            raise TypeError("correctness_passed must be boolean")
        if not isinstance(self.synthesis_passed, bool):
            raise TypeError("synthesis_passed must be boolean")
        if self.objective_feasible is not None and not isinstance(
            self.objective_feasible,
            bool,
        ):
            raise TypeError("objective_feasible must be boolean or null")
        if self.ppa is not None and not isinstance(self.ppa, PpaEvidence):
            raise TypeError("ppa must be PpaEvidence or null")
        cache_key = _sha256(self.cache_key_sha256, "cache_key_sha256")
        if not isinstance(self.cache_hit, bool):
            raise TypeError("cache_hit must be boolean")
        before = _json_mapping(self.budget_before, "budget_before")
        after = _json_mapping(self.budget_after, "budget_after")
        decision = _json_mapping(self.decision, "decision")
        if status is QualificationStatus.ACCEPTED:
            if not self.correctness_passed or not self.synthesis_passed:
                raise ValueError("accepted qualification requires correctness and synthesis")
            if self.ppa is None:
                raise ValueError("accepted qualification requires PPA evidence")
        elif self.objective_feasible is not None and self.ppa is None:
            raise ValueError("objective feasibility requires PPA evidence")
        object.__setattr__(self, "qualification_id", qualification_id)
        object.__setattr__(self, "candidate_id", candidate_id)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "steps", steps)
        object.__setattr__(self, "cache_key_sha256", cache_key)
        object.__setattr__(self, "budget_before", before)
        object.__setattr__(self, "budget_after", after)
        object.__setattr__(self, "decision", decision)

    @property
    def accepted(self) -> bool:
        return self.status is QualificationStatus.ACCEPTED

    @property
    def cacheable(self) -> bool:
        return self.status in {
            QualificationStatus.ACCEPTED,
            QualificationStatus.REJECTED,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "qualification_id": self.qualification_id,
            "candidate_id": self.candidate_id,
            "status": self.status.value,
            "steps": [item.to_dict() for item in self.steps],
            "correctness_passed": self.correctness_passed,
            "synthesis_passed": self.synthesis_passed,
            "objective_feasible": self.objective_feasible,
            "ppa": None if self.ppa is None else self.ppa.to_dict(),
            "cache_key_sha256": self.cache_key_sha256,
            "cache_hit": self.cache_hit,
            "budget_before": _json_mapping(
                self.budget_before,
                "budget_before",
            ),
            "budget_after": _json_mapping(
                self.budget_after,
                "budget_after",
            ),
            "decision": _json_mapping(self.decision, "decision"),
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "CandidateQualificationResult":
        value = _strict_payload(
            payload,
            {
                "schema_version",
                "qualification_id",
                "candidate_id",
                "status",
                "steps",
                "correctness_passed",
                "synthesis_passed",
                "objective_feasible",
                "ppa",
                "cache_key_sha256",
                "cache_hit",
                "budget_before",
                "budget_after",
                "decision",
            },
            "qualification result",
        )
        return cls(
            qualification_id=value["qualification_id"],
            candidate_id=value["candidate_id"],
            status=value["status"],
            steps=tuple(
                QualificationStepRecord.from_dict(item)
                for item in value["steps"]
            ),
            correctness_passed=value["correctness_passed"],
            synthesis_passed=value["synthesis_passed"],
            objective_feasible=value["objective_feasible"],
            ppa=(
                None
                if value["ppa"] is None
                else PpaEvidence.from_dict(value["ppa"])
            ),
            cache_key_sha256=value["cache_key_sha256"],
            cache_hit=value["cache_hit"],
            budget_before=value["budget_before"],
            budget_after=value["budget_after"],
            decision=value["decision"],
        )

    def to_cache_evidence(self) -> dict[str, Any]:
        if not self.cacheable:
            raise ValueError("only accepted/rejected qualification is cacheable")
        decision = dict(self.decision)
        decision.pop("candidate_id", None)
        return {
            "schema_version": self.schema_version,
            "status": self.status.value,
            "steps": [item.to_dict() for item in self.steps],
            "correctness_passed": self.correctness_passed,
            "synthesis_passed": self.synthesis_passed,
            "objective_feasible": self.objective_feasible,
            "ppa": None if self.ppa is None else self.ppa.to_dict(),
            "decision": decision,
        }

    @classmethod
    def from_cache_evidence(
        cls,
        evidence: Mapping[str, Any],
        *,
        qualification_id: str,
        candidate_id: str,
        cache_key_sha256: str,
        budget_snapshot: Mapping[str, Any],
    ) -> "CandidateQualificationResult":
        allowed = {
            "schema_version",
            "status",
            "steps",
            "correctness_passed",
            "synthesis_passed",
            "objective_feasible",
            "ppa",
            "decision",
        }
        value = _strict_payload(evidence, allowed, "cached qualification evidence")
        cached_steps = tuple(
            QualificationStepRecord.from_dict(item) for item in value["steps"]
        )
        steps = tuple(
            replace(
                item,
                metadata={
                    **dict(item.metadata),
                    "cache_reused": True,
                    "physical_execution": False,
                },
            )
            for item in cached_steps
        )
        decision = {**dict(value["decision"]), "candidate_id": candidate_id}
        return cls(
            qualification_id=qualification_id,
            candidate_id=candidate_id,
            status=value["status"],
            steps=steps,
            correctness_passed=value["correctness_passed"],
            synthesis_passed=value["synthesis_passed"],
            objective_feasible=value["objective_feasible"],
            ppa=(
                None
                if value["ppa"] is None
                else PpaEvidence.from_dict(value["ppa"])
            ),
            cache_key_sha256=cache_key_sha256,
            cache_hit=True,
            budget_before=budget_snapshot,
            budget_after=budget_snapshot,
            decision=decision,
        )

    def apply_to_candidate(
        self,
        candidate: CandidateRecord,
    ) -> CandidateRecord:
        if not isinstance(candidate, CandidateRecord):
            raise TypeError("candidate must be CandidateRecord")
        if candidate.candidate_id != self.candidate_id:
            raise ValueError("result candidate_id does not match candidate")
        current = candidate.transition_to(CandidateStatus.VALIDATING)
        target_status = {
            QualificationStatus.ACCEPTED: CandidateStatus.ACCEPTED,
            QualificationStatus.REJECTED: CandidateStatus.REJECTED,
            QualificationStatus.BLOCKED: CandidateStatus.BLOCKED,
            QualificationStatus.REVIEW_REQUIRED: CandidateStatus.BLOCKED,
            QualificationStatus.ERROR: CandidateStatus.ERROR,
        }[self.status]
        correctness = {
            "schema_version": self.schema_version,
            "qualification_id": self.qualification_id,
            "passed": self.correctness_passed,
            "stage_outcomes": {
                item.stage.value: item.outcome.value
                for item in self.steps
                if item.stage
                in {
                    QualificationStage.SOURCE,
                    QualificationStage.PREFLIGHT,
                    QualificationStage.PUBLIC,
                    QualificationStage.HIDDEN,
                }
            },
        }
        synthesis = {
            "schema_version": self.schema_version,
            "qualification_id": self.qualification_id,
            "passed": self.synthesis_passed,
            "ppa_evidence_id": (
                None if self.ppa is None else self.ppa.evidence_id
            ),
            "report_sha256": (
                None if self.ppa is None else self.ppa.report_sha256
            ),
        }
        return current.transition_to(
            target_status,
            correctness=correctness,
            synthesis=synthesis,
            ppa={} if self.ppa is None else self.ppa.to_dict(),
            budget_after=self.budget_after,
            decision=self.decision,
        )


class Stage3QualificationOrchestrator:
    """Run handlers in the Stage 3 order without changing Stage 2 semantics."""

    orchestrator_version = 1

    def __init__(
        self,
        handlers: Mapping[QualificationStage | str, QualificationStageHandler],
        *,
        ppa_adapter: VitisHlsPpaReportAdapter | None = None,
        cache: QualificationEvidenceCache | None = None,
        router: FeedbackRouter | None = None,
    ) -> None:
        normalized: dict[QualificationStage, QualificationStageHandler] = {}
        for raw_stage, handler in handlers.items():
            stage = _enum(raw_stage, QualificationStage, "handler stage")
            if stage not in {
                QualificationStage.PREFLIGHT,
                QualificationStage.PUBLIC,
                QualificationStage.CSYNTH,
                QualificationStage.HIDDEN,
            }:
                raise ValueError(f"handler is not valid for stage {stage.value}")
            if stage in normalized:
                raise ValueError(f"duplicate qualification handler: {stage.value}")
            if not callable(handler):
                raise TypeError(f"handler for {stage.value} must be callable")
            normalized[stage] = handler
        self._handlers = normalized
        self._ppa_adapter = ppa_adapter or VitisHlsPpaReportAdapter()
        self._cache = cache
        self._router = router or FeedbackRouter()

    def run(
        self,
        context: RunContext,
        request: CandidateQualificationRequest,
    ) -> CandidateQualificationResult:
        if not isinstance(context, RunContext):
            raise TypeError("context must be RunContext")
        if not isinstance(request, CandidateQualificationRequest):
            raise TypeError("request must be CandidateQualificationRequest")
        budget_before = context.budget.snapshot().to_dict()
        steps: list[QualificationStepRecord] = []

        source_step = self._source_step(request)
        steps.append(source_step)
        context.trace.record(
            "optimizer.qualification.source",
            phase="optimizer_qualification",
            status=source_step.outcome.value,
            metadata={
                "qualification_id": request.qualification_id,
                "candidate_id": request.candidate.candidate_id,
                "source_sha256": request.candidate.source_sha256,
            },
        )
        if source_step.outcome is not QualificationStepOutcome.PASSED:
            return self._finish(
                context,
                request,
                steps,
                status=QualificationStatus.REJECTED,
                correctness=False,
                synthesis=False,
                ppa=None,
                budget_before=budget_before,
                reason_codes=("source_validation_failed",),
            )

        if self._cache is not None:
            evidence = self._cache.load(request.cache_identity)
            if evidence is not None:
                snapshot = context.budget.snapshot().to_dict()
                result = CandidateQualificationResult.from_cache_evidence(
                    evidence,
                    qualification_id=request.qualification_id,
                    candidate_id=request.candidate.candidate_id,
                    cache_key_sha256=(
                        request.cache_identity.cache_key_sha256
                    ),
                    budget_snapshot=snapshot,
                )
                context.trace.record(
                    "optimizer.qualification.cache_hit",
                    phase="optimizer_qualification",
                    status=result.status.value,
                    metadata={
                        "qualification_id": request.qualification_id,
                        "candidate_id": request.candidate.candidate_id,
                        "cache_key_sha256": result.cache_key_sha256,
                        "real_tool_launches": 0,
                    },
                )
                return result

        required = [
            QualificationStage.PREFLIGHT,
            *(
                [QualificationStage.PUBLIC]
                if request.cache_identity.public_suites
                else []
            ),
            QualificationStage.CSYNTH,
            *(
                [QualificationStage.HIDDEN]
                if request.cache_identity.hidden_suites
                else []
            ),
        ]
        missing = [stage.value for stage in required if stage not in self._handlers]
        if missing:
            raise ValueError(
                "Missing Stage 3 qualification handlers: " + ", ".join(missing)
            )

        correctness = True
        synthesis = False
        for stage in required:
            handler_result = self._run_handler(
                context,
                request,
                stage,
            )
            steps.append(handler_result[0])
            terminal_status = handler_result[1]
            if terminal_status is not None:
                if stage in {
                    QualificationStage.PREFLIGHT,
                    QualificationStage.PUBLIC,
                    QualificationStage.HIDDEN,
                }:
                    correctness = False
                return self._finish(
                    context,
                    request,
                    steps,
                    status=terminal_status,
                    correctness=correctness,
                    synthesis=synthesis,
                    ppa=None,
                    budget_before=budget_before,
                    reason_codes=steps[-1].reason_codes,
                )
            if stage is QualificationStage.CSYNTH:
                synthesis = True

        try:
            ppa = self._ppa_adapter.parse(
                request.ppa_work_dir,
                top_function=request.top_function,
                parser_profile=request.cache_identity.parser_profile,
                comparison_context_identity_sha256=(
                    request.cache_identity.comparison_context_identity_sha256
                ),
                resource_limits=request.resource_limits,
                evidence_id=(
                    f"{request.qualification_id}.ppa"
                ),
            )
        except (PpaParseError, FileNotFoundError, TypeError, ValueError) as exc:
            steps.append(
                QualificationStepRecord(
                    stage=QualificationStage.PPA,
                    outcome=QualificationStepOutcome.REVIEW_REQUIRED,
                    evidence_view="internal_safe",
                    route_action=None,
                    source="ppa_report_adapter",
                    source_report_id=None,
                    source_item_count=0,
                    source_blocking=True,
                    reason_codes=("ppa_report_unusable",),
                    metadata={
                        "adapter_version": self._ppa_adapter.adapter_version,
                        "error_type": type(exc).__name__,
                    },
                )
            )
            return self._finish(
                context,
                request,
                steps,
                status=QualificationStatus.REVIEW_REQUIRED,
                correctness=True,
                synthesis=True,
                ppa=None,
                budget_before=budget_before,
                reason_codes=("ppa_report_unusable",),
            )

        steps.append(
            QualificationStepRecord(
                stage=QualificationStage.PPA,
                outcome=QualificationStepOutcome.PASSED,
                evidence_view="internal_safe",
                route_action=None,
                source="ppa_report_adapter",
                source_report_id=ppa.evidence_id,
                source_item_count=1,
                source_blocking=False,
                reason_codes=("ppa_report_parsed",),
                metadata={
                    "adapter_version": self._ppa_adapter.adapter_version,
                    "report_format": ppa.report_format.value,
                    "report_sha256": ppa.report_sha256,
                    "latency_cycles_max": ppa.latency_cycles_max,
                },
            )
        )
        if ppa.objective_feasible is None:
            feasibility_outcome = QualificationStepOutcome.REVIEW_REQUIRED
            status = QualificationStatus.REVIEW_REQUIRED
            reasons = ("objective_feasibility_unknown",)
        elif ppa.objective_feasible is False:
            feasibility_outcome = QualificationStepOutcome.PASSED
            status = QualificationStatus.ACCEPTED
            reasons = ("correct_but_objective_infeasible",)
        else:
            feasibility_outcome = QualificationStepOutcome.PASSED
            status = QualificationStatus.ACCEPTED
            reasons = ("objective_feasible",)
        steps.append(
            QualificationStepRecord(
                stage=QualificationStage.FEASIBILITY,
                outcome=feasibility_outcome,
                evidence_view="internal_safe",
                route_action=None,
                source="objective_feasibility",
                source_report_id=ppa.evidence_id,
                source_item_count=len(ppa.constraint_violations),
                source_blocking=(
                    feasibility_outcome
                    is QualificationStepOutcome.REVIEW_REQUIRED
                ),
                reason_codes=reasons,
                metadata={
                    "objective": "latency",
                    "objective_feasible": ppa.objective_feasible,
                    "constraint_violation_count": len(
                        ppa.constraint_violations
                    ),
                },
            )
        )
        result = self._finish(
            context,
            request,
            steps,
            status=status,
            correctness=True,
            synthesis=True,
            ppa=ppa,
            budget_before=budget_before,
            reason_codes=reasons,
        )
        if self._cache is not None and result.cacheable:
            self._cache.store(
                request.cache_identity,
                result.to_cache_evidence(),
            )
            context.trace.record(
                "optimizer.qualification.cache_store",
                phase="optimizer_qualification",
                status=result.status.value,
                metadata={
                    "qualification_id": request.qualification_id,
                    "cache_key_sha256": result.cache_key_sha256,
                },
            )
        return result

    def _source_step(
        self,
        request: CandidateQualificationRequest,
    ) -> QualificationStepRecord:
        from hashlib import sha256

        path = Path(request.source_path)
        actual = sha256(path.read_bytes()).hexdigest()
        passed = actual == request.candidate.source_sha256
        return QualificationStepRecord(
            stage=QualificationStage.SOURCE,
            outcome=(
                QualificationStepOutcome.PASSED
                if passed
                else QualificationStepOutcome.FAILED
            ),
            evidence_view="internal_safe",
            route_action=None,
            source="source_schema_validation",
            source_report_id=None,
            source_item_count=0 if passed else 1,
            source_blocking=not passed,
            reason_codes=(
                "source_sha256_verified"
                if passed
                else "source_sha256_mismatch"
            ,),
            metadata={
                "expected_source_sha256": request.candidate.source_sha256,
                "actual_source_sha256": actual,
            },
        )

    def _run_handler(
        self,
        context: RunContext,
        request: CandidateQualificationRequest,
        stage: QualificationStage,
    ) -> tuple[QualificationStepRecord, QualificationStatus | None]:
        handler = self._handlers[stage]
        context.trace.record(
            "optimizer.qualification.stage_started",
            phase=stage.value,
            status="running",
            metadata={
                "qualification_id": request.qualification_id,
                "candidate_id": request.candidate.candidate_id,
                "stage": stage.value,
            },
        )
        try:
            report = handler(context)
        except Exception as exc:  # noqa: BLE001 - converted to safe terminal evidence.
            step = QualificationStepRecord(
                stage=stage,
                outcome=QualificationStepOutcome.ERROR,
                evidence_view=(
                    "operator_full"
                    if stage is QualificationStage.HIDDEN
                    else "agent_safe"
                ),
                route_action=None,
                source="handler_exception",
                source_report_id=None,
                source_item_count=0,
                source_blocking=True,
                reason_codes=("handler_exception",),
                metadata={"error_type": type(exc).__name__},
            )
            context.trace.record(
                "optimizer.qualification.stage_finished",
                phase=stage.value,
                status="error",
                metadata={
                    "qualification_id": request.qualification_id,
                    "stage": stage.value,
                    "error_type": type(exc).__name__,
                },
            )
            return step, QualificationStatus.ERROR
        if not isinstance(report, FeedbackReport):
            raise TypeError(
                f"qualification handler {stage.value} returned "
                f"{type(report).__name__}, expected FeedbackReport"
            )
        decision = self._router.route(
            report,
            decision_id=f"{request.qualification_id}.{stage.value}.decision",
        )
        hidden = stage is QualificationStage.HIDDEN
        if decision.action is FeedbackRouteAction.CONTINUE_VALIDATION:
            outcome = QualificationStepOutcome.PASSED
            terminal = None
            reasons = (f"{stage.value}_passed",)
        else:
            terminal, outcome, reasons = _terminal_from_route(
                decision.action,
                stage,
            )
            if stage is QualificationStage.PREFLIGHT:
                typed_reason = report.metadata.get(
                    "preflight_reason_code"
                )
                if (
                    isinstance(typed_reason, str)
                    and typed_reason
                    and typed_reason != "passed"
                ):
                    reasons = tuple(
                        dict.fromkeys(
                            (typed_reason, *reasons)
                        )
                    )
        step = QualificationStepRecord(
            stage=stage,
            outcome=outcome,
            evidence_view="operator_full" if hidden else "agent_safe",
            route_action=decision.action,
            source=_safe_source_name(report.source),
            source_report_id=None if hidden else report.report_id,
            source_item_count=len(report.items),
            source_blocking=report.blocking,
            reason_codes=reasons,
            metadata=_safe_report_metadata(report.metadata),
        )
        context.trace.record(
            "optimizer.qualification.stage_finished",
            phase=stage.value,
            status=outcome.value,
            metadata={
                "qualification_id": request.qualification_id,
                "candidate_id": request.candidate.candidate_id,
                "stage": stage.value,
                "route_action": decision.action.value,
                "hidden_source_suppressed": hidden,
                "item_count": len(report.items),
                "blocking": report.blocking,
            },
        )
        return step, terminal

    def _finish(
        self,
        context: RunContext,
        request: CandidateQualificationRequest,
        steps: list[QualificationStepRecord],
        *,
        status: QualificationStatus,
        correctness: bool,
        synthesis: bool,
        ppa: PpaEvidence | None,
        budget_before: Mapping[str, Any],
        reason_codes: tuple[str, ...],
    ) -> CandidateQualificationResult:
        budget_after = context.budget.snapshot().to_dict()
        objective_feasible = None if ppa is None else ppa.objective_feasible
        decision_name = {
            QualificationStatus.ACCEPTED: "update_best",
            QualificationStatus.REJECTED: "reject",
            QualificationStatus.BLOCKED: "block",
            QualificationStatus.REVIEW_REQUIRED: "review_required",
            QualificationStatus.ERROR: "block",
        }[status]
        decision = {
            "schema_version": self.orchestrator_version,
            "candidate_id": request.candidate.candidate_id,
            "decision": decision_name,
            "correctness_passed": correctness,
            "synthesis_passed": synthesis,
            "objective_feasible": objective_feasible,
            "comparison": {
                "better": None,
                "reason": (
                    "baseline_initial_best"
                    if status is QualificationStatus.ACCEPTED
                    else "not_compared"
                ),
            },
            "rollback_to_candidate_id": (
                request.candidate.candidate_id
                if status is QualificationStatus.ACCEPTED
                and request.candidate.is_baseline
                else None
            ),
            "reason_codes": list(reason_codes),
        }
        result = CandidateQualificationResult(
            qualification_id=request.qualification_id,
            candidate_id=request.candidate.candidate_id,
            status=status,
            steps=tuple(steps),
            correctness_passed=correctness,
            synthesis_passed=synthesis,
            objective_feasible=objective_feasible,
            ppa=ppa,
            cache_key_sha256=request.cache_identity.cache_key_sha256,
            cache_hit=False,
            budget_before=budget_before,
            budget_after=budget_after,
            decision=decision,
        )
        context.trace.record(
            "optimizer.qualification.finished",
            phase="optimizer_qualification",
            status=status.value,
            metadata={
                "qualification_id": request.qualification_id,
                "candidate_id": request.candidate.candidate_id,
                "status": status.value,
                "correctness_passed": correctness,
                "synthesis_passed": synthesis,
                "objective_feasible": objective_feasible,
                "cache_hit": False,
                "step_order": [item.stage.value for item in steps],
            },
        )
        return result


def initialize_qualified_baseline(
    state: OptimizerState,
    baseline: CandidateRecord,
    result: CandidateQualificationResult,
) -> OptimizerState:
    """Initialize best_correct and, when feasible, best_ppa from baseline."""

    if not isinstance(state, OptimizerState):
        raise TypeError("state must be OptimizerState")
    if not isinstance(baseline, CandidateRecord):
        raise TypeError("baseline must be CandidateRecord")
    if not isinstance(result, CandidateQualificationResult):
        raise TypeError("result must be CandidateQualificationResult")
    if not baseline.is_baseline or result.candidate_id != baseline.candidate_id:
        raise ValueError("baseline qualification linkage is invalid")
    if result.accepted:
        if baseline.status is not CandidateStatus.ACCEPTED:
            raise ValueError("accepted result requires accepted baseline record")
        initialized = state.with_qualified_baseline(baseline)
        if result.objective_feasible is True:
            initialized = replace(
                initialized,
                best_ppa_candidate_id=baseline.candidate_id,
            )
        initialized.validate_against_candidates({baseline.candidate_id: baseline})
        return initialized
    terminal = {
        QualificationStatus.REJECTED: OptimizerTerminalStatus.BASELINE_REJECTED,
        QualificationStatus.BLOCKED: OptimizerTerminalStatus.BLOCKED,
        QualificationStatus.REVIEW_REQUIRED: (
            OptimizerTerminalStatus.REVIEW_REQUIRED
        ),
        QualificationStatus.ERROR: OptimizerTerminalStatus.ERROR,
        QualificationStatus.ACCEPTED: None,
    }[result.status]
    assert terminal is not None
    return replace(state, terminal_status=terminal)


def _terminal_from_route(
    action: FeedbackRouteAction,
    stage: QualificationStage,
) -> tuple[
    QualificationStatus,
    QualificationStepOutcome,
    tuple[str, ...],
]:
    if action is FeedbackRouteAction.STOP_BUDGET_EXHAUSTED:
        return (
            QualificationStatus.BLOCKED,
            QualificationStepOutcome.BLOCKED,
            ("budget_exhausted",),
        )
    if action in {
        FeedbackRouteAction.FIX_TOOLCHAIN,
        FeedbackRouteAction.FIX_CONFIGURATION,
        FeedbackRouteAction.FIX_TASK_INPUT,
    }:
        return (
            QualificationStatus.BLOCKED,
            QualificationStepOutcome.BLOCKED,
            (action.value,),
        )
    if action in {
        FeedbackRouteAction.REVIEW_UNKNOWN,
        FeedbackRouteAction.REVIEW_MIXED,
    }:
        return (
            QualificationStatus.REVIEW_REQUIRED,
            QualificationStepOutcome.REVIEW_REQUIRED,
            (action.value,),
        )
    if action in {
        FeedbackRouteAction.REPAIR_TESTBENCH,
        FeedbackRouteAction.REPAIR_CANDIDATE,
        FeedbackRouteAction.REPAIR_ORIGINAL,
    }:
        return (
            QualificationStatus.REJECTED,
            QualificationStepOutcome.FAILED,
            (f"{stage.value}_failed", action.value),
        )
    raise ValueError(f"unsupported terminal route action: {action.value}")


def _safe_report_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    selected = {
        key: child
        for key, child in value.items()
        if key in _SAFE_METADATA_FIELDS
    }
    return _json_mapping(selected, "safe report metadata")


def _safe_source_name(value: str) -> str:
    cleaned = value.strip().lower().replace(" ", "_")
    cleaned = re.sub(r"[^a-z0-9._-]", "_", cleaned)
    return cleaned or "validation_handler"


def _regular_file(value: str | os.PathLike[str], name: str) -> Path:
    path = Path(os.fspath(value)).expanduser()
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError(f"{name} must be a regular file: {path}")
    return path.resolve()


def _directory_path(value: str | os.PathLike[str], name: str) -> Path:
    path = Path(os.fspath(value)).expanduser()
    if path.exists() and (path.is_symlink() or not path.is_dir()):
        raise ValueError(f"{name} must be a real directory")
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _strict_payload(
    payload: Mapping[str, Any],
    allowed: set[str],
    name: str,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TypeError(f"{name} payload must be a mapping")
    unknown = set(payload) - allowed
    missing = allowed - set(payload)
    if unknown or missing:
        raise ValueError(
            f"{name} fields mismatch: unknown={sorted(unknown)} "
            f"missing={sorted(missing)}"
        )
    if payload.get("schema_version") != QUALIFICATION_SCHEMA_VERSION:
        raise ValueError(f"unsupported {name} schema_version")
    return _json_mapping(payload, name)


def _json_mapping(value: Mapping[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        copied = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite JSON") from exc
    if not isinstance(copied, dict):
        raise TypeError(f"{name} must normalize to an object")
    return copied


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    return cleaned


def _required_id(value: Any, name: str) -> str:
    cleaned = _required_text(value, name)
    if _SAFE_ID_RE.fullmatch(cleaned) is None:
        raise ValueError(f"{name} contains unsafe characters")
    return cleaned


def _optional_id(value: Any, name: str) -> str | None:
    return None if value is None else _required_id(value, name)


def _id_tuple(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, Mapping)):
        raise TypeError(f"{name} must be a sequence")
    result = tuple(_required_id(item, name) for item in value)
    if not result:
        raise ValueError(f"{name} must not be empty")
    if len(result) != len(set(result)):
        raise ValueError(f"{name} values must be unique")
    return result


def _nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _sha256(value: Any, name: str) -> str:
    cleaned = _required_text(value, name).lower()
    if re.fullmatch(r"[0-9a-f]{64}", cleaned) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return cleaned


def _enum(value: Any, enum_type: type[Enum], name: str) -> Any:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except ValueError as exc:
        raise ValueError(f"unsupported {name}: {value!r}") from exc
