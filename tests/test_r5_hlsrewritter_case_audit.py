from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "r5_hlsrewritter_case_audit",
    ROOT / "scripts" / "r5_hlsrewritter_case_audit.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


TCL = """\
open_project csyn
set_top top
add_files \"kernel.cpp\"
open_solution solution
set_part xcu200-fsgd2104-2-e
create_clock -period 200MHz -name default
csynth_design
"""


class R5HlsrewritterCaseAuditTests(unittest.TestCase):
    def _case(self, root: Path, *, testbench: str, tcl: str = TCL) -> Path:
        case = root / "family_E1_case"
        case.mkdir(parents=True)
        (case / "main.cpp").write_text("int top(int x) { return x + 1; }\n")
        (case / "kernel.cpp").write_text("int top(int x) { return x + 1; }\n")
        (case / "tb.cpp").write_text(testbench)
        (case / "vitis.tcl").write_text(tcl)
        return case

    def test_legacy_comparison_case_requires_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            dataset = repo / "src" / "hlsrewritter"
            case = self._case(
                dataset,
                testbench=(
                    '#include "main.cpp"\n'
                    "int main() { int expected = 2; int got = top(1); "
                    'if (got == expected) { return 0; } return 0; }\n'
                ),
            )
            result = MODULE.audit_case(case, dataset_root=dataset, repository=repo)
            self.assertEqual(result["status"], "adapter_required")
            self.assertIn(
                "legacy_testbench_includes_implementation", result["reasons"]
            )
            self.assertIn(
                "no_enforceable_process_failure_oracle", result["reasons"]
            )
            self.assertFalse(result["admission"]["real_campaign_eligible"])

    def test_print_only_case_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            dataset = repo / "src" / "hlsrewritter"
            case = self._case(
                dataset,
                testbench=(
                    "#include <iostream>\n"
                    "int top(int);\n"
                    'int main() { std::cout << top(1); return 0; }\n'
                ),
            )
            result = MODULE.audit_case(case, dataset_root=dataset, repository=repo)
            self.assertEqual(result["status"], "rejected")
            self.assertIn(
                "no_static_expected_or_comparison_evidence", result["reasons"]
            )

    def test_data_dependency_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            dataset = repo / "src" / "hlsrewritter"
            case = self._case(
                dataset,
                testbench=(
                    '#include "main.cpp"\n'
                    '#include <fstream>\n'
                    'int main() { std::ifstream f("missing.txt"); return 0; }\n'
                ),
            )
            result = MODULE.audit_case(case, dataset_root=dataset, repository=repo)
            self.assertIn("external_test_data_missing", result["reasons"])
            self.assertTrue(
                result["testbench"]["data_dependencies"][
                    "dynamic_paths_may_require_manual_review"
                ]
            )

    def test_complete_distinct_oracles_can_be_directly_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            dataset = repo / "src" / "hlsrewritter"
            tcl = TCL.replace(
                'add_files "kernel.cpp"',
                'add_files "candidate.cpp"\n'
                'add_files -tb "public_tb.cpp"\n'
                'add_files -tb "hidden_tb.cpp"',
            ).replace("csynth_design", "csim_design\ncsynth_design\ncosim_design")
            testbench = (
                "int top(int);\n"
                "int main() { int expected = 2; return top(1) == expected ? 0 : 1; }\n"
            )
            case = self._case(dataset, testbench=testbench, tcl=tcl)
            (case / "public_tb.cpp").write_text(testbench)
            (case / "hidden_tb.cpp").write_text(testbench.replace("1)", "2)").replace("2;", "3;"))
            result = MODULE.audit_case(case, dataset_root=dataset, repository=repo)
            self.assertEqual(result["status"], "eligible_direct")
            self.assertEqual(result["reasons"], [])
            self.assertTrue(result["admission"]["real_campaign_eligible"])

    def test_repository_inventory_has_twenty_unadmitted_cases(self) -> None:
        dataset = ROOT / "src" / "hlsrewritter"
        result = MODULE.build_audit(dataset, repository=ROOT)
        self.assertEqual(result["case_count"], 20)
        self.assertEqual(result["classification_counts"]["eligible_direct"], 0)
        self.assertFalse(result["real_campaign_allowed"])
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(result["vitis_launches"], 0)


if __name__ == "__main__":
    unittest.main()
