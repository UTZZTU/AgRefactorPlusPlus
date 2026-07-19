"""Atomic writer for versioned, agent-safe repair artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .protocol import (
    REPAIR_PROTOCOL_SCHEMA_VERSION,
    RepairRunRecord,
)


@dataclass(frozen=True, slots=True)
class RepairArtifactFile:
    relative_path: str
    sha256: str
    size_bytes: int
    record_type: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "record_type": self.record_type,
        }


@dataclass(frozen=True, slots=True)
class RepairArtifactWriteResult:
    root: str
    run_record_path: str
    attempt_paths: tuple[str, ...]
    artifact_manifest_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "run_record_path": self.run_record_path,
            "attempt_paths": list(self.attempt_paths),
            "artifact_manifest_path": (
                self.artifact_manifest_path
            ),
        }


class RepairArtifactWriter:
    """Write one immutable repair artifact bundle."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        try:
            raw = os.fspath(root)
        except TypeError as exc:
            raise TypeError(
                "root must be path-like"
            ) from exc
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("root must not be empty")
        self._root = Path(raw)

    @property
    def root(self) -> Path:
        return self._root

    def write(
        self,
        run: RepairRunRecord,
    ) -> RepairArtifactWriteResult:
        if not isinstance(run, RepairRunRecord):
            raise TypeError(
                "run must be RepairRunRecord"
            )
        if self._root.exists():
            if not self._root.is_dir():
                raise FileExistsError(
                    f"repair artifact root is not a directory: {self._root}"
                )
            if any(self._root.iterdir()):
                raise FileExistsError(
                    f"repair artifact root is not empty: {self._root}"
                )
        self._root.mkdir(
            parents=True,
            exist_ok=True,
        )
        attempts_root = self._root / "attempts"
        attempts_root.mkdir(
            parents=True,
            exist_ok=True,
        )

        files: list[RepairArtifactFile] = []
        attempt_paths: list[str] = []

        for attempt in run.attempts:
            relative = (
                Path("attempts")
                / f"attempt_{attempt.sequence_index:03d}.json"
            )
            path = self._root / relative
            self._atomic_json_write(
                path,
                attempt.to_safe_dict(),
            )
            attempt_paths.append(str(path))
            files.append(
                self._file_record(
                    path,
                    relative,
                    "repair_attempt",
                )
            )

        run_path = self._root / "repair_run.json"
        self._atomic_json_write(
            run_path,
            run.to_safe_dict(),
        )
        files.append(
            self._file_record(
                run_path,
                Path("repair_run.json"),
                "repair_run",
            )
        )

        manifest = {
            "schema_version": (
                REPAIR_PROTOCOL_SCHEMA_VERSION
            ),
            "run_id": run.run_id,
            "artifact_role": run.artifact_role.value,
            "terminal_status": (
                run.terminal_status.value
            ),
            "stop_reason": run.stop_reason,
            "evidence_view": "agent_safe",
            "files": [
                item.to_dict()
                for item in sorted(
                    files,
                    key=lambda item: item.relative_path,
                )
            ],
        }
        manifest_path = (
            self._root / "artifact_manifest.json"
        )
        self._atomic_json_write(
            manifest_path,
            manifest,
        )

        leftovers = tuple(
            self._root.rglob("*.tmp")
        )
        if leftovers:
            raise RuntimeError(
                "temporary repair artifact files remain"
            )

        return RepairArtifactWriteResult(
            root=str(self._root),
            run_record_path=str(run_path),
            attempt_paths=tuple(attempt_paths),
            artifact_manifest_path=str(manifest_path),
        )

    @staticmethod
    def _atomic_json_write(
        path: Path,
        payload: dict[str, Any],
    ) -> None:
        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            try:
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                    allow_nan=False,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            except Exception:
                temporary.unlink(
                    missing_ok=True
                )
                raise
        try:
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    def _file_record(
        path: Path,
        relative: Path,
        record_type: str,
    ) -> RepairArtifactFile:
        data = path.read_bytes()
        return RepairArtifactFile(
            relative_path=relative.as_posix(),
            sha256=sha256(data).hexdigest(),
            size_bytes=len(data),
            record_type=record_type,
        )
