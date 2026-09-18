"""Freeze and verify a pre-existing Candidate for R5 history acquisition."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_RELATIVE = re.compile(r"^[A-Za-z0-9_./-]+$")
ISOLATION_VERSION = "r5-historical-candidate-symbol-isolation-v1"


class R5HistoricalCandidateError(ValueError):
    """Raised when historical Candidate evidence is not reproducible."""


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise R5HistoricalCandidateError(f"missing evidence file: {path}") from exc
    return digest.hexdigest()


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def isolate_candidate_symbols(
    candidate: str,
    symbol_map: Sequence[Mapping[str, Any]],
) -> str:
    """Add deterministic preprocessor aliases without changing Candidate logic."""

    if not isinstance(candidate, str) or not candidate.strip():
        raise R5HistoricalCandidateError("historical Candidate is empty")
    if not isinstance(symbol_map, Sequence) or isinstance(symbol_map, (str, bytes)):
        raise R5HistoricalCandidateError("symbol isolation map must be a sequence")
    pairs: list[tuple[str, str]] = []
    sources: set[str] = set()
    targets: set[str] = set()
    for raw in symbol_map:
        if not isinstance(raw, Mapping):
            raise R5HistoricalCandidateError("symbol isolation entry must be an object")
        source = raw.get("source")
        target = raw.get("target")
        if (
            not isinstance(source, str)
            or not _IDENTIFIER.fullmatch(source)
            or not isinstance(target, str)
            or not _IDENTIFIER.fullmatch(target)
            or source == target
            or source in sources
            or target in targets
        ):
            raise R5HistoricalCandidateError("symbol isolation entry is invalid")
        if re.search(rf"\b{re.escape(source)}\b", candidate) is None:
            raise R5HistoricalCandidateError(
                f"symbol isolation source is absent: {source}"
            )
        sources.add(source)
        targets.add(target)
        pairs.append((source, target))
    if not pairs:
        raise R5HistoricalCandidateError("symbol isolation map is empty")
    if sources & targets:
        raise R5HistoricalCandidateError("symbol isolation map contains a cycle")
    preamble = [
        f"// {ISOLATION_VERSION}",
        "// Mechanical link isolation only; the checked-in Candidate body follows.",
        *(f"#define {source} {target}" for source, target in pairs),
    ]
    return "\n".join(preamble) + "\n" + candidate.rstrip() + "\n"


def _resolve(repository: Path, value: Any, label: str) -> tuple[str, Path]:
    if not isinstance(value, str) or not _SAFE_RELATIVE.fullmatch(value):
        raise R5HistoricalCandidateError(f"{label} is not a safe relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise R5HistoricalCandidateError(f"{label} escapes the repository")
    path = (repository / relative).resolve()
    try:
        path.relative_to(repository)
    except ValueError as exc:
        raise R5HistoricalCandidateError(f"{label} escapes the repository") from exc
    if path.is_symlink() or not path.is_file():
        raise R5HistoricalCandidateError(f"{label} is not a regular file")
    return relative.as_posix(), path


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise R5HistoricalCandidateError(f"{label} is not a lowercase SHA-256")
    return value


@dataclass(frozen=True, slots=True)
class R5HistoricalCandidateBundle:
    case_id: str
    reference_top: str
    candidate_top: str
    paths: Mapping[str, str]
    file_sha256s: Mapping[str, str]
    reference_code: str
    legacy_candidate_code: str
    isolated_candidate_code: str
    public_test_code: str
    hidden_test_code: str
    source_sha256: str
    isolated_candidate_sha256: str
    plan_sha256: str

    def to_identity(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "reference_top": self.reference_top,
            "candidate_top": self.candidate_top,
            "paths": dict(self.paths),
            "file_sha256s": dict(self.file_sha256s),
            "source_sha256": self.source_sha256,
            "isolated_candidate_sha256": self.isolated_candidate_sha256,
            "plan_sha256": self.plan_sha256,
            "isolation_version": ISOLATION_VERSION,
        }


def verify_historical_candidate_plan(
    repository: str | Path,
    plan: Mapping[str, Any],
) -> R5HistoricalCandidateBundle:
    root = Path(repository).expanduser().resolve()
    if not root.is_dir():
        raise R5HistoricalCandidateError("repository root is missing")
    if (
        plan.get("schema_version") != 1
        or plan.get("status")
        != "frozen_before_preexisting_candidate_outcome_observation"
        or plan.get("period") != "history"
        or plan.get("control_role") != "positive"
        or plan.get("failure_family") != "unsupported_construct"
        or plan.get("legacy_candidate_outcome_observed") is not False
        or plan.get("future_outcomes_observed") is not False
        or plan.get("trusted_revision_creation_allowed") is not False
        or plan.get("r5_real_campaign_allowed") is not False
    ):
        raise R5HistoricalCandidateError("historical Candidate plan boundary is invalid")
    case_id = plan.get("case_id")
    reference_top = plan.get("reference_top")
    candidate_top = plan.get("candidate_top")
    if not isinstance(case_id, str) or not case_id:
        raise R5HistoricalCandidateError("case_id is missing")
    if (
        not isinstance(reference_top, str)
        or not _IDENTIFIER.fullmatch(reference_top)
        or not isinstance(candidate_top, str)
        or not _IDENTIFIER.fullmatch(candidate_top)
        or reference_top == candidate_top
    ):
        raise R5HistoricalCandidateError("top function identities are invalid")
    raw_paths = plan.get("paths")
    raw_hashes = plan.get("file_sha256s")
    if not isinstance(raw_paths, Mapping) or not isinstance(raw_hashes, Mapping):
        raise R5HistoricalCandidateError("plan paths or hashes are missing")
    paths: dict[str, str] = {}
    resolved: dict[str, Path] = {}
    hashes: dict[str, str] = {}
    for role in ("reference", "legacy_candidate", "public_test", "hidden_test"):
        relative, path = _resolve(root, raw_paths.get(role), role)
        expected = _require_sha256(raw_hashes.get(role), f"{role} hash")
        if file_sha256(path) != expected:
            raise R5HistoricalCandidateError(f"{role} file hash mismatch")
        paths[role] = relative
        resolved[role] = path
        hashes[role] = expected
    if hashes["public_test"] == hashes["hidden_test"]:
        raise R5HistoricalCandidateError("Public and Hidden tests are not distinct")

    reference = resolved["reference"].read_text(encoding="utf-8")
    legacy = resolved["legacy_candidate"].read_text(encoding="utf-8")
    public = resolved["public_test"].read_text(encoding="utf-8")
    hidden = resolved["hidden_test"].read_text(encoding="utf-8")
    for split, text in (("Public", public), ("Hidden", hidden)):
        if re.search(r'#\s*include\s*["<][^">]+\.cpp[">]', text):
            raise R5HistoricalCandidateError(f"{split} test includes an implementation")
        for symbol in (reference_top, candidate_top):
            if re.search(rf"\b{re.escape(symbol)}\s*\(", text) is None:
                raise R5HistoricalCandidateError(f"{split} test does not call {symbol}")
        if re.search(r"\breturn\s+1\s*;", text) is None:
            raise R5HistoricalCandidateError(f"{split} test lacks a failure return")

    symbol_map = plan.get("symbol_isolation_map")
    isolated = isolate_candidate_symbols(legacy, symbol_map)  # type: ignore[arg-type]
    expected_isolated = _require_sha256(
        plan.get("isolated_candidate_sha256"),
        "isolated Candidate hash",
    )
    if text_sha256(isolated) != expected_isolated:
        raise R5HistoricalCandidateError("isolated Candidate hash mismatch")
    mapped_top = [
        item.get("target")
        for item in symbol_map
        if isinstance(item, Mapping) and item.get("source") == reference_top
    ]
    if mapped_top != [candidate_top]:
        raise R5HistoricalCandidateError("top isolation mapping is not exact")
    source_sha = text_sha256(reference)
    if source_sha != _require_sha256(plan.get("source_sha256"), "source hash"):
        raise R5HistoricalCandidateError("reference text hash mismatch")
    predecessor_sources = plan.get("predecessor_source_sha256s")
    if (
        not isinstance(predecessor_sources, list)
        or not predecessor_sources
        or any(not isinstance(item, str) or not _SHA256.fullmatch(item) for item in predecessor_sources)
        or source_sha in predecessor_sources
    ):
        raise R5HistoricalCandidateError("historical source is not predecessor-independent")
    future_ids = plan.get("future_holdout_case_ids")
    if (
        not isinstance(future_ids, list)
        or len(future_ids) < 2
        or any(not isinstance(item, str) or not item for item in future_ids)
        or case_id in future_ids
    ):
        raise R5HistoricalCandidateError("future holdout declaration is invalid")
    budget = plan.get("budget")
    if not isinstance(budget, Mapping) or any(
        budget.get(name) != expected
        for name, expected in {
            "provider_calls_before": 93,
            "vitis_launches_before": 33,
            "provider_call_upper_bound": 2,
            "vitis_launch_upper_bound": 6,
            "provider_hard_cap": 500,
            "vitis_hard_cap": 500,
        }.items()
    ):
        raise R5HistoricalCandidateError("historical acquisition budget is invalid")
    plan_sha = canonical_sha256(dict(plan))
    return R5HistoricalCandidateBundle(
        case_id=case_id,
        reference_top=reference_top,
        candidate_top=candidate_top,
        paths=paths,
        file_sha256s=hashes,
        reference_code=reference,
        legacy_candidate_code=legacy,
        isolated_candidate_code=isolated,
        public_test_code=public,
        hidden_test_code=hidden,
        source_sha256=source_sha,
        isolated_candidate_sha256=expected_isolated,
        plan_sha256=plan_sha,
    )


__all__ = [
    "ISOLATION_VERSION",
    "R5HistoricalCandidateBundle",
    "R5HistoricalCandidateError",
    "canonical_sha256",
    "file_sha256",
    "isolate_candidate_symbols",
    "text_sha256",
    "verify_historical_candidate_plan",
]
