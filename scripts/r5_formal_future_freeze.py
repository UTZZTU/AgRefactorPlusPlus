#!/usr/bin/env python3
"""Freeze the R5 formal future partition without relabeling observed history."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Mapping
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agrefactor.campaign.r5_protocol import estimate_upper_bound
from agrefactor.recovery.r5_budget import R5BudgetLedger


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_RELATIVE = re.compile(r"^[A-Za-z0-9_./-]+$")
_FILE_ROLES = ("source", "legacy_candidate", "legacy_testbench", "public_test", "hidden_test")
_CONTROL_ROLES = {"positive", "inapplicable_or_confusable"}


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def signed(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    result = dict(value)
    result[field] = canonical_sha256(result)
    return result


def verify_signed(value: Mapping[str, Any], field: str) -> str:
    stored = value.get(field)
    unsigned = {key: item for key, item in value.items() if key != field}
    if not isinstance(stored, str) or not _SHA256.fullmatch(stored):
        raise ValueError(f"{field} missing or malformed")
    if canonical_sha256(unsigned) != stored:
        raise ValueError(f"{field} mismatch")
    return stored


def git(repository: Path, *args: str, text: bool = True) -> Any:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=text,
    )
    return completed.stdout.strip() if text else completed.stdout


def git_blob(repository: Path, commit: str, relative: str) -> bytes:
    if not _SAFE_RELATIVE.fullmatch(relative) or ".." in Path(relative).parts:
        raise ValueError("unsafe repository-relative path")
    return git(repository, "show", f"{commit}:{relative}", text=False)


def host_syntax(repository: Path, relative: str) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-Wno-unknown-pragmas",
            "-fsyntax-only",
            str(repository / relative),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "passed": completed.returncode == 0,
        "returncode": completed.returncode,
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
    }


def validate_plan(
    repository: Path,
    plan: Mapping[str, Any],
    *,
    commit: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if plan.get("status") != "predeclared_before_future_outcome_observation":
        raise ValueError("future plan was not frozen before outcome observation")
    if plan.get("history_binding") != "accepted_r5_authorized_revalidation_admission":
        raise ValueError("future plan does not bind accepted history")
    if plan.get("pilot_exclusions") != ["Exception_E3_Turbo_Encoder"]:
        raise ValueError("observed pilot case is not explicitly excluded")
    suffix = plan.get("candidate_top_suffix")
    if not isinstance(suffix, str) or not suffix:
        raise ValueError("candidate_top_suffix is required")
    cases = plan.get("future_cases")
    if not isinstance(cases, list) or len(cases) != 2:
        raise ValueError("formal future plan requires exactly two cases")
    ids: set[str] = set()
    sources: set[str] = set()
    adapter_hashes: set[str] = set()
    records: list[dict[str, Any]] = []
    for raw in cases:
        if not isinstance(raw, Mapping):
            raise ValueError("future case must be an object")
        case_id = raw.get("case_id")
        role = raw.get("control_role")
        top = raw.get("top")
        if (
            not isinstance(case_id, str)
            or not case_id
            or case_id in ids
            or case_id in plan["pilot_exclusions"]
        ):
            raise ValueError("future case identity is invalid or already observed")
        ids.add(case_id)
        if role not in _CONTROL_ROLES:
            raise ValueError(f"{case_id} has invalid control role")
        if not isinstance(top, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", top):
            raise ValueError(f"{case_id} has invalid top")
        if raw.get("deterministic_repair_expected") is not False:
            raise ValueError(f"{case_id} is preclassified for deterministic repair")
        if role == "positive" and (
            raw.get("expected_r2_failure_class") != "unsupported_construct"
            or raw.get("expected_r2_entry_boundary") != "unknown_or_mixed_review"
        ):
            raise ValueError("positive case is outside the accepted R2 scope")
        if role == "inapplicable_or_confusable" and (
            raw.get("expected_r2_failure_class") != "not_applicable"
            or raw.get("expected_r2_entry_boundary") != "inapplicable_or_confusable"
        ):
            raise ValueError("control case has incompatible R2 boundary")
        paths: dict[str, str] = {}
        hashes: dict[str, str] = {}
        for file_role in _FILE_ROLES:
            relative = raw.get(file_role)
            if (
                not isinstance(relative, str)
                or not _SAFE_RELATIVE.fullmatch(relative)
                or ".." in Path(relative).parts
            ):
                raise ValueError(f"{case_id}.{file_role} path is unsafe")
            content = git_blob(repository, commit, relative)
            paths[file_role] = relative
            hashes[file_role] = hashlib.sha256(content).hexdigest()
        if hashes["source"] in sources:
            raise ValueError("future source hash is reused")
        sources.add(hashes["source"])
        if hashes["public_test"] == hashes["hidden_test"]:
            raise ValueError("Public and Hidden adapters are identical")
        for file_role in ("public_test", "hidden_test"):
            if hashes[file_role] in adapter_hashes:
                raise ValueError("adapter crosses a Public/Hidden boundary")
            adapter_hashes.add(hashes[file_role])
            text = git_blob(repository, commit, paths[file_role]).decode("utf-8")
            for symbol in (top, top + suffix):
                if re.search(rf"\b{re.escape(symbol)}\s*\(", text) is None:
                    raise ValueError(f"{case_id}.{file_role} does not call {symbol}")
            if re.search(r"\breturn\s+1\s*;", text) is None:
                raise ValueError(f"{case_id}.{file_role} lacks failure return")
            if re.search(r'#\s*include\s*["<][^">]+\.cpp[">]', text):
                raise ValueError(f"{case_id}.{file_role} includes implementation")
        syntax = host_syntax(repository, paths["source"])
        if not syntax["passed"]:
            raise ValueError(f"{case_id} reference source is not host-syntax clean")
        record = {
            "case_id": case_id,
            "period": "future",
            "control_role": role,
            "top": top,
            "candidate_top": top + suffix,
            "expected_r2_failure_class": raw.get("expected_r2_failure_class"),
            "expected_r2_entry_boundary": raw.get("expected_r2_entry_boundary"),
            "deterministic_repair_expected": False,
            "paths": paths,
            "hashes": hashes,
            "reference_source_host_syntax": syntax,
            "outcome_observed": False,
            "hidden_model_visible": False,
        }
        record["case_identity_sha256"] = canonical_sha256(record)
        records.append(record)
    if {item["control_role"] for item in records} != _CONTROL_ROLES:
        raise ValueError("future partition requires one positive and one control")
    required_invariants = {
        "history_outcomes_reclassified_as_unobserved": False,
        "future_outcomes_observed_at_freeze": False,
        "pilot_case_reused": False,
        "expected_outputs_invented": False,
        "reference_top_is_oracle": True,
        "public_hidden_inputs_distinct": True,
        "original_benchmark_files_mutated": False,
        "hidden_model_visible": False,
        "trusted_revision_creation_allowed": False,
        "new_cli_allowed": False,
        "second_vitis_flow_allowed": False,
        "r5_accepted": False,
        "r6_started": False,
    }
    invariants = plan.get("invariants")
    if not isinstance(invariants, Mapping) or any(
        invariants.get(key) is not expected
        for key, expected in required_invariants.items()
    ):
        raise ValueError("future plan invariants are incomplete")
    return records, dict(plan["budget"])


def validate_budget(state: Mapping[str, Any], plan_budget: Mapping[str, Any]) -> dict[str, Any]:
    required_state = {
        "R4_ACCEPTED": True,
        "R5_ACCEPTED": False,
        "R6_STARTED": False,
        "R5_REAL_CAMPAIGN_ALLOWED": False,
        "R5_PROVIDER_CALL_HARD_CAP": 500,
        "R5_VITIS_LAUNCH_HARD_CAP": 500,
    }
    if any(state.get(key) != expected for key, expected in required_state.items()):
        raise ValueError("roadmap authority state is incompatible")
    provider_used = state.get("R5_CONSUMED_PROVIDER_CALLS")
    vitis_used = state.get("R5_CONSUMED_VITIS_LAUNCHES")
    if provider_used != plan_budget.get("provider_calls_before") or vitis_used != plan_budget.get("vitis_launches_before"):
        raise ValueError("plan budget does not match the authoritative ledger")
    provider_bound, vitis_bound = estimate_upper_bound(case_count=2)
    if (
        provider_bound != plan_budget.get("provider_upper_bound")
        or vitis_bound != plan_budget.get("vitis_upper_bound")
    ):
        raise ValueError("formal upper bound is incompatible with A0-A6 protocol")
    ledger = R5BudgetLedger(
        provider_cap=500,
        vitis_cap=500,
        provider_used=int(provider_used),
        vitis_used=int(vitis_used),
    )
    reservation = ledger.reserve_with_recovery(
        provider_upper_bound=provider_bound,
        vitis_upper_bound=vitis_bound,
        reserve_fraction=float(plan_budget.get("reserve_fraction", -1)),
    )
    return {
        "ledger": ledger.to_dict(),
        "provider_upper_bound": provider_bound,
        "vitis_upper_bound": vitis_bound,
        "provider_recovery_reserve": reservation.provider_recovery_reserve,
        "vitis_recovery_reserve": reservation.vitis_recovery_reserve,
        "reservation_sha256": reservation.reservation_sha256,
    }


def validate_history(
    state: Mapping[str, Any],
    admission_root: Path,
    admission_audit_path: Path,
) -> dict[str, Any]:
    required_files = {
        "ledger_manifest": admission_root / "ledger" / "ledger_manifest.json",
        "lifecycle_reduction": admission_root / "lifecycle_reduction.json",
        "memory_snapshot": admission_root / "memory_snapshot.json",
        "memory_payload": admission_root / "memory_payload.json",
        "admission_result": admission_root / "admission_result.json",
        "admission_audit": admission_audit_path,
    }
    if any(not path.is_file() for path in required_files.values()):
        raise ValueError("accepted history artifacts are incomplete")
    audit = load(admission_audit_path)
    if audit.get("status") != "clean_verified_positive_lifecycle_admission":
        raise ValueError("accepted history audit is not clean")
    if file_sha256(required_files["ledger_manifest"]) != state.get("R5_AUTHORIZED_CANDIDATE_REVALIDATION_LEDGER_MANIFEST_FILE_SHA256"):
        raise ValueError("history ledger manifest differs from roadmap state")
    if file_sha256(admission_audit_path) != state.get("R5_AUTHORIZED_CANDIDATE_REVALIDATION_ADMISSION_AUDIT_FILE_SHA256"):
        raise ValueError("history audit differs from roadmap state")
    snapshot = load(required_files["memory_snapshot"])
    payload = load(required_files["memory_payload"])
    if snapshot.get("snapshot_sha256") != state.get("R5_AUTHORIZED_CANDIDATE_REVALIDATION_SNAPSHOT_SHA256"):
        raise ValueError("history snapshot identity mismatch")
    if payload.get("payload_sha256") != state.get("R5_AUTHORIZED_CANDIDATE_REVALIDATION_PAYLOAD_SHA256"):
        raise ValueError("history payload identity mismatch")
    return {
        "files": {key: str(path) for key, path in required_files.items()},
        "file_sha256": {key: file_sha256(path) for key, path in required_files.items()},
        "revision_sha256": state.get("R5_AUTHORIZED_CANDIDATE_REVALIDATION_REVISION_SHA256"),
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "payload_sha256": payload["payload_sha256"],
        "lifecycle": state.get("R5_AUTHORIZED_CANDIDATE_REVALIDATION_LIFECYCLE"),
        "verified_positive_episode_count": state.get("R5_COMBINED_HISTORY_VERIFIED_POSITIVE_EPISODE_COUNT"),
    }


def build_freeze(
    repository: Path,
    plan: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    admission_root: Path,
    admission_audit_path: Path,
    pilot_reconciliation_path: Path,
) -> dict[str, Any]:
    repository = repository.resolve()
    if git(repository, "branch", "--show-current") != "research-roadmap-v2.3":
        raise ValueError("formal freeze requires the R5 branch")
    if git(repository, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("formal freeze requires a clean worktree")
    commit = git(repository, "rev-parse", "HEAD")
    cases, plan_budget = validate_plan(repository, plan, commit=commit)
    budget = validate_budget(state, plan_budget)
    history = validate_history(state, admission_root, admission_audit_path)
    pilot = load(pilot_reconciliation_path)
    if (
        pilot.get("status") != "complete_structurally_clean_no_verified_repair"
        or pilot.get("formal_campaign_boundary", {}).get("pilot_case_may_remain_primary_future_positive") is not False
    ):
        raise ValueError("pilot exclusion evidence is incomplete")
    pilot_source = None
    pilot_result_path = Path(str(pilot.get("evidence", {}).get("combined_result_v2_path", "")))
    if pilot_result_path.is_file():
        pilot_result = load(pilot_result_path)
        baselines = pilot_result.get("baselines")
        if isinstance(baselines, list) and baselines:
            pilot_source = baselines[0].get("source_sha256")
    if pilot_source in {item["hashes"]["source"] for item in cases}:
        raise ValueError("formal future source reuses the observed pilot source")
    manifest = signed(
        {
            "schema_version": 1,
            "manifest_id": "v2.3-r5-formal-future-only-manifest-v1",
            "status": "frozen_ready_for_formal_campaign",
            "repository_commit": commit,
            "plan_sha256": canonical_sha256(plan),
            "history_binding": history,
            "pilot_exclusion": {
                "case_ids": list(plan["pilot_exclusions"]),
                "reconciliation_path": str(pilot_reconciliation_path),
                "reconciliation_file_sha256": file_sha256(pilot_reconciliation_path),
                "observed_source_sha256": pilot_source,
            },
            "future_cases": cases,
            "budget": budget,
            "provider_calls": 0,
            "vitis_launches": 0,
            "git_history_mutations": 0,
            "r5_accepted": False,
            "r6_started": False,
        },
        "manifest_sha256",
    )
    audit = signed(
        {
            "schema_version": 1,
            "audit_id": "v2.3-r5-formal-future-freeze-audit-v1",
            "status": "clean_ready_for_formal_campaign",
            "manifest_sha256": manifest["manifest_sha256"],
            "checks": {
                "accepted_history_bound_without_relabeling": True,
                "future_positive_and_control_distinct": True,
                "future_sources_independent": True,
                "public_hidden_oracles_distinct": True,
                "pilot_case_and_source_excluded": True,
                "formal_budget_reserved_with_recovery": True,
                "new_cli_created": False,
                "second_vitis_flow_created": False,
                "future_outcomes_observed": False,
            },
            "provider_calls": 0,
            "vitis_launches": 0,
            "git_history_mutations": 0,
            "r5_accepted": False,
            "r6_started": False,
        },
        "audit_sha256",
    )
    return {"formal_future_manifest": manifest, "independent_protocol_audit": audit}


def write_outputs(output: Path, values: Mapping[str, Any]) -> None:
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        for name, value in values.items():
            (temporary / f"{name}.json").write_text(
                json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        temporary.replace(output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--admission-root", type=Path, required=True)
    parser.add_argument("--admission-audit", type=Path, required=True)
    parser.add_argument("--pilot-reconciliation", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    values = build_freeze(
        args.repo,
        load(args.plan),
        load(args.state),
        admission_root=args.admission_root,
        admission_audit_path=args.admission_audit,
        pilot_reconciliation_path=args.pilot_reconciliation,
    )
    write_outputs(args.output_dir.resolve(), values)
    print("R5_FORMAL_FUTURE_FREEZE_STATUS=clean_ready_for_formal_campaign")
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    print("GIT_HISTORY_MUTATIONS=0")
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
