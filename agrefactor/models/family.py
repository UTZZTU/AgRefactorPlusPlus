"""Typed, vendor-neutral model-family capabilities and safe defaults."""

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
                    "safe_default_parameters must not contain "
                    f"credential-like key: {child_path}"
                )
            _reject_secret_keys(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_keys(child, f"{path}[{index}]")


class ModelCapabilityTag(str, Enum):
    """Minimal capabilities used only for safe defaults and instructions."""

    REASONING_MODEL = "reasoning_model"
    CODE_SPECIALIZED = "code_specialized"
    STRICT_INSTRUCTION = "strict_instruction"
    THINKING_TAG_POSSIBLE = "thinking_tag_possible"
    STRICT_COMPLETION = "strict_completion"


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


@dataclass(frozen=True, slots=True)
class ModelFamilyProfile:
    """Describe one family without selecting or replacing a model."""

    name: str
    capabilities: frozenset[ModelCapabilityTag] = field(
        default_factory=frozenset
    )
    safe_default_parameters: Mapping[str, Any] = field(
        default_factory=dict
    )

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

    def merge_parameters(
        self,
        model_defaults: Mapping[str, Any] | None = None,
        call_overrides: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply profile defaults, then model defaults, then call overrides."""

        merged = dict(self.safe_default_parameters)
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
        return _copy_json_mapping(
            "effective_parameters",
            merged,
        )

    def to_manifest(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "capability_tags": list(self.capability_tags),
            "instruction_present": (
                self.render_instruction() is not None
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.to_manifest(),
            "safe_default_parameters": dict(
                self.safe_default_parameters
            ),
        }


NEUTRAL_MODEL_FAMILY_PROFILE = ModelFamilyProfile(
    name="default"
)
