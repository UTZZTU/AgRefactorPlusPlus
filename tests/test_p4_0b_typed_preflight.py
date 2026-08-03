import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

from agrefactor.evaluation import TestbenchPreflight
from agrefactor.evidence import (
    FeedbackOwner,
    TestbenchFailureOwner,
    TestbenchPreflightComponent,
    TestbenchPreflightReasonCode,
)
from agrefactor.runtime import (
    BudgetLimits,
    BudgetManager,
    PreflightStageInputs,
    PreflightValidationStageHandler,
    RunContext,
    TraceRecorder,
)
from agrefactor.optimization import (
    QualificationStage,
    QualificationStatus,
    Stage3QualificationOrchestrator,
)
from agrefactor.config import TaskSpec


ORIGINAL = (
    'extern "C" int original_top(int x) {\n'
    '    return x + 1;\n'
    '}\n'
)
CANDIDATE = (
    'extern "C" int candidate_top(int x) {\n'
    '    return x + 1;\n'
    '}\n'
)
TESTBENCH = (
    'extern "C" int original_top(int);\n'
    'extern "C" int candidate_top(int);\n'
    'int main() {\n'
    '    return original_top(4) != candidate_top(4);\n'
    '}\n'
)


def run_preflight(
    directory,
    *,
    testbench=TESTBENCH,
    original=ORIGINAL,
    candidate=CANDIDATE,
    budget=None,
):
    return TestbenchPreflight().compile_and_link(
        work_dir=directory,
        testbench_code=testbench,
        original_code=original,
        candidate_code=candidate,
        budget=budget,
        original_top_function="original_top",
        candidate_top_function="candidate_top",
    )


