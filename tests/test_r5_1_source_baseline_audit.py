from __future__ import annotations

import copy
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "r5_1_audit_source_baseline",
    ROOT / "scripts" / "r5_1_audit_source_baseline.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _seal(value: dict) -> dict:
    value["result_sha256"] = MODULE.sha_value(value)
    return value


class R51SourceBaselineAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = MODULE.load_object(
            ROOT / "configs" / "r5_1" / "source_baseline_protocol.json"
        )

    def _preflight(self) -> dict:
        cases = []
        for index, case in enumerate(self.plan["cases"]):
            cases.append(
                {
                    "case_id": case["case_id"],
                    "status": "passed",
                    "material_identity": {
                        "design_sha256": f"{index + 1:064x}",
                        "testbench_sha256": f"{index + 101:064x}",
                    },
                }
            )
        return _seal(
            {
                "status": "passed",
                "plan_sha256": MODULE.sha_value(self.plan),
                "cases": cases,
                "provider_calls": 0,
                "vitis_launches": 0,
                "git_history_mutations": 0,
                "working_tree_mutations": 0,
            }
        )

    def _result(self, root: Path, preflight: dict) -> dict:
        cases = []
        for planned, prior in zip(self.plan["cases"], preflight["cases"]):
            evidence = root / planned["case_id"] / "trace.jsonl"
            evidence.parent.mkdir(parents=True)
            evidence.write_text("{}\n", encoding="utf-8")
            cases.append(
                {
                    "case_id": planned["case_id"],
                    "material_identity": prior["material_identity"],
                    "classification": "raw_fail_ambiguous",
                    "terminal_state": "public_evaluation",
                    "stage_statuses": {
                        "S0_host_oracle": "passed",
                        "S1_public_csim": "failed",
                        "S2_csynth": "not_run",
                        "S3_public_cosim": "not_run",
                    },
                    "budget_usage": {
                        "llm_calls": 0,
                        "csim_calls": 0,
                        "csynth_calls": 0,
                        "cosim_calls": 0,
                    },
                    "evidence_inventory": [
                        {
                            "path": evidence.relative_to(root).as_posix(),
                            "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                        }
                    ],
                }
            )
        return _seal(
            {
                "status": "complete",
                "evidence_root": str(root),
                "plan_sha256": MODULE.sha_value(self.plan),
                "preflight_result_sha256": preflight["result_sha256"],
                "cases": cases,
                "classification_counts": {"raw_fail_ambiguous": len(cases)},
                "provider_calls": 0,
                "vitis_launches": 0,
                "git_history_mutations": 0,
                "working_tree_mutations": 0,
                "r5_accepted": False,
                "r6_started": False,
            }
        )

    def test_protocol_and_clean_preflight_pass(self) -> None:
        self.assertEqual(MODULE.audit_protocol(self.plan)["status"], "passed")
        self.assertEqual(
            MODULE.audit_preflight(self.plan, self._preflight())["status"],
            "passed",
        )

    def test_preflight_external_call_is_rejected(self) -> None:
        preflight = self._preflight()
        preflight["provider_calls"] = 1
        preflight["result_sha256"] = MODULE.sha_value(
            {key: value for key, value in preflight.items() if key != "result_sha256"}
        )
        audit = MODULE.audit_preflight(self.plan, preflight)
        self.assertEqual(audit["status"], "failed")
        self.assertIn(
            "unexpected_external_calls", {item["code"] for item in audit["findings"]}
        )

    def test_clean_result_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            preflight = self._preflight()
            result = self._result(Path(temporary), preflight)
            audit = MODULE.audit_result(self.plan, preflight, result)
            self.assertEqual(audit["status"], "passed")
            self.assertEqual(audit["critical_findings"], 0)

    def test_nonpass_terminal_stage_cannot_be_claimed_as_passed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            preflight = self._preflight()
            result = self._result(Path(temporary), preflight)
            result["cases"][0]["stage_statuses"]["S1_public_csim"] = "passed"
            result["result_sha256"] = MODULE.sha_value(
                {key: value for key, value in result.items() if key != "result_sha256"}
            )
            audit = MODULE.audit_result(self.plan, preflight, result)
            self.assertEqual(audit["status"], "failed")
            self.assertIn(
                "terminal_stage_claim_invalid",
                {item["code"] for item in audit["findings"]},
            )

    def test_evidence_path_escape_and_hash_mismatch_are_rejected(self) -> None:
        for mutation, expected in (("escape", "evidence_path_escape"), ("hash", "evidence_hash_mismatch")):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                preflight = self._preflight()
                result = self._result(Path(temporary), preflight)
                record = result["cases"][0]["evidence_inventory"][0]
                if mutation == "escape":
                    record["path"] = "../outside.json"
                else:
                    record["sha256"] = "0" * 64
                result["result_sha256"] = MODULE.sha_value(
                    {key: value for key, value in result.items() if key != "result_sha256"}
                )
                audit = MODULE.audit_result(self.plan, preflight, result)
                self.assertEqual(audit["status"], "failed")
                self.assertIn(expected, {item["code"] for item in audit["findings"]})


if __name__ == "__main__":
    unittest.main()
