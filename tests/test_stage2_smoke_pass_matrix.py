from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from agrefactor.config import TargetProfile
from agrefactor.evaluation import ValidationState
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
)
from agrefactor.smoke import (
    STAGE2_SMOKE_CASES,
    Stage2SmokePassMatrixError,
    Stage2SmokePassMatrixRunner,
    expected_stage2_smoke_pass_budget,
)


class _PassingHandlerFactory:
    def __init__(
        self,
        *,
        consume_hidden=True,
        fail_state=None,
        hidden_trace_marker=None,
    ):
        self.consume_hidden = consume_hidden
        self.fail_state = fail_state
        self.hidden_trace_marker = hidden_trace_marker
        self.budget_ids = []
        self.validation_ids = []

    def build(self, request):
        self.validation_ids.append(request.validation_id)

        def handler(state):
            def execute(context):
                self.budget_ids.append(id(context.budget))
                if state is ValidationState.PREFLIGHT:
                    context.budget.consume(
                        tool_calls=1,
                        compile_calls=1,
                    )
                elif state is ValidationState.CSYNTH:
                    context.budget.consume(
                        tool_calls=1,
                        csynth_calls=1,
                    )
                elif (
                    state
                    is ValidationState.PUBLIC_EVALUATION
                ):
                    context.budget.consume(
                        tool_calls=1,
                        csim_calls=1,
                    )
                elif (
                    state
                    is ValidationState.HIDDEN_EVALUATION
                    and self.consume_hidden
                ):
                    context.budget.consume(
                        tool_calls=2,
                        compile_calls=1,
                        csim_calls=1,
                    )

                if (
                    state
                    is ValidationState.HIDDEN_EVALUATION
                    and self.hidden_trace_marker is not None
                ):
                    context.trace.record(
                        "fake.hidden.marker",
                        phase=state.value,
                        message=self.hidden_trace_marker,
                    )

                items = ()
                if state is self.fail_state:
                    items = (
                        FeedbackItem(
                            feedback_id=(
                                f"{request.validation_id}.failure"
                            ),
                            stage=FeedbackStage.COMPILE,
                            category=(
                                FeedbackCategory.SYNTAX_ERROR
                            ),
                            severity=FeedbackSeverity.ERROR,
                            owner=FeedbackOwner.CANDIDATE,
                            summary="Injected deterministic failure",
                            source="fake",
                        ),
                    )

                view = (
                    "operator_full"
                    if state
                    is ValidationState.HIDDEN_EVALUATION
                    else "agent_safe"
                )
                return FeedbackReport(
                    report_id=(
                        f"{request.validation_id}."
                        f"{state.value}"
                    ),
                    source="fake",
                    items=items,
                    metadata={"evidence_view": view},
                )

            return execute

        return {
            state: handler(state)
            for state in (
                ValidationState.PREFLIGHT,
                ValidationState.CSYNTH,
                ValidationState.PUBLIC_EVALUATION,
                ValidationState.HIDDEN_EVALUATION,
            )
        }


