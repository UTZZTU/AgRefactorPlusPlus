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
    CandidateExecutionRequest,
    CandidateRecord,
    CandidateStatus,
    FakeCandidateExecutor,
    FakeExecutionOutcome,
    HypothesisRequest,
    OptimizationLevel,
    PpaEvidence,
    PpaReportFormat,
    PpaResourceUsage,
    PragmaActionRecord,
    PragmaAnalysisResponseContract,
    PragmaConfidence,
    PragmaKind,
    PragmaModelArtifactWriter,
    PragmaModelCandidateExecutor,
    PragmaModelCandidateGenerator,
    PragmaModelContractError,
    PragmaModelHypothesisProvider,
    PragmaTargetKind,
)
from agrefactor.runtime.budget import BudgetManager


FIXED_TIME = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
SOURCE = """#include <stdint.h>\nvoid top(int *a, int n) {\n    for (int i = 0; i < n; ++i) { a[i] = a[i] + 1; }\n}\n"""
REWRITE = """#include <stdint.h>\nvoid top(int *a, int n) {\n#pragma HLS PIPELINE II=1\n    for (int i = 0; i < n; ++i) { a[i] = a[i] + 1; }\n}\n"""
CONTEXT = "6" * 64


def fixed_clock():
    return FIXED_TIME


def task():
    return TaskSpec(
        task_id="s36-task",
        kernel_path="kernel.cpp",
        kernel_name="top",
        mode=RunMode.OPTIMIZE,
    )


