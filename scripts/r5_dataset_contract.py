#!/usr/bin/env python3
"""Validate and freeze the R5 dataset boundary without inventing cases.

The predecessor inventory is evidence, not an R5 history/future split.  This
tool writes explicit blocked manifests until an operator supplies a separately
verified case manifest.  It never calls a provider or Vitis and never creates
Trusted memory revisions.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


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


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")
    return value


def build_contract(inventory: Mapping[str, Any]) -> dict[str, Any]:
    stored = inventory.get("inventory_sha256")
    unsigned = {key: value for key, value in inventory.items() if key != "inventory_sha256"}
    if not isinstance(stored, str) or stored != _sha(unsigned):
        raise ValueError("inventory_sha256 mismatch")
    readiness = inventory.get("dataset_readiness")
    if not isinstance(readiness, Mapping):
        raise ValueError("inventory.dataset_readiness is required")
    reasons = tuple(str(item) for item in readiness.get("reasons", ()))
    ready = readiness.get("primary_r5_campaign_ready") is True and not reasons
    status = "ready_for_case_manifest" if ready else "blocked_data_acquisition"
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    records = []
    for item in inventory.get("artifacts", ()):
        if not isinstance(item, Mapping):
            continue
        records.append(
            {
                "record_id": item.get("record_id"),
                "artifact_kind": item.get("artifact_kind"),
                "artifact_status": item.get("artifact_status"),
                "logical_artifact_key": item.get("logical_artifact_key"),
                "source_hashes": sorted(
                    {
                        value
                        for value in item.get("source_hashes", ())
                        if isinstance(value, str)
                    }
                ),
                "context_signatures": sorted(
                    {
                        value
                        for value in item.get("context_signatures", ())
                        if isinstance(value, str)
                    }
                ),
            }
        )
    dataset = {
        "schema_version": 1,
        "dataset_id": "v2.3-r5-dataset-boundary-v1",
        "created_at": now,
        "status": status,
        "inventory_sha256": stored,
        "history_future_split": None,
        "source_holdout": None,
        "cases": [],
        "predecessor_records": records,
        "trusted_revision_creation_allowed": False,
        "real_campaign_allowed": False,
        "reasons": list(reasons)
        or ["operator must provide a separately verified case manifest"],
    }
    split = {
        "schema_version": 1,
        "split_id": "v2.3-r5-history-future-split-v1",
        "status": "not_frozen",
        "inventory_sha256": stored,
        "history_case_ids": [],
        "future_case_ids": [],
        "source_holdout_verified": False,
        "future_visibility": "forbidden_before_snapshot_freeze",
        "reasons": list(dataset["reasons"]),
    }
    audit = {
        "schema_version": 1,
        "audit_id": "v2.3-r5-inventory-audit-v1",
        "inventory_sha256": stored,
        "dataset_manifest_sha256": _sha(dataset),
        "status": status,
        "provider_calls": 0,
        "vitis_launches": 0,
        "git_history_mutations": 0,
        "checks": {
            "inventory_hash_verified": True,
            "history_future_split_frozen": False,
            "source_holdout_verified": False,
            "positive_and_inapplicable_controls_verified": False,
            "no_manual_trusted_revision": True,
        },
        "reasons": list(dataset["reasons"]),
    }
    return {"dataset": dataset, "split": split, "audit": audit}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    contract = build_contract(_load(args.inventory))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, key in (
        ("dataset_manifest.json", "dataset"),
        ("split_manifest.json", "split"),
        ("inventory_audit.json", "audit"),
    ):
        (args.output_dir / name).write_text(
            json.dumps(contract[key], ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
    status = contract["dataset"]["status"]
    print(f"R5_DATASET_CONTRACT_STATUS={status}")
    print(f"R5_DATASET_CONTRACT_REASON={';'.join(contract['dataset']['reasons'])}")
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    print("GIT_HISTORY_MUTATIONS=0")
    print("R5_REAL_CAMPAIGN_ALLOWED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
