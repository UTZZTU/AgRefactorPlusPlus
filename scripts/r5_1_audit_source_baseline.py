#!/usr/bin/env python3
"""File-only audits for the R5.1 P4 source-baseline protocol and result."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from r5_1_source_baseline import (  # noqa: E402
    ALLOWED_CLASSIFICATIONS,
    canonical,
    classify_host_preflight,
    load_object,
    sha_file,
    sha_value,
    validate_plan,
)


def _finding(code: str, message: str, severity: str = "critical") -> dict[str, str]:
    return {"code": code, "message": message, "severity": severity}


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _result_hash_valid(value: Mapping[str, Any]) -> bool:
    expected = value.get("result_sha256")
    body = {key: item for key, item in value.items() if key != "result_sha256"}
    return isinstance(expected, str) and expected == sha_value(body)


def audit_protocol(plan: Mapping[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    try:
        validate_plan(plan)
    except Exception as exc:
        findings.append(_finding("protocol_invalid", f"{type(exc).__name__}: {exc}"))
    cases = plan.get("cases") if isinstance(plan.get("cases"), list) else []
    sources = {case.get("source_id") for case in cases if isinstance(case, Mapping)}
    families = {
        case.get("algorithm_family") for case in cases if isinstance(case, Mapping)
    }
    reserve = (
        plan.get("budget", {}).get("campaign_reserve", {})
        if isinstance(plan.get("budget"), Mapping)
        else {}
    )
    audit = {
        "schema_version": 1,
        "audit_id": "v2.3-r5.1-p4-source-baseline-protocol-audit-v1",
        "status": "passed" if not findings else "failed",
        "plan_sha256": sha_value(plan),
        "case_count": len(cases),
        "source_count": len(sources),
        "algorithm_family_count": len(families),
        "reserved_provider_calls": reserve.get("provider_calls"),
        "reserved_vitis_launches": reserve.get("vitis_launches"),
        "findings": findings,
        "critical_findings": sum(item["severity"] == "critical" for item in findings),
        "provider_calls": 0,
        "vitis_launches": 0,
        "git_history_mutations": 0,
        "r5_accepted": False,
        "r6_started": False,
    }
    audit["audit_sha256"] = sha_value(audit)
    return audit


def audit_preflight(
    plan: Mapping[str, Any],
    preflight: Mapping[str, Any],
) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    if not _result_hash_valid(preflight):
        findings.append(_finding("preflight_hash_invalid", "host preflight hash is invalid"))
    if preflight.get("plan_sha256") != sha_value(plan):
        findings.append(_finding("plan_identity_mismatch", "host preflight used another plan"))
    if preflight.get("provider_calls") != 0 or preflight.get("vitis_launches") != 0:
        findings.append(_finding("unexpected_external_calls", "host preflight crossed an external call boundary"))
    if preflight.get("git_history_mutations") != 0 or preflight.get("working_tree_mutations") != 0:
        findings.append(_finding("repository_mutation", "host preflight changed repository state"))
    cases = preflight.get("cases")
    if not isinstance(cases, list) or len(cases) != len(plan["cases"]):
        findings.append(_finding("case_count_mismatch", "host preflight case count is incomplete"))
        cases = []
    expected_ids = {case["case_id"] for case in plan["cases"]}
    planned_cases = {case["case_id"]: case for case in plan["cases"]}
    observed_ids = {case.get("case_id") for case in cases if isinstance(case, Mapping)}
    if expected_ids != observed_ids:
        findings.append(_finding("case_identity_mismatch", "host preflight case identities differ from the plan"))
    for case in cases:
        if not isinstance(case, Mapping) or case.get("case_id") not in planned_cases:
            findings.append(_finding("host_oracle_failed", "host oracle record is invalid"))
            continue
        case_id = case["case_id"]
        planned = planned_cases[case_id]
        passed, observed = classify_host_preflight(
            expected=planned["host_preflight_expected"],
            compile_returncode=case.get("compile_returncode"),
            run_returncode=case.get("run_returncode"),
            pass_marker_observed=case.get("pass_marker_observed") is True,
            mismatch_returncodes=set(
                planned["runtime_contract"]["candidate_mismatch_returncodes"]
            ),
        )
        if (
            not passed
            or case.get("status") != "passed"
            or case.get("expected_outcome") != planned["host_preflight_expected"]
            or case.get("observed_outcome") != observed
        ):
            findings.append(
                _finding(
                    "host_expectation_mismatch",
                    f"host observation does not match the frozen expectation: {case_id}",
                )
            )
        identity = case.get("material_identity")
        if not isinstance(identity, Mapping):
            findings.append(_finding("material_identity_missing", f"material identity missing: {case.get('case_id')}"))
            continue
        for field in ("design_sha256", "testbench_sha256"):
            value = identity.get(field)
            if not isinstance(value, str) or len(value) != 64:
                findings.append(_finding("material_identity_invalid", f"{field} invalid: {case.get('case_id')}"))
    audit = {
        "schema_version": 1,
        "audit_id": "v2.3-r5.1-p4-source-baseline-host-preflight-audit-v1",
        "status": "passed" if not findings else "failed",
        "plan_sha256": sha_value(plan),
        "preflight_result_sha256": preflight.get("result_sha256"),
        "case_count": len(cases),
        "findings": findings,
        "critical_findings": sum(item["severity"] == "critical" for item in findings),
        "provider_calls": 0,
        "vitis_launches": 0,
        "git_history_mutations": 0,
        "r5_accepted": False,
        "r6_started": False,
    }
    audit["audit_sha256"] = sha_value(audit)
    return audit


def _verify_inventory(
    result_root: Path,
    case: Mapping[str, Any],
    findings: list[dict[str, str]],
) -> None:
    inventory = case.get("evidence_inventory")
    if not isinstance(inventory, list) or not inventory:
        findings.append(_finding("evidence_inventory_missing", f"evidence inventory missing: {case.get('case_id')}"))
        return
    seen: set[str] = set()
    for record in inventory:
        if not isinstance(record, Mapping):
            findings.append(_finding("evidence_record_invalid", f"invalid evidence record: {case.get('case_id')}"))
            continue
        relative = record.get("path")
        if not isinstance(relative, str) or not relative or "\\" in relative:
            findings.append(_finding("evidence_path_invalid", f"invalid evidence path: {case.get('case_id')}"))
            continue
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or relative in seen:
            findings.append(_finding("evidence_path_escape", f"unsafe evidence path: {relative}"))
            continue
        seen.add(relative)
        resolved = result_root / path
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest() if resolved.is_file() and not resolved.is_symlink() else None
        if digest != record.get("sha256"):
            findings.append(_finding("evidence_hash_mismatch", f"evidence hash mismatch: {relative}"))


def audit_result(
    plan: Mapping[str, Any],
    preflight: Mapping[str, Any],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    if not _result_hash_valid(result):
        findings.append(_finding("result_hash_invalid", "source baseline result hash is invalid"))
    if result.get("status") != "complete":
        findings.append(_finding("campaign_incomplete", "source baseline did not complete"))
    if result.get("plan_sha256") != sha_value(plan):
        findings.append(_finding("plan_identity_mismatch", "result used another plan"))
    if result.get("preflight_result_sha256") != preflight.get("result_sha256"):
        findings.append(_finding("preflight_identity_mismatch", "result used another host preflight"))
    preflight_audit = audit_preflight(plan, preflight)
    if preflight_audit["critical_findings"] != 0:
        findings.append(_finding("preflight_contract_invalid", "result depends on an invalid host preflight"))
    if result.get("provider_calls") != 0:
        findings.append(_finding("provider_call_violation", "source baseline made Provider calls"))
    reserve = plan["budget"]["campaign_reserve"]["vitis_launches"]
    observed_vitis = result.get("vitis_launches")
    if isinstance(observed_vitis, bool) or not isinstance(observed_vitis, int) or observed_vitis < 0 or observed_vitis > reserve:
        findings.append(_finding("vitis_reserve_exceeded", "source baseline exceeded the audited Vitis reserve"))
    if result.get("git_history_mutations") != 0 or result.get("working_tree_mutations") != 0:
        findings.append(_finding("repository_mutation", "source baseline changed repository state"))
    if result.get("r5_accepted") is not False or result.get("r6_started") is not False:
        findings.append(_finding("acceptance_boundary_violation", "source baseline changed R5/R6 state"))

    cases = result.get("cases")
    if not isinstance(cases, list) or len(cases) != len(plan["cases"]):
        findings.append(_finding("case_count_mismatch", "source baseline case count is incomplete"))
        cases = []
    planned = {case["case_id"]: case for case in plan["cases"]}
    preflight_cases = {case["case_id"]: case for case in preflight["cases"]}
    usage_vitis = 0
    classifications: Counter[str] = Counter()
    result_root = Path(str(result.get("evidence_root", "")))
    for case in cases:
        if not isinstance(case, Mapping) or case.get("case_id") not in planned:
            findings.append(_finding("case_identity_mismatch", "result contains an unplanned case"))
            continue
        case_id = case["case_id"]
        if case.get("material_identity") != preflight_cases[case_id].get("material_identity"):
            findings.append(_finding("material_identity_mismatch", f"source material changed after preflight: {case_id}"))
        classification = case.get("classification")
        if classification not in ALLOWED_CLASSIFICATIONS:
            findings.append(_finding("classification_invalid", f"invalid classification: {case_id}"))
            continue
        classifications[classification] += 1
        stages = case.get("stage_statuses")
        if not isinstance(stages, Mapping) or stages.get("S0_host_oracle") != "passed":
            findings.append(_finding("stage_matrix_invalid", f"S0 host oracle missing: {case_id}"))
        terminal_stage = {
            "preflight": "S0_host_oracle",
            "public_evaluation": "S1_public_csim",
            "csynth": "S2_csynth",
            "public_cosim": "S3_public_cosim",
        }.get(case.get("terminal_state"))
        if (
            classification != "raw_pass_all"
            and terminal_stage is not None
            and (
                not isinstance(stages, Mapping)
                or stages.get(terminal_stage) != "failed"
            )
        ):
            findings.append(
                _finding(
                    "terminal_stage_claim_invalid",
                    f"terminal stage is not marked failed: {case_id}",
                )
            )
        if classification == "raw_pass_all" and (
            not isinstance(stages, Mapping)
            or any(stages.get(stage) != "passed" for stage in ("S1_public_csim", "S2_csynth", "S3_public_cosim"))
        ):
            findings.append(_finding("raw_pass_claim_invalid", f"raw_pass_all lacks a full prefix: {case_id}"))
        if classification in {"oracle_or_adapter_invalid", "infrastructure_failure"}:
            findings.append(_finding("case_excluded", f"{case_id}: {classification}", "warning"))
        usage = case.get("budget_usage")
        if not isinstance(usage, Mapping) or usage.get("llm_calls") != 0:
            findings.append(_finding("case_usage_invalid", f"invalid case usage: {case_id}"))
            continue
        usage_vitis += sum(int(usage.get(name, 0)) for name in ("csim_calls", "csynth_calls", "cosim_calls"))
        _verify_inventory(result_root, case, findings)
    if usage_vitis != observed_vitis:
        findings.append(_finding("vitis_count_mismatch", "case Vitis usage does not match the campaign total"))
    declared_counts = result.get("classification_counts")
    if declared_counts != dict(sorted(classifications.items())):
        findings.append(_finding("classification_count_mismatch", "classification summary is stale"))

    critical = sum(item["severity"] == "critical" for item in findings)
    warning = sum(item["severity"] == "warning" for item in findings)
    status = "failed" if critical else ("passed_with_exclusions" if warning else "passed")
    audit = {
        "schema_version": 1,
        "audit_id": "v2.3-r5.1-p4-source-baseline-result-audit-v1",
        "status": status,
        "plan_sha256": sha_value(plan),
        "source_result_sha256": result.get("result_sha256"),
        "case_count": len(cases),
        "classification_counts": dict(sorted(classifications.items())),
        "audited_provider_calls": result.get("provider_calls"),
        "audited_vitis_launches": observed_vitis,
        "findings": findings,
        "critical_findings": critical,
        "warning_findings": warning,
        "provider_calls": 0,
        "vitis_launches": 0,
        "git_history_mutations": 0,
        "r5_accepted": False,
        "r6_started": False,
    }
    audit["audit_sha256"] = sha_value(audit)
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    protocol = subparsers.add_parser("protocol")
    protocol.add_argument("--plan", type=Path, required=True)
    protocol.add_argument("--output", type=Path, required=True)
    preflight_cmd = subparsers.add_parser("preflight")
    preflight_cmd.add_argument("--plan", type=Path, required=True)
    preflight_cmd.add_argument("--preflight", type=Path, required=True)
    preflight_cmd.add_argument("--output", type=Path, required=True)
    result_cmd = subparsers.add_parser("result")
    result_cmd.add_argument("--plan", type=Path, required=True)
    result_cmd.add_argument("--preflight", type=Path, required=True)
    result_cmd.add_argument("--result", type=Path, required=True)
    result_cmd.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = load_object(args.plan)
    if args.command == "protocol":
        audit = audit_protocol(plan)
        label = "PROTOCOL"
    elif args.command == "preflight":
        audit = audit_preflight(plan, load_object(args.preflight))
        label = "HOST_PREFLIGHT"
    else:
        audit = audit_result(plan, load_object(args.preflight), load_object(args.result))
        label = "RESULT"
    _write_json(args.output, audit)
    print(f"R5_1_P4_{label}_AUDIT_STATUS={audit['status']}")
    print(f"R5_1_P4_{label}_AUDIT_CRITICAL={audit['critical_findings']}")
    print("AUDITOR_PROVIDER_CALLS=0")
    print("AUDITOR_VITIS_LAUNCHES=0")
    return 0 if audit["critical_findings"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
