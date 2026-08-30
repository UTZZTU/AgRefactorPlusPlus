from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from agrefactor.evidence import DiagnosticEvent
from agrefactor.models import (
    ModelProvider,
    ModelResponse,
    ModelSpec,
    TokenUsage,
)
from agrefactor.recovery import (
    AdvisoryConfidence,
    AdvisoryOwner,
    AdvisoryRepairScope,
    DiagnosticAdvisory,
    ProviderBackedShadowDiagnosticAdvisor,
    ShadowInputRejected,
    ShadowReserve,
    build_shadow_request,
    compare_shadow_equivalence,
    diagnostic_event_from_dict,
    evaluate_calibration,
    freeze_calibration_protocol,
    run_shadow_diagnostics,
)
from agrefactor.runtime.budget import BudgetLimits, BudgetManager
from agrefactor.runtime.trace import TraceRecorder


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def event(**overrides) -> DiagnosticEvent:
    values = {
        "event_id": "diagnostic-r2-test",
        "run_id": "run-r2",
        "validation_id": "validation-r2",
        "stage": "public_evaluation",
        "owner": "unknown",
        "failure_classes": ("runtime_mismatch",),
        "severities": ("error",),
        "route_action": "review_unknown",
        "repair_scope": "none_abstain",
        "evidence_refs": ("report-r2", "feedback-r2"),
        "target_identity": {"name": "target", "fingerprint": SHA_B},
        "toolchain_identity": {
            "toolchain": "vitis_hls",
            "toolchain_version": "2023.2",
            "fingerprint": SHA_C,
        },
        "candidate_sha256": SHA_A,
        "public_suite_identities": (
            {
                "suite_id": "public-main",
                "split": "public",
                "content_sha256": SHA_D,
            },
        ),
        "physical_tool_launched": True,
        "evidence_complete": True,
        "context_signature": SHA_B,
        "created_at": "2026-08-29T00:00:00+00:00",
    }
    values.update(overrides)
    return DiagnosticEvent(**values)


def valid_output(**overrides) -> str:
    values = {
        "suspected_owner": "candidate",
        "suspected_failure_class": "runtime_mismatch",
        "evidence_refs": ["report-r2"],
        "repair_scope": "candidate_only",
        "confidence": "medium",
        "bounded_repair_intent": "inspect the indexed public mismatch",
    }
    values.update(overrides)
    return json.dumps(values)


class FakeProvider(ModelProvider):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    @property
    def name(self) -> str:
        return "fake-r2"

    def generate(self, model, request):
        self.calls.append((model, request))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return ModelResponse(
            text=response,
            model=model.model,
            usage=TokenUsage(
                prompt_tokens=3,
                completion_tokens=2,
                cost_usd=0.01,
            ),
        )


class StaticAdvisor:
    def __init__(self):
        self.calls = 0

    def diagnose(self, request):
        self.calls += 1
        return DiagnosticAdvisory(
            suspected_owner=AdvisoryOwner.CANDIDATE,
            suspected_failure_class="runtime_mismatch",
            evidence_refs=(request.evidence_ids[0],),
            repair_scope=AdvisoryRepairScope.CANDIDATE_ONLY,
            confidence=AdvisoryConfidence.MEDIUM,
            metadata={"bounded_repair_intent_executed": False},
        )


