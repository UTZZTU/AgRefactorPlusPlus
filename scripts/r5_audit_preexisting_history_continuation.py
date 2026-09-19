#!/usr/bin/env python3
"""Zero-call audit for the bounded R5 pre-existing-history continuation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from agrefactor.recovery.r5_historical_candidate import canonical_sha256, file_sha256
from agrefactor.recovery.r5_preexisting_history_continuation import (
    verify_preexisting_history_continuation_plan,
)


class PreexistingHistoryContinuationAuditError(RuntimeError):
    """Raised when continuation cannot be authorized without real calls."""


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise PreexistingHistoryContinuationAuditError(
            f"invalid JSON evidence: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise PreexistingHistoryContinuationAuditError(
            f"JSON evidence is not an object: {path}"
        )
    return value


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise PreexistingHistoryContinuationAuditError(
            "git command failed: " + " ".join(args)
        )
    return completed.stdout.strip()


def _compile_and_run(
    *,
    compiler: str,
    reference_code: str,
    candidate_code: str,
    test_code: str,
    split: str,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="r5-history-continuation-audit-") as tmp:
        root = Path(tmp)
        paths = {
            "reference": root / "reference.cpp",
            "candidate": root / "candidate.cpp",
            "test": root / f"{split.casefold()}.cpp",
        }
        paths["reference"].write_text(reference_code, encoding="utf-8")
        paths["candidate"].write_text(candidate_code, encoding="utf-8")
        paths["test"].write_text(test_code, encoding="utf-8")
        executable = root / "oracle"
        compiled = subprocess.run(
            [
                compiler,
                "-std=c++17",
                "-O0",
                str(paths["reference"]),
                str(paths["candidate"]),
                str(paths["test"]),
                "-o",
                str(executable),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        if compiled.returncode:
            raise PreexistingHistoryContinuationAuditError(
                f"{split} host adapter compile failed"
            )
        executed = subprocess.run(
            [str(executable)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        if executed.returncode:
            raise PreexistingHistoryContinuationAuditError(
                f"{split} host differential oracle failed"
            )
        return {
            "split": split,
            "compile_returncode": compiled.returncode,
            "run_returncode": executed.returncode,
            "stdout_sha256": hashlib.sha256(executed.stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(executed.stderr).hexdigest(),
            "source_persisted": False,
        }


def audit(
    *,
    repository: Path,
    plan_path: Path,
    state_path: Path,
    output_path: Path,
    compiler: str,
) -> dict[str, Any]:
    repository = repository.expanduser().resolve()
    plan_path = plan_path.expanduser().resolve()
    state_path = state_path.expanduser().resolve()
    plan = _load(plan_path)
    state = _load(state_path)
    bundle = verify_preexisting_history_continuation_plan(repository, plan)
    head = _git(repository, "rev-parse", "HEAD")
    branch = _git(repository, "branch", "--show-current")
    status = _git(repository, "status", "--porcelain", "--untracked-files=all")
    if branch != "research-roadmap-v2.3" or status:
        raise PreexistingHistoryContinuationAuditError(
            "continuation audit requires the clean R5 branch"
        )
    if (
        state.get("R4_ACCEPTED") is not True
        or state.get("R5_STARTED") is not True
        or state.get("R5_ACCEPTED") is not False
        or state.get("R6_STARTED") is not False
        or state.get("R5_REAL_CAMPAIGN_ALLOWED") is not False
        or state.get("R5_PREDECESSOR_LIFECYCLE") != "Provisional"
        or state.get("R5_PREEXISTING_HISTORY_STATUS")
        != "clean_safe_calibration_abstention"
        or state.get("R5_PREEXISTING_HISTORY_VERIFIED_POSITIVE_EPISODE_COUNT") != 0
        or state.get("R5_CONSUMED_PROVIDER_CALLS") != 94
        or state.get("R5_CONSUMED_VITIS_LAUNCHES") != 35
        or state.get("R5_PROVIDER_CALL_HARD_CAP") != 500
        or state.get("R5_VITIS_LAUNCH_HARD_CAP") != 500
    ):
        raise PreexistingHistoryContinuationAuditError(
            "roadmap state does not permit continuation"
        )
    candidate = bundle.candidate
    host_checks = [
        _compile_and_run(
            compiler=compiler,
            reference_code=candidate.reference_code,
            candidate_code=candidate.isolated_candidate_code,
            test_code=candidate.public_test_code,
            split="Public",
        ),
        _compile_and_run(
            compiler=compiler,
            reference_code=candidate.reference_code,
            candidate_code=candidate.isolated_candidate_code,
            test_code=candidate.hidden_test_code,
            split="Hidden",
        ),
    ]
    runner_path = repository / "scripts" / "r5_continue_preexisting_history.py"
    engine_path = repository / "scripts" / "r5_acquire_preexisting_history.py"
    module_path = (
        repository
        / "agrefactor"
        / "recovery"
        / "r5_preexisting_history_continuation.py"
    )
    result: dict[str, Any] = {
        "schema_version": 1,
        "audit_id": "v2.3-r5-preexisting-history-continuation-zero-call-audit-v1",
        "status": "ready_for_preexisting_history_continuation",
        "repository_head": head,
        "repository_branch": branch,
        "plan_path": str(plan_path),
        "plan_file_sha256": file_sha256(plan_path),
        "plan_sha256": bundle.plan_sha256,
        "state_file_sha256": file_sha256(state_path),
        "runner_file_sha256": file_sha256(runner_path),
        "acquisition_engine_file_sha256": file_sha256(engine_path),
        "continuation_module_file_sha256": file_sha256(module_path),
        "case_identity": candidate.to_identity(),
        "prior_evidence_archive_sha256": bundle.prior_audit[
            "evidence_archive_sha256"
        ],
        "prior_independent_audit_file_sha256": file_sha256(
            bundle.prior_audit_path
        ),
        "prior_independent_audit_sha256": bundle.prior_audit["audit_sha256"],
        "prior_status": bundle.prior_audit["status"],
        "host_adapter_checks": host_checks,
        "maximum_additional_attempts": 2,
        "stop_after_first_verified_positive": True,
        "maximum_candidate_mutations_per_attempt": 1,
        "provider_calls_before": 94,
        "vitis_launches_before": 35,
        "provider_call_upper_bound_per_attempt": 2,
        "vitis_launch_upper_bound_per_attempt": 6,
        "provider_call_upper_bound_total": 4,
        "vitis_launch_upper_bound_total": 12,
        "provider_hard_cap": 500,
        "vitis_hard_cap": 500,
        "confidence_minimum_authorized_label": "high",
        "confidence_threshold_weakened": False,
        "future_holdout_case_ids": list(plan["future_holdout_case_ids"]),
        "future_files_read": False,
        "future_outcomes_observed": False,
        "trusted_revision_creation_allowed": False,
        "r5_real_campaign_allowed": False,
        "provider_calls": 0,
        "vitis_launches": 0,
        "critical_finding_count": 0,
        "findings": [],
        "r5_accepted": False,
        "r6_started": False,
        "next_step": "run_bounded_same_source_continuation",
    }
    result["audit_sha256"] = canonical_sha256(result)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name("." + output_path.name + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, output_path)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--plan",
        type=Path,
        default=Path(
            "configs/r5/preexisting_history/recursive_e2_dfs_continuation_plan.json"
        ),
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=Path("docs/roadmap/V2_3_STATE.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compiler", default="g++")
    args = parser.parse_args(argv)
    result = audit(
        repository=args.repo,
        plan_path=args.plan,
        state_path=args.state,
        output_path=args.output,
        compiler=args.compiler,
    )
    print("R5_PREEXISTING_HISTORY_CONTINUATION_AUDIT_STATUS=" + result["status"])
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
