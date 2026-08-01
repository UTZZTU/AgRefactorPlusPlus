
import io
import json
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from agrefactor.cli import build_parser, main
from agrefactor.compat import (
    LegacyRefactorAdapter,
    LegacyRefactorSettings,
    build_legacy_refactor_kwargs,
)
from agrefactor.config import (
    EvaluationSplit,
    OverallTestSourceMode,
    RunMode,
    TaskSpec,
    TestSourceSelectionMode,
    resolve_target_profile,
)
from agrefactor.models import resolve_model_runtime
from agrefactor.product import (
    SourceBootstrapPhase,
    SourceBootstrapRequest,
    SourceRunLayout,
    build_test_source_plan,
)
from agrefactor.runtime import (
    BudgetLimits,
    BudgetManager,
    PhaseResult,
    PhaseStatus,
    RunContext,
    RunPhase,
    TraceRecorder,
)
from agrefactor.runtime.budget_profile import (
    DEFAULT_SOURCE_RUN_BUDGET_PROFILE,
)


ORIGINAL = 'extern "C" int top(int x) { return x; }\n'
CANDIDATE = 'extern "C" int top_hls(int x) { return x + 1; }\n'
PUBLIC = (
    'extern "C" int top(int);\n'
    'extern "C" int top_hls(int);\n'
    "int main() { return top_hls(1) >= top(1) ? 0 : 1; }\n"
)
HIDDEN_SECRET = "P2_HIDDEN_TEST_SECRET"
HIDDEN = (
    f"// {HIDDEN_SECRET}\n"
    'extern "C" int top(int);\n'
    'extern "C" int top_hls(int);\n'
    "int main() { return top_hls(2) >= top(2) ? 0 : 1; }\n"
)
PUBLIC_HLS_DECL = 'extern "C" int top_hls(int);'
PUBLIC_HLS_DECL_SHA256 = sha256(
    PUBLIC_HLS_DECL.encode("utf-8")
).hexdigest()


def make_model_data_boundary(include_hidden: bool):
    order = ["public_generation", "candidate_generation"]
    if include_hidden:
        order.append("hidden_generation")
    return {
        "schema_version": 1,
        "boundary": "public_to_hidden_one_way",
        "complete": True,
        "generation_event_order": order,
        "public_generation_hidden_inputs": [],
        "candidate_generation_hidden_inputs": [],
        "public_repair_hidden_inputs": [],
        "candidate_repair_hidden_inputs": [],
        "hidden_generation_inputs": (
            ["original_source", "public_hls_decl"]
            if include_hidden
            else []
        ),
        "hidden_generation_enabled": include_hidden,
        "hidden_generation_after_candidate": (
            True if include_hidden else None
        ),
        "public_hls_decl_sha256": PUBLIC_HLS_DECL_SHA256,
        "hidden_testbench_exposed_to_generation_model": False,
    }


class FakeGenerationAdapter:
    def __init__(self, *, include_public=True, include_hidden=True):
        self.last_raw_result = None
        self.context_ids = []
        self.include_public = include_public
        self.include_hidden = include_hidden

    def __call__(self, context):
        self.context_ids.append(
            (id(context.budget), id(context.trace), context.task)
        )
        context.budget.consume(llm_calls=1)
        self.last_raw_result = (
            True,
            {
                "curr_code": CANDIDATE,
                "new_kernel_name": "top_hls",
                "testbench": PUBLIC if self.include_public else "",
                "generated_hidden_testbench": (
                    HIDDEN if self.include_hidden else ""
                ),
                "public_hls_decl_verbatim": PUBLIC_HLS_DECL,
                "public_hls_decl_sha256": (
                    PUBLIC_HLS_DECL_SHA256
                ),
                "model_data_boundary": make_model_data_boundary(
                    self.include_hidden
                ),
            },
        )
        return PhaseResult(
            phase=RunPhase.REFACTOR,
            status=PhaseStatus.SUCCEEDED,
            metadata={"generation_only": True},
        )


