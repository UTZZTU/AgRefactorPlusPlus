"""Verify the one-attempt R5 history resume after an audited contract fix."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .r5_historical_candidate import canonical_sha256, file_sha256
from .r5_preexisting_history_continuation import (
    R5PreexistingHistoryContinuationBundle,
    verify_preexisting_history_continuation_plan,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class R5PreexistingHistoryResumeError(ValueError):
    """Raised when the post-fix resume is not evidence-bound."""


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise R5PreexistingHistoryResumeError(
            f"invalid JSON evidence: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise R5PreexistingHistoryResumeError(
            f"JSON evidence is not an object: {path}"
        )
    return value


def _repo_file(repository: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise R5PreexistingHistoryResumeError(f"{label} is missing")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise R5PreexistingHistoryResumeError(f"{label} is unsafe")
    path = (repository / relative).resolve()
    try:
        path.relative_to(repository)
    except ValueError as exc:
        raise R5PreexistingHistoryResumeError(f"{label} is unsafe") from exc
    if path.is_symlink() or not path.is_file():
        raise R5PreexistingHistoryResumeError(f"{label} is not a file")
    return path


def _external_file(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise R5PreexistingHistoryResumeError(f"{label} is missing")
    path = Path(value).expanduser().resolve()
    if path.is_symlink() or not path.is_file():
        raise R5PreexistingHistoryResumeError(f"{label} is not a file")
    return path


def _hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise R5PreexistingHistoryResumeError(
            f"{label} is not a lowercase SHA-256"
        )
    return value


def _git_object_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _GIT_OBJECT_ID.fullmatch(value):
        raise R5PreexistingHistoryResumeError(
            f"{label} is not a full lowercase Git object ID"
        )
    return value


@dataclass(frozen=True, slots=True)
class R5PreexistingHistoryResumeBundle:
    plan: Mapping[str, Any]
    plan_sha256: str
    continuation_bundle: R5PreexistingHistoryContinuationBundle
    failed_run_root: Path
    failed_manifest: Mapping[str, Any]
    failed_result: Mapping[str, Any]
    failed_audit_path: Path
    failed_audit: Mapping[str, Any]
    reconciliation_path: Path


def verify_preexisting_history_resume_plan(
    repository: str | Path,
    plan: Mapping[str, Any],
) -> R5PreexistingHistoryResumeBundle:
    root = Path(repository).expanduser().resolve()
    if not root.is_dir():
        raise R5PreexistingHistoryResumeError("repository root is missing")
    if (
        plan.get("schema_version") != 1
        or plan.get("status")
        != "frozen_after_audited_contract_fix_before_remaining_attempt"
        or plan.get("period") != "history"
        or plan.get("control_role") != "positive"
        or plan.get("failure_family") != "unsupported_construct"
        or plan.get("attempts_authorized_originally") != 2
        or plan.get("attempts_consumed") != 1
        or plan.get("attempt_ordinal") != 3
        or plan.get("maximum_remaining_attempts") != 1
        or plan.get("stop_after_first_verified_positive") is not True
        or plan.get("maximum_candidate_mutations") != 1
        or plan.get("future_files_read_by_protocol") is not False
        or plan.get("future_outcomes_observed") is not False
        or plan.get("expected_outputs_invented") is not False
        or plan.get("original_benchmark_files_mutated") is not False
        or plan.get("trusted_revision_creation_allowed") is not False
        or plan.get("r5_real_campaign_allowed") is not False
        or plan.get("r5_accepted") is not False
        or plan.get("r6_started") is not False
    ):
        raise R5PreexistingHistoryResumeError("resume plan boundary is invalid")
    budget = plan.get("budget")
    confidence = plan.get("confidence_boundary")
    if (
        not isinstance(budget, Mapping)
        or budget
        != {
            "provider_calls_before": 95,
            "vitis_launches_before": 37,
            "provider_call_upper_bound": 2,
            "vitis_launch_upper_bound": 6,
            "provider_hard_cap": 500,
            "vitis_hard_cap": 500,
        }
        or not isinstance(confidence, Mapping)
        or confidence.get("minimum_authorized_label") != "high"
        or confidence.get("accepted_calibration_certificate_unchanged") is not True
        or confidence.get("threshold_weakened") is not False
        or confidence.get("medium_may_mutate") is not False
    ):
        raise R5PreexistingHistoryResumeError(
            "resume budget or confidence boundary is invalid"
        )

    continuation_path = _repo_file(
        root, plan.get("continuation_plan_path"), "continuation plan"
    )
    if file_sha256(continuation_path) != _hash(
        plan.get("continuation_plan_file_sha256"),
        "continuation plan file hash",
    ):
        raise R5PreexistingHistoryResumeError(
            "continuation plan file hash mismatch"
        )
    continuation_plan = _load(continuation_path)
    continuation = verify_preexisting_history_continuation_plan(
        root, continuation_plan
    )
    if continuation.plan_sha256 != _hash(
        plan.get("continuation_plan_sha256"), "continuation plan hash"
    ):
        raise R5PreexistingHistoryResumeError("continuation plan hash mismatch")
    if (
        continuation.candidate.case_id != plan.get("case_id")
        or list(continuation.plan.get("future_holdout_case_ids", ()))
        != list(plan.get("future_holdout_case_ids", ()))
    ):
        raise R5PreexistingHistoryResumeError(
            "resume case identity or holdout declaration changed"
        )

    run_root_value = plan.get("failed_continuation_run_root")
    if not isinstance(run_root_value, str) or not run_root_value:
        raise R5PreexistingHistoryResumeError("failed continuation root is missing")
    failed_root = Path(run_root_value).expanduser().resolve()
    manifest_path = failed_root / "continuation_manifest.json"
    result_path = failed_root / "continuation_result.json"
    if any(path.is_symlink() or not path.is_file() for path in (manifest_path, result_path)):
        raise R5PreexistingHistoryResumeError(
            "failed continuation evidence is missing"
        )
    if (
        file_sha256(manifest_path)
        != _hash(
            plan.get("failed_continuation_manifest_file_sha256"),
            "failed continuation manifest hash",
        )
        or file_sha256(result_path)
        != _hash(
            plan.get("failed_continuation_result_file_sha256"),
            "failed continuation result hash",
        )
    ):
        raise R5PreexistingHistoryResumeError(
            "failed continuation evidence hash mismatch"
        )
    failed_manifest = _load(manifest_path)
    failed_result = _load(result_path)
    attempts = failed_result.get("attempts")
    if (
        failed_result.get("status") != "stopped_inconclusive"
        or failed_result.get("stop_reason")
        != "attempt_was_not_verified_positive_or_safe_abstention"
        or failed_result.get("attempt_count") != 1
        or not isinstance(attempts, list)
        or len(attempts) != 1
        or attempts[0].get("attempt_ordinal") != 2
        or attempts[0].get("status") != "inconclusive"
        or failed_result.get("provider_calls") != 1
        or failed_result.get("vitis_launches") != 2
        or failed_result.get("provider_calls_after") != 95
        or failed_result.get("vitis_launches_after") != 37
        or failed_result.get("verified_positive_episode_count") != 0
        or failed_result.get("future_files_read") is not False
        or failed_result.get("future_outcomes_observed") is not False
    ):
        raise R5PreexistingHistoryResumeError(
            "failed continuation does not leave one safe remaining attempt"
        )

    audit_path = _external_file(
        plan.get("failed_continuation_independent_audit_path"),
        "failed continuation independent audit",
    )
    if file_sha256(audit_path) != _hash(
        plan.get("failed_continuation_independent_audit_file_sha256"),
        "failed continuation independent audit file hash",
    ):
        raise R5PreexistingHistoryResumeError(
            "failed continuation independent audit file hash mismatch"
        )
    audit = _load(audit_path)
    if (
        audit.get("audit_sha256")
        != _hash(
            plan.get("failed_continuation_independent_audit_sha256"),
            "failed continuation independent audit hash",
        )
        or audit.get("status") != "clean_stopped_inconclusive"
        or audit.get("critical_finding_count") != 0
        or audit.get("blocking_finding_count") != 1
        or audit.get("provider_calls") != 1
        or audit.get("vitis_launches") != 2
        or audit.get("r5_accepted") is not False
        or audit.get("r6_started") is not False
    ):
        raise R5PreexistingHistoryResumeError(
            "failed continuation independent audit is incompatible"
        )

    reconciliation_path = _repo_file(
        root, plan.get("failure_reconciliation_path"), "failure reconciliation"
    )
    if file_sha256(reconciliation_path) != _hash(
        plan.get("failure_reconciliation_file_sha256"),
        "failure reconciliation file hash",
    ):
        raise R5PreexistingHistoryResumeError(
            "failure reconciliation hash mismatch"
        )
    reconciliation = _load(reconciliation_path)
    if (
        reconciliation.get("status")
        != "audited_pre_provider_contract_failure_fixed"
        or reconciliation.get("fix_repository_head")
        != plan.get("contract_fix_commit")
        or reconciliation.get("budget_after")
        != {"provider_calls": 95, "vitis_launches": 37}
        or reconciliation.get("remaining_attempts_under_original_continuation") != 1
        or reconciliation.get("confidence_threshold_weakened") is not False
        or reconciliation.get("r5_accepted") is not False
        or reconciliation.get("r6_started") is not False
    ):
        raise R5PreexistingHistoryResumeError(
            "failure reconciliation is incompatible"
        )
    _git_object_id(plan.get("contract_fix_commit"), "contract fix commit")
    return R5PreexistingHistoryResumeBundle(
        plan=dict(plan),
        plan_sha256=canonical_sha256(dict(plan)),
        continuation_bundle=continuation,
        failed_run_root=failed_root,
        failed_manifest=failed_manifest,
        failed_result=failed_result,
        failed_audit_path=audit_path,
        failed_audit=audit,
        reconciliation_path=reconciliation_path,
    )


__all__ = [
    "R5PreexistingHistoryResumeBundle",
    "R5PreexistingHistoryResumeError",
    "verify_preexisting_history_resume_plan",
]
