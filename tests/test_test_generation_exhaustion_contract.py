from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agrefactor.config import RunMode, TaskSpec, resolve_target_profile
from agrefactor.evidence import RootRunRecordStore
from agrefactor.models import resolve_model_runtime
from agrefactor.product import (
    SourceBootstrapPhase,
    SourceBootstrapRequest,
    SourceRunLayout,
    build_test_source_plan,
)
from agrefactor.runtime import (
    BudgetManager,
    PhaseResult,
    PhaseStatus,
    RunContext,
    RunPhase,
    RunStatus,
    TraceRecorder,
    UnifiedRunner,
)
from agrefactor.runtime.budget_profile import (
    DEFAULT_SOURCE_RUN_BUDGET_PROFILE,
)
from flow import new as flow_new
from flow.tools import tb_optimizer


def failed_trajectory(
    *,
    owner: str = "stub",
    action: str = "regenerate_stub",
    secret: str = "",
):
    return {
        "trajectory_idx": 0,
        "best_round": 2,
        "best_cov": 0.0,
        "best_tb": f"// hidden-only {secret}",
        "best_stub": f"// generated-stub {secret}",
        "rounds": [
            {
                "round": 1,
                "status": "compile_failed",
                "failure_owner": owner,
                "next_action": action,
                "compile_stderr": (
                    "refactor_code.cpp: error: generated Stub does not compile"
                ),
                "tb_code": f"// private-testbench {secret}",
                "stub_code": f"// private-stub {secret}",
            },
            {
                "round": 2,
                "status": "compile_failed",
                "failure_owner": owner,
                "next_action": action,
                "compile_stderr": (
                    "refactor_code.cpp: error: replacement Stub still fails"
                ),
                "tb_code": f"// private-testbench-2 {secret}",
                "stub_code": f"// private-stub-2 {secret}",
            },
        ],
        "synth_ok": False,
        "qualified": False,
        "trajectory_status": "coverage_failed",
        "synth_error": "replacement Stub still fails",
    }


class FailedGenerationAdapter:
    def __init__(self, payload):
        self.payload = payload
        self.last_raw_result = None

    def __call__(self, context):
        context.budget.consume(llm_calls=1)
        self.last_raw_result = (
            False,
            {"generation_failure": self.payload},
        )
        return PhaseResult(
            phase=RunPhase.REFACTOR,
            status=PhaseStatus.FAILED,
            summary="bounded generation exhaustion",
            metadata={"generation_only": True},
        )


class FailedCandidateGenerationAdapter:
    def __call__(self, context):
        for index in range(3):
            context.trace.record(
                "candidate_generation.abstained",
                phase="refactor",
                status="not_generated",
                metadata={"candidate_created": False, "index": index},
            )
        error = RuntimeError("candidate output was unavailable")
        error.candidate_generation_failure = {
            "error_class": "RuntimeError",
            "error_fingerprint": "a" * 24,
        }
        raise error


