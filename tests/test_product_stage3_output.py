from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from agrefactor.config import RunMode
from agrefactor.product.run_output import (
    build_product_summary,
    capture_product_streams,
    finalize_product_artifacts,
)
from agrefactor.runtime import (
    BudgetUsage,
    PhaseResult,
    PhaseStatus,
    RunPhase,
    RunResult,
    RunStatus,
)


def _root_identity(root: Path) -> None:
    (root / "execution_identity.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "run-s37",
                "execution_id": "exec-upstream",
                "request_identity_sha256": "1" * 64,
                "cache_identity_sha256": "2" * 64,
                "bundle_sha256": "3" * 64,
                "source": {"top_function": "kernel_top"},
                "normalized_task": {"value": {"mode": "optimize"}},
                "model": {
                    "value": {"model_id": "deepseek-v4-flash"},
                    "pricing": {
                        "cost_estimation_quality": "unavailable",
                        "actual_estimation": {
                            "quality": "unavailable",
                            "amounts_by_currency": {},
                            "is_invoice": False,
                        },
                    },
                },
                "prompt_identity": {},
                "suites": [
                    {"suite_id": "public", "split": "public"},
                    {"suite_id": "hidden", "split": "hidden"},
                ],
                "budget": {
                    "contract": {
                        "system_defaults": {},
                        "system_safety_ceilings": {},
                        "user_requested": {},
                        "effective_hard_limits": {},
                        "soft_usage_budgets": {},
                    },
                    "usage": None,
                    "remaining_hard_budget": {},
                    "hard_budget_exhaustion": None,
                    "soft_budget_exceeded": {},
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "run_artifact_manifest.json").write_text(
        json.dumps({"schema_version": 1, "files": []}), encoding="utf-8"
    )


def _stage3_identity(root: Path) -> None:
    candidate = root / "optimize" / "final_candidate.cpp"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text("void kernel_top() {}\n", encoding="utf-8")
    (root / "stage3_execution_identity.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "terminal_status": "accepted_no_improvement",
                "identity_sha256": "4" * 64,
                "state": {
                    "best_correct_candidate_id": "baseline",
                    "best_ppa_candidate_id": "baseline",
                    "executed_candidate_count": 3,
                },
                "final_candidate": {"path": str(candidate), "sha256": "5" * 64},
                "budget_usage": {
                    "llm_calls": 6,
                    "tool_calls": 24,
                    "compile_calls": 12,
                    "csim_calls": 8,
                    "csynth_calls": 4,
                    "tokens": 12345,
                    "cost_usd": 0.0,
                    "elapsed_s": 99.0,
                    "costs_by_currency": {"CNY": "0.50"},
                },
                "model_calls": {
                    "record_count": 6,
                    "pricing": {
                        "cost_estimation_quality": "verified",
                        "actual_estimation": {
                            "quality": "verified",
                            "amounts_by_currency": {"CNY": "0.50"},
                            "is_invoice": False,
                        },
                        "is_invoice": False,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    model_root = root / "optimize" / "model"
    model_root.mkdir(parents=True, exist_ok=True)
    kinds = (
        "structural_hypothesis",
        "structural_rewrite",
        "bottleneck_analysis",
        "bottleneck_rewrite",
        "pragma_analysis",
        "pragma_rewrite",
    )
    records = [
        {
            "schema_version": 1,
            "sequence": sequence,
            "call_kind": kind,
            "prompt_identity_sha256": str(sequence) * 64,
            "response_sha256": "a" * 64,
            "response_valid": True,
            "usage": {"completion_tokens": 10},
            "finish_reason": "stop",
        }
        for sequence, kind in enumerate(kinds, 1)
    ]
    (model_root / "model_calls.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in records), encoding="utf-8"
    )


def _result() -> RunResult:
    return RunResult(
        run_id="run-s37",
        task_id="task-s37",
        mode=RunMode.OPTIMIZE,
        status=RunStatus.SUCCEEDED,
        phases=(
            PhaseResult(
                phase=RunPhase.OPTIMIZE,
                status=PhaseStatus.SUCCEEDED,
                summary="Stage 3 safe-v1 finished",
                metadata={"accepted": True, "repair_attempt_count": 0},
            ),
        ),
        budget_usage=BudgetUsage(
            llm_calls=6,
            tool_calls=24,
            compile_calls=12,
            csim_calls=8,
            csynth_calls=4,
            tokens=12345,
            cost_usd=0.0,
            elapsed_s=99.0,
            costs_by_currency={"CNY": Decimal("0.50")},
        ),
        metadata={},
    )


class ProductStage3OutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_optimize_summary_uses_stage3_identity_and_qualification(self) -> None:
        _root_identity(self.root)
        _stage3_identity(self.root)
        summary = build_product_summary(_result(), artifact_root=self.root)
        self.assertEqual(summary["status"], "accepted")
        self.assertEqual(
            summary["validation"],
            {"csynth": "passed", "public": "passed", "hidden": "passed"},
        )
        self.assertEqual(summary["usage"]["tokens"], 12345)
        self.assertEqual(summary["optimizer"]["terminal_status"], "accepted_no_improvement")
        self.assertTrue(summary["candidate"].endswith("optimize/final_candidate.cpp"))
        self.assertEqual(summary["cost_estimation_quality"], "verified")

    def test_finalize_merges_safe_optimizer_model_call_records(self) -> None:
        root = self.root / "artifacts"
        work = self.root / "work"
        root.mkdir()
        work.mkdir()
        _root_identity(root)
        _stage3_identity(root)
        with capture_product_streams(work) as captured:
            pass
        finalize_product_artifacts(
            _result(), artifact_root=root, work_root=work, captured=captured
        )
        payload = json.loads((root / "model_calls.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["optimizer_call_count"], 6)
        self.assertEqual(
            [item["sequence"] for item in payload["optimizer_calls"]],
            list(range(1, 7)),
        )
        encoded = json.dumps(payload)
        self.assertNotIn("raw_prompt", encoded)
        self.assertNotIn("reasoning_content", encoded)
        self.assertIs(payload["plaintext_prompts_persisted"], False)
        self.assertIs(payload["plaintext_responses_persisted"], False)
        self.assertEqual(payload["pricing"]["cost_estimation_quality"], "verified")


if __name__ == "__main__":
    unittest.main()
