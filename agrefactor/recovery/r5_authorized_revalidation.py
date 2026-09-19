"""Verify one frozen R5 revalidation of a previously authorized Candidate."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping
import zipfile

from agrefactor.recovery.r5_historical_candidate import (
    canonical_sha256,
    file_sha256,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RUNTIME_KIND = "public_differential_self_check_v1"


class R5AuthorizedRevalidationError(ValueError):
    """Raised when sealed Candidate revalidation evidence is incompatible."""


def _load_json_bytes(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise R5AuthorizedRevalidationError(f"invalid JSON: {label}") from exc
    if not isinstance(value, dict):
        raise R5AuthorizedRevalidationError(f"JSON root is not an object: {label}")
    return value


def _load_json_file(path: Path, label: str) -> dict[str, Any]:
    try:
        return _load_json_bytes(path.read_bytes(), label)
    except OSError as exc:
        raise R5AuthorizedRevalidationError(f"missing evidence: {label}") from exc


def _require_hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise R5AuthorizedRevalidationError(f"invalid SHA-256: {label}")
    return value


def _repo_file(
    repository: Path,
    relative: Any,
    expected_sha256: Any,
    label: str,
) -> tuple[str, Path]:
    if not isinstance(relative, str) or not relative:
        raise R5AuthorizedRevalidationError(f"missing path: {label}")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        raise R5AuthorizedRevalidationError(f"unsafe path: {label}")
    path = (repository / Path(*pure.parts)).resolve()
    try:
        path.relative_to(repository)
    except ValueError as exc:
        raise R5AuthorizedRevalidationError(f"unsafe path: {label}") from exc
    if path.is_symlink() or not path.is_file():
        raise R5AuthorizedRevalidationError(f"missing path: {label}")
    expected = _require_hash(expected_sha256, label + " hash")
    if file_sha256(path) != expected:
        raise R5AuthorizedRevalidationError(f"file hash mismatch: {label}")
    return relative, path


def _sealed_payloads(
    archive: Path,
    *,
    archive_sha256: str,
    content_manifest_file_sha256: str,
) -> dict[str, bytes]:
    if archive.is_symlink() or not archive.is_file():
        raise R5AuthorizedRevalidationError("sealed evidence archive is missing")
    if file_sha256(archive) != archive_sha256:
        raise R5AuthorizedRevalidationError("sealed evidence archive hash mismatch")
    try:
        with zipfile.ZipFile(archive) as sealed:
            infos = sealed.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise R5AuthorizedRevalidationError(
                    "sealed evidence archive has duplicate members"
                )
            for info in infos:
                path = PurePosixPath(info.filename)
                mode = info.external_attr >> 16
                if (
                    info.is_dir()
                    or path.is_absolute()
                    or ".." in path.parts
                    or (mode & 0o170000) == 0o120000
                ):
                    raise R5AuthorizedRevalidationError(
                        "sealed evidence archive has an unsafe member"
                    )
            payloads = {info.filename: sealed.read(info) for info in infos}
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise R5AuthorizedRevalidationError("sealed evidence archive is invalid") from exc
    manifest_name = "evidence_content_manifest.json"
    manifest_bytes = payloads.get(manifest_name)
    if manifest_bytes is None:
        raise R5AuthorizedRevalidationError("sealed content manifest is missing")
    if hashlib.sha256(manifest_bytes).hexdigest() != content_manifest_file_sha256:
        raise R5AuthorizedRevalidationError("sealed content manifest hash mismatch")
    manifest = _load_json_bytes(manifest_bytes, manifest_name)
    files = manifest.get("files")
    if manifest.get("schema_version") != 1 or not isinstance(files, Mapping):
        raise R5AuthorizedRevalidationError("sealed content manifest shape is invalid")
    if set(payloads) != set(files) | {manifest_name}:
        raise R5AuthorizedRevalidationError("sealed archive membership mismatch")
    for name, expected in files.items():
        if (
            not isinstance(name, str)
            or not isinstance(expected, str)
            or _SHA256.fullmatch(expected) is None
            or hashlib.sha256(payloads[name]).hexdigest() != expected
        ):
            raise R5AuthorizedRevalidationError("sealed member hash mismatch")
    return payloads


@dataclass(frozen=True, slots=True)
class R5AuthorizedRevalidationBundle:
    plan_sha256: str
    case_id: str
    reference_top: str
    candidate_top: str
    source_sha256: str
    candidate_before_sha256: str
    candidate_after_sha256: str
    paths: Mapping[str, str]
    file_sha256s: Mapping[str, str]
    reference_code: str
    candidate_code: str
    public_test_code: str
    hidden_test_code: str
    public_runtime_contract: Mapping[str, Any]
    authorization: Mapping[str, Any]
    source_episode: Mapping[str, Any]
    archive_sha256: str
    content_manifest_file_sha256: str
    source_episode_sha256: str

    def identity(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "reference_top": self.reference_top,
            "candidate_top": self.candidate_top,
            "source_sha256": self.source_sha256,
            "candidate_before_sha256": self.candidate_before_sha256,
            "candidate_after_sha256": self.candidate_after_sha256,
            "paths": dict(self.paths),
            "file_sha256s": dict(self.file_sha256s),
            "public_runtime_contract": dict(self.public_runtime_contract),
            "authorization_id": self.authorization.get("authorization_id"),
            "source_episode_id": self.source_episode.get("episode_id"),
            "source_episode_sha256": self.source_episode_sha256,
            "archive_sha256": self.archive_sha256,
            "content_manifest_file_sha256": self.content_manifest_file_sha256,
            "plan_sha256": self.plan_sha256,
        }


def verify_authorized_revalidation_plan(
    repository: str | Path,
    plan: Mapping[str, Any],
) -> R5AuthorizedRevalidationBundle:
    root = Path(repository).expanduser().resolve()
    if not root.is_dir():
        raise R5AuthorizedRevalidationError("repository root is missing")
    if (
        plan.get("schema_version") != 1
        or plan.get("status") != "frozen_authorized_candidate_revalidation"
        or plan.get("period") != "history"
        or plan.get("provider_calls_allowed") is not False
        or plan.get("future_files_read_by_protocol") is not False
        or plan.get("future_outcomes_observed") is not False
        or plan.get("trusted_revision_creation_allowed") is not False
        or plan.get("r5_real_campaign_allowed") is not False
        or plan.get("r5_accepted") is not False
        or plan.get("r6_started") is not False
    ):
        raise R5AuthorizedRevalidationError("revalidation plan boundary is invalid")
    case_id = plan.get("case_id")
    reference_top = plan.get("reference_top")
    candidate_top = plan.get("candidate_top")
    if not isinstance(case_id, str) or not case_id:
        raise R5AuthorizedRevalidationError("case identity is missing")
    if (
        not isinstance(reference_top, str)
        or _IDENTIFIER.fullmatch(reference_top) is None
        or not isinstance(candidate_top, str)
        or _IDENTIFIER.fullmatch(candidate_top) is None
        or reference_top == candidate_top
    ):
        raise R5AuthorizedRevalidationError("top identities are invalid")
    raw_paths = plan.get("paths")
    raw_hashes = plan.get("file_sha256s")
    if not isinstance(raw_paths, Mapping) or not isinstance(raw_hashes, Mapping):
        raise R5AuthorizedRevalidationError("repository evidence is missing")
    paths: dict[str, str] = {}
    files: dict[str, Path] = {}
    hashes: dict[str, str] = {}
    for role in ("reference", "public_test", "hidden_test"):
        relative, path = _repo_file(
            root, raw_paths.get(role), raw_hashes.get(role), role
        )
        paths[role] = relative
        files[role] = path
        hashes[role] = str(raw_hashes[role])
    reference_code = files["reference"].read_text(encoding="utf-8")
    public_code = files["public_test"].read_text(encoding="utf-8")
    hidden_code = files["hidden_test"].read_text(encoding="utf-8")
    source_sha = hashlib.sha256(reference_code.encode("utf-8")).hexdigest()
    if source_sha != _require_hash(plan.get("source_sha256"), "source"):
        raise R5AuthorizedRevalidationError("source identity mismatch")

    contract = plan.get("public_runtime_contract")
    if (
        not isinstance(contract, Mapping)
        or set(contract)
        != {
            "schema_version",
            "kind",
            "candidate_mismatch_returncodes",
            "cosim_interface_depths",
        }
        or contract.get("schema_version") != 2
        or contract.get("kind") != _RUNTIME_KIND
        or contract.get("candidate_mismatch_returncodes") != [1]
        or contract.get("cosim_interface_depths") != {"arr": 9}
    ):
        raise R5AuthorizedRevalidationError("Public runtime contract is invalid")

    sealed = plan.get("sealed_candidate_evidence")
    if not isinstance(sealed, Mapping):
        raise R5AuthorizedRevalidationError("sealed Candidate evidence is missing")
    archive = Path(str(sealed.get("archive_path", ""))).expanduser().resolve()
    archive_sha = _require_hash(sealed.get("archive_sha256"), "archive")
    content_sha = _require_hash(
        sealed.get("content_manifest_file_sha256"), "content manifest"
    )
    payloads = _sealed_payloads(
        archive,
        archive_sha256=archive_sha,
        content_manifest_file_sha256=content_sha,
    )
    candidate_member = sealed.get("candidate_member")
    episode_member = sealed.get("source_episode_member")
    if not isinstance(candidate_member, str) or not isinstance(episode_member, str):
        raise R5AuthorizedRevalidationError("sealed member binding is missing")
    try:
        candidate_bytes = payloads[candidate_member]
        episode_bytes = payloads[episode_member]
        candidate_code = candidate_bytes.decode("utf-8")
    except (KeyError, UnicodeDecodeError) as exc:
        raise R5AuthorizedRevalidationError("sealed Candidate member is invalid") from exc
    candidate_after = _require_hash(
        sealed.get("candidate_after_sha256"), "Candidate after"
    )
    if hashlib.sha256(candidate_bytes).hexdigest() != candidate_after:
        raise R5AuthorizedRevalidationError("sealed Candidate hash mismatch")
    episode_sha = _require_hash(sealed.get("source_episode_sha256"), "episode")
    if hashlib.sha256(episode_bytes).hexdigest() != episode_sha:
        raise R5AuthorizedRevalidationError("source episode hash mismatch")
    episode = _load_json_bytes(episode_bytes, episode_member)
    payload = episode.get("payload")
    authorization = payload.get("authorization") if isinstance(payload, Mapping) else None
    candidate_before = _require_hash(
        sealed.get("candidate_before_sha256"), "Candidate before"
    )
    if (
        episode.get("episode_kind") != "r4_repair"
        or episode.get("outcome") != "inconclusive"
        or episode.get("source_sha256") != source_sha
        or not isinstance(payload, Mapping)
        or payload.get("outcome_reason") != "independent_auditor_not_clean"
        or payload.get("candidate_before_sha256") != candidate_before
        or payload.get("candidate_after_sha256") != candidate_after
        or payload.get("provider_call_count") != 1
        or payload.get("budget_actual", {}).get("mutation_calls") != 1
        or not isinstance(authorization, Mapping)
        or authorization.get("authorization_id")
        != sealed.get("authorization_id")
        or authorization.get("before_candidate_sha256") != candidate_before
        or authorization.get("research_authorization", {}).get("arm_id") != "A2"
        or authorization.get("research_authorization", {}).get("mode")
        != "advisor_only"
        or episode.get("agent_safe_summary", {}).get("false_repair") is not False
        or episode.get("agent_safe_summary", {}).get("unsafe_scope") is not False
        or episode.get("agent_safe_summary", {}).get("critical_safety_violation")
        is not False
    ):
        raise R5AuthorizedRevalidationError("source authorization episode is invalid")

    prior = plan.get("prior_configuration_failure")
    if not isinstance(prior, Mapping):
        raise R5AuthorizedRevalidationError("prior configuration failure is missing")
    prior_paths = {
        "result": Path(str(prior.get("acquisition_result_path", ""))).resolve(),
        "audit": Path(str(prior.get("independent_audit_path", ""))).resolve(),
        "reconciliation": (
            root / str(prior.get("reconciliation_path", ""))
        ).resolve(),
    }
    expected_hashes = {
        "result": prior.get("acquisition_result_file_sha256"),
        "audit": prior.get("independent_audit_file_sha256"),
        "reconciliation": prior.get("reconciliation_file_sha256"),
    }
    for role, path in prior_paths.items():
        if path.is_symlink() or not path.is_file():
            raise R5AuthorizedRevalidationError("prior evidence is missing")
        if file_sha256(path) != _require_hash(expected_hashes[role], role):
            raise R5AuthorizedRevalidationError("prior evidence hash mismatch")
    result = _load_json_file(prior_paths["result"], "prior result")
    audit = _load_json_file(prior_paths["audit"], "prior audit")
    reconciliation = _load_json_file(
        prior_paths["reconciliation"], "prior reconciliation"
    )
    if (
        result.get("status") != "inconclusive"
        or result.get("provider_calls") != 2
        or result.get("vitis_launches") != 5
        or audit.get("status")
        != "clean_cosim_interface_depth_configuration_failure"
        or audit.get("outcome", {}).get("candidate_after_sha256") != candidate_after
        or audit.get("critical_finding_count") != 0
        or reconciliation.get("status")
        != "audited_cosim_interface_depth_contract_fixed"
        or reconciliation.get("candidate_after_sha256") != candidate_after
    ):
        raise R5AuthorizedRevalidationError("prior configuration evidence is invalid")

    budget = plan.get("budget")
    if (
        not isinstance(budget, Mapping)
        or budget.get("provider_calls_before") != 104
        or budget.get("vitis_launches_before") != 56
        or budget.get("provider_call_upper_bound") != 0
        or budget.get("vitis_launch_upper_bound") != 3
        or budget.get("provider_hard_cap") != 500
        or budget.get("vitis_hard_cap") != 500
    ):
        raise R5AuthorizedRevalidationError("revalidation budget is invalid")
    return R5AuthorizedRevalidationBundle(
        plan_sha256=canonical_sha256(dict(plan)),
        case_id=case_id,
        reference_top=reference_top,
        candidate_top=candidate_top,
        source_sha256=source_sha,
        candidate_before_sha256=candidate_before,
        candidate_after_sha256=candidate_after,
        paths=paths,
        file_sha256s=hashes,
        reference_code=reference_code,
        candidate_code=candidate_code,
        public_test_code=public_code,
        hidden_test_code=hidden_code,
        public_runtime_contract=dict(contract),
        authorization=dict(authorization),
        source_episode=episode,
        archive_sha256=archive_sha,
        content_manifest_file_sha256=content_sha,
        source_episode_sha256=episode_sha,
    )


__all__ = [
    "R5AuthorizedRevalidationBundle",
    "R5AuthorizedRevalidationError",
    "verify_authorized_revalidation_plan",
]
