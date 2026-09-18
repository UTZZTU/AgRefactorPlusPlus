#!/usr/bin/env python3
"""Audit a frozen R5 oracle-adapter manifest without real execution."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agrefactor.campaign.r5_protocol import estimate_upper_bound
from agrefactor.recovery.r5_budget import R5BudgetLedger


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
_SAFE_RELATIVE = re.compile(r"^[A-Za-z0-9_./-]+$")
_FILE_ROLES = (
    "source",
    "legacy_candidate",
    "legacy_testbench",
    "public_test",
    "hidden_test",
)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _relative(repository: Path, path: Path, name: str) -> str:
    try:
        relative = path.resolve().relative_to(repository.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"{name} is outside the repository") from exc
    if not _SAFE_RELATIVE.fullmatch(relative) or ".." in Path(relative).parts:
        raise ValueError(f"{name} is not a safe repository-relative path")
    return relative


def _git(repository: Path, *args: str, text: bool = True) -> Any:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=text,
    )
    return completed.stdout.strip() if text else completed.stdout


def _git_blob(repository: Path, commit: str, relative: str) -> bytes:
    if not _SAFE_RELATIVE.fullmatch(relative) or ".." in Path(relative).parts:
        raise ValueError("manifest contains an unsafe repository path")
    return _git(repository, "show", f"{commit}:{relative}", text=False)


def _verify_signed_object(value: Mapping[str, Any], field: str) -> str:
    stored = value.get(field)
    if not isinstance(stored, str) or not _SHA256.fullmatch(stored):
        raise ValueError(f"{field} is missing or malformed")
    unsigned = {key: item for key, item in value.items() if key != field}
    if _sha(unsigned) != stored:
        raise ValueError(f"{field} mismatch")
    return stored


def _signed(value: dict[str, Any], field: str) -> dict[str, Any]:
    value[field] = _sha(value)
    return value


def build_audit(
    repository: Path,
    manifest: Mapping[str, Any],
    plan: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    manifest_file_sha256: str,
    plan_relative_path: str,
) -> dict[str, dict[str, Any]]:
    repository = repository.resolve()
    if not _SHA256.fullmatch(manifest_file_sha256):
        raise ValueError("manifest_file_sha256 must be SHA-256")
    manifest_sha256 = _verify_signed_object(manifest, "manifest_sha256")
    if manifest.get("status") != "ready_for_zero_call_protocol_audit":
        raise ValueError("adapter manifest is not ready for protocol audit")
    if any(
        manifest.get(name) != expected
        for name, expected in {
            "outcomes_observed": False,
            "provider_calls": 0,
            "vitis_launches": 0,
            "git_history_mutations": 0,
            "real_campaign_allowed": False,
            "trusted_revision_creation_allowed": False,
            "source_holdout_verified": True,
            "positive_and_inapplicable_controls_verified": True,
            "public_hidden_independence_verified": True,
        }.items()
    ):
        raise ValueError("adapter manifest safety flags are incomplete")

    commit = manifest.get("repository_commit")
    if not isinstance(commit, str) or not _GIT_COMMIT.fullmatch(commit):
        raise ValueError("repository_commit is malformed")
    _git(repository, "cat-file", "-e", f"{commit}^{{commit}}")
    ancestor = subprocess.run(
        ["git", "-C", str(repository), "merge-base", "--is-ancestor", commit, "HEAD"],
        check=False,
        capture_output=True,
    )
    if ancestor.returncode != 0:
        raise ValueError("frozen repository commit is not an ancestor of HEAD")

    if _sha(plan) != manifest.get("plan_sha256"):
        raise ValueError("plan_sha256 mismatch")
    frozen_plan = json.loads(
        _git_blob(repository, commit, plan_relative_path).decode("utf-8")
    )
    if _canonical(frozen_plan) != _canonical(plan):
        raise ValueError("plan differs from the copy at the frozen commit")
    plan_cases = plan.get("cases")
    manifest_cases = manifest.get("cases")
    if not isinstance(plan_cases, list) or not isinstance(manifest_cases, list):
        raise ValueError("plan and manifest cases are required")
    planned = {
        item.get("case_id"): item
        for item in plan_cases
        if isinstance(item, Mapping) and isinstance(item.get("case_id"), str)
    }
    if len(planned) != len(plan_cases) or len(manifest_cases) != len(plan_cases):
        raise ValueError("plan and manifest case identities do not match")

    records: list[dict[str, Any]] = []
    source_periods: dict[str, set[str]] = {}
    adapter_hashes: set[str] = set()
    for raw in manifest_cases:
        if not isinstance(raw, Mapping):
            raise ValueError("manifest case must be an object")
        case_id = raw.get("case_id")
        expected = planned.get(case_id)
        if expected is None:
            raise ValueError("manifest contains an unplanned case")
        for field in ("period", "control_role", "top", "prior_evidence"):
            if raw.get(field) != expected.get(field):
                raise ValueError(f"{case_id}.{field} differs from the frozen plan")
        if raw.get("candidate_top") != str(raw.get("top")) + str(
            plan.get("candidate_top_suffix")
        ):
            raise ValueError(f"{case_id}.candidate_top is not plan-derived")
        if raw.get("outcome_observed") is not False or raw.get(
            "hidden_model_visible"
        ) is not False:
            raise ValueError(f"{case_id} observed an outcome or exposed Hidden data")
        paths = raw.get("paths")
        hashes = raw.get("hashes")
        if not isinstance(paths, Mapping) or not isinstance(hashes, Mapping):
            raise ValueError(f"{case_id} paths and hashes are required")
        for role in _FILE_ROLES:
            relative = paths.get(role)
            stored_hash = hashes.get(role)
            if relative != expected.get(role):
                raise ValueError(f"{case_id}.{role} differs from the frozen plan")
            if not isinstance(relative, str) or not isinstance(stored_hash, str):
                raise ValueError(f"{case_id}.{role} identity is incomplete")
            if _sha_bytes(_git_blob(repository, commit, relative)) != stored_hash:
                raise ValueError(f"{case_id}.{role} hash does not match frozen Git")
        if hashes["public_test"] == hashes["hidden_test"]:
            raise ValueError(f"{case_id} Public and Hidden adapters are identical")
        for role in ("public_test", "hidden_test"):
            value = hashes[role]
            if value in adapter_hashes:
                raise ValueError("adapter hash is reused across a Public/Hidden boundary")
            adapter_hashes.add(value)
        source_hash = hashes["source"]
        period = str(raw.get("period"))
        source_periods.setdefault(source_hash, set()).add(period)
        records.append(
            {
                "case_id": case_id,
                "period": period,
                "control_role": raw.get("control_role"),
                "failure_family": raw.get("failure_family"),
                "top": raw.get("top"),
                "candidate_top": raw.get("candidate_top"),
                "source_sha256": source_hash,
                "adapter_identity_sha256": raw.get("adapter_identity_sha256"),
                "public_test_sha256": hashes["public_test"],
                "hidden_test_sha256": hashes["hidden_test"],
                "outcome_observed": False,
                "hidden_model_visible": False,
            }
        )

    if any(len(periods) != 1 for periods in source_periods.values()):
        raise ValueError("source hash crosses history/future boundary")
    if len(source_periods) != len(records):
        raise ValueError("case set does not contain independent source hashes")
    history = [item for item in records if item["period"] == "history"]
    future = [item for item in records if item["period"] == "future"]
    if len(history) < 2 or len(future) < 2:
        raise ValueError("at least two history and two future cases are required")
    if any(item["control_role"] != "positive" for item in history):
        raise ValueError("history acquisition requires two positive controls")
    future_controls = {item["control_role"] for item in future}
    if future_controls != {"positive", "inapplicable_or_confusable"}:
        raise ValueError("future partition requires positive and inapplicable controls")
    if manifest.get("history_case_ids") != [item["case_id"] for item in history]:
        raise ValueError("history_case_ids order or membership mismatch")
    if manifest.get("future_case_ids") != [item["case_id"] for item in future]:
        raise ValueError("future_case_ids order or membership mismatch")

    required_state = {
        "R5_ACCEPTED": False,
        "R6_STARTED": False,
        "R5_PROVIDER_CALL_HARD_CAP": 500,
        "R5_VITIS_LAUNCH_HARD_CAP": 500,
        "R5_CONSUMED_PROVIDER_CALLS": 0,
        "R5_CONSUMED_VITIS_LAUNCHES": 0,
        "R5_REAL_CAMPAIGN_ALLOWED": False,
    }
    if any(state.get(key) != expected for key, expected in required_state.items()):
        raise ValueError("authoritative R5 state or budget is incompatible")

    budget = R5BudgetLedger(
        provider_cap=500,
        vitis_cap=500,
        provider_used=0,
        vitis_used=0,
    )
    pilot_provider, pilot_vitis = estimate_upper_bound(case_count=1)
    formal_provider, formal_vitis = estimate_upper_bound(case_count=len(future))
    if pilot_provider > 60 or pilot_vitis > 80:
        raise ValueError("one-case pilot exceeds the frozen pilot cap")
    reservation = budget.reserve_with_recovery(
        provider_upper_bound=formal_provider,
        vitis_upper_bound=formal_vitis,
    )

    dataset = _signed(
        {
            "schema_version": 1,
            "dataset_id": "v2.3-r5-reference-oracle-dataset-v1",
            "status": "ready_for_history_acquisition",
            "repository_commit": commit,
            "adapter_manifest_file_sha256": manifest_file_sha256,
            "adapter_manifest_sha256": manifest_sha256,
            "plan_sha256": manifest.get("plan_sha256"),
            "failure_family": manifest.get("failure_family"),
            "cases": records,
            "history_case_ids": [item["case_id"] for item in history],
            "future_case_ids": [item["case_id"] for item in future],
            "history_outcomes_observed": False,
            "future_outcomes_observed": False,
            "trusted_revision_creation_allowed": False,
            "real_campaign_allowed": False,
            "next_step": "acquire_two_independently_validated_positive_history_episodes",
        },
        "dataset_manifest_sha256",
    )
    split = _signed(
        {
            "schema_version": 1,
            "split_id": "v2.3-r5-history-future-split-v1",
            "status": "frozen_before_outcome_observation",
            "dataset_manifest_sha256": dataset["dataset_manifest_sha256"],
            "history_case_ids": dataset["history_case_ids"],
            "future_case_ids": dataset["future_case_ids"],
            "history_source_sha256": [item["source_sha256"] for item in history],
            "future_source_sha256": [item["source_sha256"] for item in future],
            "source_holdout_verified": True,
            "future_visibility": "forbidden_before_history_snapshot_freeze",
            "future_outcomes_observed": False,
        },
        "split_manifest_sha256",
    )
    budget_plan = _signed(
        {
            "schema_version": 1,
            "budget_plan_id": "v2.3-r5-future-only-budget-v1",
            "ledger": budget.to_dict(),
            "future_case_count": len(future),
            "repeats": 3,
            "pilot_case_count": 1,
            "pilot_provider_upper_bound": pilot_provider,
            "pilot_vitis_upper_bound": pilot_vitis,
            "pilot_provider_cap": 60,
            "pilot_vitis_cap": 80,
            "formal_provider_upper_bound": formal_provider,
            "formal_vitis_upper_bound": formal_vitis,
            "provider_recovery_reserve": reservation.provider_recovery_reserve,
            "vitis_recovery_reserve": reservation.vitis_recovery_reserve,
            "reservation_sha256": reservation.reservation_sha256,
            "fits_hard_caps_with_recovery_reserve": True,
            "provider_calls": 0,
            "vitis_launches": 0,
        },
        "budget_plan_sha256",
    )
    audit = _signed(
        {
            "schema_version": 1,
            "audit_id": "v2.3-r5-reference-oracle-protocol-audit-v1",
            "status": "ready_for_history_acquisition",
            "adapter_manifest_file_sha256": manifest_file_sha256,
            "adapter_manifest_sha256": manifest_sha256,
            "dataset_manifest_sha256": dataset["dataset_manifest_sha256"],
            "split_manifest_sha256": split["split_manifest_sha256"],
            "budget_plan_sha256": budget_plan["budget_plan_sha256"],
            "checks": {
                "frozen_commit_is_ancestor": True,
                "plan_matches_frozen_commit": True,
                "all_file_hashes_match_frozen_git": True,
                "public_hidden_adapters_are_distinct": True,
                "history_future_roles_predeclared": True,
                "source_holdout_verified": True,
                "history_has_two_positive_sources": True,
                "future_has_positive_and_inapplicable_controls": True,
                "future_only_upper_bound_verified": True,
                "no_outcomes_observed": True,
                "no_manual_trusted_revision": True,
            },
            "provider_calls": 0,
            "vitis_launches": 0,
            "git_history_mutations": 0,
            "trusted_revision_creation_allowed": False,
            "real_campaign_allowed": False,
            "next_step": "history_acquisition_through_existing_refactor_r4_path",
        },
        "protocol_audit_sha256",
    )
    return {
        "dataset_manifest": dataset,
        "split_manifest": split,
        "budget_plan": budget_plan,
        "protocol_audit": audit,
    }


def _write_output_directory(output_dir: Path, outputs: Mapping[str, Any]) -> None:
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        for name, value in outputs.items():
            (temporary / f"{name}.json").write_text(
                json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        temporary.replace(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    repository = args.repo.resolve()
    manifest_bytes = args.manifest.read_bytes()
    outputs = build_audit(
        repository,
        json.loads(manifest_bytes.decode("utf-8")),
        _load(args.plan),
        _load(args.state),
        manifest_file_sha256=_sha_bytes(manifest_bytes),
        plan_relative_path=_relative(repository, args.plan, "plan"),
    )
    _write_output_directory(args.output_dir, outputs)
    audit = outputs["protocol_audit"]
    budget = outputs["budget_plan"]
    print(f"R5_ORACLE_PROTOCOL_AUDIT_STATUS={audit['status']}")
    print(f"HISTORY_CASES={len(outputs['dataset_manifest']['history_case_ids'])}")
    print(f"FUTURE_CASES={len(outputs['dataset_manifest']['future_case_ids'])}")
    print(f"PILOT_PROVIDER_UPPER_BOUND={budget['pilot_provider_upper_bound']}")
    print(f"PILOT_VITIS_UPPER_BOUND={budget['pilot_vitis_upper_bound']}")
    print(f"FORMAL_PROVIDER_UPPER_BOUND={budget['formal_provider_upper_bound']}")
    print(f"FORMAL_VITIS_UPPER_BOUND={budget['formal_vitis_upper_bound']}")
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    print("GIT_HISTORY_MUTATIONS=0")
    print("R5_REAL_CAMPAIGN_ALLOWED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
