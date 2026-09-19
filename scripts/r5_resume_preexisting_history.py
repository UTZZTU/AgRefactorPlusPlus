#!/usr/bin/env python3
"""Execute exactly one audited post-fix R5 history attempt."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = Path(__file__).resolve().parent
for value in (str(REPOSITORY_ROOT), str(SCRIPT_ROOT)):
    if value not in sys.path:
        sys.path.insert(0, value)

from agrefactor.recovery.r5_historical_candidate import canonical_sha256, file_sha256
from agrefactor.recovery.r5_preexisting_history_resume import (
    R5PreexistingHistoryResumeBundle,
    verify_preexisting_history_resume_plan,
)
from r5_acquire_preexisting_history import (  # type: ignore[import-not-found]
    _assert_safe,
    _load_certificate,
    acquire,
)
from r5_continue_preexisting_history import (  # type: ignore[import-not-found]
    _is_safe_abstention,
)


class PreexistingHistoryResumeError(RuntimeError):
    """Raised when the remaining post-fix attempt cannot run safely."""


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise PreexistingHistoryResumeError(
            f"invalid JSON evidence: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise PreexistingHistoryResumeError(
            f"JSON evidence is not an object: {path}"
        )
    return value


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise PreexistingHistoryResumeError(
            "git command failed: " + " ".join(args)
        )
    return completed.stdout.strip()


def _validate_audit(
    *,
    repository: Path,
    plan_path: Path,
    state_path: Path,
    audit: Mapping[str, Any],
    head: str,
) -> None:
    unsigned = {key: value for key, value in audit.items() if key != "audit_sha256"}
    files = {
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
    if (
        audit.get("status") != "ready_for_one_post_fix_history_attempt"
        or audit.get("repository_head") != head
        or audit.get("plan_file_sha256") != file_sha256(plan_path)
        or audit.get("state_file_sha256") != file_sha256(state_path)
        or audit.get("audit_sha256") != canonical_sha256(unsigned)
        or any(audit.get(key) != file_sha256(path) for key, path in files.items())
        or audit.get("contract_fix_is_ancestor") is not True
        or audit.get("attempts_authorized_originally") != 2
        or audit.get("attempts_consumed") != 1
        or audit.get("attempt_ordinal") != 3
        or audit.get("maximum_remaining_attempts") != 1
        or audit.get("maximum_candidate_mutations") != 1
        or audit.get("provider_calls_before") != 95
        or audit.get("vitis_launches_before") != 37
        or audit.get("provider_call_upper_bound") != 2
        or audit.get("vitis_launch_upper_bound") != 6
        or audit.get("confidence_minimum_authorized_label") != "high"
        or audit.get("confidence_threshold_weakened") is not False
        or audit.get("provider_calls") != 0
        or audit.get("vitis_launches") != 0
        or audit.get("future_files_read") is not False
        or audit.get("future_outcomes_observed") is not False
        or audit.get("critical_finding_count") != 0
        or audit.get("findings") != []
        or audit.get("r5_accepted") is not False
        or audit.get("r6_started") is not False
    ):
        raise PreexistingHistoryResumeError(
            "zero-call post-fix resume audit is incompatible"
        )


def load_preflight(
    *,
    repository: Path,
    plan_path: Path,
    state_path: Path,
    audit_path: Path,
) -> dict[str, Any]:
    repository = repository.expanduser().resolve()
    plan_path = plan_path.expanduser().resolve()
    state_path = state_path.expanduser().resolve()
    audit_path = audit_path.expanduser().resolve()
    head = _git(repository, "rev-parse", "HEAD")
    branch = _git(repository, "branch", "--show-current")
    status = _git(repository, "status", "--porcelain", "--untracked-files=all")
    if branch != "research-roadmap-v2.3" or status:
        raise PreexistingHistoryResumeError("resume requires the clean R5 branch")
    plan = _load(plan_path)
    bundle = verify_preexisting_history_resume_plan(repository, plan)
    state = _load(state_path)
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
    ):
        raise PreexistingHistoryResumeError(
            "roadmap state does not permit the remaining attempt"
        )
    audit = _load(audit_path)
    _validate_audit(
        repository=repository,
        plan_path=plan_path,
        state_path=state_path,
        audit=audit,
        head=head,
    )
    base_plan = bundle.continuation_bundle.base_plan
    calibration_path = Path(str(base_plan["calibration_bundle"]))
    if file_sha256(calibration_path) != base_plan.get(
        "calibration_bundle_file_sha256"
    ):
        raise PreexistingHistoryResumeError("calibration bundle hash mismatch")
    calibration = _load(calibration_path)
    certificate = _load_certificate(calibration)
    if certificate.certificate_id != base_plan.get("calibration_certificate_id"):
        raise PreexistingHistoryResumeError(
            "calibration certificate identity mismatch"
        )
    runtime = calibration.get("model_runtime")
    if not isinstance(runtime, Mapping) or not isinstance(
        runtime.get("request_parameters"), Mapping
    ):
        raise PreexistingHistoryResumeError(
            "calibration model runtime is incomplete"
        )
    api_key_env = runtime.get("api_key_env")
    if not isinstance(api_key_env, str) or not os.environ.get(api_key_env):
        raise PreexistingHistoryResumeError(
            "selected Provider credential is missing"
        )
    return {
        "repository": repository,
        "repository_head": head,
        "state": state,
        "state_path": state_path,
        "plan": plan,
        "plan_path": plan_path,
        "bundle": bundle,
        "audit": audit,
        "audit_path": audit_path,
        "calibration_bundle": calibration,
        "calibration_path": calibration_path,
        "certificate": certificate,
        "model_runtime": dict(runtime),
    }


def build_resume_manifest(
    preflight: Mapping[str, Any],
    *,
    resume_id: str,
) -> dict[str, Any]:
    bundle: R5PreexistingHistoryResumeBundle = preflight["bundle"]
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "manifest_id": "v2.3-r5-preexisting-history-post-fix-resume-v1",
        "status": "frozen_before_one_post_fix_attempt",
        "resume_id": resume_id,
        "repository_head": preflight["repository_head"],
        "repository_branch": "research-roadmap-v2.3",
        "plan_file_sha256": file_sha256(preflight["plan_path"]),
        "plan_sha256": bundle.plan_sha256,
        "protocol_audit_file_sha256": file_sha256(preflight["audit_path"]),
        "protocol_audit_sha256": preflight["audit"]["audit_sha256"],
        "case_identity": bundle.continuation_bundle.candidate.to_identity(),
        "contract_fix_commit": preflight["plan"]["contract_fix_commit"],
        "failed_continuation_audit_sha256": bundle.failed_audit["audit_sha256"],
        "attempts_authorized_originally": 2,
        "attempts_consumed": 1,
        "attempt_ordinal": 3,
        "maximum_remaining_attempts": 1,
        "maximum_candidate_mutations": 1,
        "provider_calls_before": 95,
        "vitis_launches_before": 37,
        "provider_call_upper_bound": 2,
        "vitis_launch_upper_bound": 6,
        "confidence_minimum_authorized_label": "high",
        "confidence_threshold_weakened": False,
        "future_holdout_case_ids": list(
            preflight["plan"]["future_holdout_case_ids"]
        ),
        "future_files_read": False,
        "future_outcomes_observed": False,
        "trusted_revision_creation_allowed": False,
        "r5_real_campaign_allowed": False,
        "r5_accepted": False,
        "r6_started": False,
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    return manifest


def run_resume(
    *,
    preflight: Mapping[str, Any],
    output: Path,
    resume_id: str,
) -> dict[str, Any]:
    output = output.expanduser().resolve()
    if output.exists():
        raise PreexistingHistoryResumeError(
            "output directory must not already exist"
        )
    output.mkdir(parents=True)
    manifest = build_resume_manifest(preflight, resume_id=resume_id)
    _atomic_json(output / "resume_manifest.json", manifest)
    bundle: R5PreexistingHistoryResumeBundle = preflight["bundle"]
    candidate = bundle.continuation_bundle.candidate
    attempt_root = output / "attempt-03"
    attempt_preflight = {
        "repository": preflight["repository"],
        "repository_head": preflight["repository_head"],
        "state": preflight["state"],
        "state_path": preflight["state_path"],
        "plan": preflight["plan"],
        "plan_path": preflight["plan_path"],
        "audit": preflight["audit"],
        "audit_path": preflight["audit_path"],
        "candidate": candidate,
        "calibration_bundle": preflight["calibration_bundle"],
        "calibration_path": preflight["calibration_path"],
        "certificate": preflight["certificate"],
        "model_runtime": preflight["model_runtime"],
        "manifest_id": "v2.3-r5-preexisting-history-post-fix-attempt-v1",
        "canary_manifest_id": "v2.3-r5-preexisting-history-post-fix-v1",
        "budget": {
            "provider_calls_before": 95,
            "vitis_launches_before": 37,
            "provider_call_upper_bound": 2,
            "vitis_launch_upper_bound": 6,
        },
        "continuation": {
            "resume_id": resume_id,
            "resume_manifest_sha256": manifest["manifest_sha256"],
            "attempt_ordinal": 3,
            "maximum_remaining_attempts": 1,
            "contract_fix_commit": preflight["plan"]["contract_fix_commit"],
            "failed_continuation_audit_sha256": bundle.failed_audit["audit_sha256"],
        },
    }
    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    attempt = acquire(
        preflight=attempt_preflight,
        output=attempt_root,
        run_id="r5-preexisting-history-recursive-e2-dfs-03",
    )
    if attempt["provider_calls"] > 2 or attempt["vitis_launches"] > 6:
        raise PreexistingHistoryResumeError(
            "remaining attempt exceeded its frozen budget"
        )
    if attempt["status"] == "verified_positive":
        status = "verified_positive"
        reason = "remaining_attempt_verified_positive"
    elif _is_safe_abstention(attempt):
        status = "exhausted_safe_abstention"
        reason = "remaining_attempt_safely_abstained"
    else:
        status = "stopped_inconclusive"
        reason = "remaining_attempt_inconclusive"
    completed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    result: dict[str, Any] = {
        "schema_version": 1,
        "status": status,
        "reason": reason,
        "resume_id": resume_id,
        "manifest_sha256": manifest["manifest_sha256"],
        "repository_head": preflight["repository_head"],
        "started_at": started_at,
        "completed_at": completed_at,
        "attempt": {
            "attempt_ordinal": 3,
            "run_id": attempt["run_id"],
            "run_root": str(attempt_root),
            "status": attempt["status"],
            "provider_calls": attempt["provider_calls"],
            "vitis_launches": attempt["vitis_launches"],
            "acquisition_manifest_file_sha256": file_sha256(
                attempt_root / "acquisition_manifest.json"
            ),
            "acquisition_result_file_sha256": file_sha256(
                attempt_root / "acquisition_result.json"
            ),
            "evidence_archive_sha256": file_sha256(attempt_root / "evidence.zip"),
        },
        "provider_calls": attempt["provider_calls"],
        "vitis_launches": attempt["vitis_launches"],
        "provider_calls_after": 95 + int(attempt["provider_calls"]),
        "vitis_launches_after": 37 + int(attempt["vitis_launches"]),
        "verified_positive_episode_count": (
            1 if status == "verified_positive" else 0
        ),
        "remaining_attempts": 0,
        "confidence_threshold_weakened": False,
        "future_files_read": False,
        "future_outcomes_observed": False,
        "trusted_revision_created": False,
        "r5_real_campaign_allowed": False,
        "raw_provider_response_persisted": False,
        "private_reasoning_persisted": False,
        "secret_values_persisted": False,
        "r5_accepted": False,
        "r6_started": False,
    }
    result["result_sha256"] = canonical_sha256(result)
    secret = os.environ.get(str(preflight["model_runtime"]["api_key_env"]))
    _assert_safe(result, secret=secret)
    _atomic_json(output / "resume_result.json", result)
    print("R5_PREEXISTING_HISTORY_RESUME_STATUS=" + status)
    print("REASON=" + reason)
    print(f"PROVIDER_CALLS={result['provider_calls']}")
    print(f"VITIS_LAUNCHES={result['vitis_launches']}")
    print("REMAINING_ATTEMPTS=0")
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
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
    parser.add_argument("--protocol-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--resume-id",
        default="r5-preexisting-history-recursive-e2-dfs-post-fix-01",
    )
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args(argv)
    preflight = load_preflight(
        repository=args.repo,
        plan_path=args.plan,
        state_path=args.state,
        audit_path=args.protocol_audit,
    )
    manifest = build_resume_manifest(preflight, resume_id=args.resume_id)
    print("R5_PREEXISTING_HISTORY_RESUME_PREFLIGHT=passed")
    print("ATTEMPT_ORDINAL=3")
    print("MAXIMUM_REMAINING_ATTEMPTS=1")
    print("PROVIDER_CALL_UPPER_BOUND=2")
    print("VITIS_LAUNCH_UPPER_BOUND=6")
    if args.preflight_only:
        print("PROVIDER_CALLS=0")
        print("VITIS_LAUNCHES=0")
        print("R5_ACCEPTED=false")
        print("R6_STARTED=false")
        return 0
    run_resume(
        preflight=preflight,
        output=args.output,
        resume_id=str(manifest["resume_id"]),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