class TestGenerationExhaustionContractTests(unittest.TestCase):
    def test_candidate_generation_exception_is_a_pre_formal_event(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "kernel.cpp"
            source.write_text(
                'extern "C" int top(int x) { return x; }\n',
                encoding="utf-8",
            )
            runtime = resolve_model_runtime("deepseek-v4-flash")
            request = SourceBootstrapRequest(
                source_path=source,
                top_function="top",
                mode=RunMode.REFACTOR,
                effective_model_config=runtime.effective_config,
                target=resolve_target_profile(None),
                test_source_plan=build_test_source_plan(),
                budget_contract=DEFAULT_SOURCE_RUN_BUDGET_PROFILE.resolve(),
                max_candidate_repairs=2,
                run_id="candidate-generation-failure-test",
            )
            layout = SourceRunLayout.create(
                request.run_id,
                artifact_base=root / "artifacts",
                work_base=root / "work",
            )
            layout.artifact_root.mkdir(parents=True)
            trace = TraceRecorder(request.run_id, task_id="source-task")
            root_record = RootRunRecordStore(
                layout.artifact_root / "root_run", request.run_id
            )
            phase = SourceBootstrapPhase(
                request=request,
                layout=layout,
                generation_adapter=FailedCandidateGenerationAdapter(),
                formal_phase_builder=lambda *_args: self.fail(
                    "formal validation must not start"
                ),
            )
            context = RunContext(
                run_id=request.run_id,
                task=TaskSpec(
                    task_id="source-task",
                    kernel_path=str(source),
                    kernel_name="top",
                ),
                budget=BudgetManager(
                    request.budget_contract.to_budget_limits()
                ),
                trace=trace,
                root_record=root_record,
            )

            result = phase(context)
            event = result.metadata["pre_formal_failure_event"]
            root_snapshot = root_record.snapshot()
            persisted = json.loads(
                (
                    layout.artifact_root
                    / "bootstrap"
                    / "candidate_generation_failure.json"
                ).read_text(encoding="utf-8")
            )
            terminal_events = list(
                (
                    layout.artifact_root
                    / "bootstrap"
                ).glob("candidate_generation_failure.json")
            )

        self.assertIs(result.status, PhaseStatus.FAILED)
        self.assertFalse(result.metadata["formal_validation_started"])
        self.assertEqual(
            result.metadata["failed_stage"], "candidate_generation"
        )
        self.assertEqual(event, persisted)
        self.assertEqual(tuple(root_snapshot["events"]), (event["event_id"],))
        self.assertEqual(root_snapshot["cases"], {})
        self.assertEqual(root_snapshot["attempts"], {})
        self.assertEqual(len(terminal_events), 1)
        self.assertEqual(event["event_type"], "pre_formal_generation_failure")
        self.assertFalse(event["candidate_artifact_available"])
        self.assertNotIn("candidate output was unavailable", json.dumps(event))

    def test_original_only_sanitizer_failure_repairs_hidden_testbench(self):
        failure = {
            "status": "original_run_failed",
            "failure_owner": "unknown",
            "next_action": "review_unknown",
            "failure_evidence_source": "original-only sanitizer/runtime",
            "owner_authority": "runtime_not_isolated",
            "evidence_complete": False,
        }

        self.assertEqual(
            tb_optimizer._coverage_action(failure),
            "review_unknown",
        )
        self.assertEqual(
            tb_optimizer._hidden_generation_action(failure),
            "repair_testbench",
        )
        self.assertEqual(failure["failure_owner"], "unknown")

    def test_hidden_generation_keeps_other_unknown_failures_for_review(self):
        failure = {
            "status": "original_run_timeout",
            "failure_owner": "unknown",
            "next_action": "review_unknown",
            "failure_evidence_source": "original-only timeout",
        }

        self.assertEqual(
            tb_optimizer._hidden_generation_action(failure),
            "review_unknown",
        )

    def test_hidden_payload_is_code_free_and_structured(self):
        secret = "PRIVATE_HELD_OUT_STRUCTURE"
        exc = tb_optimizer.TestbenchGenerationExhausted(
            split="hidden",
            stage="hidden_generation_qualification",
            trajectories=[failed_trajectory(secret=secret)],
        )
        payload = exc.to_dict()
        rendered = json.dumps(payload, sort_keys=True)

        self.assertEqual(
            payload["failure_kind"],
            "testbench_generation_exhausted",
        )
        self.assertEqual(payload["failure_owner"], "stub")
        self.assertEqual(payload["next_action"], "regenerate_stub")
        self.assertIn("no qualified trajectory", str(exc))
        self.assertIn(
            "golden hidden testbench generation",
            str(exc),
        )
        self.assertEqual(payload["attempt_count"], 2)
        self.assertEqual(payload["trajectory_count"], 1)
        self.assertTrue(payload["bounded"])
        self.assertFalse(
            payload["hidden_testbench_exposed_to_model"]
        )
        self.assertNotIn(secret, rendered)
        self.assertNotIn("tb_code", rendered)
        self.assertNotIn("stub_code", rendered)

    def test_hidden_testbench_owned_diagnostic_is_redacted(self):
        secret = "DO_NOT_SURFACE_THIS_HELD_OUT_DETAIL"
        trajectory = failed_trajectory(
            owner="testbench",
            action="repair_testbench",
            secret=secret,
        )
        trajectory["rounds"][-1]["compile_stderr"] = secret
        exc = tb_optimizer.TestbenchGenerationExhausted(
            split="hidden",
            stage="hidden_generation_qualification",
            trajectories=[trajectory],
        )
        rendered = json.dumps(exc.to_dict(), sort_keys=True)
        self.assertNotIn(secret, rendered)
        self.assertIn("operator-only artifacts", rendered)

    def test_hidden_generator_raises_typed_exhaustion(self):
        failed = failed_trajectory()
        with patch.object(
            tb_optimizer,
            "run_trajectory",
            return_value=failed,
        ):
            with self.assertRaises(
                tb_optimizer.TestbenchGenerationExhausted
            ) as captured:
                tb_optimizer.make_golden_hidden_tb(
                    orig_code="void top(){}\n",
                    kernel_name="top",
                    pinned_public_hls_decl="void top_hls();",
                    M=1,
                    K=1,
                    llm_config={},
                )

        payload = captured.exception.to_dict()
        self.assertEqual(payload["split"], "hidden")
        self.assertEqual(
            payload["stage"],
            "hidden_generation_qualification",
        )
        self.assertEqual(payload["failure_owner"], "stub")

    def test_flow_converts_typed_exhaustion_to_false_result(self):
        exc = tb_optimizer.TestbenchGenerationExhausted(
            split="hidden",
            stage="hidden_generation_qualification",
            trajectories=[failed_trajectory()],
        )
        cv = {
            "generation_event_order": [
                "public_generation",
                "candidate_generation",
            ],
            "generated_hidden_testbench": "must-be-cleared",
        }
        with tempfile.TemporaryDirectory() as directory:
            from agrefactor.evidence import CaseRecorder, CaseStore, CaseStatus

            root = Path(directory)
            experience_root = root / "experiences"
            recorder = CaseRecorder(
                root / "cases",
                "public-generation",
                case_scope_id="public-generation",
                experience_root=experience_root,
            )
            recorder.record_public_generation_diagnostic(
                {
                    "event_id": "public-generation-diagnostic",
                    "run_id": "public-generation",
                    "validation_id": "public-generation",
                    "stage": "public_testbench_generation",
                    "split": "public",
                    "owner": "testbench",
                    "route_action": "review_unknown",
                    "evidence_refs": ["evidence:public-generation"],
                    "evidence_view": "agent_safe",
                    "hidden_content_persisted": False,
                }
            )
            case_id = CaseStore(root / "cases").open_cases()[0].case_id
            with patch.object(
                flow_new.tools.general,
                "save_context",
            ) as save:
                success, returned = (
                    flow_new._finish_test_generation_exhaustion(
                        cv,
                        exc,
                        str(root / "output"),
                        recorder,
                    )
                )

            case = CaseStore(root / "cases").read_case(case_id)
            self.assertEqual(case.status, CaseStatus.CLOSED)
            self.assertEqual(case.outcome.value, "unresolved")
            self.assertEqual(
                list((experience_root / "records").glob("*.json")), []
            )

        self.assertFalse(success)
        self.assertIs(returned, cv)
        self.assertEqual(
            returned["failed_stage"],
            "hidden_generation_qualification",
        )
        self.assertEqual(returned["failure_owner"], "stub")
        self.assertEqual(
            returned["generated_hidden_testbench"],
            "",
        )
        self.assertFalse(
            returned["hidden_testbench_exposed_to_model"]
        )
        save.assert_called_once()

    def test_source_bootstrap_preserves_structured_failed_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "kernel.cpp"
            source.write_text(
                'extern "C" int top(int x) { return x; }\n',
                encoding="utf-8",
            )
            runtime = resolve_model_runtime("deepseek-v4-flash")
            request = SourceBootstrapRequest(
                source_path=source,
                top_function="top",
                mode=RunMode.REFACTOR,
                effective_model_config=runtime.effective_config,
                target=resolve_target_profile(None),
                test_source_plan=build_test_source_plan(),
                budget_contract=(
                    DEFAULT_SOURCE_RUN_BUDGET_PROFILE.resolve()
                ),
                max_candidate_repairs=2,
                run_id="generation-exhaustion-test",
            )
            layout = SourceRunLayout.create(
                request.run_id,
                artifact_base=root / "artifacts",
                work_base=root / "work",
            )
            layout.artifact_root.mkdir(parents=True)
            payload = (
                tb_optimizer.TestbenchGenerationExhausted(
                    split="hidden",
                    stage="hidden_generation_qualification",
                    trajectories=[failed_trajectory()],
                ).to_dict()
            )
            generation = FailedGenerationAdapter(payload)
            payload.update(
                counter_source="explicit",
                artifact_request_count=9,
                artifact_response_count=8,
                artifact_format_retry_count=6,
                coverage_check_count=0,
                trajectory_summaries=[{"tb_code": "PRIVATE_HIDDEN_CODE"}],
            )
            phase = SourceBootstrapPhase(
                request=request,
                layout=layout,
                generation_adapter=generation,
                formal_phase_builder=lambda *_args: self.fail(
                    "formal validation must not start"
                ),
            )
            context = RunContext(
                run_id=request.run_id,
                task=TaskSpec(
                    task_id="source-task",
                    kernel_path=str(source),
                    kernel_name="top",
                ),
                budget=BudgetManager(
                    request.budget_contract.to_budget_limits()
                ),
                trace=TraceRecorder(
                    request.run_id,
                    task_id="source-task",
                ),
            )

            result = phase(context)

        self.assertIs(result.status, PhaseStatus.FAILED)
        self.assertFalse(result.succeeded)
        self.assertFalse(
            result.metadata["formal_validation_started"]
        )
        self.assertEqual(
            result.metadata["failed_stage"],
            "hidden_generation_qualification",
        )
        self.assertEqual(
            result.metadata["failure_owner"],
            "stub",
        )
        self.assertEqual(result.metadata["attempt_count"], 2)
        self.assertEqual(result.metadata["trajectory_count"], 1)
        failure = result.metadata["generation_failure"]
        self.assertEqual(failure["counter_source"], "explicit")
        self.assertEqual(failure["artifact_request_count"], 9)
        self.assertEqual(failure["artifact_response_count"], 8)
        self.assertEqual(failure["artifact_format_retry_count"], 6)
        self.assertEqual(failure["coverage_check_count"], 0)
        self.assertNotIn("PRIVATE_HIDDEN_CODE", json.dumps(result.metadata))
        self.assertFalse(
            result.metadata["hidden_testbench_exposed_to_model"]
        )

    def test_unrelated_infrastructure_exception_remains_error(self):
        def broken(_context):
            raise OSError("filesystem unavailable")

        task = TaskSpec(
            task_id="infrastructure-error-test",
            kernel_path="kernel.cpp",
            kernel_name="top",
            mode=RunMode.REFACTOR,
        )
        result = UnifiedRunner(
            {RunPhase.REFACTOR: broken}
        ).run(task, run_id="infrastructure-error-test")
        self.assertIs(result.status, RunStatus.ERROR)
        self.assertIs(
            result.phases[0].status,
            PhaseStatus.ERROR,
        )


if __name__ == "__main__":
    unittest.main()


class GenerationTerminationEvidenceTests(unittest.TestCase):
    def test_precondition_failure_is_not_exhaustion_and_preserves_actual_events(self):
        trajectory = {
            "trajectory_idx": 0, "trajectory_status": "exception", "rounds": [],
            "failure_owner": "unknown", "next_action": "review_unknown",
            "termination_reason": "frozen_contract_precondition_failed",
            "retry_exhausted": False, "repair_eligible": False,
            "qualification_checks_used": 0, "repairs_used": 0,
            "artifact_event_counts": {"testbench": {"requests": 0, "responses": 0}},
        }
        failure = tb_optimizer.TestbenchGenerationExhausted(
            split="hidden", stage="hidden_generation_qualification", trajectories=[trajectory],
        ).to_dict()
        self.assertFalse(failure["retry_exhausted"])
        self.assertEqual(failure["termination_reason"], "frozen_contract_precondition_failed")
        self.assertFalse(failure["repair_eligible"])
        self.assertEqual(failure["attempt_count"], 0)
        from agrefactor.product.source_bootstrap import _extract_generation_failure_metadata
        metadata = _extract_generation_failure_metadata((False, {"generation_failure": failure}))
        self.assertEqual(metadata["termination_reason"], failure["termination_reason"])
        self.assertFalse(metadata["retry_exhausted"])
        self.assertFalse(metadata["repair_eligible"])
        self.assertEqual(metadata["generation_failure"]["trajectory_summaries"][0]["termination_reason"],
                         failure["termination_reason"])

    def test_mixed_termination_keeps_each_actual_cause(self):
        blocked = {"trajectory_idx": 0, "rounds": [], "qualification_checks_used": 0,
                   "repairs_used": 0, "termination_reason": "frozen_contract_precondition_failed",
                   "retry_exhausted": False, "repair_eligible": False}
        exhausted = failed_trajectory()
        exhausted.update(trajectory_idx=1, termination_reason="bounded_generation_exhausted",
                         retry_exhausted=True, repair_eligible=True)
        failure = tb_optimizer.TestbenchGenerationExhausted(
            split="hidden", stage="hidden_generation_qualification", trajectories=[blocked, exhausted],
        ).to_dict()
        self.assertEqual(failure["termination_reason"], "mixed_termination")
        self.assertFalse(failure["retry_exhausted"])
        self.assertEqual([item["retry_exhausted"] for item in failure["trajectory_summaries"]], [False, True])

    def test_explicit_exhausted_branch_can_have_zero_repair_budget(self):
        trajectory = {"rounds": [], "qualification_checks_used": 0, "repairs_used": 0,
                      "termination_reason": "bounded_generation_exhausted", "retry_exhausted": True,
                      "repair_eligible": True}
        failure = tb_optimizer.TestbenchGenerationExhausted(
            split="public", stage="public_generation_qualification", trajectories=[trajectory],
        ).to_dict()
        self.assertTrue(failure["retry_exhausted"])
        self.assertEqual(failure["termination_reason"], "bounded_generation_exhausted")

    def test_zero_repair_review_qualification_does_not_claim_exhaustion(self):
        for owner in ("unknown", "toolchain", "original"):
            with self.subTest(owner=owner):
                result, requests = self._zero_repair_qualification(owner, "review_" + owner)
                self.assertEqual(result["trajectory_status"], "coverage_failed")
                self.assertEqual(result["termination_reason"], "qualification_review_required")
                self.assertFalse(result["retry_exhausted"])
                self.assertEqual(requests, 2)
                self.assertEqual(result["rounds"][-1]["failure_owner"], owner)

    def test_zero_repair_eligible_qualification_still_reports_exhaustion(self):
        result, requests = self._zero_repair_qualification("testbench", "repair_testbench")
        self.assertEqual(result["termination_reason"], "bounded_generation_exhausted")
        self.assertTrue(result["retry_exhausted"])
        self.assertEqual(requests, 2)

    def _zero_repair_qualification(self, owner, action):
        testbench = "int entry(int value); int entry_hls(int value); int main(){return 0;}"
        stub = "int entry_hls(int value){return value;}"
        with patch.object(tb_optimizer, "HLSAgentLoader"), \
             patch.object(tb_optimizer, "_request_cpp_artifact", side_effect=[testbench, stub]) as request, \
             patch.object(tb_optimizer, "_measure_qualified_coverage", return_value={
                 "status": "compile_failed", "cov_pct": None,
                 "compile_stderr": "qualification diagnostic", "failure_owner": owner,
                 "next_action": action,
             }):
            result = tb_optimizer.run_trajectory(
                "int entry(int value){return value;}", "entry", K=1, target_pct=80,
                llm_config={}, want_sig_spec=False, max_repairs=0,
            )
        return result, request.call_count

    def test_valid_public_with_unreplayable_context_is_extraction_precondition(self):
        from flow.tools.tb_optimizer import freeze_public_type_contract, ModelArtifactError
        source = "constexpr int Extent = 4; void compute(int values[Extent]);"
        incomplete_context = {"status": "confirmed", "types": [], "fingerprint": "layout", "declaration_context": ""}
        with patch.object(tb_optimizer, "extract_top_type_contract", return_value=incomplete_context):
            with self.assertRaises(ModelArtifactError) as raised:
                freeze_public_type_contract(source, "compute")
        self.assertFalse(raised.exception.repair_eligible)
        self.assertEqual(raised.exception.termination_reason, "frozen_contract_precondition_failed")


class FrozenContractCacheIdentityTests(unittest.TestCase):
    def test_changed_value_dependency_invalidates_cache_without_changing_layout(self):
        first = {"fingerprint": "same-layout", "types": [],
                 "declaration_context": "constexpr int Width=4;"}
        changed = dict(first, declaration_context="constexpr int Width=5;")
        first_sha = tb_optimizer.public_type_contract_sha256(first)
        second_sha = tb_optimizer.public_type_contract_sha256(changed)
        self.assertNotEqual(first_sha, second_sha)
        self.assertEqual(first["fingerprint"], changed["fingerprint"])
        with tempfile.TemporaryDirectory() as directory:
            tb_optimizer._write_golden_cache(directory, "entry", {
                "orig_sha256": "source", "public_type_contract_sha256": first_sha,
            })
            self.assertIsNotNone(tb_optimizer._load_golden_cache(
                directory, "entry", "source", expected_type_contract_sha=first_sha))
            self.assertIsNone(tb_optimizer._load_golden_cache(
                directory, "entry", "source", expected_type_contract_sha=second_sha))


class FrozenDeclarationReplayAndTerminationTests(unittest.TestCase):
    def test_unrelated_context_error_cannot_be_hidden_by_valid_top_cursor(self):
        contract = {"status": "confirmed", "types": [], "fingerprint": "layout",
                    "declaration_context": "constexpr int Bound = sizeof(MissingType);"}
        with patch.object(tb_optimizer, "extract_top_type_contract", return_value=contract):
            with self.assertRaises(tb_optimizer.ModelArtifactError) as raised:
                tb_optimizer.freeze_public_type_contract("void entry(int value);", "entry")
        self.assertEqual(raised.exception.termination_reason, "frozen_contract_precondition_failed")
        self.assertFalse(raised.exception.repair_eligible)

    def test_failed_coordinated_qualification_does_not_claim_remaining_budget_exhausted(self):
        tb = "int entry_hls(int value); int entry(int value); int main(){return 0;}"
        stub = "int entry_hls(int value){return value;}"
        rounds = [{"round":1, "status":"ok", "cov_pct":100, "tb_code":tb,
                   "stub_code":stub, "uncovered_lines":[]}]
        with patch.object(tb_optimizer, "_synth_check", return_value=(False, "generic synth failure")), \
             patch.object(tb_optimizer, "_request_cpp_artifact", side_effect=[stub, tb, stub]), \
             patch.object(tb_optimizer, "_measure_qualified_coverage", return_value={
                 "status":"compile_failed", "cov_pct":None, "compile_stderr":"generic qualification failure"}):
            result = tb_optimizer._finalize_trajectory(
                object(), rounds, False, 0, expected_hls_name="entry_hls",
                orig_code="int entry(int value){return value;}", synth_retry_budget=3,
            )
        self.assertFalse(result["qualified"])
        self.assertFalse(result["retry_exhausted"])
        self.assertEqual(result["termination_reason"], "qualification_review_required")
        self.assertEqual(len(result["rounds"]), 2)


class RefreshedPublicContractIdentityTests(unittest.TestCase):
    def test_public_refresh_updates_final_suite_and_preserves_hidden_identity(self):
        from agrefactor.config import EvaluationSplit, TestSuiteSpec
        from agrefactor.product.source_bootstrap import _apply_public_runtime_contract_updates
        old = {"schema_version":1, "kind":"public_differential_self_check_v1",
               "candidate_mismatch_returncodes":[1]}
        new = dict(old, schema_version=2, cosim_interface_depths={"payload":1})
        public = TestSuiteSpec(suite_id="public", split=EvaluationSplit.PUBLIC, runtime_contract=old)
        hidden = TestSuiteSpec(suite_id="held-out", split=EvaluationSplit.HIDDEN)
        updated = _apply_public_runtime_contract_updates((public, hidden), {"public":new})
        self.assertEqual(updated[0].to_dict()["runtime_contract"], new)
        self.assertIs(updated[1], hidden)
        self.assertEqual(public.to_dict()["runtime_contract"], old)
        with self.assertRaises(ValueError):
            _apply_public_runtime_contract_updates((public, hidden), {"held-out":new})
