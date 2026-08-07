"""Exact-model output policies that must not leak into family defaults."""

from __future__ import annotations

from .family import ModelArtifactKind, ModelOutputPolicy


DEEPSEEK_V4_FLASH_OUTPUT_POLICY = ModelOutputPolicy(
    parameter_name="max_tokens",
    default_limit=32_768,
    safety_ceiling=300_000,
    per_artifact_limits={
        ModelArtifactKind.CANDIDATE: 150_000,
        ModelArtifactKind.CANDIDATE_REPAIR: 150_000,
        ModelArtifactKind.TESTBENCH: 32_768,
        ModelArtifactKind.TESTBENCH_REPAIR: 32_768,
    },
)

CONCRETE_MODEL_OUTPUT_POLICIES = {
    "deepseek-v4-flash": DEEPSEEK_V4_FLASH_OUTPUT_POLICY,
}


def resolve_concrete_model_output_policy(
    model_id: str,
) -> ModelOutputPolicy | None:
    if not isinstance(model_id, str):
        raise TypeError("model_id must be a string")
    cleaned = model_id.strip().casefold()
    if not cleaned:
        raise ValueError("model_id must not be empty")
    return CONCRETE_MODEL_OUTPUT_POLICIES.get(cleaned)
