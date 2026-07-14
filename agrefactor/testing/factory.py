"""Factories for provider-backed testbench repair components."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agrefactor.models import (
    ModelRegistry,
    ModelSpec,
    OpenAICompatibleProvider,
)

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
    model: str,
    base_url: str | None = None,
    api_key_env: str = "OPENAI_API_KEY",
    parameters: Mapping[str, Any] | None = None,
) -> ModelTestbenchRepairer:
    """Build a testbench-only repairer backed by one compatible endpoint."""

    if not isinstance(api_key_env, str) or not api_key_env.strip():
        raise ValueError("api_key_env must not be empty")

    cleaned_model = model.strip() if isinstance(model, str) else model
    family = infer_model_family(cleaned_model)

    provider = OpenAICompatibleProvider()
    registry = ModelRegistry()
    registry.register_provider(provider)
    registry.register_model(
        ModelSpec(
            name="testbench-repair",
            provider=provider.name,
            model=cleaned_model,
            family=family,
            base_url=base_url,
            api_key_env=api_key_env.strip(),
        )
    )

    family_instructions = {
        "deepseek": (
            "Reason internally. Return only the final complete C++ "
            "testbench block and never expose reasoning."
        ),
        "openai": (
            "Return only the final complete C++ testbench block."
        ),
        "qwen": (
            "Keep analysis private and emit only the final complete "
            "C++ testbench block."
        ),
    }

    return ModelTestbenchRepairer(
        registry=registry,
        model_name="testbench-repair",
        parameters=parameters,
        family_instructions=family_instructions,
    )
