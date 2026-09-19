#!/usr/bin/env python3
"""Independent file-only audit for the R5 corrective replication."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.r5_audit_formal_campaign import (
    ALLOWED_STATUSES,
    ARMS,
    canonical_sha256,
    paired_endpoint_rows,
    unsafe_persisted_content,
)

FREEZE_ROOT = Path(
    "/data/agrefactor_runs/r5_p6_formal_public_contract_correction_v2_freeze"
)
PARENT_FREEZE_ROOT = Path(
    "/data/agrefactor_runs/r5_p6_formal_future_freeze_preflight"
)
PARENT_RUN_ROOT = Path(
    "/data/agrefactor_runs/r5_p6_formal_campaign_real"
)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add_finding(
    findings: list[dict[str, str]],
    severity: str,
    code: str,
    message: str,
) -> None:
    findings.append({"severity": severity, "code": code, "message": message})


def outcome_counts(observations: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(str(item.get("status", "unknown")) for item in observations)
    return {
        status: counts[status]
        for status in (
            "baseline_accepted",
            "verified_positive",
            "verified_negative",
            "abstained",
            "inconclusive",
            "invalid_evidence",
        )
    } | {
        "unknown": sum(
            count
            for status, count in counts.items()
            if status not in ALLOWED_STATUSES
        )
    }


def audit(root: Path) -> dict[str, Any]:
    result_path = root / "formal_campaign_result.json"
    correction_path = FREEZE_ROOT / "correction_manifest.json"
    protocol_path = FREEZE_ROOT / "independent_protocol_audit.json"
    parent_manifest_path = PARENT_FREEZE_ROOT / "formal_future_manifest.json"
    parent_result_path = PARENT_RUN_ROOT / "formal_campaign_result.json"
    parent_audit_path = PARENT_RUN_ROOT / "independent_audit.json"

    result = load(result_path)
    correction = load(correction_path)
    protocol = load(protocol_path)
    parent_manifest = load(parent_manifest_path)
    parent_result = load(parent_result_path)
    parent_audit = load(parent_audit_path)
    findings: list[dict[str, str]] = []

    unsigned = dict(result)
    stored_result_sha = unsigned.pop("result_sha256", None)
    if stored_result_sha != canonical_sha256(unsigned):
        add_finding(
            findings,
            "critical",
            "result_hash_invalid",
            "The corrective result canonical hash does not verify.",
        )
    if result.get("status") != "ready_for_independent_audit":
        add_finding(
            findings,
            "critical",
            "replication_not_auditable",
            "The corrective replication did not reach its audit boundary.",
        )
    if (
        result.get("campaign_role") != "posthoc_corrective_replication"
        or result.get("future_holdout_claim") is not False
    ):
        add_finding(
            findings,
            "critical",
            "corrective_role_misrepresented",
            "The replication was represented as a new future holdout.",
        )
    if (
        correction.get("status") != "frozen_ready_for_corrective_replication"
        or protocol.get("status") != "clean_ready_for_corrective_replication"
    ):
        add_finding(
            findings,
            "critical",
            "corrective_freeze_not_clean",
            "The zero-call corrective freeze or protocol audit is not clean.",
        )
    if result.get("correction_manifest_sha256") != correction.get("manifest_sha256"):
        add_finding(
            findings,
            "critical",
            "correction_manifest_mismatch",
            "The result is not bound to the corrective manifest.",
        )
    if protocol.get("manifest_sha256") != correction.get("manifest_sha256"):
        add_finding(
            findings,
            "critical",
            "protocol_manifest_mismatch",
            "The corrective protocol audit is not bound to its manifest.",
        )
    if result.get("parent_manifest_sha256") != parent_manifest.get("manifest_sha256"):
        add_finding(
            findings,
            "critical",
            "parent_manifest_mismatch",
            "The corrective result is not bound to the original frozen campaign.",
        )
    if correction.get("parent_manifest_sha256") != parent_manifest.get("manifest_sha256"):
        add_finding(
            findings,
            "critical",
            "freeze_parent_mismatch",
            "The corrective freeze is not bound to the original manifest.",
        )
    if (
        correction.get("parent_result_file_sha256") != file_sha256(parent_result_path)
        or correction.get("parent_audit_file_sha256") != file_sha256(parent_audit_path)
        or parent_result.get("status") != "ready_for_independent_audit"
        or parent_audit.get("critical_finding_count") != 0
    ):
        add_finding(
            findings,
            "critical",
            "parent_evidence_changed_or_unclean",
            "The original campaign evidence was changed or was not cleanly audited.",
        )
    if result.get("repository_commit") != correction.get("repository_commit"):
        add_finding(
            findings,
            "critical",
            "repository_commit_mismatch",
            "The replication commit differs from the zero-call freeze.",
        )

    raw_contracts = correction.get("case_contracts")
    contracts = (
        [dict(item) for item in raw_contracts if isinstance(item, Mapping)]
        if isinstance(raw_contracts, list)
        else []
    )
    case_by_id = {str(item.get("case_id")): item for item in contracts}
    expected_case_ids = tuple(sorted(case_by_id))
    if len(case_by_id) != 2:
        add_finding(
            findings,
            "critical",
            "case_contract_inventory_invalid",
            "Exactly two frozen corrective case contracts are required.",
        )
    if tuple(sorted(str(item) for item in result.get("case_ids", ()))) != expected_case_ids:
        add_finding(
            findings,
            "critical",
            "case_inventory_mismatch",
            "Executed cases differ from the corrective freeze.",
        )
    if result.get("repeats") != 3:
        add_finding(
            findings,
            "critical",
            "repeat_count_mismatch",
            "All three corrective repeats were not retained.",
        )

    baselines = result.get("baselines")
    observations = result.get("observations")
    if not isinstance(baselines, list):
        baselines = []
    if not isinstance(observations, list):
        observations = []
    if len(baselines) != len(case_by_id) * 3:
        add_finding(findings, "critical", "baseline_count_mismatch", "Expected six baselines.")
    if len(observations) != len(case_by_id) * 3 * len(ARMS):
        add_finding(findings, "critical", "arm_count_mismatch", "Expected 42 A0-A6 observations.")

    baseline_ids: set[str] = set()
    baseline_provider = baseline_vitis = 0
    accepted_baselines = 0
    for item in baselines:
        if not isinstance(item, Mapping):
            add_finding(findings, "critical", "baseline_malformed", "A baseline is not an object.")
            continue
        baseline_id = item.get("baseline_id")
        if not isinstance(baseline_id, str) or baseline_id in baseline_ids:
            add_finding(findings, "critical", "baseline_identity_invalid", "A baseline ID is missing or duplicated.")
        else:
            baseline_ids.add(baseline_id)
        provider = item.get("provider_calls")
        vitis = item.get("vitis_launches")
        if not isinstance(provider, int) or not isinstance(vitis, int):
            add_finding(findings, "critical", "baseline_usage_malformed", "Baseline usage is malformed.")
        else:
            baseline_provider += provider
            baseline_vitis += vitis
        if item.get("formal_status") == "accepted":
            accepted_baselines += 1
        if (
            item.get("hidden_input_count") != 0
            or item.get("cross_arm_cache_used") is not False
            or unsafe_persisted_content(item)
        ):
            add_finding(findings, "critical", "baseline_boundary_violation", "A baseline crossed an evidence boundary.")

    coverage: dict[tuple[str, int], set[str]] = defaultdict(set)
    arm_provider = arm_vitis = 0
    for item in observations:
        if not isinstance(item, Mapping):
            add_finding(findings, "critical", "observation_malformed", "An observation is not an object.")
            continue
        case_id = str(item.get("case_id", ""))
        repeat = item.get("repeat")
        arm = str(item.get("arm", ""))
        key = (case_id, repeat if isinstance(repeat, int) else -1)
        if case_id not in case_by_id or repeat not in {1, 2, 3} or arm not in ARMS:
            add_finding(findings, "critical", "observation_identity_invalid", f"Invalid observation {case_id}/{repeat}/{arm}.")
        if arm in coverage[key]:
            add_finding(findings, "critical", "duplicate_observation", f"Duplicate observation {case_id}/{repeat}/{arm}.")
        coverage[key].add(arm)
        if item.get("baseline_id") not in baseline_ids:
            add_finding(findings, "critical", "baseline_reference_invalid", f"Unknown baseline in {case_id}/{repeat}/{arm}.")
        if item.get("source_sha256") != case_by_id.get(case_id, {}).get("source_sha256"):
            add_finding(findings, "critical", "source_mismatch", f"Source mismatch in {case_id}/{repeat}/{arm}.")
        if (
            item.get("hidden_input_count") != 0
            or item.get("cross_arm_cache_used") is not False
            or unsafe_persisted_content(item)
        ):
            add_finding(findings, "critical", "observation_boundary_violation", f"Boundary violation in {case_id}/{repeat}/{arm}.")
        provider = item.get("provider_calls")
        vitis = item.get("vitis_launches")
        if not isinstance(provider, int) or not isinstance(vitis, int):
            add_finding(findings, "critical", "arm_usage_malformed", f"Malformed usage in {case_id}/{repeat}/{arm}.")
        else:
            arm_provider += provider
            arm_vitis += vitis
            if provider > 2 or vitis > 4:
                add_finding(findings, "critical", "arm_bound_exceeded", f"Arm bound exceeded in {case_id}/{repeat}/{arm}.")
        status = str(item.get("status", "unknown"))
        if status not in ALLOWED_STATUSES:
            add_finding(findings, "critical", "unknown_status", f"Unknown status {status!r}.")
        if status == "verified_positive" and item.get("verified_repair") is not True:
            add_finding(findings, "critical", "verified_flag_mismatch", f"Verified flag mismatch in {case_id}/{repeat}/{arm}.")

    for case_id in expected_case_ids:
        for repeat in range(1, 4):
            if coverage.get((case_id, repeat), set()) != set(ARMS):
                add_finding(findings, "critical", "arm_coverage_incomplete", f"Incomplete A0-A6 coverage for {case_id}/{repeat}.")

    total_provider = baseline_provider + arm_provider
    total_vitis = baseline_vitis + arm_vitis
    budget = correction.get("budget", {})
    if total_provider != result.get("provider_calls"):
        add_finding(findings, "critical", "provider_total_mismatch", "Provider usage does not reconcile.")
    if total_vitis != result.get("vitis_launches"):
        add_finding(findings, "critical", "vitis_total_mismatch", "Vitis usage does not reconcile.")
    if result.get("provider_calls_after") != budget.get("provider_calls_before", -1) + total_provider:
        add_finding(findings, "critical", "provider_ledger_mismatch", "Provider ledger does not reconcile.")
    if result.get("vitis_launches_after") != budget.get("vitis_launches_before", -1) + total_vitis:
        add_finding(findings, "critical", "vitis_ledger_mismatch", "Vitis ledger does not reconcile.")
    if result.get("provider_calls_after", 501) > budget.get("provider_hard_cap", 0):
        add_finding(findings, "critical", "provider_hard_cap_exceeded", "Provider hard cap was exceeded.")
    if result.get("vitis_launches_after", 501) > budget.get("vitis_hard_cap", 0):
        add_finding(findings, "critical", "vitis_hard_cap_exceeded", "Vitis hard cap was exceeded.")
    if (
        result.get("hidden_input_count") != 0
        or result.get("cross_arm_cache_used") is not False
        or result.get("git_history_mutations") != 0
        or result.get("r5_accepted") is not False
        or result.get("r6_started") is not False
    ):
        add_finding(findings, "critical", "authority_boundary_violation", "The runner crossed an authority or data boundary.")

    counts = outcome_counts(observations)
    negative_transfer = sum(
        item.get("status") in {"verified_negative", "invalid_evidence"}
        for item in observations
        if isinstance(item, Mapping)
    )
    if counts["invalid_evidence"]:
        add_finding(findings, "critical", "invalid_evidence_observed", "Corrective replication produced invalid evidence.")
    if accepted_baselines == len(baselines) and counts["verified_positive"] == 0:
        add_finding(
            findings,
            "warning",
            "all_cases_resolved_by_common_baseline",
            "All corrected baselines were accepted, so no legal diagnostic entered a mutation arm.",
        )
    if counts["verified_positive"] == 0:
        add_finding(
            findings,
            "warning",
            "no_verified_corrective_memory_repair",
            "The posthoc replication cannot establish a memory repair effect.",
        )

    critical = sum(item["severity"] == "critical" for item in findings)
    status = "blocked" if critical else "clean_negative_no_memory_effect"
    audit_value: dict[str, Any] = {
        "schema_version": "r5-formal-public-contract-correction-audit-v1",
        "auditor": "r5-formal-public-contract-correction-file-only-auditor-v1",
        "status": status,
        "reason": (
            "critical_corrective_replication_finding"
            if critical
            else "public_contract_fix_verified_but_no_memory_effect_observed"
        ),
        "campaign_role": "posthoc_corrective_replication",
        "future_holdout_claim": False,
        "critical_finding_count": critical,
        "warning_finding_count": sum(item["severity"] == "warning" for item in findings),
        "findings": findings,
        "case_count": len(case_by_id),
        "repeat_count": 3,
        "baseline_count": len(baselines),
        "accepted_baseline_count": accepted_baselines,
        "arm_observation_count": len(observations),
        "outcome_counts": counts,
        "paired_endpoints": paired_endpoint_rows(observations),
        "negative_transfer_or_false_repair_count": negative_transfer,
        "baseline_provider_calls": baseline_provider,
        "arm_provider_calls": arm_provider,
        "total_provider_calls": total_provider,
        "baseline_vitis_launches": baseline_vitis,
        "arm_vitis_launches": arm_vitis,
        "total_vitis_launches": total_vitis,
        "provider_calls_after": result.get("provider_calls_after"),
        "vitis_launches_after": result.get("vitis_launches_after"),
        "hidden_input_count": 0,
        "cross_arm_cache_used": False,
        "external_provider_calls": 0,
        "external_vitis_launches": 0,
        "git_history_mutations": 0,
        "r5_accepted": False,
        "r6_started": False,
        "campaign_result_sha256": file_sha256(result_path),
        "correction_manifest_sha256": file_sha256(correction_path),
        "corrective_protocol_audit_sha256": file_sha256(protocol_path),
        "parent_campaign_result_sha256": file_sha256(parent_result_path),
        "parent_campaign_audit_sha256": file_sha256(parent_audit_path),
    }
    audit_value["audit_sha256"] = canonical_sha256(audit_value)
    return audit_value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence_root", type=Path)
    args = parser.parse_args()
    root = args.evidence_root.expanduser().resolve()
    audit_value = audit(root)
    output = root / "independent_audit.json"
    output.write_text(
        json.dumps(audit_value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("R5_CORRECTIVE_AUDIT_STATUS=" + str(audit_value["status"]))
    print("R5_CORRECTIVE_AUDIT_REASON=" + str(audit_value["reason"]))
    print("CRITICAL_FINDINGS=" + str(audit_value["critical_finding_count"]))
    print("WARNING_FINDINGS=" + str(audit_value["warning_finding_count"]))
    print("ACCEPTED_BASELINES=" + str(audit_value["accepted_baseline_count"]))
    print("VERIFIED_REPAIRS=" + str(audit_value["outcome_counts"]["verified_positive"]))
    print("ABSTENTIONS=" + str(audit_value["outcome_counts"]["abstained"]))
    print("INVALID_EVIDENCE=" + str(audit_value["outcome_counts"]["invalid_evidence"]))
    print("NEGATIVE_TRANSFER_OR_FALSE_REPAIR=" + str(audit_value["negative_transfer_or_false_repair_count"]))
    print("OBSERVED_PROVIDER_CALLS=" + str(audit_value["total_provider_calls"]))
    print("OBSERVED_VITIS_LAUNCHES=" + str(audit_value["total_vitis_launches"]))
    print("AUDITOR_PROVIDER_CALLS=0")
    print("AUDITOR_VITIS_LAUNCHES=0")
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return 0 if audit_value["critical_finding_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
