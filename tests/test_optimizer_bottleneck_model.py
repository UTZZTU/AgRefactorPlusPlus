import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from agrefactor.config import RunMode, TaskSpec
from agrefactor.models import (
    CandidateResponseError,
    ModelArtifactKind,
    ModelPricingSnapshot,
    ModelProvider,
    ModelRegistry,
    ModelResponse,
    ModelSpec,
    PricingVerificationStatus,
    TokenUsage,
)
from agrefactor.optimization import (
    BottleneckAnalysisResponseContract,
    BottleneckClassificationRecord,
    BottleneckConfidence,
    BottleneckEvidenceView,
    BottleneckKind,
    BottleneckModelArtifactWriter,
    BottleneckModelCandidateExecutor,
    BottleneckModelCandidateGenerator,
    BottleneckModelContractError,
    BottleneckModelHypothesisProvider,
    CandidateExecutionRequest,
    CandidateRecord,
    CandidateStatus,
    DeterministicOptimizerStateMachine,
    FakeCandidateExecutor,
    FakeExecutionOutcome,
    HypothesisRequest,
    OptimizationLevel,
    OptimizerCheckpointWriter,
    OptimizerState,
    PpaEvidence,
    PpaReportFormat,
    PpaResourceUsage,
)
from agrefactor.runtime.budget import BudgetLimits, BudgetManager
from agrefactor.runtime.trace import TraceRecorder


FIXED_TIME = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
SOURCE = """#include <stdint.h>\nvoid top(int *a, int n) {\n    int acc = 0;\n    for (int i = 0; i < n; ++i) { acc += a[i]; a[i] = acc; }\n}\n"""
REWRITE = """#include <stdint.h>\nvoid top(int *a, int n) {\n    int acc0 = 0;\n    for (int i = 0; i < n; ++i) { int next = acc0 + a[i]; a[i] = next; acc0 = next; }\n}\n"""
CONTEXT = "e" * 64


def fixed_clock():
    return FIXED_TIME


def task():
    return TaskSpec(
        task_id="s35-task",
        kernel_path="kernel.cpp",
        kernel_name="top",
        mode=RunMode.OPTIMIZE,
    )


def ppa(candidate_id="baseline", latency=112, ii=2):
    return PpaEvidence(
        evidence_id=f"ppa-{candidate_id}",
        parser_profile="s35-test",
        report_format=PpaReportFormat.XML,
        report_relative_path=f"reports/{candidate_id}_csynth.xml",
        report_sha256=hashlib.sha256(candidate_id.encode()).hexdigest(),
        comparison_context_identity_sha256=CONTEXT,
        latency_cycles_min=96,
        latency_cycles_max=latency,
        initiation_interval_min=1,
        initiation_interval_max=ii,
        target_clock_period_ns=5.0,
        achieved_clock_period_ns=4.25,
        resources_used=PpaResourceUsage(bram_18k=4, dsp=8, ff=1200, lut=900, uram=0),
        resources_available=PpaResourceUsage(bram_18k=100, dsp=200, ff=100000, lut=50000, uram=20),
        max_resource_utilization_ratio=0.04,
        objective_feasible=True,
        parser_warnings=("fixture_only",),
    )


def parent_record(status=CandidateStatus.ACCEPTED, include_ppa=True):
    return CandidateRecord(
        candidate_id="baseline",
        sequence=0,
        parent_candidate_id=None,
        hypothesis_id=None,
        level=None,
        source_sha256=hashlib.sha256(SOURCE.encode()).hexdigest(),
        source_artifact="candidates/baseline/source.cpp",
        status=status,
        ppa=ppa().to_dict() if include_ppa else {},
    )


