"""Typed per-call reasoning and Thinking policy for model execution."""
from __future__ import annotations

from collections.abc import Mapping
import copy
from dataclasses import dataclass
from enum import Enum
import json
from typing import Any

DEFAULT_MODEL_ID = "deepseek-v4-flash"
DEFAULT_REASONING_EFFORT = "auto"
_INTERNAL_EVIDENCE_KEY = "_agrefactor_call_evidence"


class ModelCallRole(str, Enum):
    NON_SYNTHESIZABLE_IDENTIFICATION = "non_synthesizable_identification"
    PUBLIC_TEST_GENERATION = "public_test_generation"
    HIDDEN_TEST_GENERATION = "hidden_test_generation"
    DEDUPLICATION = "deduplication"
    SIMPLE_CLASSIFICATION = "simple_classification"
    REFACTOR_PLANNING = "refactor_planning"
    REFACTOR_SOURCE_GENERATION = "refactor_source_generation"
    TESTBENCH_REPAIR = "testbench_repair"
    CANDIDATE_REPAIR = "candidate_repair"
    BOTTLENECK_DIAGNOSIS = "bottleneck_diagnosis"
    OPTIMIZATION_ACTION_SELECTION = "optimization_action_selection"
    OPTIMIZATION_CANDIDATE_GENERATION = "optimization_candidate_generation"


_MEDIUM_ROLES = frozenset(
    {
        ModelCallRole.NON_SYNTHESIZABLE_IDENTIFICATION,
        ModelCallRole.PUBLIC_TEST_GENERATION,
        ModelCallRole.HIDDEN_TEST_GENERATION,
        ModelCallRole.DEDUPLICATION,
        ModelCallRole.SIMPLE_CLASSIFICATION,
    }
)


def _copy_json(value: Mapping[str, Any]) -> dict[str, Any]:
    """Copy one provider-neutral JSON mapping."""

    if not isinstance(value, Mapping):
        raise TypeError("model parameters must be a mapping")
    copied = json.loads(
        json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        )
    )
    if not isinstance(copied, dict):
        raise TypeError("model parameters must normalize to an object")
    return copied


def _copy_preserving_objects(value: Mapping[str, Any]) -> dict[str, Any]:
    """Copy an AG2 configuration without forcing imported Python objects to JSON."""

    if not isinstance(value, Mapping):
        raise TypeError("model parameters must be a mapping")
    copied = copy.deepcopy(dict(value))
    if not isinstance(copied, dict):
        raise TypeError("model parameters must normalize to an object")
    return copied


def normalize_requested_reasoning_effort(
    value: str | None,
    *,
    allow_legacy_low: bool = True,
) -> str:
    if value is None:
        return DEFAULT_REASONING_EFFORT
    if not isinstance(value, str) or not value.strip():
        raise ValueError("reasoning_effort must be auto, medium, or high")
    cleaned = value.strip().casefold()
    allowed = {"auto", "medium", "high"}
    if allow_legacy_low:
        allowed.add("low")
    if cleaned not in allowed:
        raise ValueError("reasoning_effort must be auto, medium, or high")
    return cleaned


def normalize_call_role(value: str | ModelCallRole) -> ModelCallRole:
    if isinstance(value, ModelCallRole):
        return value
    try:
        return ModelCallRole(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"unsupported model call role: {value!r}") from exc


def default_project_effort(role: str | ModelCallRole) -> str:
    selected = normalize_call_role(role)
    return "medium" if selected in _MEDIUM_ROLES else "high"


@dataclass(frozen=True, slots=True)
class ModelCallPolicyEvidence:
    call_role: str
    model_id: str
    provider: str
    requested_reasoning_effort: str
    effective_project_reasoning_effort: str | None
    effective_provider_reasoning_effort: str | None
    thinking_requested: bool
    thinking_effective: bool
    parameter_policy_profile: str
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "call_role": self.call_role,
            "model_id": self.model_id,
            "provider": self.provider,
            "requested_reasoning_effort": self.requested_reasoning_effort,
            "effective_project_reasoning_effort": (
                self.effective_project_reasoning_effort
            ),
            "effective_provider_reasoning_effort": (
                self.effective_provider_reasoning_effort
            ),
            "thinking_requested": self.thinking_requested,
            "thinking_effective": self.thinking_effective,
            "parameter_policy_profile": self.parameter_policy_profile,
        }


_CALL_EVIDENCE_FIELDS = frozenset(
    {
        "schema_version",
        "call_role",
        "model_id",
        "provider",
        "requested_reasoning_effort",
        "effective_project_reasoning_effort",
        "effective_provider_reasoning_effort",
        "thinking_requested",
        "thinking_effective",
        "parameter_policy_profile",
    }
)


def normalize_call_policy_evidence(
    value: Mapping[str, Any] | ModelCallPolicyEvidence,
) -> dict[str, Any]:
    if isinstance(value, ModelCallPolicyEvidence):
        evidence = value.to_dict()
    elif isinstance(value, Mapping):
        evidence = _copy_json(value)
    else:
        raise TypeError("model call policy evidence must be a mapping")
    if set(evidence) != _CALL_EVIDENCE_FIELDS:
        raise ValueError(
            "model call policy evidence fields do not match schema"
        )
    if evidence["schema_version"] != 1:
        raise ValueError("unsupported model call policy evidence schema")
    return evidence


