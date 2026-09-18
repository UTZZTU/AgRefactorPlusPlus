#!/usr/bin/env python3
"""Freeze predeclared R5 reference-oracle adapters without observing outcomes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping


_SAFE_RELATIVE = re.compile(r"^[A-Za-z0-9_./-]+$")
_ALLOWED_PERIODS = {"history", "future"}
_ALLOWED_CONTROLS = {"positive", "inapplicable_or_confusable"}
_ALLOWED_R2_BOUNDARIES = {"unknown_or_mixed_review", "inapplicable_or_confusable"}


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


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _resolve(repository: Path, value: Any, name: str) -> tuple[str, Path]:
    if not isinstance(value, str) or not _SAFE_RELATIVE.fullmatch(value):
        raise ValueError(f"{name} must be a safe repository-relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{name} escapes the repository")
    path = (repository / relative).resolve()
    try:
        path.relative_to(repository)
    except ValueError as exc:
        raise ValueError(f"{name} escapes the repository") from exc
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{name} must be a regular file")
    return relative.as_posix(), path


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _validate_adapter_source(
    *,
    text: str,
    top: str,
    candidate_top: str,
    split: str,
) -> None:
    if re.search(r'#\s*include\s*["<][^">]+\.cpp[">]', text):
        raise ValueError(f"{split} adapter must not include an implementation")
    for symbol in (top, candidate_top):
        if re.search(rf"\b{re.escape(symbol)}\s*\(", text) is None:
            raise ValueError(f"{split} adapter does not call {symbol}")
    if re.search(r"\breturn\s+1\s*;", text) is None:
        raise ValueError(f"{split} adapter lacks an enforceable failure return")


def build_manifest(repository: Path, plan: Mapping[str, Any]) -> dict[str, Any]:
    repository = repository.resolve()
    cases = plan.get("cases")
    if not isinstance(cases, list) or len(cases) < 2:
        raise ValueError("plan must contain at least two cases")
    if plan.get("status") != "predeclared_before_outcome_observation":
        raise ValueError("plan was not frozen before outcome observation")
    suffix = plan.get("candidate_top_suffix")
    if not isinstance(suffix, str) or not suffix:
        raise ValueError("candidate_top_suffix is required")

    records: list[dict[str, Any]] = []
    case_ids: set[str] = set()
    source_periods: dict[str, set[str]] = {}
    for raw in cases:
        if not isinstance(raw, Mapping):
            raise ValueError("case entry must be an object")
        case_id = raw.get("case_id")
        period = raw.get("period")
        control = raw.get("control_role")
        top = raw.get("top")
        failure_class = raw.get("expected_r2_failure_class")
        r2_boundary = raw.get("expected_r2_entry_boundary")
        deterministic_repair_expected = raw.get("deterministic_repair_expected")
        if not isinstance(case_id, str) or not case_id or case_id in case_ids:
            raise ValueError("case_id must be unique and non-empty")
        case_ids.add(case_id)
        if period not in _ALLOWED_PERIODS:
            raise ValueError(f"invalid period for {case_id}")
        if control not in _ALLOWED_CONTROLS:
            raise ValueError(f"invalid control role for {case_id}")
        if not isinstance(top, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", top):
            raise ValueError(f"invalid top for {case_id}")
        if not isinstance(failure_class, str) or not failure_class.strip():
            raise ValueError(f"expected R2 failure class is required for {case_id}")
        if r2_boundary not in _ALLOWED_R2_BOUNDARIES:
            raise ValueError(f"invalid expected R2 entry boundary for {case_id}")
        if deterministic_repair_expected is not False:
            raise ValueError(f"{case_id} is preclassified for deterministic repair")
        if control == "positive" and r2_boundary != "unknown_or_mixed_review":
            raise ValueError(f"positive case {case_id} cannot reach the R2 review boundary")
        paths: dict[str, str] = {}
        hashes: dict[str, str] = {}
        texts: dict[str, str] = {}
        for role in (
            "source",
            "legacy_candidate",
            "legacy_testbench",
            "public_test",
            "hidden_test",
        ):
            relative, path = _resolve(repository, raw.get(role), f"{case_id}.{role}")
            paths[role] = relative
            hashes[role] = _sha_file(path)
            if role in {"public_test", "hidden_test"}:
                texts[role] = path.read_text(encoding="utf-8")
        if hashes["public_test"] == hashes["hidden_test"]:
            raise ValueError(f"{case_id} Public/Hidden adapters are not distinct")
        candidate_top = top + suffix
        _validate_adapter_source(
            text=texts["public_test"],
            top=top,
            candidate_top=candidate_top,
            split="Public",
        )
        _validate_adapter_source(
            text=texts["hidden_test"],
            top=top,
            candidate_top=candidate_top,
            split="Hidden",
        )
        source_periods.setdefault(hashes["source"], set()).add(str(period))
        record = {
            "case_id": case_id,
            "failure_family": plan.get("primary_failure_family"),
            "period": period,
            "control_role": control,
            "top": top,
            "candidate_top": candidate_top,
            "paths": paths,
            "hashes": hashes,
            "prior_evidence": raw.get("prior_evidence"),
            "expected_r2_failure_class": failure_class.strip(),
            "expected_r2_entry_boundary": r2_boundary,
            "deterministic_repair_expected": False,
            "outcome_observed": False,
            "hidden_model_visible": False,
            "adapter_identity_sha256": "",
        }
        record["adapter_identity_sha256"] = _sha(
            {key: value for key, value in record.items() if key != "adapter_identity_sha256"}
        )
        records.append(record)

    crossing = sorted(
        source for source, periods in source_periods.items() if len(periods) > 1
    )
    if crossing:
        raise ValueError("source hash crosses history/future boundary")
    periods = {record["period"] for record in records}
    controls = {record["control_role"] for record in records}
    if periods != _ALLOWED_PERIODS:
        raise ValueError("both history and future cases are required")
    if controls != _ALLOWED_CONTROLS:
        raise ValueError("positive and inapplicable/confusable controls are required")
    invariants = plan.get("invariants")
    if not isinstance(invariants, Mapping) or any(
        invariants.get(name) is not expected
        for name, expected in {
            "expected_outputs_invented": False,
            "reference_top_is_oracle": True,
            "public_hidden_inputs_distinct": True,
            "original_benchmark_files_mutated": False,
            "history_future_roles_predeclared": True,
            "outcomes_observed_at_freeze": False,
            "hidden_model_visible": False,
            "trusted_revision_creation_allowed": False,
            "real_campaign_allowed": False,
        }.items()
    ):
        raise ValueError("plan invariants are incomplete")

    manifest = {
        "schema_version": 2,
        "manifest_id": "v2.3-r5-reference-oracle-adapter-manifest-v2",
        "plan_id": plan.get("plan_id"),
        "plan_sha256": _sha(plan),
        "frozen_at": plan.get("frozen_at"),
        "repository_commit": _git(repository, "rev-parse", "HEAD"),
        "failure_family": plan.get("primary_failure_family"),
        "cases": records,
        "history_case_ids": [
            record["case_id"] for record in records if record["period"] == "history"
        ],
        "future_case_ids": [
            record["case_id"] for record in records if record["period"] == "future"
        ],
        "source_holdout_verified": True,
        "positive_and_inapplicable_controls_verified": True,
        "public_hidden_independence_verified": True,
        "outcomes_observed": False,
        "provider_calls": 0,
        "vitis_launches": 0,
        "git_history_mutations": 0,
        "status": "ready_for_zero_call_protocol_audit",
        "trusted_revision_creation_allowed": False,
        "real_campaign_allowed": False,
    }
    manifest["manifest_sha256"] = _sha(manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_manifest(args.repo.resolve(), _load(args.plan))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"R5_ORACLE_ADAPTER_FREEZE_STATUS={manifest['status']}")
    print(f"HISTORY_CASES={len(manifest['history_case_ids'])}")
    print(f"FUTURE_CASES={len(manifest['future_case_ids'])}")
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    print("GIT_HISTORY_MUTATIONS=0")
    print("R5_REAL_CAMPAIGN_ALLOWED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
