"""Model registry, provider abstractions, and repair adapters."""

from .base import (
    ChatMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelSpec,
    TokenUsage,
)
from .family import (
    ModelCapabilityTag,
    ModelFamilyProfile,
    NEUTRAL_MODEL_FAMILY_PROFILE,
)
from .openai_compatible import (
    MissingModelCredentialError,
    OpenAICompatibleProvider,
    OpenAICompatibleProviderError,
    OpenAICompatibleResponseError,
)
from .registry import (
    ModelRegistry,
    ModelRegistryError,
    UnknownModelError,
    UnknownModelFamilyProfileError,
    UnknownProviderError,
)

_CANDIDATE_ADAPTER_EXPORTS = frozenset(
    {
        "CandidateModelAdapter",
        "CandidateModelRequest",
        "CandidateModelResult",
        "CandidateResponseContract",
        "CandidateResponseError",
    }
)


def __getattr__(name: str):
    if name in _CANDIDATE_ADAPTER_EXPORTS:
        from . import candidate_adapter

        value = getattr(candidate_adapter, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CandidateModelAdapter",
    "CandidateModelRequest",
    "CandidateModelResult",
    "CandidateResponseContract",
    "CandidateResponseError",
    "ChatMessage",
    "MissingModelCredentialError",
    "ModelCapabilityTag",
    "ModelFamilyProfile",
    "ModelProvider",
    "ModelRegistry",
    "ModelRegistryError",
    "ModelRequest",
    "ModelResponse",
    "ModelSpec",
    "NEUTRAL_MODEL_FAMILY_PROFILE",
    "OpenAICompatibleProvider",
    "OpenAICompatibleProviderError",
    "OpenAICompatibleResponseError",
    "TokenUsage",
    "UnknownModelError",
    "UnknownModelFamilyProfileError",
    "UnknownProviderError",
]
