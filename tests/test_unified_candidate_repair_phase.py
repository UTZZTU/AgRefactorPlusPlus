from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agrefactor.cli import main
from agrefactor.config import (
    EvaluationSplit,
    RunMode,
    TaskSpec,
    TestSuiteSpec,
)
from agrefactor.evaluation import ValidationState
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
)
from agrefactor.models import (
    CandidateModelAdapter,
    ModelProvider,
    ModelRegistry,
    ModelResponse,
    ModelSpec,
    TokenUsage,
)
from agrefactor.repair import (
    CandidateValidationResult,
)
from agrefactor.runtime import (
    BudgetLimits,
    CandidateRepairOrchestrationRequest,
    CandidateRepairPhase,
    CandidateRepairPhaseConfig,
    PhaseResult,
    PhaseStatus,
    RunArtifactWriter,
    RunPhase,
    UnifiedRunner,
    build_candidate_repair_phase,
)


BASE = (
    'extern "C" int top(int x) '
    "{ return x; }\n"
)
REPAIRED = (
    'extern "C" int top(int x) '
    "{ return x + 1; }\n"
)
TB = (
    'extern "C" int top(int);\n'
    "int main() { return top(1) >= 0 ? 0 : 1; }\n"
)
HIDDEN_SECRET = "CLI_HIDDEN_SUITE_SECRET"


class FakeProvider(ModelProvider):
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []

    @property
    def name(self):
        return "fake"

    def generate(self, model, request):
        self.calls.append((model, request))
        if not self.responses:
            raise RuntimeError("no response")
        response = self.responses.pop(0)
        return ModelResponse(
            text=f"```cpp\n{response}\n```",
            model=model.model,
            usage=TokenUsage(
                prompt_tokens=4,
                completion_tokens=3,
                cost_usd=0.0,
            ),
            finish_reason="stop",
        )


def make_adapter(responses=()):
    provider = FakeProvider(responses)
    registry = ModelRegistry()
    registry.register_provider(provider)
    registry.register_model(
        ModelSpec(
            name="fixed-model",
            provider="fake",
            model="fake-model",
        )
    )
    return (
        CandidateModelAdapter(
            registry=registry,
            model_name="fixed-model",
        ),
        provider,
    )


def make_task(*, hidden=False):
    suites = ()
    if hidden:
        suites = (
            TestSuiteSpec(
                suite_id="hidden-final",
                split=EvaluationSplit.HIDDEN,
            ),
        )
    return TaskSpec(
        task_id="repair-aware-phase",
        kernel_path="original.cpp",
        kernel_name="top",
        mode=RunMode.REFACTOR,
        test_suites=suites,
    )


def make_request(*, hidden=False):
    return CandidateRepairOrchestrationRequest(
        initial_candidate=BASE,
        original_code=BASE,
        preflight_testbench_code=TB,
        suite_testbench_codes=(
            {
                "hidden-final": (
                    TB
                    + "// "
                    + HIDDEN_SECRET
                    + "\n"
                )
            }
            if hidden
            else {}
        ),
        prompt_public_testbench_code=None,
        max_attempts=1,
    )


def feedback_report():
    return FeedbackReport(
        report_id="candidate-failure",
        source="preflight",
        items=(
            FeedbackItem(
                feedback_id="candidate.compile",
                stage=FeedbackStage.COMPILE,
                category=FeedbackCategory.SYNTAX_ERROR,
                severity=FeedbackSeverity.ERROR,
                owner=FeedbackOwner.CANDIDATE,
                summary="candidate compile failure",
                detail="safe detail",
                source="deterministic-test",
            ),
        ),
        metadata={"evidence_view": "agent_safe"},
    )


def pass_report(state):
    hidden = (
        state
        is ValidationState.HIDDEN_EVALUATION
    )
    return FeedbackReport(
        report_id=f"{state.value}.pass",
        source=(
            "test_evaluation"
            if hidden
            else state.value
        ),
        metadata={
            "evidence_view": (
                "operator_full"
                if hidden
                else "agent_safe"
            )
        },
    )


