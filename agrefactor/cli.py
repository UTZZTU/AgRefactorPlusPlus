"""Minimal command-line entry point for AgRefactor++."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

from agrefactor.config import TaskSpec


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
    except (
        FileNotFoundError,
        IsADirectoryError,
        PermissionError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        stderr.write(f"Task validation failed: {exc}\n")
        return 2

    stderr.write(f"Unsupported command: {args.command}\n")
    return 2
