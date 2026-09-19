#!/usr/bin/env python3
"""Independently audit the one-attempt post-fix R5 history resume."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = Path(__file__).resolve().parent
for value in (str(REPOSITORY_ROOT), str(SCRIPT_ROOT)):
    if value not in sys.path:
        sys.path.insert(0, value)

from agrefactor.recovery.r5_historical_candidate import canonical_sha256, file_sha256
from r5_audit_preexisting_history_result import (  # type: ignore[import-not-found]
    audit as audit_attempt,
)


class PreexistingHistoryResumeResultAuditError(RuntimeError):
    """Raised when post-fix resume evidence is inconsistent."""


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise PreexistingHistoryResumeResultAuditError(
            f"invalid JSON evidence: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise PreexistingHistoryResumeResultAuditError(
            f"JSON evidence is not an object: {path}"
        )
    return value


def audit(root: Path) -> dict[str, Any]:
    root = root.expanduser().resolve()
    manifest_path = root / "resume_manifest.json"
    result_path = root / "resume_result.json"
    manifest = _load(manifest_path)
    result = _load(result_path)
    unsigned_manifest = {
        key: value for key, value in manifest.items() if key != "manifest_sha256"
    }
    unsigned_result = {
        key: value for key, value in result.items() if key != "result_sha256"
    }
    attempt = result.get("attempt")
    if (
        manifest.get("status") != "frozen_before_one_post_fix_attempt"
        or manifest.get("manifest_sha256") != canonical_sha256(unsigned_manifest)
        or manifest.get("attempts_authorized_originally") != 2
        or manifest.get("attempts_consumed") != 1
        or manifest.get("attempt_ordinal") != 3
        or manifest.get("maximum_remaining_attempts") != 1
        or manifest.get("maximum_candidate_mutations") != 1
        or manifest.get("provider_calls_before") != 95
        or manifest.get("vitis_launches_before") != 37
        or manifest.get("provider_call_upper_bound") != 2
        or manifest.get("vitis_launch_upper_bound") != 6
        or manifest.get("confidence_minimum_authorized_label") != "high"
        or manifest.get("confidence_threshold_weakened") is not False
        or manifest.get("future_files_read") is not False
        or manifest.get("future_outcomes_observed") is not False
        or manifest.get("r5_accepted") is not False
        or manifest.get("r6_started") is not False
        or result.get("manifest_sha256") != manifest.get("manifest_sha256")
        or result.get("result_sha256") != canonical_sha256(unsigned_result)
        or not isinstance(attempt, Mapping)
        or attempt.get("attempt_ordinal") != 3
        or result.get("provider_calls") != attempt.get("provider_calls")
        or result.get("vitis_launches") != attempt.get("vitis_launches")
        or not 0 <= int(result.get("provider_calls", -1)) <= 2
        or not 0 <= int(result.get("vitis_launches", -1)) <= 6
        or result.get("provider_calls_after") != 95 + result["provider_calls"]
        or result.get("vitis_launches_after") != 37 + result["vitis_launches"]
        or result.get("remaining_attempts") != 0
        or result.get("confidence_threshold_weakened") is not False
        or result.get("future_files_read") is not False
        or result.get("future_outcomes_observed") is not False
        or result.get("trusted_revision_created") is not False
        or result.get("r5_real_campaign_allowed") is not False
        or result.get("r5_accepted") is not False
        or result.get("r6_started") is not False
    ):
        raise PreexistingHistoryResumeResultAuditError(
            "post-fix resume aggregate invariants failed"
        )
    attempt_root = Path(str(attempt.get("run_root", ""))).resolve()
    if attempt_root.parent != root or attempt_root.name != "attempt-03":
        raise PreexistingHistoryResumeResultAuditError(
            "post-fix attempt escapes its resume root"
        )
    expected_files = {
        "acquisition_manifest_file_sha256": attempt_root
        / "acquisition_manifest.json",
        "acquisition_result_file_sha256": attempt_root
        / "acquisition_result.json",
        "evidence_archive_sha256": attempt_root / "evidence.zip",
    }
    if any(attempt.get(key) != file_sha256(path) for key, path in expected_files.items()):
        raise PreexistingHistoryResumeResultAuditError(
            "post-fix attempt file hash mismatch"
        )
    attempt_audit = audit_attempt(attempt_root)
    status = result.get("status")
    audit_status = attempt_audit.get("status")
    if status == "verified_positive":
        valid = (
            result.get("reason") == "remaining_attempt_verified_positive"
            and result.get("verified_positive_episode_count") == 1
            and attempt.get("status") == "verified_positive"
            and audit_status == "clean_verified_positive"
        )
    elif status == "exhausted_safe_abstention":
        valid = (
            result.get("reason") == "remaining_attempt_safely_abstained"
            and result.get("verified_positive_episode_count") == 0
            and attempt.get("status") == "abstained"
            and audit_status == "clean_safe_calibration_abstention"
        )
    elif status == "stopped_inconclusive":
        valid = (
            result.get("reason") == "remaining_attempt_inconclusive"
            and result.get("verified_positive_episode_count") == 0
            and attempt.get("status") == "inconclusive"
            and audit_status == "clean_pre_provider_mutation_contract_failure"
        )
    else:
        valid = False
    if not valid:
        raise PreexistingHistoryResumeResultAuditError(
            "post-fix resume outcome does not match attempt audit"
        )
    value: dict[str, Any] = {
        "schema_version": 1,
        "auditor": "r5-preexisting-history-post-fix-resume-file-auditor-v1",
        "execution_boundary": "separate_python_process_file_only",
        "status": "clean_" + str(status),
        "repository_head": result.get("repository_head"),
        "resume_id": result.get("resume_id"),
        "resume_manifest_file_sha256": file_sha256(manifest_path),
        "resume_result_file_sha256": file_sha256(result_path),
        "attempt_audit": attempt_audit,
        "provider_calls": result["provider_calls"],
        "vitis_launches": result["vitis_launches"],
        "provider_calls_after": result["provider_calls_after"],
        "vitis_launches_after": result["vitis_launches_after"],
        "verified_positive_episode_count": result[
            "verified_positive_episode_count"
        ],
        "auditor_provider_calls": 0,
        "auditor_vitis_launches": 0,
        "confidence_threshold_weakened": False,
        "future_files_read": False,
        "future_outcomes_observed": False,
        "critical_finding_count": int(
            attempt_audit.get("critical_finding_count", 0)
        ),
        "blocking_finding_count": int(
            attempt_audit.get("blocking_finding_count", 0)
        ),
        "findings": list(attempt_audit.get("findings", ())),
        "r5_accepted": False,
        "r6_started": False,
    }
    value["audit_sha256"] = canonical_sha256(value)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence_root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = audit(args.evidence_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name("." + args.output.name + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, args.output)
    print("R5_PREEXISTING_HISTORY_RESUME_RESULT_AUDIT_STATUS=" + result["status"])
    print("AUDITOR_PROVIDER_CALLS=0")
    print("AUDITOR_VITIS_LAUNCHES=0")
    print("CRITICAL_FINDINGS=" + str(result["critical_finding_count"]))
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
