"""Atomic, recoverable checkpoints for the Stage 3 optimizer foundation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from agrefactor.runtime.execution_identity import (
    canonical_json_sha256,
    file_sha256,
)

from .state import (
    SCHEMA_VERSION,
    CandidateRecord,
    OptimizerState,
    candidate_index_from_dict,
    candidate_index_to_dict,
    normalize_candidate_index,
)


_CHECKPOINT_RE = re.compile(r"^checkpoint-([0-9]{4,})\.json$")


@dataclass(frozen=True, slots=True)
class OptimizerCheckpointSnapshot:
    """One validated immutable checkpoint and its typed records."""

    state: OptimizerState
    candidates: Mapping[str, CandidateRecord]
    checkpoint_path: Path

    def __post_init__(self) -> None:
        if not isinstance(self.state, OptimizerState):
            raise TypeError("state must be an OptimizerState")
        normalized = normalize_candidate_index(self.candidates)
        self.state.validate_against_candidates(normalized)
        path = Path(self.checkpoint_path)
        object.__setattr__(self, "candidates", normalized)
        object.__setattr__(self, "checkpoint_path", path)


class OptimizerCheckpointWriter:
    """Write projections atomically and commit immutable checkpoint markers last."""

    schema_version = SCHEMA_VERSION

    def __init__(
        self,
        optimizer_root: str | os.PathLike[str],
        *,
        before_write: Callable[[str, Path], None] | None = None,
    ) -> None:
        self._root = _prepare_root(optimizer_root)
        if before_write is not None and not callable(before_write):
            raise TypeError("before_write must be callable or None")
        self._before_write = before_write

    @property
    def root(self) -> Path:
        return self._root

    def write_candidate_source(
        self,
        candidate: CandidateRecord,
        source: str | bytes,
    ) -> Path:
        """Create one immutable candidate source artifact after hash validation."""

        if not isinstance(candidate, CandidateRecord):
            raise TypeError("candidate must be a CandidateRecord")
        data = source.encode("utf-8") if isinstance(source, str) else source
        if not isinstance(data, bytes):
            raise TypeError("source must be str or bytes")
        from hashlib import sha256

        actual = sha256(data).hexdigest()
        if actual != candidate.source_sha256:
            raise ValueError("source bytes do not match candidate source_sha256")
        path = _resolve_member(
            self._root,
            candidate.source_artifact,
            require_file=False,
        )
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise ValueError("candidate source path is not a regular file")
            if file_sha256(path) != candidate.source_sha256:
                raise FileExistsError(
                    "candidate source artifact exists with different content"
                )
            return path
        self._invoke_hook("candidate_source", path)
        _atomic_write_bytes(path, data, overwrite=False)
        return path

    def write_checkpoint(
        self,
        state: OptimizerState,
        candidates: Mapping[str, CandidateRecord],
    ) -> OptimizerCheckpointSnapshot:
        """Write the next monotonic checkpoint and return its persisted state."""

        if not isinstance(state, OptimizerState):
            raise TypeError("state must be an OptimizerState")
        index = normalize_candidate_index(candidates)
        state.validate_against_candidates(index)

        latest = self.load_latest(required=False)
        latest_sequence = 0 if latest is None else latest.state.checkpoint_sequence
        if state.checkpoint_sequence != latest_sequence:
            raise ValueError(
                "state checkpoint_sequence is stale or skips the latest checkpoint"
            )
        if latest is not None:
            if state.run_id != latest.state.run_id:
                raise ValueError("run_id cannot change across checkpoints")
            if (
                latest.state.best_ppa_candidate_id is not None
                and state.best_ppa_candidate_id is None
            ):
                raise ValueError("best_ppa cannot be cleared by a later checkpoint")

        next_state = state.with_checkpoint_sequence(latest_sequence + 1)
        next_state.validate_against_candidates(index)
        self._verify_candidate_sources(index)

        state_payload = next_state.to_dict()
        candidate_payload = candidate_index_to_dict(index)
        checkpoint_payload = {
            "schema_version": self.schema_version,
            "checkpoint_sequence": next_state.checkpoint_sequence,
            "previous_checkpoint_sequence": latest_sequence,
            "state_sha256": canonical_json_sha256(state_payload),
            "candidate_index_sha256": canonical_json_sha256(candidate_payload),
            "best_correct": self._best_source_identity(
                next_state.best_correct_candidate_id,
                index,
            ),
            "best_ppa": self._best_source_identity(
                next_state.best_ppa_candidate_id,
                index,
            ),
            "state": state_payload,
            "candidate_index": candidate_payload,
        }

        state_path = _resolve_member(
            self._root,
            "state.json",
            require_file=False,
        )
        index_path = _resolve_member(
            self._root,
            "candidate_index.json",
            require_file=False,
        )
        best_correct_path = _resolve_member(
            self._root,
            "best_correct.cpp",
            require_file=False,
        )
        best_ppa_path = _resolve_member(
            self._root,
            "best_ppa.cpp",
            require_file=False,
        )
        checkpoint_path = _resolve_member(
            self._root,
            (
                "checkpoints/checkpoint-"
                f"{next_state.checkpoint_sequence:04d}.json"
            ),
            require_file=False,
        )
        if checkpoint_path.exists():
            raise FileExistsError(
                f"checkpoint already exists: {checkpoint_path}"
            )

        # Projections may be partially updated if a write fails. They are never
        # authoritative: the immutable checkpoint marker is written last, and
        # recover_latest() rebuilds every projection from the newest valid marker.
        self._write_json_projection("candidate_index", index_path, candidate_payload)
        self._write_best_projection(
            "best_correct",
            best_correct_path,
            next_state.best_correct_candidate_id,
            index,
        )
        if next_state.best_ppa_candidate_id is None:
            _safe_unlink_projection(self._root, best_ppa_path)
        else:
            self._write_best_projection(
                "best_ppa",
                best_ppa_path,
                next_state.best_ppa_candidate_id,
                index,
            )
        self._write_json_projection("state", state_path, state_payload)

        self._invoke_hook("checkpoint", checkpoint_path)
        _atomic_write_bytes(
            checkpoint_path,
            _json_bytes(checkpoint_payload),
            overwrite=False,
        )
        return OptimizerCheckpointSnapshot(
            state=next_state,
            candidates=index,
            checkpoint_path=checkpoint_path,
        )

    def load_latest(
        self,
        *,
        required: bool = True,
    ) -> OptimizerCheckpointSnapshot | None:
        """Load the newest complete checkpoint, falling back past corrupt files."""

        checkpoint_dir = _resolve_member(
            self._root,
            "checkpoints",
            require_file=False,
        )
        if not checkpoint_dir.exists():
            if required:
                raise FileNotFoundError("no optimizer checkpoint exists")
            return None
        if checkpoint_dir.is_symlink() or not checkpoint_dir.is_dir():
            raise ValueError("checkpoints path must be a real directory")

        candidates: list[tuple[int, Path]] = []
        for path in checkpoint_dir.iterdir():
            if path.is_symlink() or not path.is_file():
                continue
            match = _CHECKPOINT_RE.fullmatch(path.name)
            if match is not None:
                candidates.append((int(match.group(1)), path))
        if not candidates:
            if required:
                raise FileNotFoundError("no optimizer checkpoint exists")
            return None

        errors: list[str] = []
        for sequence, path in sorted(candidates, reverse=True):
            try:
                return self._load_checkpoint(path, sequence)
            except Exception as exc:  # noqa: BLE001 - fallback is the contract.
                errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
        raise RuntimeError(
            "no complete optimizer checkpoint could be recovered; "
            + " | ".join(errors)
        )

    def recover_latest(self) -> OptimizerCheckpointSnapshot:
        """Restore mutable projections from the newest complete checkpoint."""

        snapshot = self.load_latest(required=True)
        assert snapshot is not None
        state_payload = snapshot.state.to_dict()
        candidate_payload = candidate_index_to_dict(snapshot.candidates)
        self._write_json_projection(
            "recover_candidate_index",
            _resolve_member(
                self._root,
                "candidate_index.json",
                require_file=False,
            ),
            candidate_payload,
        )
        self._write_best_projection(
            "recover_best_correct",
            _resolve_member(
                self._root,
                "best_correct.cpp",
                require_file=False,
            ),
            snapshot.state.best_correct_candidate_id,
            snapshot.candidates,
        )
        best_ppa_path = _resolve_member(
            self._root,
            "best_ppa.cpp",
            require_file=False,
        )
        if snapshot.state.best_ppa_candidate_id is None:
            _safe_unlink_projection(self._root, best_ppa_path)
        else:
            self._write_best_projection(
                "recover_best_ppa",
                best_ppa_path,
                snapshot.state.best_ppa_candidate_id,
                snapshot.candidates,
            )
        self._write_json_projection(
            "recover_state",
            _resolve_member(
                self._root,
                "state.json",
                require_file=False,
            ),
            state_payload,
        )
        return snapshot

    def _load_checkpoint(
        self,
        path: Path,
        filename_sequence: int,
    ) -> OptimizerCheckpointSnapshot:
        if path.is_symlink():
            raise ValueError("checkpoint must not be a symbolic link")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("checkpoint is not readable JSON") from exc
        if not isinstance(raw, Mapping):
            raise TypeError("checkpoint must contain a JSON object")
        allowed = {
            "schema_version",
            "checkpoint_sequence",
            "previous_checkpoint_sequence",
            "state_sha256",
            "candidate_index_sha256",
            "best_correct",
            "best_ppa",
            "state",
            "candidate_index",
        }
        unknown = set(raw) - allowed
        missing = allowed - set(raw)
        if unknown or missing:
            raise ValueError(
                "checkpoint fields mismatch: "
                f"unknown={sorted(unknown)} missing={sorted(missing)}"
            )
        if raw["schema_version"] != self.schema_version:
            raise ValueError("unsupported checkpoint schema_version")
        if raw["checkpoint_sequence"] != filename_sequence:
            raise ValueError("checkpoint filename and sequence disagree")
        if raw["previous_checkpoint_sequence"] != filename_sequence - 1:
            raise ValueError("checkpoint previous sequence is not monotonic")
        state_payload = raw["state"]
        candidate_payload = raw["candidate_index"]
        if canonical_json_sha256(state_payload) != raw["state_sha256"]:
            raise ValueError("checkpoint state hash does not match")
        if (
            canonical_json_sha256(candidate_payload)
            != raw["candidate_index_sha256"]
        ):
            raise ValueError("checkpoint candidate index hash does not match")

        state = OptimizerState.from_dict(state_payload)
        index = candidate_index_from_dict(candidate_payload)
        if state.checkpoint_sequence != filename_sequence:
            raise ValueError("typed state checkpoint sequence does not match")
        state.validate_against_candidates(index)
        self._verify_candidate_sources(index)
        if raw["best_correct"] != self._best_source_identity(
            state.best_correct_candidate_id,
            index,
        ):
            raise ValueError("checkpoint best_correct identity does not match")
        if raw["best_ppa"] != self._best_source_identity(
            state.best_ppa_candidate_id,
            index,
        ):
            raise ValueError("checkpoint best_ppa identity does not match")
        return OptimizerCheckpointSnapshot(
            state=state,
            candidates=index,
            checkpoint_path=path,
        )

    def _verify_candidate_sources(
        self,
        candidates: Mapping[str, CandidateRecord],
    ) -> None:
        for record in candidates.values():
            path = _resolve_member(
                self._root,
                record.source_artifact,
                require_file=True,
            )
            if file_sha256(path) != record.source_sha256:
                raise ValueError(
                    f"candidate source SHA-256 mismatch: {record.candidate_id}"
                )

    def _best_source_identity(
        self,
        candidate_id: str | None,
        candidates: Mapping[str, CandidateRecord],
    ) -> dict[str, Any] | None:
        if candidate_id is None:
            return None
        record = candidates[candidate_id]
        return {
            "candidate_id": record.candidate_id,
            "source_artifact": record.source_artifact,
            "source_sha256": record.source_sha256,
        }

    def _write_json_projection(
        self,
        label: str,
        path: Path,
        payload: Mapping[str, Any],
    ) -> None:
        self._invoke_hook(label, path)
        _atomic_write_bytes(path, _json_bytes(payload), overwrite=True)

    def _write_best_projection(
        self,
        label: str,
        path: Path,
        candidate_id: str | None,
        candidates: Mapping[str, CandidateRecord],
    ) -> None:
        if candidate_id is None:
            raise ValueError(f"{label} candidate id must not be empty")
        record = candidates[candidate_id]
        source_path = _resolve_member(
            self._root,
            record.source_artifact,
            require_file=True,
        )
        data = source_path.read_bytes()
        self._invoke_hook(label, path)
        _atomic_write_bytes(path, data, overwrite=True)

    def _invoke_hook(self, label: str, path: Path) -> None:
        if self._before_write is not None:
            self._before_write(label, path)


def _prepare_root(value: str | os.PathLike[str]) -> Path:
    try:
        raw = os.fspath(value)
    except TypeError as exc:
        raise TypeError("optimizer_root must be path-like") from exc
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("optimizer_root must not be empty")
    root = Path(raw.strip()).expanduser()
    if root.exists() and root.is_symlink():
        raise ValueError("optimizer_root must not be a symbolic link")
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise ValueError("optimizer_root must be a directory")
    return root.resolve()


def _resolve_member(
    root: Path,
    relative: str,
    *,
    require_file: bool,
) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError("artifact member path must not be empty")
    candidate = root.joinpath(*relative.split("/"))
    current = root
    for part in relative.split("/")[:-1]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError("artifact path traverses a symbolic link")
    resolved_parent = candidate.parent.resolve()
    if not resolved_parent.is_relative_to(root):
        raise ValueError("artifact path escapes optimizer_root")
    if candidate.exists() and candidate.is_symlink():
        raise ValueError("artifact path must not be a symbolic link")
    if require_file and not candidate.is_file():
        raise FileNotFoundError(f"optimizer artifact not found: {candidate}")
    return candidate


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    overwrite: bool,
) -> None:
    if not isinstance(data, bytes):
        raise TypeError("atomic payload must be bytes")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise ValueError("atomic write parent must not be a symbolic link")
    if not overwrite and path.exists():
        raise FileExistsError(f"artifact already exists: {path}")
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    try:
        if not overwrite and path.exists():
            raise FileExistsError(f"artifact already exists: {path}")
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _safe_unlink_projection(root: Path, path: Path) -> None:
    resolved_parent = path.parent.resolve()
    if not resolved_parent.is_relative_to(root):
        raise ValueError("projection path escapes optimizer_root")
    if path.is_symlink():
        raise ValueError("projection must not be a symbolic link")
    if path.exists():
        if not path.is_file():
            raise ValueError("projection path must be a regular file")
        path.unlink()
        _fsync_directory(path.parent)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
