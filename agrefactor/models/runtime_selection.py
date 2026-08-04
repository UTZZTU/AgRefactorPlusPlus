
"""Resolve one fixed user-selected model into a typed runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .call_policy import (
    DEFAULT_MODEL_ID,
    DEFAULT_REASONING_EFFORT,
    normalize_requested_reasoning_effort,
)
from .effective_config import EffectiveModelConfig
from .family import ModelArtifactKind
from .official_pricing import find_official_model_pricing_snapshots
from .openai_compatible import OpenAICompatibleProvider
from .registry import ModelRegistry
from .base import ModelSpec


@dataclass(frozen=True, slots=True)
class ConcreteModelRuntimeDefaults:
    """Static defaults for one exact model identifier."""

    model_id: str
    family: str
    base_url: str | None
    api_key_env: str
    pricing_provider: str | None = None

    def __post_init__(self) -> None:
        for name in ("model_id", "family", "api_key_env"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")
            object.__setattr__(self, name, value.strip())
        if self.base_url is not None:
            if not isinstance(self.base_url, str):
                raise TypeError("base_url must be a string or None")
            object.__setattr__(
                self,
                "base_url",
                self.base_url.strip() or None,
            )
        if self.pricing_provider is not None:
            if not isinstance(self.pricing_provider, str):
                raise TypeError(
                    "pricing_provider must be a string or None"
                )
            object.__setattr__(
                self,
                "pricing_provider",
                self.pricing_provider.strip().casefold() or None,
            )


@dataclass(frozen=True, slots=True)
class ModelRuntimeSelection:
    """Registry plus the exact immutable effective configuration."""

    registry: ModelRegistry
    effective_config: EffectiveModelConfig
    defaults_source: str

    def __post_init__(self) -> None:
        if not isinstance(self.registry, ModelRegistry):
            raise TypeError("registry must be a ModelRegistry")
        if not isinstance(self.effective_config, EffectiveModelConfig):
            raise TypeError(
                "effective_config must be an EffectiveModelConfig"
            )
        if (
            not isinstance(self.defaults_source, str)
            or not self.defaults_source.strip()
        ):
            raise ValueError("defaults_source must not be empty")


CONCRETE_MODEL_RUNTIME_DEFAULTS = {
    DEFAULT_MODEL_ID: ConcreteModelRuntimeDefaults(
        model_id=DEFAULT_MODEL_ID,
        family="deepseek",
        base_url="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        pricing_provider="deepseek",
    ),
}

_FAMILY_PREFIXES = (
    ("deepseek", "deepseek"),
    ("kimi", "kimi"),
    ("moonshot", "kimi"),
    ("glm", "glm"),
    ("minimax", "minimax"),
    ("qwen", "qwen"),
)


def infer_model_family(model_id: str) -> str:
    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError("model_id must not be empty")
    lowered = model_id.strip().casefold()
    exact = CONCRETE_MODEL_RUNTIME_DEFAULTS.get(lowered)
    if exact is not None:
        return exact.family
    for prefix, family in _FAMILY_PREFIXES:
        if lowered.startswith(prefix):
            return family
    return "generic-openai-compatible"


def _pricing_snapshot(
    defaults: ConcreteModelRuntimeDefaults | None,
    model_id: str,
):
    if defaults is None or defaults.pricing_provider is None:
        return None
    snapshots = find_official_model_pricing_snapshots(
        provider=defaults.pricing_provider,
        model_id=model_id,
    )
    if not snapshots:
        return None
    return sorted(
        snapshots,
        key=lambda item: item.pricing_snapshot_sha256,
    )[0]


def resolve_model_runtime(
    model_id: str | None = None,
    *,
    family: str | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
    reasoning_effort: str | None = DEFAULT_REASONING_EFFORT,
    parameters: dict[str, Any] | None = None,
) -> ModelRuntimeSelection:
    """Resolve the default or exact user-selected model without routing."""

    if model_id is not None and not isinstance(model_id, str):
        raise TypeError("model_id must be a string or None")
    if isinstance(model_id, str) and not model_id.strip():
        raise ValueError("model_id must not be empty")
    explicit = isinstance(model_id, str)
    cleaned_model = (
        model_id.strip() if explicit else DEFAULT_MODEL_ID
    )
    defaults = CONCRETE_MODEL_RUNTIME_DEFAULTS.get(
        cleaned_model.casefold()
    )

    resolved_family = (
        family.strip()
        if isinstance(family, str) and family.strip()
        else (
            defaults.family
            if defaults is not None
            else infer_model_family(cleaned_model)
        )
    )
    resolved_base_url = (
        base_url.strip()
        if isinstance(base_url, str) and base_url.strip()
        else (defaults.base_url if defaults is not None else None)
    )
    resolved_key_env = (
        api_key_env.strip()
        if isinstance(api_key_env, str) and api_key_env.strip()
        else (
            defaults.api_key_env
            if defaults is not None
            else "OPENAI_API_KEY"
        )
    )

    registry = ModelRegistry()
    profile = registry.get_family_profile(resolved_family)
    provider = OpenAICompatibleProvider(
        default_base_url=resolved_base_url,
        default_api_key_env=resolved_key_env,
        timeout_s=profile.request_timeout_s,
    )
    registry.register_provider(provider)
    registry.register_model(
        ModelSpec(
            name=cleaned_model,
            provider=provider.name,
            model=cleaned_model,
            family=resolved_family,
            base_url=resolved_base_url,
            api_key_env=resolved_key_env,
        )
    )

    requested_reasoning = normalize_requested_reasoning_effort(
        reasoning_effort
    )
    call_parameters = dict(parameters or {})
    # Preserve accepted explicit low/medium/high family behavior. Auto is a
    # per-call role decision and is not sent to the family merge layer.
    if requested_reasoning != "auto":
        call_parameters["reasoning_effort"] = requested_reasoning

    effective = registry.resolve_effective_config(
        cleaned_model,
        parameters=call_parameters,
        artifact_kind=ModelArtifactKind.CANDIDATE,
        pricing_snapshot=_pricing_snapshot(defaults, cleaned_model),
        allow_approximate_cost=True,
    )
    effective = replace(
        effective,
        requested_reasoning_effort=requested_reasoning,
    )
    source = (
        "p4_0e_default_deepseek_v4_flash"
        if not explicit
        else (
            "exact_static_model_defaults"
            if defaults is not None
            else "family_inference_and_transport_defaults"
        )
    )
    return ModelRuntimeSelection(
        registry=registry,
        effective_config=effective,
        defaults_source=source,
    )
