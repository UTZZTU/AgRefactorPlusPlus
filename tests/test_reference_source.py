from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from agrefactor.reference_source import isolate_reference_program_entry
from flow.tools.tb_coverage import check_original_execution


class ReferenceSourceTests(unittest.TestCase):
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
