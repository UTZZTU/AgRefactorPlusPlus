"""Deterministically derive narrowly scoped experience from resolved cases."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any

from .case_record import (
    CaseOutcome,
    CaseStatus,
    DiagnosticCase,
    ExperiencePolarity,
    ExperienceRecord,
)
from .case_store import StoredChange


_TOKEN = re.compile(r"[^a-z0-9_.-]+")


@dataclass(frozen=True, slots=True)
class DerivedExperience:
    record: ExperienceRecord
    change: StoredChange


def _code(value: object, default: str) -> str:
    result = _TOKEN.sub("_", str(value or "").casefold()).strip("_.-")
    return result[:120] or default


def derive_experiences(
    case: DiagnosticCase,
    *,
    changes: Mapping[str, StoredChange],
    events: Mapping[str, Mapping[str, Any]],
) -> tuple[DerivedExperience, ...]:
    if not isinstance(case, DiagnosticCase):
        raise TypeError("case must be DiagnosticCase")
    if case.status is not CaseStatus.CLOSED or case.outcome is not CaseOutcome.RESOLVED:
        return ()

    derived: list[DerivedExperience] = []
    for attempt in case.attempts:
        change = changes.get(attempt.attempt_id)
        if change is None or attempt.changed is not True or not attempt.post_validation_event_refs:
            continue
        validations = [
            (ref, events.get(ref)) for ref in attempt.post_validation_event_refs
        ]
        validations = [(ref, event) for ref, event in validations if event is not None]
        if not validations:
            continue
        accepted = [(ref, event) for ref, event in validations if event.get("accepted") is True]
        rejected = [(ref, event) for ref, event in validations if event.get("accepted") is False]
        if attempt.result == "validated" and accepted:
            polarity = ExperiencePolarity.POSITIVE
            validation_refs, validation_event = accepted[-1]
            fingerprint = None
        elif attempt.result == "validation_failed" and rejected:
            polarity = ExperiencePolarity.NEGATIVE
            validation_refs, validation_event = rejected[-1]
            fingerprint = change.change_fingerprint
        else:
            continue

        stage = _code(validation_event.get("stage"), "unknown")
        owner = next(
            (
                _code(item.get("owner"), "unknown")
                for item in validation_event.get("diagnostic_items", ())
                if isinstance(item, Mapping) and item.get("owner")
            ),
            "unknown",
        )
        components = "+".join(change.components)
        added = sum(1 for line in change.patch.splitlines() if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in change.patch.splitlines() if line.startswith("-") and not line.startswith("---"))
        action_summary = f"{components} change: {added} added and {removed} removed lines"
        attempt_ids = (attempt.attempt_id,)
        experience_key = {
            "case_id": case.case_id,
            "attempt_ids": list(attempt_ids),
            "polarity": polarity.value,
            "source_version_hash": change.before_sha256,
            "validation_ref": validation_refs,
            "change_fingerprint": change.change_fingerprint,
        }
        experience_id = "experience-" + sha256(
            json.dumps(experience_key, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:24]
        record = ExperienceRecord.from_case(
            case,
            experience_id=experience_id,
            source_attempt_ids=attempt_ids,
            polarity=polarity,
            applicability_conditions=(f"stage.{stage}", f"owner.{owner}"),
            action_summary=action_summary,
            validation_scope=stage,
            validation_refs=(validation_refs,),
            evidence_refs=(f"change:{attempt.attempt_id}",),
            source_version_hash=change.before_sha256,
            change_fingerprint=fingerprint,
        )
        derived.append(DerivedExperience(record=record, change=change))
    return tuple(derived)
