from __future__ import annotations

from dataclasses import replace
import tempfile
import unittest
from pathlib import Path

from agrefactor.campaign import (
    ExistingRefactorR5CampaignExecutor,
    R5Arm,
    R5CaseSpec,
)
from agrefactor.models import CandidateModelAdapter
from agrefactor.product import R5CommonBaselineCapture
from agrefactor.recovery import (
    AdvisoryConfidence,
    AdvisoryOwner,
    AdvisoryRepairScope,
    DiagnosticAdvisory,
)
from agrefactor.runtime import BudgetLimits, CandidateRepairPhaseConfig
from agrefactor.runtime.r5_profile import resolve_r5_profile
from tests.test_r4_existing_orchestrator_integration import (
    R4ExistingOrchestratorIntegrationTests,
    h,
    helpers,
)


class _BudgetedAdvisor:
    def __init__(self, context):
        self._context = context

    @property
    def identity(self):
        return {
            "provider": "test-provider",
            "model_name": "test-model",
            "model": "test-model-id",
        }

    def diagnose(self, request):
        self._context.budget.consume(llm_calls=1)
        return DiagnosticAdvisory(
            suspected_owner=AdvisoryOwner.CANDIDATE,
            suspected_failure_class="unsupported_construct",
            evidence_refs=(request.evidence_ids[0],),
            repair_scope=AdvisoryRepairScope.CANDIDATE_ONLY,
            confidence=AdvisoryConfidence.HIGH,
            metadata={
                "strict_parser": "r2-v1",
                "prompt_contract_version": "r2-shadow-output-v4",
            },
        )


class _Integration:
    def __init__(self, arm, context):
        self.profile = resolve_r5_profile(arm.value)
        self._context = context

    @property
    def approved_memory_snippets(self):
        return ()

    def run_from_existing_orchestrator(self, **kwargs):
        del kwargs
        self._context.budget.consume(
            llm_calls=1,
            csim_calls=1,
            csynth_calls=1,
            cosim_calls=1,
        )
        return {
            "schema_version": 1,
            "status": "verified_positive",
            "main_result_unchanged": True,
            "accepted_by_integration": False,
        }


