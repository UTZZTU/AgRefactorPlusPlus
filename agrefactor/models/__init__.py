"""Model registry, provider abstractions, and repair adapters."""

from .base import (
    ChatMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelSpec,
    TokenUsage,
)
from .cost_estimator import estimate_model_cost
from .effective_config import EffectiveModelConfig
from .family import (
    ModelArtifactKind,
    ModelCapabilityTag,
    ModelFamilyProfile,
    ModelOutputPolicy,
    ModelParameterAliasConflictError,
    ModelParameterPolicyError,
    ModelProfileVerificationStatus,
    NEUTRAL_MODEL_FAMILY_PROFILE,
    ReasoningLevel,
    ReasoningLevelRule,
    ReasoningPolicy,
    ReasoningPolicyAction,
    RejectedModelParameterError,
    UnsupportedModelParameterError,
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
from .official_pricing import (
    OFFICIAL_MODEL_PRICING_SNAPSHOTS,
    OFFICIAL_PRICING_MANIFEST_FILE_SHA256,
    OFFICIAL_PRICING_SOURCE_RECORDS,
    OfficialPricingSourceRecord,
    find_official_model_pricing_snapshots,
    find_official_pricing_sources,
    official_pricing_manifest,
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
from .call_policy import (
    DEFAULT_MODEL_ID,
    DEFAULT_REASONING_EFFORT,
    ModelCallPolicyEvidence,
    ModelCallRole,
    pop_internal_call_evidence,
)
from .environment import (
    InvocationEnvironmentEvidence,
    credential_presence_evidence,
    load_invocation_dotenv,
)
from .runtime_selection import (
    CONCRETE_MODEL_RUNTIME_DEFAULTS,
    ConcreteModelRuntimeDefaults,
    ModelRuntimeSelection,
    infer_model_family,
    resolve_model_runtime,
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
    "CONCRETE_MODEL_RUNTIME_DEFAULTS",
    "DEFAULT_MODEL_ID",
    "DEFAULT_REASONING_EFFORT",
    "ModelCallPolicyEvidence",
    "ModelCallRole",
    "pop_internal_call_evidence",
    "InvocationEnvironmentEvidence",
    "credential_presence_evidence",
    "load_invocation_dotenv",
    "ConcreteModelRuntimeDefaults",
    "DEEPSEEK_MODEL_FAMILY_PROFILE",
    "EffectiveModelConfig",
    "GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE",
    "GLM_MODEL_FAMILY_PROFILE",
    "KIMI_MODEL_FAMILY_PROFILE",
    "KNOWN_MODEL_FAMILY_ALIASES",
    "KNOWN_MODEL_FAMILY_PROFILE_NAMES",
    "KNOWN_MODEL_FAMILY_PROFILES",
    "MINIMAX_MODEL_FAMILY_PROFILE",
    "MissingModelCredentialError",
    "ModelArtifactKind",
    "ModelCapabilityTag",
    "ModelFamilyProfile",
    "ModelOutputPolicy",
    "ModelParameterAliasConflictError",
    "ModelParameterPolicyError",
    "ModelProfileVerificationStatus",
    "ModelPricingSnapshot",
    "ModelProvider",
    "ModelRegistry",
    "ModelRegistryError",
    "ModelRuntimeSelection",
    "ModelRequest",
    "ModelResponse",
    "ModelSpec",
    "NEUTRAL_MODEL_FAMILY_PROFILE",
    "OFFICIAL_MODEL_PRICING_SNAPSHOTS",
    "OFFICIAL_PRICING_MANIFEST_FILE_SHA256",
    "OFFICIAL_PRICING_SOURCE_RECORDS",
    "OfficialPricingSourceRecord",
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
    "UnsupportedModelParameterError",
    "UnsupportedReasoningLevelError",
    "estimate_model_cost",
    "find_official_model_pricing_snapshots",
    "infer_model_family",
    "find_official_pricing_sources",
    "official_pricing_manifest",
    "resolve_model_runtime",
]
