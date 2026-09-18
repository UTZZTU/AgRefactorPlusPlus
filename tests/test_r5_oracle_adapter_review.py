from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "r5_oracle_adapter_review",
    ROOT / "scripts" / "r5_oracle_adapter_review.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _audit(*, public_hidden_distinct: bool = False) -> dict:
    files = {
        role: {
            "path": f"family_E1_case/{role}.cpp",
            "present": True,
            "sha256": "a" * 64,
        }
        for role in ("source", "reference", "legacy_testbench")
    }
    files["tcl"] = {
        "path": "family_E1_case/vitis.tcl",
        "present": True,
        "sha256": "b" * 64,
    }
    case = {
        "case_id": "family_E1_case",
        "status": "adapter_required",
        "reasons": ["legacy_testbench_includes_implementation"],
        "files": files,
        "target": {"stages": {"csim": True, "csynth": True, "cosim": True}},
        "testbench": {
            "public_hidden_distinct": public_hidden_distinct,
            "candidate_linked_by_tcl": False,
            "return_contract": {"nonzero_failure_path_present": False},
            "includes": {"includes_implementation": True},
            "data_dependencies": {"missing_local": []},
        },
    }
    audit = {
        "schema_version": 1,
        "audit_id": "test",
        "cases": [case, {"case_id": "reject", "status": "rejected"}],
    }
    audit["audit_sha256"] = MODULE._sha(
        {key: value for key, value in audit.items()}
    )
    return audit


class R5OracleAdapterReviewTests(unittest.TestCase):
    def test_review_is_blocked_without_independent_oracles(self) -> None:
        result = MODULE.build_review(_audit())
        self.assertEqual(result["status"], "blocked_data_acquisition")
        self.assertEqual(result["adapter_required_count"], 1)
        self.assertEqual(result["rejected_case_ids"], ["reject"])
        self.assertFalse(result["real_campaign_allowed"])
        self.assertEqual(result["provider_calls"], 0)
        self.assertIn(
            "reviewed_public_hidden_pair_missing",
            result["adapters"][0]["blockers"],
        )

    def test_review_never_creates_expected_oracle_fields(self) -> None:
        result = MODULE.build_review(_audit(public_hidden_distinct=True))
        generated = {
            key: value
            for key, value in result.items()
            if key not in {"adapters"}
        }
        generated["adapters"] = [
            {
                key: value
                for key, value in result["adapters"][0].items()
                if key != "forbidden_adapter_actions"
            }
        ]
        serialized = str(generated)
        self.assertNotIn("expected_output", serialized)
        self.assertNotIn("expected_outcome", serialized)
        self.assertIn(
            "invent_expected_output_or_failure_outcome",
            result["adapters"][0]["forbidden_adapter_actions"],
        )

    def test_audit_hash_is_required(self) -> None:
        audit = _audit()
        audit["cases"][0]["case_id"] = "tampered"
        with self.assertRaises(ValueError):
            MODULE.build_review(audit)

    def test_checked_in_audit_has_no_real_calls(self) -> None:
        audit_path = Path(
            "/data/agrefactor_runs/r5_p2_hlsrewritter_audit/"
            "hlsrewritter_case_audit.json"
        )
        if not audit_path.exists():
            self.skipTest("server-only audit")
        result = MODULE.build_review(MODULE._load(audit_path))
        self.assertEqual(result["adapter_required_count"], 13)
        self.assertEqual(result["rejected_count"], 7)
        self.assertEqual(result["eligible_direct_count"], 0)
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(result["vitis_launches"], 0)


if __name__ == "__main__":
    unittest.main()