class P40BTypedPreflightTests(unittest.TestCase):
    def test_success_has_staged_evidence_and_physical_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = BudgetManager(BudgetLimits())
            result = run_preflight(
                directory,
                budget=budget,
            )
            invocation = json.loads(
                (
                    Path(directory)
                    / "testbench_preflight_invocation.json"
                ).read_text(encoding="utf-8")
            )
        self.assertTrue(result.succeeded)
        self.assertEqual(
            result.reason_code,
            TestbenchPreflightReasonCode.PASSED,
        )
        self.assertEqual(len(result.substeps), 9)
        self.assertEqual(
            [item.substage.value for item in result.substeps[:3]],
            [
                "testbench_compile",
                "reference_compile",
                "candidate_compile",
            ],
        )
        usage = budget.snapshot()
        self.assertEqual(usage.tool_calls, 9)
        self.assertEqual(usage.compile_calls, 6)
        self.assertEqual(invocation["schema_version"], 2)
        self.assertEqual(invocation["reason_code"], "passed")
        self.assertEqual(
            invocation["execution"]["status"],
            "completed",
        )

    def test_testbench_compile_failure_is_owned_before_other_units(self):
        broken = TESTBENCH.replace(";\n}", "\n}")
        with tempfile.TemporaryDirectory() as directory:
            result = run_preflight(
                directory,
                testbench=broken,
            )
        self.assertEqual(
            result.reason_code,
            TestbenchPreflightReasonCode.TESTBENCH_COMPILE_FAILED,
        )
        self.assertEqual(
            result.failure_owner,
            TestbenchFailureOwner.TESTBENCH,
        )
        self.assertEqual(len(result.substeps), 1)
        self.assertEqual(
            result.failed_component,
            TestbenchPreflightComponent.TESTBENCH,
        )

    def test_reference_compile_failure_is_owned(self):
        broken = ORIGINAL.replace("x + 1", "x +")
        with tempfile.TemporaryDirectory() as directory:
            result = run_preflight(
                directory,
                original=broken,
            )
        self.assertEqual(
            result.reason_code,
            TestbenchPreflightReasonCode.REFERENCE_COMPILE_FAILED,
        )
        self.assertEqual(
            result.failure_owner,
            TestbenchFailureOwner.ORIGINAL,
        )
        self.assertEqual(len(result.substeps), 2)

    def test_cand2_compile_failure_is_candidate_owned(self):
        broken = CANDIDATE.replace("x + 1", "x +")
        with tempfile.TemporaryDirectory() as directory:
            result = run_preflight(
                directory,
                candidate=broken,
            )
        self.assertEqual(
            result.reason_code,
            TestbenchPreflightReasonCode.CANDIDATE_COMPILE_FAILED,
        )
        self.assertEqual(
            result.failure_owner,
            TestbenchFailureOwner.CANDIDATE,
        )
        self.assertEqual(result.next_action, "repair_candidate")
        self.assertEqual(len(result.substeps), 3)
        self.assertFalse(
            any(
                item.substage.value == "link"
                for item in result.substeps
            )
        )

    def test_candidate_top_missing_is_candidate_owned(self):
        missing = CANDIDATE.replace(
            "candidate_top",
            "different_top",
        )
        with tempfile.TemporaryDirectory() as directory:
            result = run_preflight(
                directory,
                candidate=missing,
            )
        self.assertEqual(
            result.reason_code,
            TestbenchPreflightReasonCode.CANDIDATE_TOP_MISSING,
        )
        self.assertEqual(
            result.failure_owner,
            TestbenchFailureOwner.CANDIDATE,
        )
        self.assertEqual(
            result.failed_component,
            TestbenchPreflightComponent.CANDIDATE,
        )

    def test_reference_top_missing_is_reference_owned(self):
        missing = ORIGINAL.replace(
            "original_top",
            "different_top",
        )
        with tempfile.TemporaryDirectory() as directory:
            result = run_preflight(
                directory,
                original=missing,
            )
        self.assertEqual(
            result.reason_code,
            TestbenchPreflightReasonCode.REFERENCE_TOP_MISSING,
        )
        self.assertEqual(
            result.failure_owner,
            TestbenchFailureOwner.ORIGINAL,
        )

    def test_candidate_lto_interface_mismatch_is_candidate_owned(self):
        mismatch = (
            'extern "C" long candidate_top(long x) {\n'
            '    return x + 1;\n'
            '}\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            result = run_preflight(
                directory,
                candidate=mismatch,
            )
        self.assertEqual(
            result.reason_code,
            TestbenchPreflightReasonCode.INTERFACE_MISMATCH,
        )
        self.assertEqual(
            result.failure_owner,
            TestbenchFailureOwner.CANDIDATE,
        )
        self.assertIn("lto-type-mismatch", result.stderr)

    def test_unrelated_link_failure_is_unknown_safe(self):
        unresolved = (
            'extern int helper(int);\n'
            'extern "C" int candidate_top(int x) {\n'
            '    return helper(x);\n'
            '}\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            result = run_preflight(
                directory,
                candidate=unresolved,
            )
        self.assertEqual(
            result.reason_code,
            TestbenchPreflightReasonCode.LINK_FAILED,
        )
        self.assertIn(
            TestbenchPreflightReasonCode.OWNERSHIP_UNKNOWN,
            result.reason_codes,
        )
        self.assertEqual(
            result.failure_owner,
            TestbenchFailureOwner.UNKNOWN,
        )

    def test_stage_handler_retains_typed_agent_safe_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            task = TaskSpec(
                task_id="p4-0b-handler",
                kernel_path="kernel.cpp",
                kernel_name="candidate_top",
            )
            context = RunContext(
                run_id="p4-0b-handler",
                task=task,
                budget=BudgetManager(BudgetLimits()),
                trace=TraceRecorder(
                    "p4-0b-handler",
                    task_id=task.task_id,
                ),
            )
            broken = CANDIDATE.replace("x + 1", "x +")
            report = PreflightValidationStageHandler(
                PreflightStageInputs(
                    work_dir=directory,
                    testbench_code=TESTBENCH,
                    original_code=ORIGINAL,
                    candidate_code=broken,
                    original_top_function="original_top",
                    candidate_top_function="candidate_top",
                )
            )(context)
            payload = json.dumps(
                report.to_dict(),
                sort_keys=True,
            )
        self.assertTrue(report.blocking)
        self.assertEqual(
            report.metadata["preflight_reason_code"],
            "candidate_compile_failed",
        )
        self.assertEqual(
            report.metadata["failed_component"],
            "candidate",
        )
        self.assertEqual(
            report.items[0].owner,
            FeedbackOwner.CANDIDATE,
        )
        self.assertNotIn(str(Path(directory).resolve()), payload)

    def test_stage3_qualification_retains_candidate_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            task = TaskSpec(
                task_id="p4-0b-stage3",
                kernel_path="kernel.cpp",
                kernel_name="candidate_top",
            )
            context = RunContext(
                run_id="p4-0b-stage3",
                task=task,
                budget=BudgetManager(BudgetLimits()),
                trace=TraceRecorder(
                    "p4-0b-stage3",
                    task_id=task.task_id,
                ),
            )
            broken = CANDIDATE.replace("x + 1", "x +")
            handler = PreflightValidationStageHandler(
                PreflightStageInputs(
                    work_dir=directory,
                    testbench_code=TESTBENCH,
                    original_code=ORIGINAL,
                    candidate_code=broken,
                    original_top_function="original_top",
                    candidate_top_function="candidate_top",
                )
            )
            orchestrator = Stage3QualificationOrchestrator(
                {QualificationStage.PREFLIGHT: handler}
            )
            step, terminal = orchestrator._run_handler(
                context,
                SimpleNamespace(
                    qualification_id="p4-0b-q",
                    candidate=SimpleNamespace(
                        candidate_id="cand-2"
                    ),
                ),
                QualificationStage.PREFLIGHT,
            )
        self.assertEqual(terminal, QualificationStatus.REJECTED)
        self.assertEqual(
            step.reason_codes[0],
            "candidate_compile_failed",
        )
        self.assertEqual(
            step.route_action.value,
            "repair_candidate",
        )

    def test_invalid_top_contract_routes_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            result = TestbenchPreflight().compile_and_link(
                work_dir=directory,
                testbench_code=TESTBENCH,
                original_code=ORIGINAL,
                candidate_code=CANDIDATE,
                original_top_function="original_top",
                candidate_top_function="candidate::top",
            )
        self.assertEqual(
            result.reason_code,
            TestbenchPreflightReasonCode.CONFIGURATION_FAILED,
        )
        self.assertEqual(
            result.failure_owner.value,
            "configuration",
        )
        self.assertEqual(
            result.next_action,
            "inspect_configuration",
        )

    def test_total_budget_is_checked_before_first_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = BudgetManager(
                BudgetLimits(
                    max_compile_calls=5,
                    max_tool_calls=100,
                )
            )
            with self.assertRaises(Exception) as captured:
                run_preflight(
                    directory,
                    budget=budget,
                )
            invocation = json.loads(
                (
                    Path(directory)
                    / "testbench_preflight_invocation.json"
                ).read_text(encoding="utf-8")
            )
        self.assertEqual(
            type(captured.exception).__name__,
            "BudgetExceededError",
        )
        self.assertEqual(budget.snapshot().compile_calls, 0)
        self.assertEqual(
            invocation["execution"]["status"],
            "blocked_by_budget",
        )
        self.assertEqual(invocation["substeps"], [])


class P40BReplayToolEntrypointTests(unittest.TestCase):
    def test_cand2_replay_tool_direct_entrypoint(self):
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "replay.json"
            work = root / "work"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(
                        repo_root
                        / "tools"
                        / "p4_0b_preflight_replay.py"
                    ),
                    "--work-dir",
                    str(work),
                    "--output",
                    str(output),
                ],
                cwd=repo_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                msg=completed.stdout,
            )
            payload = json.loads(
                output.read_text(encoding="utf-8")
            )
        self.assertTrue(payload["passed"])
        self.assertEqual(
            payload["reason_code"],
            "candidate_compile_failed",
        )
        self.assertEqual(
            payload["route_action"],
            "repair_candidate",
        )

if __name__ == "__main__":
    unittest.main()
