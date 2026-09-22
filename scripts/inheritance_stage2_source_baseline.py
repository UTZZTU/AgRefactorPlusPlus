#!/usr/bin/env python3
"""Run the four frozen Stage II sources through existing formal validators."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
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
from scripts.inheritance_stage2_preflight import (  # noqa: E402
    canonical_sha256,
    file_sha256,
    git,
    load_object,
    verify_protocol,
    write_object,
)
from scripts.r5_1_source_baseline import (  # noqa: E402
    REFERENCE_STUB,
    _physical_for_state,
    _stage_statuses,
    classify_terminal,
)


DEFAULT_PROTOCOL = ROOT / "configs" / "inheritance" / "stage2" / "protocol.json"


def evidence_inventory(root: Path) -> list[dict[str, Any]]:
    records = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        records.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": file_sha256(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    protocol_path = args.protocol.resolve()
    output = args.output.resolve()
    protocol = load_object(protocol_path)
    verify_protocol(repo, protocol)
    preflight = load_object(args.preflight.resolve())
    if (
        preflight.get("status") != "passed"
        or preflight.get("protocol_file_sha256") != file_sha256(protocol_path)
        or preflight.get("protocol_sha256") != canonical_sha256(protocol)
    ):
        raise ValueError("independent host preflight is absent or stale")
    if output.exists() and any(output.iterdir()):
        raise ValueError("output must be empty or absent")
    output.mkdir(parents=True, exist_ok=True)
    branch = git(repo, "branch", "--show-current")
    head = git(repo, "rev-parse", "HEAD")
    if branch != "research-roadmap-v2.3":
        raise ValueError("wrong branch")
    if git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("repository must be clean")
    if preflight.get("repo_head") != head:
        raise ValueError("preflight repository identity is stale")
    target = resolve_target_profile(protocol["target_profile"]["name"])
    route_prefix = (
        "stage2b"
        if protocol.get("route") == "V2.3-INHERITANCE-FIRST-STAGE-II-B"
        else "stage2"
    )
    cases = []
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
            suite_version="inheritance-stage2-v1",
            case_count=1,
            testbench_path=f"frozen:{case['source_oracle_path']}",
            runtime_contract=contract,
        )
        task = TaskSpec(
            task_id=f"inheritance-{route_prefix}-{case['case_id']}-source",
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
        run_id = f"inheritance-{route_prefix}-{case['case_id']}-source"
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
            execution_error = {
                "message": str(exc)[:2000],
                "type": type(exc).__name__,
            }
        usage = budget.snapshot().to_dict()
        if outcome is None:
            classification = "infrastructure_failure"
            stages = {
                "S0_compile": "unknown",
                "S1_public_csim": "not_run",
                "S2_csynth": "not_run",
                "S3_public_cosim": "not_run",
            }
            terminal_state = "execution_error"
            validation = None
            feedback = None
        else:
            terminal_state = outcome.terminal_state.value
            report = outcome.terminal_report
            owners = set() if report is None else {item.owner.value for item in report.items}
            classification = classify_terminal(
                accepted=outcome.result.accepted,
                terminal_state=terminal_state,
                owners=owners,
                physical_execution=_physical_for_state(terminal_state, usage),
            )
            stages = _stage_statuses(outcome)
            validation = outcome.to_dict()
            feedback = None if report is None else report.to_dict()
        record = {
            "budget_usage": usage,
            "case_id": case["case_id"],
            "classification": classification,
            "execution_error": execution_error,
            "source_path": case["source_path"],
            "source_sha256": case["source_sha256"],
            "stage_statuses": stages,
            "terminal_feedback": feedback,
            "terminal_state": terminal_state,
            "validation": validation,
        }
        write_object(case_root / "case_result.json", record)
        cases.append(record)
        write_object(
            output / "partial_result.json",
            {
                "cases": cases,
                "provider_calls": sum(item["budget_usage"]["llm_calls"] for item in cases),
                "status": "running",
                "vitis_launches": sum(
                    item["budget_usage"][name]
                    for item in cases
                    for name in ("csim_calls", "csynth_calls", "cosim_calls")
                ),
            },
        )
    provider_calls = sum(item["budget_usage"]["llm_calls"] for item in cases)
    vitis_launches = sum(
        item["budget_usage"][name]
        for item in cases
        for name in ("csim_calls", "csynth_calls", "cosim_calls")
    )
    repo_status_after = git(repo, "status", "--porcelain", "--untracked-files=all")
    result = {
        "branch": branch,
        "cases": cases,
        "completed_case_count": len(cases),
        "git_history_mutations": 0,
        "protocol_file_sha256": file_sha256(protocol_path),
        "protocol_sha256": canonical_sha256(protocol),
        "provider_calls": provider_calls,
        "repo_head": head,
        "repository_unchanged": not repo_status_after and git(repo, "rev-parse", "HEAD") == head,
        "schema_version": 1,
        "status": "complete" if len(cases) == len(protocol["cases"]) else "incomplete",
        "vitis_launches": vitis_launches,
    }
    result["result_sha256"] = canonical_sha256(result)
    write_object(output / "result.json", result)
    (output / "partial_result.json").unlink(missing_ok=True)
    write_object(
        output / "artifact_manifest.json",
        {
            "artifacts": evidence_inventory(output / "cases"),
            "provider_calls": provider_calls,
            "schema_version": 1,
            "vitis_launches": vitis_launches,
        },
    )
    print(f"INHERITANCE_STAGE2_SOURCE_BASELINE_STATUS={result['status']}")
    print(f"PROVIDER_CALLS={provider_calls}")
    print(f"VITIS_LAUNCHES={vitis_launches}")
    return 0 if result["status"] == "complete" and result["repository_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
