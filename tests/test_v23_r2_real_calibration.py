from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "research" / "v23_r2_real_calibration.py"
SPEC = importlib.util.spec_from_file_location("v23_r2_real_calibration", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def manifest():
    return json.loads(
        (ROOT / "docs" / "roadmap" / "V2_3_R2_REAL_CALIBRATION.json").read_text(
            encoding="utf-8"
        )
    )


def event(*, parser_rule="unknown_fallback"):
    item_ref = "report.item.1"
    diagnostic_items = [{
        "evidence_ref": item_ref,
        "stage": "csynth",
        "category": "unknown",
        "severity": "error",
        "owner": "unknown",
        "summary": "Unclassified CSYNTH diagnostic",
        "detail": "ERROR: [HLS 999-999] unsupported construct",
        "diagnostic_code": "HLS 999-999",
        "parser_rule": parser_rule,
        "classification_confidence": "unknown",
    }]
    digest = MODULE.sha256_value(diagnostic_items)
    return {
        "schema_version": 2,
        "event_id": "diagnostic-calibration-test",
        "run_id": "run-calibration-test",
        "validation_id": "validation-calibration-test",
        "stage": "csynth",
        "owner": "unknown",
        "failure_classes": ["unknown"],
        "severities": ["error"],
        "route_action": "review_unknown",
        "repair_scope": "none_abstain",
        "evidence_refs": ["report", item_ref],
        "target_identity": {"name": "target", "fingerprint": "a" * 64},
        "toolchain_identity": {
            "toolchain": "vitis_hls",
            "toolchain_version": "2023.2",
            "fingerprint": "b" * 64,
        },
        "candidate_sha256": "c" * 64,
        "public_suite_identities": [{
            "suite_id": "public",
            "split": "public",
            "content_sha256": "d" * 64,
        }],
        "physical_tool_launched": True,
        "evidence_complete": True,
        "context_signature": "e" * 64,
        "created_at": "2026-09-18T00:00:00+00:00",
        "source_kind": "feedback_report",
        "diagnostic_items": diagnostic_items,
        "diagnostic_items_sha256": digest,
        "metadata": {"context_signature_includes_diagnostic_items": True},
        "evidence_view": "agent_safe",
        "hidden_input_count": 0,
        "hidden_content_persisted": False,
        "accepted": False,
        "success_authority": False,
        "fsm_mutation_allowed": False,
    }


class V23R2RealCalibrationTests(unittest.TestCase):
    def test_pool_is_pre_registered_and_sources_are_unique(self):
        selected_manifest = manifest()
        specs = MODULE.case_specs(selected_manifest)
        self.assertEqual(len(specs), 32)
        self.assertEqual(len({item["case_id"] for item in specs}), 32)
        hashes = {
            hashlib.sha256(
                MODULE.candidate_source(item["family"], item["variant"]).encode()
            ).hexdigest()
            for item in specs
        }
        self.assertEqual(len(hashes), 32)
        self.assertTrue(
            all(item["case_id"].startswith("v23-r2-calibration-") for item in specs)
        )

    def test_selection_requires_one_specific_real_unknown_event(self):
        value = {"diagnostic_events": [event()]}
        self.assertIsNotNone(MODULE.eligible_event(value, manifest()))
        value["diagnostic_events"] = [event(parser_rule="aggregate_source_synthesis")]
        self.assertIsNone(MODULE.eligible_event(value, manifest()))
        value["diagnostic_events"] = [event(), event()]
        self.assertIsNone(MODULE.eligible_event(value, manifest()))

    def test_manifest_freezes_budgets_policy_and_held_out_boundary(self):
        value = manifest()
        self.assertEqual(value["hard_budgets"], {
            "provider_calls": 200,
            "vitis_launches": 200,
        })
        self.assertEqual(value["contracts"]["prompt_contract_version"], "r2-shadow-output-v4")
        self.assertTrue(value["held_out_boundary"]["case_id_reuse_forbidden"])
        self.assertTrue(value["held_out_boundary"]["candidate_source_hash_reuse_forbidden"])
        self.assertEqual(value["product_entrypoints_unchanged"], ["refactor", "optimize", "full"])


if __name__ == "__main__":
    unittest.main()
