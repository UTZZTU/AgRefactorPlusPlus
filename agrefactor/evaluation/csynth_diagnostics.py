"""Deterministically parse high-confidence Vitis HLS diagnostics."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from pathlib import PurePath
import re
from typing import Any

from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
)


_MESSAGE_LINE_RE = re.compile(
    r"^\s*(?P<severity>"
    r"CRITICAL WARNING|WARNING|ERROR|FATAL"
    r")\s*:\s*"
    r"(?:\[(?P<family>[A-Za-z][A-Za-z0-9_-]*)\s+"
    r"(?P<code>\d+(?:-\d+)*)\]\s*)?"
    r"(?P<message>.*?)\s*$",
    flags=re.IGNORECASE,
)

_SOURCE_PREFIX_RE = re.compile(
    r"^\s*(?P<file>.+?):"
    r"(?P<line>\d+):(?P<column>\d+):\s*"
    r"(?P<severity>fatal error|error|warning)\s*:\s*"
    r"(?P<message>.*?)\s*$",
    flags=re.IGNORECASE,
)

_SOURCE_AFTER_SEVERITY_RE = re.compile(
    r"^\s*(?P<severity>fatal error|error|warning)\s*:\s*"
    r"(?P<file>.+?):"
    r"(?P<line>\d+):(?P<column>\d+):\s*"
    r"(?P<message>.*?)\s*$",
    flags=re.IGNORECASE,
)

_TRAILING_LOCATION_RE = re.compile(
    r"\s*\((?P<file>[^()]+?):"
    r"(?P<line>\d+):(?P<column>\d+)\)\s*$"
)

_WHITESPACE_RE = re.compile(r"\s+")

_UNDECLARED_IDENTIFIER_RE = re.compile(
    r"\buse of undeclared identifier\b",
    flags=re.IGNORECASE,
)
_EXPECTED_TOKEN_RE = re.compile(
    r"\bexpected\b\s+.+",
    flags=re.IGNORECASE,
)
_INVALID_GOTO_RE = re.compile(
    r"\bcannot jump from this goto statement to its label\b",
    flags=re.IGNORECASE,
)
_S_AXILITE_BUNDLE_RE = re.compile(
    r"\bVitis kernel mode requires that all s_axilite ports "
    r"must be bundled into one bundle\b",
    flags=re.IGNORECASE,
)
_CARRIED_DEPENDENCE_RE = re.compile(
    r"\bII Violation\b.*"
    r"\bUnable to enforce a carried dependence constraint\b",
    flags=re.IGNORECASE,
)
_LOOP_EXIT_SCHEDULING_RE = re.compile(
    r"\bUnable to schedule the loop exit test\b.*"
    r"\bin the first pipeline iteration\b",
    flags=re.IGNORECASE,
)
_AGGREGATE_SOURCE_SYNTHESIS_RE = re.compile(
    r"\bEncountered problem during source synthesis\b",
    flags=re.IGNORECASE,
)


class CsynthDiagnosticParser:
    """Parse reviewed Vitis diagnostics into operator feedback.

    The parser recognizes only exact, stable message forms observed in
    the local Vitis HLS 2023.2 corpus. Every explicit error that passes
    the structural gate is retained. An unrecognized error is emitted
    as blocking ``UNKNOWN`` feedback rather than discarded or guessed.

    Ordinary warnings are retained in source evidence but not emitted.
    Only reviewed high-value warnings are converted to feedback items.
    This parser does not execute csynth and is not connected to prompts
    or state transitions by this commit.
    """

    source = "csynth_diagnostic"
    parser_version = 1

    def parse_text(
        self,
        text: str,
        *,
        report_id: str,
        evidence_ref: str | None = None,
        owner: FeedbackOwner | str = FeedbackOwner.UNKNOWN,
    ) -> FeedbackReport:
        """Parse one log or diagnostic text into an operator report."""

        if not isinstance(text, str):
            raise TypeError("text must be a string")

        normalized_report_id = self._required_text(
            report_id,
            "report_id",
        )
        normalized_ref = self._optional_text(
            evidence_ref,
            "evidence_ref",
        )
        normalized_owner = self._owner(owner)

        parsed = []
        ignored_warning_count = 0
        rejected_severity_line_count = 0

        for line_number, raw_line in enumerate(
            text.splitlines(),
            start=1,
        ):
            record = self._parse_line(
                raw_line,
                input_line=line_number,
            )
            if record is None:
                if self._looks_like_severity_line(raw_line):
                    rejected_severity_line_count += 1
                continue

            category, summary, rule, confidence = self._classify(
                record
            )
            record.update(
                {
                    "category": category.value,
                    "summary": summary,
                    "parser_rule": rule,
                    "classification_confidence": confidence,
                }
            )

            if (
                record["raw_severity"] == "WARNING"
                and rule not in {
                    "pipeline_carried_dependence",
                    "loop_exit_scheduling",
                }
            ):
                record["disposition"] = "ignored_low_value_warning"
                ignored_warning_count += 1
            else:
                record["disposition"] = "candidate"

            parsed.append(record)

        grouped, duplicates = self._deduplicate(parsed)

        specific_blocking_exists = any(
            record["disposition"] == "candidate"
            and record["raw_severity"] in {"ERROR", "FATAL"}
            and record["parser_rule"]
            != "aggregate_source_synthesis"
            for record in grouped
        )

        items = []
        suppressed_aggregate_count = 0
        emitted_records = []

        for record in grouped:
            if record["disposition"] != "candidate":
                emitted_records.append(record)
                continue

            if (
                record["parser_rule"]
                == "aggregate_source_synthesis"
                and specific_blocking_exists
            ):
                record["disposition"] = (
                    "suppressed_aggregate_error"
                )
                suppressed_aggregate_count += 1
                emitted_records.append(record)
                continue

            record["disposition"] = "emitted"
            emitted_records.append(record)
            items.append(
                self._to_item(
                    record,
                    report_id=normalized_report_id,
                    item_index=len(items) + 1,
                    evidence_ref=normalized_ref,
                    owner=normalized_owner,
                )
            )

        return FeedbackReport(
            report_id=normalized_report_id,
            source=self.source,
            items=tuple(items),
            source_evidence={
                "diagnostics": emitted_records,
                "duplicates": duplicates,
            },
            metadata={
                "parser_version": self.parser_version,
                "evidence_view": "operator_full",
                "evidence_ref": normalized_ref,
                "owner_context": normalized_owner.value,
                "input_line_count": len(text.splitlines()),
                "parsed_diagnostic_count": len(parsed),
                "deduplicated_diagnostic_count": len(grouped),
                "emitted_item_count": len(items),
                "ignored_warning_count": ignored_warning_count,
                "suppressed_aggregate_count": (
                    suppressed_aggregate_count
                ),
                "rejected_severity_line_count": (
                    rejected_severity_line_count
                ),
            },
        )

    def _parse_line(
        self,
        raw_line: str,
        *,
        input_line: int,
    ) -> dict[str, Any] | None:
        source_match = _SOURCE_PREFIX_RE.match(raw_line)
        if source_match is None:
            source_match = _SOURCE_AFTER_SEVERITY_RE.match(
                raw_line
            )

        if source_match is not None:
            severity = self._normalize_severity(
                source_match.group("severity")
            )
            return {
                "raw_line": raw_line.strip(),
                "input_line": input_line,
                "raw_severity": severity,
                "message_family": None,
                "message_code": None,
                "message_id": None,
                "message": source_match.group(
                    "message"
                ).strip(),
                "file": source_match.group("file").strip(),
                "line": int(source_match.group("line")),
                "column": int(source_match.group("column")),
            }

        message_match = _MESSAGE_LINE_RE.match(raw_line)
        if message_match is None:
            return None

        severity = self._normalize_severity(
            message_match.group("severity")
        )
        family = message_match.group("family")
        code = message_match.group("code")
        message = message_match.group("message").strip()

        file_name = None
        source_line = None
        source_column = None
        location_match = _TRAILING_LOCATION_RE.search(message)
        if location_match is not None:
            file_name = location_match.group("file").strip()
            source_line = int(location_match.group("line"))
            source_column = int(
                location_match.group("column")
            )
            message = message[: location_match.start()].strip()

        # A bare severity label without either a Vitis message ID or a
        # source location is too weak. This excludes unittest headings
        # such as ``ERROR: test_name`` from a synthesis log corpus.
        if family is None and file_name is None:
            return None

        normalized_family = (
            family.upper() if family is not None else None
        )
        message_id = (
            f"{normalized_family} {code}"
            if normalized_family is not None
            and code is not None
            else None
        )

        return {
            "raw_line": raw_line.strip(),
            "input_line": input_line,
            "raw_severity": severity,
            "message_family": normalized_family,
            "message_code": code,
            "message_id": message_id,
            "message": message,
            "file": file_name,
            "line": source_line,
            "column": source_column,
        }

    @staticmethod
    def _classify(
        record: Mapping[str, Any],
    ) -> tuple[
        FeedbackCategory,
        str,
        str,
        str,
    ]:
        message = str(record["message"])
        message_id = record.get("message_id")

        if _UNDECLARED_IDENTIFIER_RE.search(message):
            return (
                FeedbackCategory.UNDECLARED_SYMBOL,
                "HLS source uses an undeclared identifier",
                "undeclared_identifier",
                "high",
            )

        if _INVALID_GOTO_RE.search(message):
            return (
                FeedbackCategory.SYNTAX_ERROR,
                "HLS source contains an invalid goto control flow",
                "invalid_goto_control_flow",
                "high",
            )

        if _EXPECTED_TOKEN_RE.search(message):
            return (
                FeedbackCategory.SYNTAX_ERROR,
                "HLS source contains a syntax error",
                "expected_token",
                "high",
            )

        if _S_AXILITE_BUNDLE_RE.search(message):
            return (
                FeedbackCategory.INVALID_CONFIGURATION,
                "AXI-Lite control ports use inconsistent bundles",
                "s_axilite_bundle_mismatch",
                "high",
            )

        if (
            message_id == "HLS 200-880"
            and _CARRIED_DEPENDENCE_RE.search(message)
        ):
            return (
                FeedbackCategory.PIPELINE_DEPENDENCY,
                (
                    "Pipeline initiation interval is limited by "
                    "a carried dependence"
                ),
                "pipeline_carried_dependence",
                "high",
            )

        if (
            message_id == "HLS 200-878"
            and _LOOP_EXIT_SCHEDULING_RE.search(message)
        ):
            return (
                FeedbackCategory.UNKNOWN,
                (
                    "CSYNTH could not schedule a loop exit test "
                    "in the first pipeline iteration"
                ),
                "loop_exit_scheduling",
                "partial",
            )

        if _AGGREGATE_SOURCE_SYNTHESIS_RE.search(message):
            return (
                FeedbackCategory.UNKNOWN,
                "Vitis reported a source synthesis failure",
                "aggregate_source_synthesis",
                "aggregate",
            )

        return (
            FeedbackCategory.UNKNOWN,
            "Unclassified CSYNTH diagnostic",
            "unknown_fallback",
            "unknown",
        )

    def _to_item(
        self,
        record: Mapping[str, Any],
        *,
        report_id: str,
        item_index: int,
        evidence_ref: str | None,
        owner: FeedbackOwner,
    ) -> FeedbackItem:
        category = FeedbackCategory(record["category"])
        rule = str(record["parser_rule"])
        effective_owner = (
            owner
            if rule
            in {
                "undeclared_identifier",
                "invalid_goto_control_flow",
                "expected_token",
                "s_axilite_bundle_mismatch",
                "pipeline_carried_dependence",
            }
            else FeedbackOwner.UNKNOWN
        )

        return FeedbackItem(
            feedback_id=(
                f"{report_id}.diagnostic.{item_index}"
            ),
            stage=FeedbackStage.CSYNTH,
            category=category,
            severity=self._feedback_severity(
                str(record["raw_severity"])
            ),
            owner=effective_owner,
            summary=str(record["summary"]),
            detail=str(record["raw_line"]),
            source=self.source,
            evidence_ref=evidence_ref,
            metadata={
                "raw_severity": record["raw_severity"],
                "message_family": record["message_family"],
                "message_code": record["message_code"],
                "message_id": record["message_id"],
                "file": record["file"],
                "line": record["line"],
                "column": record["column"],
                "input_line": record["input_line"],
                "parser_rule": rule,
                "classification_confidence": record[
                    "classification_confidence"
                ],
                "occurrence_count": record[
                    "occurrence_count"
                ],
            },
        )

    @staticmethod
    def _deduplicate(
        records: list[dict[str, Any]],
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
    ]:
        grouped: dict[
            tuple[Any, ...],
            list[dict[str, Any]],
        ] = defaultdict(list)

        for record in records:
            grouped[
                CsynthDiagnosticParser._semantic_key(record)
            ].append(record)

        representatives = []
        duplicates = []

        for values in grouped.values():
            preferred = sorted(
                values,
                key=lambda item: (
                    item["message_id"] is None,
                    item["input_line"],
                ),
            )[0]
            representative = dict(preferred)
            representative["occurrence_count"] = len(values)
            representatives.append(representative)

            for duplicate in values:
                if duplicate is preferred:
                    continue
                duplicates.append(
                    {
                        "representative_input_line": (
                            preferred["input_line"]
                        ),
                        "duplicate_input_line": (
                            duplicate["input_line"]
                        ),
                        "raw_line": duplicate["raw_line"],
                    }
                )

        representatives.sort(
            key=lambda item: item["input_line"]
        )
        duplicates.sort(
            key=lambda item: item["duplicate_input_line"]
        )
        return representatives, duplicates

    @staticmethod
    def _semantic_key(
        record: Mapping[str, Any],
    ) -> tuple[Any, ...]:
        file_name = record.get("file")
        basename = (
            PurePath(str(file_name)).name
            if file_name
            else None
        )
        message = _WHITESPACE_RE.sub(
            " ",
            str(record["message"]).strip().lower(),
        )
        return (
            record["raw_severity"],
            message,
            basename,
            record.get("line"),
            record.get("column"),
        )

    @staticmethod
    def _feedback_severity(
        raw_severity: str,
    ) -> FeedbackSeverity:
        if raw_severity == "FATAL":
            return FeedbackSeverity.FATAL
        if raw_severity == "ERROR":
            return FeedbackSeverity.ERROR
        return FeedbackSeverity.WARNING

    @staticmethod
    def _normalize_severity(value: str) -> str:
        normalized = value.strip().upper()
        if normalized == "FATAL ERROR":
            return "FATAL"
        return normalized

    @staticmethod
    def _looks_like_severity_line(value: str) -> bool:
        lowered = value.lstrip().lower()
        return lowered.startswith(
            (
                "error:",
                "warning:",
                "critical warning:",
                "fatal:",
                "fatal error:",
            )
        )

    @staticmethod
    def _owner(
        value: FeedbackOwner | str,
    ) -> FeedbackOwner:
        if isinstance(value, FeedbackOwner):
            return value
        try:
            return FeedbackOwner(str(value))
        except ValueError as exc:
            raise ValueError(
                f"Unsupported feedback owner {value!r}"
            ) from exc

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

    @staticmethod
    def _optional_text(
        value: str | None,
        field_name: str,
    ) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError(
                f"{field_name} must be a string or null"
            )
        cleaned = value.strip()
        return cleaned or None
