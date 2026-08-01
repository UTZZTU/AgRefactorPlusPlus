from pathlib import Path
import runpy
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "stage3_s35_real_bottleneck_smoke.py"


class BottleneckSmokeToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = runpy.run_path(str(TOOL), run_name="stage3_s35_smoke_test")

    def test_direct_tool_bootstrap_and_inside_repository_source(self):
        resolve_source = self.module["resolve_source"]
        source = resolve_source(
            REPO,
            "tests/fixtures/stage3_s35/bottleneck_smoke_kernel.cpp",
        )
        self.assertTrue(source.is_file())
        self.assertEqual(source.name, "bottleneck_smoke_kernel.cpp")

    def test_resolve_source_rejects_path_outside_repository(self):
        with tempfile.NamedTemporaryFile() as handle:
            with self.assertRaises(ValueError):
                self.module["resolve_source"](REPO, handle.name)

    def test_invoke_one_llm_call_consumes_slot_on_success_and_exception(self):
        from agrefactor.runtime import BudgetLimits, BudgetManager

        invoke = self.module["invoke_one_llm_call"]
        budget = BudgetManager(BudgetLimits(max_llm_calls=2), clock=lambda: 0.0)
        self.assertEqual(invoke(budget, lambda: "ok"), "ok")
        with self.assertRaises(RuntimeError):
            invoke(budget, lambda: (_ for _ in ()).throw(RuntimeError("x")))
        self.assertEqual(budget.snapshot().llm_calls, 2)

    def test_smoke_ppa_has_explicit_ii_and_fixture_identity(self):
        evidence = self.module["smoke_ppa"]()
        self.assertEqual(evidence.initiation_interval_max, 4)
        self.assertEqual(evidence.evidence_id, "ppa-s35-smoke")
        self.assertIn("typed_fixture_not_live_vitis", evidence.parser_warnings)

    def test_deepseek_parameters_disable_thinking_without_provider_json_mode(self):
        parameters = self.module["smoke_model_parameters"](
            family="deepseek",
            max_tokens=32768,
        )
        self.assertEqual(parameters["temperature"], 0)
        self.assertEqual(parameters["max_tokens"], 32768)
        self.assertEqual(
            parameters["extra_body"],
            {"thinking": {"type": "disabled"}},
        )
        self.assertNotIn("response_format", parameters)
        ceiling_parameters = self.module["smoke_model_parameters"](
            family="deepseek",
            max_tokens=65536,
        )
        self.assertEqual(ceiling_parameters["max_tokens"], 65536)
        with self.assertRaises(ValueError):
            self.module["smoke_model_parameters"](
                family="deepseek",
                max_tokens=65537,
            )

    def test_non_deepseek_parameters_preserve_provider_default_thinking(self):
        parameters = self.module["smoke_model_parameters"](
            family="generic-openai-compatible",
            max_tokens=4096,
        )
        self.assertEqual(
            parameters,
            {"temperature": 0, "max_tokens": 4096},
        )

    def test_verify_summary_requires_two_llm_and_no_tool_calls(self):
        summary = {
            "budget_usage": {
                "llm_calls": 2,
                "tool_calls": 0,
                "compile_calls": 0,
                "csim_calls": 0,
                "csynth_calls": 0,
            },
            "classification_authoritative": False,
            "raw_report_used": False,
            "static_bottleneck_gate_used": False,
            "analysis_json_authority": "local_strict_response_contract",
            "analysis_provider_json_mode": False,
            "model_family": "deepseek",
            "thinking_mode_control": "disabled",
            "output_token_policy": "stage2_typed_output_policy",
            "analysis_max_tokens": 32768,
            "rewrite_max_tokens": 32768,
            "output_token_safety_ceiling": 65536,
            "candidate_semantically_changed": True,
            "top_interface_preserved": True,
            "hidden_evidence_exposed": False,
            "product_optimize_full_enabled": False,
        }
        self.module["verify_summary"](summary)
        bad = dict(summary)
        bad["classification_authoritative"] = True
        with self.assertRaises(AssertionError):
            self.module["verify_summary"](bad)


if __name__ == "__main__":
    unittest.main()
