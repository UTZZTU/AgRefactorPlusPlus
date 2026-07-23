"""Guard preventing Hidden test sources from model-visible prompts."""

from __future__ import annotations

from collections.abc import Iterable
import hashlib
from pathlib import Path
from typing import Any

from agrefactor.config import EvaluationSplit, TaskSpec


class HiddenTestSourceIsolationError(ValueError):
    pass


def _messages_text(messages: Iterable[Any]) -> str:
    values = []
    for message in messages:
        if isinstance(message, str):
            values.append(message)
            continue
        content = getattr(message, "content", None)
        if isinstance(content, str):
            values.append(content)
    return "\n".join(values)


def assert_hidden_test_sources_absent(
    *,
    task: TaskSpec,
    messages: Iterable[Any],
    artifacts: Iterable[Any] = (),
) -> None:
    """Reject Hidden paths, digests, and exact source-content artifacts."""

    if not isinstance(task, TaskSpec):
        raise TypeError("task must be a TaskSpec")
    prompt_text = _messages_text(messages)
    artifact_values = tuple(artifacts)
    artifact_text = "\n".join(
        value for value in (
            getattr(item, "content", None) for item in artifact_values
        ) if isinstance(value, str)
    )
    for suite in task.test_suites:
        if suite.split is not EvaluationSplit.HIDDEN:
            continue
        source = suite.source
        forbidden = []
        if suite.testbench_path:
            forbidden.append(suite.testbench_path)
        if source is not None:
            if source.operator_artifact_path:
                forbidden.append(source.operator_artifact_path)
            if source.expected_content_sha256:
                forbidden.append(source.expected_content_sha256)
        for marker in forbidden:
            if marker and (marker in prompt_text or marker in artifact_text):
                raise HiddenTestSourceIsolationError(
                    "hidden test source path/digest entered model-visible data"
                )
        if suite.testbench_path:
            candidate = Path(suite.testbench_path).expanduser()
            if candidate.is_file():
                hidden_content = candidate.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
                if hidden_content and (
                    hidden_content in prompt_text
                    or hidden_content in artifact_text
                ):
                    raise HiddenTestSourceIsolationError(
                        "hidden test source content entered model-visible data"
                    )
        if source is None or source.expected_content_sha256 is None:
            continue
        expected = source.expected_content_sha256
        for item in artifact_values:
            content = getattr(item, "content", None)
            if not isinstance(content, str):
                continue
            observed = hashlib.sha256(
                content.encode("utf-8")
            ).hexdigest()
            if observed == expected:
                raise HiddenTestSourceIsolationError(
                    "exact hidden test source content entered a model artifact"
                )
