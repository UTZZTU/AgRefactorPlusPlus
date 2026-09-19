#!/usr/bin/env python3
"""Admit one audited R5 revalidation into append-only lifecycle history."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agrefactor.recovery import (  # noqa: E402
    AppendOnlyEpisodeLedger,
    Lifecycle,
    R5EpisodeEnvelope,
    R5EpisodeOutcome,
    R5LifecycleReducer,
    R5MemoryPayload,
    R5SnapshotBuilder,
    R5_MEMORY_PAYLOAD_POLICY_SHA256,
    canonical_sha256,
)
from agrefactor.recovery.r5_authorized_revalidation import (  # noqa: E402
    R5AuthorizedRevalidationError,
    verify_authorized_revalidation_plan,
)


class AdmissionError(RuntimeError):
    pass


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdmissionError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise AdmissionError(f"JSON root is not an object: {path}")
    return value


def sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise AdmissionError(f"missing file: {path}") from exc


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=False,
        capture_output=True, text=True,
    )
    if result.returncode:
        raise AdmissionError("git command failed")
    return result.stdout.strip()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def verify_positive(
    repo: Path, plan_path: Path, state_path: Path, evidence_root: Path,
    audit_path: Path,
) -> tuple[Any, dict[str, Any], dict[str, Any], str, str]:
    plan = load(plan_path)
    state = load(state_path)
    try:
        bundle = verify_authorized_revalidation_plan(repo, plan)
    except (R5AuthorizedRevalidationError, ValueError, OSError) as exc:
        raise AdmissionError("frozen revalidation plan is invalid") from exc
    if (
        git(repo, "branch", "--show-current") != "research-roadmap-v2.3"
        or git(repo, "status", "--porcelain", "--untracked-files=all")
        or state.get("R4_ACCEPTED") is not True
        or state.get("R5_STARTED") is not True
        or state.get("R5_ACCEPTED") is not False
        or state.get("R6_STARTED") is not False
        or state.get("R5_REAL_CAMPAIGN_ALLOWED") is not False
        or state.get("R5_CONSUMED_PROVIDER_CALLS") != 104
        or state.get("R5_CONSUMED_VITIS_LAUNCHES") != 59
    ):
        raise AdmissionError("roadmap state does not permit admission")
    result_path = evidence_root / "revalidation_result.json"
    archive_path = evidence_root / "evidence.zip"
    result = load(result_path)
    audit = load(audit_path)
    archive_sha = sha(archive_path)
    audit_sha = audit.get("audit_sha256")
    if (
        not isinstance(audit_sha, str)
        or len(audit_sha) != 64
        or result.get("status") != "ready_for_independent_audit"
        or result.get("result_sha256") != canonical_sha256(
            {key: value for key, value in result.items() if key != "result_sha256"}
        )
        or result.get("validation_accepted") is not True
        or result.get("candidate_after_sha256") != bundle.candidate_after_sha256
        or result.get("source_episode_id") != bundle.source_episode["episode_id"]
        or result.get("source_episode_sha256") != bundle.source_episode_sha256
        or result.get("provider_calls") != 0
        or result.get("vitis_launches") != 3
        or result.get("original_episode_mutated") is not False
        or result.get("trusted_revision_created") is not False
        or result.get("r5_accepted") is not False
        or result.get("r6_started") is not False
        or audit.get("status") != "clean_verified_positive_revalidation"
        or audit.get("evidence_archive_sha256") != archive_sha
        or audit.get("candidate_after_sha256") != bundle.candidate_after_sha256
        or audit.get("source_episode_id") != bundle.source_episode["episode_id"]
        or audit.get("source_episode_sha256") != bundle.source_episode_sha256
        or audit.get("provider_calls") != 0
        or audit.get("vitis_launches") != 3
        or audit.get("critical_finding_count") != 0
        or audit.get("blocking_finding_count") != 0
        or audit.get("eligible_for_lifecycle_reduction") is not True
        or audit.get("original_episode_mutated") is not False
        or audit.get("future_files_read") is not False
        or audit.get("future_outcomes_observed") is not False
        or audit.get("trusted_revision_created") is not False
        or audit.get("r5_accepted") is not False
        or audit.get("r6_started") is not False
    ):
        raise AdmissionError("positive revalidation certificate is invalid")
    return bundle, state, result, audit_sha, archive_sha


def supplemental_episode(
    bundle: Any, result: Mapping[str, Any], audit_sha: str, archive_sha: str,
    calibration_id: str,
) -> R5EpisodeEnvelope:
    validation = result.get("validation", {}).get("result", {})
    validation_id = validation.get("validation_id")
    completed_at = result.get("completed_at")
    if not isinstance(validation_id, str) or not isinstance(completed_at, str):
        raise AdmissionError("revalidation identity is incomplete")
    context = canonical_sha256({
        "case_id": bundle.case_id,
        "candidate_after_sha256": bundle.candidate_after_sha256,
        "evidence_archive_sha256": archive_sha,
        "validation_id": validation_id,
    })
    execution_identity = canonical_sha256({
        "authorization_id": bundle.authorization["authorization_id"],
        "candidate_after_sha256": bundle.candidate_after_sha256,
        "source_sha256": bundle.source_sha256,
        "validation_id": validation_id,
        "runtime_contract": dict(bundle.public_runtime_contract),
    })
    payload = {
        "revalidation_kind": "authorized_candidate_revalidation",
        "source_episode_id": bundle.source_episode["episode_id"],
        "source_episode_sha256": bundle.source_episode_sha256,
        "candidate_before_sha256": bundle.candidate_before_sha256,
        "candidate_after_sha256": bundle.candidate_after_sha256,
        "authorization_id": bundle.authorization["authorization_id"],
        "validation_id": validation_id,
        "evidence_archive_sha256": archive_sha,
        "independent_audit_sha256": audit_sha,
        "failure_family": "unsupported_construct",
        "stage": "csynth",
        "owner": "candidate",
        "outcome_reason": "fresh_full_prefix_and_independent_audit_passed",
        "provider_call_count": 0,
        "vitis_phase_count": 3,
        "calibration_certificate_id": calibration_id,
        "runtime_contract_schema_version": 2,
        "cosim_interface_depth_arr": 9,
    }
    summary = {
        "failure_family": "unsupported_construct",
        "stage": "csynth",
        "owner": "candidate",
        "false_repair": False,
        "unsafe_scope": False,
        "critical_safety_violation": False,
        "independent_review_sha256": audit_sha,
        "supplemental_verification": True,
    }
    return R5EpisodeEnvelope(
        episode_kind="r4_repair",
        episode_id="r5-authorized-revalidation-" + audit_sha[:32],
        payload_schema_version="r5-authorized-revalidation-v1",
        payload=payload,
        execution_identity_sha256=execution_identity,
        source_sha256=bundle.source_sha256,
        context_signature=context,
        created_at=completed_at,
        observed_at=completed_at,
        lineage=(bundle.source_episode["episode_id"],),
        agent_safe_summary=summary,
        outcome=R5EpisodeOutcome.VERIFIED_POSITIVE,
        manifest_sha256=canonical_sha256({
            "plan_sha256": bundle.plan_sha256,
            "archive_sha256": archive_sha,
            "audit_sha256": audit_sha,
        }),
    )


def admit(
    repo: Path, plan: Path, state_path: Path, evidence: Path, audit_path: Path,
    predecessor: Path, output: Path,
) -> dict[str, Any]:
    if output.exists():
        raise AdmissionError("output directory already exists")
    bundle, state, result, audit_sha, archive_sha = verify_positive(
        repo, plan, state_path, evidence, audit_path
    )
    predecessor_manifest = predecessor / "ledger_manifest.json"
    if sha(predecessor_manifest) != state[
        "R5_PREDECESSOR_IMPORT_LEDGER_MANIFEST_FILE_SHA256"
    ]:
        raise AdmissionError("predecessor manifest identity mismatch")
    old = AppendOnlyEpisodeLedger(predecessor)
    predecessors = old.records()
    if (
        len(predecessors) != 2
        or any(item.outcome is not R5EpisodeOutcome.VERIFIED_POSITIVE for item in predecessors)
        or len({item.source_sha256 for item in predecessors}) != 1
    ):
        raise AdmissionError("predecessor partition is not the frozen positive seed")
    extra = supplemental_episode(
        bundle, result, audit_sha, archive_sha,
        state["R2_TO_R4_CALIBRATION_CERTIFICATE_ID"],
    )
    output.mkdir(parents=True)
    combined = AppendOnlyEpisodeLedger(output / "ledger")
    for item in (*predecessors, extra):
        combined.append(item)
    episodes = combined.records()
    inventory = {
        "schema_version": 1,
        "predecessor_ledger_manifest_sha256": sha(predecessor_manifest),
        "supplemental_evidence_archive_sha256": archive_sha,
        "supplemental_audit_sha256": audit_sha,
        "episode_ids": [item.episode_id for item in episodes],
        "episode_hashes": [item.envelope_sha256 for item in episodes],
        "source_sha256s": sorted({item.source_sha256 for item in episodes}),
        "context_signatures": sorted({item.context_signature for item in episodes}),
        "future_holdout_case_ids": [
            "Exception_E3_Turbo_Encoder", "Pointer_E3_double_pointer"
        ],
        "future_files_read": False,
        "future_outcomes_observed": False,
    }
    inventory["inventory_sha256"] = canonical_sha256(inventory)
    write_json(output / "evidence_inventory.json", inventory)
    reducer = R5LifecycleReducer()
    reduction = reducer.reduce(
        episodes,
        revision_id="r5-unsupported-construct-trusted-r1",
        failure_family="unsupported_construct",
        stage="csynth",
        owner="candidate",
        supported_when={
            "failure_family": "unsupported_construct", "stage": "csynth",
            "owner": "candidate", "calibration": "accepted", "confidence": "high",
        },
        avoid_when={
            "conflict": True, "sparse": True, "ood": True,
            "confidence": "medium_or_lower",
        },
        exact_exclusions={"candidate_only": True, "requires_full_validation": True},
        required_evidence=(
            "agent_safe_diagnostic", "accepted_calibration",
            "fresh_full_validation", "independent_audit",
        ),
        calibration_refs=(state["R2_TO_R4_CALIBRATION_CERTIFICATE_ID"],),
        memory_payload_manifest_sha256=R5_MEMORY_PAYLOAD_POLICY_SHA256,
        created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    if (
        reduction.revision.lifecycle is not Lifecycle.TRUSTED
        or reduction.positive_count != 3
        or reduction.negative_count != 0
        or reduction.independent_sources != 2
        or reduction.independent_contexts != 3
    ):
        raise AdmissionError("combined evidence did not meet Trusted thresholds")
    snapshot = R5SnapshotBuilder().build(
        episodes, [reduction],
        latest_allowed_timestamp=result["completed_at"],
        frozen_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        evidence_inventory_sha256=inventory["inventory_sha256"],
        exact_exclusions=reduction.revision.exact_exclusions,
        conflict_sparsity_ood_facts={
            "conflict": "abstain", "sparse": "abstain", "ood": "abstain",
        },
    )
    payload = R5MemoryPayload.from_revision(
        reduction.revision,
        snapshot_sha256=snapshot.snapshot_sha256,
        repair_intent_or_recipe=(
            "Apply a bounded candidate-only rewrite for a calibrated unsupported "
            "construct; abstain on conflict, sparse evidence, OOD context, or "
            "non-high calibrated confidence."
        ),
        source_episode_hashes=tuple(item.envelope_sha256 for item in episodes),
        evidence_refs=(
            "calibration:" + state["R2_TO_R4_CALIBRATION_CERTIFICATE_ID"],
            "revalidation-audit:" + audit_sha,
            "revalidation-archive:" + archive_sha,
        ),
    )
    write_json(output / "lifecycle_reduction.json", reduction.revision.to_dict())
    write_json(output / "lifecycle_reduction_summary.json", {
        "schema_version": 1, "revision_sha256": reduction.revision.revision_sha256,
        "lifecycle": reduction.revision.lifecycle.value,
        "positive_count": reduction.positive_count,
        "negative_count": reduction.negative_count,
        "independent_sources": reduction.independent_sources,
        "independent_contexts": reduction.independent_contexts,
        "eligible_episode_ids": list(reduction.eligible_episode_ids),
        "rejected_episode_ids": list(reduction.rejected_episode_ids),
    })
    write_json(output / "memory_snapshot.json", snapshot.to_dict())
    write_json(output / "memory_payload.json", payload.to_dict())
    manifest = {
        "schema_version": 1, "status": "ready_for_independent_admission_audit",
        "repository_head": git(repo, "rev-parse", "HEAD"),
        "candidate_after_sha256": bundle.candidate_after_sha256,
        "supplemental_episode_id": extra.episode_id,
        "supplemental_episode_sha256": extra.envelope_sha256,
        "supplemental_audit_sha256": audit_sha,
        "inventory_sha256": inventory["inventory_sha256"],
        "revision_sha256": reduction.revision.revision_sha256,
        "snapshot_sha256": snapshot.snapshot_sha256,
        "payload_sha256": payload.payload_sha256,
        "provider_calls": 0, "vitis_launches": 0,
        "future_files_read": False, "future_outcomes_observed": False,
        "trusted_revision_created": False, "r5_accepted": False, "r6_started": False,
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    write_json(output / "admission_manifest.json", manifest)
    result_value = {
        "schema_version": 1, "status": "ready_for_independent_admission_audit",
        "repository_head": manifest["repository_head"],
        "case_id": bundle.case_id,
        "candidate_after_sha256": bundle.candidate_after_sha256,
        "supplemental_episode_id": extra.episode_id,
        "supplemental_episode_sha256": extra.envelope_sha256,
        "lifecycle": "Trusted",
        "revision_sha256": reduction.revision.revision_sha256,
        "snapshot_sha256": snapshot.snapshot_sha256,
        "payload_sha256": payload.payload_sha256,
        "positive_count": reduction.positive_count,
        "independent_sources": reduction.independent_sources,
        "independent_contexts": reduction.independent_contexts,
        "provider_calls": 0, "vitis_launches": 0,
        "future_files_read": False, "future_outcomes_observed": False,
        "trusted_revision_created": False, "r5_accepted": False, "r6_started": False,
    }
    result_value["result_sha256"] = canonical_sha256(result_value)
    write_json(output / "admission_result.json", result_value)
    return result_value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--predecessor-ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    value = admit(
        args.repo.resolve(), args.plan.resolve(), args.state.resolve(),
        args.evidence_root.resolve(), args.audit.resolve(),
        args.predecessor_ledger.resolve(), args.output.resolve(),
    )
    for key in ("status", "lifecycle", "positive_count", "independent_sources", "independent_contexts"):
        print(f"R5_ADMISSION_{key.upper()}={value.get(key)}")
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
