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


def _event_terms(event: Mapping[str, Any], *names: str) -> tuple[str, ...]:
    values: list[str] = []
    for name in names:
        raw = event.get(name)
        if isinstance(raw, str):
            values.append(_code(raw, ""))
        elif isinstance(raw, (list, tuple)):
            values.extend(_code(item, "") for item in raw if isinstance(item, str))
    return tuple(dict.fromkeys(value for value in values if value))


def _diagnostic_terms(event: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for name in (
        "failure_classes",
        "diagnostic_codes",
        "diagnostic_terms",
        "status",
        "failure_code",
    ):
        values.extend(_event_terms(event, name))
    for item in event.get("diagnostic_items", ()):
        if isinstance(item, Mapping):
            values.extend(
                _event_terms(
                    item,
                    "diagnostic_code",
                    "parser_rule",
                    "category_id",
                    "category",
                )
            )
    return tuple(dict.fromkeys(values))[:32]


def _public_visibility(event: Mapping[str, Any]) -> str | None:
    visibility = event.get("visibility")
    if visibility in {"public", "public_generation"}:
        return visibility
    if event.get("split") == "public":
        return "public_generation" if event.get("stage") == "public_testbench_generation" else "public"
    return None


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
    attempts_by_id = {item.attempt_id: item for item in case.attempts}
    for attempt in case.attempts:
        change = changes.get(attempt.attempt_id)
        if (
            change is None
            or change.attempt_id != attempt.attempt_id
            or attempt.changed is not True
            or not attempt.post_validation_event_refs
        ):
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
            fingerprint = change.change_fingerprint
        elif (
            attempt.result == "validation_failed"
            and rejected
            and rejected[-1][1].get("failure_attributable_to_change") is True
            and isinstance(rejected[-1][1].get("failure_code"), str)
        ):
            polarity = ExperiencePolarity.NEGATIVE
            validation_refs, validation_event = rejected[-1]
            fingerprint = change.change_fingerprint
        else:
            continue

        source_attempt_ids = change.source_attempt_ids
        source_attempt = attempts_by_id.get(source_attempt_ids[0])
        if source_attempt is None:
            continue
        pre_action = next(
            (
                events.get(ref)
                for ref in source_attempt.pre_action_event_refs
                if events.get(ref)
            ),
            None,
        )
        if not isinstance(pre_action, Mapping):
            continue
        stage_value = pre_action.get("stage")
        owner_value = pre_action.get("owner")
        visibility = _public_visibility(pre_action)
        contract = validation_event.get("validation_contract") or validation_event.get("contract_id")
        terms = _diagnostic_terms(pre_action)
        if not all(isinstance(value, str) and value for value in (stage_value, owner_value, contract)):
            continue
        if visibility is None or not terms:
            continue
        stage = _code(stage_value, "unknown")
        owner = _code(owner_value, "unknown")
        if polarity is ExperiencePolarity.NEGATIVE:
            failure_code = _code(validation_event.get("failure_code"), "")
            if not failure_code:
                continue
        else:
            failure_code = None
        evidence_terms = _event_terms(pre_action, "evidence_classes", "evidence_terms")
        source_terms = _event_terms(pre_action, "source_terms", "symbol_terms", "type_terms")
        toolchain_identity = pre_action.get("toolchain_identity")
        toolchain = (
            _code(toolchain_identity.get("name") or toolchain_identity.get("tool"), "")
            if isinstance(toolchain_identity, Mapping)
            else _code(pre_action.get("toolchain"), "")
        ) or None
        components = "+".join(change.components)
        added = sum(
            1
            for line in change.patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        removed = sum(
            1
            for line in change.patch.splitlines()
            if line.startswith("-") and not line.startswith("---")
        )
        step_label = (
            f" across {len(source_attempt_ids)} repair attempts"
            if len(source_attempt_ids) > 1
            else ""
        )
        action_summary = (
            f"{components} cumulative change{step_label}: "
            f"{added} added and {removed} removed lines"
        )
        attempt_ids = source_attempt_ids
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
            evidence_refs=(f"change:{change.attempt_id}",),
            source_version_hash=change.before_sha256,
            change_fingerprint=fingerprint,
            schema_version=2,
            stage=stage,
            owner=owner,
            components=change.components,
            visibility=visibility,
            diagnostic_terms=terms,
            source_terms=source_terms,
            evidence_terms=evidence_terms,
            validation_contract=_code(contract, "unknown"),
            toolchain=toolchain,
            failure_code=failure_code,
        )
        derived.append(DerivedExperience(record=record, change=change))
    return tuple(derived)
