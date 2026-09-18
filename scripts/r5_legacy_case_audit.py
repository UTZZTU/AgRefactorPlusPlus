#!/usr/bin/env python3
"""Audit legacy real cases for R5 dataset eligibility without relabeling them."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping


_RUN_TIMESTAMP = re.compile(r"(20\d{6}T\d{6}Z)")


def _load(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _sha(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _timestamp(root: Path, launch: Mapping[str, Any]) -> str | None:
    for candidate in (str(launch.get("run_id", "")), root.name):
        match = _RUN_TIMESTAMP.search(candidate)
        if match:
            return datetime.strptime(
                match.group(1), "%Y%m%dT%H%M%SZ"
            ).replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    return None


def audit_root(root: Path, *, cutoff: str) -> dict[str, Any]:
    cutoff_time = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
    cases = []
    case_root = root / "cases"
    for directory in sorted(case_root.iterdir()) if case_root.is_dir() else ():
        if not directory.is_dir():
            continue
        required = {
            "expected": directory / "case_expected.json",
            "result": directory / "case_result.json",
            "launch": directory / "launch_contract.json",
            "identity": directory / "product_artifacts" / "execution_identity.json",
        }
        missing = [name for name, path in required.items() if not path.is_file()]
        if missing:
            cases.append(
                {
                    "case_id": directory.name,
                    "status": "invalid_evidence",
                    "missing_artifacts": missing,
                    "future_eligible": False,
                }
            )
            continue
        expected = _load(required["expected"])
        result = _load(required["result"])
        launch = _load(required["launch"])
        identity = _load(required["identity"])
        observed_at = _timestamp(root, launch)
        observed_time = (
            None
            if observed_at is None
            else datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        )
        before_cutoff = observed_time is not None and observed_time < cutoff_time
        completeness = identity.get("completeness")
        identity_complete = (
            isinstance(completeness, Mapping)
            and completeness.get("required_non_sensitive_fields_present") is True
            and completeness.get("actual_prompt_calls_recorded") is True
            and completeness.get("actual_toolchain_version_recorded") is True
        )
        source_sha = expected.get("source_sha256")
        public_sha = expected.get("public_sha256")
        hidden_sha = expected.get("hidden_sha256")
        hashes_complete = all(
            isinstance(item, str) and len(item) == 64
            for item in (source_sha, public_sha, hidden_sha)
        )
        reasons = []
        if before_cutoff:
            reasons.append("outcome_observed_before_r5_dataset_freeze")
        if not identity_complete:
            reasons.append("execution_identity_incomplete_for_r5")
        if not hashes_complete:
            reasons.append("source_or_test_hash_missing")
        reasons.append("no_predeclared_r5_control_role")
        cases.append(
            {
                "case_id": directory.name,
                "accepted": result.get("accepted") is True,
                "source_sha256": source_sha,
                "public_sha256": public_sha,
                "hidden_sha256": hidden_sha,
                "observed_at": observed_at,
                "before_r5_cutoff": before_cutoff,
                "identity_complete": identity_complete,
                "hashes_complete": hashes_complete,
                "predecessor_history_eligible": (
                    before_cutoff and identity_complete and hashes_complete
                ),
                "future_eligible": False,
                "control_role": "unassigned",
                "reasons": reasons,
            }
        )
    source_hashes = {
        item.get("source_sha256")
        for item in cases
        if item.get("predecessor_history_eligible") is True
    }
    result = {
        "schema_version": 1,
        "audit_id": "v2.3-r5-legacy-case-eligibility-v1",
        "created_at": datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "root": str(root),
        "r5_cutoff": cutoff,
        "status": "blocked_data_acquisition",
        "case_count": len(cases),
        "predecessor_history_eligible_count": sum(
            item.get("predecessor_history_eligible") is True for item in cases
        ),
        "future_eligible_count": 0,
        "independent_history_source_count": len(source_hashes),
        "positive_control_count": 0,
        "inapplicable_control_count": 0,
        "history_future_split_frozen": False,
        "source_holdout_verified": False,
        "real_campaign_allowed": False,
        "reasons": [
            "legacy outcomes predate the R5 dataset freeze",
            "future outcomes cannot be recovered retrospectively",
            "positive and inapplicable control roles were not predeclared",
        ],
        "cases": cases,
        "provider_calls": 0,
        "vitis_launches": 0,
        "git_history_mutations": 0,
        "audit_sha256": "",
    }
    result["audit_sha256"] = _sha(
        {key: value for key, value in result.items() if key != "audit_sha256"}
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--cutoff", default="2026-09-18T00:00:00Z")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit_root(args.root, cutoff=args.cutoff)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"R5_LEGACY_CASE_AUDIT_STATUS={result['status']}")
    print(f"CASE_COUNT={result['case_count']}")
    print(
        "PREDECESSOR_HISTORY_ELIGIBLE="
        f"{result['predecessor_history_eligible_count']}"
    )
    print(f"FUTURE_ELIGIBLE={result['future_eligible_count']}")
    print(f"INDEPENDENT_HISTORY_SOURCES={result['independent_history_source_count']}")
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    print("R5_REAL_CAMPAIGN_ALLOWED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
