#!/usr/bin/env python3
"""Zero-call audit for one frozen pre-existing R5 history Candidate."""

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

from agrefactor.recovery.r5_historical_candidate import (
    canonical_sha256,
    file_sha256,
    verify_historical_candidate_plan,
)


class PreexistingHistoryAuditError(RuntimeError):
    """Raised when the frozen history acquisition cannot run safely."""


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise PreexistingHistoryAuditError(f"invalid JSON evidence: {path}") from exc
    if not isinstance(value, dict):
        raise PreexistingHistoryAuditError(f"JSON root is not an object: {path}")
    return value


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise PreexistingHistoryAuditError("git command failed: " + " ".join(args))
    return completed.stdout.strip()


def _compile_and_run(
    *,
    compiler: str,
    reference_code: str,
    candidate_code: str,
    test_code: str,
    split: str,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="r5-history-audit-") as temporary:
        root = Path(temporary)
        reference = root / "reference.cpp"
        candidate = root / "candidate.cpp"
        test = root / f"{split.casefold()}.cpp"
        executable = root / "oracle"
        reference.write_text(reference_code, encoding="utf-8")
        candidate.write_text(candidate_code, encoding="utf-8")
        test.write_text(test_code, encoding="utf-8")
        compile_result = subprocess.run(
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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        if compile_result.returncode != 0:
            raise PreexistingHistoryAuditError(
                f"{split} host adapter compile failed"
            )
        run_result = subprocess.run(
            [str(executable)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        if run_result.returncode != 0:
            raise PreexistingHistoryAuditError(
                f"{split} host differential oracle failed"
            )
        return {
            "split": split,
            "compile_returncode": compile_result.returncode,
            "run_returncode": run_result.returncode,
            "stdout_sha256": hashlib.sha256(run_result.stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(run_result.stderr).hexdigest(),
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
    bundle = verify_historical_candidate_plan(repository, plan)

    head = _git(repository, "rev-parse", "HEAD")
    branch = _git(repository, "branch", "--show-current")
    status = _git(repository, "status", "--porcelain", "--untracked-files=all")
    if branch != "research-roadmap-v2.3" or status:
        raise PreexistingHistoryAuditError("protocol audit requires the clean R5 branch")
    if (
        state.get("R4_ACCEPTED") is not True
        or state.get("R5_STARTED") is not True
        or state.get("R5_ACCEPTED") is not False
        or state.get("R6_STARTED") is not False
        or state.get("R5_REAL_CAMPAIGN_ALLOWED") is not False
        or state.get("R5_CONSUMED_PROVIDER_CALLS") != 93
        or state.get("R5_CONSUMED_VITIS_LAUNCHES") != 33
        or state.get("R5_PROVIDER_CALL_HARD_CAP") != 500
        or state.get("R5_VITIS_LAUNCH_HARD_CAP") != 500
        or state.get("R5_PREDECESSOR_LIFECYCLE") != "Provisional"
    ):
        raise PreexistingHistoryAuditError("roadmap state does not permit acquisition")

    predecessor_path = Path(str(plan["predecessor_import_result"]))
    if (
        file_sha256(predecessor_path)
        != plan.get("predecessor_import_result_file_sha256")
    ):
        raise PreexistingHistoryAuditError("predecessor import result hash mismatch")
    predecessor = _load(predecessor_path)
    predecessor_sources = sorted(
        {
            item.get("source_sha256")
            for item in (
                _load(path)
                for path in sorted(
                    (predecessor_path.parent / "ledger").glob("*.json")
                )
                if path.name != "ledger_manifest.json"
            )
        }
    )
    if (
        predecessor.get("status")
        != "ready_for_independent_predecessor_import_audit"
        or predecessor.get("verified_positive_count") != 2
        or predecessor.get("independent_source_count") != 1
        or predecessor.get("lifecycle_reduction", {}).get("lifecycle")
        != "Provisional"
        or predecessor_sources != sorted(plan["predecessor_source_sha256s"])
        or bundle.source_sha256 in predecessor_sources
    ):
        raise PreexistingHistoryAuditError("predecessor independence is not proven")

    calibration_path = Path(str(plan["calibration_bundle"]))
    if file_sha256(calibration_path) != plan.get("calibration_bundle_file_sha256"):
        raise PreexistingHistoryAuditError("calibration bundle hash mismatch")
    calibration = _load(calibration_path)
    certificate = calibration.get("certificate")
    if (
        not isinstance(certificate, Mapping)
        or certificate.get("accepted") is not True
        or certificate.get("certificate_id")
        != plan.get("calibration_certificate_id")
        or certificate.get("failure_class_scope")
        != plan.get("calibration_failure_class_scope")
    ):
        raise PreexistingHistoryAuditError("calibration scope is incompatible")

    legacy = bundle.legacy_candidate_code
    if not all(
        marker in legacy
        for marker in (
            "insert(newitem, tree.space[root].right)",
            "insert(newitem, tree.space[root].left)",
            "dfs_traverse(tree.space[root].left)",
            "dfs_traverse(tree.space[root].right)",
        )
    ):
        raise PreexistingHistoryAuditError("recursive construct evidence is missing")
    host_checks = [
        _compile_and_run(
            compiler=compiler,
            reference_code=bundle.reference_code,
            candidate_code=bundle.isolated_candidate_code,
            test_code=bundle.public_test_code,
            split="Public",
        ),
        _compile_and_run(
            compiler=compiler,
            reference_code=bundle.reference_code,
            candidate_code=bundle.isolated_candidate_code,
            test_code=bundle.hidden_test_code,
            split="Hidden",
        ),
    ]
    result: dict[str, Any] = {
        "schema_version": 1,
        "audit_id": "v2.3-r5-preexisting-history-zero-call-audit-v1",
        "status": "ready_for_preexisting_history_acquisition",
        "repository_head": head,
        "repository_branch": branch,
        "plan_path": str(plan_path),
        "plan_file_sha256": file_sha256(plan_path),
        "plan_sha256": bundle.plan_sha256,
        "state_file_sha256": file_sha256(state_path),
        "case_identity": bundle.to_identity(),
        "host_adapter_checks": host_checks,
        "predecessor_import_result_file_sha256": file_sha256(predecessor_path),
        "predecessor_source_sha256s": predecessor_sources,
        "source_independence_verified": True,
        "calibration_bundle_file_sha256": file_sha256(calibration_path),
        "calibration_certificate_id": certificate["certificate_id"],
        "calibration_failure_class_scope": certificate["failure_class_scope"],
        "provider_call_upper_bound": 2,
        "vitis_launch_upper_bound": 6,
        "provider_calls": 0,
        "vitis_launches": 0,
        "future_holdout_case_ids": list(plan["future_holdout_case_ids"]),
        "future_files_read": False,
        "future_outcomes_observed": False,
        "trusted_revision_creation_allowed": False,
        "r5_real_campaign_allowed": False,
        "r5_accepted": False,
        "r6_started": False,
        "critical_finding_count": 0,
        "findings": [],
        "next_step": "run_one_preexisting_history_candidate_through_existing_r2_r4",
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
            "configs/r5/preexisting_history/recursive_e2_dfs_plan.json"
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
    print("R5_PREEXISTING_HISTORY_AUDIT_STATUS=" + str(result["status"]))
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
