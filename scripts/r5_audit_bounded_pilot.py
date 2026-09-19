#!/usr/bin/env python3
"""Independent file-only audit for the bounded R5 pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

ROOT = Path("/data/AgRefactor")
STATE = ROOT / "docs/roadmap/V2_3_STATE.json"
SHA256 = 64


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("JSON object required: " + str(path))
    return value


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


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
        return any(tag in lowered for tag in ("<think", "</think", "<reasoning", "</reasoning"))
    return False


def audit_partial(
    root: Path,
    *,
    state: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Audit an interrupted pilot without relabelling it as complete."""

    findings: list[dict[str, str]] = []
    unsigned = dict(manifest)
    stored = unsigned.pop("pilot_manifest_sha256", None)
    if not isinstance(stored, str) or hashlib.sha256(
        canonical(unsigned).encode()
    ).hexdigest() != stored:
        findings.append({"severity": "critical", "code": "pilot_manifest_hash_invalid"})
    if manifest.get("status") != "frozen_before_pilot_outcome_observation":
        findings.append({"severity": "critical", "code": "pilot_manifest_not_frozen"})
    if manifest.get("r5_accepted") is not False or manifest.get("r6_started") is not False:
        findings.append({"severity": "critical", "code": "authority_boundary_violation"})
    if manifest.get("repository_head") != state.get("head"):
        findings.append({"severity": "warning", "code": "pilot_head_differs_from_state"})

    expected_repeats = int(manifest.get("repeats", 0))
    repeat_roots = sorted((root / "pairs").glob("repeat-*"))
    observed_repeats: list[int] = []
    complete_repeats: list[int] = []
    incomplete_repeats: list[int] = []
    baseline_ids: set[str] = set()
    provider_calls = 0
    vitis_launches = 0
    arm_count = 0
    evidence_hashes: dict[str, str] = {
        "pilot_manifest.json": sha(root / "pilot_manifest.json"),
    }
    for repeat_root in repeat_roots:
        try:
            repeat = int(repeat_root.name.removeprefix("repeat-"))
        except ValueError:
            findings.append({"severity": "critical", "code": "repeat_name_invalid"})
            continue
        observed_repeats.append(repeat)
        baseline_paths = tuple((repeat_root / "paired").rglob("common_baseline.json"))
        run_path = repeat_root / "common-product" / "run_result.json"
        if len(baseline_paths) != 1 or not run_path.is_file():
            findings.append({"severity": "critical", "code": "partial_baseline_missing"})
            incomplete_repeats.append(repeat)
            continue
        baseline_path = baseline_paths[0]
        baseline = load(baseline_path)
        run_result = load(run_path)
        relative_baseline = str(baseline_path.relative_to(root))
        relative_run = str(run_path.relative_to(root))
        evidence_hashes[relative_baseline] = sha(baseline_path)
        evidence_hashes[relative_run] = sha(run_path)
        baseline_id = baseline.get("baseline_id")
        if not isinstance(baseline_id, str) or baseline_id in baseline_ids:
            findings.append({"severity": "critical", "code": "baseline_identity_not_unique"})
        else:
            baseline_ids.add(baseline_id)
        baseline_provider = baseline.get("provider_calls")
        baseline_vitis = baseline.get("vitis_launches")
        if not isinstance(baseline_provider, int) or not isinstance(baseline_vitis, int):
            findings.append({"severity": "critical", "code": "baseline_usage_malformed"})
            incomplete_repeats.append(repeat)
            continue
        budget = run_result.get("budget_usage", {})
        run_provider = budget.get("llm_calls") if isinstance(budget, Mapping) else None
        run_vitis = (
            sum(int(budget.get(key, 0)) for key in ("csim_calls", "csynth_calls", "cosim_calls"))
            if isinstance(budget, Mapping)
            else None
        )
        if run_provider != baseline_provider or run_vitis != baseline_vitis:
            findings.append({"severity": "critical", "code": "baseline_usage_mismatch"})
        provider_calls += baseline_provider
        vitis_launches += baseline_vitis

        arms: set[str] = set()
        for arm_path in sorted((repeat_root / "paired").rglob("arm_result.json")):
            item = load(arm_path)
            relative_arm = str(arm_path.relative_to(root))
            evidence_hashes[relative_arm] = sha(arm_path)
            arm = item.get("arm")
            if arm not in {"A0", "A1", "A2", "A3", "A4", "A5", "A6"} or arm in arms:
                findings.append({"severity": "critical", "code": "partial_arm_identity_invalid"})
            else:
                arms.add(str(arm))
            if item.get("baseline_id") != baseline_id:
                findings.append({"severity": "critical", "code": "partial_arm_baseline_mismatch"})
            if item.get("hidden_input_count") != 0 or item.get("cross_arm_cache_used") is not False:
                findings.append({"severity": "critical", "code": "partial_arm_boundary_violation"})
            arm_provider = item.get("provider_calls")
            arm_vitis = item.get("vitis_launches")
            if not isinstance(arm_provider, int) or not isinstance(arm_vitis, int):
                findings.append({"severity": "critical", "code": "partial_arm_usage_malformed"})
            else:
                provider_calls += arm_provider
                vitis_launches += arm_vitis
            if item.get("status") == "invalid_evidence":
                findings.append({"severity": "critical", "code": "invalid_evidence_observed"})
            if unsafe_persisted_content(item):
                findings.append({"severity": "critical", "code": "private_or_raw_content_persisted"})
        arm_count += len(arms)
        if arms == {"A0", "A1", "A2", "A3", "A4", "A5", "A6"}:
            complete_repeats.append(repeat)
        else:
            incomplete_repeats.append(repeat)

    expected = list(range(1, expected_repeats + 1))
    if observed_repeats != expected:
        findings.append({"severity": "critical", "code": "partial_repeat_coverage_invalid"})
    if provider_calls > int(manifest.get("provider_upper_bound", -1)):
        findings.append({"severity": "critical", "code": "provider_bound_violation"})
    if vitis_launches > int(manifest.get("vitis_upper_bound", -1)):
        findings.append({"severity": "critical", "code": "vitis_bound_violation"})

    critical = sum(item["severity"] == "critical" for item in findings)
    audit = {
        "schema_version": 1,
        "auditor": "r5-bounded-pilot-file-only-partial-v1",
        "status": "clean_partial" if critical == 0 else "blocked",
        "reason": (
            "incomplete_pilot_evidence_reconciled_without_completion_claim"
            if critical == 0
            else "critical_partial_pilot_finding"
        ),
        "critical_finding_count": critical,
        "findings": findings,
        "pilot_complete": False,
        "observed_repeats": observed_repeats,
        "complete_repeats": complete_repeats,
        "resume_repeats": sorted(set(incomplete_repeats)),
        "baseline_count": len(baseline_ids),
        "arm_observation_count": arm_count,
        "provider_calls": provider_calls,
        "vitis_launches": vitis_launches,
        "provider_calls_after": int(manifest.get("provider_used_before", 0)) + provider_calls,
        "vitis_launches_after": int(manifest.get("vitis_used_before", 0)) + vitis_launches,
        "evidence_file_sha256": evidence_hashes,
        "external_provider_calls": 0,
        "external_vitis_launches": 0,
        "git_history_mutations": 0,
        "r5_accepted": False,
        "r6_started": False,
    }
    audit["audit_sha256"] = hashlib.sha256(canonical(audit).encode()).hexdigest()
    return audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence_root", type=Path)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    root = args.evidence_root.resolve()
    state = load(STATE)
    manifest = load(root / "pilot_manifest.json")
    if not (root / "pilot_result.json").is_file():
        if not args.allow_partial:
            raise RuntimeError("pilot_result.json is missing; use --allow-partial to audit an interrupted pilot")
        audit = audit_partial(root, state=state, manifest=manifest)
        (root / "partial_independent_audit.json").write_text(
            json.dumps(audit, indent=2, sort_keys=True) + chr(10),
            encoding="utf-8",
        )
        print("R5_BOUNDED_PILOT_PARTIAL_AUDIT_STATUS=" + audit["status"])
        print("R5_BOUNDED_PILOT_PARTIAL_AUDIT_REASON=" + audit["reason"])
        print("CRITICAL_FINDINGS=" + str(audit["critical_finding_count"]))
        print("OBSERVED_PROVIDER_CALLS=" + str(audit["provider_calls"]))
        print("OBSERVED_VITIS_LAUNCHES=" + str(audit["vitis_launches"]))
        print("EXTERNAL_AUDITOR_PROVIDER_CALLS=0")
        print("EXTERNAL_AUDITOR_VITIS_LAUNCHES=0")
        return 0 if audit["status"] == "clean_partial" else 1
    result = load(root / "pilot_result.json")
    findings: list[dict[str, str]] = []
    if manifest.get("status") != "frozen_before_pilot_outcome_observation":
        findings.append({"severity": "critical", "code": "pilot_manifest_not_frozen"})
    if result.get("pilot_manifest_sha256") != manifest.get("pilot_manifest_sha256"):
        findings.append({"severity": "critical", "code": "manifest_binding_mismatch"})
    unsigned = dict(manifest); stored = unsigned.pop("pilot_manifest_sha256", None)
    if not isinstance(stored, str) or hashlib.sha256(canonical(unsigned).encode()).hexdigest() != stored:
        findings.append({"severity": "critical", "code": "pilot_manifest_hash_invalid"})
    unsigned_result = dict(result); stored_result = unsigned_result.pop("result_sha256", None)
    if not isinstance(stored_result, str) or hashlib.sha256(canonical(unsigned_result).encode()).hexdigest() != stored_result:
        findings.append({"severity": "critical", "code": "pilot_result_hash_invalid"})
    if manifest.get("repository_head") != result.get("repository_head"):
        findings.append({"severity": "critical", "code": "repository_head_mismatch"})
    if manifest.get("repository_head") != state.get("head"):
        findings.append({"severity": "warning", "code": "pilot_head_differs_from_state"})
    if manifest.get("case_id") != result.get("case_id") or manifest.get("repeats") != 3:
        findings.append({"severity": "critical", "code": "pilot_case_or_repeat_mismatch"})
    if result.get("future_outcomes_observed") is not True or result.get("future_files_executed") is not True:
        findings.append({"severity": "critical", "code": "pilot_future_execution_not_recorded"})
    if result.get("cross_arm_cache_used") is not False or result.get("hidden_input_count") != 0:
        findings.append({"severity": "critical", "code": "cache_or_hidden_boundary_violation"})
    if result.get("git_history_mutations") != 0 or result.get("r5_accepted") is not False or result.get("r6_started") is not False:
        findings.append({"severity": "critical", "code": "authority_or_git_boundary_violation"})
    provider = int(result.get("provider_calls", -1)); vitis = int(result.get("vitis_launches", -1))
    if provider < 0 or provider > int(manifest.get("provider_upper_bound", 60)):
        findings.append({"severity": "critical", "code": "provider_bound_violation"})
    if vitis < 0 or vitis > int(manifest.get("vitis_upper_bound", 72)):
        findings.append({"severity": "critical", "code": "vitis_bound_violation"})
    if int(result.get("provider_calls_after", -1)) != int(manifest.get("provider_used_before", -2)) + provider:
        findings.append({"severity": "critical", "code": "provider_ledger_mismatch"})
    if int(result.get("vitis_launches_after", -1)) != int(manifest.get("vitis_used_before", -2)) + vitis:
        findings.append({"severity": "critical", "code": "vitis_ledger_mismatch"})
    baselines = result.get("baselines", [])
    observations = result.get("observations", [])
    if not isinstance(baselines, list) or len(baselines) != 3:
        findings.append({"severity": "critical", "code": "baseline_repeat_count"})
    if not isinstance(observations, list) or len(observations) != 21:
        findings.append({"severity": "critical", "code": "arm_observation_count"})
    baseline_ids = {item.get("baseline_id") for item in baselines if isinstance(item, Mapping)}
    if len(baseline_ids) != 3:
        findings.append({"severity": "critical", "code": "baseline_identity_not_unique"})
    for index, item in enumerate(observations if isinstance(observations, list) else []):
        if not isinstance(item, Mapping):
            findings.append({"severity": "critical", "code": "observation_not_object"})
            continue
        if item.get("baseline_id") not in baseline_ids:
            findings.append({"severity": "critical", "code": "observation_baseline_mismatch"})
        if item.get("case_id") != manifest.get("case_id"):
            findings.append({"severity": "critical", "code": "observation_case_mismatch"})
        if item.get("hidden_input_count") != 0 or item.get("cross_arm_cache_used") is not False:
            findings.append({"severity": "critical", "code": "observation_boundary_violation"})
        arm = item.get("arm")
        if arm not in {"A0", "A1", "A2", "A3", "A4", "A5", "A6"}:
            findings.append({"severity": "critical", "code": "unknown_arm"})
        if not isinstance(item.get("provider_calls"), int) or not isinstance(item.get("vitis_launches"), int):
            findings.append({"severity": "critical", "code": "observation_usage_malformed"})
        if unsafe_persisted_content(item):
            findings.append({"severity": "critical", "code": "private_or_raw_content_persisted"})
    statuses = [item.get("status") for item in observations if isinstance(item, Mapping)]
    if any(status == "invalid_evidence" for status in statuses):
        findings.append({"severity": "critical", "code": "invalid_evidence_observed"})
    critical = sum(item["severity"] == "critical" for item in findings)
    status = "clean" if critical == 0 else "blocked"
    audit = {
        "schema_version": 1,
        "auditor": "r5-bounded-pilot-file-only-v1",
        "status": status,
        "reason": "all_pilot_boundaries_verified" if status == "clean" else "critical_pilot_finding",
        "critical_finding_count": critical,
        "findings": findings,
        "pilot_manifest_sha256": sha(root / "pilot_manifest.json"),
        "pilot_result_sha256": sha(root / "pilot_result.json"),
        "provider_calls": 0,
        "vitis_launches": 0,
        "git_history_mutations": 0,
        "future_files_read_by_auditor": False,
        "r5_accepted": False,
        "r6_started": False,
    }
    audit["audit_sha256"] = hashlib.sha256(canonical(audit).encode()).hexdigest()
    (root / "independent_audit.json").write_text(json.dumps(audit, indent=2, sort_keys=True) + chr(10), encoding="utf-8")
    print("R5_BOUNDED_PILOT_AUDIT_STATUS=" + status)
    print("R5_BOUNDED_PILOT_AUDIT_REASON=" + audit["reason"])
    print("CRITICAL_FINDINGS=" + str(critical))
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    return 0 if status == "clean" else 1


if __name__ == "__main__":
    raise SystemExit(main())
