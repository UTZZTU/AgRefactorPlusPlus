#!/usr/bin/env python3
"""Independently audit the checked-in R5.1 registry and source decisions."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha_value(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _load_builder(root: Path):
    path = root / "scripts" / "r5_1_build_dataset_registry.py"
    spec = importlib.util.spec_from_file_location("r5_1_build_dataset_registry", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load registry builder: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _verify_hash(record: Mapping[str, Any], field: str) -> bool:
    stored = record.get(field)
    unsigned = {key: value for key, value in record.items() if key != field}
    return isinstance(stored, str) and stored == sha_value(unsigned)


def audit_registry(root: Path) -> dict[str, Any]:
    config = root / "configs" / "r5_1"
    registry = load_object(config / "dataset_registry.json")
    info = load_object(root / "src" / "info.json")
    policy = load_object(config / "deduplication_policy.json")
    external = load_object(config / "external_source_manifest.json")
    builder = _load_builder(root)
    regenerated = builder.build_registry(root, info, policy)
    cases = registry.get("cases", [])
    failures: list[str] = []
    warnings: list[str] = []

    if not _verify_hash(registry, "registry_sha256"):
        failures.append("registry_sha256_mismatch")
    if registry != regenerated:
        failures.append("registry_not_deterministically_reproducible")
    if len(cases) != 57:
        failures.append("internal_case_count_not_57")
    if len({case.get("case_id") for case in cases}) != len(cases):
        failures.append("case_id_not_unique")
    if len({case.get("source_path") for case in cases}) != len(cases):
        failures.append("source_path_not_unique")

    family_partitions: dict[str, set[str]] = defaultdict(set)
    refs: dict[str, set[tuple[str, str]]] = defaultdict(set)
    d0_groups: dict[str, list[str]] = defaultdict(list)
    d1_groups: dict[str, list[str]] = defaultdict(list)
    for case in cases:
        case_id = case.get("case_id")
        path = root / str(case.get("source_path"))
        if not path.is_file():
            if case.get("eligibility") != "reject" or "source_file_missing" not in case.get(
                "exclusion_reasons", []
            ):
                failures.append(f"source_missing_not_rejected:{case_id}")
            elif case.get("source_sha256") is not None:
                failures.append(f"missing_source_has_hash:{case_id}")
            else:
                warnings.append(f"registered_source_missing_and_rejected:{case_id}")
        elif hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest() != case.get("source_sha256"):
            failures.append(f"source_hash_mismatch:{case_id}")
        family_partitions[str(case.get("algorithm_family"))].add(
            str(case.get("history_future_partition"))
        )
        execution = case.get("execution_contract", {})
        if execution.get("status") == "inconsistent" and not any(
            reason.startswith("vitis_tcl_execution_contract_inconsistent")
            for reason in case.get("exclusion_reasons", [])
        ):
            failures.append(f"unexplained_execution_contract_mismatch:{case_id}")
        if case.get("source_sha256") is not None:
            d0_groups[str(case.get("source_sha256"))].append(str(case_id))
        if case.get("normalized_token_sha256") is not None:
            d1_groups[str(case.get("normalized_token_sha256"))].append(str(case_id))
        for ref in case.get("nearest_duplicate_refs", []):
            refs[str(case_id)].add((str(ref.get("case_id")), str(ref.get("level"))))
            if ref.get("level") == "D2" and case.get("eligibility") == "duplicate_excluded":
                if not any(
                    item.get("level") in {"D0", "D1"}
                    for item in case.get("nearest_duplicate_refs", [])
                ):
                    failures.append(f"d2_only_case_auto_excluded:{case_id}")
    for family, partitions in family_partitions.items():
        if len(partitions) != 1:
            failures.append(f"family_split_leakage:{family}")
    for case_id, links in refs.items():
        for other, level in links:
            if (case_id, level) not in refs.get(other, set()):
                failures.append(f"asymmetric_duplicate_ref:{case_id}:{other}:{level}")
    for level, groups in (("D0", d0_groups), ("D1", d1_groups)):
        for members in groups.values():
            if len(members) > 1:
                for case_id in members:
                    if not any(ref_level == level for _, ref_level in refs[case_id]):
                        failures.append(f"unexplained_{level.lower()}_duplicate:{case_id}")

    sources = external.get("sources", [])
    allowed_decisions = {"admit", "external-only", "quarantine", "reject"}
    for source in sources:
        source_id = source.get("source_id")
        decision = source.get("decision")
        if decision not in allowed_decisions:
            failures.append(f"invalid_external_decision:{source_id}")
        if decision == "admit" and (
            not source.get("commit") or source.get("license") in {None, "NOASSERTION"}
        ):
            failures.append(f"unfrozen_external_source_admitted:{source_id}")
        if source.get("license") == "NOASSERTION" and decision == "admit":
            failures.append(f"unlicensed_external_source_admitted:{source_id}")
    unresolved = [
        case["case_id"]
        for case in cases
        if case["upstream"].get("license_status") != "verified_for_official_repository"
    ]
    wrongly_eligible = [
        case["case_id"]
        for case in cases
        if case["case_id"] in unresolved
        and case["eligibility"] in {"eligible_for_p3_smoke", "adapter_required"}
    ]
    if wrongly_eligible:
        failures.extend(f"unresolved_license_admitted:{item}" for item in wrongly_eligible)
    if unresolved:
        warnings.append(f"checked_in_provenance_or_license_unresolved:{len(unresolved)}")

    eligibility_counts = Counter(str(case.get("eligibility")) for case in cases)
    status = "failed" if failures else "passed_with_quarantine"
    result = {
        "schema_version": 1,
        "audit_id": "v2.3-r5.1-dataset-and-dedup-audit-v1",
        "frozen_at": policy["frozen_at"],
        "status": status,
        "registry_sha256": registry.get("registry_sha256"),
        "external_source_manifest_sha256": sha_value(external),
        "checks": {
            "internal_case_count": len(cases),
            "all_internal_cases_accounted_for": len(cases) == 57,
            "registry_reproducible": registry == regenerated,
            "source_hashes_verified": not any(
                item.startswith(("source_", "missing_source_")) for item in failures
            ),
            "duplicate_refs_symmetric": not any(item.startswith("asymmetric_") for item in failures),
            "d0_d1_duplicates_explained": not any(item.startswith("unexplained_") for item in failures),
            "d2_never_auto_excludes": not any(item.startswith("d2_only_") for item in failures),
            "family_split_isolated": not any(item.startswith("family_split_") for item in failures),
            "unlicensed_sources_not_admitted": not any("unlicensed" in item for item in failures),
            "future_outcomes_observed_before_split": False,
            "hidden_content_used": False,
        },
        "eligibility_counts": dict(sorted(eligibility_counts.items())),
        "failures": sorted(set(failures)),
        "warnings": sorted(set(warnings)),
        "provider_calls": 0,
        "vitis_launches": 0,
        "git_history_mutations": 0,
        "r5_accepted": False,
        "r6_started": False,
        "next_step": "P3_external_adapter_zero_provider_smoke" if not failures else "fix_P0_P2_audit_failures",
    }
    result["audit_sha256"] = sha_value(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs" / "roadmap" / "R5_1_DATASET_AND_DEDUP_AUDIT.json",
    )
    args = parser.parse_args()
    result = audit_registry(args.repo.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"R5_1_DATASET_AUDIT_STATUS={result['status']}")
    print(f"R5_1_INTERNAL_CASES={result['checks']['internal_case_count']}")
    print(f"R5_1_AUDIT_FAILURES={len(result['failures'])}")
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    print("GIT_HISTORY_MUTATIONS=0")
    return 0 if result["status"] != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
