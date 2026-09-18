from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import unittest

from agrefactor.evidence import audit_r2_calibration_bundle
from agrefactor.recovery.shadow_advisor import (
    CalibrationAcceptancePolicy,
    certify_calibration,
    evaluate_calibration,
    freeze_calibration_protocol,
)


def canonical_sha256(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def calibration_bundle():
    provider_identity = {
        "provider": "openai-compatible",
        "model": "calibration-model",
        "model_name": "calibration-model",
    }
    records = []
    for index, failure_class in enumerate(
        ("bounds", "interface", "syntax", "unsupported_construct"),
        start=1,
    ):
        evidence_id = f"evidence-{index}"
        advisory = {
            "schema_version": 1,
            "accepted": False,
            "suspected_owner": "candidate",
            "suspected_failure_class": failure_class,
            "evidence_refs": [evidence_id],
            "repair_scope": "candidate_only",
            "confidence": "high",
            "abstain_reason": None,
            "bounded_repair_intent": "repair candidate only",
            "metadata": {
                "strict_parser": "r2-v1",
                "prompt_contract_version": "r2-shadow-output-v3",
                "bounded_repair_intent_executed": False,
            },
        }
        records.append({
            "record_id": f"record-{index}",
            "evidence_ids": [evidence_id],
            "advisory": advisory,
            "truth": {
                "owner": "candidate",
                "failure_class": failure_class,
            },
            "shadow": {
                "schema_version": 1,
                "event_id": f"event-{index}",
                "request_sha256": canonical_sha256({"event": index}),
                "provider_identity": dict(provider_identity),
                "advisory": advisory,
                "accounting": {"provider_calls": 1},
                "equivalence": {"equivalent": True, "changed_fields": []},
                "authority": "deterministic_fsm_and_evidence_auditor",
                "shadow_only": True,
                "critical_safety_violation": False,
            },
        })
    protocol = freeze_calibration_protocol(
        "calibration-audit-test",
        [item["record_id"] for item in records],
    )
    report = evaluate_calibration(records, protocol=protocol)
    policy = CalibrationAcceptancePolicy(
        policy_id="calibration-audit-test-policy",
        minimum_total=4,
        minimum_covered=4,
        minimum_high_confidence=4,
        minimum_coverage=1.0,
        minimum_citation_validity_lower_95=0.0,
        maximum_selective_risk=0.0,
        maximum_high_confidence_error_rate=0.0,
        maximum_high_confidence_error_upper_95=1.0,
        maximum_unsafe_scope_rate=0.0,
        maximum_unsafe_scope_upper_95=1.0,
    )
    certificate = certify_calibration(
        report,
        policy=policy,
        provider_identity=provider_identity,
    )
    return {
        "schema_version": 1,
        "protocol": protocol.to_dict(),
        "records": records,
        "report": report.to_dict(),
        "policy": policy.to_dict(),
        "provider_identity": provider_identity,
        "certificate": certificate.to_dict(),
        "execution": {
            "provider_calls": 4,
            "vitis_launches": 0,
            "git_history_mutations": 0,
            "raw_provider_response_persisted": False,
            "private_reasoning_persisted": False,
        },
    }


def refresh_certificate_id(certificate):
    body = {key: value for key, value in certificate.items() if key != "certificate_id"}
    certificate["certificate_id"] = (
        "r2-calibration-" + canonical_sha256(body)[:32]
    )


class R2CalibrationAuditorTests(unittest.TestCase):
    def test_clean_accepted_bundle(self):
        report = audit_r2_calibration_bundle(calibration_bundle())
        self.assertEqual(report.status, "clean")
        self.assertEqual(report.summary_status, "calibration_accepted")
        self.assertTrue(report.terminal_evidence["independently_accepted"])

    def test_report_and_certificate_forged_together_are_rejected(self):
        bundle = calibration_bundle()
        bundle["report"]["selective_risk"] = 0.25
        bundle["certificate"]["report_sha256"] = canonical_sha256(bundle["report"])
        refresh_certificate_id(bundle["certificate"])
        report = audit_r2_calibration_bundle(bundle)
        self.assertEqual(report.status, "contradiction")
        self.assertIn(
            "r2_calibration_report_mismatch",
            {item.code for item in report.findings},
        )

    def test_provider_identity_tampering_is_rejected(self):
        bundle = calibration_bundle()
        bundle["provider_identity"]["model"] = "different-model"
        report = audit_r2_calibration_bundle(bundle)
        codes = {item.code for item in report.findings}
        self.assertIn("r2_calibration_shadow_authority_violation", codes)
        self.assertIn("r2_calibration_certificate_mismatch", codes)

    def test_raw_response_and_private_reasoning_are_rejected(self):
        bundle = calibration_bundle()
        bundle["records"][0]["raw_provider_response"] = "raw response"
        bundle["records"][1]["private_reasoning"] = "<think>hidden</think>"
        report = audit_r2_calibration_bundle(bundle)
        self.assertIn(
            "r2_calibration_private_payload_persisted",
            {item.code for item in report.findings},
        )

    def test_shadow_mutation_or_authority_is_rejected(self):
        for mutate in (
            lambda item: item["shadow"]["equivalence"].update(
                {"equivalent": False, "changed_fields": ["status"]}
            ),
            lambda item: item["advisory"].update({"accepted": True}),
        ):
            with self.subTest(mutate=mutate):
                bundle = deepcopy(calibration_bundle())
                mutate(bundle["records"][0])
                report = audit_r2_calibration_bundle(bundle)
                self.assertIn(
                    "r2_calibration_shadow_authority_violation",
                    {item.code for item in report.findings},
                )


if __name__ == "__main__":
    unittest.main()
