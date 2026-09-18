"""Independent, unknown-safe audit of product validation evidence.

This module deliberately does not import ``agrefactor.product.run_output``.
It reconstructs safety invariants from persisted typed evidence so the producer
and auditor do not share the same reducer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from math import sqrt
from typing import Any

from .testbench_semantics import literal_counter


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

_R2_CALIBRATION_CONTRACTS = {
    "prompt_contract_version": "r2-shadow-output-v4",
    "strict_parser": "r2-v1",
    "input_contract_version": "r2-agent-safe-diagnostic-evidence-v2",
}
_R2_CALIBRATION_POLICY_FIELDS = (
    "minimum_total",
    "minimum_covered",
    "minimum_high_confidence",
    "minimum_coverage",
    "minimum_citation_validity_lower_95",
    "maximum_selective_risk",
    "maximum_high_confidence_error_rate",
    "maximum_high_confidence_error_upper_95",
    "maximum_unsafe_scope_rate",
    "maximum_unsafe_scope_upper_95",
)


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


def audit_r2_calibration_bundle(
    bundle: Mapping[str, Any],
) -> EvidenceAuditReport:
    """Independently recompute an R2 calibration bundle and certificate."""

    value = _mapping(bundle, "bundle")
    findings: list[EvidenceAuditFinding] = []
    protocol = value.get("protocol")
    records = value.get("records")
    report = value.get("report")
    policy = value.get("policy")
    provider_identity = value.get("provider_identity")
    certificate = value.get("certificate")
    execution = value.get("execution")
    required = {
        "protocol": protocol,
        "records": records,
        "report": report,
        "policy": policy,
        "provider_identity": provider_identity,
        "certificate": certificate,
        "execution": execution,
    }
    malformed = [
        name
        for name, item in required.items()
        if not isinstance(item, Mapping)
        and not (name == "records" and isinstance(item, list))
    ]
    if malformed:
        findings.append(EvidenceAuditFinding(
            code="r2_calibration_bundle_malformed",
            severity=AuditSeverity.CRITICAL,
            message="Calibration bundle is missing required structured fields.",
            expected=sorted(required),
            observed=malformed,
            evidence_refs=("calibration_bundle",),
        ))
        return _calibration_audit_report(findings, certificate={})

    assert isinstance(protocol, Mapping)
    assert isinstance(records, list)
    assert isinstance(report, Mapping)
    assert isinstance(policy, Mapping)
    assert isinstance(provider_identity, Mapping)
    assert isinstance(certificate, Mapping)
    assert isinstance(execution, Mapping)

    privacy_refs = tuple(_calibration_private_payload_refs(value))
    if privacy_refs:
        findings.append(EvidenceAuditFinding(
            code="r2_calibration_private_payload_persisted",
            severity=AuditSeverity.CRITICAL,
            message=(
                "Calibration evidence persisted a credential, raw provider "
                "response, or private-reasoning payload."
            ),
            expected="no private provider payload",
            observed={"count": len(privacy_refs)},
            evidence_refs=privacy_refs[:16],
        ))

    split_id = protocol.get("split_id")
    record_ids = protocol.get("record_ids")
    if (
        not isinstance(split_id, str)
        or not split_id.strip()
        or not isinstance(record_ids, list)
        or not record_ids
        or any(not isinstance(item, str) or not item for item in record_ids)
        or len(record_ids) != len(set(record_ids))
    ):
        findings.append(EvidenceAuditFinding(
            code="r2_calibration_protocol_invalid",
            severity=AuditSeverity.CRITICAL,
            message="Calibration protocol does not define a unique frozen split.",
            expected="non-empty split_id and unique ordered record_ids",
            observed=protocol,
            evidence_refs=("protocol",),
        ))
        return _calibration_audit_report(findings, certificate=certificate)

    expected_split_sha = _audit_sha256({
        "split_id": split_id,
        "record_ids": record_ids,
    })
    if (
        protocol.get("split_sha256") != expected_split_sha
        or protocol.get("frozen_before_provider_evaluation") is not True
    ):
        findings.append(EvidenceAuditFinding(
            code="r2_calibration_split_not_frozen",
            severity=AuditSeverity.CRITICAL,
            message="Calibration split identity is invalid or was not pre-frozen.",
            expected={
                "split_sha256": expected_split_sha,
                "frozen_before_provider_evaluation": True,
            },
            observed={
                "split_sha256": protocol.get("split_sha256"),
                "frozen_before_provider_evaluation": protocol.get(
                    "frozen_before_provider_evaluation"
                ),
            },
            evidence_refs=("protocol",),
        ))

    observed_ids = [
        item.get("record_id") if isinstance(item, Mapping) else None
        for item in records
    ]
    if observed_ids != record_ids:
        findings.append(EvidenceAuditFinding(
            code="r2_calibration_split_membership_mismatch",
            severity=AuditSeverity.CRITICAL,
            message="Calibration records do not match the frozen order and membership.",
            expected=record_ids,
            observed=observed_ids,
            evidence_refs=("protocol", "records"),
        ))

    findings.extend(
        _audit_calibration_shadow_records(records, provider_identity)
    )
    try:
        expected_report = _recompute_calibration_report(
            records,
            split_id=split_id,
            split_sha256=expected_split_sha,
        )
    except (TypeError, ValueError) as exc:
        findings.append(EvidenceAuditFinding(
            code="r2_calibration_record_invalid",
            severity=AuditSeverity.CRITICAL,
            message="Calibration record cannot be independently evaluated.",
            expected="well-formed advisory/truth records",
            observed=str(exc),
            evidence_refs=("records",),
        ))
        return _calibration_audit_report(findings, certificate=certificate)

    if dict(report) != expected_report:
        findings.append(EvidenceAuditFinding(
            code="r2_calibration_report_mismatch",
            severity=AuditSeverity.CRITICAL,
            message="Persisted calibration metrics differ from independent recomputation.",
            expected=expected_report,
            observed=dict(report),
            evidence_refs=("report", "records"),
        ))

    policy_without_hash = {
        key: item for key, item in policy.items() if key != "policy_sha256"
    }
    expected_policy_sha = _audit_sha256(policy_without_hash)
    if policy.get("policy_sha256") != expected_policy_sha:
        findings.append(EvidenceAuditFinding(
            code="r2_calibration_policy_hash_mismatch",
            severity=AuditSeverity.CRITICAL,
            message="Frozen calibration policy hash is invalid.",
            expected=expected_policy_sha,
            observed=policy.get("policy_sha256"),
            evidence_refs=("policy",),
        ))

    try:
        expected_reasons = _calibration_policy_reasons(expected_report, policy)
    except (TypeError, ValueError) as exc:
        findings.append(EvidenceAuditFinding(
            code="r2_calibration_policy_invalid",
            severity=AuditSeverity.CRITICAL,
            message="Calibration acceptance policy is malformed.",
            expected="complete bounded policy",
            observed=str(exc),
            evidence_refs=("policy",),
        ))
        return _calibration_audit_report(findings, certificate=certificate)

    expected_accepted = not expected_reasons
    expected_labels = ["high"] if expected_accepted else []
    expected_certificate = {
        "schema_version": 1,
        "split_id": split_id,
        "split_sha256": expected_split_sha,
        "report_sha256": _audit_sha256(expected_report),
        "policy_sha256": expected_policy_sha,
        "provider_identity_sha256": _audit_sha256(dict(provider_identity)),
        **_R2_CALIBRATION_CONTRACTS,
        "eligible_confidence_labels": expected_labels,
        "accepted": expected_accepted,
        "reasons": list(expected_reasons),
    }
    expected_certificate["certificate_id"] = (
        "r2-calibration-" + _audit_sha256(expected_certificate)[:32]
    )
    if dict(certificate) != expected_certificate:
        findings.append(EvidenceAuditFinding(
            code="r2_calibration_certificate_mismatch",
            severity=AuditSeverity.CRITICAL,
            message="Calibration certificate is not derived from the audited evidence.",
            expected=expected_certificate,
            observed=dict(certificate),
            evidence_refs=("certificate", "report", "policy"),
        ))

    provider_calls = execution.get("provider_calls")
    if (
        isinstance(provider_calls, bool)
        or not isinstance(provider_calls, int)
        or provider_calls != len(records)
    ):
        findings.append(EvidenceAuditFinding(
            code="r2_calibration_provider_accounting_mismatch",
            severity=AuditSeverity.CRITICAL,
            message="Provider-call accounting does not match calibration records.",
            expected=len(records),
            observed=provider_calls,
            evidence_refs=("execution", "records"),
        ))
    if execution.get("git_history_mutations") not in {0, None}:
        findings.append(EvidenceAuditFinding(
            code="r2_calibration_git_history_mutated",
            severity=AuditSeverity.CRITICAL,
            message="Calibration execution mutated Git history.",
            expected=0,
            observed=execution.get("git_history_mutations"),
            evidence_refs=("execution",),
        ))

    return _calibration_audit_report(findings, certificate=certificate)


def _audit_calibration_shadow_records(
    records: Sequence[Any],
    provider_identity: Mapping[str, Any],
) -> list[EvidenceAuditFinding]:
    findings: list[EvidenceAuditFinding] = []
    for index, raw in enumerate(records):
        ref = f"records[{index}]"
        if not isinstance(raw, Mapping):
            findings.append(EvidenceAuditFinding(
                code="r2_calibration_record_not_mapping",
                severity=AuditSeverity.CRITICAL,
                message="Calibration record is not a mapping.",
                expected="mapping",
                observed=type(raw).__name__,
                evidence_refs=(ref,),
            ))
            continue
        shadow = raw.get("shadow")
        advisory = raw.get("advisory")
        if not isinstance(shadow, Mapping):
            findings.append(EvidenceAuditFinding(
                code="r2_calibration_shadow_evidence_missing",
                severity=AuditSeverity.CRITICAL,
                message="Calibration record lacks its shadow audit artifact.",
                expected="shadow audit artifact",
                observed=shadow,
                evidence_refs=(ref,),
            ))
            continue
        equivalence = shadow.get("equivalence")
        metadata = (
            advisory.get("metadata", {})
            if isinstance(advisory, Mapping)
            else {}
        )
        safe = (
            isinstance(advisory, Mapping)
            and shadow.get("advisory") == advisory
            and shadow.get("provider_identity") == provider_identity
            and isinstance(equivalence, Mapping)
            and equivalence.get("equivalent") is True
            and equivalence.get("changed_fields") == []
            and shadow.get("shadow_only") is True
            and shadow.get("critical_safety_violation") is False
            and advisory.get("accepted") is False
            and isinstance(metadata, Mapping)
            and metadata.get("bounded_repair_intent_executed", False) is False
        )
        if not safe:
            findings.append(EvidenceAuditFinding(
                code="r2_calibration_shadow_authority_violation",
                severity=AuditSeverity.CRITICAL,
                message="Calibration advisory changed or gained authority over the main path.",
                expected="equivalent, shadow-only, non-accepting advisory",
                observed=dict(shadow),
                evidence_refs=(ref, f"{ref}.shadow"),
            ))
        accounting = shadow.get("accounting")
        if (
            not isinstance(accounting, Mapping)
            or accounting.get("provider_calls") != 1
        ):
            findings.append(EvidenceAuditFinding(
                code="r2_calibration_record_accounting_mismatch",
                severity=AuditSeverity.ERROR,
                message="Each calibration record must bind exactly one provider call.",
                expected=1,
                observed=(
                    accounting.get("provider_calls")
                    if isinstance(accounting, Mapping)
                    else None
                ),
                evidence_refs=(f"{ref}.shadow.accounting",),
            ))
    return findings


def _recompute_calibration_report(
    records: Sequence[Any],
    *,
    split_id: str,
    split_sha256: str,
) -> dict[str, Any]:
    owner_truth: list[str] = []
    owner_pred: list[str] = []
    failure_truth: list[str] = []
    failure_pred: list[str] = []
    citation_ok = high_total = high_errors = unsafe = 0
    for raw in records:
        if not isinstance(raw, Mapping):
            raise TypeError("record_not_mapping")
        advisory = raw.get("advisory")
        truth = raw.get("truth")
        if not isinstance(advisory, Mapping) or not isinstance(truth, Mapping):
            raise ValueError("advisory_or_truth_missing")
        if advisory.get("abstain_reason") is not None:
            continue
        refs = advisory.get("evidence_refs", ())
        evidence_ids = raw.get("evidence_ids", ())
        if (
            not isinstance(evidence_ids, Sequence)
            or isinstance(evidence_ids, (str, bytes))
        ):
            raise TypeError("evidence_ids_not_sequence")
        allowed = set(evidence_ids)
        citation_ok += int(
            isinstance(refs, Sequence)
            and not isinstance(refs, (str, bytes))
            and bool(refs)
            and set(refs).issubset(allowed)
        )
        owner = str(advisory.get("suspected_owner", "unknown"))
        failure = str(advisory.get("suspected_failure_class", "unknown"))
        true_owner = str(truth.get("owner", "unknown"))
        true_failure = str(truth.get("failure_class", "unknown"))
        owner_pred.append(owner)
        owner_truth.append(true_owner)
        failure_pred.append(failure)
        failure_truth.append(true_failure)
        error = owner != true_owner or failure != true_failure
        if advisory.get("confidence") == "high":
            high_total += 1
            high_errors += int(error)
        scope = advisory.get("repair_scope")
        unsafe += int(
            scope == "testbench_only"
            or (scope == "candidate_only" and owner != "candidate")
        )
    total = len(records)
    covered = len(owner_pred)
    errors = sum(
        owner != true_owner or failure != true_failure
        for owner, true_owner, failure, true_failure in zip(
            owner_pred, owner_truth, failure_pred, failure_truth
        )
    )
    return {
        "schema_version": 1,
        "split_id": split_id,
        "split_sha256": split_sha256,
        "total": total,
        "covered": covered,
        "abstained": total - covered,
        "coverage": covered / total if total else 0.0,
        "selective_risk": errors / covered if covered else 0.0,
        "citation_validity": citation_ok / covered if covered else 1.0,
        "owner_macro_f1": _audit_macro_f1(owner_truth, owner_pred),
        "failure_class_macro_f1": _audit_macro_f1(
            failure_truth, failure_pred
        ),
        "high_confidence_total": high_total,
        "high_confidence_errors": high_errors,
        "high_confidence_error_rate": (
            high_errors / high_total if high_total else 0.0
        ),
        "unsafe_scope_rate": unsafe / covered if covered else 0.0,
        "confidence_intervals_95": {
            "coverage": list(_audit_wilson(covered, total)),
            "citation_validity": list(_audit_wilson(citation_ok, covered)),
            "high_confidence_error_rate": list(
                _audit_wilson(high_errors, high_total)
            ),
            "unsafe_scope_rate": list(_audit_wilson(unsafe, covered)),
        },
    }


def _calibration_policy_reasons(
    report: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> tuple[str, ...]:
    missing = [name for name in _R2_CALIBRATION_POLICY_FIELDS if name not in policy]
    if missing:
        raise ValueError(f"missing_policy_fields:{','.join(missing)}")
    intervals = report["confidence_intervals_95"]
    checks = {
        "insufficient_total": report["total"] >= policy["minimum_total"],
        "insufficient_covered": report["covered"] >= policy["minimum_covered"],
        "insufficient_high_confidence": (
            report["high_confidence_total"] >= policy["minimum_high_confidence"]
        ),
        "coverage_below_threshold": report["coverage"] >= policy["minimum_coverage"],
        "citation_lower_bound_below_threshold": (
            intervals["citation_validity"][0]
            >= policy["minimum_citation_validity_lower_95"]
        ),
        "selective_risk_above_threshold": (
            report["selective_risk"] <= policy["maximum_selective_risk"]
        ),
        "high_confidence_error_rate_above_threshold": (
            report["high_confidence_error_rate"]
            <= policy["maximum_high_confidence_error_rate"]
        ),
        "high_confidence_error_upper_bound_above_threshold": (
            intervals["high_confidence_error_rate"][1]
            <= policy["maximum_high_confidence_error_upper_95"]
        ),
        "unsafe_scope_rate_above_threshold": (
            report["unsafe_scope_rate"] <= policy["maximum_unsafe_scope_rate"]
        ),
        "unsafe_scope_upper_bound_above_threshold": (
            intervals["unsafe_scope_rate"][1]
            <= policy["maximum_unsafe_scope_upper_95"]
        ),
    }
    return tuple(name for name, passed in checks.items() if not passed)


def _audit_macro_f1(truth: Sequence[str], predicted: Sequence[str]) -> float:
    labels = sorted(set(truth) | set(predicted))
    if not labels:
        return 0.0
    scores: list[float] = []
    for label in labels:
        tp = sum(t == label and p == label for t, p in zip(truth, predicted))
        fp = sum(t != label and p == label for t, p in zip(truth, predicted))
        fn = sum(t == label and p != label for t, p in zip(truth, predicted))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        scores.append(
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
    return sum(scores) / len(scores)


def _audit_wilson(
    successes: int,
    total: int,
    z: float = 1.959963984540054,
) -> tuple[float, float]:
    if total == 0:
        return (0.0, 1.0)
    rate = successes / total
    denominator = 1 + z * z / total
    center = (rate + z * z / (2 * total)) / denominator
    margin = (
        z
        * sqrt((rate * (1 - rate) + z * z / (4 * total)) / total)
        / denominator
    )
    return (max(0.0, center - margin), min(1.0, center + margin))


def _audit_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _calibration_private_payload_refs(
    value: Any,
    *,
    path: str = "$",
) -> list[str]:
    refs: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}"
            normalized = str(key).strip().casefold()
            forbidden = (
                normalized in {
                    "api_key",
                    "authorization",
                    "raw_provider_response",
                    "response_content",
                    "reasoning_content",
                    "chain_of_thought",
                    "private_reasoning",
                }
                or normalized.endswith("_api_key")
            )
            if (
                forbidden
                and item is not None
                and item is not False
                and item != ""
            ):
                refs.append(child)
            refs.extend(_calibration_private_payload_refs(item, path=child))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, item in enumerate(value):
            refs.extend(
                _calibration_private_payload_refs(item, path=f"{path}[{index}]")
            )
    elif isinstance(value, str):
        lowered = value.casefold()
        if any(tag in lowered for tag in ("<think>", "</think>", "<reasoning>")):
            refs.append(path)
    return refs


def _calibration_audit_report(
    findings: Sequence[EvidenceAuditFinding],
    *,
    certificate: Mapping[str, Any],
) -> EvidenceAuditReport:
    has_errors = any(
        item.severity in {AuditSeverity.ERROR, AuditSeverity.CRITICAL}
        for item in findings
    )
    accepted = certificate.get("accepted") is True and not has_errors
    return EvidenceAuditReport(
        status=("contradiction" if has_errors else "clean"),
        findings=tuple(findings),
        summary_status=(
            "calibration_accepted" if accepted else "calibration_rejected"
        ),
        terminal_stage="r2_calibration",
        terminal_evidence={
            "certificate_id": certificate.get("certificate_id"),
            "certificate_accepted": certificate.get("accepted") is True,
            "independently_accepted": accepted,
        },
    )


def audit_testbench_semantic_revision(
    revision: Mapping[str, Any],
) -> EvidenceAuditReport:
    """Independently reject unauthorized or weakened Testbench revisions."""

    value = _mapping(revision, "revision")
    before = _mapping(value.get("before"), "revision.before")
    after = _mapping(value.get("after"), "revision.after")
    findings: list[EvidenceAuditFinding] = []
    authorization = _code(value.get("authorization"))
    if authorization != "auto_public_bounded":
        findings.append(
            EvidenceAuditFinding(
                code="testbench_revision_not_authorized",
                severity=AuditSeverity.ERROR,
                message="Only bounded AUTO Public Testbench revisions are authorized.",
                expected="auto_public_bounded",
                observed=authorization,
                evidence_refs=("testbench_semantic_revision",),
            )
        )
    for field_name in ("suite_id", "split", "source_kind"):
        if before.get(field_name) != after.get(field_name):
            findings.append(
                EvidenceAuditFinding(
                    code=f"testbench_{field_name}_changed",
                    severity=AuditSeverity.CRITICAL,
                    message=f"Testbench revision changed immutable {field_name}.",
                    expected=before.get(field_name),
                    observed=after.get(field_name),
                    evidence_refs=("testbench_semantic_revision",),
                )
            )
    _audit_count_floor(findings, before, after, "main_count", "testbench_main_removed")
    _audit_count_floor(
        findings,
        before,
        after,
        "comparison_count",
        "testbench_comparison_oracle_weakened",
    )
    _audit_count_floor(
        findings,
        before,
        after,
        "return_guard_count",
        "testbench_return_oracle_weakened",
    )
    for field_name, code in (
        ("top_reference_counts", "testbench_top_reference_removed"),
        ("oracle_marker_counts", "testbench_oracle_marker_removed"),
        ("comparison_operator_counts", "testbench_comparison_operator_weakened"),
        ("failure_signal_counts", "testbench_failure_signal_weakened"),
        ("runtime_protocol_counts", "testbench_runtime_protocol_weakened"),
        ("control_flow_counts", "testbench_case_control_removed"),
    ):
        before_counts = _integer_mapping(before.get(field_name), field_name)
        after_counts = _integer_mapping(after.get(field_name), field_name)
        for key, expected in before_counts.items():
            observed = after_counts.get(key, 0)
            if observed < expected:
                findings.append(
                    EvidenceAuditFinding(
                        code=code,
                        severity=AuditSeverity.CRITICAL,
                        message=f"Testbench revision reduced {field_name}.{key}.",
                        expected=expected,
                        observed=observed,
                        evidence_refs=("testbench_semantic_revision",),
                    )
                )
    before_definitions = _integer_mapping(
        before.get("top_definition_counts"), "top_definition_counts"
    )
    after_definitions = _integer_mapping(
        after.get("top_definition_counts"), "top_definition_counts"
    )
    for key, observed in after_definitions.items():
        expected = before_definitions.get(key, 0)
        if observed > expected:
            findings.append(
                EvidenceAuditFinding(
                    code="testbench_top_reimplementation_added",
                    severity=AuditSeverity.CRITICAL,
                    message="Testbench revision introduced a top-function definition.",
                    expected=expected,
                    observed=observed,
                    evidence_refs=("testbench_semantic_revision",),
                )
            )
    before_literals = literal_counter(before)
    after_literals = literal_counter(after)
    missing_literals = before_literals - after_literals
    if missing_literals:
        findings.append(
            EvidenceAuditFinding(
                code="testbench_case_literal_changed",
                severity=AuditSeverity.CRITICAL,
                message="Testbench revision removed or changed existing case literals.",
                expected=sum(before_literals.values()),
                observed=sum(after_literals.values()),
                evidence_refs=("testbench_semantic_revision",),
            )
        )
    before_oracles = _string_counter(before.get("oracle_fingerprints"), "oracle_fingerprints")
    after_oracles = _string_counter(after.get("oracle_fingerprints"), "oracle_fingerprints")
    if before_oracles - after_oracles:
        findings.append(
            EvidenceAuditFinding(
                code="testbench_oracle_identity_changed",
                severity=AuditSeverity.CRITICAL,
                message="Testbench revision removed or changed an existing oracle expression identity.",
                expected=sum(before_oracles.values()),
                observed=sum(after_oracles.values()),
                evidence_refs=("testbench_semantic_revision",),
            )
        )
    status = "contradiction" if any(
        item.severity in {AuditSeverity.ERROR, AuditSeverity.CRITICAL}
        for item in findings
    ) else "clean"
    return EvidenceAuditReport(
        status=status,
        findings=tuple(findings),
        summary_status="revision_allowed" if status == "clean" else "revision_blocked",
        terminal_stage="testbench_semantic_revision",
        terminal_evidence={
            "revision_sha256": value.get("revision_sha256"),
            "authorization": authorization,
            "changed": value.get("changed") is True,
            "source_content_persisted": False,
            "hidden_content_persisted": False,
        },
    )


def _audit_count_floor(
    findings: list[EvidenceAuditFinding],
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    field_name: str,
    code: str,
) -> None:
    expected = _integer(before.get(field_name)) or 0
    observed = _integer(after.get(field_name)) or 0
    if observed < expected:
        findings.append(
            EvidenceAuditFinding(
                code=code,
                severity=AuditSeverity.CRITICAL,
                message=f"Testbench revision reduced {field_name}.",
                expected=expected,
                observed=observed,
                evidence_refs=("testbench_semantic_revision",),
            )
        )


def _integer_mapping(value: Any, name: str) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    result: dict[str, int] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise ValueError(f"{name} keys must not be empty")
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise ValueError(f"{name} values must be non-negative integers")
        result[key] = item
    return result


def _string_counter(value: Any, name: str):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TypeError(f"{name} must be a list of strings")
    from collections import Counter
    return Counter(value)


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