def ppa(candidate_id="baseline", latency=128, ii=4):
    return PpaEvidence(
        evidence_id=f"ppa-{candidate_id}",
        parser_profile="s36-test",
        report_format=PpaReportFormat.XML,
        report_relative_path=f"reports/{candidate_id}_csynth.xml",
        report_sha256=hashlib.sha256(candidate_id.encode()).hexdigest(),
        comparison_context_identity_sha256=CONTEXT,
        latency_cycles_min=120,
        latency_cycles_max=latency,
        initiation_interval_min=4,
        initiation_interval_max=ii,
        target_clock_period_ns=5.0,
        achieved_clock_period_ns=4.2,
        resources_used=PpaResourceUsage(
            bram_18k=2, dsp=4, ff=900, lut=700, uram=0
        ),
        resources_available=PpaResourceUsage(
            bram_18k=100, dsp=200, ff=100000, lut=50000, uram=20
        ),
        max_resource_utilization_ratio=0.02,
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


def analysis_json(
    *,
    kind="pipeline",
    target_kind="loop",
    target_ref="top.loop_i",
    parameters=None,
    confidence="high",
    hypotheses=True,
    signal="initiation_interval_max",
):
    if parameters is None:
        parameters = {"ii": 1}
    unknown = kind == "unknown"
    actions = [
        {
            "kind": kind,
            "target_kind": "unknown" if unknown else target_kind,
            "target_ref": None if unknown else target_ref,
            "parameters": {} if unknown else parameters,
            "claim": (
                "Evidence is insufficient for a safe pragma action."
                if unknown
                else "Pipeline the reported high-II loop with II=1."
            ),
            "confidence": confidence,
            "supporting_evidence_ids": [] if unknown else ["ppa-baseline"],
            "signal_fields": [] if unknown else [signal],
        }
    ]
    values = []
    if hypotheses:
        values.append(
            {
                "action_index": 1,
                "claim": "Add one pipeline directive to the selected loop.",
                "supporting_evidence_ids": ["ppa-baseline"],
                "expected_benefit": {"metric": "latency", "direction": "decrease"},
                "risk": "medium",
                "modification_scope": ["top.loop_i pipeline directive only"],
                "verification_plan": ["preflight", "public", "csynth", "hidden"],
            }
        )
    return json.dumps(
        {"schema_version": 1, "actions": actions, "hypotheses": values}
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


def endpoint(values, root, budget=None, parameters=None, pricing_snapshot=None):
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
        PragmaModelArtifactWriter(root, clock=fixed_clock),
    )


def hypothesis_request(**updates):
    values = {
        "run_id": "run-s36",
        "level": OptimizationLevel.PRAGMA,
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
    registry, config, _, budget, artifacts = endpoint(
        [response(analysis_json())], root
    )
    provider = PragmaModelHypothesisProvider(
        registry=registry,
        effective_config=config,
        task=task(),
        budget=budget,
        artifacts=artifacts,
    )
    return provider.propose(hypothesis_request())[0]


def execution_request(hypothesis, **updates):
    values = {
        "run_id": "run-s36",
        "sequence": 1,
        "candidate_id": "cand-1",
        "level": OptimizationLevel.PRAGMA,
        "round_number": 1,
        "parent_candidate": parent_record(),
        "parent_source": SOURCE.encode(),
        "hypothesis": hypothesis,
        "budget_before": {},
    }
    values.update(updates)
    return CandidateExecutionRequest(**values)


class PragmaResponseContractTests(unittest.TestCase):
    def contract(self):
        return PragmaAnalysisResponseContract(
            max_actions=3,
            max_hypotheses=3,
            allowed_evidence_ids=("ppa-baseline",),
        )

    def test_raw_json_is_accepted(self):
        parsed = self.contract().parse(analysis_json())
        self.assertEqual(parsed.actions[0].kind, PragmaKind.PIPELINE)
        self.assertEqual(parsed.actions[0].parameters, {"ii": 1})
        self.assertEqual(len(parsed.hypotheses), 1)

    def test_single_json_fence_is_accepted(self):
        parsed = self.contract().parse("```json\n" + analysis_json() + "\n```")
        self.assertEqual(parsed.actions[0].target_kind, PragmaTargetKind.LOOP)

    def test_commentary_is_rejected(self):
        with self.assertRaises(PragmaModelContractError):
            self.contract().parse("Result: " + analysis_json())

    def test_extra_top_level_key_is_rejected(self):
        payload = json.loads(analysis_json())
        payload["extra"] = True
        with self.assertRaises(PragmaModelContractError):
            self.contract().parse(json.dumps(payload))

    def test_unknown_action_without_hypothesis_is_valid(self):
        parsed = self.contract().parse(
            analysis_json(kind="unknown", confidence="low", hypotheses=False)
        )
        self.assertEqual(parsed.actions[0].kind, PragmaKind.UNKNOWN)
        self.assertEqual(parsed.hypotheses, ())

    def test_unknown_action_requires_low_confidence(self):
        with self.assertRaises(PragmaModelContractError):
            self.contract().parse(
                analysis_json(kind="unknown", confidence="high", hypotheses=False)
            )

    def test_unknown_action_cannot_have_hypothesis(self):
        with self.assertRaises(PragmaModelContractError):
            self.contract().parse(
                analysis_json(kind="unknown", confidence="low", hypotheses=True)
            )

    def test_unknown_signal_field_is_rejected(self):
        with self.assertRaises(PragmaModelContractError):
            self.contract().parse(analysis_json(signal="source_contains_pragma"))

    def test_resource_container_aliases_are_rejected(self):
        for signal in ("resources_used", "resources_available"):
            with self.subTest(signal=signal):
                with self.assertRaises(PragmaModelContractError):
                    self.contract().parse(analysis_json(signal=signal))

    def test_hypothesis_evidence_must_be_subset(self):
        payload = json.loads(analysis_json())
        payload["hypotheses"][0]["supporting_evidence_ids"] = ["invented"]
        with self.assertRaises(PragmaModelContractError):
            self.contract().parse(json.dumps(payload))

    def test_missing_action_reference_is_rejected(self):
        payload = json.loads(analysis_json())
        payload["hypotheses"][0]["action_index"] = 2
        with self.assertRaises(PragmaModelContractError):
            self.contract().parse(json.dumps(payload))

    def test_too_many_actions_are_rejected(self):
        payload = json.loads(analysis_json())
        payload["actions"] *= 4
        with self.assertRaises(PragmaModelContractError):
            self.contract().parse(json.dumps(payload))

    def test_extra_action_key_is_rejected(self):
        payload = json.loads(analysis_json())
        payload["actions"][0]["source_match"] = "for"
        with self.assertRaises(PragmaModelContractError):
            self.contract().parse(json.dumps(payload))

    def test_wrong_verification_order_is_rejected(self):
        payload = json.loads(analysis_json())
        payload["hypotheses"][0]["verification_plan"] = ["csynth", "public"]
        with self.assertRaises(PragmaModelContractError):
            self.contract().parse(json.dumps(payload))

    def test_target_kind_must_match_directive(self):
        with self.assertRaises(PragmaModelContractError):
            self.contract().parse(analysis_json(target_kind="array"))

    def test_generic_resource_directive_is_rejected(self):
        with self.assertRaises(PragmaModelContractError):
            self.contract().parse(analysis_json(kind="resource"))


class PragmaParameterTests(unittest.TestCase):
    def contract(self):
        return PragmaAnalysisResponseContract(
            max_actions=3,
            max_hypotheses=3,
            allowed_evidence_ids=("ppa-baseline",),
        )

    def test_unroll_empty_parameters_means_complete_proposal(self):
        parsed = self.contract().parse(
            analysis_json(kind="unroll", parameters={})
        )
        self.assertEqual(parsed.actions[0].parameters, {})

    def test_unroll_factor_must_be_positive(self):
        with self.assertRaises(PragmaModelContractError):
            self.contract().parse(
                analysis_json(kind="unroll", parameters={"factor": 0})
            )

    def test_complete_partition_rejects_factor(self):
        with self.assertRaises(PragmaModelContractError):
            self.contract().parse(
                analysis_json(
                    kind="array_partition",
                    target_kind="array",
                    parameters={"type": "complete", "factor": 2},
                )
            )

    def test_cyclic_partition_requires_factor(self):
        with self.assertRaises(PragmaModelContractError):
            self.contract().parse(
                analysis_json(
                    kind="array_partition",
                    target_kind="array",
                    parameters={"type": "cyclic"},
                )
            )

    def test_dataflow_rejects_parameters(self):
        with self.assertRaises(PragmaModelContractError):
            self.contract().parse(
                analysis_json(
                    kind="dataflow",
                    target_kind="region",
                    parameters={"ii": 1},
                )
            )

    def test_inline_requires_explicit_mode(self):
        with self.assertRaises(PragmaModelContractError):
            self.contract().parse(
                analysis_json(kind="inline", target_kind="function", parameters={})
            )

    def test_bind_storage_exact_schema(self):
        parsed = self.contract().parse(
            analysis_json(
                kind="bind_storage",
                target_kind="array",
                parameters={"type": "ram_2p", "impl": "bram", "latency": 1},
            )
        )
        self.assertEqual(parsed.actions[0].parameters["impl"], "bram")

    def test_bind_op_rejects_unknown_impl(self):
        with self.assertRaises(PragmaModelContractError):
            self.contract().parse(
                analysis_json(
                    kind="bind_op",
                    target_kind="operation",
                    parameters={"op": "mul", "impl": "magic"},
                )
            )


class PragmaArtifactTests(unittest.TestCase):
    def action(self):
        return PragmaActionRecord(
            action_id="pragma-baseline-r1-1",
            parent_candidate_id="baseline",
            kind=PragmaKind.PIPELINE,
            target_kind=PragmaTargetKind.LOOP,
            target_ref="top.loop_i",
            parameters={"ii": 1},
            claim="Pipeline the selected loop.",
            confidence=PragmaConfidence.HIGH,
            supporting_evidence_ids=("ppa-baseline",),
            signal_fields=("initiation_interval_max",),
            model_identity={"provider": "fixture"},
            prompt_identity_sha256="a" * 64,
        )

    def test_action_artifact_is_immutable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = PragmaModelArtifactWriter(directory, clock=fixed_clock)
            first = writer.write_action(self.action())
            second = writer.write_action(self.action())
            self.assertEqual(first, second)
            payload = json.loads(first.read_text())
            self.assertFalse(payload["authoritative"])
            self.assertEqual(payload["action_source"], "model_proposal")

    def test_action_artifact_rejects_conflicting_content(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = PragmaModelArtifactWriter(directory, clock=fixed_clock)
            writer.write_action(self.action())
            with self.assertRaises(FileExistsError):
                writer.write_action(replace(self.action(), target_ref="other.loop"))

    def test_action_rejects_agent_unsafe_claim(self):
        with self.assertRaises(PragmaModelContractError):
            replace(self.action(), claim="Read the Hidden diagnostic")

    def test_action_cannot_be_authoritative(self):
        with self.assertRaises(ValueError):
            replace(self.action(), authoritative=True)


class PragmaProviderTests(unittest.TestCase):
    def test_provider_builds_hypothesis_and_action(self):
        with tempfile.TemporaryDirectory() as directory:
            registry, config, fake, budget, artifacts = endpoint(
                [response(analysis_json())], directory
            )
            provider = PragmaModelHypothesisProvider(
                registry=registry,
                effective_config=config,
                task=task(),
                budget=budget,
                artifacts=artifacts,
            )
            hypotheses = provider.propose(hypothesis_request())
            self.assertEqual(len(hypotheses), 1)
            self.assertEqual(hypotheses[0].level, OptimizationLevel.PRAGMA)
            self.assertFalse(
                hypotheses[0].model_identity["pragma_action"]["authoritative"]
            )
            self.assertEqual(provider.actions[0].kind, PragmaKind.PIPELINE)
            self.assertEqual(len(fake.calls), 1)

    def test_provider_accepts_unknown_and_returns_no_hypothesis(self):
        with tempfile.TemporaryDirectory() as directory:
            registry, config, _, budget, artifacts = endpoint(
                [response(analysis_json(kind="unknown", confidence="low", hypotheses=False))],
                directory,
            )
            provider = PragmaModelHypothesisProvider(
                registry=registry,
                effective_config=config,
                task=task(),
                budget=budget,
                artifacts=artifacts,
            )
            self.assertEqual(provider.propose(hypothesis_request()), ())
            self.assertEqual(provider.actions[0].kind, PragmaKind.UNKNOWN)

    def test_provider_records_observed_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            registry, config, _, budget, artifacts = endpoint(
                [response(analysis_json(), prompt_tokens=20, completion_tokens=7)],
                directory,
            )
            provider = PragmaModelHypothesisProvider(
                registry=registry,
                effective_config=config,
                task=task(),
                budget=budget,
                artifacts=artifacts,
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
            provider = PragmaModelHypothesisProvider(
                registry=registry,
                effective_config=config,
                task=task(),
                budget=budget,
                artifacts=artifacts,
            )
            provider.propose(hypothesis_request())
            metadata = provider.responses[0].metadata
            self.assertTrue(metadata["pricing_estimation_attempted"])
            self.assertEqual(
                metadata["pricing_snapshot_sha256"],
                snapshot.pricing_snapshot_sha256,
            )
            self.assertNotIn("pricing_source", metadata)
            self.assertNotIn("pricing_version", metadata)

    def test_provider_rejects_bottleneck_request_before_call(self):
        with tempfile.TemporaryDirectory() as directory:
            registry, config, fake, budget, artifacts = endpoint(
                [response(analysis_json())], directory
            )
            provider = PragmaModelHypothesisProvider(
                registry=registry,
                effective_config=config,
                task=task(),
                budget=budget,
                artifacts=artifacts,
            )
            with self.assertRaises(ValueError):
                provider.propose(
                    hypothesis_request(level=OptimizationLevel.BOTTLENECK)
                )
            self.assertEqual(fake.calls, [])

    def test_provider_requires_exact_parent_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            registry, config, fake, budget, artifacts = endpoint(
                [response(analysis_json())], directory
            )
            provider = PragmaModelHypothesisProvider(
                registry=registry,
                effective_config=config,
                task=task(),
                budget=budget,
                artifacts=artifacts,
            )
            with self.assertRaises(PragmaModelContractError):
                provider.propose(
                    hypothesis_request(supporting_evidence_ids=("invented",))
                )
            self.assertEqual(fake.calls, [])

    def test_invalid_model_json_is_audited(self):
        with tempfile.TemporaryDirectory() as directory:
            registry, config, _, budget, artifacts = endpoint(
                [response("not json")], directory
            )
            provider = PragmaModelHypothesisProvider(
                registry=registry,
                effective_config=config,
                task=task(),
                budget=budget,
                artifacts=artifacts,
            )
            with self.assertRaises(PragmaModelContractError):
                provider.propose(hypothesis_request())
            record = json.loads(artifacts.path.read_text().splitlines()[0])
            self.assertFalse(record["response_valid"])
            self.assertEqual(record["call_kind"], "pragma_analysis")

    def test_provider_exception_is_audited(self):
        with tempfile.TemporaryDirectory() as directory:
            registry, config, _, budget, artifacts = endpoint(
                [RuntimeError("transport")], directory
            )
            provider = PragmaModelHypothesisProvider(
                registry=registry,
                effective_config=config,
                task=task(),
                budget=budget,
                artifacts=artifacts,
            )
            with self.assertRaises(RuntimeError):
                provider.propose(hypothesis_request())
            record = json.loads(artifacts.path.read_text().splitlines()[0])
            self.assertEqual(record["error_code"], "RuntimeError")
            self.assertIsNone(record["response_sha256"])


class FakeQualifier:
    name = "fake-qualifier"
    uses_vitis = False

    def __init__(self):
        self.calls = []
        self.executor = FakeCandidateExecutor(
            default_outcome=FakeExecutionOutcome(
                latency_cycles_max=90,
                initiation_interval_max=1,
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


class PragmaGenerationAndIntegrationTests(unittest.TestCase):
    def test_generator_accepts_complete_changed_source(self):
        with tempfile.TemporaryDirectory() as directory:
            hyp = provider_hypothesis(Path(directory) / "analysis")
            registry, config, fake, budget, artifacts = endpoint(
                [response("```cpp\n" + REWRITE + "```")],
                Path(directory) / "rewrite",
            )
            generator = PragmaModelCandidateGenerator(
                registry=registry,
                effective_config=config,
                task=task(),
                budget=budget,
                artifacts=artifacts,
            )
            result = generator.generate(execution_request(hyp))
            self.assertEqual(result.source, REWRITE.strip().encode())
            self.assertEqual(len(fake.calls), 1)

    def test_generator_rejects_patch_response(self):
        with tempfile.TemporaryDirectory() as directory:
            hyp = provider_hypothesis(Path(directory) / "analysis")
            registry, config, _, budget, artifacts = endpoint(
                [response("```cpp\n@@ -1 +1 @@\n```")],
                Path(directory) / "rewrite",
            )
            generator = PragmaModelCandidateGenerator(
                registry=registry,
                effective_config=config,
                task=task(),
                budget=budget,
                artifacts=artifacts,
            )
            with self.assertRaises(CandidateResponseError):
                generator.generate(execution_request(hyp))

    def test_generator_rejects_changed_top_interface(self):
        changed = REWRITE.replace("void top(int *a, int n)", "void top(int *a, long n)")
        with tempfile.TemporaryDirectory() as directory:
            hyp = provider_hypothesis(Path(directory) / "analysis")
            registry, config, _, budget, artifacts = endpoint(
                [response("```cpp\n" + changed + "```")],
                Path(directory) / "rewrite",
            )
            generator = PragmaModelCandidateGenerator(
                registry=registry,
                effective_config=config,
                task=task(),
                budget=budget,
                artifacts=artifacts,
            )
            with self.assertRaises(CandidateResponseError):
                generator.generate(execution_request(hyp))

    def test_generator_rejects_non_pragma_request(self):
        with tempfile.TemporaryDirectory() as directory:
            hyp = provider_hypothesis(Path(directory) / "analysis")
            registry, config, fake, budget, artifacts = endpoint(
                [response("```cpp\n" + REWRITE + "```")],
                Path(directory) / "rewrite",
            )
            generator = PragmaModelCandidateGenerator(
                registry=registry,
                effective_config=config,
                task=task(),
                budget=budget,
                artifacts=artifacts,
            )
            with self.assertRaises(ValueError):
                generator.generate(
                    execution_request(
                        hyp,
                        level=OptimizationLevel.BOTTLENECK,
                    )
                )
            self.assertEqual(fake.calls, [])

    def test_executor_delegates_qualification(self):
        with tempfile.TemporaryDirectory() as directory:
            hyp = provider_hypothesis(Path(directory) / "analysis")
            registry, config, _, budget, artifacts = endpoint(
                [response("```cpp\n" + REWRITE + "```")],
                Path(directory) / "rewrite",
            )
            qualifier = FakeQualifier()
            executor = PragmaModelCandidateExecutor(
                generator=PragmaModelCandidateGenerator(
                    registry=registry,
                    effective_config=config,
                    task=task(),
                    budget=budget,
                    artifacts=artifacts,
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
                [response("```cpp\n" + REWRITE + "```")],
                Path(directory) / "rewrite",
            )
            executor = PragmaModelCandidateExecutor(
                generator=PragmaModelCandidateGenerator(
                    registry=registry,
                    effective_config=config,
                    task=task(),
                    budget=budget,
                    artifacts=artifacts,
                ),
                qualifier=BadQualifier(),
            )
            with self.assertRaises(TypeError):
                executor.execute(execution_request(hyp))


if __name__ == "__main__":
    unittest.main()
