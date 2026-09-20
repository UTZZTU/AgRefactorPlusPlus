from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "r5_1_audit_ordinary_refactor",
    ROOT / "scripts" / "r5_1_audit_ordinary_refactor.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


class R51OrdinaryRefactorAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.protocol_path = ROOT / "configs" / "r5_1" / "ordinary_refactor_protocol.json"
        cls.protocol = MODULE.load_object(cls.protocol_path)

    def _fixture(self, root: Path) -> tuple[Path, Path]:
        protocol_audit = {
            "status": "passed",
            "protocol_sha256": MODULE.canonical_sha256(self.protocol),
            "audit_sha256": "a" * 64,
        }
        protocol_audit_path = root / "protocol_audit.json"
        _write(protocol_audit_path, protocol_audit)
        records = []
        for case in self.protocol["cases"]:
            evidence = root / "cases" / case["case_id"] / "material" / "source.cpp"
            evidence.parent.mkdir(parents=True)
            evidence.write_text("void source_only() {}\n", encoding="utf-8")
            records.append(
                {
                    "case_id": case["case_id"],
                    "eligible_as_pristine_p9_future": False,
                    "outcome": "infrastructure_failure",
                    "candidate_changed": None,
                    "budget_usage": {
                        "llm_calls": 11,
                        "csim_calls": 3,
                        "csynth_calls": 3,
                        "cosim_calls": 2,
                        "vitis_launches": 8,
                    },
                    "evidence_inventory": [
                        {
                            "path": evidence.relative_to(root).as_posix(),
                            "sha256": MODULE.file_sha256(evidence),
                            "size_bytes": evidence.stat().st_size,
                        }
                    ],
                }
            )
        result = {
            "schema_version": 1,
            "campaign_id": "v2.3-r5.1-p5-ordinary-refactor-v1",
            "status": "ready_for_independent_audit",
            "protocol_file_sha256": MODULE.file_sha256(self.protocol_path),
            "protocol_sha256": MODULE.canonical_sha256(self.protocol),
            "protocol_audit_file_sha256": MODULE.file_sha256(protocol_audit_path),
            "protocol_audit_sha256": protocol_audit["audit_sha256"],
            "cases": records,
            "planned_case_count": 13,
            "executed_case_count": 13,
            "outcome_counts": {"infrastructure_failure": 13},
            "provider_calls": 143,
            "vitis_launches": 104,
            "git_history_mutations": 0,
            "working_tree_mutations": 0,
            "hidden_evidence_claimed": False,
            "r5_accepted": False,
            "r6_started": False,
        }
        result["result_sha256"] = MODULE.canonical_sha256(result)
        result_path = root / "result.json"
        _write(result_path, result)
        return protocol_audit_path, result_path

    def test_protocol_shape_passes_and_rejects_weakened_boundary(self) -> None:
        self.assertEqual(MODULE.validate_protocol_shape(self.protocol), [])
        weakened = copy.deepcopy(self.protocol)
        weakened["product_contract"]["hidden_tests"] = "auto"
        codes = {item["code"] for item in MODULE.validate_protocol_shape(weakened)}
        self.assertIn("product_contract_changed", codes)

    def test_clean_conservative_fixture_recomputes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            audit_path, result_path = self._fixture(Path(temporary))
            audit = MODULE.audit_result(ROOT, self.protocol_path, audit_path, result_path)
            self.assertEqual(audit["status"], "passed")
            self.assertEqual(audit["recomputed_provider_calls"], 143)
            self.assertEqual(audit["recomputed_vitis_launches"], 104)

    def test_result_audit_rejects_path_escape_and_outcome_spoof(self) -> None:
        for mutation, code in (
            ("escape", "evidence_path_escape"),
            ("outcome", "outcome_mismatch"),
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                audit_path, result_path = self._fixture(Path(temporary))
                result = MODULE.load_object(result_path)
                if mutation == "escape":
                    result["cases"][0]["evidence_inventory"][0]["path"] = "../outside.json"
                else:
                    result["cases"][0]["outcome"] = "public_refactor_lift_actionable"
                result["result_sha256"] = MODULE.canonical_sha256(
                    {key: value for key, value in result.items() if key != "result_sha256"}
                )
                _write(result_path, result)
                audit = MODULE.audit_result(ROOT, self.protocol_path, audit_path, result_path)
                self.assertEqual(audit["status"], "failed")
                self.assertIn(code, {item["code"] for item in audit["findings"]})

    def test_private_payload_is_rejected(self) -> None:
        findings: list[dict[str, str]] = []
        MODULE._scan_forbidden(
            {"nested": {"private_reasoning": "do not persist"}},
            findings,
            "fixture",
        )
        self.assertIn("private_payload_persisted", {item["code"] for item in findings})


if __name__ == "__main__":
    unittest.main()
