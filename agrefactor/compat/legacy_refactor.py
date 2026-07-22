"""Compatibility adapter for the existing ``flow.new`` refactoring flow."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
import sys
from typing import Any

from agrefactor.config import TaskSpec
from agrefactor.models import EffectiveModelConfig
from agrefactor.runtime import (
    PhaseResult,
    PhaseStatus,
    RunContext,
    RunPhase,
)

LegacyRefactorBackend = Callable[..., Any]
LegacyUsageSupplier = Callable[[], Mapping[str, Any]]


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


_LEGACY_PROVIDER_API_TYPES = {
    "openai-compatible": "openai",
}
_LEGACY_LLM_RESERVED_KEYS = frozenset(
    {
        "model",
        "api_type",
        "base_url",
        "api_key",
        "api_key_env",
    }
)


def _build_effective_legacy_llm_config(
    config: EffectiveModelConfig,
) -> dict[str, Any]:
    if not isinstance(config, EffectiveModelConfig):
        raise TypeError(
            "config must be an EffectiveModelConfig"
        )
    api_type = _LEGACY_PROVIDER_API_TYPES.get(
        config.provider_name
    )
    if api_type is None:
        raise ValueError(
            "Legacy AG2 translation does not support provider "
            f"{config.provider_name!r}"
        )

    parameters = config.parameters
    conflicts = sorted(
        key
        for key in parameters
        if key in _LEGACY_LLM_RESERVED_KEYS
    )
    if conflicts:
        raise ValueError(
            "effective model parameters contain reserved "
            "Legacy AG2 identity keys: "
            + ", ".join(conflicts)
        )

    translated: dict[str, Any] = {
        "model": config.model_id,
        "api_type": api_type,
    }
    if config.base_url is not None:
        translated["base_url"] = config.base_url
    translated.update(parameters)
    return translated


@dataclass(frozen=True, slots=True)
class LegacyRefactorSettings:
    """Options forwarded to the existing ``hls_refactor_with_rag`` function."""

    knowledge_db_path: str = "./knowledge_db/tmp_db"
    embedding_model: str = "all-MiniLM-L6-v2"
    enable_rag: bool = False
    enable_rag_update: bool = False
    reset_knowledge_db: bool = False
    output_dir: str | None = None
    max_retry_attempts: int = 3
    hetero_enabled: bool = False
    debug: bool = False
    model: str | None = None
    remote: bool = False
    reasoning_effort: str | None = None
    base_url: str | None = None
    effective_model_config: EffectiveModelConfig | None = None
    enable_testbench_repair: bool = False
    max_testbench_repair_attempts: int = 2
    testbench_repair_model: str | None = None
    testbench_repair_api_key_env: str = "OPENAI_API_KEY"
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
        config = self.effective_model_config
        if config is not None:
            if not isinstance(
                config,
                EffectiveModelConfig,
            ):
                raise TypeError(
                    "effective_model_config must be an "
                    "EffectiveModelConfig or None"
                )
            if self.model is not None:
                if (
                    not isinstance(self.model, str)
                    or self.model.strip() != config.model_id
                ):
                    raise ValueError(
                        "Legacy compatibility model conflicts "
                        "with effective_model_config.model_id"
                    )
            if self.base_url is not None:
                if (
                    not isinstance(self.base_url, str)
                    or self.base_url.strip()
                    != (config.base_url or "")
                ):
                    raise ValueError(
                        "Legacy compatibility base_url conflicts "
                        "with effective_model_config.base_url"
                    )
            if self.reasoning_effort is not None:
                raise ValueError(
                    "reasoning_effort is a parallel authority "
                    "when effective_model_config is provided"
                )

        if self.max_retry_attempts < 0:
            raise ValueError("max_retry_attempts must not be negative")
        if self.max_testbench_repair_attempts < 0:
            raise ValueError(
                "max_testbench_repair_attempts must not be negative"
            )
        if (
            not isinstance(self.testbench_repair_api_key_env, str)
            or not self.testbench_repair_api_key_env.strip()
        ):
            raise ValueError(
                "testbench_repair_api_key_env must not be empty"
            )
        if self.enable_testbench_repair:
            if self.remote:
                raise ValueError(
                    "testbench repair currently supports local "
                    "validation only"
                )
            if self.max_testbench_repair_attempts < 1:
                raise ValueError(
                    "enabled testbench repair requires at least "
                    "one repair attempt"
                )
            if not (
                self.testbench_repair_model
                or self.model
                or self.effective_model_config is not None
            ):
                raise ValueError(
                    "enabled testbench repair requires "
                    "testbench_repair_model or model"
                )
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

    effective_config = settings.effective_model_config
    if effective_config is None:
        resolved_model = settings.model
        resolved_reasoning = settings.reasoning_effort
        resolved_base_url = settings.base_url
        llm_config_override = None
        effective_manifest = None
        family_instruction = None
        model_configuration_source = (
            "legacy_compatibility"
        )
    else:
        resolved_model = effective_config.model_id
        resolved_reasoning = (
            effective_config.parameters.get(
                "reasoning_effort"
            )
        )
        resolved_base_url = effective_config.base_url
        llm_config_override = (
            _build_effective_legacy_llm_config(
                effective_config
            )
        )
        effective_manifest = (
            effective_config.to_manifest()
        )
        family_instruction = (
            effective_config.family_instruction
        )
        model_configuration_source = (
            "effective_model_config"
        )

    return {
        "kernel_path": task.kernel_path,
        "kernel_name": task.kernel_name,
        "target_profile": task.target.to_dict(),
        "knowledge_db_path": settings.knowledge_db_path,
        "embedding_model": settings.embedding_model,
        "enable_rag": settings.enable_rag,
        "enable_rag_update": settings.enable_rag_update,
        "reset_knowledge_db": settings.reset_knowledge_db,
        "output_dir": settings.output_dir,
        "max_retry_attempts": settings.max_retry_attempts,
        "hetero_enabled": settings.hetero_enabled,
        "debug": 1 if settings.debug else 0,
        "model": resolved_model,
        "remote": settings.remote,
        "reasoning_effort": resolved_reasoning,
        "base_url": resolved_base_url,
        "llm_config_override": llm_config_override,
        "effective_model_config_manifest": (
            effective_manifest
        ),
        "family_instruction": family_instruction,
        "model_configuration_source": (
            model_configuration_source
        ),
        "enable_testbench_repair": (
            settings.enable_testbench_repair
        ),
        "max_testbench_repair_attempts": (
            settings.max_testbench_repair_attempts
        ),
        "testbench_repair_model": (
            settings.testbench_repair_model
        ),
        "testbench_repair_api_key_env": (
            settings.testbench_repair_api_key_env
        ),
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
        usage_supplier: LegacyUsageSupplier | None = None,
    ) -> None:
        self._settings = settings or LegacyRefactorSettings()
        self._backend = backend
        self._usage_supplier = usage_supplier

    def __call__(self, context: RunContext) -> PhaseResult:
        kwargs = build_legacy_refactor_kwargs(
            context.task,
            self._settings,
        )
        kwargs["budget"] = context.budget
        backend = self._backend or self._load_backend()
        effective_manifest = kwargs.get(
            "effective_model_config_manifest"
        )
        configuration_source = kwargs.get(
            "model_configuration_source"
        )

        context.trace.record(
            "legacy_refactor.invoked",
            phase=RunPhase.REFACTOR.value,
            status="running",
            metadata={
                "kernel_path": context.task.kernel_path,
                "kernel_name": context.task.kernel_name,
                "model_configuration_source": (
                    configuration_source
                ),
                "effective_model_config": (
                    effective_manifest
                ),
            },
        )

        try:
            raw_result = _call_backend_preserving_standard_streams(
                backend,
                kwargs,
            )
        except Exception as exc:
            usage_metadata = self._record_usage_after_error(context)
            context.trace.record(
                "legacy_refactor.errored",
                phase=RunPhase.REFACTOR.value,
                status="error",
                message=f"{type(exc).__name__}: {exc}",
                metadata={"legacy_usage": usage_metadata},
            )
            raise

        success = self._extract_success(raw_result)
        usage_metadata = self._record_usage(
            context,
            raw_result=raw_result,
        )

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
            metadata={
                "adapter": "flow.new",
                "legacy_usage": usage_metadata,
                "model_configuration_source": (
                    configuration_source
                ),
                "effective_model_config": (
                    effective_manifest
                ),
            },
        )

    def _record_usage_after_error(
        self,
        context: RunContext,
    ) -> dict[str, Any]:
        """Record partial usage without masking the backend error."""

        try:
            return self._record_usage(context)
        except Exception as exc:
            metadata = {
                "accounting_mode": "unavailable",
                "reason": (
                    "Usage accounting failed while preserving a backend "
                    f"error: {type(exc).__name__}: {exc}"
                ),
                "llm_calls_tracked": False,
                "tool_calls_tracked": False,
            }
            context.trace.record(
                "legacy_refactor.usage_unavailable",
                phase=RunPhase.REFACTOR.value,
                status="warning",
                metadata=metadata,
            )
            return metadata

    def _record_usage(
        self,
        context: RunContext,
        *,
        raw_result: Any = None,
    ) -> dict[str, Any]:
        supplier = self._usage_supplier

        if supplier is None and self._backend is None:
            supplier = self._load_usage_supplier()

        if supplier is None:
            metadata = {
                "accounting_mode": "unavailable",
                "reason": "No usage supplier for injected legacy backend",
                "llm_calls_tracked": False,
                "tool_calls_tracked": False,
            }
            context.trace.record(
                "legacy_refactor.usage_unavailable",
                phase=RunPhase.REFACTOR.value,
                status="warning",
                metadata=metadata,
            )
            return metadata

        try:
            raw_summary = supplier()
            if not isinstance(raw_summary, Mapping):
                raise TypeError("usage supplier must return a mapping")
            metadata = _normalize_usage(raw_summary)
            repair_usage = _collect_testbench_repair_usage(
                raw_result
            )
            metadata = _merge_testbench_repair_usage(
                metadata,
                repair_usage,
            )
        except Exception as exc:
            metadata = {
                "accounting_mode": "unavailable",
                "reason": f"{type(exc).__name__}: {exc}",
                "llm_calls_tracked": False,
                "tool_calls_tracked": False,
            }
            context.trace.record(
                "legacy_refactor.usage_unavailable",
                phase=RunPhase.REFACTOR.value,
                status="warning",
                metadata=metadata,
            )
            return metadata

        context.budget.consume(
            llm_calls=metadata.get("known_llm_calls", 0),
            tokens=metadata["tokens"],
            cost_usd=metadata["cost_usd"],
        )
        context.trace.record(
            "legacy_refactor.usage_recorded",
            phase=RunPhase.REFACTOR.value,
            status="recorded",
            metadata=metadata,
        )
        return metadata

    @staticmethod
    def _load_backend() -> LegacyRefactorBackend:
        from flow.new import hls_refactor_with_rag

        return hls_refactor_with_rag

    @staticmethod
    def _load_usage_supplier() -> LegacyUsageSupplier:
        from flow.base_agent import get_agrefactorpp_usage_summary

        return get_agrefactorpp_usage_summary

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


def _extract_backend_context(
    raw_result: Any,
) -> Mapping[str, Any] | None:
    if not (
        isinstance(raw_result, tuple)
        and len(raw_result) >= 2
    ):
        return None

    payload = raw_result[1]
    if isinstance(payload, Mapping):
        return payload

    data = getattr(payload, "data", None)
    if isinstance(data, Mapping):
        return data

    return None


def _collect_testbench_repair_usage(
    raw_result: Any,
) -> dict[str, Any]:
    backend_context = _extract_backend_context(raw_result)
    empty = {
        "artifacts": [],
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "known_cost_usd": 0.0,
        "cost_complete": True,
        "unknown_cost_calls": 0,
        "models": [],
    }
    if backend_context is None:
        return empty

    candidates: list[Mapping[str, Any]] = []
    top_level = backend_context.get("testbench_repair")
    if isinstance(top_level, Mapping):
        candidates.append(top_level)

    histories = backend_context.get("csynth_csim_history")
    if isinstance(histories, list):
        for history in histories:
            if not isinstance(history, Mapping):
                continue
            repair = history.get("testbench_repair")
            if isinstance(repair, Mapping):
                candidates.append(repair)

    seen: set[tuple[str, Any]] = set()
    unique: list[Mapping[str, Any]] = []
    for repair in candidates:
        artifact = (
            repair.get("artifact_path")
            or repair.get("repair_artifact_path")
        )
        key = (
            ("artifact", str(artifact))
            if artifact
            else ("object", id(repair))
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(repair)

    result = dict(empty)
    model_names: list[str] = []

    for repair in unique:
        usage = repair.get("model_usage")
        if not isinstance(usage, Mapping):
            continue

        calls = _nonnegative_int(usage.get("calls"))
        prompt_tokens = _nonnegative_int(
            usage.get("prompt_tokens")
        )
        completion_tokens = _nonnegative_int(
            usage.get("completion_tokens")
        )
        total_tokens = _nonnegative_int(
            usage.get(
                "total_tokens",
                prompt_tokens + completion_tokens,
            )
        )

        result["calls"] += calls
        result["prompt_tokens"] += prompt_tokens
        result["completion_tokens"] += completion_tokens
        result["total_tokens"] += total_tokens

        artifact = (
            repair.get("artifact_path")
            or repair.get("repair_artifact_path")
        )
        if artifact:
            result["artifacts"].append(str(artifact))

        raw_cost = usage.get("cost_usd")
        if raw_cost is None:
            if calls > 0:
                result["cost_complete"] = False
                result["unknown_cost_calls"] += calls
        else:
            result["known_cost_usd"] += _nonnegative_float(
                raw_cost
            )

        models = usage.get("models")
        if isinstance(models, (list, tuple)):
            for model in models:
                if isinstance(model, str) and model:
                    model_names.append(model)

    result["models"] = list(dict.fromkeys(model_names))
    return result


def _merge_testbench_repair_usage(
    base: dict[str, Any],
    repair: Mapping[str, Any],
) -> dict[str, Any]:
    repair_tokens = _nonnegative_int(repair.get("total_tokens"))
    repair_calls = _nonnegative_int(repair.get("calls"))

    if repair_tokens == 0 and repair_calls == 0:
        return base

    merged = dict(base)
    merged["accounting_mode"] = "post_hoc_combined"
    merged["tokens"] = (
        _nonnegative_int(base.get("tokens"))
        + repair_tokens
    )
    merged["cost_usd"] = (
        _nonnegative_float(base.get("cost_usd"))
        + _nonnegative_float(repair.get("known_cost_usd"))
    )
    merged["known_llm_calls"] = repair_calls
    merged["llm_calls_complete"] = False
    merged["llm_calls_tracked"] = False
    merged["cost_complete"] = bool(
        repair.get("cost_complete", True)
    )
    merged["unknown_cost_calls"] = _nonnegative_int(
        repair.get("unknown_cost_calls")
    )
    merged["model_breakdown_complete"] = False
    merged["testbench_repair_usage"] = dict(repair)
    merged["deduplication"] = (
        "AutoGen agent usage and provider-backed testbench repair "
        "usage are separate sources; repair artifacts are counted "
        "once by artifact path."
    )
    return merged


def _normalize_usage(summary: Mapping[str, Any]) -> dict[str, Any]:
    models: dict[str, Any] = {}
    raw_models = summary.get("models", {})

    if isinstance(raw_models, Mapping):
        for model_name, raw_info in raw_models.items():
            if not isinstance(raw_info, Mapping):
                continue
            models[str(model_name)] = {
                "prompt_tokens": _nonnegative_int(
                    raw_info.get("prompt_tokens")
                ),
                "completion_tokens": _nonnegative_int(
                    raw_info.get("completion_tokens")
                ),
                "total_tokens": _nonnegative_int(
                    raw_info.get("total_tokens")
                ),
                "cost_usd": _nonnegative_float(
                    raw_info.get("cost", raw_info.get("total_cost"))
                ),
            }

    return {
        "accounting_mode": "post_hoc",
        "source": str(summary.get("source", "unknown")),
        "registered_agents": _nonnegative_int(summary.get("agents")),
        "tokens": _nonnegative_int(summary.get("total_tokens")),
        "cost_usd": _nonnegative_float(summary.get("total_cost")),
        "models": models,
        "llm_calls_tracked": False,
        "tool_calls_tracked": False,
    }


def _nonnegative_int(value: Any) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _nonnegative_float(value: Any) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if not isfinite(number) or number < 0:
        return 0.0
    return number
