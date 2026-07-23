"""Secret-free evidence for actual model prompt launches."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
from threading import RLock
from typing import Any

_PROMPT_EVIDENCE_SCHEMA_VERSION = 1
_LOCK = RLock()
_CALLS: list[dict[str, Any]] = []


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def reset_model_prompt_evidence() -> None:
    """Clear the current process run's prompt-launch evidence."""

    with _LOCK:
        _CALLS.clear()


def record_model_prompt_call(
    *,
    template_id: str,
    template_version: int,
    system_message: str | None,
    invocation: Any,
    provider_call_observed: bool,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Record hashes of the actual prompt inputs without retaining plaintext."""

    if not isinstance(template_id, str) or not template_id.strip():
        raise ValueError("template_id must not be empty")
    if (
        isinstance(template_version, bool)
        or not isinstance(template_version, int)
        or template_version < 1
    ):
        raise ValueError("template_version must be a positive integer")
    if system_message is not None and not isinstance(system_message, str):
        raise TypeError("system_message must be a string or None")
    if not isinstance(provider_call_observed, bool):
        raise TypeError("provider_call_observed must be boolean")
    safe_metadata = dict(metadata or {})
    # Metadata is identity-only. It must never retain rendered messages.
    for key in tuple(safe_metadata):
        normalized = str(key).lower().replace("-", "_")
        if any(token in normalized for token in ("prompt", "message", "content", "secret", "api_key")):
            safe_metadata.pop(key, None)

    system_hash = (
        None
        if system_message is None
        else sha256(system_message.encode("utf-8")).hexdigest()
    )
    try:
        invocation_hash = _canonical_sha256(invocation)
    except (TypeError, ValueError, RecursionError):
        invocation_hash = _canonical_sha256(
            {
                "type": type(invocation).__name__,
                "representation_sha256": sha256(
                    repr(invocation).encode("utf-8", errors="replace")
                ).hexdigest(),
            }
        )
    sequence_hash = _canonical_sha256(
        {
            "system_message_sha256": system_hash,
            "invocation_sha256": invocation_hash,
        }
    )
    with _LOCK:
        record = {
            "schema_version": _PROMPT_EVIDENCE_SCHEMA_VERSION,
            "call_index": len(_CALLS) + 1,
            "template_id": template_id.strip(),
            "template_version": template_version,
            "system_message_sha256": system_hash,
            "invocation_sha256": invocation_hash,
            "message_sequence_sha256": sequence_hash,
            "provider_call_observed": provider_call_observed,
            "metadata": safe_metadata,
        }
        record["call_identity_sha256"] = _canonical_sha256(record)
        _CALLS.append(record)
        return json.loads(json.dumps(record, sort_keys=True))


def get_model_prompt_evidence() -> dict[str, Any]:
    """Return one stable, secret-free prompt evidence bundle."""

    with _LOCK:
        calls = json.loads(json.dumps(_CALLS, sort_keys=True))
    return {
        "schema_version": _PROMPT_EVIDENCE_SCHEMA_VERSION,
        "actual_call_count": sum(
            1 for item in calls if item.get("provider_call_observed") is True
        ),
        "calls": calls,
        "aggregate_sha256": _canonical_sha256(calls),
    }
