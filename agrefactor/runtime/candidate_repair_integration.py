"""Safely compose validation orchestration with bounded candidate repair."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any, Protocol

from agrefactor.config import (
    DEFAULT_CANDIDATE_REPAIR_ATTEMPTS,
    DEFAULT_COSIM_TIMEOUT_S,
    EvaluationSplit,
    TaskSpec,
    validate_cosim_timeout_s,
    validate_repair_attempts,
)
from agrefactor.evaluation import (
    FeedbackRouteAction,
    TestbenchPreflight,
    ValidationState,
)
from agrefactor.evidence import FeedbackOwner
from agrefactor.evidence import (
    DiagnosticEventProjector,
    audit_testbench_semantic_revision,
    build_testbench_semantic_revision,
    testbench_revision_authorization,
)
from agrefactor.models import CandidateModelAdapter
from agrefactor.recovery import (
    DiagnosticAdvisor,
    RecoveryAction,
    RecoveryAuthority,
    RecoveryLedger,
    RecoveryPolicy,
    RecoveryRequest,
    RecoveryRole,
    RecoveryStage,
    conservative_v1_policy,
    default_restart_reserve,
    build_effective_repair_quota_summary,
    run_shadow_diagnostics,
)
from agrefactor.testing import (
    TestbenchRepairLoop,
    build_openai_compatible_testbench_repairer,
)
from agrefactor.repair import (
    BoundedCandidateRepairLoop,
    CandidateRepairLoopRequest,
    CandidateRepairLoopResult,
    CandidateRepairStopReason,
    RepairArtifactWriteResult,
    CandidateValidationRequest,
    CandidateValidationResult,
)
from agrefactor.runtime.budget import (
    BudgetExceededError,
    BudgetManager,
    BudgetUsage,
)

from .runner import RunContext
from .validation_orchestrator import (
    ValidationExecutionOutcome,
    ValidationOrchestrationResult,
    ValidationOrchestrator,
    ValidationStageHandler,
)


def _select_family_instruction(
    resolved_instruction: str | None,
    request_instruction: str | None,
) -> tuple[str | None, str]:
    """Select one family instruction without accepting conflicts."""

    _optional_text(
        resolved_instruction,
        "resolved_instruction",
    )
    _optional_text(
        request_instruction,
        "request_instruction",
    )
    if (
        resolved_instruction is not None
        and request_instruction is not None
        and resolved_instruction != request_instruction
    ):
        raise ValueError(
            "request family_instruction conflicts with "
            "EffectiveModelConfig.family_instruction"
        )
    if resolved_instruction is not None:
        return resolved_instruction, "effective_model_config"
    if request_instruction is not None:
        return request_instruction, "request_compatibility"
    return None, "none"


class CandidateRepairOrchestrationStatus(str, Enum):
    """Terminal status of repair-aware validation orchestration."""

    ACCEPTED = "accepted"
    VALIDATION_TERMINAL = "validation_terminal"
    REPAIR_NOT_APPLICABLE = "repair_not_applicable"
    REPAIR_EXHAUSTED = "repair_exhausted"
    BUDGET_EXHAUSTED = "budget_exhausted"
    VALIDATOR_ERROR = "validator_error"


@dataclass(frozen=True, slots=True)
class CandidateValidationPlanRequest:
    """Immutable inputs used to construct handlers for one candidate."""

    task: TaskSpec
    candidate_code: str
    original_code: str
    preflight_testbench_code: str
    suite_testbench_codes: Mapping[str, str]
    attempt: int
    validation_id: str
    reference_top_function: str | None = None
    candidate_top_function: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.task, TaskSpec):
            raise TypeError("task must be a TaskSpec")
        _required_text(self.candidate_code, "candidate_code")
        _required_text(self.original_code, "original_code")
        _required_text(
            self.preflight_testbench_code,
            "preflight_testbench_code",
        )
        if isinstance(self.attempt, bool) or not isinstance(
            self.attempt,
            int,
        ):
            raise TypeError("attempt must be an integer")
        if self.attempt < 0:
            raise ValueError("attempt must be non-negative")
        _required_text(self.validation_id, "validation_id")
        _optional_text(
            self.reference_top_function,
            "reference_top_function",
        )
        _optional_text(
            self.candidate_top_function,
            "candidate_top_function",
        )
        object.__setattr__(
            self,
            "suite_testbench_codes",
            _copy_code_mapping(
                self.suite_testbench_codes,
                "suite_testbench_codes",
            ),
        )


class CandidateValidationHandlerFactory(Protocol):
    """Build a fresh validation handler set for one candidate."""

    def build(
        self,
        request: CandidateValidationPlanRequest,
    ) -> Mapping[ValidationState | str, ValidationStageHandler]:
        """Return handlers that validate exactly the supplied candidate."""


@dataclass(frozen=True, slots=True)
class CandidateRepairOrchestrationRequest:
    """Inputs for one initial validation plus bounded candidate repair."""

    initial_candidate: str
    original_code: str
    preflight_testbench_code: str
    suite_testbench_codes: Mapping[str, str]
    prompt_public_testbench_code: str | None
    max_attempts: int = DEFAULT_CANDIDATE_REPAIR_ATTEMPTS
    family_instruction: str | None = None
    reference_top_function: str | None = None
    candidate_top_function: str | None = None
    approved_memory_snippets: tuple[str, ...] = ()
    recovery_profile: str = "conservative-v1"
    llm_advisory_mode: str = "off"

    def __post_init__(self) -> None:
        _required_text(self.initial_candidate, "initial_candidate")
        _required_text(self.original_code, "original_code")
        _required_text(
            self.preflight_testbench_code,
            "preflight_testbench_code",
        )
        _optional_text(
            self.prompt_public_testbench_code,
            "prompt_public_testbench_code",
        )
        validate_repair_attempts(
            self.max_attempts,
            field_name="max_attempts",
        )
        _optional_text(
            self.family_instruction,
            "family_instruction",
        )
        _optional_text(
            self.reference_top_function,
            "reference_top_function",
        )
        _optional_text(
            self.candidate_top_function,
            "candidate_top_function",
        )
        if not isinstance(
            self.approved_memory_snippets,
            tuple,
        ):
            raise TypeError(
                "approved_memory_snippets must be a tuple"
            )
        for item in self.approved_memory_snippets:
            _required_text(item, "approved_memory_snippets")
        if self.recovery_profile != "conservative-v1":
            raise ValueError("recovery_profile must be conservative-v1")
        if self.llm_advisory_mode not in {"off", "candidate-only"}:
            raise ValueError(
                "llm_advisory_mode must be off or candidate-only"
            )
        object.__setattr__(
            self,
            "suite_testbench_codes",
            _copy_code_mapping(
                self.suite_testbench_codes,
                "suite_testbench_codes",
            ),
        )


@dataclass(frozen=True, slots=True)
class CandidateRepairOrchestrationResult:
    """Safe result from validation plus optional bounded repair."""

    validation_id: str
    status: CandidateRepairOrchestrationStatus
    initial_candidate: str
    final_candidate: str
    initial_validation: ValidationOrchestrationResult
    repair_result: CandidateRepairLoopResult | None
    candidate_validations: tuple[
        ValidationOrchestrationResult,
        ...,
    ]
    last_validation_state: ValidationState
    budget_usage: BudgetUsage
    metadata: Mapping[str, Any] = field(default_factory=dict)

    schema_version = 1

    def __post_init__(self) -> None:
        _required_text(self.validation_id, "validation_id")
        status = (
            self.status
            if isinstance(
                self.status,
                CandidateRepairOrchestrationStatus,
            )
            else CandidateRepairOrchestrationStatus(
                str(self.status)
            )
        )
        _required_text(
            self.initial_candidate,
            "initial_candidate",
        )
        _required_text(self.final_candidate, "final_candidate")
        if not isinstance(
            self.initial_validation,
            ValidationOrchestrationResult,
        ):
            raise TypeError(
                "initial_validation must be "
                "ValidationOrchestrationResult"
            )
        if (
            self.repair_result is not None
            and not isinstance(
                self.repair_result,
                CandidateRepairLoopResult,
            )
        ):
            raise TypeError(
                "repair_result must be "
                "CandidateRepairLoopResult or None"
            )
        validations = tuple(self.candidate_validations)
        if not all(
            isinstance(
                item,
                ValidationOrchestrationResult,
            )
            for item in validations
        ):
            raise TypeError(
                "candidate_validations must contain "
                "ValidationOrchestrationResult values"
            )
        state = _state(self.last_validation_state)
        if not isinstance(self.budget_usage, BudgetUsage):
            raise TypeError("budget_usage must be BudgetUsage")
        metadata = _json_mapping(
            self.metadata,
            "metadata",
        )

        if status is CandidateRepairOrchestrationStatus.ACCEPTED:
            if state is not ValidationState.ACCEPTED:
                raise ValueError(
                    "accepted orchestration requires "
                    "last_validation_state=accepted"
                )
        if (
            self.repair_result is None
            and validations
        ):
            raise ValueError(
                "candidate validations require a repair result"
            )
        if (
            self.repair_result is not None
            and len(validations)
            > len(self.repair_result.attempts)
        ):
            raise ValueError(
                "candidate validation count cannot exceed "
                "repair attempts"
            )

        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "candidate_validations",
            validations,
        )
        object.__setattr__(
            self,
            "last_validation_state",
            state,
        )
        object.__setattr__(self, "metadata", metadata)

    @property
    def accepted(self) -> bool:
        return (
            self.status
            is CandidateRepairOrchestrationStatus.ACCEPTED
        )

    def write_repair_artifacts(
        self,
        root,
    ) -> RepairArtifactWriteResult:
        if self.repair_result is None:
            raise ValueError(
                "orchestration result has no repair attempts"
            )
        return self.repair_result.write_artifacts(
            root,
            run_id=self.validation_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "validation_id": self.validation_id,
            "status": self.status.value,
            "accepted": self.accepted,
            "initial_candidate": self.initial_candidate,
            "final_candidate": self.final_candidate,
            "initial_validation": (
                self.initial_validation.to_dict()
            ),
            "repair_result": (
                None
                if self.repair_result is None
                else self.repair_result.to_dict()
            ),
            "candidate_validations": [
                item.to_dict()
                for item in self.candidate_validations
            ],
            "last_validation_state": (
                self.last_validation_state.value
            ),
            "budget_usage": self.budget_usage.to_dict(),
            "metadata": _json_mapping(
                self.metadata,
                "metadata",
            ),
        }


class LocalCandidateValidationHandlerFactory:
    """Build the existing real local handlers for each candidate."""

    def __init__(
        self,
        work_root: str | os.PathLike[str],
        *,
        csynth_timelimit: int = 300,
        csim_timelimit: int = 60,
        cosim_timelimit: int = DEFAULT_COSIM_TIMEOUT_S,
        cosim_policy: str = "required",
    ) -> None:
        try:
            raw_root = os.fspath(work_root)
        except TypeError as exc:
            raise TypeError(
                "work_root must be path-like"
            ) from exc
        if not isinstance(raw_root, str) or not raw_root.strip():
            raise ValueError("work_root must not be empty")
        for name, value in (
            ("csynth_timelimit", csynth_timelimit),
            ("csim_timelimit", csim_timelimit),
            ("cosim_timelimit", cosim_timelimit),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        self._work_root = Path(raw_root).expanduser()
        self._csynth_timelimit = csynth_timelimit
        self._csim_timelimit = csim_timelimit
        self._cosim_timelimit = validate_cosim_timeout_s(cosim_timelimit)
        from .cosim_stage import validate_cosim_policy
        self._cosim_policy = validate_cosim_policy(cosim_policy)

    @property
    def work_root(self) -> Path:
        return self._work_root

    def build(
        self,
        request: CandidateValidationPlanRequest,
    ) -> Mapping[ValidationState, ValidationStageHandler]:
        if not isinstance(
            request,
            CandidateValidationPlanRequest,
        ):
            raise TypeError(
                "request must be CandidateValidationPlanRequest"
            )

        from .csim_stage import (
            CsimStageInputs,
            CsimValidationStageHandler,
        )
        from .cosim_stage import (
            CosimStageInputs,
            CosimValidationStageHandler,
        )
        from .csynth_stage import (
            CsynthStageInputs,
            CsynthValidationStageHandler,
        )
        from .preflight_stage import (
            PreflightStageInputs,
            PreflightValidationStageHandler,
        )

        run_dir = (
            self._work_root
            / _safe_component(request.validation_id)
            / f"attempt_{request.attempt:03d}"
        )
        handlers: dict[
            ValidationState,
            ValidationStageHandler,
        ] = {
            ValidationState.PREFLIGHT: (
                PreflightValidationStageHandler(
                    PreflightStageInputs(
                        work_dir=run_dir / "preflight",
                        testbench_code=(
                            request.preflight_testbench_code
                        ),
                        original_code=request.original_code,
                        candidate_code=request.candidate_code,
                        original_top_function=(
                            request.reference_top_function
                        ),
                        candidate_top_function=(
                            request.candidate_top_function
                            or request.task.kernel_name
                        ),
                    )
                )
            ),
            ValidationState.CSYNTH: (
                CsynthValidationStageHandler(
                    CsynthStageInputs(
                        work_dir=run_dir / "csynth",
                        candidate_code=request.candidate_code,
                        timelimit=self._csynth_timelimit,
                    )
                )
            ),
        }

        if any(
            suite.split is EvaluationSplit.PUBLIC
            for suite in request.task.test_suites
        ):
            handlers[
                ValidationState.PUBLIC_EVALUATION
            ] = CsimValidationStageHandler(
                CsimStageInputs(
                    work_dir=run_dir / "csim",
                    original_code=request.original_code,
                    candidate_code=request.candidate_code,
                    suite_testbench_codes=(
                        request.suite_testbench_codes
                    ),
                    timelimit=self._csim_timelimit,
                    execution_backend="native_vitis",
                    candidate_top_function=(
                        request.candidate_top_function
                        or request.task.kernel_name
                    ),
                    target_profile=request.task.target,
                ),
                split=EvaluationSplit.PUBLIC,
            )

        if any(
            suite.split is EvaluationSplit.PUBLIC
            for suite in request.task.test_suites
        ):
            handlers[ValidationState.PUBLIC_COSIM] = CosimValidationStageHandler(
                CosimStageInputs(
                    work_dir=run_dir / "public_cosim",
                    original_code=request.original_code,
                    candidate_code=request.candidate_code,
                    suite_testbench_codes={
                        suite.suite_id: request.suite_testbench_codes[suite.suite_id]
                        for suite in request.task.test_suites
                        if suite.split is EvaluationSplit.PUBLIC
                    },
                    candidate_top_function=(
                        request.candidate_top_function or request.task.kernel_name
                    ),
                    target_profile=request.task.target,
                    timelimit=self._cosim_timelimit,
                    policy=self._cosim_policy,
                )
            )

        if any(
            suite.split is EvaluationSplit.HIDDEN
            for suite in request.task.test_suites
        ):
            handlers[
                ValidationState.HIDDEN_EVALUATION
            ] = CsimValidationStageHandler(
                CsimStageInputs(
                    work_dir=run_dir / "csim",
                    original_code=request.original_code,
                    candidate_code=request.candidate_code,
                    suite_testbench_codes=(
                        request.suite_testbench_codes
                    ),
                    timelimit=self._csim_timelimit,
                ),
                split=EvaluationSplit.HIDDEN,
            )

        return handlers


class _OrchestratedCandidateValidator:
    """Adapt full ValidationOrchestrator runs to CandidateValidator."""

    def __init__(
        self,
        *,
        parent_context: RunContext,
        handler_factory: CandidateValidationHandlerFactory,
        orchestration_request: (
            CandidateRepairOrchestrationRequest
        ),
        validation_id: str,
    ) -> None:
        self._parent_context = parent_context
        self._handler_factory = handler_factory
        self._request = orchestration_request
        self._validation_id = validation_id
        self._outcomes: list[
            ValidationExecutionOutcome
        ] = []
        self._diagnostic_events: list[dict[str, Any]] = []

    @property
    def outcomes(
        self,
    ) -> tuple[ValidationExecutionOutcome, ...]:
        return tuple(self._outcomes)

    @property
    def diagnostic_events(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._diagnostic_events)

    def validate(
        self,
        request: CandidateValidationRequest,
    ) -> CandidateValidationResult:
        if not isinstance(
            request,
            CandidateValidationRequest,
        ):
            raise TypeError(
                "request must be CandidateValidationRequest"
            )
        if request.task != self._parent_context.task:
            raise ValueError(
                "candidate validation task must match "
                "the parent context"
            )
        if request.budget is not self._parent_context.budget:
            raise ValueError(
                "candidate validator must reuse the exact "
                "parent BudgetManager"
            )

        run_id = (
            f"{self._parent_context.run_id}."
            f"candidate-repair.{request.attempt}"
        )
        validation_id = (
            f"{self._validation_id}."
            f"candidate.{request.attempt}"
        )
        plan = CandidateValidationPlanRequest(
            task=request.task,
            candidate_code=request.candidate_code,
            original_code=request.original_code,
            preflight_testbench_code=(
                self._request.preflight_testbench_code
            ),
            suite_testbench_codes=(
                self._request.suite_testbench_codes
            ),
            attempt=request.attempt,
            validation_id=validation_id,
            reference_top_function=(
                self._request.reference_top_function
            ),
            candidate_top_function=(
                self._request.candidate_top_function
                or request.task.kernel_name
            ),
        )
        handlers = _build_handlers(
            self._handler_factory,
            plan,
        )
        child_context = RunContext(
            run_id=run_id,
            task=request.task,
            budget=request.budget,
            trace=self._parent_context.trace,
        )
        outcome = ValidationOrchestrator(
            handlers
        ).run_detailed(
            child_context,
            validation_id=validation_id,
        )
        self._outcomes.append(outcome)
        completed = tuple(
            step.state for step in outcome.result.steps
        )

        if outcome.result.accepted:
            return CandidateValidationResult(
                passed=True,
                completed_stages=completed,
                summary=(
                    "Candidate passed the complete "
                    "validation plan."
                ),
            )

        report = outcome.terminal_report
        decision = outcome.terminal_decision
        if report is None or decision is None:
            raise RuntimeError(
                "non-accepted validation must retain "
                "terminal feedback internally"
            )
        terminal_step = outcome.result.steps[-1]
        diagnostic = _project_diagnostic_event(
            context=child_context,
            request=self._request,
            outcome=outcome,
            candidate_code=request.candidate_code,
        )
        if diagnostic is not None:
            self._diagnostic_events.append(diagnostic)
        view = report.metadata.get("evidence_view")
        summary = (
            terminal_step.transition.reason
            if view == "agent_safe"
            else "Operator-only terminal validation result."
        )
        return CandidateValidationResult(
            passed=False,
            completed_stages=completed,
            summary=summary,
            feedback=report,
            route_decision=decision,
            failure_state=terminal_step.state,
            metadata={
                "orchestration_validation_id": (
                    outcome.result.validation_id
                ),
                "final_state": (
                    outcome.result.final_state.value
                ),
                "terminal_evidence_view": view,
            },
        )


class CandidateRepairValidationOrchestrator:
    """Organize validation, legal repair handoff, and revalidation."""

    orchestrator_version = 1

    def __init__(
        self,
        *,
        model_adapter: CandidateModelAdapter,
        handler_factory: CandidateValidationHandlerFactory,
        shadow_advisor: DiagnosticAdvisor | None = None,
    ) -> None:
        if not isinstance(
            model_adapter,
            CandidateModelAdapter,
        ):
            raise TypeError(
                "model_adapter must be CandidateModelAdapter"
            )
        if not callable(
            getattr(handler_factory, "build", None)
        ):
            raise TypeError(
                "handler_factory must provide build(request)"
            )
        if shadow_advisor is not None and not callable(
            getattr(shadow_advisor, "diagnose", None)
        ):
            raise TypeError(
                "shadow_advisor must provide diagnose(request) or be None"
            )
        self._model_adapter = model_adapter
        self._handler_factory = handler_factory
        self._shadow_advisor = shadow_advisor
        self._recovery_policy = conservative_v1_policy()

    def run(
        self,
        context: RunContext,
        request: CandidateRepairOrchestrationRequest,
        *,
        validation_id: str,
    ) -> CandidateRepairOrchestrationResult:
        if not isinstance(context, RunContext):
            raise TypeError("context must be a RunContext")
        if not isinstance(
            request,
            CandidateRepairOrchestrationRequest,
        ):
            raise TypeError(
                "request must be "
                "CandidateRepairOrchestrationRequest"
            )
        _required_text(validation_id, "validation_id")
        _validate_suite_codes(context.task, request)
        (
            family_instruction,
            family_instruction_source,
        ) = _select_family_instruction(
            self._model_adapter.family_instruction,
            request.family_instruction,
        )

        recovery_ledger = RecoveryLedger(self._recovery_policy)
        recovery_metadata: dict[str, Any] = {"diagnostic_events": []}

        context.trace.record(
            "candidate_repair.orchestration.started",
            phase="validation",
            status="running",
            metadata={
                "validation_id": validation_id,
                "orchestrator_version": (
                    self.orchestrator_version
                ),
                "max_attempts": request.max_attempts,
            },
        )

        initial_plan = CandidateValidationPlanRequest(
            task=context.task,
            candidate_code=request.initial_candidate,
            original_code=request.original_code,
            preflight_testbench_code=(
                request.preflight_testbench_code
            ),
            suite_testbench_codes=(
                request.suite_testbench_codes
            ),
            attempt=0,
            validation_id=f"{validation_id}.initial",
            reference_top_function=(
                request.reference_top_function
            ),
            candidate_top_function=(
                request.candidate_top_function
                or context.task.kernel_name
            ),
        )
        initial_handlers = _build_handlers(
            self._handler_factory,
            initial_plan,
        )
        initial_context = RunContext(
            run_id=f"{context.run_id}.initial-validation",
            task=context.task,
            budget=context.budget,
            trace=context.trace,
        )
        initial_outcome = ValidationOrchestrator(
            initial_handlers
        ).run_detailed(
            initial_context,
            validation_id=f"{validation_id}.initial",
        )

        if initial_outcome.result.accepted:
            return self._finish(
                context,
                validation_id=validation_id,
                status=(
                    CandidateRepairOrchestrationStatus.ACCEPTED
                ),
                request=request,
                initial_validation=initial_outcome.result,
                repair_result=None,
                candidate_validations=(),
                final_candidate=request.initial_candidate,
                last_validation_state=(
                    ValidationState.ACCEPTED
                ),
                family_instruction=family_instruction,
                family_instruction_source=(
                    family_instruction_source
                ),
                recovery_ledger=recovery_ledger,
            )

        terminal_report = initial_outcome.terminal_report
        terminal_decision = initial_outcome.terminal_decision
        if terminal_report is None or terminal_decision is None:
            raise RuntimeError(
                "initial terminal validation must retain "
                "internal feedback"
            )
        initial_terminal_step = (
            initial_outcome.result.steps[-1]
        )
        initial_diagnostic = _project_diagnostic_event(
            context=initial_context,
            request=request,
            outcome=initial_outcome,
            candidate_code=request.initial_candidate,
        )
        if initial_diagnostic is not None:
            recovery_metadata["diagnostic_events"].append(initial_diagnostic)

        active_request = request
        active_outcome = initial_outcome
        if (
            active_outcome.result.final_state
            is ValidationState.REPAIR_PENDING
            and terminal_decision.action
            is FeedbackRouteAction.REPAIR_TESTBENCH
            and initial_terminal_step.state
            in {
                ValidationState.PREFLIGHT,
                ValidationState.PUBLIC_EVALUATION,
                ValidationState.PUBLIC_COSIM,
            }
        ):
            (
                active_request,
                active_outcome,
                testbench_metadata,
            ) = self._recover_public_testbench(
                context=context,
                request=active_request,
                outcome=active_outcome,
                validation_id=validation_id,
                ledger=recovery_ledger,
            )
            recovery_metadata.update(testbench_metadata)
            recovery_metadata["diagnostic_events"] = [
                *(
                    [initial_diagnostic]
                    if initial_diagnostic is not None
                    else []
                ),
                *testbench_metadata.get("diagnostic_events", []),
            ]
            if active_outcome.result.accepted:
                return self._finish(
                    context,
                    validation_id=validation_id,
                    status=CandidateRepairOrchestrationStatus.ACCEPTED,
                    request=active_request,
                    initial_validation=initial_outcome.result,
                    repair_result=None,
                    candidate_validations=(),
                    final_candidate=request.initial_candidate,
                    last_validation_state=ValidationState.ACCEPTED,
                    family_instruction=family_instruction,
                    family_instruction_source=family_instruction_source,
                    recovery_ledger=recovery_ledger,
                    extra_metadata=recovery_metadata,
                )
            terminal_report = active_outcome.terminal_report
            terminal_decision = active_outcome.terminal_decision
            if terminal_report is None or terminal_decision is None:
                raise RuntimeError(
                    "revalidated terminal result must retain feedback"
                )
            initial_terminal_step = active_outcome.result.steps[-1]

        if (
            active_outcome.result.final_state
            is not ValidationState.REPAIR_PENDING
            or terminal_decision.action
            is not FeedbackRouteAction.REPAIR_CANDIDATE
        ):
            status = (
                CandidateRepairOrchestrationStatus.REPAIR_NOT_APPLICABLE
                if active_outcome.result.final_state
                is ValidationState.REPAIR_PENDING
                else CandidateRepairOrchestrationStatus.VALIDATION_TERMINAL
            )
            return self._finish(
                context,
                validation_id=validation_id,
                status=status,
                request=active_request,
                initial_validation=initial_outcome.result,
                repair_result=None,
                candidate_validations=(),
                final_candidate=request.initial_candidate,
                last_validation_state=active_outcome.result.final_state,
                family_instruction=family_instruction,
                family_instruction_source=family_instruction_source,
                recovery_ledger=recovery_ledger,
                extra_metadata=recovery_metadata,
            )

        validator = _OrchestratedCandidateValidator(
            parent_context=context,
            handler_factory=self._handler_factory,
            orchestration_request=active_request,
            validation_id=validation_id,
        )
        loop = BoundedCandidateRepairLoop(
            model_adapter=self._model_adapter,
            validator=validator,
            budget=context.budget,
        )
        repair_result = loop.run(
            CandidateRepairLoopRequest(
                task=context.task,
                initial_candidate=request.initial_candidate,
                original_code=request.original_code,
                feedback=terminal_report,
                route_decision=terminal_decision,
                failure_state=initial_terminal_step.state,
                max_attempts=request.max_attempts,
                public_testbench_code=(
                    active_request.prompt_public_testbench_code
                ),
                family_instruction=family_instruction,
                approved_memory_snippets=(
                    active_request.approved_memory_snippets
                ),
                recovery_ledger=recovery_ledger,
            )
        )
        validation_results = tuple(
            item.result for item in validator.outcomes
        )
        recovery_metadata["diagnostic_events"] = [
            *recovery_metadata.get("diagnostic_events", []),
            *validator.diagnostic_events,
        ]

        status = _status_for_repair_result(
            repair_result,
            validation_results,
        )
        final_candidate = (
            repair_result.last_validated_candidate
            or request.initial_candidate
        )
        last_state = (
            validation_results[-1].final_state
            if validation_results
            else initial_outcome.result.final_state
        )
        if status is CandidateRepairOrchestrationStatus.ACCEPTED:
            last_state = ValidationState.ACCEPTED

        return self._finish(
            context,
            validation_id=validation_id,
            status=status,
            request=request,
            initial_validation=initial_outcome.result,
            repair_result=repair_result,
            candidate_validations=validation_results,
            final_candidate=final_candidate,
            last_validation_state=last_state,
            family_instruction=family_instruction,
            family_instruction_source=(
                family_instruction_source
            ),
            recovery_ledger=recovery_ledger,
            extra_metadata=recovery_metadata,
        )

    def _recover_public_testbench(
        self,
        *,
        context: RunContext,
        request: CandidateRepairOrchestrationRequest,
        outcome: ValidationExecutionOutcome,
        validation_id: str,
        ledger: RecoveryLedger,
    ) -> tuple[
        CandidateRepairOrchestrationRequest,
        ValidationExecutionOutcome,
        dict[str, Any],
    ]:
        report = outcome.terminal_report
        decision = outcome.terminal_decision
        if report is None or decision is None:
            raise RuntimeError("testbench recovery requires terminal feedback")
        failure_state = outcome.result.steps[-1].state
        if failure_state not in {
            ValidationState.PREFLIGHT,
            ValidationState.PUBLIC_EVALUATION,
            ValidationState.PUBLIC_COSIM,
        }:
            raise ValueError("runtime Testbench recovery is formal Public-only")
        selected = {item.feedback_id: item for item in report.items}
        if failure_state is ValidationState.PREFLIGHT:
            suite_ids = {
                suite.suite_id
                for suite in context.task.test_suites
                if suite.split is EvaluationSplit.PUBLIC
                and request.suite_testbench_codes.get(suite.suite_id)
                == request.preflight_testbench_code
            }
        else:
            suite_ids = {
                str(selected[item_id].metadata.get("suite_id"))
                for item_id in decision.selected_feedback_ids
                if item_id in selected
                and selected[item_id].owner is FeedbackOwner.TESTBENCH
                and selected[item_id].metadata.get("suite_id")
            }
        if len(suite_ids) != 1:
            raise ValueError(
                "runtime Testbench recovery requires one deterministic suite"
            )
        suite_id = next(iter(suite_ids))
        public_ids = {
            suite.suite_id
            for suite in context.task.test_suites
            if suite.split is EvaluationSplit.PUBLIC
        }
        if suite_id not in public_ids:
            raise ValueError("selected Testbench suite is not Public")
        suite = next(
            item for item in context.task.test_suites
            if item.suite_id == suite_id
        )
        source_kind = (
            suite.source.source_kind.value
            if suite.source is not None
            else "unspecified"
        )
        authorization = testbench_revision_authorization(
            split=suite.split.value,
            source_kind=source_kind,
        )
        if authorization != "auto_public_bounded":
            return request, outcome, {
                "runtime_testbench_suite_id": suite_id,
                "runtime_testbench_failure_state": failure_state.value,
                "runtime_testbench_source_kind": source_kind,
                "runtime_testbench_authorization": authorization,
                "runtime_testbench_recovery_status": "review_required",
                "runtime_testbench_recovery_reason": (
                    "only provenance-backed AUTO Public Testbench revisions "
                    "may be repaired automatically"
                ),
                "diagnostic_events": [],
            }

        stage = RecoveryStage(failure_state.value)
        ledger.reserve(
            RecoveryRequest(
                action=RecoveryAction.REPAIR,
                role=RecoveryRole.TESTBENCH,
                stage=stage,
                evidence_view="agent_safe",
                owner_authority=RecoveryAuthority.DETERMINISTIC_PROVEN,
                lineage_id=context.task.task_id,
                physical_tool_launched=True,
                evidence_complete=True,
                advisory_mode=request.llm_advisory_mode,
                timeout_class=decision.metadata.get("timeout_class"),
            ),
            budget=context.budget,
            restart_reserve=default_restart_reserve(stage),
        )
        repairer = build_openai_compatible_testbench_repairer(
            effective_config=self._model_adapter.effective_config,
            budget=context.budget,
        )
        loop = TestbenchRepairLoop(
            preflight=TestbenchPreflight(),
            repairer=repairer,
            max_repair_attempts=1,
        )
        work_root = getattr(
            self._handler_factory,
            "work_root",
            Path(os.getenv("AGREFACTOR_WORK_ROOT", "/data/agrefactor_work")),
        )
        repair = loop.run(
            work_dir=(
                Path(work_root)
                / _safe_component(validation_id)
                / "runtime_testbench_repair"
                / _safe_component(suite_id)
            ),
            testbench_code=request.suite_testbench_codes[suite_id],
            original_code=request.original_code,
            candidate_code=request.initial_candidate,
            budget=context.budget,
            task=context.task,
            original_top_function=request.reference_top_function,
            candidate_top_function=(
                request.candidate_top_function or context.task.kernel_name
            ),
            runtime_feedback=report,
            failure_state=failure_state,
        )
        metadata = {
            "runtime_testbench_recovery": repair.to_dict(),
            "runtime_testbench_suite_id": suite_id,
            "runtime_testbench_failure_state": failure_state.value,
            "runtime_testbench_source_kind": source_kind,
            "runtime_testbench_authorization": authorization,
            "diagnostic_events": [],
        }
        if not repair.succeeded:
            return request, outcome, metadata

        semantic_revision = build_testbench_semantic_revision(
            request.suite_testbench_codes[suite_id],
            repair.testbench_code,
            suite_id=suite_id,
            split=suite.split.value,
            source_kind=source_kind,
            original_top_function=request.reference_top_function,
            candidate_top_function=(
                request.candidate_top_function or context.task.kernel_name
            ),
        )
        semantic_audit = audit_testbench_semantic_revision(semantic_revision)
        metadata["testbench_semantic_revision"] = semantic_revision
        metadata["testbench_semantic_audit"] = semantic_audit.to_dict()
        if semantic_audit.has_errors:
            metadata["runtime_testbench_recovery_status"] = (
                "blocked_semantic_weakening"
            )
            return request, outcome, metadata

        codes = dict(request.suite_testbench_codes)
        old_code = codes[suite_id]
        codes[suite_id] = repair.testbench_code
        updated = replace(
            request,
            suite_testbench_codes=codes,
            preflight_testbench_code=(
                repair.testbench_code
                if request.preflight_testbench_code == old_code
                else request.preflight_testbench_code
            ),
            prompt_public_testbench_code=(
                repair.testbench_code
                if request.prompt_public_testbench_code == old_code
                else request.prompt_public_testbench_code
            ),
        )
        ledger.record_validation_restart(
            lineage_id=context.task.task_id,
            stage=stage,
            budget=context.budget,
            restart_reserve=default_restart_reserve(stage),
        )
        plan = CandidateValidationPlanRequest(
            task=context.task,
            candidate_code=request.initial_candidate,
            original_code=updated.original_code,
            preflight_testbench_code=updated.preflight_testbench_code,
            suite_testbench_codes=updated.suite_testbench_codes,
            attempt=1,
            validation_id=f"{validation_id}.testbench.1",
            reference_top_function=updated.reference_top_function,
            candidate_top_function=(
                updated.candidate_top_function or context.task.kernel_name
            ),
        )
        child = RunContext(
            run_id=f"{context.run_id}.testbench-repair.1",
            task=context.task,
            budget=context.budget,
            trace=context.trace,
        )
        revalidated = ValidationOrchestrator(
            _build_handlers(self._handler_factory, plan)
        ).run_detailed(
            child,
            validation_id=f"{validation_id}.testbench.1",
        )
        metadata["runtime_testbench_revalidation"] = (
            revalidated.result.to_dict()
        )
        revalidation_diagnostic = _project_diagnostic_event(
            context=child,
            request=updated,
            outcome=revalidated,
            candidate_code=request.initial_candidate,
        )
        if revalidation_diagnostic is not None:
            metadata["diagnostic_events"].append(revalidation_diagnostic)
        return updated, revalidated, metadata

    def _finish(
        self,
        context: RunContext,
        *,
        validation_id: str,
        status: CandidateRepairOrchestrationStatus,
        request: CandidateRepairOrchestrationRequest,
        initial_validation: ValidationOrchestrationResult,
        repair_result: CandidateRepairLoopResult | None,
        candidate_validations: tuple[
            ValidationOrchestrationResult,
            ...,
        ],
        final_candidate: str,
        last_validation_state: ValidationState,
        family_instruction: str | None,
        family_instruction_source: str,
        recovery_ledger: RecoveryLedger | None = None,
        extra_metadata: Mapping[str, Any] | None = None,
    ) -> CandidateRepairOrchestrationResult:
        effective_quota = (
            None
            if recovery_ledger is None
            else build_effective_repair_quota_summary(
                ledger=recovery_ledger,
                budget=context.budget,
                candidate_requested_max=request.max_attempts,
                runtime_testbench_local_max=1,
            ).to_dict()
        )
        merged_metadata = dict(extra_metadata or {})
        repair_count = (
            0 if repair_result is None else len(repair_result.attempts)
        )
        def shadow_main_snapshot() -> dict[str, Any]:
            return {
                "route": last_validation_state.value,
                "status": status.value,
                "final_candidate_sha256": sha256(
                    (final_candidate.rstrip() + "\n").encode("utf-8")
                ).hexdigest(),
                "recovery_ledger_count": (
                    0 if recovery_ledger is None else len(recovery_ledger.events)
                ),
                "repair_count": repair_count,
                "best_correct_pointer": None,
            }

        main_snapshot = shadow_main_snapshot()
        shadow_events = merged_metadata.get("diagnostic_events", ())
        if self._shadow_advisor is not None and isinstance(
            shadow_events, (list, tuple)
        ):
            merged_metadata["r2_shadow_diagnostics"] = list(
                run_shadow_diagnostics(
                    shadow_events,
                    advisor=self._shadow_advisor,
                    main_before=main_snapshot,
                    main_after=shadow_main_snapshot,
                )
            )
            merged_metadata["r2_shadow_enabled"] = True
        else:
            merged_metadata["r2_shadow_diagnostics"] = []
            merged_metadata["r2_shadow_enabled"] = False
        result = CandidateRepairOrchestrationResult(
            validation_id=validation_id,
            status=status,
            initial_candidate=request.initial_candidate,
            final_candidate=final_candidate,
            initial_validation=initial_validation,
            repair_result=repair_result,
            candidate_validations=candidate_validations,
            last_validation_state=last_validation_state,
            budget_usage=_safe_budget_snapshot(
                context.budget
            ),
            metadata={
                "orchestrator_version": (
                    self.orchestrator_version
                ),
                "repair_attempt_count": (
                    repair_count
                ),
                "candidate_validation_count": len(
                    candidate_validations
                ),
                "final_candidate_source": (
                    "validated_repair"
                    if (
                        repair_result is not None
                        and repair_result.last_validated_candidate
                        is not None
                    )
                    else "initial_candidate"
                ),
                "hidden_feedback_retained_in_result": False,
                "effective_model_config": (
                    self._model_adapter.effective_config.to_manifest()
                ),
                "family_instruction": family_instruction,
                "family_instruction_source": (
                    family_instruction_source
                ),
                "recovery_profile": request.recovery_profile,
                "llm_advisory_mode": request.llm_advisory_mode,
                "recovery_ledger": (
                    None
                    if recovery_ledger is None
                    else recovery_ledger.to_dict()
                ),
                "effective_repair_quota": effective_quota,
                **merged_metadata,
            },
        )
        context.trace.record(
            "candidate_repair.orchestration.finished",
            phase="validation",
            status=status.value,
            metadata={
                "validation_id": validation_id,
                "accepted": result.accepted,
                "repair_attempt_count": (
                    result.metadata[
                        "repair_attempt_count"
                    ]
                ),
                "candidate_validation_count": (
                    result.metadata[
                        "candidate_validation_count"
                    ]
                ),
                "final_candidate_source": (
                    result.metadata[
                        "final_candidate_source"
                    ]
                ),
                "last_validation_state": (
                    result.last_validation_state.value
                ),
            },
        )
        return result


def _project_diagnostic_event(
    *,
    context: RunContext,
    request: CandidateRepairOrchestrationRequest,
    outcome: ValidationExecutionOutcome,
    candidate_code: str,
) -> dict[str, Any] | None:
    """Project one terminal Public event without widening authority."""

    if outcome.result.accepted:
        return None
    report = outcome.terminal_report
    decision = outcome.terminal_decision
    if report is None or decision is None:
        return None
    terminal_step = outcome.result.steps[-1]
    if terminal_step.state is ValidationState.HIDDEN_EVALUATION:
        return None
    public_suites = []
    for suite in context.task.test_suites:
        if suite.split is not EvaluationSplit.PUBLIC:
            continue
        code = request.suite_testbench_codes.get(suite.suite_id)
        public_suites.append(
            {
                "suite_id": suite.suite_id,
                "split": suite.split.value,
                "suite_version": suite.suite_version,
                "source_kind": (
                    suite.source.source_kind.value
                    if suite.source is not None
                    else "unspecified"
                ),
                "runtime_contract_schema_version": (
                    suite.runtime_contract.get("schema_version")
                    if suite.runtime_contract is not None
                    else None
                ),
                "content_sha256": (
                    suite.source.expected_content_sha256
                    if (
                        suite.source is not None
                        and suite.source.expected_content_sha256 is not None
                    )
                    else (
                        sha256(code.encode("utf-8")).hexdigest()
                        if code is not None
                        else None
                    )
                ),
            }
        )
    event = DiagnosticEventProjector().from_feedback(
        report,
        run_id=context.run_id,
        validation_id=outcome.result.validation_id,
        validation_state=terminal_step.state.value,
        route_action=decision.action.value,
        selected_feedback_ids=decision.selected_feedback_ids,
        target=context.task.target.to_dict(),
        candidate_code=candidate_code,
        public_suite_identities=tuple(public_suites),
    )
    return event.to_dict()


def _status_for_repair_result(
    result: CandidateRepairLoopResult,
    validations: tuple[
        ValidationOrchestrationResult,
        ...,
    ],
) -> CandidateRepairOrchestrationStatus:
    reason = result.stop_reason
    if reason is CandidateRepairStopReason.VALIDATED:
        return CandidateRepairOrchestrationStatus.ACCEPTED
    if reason is CandidateRepairStopReason.BUDGET_EXHAUSTED:
        return (
            CandidateRepairOrchestrationStatus.
            BUDGET_EXHAUSTED
        )
    if reason is CandidateRepairStopReason.VALIDATOR_ERROR:
        return (
            CandidateRepairOrchestrationStatus.
            VALIDATOR_ERROR
        )
    if reason is CandidateRepairStopReason.ATTEMPTS_EXHAUSTED:
        return (
            CandidateRepairOrchestrationStatus.
            REPAIR_EXHAUSTED
        )
    if reason is CandidateRepairStopReason.TERMINAL_FEEDBACK:
        if validations:
            return (
                CandidateRepairOrchestrationStatus.
                VALIDATION_TERMINAL
            )
        return (
            CandidateRepairOrchestrationStatus.
            REPAIR_NOT_APPLICABLE
        )
    raise ValueError(
        f"Unsupported repair stop reason: {reason.value}"
    )


def _build_handlers(
    factory: CandidateValidationHandlerFactory,
    request: CandidateValidationPlanRequest,
) -> Mapping[
    ValidationState | str,
    ValidationStageHandler,
]:
    value = factory.build(request)
    if not isinstance(value, Mapping):
        raise TypeError(
            "handler_factory.build must return a mapping"
        )
    return value


def _validate_suite_codes(
    task: TaskSpec,
    request: CandidateRepairOrchestrationRequest,
) -> None:
    declared = {
        suite.suite_id for suite in task.test_suites
    }
    missing = declared - set(
        request.suite_testbench_codes
    )
    if missing:
        raise ValueError(
            "missing testbench code for declared suites: "
            + ", ".join(sorted(missing))
        )
    if any(
        suite.split is EvaluationSplit.PUBLIC
        for suite in task.test_suites
    ) and request.prompt_public_testbench_code is None:
        raise ValueError(
            "tasks with Public suites require "
            "prompt_public_testbench_code"
        )


def _safe_budget_snapshot(
    budget: BudgetManager,
) -> BudgetUsage:
    try:
        return budget.snapshot()
    except BudgetExceededError:
        return budget.record_observed()


def _copy_code_mapping(
    value: Mapping[str, str],
    field_name: str,
) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    result: dict[str, str] = {}
    for raw_key, raw_code in value.items():
        _required_text(raw_key, field_name)
        _required_text(raw_code, field_name)
        key = raw_key.strip()
        if key in result:
            raise ValueError(
                f"duplicate {field_name} key: {key}"
            )
        result[key] = raw_code
    return result


def _required_text(value: str, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _optional_text(
    value: str | None,
    field_name: str,
) -> None:
    if value is not None:
        _required_text(value, field_name)


def _state(value: Any) -> ValidationState:
    if isinstance(value, ValidationState):
        return value
    try:
        return ValidationState(str(value))
    except ValueError as exc:
        raise ValueError(
            f"Unsupported validation state: {value!r}"
        ) from exc


def _json_mapping(
    value: Mapping[str, Any],
    field_name: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        )
        copied = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field_name} must contain finite JSON data"
        ) from exc
    if not isinstance(copied, dict):
        raise TypeError(
            f"{field_name} must normalize to an object"
        )
    return copied


def _safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    cleaned = cleaned.strip("._")
    return cleaned or "validation"
