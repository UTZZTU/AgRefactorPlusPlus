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
MATERIALIZED_ISOLATION_VERSION = "r5-historical-candidate-symbol-isolation-v2"


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


def materialize_candidate_symbols(
    candidate: str,
    symbol_map: Sequence[Mapping[str, Any]],
) -> str:
    """Rename complete identifier tokens so deterministic parsers see the ABI."""

    isolate_candidate_symbols(candidate, symbol_map)
    pairs = {
        str(item["source"]): str(item["target"])
        for item in symbol_map
        if isinstance(item, Mapping)
    }
    matcher = re.compile(
        r"\b(?:" + "|".join(re.escape(name) for name in pairs) + r")\b"
    )
    renamed = matcher.sub(lambda match: pairs[match.group(0)], candidate)
    return (
        f"// {MATERIALIZED_ISOLATION_VERSION}\n"
        "// Mechanical identifier isolation only; Candidate logic is unchanged.\n"
        + renamed.rstrip()
        + "\n"
    )


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
    isolation_version: str = ISOLATION_VERSION

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
            "isolation_version": self.isolation_version,
        }


def verify_historical_candidate_plan(
    repository: str | Path,
    plan: Mapping[str, Any],
) -> R5HistoricalCandidateBundle:
    root = Path(repository).expanduser().resolve()
    if not root.is_dir():
        raise R5HistoricalCandidateError("repository root is missing")
    schema_version = plan.get("schema_version")
    is_initial_freeze = schema_version in {1, 2}
    is_fixed_resume = schema_version == 3
    is_final_resume = schema_version == 4
    if (
        schema_version not in {1, 2, 3, 4}
        or (
            is_initial_freeze
            and plan.get("status")
            != "frozen_before_preexisting_candidate_outcome_observation"
        )
        or (
            is_fixed_resume
            and plan.get("status")
            != "frozen_after_audited_pre_provider_isolation_fix"
        )
        or (
            is_final_resume
            and plan.get("status")
            != "frozen_before_final_expanded_history_attempt"
        )
        or plan.get("period") != "history"
        or plan.get("control_role") != "positive"
        or plan.get("failure_family") != "unsupported_construct"
        or (
            is_initial_freeze
            and plan.get("legacy_candidate_outcome_observed") is not False
        )
        or (
            (is_fixed_resume or is_final_resume)
            and plan.get("legacy_candidate_outcome_observed") is not True
        )
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
    isolation_version = plan.get("symbol_isolation_version", ISOLATION_VERSION)
    if isolation_version == ISOLATION_VERSION:
        isolated = isolate_candidate_symbols(legacy, symbol_map)  # type: ignore[arg-type]
    elif isolation_version == MATERIALIZED_ISOLATION_VERSION:
        isolated = materialize_candidate_symbols(legacy, symbol_map)  # type: ignore[arg-type]
    else:
        raise R5HistoricalCandidateError("symbol isolation version is unsupported")
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
    if schema_version in {2, 3, 4}:
        prior_sources = plan.get("prior_observed_source_sha256s")
        required_markers = plan.get("required_legacy_markers")
        if (
            not isinstance(prior_sources, list)
            or not prior_sources
            or any(
                not isinstance(item, str) or not _SHA256.fullmatch(item)
                for item in prior_sources
            )
            or not set(predecessor_sources).issubset(prior_sources)
            or source_sha in prior_sources
        ):
            raise R5HistoricalCandidateError(
                "historical source is not independent of all observed sources"
            )
        if (
            not isinstance(required_markers, list)
            or not required_markers
            or len(required_markers) > 32
            or any(not isinstance(item, str) or not item for item in required_markers)
            or any(item not in legacy for item in required_markers)
        ):
            raise R5HistoricalCandidateError(
                "required legacy construct evidence is missing"
            )
    if is_fixed_resume:
        prior_attempt = plan.get("prior_failed_attempt")
        if (
            isolation_version != MATERIALIZED_ISOLATION_VERSION
            or not isinstance(prior_attempt, Mapping)
            or prior_attempt.get("status")
            not in {
                "clean_pre_provider_model_adapter_failure",
                "clean_provider_or_response_contract_failure",
            }
            or prior_attempt.get("attempts_consumed") != 1
            or prior_attempt.get("maximum_remaining_attempts") != 2
            or plan.get("confidence_threshold_weakened") is not False
        ):
            raise R5HistoricalCandidateError(
                "post-fix historical resume boundary is invalid"
            )
    if is_final_resume:
        prior_attempt = plan.get("prior_attempt")
        if (
            isolation_version != MATERIALIZED_ISOLATION_VERSION
            or not isinstance(prior_attempt, Mapping)
            or prior_attempt.get("status")
            != "clean_safe_calibration_abstention"
            or prior_attempt.get("attempts_consumed") != 2
            or prior_attempt.get("maximum_remaining_attempts") != 1
            or plan.get("confidence_threshold_weakened") is not False
        ):
            raise R5HistoricalCandidateError(
                "final historical attempt boundary is invalid"
            )
    future_ids = plan.get("future_holdout_case_ids")
    if (
        not isinstance(future_ids, list)
        or len(future_ids) < 2
        or any(not isinstance(item, str) or not item for item in future_ids)
        or case_id in future_ids
    ):
        raise R5HistoricalCandidateError("future holdout declaration is invalid")
    budget = plan.get("budget")
    if not isinstance(budget, Mapping):
        raise R5HistoricalCandidateError("historical acquisition budget is invalid")
    expected_budget = {
        "provider_call_upper_bound": 2,
        "vitis_launch_upper_bound": 6,
        "provider_hard_cap": 500,
        "vitis_hard_cap": 500,
    }
    if any(budget.get(name) != expected for name, expected in expected_budget.items()):
        raise R5HistoricalCandidateError("historical acquisition budget is invalid")
    provider_before = budget.get("provider_calls_before")
    vitis_before = budget.get("vitis_launches_before")
    if (
        not isinstance(provider_before, int)
        or isinstance(provider_before, bool)
        or not isinstance(vitis_before, int)
        or isinstance(vitis_before, bool)
        or provider_before < 0
        or vitis_before < 0
        or provider_before + 2 > 500
        or vitis_before + 6 > 500
        or (
            plan.get("schema_version") == 1
            and (provider_before != 93 or vitis_before != 33)
        )
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
        isolation_version=str(isolation_version),
    )


__all__ = [
    "ISOLATION_VERSION",
    "MATERIALIZED_ISOLATION_VERSION",
    "R5HistoricalCandidateBundle",
    "R5HistoricalCandidateError",
    "canonical_sha256",
    "file_sha256",
    "isolate_candidate_symbols",
    "materialize_candidate_symbols",
    "text_sha256",
    "verify_historical_candidate_plan",
]
