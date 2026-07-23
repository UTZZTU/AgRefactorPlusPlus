"""Static known-family profiles reviewed and deterministically tested."""

from __future__ import annotations

from .family import (
    ModelArtifactKind,
    ModelCapabilityTag,
    ModelFamilyProfile,
    ModelOutputPolicy,
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

# This is a declarative known-supported vocabulary, not an exhaustive runtime
# allowlist. Compatible providers may carry extension objects/fields.
# Hard rejection remains explicit in rejected_parameters; family reasoning
# policy still maps/omits/rejects the unstable reasoning field.
_COMMON_PARAMETERS = frozenset(
    {
        "temperature",
        "top_p",
        "max_tokens",
        "stop",
        "seed",
        "frequency_penalty",
        "presence_penalty",
        "n",
        "stream",
        "response_format",
        "tools",
        "tool_choice",
        "reasoning_effort",
        "thinking",
        "enable_thinking",
        "extra_body",
    }
)

# Artifact identities are explicit even when a family deliberately has no
# extra scalar defaults. The typed output policy supplies per-artifact limits.
_ARTIFACT_DEFAULTS = {
    ModelArtifactKind.CANDIDATE: {},
    ModelArtifactKind.TESTBENCH: {},
    ModelArtifactKind.CANDIDATE_REPAIR: {},
    ModelArtifactKind.TESTBENCH_REPAIR: {},
}
_OUTPUT_POLICY = ModelOutputPolicy(
    parameter_name="max_tokens",
    safety_ceiling=65536,
    per_artifact_limits={
        ModelArtifactKind.CANDIDATE: 32768,
        ModelArtifactKind.TESTBENCH: 32768,
        ModelArtifactKind.CANDIDATE_REPAIR: 32768,
        ModelArtifactKind.TESTBENCH_REPAIR: 32768,
    },
)


def _profile(
    *,
    name: str,
    status: ModelProfileVerificationStatus,
    note: str,
    reasoning_policy: ReasoningPolicy,
    aliases=None,
    prompt_profile: str,
) -> ModelFamilyProfile:
    return ModelFamilyProfile(
        name=name,
        capabilities=_REASONING_CODE_CAPABILITIES,
        verification_status=status,
        verification_note=note,
        reasoning_policy=reasoning_policy,
        parameter_aliases={} if aliases is None else aliases,
        supported_parameters=_COMMON_PARAMETERS,
        artifact_default_parameters=_ARTIFACT_DEFAULTS,
        output_policy=_OUTPUT_POLICY,
        request_timeout_s=120.0,
        prompt_profile=prompt_profile,
    )


DEEPSEEK_MODEL_FAMILY_PROFILE = _profile(
    name="deepseek",
    status=ModelProfileVerificationStatus.DETERMINISTICALLY_TESTED,
    note=(
        "The family contract passed deterministic tests. The separate "
        "deepseek-v4-flash concrete-model record completed the bounded "
        "P1-D real-network smoke; that claim is not widened to the family."
    ),
    reasoning_policy=ReasoningPolicy.mapped(
        low="high",
        medium="high",
        high="high",
    ),
    aliases={"max_completion_tokens": "max_tokens"},
    prompt_profile="deepseek-chat-completions",
)
KIMI_MODEL_FAMILY_PROFILE = _profile(
    name="kimi",
    status=ModelProfileVerificationStatus.DETERMINISTICALLY_TESTED,
    note=(
        "Official behavior was reviewed and the family contract passed "
        "deterministic tests; no network-smoke claim is made."
    ),
    reasoning_policy=ReasoningPolicy.omit_all(),
    prompt_profile="kimi-chat-completions",
)
GLM_MODEL_FAMILY_PROFILE = _profile(
    name="glm",
    status=ModelProfileVerificationStatus.DETERMINISTICALLY_TESTED,
    note=(
        "Concrete-model reasoning support varies; deterministic family "
        "tests preserve provider defaults."
    ),
    reasoning_policy=ReasoningPolicy.omit_all(),
    prompt_profile="glm-chat-completions",
)
MINIMAX_MODEL_FAMILY_PROFILE = _profile(
    name="minimax",
    status=ModelProfileVerificationStatus.DETERMINISTICALLY_TESTED,
    note=(
        "Model-specific thinking controls are not generalized; the family "
        "contract passed deterministic compatibility tests."
    ),
    reasoning_policy=ReasoningPolicy.omit_all(),
    prompt_profile="minimax-chat-completions",
)
QWEN_MODEL_FAMILY_PROFILE = _profile(
    name="qwen",
    status=ModelProfileVerificationStatus.DETERMINISTICALLY_TESTED,
    note=(
        "Reasoning controls differ by API and deployment; deterministic "
        "family tests omit the unstable family-wide effort field."
    ),
    reasoning_policy=ReasoningPolicy.omit_all(),
    prompt_profile="qwen-chat-completions",
)
GENERIC_OPENAI_COMPATIBLE_MODEL_FAMILY_PROFILE = ModelFamilyProfile(
    name="generic-openai-compatible",
    verification_status=(
        ModelProfileVerificationStatus.DETERMINISTICALLY_TESTED
    ),
    verification_note=(
        "Transport compatibility does not prove vendor reasoning support; "
        "deterministic tests enforce rejection."
    ),
    reasoning_policy=ReasoningPolicy.reject_all(),
    supported_parameters=_COMMON_PARAMETERS,
    artifact_default_parameters=_ARTIFACT_DEFAULTS,
    output_policy=_OUTPUT_POLICY,
    request_timeout_s=120.0,
    prompt_profile="generic-openai-compatible",
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
KNOWN_MODEL_FAMILY_ALIASES = {
    "openai": "generic-openai-compatible",
}
