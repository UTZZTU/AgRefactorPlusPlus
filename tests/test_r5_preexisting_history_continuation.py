from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

from agrefactor.recovery.r5_historical_candidate import canonical_sha256
from agrefactor.recovery.r5_preexisting_history_continuation import (
    R5PreexistingHistoryContinuationError,
    verify_preexisting_history_continuation_plan,
)


ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNNER = _load_script("r5_continue_preexisting_history")
RESULT_AUDITOR = _load_script("r5_audit_preexisting_history_continuation_result")


class R5PreexistingHistoryContinuationTests(unittest.TestCase):
    def test_confidence_threshold_cannot_be_weakened(self) -> None:
        plan = {
            "schema_version": 1,
            "status": "frozen_after_first_safe_abstention_before_continuation",
            "period": "history",
            "control_role": "positive",
            "failure_family": "unsupported_construct",
            "maximum_additional_attempts": 2,
            "stop_after_first_verified_positive": True,
            "maximum_candidate_mutations_per_attempt": 1,
            "future_files_read_by_protocol": False,
            "future_outcomes_observed": False,
            "expected_outputs_invented": False,
            "original_benchmark_files_mutated": False,
            "trusted_revision_creation_allowed": False,
            "r5_real_campaign_allowed": False,
            "r5_accepted": False,
            "r6_started": False,
            "budget": {
                "provider_calls_before": 94,
                "vitis_launches_before": 35,
                "provider_call_upper_bound_per_attempt": 2,
                "vitis_launch_upper_bound_per_attempt": 6,
                "provider_call_upper_bound_total": 4,
                "vitis_launch_upper_bound_total": 12,
                "provider_hard_cap": 500,
                "vitis_hard_cap": 500,
            },
            "confidence_boundary": {
                "minimum_authorized_label": "medium",
                "accepted_calibration_certificate_unchanged": True,
                "threshold_weakened": True,
                "medium_may_mutate": True,
            },
        }
        with self.assertRaisesRegex(
            R5PreexistingHistoryContinuationError,
            "confidence boundary",
        ):
            verify_preexisting_history_continuation_plan(ROOT, plan)

    def test_only_low_or_medium_calibration_abstention_may_continue(self) -> None:
        def result(confidence: str) -> dict:
            return {
                "status": "abstained",
                "provider_calls": 1,
                "vitis_launches": 2,
                "r2_shadow_diagnostics": [
                    {"advisory": {"confidence": confidence}}
                ],
                "r5_integration": {
                    "status": "abstained",
                    "reason": "r2_calibration_unverified",
                    "main_result_unchanged": True,
                    "accepted_by_integration": False,
                },
            }

        self.assertTrue(RUNNER._is_safe_abstention(result("medium")))
        self.assertFalse(RUNNER._is_safe_abstention(result("high")))

    def test_two_safe_abstentions_are_auditable_exhaustion(self) -> None:
        manifest = {
            "status": "frozen_before_real_continuation",
            "maximum_additional_attempts": 2,
            "stop_after_first_verified_positive": True,
            "provider_calls_before": 94,
            "vitis_launches_before": 35,
            "provider_call_upper_bound_total": 4,
            "vitis_launch_upper_bound_total": 12,
            "confidence_minimum_authorized_label": "high",
            "confidence_threshold_weakened": False,
            "future_files_read": False,
            "future_outcomes_observed": False,
            "r5_accepted": False,
            "r6_started": False,
        }
        manifest["manifest_sha256"] = canonical_sha256(manifest)
        attempts = [
            {
                "attempt_ordinal": ordinal,
                "status": "abstained",
                "provider_calls": 1,
                "vitis_launches": 2,
            }
            for ordinal in (2, 3)
        ]
        result = {
            "status": "exhausted_safe_abstention",
            "stop_reason": "maximum_additional_attempts_exhausted",
            "manifest_sha256": manifest["manifest_sha256"],
            "attempts": attempts,
            "attempt_count": 2,
            "provider_calls": 2,
            "vitis_launches": 4,
            "provider_calls_after": 96,
            "vitis_launches_after": 39,
            "verified_positive_episode_count": 0,
            "stopped_after_first_verified_positive": False,
            "confidence_threshold_weakened": False,
            "future_files_read": False,
            "future_outcomes_observed": False,
            "trusted_revision_created": False,
            "r5_real_campaign_allowed": False,
            "r5_accepted": False,
            "r6_started": False,
        }
        result["result_sha256"] = canonical_sha256(result)
        audits = [
            {"status": "clean_safe_calibration_abstention"},
            {"status": "clean_safe_calibration_abstention"},
        ]
        RESULT_AUDITOR._verify_aggregate(
            manifest=manifest,
            result=result,
            attempt_audits=audits,
        )
        result["stop_reason"] = "keep_trying"
        result["result_sha256"] = canonical_sha256(
            {key: value for key, value in result.items() if key != "result_sha256"}
        )
        with self.assertRaisesRegex(
            RESULT_AUDITOR.PreexistingHistoryContinuationResultAuditError,
            "exhaustion condition",
        ):
            RESULT_AUDITOR._verify_aggregate(
                manifest=manifest,
                result=result,
                attempt_audits=audits,
            )

    def test_pre_provider_contract_failure_is_auditable_stop(self) -> None:
        manifest = {
            "status": "frozen_before_real_continuation",
            "maximum_additional_attempts": 2,
            "stop_after_first_verified_positive": True,
            "provider_calls_before": 94,
            "vitis_launches_before": 35,
            "provider_call_upper_bound_total": 4,
            "vitis_launch_upper_bound_total": 12,
            "confidence_minimum_authorized_label": "high",
            "confidence_threshold_weakened": False,
            "future_files_read": False,
            "future_outcomes_observed": False,
            "r5_accepted": False,
            "r6_started": False,
        }
        manifest["manifest_sha256"] = canonical_sha256(manifest)
        result = {
            "status": "stopped_inconclusive",
            "stop_reason": "attempt_was_not_verified_positive_or_safe_abstention",
            "manifest_sha256": manifest["manifest_sha256"],
            "attempts": [
                {
                    "attempt_ordinal": 2,
                    "status": "inconclusive",
                    "provider_calls": 1,
                    "vitis_launches": 2,
                }
            ],
            "attempt_count": 1,
            "provider_calls": 1,
            "vitis_launches": 2,
            "provider_calls_after": 95,
            "vitis_launches_after": 37,
            "verified_positive_episode_count": 0,
            "stopped_after_first_verified_positive": False,
            "confidence_threshold_weakened": False,
            "future_files_read": False,
            "future_outcomes_observed": False,
            "trusted_revision_created": False,
            "r5_real_campaign_allowed": False,
            "r5_accepted": False,
            "r6_started": False,
        }
        result["result_sha256"] = canonical_sha256(result)
        RESULT_AUDITOR._verify_aggregate(
            manifest=manifest,
            result=result,
            attempt_audits=[
                {"status": "clean_pre_provider_mutation_contract_failure"}
            ],
        )


if __name__ == "__main__":
    unittest.main()
