"""Strictly import accepted R4 repair evidence into the R5 episode ledger."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any
import zipfile

from .episode_ledger import (
    AppendOnlyEpisodeLedger,
    R5EpisodeEnvelope,
    R5EpisodeOutcome,
    canonical_sha256,
)
from .r4_episode import R4RepairEpisode, R4RepairEpisodeReader
from .r4_provenance import canonical_artifact_sha256


class R5PredecessorImportError(ValueError):
    """Raised when predecessor evidence cannot be admitted without weakening R5."""


@dataclass(frozen=True, slots=True)
class R5PredecessorImportResult:
    evidence_root: str
    archive_sha256: str
    content_manifest_sha256: str
    independent_audit_sha256: str
    envelopes: tuple[R5EpisodeEnvelope, ...]
    appended_episode_ids: tuple[str, ...]
    existing_episode_ids: tuple[str, ...]

    @property
    def independent_source_count(self) -> int:
        return len({item.source_sha256 for item in self.envelopes})

    @property
    def independent_context_count(self) -> int:
        return len({item.context_signature for item in self.envelopes})

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "evidence_root": self.evidence_root,
            "archive_sha256": self.archive_sha256,
            "content_manifest_sha256": self.content_manifest_sha256,
            "independent_audit_sha256": self.independent_audit_sha256,
            "episode_ids": [item.episode_id for item in self.envelopes],
            "episode_sha256s": [item.envelope_sha256 for item in self.envelopes],
            "predecessor_episode_sha256s": [
                str(item.payload["predecessor_episode_sha256"])
                for item in self.envelopes
            ],
            "appended_episode_ids": list(self.appended_episode_ids),
            "existing_episode_ids": list(self.existing_episode_ids),
            "verified_positive_count": len(self.envelopes),
            "independent_source_count": self.independent_source_count,
            "independent_context_count": self.independent_context_count,
            "provider_calls": 0,
            "vitis_launches": 0,
            "accepted_by_importer": False,
        }


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise R5PredecessorImportError(f"invalid JSON evidence: {path.name}") from exc
    if not isinstance(value, dict):
        raise R5PredecessorImportError(f"JSON evidence is not an object: {path.name}")
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise R5PredecessorImportError(f"missing evidence file: {path.name}") from exc
    return digest.hexdigest()


def _require_sha256(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise R5PredecessorImportError(f"{name} is not a lowercase SHA-256")
    return value


def _content_file(
    *, root: Path, files: Mapping[str, Any], relative: str, archive: Path
) -> Path:
    expected = _require_sha256(files.get(relative), f"content manifest entry {relative}")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise R5PredecessorImportError("content manifest path escapes evidence root") from exc
    if _file_sha256(path) != expected:
        raise R5PredecessorImportError(f"content hash mismatch: {relative}")
    try:
        with zipfile.ZipFile(archive) as evidence_zip:
            matches = [item for item in evidence_zip.infolist() if item.filename == relative]
            if len(matches) != 1 or hashlib.sha256(
                evidence_zip.read(matches[0])
            ).hexdigest() != expected:
                raise R5PredecessorImportError(
                    f"archive content mismatch: {relative}"
                )
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise R5PredecessorImportError("Package D archive is invalid") from exc
    return path


def _matching_event(result: Mapping[str, Any], episode: R4RepairEpisode) -> dict[str, Any]:
    orchestration = result.get("orchestration_result")
    metadata = orchestration.get("metadata") if isinstance(orchestration, Mapping) else None
    events = metadata.get("diagnostic_events") if isinstance(metadata, Mapping) else None
    if not isinstance(events, list):
        raise R5PredecessorImportError("run lacks diagnostic event evidence")
    matches = [
        dict(item)
        for item in events
        if isinstance(item, Mapping) and item.get("event_id") == episode.event_ref
    ]
    if len(matches) != 1:
        raise R5PredecessorImportError("R4 episode does not have one matching diagnostic event")
    event = matches[0]
    context = _require_sha256(event.get("context_signature"), "context_signature")
    if (
        event.get("evidence_view", "agent_safe") != "agent_safe"
        or event.get("evidence_complete") is not True
        or event.get("physical_tool_launched") is not True
        or event.get("hidden_input_count", 0) != 0
        or event.get("secret_present", False)
        or event.get("private_reasoning_present", False)
        or event.get("stage") != "csynth"
    ):
        raise R5PredecessorImportError("diagnostic event violates the agent-safe boundary")
    event["context_signature"] = context
    return event


def _verify_run(
    *,
    root: Path,
    files: Mapping[str, Any],
    archive: Path,
    frozen: Mapping[str, Any],
    run_id: str,
    archive_sha256: str,
    content_manifest_sha256: str,
    independent_audit_sha256: str,
    expected_calibration_certificate_id: str,
) -> R5EpisodeEnvelope:
    result_relative = f"runs/{run_id}/result.json"
    result = _load_object(
        _content_file(
            root=root, files=files, relative=result_relative, archive=archive
        )
    )
    if (
        result.get("run_id") != run_id
        or result.get("arm") != "canary"
        or result.get("status") != "verified_positive"
        or result.get("hidden_content_exposed_to_model") is not False
        or result.get("raw_provider_response_persisted") is not False
        or result.get("secret_values_persisted") is not False
        or result.get("frozen_manifest_sha256")
        != frozen.get("canary_manifest_sha256")
    ):
        raise R5PredecessorImportError(f"{run_id} result is not an admissible positive canary")

    orchestration = result.get("orchestration_result")
    metadata = orchestration.get("metadata") if isinstance(orchestration, Mapping) else None
    integration = metadata.get("r4_integration") if isinstance(metadata, Mapping) else None
    if not isinstance(integration, Mapping):
        raise R5PredecessorImportError(f"{run_id} lacks R4 integration evidence")
    r4_result = integration.get("r4_result")
    provenance = integration.get("provenance")
    episode_path_value = integration.get("episode_path")
    if (
        integration.get("status") != "verified_positive"
        or integration.get("main_result_unchanged") is not True
        or integration.get("accepted_by_integration") is not False
        or not isinstance(r4_result, Mapping)
        or r4_result.get("outcome") != "verified_positive"
        or r4_result.get("accepted") is not True
        or r4_result.get("mutation_count") != 1
        or not isinstance(provenance, Mapping)
        or provenance.get("valid") is not True
        or not isinstance(episode_path_value, str)
    ):
        raise R5PredecessorImportError(f"{run_id} R4 integration invariants failed")

    episode_name = Path(episode_path_value).name
    if episode_name in {"", ".", ".."} or not episode_name.endswith(".json"):
        raise R5PredecessorImportError("invalid predecessor episode path")
    episode_relative = f"runs/{run_id}/episodes/{episode_name}"
    episode_path = _content_file(
        root=root, files=files, relative=episode_relative, archive=archive
    )
    try:
        episode = R4RepairEpisodeReader.read(episode_path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise R5PredecessorImportError(f"invalid predecessor R4 episode: {run_id}") from exc
    if (
        episode.outcome != "verified_positive"
        or episode.episode_hash != integration.get("episode_hash")
        or episode.candidate_before_sha256 == episode.candidate_after_sha256
        or episode.provider_call_count != 1
        or episode.auditor_result.get("status") != "clean"
    ):
        raise R5PredecessorImportError(f"{run_id} R4 episode is not verified positive")

    advisory = episode.advisory_identity
    execution = episode.execution_identity
    authorization = episode.authorization_identity
    source_sha256 = _require_sha256(execution.get("source_sha256"), "source_sha256")
    fixture_hashes = result.get("fixture_hashes")
    if (
        not isinstance(fixture_hashes, Mapping)
        or fixture_hashes.get("reference.cpp") != source_sha256
        or frozen.get("original_source_sha256") != source_sha256
        or execution.get("identity_complete") is not True
        or execution.get("hidden_input_count", 0) != 0
        or execution.get("secret_present", False)
        or execution.get("private_reasoning_present", False)
        or execution.get("stage") != "csynth"
        or advisory.get("calibration_verified") is not True
        or advisory.get("calibration_certificate_id")
        != expected_calibration_certificate_id
        or advisory.get("suspected_failure_class") != "unsupported_construct"
        or advisory.get("suspected_owner") != "candidate"
        or advisory.get("repair_scope") != "candidate_only"
        or advisory.get("confidence") != "high"
        or advisory.get("abstain_reason") is not None
        or authorization.get("canary_manifest_sha256")
        != frozen.get("canary_manifest_sha256")
    ):
        raise R5PredecessorImportError(f"{run_id} identity or calibration invariants failed")

    event = _matching_event(result, episode)
    execution_identity_sha256 = canonical_artifact_sha256(dict(execution))
    payload = {
        "predecessor_episode_id": episode.episode_id,
        "predecessor_episode_sha256": episode.episode_hash,
        "predecessor_archive_sha256": archive_sha256,
        "predecessor_content_manifest_sha256": content_manifest_sha256,
        "predecessor_run_id": run_id,
        "predecessor_case_id": execution.get("case_id"),
        "failure_family": "unsupported_construct",
        "stage": "csynth",
        "owner": "candidate",
        "candidate_before_sha256": episode.candidate_before_sha256,
        "candidate_after_sha256": episode.candidate_after_sha256,
        "formal_validation_id": episode.formal_validation_id,
        "validation_evidence_refs": list(episode.validation_evidence_refs),
        "provider_call_count": episode.provider_call_count,
        "vitis_phase_count": episode.vitis_phase_count,
        "outcome_reason": episode.outcome_reason,
    }
    return R5EpisodeEnvelope(
        episode_kind="r4_repair",
        episode_id="r5-import-" + episode.episode_id,
        payload_schema_version="r4-predecessor-import-v1",
        payload=payload,
        execution_identity_sha256=execution_identity_sha256,
        source_sha256=source_sha256,
        context_signature=str(event["context_signature"]),
        created_at=episode.created_at,
        observed_at=episode.created_at,
        lineage=(episode.episode_id, *episode.lineage),
        agent_safe_summary={
            "failure_family": "unsupported_construct",
            "stage": "csynth",
            "owner": "candidate",
            "false_repair": False,
            "unsafe_scope": False,
            "critical_safety_violation": False,
            "predecessor_episode_sha256": episode.episode_hash,
            "independent_review_sha256": independent_audit_sha256,
        },
        outcome=R5EpisodeOutcome.VERIFIED_POSITIVE,
        manifest_sha256=content_manifest_sha256,
    )


def verify_package_d_predecessor(
    *,
    evidence_root: str | Path,
    expected_archive_sha256: str,
    expected_calibration_certificate_id: str,
    run_ids: Sequence[str] = ("canary-02", "canary-03"),
) -> tuple[R5EpisodeEnvelope, ...]:
    root = Path(evidence_root).expanduser().resolve()
    archive_sha256 = _require_sha256(expected_archive_sha256, "expected_archive_sha256")
    archive = root / "package_d_v1_6_evidence.zip"
    if _file_sha256(archive) != archive_sha256:
        raise R5PredecessorImportError("Package D archive hash mismatch")
    sidecar = root / "package_d_v1_6_evidence.zip.sha256"
    try:
        sidecar_hash = sidecar.read_text(encoding="utf-8").split()[0]
    except (OSError, IndexError) as exc:
        raise R5PredecessorImportError("Package D archive sidecar is invalid") from exc
    if sidecar_hash != archive_sha256:
        raise R5PredecessorImportError("Package D archive sidecar mismatch")

    package_result = _load_object(root / "PACKAGE_D_RESULT.json")
    audit_path = root / "independent_audit.json"
    audit = _load_object(audit_path)
    audit_sha256 = _file_sha256(audit_path)
    if (
        package_result.get("evidence_archive_sha256") != archive_sha256
        or package_result.get("status") != "ready_for_manual_checkpoint"
        or package_result.get("r4_ready_for_manual_checkpoint") is not True
        or package_result.get("critical_finding_count") != 0
        or package_result.get("findings") != []
        or package_result.get("execution_boundary")
        != "separate_python_process_file_only"
        or audit.get("status") != "ready_for_manual_checkpoint"
        or audit.get("r4_ready_for_manual_checkpoint") is not True
        or audit.get("critical_finding_count") != 0
        or audit.get("findings") != []
        or audit.get("execution_boundary") != "separate_python_process_file_only"
    ):
        raise R5PredecessorImportError("Package D independent acceptance evidence is not clean")

    content_path = root / "package_d_content_manifest.json"
    try:
        with zipfile.ZipFile(archive) as evidence_zip:
            names = evidence_zip.namelist()
            if len(names) != len(set(names)):
                raise R5PredecessorImportError("Package D archive has duplicate members")
            archived_manifest = evidence_zip.read("package_d_content_manifest.json")
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise R5PredecessorImportError("Package D archive is invalid") from exc
    if archived_manifest != content_path.read_bytes():
        raise R5PredecessorImportError(
            "Package D content manifest is not bound to the accepted archive"
        )
    content = _load_object(content_path)
    files = content.get("files")
    if content.get("schema_version") != 1 or not isinstance(files, Mapping):
        raise R5PredecessorImportError("Package D content manifest is invalid")
    content_manifest_sha256 = _file_sha256(content_path)
    _content_file(
        root=root,
        files=files,
        relative="independent_audit.json",
        archive=archive,
    )
    frozen_path = _content_file(
        root=root, files=files, relative="frozen_manifest.json", archive=archive
    )
    campaign_path = _content_file(
        root=root, files=files, relative="campaign_index.json", archive=archive
    )
    frozen = _load_object(frozen_path)
    campaign = _load_object(campaign_path)
    if (
        frozen.get("manifest_schema_version") != 2
        or frozen.get("repository_branch") != "research-roadmap-v2.3"
        or frozen.get("calibration_evidence", {}).get("certificate", {}).get("certificate_id")
        != expected_calibration_certificate_id
        or frozen.get("calibration_evidence", {}).get("certificate", {}).get("accepted")
        is not True
        or campaign.get("frozen_manifest_sha256")
        != frozen.get("canary_manifest_sha256")
    ):
        raise R5PredecessorImportError("Package D frozen identity is invalid")
    records = campaign.get("records")
    if not isinstance(records, list):
        raise R5PredecessorImportError("Package D campaign index is invalid")
    statuses = {
        item.get("run_id"): item.get("status")
        for item in records
        if isinstance(item, Mapping)
    }
    normalized_run_ids = tuple(run_ids)
    if (
        not normalized_run_ids
        or len(normalized_run_ids) != len(set(normalized_run_ids))
        or any(statuses.get(run_id) != "verified_positive" for run_id in normalized_run_ids)
    ):
        raise R5PredecessorImportError("requested predecessor runs are not verified positive")

    envelopes = tuple(
        _verify_run(
            root=root,
            files=files,
            archive=archive,
            frozen=frozen,
            run_id=run_id,
            archive_sha256=archive_sha256,
            content_manifest_sha256=content_manifest_sha256,
            independent_audit_sha256=audit_sha256,
            expected_calibration_certificate_id=expected_calibration_certificate_id,
        )
        for run_id in normalized_run_ids
    )
    if len({item.episode_id for item in envelopes}) != len(envelopes):
        raise R5PredecessorImportError("predecessor import produced duplicate episode ids")
    return envelopes


def import_package_d_predecessor(
    *,
    evidence_root: str | Path,
    ledger_root: str | Path,
    expected_archive_sha256: str,
    expected_calibration_certificate_id: str,
    run_ids: Sequence[str] = ("canary-02", "canary-03"),
) -> R5PredecessorImportResult:
    root = Path(evidence_root).expanduser().resolve()
    envelopes = verify_package_d_predecessor(
        evidence_root=root,
        expected_archive_sha256=expected_archive_sha256,
        expected_calibration_certificate_id=expected_calibration_certificate_id,
        run_ids=run_ids,
    )
    ledger = AppendOnlyEpisodeLedger(ledger_root)
    appended: list[str] = []
    existing: list[str] = []
    for envelope in envelopes:
        prior = ledger.get(envelope.episode_id)
        if prior is not None:
            if prior.envelope_sha256 != envelope.envelope_sha256:
                raise R5PredecessorImportError("existing predecessor episode changed")
            existing.append(envelope.episode_id)
            continue
        ledger.append(envelope)
        appended.append(envelope.episode_id)
    return R5PredecessorImportResult(
        evidence_root=str(root),
        archive_sha256=expected_archive_sha256,
        content_manifest_sha256=_file_sha256(root / "package_d_content_manifest.json"),
        independent_audit_sha256=_file_sha256(root / "independent_audit.json"),
        envelopes=envelopes,
        appended_episode_ids=tuple(appended),
        existing_episode_ids=tuple(existing),
    )


__all__ = [
    "R5PredecessorImportError",
    "R5PredecessorImportResult",
    "import_package_d_predecessor",
    "verify_package_d_predecessor",
]
