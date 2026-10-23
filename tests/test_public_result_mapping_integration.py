import json
import concurrent.futures
from contextlib import ExitStack
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

    def test_hidden_result_gates_root_success_without_repairing(self):
        final_success = self.flow._final_run_succeeded
        self.assertTrue(
            final_success(
                True, hidden_enabled=False, hidden_passed=False
            )
        )
        self.assertFalse(
            final_success(True, hidden_enabled=True, hidden_passed=False)
        )
        self.assertTrue(
            final_success(True, hidden_enabled=True, hidden_passed=True)
        )
        self.assertFalse(
            final_success(False, hidden_enabled=True, hidden_passed=True)
        )
        self.assertFalse(
            final_success(True, hidden_enabled=True, hidden_passed=None)
        )

    def test_hidden_evaluation_controls_return_after_formal_validation(self):
        runtime = {
            "schema_version": 2,
            "kind": "public_differential_self_check_v1",
            "candidate_mismatch_returncodes": [1],
            "cosim_interface_depths": {"out": 1, "status": 1},
        }

        for formal_passed, hidden_enabled, hidden_passed, expected_success, hidden_artifact in (
            (True, True, True, True, "generated"),
            (True, True, False, False, "generated"),
            (False, True, False, False, "generated"),
            (True, True, None, None, "missing"),
            (True, False, None, True, "disabled"),
            (False, False, None, False, "disabled"),
        ):
            with self.subTest(
                formal_passed=formal_passed,
                hidden_enabled=hidden_enabled,
                hidden_passed=hidden_passed,
                hidden_artifact=hidden_artifact,
            ), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "source.cpp"
                source.write_text(ORIGINAL, encoding="utf-8")
                agent = Mock()
                agent.run.return_value = Mock(
                    messages=[{"content": "Use the frozen Candidate ABI."}]
                )
                loader = Mock()
                loader.load_agent.return_value = agent
                hidden_eval = Mock(
                    return_value={
                        "passed": hidden_passed,
                        "failure_kind": (
                            "none" if hidden_passed else "mismatch"
                        ),
                        "cov_hidden": 50.0,
                        "lines_total": 2,
                        "lines_hit": 1,
                    }
                )
                formal_result = "succeeded" if formal_passed else "failed"
                formal = (
                    False,
                    "public-csim",
                    (formal_result, ""),
                    "candidate-csim",
                    (formal_result, "synthetic failure"),
                )

                with (
                    patch.object(self.flow, "generate_input_domain_contract", return_value={"input": {"minimum": -8, "maximum": 8}}),
                    patch.object(self.flow.tools.general, "create_log_and_redirect"),
                    patch.object(self.flow.tools.general, "csynth_and_csim", return_value=formal),
                    patch.object(self.flow.tools.general, "try_fixing") as try_fixing,
                    patch.object(self.flow.tools.identifying, "identify_non_synthesizable_items", side_effect=self.completed_identification),
                    patch.object(self.flow.tools.planning, "generate_plan", return_value=("plan", "")),
                    patch.object(self.flow.tools.refactoring, "refactor_code", return_value=(CANDIDATE, "")),
                    patch.object(self.flow.tools.testbench, "HLSAgentLoader", return_value=loader),
                    patch.object(self.optimizer, "HLSAgentLoader", return_value=loader),
                    patch.object(self.optimizer, "_request_cpp_artifact", side_effect=lambda _agent, _message, **kwargs: testbench([3, -2]) if kwargs["artifact_kind"] == "testbench" else CANDIDATE),
                    patch.object(self.optimizer, "_synth_check", return_value=(True, "")),
                    patch.object(self.optimizer, "generate_public_runtime_contract", return_value=runtime),
                    patch.object(
                        self.optimizer,
                        "make_golden_hidden_tb",
                        return_value={
                            "hidden_tb": (
                                "" if hidden_artifact == "missing"
                                else testbench([5])
                            ),
                            "hidden_cov": 100.0,
                        },
                    ) as generate_hidden,
                    patch.object(self.flow.tools.tb_hidden_eval, "eval_against_hidden_tb", hidden_eval),
                ):
                    if hidden_artifact == "missing":
                        with self.assertRaisesRegex(
                            RuntimeError,
                            "enabled without generated evaluator",
                        ):
                            self.flow.hls_refactor_with_rag(
                                kernel_path=str(source),
                                kernel_name="source_task",
                                output_dir=str(root / "output"),
                                max_retry_attempts=0,
                                test_generation_profile="lightweight",
                                enable_hidden_tb_eval=hidden_enabled,
                                hidden_tb_trajectories=1,
                                hidden_tb_rounds=1,
                                llm_config_override={},
                            )
                        succeeded = None
                        cv = None
                    else:
                        succeeded, cv = self.flow.hls_refactor_with_rag(
                            kernel_path=str(source),
                            kernel_name="source_task",
                            output_dir=str(root / "output"),
                            max_retry_attempts=0,
                            test_generation_profile="lightweight",
                            enable_hidden_tb_eval=hidden_enabled,
                            hidden_tb_trajectories=1,
                            hidden_tb_rounds=1,
                            llm_config_override={},
                        )

                if hidden_artifact == "generated":
                    self.assertEqual(succeeded, expected_success)
                if hidden_artifact == "disabled":
                    self.assertEqual(succeeded, expected_success)
                self.assertEqual(
                    hidden_eval.call_count,
                    int(
                        formal_passed
                        and hidden_enabled
                        and hidden_artifact == "generated"
                    ),
                )
                self.assertEqual(
                    generate_hidden.call_count,
                    int(hidden_enabled),
                )
                try_fixing.assert_not_called()
                if formal_passed and hidden_enabled and hidden_artifact == "generated":
                    self.assertEqual(cv["pass_hidden"], hidden_passed)

    def test_generation_case_stays_open_through_runtime_contract_handoff(self):
        from agrefactor.evidence import CaseRecorder, CaseStore, CaseStatus

        public = testbench([3, -2])
        runtime = {
            "schema_version": 2,
            "kind": "public_differential_self_check_v1",
            "candidate_mismatch_returncodes": [1],
            "cosim_interface_depths": {"out": 1, "status": 1},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.cpp"
            source.write_text(ORIGINAL, encoding="utf-8")
            experience_root = root / "experiences"
            recorder = CaseRecorder(
                root / "cases",
                "generation-run",
                case_scope_id="generation-run",
                experience_root=experience_root,
            )
            recorder.record_public_generation_diagnostic(
                {
                    "event_id": "public-diagnostic",
                    "run_id": "generation-run",
                    "validation_id": "generation-run",
                    "stage": "public_testbench_generation",
                    "split": "public",
                    "owner": "testbench",
                    "route_action": "review_unknown",
                    "evidence_refs": ["evidence:public-diagnostic"],
                    "evidence_view": "agent_safe",
                    "hidden_content_persisted": False,
                }
            )
            case_id = CaseStore(root / "cases").open_cases()[0].case_id

            def fake_public_generation(cv, *args, **kwargs):
                self.assertIs(args[6], recorder)
                cv["public_testbench_coverage"] = {
                    "schema_version": 1,
                    "trajectory_count": 1,
                    "trajectories": [],
                }
                return public, "instruction", "source_task_hls"

            def check_case_open(**_kwargs):
                case = CaseStore(root / "cases").read_case(case_id)
                self.assertEqual(case.status, CaseStatus.OPEN)
                self.assertEqual(
                    list((experience_root / "records").glob("*.json")), []
                )
                return runtime

            agent = Mock()
            response = Mock(messages=[{"content": "Use the frozen Candidate ABI."}])
            agent.run.return_value = response
            loader = Mock()
            loader.load_agent.return_value = agent
            with (
                patch.object(self.flow, "generate_input_domain_contract", return_value={"input": {"minimum": -8, "maximum": 8}}),
                patch.object(self.flow.tools.general, "create_log_and_redirect"),
                patch.object(
                    self.flow.tools.general,
                    "csynth_and_csim",
                    return_value=(
                        False,
                        "public-csim",
                        ("succeeded", ""),
                        "candidate-csim",
                        ("succeeded", ""),
                    ),
                ),
                patch.object(self.flow.tools.identifying, "identify_non_synthesizable_items", side_effect=self.completed_identification),
                patch.object(self.flow.tools.planning, "generate_plan", return_value=("plan", "")),
                patch.object(self.flow.tools.refactoring, "refactor_code", return_value=(CANDIDATE, "")),
                patch.object(self.flow.tools.testbench, "HLSAgentLoader", return_value=loader),
                patch.object(self.optimizer, "HLSAgentLoader", return_value=loader),
                patch.object(self.optimizer, "gen_tb_with_coverage", side_effect=fake_public_generation),
                patch.object(
                    self.optimizer,
                    "make_golden_hidden_tb",
                    return_value={
                        "hidden_tb": testbench([5]),
                        "hidden_cov": 100.0,
                    },
                ),
                patch.object(self.flow, "_build_public_generation_case_recorder", return_value=(recorder, None)),
                patch.object(self.optimizer, "generate_public_runtime_contract", side_effect=check_case_open),
                patch.object(
                    self.flow.tools.tb_hidden_eval,
                    "eval_against_hidden_tb",
                    return_value={
                        "passed": False,
                        "failure_kind": "mismatch",
                        "cov_hidden": 50.0,
                        "lines_total": 2,
                        "lines_hit": 1,
                    },
                ),
            ):
                succeeded, _cv = self.flow.hls_refactor_with_rag(
                    kernel_path=str(source),
                    kernel_name="source_task",
                    output_dir=str(root / "output"),
                    generation_only=False,
                    test_generation_profile="coverage-enhanced",
                    enable_tb_coverage_loop=True,
                    enable_hidden_tb_eval=True,
                    public_tb_rounds=1,
                    public_tb_trajectories=1,
                    record_diagnostic_cases=True,
                    llm_config_override={},
                )

            self.assertFalse(succeeded)
            case = CaseStore(root / "cases").read_case(case_id)
            self.assertEqual(case.status, CaseStatus.CLOSED)
            self.assertEqual(case.outcome.value, "resolved")
            self.assertLessEqual(
                len(list((experience_root / "records").glob("*.json"))),
                1,
            )

    def test_runtime_contract_failure_leaves_generation_case_unresolved(self):
        from agrefactor.evidence import CaseRecorder, CaseStore, CaseStatus

        public = testbench([3, -2])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.cpp"
            source.write_text(ORIGINAL, encoding="utf-8")
            experience_root = root / "experiences"
            recorder = CaseRecorder(
                root / "cases",
                "generation-failure-run",
                case_scope_id="generation-failure-run",
                experience_root=experience_root,
            )
            recorder.record_public_generation_diagnostic(
                {
                    "event_id": "public-diagnostic-failure",
                    "run_id": "generation-failure-run",
                    "validation_id": "generation-failure-run",
                    "stage": "public_testbench_generation",
                    "split": "public",
                    "owner": "testbench",
                    "route_action": "review_unknown",
                    "evidence_refs": ["evidence:public-diagnostic-failure"],
                    "evidence_view": "agent_safe",
                    "hidden_content_persisted": False,
                }
            )
            case_id = CaseStore(root / "cases").open_cases()[0].case_id

            def fake_public_generation(cv, *args, **kwargs):
                cv["public_testbench_coverage"] = {
                    "schema_version": 1,
                    "trajectory_count": 1,
                    "trajectories": [],
                }
                return public, "instruction", "source_task_hls"

            with (
                patch.object(self.flow, "generate_input_domain_contract", return_value={"input": {"minimum": -8, "maximum": 8}}),
                patch.object(self.flow.tools.general, "create_log_and_redirect"),
                patch.object(self.flow.tools.identifying, "identify_non_synthesizable_items", side_effect=self.completed_identification),
                patch.object(self.flow.tools.planning, "generate_plan", return_value=("plan", "")),
                patch.object(self.flow.tools.refactoring, "refactor_code", return_value=(CANDIDATE, "")),
                patch.object(self.optimizer, "gen_tb_with_coverage", side_effect=fake_public_generation),
                patch.object(self.flow, "_build_public_generation_case_recorder", return_value=(recorder, None)),
                patch.object(self.optimizer, "generate_public_runtime_contract", side_effect=RuntimeError("runtime contract unavailable")),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "runtime contract unavailable"
                ):
                    self.flow.hls_refactor_with_rag(
                        kernel_path=str(source),
                        kernel_name="source_task",
                        output_dir=str(root / "output"),
                        generation_only=True,
                        test_generation_profile="coverage-enhanced",
                        enable_tb_coverage_loop=True,
                        public_tb_rounds=1,
                        public_tb_trajectories=1,
                        record_diagnostic_cases=True,
                        llm_config_override={},
                    )

            case = CaseStore(root / "cases").read_case(case_id)
            self.assertEqual(case.status, CaseStatus.CLOSED)
            self.assertEqual(case.outcome.value, "unresolved")
            self.assertEqual(
                list((experience_root / "records").glob("*.json")), []
            )

    def test_abi_mapping_and_type_handoff_failures_leave_case_unresolved(self):
        from agrefactor.evidence import CaseRecorder, CaseStore, CaseStatus
        from flow.tools.result_mapping import ResultMappingVerificationError
        from flow.tools.tb_optimizer import ModelArtifactError

        public = testbench([3, -2])
        for stage in ("abi", "mapping", "type"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "source.cpp"
                source.write_text(ORIGINAL, encoding="utf-8")
                experience_root = root / "experiences"
                recorder = CaseRecorder(
                    root / "cases",
                    f"generation-{stage}-failure",
                    case_scope_id=f"generation-{stage}-failure",
                    experience_root=experience_root,
                )
                recorder.record_public_generation_diagnostic(
                    {
                        "event_id": f"public-diagnostic-{stage}",
                        "run_id": f"generation-{stage}-failure",
                        "validation_id": f"generation-{stage}-failure",
                        "stage": "public_testbench_generation",
                        "split": "public",
                        "owner": "testbench",
                        "route_action": "review_unknown",
                        "evidence_refs": [f"evidence:{stage}"],
                        "evidence_view": "agent_safe",
                        "hidden_content_persisted": False,
                    }
                )
                case_id = CaseStore(root / "cases").open_cases()[0].case_id

                def fake_public_generation(cv, *args, **kwargs):
                    cv["public_testbench_coverage"] = {
                        "schema_version": 1,
                        "trajectory_count": 1,
                        "trajectories": [],
                    }
                    return public, "instruction", "source_task_hls"

                with ExitStack() as stack:
                    stack.enter_context(
                        patch.object(
                            self.flow,
                            "generate_input_domain_contract",
                            return_value={"input": {"minimum": -8, "maximum": 8}},
                        )
                    )
                    stack.enter_context(
                        patch.object(self.flow.tools.general, "create_log_and_redirect")
                    )
                    stack.enter_context(
                        patch.object(
                            self.flow.tools.identifying,
                            "identify_non_synthesizable_items",
                            side_effect=self.completed_identification,
                        )
                    )
                    stack.enter_context(
                        patch.object(
                            self.flow.tools.planning,
                            "generate_plan",
                            return_value=("plan", ""),
                        )
                    )
                    stack.enter_context(
                        patch.object(
                            self.flow.tools.refactoring,
                            "refactor_code",
                            return_value=(CANDIDATE, ""),
                        )
                    )
                    stack.enter_context(
                        patch.object(
                            self.optimizer,
                            "gen_tb_with_coverage",
                            side_effect=fake_public_generation,
                        )
                    )
                    stack.enter_context(
                        patch.object(
                            self.flow,
                            "_build_public_generation_case_recorder",
                            return_value=(recorder, None),
                        )
                    )
                    if stage == "abi":
                        stack.enter_context(
                            patch.object(
                                self.optimizer,
                                "extract_hls_decl_from_testbench",
                                side_effect=RuntimeError("ABI extraction failed"),
                            )
                        )
                    elif stage == "mapping":
                        stack.enter_context(
                            patch.object(
                                self.flow,
                                "freeze_result_mapping",
                                side_effect=ResultMappingVerificationError(
                                    "mapping incomplete", {}, {"diagnostics": []}
                                ),
                            )
                        )
                    else:
                        stack.enter_context(
                            patch.object(
                                self.optimizer,
                                "freeze_public_type_contract",
                                side_effect=ModelArtifactError(
                                    "Public type contract failed"
                                ),
                            )
                        )

                    if stage == "abi":
                        with self.assertRaisesRegex(RuntimeError, "ABI extraction failed"):
                            self.flow.hls_refactor_with_rag(
                                kernel_path=str(source),
                                kernel_name="source_task",
                                output_dir=str(root / "output"),
                                generation_only=True,
                                test_generation_profile="coverage-enhanced",
                                enable_tb_coverage_loop=True,
                                public_tb_rounds=1,
                                public_tb_trajectories=1,
                                record_diagnostic_cases=True,
                                llm_config_override={},
                            )
                    else:
                        succeeded, _cv = self.flow.hls_refactor_with_rag(
                            kernel_path=str(source),
                            kernel_name="source_task",
                            output_dir=str(root / "output"),
                            generation_only=True,
                            test_generation_profile="coverage-enhanced",
                            enable_tb_coverage_loop=True,
                            public_tb_rounds=1,
                            public_tb_trajectories=1,
                            record_diagnostic_cases=True,
                            llm_config_override={},
                        )
                        self.assertFalse(succeeded)

                case = CaseStore(root / "cases").read_case(case_id)
                self.assertEqual(case.status, CaseStatus.CLOSED)
                self.assertEqual(case.outcome.value, "unresolved")
                self.assertEqual(
                    list((experience_root / "records").glob("*.json")), []
                )

    def test_public_generation_exhaustion_closes_case_unresolved(self):
        from agrefactor.evidence import CaseRecorder, CaseStore, CaseStatus

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.cpp"
            source.write_text(ORIGINAL, encoding="utf-8")
            experience_root = root / "experiences"
            recorder = CaseRecorder(
                root / "cases",
                "generation-exhaustion-run",
                case_scope_id="generation-exhaustion-run",
                experience_root=experience_root,
            )
            recorder.record_public_generation_diagnostic(
                {
                    "event_id": "public-exhaustion-diagnostic",
                    "run_id": "generation-exhaustion-run",
                    "validation_id": "generation-exhaustion-run",
                    "stage": "public_testbench_generation",
                    "split": "public",
                    "owner": "testbench",
                    "route_action": "review_unknown",
                    "evidence_refs": ["evidence:public-exhaustion"],
                    "evidence_view": "agent_safe",
                    "hidden_content_persisted": False,
                }
            )
            case_id = CaseStore(root / "cases").open_cases()[0].case_id
            exhausted = self.optimizer.TestbenchGenerationExhausted(
                split="public",
                stage="public_generation_qualification",
                trajectories=[
                    {
                        "trajectory_idx": 0,
                        "qualified": False,
                        "trajectory_status": "coverage_failed",
                        "rounds": [],
                    }
                ],
            )

            with (
                patch.object(
                    self.flow,
                    "generate_input_domain_contract",
                    return_value={"input": {"minimum": -8, "maximum": 8}},
                ),
                patch.object(self.flow.tools.general, "create_log_and_redirect"),
                patch.object(
                    self.flow.tools.identifying,
                    "identify_non_synthesizable_items",
                    side_effect=self.completed_identification,
                ),
                patch.object(
                    self.optimizer,
                    "gen_tb_with_coverage",
                    side_effect=exhausted,
                ),
                patch.object(
                    self.flow,
                    "_build_public_generation_case_recorder",
                    return_value=(recorder, None),
                ),
            ):
                succeeded, _cv = self.flow.hls_refactor_with_rag(
                    kernel_path=str(source),
                    kernel_name="source_task",
                    output_dir=str(root / "output"),
                    generation_only=True,
                    test_generation_profile="coverage-enhanced",
                    enable_tb_coverage_loop=True,
                    public_tb_rounds=1,
                    public_tb_trajectories=1,
                    record_diagnostic_cases=True,
                    llm_config_override={},
                )

            case = CaseStore(root / "cases").read_case(case_id)
            self.assertFalse(succeeded)
            self.assertEqual(case.status, CaseStatus.CLOSED)
            self.assertEqual(case.outcome.value, "unresolved")
            self.assertEqual(
                list((experience_root / "records").glob("*.json")), []
            )

    def test_external_public_testbench_skips_runtime_contract_generation(self):
        external = testbench([3, -2])
        runtime_contract = Mock(
            side_effect=AssertionError(
                "external Public TB does not generate a runtime contract"
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.cpp"
            source.write_text(ORIGINAL, encoding="utf-8")
            with (
                patch.object(
                    self.flow,
                    "generate_input_domain_contract",
                    return_value={"input": {"minimum": -8, "maximum": 8}},
                ),
                patch.object(self.flow.tools.general, "create_log_and_redirect"),
                patch.object(
                    self.optimizer,
                    "extract_hls_decl_from_testbench",
                    return_value="extern \"C\" int source_task_hls(int);",
                ),
                patch.object(
                    self.flow.tools.testbench,
                    "qualify_external_public_testbench",
                ),
                patch.object(self.flow, "freeze_result_mapping", return_value=None),
                patch.object(
                    self.optimizer,
                    "freeze_public_type_contract",
                    return_value={"schema_version": 1, "types": []},
                ),
                patch.object(
                    self.flow.tools.identifying,
                    "identify_non_synthesizable_items",
                    side_effect=self.completed_identification,
                ),
                patch.object(
                    self.flow.tools.planning,
                    "generate_plan",
                    return_value=("plan", ""),
                ),
                patch.object(
                    self.flow.tools.refactoring,
                    "refactor_code",
                    return_value=(CANDIDATE, ""),
                ),
                patch.object(
                    self.optimizer,
                    "generate_public_runtime_contract",
                    runtime_contract,
                ),
            ):
                succeeded, cv = self.flow.hls_refactor_with_rag(
                    kernel_path=str(source),
                    kernel_name="source_task",
                    output_dir=str(root / "output"),
                    external_testbench=external,
                    generation_only=True,
                    llm_config_override={},
                )

        self.assertTrue(succeeded)
        self.assertIsNone(cv["public_runtime_contract"])
        runtime_contract.assert_not_called()

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

    def test_public_repair_summary_retains_bounded_manifest_difference(self):
        public = testbench([3, -2])
        broken = public.replace('int mismatch = 0;', 'int mismatch = missing_name;')
        altered = public.replace('Original call return status', 'different observation ' * 1000)
        repairer = Mock()
        repairer.repair.side_effect = [altered, public]
        repairer.audit_events = []
        with tempfile.TemporaryDirectory() as directory:
            result = self.loop_type(
                preflight=self.preflight_type(), repairer=repairer, max_repair_attempts=2
            ).run(
                work_dir=directory, testbench_code=broken,
                original_code=ORIGINAL, candidate_code=CANDIDATE,
                original_top_function='source_task', candidate_top_function='source_task_hls',
            )
        self.assertTrue(result.succeeded, result.reason)
        self.assertEqual(result.repair_attempts_used, 2)
        summary = repairer.repair.call_args_list[1].args[0].prior_attempt_summaries[-1]
        self.assertIn('/observables/0/original', summary)
        self.assertIn('Original call return status', summary)
        self.assertIn('truncated', summary)
        self.assertIn('encoded_length', summary)
        self.assertIn('expected', summary)

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