class ScenarioFactory:
    def __init__(self, *, require_repair=False):
        self.require_repair = require_repair
        self.requests = []
        self.context_ids = []

    def build(self, request):
        self.requests.append(request)
        states = [
            ValidationState.PREFLIGHT,
            ValidationState.CSYNTH,
        ]
        if any(
            suite.split is EvaluationSplit.HIDDEN
            for suite in request.task.test_suites
        ):
            states.append(
                ValidationState.HIDDEN_EVALUATION
            )
        handlers = {}
        for state in states:
            def handler(
                context,
                state=state,
                request=request,
            ):
                self.context_ids.append(
                    (
                        id(context.budget),
                        id(context.trace),
                    )
                )
                if (
                    self.require_repair
                    and request.attempt == 0
                    and state
                    is ValidationState.PREFLIGHT
                ):
                    return feedback_report()
                return pass_report(state)
            handlers[state] = handler
        return handlers


def file_hash(path):
    import hashlib

    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


class CandidateRepairPhaseConfigTests(
    unittest.TestCase
):
    def test_rejects_invalid_request(self):
        with self.assertRaises(TypeError):
            CandidateRepairPhaseConfig(
                request=object(),
                work_root="/tmp/work",
                artifact_root="/tmp/artifacts",
            )

    def test_rejects_empty_paths_and_bad_timelimit(self):
        with self.assertRaises(ValueError):
            CandidateRepairPhaseConfig(
                request=make_request(),
                work_root=" ",
                artifact_root="/tmp/artifacts",
            )
        with self.assertRaises(ValueError):
            CandidateRepairPhaseConfig(
                request=make_request(),
                work_root="/tmp/work",
                artifact_root="/tmp/artifacts",
                csynth_timelimit=0,
            )

    def test_builder_returns_formal_phase(self):
        adapter, _ = make_adapter()
        phase = build_candidate_repair_phase(
            model_adapter=adapter,
            request=make_request(),
            work_root="/tmp/work",
            artifact_root="/tmp/artifacts",
            handler_factory=ScenarioFactory(),
        )
        self.assertIsInstance(
            phase,
            CandidateRepairPhase,
        )


class CandidateValidationResultPlanTests(
    unittest.TestCase
):
    def test_hidden_only_plan_is_legal_and_invalid_order_is_rejected(self):
        result = CandidateValidationResult(
            passed=True,
            completed_stages=(
                ValidationState.PREFLIGHT,
                ValidationState.CSYNTH,
                ValidationState.HIDDEN_EVALUATION,
            ),
            summary="hidden-only plan passed",
        )
        self.assertTrue(result.passed)

        with self.assertRaises(ValueError):
            CandidateValidationResult(
                passed=True,
                completed_stages=(
                    ValidationState.PREFLIGHT,
                    ValidationState.HIDDEN_EVALUATION,
                ),
                summary="invalid plan",
            )


