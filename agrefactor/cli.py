"""Command-line entry point for AgRefactor++."""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

from agrefactor.compat import (
    LegacyRefactorAdapter,
    LegacyRefactorSettings,
)
from agrefactor.config import (
    CSIM_TIMEOUT_SAFETY_CEILING,
    CSYNTH_TIMEOUT_SAFETY_CEILING,
    COSIM_TIMEOUT_SAFETY_CEILING,
    DEFAULT_CANDIDATE_REPAIR_ATTEMPTS,
    DEFAULT_CSIM_TIMEOUT_S,
    DEFAULT_CSYNTH_TIMEOUT_S,
    DEFAULT_COSIM_TIMEOUT_S,
    DEFAULT_HIDDEN_COVERAGE_ROUNDS,
    DEFAULT_HIDDEN_GENERATION_TRAJECTORIES,
    DEFAULT_PUBLIC_COVERAGE_ROUNDS,
    DEFAULT_PUBLIC_GENERATION_TRAJECTORIES,
    DEFAULT_TESTBENCH_REPAIR_ATTEMPTS,
    DEFAULT_TEST_GENERATION_TRAJECTORIES,
    REPAIR_ATTEMPT_SAFETY_CEILING,
    TEST_GENERATION_COUNT_SAFETY_CEILING,
    EvaluationSplit,
    RunMode,
    TaskSpec,
    TestGenerationProfile,
    validate_csim_timeout_s,
    validate_csynth_timeout_s,
    validate_cosim_timeout_s,
    validate_repair_attempts,
    validate_test_generation_count,
)
from agrefactor.models import (
    DEFAULT_MODEL_ID,
    CandidateModelAdapter,
    ModelRegistry,
    ModelSpec,
    OpenAICompatibleProvider,
)
from agrefactor.runtime.budget_profile import run_budget_profile_for_mode
from agrefactor.runtime import (
    BudgetLimits,
    CandidateRepairOrchestrationRequest,
    PhaseResult,
    PhaseStatus,
    RunPhase,
    RunResult,
    UnifiedRunner,
    build_candidate_repair_phase,
)



class _StoreWithExplicitFlag(argparse.Action):
    """Store a value and remember that the user supplied the option."""

    def __call__(
        self,
        parser,
        namespace,
        values,
        option_string=None,
    ) -> None:
        del parser, option_string
        setattr(namespace, self.dest, values)
        setattr(namespace, f"{self.dest}_explicit", True)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError(
            "value must be a positive integer"
        )
    return parsed


def _test_generation_count(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "test generation count must be an integer"
        ) from exc
    try:
        return validate_test_generation_count(
            parsed,
            field_name="test generation count",
        )
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _csim_timeout(value: str) -> int:
    try:
        return validate_csim_timeout_s(int(value))
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _csynth_timeout(value: str) -> int:
    try:
        return validate_csynth_timeout_s(int(value))
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _cosim_timeout(value: str) -> int:
    try:
        return validate_cosim_timeout_s(int(value))
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _repair_attempt_count(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "repair attempts must be an integer"
        ) from exc
    try:
        return validate_repair_attempts(
            parsed,
            field_name="repair attempts",
        )
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


_ADVANCED_RUN_MODE_DEPRECATION = (
    " is a deprecated advanced compatibility selector. "
    "Use refactor/optimize/full for normal product execution; "
    "run task.json is retained only for advanced reproduction "
    "and migration."
)


