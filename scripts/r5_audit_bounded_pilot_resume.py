#!/usr/bin/env python3
"""Independent file-only audit combining pilot repeats 1/2 with resumed 3."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


BASELINE_ARMS = frozenset({"A0", "A1"})
MUTATION_ARMS = frozenset({"A2", "A3", "A4", "A5", "A6"})


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("JSON object required: " + str(path))
    return value


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def valid_embedded_hash(value: Mapping[str, Any], field: str) -> bool:
    unsigned = dict(value)
    stored = unsigned.pop(field, None)
    return isinstance(stored, str) and hashlib.sha256(canonical(unsigned).encode()).hexdigest() == stored


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
            if key in forbidden or (
                key.endswith("_persisted") and child not in (False, "false")
            ):
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


def observation_outcomes(value: Mapping[str, Any]) -> tuple[bool, bool]:
    """Derive baseline acceptance and verified repair without conflating them."""

    arm = str(value.get("arm", ""))
    status = str(value.get("status", ""))
    baseline_accepted = arm in BASELINE_ARMS and status in {
        "baseline_accepted",
        "verified_positive",  # Legacy pilot encoding before the metric split.
    }
    integration = value.get("integration")
    verified_repair = (
        arm in MUTATION_ARMS
        and status == "verified_positive"
        and isinstance(integration, Mapping)
        and integration.get("status") == "verified_positive"
        and integration.get("accepted_by_integration") is True
    )
    return baseline_accepted, verified_repair


def original_observations(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    baselines: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    for repeat in (1, 2):
        repeat_root = root / "pairs" / ("repeat-%02d" % repeat) / "paired"
        baseline_paths = tuple(repeat_root.rglob("common_baseline.json"))
        if len(baseline_paths) != 1:
            raise RuntimeError("original completed repeat lacks one common baseline")
        baselines.append(load(baseline_paths[0]))
        arm_paths = tuple(sorted(repeat_root.rglob("arm_result.json")))
        if len(arm_paths) != 7:
            raise RuntimeError("original completed repeat lacks seven arms")
        observations.extend(load(path) for path in arm_paths)
    return baselines, observations


def audit(original: Path, resume: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    partial = load(original / "partial_independent_audit.json")
    resume_manifest = load(resume / "resume_manifest.json")
    resume_result = load(resume / "resume_result.json")
    findings: list[dict[str, str]] = []
    if partial.get("status") != "clean_partial" or partial.get("complete_repeats") != [1, 2]:
        findings.append({"severity": "critical", "code": "original_partial_audit_invalid"})
    if not valid_embedded_hash(resume_manifest, "resume_manifest_sha256"):
        findings.append({"severity": "critical", "code": "resume_manifest_hash_invalid"})
    if not valid_embedded_hash(resume_result, "result_sha256"):
        findings.append({"severity": "critical", "code": "resume_result_hash_invalid"})
    if resume_result.get("resume_manifest_sha256") != resume_manifest.get("resume_manifest_sha256"):
        findings.append({"severity": "critical", "code": "resume_manifest_binding_mismatch"})
    if resume_result.get("replacement_repeat") != 3:
        findings.append({"severity": "critical", "code": "wrong_replacement_repeat"})
    if (
        resume_result.get("git_history_mutations") != 0
        or resume_result.get("r5_accepted") is not False
        or resume_result.get("r6_started") is not False
    ):
        findings.append(
            {"severity": "critical", "code": "resume_authority_boundary_violation"}
        )
    if resume_result.get("provider_calls_after") != 131 + resume_result.get("provider_calls", -1):
        findings.append({"severity": "critical", "code": "resume_provider_ledger_mismatch"})
    if resume_result.get("vitis_launches_after") != 63 + resume_result.get("vitis_launches", -1):
        findings.append({"severity": "critical", "code": "resume_vitis_ledger_mismatch"})
    if resume_result.get("provider_calls", 21) > 20 or resume_result.get("vitis_launches", 25) > 24:
        findings.append({"severity": "critical", "code": "resume_budget_bound_violation"})

    baselines, observations = original_observations(original)
    replacement_baseline = resume_result.get("baseline")
    replacement_observations = resume_result.get("observations")
    if not isinstance(replacement_baseline, Mapping):
        findings.append({"severity": "critical", "code": "replacement_baseline_missing"})
    else:
        baselines.append(dict(replacement_baseline))
    if not isinstance(replacement_observations, list) or len(replacement_observations) != 7:
        findings.append({"severity": "critical", "code": "replacement_arm_count"})
    else:
        observations.extend(dict(item) for item in replacement_observations if isinstance(item, Mapping))
    if len(baselines) != 3 or len({item.get("baseline_id") for item in baselines}) != 3:
        findings.append({"severity": "critical", "code": "combined_baseline_identity_invalid"})
    arm_repeat_counts = Counter((item.get("repeat"), item.get("arm")) for item in observations)
    expected = {(repeat, "A%d" % arm) for repeat in (1, 2, 3) for arm in range(7)}
    if set(arm_repeat_counts) != expected or any(count != 1 for count in arm_repeat_counts.values()):
        findings.append({"severity": "critical", "code": "combined_arm_matrix_invalid"})
    for item in observations:
        if item.get("status") == "invalid_evidence":
            findings.append({"severity": "critical", "code": "invalid_evidence_observed"})
        if item.get("hidden_input_count") != 0 or item.get("cross_arm_cache_used") is not False:
            findings.append({"severity": "critical", "code": "arm_boundary_violation"})
        if not isinstance(item.get("provider_calls"), int) or not isinstance(
            item.get("vitis_launches"), int
        ):
            findings.append({"severity": "critical", "code": "arm_usage_malformed"})
        if unsafe_persisted_content(item):
            findings.append(
                {"severity": "critical", "code": "private_or_raw_content_persisted"}
            )

    status_counts = dict(sorted(Counter(str(item.get("status")) for item in observations).items()))
    outcome_flags = [observation_outcomes(item) for item in observations]
    baseline_accepted = sum(flag[0] for flag in outcome_flags)
    verified_repairs = sum(flag[1] for flag in outcome_flags)
    if verified_repairs == 0:
        findings.append({"severity": "warning", "code": "pilot_no_verified_repair"})
    critical = sum(item["severity"] == "critical" for item in findings)
    combined = {
        "schema_version": 2,
        "status": "ready_for_independent_audit" if critical == 0 else "blocked",
        "original_run_root": str(original),
        "resume_run_root": str(resume),
        "baselines": baselines,
        "observations": observations,
        "status_counts": status_counts,
        "baseline_accepted_count": baseline_accepted,
        "verified_repair_count": verified_repairs,
        "metric_semantics": {
            "baseline_accepted": "A0/A1 accepted without an R4/R5 repair claim",
            "verified_repair": (
                "A2-A6 only, with both arm and existing-integration status "
                "verified_positive and accepted_by_integration=true"
            ),
        },
        "provider_calls_original": partial.get("provider_calls"),
        "vitis_launches_original": partial.get("vitis_launches"),
        "provider_calls_resume": resume_result.get("provider_calls"),
        "vitis_launches_resume": resume_result.get("vitis_launches"),
        "provider_calls_after": resume_result.get("provider_calls_after"),
        "vitis_launches_after": resume_result.get("vitis_launches_after"),
        "r5_accepted": False,
        "r6_started": False,
    }
    combined["combined_result_sha256"] = hashlib.sha256(canonical(combined).encode()).hexdigest()
    audit = {
        "schema_version": 2,
        "auditor": "r5-bounded-pilot-resume-file-only-v2",
        "status": "clean" if critical == 0 else "blocked",
        "reason": (
            "combined_three_repeat_pilot_structurally_clean"
            if critical == 0
            else "critical_combined_pilot_finding"
        ),
        "critical_finding_count": critical,
        "findings": findings,
        "baseline_accepted_count": baseline_accepted,
        "verified_repair_count": verified_repairs,
        "combined_result_sha256": combined["combined_result_sha256"],
        "original_partial_audit_file_sha256": sha(original / "partial_independent_audit.json"),
        "resume_manifest_file_sha256": sha(resume / "resume_manifest.json"),
        "resume_result_file_sha256": sha(resume / "resume_result.json"),
        "provider_calls": 0,
        "vitis_launches": 0,
        "git_history_mutations": 0,
        "r5_accepted": False,
        "r6_started": False,
    }
    audit["audit_sha256"] = hashlib.sha256(canonical(audit).encode()).hexdigest()
    return combined, audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--resume", type=Path, required=True)
    args = parser.parse_args()
    combined, result = audit(args.original.resolve(), args.resume.resolve())
    resume = args.resume.resolve()
    (resume / "combined_pilot_result_v2.json").write_text(
        json.dumps(combined, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (resume / "independent_audit_v2.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print("R5_COMBINED_PILOT_AUDIT_STATUS=" + result["status"])
    print("CRITICAL_FINDINGS=" + str(result["critical_finding_count"]))
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return 0 if result["status"] == "clean" else 1


if __name__ == "__main__":
    raise SystemExit(main())
