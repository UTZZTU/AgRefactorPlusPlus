import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from agrefactor.optimization import (
    BudgetIncrement,
    CandidateExecutionResult,
    CandidateGenerationAbstained,
    CandidateRecord,
    CandidateStatus,
    DeterministicOptimizerStateMachine,
    FakeCandidateExecutor,
    FakeExecutionOutcome,
    FakeExecutionStatus,
    FakeHypothesisProvider,
    HypothesisGenerationAbstained,
    HypothesisRecord,
    HypothesisRequest,
    HypothesisRisk,
    OptimizationLevel,
    OptimizerCheckpointWriter,
    OptimizerState,
    OptimizerTerminalStatus,
    PpaEvidence,
    PpaReportFormat,
    PpaResourceUsage,
    SafeOptimizerPolicy,
)
from agrefactor.runtime.budget import BudgetLimits, BudgetManager
from agrefactor.runtime.trace import TraceRecorder


FIXED_TIME = datetime(2026, 7, 31, 0, 0, tzinfo=timezone.utc)
CONTEXT = "a" * 64
BASELINE_SOURCE = b"int top(){return 0;}\n"


def fixed_clock():
    return FIXED_TIME


def ppa(
    candidate_id="baseline",
    *,
    latency=100,
    feasible=True,
    context=CONTEXT,
):
    return PpaEvidence(
        evidence_id=f"ppa-{candidate_id}",
        parser_profile="fake-s3.3",
        report_format=PpaReportFormat.XML,
        report_relative_path=f"fake_reports/{candidate_id}.xml",
        report_sha256=hashlib.sha256(candidate_id.encode()).hexdigest(),
        comparison_context_identity_sha256=context,
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
        max_resource_utilization_ratio=0.10,
        objective_feasible=feasible,
        constraint_violations=(
            () if feasible is not False else ("fixture_limit",)
        ),
        parser_warnings=("deterministic_fixture_only",),
    )


def accepted_baseline(*, feasible=True, latency=100):
    record = CandidateRecord(
        candidate_id="baseline",
        sequence=0,
        parent_candidate_id=None,
        hypothesis_id=None,
        level=None,
        source_sha256=hashlib.sha256(BASELINE_SOURCE).hexdigest(),
        source_artifact="candidates/baseline/source.cpp",
        status=CandidateStatus.ACCEPTED,
        ppa=ppa(feasible=feasible, latency=latency).to_dict(),
    )
    state = OptimizerState.initial(run_id="run-s33").with_qualified_baseline(
        record
    )
    if feasible is True:
        state = replace(state, best_ppa_candidate_id="baseline")
    return state, record


def rejected_baseline():
    record = CandidateRecord(
        candidate_id="baseline",
        sequence=0,
        parent_candidate_id=None,
        hypothesis_id=None,
        level=None,
        source_sha256=hashlib.sha256(BASELINE_SOURCE).hexdigest(),
        source_artifact="candidates/baseline/source.cpp",
        status=CandidateStatus.REJECTED,
        decision={"action": "baseline_rejected"},
    )
    state = replace(
        OptimizerState.initial(run_id="run-s33"),
        terminal_status=OptimizerTerminalStatus.BASELINE_REJECTED,
    )
    return state, record


def valid_hypothesis(
    *,
    level=OptimizationLevel.STRUCTURAL,
    round_number=1,
    index=1,
    parent="baseline",
    evidence=(),
    claim="safe hypothesis",
):
    if level is OptimizationLevel.BOTTLENECK and not evidence:
        evidence = ("ppa-baseline",)
    return HypothesisRecord(
        hypothesis_id=f"hyp-{level.value}-r{round_number}-{index}",
        level=level,
        parent_candidate_id=parent,
        claim=claim,
        supporting_evidence_ids=tuple(evidence),
        expected_benefit={"metric": "latency", "direction": "decrease"},
        risk=HypothesisRisk.LOW,
        modification_scope=("candidate_source",),
        verification_plan=("preflight", "public", "csynth", "hidden"),
        model_identity={"provider": "fixture", "network": False},
        prompt_identity_sha256="c" * 64,
    )


