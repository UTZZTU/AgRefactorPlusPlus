import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from agrefactor.config import RunMode, TaskSpec
from agrefactor.models import (
    ModelArtifactKind,
    ModelProvider,
    ModelRegistry,
    ModelResponse,
    ModelSpec,
    TokenUsage,
    CandidateResponseError,
)
from agrefactor.optimization import (
    CandidateExecutionRequest,
    CandidateRecord,
    CandidateStatus,
    DeterministicOptimizerStateMachine,
    FakeCandidateExecutor,
    FakeExecutionOutcome,
    HypothesisRecord,
    HypothesisRequest,
    HypothesisRisk,
    OptimizationLevel,
    OptimizerCheckpointWriter,
    OptimizerState,
    PpaEvidence,
    PpaReportFormat,
    PpaResourceUsage,
    StructuralCandidateGenerationResult,
    StructuralHypothesisResponseContract,
    StructuralModelArtifactWriter,
    StructuralModelCandidateExecutor,
    StructuralModelCandidateGenerator,
    StructuralModelContractError,
    StructuralModelHypothesisProvider,
)
from agrefactor.runtime.budget import BudgetLimits, BudgetManager
from agrefactor.runtime.trace import TraceRecorder


FIXED_TIME = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
SOURCE = """#include <stdint.h>\nvoid top(int *a, int n) {\n    for (int i = 0; i < n; ++i) a[i] += 1;\n}\n"""
REWRITE = """#include <stdint.h>\nvoid top(int *a, int n) {\n    int i = 0;\n    for (; i + 1 < n; i += 2) { a[i] += 1; a[i + 1] += 1; }\n    if (i < n) a[i] += 1;\n}\n"""
CONTEXT = "d" * 64


def fixed_clock():
    return FIXED_TIME


def task():
    return TaskSpec(
        task_id="s34-task",
        kernel_path="kernel.cpp",
        kernel_name="top",
        mode=RunMode.OPTIMIZE,
    )


def hypothesis_json(count=1):
    items = []
    for index in range(count):
        items.append(
            {
                "claim": f"Process two adjacent elements per loop iteration {index}",
                "expected_benefit": {"metric": "latency", "direction": "decrease"},
                "risk": "low",
                "modification_scope": ["loop organization"],
                "verification_plan": ["preflight", "public", "csynth", "hidden"],
            }
        )
    return json.dumps({"schema_version": 1, "hypotheses": items})


def structural_hypothesis(parent="baseline"):
    return HypothesisRecord(
        hypothesis_id="hyp-structural-r1-1",
        level=OptimizationLevel.STRUCTURAL,
        parent_candidate_id=parent,
        claim="Process two adjacent elements per loop iteration",
        supporting_evidence_ids=(),
        expected_benefit={"metric": "latency", "direction": "decrease"},
        risk=HypothesisRisk.LOW,
        modification_scope=("loop organization",),
        verification_plan=("preflight", "public", "csynth", "hidden"),
        model_identity={"provider": "fixture", "network": False},
        prompt_identity_sha256="a" * 64,
    )


def ppa(candidate_id="baseline", latency=100):
    return PpaEvidence(
        evidence_id=f"ppa-{candidate_id}",
        parser_profile="s34-test",
        report_format=PpaReportFormat.XML,
        report_relative_path=f"reports/{candidate_id}.xml",
        report_sha256=hashlib.sha256(candidate_id.encode()).hexdigest(),
        comparison_context_identity_sha256=CONTEXT,
        latency_cycles_min=latency,
        latency_cycles_max=latency,
        initiation_interval_min=1,
        initiation_interval_max=1,
        target_clock_period_ns=5.0,
        achieved_clock_period_ns=4.0,
        resources_used=PpaResourceUsage(bram_18k=1, dsp=1, ff=10, lut=10, uram=0),
        resources_available=PpaResourceUsage(bram_18k=100, dsp=100, ff=1000, lut=1000, uram=10),
        max_resource_utilization_ratio=0.1,
        objective_feasible=True,
    )


