#!/usr/bin/env python3
"""Independently audit sealed R5 authorized-Candidate revalidation evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping, Sequence
import zipfile


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PRIVATE_TAGS = (b"<think", b"</think", b"<reasoning", b"</reasoning")


class R5AuthorizedRevalidationResultAuditError(RuntimeError):
    """Raised when sealed revalidation evidence is invalid."""


def _load_bytes(data: bytes | None, label: str) -> dict[str, Any]:
    if data is None:
        raise R5AuthorizedRevalidationResultAuditError(f"missing artifact: {label}")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise R5AuthorizedRevalidationResultAuditError(f"invalid JSON: {label}") from exc
    if not isinstance(value, dict):
        raise R5AuthorizedRevalidationResultAuditError(f"invalid JSON root: {label}")
    return value


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _verify_archive(root: Path) -> tuple[dict[str, bytes], str, str]:
    archive = root / "evidence.zip"
    sidecar = root / "evidence.zip.sha256"
    if archive.is_symlink() or sidecar.is_symlink() or not archive.is_file():
        raise R5AuthorizedRevalidationResultAuditError("evidence archive is missing")
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    if sidecar.read_text(encoding="utf-8") != f"{archive_sha}  evidence.zip\n":
        raise R5AuthorizedRevalidationResultAuditError("archive sidecar mismatch")
    try:
        with zipfile.ZipFile(archive) as sealed:
            infos = sealed.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise R5AuthorizedRevalidationResultAuditError("duplicate archive member")
            for info in infos:
                pure = PurePosixPath(info.filename)
                mode = info.external_attr >> 16
                if (
                    info.is_dir()
                    or pure.is_absolute()
                    or ".." in pure.parts
                    or (mode & 0o170000) == 0o120000
                ):
                    raise R5AuthorizedRevalidationResultAuditError(
                        "unsafe archive member"
                    )
            payloads = {info.filename: sealed.read(info) for info in infos}
    except (OSError, zipfile.BadZipFile) as exc:
        raise R5AuthorizedRevalidationResultAuditError("invalid archive") from exc
    content_bytes = payloads.get("evidence_content_manifest.json")
    content = _load_bytes(content_bytes, "evidence_content_manifest.json")
    files = content.get("files")
    if content.get("schema_version") != 1 or not isinstance(files, Mapping):
        raise R5AuthorizedRevalidationResultAuditError("invalid content manifest")
    if set(payloads) != set(files) | {"evidence_content_manifest.json"}:
        raise R5AuthorizedRevalidationResultAuditError("archive membership mismatch")
    for name, expected in files.items():
        if (
            not isinstance(name, str)
            or not isinstance(expected, str)
            or _SHA256.fullmatch(expected) is None
            or hashlib.sha256(payloads[name]).hexdigest() != expected
        ):
            raise R5AuthorizedRevalidationResultAuditError("archive member hash mismatch")
    for name, data in payloads.items():
        lowered = data.lower()
        if any(tag in lowered for tag in _PRIVATE_TAGS):
            raise R5AuthorizedRevalidationResultAuditError(
                f"private reasoning tag in artifact: {name}"
            )
    return (
        payloads,
        archive_sha,
        hashlib.sha256(content_bytes or b"").hexdigest(),
    )


def _invocation(
    payloads: Mapping[str, bytes],
    name: str,
) -> dict[str, Any]:
    return _load_bytes(payloads.get(name), name)


def _serialized_acceptance(serialized: Mapping[str, Any]) -> Any:
    metadata = serialized.get("metadata")
    return metadata.get("accepted") if isinstance(metadata, Mapping) else None


def audit(root: Path) -> dict[str, Any]:
    root = root.expanduser().resolve()
    payloads, archive_sha, content_sha = _verify_archive(root)
    manifest = _load_bytes(payloads.get("revalidation_manifest.json"), "manifest")
    result = _load_bytes(payloads.get("revalidation_result.json"), "result")
    unsigned_result = {
        key: value for key, value in result.items() if key != "result_sha256"
    }
    identity = manifest.get("case_identity")
    candidate_after = (
        identity.get("candidate_after_sha256")
        if isinstance(identity, Mapping)
        else None
    )
    validation = result.get("validation", {})
    serialized = validation.get("result", {}) if isinstance(validation, Mapping) else {}
    steps = serialized.get("steps") if isinstance(serialized, Mapping) else None
    expected_states = [
        "preflight",
        "public_evaluation",
        "csynth",
        "public_cosim",
        "hidden_evaluation",
    ]
    if (
        manifest.get("schema_version") != 1
        or result.get("schema_version") != 1
        or result.get("status") != "ready_for_independent_audit"
        or result.get("manifest_sha256") != manifest.get("manifest_sha256")
        or manifest.get("manifest_sha256")
        != _canonical_sha256(
            {key: value for key, value in manifest.items() if key != "manifest_sha256"}
        )
        or result.get("result_sha256") != _canonical_sha256(unsigned_result)
        or not isinstance(candidate_after, str)
        or _SHA256.fullmatch(candidate_after) is None
        or result.get("candidate_after_sha256") != candidate_after
        or result.get("authorization_id") != identity.get("authorization_id")
        or result.get("source_episode_id") != identity.get("source_episode_id")
        or result.get("source_episode_sha256") != identity.get("source_episode_sha256")
        or result.get("provider_calls") != 0
        or result.get("vitis_launches") > 3
        or result.get("fresh_validation") is not True
        or result.get("original_episode_mutated") is not False
        or result.get("raw_provider_response_persisted") is not False
        or result.get("private_reasoning_persisted") is not False
        or result.get("future_files_read") is not False
        or result.get("future_outcomes_observed") is not False
        or result.get("self_declared_verified_positive") is not False
        or result.get("trusted_revision_created") is not False
        or result.get("r5_accepted") is not False
        or result.get("r6_started") is not False
        or not isinstance(steps, list)
    ):
        raise R5AuthorizedRevalidationResultAuditError(
            "revalidation result boundary is invalid"
        )
    accepted = result.get("validation_accepted") is True
    serialized_accepted = _serialized_acceptance(serialized)
    states = [step.get("state") for step in steps if isinstance(step, Mapping)]
    if accepted:
        if (
            serialized_accepted is not True
            or serialized.get("final_state") != "accepted"
            or states != expected_states
            or result.get("vitis_launches") != 3
        ):
            raise R5AuthorizedRevalidationResultAuditError(
                "accepted revalidation lacks the complete fresh prefix"
            )
    elif serialized_accepted is not False:
        raise R5AuthorizedRevalidationResultAuditError(
            "non-accepted revalidation is inconsistent"
        )

    validation_id = serialized.get("validation_id")
    if not isinstance(validation_id, str) or not validation_id:
        raise R5AuthorizedRevalidationResultAuditError("validation identity is missing")
    base = PurePosixPath("work") / validation_id / "attempt_000"
    public_csim = _invocation(
        payloads, (base / "csim/public/suite_001/csim_invocation.json").as_posix()
    )
    csynth = _invocation(
        payloads, (base / "csynth/csynth_invocation.json").as_posix()
    )
    if (
        public_csim.get("typed_outcome", {}).get("candidate_sha256")
        != candidate_after
        or csynth.get("candidate_sha256") not in {None, candidate_after}
    ):
        raise R5AuthorizedRevalidationResultAuditError(
            "fresh invocation Candidate identity mismatch"
        )
    if accepted:
        public_cosim = _invocation(
            payloads,
            (base / "public_cosim/suite_001/cosim_invocation.json").as_posix(),
        )
        hidden_csim = _invocation(
            payloads, (base / "csim/hidden/suite_001/csim_invocation.json").as_posix()
        )
        if (
            public_csim.get("runtime_classification", {}).get("status") != "passed"
            or public_csim.get("typed_outcome", {}).get("status") != "passed"
            or csynth.get("execution", {}).get("status") != "completed"
            or csynth.get("execution", {}).get("returncode") != 0
            or public_cosim.get("runtime_contract", {}).get("schema_version") != 2
            or public_cosim.get("cosim_interface_depths") != {"arr": 9}
            or public_cosim.get("execution", {}).get("status") != "completed"
            or public_cosim.get("execution", {}).get("returncode") != 0
            or public_cosim.get("execution", {}).get("cosim_launched") is not True
            or public_cosim.get("typed_outcome", {}).get("status") != "passed"
            or hidden_csim.get("typed_outcome", {}).get("status") != "passed"
            or hidden_csim.get("typed_outcome", {}).get("candidate_sha256")
            != candidate_after
        ):
            raise R5AuthorizedRevalidationResultAuditError(
                "accepted revalidation physical evidence is incomplete"
            )
        status = "clean_verified_positive_revalidation"
        reason = "fresh_full_prefix_and_independent_audit_passed"
    else:
        status = "clean_non_promoting_revalidation"
        reason = "fresh_validation_not_accepted"
    value: dict[str, Any] = {
        "schema_version": 1,
        "auditor": "r5-authorized-candidate-revalidation-file-auditor-v1",
        "status": status,
        "reason": reason,
        "evidence_archive_sha256": archive_sha,
        "content_manifest_file_sha256": content_sha,
        "repository_head": manifest.get("repository_head"),
        "run_id": result.get("run_id"),
        "source_sha256": identity.get("source_sha256"),
        "candidate_before_sha256": result.get("candidate_before_sha256"),
        "candidate_after_sha256": candidate_after,
        "authorization_id": result.get("authorization_id"),
        "source_episode_id": result.get("source_episode_id"),
        "source_episode_sha256": result.get("source_episode_sha256"),
        "fresh_full_validation": accepted,
        "semantic_preservation": accepted,
        "complete_identity": True,
        "original_episode_mutated": False,
        "provider_calls": 0,
        "vitis_launches": result.get("vitis_launches"),
        "auditor_provider_calls": 0,
        "auditor_vitis_launches": 0,
        "future_files_read": False,
        "future_outcomes_observed": False,
        "critical_finding_count": 0,
        "blocking_finding_count": 0 if accepted else 1,
        "findings": (
            []
            if accepted
            else [
                {
                    "code": "fresh_validation_not_accepted",
                    "severity": "blocking",
                    "message": "The sealed Candidate did not pass the full fresh validation prefix.",
                }
            ]
        ),
        "eligible_for_lifecycle_reduction": accepted,
        "trusted_revision_created": False,
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
    temporary = args.output.with_name("." + args.output.name + ".tmp")
    temporary.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, args.output)
    print("R5_AUTHORIZED_REVALIDATION_RESULT_AUDIT_STATUS=" + result["status"])
    print("AUDITOR_PROVIDER_CALLS=0")
    print("AUDITOR_VITIS_LAUNCHES=0")
    print("CRITICAL_FINDINGS=0")
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
