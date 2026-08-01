"""Deterministic, checkpointed Stage 3 optimizer state machine.

S3.3 established the injected control plane. S3.4 preserves that deterministic
engine while allowing explicitly injected model-backed Structural components.
The product CLI remains gated and no provider/tool is imported implicitly.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from agrefactor.runtime.budget import BudgetExceededError, BudgetManager
from agrefactor.runtime.trace import TraceRecorder

from .artifacts import OptimizerArtifactStore
from .checkpoint import OptimizerCheckpointWriter
from .execution import (
    CandidateExecutionRequest,
    CandidateExecutionResult,
    CandidateExecutor,
    CandidateGenerationAbstained,
)
from .policy import BudgetIncrement, SafeOptimizerPolicy
from .ppa import LatencyPpaComparator, PpaComparisonDecision, PpaEvidence
from .provider import (
    HypothesisGenerationAbstained,
    HypothesisProvider,
    HypothesisRequest,
    normalize_hypothesis,
)
from .qualification import QualificationStatus
from .state import (
    CandidateRecord,
    CandidateStatus,
    HypothesisRecord,
    OptimizationLevel,
    OptimizerState,
    OptimizerTerminalStatus,
    normalize_candidate_index,
)


STATE_MACHINE_SCHEMA_VERSION = 2


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class OptimizerRunCounters:
    provider_calls: int = 0
    hypothesis_generation_abstentions: int = 0
    proposed_hypotheses: int = 0
    valid_hypotheses: int = 0
    invalid_hypotheses: int = 0
    selected_hypotheses: int = 0
    executor_calls: int = 0
    candidate_generation_abstentions: int = 0
    accepted_candidates: int = 0
    rejected_candidates: int = 0
    blocked_candidates: int = 0
    review_required_candidates: int = 0
    error_candidates: int = 0

    def increment(self, **changes: int) -> "OptimizerRunCounters":
        values = self.to_dict()
        for name, amount in changes.items():
            if name not in values:
                raise ValueError(f"unknown optimizer counter: {name}")
            if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                raise ValueError("counter increments must be non-negative integers")
            values[name] += amount
        return OptimizerRunCounters(**values)

    def to_dict(self) -> dict[str, int]:
        return {
            "provider_calls": self.provider_calls,
            "hypothesis_generation_abstentions": self.hypothesis_generation_abstentions,
            "proposed_hypotheses": self.proposed_hypotheses,
            "valid_hypotheses": self.valid_hypotheses,
            "invalid_hypotheses": self.invalid_hypotheses,
            "selected_hypotheses": self.selected_hypotheses,
            "executor_calls": self.executor_calls,
            "candidate_generation_abstentions": self.candidate_generation_abstentions,
            "accepted_candidates": self.accepted_candidates,
            "rejected_candidates": self.rejected_candidates,
            "blocked_candidates": self.blocked_candidates,
            "review_required_candidates": self.review_required_candidates,
            "error_candidates": self.error_candidates,
        }


@dataclass(frozen=True, slots=True)
class OptimizerRunResult:
    state: OptimizerState
    candidates: Mapping[str, CandidateRecord]
    counters: OptimizerRunCounters
    budget_usage: Mapping[str, Any]
    checkpoint_root: Path

    schema_version = STATE_MACHINE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.state, OptimizerState):
            raise TypeError("state must be OptimizerState")
        candidates = normalize_candidate_index(self.candidates)
        self.state.validate_against_candidates(candidates)
        if not isinstance(self.counters, OptimizerRunCounters):
            raise TypeError("counters must be OptimizerRunCounters")
        object.__setattr__(self, "candidates", candidates)
        object.__setattr__(self, "budget_usage", dict(self.budget_usage))
        object.__setattr__(self, "checkpoint_root", Path(self.checkpoint_root))

    @property
    def terminal_status(self) -> OptimizerTerminalStatus | None:
        return self.state.terminal_status


class DeterministicOptimizerStateMachine:
    """Run the frozen safe-v1 policy with injected deterministic components."""

    def __init__(
        self,
        *,
        state: OptimizerState,
        candidates: Mapping[str, CandidateRecord],
        checkpoint_writer: OptimizerCheckpointWriter,
        provider: HypothesisProvider,
        executor: CandidateExecutor,
        budget: BudgetManager,
        trace: TraceRecorder,
        policy: SafeOptimizerPolicy | None = None,
        comparator: LatencyPpaComparator | None = None,
        artifact_store: OptimizerArtifactStore | None = None,
        clock: Callable[[], datetime] = _utc_now,
        resume: bool = True,
    ) -> None:
        if not isinstance(state, OptimizerState):
            raise TypeError("state must be OptimizerState")
        index = normalize_candidate_index(candidates)
        state.validate_against_candidates(index)
        if (
            state.terminal_status is None
            and state.best_correct_candidate_id is None
        ):
            raise ValueError(
                "S3.3 requires a qualified baseline or a terminal baseline outcome"
            )
        if not isinstance(checkpoint_writer, OptimizerCheckpointWriter):
            raise TypeError("checkpoint_writer must be OptimizerCheckpointWriter")
        if not hasattr(provider, "propose") or not hasattr(provider, "budget_increment"):
            raise TypeError("provider does not satisfy HypothesisProvider")
        if not hasattr(executor, "execute") or not hasattr(executor, "budget_increment"):
            raise TypeError("executor does not satisfy CandidateExecutor")
        if not isinstance(budget, BudgetManager):
            raise TypeError("budget must be BudgetManager")
        if not isinstance(trace, TraceRecorder):
            raise TypeError("trace must be TraceRecorder")
        selected_policy = policy or SafeOptimizerPolicy.safe_v1()
        if state.policy_profile != selected_policy.name:
            raise ValueError("state policy_profile does not match policy")
        if state.objective != selected_policy.objective:
            raise ValueError("state objective does not match policy")
        if not callable(clock):
            raise TypeError("clock must be callable")

        self._state = state
        self._candidates = index
        self._writer = checkpoint_writer
        self._provider = provider
        self._executor = executor
        self._budget = budget
        self._trace = trace
        self._policy = selected_policy
        self._comparator = comparator or LatencyPpaComparator()
        self._artifacts = artifact_store or OptimizerArtifactStore(
            checkpoint_writer.root
        )
        self._clock = clock
        self._resume = bool(resume)
        self._counters = OptimizerRunCounters()
        self._initialized = False

    @property
    def state(self) -> OptimizerState:
        return self._state

    @property
    def candidates(self) -> Mapping[str, CandidateRecord]:
        return dict(self._candidates)

    @property
    def counters(self) -> OptimizerRunCounters:
        return self._counters

    def step(self) -> OptimizerRunResult:
        """Execute at most one deterministic round/transition.

        This is primarily useful for checkpoint/resume integration and tests.
        A completed checkpoint always contains the next action, so a new engine
        may resume without repeating an executed candidate.
        """

        self._ensure_initialized()
        if self._state.terminal_status is None:
            if (
                self._state.executed_candidate_count
                >= self._policy.max_executed_candidates
            ):
                self._finalize("max_executed_candidates_reached")
            else:
                self._run_current_round()
        return self._result()

    def run(self) -> OptimizerRunResult:
        self._ensure_initialized()
        if self._state.terminal_status is not None:
            return self._result()
        if self._state.best_correct_candidate_id is None:
            raise ValueError(
                "S3.3 requires an accepted, initialized baseline before search"
            )

        self._trace.record(
            "optimizer.started",
            phase="optimize",
            status="running",
            metadata={
                "policy": self._policy.to_dict(),
                "provider": self._provider.name,
                "executor": self._executor.name,
                "real_network": self._uses_network(),
                "real_vitis": self._uses_vitis(),
            },
        )

        while self._state.terminal_status is None:
            if (
                self._state.executed_candidate_count
                >= self._policy.max_executed_candidates
            ):
                self._finalize("max_executed_candidates_reached")
                break
            self._run_current_round()

        self._trace.record(
            "optimizer.finished",
            phase="optimize",
            status=self._state.terminal_status.value,
            metadata={
                "best_correct_candidate_id": (
                    self._state.best_correct_candidate_id
                ),
                "best_ppa_candidate_id": self._state.best_ppa_candidate_id,
                "executed_candidate_count": (
                    self._state.executed_candidate_count
                ),
                "counters": self._counters.to_dict(),
                "real_network": self._uses_network(),
                "real_vitis": self._uses_vitis(),
            },
        )
        return self._result()

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        self._load_or_initialize_checkpoint()
        self._initialized = True

    def _load_or_initialize_checkpoint(self) -> None:
        latest = self._writer.load_latest(required=False)
        if latest is not None:
            if not self._resume:
                raise FileExistsError(
                    "optimizer checkpoint already exists and resume is disabled"
                )
            self._state = latest.state
            self._candidates = dict(latest.candidates)
            return
        snapshot = self._writer.write_checkpoint(self._state, self._candidates)
        self._state = snapshot.state
        self._candidates = dict(snapshot.candidates)
        self._record_decision(
            event="baseline_checkpointed",
            action="enter_search" if self._state.terminal_status is None else "stop",
            reason=(
                "qualified_baseline_ready"
                if self._state.best_correct_candidate_id is not None
                else "baseline_not_qualified"
            ),
        )

    def _run_current_round(self) -> None:
        level = self._state.current_level
        round_number = self._state.current_round
        limits = self._policy.for_level(level)
        if round_number > limits.max_rounds:
            self._advance_level("round_limit_already_complete")
            return

        parent_id = self._state.current_candidate_id
        parent = self._candidates[parent_id]
        parent_source = self._read_candidate_source(parent)
        evidence_ids = self._supporting_evidence_ids(parent)
        request = HypothesisRequest(
            run_id=self._state.run_id,
            level=level,
            round_number=round_number,
            parent_candidate=parent,
            max_hypotheses=limits.hypotheses_per_round,
            supporting_evidence_ids=evidence_ids,
            safe_context={
                "policy": self._policy.name,
                "objective": self._policy.objective,
                "parent_candidate_id": parent.candidate_id,
                "parent_source_sha256": parent.source_sha256,
            },
            parent_source=parent_source,
        )

        if not self._preflight_invocation(
            self._provider.budget_increment,
            "hypothesis_provider",
        ):
            return
        try:
            response = self._provider.propose(request)
            if not isinstance(response, (list, tuple)):
                raise TypeError("provider must return a list or tuple")
            considered = tuple(response[: limits.hypotheses_per_round])
        except HypothesisGenerationAbstained as exc:
            self._consume_invocation(self._provider.budget_increment)
            self._counters = self._counters.increment(
                provider_calls=1,
                hypothesis_generation_abstentions=1,
            )
            self._record_decision(
                event="hypothesis_generation_abstained",
                action="advance_level_without_hypothesis",
                reason=exc.reason_code,
                metadata={
                    "error_code": exc.error_code,
                    "detail_codes": list(exc.detail_codes),
                    "best_correct_candidate_id": self._state.best_correct_candidate_id,
                    "automatic_retry": False,
                    "hypothesis_created": False,
                },
            )
            self._advance_level("hypothesis_generation_abstained")
            return
        except Exception as exc:  # noqa: BLE001 - provider boundary is explicit.
            # The invocation was launched, so simulated/physical usage is
            # consumed even when the provider boundary raises.
            self._consume_invocation(self._provider.budget_increment)
            self._counters = self._counters.increment(provider_calls=1)
            self._terminal_error(
                "hypothesis_provider_error",
                type(exc).__name__,
            )
            return
        self._consume_invocation(self._provider.budget_increment)
        self._counters = self._counters.increment(
            provider_calls=1,
            proposed_hypotheses=len(considered),
        )

        valid, invalid_reasons = self._validate_hypotheses(
            considered,
            level=level,
            parent_candidate_id=parent_id,
        )
        self._counters = self._counters.increment(
            valid_hypotheses=len(valid),
            invalid_hypotheses=len(invalid_reasons),
        )
        for hypothesis in valid:
            self._artifacts.write_hypothesis(hypothesis)

        if not valid:
            self._record_decision(
                event="round_no_executable_hypothesis",
                action="advance_level",
                reason="provider_returned_no_valid_hypothesis",
                metadata={
                    "invalid_reasons": invalid_reasons,
                    "proposed_count": len(considered),
                },
            )
            self._advance_level("no_valid_hypothesis")
            return

        selected = valid[0]
        self._counters = self._counters.increment(selected_hypotheses=1)
        if limits.executed_branches_per_round != 1:
            raise AssertionError("safe-v1 must execute exactly one branch per round")
        self._execute_selected(parent, selected)

    def _validate_hypotheses(
        self,
        values: tuple[Any, ...],
        *,
        level: OptimizationLevel,
        parent_candidate_id: str,
    ) -> tuple[list[HypothesisRecord], list[str]]:
        valid: list[HypothesisRecord] = []
        invalid: list[str] = []
        seen_ids: set[str] = set()
        for index, value in enumerate(values, start=1):
            try:
                hypothesis = normalize_hypothesis(value)
                if hypothesis.level is not level:
                    raise ValueError("level_mismatch")
                if hypothesis.parent_candidate_id != parent_candidate_id:
                    raise ValueError("parent_mismatch")
                if hypothesis.hypothesis_id in seen_ids:
                    raise ValueError("duplicate_hypothesis_id")
                seen_ids.add(hypothesis.hypothesis_id)
                valid.append(hypothesis)
            except Exception as exc:  # noqa: BLE001 - malformed fixtures are tested.
                invalid.append(f"proposal_{index}:{type(exc).__name__}")
        return valid, invalid

    def _execute_selected(
        self,
        parent: CandidateRecord,
        hypothesis: HypothesisRecord,
    ) -> None:
        level = self._state.current_level
        round_number = self._state.current_round
        sequence = self._state.executed_candidate_count + 1
        candidate_id = f"cand-{sequence}"
        budget_before = self._budget_snapshot()

        if not self._preflight_invocation(
            self._executor.budget_increment,
            "candidate_executor",
        ):
            return
        parent_source = self._read_candidate_source(parent)
        request = CandidateExecutionRequest(
            run_id=self._state.run_id,
            sequence=sequence,
            candidate_id=candidate_id,
            level=level,
            round_number=round_number,
            parent_candidate=parent,
            parent_source=parent_source,
            hypothesis=hypothesis,
            budget_before=budget_before,
        )
        try:
            execution = self._executor.execute(request)
            if not isinstance(execution, CandidateExecutionResult):
                raise TypeError("executor returned an invalid result type")
        except CandidateGenerationAbstained as exc:
            self._consume_invocation(self._executor.budget_increment)
            self._counters = self._counters.increment(
                executor_calls=1,
                candidate_generation_abstentions=1,
            )
            self._record_decision(
                event="candidate_generation_abstained",
                action="advance_level_without_candidate",
                reason=exc.reason_code,
                hypothesis_id=hypothesis.hypothesis_id,
                metadata={
                    "candidate_id_reserved_but_not_created": candidate_id,
                    "error_code": exc.error_code,
                    "detail_codes": list(exc.detail_codes),
                    "best_correct_candidate_id": self._state.best_correct_candidate_id,
                    "automatic_retry": False,
                    "candidate_created": False,
                    "qualification_started": False,
                },
            )
            self._advance_level("candidate_generation_abstained")
            return
        except Exception as exc:  # noqa: BLE001 - executor boundary is explicit.
            self._consume_invocation(self._executor.budget_increment)
            self._counters = self._counters.increment(executor_calls=1)
            self._terminal_error(
                "candidate_executor_error",
                type(exc).__name__,
                hypothesis_id=hypothesis.hypothesis_id,
            )
            return

        self._consume_invocation(self._executor.budget_increment)
        self._counters = self._counters.increment(executor_calls=1)
        budget_after = self._budget_snapshot()
        if execution.qualification.candidate_id != candidate_id:
            self._terminal_error(
                "qualification_linkage_error",
                "executor qualification candidate_id mismatch",
                hypothesis_id=hypothesis.hypothesis_id,
            )
            return

        source_sha = sha256(execution.source).hexdigest()
        generated = CandidateRecord(
            candidate_id=candidate_id,
            sequence=sequence,
            parent_candidate_id=parent.candidate_id,
            hypothesis_id=hypothesis.hypothesis_id,
            level=level,
            source_sha256=source_sha,
            source_artifact=f"candidates/{candidate_id}/source.cpp",
            status=CandidateStatus.GENERATED,
            budget_before=budget_before,
            created_at_utc=self._timestamp(),
        )
        self._writer.write_candidate_source(generated, execution.source)
        qualification = replace(
            execution.qualification,
            budget_before=budget_before,
            budget_after=budget_after,
        )
        terminal_candidate = qualification.apply_to_candidate(generated)

        state_updates, optimizer_decision = self._decide_candidate(
            terminal_candidate,
            qualification.status,
            level=level,
            round_number=round_number,
        )
        terminal_candidate = replace(
            terminal_candidate,
            decision={
                **dict(terminal_candidate.decision),
                **optimizer_decision,
            },
        )
        self._candidates[candidate_id] = terminal_candidate
        self._state = replace(
            self._state,
            executed_candidate_count=sequence,
            **state_updates,
        )

        if self._state.terminal_status is None:
            self._advance_after_executed_round()
        self._checkpoint()
        self._record_decision(
            event="candidate_terminal",
            action=optimizer_decision["optimizer_action"],
            reason=optimizer_decision["optimizer_reason"],
            candidate_id=candidate_id,
            hypothesis_id=hypothesis.hypothesis_id,
            level=level,
            round_number=round_number,
            metadata={
                "qualification_status": qualification.status.value,
                "candidate_status": terminal_candidate.status.value,
                "best_correct_candidate_id": (
                    self._state.best_correct_candidate_id
                ),
                "best_ppa_candidate_id": self._state.best_ppa_candidate_id,
                "next_level": self._state.current_level.value,
                "next_round": self._state.current_round,
                "terminal_status": (
                    None
                    if self._state.terminal_status is None
                    else self._state.terminal_status.value
                ),
            },
        )

    def _decide_candidate(
        self,
        candidate: CandidateRecord,
        qualification_status: QualificationStatus,
        *,
        level: OptimizationLevel,
        round_number: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        base_decision = {
            "optimizer_policy": self._policy.name,
            "optimizer_level": level.value,
            "optimizer_round": round_number,
            "selection_rule": "first_valid_provider_order",
            "rollback_target": self._state.best_correct_candidate_id,
        }

        if qualification_status is QualificationStatus.REJECTED:
            self._counters = self._counters.increment(rejected_candidates=1)
            return (
                {"current_candidate_id": self._required_best_correct()},
                {
                    **base_decision,
                    "optimizer_action": "reject_and_rollback",
                    "optimizer_reason": "qualification_rejected",
                },
            )
        if qualification_status is QualificationStatus.BLOCKED:
            self._counters = self._counters.increment(blocked_candidates=1)
            return (
                {
                    "current_candidate_id": self._required_best_correct(),
                    "terminal_status": OptimizerTerminalStatus.BLOCKED,
                },
                {
                    **base_decision,
                    "optimizer_action": "stop_blocked",
                    "optimizer_reason": "qualification_blocked",
                },
            )
        if qualification_status is QualificationStatus.REVIEW_REQUIRED:
            self._counters = self._counters.increment(
                review_required_candidates=1
            )
            return (
                {
                    "current_candidate_id": self._required_best_correct(),
                    "terminal_status": OptimizerTerminalStatus.REVIEW_REQUIRED,
                },
                {
                    **base_decision,
                    "optimizer_action": "stop_for_review",
                    "optimizer_reason": "qualification_review_required",
                },
            )
        if qualification_status is QualificationStatus.ERROR:
            self._counters = self._counters.increment(error_candidates=1)
            return (
                {
                    "current_candidate_id": self._required_best_correct(),
                    "terminal_status": OptimizerTerminalStatus.ERROR,
                },
                {
                    **base_decision,
                    "optimizer_action": "stop_error",
                    "optimizer_reason": "qualification_error",
                },
            )

        self._counters = self._counters.increment(accepted_candidates=1)
        candidate_ppa = self._candidate_ppa(candidate)
        feasible = candidate_ppa.objective_feasible
        if feasible is None:
            return (
                {
                    "current_candidate_id": self._required_best_correct(),
                    "terminal_status": OptimizerTerminalStatus.REVIEW_REQUIRED,
                },
                {
                    **base_decision,
                    "optimizer_action": "stop_for_review",
                    "optimizer_reason": "objective_feasibility_unknown",
                },
            )

        incumbent_id = self._state.best_ppa_candidate_id
        if feasible is True and incumbent_id is None:
            return (
                {
                    "current_candidate_id": candidate.candidate_id,
                    "best_correct_candidate_id": candidate.candidate_id,
                    "best_ppa_candidate_id": candidate.candidate_id,
                },
                {
                    **base_decision,
                    "optimizer_action": "update_best_correct_and_ppa",
                    "optimizer_reason": "first_objective_feasible_candidate",
                    "comparison": None,
                },
            )

        if feasible is True:
            assert incumbent_id is not None
            incumbent = self._candidates[incumbent_id]
            comparison = self._comparator.compare(
                candidate_ppa,
                self._candidate_ppa(incumbent),
                candidate_sequence=candidate.sequence,
                incumbent_sequence=incumbent.sequence,
            )
            if comparison.decision is PpaComparisonDecision.INCOMPARABLE:
                return (
                    {
                        "current_candidate_id": self._required_best_correct(),
                        "terminal_status": OptimizerTerminalStatus.REVIEW_REQUIRED,
                    },
                    {
                        **base_decision,
                        "optimizer_action": "stop_for_review",
                        "optimizer_reason": "ppa_incomparable",
                        "comparison": comparison.to_dict(),
                    },
                )
            if comparison.better is True:
                return (
                    {
                        "current_candidate_id": candidate.candidate_id,
                        "best_correct_candidate_id": candidate.candidate_id,
                        "best_ppa_candidate_id": candidate.candidate_id,
                    },
                    {
                        **base_decision,
                        "optimizer_action": "update_best_correct_and_ppa",
                        "optimizer_reason": "ppa_improved",
                        "comparison": comparison.to_dict(),
                    },
                )
            return (
                {"current_candidate_id": self._required_best_correct()},
                {
                    **base_decision,
                    "optimizer_action": "retain_incumbent_and_rollback",
                    "optimizer_reason": "ppa_not_improved",
                    "comparison": comparison.to_dict(),
                },
            )

        # Correct and synthesizable but objective-infeasible.  It remains a
        # recorded accepted candidate.  It may advance best_correct only while
        # the run has no objective-feasible recovery pointer; once best_ppa
        # exists, regression/infeasibility cannot move the selected path.
        if incumbent_id is None:
            return (
                {
                    "current_candidate_id": candidate.candidate_id,
                    "best_correct_candidate_id": candidate.candidate_id,
                },
                {
                    **base_decision,
                    "optimizer_action": "update_best_correct_only",
                    "optimizer_reason": "accepted_but_objective_infeasible",
                    "comparison": None,
                },
            )
        return (
            {"current_candidate_id": self._required_best_correct()},
            {
                **base_decision,
                "optimizer_action": "retain_feasible_incumbent_and_rollback",
                "optimizer_reason": "candidate_objective_infeasible",
                "comparison": None,
            },
        )

    def _advance_after_executed_round(self) -> None:
        level = self._state.current_level
        round_number = self._state.current_round
        limits = self._policy.for_level(level)
        if round_number < limits.max_rounds:
            self._state = replace(self._state, current_round=round_number + 1)
            return
        next_level = self._policy.next_level(level)
        if next_level is None:
            self._state = replace(
                self._state,
                terminal_status=self._completion_status(),
            )
            return
        self._state = replace(
            self._state,
            current_level=next_level,
            current_round=1,
            current_candidate_id=self._required_best_correct(),
        )

    def _advance_level(self, reason: str) -> None:
        level = self._state.current_level
        next_level = self._policy.next_level(level)
        if next_level is None:
            self._state = replace(
                self._state,
                terminal_status=self._completion_status(),
                current_candidate_id=self._required_best_correct(),
            )
            action = "terminalize"
        else:
            self._state = replace(
                self._state,
                current_level=next_level,
                current_round=1,
                current_candidate_id=self._required_best_correct(),
            )
            action = "enter_next_level"
        self._checkpoint()
        self._record_decision(
            event="level_transition",
            action=action,
            reason=reason,
            metadata={
                "from_level": level.value,
                "to_level": None if next_level is None else next_level.value,
                "terminal_status": (
                    None
                    if self._state.terminal_status is None
                    else self._state.terminal_status.value
                ),
            },
        )

    def _completion_status(self) -> OptimizerTerminalStatus:
        if self._state.best_ppa_candidate_id is None:
            if self._has_unknown_feasibility():
                return OptimizerTerminalStatus.REVIEW_REQUIRED
            return OptimizerTerminalStatus.NO_FEASIBLE_CANDIDATE
        if self._state.best_ppa_candidate_id == self._state.baseline_candidate_id:
            return OptimizerTerminalStatus.ACCEPTED_NO_IMPROVEMENT
        return OptimizerTerminalStatus.ACCEPTED_IMPROVED

    def _finalize(self, reason: str) -> None:
        self._state = replace(
            self._state,
            terminal_status=self._completion_status(),
            current_candidate_id=self._required_best_correct(),
        )
        self._checkpoint()
        self._record_decision(
            event="optimizer_terminal",
            action="terminalize",
            reason=reason,
            metadata={"terminal_status": self._state.terminal_status.value},
        )

    def _preflight_invocation(
        self,
        increment: BudgetIncrement,
        label: str,
    ) -> bool:
        if not isinstance(increment, BudgetIncrement):
            self._terminal_error(
                "invalid_budget_increment",
                f"{label} budget_increment must be BudgetIncrement",
            )
            return False
        try:
            self._budget.ensure_available(**increment.to_kwargs())
            return True
        except BudgetExceededError as exc:
            terminal = (
                OptimizerTerminalStatus.BUDGET_EXHAUSTED_WITH_BEST_CORRECT
                if self._state.best_correct_candidate_id is not None
                else OptimizerTerminalStatus.BLOCKED
            )
            self._state = replace(self._state, terminal_status=terminal)
            self._checkpoint()
            self._record_decision(
                event="budget_exhausted",
                action="stop_before_invocation",
                reason=f"{label}:{exc.resource}",
                metadata={
                    "resource": exc.resource,
                    "limit": exc.limit,
                    "attempted": exc.attempted,
                    "invocation_launched": False,
                },
            )
            return False

    def _consume_invocation(self, increment: BudgetIncrement) -> None:
        if increment.is_zero:
            return
        self._budget.consume(**increment.to_kwargs())

    def _terminal_error(
        self,
        reason: str,
        detail: str,
        *,
        hypothesis_id: str | None = None,
    ) -> None:
        self._state = replace(
            self._state,
            terminal_status=OptimizerTerminalStatus.ERROR,
            current_candidate_id=(
                self._state.best_correct_candidate_id
                or self._state.current_candidate_id
            ),
        )
        self._checkpoint()
        self._record_decision(
            event="optimizer_error",
            action="stop_error",
            reason=reason,
            hypothesis_id=hypothesis_id,
            metadata={"error_type_or_code": detail},
        )

    def _checkpoint(self) -> None:
        snapshot = self._writer.write_checkpoint(self._state, self._candidates)
        self._state = snapshot.state
        self._candidates = dict(snapshot.candidates)

    def _record_decision(
        self,
        *,
        event: str,
        action: str,
        reason: str,
        candidate_id: str | None = None,
        hypothesis_id: str | None = None,
        level: OptimizationLevel | str | None = None,
        round_number: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        timestamp = self._timestamp()
        decision_level = (
            self._state.current_level.value
            if level is None
            else (level.value if isinstance(level, OptimizationLevel) else str(level))
        )
        decision_round = self._state.current_round if round_number is None else round_number
        self._artifacts.append_decision(
            event=event,
            level=decision_level,
            round_number=decision_round,
            candidate_id=candidate_id,
            hypothesis_id=hypothesis_id,
            action=action,
            reason=reason,
            metadata={} if metadata is None else metadata,
            timestamp_utc=timestamp,
        )
        self._trace.record(
            f"optimizer.{event}",
            phase="optimize",
            status=(
                "running"
                if self._state.terminal_status is None
                else self._state.terminal_status.value
            ),
            message=reason,
            metadata={
                "level": decision_level,
                "round": decision_round,
                "candidate_id": candidate_id,
                "hypothesis_id": hypothesis_id,
                "action": action,
                **({} if metadata is None else dict(metadata)),
            },
        )


    def _uses_network(self) -> bool:
        return bool(
            getattr(self._provider, "uses_network", False)
            or getattr(self._executor, "uses_network", False)
        )

    def _uses_vitis(self) -> bool:
        return bool(getattr(self._executor, "uses_vitis", False))

    def _supporting_evidence_ids(
        self,
        candidate: CandidateRecord,
    ) -> tuple[str, ...]:
        if not candidate.ppa:
            return ()
        try:
            return (PpaEvidence.from_dict(candidate.ppa).evidence_id,)
        except Exception:
            return ()

    def _candidate_ppa(self, candidate: CandidateRecord) -> PpaEvidence:
        if not candidate.ppa:
            raise ValueError(
                f"accepted candidate has no PPA evidence: {candidate.candidate_id}"
            )
        return PpaEvidence.from_dict(candidate.ppa)

    def _has_unknown_feasibility(self) -> bool:
        for candidate in self._candidates.values():
            if candidate.status is not CandidateStatus.ACCEPTED or not candidate.ppa:
                continue
            try:
                if PpaEvidence.from_dict(candidate.ppa).objective_feasible is None:
                    return True
            except Exception:
                return True
        return False

    def _read_candidate_source(self, candidate: CandidateRecord) -> bytes:
        path = self._writer.root.joinpath(*candidate.source_artifact.split("/"))
        if path.is_symlink() or not path.is_file():
            raise ValueError("candidate source artifact is not a regular file")
        data = path.read_bytes()
        if sha256(data).hexdigest() != candidate.source_sha256:
            raise ValueError("candidate source artifact hash mismatch")
        return data

    def _required_best_correct(self) -> str:
        value = self._state.best_correct_candidate_id
        if value is None:
            raise ValueError("best_correct is required for S3.3 search")
        return value

    def _budget_snapshot(self) -> dict[str, Any]:
        return self._budget.snapshot().to_dict()

    def _timestamp(self) -> str:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("optimizer clock must return timezone-aware datetime")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _result(self) -> OptimizerRunResult:
        return OptimizerRunResult(
            state=self._state,
            candidates=self._candidates,
            counters=self._counters,
            budget_usage=self._budget_snapshot(),
            checkpoint_root=self._writer.root,
        )
