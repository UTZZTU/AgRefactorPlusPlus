from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agrefactor.config import (
    EvaluationSplit,
    TaskSpec,
    TestSourceKind,
    TestSourceSpec,
    TestSuiteSpec,
)
from agrefactor.evaluation import ValidationState
from agrefactor.evidence import (
    CorpusEvidenceLevel,
    CorpusOutcome,
    DiagnosticCorpusRecord,
    DiagnosticEventProjector,
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
    audit_testbench_semantic_revision,
    build_testbench_semantic_revision,
    testbench_revision_authorization,
    write_diagnostic_corpus,
)
from agrefactor.models import (
    CandidateModelAdapter,
    ModelProvider,
    ModelRegistry,
    ModelSpec,
)
from agrefactor.recovery import (
    RecoveryAction,
    RecoveryAuthority,
    RecoveryLedger,
    RecoveryRequest,
    RecoveryRole,
    RecoveryStage,
    build_effective_repair_quota_summary,
    conservative_v1_policy,
)
from agrefactor.runtime import (
    BudgetLimits,
    BudgetManager,
    CandidateRepairOrchestrationRequest,
    CandidateRepairOrchestrationStatus,
    CandidateRepairPhaseArtifactWriter,
    CandidateRepairValidationOrchestrator,
    RunContext,
    TraceRecorder,
)
from agrefactor.runtime import candidate_repair_integration as integration


ORIGINAL = 'extern "C" int top(int x) { return x; }\n'
CANDIDATE = 'extern "C" int top_hls(int x) { return x; }\n'
BROKEN_TESTBENCH = (
    'extern "C" int top(int);\n'
    'extern "C" int top_hls(int);\n'
    'int main() { return top(7) == top_hls(7) ? 0 : 1 }\n'
)
FIXED_TESTBENCH = BROKEN_TESTBENCH.replace("1 }", "1; }")
WEAK_TESTBENCH = (
    'extern "C" int top(int);\n'
    'extern "C" int top_hls(int);\n'
    'int main() { (void)top(7); (void)top_hls(7); return 0; }\n'
)


class _UnusedProvider(ModelProvider):
    @property
    def name(self):
        return "unused"

    def generate(self, model, request):
        raise AssertionError("R1 deterministic tests must not call a provider")


def _adapter():
    registry = ModelRegistry()
    registry.register_provider(_UnusedProvider())
    registry.register_model(
        ModelSpec(name="unused-model", provider="unused", model="unused")
    )
    return CandidateModelAdapter(registry=registry, model_name="unused-model")


def _task(source_kind=TestSourceKind.DERIVED):
    return TaskSpec(
        task_id="r1-preflight-testbench",
        kernel_path="candidate.cpp",
        kernel_name="top_hls",
        test_suites=(
            TestSuiteSpec(
                suite_id="public-r1",
                suite_version="1",
                split=EvaluationSplit.PUBLIC,
                testbench_path="public-r1.cpp",
                source=TestSourceSpec(
                    source_id="public-r1-source",
                    source_kind=source_kind,
                ),
            ),
        ),
    )


def _context(task, root):
    return RunContext(
        run_id="r1-run",
        task=task,
        budget=BudgetManager(
            BudgetLimits(
                max_llm_calls=20,
                max_tool_calls=30,
                max_compile_calls=30,
                max_csim_calls=10,
                max_csynth_calls=10,
                max_cosim_calls=10,
                max_wall_time_s=5000,
            )
        ),
        trace=TraceRecorder(
            "r1-run", task_id=task.task_id, output_path=Path(root) / "trace.jsonl"
        ),
    )


def _request(code=BROKEN_TESTBENCH):
    return CandidateRepairOrchestrationRequest(
        initial_candidate=CANDIDATE,
        original_code=ORIGINAL,
        preflight_testbench_code=code,
        suite_testbench_codes={"public-r1": code},
        prompt_public_testbench_code=code,
        max_attempts=1,
        reference_top_function="top",
        candidate_top_function="top_hls",
    )


