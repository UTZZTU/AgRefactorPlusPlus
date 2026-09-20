#!/usr/bin/env python3
"""File-only independent auditor for the R5.1 P3 external host smoke."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


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


def audit_result(result: Mapping[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    stored = result.get("result_sha256")
    unsigned = {key: value for key, value in result.items() if key != "result_sha256"}
    if stored != sha_value(unsigned):
        findings.append({"severity": "critical", "code": "result_hash_mismatch"})
    cases = result.get("cases")
    if not isinstance(cases, list) or len(cases) < 4:
        findings.append({"severity": "critical", "code": "source_coverage_incomplete"})
        cases = []
    source_ids = {str(case.get("source_id")) for case in cases if isinstance(case, Mapping)}
    if len(source_ids) != len(cases):
        findings.append({"severity": "critical", "code": "source_smoke_not_one_per_source"})
    for case in cases:
        case_id = str(case.get("case_id"))
        if case.get("status") != "passed":
            findings.append({"severity": "critical", "code": f"smoke_failed:{case_id}"})
        if case.get("compile_returncode") != 0 or case.get("run_returncode") != 0:
            findings.append({"severity": "critical", "code": f"process_failed:{case_id}"})
        if case.get("pass_marker_observed") is not True:
            findings.append({"severity": "critical", "code": f"oracle_not_enforced:{case_id}"})
        if case.get("decision") not in {"admit", "external-only"}:
            findings.append({"severity": "critical", "code": f"invalid_decision:{case_id}"})
    for field in ("provider_calls", "vitis_launches", "git_history_mutations"):
        if result.get(field) != 0:
            findings.append({"severity": "critical", "code": f"nonzero_{field}"})
    if result.get("formal_admission_unchanged") is not True:
        findings.append({"severity": "critical", "code": "formal_admission_changed"})
    if result.get("r5_accepted") is not False or result.get("r6_started") is not False:
        findings.append({"severity": "critical", "code": "unauthorized_state_transition"})
    critical = sum(item["severity"] == "critical" for item in findings)
    audit = {
        "schema_version": 1,
        "audit_id": "v2.3-r5.1-p3-external-adapter-smoke-audit-v1",
        "status": "passed" if critical == 0 else "failed",
        "source_result_sha256": stored,
        "source_count": len(source_ids),
        "critical_findings": critical,
        "findings": findings,
        "provider_calls": 0,
        "vitis_launches": 0,
        "git_history_mutations": 0,
        "r5_accepted": False,
        "r6_started": False,
    }
    audit["audit_sha256"] = sha_value(audit)
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit = audit_result(load_object(args.result))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"R5_1_P3_EXTERNAL_AUDIT_STATUS={audit['status']}")
    print(f"R5_1_P3_EXTERNAL_AUDIT_SOURCES={audit['source_count']}")
    print(f"R5_1_P3_EXTERNAL_AUDIT_CRITICAL={audit['critical_findings']}")
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    print("GIT_HISTORY_MUTATIONS=0")
    return 0 if audit["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
