from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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
    TraceRecorder,
)
from agrefactor.runtime.budget_profile import (
    DEFAULT_SOURCE_RUN_BUDGET_PROFILE,
)
from flow import new as flow_new
from flow.tools import tb_optimizer


def provided_failure(*, stage: str = "external_public_original_qualification"):
    """Build the smallest code-free supplied-suite qualification failure."""

    return tb_optimizer.TestbenchGenerationExhausted(
        split="public",
        stage=stage,
        qualification_mode="provided",
        trajectories=[
            {
                "trajectory_idx": 0,
                "rounds": [
                    {
                        "status": "original_run_failed",
                        "failure_owner": "unknown",
                        "next_action": "review_unknown",
                        "run_stderr": "provided qualification did not pass",
                    }
                ],
            }
        ],
    )


class FailedGenerationAdapter:
    def __init__(self, payload):
        self.payload = payload
        self.last_raw_result = None

    def __call__(self, context):
        self.last_raw_result = (
            False,
            {"generation_failure": self.payload},
        )
        return PhaseResult(
            phase=RunPhase.REFACTOR,
            status=PhaseStatus.FAILED,
            summary="provided qualification failed",
            metadata={"generation_only": True},
        )


class ProvidedQualificationResultContractTests(unittest.TestCase):
    def test_generated_mode_remains_legacy_default(self):
        payload = tb_optimizer.TestbenchGenerationExhausted(
            split="hidden",
            stage="hidden_generation_qualification",
            trajectories=[
                {
                    "rounds": [
                        {
                            "status": "compile_failed",
                            "failure_owner": "stub",
                            "next_action": "regenerate_stub",
                            "compile_stderr": "stub failed",
                        }
                    ]
                }
            ],
        ).to_dict()

        self.assertEqual(payload["failure_kind"], "testbench_generation_exhausted")
        self.assertEqual(payload["terminal_class"], "bounded_generation_failure")
        self.assertEqual(payload["qualification_mode"], "generated")
        self.assertTrue(payload["retry_exhausted"])
        self.assertEqual(payload["repair_attempt_count"], 1)

    def test_provided_artifact_references_stay_operator_only(self):
        cv = {
            "generation_event_order": [],
            "provided_public_qualification": {
                "artifact_dir": "/work/provided/attempt_001",
                "original_receipt": "/work/provided/attempt_001/receipt.json",
            },
        }
        with patch.object(flow_new.tools.general, "save_context"):
            ok, context = flow_new._finish_test_generation_exhaustion(
                cv,
                provided_failure(),
                "/tmp/output",
            )
        self.assertFalse(ok)
        self.assertEqual(
            context["generation_failure"]["qualification_artifact_dir"],
            "/work/provided/attempt_001",
        )
        self.assertEqual(
            context["generation_failure"]["qualification_receipt"],
            "/work/provided/attempt_001/receipt.json",
        )

    def test_provided_exception_is_distinct_from_bounded_generation(self):
        payload = provided_failure().to_dict()

        self.assertEqual(payload["failure_kind"], "testbench_qualification_failed")
        self.assertEqual(payload["terminal_class"], "provided_qualification_failure")
        self.assertEqual(payload["qualification_mode"], "provided")
        self.assertEqual(payload["repair_attempt_count"], 0)
        self.assertFalse(payload["retry_exhausted"])
        self.assertEqual(payload["attempt_count"], 1)
        self.assertEqual(payload["trajectory_count"], 1)
        self.assertEqual(payload["failure_owner"], "unknown")
        self.assertEqual(
            payload["next_action"],
            "review_required_provided",
        )
        self.assertIn(
            "provided Public Testbench qualification failed before formal validation",
            str(provided_failure()),
        )

    def test_external_branch_converts_only_typed_provided_failure(self):
        source = 'extern "C" int top(int x) { return x; }\n'
        testbench = (
            'extern "C" int top(int);\n'
            'extern "C" int top_hls(int);\n'
            "int main() { return top(0) != top_hls(0); }\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "kernel.cpp"
            source_path.write_text(source, encoding="utf-8")
            output_dir = root / "output"
            output_dir.mkdir()
            failure = provided_failure()
            with patch.object(
                flow_new.tools.general,
                "create_output_dir",
                return_value=str(output_dir),
            ), patch.object(
                flow_new.tools.general,
                "create_log_and_redirect",
            ), patch.object(
                flow_new.tools.general,
                "save_context",
            ), patch.object(
                flow_new,
                "normalize_input_domain_contract",
                return_value={},
            ), patch.object(
                flow_new,
                "input_domain_sha256",
                return_value="provided-domain-sha",
            ), patch.object(
                flow_new.tools.tb_optimizer,
                "extract_hls_decl_from_testbench",
                return_value='extern "C" int top_hls(int);',
            ), patch.object(
                flow_new.tools.testbench,
                "qualify_external_public_testbench",
                side_effect=failure,
            ):
                success, context = flow_new.hls_refactor_with_rag(
                    str(source_path),
                    "top",
                    output_dir=str(output_dir),
                    external_testbench=testbench,
                    input_domain_contract={},
                )

        self.assertFalse(success)
        self.assertEqual(
            context["generation_failure_kind"],
            "testbench_qualification_failed",
        )
        self.assertEqual(
            context["failed_stage"],
            "external_public_original_qualification",
        )
        self.assertEqual(context["failure_owner"], "unknown")
        self.assertEqual(
            context["next_action"],
            "review_required_provided",
        )
        self.assertEqual(context["qualification_mode"], "provided")

    def test_external_branch_does_not_swallow_internal_exception(self):
        source = 'extern "C" int top(int x) { return x; }\n'
        testbench = (
            'extern "C" int top(int);\n'
            'extern "C" int top_hls(int);\n'
            "int main() { return top(0) != top_hls(0); }\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "kernel.cpp"
            source_path.write_text(source, encoding="utf-8")
            output_dir = root / "output"
            output_dir.mkdir()
            with patch.object(
                flow_new.tools.general,
                "create_output_dir",
                return_value=str(output_dir),
            ), patch.object(
                flow_new.tools.general,
                "create_log_and_redirect",
            ), patch.object(
                flow_new,
                "normalize_input_domain_contract",
                return_value={},
            ), patch.object(
                flow_new,
                "input_domain_sha256",
                return_value="provided-domain-sha",
            ), patch.object(
                flow_new.tools.tb_optimizer,
                "extract_hls_decl_from_testbench",
                return_value='extern "C" int top_hls(int);',
            ), patch.object(
                flow_new.tools.testbench,
                "qualify_external_public_testbench",
                side_effect=RuntimeError("internal qualification bug"),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "internal qualification bug",
                ):
                    flow_new.hls_refactor_with_rag(
                        str(source_path),
                        "top",
                        output_dir=str(output_dir),
                        external_testbench=testbench,
                        input_domain_contract={},
                    )

    def test_source_bootstrap_keeps_provided_failure_before_formal_validation(self):
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
                run_id="provided-qualification-test",
            )
            layout = SourceRunLayout.create(
                request.run_id,
                artifact_base=root / "artifacts",
                work_base=root / "work",
            )
            layout.artifact_root.mkdir(parents=True)
            payload = provided_failure().to_dict()
            adapter = FailedGenerationAdapter(payload)
            phase = SourceBootstrapPhase(
                request=request,
                layout=layout,
                generation_adapter=adapter,
                formal_phase_builder=lambda *_args: self.fail(
                    "formal validation must not start"
                ),
            )
            context = RunContext(
                run_id=request.run_id,
                task=TaskSpec(
                    task_id="provided-qualification-task",
                    kernel_path=str(source),
                    kernel_name="top",
                ),
                budget=BudgetManager(
                    request.budget_contract.to_budget_limits()
                ),
                trace=TraceRecorder(
                    request.run_id,
                    task_id="provided-qualification-task",
                ),
            )

            result = phase(context)

        self.assertIs(result.status, PhaseStatus.FAILED)
        self.assertFalse(result.succeeded)
        self.assertFalse(result.metadata["formal_validation_started"])
        self.assertEqual(
            result.metadata["generation_failure_kind"],
            "testbench_qualification_failed",
        )
        self.assertEqual(
            result.metadata["failed_stage"],
            "external_public_original_qualification",
        )
        self.assertEqual(result.metadata["failure_owner"], "unknown")
        self.assertEqual(result.metadata["qualification_mode"], "provided")
        self.assertIn(
            "Provided Public Testbench qualification failed before formal validation",
            result.summary,
        )


if __name__ == "__main__":
    unittest.main()
