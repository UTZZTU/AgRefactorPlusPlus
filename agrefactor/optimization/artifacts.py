"""Small, deterministic S3.3 lineage and decision artifact store."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .state import HypothesisRecord


ARTIFACT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class OptimizerDecisionRecord:
    sequence: int
    event: str
    level: str | None
    round_number: int | None
    candidate_id: str | None
    hypothesis_id: str | None
    action: str
    reason: str
    metadata: Mapping[str, Any]
    timestamp_utc: str

    schema_version = ARTIFACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or self.sequence < 1:
            raise ValueError("decision sequence must be positive")
        for name in ("event", "action", "reason", "timestamp_utc"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")
        if self.round_number is not None and (
            isinstance(self.round_number, bool) or self.round_number < 1
        ):
            raise ValueError("round_number must be positive or null")
        clean_metadata = _json_copy(self.metadata)
        _reject_unsafe_keys(clean_metadata)
        object.__setattr__(self, "metadata", clean_metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "sequence": self.sequence,
            "event": self.event,
            "level": self.level,
            "round_number": self.round_number,
            "candidate_id": self.candidate_id,
            "hypothesis_id": self.hypothesis_id,
            "action": self.action,
            "reason": self.reason,
            "metadata": _json_copy(self.metadata),
            "timestamp_utc": self.timestamp_utc,
        }


class OptimizerArtifactStore:
    """Persist only artifacts with an immediate S3.3 consumer."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        path = Path(root)
        if path.exists() and path.is_symlink():
            raise ValueError("artifact root must not be a symbolic link")
        path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            raise ValueError("artifact root must be a directory")
        self._root = path.resolve()
        self._decision_path = self._root / "decisions.jsonl"
        self._next_sequence = self._read_next_sequence()

    @property
    def root(self) -> Path:
        return self._root

    @property
    def decision_path(self) -> Path:
        return self._decision_path

    def write_hypothesis(self, hypothesis: HypothesisRecord) -> Path:
        if not isinstance(hypothesis, HypothesisRecord):
            raise TypeError("hypothesis must be HypothesisRecord")
        directory = self._root / "hypotheses"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{hypothesis.hypothesis_id}.json"
        data = _json_bytes(hypothesis.to_dict())
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise ValueError("hypothesis artifact must be a regular file")
            if path.read_bytes() != data:
                raise FileExistsError(
                    "hypothesis artifact exists with different content"
                )
            return path
        _atomic_write(path, data, overwrite=False)
        return path

    def append_decision(
        self,
        *,
        event: str,
        level: str | None,
        round_number: int | None,
        candidate_id: str | None,
        hypothesis_id: str | None,
        action: str,
        reason: str,
        metadata: Mapping[str, Any] | None,
        timestamp_utc: str,
    ) -> OptimizerDecisionRecord:
        record = OptimizerDecisionRecord(
            sequence=self._next_sequence,
            event=event,
            level=level,
            round_number=round_number,
            candidate_id=candidate_id,
            hypothesis_id=hypothesis_id,
            action=action,
            reason=reason,
            metadata={} if metadata is None else metadata,
            timestamp_utc=timestamp_utc,
        )
        path = self._decision_path
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise ValueError("decisions.jsonl must be a regular file")
        line = json.dumps(
            record.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8") + b"\n"
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        try:
            os.write(descriptor, line)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._next_sequence += 1
        return record

    def _read_next_sequence(self) -> int:
        path = self._decision_path
        if not path.exists():
            return 1
        if path.is_symlink() or not path.is_file():
            raise ValueError("decisions.jsonl must be a regular file")
        expected = 1
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"decisions.jsonl line {line_number} is invalid JSON"
                ) from exc
            if payload.get("sequence") != expected:
                raise ValueError("decision sequences must be contiguous")
            expected += 1
        return expected


def _json_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(json.dumps(dict(value), ensure_ascii=False, sort_keys=True))
    except (TypeError, ValueError) as exc:
        raise TypeError("artifact metadata must be JSON-serializable") from exc


def _reject_unsafe_keys(value: Any, path: str = "metadata") -> None:
    forbidden = {
        "hidden",
        "hidden_diagnostic",
        "hidden_report",
        "operator_full",
        "private_testbench",
        "secret",
        "api_key",
        "token",
        "password",
    }
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in forbidden or normalized.startswith("hidden_"):
                raise ValueError(f"{path} contains unsafe key: {key}")
            _reject_unsafe_keys(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_unsafe_keys(item, f"{path}[{index}]")


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            dict(value), ensure_ascii=False, sort_keys=True, indent=2
        )
        + "\n"
    ).encode("utf-8")


def _atomic_write(path: Path, data: bytes, *, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise ValueError("artifact parent must not be a symbolic link")
    if not overwrite and path.exists():
        raise FileExistsError(path)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = ""
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