def baseline():
    record = CandidateRecord(
        candidate_id="baseline",
        sequence=0,
        parent_candidate_id=None,
        hypothesis_id=None,
        level=None,
        source_sha256=hashlib.sha256(SOURCE.encode()).hexdigest(),
        source_artifact="candidates/baseline/source.cpp",
        status=CandidateStatus.ACCEPTED,
        ppa=ppa().to_dict(),
    )
    state = OptimizerState.initial(run_id="run-s34").with_qualified_baseline(record)
    state = replace(state, best_ppa_candidate_id="baseline")
    return state, record


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


def endpoint(values, root, budget=None, parameters=None):
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
    )
    return (
        registry,
        config,
        provider,
        budget or BudgetManager(clock=lambda: 0.0),
        StructuralModelArtifactWriter(root, clock=fixed_clock),
    )


def parent_record():
    return CandidateRecord(
        candidate_id="baseline",
        sequence=0,
        parent_candidate_id=None,
        hypothesis_id=None,
        level=None,
        source_sha256=hashlib.sha256(SOURCE.encode()).hexdigest(),
        source_artifact="candidates/baseline/source.cpp",
        status=CandidateStatus.ACCEPTED,
        ppa=ppa().to_dict(),
    )


def hypothesis_request(**updates):
    values = {
        "run_id": "run-s34",
        "level": OptimizationLevel.STRUCTURAL,
        "round_number": 1,
        "parent_candidate": parent_record(),
        "max_hypotheses": 3,
        "safe_context": {"policy": "safe-v1", "objective": "latency"},
        "parent_source": SOURCE.encode(),
    }
    values.update(updates)
    return HypothesisRequest(**values)


def execution_request(**updates):
    values = {
        "run_id": "run-s34",
        "sequence": 1,
        "candidate_id": "cand-1",
        "level": OptimizationLevel.STRUCTURAL,
        "round_number": 1,
        "parent_candidate": parent_record(),
        "parent_source": SOURCE.encode(),
        "hypothesis": structural_hypothesis(),
        "budget_before": {},
    }
    values.update(updates)
    return CandidateExecutionRequest(**values)


class StructuralHypothesisResponseContractTests(unittest.TestCase):
    def test_raw_json_is_accepted(self):
        values = StructuralHypothesisResponseContract(max_hypotheses=3).parse(hypothesis_json(2))
        self.assertEqual(len(values), 2)

    def test_single_json_fence_is_accepted(self):
        values = StructuralHypothesisResponseContract(max_hypotheses=3).parse(
            "```json\n" + hypothesis_json(1) + "\n```"
        )
        self.assertEqual(len(values), 1)

    def test_commentary_is_rejected(self):
        with self.assertRaises(StructuralModelContractError):
            StructuralHypothesisResponseContract(max_hypotheses=3).parse(
                "Here is the result: " + hypothesis_json(1)
            )

    def test_extra_top_level_key_is_rejected(self):
        payload = json.loads(hypothesis_json(1))
        payload["note"] = "extra"
        with self.assertRaises(StructuralModelContractError):
            StructuralHypothesisResponseContract(max_hypotheses=3).parse(json.dumps(payload))

    def test_too_many_hypotheses_are_rejected(self):
        with self.assertRaises(StructuralModelContractError):
            StructuralHypothesisResponseContract(max_hypotheses=2).parse(hypothesis_json(3))

    def test_extra_hypothesis_key_is_rejected(self):
        payload = json.loads(hypothesis_json(1))
        payload["hypotheses"][0]["hypothesis_id"] = "invented"
        with self.assertRaises(StructuralModelContractError):
            StructuralHypothesisResponseContract(max_hypotheses=3).parse(json.dumps(payload))

    def test_wrong_expected_benefit_is_rejected(self):
        payload = json.loads(hypothesis_json(1))
        payload["hypotheses"][0]["expected_benefit"]["metric"] = "area"
        with self.assertRaises(StructuralModelContractError):
            StructuralHypothesisResponseContract(max_hypotheses=3).parse(json.dumps(payload))

    def test_wrong_verification_order_is_rejected(self):
        payload = json.loads(hypothesis_json(1))
        payload["hypotheses"][0]["verification_plan"] = ["preflight", "csynth", "public", "hidden"]
        with self.assertRaises(StructuralModelContractError):
            StructuralHypothesisResponseContract(max_hypotheses=3).parse(json.dumps(payload))

    def test_empty_hypothesis_list_is_valid(self):
        values = StructuralHypothesisResponseContract(max_hypotheses=3).parse(hypothesis_json(0))
        self.assertEqual(values, ())


