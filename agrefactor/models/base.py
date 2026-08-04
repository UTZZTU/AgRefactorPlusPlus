"""Provider-neutral model request and response interfaces."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from typing import Any

from .pricing import CostEstimate, TokenUsageBreakdown


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
    copied = dict(value)
    try:
        serialized = json.dumps(copied, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be JSON-serializable") from exc
    return json.loads(serialized)


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """Describe one logical model without embedding credentials."""

    name: str
    provider: str
    model: str
    family: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    default_parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _clean_required("name", self.name))
        object.__setattr__(
            self,
            "provider",
            _clean_required("provider", self.provider),
        )
        object.__setattr__(self, "model", _clean_required("model", self.model))
        object.__setattr__(
            self,
            "family",
            _clean_optional("family", self.family),
        )
        object.__setattr__(
            self,
            "base_url",
            _clean_optional("base_url", self.base_url),
        )
        object.__setattr__(
            self,
            "api_key_env",
            _clean_optional("api_key_env", self.api_key_env),
        )
        object.__setattr__(
            self,
            "default_parameters",
            _copy_json_mapping(
                "default_parameters",
                self.default_parameters,
            ),
        )


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """One provider-neutral chat message."""

    role: str
    content: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _clean_required("role", self.role))
        object.__setattr__(
            self,
            "content",
            _clean_required("content", self.content),
        )


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """Provider-neutral request passed to a model provider."""

    messages: tuple[ChatMessage, ...]
    parameters: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        messages = tuple(self.messages)
        if not messages:
            raise ValueError("ModelRequest.messages must not be empty")
        if not all(isinstance(message, ChatMessage) for message in messages):
            raise TypeError(
                "ModelRequest.messages must contain only ChatMessage values"
            )

        object.__setattr__(self, "messages", messages)
        object.__setattr__(
            self,
            "parameters",
            _copy_json_mapping("parameters", self.parameters),
        )
        object.__setattr__(
            self,
            "metadata",
            _copy_json_mapping("metadata", self.metadata),
        )


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Normalized token and cost accounting returned by a provider."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None
    breakdown: TokenUsageBreakdown | None = None
    estimated_cost: CostEstimate | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("prompt_tokens", self.prompt_tokens),
            ("completion_tokens", self.completion_tokens),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

        if self.cost_usd is not None:
            if (
                isinstance(self.cost_usd, bool)
                or not isinstance(self.cost_usd, (int, float))
                or not isfinite(float(self.cost_usd))
                or self.cost_usd < 0
            ):
                raise ValueError(
                    "cost_usd must be a finite non-negative number or None"
                )

        if self.breakdown is not None and not isinstance(
            self.breakdown,
            TokenUsageBreakdown,
        ):
            raise TypeError(
                "breakdown must be a TokenUsageBreakdown or None"
            )
        if self.estimated_cost is not None and not isinstance(
            self.estimated_cost,
            CostEstimate,
        ):
            raise TypeError(
                "estimated_cost must be a CostEstimate or None"
            )
        if (
            self.cost_usd is not None
            and self.estimated_cost is not None
            and self.estimated_cost.currency not in (None, "USD")
        ):
            raise ValueError(
                "cost_usd must be None when estimated_cost uses "
                "a non-USD currency"
            )

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "breakdown": (
                None
                if self.breakdown is None
                else self.breakdown.to_dict()
            ),
            "estimated_cost": (
                None
                if self.estimated_cost is None
                else self.estimated_cost.to_dict()
            ),
        }


@dataclass(frozen=True, slots=True)
class ModelResponse:
    """Normalized response returned by any provider implementation."""

    text: str
    model: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    finish_reason: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _clean_required("text", self.text))
        object.__setattr__(self, "model", _clean_required("model", self.model))
        if not isinstance(self.usage, TokenUsage):
            raise TypeError("usage must be a TokenUsage")
        object.__setattr__(
            self,
            "finish_reason",
            _clean_optional("finish_reason", self.finish_reason),
        )
        object.__setattr__(
            self,
            "metadata",
            _copy_json_mapping("metadata", self.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "model": self.model,
            "usage": self.usage.to_dict(),
            "finish_reason": self.finish_reason,
            "metadata": dict(self.metadata),
        }


class ModelProvider(ABC):
    """Abstract interface implemented by concrete LLM providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return a stable provider identifier."""

    @abstractmethod
    def generate(
        self,
        model: ModelSpec,
        request: ModelRequest,
    ) -> ModelResponse:
        """Generate one response for the supplied logical model."""
