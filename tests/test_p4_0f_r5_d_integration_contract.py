import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class IntegrationContractTests(unittest.TestCase):
    def test_candidate_loop_supports_public_cosim(self):
        text = (ROOT / "agrefactor/repair/candidate_loop.py").read_text()
        self.assertIn("ValidationState.PUBLIC_COSIM", text)
        self.assertIn("build_candidate_public_cosim_repair_prompt", text)

    def test_candidate_loop_uses_shared_recovery_ledger(self):
        text = (ROOT / "agrefactor/repair/candidate_loop.py").read_text()
        self.assertIn("recovery_ledger.reserve", text)
        self.assertIn("recovery_ledger.record_validation_restart", text)
        self.assertIn("default_restart_reserve", text)

    def test_orchestrator_has_testbench_recovery_path(self):
        text = (ROOT / "agrefactor/runtime/candidate_repair_integration.py").read_text()
        self.assertIn("_recover_public_testbench", text)
        self.assertIn("FeedbackRouteAction.REPAIR_TESTBENCH", text)

    def test_testbench_request_accepts_runtime_feedback(self):
        text = (ROOT / "agrefactor/testing/testbench_repair.py").read_text()
        self.assertIn("runtime_feedback: FeedbackReport | None", text)
        self.assertIn("failure_state: ValidationState | None", text)

    def test_testbench_prompt_uses_runtime_feedback(self):
        text = (ROOT / "agrefactor/testing/model_testbench_repairer.py").read_text()
        self.assertIn("request.runtime_feedback", text)
        self.assertIn("agent_report = request.runtime_feedback", text)

    def test_timeout_classification_is_wired_to_both_stages(self):
        for relative in (
            "agrefactor/runtime/csim_stage.py",
            "agrefactor/runtime/cosim_stage.py",
        ):
            tree = ast.parse((ROOT / relative).read_text())
            imports = {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                for alias in node.names
            }
            self.assertIn("classify_public_timeout", imports)

    def test_r5_c_trailing_whitespace_is_removed(self):
        path = ROOT / "agrefactor/product/refactor_eligibility.py"
        lines = path.read_text().splitlines()
        self.assertFalse(any(line != line.rstrip() for line in lines))


if __name__ == "__main__":
    unittest.main()
