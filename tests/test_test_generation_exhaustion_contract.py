from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agrefactor.config import RunMode, TaskSpec, resolve_target_profile
from agrefactor.models import resolve_model_runtime
from agrefactor.product import (
    SourceBootstrapPhase,
    SourceBootstrapRequest,
    SourceRunLayout,
    build_test_source_plan,
)
from agrefactor.runtime import (
    BudgetManager,
    PhaseResult,
    PhaseStatus,
    RunContext,
    RunPhase,
    RunStatus,
    TraceRecorder,
    UnifiedRunner,
)
from agrefactor.runtime.budget_profile import (
    DEFAULT_SOURCE_RUN_BUDGET_PROFILE,
)
from flow import new as flow_new
from flow.tools import tb_optimizer


def failed_trajectory(
    *,
    owner: str = "stub",
    action: str = "regenerate_stub",
    secret: str = "",
):
    return {
        "trajectory_idx": 0,
        "best_round": 2,
        "best_cov": 0.0,
        "best_tb": f"// hidden-only {secret}",
        "best_stub": f"// generated-stub {secret}",
        "rounds": [
            {
                "round": 1,
                "status": "compile_failed",
                "failure_owner": owner,
                "next_action": action,
                "compile_stderr": (
                    "refactor_code.cpp: error: generated Stub does not compile"
                ),
                "tb_code": f"// private-testbench {secret}",
                "stub_code": f"// private-stub {secret}",
            },
            {
                "round": 2,
                "status": "compile_failed",
                "failure_owner": owner,
                "next_action": action,
                "compile_stderr": (
                    "refactor_code.cpp: error: replacement Stub still fails"
                ),
                "tb_code": f"// private-testbench-2 {secret}",
                "stub_code": f"// private-stub-2 {secret}",
            },
        ],
        "synth_ok": False,
        "qualified": False,
        "trajectory_status": "coverage_failed",
        "synth_error": "replacement Stub still fails",
    }


class FailedGenerationAdapter:
    def __init__(self, payload):
        self.payload = payload
        self.last_raw_result = None

    def __call__(self, context):
        context.budget.consume(llm_calls=1)
        self.last_raw_result = (
            False,
            {"generation_failure": self.payload},
        )
        return PhaseResult(
            phase=RunPhase.REFACTOR,
            status=PhaseStatus.FAILED,
            summary="bounded generation exhaustion",
            metadata={"generation_only": True},
        )


