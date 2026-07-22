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
    ModelParameterAliasConflictError,
    ModelParameterPolicyError,
    ModelProfileVerificationStatus,
    NEUTRAL_MODEL_FAMILY_PROFILE,
    ReasoningLevel,
    ReasoningLevelRule,
    ReasoningPolicy,
    ReasoningPolicyAction,
    RejectedModelParameterError,
    UnsupportedReasoningLevelError,
)
from .known_profiles import (
    DEEPSEEK_MODEL_FAMILY_PROFILE,
    GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE,
    GLM_MODEL_FAMILY_PROFILE,
    KIMI_MODEL_FAMILY_PROFILE,
    KNOWN_MODEL_FAMILY_ALIASES,
    KNOWN_MODEL_FAMILY_PROFILE_NAMES,
    KNOWN_MODEL_FAMILY_PROFILES,
    MINIMAX_MODEL_FAMILY_PROFILE,
    QWEN_MODEL_FAMILY_PROFILE,
)
from .pricing import (
    CostEstimate,
    CostEstimationQuality,
    ModelPricingSnapshot,
    PricingApplicability,
    PricingRate,
    PricingVerificationStatus,
    TokenUsageBreakdown,
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
    "CostEstimate",
    "CostEstimationQuality",
    "DEEPSEEK_MODEL_FAMILY_PROFILE",
    "GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE",
    "GLM_MODEL_FAMILY_PROFILE",
    "KIMI_MODEL_FAMILY_PROFILE",
    "KNOWN_MODEL_FAMILY_ALIASES",
    "KNOWN_MODEL_FAMILY_PROFILE_NAMES",
    "KNOWN_MODEL_FAMILY_PROFILES",
    "MINIMAX_MODEL_FAMILY_PROFILE",
    "MissingModelCredentialError",
    "ModelCapabilityTag",
    "ModelFamilyProfile",
    "ModelParameterAliasConflictError",
    "ModelParameterPolicyError",
    "ModelProfileVerificationStatus",
    "ModelPricingSnapshot",
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
    "PricingApplicability",
    "PricingRate",
    "PricingVerificationStatus",
    "QWEN_MODEL_FAMILY_PROFILE",
    "ReasoningLevel",
    "ReasoningLevelRule",
    "ReasoningPolicy",
    "ReasoningPolicyAction",
    "RejectedModelParameterError",
    "TokenUsage",
    "TokenUsageBreakdown",
    "UnknownModelError",
    "UnknownModelFamilyProfileError",
    "UnknownProviderError",
    "UnsupportedReasoningLevelError",
]