def parameterize_model_call(
    *,
    base_parameters: Mapping[str, Any],
    model_id: str,
    provider: str,
    family_profile,
    requested_reasoning_effort: str | None,
    role: str | ModelCallRole,
) -> tuple[dict[str, Any], ModelCallPolicyEvidence]:
    selected = normalize_call_role(role)
    requested = normalize_requested_reasoning_effort(
        requested_reasoning_effort
    )
    project = (
        default_project_effort(selected)
        if requested == "auto"
        else requested
    )
    # Compatibility-only low becomes project medium before transport mapping.
    if project == "low":
        project = "medium"

    params = _copy_json(base_parameters)
    family_name = str(getattr(family_profile, "name", "")).strip().casefold()
    # EffectiveModelConfig predates P4-0E and is already provider-resolved.
    # Direct registry callers may therefore carry a provider reasoning value
    # while the new requested-effort field remains at its compatibility default
    # of auto. Preserve that authoritative value rather than remapping it by
    # role. P4-0E CLI/runtime selections set requested_reasoning_effort
    # explicitly and do not take this compatibility branch.
    pre_resolved = params.get("reasoning_effort")
    if requested == "auto" and isinstance(pre_resolved, str):
        evidence = ModelCallPolicyEvidence(
            call_role=selected.value,
            model_id=model_id,
            provider=provider,
            requested_reasoning_effort=requested,
            effective_project_reasoning_effort=None,
            effective_provider_reasoning_effort=pre_resolved,
            thinking_requested=False,
            thinking_effective=False,
            parameter_policy_profile=(
                f"family-profile:{family_name or 'unknown'}:"
                "pre-resolved-preserved"
            ),
        )
        return params, evidence

    thinking_requested = False
    thinking_effective = False
    provider_effort = None
    profile = f"family-profile:{family_name or 'unknown'}"

    if (
        model_id.strip().casefold() == DEFAULT_MODEL_ID
        and family_name == "deepseek"
    ):
        provider_effort = "max" if project == "high" else "high"
        existing_extra = params.get("extra_body", {})
        if existing_extra is None:
            existing_extra = {}
        if not isinstance(existing_extra, Mapping):
            raise TypeError("extra_body must be a mapping")
        extra = _copy_json(existing_extra)
        required = {"type": "enabled"}
        existing = extra.get("thinking")
        if existing not in (None, required):
            raise ValueError(
                "DeepSeek Thinking configuration conflicts with required "
                "enabled policy"
            )
        extra["thinking"] = required
        params["extra_body"] = extra
        params["reasoning_effort"] = provider_effort
        params.pop("thinking", None)
        params.pop("enable_thinking", None)
        thinking_requested = True
        thinking_effective = True
        profile = "deepseek-v4-flash-thinking-v1"
    else:
        trial = dict(params)
        trial["reasoning_effort"] = project
        try:
            params = family_profile.reasoning_policy.apply(trial)
        except Exception as exc:
            # Auto is a project preference, not an explicit claim that an
            # unknown deployment supports a reasoning field. Preserve old
            # provider-neutral/fake-provider behavior by omitting only known
            # typed unsupported/rejected policy outcomes. Explicit medium/high
            # remains fail-closed and is never guessed.
            typed_policy_errors = {
                "ModelParameterPolicyError",
                "RejectedModelParameterError",
                "UnsupportedModelParameterError",
                "UnsupportedReasoningLevelError",
            }
            if requested != "auto" or type(exc).__name__ not in typed_policy_errors:
                raise
            params = _copy_json(base_parameters)
            profile = f"family-profile:{family_name or 'unknown'}:auto-omitted"
        raw = params.get("reasoning_effort")
        provider_effort = raw if isinstance(raw, str) else None

    evidence = ModelCallPolicyEvidence(
        call_role=selected.value,
        model_id=model_id,
        provider=provider,
        requested_reasoning_effort=requested,
        effective_project_reasoning_effort=project,
        effective_provider_reasoning_effort=provider_effort,
        thinking_requested=thinking_requested,
        thinking_effective=thinking_effective,
        parameter_policy_profile=profile,
    )
    # Transport parameters remain a public provider contract. Internal evidence
    # travels separately through ModelRequest.metadata or the legacy AG2 bridge.
    return params, evidence


def parameterize_effective_config_call(
    config,
    role: str | ModelCallRole,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Parameterize a real config while retaining old injectable test doubles."""

    parameterize = getattr(config, "parameterize_call", None)
    if callable(parameterize):
        parameters, evidence = parameterize(role)
        return (
            _copy_preserving_objects(parameters),
            normalize_call_policy_evidence(evidence),
        )
    parameters = getattr(config, "parameters", {})
    return _copy_preserving_objects(parameters), None


def add_internal_call_evidence(
    parameters: Mapping[str, Any],
    evidence: Mapping[str, Any] | ModelCallPolicyEvidence,
) -> dict[str, Any]:
    """Attach safe evidence only for the legacy AG2 in-memory config bridge."""

    params = _copy_preserving_objects(parameters)
    if _INTERNAL_EVIDENCE_KEY in params:
        raise ValueError("internal model call evidence already exists")
    params[_INTERNAL_EVIDENCE_KEY] = normalize_call_policy_evidence(evidence)
    return params


def pop_internal_call_evidence(
    parameters: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Strip AG2-only evidence while preserving imported response-format types."""

    params = _copy_preserving_objects(parameters)
    raw = params.pop(_INTERNAL_EVIDENCE_KEY, None)
    if raw is None:
        return params, None
    return params, normalize_call_policy_evidence(raw)
