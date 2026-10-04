"""Typed source-package metadata for multi-file HLS examples.

The package preserves relative paths for the real compiler while keeping one
target source as the only model-editable file.  It deliberately does not infer
roles from file names or C/C++ text.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil


_HEADER_SUFFIXES = frozenset({".h", ".hh", ".hpp", ".hxx"})
_SOURCE_SUFFIXES = frozenset({".c", ".cc", ".cpp", ".cxx"})


@dataclass(frozen=True, slots=True)
class SourcePackageSpec:
    """Describe a source root and explicit independent source files."""

    root: Path
    target: Path
    extra_sources: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        root = Path(self.root).expanduser().resolve()
        target = Path(self.target).expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"source package root not found: {root}")
        if not target.is_file():
            raise FileNotFoundError(f"source package target not found: {target}")
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError("source package target must be inside root") from exc
        extras: list[Path] = []
        seen: set[Path] = set()
        for raw in self.extra_sources:
            path = Path(raw).expanduser().resolve()
            if not path.is_file():
                raise FileNotFoundError(f"extra source not found: {path}")
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ValueError("extra source must be inside root") from exc
            if path == target:
                raise ValueError("target source cannot also be an extra source")
            if path not in seen:
                seen.add(path)
                extras.append(path)
        object.__setattr__(self, "root", root)
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "extra_sources", tuple(extras))

    @property
    def target_relative(self) -> Path:
        return self.target.relative_to(self.root)

    @property
    def extra_relative(self) -> tuple[Path, ...]:
        return tuple(path.relative_to(self.root) for path in self.extra_sources)

    def to_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "target": str(self.target_relative),
            "extra_sources": [str(path) for path in self.extra_relative],
        }

    @classmethod
    def from_dict(cls, data: object) -> "SourcePackageSpec":
        if not isinstance(data, dict):
            raise TypeError("source_package must be an object")
        root = data.get("root")
        target = data.get("target")
        extras = data.get("extra_sources", ())
        if not isinstance(root, str) or not isinstance(target, str):
            raise TypeError("source_package.root and target must be strings")
        if not isinstance(extras, (list, tuple)) or not all(
            isinstance(item, str) for item in extras
        ):
            raise TypeError("source_package.extra_sources must be strings")
        root_path = Path(root).expanduser().resolve()
        return cls(
            root=root_path,
            target=root_path / target,
            extra_sources=tuple(root_path / item for item in extras),
        )

    def stage_into(self, work_dir: Path) -> None:
        """Copy the package into an isolated work directory."""
        destination = Path(work_dir).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        for source in self.root.rglob("*"):
            if source.is_symlink() or not source.is_file():
                continue
            relative = source.relative_to(self.root)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def context_files(self, *, max_bytes: int = 64 * 1024) -> tuple[tuple[str, str], ...]:
        """Return bounded read-only source context for model prompts."""
        items: list[tuple[str, str]] = []
        extra_sources = set(self.extra_sources)
        for path in sorted(self.root.rglob("*")):
            if path.is_symlink() or not path.is_file() or path == self.target:
                continue
            if (
                path.suffix.casefold() not in _HEADER_SUFFIXES
                and path not in extra_sources
            ):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if len(text.encode("utf-8")) <= max_bytes:
                items.append((str(path.relative_to(self.root)), text))
        return tuple(items)
