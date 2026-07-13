import unittest

from agrefactor.config import TargetProfile
from agrefactor.evaluation import (
    EvaluationRequest,
    EvaluationResult,
    EvaluationStatus,
    Evaluator,
)


class DummyEvaluator(Evaluator):
    @property
    def name(self) -> str:
        return "dummy"

    def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        return EvaluationResult(
            evaluator=self.name,
            status=EvaluationStatus.PASSED,
            summary=f"Checked {request.kernel_name}",
            return_code=0,
            metrics={"latency_cycles": 42},
        )


class EvaluatorBaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = TargetProfile(
            name="vitis-2023.2-default",
            toolchain="vitis_hls",
            toolchain_version="2023.2",
        )

    def test_request_normalizes_fields(self) -> None:
        request = EvaluationRequest(
            task_id=" dfs ",
            kernel_path=" kernel.cpp ",
            kernel_name=" process_top ",
            target=self.target,
            work_dir=" /tmp/run ",
            options={"mode": "csim"},
        )

        self.assertEqual(request.task_id, "dfs")
        self.assertEqual(request.kernel_name, "process_top")
        self.assertEqual(request.options, {"mode": "csim"})

    def test_dummy_evaluator_returns_normalized_result(self) -> None:
        request = EvaluationRequest(
            task_id="dfs",
            kernel_path="kernel.cpp",
            kernel_name="process_top",
            target=self.target,
            work_dir="/tmp/run",
        )

        result = DummyEvaluator().evaluate(request)

        self.assertTrue(result.succeeded)
        self.assertEqual(result.evaluator, "dummy")
        self.assertEqual(result.metrics["latency_cycles"], 42.0)

    def test_result_accepts_status_string(self) -> None:
        result = EvaluationResult(
            evaluator="dummy",
            status="failed",
            diagnostics=("compile error",),
        )

        self.assertEqual(result.status, EvaluationStatus.FAILED)
        self.assertFalse(result.succeeded)

    def test_rejects_non_serializable_options(self) -> None:
        with self.assertRaises(TypeError):
            EvaluationRequest(
                task_id="dfs",
                kernel_path="kernel.cpp",
                kernel_name="process_top",
                target=self.target,
                work_dir="/tmp/run",
                options={"bad": object()},
            )

    def test_rejects_non_finite_metric(self) -> None:
        with self.assertRaises(ValueError):
            EvaluationResult(
                evaluator="dummy",
                status=EvaluationStatus.PASSED,
                metrics={"latency": float("inf")},
            )


if __name__ == "__main__":
    unittest.main()
