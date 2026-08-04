'''Execute deterministic validation states through injected handlers.'''

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import json
from typing import Any

from agrefactor.config import EvaluationSplit
from agrefactor.evaluation.feedback_coordination import (
    ValidationFeedbackCoordinator,
)
from agrefactor.evaluation.feedback_routing import (
    FeedbackRouteAction,
    FeedbackRouteDecision,
)
from agrefactor.evaluation.validation_state import (
    ValidationState,
    ValidationTransition,
)
from agrefactor.evidence import FeedbackItem, FeedbackReport

from .runner import RunContext


ValidationStageHandler = Callable[[RunContext], FeedbackReport]


@dataclass(frozen=True, slots=True)
class ValidationStepRecord:
    '''Safe record of one executed validation state.'''

    step_id: str
    state: ValidationState
    evidence_view: str
    route_action: FeedbackRouteAction
    transition: ValidationTransition
    source: str
    source_report_id: str | None
    source_item_count: int
    source_blocking: bool
    selected_feedback_items: tuple[FeedbackItem, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    schema_version = 1

    def __post_init__(self) -> None:
        step_id = _required(self.step_id, "step_id")
        state = _coerce_state(self.state)
        evidence_view = _required(
            self.evidence_view,
            "evidence_view",
        )
        if evidence_view not in {"agent_safe", "operator_full"}:
            raise ValueError(
                "evidence_view must be agent_safe or operator_full"
            )

        route_action = (
            self.route_action
            if isinstance(self.route_action, FeedbackRouteAction)
            else FeedbackRouteAction(str(self.route_action))
        )
        if not isinstance(self.transition, ValidationTransition):
            raise TypeError(
                "transition must be a ValidationTransition"
            )
        if self.transition.current_state is not state:
            raise ValueError(
                "transition current_state must match step state"
            )

        source = _required(self.source, "source")
        source_report_id = _optional(
            self.source_report_id,
            "source_report_id",
        )
        if (
            isinstance(self.source_item_count, bool)
            or not isinstance(self.source_item_count, int)
        ):
            raise TypeError(
                "source_item_count must be an integer"
            )
        if self.source_item_count < 0:
            raise ValueError(
                "source_item_count must be non-negative"
            )
        if not isinstance(self.source_blocking, bool):
            raise TypeError(
                "source_blocking must be a boolean"
            )

        selected = tuple(self.selected_feedback_items)
        if not all(
            isinstance(item, FeedbackItem)
            for item in selected
        ):
            raise TypeError(
                "selected_feedback_items must contain FeedbackItem"
            )
        selected_ids = tuple(
            item.feedback_id for item in selected
        )
        if len(selected_ids) != len(set(selected_ids)):
            raise ValueError(
                "selected_feedback_items must have unique IDs"
            )
        if selected_ids != self.transition.selected_feedback_ids:
            raise ValueError(
                "selected feedback must match transition IDs"
            )

        hidden = state is ValidationState.HIDDEN_EVALUATION
        if hidden:
            if evidence_view != "operator_full":
                raise ValueError(
                    "hidden state must use operator_full evidence"
                )
            if source_report_id is not None:
                raise ValueError(
                    "hidden source_report_id must not be retained"
                )
            if selected:
                raise ValueError(
                    "hidden feedback items must not be retained"
                )
        elif evidence_view != "agent_safe":
            raise ValueError(
                "non-hidden state must use agent_safe evidence"
            )

        if self.transition.agent_feedback_allowed:
            if not selected:
                raise ValueError(
                    "agent feedback requires selected items"
                )
        elif selected:
            raise ValueError(
                "selected items are forbidden without agent feedback"
            )

        object.__setattr__(self, "step_id", step_id)
        object.__setattr__(self, "state", state)
        object.__setattr__(
            self,
            "evidence_view",
            evidence_view,
        )
        object.__setattr__(
            self,
            "route_action",
            route_action,
        )
        object.__setattr__(self, "source", source)
        object.__setattr__(
            self,
            "source_report_id",
            source_report_id,
        )
        object.__setattr__(
            self,
            "selected_feedback_items",
            selected,
        )
        object.__setattr__(
            self,
            "metadata",
            _json_mapping(self.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "step_id": self.step_id,
            "state": self.state.value,
            "evidence_view": self.evidence_view,
            "route_action": self.route_action.value,
            "transition": self.transition.to_dict(),
            "source": self.source,
            "source_report_id": self.source_report_id,
            "source_item_count": self.source_item_count,
            "source_blocking": self.source_blocking,
            "selected_feedback_items": [
                item.to_dict()
                for item in self.selected_feedback_items
            ],
            "metadata": _json_mapping(self.metadata),
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "ValidationStepRecord":
        if not isinstance(payload, Mapping):
            raise TypeError("step payload must be a mapping")
        if payload.get("schema_version", 1) != 1:
            raise ValueError(
                "Unsupported validation step schema_version"
            )
        return cls(
            step_id=payload["step_id"],
            state=payload["state"],
            evidence_view=payload["evidence_view"],
            route_action=payload["route_action"],
            transition=ValidationTransition.from_dict(
                payload["transition"]
            ),
            source=payload["source"],
            source_report_id=payload.get("source_report_id"),
            source_item_count=payload["source_item_count"],
            source_blocking=payload["source_blocking"],
            selected_feedback_items=tuple(
                FeedbackItem.from_dict(item)
                for item in payload.get(
                    "selected_feedback_items",
                    (),
                )
            ),
            metadata=payload.get("metadata", {}),
        )


@dataclass(frozen=True, slots=True)
class ValidationOrchestrationResult:
    '''Serializable result of one validation orchestration.'''

    validation_id: str
    task_id: str
    initial_state: ValidationState
    final_state: ValidationState
    steps: tuple[ValidationStepRecord, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    schema_version = 1

    def __post_init__(self) -> None:
        validation_id = _required(
            self.validation_id,
            "validation_id",
        )
        task_id = _required(self.task_id, "task_id")
        initial_state = _coerce_state(self.initial_state)
        final_state = _coerce_state(self.final_state)
        steps = tuple(self.steps)

        if not steps:
            raise ValueError(
                "orchestration requires at least one step"
            )
        if not all(
            isinstance(step, ValidationStepRecord)
            for step in steps
        ):
            raise TypeError(
                "steps must contain ValidationStepRecord"
            )
        if steps[0].state is not initial_state:
            raise ValueError(
                "first step must start at initial_state"
            )
        for previous, current in zip(steps, steps[1:]):
            if (
                previous.transition.next_state
                is not current.state
            ):
                raise ValueError(
                    "validation steps must form a state chain"
                )
        if steps[-1].transition.next_state is not final_state:
            raise ValueError(
                "final_state must match last transition"
            )
        if final_state.active:
            raise ValueError(
                "orchestration cannot finish in an active state"
            )

        object.__setattr__(
            self,
            "validation_id",
            validation_id,
        )
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(
            self,
            "initial_state",
            initial_state,
        )
        object.__setattr__(
            self,
            "final_state",
            final_state,
        )
        object.__setattr__(self, "steps", steps)
        object.__setattr__(
            self,
            "metadata",
            _json_mapping(self.metadata),
        )

    @property
    def accepted(self) -> bool:
        return self.final_state is ValidationState.ACCEPTED

    @property
    def repair_pending(self) -> bool:
        return (
            self.final_state
            is ValidationState.REPAIR_PENDING
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "validation_id": self.validation_id,
            "task_id": self.task_id,
            "initial_state": self.initial_state.value,
            "final_state": self.final_state.value,
            "steps": [
                step.to_dict() for step in self.steps
            ],
            "metadata": _json_mapping(self.metadata),
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "ValidationOrchestrationResult":
        if not isinstance(payload, Mapping):
            raise TypeError("result payload must be a mapping")
        if payload.get("schema_version", 1) != 1:
            raise ValueError(
                "Unsupported orchestration schema_version"
            )
        return cls(
            validation_id=payload["validation_id"],
            task_id=payload["task_id"],
            initial_state=payload["initial_state"],
            final_state=payload["final_state"],
            steps=tuple(
                ValidationStepRecord.from_dict(step)
                for step in payload["steps"]
            ),
            metadata=payload.get("metadata", {}),
        )



@dataclass(frozen=True, slots=True)
class ValidationExecutionOutcome:
    """Internal-capable outcome with a safe serialized projection.

    ``terminal_report`` and ``terminal_decision`` are retained only in memory
    for a legal repair handoff. They are intentionally omitted from ``to_dict``.
    """

    result: ValidationOrchestrationResult
    terminal_report: FeedbackReport | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    terminal_decision: FeedbackRouteDecision | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    schema_version = 1

    def __post_init__(self) -> None:
        if not isinstance(
            self.result,
            ValidationOrchestrationResult,
        ):
            raise TypeError(
                "result must be ValidationOrchestrationResult"
            )

        if self.result.accepted:
            if (
                self.terminal_report is not None
                or self.terminal_decision is not None
            ):
                raise ValueError(
                    "accepted validation must not retain "
                    "terminal feedback"
                )
            return

        if not isinstance(
            self.terminal_report,
            FeedbackReport,
        ):
            raise TypeError(
                "non-accepted validation requires "
                "terminal_report"
            )
        if not isinstance(
            self.terminal_decision,
            FeedbackRouteDecision,
        ):
            raise TypeError(
                "non-accepted validation requires "
                "terminal_decision"
            )
        if (
            self.terminal_decision.source_report_id
            != self.terminal_report.report_id
        ):
            raise ValueError(
                "terminal decision must reference "
                "terminal report"
            )
        terminal_step = self.result.steps[-1]
        if (
            self.terminal_decision.action
            is not terminal_step.route_action
        ):
            raise ValueError(
                "terminal decision action must match "
                "the final validation step"
            )
        expected_view = (
            "operator_full"
            if terminal_step.state
            is ValidationState.HIDDEN_EVALUATION
            else "agent_safe"
        )
        if (
            self.terminal_report.metadata.get(
                "evidence_view"
            )
            != expected_view
        ):
            raise ValueError(
                "terminal feedback evidence view does "
                "not match the terminal state"
            )

    @property
    def terminal_state(self) -> ValidationState:
        return self.result.steps[-1].state

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "result": self.result.to_dict(),
            "terminal_state": self.terminal_state.value,
            "terminal_feedback_available": (
                self.terminal_report is not None
            ),
            "terminal_evidence_view": (
                None
                if self.terminal_report is None
                else self.terminal_report.metadata.get(
                    "evidence_view"
                )
            ),
            "terminal_feedback_serialized": False,
        }


class ValidationOrchestrator:
    '''Execute an acyclic validation plan with shared services.

    Handlers are injected and return normalized FeedbackReport values.
    This class does not import legacy tools, call a model, or repair
    source code.
    '''

    orchestrator_version = 1

    def __init__(
        self,
        handlers: Mapping[
            ValidationState | str,
            ValidationStageHandler,
        ],
    ) -> None:
        normalized: dict[
            ValidationState,
            ValidationStageHandler,
        ] = {}
        for raw_state, handler in handlers.items():
            state = _coerce_state(raw_state)
            if not state.active:
                raise ValueError(
                    f"Handler state must be active: {state.value}"
                )
            if state in normalized:
                raise ValueError(
                    f"Duplicate validation handler: {state.value}"
                )
            if not callable(handler):
                raise TypeError(
                    f"Handler for {state.value} must be callable"
                )
            normalized[state] = handler
        self._handlers = normalized

    def run(
        self,
        context: RunContext,
        *,
        validation_id: str,
    ) -> ValidationOrchestrationResult:
        return self.run_detailed(
            context,
            validation_id=validation_id,
        ).result

    def run_detailed(
        self,
        context: RunContext,
        *,
        validation_id: str,
    ) -> ValidationExecutionOutcome:
        if not isinstance(context, RunContext):
            raise TypeError(
                "context must be a RunContext"
            )

        resolved_id = _required(
            validation_id,
            "validation_id",
        )
        required_states = self._required_states(context)
        missing = tuple(
            state.value
            for state in required_states
            if state not in self._handlers
        )
        if missing:
            raise ValueError(
                "Missing validation handlers: "
                + ", ".join(missing)
            )

        coordinator = ValidationFeedbackCoordinator(
            context.task
        )
        current_state = ValidationState.PREFLIGHT
        steps: list[ValidationStepRecord] = []
        terminal_report: FeedbackReport | None = None
        terminal_decision: FeedbackRouteDecision | None = None

        context.trace.record(
            "validation.started",
            phase="validation",
            status="running",
            metadata={
                "validation_id": resolved_id,
                "initial_state": current_state.value,
                "required_states": [
                    state.value
                    for state in required_states
                ],
            },
        )

        while current_state.active:
            handler = self._handlers[current_state]
            step_id = (
                f"{resolved_id}.step.{len(steps) + 1}"
            )
            context.trace.record(
                "validation.stage.started",
                phase=current_state.value,
                status="running",
                metadata={
                    "validation_id": resolved_id,
                    "step_id": step_id,
                    "state": current_state.value,
                },
            )

            try:
                report = handler(context)
            except Exception as exc:
                context.trace.record(
                    "validation.stage.error",
                    phase=current_state.value,
                    status="error",
                    message=(
                        f"{type(exc).__name__}: {exc}"
                    ),
                    metadata={
                        "validation_id": resolved_id,
                        "step_id": step_id,
                    },
                )
                raise

            if not isinstance(report, FeedbackReport):
                raise TypeError(
                    f"Handler for {current_state.value} returned "
                    f"{type(report).__name__}, expected FeedbackReport"
                )

            coordination, decision = (
                coordinator.coordinate_with_decision(
                    report,
                    current_state,
                    coordination_id=step_id,
                )
            )
            terminal_report = report
            terminal_decision = decision
            hidden = (
                current_state
                is ValidationState.HIDDEN_EVALUATION
            )
            step = ValidationStepRecord(
                step_id=step_id,
                state=current_state,
                evidence_view=coordination.evidence_view,
                route_action=coordination.route_action,
                transition=coordination.transition,
                source=report.source,
                source_report_id=(
                    None if hidden else report.report_id
                ),
                source_item_count=len(report.items),
                source_blocking=report.blocking,
                selected_feedback_items=(
                    coordination.selected_feedback_items
                ),
                metadata={
                    "orchestrator_version": (
                        self.orchestrator_version
                    ),
                    "hidden_source_suppressed": hidden,
                },
            )
            steps.append(step)

            self._record_feedback(
                context,
                validation_id=resolved_id,
                step=step,
                report=report,
            )
            self._record_transition(
                context,
                validation_id=resolved_id,
                step=step,
                decision=decision,
            )

            current_state = step.transition.next_state
            if len(steps) > 8:
                raise RuntimeError(
                    "validation transition guard exceeded"
                )

        result = ValidationOrchestrationResult(
            validation_id=resolved_id,
            task_id=context.task.task_id,
            initial_state=ValidationState.PREFLIGHT,
            final_state=current_state,
            steps=tuple(steps),
            metadata={
                "orchestrator_version": (
                    self.orchestrator_version
                ),
                "step_count": len(steps),
                "accepted": (
                    current_state
                    is ValidationState.ACCEPTED
                ),
            },
        )
        context.trace.record(
            "validation.finished",
            phase="validation",
            status=current_state.value,
            metadata={
                "validation_id": resolved_id,
                "final_state": current_state.value,
                "step_count": len(steps),
            },
        )
        return ValidationExecutionOutcome(
            result=result,
            terminal_report=(
                None if result.accepted else terminal_report
            ),
            terminal_decision=(
                None if result.accepted else terminal_decision
            ),
        )

    @staticmethod
    def _required_states(
        context: RunContext,
    ) -> tuple[ValidationState, ...]:
        states = [ValidationState.PREFLIGHT]
        has_public = any(
            suite.split is EvaluationSplit.PUBLIC
            for suite in context.task.test_suites
        )
        if has_public:
            states.append(ValidationState.PUBLIC_EVALUATION)
        states.append(ValidationState.CSYNTH)
        if has_public:
            states.append(ValidationState.PUBLIC_COSIM)
        if any(
            suite.split is EvaluationSplit.HIDDEN
            for suite in context.task.test_suites
        ):
            states.append(ValidationState.HIDDEN_EVALUATION)
        return tuple(states)

    @staticmethod
    def _record_feedback(
        context: RunContext,
        *,
        validation_id: str,
        step: ValidationStepRecord,
        report: FeedbackReport,
    ) -> None:
        if (
            step.state
            is ValidationState.HIDDEN_EVALUATION
        ):
            metadata = {
                "validation_id": validation_id,
                "step_id": step.step_id,
                "state": step.state.value,
                "evidence_view": "operator_full",
                "source": report.source,
                "item_count": len(report.items),
                "blocking": report.blocking,
                "hidden_source_suppressed": True,
                "feedback_report_summary": (
                    _operator_feedback_report_summary(
                        report,
                        step_id=step.step_id,
                    )
                ),
            }
        else:
            metadata = {
                "validation_id": validation_id,
                "step_id": step.step_id,
                "state": step.state.value,
                "evidence_view": "agent_safe",
                "feedback_report": report.to_dict(),
            }

        context.trace.record(
            "validation.feedback",
            phase=step.state.value,
            status=(
                "blocking"
                if report.blocking
                else "non_blocking"
            ),
            metadata=metadata,
        )

    @staticmethod
    def _record_transition(
        context: RunContext,
        *,
        validation_id: str,
        step: ValidationStepRecord,
        decision: FeedbackRouteDecision,
    ) -> None:
        if not isinstance(decision, FeedbackRouteDecision):
            raise TypeError(
                "decision must be a FeedbackRouteDecision"
            )

        transition = step.transition
        if (
            transition.source_decision_id
            != decision.decision_id
        ):
            raise ValueError(
                "transition must reference the recorded decision"
            )

        hidden = (
            step.state
            is ValidationState.HIDDEN_EVALUATION
        )
        evidence_view = (
            "operator_full" if hidden else "agent_safe"
        )
        source_report_id = (
            _redacted_hidden_report_id(step.step_id)
            if hidden
            else decision.source_report_id
        )
        metadata: dict[str, Any] = {
            "validation_id": validation_id,
            "step_id": step.step_id,
            "current_state": (
                transition.current_state.value
            ),
            "next_state": transition.next_state.value,
            "kind": transition.kind.value,
            "route_action": step.route_action.value,
            "repair_allowed": transition.repair_allowed,
            "agent_feedback_allowed": (
                transition.agent_feedback_allowed
            ),
            "selected_feedback_count": len(
                step.selected_feedback_items
            ),
            "evidence_view": evidence_view,
            "route_decision_summary": (
                _route_decision_summary(
                    decision,
                    source_report_id=source_report_id,
                    source_report_redacted=hidden,
                )
            ),
        }
        if hidden:
            metadata["hidden_feedback_suppressed"] = True
        else:
            metadata["selected_feedback_items"] = [
                item.to_dict()
                for item in step.selected_feedback_items
            ]

        context.trace.record(
            "validation.transition",
            phase=step.state.value,
            status=transition.next_state.value,
            message=transition.reason,
            metadata=metadata,
        )


def _redacted_hidden_report_id(step_id: str) -> str:
    return f"{_required(step_id, 'step_id')}.hidden-report"


def _operator_feedback_report_summary(
    report: FeedbackReport,
    *,
    step_id: str,
) -> dict[str, Any]:
    """Return an operator-only Hidden feedback audit projection."""

    if not isinstance(report, FeedbackReport):
        raise TypeError("report must be a FeedbackReport")

    return {
        "schema_version": 1,
        "report_id": _redacted_hidden_report_id(step_id),
        "source": report.source,
        "item_count": len(report.items),
        "blocking": report.blocking,
        "items": [
            {
                "stage": item.stage.value,
                "category": item.category.value,
                "severity": item.severity.value,
                "owner": item.owner.value,
                "blocking": item.blocking,
            }
            for item in report.items
        ],
        "source_report_id_redacted": True,
        "item_identifiers_retained": False,
        "item_text_retained": False,
        "source_evidence_retained": False,
    }


def _route_decision_summary(
    decision: FeedbackRouteDecision,
    *,
    source_report_id: str,
    source_report_redacted: bool,
) -> dict[str, Any]:
    """Return a bounded route-decision audit projection."""

    if not isinstance(decision, FeedbackRouteDecision):
        raise TypeError(
            "decision must be a FeedbackRouteDecision"
        )

    candidate_actions = decision.metadata.get(
        "candidate_actions",
        [],
    )
    if not isinstance(candidate_actions, list):
        candidate_actions = []

    return {
        "schema_version": 1,
        "decision_id": decision.decision_id,
        "source_report_id": _required(
            source_report_id,
            "source_report_id",
        ),
        "action": decision.action.value,
        "blocking_feedback_count": len(
            decision.blocking_feedback_ids
        ),
        "selected_feedback_count": len(
            decision.selected_feedback_ids
        ),
        "advisory_feedback_count": len(
            decision.advisory_feedback_ids
        ),
        "candidate_actions": [
            str(action)
            for action in candidate_actions
        ],
        "source_report_redacted": source_report_redacted,
        "feedback_ids_retained": False,
        "reason_retained": False,
    }



def _coerce_state(
    value: ValidationState | str,
) -> ValidationState:
    if isinstance(value, ValidationState):
        return value
    try:
        return ValidationState(str(value))
    except ValueError as exc:
        raise ValueError(
            f"Unsupported validation state: {value!r}"
        ) from exc


def _required(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(
            f"{field_name} must be a string"
        )
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(
            f"{field_name} must not be empty"
        )
    return cleaned


def _optional(
    value: str | None,
    field_name: str,
) -> str | None:
    if value is None:
        return None
    return _required(value, field_name)


def _json_mapping(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("metadata must be a mapping")
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
            "metadata must be finite JSON data"
        ) from exc
    return copied
