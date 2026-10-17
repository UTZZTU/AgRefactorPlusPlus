from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agrefactor.config import EvaluationSplit, TestSuiteSpec
from flow.tools.tb_optimizer import ModelArtifactError, public_runtime_contract_depth_ports, validate_public_runtime_contract, generate_public_runtime_contract

class EvidenceBoundDepthRepairTests(unittest.TestCase):
    def test_generated_contract_refresh_restarts_and_preserves_candidate_and_hidden(self, followup_failure=False):
        from hashlib import sha256
        from agrefactor.config import TaskSpec, TestSourceSpec, TestSourceKind
        from agrefactor.evaluation import ValidationState
        from agrefactor.evidence import FeedbackCategory, FeedbackItem, FeedbackOwner, FeedbackReport, FeedbackSeverity, FeedbackStage
        from agrefactor.runtime import CandidateRepairOrchestrationRequest, CandidateRepairValidationOrchestrator
        from tests.test_candidate_repair_integration import ScenarioFactory, make_adapter, make_context, report_for
        code = "struct Packet {int data[5];}; void top_hls(const Packet& control, Packet& memory); int main(){return 0;}"
        candidate = "struct Packet {int data[5];}; void top_hls(const Packet& control, Packet& memory){}"
        old = {"schema_version": 2, "kind": "public_differential_self_check_v1", "candidate_mismatch_returncodes": [1],
               "cosim_interface_depths": {"control": 1, "memory": 1}}
        public = TestSuiteSpec(suite_id="public", split=EvaluationSplit.PUBLIC, testbench_path="public.cpp", runtime_contract=old,
                               source=TestSourceSpec(source_id="auto", source_kind=TestSourceKind.GENERATED, generation_model="test", generation_profile="test", operator_artifact_path="/tmp/artifact", prompt_sha256="a"*64, round_index=0, trajectory_id="t"))
        hidden = TestSuiteSpec(suite_id="hidden", split=EvaluationSplit.HIDDEN)
        task = TaskSpec(task_id="depth-restart", kernel_path="original.cpp", kernel_name="top_hls", test_suites=(public, hidden))
        proof = {"status": "verified", "candidate_sha256": sha256(candidate.encode()).hexdigest(),
                 "actual_maxi_ports": ["memory"], "source_to_hardware": {"memory": "gmem"}, "csynth_tool_launched": True}
        item = FeedbackItem(feedback_id="contract", stage=FeedbackStage.COSIM, category=FeedbackCategory.INVALID_CONFIGURATION,
                            severity=FeedbackSeverity.ERROR, owner=FeedbackOwner.TESTBENCH, summary="current contract ports differ",
                            source="public_rtl_cosim", metadata={"suite_id": "public", "failure_kind": "runtime_contract_interface_mismatch",
                            "owner_authority": "deterministic_proven", "evidence_complete": True, "repair_eligible": True,
                            "tool_launched": True, "physical_tool_launched": True, "interface_contract_evidence": proof})
        def scenario(request, state):
            if request.attempt == 0 and state is ValidationState.PUBLIC_COSIM:
                return FeedbackReport(report_id="depth.failed", source="public_rtl_cosim", items=(item,),
                                      metadata={"evidence_view": "agent_safe", "evaluation_split": "public", "feedback_visible_to_agent": True})
            if followup_failure and request.attempt == 1 and state is ValidationState.PUBLIC_EVALUATION:
                followup = FeedbackItem(feedback_id="harness", stage=FeedbackStage.CSIM,
                    category=FeedbackCategory.RUNTIME_CRASH, severity=FeedbackSeverity.ERROR,
                    owner=FeedbackOwner.TESTBENCH, summary="generated harness failure", source="test",
                    metadata={"suite_id": "public", "owner_authority": "deterministic_proven",
                              "evidence_complete": True, "repair_eligible": True, "tool_launched": True})
                return FeedbackReport(report_id="followup.failed", source="test", items=(followup,),
                                      metadata={"evidence_view": "agent_safe", "evaluation_split": "public", "feedback_visible_to_agent": True})
            return report_for(state, report_id=f"{request.validation_id}.{state.value}")
        factory = ScenarioFactory(scenario)
        adapter, provider = make_adapter([])
        from types import SimpleNamespace
        def repair_source(**kwargs):
            kwargs["before_repair"]()
            return SimpleNamespace(succeeded=True, testbench_code=code+"\n// generated harness repaired\n",
                                   attempts=(),
                                   to_dict=lambda: {"repair_attempts_used": 1, "attempts": []})
        with patch("agrefactor.runtime.candidate_repair_integration.build_openai_compatible_testbench_repairer"), patch("agrefactor.runtime.candidate_repair_integration.TestbenchRepairLoop") as loop:
            loop.return_value.run.side_effect = repair_source
            result = CandidateRepairValidationOrchestrator(model_adapter=adapter, handler_factory=factory).run(
                make_context(task), CandidateRepairOrchestrationRequest(
                    initial_candidate=candidate, original_code="void original(){}", preflight_testbench_code=code,
                    suite_testbench_codes={"public": code, "hidden": "int main(){return 0;}"},
                    prompt_public_testbench_code=code, candidate_top_function="top_hls", max_testbench_repairs=3,
                ), validation_id="depth",
            )
        self.assertTrue(result.accepted)
        self.assertEqual(provider.calls, [])
        self.assertEqual(factory.requests[1].candidate_code, candidate)
        self.assertEqual(factory.requests[1].task.test_suites[1], hidden)
        self.assertEqual(factory.requests[1].suite_testbench_codes["public"], code)
        self.assertEqual(factory.requests[1].task.test_suites[0].runtime_contract["cosim_interface_depths"], {"memory": 1})
        self.assertEqual(factory.context_ids[4][1], ValidationState.PREFLIGHT)
        self.assertEqual(result.metadata["runtime_testbench_repair_attempts_used"], 1 if followup_failure else 0)
        self.assertEqual(result.metadata["updated_public_runtime_contracts"]["public"]["cosim_interface_depths"], {"memory": 1})

    def test_record_references_are_candidates_and_scalar_reference_is_not(self):
        code = "struct Packet { int values[9]; }; typedef Packet Alias; void top_hls(const Packet& control, Alias& memory, int& count);"
        self.assertEqual(public_runtime_contract_depth_ports(
            testbench_code=code, candidate_top_function="top_hls",
            frozen_hls_decl="void top_hls(const Packet& control, Alias& memory, int& count);",
        ), ("control", "memory"))
        self.assertEqual(public_runtime_contract_depth_ports(
            testbench_code=code, candidate_top_function="top_hls",
            frozen_hls_decl="void top_hls(const Packet& control, Alias& memory, int& count);",
            confirmed_depth_ports=("memory",),
        ), ("memory",))

    def test_unparsed_interface_cannot_silently_become_scalar(self):
        with self.assertRaises(ModelArtifactError):
            public_runtime_contract_depth_ports(
                testbench_code="void other();", candidate_top_function="absent",
                frozen_hls_decl="void absent(Undeclared& memory);",
            )

    def test_confirmed_scalar_report_keeps_schema_one_compatible(self):
        code = "struct Packet { int value; }; void top_hls(const Packet& control);"
        contract = validate_public_runtime_contract(
            {"schema_version": 1, "kind": "public_differential_self_check_v1", "candidate_mismatch_returncodes": [1]},
            testbench_code=code, candidate_top_function="top_hls",
            frozen_hls_decl="void top_hls(const Packet& control);", confirmed_depth_ports=(),
        )
        self.assertEqual(contract["schema_version"], 1)

    def test_runtime_repair_response_attempts_share_the_supplied_limit(self):
        class Response:
            messages = [{"content": "{}"}]
            def process(self): return None
        class Agent:
            calls = 0
            def run(self, **kwargs):
                self.calls += 1
                return Response()
        agent = Agent()
        requests = []
        with patch("flow.tools.tb_optimizer.HLSAgentLoader") as loader:
            loader.return_value.load_agent.return_value = agent
            with self.assertRaises(ModelArtifactError):
                generate_public_runtime_contract(
                    orig_code="void top(int* data);",
                    testbench_code="void top_hls(int* data); int main(){return 0;}",
                    candidate_top_function="top_hls", frozen_hls_decl="void top_hls(int* data);",
                    max_response_attempts=2, before_request=lambda: requests.append(1),
                )
        self.assertEqual(agent.calls, 2)
        self.assertEqual(len(requests), 2)

    @staticmethod
    def _contract_failure(*, substage="runtime_contract_qualification", typed="passed"):
        return {
            "status": "failed", "failure_kind": "runtime_contract_interface_mismatch",
            "failure_owner": "testbench", "reason_code": "depth_ports_differ",
            "timed_out": False, "tool_launched": substage != "runtime_contract_qualification",
            "cosim_launched": substage != "runtime_contract_qualification",
            "returncode": 0 if substage != "runtime_contract_qualification" else None,
            "evidence_sha256": "a" * 64,
            "interface_contract_evidence": {
                "status": "verified", "interface_mapping_status": "verified",
                "failure_substage": substage, "report_sha256": "b" * 64,
                "receipt_sha256": "a" * 64, "csynth_tool_launched": True,
                "actual_maxi_ports": ["memory"], "declared_depth_ports": [],
                "typed_status": typed, "typed_identity_verified": True,
            },
        }

    def test_contract_failure_kind_does_not_imply_fixed_owner(self):
        from agrefactor.runtime.cosim_stage import _normalize_outcome
        configuration = self._contract_failure()
        configuration.update(reason_code="cosim_source_to_hardware_mapping_unresolved",
                             tool_launched=False, cosim_launched=False,
                             interface_contract_evidence=None)
        normalized = _normalize_outcome(configuration)
        self.assertEqual(normalized["failure_owner"], "configuration")
        self.assertFalse(normalized["repair_eligible"])
        normalized = _normalize_outcome(self._contract_failure())
        self.assertEqual(normalized["failure_owner"], "testbench")
        self.assertTrue(normalized["repair_eligible"])
        self.assertTrue(normalized["evidence_complete"])
        self.assertEqual(normalized["raw_failure_kind"], "runtime_contract_interface_mismatch")

    def test_post_cosim_contract_failure_requires_actual_typed_pass(self):
        from agrefactor.runtime.cosim_stage import _normalize_outcome
        passed = _normalize_outcome(self._contract_failure(substage="post_cosim_interface_validation"))
        self.assertEqual(passed["failure_owner"], "testbench")
        failed = _normalize_outcome(self._contract_failure(substage="post_cosim_interface_validation", typed="failed"))
        self.assertEqual(failed["failure_owner"], "unknown")
        self.assertFalse(failed["repair_eligible"])

    def test_current_csynth_interface_requires_identity_and_current_report(self):
        from hashlib import sha256
        from agrefactor.vitis_interface import current_csynth_interface
        from agrefactor.config import default_target_profile
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            code = "void top(int* memory) {}"
            source = root / "top.cpp"
            source.write_text(code)
            xml = root / "csynth" / "solution" / "top-io-fe.xml"
            xml.parent.mkdir(parents=True)
            xml.write_text('<root><kernel src_name="top"><args><arg src_name="memory" src_type="int *" src_isptr="1"><hw hw_interface="MAXI" hw_name="gmem"/></arg><arg src_name="control" src_type="int" src_isptr="0"><hw hw_interface="S_AXILite" hw_name="control"/></arg></args></kernel></root>')
            receipt = {"top_kernel": "top", "target_profile": default_target_profile().to_dict(),
                       "execution": {"status": "completed", "returncode": 0, "timeout": False},
                       "toolchain_version_verification": {"status": "matched"},
                       "expected_report": {"produced_by_current_invocation": True},
                       "source_provenance": {"top.cpp": {"path": str(source), "role": "candidate", "sha256": sha256(code.encode()).hexdigest()}},
                       "top_io": {"isolated_before_launch": True, "artifacts": [{"path": str(xml), "sha256": sha256(xml.read_bytes()).hexdigest(), "produced_by_current_invocation": True}]}}
            path = root / "csynth_invocation.json"
            path.write_text(json.dumps(receipt))
            kwargs = dict(candidate_code=code, expected_top="top", target_profile=default_target_profile().to_dict())
            verified = current_csynth_interface(root, **kwargs)
            self.assertEqual(verified["status"], "verified")
            self.assertEqual(verified["actual_maxi_ports"], ["memory"])
            self.assertEqual(verified["source_to_hardware"], {"memory": "gmem"})
            self.assertEqual(current_csynth_interface(root, **{**kwargs, "candidate_code": code + " "})["status"], "incomplete")
            original_xml = xml.read_text()
            for malformed in [original_xml.replace('src_isptr="1"', ''),
                              original_xml.replace('src_isptr="1"', 'src_isptr="invalid"'),
                              original_xml.replace('src_name="control"', 'src_name="memory"'),
                              '<invalid xml']:
                xml.write_text(malformed)
                receipt["top_io"]["artifacts"][0]["sha256"] = sha256(xml.read_bytes()).hexdigest()
                path.write_text(json.dumps(receipt))
                self.assertEqual(current_csynth_interface(root, **kwargs)["status"], "incomplete")
            xml.write_text(original_xml)
            receipt["top_io"]["artifacts"][0]["sha256"] = sha256(xml.read_bytes()).hexdigest()
            receipt["target_profile"] = {"different_profile": True}
            path.write_text(json.dumps(receipt))
            self.assertEqual(current_csynth_interface(root, **kwargs)["status"], "incomplete")
            receipt["target_profile"] = kwargs["target_profile"]
            receipt["top_io"]["isolated_before_launch"] = False
            path.write_text(json.dumps(receipt))
            self.assertEqual(current_csynth_interface(root, **kwargs)["status"], "incomplete")
            receipt["top_io"]["isolated_before_launch"] = True
            receipt["top_io"]["artifacts"].append(dict(receipt["top_io"]["artifacts"][0]))
            path.write_text(json.dumps(receipt))
            self.assertEqual(current_csynth_interface(root, **kwargs)["status"], "incomplete")

    def test_unproven_contract_failure_does_not_gain_candidate_authority_from_isolation(self):
        from agrefactor.runtime.cosim_stage import CosimStageInputs, CosimValidationStageHandler
        from tests.test_p4_0d_public_rtl_cosim import P4DPublicRtlCosimTests
        from tests.test_candidate_repair_integration import make_context
        task = P4DPublicRtlCosimTests()._task()
        raw = self._contract_failure(substage="post_cosim_interface_validation", typed="failed")
        with tempfile.TemporaryDirectory() as temp:
            handler = CosimValidationStageHandler(CosimStageInputs(
                work_dir=temp, original_code="void reference(){}", candidate_code="void kernel(){}",
                suite_testbench_codes={"public-1": "int main(){return 1;}"},
                candidate_top_function="kernel", target_profile=task.target,
                timelimit=20, reference_top_function="reference",
            ), executor=lambda **kwargs: raw)
            with patch("flow.tools.vitis_csim._original_isolation_evidence") as isolation:
                report = handler(make_context(task))
            isolation.assert_not_called()
        self.assertEqual(report.items[0].owner.value, "unknown")
        self.assertFalse(report.items[0].metadata["repair_eligible"])

    def test_catalog_classifies_runtime_contract_without_owner_or_fixed_route(self):
        from agrefactor.evaluation.diagnostic_catalog import load_catalog
        result = load_catalog().match("runtime_contract_interface_mismatch", stage="cosim")
        self.assertIsNotNone(result)
        self.assertEqual(result.category_id, "runtime_contract_interface_mismatch")
        self.assertEqual(result.owner_policy, "resolve_from_evidence")

    def test_generated_depth_refresh_preserves_nondefault_comparison_protocol(self, blocked_error=None):
        from hashlib import sha256
        from dataclasses import replace
        from contextlib import nullcontext
        from agrefactor.recovery import RecoveryLedger, RecoveryBudgetBlockedError
        from agrefactor.runtime import BudgetLimits
        from agrefactor.config import TaskSpec, TestSourceSpec, TestSourceKind
        from agrefactor.evaluation import ValidationState
        from agrefactor.evidence import FeedbackCategory, FeedbackItem, FeedbackOwner, FeedbackReport, FeedbackSeverity, FeedbackStage
        from agrefactor.runtime import CandidateRepairOrchestrationRequest, CandidateRepairValidationOrchestrator
        from tests.test_candidate_repair_integration import ScenarioFactory, make_adapter, make_context, report_for
        code = "void top_hls(int* memory); int main(){return 0;}"
        candidate = "void top_hls(int* memory){}"
        old = {"schema_version": 1, "kind": "public_differential_self_check_v1", "candidate_mismatch_returncodes": [2]}
        public = TestSuiteSpec(suite_id="public", split=EvaluationSplit.PUBLIC, testbench_path="public.cpp", runtime_contract=old,
                               source=TestSourceSpec(source_id="auto", source_kind=TestSourceKind.GENERATED,
                               generation_model="test", generation_profile="test", operator_artifact_path="/tmp/artifact",
                               prompt_sha256="a"*64, round_index=0, trajectory_id="t"))
        task = TaskSpec(task_id="depth-restart-protocol", kernel_path="original.cpp", kernel_name="top_hls", test_suites=(public,))
        proof = {"status": "verified", "candidate_sha256": sha256(candidate.encode()).hexdigest(),
                 "actual_maxi_ports": ["memory"], "source_to_hardware": {"memory": "gmem"}, "csynth_tool_launched": True}
        item = FeedbackItem(feedback_id="contract", stage=FeedbackStage.COSIM, category=FeedbackCategory.INVALID_CONFIGURATION,
                            severity=FeedbackSeverity.ERROR, owner=FeedbackOwner.TESTBENCH, summary="current contract ports differ",
                            source="public_rtl_cosim", metadata={"suite_id": "public", "failure_kind": "runtime_contract_interface_mismatch",
                            "owner_authority": "deterministic_proven", "evidence_complete": True, "repair_eligible": True,
                            "tool_launched": False, "physical_tool_launched": False, "interface_contract_evidence": proof})
        def scenario(request, state):
            if request.attempt == 0 and state is ValidationState.PUBLIC_COSIM:
                return FeedbackReport(report_id="depth.failed", source="public_rtl_cosim", items=(item,),
                                      metadata={"evidence_view": "agent_safe", "evaluation_split": "public", "feedback_visible_to_agent": True})
            return report_for(state, report_id=f"{request.validation_id}.{state.value}")
        def depth_response(**kwargs):
            kwargs["before_request"]()
            if blocked_error is not None:
                if blocked_error is RecoveryBudgetBlockedError:
                    kwargs["budget"].consume(tool_calls=1)
                kwargs["before_request"]()
                self.fail("second recovery reservation must be blocked")
            return {"schema_version": 2, "kind": "public_differential_self_check_v1", "candidate_mismatch_returncodes": [1],
                    "cosim_interface_depths": {"memory": 4}}
        factory = ScenarioFactory(scenario)
        adapter, provider = make_adapter([])
        ledger_patch = nullcontext()
        if blocked_error is not None and blocked_error is not RecoveryBudgetBlockedError:
            def one_request_ledger(policy):
                return RecoveryLedger(replace(policy, limits=replace(policy.limits, testbench_public_cosim_repairs=1)))
            ledger_patch = patch("agrefactor.runtime.candidate_repair_integration.RecoveryLedger", side_effect=one_request_ledger)
        limits = BudgetLimits(max_tool_calls=3) if blocked_error is RecoveryBudgetBlockedError else None
        with ledger_patch, patch("flow.tools.tb_optimizer.generate_public_runtime_contract", side_effect=depth_response), patch("agrefactor.compat.legacy_refactor.build_effective_legacy_llm_config", return_value={}):
            result = CandidateRepairValidationOrchestrator(model_adapter=adapter, handler_factory=factory).run(
                make_context(task, limits=limits), CandidateRepairOrchestrationRequest(
                    initial_candidate=candidate, original_code="void original(){}", preflight_testbench_code=code,
                    suite_testbench_codes={"public": code}, prompt_public_testbench_code=code,
                    candidate_top_function="top_hls", max_testbench_repairs=3,
                ), validation_id="depth-protocol",
            )
        if blocked_error is not None:
            self.assertFalse(result.accepted)
            self.assertEqual(provider.calls, [])
            self.assertEqual(result.metadata["runtime_testbench_repair_attempts_used"], 1)
            self.assertIn(result.metadata["runtime_testbench_recovery_status"],
                          {"recovery_restart_budget_unavailable", "recovery_limit_exhausted"})
            self.assertEqual([event["accepted"] for event in result.metadata["recovery_ledger"]["events"]], [True, False])
            self.assertEqual(result.metadata["runtime_contract_recovery_ledger"], result.metadata["recovery_ledger"])
            self.assertEqual(len(factory.requests), 1)
            return
        self.assertTrue(result.accepted)
        self.assertEqual(provider.calls, [])
        final = result.metadata["updated_public_runtime_contracts"]["public"]
        self.assertEqual(final["candidate_mismatch_returncodes"], [2])
        self.assertEqual(final["cosim_interface_depths"], {"memory": 4})
        self.assertEqual(result.metadata["runtime_testbench_repair_attempts_used"], 1)

    def test_zero_request_prune_does_not_discard_unused_public_repair_quota(self):
        self.test_generated_contract_refresh_restarts_and_preserves_candidate_and_hidden(followup_failure=True)

    def test_verified_pre_synthesis_report_cannot_replace_actual_post_cosim_report(self):
        from hashlib import sha256
        from agrefactor.config import default_target_profile
        from flow.tools.vitis_cosim import run_vitis_cosim
        from tests.test_p4_0d_public_rtl_cosim import P4DPublicRtlCosimTests
        profile = default_target_profile()
        candidate = "void kernel(int* memory){}"
        proof = {"status": "verified", "candidate_sha256": sha256(candidate.encode()).hexdigest(),
                 "top_name": "kernel", "target_profile": profile.to_dict(),
                 "actual_maxi_ports": ["memory"], "source_to_hardware": {"memory": "gmem"}}
        contract = {"schema_version": 2, "kind": "public_differential_self_check_v1",
                    "candidate_mismatch_returncodes": [1], "cosim_interface_depths": {"memory": 1}}
        for produce_report in (False, True):
            with self.subTest(produce_report=produce_report), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                calls = []
                def tool(work_dir, command, timelimit):
                    calls.append(1)
                    if len(calls) == 1:
                        (root / "toolchain_version.txt").write_text("2023.2\n")
                    else:
                        invocation = json.loads((root / "cosim_invocation.json").read_text())
                        (root / "cosim_command_status.json").write_text(json.dumps({"schema_version": 1, "status": "passed", "phase": "cosim", "reason_code": "cosim_passed"}))
                        (root / "agrefactor_cosim_outcome.json").write_text(json.dumps({"schema_version": 2,
                            **invocation["typed_outcome_identities"]["cosim"], "status": "passed", "testbench_returncode": 0}))
                        if produce_report:
                            report = root / "agrefactor_public_cosim" / "solution" / "top-io-fe.xml"
                            report.parent.mkdir(parents=True)
                            report.write_text('<root><kernel src_name="kernel"><args><arg src_name="memory" src_type="int*" src_isptr="1"><hw hw_interface="MAXI" hw_name="gmem"/></arg></args></kernel></root>')
                    return {"returncode": 0, "timeout": False}
                with patch("flow.tools.vitis_cosim.resolve_csynth_command", return_value=P4DPublicRtlCosimTests._resolution()), patch("flow.tools.vitis_cosim.tools.general.run_cmd", side_effect=tool):
                    outcome = run_vitis_cosim(work_dir=root, original_code="int reference;", candidate_code=candidate,
                        testbench_code="int main(){return 0;}", candidate_top_function="kernel", target_profile=profile,
                        timelimit=20, runtime_contract=contract, synthesized_interface_evidence=proof)
                if produce_report:
                    self.assertEqual(outcome["status"], "passed")
                else:
                    self.assertEqual(outcome["status"], "failed")
                    self.assertEqual(outcome["failure_owner"], "unknown")
                    self.assertEqual(outcome["reason_code"], "cosim_current_interface_report_incomplete")

    def test_prelaunch_calibration_uses_prior_synthesis_without_claiming_cosim_launch(self):
        from agrefactor.runtime.cosim_stage import CosimStageInputs, CosimValidationStageHandler
        from tests.test_p4_0d_public_rtl_cosim import P4DPublicRtlCosimTests
        from tests.test_candidate_repair_integration import make_context
        task = P4DPublicRtlCosimTests()._task()
        proof = {"status": "verified", "receipt_sha256": "a"*64, "report_sha256": "b"*64,
                 "candidate_sha256": "c"*64, "top_name": "kernel", "csynth_tool_launched": True,
                 "actual_maxi_ports": ["memory"], "source_to_hardware": {"memory": "gmem"}}
        with tempfile.TemporaryDirectory() as temp:
            handler = CosimValidationStageHandler(CosimStageInputs(
                work_dir=Path(temp)/"public_cosim", original_code="void reference(){}", candidate_code="void kernel(int* memory){}",
                suite_testbench_codes={"public-1": "int main(){return 0;}"}, candidate_top_function="kernel",
                target_profile=task.target, timelimit=20, csynth_work_dir=Path(temp)/"csynth",
            ), executor=lambda **kwargs: self.fail("COSIM must wait until contract requalification"))
            with patch("agrefactor.vitis_interface.current_csynth_interface", return_value=proof):
                report = handler(make_context(task))
        item = report.items[0]
        self.assertEqual(item.owner.value, "testbench")
        self.assertFalse(item.metadata["tool_launched"])
        self.assertFalse(item.metadata["cosim_launched"])
        self.assertFalse(report.metadata["physical_execution"])
        self.assertTrue(item.metadata["interface_contract_evidence"]["csynth_tool_launched"])
        self.assertTrue(item.metadata["repair_eligible"])

    def test_contract_recovery_block_after_request_preserves_actual_accounting(self):
        from agrefactor.recovery import RecoveryDeniedError, RecoveryBudgetBlockedError
        for error in (RecoveryDeniedError, RecoveryBudgetBlockedError):
            with self.subTest(error=error.__name__):
                self.test_generated_depth_refresh_preserves_nondefault_comparison_protocol(blocked_error=error)
