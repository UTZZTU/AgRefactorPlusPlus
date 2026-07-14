"""Compatibility adapter for the existing ``flow.new`` refactoring flow."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any

from agrefactor.config import TaskSpec
from agrefactor.runtime import (
    PhaseResult,
    PhaseStatus,
    RunContext,
    RunPhase,
)

LegacyRefactorBackend = Callable[..., Any]


def _call_backend_preserving_standard_streams(
    backend: LegacyRefactorBackend,
    kwargs: dict[str, Any],
) -> Any:
    """Call a legacy backend without leaking global stdout/stderr changes."""

    original_stdout = sys.stdout
    original_stderr = sys.stderr

    try:
        return backend(**kwargs)
    finally:
        redirected_streams = (sys.stdout, sys.stderr)
        sys.stdout = original_stdout
        sys.stderr = original_stderr

        closed_ids: set[int] = set()
        for stream in redirected_streams:
            stream_id = id(stream)
            if stream_id in closed_ids:
                continue
            closed_ids.add(stream_id)

            if stream is original_stdout or stream is original_stderr:
                continue

            try:
                stream.flush()
            except Exception:
                pass

            try:
                stream.close()
            except Exception:
                pass


@dataclass(frozen=True, slots=True)
class LegacyRefactorSettings:
    """Options forwarded to the existing ``hls_refactor_with_rag`` function."""

    knowledge_db_path: str = "./knowledge_db/tmp_db"
    embedding_model: str = "all-MiniLM-L6-v2"
    enable_rag: bool = False
    enable_rag_update: bool = False
    reset_knowledge_db: bool = False
    output_dir: str | None = None
    max_retry_attempts: int = 4
    hetero_enabled: bool = False
    debug: bool = False
    model: str | None = None
    remote: bool = False
    reasoning_effort: str | None = None
    base_url: str | None = None
    external_tb_instruction: str | None = None
    external_kernel_name: str | None = None
    enable_tb_coverage_loop: bool = False
    public_tb_rounds: int = 3
    public_tb_target: float = 80.0
    enable_hidden_tb_eval: bool = False
    hidden_tb_rounds: int = 6
    hidden_tb_trajectories: int = 3
    hidden_tb_target: float = 90.0
    golden_tb_cache_dir: str | None = None
    golden_tb_cache_key: str | None = None
    use_cached_tb_as_public: bool = False

    def __post_init__(self) -> None:
        if self.max_retry_attempts < 1:
            raise ValueError("max_retry_attempts must be at least 1")
        if self.public_tb_rounds < 1:
            raise ValueError("public_tb_rounds must be at least 1")
        if self.hidden_tb_rounds < 1:
            raise ValueError("hidden_tb_rounds must be at least 1")
        if self.hidden_tb_trajectories < 1:
            raise ValueError("hidden_tb_trajectories must be at least 1")
        if not 0.0 <= self.public_tb_target <= 100.0:
            raise ValueError("public_tb_target must be between 0 and 100")
        if not 0.0 <= self.hidden_tb_target <= 100.0:
            raise ValueError("hidden_tb_target must be between 0 and 100")


def build_legacy_refactor_kwargs(
    task: TaskSpec,
    settings: LegacyRefactorSettings,
) -> dict[str, Any]:
    """Translate shared task data into the existing flow's keyword arguments."""

    if not isinstance(task, TaskSpec):
        raise TypeError("task must be a TaskSpec")
    if not isinstance(settings, LegacyRefactorSettings):
        raise TypeError("settings must be LegacyRefactorSettings")

    external_testbench: str | None = None
    if task.testbench_path is not None:
        external_testbench = Path(task.testbench_path).read_text(
            encoding="utf-8"
        )

    return {
        "kernel_path": task.kernel_path,
        "kernel_name": task.kernel_name,
        "knowledge_db_path": settings.knowledge_db_path,
        "embedding_model": settings.embedding_model,
        "enable_rag": settings.enable_rag,
        "enable_rag_update": settings.enable_rag_update,
        "reset_knowledge_db": settings.reset_knowledge_db,
        "output_dir": settings.output_dir,
        "max_retry_attempts": settings.max_retry_attempts,
        "hetero_enabled": settings.hetero_enabled,
        "debug": 1 if settings.debug else 0,
        "model": settings.model,
        "remote": settings.remote,
        "reasoning_effort": settings.reasoning_effort,
        "base_url": settings.base_url,
        "external_testbench": external_testbench,
        "external_tb_instruction": settings.external_tb_instruction,
        "external_kernel_name": settings.external_kernel_name,
        "enable_tb_coverage_loop": settings.enable_tb_coverage_loop,
        "public_tb_rounds": settings.public_tb_rounds,
        "public_tb_target": settings.public_tb_target,
        "enable_hidden_tb_eval": settings.enable_hidden_tb_eval,
        "hidden_tb_rounds": settings.hidden_tb_rounds,
        "hidden_tb_trajectories": settings.hidden_tb_trajectories,
        "hidden_tb_target": settings.hidden_tb_target,
        "golden_tb_cache_dir": settings.golden_tb_cache_dir,
        "golden_tb_cache_key": settings.golden_tb_cache_key,
        "use_cached_tb_as_public": settings.use_cached_tb_as_public,
    }


class LegacyRefactorAdapter:
    """Expose the existing refactoring flow as a UnifiedRunner phase handler."""

    def __init__(
        self,
        settings: LegacyRefactorSettings | None = None,
        *,
        backend: LegacyRefactorBackend | None = None,
    ) -> None:
        self._settings = settings or LegacyRefactorSettings()
        self._backend = backend

    def __call__(self, context: RunContext) -> PhaseResult:
        kwargs = build_legacy_refactor_kwargs(
            context.task,
            self._settings,
        )
        backend = self._backend or self._load_backend()

        context.trace.record(
            "legacy_refactor.invoked",
            phase=RunPhase.REFACTOR.value,
            status="running",
            metadata={
                "kernel_path": context.task.kernel_path,
                "kernel_name": context.task.kernel_name,
            },
        )

        raw_result = _call_backend_preserving_standard_streams(
            backend,
            kwargs,
        )
        success = self._extract_success(raw_result)

        context.trace.record(
            "legacy_refactor.returned",
            phase=RunPhase.REFACTOR.value,
            status="succeeded" if success else "failed",
        )

        return PhaseResult(
            phase=RunPhase.REFACTOR,
            status=(
                PhaseStatus.SUCCEEDED
                if success
                else PhaseStatus.FAILED
            ),
            summary=(
                "Legacy refactoring flow completed successfully"
                if success
                else "Legacy refactoring flow reported failure"
            ),
            metadata={"adapter": "flow.new"},
        )

    @staticmethod
    def _load_backend() -> LegacyRefactorBackend:
        # Lazy import keeps unit tests and dry-runs independent of heavy
        # AutoGen, embedding, and HLS dependencies.
        from flow.new import hls_refactor_with_rag

        return hls_refactor_with_rag

    @staticmethod
    def _extract_success(raw_result: Any) -> bool:
        if isinstance(raw_result, bool):
            return raw_result

        if (
            isinstance(raw_result, tuple)
            and raw_result
            and isinstance(raw_result[0], bool)
        ):
            return raw_result[0]

        raise TypeError(
            "Legacy refactor backend must return bool or a tuple "
            "whose first item is bool"
        )
