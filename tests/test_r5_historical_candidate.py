from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from agrefactor.recovery.r5_historical_candidate import (
    R5HistoricalCandidateError,
    file_sha256,
    isolate_candidate_symbols,
    materialize_candidate_symbols,
    text_sha256,
    verify_historical_candidate_plan,
)


class R5HistoricalCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "case").mkdir()
        self.reference = "void top(int *out) { *out = 7; }\n"
        self.legacy = (
            "int helper(int n) { return n ? helper(n - 1) + 1 : 0; }\n"
            "void top(int *out) { *out = helper(7); }\n"
        )
        self.public = (
            "void top(int*);\n"
            "void top_hls(int*);\n"
            "int main(){int a=0,b=0;top(&a);top_hls(&b);"
            "if(a!=b){return 1;}return 0;}\n"
        )
        self.hidden = (
            "void top(int*);\n"
            "void top_hls(int*);\n"
            "int main(){int a=1,b=2;top(&a);top_hls(&b);"
            "if(a!=b){return 1;}return 0;}\n"
        )
        values = {
            "reference.cpp": self.reference,
            "candidate.cpp": self.legacy,
            "public.cpp": self.public,
            "hidden.cpp": self.hidden,
        }
        for name, value in values.items():
            (self.root / "case" / name).write_text(value, encoding="utf-8")
        self.symbol_map = [
            {"source": "helper", "target": "helper_hls"},
            {"source": "top", "target": "top_hls"},
        ]
        isolated = isolate_candidate_symbols(self.legacy, self.symbol_map)
        self.plan = {
            "schema_version": 1,
            "status": "frozen_before_preexisting_candidate_outcome_observation",
            "case_id": "history-case",
            "period": "history",
            "control_role": "positive",
            "failure_family": "unsupported_construct",
            "reference_top": "top",
            "candidate_top": "top_hls",
            "paths": {
                "reference": "case/reference.cpp",
                "legacy_candidate": "case/candidate.cpp",
                "public_test": "case/public.cpp",
                "hidden_test": "case/hidden.cpp",
            },
            "file_sha256s": {
                key: file_sha256(self.root / value)
                for key, value in {
                    "reference": "case/reference.cpp",
                    "legacy_candidate": "case/candidate.cpp",
                    "public_test": "case/public.cpp",
                    "hidden_test": "case/hidden.cpp",
                }.items()
            },
            "source_sha256": text_sha256(self.reference),
            "symbol_isolation_map": self.symbol_map,
            "isolated_candidate_sha256": text_sha256(isolated),
            "predecessor_source_sha256s": ["a" * 64],
            "future_holdout_case_ids": ["future-a", "future-b"],
            "budget": {
                "provider_calls_before": 93,
                "vitis_launches_before": 33,
                "provider_call_upper_bound": 2,
                "vitis_launch_upper_bound": 6,
                "provider_hard_cap": 500,
                "vitis_hard_cap": 500,
            },
            "legacy_candidate_outcome_observed": False,
            "future_outcomes_observed": False,
            "trusted_revision_creation_allowed": False,
            "r5_real_campaign_allowed": False,
        }

    def test_plan_rebuilds_isolated_candidate_and_proves_independence(self) -> None:
        bundle = verify_historical_candidate_plan(self.root, self.plan)
        self.assertEqual(bundle.source_sha256, text_sha256(self.reference))
        self.assertEqual(
            bundle.isolated_candidate_sha256,
            self.plan["isolated_candidate_sha256"],
        )
        self.assertIn("#define top top_hls", bundle.isolated_candidate_code)
        self.assertTrue(bundle.isolated_candidate_code.endswith(self.legacy))

    def test_changed_checked_in_candidate_is_rejected(self) -> None:
        (self.root / "case" / "candidate.cpp").write_text(
            self.legacy + "// changed\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            R5HistoricalCandidateError,
            "legacy_candidate file hash mismatch",
        ):
            verify_historical_candidate_plan(self.root, self.plan)

    def test_predecessor_source_reuse_is_rejected(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["predecessor_source_sha256s"] = [plan["source_sha256"]]
        with self.assertRaisesRegex(
            R5HistoricalCandidateError,
            "not predecessor-independent",
        ):
            verify_historical_candidate_plan(self.root, plan)

    def test_isolation_map_must_bind_candidate_top_exactly(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["symbol_isolation_map"][-1]["target"] = "wrong_top"
        plan["isolated_candidate_sha256"] = text_sha256(
            isolate_candidate_symbols(
                self.legacy,
                plan["symbol_isolation_map"],
            )
        )
        with self.assertRaisesRegex(
            R5HistoricalCandidateError,
            "top isolation mapping is not exact",
        ):
            verify_historical_candidate_plan(self.root, plan)

    def test_v2_plan_binds_current_budget_and_all_observed_sources(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["schema_version"] = 2
        plan["prior_observed_source_sha256s"] = ["a" * 64, "b" * 64]
        plan["required_legacy_markers"] = [
            "helper(n - 1)",
            "void top(int *out)",
        ]
        plan["budget"]["provider_calls_before"] = 96
        plan["budget"]["vitis_launches_before"] = 39
        bundle = verify_historical_candidate_plan(self.root, plan)
        self.assertEqual(bundle.case_id, "history-case")

        plan["prior_observed_source_sha256s"].append(plan["source_sha256"])
        with self.assertRaisesRegex(
            R5HistoricalCandidateError,
            "all observed sources",
        ):
            verify_historical_candidate_plan(self.root, plan)

    def test_v2_plan_budget_must_retain_complete_attempt_reserve(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["schema_version"] = 2
        plan["prior_observed_source_sha256s"] = ["a" * 64]
        plan["required_legacy_markers"] = ["helper(n - 1)"]
        plan["budget"]["provider_calls_before"] = 499
        plan["budget"]["vitis_launches_before"] = 39
        with self.assertRaisesRegex(
            R5HistoricalCandidateError,
            "budget is invalid",
        ):
            verify_historical_candidate_plan(self.root, plan)

    def test_materialized_isolation_exposes_renamed_top_to_parsers(self) -> None:
        isolated = materialize_candidate_symbols(self.legacy, self.symbol_map)
        self.assertIn("void top_hls(int *out)", isolated)
        self.assertIn("helper_hls(n - 1)", isolated)
        self.assertNotIn("#define top top_hls", isolated)
        self.assertNotIn("void top(int *out)", isolated)

    def test_post_fix_resume_requires_materialized_isolation(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan.update(
            {
                "schema_version": 3,
                "status": "frozen_after_audited_pre_provider_isolation_fix",
                "legacy_candidate_outcome_observed": True,
                "symbol_isolation_version": (
                    "r5-historical-candidate-symbol-isolation-v2"
                ),
                "prior_observed_source_sha256s": ["a" * 64],
                "required_legacy_markers": ["helper(n - 1)"],
                "prior_failed_attempt": {
                    "status": "clean_pre_provider_model_adapter_failure",
                    "attempts_consumed": 1,
                    "maximum_remaining_attempts": 2,
                },
                "confidence_threshold_weakened": False,
            }
        )
        plan["isolated_candidate_sha256"] = text_sha256(
            materialize_candidate_symbols(self.legacy, self.symbol_map)
        )
        plan["budget"]["provider_calls_before"] = 97
        plan["budget"]["vitis_launches_before"] = 41
        bundle = verify_historical_candidate_plan(self.root, plan)
        self.assertEqual(
            bundle.to_identity()["isolation_version"],
            "r5-historical-candidate-symbol-isolation-v2",
        )

        post_provider = copy.deepcopy(plan)
        post_provider["prior_failed_attempt"]["status"] = (
            "clean_provider_or_response_contract_failure"
        )
        verify_historical_candidate_plan(self.root, post_provider)

        plan["symbol_isolation_version"] = (
            "r5-historical-candidate-symbol-isolation-v1"
        )
        plan["isolated_candidate_sha256"] = text_sha256(
            isolate_candidate_symbols(self.legacy, self.symbol_map)
        )
        with self.assertRaisesRegex(
            R5HistoricalCandidateError,
            "post-fix historical resume boundary",
        ):
            verify_historical_candidate_plan(self.root, plan)

    def test_final_attempt_boundary_is_explicit(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan.update(
            {
                "schema_version": 4,
                "status": "frozen_before_final_expanded_history_attempt",
                "legacy_candidate_outcome_observed": True,
                "symbol_isolation_version": (
                    "r5-historical-candidate-symbol-isolation-v2"
                ),
                "prior_observed_source_sha256s": ["a" * 64],
                "required_legacy_markers": ["helper(n - 1)"],
                "prior_attempt": {
                    "status": "clean_safe_calibration_abstention",
                    "attempts_consumed": 2,
                    "maximum_remaining_attempts": 1,
                },
                "confidence_threshold_weakened": False,
            }
        )
        plan["isolated_candidate_sha256"] = text_sha256(
            materialize_candidate_symbols(self.legacy, self.symbol_map)
        )
        plan["budget"]["provider_calls_before"] = 98
        plan["budget"]["vitis_launches_before"] = 43
        self.assertEqual(
            verify_historical_candidate_plan(self.root, plan).case_id,
            "history-case",
        )
        plan["prior_attempt"]["maximum_remaining_attempts"] = 2
        with self.assertRaisesRegex(
            R5HistoricalCandidateError,
            "final historical attempt boundary",
        ):
            verify_historical_candidate_plan(self.root, plan)


if __name__ == "__main__":
    unittest.main()
