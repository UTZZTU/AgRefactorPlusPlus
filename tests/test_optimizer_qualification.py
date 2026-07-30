from pathlib import Path
import json
import shutil
import tempfile
import unittest

from agrefactor.config import RunMode, TaskSpec
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
)
from agrefactor.optimization import (
    CandidateQualificationRequest,
    CandidateQualificationResult,
    CandidateRecord,
    CandidateStatus,
    OptimizerCheckpointWriter,
    OptimizerState,
    QualificationEvidenceCache,
    QualificationStage,
    QualificationStatus,
    Stage3QualificationOrchestrator,
    SuiteIdentity,
    ValidationCacheIdentity,
    initialize_qualified_baseline,
)
from agrefactor.runtime import (
    BudgetLimits,
    BudgetManager,
    RunContext,
    TraceRecorder,
)


FIXTURES = Path(__file__).parent / "fixtures" / "optimizer"


def pass_report(name, *, hidden=False):
    return FeedbackReport(
        report_id=f"{name}.report",
        source=name,
        items=(),
        source_evidence=(
            {"hidden_testbench": "MUST_NOT_LEAK"} if hidden else {}
        ),
        metadata={
            "evidence_view": "operator_full" if hidden else "agent_safe",
            "physical_execution": True,
            "stage_handler_version": 1,
        },
    )


def fail_report(
    name,
    *,
    owner=FeedbackOwner.CANDIDATE,
    category=FeedbackCategory.FUNCTIONAL_MISMATCH,
    hidden=False,
):
    return FeedbackReport(
        report_id=f"{name}.SECRET_REPORT_ID" if hidden else f"{name}.report",
        source=name,
        items=(
            FeedbackItem(
                feedback_id=f"{name}.item",
                stage=(
                    FeedbackStage.CSIM if hidden else FeedbackStage.TEST
                ),
                category=category,
                severity=FeedbackSeverity.ERROR,
                owner=owner,
                summary="blocking result",
                detail="HIDDEN_DETAIL_MUST_NOT_LEAK" if hidden else None,
                source=name,
                evidence_ref="/private/hidden.cpp" if hidden else None,
            ),
        ),
        source_evidence={
            "hidden_testbench": "MUST_NOT_LEAK" if hidden else "none"
        },
        metadata={
            "evidence_view": "operator_full" if hidden else "agent_safe",
            "physical_execution": True,
        },
    )


def cache_identity(source_sha):
    return ValidationCacheIdentity.build(
        source_sha256=source_sha,
        effective_target={
            "profile": {
                "name": "vitis-2023.2-default",
                "toolchain": "vitis_hls",
                "toolchain_version": "2023.2",
                "device": "xcu200-fsgd2104-2-e",
                "clock_period_ns": 5.0,
                "compile_flags": [],
                "parser_profile": "vitis-hls-2023.2",
                "resource_limits": {},
            }
        },
        toolchain_fingerprint_sha256="2" * 64,
        suites=(
            SuiteIdentity(
                suite_id="public-main",
                split="public",
                content_sha256="3" * 64,
            ),
            SuiteIdentity(
                suite_id="hidden-final",
                split="hidden",
                content_sha256="4" * 64,
            ),
        ),
        compile_flags=(),
        clock_period_ns=5.0,
        device="xcu200-fsgd2104-2-e",
        parser_profile="vitis-hls-2023.2",
    )