def _warn_deprecated_advanced_run_modes(args) -> None:
    # Warn without changing normal stderr output; DeprecationWarning remains
    # capturable by tests and advanced callers.
    if getattr(args, "command", None) != "run":
        return
    for attribute, option in (
        ("legacy", "--legacy"),
        ("repair_aware", "--repair-aware"),
    ):
        if getattr(args, attribute, False):
            warnings.warn(
                option + _ADVANCED_RUN_MODE_DEPRECATION,
                DeprecationWarning,
                stacklevel=3,
            )


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level AgRefactor++ argument parser."""

    parser = argparse.ArgumentParser(
        prog="agrefactor",
        description="Shared command-line interface for AgRefactor++.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate-task",
        help="Validate and normalize a TaskSpec JSON file.",
    )
    validate_parser.add_argument(
        "task_file",
        type=Path,
        help="Path to a JSON file containing a TaskSpec.",
    )
    validate_parser.add_argument(
        "--output",
        type=Path,
        help="Optional path for the normalized JSON output.",
    )

    run_parser = subparsers.add_parser(
        "run",
        help="Run a TaskSpec through the unified runner.",
    )
    run_parser.add_argument(
        "task_file",
        type=Path,
        help="Path to a JSON file containing a TaskSpec.",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Exercise orchestration without invoking legacy flows or tools.",
    )
    run_parser.add_argument(
        "--legacy",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    run_parser.add_argument(
        "--repair-aware",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    run_parser.add_argument(
        "--run-id",
        help="Optional stable run identifier.",
    )
    run_parser.add_argument(
        "--trace",
        type=Path,
        help="Optional JSONL path for the structured trace.",
    )
    run_parser.add_argument(
        "--model",
        help=(
            "User-selected model identifier for advanced execution."
        ),
    )
    run_parser.add_argument(
        "--model-family",
        help=(
            "Optional vendor-neutral model-family "
            "profile name."
        ),
    )
    run_parser.add_argument(
        "--api-key-env",
        default="OPENAI_API_KEY",
        help=(
            "Environment variable holding the model "
            "credential. Default: OPENAI_API_KEY."
        ),
    )
    run_parser.add_argument(
        "--base-url",
        help="Legacy OpenAI-compatible API base URL.",
    )
    run_parser.add_argument(
        "--reasoning-effort",
        help="Legacy reasoning effort, such as low, medium, or high.",
    )
    run_parser.add_argument(
        "--max-retry-attempts",
        type=int,
        default=3,
        help=(
            "Maximum repair retries after the initial legacy attempt. "
            "Use 0 for a single attempt. Default: 3."
        ),
    )
    run_parser.add_argument(
        "--enable-testbench-repair",
        action="store_true",
        help=(
            "Enable bounded testbench-only repair before synthesis."
        ),
    )
    run_parser.add_argument(
        "--max-testbench-repair-attempts",
        type=_repair_attempt_count,
        default=DEFAULT_TESTBENCH_REPAIR_ATTEMPTS,
        help=(
            "Independent Testbench repair attempts. "
            f"Default: {DEFAULT_TESTBENCH_REPAIR_ATTEMPTS}; "
            f"valid range: 1..{REPAIR_ATTEMPT_SAFETY_CEILING}."
        ),
    )
    run_parser.add_argument(
        "--testbench-repair-model",
        help=(
            "Dedicated repair model; defaults to --model when omitted."
        ),
    )
    run_parser.add_argument(
        "--testbench-repair-api-key-env",
        default="OPENAI_API_KEY",
        help=(
            "Environment variable containing the repair API key. "
            "Default: OPENAI_API_KEY."
        ),
    )
    run_parser.add_argument(
        "--output-dir",
        help=(
            "Optional isolated working directory "
            "for the legacy flow."
        ),
    )
    run_parser.add_argument(
        "--candidate-file",
        type=Path,
        help=(
            "Initial candidate source for advanced formal validation."
        ),
    )
    run_parser.add_argument(
        "--original-file",
        type=Path,
        help=(
            "Original source for advanced formal validation; "
            "defaults to TaskSpec.kernel_path."
        ),
    )
    run_parser.add_argument(
        "--preflight-testbench-file",
        type=Path,
        help=(
            "Preflight testbench; defaults to "
            "TaskSpec.testbench_path."
        ),
    )
    run_parser.add_argument(
        "--prompt-public-testbench-file",
        type=Path,
        help=(
            "Explicit prompt-facing public "
            "testbench when multiple public suites "
            "are declared."
        ),
    )
    run_parser.add_argument(
        "--repair-work-dir",
        type=Path,
        help=(
            "Isolated local validation work root "
            "for advanced formal validation."
        ),
    )
    run_parser.add_argument(
        "--artifact-dir",
        type=Path,
        help=(
            "Empty output root for the versioned "
            "advanced validation run bundle."
        ),
    )
    run_parser.add_argument(
        "--max-candidate-repair-attempts",
        type=_repair_attempt_count,
        default=DEFAULT_CANDIDATE_REPAIR_ATTEMPTS,
        help=(
            "Maximum Candidate repair model calls after initial validation. "
            f"Default: {DEFAULT_CANDIDATE_REPAIR_ATTEMPTS}; "
            f"valid range: 1..{REPAIR_ATTEMPT_SAFETY_CEILING}."
        ),
    )
    run_parser.add_argument(
        "--csynth-timelimit",
        type=_csynth_timeout,
        default=DEFAULT_CSYNTH_TIMEOUT_S,
        help=(
            "Per-CSYNTH timeout in seconds. "
            f"Default: {DEFAULT_CSYNTH_TIMEOUT_S}; "
            f"valid range: 1..{CSYNTH_TIMEOUT_SAFETY_CEILING}."
        ),
    )
    run_parser.add_argument(
        "--csim-timelimit",
        type=_csim_timeout,
        default=DEFAULT_CSIM_TIMEOUT_S,
        help=(
            "Per-CSIM timeout in seconds. "
            f"Default: {DEFAULT_CSIM_TIMEOUT_S}; "
            f"valid range: 1..{CSIM_TIMEOUT_SAFETY_CEILING}."
        ),
    )
    run_parser.add_argument(
        "--cosim-timelimit",
        type=_cosim_timeout,
        default=DEFAULT_COSIM_TIMEOUT_S,
        help=(
            "Per-Public-RTL-COSIM timeout in seconds. "
            f"Default: {DEFAULT_COSIM_TIMEOUT_S}; "
            f"valid range: 1..{COSIM_TIMEOUT_SAFETY_CEILING}."
        ),
    )
    run_parser.add_argument(
        "--cosim-policy",
        choices=("required", "off"),
        default="required",
        help="Advanced repair-aware Public RTL COSIM policy.",
    )
    run_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable legacy flow debug output.",
    )


    for source_command in ("refactor", "optimize", "full"):
        source_parser = subparsers.add_parser(
            source_command,
            help=(
                "Run the normal source-only product entrypoint. "
                "Stage 3 safe-v1 is available for optimize/full."
            ),
        )
        source_parser.set_defaults(
            reasoning_effort_explicit=False,
            test_generation_profile_explicit=False,
            public_coverage_rounds_explicit=False,
            hidden_coverage_rounds_explicit=False,
            public_generation_trajectories_explicit=False,
            hidden_generation_trajectories_explicit=False,
            test_generation_trajectories_explicit=False,
            max_testbench_repairs_explicit=False,
            max_candidate_repairs_explicit=False,
        )
        source_parser.add_argument(
            "source",
            type=Path,
            help="Input C/C++ source file.",
        )
        source_parser.add_argument(
            "--top",
            required=True,
            help="Explicit source top function name.",
        )
        source_parser.add_argument(
            "--reference-source",
            type=Path,
            help=(
                "Independent reference source required by direct optimize; "
                "full mode obtains this from the refactor phase."
            ),
        )
        source_parser.add_argument(
            "--reference-top",
            help="Reference top function for direct optimize. Defaults to --top.",
        )
        if source_command in {"optimize", "full"}:
            source_parser.add_argument(
                "--optimizer-profile",
                choices=("safe-v1",),
                default="safe-v1",
                help="Typed optimizer policy profile. Stage 3 v1 supports only safe-v1.",
            )
            source_parser.add_argument(
                "--optimization-objective",
                choices=("latency",),
                default="latency",
                help="Optimization objective. Stage 3 v1 supports only latency.",
            )
        source_parser.add_argument(
            "--model",
            default=DEFAULT_MODEL_ID,
            action=_StoreWithExplicitFlag,
            help=(
                "Exact fixed model identifier. Default: "
                f"{DEFAULT_MODEL_ID}."
            ),
        )
        source_parser.add_argument(
            "--model-family",
            help="Optional explicit static model-family profile.",
        )
        source_parser.add_argument(
            "--base-url",
            help="Optional transport base URL override.",
        )
        source_parser.add_argument(
            "--api-key-env",
            help="Optional credential environment-variable override.",
        )
        source_parser.add_argument(
            "--reasoning-effort",
            choices=("auto", "medium", "high"),
            default="auto",
            action=_StoreWithExplicitFlag,
            help=(
                "Requested reasoning effort. Default: auto, which selects a "
                "frozen role-specific medium/high project level."
            ),
        )
        source_parser.add_argument(
            "--target",
            default="vitis-2023.2-default",
            help="Named TargetProfile. Default: vitis-2023.2-default.",
        )
        source_parser.add_argument(
            "--part",
            help="Optional device/part override.",
        )
        source_parser.add_argument(
            "--clock-period",
            type=float,
            help="Optional target clock period in nanoseconds.",
        )
        source_parser.add_argument(
            "--replace-compile-flag",
            dest="compile_flags",
            action="append",
            default=[],
            help=(
                "Repeatable replacement for the TargetProfile compile flag "
                "list; supplying it replaces committed defaults."
            ),
        )
        source_parser.add_argument(
            "--compile-flag",
            dest="deprecated_compile_flags",
            action="append",
            default=[],
            help=argparse.SUPPRESS,
        )
        source_parser.add_argument(
            "--test-generation-profile",
            choices=tuple(
                item.value for item in TestGenerationProfile
            ),
            default=TestGenerationProfile.LIGHTWEIGHT.value,
            action=_StoreWithExplicitFlag,
            help=(
                "Testbench-generation strategy. Default: lightweight. "
                "Use coverage-enhanced explicitly for iterative coverage."
            ),
        )
        source_parser.add_argument(
            "--public-coverage-rounds",
            type=_test_generation_count,
            default=DEFAULT_PUBLIC_COVERAGE_ROUNDS,
            action=_StoreWithExplicitFlag,
            help=(
                "Public coverage rounds used only by coverage-enhanced. "
                f"Default: {DEFAULT_PUBLIC_COVERAGE_ROUNDS}; "
                f"valid range: 1..{TEST_GENERATION_COUNT_SAFETY_CEILING}."
            ),
        )
        source_parser.add_argument(
            "--hidden-coverage-rounds",
            type=_test_generation_count,
            default=DEFAULT_HIDDEN_COVERAGE_ROUNDS,
            action=_StoreWithExplicitFlag,
            help=(
                "Hidden coverage rounds used only by coverage-enhanced. "
                f"Default: {DEFAULT_HIDDEN_COVERAGE_ROUNDS}; "
                f"valid range: 1..{TEST_GENERATION_COUNT_SAFETY_CEILING}."
            ),
        )
        source_parser.add_argument(
            "--public-generation-trajectories",
            type=_test_generation_count,
            default=DEFAULT_PUBLIC_GENERATION_TRAJECTORIES,
            action=_StoreWithExplicitFlag,
            help=(
                "Independent Public generation trajectories used only by "
                "coverage-enhanced. "
                f"Default: {DEFAULT_PUBLIC_GENERATION_TRAJECTORIES}; "
                f"valid range: 1..{TEST_GENERATION_COUNT_SAFETY_CEILING}."
            ),
        )
        source_parser.add_argument(
            "--hidden-generation-trajectories",
            type=_test_generation_count,
            default=DEFAULT_HIDDEN_GENERATION_TRAJECTORIES,
            action=_StoreWithExplicitFlag,
            help=(
                "Independent Hidden generation trajectories used only by "
                "coverage-enhanced. "
                f"Default: {DEFAULT_HIDDEN_GENERATION_TRAJECTORIES}; "
                f"valid range: 1..{TEST_GENERATION_COUNT_SAFETY_CEILING}."
            ),
        )
        source_parser.add_argument(
            "--test-generation-trajectories",
            type=_test_generation_count,
            default=DEFAULT_TEST_GENERATION_TRAJECTORIES,
            action=_StoreWithExplicitFlag,
            help=argparse.SUPPRESS,
        )
        source_parser.add_argument(
            "--public-tests",
            choices=("auto",),
            default=None,
            help=(
                "Public source mode when --public-test is absent. "
                "Default and only normal-mode value: auto."
            ),
        )
        source_parser.add_argument(
            "--public-test",
            dest="public_tests_provided",
            action="append",
            default=[],
            help="Repeatable provided Public testbench path.",
        )
        source_parser.add_argument(
            "--hidden-tests",
            choices=("auto", "none"),
            default=None,
            help=(
                "Hidden source mode when --hidden-test is absent. "
                "Default: auto."
            ),
        )
        source_parser.add_argument(
            "--hidden-test",
            dest="hidden_tests_provided",
            action="append",
            default=[],
            help="Repeatable provided Hidden testbench path.",
        )
        source_parser.add_argument(
            "--max-testbench-repairs",
            type=_repair_attempt_count,
            default=DEFAULT_TESTBENCH_REPAIR_ATTEMPTS,
            action=_StoreWithExplicitFlag,
            help=(
                "Bounded Public Testbench repair attempts. "
                f"Default: {DEFAULT_TESTBENCH_REPAIR_ATTEMPTS}; "
                f"valid range: 1..{REPAIR_ATTEMPT_SAFETY_CEILING}."
            ),
        )
        source_parser.add_argument(
            "--max-candidate-repairs",
            type=_repair_attempt_count,
            default=DEFAULT_CANDIDATE_REPAIR_ATTEMPTS,
            action=_StoreWithExplicitFlag,
            help=(
                "Bounded formal Candidate repair attempts. "
                f"Default: {DEFAULT_CANDIDATE_REPAIR_ATTEMPTS}; "
                f"valid range: 1..{REPAIR_ATTEMPT_SAFETY_CEILING}."
            ),
        )
        budget_profile = run_budget_profile_for_mode(source_command)
        budget_defaults = budget_profile.system_defaults
        budget_ceilings = budget_profile.system_safety_ceilings
        for option, field_name, value_type, label in (
            ("--max-llm-calls", "max_llm_calls", int, "LLM calls"),
            ("--max-tool-calls", "max_tool_calls", int, "tool calls"),
            (
                "--max-compile-calls",
                "max_compile_calls",
                int,
                "compile calls",
            ),
            ("--max-csim-calls", "max_csim_calls", int, "CSIM calls"),
            (
                "--max-csynth-calls",
                "max_csynth_calls",
                int,
                "CSYNTH calls",
            ),
            ("--max-cosim-calls", "max_cosim_calls", int, "RTL COSIM calls"),
            (
                "--max-wall-time-s",
                "max_wall_time_s",
                float,
                "wall-clock seconds",
            ),
        ):
            source_parser.add_argument(
                option,
                type=value_type,
                help=(
                    f"Hard run limit for {label}. "
                    f"Selected profile: {budget_profile.name}; "
                    f"system default: {getattr(budget_defaults, field_name)}; "
                    f"safety ceiling: "
                    f"{getattr(budget_ceilings, field_name)}."
                ),
            )
        source_parser.add_argument(
            "--token-budget",
            type=int,
            help=(
                "Observed-only soft token budget; does not stop execution."
            ),
        )
        source_parser.add_argument(
            "--cost-budget",
            help=(
                "Observed-only soft cost budget in the selected "
                "pricing snapshot currency; does not stop execution."
            ),
        )
        source_parser.add_argument(
            "--csim-timeout-s",
            type=_csim_timeout,
            default=DEFAULT_CSIM_TIMEOUT_S,
            help=(
                "Per-CSIM timeout in seconds. "
                f"Default: {DEFAULT_CSIM_TIMEOUT_S}; "
                f"valid range: 1..{CSIM_TIMEOUT_SAFETY_CEILING}."
            ),
        )
        source_parser.add_argument(
            "--csynth-timeout-s",
            type=_csynth_timeout,
            default=DEFAULT_CSYNTH_TIMEOUT_S,
            help=(
                "Per-CSYNTH timeout in seconds. "
                f"Default: {DEFAULT_CSYNTH_TIMEOUT_S}; "
                f"valid range: 1..{CSYNTH_TIMEOUT_SAFETY_CEILING}."
            ),
        )
        source_parser.add_argument(
            "--cosim-timeout-s",
            type=_cosim_timeout,
            default=DEFAULT_COSIM_TIMEOUT_S,
            help=(
                f"Per-RTL-COSIM timeout. Default: {DEFAULT_COSIM_TIMEOUT_S}; "
                f"valid range: 1..{COSIM_TIMEOUT_SAFETY_CEILING}."
            ),
        )
        source_parser.add_argument(
            "--cosim-policy",
            choices=("required", "off"),
            default="required",
            help=(
                "Public RTL COSIM policy. required is normal; "
                "off is development-only."
            ),
        )
        source_parser.add_argument(
            "--public-test-contract",
            dest="public_test_contracts_provided",
            action="append",
            default=[],
            type=Path,
            help=(
                "Repeatable typed contract JSON paired by order with "
                "--public-test. Runtime contract v2 may declare explicit "
                "COSIM m_axi interface depths."
            ),
        )
        source_parser.add_argument(
            "--output-dir",
            type=Path,
            help=(
                "Exact empty or not-yet-created persistent artifact "
                "directory for this run. Temporary tool work remains under "
                "WORK_DIR/AGREFACTOR_WORK_ROOT."
            ),
        )
        source_parser.add_argument(
            "--run-id",
            help="Optional stable run identifier.",
        )
        output_group = source_parser.add_mutually_exclusive_group()
        output_group.add_argument(
            "--json",
            dest="json_output",
            action="store_true",
            help="Emit one stable machine-readable product summary.",
        )
        output_group.add_argument(
            "--verbose",
            action="store_true",
            help="Emit phase-level progress and the concise summary.",
        )
        output_group.add_argument(
            "--debug",
            action="store_true",
            help=(
                "Tee complete safe model/tool diagnostics while retaining "
                "all captured logs in artifacts."
            ),
        )

    return parser


def load_task_file(path: Path) -> TaskSpec:
    """Load and validate a TaskSpec from a JSON file."""

    with path.open("r", encoding="utf-8") as file:
        data: Any = json.load(file)

    if not isinstance(data, dict):
        raise TypeError("Task file root must be a JSON object")

    return TaskSpec.from_dict(data)


def _write_normalized_task(
    task: TaskSpec,
    *,
    output: Path | None,
    stdout: TextIO,
) -> None:
    payload = json.dumps(
        task.to_dict(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"

    if output is None:
        stdout.write(payload)
        return

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(payload, encoding="utf-8")
    stdout.write(f"Validated task written to: {output}\n")


def _dry_run_handler(phase: RunPhase):
    def handler(context) -> PhaseResult:
        context.trace.record(
            "dry_run.checked",
            phase=phase.value,
            status="success",
            metadata={
                "kernel_name": context.task.kernel_name,
                "kernel_path": context.task.kernel_path,
            },
        )
        return PhaseResult(
            phase=phase,
            status=PhaseStatus.SUCCEEDED,
            summary=f"Dry-run completed for {phase.value}",
            metadata={"dry_run": True},
        )

    return handler


def _run_dry_task(
    task: TaskSpec,
    *,
    run_id: str | None,
    trace_path: Path | None,
) -> RunResult:
    runner = UnifiedRunner(
        {
            RunPhase.REFACTOR: _dry_run_handler(RunPhase.REFACTOR),
            RunPhase.OPTIMIZE: _dry_run_handler(RunPhase.OPTIMIZE),
        }
    )
    return runner.run(
        task,
        run_id=run_id,
        trace_path=trace_path,
    )


def _build_cli_legacy_settings(
    args,
) -> LegacyRefactorSettings:
    effective_config = None
    logical_name = None
    if args.model is not None:
        if (
            not isinstance(args.model, str)
            or not args.model.strip()
        ):
            raise ValueError(
                "--model must not be empty"
            )
        logical_name = args.model.strip()

    typed_resolution_requested = (
        logical_name is not None
        and args.model_family is not None
    )
    if typed_resolution_requested:
        if (
            not isinstance(args.api_key_env, str)
            or not args.api_key_env.strip()
        ):
            raise ValueError(
                "--api-key-env must not be empty"
            )
        provider = OpenAICompatibleProvider(
            default_base_url=args.base_url,
            default_api_key_env=args.api_key_env,
        )
        registry = ModelRegistry()
        registry.register_provider(provider)
        registry.register_model(
            ModelSpec(
                name=logical_name,
                provider=provider.name,
                model=logical_name,
                family=args.model_family,
                base_url=args.base_url,
                api_key_env=args.api_key_env,
            )
        )
        call_parameters: dict[str, Any] = {}
        if args.reasoning_effort is not None:
            call_parameters["reasoning_effort"] = (
                args.reasoning_effort
            )
        effective_config = (
            registry.resolve_effective_config(
                logical_name,
                parameters=call_parameters,
            )
        )

    return LegacyRefactorSettings(
        effective_model_config=effective_config,
        model=logical_name,
        base_url=args.base_url,
        reasoning_effort=(
            None
            if effective_config is not None
            else args.reasoning_effort
        ),
        max_retry_attempts=args.max_retry_attempts,
        enable_testbench_repair=(
            args.enable_testbench_repair
        ),
        max_testbench_repair_attempts=(
            args.max_testbench_repair_attempts
        ),
        testbench_repair_model=(
            args.testbench_repair_model
        ),
        testbench_repair_api_key_env=(
            args.testbench_repair_api_key_env
        ),
        output_dir=args.output_dir,
        debug=args.debug,
    )


def _run_legacy_refactor(
    task: TaskSpec,
    *,
    run_id: str | None,
    trace_path: Path | None,
    settings: LegacyRefactorSettings,
) -> RunResult:
    if task.mode is not RunMode.REFACTOR:
        raise ValueError(
            "Legacy execution currently supports only mode='refactor'. "
            "Use the normal optimize/full source commands; advanced legacy run supports refactor only."
        )

    runner = UnifiedRunner(
        {
            RunPhase.REFACTOR: LegacyRefactorAdapter(settings),
        }
    )
    return runner.run(
        task,
        run_id=run_id,
        trace_path=trace_path,
    )


def _read_required_code(
    path: Path,
    label: str,
) -> str:
    value = path.read_text(encoding="utf-8")
    if not value.strip():
        raise ValueError(
            f"{label} must not be empty: {path}"
        )
    return value


def _task_relative_path(
    task_file: Path,
    value: str,
) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = task_file.parent / path
    return path


def _load_candidate_repair_request(
    task: TaskSpec,
    *,
    task_file: Path,
    candidate_file: Path,
    original_file: Path | None,
    preflight_testbench_file: Path | None,
    prompt_public_testbench_file: Path | None,
    max_attempts: int,
) -> CandidateRepairOrchestrationRequest:
    if (
        isinstance(max_attempts, bool)
        or not isinstance(max_attempts, int)
        or max_attempts <= 0
    ):
        raise ValueError(
            "--max-candidate-repair-attempts "
            "must be positive"
        )

    candidate_path = candidate_file.expanduser()
    original_path = (
        original_file.expanduser()
        if original_file is not None
        else _task_relative_path(
            task_file,
            task.kernel_path,
        )
    )
    if preflight_testbench_file is not None:
        preflight_path = (
            preflight_testbench_file.expanduser()
        )
    elif task.testbench_path is not None:
        preflight_path = _task_relative_path(
            task_file,
            task.testbench_path,
        )
    else:
        raise ValueError(
            "--repair-aware requires "
            "--preflight-testbench-file or "
            "TaskSpec.testbench_path"
        )

    suite_codes: dict[str, str] = {}
    public_codes: list[str] = []
    for suite in task.test_suites:
        if suite.testbench_path is None:
            raise ValueError(
                "repair-aware suites require "
                f"testbench_path: {suite.suite_id}"
            )
        suite_path = _task_relative_path(
            task_file,
            suite.testbench_path,
        )
        code = _read_required_code(
            suite_path,
            f"suite testbench {suite.suite_id}",
        )
        suite_codes[suite.suite_id] = code
        if suite.split is EvaluationSplit.PUBLIC:
            public_codes.append(code)

    if prompt_public_testbench_file is not None:
        prompt_public_code = _read_required_code(
            prompt_public_testbench_file.expanduser(),
            "prompt public testbench",
        )
    elif len(public_codes) == 1:
        prompt_public_code = public_codes[0]
    elif len(public_codes) > 1:
        raise ValueError(
            "multiple public suites require "
            "--prompt-public-testbench-file"
        )
    else:
        prompt_public_code = None

    return CandidateRepairOrchestrationRequest(
        initial_candidate=_read_required_code(
            candidate_path,
            "candidate source",
        ),
        original_code=_read_required_code(
            original_path,
            "original source",
        ),
        preflight_testbench_code=_read_required_code(
            preflight_path,
            "preflight testbench",
        ),
        suite_testbench_codes=suite_codes,
        prompt_public_testbench_code=prompt_public_code,
        max_attempts=max_attempts,
    )


def _build_cli_candidate_adapter(args) -> CandidateModelAdapter:
    if not isinstance(args.model, str) or not args.model.strip():
        raise ValueError(
            "--model is required for --repair-aware"
        )
    if (
        not isinstance(args.api_key_env, str)
        or not args.api_key_env.strip()
    ):
        raise ValueError(
            "--api-key-env must not be empty"
        )

    provider = OpenAICompatibleProvider(
        default_base_url=args.base_url,
        default_api_key_env=args.api_key_env,
    )
    registry = ModelRegistry()
    registry.register_provider(provider)
    logical_name = args.model.strip()
    registry.register_model(
        ModelSpec(
            name=logical_name,
            provider=provider.name,
            model=logical_name,
            family=args.model_family,
            base_url=args.base_url,
            api_key_env=args.api_key_env,
        )
    )
    call_parameters: dict[str, Any] = {}
    if args.reasoning_effort is not None:
        call_parameters["reasoning_effort"] = (
            args.reasoning_effort
        )
    effective_config = registry.resolve_effective_config(
        logical_name,
        parameters=call_parameters,
    )
    return CandidateModelAdapter(
        registry=registry,
        effective_config=effective_config,
    )


def _run_repair_aware_refactor(
    task: TaskSpec,
    *,
    task_file: Path,
    args,
) -> RunResult:
    if task.mode is not RunMode.REFACTOR:
        raise ValueError(
            "Repair-aware execution currently "
            "supports only mode='refactor'."
        )
    if args.candidate_file is None:
        raise ValueError(
            "--candidate-file is required for --repair-aware"
        )
    if args.repair_work_dir is None:
        raise ValueError(
            "--repair-work-dir is required for --repair-aware"
        )
    if args.artifact_dir is None:
        raise ValueError(
            "--artifact-dir is required for --repair-aware"
        )

    request = _load_candidate_repair_request(
        task,
        task_file=task_file,
        candidate_file=args.candidate_file,
        original_file=args.original_file,
        preflight_testbench_file=args.preflight_testbench_file,
        prompt_public_testbench_file=(
            args.prompt_public_testbench_file
        ),
        max_attempts=args.max_candidate_repair_attempts,
    )
    adapter = _build_cli_candidate_adapter(args)
    phase = build_candidate_repair_phase(
        model_adapter=adapter,
        request=request,
        work_root=args.repair_work_dir,
        artifact_root=args.artifact_dir,
        csynth_timelimit=args.csynth_timelimit,
        csim_timelimit=args.csim_timelimit,
        cosim_timelimit=args.cosim_timelimit,
        cosim_policy=args.cosim_policy,
    )
    runner = UnifiedRunner(
        {RunPhase.REFACTOR: phase},
        budget_limits=BudgetLimits(
            max_llm_calls=args.max_candidate_repair_attempts
        ),
    )
    trace_path = (
        args.trace
        if args.trace is not None
        else args.artifact_dir / "trace.jsonl"
    )
    return runner.run(
        task,
        run_id=args.run_id,
        trace_path=trace_path,
        artifact_root=args.artifact_dir,
        run_metadata={
            "execution_mode": "repair_aware",
            "legacy_mode": False,
            "model_selection": "user_fixed",
        },
    )


def _run_result_to_dict(result: RunResult) -> dict[str, Any]:
    return result.to_dict()


def _write_run_result(
    result: RunResult,
    stdout: TextIO,
    *,
    execution_mode: str,
    artifact_manifest: Path | None = None,
) -> None:
    payload = _run_result_to_dict(result)
    payload["execution_mode"] = execution_mode
    payload["legacy_mode"] = (
        execution_mode == "legacy"
    )
    payload["artifact_manifest"] = (
        None
        if artifact_manifest is None
        else str(artifact_manifest)
    )
    stdout.write(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """Run the CLI and return a process exit code."""

    parser = build_parser()
    args = parser.parse_args(argv)
    _warn_deprecated_advanced_run_modes(args)

    try:
        if args.command in ("refactor", "optimize", "full"):
            from agrefactor.product import (
                ProductOutputMode,
                SourceCommandRejected,
                build_product_summary,
                build_rejection_summary,
                render_product_output,
                resolve_output_mode,
                run_source_command,
            )

            output_mode = resolve_output_mode(args)
            if output_mode in {
                ProductOutputMode.VERBOSE,
                ProductOutputMode.DEBUG,
            }:
                stdout.write(f"Phase {args.command}: started\n")
            try:
                execution = run_source_command(
                    args,
                    stdout=stdout,
                    stderr=stderr,
                )
            except SourceCommandRejected as exc:
                render_product_output(
                    build_rejection_summary(exc.artifact_root),
                    mode=output_mode,
                    stdout=stdout,
                )
                return 2
            summary = build_product_summary(
                execution.result,
                artifact_root=execution.layout.artifact_root,
            )
            render_product_output(
                summary,
                mode=output_mode,
                stdout=stdout,
            )
            return 0 if execution.result.succeeded else 1

        if args.command == "validate-task":
            task = load_task_file(args.task_file)
            _write_normalized_task(
                task,
                output=args.output,
                stdout=stdout,
            )
            return 0

        if args.command == "run":
            execution_mode_count = sum(
                bool(value)
                for value in (
                    args.dry_run,
                    args.legacy,
                    args.repair_aware,
                )
            )
            if execution_mode_count != 1:
                stderr.write(
                    "Choose exactly one execution mode: "
                    "--dry-run, --legacy, or "
                    "--repair-aware.\n"
                )
                return 2

            task = load_task_file(args.task_file)

            if args.dry_run:
                execution_mode = "dry_run"
                result = _run_dry_task(
                    task,
                    run_id=args.run_id,
                    trace_path=args.trace,
                )
            elif args.repair_aware:
                execution_mode = "repair_aware"
                result = _run_repair_aware_refactor(
                    task,
                    task_file=args.task_file,
                    args=args,
                )
            else:
                execution_mode = "legacy"
                settings = _build_cli_legacy_settings(
                    args
                )
                result = _run_legacy_refactor(
                    task,
                    run_id=args.run_id,
                    trace_path=args.trace,
                    settings=settings,
                )

            artifact_manifest = (
                args.artifact_dir
                / "run_artifact_manifest.json"
                if args.repair_aware
                and args.artifact_dir is not None
                else None
            )
            _write_run_result(
                result,
                stdout,
                execution_mode=execution_mode,
                artifact_manifest=artifact_manifest,
            )
            return 0 if result.succeeded else 1

    except (
        FileExistsError,
        FileNotFoundError,
        IsADirectoryError,
        PermissionError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        stderr.write(f"Command failed: {exc}\n")
        return 2

    stderr.write(f"Unsupported command: {args.command}\n")
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
