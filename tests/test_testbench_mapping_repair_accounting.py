from contextlib import ExitStack
import unittest
from unittest.mock import Mock, patch

from autogen.agentchat.group import ContextVariables
from agrefactor.runtime.budget import BudgetExceededError
from flow.tools import tb_optimizer, testbench
from flow.tools.result_mapping import ResultMappingVerificationError


DRIVER = "void origin(); void origin_hls(); int main(){origin();origin_hls();}"


class MappingRepairAccountingTests(unittest.TestCase):
    def harness(self, *, mapping_results, requests=None, originals=None, target=False):
        stack = ExitStack()
        self.addCleanup(stack.close)
        agent = Mock()
        agent.run.return_value = Mock(messages=[{"content": "instruction"}])
        loader = Mock()
        loader.load_agent.return_value = agent
        stack.enter_context(patch.object(testbench, "HLSAgentLoader", return_value=loader))
        stack.enter_context(patch.object(testbench, "isolate_reference_program_entry", return_value="void origin() {}"))
        stack.enter_context(patch.object(testbench, "freeze_result_mapping", side_effect=mapping_results))
        for name in ("validate_testbench_top_contract", "validate_testbench_input_domain", "validate_stub_contract"):
            stack.enter_context(patch.object(tb_optimizer, name))
        stack.enter_context(patch.object(tb_optimizer, "extract_hls_decl_from_testbench", return_value="void origin_hls();"))
        stack.enter_context(patch.object(tb_optimizer, "freeze_public_type_contract", return_value={}))
        stack.enter_context(patch.object(tb_optimizer, "frozen_type_instruction", return_value=""))
        request = stack.enter_context(patch.object(
            tb_optimizer, "_request_cpp_artifact", return_value=DRIVER,
            **({"side_effect": requests} if requests is not None else {}),
        ))
        stack.enter_context(patch.object(
            testbench.tools.tb_coverage, "check_original_execution",
            return_value={"status": "ok"},
            **({"side_effect": originals} if originals is not None else {}),
        ))
        stack.enter_context(patch.object(tb_optimizer, "_synth_check", return_value=(False, "abi-probe-failure")))
        context = {"kernel_name": "origin", "curr_code": "void origin() {}", "max_testbench_repair_attempts": 3}
        if target:
            context["target_profile"] = {"compile_flags": ()}
        return context, request

    def test_four_failed_checks_report_three_actual_repair_requests(self):
        context, request = self.harness(mapping_results=[ValueError(f"manifest-failure-{i}") for i in range(4)])
        with self.assertRaises(tb_optimizer.TestbenchGenerationExhausted) as caught:
            testbench.gen_tb_prior(context)
        payload = caught.exception.to_dict()
        events = context["public_result_mapping_qualification"]
        self.assertEqual(events["qualification_checks_used"], 4)
        self.assertEqual(events["mapping_repair_requests_used"], 3)
        self.assertEqual([item["repair_requested"] for item in events["rounds"]], [True, True, True, False])
        self.assertEqual(payload["attempt_count"], 4)
        self.assertEqual(payload["repair_attempt_count"], 3)
        self.assertEqual(request.call_count, 4)
        for call in request.call_args_list[1:]:
            self.assertEqual(call.kwargs["max_repairs"], 0)
        final_prompt = request.call_args_list[-1].args[1]
        for index in range(3):
            self.assertIn(f"manifest-failure-{index}", final_prompt)
        self.assertIn("AGREFACTOR_RESULT_MAPPING_MANIFEST", final_prompt)

    def test_three_repairs_can_still_qualify_the_fourth_artifact(self):
        context, request = self.harness(mapping_results=[ValueError(f"failure-{i}") for i in range(3)] + [None])
        generated, _, _ = testbench.gen_tb_prior(context)
        self.assertEqual(generated, DRIVER)
        events = context["public_result_mapping_qualification"]
        self.assertEqual(events["qualification_checks_used"], 4)
        self.assertEqual(events["mapping_repair_requests_used"], 3)
        self.assertEqual(events["rounds"][-1]["status"], "ok")
        self.assertEqual(request.call_count, 4)

    def test_real_context_variables_clear_stale_mapping_on_identity_revision(self):
        context, _ = self.harness(mapping_results=[None])
        context = ContextVariables(data={
            **context, "public_result_mapping": {"stale": True},
            "public_result_mapping_unverified": {"stale": True},
            "public_result_mapping_diagnostics": [{"stale": True}],
        })
        testbench.gen_tb_prior(context)
        self.assertIsNone(context["public_result_mapping"])
        self.assertIsNone(context["public_result_mapping_unverified"])
        self.assertIsNone(context["public_result_mapping_diagnostics"])

    def test_mapping_quota_is_shared_across_initial_original_and_abi_paths(self):
        failed_original = {"status": "original_run_failed", "failure_owner": "unknown", "run_stderr": "original-qualification-failure"}
        context, request = self.harness(
            mapping_results=[ValueError("initial-failure"), None, ValueError("original-replacement-failure"), None,
                             ValueError("abi-replacement-failure"), ValueError("third-repair-failure")],
            originals=[failed_original, {"status": "ok"}], target=True,
        )
        with self.assertRaises(tb_optimizer.TestbenchGenerationExhausted):
            testbench.gen_tb_prior(context)
        events = context["public_result_mapping_qualification"]
        self.assertEqual(events["mapping_repair_requests_used"], 3)
        self.assertEqual(events["qualification_checks_used"], 6)
        self.assertEqual(request.call_count, 7)
        final_prompt = request.call_args_list[-1].args[1]
        for text in ("initial-failure", "original-qualification-failure", "original-replacement-failure",
                     "abi-probe-failure", "abi-replacement-failure"):
            self.assertIn(text, final_prompt)

    def test_budget_denial_does_not_count_as_a_repair_request(self):
        context, request = self.harness(
            mapping_results=[ValueError("manifest-failure")],
            requests=[DRIVER, BudgetExceededError("llm_calls", 1, 2)],
        )
        with self.assertRaises(BudgetExceededError):
            testbench.gen_tb_prior(context)
        events = context["public_result_mapping_qualification"]
        self.assertEqual(events["mapping_repair_requests_used"], 0)
        self.assertFalse(events["rounds"][0]["repair_requested"])
        self.assertEqual(request.call_count, 2)

    def test_invalid_response_still_counts_the_dispatched_request(self):
        context, _ = self.harness(
            mapping_results=[ValueError("manifest-failure")],
            requests=[DRIVER, tb_optimizer.ModelArtifactError("invalid model artifact")],
        )
        with self.assertRaises(tb_optimizer.ModelArtifactError):
            testbench.gen_tb_prior(context)
        self.assertEqual(context["public_result_mapping_qualification"]["mapping_repair_requests_used"], 1)

    def test_incomplete_compiler_context_preserves_mapping_without_model_repairs(self):
        failure = ResultMappingVerificationError("missing call facts", {"shared_cpp": "immutable-mapping"}, {
            "parse_context": {"source_path": "/tmp/testbench.cpp"},
            "diagnostics": [{"severity": 3, "file": "/vendor/header.hpp", "message": "compiler context mismatch"}],
        })
        context, request = self.harness(mapping_results=[failure])
        with self.assertRaises(tb_optimizer.TestbenchGenerationExhausted) as caught:
            testbench.gen_tb_prior(context)
        payload = caught.exception.to_dict()
        self.assertEqual(payload["failure_owner"], "unknown")
        self.assertEqual(payload["next_action"], "review_unknown")
        self.assertEqual(payload["repair_attempt_count"], 0)
        self.assertEqual(context["public_result_mapping_unverified"]["shared_cpp"], "immutable-mapping")
        self.assertFalse(context["public_result_mapping_unverified"]["compiler_structure_verified"])
        self.assertEqual(request.call_count, 1)

    def test_probe_type_drift_retries_only_probe_before_real_synthesis(self):
        context, request = self.harness(mapping_results=[None], target=True)
        drift = tb_optimizer.ModelArtifactError("top type field layout differs")
        with patch.object(tb_optimizer, "validate_stub_contract", side_effect=[drift, None]), \
             patch.object(tb_optimizer, "_synth_check", return_value=(True, "")) as synth:
            generated, _, _ = testbench.gen_tb_prior(context)
        self.assertEqual(generated, DRIVER)
        self.assertEqual([call.kwargs["artifact_kind"] for call in request.call_args_list],
                         ["testbench", "empty_stub", "empty_stub"])
        synth.assert_called_once()
        second_probe = request.call_args_list[-1].args[1]
        self.assertIn("top type field layout differs", second_probe)
        self.assertIn("Previous synthesis probe", second_probe)
        self.assertNotIn("explicit coordinated ABI correction", second_probe)

    def test_probe_type_drift_consumes_existing_three_retries(self):
        context, request = self.harness(mapping_results=[None], target=True)
        with patch.object(tb_optimizer, "validate_stub_contract", side_effect=tb_optimizer.ModelArtifactError("type drift")), \
             patch.object(tb_optimizer, "_synth_check") as synth:
            with self.assertRaises(tb_optimizer.TestbenchGenerationExhausted) as caught:
                testbench.gen_tb_prior(context)
        self.assertEqual(caught.exception.to_dict()["failure_owner"], "stub")
        self.assertEqual(caught.exception.to_dict()["attempt_count"], 4)
        self.assertEqual(caught.exception.to_dict()["repair_attempt_count"], 3)
        self.assertEqual(context["public_abi_probe_qualification"]["probe_repair_requests_used"], 3)
        self.assertEqual(context["public_abi_probe_qualification"]["synthesis_calls_used"], 0)
        self.assertEqual(request.call_count, 5)
        self.assertEqual([call.kwargs["artifact_kind"] for call in request.call_args_list],
                         ["testbench", "empty_stub", "empty_stub", "empty_stub", "empty_stub"])
        synth.assert_not_called()

    def test_budget_denied_probe_replacement_is_not_counted(self):
        context, _ = self.harness(mapping_results=[None], target=True,
            requests=[DRIVER, DRIVER, BudgetExceededError("llm_calls", 2, 3)])
        with patch.object(tb_optimizer, "validate_stub_contract", side_effect=tb_optimizer.ModelArtifactError("type drift")):
            with self.assertRaises(BudgetExceededError):
                testbench.gen_tb_prior(context)
        self.assertEqual(context["public_abi_probe_qualification"]["probe_repair_requests_used"], 0)
        self.assertEqual(context["public_abi_probe_qualification"]["qualification_checks_used"], 1)

    def test_provided_probe_drift_requires_review_without_automatic_replacement(self):
        context, request = self.harness(mapping_results=[None])
        with patch.object(tb_optimizer, "validate_stub_contract", side_effect=tb_optimizer.ModelArtifactError("type drift")), \
             patch.object(tb_optimizer, "_synth_check") as synth:
            with self.assertRaises(tb_optimizer.TestbenchGenerationExhausted) as caught:
                testbench.qualify_external_public_testbench(context, DRIVER)
        payload = caught.exception.to_dict()
        self.assertEqual(payload["qualification_mode"], "provided")
        self.assertEqual(payload["next_action"], "review_required_provided")
        self.assertEqual(payload["repair_attempt_count"], 0)
        request.assert_called_once()
        synth.assert_not_called()


if __name__ == "__main__":
    unittest.main()
