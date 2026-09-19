from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "r5_audit_preexisting_history",
    ROOT / "scripts" / "r5_audit_preexisting_history.py",
)
assert SPEC is not None and SPEC.loader is not None
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class R5PreexistingHistoryAuditTests(unittest.TestCase):
    def test_candidate_hash_shape_is_strict(self) -> None:
        self.assertIsNotNone(AUDIT._SHA256.fullmatch("a" * 64))
        self.assertIsNone(AUDIT._SHA256.fullmatch("A" * 64))
        self.assertIsNone(AUDIT._SHA256.fullmatch("a" * 63))

    def test_accepted_legacy_split_has_narrow_implicit_scope(self) -> None:
        self.assertEqual(
            AUDIT._certificate_scope(
                {
                    "schema_version": 1,
                    "split_id": "v23-r2-real-vitis-unsupported-construct-v1",
                }
            ),
            ["unsupported_construct"],
        )

    def test_explicit_scope_is_preserved(self) -> None:
        self.assertEqual(
            AUDIT._certificate_scope(
                {
                    "schema_version": 2,
                    "failure_class_scope": ["unsupported_construct"],
                }
            ),
            ["unsupported_construct"],
        )

    def test_unscoped_certificate_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            AUDIT.PreexistingHistoryAuditError,
            "failure-class scope is missing",
        ):
            AUDIT._certificate_scope({"schema_version": 1, "split_id": "other"})


if __name__ == "__main__":
    unittest.main()
