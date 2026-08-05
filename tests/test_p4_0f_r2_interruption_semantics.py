from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from agrefactor.optimization import (
    BudgetIncrement,
    CandidateRecord,
    CandidateStatus,
    DeterministicOptimizerStateMachine,
    FakeCandidateExecutor,
    FakeExecutionOutcome,
    FakeHypothesisProvider,
    OptimizationLevel,
    OptimizerCheckpointWriter,
    OptimizerState,
    OptimizerTerminalStatus,
    PpaEvidence,
    PpaReportFormat,
    PpaResourceUsage,
)
from agrefactor.product.run_output import (
    _failed_stage,
    _optimizer_decision_event,
)
from agrefactor.runtime import RunPhase
from agrefactor.runtime.budget import BudgetManager
from agrefactor.runtime.trace import TraceRecorder


FIXED_TIME = datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc)
BASELINE_SOURCE = b"int top(){return 0;}\n"
CONTEXT = "a" * 64


def fixed_clock():
    return FIXED_TIME


def ppa(candidate_id: str, latency: int) -> PpaEvidence:
    return PpaEvidence(
        evidence_id=f"ppa-{candidate_id}",
        parser_profile="p4-0f-r2-fixture",
        report_format=PpaReportFormat.XML,
        report_relative_path=f"fake_reports/{candidate_id}.xml",
        report_sha256=hashlib.sha256(candidate_id.encode()).hexdigest(),
        comparison_context_identity_sha256=CONTEXT,
        latency_cycles_min=latency,
        latency_cycles_max=latency,
        initiation_interval_min=1,
        initiation_interval_max=1,
        target_clock_period_ns=5.0,
        achieved_clock_period_ns=4.0,
        resources_used=PpaResourceUsage(
            bram_18k=1,
            dsp=1,
            ff=10,
            lut=10,
            uram=0,
        ),
        resources_available=PpaResourceUsage(
            bram_18k=100,
            dsp=100,
            ff=1000,
            lut=1000,
            uram=10,
        ),
        max_resource_utilization_ratio=0.10,
        objective_feasible=True,
        constraint_violations=(),
        parser_warnings=("deterministic_fixture_only",),
    )


def accepted_baseline() -> tuple[OptimizerState, CandidateRecord]:
    record = CandidateRecord(
        candidate_id="baseline",
        sequence=0,
        parent_candidate_id=None,
        hypothesis_id=None,
        level=None,
        source_sha256=hashlib.sha256(BASELINE_SOURCE).hexdigest(),
        source_artifact="candidates/baseline/source.cpp",
        status=CandidateStatus.ACCEPTED,
        ppa=ppa("baseline", 100).to_dict(),
    )
    state = OptimizerState.initial(run_id="p4-0f-r2-test").with_qualified_baseline(
        record
    )
    from dataclasses import replace

    return replace(state, best_ppa_candidate_id="baseline"), record


class RaisingProvider:
    name = "raising-provider"
    uses_network = True

    def __init__(self, *, raise_on: int):
        self._delegate = FakeHypothesisProvider()
        self._raise_on = raise_on
        self.calls = 0

    @property
    def budget_increment(self) -> BudgetIncrement:
        return self._delegate.budget_increment

    def propose(self, request):
        self.calls += 1
        if self.calls == self._raise_on:
            raise RuntimeError("provider_fixture_failure")
        return self._delegate.propose(request)


class RaisingExecutor:
    name = "raising-executor"
    uses_vitis = True

    def __init__(self, *, raise_on: int, first_latency: int = 90):
        self._delegate = FakeCandidateExecutor(
            {1: FakeExecutionOutcome(latency_cycles_max=first_latency)}
        )
        self._raise_on = raise_on
        self.calls = 0

    @property
    def budget_increment(self) -> BudgetIncrement:
        return self._delegate.budget_increment

    def execute(self, request):
        self.calls += 1
        if self.calls == self._raise_on:
            raise RuntimeError("executor_fixture_failure")
        return self._delegate.execute(request)


