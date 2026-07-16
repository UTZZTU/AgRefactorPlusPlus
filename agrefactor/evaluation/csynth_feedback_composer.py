"""Compose invocation- and diagnostic-level CSYNTH feedback."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
)


class CsynthFeedbackComposer:
    """Merge two independently produced CSYNTH feedback reports.

    The invocation report describes execution-level facts such as
    budget gating, toolchain verification, launch, timeout, and final
    return status. The diagnostic report describes parsed Vitis log
    messages.

    A generic invocation-level ``UNKNOWN`` failure is suppressed only
    when the diagnostic report contains at least one blocking item.
    Diagnostic warnings alone cannot explain a failed invocation, so
    they do not suppress the generic blocking failure.

    This class is pure composition. It does not run CSYNTH, read files,
    expose feedback to an agent, or choose a state transition.
    """

    source = "csynth"
    composer_version = 1

    _INVOCATION_SOURCE = "csynth_invocation"
    _DIAGNOSTIC_SOURCE = "csynth_diagnostic"

    def compose(
        self,
        *,
        invocation_report: FeedbackReport,
        diagnostic_report: FeedbackReport,
        report_id: str,
    ) -> FeedbackReport:
        """Return one operator-oriented CSYNTH feedback report."""

        self._validate_component(
            invocation_report,
            field_name="invocation_report",
            expected_source=self._INVOCATION_SOURCE,
        )
        self._validate_component(
            diagnostic_report,
            field_name="diagnostic_report",
            expected_source=self._DIAGNOSTIC_SOURCE,
        )
        normalized_report_id = self._required_text(
            report_id,
            "report_id",
        )

        diagnostic_has_blocking = diagnostic_report.blocking
        composed_items: list[FeedbackItem] = []
        suppressed_invocation_items: list[dict[str, Any]] = []

        for item in invocation_report.items:
            if (
                diagnostic_has_blocking
                and self._is_generic_invocation_failure(item)
            ):
                suppressed_invocation_items.append(item.to_dict())
                continue
            composed_items.append(
                self._copy_item(
                    item,
                    feedback_id=(
                        f"{normalized_report_id}.invocation."
                        f"{len(composed_items) + 1}"
                    ),
                    component="invocation",
                    component_report_id=invocation_report.report_id,
                )
            )

        diagnostic_offset = len(composed_items)
        for index, item in enumerate(
            diagnostic_report.items,
            start=1,
        ):
            composed_items.append(
                self._copy_item(
                    item,
                    feedback_id=(
                        f"{normalized_report_id}.diagnostic."
                        f"{index}"
                    ),
                    component="diagnostic",
                    component_report_id=diagnostic_report.report_id,
                )
            )

        invocation_item_count = sum(
            1
            for item in composed_items
            if item.metadata.get("component") == "invocation"
        )
        diagnostic_item_count = (
            len(composed_items) - invocation_item_count
        )

        return FeedbackReport(
            report_id=normalized_report_id,
            source=self.source,
            items=tuple(composed_items),
            source_evidence={
                "invocation_report": invocation_report.to_dict(),
                "diagnostic_report": diagnostic_report.to_dict(),
                "suppressed_invocation_items": (
                    suppressed_invocation_items
                ),
            },
            metadata={
                "composer_version": self.composer_version,
                "evidence_view": "operator_full",
                "invocation_report_id": (
                    invocation_report.report_id
                ),
                "diagnostic_report_id": (
                    diagnostic_report.report_id
                ),
                "invocation_item_count": invocation_item_count,
                "diagnostic_item_count": diagnostic_item_count,
                "suppressed_generic_invocation_count": len(
                    suppressed_invocation_items
                ),
                "diagnostic_has_blocking": (
                    diagnostic_has_blocking
                ),
                "component_item_count_before_composition": (
                    len(invocation_report.items)
                    + len(diagnostic_report.items)
                ),
                "composed_item_count": len(composed_items),
                "diagnostic_item_offset": diagnostic_offset,
            },
        )

    @staticmethod
    def _is_generic_invocation_failure(
        item: FeedbackItem,
    ) -> bool:
        return (
            item.source == "csynth_invocation"
            and item.stage is FeedbackStage.CSYNTH
            and item.category is FeedbackCategory.UNKNOWN
            and item.severity is FeedbackSeverity.ERROR
            and item.owner is FeedbackOwner.UNKNOWN
        )

    @staticmethod
    def _copy_item(
        item: FeedbackItem,
        *,
        feedback_id: str,
        component: str,
        component_report_id: str,
    ) -> FeedbackItem:
        metadata = dict(item.metadata)
        metadata.update(
            {
                "component": component,
                "component_report_id": component_report_id,
                "component_feedback_id": item.feedback_id,
            }
        )
        return FeedbackItem(
            feedback_id=feedback_id,
            stage=item.stage,
            category=item.category,
            severity=item.severity,
            owner=item.owner,
            summary=item.summary,
            detail=item.detail,
            source=item.source,
            evidence_ref=item.evidence_ref,
            metadata=metadata,
        )

    @staticmethod
    def _validate_component(
        value: FeedbackReport,
        *,
        field_name: str,
        expected_source: str,
    ) -> None:
        if not isinstance(value, FeedbackReport):
            raise TypeError(
                f"{field_name} must be a FeedbackReport"
            )
        if value.source != expected_source:
            raise ValueError(
                f"{field_name}.source must be "
                f"{expected_source!r}"
            )
        evidence_view = value.metadata.get("evidence_view")
        if evidence_view != "operator_full":
            raise ValueError(
                f"{field_name} must use operator_full evidence"
            )

    @staticmethod
    def _required_text(value: str, field_name: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{field_name} must be a string")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(
                f"{field_name} must not be empty"
            )
        return cleaned
