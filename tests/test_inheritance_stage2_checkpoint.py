from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEAL = load_script("inheritance_stage2_seal")
AUDIT = load_script("inheritance_stage2_audit")


class InheritanceStage2CheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inputs = json.loads(
            (
                ROOT
                / "configs"
                / "inheritance"
                / "stage2"
                / "checkpoint_inputs.json"
            ).read_text(encoding="utf-8")
        )

    def test_selection_freezes_all_four_cases_and_budget(self) -> None:
        self.assertEqual(
            [item["case_id"] for item in self.inputs["cases"]],
            ["dfs", "mergesort", "ahocorasick", "strassen"],
        )
        self.assertEqual(self.inputs["budget"]["provider_calls_expected"], 60)
        self.assertEqual(self.inputs["budget"]["vitis_launches_expected"], 23)
        self.assertLessEqual(
            self.inputs["budget"]["provider_calls_expected"],
            self.inputs["budget"]["provider_calls_hard_cap"],
        )
        self.assertLessEqual(
            self.inputs["budget"]["vitis_launches_expected"],
            self.inputs["budget"]["vitis_launches_hard_cap"],
        )

    def test_seal_anchor_check_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "evidence.json"
            path.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "evidence hash mismatch"):
                SEAL.require_hash(path, "0" * 64)

    def test_private_reasoning_and_plaintext_flags_fail_closed(self) -> None:
        clean = {
            "plaintext_prompts_persisted": False,
            "plaintext_responses_persisted": False,
            "optimizer_calls": [
                {
                    "metadata": {
                        "private_reasoning_persisted": False,
                        "legacy_ag2_transport": {
                            "private_reasoning_persisted": False
                        },
                    }
                }
            ],
        }
        self.assertTrue(AUDIT.private_reasoning_absent(clean))
        dirty = json.loads(json.dumps(clean))
        dirty["optimizer_calls"][0]["metadata"][
            "private_reasoning_persisted"
        ] = True
        self.assertFalse(AUDIT.private_reasoning_absent(dirty))

    def test_audit_accumulates_named_failures(self) -> None:
        audit = AUDIT.Audit()
        audit.check("valid", True)
        audit.check("invalid", False)
        self.assertTrue(audit.checks["valid"])
        self.assertFalse(audit.checks["invalid"])
        self.assertEqual(audit.failures, ["invalid"])


if __name__ == "__main__":
    unittest.main()
