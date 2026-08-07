import hashlib
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from agrefactor.evaluation import FeedbackRouteAction
from agrefactor.optimization import (
    CandidateQualificationResult,
    CandidateRecord,
    CandidateStatus,
    DeterministicOptimizerStateMachine,
    FakeCandidateExecutor,
    FakeHypothesisProvider,
    OptimizationLevel,
    OptimizerCheckpointWriter,
    OptimizerState,
    OptimizerTerminalStatus,
    QualificationStage,
    QualificationStatus,
    QualificationStepOutcome,
    QualificationStepRecord,
)
from agrefactor.runtime.budget import BudgetManager
from agrefactor.runtime.trace import TraceRecorder


FIXED_TIME = datetime(2026, 8, 7, 0, 0, tzinfo=timezone.utc)
BASELINE_SOURCE = b"int top(){return 0;}\\n"
CANDIDATE_SOURCE = b"int top(){return 1;}\\n"


def _clock():
    return FIXED_TIME


def _accepted_baseline():
    baseline = CandidateRecord(
        candidate_id="baseline",
        sequence=0,
        parent_candidate_id=None,
        hypothesis_id=None,
        level=None,
        source_sha256=hashlib.sha256(BASELINE_SOURCE).hexdigest(),
        source_artifact="candidates/baseline/source.cpp",
        status=CandidateStatus.ACCEPTED,
    )
    state = OptimizerState.initial(
        run_id="run-p4-0f-p0-budget-terminal"
    ).with_qualified_baseline(baseline)
    return state, baseline


def _blocked_qualification(*, route_action, reason_codes):
    step = QualificationStepRecord(
        stage=QualificationStage.PREFLIGHT,
        outcome=QualificationStepOutcome.BLOCKED,
        evidence_view="agent_safe",
        route_action=route_action,
        source="p0_fixture",
        source_report_id="p0-fixture-report",
        source_item_count=1,
        source_blocking=True,
        reason_codes=tuple(reason_codes),
        metadata={"physical_execution": False},
    )
    return CandidateQualificationResult(
        qualification_id="qual-cand-1",
        candidate_id="cand-1",
        status=QualificationStatus.BLOCKED,
        steps=(step,),
        correctness_passed=False,
        synthesis_passed=False,
        objective_feasible=None,
        ppa=None,
        cache_key_sha256="a" * 64,
        cache_hit=False,
        budget_before={},
        budget_after={},
        decision={
            "schema_version": 1,
            "candidate_id": "cand-1",
            "decision": "block",
            "reason_codes": list(reason_codes),
        },
    )


def _blocked_candidate(qualification):
    generated = CandidateRecord(
        candidate_id="cand-1",
        sequence=1,
        parent_candidate_id="baseline",
        hypothesis_id="hyp-structural-r1-1",
        level=OptimizationLevel.STRUCTURAL,
        source_sha256=hashlib.sha256(CANDIDATE_SOURCE).hexdigest(),
        source_artifact="candidates/cand-1/source.cpp",
        status=CandidateStatus.GENERATED,
    )
    return qualification.apply_to_candidate(generated)


class P4_0F_P0_BudgetTerminalPolicyTests(unittest.TestCase):
    def _engine(self):
        state, baseline = _accepted_baseline()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        writer = OptimizerCheckpointWriter(root)
        writer.write_candidate_source(baseline, BASELINE_SOURCE)
        return DeterministicOptimizerStateMachine(
            state=state,
            candidates={"baseline": baseline},
            checkpoint_writer=writer,
            provider=FakeHypothesisProvider(),
            executor=FakeCandidateExecutor(),
            budget=BudgetManager(clock=lambda: 0.0),
            trace=TraceRecorder(
                "run-p4-0f-p0-budget-terminal",
                clock=_clock,
            ),
            clock=_clock,
            resume=False,
        )

    def test_typed_budget_block_preserves_best_correct_terminal(self):
        qualification = _blocked_qualification(
            route_action=FeedbackRouteAction.STOP_BUDGET_EXHAUSTED,
            reason_codes=("budget_exhausted",),
        )
        engine = self._engine()
        updates, decision = engine._decide_candidate(
            _blocked_candidate(qualification),
            qualification,
            level=OptimizationLevel.STRUCTURAL,
            round_number=1,
        )
        self.assertEqual(
            updates["terminal_status"],
            OptimizerTerminalStatus.BUDGET_EXHAUSTED_WITH_BEST_CORRECT,
        )
        self.assertEqual(updates["current_candidate_id"], "baseline")
        self.assertEqual(
            decision["optimizer_action"],
            "stop_budget_exhausted_with_best_correct",
        )
        self.assertEqual(
            decision["optimizer_reason"],
            "qualification_budget_exhausted_with_best_correct",
        )
        self.assertEqual(engine.state.best_correct_candidate_id, "baseline")
        self.assertEqual(engine.counters.blocked_candidates, 1)

    def test_non_budget_block_remains_blocked(self):
        qualification = _blocked_qualification(
            route_action=FeedbackRouteAction.FIX_TOOLCHAIN,
            reason_codes=("fix_toolchain",),
        )
        engine = self._engine()
        updates, decision = engine._decide_candidate(
            _blocked_candidate(qualification),
            qualification,
            level=OptimizationLevel.STRUCTURAL,
            round_number=1,
        )
        self.assertEqual(
            updates["terminal_status"],
            OptimizerTerminalStatus.BLOCKED,
        )
        self.assertEqual(updates["current_candidate_id"], "baseline")
        self.assertEqual(decision["optimizer_action"], "stop_blocked")
        self.assertEqual(
            decision["optimizer_reason"],
            "qualification_blocked",
        )

    def test_mismatched_budget_like_evidence_is_unknown_safe_blocked(self):
        qualification = _blocked_qualification(
            route_action=FeedbackRouteAction.FIX_TOOLCHAIN,
            reason_codes=("budget_exhausted",),
        )
        engine = self._engine()
        updates, decision = engine._decide_candidate(
            _blocked_candidate(qualification),
            qualification,
            level=OptimizationLevel.STRUCTURAL,
            round_number=1,
        )
        self.assertEqual(
            updates["terminal_status"],
            OptimizerTerminalStatus.BLOCKED,
        )
        self.assertEqual(decision["optimizer_action"], "stop_blocked")


if __name__ == "__main__":
    unittest.main()
