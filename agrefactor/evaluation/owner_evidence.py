"""Small helpers for evidence-backed source ownership."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


_SOURCE_OWNERS = frozenset({
    "testbench", "original", "candidate", "configuration", "task_input", "stub",
})


@dataclass(frozen=True, slots=True)
class OwnerEvidence:
    owner: str
    owner_authority: str
    evidence_complete: bool
    matched_files: tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "owner_authority": self.owner_authority,
            "evidence_complete": self.evidence_complete,
            "matched_files": list(self.matched_files),
            "reason": self.reason,
        }


def _diagnostic_file(item: Any) -> str | None:
    if isinstance(item, Mapping):
        value = item.get("file")
    else:
        value = getattr(item, "file", None)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _canonical(path: str | Path, work_dir: Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = work_dir / candidate
    return candidate.resolve(strict=False)


def resolve_source_owner(
    diagnostics: Iterable[Any],
    *,
    source_roles: Mapping[str, str],
    work_dir: str | Path,
    compile_units: Mapping[str, str] | Iterable[str] | None = None,
    execution: Mapping[str, Any] | None = None,
    source_spans: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
) -> OwnerEvidence:
    """Resolve an owner only from exact compiler-unit provenance.

    A diagnostic without a file, a path outside ``work_dir``, an unlisted
    compile unit, or diagnostics spanning more than one role is deliberately
    inconclusive.  Text matching and basenames are not used as evidence.
    """
    facts = execution if isinstance(execution, Mapping) else {}
    returncode = facts.get("returncode")
    if (
        facts.get("status") != "completed"
        or facts.get("timeout") is not False
        or not isinstance(returncode, int)
        or isinstance(returncode, bool)
        or returncode <= 0
    ):
        return OwnerEvidence(
            "unknown", "execution_not_proven", False, (),
            "source ownership requires an observed nonzero completed execution",
        )
    root = Path(work_dir).expanduser().resolve(strict=False)
    roles: dict[Path, str] = {}
    for raw_path, role in source_roles.items():
        if not isinstance(role, str) or role.strip() not in _SOURCE_OWNERS | {"unknown"}:
            continue
        path = _canonical(raw_path, root)
        try:
            path.relative_to(root)
        except ValueError:
            continue
        roles[path] = role.strip()

    if isinstance(compile_units, Mapping):
        unit_paths = set()
        for raw_path, role in compile_units.items():
            if isinstance(role, str) and role.strip() in _SOURCE_OWNERS | {"unknown"}:
                path = _canonical(raw_path, root)
                try:
                    path.relative_to(root)
                except ValueError:
                    continue
                existing = roles.get(path)
                roles[path] = role.strip() if existing in {None, role.strip()} else "unknown"
                unit_paths.add(path)
        roles = {path: role for path, role in roles.items() if path in unit_paths}
    elif compile_units is not None:
        unit_paths = set()
        for raw_path in compile_units:
            path = _canonical(str(raw_path), root)
            unit_paths.add(path)
        roles = {path: role for path, role in roles.items() if path in unit_paths}

    files: list[str] = []
    matched_roles: set[str] = set()
    used_spans = False
    spans = {
        _canonical(raw_path, root): tuple(segments)
        for raw_path, segments in (source_spans or {}).items()
    }
    for diagnostic in diagnostics:
        raw_file = _diagnostic_file(diagnostic)
        if raw_file is None:
            return OwnerEvidence(
                "unknown", "insufficient_provenance", False, tuple(files),
                "diagnostic has no source file",
            )
        path = _canonical(raw_file, root)
        try:
            path.relative_to(root)
        except ValueError:
            return OwnerEvidence(
                "unknown", "path_outside_work_dir", False, tuple(files),
                "diagnostic path is outside the executed work directory",
            )
        role = roles.get(path)
        if role is None:
            return OwnerEvidence(
                "unknown", "unlisted_compile_unit", False, tuple(files),
                "diagnostic file is not in the executed compile-unit map",
            )
        if path in spans:
            line = diagnostic.get("line") if isinstance(diagnostic, Mapping) else getattr(diagnostic, "line", None)
            segment_roles = {
                segment.get("owner")
                for segment in spans[path]
                if isinstance(line, int) and not isinstance(line, bool)
                and segment.get("line_start", 1) <= line <= segment.get("line_end", 0)
            }
            role = next(iter(segment_roles)) if len(segment_roles) == 1 else "unknown"
            used_spans = True
        if role not in _SOURCE_OWNERS:
            return OwnerEvidence(
                "unknown", "unresolved_source_span", False, tuple(files),
                "diagnostic source role is ambiguous or not supported",
            )
        matched_roles.add(role)
        files.append(path.relative_to(root).as_posix())

    if len(matched_roles) != 1:
        return OwnerEvidence(
            "unknown", "mixed_or_empty_provenance", False, tuple(files),
            "no single source role explains all diagnostics",
        )
    owner = next(iter(matched_roles))
    return OwnerEvidence(
        owner,
        "source_span" if used_spans else "source_compile_unit",
        True,
        tuple(files),
        "all diagnostics exactly matched executed compile units",
    )


def tool_launch_owner(
    *,
    launch_error: bool = False,
    version_mismatch: bool = False,
    explicit_process_failure: bool = False,
    timeout: bool = False,
) -> OwnerEvidence:
    """Classify only explicit tool failures; timeout alone is inconclusive."""
    if launch_error or version_mismatch or explicit_process_failure:
        return OwnerEvidence(
            "toolchain", "explicit_tool_failure", True, (),
            "tool launch/version/process failure was directly observed",
        )
    if timeout:
        return OwnerEvidence(
            "unknown", "timeout_without_owner", False, (),
            "timeout does not identify the failing component",
        )
    return OwnerEvidence(
        "unknown", "insufficient_provenance", False, (),
        "no explicit tool failure evidence",
    )
