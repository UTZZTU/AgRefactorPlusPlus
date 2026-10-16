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
    source_attempt_ids: tuple[str, ...] = ()

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
        source_attempt_ids = self.source_attempt_ids or (self.attempt_id,)
        if (
            any(
                not item or any(char in item for char in "/\\\x00")
                for item in source_attempt_ids
            )
            or self.attempt_id not in source_attempt_ids
            or len(set(source_attempt_ids)) != len(source_attempt_ids)
        ):
            raise ValueError(
                "source_attempt_ids must identify this change and be unique"
            )
        object.__setattr__(self, "source_attempt_ids", tuple(source_attempt_ids))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "attempt_id": self.attempt_id,
            "components": list(self.components),
            "before_sha256": self.before_sha256,
            "after_sha256": self.after_sha256,
            "change_fingerprint": self.change_fingerprint,
            "patch": self.patch,
            "source_attempt_ids": list(self.source_attempt_ids),
            "evidence_view": "agent_safe",
            "hidden_content_persisted": False,
        }


@dataclass(frozen=True, slots=True)
class StoredExperience:
    record: ExperienceRecord
    change: StoredChange


@dataclass(frozen=True, slots=True)
class ExperienceSnapshot:
    revision: str
    experiences: tuple[StoredExperience, ...]
    excluded: tuple[tuple[str, str], ...]


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
                source_attempt_ids=tuple(
                    value.get("source_attempt_ids", ())
                ),
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
        if (
            change is not None
            and tuple(record.source_attempt_ids)
            != tuple(change.source_attempt_ids)
        ):
            raise ValueError(
                "experience attempts must match the stored change chain"
            )
        record_path = self._root / "records" / f"{record.experience_id}.json"
        with self._lock:
            if change is not None:
                change_path = self._root / "changes" / f"{change.change_fingerprint}.json"
                shared_change = {
                    "schema_version": 1,
                    "attempt_id": change.attempt_id,
                    "components": list(change.components),
                    "before_sha256": change.before_sha256,
                    "after_sha256": change.after_sha256,
                    "change_fingerprint": change.change_fingerprint,
                    "patch": change.patch,
                    "source_attempt_ids": list(change.source_attempt_ids),
                    "evidence_view": "agent_safe",
                    "hidden_content_persisted": False,
                }
                _atomic_write(change_path, _canonical(shared_change), create_only=True)
            return _atomic_write(record_path, _canonical(record.to_dict()), create_only=True)

    def read_snapshot(self) -> ExperienceSnapshot:
        """Read a stable, bounded snapshot; malformed entries are isolated."""
        root = self._root.resolve()
        records_dir = self._root / "records"
        changes_dir = self._root / "changes"
        candidates: list[tuple[str, Path]] = []
        for directory in (records_dir, changes_dir):
            if directory.exists():
                try:
                    directory.resolve(strict=True).relative_to(root)
                except (OSError, ValueError):
                    return ExperienceSnapshot(
                        revision=sha256(b"unsafe_store_root").hexdigest(),
                        experiences=(),
                        excluded=(("store", "unsafe_store_root"),),
                    )
                candidates.extend(
                    (directory.name, path)
                    for path in sorted(directory.glob("*.json"), key=lambda item: item.name)
                )

        file_hashes: list[str] = []
        loaded_records: list[tuple[str, Path, bytes]] = []
        excluded: list[tuple[str, str]] = []
        changes: dict[str, tuple[Mapping[str, Any], str]] = {}
        max_entries = 4096
        max_total_bytes = 64 * 1024 * 1024
        if len(candidates) > max_entries:
            candidates = candidates[:max_entries]
            excluded.append(("store", "entry_limit_exceeded"))
        total_bytes = 0
        for kind, path in candidates:
            identifier = path.stem
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(root)
                if not resolved.is_file():
                    raise ValueError("not_regular_file")
                data = resolved.read_bytes()
                max_bytes = 20 * 1024 if kind == "changes" else 128 * 1024
                if len(data) > max_bytes:
                    raise ValueError("artifact_too_large")
                total_bytes += len(data)
                if total_bytes > max_total_bytes:
                    excluded.append(("store", "byte_limit_exceeded"))
                    break
            except (OSError, ValueError):
                excluded.append((identifier, "unsafe_or_unreadable_artifact"))
                file_hashes.append(f"{kind}/{path.name}:unreadable")
                continue
            digest = sha256(data).hexdigest()
            file_hashes.append(f"{kind}/{path.name}:{digest}")
            if kind == "changes":
                try:
                    value = json.loads(data)
                    if not isinstance(value, Mapping):
                        raise ValueError("change_not_object")
                    changes[identifier] = (value, digest)
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    excluded.append((identifier, "change_invalid"))
            else:
                loaded_records.append((identifier, path, data))

        revision = sha256("\n".join(file_hashes).encode("utf-8")).hexdigest()
        experiences: list[StoredExperience] = []
        for identifier, _path, data in loaded_records:
            try:
                value = json.loads(data)
                if not isinstance(value, Mapping):
                    raise ValueError("record_not_object")
                record = ExperienceRecord.from_dict(value)
                if record.experience_id != identifier:
                    raise ValueError("record_identity_mismatch")
                if record.schema_version != 2:
                    excluded.append((identifier, "retrieval_fields_missing"))
                    continue
                if not record.source_attempt_ids or record.change_fingerprint is None:
                    excluded.append((identifier, "change_reference_incomplete"))
                    continue
                stored = changes.get(record.change_fingerprint)
                if stored is None:
                    excluded.append((identifier, "change_missing"))
                    continue
                change_data, _change_digest = stored
                change_attempt_id = change_data.get("attempt_id")
                source_attempt_ids = tuple(
                    change_data.get("source_attempt_ids", ())
                )
                if not source_attempt_ids:
                    source_attempt_ids = (
                        str(change_attempt_id or record.source_attempt_ids[0]),
                    )
                change_attempt_id = change_attempt_id or source_attempt_ids[-1]
                linked_ref = f"change:{change_attempt_id}"
                if (
                    change_data.get("change_fingerprint") != record.change_fingerprint
                    or change_data.get("evidence_view") != "agent_safe"
                    or change_data.get("hidden_content_persisted") is not False
                    or not isinstance(change_attempt_id, str)
                    or change_attempt_id not in record.source_attempt_ids
                    or source_attempt_ids != record.source_attempt_ids
                    or linked_ref not in record.evidence_refs
                ):
                    excluded.append((identifier, "change_identity_or_visibility_invalid"))
                    continue
                change = StoredChange(
                    attempt_id=str(change_attempt_id or source_attempt_ids[-1]),
                    components=tuple(change_data["components"]),
                    before_sha256=change_data["before_sha256"],
                    after_sha256=change_data["after_sha256"],
                    change_fingerprint=change_data["change_fingerprint"],
                    patch=change_data["patch"],
                    source_attempt_ids=source_attempt_ids,
                )
                if change.components != record.components:
                    excluded.append((identifier, "component_reference_mismatch"))
                    continue
                experiences.append(StoredExperience(record=record, change=change))
            except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
                excluded.append((identifier, "record_invalid"))
        experiences.sort(key=lambda item: item.record.experience_id)
        excluded.sort()
        return ExperienceSnapshot(
            revision=revision,
            experiences=tuple(experiences),
            excluded=tuple(excluded),
        )
