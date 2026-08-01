from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agrefactor.config import RunMode, resolve_target_profile
from agrefactor.optimization import (
    BottleneckModelArtifactWriter,
    PragmaModelArtifactWriter,
    StructuralModelArtifactWriter,
)
from agrefactor.product.stage3_optimizer import (
    AcceptedOptimizationMaterial,
    ProductOptimizerRequest,
    Stage3ProductOptimizationPhase,
    UnifiedStage3ModelArtifactWriter,
    build_direct_optimization_material,
)


def _write(path: Path, text: str) -> Path:
    path.write_text(text.rstrip() + "\n", encoding="utf-8")
    return path


def _material(root: Path) -> AcceptedOptimizationMaterial:
    baseline = _write(
        root / "candidate.cpp",
        'extern "C" void candidate_top(int *x) { x[0] += 1; }',
    )
    reference = _write(
        root / "original.cpp",
        'extern "C" void original_top(int *x) { x[0] += 1; }',
    )
    public = _write(root / "public.cpp", "int main() { return 0; }")
    hidden = _write(root / "hidden.cpp", "int main() { int x = 1; return x - 1; }")
    return build_direct_optimization_material(
        source_path=baseline,
        top_function="candidate_top",
        reference_source_path=reference,
        reference_top_function="original_top",
        public_test_paths=(public,),
        hidden_test_paths=(hidden,),
        target=resolve_target_profile(None),
    )


class ProductStage3OptimizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_direct_optimize_requires_independent_reference(self) -> None:
        source = _write(self.root / "candidate.cpp", "void candidate_top() {}")
        public = _write(self.root / "public.cpp", "int main() { return 0; }")
        hidden = _write(self.root / "hidden.cpp", "int main() { return 0; }")
        with self.assertRaisesRegex(ValueError, "--reference-source"):
            build_direct_optimization_material(
                source_path=source,
                top_function="candidate_top",
                reference_source_path=None,
                reference_top_function=None,
                public_test_paths=(public,),
                hidden_test_paths=(hidden,),
                target=resolve_target_profile(None),
            )

    def test_direct_optimize_requires_public_and_hidden(self) -> None:
        source = _write(self.root / "candidate.cpp", "void candidate_top() {}")
        reference = _write(self.root / "original.cpp", "void original_top() {}")
        public = _write(self.root / "public.cpp", "int main() { return 0; }")
        with self.assertRaisesRegex(ValueError, "Public and one provided Hidden"):
            build_direct_optimization_material(
                source_path=source,
                top_function="candidate_top",
                reference_source_path=reference,
                reference_top_function="original_top",
                public_test_paths=(public,),
                hidden_test_paths=(),
                target=resolve_target_profile(None),
            )

    def test_material_is_typed_and_has_independent_splits(self) -> None:
        material = _material(self.root)
        self.assertIs(material.task.mode, RunMode.OPTIMIZE)
        self.assertEqual(material.preflight_suite_id, "public-1")
        self.assertEqual({item.split.value for item in material.suites}, {"public", "hidden"})
        self.assertIs(material.provenance["reference_required"], True)
        self.assertNotEqual(material.baseline_source_path, material.reference_source_path)

    def test_material_rejects_persisted_suite_mismatch(self) -> None:
        material = _material(self.root)
        bad = dict(material.suite_codes)
        bad["public-1"] = "int main() { return 1; }\n"
        with self.assertRaisesRegex(ValueError, "does not match persisted"):
            AcceptedOptimizationMaterial(
                baseline_source_path=material.baseline_source_path,
                reference_source_path=material.reference_source_path,
                top_function=material.top_function,
                reference_top_function=material.reference_top_function,
                target=material.target,
                suites=material.suites,
                suite_codes=bad,
                preflight_suite_id=material.preflight_suite_id,
            )

    def test_unified_writer_satisfies_all_level_writer_types(self) -> None:
        writer = UnifiedStage3ModelArtifactWriter(self.root / "model")
        self.assertIsInstance(writer, StructuralModelArtifactWriter)
        self.assertIsInstance(writer, BottleneckModelArtifactWriter)
        self.assertIsInstance(writer, PragmaModelArtifactWriter)

    def test_full_material_handoff_is_explicit(self) -> None:
        material = _material(self.root)

        class RefactorPhase:
            accepted_optimization_material = material

        request = object.__new__(ProductOptimizerRequest)
        object.__setattr__(request, "mode", RunMode.FULL)
        object.__setattr__(request, "refactor_phase", RefactorPhase())
        phase = Stage3ProductOptimizationPhase.__new__(Stage3ProductOptimizationPhase)
        phase._request = request
        self.assertIs(phase._resolve_material(), material)

    def test_full_handoff_missing_is_not_silent_fallback(self) -> None:
        class RefactorPhase:
            accepted_optimization_material = None

        request = object.__new__(ProductOptimizerRequest)
        object.__setattr__(request, "mode", RunMode.FULL)
        object.__setattr__(request, "refactor_phase", RefactorPhase())
        phase = Stage3ProductOptimizationPhase.__new__(Stage3ProductOptimizationPhase)
        phase._request = request
        with self.assertRaisesRegex(RuntimeError, "without an accepted refactor handoff"):
            phase._resolve_material()

