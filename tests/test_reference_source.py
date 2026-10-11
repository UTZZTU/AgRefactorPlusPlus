from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from agrefactor.reference_source import isolate_reference_program_entry
from flow.tools.tb_coverage import _prepare_original_reference, check_original_execution, measure_coverage


class ReferenceSourceTests(unittest.TestCase):
    def test_direct_template_calls_keep_definition_in_shared_translation_unit(self):
        source = "template<int Extent> void transform(int (&values)[Extent]) { values[0] += 2; }"
        driver = "template<int Extent> void transform(int (&values)[Extent]); int main(){int values[3]={};transform<3>(values);return values[0]!=2;}"
        reference = isolate_reference_program_entry(source, top_function="transform", testbench_code=driver)
        self.assertNotIn("agrefactor_reference_arg_", reference)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "all.cpp").write_text(reference + driver)
            compiled = subprocess.run(["g++", "all.cpp", "-o", "check"], cwd=root, capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            self.assertEqual(subprocess.run([str(root / "check")]).returncode, 0)

    def test_template_without_testbench_does_not_invent_dependent_bridge_aliases(self):
        source = "template<int Extent> void transform(int (&values)[Extent]) { values[0] += 2; }"
        reference = isolate_reference_program_entry(source, top_function="transform")
        self.assertNotIn("agrefactor_reference_arg_", reference)

    def test_internal_free_function_is_bridged_and_really_executed(self):
        for source in (
            "static int entry(int value) { return value + 7; }\n",
            "namespace { int entry(int value) { return value + 7; } }\n",
        ):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                result = check_original_execution(
                    source,
                    "int entry(int);\nint candidate(int);\nint main() { return entry(2) == 9 ? 0 : 1; }\n",
                    "int candidate(int);", "candidate", original_name="entry",
                    required_original_entry="entry", keep_dir=directory,
                )
                self.assertEqual(result["status"], "ok", result)
                self.assertTrue(result["original_entry_executed"], result)
                self.assertTrue(result["prepared_reference_sha256"])
                self.assertTrue(any(item["component"] == "gcov" for item in result["executions"]))
                self.assertIn("agrefactor_reference_impl_entry", (Path(directory) / "orig_code.cpp").read_text())

    def test_internal_free_function_is_bridged_for_coverage(self):
        result = measure_coverage(
            "namespace { int entry(int value) { return value + 7; } }\n",
            "int entry(int);\nint candidate(int);\nint main() { return entry(2) == candidate(2) ? 0 : 1; }\n",
            "int candidate(int value) { return value + 7; }\n",
            original_name="entry",
        )
        self.assertEqual(result["status"], "ok", result)
        self.assertGreater(result["lines_hit"], 0, result)

    def test_prepared_global_state_bridge_is_not_rewrapped(self):
        source = "namespace { int state; void entry() { ++state; } }\n"
        prepared = isolate_reference_program_entry(source, top_function="entry")
        spans = []
        repeated = _prepare_original_reference(
            prepared, "entry", testbench_code="", source_root=None,
            work_dir=tempfile.gettempdir(), compile_flags=(), source_provenance=spans,
        )
        self.assertEqual(repeated, prepared)
        result = check_original_execution(
            prepared,
            "void entry(int&);\nint candidate();\nint main() { int state=4; entry(state); return state == 5 ? 0 : 1; }\n",
            "int candidate();", "candidate", original_name="entry",
            required_original_entry="entry",
        )
        self.assertEqual(result["status"], "ok", result)

    def test_member_and_ambiguous_internal_entries_still_stop(self):
        for source in (
            "struct Object { static int entry() { return 7; } };\n",
            "static int entry() { return 7; }\nstatic int entry(int value) { return value; }\n",
        ):
            with self.subTest(source=source):
                result = check_original_execution(
                    source, "int entry();\nint main() { return entry(); }\n",
                    "int candidate();", "candidate", original_name="entry",
                )
                self.assertEqual(result["status"], "entry_contract_failed", result)
                self.assertEqual(result["next_action"], "review_configuration", result)
                self.assertEqual(result["executions"], [])

    def test_source_without_program_entry_keeps_runtime_behavior(self):
        result = check_original_execution(
            "int top() { return 7; }\n",
            "int top();\nint main() { return top() == 7 ? 0 : 1; }\n",
            "void top_hls();",
            "top_hls",
        )
        self.assertEqual(result["status"], "ok", result)

    def test_original_only_execution_isolates_program_entry(self):
        result = check_original_execution(
            "int top() { return 7; }\nint main() { return 99; }\n",
            "int top();\nint main() { return top() == 7 ? 0 : 1; }\n",
            "void top_hls();",
            "top_hls",
        )
        self.assertEqual(result["status"], "ok", result)

    def test_main_text_in_comment_and_string_does_not_change_behavior(self):
        result = check_original_execution(
            '/* main( */\nconst char *label = "main(";\nint top() { return 7; }\n',
            "int top();\nint main() { return top() == 7 ? 0 : 1; }\n",
            "void top_hls();",
            "top_hls",
        )
        self.assertEqual(result["status"], "ok", result)

    def test_original_program_entry_does_not_conflict_with_testbench(self):
        compiler = shutil.which("g++") or shutil.which("c++")
        if compiler is None:
            self.skipTest("C++ compiler unavailable")
        original = isolate_reference_program_entry(
            "int top() { return 7; }\nint main() { return 99; }\n"
        )
        testbench = "int top();\nint main() { return top() == 7 ? 0 : 1; }\n"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "original.cpp").write_text(original, encoding="utf-8")
            (root / "testbench.cpp").write_text(testbench, encoding="utf-8")
            executable = root / "testbench"
            subprocess.run(
                [compiler, "original.cpp", "testbench.cpp", "-o", str(executable)],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            completed = subprocess.run([str(executable)], cwd=root, check=False)
        self.assertEqual(completed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
