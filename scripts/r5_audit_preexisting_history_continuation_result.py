#!/usr/bin/env python3
"""Independently audit a completed R5 history continuation from sealed files."""

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


class PreexistingHistoryContinuationResultAuditError(RuntimeError):
    """Raised when continuation evidence is inconsistent."""


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise PreexistingHistoryContinuationResultAuditError(
            f"invalid JSON evidence: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise PreexistingHistoryContinuationResultAuditError(
            f"JSON evidence is not an object: {path}"
        )
    return value


def _verify_aggregate(
    *,
    manifest: Mapping[str, Any],
    result: Mapping[str, Any],
    attempt_audits: Sequence[Mapping[str, Any]],
) -> None:
    unsigned_manifest = {
        key: value for key, value in manifest.items() if key != "manifest_sha256"
    }
    unsigned_result = {
        key: value for key, value in result.items() if key != "result_sha256"
    }
    attempts = result.get("attempts")
    if (
        manifest.get("status") != "frozen_before_real_continuation"
        or manifest.get("manifest_sha256") != canonical_sha256(unsigned_manifest)
        or manifest.get("maximum_additional_attempts") != 2
        or manifest.get("stop_after_first_verified_positive") is not True
        or manifest.get("provider_calls_before") != 94
        or manifest.get("vitis_launches_before") != 35
        or manifest.get("provider_call_upper_bound_total") != 4
        or manifest.get("vitis_launch_upper_bound_total") != 12
        or manifest.get("confidence_minimum_authorized_label") != "high"
        or manifest.get("confidence_threshold_weakened") is not False
        or manifest.get("future_files_read") is not False
        or manifest.get("future_outcomes_observed") is not False
        or manifest.get("r5_accepted") is not False
        or manifest.get("r6_started") is not False
        or result.get("manifest_sha256") != manifest.get("manifest_sha256")
        or result.get("result_sha256") != canonical_sha256(unsigned_result)
        or not isinstance(attempts, list)
        or not 1 <= len(attempts) <= 2
        or result.get("attempt_count") != len(attempts)
        or len(attempt_audits) != len(attempts)
        or result.get("provider_calls")
        != sum(int(item.get("provider_calls", -1)) for item in attempts)
        or result.get("vitis_launches")
        != sum(int(item.get("vitis_launches", -1)) for item in attempts)
        or not 0 <= int(result.get("provider_calls", -1)) <= 4
        or not 0 <= int(result.get("vitis_launches", -1)) <= 12
        or result.get("provider_calls_after") != 94 + result["provider_calls"]
        or result.get("vitis_launches_after") != 35 + result["vitis_launches"]
        or result.get("confidence_threshold_weakened") is not False
        or result.get("future_files_read") is not False
        or result.get("future_outcomes_observed") is not False
        or result.get("trusted_revision_created") is not False
        or result.get("r5_real_campaign_allowed") is not False
        or result.get("r5_accepted") is not False
        or result.get("r6_started") is not False
    ):
        raise PreexistingHistoryContinuationResultAuditError(
            "continuation aggregate invariants failed"
        )
    ordinals = [item.get("attempt_ordinal") for item in attempts]
    if ordinals not in ([2], [2, 3]):
        raise PreexistingHistoryContinuationResultAuditError(
            "continuation attempt ordering is invalid"
        )
    normalized = [item.get("status") for item in attempt_audits]
    record_statuses = [item.get("status") for item in attempts]
    expected_record_statuses = [
        "verified_positive"
        if status == "clean_verified_positive"
        else "abstained"
        if status == "clean_safe_calibration_abstention"
        else "invalid"
        for status in normalized
    ]
    if record_statuses != expected_record_statuses or "invalid" in expected_record_statuses:
        raise PreexistingHistoryContinuationResultAuditError(
            "attempt audit status does not match continuation"
        )
    positive_count = expected_record_statuses.count("verified_positive")
    if result.get("status") == "verified_positive":
        if (
            positive_count != 1
            or expected_record_statuses[-1] != "verified_positive"
            or result.get("stop_reason") != "first_verified_positive"
            or result.get("stopped_after_first_verified_positive") is not True
            or result.get("verified_positive_episode_count") != 1
        ):
            raise PreexistingHistoryContinuationResultAuditError(
                "verified-positive stop condition failed"
            )
    elif result.get("status") == "exhausted_safe_abstention":
        if (
            len(attempts) != 2
            or positive_count != 0
            or result.get("stop_reason")
            != "maximum_additional_attempts_exhausted"
            or result.get("stopped_after_first_verified_positive") is not False
            or result.get("verified_positive_episode_count") != 0
        ):
            raise PreexistingHistoryContinuationResultAuditError(
                "safe-abstention exhaustion condition failed"
            )
    else:
        raise PreexistingHistoryContinuationResultAuditError(
            "continuation is not independently admissible"
        )


def audit(root: Path) -> dict[str, Any]:
    root = root.expanduser().resolve()
    manifest_path = root / "continuation_manifest.json"
    result_path = root / "continuation_result.json"
    manifest = _load(manifest_path)
    result = _load(result_path)
    attempts = result.get("attempts")
    if not isinstance(attempts, list):
        raise PreexistingHistoryContinuationResultAuditError(
            "continuation attempts are missing"
        )
    attempt_audits: list[dict[str, Any]] = []
    for record in attempts:
        if not isinstance(record, Mapping):
            raise PreexistingHistoryContinuationResultAuditError(
                "continuation attempt record is invalid"
            )
        attempt_root = Path(str(record.get("run_root", ""))).resolve()
        if attempt_root.parent != root:
            raise PreexistingHistoryContinuationResultAuditError(
                "continuation attempt escapes its campaign root"
            )
        expected = {
            "acquisition_manifest_file_sha256": attempt_root
            / "acquisition_manifest.json",
            "acquisition_result_file_sha256": attempt_root
            / "acquisition_result.json",
            "evidence_archive_sha256": attempt_root / "evidence.zip",
        }
        if any(record.get(key) != file_sha256(path) for key, path in expected.items()):
            raise PreexistingHistoryContinuationResultAuditError(
                "continuation attempt file hash mismatch"
            )
        attempt_audits.append(audit_attempt(attempt_root))
    _verify_aggregate(
        manifest=manifest,
        result=result,
        attempt_audits=attempt_audits,
    )
    value: dict[str, Any] = {
        "schema_version": 1,
        "auditor": "r5-preexisting-history-continuation-file-auditor-v1",
        "execution_boundary": "separate_python_process_file_only",
        "status": "clean_" + str(result["status"]),
        "repository_head": result.get("repository_head"),
        "campaign_id": result.get("campaign_id"),
        "continuation_manifest_file_sha256": file_sha256(manifest_path),
        "continuation_result_file_sha256": file_sha256(result_path),
        "attempt_audits": attempt_audits,
        "attempt_count": result["attempt_count"],
        "provider_calls": result["provider_calls"],
        "vitis_launches": result["vitis_launches"],
        "auditor_provider_calls": 0,
        "auditor_vitis_launches": 0,
        "confidence_threshold_weakened": False,
        "future_files_read": False,
        "future_outcomes_observed": False,
        "critical_finding_count": 0,
        "findings": [],
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
    print("R5_PREEXISTING_HISTORY_CONTINUATION_RESULT_AUDIT_STATUS=" + result["status"])
    print("AUDITOR_PROVIDER_CALLS=0")
    print("AUDITOR_VITIS_LAUNCHES=0")
    print("CRITICAL_FINDINGS=0")
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