class StructuralModelArtifactWriterTests(unittest.TestCase):
    def test_writer_rejects_symlink_root(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target"
            target.mkdir()
            link = Path(directory) / "link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaises(ValueError):
                StructuralModelArtifactWriter(link)

    def test_writer_reloads_contiguous_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            registry, config, _, _, writer = endpoint([], directory)
            manifest = {"prompt_identity_sha256": "a" * 64, "hidden_test_source_isolation": "verified"}
            writer.append(
                call_kind="structural_hypothesis",
                effective_config=config,
                prompt_manifest=manifest,
                response=response(hypothesis_json()),
                response_valid=True,
                error_code=None,
            )
            second = StructuralModelArtifactWriter(directory, clock=fixed_clock)
            record = second.append(
                call_kind="structural_rewrite",
                effective_config=config,
                prompt_manifest={"prompt_identity_sha256": "b" * 64},
                response=response("```cpp\n" + REWRITE + "```") ,
                response_valid=True,
                error_code=None,
            )
            self.assertEqual(record.sequence, 2)

    def test_writer_rejects_unsafe_manifest_key(self):
        with tempfile.TemporaryDirectory() as directory:
            _, config, _, _, writer = endpoint([], directory)
            with self.assertRaises(ValueError):
                writer.append(
                    call_kind="structural_hypothesis",
                    effective_config=config,
                    prompt_manifest={"prompt_identity_sha256": "a" * 64, "hidden_report": "x"},
                    response=None,
                    response_valid=False,
                    error_code="x",
                )

    def test_writer_does_not_store_raw_response(self):
        with tempfile.TemporaryDirectory() as directory:
            registry, config, _, _, writer = endpoint([], directory)
            raw = hypothesis_json(1)
            writer.append(
                call_kind="structural_hypothesis",
                effective_config=config,
                prompt_manifest={"prompt_identity_sha256": "a" * 64},
                response=response(raw),
                response_valid=True,
                error_code=None,
            )
            text = writer.path.read_text(encoding="utf-8")
            self.assertNotIn(raw, text)
            self.assertIn(hashlib.sha256(raw.encode()).hexdigest(), text)


class StructuralModelHypothesisProviderTests(unittest.TestCase):
    def test_provider_builds_typed_deterministic_records(self):
        with tempfile.TemporaryDirectory() as directory:
            registry, config, fake, budget, artifacts = endpoint([response(hypothesis_json(2))], directory)
            provider = StructuralModelHypothesisProvider(
                registry=registry,
                effective_config=config,
                task=task(),
                budget=budget,
                artifacts=artifacts,
            )
            values = provider.propose(hypothesis_request())
            self.assertEqual([item.hypothesis_id for item in values], ["hyp-structural-r1-1", "hyp-structural-r1-2"])
            self.assertTrue(all(item.parent_candidate_id == "baseline" for item in values))
            self.assertTrue(all(item.model_identity["network"] is True for item in values))
            self.assertEqual(len(fake.calls), 1)

    def test_provider_records_observed_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            registry, config, _, budget, artifacts = endpoint([response(hypothesis_json(), prompt_tokens=7, completion_tokens=3)], directory)
            provider = StructuralModelHypothesisProvider(
                registry=registry, effective_config=config, task=task(), budget=budget, artifacts=artifacts
            )
            provider.propose(hypothesis_request())
            usage = budget.snapshot()
            self.assertEqual(usage.tokens, 10)
            self.assertEqual(usage.llm_calls, 0)

    def test_provider_rejects_nonstructural_request_before_call(self):
        with tempfile.TemporaryDirectory() as directory:
            registry, config, fake, budget, artifacts = endpoint([response(hypothesis_json())], directory)
            provider = StructuralModelHypothesisProvider(
                registry=registry, effective_config=config, task=task(), budget=budget, artifacts=artifacts
            )
            with self.assertRaises(ValueError):
                provider.propose(hypothesis_request(level=OptimizationLevel.BOTTLENECK))
            self.assertEqual(fake.calls, [])

    def test_provider_requires_parent_source(self):
        with tempfile.TemporaryDirectory() as directory:
            registry, config, fake, budget, artifacts = endpoint([response(hypothesis_json())], directory)
            provider = StructuralModelHypothesisProvider(
                registry=registry, effective_config=config, task=task(), budget=budget, artifacts=artifacts
            )
            with self.assertRaises(ValueError):
                provider.propose(hypothesis_request(parent_source=b""))
            self.assertEqual(fake.calls, [])

    def test_invalid_model_json_is_audited(self):
        with tempfile.TemporaryDirectory() as directory:
            registry, config, _, budget, artifacts = endpoint([response("not-json")], directory)
            provider = StructuralModelHypothesisProvider(
                registry=registry, effective_config=config, task=task(), budget=budget, artifacts=artifacts
            )
            with self.assertRaises(StructuralModelContractError):
                provider.propose(hypothesis_request())
            record = json.loads(artifacts.path.read_text(encoding="utf-8"))
            self.assertFalse(record["response_valid"])
            self.assertEqual(record["call_kind"], "structural_hypothesis")

    def test_provider_exception_is_audited(self):
        with tempfile.TemporaryDirectory() as directory:
            registry, config, _, budget, artifacts = endpoint([RuntimeError("transport")], directory)
            provider = StructuralModelHypothesisProvider(
                registry=registry, effective_config=config, task=task(), budget=budget, artifacts=artifacts
            )
            with self.assertRaises(RuntimeError):
                provider.propose(hypothesis_request())
            record = json.loads(artifacts.path.read_text(encoding="utf-8"))
            self.assertEqual(record["error_code"], "RuntimeError")
            self.assertIsNone(record["response_sha256"])


class FakeQualifier:
    name = "fake-qualifier"
    uses_vitis = False

    def __init__(self, latency=90):
        self.calls = []
        self.executor = FakeCandidateExecutor(
            default_outcome=FakeExecutionOutcome(
                latency_cycles_max=latency,
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


class StructuralCandidateGenerationAndIntegrationTests(unittest.TestCase):
    def test_generator_accepts_one_complete_changed_source(self):
        with tempfile.TemporaryDirectory() as directory:
            registry, config, fake, budget, artifacts = endpoint([response("```cpp\n" + REWRITE + "```")], directory)
            generator = StructuralModelCandidateGenerator(
                registry=registry, effective_config=config, task=task(), budget=budget, artifacts=artifacts
            )
            result = generator.generate(execution_request())
            self.assertEqual(result.candidate_code, REWRITE.strip())
            self.assertEqual(result.source, REWRITE.strip().encode())
            self.assertEqual(len(fake.calls), 1)

    def test_generator_rejects_patch_response(self):
        with tempfile.TemporaryDirectory() as directory:
            registry, config, _, budget, artifacts = endpoint([response("```cpp\n@@ -1 +1 @@\n```" )], directory)
            generator = StructuralModelCandidateGenerator(
                registry=registry, effective_config=config, task=task(), budget=budget, artifacts=artifacts
            )
            with self.assertRaises(CandidateResponseError):
                generator.generate(execution_request())

    def test_generator_rejects_changed_top_interface(self):
        changed = REWRITE.replace("void top(int *a, int n)", "void top(int *a, long n)")
        with tempfile.TemporaryDirectory() as directory:
            registry, config, _, budget, artifacts = endpoint([response("```cpp\n" + changed + "```")], directory)
            generator = StructuralModelCandidateGenerator(
                registry=registry, effective_config=config, task=task(), budget=budget, artifacts=artifacts
            )
            with self.assertRaises(CandidateResponseError):
                generator.generate(execution_request())

    def test_executor_delegates_qualification_after_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            registry, config, _, budget, artifacts = endpoint([response("```cpp\n" + REWRITE + "```")], directory)
            qualifier = FakeQualifier()
            executor = StructuralModelCandidateExecutor(
                generator=StructuralModelCandidateGenerator(
                    registry=registry, effective_config=config, task=task(), budget=budget, artifacts=artifacts
                ),
                qualifier=qualifier,
            )
            result = executor.execute(execution_request())
            self.assertEqual(result.source, REWRITE.strip().encode())
            self.assertEqual(len(qualifier.calls), 1)
            self.assertEqual(result.qualification.candidate_id, "cand-1")

    def test_executor_rejects_invalid_qualifier_result(self):
        with tempfile.TemporaryDirectory() as directory:
            registry, config, _, budget, artifacts = endpoint([response("```cpp\n" + REWRITE + "```")], directory)
            executor = StructuralModelCandidateExecutor(
                generator=StructuralModelCandidateGenerator(
                    registry=registry, effective_config=config, task=task(), budget=budget, artifacts=artifacts
                ),
                qualifier=BadQualifier(),
            )
            with self.assertRaises(TypeError):
                executor.execute(execution_request())

    def test_state_machine_step_uses_two_model_calls_and_accepts_qualified_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            budget = BudgetManager(BudgetLimits(max_llm_calls=2), clock=lambda: 0.0)
            hr, hc, hp, _, artifacts = endpoint([response(hypothesis_json())], root / "model", budget=budget)
            gr, gc, gp, _, _ = endpoint([response("```cpp\n" + REWRITE + "```")], root / "model", budget=budget)
            provider = StructuralModelHypothesisProvider(
                registry=hr, effective_config=hc, task=task(), budget=budget, artifacts=artifacts
            )
            generator = StructuralModelCandidateGenerator(
                registry=gr, effective_config=gc, task=task(), budget=budget, artifacts=artifacts
            )
            executor = StructuralModelCandidateExecutor(generator=generator, qualifier=FakeQualifier())
            state, base = baseline()
            writer = OptimizerCheckpointWriter(root / "optimizer")
            writer.write_candidate_source(base, SOURCE.encode())
            engine = DeterministicOptimizerStateMachine(
                state=state,
                candidates={"baseline": base},
                checkpoint_writer=writer,
                provider=provider,
                executor=executor,
                budget=budget,
                trace=TraceRecorder("run-s34", clock=fixed_clock),
                clock=fixed_clock,
            )
            result = engine.step()
            self.assertEqual(result.candidates["cand-1"].status, CandidateStatus.ACCEPTED)
            self.assertEqual(result.state.best_correct_candidate_id, "cand-1")
            self.assertEqual(result.budget_usage["llm_calls"], 2)
            self.assertEqual(len(hp.calls), 1)
            self.assertEqual(len(gp.calls), 1)

    def test_state_machine_budget_stops_before_rewrite_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            budget = BudgetManager(BudgetLimits(max_llm_calls=1), clock=lambda: 0.0)
            hr, hc, _, _, artifacts = endpoint([response(hypothesis_json())], root / "model", budget=budget)
            gr, gc, gp, _, _ = endpoint([response("```cpp\n" + REWRITE + "```")], root / "model", budget=budget)
            provider = StructuralModelHypothesisProvider(
                registry=hr, effective_config=hc, task=task(), budget=budget, artifacts=artifacts
            )
            executor = StructuralModelCandidateExecutor(
                generator=StructuralModelCandidateGenerator(
                    registry=gr, effective_config=gc, task=task(), budget=budget, artifacts=artifacts
                ),
                qualifier=FakeQualifier(),
            )
            state, base = baseline()
            writer = OptimizerCheckpointWriter(root / "optimizer")
            writer.write_candidate_source(base, SOURCE.encode())
            result = DeterministicOptimizerStateMachine(
                state=state,
                candidates={"baseline": base},
                checkpoint_writer=writer,
                provider=provider,
                executor=executor,
                budget=budget,
                trace=TraceRecorder("run-s34", clock=fixed_clock),
                clock=fixed_clock,
            ).step()
            self.assertEqual(result.terminal_status.value, "budget_exhausted_with_best_correct")
            self.assertEqual(gp.calls, [])
            self.assertEqual(result.state.executed_candidate_count, 0)


if __name__ == "__main__":
    unittest.main()
