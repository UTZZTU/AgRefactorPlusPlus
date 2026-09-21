#!/usr/bin/env python3
"""Validate frozen Stage II oracles without Provider or Vitis calls."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROTOCOL = ROOT / "configs" / "inheritance" / "stage2" / "protocol.json"


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def write_object(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run(command: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def git(repo: Path, *args: str) -> str:
    completed = run(["git", *args], repo, 30)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "git command failed")
    return completed.stdout.strip()


def verify_protocol(repo: Path, protocol: dict[str, Any]) -> None:
    if protocol.get("schema_version") != 1:
        raise ValueError("unsupported protocol schema")
    if protocol.get("route") != "V2.3-INHERITANCE-FIRST-STAGE-II":
        raise ValueError("unexpected route")
    if [case.get("case_id") for case in protocol.get("cases", [])] != [
        "dfs",
        "mergesort",
        "ahocorasick",
        "strassen",
    ]:
        raise ValueError("case order or identity differs from the frozen cohort")
    execution = protocol.get("execution")
    expected_execution = {
        "candidate_top_suffix": "_hls",
        "cosim_policy": "required",
        "hidden_tests": "none",
        "memory_enabled": False,
        "mode": "refactor",
        "optimize_enabled": False,
        "r2_r4_enabled": False,
    }
    if execution != expected_execution:
        raise ValueError("execution boundary is not ordinary refactor")
    budget = protocol.get("budget")
    if budget != {
        "provider_calls_hard_cap": 200,
        "vitis_launches_hard_cap": 200,
    }:
        raise ValueError("Stage II budget authority changed")
    target = protocol.get("target_profile")
    if not isinstance(target, dict):
        raise ValueError("target profile is missing")
    if file_sha256(repo / target["path"]) != target["sha256"]:
        raise ValueError("target profile hash mismatch")
    for case in protocol["cases"]:
        if case.get("top") != "process_top" or case.get("dependencies") != []:
            raise ValueError(f"invalid top or dependencies: {case.get('case_id')}")
        for path_key, sha_key in (
            ("source_path", "source_sha256"),
            ("source_oracle_path", "source_oracle_sha256"),
            ("public_test_path", "public_test_sha256"),
            ("contract_path", "contract_sha256"),
            ("oracle_stub_path", "oracle_stub_sha256"),
            ("mismatch_stub_path", "mismatch_stub_sha256"),
        ):
            path = repo / case[path_key]
            if not path.is_file() or path.is_symlink():
                raise ValueError(f"missing frozen asset: {path_key}")
            if file_sha256(path) != case[sha_key]:
                raise ValueError(f"frozen asset hash mismatch: {path_key}")
        contract = load_object(repo / case["contract_path"])
        if (
            contract.get("schema_version") != 2
            or contract.get("kind") != "public_differential_self_check_v1"
            or contract.get("candidate_mismatch_returncodes") != [1]
            or not contract.get("cosim_interface_depths")
        ):
            raise ValueError(f"invalid runtime contract: {case['case_id']}")
        for key in ("source_oracle_path", "public_test_path"):
            text = (repo / case[key]).read_text(encoding="utf-8").lower()
            if "hidden" in text or "future" in text:
                raise ValueError(f"forbidden data-boundary token: {case['case_id']}:{key}")


def execute_binary(binary: Path, cwd: Path, marker: str, expected_rc: int) -> dict[str, Any]:
    completed = run([str(binary)], cwd, 30)
    return {
        "marker_observed": marker in completed.stdout,
        "returncode": completed.returncode,
        "stderr": completed.stderr,
        "stdout": completed.stdout,
        "valid": completed.returncode == expected_rc
        and ((expected_rc != 0) or marker in completed.stdout),
    }


def compile_case(
    compiler: str,
    source: Path,
    testbench: Path,
    output: Path,
    timeout: int,
    stub: Path | None = None,
) -> dict[str, Any]:
    command = [
        compiler,
        "-std=c++17",
        "-O0",
        "-Wall",
        "-Wextra",
        "-Wno-unknown-pragmas",
        str(source),
        str(testbench),
    ]
    if stub is not None:
        command.append(str(stub))
    command.extend(["-o", str(output)])
    completed = run(command, output.parent, timeout)
    return {
        "command": command,
        "returncode": completed.returncode,
        "stderr": completed.stderr,
        "stdout": completed.stdout,
        "valid": completed.returncode == 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    protocol_path = args.protocol.resolve()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("output must be empty or absent")
    output.mkdir(parents=True, exist_ok=True)
    protocol = load_object(protocol_path)
    verify_protocol(repo, protocol)
    if git(repo, "branch", "--show-current") != "research-roadmap-v2.3":
        raise ValueError("wrong branch")
    if git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("repository must be clean")
    compiler = shutil.which("g++")
    if compiler is None:
        raise RuntimeError("g++ is required")
    timeout = int(protocol["timeouts_s"]["host"])
    cases = []
    for case in protocol["cases"]:
        case_root = output / "cases" / case["case_id"]
        case_root.mkdir(parents=True)
        source = repo / case["source_path"]
        source_compile = compile_case(
            compiler,
            source,
            repo / case["source_oracle_path"],
            case_root / "source_oracle",
            timeout,
        )
        source_runs = []
        if source_compile["valid"]:
            source_runs = [
                execute_binary(
                    case_root / "source_oracle",
                    case_root,
                    case["source_marker"],
                    0,
                )
                for _ in range(2)
            ]
        differential_compile = compile_case(
            compiler,
            source,
            repo / case["public_test_path"],
            case_root / "differential_oracle",
            timeout,
            repo / case["oracle_stub_path"],
        )
        differential_runs = []
        if differential_compile["valid"]:
            differential_runs = [
                execute_binary(
                    case_root / "differential_oracle",
                    case_root,
                    case["public_marker"],
                    0,
                )
                for _ in range(2)
            ]
        mismatch_compile = compile_case(
            compiler,
            source,
            repo / case["public_test_path"],
            case_root / "differential_mismatch",
            timeout,
            repo / case["mismatch_stub_path"],
        )
        mismatch_run = None
        if mismatch_compile["valid"]:
            mismatch_run = execute_binary(
                case_root / "differential_mismatch",
                case_root,
                case["public_marker"],
                1,
            )
        passed = (
            source_compile["valid"]
            and len(source_runs) == 2
            and all(item["valid"] for item in source_runs)
            and source_runs[0]["stdout"] == source_runs[1]["stdout"]
            and differential_compile["valid"]
            and len(differential_runs) == 2
            and all(item["valid"] for item in differential_runs)
            and differential_runs[0]["stdout"] == differential_runs[1]["stdout"]
            and mismatch_compile["valid"]
            and mismatch_run is not None
            and mismatch_run["valid"]
            and not mismatch_run["marker_observed"]
        )
        record = {
            "case_id": case["case_id"],
            "differential_compile": differential_compile,
            "differential_runs": differential_runs,
            "mismatch_compile": mismatch_compile,
            "mismatch_run": mismatch_run,
            "passed": passed,
            "source_compile": source_compile,
            "source_runs": source_runs,
        }
        write_object(case_root / "preflight.json", record)
        cases.append(record)
    result = {
        "branch": git(repo, "branch", "--show-current"),
        "cases": cases,
        "git_history_mutations": 0,
        "provider_calls": 0,
        "protocol_file_sha256": file_sha256(protocol_path),
        "protocol_sha256": canonical_sha256(protocol),
        "repo_head": git(repo, "rev-parse", "HEAD"),
        "schema_version": 1,
        "status": "passed" if all(case["passed"] for case in cases) else "failed",
        "vitis_launches": 0,
    }
    result["result_sha256"] = canonical_sha256(result)
    write_object(output / "result.json", result)
    print(f"INHERITANCE_STAGE2_PREFLIGHT_STATUS={result['status']}")
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
