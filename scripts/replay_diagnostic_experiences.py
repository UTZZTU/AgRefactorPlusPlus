#!/usr/bin/env python3
"""Replay a frozen, local diagnostic-experience retrieval fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agrefactor.evidence.case_record import ExperienceRecord
from agrefactor.evidence.case_store import (
    ExperienceSnapshot,
    StoredChange,
    StoredExperience,
)
from agrefactor.evidence.experience_retrieval import (
    CurrentCaseQuery,
    retrieve_experiences,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_frozen_file(root: Path, spec: dict[str, Any]) -> tuple[bytes, Path]:
    relative = spec.get("path")
    expected = spec.get("sha256")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("fixture path must be relative to the manifest")
    path = (root / relative).resolve(strict=True)
    path.relative_to(root)
    if not path.is_file():
        raise ValueError("fixture path is not a regular file")
    data = path.read_bytes()
    if not isinstance(expected, str) or _sha256(data) != expected:
        raise ValueError(f"fixture hash mismatch: {relative}")
    return data, path


def _build_snapshot(rows: list[dict[str, Any]], partition: str, revision: str):
    experiences: list[StoredExperience] = []
    fingerprints: dict[str, str] = {}
    source_families: dict[str, str] = {}
    for row in rows:
        if row.get("partition") != partition:
            continue
        record_data = dict(row["record"])
        patch = row.get("patch")
        if not isinstance(patch, str) or not patch:
            raise ValueError("fixture change patch must be non-empty text")
        fingerprint = _sha256(patch.encode("utf-8"))
        record_data["change_fingerprint"] = fingerprint
        record = ExperienceRecord.from_dict(record_data)
        attempt_ids = record.source_attempt_ids
        change = StoredChange(
            attempt_id=attempt_ids[-1],
            components=record.components,
            before_sha256=_sha256(f"before:{record.experience_id}".encode()),
            after_sha256=_sha256(f"after:{record.experience_id}".encode()),
            change_fingerprint=fingerprint,
            patch=patch,
            source_attempt_ids=attempt_ids,
        )
        experiences.append(StoredExperience(record, change))
        fingerprints[record.experience_id] = fingerprint
        family = row.get("source_family")
        if not isinstance(family, str) or not family:
            raise ValueError("experience source_family is required")
        source_families[family] = partition
    return ExperienceSnapshot(revision, tuple(experiences), ()), fingerprints, source_families


def replay_manifest(manifest_path: str | Path) -> dict[str, Any]:
    manifest_path = Path(manifest_path).expanduser().resolve(strict=True)
    root = manifest_path.parent.resolve(strict=True)
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("unsupported replay manifest")
    if manifest.get("dataset_status") != "synthetic_retrieval_contract_fixture":
        raise ValueError("replay manifest must identify synthetic fixture data")

    experiences_bytes, experiences_path = _read_frozen_file(
        root, manifest["experiences"]
    )
    queries_bytes, queries_path = _read_frozen_file(root, manifest["queries"])
    experience_data = json.loads(experiences_bytes)
    query_data = json.loads(queries_bytes)
    experience_rows = experience_data.get("experiences")
    scenarios = query_data.get("scenarios")
    if not isinstance(experience_rows, list) or not isinstance(scenarios, list):
        raise ValueError("fixture files must contain experience/scenario lists")

    snapshots: dict[str, ExperienceSnapshot] = {}
    fingerprints: dict[str, str] = {}
    family_partitions: dict[str, set[str]] = {}
    for partition in ("development", "heldout"):
        snapshot, partition_fingerprints, source_families = _build_snapshot(
            experience_rows,
            partition,
            _sha256(experiences_bytes + partition.encode()),
        )
        snapshots[partition] = snapshot
        fingerprints.update(partition_fingerprints)
        for family in source_families:
            family_partitions.setdefault(family, set()).add(partition)

    scenario_ids: set[str] = set()
    for scenario in scenarios:
        identity = scenario.get("scenario_id")
        partition = scenario.get("partition")
        family = scenario.get("source_family")
        if not isinstance(identity, str) or identity in scenario_ids:
            raise ValueError("scenario IDs must be unique strings")
        scenario_ids.add(identity)
        if partition not in snapshots or not isinstance(family, str) or not family:
            raise ValueError(f"invalid scenario partition/family: {identity}")
        family_partitions.setdefault(family, set()).add(partition)
    leaked_families = sorted(
        family for family, partitions in family_partitions.items() if len(partitions) > 1
    )
    if leaked_families:
        raise ValueError("source families cross development/heldout split")

    manifest_sources = {
        item.get("source_id"): item
        for item in manifest.get("sources", ())
        if isinstance(item, dict) and isinstance(item.get("source_id"), str)
    }
    results = []
    all_passed = True
    for scenario in scenarios:
        unknown_sources = set(scenario.get("source_refs", ())) - set(manifest_sources)
        if unknown_sources:
            raise ValueError(f"scenario references unknown sources: {scenario['scenario_id']}")
        query_data = dict(scenario["query"])
        attempted_ids = tuple(scenario.get("attempted_experience_ids", ()))
        query_data["attempted_change_fingerprints"] = tuple(
            fingerprints[identity] for identity in attempted_ids
        )
        query = CurrentCaseQuery(**query_data)
        retrieval = retrieve_experiences(query, snapshots[scenario["partition"]])
        positive = [item.item.record.experience_id for item in retrieval.positive]
        negative = [item.item.record.experience_id for item in retrieval.negative]
        excluded = dict(retrieval.excluded)
        expected = scenario["expected"]
        passed = (
            positive == expected.get("positive", [])
            and negative == expected.get("negative", [])
            and all(excluded.get(key) == reason for key, reason in expected.get("excluded", {}).items())
        )
        for item in (*retrieval.positive, *retrieval.negative):
            expected_change = expected.get("change_available", True)
            if item.change_available is not expected_change:
                passed = False
        expected_attempted = set(expected.get("previously_attempted", ()))
        actual_attempted = {
            item.item.record.experience_id
            for item in (*retrieval.positive, *retrieval.negative)
            if item.previously_attempted
        }
        if actual_attempted != expected_attempted:
            passed = False
        all_passed = all_passed and passed
        results.append(
            {
                "scenario_id": scenario["scenario_id"],
                "partition": scenario["partition"],
                "source_family": scenario["source_family"],
                "synthetic": bool(scenario.get("synthetic", False)),
                "source_refs": list(scenario.get("source_refs", ())),
                "positive": [
                    {
                        "experience_id": item.item.record.experience_id,
                        "score": list(item.score),
                        "reasons": list(item.reasons),
                    }
                    for item in retrieval.positive
                ],
                "negative": [
                    {
                        "experience_id": item.item.record.experience_id,
                        "score": list(item.score),
                        "reasons": list(item.reasons),
                        "previously_attempted": item.previously_attempted,
                    }
                    for item in retrieval.negative
                ],
                "excluded": [
                    {"experience_id": identity, "reason": reason}
                    for identity, reason in retrieval.excluded
                ],
                "passed": passed,
            }
        )

    source_audit = [
        {
            "source_id": item["source_id"],
            "batch": item["batch"],
            "relative_path": item["relative_path"],
            "sha256": item["sha256"],
            "fields_used": list(item["fields_used"]),
            "interpretation": item["interpretation"],
        }
        for item in manifest_sources.values()
    ]
    return {
        "schema_version": 1,
        "dataset_id": manifest["dataset_id"],
        "dataset_status": manifest["dataset_status"],
        "manifest_sha256": _sha256(manifest_bytes),
        "fixture_hashes": {
            "experiences": _sha256(experiences_path.read_bytes()),
            "queries": _sha256(queries_path.read_bytes()),
        },
        "source_artifacts": source_audit,
        "source_family_split_leaks": leaked_families,
        "scenario_count": len(results),
        "passed": all_passed,
        "scenarios": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        report = replay_manifest(args.manifest)
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        parser.exit(2, f"Replay failed: {type(exc).__name__}: {exc}\n")
    print(json.dumps({"passed": report["passed"], "scenario_count": report["scenario_count"], "output": str(output)}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