def _report(state, *, failing=False):
    items = ()
    if failing:
        items = (
            FeedbackItem(
                feedback_id="preflight.testbench.syntax",
                stage=FeedbackStage.COMPILE,
                category=FeedbackCategory.SYNTAX_ERROR,
                severity=FeedbackSeverity.ERROR,
                owner=FeedbackOwner.TESTBENCH,
                summary="Public Testbench syntax failure",
                detail="agent-safe normalized diagnostic",
                source="r1_deterministic_fixture",
            ),
        )
    return FeedbackReport(
        report_id=f"r1.{state.value}.{'failed' if failing else 'passed'}",
        source=state.value,
        items=items,
        source_evidence={"fixture": True},
        metadata={
            "evidence_view": "agent_safe",
            "physical_execution": True,
            "evidence_complete": True,
        },
    )


class _ScenarioFactory:
    def __init__(self, work_root):
        self.work_root = Path(work_root)
        self.requests = []

    def build(self, request):
        self.requests.append(request)
        states = (
            ValidationState.PREFLIGHT,
            ValidationState.PUBLIC_EVALUATION,
            ValidationState.CSYNTH,
            ValidationState.PUBLIC_COSIM,
        )

        def handler(context, *, state):
            return _report(
                state,
                failing=(request.attempt == 0 and state is ValidationState.PREFLIGHT),
            )

        return {state: (lambda context, state=state: handler(context, state=state)) for state in states}


class _Repairer:
    def __init__(self, replacement):
        self.replacement = replacement
        self.calls = []

    def repair(self, request):
        self.calls.append(request)
        return self.replacement


class R1RuntimeRecoveryTests(unittest.TestCase):
    def test_preflight_auto_public_testbench_repair_revalidates_full_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            task = _task()
            repairer = _Repairer(FIXED_TESTBENCH)
            factory = _ScenarioFactory(Path(temp) / "work")
            with patch.object(
                integration,
                "build_openai_compatible_testbench_repairer",
                return_value=repairer,
            ):
                result = CandidateRepairValidationOrchestrator(
                    model_adapter=_adapter(), handler_factory=factory
                ).run(_context(task, temp), _request(), validation_id="r1-validation")
            artifacts = CandidateRepairPhaseArtifactWriter(
                Path(temp) / "artifacts"
            ).write(result)
            self.assertTrue(Path(artifacts.effective_repair_quota_path).is_file())
            self.assertTrue(Path(artifacts.diagnostic_events_path).is_file())
            self.assertTrue(Path(artifacts.testbench_semantic_revision_path).is_file())
        self.assertEqual(result.status, CandidateRepairOrchestrationStatus.ACCEPTED)
        self.assertEqual(len(repairer.calls), 1)
        self.assertEqual([item.attempt for item in factory.requests], [0, 1])
        self.assertEqual(result.metadata["runtime_testbench_authorization"], "auto_public_bounded")
        self.assertFalse(result.metadata["testbench_semantic_audit"]["has_errors"])
        self.assertEqual(result.metadata["effective_repair_quota"]["authority"], "explanation_only")
        self.assertGreaterEqual(len(result.metadata["diagnostic_events"]), 1)

    def test_provided_public_testbench_requires_review_without_provider(self):
        with tempfile.TemporaryDirectory() as temp:
            task = _task(TestSourceKind.PROVIDED)
            repairer = _Repairer(FIXED_TESTBENCH)
            with patch.object(
                integration,
                "build_openai_compatible_testbench_repairer",
                return_value=repairer,
            ):
                result = CandidateRepairValidationOrchestrator(
                    model_adapter=_adapter(),
                    handler_factory=_ScenarioFactory(Path(temp) / "work"),
                ).run(_context(task, temp), _request(), validation_id="r1-validation")
        self.assertEqual(result.status, CandidateRepairOrchestrationStatus.REPAIR_NOT_APPLICABLE)
        self.assertEqual(repairer.calls, [])
        self.assertEqual(result.metadata["runtime_testbench_authorization"], "review_required_provided")

    def test_semantic_weakening_is_blocked_before_revalidation(self):
        with tempfile.TemporaryDirectory() as temp:
            task = _task()
            repairer = _Repairer(WEAK_TESTBENCH)
            factory = _ScenarioFactory(Path(temp) / "work")
            with patch.object(
                integration,
                "build_openai_compatible_testbench_repairer",
                return_value=repairer,
            ):
                result = CandidateRepairValidationOrchestrator(
                    model_adapter=_adapter(), handler_factory=factory
                ).run(_context(task, temp), _request(), validation_id="r1-validation")
        self.assertEqual(result.status, CandidateRepairOrchestrationStatus.REPAIR_NOT_APPLICABLE)
        self.assertEqual(len(factory.requests), 1)
        self.assertTrue(result.metadata["testbench_semantic_audit"]["has_errors"])
        self.assertEqual(result.metadata["runtime_testbench_recovery_status"], "blocked_semantic_weakening")


