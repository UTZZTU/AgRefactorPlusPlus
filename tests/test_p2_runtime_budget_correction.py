from __future__ import annotations

import concurrent.futures
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agrefactor.compat import LegacyRefactorAdapter, LegacyRefactorSettings
from agrefactor.config import TaskSpec
from agrefactor.runtime import (
    BudgetExceededError,
    BudgetLimits,
    BudgetManager,
    RunContext,
    TraceRecorder,
)
from flow.base_agent import HLSAgentLoader
from flow.tools.tb_coverage import measure_coverage


class FakeRunAgent:
    def __init__(self):
        self.calls = 0

    def run(self, *args, **kwargs):
        self.calls += 1
        return object()


class P2RuntimeBudgetCorrectionTests(unittest.TestCase):
    def test_agent_run_is_blocked_before_second_model_launch(self):
        with tempfile.TemporaryDirectory() as raw:
            config = Path(raw) / "agent.yaml"
            config.write_text(
                "agents:\n  worker:\n    name: worker\n    system_message: test\n",
                encoding="utf-8",
            )
            fake = FakeRunAgent()
            budget = BudgetManager(BudgetLimits(max_llm_calls=1))
            with patch("flow.base_agent.ConversableAgent", return_value=fake):
                agent = HLSAgentLoader(config, budget=budget).load_agent("worker")
            agent.run(message="one", max_turns=1)
            with self.assertRaises(BudgetExceededError):
                agent.run(message="two", max_turns=1)
            self.assertEqual(fake.calls, 1)
            self.assertEqual(budget.snapshot().llm_calls, 1)

    def test_budget_manager_concurrent_reservations_are_atomic(self):
        budget = BudgetManager(BudgetLimits(max_llm_calls=5))

        def reserve():
            try:
                budget.consume(llm_calls=1)
                return True
            except BudgetExceededError:
                return False

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            results = list(executor.map(lambda _: reserve(), range(20)))
        self.assertEqual(sum(results), 5)
        self.assertEqual(budget.snapshot().llm_calls, 5)

    def test_coverage_compile_is_blocked_before_subprocess(self):
        budget = BudgetManager(BudgetLimits(max_tool_calls=0, max_compile_calls=0))
        with patch("flow.tools.tb_coverage.subprocess.run") as run:
            with self.assertRaises(BudgetExceededError):
                measure_coverage(
                    "int top(){return 0;}",
                    "int main(){return 0;}",
                    "int top_hls(){return 0;}",
                    budget=budget,
                )
        run.assert_not_called()

    def test_coverage_fallback_compile_requires_second_budget_unit(self):
        first = type("Result", (), {"returncode": 1, "stderr": "missing include"})()
        budget = BudgetManager(BudgetLimits(max_tool_calls=1, max_compile_calls=1))
        with patch("flow.tools.tb_coverage.subprocess.run", return_value=first) as run:
            with self.assertRaises(BudgetExceededError):
                measure_coverage(
                    "int top(){return 0;}",
                    "int main(){return 0;}",
                    "int top_hls(){return 0;}",
                    budget=budget,
                )
        self.assertEqual(run.call_count, 1)
        usage = budget.snapshot()
        self.assertEqual(usage.tool_calls, 1)
        self.assertEqual(usage.compile_calls, 1)

    def test_legacy_posthoc_usage_does_not_double_count_launches(self):
        task = TaskSpec(task_id="budget", kernel_path="kernel.cpp", kernel_name="top")
        budget = BudgetManager(BudgetLimits(max_llm_calls=2))

        def backend(**kwargs):
            kwargs["budget"].consume(llm_calls=1)
            kwargs["budget"].consume(llm_calls=1)
            return True, {"curr_code": "int top_hls(){return 0;}"}

        adapter = LegacyRefactorAdapter(
            LegacyRefactorSettings(generation_only=True),
            backend=backend,
            usage_supplier=lambda: {
                "agents": 1,
                "models": {
                    "model": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    }
                },
                "total_tokens": 15,
                "source": "test",
            },
        )
        result = adapter(
            RunContext(
                run_id="budget",
                task=task,
                budget=budget,
                trace=TraceRecorder("budget", task_id=task.task_id),
            )
        )
        self.assertTrue(result.succeeded)
        usage = budget.snapshot()
        self.assertEqual(usage.llm_calls, 2)
        self.assertEqual(usage.tokens, 15)
        metadata = result.metadata["legacy_usage"]
        self.assertEqual(metadata["precall_budgeted_llm_calls"], 2)
        self.assertTrue(metadata["llm_calls_tracked"])
        self.assertEqual(metadata["launch_accounting"], "precall_shared_budget")

    def test_flow_generation_propagates_budget_to_all_active_consumers(self):
        source = (
            Path(__file__).resolve().parents[1] / "flow" / "new.py"
        ).read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("budget=budget"), 3)
        self.assertIn("llm_config, budget", source)
        self.assertIn("pinned_hls_decl_for_public, budget", source)
        self.assertIn("tools.refactoring.refactor_code", source)

