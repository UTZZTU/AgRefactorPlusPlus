"""Deterministic tests for the bounded S3.4 real smoke acceptance tool."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agrefactor.runtime import BudgetLimits, BudgetManager


_REPO = Path(__file__).resolve().parents[1]
_TOOL_PATH = _REPO / "tools" / "stage3_s34_real_structural_smoke.py"
_SPEC = importlib.util.spec_from_file_location("stage3_s34_real_structural_smoke", _TOOL_PATH)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover - import machinery failure
    raise RuntimeError("unable to load S3.4 real smoke tool")
_TOOL = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_TOOL)


class StructuralSmokeToolTests(unittest.TestCase):
    def test_direct_tool_bootstrap_and_inside_repository_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [sys.executable, str(_TOOL_PATH), "--help"],
                cwd=directory,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("bounded S3.4 real Structural model smoke", completed.stdout)
        path = _TOOL.resolve_source(
            _REPO,
            "tests/fixtures/stage3_s34/structural_smoke_kernel.cpp",
        )
        self.assertTrue(path.is_file())
        self.assertEqual(path.parent.name, "stage3_s34")

    def test_resolve_source_rejects_path_outside_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            outside = Path(directory) / "kernel.cpp"
            outside.write_text("void top() {}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "inside the repository"):
                _TOOL.resolve_source(_REPO, str(outside))

    def test_invoke_one_llm_call_consumes_slot_on_success_and_exception(self) -> None:
        budget = BudgetManager(BudgetLimits(max_llm_calls=2))
        self.assertEqual(_TOOL.invoke_one_llm_call(budget, lambda: "ok"), "ok")
        self.assertEqual(budget.snapshot().llm_calls, 1)

        def fail() -> None:
            raise RuntimeError("transport failed")

        with self.assertRaisesRegex(RuntimeError, "transport failed"):
            _TOOL.invoke_one_llm_call(budget, fail)
        self.assertEqual(budget.snapshot().llm_calls, 2)
        with self.assertRaises(Exception):
            _TOOL.invoke_one_llm_call(budget, lambda: None)
        self.assertEqual(budget.snapshot().llm_calls, 2)

    def test_verify_summary_requires_two_llm_and_zero_tool_calls(self) -> None:
        summary = {
            "budget_usage": {
                "llm_calls": 2,
                "tool_calls": 0,
                "compile_calls": 0,
                "csim_calls": 0,
                "csynth_calls": 0,
            },
            "candidate_semantically_changed": True,
            "top_interface_preserved": True,
            "hidden_evidence_exposed": False,
            "product_optimize_full_enabled": False,
        }
        _TOOL.verify_summary(summary)
        summary["budget_usage"]["tool_calls"] = 1
        with self.assertRaisesRegex(AssertionError, "tool_calls"):
            _TOOL.verify_summary(summary)


if __name__ == "__main__":
    unittest.main()
