#!/usr/bin/env python3
"""Seal selected Stage II evidence without Provider or Vitis calls."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUTS = ROOT / "configs" / "inheritance" / "stage2" / "checkpoint_inputs.json"

COMMON_FILES = (
    "run_result.json",
    "full_result.json",
    "execution_identity.json",
    "model_calls.json",
    "tool_calls.json",
    "run_artifact_manifest.json",
    "stdout.log",
    "stderr.log",
    "bootstrap/effective_budget_contract.json",
    "bootstrap/effective_model_config.json",
    "bootstrap/effective_target_profile.json",
    "bootstrap/initial_generation_request.json",
    "bootstrap/source_request.json",
    "bootstrap/test_source_plan.json",
)

SUCCESS_FILES = (
    "bootstrap/formal_validation_request.json",
    "bootstrap/initial_candidate.cpp",
    "bootstrap/model_data_boundary.json",
    "bootstrap/normalized_task.json",
    "refactor/artifact_manifest.json",
    "refactor/effective_repair_quota.json",
    "refactor/final_candidate.cpp",
    "refactor/orchestration_result.json",
)


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


def require_hash(path: Path, expected: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"missing or non-regular evidence file: {path}")
    observed = file_sha256(path)
    if observed != expected:
        raise ValueError(f"evidence hash mismatch: {path}: {observed}")


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


def evidence_files(root: Path, success: bool) -> list[dict[str, Any]]:
    names = list(COMMON_FILES)
    if success:
        names.extend(SUCCESS_FILES)
    extraction = root / "bootstrap" / "generation_extraction_outcome.json"
    if extraction.is_file():
        names.append("bootstrap/generation_extraction_outcome.json")
    records = []
    for name in names:
        path = root / name
        if not path.is_file() or path.is_symlink():
            if name in {"stdout.log", "stderr.log"}:
                continue
            raise ValueError(f"required run evidence missing: {path}")
        records.append(
            {
                "relative_path": name,
                "sha256": file_sha256(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return records


def vitis_launches(usage: dict[str, Any]) -> int:
    return sum(int(usage.get(key, 0)) for key in ("csim_calls", "csynth_calls", "cosim_calls"))


def run_record(root: Path, success: bool, classification: str | None = None) -> dict[str, Any]:
    result = load_object(root / "run_result.json")
    identity = load_object(root / "execution_identity.json")
    usage = result.get("budget_usage", {})
    record: dict[str, Any] = {
        "classification": classification,
        "evidence_files": evidence_files(root, success),
        "execution_mode": result.get("metadata", {}).get("execution_mode"),
        "provider_calls": int(usage.get("llm_calls", 0)),
        "root": str(root),
        "run_id": result.get("run_id"),
        "status": result.get("status"),
        "vitis_launches": vitis_launches(usage),
    }
    if success:
        phase = result.get("phases", [{}])[-1]
        candidates = identity.get("candidates", {})
        record.update(
            {
                "accepted_ready": identity.get("completeness", {}).get("accepted_ready"),
                "candidate_sha256": candidates.get("final", {}).get("sha256"),
                "candidate_top": phase.get("metadata", {}).get("candidate_top_function"),
                "hidden_suite_count": phase.get("metadata", {}).get("hidden_suite_count"),
                "orchestration_status": phase.get("metadata", {}).get("orchestration_status"),
                "repair_attempt_count": phase.get("metadata", {}).get("repair_attempt_count"),
                "validation_state": phase.get("metadata", {}).get("last_validation_state"),
            }
        )
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--inputs", type=Path, default=DEFAULT_INPUTS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    inputs_path = args.inputs.resolve()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("output must be empty or absent")
    output.mkdir(parents=True, exist_ok=True)

    inputs = load_object(inputs_path)
    if inputs.get("schema_version") != 1:
        raise ValueError("unsupported checkpoint input schema")
    if git(repo, "branch", "--show-current") != inputs["expected_branch"]:
        raise ValueError("wrong branch")
    if git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("repository must be clean before sealing")
    protocol_path = repo / inputs["protocol_path"]
    require_hash(protocol_path, inputs["protocol_sha256"])
    protocol = load_object(protocol_path)
    if protocol.get("cohort_sha256") != inputs["stage1_cohort_sha256"]:
        raise ValueError("Stage II-B cohort identity changed")

    preflight = inputs["preflight"]
    require_hash(Path(preflight["failed_root"]) / "result.json", preflight["failed_result_sha256"])
    require_hash(Path(preflight["passed_root"]) / "result.json", preflight["passed_result_sha256"])
    baseline = inputs["baseline"]
    baseline_root = Path(baseline["root"])
    require_hash(baseline_root / "result.json", baseline["result_sha256"])
    require_hash(
        baseline_root / "artifact_manifest.json",
        baseline["artifact_manifest_sha256"],
    )

    selected_cases = []
    all_attempts = []
    for selection in inputs["cases"]:
        successful = selection["successful_run"]
        success_root = Path(successful["root"])
        require_hash(success_root / "run_result.json", successful["run_result_sha256"])
        require_hash(
            success_root / "run_artifact_manifest.json",
            successful["run_artifact_manifest_sha256"],
        )
        success_record = run_record(success_root, True)
        all_attempts.append(success_record)
        failed_records = []
        for failed in selection["failed_attempts"]:
            failed_root = Path(failed["root"])
            require_hash(failed_root / "run_result.json", failed["run_result_sha256"])
            require_hash(
                failed_root / "run_artifact_manifest.json",
                failed["run_artifact_manifest_sha256"],
            )
            failed_record = run_record(
                failed_root,
                False,
                failed["classification"],
            )
            failed_records.append(failed_record)
            all_attempts.append(failed_record)
        selected_cases.append(
            {
                "case_id": selection["case_id"],
                "failed_attempts": failed_records,
                "successful_run": success_record,
            }
        )

    baseline_result = load_object(baseline_root / "result.json")
    budget = inputs["budget"]
    provider_calls = sum(item["provider_calls"] for item in all_attempts) + int(
        budget.get("prior_provider_calls", 0)
    )
    baseline_vitis = sum(
        vitis_launches(case.get("budget_usage", {}))
        for case in baseline_result.get("cases", [])
    )
    refactor_vitis = sum(item["vitis_launches"] for item in all_attempts)
    vitis_calls = (
        baseline_vitis
        + refactor_vitis
        + int(budget.get("prior_vitis_launches", 0))
    )
    if provider_calls != budget["provider_calls_expected"]:
        raise ValueError(f"provider call count mismatch: {provider_calls}")
    if vitis_calls != budget["vitis_launches_expected"]:
        raise ValueError(f"Vitis launch count mismatch: {vitis_calls}")
    if provider_calls > budget["provider_calls_hard_cap"]:
        raise ValueError("Provider hard cap exceeded")
    if vitis_calls > budget["vitis_launches_hard_cap"]:
        raise ValueError("Vitis hard cap exceeded")

    manifest = {
        "baseline": {
            "artifact_manifest_sha256": baseline["artifact_manifest_sha256"],
            "cases": [
                {
                    "case_id": case.get("case_id"),
                    "classification": case.get("classification"),
                    "source_sha256": case.get("source_sha256"),
                    "stage_statuses": case.get("stage_statuses"),
                    "terminal_state": case.get("terminal_state"),
                }
                for case in baseline_result.get("cases", [])
            ],
            "result_sha256": baseline["result_sha256"],
            "root": str(baseline_root),
        },
        "budget": {
            "provider_calls": provider_calls,
            "provider_calls_hard_cap": budget["provider_calls_hard_cap"],
            "vitis_launches": vitis_calls,
            "vitis_launches_hard_cap": budget["vitis_launches_hard_cap"],
        },
        "cases": selected_cases,
        "checkpoint_id": inputs["checkpoint_id"],
        "execution_boundary": protocol["execution"],
        "git": {
            "branch": git(repo, "branch", "--show-current"),
            "head": git(repo, "rev-parse", "HEAD"),
            "status_porcelain": "",
        },
        "outcome": {
            "blocked_count": 0,
            "ordinary_refactor_success_count": len(selected_cases),
            "regression_count": 0,
            "source_baseline_full_pass_count": 0,
        },
        "preflight": {
            "failed_result_sha256": preflight["failed_result_sha256"],
            "failed_root": preflight["failed_root"],
            "passed_result_sha256": preflight["passed_result_sha256"],
            "passed_root": preflight["passed_root"],
        },
        "product_fix_commit": inputs["product_fix_commit"],
        "protocol": {
            "cohort_sha256": protocol["cohort_sha256"],
            "path": inputs["protocol_path"],
            "protocol_id": protocol["protocol_id"],
            "sha256": inputs["protocol_sha256"],
        },
        "schema_version": 1,
        "seal_process": {
            "git_history_mutations": 0,
            "provider_calls": 0,
            "vitis_launches": 0,
        },
        "status": "sealed_for_independent_audit",
    }
    manifest_path = output / "campaign_manifest.json"
    write_object(manifest_path, manifest)
    print("INHERITANCE_STAGE2_SEAL_STATUS=passed")
    print(f"CAMPAIGN_MANIFEST_SHA256={file_sha256(manifest_path)}")
    print(f"PROVIDER_CALLS={provider_calls}")
    print(f"VITIS_LAUNCHES={vitis_calls}")
    print("SEAL_PROVIDER_CALLS=0")
    print("SEAL_VITIS_LAUNCHES=0")
    print("GIT_HISTORY_MUTATIONS=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
