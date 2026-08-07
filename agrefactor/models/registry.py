"""Registry for logical models, providers, and family profiles."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import ModelProvider, ModelSpec
from .concrete_output_policy import (
    resolve_concrete_model_output_policy,
)
from .effective_config import EffectiveModelConfig
from .family import (
    ModelArtifactKind,
    ModelFamilyProfile,
    NEUTRAL_MODEL_FAMILY_PROFILE,
)
from .known_profiles import (
    KNOWN_MODEL_FAMILY_ALIASES,
    KNOWN_MODEL_FAMILY_PROFILES,
)
from .pricing import ModelPricingSnapshot


class ModelRegistryError(LookupError):
    """Base error raised for model-registry lookup failures."""


class UnknownModelError(ModelRegistryError):
    """Raised when a logical model name is not registered."""


class UnknownProviderError(ModelRegistryError):
    """Raised when a provider name is not registered."""


class UnknownModelFamilyProfileError(ModelRegistryError):
    """Raised when an explicitly requested profile is not registered."""


class ModelRegistry:
    """Store fixed model specs separately from providers and profiles."""

    def __init__(
        self,
        *,
        include_known_family_profiles: bool = True,
    ) -> None:
        self._models: dict[str, ModelSpec] = {}
        self._providers: dict[str, ModelProvider] = {}
        self._family_profiles: dict[str, ModelFamilyProfile] = {
            NEUTRAL_MODEL_FAMILY_PROFILE.name: (
                NEUTRAL_MODEL_FAMILY_PROFILE
            )
        }
        self._family_aliases: dict[str, str] = {}
        if include_known_family_profiles:
            for profile in KNOWN_MODEL_FAMILY_PROFILES:
                self.register_family_profile(profile)
            for alias, target in KNOWN_MODEL_FAMILY_ALIASES.items():
                self.register_family_alias(alias, target)

    def register_model(
        self,
        spec: ModelSpec,
        *,
        replace: bool = False,
    ) -> None:
        if not isinstance(spec, ModelSpec):
            raise TypeError("spec must be a ModelSpec")
        if spec.name in self._models and not replace:
            raise ValueError(f"Model already registered: {spec.name}")
        self._models[spec.name] = spec

    def register_provider(
        self,
        provider: ModelProvider,
        *,
        replace: bool = False,
    ) -> None:
        if not isinstance(provider, ModelProvider):
            raise TypeError("provider must implement ModelProvider")

        name = provider.name.strip()
        if not name:
            raise ValueError("Provider name must not be empty")
        if name in self._providers and not replace:
            raise ValueError(f"Provider already registered: {name}")
        self._providers[name] = provider

    def register_family_profile(
        self,
        profile: ModelFamilyProfile,
        *,
        replace: bool = False,
    ) -> None:
        if not isinstance(profile, ModelFamilyProfile):
            raise TypeError(
                "profile must be a ModelFamilyProfile"
            )
        if profile.name in self._family_aliases:
            raise ValueError(
                "Model family profile name conflicts with alias: "
                + profile.name
            )
        if (
            profile.name in self._family_profiles
            and not replace
        ):
            raise ValueError(
                "Model family profile already registered: "
                + profile.name
            )
        self._family_profiles[profile.name] = profile

    def register_family_alias(
        self,
        alias: str,
        target_profile: str,
        *,
        replace: bool = False,
    ) -> None:
        alias_name = self._clean_name(
            "model family alias",
            alias,
        )
        target_name = self._clean_name(
            "model family alias target",
            target_profile,
        )
        if alias_name == target_name:
            raise ValueError(
                "model family alias must differ from its target"
            )
        if alias_name in self._family_profiles:
            raise ValueError(
                "Model family alias conflicts with profile: "
                + alias_name
            )
        if target_name not in self._family_profiles:
            raise UnknownModelFamilyProfileError(
                "Unknown model family alias target: "
                + target_name
            )
        if alias_name in self._family_aliases and not replace:
            raise ValueError(
                "Model family alias already registered: "
                + alias_name
            )
        self._family_aliases[alias_name] = target_name

    def get_model(self, name: str) -> ModelSpec:
        cleaned = self._clean_name("model name", name)
        try:
            return self._models[cleaned]
        except KeyError as exc:
            raise UnknownModelError(
                f"Unknown model: {cleaned}"
            ) from exc

    def get_provider(self, name: str) -> ModelProvider:
        cleaned = self._clean_name("provider name", name)
        try:
            return self._providers[cleaned]
        except KeyError as exc:
            raise UnknownProviderError(
                f"Unknown provider: {cleaned}"
            ) from exc

    def get_family_profile(
        self,
        name: str,
    ) -> ModelFamilyProfile:
        cleaned = self._clean_name(
            "model family profile name",
            name,
        )
        canonical = self._family_aliases.get(cleaned, cleaned)
        try:
            return self._family_profiles[canonical]
        except KeyError as exc:
            raise UnknownModelFamilyProfileError(
                f"Unknown model family profile: {cleaned}"
            ) from exc

    def resolve(
        self,
        model_name: str,
    ) -> tuple[ModelSpec, ModelProvider]:
        """Resolve exactly the fixed logical model selected by the caller."""

        spec = self.get_model(model_name)
        provider = self.get_provider(spec.provider)
        return spec, provider

    def resolve_family_profile(
        self,
        model_name: str,
    ) -> ModelFamilyProfile:
        """Resolve a typed profile without changing the selected model."""

        spec = self.get_model(model_name)
        if spec.family is None:
            return NEUTRAL_MODEL_FAMILY_PROFILE
        return self.get_family_profile(spec.family)

    def resolve_with_profile(
        self,
        model_name: str,
    ) -> tuple[
        ModelSpec,
        ModelProvider,
        ModelFamilyProfile,
    ]:
        spec, provider = self.resolve(model_name)
        return (
            spec,
            provider,
            self.resolve_family_profile(model_name),
        )

    def resolve_effective_config(
        self,
        model_name: str,
        *,
        parameters: Mapping[str, Any] | None = None,
        artifact_kind: str | ModelArtifactKind | None = None,
        pricing_snapshot: ModelPricingSnapshot | None = None,
        allow_approximate_cost: bool = False,
    ) -> EffectiveModelConfig:
        'Resolve one immutable model configuration before Provider use.'

        spec, provider, family_profile = (
            self.resolve_with_profile(model_name)
        )
        effective_parameters = (
            family_profile.merge_parameters(
                spec.default_parameters,
                parameters,
                artifact_kind=artifact_kind,
                output_policy_override=(
                    resolve_concrete_model_output_policy(spec.model)
                ),
            )
        )
        return EffectiveModelConfig(
            logical_model_name=spec.name,
            provider_name=provider.name,
            model_id=spec.model,
            requested_family_name=spec.family,
            family_profile=family_profile,
            base_url=spec.base_url,
            api_key_env=spec.api_key_env,
            effective_parameters=effective_parameters,
            pricing_snapshot=pricing_snapshot,
            allow_approximate_cost=allow_approximate_cost,
        )

    def model_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._models))

    def provider_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    def family_profile_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._family_profiles))

    def family_aliases(self) -> dict[str, str]:
        return dict(sorted(self._family_aliases.items()))

    @staticmethod
    def _clean_name(label: str, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{label} must be a string")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{label} must not be empty")
        return cleaned
