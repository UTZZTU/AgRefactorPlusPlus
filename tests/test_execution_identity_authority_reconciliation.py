import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agrefactor.cli import build_parser
from agrefactor.product import run_source_command
from agrefactor.runtime import (
    build_execution_identity_bundle,
    build_rejected_execution_identity_bundle,
    validate_execution_identity_bundle,
)
from agrefactor.runtime.prompt_evidence import (
    get_model_prompt_evidence,
    record_model_prompt_call,
    reset_model_prompt_evidence,
)
import test_execution_identity as execution_identity_test_module


class ExecutionIdentityAuthorityReconciliationTests(unittest.TestCase):
    def _fixture(self, root: Path):
        case = execution_identity_test_module.ExecutionIdentityTests(
            methodName="test_canonical_hash_is_order_independent"
        )
        return case._fixture(root)

    def test_actual_prompt_registry_is_hash_only(self):
        reset_model_prompt_evidence()
        record_model_prompt_call(
            template_id="ag2:test:agent",
            template_version=1,
            system_message="SYSTEM SECRET-FREE TEXT",
            invocation={"message": "rendered user request"},
            provider_call_observed=True,
            metadata={"agent_name": "agent"},
        )
        payload = get_model_prompt_evidence()
        rendered = str(payload)
        self.assertEqual(payload["actual_call_count"], 1)
        self.assertNotIn("SYSTEM SECRET-FREE TEXT", rendered)
        self.assertNotIn("rendered user request", rendered)

    def test_prompt_registry_reset_is_run_scoped(self):
        reset_model_prompt_evidence()
        record_model_prompt_call(
            template_id="ag2:test:agent",
            template_version=1,
            system_message="system",
            invocation="user",
            provider_call_observed=True,
        )
        reset_model_prompt_evidence()
        payload = get_model_prompt_evidence()
        self.assertEqual(payload["actual_call_count"], 0)
        self.assertEqual(payload["calls"], [])

    def test_rendered_prompt_change_changes_cache_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            values = self._fixture(Path(directory))
            baseline = build_execution_identity_bundle(**values)
            changed = copy.deepcopy(values)
            changed["prompt_evidence"]["calls"][0][
                "invocation_sha256"
            ] = "8" * 64
            candidate = build_execution_identity_bundle(**changed)
            self.assertNotEqual(
                baseline["cache_identity_sha256"],
                candidate["cache_identity_sha256"],
            )

    def test_prompt_template_version_changes_cache_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            values = self._fixture(Path(directory))
            baseline = build_execution_identity_bundle(**values)
            changed = copy.deepcopy(values)
            changed["prompt_evidence"]["calls"][0][
                "template_version"
            ] = 2
            candidate = build_execution_identity_bundle(**changed)
            self.assertNotEqual(
                baseline["cache_identity_sha256"],
                candidate["cache_identity_sha256"],
            )

    def test_pending_suite_cannot_claim_accepted_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            values = self._fixture(Path(directory))
            values["suite_manifests"][0]["source"][
                "qualification_status"
            ] = "pending"
            bundle = build_execution_identity_bundle(**values)
            self.assertFalse(bundle["completeness"]["accepted_ready"])
            with self.assertRaisesRegex(ValueError, "qualified suite"):
                validate_execution_identity_bundle(
                    bundle,
                    require_accepted_ready=True,
                )

    def test_qualified_suite_coverage_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = build_execution_identity_bundle(
                **self._fixture(Path(directory))
            )
            suite = bundle["suites"][0]
            self.assertEqual(suite["qualification_status"], "qualified")
            self.assertEqual(suite["evaluation_status"], "passed")
            self.assertTrue(suite["coverage"]["compile_attempted"])

    def test_actual_approximate_cost_quality_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = build_execution_identity_bundle(
                **self._fixture(Path(directory))
            )
            pricing = bundle["model"]["pricing"]
            self.assertEqual(
                pricing["cost_estimation_quality"],
                "approximate",
            )
            self.assertFalse(pricing["is_invoice"])

    def test_actual_unavailable_cost_quality_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            values = self._fixture(Path(directory))
            values["model_manifest"]["actual_cost_estimation"] = {
                "schema_version": 1,
                "quality": "unavailable",
                "observations": [],
                "amounts_by_currency": {},
                "is_invoice": False,
            }
            bundle = build_execution_identity_bundle(**values)
            self.assertEqual(
                bundle["model"]["pricing"]["cost_estimation_quality"],
                "unavailable",
            )

    def test_safety_ceiling_rejection_identity_is_structured(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = self._fixture(root)
            bundle = build_rejected_execution_identity_bundle(
                run_id="rejected-run",
                source_path=values["source_path"],
                top_function=values["top_function"],
                normalized_task=values["normalized_task"],
                model_manifest=values["model_manifest"],
                target_manifest=values["target_manifest"],
                test_source_plan={"public": "auto", "hidden": "auto"},
                budget_request={
                    "system_defaults": {"max_llm_calls": 64},
                    "system_safety_ceilings": {"max_llm_calls": 256},
                    "user_requested": {"max_llm_calls": 257},
                    "effective_hard_limits": None,
                },
                rejection={
                    "kind": "safety_ceiling_exceeded",
                    "resource": "max_llm_calls",
                    "user_requested": 257,
                    "system_safety_ceiling": 256,
                },
                artifact_schema_version=1,
            )
            self.assertEqual(bundle["execution_status"], "request_rejected")
            self.assertEqual(
                bundle["request_rejection"]["resource"],
                "max_llm_calls",
            )
            self.assertFalse(bundle["completeness"]["accepted_ready"])
            self.assertNotIn("api_key\":", str(bundle))

    def test_source_command_persists_safety_ceiling_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "kernel.cpp"
            source.write_text(
                'extern "C" int top(int x) { return x; }\n',
                encoding="utf-8",
            )
            args = build_parser().parse_args(
                [
                    "refactor",
                    str(source),
                    "--top",
                    "top",
                    "--model",
                    "deepseek-v4-flash",
                    "--max-llm-calls",
                    "257",
                    "--run-id",
                    "ceiling-rejected",
                ]
            )
            run_root = root / "runs"
            work_root = root / "work"
            with patch.dict(
                os.environ,
                {
                    "AGREFACTOR_RUN_ROOT": str(run_root),
                    "AGREFACTOR_WORK_ROOT": str(work_root),
                },
                clear=False,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "rejection artifacts",
                ):
                    run_source_command(args)
            artifact = run_root / "source_run_ceiling-rejected"
            identity = json.loads(
                (artifact / "execution_identity.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                identity["execution_status"],
                "request_rejected",
            )
            self.assertTrue(
                (artifact / "request_rejection.json").is_file()
            )
            self.assertTrue(
                (artifact / "run_artifact_manifest.json").is_file()
            )



if __name__ == "__main__":
    unittest.main()
