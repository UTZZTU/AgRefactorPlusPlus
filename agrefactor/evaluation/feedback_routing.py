"""Route normalized feedback without invoking repair logic."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
import json
from typing import Any

from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackStage,
)


class FeedbackRouteAction(str, Enum):
    """Describe the next high-level action suggested by feedback."""

    CONTINUE_VALIDATION = "continue_validation"
    STOP_BUDGET_EXHAUSTED = "stop_budget_exhausted"
    FIX_TOOLCHAIN = "fix_toolchain"
    FIX_CONFIGURATION = "fix_configuration"
    FIX_TASK_INPUT = "fix_task_input"
    REPAIR_TESTBENCH = "repair_testbench"
    REPAIR_CANDIDATE = "repair_candidate"
    REPAIR_ORIGINAL = "repair_original"
    REVIEW_UNKNOWN = "review_unknown"
    REVIEW_MIXED = "review_mixed"


_ALLOWED_DECISION_FIELDS = frozenset(
    {
        "schema_version",
        "decision_id",
        "action",
        "reason",
        "source_report_id",
        "blocking_feedback_ids",
        "selected_feedback_ids",
        "advisory_feedback_ids",
        "metadata",
    }
)


@dataclass(frozen=True, slots=True)
class FeedbackRouteDecision:
    """Serializable result of deterministic feedback routing."""

    decision_id: str
    action: FeedbackRouteAction
    reason: str
    source_report_id: str
    blocking_feedback_ids: tuple[str, ...] = ()
    selected_feedback_ids: tuple[str, ...] = ()
    advisory_feedback_ids: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    schema_version = 1

    def __post_init__(self) -> None:
        decision_id = _clean_required(
            self.decision_id,
            "FeedbackRouteDecision.decision_id",
        )
        source_report_id = _clean_required(
            self.source_report_id,
            "FeedbackRouteDecision.source_report_id",
        )
        reason = _clean_required(
            self.reason,
            "FeedbackRouteDecision.reason",
        )
        action = (
            self.action
            if isinstance(self.action, FeedbackRouteAction)
            else FeedbackRouteAction(str(self.action))
        )
        blocking = _clean_id_sequence(
            self.blocking_feedback_ids,
            "blocking_feedback_ids",
        )
        selected = _clean_id_sequence(
            self.selected_feedback_ids,
            "selected_feedback_ids",
        )
        advisory = _clean_id_sequence(
            self.advisory_feedback_ids,
            "advisory_feedback_ids",
        )

        if not set(selected).issubset(set(blocking)):
            raise ValueError(
                "selected_feedback_ids must be a subset of "
                "blocking_feedback_ids"
            )

        metadata = _copy_json_mapping(
            self.metadata,
            "FeedbackRouteDecision.metadata",
        )

        object.__setattr__(self, "decision_id", decision_id)
        object.__setattr__(self, "source_report_id", source_report_id)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "action", action)
        object.__setattr__(
            self,
            "blocking_feedback_ids",
            blocking,
        )
        object.__setattr__(
            self,
            "selected_feedback_ids",
            selected,
        )
        object.__setattr__(
            self,
            "advisory_feedback_ids",
            advisory,
        )
        object.__setattr__(self, "metadata", metadata)

    def to_dict(self) -> dict[str, Any]:
        """Return a finite JSON-serializable representation."""

        return {
            "schema_version": self.schema_version,
            "decision_id": self.decision_id,
            "action": self.action.value,
            "reason": self.reason,
            "source_report_id": self.source_report_id,
            "blocking_feedback_ids": list(
                self.blocking_feedback_ids
            ),
            "selected_feedback_ids": list(
                self.selected_feedback_ids
            ),
            "advisory_feedback_ids": list(
                self.advisory_feedback_ids
            ),
            "metadata": _copy_json_mapping(
                self.metadata,
                "FeedbackRouteDecision.metadata",
            ),
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "FeedbackRouteDecision":
        """Restore one route decision from its serialized form."""

        if not isinstance(payload, Mapping):
            raise TypeError(
                "FeedbackRouteDecision payload must be a mapping"
            )
        unknown = set(payload) - _ALLOWED_DECISION_FIELDS
        if unknown:
            raise ValueError(
                "Unknown FeedbackRouteDecision fields: "
                + ", ".join(sorted(unknown))
            )
        if payload.get("schema_version", 1) != 1:
            raise ValueError(
                "Unsupported FeedbackRouteDecision schema_version"
            )

        return cls(
            decision_id=payload["decision_id"],
            action=payload["action"],
            reason=payload["reason"],
            source_report_id=payload["source_report_id"],
            blocking_feedback_ids=tuple(
                payload.get("blocking_feedback_ids", ())
            ),
            selected_feedback_ids=tuple(
                payload.get("selected_feedback_ids", ())
            ),
            advisory_feedback_ids=tuple(
                payload.get("advisory_feedback_ids", ())
            ),
            metadata=payload.get("metadata", {}),
        )


class FeedbackRouter:
    """Choose a conservative next action from normalized feedback.

    Routing uses only normalized stage, category, severity, and owner.
    It never reads raw source evidence, diagnostic details, paths, or
    hidden test content. Unknown ownership is never assumed to be a
    candidate problem. Multiple distinct blocking repair directions
    produce ``REVIEW_MIXED`` instead of an arbitrary repair choice.
    """

    policy_version = 1

    def route(
        self,
        report: FeedbackReport,
        *,
        decision_id: str,
    ) -> FeedbackRouteDecision:
        """Return a deterministic, non-executing route decision."""

        if not isinstance(report, FeedbackReport):
            raise TypeError("report must be a FeedbackReport")

        normalized_decision_id = _clean_required(
            decision_id,
            "decision_id",
        )
        blocking = tuple(
            item for item in report.items if item.blocking
        )
        advisory = tuple(
            item for item in report.items if not item.blocking
        )

        common_metadata = {
            "policy_version": self.policy_version,
            "source": report.source,
            "evidence_view": report.metadata.get(
                "evidence_view"
            ),
            "item_count": len(report.items),
            "blocking_item_count": len(blocking),
            "advisory_item_count": len(advisory),
        }

        if not blocking:
            return FeedbackRouteDecision(
                decision_id=normalized_decision_id,
                action=(
                    FeedbackRouteAction.CONTINUE_VALIDATION
                ),
                reason=(
                    "No blocking feedback is present; validation "
                    "may continue."
                ),
                source_report_id=report.report_id,
                blocking_feedback_ids=(),
                selected_feedback_ids=(),
                advisory_feedback_ids=tuple(
                    item.feedback_id for item in advisory
                ),
                metadata={
                    **common_metadata,
                    "candidate_actions": [],
                },
            )

        grouped: dict[
            FeedbackRouteAction,
            list[FeedbackItem],
        ] = defaultdict(list)
        for item in blocking:
            grouped[self._action_for_item(item)].append(item)

        blocking_ids = tuple(
            item.feedback_id for item in blocking
        )
        advisory_ids = tuple(
            item.feedback_id for item in advisory
        )
        candidate_actions = sorted(
            action.value for action in grouped
        )

        budget_items = grouped.get(
            FeedbackRouteAction.STOP_BUDGET_EXHAUSTED,
            [],
        )
        if budget_items:
            selected_ids = tuple(
                item.feedback_id for item in budget_items
            )
            return FeedbackRouteDecision(
                decision_id=normalized_decision_id,
                action=(
                    FeedbackRouteAction.STOP_BUDGET_EXHAUSTED
                ),
                reason=(
                    "Execution budget is exhausted; no additional "
                    "tool or model work should be launched."
                ),
                source_report_id=report.report_id,
                blocking_feedback_ids=blocking_ids,
                selected_feedback_ids=selected_ids,
                advisory_feedback_ids=advisory_ids,
                metadata={
                    **common_metadata,
                    "candidate_actions": candidate_actions,
                    "deferred_blocking_feedback_ids": [
                        item.feedback_id
                        for item in blocking
                        if item.feedback_id
                        not in set(selected_ids)
                    ],
                },
            )

        if len(grouped) > 1:
            return FeedbackRouteDecision(
                decision_id=normalized_decision_id,
                action=FeedbackRouteAction.REVIEW_MIXED,
                reason=(
                    "Blocking feedback points to multiple distinct "
                    "repair directions; ownership must be resolved "
                    "before automated repair."
                ),
                source_report_id=report.report_id,
                blocking_feedback_ids=blocking_ids,
                selected_feedback_ids=blocking_ids,
                advisory_feedback_ids=advisory_ids,
                metadata={
                    **common_metadata,
                    "candidate_actions": candidate_actions,
                    "mixed_action_count": len(grouped),
                },
            )

        action = next(iter(grouped))
        selected_items = grouped[action]
        return FeedbackRouteDecision(
            decision_id=normalized_decision_id,
            action=action,
            reason=self._reason(action),
            source_report_id=report.report_id,
            blocking_feedback_ids=blocking_ids,
            selected_feedback_ids=tuple(
                item.feedback_id for item in selected_items
            ),
            advisory_feedback_ids=advisory_ids,
            metadata={
                **common_metadata,
                "candidate_actions": candidate_actions,
            },
        )

    @staticmethod
    def _action_for_item(
        item: FeedbackItem,
    ) -> FeedbackRouteAction:
        if (
            item.category
            is FeedbackCategory.BUDGET_EXHAUSTED
        ):
            return (
                FeedbackRouteAction.STOP_BUDGET_EXHAUSTED
            )

        if item.owner is FeedbackOwner.TOOLCHAIN:
            return FeedbackRouteAction.FIX_TOOLCHAIN
        if item.owner is FeedbackOwner.CONFIGURATION:
            return FeedbackRouteAction.FIX_CONFIGURATION
        if item.owner is FeedbackOwner.TASK_INPUT:
            return FeedbackRouteAction.FIX_TASK_INPUT
        if item.owner is FeedbackOwner.TESTBENCH:
            return FeedbackRouteAction.REPAIR_TESTBENCH
        if item.owner is FeedbackOwner.CANDIDATE:
            return FeedbackRouteAction.REPAIR_CANDIDATE
        if item.owner is FeedbackOwner.ORIGINAL:
            return FeedbackRouteAction.REPAIR_ORIGINAL

        if (
            item.category
            is FeedbackCategory.TOOLCHAIN_FAILURE
        ):
            return FeedbackRouteAction.FIX_TOOLCHAIN
        if (
            item.stage is FeedbackStage.TOOLCHAIN
            and item.category is FeedbackCategory.TIMEOUT
        ):
            return FeedbackRouteAction.FIX_TOOLCHAIN

        return FeedbackRouteAction.REVIEW_UNKNOWN

    @staticmethod
    def _reason(action: FeedbackRouteAction) -> str:
        return {
            FeedbackRouteAction.FIX_TOOLCHAIN: (
                "Blocking feedback is owned by the toolchain; "
                "repairing source code would not address it."
            ),
            FeedbackRouteAction.FIX_CONFIGURATION: (
                "Blocking feedback is owned by configuration and "
                "must be corrected before validation continues."
            ),
            FeedbackRouteAction.FIX_TASK_INPUT: (
                "Blocking feedback is owned by task input and must "
                "be corrected before execution continues."
            ),
            FeedbackRouteAction.REPAIR_TESTBENCH: (
                "Blocking feedback is owned by the testbench and "
                "may enter the bounded testbench repair path."
            ),
            FeedbackRouteAction.REPAIR_CANDIDATE: (
                "Blocking feedback is owned by the candidate and "
                "may enter a candidate repair path."
            ),
            FeedbackRouteAction.REPAIR_ORIGINAL: (
                "Blocking feedback is owned by the original source "
                "and requires source correction before optimization."
            ),
            FeedbackRouteAction.REVIEW_UNKNOWN: (
                "Blocking feedback lacks a reliable repair owner or "
                "specific enough cause; review or gather evidence "
                "before automated repair."
            ),
            FeedbackRouteAction.CONTINUE_VALIDATION: (
                "No blocking feedback is present."
            ),
            FeedbackRouteAction.STOP_BUDGET_EXHAUSTED: (
                "Execution budget is exhausted."
            ),
            FeedbackRouteAction.REVIEW_MIXED: (
                "Multiple repair directions are present."
            ),
        }[action]


def _clean_required(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(
            f"{field_name} must not be empty"
        )
    return cleaned


def _clean_id_sequence(
    value: Sequence[str],
    field_name: str,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise TypeError(
            f"{field_name} must be a sequence of strings"
        )

    result = tuple(
        _clean_required(item, field_name)
        for item in value
    )
    if len(result) != len(set(result)):
        raise ValueError(
            f"{field_name} must contain unique values"
        )
    return result


def _copy_json_mapping(
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
            f"{field_name} must be finite JSON data"
        ) from exc
    if not isinstance(copied, dict):
        raise TypeError(
            f"{field_name} must normalize to an object"
        )
    return copied