class R5ProductCampaignExecutorTests(unittest.TestCase):
    def _capture(self, root):
        m = helpers()
        base = R4ExistingOrchestratorIntegrationTests()
        result, request, _, _ = base._run_main(m)
        result = replace(
            result,
            metadata={
                **dict(result.metadata),
                "r2_shadow_diagnostics": [],
                "r2_shadow_enabled": False,
            },
        )
        request = replace(
            request,
            r5_arm="A0",
            llm_advisory_mode="off",
        )
        adapter, _ = m.make_adapter([m.P1])
        self.assertIsInstance(adapter, CandidateModelAdapter)
        event = result.metadata["diagnostic_events"][0]
        before = {
            "llm_calls": 0,
            "tool_calls": 0,
            "compile_calls": 0,
            "csim_calls": 0,
            "csynth_calls": 0,
            "cosim_calls": 0,
            "tokens": 0,
            "cost_usd": 0.0,
            "elapsed_s": 0.0,
            "costs_by_currency": {},
        }
        after = {
            **before,
            "llm_calls": 1,
            "csim_calls": 1,
            "csynth_calls": 1,
            "cosim_calls": 1,
        }
        capture = R5CommonBaselineCapture(
            baseline_id=h("baseline"),
            run_id="r5-product-test",
            source_sha256=h("source"),
            context_signature=event["context_signature"],
            formal_task=m.make_task(),
            formal_request=request,
            formal_result=result,
            phase_config=CandidateRepairPhaseConfig(
                request=request,
                work_root=Path(root) / "captured-work",
                artifact_root=Path(root) / "captured-artifacts",
            ),
            model_adapter=adapter,
            execution_identity={
                "identity_complete": True,
                "hidden_input_count": 0,
            },
            budget_before=before,
            budget_after=after,
            budget_limits=BudgetLimits(
                max_llm_calls=4,
                max_tool_calls=20,
                max_compile_calls=20,
                max_csim_calls=10,
                max_csynth_calls=10,
                max_cosim_calls=10,
                max_wall_time_s=5000,
            ),
            artifact_sha256=h("baseline-artifact"),
        )
        return capture

    def test_model_adapter_fork_has_no_cross_arm_history(self):
        with tempfile.TemporaryDirectory() as root:
            capture = self._capture(root)
            forked = capture.model_adapter.fork()
            self.assertIsNot(forked, capture.model_adapter)
            self.assertEqual(forked.effective_config, capture.model_adapter.effective_config)
            self.assertEqual(forked.prompts, ())
            self.assertEqual(forked.responses, ())
            self.assertEqual(forked.results, ())

    def test_history_capture_binds_observed_context_before_arm_execution(self):
        with tempfile.TemporaryDirectory() as root:
            capture = self._capture(root)
            executor = ExistingRefactorR5CampaignExecutor(
                artifact_root=Path(root) / "history",
                baseline_runner=lambda *args: self.fail(
                    "history adoption must not launch a second baseline"
                ),
                advisor_factory=lambda captured, arm, context: _BudgetedAdvisor(
                    context
                ),
                integration_factory=lambda captured, arm, context, episodes, model_adapter: _Integration(
                    arm, context
                ),
            )

            case, baseline = executor.adopt_history_common_baseline(
                case_id="history-case",
                source_sha256=capture.source_sha256,
                repeat=1,
                capture=capture,
            )

            self.assertEqual(case.period, "history")
            self.assertEqual(case.context_signature, capture.context_signature)
            self.assertEqual(baseline["baseline_id"], capture.baseline_id)
            observation = executor.run_arm(
                case=case,
                repeat=1,
                arm=R5Arm.A2,
                baseline=baseline,
                arm_index=0,
            )
            self.assertEqual(observation["status"], "verified_positive")
            self.assertEqual(observation["source_sha256"], capture.source_sha256)

    def test_all_arms_consume_one_capture_with_isolated_usage(self):
        with tempfile.TemporaryDirectory() as root:
            capture = self._capture(root)
            baseline_calls = []
            arm_adapters = []

            def baseline_runner(case, repeat, artifact_root):
                baseline_calls.append((case.case_id, repeat, artifact_root))
                return capture

            def integration_factory(
                captured,
                arm,
                context,
                episodes,
                model_adapter,
            ):
                del captured, episodes
                arm_adapters.append(model_adapter)
                return _Integration(arm, context)

            executor = ExistingRefactorR5CampaignExecutor(
                artifact_root=Path(root) / "campaign",
                baseline_runner=baseline_runner,
                advisor_factory=lambda captured, arm, context: _BudgetedAdvisor(
                    context
                ),
                integration_factory=integration_factory,
            )
            case = R5CaseSpec(
                "case-1",
                capture.source_sha256,
                capture.context_signature,
                "future",
            )
            baseline = executor.prepare_common_baseline(case, 1)
            self.assertEqual(len(baseline_calls), 1)
            self.assertEqual(baseline["provider_calls"], 1)
            self.assertEqual(baseline["vitis_launches"], 3)

            observations = {
                arm: executor.run_arm(
                    case=case,
                    repeat=1,
                    arm=arm,
                    baseline=baseline,
                    arm_index=index,
                )
                for index, arm in enumerate(R5Arm)
            }
            self.assertEqual(
                (observations[R5Arm.A0]["provider_calls"], observations[R5Arm.A0]["vitis_launches"]),
                (0, 0),
            )
            self.assertEqual(
                (observations[R5Arm.A1]["provider_calls"], observations[R5Arm.A1]["vitis_launches"]),
                (1, 0),
            )
            for arm in (R5Arm.A2, R5Arm.A3, R5Arm.A4, R5Arm.A5, R5Arm.A6):
                self.assertEqual(
                    (observations[arm]["provider_calls"], observations[arm]["vitis_launches"]),
                    (2, 3),
                )
            self.assertEqual(len(arm_adapters), 5)
            self.assertEqual(len({id(item) for item in arm_adapters}), 5)
            self.assertTrue(
                all(item is not capture.model_adapter for item in arm_adapters)
            )
            arm_files = tuple(
                (Path(root) / "campaign").glob(
                    "case-*/repeat-01/arms/*/arm_result.json"
                )
            )
            self.assertEqual(len(arm_files), 7)
            self.assertEqual(len({path.parent for path in arm_files}), 7)

    def test_mutation_arm_abstains_before_factory_when_baseline_has_no_event(self):
        with tempfile.TemporaryDirectory() as root:
            capture = self._capture(root)
            capture = replace(
                capture,
                formal_result=replace(
                    capture.formal_result,
                    status=capture.formal_result.status.__class__.ACCEPTED,
                    last_validation_state=(
                        capture.formal_result.last_validation_state.__class__.ACCEPTED
                    ),
                    metadata={
                        **dict(capture.formal_result.metadata),
                        "diagnostic_events": [],
                    },
                ),
            )
            executor = ExistingRefactorR5CampaignExecutor(
                artifact_root=Path(root) / "campaign",
                baseline_runner=lambda *args: capture,
                advisor_factory=lambda *args: self.fail(
                    "R2 must not run without one diagnostic event"
                ),
                integration_factory=lambda *args: self.fail(
                    "R4/R5 integration must not be built without one diagnostic event"
                ),
            )
            case = R5CaseSpec(
                "accepted-case",
                capture.source_sha256,
                capture.context_signature,
                "future",
            )
            baseline = executor.prepare_common_baseline(case, 1)
            observation = executor.run_arm(
                case=case,
                repeat=1,
                arm=R5Arm.A4,
                baseline=baseline,
                arm_index=0,
            )

            self.assertEqual(observation["status"], "abstained")
            self.assertEqual(
                observation["integration"]["reason"],
                "accepted_without_diagnostic",
            )
            self.assertEqual(observation["provider_calls"], 0)
            self.assertEqual(observation["vitis_launches"], 0)

            a0 = executor.run_arm(
                case=case,
                repeat=1,
                arm=R5Arm.A0,
                baseline=baseline,
                arm_index=1,
            )
            self.assertEqual(a0["status"], "baseline_accepted")
            self.assertTrue(a0["baseline_accepted"])
            self.assertFalse(a0["verified_repair"])


if __name__ == "__main__":
    unittest.main()