class R2ShadowAdvisorTests(unittest.TestCase):
    def model(self) -> ModelSpec:
        return ModelSpec(
            name="fake-r2",
            provider="fake-r2",
            model="fake-r2",
        )

    def advisor(self, responses, **kwargs):
        provider = FakeProvider(responses)
        selected = ProviderBackedShadowDiagnosticAdvisor(
            provider=provider,
            model=self.model(),
            budget=kwargs.pop("budget", BudgetManager()),
            **kwargs,
        )
        return selected, provider

    def test_trigger_accepts_only_complete_public_unknown_review(self):
        for stage in (
            "public_csim",
            "public_evaluation",
            "csim",
            "csynth",
            "public_cosim",
        ):
            request = build_shadow_request(event(stage=stage))
            self.assertEqual(request.evidence_view, "agent_safe")
            self.assertTrue(request.run_identity_complete)
        rejected = (
            {"stage": "preflight"},
            {"owner": "candidate", "route_action": "repair_candidate"},
            {"physical_tool_launched": False},
            {"evidence_complete": False},
            {"candidate_sha256": None},
            {"public_suite_identities": ()},
            {"failure_classes": ("infrastructure_failure",)},
        )
        for change in rejected:
            with self.subTest(change=change), self.assertRaises(ShadowInputRejected):
                build_shadow_request(event(**change))

    def test_event_projection_rejects_hidden_markers(self):
        payload = event().to_dict()
        payload["hidden_input_count"] = 1
        with self.assertRaises(ShadowInputRejected):
            diagnostic_event_from_dict(payload)
        payload = event().to_dict()
        payload["accepted"] = True
        with self.assertRaises(ShadowInputRejected):
            diagnostic_event_from_dict(payload)
        with self.assertRaises(ShadowInputRejected):
            build_shadow_request(event(evidence_refs=("hidden-report",)))
        with self.assertRaises(ShadowInputRejected):
            build_shadow_request(
                event(
                    target_identity={
                        "fingerprint": SHA_B,
                        "raw_source_path": "/private/source.cpp",
                    }
                )
            )
        payload = event().to_dict()
        payload["evidence_view"] = "operator_full"
        with self.assertRaises(ShadowInputRejected):
            diagnostic_event_from_dict(payload)

    def test_valid_advisory_is_accounted_and_traced(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = TraceRecorder(
                "r2-run",
                output_path=Path(directory) / "trace.jsonl",
            )
            advisor, provider = self.advisor([valid_output()], trace=trace)
            result = advisor.diagnose(build_shadow_request(event()))
            self.assertEqual(result.suspected_owner, AdvisoryOwner.CANDIDATE)
            self.assertEqual(result.repair_scope, AdvisoryRepairScope.CANDIDATE_ONLY)
            self.assertFalse(result.accepted)
            self.assertEqual(len(provider.calls), 1)
            self.assertEqual(advisor.accounting.provider_calls, 1)
            self.assertEqual(advisor.accounting.tokens, 5)
            self.assertAlmostEqual(advisor.accounting.cost_usd, 0.01)
            self.assertEqual(trace.events[-1].event, "r2.shadow_advisor.finished")
            request = provider.calls[0][1]
            combined = json.dumps(request.metadata) + "".join(
                item.content for item in request.messages
            )
            self.assertNotIn("hidden-final", combined)
            self.assertEqual(request.metadata["authority"], "shadow_only")

    def test_strict_output_negative_cases_abstain(self):
        cases = {
            "invalid_json": "not-json",
            "extra_field": valid_output(secret="x"),
            "accepted_true": valid_output(accepted=True),
            "wrong_evidence_ref": valid_output(evidence_refs=["other"]),
            "unknown_owner": valid_output(
                suspected_owner="unknown", repair_scope="none"
            ),
            "testbench_scope": valid_output(
                suspected_owner="testbench", repair_scope="testbench_only"
            ),
            "invalid_enum": valid_output(confidence="certain"),
        }
        for name, response in cases.items():
            with self.subTest(name=name):
                advisor, provider = self.advisor([response])
                result = advisor.diagnose(build_shadow_request(event()))
                self.assertIsNotNone(result.abstain_reason)
                self.assertFalse(result.accepted)
                self.assertEqual(len(provider.calls), 1)

    def test_explicit_model_abstention_is_normalized(self):
        advisor, _ = self.advisor(
            [
                valid_output(
                    suspected_owner="unknown",
                    evidence_refs=[],
                    repair_scope="none",
                    confidence="high",
                    abstain_reason="insufficient_public_evidence",
                )
            ]
        )
        result = advisor.diagnose(build_shadow_request(event()))
        self.assertEqual(result.abstain_reason, "insufficient_public_evidence")
        self.assertEqual(result.confidence, AdvisoryConfidence.LOW)

    def test_provider_timeout_and_error_degrade_without_escape(self):
        for failure, reason in (
            (TimeoutError("late"), "provider_timeout"),
            (RuntimeError("down"), "provider_error:runtimeerror"),
        ):
            with self.subTest(reason=reason):
                advisor, provider = self.advisor([failure])
                result = advisor.diagnose(build_shadow_request(event()))
                self.assertEqual(result.abstain_reason, reason)
                self.assertEqual(len(provider.calls), 1)
                self.assertFalse(result.accepted)
                self.assertIsNone(result.metadata["bounded_repair_intent"])
                self.assertFalse(
                    result.metadata["bounded_repair_intent_executed"]
                )
                self.assertFalse(result.metadata["provider_response_persisted"])
                self.assertFalse(result.metadata["provider_response_observed"])
                self.assertEqual(
                    result.metadata["provider_exception_type"],
                    "timeouterror"
                    if reason == "provider_timeout"
                    else "runtimeerror",
                )

    def test_budget_block_happens_before_provider(self):
        advisor, provider = self.advisor(
            [valid_output()],
            budget=BudgetManager(BudgetLimits(max_llm_calls=0)),
        )
        result = advisor.diagnose(build_shadow_request(event()))
        self.assertEqual(result.abstain_reason, "budget_block")
        self.assertEqual(provider.calls, [])
        self.assertEqual(advisor.accounting.provider_calls, 0)

    def test_shadow_call_reserve_is_enforced(self):
        advisor, provider = self.advisor(
            [valid_output()], reserve=ShadowReserve(max_calls=1)
        )
        request = build_shadow_request(event())
        self.assertIsNone(advisor.diagnose(request).abstain_reason)
        second = advisor.diagnose(request)
        self.assertEqual(second.abstain_reason, "shadow_call_reserve_exhausted")
        self.assertEqual(len(provider.calls), 1)

    def test_equivalence_reducer_covers_all_frozen_fields(self):
        baseline = {
            "route": "review_required",
            "status": "validation_terminal",
            "final_candidate_sha256": SHA_A,
            "recovery_ledger_count": 0,
            "repair_count": 0,
            "best_correct_pointer": None,
        }
        self.assertTrue(compare_shadow_equivalence(baseline, baseline).equivalent)
        for field in tuple(baseline):
            changed = dict(baseline)
            changed[field] = "changed"
            result = compare_shadow_equivalence(baseline, changed)
            self.assertFalse(result.equivalent)
            self.assertIn(field, result.changed_fields)

    def test_provider_independent_audit_artifact_closes_authority(self):
        main = {
            "route": "review_required",
            "status": "validation_terminal",
            "final_candidate_sha256": SHA_A,
            "recovery_ledger_count": 0,
            "repair_count": 0,
            "best_correct_pointer": None,
        }
        advisor = StaticAdvisor()
        artifacts = run_shadow_diagnostics(
            [event().to_dict()],
            advisor=advisor,
            main_before=main,
            main_after=dict(main),
        )
        self.assertEqual(advisor.calls, 1)
        self.assertEqual(len(artifacts), 1)
        artifact = artifacts[0]
        self.assertTrue(artifact["equivalence"]["equivalent"])
        self.assertFalse(artifact["advisory"]["accepted"])
        self.assertFalse(artifact["critical_safety_violation"])
        self.assertEqual(artifact["authority"], "deterministic_fsm_and_evidence_auditor")

    def test_calibration_requires_frozen_split_and_reports_risk(self):
        protocol = freeze_calibration_protocol("r2-calibration-v1", ["r1", "r2"])
        records = [
            {
                "record_id": "r1",
                "evidence_ids": ["e1"],
                "truth": {"owner": "candidate", "failure_class": "x"},
                "advisory": {
                    "suspected_owner": "candidate",
                    "suspected_failure_class": "x",
                    "evidence_refs": ["e1"],
                    "repair_scope": "candidate_only",
                    "confidence": "high",
                    "abstain_reason": None,
                },
            },
            {
                "record_id": "r2",
                "evidence_ids": ["e2"],
                "truth": {"owner": "unknown", "failure_class": "y"},
                "advisory": {"abstain_reason": "insufficient_evidence"},
            },
        ]
        report = evaluate_calibration(records, protocol=protocol)
        self.assertEqual(report.total, 2)
        self.assertEqual(report.covered, 1)
        self.assertEqual(report.abstained, 1)
        self.assertEqual(report.coverage, 0.5)
        self.assertEqual(report.selective_risk, 0.0)
        self.assertEqual(report.citation_validity, 1.0)
        with self.assertRaises(ValueError):
            evaluate_calibration(list(reversed(records)), protocol=protocol)

    def test_orchestrator_shadow_mode_preserves_main_result(self):
        path = Path(__file__).with_name("test_candidate_repair_integration.py")
        spec = importlib.util.spec_from_file_location("_r2_r1_helpers", path)
        helpers = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(helpers)

        def scenario(request, state):
            if request.attempt == 0 and state is helpers.ValidationState.CSYNTH:
                return helpers.report_for(
                    state,
                    item=helpers.feedback_item(
                        "unknown.failure",
                        state=state,
                        owner=helpers.FeedbackOwner.UNKNOWN,
                        category=helpers.FeedbackCategory.UNKNOWN,
                    ),
                    report_id="unknown-report",
                )
            return helpers.pass_scenario(request, state)

        baseline_adapter, _ = helpers.make_adapter([helpers.P1])
        baseline = helpers.CandidateRepairValidationOrchestrator(
            model_adapter=baseline_adapter,
            handler_factory=helpers.ScenarioFactory(scenario),
        ).run(
            helpers.make_context(),
            helpers.make_request(),
            validation_id="r2-baseline",
        )
        shadow_adapter, _ = helpers.make_adapter([helpers.P1])
        static = StaticAdvisor()
        shadow = helpers.CandidateRepairValidationOrchestrator(
            model_adapter=shadow_adapter,
            handler_factory=helpers.ScenarioFactory(scenario),
            shadow_advisor=static,
        ).run(
            helpers.make_context(),
            helpers.make_request(),
            validation_id="r2-shadow",
        )
        self.assertEqual(shadow.status, baseline.status)
        self.assertEqual(shadow.last_validation_state, baseline.last_validation_state)
        self.assertEqual(shadow.final_candidate, baseline.final_candidate)
        self.assertEqual(
            shadow.metadata["repair_attempt_count"],
            baseline.metadata["repair_attempt_count"],
        )
        self.assertEqual(
            len(shadow.metadata["recovery_ledger"]["events"]),
            len(baseline.metadata["recovery_ledger"]["events"]),
        )
        self.assertEqual(static.calls, 1)
        self.assertTrue(shadow.metadata["r2_shadow_enabled"])
        self.assertFalse(baseline.metadata["r2_shadow_enabled"])
        artifact = shadow.metadata["r2_shadow_diagnostics"][0]
        self.assertTrue(artifact["equivalence"]["equivalent"])
        self.assertFalse(artifact["critical_safety_violation"])


if __name__ == "__main__":
    unittest.main()
