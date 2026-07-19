"""Command-line entry point for AgRefactor++."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

from agrefactor.compat import (
    LegacyRefactorAdapter,
    LegacyRefactorSettings,
)
from agrefactor.config import (
    EvaluationSplit,
    RunMode,
    TaskSpec,
)
from agrefactor.models import (
    CandidateModelAdapter,
    ModelRegistry,
    ModelSpec,
    OpenAICompatibleProvider,
)
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
        help=(
            "Invoke the existing flow.new "
            "refactoring backend."
        ),
    )
    run_parser.add_argument(
        "--repair-aware",
        action="store_true",
        help=(
            "Run the formal local validation and "
            "bounded candidate-repair path."
        ),
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
            "User-selected model identifier. "
            "Required for --repair-aware."
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
        type=int,
        default=2,
        help="Independent testbench repair budget. Default: 2.",
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
        help="Initial candidate source for --repair-aware.",
    )
    run_parser.add_argument(
        "--original-file",
        type=Path,
        help=(
            "Original source for --repair-aware; "
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
            "for --repair-aware."
        ),
    )
    run_parser.add_argument(
        "--artifact-dir",
        type=Path,
        help=(
            "Empty output root for the versioned "
            "repair-aware run bundle."
        ),
    )
    run_parser.add_argument(
        "--max-candidate-repair-attempts",
        type=int,
        default=2,
        help=(
            "Maximum candidate model calls after "
            "initial validation. Default: 2."
        ),
    )
    run_parser.add_argument(
        "--csynth-timelimit",
        type=int,
        default=300,
        help="Per-CSYNTH timeout in seconds. Default: 300.",
    )
    run_parser.add_argument(
        "--csim-timelimit",
        type=int,
        default=60,
        help="Per-CSIM timeout in seconds. Default: 60.",
    )
    run_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable legacy flow debug output.",
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
            "Use --dry-run for optimize/full until their adapters are added."
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
    return CandidateModelAdapter(
        registry=registry,
        model_name=logical_name,
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

    try:
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
                settings = LegacyRefactorSettings(
                    model=args.model,
                    base_url=args.base_url,
                    reasoning_effort=args.reasoning_effort,
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