def analysis_json(*, kind="initiation_interval", confidence="high", hypotheses=True, signal="initiation_interval_max"):
    classifications = [
        {
            "kind": kind,
            "claim": "The reported initiation interval is above one." if kind != "unknown" else "Evidence is insufficient for a specific bottleneck.",
            "confidence": confidence,
            "supporting_evidence_ids": [] if kind == "unknown" else ["ppa-baseline"],
            "signal_fields": [] if kind == "unknown" else [signal],
        }
    ]
    values = []
    if hypotheses:
        values.append(
            {
                "classification_index": 1,
                "claim": "Reorganize the carried update to reduce initiation interval.",
                "supporting_evidence_ids": ["ppa-baseline"],
                "expected_benefit": {"metric": "latency", "direction": "decrease"},
                "risk": "medium",
                "modification_scope": ["loop-carried accumulator"],
                "verification_plan": ["preflight", "public", "csynth", "hidden"],
            }
        )
    return json.dumps(
        {"schema_version": 1, "classifications": classifications, "hypotheses": values}
    )


class QueueProvider(ModelProvider):
    def __init__(self, values):
        self.values = list(values)
        self.calls = []

    @property
    def name(self):
        return "queue-provider"

    def generate(self, model, request):
        self.calls.append((model, request))
        if not self.values:
            raise AssertionError("unexpected model call")
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def response(text, *, prompt_tokens=10, completion_tokens=5):
    return ModelResponse(
        text=text,
        model="fixture-model",
        usage=TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
        finish_reason="stop",
    )


def endpoint(
    values,
    root,
    budget=None,
    parameters=None,
    pricing_snapshot=None,
):
    provider = QueueProvider(values)
    registry = ModelRegistry()
    registry.register_provider(provider)
    registry.register_model(
        ModelSpec(
            name="fixture-model",
            provider=provider.name,
            model="fixture-model",
            family="generic-openai-compatible",
        )
    )
    config = registry.resolve_effective_config(
        "fixture-model",
        parameters=parameters or {"temperature": 0},
        artifact_kind=ModelArtifactKind.CANDIDATE,
        pricing_snapshot=pricing_snapshot,
    )
    return (
        registry,
        config,
        provider,
        budget or BudgetManager(clock=lambda: 0.0),
        BottleneckModelArtifactWriter(root, clock=fixed_clock),
    )


def hypothesis_request(**updates):
    values = {
        "run_id": "run-s35",
        "level": OptimizationLevel.BOTTLENECK,
        "round_number": 1,
        "parent_candidate": parent_record(),
        "max_hypotheses": 3,
        "supporting_evidence_ids": ("ppa-baseline",),
        "safe_context": {"policy": "safe-v1", "objective": "latency"},
        "parent_source": SOURCE.encode(),
    }
    values.update(updates)
    return HypothesisRequest(**values)


def provider_hypothesis(root):
    registry, config, _, budget, artifacts = endpoint([response(analysis_json())], root)
    provider = BottleneckModelHypothesisProvider(
        registry=registry,
        effective_config=config,
        task=task(),
        budget=budget,
        artifacts=artifacts,
    )
    return provider.propose(hypothesis_request())[0]


def execution_request(hypothesis, **updates):
    values = {
        "run_id": "run-s35",
        "sequence": 1,
        "candidate_id": "cand-1",
        "level": OptimizationLevel.BOTTLENECK,
        "round_number": 1,
        "parent_candidate": parent_record(),
        "parent_source": SOURCE.encode(),
        "hypothesis": hypothesis,
        "budget_before": {},
    }
    values.update(updates)
    return CandidateExecutionRequest(**values)


