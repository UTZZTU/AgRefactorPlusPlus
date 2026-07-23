import sys
import tempfile
import unittest
from pathlib import Path

from agrefactor.compat import (
    LegacyRefactorAdapter,
    LegacyRefactorSettings,
    build_legacy_refactor_kwargs,
)
from agrefactor.config import RunMode, TargetProfile, TaskSpec
from agrefactor.runtime import (
    BudgetManager,
    PhaseStatus,
    RunContext,
    TraceRecorder,
)


class LegacyRefactorAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = TargetProfile(
            name="vitis-2023.2-default",
            toolchain="vitis_hls",
            toolchain_version="2023.2",
        )
        self.task = TaskSpec(
            task_id="dfs-refactor",
            kernel_path="src/heterorefactor/dfs/kernel.cpp",
            kernel_name="process_top",
            target=self.target,
            mode=RunMode.REFACTOR,
        )

    def make_context(self, task: TaskSpec | None = None) -> RunContext:
        return RunContext(
            run_id="adapter-test",
            task=task or self.task,
            budget=BudgetManager(),
            trace=TraceRecorder("adapter-test"),
        )

    def test_builds_legacy_keyword_arguments(self) -> None:
        settings = LegacyRefactorSettings(
            model="deepseek-v4-flash",
            reasoning_effort="low",
            base_url="https://api.deepseek.com",
            enable_testbench_repair=True,
            max_testbench_repair_attempts=2,
            testbench_repair_model="deepseek-chat",
            testbench_repair_api_key_env="DEEPSEEK_API_KEY",
            debug=True,
        )

        kwargs = build_legacy_refactor_kwargs(self.task, settings)

        self.assertEqual(kwargs["kernel_name"], "process_top")
        self.assertEqual(
            kwargs["target_profile"],
            self.target.to_dict(),
        )
        self.assertEqual(kwargs["model"], "deepseek-v4-flash")
        self.assertEqual(kwargs["reasoning_effort"], "low")
        self.assertEqual(kwargs["debug"], 1)
        self.assertTrue(kwargs["enable_testbench_repair"])
        self.assertEqual(
            kwargs["max_testbench_repair_attempts"],
            2,
        )
        self.assertEqual(
            kwargs["testbench_repair_model"],
            "deepseek-chat",
        )
        self.assertEqual(
            kwargs["testbench_repair_api_key_env"],
            "DEEPSEEK_API_KEY",
        )
        self.assertIsNone(kwargs["external_testbench"])

    def test_rejects_enabled_repair_without_model(self) -> None:
        with self.assertRaises(ValueError):
            LegacyRefactorSettings(
                enable_testbench_repair=True,
            )

    def test_rejects_remote_testbench_repair(self) -> None:
        with self.assertRaises(ValueError):
            LegacyRefactorSettings(
                model="deepseek-chat",
                enable_testbench_repair=True,
                remote=True,
            )

    def test_reads_external_testbench_from_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tb_path = Path(directory) / "tb.cpp"
            tb_path.write_text("int main() { return 0; }\n", encoding="utf-8")
            task = TaskSpec(
                task_id="dfs-external-tb",
                kernel_path=self.task.kernel_path,
                kernel_name=self.task.kernel_name,
                target=self.target,
                mode=RunMode.REFACTOR,
                testbench_path=str(tb_path),
            )

            kwargs = build_legacy_refactor_kwargs(
                task,
                LegacyRefactorSettings(),
            )

            self.assertEqual(
                kwargs["external_testbench"],
                "int main() { return 0; }\n",
            )

    def test_adapter_converts_success_result(self) -> None:
        captured = {}

        def backend(**kwargs):
            captured.update(kwargs)
            return True, {"unused": "payload"}

        adapter = LegacyRefactorAdapter(
            LegacyRefactorSettings(model="deepseek-v4-flash"),
            backend=backend,
        )

        result = adapter(self.make_context())

        self.assertEqual(result.status, PhaseStatus.SUCCEEDED)
        self.assertEqual(captured["kernel_path"], self.task.kernel_path)
        self.assertEqual(
            captured["target_profile"],
            self.target.to_dict(),
        )

    def test_adapter_converts_failure_result(self) -> None:
        adapter = LegacyRefactorAdapter(
            backend=lambda **kwargs: (False, None)
        )

        result = adapter(self.make_context())

        self.assertEqual(result.status, PhaseStatus.FAILED)

    def test_rejects_invalid_backend_result(self) -> None:
        adapter = LegacyRefactorAdapter(
            backend=lambda **kwargs: "unexpected"
        )

        with self.assertRaises(TypeError):
            adapter(self.make_context())

    def test_records_legacy_token_and_cost_usage(self) -> None:
        context = self.make_context()
        adapter = LegacyRefactorAdapter(
            backend=lambda **kwargs: (True, None),
            usage_supplier=lambda: {
                "agents": 4,
                "models": {
                    "deepseek-v4-flash": {
                        "prompt_tokens": 900,
                        "completion_tokens": 100,
                        "total_tokens": 1000,
                        "cost": 0.000154,
                    }
                },
                "total_tokens": 1000,
                "total_cost": 0.000154,
                "source": "test-summary",
            },
        )

        result = adapter(context)
        usage = context.budget.snapshot()

        self.assertEqual(usage.tokens, 1000)
        self.assertEqual(usage.cost_usd, 0.0)
        self.assertEqual(
            result.metadata["legacy_usage"]["accounting_mode"],
            "native_post_hoc",
        )
        self.assertFalse(
            result.metadata["legacy_usage"]["llm_calls_tracked"]
        )
        self.assertIn(
            "legacy_refactor.usage_recorded",
            [event.event for event in context.trace.events],
        )

    def test_merges_repair_usage_without_double_counting_history(
        self,
    ) -> None:
        context = self.make_context()
        repair = {
            "artifact_path": "/tmp/testbench_repair.json",
            "model_usage": {
                "calls": 2,
                "prompt_tokens": 40,
                "completion_tokens": 10,
                "total_tokens": 50,
                "cost_usd": None,
                "models": [
                    "deepseek-v4-flash",
                    "deepseek-v4-flash",
                ],
            },
        }

        adapter = LegacyRefactorAdapter(
            backend=lambda **kwargs: (
                True,
                {
                    "testbench_repair": repair,
                    "csynth_csim_history": [
                        {"testbench_repair": repair},
                    ],
                },
            ),
            usage_supplier=lambda: {
                "agents": 4,
                "models": {
                    "deepseek-v4-flash": {
                        "prompt_tokens": 900,
                        "completion_tokens": 100,
                        "total_tokens": 1000,
                        "cost": 0.000154,
                    }
                },
                "total_tokens": 1000,
                "total_cost": 0.000154,
                "source": "test-summary",
            },
        )

        result = adapter(context)
        usage = context.budget.snapshot()
        metadata = result.metadata["legacy_usage"]

        self.assertEqual(usage.tokens, 1050)
        self.assertEqual(usage.llm_calls, 2)
        self.assertEqual(usage.cost_usd, 0.0)
        self.assertEqual(
            metadata["accounting_mode"],
            "native_combined",
        )
        self.assertEqual(metadata["known_llm_calls"], 2)
        self.assertFalse(metadata["llm_calls_complete"])
        self.assertFalse(metadata["cost_complete"])
        self.assertEqual(metadata["unknown_cost_calls"], 2)
        self.assertEqual(
            metadata["testbench_repair_usage"]["total_tokens"],
            50,
        )
        self.assertEqual(
            metadata["testbench_repair_usage"]["artifacts"],
            ["/tmp/testbench_repair.json"],
        )

    def test_merges_distinct_repair_artifacts_once_each(self) -> None:
        context = self.make_context()
        first = {
            "artifact_path": "/tmp/repair-1.json",
            "model_usage": {
                "calls": 1,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "cost_usd": 0.0001,
                "models": ["repair-model"],
            },
        }
        second = {
            "artifact_path": "/tmp/repair-2.json",
            "model_usage": {
                "calls": 1,
                "prompt_tokens": 20,
                "completion_tokens": 5,
                "total_tokens": 25,
                "cost_usd": 0.0002,
                "models": ["repair-model"],
            },
        }

        adapter = LegacyRefactorAdapter(
            backend=lambda **kwargs: (
                True,
                {
                    "testbench_repair": second,
                    "csynth_csim_history": [
                        {"testbench_repair": first},
                        {"testbench_repair": second},
                    ],
                },
            ),
            usage_supplier=lambda: {
                "agents": 1,
                "models": {},
                "total_tokens": 100,
                "total_cost": 0.001,
                "source": "test-summary",
            },
        )

        result = adapter(context)
        usage = context.budget.snapshot()
        metadata = result.metadata["legacy_usage"]

        self.assertEqual(usage.tokens, 140)
        self.assertEqual(usage.llm_calls, 2)
        self.assertAlmostEqual(usage.cost_usd, 0.0003)
        self.assertFalse(metadata["cost_complete"])
        self.assertEqual(
            metadata["testbench_repair_usage"]["artifacts"],
            ["/tmp/repair-2.json", "/tmp/repair-1.json"],
        )

    def test_usage_supplier_failure_does_not_hide_success(self) -> None:
        context = self.make_context()

        def fail_usage():
            raise RuntimeError("usage unavailable")

        adapter = LegacyRefactorAdapter(
            backend=lambda **kwargs: (True, None),
            usage_supplier=fail_usage,
        )

        result = adapter(context)

        self.assertEqual(result.status, PhaseStatus.SUCCEEDED)
        self.assertEqual(
            result.metadata["legacy_usage"]["accounting_mode"],
            "unavailable",
        )
        self.assertIn(
            "legacy_refactor.usage_unavailable",
            [event.event for event in context.trace.events],
        )

    def test_records_partial_usage_when_backend_raises(self) -> None:
        context = self.make_context()

        def backend(**kwargs):
            raise RuntimeError("provider unavailable")

        adapter = LegacyRefactorAdapter(
            backend=backend,
            usage_supplier=lambda: {
                "agents": 1,
                "models": {
                    "deepseek-v4-flash": {
                        "prompt_tokens": 300,
                        "completion_tokens": 20,
                        "total_tokens": 320,
                        "cost": 0.0000476,
                    }
                },
                "total_tokens": 320,
                "total_cost": 0.0000476,
                "source": "partial-test-summary",
            },
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "provider unavailable",
        ):
            adapter(context)

        usage = context.budget.snapshot()
        self.assertEqual(usage.tokens, 320)
        self.assertEqual(usage.cost_usd, 0.0)

        events = [event.event for event in context.trace.events]
        self.assertIn("legacy_refactor.usage_recorded", events)
        self.assertIn("legacy_refactor.errored", events)

    def test_usage_failure_does_not_mask_backend_error(self) -> None:
        context = self.make_context()

        def backend(**kwargs):
            raise RuntimeError("original backend error")

        def usage_supplier():
            raise ValueError("usage supplier error")

        adapter = LegacyRefactorAdapter(
            backend=backend,
            usage_supplier=usage_supplier,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "original backend error",
        ):
            adapter(context)

        events = [event.event for event in context.trace.events]
        self.assertIn("legacy_refactor.usage_unavailable", events)
        self.assertIn("legacy_refactor.errored", events)

    def test_restores_streams_after_legacy_redirection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "legacy-output.txt"
            original_stdout = sys.stdout
            original_stderr = sys.stderr
            redirected = None

            def backend(**kwargs):
                nonlocal redirected
                redirected = log_path.open("w", encoding="utf-8")
                sys.stdout = redirected
                sys.stderr = redirected
                print("legacy log line")
                return True, None

            adapter = LegacyRefactorAdapter(backend=backend)
            result = adapter(self.make_context())

            self.assertEqual(result.status, PhaseStatus.SUCCEEDED)
            self.assertIs(sys.stdout, original_stdout)
            self.assertIs(sys.stderr, original_stderr)
            self.assertIsNotNone(redirected)
            self.assertTrue(redirected.closed)
            self.assertIn(
                "legacy log line",
                log_path.read_text(encoding="utf-8"),
            )

    def test_restores_streams_when_legacy_backend_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "legacy-error.txt"
            original_stdout = sys.stdout
            original_stderr = sys.stderr
            redirected = None

            def backend(**kwargs):
                nonlocal redirected
                redirected = log_path.open("w", encoding="utf-8")
                sys.stdout = redirected
                sys.stderr = redirected
                print("before failure")
                raise RuntimeError("legacy failure")

            adapter = LegacyRefactorAdapter(backend=backend)

            with self.assertRaisesRegex(RuntimeError, "legacy failure"):
                adapter(self.make_context())

            self.assertIs(sys.stdout, original_stdout)
            self.assertIs(sys.stderr, original_stderr)
            self.assertIsNotNone(redirected)
            self.assertTrue(redirected.closed)
            self.assertIn(
                "before failure",
                log_path.read_text(encoding="utf-8"),
            )

    def test_accepts_zero_retry_limit_for_single_attempt(self) -> None:
        settings = LegacyRefactorSettings(max_retry_attempts=0)
        self.assertEqual(settings.max_retry_attempts, 0)

    def test_rejects_negative_retry_limit(self) -> None:
        with self.assertRaises(ValueError):
            LegacyRefactorSettings(max_retry_attempts=-1)


if __name__ == "__main__":
    unittest.main()
