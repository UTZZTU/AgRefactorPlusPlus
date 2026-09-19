"""Verify the bounded continuation contract for an R5 history Candidate."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .r5_historical_candidate import (
    R5HistoricalCandidateBundle,
    canonical_sha256,
    file_sha256,
    verify_historical_candidate_plan,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class R5PreexistingHistoryContinuationError(ValueError):
    """Raised when a history continuation would cross its frozen boundary."""


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise R5PreexistingHistoryContinuationError(
            f"invalid JSON evidence: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise R5PreexistingHistoryContinuationError(
            f"JSON evidence is not an object: {path}"
        )
    return value


def _repo_path(repository: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise R5PreexistingHistoryContinuationError(f"{label} is missing")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise R5PreexistingHistoryContinuationError(f"{label} is unsafe")
    resolved = (repository / relative).resolve()
    try:
        resolved.relative_to(repository)
    except ValueError as exc:
        raise R5PreexistingHistoryContinuationError(f"{label} is unsafe") from exc
    if resolved.is_symlink() or not resolved.is_file():
        raise R5PreexistingHistoryContinuationError(f"{label} is not a file")
    return resolved


def _external_file(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise R5PreexistingHistoryContinuationError(f"{label} is missing")
    path = Path(value).expanduser().resolve()
    if path.is_symlink() or not path.is_file():
        raise R5PreexistingHistoryContinuationError(f"{label} is not a file")
    return path


def _require_hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise R5PreexistingHistoryContinuationError(
            f"{label} is not a lowercase SHA-256"
        )
    return value


@dataclass(frozen=True, slots=True)
class R5PreexistingHistoryContinuationBundle:
    plan: Mapping[str, Any]
    plan_sha256: str
    base_plan_path: Path
    base_plan: Mapping[str, Any]
    candidate: R5HistoricalCandidateBundle
    prior_run_root: Path
    prior_result: Mapping[str, Any]
    prior_audit_path: Path
    prior_audit: Mapping[str, Any]
    reconciliation_path: Path


def verify_preexisting_history_continuation_plan(
    repository: str | Path,
    plan: Mapping[str, Any],
) -> R5PreexistingHistoryContinuationBundle:
    root = Path(repository).expanduser().resolve()
    if not root.is_dir():
        raise R5PreexistingHistoryContinuationError("repository root is missing")
    if (
        plan.get("schema_version") != 1
        or plan.get("status")
        != "frozen_after_first_safe_abstention_before_continuation"
        or plan.get("period") != "history"
        or plan.get("control_role") != "positive"
        or plan.get("failure_family") != "unsupported_construct"
        or plan.get("maximum_additional_attempts") != 2
        or plan.get("stop_after_first_verified_positive") is not True
        or plan.get("maximum_candidate_mutations_per_attempt") != 1
        or plan.get("future_files_read_by_protocol") is not False
        or plan.get("future_outcomes_observed") is not False
        or plan.get("expected_outputs_invented") is not False
        or plan.get("original_benchmark_files_mutated") is not False
        or plan.get("trusted_revision_creation_allowed") is not False
        or plan.get("r5_real_campaign_allowed") is not False
        or plan.get("r5_accepted") is not False
        or plan.get("r6_started") is not False
    ):
        raise R5PreexistingHistoryContinuationError(
            "continuation plan boundary is invalid"
        )
    budget = plan.get("budget")
    expected_budget = {
        "provider_calls_before": 94,
        "vitis_launches_before": 35,
        "provider_call_upper_bound_per_attempt": 2,
        "vitis_launch_upper_bound_per_attempt": 6,
        "provider_call_upper_bound_total": 4,
        "vitis_launch_upper_bound_total": 12,
        "provider_hard_cap": 500,
        "vitis_hard_cap": 500,
    }
    confidence = plan.get("confidence_boundary")
    if (
        not isinstance(budget, Mapping)
        or any(budget.get(key) != value for key, value in expected_budget.items())
        or not isinstance(confidence, Mapping)
        or confidence.get("minimum_authorized_label") != "high"
        or confidence.get("accepted_calibration_certificate_unchanged") is not True
        or confidence.get("threshold_weakened") is not False
        or confidence.get("medium_may_mutate") is not False
    ):
        raise R5PreexistingHistoryContinuationError(
            "continuation budget or confidence boundary is invalid"
        )

    base_path = _repo_path(root, plan.get("base_plan_path"), "base plan")
    if file_sha256(base_path) != _require_hash(
        plan.get("base_plan_file_sha256"), "base plan file hash"
    ):
        raise R5PreexistingHistoryContinuationError("base plan file hash mismatch")
    base_plan = _load(base_path)
    candidate = verify_historical_candidate_plan(root, base_plan)
    if (
        candidate.plan_sha256
        != _require_hash(plan.get("base_plan_sha256"), "base plan hash")
        or candidate.case_id != plan.get("case_id")
        or candidate.source_sha256 != plan.get("source_sha256")
        or candidate.isolated_candidate_sha256
        != plan.get("isolated_candidate_sha256")
        or list(base_plan.get("future_holdout_case_ids", ()))
        != list(plan.get("future_holdout_case_ids", ()))
    ):
        raise R5PreexistingHistoryContinuationError(
            "base plan identity does not match continuation"
        )

    prior = plan.get("prior_attempt")
    if not isinstance(prior, Mapping):
        raise R5PreexistingHistoryContinuationError("prior attempt is missing")
    prior_root_value = prior.get("run_root")
    if not isinstance(prior_root_value, str) or not prior_root_value:
        raise R5PreexistingHistoryContinuationError("prior run root is missing")
    prior_root = Path(prior_root_value).expanduser().resolve()
    manifest_path = prior_root / "acquisition_manifest.json"
    result_path = prior_root / "acquisition_result.json"
    archive_path = prior_root / "evidence.zip"
    if any(path.is_symlink() or not path.is_file() for path in (manifest_path, result_path, archive_path)):
        raise R5PreexistingHistoryContinuationError("prior sealed evidence is missing")
    if (
        file_sha256(manifest_path)
        != _require_hash(
            prior.get("acquisition_manifest_file_sha256"),
            "prior manifest file hash",
        )
        or file_sha256(result_path)
        != _require_hash(
            prior.get("acquisition_result_file_sha256"),
            "prior result file hash",
        )
        or file_sha256(archive_path)
        != _require_hash(prior.get("evidence_archive_sha256"), "prior archive hash")
    ):
        raise R5PreexistingHistoryContinuationError("prior sealed evidence hash mismatch")
    prior_result = _load(result_path)
    shadow = prior_result.get("r2_shadow_diagnostics")
    integration = prior_result.get("r5_integration")
    if (
        prior.get("status") != "clean_safe_calibration_abstention"
        or prior.get("r2_confidence") != "medium"
        or prior.get("provider_calls") != 1
        or prior.get("vitis_launches") != 2
        or prior.get("verified_positive_episode_count") != 0
        or prior_result.get("run_id") != prior.get("run_id")
        or prior_result.get("status") != "abstained"
        or prior_result.get("provider_calls") != 1
        or prior_result.get("vitis_launches") != 2
        or not isinstance(shadow, list)
        or len(shadow) != 1
        or shadow[0].get("advisory", {}).get("confidence") != "medium"
        or not isinstance(integration, Mapping)
        or integration.get("status") != "abstained"
        or integration.get("reason") != "r2_calibration_unverified"
        or integration.get("calibration", {}).get("verified") is not False
        or integration.get("main_result_unchanged") is not True
        or integration.get("accepted_by_integration") is not False
    ):
        raise R5PreexistingHistoryContinuationError(
            "prior attempt is not a safe calibration abstention"
        )

    prior_audit_path = _external_file(
        prior.get("independent_audit_path"), "prior independent audit"
    )
    if file_sha256(prior_audit_path) != _require_hash(
        prior.get("independent_audit_file_sha256"),
        "prior independent audit file hash",
    ):
        raise R5PreexistingHistoryContinuationError(
            "prior independent audit file hash mismatch"
        )
    prior_audit = _load(prior_audit_path)
    if (
        prior_audit.get("audit_sha256")
        != _require_hash(
            prior.get("independent_audit_sha256"),
            "prior independent audit hash",
        )
        or prior_audit.get("status") != "clean_safe_calibration_abstention"
        or prior_audit.get("critical_finding_count") != 0
        or prior_audit.get("source_sha256") != candidate.source_sha256
        or prior_audit.get("evidence_archive_sha256")
        != prior.get("evidence_archive_sha256")
        or prior_audit.get("r5_accepted") is not False
        or prior_audit.get("r6_started") is not False
    ):
        raise R5PreexistingHistoryContinuationError(
            "prior independent audit is incompatible"
        )

    reconciliation_path = _repo_path(
        root, plan.get("reconciliation_path"), "reconciliation"
    )
    if file_sha256(reconciliation_path) != _require_hash(
        plan.get("reconciliation_file_sha256"), "reconciliation file hash"
    ):
        raise R5PreexistingHistoryContinuationError(
            "safe-abstention reconciliation hash mismatch"
        )
    reconciliation = _load(reconciliation_path)
    if (
        reconciliation.get("status") != "clean_safe_calibration_abstention"
        or reconciliation.get("source_sha256") != candidate.source_sha256
        or reconciliation.get("budget_after")
        != {"provider_calls": 94, "vitis_launches": 35}
        or reconciliation.get("verified_positive_episode_count") != 0
        or reconciliation.get("confidence_threshold_weakened") is not False
        or reconciliation.get("r5_accepted") is not False
        or reconciliation.get("r6_started") is not False
    ):
        raise R5PreexistingHistoryContinuationError(
            "safe-abstention reconciliation is incompatible"
        )
    return R5PreexistingHistoryContinuationBundle(
        plan=dict(plan),
        plan_sha256=canonical_sha256(dict(plan)),
        base_plan_path=base_path,
        base_plan=base_plan,
        candidate=candidate,
        prior_run_root=prior_root,
        prior_result=prior_result,
        prior_audit_path=prior_audit_path,
        prior_audit=prior_audit,
        reconciliation_path=reconciliation_path,
    )


__all__ = [
    "R5PreexistingHistoryContinuationBundle",
    "R5PreexistingHistoryContinuationError",
    "verify_preexisting_history_continuation_plan",
]
