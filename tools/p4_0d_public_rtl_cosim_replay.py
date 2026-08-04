#!/usr/bin/env python3
"""Deterministic P4-0D replay without network access or Vitis."""

from __future__ import annotations

import json
import tempfile

from agrefactor.config import (
    EvaluationSplit,
    RunMode,
    TaskSpec,
    TestSuiteSpec,
    default_target_profile,
)
from agrefactor.runtime import BudgetManager, RunContext, TraceRecorder
from agrefactor.runtime.cosim_stage import (
    CosimStageInputs,
    CosimValidationStageHandler,
)


def main() -> int:
    task = TaskSpec(
        task_id="p4d-replay",
        kernel_path="candidate.cpp",
        kernel_name="vector_add",
        target=default_target_profile(),
        mode=RunMode.REFACTOR,
        test_suites=(
            TestSuiteSpec(
                suite_id="public-1",
                split=EvaluationSplit.PUBLIC,
                testbench_path="public_tb.cpp",
            ),
        ),
    )
    context = RunContext(
        run_id="p4d-replay",
        task=task,
        budget=BudgetManager(),
        trace=TraceRecorder("p4d-replay", task_id=task.task_id),
    )
    with tempfile.TemporaryDirectory() as raw:
        handler = CosimValidationStageHandler(
            CosimStageInputs(
                work_dir=raw,
                original_code="int reference;",
                candidate_code="int candidate;",
                suite_testbench_codes={
                    "public-1": "int main(){return 0;}"
                },
                candidate_top_function="vector_add",
                target_profile=default_target_profile(),
                timelimit=30,
            ),
            executor=lambda **_: {
                "schema_version": 1,
                "status": "passed",
                "failure_kind": None,
                "failure_owner": "none",
                "reason_code": "cosim_passed",
                "timed_out": False,
                "returncode": 0,
                "tool_launched": True,
                "cosim_launched": True,
                "evidence_sha256": "f" * 64,
            },
        )
        report = handler(context)

    payload = {
        "accepted": not report.blocking,
        "network_llm_used": False,
        "repair_allowed": report.metadata.get("repair_allowed"),
        "hidden_evidence_exposed": report.metadata.get(
            "hidden_evidence_exposed"
        ),
        "declared_suite_count": report.metadata.get(
            "declared_suite_count"
        ),
        "attempted_suite_count": report.metadata.get(
            "attempted_suite_count"
        ),
    }
    expected = {
        "accepted": True,
        "network_llm_used": False,
        "repair_allowed": False,
        "hidden_evidence_exposed": False,
        "declared_suite_count": 1,
        "attempted_suite_count": 1,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    if payload != expected:
        return 1
    print("P4_0D_DETERMINISTIC_REPLAY_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