class Stage2SmokePassMatrixRunnerTests(unittest.TestCase):
    def _run(
        self,
        *,
        count=2,
        factory=None,
    ):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "matrix"
        resolved_factory = factory or _PassingHandlerFactory()
        runner = Stage2SmokePassMatrixRunner(
            root,
            handler_factory=resolved_factory,
        )
        result = runner.run(
            STAGE2_SMOKE_CASES[:count],
            matrix_id="test-pass-matrix",
        )
        return root, resolved_factory, result

    def test_runner_rejects_blank_work_root(self):
        with self.assertRaises(ValueError):
            Stage2SmokePassMatrixRunner(" ")

    def test_runner_rejects_factory_without_build(self):
        with self.assertRaises(TypeError):
            Stage2SmokePassMatrixRunner(
                "/tmp/stage2-smoke-test",
                handler_factory=object(),
            )

    def test_run_rejects_empty_cases(self):
        with TemporaryDirectory() as temporary:
            runner = Stage2SmokePassMatrixRunner(
                Path(temporary) / "matrix",
                handler_factory=_PassingHandlerFactory(),
            )
            with self.assertRaises(ValueError):
                runner.run(())

    def test_run_rejects_non_case(self):
        with TemporaryDirectory() as temporary:
            runner = Stage2SmokePassMatrixRunner(
                Path(temporary) / "matrix",
                handler_factory=_PassingHandlerFactory(),
            )
            with self.assertRaises(TypeError):
                runner.run((object(),))

    def test_run_rejects_duplicate_case_ids(self):
        case = STAGE2_SMOKE_CASES[0]
        with TemporaryDirectory() as temporary:
            runner = Stage2SmokePassMatrixRunner(
                Path(temporary) / "matrix",
                handler_factory=_PassingHandlerFactory(),
            )
            with self.assertRaises(ValueError):
                runner.run((case, case))

    def test_run_rejects_non_target(self):
        with TemporaryDirectory() as temporary:
            runner = Stage2SmokePassMatrixRunner(
                Path(temporary) / "matrix",
                handler_factory=_PassingHandlerFactory(),
            )
            with self.assertRaises(TypeError):
                runner.run(
                    STAGE2_SMOKE_CASES[:1],
                    target=object(),
                )

    def test_passing_subset_uses_one_shared_budget(self):
        _, factory, result = self._run()
        self.assertTrue(result.accepted)
        self.assertEqual(len(set(factory.budget_ids)), 1)

    def test_passing_subset_has_exact_stage_order(self):
        _, _, result = self._run()
        expected = (
            ValidationState.PREFLIGHT,
            ValidationState.PUBLIC_EVALUATION,
            ValidationState.CSYNTH,
            ValidationState.HIDDEN_EVALUATION,
        )
        for case_result in result.case_results:
            self.assertEqual(
                case_result.completed_stages,
                expected,
            )

    def test_passing_subset_has_exact_per_case_delta(self):
        _, _, result = self._run()
        for case_result in result.case_results:
            self.assertEqual(
                case_result.budget_delta.to_dict(),
                {
                    "tool_calls": 5,
                    "compile_calls": 2,
                    "csynth_calls": 1,
                    "csim_calls": 2,
                    "llm_calls": 0,
                    "tokens": 0,
                    "cost_usd": 0.0,
                },
            )

    def test_passing_subset_has_exact_total_usage(self):
        _, _, result = self._run()
        self.assertEqual(
            result.expected_total_budget.to_dict(),
            {
                "tool_calls": 10,
                "compile_calls": 4,
                "csynth_calls": 2,
                "csim_calls": 4,
                "llm_calls": 0,
                "tokens": 0,
                "cost_usd": 0.0,
            },
        )
        self.assertEqual(result.total_usage.tool_calls, 10)
        self.assertEqual(result.total_usage.compile_calls, 4)
        self.assertEqual(result.total_usage.csynth_calls, 2)
        self.assertEqual(result.total_usage.csim_calls, 4)

    def test_result_is_json_serializable(self):
        _, _, result = self._run()
        encoded = json.dumps(
            result.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
        )
        self.assertIn("test-pass-matrix", encoded)
        self.assertIn("array-map", encoded)

    def test_result_omits_sources_ground_truth_and_secrets(self):
        _, _, result = self._run()
        encoded = json.dumps(
            result.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
        )
        self.assertNotIn("ground_truth", encoded)
        for case in STAGE2_SMOKE_CASES[:2]:
            self.assertNotIn(
                case.candidate_code,
                encoded,
            )
            self.assertNotIn(
                case.hidden_secret_marker,
                encoded,
            )
            self.assertNotIn(
                case.hidden_testbench_code,
                encoded,
            )

    def test_trace_files_are_written(self):
        root, _, result = self._run()
        for case_result in result.case_results:
            self.assertTrue(
                Path(
                    case_result.trace_jsonl_path
                ).is_file()
            )
            self.assertTrue(
                Path(
                    case_result.trace_snapshot_path
                ).is_file()
            )
        self.assertEqual(
            len(tuple((root / "traces").glob("*.jsonl"))),
            2,
        )

    def test_reusing_nonempty_work_root_is_rejected(self):
        root, factory, _ = self._run(count=1)
        runner = Stage2SmokePassMatrixRunner(
            root,
            handler_factory=factory,
        )
        with self.assertRaises(FileExistsError):
            runner.run(STAGE2_SMOKE_CASES[:1])

    def test_nonaccepted_case_raises_and_stops(self):
        factory = _PassingHandlerFactory(
            fail_state=ValidationState.PREFLIGHT
        )
        with TemporaryDirectory() as temporary:
            runner = Stage2SmokePassMatrixRunner(
                Path(temporary) / "matrix",
                handler_factory=factory,
            )
            with self.assertRaises(
                Stage2SmokePassMatrixError
            ) as context:
                runner.run(STAGE2_SMOKE_CASES[:2])
        self.assertEqual(
            context.exception.case_id,
            "array-map",
        )
        self.assertEqual(len(factory.validation_ids), 1)

    def test_budget_delta_mismatch_raises(self):
        factory = _PassingHandlerFactory(
            consume_hidden=False
        )
        with TemporaryDirectory() as temporary:
            runner = Stage2SmokePassMatrixRunner(
                Path(temporary) / "matrix",
                handler_factory=factory,
            )
            with self.assertRaises(
                Stage2SmokePassMatrixError
            ) as context:
                runner.run(STAGE2_SMOKE_CASES[:1])
        self.assertIn(
            "budget delta",
            context.exception.reason,
        )

    def test_hidden_marker_in_trace_raises(self):
        marker = STAGE2_SMOKE_CASES[
            0
        ].hidden_secret_marker
        factory = _PassingHandlerFactory(
            hidden_trace_marker=marker
        )
        with TemporaryDirectory() as temporary:
            runner = Stage2SmokePassMatrixRunner(
                Path(temporary) / "matrix",
                handler_factory=factory,
            )
            with self.assertRaises(
                Stage2SmokePassMatrixError
            ) as context:
                runner.run(STAGE2_SMOKE_CASES[:1])
        self.assertIn(
            "operator-only",
            context.exception.reason,
        )

    def test_expected_budget_sums_all_seven_cases(self):
        expected = expected_stage2_smoke_pass_budget()
        self.assertEqual(
            expected.to_dict(),
            {
                "tool_calls": 35,
                "compile_calls": 14,
                "csynth_calls": 7,
                "csim_calls": 14,
                "llm_calls": 0,
                "tokens": 0,
                "cost_usd": 0.0,
            },
        )

    def test_runner_preserves_case_order(self):
        _, factory, result = self._run(count=3)
        self.assertEqual(
            [
                item.case_id
                for item in result.case_results
            ],
            [
                case.case_id
                for case in STAGE2_SMOKE_CASES[:3]
            ],
        )
        self.assertEqual(
            factory.validation_ids,
            [
                "test-pass-matrix.array-map",
                "test-pass-matrix.reduction",
                "test-pass-matrix.nested-stencil",
            ],
        )

    def test_supplied_target_reaches_every_task(self):
        target = TargetProfile(
            name="test-target",
            toolchain="vitis_hls",
            toolchain_version="2023.2",
            device="xcu200-fsgd2104-2-e",
            clock_period_ns=5.0,
        )

        class TargetFactory(_PassingHandlerFactory):
            def __init__(self):
                super().__init__()
                self.targets = []

            def build(self, request):
                self.targets.append(request.task.target)
                return super().build(request)

        factory = TargetFactory()
        with TemporaryDirectory() as temporary:
            runner = Stage2SmokePassMatrixRunner(
                Path(temporary) / "matrix",
                handler_factory=factory,
            )
            runner.run(
                STAGE2_SMOKE_CASES[:2],
                target=target,
            )
        self.assertEqual(factory.targets, [target, target])

    def test_module_has_no_model_repair_or_legacy_imports(self):
        source = (
            Path(__file__).parents[1]
            / "agrefactor"
            / "smoke"
            / "stage2_pass_matrix.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "CandidateModelAdapter",
            "BoundedCandidateRepairLoop",
            "agrefactor.models",
            "agrefactor.repair",
            "from flow",
            "import flow",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
