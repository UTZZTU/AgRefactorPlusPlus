#!/usr/bin/env python3
"""Review HLSRewriter oracle adapters without fabricating benchmark evidence.

This is a zero-call design/audit tool. It records which existing benchmark
artifacts an adapter may expose and which contract facts still block admission
to an R5 campaign. It never writes benchmark files, creates expected outputs,
assigns history/future roles, or calls a Provider/Vitis.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


_SHA256_LENGTH = 64


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _load(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == _SHA256_LENGTH and all(
        char in "0123456789abcdef" for char in value
    )


def _audit_hash(audit: Mapping[str, Any]) -> str:
    stored = audit.get("audit_sha256")
    unsigned = {key: value for key, value in audit.items() if key != "audit_sha256"}
    if not _is_sha256(stored) or stored != _sha(unsigned):
        raise ValueError("audit_sha256 mismatch")
    return stored


def _file_refs(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    files = case.get("files")
    if not isinstance(files, Mapping):
        return refs
    for role in ("source", "reference", "legacy_testbench", "tcl"):
        record = files.get(role)
        if not isinstance(record, Mapping):
            continue
        path = record.get("path")
        sha256 = record.get("sha256")
        if isinstance(path, str) and _is_sha256(sha256) and record.get("present") is True:
            refs.append({"role": role, "path": path, "sha256": sha256})
    return refs


def _blockers(case: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    reasons = case.get("reasons")
    if isinstance(reasons, list):
        blockers.extend(str(item) for item in reasons)
    testbench = case.get("testbench")
    if not isinstance(testbench, Mapping):
        blockers.append("testbench_contract_missing")
        return sorted(set(blockers))
    if testbench.get("public_hidden_distinct") is not True:
        blockers.append("reviewed_public_hidden_pair_missing")
    if testbench.get("candidate_linked_by_tcl") is not True:
        blockers.append("candidate_linkage_not_proven")
    target = case.get("target")
    stages = target.get("stages", {}) if isinstance(target, Mapping) else {}
    if not isinstance(stages, Mapping) or not all(
        stages.get(stage) is True for stage in ("csim", "csynth", "cosim")
    ):
        blockers.append("complete_csim_csynth_cosim_prefix_missing")
    return_contract = testbench.get("return_contract")
    if not isinstance(return_contract, Mapping) or return_contract.get(
        "nonzero_failure_path_present"
    ) is not True:
        blockers.append("enforceable_process_failure_oracle_missing")
    includes = testbench.get("includes")
    if isinstance(includes, Mapping) and includes.get("includes_implementation") is True:
        blockers.append("testbench_implementation_include_must_be_removed_by_upstream_owner")
    dependencies = testbench.get("data_dependencies")
    if isinstance(dependencies, Mapping) and dependencies.get("missing_local"):
        blockers.append("external_test_data_not_reproducibly_present")
    return sorted(set(blockers))


def _adapter_record(case: Mapping[str, Any]) -> dict[str, Any]:
    case_id = case.get("case_id")
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError("adapter_required case has no case_id")
    if case.get("status") != "adapter_required":
        raise ValueError(f"adapter review received non-adapter case: {case_id}")
    blockers = _blockers(case)
    return {
        "case_id": case_id,
        "status": "blocked" if blockers else "ready_for_external_review",
        "history_or_future_role": "unassigned",
        "control_role": "unassigned",
        "existing_oracle_refs": _file_refs(case),
        "blockers": blockers,
        "permitted_adapter_actions": [
            "reference_existing_files_by_sha256",
            "expose_existing_public_or_hidden_oracle_without_mutation",
            "record_complete_target_toolchain_parser_and_provenance_identity",
        ],
        "forbidden_adapter_actions": [
            "invent_expected_output_or_failure_outcome",
            "copy_one_legacy_testbench_into_both_public_and_hidden_roles",
            "mutate_original_source_testbench_target_or_configuration",
            "assign_history_future_or_control_role_before_dataset_freeze",
            "create_trusted_memory_revision",
        ],
    }


def build_review(audit: Mapping[str, Any]) -> dict[str, Any]:
    audit_sha256 = _audit_hash(audit)
    cases = audit.get("cases")
    if not isinstance(cases, list):
        raise ValueError("audit.cases must be a list")
    adapter_cases = [
        case
        for case in cases
        if isinstance(case, Mapping) and case.get("status") == "adapter_required"
    ]
    rejected = [
        case.get("case_id")
        for case in cases
        if isinstance(case, Mapping) and case.get("status") == "rejected"
    ]
    direct = [
        case.get("case_id")
        for case in cases
        if isinstance(case, Mapping) and case.get("status") == "eligible_direct"
    ]
    records = [_adapter_record(case) for case in adapter_cases]
    status = (
        "ready_for_external_review"
        if records and all(item["status"] != "blocked" for item in records)
        else "blocked_data_acquisition"
    )
    result = {
        "schema_version": 1,
        "review_id": "v2.3-r5-reviewed-oracle-adapter-design-v1",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_audit_sha256": audit_sha256,
        "status": status,
        "adapter_required_count": len(records),
        "eligible_direct_count": len(direct),
        "rejected_count": len(rejected),
        "adapters": records,
        "rejected_case_ids": sorted(str(item) for item in rejected if isinstance(item, str)),
        "direct_case_ids": sorted(str(item) for item in direct if isinstance(item, str)),
        "history_future_split_frozen": False,
        "source_holdout_verified": False,
        "positive_and_inapplicable_controls_verified": False,
        "trusted_revision_creation_allowed": False,
        "real_campaign_allowed": False,
        "provider_calls": 0,
        "vitis_launches": 0,
        "git_history_mutations": 0,
        "reasons": [
            "adapter review does not create Public/Hidden oracles",
            "adapter review does not assign history/future or control roles",
            "campaign remains blocked until every selected case has a reviewed independent oracle pair",
        ],
    }
    result["review_sha256"] = _sha(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_review(_load(args.audit))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"R5_ORACLE_ADAPTER_REVIEW_STATUS={result['status']}")
    print(f"ADAPTER_REQUIRED={result['adapter_required_count']}")
    print(f"ELIGIBLE_DIRECT={result['eligible_direct_count']}")
    print(f"REJECTED={result['rejected_count']}")
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    print("GIT_HISTORY_MUTATIONS=0")
    print("R5_REAL_CAMPAIGN_ALLOWED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
