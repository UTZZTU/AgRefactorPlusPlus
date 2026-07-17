import json
from pathlib import Path
import tempfile
import unittest

from agrefactor.config import (
    EvaluationSplit,
    TaskSpec,
    TestSuiteSpec,
)
from agrefactor.evaluation import (
    CsimSuiteEvaluator,
    ValidationFeedbackCoordinator,
    ValidationState,
)
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackOwner,
)
from agrefactor.runtime import (
    BudgetExceededError,
    BudgetLimits,
    BudgetManager,
    CsimStageInputs,
    CsimValidationStageHandler,
    RunContext,
    TraceRecorder,
    read_csim_invocation_summary,
)


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


def make_context(suites):
    task = TaskSpec(
        task_id="csim-stage-task",
        kernel_path="candidate_top.cpp",
        kernel_name="candidate_top",
        test_suites=tuple(suites),
    )
    return RunContext(
        run_id="csim-stage-run",
        task=task,
        budget=BudgetManager(BudgetLimits()),
        trace=TraceRecorder(
            "csim-stage-run",
            task_id=task.task_id,
        ),
    )


def write_invocation(
    work_dir,
    *,
    budget_status="consumed",
    compile_status="completed",
    compile_returncode=0,
    simulation_status="completed",
    simulation_returncode=0,
):
    root = Path(work_dir)
    root.mkdir(parents=True, exist_ok=True)
    (
        root / "csim_invocation.json"
    ).write_text(
        json.dumps(
            {
                "budget": {
                    "status": budget_status,
                    "checkpoint": "before_csim_launch",
                },
                "compile_execution": {
                    "status": compile_status,
                    "returncode": compile_returncode,
                    "timeout": False,
                },
                "simulation_execution": {
                    "status": simulation_status,
                    "returncode": simulation_returncode,
                    "timeout": False,
                },
                "compile_command": "/private/g++",
                "simulation_command": "/private/csim",
                "work_dir": str(root.resolve()),
            }
        ),
        encoding="utf-8",
    )


class ScriptedExecutor:
    def __init__(self):
        self.calls = []

    def __call__(
        self,
        work_dir,
        variables,
        timelimit,
        *,
        budget=None,
    ):
        code = variables["testbench"]
        self.calls.append(
            {
                "work_dir": work_dir,
                "testbench": code,
                "budget": budget,
                "timelimit": timelimit,
            }
        )

        if "BUDGET_BLOCK" in code:
            root = Path(work_dir)
            root.mkdir(parents=True, exist_ok=True)
            (
                root / "csim_invocation.json"
            ).write_text(
                json.dumps(
                    {
                        "budget": {
                            "status": "blocked",
                            "checkpoint": (
                                "before_csim_plan"
                            ),
                            "resource": "csim_calls",
                            "limit": 0,
                            "attempted": 1,
                        },
                        "compile_execution": {
                            "status": (
                                "blocked_by_budget"
                            ),
                            "returncode": None,
                            "timeout": False,
                        },
                        "simulation_execution": {
                            "status": (
                                "blocked_by_budget"
                            ),
                            "returncode": None,
                            "timeout": False,
                        },
                    }
                ),
                encoding="utf-8",
            )
            raise BudgetExceededError(
                "csim_calls",
                0,
                1,
            )

        if "FAIL_PUBLIC" in code:
            write_invocation(
                work_dir,
                simulation_returncode=1,
            )
            return (
                "csim_failed",
                (
                    f"{Path(work_dir).resolve()}/"
                    "PUBLIC_MISMATCH"
                ),
            )

        if "FAIL_HIDDEN" in code:
            write_invocation(
                work_dir,
                simulation_returncode=1,
            )
            return (
                "csim_failed",
                "HIDDEN_SECRET_DIAGNOSTIC",
            )

        write_invocation(work_dir)
        return "succeeded", ""


