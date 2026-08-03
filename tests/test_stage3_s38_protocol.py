from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agrefactor.evaluation.stage3_s38 import (
    DEFAULT_S38_CASE_IDS,
    S38Arm,
    S38Protocol,
    S38RunSpec,
    S38_REPORT_SCHEMA_VERSION,
    S38_RUN_RECORD_SCHEMA_VERSION,
    S38_SUPPORTED_RUN_RECORD_READ_VERSIONS,
    _REQUIRED_STAGE_ORDER,
    aggregate_s38_records,
    build_s38_run_matrix,
    materialize_s38_corpus,
    legacy_record_has_execution_evidence,
)


class Stage3S38ProtocolTests(unittest.TestCase):

    def _record(self, protocol, item, **overrides):
        value = {
            "schema_version": (
                S38_RUN_RECORD_SCHEMA_VERSION
                if item.arm is S38Arm.SIMPLE_ITER
                else 1
            ),
            "run_id": item.run_id,
            "case_id": item.case_id,
            "repeat_index": item.repeat_index,
            "arm": item.arm.value,
            "protocol_identity_sha256": protocol.identity_sha256,
            "accepted": False,
            "automatic_model_retry": False,
            "raw_prompt_response_persisted": False,
            "hidden_exposed_to_model": False,
        }
        if item.arm is S38Arm.SIMPLE_ITER:
            value.update(
                {
                    "independent_baseline_qualification_observed": True,
                    "legacy_execution_started": True,
                    "legacy_process_completed": True,
                    "legacy_evaluation_artifact_observed": True,
                    "legacy_reference_supplied": True,
                    "legacy_reference_isolated": True,
                    "legacy_harness_contract_version": 1,
                    "legacy_harness_evidence_observed": True,
                    "independent_final_qualification_observed": False,
                    "qualified_candidate_count": 0,
                    "llm_calls": 1,
                    "status": "no_testbench_passing_candidate",
                }
            )
        value.update(overrides)
        if (
            item.arm is S38Arm.SIMPLE_ITER
            and (value.get("accepted") is True or int(value.get("qualified_candidate_count") or 0) > 0)
            and "independent_final_qualification_observed" not in overrides
        ):
            value["independent_final_qualification_observed"] = True
        return value
    def test_default_protocol_is_three_by_two_by_three(self) -> None:
        protocol = S38Protocol(model="deepseek-v4-flash")
        self.assertEqual(protocol.case_ids, DEFAULT_S38_CASE_IDS)
        self.assertEqual(protocol.repeats, 2)
        self.assertEqual(protocol.run_count, 18)
        self.assertEqual(protocol.simple_iter_iterations, 14)
        self.assertEqual(set(protocol.arms), set(S38Arm))

    def test_protocol_requires_three_distinct_kernels(self) -> None:
        with self.assertRaisesRegex(ValueError, "three distinct"):
            S38Protocol(model="m", case_ids=("array-map", "reduction"))
        with self.assertRaisesRegex(ValueError, "three distinct"):
            S38Protocol(model="m", case_ids=("array-map", "array-map", "reduction"))

    def test_protocol_requires_two_repeats(self) -> None:
        with self.assertRaisesRegex(ValueError, "two repeats"):
            S38Protocol(model="m", repeats=1)

    def test_protocol_requires_all_three_arms(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires optimize"):
            S38Protocol(
                model="m",
                arms=(S38Arm.SAFE_OPTIMIZE, S38Arm.SOURCE_FULL),
            )

    def test_protocol_rejects_unknown_case(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown Stage 2 smoke"):
            S38Protocol(
                model="m",
                case_ids=("array-map", "reduction", "not-real"),
            )

    def test_protocol_rejects_simple_iter_over_llm_ceiling(self) -> None:
        with self.assertRaisesRegex(ValueError, "LLM-call ceiling"):
            S38Protocol(model="m", max_llm_calls=6, simple_iter_iterations=7)

    def test_protocol_rejects_simple_iter_over_csynth_ceiling(self) -> None:
        with self.assertRaisesRegex(ValueError, "CSYNTH ceiling"):
            S38Protocol(model="m", max_csynth_calls=10, simple_iter_iterations=9)

    def test_protocol_records_effective_provider_parameters(self) -> None:
        payload = S38Protocol(
            model="m",
            provider_reasoning_effort="high",
            max_output_tokens=32768,
        ).to_dict()
        self.assertEqual(
            payload["effective_model_request"],
            {
                "provider_reasoning_effort": "high",
                "max_output_tokens": 32768,
            },
        )
        self.assertTrue(
            payload["fairness"]["same_effective_model_request_parameters"]
        )

    def test_protocol_identity_is_deterministic(self) -> None:
        first = S38Protocol(model="m")
        second = S38Protocol(model="m")
        self.assertEqual(first.identity_sha256, second.identity_sha256)
        self.assertEqual(len(first.identity_sha256), 64)

    def test_protocol_declares_fairness_boundaries(self) -> None:
        fairness = S38Protocol(model="m").to_dict()["fairness"]
        self.assertTrue(fairness["same_model"])
        self.assertTrue(fairness["same_target"])
        self.assertTrue(fairness["same_public_hidden_suites"])
        self.assertTrue(fairness["legacy_candidate_independently_qualified"])
        self.assertFalse(fairness["hidden_exposed_to_models"])

    def test_run_matrix_order_is_deterministic(self) -> None:
        matrix = build_s38_run_matrix(S38Protocol(model="m"))
        self.assertEqual(matrix[0].run_id, "s38-array-map-r1-safe-optimize")
        self.assertEqual(matrix[1].run_id, "s38-array-map-r1-source-full")
        self.assertEqual(matrix[2].run_id, "s38-array-map-r1-simple-iter")
        self.assertEqual(matrix[-1].run_id, "s38-nested-stencil-r2-simple-iter")
        self.assertEqual([item.sequence for item in matrix], list(range(1, 19)))

    def test_run_spec_rejects_noncanonical_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "run_id must"):
            S38RunSpec(
                sequence=1,
                case_id="array-map",
                repeat_index=1,
                arm=S38Arm.SAFE_OPTIMIZE,
                run_id="wrong",
            )

    def test_corpus_materializes_three_distinct_kernels(self) -> None:
        with TemporaryDirectory() as directory:
            manifest = materialize_s38_corpus(directory, DEFAULT_S38_CASE_IDS)
            self.assertEqual(manifest["kernel_count"], 3)
            self.assertEqual(
                [item["case_id"] for item in manifest["kernels"]],
                list(DEFAULT_S38_CASE_IDS),
            )
            self.assertEqual(len(manifest["manifest_sha256"]), 64)

    def test_corpus_direct_suites_remain_independent(self) -> None:
        with TemporaryDirectory() as directory:
            manifest = materialize_s38_corpus(directory, DEFAULT_S38_CASE_IDS)
            for kernel in manifest["kernels"]:
                hashes = kernel["suite_sha256"]
                self.assertNotEqual(hashes["direct_public"], hashes["direct_hidden"])

    def test_corpus_full_testbench_uses_handoff_top(self) -> None:
        with TemporaryDirectory() as directory:
            manifest = materialize_s38_corpus(directory, DEFAULT_S38_CASE_IDS)
            for kernel in manifest["kernels"]:
                public = Path(kernel["full"]["public"]).read_text(encoding="utf-8")
                hidden = Path(kernel["full"]["hidden"]).read_text(encoding="utf-8")
                self.assertIn("original_top_hls", public)
                self.assertIn("original_top_hls", hidden)
                self.assertNotIn("candidate_top", public)
                self.assertNotIn("candidate_top", hidden)

    def test_corpus_refuses_nonempty_destination(self) -> None:
        with TemporaryDirectory() as directory:
            Path(directory, "occupied").write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(FileExistsError, "not empty"):
                materialize_s38_corpus(directory, DEFAULT_S38_CASE_IDS)

    def test_aggregate_marks_complete_matrix(self) -> None:
        protocol = S38Protocol(model="m")
        records = []
        for item in build_s38_run_matrix(protocol):
            records.append(
                self._record(
                    protocol,
                    item,
                    accepted=True,
                    llm_calls=2,
                    wall_time_s=1.0,
                    rollback_count=0,
                    ppa={
                        "latency_cycles_max": 10,
                        "initiation_interval_max": 1,
                        "max_resource_utilization_ratio": 0.1,
                    },
                    baseline_ppa={"latency_cycles_max": 20},
                )
            )
        report = aggregate_s38_records(records, protocol=protocol)
        self.assertTrue(report["complete_matrix"])
        self.assertTrue(report["evaluation_valid"])
        self.assertEqual(report["observed_run_count"], 18)
        self.assertFalse(report["stable_superiority_claimed"])

    def test_aggregate_detects_missing_run(self) -> None:
        protocol = S38Protocol(model="m")
        matrix = build_s38_run_matrix(protocol)
        records = [self._record(protocol, item) for item in matrix[:-1]]
        report = aggregate_s38_records(records, protocol=protocol)
        self.assertFalse(report["complete_matrix"])
        self.assertFalse(report["evaluation_valid"])
        self.assertEqual(report["missing_run_ids"], [matrix[-1].run_id])

    def test_aggregate_detects_duplicate_run(self) -> None:
        protocol = S38Protocol(model="m")
        matrix = build_s38_run_matrix(protocol)
        records = [self._record(protocol, item) for item in matrix]
        records.append(dict(records[0]))
        report = aggregate_s38_records(records, protocol=protocol)
        self.assertTrue(report["duplicate_run_ids"])
        self.assertFalse(report["evaluation_valid"])

    def test_aggregate_counts_candidate_and_infrastructure_failures(self) -> None:
        protocol = S38Protocol(model="m")
        records = []
        for index, item in enumerate(build_s38_run_matrix(protocol)):
            records.append(
                self._record(
                    protocol,
                    item,
                    failure_class=(
                        "infrastructure" if index == 0 else "candidate"
                    ),
                )
            )
        report = aggregate_s38_records(records, protocol=protocol)
        safe = report["arms"]["safe-optimize"]
        self.assertEqual(safe["infrastructure_failures"], 1)
        self.assertGreaterEqual(safe["invalid_candidate_runs"], 1)

    def test_aggregate_rejects_record_contract_mismatch(self) -> None:
        protocol = S38Protocol(model="m")
        records = [
            self._record(protocol, item)
            for item in build_s38_run_matrix(protocol)
        ]
        records[0]["case_id"] = "reduction"
        report = aggregate_s38_records(records, protocol=protocol)
        self.assertFalse(report["evaluation_valid"])
        self.assertTrue(report["record_contract_issues"])

    def test_aggregate_rejects_hidden_or_retry_boundary_violation(self) -> None:
        protocol = S38Protocol(model="m")
        records = [
            self._record(protocol, item)
            for item in build_s38_run_matrix(protocol)
        ]
        records[0]["hidden_exposed_to_model"] = True
        records[1]["automatic_model_retry"] = True
        report = aggregate_s38_records(records, protocol=protocol)
        self.assertFalse(report["evaluation_valid"])
        self.assertEqual(len(report["record_contract_issues"]), 2)

    def test_aggregate_includes_ppa_and_call_distributions(self) -> None:
        protocol = S38Protocol(model="m")
        records = [
            self._record(
                protocol,
                item,
                accepted=True,
                ppa={
                    "latency_cycles_max": 10,
                    "initiation_interval_max": 1,
                    "max_resource_utilization_ratio": 0.25,
                },
                baseline_ppa={"latency_cycles_max": 20},
                llm_calls=4,
                tool_calls=8,
                compile_calls=3,
                csim_calls=2,
                csynth_calls=1,
                invalid_candidate_ratio=0.0,
                rollback_count=0,
            )
            for item in build_s38_run_matrix(protocol)
        ]
        report = aggregate_s38_records(records, protocol=protocol)
        safe = report["arms"]["safe-optimize"]
        self.assertEqual(safe["latency_improvement_ratio"]["median"], 0.5)
        self.assertEqual(safe["csynth_calls"]["median"], 1.0)
        self.assertEqual(safe["initiation_interval_max"]["median"], 1.0)


    def test_external_qualification_observer_uses_full_stage3_order(self) -> None:
        self.assertEqual(
            _REQUIRED_STAGE_ORDER,
            (
                "source",
                "preflight",
                "public",
                "csynth",
                "hidden",
                "ppa",
                "feasibility",
            ),
        )

    def test_record_and_report_schema_versions_are_separate(self) -> None:
        self.assertEqual(S38_RUN_RECORD_SCHEMA_VERSION, 3)
        self.assertEqual(S38_SUPPORTED_RUN_RECORD_READ_VERSIONS, (1, 2, 3))
        self.assertEqual(S38_REPORT_SCHEMA_VERSION, 3)

    def test_aggregate_rejects_unexecuted_legacy_records(self) -> None:
        protocol = S38Protocol(model="m")
        records = [
            self._record(protocol, item)
            for item in build_s38_run_matrix(protocol)
        ]
        legacy = next(item for item in records if item["arm"] == "simple-iter")
        legacy["legacy_execution_started"] = False
        legacy["llm_calls"] = 0
        report = aggregate_s38_records(records, protocol=protocol)
        self.assertFalse(report["evaluation_valid"])
        self.assertFalse(report["legacy_simple_iter_comparison_executed"])
        self.assertTrue(
            any(
                "legacy_execution_not_started" in issue
                for issue in report["record_contract_issues"]
            )
        )

    def test_aggregate_accepts_mixed_product_v1_and_legacy_v3_records(self) -> None:
        protocol = S38Protocol(model="m")
        records = [
            self._record(protocol, item)
            for item in build_s38_run_matrix(protocol)
        ]
        report = aggregate_s38_records(records, protocol=protocol)
        self.assertTrue(report["evaluation_valid"])
        self.assertTrue(report["legacy_simple_iter_comparison_executed"])
        self.assertEqual(report["schema_version"], 3)
        self.assertEqual(report["supported_run_record_schema_versions"], [1, 2, 3])
        self.assertEqual(report["legacy_simple_iter_harness_validated_runs"], 6)
        self.assertEqual(report["legacy_simple_iter_reference_isolated_runs"], 6)


    def test_legacy_execution_evidence_helper_rejects_v1_zero_call_record(self) -> None:
        self.assertFalse(
            legacy_record_has_execution_evidence(
                {
                    "schema_version": 1,
                    "legacy_execution_started": False,
                    "llm_calls": 0,
                }
            )
        )
        self.assertTrue(
            legacy_record_has_execution_evidence(
                {
                    "schema_version": 3,
                    "accepted": False,
                    "status": "no_testbench_passing_candidate",
                    "independent_baseline_qualification_observed": True,
                    "legacy_execution_started": True,
                    "legacy_process_completed": True,
                    "legacy_evaluation_artifact_observed": True,
                    "legacy_reference_supplied": True,
                    "legacy_reference_isolated": True,
                    "legacy_harness_contract_version": 1,
                    "legacy_harness_evidence_observed": True,
                    "qualified_candidate_count": 0,
                    "llm_calls": 14,
                }
            )
        )

    def test_aggregate_rejects_v2_legacy_records_without_harness_contract(self) -> None:
        protocol = S38Protocol(model="m")
        records = [self._record(protocol, item) for item in build_s38_run_matrix(protocol)]
        legacy = next(item for item in records if item["arm"] == "simple-iter")
        legacy["schema_version"] = 2
        legacy.pop("legacy_reference_isolated", None)
        legacy.pop("legacy_harness_contract_version", None)
        report = aggregate_s38_records(records, protocol=protocol)
        self.assertFalse(report["evaluation_valid"])
        self.assertFalse(report["legacy_simple_iter_comparison_executed"])
        self.assertTrue(any("legacy_record_requires_schema_v3" in issue for issue in report["record_contract_issues"]))

    def test_legacy_comparison_requires_completed_process_and_harness(self) -> None:
        protocol = S38Protocol(model="m")
        records = [self._record(protocol, item) for item in build_s38_run_matrix(protocol)]
        legacy = next(item for item in records if item["arm"] == "simple-iter")
        legacy["legacy_process_completed"] = False
        report = aggregate_s38_records(records, protocol=protocol)
        self.assertFalse(report["legacy_simple_iter_comparison_executed"])
        self.assertEqual(report["legacy_simple_iter_process_completed_runs"], 5)

    def test_protocol_json_is_safe_and_round_trippable(self) -> None:
        payload = S38Protocol(model="m", api_key_env="DEEPSEEK_API_KEY").to_dict()
        text = json.dumps(payload, allow_nan=False)
        self.assertNotIn("api_key_value", text)
        self.assertIn("DEEPSEEK_API_KEY", text)


if __name__ == "__main__":
    unittest.main()
