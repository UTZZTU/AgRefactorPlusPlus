from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "r5_audit_preexisting_history_result",
    ROOT / "scripts" / "r5_audit_preexisting_history_result.py",
)
assert SPEC is not None and SPEC.loader is not None
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class R5PreexistingHistoryResultAuditTests(unittest.TestCase):
    def test_diagnostic_audit_uses_semantics_not_one_log_code(self) -> None:
        event = {
            "stage": "csynth",
            "physical_tool_launched": True,
            "evidence_complete": True,
            "hidden_input_count": 0,
            "evidence_refs": ["event", "item"],
            "diagnostic_items": [
                {
                    "diagnostic_code": "HLS OTHER-CODE",
                    "stage": "csynth",
                    "severity": "error",
                    "detail": "Unsupported recursive construct",
                }
            ],
            "event_id": "diagnostic-1",
        }
        shadow = {
            "event_id": "diagnostic-1",
            "input_status": "eligible",
            "critical_safety_violation": False,
            "equivalence": {"equivalent": True},
            "advisory": {
                "suspected_failure_class": "unsupported_construct",
                "suspected_owner": "candidate",
                "repair_scope": "candidate_only",
                "abstain_reason": None,
                "evidence_refs": ["item"],
            },
        }
        observed_event, observed_shadow = AUDIT._verify_diagnostic(
            {
                "diagnostic_events": [event],
                "r2_shadow_diagnostics": [shadow],
            }
        )
        self.assertEqual(observed_event["event_id"], "diagnostic-1")
        self.assertEqual(
            observed_shadow["advisory"]["suspected_failure_class"],
            "unsupported_construct",
        )

    def test_safe_calibration_abstention_is_distinct_from_positive(self) -> None:
        result = {
            "status": "abstained",
            "provider_calls": 1,
            "vitis_launches": 2,
            "r5_integration": {
                "status": "abstained",
                "reason": "r2_calibration_unverified",
                "calibration": {
                    "verified": False,
                    "reasons": ["confidence_label_not_calibrated"],
                },
                "main_result_unchanged": True,
                "accepted_by_integration": False,
            },
        }
        shadow = {"advisory": {"confidence": "medium"}}
        value = AUDIT._verify_abstention(result, shadow)
        self.assertEqual(value["status"], "clean_safe_calibration_abstention")

    def test_high_confidence_cannot_be_relabelled_as_calibration_abstention(self) -> None:
        result = {
            "status": "abstained",
            "provider_calls": 1,
            "vitis_launches": 2,
            "r5_integration": {
                "status": "abstained",
                "reason": "r2_calibration_unverified",
                "calibration": {
                    "verified": False,
                    "reasons": ["confidence_label_not_calibrated"],
                },
                "main_result_unchanged": True,
                "accepted_by_integration": False,
            },
        }
        with self.assertRaisesRegex(
            AUDIT.PreexistingHistoryResultAuditError,
            "abstention invariants failed",
        ):
            AUDIT._verify_abstention(
                result,
                {"advisory": {"confidence": "high"}},
            )

    def test_private_payload_field_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            AUDIT.PreexistingHistoryResultAuditError,
            "raw private field persisted",
        ):
            AUDIT._assert_privacy(
                {"nested": {"reasoning_content": "must not persist"}}
            )

    def test_high_confidence_pre_provider_failure_is_not_safe_abstention(self) -> None:
        result = {
            "status": "abstained",
            "provider_calls": 1,
            "vitis_launches": 2,
            "r5_integration": {
                "status": "abstained",
                "reason": "r2_calibration_unverified",
                "calibration": {
                    "verified": False,
                    "reasons": ["confidence_label_not_calibrated"],
                },
                "main_result_unchanged": True,
                "accepted_by_integration": False,
            },
        }
        with self.assertRaisesRegex(
            AUDIT.PreexistingHistoryResultAuditError,
            "abstention invariants failed",
        ):
            AUDIT._verify_abstention(
                result,
                {"advisory": {"confidence": "high"}},
            )

    def test_deterministic_candidate_owner_stays_outside_r2(self) -> None:
        result = {
            "status": "abstained",
            "provider_calls": 0,
            "vitis_launches": 2,
            "diagnostic_events": [
                {
                    "event_id": "diagnostic-1",
                    "stage": "csynth",
                    "owner": "candidate",
                    "repair_scope": "candidate_only",
                    "failure_classes": ["unsupported_construct"],
                    "physical_tool_launched": True,
                    "evidence_complete": True,
                    "hidden_input_count": 0,
                    "diagnostic_items": [
                        {
                            "stage": "csynth",
                            "severity": "error",
                            "owner": "candidate",
                            "category": "unsupported_construct",
                            "classification_confidence": "high",
                            "detail": "Unsupported dynamic allocation",
                        }
                    ],
                }
            ],
            "r2_shadow_diagnostics": [
                {
                    "event_id": "diagnostic-1",
                    "input_status": "rejected:owner_not_unknown_or_review",
                    "request_sha256": None,
                    "provider_identity": {},
                    "accounting": {},
                    "critical_safety_violation": False,
                    "equivalence": {"equivalent": True},
                    "advisory": {
                        "suspected_owner": "unknown",
                        "suspected_failure_class": "unknown",
                        "repair_scope": "none",
                        "confidence": "low",
                        "evidence_refs": [],
                        "abstain_reason": "owner_not_unknown_or_review",
                    },
                }
            ],
            "r5_integration": {
                "status": "abstained",
                "reason": "eligible_r2_event_not_unique",
                "main_result_unchanged": True,
                "accepted_by_integration": False,
            },
        }
        value = AUDIT._verify_pre_r2_deterministic_boundary(result)
        self.assertEqual(value["status"], "clean_pre_r2_deterministic_boundary")
        self.assertEqual(value["r2_provider_calls"], 0)

    def test_model_adapter_failure_is_auditable_without_call_or_mutation(self) -> None:
        result = {
            "status": "inconclusive",
            "provider_calls": 1,
            "vitis_launches": 2,
            "initial_candidate_sha256": "a" * 64,
            "r5_integration": {
                "status": "inconclusive",
                "main_result_unchanged": True,
                "accepted_by_integration": False,
                "episode_path": "/evidence/episode.json",
                "r4_controller_result": {
                    "outcome": "inconclusive",
                    "reasons": ["pre_provider_model_adapter_failure"],
                    "provider_call_count": 0,
                    "mutation_count": 0,
                    "after_candidate_sha256": None,
                    "formal_validation_id": None,
                },
            },
        }
        episode = {
            "outcome": "inconclusive",
            "source_sha256": "b" * 64,
            "manifest_sha256": "c" * 64,
            "episode_id": "episode-1",
            "payload": {
                "outcome_reason": "pre_provider_model_adapter_failure",
                "candidate_before_sha256": "a" * 64,
                "candidate_after_sha256": None,
                "formal_validation_id": None,
                "provider_call_count": 0,
                "budget_actual": {
                    "provider_calls": 0,
                    "mutation_calls": 0,
                    "budget_delta": {"llm_calls": 0},
                },
            },
            "agent_safe_summary": {
                "reason": "pre_provider_model_adapter_failure",
                "false_repair": False,
                "unsafe_scope": False,
                "critical_safety_violation": False,
            },
        }
        value = AUDIT._verify_pre_provider_contract_failure(
            payloads={"ledger/episode.json": json.dumps(episode).encode("utf-8")},
            manifest={
                "manifest_sha256": "c" * 64,
                "case_identity": {"source_sha256": "b" * 64},
            },
            result=result,
            shadow={"advisory": {"confidence": "high"}},
        )
        self.assertEqual(value["status"], "clean_pre_provider_model_adapter_failure")

    def test_provider_response_contract_failure_is_auditable(self) -> None:
        result = {
            "status": "inconclusive",
            "provider_calls": 2,
            "vitis_launches": 2,
            "initial_candidate_sha256": "a" * 64,
            "r5_integration": {
                "status": "inconclusive",
                "main_result_unchanged": True,
                "accepted_by_integration": False,
                "episode_path": "/evidence/episode.json",
                "r4_controller_result": {
                    "outcome": "inconclusive",
                    "reasons": [
                        "provider_or_response_contract_failure",
                        "response_contract_top_interface_changed",
                    ],
                    "provider_call_count": 1,
                    "mutation_count": 0,
                    "after_candidate_sha256": None,
                    "formal_validation_id": None,
                },
            },
        }
        episode_reason = (
            "provider_or_response_contract_failure;"
            "response_contract_top_interface_changed"
        )
        episode = {
            "outcome": "inconclusive",
            "source_sha256": "b" * 64,
            "manifest_sha256": "c" * 64,
            "episode_id": "episode-1",
            "payload": {
                "outcome_reason": episode_reason,
                "candidate_before_sha256": "a" * 64,
                "candidate_after_sha256": None,
                "formal_validation_id": None,
                "provider_call_count": 1,
                "budget_actual": {
                    "provider_calls": 1,
                    "mutation_calls": 0,
                    "budget_delta": {"llm_calls": 1},
                },
            },
            "agent_safe_summary": {
                "reason": episode_reason,
                "false_repair": False,
                "unsafe_scope": False,
                "critical_safety_violation": False,
            },
        }
        value = AUDIT._verify_pre_provider_contract_failure(
            payloads={"ledger/episode.json": json.dumps(episode).encode("utf-8")},
            manifest={
                "manifest_sha256": "c" * 64,
                "case_identity": {"source_sha256": "b" * 64},
            },
            result=result,
            shadow={"advisory": {"confidence": "high"}},
        )
        self.assertEqual(
            value["status"],
            "clean_provider_or_response_contract_failure",
        )
        self.assertEqual(
            value["response_contract_reason_codes"],
            ["response_contract_top_interface_changed"],
        )

    def test_missing_cosim_depth_is_configuration_not_candidate_failure(self) -> None:
        formal_id = "formal-validation.r4"
        before = "a" * 64
        after = "b" * 64
        source = "c" * 64
        manifest_sha = "d" * 64
        base = f"work/{formal_id}/attempt_001"
        episode = {
            "outcome": "inconclusive",
            "source_sha256": source,
            "manifest_sha256": manifest_sha,
            "episode_id": "episode-1",
            "payload": {
                "outcome_reason": "independent_auditor_not_clean",
                "candidate_before_sha256": before,
                "candidate_after_sha256": after,
                "formal_validation_id": formal_id,
                "provider_call_count": 1,
                "budget_actual": {
                    "provider_calls": 1,
                    "mutation_calls": 1,
                    "budget_delta": {"llm_calls": 1},
                },
            },
            "agent_safe_summary": {
                "false_repair": False,
                "unsafe_scope": False,
                "critical_safety_violation": False,
            },
        }
        payloads = {
            "ledger/episode.json": json.dumps(episode).encode("utf-8"),
            f"{base}/csim/public/suite_001/csim_invocation.json": json.dumps(
                {
                    "runtime_classification": {"status": "passed"},
                    "typed_outcome": {
                        "status": "passed",
                        "candidate_sha256": after,
                    },
                }
            ).encode("utf-8"),
            f"{base}/csynth/csynth_invocation.json": json.dumps(
                {"execution": {"status": "completed", "returncode": 0}}
            ).encode("utf-8"),
            f"{base}/public_cosim/suite_001/cosim_invocation.json": json.dumps(
                {
                    "runtime_contract": {"schema_version": 1},
                    "cosim_interface_depths": {},
                    "execution": {
                        "status": "completed",
                        "returncode": 1,
                        "cosim_launched": True,
                    },
                    "typed_outcome": {"status": "missing_or_invalid"},
                    "result_summary": {
                        "failure_kind": "ownership_unknown",
                        "reason_code": "cosim_failed_without_typed_owner",
                    },
                }
            ).encode("utf-8"),
            f"{base}/public_cosim/suite_001/cosim_command_status.json": json.dumps(
                {
                    "schema_version": 1,
                    "status": "failed",
                    "phase": "cosim",
                    "reason_code": "cosim_command_failed",
                }
            ).encode("utf-8"),
            (
                f"{base}/public_cosim/suite_001/agrefactor_public_cosim/"
                "solution/.temp11.log"
            ): (
                b"ERROR: A depth specification is required for MAXI "
                b"interface port 'gmem' for cosimulation.\n"
            ),
        }
        result = {
            "status": "inconclusive",
            "provider_calls": 2,
            "vitis_launches": 5,
            "initial_candidate_sha256": before,
            "r5_integration": {
                "status": "inconclusive",
                "main_result_unchanged": True,
                "accepted_by_integration": False,
                "episode_path": "/evidence/episode.json",
                "r4_controller_result": {
                    "outcome": "inconclusive",
                    "reasons": ["independent_auditor_not_clean"],
                    "provider_call_count": 1,
                    "mutation_count": 1,
                    "after_candidate_sha256": after,
                    "formal_validation_id": formal_id,
                },
            },
        }
        value = AUDIT._verify_post_mutation_cosim_configuration_failure(
            payloads=payloads,
            manifest={
                "manifest_sha256": manifest_sha,
                "case_identity": {"source_sha256": source},
            },
            result=result,
            shadow={"advisory": {"confidence": "high"}},
        )
        self.assertEqual(
            value["status"],
            "clean_cosim_interface_depth_configuration_failure",
        )
        self.assertEqual(value["missing_maxi_interface_bundle"], "gmem")
        self.assertFalse(value["rtl_cosim_started"])


if __name__ == "__main__":
    unittest.main()