class QualificationHarness:
    def __init__(self, test_case, *, resource_limits=None, cache=None):
        self.test_case = test_case
        self.temporary = tempfile.TemporaryDirectory()
        test_case.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "baseline.cpp"
        self.source.write_text(
            "extern \"C\" void top(int *a){a[0]+=1;}\n",
            encoding="utf-8",
        )
        import hashlib

        source_sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.candidate = CandidateRecord(
            candidate_id="baseline",
            sequence=0,
            parent_candidate_id=None,
            hypothesis_id=None,
            level=None,
            source_sha256=source_sha,
            source_artifact="candidates/baseline/source.cpp",
            status=CandidateStatus.GENERATED,
            created_at_utc="2026-07-28T00:00:00Z",
        )
        report = self.root / "ppa/csynth/solution/syn/report"
        report.mkdir(parents=True)
        shutil.copyfile(
            FIXTURES / "vitis_hls_2023_2_csynth.xml",
            report / "top_csynth.xml",
        )
        self.identity = cache_identity(source_sha)
        self.request = CandidateQualificationRequest(
            qualification_id="qual-1",
            candidate=self.candidate,
            source_path=self.source,
            ppa_work_dir=self.root / "ppa",
            top_function="top",
            cache_identity=self.identity,
            resource_limits=resource_limits or {},
        )
        task = TaskSpec(
            task_id="task",
            kernel_path=str(self.source),
            kernel_name="top",
            mode=RunMode.OPTIMIZE,
        )
        self.budget = BudgetManager(BudgetLimits(max_tool_calls=100))
        self.trace = TraceRecorder("run", task_id="task")
        self.context = RunContext(
            run_id="run",
            task=task,
            budget=self.budget,
            trace=self.trace,
        )
        self.cache = cache

    def run(self, reports=None, *, order=None):
        reports = reports or {}
        order_log = order if order is not None else []

        def handler(stage):
            def invoke(_context):
                order_log.append(stage.value)
                value = reports.get(stage)
                if isinstance(value, Exception):
                    raise value
                if value is not None:
                    return value
                return pass_report(
                    stage.value,
                    hidden=stage is QualificationStage.HIDDEN,
                )

            return invoke

        orchestrator = Stage3QualificationOrchestrator(
            {
                QualificationStage.PREFLIGHT: handler(
                    QualificationStage.PREFLIGHT
                ),
                QualificationStage.PUBLIC: handler(QualificationStage.PUBLIC),
                QualificationStage.CSYNTH: handler(QualificationStage.CSYNTH),
                QualificationStage.HIDDEN: handler(QualificationStage.HIDDEN),
            },
            cache=self.cache,
        )
        return orchestrator.run(self.context, self.request), order_log


