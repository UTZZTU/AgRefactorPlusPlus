"""Create an agent-safe view of unified CSYNTH feedback."""

from __future__ import annotations

from pathlib import PurePath
import re
from typing import Any

from agrefactor.evidence import (
    FeedbackItem,
    FeedbackReport,
)


_ABSOLUTE_POSIX_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"/(?:[^/\s:;,()]+/)*"
    r"[^/\s:;,()]+"
)

_WINDOWS_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"[A-Za-z]:\\(?:[^\\\s:;,()]+\\)*"
    r"[^\\\s:;,()]+"
)

_SAFE_ITEM_METADATA_KEYS = frozenset(
    {
        "raw_severity",
        "message_family",
        "message_code",
        "message_id",
        "file",
        "line",
        "column",
        "input_line",
        "parser_rule",
        "classification_confidence",
        "occurrence_count",
        "legacy_status",
        "execution_status",
        "execution_returncode",
        "execution_timeout",
        "toolchain_verification_status",
        "toolchain_requested_version",
        "toolchain_actual_version",
        "budget_status",
        "budget_checkpoint",
        "budget_resource",
        "component",
    }
)


class CsynthFeedbackViewAdapter:
    """Redact an operator CSYNTH report for model consumption.

    The safe view preserves actionable classifications and sanitized
    diagnostic text. It removes artifact references, absolute paths,
    full invocation payloads, nested operator reports, command details,
    environment-specific locations, and raw artifact-loading evidence.

    This class does not build prompts or call a model. It only produces
    a structurally valid ``FeedbackReport`` whose
    ``metadata["evidence_view"]`` is ``"agent_safe"``.
    """

    adapter_version = 1
    source = "csynth"

    def to_agent_report(
        self,
        report: FeedbackReport,
        *,
        report_id: str,
    ) -> FeedbackReport:
        """Return a redacted report safe to expose to an agent."""

        self._validate_source_report(report)
        normalized_report_id = self._required_text(
            report_id,
            "report_id",
        )

        safe_items = tuple(
            self._safe_item(
                item,
                feedback_id=(
                    f"{normalized_report_id}.item.{index}"
                ),
            )
            for index, item in enumerate(
                report.items,
                start=1,
            )
        )

        category_counts: dict[str, int] = {}
        severity_counts: dict[str, int] = {}
        owner_counts: dict[str, int] = {}

        for item in safe_items:
            category_counts[item.category.value] = (
                category_counts.get(item.category.value, 0) + 1
            )
            severity_counts[item.severity.value] = (
                severity_counts.get(item.severity.value, 0) + 1
            )
            owner_counts[item.owner.value] = (
                owner_counts.get(item.owner.value, 0) + 1
            )

        return FeedbackReport(
            report_id=normalized_report_id,
            source=self.source,
            items=safe_items,
            source_evidence={
                "source_report_id": report.report_id,
                "source": report.source,
                "item_count": len(safe_items),
                "blocking": report.blocking,
                "highest_severity": (
                    report.highest_severity.value
                    if report.highest_severity is not None
                    else None
                ),
                "category_counts": category_counts,
                "severity_counts": severity_counts,
                "owner_counts": owner_counts,
                "redacted": True,
            },
            metadata={
                "adapter_version": self.adapter_version,
                "evidence_view": "agent_safe",
                "source_report_id": report.report_id,
                "source_redacted": True,
                "item_count": len(safe_items),
                "blocking": report.blocking,
                "highest_severity": (
                    report.highest_severity.value
                    if report.highest_severity is not None
                    else None
                ),
                "category_counts": category_counts,
                "severity_counts": severity_counts,
                "owner_counts": owner_counts,
            },
        )

    def _safe_item(
        self,
        item: FeedbackItem,
        *,
        feedback_id: str,
    ) -> FeedbackItem:
        safe_metadata: dict[str, Any] = {}

        for key in _SAFE_ITEM_METADATA_KEYS:
            if key not in item.metadata:
                continue

            value = item.metadata[key]
            if key == "file":
                value = self._basename_or_none(value)
            elif isinstance(value, str):
                value = self._sanitize_text(value)

            safe_metadata[key] = value

        detail = (
            self._sanitize_text(item.detail)
            if item.detail is not None
            else None
        )

        return FeedbackItem(
            feedback_id=feedback_id,
            stage=item.stage,
            category=item.category,
            severity=item.severity,
            owner=item.owner,
            summary=self._sanitize_text(item.summary),
            detail=detail,
            source=item.source,
            evidence_ref=None,
            metadata=safe_metadata,
        )

    @classmethod
    def _sanitize_text(cls, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("text value must be a string")

        sanitized = _WINDOWS_PATH_RE.sub(
            "<PATH>",
            value,
        )
        sanitized = _ABSOLUTE_POSIX_PATH_RE.sub(
            "<PATH>",
            sanitized,
        )
        return sanitized.strip()

    @staticmethod
    def _basename_or_none(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            return None

        cleaned = value.strip()
        if not cleaned:
            return None

        normalized = cleaned.replace("\\", "/")
        return PurePath(normalized).name

    @classmethod
    def _validate_source_report(
        cls,
        report: FeedbackReport,
    ) -> None:
        if not isinstance(report, FeedbackReport):
            raise TypeError(
                "report must be a FeedbackReport"
            )
        if report.source != cls.source:
            raise ValueError(
                "report.source must be 'csynth'"
            )
        if report.metadata.get("evidence_view") != "operator_full":
            raise ValueError(
                "report must use operator_full evidence"
            )

    @staticmethod
    def _required_text(value: str, field_name: str) -> str:
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