class BottleneckEvidenceViewTests(unittest.TestCase):
    def test_view_from_accepted_candidate(self):
        view = BottleneckEvidenceView.from_candidate(parent_record())
        self.assertEqual(view.evidence_id, "ppa-baseline")
        self.assertEqual(view.initiation_interval_max, 2)

    def test_view_rejects_unaccepted_candidate(self):
        with self.assertRaises(BottleneckModelContractError):
            BottleneckEvidenceView.from_candidate(parent_record(CandidateStatus.GENERATED))

    def test_view_rejects_missing_ppa(self):
        with self.assertRaises(BottleneckModelContractError):
            BottleneckEvidenceView.from_candidate(parent_record(include_ppa=False))

    def test_view_excludes_report_path_and_raw_content(self):
        payload = BottleneckEvidenceView.from_candidate(parent_record()).to_dict()
        self.assertNotIn("report_relative_path", json.dumps(payload))
        self.assertFalse(payload["raw_report_included"])
        self.assertFalse(payload["hidden_evidence_included"])

    def test_view_identity_is_deterministic(self):
        first = BottleneckEvidenceView.from_candidate(parent_record()).identity_sha256
        second = BottleneckEvidenceView.from_candidate(parent_record()).identity_sha256
        self.assertEqual(first, second)

    def test_view_rejects_invalid_direct_metric_ranges(self):
        view = BottleneckEvidenceView.from_candidate(parent_record())
        with self.assertRaises(ValueError):
            replace(view, latency_cycles_min=view.latency_cycles_max + 1)
        with self.assertRaises(ValueError):
            replace(view, initiation_interval_min=3, initiation_interval_max=2)

    def test_view_rejects_non_finite_or_non_positive_clocks(self):
        view = BottleneckEvidenceView.from_candidate(parent_record())
        with self.assertRaises(ValueError):
            replace(view, target_clock_period_ns=0.0)
        with self.assertRaises(ValueError):
            replace(view, achieved_clock_period_ns=float("nan"))


class BottleneckResponseContractTests(unittest.TestCase):
    def contract(self):
        return BottleneckAnalysisResponseContract(
            max_classifications=3,
            max_hypotheses=3,
            allowed_evidence_ids=("ppa-baseline",),
        )

    def test_raw_json_is_accepted(self):
        parsed = self.contract().parse(analysis_json())
        self.assertEqual(len(parsed.classifications), 1)
        self.assertEqual(len(parsed.hypotheses), 1)

    def test_single_json_fence_is_accepted(self):
        parsed = self.contract().parse("```json\n" + analysis_json() + "\n```")
        self.assertEqual(parsed.classifications[0].kind, BottleneckKind.INITIATION_INTERVAL)

    def test_commentary_is_rejected(self):
        with self.assertRaises(BottleneckModelContractError):
            self.contract().parse("Here is the result: " + analysis_json())

    def test_extra_top_level_key_is_rejected(self):
        payload = json.loads(analysis_json())
        payload["extra"] = True
        with self.assertRaises(BottleneckModelContractError):
            self.contract().parse(json.dumps(payload))

    def test_unknown_classification_with_no_hypothesis_is_valid(self):
        parsed = self.contract().parse(analysis_json(kind="unknown", confidence="low", hypotheses=False))
        self.assertEqual(parsed.classifications[0].kind, BottleneckKind.UNKNOWN)
        self.assertEqual(parsed.hypotheses, ())

    def test_unknown_classification_requires_low_confidence(self):
        with self.assertRaises(BottleneckModelContractError):
            self.contract().parse(analysis_json(kind="unknown", confidence="high", hypotheses=False))

    def test_unknown_classification_cannot_have_hypothesis(self):
        with self.assertRaises(BottleneckModelContractError):
            self.contract().parse(analysis_json(kind="unknown", confidence="low", hypotheses=True))

    def test_unknown_evidence_id_is_rejected(self):
        payload = json.loads(analysis_json())
        payload["classifications"][0]["supporting_evidence_ids"] = ["invented"]
        with self.assertRaises(BottleneckModelContractError):
            self.contract().parse(json.dumps(payload))

    def test_unknown_signal_field_is_rejected(self):
        with self.assertRaises(BottleneckModelContractError):
            self.contract().parse(analysis_json(signal="source_contains_pipeline"))

    def test_contract_rejects_resource_container_aliases(self):
        for signal in ("resources_used", "resources_available"):
            with self.subTest(signal=signal):
                with self.assertRaisesRegex(
                    BottleneckModelContractError,
                    "classification 1 violates the frozen schema",
                ):
                    self.contract().parse(analysis_json(signal=signal))

    def test_hypothesis_evidence_must_be_subset(self):
        payload = json.loads(analysis_json())
        payload["hypotheses"][0]["supporting_evidence_ids"] = ["invented"]
        with self.assertRaises(BottleneckModelContractError):
            self.contract().parse(json.dumps(payload))

    def test_missing_classification_reference_is_rejected(self):
        payload = json.loads(analysis_json())
        payload["hypotheses"][0]["classification_index"] = 2
        with self.assertRaises(BottleneckModelContractError):
            self.contract().parse(json.dumps(payload))

    def test_too_many_classifications_are_rejected(self):
        payload = json.loads(analysis_json())
        payload["classifications"] *= 4
        with self.assertRaises(BottleneckModelContractError):
            self.contract().parse(json.dumps(payload))

    def test_extra_classification_key_is_rejected(self):
        payload = json.loads(analysis_json())
        payload["classifications"][0]["threshold"] = 1
        with self.assertRaises(BottleneckModelContractError):
            self.contract().parse(json.dumps(payload))

    def test_wrong_verification_order_is_rejected(self):
        payload = json.loads(analysis_json())
        payload["hypotheses"][0]["verification_plan"] = ["csynth", "public"]
        with self.assertRaises(BottleneckModelContractError):
            self.contract().parse(json.dumps(payload))


