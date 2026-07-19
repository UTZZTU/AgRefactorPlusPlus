"""Registry for logical models, providers, and family profiles."""

from __future__ import annotations

from .base import ModelProvider, ModelSpec
from .family import (
    ModelFamilyProfile,
    NEUTRAL_MODEL_FAMILY_PROFILE,
)


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

    def __init__(self) -> None:
        self._models: dict[str, ModelSpec] = {}
        self._providers: dict[str, ModelProvider] = {}
        self._family_profiles: dict[str, ModelFamilyProfile] = {
            NEUTRAL_MODEL_FAMILY_PROFILE.name: (
                NEUTRAL_MODEL_FAMILY_PROFILE
            )
        }

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
        if (
            profile.name in self._family_profiles
            and not replace
        ):
            raise ValueError(
                "Model family profile already registered: "
                + profile.name
            )
        self._family_profiles[profile.name] = profile

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
        try:
            return self._family_profiles[cleaned]
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
        registered = self._family_profiles.get(spec.family)
        if registered is not None:
            return registered
        return ModelFamilyProfile(name=spec.family)

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

    def model_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._models))

    def provider_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    def family_profile_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._family_profiles))

    @staticmethod
    def _clean_name(label: str, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{label} must be a string")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{label} must not be empty")
        return cleaned
