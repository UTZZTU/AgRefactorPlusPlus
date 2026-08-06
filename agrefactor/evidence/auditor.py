"""Independent, unknown-safe audit of product validation evidence.

This module deliberately does not import ``agrefactor.product.run_output``.
It reconstructs safety invariants from persisted typed evidence so the producer
and auditor do not share the same reducer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
import json
from typing import Any


class AuditSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {
            AuditSeverity.INFO: 0,
            AuditSeverity.WARNING: 1,
            AuditSeverity.ERROR: 2,
            AuditSeverity.CRITICAL: 3,
        }[self]


@dataclass(frozen=True, slots=True)
class EvidenceAuditFinding:
    code: str
    severity: AuditSeverity
    message: str
    expected: Any = None
    observed: Any = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        code = _required_code(self.code, "finding.code")
        message = _required_text(self.message, "finding.message")
        severity = (
            self.severity
            if isinstance(self.severity, AuditSeverity)
            else AuditSeverity(str(self.severity))
        )
        refs = tuple(
            item.strip()
            for item in self.evidence_refs
            if isinstance(item, str) and item.strip()
        )
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "severity", severity)
        object.__setattr__(self, "evidence_refs", refs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "expected": _json_copy(self.expected),
            "observed": _json_copy(self.observed),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class EvidenceAuditReport:
    status: str
    findings: tuple[EvidenceAuditFinding, ...]
    summary_status: str | None
    terminal_stage: str | None
    terminal_evidence: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = 1
    auditor_version: int = 1

    @property
    def has_errors(self) -> bool:
        return any(
            item.severity in {AuditSeverity.ERROR, AuditSeverity.CRITICAL}
            for item in self.findings
        )

    @property
    def has_critical(self) -> bool:
        return any(
            item.severity is AuditSeverity.CRITICAL
            for item in self.findings
        )

    def to_dict(self) -> dict[str, Any]:
        counts = {
            level.value: sum(
                1 for item in self.findings if item.severity is level
            )
            for level in AuditSeverity
        }
        return {
            "schema_version": self.schema_version,
            "auditor_version": self.auditor_version,
            "status": self.status,
            "summary_status": self.summary_status,
            "terminal_stage": self.terminal_stage,
            "terminal_evidence": _json_copy(dict(self.terminal_evidence)),
            "finding_counts": counts,
            "has_errors": self.has_errors,
            "has_critical": self.has_critical,
            "findings": [item.to_dict() for item in self.findings],
        }


_SUCCESS_CODES = frozenset({
    "accepted", "passed", "public_passed", "csynth_passed",
    "cosim_passed", "public_cosim_passed", "hidden_passed",
    "validation_passed", "none", "ok",
})
_FAILURE_STATUSES = frozenset({
    "failed", "error", "blocked", "rejected", "timeout",
})
_TERMINAL_STAGES = frozenset({
    "public", "public_csim", "csim", "csynth",
    "public_cosim", "cosim", "hidden",
})
_STAGE_EQUIVALENTS = {
    "public": frozenset({"public", "public_csim", "csim"}),
    "public_csim": frozenset({"public", "public_csim", "csim"}),
    "csim": frozenset({"public", "public_csim", "csim"}),
    "public_cosim": frozenset({"public_cosim", "cosim"}),
    "cosim": frozenset({"public_cosim", "cosim"}),
    "csynth": frozenset({"csynth"}),
    "hidden": frozenset({"hidden"}),
}


def audit_product_evidence(
    summary: Mapping[str, Any],
    identity: Mapping[str, Any],
    *,
    full_result: Mapping[str, Any] | None = None,
    process_record: Mapping[str, Any] | None = None,
) -> EvidenceAuditReport:
    """Audit persisted evidence without reusing the product summary reducer."""

    summary_map = _mapping(summary, "summary")
    identity_map = _mapping(identity, "identity")
    full_map = None if full_result is None else _mapping(full_result, "full_result")
    process_map = (
        None if process_record is None
        else _mapping(process_record, "process_record")
    )

    findings: list[EvidenceAuditFinding] = []
    summary_status = _code(summary_map.get("status"))
    failed_stage = _code(summary_map.get("failed_stage"))
    records = list(_walk_typed_records(identity_map))
    candidates = _select_terminal_records(records, failed_stage)
    terminal = _summarize_terminal(candidates, failed_stage)

    authoritative_failures = [
        item for item in records if _authoritative_failure(item)
    ]
    if summary_status == "accepted" and authoritative_failures:
        findings.append(EvidenceAuditFinding(
            code="false_success_blocking_evidence",
            severity=AuditSeverity.CRITICAL,
            message=(
                "Product summary is accepted while authoritative validation "
                "evidence contains a terminal failure."
            ),
            expected="no authoritative blocking failure",
            observed={
                "count": len(authoritative_failures),
                "stages": sorted({
                    item.get("stage") or item.get("scope") or "unknown"
                    for item in authoritative_failures
                }),
            },
            evidence_refs=tuple(
                str(item["path"]) for item in authoritative_failures[:8]
            ),
        ))

    if process_map is not None:
        exit_code = _integer(
            process_map.get("exit_code"),
            process_map.get("returncode"),
            process_map.get("return_code"),
        )
        timed_out = process_map.get("timed_out") is True
        if summary_status == "accepted" and (
            timed_out or exit_code not in {None, 0}
        ):
            findings.append(EvidenceAuditFinding(
                code="false_success_process_failure",
                severity=AuditSeverity.CRITICAL,
                message=(
                    "Product summary is accepted although the recorded "
                    "physical process did not complete successfully."
                ),
                expected={"exit_code": 0, "timed_out": False},
                observed={"exit_code": exit_code, "timed_out": timed_out},
                evidence_refs=("process_record",),
            ))

    if full_map is not None and summary_status == "accepted":
        full_status = _code(full_map.get("status"))
        full_succeeded = full_map.get("succeeded")
        if full_status in _FAILURE_STATUSES or full_succeeded is False:
            findings.append(EvidenceAuditFinding(
                code="false_success_full_result_conflict",
                severity=AuditSeverity.CRITICAL,
                message=(
                    "Product summary is accepted but full_result records "
                    "a failed terminal result."
                ),
                expected="accepted/succeeded",
                observed={
                    "status": full_status,
                    "succeeded": full_succeeded,
                },
                evidence_refs=("full_result",),
            ))

    findings.extend(_identity_findings(summary_map, identity_map))

    if failed_stage in _TERMINAL_STAGES:
        if not candidates:
            findings.append(EvidenceAuditFinding(
                code="terminal_evidence_missing",
                severity=AuditSeverity.ERROR,
                message=(
                    "The summary names a validation failure stage, but no "
                    "matching typed terminal evidence could be selected."
                ),
                expected=failed_stage,
                observed=None,
                evidence_refs=("execution_identity",),
            ))
        else:
            findings.extend(_terminal_conflict_findings(terminal, candidates))
            findings.extend(_summary_terminal_findings(summary_map, terminal))

    if (
        summary_status == "rejected"
        and _summary_validation_all_passed(summary_map)
        and not authoritative_failures
    ):
        findings.append(EvidenceAuditFinding(
            code="possible_false_failure",
            severity=AuditSeverity.WARNING,
            message=(
                "The product is rejected while all reported validation "
                "stages pass and no authoritative blocking evidence is present."
            ),
            expected="typed rejection evidence",
            observed=summary_map.get("validation"),
            evidence_refs=("product_summary",),
        ))

    highest = max(
        (item.severity.rank for item in findings),
        default=-1,
    )
    status = (
        "contradiction"
        if highest >= AuditSeverity.ERROR.rank
        else "warning"
        if highest == AuditSeverity.WARNING.rank
        else "clean"
    )
    return EvidenceAuditReport(
        status=status,
        findings=tuple(findings),
        summary_status=summary_status,
        terminal_stage=failed_stage,
        terminal_evidence=terminal,
    )


def _walk_typed_records(
    value: Any,
    *,
    path: str = "$",
    scope: str | None = None,
):
    if isinstance(value, Mapping):
        local_scope = scope
        suite = _code(value.get("suite_id")) or _code(value.get("suite"))
        split = _code(value.get("split"))
        if suite in _TERMINAL_STAGES:
            local_scope = suite
        elif split in {"public", "hidden"}:
            local_scope = split

        stage = (
            _code(value.get("failed_stage"))
            or _code(value.get("stage"))
            or _code(value.get("phase"))
        )
        status = (
            _code(value.get("evaluation_status"))
            or _code(value.get("status"))
        )
        record = {
            "path": path,
            "scope": local_scope,
            "stage": stage,
            "status": status,
            "blocking": value.get("blocking") is True,
            "failure_kind": (
                _code(value.get("failure_kind"))
                or _code(value.get("category"))
            ),
            "failure_owner": (
                _code(value.get("failure_owner"))
                or _code(value.get("owner"))
            ),
            "route_action": (
                _code(value.get("route_action"))
                or _code(value.get("next_action"))
                or _code(value.get("action"))
            ),
            "reason_code": (
                _code(value.get("reason_code"))
                or _code(value.get("summary"))
            ),
            "terminal": value.get("terminal") is True,
            "authoritative": value.get("authoritative") is True,
        }
        if (
            record["blocking"]
            or record["status"] in _FAILURE_STATUSES
            or any(
                record[key] is not None
                for key in (
                    "stage", "failure_kind", "failure_owner",
                    "route_action", "reason_code",
                )
            )
        ):
            yield record
        for key, item in value.items():
            yield from _walk_typed_records(
                item,
                path=f"{path}.{key}",
                scope=local_scope,
            )
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, item in enumerate(value):
            yield from _walk_typed_records(
                item,
                path=f"{path}[{index}]",
                scope=scope,
            )


def _select_terminal_records(
    records: Sequence[Mapping[str, Any]],
    stage: str | None,
) -> list[Mapping[str, Any]]:
    if stage not in _TERMINAL_STAGES:
        return []
    equivalent = _STAGE_EQUIVALENTS.get(stage, frozenset({stage}))
    levels = (
        [
            item for item in records
            if item.get("stage") in equivalent
            and item.get("blocking") is True
        ],
        [
            item for item in records
            if item.get("stage") in equivalent
        ],
        [
            item for item in records
            if item.get("scope") in equivalent
            and item.get("blocking") is True
        ],
        [
            item for item in records
            if item.get("scope") in equivalent
        ],
        [
            item for item in records
            if item.get("blocking") is True
            and (
                item.get("stage") in _TERMINAL_STAGES
                or item.get("scope") in _TERMINAL_STAGES
            )
        ],
    )
    for selected in levels:
        if selected:
            return selected
    return []


def _summarize_terminal(
    records: Sequence[Mapping[str, Any]],
    stage: str | None,
) -> dict[str, Any]:
    if not records:
        return {}
    kinds = _non_success_values(records, "failure_kind")
    reasons = _non_success_values(records, "reason_code")
    owners = _non_success_values(records, "failure_owner")
    actions = _non_success_values(records, "route_action")
    conflict_fields = [
        name for name, values in (
            ("failure_kind", kinds),
            ("reason_code", reasons if not kinds else set()),
            ("failure_owner", owners),
            ("route_action", actions),
        )
        if len(values) > 1
    ]
    reason = (
        next(iter(kinds))
        if len(kinds) == 1
        else next(iter(reasons))
        if not kinds and len(reasons) == 1
        else None
    )
    return {
        "stage": stage,
        "reason_code": reason,
        "failure_owner": next(iter(owners)) if len(owners) == 1 else None,
        "route_action": next(iter(actions)) if len(actions) == 1 else None,
        "conflict_fields": conflict_fields,
        "record_count": len(records),
        "evidence_refs": [str(item["path"]) for item in records[:16]],
    }


def _authoritative_failure(record: Mapping[str, Any]) -> bool:
    status_failed = record.get("status") in _FAILURE_STATUSES
    terminal_stage = (
        record.get("stage") in _TERMINAL_STAGES
        or record.get("scope") in _TERMINAL_STAGES
    )
    path = str(record.get("path", ""))
    validation_path = any(
        token in path
        for token in (
            ".suites", ".validation", ".public_rtl_cosim",
            ".csynth", ".cosim", ".hidden",
        )
    )
    explicit = (
        record.get("terminal") is True
        or record.get("authoritative") is True
    )
    return bool(
        terminal_stage
        and (record.get("blocking") is True or status_failed)
        and (validation_path or explicit)
    )


def _terminal_conflict_findings(
    terminal: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> list[EvidenceAuditFinding]:
    conflicts = terminal.get("conflict_fields")
    if not isinstance(conflicts, list) or not conflicts:
        return []
    return [EvidenceAuditFinding(
        code="terminal_typed_evidence_conflict",
        severity=AuditSeverity.ERROR,
        message=(
            "Multiple equally authoritative terminal records disagree; "
            "the result must remain unknown-safe."
        ),
        expected="one consistent terminal owner/reason/action",
        observed={"conflict_fields": conflicts},
        evidence_refs=tuple(str(item["path"]) for item in candidates[:16]),
    )]


def _summary_terminal_findings(
    summary: Mapping[str, Any],
    terminal: Mapping[str, Any],
) -> list[EvidenceAuditFinding]:
    findings: list[EvidenceAuditFinding] = []
    conflicts = terminal.get("conflict_fields")
    if isinstance(conflicts, list) and conflicts:
        dangerous = bool(
            {"failure_owner", "route_action"} & set(conflicts)
        )
        stage = _code(terminal.get("stage")) or "unknown"
        fallback = (
            "public"
            if stage in {"public_csim", "csim"}
            else stage
        )
        expected_reason = (
            "unknown_conflicting_evidence"
            if dangerous
            else f"{fallback}_validation_unknown"
        )
        expected = {"reason_code": expected_reason}
        if dangerous:
            expected.update({
                "failure_owner": "unknown",
                "route_action": "review_unknown",
            })
        for field_name, expected_value in expected.items():
            observed = _code(summary.get(field_name))
            if observed != expected_value:
                findings.append(EvidenceAuditFinding(
                    code=f"summary_{field_name}_not_unknown_safe",
                    severity=AuditSeverity.ERROR,
                    message=(
                        "The summary does not fail closed after conflicting "
                        "terminal typed evidence."
                    ),
                    expected=expected_value,
                    observed=observed,
                    evidence_refs=("product_summary",),
                ))
        return findings

    for field_name in ("reason_code", "failure_owner", "route_action"):
        expected = _code(terminal.get(field_name))
        observed = _code(summary.get(field_name))
        if expected is not None and observed != expected:
            findings.append(EvidenceAuditFinding(
                code=f"summary_{field_name}_conflict",
                severity=AuditSeverity.ERROR,
                message=(
                    f"Product summary {field_name} conflicts with selected "
                    "terminal typed evidence."
                ),
                expected=expected,
                observed=observed,
                evidence_refs=(
                    "product_summary",
                    *tuple(terminal.get("evidence_refs", ()))[:8],
                ),
            ))
    return findings


def _identity_findings(
    summary: Mapping[str, Any],
    identity: Mapping[str, Any],
) -> list[EvidenceAuditFinding]:
    findings: list[EvidenceAuditFinding] = []
    summary_identity = summary.get("execution_identity")
    if not isinstance(summary_identity, Mapping):
        return findings
    for field_name in (
        "execution_id",
        "request_identity_sha256",
        "cache_identity_sha256",
        "bundle_sha256",
    ):
        left = summary_identity.get(field_name)
        right = identity.get(field_name)
        if left is None or right is None:
            continue
        if left != right:
            findings.append(EvidenceAuditFinding(
                code=f"identity_{field_name}_conflict",
                severity=AuditSeverity.CRITICAL,
                message=(
                    "Product summary and execution identity refer to "
                    "different immutable execution evidence."
                ),
                expected=right,
                observed=left,
                evidence_refs=("product_summary", "execution_identity"),
            ))
    return findings


def _summary_validation_all_passed(summary: Mapping[str, Any]) -> bool:
    validation = summary.get("validation")
    if not isinstance(validation, Mapping) or not validation:
        return False
    observed = [
        _code(value) for value in validation.values()
        if _code(value) not in {None, "not_run"}
    ]
    return bool(observed) and all(value == "passed" for value in observed)


def _non_success_values(
    records: Sequence[Mapping[str, Any]],
    key: str,
) -> set[str]:
    return {
        value for item in records
        if (value := _code(item.get(key))) is not None
        and value not in _SUCCESS_CODES
        and value != "none"
    }


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return value


def _integer(*values: Any) -> int | None:
    for value in values:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _code(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().lower()
    if not cleaned or len(cleaned) > 160 or not cleaned[0].isalnum():
        return None
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_-. :/")
    return cleaned if all(char in allowed for char in cleaned) else None


def _required_code(value: str, name: str) -> str:
    cleaned = _code(value)
    if cleaned is None or " " in cleaned or "/" in cleaned:
        raise ValueError(f"{name} must be a safe code")
    return cleaned


def _required_text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value.strip()


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