class Harness:
    def __init__(
        self,
        testcase,
        *,
        state=None,
        baseline=None,
        provider=None,
        executor=None,
        budget=None,
        root=None,
        resume=True,
    ):
        self.testcase = testcase
        if state is None or baseline is None:
            state, baseline = accepted_baseline()
        self.state = state
        self.baseline = baseline
        self.temporary = None
        if root is None:
            self.temporary = tempfile.TemporaryDirectory()
            testcase.addCleanup(self.temporary.cleanup)
            root = self.temporary.name
        self.root = Path(root)
        self.writer = OptimizerCheckpointWriter(self.root)
        source_path = self.root / baseline.source_artifact
        if not source_path.exists():
            self.writer.write_candidate_source(baseline, BASELINE_SOURCE)
        self.provider = provider or FakeHypothesisProvider()
        self.executor = executor or FakeCandidateExecutor()
        self.budget = budget or BudgetManager(clock=lambda: 0.0)
        self.trace = TraceRecorder("run-s33", clock=fixed_clock)
        self.engine = DeterministicOptimizerStateMachine(
            state=state,
            candidates={"baseline": baseline},
            checkpoint_writer=self.writer,
            provider=self.provider,
            executor=self.executor,
            budget=self.budget,
            trace=self.trace,
            clock=fixed_clock,
            resume=resume,
        )


class SafeOptimizerPolicyTests(unittest.TestCase):
    def test_safe_v1_exact_limits(self):
        policy = SafeOptimizerPolicy.safe_v1()
        self.assertEqual(policy.level_order, (
            OptimizationLevel.STRUCTURAL,
            OptimizationLevel.BOTTLENECK,
            OptimizationLevel.PRAGMA,
        ))
        self.assertEqual(
            [policy.for_level(level).max_rounds for level in policy.level_order],
            [2, 2, 3],
        )
        self.assertTrue(all(
            policy.for_level(level).hypotheses_per_round == 3
            for level in policy.level_order
        ))
        self.assertEqual(policy.max_executed_candidates, 7)
        self.assertEqual(policy.candidate_correctness_repair_attempts, 0)

    def test_policy_rejects_other_name(self):
        with self.assertRaises(ValueError):
            SafeOptimizerPolicy(name="unsafe")

    def test_policy_rejects_other_objective(self):
        with self.assertRaises(ValueError):
            SafeOptimizerPolicy(objective="area")

    def test_budget_increment_rejects_negative(self):
        with self.assertRaises(ValueError):
            BudgetIncrement(llm_calls=-1)


class FakeProviderExecutorTests(unittest.TestCase):
    def test_default_provider_is_deterministic_and_bounded(self):
        state, baseline = accepted_baseline()
        request = HypothesisRequest(
            run_id=state.run_id,
            level=OptimizationLevel.STRUCTURAL,
            round_number=1,
            parent_candidate=baseline,
            max_hypotheses=3,
        )
        first = FakeHypothesisProvider().propose(request)
        second = FakeHypothesisProvider().propose(request)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 3)
        self.assertTrue(all(item.model_identity["network"] is False for item in first))

    def test_bottleneck_default_has_evidence(self):
        _, baseline = accepted_baseline()
        request = HypothesisRequest(
            run_id="run-s33",
            level=OptimizationLevel.BOTTLENECK,
            round_number=1,
            parent_candidate=baseline,
            max_hypotheses=3,
            supporting_evidence_ids=("ppa-baseline",),
        )
        values = FakeHypothesisProvider().propose(request)
        self.assertEqual(values[0].supporting_evidence_ids, ("ppa-baseline",))

    def test_explicit_empty_fixture(self):
        provider = FakeHypothesisProvider(
            {(OptimizationLevel.STRUCTURAL, 1): None}
        )
        _, baseline = accepted_baseline()
        request = HypothesisRequest(
            run_id="run-s33",
            level=OptimizationLevel.STRUCTURAL,
            round_number=1,
            parent_candidate=baseline,
            max_hypotheses=3,
        )
        self.assertEqual(tuple(provider.propose(request)), ())

    def test_agent_unsafe_context_rejected(self):
        _, baseline = accepted_baseline()
        with self.assertRaises(ValueError):
            HypothesisRequest(
                run_id="run-s33",
                level=OptimizationLevel.STRUCTURAL,
                round_number=1,
                parent_candidate=baseline,
                max_hypotheses=3,
                safe_context={"hidden_report": "no"},
            )

    def test_fake_executor_returns_s32_compatible_result(self):
        state, baseline = accepted_baseline()
        hypothesis = valid_hypothesis()
        from agrefactor.optimization import CandidateExecutionRequest
        request = CandidateExecutionRequest(
            run_id=state.run_id,
            sequence=1,
            candidate_id="cand-1",
            level=OptimizationLevel.STRUCTURAL,
            round_number=1,
            parent_candidate=baseline,
            parent_source=BASELINE_SOURCE,
            hypothesis=hypothesis,
            budget_before=BudgetManager(clock=lambda: 0.0).snapshot().to_dict(),
        )
        result = FakeCandidateExecutor().execute(request)
        self.assertEqual(result.qualification.candidate_id, "cand-1")
        self.assertTrue(result.qualification.accepted)
        self.assertIsNotNone(result.qualification.ppa)
        self.assertIn(b"cand-1", result.source)


