#!/usr/bin/env python3
"""Audit source-level HLSRewriter cases before R5 dataset admission.

The audit is intentionally static.  It records what the checked-in source,
testbench, and Tcl contracts can prove; it never calls a provider or Vitis and
it never creates an R5 history/future split.  A case marked ``adapter_required``
still needs an immutable, reviewed Public/Hidden adapter before it can enter a
real campaign.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Iterable


_INCLUDE = re.compile(r"^\s*#\s*include\s*([<\"])([^>\"]+)[>\"]", re.MULTILINE)
_RETURN = re.compile(r"\breturn\s+([^;]+);", re.MULTILINE)
_SET_TOP = re.compile(r"(?m)^\s*set_top\s+(?:\{([^}]+)\}|\"([^\"]+)\"|(\S+))")
_SET_PART = re.compile(r"(?m)^\s*set_part\s+(?:\{([^}]+)\}|\"([^\"]+)\"|(\S+))")
_CLOCK = re.compile(r"(?m)^\s*create_clock\b[^\n]*?-period\s+(\S+)")
_ADD_FILES = re.compile(r"(?m)^\s*add_files\s+(?:\{([^}]+)\}|\"([^\"]+)\"|(\S+))")
_FAILURE_ASSERT = re.compile(r"\b(?:assert|abort)\s*\(|\b(?:std::)?exit\s*\(\s*[1-9]")
_ORACLE_EVIDENCE = re.compile(
    r"(?:\bexpected\b|\bPASS(?:ED)?\b|\bFAIL(?:ED)?\b|mismatch|unmatch|"
    r"error_count|reference\s+output|SQNR_ref)",
    re.IGNORECASE,
)
_DATA_LITERAL = re.compile(
    r"[\"']([^\"']+\.(?:bmp|bin|csv|dat|hex|json|txt))[\"']",
    re.IGNORECASE,
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


def _sha_file(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def _capture(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    if match is None:
        return None
    for value in match.groups():
        if value:
            return value.strip()
    return None


def _git_provenance(repository: Path, relative: Path) -> dict[str, Any]:
    try:
        process = subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "log",
                "-1",
                "--format=%H%x00%aI",
                "--",
                relative.as_posix(),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "authored_at": None, "complete": False}
    commit, separator, authored_at = process.stdout.strip().partition("\x00")
    return {
        "commit": commit or None,
        "authored_at": authored_at or None if separator else None,
        "complete": bool(commit and separator and authored_at),
    }


def _return_contract(testbench: str) -> dict[str, Any]:
    expressions = [" ".join(item.split()) for item in _RETURN.findall(testbench)]
    normalized = [item.strip("() ") for item in expressions]
    nonzero = [item for item in normalized if item not in {"0", "EXIT_SUCCESS"}]
    failure_assert = _FAILURE_ASSERT.search(testbench) is not None
    return {
        "expressions": expressions,
        "always_zero_when_explicit": bool(expressions) and not nonzero,
        "nonzero_failure_path_present": bool(nonzero) or failure_assert,
        "assert_or_nonzero_exit_present": failure_assert,
    }


def _include_contract(case_root: Path, testbench: str) -> dict[str, Any]:
    local: list[str] = []
    system: list[str] = []
    missing: list[str] = []
    for delimiter, value in _INCLUDE.findall(testbench):
        if delimiter == "<":
            system.append(value)
            continue
        local.append(value)
        if not (case_root / value).is_file():
            missing.append(value)
    implementation = sorted(
        value for value in local if Path(value).name in {"main.cpp", "kernel.cpp", "candidate.cpp"}
    )
    return {
        "local": sorted(set(local)),
        "system": sorted(set(system)),
        "missing_local": sorted(set(missing)),
        "implementation_includes": implementation,
        "includes_implementation": bool(implementation),
    }


def _data_dependencies(case_root: Path, testbench: str) -> dict[str, Any]:
    referenced = sorted(set(_DATA_LITERAL.findall(testbench)))
    present = [value for value in referenced if (case_root / value).is_file()]
    missing = [value for value in referenced if not (case_root / value).is_file()]
    return {
        "referenced_literals": referenced,
        "present_local": present,
        "missing_local": missing,
        "dynamic_paths_may_require_manual_review": bool(
            re.search(r"\b(?:fstream|ifstream|ofstream)\b|\bfopen\s*\(", testbench)
        ),
    }


def _file_record(path: Path, *, relative_to: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(relative_to).as_posix(),
        "present": path.is_file(),
        "sha256": _sha_file(path),
        "size_bytes": path.stat().st_size if path.is_file() else None,
    }


def audit_case(case_root: Path, *, dataset_root: Path, repository: Path) -> dict[str, Any]:
    source = case_root / "main.cpp"
    reference = case_root / "kernel.cpp"
    legacy_tb = case_root / "tb.cpp"
    tcl = case_root / "vitis.tcl"
    public_tb = case_root / "public_tb.cpp"
    hidden_tb = case_root / "hidden_tb.cpp"
    required = (source, reference, legacy_tb, tcl)
    missing_required = [path.name for path in required if not path.is_file()]

    tb_text = _read(legacy_tb)
    tcl_text = _read(tcl)
    top = _capture(_SET_TOP, tcl_text)
    part = _capture(_SET_PART, tcl_text)
    clock = _capture(_CLOCK, tcl_text)
    add_files = [next(value for value in groups if value).strip() for groups in _ADD_FILES.findall(tcl_text)]
    stages = {
        "csim": re.search(r"(?m)^\s*csim_design\b", tcl_text) is not None,
        "csynth": re.search(r"(?m)^\s*csynth_design\b", tcl_text) is not None,
        "cosim": re.search(r"(?m)^\s*cosim_design\b", tcl_text) is not None,
    }
    includes = _include_contract(case_root, tb_text)
    data_dependencies = _data_dependencies(case_root, tb_text)
    returns = _return_contract(tb_text)
    semantic_oracle_evidence = _ORACLE_EVIDENCE.search(tb_text) is not None
    top_referenced = bool(top and re.search(rf"\b{re.escape(top)}\b", tb_text))
    public_hidden_present = public_tb.is_file() and hidden_tb.is_file()
    public_hidden_distinct = bool(
        public_hidden_present and _sha_file(public_tb) != _sha_file(hidden_tb)
    )
    candidate_added = any(Path(item).name == "candidate.cpp" for item in add_files)
    complete_target = all((top, part, clock))
    direct = all(
        (
            not missing_required,
            complete_target,
            public_hidden_distinct,
            candidate_added,
            all(stages.values()),
            returns["nonzero_failure_path_present"],
            not includes["includes_implementation"],
            not includes["missing_local"],
        )
    )

    reasons: list[str] = []
    if missing_required:
        reasons.append("required_source_artifact_missing")
    if not complete_target:
        reasons.append("target_identity_incomplete")
    if includes["includes_implementation"]:
        reasons.append("legacy_testbench_includes_implementation")
    if includes["missing_local"]:
        reasons.append("testbench_local_dependency_missing")
    if data_dependencies["missing_local"]:
        reasons.append("external_test_data_missing")
    if data_dependencies["dynamic_paths_may_require_manual_review"]:
        reasons.append("external_test_data_dependency_requires_review")
    if not top_referenced:
        reasons.append("candidate_top_call_not_statically_proven")
    if not returns["nonzero_failure_path_present"]:
        reasons.append("no_enforceable_process_failure_oracle")
    if not semantic_oracle_evidence:
        reasons.append("no_static_expected_or_comparison_evidence")
    if not public_hidden_distinct:
        reasons.append("distinct_public_hidden_oracles_missing")
    if not candidate_added:
        reasons.append("tcl_does_not_link_candidate")
    if not all(stages.values()):
        reasons.append("full_csim_csynth_cosim_prefix_missing")

    if direct:
        status = "eligible_direct"
        reasons = []
    elif (
        not missing_required
        and complete_target
        and semantic_oracle_evidence
        and not includes["missing_local"]
    ):
        status = "adapter_required"
    else:
        status = "rejected"

    relative = case_root.relative_to(repository)
    record = {
        "case_id": case_root.name,
        "relative_root": case_root.relative_to(dataset_root).as_posix(),
        "status": status,
        "reasons": reasons,
        "files": {
            "source": _file_record(source, relative_to=dataset_root),
            "reference": _file_record(reference, relative_to=dataset_root),
            "legacy_testbench": _file_record(legacy_tb, relative_to=dataset_root),
            "tcl": _file_record(tcl, relative_to=dataset_root),
            "public_testbench": _file_record(public_tb, relative_to=dataset_root),
            "hidden_testbench": _file_record(hidden_tb, relative_to=dataset_root),
        },
        "target": {
            "top": top,
            "part": part,
            "clock_period": clock,
            "complete": complete_target,
            "tcl_add_files": add_files,
            "stages": stages,
        },
        "testbench": {
            "includes": includes,
            "data_dependencies": data_dependencies,
            "return_contract": returns,
            "semantic_oracle_evidence": semantic_oracle_evidence,
            "top_referenced": top_referenced,
            "candidate_linked_by_tcl": candidate_added,
            "public_hidden_present": public_hidden_present,
            "public_hidden_distinct": public_hidden_distinct,
        },
        "provenance": _git_provenance(repository, relative),
        "admission": {
            "history_or_future_role": "unassigned",
            "control_role": "unassigned",
            "real_campaign_eligible": direct,
            "manual_adapter_review_required": status == "adapter_required",
            "public_hidden_split_status": (
                "frozen_distinct"
                if public_hidden_distinct
                else "adapter_review_required"
                if status == "adapter_required"
                else "unsupported"
            ),
            "outcome_observed": False,
        },
    }
    record["record_sha256"] = _sha(record)
    return record


def build_audit(dataset_root: Path, *, repository: Path) -> dict[str, Any]:
    cases = [
        audit_case(path, dataset_root=dataset_root, repository=repository)
        for path in sorted(dataset_root.iterdir())
        if path.is_dir()
    ]
    counts = {
        label: sum(item["status"] == label for item in cases)
        for label in ("eligible_direct", "adapter_required", "rejected")
    }
    result = {
        "schema_version": 1,
        "audit_id": "v2.3-r5-hlsrewritter-case-eligibility-v1",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "repository": str(repository),
        "dataset_root": str(dataset_root),
        "status": "ready_for_adapter_design" if counts["adapter_required"] else "blocked_data_acquisition",
        "case_count": len(cases),
        "classification_counts": counts,
        "cases": cases,
        "real_campaign_allowed": False,
        "trusted_revision_creation_allowed": False,
        "provider_calls": 0,
        "vitis_launches": 0,
        "git_history_mutations": 0,
        "reasons": [
            "source eligibility audit does not freeze history/future or control roles",
            "adapter_required cases need distinct reviewed Public and Hidden oracles",
            "no case may enter a campaign until the dataset contract and split are frozen",
        ],
        "audit_sha256": "",
    }
    result["audit_sha256"] = _sha(
        {key: value for key, value in result.items() if key != "audit_sha256"}
    )
    return result


def _case_roots(dataset_root: Path) -> Iterable[Path]:
    return (path for path in sorted(dataset_root.iterdir()) if path.is_dir())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repository = args.repo.resolve()
    dataset_root = (args.dataset_root or repository / "src" / "hlsrewritter").resolve()
    if not dataset_root.is_dir():
        raise SystemExit(f"dataset root is not a directory: {dataset_root}")
    result = build_audit(dataset_root, repository=repository)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"R5_HLSREWRITTER_AUDIT_STATUS={result['status']}")
    print(f"CASE_COUNT={result['case_count']}")
    for label, count in result["classification_counts"].items():
        print(f"{label.upper()}={count}")
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    print("GIT_HISTORY_MUTATIONS=0")
    print("R5_REAL_CAMPAIGN_ALLOWED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
