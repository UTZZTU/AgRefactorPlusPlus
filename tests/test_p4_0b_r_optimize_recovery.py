from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agrefactor.config import TaskSpec
from agrefactor.evaluation import FeedbackRouteAction, FeedbackRouter
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
)
from agrefactor.models import (
    CandidateModelAdapter,
    CandidateModelResult,
    CandidateResponseContract,
    ModelResponse,
    TokenUsage,
)
from agrefactor.optimization import (
    BoundedOptimizeCandidateRecoveryCoordinator,
    BoundedRecoveryOptimizerStateMachine,
    BudgetIncrement,
    CandidateQualificationResult,
    CandidateRecord,
    CandidateStatus,
    FakeCandidateExecutor,
    FakeExecutionOutcome,
    FakeExecutionStatus,
    FakeHypothesisProvider,
    HypothesisRecord,
    HypothesisRisk,
    OptimizationLevel,
    OptimizeCandidateRecoveryRequest,
    OptimizeCandidateRecoveryResult,
    OptimizeRecoveryEvidence,
    OptimizeRecoveryStage,
    OptimizeRecoveryStatus,
    OptimizerArtifactStore,
    OptimizerCheckpointWriter,
    OptimizerState,
    PpaEvidence,
    PpaReportFormat,
    PpaResourceUsage,
    QualificationStage,
    QualificationStatus,
    QualificationStepOutcome,
    QualificationStepRecord,
)
from agrefactor.runtime import (
    BudgetLimits,
    BudgetManager,
    TraceRecorder,
)

TOP_SOURCE = b"void kernel(int *a) { a[0] = a[0] + 1; }\n"
BAD_SOURCE = b"void kernel(int *a) { a[0] = missing_value + 1; }\n"
REPAIRED_SOURCE = b"void kernel(int *a) { a[0] = a[0] + 2; }\n"


def _ppa(candidate_id: str, latency: int) -> PpaEvidence:
    return PpaEvidence(
        evidence_id=f"ppa-{candidate_id}",
        parser_profile="fixture",
        report_format=PpaReportFormat.XML,
        report_relative_path=f"fake/{candidate_id}.xml",
        report_sha256=sha256(f"{candidate_id}:{latency}".encode()).hexdigest(),
        comparison_context_identity_sha256="a" * 64,
        latency_cycles_min=latency,
        latency_cycles_max=latency,
        initiation_interval_min=1,
        initiation_interval_max=1,
        target_clock_period_ns=5.0,
        achieved_clock_period_ns=4.0,
        resources_used=PpaResourceUsage(
            bram_18k=1, dsp=1, ff=10, lut=10, uram=0
        ),
        resources_available=PpaResourceUsage(
            bram_18k=100, dsp=100, ff=1000, lut=1000, uram=10
        ),
        max_resource_utilization_ratio=0.1,
        objective_feasible=True,
        constraint_violations=(),
        parser_warnings=("fixture",),
    )


def _accepted(candidate_id: str, latency: int) -> CandidateQualificationResult:
    steps = []
    for stage in (
        QualificationStage.SOURCE,
        QualificationStage.PREFLIGHT,
        QualificationStage.PUBLIC,
        QualificationStage.CSYNTH,
        QualificationStage.HIDDEN,
        QualificationStage.PPA,
        QualificationStage.FEASIBILITY,
    ):
        steps.append(
            QualificationStepRecord(
                stage=stage,
                outcome=QualificationStepOutcome.PASSED,
                evidence_view=(
                    "operator_full"
                    if stage is QualificationStage.HIDDEN
                    else "internal_safe"
                ),
                route_action=None,
                source="fixture",
                source_report_id=(
                    None
                    if stage is QualificationStage.HIDDEN
                    else f"{candidate_id}-{stage.value}"
                ),
                source_item_count=0,
                source_blocking=False,
                reason_codes=("fixture_passed",),
                metadata={"physical_execution": False},
            )
        )
    return CandidateQualificationResult(
        qualification_id=f"qual-{candidate_id}",
        candidate_id=candidate_id,
        status=QualificationStatus.ACCEPTED,
        steps=tuple(steps),
        correctness_passed=True,
        synthesis_passed=True,
        objective_feasible=True,
        ppa=_ppa(candidate_id, latency),
        cache_key_sha256=sha256(f"cache:{candidate_id}".encode()).hexdigest(),
        cache_hit=False,
        budget_before={},
        budget_after={},
        decision={"decision": "accept", "reason_codes": ["fixture_passed"]},
    )