class R1EvidenceContractTests(unittest.TestCase):
    def test_authorization_is_provenance_and_split_bounded(self):
        self.assertEqual(
            testbench_revision_authorization(split="public", source_kind="generated"),
            "auto_public_bounded",
        )
        self.assertEqual(
            testbench_revision_authorization(split="public", source_kind="provided"),
            "review_required_provided",
        )
        self.assertEqual(
            testbench_revision_authorization(split="hidden", source_kind="generated"),
            "forbidden_hidden",
        )

    def test_semantic_auditor_rejects_oracle_removal_without_source_persistence(self):
        revision = build_testbench_semantic_revision(
            FIXED_TESTBENCH,
            WEAK_TESTBENCH,
            suite_id="public-r1",
            split="public",
            source_kind="generated",
            original_top_function="top",
            candidate_top_function="top_hls",
        )
        audit = audit_testbench_semantic_revision(revision)
        self.assertTrue(audit.has_critical)
        self.assertNotIn(FIXED_TESTBENCH, json.dumps(revision))
        self.assertFalse(revision["source_content_persisted"])

    def test_diagnostic_event_rejects_hidden_and_has_no_success_authority(self):
        report = _report(ValidationState.PREFLIGHT, failing=True)
        projector = DiagnosticEventProjector()
        event = projector.from_feedback(
            report,
            run_id="run",
            validation_id="validation",
            validation_state="preflight",
            route_action="repair_testbench",
            selected_feedback_ids=("preflight.testbench.syntax",),
            candidate_code=CANDIDATE,
            public_suite_identities=({"suite_id": "public-r1", "split": "public"},),
        )
        payload = event.to_dict()
        self.assertFalse(payload["accepted"])
        self.assertFalse(payload["success_authority"])
        self.assertFalse(payload["fsm_mutation_allowed"])
        with self.assertRaises(ValueError):
            projector.from_feedback(
                report,
                run_id="run",
                validation_id="hidden",
                validation_state="hidden_evaluation",
                route_action="stop",
            )

    def test_effective_quota_is_explanation_only_and_corpus_is_hashed(self):
        ledger = RecoveryLedger(conservative_v1_policy())
        budget = BudgetManager(BudgetLimits(max_llm_calls=5, max_tool_calls=5))
        ledger.reserve(
            RecoveryRequest(
                action=RecoveryAction.REPAIR,
                role=RecoveryRole.TESTBENCH,
                stage=RecoveryStage.PREFLIGHT,
                evidence_view="agent_safe",
                owner_authority=RecoveryAuthority.DETERMINISTIC_PROVEN,
                lineage_id="lineage",
                physical_tool_launched=True,
                evidence_complete=True,
            ),
            budget=budget,
        )
        quota = build_effective_repair_quota_summary(
            ledger=ledger, budget=budget, candidate_requested_max=3
        ).to_dict()
        self.assertEqual(quota["authority"], "explanation_only")
        self.assertFalse(quota["creates_action_counter"])

        event = DiagnosticEventProjector().from_feedback(
            _report(ValidationState.PREFLIGHT, failing=True),
            run_id="run",
            validation_id="validation",
            validation_state="preflight",
            route_action="repair_testbench",
            selected_feedback_ids=("preflight.testbench.syntax",),
        )
        with tempfile.TemporaryDirectory() as temp:
            manifest = write_diagnostic_corpus(
                Path(temp) / "corpus",
                (
                    DiagnosticCorpusRecord(
                        record_id="r1-e2-testbench-syntax",
                        event=event,
                        evidence_level=CorpusEvidenceLevel.E2_DETERMINISTIC,
                        outcome=CorpusOutcome.VERIFIED_FAILURE,
                        observed_at=event.created_at,
                        source_identity={"fixture": "deterministic"},
                        eligible_for_future_promotion=False,
                    ),
                ),
                corpus_id="r1-corpus-v1",
            )
            self.assertEqual(manifest["record_count"], 1)
            self.assertEqual(len(manifest["manifest_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
