"""Typed, vendor-neutral model-family compatibility policies."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
import json
import re
from typing import Any


_SECRET_KEY_RE = re.compile(
    r"(?:api[_-]?key|authorization|bearer|credential|"
    r"password|refresh[_-]?token|secret|access[_-]?token)",
    re.IGNORECASE,
)


def _clean_required(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    return cleaned


def _clean_optional(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string or None")
    cleaned = value.strip()
    return cleaned or None


def _copy_json_mapping(
    name: str,
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        )
        copied = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{name} must contain finite JSON-serializable data"
        ) from exc
    if not isinstance(copied, dict):
        raise TypeError(f"{name} must normalize to an object")
    return copied


def _reject_secret_keys(value: Any, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}"
            if _SECRET_KEY_RE.search(key_text):
                raise ValueError(
                    "model parameters must not contain "
                    f"credential-like key: {child_path}"
                )
            _reject_secret_keys(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_keys(child, f"{path}[{index}]")


def _clean_name_set(name: str, values) -> frozenset[str]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{name} must be an iterable of strings")
    try:
        items = tuple(values)
    except TypeError as exc:
        raise TypeError(
            f"{name} must be an iterable of strings"
        ) from exc
    return frozenset(
        _clean_required(f"{name} item", str(item))
        for item in items
    )


class ModelCapabilityTag(str, Enum):
    REASONING_MODEL = "reasoning_model"
    CODE_SPECIALIZED = "code_specialized"
    STRICT_INSTRUCTION = "strict_instruction"
    THINKING_TAG_POSSIBLE = "thinking_tag_possible"
    STRICT_COMPLETION = "strict_completion"


class ModelProfileVerificationStatus(str, Enum):
    DECLARED = "declared"
    DETERMINISTICALLY_TESTED = "deterministically_tested"
    NETWORK_SMOKE_VERIFIED = "network_smoke_verified"
    # Historical compatibility only; frozen profiles no longer use this as
    # a substitute for deterministic or network verification.
    OFFICIAL_DOCS_REVIEWED = "official_docs_reviewed"


class ReasoningLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ReasoningPolicyAction(str, Enum):
    MAP = "map"
    OMIT = "omit"
    REJECT = "reject"


class ModelParameterPolicyError(ValueError):
    pass


class UnsupportedReasoningLevelError(ModelParameterPolicyError):
    pass


class RejectedModelParameterError(ModelParameterPolicyError):
    pass


class ModelParameterAliasConflictError(ModelParameterPolicyError):
    pass


@dataclass(frozen=True, slots=True)
class ReasoningLevelRule:
    action: ReasoningPolicyAction
    provider_value: str | None = None

    def __post_init__(self) -> None:
        action = self.action
        if not isinstance(action, ReasoningPolicyAction):
            try:
                action = ReasoningPolicyAction(str(action))
            except ValueError as exc:
                raise ValueError(
                    f"unsupported reasoning policy action: {self.action!r}"
                ) from exc
        provider_value = _clean_optional(
            "provider_value",
            self.provider_value,
        )
        if action is ReasoningPolicyAction.MAP:
            if provider_value is None:
                raise ValueError(
                    "mapped reasoning rule requires provider_value"
                )
        elif provider_value is not None:
            raise ValueError(
                "omit/reject reasoning rule must not set provider_value"
            )
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "provider_value", provider_value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "provider_value": self.provider_value,
        }


def _reject_all_reasoning_rules() -> dict[str, ReasoningLevelRule]:
    return {
        level.value: ReasoningLevelRule(
            action=ReasoningPolicyAction.REJECT
        )
        for level in ReasoningLevel
    }


@dataclass(frozen=True, slots=True)
class ReasoningPolicy:
    parameter_name: str = "reasoning_effort"
    rules: Mapping[
        str | ReasoningLevel,
        ReasoningLevelRule,
    ] = field(default_factory=_reject_all_reasoning_rules)

    def __post_init__(self) -> None:
        parameter_name = _clean_required(
            "reasoning parameter_name",
            self.parameter_name,
        )
        if not isinstance(self.rules, Mapping):
            raise TypeError("reasoning rules must be a mapping")

        normalized: dict[str, ReasoningLevelRule] = {}
        for raw_level, raw_rule in self.rules.items():
            try:
                level = (
                    raw_level
                    if isinstance(raw_level, ReasoningLevel)
                    else ReasoningLevel(str(raw_level).strip())
                )
            except ValueError as exc:
                raise ValueError(
                    f"unsupported reasoning level: {raw_level!r}"
                ) from exc
            if not isinstance(raw_rule, ReasoningLevelRule):
                raise TypeError(
                    "reasoning rule values must be "
                    "ReasoningLevelRule instances"
                )
            normalized[level.value] = raw_rule

        expected = {level.value for level in ReasoningLevel}
        if set(normalized) != expected:
            raise ValueError(
                "reasoning rules must cover low/medium/high exactly"
            )
        object.__setattr__(self, "parameter_name", parameter_name)
        object.__setattr__(self, "rules", normalized)

    @classmethod
    def mapped(
        cls,
        *,
        low: str,
        medium: str,
        high: str,
        parameter_name: str = "reasoning_effort",
    ) -> "ReasoningPolicy":
        return cls(
            parameter_name=parameter_name,
            rules={
                ReasoningLevel.LOW: ReasoningLevelRule(
                    ReasoningPolicyAction.MAP, low
                ),
                ReasoningLevel.MEDIUM: ReasoningLevelRule(
                    ReasoningPolicyAction.MAP, medium
                ),
                ReasoningLevel.HIGH: ReasoningLevelRule(
                    ReasoningPolicyAction.MAP, high
                ),
            },
        )

    @classmethod
    def omit_all(
        cls,
        *,
        parameter_name: str = "reasoning_effort",
    ) -> "ReasoningPolicy":
        return cls(
            parameter_name=parameter_name,
            rules={
                level: ReasoningLevelRule(
                    ReasoningPolicyAction.OMIT
                )
                for level in ReasoningLevel
            },
        )

    @classmethod
    def reject_all(
        cls,
        *,
        parameter_name: str = "reasoning_effort",
    ) -> "ReasoningPolicy":
        return cls(
            parameter_name=parameter_name,
            rules={
                level: ReasoningLevelRule(
                    ReasoningPolicyAction.REJECT
                )
                for level in ReasoningLevel
            },
        )

    def apply(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        effective = _copy_json_mapping(
            "reasoning parameters",
            parameters,
        )
        if self.parameter_name not in effective:
            return effective

        raw_level = effective[self.parameter_name]
        if not isinstance(raw_level, str):
            raise UnsupportedReasoningLevelError(
                f"{self.parameter_name} must be one of low/medium/high"
            )
        try:
            level = ReasoningLevel(raw_level.strip().lower())
        except ValueError as exc:
            raise UnsupportedReasoningLevelError(
                f"{self.parameter_name} must be one of low/medium/high; "
                f"got {raw_level!r}"
            ) from exc

        rule = self.rules[level.value]
        if rule.action is ReasoningPolicyAction.REJECT:
            raise UnsupportedReasoningLevelError(
                f"model family rejects {self.parameter_name}="
                f"{level.value!r}"
            )
        if rule.action is ReasoningPolicyAction.OMIT:
            del effective[self.parameter_name]
            return effective

        effective[self.parameter_name] = rule.provider_value
        return effective

    def to_manifest(self) -> dict[str, Any]:
        return {
            "parameter_name": self.parameter_name,
            "actions": {
                level.value: self.rules[level.value].action.value
                for level in ReasoningLevel
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "parameter_name": self.parameter_name,
            "rules": {
                level.value: self.rules[level.value].to_dict()
                for level in ReasoningLevel
            },
        }


_CAPABILITY_INSTRUCTIONS = {
    ModelCapabilityTag.REASONING_MODEL: (
        "Reason internally, but do not expose private reasoning "
        "or chain-of-thought."
    ),
    ModelCapabilityTag.CODE_SPECIALIZED: (
        "Prefer precise, compilable C/C++ while preserving the "
        "declared interface and observable behavior."
    ),
    ModelCapabilityTag.STRICT_INSTRUCTION: (
        "Follow the supplied modification scope, evidence boundary, "
        "and output contract literally."
    ),
    ModelCapabilityTag.THINKING_TAG_POSSIBLE: (
        "Do not emit <think>, reasoning tags, or hidden-analysis "
        "text; return only the permitted final artifact."
    ),
    ModelCapabilityTag.STRICT_COMPLETION: (
        "Return one complete final artifact in this response; do not "
        "return a partial edit, continuation request, or patch."
    ),
}



class ModelArtifactKind(str, Enum):
    CANDIDATE = "candidate"
    TESTBENCH = "testbench"
    CANDIDATE_REPAIR = "candidate_repair"
    TESTBENCH_REPAIR = "testbench_repair"


@dataclass(frozen=True, slots=True)
class ModelOutputPolicy:
    """Typed maximum-output contract for one model family."""

    parameter_name: str = "max_tokens"
    default_limit: int | None = None
    safety_ceiling: int | None = None
    per_artifact_limits: Mapping[
        str | ModelArtifactKind,
        int,
    ] = field(default_factory=dict)

    def __post_init__(self) -> None:
        parameter_name = _clean_required(
            "ModelOutputPolicy.parameter_name",
            self.parameter_name,
        )

        def validate(name: str, value: int | None) -> int | None:
            if value is None:
                return None
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer or None")
            if value <= 0:
                raise ValueError(f"{name} must be positive")
            return value

        default_limit = validate("default_limit", self.default_limit)
        ceiling = validate("safety_ceiling", self.safety_ceiling)
        if (
            default_limit is not None
            and ceiling is not None
            and default_limit > ceiling
        ):
            raise ValueError(
                "default output limit must not exceed safety ceiling"
            )
        if not isinstance(self.per_artifact_limits, Mapping):
            raise TypeError("per_artifact_limits must be a mapping")
        normalized: dict[str, int] = {}
        for raw_kind, raw_limit in self.per_artifact_limits.items():
            try:
                kind = (
                    raw_kind
                    if isinstance(raw_kind, ModelArtifactKind)
                    else ModelArtifactKind(str(raw_kind))
                )
            except ValueError as exc:
                raise ValueError(
                    f"unsupported model artifact kind: {raw_kind!r}"
                ) from exc
            item = validate(
                f"per_artifact_limits[{kind.value}]",
                raw_limit,
            )
            assert item is not None
            if ceiling is not None and item > ceiling:
                raise ValueError(
                    f"{kind.value} output limit exceeds safety ceiling"
                )
            normalized[kind.value] = item
        object.__setattr__(self, "parameter_name", parameter_name)
        object.__setattr__(self, "default_limit", default_limit)
        object.__setattr__(self, "safety_ceiling", ceiling)
        object.__setattr__(self, "per_artifact_limits", normalized)

    def limit_for(
        self,
        artifact_kind: str | ModelArtifactKind | None,
    ) -> int | None:
        if artifact_kind is None:
            return self.default_limit
        kind = (
            artifact_kind
            if isinstance(artifact_kind, ModelArtifactKind)
            else ModelArtifactKind(str(artifact_kind))
        )
        return self.per_artifact_limits.get(
            kind.value,
            self.default_limit,
        )

    def apply(
        self,
        parameters: Mapping[str, Any],
        artifact_kind: str | ModelArtifactKind | None,
    ) -> dict[str, Any]:
        effective = _copy_json_mapping(
            "output-policy parameters",
            parameters,
        )
        selected = self.limit_for(artifact_kind)
        if self.parameter_name not in effective:
            if selected is not None:
                effective[self.parameter_name] = selected
            return effective
        value = effective[self.parameter_name]
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{self.parameter_name} must be an integer")
        if value <= 0:
            raise ValueError(f"{self.parameter_name} must be positive")
        if (
            self.safety_ceiling is not None
            and value > self.safety_ceiling
        ):
            raise ValueError(
                f"{self.parameter_name} exceeds model-family "
                "output safety ceiling"
            )
        return effective

    def to_manifest(self) -> dict[str, Any]:
        return {
            "parameter_name": self.parameter_name,
            "default_limit": self.default_limit,
            "safety_ceiling": self.safety_ceiling,
            "per_artifact_limits": dict(self.per_artifact_limits),
        }


class UnsupportedModelParameterError(ModelParameterPolicyError):
    pass


@dataclass(frozen=True, slots=True)
class ModelFamilyProfile:
    name: str
    capabilities: frozenset[ModelCapabilityTag] = field(
        default_factory=frozenset
    )
    safe_default_parameters: Mapping[str, Any] = field(
        default_factory=dict
    )
    verification_status: ModelProfileVerificationStatus = (
        ModelProfileVerificationStatus.DECLARED
    )
    verification_note: str | None = None
    reasoning_policy: ReasoningPolicy = field(
        default_factory=ReasoningPolicy.reject_all
    )
    parameter_aliases: Mapping[str, str] = field(default_factory=dict)
    rejected_parameters: frozenset[str] = field(
        default_factory=frozenset
    )
    supported_parameters: frozenset[str] = field(
        default_factory=frozenset
    )
    artifact_default_parameters: Mapping[
        str | ModelArtifactKind,
        Mapping[str, Any],
    ] = field(default_factory=dict)
    output_policy: ModelOutputPolicy = field(
        default_factory=ModelOutputPolicy
    )
    request_timeout_s: float = 240.0
    prompt_profile: str | None = None

    def __post_init__(self) -> None:
        name = _clean_required("name", self.name)
        raw_capabilities = self.capabilities
        if isinstance(raw_capabilities, (str, bytes)):
            raise TypeError(
                "capabilities must be an iterable of capability tags"
            )
        try:
            raw_items = tuple(raw_capabilities)
        except TypeError as exc:
            raise TypeError(
                "capabilities must be an iterable of capability tags"
            ) from exc

        normalized: set[ModelCapabilityTag] = set()
        for item in raw_items:
            if isinstance(item, ModelCapabilityTag):
                normalized.add(item)
                continue
            try:
                normalized.add(ModelCapabilityTag(str(item)))
            except ValueError as exc:
                raise ValueError(
                    f"unsupported model capability tag: {item!r}"
                ) from exc

        parameters = _copy_json_mapping(
            "safe_default_parameters",
            self.safe_default_parameters,
        )
        _reject_secret_keys(
            parameters,
            "safe_default_parameters",
        )

        status = self.verification_status
        if not isinstance(status, ModelProfileVerificationStatus):
            try:
                status = ModelProfileVerificationStatus(str(status))
            except ValueError as exc:
                raise ValueError(
                    "unsupported model profile verification status: "
                    f"{self.verification_status!r}"
                ) from exc

        if not isinstance(self.reasoning_policy, ReasoningPolicy):
            raise TypeError(
                "reasoning_policy must be a ReasoningPolicy"
            )

        if not isinstance(self.parameter_aliases, Mapping):
            raise TypeError("parameter_aliases must be a mapping")
        aliases: dict[str, str] = {}
        for raw_alias, raw_target in self.parameter_aliases.items():
            alias = _clean_required("parameter alias", str(raw_alias))
            target = _clean_required(
                "parameter alias target",
                str(raw_target),
            )
            if alias == target:
                raise ValueError(
                    f"parameter alias must change the name: {alias}"
                )
            aliases[alias] = target
        if set(aliases) & set(aliases.values()):
            raise ValueError(
                "parameter alias chains/cycles are not supported"
            )

        rejected = _clean_name_set(
            "rejected_parameters",
            self.rejected_parameters,
        )
        conflicts = sorted(
            (set(aliases) | set(aliases.values())) & rejected
        )
        if conflicts:
            raise ValueError(
                "aliased parameters must not also be rejected: "
                + ", ".join(conflicts)
            )

        supported = _clean_name_set(
            "supported_parameters",
            self.supported_parameters,
        )
        if supported:
            unknown_defaults = sorted(set(parameters) - set(supported))
            if unknown_defaults:
                raise ValueError(
                    "safe defaults are absent from supported_parameters: "
                    + ", ".join(unknown_defaults)
                )
            unknown_targets = sorted(
                set(aliases.values()) - set(supported)
            )
            if unknown_targets:
                raise ValueError(
                    "alias targets are absent from supported_parameters: "
                    + ", ".join(unknown_targets)
                )

        if not isinstance(self.artifact_default_parameters, Mapping):
            raise TypeError("artifact_default_parameters must be a mapping")
        artifact_defaults: dict[str, dict[str, Any]] = {}
        for raw_kind, raw_defaults in (
            self.artifact_default_parameters.items()
        ):
            try:
                kind = (
                    raw_kind
                    if isinstance(raw_kind, ModelArtifactKind)
                    else ModelArtifactKind(str(raw_kind))
                )
            except ValueError as exc:
                raise ValueError(
                    f"unsupported model artifact kind: {raw_kind!r}"
                ) from exc
            defaults = _copy_json_mapping(
                f"artifact_default_parameters[{kind.value}]",
                raw_defaults,
            )
            _reject_secret_keys(
                defaults,
                f"artifact_default_parameters[{kind.value}]",
            )
            if supported:
                unknown = sorted(set(defaults) - set(supported))
                if unknown:
                    raise ValueError(
                        f"{kind.value} defaults are absent from "
                        "supported_parameters: " + ", ".join(unknown)
                    )
            artifact_defaults[kind.value] = defaults

        if not isinstance(self.output_policy, ModelOutputPolicy):
            raise TypeError("output_policy must be a ModelOutputPolicy")
        timeout = self.request_timeout_s
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or float(timeout) <= 0
        ):
            raise ValueError("request_timeout_s must be positive")
        prompt_profile = (
            _clean_optional("prompt_profile", self.prompt_profile) or name
        )

        object.__setattr__(self, "name", name)
        object.__setattr__(
            self,
            "capabilities",
            frozenset(normalized),
        )
        object.__setattr__(
            self,
            "safe_default_parameters",
            parameters,
        )
        object.__setattr__(
            self,
            "verification_status",
            status,
        )
        object.__setattr__(
            self,
            "verification_note",
            _clean_optional(
                "verification_note",
                self.verification_note,
            ),
        )
        object.__setattr__(self, "parameter_aliases", aliases)
        object.__setattr__(self, "rejected_parameters", rejected)
        object.__setattr__(self, "supported_parameters", supported)
        object.__setattr__(
            self,
            "artifact_default_parameters",
            artifact_defaults,
        )
        object.__setattr__(self, "request_timeout_s", float(timeout))
        object.__setattr__(self, "prompt_profile", prompt_profile)

    @property
    def capability_tags(self) -> tuple[str, ...]:
        return tuple(
            tag.value
            for tag in ModelCapabilityTag
            if tag in self.capabilities
        )

    def render_instruction(self) -> str | None:
        instructions = tuple(
            _CAPABILITY_INSTRUCTIONS[tag]
            for tag in ModelCapabilityTag
            if tag in self.capabilities
        )
        if not instructions:
            return None
        return "\n".join(
            (
                "Apply these model-capability safeguards:",
                *(f"- {item}" for item in instructions),
            )
        )

    def _apply_aliases(
        self,
        parameters: Mapping[str, Any],
    ) -> dict[str, Any]:
        effective = _copy_json_mapping(
            "parameters before alias normalization",
            parameters,
        )
        for alias, canonical in self.parameter_aliases.items():
            if alias not in effective:
                continue
            alias_value = effective[alias]
            if (
                canonical in effective
                and effective[canonical] != alias_value
            ):
                raise ModelParameterAliasConflictError(
                    f"parameter alias conflict: {alias!r} and "
                    f"{canonical!r} have different values"
                )
            effective[canonical] = alias_value
            del effective[alias]
        return effective

    def _reject_parameters(
        self,
        parameters: Mapping[str, Any],
    ) -> None:
        present = sorted(
            set(parameters) & set(self.rejected_parameters)
        )
        if present:
            raise RejectedModelParameterError(
                f"model family {self.name!r} rejects parameters: "
                + ", ".join(present)
            )

    def merge_parameters(
        self,
        model_defaults: Mapping[str, Any] | None = None,
        call_overrides: Mapping[str, Any] | None = None,
        *,
        artifact_kind: str | ModelArtifactKind | None = None,
        output_policy_override: ModelOutputPolicy | None = None,
    ) -> dict[str, Any]:
        merged = dict(self.safe_default_parameters)
        if artifact_kind is not None:
            kind = (
                artifact_kind
                if isinstance(artifact_kind, ModelArtifactKind)
                else ModelArtifactKind(str(artifact_kind))
            )
            merged.update(
                self.artifact_default_parameters.get(kind.value, {})
            )
        if model_defaults is not None:
            merged.update(
                _copy_json_mapping(
                    "model_defaults",
                    model_defaults,
                )
            )
        if call_overrides is not None:
            merged.update(
                _copy_json_mapping(
                    "call_overrides",
                    call_overrides,
                )
            )

        _reject_secret_keys(merged, "effective_parameters")
        effective = self._apply_aliases(merged)
        self._reject_parameters(effective)
        effective = self.reasoning_policy.apply(effective)
        if (
            output_policy_override is not None
            and not isinstance(output_policy_override, ModelOutputPolicy)
        ):
            raise TypeError(
                "output_policy_override must be ModelOutputPolicy or None"
            )
        output_policy = output_policy_override or self.output_policy
        effective = output_policy.apply(effective, artifact_kind)
        # supported_parameters is declarative and intentionally non-exhaustive.
        # Compatible endpoints frequently require provider/model-specific
        # extension objects. Explicit hard rejection remains the responsibility
        # of rejected_parameters and the typed reasoning/output policies.
        _reject_secret_keys(effective, "effective_parameters")
        return _copy_json_mapping(
            "effective_parameters",
            effective,
        )

    def to_manifest(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "capability_tags": list(self.capability_tags),
            "instruction_present": (
                self.render_instruction() is not None
            ),
            "verification_status": self.verification_status.value,
            "verification_note": self.verification_note,
            "reasoning_policy": self.reasoning_policy.to_manifest(),
            "supported_parameters": sorted(self.supported_parameters),
            "parameter_aliases": sorted(self.parameter_aliases),
            "rejected_parameters": sorted(self.rejected_parameters),
            "artifact_default_kinds": sorted(
                self.artifact_default_parameters
            ),
            "output_policy": self.output_policy.to_manifest(),
            "request_timeout_s": self.request_timeout_s,
            "prompt_profile": self.prompt_profile,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.to_manifest(),
            "safe_default_parameters": dict(
                self.safe_default_parameters
            ),
            "reasoning_policy": self.reasoning_policy.to_dict(),
            "parameter_aliases": dict(self.parameter_aliases),
            "artifact_default_parameters": {
                key: dict(value)
                for key, value in self.artifact_default_parameters.items()
            },
        }


NEUTRAL_MODEL_FAMILY_PROFILE = ModelFamilyProfile(
    name="default",
    reasoning_policy=ReasoningPolicy.reject_all(),
    verification_note=(
        "No explicit model family was selected; vendor-specific "
        "reasoning parameters are rejected."
    ),
)
