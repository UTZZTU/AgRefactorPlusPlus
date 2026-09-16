"""Immutable, append-only R4 repair episode evidence.

This schema is intentionally separate from the R3 shadow DiagnosticEpisode.
It records an authorized Candidate mutation and full-prefix evidence without
granting the episode or its writer success authority.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

OUTCOMES = frozenset({"verified_positive", "verified_negative", "abstained", "inconclusive", "invalid_evidence"})


def _json(value: Mapping[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    try:
        result = json.loads(json.dumps(dict(value), ensure_ascii=False, allow_nan=False, sort_keys=True))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain finite JSON") from exc
    if not isinstance(result, dict):
        raise TypeError(f"{name} must be an object")
    return result


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _digest(value: Any, name: str) -> str:
    value = _text(value, name).lower()
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be a SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True)
class R4RepairEpisode:
    episode_id: str
    created_at: str
    event_ref: str
    execution_identity: Mapping[str, Any]
    request_identity: Mapping[str, Any]
    advisory_identity: Mapping[str, Any]
    gate_identity: Mapping[str, Any]
    authorization_identity: Mapping[str, Any]
    candidate_before_sha256: str
    candidate_after_sha256: str | None
    public_testbench_before: Mapping[str, str]
    public_testbench_after: Mapping[str, str]
    hidden_testbench_before: Mapping[str, str]
    hidden_testbench_after: Mapping[str, str]
    formal_validation_id: str | None
    validation_evidence_refs: tuple[str, ...]
    auditor_result: Mapping[str, Any]
    budget_delta: Mapping[str, Any]
    provider_call_count: int
    vitis_phase_count: int
    outcome: str
    outcome_reason: str
    episode_hash: str = ""

    def __post_init__(self) -> None:
        episode_id = _text(self.episode_id, "episode_id")
        if episode_id in {".", ".."} or "/" in episode_id or "\\" in episode_id:
            raise ValueError("episode_id must be a single path component")
        _text(self.created_at, "created_at")
        _text(self.event_ref, "event_ref")
        for name in ("execution_identity", "request_identity", "advisory_identity", "gate_identity", "authorization_identity", "auditor_result", "budget_delta"):
            object.__setattr__(self, name, _freeze(_json(getattr(self, name), name)))
        object.__setattr__(self, "candidate_before_sha256", _digest(self.candidate_before_sha256, "candidate_before_sha256"))
        if self.candidate_after_sha256 is not None:
            object.__setattr__(self, "candidate_after_sha256", _digest(self.candidate_after_sha256, "candidate_after_sha256"))
        for name in ("public_testbench_before", "public_testbench_after", "hidden_testbench_before", "hidden_testbench_after"):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise TypeError(f"{name} must be a mapping")
            normalized = {str(key): _digest(item, f"{name}[{key}]") for key, item in value.items()}
            object.__setattr__(self, name, MappingProxyType(dict(sorted(normalized.items()))))
        if self.formal_validation_id is not None:
            _text(self.formal_validation_id, "formal_validation_id")
        refs = tuple(_text(item, "validation_evidence_ref") for item in self.validation_evidence_refs)
        if len(refs) != len(set(refs)):
            raise ValueError("validation_evidence_refs must be unique")
        object.__setattr__(self, "validation_evidence_refs", refs)
        if isinstance(self.provider_call_count, bool) or not isinstance(self.provider_call_count, int) or self.provider_call_count < 0:
            raise ValueError("provider_call_count must be non-negative")
        if isinstance(self.vitis_phase_count, bool) or not isinstance(self.vitis_phase_count, int) or self.vitis_phase_count < 0:
            raise ValueError("vitis_phase_count must be non-negative")
        if self.outcome not in OUTCOMES:
            raise ValueError("unsupported R4 outcome")
        _text(self.outcome_reason, "outcome_reason")
        if self.outcome == "verified_positive":
            if self.candidate_after_sha256 is None or self.formal_validation_id is None or not refs:
                raise ValueError("verified_positive requires mutation and full validation evidence")
            if self.provider_call_count != 1 or self.vitis_phase_count < 1:
                raise ValueError("verified_positive requires one provider call and real Vitis phases")
            if self.auditor_result.get("status") != "clean":
                raise ValueError("verified_positive requires a clean independent auditor")
            if dict(self.public_testbench_before) != dict(self.public_testbench_after) or dict(self.hidden_testbench_before) != dict(self.hidden_testbench_after):
                raise ValueError("verified_positive requires unchanged Testbench identities")
        if self.outcome == "verified_negative":
            if self.auditor_result.get("failure_attributable") is not True:
                raise ValueError("verified_negative requires independent failure attribution")
            if self.auditor_result.get("environment_excluded") is not True:
                raise ValueError("verified_negative requires environment exclusion")
        if self.outcome == "abstained" and (self.candidate_after_sha256 is not None or self.provider_call_count != 0):
            raise ValueError("abstained episode cannot contain a mutation or provider call")
        expected = self._compute_hash()
        if self.episode_hash and self.episode_hash != expected:
            raise ValueError("episode_hash mismatch")
        object.__setattr__(self, "episode_hash", expected)

    def _payload(self) -> dict[str, Any]:
        return {"schema_version": 1, "episode_id": self.episode_id, "created_at": self.created_at, "event_ref": self.event_ref, "execution_identity": _plain(self.execution_identity), "request_identity": _plain(self.request_identity), "advisory_identity": _plain(self.advisory_identity), "gate_identity": _plain(self.gate_identity), "authorization_identity": _plain(self.authorization_identity), "candidate_before_sha256": self.candidate_before_sha256, "candidate_after_sha256": self.candidate_after_sha256, "public_testbench_before": dict(self.public_testbench_before), "public_testbench_after": dict(self.public_testbench_after), "hidden_testbench_before": dict(self.hidden_testbench_before), "hidden_testbench_after": dict(self.hidden_testbench_after), "formal_validation_id": self.formal_validation_id, "validation_evidence_refs": list(self.validation_evidence_refs), "auditor_result": _plain(self.auditor_result), "budget_delta": _plain(self.budget_delta), "provider_call_count": self.provider_call_count, "vitis_phase_count": self.vitis_phase_count, "outcome": self.outcome, "outcome_reason": self.outcome_reason}

    def _compute_hash(self) -> str:
        return hashlib.sha256(json.dumps(self._payload(), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        value = self._payload()
        value["episode_hash"] = self.episode_hash
        value["accepted_by_episode"] = False
        return value

    def append_only_write(self, root: str | Path) -> Path:
        root = Path(root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{self.episode_id}.json"
        if path.exists():
            raise FileExistsError(f"episode already exists: {self.episode_id}")
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        return path


class R4RepairEpisodeReader:
    """Read-only verifier for append-only R4 episode files."""

    @staticmethod
    def read(path: str | Path) -> R4RepairEpisode:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        payload.pop("schema_version", None)
        payload.pop("accepted_by_episode", None)
        return R4RepairEpisode(**payload)


__all__ = ["R4RepairEpisode", "R4RepairEpisodeReader", "OUTCOMES"]
