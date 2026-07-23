"""Factories for provider-backed testbench repair components."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agrefactor.models import (
    EffectiveModelConfig,
    GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE,
    ModelRegistry,
    OpenAICompatibleProvider,
)
from agrefactor.runtime.budget import BudgetManager

from .model_testbench_repairer import ModelTestbenchRepairer


def infer_model_family(model: str) -> str | None:
    """Infer a coarse prompt family without coupling to one provider."""

    if not isinstance(model, str):
        raise TypeError("model must be a string")

    cleaned = model.strip()
    if not cleaned:
        raise ValueError("model must not be empty")

    lowered = cleaned.lower()
    if "deepseek" in lowered:
        return "deepseek"
    if "qwen" in lowered:
        return "qwen"
    if (
        lowered.startswith("gpt-")
        or lowered.startswith("o1")
        or lowered.startswith("o3")
        or lowered.startswith("o4")
    ):
        return "openai"
    return None


def build_openai_compatible_testbench_repairer(
    *,
    model: str | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
    parameters: Mapping[str, Any] | None = None,
    effective_config: EffectiveModelConfig | None = None,
    budget: BudgetManager | None = None,
) -> ModelTestbenchRepairer:
    # Build one typed OpenAI-compatible testbench repairer.

    if budget is not None and not isinstance(
        budget,
        BudgetManager,
    ):
        raise TypeError(
            "budget must be a BudgetManager or None"
        )

    provider = OpenAICompatibleProvider()
    registry = ModelRegistry()
    registry.register_provider(provider)

    if effective_config is not None:
        if not isinstance(
            effective_config,
            EffectiveModelConfig,
        ):
            raise TypeError(
                "effective_config must be an "
                "EffectiveModelConfig or None"
            )
        conflicts = []
        if model is not None:
            conflicts.append("model")
        if base_url is not None:
            conflicts.append("base_url")
        if api_key_env is not None:
            conflicts.append("api_key_env")
        if parameters is not None:
            conflicts.append("parameters")
        if conflicts:
            raise ValueError(
                "effective_config is authoritative; "
                "remove parallel factory arguments: "
                + ", ".join(conflicts)
            )
        if effective_config.provider_name != provider.name:
            raise ValueError(
                "effective_config.provider_name must match "
                f"{provider.name!r}"
            )
        config = effective_config
    else:
        cleaned_model = (
            model.strip()
            if isinstance(model, str)
            else model
        )
        if not cleaned_model:
            raise ValueError("model must not be empty")
        cleaned_env = (
            "OPENAI_API_KEY"
            if api_key_env is None
            else api_key_env.strip()
        )
        if not cleaned_env:
            raise ValueError(
                "api_key_env must not be empty"
            )
        config = EffectiveModelConfig(
            logical_model_name="testbench-repair",
            provider_name=provider.name,
            model_id=cleaned_model,
            family_profile=(
                GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE
            ),
            effective_parameters=dict(
                parameters or {}
            ),
            requested_family_name=(
                "generic-openai-compatible"
            ),
            base_url=base_url,
            api_key_env=cleaned_env,
        )

    return ModelTestbenchRepairer(
        registry=registry,
        effective_config=config,
        budget=budget,
    )
