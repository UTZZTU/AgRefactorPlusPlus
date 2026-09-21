from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InheritanceStageOneInventoryTests(unittest.TestCase):
    def test_inventory_is_deterministic_and_covers_every_src_code_file(self) -> None:
        builder = load_module("inheritance_builder", ROOT / "scripts" / "build_inheritance_inventory.py")
        first = builder.build_inventory(ROOT)
        second = builder.build_inventory(ROOT)
        self.assertEqual(first, second)
        expected = {
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "src").rglob("*")
            if path.is_file() and path.suffix.lower() in builder.CODE_SUFFIXES
        }
        self.assertEqual({record["path"] for record in first["files"]}, expected)

    def test_source_only_heterorefactor_cases_are_not_excluded_for_missing_harness(self) -> None:
        builder = load_module("inheritance_builder_source_only", ROOT / "scripts" / "build_inheritance_inventory.py")
        inventory = builder.build_inventory(ROOT)
        records = {
            record["path"]: record for record in inventory["files"]
            if record["source_family"] == "heterorefactor"
        }
        self.assertEqual(len(records), 6)
        self.assertTrue(all(record["inheritance_testable"] for record in records.values()))
        self.assertTrue(all(record["existing_testbench_paths"] == [] for record in records.values()))
        self.assertTrue(all(record["existing_tcl_paths"] == [] for record in records.values()))

    def test_frozen_pilot_is_not_authorized_for_stage_two_execution(self) -> None:
        cohort = json.loads(
            (ROOT / "configs" / "inheritance" / "heterorefactor_cohort_v1.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(cohort["cases"]), 4)
        self.assertFalse(cohort["execution_authorized"])
        self.assertFalse(cohort["r2_r4_enabled"])
        self.assertFalse(cohort["memory_enabled"])

    def test_independent_stage_one_audit_passes(self) -> None:
        auditor = load_module("inheritance_auditor", ROOT / "scripts" / "audit_inheritance_inventory.py")
        result = auditor.audit(
            ROOT,
            ROOT / "configs" / "inheritance" / "inheritance_manifest_v1.json",
            ROOT / "configs" / "inheritance" / "heterorefactor_cohort_v1.json",
        )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(result["vitis_launches"], 0)


if __name__ == "__main__":
    unittest.main()
