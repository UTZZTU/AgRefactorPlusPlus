#!/usr/bin/env python3
"""Zero-call independent consistency audit for the R5.1 scope amendment."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "configs" / "r5_1" / "r5_1_pre_r6_scope_protocol_v2.json"
R5_GATE = ROOT / "configs" / "r5_1" / "r5_acceptance_gate_v2.json"
R6_GATE = ROOT / "configs" / "r5_1" / "r6_entry_gate_v2.json"
STATE = ROOT / "docs" / "roadmap" / "V2_3_STATE.json"
INDEX = ROOT / "docs" / "roadmap" / "V2_3_AUTHORITY_INDEX.json"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _finding(findings: list[dict[str, str]], code: str, message: str) -> None:
    findings.append({"severity": "critical", "code": code, "message": message})


def validate_contracts(
    protocol: Mapping[str, Any],
    r5_gate: Mapping[str, Any],
    r6_gate: Mapping[str, Any],
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if protocol.get("schema_version") != 2 or protocol.get("protocol_id") != "v2.3-r5.1-pre-r6-scope-v2":
        _finding(findings, "protocol_identity_invalid", "scope protocol v2 identity changed")
    supersession = protocol.get("supersession", {})
    if (
        supersession.get("prospective_only") is not True
        or supersession.get("previous_document_rewritten") is not False
        or supersession.get("previous_scope_file_sha256")
        != "11635a6671720ac22e7ca289a09249f28f434364889429104fe70e3dd281c11b"
    ):
        _finding(findings, "supersession_boundary_invalid", "historical route is not immutably preserved")
    checkpoint = protocol.get("effective_after_checkpoint", {})
    if (
        checkpoint.get("p5_preserved") is not True
        or checkpoint.get("p0_p5_evidence_rewritten") is not False
        or checkpoint.get("p0_p5_reexecution_required") is not False
    ):
        _finding(findings, "p5_boundary_invalid", "P5 preservation boundary changed")
    p8 = protocol.get("p8_mandatory_minimum", {})
    if p8.get("levels") != ["O0_Captured", "O1_Observed"]:
        _finding(findings, "p8_levels_invalid", "only O0/O1 may be mandatory")
    required_forbidden = {
        "product_prompt",
        "applicability_gate",
        "r4_mutation_authorization",
        "r5_mutation_authorization",
        "repair_pattern_lifecycle",
    }
    if set(p8.get("forbidden_consumers", [])) != required_forbidden:
        _finding(findings, "observation_consumer_boundary_invalid", "O0/O1 consumer exclusions changed")
    if p8.get("product_prompt_injection_default") is not False:
        _finding(findings, "observation_prompt_enabled", "O0/O1 prompt injection must remain disabled")
    deferred = protocol.get("deferred_extensions", {})
    if (
        deferred.get("owner_approval_required") is not True
        or deferred.get("current_budget_allowed") is not False
        or deferred.get("implementation_required_for_r5_acceptance") is not False
        or deferred.get("implementation_required_for_r6_entry") is not False
    ):
        _finding(findings, "deferred_scope_invalid", "post-R6 extension boundary changed")
    p9 = protocol.get("p9_primary_matrix", {})
    if p9.get("required_arms") != {
        "S": "raw_source_baseline",
        "B": "ordinary_refactor_R2_R5_off",
        "D": "R2_R4_on_memory_none",
        "M-R": "trusted_repair_memory",
    }:
        _finding(findings, "p9_primary_matrix_invalid", "P9 required arms must be S/B/D/M-R")
    if p9.get("optional_arms") != {"M-O": "qualified_observation_memory_post_R6_extension"}:
        _finding(findings, "p9_optional_matrix_invalid", "M-O must be optional and isolated")
    if p9.get("m_o_current_budget_allowed") is not False or p9.get("m_o_primary_efficacy_claim_allowed") is not False:
        _finding(findings, "m_o_authority_invalid", "M-O cannot consume current budget or support the primary claim")
    products = protocol.get("product_boundaries", {})
    if products.get("formal_entrypoints") != ["refactor", "optimize", "full"]:
        _finding(findings, "entrypoints_invalid", "formal entrypoints changed")
    for key in (
        "new_cli_allowed",
        "second_candidate_repair_allowed",
        "second_vitis_runner_allowed",
        "second_validation_or_success_authority_allowed",
        "r2_r5_default_enabled",
    ):
        if products.get(key) is not False:
            _finding(findings, "product_boundary_invalid", f"product boundary changed: {key}")
    budget = protocol.get("budget_at_effective_boundary", {})
    if budget != {
        "provider_hard_cap": 650,
        "vitis_hard_cap": 650,
        "cumulative_provider_calls": 512,
        "cumulative_vitis_launches": 232,
        "remaining_provider_calls": 138,
        "remaining_vitis_launches": 418,
        "scope_amendment_provider_calls": 0,
        "scope_amendment_vitis_launches": 0,
    }:
        _finding(findings, "budget_boundary_invalid", "scope-boundary budget accounting changed")
    if r5_gate.get("gate_id") != "v2.3-r5.1-acceptance-gate-v2":
        _finding(findings, "r5_gate_identity_invalid", "R5 gate v2 identity changed")
    observation_gate = r5_gate.get("observation_governance_requirements", {})
    for key in (
        "prompt_consumption_allowed",
        "gate_consumption_allowed",
        "mutation_authorization_allowed",
        "repair_lifecycle_promotion_allowed",
        "verified_positive_credit_allowed",
        "memory_efficacy_credit_allowed",
        "future_hidden_leakage_allowed",
        "retroactive_relabel_allowed",
    ):
        if observation_gate.get(key) is not False:
            _finding(findings, "r5_observation_gate_invalid", f"R5 observation authority enabled: {key}")
    if r6_gate.get("gate_id") != "v2.3-r6-entry-gate-v2" or r6_gate.get("status") != "closed":
        _finding(findings, "r6_gate_identity_invalid", "R6 entry gate must remain closed")
    if set(r5_gate.get("deferred_non_blockers", [])) != set(r6_gate.get("not_required_for_entry", [])):
        _finding(findings, "deferred_gate_mismatch", "R5 and R6 deferred scope differs")
    if r5_gate.get("current_state", {}).get("r5_accepted") is not False:
        _finding(findings, "r5_self_accepted", "R5 cannot be accepted by the amendment")
    if r6_gate.get("current_state", {}).get("r6_started") is not False:
        _finding(findings, "r6_started", "R6 cannot start through the amendment")
    return findings


def audit(repo: Path) -> dict[str, Any]:
    protocol = load_object(repo / PROTOCOL.relative_to(ROOT))
    r5_gate = load_object(repo / R5_GATE.relative_to(ROOT))
    r6_gate = load_object(repo / R6_GATE.relative_to(ROOT))
    state = load_object(repo / STATE.relative_to(ROOT))
    authority = load_object(repo / INDEX.relative_to(ROOT))
    findings = validate_contracts(protocol, r5_gate, r6_gate)

    expected_files = {
        "docs/roadmap/V2_3_PRE_R6_ROBUSTNESS_AND_EVIDENCE_PLAN.md":
            "11635a6671720ac22e7ca289a09249f28f434364889429104fe70e3dd281c11b",
        "docs/roadmap/R5_1_P5_ORDINARY_REFACTOR_CHECKPOINT.json":
            "6bcfbc94c51a0feee8d832810702ada66d05848daa3b028eddaaa334849e1ac0",
    }
    for relative, expected in expected_files.items():
        path = repo / relative
        if not path.is_file() or file_sha256(path) != expected:
            _finding(findings, "historical_file_changed", relative)

    checkpoint = load_object(repo / "docs/roadmap/R5_1_P5_ORDINARY_REFACTOR_CHECKPOINT.json")
    external_hashes = {
        Path(checkpoint["campaign"]["evidence_root"]) / "result.json": checkpoint["campaign"]["result_file_sha256"],
        Path(checkpoint["campaign"]["evidence_root"]) / "independent_audit.json": checkpoint["independent_audit"]["file_sha256"],
        Path(checkpoint["campaign"]["evidence_root"]) / "checkpoint_manifest.sha256": checkpoint["campaign"]["checkpoint_manifest_file_sha256"],
        Path(checkpoint["campaign"]["checkpoint_archive"]): checkpoint["campaign"]["checkpoint_archive_sha256"],
        Path(checkpoint["campaign"]["protocol_audit"]): checkpoint["campaign"]["protocol_audit_file_sha256"],
    }
    for path, expected in external_hashes.items():
        if not path.is_file() or file_sha256(path) != expected:
            _finding(findings, "p5_evidence_changed", str(path))

    if (
        state.get("R5_1_SCOPE_PROTOCOL_VERSION") != 2
        or state.get("R5_1_P8_REQUIRED_LEVELS") != ["O0_Captured", "O1_Observed"]
        or state.get("R5_1_P9_REQUIRED_ARMS") != ["S", "B", "D", "M-R"]
        or state.get("R5_1_M_O_CURRENT_BUDGET_ALLOWED") is not False
        or state.get("R5_ACCEPTED") is not False
        or state.get("R6_STARTED") is not False
    ):
        _finding(findings, "state_not_synchronized", "machine-readable state does not match scope v2")
    indexed = {
        item.get("path")
        for item in authority.get("documents", [])
        if isinstance(item, Mapping) and item.get("status") == "current"
    }
    for required in (
        "docs/roadmap/V2_3_R5_1_SCOPE_AMENDMENT_20260920.md",
        "configs/r5_1/r5_1_pre_r6_scope_protocol_v2.json",
        "configs/r5_1/r5_acceptance_gate_v2.json",
        "configs/r5_1/r6_entry_gate_v2.json",
        "docs/roadmap/R5_1_P5_ORDINARY_REFACTOR_CHECKPOINT.json",
    ):
        if required not in indexed:
            _finding(findings, "authority_index_missing", required)

    cli = (repo / "agrefactor/cli.py").read_text(encoding="utf-8")
    profile = (repo / "agrefactor/runtime/r5_profile.py").read_text(encoding="utf-8")
    if 'for source_command in ("refactor", "optimize", "full"):' not in cli:
        _finding(findings, "product_entrypoint_drift", "product CLI entrypoint tuple changed")
    if 'return R5Profile()' not in profile or 'unselected R5 profile must remain off' not in profile:
        _finding(findings, "default_off_drift", "R5 default-off contract changed")

    result: dict[str, Any] = {
        "schema_version": 1,
        "audit_id": "v2.3-r5.1-scope-amendment-independent-audit-v1",
        "status": "passed" if not findings else "failed",
        "critical_findings": len(findings),
        "findings": findings,
        "protocol_file_sha256": file_sha256(repo / PROTOCOL.relative_to(ROOT)),
        "protocol_sha256": canonical_sha256(protocol),
        "old_scope_file_sha256": expected_files["docs/roadmap/V2_3_PRE_R6_ROBUSTNESS_AND_EVIDENCE_PLAN.md"],
        "p5_preserved": not any(item["code"] == "p5_evidence_changed" for item in findings),
        "old_scope_preserved": not any(item["code"] == "historical_file_changed" for item in findings),
        "provider_calls": 0,
        "vitis_launches": 0,
        "git_history_mutations": 0,
        "r5_accepted": False,
        "r6_started": False,
        "next_step": "P6_opportunity_and_abstention_root_cause_protocol" if not findings else "fix_scope_consistency",
    }
    result["audit_sha256"] = canonical_sha256(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    before = subprocess.run(
        ["git", "-C", str(args.repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    result = audit(args.repo.resolve())
    after = subprocess.run(
        ["git", "-C", str(args.repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if before != after:
        _finding(result["findings"], "git_history_mutated", "audit changed repository HEAD")
        result["status"] = "failed"
        result["critical_findings"] = len(result["findings"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"R5_1_SCOPE_AMENDMENT_AUDIT_STATUS={result['status']}")
    print(f"CRITICAL_FINDINGS={result['critical_findings']}")
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    print("GIT_HISTORY_MUTATIONS=0")
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