class BottleneckArtifactTests(unittest.TestCase):
    def classification(self):
        return BottleneckClassificationRecord(
            classification_id="btl-baseline-r1-1",
            parent_candidate_id="baseline",
            kind=BottleneckKind.INITIATION_INTERVAL,
            claim="The reported II is above one.",
            confidence=BottleneckConfidence.HIGH,
            supporting_evidence_ids=("ppa-baseline",),
            signal_fields=("initiation_interval_max",),
            model_identity={"provider": "fixture"},
            prompt_identity_sha256="a" * 64,
        )

    def test_classification_artifact_is_immutable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = BottleneckModelArtifactWriter(directory, clock=fixed_clock)
            first = writer.write_classification(self.classification())
            second = writer.write_classification(self.classification())
            self.assertEqual(first, second)
            self.assertFalse(json.loads(first.read_text())["authoritative"])

    def test_classification_artifact_rejects_conflicting_content(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = BottleneckModelArtifactWriter(directory, clock=fixed_clock)
            writer.write_classification(self.classification())
            changed = replace(self.classification(), claim="Different claim")
            with self.assertRaises(FileExistsError):
                writer.write_classification(changed)

    def test_classification_rejects_agent_unsafe_claim(self):
        with self.assertRaises(BottleneckModelContractError):
            replace(self.classification(), claim="Read the Hidden diagnostic")

    def test_unknown_classification_record_requires_low_confidence(self):
        with self.assertRaises(ValueError):
            replace(
                self.classification(),
                kind=BottleneckKind.UNKNOWN,
                confidence=BottleneckConfidence.HIGH,
                supporting_evidence_ids=(),
                signal_fields=(),
            )


class BottleneckProviderTests(unittest.TestCase):
    def test_provider_builds_records_and_classification(self):
        with tempfile.TemporaryDirectory() as directory:
            registry, config, fake, budget, artifacts = endpoint([response(analysis_json())], directory)
            provider = BottleneckModelHypothesisProvider(
                registry=registry, effective_config=config, task=task(), budget=budget, artifacts=artifacts
            )
            hypotheses = provider.propose(hypothesis_request())
            self.assertEqual(len(hypotheses), 1)
            self.assertEqual(hypotheses[0].level, OptimizationLevel.BOTTLENECK)
            self.assertEqual(hypotheses[0].supporting_evidence_ids, ("ppa-baseline",))
            self.assertFalse(hypotheses[0].model_identity["classification"]["authoritative"])
            self.assertEqual(len(provider.classifications), 1)
            self.assertEqual(len(fake.calls), 1)

    def test_provider_accepts_unknown_and_returns_no_hypothesis(self):
        with tempfile.TemporaryDirectory() as directory:
            registry, config, _, budget, artifacts = endpoint(
                [response(analysis_json(kind="unknown", confidence="low", hypotheses=False))], directory
            )
            provider = BottleneckModelHypothesisProvider(
                registry=registry, effective_config=config, task=task(), budget=budget, artifacts=artifacts
            )
            self.assertEqual(provider.propose(hypothesis_request()), ())
            self.assertEqual(provider.classifications[0].kind, BottleneckKind.UNKNOWN)

    def test_provider_records_observed_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            registry, config, _, budget, artifacts = endpoint(
                [response(analysis_json(), prompt_tokens=20, completion_tokens=7)], directory
            )
            provider = BottleneckModelHypothesisProvider(
                registry=registry, effective_config=config, task=task(), budget=budget, artifacts=artifacts
            )
            provider.propose(hypothesis_request())
            self.assertEqual(budget.snapshot().tokens, 27)

    def test_provider_uses_typed_pricing_metadata_contract(self):
        snapshot = ModelPricingSnapshot(
            provider="queue-provider",
            model_id="fixture-model",
            official_source_identity="fixture-pricing",
            official_source_url="https://example.invalid/pricing",
            retrieved_at="2026-08-01T00:00:00Z",
            verification_status=PricingVerificationStatus.UNKNOWN,
        )
        with tempfile.TemporaryDirectory() as directory:
            registry, config, _, budget, artifacts = endpoint(
                [response(analysis_json())],
                directory,
                pricing_snapshot=snapshot,
            )
            provider = BottleneckModelHypothesisProvider(
                registry=registry,
                effective_config=config,
                task=task(),
                budget=budget,
                artifacts=artifacts,
            )
            hypotheses = provider.propose(hypothesis_request())
            self.assertEqual(len(hypotheses), 1)
            metadata = provider.responses[0].metadata
            self.assertTrue(metadata["pricing_estimation_attempted"])
            self.assertEqual(
                metadata["pricing_snapshot_sha256"],
                snapshot.pricing_snapshot_sha256,
            )
            self.assertEqual(metadata["pricing_estimation_quality"], "unavailable")
            self.assertFalse(metadata["pricing_amount_available"])
            self.assertNotIn("pricing_source", metadata)
            self.assertNotIn("pricing_version", metadata)

    def test_provider_rejects_structural_request_before_call(self):
        with tempfile.TemporaryDirectory() as directory:
            registry, config, fake, budget, artifacts = endpoint([response(analysis_json())], directory)
            provider = BottleneckModelHypothesisProvider(
                registry=registry, effective_config=config, task=task(), budget=budget, artifacts=artifacts
            )
            with self.assertRaises(ValueError):
                provider.propose(hypothesis_request(level=OptimizationLevel.STRUCTURAL, supporting_evidence_ids=()))
            self.assertEqual(fake.calls, [])

    def test_provider_requires_exact_parent_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            registry, config, fake, budget, artifacts = endpoint([response(analysis_json())], directory)
            provider = BottleneckModelHypothesisProvider(
                registry=registry, effective_config=config, task=task(), budget=budget, artifacts=artifacts
            )
            with self.assertRaises(BottleneckModelContractError):
                provider.propose(hypothesis_request(supporting_evidence_ids=("invented",)))
            self.assertEqual(fake.calls, [])

    def test_invalid_model_json_is_audited(self):
        with tempfile.TemporaryDirectory() as directory:
            registry, config, _, budget, artifacts = endpoint([response("not json")], directory)
            provider = BottleneckModelHypothesisProvider(
                registry=registry, effective_config=config, task=task(), budget=budget, artifacts=artifacts
            )
            with self.assertRaises(BottleneckModelContractError):
                provider.propose(hypothesis_request())
            record = json.loads(artifacts.path.read_text().splitlines()[0])
            self.assertFalse(record["response_valid"])
            self.assertEqual(record["call_kind"], "bottleneck_analysis")

    def test_provider_exception_is_audited(self):
        with tempfile.TemporaryDirectory() as directory:
            registry, config, _, budget, artifacts = endpoint([RuntimeError("transport")], directory)
            provider = BottleneckModelHypothesisProvider(
                registry=registry, effective_config=config, task=task(), budget=budget, artifacts=artifacts
            )
            with self.assertRaises(RuntimeError):
                provider.propose(hypothesis_request())
            record = json.loads(artifacts.path.read_text().splitlines()[0])
            self.assertEqual(record["error_code"], "RuntimeError")
            self.assertIsNone(record["response_sha256"])


class FakeQualifier:
    name = "fake-qualifier"
    uses_vitis = False

    def __init__(self, latency=90, ii=1):
        self.calls = []
        self.executor = FakeCandidateExecutor(
            default_outcome=FakeExecutionOutcome(
                latency_cycles_max=latency,
                initiation_interval_max=ii,
                comparison_context_identity_sha256=CONTEXT,
            )
        )

    def qualify(self, request, source):
        self.calls.append((request, source))
        return self.executor.execute(request).qualification


class BadQualifier:
    name = "bad-qualifier"
    uses_vitis = False

    def qualify(self, request, source):
        return object()


class BottleneckGenerationAndIntegrationTests(unittest.TestCase):
    def test_generator_accepts_complete_changed_source(self):
        with tempfile.TemporaryDirectory() as directory:
            hyp = provider_hypothesis(Path(directory) / "analysis")
            registry, config, fake, budget, artifacts = endpoint(
                [response("```cpp\n" + REWRITE + "```")], Path(directory) / "rewrite"
            )
            generator = BottleneckModelCandidateGenerator(
                registry=registry, effective_config=config, task=task(), budget=budget, artifacts=artifacts
            )
            result = generator.generate(execution_request(hyp))
            self.assertEqual(result.source, REWRITE.strip().encode())
            self.assertEqual(len(fake.calls), 1)

    def test_generator_rejects_patch_response(self):
        with tempfile.TemporaryDirectory() as directory:
            hyp = provider_hypothesis(Path(directory) / "analysis")
            registry, config, _, budget, artifacts = endpoint(
                [response("```cpp\n@@ -1 +1 @@\n```")], Path(directory) / "rewrite"
            )
            generator = BottleneckModelCandidateGenerator(
                registry=registry, effective_config=config, task=task(), budget=budget, artifacts=artifacts
            )
            with self.assertRaises(CandidateResponseError):
                generator.generate(execution_request(hyp))

    def test_generator_rejects_changed_top_interface(self):
        changed = REWRITE.replace("void top(int *a, int n)", "void top(int *a, long n)")
        with tempfile.TemporaryDirectory() as directory:
            hyp = provider_hypothesis(Path(directory) / "analysis")
            registry, config, _, budget, artifacts = endpoint(
                [response("```cpp\n" + changed + "```")], Path(directory) / "rewrite"
            )
            generator = BottleneckModelCandidateGenerator(
                registry=registry, effective_config=config, task=task(), budget=budget, artifacts=artifacts
            )
            with self.assertRaises(CandidateResponseError):
                generator.generate(execution_request(hyp))

    def test_executor_delegates_qualification(self):
        with tempfile.TemporaryDirectory() as directory:
            hyp = provider_hypothesis(Path(directory) / "analysis")
            registry, config, _, budget, artifacts = endpoint(
                [response("```cpp\n" + REWRITE + "```")], Path(directory) / "rewrite"
            )
            qualifier = FakeQualifier()
            executor = BottleneckModelCandidateExecutor(
                generator=BottleneckModelCandidateGenerator(
                    registry=registry, effective_config=config, task=task(), budget=budget, artifacts=artifacts
                ),
                qualifier=qualifier,
            )
            result = executor.execute(execution_request(hyp))
            self.assertEqual(result.source, REWRITE.strip().encode())
            self.assertEqual(len(qualifier.calls), 1)

    def test_executor_rejects_invalid_qualifier_result(self):
        with tempfile.TemporaryDirectory() as directory:
            hyp = provider_hypothesis(Path(directory) / "analysis")
            registry, config, _, budget, artifacts = endpoint(
                [response("```cpp\n" + REWRITE + "```")], Path(directory) / "rewrite"
            )
            executor = BottleneckModelCandidateExecutor(
                generator=BottleneckModelCandidateGenerator(
                    registry=registry, effective_config=config, task=task(), budget=budget, artifacts=artifacts
                ),
                qualifier=BadQualifier(),
            )
            with self.assertRaises(TypeError):
                executor.execute(execution_request(hyp))

    def test_state_machine_bottleneck_step_uses_two_model_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            budget = BudgetManager(BudgetLimits(max_llm_calls=2), clock=lambda: 0.0)
            hr, hc, hp, _, artifacts = endpoint([response(analysis_json())], root / "model", budget=budget)
            gr, gc, gp, _, _ = endpoint([response("```cpp\n" + REWRITE + "```")], root / "model", budget=budget)
            provider = BottleneckModelHypothesisProvider(
                registry=hr, effective_config=hc, task=task(), budget=budget, artifacts=artifacts
            )
            executor = BottleneckModelCandidateExecutor(
                generator=BottleneckModelCandidateGenerator(
                    registry=gr, effective_config=gc, task=task(), budget=budget, artifacts=artifacts
                ),
                qualifier=FakeQualifier(),
            )
            base = parent_record()
            state = OptimizerState.initial(run_id="run-s35").with_qualified_baseline(base)
            state = replace(
                state,
                best_ppa_candidate_id="baseline",
                current_level=OptimizationLevel.BOTTLENECK,
                current_round=1,
            )
            writer = OptimizerCheckpointWriter(root / "optimizer")
            writer.write_candidate_source(base, SOURCE.encode())
            result = DeterministicOptimizerStateMachine(
                state=state,
                candidates={"baseline": base},
                checkpoint_writer=writer,
                provider=provider,
                executor=executor,
                budget=budget,
                trace=TraceRecorder("run-s35", clock=fixed_clock),
                clock=fixed_clock,
            ).step()
            self.assertEqual(result.candidates["cand-1"].status, CandidateStatus.ACCEPTED)
            self.assertEqual(result.candidates["cand-1"].level, OptimizationLevel.BOTTLENECK)
            self.assertEqual(result.budget_usage["llm_calls"], 2)
            self.assertEqual(len(hp.calls), 1)
            self.assertEqual(len(gp.calls), 1)

    def test_state_machine_budget_stops_before_rewrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            budget = BudgetManager(BudgetLimits(max_llm_calls=1), clock=lambda: 0.0)
            hr, hc, _, _, artifacts = endpoint([response(analysis_json())], root / "model", budget=budget)
            gr, gc, gp, _, _ = endpoint([response("```cpp\n" + REWRITE + "```")], root / "model", budget=budget)
            provider = BottleneckModelHypothesisProvider(
                registry=hr, effective_config=hc, task=task(), budget=budget, artifacts=artifacts
            )
            executor = BottleneckModelCandidateExecutor(
                generator=BottleneckModelCandidateGenerator(
                    registry=gr, effective_config=gc, task=task(), budget=budget, artifacts=artifacts
                ),
                qualifier=FakeQualifier(),
            )
            base = parent_record()
            state = OptimizerState.initial(run_id="run-s35-budget").with_qualified_baseline(base)
            state = replace(
                state,
                best_ppa_candidate_id="baseline",
                current_level=OptimizationLevel.BOTTLENECK,
                current_round=1,
            )
            writer = OptimizerCheckpointWriter(root / "optimizer")
            writer.write_candidate_source(base, SOURCE.encode())
            result = DeterministicOptimizerStateMachine(
                state=state,
                candidates={"baseline": base},
                checkpoint_writer=writer,
                provider=provider,
                executor=executor,
                budget=budget,
                trace=TraceRecorder("run-s35-budget", clock=fixed_clock),
                clock=fixed_clock,
            ).step()
            self.assertEqual(result.terminal_status.value, "budget_exhausted_with_best_correct")
            self.assertEqual(gp.calls, [])
            self.assertEqual(result.state.executed_candidate_count, 0)


if __name__ == "__main__":
    unittest.main()
