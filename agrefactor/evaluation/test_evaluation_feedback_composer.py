"""Compose multiple suite-level test feedback reports by split."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agrefactor.config import EvaluationSplit
from agrefactor.evidence import (
    FeedbackItem,
    FeedbackReport,
)


class TestEvaluationFeedbackComposer:
    """Merge ordered suite reports without changing evaluation semantics.

    Public composition accepts only ``agent_safe`` component reports.
    Hidden composition accepts only ``operator_full`` component reports.
    A composed hidden report is still operator-only and may preserve full
    component evidence; the validation coordinator remains responsible for
    suppressing hidden identities and items from ordinary orchestration
    results and traces.

    This class does not execute suites, consume budgets, choose fail-fast
    policy, route feedback, transition validation state, or call a model.
    """

    source = "test_evaluation"
    composer_version = 1

    def compose(
        self,
        *,
        reports: Sequence[FeedbackReport],
        report_id: str,
        split: EvaluationSplit | str,
    ) -> FeedbackReport:
        """Return one ordered report for a public or hidden split."""

        normalized_report_id = self._required_text(
            report_id,
            "report_id",
        )
        normalized_split = self._split(split)
        expected_view = (
            "agent_safe"
            if normalized_split is EvaluationSplit.PUBLIC
            else "operator_full"
        )
        expected_visibility = (
            normalized_split.feedback_visible_to_agent
        )
        components = self._reports(reports)

        suite_ids: list[str] = []
        component_report_ids: list[str] = []
        blocking_suite_ids: list[str] = []
        composed_items: list[FeedbackItem] = []
        component_payloads: list[dict[str, Any]] = []

        category_counts: dict[str, int] = {}
        severity_counts: dict[str, int] = {}
        owner_counts: dict[str, int] = {}

        for component_index, report in enumerate(
            components,
            start=1,
        ):
            suite_id = self._validate_component(
                report,
                component_index=component_index,
                expected_split=normalized_split,
                expected_view=expected_view,
                expected_visibility=expected_visibility,
            )
            if suite_id in suite_ids:
                raise ValueError(
                    f"duplicate suite_id in composition: {suite_id}"
                )
            if report.report_id in component_report_ids:
                raise ValueError(
                    "component report_id values must be unique"
                )

            suite_ids.append(suite_id)
            component_report_ids.append(report.report_id)
            component_payloads.append(report.to_dict())
            if report.blocking:
                blocking_suite_ids.append(suite_id)

            for item_index, item in enumerate(
                report.items,
                start=1,
            ):
                copied = self._copy_item(
                    item,
                    feedback_id=(
                        f"{normalized_report_id}.suite."
                        f"{component_index}.item.{item_index}"
                    ),
                    suite_id=suite_id,
                    split=normalized_split,
                    component_index=component_index,
                    component_report_id=report.report_id,
                )
                composed_items.append(copied)
                category_counts[copied.category.value] = (
                    category_counts.get(
                        copied.category.value,
                        0,
                    )
                    + 1
                )
                severity_counts[copied.severity.value] = (
                    severity_counts.get(
                        copied.severity.value,
                        0,
                    )
                    + 1
                )
                owner_counts[copied.owner.value] = (
                    owner_counts.get(
                        copied.owner.value,
                        0,
                    )
                    + 1
                )

        return FeedbackReport(
            report_id=normalized_report_id,
            source=self.source,
            items=tuple(composed_items),
            source_evidence={
                "evaluation_split": normalized_split.value,
                "evidence_view": expected_view,
                "component_reports": component_payloads,
            },
            metadata={
                "composer_version": self.composer_version,
                "evidence_view": expected_view,
                "evaluation_split": normalized_split.value,
                "feedback_visible_to_agent": expected_visibility,
                "suite_count": len(suite_ids),
                "suite_ids": suite_ids,
                "component_report_ids": component_report_ids,
                "blocking_suite_ids": blocking_suite_ids,
                "blocking_suite_count": len(
                    blocking_suite_ids
                ),
                "component_item_count": sum(
                    len(report.items)
                    for report in components
                ),
                "composed_item_count": len(
                    composed_items
                ),
                "category_counts": category_counts,
                "severity_counts": severity_counts,
                "owner_counts": owner_counts,
                "component_order_preserved": True,
            },
        )

    @classmethod
    def _validate_component(
        cls,
        report: FeedbackReport,
        *,
        component_index: int,
        expected_split: EvaluationSplit,
        expected_view: str,
        expected_visibility: bool,
    ) -> str:
        if not isinstance(report, FeedbackReport):
            raise TypeError(
                "reports must contain FeedbackReport values"
            )
        if report.source != cls.source:
            raise ValueError(
                f"reports[{component_index - 1}].source "
                f"must be {cls.source!r}"
            )

        view = report.metadata.get("evidence_view")
        if view != expected_view:
            raise ValueError(
                f"{expected_split.value} composition requires "
                f"{expected_view} component reports"
            )

        split = report.metadata.get("evaluation_split")
        if split != expected_split.value:
            raise ValueError(
                "component evaluation_split does not match "
                f"{expected_split.value}"
            )

        visible = report.metadata.get(
            "feedback_visible_to_agent"
        )
        if visible is not expected_visibility:
            raise ValueError(
                "component feedback visibility conflicts with split"
            )

        return cls._required_text(
            report.metadata.get("suite_id"),
            f"reports[{component_index - 1}].metadata.suite_id",
        )

    @staticmethod
    def _copy_item(
        item: FeedbackItem,
        *,
        feedback_id: str,
        suite_id: str,
        split: EvaluationSplit,
        component_index: int,
        component_report_id: str,
    ) -> FeedbackItem:
        metadata = dict(item.metadata)
        metadata.update(
            {
                "suite_id": suite_id,
                "evaluation_split": split.value,
                "component_index": component_index,
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
    def _reports(
        value: Sequence[FeedbackReport],
    ) -> tuple[FeedbackReport, ...]:
        if isinstance(value, (str, bytes, Mapping)):
            raise TypeError(
                "reports must be a sequence of FeedbackReport values"
            )
        if not isinstance(value, Sequence):
            raise TypeError(
                "reports must be a sequence of FeedbackReport values"
            )
        reports = tuple(value)
        if not reports:
            raise ValueError(
                "reports must contain at least one component"
            )
        for report in reports:
            if not isinstance(report, FeedbackReport):
                raise TypeError(
                    "reports must contain FeedbackReport values"
                )
        return reports

    @staticmethod
    def _split(
        value: EvaluationSplit | str,
    ) -> EvaluationSplit:
        if isinstance(value, EvaluationSplit):
            return value
        try:
            return EvaluationSplit(str(value))
        except ValueError as exc:
            raise ValueError(
                f"unsupported evaluation split: {value!r}"
            ) from exc

    @staticmethod
    def _required_text(
        value: Any,
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
