"""Create an agent-safe view of testbench preflight feedback."""

from __future__ import annotations

from pathlib import PurePath
import re
from typing import Any

from agrefactor.evidence import FeedbackItem, FeedbackReport


_POSIX_PATH = re.compile(
    r"(?<![A-Za-z0-9_.-])/(?:[^/\s:;,()]+/)*[^/\s:;,()]+"
)
_WINDOWS_PATH = re.compile(
    r"(?<![A-Za-z0-9_.-])[A-Za-z]:\\(?:[^\\\s:;,()]+\\)*[^\\\s:;,()]+"
)
_SAFE_ITEM_KEYS = frozenset(
    {
        "diagnostic_kind",
        "file",
        "line",
        "column",
        "preflight_status",
        "next_action",
        "fallback_item",
    }
)
_SAFE_REPORT_KEYS = frozenset(
    {
        "preflight_status",
        "preflight_stage",
        "failure_kind",
        "failure_owner",
        "next_action",
        "duration_s",
    }
)


class TestbenchPreflightFeedbackViewAdapter:
    """Redact operator preflight feedback for agent consumption.

    Structured diagnostics retain normalized semantics and sanitized
    messages. Commands, full stdout/stderr, artifacts, evidence refs,
    absolute paths, and nested operator evidence are never copied.
    """

    source = "testbench_preflight"
    adapter_version = 1

    def to_agent_report(
        self,
        report: FeedbackReport,
        *,
        report_id: str,
    ) -> FeedbackReport:
        self._validate(report)
        report_id = self._required(report_id, "report_id")

        items = tuple(
            self._safe_item(
                item,
                feedback_id=f"{report_id}.item.{index}",
            )
            for index, item in enumerate(report.items, start=1)
        )

        counts = {
            "category_counts": self._counts(
                item.category.value for item in items
            ),
            "severity_counts": self._counts(
                item.severity.value for item in items
            ),
            "owner_counts": self._counts(
                item.owner.value for item in items
            ),
        }
        source_metadata = {
            key: report.metadata[key]
            for key in _SAFE_REPORT_KEYS
            if key in report.metadata
        }
        source_report_id = self._sanitize(report.report_id)

        return FeedbackReport(
            report_id=report_id,
            source=self.source,
            items=items,
            source_evidence={
                "source_report_id": source_report_id,
                "source": report.source,
                "item_count": len(items),
                "blocking": report.blocking,
                "highest_severity": (
                    report.highest_severity.value
                    if report.highest_severity is not None
                    else None
                ),
                "preflight": source_metadata,
                "redacted": True,
                **counts,
            },
            metadata={
                "adapter_version": self.adapter_version,
                "evidence_view": "agent_safe",
                "source_report_id": source_report_id,
                "source_redacted": True,
                "item_count": len(items),
                "blocking": report.blocking,
                "highest_severity": (
                    report.highest_severity.value
                    if report.highest_severity is not None
                    else None
                ),
                **source_metadata,
                **counts,
            },
        )

    def _safe_item(
        self,
        item: FeedbackItem,
        *,
        feedback_id: str,
    ) -> FeedbackItem:
        metadata: dict[str, Any] = {}
        for key in _SAFE_ITEM_KEYS:
            if key not in item.metadata:
                continue
            value = item.metadata[key]
            if key == "file":
                value = self._basename(value)
            elif isinstance(value, str):
                value = self._sanitize(value)
            metadata[key] = value

        fallback = item.metadata.get("fallback_item") is True
        if fallback:
            detail = (
                "Raw preflight stdout and stderr were redacted "
                "because no structured diagnostic was available."
            )
            metadata["detail_redacted"] = True
        else:
            detail = (
                self._sanitize(item.detail)
                if item.detail is not None
                else None
            )
            metadata["detail_redacted"] = False

        return FeedbackItem(
            feedback_id=feedback_id,
            stage=item.stage,
            category=item.category,
            severity=item.severity,
            owner=item.owner,
            summary=self._sanitize(item.summary),
            detail=detail,
            source=item.source,
            evidence_ref=None,
            metadata=metadata,
        )

    @staticmethod
    def _counts(values) -> dict[str, int]:
        result: dict[str, int] = {}
        for value in values:
            result[value] = result.get(value, 0) + 1
        return result

    @classmethod
    def _sanitize(cls, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("text value must be a string")
        value = _WINDOWS_PATH.sub("<PATH>", value)
        value = _POSIX_PATH.sub("<PATH>", value)
        return value.strip()

    @staticmethod
    def _basename(value: Any) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        return PurePath(value.strip().replace("\\", "/")).name

    @classmethod
    def _validate(cls, report: FeedbackReport) -> None:
        if not isinstance(report, FeedbackReport):
            raise TypeError("report must be a FeedbackReport")
        if report.source != cls.source:
            raise ValueError(
                "report.source must be 'testbench_preflight'"
            )
        if report.metadata.get("evidence_view") != "operator_full":
            raise ValueError(
                "report must use operator_full evidence"
            )

    @staticmethod
    def _required(value: str, name: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{name} must be a string")
        value = value.strip()
        if not value:
            raise ValueError(f"{name} must not be empty")
        return value
