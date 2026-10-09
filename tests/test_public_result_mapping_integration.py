import json
import concurrent.futures
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

try:
    from tests.test_public_result_mapping import ORIGINAL, SHARED, testbench
except ImportError:
    from test_public_result_mapping import ORIGINAL, SHARED, testbench


CANDIDATE = """void source_task_hls(int input, int *out, int *status) {
    *out = input * 2;
    *status = input < 0 ? 7 : 0;
}
"""


class ResultMappingIntegrationTests(unittest.TestCase):
    def setUp(self):
        try:
            from flow import new
            from flow.tools import tb_optimizer
            from agrefactor.testing import TestbenchRepairLoop
            from agrefactor.evaluation import TestbenchPreflight
            from agrefactor.product import source_bootstrap
        except ImportError:
            self.skipTest("complete repository runtime is unavailable in local patch directory")
        self.flow = new
        self.optimizer = tb_optimizer
        self.loop_type = TestbenchRepairLoop
        self.preflight_type = TestbenchPreflight
        self.bootstrap = source_bootstrap

    @staticmethod
    def completed_identification(*_args, **_kwargs):
        future = concurrent.futures.Future()
        future.set_result(([], []))
        return future

    def test_public_qualification_freezes_mapping_before_candidate_and_hidden_uses_it(self):
        public = testbench([3, -2])
        hidden = testbench([5, -7])
        events = []
        agent = Mock()
        response = Mock(messages=[{"content": "Use the frozen Candidate ABI."}])
        agent.run.return_value = response
        loader = Mock()
        loader.load_agent.return_value = agent

        def generate_candidate(cv, *_args):
            events.append("candidate")
            self.assertIsNotNone(cv["public_result_mapping"])
            self.assertEqual(cv["public_result_mapping"]["shared_cpp"], SHARED)
            self.assertIn("read-only Testbench adapter", cv["tb_aligned_instruction"])
            self.assertNotIn("-7", cv["tb_aligned_instruction"])
            return CANDIDATE, ""

        def artifact(_agent, message, **kwargs):
            if kwargs["artifact_kind"] == "testbench":
                if "FROZEN PUBLIC COMMON RESULT OBSERVATION" in message:
                    events.append("hidden")
                    self.assertIn(SHARED, message)
                    return hidden
                events.append("public")
                return public
            return CANDIDATE

        runtime = {
            "schema_version": 2, "kind": "public_differential_self_check_v1",
            "candidate_mismatch_returncodes": [1],
            "cosim_interface_depths": {"out": 1, "status": 1},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.cpp"
            source.write_text(ORIGINAL, encoding="utf-8")
            with (
                patch.object(self.flow, "generate_input_domain_contract", return_value={"input": {"minimum": -8, "maximum": 8}}),
                patch.object(self.flow.tools.general, "create_log_and_redirect"),
                patch.object(self.flow.tools.identifying, "identify_non_synthesizable_items", side_effect=self.completed_identification),
                patch.object(self.flow.tools.planning, "generate_plan", return_value=("plan", "")),
                patch.object(self.flow.tools.refactoring, "refactor_code", side_effect=generate_candidate),
                patch.object(self.flow.tools.testbench, "HLSAgentLoader", return_value=loader),
                patch.object(self.optimizer, "HLSAgentLoader", return_value=loader),
                patch.object(self.optimizer, "_request_cpp_artifact", side_effect=artifact),
                patch.object(self.optimizer, "_synth_check", return_value=(True, "")),
                patch.object(self.optimizer, "generate_public_runtime_contract", return_value=runtime),
            ):
                succeeded, cv = self.flow.hls_refactor_with_rag(
                    kernel_path=str(source), kernel_name="source_task",
                    output_dir=str(root / "output"), generation_only=True,
                    test_generation_profile="lightweight", enable_hidden_tb_eval=True,
                    hidden_tb_trajectories=1, hidden_tb_rounds=1,
                    llm_config_override={},
                )
            self.assertTrue(succeeded)
            self.assertEqual(events, ["public", "candidate", "hidden"])
            self.assertEqual(cv["generated_hidden_testbench"], hidden)
            self.assertEqual(cv["generation_event_order"], ["public_generation", "candidate_generation", "hidden_generation"])
            self.assertFalse(cv["model_data_boundary"]["hidden_testbench_exposed_to_generation_model"])
            persisted = json.loads((root / "output" / "public_result_mapping.json").read_text())
            self.assertEqual(persisted, cv["public_result_mapping"])

    def test_public_testbench_repair_reuses_total_limit_and_rejects_mapping_change(self):
        public = testbench([3, -2])
        broken = public.replace("int mismatch = 0;", "int mismatch = missing_name;")
        changed = public.replace("original.status == candidate.status && ", "")
        repairer = Mock()
        repairer.repair.side_effect = [changed, public]
        repairer.audit_events = []
        with tempfile.TemporaryDirectory() as directory:
            result = self.loop_type(
                preflight=self.preflight_type(), repairer=repairer, max_repair_attempts=2
            ).run(
                work_dir=directory, testbench_code=broken,
                original_code=ORIGINAL, candidate_code=CANDIDATE,
                original_top_function="source_task", candidate_top_function="source_task_hls",
            )
        self.assertTrue(result.succeeded, result.reason)
        self.assertEqual(result.repair_attempts_used, 2)
        self.assertEqual(result.testbench_code, public.strip())
        self.assertIn("contract_contradiction", result.attempts[1].error)
        self.assertIn("contract_contradiction", repairer.repair.call_args_list[1].args[0].prior_attempt_summaries[-1])

    def test_hidden_cache_requires_the_same_public_result_mapping_identity(self):
        from flow.tools.result_mapping import freeze_result_mapping

        mapping = freeze_result_mapping(testbench([3]), ORIGINAL, "source_task", "source_task_hls")
        with tempfile.TemporaryDirectory() as directory:
            payload = {"orig_sha256": "source", "input_domain_contract_sha256": "domain",
                       "result_mapping_sha256": mapping["sha256"]}
            (Path(directory) / "cache.json").write_text(json.dumps(payload), encoding="utf-8")
            self.assertIsNotNone(self.optimizer._load_golden_cache(directory, "cache", "source", "domain", mapping["sha256"]))
            self.assertIsNone(self.optimizer._load_golden_cache(directory, "cache", "source", "domain", "other-mapping"))

    def test_public_repair_cannot_change_frozen_user_type_layout(self):
        from flow.tools.tb_optimizer import freeze_public_type_contract

        public = '''struct Input { int value; int offset; };
int source_task(Input *input); int source_task_hls(Input *input);
int main(){Input a{2,3},b{2,3};return source_task(&a)!=source_task_hls(&b);}'''
        original = 'struct Input { int value; int offset; }; int source_task(Input *p){return p->value+p->offset;}'
        candidate = 'struct Input { int value; int offset; }; int source_task_hls(Input *p){return p->value+p->offset;}'
        frozen = freeze_public_type_contract(public, 'source_task_hls')
        broken = public.replace('Input a{2,3}', 'Input a{missing_name,3}')
        changed = public.replace('int value; int offset;', 'int offset; int value;')
        repairer = Mock()
        repairer.repair.side_effect = [changed, public]
        repairer.audit_events = []
        with tempfile.TemporaryDirectory() as directory:
            result = self.loop_type(
                preflight=self.preflight_type(), repairer=repairer, max_repair_attempts=2
            ).run(
                work_dir=directory, testbench_code=broken, original_code=original,
                candidate_code=candidate, original_top_function='source_task',
                candidate_top_function='source_task_hls', frozen_type_contract=frozen,
            )
        self.assertTrue(result.succeeded, result.reason)
        self.assertEqual(result.repair_attempts_used, 2)
        self.assertIn('frozen Public Candidate type layout', result.attempts[1].error)


if __name__ == "__main__":
    unittest.main()
