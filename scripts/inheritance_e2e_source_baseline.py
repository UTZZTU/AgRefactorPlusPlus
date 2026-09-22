#!/usr/bin/env python3
"""Run frozen sources through existing formal validators as an E2E control."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agrefactor.config import (  # noqa: E402
    EvaluationSplit,
    RunMode,
    TaskSpec,
    TestSuiteSpec,
    resolve_target_profile,
)
from agrefactor.runtime import (  # noqa: E402
    BudgetLimits,
    BudgetManager,
    CandidateValidationPlanRequest,
    LocalCandidateValidationHandlerFactory,
    RunContext,
    TraceRecorder,
    ValidationOrchestrator,
)
from scripts.r5_1_source_baseline import (  # noqa: E402
    REFERENCE_STUB,
    _physical_for_state,
    _stage_statuses,
    classify_terminal,
)


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
        raise ValueError(f"JSON object required: {path}")
    return value


def write_object(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def verify_protocol(repo: Path, protocol: dict[str, Any]) -> None:
    execution = protocol.get("execution", {})
    if protocol.get("schema_version") != 1 or execution.get("mode") != "refactor":
        raise ValueError("invalid E2E protocol")
    if execution.get("public_tests") != "auto" or execution.get("hidden_tests") != "auto":
        raise ValueError("E2E protocol must require auto Public and Hidden modes")
    route = protocol.get("route")
    expected = (
        ["dfs", "mergesort", "ahocorasick", "strassen"]
        if route == "V2.3-INHERITANCE-FIRST-STAGE-II"
        else ["linkedlist", "strassen_break"]
    )
    if route not in {
        "V2.3-INHERITANCE-FIRST-STAGE-II",
        "V2.3-INHERITANCE-FIRST-STAGE-II-B",
    } or [case.get("case_id") for case in protocol.get("cases", [])] != expected:
        raise ValueError("route or case order differs from frozen cohort")
    target = protocol["target_profile"]
    if file_sha256(repo / target["path"]) != target["sha256"]:
        raise ValueError("target profile hash mismatch")
    forbidden = {"public_test_path", "hidden_test_path", "public_tests", "hidden_tests"}
    for case in protocol["cases"]:
        if set(case) & forbidden:
            raise ValueError("provided testbench path leaked into E2E protocol")
        for key, hash_key in (
            ("source_path", "source_sha256"),
            ("source_oracle_path", "source_oracle_sha256"),
            ("contract_path", "contract_sha256"),
        ):
            path = repo / case[key]
            if not path.is_file() or file_sha256(path) != case[hash_key]:
                raise ValueError(f"frozen asset mismatch: {case['case_id']}:{key}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    protocol_path = args.protocol.resolve()
    output = args.output.resolve()
    protocol = load_object(protocol_path)
    verify_protocol(repo, protocol)
    if git(repo, "branch", "--show-current") != "research-roadmap-v2.3":
        raise ValueError("wrong branch")
    if git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("repository must be clean")
    if output.exists() and any(output.iterdir()):
        raise ValueError("output must be empty or absent")
    output.mkdir(parents=True, exist_ok=True)
    target = resolve_target_profile(protocol["target_profile"]["name"])
    route_prefix = "stage2b" if protocol["route"].endswith("STAGE-II-B") else "stage2"
    cases: list[dict[str, Any]] = []
    for case in protocol["cases"]:
        case_root = output / "cases" / case["case_id"]
        case_root.mkdir(parents=True)
        contract = load_object(repo / case["contract_path"])
        testbench = (repo / case["source_oracle_path"]).read_text(encoding="utf-8")
        source = (repo / case["source_path"]).read_text(encoding="utf-8")
        suite_id = f"{route_prefix}-{case['case_id']}-source"
        suite = TestSuiteSpec(
            suite_id=suite_id,
            split=EvaluationSplit.PUBLIC,
            suite_version="inheritance-e2e-source-v1",
            case_count=1,
            testbench_path=f"frozen:{case['source_oracle_path']}",
            runtime_contract=contract,
        )
        task = TaskSpec(
            task_id=f"inheritance-{suite_id}",
            kernel_path=case["source_path"],
            kernel_name=case["top"],
            target=target,
            mode=RunMode.REFACTOR,
            test_suites=(suite,),
        )
        budget = BudgetManager(
            BudgetLimits(
                max_llm_calls=0,
                max_tool_calls=11,
                max_compile_calls=5,
                max_csim_calls=1,
                max_csynth_calls=1,
                max_cosim_calls=1,
                max_tokens=0,
                max_cost_usd=0,
            )
        )
        run_id = f"inheritance-{suite_id}"
        trace = TraceRecorder(
            run_id,
            task_id=task.task_id,
            output_path=case_root / "trace.jsonl",
        )
        context = RunContext(run_id=run_id, task=task, budget=budget, trace=trace)
        factory = LocalCandidateValidationHandlerFactory(
            case_root / "work",
            csim_timelimit=int(protocol["timeouts_s"]["csim"]),
            csynth_timelimit=int(protocol["timeouts_s"]["csynth"]),
            cosim_timelimit=int(protocol["timeouts_s"]["cosim"]),
            cosim_policy="required",
        )
        request = CandidateValidationPlanRequest(
            task=task,
            candidate_code=source,
            original_code=REFERENCE_STUB,
            preflight_testbench_code=testbench,
            suite_testbench_codes={suite_id: testbench},
            attempt=0,
            validation_id=run_id,
            reference_top_function=None,
            candidate_top_function=case["top"],
        )
        outcome = None
        execution_error = None
        try:
            outcome = ValidationOrchestrator(factory.build(request)).run_detailed(
                context,
                validation_id=run_id,
            )
        except Exception as exc:
            execution_error = {"type": type(exc).__name__, "message": str(exc)[:2000]}
        usage = budget.snapshot().to_dict()
        if outcome is None:
            record = {
                "budget_usage": usage,
                "case_id": case["case_id"],
                "classification": "infrastructure_failure",
                "execution_error": execution_error,
                "source_path": case["source_path"],
                "source_sha256": case["source_sha256"],
                "stage_statuses": {
                    "S0_compile": "unknown",
                    "S1_public_csim": "not_run",
                    "S2_csynth": "not_run",
                    "S3_public_cosim": "not_run",
                },
                "terminal_state": "execution_error",
                "validation": None,
            }
        else:
            terminal_state = outcome.terminal_state.value
            report = outcome.terminal_report
            owners = set() if report is None else {item.owner.value for item in report.items}
            record = {
                "budget_usage": usage,
                "case_id": case["case_id"],
                "classification": classify_terminal(
                    accepted=outcome.result.accepted,
                    terminal_state=terminal_state,
                    owners=owners,
                    physical_execution=_physical_for_state(terminal_state, usage),
                ),
                "execution_error": execution_error,
                "source_path": case["source_path"],
                "source_sha256": case["source_sha256"],
                "stage_statuses": _stage_statuses(outcome),
                "terminal_state": terminal_state,
                "validation": outcome.to_dict(),
            }
        write_object(case_root / "case_result.json", record)
        cases.append(record)
    result = {
        "branch": git(repo, "branch", "--show-current"),
        "cases": cases,
        "completed_case_count": len(cases),
        "git_history_mutations": 0,
        "protocol_file_sha256": file_sha256(protocol_path),
        "protocol_sha256": canonical_sha256(protocol),
        "provider_calls": sum(int(case["budget_usage"].get("llm_calls", 0)) for case in cases),
        "repo_head": git(repo, "rev-parse", "HEAD"),
        "repository_unchanged": not git(repo, "status", "--porcelain", "--untracked-files=all"),
        "schema_version": 1,
        "status": "complete",
        "vitis_launches": sum(
            int(case["budget_usage"].get(key, 0))
            for case in cases
            for key in ("csim_calls", "csynth_calls", "cosim_calls")
        ),
    }
    result["result_sha256"] = canonical_sha256(result)
    write_object(output / "result.json", result)
    artifacts = [
        {
            "path": path.relative_to(output).as_posix(),
            "sha256": file_sha256(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(output.rglob("*"))
        if path.is_file()
    ]
    write_object(
        output / "artifact_manifest.json",
        {
            "artifacts": artifacts,
            "provider_calls": result["provider_calls"],
            "schema_version": 1,
            "vitis_launches": result["vitis_launches"],
        },
    )
    print(f"INHERITANCE_E2E_SOURCE_BASELINE_STATUS={result['status']}")
    print(f"PROVIDER_CALLS={result['provider_calls']}")
    print(f"VITIS_LAUNCHES={result['vitis_launches']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
