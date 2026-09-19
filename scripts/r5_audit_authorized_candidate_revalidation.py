#!/usr/bin/env python3
"""Zero-call audit for an R5 content-addressed Candidate revalidation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from agrefactor.recovery.r5_authorized_revalidation import (
    verify_authorized_revalidation_plan,
)
from agrefactor.recovery.r5_historical_candidate import (
    canonical_sha256,
    file_sha256,
)


class R5AuthorizedRevalidationAuditError(RuntimeError):
    """Raised when the zero-call revalidation boundary is unsafe."""


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise R5AuthorizedRevalidationAuditError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise R5AuthorizedRevalidationAuditError(f"JSON root is invalid: {path}")
    return value


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise R5AuthorizedRevalidationAuditError("git command failed")
    return completed.stdout.strip()


def _host_check(
    *,
    compiler: str,
    reference_code: str,
    candidate_code: str,
    test_code: str,
    split: str,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="r5-authorized-revalidation-") as raw:
        root = Path(raw)
        reference = root / "reference.cpp"
        candidate = root / "candidate.cpp"
        test = root / "test.cpp"
        executable = root / "oracle"
        reference.write_text(reference_code, encoding="utf-8")
        candidate.write_text(candidate_code, encoding="utf-8")
        test.write_text(test_code, encoding="utf-8")
        compiled = subprocess.run(
            [
                compiler,
                "-std=c++17",
                "-O0",
                str(reference),
                str(candidate),
                str(test),
                "-o",
                str(executable),
            ],
            check=False,
            capture_output=True,
            timeout=60,
        )
        if compiled.returncode:
            raise R5AuthorizedRevalidationAuditError(
                f"{split} host oracle compile failed"
            )
        executed = subprocess.run(
            [str(executable)],
            check=False,
            capture_output=True,
            timeout=30,
        )
        if executed.returncode:
            raise R5AuthorizedRevalidationAuditError(
                f"{split} host oracle rejected sealed Candidate"
            )
        return {
            "split": split,
            "compile_returncode": compiled.returncode,
            "run_returncode": executed.returncode,
            "stdout_sha256": hashlib.sha256(executed.stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(executed.stderr).hexdigest(),
            "provider_calls": 0,
            "vitis_launches": 0,
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
    bundle = verify_authorized_revalidation_plan(repository, plan)
    head = _git(repository, "rev-parse", "HEAD")
    branch = _git(repository, "branch", "--show-current")
    status = _git(repository, "status", "--porcelain", "--untracked-files=all")
    if branch != "research-roadmap-v2.3" or status:
        raise R5AuthorizedRevalidationAuditError(
            "protocol audit requires the clean R5 branch"
        )
    budget = plan["budget"]
    if (
        state.get("R4_ACCEPTED") is not True
        or state.get("R5_STARTED") is not True
        or state.get("R5_ACCEPTED") is not False
        or state.get("R6_STARTED") is not False
        or state.get("R5_REAL_CAMPAIGN_ALLOWED") is not False
        or state.get("R5_CONSUMED_PROVIDER_CALLS")
        != budget["provider_calls_before"]
        or state.get("R5_CONSUMED_VITIS_LAUNCHES")
        != budget["vitis_launches_before"]
        or state.get("R5_PROVIDER_CALL_HARD_CAP") != 500
        or state.get("R5_VITIS_LAUNCH_HARD_CAP") != 500
        or state.get("R5_C2HLSC_QUICKSORT_REMAINING_ATTEMPTS") != 0
        or state.get("R5_C2HLSC_QUICKSORT_CANDIDATE_AFTER_SHA256")
        != bundle.candidate_after_sha256
        or state.get("R5_PREDECESSOR_LIFECYCLE") != "Provisional"
    ):
        raise R5AuthorizedRevalidationAuditError(
            "roadmap state does not permit sealed Candidate revalidation"
        )
    host_checks = [
        _host_check(
            compiler=compiler,
            reference_code=bundle.reference_code,
            candidate_code=bundle.candidate_code,
            test_code=bundle.public_test_code,
            split="Public",
        ),
        _host_check(
            compiler=compiler,
            reference_code=bundle.reference_code,
            candidate_code=bundle.candidate_code,
            test_code=bundle.hidden_test_code,
            split="Hidden",
        ),
    ]
    result: dict[str, Any] = {
        "schema_version": 1,
        "audit_id": "v2.3-r5-authorized-candidate-revalidation-zero-call-v1",
        "status": "ready_for_authorized_candidate_revalidation",
        "repository_head": head,
        "repository_branch": branch,
        "plan_path": str(plan_path),
        "plan_file_sha256": file_sha256(plan_path),
        "plan_sha256": bundle.plan_sha256,
        "state_file_sha256": file_sha256(state_path),
        "case_identity": bundle.identity(),
        "host_oracle_checks": host_checks,
        "original_authorization_verified": True,
        "sealed_candidate_verified": True,
        "prior_configuration_failure_verified": True,
        "fresh_validation_required": True,
        "provider_calls_before": budget["provider_calls_before"],
        "vitis_launches_before": budget["vitis_launches_before"],
        "provider_call_upper_bound": 0,
        "vitis_launch_upper_bound": 3,
        "provider_calls": 0,
        "vitis_launches": 0,
        "future_holdout_case_ids": list(plan["future_holdout_case_ids"]),
        "future_files_read": False,
        "future_outcomes_observed": False,
        "original_episode_mutated": False,
        "trusted_revision_creation_allowed": False,
        "r5_real_campaign_allowed": False,
        "critical_finding_count": 0,
        "findings": [],
        "r5_accepted": False,
        "r6_started": False,
        "next_step": "run_exact_sealed_candidate_through_existing_full_validation",
    }
    result["audit_sha256"] = canonical_sha256(result)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name("." + output_path.name + ".tmp")
    temporary.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, output_path)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument(
        "--state", type=Path, default=Path("docs/roadmap/V2_3_STATE.json")
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
    print("R5_AUTHORIZED_REVALIDATION_AUDIT_STATUS=" + result["status"])
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
