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
from agrefactor.config import RunMode, TaskSpec
from agrefactor.runtime import (
    PhaseResult,
    PhaseStatus,
    RunPhase,
    RunResult,
    UnifiedRunner,
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
        help="Invoke the existing flow.new refactoring backend.",
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
        help="Legacy refactoring model override.",
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
        help="Optional isolated working directory for the legacy flow.",
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


def _run_result_to_dict(result: RunResult) -> dict[str, Any]:
    usage = result.budget_usage
    return {
        "run_id": result.run_id,
        "task_id": result.task_id,
        "mode": result.mode.value,
        "status": result.status.value,
        "succeeded": result.succeeded,
        "phases": [
            {
                "phase": phase.phase.value,
                "status": phase.status.value,
                "summary": phase.summary,
                "metadata": dict(phase.metadata),
            }
            for phase in result.phases
        ],
        "budget_usage": (
            None
            if usage is None
            else {
                "llm_calls": usage.llm_calls,
                "tool_calls": usage.tool_calls,
                "tokens": usage.tokens,
                "cost_usd": usage.cost_usd,
                "elapsed_s": usage.elapsed_s,
            }
        ),
    }


def _write_run_result(result: RunResult, stdout: TextIO) -> None:
    stdout.write(
        json.dumps(
            _run_result_to_dict(result),
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
            if args.dry_run == args.legacy:
                stderr.write(
                    "Choose exactly one execution mode: --dry-run or --legacy.\n"
                )
                return 2

            task = load_task_file(args.task_file)

            if args.dry_run:
                result = _run_dry_task(
                    task,
                    run_id=args.run_id,
                    trace_path=args.trace,
                )
            else:
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

            _write_run_result(result, stdout)
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