class CandidateRepairPhaseTests(unittest.TestCase):
    def test_initial_acceptance_writes_complete_phase_bundle(self):
        adapter, provider = make_adapter()
        factory = ScenarioFactory()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            phase = build_candidate_repair_phase(
                model_adapter=adapter,
                request=make_request(),
                work_root=root / "work",
                artifact_root=root / "artifacts",
                handler_factory=factory,
            )
            result = UnifiedRunner(
                {RunPhase.REFACTOR: phase}
            ).run(
                make_task(),
                run_id="initial-pass",
                trace_path=(
                    root
                    / "artifacts"
                    / "trace.jsonl"
                ),
                artifact_root=(
                    root / "artifacts"
                ),
                run_metadata={
                    "execution_mode": "repair_aware",
                    "legacy_mode": False,
                },
            )
            phase_root = (
                root / "artifacts" / "refactor"
            )
            self.assertTrue(result.succeeded)
            self.assertEqual(provider.calls, [])
            self.assertTrue(
                (
                    phase_root
                    / "orchestration_result.json"
                ).is_file()
            )
            self.assertTrue(
                (
                    phase_root
                    / "final_candidate.cpp"
                ).is_file()
            )
            self.assertTrue(
                (
                    phase_root
                    / "artifact_manifest.json"
                ).is_file()
            )

    def test_repair_reuses_exact_budget_trace_and_writes_nested_artifacts(self):
        adapter, provider = make_adapter(
            [REPAIRED]
        )
        factory = ScenarioFactory(
            require_repair=True
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            phase = build_candidate_repair_phase(
                model_adapter=adapter,
                request=make_request(),
                work_root=root / "work",
                artifact_root=root / "artifacts",
                handler_factory=factory,
            )
            result = UnifiedRunner(
                {RunPhase.REFACTOR: phase},
                budget_limits=BudgetLimits(
                    max_llm_calls=1
                ),
            ).run(
                make_task(),
                run_id="repair-pass",
                trace_path=(
                    root
                    / "artifacts"
                    / "trace.jsonl"
                ),
                artifact_root=(
                    root / "artifacts"
                ),
                run_metadata={
                    "execution_mode": "repair_aware",
                    "legacy_mode": False,
                },
            )
            repair_manifest = (
                root
                / "artifacts"
                / "refactor"
                / "repair_artifacts"
                / "artifact_manifest.json"
            )
            self.assertTrue(result.succeeded)
            self.assertTrue(
                repair_manifest.is_file()
            )
            self.assertEqual(
                len(provider.calls),
                1,
            )
            self.assertEqual(
                result.budget_usage.llm_calls,
                1,
            )
            self.assertEqual(
                len(
                    {
                        budget_id
                        for budget_id, _
                        in factory.context_ids
                    }
                ),
                1,
            )
            self.assertEqual(
                len(
                    {
                        trace_id
                        for _, trace_id
                        in factory.context_ids
                    }
                ),
                1,
            )


    def test_repair_with_hidden_only_suite_is_accepted(self):
        adapter, provider = make_adapter(
            [REPAIRED]
        )
        factory = ScenarioFactory(
            require_repair=True
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            phase = build_candidate_repair_phase(
                model_adapter=adapter,
                request=make_request(hidden=True),
                work_root=root / "work",
                artifact_root=root / "artifacts",
                handler_factory=factory,
            )
            result = UnifiedRunner(
                {RunPhase.REFACTOR: phase},
                budget_limits=BudgetLimits(
                    max_llm_calls=1
                ),
            ).run(
                make_task(hidden=True),
                run_id="repair-hidden-only",
                artifact_root=(
                    root / "artifacts"
                ),
                run_metadata={
                    "execution_mode": "repair_aware",
                    "legacy_mode": False,
                },
            )
            self.assertTrue(result.succeeded)
            self.assertEqual(
                result.phases[0].status,
                PhaseStatus.SUCCEEDED,
            )
            self.assertEqual(
                len(provider.calls),
                1,
            )
            self.assertTrue(
                (
                    root
                    / "artifacts"
                    / "refactor"
                    / "repair_artifacts"
                    / "artifact_manifest.json"
                ).is_file()
            )

    def test_hidden_suite_code_never_enters_artifacts(self):
        adapter, provider = make_adapter()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            phase = build_candidate_repair_phase(
                model_adapter=adapter,
                request=make_request(hidden=True),
                work_root=root / "work",
                artifact_root=root / "artifacts",
                handler_factory=ScenarioFactory(),
            )
            result = UnifiedRunner(
                {RunPhase.REFACTOR: phase}
            ).run(
                make_task(hidden=True),
                run_id="hidden-pass",
                artifact_root=(
                    root / "artifacts"
                ),
                run_metadata={
                    "execution_mode": "repair_aware"
                },
            )
            joined = b"\n".join(
                path.read_bytes()
                for path in (
                    root / "artifacts"
                ).rglob("*")
                if path.is_file()
            )
            self.assertTrue(result.succeeded)
            self.assertEqual(provider.calls, [])
            self.assertNotIn(
                HIDDEN_SECRET.encode(),
                joined,
            )

    def test_phase_manifest_hashes_match_files(self):
        adapter, _ = make_adapter()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            phase = build_candidate_repair_phase(
                model_adapter=adapter,
                request=make_request(),
                work_root=root / "work",
                artifact_root=root / "artifacts",
                handler_factory=ScenarioFactory(),
            )
            UnifiedRunner(
                {RunPhase.REFACTOR: phase}
            ).run(
                make_task(),
                run_id="hashes",
                artifact_root=(
                    root / "artifacts"
                ),
            )
            phase_root = (
                root / "artifacts" / "refactor"
            )
            manifest = json.loads(
                (
                    phase_root
                    / "artifact_manifest.json"
                ).read_text(encoding="utf-8")
            )
            for item in manifest["files"]:
                file_path = (
                    phase_root
                    / item["relative_path"]
                )
                self.assertEqual(
                    item["sha256"],
                    file_hash(file_path),
                )

    def test_nonempty_phase_root_becomes_phase_error(self):
        adapter, _ = make_adapter()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            phase_root = (
                root / "artifacts" / "refactor"
            )
            phase_root.mkdir(parents=True)
            (
                phase_root / "existing.txt"
            ).write_text(
                "existing",
                encoding="utf-8",
            )
            phase = build_candidate_repair_phase(
                model_adapter=adapter,
                request=make_request(),
                work_root=root / "work",
                artifact_root=root / "artifacts",
                handler_factory=ScenarioFactory(),
            )
            result = UnifiedRunner(
                {RunPhase.REFACTOR: phase}
            ).run(
                make_task(),
                run_id="nonempty-phase",
            )
            self.assertEqual(
                result.phases[0].status,
                PhaseStatus.ERROR,
            )


class RunArtifactWriterTests(unittest.TestCase):
    def test_runner_writes_versioned_safe_bundle_and_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = UnifiedRunner(
                {
                    RunPhase.REFACTOR: (
                        lambda context: PhaseResult(
                            phase=RunPhase.REFACTOR,
                            status=PhaseStatus.SUCCEEDED,
                            metadata={
                                "execution_mode": "repair_aware",
                                "legacy_mode": False,
                            },
                        )
                    )
                }
            ).run(
                make_task(),
                run_id="run-bundle",
                trace_path=root / "trace.jsonl",
                artifact_root=root,
                run_metadata={
                    "execution_mode": "repair_aware",
                    "legacy_mode": False,
                },
            )
            payload = json.loads(
                (
                    root / "run_result.json"
                ).read_text(encoding="utf-8")
            )
            manifest = json.loads(
                (
                    root
                    / "run_artifact_manifest.json"
                ).read_text(encoding="utf-8")
            )
            self.assertTrue(result.succeeded)
            self.assertEqual(
                payload["metadata"][
                    "execution_mode"
                ],
                "repair_aware",
            )
            self.assertEqual(
                manifest["evidence_view"],
                "agent_safe",
            )
            self.assertFalse(
                manifest["legacy_mode"]
            )
            for item in manifest["files"]:
                file_path = (
                    root / item["relative_path"]
                )
                self.assertEqual(
                    item["sha256"],
                    file_hash(file_path),
                )

    def test_nonempty_run_root_is_rejected_before_handler(self):
        called = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "existing.txt").write_text(
                "existing",
                encoding="utf-8",
            )
            runner = UnifiedRunner(
                {
                    RunPhase.REFACTOR: (
                        lambda context: called.append(
                            True
                        )
                    )
                }
            )
            with self.assertRaises(
                FileExistsError
            ):
                runner.run(
                    make_task(),
                    artifact_root=root,
                )
        self.assertEqual(called, [])

    def test_writer_rejects_second_write_and_leaves_no_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = UnifiedRunner(
                {
                    RunPhase.REFACTOR: (
                        lambda context: PhaseResult(
                            phase=RunPhase.REFACTOR,
                            status=PhaseStatus.SUCCEEDED,
                        )
                    )
                }
            ).run(
                make_task(),
                run_id="single-write",
            )
            writer = RunArtifactWriter(root)
            writer.write(result)
            self.assertEqual(
                tuple(root.rglob("*.tmp")),
                (),
            )
            with self.assertRaises(
                FileExistsError
            ):
                writer.write(result)


def write_cli_task(
    root,
    *,
    mode="refactor",
    public_suites=0,
):
    original = root / "original.cpp"
    preflight = root / "preflight.cpp"
    candidate = root / "candidate.cpp"
    original.write_text(BASE, encoding="utf-8")
    preflight.write_text(TB, encoding="utf-8")
    candidate.write_text(BASE, encoding="utf-8")
    suites = []
    for index in range(public_suites):
        suite_path = root / f"public_{index}.cpp"
        suite_path.write_text(
            TB,
            encoding="utf-8",
        )
        suites.append(
            {
                "suite_id": f"public-{index}",
                "split": "public",
                "testbench_path": suite_path.name,
            }
        )
    task = root / "task.json"
    task.write_text(
        json.dumps(
            {
                "task_id": "cli-repair-aware",
                "kernel_path": original.name,
                "kernel_name": "top",
                "mode": mode,
                "testbench_path": preflight.name,
                "test_suites": suites,
            }
        ),
        encoding="utf-8",
    )
    return task, candidate


class RepairAwareCliTests(unittest.TestCase):
    def test_execution_modes_are_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task, _ = write_cli_task(root)
            stderr = io.StringIO()
            code = main(
                [
                    "run",
                    str(task),
                    "--dry-run",
                    "--repair-aware",
                ],
                stdout=io.StringIO(),
                stderr=stderr,
            )
        self.assertEqual(code, 2)
        self.assertIn(
            "Choose exactly one execution mode",
            stderr.getvalue(),
        )

    def test_repair_aware_requires_refactor_model_and_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task, candidate = write_cli_task(
                root,
                mode="full",
            )
            stderr = io.StringIO()
            code = main(
                [
                    "run",
                    str(task),
                    "--repair-aware",
                    "--model",
                    "fake",
                    "--candidate-file",
                    str(candidate),
                    "--repair-work-dir",
                    str(root / "work"),
                    "--artifact-dir",
                    str(root / "artifacts"),
                ],
                stdout=io.StringIO(),
                stderr=stderr,
            )
            self.assertEqual(code, 2)
            self.assertIn(
                "supports only mode='refactor'",
                stderr.getvalue(),
            )

            task, candidate = write_cli_task(root)
            stderr = io.StringIO()
            code = main(
                [
                    "run",
                    str(task),
                    "--repair-aware",
                    "--candidate-file",
                    str(candidate),
                    "--repair-work-dir",
                    str(root / "work2"),
                    "--artifact-dir",
                    str(root / "artifacts2"),
                ],
                stdout=io.StringIO(),
                stderr=stderr,
            )
            self.assertEqual(code, 2)
            self.assertIn(
                "--model is required",
                stderr.getvalue(),
            )

            stderr = io.StringIO()
            code = main(
                [
                    "run",
                    str(task),
                    "--repair-aware",
                    "--model",
                    "fake",
                    "--repair-work-dir",
                    str(root / "work3"),
                    "--artifact-dir",
                    str(root / "artifacts3"),
                ],
                stdout=io.StringIO(),
                stderr=stderr,
            )
            self.assertEqual(code, 2)
            self.assertIn(
                "--candidate-file is required",
                stderr.getvalue(),
            )

    def test_multiple_public_suites_require_explicit_prompt_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task, candidate = write_cli_task(
                root,
                public_suites=2,
            )
            stderr = io.StringIO()
            code = main(
                [
                    "run",
                    str(task),
                    "--repair-aware",
                    "--model",
                    "fake",
                    "--candidate-file",
                    str(candidate),
                    "--repair-work-dir",
                    str(root / "work"),
                    "--artifact-dir",
                    str(root / "artifacts"),
                ],
                stdout=io.StringIO(),
                stderr=stderr,
            )
        self.assertEqual(code, 2)
        self.assertIn(
            "multiple public suites require",
            stderr.getvalue(),
        )

    def test_cli_constructs_formal_phase_without_reading_api_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task, candidate = write_cli_task(root)
            stdout = io.StringIO()
            stderr = io.StringIO()

            def fake_phase(context):
                return PhaseResult(
                    phase=RunPhase.REFACTOR,
                    status=PhaseStatus.SUCCEEDED,
                    metadata={
                        "execution_mode": "repair_aware",
                        "legacy_mode": False,
                    },
                )

            with patch(
                "agrefactor.cli."
                "build_candidate_repair_phase",
                return_value=fake_phase,
            ) as builder:
                code = main(
                    [
                        "run",
                        str(task),
                        "--repair-aware",
                        "--model",
                        "network-model-not-called",
                        "--candidate-file",
                        str(candidate),
                        "--repair-work-dir",
                        str(root / "work"),
                        "--artifact-dir",
                        str(root / "artifacts"),
                        "--run-id",
                        "cli-formal",
                    ],
                    stdout=stdout,
                    stderr=stderr,
                )
            payload = json.loads(
                stdout.getvalue()
            )
            self.assertEqual(code, 0)
            self.assertEqual(
                stderr.getvalue(),
                "",
            )
            self.assertTrue(builder.called)
            self.assertEqual(
                payload["execution_mode"],
                "repair_aware",
            )
            self.assertFalse(
                payload["legacy_mode"]
            )
            self.assertTrue(
                payload[
                    "artifact_manifest"
                ].endswith(
                    "run_artifact_manifest.json"
                )
            )

    def test_cli_resolves_task_relative_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task, candidate = write_cli_task(
                root,
                public_suites=1,
            )
            observed = {}

            def fake_builder(**kwargs):
                observed["request"] = kwargs[
                    "request"
                ]
                return lambda context: PhaseResult(
                    phase=RunPhase.REFACTOR,
                    status=PhaseStatus.SUCCEEDED,
                )

            with patch(
                "agrefactor.cli."
                "build_candidate_repair_phase",
                side_effect=fake_builder,
            ):
                code = main(
                    [
                        "run",
                        str(task),
                        "--repair-aware",
                        "--model",
                        "not-called",
                        "--candidate-file",
                        str(candidate),
                        "--repair-work-dir",
                        str(root / "work"),
                        "--artifact-dir",
                        str(root / "artifacts"),
                    ],
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )
            self.assertEqual(code, 0)
            request = observed["request"]
            self.assertEqual(
                request.original_code,
                BASE,
            )
            self.assertEqual(
                request.preflight_testbench_code,
                TB,
            )
            self.assertEqual(
                request.suite_testbench_codes[
                    "public-0"
                ],
                TB,
            )
            self.assertEqual(
                request.prompt_public_testbench_code,
                TB,
            )

    def test_legacy_output_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task, _ = write_cli_task(root)
            stdout = io.StringIO()

            def fake_handler(context):
                return PhaseResult(
                    phase=RunPhase.REFACTOR,
                    status=PhaseStatus.SUCCEEDED,
                )

            with patch(
                "agrefactor.cli.LegacyRefactorAdapter",
                return_value=fake_handler,
            ):
                code = main(
                    [
                        "run",
                        str(task),
                        "--legacy",
                    ],
                    stdout=stdout,
                    stderr=io.StringIO(),
                )
            payload = json.loads(
                stdout.getvalue()
            )
            self.assertEqual(code, 0)
            self.assertEqual(
                payload["execution_mode"],
                "legacy",
            )
            self.assertTrue(
                payload["legacy_mode"]
            )

    def test_runtime_exports_and_dependency_boundary(self):
        import inspect
        import agrefactor.runtime as runtime
        import agrefactor.runtime.repair_phase as module

        for name in (
            "CandidateRepairPhase",
            "CandidateRepairPhaseConfig",
            "RunArtifactWriter",
            "build_candidate_repair_phase",
        ):
            self.assertTrue(
                hasattr(runtime, name),
                name,
            )
        source = inspect.getsource(module)
        for forbidden in (
            "agrefactor.cli",
            "LegacyRefactorAdapter",
            "optimizer",
            "OpenAICompatibleProvider",
        ):
            self.assertNotIn(
                forbidden,
                source,
            )


if __name__ == "__main__":
    unittest.main()