class DeterministicOptimizerStateMachineTests(unittest.TestCase):
    def test_nonterminal_unqualified_baseline_is_rejected_at_construction(self):
        baseline = CandidateRecord(
            candidate_id="baseline",
            sequence=0,
            parent_candidate_id=None,
            hypothesis_id=None,
            level=None,
            source_sha256=hashlib.sha256(BASELINE_SOURCE).hexdigest(),
            source_artifact="candidates/baseline/source.cpp",
            status=CandidateStatus.GENERATED,
        )
        state = OptimizerState.initial(run_id="run-s33")
        with self.assertRaisesRegex(ValueError, "qualified baseline"):
            Harness(self, state=state, baseline=baseline)

    def test_rejected_baseline_launches_nothing(self):
        state, baseline = rejected_baseline()
        harness = Harness(self, state=state, baseline=baseline)
        result = harness.engine.run()
        self.assertEqual(result.terminal_status, OptimizerTerminalStatus.BASELINE_REJECTED)
        self.assertEqual(harness.provider.call_count, 0)
        self.assertEqual(harness.executor.call_count, 0)

    def test_exact_level_and_round_order(self):
        harness = Harness(self)
        result = harness.engine.run()
        self.assertEqual(result.state.executed_candidate_count, 7)
        self.assertEqual(
            [(r.level.value, r.round_number) for r in harness.provider.requests],
            [
                ("structural", 1),
                ("structural", 2),
                ("bottleneck", 1),
                ("bottleneck", 2),
                ("pragma", 1),
                ("pragma", 2),
                ("pragma", 3),
            ],
        )

    def test_three_proposed_one_executed_per_round(self):
        harness = Harness(self)
        result = harness.engine.run()
        self.assertEqual(result.counters.proposed_hypotheses, 21)
        self.assertEqual(result.counters.selected_hypotheses, 7)
        self.assertEqual(result.counters.executor_calls, 7)

    def test_candidate_ids_and_lineage_are_contiguous(self):
        harness = Harness(self)
        result = harness.engine.run()
        generated = [result.candidates[f"cand-{i}"] for i in range(1, 8)]
        self.assertEqual([c.sequence for c in generated], list(range(1, 8)))
        self.assertTrue(all(c.parent_candidate_id is not None for c in generated))
        self.assertTrue(all(c.hypothesis_id for c in generated))

    def test_improvement_updates_both_pointers(self):
        executor = FakeCandidateExecutor(
            {1: FakeExecutionOutcome(latency_cycles_max=90)}
        )
        provider = FakeHypothesisProvider({
            (OptimizationLevel.STRUCTURAL, 2): None,
            (OptimizationLevel.BOTTLENECK, 1): None,
            (OptimizationLevel.PRAGMA, 1): None,
        })
        harness = Harness(self, provider=provider, executor=executor)
        result = harness.engine.run()
        self.assertEqual(result.state.best_correct_candidate_id, "cand-1")
        self.assertEqual(result.state.best_ppa_candidate_id, "cand-1")
        self.assertEqual(result.terminal_status, OptimizerTerminalStatus.ACCEPTED_IMPROVED)

    def test_regression_keeps_baseline_and_rolls_back(self):
        executor = FakeCandidateExecutor(
            default_outcome=FakeExecutionOutcome(latency_cycles_max=110)
        )
        harness = Harness(self, executor=executor)
        result = harness.engine.run()
        self.assertEqual(result.state.best_correct_candidate_id, "baseline")
        self.assertEqual(result.state.best_ppa_candidate_id, "baseline")
        self.assertEqual(result.state.current_candidate_id, "baseline")
        self.assertEqual(result.terminal_status, OptimizerTerminalStatus.ACCEPTED_NO_IMPROVEMENT)

    def test_infeasible_baseline_can_search_and_update_best_correct(self):
        state, baseline = accepted_baseline(feasible=False)
        provider = FakeHypothesisProvider({
            (OptimizationLevel.STRUCTURAL, 2): None,
            (OptimizationLevel.BOTTLENECK, 1): None,
            (OptimizationLevel.PRAGMA, 1): None,
        })
        executor = FakeCandidateExecutor({
            1: FakeExecutionOutcome(objective_feasible=False)
        })
        harness = Harness(
            self,
            state=state,
            baseline=baseline,
            provider=provider,
            executor=executor,
        )
        result = harness.engine.run()
        self.assertEqual(result.state.best_correct_candidate_id, "cand-1")
        self.assertIsNone(result.state.best_ppa_candidate_id)
        self.assertEqual(result.terminal_status, OptimizerTerminalStatus.NO_FEASIBLE_CANDIDATE)

    def test_first_feasible_candidate_after_infeasible_baseline_wins(self):
        state, baseline = accepted_baseline(feasible=False)
        provider = FakeHypothesisProvider({
            (OptimizationLevel.STRUCTURAL, 2): None,
            (OptimizationLevel.BOTTLENECK, 1): None,
            (OptimizationLevel.PRAGMA, 1): None,
        })
        executor = FakeCandidateExecutor({1: FakeExecutionOutcome(latency_cycles_max=150)})
        harness = Harness(
            self,
            state=state,
            baseline=baseline,
            provider=provider,
            executor=executor,
        )
        result = harness.engine.run()
        self.assertEqual(result.state.best_ppa_candidate_id, "cand-1")
        self.assertEqual(result.terminal_status, OptimizerTerminalStatus.ACCEPTED_IMPROVED)

    def test_infeasible_candidate_does_not_replace_feasible_baseline(self):
        executor = FakeCandidateExecutor(
            default_outcome=FakeExecutionOutcome(objective_feasible=False)
        )
        harness = Harness(self, executor=executor)
        result = harness.engine.run()
        self.assertEqual(result.state.best_correct_candidate_id, "baseline")
        self.assertEqual(result.state.best_ppa_candidate_id, "baseline")

    def test_rejected_candidate_rolls_back_and_continues(self):
        executor = FakeCandidateExecutor({
            1: FakeExecutionOutcome(status=FakeExecutionStatus.REJECTED),
            2: FakeExecutionOutcome(latency_cycles_max=90),
        })
        harness = Harness(self, executor=executor)
        result = harness.engine.run()
        self.assertEqual(result.candidates["cand-1"].status, CandidateStatus.REJECTED)
        self.assertEqual(result.candidates["cand-2"].parent_candidate_id, "baseline")
        self.assertIn(result.terminal_status, {
            OptimizerTerminalStatus.ACCEPTED_IMPROVED,
            OptimizerTerminalStatus.ACCEPTED_NO_IMPROVEMENT,
        })

    def test_blocked_candidate_stops_run(self):
        executor = FakeCandidateExecutor({
            1: FakeExecutionOutcome(status=FakeExecutionStatus.BLOCKED)
        })
        harness = Harness(self, executor=executor)
        result = harness.engine.run()
        self.assertEqual(result.terminal_status, OptimizerTerminalStatus.BLOCKED)
        self.assertEqual(result.state.executed_candidate_count, 1)
        self.assertEqual(harness.executor.call_count, 1)

    def test_review_required_candidate_stops_run(self):
        executor = FakeCandidateExecutor({
            1: FakeExecutionOutcome(status=FakeExecutionStatus.REVIEW_REQUIRED)
        })
        harness = Harness(self, executor=executor)
        result = harness.engine.run()
        self.assertEqual(result.terminal_status, OptimizerTerminalStatus.REVIEW_REQUIRED)
        self.assertEqual(result.counters.review_required_candidates, 1)

    def test_error_candidate_stops_run(self):
        executor = FakeCandidateExecutor({
            1: FakeExecutionOutcome(status=FakeExecutionStatus.ERROR)
        })
        harness = Harness(self, executor=executor)
        result = harness.engine.run()
        self.assertEqual(result.terminal_status, OptimizerTerminalStatus.ERROR)

    def test_unknown_feasibility_requires_review(self):
        executor = FakeCandidateExecutor({
            1: FakeExecutionOutcome(objective_feasible=None)
        })
        harness = Harness(self, executor=executor)
        result = harness.engine.run()
        self.assertEqual(result.terminal_status, OptimizerTerminalStatus.REVIEW_REQUIRED)

    def test_context_mismatch_requires_review(self):
        executor = FakeCandidateExecutor({
            1: FakeExecutionOutcome(
                latency_cycles_max=90,
                comparison_context_identity_sha256="d" * 64,
            )
        })
        harness = Harness(self, executor=executor)
        result = harness.engine.run()
        self.assertEqual(result.terminal_status, OptimizerTerminalStatus.REVIEW_REQUIRED)

    def test_empty_response_finishes_current_level_immediately(self):
        provider = FakeHypothesisProvider({
            (OptimizationLevel.STRUCTURAL, 1): None,
            (OptimizationLevel.BOTTLENECK, 1): None,
            (OptimizationLevel.PRAGMA, 1): None,
        })
        harness = Harness(self, provider=provider)
        result = harness.engine.run()
        self.assertEqual(harness.provider.call_count, 3)
        self.assertEqual(harness.executor.call_count, 0)
        self.assertEqual(result.terminal_status, OptimizerTerminalStatus.ACCEPTED_NO_IMPROVEMENT)

    def test_malformed_first_valid_second_is_selected(self):
        valid = valid_hypothesis(index=2)
        provider = FakeHypothesisProvider({
            (OptimizationLevel.STRUCTURAL, 1): (
                {"bad": "payload"},
                valid,
            ),
            (OptimizationLevel.STRUCTURAL, 2): None,
            (OptimizationLevel.BOTTLENECK, 1): None,
            (OptimizationLevel.PRAGMA, 1): None,
        })
        harness = Harness(self, provider=provider)
        result = harness.engine.run()
        self.assertEqual(result.counters.invalid_hypotheses, 1)
        self.assertEqual(result.candidates["cand-1"].hypothesis_id, valid.hypothesis_id)

    def test_all_malformed_advances_level_without_candidate(self):
        provider = FakeHypothesisProvider({
            (OptimizationLevel.STRUCTURAL, 1): ({"bad": "payload"},),
            (OptimizationLevel.BOTTLENECK, 1): None,
            (OptimizationLevel.PRAGMA, 1): None,
        })
        harness = Harness(self, provider=provider)
        result = harness.engine.run()
        self.assertEqual(result.state.executed_candidate_count, 0)
        self.assertEqual(result.counters.invalid_hypotheses, 1)

    def test_bottleneck_without_evidence_is_rejected_before_execution(self):
        raw = valid_hypothesis(level=OptimizationLevel.BOTTLENECK).to_dict()
        raw["supporting_evidence_ids"] = []
        provider = FakeHypothesisProvider({
            (OptimizationLevel.STRUCTURAL, 1): None,
            (OptimizationLevel.BOTTLENECK, 1): (raw,),
            (OptimizationLevel.PRAGMA, 1): None,
        })
        harness = Harness(self, provider=provider)
        result = harness.engine.run()
        self.assertEqual(result.state.executed_candidate_count, 0)
        self.assertEqual(result.counters.invalid_hypotheses, 1)

    def test_hidden_like_claim_is_rejected_before_execution(self):
        raw = valid_hypothesis().to_dict()
        raw["claim"] = "use hidden diagnostic"
        provider = FakeHypothesisProvider({
            (OptimizationLevel.STRUCTURAL, 1): (raw,),
            (OptimizationLevel.BOTTLENECK, 1): None,
            (OptimizationLevel.PRAGMA, 1): None,
        })
        harness = Harness(self, provider=provider)
        result = harness.engine.run()
        self.assertEqual(result.state.executed_candidate_count, 0)
        self.assertEqual(result.counters.invalid_hypotheses, 1)

    def test_provider_budget_exhaustion_launches_no_provider(self):
        provider = FakeHypothesisProvider(
            budget_increment=BudgetIncrement(llm_calls=1)
        )
        budget = BudgetManager(BudgetLimits(max_llm_calls=0), clock=lambda: 0.0)
        harness = Harness(self, provider=provider, budget=budget)
        result = harness.engine.run()
        self.assertEqual(result.terminal_status, OptimizerTerminalStatus.BUDGET_EXHAUSTED_WITH_BEST_CORRECT)
        self.assertEqual(provider.call_count, 0)
        self.assertEqual(harness.executor.call_count, 0)

    def test_executor_budget_exhaustion_launches_no_executor(self):
        executor = FakeCandidateExecutor(
            budget_increment=BudgetIncrement(tool_calls=1)
        )
        budget = BudgetManager(BudgetLimits(max_tool_calls=0), clock=lambda: 0.0)
        harness = Harness(self, executor=executor, budget=budget)
        result = harness.engine.run()
        self.assertEqual(result.terminal_status, OptimizerTerminalStatus.BUDGET_EXHAUSTED_WITH_BEST_CORRECT)
        self.assertEqual(harness.provider.call_count, 1)
        self.assertEqual(executor.call_count, 0)

    def test_exact_budget_allows_one_then_blocks_next(self):
        provider = FakeHypothesisProvider(
            budget_increment=BudgetIncrement(llm_calls=1)
        )
        budget = BudgetManager(BudgetLimits(max_llm_calls=1), clock=lambda: 0.0)
        harness = Harness(self, provider=provider, budget=budget)
        result = harness.engine.run()
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(result.state.executed_candidate_count, 1)
        self.assertEqual(result.terminal_status, OptimizerTerminalStatus.BUDGET_EXHAUSTED_WITH_BEST_CORRECT)

    def test_default_fakes_do_not_increment_physical_budget(self):
        harness = Harness(self)
        result = harness.engine.run()
        usage = result.budget_usage
        self.assertEqual(usage["llm_calls"], 0)
        self.assertEqual(usage["tool_calls"], 0)
        self.assertEqual(usage["csynth_calls"], 0)

    def test_simulated_physical_counters_remain_separate(self):
        provider = FakeHypothesisProvider(
            budget_increment=BudgetIncrement(llm_calls=1)
        )
        executor = FakeCandidateExecutor(
            budget_increment=BudgetIncrement(tool_calls=1)
        )
        budget = BudgetManager(
            BudgetLimits(max_llm_calls=1, max_tool_calls=1),
            clock=lambda: 0.0,
        )
        harness = Harness(self, provider=provider, executor=executor, budget=budget)
        result = harness.engine.run()
        self.assertEqual(result.counters.provider_calls, 1)
        self.assertEqual(result.counters.executor_calls, 1)
        self.assertEqual(result.budget_usage["llm_calls"], 1)
        self.assertEqual(result.budget_usage["tool_calls"], 1)

    def test_checkpoint_written_for_baseline_and_each_round(self):
        harness = Harness(self)
        result = harness.engine.run()
        paths = sorted((harness.root / "checkpoints").glob("checkpoint-*.json"))
        self.assertEqual(len(paths), 8)
        self.assertEqual(result.state.checkpoint_sequence, 8)

    def test_resume_after_one_step_does_not_repeat_candidate(self):
        harness = Harness(self)
        first = harness.engine.step()
        self.assertEqual(first.state.executed_candidate_count, 1)
        self.assertEqual(harness.executor.call_count, 1)

        resumed_provider = FakeHypothesisProvider()
        resumed_executor = FakeCandidateExecutor()
        resumed = Harness(
            self,
            state=harness.state,
            baseline=harness.baseline,
            provider=resumed_provider,
            executor=resumed_executor,
            root=harness.root,
            resume=True,
        )
        result = resumed.engine.run()
        self.assertEqual(result.state.executed_candidate_count, 7)
        self.assertEqual(resumed_executor.call_count, 6)
        self.assertEqual(
            (resumed_provider.requests[0].level, resumed_provider.requests[0].round_number),
            (OptimizationLevel.STRUCTURAL, 2),
        )
        self.assertEqual(result.candidates["cand-2"].sequence, 2)

    def test_rejected_candidate_never_overwrites_best_projections(self):
        executor = FakeCandidateExecutor(
            default_outcome=FakeExecutionOutcome(status=FakeExecutionStatus.REJECTED)
        )
        harness = Harness(self, executor=executor)
        harness.engine.run()
        self.assertEqual((harness.root / "best_correct.cpp").read_bytes(), BASELINE_SOURCE)
        self.assertEqual((harness.root / "best_ppa.cpp").read_bytes(), BASELINE_SOURCE)

    def test_hypothesis_and_decision_artifacts_are_persisted(self):
        harness = Harness(self)
        result = harness.engine.step()
        hypothesis_paths = list((harness.root / "hypotheses").glob("*.json"))
        self.assertEqual(len(hypothesis_paths), 3)
        decision_lines = (harness.root / "decisions.jsonl").read_text().splitlines()
        self.assertGreaterEqual(len(decision_lines), 2)
        payload = json.loads(decision_lines[-1])
        self.assertEqual(payload["candidate_id"], "cand-1")
        self.assertEqual(payload["event"], "candidate_terminal")
        self.assertEqual(payload["level"], "structural")
        self.assertEqual(payload["round_number"], 1)
        serialized = json.dumps(payload).lower()
        self.assertNotIn("hidden_report", serialized)
        self.assertEqual(result.state.executed_candidate_count, 1)

    def test_two_runs_produce_identical_authoritative_artifacts(self):
        snapshots = []
        for _ in range(2):
            harness = Harness(self)
            harness.engine.run()
            files = {}
            for path in sorted(harness.root.rglob("*")):
                if path.is_file():
                    relative = path.relative_to(harness.root).as_posix()
                    files[relative] = path.read_bytes()
            snapshots.append(files)
        self.assertEqual(snapshots[0], snapshots[1])

    def test_maximum_candidate_count_is_seven(self):
        harness = Harness(self)
        result = harness.engine.run()
        self.assertEqual(result.state.executed_candidate_count, 7)
        self.assertNotIn("cand-8", result.candidates)

    def test_provider_exception_is_terminal_error(self):
        class RaisingProvider(FakeHypothesisProvider):
            def propose(self, request):
                raise RuntimeError("fixture provider failure")

        harness = Harness(self, provider=RaisingProvider())
        result = harness.engine.run()
        self.assertEqual(result.terminal_status, OptimizerTerminalStatus.ERROR)
        self.assertEqual(result.state.executed_candidate_count, 0)

    def test_provider_exception_consumes_declared_physical_budget(self):
        class RaisingProvider(FakeHypothesisProvider):
            def propose(self, request):
                raise RuntimeError("fixture provider failure")

        provider = RaisingProvider(
            budget_increment=BudgetIncrement(llm_calls=1)
        )
        budget = BudgetManager(
            BudgetLimits(max_llm_calls=1),
            clock=lambda: 0.0,
        )
        harness = Harness(self, provider=provider, budget=budget)
        result = harness.engine.run()
        self.assertEqual(result.terminal_status, OptimizerTerminalStatus.ERROR)
        self.assertEqual(result.counters.provider_calls, 1)
        self.assertEqual(result.budget_usage["llm_calls"], 1)
        self.assertEqual(result.state.executed_candidate_count, 0)

    def test_candidate_generation_abstention_preserves_best_correct_and_advances(self):
        class AbstainingExecutor(FakeCandidateExecutor):
            def execute(self, request):
                if request.level is OptimizationLevel.BOTTLENECK:
                    raise CandidateGenerationAbstained(
                        reason_code="candidate_response_contract_abstention",
                        error_code="CandidateResponseError",
                        detail_codes=("semantic_unchanged",),
                    )
                return super().execute(request)

        harness = Harness(self, executor=AbstainingExecutor())
        result = harness.engine.run()
        self.assertIn(result.terminal_status, {
            OptimizerTerminalStatus.ACCEPTED_IMPROVED,
            OptimizerTerminalStatus.ACCEPTED_NO_IMPROVEMENT,
        })
        self.assertEqual(result.counters.candidate_generation_abstentions, 1)
        self.assertNotEqual(result.state.terminal_status, OptimizerTerminalStatus.ERROR)
        decisions = [
            json.loads(line)
            for line in (harness.root / "decisions.jsonl").read_text().splitlines()
        ]
        abstention = next(
            item for item in decisions
            if item["event"] == "candidate_generation_abstained"
        )
        self.assertEqual(abstention["level"], "bottleneck")
        self.assertEqual(abstention["metadata"]["detail_codes"], ["semantic_unchanged"])
        self.assertIs(abstention["metadata"]["candidate_created"], False)
        self.assertIs(abstention["metadata"]["qualification_started"], False)
        self.assertIs(abstention["metadata"]["automatic_retry"], False)

    def test_hypothesis_contract_abstention_advances_without_retry(self):
        class AbstainingProvider(FakeHypothesisProvider):
            def propose(self, request):
                if request.level is OptimizationLevel.BOTTLENECK:
                    self._requests.append(request)
                    raise HypothesisGenerationAbstained(
                        reason_code="hypothesis_response_contract_abstention",
                        error_code="BottleneckModelContractError",
                        detail_codes=("analysis_response_contract_invalid",),
                    )
                return super().propose(request)

        harness = Harness(self, provider=AbstainingProvider())
        result = harness.engine.run()
        self.assertNotEqual(result.terminal_status, OptimizerTerminalStatus.ERROR)
        self.assertEqual(result.counters.hypothesis_generation_abstentions, 1)
        decisions = [
            json.loads(line)
            for line in (harness.root / "decisions.jsonl").read_text().splitlines()
        ]
        events = [item["event"] for item in decisions]
        self.assertIn("hypothesis_generation_abstained", events)
        self.assertEqual(
            sum(1 for item in decisions if item["event"] == "hypothesis_generation_abstained"),
            1,
        )

    def test_abstaining_executor_consumes_declared_budget_once(self):
        class AbstainingExecutor(FakeCandidateExecutor):
            def execute(self, request):
                raise CandidateGenerationAbstained(
                    reason_code="candidate_response_contract_abstention",
                    error_code="CandidateResponseError",
                    detail_codes=("explicit_abstention",),
                )

        executor = AbstainingExecutor(
            budget_increment=BudgetIncrement(llm_calls=1)
        )
        budget = BudgetManager(BudgetLimits(max_llm_calls=3), clock=lambda: 0.0)
        harness = Harness(self, executor=executor, budget=budget)
        result = harness.engine.run()
        self.assertEqual(result.budget_usage["llm_calls"], 3)
        self.assertEqual(result.counters.executor_calls, 3)
        self.assertEqual(result.counters.candidate_generation_abstentions, 3)
        self.assertEqual(
            result.terminal_status,
            OptimizerTerminalStatus.ACCEPTED_NO_IMPROVEMENT,
        )

    def test_executor_exception_is_terminal_error_without_candidate(self):
        class RaisingExecutor(FakeCandidateExecutor):
            def execute(self, request):
                raise RuntimeError("fixture executor failure")

        harness = Harness(self, executor=RaisingExecutor())
        result = harness.engine.run()
        self.assertEqual(result.terminal_status, OptimizerTerminalStatus.ERROR)
        self.assertEqual(result.state.executed_candidate_count, 0)
        self.assertNotIn("cand-1", result.candidates)

    def test_qualification_candidate_mismatch_is_terminal_error(self):
        class WrongLinkExecutor(FakeCandidateExecutor):
            def execute(self, request):
                result = super().execute(request)
                wrong = replace(result.qualification, candidate_id="cand-99")
                return CandidateExecutionResult(source=result.source, qualification=wrong)

        harness = Harness(self, executor=WrongLinkExecutor())
        result = harness.engine.run()
        self.assertEqual(result.terminal_status, OptimizerTerminalStatus.ERROR)
        self.assertEqual(result.state.executed_candidate_count, 0)

    def test_trace_explicitly_records_no_real_network_or_vitis(self):
        harness = Harness(self)
        harness.engine.run()
        payload = json.dumps(harness.trace.to_dict(), sort_keys=True).lower()
        self.assertIn('"real_network": false', payload)
        self.assertIn('"real_vitis": false', payload)
        self.assertNotIn("hidden_report", payload)


if __name__ == "__main__":
    unittest.main()
