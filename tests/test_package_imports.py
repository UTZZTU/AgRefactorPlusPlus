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


    def test_evaluation_imports_first_in_fresh_process(
        self,
    ) -> None:
        code = (
            "from agrefactor.evaluation import "
            "CsimSuiteEvaluator, TestbenchPreflight; "
            "from agrefactor.runtime import "
            "CsynthValidationStageHandler, "
            "PreflightValidationStageHandler, "
            "TraceRecorder, ValidationOrchestrator; "
            "print('fresh-evaluation-import-ok')"
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
            "fresh-evaluation-import-ok",
            completed.stdout,
        )

    def test_runtime_integrations_are_lazy(
        self,
    ) -> None:
        code = (
            "import sys; "
            "import agrefactor.runtime as runtime; "
            "blocked = ["
            "'agrefactor.runtime.preflight_stage', "
            "'agrefactor.runtime.csynth_stage', "
            "'agrefactor.runtime.validation_orchestrator'"
            "]; "
            "assert not any(name in sys.modules for name in blocked); "
            "_ = runtime.PreflightStageInputs; "
            "assert 'agrefactor.runtime.preflight_stage' "
            "in sys.modules; "
            "print('runtime-lazy-exports-ok')"
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
            "runtime-lazy-exports-ok",
            completed.stdout,
        )


    def test_csim_runtime_export_is_lazy_in_fresh_process(
        self,
    ) -> None:
        code = (
            "import sys; "
            "import agrefactor.runtime as runtime; "
            "assert 'agrefactor.runtime.csim_stage' "
            "not in sys.modules; "
            "assert runtime.CsimStageInputs is not None; "
            "assert runtime.CsimValidationStageHandler "
            "is not None; "
            "assert runtime.read_csim_invocation_summary "
            "is not None; "
            "assert 'agrefactor.runtime.csim_stage' "
            "in sys.modules; "
            "print('fresh-csim-lazy-import-ok')"
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
            "fresh-csim-lazy-import-ok",
            completed.stdout,
        )

    def test_evaluation_first_resolves_csim_runtime_in_fresh_process(
        self,
    ) -> None:
        code = (
            "from agrefactor.evaluation import "
            "CsimSuiteEvaluator, TestbenchPreflight; "
            "from agrefactor.runtime import "
            "CsimStageInputs, "
            "CsimValidationStageHandler, "
            "PreflightValidationStageHandler, "
            "TraceRecorder, ValidationOrchestrator; "
            "assert CsimSuiteEvaluator is not None; "
            "assert TestbenchPreflight is not None; "
            "assert CsimStageInputs is not None; "
            "assert CsimValidationStageHandler is not None; "
            "assert PreflightValidationStageHandler "
            "is not None; "
            "assert TraceRecorder is not None; "
            "assert ValidationOrchestrator is not None; "
            "print('fresh-evaluation-csim-import-ok')"
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
            "fresh-evaluation-csim-import-ok",
            completed.stdout,
        )


if __name__ == "__main__":
    unittest.main()