def _rejected(candidate_id: str) -> CandidateQualificationResult:
    return CandidateQualificationResult(
        qualification_id=f"qual-{candidate_id}",
        candidate_id=candidate_id,
        status=QualificationStatus.REJECTED,
        steps=(
            QualificationStepRecord(
                stage=QualificationStage.SOURCE,
                outcome=QualificationStepOutcome.FAILED,
                evidence_view="internal_safe",
                route_action=None,
                source="fixture",
                source_report_id=f"{candidate_id}-source",
                source_item_count=1,
                source_blocking=True,
                reason_codes=("candidate_compile_failed",),
                metadata={"physical_execution": False},
            ),
        ),
        correctness_passed=False,
        synthesis_passed=False,
        objective_feasible=None,
        ppa=None,
        cache_key_sha256=sha256(f"cache:{candidate_id}".encode()).hexdigest(),
        cache_hit=False,
        budget_before={},
        budget_after={},
        decision={"decision": "reject", "reason_codes": ["candidate_compile_failed"]},
    )


def _feedback(stage, category, reason=None, owner=FeedbackOwner.CANDIDATE):
    report = FeedbackReport(
        report_id=f"r-{stage.value}-{category.value}",
        source="fixture",
        items=(
            FeedbackItem(
                feedback_id="f-1",
                stage=stage,
                category=category,
                severity=FeedbackSeverity.ERROR,
                owner=owner,
                summary="fixture failure",
            ),
        ),
        metadata={
            "evidence_view": "agent_safe",
            **({} if reason is None else {"preflight_reason_code": reason}),
        },
    )
    route = FeedbackRouter().route(report, decision_id="d-1")
    return report, route




class _CoordinatorAdapter(CandidateModelAdapter):
    def __init__(self, proposal: bytes):
        self.proposal = proposal.decode("utf-8")
        self._responses_fixture = []
        self.requests = []

    @property
    def family_instruction(self):
        return None

    @property
    def family_profile(self):
        from agrefactor.models import NEUTRAL_MODEL_FAMILY_PROFILE

        return NEUTRAL_MODEL_FAMILY_PROFILE

    @property
    def responses(self):
        return tuple(self._responses_fixture)

    def generate(
        self,
        request,
        *,
        before_provider_call=None,
        after_provider_response=None,
    ):
        self.requests.append(request)
        if before_provider_call is not None:
            before_provider_call()
        response = ModelResponse(
            text=f"```cpp\n{self.proposal}\n```",
            model="fixture-model",
            usage=TokenUsage(
                prompt_tokens=5,
                completion_tokens=5,
            ),
            finish_reason="stop",
        )
        self._responses_fixture.append(response)
        if after_provider_response is not None:
            after_provider_response(response)
        contract = (
            request.response_contract
            or CandidateResponseContract.from_candidate(
                request.task,
                request.current_candidate,
            )
        )
        candidate_code = contract.extract_and_validate(response.text)
        return CandidateModelResult(
            candidate_code=candidate_code,
            logical_model_name="fixture-model",
            provider_name="fixture-provider",
            response=response,
            request_parameters={},
            prompt_manifest=request.prompt.manifest,
            response_contract=contract,
        )


class _CoordinatorValidator:
    def __init__(self, *, accepted=True):
        self.accepted = accepted
        self.requests = []

    def validate_recovery(self, request):
        from agrefactor.optimization import CandidateExecutionResult

        self.requests.append(request)
        qualification = (
            _accepted(request.candidate_id, 80)
            if self.accepted
            else _rejected(request.candidate_id)
        )
        return CandidateExecutionResult(
            source=request.source,
            qualification=qualification,
        )


