"""Immutable manifest writer for the R1 diagnostic corpus."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .diagnostic_event import DiagnosticEvent


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CorpusEvidenceLevel(str, Enum):
    E1_SCHEMA = "E1"
    E2_DETERMINISTIC = "E2"
    E3_REAL_TOOL = "E3"


class CorpusOutcome(str, Enum):
    VERIFIED_FAILURE = "verified_failure"
    VERIFIED_NO_REPAIR = "verified_no_repair"
    ABSTAINED = "abstained"
    INCONCLUSIVE = "inconclusive"
    INVALID_EVIDENCE = "invalid_evidence"


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class DiagnosticCorpusRecord:
    record_id: str
    event: DiagnosticEvent
    evidence_level: CorpusEvidenceLevel
    outcome: CorpusOutcome
    observed_at: str
    source_identity: Mapping[str, Any]
    eligible_for_future_promotion: bool = False
    invalid_reason: str | None = None
    tags: tuple[str, ...] = ()

    schema_version = 1

    def __post_init__(self) -> None:
        if not isinstance(self.event, DiagnosticEvent):
            raise TypeError("event must be a DiagnosticEvent")
        object.__setattr__(self, "evidence_level", self.evidence_level if isinstance(self.evidence_level, CorpusEvidenceLevel) else CorpusEvidenceLevel(str(self.evidence_level)))
        object.__setattr__(self, "outcome", self.outcome if isinstance(self.outcome, CorpusOutcome) else CorpusOutcome(str(self.outcome)))
        if not isinstance(self.record_id, str) or not self.record_id.strip():
            raise ValueError("record_id must not be empty")
        try:
            datetime.fromisoformat(self.observed_at.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError("observed_at must be ISO-8601") from exc
        if not isinstance(self.eligible_for_future_promotion, bool):
            raise TypeError("eligible_for_future_promotion must be boolean")
        if self.eligible_for_future_promotion and (
            self.evidence_level is not CorpusEvidenceLevel.E3_REAL_TOOL
            or not self.event.evidence_complete
            or self.outcome in {CorpusOutcome.INCONCLUSIVE, CorpusOutcome.INVALID_EVIDENCE}
        ):
            raise ValueError("promotion eligibility requires complete E3 evidence")
        if self.outcome is CorpusOutcome.INVALID_EVIDENCE and not self.invalid_reason:
            raise ValueError("invalid evidence requires invalid_reason")
        object.__setattr__(self, "source_identity", json.loads(json.dumps(dict(self.source_identity), sort_keys=True)))
        object.__setattr__(self, "tags", tuple(sorted({str(item).strip() for item in self.tags if str(item).strip()})))

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "record_id": self.record_id,
            "event": self.event.to_dict(),
            "evidence_level": self.evidence_level.value,
            "outcome": self.outcome.value,
            "observed_at": self.observed_at,
            "source_identity": dict(self.source_identity),
            "eligible_for_future_promotion": self.eligible_for_future_promotion,
            "invalid_reason": self.invalid_reason,
            "tags": list(self.tags),
        }
        payload["record_sha256"] = _canonical_sha256(payload)
        return payload


def write_diagnostic_corpus(
    root: str | Path,
    records: Sequence[DiagnosticCorpusRecord],
    *,
    corpus_id: str,
) -> dict[str, Any]:
    destination = Path(root)
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError("corpus destination must be empty")
    destination.mkdir(parents=True, exist_ok=True)
    normalized = tuple(records)
    if not normalized:
        raise ValueError("diagnostic corpus must not be empty")
    if len({item.record_id for item in normalized}) != len(normalized):
        raise ValueError("diagnostic corpus record ids must be unique")
    entries: list[dict[str, Any]] = []
    for item in sorted(normalized, key=lambda value: value.record_id):
        payload = item.to_dict()
        path = destination / f"{item.record_id}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        entries.append({
            "record_id": item.record_id,
            "path": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "evidence_level": item.evidence_level.value,
            "outcome": item.outcome.value,
            "eligible_for_future_promotion": item.eligible_for_future_promotion,
        })
    manifest = {
        "schema_version": 1,
        "corpus_id": corpus_id,
        "record_count": len(entries),
        "records": entries,
        "hidden_record_count": 0,
        "invalid_evidence_excluded_from_promotion": True,
        "success_authority": False,
    }
    manifest["manifest_sha256"] = _canonical_sha256(manifest)
    path = destination / "corpus_manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest
