"""Deterministic, evidence-bounded retrieval for validated experiences."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re

from .case_record import ExperiencePolarity
from .case_store import ExperienceSnapshot, StoredExperience


_CODE = re.compile(r"^[a-z0-9_.-]{1,120}$")
_KNOWN_OWNERS = frozenset({"candidate", "testbench", "stub", "unknown"})
_VISIBILITIES = frozenset({"public", "public_generation", "hidden_generation"})


def _codes(values: Iterable[str], name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{name} must be a sequence of codes")
    result = tuple(str(value).strip().casefold() for value in values)
    if any(not _CODE.fullmatch(value) for value in result):
        raise ValueError(f"{name} contains an invalid code")
    return tuple(dict.fromkeys(result))


@dataclass(frozen=True, slots=True)
class CurrentCaseQuery:
    stage: str
    visibility: str
    owner: str
    diagnostic_codes: tuple[str, ...] = ()
    diagnostic_terms: tuple[str, ...] = ()
    owner_clues: tuple[str, ...] = ()
    components: tuple[str, ...] = ()
    evidence_terms: tuple[str, ...] = ()
    source_terms: tuple[str, ...] = ()
    validation_contract: str | None = None
    toolchain: str | None = None
    attempted_change_fingerprints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", _codes((self.stage,), "stage")[0])
        visibility = str(self.visibility).casefold()
        if visibility not in _VISIBILITIES:
            raise ValueError("visibility is not eligible for experience retrieval")
        object.__setattr__(self, "visibility", visibility)
        owner = str(self.owner).casefold()
        if owner not in _KNOWN_OWNERS:
            raise ValueError("owner is not a supported retrieval owner")
        object.__setattr__(self, "owner", owner)
        for name in (
            "diagnostic_codes",
            "diagnostic_terms",
            "owner_clues",
            "components",
            "evidence_terms",
            "source_terms",
        ):
            object.__setattr__(self, name, _codes(getattr(self, name), name))
        for name in ("validation_contract", "toolchain"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _codes((value,), name)[0])
        fingerprints = tuple(self.attempted_change_fingerprints)
        if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in fingerprints):
            raise ValueError("attempted change fingerprints must be SHA-256 digests")
        object.__setattr__(self, "attempted_change_fingerprints", tuple(dict.fromkeys(fingerprints)))


@dataclass(frozen=True, slots=True)
class RetrievedExperience:
    item: StoredExperience
    polarity: ExperiencePolarity
    score: tuple[int, int, int, int]
    reasons: tuple[str, ...]
    previously_attempted: bool
    change_available: bool


@dataclass(frozen=True, slots=True)
class ExperienceRetrievalResult:
    store_revision: str
    positive: tuple[RetrievedExperience, ...]
    negative: tuple[RetrievedExperience, ...]
    excluded: tuple[tuple[str, str], ...]


def _rank_one(
    query: CurrentCaseQuery,
    item: StoredExperience,
) -> tuple[RetrievedExperience | None, str | None]:
    record = item.record
    if query.visibility == "hidden_generation":
        if record.visibility != "public_generation":
            return None, "visibility_not_projected_for_hidden_generation"
    elif record.visibility not in {"public", "public_generation"}:
        return None, "visibility_mismatch"
    if query.owner != "unknown" and record.owner != query.owner:
        return None, "owner_mismatch"
    if query.components and not set(query.components).intersection(record.components):
        return None, "component_mismatch"
    if (
        query.validation_contract is not None
        and record.validation_contract != query.validation_contract
    ):
        return None, "validation_contract_mismatch"
    if query.toolchain is not None and record.toolchain is not None and record.toolchain != query.toolchain:
        return None, "toolchain_mismatch"

    experience_codes = set(record.diagnostic_terms)
    query_codes = set(query.diagnostic_codes)
    exact_code = int(bool(query_codes & experience_codes))
    term_overlap = len(set(query.diagnostic_terms) & experience_codes)
    evidence_overlap = len(set(query.evidence_terms) & set(record.evidence_terms))
    source_overlap = len(set(query.source_terms) & set(record.source_terms))
    contextual_overlap = evidence_overlap + source_overlap
    if not exact_code and not (
        term_overlap >= 2 or (term_overlap >= 1 and contextual_overlap >= 1)
    ):
        return None, "below_relevance_threshold"

    reasons: list[str] = []
    if exact_code:
        reasons.append("exact_diagnostic_code")
    if term_overlap:
        reasons.append("normalized_diagnostic_term_overlap")
    if evidence_overlap:
        reasons.append("evidence_feature_overlap")
    if source_overlap:
        reasons.append("source_symbol_or_type_overlap")
    same_stage = int(record.stage == query.stage)
    if same_stage:
        reasons.append("same_stage")
    if query.validation_contract:
        reasons.append("validation_contract_match")
    previously_attempted = bool(
        record.change_fingerprint
        and record.change_fingerprint in query.attempted_change_fingerprints
    )
    if previously_attempted:
        reasons.append("similar_change_previously_attempted")
    diagnostic_only = query.owner == "unknown"
    score = (exact_code, term_overlap, contextual_overlap, same_stage)
    return (
        RetrievedExperience(
            item=item,
            polarity=record.polarity,
            score=score,
            reasons=tuple(reasons),
            previously_attempted=previously_attempted,
            change_available=not diagnostic_only,
        ),
        None,
    )


def retrieve_experiences(
    query: CurrentCaseQuery,
    snapshot: ExperienceSnapshot,
) -> ExperienceRetrievalResult:
    if not isinstance(query, CurrentCaseQuery):
        raise TypeError("query must be a CurrentCaseQuery")
    if not isinstance(snapshot, ExperienceSnapshot):
        raise TypeError("snapshot must be an ExperienceSnapshot")
    positive: list[RetrievedExperience] = []
    negative: list[RetrievedExperience] = []
    excluded = list(snapshot.excluded)
    for item in snapshot.experiences:
        ranked, reason = _rank_one(query, item)
        if ranked is None:
            excluded.append((item.record.experience_id, reason or "excluded"))
        elif ranked.polarity is ExperiencePolarity.POSITIVE:
            positive.append(ranked)
        else:
            negative.append(ranked)
    key = lambda value: (
        tuple(-part for part in value.score),
        value.item.record.experience_id,
    )
    positive.sort(key=key)
    negative.sort(key=key)
    excluded.sort()
    return ExperienceRetrievalResult(
        store_revision=snapshot.revision,
        positive=tuple(positive),
        negative=tuple(negative),
        excluded=tuple(excluded),
    )
