'''Plan deterministic validation-state transitions.'''

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
import json
from typing import Any

from agrefactor.config import EvaluationSplit, TaskSpec

from .feedback_routing import (
    FeedbackRouteAction,
    FeedbackRouteDecision,
)


class ValidationState(str, Enum):
    '''Coarse-grained validation workflow state.'''

    PREFLIGHT = "preflight"
    CSYNTH = "csynth"
    PUBLIC_EVALUATION = "public_evaluation"
    HIDDEN_EVALUATION = "hidden_evaluation"
    REPAIR_PENDING = "repair_pending"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"
    ACCEPTED = "accepted"
    REJECTED = "rejected"

    @property
    def active(self) -> bool:
        return self in {
            ValidationState.PREFLIGHT,
            ValidationState.CSYNTH,
            ValidationState.PUBLIC_EVALUATION,
            ValidationState.HIDDEN_EVALUATION,
        }

    @property
    def terminal(self) -> bool:
        return self in {
            ValidationState.REVIEW_REQUIRED,
            ValidationState.BLOCKED,
            ValidationState.ACCEPTED,
            ValidationState.REJECTED,
        }


class ValidationTransitionKind(str, Enum):
    ADVANCE = "advance"
    REQUEST_REPAIR = "request_repair"
    REQUIRE_REVIEW = "require_review"
    BLOCK = "block"
    ACCEPT = "accept"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class ValidationTransition:
    '''Serializable result of a deterministic state transition.'''

    transition_id: str
    current_state: ValidationState
    next_state: ValidationState
    kind: ValidationTransitionKind
    route_action: FeedbackRouteAction
    reason: str
    source_decision_id: str
    resume_state: ValidationState | None = None
    repair_allowed: bool = False
    agent_feedback_allowed: bool = False
    selected_feedback_ids: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    schema_version = 1

    def __post_init__(self) -> None:
        transition_id = _required(
            self.transition_id,
            "transition_id",
        )
        reason = _required(self.reason, "reason")
        source_decision_id = _required(
            self.source_decision_id,
            "source_decision_id",
        )
        current_state = _enum(
            self.current_state,
            ValidationState,
            "current_state",
        )
        next_state = _enum(
            self.next_state,
            ValidationState,
            "next_state",
        )
        kind = _enum(
            self.kind,
            ValidationTransitionKind,
            "kind",
        )
        route_action = _enum(
            self.route_action,
            FeedbackRouteAction,
            "route_action",
        )
        resume_state = (
            None
            if self.resume_state is None
            else _enum(
                self.resume_state,
                ValidationState,
                "resume_state",
            )
        )

        if not isinstance(self.repair_allowed, bool):
            raise TypeError("repair_allowed must be boolean")
        if not isinstance(
            self.agent_feedback_allowed,
            bool,
        ):
            raise TypeError(
                "agent_feedback_allowed must be boolean"
            )

        selected = tuple(
            _required(value, "selected_feedback_ids")
            for value in self.selected_feedback_ids
        )
        if len(selected) != len(set(selected)):
            raise ValueError(
                "selected_feedback_ids must be unique"
            )

        if next_state is ValidationState.REPAIR_PENDING:
            if not self.repair_allowed:
                raise ValueError(
                    "REPAIR_PENDING requires repair_allowed"
                )
            if resume_state is None or not resume_state.active:
                raise ValueError(
                    "REPAIR_PENDING requires active resume_state"
                )
            if self.agent_feedback_allowed and not selected:
                raise ValueError(
                    "agent feedback requires selected feedback"
                )
        else:
            if self.repair_allowed:
                raise ValueError(
                    "repair_allowed is only valid for repair"
                )
            if self.agent_feedback_allowed:
                raise ValueError(
                    "agent feedback is only valid for repair"
                )
            if resume_state is not None:
                raise ValueError(
                    "resume_state is only valid for repair"
                )

        object.__setattr__(
            self,
            "transition_id",
            transition_id,
        )
        object.__setattr__(
            self,
            "current_state",
            current_state,
        )
        object.__setattr__(
            self,
            "next_state",
            next_state,
        )
        object.__setattr__(self, "kind", kind)
        object.__setattr__(
            self,
            "route_action",
            route_action,
        )
        object.__setattr__(self, "reason", reason)
        object.__setattr__(
            self,
            "source_decision_id",
            source_decision_id,
        )
        object.__setattr__(
            self,
            "resume_state",
            resume_state,
        )
        object.__setattr__(
            self,
            "selected_feedback_ids",
            selected,
        )
        object.__setattr__(
            self,
            "metadata",
            _json_mapping(self.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "transition_id": self.transition_id,
            "current_state": self.current_state.value,
            "next_state": self.next_state.value,
            "kind": self.kind.value,
            "route_action": self.route_action.value,
            "reason": self.reason,
            "source_decision_id": self.source_decision_id,
            "resume_state": (
                None
                if self.resume_state is None
                else self.resume_state.value
            ),
            "repair_allowed": self.repair_allowed,
            "agent_feedback_allowed": (
                self.agent_feedback_allowed
            ),
            "selected_feedback_ids": list(
                self.selected_feedback_ids
            ),
            "metadata": _json_mapping(self.metadata),
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "ValidationTransition":
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping")

        allowed = {
            "schema_version",
            "transition_id",
            "current_state",
            "next_state",
            "kind",
            "route_action",
            "reason",
            "source_decision_id",
            "resume_state",
            "repair_allowed",
            "agent_feedback_allowed",
            "selected_feedback_ids",
            "metadata",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(
                "Unknown transition fields: "
                + ", ".join(sorted(unknown))
            )
        if payload.get("schema_version", 1) != 1:
            raise ValueError(
                "Unsupported transition schema_version"
            )

        return cls(
            transition_id=payload["transition_id"],
            current_state=payload["current_state"],
            next_state=payload["next_state"],
            kind=payload["kind"],
            route_action=payload["route_action"],
            reason=payload["reason"],
            source_decision_id=payload[
                "source_decision_id"
            ],
            resume_state=payload.get("resume_state"),
            repair_allowed=payload.get(
                "repair_allowed",
                False,
            ),
            agent_feedback_allowed=payload.get(
                "agent_feedback_allowed",
                False,
            ),
            selected_feedback_ids=tuple(
                payload.get("selected_feedback_ids", ())
            ),
            metadata=payload.get("metadata", {}),
        )


class ValidationStateMachine:
    '''Pure validation transition policy.

    Public feedback may enter bounded repair. Hidden evaluation is
    terminal and never exposes feedback to an agent.
    '''

    policy_version = 1

    _REPAIR = {
        FeedbackRouteAction.REPAIR_TESTBENCH,
        FeedbackRouteAction.REPAIR_CANDIDATE,
        FeedbackRouteAction.REPAIR_ORIGINAL,
    }
    _BLOCK = {
        FeedbackRouteAction.FIX_TOOLCHAIN,
        FeedbackRouteAction.FIX_CONFIGURATION,
        FeedbackRouteAction.FIX_TASK_INPUT,
    }
    _REVIEW = {
        FeedbackRouteAction.REVIEW_UNKNOWN,
        FeedbackRouteAction.REVIEW_MIXED,
    }

    def __init__(self, task: TaskSpec) -> None:
        if not isinstance(task, TaskSpec):
            raise TypeError("task must be a TaskSpec")
        self._task = task
        self._public = tuple(
            suite.suite_id
            for suite in task.test_suites
            if suite.split is EvaluationSplit.PUBLIC
        )
        self._hidden = tuple(
            suite.suite_id
            for suite in task.test_suites
            if suite.split is EvaluationSplit.HIDDEN
        )

    @property
    def initial_state(self) -> ValidationState:
        return ValidationState.PREFLIGHT

    @property
    def public_suite_ids(self) -> tuple[str, ...]:
        return self._public

    @property
    def hidden_suite_ids(self) -> tuple[str, ...]:
        return self._hidden

    def transition(
        self,
        current_state: ValidationState | str,
        decision: FeedbackRouteDecision,
        *,
        transition_id: str,
    ) -> ValidationTransition:
        state = _enum(
            current_state,
            ValidationState,
            "current_state",
        )
        if not state.active:
            raise ValueError(
                f"Cannot route inactive state {state.value}"
            )
        if not isinstance(
            decision,
            FeedbackRouteDecision,
        ):
            raise TypeError(
                "decision must be a FeedbackRouteDecision"
            )

        tid = _required(
            transition_id,
            "transition_id",
        )
        metadata = {
            "policy_version": self.policy_version,
            "task_id": self._task.task_id,
            "public_suite_ids": list(self._public),
            "hidden_suite_ids": list(self._hidden),
            "source_report_id": (
                decision.source_report_id
            ),
            "source_evidence_view": (
                decision.metadata.get("evidence_view")
            ),
        }

        if state is ValidationState.HIDDEN_EVALUATION:
            return self._hidden_transition(
                decision,
                transition_id=tid,
                metadata=metadata,
            )

        action = decision.action

        if (
            action
            is FeedbackRouteAction.CONTINUE_VALIDATION
        ):
            next_state = self._advance(state)
            return ValidationTransition(
                transition_id=tid,
                current_state=state,
                next_state=next_state,
                kind=(
                    ValidationTransitionKind.ACCEPT
                    if next_state is ValidationState.ACCEPTED
                    else ValidationTransitionKind.ADVANCE
                ),
                route_action=action,
                reason=(
                    f"{state.value} completed; "
                    f"next state is {next_state.value}."
                ),
                source_decision_id=decision.decision_id,
                metadata=metadata,
            )

        if (
            action
            is FeedbackRouteAction.STOP_BUDGET_EXHAUSTED
            or action in self._BLOCK
        ):
            return ValidationTransition(
                transition_id=tid,
                current_state=state,
                next_state=ValidationState.BLOCKED,
                kind=ValidationTransitionKind.BLOCK,
                route_action=action,
                reason=(
                    "Validation is blocked by budget or an "
                    "external correction requirement."
                ),
                source_decision_id=decision.decision_id,
                metadata=metadata,
            )

        if action in self._REVIEW:
            return ValidationTransition(
                transition_id=tid,
                current_state=state,
                next_state=ValidationState.REVIEW_REQUIRED,
                kind=(
                    ValidationTransitionKind.REQUIRE_REVIEW
                ),
                route_action=action,
                reason=(
                    "Feedback ownership is unresolved or mixed; "
                    "automated repair is not allowed."
                ),
                source_decision_id=decision.decision_id,
                metadata=metadata,
            )

        if action in self._REPAIR:
            if (
                decision.metadata.get("evidence_view")
                != "agent_safe"
            ):
                raise ValueError(
                    "Repair requires an agent_safe decision"
                )
            if not decision.selected_feedback_ids:
                raise ValueError(
                    "Repair requires selected feedback"
                )

            return ValidationTransition(
                transition_id=tid,
                current_state=state,
                next_state=ValidationState.REPAIR_PENDING,
                kind=ValidationTransitionKind.REQUEST_REPAIR,
                route_action=action,
                reason=(
                    "A single repair owner is known; bounded "
                    "repair may be requested."
                ),
                source_decision_id=decision.decision_id,
                resume_state=state,
                repair_allowed=True,
                agent_feedback_allowed=True,
                selected_feedback_ids=(
                    decision.selected_feedback_ids
                ),
                metadata=metadata,
            )

        raise ValueError(
            f"Unsupported route action {action.value}"
        )

    def _advance(
        self,
        state: ValidationState,
    ) -> ValidationState:
        if state is ValidationState.PREFLIGHT:
            return ValidationState.CSYNTH
        if state is ValidationState.CSYNTH:
            if self._public:
                return ValidationState.PUBLIC_EVALUATION
            if self._hidden:
                return ValidationState.HIDDEN_EVALUATION
            return ValidationState.ACCEPTED
        if state is ValidationState.PUBLIC_EVALUATION:
            if self._hidden:
                return ValidationState.HIDDEN_EVALUATION
            return ValidationState.ACCEPTED
        raise ValueError(
            f"No success transition for {state.value}"
        )

    def _hidden_transition(
        self,
        decision: FeedbackRouteDecision,
        *,
        transition_id: str,
        metadata: Mapping[str, Any],
    ) -> ValidationTransition:
        action = decision.action
        safe_metadata = {
            **metadata,
            "hidden_terminal_policy": True,
            "hidden_feedback_suppressed": (
                action
                is not FeedbackRouteAction.CONTINUE_VALIDATION
            ),
        }

        if (
            action
            is FeedbackRouteAction.CONTINUE_VALIDATION
        ):
            next_state = ValidationState.ACCEPTED
            kind = ValidationTransitionKind.ACCEPT
            reason = "Hidden final evaluation passed."
        elif (
            action
            is FeedbackRouteAction.STOP_BUDGET_EXHAUSTED
            or action in self._BLOCK
        ):
            next_state = ValidationState.BLOCKED
            kind = ValidationTransitionKind.BLOCK
            reason = (
                "Hidden evaluation was blocked by budget or "
                "an external condition; no agent feedback is allowed."
            )
        elif action in self._REVIEW:
            next_state = ValidationState.REVIEW_REQUIRED
            kind = ValidationTransitionKind.REQUIRE_REVIEW
            reason = (
                "Hidden evaluation is ambiguous; operator review "
                "is required without agent feedback."
            )
        elif action in self._REPAIR:
            next_state = ValidationState.REJECTED
            kind = ValidationTransitionKind.REJECT
            reason = (
                "Hidden final evaluation failed; hidden feedback "
                "cannot enter iterative repair."
            )
        else:
            raise ValueError(
                f"Unsupported hidden action {action.value}"
            )

        return ValidationTransition(
            transition_id=transition_id,
            current_state=ValidationState.HIDDEN_EVALUATION,
            next_state=next_state,
            kind=kind,
            route_action=action,
            reason=reason,
            source_decision_id=decision.decision_id,
            metadata=safe_metadata,
        )


def _required(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(
            f"{field_name} must not be empty"
        )
    return cleaned


def _enum(
    value: Any,
    enum_type: type[Enum],
    field_name: str,
) -> Any:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except ValueError as exc:
        raise ValueError(
            f"Unsupported {field_name}: {value!r}"
        ) from exc


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