def _hypothesis():
    return HypothesisRecord(
        hypothesis_id="hyp-1",
        level=OptimizationLevel.STRUCTURAL,
        parent_candidate_id="baseline",
        claim="unroll one inner loop",
        supporting_evidence_ids=(),
        expected_benefit={
            "metric": "latency",
            "direction": "decrease",
        },
        risk=HypothesisRisk.LOW,
        modification_scope=("inner_loop",),
        verification_plan=(
            "preflight",
            "public",
            "csynth",
            "hidden",
        ),
        model_identity={
            "provider": "fixture",
            "network": False,
        },
        prompt_identity_sha256="b" * 64,
    )


def _source_candidate(*, already_recovered=False):
    decision = {"decision": "reject"}
    if already_recovered:
        decision["recovery_of"] = "cand-0"
    return CandidateRecord(
        candidate_id="cand-1",
        sequence=1,
        parent_candidate_id="baseline",
        hypothesis_id="hyp-1",
        level=OptimizationLevel.STRUCTURAL,
        source_sha256=sha256(BAD_SOURCE).hexdigest(),
        source_artifact="candidates/cand-1/source.cpp",
        status=CandidateStatus.REJECTED,
        correctness={"passed": False},
        synthesis={"passed": False},
        decision=decision,
        created_at_utc="2026-08-03T00:00:00Z",
    )


def _recovery_request(*, source_candidate=None):
    candidate = source_candidate or _source_candidate()
    return OptimizeCandidateRecoveryRequest(
        run_id="p4-0b-r-coordinator-test",
        source_candidate=candidate,
        source=BAD_SOURCE,
        interface_source=TOP_SOURCE,
        source_qualification=_rejected("cand-1"),
        hypothesis=_hypothesis(),
        recovery_candidate_id="cand-2",
        recovery_sequence=2,
        budget_before={},
        created_at_utc="2026-08-03T00:00:01Z",
    )


class _FakeRecovery:
    name = "fake-recovery"
    uses_network = False
    uses_vitis = False

    def __init__(self, result):
        self.result = result
        self.requests = []

    def recover(self, request):
        self.requests.append(request)
        return self.result

    def summary(self):
        return {"attempted": len(self.requests)}


