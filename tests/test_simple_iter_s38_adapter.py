from __future__ import annotations

import ast
from pathlib import Path
import unittest


_ROOT = Path(__file__).resolve().parents[1]
_MAIN = _ROOT / "opt" / "simple_iter" / "main.py"
_UTILS = _ROOT / "opt" / "utils.py"
_HARNESS = _ROOT / "opt" / "simple_iter" / "harness.py"


class SimpleIterS38AdapterTests(unittest.TestCase):
    def test_sources_parse(self) -> None:
        ast.parse(_MAIN.read_text(encoding="utf-8"))
        ast.parse(_UTILS.read_text(encoding="utf-8"))
        ast.parse(_HARNESS.read_text(encoding="utf-8"))

    def test_adapter_accepts_provided_testbench(self) -> None:
        text = _MAIN.read_text(encoding="utf-8")
        self.assertIn("--testbench_path", text)
        self.assertIn("Provided evaluation testbench", text)
        self.assertIn("shutil.copyfile(tb_path", text)

    def test_adapter_uses_named_target_profile(self) -> None:
        text = _MAIN.read_text(encoding="utf-8")
        self.assertIn("--target", text)
        self.assertIn("resolve_target_profile", text)
        self.assertIn("resolve_csynth_command(profile)", text)
        self.assertIn("target_profile=profile", text)

    def test_adapter_supports_openai_compatible_endpoint(self) -> None:
        main_text = _MAIN.read_text(encoding="utf-8")
        utils_text = _UTILS.read_text(encoding="utf-8")
        self.assertIn("--base_url", main_text)
        self.assertIn("--api_key_env", main_text)
        self.assertIn("base_url: str | None", utils_text)
        self.assertIn("api_key_env: str | None", utils_text)

    def test_evaluation_mode_disables_automatic_retry(self) -> None:
        text = _MAIN.read_text(encoding="utf-8")
        self.assertIn("--max_model_attempts", text)
        self.assertIn("max_attempts=args.max_model_attempts", text)
        self.assertIn("automatic_model_retry", text)

    def test_adapter_uses_effective_provider_parameters(self) -> None:
        main_text = _MAIN.read_text(encoding="utf-8")
        utils_text = _UTILS.read_text(encoding="utf-8")
        self.assertIn("--provider_reasoning_effort", main_text)
        self.assertIn("--max_output_tokens", main_text)
        self.assertIn("max_tokens: int | None", utils_text)
        self.assertIn('request["max_tokens"]', utils_text)

    def test_evaluation_logs_redact_raw_model_content(self) -> None:
        text = _MAIN.read_text(encoding="utf-8")
        self.assertIn("_EVALUATION_SAFE_LOG", text)
        self.assertIn("<REDACTED_CONTENT sha256=", text)
        self.assertIn('"raw_prompt_response_persisted": False', text)

    def test_evaluation_summary_is_incremental_and_atomic(self) -> None:
        text = _MAIN.read_text(encoding="utf-8")
        self.assertIn('write_evaluation_summary("model_call_started")', text)
        self.assertIn('write_evaluation_summary("csynth_started")', text)
        self.assertIn("os.replace(temporary, final)", text)

    def test_run_tb_reports_compile_and_csim_launches(self) -> None:
        text = _MAIN.read_text(encoding="utf-8")
        self.assertIn("harness_result.compile_calls", text)
        self.assertIn("harness_result.csim_calls", text)
        self.assertIn('"compile_calls": compile_calls', text)
        self.assertIn('"csim_calls": csim_calls', text)

    def test_selected_source_is_persisted(self) -> None:
        text = _MAIN.read_text(encoding="utf-8")
        self.assertIn("best_candidate.cpp", text)
        self.assertIn("best_candidate_path", text)

    def test_legacy_defaults_remain_available(self) -> None:
        text = _UTILS.read_text(encoding="utf-8")
        self.assertIn('api_key_env or "OPENAI_API_KEY"', text)
        self.assertIn("max_attempts: int | None = None", text)
        self.assertIn("retains the legacy retry-until-success", text)

    def test_evaluation_summary_records_physical_counts(self) -> None:
        text = _MAIN.read_text(encoding="utf-8")
        for field in (
            '"model_calls"',
            '"completed_rounds"',
            '"tool_calls"',
            '"compile_calls"',
            '"csim_calls"',
            '"csynth_calls"',
            '"wall_time_s"',
            '"provided_testbench"',
        ):
            self.assertIn(field, text)

    def test_evaluation_requires_independent_reference(self) -> None:
        text = _MAIN.read_text(encoding="utf-8")
        self.assertIn("--reference_path", text)
        self.assertIn("--reference_top_name", text)
        self.assertIn("--reference_path is required in evaluation mode", text)
        self.assertIn("shutil.copy(reference_path", text)

    def test_harness_compiles_with_synthesis_macro_and_separate_link(self) -> None:
        text = _HARNESS.read_text(encoding="utf-8")
        self.assertIn('"-D__SYNTHESIS__"', text)
        self.assertIn('"-c"', text)
        self.assertIn('"legacy_harness_link.log"', text)
        self.assertIn("compile_calls=2", text)

    def test_harness_enforces_strong_disjoint_symbols(self) -> None:
        text = _HARNESS.read_text(encoding="utf-8")
        self.assertIn('"nm", "-g", "--defined-only"', text)
        self.assertIn('symbols.get(name) == "T"', text)
        self.assertIn("reference_defines_candidate_top", text)
        self.assertIn("candidate_defines_reference_top", text)

    def test_invalid_model_output_is_no_retry_abstention(self) -> None:
        text = _MAIN.read_text(encoding="utf-8")
        self.assertIn("except ValueError", text)
        self.assertIn("MODEL_OUTPUT_ABSTAINED", text)
        self.assertIn("model_output_abstentions", text)
        self.assertIn('write_evaluation_summary("model_output_abstained")', text)

    def test_evaluation_summary_records_harness_evidence(self) -> None:
        text = _MAIN.read_text(encoding="utf-8")
        for field in (
            '"harness_contract_version"',
            '"reference_isolated"',
            '"harness_attempts"',
            '"harness_passes"',
            '"harness_failure_counts"',
            '"best_candidate_harness_validated"',
        ):
            self.assertIn(field, text)

    def test_harness_artifact_is_atomic_and_content_safe(self) -> None:
        text = _HARNESS.read_text(encoding="utf-8")
        self.assertIn("legacy_harness_result.json", text)
        self.assertIn("message_sha256", text)
        self.assertIn("message_chars", text)
        self.assertIn("os.replace(temporary, path)", text)


if __name__ == "__main__":
    unittest.main()
