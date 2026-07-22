"""Static known-family profiles reviewed against official API docs."""

from __future__ import annotations

from .family import (
    ModelCapabilityTag,
    ModelFamilyProfile,
    ModelProfileVerificationStatus,
    ReasoningPolicy,
)


_REASONING_CODE_CAPABILITIES = frozenset(
    {
        ModelCapabilityTag.REASONING_MODEL,
        ModelCapabilityTag.CODE_SPECIALIZED,
        ModelCapabilityTag.STRICT_INSTRUCTION,
        ModelCapabilityTag.THINKING_TAG_POSSIBLE,
        ModelCapabilityTag.STRICT_COMPLETION,
    }
)


DEEPSEEK_MODEL_FAMILY_PROFILE = ModelFamilyProfile(
    name="deepseek",
    capabilities=_REASONING_CODE_CAPABILITIES,
    verification_status=(
        ModelProfileVerificationStatus.OFFICIAL_DOCS_REVIEWED
    ),
    verification_note=(
        "DeepSeek V4 reasoning-effort documentation reviewed "
        "2026-07-22; this family policy does not claim a network smoke."
    ),
    reasoning_policy=ReasoningPolicy.mapped(
        low="high",
        medium="high",
        high="high",
    ),
    parameter_aliases={
        "max_completion_tokens": "max_tokens",
    },
)


KIMI_MODEL_FAMILY_PROFILE = ModelFamilyProfile(
    name="kimi",
    capabilities=_REASONING_CODE_CAPABILITIES,
    verification_status=(
        ModelProfileVerificationStatus.OFFICIAL_DOCS_REVIEWED
    ),
    verification_note=(
        "Kimi Chat Completions documents a thinking object rather "
        "than a stable family-wide low/medium/high effort field; "
        "the generic effort field is omitted."
    ),
    reasoning_policy=ReasoningPolicy.omit_all(),
)


GLM_MODEL_FAMILY_PROFILE = ModelFamilyProfile(
    name="glm",
    capabilities=_REASONING_CODE_CAPABILITIES,
    verification_status=(
        ModelProfileVerificationStatus.OFFICIAL_DOCS_REVIEWED
    ),
    verification_note=(
        "GLM reasoning-effort support varies by concrete model "
        "version; the family-level policy preserves provider defaults "
        "until a concrete ModelSpec policy is selected."
    ),
    reasoning_policy=ReasoningPolicy.omit_all(),
)


MINIMAX_MODEL_FAMILY_PROFILE = ModelFamilyProfile(
    name="minimax",
    capabilities=_REASONING_CODE_CAPABILITIES,
    verification_status=(
        ModelProfileVerificationStatus.OFFICIAL_DOCS_REVIEWED
    ),
    verification_note=(
        "MiniMax Chat Completions documents model-specific thinking "
        "controls rather than a stable family-wide three-level effort "
        "field; the generic effort field is omitted."
    ),
    reasoning_policy=ReasoningPolicy.omit_all(),
)


QWEN_MODEL_FAMILY_PROFILE = ModelFamilyProfile(
    name="qwen",
    capabilities=_REASONING_CODE_CAPABILITIES,
    verification_status=(
        ModelProfileVerificationStatus.OFFICIAL_DOCS_REVIEWED
    ),
    verification_note=(
        "Qwen reasoning controls differ between Chat Completions, "
        "Responses, deployment region, and concrete model; the "
        "family-level Chat Completions policy omits reasoning_effort."
    ),
    reasoning_policy=ReasoningPolicy.omit_all(),
)


GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE = ModelFamilyProfile(
    name="generic-openai-compatible",
    verification_status=ModelProfileVerificationStatus.DECLARED,
    verification_note=(
        "Transport compatibility alone does not prove support for "
        "vendor-specific reasoning parameters."
    ),
    reasoning_policy=ReasoningPolicy.reject_all(),
)


KNOWN_MODEL_FAMILY_PROFILES = (
    DEEPSEEK_MODEL_FAMILY_PROFILE,
    KIMI_MODEL_FAMILY_PROFILE,
    GLM_MODEL_FAMILY_PROFILE,
    MINIMAX_MODEL_FAMILY_PROFILE,
    QWEN_MODEL_FAMILY_PROFILE,
    GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE,
)

KNOWN_MODEL_FAMILY_PROFILE_NAMES = tuple(
    profile.name for profile in KNOWN_MODEL_FAMILY_PROFILES
)

# Existing factory code historically emits "openai" for GPT/O-series
# identifiers. Keep that spelling as a compatibility alias while the
# canonical profile remains vendor-neutral.
KNOWN_MODEL_FAMILY_ALIASES = {
    "openai": "generic-openai-compatible",
}
