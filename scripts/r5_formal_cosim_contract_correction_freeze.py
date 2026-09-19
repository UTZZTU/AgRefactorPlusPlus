#!/usr/bin/env python3
"""Freeze the zero-call R5 formal COSIM-contract corrective replication."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "configs/r5/formal_campaign/cosim_contract_correction_plan.json"
STATE = ROOT / "docs/roadmap/V2_3_STATE.json"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def git(*args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def run(output: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    if output.exists():
        raise RuntimeError(f"output directory already exists: {output}")
    plan = load(PLAN)
    state = load(STATE)
    parent = plan["parent_evidence"]
    parent_manifest_path = Path(str(parent["frozen_manifest"]))
    parent_result_path = Path(str(parent["formal_campaign_result"]))
    parent_audit_path = Path(str(parent["independent_audit"]))
    parent_manifest = load(parent_manifest_path)
    parent_result = load(parent_result_path)
    parent_audit = load(parent_audit_path)

    if plan.get("status") != "predeclared_before_corrective_replication":
        raise RuntimeError("correction plan is not frozen")
    if parent_result.get("status") != "ready_for_independent_audit":
        raise RuntimeError("parent formal campaign is not complete")
    if parent_audit.get("status") != "clean_inconclusive_no_verified_future_repair":
        raise RuntimeError("parent audit does not authorize a corrective replication")
    if parent_audit.get("critical_finding_count") != 0:
        raise RuntimeError("parent formal campaign has a critical finding")
    if file_sha256(parent_result_path) != parent["formal_campaign_result_file_sha256"]:
        raise RuntimeError("parent result file hash mismatch")
    if file_sha256(parent_audit_path) != parent["independent_audit_file_sha256"]:
        raise RuntimeError("parent audit file hash mismatch")
    if git("status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("correction freeze requires a clean worktree")
    if git("branch", "--show-current") != "research-roadmap-v2.3":
        raise RuntimeError("correction freeze requires the R5 branch")
    if state.get("R5_ACCEPTED") is not False or state.get("R6_STARTED") is not False:
        raise RuntimeError("R5/R6 authority boundary changed")

    budget = dict(plan["budget"])
    provider_before = int(budget["provider_calls_before_parent"]) + int(budget["parent_provider_calls"])
    vitis_before = int(budget["vitis_launches_before_parent"]) + int(budget["parent_vitis_launches"])
    if parent_result.get("provider_calls") != budget["parent_provider_calls"]:
        raise RuntimeError("parent Provider usage mismatch")
    if parent_result.get("vitis_launches") != budget["parent_vitis_launches"]:
        raise RuntimeError("parent Vitis usage mismatch")
    if provider_before + int(budget["provider_upper_bound"]) > int(budget["provider_hard_cap"]):
        raise RuntimeError("corrective Provider reservation exceeds the hard cap")
    if vitis_before + int(budget["vitis_upper_bound"]) > int(budget["vitis_hard_cap"]):
        raise RuntimeError("corrective Vitis reservation exceeds the hard cap")

    case_by_id = {
        str(item["case_id"]): item
        for item in parent_manifest.get("future_cases", ())
        if isinstance(item, Mapping)
    }
    contracts: list[dict[str, Any]] = []
    for case_id, relative in sorted(plan["case_contracts"].items()):
        if case_id not in case_by_id:
            raise RuntimeError(f"correction references unknown case: {case_id}")
        path = (ROOT / str(relative)).resolve()
        value = load(path)
        depths = value.get("cosim_interface_depths")
        case = case_by_id[case_id]
        if (
            value.get("schema_version") != 2
            or value.get("kind") != "public_differential_self_check_v1"
            or value.get("candidate_mismatch_returncodes") != [1]
            or not isinstance(depths, Mapping)
            or not depths
            or any(not isinstance(name, str) or not isinstance(depth, int) or depth <= 0 for name, depth in depths.items())
        ):
            raise RuntimeError(f"invalid Public runtime contract: {case_id}")
        public_test = ROOT / str(case["paths"]["public_test"])
        public_text = public_test.read_text(encoding="utf-8")
        candidate_top = str(case["candidate_top"])
        declaration_match = re.search(
            rf"^[^#\n]*\b{re.escape(candidate_top)}\s*\([^;{{}}]*\)\s*;",
            public_text,
            flags=re.MULTILINE,
        )
        if declaration_match is None:
            raise RuntimeError(
                f"Public test lacks Candidate declaration: {case_id}"
            )
        declaration = declaration_match.group(0)
        missing_ports = [
            name
            for name in depths
            if not re.search(rf"\b{re.escape(name)}\b", declaration)
        ]
        if missing_ports:
            raise RuntimeError(
                f"COSIM depth ports are absent from Public ABI for {case_id}: "
                + ",".join(sorted(missing_ports))
            )
        contracts.append(
            {
                "case_id": case_id,
                "path": str(Path(relative)),
                "file_sha256": file_sha256(path),
                "runtime_contract": value,
                "source_sha256": case_by_id[case_id]["hashes"]["source"],
                "public_test_sha256": case_by_id[case_id]["hashes"]["public_test"],
            }
        )

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "manifest_id": "v2.3-r5-formal-public-contract-correction-v2",
        "status": "frozen_ready_for_corrective_replication",
        "repository_commit": git("rev-parse", "HEAD"),
        "branch": "research-roadmap-v2.3",
        "plan_file_sha256": file_sha256(PLAN),
        "parent_manifest_file_sha256": file_sha256(parent_manifest_path),
        "parent_manifest_sha256": parent_manifest["manifest_sha256"],
        "parent_result_file_sha256": file_sha256(parent_result_path),
        "parent_audit_file_sha256": file_sha256(parent_audit_path),
        "case_contracts": contracts,
        "case_ids": sorted(case_by_id),
        "repeats": 3,
        "arms": [f"A{index}" for index in range(7)],
        "budget": {
            "provider_calls_before": provider_before,
            "vitis_launches_before": vitis_before,
            "provider_upper_bound": budget["provider_upper_bound"],
            "vitis_upper_bound": budget["vitis_upper_bound"],
            "provider_hard_cap": budget["provider_hard_cap"],
            "vitis_hard_cap": budget["vitis_hard_cap"],
        },
        "claim_limits": plan["claim_limits"],
        "invariants": plan["invariants"],
        "provider_calls": 0,
        "vitis_launches": 0,
        "git_history_mutations": 0,
        "r5_accepted": False,
        "r6_started": False,
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    audit: dict[str, Any] = {
        "schema_version": 1,
        "auditor": "r5-formal-public-contract-correction-protocol-auditor-v2",
        "status": "clean_ready_for_corrective_replication",
        "reason": "parent_preserved_public_abi_and_runtime_contracts_verified",
        "critical_finding_count": 0,
        "manifest_sha256": manifest["manifest_sha256"],
        "parent_campaign_preserved": True,
        "public_candidate_abi_bound": True,
        "runtime_depth_ports_bound_to_public_abi": True,
        "posthoc_corrective_replication": True,
        "new_future_holdout_claim": False,
        "sample_specific_error_rule_added": False,
        "provider_calls": 0,
        "vitis_launches": 0,
        "git_history_mutations": 0,
        "r5_accepted": False,
        "r6_started": False,
    }
    audit["audit_sha256"] = canonical_sha256(audit)
    write_json(output / "correction_manifest.json", manifest)
    write_json(output / "independent_protocol_audit.json", audit)
    return manifest, audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest, audit = run(args.output.expanduser().resolve())
    print("R5_FORMAL_COSIM_CORRECTION_FREEZE_STATUS=" + audit["status"])
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    print("GIT_HISTORY_MUTATIONS=0")
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
