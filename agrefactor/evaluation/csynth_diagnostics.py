"""Deterministically parse high-confidence Vitis HLS diagnostics."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
import hashlib
import json
import re
from typing import Any

from agrefactor.evaluation.diagnostic_catalog import DiagnosticCatalog, load_catalog
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
    r"CRITICAL WARNING|WARNING|ERROR|FATAL ERROR|FATAL"
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


def _source_position(value: str) -> int | None:
    """Normalize Vitis' zero sentinel for an unknown source position."""
    position = int(value)
    return position if position > 0 else None

_GLOBAL_VARIABLE_DEFINITION_RE = re.compile(
    r"\bGlobal variable\s+['\"](?P<symbol>[A-Za-z_][A-Za-z0-9_]*)['\"]"
    r"\s+must have definition\b",
    flags=re.IGNORECASE,
)
class CsynthDiagnosticParser:
    """Parse reviewed Vitis diagnostics into operator feedback.

    The catalog recognizes reviewed message forms. Every explicit
    error is retained. An unrecognized error is emitted
    as blocking ``UNKNOWN`` feedback rather than discarded or guessed.

    Ordinary warnings are retained in source evidence but not emitted.
    Only reviewed high-value warnings are converted to feedback items.
    This parser classifies diagnostics without executing CSYNTH or
    choosing a repair route.
    """

    source = "csynth_diagnostic"
    parser_version = 3

    def __init__(self, catalog: DiagnosticCatalog | None = None) -> None:
        self._catalog = catalog or load_catalog()

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
        rejected_severity_lines = []

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
                    if len(rejected_severity_lines) < 100:
                        rejected_severity_lines.append({
                            "input_line": line_number,
                            "raw_line": raw_line.strip()[:4000],
                        })
                continue

            category, summary, rule, confidence = self._classify(
                record
            )
            catalog_rule = self._catalog.match(record)
            record.update(
                {
                    "category": category.value,
                    "summary": summary,
                    "parser_rule": rule,
                    "classification_confidence": confidence,
                    "diagnostic_id": (
                        catalog_rule.diagnostic_id
                        if catalog_rule is not None
                        else "unknown_fallback"
                    ),
                    "category_id": (
                        catalog_rule.category_id
                        if catalog_rule is not None
                        else "unknown"
                    ),
                    "catalog_stage": (
                        catalog_rule.stage.value
                        if catalog_rule is not None
                        else FeedbackStage.CSYNTH.value
                    ),
                    "evidence_requirements": (
                        list(catalog_rule.evidence_requirements)
                        if catalog_rule is not None
                        else ["raw_diagnostic"]
                    ),
                    "allowed_actions": (
                        list(catalog_rule.allowed_actions)
                        if catalog_rule is not None
                        else ["review_unknown"]
                    ),
                    "owner_policy": (
                        catalog_rule.owner_policy
                        if catalog_rule is not None
                        else "resolve_from_evidence"
                    ),
                }
            )
            record["evidence_fingerprint"] = hashlib.sha256(json.dumps({
                "stage": record["catalog_stage"],
                "code": record["message_id"],
                "message": record["message"],
                "file": record["file"],
                "line": record["line"],
                "column": record["column"],
            }, sort_keys=True).encode("utf-8")).hexdigest()

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
                "rejected_severity_lines": rejected_severity_lines,
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
                "diagnostic_parse_complete": rejected_severity_line_count == 0,
                "evidence_complete": False,
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
                "line": _source_position(source_match.group("line")),
                "column": _source_position(source_match.group("column")),
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
            source_line = _source_position(
                location_match.group("line")
            )
            source_column = _source_position(
                location_match.group("column")
            )
            message = message[: location_match.start()].strip()

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

    def _classify(
        self,
        record: Mapping[str, Any],
    ) -> tuple[
        FeedbackCategory,
        str,
        str,
        str,
    ]:
        match = self._catalog.classify(record)
        if match is not None:
            category, summary, rule, confidence = match
            return (
                FeedbackCategory(category),
                summary,
                rule,
                confidence,
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
        # The catalog classifies diagnostics only.  Ownership comes from
        # the caller's execution evidence and is never inferred from an
        # HLS number or unsupported-construct message.
        effective_owner = (
            owner
            if category is not FeedbackCategory.UNKNOWN
            else FeedbackOwner.UNKNOWN
        )


        affected_symbol = None
        if rule == "global_variable_requires_definition":
            match = _GLOBAL_VARIABLE_DEFINITION_RE.search(
                str(record["message"])
            )
            if match is not None:
                affected_symbol = match.group("symbol")

        try:
            item_stage = FeedbackStage(
                str(record.get("catalog_stage", FeedbackStage.CSYNTH.value))
            )
        except ValueError:
            item_stage = FeedbackStage.CSYNTH

        return FeedbackItem(
            feedback_id=(
                f"{report_id}.diagnostic.{item_index}"
            ),
            stage=item_stage,
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
                "diagnostic_id": record.get(
                    "diagnostic_id", "unknown_fallback"
                ),
                "category_id": record.get("category_id", "unknown"),
                "evidence_fingerprint": record["evidence_fingerprint"],
                "evidence_requirements": record.get(
                    "evidence_requirements", ["raw_diagnostic"]
                ),
                "allowed_actions": record.get(
                    "allowed_actions", ["review_unknown"]
                ),
                "owner_policy": record.get(
                    "owner_policy", "resolve_from_evidence"
                ),
                "classification_confidence": record[
                    "classification_confidence"
                ],
                "affected_symbol": affected_symbol,
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
        source_path = (
            str(file_name).replace("\\", "/")
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
            source_path,
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