class QualificationOrchestratorTests(unittest.TestCase):
    def test_frozen_stage_order(self):
        harness = QualificationHarness(self)
        result, order = harness.run()
        self.assertTrue(result.accepted)
        self.assertEqual(order, ["preflight", "public", "csynth", "hidden"])
        self.assertEqual(
            [item.stage.value for item in result.steps],
            ["source", "preflight", "public", "csynth", "hidden", "ppa", "feasibility"],
        )

    def test_preflight_failure_stops_before_public_and_csynth(self):
        harness = QualificationHarness(self)
        result, order = harness.run(
            {QualificationStage.PREFLIGHT: fail_report("preflight")}
        )
        self.assertEqual(result.status, QualificationStatus.REJECTED)
        self.assertEqual(order, ["preflight"])
        self.assertFalse(result.synthesis_passed)

    def test_public_failure_stops_before_csynth(self):
        harness = QualificationHarness(self)
        result, order = harness.run(
            {QualificationStage.PUBLIC: fail_report("public")}
        )
        self.assertEqual(result.status, QualificationStatus.REJECTED)
        self.assertEqual(order, ["preflight", "public"])

    def test_csynth_failure_stops_before_hidden(self):
        harness = QualificationHarness(self)
        result, order = harness.run(
            {QualificationStage.CSYNTH: fail_report("csynth")}
        )
        self.assertEqual(result.status, QualificationStatus.REJECTED)
        self.assertEqual(order, ["preflight", "public", "csynth"])

    def test_hidden_failure_rejects_without_leakage(self):
        harness = QualificationHarness(self)
        result, _ = harness.run(
            {
                QualificationStage.HIDDEN: fail_report(
                    "hidden",
                    hidden=True,
                )
            }
        )
        payload = json.dumps(result.to_dict(), sort_keys=True)
        self.assertEqual(result.status, QualificationStatus.REJECTED)
        self.assertNotIn("SECRET_REPORT_ID", payload)
        self.assertNotIn("HIDDEN_DETAIL_MUST_NOT_LEAK", payload)
        self.assertNotIn("/private/hidden.cpp", payload)
        hidden = next(
            item for item in result.steps if item.stage is QualificationStage.HIDDEN
        )
        self.assertIsNone(hidden.source_report_id)

    def test_budget_failure_blocks(self):
        harness = QualificationHarness(self)
        result, _ = harness.run(
            {
                QualificationStage.PUBLIC: fail_report(
                    "public",
                    owner=FeedbackOwner.EVALUATOR,
                    category=FeedbackCategory.BUDGET_EXHAUSTED,
                )
            }
        )
        self.assertEqual(result.status, QualificationStatus.BLOCKED)

    def test_toolchain_failure_blocks(self):
        harness = QualificationHarness(self)
        result, _ = harness.run(
            {
                QualificationStage.CSYNTH: fail_report(
                    "csynth",
                    owner=FeedbackOwner.TOOLCHAIN,
                    category=FeedbackCategory.TOOLCHAIN_FAILURE,
                )
            }
        )
        self.assertEqual(result.status, QualificationStatus.BLOCKED)

    def test_unknown_failure_requires_review(self):
        harness = QualificationHarness(self)
        result, _ = harness.run(
            {
                QualificationStage.CSYNTH: fail_report(
                    "csynth",
                    owner=FeedbackOwner.UNKNOWN,
                    category=FeedbackCategory.UNKNOWN,
                )
            }
        )
        self.assertEqual(result.status, QualificationStatus.REVIEW_REQUIRED)

    def test_handler_exception_is_safe_error(self):
        harness = QualificationHarness(self)
        result, _ = harness.run(
            {QualificationStage.PUBLIC: RuntimeError("PRIVATE_PATH_SECRET")}
        )
        payload = json.dumps(result.to_dict(), sort_keys=True)
        self.assertEqual(result.status, QualificationStatus.ERROR)
        self.assertNotIn("PRIVATE_PATH_SECRET", payload)
        self.assertIn("RuntimeError", payload)

    def test_missing_ppa_requires_review(self):
        harness = QualificationHarness(self)
        shutil.rmtree(harness.root / "ppa/csynth")
        result, _ = harness.run()
        self.assertEqual(result.status, QualificationStatus.REVIEW_REQUIRED)
        self.assertTrue(result.correctness_passed)
        self.assertTrue(result.synthesis_passed)

    def test_resource_infeasible_is_still_accepted_correct(self):
        harness = QualificationHarness(self, resource_limits={"max_lut": 800})
        result, _ = harness.run()
        self.assertEqual(result.status, QualificationStatus.ACCEPTED)
        self.assertFalse(result.objective_feasible)
        self.assertTrue(result.correctness_passed)

    def test_apply_accepted_result_to_baseline(self):
        harness = QualificationHarness(self)
        result, _ = harness.run()
        baseline = result.apply_to_candidate(harness.candidate)
        self.assertEqual(baseline.status, CandidateStatus.ACCEPTED)
        self.assertTrue(baseline.correctness["passed"])
        self.assertTrue(baseline.synthesis["passed"])
        self.assertEqual(baseline.ppa["latency_cycles_max"], 112)

    def test_apply_rejected_result_to_baseline(self):
        harness = QualificationHarness(self)
        result, _ = harness.run(
            {QualificationStage.PUBLIC: fail_report("public")}
        )
        baseline = result.apply_to_candidate(harness.candidate)
        self.assertEqual(baseline.status, CandidateStatus.REJECTED)

    def test_initialize_best_correct_and_best_ppa(self):
        harness = QualificationHarness(self)
        result, _ = harness.run()
        baseline = result.apply_to_candidate(harness.candidate)
        state = initialize_qualified_baseline(
            OptimizerState.initial(run_id="run"),
            baseline,
            result,
        )
        self.assertEqual(state.best_correct_candidate_id, "baseline")
        self.assertEqual(state.best_ppa_candidate_id, "baseline")

    def test_infeasible_baseline_has_best_correct_not_best_ppa(self):
        harness = QualificationHarness(self, resource_limits={"max_lut": 800})
        result, _ = harness.run()
        baseline = result.apply_to_candidate(harness.candidate)
        state = initialize_qualified_baseline(
            OptimizerState.initial(run_id="run"),
            baseline,
            result,
        )
        self.assertEqual(state.best_correct_candidate_id, "baseline")
        self.assertIsNone(state.best_ppa_candidate_id)

    def test_rejected_baseline_sets_terminal_status(self):
        harness = QualificationHarness(self)
        result, _ = harness.run(
            {QualificationStage.PUBLIC: fail_report("public")}
        )
        baseline = result.apply_to_candidate(harness.candidate)
        state = initialize_qualified_baseline(
            OptimizerState.initial(run_id="run"),
            baseline,
            result,
        )
        self.assertEqual(state.terminal_status.value, "baseline_rejected")

    def test_result_round_trip(self):
        harness = QualificationHarness(self)
        original, _ = harness.run()
        restored = CandidateQualificationResult.from_dict(original.to_dict())
        self.assertEqual(restored, original)

    def test_unknown_result_field_rejected(self):
        harness = QualificationHarness(self)
        result, _ = harness.run()
        payload = result.to_dict()
        payload["unexpected"] = True
        with self.assertRaises(ValueError):
            CandidateQualificationResult.from_dict(payload)

    def test_source_hash_mismatch_rejected_before_handlers(self):
        harness = QualificationHarness(self)
        harness.source.write_text("changed\n", encoding="utf-8")
        result, order = harness.run()
        self.assertEqual(result.status, QualificationStatus.REJECTED)
        self.assertEqual(order, [])

    def test_candidate_budget_snapshot_accepts_observed_tokens(self):
        harness = QualificationHarness(self)
        result, _ = harness.run()
        payload = result.to_dict()
        payload["budget_after"] = {
            "llm_calls": 0,
            "tool_calls": 4,
            "compile_calls": 1,
            "csim_calls": 2,
            "csynth_calls": 1,
            "tokens": 0,
            "cost_usd": 0.0,
            "elapsed_s": 1.0,
            "costs_by_currency": {},
        }
        adjusted = CandidateQualificationResult.from_dict(payload)
        candidate = adjusted.apply_to_candidate(harness.candidate)
        self.assertEqual(candidate.budget_after["tokens"], 0)

    def test_candidate_budget_snapshot_rejects_unknown_fields(self):
        harness = QualificationHarness(self)
        result, _ = harness.run()
        payload = result.to_dict()
        payload["budget_after"] = {"api_key": "secret"}
        adjusted = CandidateQualificationResult.from_dict(payload)
        with self.assertRaises(ValueError):
            adjusted.apply_to_candidate(harness.candidate)

        payload["budget_after"] = {
            "tokens": 0,
            "costs_by_currency": {"secret": "must-not-persist"},
        }
        adjusted = CandidateQualificationResult.from_dict(payload)
        with self.assertRaises(ValueError):
            adjusted.apply_to_candidate(harness.candidate)

        payload["budget_after"] = {"tokens": "zero"}
        adjusted = CandidateQualificationResult.from_dict(payload)
        with self.assertRaises(ValueError):
            adjusted.apply_to_candidate(harness.candidate)

    def test_candidate_must_start_generated(self):
        harness = QualificationHarness(self)
        validating = harness.candidate.transition_to(CandidateStatus.VALIDATING)
        with self.assertRaises(ValueError):
            CandidateQualificationRequest(
                qualification_id="qual-2",
                candidate=validating,
                source_path=harness.source,
                ppa_work_dir=harness.root / "ppa",
                top_function="top",
                cache_identity=harness.identity,
            )

    def test_required_handler_missing_rejected(self):
        harness = QualificationHarness(self)
        orchestrator = Stage3QualificationOrchestrator(
            {QualificationStage.PREFLIGHT: lambda _ctx: pass_report("preflight")}
        )
        with self.assertRaises(ValueError):
            orchestrator.run(harness.context, harness.request)

    def test_cache_hit_launches_no_handlers_and_uses_no_budget(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            cache = QualificationEvidenceCache(cache_dir)
            first = QualificationHarness(self, cache=cache)
            result, order = first.run()
            self.assertTrue(result.accepted)
            self.assertEqual(order, ["preflight", "public", "csynth", "hidden"])

            second = QualificationHarness(self, cache=cache)
            before = second.budget.snapshot().to_dict()
            cached, second_order = second.run()
            after = second.budget.snapshot().to_dict()
            self.assertTrue(cached.cache_hit)
            self.assertEqual(second_order, [])
            self.assertEqual(before["tool_calls"], after["tool_calls"])
            self.assertEqual(before["compile_calls"], after["compile_calls"])
            self.assertEqual(before["csim_calls"], after["csim_calls"])
            self.assertEqual(before["csynth_calls"], after["csynth_calls"])

    def test_cache_miss_on_source_change(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            cache = QualificationEvidenceCache(cache_dir)
            first = QualificationHarness(self, cache=cache)
            first.run()
            second = QualificationHarness(self, cache=cache)
            second.source.write_text(
                "extern \"C\" void top(int *a){a[0]+=2;}\n",
                encoding="utf-8",
            )
            import hashlib

            new_sha = hashlib.sha256(second.source.read_bytes()).hexdigest()
            second.candidate = CandidateRecord(
                candidate_id="baseline",
                sequence=0,
                parent_candidate_id=None,
                hypothesis_id=None,
                level=None,
                source_sha256=new_sha,
                source_artifact="candidates/baseline/source.cpp",
                status=CandidateStatus.GENERATED,
                created_at_utc="2026-07-28T00:00:00Z",
            )
            second.identity = cache_identity(new_sha)
            second.request = CandidateQualificationRequest(
                qualification_id="qual-2",
                candidate=second.candidate,
                source_path=second.source,
                ppa_work_dir=second.root / "ppa",
                top_function="top",
                cache_identity=second.identity,
            )
            result, order = second.run()
            self.assertFalse(result.cache_hit)
            self.assertTrue(order)

    def test_trace_records_cache_hit(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            cache = QualificationEvidenceCache(cache_dir)
            first = QualificationHarness(self, cache=cache)
            first.run()
            second = QualificationHarness(self, cache=cache)
            second.run()
            events = [event.event for event in second.trace.events]
            self.assertIn("optimizer.qualification.cache_hit", events)


    def test_rejected_baseline_checkpoint_has_no_best_projection(self):
        harness = QualificationHarness(self)
        result, _ = harness.run(
            {QualificationStage.PUBLIC: fail_report("public")}
        )
        terminal = result.apply_to_candidate(harness.candidate)
        state = initialize_qualified_baseline(
            OptimizerState.initial(run_id="run-rejected-baseline"),
            terminal,
            result,
        )
        writer = OptimizerCheckpointWriter(harness.root / "optimizer")
        writer.write_candidate_source(terminal, harness.source.read_bytes())
        snapshot = writer.write_checkpoint(
            state,
            {"baseline": terminal},
        )
        self.assertEqual(
            snapshot.state.terminal_status.value,
            "baseline_rejected",
        )
        self.assertIsNone(snapshot.state.best_correct_candidate_id)
        self.assertIsNone(snapshot.state.best_ppa_candidate_id)
        self.assertFalse((writer.root / "best_correct.cpp").exists())
        self.assertFalse((writer.root / "best_ppa.cpp").exists())
        recovered = writer.recover_latest()
        self.assertEqual(recovered.state, snapshot.state)
        self.assertFalse((writer.root / "best_correct.cpp").exists())

    def test_no_public_suites_skips_public_handler_requirement(self):
        harness = QualificationHarness(self)
        identity = ValidationCacheIdentity.build(
            source_sha256=harness.candidate.source_sha256,
            effective_target={"profile": {"name": "target"}},
            toolchain_fingerprint_sha256="2" * 64,
            suites=(
                SuiteIdentity(
                    suite_id="hidden-final",
                    split="hidden",
                    content_sha256="4" * 64,
                ),
            ),
            compile_flags=(),
            clock_period_ns=5.0,
            device="device",
            parser_profile="vitis-hls-2023.2",
        )
        request = CandidateQualificationRequest(
            qualification_id="qual-no-public",
            candidate=harness.candidate,
            source_path=harness.source,
            ppa_work_dir=harness.root / "ppa",
            top_function="top",
            cache_identity=identity,
        )
        order = []

        def stage(name, hidden=False):
            def call(_ctx):
                order.append(name)
                return pass_report(name, hidden=hidden)
            return call

        result = Stage3QualificationOrchestrator(
            {
                QualificationStage.PREFLIGHT: stage("preflight"),
                QualificationStage.CSYNTH: stage("csynth"),
                QualificationStage.HIDDEN: stage("hidden", hidden=True),
            }
        ).run(harness.context, request)
        self.assertTrue(result.accepted)
        self.assertEqual(order, ["preflight", "csynth", "hidden"])


if __name__ == "__main__":
    unittest.main()
