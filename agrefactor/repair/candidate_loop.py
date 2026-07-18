"""Bounded, provider-neutral candidate repair control.

The controller delegates validation to an injected validator. It requires every
changed candidate to restart from a legal validation prefix and controls only
bounded model attempts, budget accounting, and conservative state preservation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from enum import Enum
import json
from typing import Any, Protocol

from agrefactor.config import EvaluationSplit, TaskSpec
from agrefactor.evaluation import FeedbackRouteAction, FeedbackRouteDecision, ValidationState
from agrefactor.evidence import FeedbackOwner, FeedbackReport, FeedbackStage
from agrefactor.models import (
    CandidateModelAdapter,
    CandidateModelRequest,
    CandidateModelResult,
    CandidateResponseError,
    ModelResponse,
)
from agrefactor.prompts import (
    CandidateRepairPromptInputs,
    build_candidate_compile_repair_prompt,
    build_candidate_csynth_repair_prompt,
    build_candidate_public_csim_repair_prompt,
)
from agrefactor.runtime import BudgetExceededError, BudgetManager, BudgetUsage

_CANONICAL_VALIDATION_ORDER = (
    ValidationState.PREFLIGHT,
    ValidationState.CSYNTH,
    ValidationState.PUBLIC_EVALUATION,
    ValidationState.HIDDEN_EVALUATION,
)
_ALLOWED_REPAIR_STATES = frozenset(
    {ValidationState.PREFLIGHT, ValidationState.CSYNTH, ValidationState.PUBLIC_EVALUATION}
)
_STAGE_BY_REPAIR_STATE = {
    ValidationState.PREFLIGHT: frozenset(
        {FeedbackStage.STATIC_CHECK, FeedbackStage.COMPILE, FeedbackStage.LINK}
    ),
    ValidationState.CSYNTH: frozenset({FeedbackStage.CSYNTH}),
    ValidationState.PUBLIC_EVALUATION: frozenset(
        {FeedbackStage.TEST, FeedbackStage.CSIM}
    ),
}
_REQUIRED_PREFIX_BY_REPAIR_STATE = {
    ValidationState.PREFLIGHT: (ValidationState.PREFLIGHT,),
    ValidationState.CSYNTH: (ValidationState.PREFLIGHT, ValidationState.CSYNTH),
    ValidationState.PUBLIC_EVALUATION: (
        ValidationState.PREFLIGHT,
        ValidationState.CSYNTH,
        ValidationState.PUBLIC_EVALUATION,
    ),
}


class CandidateRepairAttemptStatus(str, Enum):
    BUDGET_BLOCKED = "budget_blocked"
    PROVIDER_ERROR = "provider_error"
    RESPONSE_REJECTED = "response_rejected"
    VALIDATOR_ERROR = "validator_error"
    VALIDATION_FAILED = "validation_failed"
    VALIDATED = "validated"


class CandidateRepairStopReason(str, Enum):
    VALIDATED = "validated"
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"
    BUDGET_EXHAUSTED = "budget_exhausted"
    TERMINAL_FEEDBACK = "terminal_feedback"
    VALIDATOR_ERROR = "validator_error"


@dataclass(frozen=True, slots=True)
class CandidateRepairLoopRequest:
    task: TaskSpec
    initial_candidate: str
    original_code: str
    feedback: FeedbackReport
    route_decision: FeedbackRouteDecision
    failure_state: ValidationState
    max_attempts: int
    public_testbench_code: str | None = None
    family_instruction: str | None = None
    approved_memory_snippets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.task, TaskSpec):
            raise TypeError("task must be a TaskSpec")
        _required_text(self.initial_candidate, "initial_candidate")
        _required_text(self.original_code, "original_code")
        _optional_text(self.public_testbench_code, "public_testbench_code")
        _optional_text(self.family_instruction, "family_instruction")
        _text_tuple(self.approved_memory_snippets, "approved_memory_snippets")
        if not isinstance(self.feedback, FeedbackReport):
            raise TypeError("feedback must be a FeedbackReport")
        if not isinstance(self.route_decision, FeedbackRouteDecision):
            raise TypeError("route_decision must be a FeedbackRouteDecision")
        state = _validation_state(self.failure_state)
        object.__setattr__(self, "failure_state", state)
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int):
            raise TypeError("max_attempts must be an integer")
        if self.max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        _validate_repair_context(
            self.feedback,
            self.route_decision,
            state,
            public_testbench_code=self.public_testbench_code,
        )


@dataclass(frozen=True, slots=True)
class CandidateValidationRequest:
    task: TaskSpec
    candidate_code: str
    original_code: str
    public_testbench_code: str | None
    attempt: int
    source_failure_state: ValidationState
    required_prefix: tuple[ValidationState, ...]
    budget: BudgetManager

    def __post_init__(self) -> None:
        if not isinstance(self.task, TaskSpec):
            raise TypeError("task must be a TaskSpec")
        _required_text(self.candidate_code, "candidate_code")
        _required_text(self.original_code, "original_code")
        _optional_text(self.public_testbench_code, "public_testbench_code")
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int):
            raise TypeError("attempt must be an integer")
        if self.attempt <= 0:
            raise ValueError("attempt must be positive")
        state = _validation_state(self.source_failure_state)
        object.__setattr__(self, "source_failure_state", state)
        prefix = tuple(_validation_state(item) for item in self.required_prefix)
        if prefix != _required_prefix(state):
            raise ValueError("required_prefix must match the source failure state")
        object.__setattr__(self, "required_prefix", prefix)
        if not isinstance(self.budget, BudgetManager):
            raise TypeError("budget must be a BudgetManager")


@dataclass(frozen=True, slots=True)
class CandidateValidationResult:
    passed: bool
    completed_stages: tuple[ValidationState, ...]
    summary: str
    feedback: FeedbackReport | None = None
    route_decision: FeedbackRouteDecision | None = None
    failure_state: ValidationState | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.passed, bool):
            raise TypeError("passed must be boolean")
        _required_text(self.summary, "summary")
        stages = tuple(_validation_state(item) for item in self.completed_stages)
        if not stages:
            raise ValueError("completed_stages must not be empty")
        if stages != _CANONICAL_VALIDATION_ORDER[: len(stages)]:
            raise ValueError(
                "completed_stages must be a contiguous prefix starting with preflight"
            )
        object.__setattr__(self, "completed_stages", stages)
        object.__setattr__(self, "metadata", _json_mapping(self.metadata, "metadata"))
        if self.passed:
            if any(
                item is not None
                for item in (self.feedback, self.route_decision, self.failure_state)
            ):
                raise ValueError("passed validation must not carry failure feedback")
            return
        if not isinstance(self.feedback, FeedbackReport):
            raise TypeError("failed validation requires a FeedbackReport")
        if not isinstance(self.route_decision, FeedbackRouteDecision):
            raise TypeError("failed validation requires a FeedbackRouteDecision")
        state = _validation_state(self.failure_state)
        if state is not stages[-1]:
            raise ValueError("failure_state must be the final completed stage")
        if self.route_decision.source_report_id != self.feedback.report_id:
            raise ValueError("validation route must reference its feedback report")
        object.__setattr__(self, "failure_state", state)

    def to_safe_dict(self) -> dict[str, Any]:
        view = None if self.feedback is None else self.feedback.metadata.get("evidence_view")
        report_id = None
        if self.feedback is not None:
            report_id = (
                self.feedback.report_id
                if view == "agent_safe"
                else "operator-only-redacted"
            )
        return {
            "passed": self.passed,
            "completed_stages": [item.value for item in self.completed_stages],
            "summary": (
                self.summary
                if view == "agent_safe"
                else "operator-only validation result"
            ),
            "source_report_id": report_id,
            "evidence_view": view,
            "route_action": (
                None
                if view != "agent_safe" or self.route_decision is None
                else self.route_decision.action.value
            ),
            "failure_state": (
                None if self.failure_state is None else self.failure_state.value
            ),
            "metadata": {} if view == "operator_full" else dict(self.metadata),
        }


class CandidateValidator(Protocol):
    def validate(self, request: CandidateValidationRequest) -> CandidateValidationResult:
        """Validate one candidate with the supplied shared budget."""


@dataclass(frozen=True, slots=True)
class CandidateRepairAttempt:
    attempt: int
    status: CandidateRepairAttemptStatus
    input_candidate: str
    proposal: str | None
    model_response: ModelResponse | None
    model_result: CandidateModelResult | None
    validation_result: CandidateValidationResult | None
    error_type: str | None
    error_message: str | None
    budget_before: BudgetUsage
    budget_after: BudgetUsage

    def __post_init__(self) -> None:
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int):
            raise TypeError("attempt must be an integer")
        if self.attempt <= 0:
            raise ValueError("attempt must be positive")
        object.__setattr__(
            self,
            "status",
            self.status
            if isinstance(self.status, CandidateRepairAttemptStatus)
            else CandidateRepairAttemptStatus(str(self.status)),
        )
        _required_text(self.input_candidate, "input_candidate")
        _optional_text(self.proposal, "proposal")
        if self.model_response is not None and not isinstance(
            self.model_response, ModelResponse
        ):
            raise TypeError("model_response must be ModelResponse or None")
        if self.model_result is not None and not isinstance(
            self.model_result, CandidateModelResult
        ):
            raise TypeError("model_result must be CandidateModelResult or None")
        if self.validation_result is not None and not isinstance(
            self.validation_result, CandidateValidationResult
        ):
            raise TypeError("validation_result must be CandidateValidationResult or None")
        _optional_text(self.error_type, "error_type")
        _optional_text(self.error_message, "error_message")
        if not isinstance(self.budget_before, BudgetUsage):
            raise TypeError("budget_before must be BudgetUsage")
        if not isinstance(self.budget_after, BudgetUsage):
            raise TypeError("budget_after must be BudgetUsage")

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "status": self.status.value,
            "input_candidate": self.input_candidate,
            "proposal": self.proposal,
            "model_response": _response_dict(self.model_response),
            "model_result": (
                None if self.model_result is None else self.model_result.to_dict()
            ),
            "validation_result": (
                None
                if self.validation_result is None
                else self.validation_result.to_safe_dict()
            ),
            "error_type": self.error_type,
            "error_message": self.error_message,
            "budget_before": asdict(self.budget_before),
            "budget_after": asdict(self.budget_after),
        }


@dataclass(frozen=True, slots=True)
class CandidateRepairLoopResult:
    stop_reason: CandidateRepairStopReason
    initial_candidate: str
    current_candidate: str
    last_validated_candidate: str | None
    last_proposal: str | None
    attempts: tuple[CandidateRepairAttempt, ...]
    budget_usage: BudgetUsage

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "stop_reason",
            self.stop_reason
            if isinstance(self.stop_reason, CandidateRepairStopReason)
            else CandidateRepairStopReason(str(self.stop_reason)),
        )
        _required_text(self.initial_candidate, "initial_candidate")
        _required_text(self.current_candidate, "current_candidate")
        _optional_text(self.last_validated_candidate, "last_validated_candidate")
        _optional_text(self.last_proposal, "last_proposal")
        attempts = tuple(self.attempts)
        if not all(isinstance(item, CandidateRepairAttempt) for item in attempts):
            raise TypeError("attempts must contain CandidateRepairAttempt values")
        object.__setattr__(self, "attempts", attempts)
        if not isinstance(self.budget_usage, BudgetUsage):
            raise TypeError("budget_usage must be BudgetUsage")

    @property
    def succeeded(self) -> bool:
        return self.stop_reason is CandidateRepairStopReason.VALIDATED

    def to_dict(self) -> dict[str, Any]:
        return {
            "stop_reason": self.stop_reason.value,
            "succeeded": self.succeeded,
            "initial_candidate": self.initial_candidate,
            "current_candidate": self.current_candidate,
            "last_validated_candidate": self.last_validated_candidate,
            "last_proposal": self.last_proposal,
            "attempts": [item.to_dict() for item in self.attempts],
            "budget_usage": asdict(self.budget_usage),
        }


class BoundedCandidateRepairLoop:
    """Generate one candidate per attempt and delegate validation."""

    def __init__(
        self,
        *,
        model_adapter: CandidateModelAdapter,
        validator: CandidateValidator,
        budget: BudgetManager,
    ) -> None:
        if not isinstance(model_adapter, CandidateModelAdapter):
            raise TypeError("model_adapter must be a CandidateModelAdapter")
        if not callable(getattr(validator, "validate", None)):
            raise TypeError("validator must provide a callable validate method")
        if not isinstance(budget, BudgetManager):
            raise TypeError("budget must be a BudgetManager")
        self._model_adapter = model_adapter
        self._validator = validator
        self._budget = budget

    @property
    def budget(self) -> BudgetManager:
        return self._budget

    def run(self, request: CandidateRepairLoopRequest) -> CandidateRepairLoopResult:
        if not isinstance(request, CandidateRepairLoopRequest):
            raise TypeError("request must be a CandidateRepairLoopRequest")

        initial_candidate = request.initial_candidate
        current_candidate = initial_candidate
        last_validated_candidate: str | None = None
        last_proposal: str | None = None
        attempts: list[CandidateRepairAttempt] = []
        feedback = request.feedback
        route = request.route_decision
        failure_state = request.failure_state
        prior_summaries: list[str] = []

        for attempt_number in range(1, request.max_attempts + 1):
            try:
                _validate_repair_context(
                    feedback,
                    route,
                    failure_state,
                    public_testbench_code=request.public_testbench_code,
                )
            except (TypeError, ValueError):
                return self._result(
                    CandidateRepairStopReason.TERMINAL_FEEDBACK,
                    initial_candidate,
                    current_candidate,
                    last_validated_candidate,
                    last_proposal,
                    attempts,
                )

            budget_before = self._safe_snapshot()
            try:
                self._budget.ensure_available(llm_calls=1)
            except BudgetExceededError as exc:
                attempts.append(
                    CandidateRepairAttempt(
                        attempt=attempt_number,
                        status=CandidateRepairAttemptStatus.BUDGET_BLOCKED,
                        input_candidate=current_candidate,
                        proposal=None,
                        model_response=None,
                        model_result=None,
                        validation_result=None,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                        budget_before=budget_before,
                        budget_after=self._safe_snapshot(),
                    )
                )
                return self._result(
                    CandidateRepairStopReason.BUDGET_EXHAUSTED,
                    initial_candidate,
                    current_candidate,
                    last_validated_candidate,
                    last_proposal,
                    attempts,
                )

            prompt = _build_prompt(
                request,
                feedback=feedback,
                failure_state=failure_state,
                current_candidate=current_candidate,
                attempt=attempt_number,
                prior_summaries=tuple(prior_summaries),
            )
            model_request = CandidateModelRequest(
                prompt=prompt,
                task=request.task,
                current_candidate=current_candidate,
            )
            response_count_before = len(self._model_adapter.responses)
            model_result: CandidateModelResult | None = None
            model_response: ModelResponse | None = None

            try:
                model_result = self._model_adapter.generate(
                    model_request,
                    before_provider_call=self._consume_llm_launch,
                    after_provider_response=self._record_model_usage,
                )
                model_response = model_result.response
            except BudgetExceededError as exc:
                attempts.append(
                    CandidateRepairAttempt(
                        attempt=attempt_number,
                        status=CandidateRepairAttemptStatus.BUDGET_BLOCKED,
                        input_candidate=current_candidate,
                        proposal=None,
                        model_response=None,
                        model_result=None,
                        validation_result=None,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                        budget_before=budget_before,
                        budget_after=self._safe_snapshot(),
                    )
                )
                return self._result(
                    CandidateRepairStopReason.BUDGET_EXHAUSTED,
                    initial_candidate,
                    current_candidate,
                    last_validated_candidate,
                    last_proposal,
                    attempts,
                )
            except CandidateResponseError as exc:
                model_response = self._new_response(response_count_before)
                attempts.append(
                    CandidateRepairAttempt(
                        attempt=attempt_number,
                        status=CandidateRepairAttemptStatus.RESPONSE_REJECTED,
                        input_candidate=current_candidate,
                        proposal=None,
                        model_response=model_response,
                        model_result=None,
                        validation_result=None,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                        budget_before=budget_before,
                        budget_after=self._safe_snapshot(),
                    )
                )
                prior_summaries.append(
                    "The previous response violated the candidate replacement contract."
                )
                if self._budget_exhausted():
                    return self._result(
                        CandidateRepairStopReason.BUDGET_EXHAUSTED,
                        initial_candidate,
                        current_candidate,
                        last_validated_candidate,
                        last_proposal,
                        attempts,
                    )
                continue
            except Exception as exc:
                model_response = self._new_response(response_count_before)
                attempts.append(
                    CandidateRepairAttempt(
                        attempt=attempt_number,
                        status=CandidateRepairAttemptStatus.PROVIDER_ERROR,
                        input_candidate=current_candidate,
                        proposal=None,
                        model_response=model_response,
                        model_result=None,
                        validation_result=None,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                        budget_before=budget_before,
                        budget_after=self._safe_snapshot(),
                    )
                )
                prior_summaries.append(
                    "The previous provider call did not produce a validated candidate."
                )
                if self._budget_exhausted():
                    return self._result(
                        CandidateRepairStopReason.BUDGET_EXHAUSTED,
                        initial_candidate,
                        current_candidate,
                        last_validated_candidate,
                        last_proposal,
                        attempts,
                    )
                continue

            last_proposal = model_result.candidate_code
            previous_candidate = current_candidate
            current_candidate = last_proposal
            validation_request = CandidateValidationRequest(
                task=request.task,
                candidate_code=current_candidate,
                original_code=request.original_code,
                public_testbench_code=request.public_testbench_code,
                attempt=attempt_number,
                source_failure_state=failure_state,
                required_prefix=_required_prefix(failure_state),
                budget=self._budget,
            )

            try:
                validation_result = self._validator.validate(validation_request)
                if not isinstance(validation_result, CandidateValidationResult):
                    raise TypeError("validator must return CandidateValidationResult")
                _validate_completed_prefix(
                    validation_result,
                    validation_request.required_prefix,
                )
            except Exception as exc:
                attempts.append(
                    CandidateRepairAttempt(
                        attempt=attempt_number,
                        status=CandidateRepairAttemptStatus.VALIDATOR_ERROR,
                        input_candidate=previous_candidate,
                        proposal=last_proposal,
                        model_response=model_response,
                        model_result=model_result,
                        validation_result=None,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                        budget_before=budget_before,
                        budget_after=self._safe_snapshot(),
                    )
                )
                return self._result(
                    CandidateRepairStopReason.VALIDATOR_ERROR,
                    initial_candidate,
                    current_candidate,
                    last_validated_candidate,
                    last_proposal,
                    attempts,
                )

            if validation_result.passed:
                last_validated_candidate = current_candidate
                attempts.append(
                    CandidateRepairAttempt(
                        attempt=attempt_number,
                        status=CandidateRepairAttemptStatus.VALIDATED,
                        input_candidate=previous_candidate,
                        proposal=last_proposal,
                        model_response=model_response,
                        model_result=model_result,
                        validation_result=validation_result,
                        error_type=None,
                        error_message=None,
                        budget_before=budget_before,
                        budget_after=self._safe_snapshot(),
                    )
                )
                return self._result(
                    CandidateRepairStopReason.VALIDATED,
                    initial_candidate,
                    current_candidate,
                    last_validated_candidate,
                    last_proposal,
                    attempts,
                )

            attempts.append(
                CandidateRepairAttempt(
                    attempt=attempt_number,
                    status=CandidateRepairAttemptStatus.VALIDATION_FAILED,
                    input_candidate=previous_candidate,
                    proposal=last_proposal,
                    model_response=model_response,
                    model_result=model_result,
                    validation_result=validation_result,
                    error_type=None,
                    error_message=None,
                    budget_before=budget_before,
                    budget_after=self._safe_snapshot(),
                )
            )
            prior_summaries.append(validation_result.summary)
            assert validation_result.feedback is not None
            assert validation_result.route_decision is not None
            assert validation_result.failure_state is not None
            feedback = validation_result.feedback
            route = validation_result.route_decision
            failure_state = validation_result.failure_state

            try:
                _validate_repair_context(
                    feedback,
                    route,
                    failure_state,
                    public_testbench_code=request.public_testbench_code,
                )
            except (TypeError, ValueError):
                return self._result(
                    CandidateRepairStopReason.TERMINAL_FEEDBACK,
                    initial_candidate,
                    current_candidate,
                    last_validated_candidate,
                    last_proposal,
                    attempts,
                )
            if self._budget_exhausted():
                return self._result(
                    CandidateRepairStopReason.BUDGET_EXHAUSTED,
                    initial_candidate,
                    current_candidate,
                    last_validated_candidate,
                    last_proposal,
                    attempts,
                )

        return self._result(
            CandidateRepairStopReason.ATTEMPTS_EXHAUSTED,
            initial_candidate,
            current_candidate,
            last_validated_candidate,
            last_proposal,
            attempts,
        )

    def _consume_llm_launch(self) -> None:
        self._budget.consume(llm_calls=1)

    def _record_model_usage(self, response: ModelResponse) -> None:
        self._budget.record_observed(
            tokens=response.usage.total_tokens,
            cost_usd=(
                0.0 if response.usage.cost_usd is None else float(response.usage.cost_usd)
            ),
        )

    def _new_response(self, previous_count: int) -> ModelResponse | None:
        responses = self._model_adapter.responses
        if len(responses) == previous_count + 1:
            return responses[-1]
        return None

    def _safe_snapshot(self) -> BudgetUsage:
        try:
            return self._budget.snapshot()
        except BudgetExceededError:
            return self._budget.record_observed()

    def _budget_exhausted(self) -> bool:
        try:
            return self._budget.exhausted()
        except BudgetExceededError:
            return True

    def _result(
        self,
        reason: CandidateRepairStopReason,
        initial_candidate: str,
        current_candidate: str,
        last_validated_candidate: str | None,
        last_proposal: str | None,
        attempts: list[CandidateRepairAttempt],
    ) -> CandidateRepairLoopResult:
        return CandidateRepairLoopResult(
            stop_reason=reason,
            initial_candidate=initial_candidate,
            current_candidate=current_candidate,
            last_validated_candidate=last_validated_candidate,
            last_proposal=last_proposal,
            attempts=tuple(attempts),
            budget_usage=self._safe_snapshot(),
        )


def _build_prompt(
    request: CandidateRepairLoopRequest,
    *,
    feedback: FeedbackReport,
    failure_state: ValidationState,
    current_candidate: str,
    attempt: int,
    prior_summaries: tuple[str, ...],
):
    inputs = CandidateRepairPromptInputs(
        task=request.task,
        feedback=feedback,
        candidate_code=current_candidate,
        original_code=request.original_code,
        public_testbench_code=request.public_testbench_code,
        attempt=attempt,
        max_attempts=request.max_attempts,
        family_instruction=request.family_instruction,
        prior_attempt_summaries=prior_summaries,
        approved_memory_snippets=request.approved_memory_snippets,
    )
    if failure_state is ValidationState.PREFLIGHT:
        return build_candidate_compile_repair_prompt(inputs)
    if failure_state is ValidationState.CSYNTH:
        return build_candidate_csynth_repair_prompt(inputs)
    if failure_state is ValidationState.PUBLIC_EVALUATION:
        return build_candidate_public_csim_repair_prompt(inputs)
    raise ValueError(f"Unsupported candidate repair state: {failure_state.value}")


def _validate_completed_prefix(
    result: CandidateValidationResult,
    required_prefix: tuple[ValidationState, ...],
) -> None:
    if result.completed_stages[: len(required_prefix)] != required_prefix:
        raise ValueError(
            "validator skipped a required validation stage; changed candidates must restart from preflight"
        )


def _required_prefix(state: ValidationState) -> tuple[ValidationState, ...]:
    try:
        return _REQUIRED_PREFIX_BY_REPAIR_STATE[state]
    except KeyError as exc:
        raise ValueError(f"No repair validation prefix for {state.value}") from exc


def _validate_repair_context(
    feedback: FeedbackReport,
    route: FeedbackRouteDecision,
    failure_state: ValidationState,
    *,
    public_testbench_code: str | None,
) -> None:
    if not isinstance(feedback, FeedbackReport):
        raise TypeError("feedback must be a FeedbackReport")
    if not isinstance(route, FeedbackRouteDecision):
        raise TypeError("route must be a FeedbackRouteDecision")
    state = _validation_state(failure_state)
    if state not in _ALLOWED_REPAIR_STATES:
        raise ValueError(
            "candidate repair is allowed only for preflight, csynth, or Public evaluation"
        )
    if route.action is not FeedbackRouteAction.REPAIR_CANDIDATE:
        raise ValueError("candidate repair requires route=repair_candidate")
    if route.source_report_id != feedback.report_id:
        raise ValueError("route source_report_id must match feedback report")
    if feedback.metadata.get("evidence_view") != "agent_safe":
        raise ValueError("candidate repair requires agent_safe feedback")
    if route.metadata.get("evidence_view") != "agent_safe":
        raise ValueError("candidate repair route must be agent_safe")
    if not feedback.blocking:
        raise ValueError("candidate repair requires blocking feedback")
    selected_ids = set(route.selected_feedback_ids)
    if not selected_ids:
        raise ValueError("candidate repair requires selected feedback")
    item_by_id = {item.feedback_id: item for item in feedback.items}
    if not selected_ids.issubset(item_by_id):
        raise ValueError("route selected an unknown feedback item")
    blocking_ids = {item.feedback_id for item in feedback.items if item.blocking}
    if selected_ids != blocking_ids:
        raise ValueError("candidate repair must select all blocking feedback")
    allowed_stages = _STAGE_BY_REPAIR_STATE[state]
    for feedback_id in selected_ids:
        item = item_by_id[feedback_id]
        if not item.blocking:
            raise ValueError("selected candidate feedback must be blocking")
        if item.owner is not FeedbackOwner.CANDIDATE:
            raise ValueError("selected feedback must be candidate-owned")
        if item.stage not in allowed_stages:
            raise ValueError("selected feedback stage does not match failure state")
    if state is ValidationState.PUBLIC_EVALUATION:
        if public_testbench_code is None:
            raise ValueError("Public CSIM repair requires a Public testbench")
        if feedback.metadata.get("evaluation_split") != EvaluationSplit.PUBLIC.value:
            raise ValueError("Public repair requires split=public")
        if feedback.metadata.get("feedback_visible_to_agent") is not True:
            raise ValueError("Public repair requires visible agent feedback")


def _response_dict(response: ModelResponse | None) -> dict[str, Any] | None:
    if response is None:
        return None
    return {
        "text": response.text,
        "model": response.model,
        "usage": {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
            "cost_usd": response.usage.cost_usd,
        },
        "finish_reason": response.finish_reason,
        "metadata": _json_mapping(response.metadata, "response.metadata"),
    }


def _validation_state(value: Any) -> ValidationState:
    if isinstance(value, ValidationState):
        return value
    try:
        return ValidationState(str(value))
    except ValueError as exc:
        raise ValueError(f"Unsupported validation state: {value!r}") from exc


def _required_text(value: str, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _optional_text(value: str | None, field_name: str) -> None:
    if value is not None:
        _required_text(value, field_name)


def _text_tuple(value: tuple[str, ...], field_name: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field_name} must be a tuple of strings")
    for item in value:
        _required_text(item, field_name)


def _json_mapping(value: Mapping[str, Any], field_name: str) -> dict[str, Any]:
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
        raise ValueError(f"{field_name} must be finite JSON data") from exc
    if not isinstance(copied, dict):
        raise TypeError(f"{field_name} must normalize to an object")
    return copied