class ProductStage3OptimizerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_direct_optimize_rejects_self_oracle(self) -> None:
        source = _write(self.root / "candidate.cpp", "void candidate_top() {}")
        public = _write(self.root / "public.cpp", "int main() { return 0; }")
        hidden = _write(self.root / "hidden.cpp", "int main() { return 0; }")
        with self.assertRaisesRegex(
            ValueError,
            "cannot be its own correctness oracle",
        ):
            build_direct_optimization_material(
                source_path=source,
                top_function="candidate_top",
                reference_source_path=source,
                reference_top_function="candidate_top",
                public_test_paths=(public,),
                hidden_test_paths=(hidden,),
                target=resolve_target_profile(None),
            )

    def test_full_runner_stops_before_optimize_when_refactor_fails(self) -> None:
        from agrefactor.config import TaskSpec
        from agrefactor.runtime import (
            BudgetLimits,
            PhaseResult,
            PhaseStatus,
            RunPhase,
            UnifiedRunner,
        )

        calls: list[str] = []

        def refactor(_context):
            calls.append("refactor")
            return PhaseResult(
                phase=RunPhase.REFACTOR,
                status=PhaseStatus.FAILED,
                summary="refactor failed",
            )

        def optimize(_context):
            calls.append("optimize")
            return PhaseResult(
                phase=RunPhase.OPTIMIZE,
                status=PhaseStatus.SUCCEEDED,
            )

        source = _write(self.root / "kernel.cpp", "void top() {}")
        task = TaskSpec(
            task_id="full-gate",
            kernel_path=str(source),
            kernel_name="top",
            target=resolve_target_profile(None),
            mode=RunMode.FULL,
        )
        result = UnifiedRunner(
            {RunPhase.REFACTOR: refactor, RunPhase.OPTIMIZE: optimize},
            budget_limits=BudgetLimits(max_wall_time_s=60),
        ).run(task, artifact_root=self.root / "artifacts")
        self.assertFalse(result.succeeded)
        self.assertEqual(calls, ["refactor"])

    def test_baseline_rejection_writes_zero_optimizer_counters_without_model_calls(self) -> None:
        import json

        import agrefactor.product.stage3_optimizer as module
        from agrefactor.models import resolve_model_runtime
        from agrefactor.optimization import (
            CandidateQualificationResult,
            QualificationStage,
            QualificationStatus,
            QualificationStepOutcome,
            QualificationStepRecord,
        )
        from agrefactor.runtime import RunPhase, UnifiedRunner
        from agrefactor.runtime.budget_profile import DEFAULT_SOURCE_RUN_BUDGET_PROFILE

        material = _material(self.root)

        class RejectedQualification:
            def __init__(self, **_kwargs):
                pass

            def qualify_baseline(self, candidate):
                return CandidateQualificationResult(
                    qualification_id="qual-baseline-rejected",
                    candidate_id=candidate.candidate_id,
                    status=QualificationStatus.REJECTED,
                    steps=(
                        QualificationStepRecord(
                            stage=QualificationStage.PREFLIGHT,
                            outcome=QualificationStepOutcome.FAILED,
                            evidence_view="internal_safe",
                            route_action=None,
                            source="deterministic_product_fixture",
                            source_report_id=None,
                            source_item_count=0,
                            source_blocking=True,
                            reason_codes=("fixture_rejected",),
                            metadata={"physical_execution": False},
                        ),
                    ),
                    correctness_passed=False,
                    synthesis_passed=False,
                    objective_feasible=None,
                    ppa=None,
                    cache_key_sha256="c" * 64,
                    cache_hit=False,
                    budget_before={},
                    budget_after={},
                    decision={"action": "reject_baseline"},
                )

        runtime = resolve_model_runtime(
            "deepseek-v4-flash",
            parameters={"temperature": 0, "max_tokens": 32768},
        )
        budget = DEFAULT_SOURCE_RUN_BUDGET_PROFILE.resolve(
            user_requested={
                "max_llm_calls": 14,
                "max_tool_calls": 0,
                "max_compile_calls": 0,
                "max_csim_calls": 0,
                "max_csynth_calls": 0,
                "max_wall_time_s": 60.0,
            }
        )
        artifact_root = self.root / "baseline-rejected-artifacts"
        work_root = self.root / "baseline-rejected-work"
        phase = Stage3ProductOptimizationPhase(
            ProductOptimizerRequest(
                run_id="run-s37-baseline-rejected",
                mode=RunMode.OPTIMIZE,
                registry=runtime.registry,
                effective_model_config=runtime.effective_config,
                budget_contract=budget,
                artifact_root=artifact_root,
                work_root=work_root,
                csim_timeout_s=30,
                csynth_timeout_s=30,
                direct_material=material,
            )
        )
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    module,
                    "_observe_toolchain",
                    lambda _target: {
                        "schema_version": 1,
                        "profile_name": "fixture",
                        "actual_version": "fixture",
                    },
                )
            )
            stack.enter_context(
                patch.object(module, "ProductQualificationAdapter", RejectedQualification)
            )
            result = UnifiedRunner(
                {RunPhase.OPTIMIZE: phase},
                budget_limits=budget.to_budget_limits(),
            ).run(
                material.task,
                run_id="run-s37-baseline-rejected",
                artifact_root=artifact_root,
                trace_path=artifact_root / "trace.jsonl",
            )

        self.assertFalse(result.succeeded)
        identity = json.loads(
            (artifact_root / "stage3_execution_identity.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(identity["baseline_qualification"]["status"], "rejected")
        self.assertEqual(identity["optimizer_counters"]["provider_calls"], 0)
        self.assertEqual(identity["optimizer_counters"]["executor_calls"], 0)
        self.assertEqual(identity["model_calls"]["record_count"], 0)
        self.assertEqual(identity["model_calls"]["invalid_record_count"], 0)

    def test_product_phase_internal_full_chain_preserves_best_correct(self) -> None:
        import hashlib
        import json

        import agrefactor.product.stage3_optimizer as module
        from agrefactor.models import resolve_model_runtime
        from agrefactor.optimization import (
            CandidateQualificationResult,
            FakeCandidateExecutor,
            FakeExecutionOutcome,
            FakeExecutionStatus,
            FakeHypothesisProvider,
            PpaEvidence,
            PpaReportFormat,
            PpaResourceUsage,
            QualificationStage,
            QualificationStatus,
            QualificationStepOutcome,
            QualificationStepRecord,
        )
        from agrefactor.runtime import RunPhase, UnifiedRunner
        from agrefactor.runtime.budget_profile import DEFAULT_SOURCE_RUN_BUDGET_PROFILE

        material = _material(self.root)
        context_sha = "a" * 64

        def baseline_result(candidate):
            ppa = PpaEvidence(
                evidence_id="ppa-baseline",
                parser_profile="product-s37-fixture",
                report_format=PpaReportFormat.XML,
                report_relative_path="fake/baseline.xml",
                report_sha256=hashlib.sha256(b"baseline").hexdigest(),
                comparison_context_identity_sha256=context_sha,
                latency_cycles_min=100,
                latency_cycles_max=100,
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
                objective_feasible=True,
                parser_warnings=("deterministic_product_fixture",),
            )
            steps = tuple(
                QualificationStepRecord(
                    stage=stage,
                    outcome=QualificationStepOutcome.PASSED,
                    evidence_view=(
                        "operator_full"
                        if stage is QualificationStage.HIDDEN
                        else "internal_safe"
                    ),
                    route_action=None,
                    source="deterministic_product_fixture",
                    source_report_id=None,
                    source_item_count=0,
                    source_blocking=False,
                    reason_codes=("fixture_passed",),
                    metadata={"physical_execution": False},
                )
                for stage in (
                    QualificationStage.SOURCE,
                    QualificationStage.PREFLIGHT,
                    QualificationStage.PUBLIC,
                    QualificationStage.CSYNTH,
                    QualificationStage.HIDDEN,
                    QualificationStage.PPA,
                    QualificationStage.FEASIBILITY,
                )
            )
            return CandidateQualificationResult(
                qualification_id="qual-baseline",
                candidate_id=candidate.candidate_id,
                status=QualificationStatus.ACCEPTED,
                steps=steps,
                correctness_passed=True,
                synthesis_passed=True,
                objective_feasible=True,
                ppa=ppa,
                cache_key_sha256="b" * 64,
                cache_hit=False,
                budget_before={},
                budget_after={},
                decision={"action": "baseline_accepted", "physical_execution": False},
            )

        class Qualification:
            def __init__(self, **_kwargs):
                pass

            def qualify_baseline(self, candidate):
                return baseline_result(candidate)

        provider = FakeHypothesisProvider()
        executor = FakeCandidateExecutor(
            outcomes={
                1: FakeExecutionOutcome(latency_cycles_max=120),
                2: FakeExecutionOutcome(status=FakeExecutionStatus.REJECTED),
                3: FakeExecutionOutcome(latency_cycles_max=90),
                4: FakeExecutionOutcome(latency_cycles_max=95),
                5: FakeExecutionOutcome(latency_cycles_max=85),
                6: FakeExecutionOutcome(status=FakeExecutionStatus.REJECTED),
                7: FakeExecutionOutcome(latency_cycles_max=86),
            }
        )

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    module,
                    "_observe_toolchain",
                    lambda _target: {
                        "schema_version": 1,
                        "profile_name": "fixture",
                        "actual_version": "fixture",
                    },
                )
            )
            stack.enter_context(
                patch.object(module, "ProductQualificationAdapter", Qualification)
            )
            for name in (
                "StructuralModelHypothesisProvider",
                "BottleneckModelHypothesisProvider",
                "PragmaModelHypothesisProvider",
                "StructuralModelCandidateGenerator",
                "BottleneckModelCandidateGenerator",
                "PragmaModelCandidateGenerator",
                "StructuralModelCandidateExecutor",
                "BottleneckModelCandidateExecutor",
                "PragmaModelCandidateExecutor",
            ):
                stack.enter_context(
                    patch.object(module, name, lambda **_kwargs: object())
                )
            stack.enter_context(
                patch.object(
                    module,
                    "LevelDispatchHypothesisProvider",
                    lambda _mapping: provider,
                )
            )
            stack.enter_context(
                patch.object(
                    module,
                    "LevelDispatchCandidateExecutor",
                    lambda _mapping: executor,
                )
            )

            runtime = resolve_model_runtime(
                "deepseek-v4-flash",
                parameters={"temperature": 0, "max_tokens": 32768},
            )
            budget = DEFAULT_SOURCE_RUN_BUDGET_PROFILE.resolve(
                user_requested={
                    "max_llm_calls": 14,
                    "max_tool_calls": 0,
                    "max_compile_calls": 0,
                    "max_csim_calls": 0,
                    "max_csynth_calls": 0,
                    "max_wall_time_s": 60.0,
                }
            )
            artifact_root = self.root / "artifacts"
            work_root = self.root / "work"
            phase = Stage3ProductOptimizationPhase(
                ProductOptimizerRequest(
                    run_id="run-s37-internal",
                    mode=RunMode.OPTIMIZE,
                    registry=runtime.registry,
                    effective_model_config=runtime.effective_config,
                    budget_contract=budget,
                    artifact_root=artifact_root,
                    work_root=work_root,
                    csim_timeout_s=30,
                    csynth_timeout_s=30,
                    direct_material=material,
                )
            )
            result = UnifiedRunner(
                {RunPhase.OPTIMIZE: phase},
                budget_limits=budget.to_budget_limits(),
            ).run(
                material.task,
                run_id="run-s37-internal",
                artifact_root=artifact_root,
                trace_path=artifact_root / "trace.jsonl",
            )

        self.assertTrue(result.succeeded)
        identity = json.loads(
            (artifact_root / "stage3_execution_identity.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(identity["state"]["best_correct_candidate_id"], "cand-5")
        self.assertEqual(identity["state"]["best_ppa_candidate_id"], "cand-5")
        self.assertEqual(identity["state"]["executed_candidate_count"], 7)
        self.assertEqual(
            {item.level.value for item in provider.requests},
            {"structural", "bottleneck", "pragma"},
        )
        self.assertEqual(len(executor.requests), 7)
        self.assertIs(
            identity["boundaries"]["baseline_qualified_before_model"],
            True,
        )
        self.assertIs(identity["boundaries"]["static_source_gate_used"], False)
        self.assertIs(identity["boundaries"]["hidden_evidence_exposed"], False)
        self.assertTrue((artifact_root / "optimize" / "final_candidate.cpp").is_file())


if __name__ == "__main__":
    unittest.main()
