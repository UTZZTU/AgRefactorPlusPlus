from __future__ import annotations

import ast
import inspect
import unittest

import agrefactor.evidence.auditor as auditor_module
from agrefactor.evidence import audit_product_evidence


def _summary(status="rejected", failed_stage="csynth"):
    return {
        "status": status,
        "failed_stage": None if status == "accepted" else failed_stage,
        "reason_code": (
            None if status == "accepted" else "unsupported_construct"
        ),
        "failure_owner": (
            None if status == "accepted" else "candidate"
        ),
        "route_action": (
            None if status == "accepted" else "repair_candidate"
        ),
        "validation": {
            "csynth": "passed" if status == "accepted" else "failed"
        },
        "execution_identity": {
            "execution_id": "run-1",
            "bundle_sha256": "a" * 64,
        },
    }


def _identity():
    return {
        "execution_id": "run-1",
        "bundle_sha256": "a" * 64,
        "validation": {
            "stage": "csynth",
            "status": "failed",
            "blocking": True,
            "failure_kind": "unsupported_construct",
            "failure_owner": "candidate",
            "route_action": "repair_candidate",
            "terminal": True,
        },
    }


class R5ABEvidenceAuditorTests(unittest.TestCase):
    def test_matching_rejection_is_clean(self):
        report = audit_product_evidence(_summary(), _identity())
        self.assertEqual(report.status, "clean")
        self.assertFalse(report.has_errors)

    def test_accepted_with_blocking_terminal_is_critical(self):
        report = audit_product_evidence(
            _summary(status="accepted"),
            _identity(),
        )
        self.assertTrue(report.has_critical)
        self.assertIn(
            "false_success_blocking_evidence",
            {item.code for item in report.findings},
        )

    def test_accepted_with_nonzero_process_is_critical(self):
        report = audit_product_evidence(
            _summary(status="accepted"),
            {
                "execution_id": "run-1",
                "bundle_sha256": "a" * 64,
            },
            process_record={"exit_code": 1, "timed_out": False},
        )
        self.assertTrue(report.has_critical)
        self.assertIn(
            "false_success_process_failure",
            {item.code for item in report.findings},
        )

    def test_identity_conflict_is_critical(self):
        identity = _identity()
        identity["bundle_sha256"] = "b" * 64
        report = audit_product_evidence(_summary(), identity)
        self.assertTrue(report.has_critical)
        self.assertIn(
            "identity_bundle_sha256_conflict",
            {item.code for item in report.findings},
        )

    def test_stale_unrelated_action_does_not_override_terminal(self):
        identity = _identity()
        identity["history"] = {
            "stage": "public",
            "blocking": True,
            "failure_kind": "ownership_unknown",
            "failure_owner": "unknown",
            "route_action": "review_unknown",
        }
        report = audit_product_evidence(_summary(), identity)
        self.assertFalse(report.has_errors)

    def test_auditor_does_not_import_product_reducer(self):
        source = inspect.getsource(auditor_module)
        tree = ast.parse(source)
        imported_targets = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_targets.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_targets.add(node.module)
                imported_targets.update(
                    f"{node.module}.{alias.name}"
                    for alias in node.names
                    if alias.name != "*"
                )
        self.assertNotIn(
            "agrefactor.product.run_output",
            imported_targets,
        )


if __name__ == "__main__":
    unittest.main()
