from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


_TOOL_PATH = Path(__file__).resolve().parents[1] / "tools" / "stage3_s38_evaluate.py"
_SPEC = importlib.util.spec_from_file_location("stage3_s38_evaluate", _TOOL_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_TOOL = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_TOOL)


class Stage3S38ToolTests(unittest.TestCase):
    def test_expected_baseline_is_s37_closure(self) -> None:
        self.assertEqual(
            _TOOL.EXPECTED_BASELINE,
            "84b6fac0a00469fc9651f5f6553b50febedb21c7",
        )

    def test_common_product_args_share_budget(self) -> None:
        from agrefactor.evaluation.stage3_s38 import S38Protocol

        args = _TOOL.common_product_args(
            S38Protocol(model="m"), Path("/tmp/artifacts"), "run"
        )
        for option in (
            "--max-llm-calls",
            "--max-tool-calls",
            "--max-compile-calls",
            "--max-csim-calls",
            "--max-csynth-calls",
            "--max-wall-time-s",
        ):
            self.assertIn(option, args)
        self.assertIn("--json", args)

    def test_product_artifact_parser_observes_best_candidate(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "optimize" / "optimizer").mkdir(parents=True)
            (root / "full_result.json").write_text(
                json.dumps(
                    {
                        "status": "succeeded",
                        "phases": [
                            {
                                "phase": "optimize",
                                "metadata": {"accepted": True},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (root / "stage3_execution_identity.json").write_text(
                json.dumps(
                    {
                        "state": {
                            "best_correct_candidate_id": "cand-1",
                            "best_ppa_candidate_id": "cand-1",
                            "executed_candidate_count": 1,
                            "terminal_status": "accepted_improved",
                        },
                        "terminal_status": "accepted_improved",
                        "candidate_index": {
                            "baseline": {
                                "status": "accepted",
                                "ppa": {"latency_cycles_max": 20},
                            },
                            "cand-1": {
                                "status": "accepted",
                                "ppa": {
                                    "latency_cycles_max": 12,
                                    "initiation_interval_max": 1,
                                },
                            },
                        },
                        "budget_usage": {
                            "llm_calls": 4,
                            "tool_calls": 12,
                            "compile_calls": 6,
                            "csim_calls": 4,
                            "csynth_calls": 2,
                        },
                    }
                ),
                encoding="utf-8",
            )
            parsed = _TOOL.parse_product_artifacts(root)
            self.assertTrue(parsed["accepted"])
            self.assertEqual(parsed["ppa"]["latency_cycles_max"], 12)
            self.assertEqual(parsed["llm_calls"], 4)
            self.assertEqual(parsed["baseline_ppa"]["latency_cycles_max"], 20)
            self.assertTrue(parsed["best_correct_protected"])

    def test_product_parser_does_not_treat_missing_artifacts_as_success(self) -> None:
        with TemporaryDirectory() as directory:
            parsed = _TOOL.parse_product_artifacts(Path(directory))
            self.assertFalse(parsed["accepted"])
            self.assertEqual(parsed["failure_class"], "candidate")

    def test_normalize_ppa_accepts_nested_metrics(self) -> None:
        ppa = _TOOL.normalize_ppa(
            {
                "metrics": {
                    "latency_cycles_max": 20,
                    "initiation_interval_max": 2,
                    "objective_feasible": True,
                },
                "resources_used": {"LUT": 10},
            }
        )
        self.assertEqual(ppa["latency_cycles_max"], 20)
        self.assertEqual(ppa["resources_used"], {"LUT": 10})

    def test_redact_argv_hides_explicit_secret_values(self) -> None:
        self.assertEqual(
            _TOOL.redact_argv(["cmd", "--api-key", "secret", "--model", "m"]),
            ["cmd", "--api-key", "<REDACTED>", "--model", "m"],
        )

    def test_safe_text_redacts_key_prefix(self) -> None:
        self.assertIn("<REDACTED>", _TOOL.safe_text("failure sk-secretvalue"))

    def test_exception_classification_distinguishes_candidate(self) -> None:
        self.assertEqual(
            _TOOL.classify_exception(ValueError("model candidate invalid")),
            "candidate",
        )
        self.assertEqual(
            _TOOL.classify_exception(RuntimeError("Vitis executable not found")),
            "infrastructure",
        )

    def test_process_failure_classification_uses_transport_evidence(self) -> None:
        from subprocess import CompletedProcess

        infra = CompletedProcess(
            ["cmd"], 1, stdout="", stderr="Missing API credential environment variable"
        )
        candidate = CompletedProcess(
            ["cmd"], 1, stdout="candidate response invalid", stderr=""
        )
        self.assertEqual(_TOOL.classify_process_failure(infra), "infrastructure")
        self.assertEqual(_TOOL.classify_process_failure(candidate), "candidate")

    def test_budget_check_covers_all_physical_counters(self) -> None:
        from agrefactor.evaluation.stage3_s38 import S38Protocol

        protocol = S38Protocol(model="m")
        self.assertFalse(
            _TOOL.exceeds_budget(
                {
                    "llm_calls": 14,
                    "tool_calls": 54,
                    "compile_calls": 20,
                    "csim_calls": 18,
                    "csynth_calls": 16,
                },
                protocol,
            )
        )
        self.assertTrue(
            _TOOL.exceeds_budget({"csynth_calls": 17}, protocol)
        )

    def test_mapping_sha_is_deterministic(self) -> None:
        first = _TOOL.mapping_sha256({"b": 2, "a": 1})
        second = _TOOL.mapping_sha256({"a": 1, "b": 2})
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_resume_reuses_non_infrastructure_record(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            run_root = root / "runs" / "run-1"
            run_root.mkdir(parents=True)
            (run_root / "marker.txt").write_text("kept", encoding="utf-8")
            action = _TOOL.prepare_run_root(
                evaluation_root=root,
                run_root=run_root,
                resume=True,
                existing_record={"failure_class": "candidate"},
            )
            self.assertEqual(action, "reuse_record")
            self.assertTrue((run_root / "marker.txt").is_file())
            self.assertFalse((root / "failed_attempts").exists())

    def test_resume_archives_retryable_or_interrupted_attempt(self) -> None:
        for existing_record, expected in (
            ({"failure_class": "infrastructure"}, "retry_infrastructure"),
            (None, "retry_interrupted"),
        ):
            with self.subTest(expected=expected), TemporaryDirectory() as directory:
                root = Path(directory)
                run_root = root / "runs" / "run-1"
                run_root.mkdir(parents=True)
                (run_root / "marker.txt").write_text("archived", encoding="utf-8")
                action = _TOOL.prepare_run_root(
                    evaluation_root=root,
                    run_root=run_root,
                    resume=True,
                    existing_record=existing_record,
                )
                self.assertEqual(action, expected)
                self.assertFalse(run_root.exists())
                archives = list((root / "failed_attempts").iterdir())
                self.assertEqual(len(archives), 1)
                self.assertEqual(
                    (archives[0] / "marker.txt").read_text(encoding="utf-8"),
                    "archived",
                )

    def test_tool_passes_effective_legacy_model_parameters(self) -> None:
        text = _TOOL_PATH.read_text(encoding="utf-8")
        self.assertIn("--provider_reasoning_effort", text)
        self.assertIn("--max_output_tokens", text)
        self.assertIn("effective.parameters.get", text)

    def test_legacy_uses_one_shared_wall_deadline(self) -> None:
        text = _TOOL_PATH.read_text(encoding="utf-8")
        self.assertIn("deadline = time.monotonic() + protocol.max_wall_time_s", text)
        self.assertGreaterEqual(text.count("max_wall_time_s=remaining_wall()"), 2)

    def test_tool_source_requires_legacy_post_qualification(self) -> None:
        text = _TOOL_PATH.read_text(encoding="utf-8")
        self.assertGreaterEqual(text.count("qualify_external_candidate("), 2)
        self.assertIn("baseline_qualification", text)
        self.assertIn("final_qualification", text)
        self.assertIn("hidden_exposed_to_model", text)


    def test_observer_contract_failure_is_typed_infrastructure(self) -> None:
        message = (
            "qualification stage order mismatch: "
            "['source', 'preflight', 'public', 'csynth', "
            "'hidden', 'ppa', 'feasibility']"
        )
        self.assertEqual(
            _TOOL.classify_exception(
                _TOOL.S38QualificationObserverError(message)
            ),
            "infrastructure",
        )
        self.assertEqual(
            _TOOL.classify_exception(RuntimeError(message)),
            "candidate",
        )

    def test_forced_retry_archives_candidate_record(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            run_root = root / "runs" / "run-1"
            run_root.mkdir(parents=True)
            (run_root / "marker.txt").write_text("archived", encoding="utf-8")
            action = _TOOL.prepare_run_root(
                evaluation_root=root,
                run_root=run_root,
                resume=True,
                existing_record={"failure_class": "candidate"},
                force_retry=True,
            )
            self.assertEqual(action, "retry_forced")
            self.assertFalse(run_root.exists())
            archives = list((root / "failed_attempts").iterdir())
            self.assertEqual(len(archives), 1)
            self.assertEqual(
                (archives[0] / "marker.txt").read_text(encoding="utf-8"),
                "archived",
            )

    def test_base_record_uses_run_record_schema_v3(self) -> None:
        from agrefactor.evaluation.stage3_s38 import (
            S38Arm,
            S38Protocol,
            S38RunSpec,
        )

        record = _TOOL.base_record(
            S38Protocol(model="m"),
            S38RunSpec(
                sequence=1,
                case_id="array-map",
                repeat_index=1,
                arm=S38Arm.SIMPLE_ITER,
                run_id="s38-array-map-r1-simple-iter",
            ),
        )
        self.assertEqual(record["schema_version"], 3)

    def test_tool_supports_targeted_legacy_retry(self) -> None:
        text = _TOOL_PATH.read_text(encoding="utf-8")
        self.assertIn("--retry-arm", text)
        self.assertIn("force_retry", text)
        self.assertIn("LEGACY_SIMPLE_ITER_COMPARISON_EXECUTED", text)


    def test_tool_supports_retrying_only_invalid_legacy_records(self) -> None:
        text = _TOOL_PATH.read_text(encoding="utf-8")
        self.assertIn("--retry-invalid-legacy", text)
        self.assertIn("legacy_record_has_execution_evidence(existing_record)", text)

    def test_tool_passes_isolated_reference_to_legacy(self) -> None:
        text = _TOOL_PATH.read_text(encoding="utf-8")
        self.assertIn('"--reference_path"', text)
        self.assertIn('material["reference"]', text)
        self.assertIn('"--reference_top_name"', text)
        self.assertIn('material["reference_top_function"]', text)

    def test_tool_preserves_final_qualification_status(self) -> None:
        text = _TOOL_PATH.read_text(encoding="utf-8")
        self.assertIn("final_qualification_status", text)
        self.assertIn("final_qualification_review_required", text)
        self.assertIn("final_qualification_failure_owner", text)
        self.assertIn('failure_class = "review"', text)

    def test_tool_classifies_no_candidate_causes(self) -> None:
        text = _TOOL_PATH.read_text(encoding="utf-8")
        self.assertIn("no_parseable_legacy_candidate", text)
        self.assertIn("no_testbench_passing_candidate", text)
        self.assertIn("no_resource_feasible_candidate", text)
        self.assertIn("no_synthesizable_legacy_candidate", text)

    def test_tool_does_not_claim_stable_superiority(self) -> None:
        text = _TOOL_PATH.read_text(encoding="utf-8")
        self.assertIn("STABLE_SUPERIORITY_CLAIMED=false", text)


if __name__ == "__main__":
    unittest.main()