class P40BROptimizeRecoveryTests(unittest.TestCase):
    def test_preflight_compile_eligible(self):
        report, route = _feedback(
            FeedbackStage.COMPILE,
            FeedbackCategory.SYNTAX_ERROR,
            "candidate_compile_failed",
        )
        evidence = OptimizeRecoveryEvidence(
            OptimizeRecoveryStage.PREFLIGHT,
            report,
            route,
            ("candidate_compile_failed",),
        )
        self.assertEqual(evidence.stage.value, "preflight")

    def test_preflight_top_missing_eligible(self):
        report, route = _feedback(
            FeedbackStage.STATIC_CHECK,
            FeedbackCategory.UNDECLARED_SYMBOL,
            "candidate_top_missing",
        )
        evidence = OptimizeRecoveryEvidence(
            OptimizeRecoveryStage.PREFLIGHT,
            report,
            route,
            ("candidate_top_missing",),
        )
        self.assertIn("candidate_top_missing", evidence.reason_codes)

    def test_preflight_unknown_ineligible(self):
        report, route = _feedback(
            FeedbackStage.COMPILE,
            FeedbackCategory.UNKNOWN,
            "ownership_unknown",
        )
        with self.assertRaises(ValueError):
            OptimizeRecoveryEvidence(
                OptimizeRecoveryStage.PREFLIGHT,
                report,
                route,
                ("ownership_unknown",),
            )

    def test_testbench_owner_ineligible(self):
        report, route = _feedback(
            FeedbackStage.COMPILE,
            FeedbackCategory.SYNTAX_ERROR,
            "candidate_compile_failed",
            FeedbackOwner.TESTBENCH,
        )
        self.assertIs(route.action, FeedbackRouteAction.REPAIR_TESTBENCH)
        with self.assertRaises(ValueError):
            OptimizeRecoveryEvidence(
                OptimizeRecoveryStage.PREFLIGHT,
                report,
                route,
                ("candidate_compile_failed",),
            )

    def test_csynth_legality_eligible(self):
        report, route = _feedback(
            FeedbackStage.CSYNTH,
            FeedbackCategory.UNSUPPORTED_CONSTRUCT,
        )
        evidence = OptimizeRecoveryEvidence(
            OptimizeRecoveryStage.CSYNTH,
            report,
            route,
            ("candidate_csynth_legality_failed",),
        )
        self.assertEqual(evidence.stage.value, "csynth")

    def test_csynth_timing_ineligible(self):
        report, route = _feedback(
            FeedbackStage.CSYNTH,
            FeedbackCategory.TIMING_VIOLATION,
        )
        with self.assertRaises(ValueError):
            OptimizeRecoveryEvidence(
                OptimizeRecoveryStage.CSYNTH,
                report,
                route,
                ("timing_violation",),
            )

    def test_hidden_public_ppa_are_not_recovery_stages(self):
        values = {item.value for item in OptimizeRecoveryStage}
        self.assertNotIn("hidden", values)
        self.assertNotIn("public", values)
        self.assertNotIn("ppa", values)

    def test_result_requires_qualification_pair(self):
        with self.assertRaises(ValueError):
            OptimizeCandidateRecoveryResult(
                status=OptimizeRecoveryStatus.VALIDATED,
                source_candidate_id="cand-1",
                recovery_candidate_id="cand-2",
                stage=OptimizeRecoveryStage.PREFLIGHT,
                reason_codes=("candidate_compile_failed",),
                source=REPAIRED_SOURCE,
                qualification=None,
                budget_before={},
                budget_after={},
            )

    def test_lineage_metadata_explicit(self):
        result = self._recovery_result(latency=80)
        metadata = result.lineage_metadata()
        self.assertEqual(metadata["recovery_of"], "cand-1")
        self.assertEqual(metadata["recovery_attempt"], 1)

    def test_recovery_creates_new_candidate(self):
        result = self._run(self._recovery_result(latency=80))
        self.assertIn("cand-1", result.candidates)
        self.assertIn("cand-2", result.candidates)
        self.assertEqual(result.candidates["cand-2"].parent_candidate_id, "cand-1")
        self.assertEqual(
            result.candidates["cand-2"].hypothesis_id,
            result.candidates["cand-1"].hypothesis_id,
        )
        self.assertEqual(result.state.executed_candidate_count, 2)

    def test_improved_repair_updates_best_correct(self):
        result = self._run(self._recovery_result(latency=80))
        self.assertEqual(result.state.best_correct_candidate_id, "cand-2")
        self.assertEqual(result.state.best_ppa_candidate_id, "cand-2")

    def test_non_improving_repair_preserves_baseline(self):
        result = self._run(self._recovery_result(latency=120))
        self.assertEqual(result.state.best_correct_candidate_id, "baseline")
        self.assertEqual(result.state.best_ppa_candidate_id, "baseline")

    def test_failed_repair_descendant_rejected(self):
        recovery = OptimizeCandidateRecoveryResult(
            status=OptimizeRecoveryStatus.VALIDATION_FAILED,
            source_candidate_id="cand-1",
            recovery_candidate_id="cand-2",
            stage=OptimizeRecoveryStage.PREFLIGHT,
            reason_codes=("candidate_compile_failed",),
            source=REPAIRED_SOURCE,
            qualification=_rejected("cand-2"),
            budget_before={},
            budget_after={},
        )
        result = self._run(recovery)
        self.assertEqual(result.candidates["cand-2"].status, CandidateStatus.REJECTED)
        self.assertEqual(result.state.best_correct_candidate_id, "baseline")

    def test_blocked_recovery_creates_no_descendant(self):
        recovery = OptimizeCandidateRecoveryResult(
            status=OptimizeRecoveryStatus.BUDGET_BLOCKED,
            source_candidate_id="cand-1",
            recovery_candidate_id="cand-2",
            stage=OptimizeRecoveryStage.PREFLIGHT,
            reason_codes=("candidate_compile_failed",),
            source=None,
            qualification=None,
            budget_before={},
            budget_after={},
            error_type="BudgetExceededError",
        )
        result = self._run(recovery)
        self.assertIn("cand-1", result.candidates)
        self.assertNotIn("cand-2", result.candidates)
        self.assertEqual(result.state.executed_candidate_count, 1)

    def test_recovery_is_called_once_without_nesting(self):
        recovery = _FakeRecovery(
            OptimizeCandidateRecoveryResult(
                status=OptimizeRecoveryStatus.VALIDATION_FAILED,
                source_candidate_id="cand-1",
                recovery_candidate_id="cand-2",
                stage=OptimizeRecoveryStage.PREFLIGHT,
                reason_codes=("candidate_compile_failed",),
                source=REPAIRED_SOURCE,
                qualification=_rejected("cand-2"),
                budget_before={},
                budget_after={},
            )
        )
        self._run_with_coordinator(recovery)
        self.assertEqual(len(recovery.requests), 1)


    def test_coordinator_success_writes_artifacts_and_restarts(self):
        report, route = _feedback(
            FeedbackStage.COMPILE,
            FeedbackCategory.SYNTAX_ERROR,
            "candidate_compile_failed",
        )
        evidence = OptimizeRecoveryEvidence(
            OptimizeRecoveryStage.PREFLIGHT,
            report,
            route,
            ("candidate_compile_failed",),
        )
        adapter = _CoordinatorAdapter(REPAIRED_SOURCE)
        validator = _CoordinatorValidator(accepted=True)
        with tempfile.TemporaryDirectory() as tmp:
            coordinator = BoundedOptimizeCandidateRecoveryCoordinator(
                model_adapter=adapter,
                validator=validator,
                evidence_provider=lambda *_: evidence,
                task=TaskSpec(
                    task_id="p4-0b-r",
                    kernel_path="kernel.cpp",
                    kernel_name="kernel",
                ),
                original_code=TOP_SOURCE.decode(),
                budget=BudgetManager(),
                validation_increment=BudgetIncrement(
                    tool_calls=14,
                    compile_calls=8,
                    csim_calls=2,
                    csynth_calls=1,
                ),
                artifact_root=Path(tmp) / "recovery",
            )
            result = coordinator.recover(_recovery_request())
            self.assertIs(result.status, OptimizeRecoveryStatus.VALIDATED)
            self.assertEqual(len(adapter.requests), 1)
            self.assertEqual(len(validator.requests), 1)
            self.assertEqual(
                adapter.requests[0].prompt.manifest["purpose"],
                "candidate_compile_repair",
            )
            self.assertEqual(coordinator.summary()["attempted"], 1)
            self.assertTrue(Path(result.artifact_paths["run_record_path"]).is_file())
            self.assertEqual(coordinator._budget.snapshot().llm_calls, 1)

    def test_coordinator_budget_blocked_before_model(self):
        report, route = _feedback(
            FeedbackStage.COMPILE,
            FeedbackCategory.SYNTAX_ERROR,
            "candidate_compile_failed",
        )
        evidence = OptimizeRecoveryEvidence(
            OptimizeRecoveryStage.PREFLIGHT,
            report,
            route,
            ("candidate_compile_failed",),
        )
        adapter = _CoordinatorAdapter(REPAIRED_SOURCE)
        validator = _CoordinatorValidator()
        with tempfile.TemporaryDirectory() as tmp:
            coordinator = BoundedOptimizeCandidateRecoveryCoordinator(
                model_adapter=adapter,
                validator=validator,
                evidence_provider=lambda *_: evidence,
                task=TaskSpec(
                    task_id="p4-0b-r",
                    kernel_path="kernel.cpp",
                    kernel_name="kernel",
                ),
                original_code=TOP_SOURCE.decode(),
                budget=BudgetManager(BudgetLimits(max_llm_calls=0)),
                validation_increment=BudgetIncrement(),
                artifact_root=Path(tmp) / "recovery",
            )
            result = coordinator.recover(_recovery_request())
            self.assertIs(
                result.status,
                OptimizeRecoveryStatus.BUDGET_BLOCKED,
            )
            self.assertEqual(adapter.requests, [])
            self.assertEqual(validator.requests, [])
            self.assertFalse(result.descendant_created)

    def test_coordinator_rejects_parent_source_fallback(self):
        report, route = _feedback(
            FeedbackStage.COMPILE,
            FeedbackCategory.SYNTAX_ERROR,
            "candidate_compile_failed",
        )
        evidence = OptimizeRecoveryEvidence(
            OptimizeRecoveryStage.PREFLIGHT,
            report,
            route,
            ("candidate_compile_failed",),
        )
        adapter = _CoordinatorAdapter(TOP_SOURCE)
        validator = _CoordinatorValidator()
        with tempfile.TemporaryDirectory() as tmp:
            coordinator = BoundedOptimizeCandidateRecoveryCoordinator(
                model_adapter=adapter,
                validator=validator,
                evidence_provider=lambda *_: evidence,
                task=TaskSpec(
                    task_id="p4-0b-r",
                    kernel_path="kernel.cpp",
                    kernel_name="kernel",
                ),
                original_code=TOP_SOURCE.decode(),
                budget=BudgetManager(),
                validation_increment=BudgetIncrement(),
                artifact_root=Path(tmp) / "recovery",
            )
            result = coordinator.recover(_recovery_request())
            self.assertIs(
                result.status,
                OptimizeRecoveryStatus.RESPONSE_REJECTED,
            )
            self.assertIn("parent_source_fallback", result.reason_codes)
            self.assertEqual(validator.requests, [])

    def test_coordinator_semantic_unchanged_is_response_rejected(self):
        report, route = _feedback(
            FeedbackStage.COMPILE,
            FeedbackCategory.SYNTAX_ERROR,
            "candidate_compile_failed",
        )
        evidence = OptimizeRecoveryEvidence(
            OptimizeRecoveryStage.PREFLIGHT,
            report,
            route,
            ("candidate_compile_failed",),
        )
        adapter = _CoordinatorAdapter(BAD_SOURCE)
        validator = _CoordinatorValidator()
        with tempfile.TemporaryDirectory() as tmp:
            coordinator = BoundedOptimizeCandidateRecoveryCoordinator(
                model_adapter=adapter,
                validator=validator,
                evidence_provider=lambda *_: evidence,
                task=TaskSpec(
                    task_id="p4-0b-r",
                    kernel_path="kernel.cpp",
                    kernel_name="kernel",
                ),
                original_code=TOP_SOURCE.decode(),
                budget=BudgetManager(),
                validation_increment=BudgetIncrement(),
                artifact_root=Path(tmp) / "recovery",
            )
            result = coordinator.recover(_recovery_request())
            self.assertIs(
                result.status,
                OptimizeRecoveryStatus.RESPONSE_REJECTED,
            )
            self.assertEqual(validator.requests, [])

    def test_coordinator_csynth_uses_csynth_prompt(self):
        report, route = _feedback(
            FeedbackStage.CSYNTH,
            FeedbackCategory.UNSUPPORTED_CONSTRUCT,
        )
        evidence = OptimizeRecoveryEvidence(
            OptimizeRecoveryStage.CSYNTH,
            report,
            route,
            ("candidate_csynth_legality_failed",),
        )
        adapter = _CoordinatorAdapter(REPAIRED_SOURCE)
        validator = _CoordinatorValidator()
        with tempfile.TemporaryDirectory() as tmp:
            coordinator = BoundedOptimizeCandidateRecoveryCoordinator(
                model_adapter=adapter,
                validator=validator,
                evidence_provider=lambda *_: evidence,
                task=TaskSpec(
                    task_id="p4-0b-r",
                    kernel_path="kernel.cpp",
                    kernel_name="kernel",
                ),
                original_code=TOP_SOURCE.decode(),
                budget=BudgetManager(),
                validation_increment=BudgetIncrement(),
                artifact_root=Path(tmp) / "recovery",
            )
            result = coordinator.recover(_recovery_request())
            self.assertIs(result.status, OptimizeRecoveryStatus.VALIDATED)
            self.assertEqual(
                adapter.requests[0].prompt.manifest["purpose"],
                "candidate_csynth_repair",
            )
            prompt_text = "\n".join(
                message.content
                for message in adapter.requests[0].prompt.messages
            ).lower()
            self.assertNotIn("hidden testbench", prompt_text)

    def test_coordinator_rejects_nested_recovery_source(self):
        report, route = _feedback(
            FeedbackStage.COMPILE,
            FeedbackCategory.SYNTAX_ERROR,
            "candidate_compile_failed",
        )
        evidence = OptimizeRecoveryEvidence(
            OptimizeRecoveryStage.PREFLIGHT,
            report,
            route,
            ("candidate_compile_failed",),
        )
        adapter = _CoordinatorAdapter(REPAIRED_SOURCE)
        validator = _CoordinatorValidator()
        with tempfile.TemporaryDirectory() as tmp:
            coordinator = BoundedOptimizeCandidateRecoveryCoordinator(
                model_adapter=adapter,
                validator=validator,
                evidence_provider=lambda *_: evidence,
                task=TaskSpec(
                    task_id="p4-0b-r",
                    kernel_path="kernel.cpp",
                    kernel_name="kernel",
                ),
                original_code=TOP_SOURCE.decode(),
                budget=BudgetManager(),
                validation_increment=BudgetIncrement(),
                artifact_root=Path(tmp) / "recovery",
            )
            result = coordinator.recover(
                _recovery_request(
                    source_candidate=_source_candidate(
                        already_recovered=True
                    )
                )
            )
            self.assertIs(result.status, OptimizeRecoveryStatus.INELIGIBLE)
            self.assertEqual(adapter.requests, [])
            self.assertEqual(validator.requests, [])

    @staticmethod
    def _recovery_result(latency):
        return OptimizeCandidateRecoveryResult(
            status=OptimizeRecoveryStatus.VALIDATED,
            source_candidate_id="cand-1",
            recovery_candidate_id="cand-2",
            stage=OptimizeRecoveryStage.PREFLIGHT,
            reason_codes=("candidate_compile_failed",),
            source=REPAIRED_SOURCE,
            qualification=_accepted("cand-2", latency),
            budget_before={},
            budget_after={},
        )

    def _run(self, recovery_result):
        return self._run_with_coordinator(_FakeRecovery(recovery_result))

    @staticmethod
    def _run_with_coordinator(coordinator):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            writer = OptimizerCheckpointWriter(root / "optimizer")
            baseline = CandidateRecord(
                candidate_id="baseline",
                sequence=0,
                parent_candidate_id=None,
                hypothesis_id=None,
                level=None,
                source_sha256=sha256(TOP_SOURCE).hexdigest(),
                source_artifact="candidates/baseline/source.cpp",
                status=CandidateStatus.ACCEPTED,
                correctness={"passed": True},
                synthesis={"passed": True},
                ppa=_ppa("baseline", 100).to_dict(),
                decision={"decision": "baseline_accepted"},
                created_at_utc="2026-08-03T00:00:00Z",
            )
            writer.write_candidate_source(baseline, TOP_SOURCE)
            state = replace(
                OptimizerState.initial(run_id="p4-0b-r-test").with_qualified_baseline(
                    baseline
                ),
                best_ppa_candidate_id="baseline",
            )
            engine = BoundedRecoveryOptimizerStateMachine(
                state=state,
                candidates={"baseline": baseline},
                checkpoint_writer=writer,
                provider=FakeHypothesisProvider(),
                executor=FakeCandidateExecutor(
                    outcomes={
                        1: FakeExecutionOutcome(
                            status=FakeExecutionStatus.REJECTED,
                            reason_code="candidate_compile_failed",
                            source_suffix="\n// broken candidate\n",
                        )
                    }
                ),
                recovery_coordinator=coordinator,
                budget=BudgetManager(),
                trace=TraceRecorder("p4-0b-r-test"),
                artifact_store=OptimizerArtifactStore(writer.root),
                resume=False,
            )
            return engine.step()

    def test_replay_direct_entrypoint_is_self_contained(self):
        repo_root = Path(__file__).resolve().parents[1]
        replay = repo_root / "tools/p4_0b_r_replay.py"
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment["PYTHONNOUSERSITE"] = "1"

        with tempfile.TemporaryDirectory(
            prefix="p4_0b_r_entrypoint_test_"
        ) as temporary:
            output = (
                Path(temporary) / "replay.json"
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(replay),
                    "--output",
                    str(output),
                ],
                cwd=temporary,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            payload = json.loads(
                output.read_text(encoding="utf-8")
            )

        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )
        self.assertTrue(payload["passed"])
        self.assertTrue(
            payload["direct_entrypoint"]
        )
        self.assertTrue(
            payload["repo_root_bootstrap"]
        )
        self.assertFalse(
            payload["test_module_dependency"]
        )
        self.assertFalse(payload["network_used"])
        self.assertFalse(payload["vitis_used"])


if __name__ == "__main__":
    unittest.main()
