import json
from pathlib import Path
import tempfile
import unittest

from agrefactor.config import RunMode, TaskSpec
from agrefactor.runtime import (
    BudgetLimits,
    PhaseResult,
    PhaseStatus,
    RunPhase,
    UnifiedRunner,
    build_execution_identity_bundle,
    canonical_json_sha256,
    execution_identity_summary,
    file_sha256,
    finalize_execution_identity_bundle,
    validate_execution_identity_bundle,
    write_execution_identity_bundle,
)


class ExecutionIdentityTests(unittest.TestCase):
    def _fixture(self, root: Path, **overrides):
        source = root / "kernel.cpp"
        source.write_text('extern "C" int top(int x) { return x; }\n', encoding="utf-8")
        initial = root / "initial.cpp"
        initial.write_text('extern "C" int top_hls(int x) { return x; }\n', encoding="utf-8")
        final = root / "final.cpp"
        final.write_text('extern "C" int top_hls(int x) { return x + 1; }\n', encoding="utf-8")
        testbench = root / "public.cpp"
        testbench.write_text("int main() { return 0; }\n", encoding="utf-8")
        invocation_root = root / "tool"
        invocation_root.mkdir()
        (invocation_root / "csynth_invocation.json").write_text(
            json.dumps(
                {
                    "profile_name": "vitis-2023.2-default",
                    "executable": "vitis-run",
                    "resolved_executable": None,
                    "settings_path": None,
                    "command_source": "builtin_default",
                    "toolchain_version_verification": {
                        "requested": "2023.2",
                        "actual": "2023.2",
                        "status": "matched",
                        "probe_source": "resolved_executable",
                        "stdout": "Vitis v2023.2",
                        "stderr": "",
                    },
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        model = {
            "logical_model_name": "deepseek-v4-flash",
            "provider_name": "openai-compatible",
            "model_id": "deepseek-v4-flash",
            "family_profile_name": "deepseek",
            "base_url": "https://api.deepseek.com",
            "api_key_env": "DEEPSEEK_API_KEY",
            "effective_parameters": {"reasoning_effort": "high"},
            "pricing_snapshot": {
                "pricing_snapshot_sha256": "1" * 64,
                "provider": "deepseek",
                "model_id": "deepseek-v4-flash",
                "model_version": "v4-flash",
                "currency": "CNY",
                "verification_status": "official_verified",
            },
            "allow_approximate_cost": True,
            "actual_cost_estimation": {
                "schema_version": 1,
                "quality": "approximate",
                "observations": [
                    {
                        "quality": "approximate",
                        "amount": "0.01",
                        "currency": "CNY",
                        "pricing_snapshot_sha256": "1" * 64,
                    }
                ],
                "amounts_by_currency": {"CNY": "0.01"},
                "is_invoice": False,
            },
        }
        target = {
            "schema_version": 2,
            "profile": {
                "name": "vitis-2023.2-default",
                "toolchain": "vitis_hls",
                "toolchain_version": "2023.2",
                "device": "xcu200-fsgd2104-2-e",
                "clock_period_ns": 5.0,
                "compile_flags": ["-D XILINX"],
                "executable": "vitis-run",
                "settings_path": None,
                "parser_profile": "vitis-hls-2023.2",
                "resource_limits": {},
            },
            "field_provenance": {"device": "committed_profile"},
        }
        budget = {
            "schema_version": 1,
            "system_defaults": {
                "max_llm_calls": 32,
                "max_tool_calls": 64,
                "max_compile_calls": 24,
                "max_csim_calls": 16,
                "max_csynth_calls": 8,
                "max_wall_time_s": 1800.0,
            },
            "system_safety_ceilings": {
                "max_llm_calls": 128,
                "max_tool_calls": 256,
                "max_compile_calls": 96,
                "max_csim_calls": 64,
                "max_csynth_calls": 32,
                "max_wall_time_s": 7200.0,
            },
            "user_requested": {
                "max_llm_calls": None,
                "max_tool_calls": None,
                "max_compile_calls": None,
                "max_csim_calls": None,
                "max_csynth_calls": None,
                "max_wall_time_s": None,
            },
            "effective_hard_limits": {
                "max_llm_calls": 32,
                "max_tool_calls": 64,
                "max_compile_calls": 24,
                "max_csim_calls": 16,
                "max_csynth_calls": 8,
                "max_wall_time_s": 1800.0,
            },
            "budget_source_per_field": {
                "max_llm_calls": "system_default",
                "max_tool_calls": "system_default",
                "max_compile_calls": "system_default",
                "max_csim_calls": "system_default",
                "max_csynth_calls": "system_default",
                "max_wall_time_s": "system_default",
            },
            "soft_usage_budgets": {
                "token_budget": 1000,
                "cost_budget": "1",
                "currency": "CNY",
                "enforcement": "observed_only",
                "blocking": False,
            },
        }
        usage = {
            "llm_calls": 3,
            "tool_calls": 4,
            "compile_calls": 1,
            "csim_calls": 1,
            "csynth_calls": 1,
            "tokens": 200,
            "cost_usd": 0.0,
            "costs_by_currency": {"CNY": "0.01"},
            "elapsed_s": 4.0,
        }
        values = {
            "run_id": "execution-test",
            "source_path": source,
            "top_function": "top",
            "normalized_task": {
                "task_id": "execution-test.formal",
                "kernel_path": str(source),
                "kernel_name": "top_hls",
                "mode": "refactor",
                "target": target["profile"],
            },
            "model_manifest": model,
            "prompt_hashes": {
                "initial_generation_request": "2" * 64,
                "formal_validation_request": "3" * 64,
            },
            "target_manifest": target,
            "prompt_evidence": {
                "schema_version": 1,
                "actual_call_count": 1,
                "aggregate_sha256": "4" * 64,
                "calls": [
                    {
                        "schema_version": 1,
                        "call_index": 1,
                        "template_id": "ag2:test:refactor",
                        "template_version": 1,
                        "system_message_sha256": "5" * 64,
                        "invocation_sha256": "6" * 64,
                        "message_sequence_sha256": "7" * 64,
                        "provider_call_observed": True,
                        "metadata": {"agent_name": "refactor"},
                    }
                ],
            },
            "suite_manifests": [
                {
                    "suite_id": "public-001",
                    "suite_version": "1",
                    "split": "public",
                    "testbench_path": str(testbench),
                    "evaluation_status": "passed",
                    "source": {
                        "source_kind": "provided",
                        "expected_content_sha256": file_sha256(testbench),
                        "qualification_status": "qualified",
                        "coverage": {
                            "compile_attempted": True,
                            "simulation_attempted": True,
                        },
                    },
                }
            ],
            "initial_candidate_path": initial,
            "final_candidate_path": final,
            "budget_contract": budget,
            "budget_usage": usage,
            "artifact_schema_version": 1,
            "execution_status": "accepted",
            "toolchain_evidence_root": invocation_root,
        }
        values.update(overrides)
        return values

    def test_canonical_hash_is_order_independent(self):
        self.assertEqual(
            canonical_json_sha256({"a": 1, "b": [2, 3]}),
            canonical_json_sha256({"b": [2, 3], "a": 1}),
        )

    def test_credentials_are_rejected_but_env_name_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = self._fixture(root)
            bundle = build_execution_identity_bundle(**values)
            self.assertEqual(
                bundle["model"]["value"]["api_key_env"],
                "DEEPSEEK_API_KEY",
            )
            bad = dict(values)
            bad["model_manifest"] = {"api_key": "secret"}
            with self.assertRaisesRegex(ValueError, "credential"):
                build_execution_identity_bundle(**bad)

    def test_complete_bundle_validates_for_accepted_run(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = build_execution_identity_bundle(
                **self._fixture(Path(directory))
            )
            validate_execution_identity_bundle(
                bundle,
                require_accepted_ready=True,
            )
            self.assertTrue(bundle["completeness"]["accepted_ready"])
            self.assertEqual(len(bundle["cache_identity_sha256"]), 64)

    def test_source_change_changes_cache_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_values = self._fixture(root)
            first = build_execution_identity_bundle(**first_values)
            Path(first_values["source_path"]).write_text(
                'extern "C" int top(int x) { return x + 1; }\n',
                encoding="utf-8",
            )
            second = build_execution_identity_bundle(**first_values)
            self.assertNotEqual(
                first["cache_identity_sha256"],
                second["cache_identity_sha256"],
            )

    def test_model_pricing_and_budget_changes_change_cache_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = self._fixture(root)
            baseline = build_execution_identity_bundle(**values)
            changed_model = json.loads(json.dumps(values["model_manifest"]))
            changed_model["pricing_snapshot"]["pricing_snapshot_sha256"] = "9" * 64
            changed_budget = json.loads(json.dumps(values["budget_contract"]))
            changed_budget["effective_hard_limits"]["max_llm_calls"] = 31
            changed_budget["user_requested"]["max_llm_calls"] = 31
            changed_budget["budget_source_per_field"]["max_llm_calls"] = "user_requested"
            for replacement in (
                {"model_manifest": changed_model},
                {"budget_contract": changed_budget},
            ):
                current = dict(values)
                current.update(replacement)
                candidate = build_execution_identity_bundle(**current)
                self.assertNotEqual(
                    baseline["cache_identity_sha256"],
                    candidate["cache_identity_sha256"],
                )

    def test_target_or_actual_toolchain_change_changes_cache_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = self._fixture(root)
            baseline = build_execution_identity_bundle(**values)
            changed_target = json.loads(json.dumps(values["target_manifest"]))
            changed_target["profile"]["clock_period_ns"] = 4.0
            target_values = dict(values)
            target_values["target_manifest"] = changed_target
            target_bundle = build_execution_identity_bundle(**target_values)
            self.assertNotEqual(
                baseline["cache_identity_sha256"],
                target_bundle["cache_identity_sha256"],
            )
            invocation = Path(values["toolchain_evidence_root"]) / "csynth_invocation.json"
            payload = json.loads(invocation.read_text(encoding="utf-8"))
            payload["toolchain_version_verification"]["actual"] = "2024.1"
            invocation.write_text(json.dumps(payload), encoding="utf-8")
            tool_bundle = build_execution_identity_bundle(**values)
            self.assertNotEqual(
                baseline["cache_identity_sha256"],
                tool_bundle["cache_identity_sha256"],
            )

    def test_suite_hash_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            values = self._fixture(Path(directory))
            values["suite_manifests"][0]["source"][
                "expected_content_sha256"
            ] = "f" * 64
            with self.assertRaisesRegex(ValueError, "does not match"):
                build_execution_identity_bundle(**values)

    def test_incomplete_bundle_cannot_claim_accepted_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            values = self._fixture(
                Path(directory),
                final_candidate_path=None,
                budget_usage=None,
                toolchain_evidence_root=None,
            )
            bundle = build_execution_identity_bundle(**values)
            self.assertFalse(bundle["completeness"]["accepted_ready"])
            with self.assertRaisesRegex(ValueError, "incomplete"):
                validate_execution_identity_bundle(
                    bundle,
                    require_accepted_ready=True,
                )

    def test_atomic_write_and_runtime_finalization_preserve_cache_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = build_execution_identity_bundle(**self._fixture(root))
            path = root / "artifacts" / "execution_identity.json"
            write_execution_identity_bundle(path, bundle)
            finalized = finalize_execution_identity_bundle(
                bundle,
                budget_usage={
                    **bundle["budget"]["usage"],
                    "tokens": 999,
                },
                execution_status="succeeded",
            )
            self.assertEqual(
                bundle["cache_identity_sha256"],
                finalized["cache_identity_sha256"],
            )
            self.assertNotEqual(bundle["execution_id"], finalized["execution_id"])
            self.assertFalse(tuple(path.parent.glob("*.tmp")))
            summary = execution_identity_summary(finalized)
            self.assertNotIn("source", summary)

    def test_unified_runner_promotes_identity_to_result_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = self._fixture(root)
            artifact_root = root / "run-artifacts"
            source = values["source_path"]
            task = TaskSpec(
                task_id="runner-task",
                kernel_path=str(source),
                kernel_name="top",
                mode=RunMode.REFACTOR,
            )

            def handler(context):
                context.budget.consume(llm_calls=1)
                bundle = build_execution_identity_bundle(**values)
                write_execution_identity_bundle(
                    artifact_root / "execution_identity.json",
                    bundle,
                )
                return PhaseResult(
                    phase=RunPhase.REFACTOR,
                    status=PhaseStatus.SUCCEEDED,
                    metadata={
                        "execution_identity": execution_identity_summary(bundle)
                    },
                )

            runner = UnifiedRunner(
                {RunPhase.REFACTOR: handler},
                budget_limits=BudgetLimits(max_llm_calls=2),
            )
            result = runner.run(
                task,
                run_id="runner-execution",
                artifact_root=artifact_root,
            )
            self.assertIn("execution_identity", result.metadata)
            manifest = json.loads(
                (artifact_root / "run_artifact_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                manifest["execution_identity"]["execution_id"],
                result.metadata["execution_identity"]["execution_id"],
            )
            stored = json.loads(
                (artifact_root / "execution_identity.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(stored["budget"]["usage"]["llm_calls"], 1)


if __name__ == "__main__":
    unittest.main()
