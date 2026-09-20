from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "r5_1_audit_dataset_registry",
    ROOT / "scripts" / "r5_1_audit_dataset_registry.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class R51DatasetAuditTests(unittest.TestCase):
    def test_checked_in_registry_passes_zero_call_audit(self) -> None:
        audit = MODULE.audit_registry(ROOT)
        self.assertEqual(audit["status"], "passed_with_quarantine")
        self.assertEqual(audit["failures"], [])
        self.assertEqual(audit["checks"]["internal_case_count"], 57)
        self.assertEqual(audit["provider_calls"], 0)
        self.assertEqual(audit["vitis_launches"], 0)
        self.assertFalse(audit["r5_accepted"])
        self.assertFalse(audit["r6_started"])

    def test_external_admission_requires_commit_and_license(self) -> None:
        external = MODULE.load_object(
            ROOT / "configs" / "r5_1" / "external_source_manifest.json"
        )
        admitted = [
            source for source in external["sources"] if source["decision"] == "admit"
        ]
        self.assertGreaterEqual(len(admitted), 1)
        for source in admitted:
            self.assertRegex(source["commit"], r"^[0-9a-f]{40}$")
            self.assertNotEqual(source["license"], "NOASSERTION")

    def test_unlicensed_sources_are_not_admitted(self) -> None:
        external = MODULE.load_object(
            ROOT / "configs" / "r5_1" / "external_source_manifest.json"
        )
        for source in external["sources"]:
            if source["license"] == "NOASSERTION":
                self.assertIn(source["decision"], {"external-only", "quarantine", "reject"})


if __name__ == "__main__":
    unittest.main()
