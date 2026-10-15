"""Atomic storage for diagnostic cases, safe events, and change artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile
from threading import RLock
from typing import Any
from hashlib import sha256

from .case_record import DiagnosticCase, ExperienceRecord


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _atomic_write(path: Path, data: bytes, *, create_only: bool = False) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_bytes()
        if existing == data:
            return False
        if create_only:
            raise FileExistsError(f"conflicting artifact already exists: {path.name}")
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if create_only:
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() == data:
                    return False
                raise FileExistsError(f"conflicting artifact already exists: {path.name}")
        else:
            os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return True
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@dataclass(frozen=True, slots=True)
class StoredChange:
    attempt_id: str
    components: tuple[str, ...]
    before_sha256: str
    after_sha256: str
    change_fingerprint: str
    patch: str

    def __post_init__(self) -> None:
        if not self.attempt_id or any(char in self.attempt_id for char in "/\\\x00"):
            raise ValueError("attempt_id must be a stable file-safe identifier")
        if not self.components or any(not item for item in self.components):
            raise ValueError("components must not be empty")
        for name in ("before_sha256", "after_sha256", "change_fingerprint"):
            value = getattr(self, name)
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        if not isinstance(self.patch, str) or not self.patch or len(self.patch.encode("utf-8")) > 16384:
            raise ValueError("patch must be non-empty and bounded")
        if sha256(self.patch.encode("utf-8")).hexdigest() != self.change_fingerprint:
            raise ValueError("change_fingerprint must match patch content")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "attempt_id": self.attempt_id,
            "components": list(self.components),
            "before_sha256": self.before_sha256,
            "after_sha256": self.after_sha256,
            "change_fingerprint": self.change_fingerprint,
            "patch": self.patch,
            "evidence_view": "agent_safe",
            "hidden_content_persisted": False,
        }


class CaseStore:
    """Persist one root-run's cases without copying source files or prompts."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root)
        self._lock = RLock()

    @property
    def root(self) -> Path:
        return self._root

    def write_event(self, case_id: str, event_id: str, value: Mapping[str, Any]) -> bool:
        if not isinstance(value, Mapping) or value.get("event_id") != event_id:
            raise ValueError("event payload must match its stable event ID")
        if value.get("evidence_view") != "agent_safe":
            raise ValueError("only agent_safe events can be stored in a case")
        stage = str(value.get("stage", "")).casefold()
        if stage in {"hidden", "hidden_evaluation", "hidden-generation"}:
            raise ValueError("Hidden events cannot be stored in a case")
        path = self._root / case_id / "events" / f"{event_id}.json"
        with self._lock:
            return _atomic_write(path, _canonical(dict(value)), create_only=True)

    def write_decision(
        self, case_id: str, decision_ref: str, value: Mapping[str, Any]
    ) -> bool:
        if not isinstance(decision_ref, str) or re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", decision_ref
        ) is None:
            raise ValueError("decision_ref must be a stable file-safe identifier")
        if not isinstance(value, Mapping) or value.get("decision_ref") != decision_ref:
            raise ValueError("decision payload must match its stable reference")
        if value.get("evidence_view") != "agent_safe":
            raise ValueError("only agent_safe decisions can be stored in a case")
        stage = str(value.get("stage", "")).casefold()
        if "hidden" in stage:
            raise ValueError("Hidden decisions cannot be stored in a case")
        path = self._root / case_id / "decisions" / f"{decision_ref}.json"
        with self._lock:
            return _atomic_write(path, _canonical(dict(value)), create_only=True)

    def read_decision(self, case_id: str, decision_ref: str) -> dict[str, Any]:
        if not isinstance(decision_ref, str) or re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", decision_ref
        ) is None:
            raise ValueError("decision_ref must be a stable file-safe identifier")
        path = self._root / case_id / "decisions" / f"{decision_ref}.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("decision_ref") != decision_ref:
            raise ValueError("stored decision reference does not match its path")
        return value

    def write_change(self, case_id: str, change: StoredChange) -> bool:
        if not isinstance(change, StoredChange):
            raise TypeError("change must be StoredChange")
        path = self._root / case_id / "changes" / f"{change.attempt_id}.json"
        with self._lock:
            return _atomic_write(path, _canonical(change.to_dict()), create_only=True)

    def read_changes(self, case_id: str) -> dict[str, StoredChange]:
        result = {}
        for path in sorted((self._root / case_id / "changes").glob("*.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            change = StoredChange(
                attempt_id=value["attempt_id"],
                components=tuple(value["components"]),
                before_sha256=value["before_sha256"],
                after_sha256=value["after_sha256"],
                change_fingerprint=value["change_fingerprint"],
                patch=value["patch"],
            )
            result[change.attempt_id] = change
        return result

    def read_events(self, case_id: str) -> dict[str, dict[str, Any]]:
        result = {}
        for path in sorted((self._root / case_id / "events").glob("*.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            result[value["event_id"]] = value
        return result

    def write_case(self, case: DiagnosticCase) -> bool:
        if not isinstance(case, DiagnosticCase):
            raise TypeError("case must be DiagnosticCase")
        path = self._root / case.case_id / "case.json"
        with self._lock:
            return _atomic_write(path, _canonical(case.to_dict()), create_only=True)

    def update_case(self, case: DiagnosticCase) -> bool:
        if not isinstance(case, DiagnosticCase):
            raise TypeError("case must be DiagnosticCase")
        path = self._root / case.case_id / "case.json"
        with self._lock:
            return _atomic_write(path, _canonical(case.to_dict()))

    def read_case(self, case_id: str) -> DiagnosticCase:
        path = self._root / case_id / "case.json"
        return DiagnosticCase.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def open_cases(self) -> tuple[DiagnosticCase, ...]:
        cases = []
        for path in sorted(self._root.glob("*/case.json")):
            case = DiagnosticCase.from_dict(json.loads(path.read_text(encoding="utf-8")))
            if case.status.value == "open":
                cases.append(case)
        return tuple(cases)


class ExperienceStore:
    """Append-only, idempotent store for derived experience records."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root)
        self._lock = RLock()

    def write(self, record: ExperienceRecord, change: StoredChange | None) -> bool:
        if not isinstance(record, ExperienceRecord):
            raise TypeError("record must be ExperienceRecord")
        record_path = self._root / "records" / f"{record.experience_id}.json"
        with self._lock:
            if change is not None:
                change_path = self._root / "changes" / f"{change.change_fingerprint}.json"
                shared_change = {
                    "schema_version": 1,
                    "components": list(change.components),
                    "before_sha256": change.before_sha256,
                    "after_sha256": change.after_sha256,
                    "change_fingerprint": change.change_fingerprint,
                    "patch": change.patch,
                    "evidence_view": "agent_safe",
                    "hidden_content_persisted": False,
                }
                _atomic_write(change_path, _canonical(shared_change), create_only=True)
            return _atomic_write(record_path, _canonical(record.to_dict()), create_only=True)
