#!/usr/bin/env python3
"""Zero-call audit for the one remaining post-fix R5 history attempt."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = Path(__file__).resolve().parent
for value in (str(REPOSITORY_ROOT), str(SCRIPT_ROOT)):
    if value not in sys.path:
        sys.path.insert(0, value)

from agrefactor.recovery.r5_historical_candidate import canonical_sha256, file_sha256
from agrefactor.recovery.r5_preexisting_history_resume import (
    verify_preexisting_history_resume_plan,
)
from r5_audit_preexisting_history_continuation import (  # type: ignore[import-not-found]
    _compile_and_run,
)


class PreexistingHistoryResumeAuditError(RuntimeError):
    """Raised when the remaining attempt cannot be authorized safely."""


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise PreexistingHistoryResumeAuditError(
            f"invalid JSON evidence: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise PreexistingHistoryResumeAuditError(
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
        raise PreexistingHistoryResumeAuditError(
            "git command failed: " + " ".join(args)
        )
    return completed.stdout.strip()


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
    bundle = verify_preexisting_history_resume_plan(repository, plan)
    head = _git(repository, "rev-parse", "HEAD")
    branch = _git(repository, "branch", "--show-current")
    status = _git(repository, "status", "--porcelain", "--untracked-files=all")
    if branch != "research-roadmap-v2.3" or status:
        raise PreexistingHistoryResumeAuditError(
            "resume audit requires the clean R5 branch"
        )
    fix_commit = str(plan["contract_fix_commit"])
    ancestor = subprocess.run(
        ["git", "-C", str(repository), "merge-base", "--is-ancestor", fix_commit, head],
        check=False,
        capture_output=True,
    )
    if ancestor.returncode != 0:
        raise PreexistingHistoryResumeAuditError(
            "contract fix is not an ancestor of the audited head"
        )
    if (
        state.get("R4_ACCEPTED") is not True
        or state.get("R5_STARTED") is not True
        or state.get("R5_ACCEPTED") is not False
        or state.get("R6_STARTED") is not False
        or state.get("R5_REAL_CAMPAIGN_ALLOWED") is not False
        or state.get("R5_PREDECESSOR_LIFECYCLE") != "Provisional"
        or state.get("R5_PREEXISTING_HISTORY_CONTINUATION_STATUS")
        != "audited_pre_provider_contract_failure_fixed"
        or state.get("R5_PREEXISTING_HISTORY_CONTINUATION_REMAINING_ATTEMPTS") != 1
        or state.get("R5_CONSUMED_PROVIDER_CALLS") != 95
        or state.get("R5_CONSUMED_VITIS_LAUNCHES") != 37
        or state.get("R5_PROVIDER_CALL_HARD_CAP") != 500
        or state.get("R5_VITIS_LAUNCH_HARD_CAP") != 500
    ):
        raise PreexistingHistoryResumeAuditError(
            "roadmap state does not permit the remaining attempt"
        )
    candidate = bundle.continuation_bundle.candidate
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
    code_files = {
        "runner_file_sha256": repository
        / "scripts"
        / "r5_resume_preexisting_history.py",
        "acquisition_engine_file_sha256": repository
        / "scripts"
        / "r5_acquire_preexisting_history.py",
        "resume_module_file_sha256": repository
        / "agrefactor"
        / "recovery"
        / "r5_preexisting_history_resume.py",
        "r5_integration_file_sha256": repository
        / "agrefactor"
        / "runtime"
        / "r5_integration.py",
    }
    result: dict[str, Any] = {
        "schema_version": 1,
        "audit_id": "v2.3-r5-preexisting-history-post-fix-resume-zero-call-audit-v1",
        "status": "ready_for_one_post_fix_history_attempt",
        "repository_head": head,
        "repository_branch": branch,
        "contract_fix_commit": fix_commit,
        "contract_fix_is_ancestor": True,
        "plan_path": str(plan_path),
        "plan_file_sha256": file_sha256(plan_path),
        "plan_sha256": bundle.plan_sha256,
        "state_file_sha256": file_sha256(state_path),
        **{key: file_sha256(path) for key, path in code_files.items()},
        "case_identity": candidate.to_identity(),
        "failed_continuation_audit_file_sha256": file_sha256(
            bundle.failed_audit_path
        ),
        "failed_continuation_audit_sha256": bundle.failed_audit["audit_sha256"],
        "failure_reconciliation_file_sha256": file_sha256(
            bundle.reconciliation_path
        ),
        "host_adapter_checks": host_checks,
        "attempts_authorized_originally": 2,
        "attempts_consumed": 1,
        "attempt_ordinal": 3,
        "maximum_remaining_attempts": 1,
        "maximum_candidate_mutations": 1,
        "provider_calls_before": 95,
        "vitis_launches_before": 37,
        "provider_call_upper_bound": 2,
        "vitis_launch_upper_bound": 6,
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
        "next_step": "run_exactly_one_post_fix_history_attempt",
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
            "configs/r5/preexisting_history/recursive_e2_dfs_post_fix_resume_plan.json"
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
    print("R5_PREEXISTING_HISTORY_RESUME_AUDIT_STATUS=" + result["status"])
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
