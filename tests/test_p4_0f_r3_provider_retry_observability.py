from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from agrefactor.optimization import (
    BudgetIncrement,
    CandidateRecord,
    CandidateStatus,
    DeterministicOptimizerStateMachine,
    FakeCandidateExecutor,
    FakeExecutionOutcome,
    FakeHypothesisProvider,
    OptimizerCheckpointWriter,
    OptimizerState,
    OptimizerTerminalStatus,
    PpaEvidence,
    PpaReportFormat,
    PpaResourceUsage,
)
from agrefactor.optimization.structural_model import _provider_error_reason_codes
from agrefactor.product.run_output import _optimizer_reason_code
from agrefactor.runtime.budget import BudgetLimits, BudgetManager
from agrefactor.runtime.trace import TraceRecorder

FIXED_TIME = datetime(2026, 8, 5, tzinfo=timezone.utc)
BASELINE_SOURCE = b"int top(){return 0;}\n"
CONTEXT = "a" * 64


def fixed_clock():
    return FIXED_TIME


def ppa(candidate_id: str, latency: int) -> PpaEvidence:
    return PpaEvidence(
        evidence_id=f"ppa-{candidate_id}",
        parser_profile="p4-0f-r3-fixture",
        report_format=PpaReportFormat.XML,
        report_relative_path=f"fake/{candidate_id}.xml",
        report_sha256=hashlib.sha256(candidate_id.encode()).hexdigest(),
        comparison_context_identity_sha256=CONTEXT,
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


def accepted_baseline():
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
    state = OptimizerState.initial(
        run_id="p4-0f-r3-test"
    ).with_qualified_baseline(record)
    from dataclasses import replace

    return replace(state, best_ppa_candidate_id="baseline"), record


class RetryableResponseError(RuntimeError):
    def __init__(self, code="provider_empty_final_content"):
        super().__init__("private detail must not persist")
        self.reason_codes = (code,)
        self.diagnostics = {
            "choices_count": 1,
            "message_present": True,
            "content_present": False,
            "content_chars": 0,
            "content_shape": "none",
            "reasoning_content_present": True,
            "reasoning_content_chars": 42,
            "finish_reason": (
                "length" if code == "provider_finish_length" else None
            ),
            "usage_present": True,
            "usage_field_names": ["prompt_tokens"],
        }


class SequenceProvider:
    name = "sequence-provider"
    uses_network = True

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.requests = []
        self.delegate = FakeHypothesisProvider()

    @property
    def budget_increment(self):
        return BudgetIncrement(llm_calls=1)

    def propose(self, request):
        self.calls += 1
        self.requests.append(request)
        outcome = self.outcomes.pop(0) if self.outcomes else "success"
        if isinstance(outcome, BaseException):
            raise outcome
        return self.delegate.propose(request)


class SequenceExecutor:
    name = "sequence-executor"
    uses_vitis = True

    def __init__(self, outcomes, first_latency=90):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.requests = []
        self.delegate = FakeCandidateExecutor(
            {1: FakeExecutionOutcome(latency_cycles_max=first_latency)}
        )

    @property
    def budget_increment(self):
        return BudgetIncrement(llm_calls=1)

    def execute(self, request):
        self.calls += 1
        self.requests.append(request)
        outcome = self.outcomes.pop(0) if self.outcomes else "success"
        if isinstance(outcome, BaseException):
            raise outcome
        return self.delegate.execute(request)


class Harness:
    def __init__(self, testcase, provider, executor, *, budget_limits=None):
        state, baseline = accepted_baseline()
        self.tmp = tempfile.TemporaryDirectory()
        testcase.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.writer = OptimizerCheckpointWriter(self.root)
        self.writer.write_candidate_source(baseline, BASELINE_SOURCE)
        self.trace = TraceRecorder("p4-0f-r3-test", clock=fixed_clock)
        self.budget = BudgetManager(
            limits=budget_limits,
            clock=lambda: 0.0,
        )
        self.engine = DeterministicOptimizerStateMachine(
            state=state,
            candidates={"baseline": baseline},
            checkpoint_writer=self.writer,
            provider=provider,
            executor=executor,
            budget=self.budget,
            trace=self.trace,
            clock=fixed_clock,
            resume=True,
        )

    def decisions(self):
        path = next(self.root.rglob("decisions.jsonl"))
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


class ProviderRetryTests(unittest.TestCase):
    def test_retryable_provider_failure_retries_once_and_succeeds(self):
        provider = SequenceProvider([RetryableResponseError(), "success"])
        executor = FakeCandidateExecutor(
            {1: FakeExecutionOutcome(latency_cycles_max=90)}
        )
        harness = Harness(self, provider, executor)
        result = harness.engine.run()
        # The optimizer continues its normal bounded search after the retry
        # succeeds, so total run-level provider calls may exceed two.  The
        # retry contract is instead proven at the exact failed boundary:
        # attempts 1 and 2 receive the same immutable request object, exactly
        # one typed retry is scheduled, and every physical call is accounted.
        self.assertGreaterEqual(provider.calls, 2)
        self.assertIs(provider.requests[0], provider.requests[1])
        self.assertEqual(result.counters.provider_calls, provider.calls)
        expected_llm_calls = (
            result.counters.provider_calls
            * provider.budget_increment.llm_calls
            + result.counters.executor_calls
            * executor.budget_increment.llm_calls
        )
        self.assertEqual(
            result.budget_usage["llm_calls"], expected_llm_calls
        )
        self.assertEqual(provider.budget_increment.llm_calls, 1)
        self.assertEqual(executor.budget_increment.llm_calls, 0)
        self.assertNotEqual(
            result.terminal_status, OptimizerTerminalStatus.ERROR
        )
        events = [
            item
            for item in harness.decisions()
            if item.get("event")
            == "hypothesis_provider_response_retry_scheduled"
        ]
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0]["metadata"]["provider_reason_codes"],
            ["provider_empty_final_content"],
        )
        self.assertNotIn("private detail", json.dumps(events))
        succeeded = [
            item
            for item in harness.decisions()
            if item.get("event")
            == "hypothesis_provider_response_retry_succeeded"
        ]
        self.assertEqual(len(succeeded), 1)
        self.assertEqual(succeeded[0]["metadata"]["retry_outcome"], "succeeded")
        self.assertEqual(succeeded[0]["metadata"]["retry_attempt"], 2)

    def test_retry_budget_preflight_blocks_second_physical_call(self):
        provider = SequenceProvider([RetryableResponseError(), "success"])
        harness = Harness(
            self,
            provider,
            FakeCandidateExecutor(),
            budget_limits=BudgetLimits(max_llm_calls=1),
        )
        result = harness.engine.run()
        self.assertEqual(provider.calls, 1)
        self.assertEqual(result.counters.provider_calls, 1)
        self.assertEqual(result.budget_usage["llm_calls"], 1)
        self.assertEqual(
            result.terminal_status,
            OptimizerTerminalStatus.BUDGET_EXHAUSTED_WITH_BEST_CORRECT,
        )
        retry_events = [
            item
            for item in harness.decisions()
            if item.get("event", "").endswith("_response_retry_scheduled")
        ]
        self.assertEqual(retry_events, [])
        budget_events = [
            item
            for item in harness.decisions()
            if item.get("event") == "budget_exhausted"
        ]
        self.assertEqual(len(budget_events), 1)
        self.assertIn("hypothesis_provider_retry", budget_events[0]["reason"])

    def test_unknown_exception_does_not_retry(self):
        provider = SequenceProvider([RuntimeError("unknown private detail")])
        harness = Harness(self, provider, FakeCandidateExecutor())
        result = harness.engine.run()
        self.assertEqual(provider.calls, 1)
        self.assertEqual(result.terminal_status, OptimizerTerminalStatus.ERROR)

    def test_mixed_retryable_and_nonretryable_codes_do_not_retry(self):
        error = RetryableResponseError()
        error.reason_codes = (
            "provider_empty_final_content",
            "provider_content_filtered",
        )
        provider = SequenceProvider([error])
        harness = Harness(self, provider, FakeCandidateExecutor())
        result = harness.engine.run()
        self.assertEqual(provider.calls, 1)
        self.assertEqual(result.terminal_status, OptimizerTerminalStatus.ERROR)

    def test_retry_event_contains_only_safe_diagnostics(self):
        error = RetryableResponseError()
        error.diagnostics["not_allowlisted"] = "private"
        provider = SequenceProvider([error, "success"])
        harness = Harness(
            self,
            provider,
            FakeCandidateExecutor(
                {1: FakeExecutionOutcome(latency_cycles_max=90)}
            ),
        )
        harness.engine.run()
        event = [
            item for item in harness.decisions()
            if item.get("event")
            == "hypothesis_provider_response_retry_scheduled"
        ][0]
        diagnostics = event["metadata"]["provider_diagnostics"]
        self.assertNotIn("not_allowlisted", diagnostics)
        self.assertEqual(diagnostics["content_shape"], "none")
        self.assertNotIn("private detail", json.dumps(event))

    def test_nonretryable_provider_failure_does_not_retry(self):
        provider = SequenceProvider(
            [RetryableResponseError("provider_content_filtered")]
        )
        harness = Harness(self, provider, FakeCandidateExecutor())
        result = harness.engine.run()
        self.assertEqual(provider.calls, 1)
        self.assertEqual(
            result.terminal_status, OptimizerTerminalStatus.ERROR
        )

    def test_two_retryable_failures_fail_closed_without_improvement(self):
        provider = SequenceProvider(
            [RetryableResponseError(), RetryableResponseError()]
        )
        harness = Harness(self, provider, FakeCandidateExecutor())
        result = harness.engine.run()
        self.assertEqual(provider.calls, 2)
        self.assertEqual(
            result.terminal_status, OptimizerTerminalStatus.ERROR
        )
        error = [
            item
            for item in harness.decisions()
            if item.get("event") == "optimizer_error"
        ][-1]
        self.assertTrue(error["metadata"]["retry_attempted"])
        self.assertEqual(error["metadata"]["retry_count"], 1)

    def test_llm_budget_uses_declared_boundary_increments(self):
        provider = FakeHypothesisProvider()
        executor = SequenceExecutor(["success"])
        harness = Harness(self, provider, executor)
        result = harness.engine.run()
        self.assertGreater(result.counters.provider_calls, 0)
        self.assertGreater(result.counters.executor_calls, 0)
        self.assertEqual(provider.budget_increment.llm_calls, 0)
        self.assertEqual(executor.budget_increment.llm_calls, 1)
        expected_llm_calls = (
            result.counters.provider_calls
            * provider.budget_increment.llm_calls
            + result.counters.executor_calls
            * executor.budget_increment.llm_calls
        )
        self.assertEqual(
            result.budget_usage["llm_calls"], expected_llm_calls
        )
        self.assertEqual(result.budget_usage["llm_calls"], executor.calls)
        self.assertNotEqual(
            result.budget_usage["llm_calls"],
            result.counters.provider_calls + result.counters.executor_calls,
        )

    def test_executor_provider_response_failure_retries_once(self):
        provider = FakeHypothesisProvider()
        executor = SequenceExecutor([RetryableResponseError(), "success"])
        harness = Harness(self, provider, executor)
        result = harness.engine.run()
        # A successful retry returns control to the normal optimizer loop;
        # later hypotheses/candidates are ordinary invocations, not retries.
        # Verify the exact boundary and run-level physical-call accounting
        # rather than assuming the entire optimizer terminates after attempt 2.
        self.assertGreaterEqual(executor.calls, 2)
        self.assertIs(executor.requests[0], executor.requests[1])
        self.assertEqual(result.counters.executor_calls, executor.calls)
        expected_llm_calls = (
            result.counters.provider_calls
            * provider.budget_increment.llm_calls
            + result.counters.executor_calls
            * executor.budget_increment.llm_calls
        )
        self.assertEqual(
            result.budget_usage["llm_calls"], expected_llm_calls
        )
        self.assertEqual(provider.budget_increment.llm_calls, 0)
        self.assertEqual(executor.budget_increment.llm_calls, 1)
        self.assertEqual(result.budget_usage["llm_calls"], executor.calls)
        self.assertNotEqual(
            result.budget_usage["llm_calls"],
            result.counters.provider_calls + result.counters.executor_calls,
        )
        self.assertNotEqual(
            result.terminal_status, OptimizerTerminalStatus.ERROR
        )
        scheduled = [
            item
            for item in harness.decisions()
            if item.get("event")
            == "candidate_executor_response_retry_scheduled"
        ]
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(
            scheduled[0]["metadata"]["provider_reason_codes"],
            ["provider_empty_final_content"],
        )
        succeeded = [
            item
            for item in harness.decisions()
            if item.get("event")
            == "candidate_executor_response_retry_succeeded"
        ]
        self.assertEqual(len(succeeded), 1)
        self.assertEqual(succeeded[0]["metadata"]["retry_attempt"], 2)

    def test_retry_exhausted_after_improvement_preserves_best(self):
        provider = SequenceProvider(
            ["success", RetryableResponseError(), RetryableResponseError()]
        )
        harness = Harness(
            self,
            provider,
            FakeCandidateExecutor(
                {1: FakeExecutionOutcome(latency_cycles_max=90)}
            ),
        )
        result = harness.engine.run()
        self.assertEqual(
            result.terminal_status,
            OptimizerTerminalStatus.ACCEPTED_IMPROVED,
        )
        interruption = [
            item
            for item in harness.decisions()
            if item.get("event") == "search_interrupted_with_best"
        ][-1]
        self.assertTrue(interruption["metadata"]["retry_attempted"])
        self.assertEqual(
            interruption["metadata"]["provider_reason_codes"],
            ["provider_empty_final_content"],
        )


class ObservabilityTests(unittest.TestCase):
    def test_provider_reason_codes_are_safe_tokens(self):
        self.assertEqual(
            _provider_error_reason_codes(RetryableResponseError()),
            ("provider_empty_final_content",),
        )
        error = RuntimeError("x")
        error.reason_codes = ("BAD CODE",)
        self.assertEqual(_provider_error_reason_codes(error), ())

    def test_product_summary_prefers_typed_provider_reason(self):
        optimizer = {
            "terminal_error": {
                "reason": "hypothesis_provider_error",
                "provider_reason_codes": ["provider_finish_length"],
            }
        }
        self.assertEqual(
            _optimizer_reason_code(optimizer), "provider_finish_length"
        )

    def test_product_summary_falls_back_to_optimizer_reason(self):
        optimizer = {
            "terminal_error": {
                "reason": "hypothesis_provider_error",
                "provider_reason_codes": [],
            }
        }
        self.assertEqual(
            _optimizer_reason_code(optimizer), "hypothesis_provider_error"
        )


if __name__ == "__main__":
    unittest.main()
