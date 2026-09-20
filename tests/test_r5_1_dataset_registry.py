from __future__ import annotations

from collections import defaultdict
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "r5_1_build_dataset_registry",
    ROOT / "scripts" / "r5_1_build_dataset_registry.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class R51DatasetRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.info = MODULE.load_object(ROOT / "src" / "info.json")
        cls.policy = MODULE.load_object(
            ROOT / "configs" / "r5_1" / "deduplication_policy.json"
        )
        cls.registry = MODULE.build_registry(ROOT, cls.info, cls.policy)

    def test_all_57_internal_cases_have_stable_identity(self) -> None:
        cases = self.registry["cases"]
        self.assertEqual(len(cases), 57)
        self.assertEqual(len({case["case_id"] for case in cases}), 57)
        self.assertEqual(len({case["source_path"] for case in cases}), 57)
        self.assertEqual(self.registry["summary"]["legacy_useful_count"], 49)
        self.assertEqual(self.registry["summary"]["legacy_not_useful_count"], 8)

    def test_build_is_byte_deterministic(self) -> None:
        rebuilt = MODULE.build_registry(ROOT, self.info, self.policy)
        self.assertEqual(MODULE.canonical(self.registry), MODULE.canonical(rebuilt))
        unsigned = {
            key: value
            for key, value in self.registry.items()
            if key != "registry_sha256"
        }
        self.assertEqual(self.registry["registry_sha256"], MODULE.sha_value(unsigned))

    def test_semantic_family_never_crosses_history_future(self) -> None:
        partitions: dict[str, set[str]] = defaultdict(set)
        for case in self.registry["cases"]:
            partitions[case["algorithm_family"]].add(
                case["history_future_partition"]
            )
        self.assertTrue(all(len(values) == 1 for values in partitions.values()))

    def test_d2_is_recall_only(self) -> None:
        for case in self.registry["cases"]:
            levels = {ref["level"] for ref in case["nearest_duplicate_refs"]}
            if levels == {"D2"} or ("D2" in levels and not levels & {"D0", "D1"}):
                self.assertNotEqual(case["eligibility"], "duplicate_excluded")

    def test_execution_sources_participate_in_global_dedup(self) -> None:
        duplicate_cases = (
            self.registry["summary"]["d0_case_count"]
            + self.registry["summary"]["d1_case_count"]
        )
        self.assertGreaterEqual(duplicate_cases, 2)
        inconsistent = [
            case
            for case in self.registry["cases"]
            if case["execution_contract"]["status"] == "inconsistent"
        ]
        self.assertGreaterEqual(len(inconsistent), 1)
        for case in inconsistent:
            self.assertIn(
                "vitis_tcl_execution_contract_inconsistent",
                case["exclusion_reasons"],
            )

    def test_unresolved_license_is_quarantined(self) -> None:
        for case in self.registry["cases"]:
            if (
                case["upstream"].get("license_status")
                != "verified_for_official_repository"
            ):
                self.assertIn(case["eligibility"], {"quarantine", "duplicate_excluded"})

    def test_registry_matches_checked_in_artifact(self) -> None:
        checked = json.loads(
            (ROOT / "configs" / "r5_1" / "dataset_registry.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(checked, self.registry)


if __name__ == "__main__":
    unittest.main()
