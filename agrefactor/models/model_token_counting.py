"""Verified local token counting for supported DeepSeek chat requests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any


class TokenCountingUnavailable(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class TokenCountConfig:
    provider: str
    model_id: str
    base_url: str
    request_mode: str
    tokenizer_dir: Path
    tokenizer_hashes: Mapping[str, str]
    context_window: int
    safe_margin_tokens: int
    verification_status: str


class ModelTokenCounter:
    def __init__(self, config: TokenCountConfig, tokenizer: Any) -> None:
        self.config = config
        self._tokenizer = tokenizer

    @classmethod
    def from_manifest(
        cls,
        manifest_path: str | Path,
        *,
        allow_pending_verification: bool = False,
    ) -> "ModelTokenCounter":
        path = Path(manifest_path)
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TokenCountingUnavailable("counting_manifest_unavailable") from exc
        if not isinstance(manifest, Mapping) or manifest.get("schema_version") != 1:
            raise TokenCountingUnavailable("counting_manifest_invalid")
        tokenizer_dir = Path(manifest["tokenizer_dir"])
        hashes = manifest["tokenizer_sha256"]
        if not isinstance(hashes, Mapping) or set(hashes) != {"tokenizer.json", "tokenizer_config.json"}:
            raise TokenCountingUnavailable("tokenizer_files_unregistered")
        for name, expected in hashes.items():
            file_path = tokenizer_dir / name
            if not file_path.is_file() or _sha256(file_path) != expected:
                raise TokenCountingUnavailable("tokenizer_hash_mismatch")
        status = manifest.get("verification_status")
        if status != "verified" and not (
            allow_pending_verification and status == "pending_usage_check"
        ):
            raise TokenCountingUnavailable("message_usage_not_verified")
        safe_margin_tokens = manifest.get("safe_margin_tokens")
        if status == "verified" and (
            isinstance(safe_margin_tokens, bool)
            or not isinstance(safe_margin_tokens, int)
            or safe_margin_tokens < 0
        ):
            raise TokenCountingUnavailable("safe_margin_unavailable")
        try:
            from transformers import AutoTokenizer

            tokenizer = AutoTokenizer.from_pretrained(
                tokenizer_dir,
                local_files_only=True,
                trust_remote_code=False,
            )
        except Exception as exc:
            raise TokenCountingUnavailable("tokenizer_load_failed") from exc
        if not tokenizer.chat_template:
            raise TokenCountingUnavailable("chat_template_missing")
        return cls(
            TokenCountConfig(
                provider=str(manifest["provider"]),
                model_id=str(manifest["model_id"]),
                base_url=str(manifest["base_url"]).rstrip("/"),
                request_mode=str(manifest["request_mode"]),
                tokenizer_dir=tokenizer_dir,
                tokenizer_hashes=dict(hashes),
                context_window=int(manifest["context_window"]),
                safe_margin_tokens=(0 if safe_margin_tokens is None else int(safe_margin_tokens)),
                verification_status=str(status),
            ),
            tokenizer,
        )

    def matches(self, *, provider: str, model_id: str, base_url: str, request_mode: str) -> bool:
        return (
            provider.casefold() == self.config.provider.casefold()
            and model_id == self.config.model_id
            and base_url.rstrip("/") == self.config.base_url
            and request_mode == self.config.request_mode
        )

    def count_messages(self, messages: Sequence[Any]) -> int:
        prepared: list[dict[str, Any]] = []
        for message in messages:
            if isinstance(message, Mapping):
                role = message.get("role")
                content = message.get("content")
                allowed = {"role", "content", "tool_calls", "name", "tool_call_id"}
                if set(message) - allowed:
                    raise TokenCountingUnavailable("message_shape_unregistered")
                if set(message) & {"tool_calls", "name", "tool_call_id"}:
                    raise TokenCountingUnavailable("tool_message_mode_unregistered")
            else:
                role = getattr(message, "role", None)
                content = getattr(message, "content", None)
            if role not in {"system", "user", "assistant", "tool"}:
                raise TokenCountingUnavailable("message_role_unregistered")
            if not isinstance(content, str):
                raise TokenCountingUnavailable("message_content_unregistered")
            prepared.append({"role": role, "content": content})
        try:
            token_ids = self._tokenizer.apply_chat_template(
                prepared,
                tokenize=True,
                add_generation_prompt=True,
            )
        except Exception as exc:
            raise TokenCountingUnavailable("chat_template_count_failed") from exc
        return len(token_ids)


@dataclass(frozen=True, slots=True)
class PromptBudgetResult:
    selected_snippets: tuple[str, ...]
    selected_ids: tuple[str, ...]
    skipped: tuple[tuple[str, str], ...]
    base_tokens: int | None
    final_tokens: int | None
    experience_budget: int
    context_window: int | None
    output_reserve: int | None
    safe_margin: int | None
    reason: str | None


def _render_experience(candidate: Any) -> str:
    from agrefactor.prompts.experience_context import (
        render_experience_reference,
    )

    return render_experience_reference(candidate)


def select_experience_context(
    result: Any,
    *,
    counter: ModelTokenCounter | None,
    provider: str,
    model_id: str,
    base_url: str,
    request_mode: str,
    call_parameters: Mapping[str, Any],
    render_messages,
) -> tuple[tuple[Any, ...], PromptBudgetResult]:
    """Add whole ranked records only when the final rendered messages fit."""
    base_messages = tuple(render_messages(()))
    if counter is None or counter.config.verification_status != "verified" or not counter.matches(
        provider=provider,
        model_id=model_id,
        base_url=base_url,
        request_mode=request_mode,
    ):
        return base_messages, PromptBudgetResult((), (), (), None, None, 0, None, None, None, "tokenizer_unavailable")
    try:
        base_tokens = counter.count_messages(base_messages)
    except TokenCountingUnavailable:
        return base_messages, PromptBudgetResult((), (), (), None, None, 0, None, None, None, "message_shape_unregistered")
    output_reserve = call_parameters.get("max_tokens", call_parameters.get("max_completion_tokens"))
    if isinstance(output_reserve, bool) or not isinstance(output_reserve, int) or output_reserve < 0:
        return base_messages, PromptBudgetResult((), (), (), base_tokens, base_tokens, 0, counter.config.context_window, None, counter.config.safe_margin_tokens, "output_reserve_unavailable")
    context = counter.config.context_window
    safe_margin = counter.config.safe_margin_tokens
    exp_budget = min(
        int(context * 0.15),
        max(0, context - output_reserve - base_tokens - safe_margin),
    )
    selected: list[Any] = []
    snippets: list[str] = []
    skipped: list[tuple[str, str]] = []
    final_messages = base_messages
    final_tokens = base_tokens
    remaining = (
        [(item, 3, "positive_limit") for item in result.positive]
        + [(item, 2, "negative_limit") for item in result.negative]
    )
    per_polarity = {"positive": 0, "negative": 0}
    for candidate, limit, limit_reason in remaining:
        polarity = candidate.polarity.value
        identity = candidate.item.record.experience_id
        if per_polarity[polarity] >= limit:
            skipped.append((identity, limit_reason))
            continue
        if not candidate.change_available and candidate.polarity.value == "positive":
            # A summary may still help diagnose an unknown owner, but never include code.
            summary_only = replace_candidate_change_availability(candidate, False)
            proposed_text = _render_experience(summary_only)
        else:
            proposed_text = _render_experience(candidate)
        proposed_snippets = tuple((*snippets, proposed_text))
        proposed_messages = tuple(render_messages(proposed_snippets))
        try:
            proposed_tokens = counter.count_messages(proposed_messages)
        except TokenCountingUnavailable:
            skipped.append((identity, "message_shape_unregistered"))
            continue
        added_tokens = max(0, proposed_tokens - base_tokens)
        if added_tokens > exp_budget:
            skipped.append((identity, "experience_budget_exceeded"))
            continue
        if proposed_tokens + output_reserve + safe_margin > context:
            skipped.append((identity, "context_window_exceeded"))
            continue
        snippets.append(proposed_text)
        selected.append(candidate)
        per_polarity[polarity] += 1
        final_messages = proposed_messages
        final_tokens = proposed_tokens
    return final_messages, PromptBudgetResult(
        selected_snippets=tuple(snippets),
        selected_ids=tuple(item.item.record.experience_id for item in selected),
        skipped=tuple(skipped),
        base_tokens=base_tokens,
        final_tokens=final_tokens,
        experience_budget=exp_budget,
        context_window=context,
        output_reserve=output_reserve,
        safe_margin=safe_margin,
        reason=None if selected else "no_eligible_experience_fit",
    )


def replace_candidate_change_availability(candidate: Any, value: bool) -> Any:
    from dataclasses import replace

    return replace(candidate, change_available=value)