class Harness:
    def __init__(self, testcase, *, provider, executor):
        state, baseline = accepted_baseline()
        self.temporary = tempfile.TemporaryDirectory()
        testcase.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.writer = OptimizerCheckpointWriter(self.root)
        self.writer.write_candidate_source(baseline, BASELINE_SOURCE)
        self.trace = TraceRecorder("p4-0f-r2-test", clock=fixed_clock)
        self.engine = DeterministicOptimizerStateMachine(
            state=state,
            candidates={"baseline": baseline},
            checkpoint_writer=self.writer,
            provider=provider,
            executor=executor,
            budget=BudgetManager(clock=lambda: 0.0),
            trace=self.trace,
            clock=fixed_clock,
            resume=True,
        )

    def decisions(self) -> list[dict]:
        paths = list(self.root.rglob("decisions.jsonl"))
        if len(paths) != 1:
            raise AssertionError(f"expected one decisions.jsonl, got {paths}")
        return [
            json.loads(line)
            for line in paths[0].read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


class OptimizerSearchInterruptionTests(unittest.TestCase):
    def test_provider_failure_before_any_improvement_remains_error(self):
        harness = Harness(
            self,
            provider=RaisingProvider(raise_on=1),
            executor=FakeCandidateExecutor(),
        )
        result = harness.engine.run()
        self.assertEqual(result.terminal_status, OptimizerTerminalStatus.ERROR)
        self.assertEqual(result.state.best_ppa_candidate_id, "baseline")
        self.assertFalse(
            any(
                item.get("event") == "search_interrupted_with_best"
                for item in harness.decisions()
            )
        )

    def test_provider_failure_after_verified_improvement_preserves_best(self):
        harness = Harness(
            self,
            provider=RaisingProvider(raise_on=2),
            executor=FakeCandidateExecutor(
                {1: FakeExecutionOutcome(latency_cycles_max=90)}
            ),
        )
        result = harness.engine.run()
        self.assertEqual(
            result.terminal_status,
            OptimizerTerminalStatus.ACCEPTED_IMPROVED,
        )
        self.assertEqual(result.state.best_correct_candidate_id, "cand-1")
        self.assertEqual(result.state.best_ppa_candidate_id, "cand-1")
        self.assertEqual(result.state.current_candidate_id, "cand-1")
        interruption = [
            item
            for item in harness.decisions()
            if item.get("event") == "search_interrupted_with_best"
        ]
        self.assertEqual(len(interruption), 1)
        self.assertEqual(interruption[0]["reason"], "hypothesis_provider_error")
        self.assertEqual(interruption[0]["action"], "accept_best_so_far")
        self.assertTrue(interruption[0]["metadata"]["search_interrupted"])
        self.assertEqual(
            interruption[0]["metadata"]["preserved_candidate_id"],
            "cand-1",
        )

    def test_provider_failure_after_non_improvement_remains_error(self):
        harness = Harness(
            self,
            provider=RaisingProvider(raise_on=2),
            executor=FakeCandidateExecutor(
                {1: FakeExecutionOutcome(latency_cycles_max=110)}
            ),
        )
        result = harness.engine.run()
        self.assertEqual(result.terminal_status, OptimizerTerminalStatus.ERROR)
        self.assertEqual(result.state.best_ppa_candidate_id, "baseline")

    def test_executor_failure_before_any_improvement_remains_error(self):
        harness = Harness(
            self,
            provider=FakeHypothesisProvider(),
            executor=RaisingExecutor(raise_on=1),
        )
        result = harness.engine.run()
        self.assertEqual(result.terminal_status, OptimizerTerminalStatus.ERROR)
        self.assertEqual(result.state.best_ppa_candidate_id, "baseline")

    def test_executor_failure_after_verified_improvement_preserves_best(self):
        harness = Harness(
            self,
            provider=FakeHypothesisProvider(),
            executor=RaisingExecutor(raise_on=2, first_latency=90),
        )
        result = harness.engine.run()
        self.assertEqual(
            result.terminal_status,
            OptimizerTerminalStatus.ACCEPTED_IMPROVED,
        )
        self.assertEqual(result.state.best_correct_candidate_id, "cand-1")
        self.assertEqual(result.state.best_ppa_candidate_id, "cand-1")
        interruption = [
            item
            for item in harness.decisions()
            if item.get("event") == "search_interrupted_with_best"
        ]
        self.assertEqual(len(interruption), 1)
        self.assertEqual(interruption[0]["reason"], "candidate_executor_error")
        self.assertEqual(
            interruption[0]["metadata"]["error_type_or_code"],
            "RuntimeError",
        )


class ProductSummaryTruthfulnessTests(unittest.TestCase):
    def test_optimize_phase_error_is_not_mislabeled_refactor(self):
        phase = SimpleNamespace(phase=RunPhase.OPTIMIZE)
        self.assertEqual(_failed_stage({}, {}, phase=phase), "optimize")

    def test_refactor_phase_error_remains_refactor(self):
        phase = SimpleNamespace(phase=RunPhase.REFACTOR)
        self.assertEqual(_failed_stage({}, {}, phase=phase), "refactor")

    def test_unknown_without_phase_is_unknown_safe(self):
        self.assertEqual(_failed_stage({}, {}, phase=None), "unknown")

    def test_validation_failure_precedes_phase_fallback(self):
        identity = {
            "suites": [
                {
                    "split": "public",
                    "evaluation_status": "failed",
                }
            ]
        }
        phase = SimpleNamespace(phase=RunPhase.OPTIMIZE)
        self.assertEqual(_failed_stage(identity, {}, phase=phase), "public")

    def test_typed_interruption_is_read_from_decision_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "optimize" / "optimizer" / "decisions.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "event": "search_interrupted_with_best",
                        "action": "accept_best_so_far",
                        "reason": "hypothesis_provider_error",
                        "metadata": {
                            "search_interrupted": True,
                            "interruption_stage": "optimize",
                            "error_type_or_code": "RuntimeError",
                            "preserved_candidate_id": "cand-1",
                            "best_correct_candidate_id": "cand-1",
                            "best_ppa_candidate_id": "cand-1",
                            "unsafe_extra": "must_not_escape",
                        },
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            value = _optimizer_decision_event(
                root,
                "search_interrupted_with_best",
            )
            self.assertEqual(value["reason"], "hypothesis_provider_error")
            self.assertEqual(value["preserved_candidate_id"], "cand-1")
            self.assertNotIn("unsafe_extra", value)


if __name__ == "__main__":
    unittest.main()
