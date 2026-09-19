#!/usr/bin/env python3
"""Run the audited, bounded continuation of one R5 history Candidate."""

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
from agrefactor.recovery.r5_preexisting_history_continuation import (
    R5PreexistingHistoryContinuationBundle,
    verify_preexisting_history_continuation_plan,
)
from r5_acquire_preexisting_history import (  # type: ignore[import-not-found]
    _assert_safe,
    _load_certificate,
    acquire,
)


class PreexistingHistoryContinuationError(RuntimeError):
    """Raised when a real continuation cannot proceed safely."""


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise PreexistingHistoryContinuationError(
            f"invalid JSON evidence: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise PreexistingHistoryContinuationError(
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
        raise PreexistingHistoryContinuationError(
            "git command failed: " + " ".join(args)
        )
    return completed.stdout.strip()


def _validate_audit(
    *,
    repository: Path,
    plan_path: Path,
    state_path: Path,
    audit_path: Path,
    audit: Mapping[str, Any],
    head: str,
) -> None:
    unsigned = {key: value for key, value in audit.items() if key != "audit_sha256"}
    expected_files = {
        "runner_file_sha256": repository
        / "scripts"
        / "r5_continue_preexisting_history.py",
        "acquisition_engine_file_sha256": repository
        / "scripts"
        / "r5_acquire_preexisting_history.py",
        "continuation_module_file_sha256": repository
        / "agrefactor"
        / "recovery"
        / "r5_preexisting_history_continuation.py",
    }
    if (
        audit.get("status") != "ready_for_preexisting_history_continuation"
        or audit.get("repository_head") != head
        or audit.get("plan_file_sha256") != file_sha256(plan_path)
        or audit.get("state_file_sha256") != file_sha256(state_path)
        or audit.get("audit_sha256") != canonical_sha256(unsigned)
        or any(audit.get(key) != file_sha256(path) for key, path in expected_files.items())
        or audit.get("maximum_additional_attempts") != 2
        or audit.get("stop_after_first_verified_positive") is not True
        or audit.get("maximum_candidate_mutations_per_attempt") != 1
        or audit.get("provider_calls_before") != 94
        or audit.get("vitis_launches_before") != 35
        or audit.get("provider_call_upper_bound_per_attempt") != 2
        or audit.get("vitis_launch_upper_bound_per_attempt") != 6
        or audit.get("provider_call_upper_bound_total") != 4
        or audit.get("vitis_launch_upper_bound_total") != 12
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
        raise PreexistingHistoryContinuationError(
            "zero-call continuation audit is incompatible"
        )
    if not audit_path.is_file():
        raise PreexistingHistoryContinuationError(
            "zero-call continuation audit file is missing"
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
        raise PreexistingHistoryContinuationError(
            "continuation requires the clean R5 branch"
        )
    plan = _load(plan_path)
    bundle = verify_preexisting_history_continuation_plan(repository, plan)
    state = _load(state_path)
    if (
        state.get("R4_ACCEPTED") is not True
        or state.get("R5_STARTED") is not True
        or state.get("R5_ACCEPTED") is not False
        or state.get("R6_STARTED") is not False
        or state.get("R5_REAL_CAMPAIGN_ALLOWED") is not False
        or state.get("R5_PREDECESSOR_LIFECYCLE") != "Provisional"
        or state.get("R5_PREEXISTING_HISTORY_STATUS")
        != "clean_safe_calibration_abstention"
        or state.get("R5_CONSUMED_PROVIDER_CALLS") != 94
        or state.get("R5_CONSUMED_VITIS_LAUNCHES") != 35
        or state.get("R5_PROVIDER_CALL_HARD_CAP") != 500
        or state.get("R5_VITIS_LAUNCH_HARD_CAP") != 500
    ):
        raise PreexistingHistoryContinuationError(
            "roadmap state does not permit continuation"
        )
    audit = _load(audit_path)
    _validate_audit(
        repository=repository,
        plan_path=plan_path,
        state_path=state_path,
        audit_path=audit_path,
        audit=audit,
        head=head,
    )
    base_plan = bundle.base_plan
    calibration_path = Path(str(base_plan["calibration_bundle"]))
    if file_sha256(calibration_path) != base_plan.get(
        "calibration_bundle_file_sha256"
    ):
        raise PreexistingHistoryContinuationError(
            "calibration bundle hash mismatch"
        )
    calibration = _load(calibration_path)
    certificate = _load_certificate(calibration)
    if certificate.certificate_id != base_plan.get("calibration_certificate_id"):
        raise PreexistingHistoryContinuationError(
            "calibration certificate identity mismatch"
        )
    runtime = calibration.get("model_runtime")
    if not isinstance(runtime, Mapping) or not isinstance(
        runtime.get("request_parameters"), Mapping
    ):
        raise PreexistingHistoryContinuationError(
            "calibration model runtime is incomplete"
        )
    api_key_env = runtime.get("api_key_env")
    if not isinstance(api_key_env, str) or not os.environ.get(api_key_env):
        raise PreexistingHistoryContinuationError(
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


def build_continuation_manifest(
    preflight: Mapping[str, Any],
    *,
    campaign_id: str,
) -> dict[str, Any]:
    bundle: R5PreexistingHistoryContinuationBundle = preflight["bundle"]
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "manifest_id": "v2.3-r5-preexisting-history-continuation-v1",
        "status": "frozen_before_real_continuation",
        "campaign_id": campaign_id,
        "repository_head": preflight["repository_head"],
        "repository_branch": "research-roadmap-v2.3",
        "plan_file_sha256": file_sha256(preflight["plan_path"]),
        "plan_sha256": bundle.plan_sha256,
        "protocol_audit_file_sha256": file_sha256(preflight["audit_path"]),
        "protocol_audit_sha256": preflight["audit"]["audit_sha256"],
        "case_identity": bundle.candidate.to_identity(),
        "prior_evidence_archive_sha256": bundle.prior_audit[
            "evidence_archive_sha256"
        ],
        "prior_independent_audit_sha256": bundle.prior_audit["audit_sha256"],
        "maximum_additional_attempts": 2,
        "stop_after_first_verified_positive": True,
        "maximum_candidate_mutations_per_attempt": 1,
        "provider_calls_before": 94,
        "vitis_launches_before": 35,
        "provider_call_upper_bound_total": 4,
        "vitis_launch_upper_bound_total": 12,
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


def _is_safe_abstention(result: Mapping[str, Any]) -> bool:
    shadows = result.get("r2_shadow_diagnostics")
    integration = result.get("r5_integration")
    return (
        result.get("status") == "abstained"
        and result.get("provider_calls") == 1
        and result.get("vitis_launches") == 2
        and isinstance(shadows, list)
        and len(shadows) == 1
        and shadows[0].get("advisory", {}).get("confidence") in {"low", "medium"}
        and isinstance(integration, Mapping)
        and integration.get("status") == "abstained"
        and integration.get("reason") == "r2_calibration_unverified"
        and integration.get("main_result_unchanged") is True
        and integration.get("accepted_by_integration") is False
    )


def run_continuation(
    *,
    preflight: Mapping[str, Any],
    output: Path,
    campaign_id: str,
) -> dict[str, Any]:
    output = output.expanduser().resolve()
    if output.exists():
        raise PreexistingHistoryContinuationError(
            "output directory must not already exist"
        )
    output.mkdir(parents=True)
    manifest = build_continuation_manifest(preflight, campaign_id=campaign_id)
    _atomic_json(output / "continuation_manifest.json", manifest)
    bundle: R5PreexistingHistoryContinuationBundle = preflight["bundle"]
    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    attempts: list[dict[str, Any]] = []
    provider_calls = 0
    vitis_launches = 0
    terminal_status = "exhausted_safe_abstention"
    stop_reason = "maximum_additional_attempts_exhausted"
    for ordinal in (2, 3):
        run_id = f"r5-preexisting-history-recursive-e2-dfs-{ordinal:02d}"
        attempt_root = output / f"attempt-{ordinal:02d}"
        attempt_preflight = {
            "repository": preflight["repository"],
            "repository_head": preflight["repository_head"],
            "state": preflight["state"],
            "state_path": preflight["state_path"],
            "plan": preflight["plan"],
            "plan_path": preflight["plan_path"],
            "audit": preflight["audit"],
            "audit_path": preflight["audit_path"],
            "candidate": bundle.candidate,
            "calibration_bundle": preflight["calibration_bundle"],
            "calibration_path": preflight["calibration_path"],
            "certificate": preflight["certificate"],
            "model_runtime": preflight["model_runtime"],
            "manifest_id": "v2.3-r5-preexisting-history-continuation-attempt-v1",
            "canary_manifest_id": "v2.3-r5-preexisting-history-continuation-v1",
            "budget": {
                "provider_calls_before": 94 + provider_calls,
                "vitis_launches_before": 35 + vitis_launches,
                "provider_call_upper_bound": 2,
                "vitis_launch_upper_bound": 6,
            },
            "continuation": {
                "campaign_id": campaign_id,
                "campaign_manifest_sha256": manifest["manifest_sha256"],
                "attempt_ordinal": ordinal,
                "maximum_additional_attempts": 2,
                "stop_after_first_verified_positive": True,
                "prior_evidence_archive_sha256": bundle.prior_audit[
                    "evidence_archive_sha256"
                ],
            },
        }
        result = acquire(
            preflight=attempt_preflight,
            output=attempt_root,
            run_id=run_id,
        )
        provider_calls += int(result["provider_calls"])
        vitis_launches += int(result["vitis_launches"])
        attempts.append(
            {
                "attempt_ordinal": ordinal,
                "run_id": run_id,
                "run_root": str(attempt_root),
                "status": result["status"],
                "provider_calls": result["provider_calls"],
                "vitis_launches": result["vitis_launches"],
                "acquisition_manifest_file_sha256": file_sha256(
                    attempt_root / "acquisition_manifest.json"
                ),
                "acquisition_result_file_sha256": file_sha256(
                    attempt_root / "acquisition_result.json"
                ),
                "evidence_archive_sha256": file_sha256(
                    attempt_root / "evidence.zip"
                ),
            }
        )
        if provider_calls > 4 or vitis_launches > 12:
            raise PreexistingHistoryContinuationError(
                "continuation exceeded its aggregate frozen budget"
            )
        if result["status"] == "verified_positive":
            terminal_status = "verified_positive"
            stop_reason = "first_verified_positive"
            break
        if not _is_safe_abstention(result):
            terminal_status = "stopped_inconclusive"
            stop_reason = "attempt_was_not_verified_positive_or_safe_abstention"
            break
    completed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    value: dict[str, Any] = {
        "schema_version": 1,
        "status": terminal_status,
        "stop_reason": stop_reason,
        "campaign_id": campaign_id,
        "manifest_sha256": manifest["manifest_sha256"],
        "repository_head": preflight["repository_head"],
        "started_at": started_at,
        "completed_at": completed_at,
        "attempts": attempts,
        "attempt_count": len(attempts),
        "provider_calls": provider_calls,
        "vitis_launches": vitis_launches,
        "provider_calls_after": 94 + provider_calls,
        "vitis_launches_after": 35 + vitis_launches,
        "verified_positive_episode_count": 1
        if terminal_status == "verified_positive"
        else 0,
        "stopped_after_first_verified_positive": (
            terminal_status == "verified_positive"
        ),
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
    value["result_sha256"] = canonical_sha256(value)
    secret = os.environ.get(str(preflight["model_runtime"]["api_key_env"]))
    _assert_safe(value, secret=secret)
    _atomic_json(output / "continuation_result.json", value)
    print("R5_PREEXISTING_HISTORY_CONTINUATION_STATUS=" + terminal_status)
    print("STOP_REASON=" + stop_reason)
    print(f"ATTEMPTS={len(attempts)}")
    print(f"PROVIDER_CALLS={provider_calls}")
    print(f"VITIS_LAUNCHES={vitis_launches}")
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return value


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
    parser.add_argument("--protocol-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--campaign-id",
        default="r5-preexisting-history-recursive-e2-dfs-continuation-01",
    )
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args(argv)
    preflight = load_preflight(
        repository=args.repo,
        plan_path=args.plan,
        state_path=args.state,
        audit_path=args.protocol_audit,
    )
    manifest = build_continuation_manifest(
        preflight,
        campaign_id=args.campaign_id,
    )
    print("R5_PREEXISTING_HISTORY_CONTINUATION_PREFLIGHT=passed")
    print("PROVIDER_CALL_UPPER_BOUND=4")
    print("VITIS_LAUNCH_UPPER_BOUND=12")
    if args.preflight_only:
        print("PROVIDER_CALLS=0")
        print("VITIS_LAUNCHES=0")
        print("R5_ACCEPTED=false")
        print("R6_STARTED=false")
        return 0
    run_continuation(
        preflight=preflight,
        output=args.output,
        campaign_id=str(manifest["campaign_id"]),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
