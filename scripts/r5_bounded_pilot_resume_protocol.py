#!/usr/bin/env python3
"""Zero-call authorization audit for the interrupted R5 pilot resume."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "docs/roadmap/V2_3_STATE.json"
RUN1 = Path("/data/agrefactor_runs/r5_p5_bounded_real_pilot_99420db_run1")
RECONCILIATION = ROOT / "docs/roadmap/R5_BOUNDED_PILOT_RUN1_RECONCILIATION.json"
FIX_HEAD = "ec6dce13b368d026c46eb1982551168bac84b6d2"
REPEAT_PROVIDER_UPPER_BOUND = 20
REPEAT_VITIS_UPPER_BOUND = 24
FORMAL_PROVIDER_RESERVE = 120
FORMAL_VITIS_RESERVE = 144


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("JSON object required: " + str(path))
    return value


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(ROOT), *args], text=True
    ).strip()


def build_audit() -> dict[str, Any]:
    state = load(STATE)
    manifest = load(RUN1 / "pilot_manifest.json")
    partial = load(RUN1 / "partial_independent_audit.json")
    reconciliation = load(RECONCILIATION)
    issues: list[str] = []
    if git("status", "--porcelain"):
        issues.append("working_tree_not_clean")
    if git("branch", "--show-current") != "research-roadmap-v2.3":
        issues.append("wrong_branch")
    if subprocess.run(
        ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", FIX_HEAD, "HEAD"],
        check=False,
    ).returncode != 0:
        issues.append("orchestration_fix_not_ancestor")
    if state.get("R4_ACCEPTED") is not True or state.get("R5_ACCEPTED") is not False:
        issues.append("r4_r5_authority_state_invalid")
    if state.get("R6_STARTED") is not False:
        issues.append("r6_already_started")
    if state.get("R5_CONSUMED_PROVIDER_CALLS") != 131:
        issues.append("provider_ledger_not_reconciled")
    if state.get("R5_CONSUMED_VITIS_LAUNCHES") != 63:
        issues.append("vitis_ledger_not_reconciled")
    if sha(RECONCILIATION) != state.get("R5_BOUNDED_PILOT_RUN1_RECONCILIATION_SHA256"):
        issues.append("reconciliation_hash_mismatch")
    if sha(RUN1 / "partial_independent_audit.json") != state.get(
        "R5_BOUNDED_PILOT_RUN1_PARTIAL_AUDIT_FILE_SHA256"
    ):
        issues.append("partial_audit_file_hash_mismatch")
    if partial.get("status") != "clean_partial" or partial.get("critical_finding_count") != 0:
        issues.append("partial_audit_not_clean")
    if partial.get("complete_repeats") != [1, 2] or partial.get("resume_repeats") != [3]:
        issues.append("resume_scope_not_exactly_repeat_03")
    if partial.get("provider_calls") != 27 or partial.get("vitis_launches") != 4:
        issues.append("partial_usage_mismatch")
    if (RUN1 / "pilot_result.json").exists():
        issues.append("original_run_unexpectedly_complete")
    if manifest.get("future_outcomes_observed_before_start") is not False:
        issues.append("original_manifest_not_frozen")

    provider_cap = int(state.get("R5_PROVIDER_CALL_HARD_CAP", 0))
    vitis_cap = int(state.get("R5_VITIS_LAUNCH_HARD_CAP", 0))
    provider_used = int(state.get("R5_CONSUMED_PROVIDER_CALLS", -1))
    vitis_used = int(state.get("R5_CONSUMED_VITIS_LAUNCHES", -1))
    provider_recovery_reserve = math.ceil((provider_cap - provider_used) * 0.1)
    vitis_recovery_reserve = math.ceil((vitis_cap - vitis_used) * 0.1)
    provider_projected = provider_used + REPEAT_PROVIDER_UPPER_BOUND + FORMAL_PROVIDER_RESERVE
    vitis_projected = vitis_used + REPEAT_VITIS_UPPER_BOUND + FORMAL_VITIS_RESERVE
    if provider_projected + provider_recovery_reserve > provider_cap:
        issues.append("provider_budget_cannot_preserve_formal_and_recovery_reserve")
    if vitis_projected + vitis_recovery_reserve > vitis_cap:
        issues.append("vitis_budget_cannot_preserve_formal_and_recovery_reserve")

    audit = {
        "schema_version": 1,
        "audit_id": "v2.3-r5-bounded-pilot-repeat-03-resume-v1",
        "status": "ready_for_repeat_03_resume" if not issues else "blocked",
        "issues": issues,
        "repository_head": git("rev-parse", "HEAD"),
        "orchestration_fix_head": FIX_HEAD,
        "original_run_root": str(RUN1),
        "original_pilot_manifest_file_sha256": sha(RUN1 / "pilot_manifest.json"),
        "partial_audit_file_sha256": sha(RUN1 / "partial_independent_audit.json"),
        "partial_audit_sha256": partial.get("audit_sha256"),
        "reconciliation_file_sha256": sha(RECONCILIATION),
        "authorized_repeats": [3],
        "forbidden_repeats": [1, 2],
        "provider_calls_before": provider_used,
        "vitis_launches_before": vitis_used,
        "resume_provider_upper_bound": REPEAT_PROVIDER_UPPER_BOUND,
        "resume_vitis_upper_bound": REPEAT_VITIS_UPPER_BOUND,
        "formal_provider_reserve": FORMAL_PROVIDER_RESERVE,
        "formal_vitis_reserve": FORMAL_VITIS_RESERVE,
        "provider_recovery_reserve": provider_recovery_reserve,
        "vitis_recovery_reserve": vitis_recovery_reserve,
        "provider_calls": 0,
        "vitis_launches": 0,
        "git_history_mutations": 0,
        "r5_accepted": False,
        "r6_started": False,
        "source_file_sha256": {
            "agrefactor/campaign/r5_product_executor.py": sha(
                ROOT / "agrefactor/campaign/r5_product_executor.py"
            ),
            "scripts/r5_bounded_pilot.py": sha(ROOT / "scripts/r5_bounded_pilot.py"),
        },
    }
    audit["audit_sha256"] = hashlib.sha256(canonical(audit).encode()).hexdigest()
    return audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError("output directory already exists")
    output.mkdir(parents=True)
    audit = build_audit()
    (output / "resume_protocol_audit.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print("R5_PILOT_RESUME_PROTOCOL_STATUS=" + audit["status"])
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return 0 if audit["status"] == "ready_for_repeat_03_resume" else 1


if __name__ == "__main__":
    raise SystemExit(main())
