#!/usr/bin/env python3
"""Independently audit sealed R5 pre-existing history evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping, Sequence
import zipfile


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PRIVATE_TAGS = ("<think", "</think", "<reasoning", "</reasoning")
_RAW_FIELDS = frozenset(
    {
        "private_reasoning",
        "provider_response",
        "raw_exception",
        "raw_message",
        "raw_provider_response",
        "reasoning_content",
        "response_content",
        "secret_value",
    }
)


class PreexistingHistoryResultAuditError(RuntimeError):
    """Raised when sealed acquisition evidence is inconsistent."""


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_bytes(value: bytes, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise PreexistingHistoryResultAuditError(
            f"invalid JSON evidence: {label}"
        ) from exc
    if not isinstance(parsed, dict):
        raise PreexistingHistoryResultAuditError(
            f"JSON evidence is not an object: {label}"
        )
    return parsed


def _assert_privacy(value: Any) -> None:
    def visit(item: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(item, Mapping):
            for raw_key, child in item.items():
                key = str(raw_key)
                lowered = key.casefold()
                if lowered in _RAW_FIELDS:
                    raise PreexistingHistoryResultAuditError(
                        "raw private field persisted: " + ".".join((*path, key))
                    )
                if lowered.endswith("_persisted") and child not in (False, "false"):
                    raise PreexistingHistoryResultAuditError(
                        "unsafe persistence flag: " + ".".join((*path, key))
                    )
                visit(child, (*path, key))
            return
        if isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                visit(child, (*path, str(index)))
            return
        if isinstance(item, str) and any(
            tag in item.casefold() for tag in _PRIVATE_TAGS
        ):
            raise PreexistingHistoryResultAuditError(
                "private reasoning tag persisted"
            )

    visit(value)


def _verify_archive(root: Path) -> tuple[dict[str, bytes], str, str]:
    archive = root / "evidence.zip"
    sidecar = root / "evidence.zip.sha256"
    content_path = root / "evidence_content_manifest.json"
    if not archive.is_file() or not sidecar.is_file() or not content_path.is_file():
        raise PreexistingHistoryResultAuditError("sealed evidence files are missing")
    archive_sha = _file_sha256(archive)
    try:
        sidecar_sha, sidecar_name = sidecar.read_text(encoding="utf-8").split()
    except (OSError, ValueError) as exc:
        raise PreexistingHistoryResultAuditError("archive sidecar is invalid") from exc
    if sidecar_sha != archive_sha or sidecar_name != "evidence.zip":
        raise PreexistingHistoryResultAuditError("archive sidecar mismatch")
    root_manifest_bytes = content_path.read_bytes()
    manifest = _load_bytes(root_manifest_bytes, "evidence_content_manifest.json")
    files = manifest.get("files")
    if manifest.get("schema_version") != 1 or not isinstance(files, Mapping):
        raise PreexistingHistoryResultAuditError("content manifest is invalid")
    with zipfile.ZipFile(archive) as evidence_zip:
        infos = evidence_zip.infolist()
        names = [item.filename for item in infos]
        if len(names) != len(set(names)):
            raise PreexistingHistoryResultAuditError("archive has duplicate members")
        expected_names = sorted([*files, "evidence_content_manifest.json"])
        if sorted(names) != expected_names:
            raise PreexistingHistoryResultAuditError("archive member set mismatch")
        if evidence_zip.read("evidence_content_manifest.json") != root_manifest_bytes:
            raise PreexistingHistoryResultAuditError("archive manifest bytes mismatch")
        payloads: dict[str, bytes] = {}
        for name, expected in files.items():
            if (
                not isinstance(name, str)
                or PurePosixPath(name).is_absolute()
                or ".." in PurePosixPath(name).parts
                or not isinstance(expected, str)
                or not _SHA256.fullmatch(expected)
            ):
                raise PreexistingHistoryResultAuditError(
                    "content manifest entry is unsafe"
                )
            data = evidence_zip.read(name)
            if hashlib.sha256(data).hexdigest() != expected:
                raise PreexistingHistoryResultAuditError(
                    f"archive content hash mismatch: {name}"
                )
            extracted = root / Path(*PurePosixPath(name).parts)
            if not extracted.is_file() or _file_sha256(extracted) != expected:
                raise PreexistingHistoryResultAuditError(
                    f"extracted content hash mismatch: {name}"
                )
            payloads[name] = data
    return payloads, archive_sha, _file_sha256(content_path)


def _verify_common(
    payloads: Mapping[str, bytes],
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _load_bytes(
        payloads.get("acquisition_manifest.json", b""),
        "acquisition_manifest.json",
    )
    result = _load_bytes(
        payloads.get("acquisition_result.json", b""),
        "acquisition_result.json",
    )
    manifest_hash = manifest.get("manifest_sha256")
    result_hash = result.get("result_sha256")
    if (
        not isinstance(manifest_hash, str)
        or manifest_hash
        != _canonical_sha256(
            {key: value for key, value in manifest.items() if key != "manifest_sha256"}
        )
        or not isinstance(result_hash, str)
        or result_hash
        != _canonical_sha256(
            {key: value for key, value in result.items() if key != "result_sha256"}
        )
        or result.get("manifest_sha256") != manifest_hash
        or manifest.get("arm") != "A2"
        or manifest.get("authorization_mode") != "advisor_only"
        or manifest.get("memory_mode") != "none"
        or manifest.get("maximum_candidate_mutations") != 1
        or manifest.get("provider_call_upper_bound") != 2
        or manifest.get("vitis_launch_upper_bound") != 6
        or manifest.get("future_files_read") is not False
        or manifest.get("future_outcomes_observed") is not False
        or result.get("future_files_read") is not False
        or result.get("future_outcomes_observed") is not False
        or result.get("accepted_by_acquisition_script") is not False
        or result.get("r5_accepted") is not False
        or result.get("r6_started") is not False
    ):
        raise PreexistingHistoryResultAuditError(
            "manifest or result common invariants failed"
        )
    _assert_privacy(manifest)
    _assert_privacy(result)
    return manifest, result


def _verify_diagnostic(result: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    events = result.get("diagnostic_events")
    shadows = result.get("r2_shadow_diagnostics")
    if (
        not isinstance(events, list)
        or len(events) != 1
        or not isinstance(events[0], Mapping)
        or not isinstance(shadows, list)
        or len(shadows) != 1
        or not isinstance(shadows[0], Mapping)
    ):
        raise PreexistingHistoryResultAuditError("one diagnostic/shadow pair is required")
    event = dict(events[0])
    shadow = dict(shadows[0])
    items = event.get("diagnostic_items")
    advisory = shadow.get("advisory")
    if (
        event.get("stage") != "csynth"
        or event.get("physical_tool_launched") is not True
        or event.get("evidence_complete") is not True
        or event.get("hidden_input_count") != 0
        or not isinstance(items, list)
        or len(items) != 1
        or not isinstance(items[0].get("diagnostic_code"), str)
        or not items[0].get("diagnostic_code")
        or items[0].get("stage") != "csynth"
        or items[0].get("severity") != "error"
        or not isinstance(items[0].get("detail"), str)
        or not items[0].get("detail")
        or shadow.get("event_id") != event.get("event_id")
        or shadow.get("input_status") != "eligible"
        or shadow.get("critical_safety_violation") is not False
        or shadow.get("equivalence", {}).get("equivalent") is not True
        or not isinstance(advisory, Mapping)
        or advisory.get("suspected_failure_class") != "unsupported_construct"
        or advisory.get("suspected_owner") != "candidate"
        or advisory.get("repair_scope") != "candidate_only"
        or advisory.get("abstain_reason") is not None
        or not set(advisory.get("evidence_refs", ())).issubset(
            set(event.get("evidence_refs", ()))
        )
    ):
        raise PreexistingHistoryResultAuditError("diagnostic pair invariants failed")
    return event, shadow


def _verify_positive(
    *,
    payloads: Mapping[str, bytes],
    manifest: Mapping[str, Any],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    integration = result.get("r5_integration")
    if (
        result.get("status") != "verified_positive"
        or result.get("provider_calls") != 2
        or not isinstance(result.get("vitis_launches"), int)
        or not 3 <= result["vitis_launches"] <= 6
        or not isinstance(integration, Mapping)
        or integration.get("status") != "verified_positive"
        or integration.get("main_result_unchanged") is not True
        or integration.get("accepted_by_integration") is not False
    ):
        raise PreexistingHistoryResultAuditError("positive result invariants failed")
    episode_path = integration.get("episode_path")
    if not isinstance(episode_path, str):
        raise PreexistingHistoryResultAuditError("positive episode path is missing")
    episode_name = Path(episode_path).name
    relative = "ledger/" + episode_name
    episode = _load_bytes(payloads.get(relative, b""), relative)
    payload = episode.get("payload")
    if (
        episode.get("outcome") != "verified_positive"
        or episode.get("source_sha256")
        != manifest.get("case_identity", {}).get("source_sha256")
        or episode.get("manifest_sha256") != manifest.get("manifest_sha256")
        or not isinstance(payload, Mapping)
        or payload.get("candidate_before_sha256")
        == payload.get("candidate_after_sha256")
        or payload.get("provider_call_count") != 1
        or not payload.get("formal_validation_id")
        or not payload.get("validation_evidence_refs")
        or episode.get("agent_safe_summary", {}).get("false_repair") is not False
        or episode.get("agent_safe_summary", {}).get("unsafe_scope") is not False
        or episode.get("agent_safe_summary", {}).get(
            "critical_safety_violation"
        )
        is not False
    ):
        raise PreexistingHistoryResultAuditError("positive episode invariants failed")
    return {
        "status": "clean_verified_positive",
        "episode_id": episode.get("episode_id"),
        "episode_sha256": hashlib.sha256(payloads[relative]).hexdigest(),
        "source_sha256": episode.get("source_sha256"),
        "context_signature": episode.get("context_signature"),
    }


def _verify_abstention(result: Mapping[str, Any], shadow: Mapping[str, Any]) -> dict[str, Any]:
    integration = result.get("r5_integration")
    advisory = shadow.get("advisory", {})
    if (
        result.get("status") != "abstained"
        or result.get("provider_calls") != 1
        or result.get("vitis_launches") != 2
        or advisory.get("confidence") not in {"low", "medium"}
        or not isinstance(integration, Mapping)
        or integration.get("status") != "abstained"
        or integration.get("reason") != "r2_calibration_unverified"
        or integration.get("calibration", {}).get("verified") is not False
        or "confidence_label_not_calibrated"
        not in integration.get("calibration", {}).get("reasons", ())
        or integration.get("main_result_unchanged") is not True
        or integration.get("accepted_by_integration") is not False
    ):
        raise PreexistingHistoryResultAuditError("abstention invariants failed")
    return {
        "status": "clean_safe_calibration_abstention",
        "confidence": advisory.get("confidence"),
        "reason": integration.get("reason"),
    }


def _verify_pre_r2_deterministic_boundary(
    result: Mapping[str, Any],
) -> dict[str, Any]:
    events = result.get("diagnostic_events")
    shadows = result.get("r2_shadow_diagnostics")
    integration = result.get("r5_integration")
    if (
        result.get("status") != "abstained"
        or result.get("provider_calls") != 0
        or result.get("vitis_launches") != 2
        or not isinstance(events, list)
        or len(events) != 1
        or not isinstance(events[0], Mapping)
        or not isinstance(shadows, list)
        or len(shadows) != 1
        or not isinstance(shadows[0], Mapping)
        or not isinstance(integration, Mapping)
        or integration.get("status") != "abstained"
        or integration.get("reason") != "eligible_r2_event_not_unique"
        or integration.get("main_result_unchanged") is not True
        or integration.get("accepted_by_integration") is not False
    ):
        raise PreexistingHistoryResultAuditError(
            "pre-R2 deterministic-boundary invariants failed"
        )
    event = events[0]
    shadow = shadows[0]
    advisory = shadow.get("advisory")
    items = event.get("diagnostic_items")
    if (
        event.get("stage") != "csynth"
        or event.get("owner") != "candidate"
        or event.get("repair_scope") != "candidate_only"
        or event.get("failure_classes") != ["unsupported_construct"]
        or event.get("physical_tool_launched") is not True
        or event.get("evidence_complete") is not True
        or event.get("hidden_input_count") != 0
        or not isinstance(items, list)
        or not items
        or any(
            not isinstance(item, Mapping)
            or item.get("stage") != "csynth"
            or item.get("severity") != "error"
            or item.get("owner") != "candidate"
            or item.get("category") != "unsupported_construct"
            or item.get("classification_confidence") != "high"
            or not isinstance(item.get("detail"), str)
            or not item.get("detail")
            for item in items
        )
        or shadow.get("event_id") != event.get("event_id")
        or shadow.get("input_status")
        != "rejected:owner_not_unknown_or_review"
        or shadow.get("request_sha256") is not None
        or shadow.get("provider_identity") != {}
        or shadow.get("accounting") != {}
        or shadow.get("critical_safety_violation") is not False
        or shadow.get("equivalence", {}).get("equivalent") is not True
        or not isinstance(advisory, Mapping)
        or advisory.get("suspected_owner") != "unknown"
        or advisory.get("suspected_failure_class") != "unknown"
        or advisory.get("repair_scope") != "none"
        or advisory.get("confidence") != "low"
        or advisory.get("evidence_refs") != []
        or advisory.get("abstain_reason") != "owner_not_unknown_or_review"
    ):
        raise PreexistingHistoryResultAuditError(
            "pre-R2 deterministic diagnostic invariants failed"
        )
    return {
        "status": "clean_pre_r2_deterministic_boundary",
        "reason": "deterministic_candidate_owner_precedes_r2",
        "diagnostic_item_count": len(items),
        "r2_provider_calls": 0,
        "mutation_count": 0,
    }


def _verify_pre_provider_contract_failure(
    *,
    payloads: Mapping[str, bytes],
    manifest: Mapping[str, Any],
    result: Mapping[str, Any],
    shadow: Mapping[str, Any],
) -> dict[str, Any]:
    integration = result.get("r5_integration")
    advisory = shadow.get("advisory", {})
    controller = (
        integration.get("r4_controller_result", {})
        if isinstance(integration, Mapping)
        else {}
    )
    reasons = controller.get("reasons") if isinstance(controller, Mapping) else None
    allowed_reasons = {
        "pre_provider_mutation_contract_failure",
        "pre_provider_model_adapter_failure",
    }
    reason = reasons[0] if isinstance(reasons, list) and len(reasons) == 1 else None
    if (
        result.get("status") != "inconclusive"
        or result.get("provider_calls") != 1
        or result.get("vitis_launches") != 2
        or advisory.get("confidence") != "high"
        or not isinstance(integration, Mapping)
        or integration.get("status") != "inconclusive"
        or integration.get("main_result_unchanged") is not True
        or integration.get("accepted_by_integration") is not False
        or not isinstance(controller, Mapping)
        or controller.get("outcome") != "inconclusive"
        or reason not in allowed_reasons
        or controller.get("provider_call_count") != 0
        or controller.get("mutation_count") != 0
        or controller.get("after_candidate_sha256") is not None
        or controller.get("formal_validation_id") is not None
    ):
        raise PreexistingHistoryResultAuditError(
            "pre-provider contract-failure invariants failed"
        )
    episode_path = integration.get("episode_path")
    if not isinstance(episode_path, str):
        raise PreexistingHistoryResultAuditError(
            "pre-provider contract-failure episode is missing"
        )
    relative = "ledger/" + Path(episode_path).name
    episode = _load_bytes(payloads.get(relative, b""), relative)
    payload = episode.get("payload")
    summary = episode.get("agent_safe_summary")
    actual = payload.get("budget_actual", {}) if isinstance(payload, Mapping) else {}
    delta = actual.get("budget_delta", {}) if isinstance(actual, Mapping) else {}
    if (
        episode.get("outcome") != "inconclusive"
        or episode.get("source_sha256")
        != manifest.get("case_identity", {}).get("source_sha256")
        or episode.get("manifest_sha256") != manifest.get("manifest_sha256")
        or not isinstance(payload, Mapping)
        or payload.get("outcome_reason") != reason
        or payload.get("candidate_before_sha256")
        != result.get("initial_candidate_sha256")
        or payload.get("candidate_after_sha256") is not None
        or payload.get("formal_validation_id") is not None
        or payload.get("provider_call_count") != 0
        or not isinstance(actual, Mapping)
        or actual.get("provider_calls") != 0
        or actual.get("mutation_calls") != 0
        or not isinstance(delta, Mapping)
        or delta.get("llm_calls") != 0
        or not isinstance(summary, Mapping)
        or summary.get("reason") != reason
        or summary.get("false_repair") is not False
        or summary.get("unsafe_scope") is not False
        or summary.get("critical_safety_violation") is not False
    ):
        raise PreexistingHistoryResultAuditError(
            "pre-provider contract-failure episode invariants failed"
        )
    return {
        "status": "clean_" + str(reason),
        "confidence": advisory.get("confidence"),
        "reason": reason,
        "episode_id": episode.get("episode_id"),
        "episode_sha256": hashlib.sha256(payloads[relative]).hexdigest(),
        "mutation_provider_calls": 0,
        "mutation_count": 0,
    }


def audit(root: Path) -> dict[str, Any]:
    root = root.expanduser().resolve()
    payloads, archive_sha, content_sha = _verify_archive(root)
    manifest, result = _verify_common(payloads)
    pre_r2_boundary = (
        result.get("status") == "abstained"
        and result.get("provider_calls") == 0
    )
    shadow: Mapping[str, Any] = {}
    if not pre_r2_boundary:
        _, shadow = _verify_diagnostic(result)
    if pre_r2_boundary:
        outcome = _verify_pre_r2_deterministic_boundary(result)
    elif result.get("status") == "verified_positive":
        outcome = _verify_positive(
            payloads=payloads,
            manifest=manifest,
            result=result,
        )
    elif result.get("status") == "abstained":
        outcome = _verify_abstention(result, shadow)
    elif result.get("status") == "inconclusive":
        outcome = _verify_pre_provider_contract_failure(
            payloads=payloads,
            manifest=manifest,
            result=result,
            shadow=shadow,
        )
    else:
        raise PreexistingHistoryResultAuditError(
            "result is neither verified-positive nor safe abstention"
        )
    value: dict[str, Any] = {
        "schema_version": 1,
        "auditor": "r5-preexisting-history-file-auditor-v1",
        "execution_boundary": "separate_python_process_file_only",
        "status": outcome["status"],
        "outcome": outcome,
        "repository_head": manifest.get("repository_head"),
        "run_id": result.get("run_id"),
        "source_sha256": manifest.get("case_identity", {}).get("source_sha256"),
        "evidence_archive_sha256": archive_sha,
        "content_manifest_file_sha256": content_sha,
        "provider_calls": result.get("provider_calls"),
        "vitis_launches": result.get("vitis_launches"),
        "auditor_provider_calls": 0,
        "auditor_vitis_launches": 0,
        "future_files_read": False,
        "future_outcomes_observed": False,
        "critical_finding_count": 0,
        "blocking_finding_count": int(
            str(outcome["status"]).startswith("clean_pre_provider_")
            or str(outcome["status"]).startswith("clean_pre_r2_")
        ),
        "findings": (
            [
                {
                    "code": str(outcome.get("reason")),
                    "severity": "blocking",
                    "message": "R5 mutation preparation failed before its Provider call.",
                }
            ]
            if str(outcome["status"]).startswith("clean_pre_provider_")
            else (
                [
                    {
                        "code": "history_source_outside_r2_boundary",
                        "severity": "blocking",
                        "message": (
                            "Deterministic Candidate ownership was already "
                            "proven, so this source belongs to pre-R2 recovery."
                        ),
                    }
                ]
                if str(outcome["status"]).startswith("clean_pre_r2_")
                else []
            )
        ),
        "r5_accepted": False,
        "r6_started": False,
    }
    value["audit_sha256"] = _canonical_sha256(value)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence_root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = audit(args.evidence_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("R5_PREEXISTING_HISTORY_RESULT_AUDIT_STATUS=" + str(result["status"]))
    print("AUDITOR_PROVIDER_CALLS=0")
    print("AUDITOR_VITIS_LAUNCHES=0")
    print("CRITICAL_FINDINGS=0")
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
