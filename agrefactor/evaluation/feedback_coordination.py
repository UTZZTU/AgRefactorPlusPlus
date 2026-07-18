"""Coordinate safe feedback routing and validation transitions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import json
from typing import Any

from agrefactor.config import TaskSpec
from agrefactor.evidence import FeedbackItem, FeedbackReport

from .feedback_routing import (
    FeedbackRouteAction,
    FeedbackRouteDecision,
    FeedbackRouter,
)
from .validation_state import (
    ValidationState,
    ValidationStateMachine,
    ValidationTransition,
)


@dataclass(frozen=True, slots=True)
class ValidationFeedbackResult:
    """Safe, serializable output of one coordination step."""

    coordination_id: str
    source_report_id: str
    evidence_view: str
    route_action: FeedbackRouteAction
    transition: ValidationTransition
    selected_feedback_items: tuple[FeedbackItem, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    schema_version = 1

    def __post_init__(self) -> None:
        coordination_id = _required(
            self.coordination_id,
            "coordination_id",
        )
        source_report_id = _required(
            self.source_report_id,
            "source_report_id",
        )
        evidence_view = _required(
            self.evidence_view,
            "evidence_view",
        )
        if evidence_view not in {
            "agent_safe",
            "operator_full",
        }:
            raise ValueError(
                "evidence_view must be agent_safe or operator_full"
            )

        route_action = (
            self.route_action
            if isinstance(
                self.route_action,
                FeedbackRouteAction,
            )
            else FeedbackRouteAction(
                str(self.route_action)
            )
        )
        if not isinstance(
            self.transition,
            ValidationTransition,
        ):
            raise TypeError(
                "transition must be a ValidationTransition"
            )

        selected: list[FeedbackItem] = []
        selected_ids: list[str] = []
        for item in self.selected_feedback_items:
            if not isinstance(item, FeedbackItem):
                raise TypeError(
                    "selected_feedback_items must contain "
                    "FeedbackItem values"
                )
            selected.append(item)
            selected_ids.append(item.feedback_id)

        if len(selected_ids) != len(set(selected_ids)):
            raise ValueError(
                "selected_feedback_items must have unique IDs"
            )
        if tuple(selected_ids) != (
            self.transition.selected_feedback_ids
        ):
            raise ValueError(
                "selected feedback must match transition IDs"
            )

        if self.transition.agent_feedback_allowed:
            if evidence_view != "agent_safe":
                raise ValueError(
                    "agent feedback requires agent_safe evidence"
                )
            if not selected:
                raise ValueError(
                    "agent feedback requires selected items"
                )
        elif selected:
            raise ValueError(
                "selected items are forbidden when agent feedback "
                "is not allowed"
            )

        object.__setattr__(
            self,
            "coordination_id",
            coordination_id,
        )
        object.__setattr__(
            self,
            "source_report_id",
            source_report_id,
        )
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
        object.__setattr__(
            self,
            "selected_feedback_items",
            tuple(selected),
        )
        object.__setattr__(
            self,
            "metadata",
            _json_mapping(self.metadata),
        )

    @property
    def agent_feedback_allowed(self) -> bool:
        return self.transition.agent_feedback_allowed

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "coordination_id": self.coordination_id,
            "source_report_id": self.source_report_id,
            "evidence_view": self.evidence_view,
            "route_action": self.route_action.value,
            "transition": self.transition.to_dict(),
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
    ) -> "ValidationFeedbackResult":
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping")

        allowed = {
            "schema_version",
            "coordination_id",
            "source_report_id",
            "evidence_view",
            "route_action",
            "transition",
            "selected_feedback_items",
            "metadata",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(
                "Unknown coordination fields: "
                + ", ".join(sorted(unknown))
            )
        if payload.get("schema_version", 1) != 1:
            raise ValueError(
                "Unsupported coordination schema_version"
            )

        return cls(
            coordination_id=payload["coordination_id"],
            source_report_id=payload["source_report_id"],
            evidence_view=payload["evidence_view"],
            route_action=payload["route_action"],
            transition=ValidationTransition.from_dict(
                payload["transition"]
            ),
            selected_feedback_items=tuple(
                FeedbackItem.from_dict(item)
                for item in payload.get(
                    "selected_feedback_items",
                    (),
                )
            ),
            metadata=payload.get("metadata", {}),
        )


class ValidationFeedbackCoordinator:
    """Compose a report, router, and transition policy safely.

    Non-hidden validation states require an ``agent_safe`` report.
    Hidden evaluation requires an ``operator_full`` report and never
    returns selected feedback items.
    """

    coordinator_version = 1

    def __init__(self, task: TaskSpec) -> None:
        if not isinstance(task, TaskSpec):
            raise TypeError("task must be a TaskSpec")
        self._router = FeedbackRouter()
        self._state_machine = ValidationStateMachine(
            task
        )

    def coordinate(
        self,
        report: FeedbackReport,
        current_state: ValidationState | str,
        *,
        coordination_id: str,
        decision_id: str | None = None,
        transition_id: str | None = None,
    ) -> ValidationFeedbackResult:
        result, _ = self.coordinate_with_decision(
            report,
            current_state,
            coordination_id=coordination_id,
            decision_id=decision_id,
            transition_id=transition_id,
        )
        return result

    def coordinate_with_decision(
        self,
        report: FeedbackReport,
        current_state: ValidationState | str,
        *,
        coordination_id: str,
        decision_id: str | None = None,
        transition_id: str | None = None,
    ) -> tuple[ValidationFeedbackResult, FeedbackRouteDecision]:
        if not isinstance(report, FeedbackReport):
            raise TypeError(
                "report must be a FeedbackReport"
            )

        state = (
            current_state
            if isinstance(current_state, ValidationState)
            else ValidationState(str(current_state))
        )
        if not state.active:
            raise ValueError(
                f"Cannot coordinate inactive state {state.value}"
            )

        cid = _required(
            coordination_id,
            "coordination_id",
        )
        view = report.metadata.get("evidence_view")
        expected_view = (
            "operator_full"
            if state is ValidationState.HIDDEN_EVALUATION
            else "agent_safe"
        )
        if view != expected_view:
            raise ValueError(
                f"{state.value} requires {expected_view} feedback"
            )

        resolved_decision_id = (
            _required(decision_id, "decision_id")
            if decision_id is not None
            else f"{cid}.route"
        )
        resolved_transition_id = (
            _required(
                transition_id,
                "transition_id",
            )
            if transition_id is not None
            else f"{cid}.transition"
        )

        decision = self._router.route(
            report,
            decision_id=resolved_decision_id,
        )
        transition = self._state_machine.transition(
            state,
            decision,
            transition_id=resolved_transition_id,
        )

        selected_items: tuple[FeedbackItem, ...] = ()
        if transition.agent_feedback_allowed:
            item_by_id = {
                item.feedback_id: item
                for item in report.items
            }
            try:
                selected_items = tuple(
                    item_by_id[feedback_id]
                    for feedback_id in (
                        transition.selected_feedback_ids
                    )
                )
            except KeyError as exc:
                raise ValueError(
                    "transition selected an unknown feedback ID"
                ) from exc

        hidden = (
            state is ValidationState.HIDDEN_EVALUATION
        )

        result = ValidationFeedbackResult(
            coordination_id=cid,
            source_report_id=(
                "hidden-redacted"
                if hidden
                else report.report_id
            ),
            evidence_view=expected_view,
            route_action=decision.action,
            transition=transition,
            selected_feedback_items=selected_items,
            metadata={
                "coordinator_version": (
                    self.coordinator_version
                ),
                "source": report.source,
                "current_state": state.value,
                "source_item_count": len(report.items),
                "source_blocking": report.blocking,
                "source_report_redacted": hidden,
                "selected_feedback_count": len(
                    selected_items
                ),
                "route_candidate_actions": (
                    decision.metadata.get(
                        "candidate_actions",
                        [],
                    )
                ),
            },
        )
        return result, decision


def _required(
    value: str,
    field_name: str,
) -> str:
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
