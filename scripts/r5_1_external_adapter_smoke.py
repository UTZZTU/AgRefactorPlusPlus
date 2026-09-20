#!/usr/bin/env python3
"""Run frozen external-source host oracle smokes without Provider or Vitis."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLAN = ROOT / "configs" / "r5_1" / "external_adapter_smoke_plan.json"
ALLOWED_DECISIONS = {"admit", "external-only"}
ALLOWED_MODES = {"concatenate", "separate_translation_units"}
MAX_PASS_MARKER_BYTES = 256


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


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _safe_relative(value: Any) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"invalid relative path: {value!r}")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"path escapes frozen root: {value}")
    return path


def validate_plan(plan: Mapping[str, Any]) -> None:
    if plan.get("schema_version") != 1:
        raise ValueError("unsupported plan schema")
    cases = plan.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("plan.cases must be a non-empty list")
    source_ids: set[str] = set()
    case_ids: set[str] = set()
    for case in cases:
        if not isinstance(case, Mapping):
            raise ValueError("case must be an object")
        source_id = str(case.get("source_id", ""))
        case_id = str(case.get("case_id", ""))
        if not source_id or source_id in source_ids:
            raise ValueError(f"source_id must be unique: {source_id}")
        if not case_id or case_id in case_ids:
            raise ValueError(f"case_id must be unique: {case_id}")
        source_ids.add(source_id)
        case_ids.add(case_id)
        if case.get("decision") not in ALLOWED_DECISIONS:
            raise ValueError(f"source is not eligible for external smoke: {source_id}")
        commit = case.get("commit")
        if not isinstance(commit, str) or len(commit) != 40:
            raise ValueError(f"invalid frozen commit: {source_id}")
        if case.get("compile_mode") not in ALLOWED_MODES:
            raise ValueError(f"invalid compile mode: {case_id}")
        adapter_path = case.get("adapter_path")
        if adapter_path is not None:
            _safe_relative(adapter_path)
        elif case.get("compile_mode") == "concatenate":
            raise ValueError(f"concatenate mode requires an adapter: {case_id}")
        source_files = case.get("source_files", [])
        for record in source_files:
            _safe_relative(record.get("path"))
            digest = record.get("sha256")
            if not isinstance(digest, str) or len(digest) != 64:
                raise ValueError(f"invalid source hash: {case_id}")
        if adapter_path is None and not any(
            record.get("role") == "oracle" for record in source_files
        ):
            raise ValueError(f"external oracle file is required: {case_id}")
        for value in case.get("include_dirs", []):
            _safe_relative(value)
        marker = case.get("pass_marker")
        if (
            not isinstance(marker, str)
            or not marker.strip()
            or marker != marker.strip()
            or "\n" in marker
            or "\r" in marker
            or "\0" in marker
            or len(marker.encode("utf-8")) > MAX_PASS_MARKER_BYTES
        ):
            raise ValueError(f"non-enforceable pass marker: {case_id}")


def _run(command: list[str], *, cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def _repo_identity(repo: Path) -> tuple[str, str]:
    head = _run(["git", "rev-parse", "HEAD"], cwd=repo, timeout=30)
    status = _run(["git", "status", "--porcelain"], cwd=repo, timeout=30)
    if head.returncode != 0 or status.returncode != 0:
        raise RuntimeError(f"external checkout identity unavailable: {repo}")
    return head.stdout.strip(), status.stdout.strip()


def run_smoke(
    root: Path,
    external_root: Path,
    output: Path,
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    validate_plan(plan)
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"output must be empty or absent: {output}")
    output.mkdir(parents=True, exist_ok=True)
    compiler = shutil.which("g++")
    if compiler is None:
        raise RuntimeError("g++ is required for P3 host smoke")
    compiler_version = _run([compiler, "--version"], cwd=output, timeout=30)
    results: list[dict[str, Any]] = []
    for case in plan["cases"]:
        case_id = case["case_id"]
        repo = external_root / case["checkout_dir"]
        head, dirty = _repo_identity(repo)
        if head != case["commit"]:
            raise ValueError(f"external_commit_mismatch:{case_id}")
        if dirty:
            raise ValueError(f"external_checkout_dirty:{case_id}")
        source_paths: list[Path] = []
        source_identities: list[dict[str, Any]] = []
        for record in case["source_files"]:
            relative = _safe_relative(record["path"])
            path = repo / relative
            if not path.is_file() or sha_file(path) != record["sha256"]:
                raise ValueError(f"external_source_hash_mismatch:{case_id}:{relative}")
            source_paths.append(path)
            source_identities.append(
                {"path": relative.as_posix(), "sha256": record["sha256"]}
            )
        adapter_relative = (
            _safe_relative(case["adapter_path"])
            if case.get("adapter_path") is not None
            else None
        )
        adapter = root / adapter_relative if adapter_relative is not None else None
        adapter_sha = sha_file(adapter) if adapter is not None else None
        if adapter is not None and adapter_sha != case["adapter_sha256"]:
            raise ValueError(f"adapter_hash_mismatch:{case_id}")
        case_output = output / case_id
        case_output.mkdir()
        binary = case_output / "oracle_smoke"
        compile_inputs: list[Path]
        if case["compile_mode"] == "concatenate":
            combined = case_output / "combined.cpp"
            with combined.open("wb") as stream:
                for path in source_paths:
                    stream.write(path.read_bytes().replace(b"\r\n", b"\n"))
                    stream.write(b"\n")
                assert adapter is not None
                stream.write(adapter.read_bytes().replace(b"\r\n", b"\n"))
            compile_inputs = [combined]
        else:
            compile_inputs = [*source_paths]
            if adapter is not None:
                compile_inputs.append(adapter)
        include_flags: list[str] = []
        for relative in case.get("include_dirs", []):
            include_flags.extend(["-I", str(repo / _safe_relative(relative))])
        compile_command = [
            compiler,
            "-std=c++17",
            "-O0",
            "-Wall",
            "-Wextra",
            "-Wno-unknown-pragmas",
            *include_flags,
            *(str(path) for path in compile_inputs),
            "-o",
            str(binary),
        ]
        compiled = _run(compile_command, cwd=case_output, timeout=120)
        executed = None
        if compiled.returncode == 0:
            executed = _run([str(binary)], cwd=case_output, timeout=60)
        stdout = executed.stdout if executed is not None else ""
        stderr = executed.stderr if executed is not None else ""
        passed = bool(
            compiled.returncode == 0
            and executed is not None
            and executed.returncode == 0
            and case["pass_marker"] in stdout
        )
        (case_output / "compile.stdout").write_text(compiled.stdout, encoding="utf-8")
        (case_output / "compile.stderr").write_text(compiled.stderr, encoding="utf-8")
        (case_output / "run.stdout").write_text(stdout, encoding="utf-8")
        (case_output / "run.stderr").write_text(stderr, encoding="utf-8")
        results.append(
            {
                "case_id": case_id,
                "source_id": case["source_id"],
                "decision": case["decision"],
                "commit": head,
                "source_files": source_identities,
                "adapter_path": adapter_relative.as_posix() if adapter_relative else None,
                "adapter_sha256": adapter_sha,
                "compile_mode": case["compile_mode"],
                "compile_returncode": compiled.returncode,
                "run_returncode": executed.returncode if executed is not None else None,
                "pass_marker": case["pass_marker"],
                "pass_marker_observed": case["pass_marker"] in stdout,
                "status": "passed" if passed else "failed",
            }
        )
    status = "passed" if all(item["status"] == "passed" for item in results) else "failed"
    result = {
        "schema_version": 1,
        "run_id": "v2.3-r5.1-p3-external-adapter-host-smoke-v1",
        "status": status,
        "plan_sha256": sha_value(plan),
        "compiler": compiler,
        "compiler_version": compiler_version.stdout.splitlines()[0],
        "cases": results,
        "provider_calls": 0,
        "vitis_launches": 0,
        "git_history_mutations": 0,
        "formal_admission_unchanged": True,
        "r5_accepted": False,
        "r6_started": False,
    }
    result["result_sha256"] = sha_value(result)
    (output / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--external-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    args = parser.parse_args()
    plan = load_object(args.plan)
    result = run_smoke(
        args.repo.resolve(),
        args.external_root.resolve(),
        args.output.resolve(),
        plan,
    )
    print(f"R5_1_P3_EXTERNAL_SMOKE_STATUS={result['status']}")
    print(f"R5_1_P3_EXTERNAL_SMOKE_CASES={len(result['cases'])}")
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    print("GIT_HISTORY_MUTATIONS=0")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
