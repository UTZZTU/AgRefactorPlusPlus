#!/usr/bin/env python3
"""Independently audit sealed Stage II evidence using persisted artifacts only."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUTS = ROOT / "configs" / "inheritance" / "stage2" / "checkpoint_inputs.json"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def write_object(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "git command failed")
    return completed.stdout.strip()


class Audit:
    def __init__(self) -> None:
        self.checks: dict[str, bool] = {}
        self.failures: list[str] = []

    def check(self, name: str, condition: bool) -> None:
        passed = bool(condition)
        self.checks[name] = self.checks.get(name, True) and passed
        if not passed:
            self.failures.append(name)


def candidate_steps(orchestration: dict[str, Any]) -> list[str]:
    validation = orchestration.get("result", {}).get("initial_validation", {})
    return [step.get("state") for step in validation.get("steps", [])]


def private_reasoning_absent(model_calls: dict[str, Any]) -> bool:
    if model_calls.get("plaintext_prompts_persisted"):
        return False
    if model_calls.get("plaintext_responses_persisted"):
        return False
    for call in model_calls.get("optimizer_calls", []):
        metadata = call.get("metadata", {})
        if metadata.get("private_reasoning_persisted"):
            return False
        transport = metadata.get("legacy_ag2_transport", {})
        if transport.get("private_reasoning_persisted"):
            return False
    return True


def stable_successful_runs(runs: list[dict[str, Any]]) -> bool:
    return (
        len(runs) == 2
        and all(item.get("valid") is True for item in runs)
        and runs[0].get("returncode") == 0
        and runs[1].get("returncode") == 0
        and runs[0].get("stdout") == runs[1].get("stdout")
    )


def verify_evidence_files(audit: Audit, record: dict[str, Any], prefix: str) -> None:
    root = Path(record["root"])
    for item in record.get("evidence_files", []):
        path = root / item["relative_path"]
        audit.check(f"{prefix}:regular:{item['relative_path']}", path.is_file() and not path.is_symlink())
        if path.is_file() and not path.is_symlink():
            audit.check(f"{prefix}:hash:{item['relative_path']}", file_sha256(path) == item["sha256"])
            audit.check(f"{prefix}:size:{item['relative_path']}", path.stat().st_size == item["size_bytes"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--inputs", type=Path, default=DEFAULT_INPUTS)
    parser.add_argument("--checkpoint", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    inputs = load_object(args.inputs.resolve())
    checkpoint = args.checkpoint.resolve()
    manifest_path = checkpoint / "campaign_manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("sealed campaign manifest is missing")
    if (checkpoint / "audit_result.json").exists():
        raise ValueError("audit result already exists; use a new checkpoint directory")

    manifest = load_object(manifest_path)
    protocol_path = repo / inputs["protocol_path"]
    protocol = load_object(protocol_path)
    protocol_cases = {item["case_id"]: item for item in protocol["cases"]}
    audit = Audit()

    audit.check("schema", manifest.get("schema_version") == 1)
    audit.check("checkpoint_identity", manifest.get("checkpoint_id") == inputs["checkpoint_id"])
    audit.check("branch", manifest.get("git", {}).get("branch") == inputs["expected_branch"])
    audit.check("current_branch", git(repo, "branch", "--show-current") == inputs["expected_branch"])
    audit.check("repository_clean", not git(repo, "status", "--porcelain", "--untracked-files=all"))
    audit.check("protocol_hash", file_sha256(protocol_path) == inputs["protocol_sha256"])
    audit.check("cohort_hash", protocol.get("cohort_sha256") == inputs["stage1_cohort_sha256"])
    audit.check(
        "ordinary_refactor_boundary",
        protocol.get("execution")
        == {
            "candidate_top_suffix": "_hls",
            "cosim_policy": "required",
            "hidden_tests": "none",
            "memory_enabled": False,
            "mode": "refactor",
            "optimize_enabled": False,
            "r2_r4_enabled": False,
        },
    )

    preflight = manifest["preflight"]
    audit.check(
        "failed_preflight_immutable",
        file_sha256(Path(preflight["failed_root"]) / "result.json")
        == preflight["failed_result_sha256"],
    )
    passed_preflight_path = Path(preflight["passed_root"]) / "result.json"
    audit.check(
        "passed_preflight_immutable",
        file_sha256(passed_preflight_path) == preflight["passed_result_sha256"],
    )
    passed_preflight = load_object(passed_preflight_path)
    audit.check("preflight_passed", passed_preflight.get("status") == "passed")
    audit.check("preflight_zero_provider", passed_preflight.get("provider_calls") == 0)
    audit.check("preflight_zero_vitis", passed_preflight.get("vitis_launches") == 0)
    for case in passed_preflight.get("cases", []):
        cid = case.get("case_id", "unknown")
        audit.check(
            f"{cid}:source_oracle_stable",
            case.get("source_compile", {}).get("valid") is True
            and stable_successful_runs(case.get("source_runs", [])),
        )
        audit.check(
            f"{cid}:differential_oracle_stable",
            case.get("differential_compile", {}).get("valid") is True
            and stable_successful_runs(case.get("differential_runs", [])),
        )
        mismatch = case.get("mismatch_run", {})
        audit.check(
            f"{cid}:mismatch_detected",
            case.get("mismatch_compile", {}).get("valid") is True
            and mismatch.get("valid") is True
            and mismatch.get("returncode") == 1
            and mismatch.get("marker_observed") is False,
        )

    baseline_path = Path(manifest["baseline"]["root"]) / "result.json"
    audit.check("baseline_immutable", file_sha256(baseline_path) == manifest["baseline"]["result_sha256"])
    baseline = load_object(baseline_path)
    audit.check("baseline_complete", baseline.get("status") == "complete")
    baseline_cases = {item["case_id"]: item for item in baseline.get("cases", [])}
    audit.check("baseline_case_set", set(baseline_cases) == set(protocol_cases))
    baseline_vitis = 0
    for case_id, case in baseline_cases.items():
        contract = protocol_cases[case_id]
        stages = case.get("stage_statuses", {})
        audit.check(f"{case_id}:source_hash", case.get("source_sha256") == contract["source_sha256"])
        audit.check(f"{case_id}:source_still_unchanged", file_sha256(repo / contract["source_path"]) == contract["source_sha256"])
        audit.check(f"{case_id}:source_host_oracle", stages.get("S0_host_oracle") == "passed")
        audit.check(f"{case_id}:source_csim", stages.get("S1_public_csim") == "passed")
        audit.check(f"{case_id}:source_csynth_failed", stages.get("S2_csynth") == "failed")
        audit.check(f"{case_id}:source_cosim_not_run", stages.get("S3_public_cosim") == "not_run")
        audit.check(f"{case_id}:source_terminal_csynth", case.get("terminal_state") == "csynth")
        audit.check(f"{case_id}:source_failure_blocking", case.get("terminal_feedback", {}).get("blocking") is True)
        usage = case.get("budget_usage", {})
        baseline_vitis += sum(int(usage.get(key, 0)) for key in ("csim_calls", "csynth_calls", "cosim_calls"))

    input_budget = inputs["budget"]
    provider_calls = int(input_budget.get("prior_provider_calls", 0))
    refactor_vitis = int(input_budget.get("prior_vitis_launches", 0))
    case_ids = []
    for case_record in manifest.get("cases", []):
        case_id = case_record["case_id"]
        case_ids.append(case_id)
        contract = protocol_cases[case_id]
        success = case_record["successful_run"]
        prefix = f"{case_id}:success"
        verify_evidence_files(audit, success, prefix)
        root = Path(success["root"])
        result = load_object(root / "run_result.json")
        identity = load_object(root / "execution_identity.json")
        orchestration = load_object(root / "refactor" / "orchestration_result.json")
        phase_manifest = load_object(root / "refactor" / "artifact_manifest.json")
        model_calls = load_object(root / "model_calls.json")
        phase = result.get("phases", [{}])[-1]
        phase_meta = phase.get("metadata", {})
        normalized = identity.get("normalized_task", {}).get("value", {})
        suites = normalized.get("test_suites", [])
        usage = result.get("budget_usage", {})
        provider_calls += int(usage.get("llm_calls", 0))
        refactor_vitis += sum(int(usage.get(key, 0)) for key in ("csim_calls", "csynth_calls", "cosim_calls"))

        audit.check(f"{prefix}:status", result.get("status") == "succeeded" and result.get("succeeded") is True)
        audit.check(f"{prefix}:mode", result.get("mode") == "refactor")
        audit.check(f"{prefix}:execution_mode", result.get("metadata", {}).get("execution_mode") == "source_bootstrap")
        audit.check(f"{prefix}:phase", phase.get("status") == "succeeded")
        audit.check(f"{prefix}:orchestration", phase_meta.get("orchestration_status") == "accepted")
        audit.check(f"{prefix}:validation", phase_meta.get("last_validation_state") == "accepted")
        audit.check(f"{prefix}:repair_count", phase_meta.get("repair_attempt_count") == 0)
        audit.check(f"{prefix}:hidden_absent", phase_meta.get("hidden_suite_count") == 0)
        audit.check(f"{prefix}:r2_disabled", orchestration.get("result", {}).get("metadata", {}).get("r2_shadow_enabled") is False)
        audit.check(f"{prefix}:memory_disabled", orchestration.get("result", {}).get("metadata", {}).get("r5_arm") is None)
        audit.check(f"{prefix}:formal_stages", candidate_steps(orchestration) == ["preflight", "public_evaluation", "csynth", "public_cosim"])
        audit.check(f"{prefix}:phase_manifest", phase_manifest.get("accepted") is True and phase_manifest.get("orchestration_status") == "accepted")
        audit.check(f"{prefix}:accepted_ready", identity.get("completeness", {}).get("accepted_ready") is True)
        audit.check(f"{prefix}:identity_complete", all(identity.get("completeness", {}).values()))
        candidate = identity.get("candidates", {}).get("final", {})
        audit.check(f"{prefix}:candidate_hash", file_sha256(root / "refactor" / "final_candidate.cpp") == candidate.get("sha256"))
        audit.check(f"{prefix}:candidate_changed", candidate.get("sha256") != contract["source_sha256"])
        audit.check(f"{prefix}:candidate_top", phase_meta.get("candidate_top_function") == f"{contract['top']}_hls")
        audit.check(f"{prefix}:normalized_mode", normalized.get("mode") == "refactor")
        audit.check(f"{prefix}:toolchain", normalized.get("target", {}).get("toolchain_version") == "2023.2")
        audit.check(f"{prefix}:public_only", len(suites) == 1 and suites[0].get("split") == "public")
        if suites:
            audit.check(
                f"{prefix}:public_hash",
                suites[0].get("source", {}).get("expected_content_sha256")
                == contract["public_test_sha256"],
            )
        model = identity.get("model", {}).get("value", {})
        audit.check(f"{prefix}:model", model.get("model_id") == protocol["model"]["name"])
        audit.check(f"{prefix}:family", model.get("requested_family_name") == protocol["model"]["family"])
        audit.check(f"{prefix}:privacy", private_reasoning_absent(model_calls))
        audit.check(f"{prefix}:vitis_stages", usage.get("csim_calls") == 1 and usage.get("csynth_calls") == 1 and usage.get("cosim_calls") == 1)

        for index, failed in enumerate(case_record.get("failed_attempts", []), start=1):
            failed_prefix = f"{case_id}:failed:{index}"
            verify_evidence_files(audit, failed, failed_prefix)
            failed_result = load_object(Path(failed["root"]) / "run_result.json")
            failed_usage = failed_result.get("budget_usage", {})
            provider_calls += int(failed_usage.get("llm_calls", 0))
            refactor_vitis += sum(int(failed_usage.get(key, 0)) for key in ("csim_calls", "csynth_calls", "cosim_calls"))
            audit.check(f"{failed_prefix}:not_success", failed_result.get("status") != "succeeded")
            audit.check(f"{failed_prefix}:classification", bool(failed.get("classification")))

    expected_case_ids = [item["case_id"] for item in inputs["cases"]]
    audit.check("case_order", case_ids == expected_case_ids)
    budget = manifest.get("budget", {})
    total_vitis = baseline_vitis + refactor_vitis
    audit.check("provider_count", provider_calls == budget.get("provider_calls") == inputs["budget"]["provider_calls_expected"])
    audit.check("vitis_count", total_vitis == budget.get("vitis_launches") == inputs["budget"]["vitis_launches_expected"])
    audit.check("provider_cap", provider_calls <= inputs["budget"]["provider_calls_hard_cap"])
    audit.check("vitis_cap", total_vitis <= inputs["budget"]["vitis_launches_hard_cap"])
    audit.check(
        "success_count",
        manifest.get("outcome", {}).get("ordinary_refactor_success_count")
        == len(expected_case_ids),
    )
    audit.check("regression_count", manifest.get("outcome", {}).get("regression_count") == 0)
    audit.check("blocked_count", manifest.get("outcome", {}).get("blocked_count") == 0)

    audit_result = {
        "audit_id": "v2.3-inheritance-first-stage2-independent-audit-v1",
        "campaign_manifest_sha256": file_sha256(manifest_path),
        "checks": audit.checks,
        "critical_findings": len(audit.failures),
        "failures": audit.failures,
        "git_history_mutations": 0,
        "provider_calls": 0,
        "schema_version": 1,
        "status": "passed" if not audit.failures else "failed",
        "vitis_launches": 0,
    }
    audit_path = checkpoint / "audit_result.json"
    write_object(audit_path, audit_result)
    artifacts = {
        "artifact_id": "v2.3-inheritance-first-stage2-checkpoint-artifacts-v1",
        "files": [
            {
                "relative_path": "campaign_manifest.json",
                "sha256": file_sha256(manifest_path),
                "size_bytes": manifest_path.stat().st_size,
            },
            {
                "relative_path": "audit_result.json",
                "sha256": file_sha256(audit_path),
                "size_bytes": audit_path.stat().st_size,
            },
        ],
        "schema_version": 1,
    }
    artifact_path = checkpoint / "artifact_manifest.json"
    write_object(artifact_path, artifacts)
    print(f"INHERITANCE_STAGE2_AUDIT_STATUS={audit_result['status']}")
    print(f"CRITICAL_FINDINGS={len(audit.failures)}")
    print(f"AUDIT_RESULT_SHA256={file_sha256(audit_path)}")
    print(f"ARTIFACT_MANIFEST_SHA256={file_sha256(artifact_path)}")
    print("AUDITOR_PROVIDER_CALLS=0")
    print("AUDITOR_VITIS_LAUNCHES=0")
    print("GIT_HISTORY_MUTATIONS=0")
    return 0 if not audit.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
