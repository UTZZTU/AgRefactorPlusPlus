import importlib
import subprocess
import sys
import unittest


class PackageImportTests(unittest.TestCase):
    def test_stage1_packages_import(self) -> None:
        modules = [
            "agrefactor",
            "agrefactor.config",
            "agrefactor.models",
            "agrefactor.evaluation",
            "agrefactor.evidence",
            "agrefactor.runtime",
            "agrefactor.testing",
        ]
        for module_name in modules:
            with self.subTest(module=module_name):
                importlib.import_module(module_name)

    def test_runtime_imports_first_in_fresh_process(
        self,
    ) -> None:
        code = (
            "from agrefactor.runtime import "
            "PreflightStageInputs, "
            "PreflightValidationStageHandler, "
            "TraceRecorder; "
            "from agrefactor.evaluation import "
            "CsimSuiteEvaluator; "
            "print('fresh-runtime-import-ok')"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(
            completed.returncode,
            0,
            msg=completed.stderr,
        )
        self.assertIn(
            "fresh-runtime-import-ok",
            completed.stdout,
        )


if __name__ == "__main__":
    unittest.main()