class TestGenerationExhaustionContractTests(unittest.TestCase):
    def test_hidden_payload_is_code_free_and_structured(self):
        secret = "PRIVATE_HELD_OUT_STRUCTURE"
        exc = tb_optimizer.TestbenchGenerationExhausted(
            split="hidden",
            stage="hidden_generation_qualification",
            trajectories=[failed_trajectory(secret=secret)],
        )
        payload = exc.to_dict()
        rendered = json.dumps(payload, sort_keys=True)

        self.assertEqual(
            payload["failure_kind"],
            "testbench_generation_exhausted",
        )
        self.assertEqual(payload["failure_owner"], "stub")
        self.assertEqual(payload["next_action"], "regenerate_stub")
        self.assertIn("no qualified trajectory", str(exc))
        self.assertIn(
            "golden hidden testbench generation",
            str(exc),
        )
        self.assertEqual(payload["attempt_count"], 2)
        self.assertEqual(payload["trajectory_count"], 1)
        self.assertTrue(payload["bounded"])
        self.assertFalse(
            payload["hidden_testbench_exposed_to_model"]
        )
        self.assertNotIn(secret, rendered)
        self.assertNotIn("tb_code", rendered)
        self.assertNotIn("stub_code", rendered)

    def test_hidden_testbench_owned_diagnostic_is_redacted(self):
        secret = "DO_NOT_SURFACE_THIS_HELD_OUT_DETAIL"
        trajectory = failed_trajectory(
            owner="testbench",
            action="repair_testbench",
            secret=secret,
        )
        trajectory["rounds"][-1]["compile_stderr"] = secret
        exc = tb_optimizer.TestbenchGenerationExhausted(
            split="hidden",
            stage="hidden_generation_qualification",
            trajectories=[trajectory],
        )
        rendered = json.dumps(exc.to_dict(), sort_keys=True)
        self.assertNotIn(secret, rendered)
        self.assertIn("operator-only artifacts", rendered)

    def test_hidden_generator_raises_typed_exhaustion(self):
        failed = failed_trajectory()
        with patch.object(
            tb_optimizer,
            "run_trajectory",
            return_value=failed,
        ):
            with self.assertRaises(
                tb_optimizer.TestbenchGenerationExhausted
            ) as captured:
                tb_optimizer.make_golden_hidden_tb(
                    orig_code="void top(){}\n",
                    kernel_name="top",
                    pinned_public_hls_decl="void top_hls();",
                    M=1,
                    K=1,
                    llm_config={},
                )

        payload = captured.exception.to_dict()
        self.assertEqual(payload["split"], "hidden")
        self.assertEqual(
            payload["stage"],
            "hidden_generation_qualification",
        )
        self.assertEqual(payload["failure_owner"], "stub")

    def test_flow_converts_typed_exhaustion_to_false_result(self):
        exc = tb_optimizer.TestbenchGenerationExhausted(
            split="hidden",
            stage="hidden_generation_qualification",
            trajectories=[failed_trajectory()],
        )
        cv = {
            "generation_event_order": [
                "public_generation",
                "candidate_generation",
            ],
            "generated_hidden_testbench": "must-be-cleared",
        }
        with patch.object(
            flow_new.tools.general,
            "save_context",
        ) as save:
            success, returned = (
                flow_new._finish_test_generation_exhaustion(
                    cv,
                    exc,
                    "/tmp/operator-only",
                )
            )

        self.assertFalse(success)
        self.assertIs(returned, cv)
        self.assertEqual(
            returned["failed_stage"],
            "hidden_generation_qualification",
        )
        self.assertEqual(returned["failure_owner"], "stub")
        self.assertEqual(
            returned["generated_hidden_testbench"],
            "",
        )
        self.assertFalse(
            returned["hidden_testbench_exposed_to_model"]
        )
        save.assert_called_once()

    def test_source_bootstrap_preserves_structured_failed_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "kernel.cpp"
            source.write_text(
                'extern "C" int top(int x) { return x; }\n',
                encoding="utf-8",
            )
            runtime = resolve_model_runtime("deepseek-v4-flash")
            request = SourceBootstrapRequest(
                source_path=source,
                top_function="top",
                mode=RunMode.REFACTOR,
                effective_model_config=runtime.effective_config,
                target=resolve_target_profile(None),
                test_source_plan=build_test_source_plan(),
                budget_contract=(
                    DEFAULT_SOURCE_RUN_BUDGET_PROFILE.resolve()
                ),
                max_candidate_repairs=2,
                run_id="generation-exhaustion-test",
            )
            layout = SourceRunLayout.create(
                request.run_id,
                artifact_base=root / "artifacts",
                work_base=root / "work",
            )
            layout.artifact_root.mkdir(parents=True)
            payload = (
                tb_optimizer.TestbenchGenerationExhausted(
                    split="hidden",
                    stage="hidden_generation_qualification",
                    trajectories=[failed_trajectory()],
                ).to_dict()
            )
            generation = FailedGenerationAdapter(payload)
            phase = SourceBootstrapPhase(
                request=request,
                layout=layout,
                generation_adapter=generation,
                formal_phase_builder=lambda *_args: self.fail(
                    "formal validation must not start"
                ),
            )
            context = RunContext(
                run_id=request.run_id,
                task=TaskSpec(
                    task_id="source-task",
                    kernel_path=str(source),
                    kernel_name="top",
                ),
                budget=BudgetManager(
                    request.budget_contract.to_budget_limits()
                ),
                trace=TraceRecorder(
                    request.run_id,
                    task_id="source-task",
                ),
            )

            result = phase(context)

        self.assertIs(result.status, PhaseStatus.FAILED)
        self.assertFalse(result.succeeded)
        self.assertFalse(
            result.metadata["formal_validation_started"]
        )
        self.assertEqual(
            result.metadata["failed_stage"],
            "hidden_generation_qualification",
        )
        self.assertEqual(
            result.metadata["failure_owner"],
            "stub",
        )
        self.assertEqual(result.metadata["attempt_count"], 2)
        self.assertEqual(result.metadata["trajectory_count"], 1)
        self.assertFalse(
            result.metadata["hidden_testbench_exposed_to_model"]
        )

    def test_unrelated_infrastructure_exception_remains_error(self):
        def broken(_context):
            raise OSError("filesystem unavailable")

        task = TaskSpec(
            task_id="infrastructure-error-test",
            kernel_path="kernel.cpp",
            kernel_name="top",
            mode=RunMode.REFACTOR,
        )
        result = UnifiedRunner(
            {RunPhase.REFACTOR: broken}
        ).run(task, run_id="infrastructure-error-test")
        self.assertIs(result.status, RunStatus.ERROR)
        self.assertIs(
            result.phases[0].status,
            PhaseStatus.ERROR,
        )


if __name__ == "__main__":
    unittest.main()
