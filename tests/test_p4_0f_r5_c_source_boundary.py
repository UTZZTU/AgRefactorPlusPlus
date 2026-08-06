from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agrefactor.cli import build_parser
from agrefactor.config import RunMode
from agrefactor.product import (
    OriginalCsynthEvidence,
    RefactorEligibilityReport,
    SourceCommandRejected,
    build_test_source_plan,
    run_source_command,
)
from agrefactor.product.source_bootstrap import (
    _evaluate_refactor_source_eligibility,
)


PRIVATE_GLOBAL = r'''
static int state = 0;
extern "C" int top(int x) {
    state += x;
    return state;
}
'''

EXPLICIT_IO = r'''
extern "C" int top(int x, int *out) {
    out[0] = x;
    return 0;
}
'''


class R5CSourceBoundaryTests(unittest.TestCase):
    def test_product_exports_typed_report(self):
        self.assertEqual(
            RefactorEligibilityReport.__name__,
            "RefactorEligibilityReport",
        )
        self.assertEqual(
            OriginalCsynthEvidence.__name__,
            "OriginalCsynthEvidence",
        )

    def test_auto_private_global_is_rejected_before_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "monobit_like.cpp"
            source.write_text(PRIVATE_GLOBAL, encoding="utf-8")
            report = _evaluate_refactor_source_eligibility(
                source=source,
                top_function="top",
                mode=RunMode.REFACTOR,
                plan=build_test_source_plan(),
            )
        self.assertIsNotNone(report)
        self.assertFalse(report.execution_allowed)
        self.assertEqual(
            report.boundary.private_global_dependencies,
            ("state",),
        )

    def test_provided_public_test_allows_stateful_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "stateful.cpp"
            public = root / "public.cpp"
            source.write_text(PRIVATE_GLOBAL, encoding="utf-8")
            public.write_text("int main(){return 0;}\n", encoding="utf-8")
            report = _evaluate_refactor_source_eligibility(
                source=source,
                top_function="top",
                mode=RunMode.REFACTOR,
                plan=build_test_source_plan(
                    public_paths=(public,),
                    hidden_mode="none",
                ),
            )
        self.assertIsNotNone(report)
        self.assertTrue(report.execution_allowed)
        self.assertIn(
            "operator_provided_public_tests",
            report.reason_codes,
        )

    def test_direct_optimize_does_not_use_refactor_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "candidate.cpp"
            source.write_text(PRIVATE_GLOBAL, encoding="utf-8")
            report = _evaluate_refactor_source_eligibility(
                source=source,
                top_function="top",
                mode=RunMode.OPTIMIZE,
                plan=build_test_source_plan(),
            )
        self.assertIsNone(report)

    def test_run_source_command_persists_typed_prelaunch_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "monobit_like.cpp"
            output = root / "artifacts"
            source.write_text(PRIVATE_GLOBAL, encoding="utf-8")
            args = build_parser().parse_args(
                [
                    "refactor",
                    str(source),
                    "--top",
                    "top",
                    "--model",
                    "deepseek-v4-flash",
                    "--hidden-tests",
                    "none",
                    "--output-dir",
                    str(output),
                    "--run-id",
                    "r5-c-boundary",
                ]
            )
            with patch.dict(
                os.environ,
                {
                    "DEEPSEEK_API_KEY": "",
                    "AGREFACTOR_WORK_ROOT": str(root / "work"),
                },
                clear=False,
            ):
                with self.assertRaises(SourceCommandRejected) as captured:
                    run_source_command(args)

            rejection = json.loads(
                (output / "request_rejection.json").read_text(
                    encoding="utf-8"
                )
            )
            eligibility = json.loads(
                (output / "refactor_eligibility.json").read_text(
                    encoding="utf-8"
                )
            )
            manifest = json.loads(
                (output / "run_artifact_manifest.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(
            rejection["reason_code"],
            "auto_public_tests_private_global_dependency",
        )
        self.assertFalse(rejection["provider_call_observed"])
        self.assertEqual(
            captured.exception.rejection["kind"],
            "refactor_eligibility_rejected",
        )
        self.assertEqual(
            eligibility["execution_status"],
            "rejected",
        )
        self.assertIn(
            "refactor_eligibility.json",
            {
                item["relative_path"]
                for item in manifest["files"]
            },
        )


if __name__ == "__main__":
    unittest.main()
