"""OpenAI-compatible implementation of the provider-neutral model API."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

from .base import (
    ChatMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelSpec,
    TokenUsage,
)
from .pricing import TokenUsageBreakdown


class OpenAICompatibleProviderError(RuntimeError):
    """Base error raised by the OpenAI-compatible provider."""


class MissingModelCredentialError(OpenAICompatibleProviderError):
    """Raised when the configured API-key environment variable is absent."""


class OpenAICompatibleResponseError(OpenAICompatibleProviderError):
    """Raised when an endpoint returns an unusable response."""


_RESERVED_PARAMETER_NAMES = {
    "model",
    "messages",
}


def _read(value: Any, name: str, default: Any = None) -> Any:
    """Read one field from either an SDK object or a mapping."""

    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _require_non_negative_int(
    value: Any,
    *,
    field_name: str,
) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OpenAICompatibleResponseError(
            f"{field_name} must be a non-negative integer"
        )
    return value


def _optional_non_negative_int(
    value: Any,
    *,
    field_name: str,
) -> int | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
    ):
        raise OpenAICompatibleResponseError(
            f"{field_name} must be a non-negative integer or None"
        )
    return value


def _read_path(
    value: Any,
    path: tuple[str, ...],
) -> Any:
    current = value
    for name in path:
        if current is None:
            return None
        current = _read(current, name)
    return current


def _coalesce_usage_int(
    usage: Any,
    *,
    field_name: str,
    paths: tuple[tuple[str, ...], ...],
) -> int | None:
    observed: list[tuple[str, int]] = []
    for path in paths:
        raw = _read_path(usage, path)
        if raw is None:
            continue
        normalized = _optional_non_negative_int(
            raw,
            field_name="usage." + ".".join(path),
        )
        assert normalized is not None
        observed.append((".".join(path), normalized))

    if not observed:
        return None

    expected = observed[0][1]
    if any(value != expected for _, value in observed[1:]):
        details = ", ".join(
            f"{name}={value}"
            for name, value in observed
        )
        raise OpenAICompatibleResponseError(
            f"conflicting usage fields for {field_name}: "
            + details
        )
    return expected


def _normalize_usage_breakdown(
    usage: Any,
    *,
    prompt_tokens: int,
    completion_tokens: int,
) -> TokenUsageBreakdown | None:
    if usage is None:
        return None

    cache_hit = _coalesce_usage_int(
        usage,
        field_name="cached input tokens",
        paths=(
            ("prompt_cache_hit_tokens",),
            ("cached_tokens",),
            ("prompt_tokens_details", "cached_tokens"),
            ("input_tokens_details", "cached_tokens"),
        ),
    )
    cache_miss = _coalesce_usage_int(
        usage,
        field_name="cache-miss input tokens",
        paths=(("prompt_cache_miss_tokens",),),
    )

    if cache_hit is not None or cache_miss is not None:
        if cache_hit is None:
            assert cache_miss is not None
            if cache_miss > prompt_tokens:
                raise OpenAICompatibleResponseError(
                    "usage cache partition exceeds prompt_tokens"
                )
            cache_hit = prompt_tokens - cache_miss
        elif cache_miss is None:
            if cache_hit > prompt_tokens:
                raise OpenAICompatibleResponseError(
                    "usage cache partition exceeds prompt_tokens"
                )
            cache_miss = prompt_tokens - cache_hit
        elif cache_hit + cache_miss != prompt_tokens:
            raise OpenAICompatibleResponseError(
                "usage cache partition must equal prompt_tokens"
            )

    cache_read = _coalesce_usage_int(
        usage,
        field_name="cache_read input tokens",
        paths=(
            ("cache_read_input_tokens",),
            ("prompt_tokens_details", "cache_read_tokens"),
        ),
    )
    cache_write = _coalesce_usage_int(
        usage,
        field_name="cache creation input tokens",
        paths=(
            ("cache_creation_input_tokens",),
            (
                "prompt_tokens_details",
                "cache_creation_input_tokens",
            ),
            (
                "prompt_tokens_details",
                "cache_creation",
                "cache_creation_input_tokens",
            ),
        ),
    )
    thinking = _coalesce_usage_int(
        usage,
        field_name="reasoning output tokens",
        paths=(
            (
                "completion_tokens_details",
                "reasoning_tokens",
            ),
            (
                "output_tokens_details",
                "reasoning_tokens",
            ),
        ),
    )
    if (
        thinking is not None
        and thinking > completion_tokens
    ):
        raise OpenAICompatibleResponseError(
            "usage reasoning tokens exceed completion_tokens"
        )

    values = (
        cache_hit,
        cache_miss,
        cache_read,
        cache_write,
        thinking,
    )
    if all(value is None for value in values):
        return None

    return TokenUsageBreakdown(
        cache_hit_input_tokens=cache_hit,
        cache_miss_input_tokens=cache_miss,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        thinking_output_tokens=thinking,
    )


def _normalize_content(content: Any) -> str:
    """Normalize common Chat Completions content representations."""

    if isinstance(content, str):
        cleaned = content.strip()
        if cleaned:
            return cleaned
        raise OpenAICompatibleResponseError(
            "response message content is empty"
        )

    if isinstance(content, (list, tuple)):
        text_parts: list[str] = []
        for block in content:
            text = _read(block, "text")
            if isinstance(text, str) and text:
                text_parts.append(text)
        cleaned = "".join(text_parts).strip()
        if cleaned:
            return cleaned

    raise OpenAICompatibleResponseError(
        "response message content is missing or unsupported"
    )


class OpenAICompatibleProvider(ModelProvider):
    """Call an OpenAI-compatible Chat Completions endpoint.

    Credentials are resolved from environment variables named by ModelSpec.
    The SDK import is lazy so unit tests and non-model workflows do not need
    to construct a real network client.
    """

    def __init__(
        self,
        *,
        name: str = "openai-compatible",
        default_base_url: str | None = None,
        default_api_key_env: str = "OPENAI_API_KEY",
        timeout_s: float | None = None,
        client_factory: Callable[..., Any] | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("provider name must not be empty")

        cleaned_key_env = default_api_key_env.strip()
        if not cleaned_key_env:
            raise ValueError(
                "default_api_key_env must not be empty"
            )

        if timeout_s is not None and timeout_s <= 0:
            raise ValueError("timeout_s must be positive or None")

        self._name = cleaned_name
        self._default_base_url = (
            default_base_url.strip()
            if isinstance(default_base_url, str)
            and default_base_url.strip()
            else None
        )
        self._default_api_key_env = cleaned_key_env
        self._timeout_s = timeout_s
        self._client_factory = client_factory
        self._environment = (
            dict(environment)
            if environment is not None
            else None
        )

    @property
    def name(self) -> str:
        return self._name

    def generate(
        self,
        model: ModelSpec,
        request: ModelRequest,
    ) -> ModelResponse:
        if not isinstance(model, ModelSpec):
            raise TypeError("model must be a ModelSpec")
        if not isinstance(request, ModelRequest):
            raise TypeError("request must be a ModelRequest")

        parameters = dict(request.parameters)
        reserved = sorted(
            _RESERVED_PARAMETER_NAMES.intersection(parameters)
        )
        if reserved:
            raise ValueError(
                "request parameters must not override reserved fields: "
                + ", ".join(reserved)
            )

        api_key_env = (
            model.api_key_env
            or self._default_api_key_env
        )
        environment = (
            self._environment
            if self._environment is not None
            else os.environ
        )
        api_key = environment.get(api_key_env)
        if not api_key:
            raise MissingModelCredentialError(
                f"missing API credential environment variable: "
                f"{api_key_env}"
            )

        base_url = model.base_url or self._default_base_url
        client_kwargs: dict[str, Any] = {
            "api_key": api_key,
        }
        if base_url:
            client_kwargs["base_url"] = base_url
        if self._timeout_s is not None:
            client_kwargs["timeout"] = self._timeout_s

        client = self._make_client(**client_kwargs)
        messages = [
            {
                "role": message.role,
                "content": message.content,
            }
            for message in request.messages
        ]

        response = client.chat.completions.create(
            model=model.model,
            messages=messages,
            **parameters,
        )

        choices = _read(response, "choices")
        if not isinstance(choices, (list, tuple)) or not choices:
            raise OpenAICompatibleResponseError(
                "response contains no choices"
            )

        first_choice = choices[0]
        message = _read(first_choice, "message")
        if message is None:
            raise OpenAICompatibleResponseError(
                "first choice contains no message"
            )

        text = _normalize_content(
            _read(message, "content")
        )
        finish_reason = _read(
            first_choice,
            "finish_reason",
        )

        usage_object = _read(response, "usage")
        prompt_tokens = (
            _coalesce_usage_int(
                usage_object,
                field_name="prompt tokens",
                paths=(
                    ("prompt_tokens",),
                    ("input_tokens",),
                ),
            )
            or 0
        )
        completion_tokens = (
            _coalesce_usage_int(
                usage_object,
                field_name="completion tokens",
                paths=(
                    ("completion_tokens",),
                    ("output_tokens",),
                ),
            )
            or 0
        )
        usage_breakdown = _normalize_usage_breakdown(
            usage_object,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

        response_model = _read(response, "model")
        if not isinstance(response_model, str) or not response_model.strip():
            response_model = model.model

        reasoning_content = _read(
            message,
            "reasoning_content",
        )
        metadata = {
            "provider": self.name,
            "logical_model": model.name,
            "requested_model": model.model,
            "response_id": _read(response, "id"),
            "created": _read(response, "created"),
            "system_fingerprint": _read(
                response,
                "system_fingerprint",
            ),
            "base_url": base_url,
            "has_reasoning_content": bool(reasoning_content),
            "reasoning_content_chars": (
                len(reasoning_content)
                if isinstance(reasoning_content, str)
                else 0
            ),
            "usage_breakdown_observed": (
                usage_breakdown is not None
            ),
            "usage_breakdown_categories": (
                []
                if usage_breakdown is None
                else [
                    key
                    for key, value
                    in usage_breakdown.to_dict().items()
                    if value is not None
                ]
            ),
        }
        metadata = {
            key: value
            for key, value in metadata.items()
            if value is not None
        }

        return ModelResponse(
            text=text,
            model=response_model.strip(),
            usage=TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=None,
                breakdown=usage_breakdown,
            ),
            finish_reason=finish_reason,
            metadata=metadata,
        )

    def _make_client(self, **kwargs: Any) -> Any:
        if self._client_factory is not None:
            return self._client_factory(**kwargs)

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise OpenAICompatibleProviderError(
                "the openai package is required for "
                "OpenAICompatibleProvider"
            ) from exc

        return OpenAI(**kwargs)