class CapturingFormalBuilder:
    def __init__(self):
        self.task = None
        self.request = None
        self.context_ids = []

    def __call__(self, task, request):
        self.task = task
        self.request = request

        def handler(context):
            self.context_ids.append(
                (id(context.budget), id(context.trace))
            )
            context.budget.consume(
                tool_calls=1,
                compile_calls=1,
            )
            return PhaseResult(
                phase=RunPhase.REFACTOR,
                status=PhaseStatus.SUCCEEDED,
                metadata={"accepted": True},
            )

        return handler


def make_request(root, *, plan):
    source = root / "kernel.cpp"
    source.write_text(ORIGINAL, encoding="utf-8")
    runtime = resolve_model_runtime("deepseek-v4-flash")
    budget = DEFAULT_SOURCE_RUN_BUDGET_PROFILE.resolve(
        token_budget=1000,
        cost_budget="1.25",
        cost_budget_currency="CNY",
    )
    return SourceBootstrapRequest(
        source_path=source,
        top_function="top",
        mode=RunMode.REFACTOR,
        effective_model_config=runtime.effective_config,
        target=resolve_target_profile(None),
        test_source_plan=plan,
        budget_contract=budget,
        max_candidate_repairs=2,
        run_id="p2-test",
    )


class P2SourceOnlyBootstrapTests(unittest.TestCase):
    def test_p4_independent_modes_and_derivation_remain_real(self):
        auto = build_test_source_plan()
        self.assertEqual(
            auto.public.mode,
            TestSourceSelectionMode.AUTO,
        )
        self.assertEqual(
            auto.hidden.mode,
            TestSourceSelectionMode.AUTO,
        )
        self.assertEqual(
            auto.overall_mode,
            OverallTestSourceMode.AUTO,
        )

        provided = build_test_source_plan(
            public_paths=("p1.cpp", "p2.cpp"),
            hidden_paths=("h1.cpp",),
        )
        self.assertEqual(
            provided.overall_mode,
            OverallTestSourceMode.PROVIDED,
        )
        self.assertEqual(len(provided.public.provided_paths), 2)

        hybrid = build_test_source_plan(
            public_paths=("p.cpp",),
            hidden_mode="auto",
        )
        self.assertEqual(
            hybrid.overall_mode,
            OverallTestSourceMode.HYBRID,
        )
        self.assertNotIn(
            "provided_paths",
            hybrid.hidden.to_agent_dict(),
        )

    def test_provided_paths_conflict_with_auto_flag(self):
        with self.assertRaisesRegex(ValueError, "conflict"):
            build_test_source_plan(
                public_mode="auto",
                public_paths=("p.cpp",),
            )

    def test_budget_defaults_ceilings_and_soft_nonblocking(self):
        resolved = DEFAULT_SOURCE_RUN_BUDGET_PROFILE.resolve(
            user_requested={
                "max_llm_calls": 5,
                "max_compile_calls": 0,
            },
            token_budget=50000,
            cost_budget="1.00",
            cost_budget_currency="CNY",
        )
        payload = resolved.to_dict()
        self.assertEqual(
            payload["effective_hard_limits"]["max_llm_calls"],
            5,
        )
        self.assertEqual(
            payload["budget_source_per_field"]["max_llm_calls"],
            "user_requested",
        )
        self.assertEqual(
            payload["budget_source_per_field"]["max_csim_calls"],
            "system_default",
        )
        self.assertEqual(
            payload["soft_usage_budgets"]["enforcement"],
            "observed_only",
        )
        self.assertFalse(
            payload["soft_usage_budgets"]["blocking"]
        )
        limits = resolved.to_budget_limits()
        self.assertIsNone(limits.max_tokens)
        self.assertIsNone(limits.max_cost_usd)

    def test_budget_rejects_request_above_safety_ceiling(self):
        ceiling = (
            DEFAULT_SOURCE_RUN_BUDGET_PROFILE
            .system_safety_ceilings.max_llm_calls
        )
        with self.assertRaisesRegex(ValueError, "safety ceiling"):
            DEFAULT_SOURCE_RUN_BUDGET_PROFILE.resolve(
                user_requested={
                    "max_llm_calls": ceiling + 1,
                }
            )

    def test_exact_deepseek_runtime_defaults_are_resolved(self):
        runtime = resolve_model_runtime(
            "deepseek-v4-flash",
            reasoning_effort="high",
        )
        config = runtime.effective_config
        self.assertEqual(config.model_id, "deepseek-v4-flash")
        self.assertEqual(config.family_profile_name, "deepseek")
        self.assertEqual(
            config.base_url,
            "https://api.deepseek.com",
        )
        self.assertEqual(config.api_key_env, "DEEPSEEK_API_KEY")
        self.assertEqual(runtime.defaults_source, "exact_static_model_defaults")
        self.assertEqual(
            config.parameters["reasoning_effort"],
            "high",
        )

    def test_bootstrap_auto_auto_persists_and_shares_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = make_request(
                root,
                plan=build_test_source_plan(),
            )
            layout = SourceRunLayout.create(
                request.run_id,
                artifact_base=root / "artifacts",
                work_base=root / "work",
            )
            layout.artifact_root.mkdir(parents=True)
            generation = FakeGenerationAdapter()
            formal = CapturingFormalBuilder()
            phase = SourceBootstrapPhase(
                request=request,
                layout=layout,
                generation_adapter=generation,
                formal_phase_builder=formal,
            )
            budget = BudgetManager(
                request.budget_contract.to_budget_limits()
            )
            trace = TraceRecorder(
                "p2-test",
                task_id="source-task",
            )
            context = RunContext(
                run_id="p2-test",
                task=TaskSpec(
                    task_id="source-task",
                    kernel_path=str(request.source_path),
                    kernel_name="top",
                ),
                budget=budget,
                trace=trace,
            )

            result = phase(context)

            self.assertTrue(result.succeeded)
            self.assertEqual(
                generation.context_ids[0][:2],
                formal.context_ids[0],
            )
            self.assertEqual(
                generation.context_ids[0][0],
                id(budget),
            )
            self.assertEqual(
                formal.task.kernel_name,
                "top_hls",
            )
            self.assertEqual(len(formal.task.test_suites), 2)
            self.assertEqual(
                {
                    suite.split
                    for suite in formal.task.test_suites
                },
                {
                    EvaluationSplit.PUBLIC,
                    EvaluationSplit.HIDDEN,
                },
            )
            self.assertEqual(
                formal.request.prompt_public_testbench_code,
                PUBLIC,
            )
            self.assertNotIn(
                HIDDEN_SECRET,
                formal.request.prompt_public_testbench_code,
            )
            normalized = json.loads(
                (
                    layout.artifact_root
                    / "bootstrap"
                    / "normalized_task.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                normalized["kernel_name"],
                "top_hls",
            )
            self.assertTrue(
                (
                    layout.artifact_root
                    / "bootstrap"
                    / "initial_candidate.cpp"
                ).is_file()
            )
            self.assertEqual(
                budget.snapshot().llm_calls,
                1,
            )
            # Auto Public preparation physically compiles once, and the
            # independent formal Stage-2 adjudicator compiles once again.
            # Both launches share and consume the same BudgetManager.
            self.assertEqual(
                budget.snapshot().compile_calls,
                2,
            )

    def test_bootstrap_supports_multiple_provided_public_and_auto_hidden(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public_one = root / "public_one.cpp"
            public_two = root / "public_two.cpp"
            public_one.write_text(PUBLIC, encoding="utf-8")
            public_two.write_text(
                PUBLIC.replace("top_hls(1)", "top_hls(3)"),
                encoding="utf-8",
            )
            plan = build_test_source_plan(
                public_paths=(public_one, public_two),
                hidden_mode="auto",
            )
            request = make_request(root, plan=plan)
            layout = SourceRunLayout.create(
                request.run_id,
                artifact_base=root / "artifacts",
                work_base=root / "work",
            )
            layout.artifact_root.mkdir(parents=True)
            generation = FakeGenerationAdapter(
                include_public=False,
                include_hidden=True,
            )
            formal = CapturingFormalBuilder()
            phase = SourceBootstrapPhase(
                request=request,
                layout=layout,
                generation_adapter=generation,
                formal_phase_builder=formal,
            )
            context = RunContext(
                run_id="p2-test",
                task=TaskSpec(
                    task_id="source-task",
                    kernel_path=str(request.source_path),
                    kernel_name="top",
                ),
                budget=BudgetManager(
                    request.budget_contract.to_budget_limits()
                ),
                trace=TraceRecorder(
                    "p2-test",
                    task_id="source-task",
                ),
            )

            result = phase(context)

            self.assertTrue(result.succeeded)
            self.assertEqual(
                plan.overall_mode,
                OverallTestSourceMode.HYBRID,
            )
            self.assertEqual(len(formal.task.test_suites), 3)
            self.assertEqual(
                len(
                    [
                        suite for suite in formal.task.test_suites
                        if suite.split is EvaluationSplit.PUBLIC
                    ]
                ),
                2,
            )

    def test_legacy_adapter_exposes_generation_only_raw_result(self):
        task = TaskSpec(
            task_id="legacy-generation-only",
            kernel_path="kernel.cpp",
            kernel_name="top",
        )
        settings = LegacyRefactorSettings(
            generation_only=True,
        )
        kwargs = build_legacy_refactor_kwargs(task, settings)
        self.assertTrue(kwargs["generation_only"])

        expected = (
            True,
            {
                "curr_code": CANDIDATE,
                "new_kernel_name": "top_hls",
                "testbench": PUBLIC,
            },
        )
        adapter = LegacyRefactorAdapter(
            settings,
            backend=lambda **_: expected,
            usage_supplier=lambda: {},
        )
        result = adapter(
            RunContext(
                run_id="legacy-generation-only",
                task=task,
                budget=BudgetManager(BudgetLimits()),
                trace=TraceRecorder(
                    "legacy-generation-only",
                    task_id=task.task_id,
                ),
            )
        )
        self.assertTrue(result.succeeded)
        self.assertIs(adapter.last_raw_result, expected)

    def test_flow_generation_only_stops_before_legacy_validation(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "flow"
            / "new.py"
        ).read_text(encoding="utf-8")
        branch = source.index("if generation_only:")
        legacy_validation = source.index(
            'debug_print(debug, "Synthesis & Simulation & Iteration")'
        )
        self.assertLess(branch, legacy_validation)
        self.assertIn(
            '"generated_hidden_testbench"',
            source,
        )

    def test_normal_cli_surface_requires_top_and_hides_internal_paths(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "refactor",
                "kernel.cpp",
                "--top",
                "top",
                "--model",
                "deepseek-v4-flash",
                "--public-test",
                "public.cpp",
                "--public-test",
                "edges.cpp",
                "--hidden-tests",
                "auto",
            ]
        )
        self.assertEqual(args.command, "refactor")
        self.assertEqual(
            args.public_tests_provided,
            ["public.cpp", "edges.cpp"],
        )
        self.assertFalse(hasattr(args, "candidate_file"))
        self.assertFalse(hasattr(args, "repair_work_dir"))
        self.assertFalse(hasattr(args, "artifact_dir"))

    def test_advanced_task_file_run_entry_is_preserved(self):
        parser = build_parser()
        args = parser.parse_args(
            ["run", "task.json", "--dry-run"]
        )
        self.assertEqual(args.command, "run")
        self.assertEqual(args.task_file, Path("task.json"))

    def test_optimize_and_full_product_adapters_are_registered(self):
        parser = build_parser()
        optimize = parser.parse_args(
            [
                "optimize", "candidate.cpp", "--top", "top",
                "--reference-source", "original.cpp",
                "--public-test", "public.cpp",
                "--hidden-test", "hidden.cpp",
                "--model", "deepseek-v4-flash",
            ]
        )
        full = parser.parse_args(
            ["full", "kernel.cpp", "--top", "top", "--model", "deepseek-v4-flash"]
        )
        self.assertEqual(optimize.command, "optimize")
        self.assertEqual(optimize.reference_source, Path("original.cpp"))
        self.assertEqual(full.command, "full")
        self.assertIsNone(full.reference_source)


if __name__ == "__main__":
    unittest.main()
