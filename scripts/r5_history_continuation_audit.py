#!/usr/bin/env python3
"""Authorize only unobserved frozen R5 history after a data-insufficient case."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

from agrefactor.campaign.r5_protocol import (
    COMMON_BASELINE_PROVIDER_CAP,
    COMMON_BASELINE_VITIS_CAP,
    MUTATION_ARM_VITIS_CAP,
    estimate_upper_bound,
)
from agrefactor.recovery.r5_budget import R5BudgetLedger


DEFAULT_MANIFEST = Path(
    "/data/agrefactor_runs/r5_p2_adapter_freeze_v2/oracle_adapter_manifest.json"
)
DEFAULT_PRIOR_AUDIT = Path(
    "/data/agrefactor_runs/"
    "r5_p2_oracle_protocol_audit_v4_vitis_corrected_7784252/"
    "protocol_audit.json"
)
DEFAULT_RECONCILIATION = Path(
    "docs/roadmap/R5_HISTORY_BASELINE_OUTCOME_RECONCILIATION.json"
)
PER_ATTEMPT_PROVIDER_CAP = COMMON_BASELINE_PROVIDER_CAP + 2
PER_ATTEMPT_VITIS_CAP = COMMON_BASELINE_VITIS_CAP + MUTATION_ARM_VITIS_CAP


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _verify_signed(value: Mapping[str, Any], field: str) -> str:
    stored = value.get(field)
    if not isinstance(stored, str) or len(stored) != 64:
        raise ValueError(f"{field} is missing or malformed")
    if _sha({key: item for key, item in value.items() if key != field}) != stored:
        raise ValueError(f"{field} mismatch")
    return stored


def build_continuation_audit(
    *,
    state: Mapping[str, Any],
    adapter_manifest: Mapping[str, Any],
    prior_audit: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
    adapter_manifest_file_sha256: str,
    prior_audit_file_sha256: str,
    reconciliation_file_sha256: str,
    repository_head: str,
) -> dict[str, Any]:
    _verify_signed(adapter_manifest, "manifest_sha256")
    _verify_signed(prior_audit, "protocol_audit_sha256")
    if prior_audit.get("status") != "ready_for_history_acquisition":
        raise ValueError("prior protocol audit did not authorize history")
    if prior_audit.get("adapter_manifest_file_sha256") != adapter_manifest_file_sha256:
        raise ValueError("adapter manifest file hash differs from prior audit")
    if prior_audit.get("adapter_manifest_sha256") != adapter_manifest.get(
        "manifest_sha256"
    ):
        raise ValueError("adapter manifest identity differs from prior audit")
    if state.get("R5_DATASET_V4_PROTOCOL_AUDIT_FILE_SHA256") != prior_audit_file_sha256:
        raise ValueError("prior protocol audit file hash differs from roadmap state")
    if (
        state.get("R5_HISTORY_BASELINE_OUTCOME_RECONCILIATION_SHA256")
        != reconciliation_file_sha256
    ):
        raise ValueError("history reconciliation hash differs from roadmap state")
    expected_state = {
        "R4_ACCEPTED": True,
        "R5_STARTED": True,
        "R5_ACCEPTED": False,
        "R6_STARTED": False,
        "R5_PROVIDER_CALL_HARD_CAP": 500,
        "R5_VITIS_LAUNCH_HARD_CAP": 500,
        "R5_REAL_CAMPAIGN_ALLOWED": False,
        "R5_CONSUMED_PROVIDER_CALLS": 66,
        "R5_CONSUMED_VITIS_LAUNCHES": 24,
    }
    if any(state.get(key) != expected for key, expected in expected_state.items()):
        raise ValueError("authoritative R5 state is incompatible with continuation")
    if reconciliation.get("status") != "reconciled_data_insufficient_common_baseline_accepted":
        raise ValueError("history reconciliation does not record data insufficiency")
    history_boundary = reconciliation.get("history_manifest")
    data_decision = reconciliation.get("data_decision")
    observed = reconciliation.get("observed_case")
    if not all(isinstance(item, Mapping) for item in (history_boundary, data_decision, observed)):
        raise ValueError("history reconciliation is incomplete")
    if (
        history_boundary.get("future_outcomes_observed") is not False
        or history_boundary.get("future_files_executed") is not False
        or data_decision.get("verified_positive_episode_created") is not False
        or data_decision.get("trusted_revision_creation_allowed") is not False
        or data_decision.get("second_history_case_outcome_observed") is not False
    ):
        raise ValueError("history reconciliation crosses the continuation boundary")
    history_ids = adapter_manifest.get("history_case_ids")
    future_ids = adapter_manifest.get("future_case_ids")
    if (
        not isinstance(history_ids, list)
        or len(history_ids) != 2
        or len(set(history_ids)) != 2
        or not isinstance(future_ids, list)
        or len(future_ids) < 2
    ):
        raise ValueError("frozen history/future split is incomplete")
    observed_id = observed.get("case_id")
    if observed_id not in history_ids:
        raise ValueError("observed case is outside the frozen history split")
    authorized = [case_id for case_id in history_ids if case_id != observed_id]
    if len(authorized) != 1:
        raise ValueError("continuation must authorize exactly one unobserved case")

    attempts = 3
    continuation_provider = len(authorized) * attempts * PER_ATTEMPT_PROVIDER_CAP
    continuation_vitis = len(authorized) * attempts * PER_ATTEMPT_VITIS_CAP
    pilot_provider, pilot_vitis = estimate_upper_bound(case_count=1)
    formal_provider, formal_vitis = estimate_upper_bound(case_count=len(future_ids))
    ledger = R5BudgetLedger(
        provider_cap=500,
        vitis_cap=500,
        provider_used=int(state["R5_CONSUMED_PROVIDER_CALLS"]),
        vitis_used=int(state["R5_CONSUMED_VITIS_LAUNCHES"]),
    )
    reservation = ledger.reserve_with_recovery(
        provider_upper_bound=(
            continuation_provider + pilot_provider + formal_provider
        ),
        vitis_upper_bound=continuation_vitis + pilot_vitis + formal_vitis,
    )
    result = {
        "schema_version": 1,
        "audit_id": "v2.3-r5-history-continuation-audit-v1",
        "status": "ready_for_history_continuation",
        "repository_head": repository_head,
        "adapter_manifest_file_sha256": adapter_manifest_file_sha256,
        "adapter_manifest_sha256": adapter_manifest["manifest_sha256"],
        "prior_protocol_audit_file_sha256": prior_audit_file_sha256,
        "prior_protocol_audit_sha256": prior_audit["protocol_audit_sha256"],
        "history_reconciliation_file_sha256": reconciliation_file_sha256,
        "observed_data_insufficient_case_ids": [observed_id],
        "authorized_history_case_ids": authorized,
        "future_case_ids": list(future_ids),
        "max_attempts_per_case": attempts,
        "continuation_provider_upper_bound": continuation_provider,
        "continuation_vitis_upper_bound": continuation_vitis,
        "future_pilot_provider_upper_bound": pilot_provider,
        "future_pilot_vitis_upper_bound": pilot_vitis,
        "future_formal_provider_upper_bound": formal_provider,
        "future_formal_vitis_upper_bound": formal_vitis,
        "provider_recovery_reserve": reservation.provider_recovery_reserve,
        "vitis_recovery_reserve": reservation.vitis_recovery_reserve,
        "global_budget_before": ledger.to_dict(),
        "checks": {
            "first_case_not_rewritten_as_r4_episode": True,
            "only_unobserved_history_case_authorized": True,
            "future_files_executed": False,
            "future_outcomes_observed": False,
            "manual_trusted_revision_created": False,
            "existing_refactor_r2_r4_path_required": True,
            "global_budget_and_recovery_reserve_fit": True,
        },
        "provider_calls": 0,
        "vitis_launches": 0,
        "git_history_mutations": 0,
        "r5_accepted": False,
        "r6_started": False,
        "next_step": "continue_unobserved_frozen_history_case_through_existing_refactor_r2_r4",
    }
    result["continuation_audit_sha256"] = _sha(result)
    return result


def _write_output(output_dir: Path, value: Mapping[str, Any]) -> Path:
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
    )
    try:
        path = temporary / "history_continuation_audit.json"
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return output_dir / "history_continuation_audit.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--prior-audit", type=Path, default=DEFAULT_PRIOR_AUDIT)
    parser.add_argument("--state", type=Path, default=Path("docs/roadmap/V2_3_STATE.json"))
    parser.add_argument("--reconciliation", type=Path, default=DEFAULT_RECONCILIATION)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    completed = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
        check=True,
        capture_output=True,
        text=True,
    )
    if completed.stdout.strip():
        raise ValueError("continuation audit requires a clean repository")
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    state_path = (repo / args.state).resolve() if not args.state.is_absolute() else args.state.resolve()
    reconciliation_path = (
        (repo / args.reconciliation).resolve()
        if not args.reconciliation.is_absolute()
        else args.reconciliation.resolve()
    )
    value = build_continuation_audit(
        state=_load(state_path),
        adapter_manifest=_load(args.manifest.resolve()),
        prior_audit=_load(args.prior_audit.resolve()),
        reconciliation=_load(reconciliation_path),
        adapter_manifest_file_sha256=_file_sha256(args.manifest.resolve()),
        prior_audit_file_sha256=_file_sha256(args.prior_audit.resolve()),
        reconciliation_file_sha256=_file_sha256(reconciliation_path),
        repository_head=head,
    )
    path = _write_output(args.output_dir, value)
    print("R5_HISTORY_CONTINUATION_AUDIT_STATUS=" + value["status"])
    print("AUTHORIZED_HISTORY_CASES=" + str(len(value["authorized_history_case_ids"])))
    print("CONTINUATION_PROVIDER_UPPER_BOUND=" + str(value["continuation_provider_upper_bound"]))
    print("CONTINUATION_VITIS_UPPER_BOUND=" + str(value["continuation_vitis_upper_bound"]))
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    print("AUDIT_FILE=" + str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
