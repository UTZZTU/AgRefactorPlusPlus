from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

from agrefactor.models import ModelArtifactKind, resolve_model_runtime
from agrefactor.product.source_bootstrap import (
    SourceBootstrapPhase,
    _build_generation_extraction_outcome,
)
from agrefactor.runtime import (
    PhaseStatus,
    record_model_prompt_call,
    reset_model_prompt_evidence,
)


class P40FPrR3GenerationOutputAccountingTests(unittest.TestCase):
    def tearDown(self):
        reset_model_prompt_evidence()

    def test_v4_flash_candidate_default_is_150k(self):
        s=resolve_model_runtime("deepseek-v4-flash")
        self.assertEqual(s.effective_config.effective_parameters["max_tokens"],150_000)

    def test_v4_flash_candidate_repair_default_is_150k(self):
        s=resolve_model_runtime("deepseek-v4-flash")
        c=s.registry.resolve_effective_config(
            "deepseek-v4-flash",
            artifact_kind=ModelArtifactKind.CANDIDATE_REPAIR,
        )
        self.assertEqual(c.effective_parameters["max_tokens"],150_000)

    def test_v4_flash_testbench_default_remains_32768(self):
        s=resolve_model_runtime("deepseek-v4-flash")
        c=s.registry.resolve_effective_config(
            "deepseek-v4-flash",
            artifact_kind=ModelArtifactKind.TESTBENCH,
        )
        self.assertEqual(c.effective_parameters["max_tokens"],32_768)

    def test_v4_flash_300k_explicit_is_allowed(self):
        s=resolve_model_runtime(
            "deepseek-v4-flash",
            parameters={"max_tokens":300_000},
        )
        self.assertEqual(s.effective_config.effective_parameters["max_tokens"],300_000)

    def test_v4_flash_above_300k_fails_closed(self):
        with self.assertRaises(ValueError):
            resolve_model_runtime(
                "deepseek-v4-flash",
                parameters={"max_tokens":300_001},
            )

    def test_other_deepseek_keeps_family_policy(self):
        s=resolve_model_runtime("deepseek-other")
        self.assertEqual(s.effective_config.effective_parameters["max_tokens"],32_768)
        with self.assertRaises(ValueError):
            resolve_model_runtime(
                "deepseek-other",
                parameters={"max_tokens":150_000},
            )

    def test_medium_effort_translation_is_unchanged(self):
        s=resolve_model_runtime(
            "deepseek-v4-flash",
            reasoning_effort="medium",
        )
        self.assertEqual(s.effective_config.requested_reasoning_effort,"medium")
        self.assertEqual(
            s.effective_config.effective_parameters["reasoning_effort"],
            "high",
        )

    def test_saturation_alone_is_not_truncation_authority(self):
        o=_build_generation_extraction_outcome(
            raw_result=(True,{"generation_response_evidence":{"completion_tokens":150_000}}),
            error_message="generation-only backend returned no candidate_code",
            model_id="deepseek-v4-flash",
            effective_max_output_tokens=150_000,
            provider_call_count=1,
        )
        self.assertEqual(o["reason_code"],"candidate_extraction_failed")
        self.assertTrue(o["output_limit_saturation_observed"])
        self.assertFalse(o["truncation_proven"])

    def test_finish_reason_length_proves_truncation(self):
        o=_build_generation_extraction_outcome(
            raw_result=(True,{"generation_response_evidence":{"finish_reason":"length","completion_tokens":150_000}}),
            error_message="generation-only backend returned no candidate_code",
            model_id="deepseek-v4-flash",
            effective_max_output_tokens=150_000,
            provider_call_count=1,
        )
        self.assertEqual(o["reason_code"],"response_truncated")
        self.assertTrue(o["truncation_proven"])

    def test_explicit_empty_content_is_typed(self):
        o=_build_generation_extraction_outcome(
            raw_result=(True,{"generation_response_evidence":{"content_present":False}}),
            error_message="generation-only backend returned no candidate_code",
            model_id="deepseek-v4-flash",
            effective_max_output_tokens=150_000,
            provider_call_count=1,
        )
        self.assertEqual(o["reason_code"],"empty_candidate")

    def test_explicit_code_block_missing_is_typed(self):
        o=_build_generation_extraction_outcome(
            raw_result=(True,{"generation_response_evidence":{"content_present":True,"extraction_status":"code_block_missing"}}),
            error_message="generation-only backend returned no candidate_code",
            model_id="deepseek-v4-flash",
            effective_max_output_tokens=150_000,
            provider_call_count=1,
        )
        self.assertEqual(o["reason_code"],"code_block_missing")

    def test_provider_failure_requires_explicit_terminal_kind(self):
        o=_build_generation_extraction_outcome(
            raw_result=(False,{"generation_response_evidence":{"terminal_kind":"provider_failure"}}),
            error_message="provider request failed",
            model_id="deepseek-v4-flash",
            effective_max_output_tokens=150_000,
            provider_call_count=1,
        )
        self.assertEqual(o["reason_code"],"provider_failure")

    def test_failure_handler_refreshes_provider_call_evidence(self):
        reset_model_prompt_evidence()
        for idx in (1,2):
            record_model_prompt_call(
                template_id="pr-r3.synthetic",
                template_version=1,
                system_message="system",
                invocation={"index":idx},
                provider_call_observed=True,
                metadata={"stage":"candidate"},
            )
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            phase=object.__new__(SourceBootstrapPhase)
            phase._request=SimpleNamespace(
                effective_model_config=SimpleNamespace(
                    model_id="deepseek-v4-flash",
                    effective_parameters={"max_tokens":150_000},
                )
            )
            phase._public_testbench_prompt_evidence=()
            phase._last_formal_phase=None
            captured={}
            def fake_identity(**kwargs):
                captured.update(kwargs)
                return {
                    "model_calls":phase._collect_prompt_evidence(),
                    "status":"failed",
                }
            phase._write_execution_identity=fake_identity
            result=phase._generation_extraction_failure_result(
                context=SimpleNamespace(
                    run_id="synthetic-run",
                    task="synthetic-task",
                    root_record=None,
                ),
                bootstrap_root=root,
                raw_result=(True,{"generation_response_evidence":{"completion_tokens":150_000}}),
                error=ValueError("generation-only backend returned no candidate_code"),
            )
            self.assertEqual(result.status,PhaseStatus.FAILED)
            payload=json.loads(
                (root/"generation_extraction_outcome.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["provider_call_count"],2)
            self.assertEqual(
                result.metadata["execution_identity"]["model_calls"]["actual_call_count"],
                2,
            )
            self.assertEqual(captured["execution_status"],"failed")

    def test_candidate_extraction_failure_emits_one_safe_pre_formal_event(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            phase = object.__new__(SourceBootstrapPhase)
            phase._request = SimpleNamespace(
                effective_model_config=SimpleNamespace(
                    model_id="test-model",
                    effective_parameters={},
                )
            )
            phase._collect_prompt_evidence = lambda: {
                "actual_call_count": 0
            }
            phase._write_execution_identity = lambda **_kwargs: {
                "status": "failed"
            }
            context = SimpleNamespace(
                run_id="run-1",
                task="task",
                root_record=None,
                trace=SimpleNamespace(record=Mock()),
            )
            result = phase._generation_extraction_failure_result(
                context=context,
                bootstrap_root=root,
                raw_result=(
                    True,
                    {"generation_response_evidence": {}},
                ),
                error=ValueError(
                    "generation-only backend returned no candidate_code"
                ),
            )

            event = result.metadata["pre_formal_failure_event"]
            self.assertEqual(event["run_id"], "run-1")
            self.assertEqual(event["final_status"], "failed")
            self.assertFalse(event["formal_validation_started"])
            self.assertFalse(event["candidate_artifact_available"])
            context.trace.record.assert_not_called()
            self.assertNotIn("candidate_code", event)
            persisted = json.loads(
                (root / "candidate_generation_failure.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(persisted, event)

    def test_public_abi_extraction_failure_is_not_candidate_failure(self):
        with tempfile.TemporaryDirectory() as td:
            phase = object.__new__(SourceBootstrapPhase)
            phase._request = SimpleNamespace(
                effective_model_config=SimpleNamespace(
                    model_id="test-model",
                    effective_parameters={},
                )
            )
            phase._collect_prompt_evidence = lambda: {
                "actual_call_count": 0
            }
            phase._write_execution_identity = lambda **_kwargs: {
                "status": "failed"
            }
            result = phase._generation_extraction_failure_result(
                context=SimpleNamespace(
                    run_id="run-2",
                    task="task",
                    trace=SimpleNamespace(record=Mock()),
                ),
                bootstrap_root=Path(td),
                raw_result=(True, {}),
                error=ValueError(
                    "generation-only backend returned no frozen Public ABI"
                ),
            )

            self.assertNotIn(
                "pre_formal_failure_event", result.metadata
            )
            self.assertFalse(result.metadata["formal_validation_started"])


if __name__=="__main__":
    unittest.main()
