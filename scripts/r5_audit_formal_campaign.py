#!/usr/bin/env python3
"""Independent file-only audit for the frozen R5 formal A0-A6 campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "docs/roadmap/V2_3_STATE.json"
FREEZE_ROOT = Path("/data/agrefactor_runs/r5_p6_formal_future_freeze_preflight")
ARMS = ("A0", "A1", "A2", "A3", "A4", "A5", "A6")
ALLOWED_STATUSES = {
    "baseline_accepted",
    "verified_positive",
    "verified_negative",
    "abstained",
    "inconclusive",
    "invalid_evidence",
}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def unsafe_persisted_content(value: Any) -> bool:
    forbidden = {
        "private_reasoning",
        "provider_response",
        "raw_provider_response",
        "reasoning_content",
        "response_content",
        "secret_value",
    }
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key).casefold()
            if key in forbidden:
                return True
            if key.endswith("_persisted") and child not in (False, "false"):
                return True
            if unsafe_persisted_content(child):
                return True
    elif isinstance(value, (list, tuple)):
        return any(unsafe_persisted_content(child) for child in value)
    elif isinstance(value, str):
        lowered = value.casefold()
        return any(
            tag in lowered
            for tag in ("<think", "</think", "<reasoning", "</reasoning")
        )
    return False


def observation_counts(observations: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(str(item.get("status", "unknown")) for item in observations)
    return {
        "baseline_accepted": counts["baseline_accepted"],
        "verified_positive": counts["verified_positive"],
        "verified_negative": counts["verified_negative"],
        "abstained": counts["abstained"],
        "inconclusive": counts["inconclusive"],
        "invalid_evidence": counts["invalid_evidence"],
        "unknown": sum(
            count for status, count in counts.items() if status not in ALLOWED_STATUSES
        ),
    }


def paired_endpoint_rows(
    observations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int], dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for item in observations:
        grouped[(str(item.get("case_id", "")), int(item.get("repeat", 0)))][
            str(item.get("arm", ""))
        ] = item
    rows: list[dict[str, Any]] = []
    for (case_id, repeat), by_arm in sorted(grouped.items()):
        baseline = by_arm.get("A0", {})
        baseline_positive = int(baseline.get("status") == "verified_positive")
        baseline_false = int(
            baseline.get("status") in {"verified_negative", "invalid_evidence"}
        )
        for arm in ARMS[1:]:
            item = by_arm.get(arm, {})
            rows.append(
                {
                    "case_id": case_id,
                    "repeat": repeat,
                    "arm": arm,
                    "paired_verified_repair_difference": int(
                        item.get("status") == "verified_positive"
                    )
                    - baseline_positive,
                    "paired_false_repair_or_negative_transfer_difference": int(
                        item.get("status")
                        in {"verified_negative", "invalid_evidence"}
                    )
                    - baseline_false,
                }
            )
    return rows


def add_finding(
    findings: list[dict[str, str]],
    severity: str,
    code: str,
    message: str,
) -> None:
    findings.append({"severity": severity, "code": code, "message": message})


def audit(root: Path) -> dict[str, Any]:
    result_path = root / "formal_campaign_result.json"
    manifest_path = FREEZE_ROOT / "formal_future_manifest.json"
    protocol_path = FREEZE_ROOT / "independent_protocol_audit.json"
    result = load(result_path)
    manifest = load(manifest_path)
    protocol = load(protocol_path)
    state = load(STATE)
    findings: list[dict[str, str]] = []

    unsigned_result = dict(result)
    stored_result_sha = unsigned_result.pop("result_sha256", None)
    if stored_result_sha != canonical_sha256(unsigned_result):
        add_finding(
            findings,
            "critical",
            "result_hash_invalid",
            "The formal campaign result canonical hash does not verify.",
        )
    if result.get("status") != "ready_for_independent_audit":
        add_finding(
            findings,
            "critical",
            "campaign_not_auditable",
            "The campaign did not reach the independent-audit boundary.",
        )
    if (
        manifest.get("status") != "frozen_ready_for_formal_campaign"
        or protocol.get("status") != "clean_ready_for_formal_campaign"
    ):
        add_finding(
            findings,
            "critical",
            "freeze_contract_not_clean",
            "The pre-outcome future contract or protocol audit is not clean.",
        )
    if result.get("manifest_sha256") != manifest.get("manifest_sha256"):
        add_finding(
            findings,
            "critical",
            "manifest_binding_mismatch",
            "The campaign result is not bound to the frozen future manifest.",
        )
    if protocol.get("manifest_sha256") != manifest.get("manifest_sha256"):
        add_finding(
            findings,
            "critical",
            "protocol_manifest_mismatch",
            "The pre-outcome protocol audit is not bound to the frozen manifest.",
        )
    if result.get("repository_commit") != manifest.get("repository_commit"):
        add_finding(
            findings,
            "critical",
            "repository_commit_mismatch",
            "The campaign repository commit differs from the frozen commit.",
        )
    if manifest.get("repository_commit") != state.get("head"):
        add_finding(
            findings,
            "warning",
            "state_head_advanced_after_campaign",
            "The roadmap head advanced after the frozen campaign commit.",
        )

    cases = manifest.get("future_cases")
    if not isinstance(cases, list) or len(cases) != 2:
        cases = []
        add_finding(
            findings,
            "critical",
            "future_case_inventory_invalid",
            "The frozen campaign must contain two future cases.",
        )
    case_by_id = {
        str(item.get("case_id")): item
        for item in cases
        if isinstance(item, Mapping)
    }
    expected_case_ids = tuple(sorted(case_by_id))
    if tuple(sorted(str(item) for item in result.get("case_ids", ()))) != expected_case_ids:
        add_finding(
            findings,
            "critical",
            "case_inventory_mismatch",
            "Executed cases differ from the frozen future cases.",
        )
    if result.get("repeats") != 3:
        add_finding(
            findings,
            "critical",
            "repeat_count_mismatch",
            "The formal campaign must retain all three repeats.",
        )

    baselines = result.get("baselines")
    observations = result.get("observations")
    if not isinstance(baselines, list):
        baselines = []
    if not isinstance(observations, list):
        observations = []
    if len(baselines) != len(case_by_id) * 3:
        add_finding(
            findings,
            "critical",
            "baseline_count_mismatch",
            "Expected one common baseline per case and repeat.",
        )
    if len(observations) != len(case_by_id) * 3 * len(ARMS):
        add_finding(
            findings,
            "critical",
            "arm_observation_count_mismatch",
            "Expected all A0-A6 observations for every case and repeat.",
        )

    baseline_ids: set[str] = set()
    baseline_provider = baseline_vitis = 0
    evidence_hashes: dict[str, str] = {
        "formal_campaign_result.json": file_sha256(result_path),
        "formal_future_manifest.json": file_sha256(manifest_path),
        "independent_protocol_audit.json": file_sha256(protocol_path),
    }
    for item in baselines:
        if not isinstance(item, Mapping):
            add_finding(findings, "critical", "baseline_not_object", "A baseline is malformed.")
            continue
        baseline_id = item.get("baseline_id")
        case_id = str(item.get("run_id", "")).split("-baseline", 1)[0]
        if not isinstance(baseline_id, str) or baseline_id in baseline_ids:
            add_finding(
                findings,
                "critical",
                "baseline_identity_not_unique",
                "A common baseline identifier is missing or duplicated.",
            )
        else:
            baseline_ids.add(baseline_id)
        provider = item.get("provider_calls")
        vitis = item.get("vitis_launches")
        if not isinstance(provider, int) or not isinstance(vitis, int):
            add_finding(
                findings,
                "critical",
                "baseline_usage_malformed",
                f"Baseline usage is malformed: {case_id}",
            )
        else:
            baseline_provider += provider
            baseline_vitis += vitis
        if item.get("hidden_input_count") != 0 or item.get("cross_arm_cache_used") is not False:
            add_finding(
                findings,
                "critical",
                "baseline_boundary_violation",
                f"Hidden input or cross-arm cache entered baseline {baseline_id}.",
            )
        if unsafe_persisted_content(item):
            add_finding(
                findings,
                "critical",
                "unsafe_baseline_content",
                f"Unsafe content was persisted in baseline {baseline_id}.",
            )

    coverage: dict[tuple[str, int], set[str]] = defaultdict(set)
    arm_provider = arm_vitis = 0
    for item in observations:
        if not isinstance(item, Mapping):
            add_finding(findings, "critical", "observation_not_object", "An arm result is malformed.")
            continue
        case_id = str(item.get("case_id", ""))
        repeat = item.get("repeat")
        arm = str(item.get("arm", ""))
        key = (case_id, repeat if isinstance(repeat, int) else -1)
        if case_id not in case_by_id or repeat not in {1, 2, 3} or arm not in ARMS:
            add_finding(
                findings,
                "critical",
                "observation_identity_invalid",
                f"Invalid observation identity: {case_id}/{repeat}/{arm}.",
            )
        if arm in coverage[key]:
            add_finding(
                findings,
                "critical",
                "duplicate_arm_observation",
                f"Duplicate observation: {case_id}/{repeat}/{arm}.",
            )
        coverage[key].add(arm)
        if item.get("baseline_id") not in baseline_ids:
            add_finding(
                findings,
                "critical",
                "observation_baseline_mismatch",
                f"Arm {case_id}/{repeat}/{arm} references an unknown baseline.",
            )
        if item.get("source_sha256") != case_by_id.get(case_id, {}).get("hashes", {}).get("source"):
            add_finding(
                findings,
                "critical",
                "observation_source_mismatch",
                f"Arm {case_id}/{repeat}/{arm} crossed the source holdout.",
            )
        if item.get("hidden_input_count") != 0 or item.get("cross_arm_cache_used") is not False:
            add_finding(
                findings,
                "critical",
                "observation_boundary_violation",
                f"Hidden input or cross-arm cache entered {case_id}/{repeat}/{arm}.",
            )
        provider = item.get("provider_calls")
        vitis = item.get("vitis_launches")
        if not isinstance(provider, int) or not isinstance(vitis, int):
            add_finding(
                findings,
                "critical",
                "observation_usage_malformed",
                f"Usage is malformed for {case_id}/{repeat}/{arm}.",
            )
        else:
            arm_provider += provider
            arm_vitis += vitis
            if arm == "A0" and (provider or vitis):
                add_finding(findings, "critical", "a0_external_work", "A0 performed extra work.")
            if arm == "A1" and vitis:
                add_finding(findings, "critical", "a1_vitis_work", "A1 launched Vitis.")
            if provider > 2 or vitis > 4:
                add_finding(
                    findings,
                    "critical",
                    "arm_usage_bound_violation",
                    f"Arm-local usage exceeded the frozen bound for {case_id}/{repeat}/{arm}.",
                )
        status = str(item.get("status", "unknown"))
        if status not in ALLOWED_STATUSES:
            add_finding(
                findings,
                "critical",
                "unknown_outcome_status",
                f"Unknown status {status!r} for {case_id}/{repeat}/{arm}.",
            )
        if status == "verified_positive" and item.get("verified_repair") is not True:
            add_finding(
                findings,
                "critical",
                "verified_positive_flag_mismatch",
                f"Verified-positive flag mismatch for {case_id}/{repeat}/{arm}.",
            )
        if unsafe_persisted_content(item):
            add_finding(
                findings,
                "critical",
                "unsafe_observation_content",
                f"Unsafe content was persisted in {case_id}/{repeat}/{arm}.",
            )

    expected_arms = set(ARMS)
    for case_id in expected_case_ids:
        for repeat in range(1, 4):
            if coverage.get((case_id, repeat), set()) != expected_arms:
                add_finding(
                    findings,
                    "critical",
                    "arm_coverage_incomplete",
                    f"A0-A6 coverage is incomplete for {case_id}/repeat-{repeat:02d}.",
                )

    total_provider = baseline_provider + arm_provider
    total_vitis = baseline_vitis + arm_vitis
    if total_provider != result.get("provider_calls"):
        add_finding(findings, "critical", "provider_total_mismatch", "Provider usage does not reconcile.")
    if total_vitis != result.get("vitis_launches"):
        add_finding(findings, "critical", "vitis_total_mismatch", "Vitis usage does not reconcile.")
    ledger = manifest.get("budget", {}).get("ledger", {})
    if result.get("provider_calls_after") != ledger.get("provider_used", -1) + total_provider:
        add_finding(findings, "critical", "provider_ledger_mismatch", "Provider ledger does not reconcile.")
    if result.get("vitis_launches_after") != ledger.get("vitis_used", -1) + total_vitis:
        add_finding(findings, "critical", "vitis_ledger_mismatch", "Vitis ledger does not reconcile.")
    if total_provider > int(result.get("provider_upper_bound", -1)):
        add_finding(findings, "critical", "provider_bound_violation", "Provider upper bound was exceeded.")
    if total_vitis > int(result.get("vitis_upper_bound", -1)):
        add_finding(findings, "critical", "vitis_bound_violation", "Vitis upper bound was exceeded.")
    if (
        result.get("future_outcomes_observed") is not True
        or result.get("future_files_executed") is not True
        or result.get("hidden_input_count") != 0
        or result.get("cross_arm_cache_used") is not False
    ):
        add_finding(findings, "critical", "campaign_boundary_violation", "A frozen execution boundary failed.")
    if (
        result.get("git_history_mutations") != 0
        or result.get("r5_accepted") is not False
        or result.get("r6_started") is not False
    ):
        add_finding(findings, "critical", "authority_boundary_violation", "The runner crossed its authority boundary.")

    counts = observation_counts(observations)
    if counts["invalid_evidence"]:
        add_finding(findings, "critical", "invalid_evidence_observed", "Formal campaign produced invalid evidence.")
    if counts["verified_positive"] == 0:
        add_finding(
            findings,
            "warning",
            "no_verified_future_repair",
            "The formal future campaign established no verified repair effect.",
        )
    positive_ids = {
        str(item.get("case_id"))
        for item in cases
        if isinstance(item, Mapping) and item.get("control_role") == "positive"
    }
    if not any(
        item.get("case_id") in positive_ids and item.get("status") == "verified_positive"
        for item in observations
        if isinstance(item, Mapping)
    ):
        add_finding(
            findings,
            "warning",
            "positive_control_not_realized",
            "The predeclared positive future case never yielded a verified repair.",
        )

    endpoint_rows = paired_endpoint_rows(observations)
    critical = sum(item["severity"] == "critical" for item in findings)
    result_status = (
        "blocked" if critical else (
            "clean_inconclusive_no_verified_future_repair"
            if counts["verified_positive"] == 0
            else "clean"
        )
    )
    audit_value = {
        "schema_version": "r5-formal-campaign-independent-audit-v1",
        "auditor": "r5-formal-campaign-file-only-auditor-v1",
        "status": result_status,
        "reason": (
            "critical_formal_campaign_finding"
            if critical
            else (
                "boundaries_clean_but_no_verified_future_repair"
                if counts["verified_positive"] == 0
                else "formal_campaign_boundaries_and_outcomes_verified"
            )
        ),
        "critical_finding_count": critical,
        "warning_finding_count": sum(item["severity"] == "warning" for item in findings),
        "findings": findings,
        "case_count": len(case_by_id),
        "repeat_count": 3,
        "baseline_count": len(baselines),
        "arm_observation_count": len(observations),
        "outcome_counts": counts,
        "paired_endpoints": endpoint_rows,
        "negative_transfer_or_false_repair_count": sum(
            item.get("status") in {"verified_negative", "invalid_evidence"}
            for item in observations
            if isinstance(item, Mapping)
        ),
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
        "future_files_read_by_auditor": False,
        "external_provider_calls": 0,
        "external_vitis_launches": 0,
        "git_history_mutations": 0,
        "r5_accepted": False,
        "r6_started": False,
        "campaign_result_sha256": file_sha256(result_path),
        "frozen_manifest_sha256": file_sha256(manifest_path),
        "frozen_protocol_audit_sha256": file_sha256(protocol_path),
        "evidence_file_sha256": evidence_hashes,
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
    print("R5_FORMAL_CAMPAIGN_AUDIT_STATUS=" + str(audit_value["status"]))
    print("R5_FORMAL_CAMPAIGN_AUDIT_REASON=" + str(audit_value["reason"]))
    print("CRITICAL_FINDINGS=" + str(audit_value["critical_finding_count"]))
    print("WARNING_FINDINGS=" + str(audit_value["warning_finding_count"]))
    print("VERIFIED_REPAIRS=" + str(audit_value["outcome_counts"]["verified_positive"]))
    print("ABSTENTIONS=" + str(audit_value["outcome_counts"]["abstained"]))
    print("INCONCLUSIVE=" + str(audit_value["outcome_counts"]["inconclusive"]))
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
