#!/usr/bin/env python3
"""Audit the derived R5 episode, Trusted reduction, snapshot, and payload."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agrefactor.recovery import (  # noqa: E402
    AppendOnlyEpisodeLedger,
    Lifecycle,
    R5MemoryPayload,
    canonical_sha256,
)


class AdmissionAuditError(RuntimeError):
    pass


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdmissionAuditError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise AdmissionAuditError(f"JSON root is not an object: {path}")
    return value


def sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise AdmissionAuditError(f"missing file: {path}") from exc


def unsigned(value: Mapping[str, Any], field: str) -> str:
    return canonical_sha256({key: item for key, item in value.items() if key != field})


def audit(root: Path, revalidation_audit: Path, output: Path) -> dict[str, Any]:
    root = root.resolve()
    source_audit = load(revalidation_audit.resolve())
    result = load(root / "admission_result.json")
    manifest = load(root / "admission_manifest.json")
    inventory = load(root / "evidence_inventory.json")
    revision = load(root / "lifecycle_reduction.json")
    summary = load(root / "lifecycle_reduction_summary.json")
    snapshot = load(root / "memory_snapshot.json")
    payload_value = load(root / "memory_payload.json")
    ledger = AppendOnlyEpisodeLedger(root / "ledger")
    records = ledger.records()
    if (
        result.get("status") != "ready_for_independent_admission_audit"
        or result.get("result_sha256") != unsigned(result, "result_sha256")
        or manifest.get("status") != "ready_for_independent_admission_audit"
        or manifest.get("manifest_sha256") != unsigned(manifest, "manifest_sha256")
        or inventory.get("inventory_sha256") != unsigned(inventory, "inventory_sha256")
        or revision.get("lifecycle") != Lifecycle.TRUSTED.value
        or summary.get("lifecycle") != Lifecycle.TRUSTED.value
        or summary.get("revision_sha256") != revision.get("revision_sha256")
        or snapshot.get("selected_revision_hashes") != [revision.get("revision_sha256")]
        or payload_value.get("revision_sha256") != revision.get("revision_sha256")
        or payload_value.get("snapshot_sha256") != snapshot.get("snapshot_sha256")
        or payload_value.get("payload_sha256") != unsigned(payload_value, "payload_sha256")
        or len(records) != 3
        or any(item.outcome.value != "verified_positive" for item in records)
        or result.get("lifecycle") != "Trusted"
        or result.get("positive_count") != 3
        or result.get("independent_sources") != 2
        or result.get("independent_contexts") != 3
        or result.get("provider_calls") != 0
        or result.get("vitis_launches") != 0
        or result.get("future_files_read") is not False
        or result.get("future_outcomes_observed") is not False
        or result.get("trusted_revision_created") is not False
        or result.get("r5_accepted") is not False
        or result.get("r6_started") is not False
    ):
        raise AdmissionAuditError("admission result or reduction is inconsistent")
    if (
        source_audit.get("status") != "clean_verified_positive_revalidation"
        or source_audit.get("audit_sha256") != manifest.get("supplemental_audit_sha256")
        or source_audit.get("candidate_after_sha256") != manifest.get("candidate_after_sha256")
        or source_audit.get("eligible_for_lifecycle_reduction") is not True
        or source_audit.get("critical_finding_count") != 0
        or source_audit.get("blocking_finding_count") != 0
        or source_audit.get("provider_calls") != 0
        or source_audit.get("vitis_launches") != 3
        or source_audit.get("original_episode_mutated") is not False
        or source_audit.get("future_files_read") is not False
        or source_audit.get("future_outcomes_observed") is not False
    ):
        raise AdmissionAuditError("supplemental certificate is invalid")
    if (
        inventory.get("episode_ids") != [item.episode_id for item in records]
        or inventory.get("episode_hashes") != [item.envelope_sha256 for item in records]
        or len(inventory.get("source_sha256s", [])) != 2
        or len(inventory.get("context_signatures", [])) != 3
        or inventory.get("future_files_read") is not False
        or inventory.get("future_outcomes_observed") is not False
        or snapshot.get("history_episode_ids") != [item.episode_id for item in records]
        or set(revision.get("positive_episode_refs", []))
        != {item.episode_id for item in records}
        or set(payload_value.get("source_episode_hashes", []))
        != {item.envelope_sha256 for item in records}
        or payload_value.get("candidate_only_scope") is not True
    ):
        raise AdmissionAuditError("history, snapshot, or payload binding is invalid")
    payload = R5MemoryPayload(**payload_value)
    if (
        payload.revision_sha256 != revision.get("revision_sha256")
        or payload.snapshot_sha256 != snapshot.get("snapshot_sha256")
    ):
        raise AdmissionAuditError("typed payload binding is invalid")
    supplemental = next(
        item for item in records
        if item.episode_id == manifest.get("supplemental_episode_id")
    )
    if (
        supplemental.payload.get("candidate_after_sha256")
        != manifest.get("candidate_after_sha256")
        or supplemental.payload.get("independent_audit_sha256")
        != manifest.get("supplemental_audit_sha256")
        or supplemental.lineage
        != (supplemental.payload.get("source_episode_id"),)
    ):
        raise AdmissionAuditError("supplemental episode binding is invalid")
    value = {
        "schema_version": 1,
        "auditor": "r5-authorized-revalidation-admission-file-auditor-v1",
        "status": "clean_verified_positive_lifecycle_admission",
        "reason": "supplemental_positive_bound_to_trusted_reduction_snapshot_and_payload",
        "admission_manifest_sha256": sha(root / "admission_manifest.json"),
        "admission_result_sha256": sha(root / "admission_result.json"),
        "ledger_manifest_sha256": sha(root / "ledger" / "ledger_manifest.json"),
        "supplemental_episode_id": supplemental.episode_id,
        "supplemental_episode_sha256": supplemental.envelope_sha256,
        "revision_sha256": revision["revision_sha256"],
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "payload_sha256": payload_value["payload_sha256"],
        "lifecycle": "Trusted",
        "positive_count": 3,
        "independent_sources": 2,
        "independent_contexts": 3,
        "provider_calls": 0,
        "vitis_launches": 0,
        "auditor_provider_calls": 0,
        "auditor_vitis_launches": 0,
        "future_files_read": False,
        "future_outcomes_observed": False,
        "critical_finding_count": 0,
        "blocking_finding_count": 0,
        "trusted_revision_created": False,
        "r5_accepted": False,
        "r6_started": False,
    }
    value["audit_sha256"] = canonical_sha256(value)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence_root", type=Path)
    parser.add_argument("--revalidation-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    value = audit(args.evidence_root, args.revalidation_audit, args.output)
    print("R5_AUTHORIZED_REVALIDATION_ADMISSION_AUDIT_STATUS=" + value["status"])
    print("R5_LIFECYCLE=Trusted")
    print("CRITICAL_FINDINGS=0")
    print("BLOCKING_FINDINGS=0")
    print("AUDITOR_PROVIDER_CALLS=0")
    print("AUDITOR_VITIS_LAUNCHES=0")
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
