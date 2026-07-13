"""Registry for logical models and provider implementations."""

from __future__ import annotations

from .base import ModelProvider, ModelSpec


class ModelRegistryError(LookupError):
    """Base error raised for model-registry lookup failures."""


class UnknownModelError(ModelRegistryError):
    """Raised when a logical model name is not registered."""


class UnknownProviderError(ModelRegistryError):
    """Raised when a provider name is not registered."""


class ModelRegistry:
    """Store model specifications separately from provider implementations."""

    def __init__(self) -> None:
        self._models: dict[str, ModelSpec] = {}
        self._providers: dict[str, ModelProvider] = {}

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

    def resolve(self, model_name: str) -> tuple[ModelSpec, ModelProvider]:
        """Resolve a logical model and its registered provider."""

        spec = self.get_model(model_name)
        provider = self.get_provider(spec.provider)
        return spec, provider

    def model_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._models))

    def provider_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    @staticmethod
    def _clean_name(label: str, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{label} must be a string")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{label} must not be empty")
        return cleaned