class CsimValidationStageHandlerTests(
    unittest.TestCase
):
    def inputs(self, directory, codes):
        return CsimStageInputs(
            work_dir=directory,
            original_code=ORIGINAL,
            candidate_code=CANDIDATE,
            suite_testbench_codes=codes,
            timelimit=23,
        )

    def test_public_collects_all_suites_and_routes_candidate(
        self,
    ):
        suites = (
            TestSuiteSpec(
                suite_id="public-a",
                split=EvaluationSplit.PUBLIC,
            ),
            TestSuiteSpec(
                suite_id="public-b",
                split=EvaluationSplit.PUBLIC,
            ),
        )
        executor = ScriptedExecutor()
        with tempfile.TemporaryDirectory() as directory:
            context = make_context(suites)
            report = CsimValidationStageHandler(
                self.inputs(
                    directory,
                    {
                        "public-a": "FAIL_PUBLIC",
                        "public-b": "PASS_PUBLIC",
                    },
                ),
                split=EvaluationSplit.PUBLIC,
                evaluator=CsimSuiteEvaluator(
                    executor=executor
                ),
            )(context)

        self.assertEqual(len(executor.calls), 2)
        self.assertEqual(
            report.metadata["attempted_suite_ids"],
            ["public-a", "public-b"],
        )
        self.assertEqual(
            report.items[0].category,
            FeedbackCategory.FUNCTIONAL_MISMATCH,
        )
        self.assertEqual(
            report.items[0].owner,
            FeedbackOwner.CANDIDATE,
        )
        coordinated = ValidationFeedbackCoordinator(
            context.task
        ).coordinate(
            report,
            ValidationState.PUBLIC_EVALUATION,
            coordination_id="public-step",
        )
        self.assertEqual(
            coordinated.transition.next_state,
            ValidationState.REPAIR_PENDING,
        )

    def test_hidden_fails_fast_and_stays_operator_full(
        self,
    ):
        suites = (
            TestSuiteSpec(
                suite_id="hidden-a",
                split=EvaluationSplit.HIDDEN,
            ),
            TestSuiteSpec(
                suite_id="hidden-b",
                split=EvaluationSplit.HIDDEN,
            ),
        )
        executor = ScriptedExecutor()
        with tempfile.TemporaryDirectory() as directory:
            context = make_context(suites)
            report = CsimValidationStageHandler(
                self.inputs(
                    directory,
                    {
                        "hidden-a": "FAIL_HIDDEN",
                        "hidden-b": "PASS_HIDDEN",
                    },
                ),
                split=EvaluationSplit.HIDDEN,
                evaluator=CsimSuiteEvaluator(
                    executor=executor
                ),
            )(context)

        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(
            report.metadata["evidence_view"],
            "operator_full",
        )
        self.assertTrue(
            report.metadata["stopped_early"]
        )
        self.assertEqual(
            report.metadata["stop_reason"],
            "hidden_blocking_result",
        )
        self.assertIn(
            "HIDDEN_SECRET_DIAGNOSTIC",
            json.dumps(report.to_dict()),
        )

    def test_budget_exception_is_normalized_and_stops(
        self,
    ):
        suites = (
            TestSuiteSpec(
                suite_id="public-a",
                split=EvaluationSplit.PUBLIC,
            ),
            TestSuiteSpec(
                suite_id="public-b",
                split=EvaluationSplit.PUBLIC,
            ),
        )
        executor = ScriptedExecutor()
        with tempfile.TemporaryDirectory() as directory:
            report = CsimValidationStageHandler(
                self.inputs(
                    directory,
                    {
                        "public-a": "BUDGET_BLOCK",
                        "public-b": "PASS_PUBLIC",
                    },
                ),
                split=EvaluationSplit.PUBLIC,
                evaluator=CsimSuiteEvaluator(
                    executor=executor
                ),
            )(make_context(suites))

        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(
            report.items[0].category,
            FeedbackCategory.BUDGET_EXHAUSTED,
        )
        self.assertEqual(
            report.items[0].owner,
            FeedbackOwner.EVALUATOR,
        )
        self.assertEqual(
            report.metadata["stop_reason"],
            "budget_exhausted",
        )

    def test_public_report_removes_operator_paths(self):
        suite = TestSuiteSpec(
            suite_id="public-a",
            split=EvaluationSplit.PUBLIC,
            testbench_path="/private/public.cpp",
        )
        executor = ScriptedExecutor()
        with tempfile.TemporaryDirectory() as directory:
            report = CsimValidationStageHandler(
                self.inputs(
                    directory,
                    {"public-a": "FAIL_PUBLIC"},
                ),
                split=EvaluationSplit.PUBLIC,
                evaluator=CsimSuiteEvaluator(
                    executor=executor
                ),
            )(make_context((suite,)))
            encoded = json.dumps(
                report.to_dict(),
                sort_keys=True,
            )

            self.assertNotIn(
                str(Path(directory).resolve()),
                encoded,
            )
            self.assertNotIn(
                "/private/public.cpp",
                encoded,
            )
            self.assertNotIn(
                "csim_invocation.json",
                encoded,
            )
            self.assertNotIn(
                '"artifacts"',
                encoded,
            )
            self.assertTrue(
                all(
                    item.evidence_ref is None
                    for item in report.items
                )
            )

    def test_hidden_coordination_suppresses_secret(self):
        suite = TestSuiteSpec(
            suite_id="hidden-a",
            split=EvaluationSplit.HIDDEN,
        )
        executor = ScriptedExecutor()
        with tempfile.TemporaryDirectory() as directory:
            context = make_context((suite,))
            report = CsimValidationStageHandler(
                self.inputs(
                    directory,
                    {"hidden-a": "FAIL_HIDDEN"},
                ),
                split=EvaluationSplit.HIDDEN,
                evaluator=CsimSuiteEvaluator(
                    executor=executor
                ),
            )(context)
            coordinated = (
                ValidationFeedbackCoordinator(
                    context.task
                ).coordinate(
                    report,
                    ValidationState.HIDDEN_EVALUATION,
                    coordination_id="hidden-step",
                )
            )
            safe = json.dumps(
                coordinated.to_dict()
            )

        self.assertNotIn(
            "HIDDEN_SECRET_DIAGNOSTIC",
            safe,
        )
        self.assertEqual(
            coordinated.selected_feedback_items,
            (),
        )
        self.assertEqual(
            coordinated.transition.next_state,
            ValidationState.REJECTED,
        )

    def test_missing_code_fails_before_execution(self):
        suite = TestSuiteSpec(
            suite_id="public-a",
            split=EvaluationSplit.PUBLIC,
        )
        executor = ScriptedExecutor()
        with tempfile.TemporaryDirectory() as directory:
            handler = CsimValidationStageHandler(
                self.inputs(directory, {}),
                split=EvaluationSplit.PUBLIC,
                evaluator=CsimSuiteEvaluator(
                    executor=executor
                ),
            )
            with self.assertRaises(ValueError):
                handler(make_context((suite,)))

        self.assertEqual(executor.calls, [])

    def test_exact_budget_instance_is_forwarded(self):
        suite = TestSuiteSpec(
            suite_id="public-a",
            split=EvaluationSplit.PUBLIC,
        )
        executor = ScriptedExecutor()
        with tempfile.TemporaryDirectory() as directory:
            context = make_context((suite,))
            CsimValidationStageHandler(
                self.inputs(
                    directory,
                    {"public-a": "PASS_PUBLIC"},
                ),
                split=EvaluationSplit.PUBLIC,
                evaluator=CsimSuiteEvaluator(
                    executor=executor
                ),
            )(context)

        self.assertIs(
            executor.calls[0]["budget"],
            context.budget,
        )
        self.assertEqual(
            executor.calls[0]["timelimit"],
            23,
        )

    def test_invocation_summary_is_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            write_invocation(directory)
            summary = read_csim_invocation_summary(
                directory
            )
            encoded = json.dumps(summary)

        self.assertEqual(
            summary["budget"]["status"],
            "consumed",
        )
        self.assertNotIn("/private", encoded)
        self.assertNotIn("work_dir", encoded)

    def test_invalid_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            CsimStageInputs(
                work_dir="",
                original_code=ORIGINAL,
                candidate_code=CANDIDATE,
                suite_testbench_codes={"a": "code"},
            )
        with self.assertRaises(ValueError):
            CsimStageInputs(
                work_dir="/tmp/work",
                original_code=ORIGINAL,
                candidate_code=CANDIDATE,
                suite_testbench_codes={"a": ""},
            )
        with self.assertRaises(ValueError):
            CsimStageInputs(
                work_dir="/tmp/work",
                original_code=ORIGINAL,
                candidate_code=CANDIDATE,
                suite_testbench_codes={"a": "code"},
                timelimit=0,
            )


    def test_suite_work_directory_exists_before_executor(
        self,
    ):
        suite = TestSuiteSpec(
            suite_id="public-a",
            split=EvaluationSplit.PUBLIC,
        )
        observed = []

        def executor(
            work_dir,
            variables,
            timelimit,
            *,
            budget=None,
        ):
            directory = Path(work_dir)
            self.assertTrue(directory.is_dir())
            observed.append(directory)
            write_invocation(directory)
            return "succeeded", ""

        with tempfile.TemporaryDirectory() as directory:
            context = make_context((suite,))
            CsimValidationStageHandler(
                self.inputs(
                    directory,
                    {"public-a": "PASS_PUBLIC"},
                ),
                split=EvaluationSplit.PUBLIC,
                evaluator=CsimSuiteEvaluator(
                    executor=executor
                ),
            )(context)

            self.assertEqual(
                observed,
                [
                    Path(directory)
                    / "public"
                    / "suite_001"
                ],
            )


if __name__ == "__main__":
    unittest.main()
