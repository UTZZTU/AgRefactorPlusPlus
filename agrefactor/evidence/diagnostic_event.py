"""Agent-safe DiagnosticEvent projection over existing typed evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

from .feedback import FeedbackReport


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HIDDEN_STATES = frozenset({"hidden", "hidden_evaluation"})


def _required(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value.strip()


def _optional(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string or null")
    return value.strip() or None


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True))


def _safe_code(value: object, default: str = "unknown") -> str:
    if not isinstance(value, str):
        return default
    cleaned = value.strip().casefold().replace(" ", "_")
    if not cleaned or len(cleaned) > 160:
        return default
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789_.-"
    return cleaned if all(char in allowed for char in cleaned) else default


@dataclass(frozen=True, slots=True)
class DiagnosticEvent:
    event_id: str
    run_id: str
    validation_id: str
    stage: str
    owner: str
    failure_classes: tuple[str, ...]
    severities: tuple[str, ...]
    route_action: str
    repair_scope: str
    evidence_refs: tuple[str, ...]
    target_identity: Mapping[str, Any]
    toolchain_identity: Mapping[str, Any]
    candidate_sha256: str | None
    public_suite_identities: tuple[Mapping[str, Any], ...]
    physical_tool_launched: bool
    evidence_complete: bool
    context_signature: str
    created_at: str
    source_kind: str = "feedback_report"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    schema_version = 1

    def __post_init__(self) -> None:
        for name in ("event_id", "run_id", "validation_id", "stage", "owner", "route_action", "repair_scope", "context_signature", "created_at", "source_kind"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        if self.stage.casefold() in _HIDDEN_STATES:
            raise ValueError("Hidden evaluation cannot become a DiagnosticEvent")
        if _SHA256.fullmatch(self.context_signature) is None:
            raise ValueError("context_signature must be a SHA-256 digest")
        if self.candidate_sha256 is not None and _SHA256.fullmatch(self.candidate_sha256) is None:
            raise ValueError("candidate_sha256 must be a SHA-256 digest or null")
        for name in ("physical_tool_launched", "evidence_complete"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be boolean")
        object.__setattr__(self, "failure_classes", tuple(sorted({_safe_code(item) for item in self.failure_classes})))
        object.__setattr__(self, "severities", tuple(sorted({_safe_code(item) for item in self.severities})))
        object.__setattr__(self, "evidence_refs", tuple(dict.fromkeys(_required(item, "evidence_ref") for item in self.evidence_refs)))
        object.__setattr__(self, "target_identity", _json_copy(dict(self.target_identity)))
        object.__setattr__(self, "toolchain_identity", _json_copy(dict(self.toolchain_identity)))
        object.__setattr__(self, "public_suite_identities", tuple(_json_copy(dict(item)) for item in self.public_suite_identities))
        object.__setattr__(self, "metadata", _json_copy(dict(self.metadata)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "run_id": self.run_id,
            "validation_id": self.validation_id,
            "stage": self.stage,
            "owner": self.owner,
            "failure_classes": list(self.failure_classes),
            "severities": list(self.severities),
            "route_action": self.route_action,
            "repair_scope": self.repair_scope,
            "evidence_refs": list(self.evidence_refs),
            "target_identity": dict(self.target_identity),
            "toolchain_identity": dict(self.toolchain_identity),
            "candidate_sha256": self.candidate_sha256,
            "public_suite_identities": [dict(item) for item in self.public_suite_identities],
            "physical_tool_launched": self.physical_tool_launched,
            "evidence_complete": self.evidence_complete,
            "context_signature": self.context_signature,
            "created_at": self.created_at,
            "source_kind": self.source_kind,
            "metadata": dict(self.metadata),
            "evidence_view": "agent_safe",
            "hidden_input_count": 0,
            "hidden_content_persisted": False,
            "accepted": False,
            "success_authority": False,
            "fsm_mutation_allowed": False,
        }


class DiagnosticEventProjector:
    """Project allowlisted fields; never parse raw success or Hidden content."""

    projector_version = 1

    def from_feedback(
        self,
        report: FeedbackReport,
        *,
        run_id: str,
        validation_id: str,
        validation_state: str,
        route_action: str,
        selected_feedback_ids: Sequence[str] = (),
        target: Mapping[str, Any] | None = None,
        candidate_code: str | None = None,
        public_suite_identities: Sequence[Mapping[str, Any]] = (),
        created_at: str | None = None,
    ) -> DiagnosticEvent:
        if not isinstance(report, FeedbackReport):
            raise TypeError("report must be a FeedbackReport")
        stage = _safe_code(validation_state)
        if stage in _HIDDEN_STATES:
            raise ValueError("Hidden evaluation cannot be projected")
        if report.metadata.get("evidence_view") != "agent_safe":
            raise ValueError("DiagnosticEvent requires agent_safe feedback")
        selected = set(str(item) for item in selected_feedback_ids)
        items = tuple(
            item for item in report.items
            if item.blocking and (not selected or item.feedback_id in selected)
        )
        if not items:
            raise ValueError("DiagnosticEvent requires blocking feedback")
        owners = {_safe_code(item.owner.value) for item in items}
        owner = next(iter(owners)) if len(owners) == 1 else "mixed"
        classes = tuple(_safe_code(item.category.value) for item in items)
        severities = tuple(_safe_code(item.severity.value) for item in items)
        action = _safe_code(route_action)
        target_identity, toolchain_identity = _target_projection(target or {})
        suites = _suite_projection(public_suite_identities)
        candidate_sha = (
            _sha256_text(candidate_code.rstrip() + "\n")
            if isinstance(candidate_code, str) and candidate_code.strip()
            else None
        )
        signature_payload = {
            "stage": stage,
            "owner": owner,
            "failure_classes": sorted(set(classes)),
            "route_action": action,
            "target_identity": target_identity,
            "toolchain_identity": toolchain_identity,
            "candidate_sha256": candidate_sha,
            "public_suite_identities": list(suites),
        }
        context_signature = _canonical_sha256(signature_payload)
        event_key = {
            "run_id": _required(run_id, "run_id"),
            "validation_id": _required(validation_id, "validation_id"),
            "report_id": report.report_id,
            "context_signature": context_signature,
        }
        return DiagnosticEvent(
            event_id=f"diagnostic-{_canonical_sha256(event_key)[:24]}",
            run_id=run_id,
            validation_id=validation_id,
            stage=stage,
            owner=owner,
            failure_classes=classes,
            severities=severities,
            route_action=action,
            repair_scope=_repair_scope(action),
            evidence_refs=(report.report_id, *(item.feedback_id for item in items)),
            target_identity=target_identity,
            toolchain_identity=toolchain_identity,
            candidate_sha256=candidate_sha,
            public_suite_identities=suites,
            physical_tool_launched=bool(report.metadata.get("physical_execution", True)),
            evidence_complete=bool(report.metadata.get("evidence_complete", True)),
            context_signature=context_signature,
            created_at=created_at or datetime.now(timezone.utc).isoformat(),
            metadata={
                "projector_version": self.projector_version,
                "source_report_id": report.report_id,
                "blocking_item_count": len(items),
                "owner_authority": "deterministic_typed_feedback",
            },
        )

    def from_typed_outcome(
        self,
        outcome: Mapping[str, Any],
        *,
        run_id: str,
        validation_id: str,
        target: Mapping[str, Any] | None = None,
        created_at: str | None = None,
    ) -> DiagnosticEvent:
        if not isinstance(outcome, Mapping):
            raise TypeError("outcome must be a mapping")
        phase = _safe_code(outcome.get("phase"))
        if phase in _HIDDEN_STATES:
            raise ValueError("Hidden typed outcome cannot be projected")
        normalized = outcome.get("stage_normalized_outcome")
        typed = normalized if isinstance(normalized, Mapping) else outcome.get("low_level_result")
        if not isinstance(typed, Mapping):
            raise ValueError("typed outcome is missing normalized evidence")
        status = _safe_code(typed.get("status"))
        if status in {"passed", "succeeded", "accepted", "ok"}:
            raise ValueError("successful outcomes are not failure DiagnosticEvents")
        owner = _safe_code(typed.get("failure_owner"))
        failure = _safe_code(typed.get("failure_kind") or typed.get("reason_code"))
        action = "review_unknown" if owner in {"unknown", "none"} else f"repair_{owner}"
        input_hashes = outcome.get("input_hashes")
        hashes = dict(input_hashes) if isinstance(input_hashes, Mapping) else {}
        candidate_sha = hashes.get("candidate")
        if not isinstance(candidate_sha, str) or _SHA256.fullmatch(candidate_sha) is None:
            candidate_sha = None
        target_identity, toolchain_identity = _target_projection(target or {})
        suite_identity = {
            "suite_id": _safe_code(outcome.get("suite_id")),
            "split": "public",
            "source_kind": "verified_archive",
            "content_sha256": hashes.get("public_test"),
        }
        suites = _suite_projection((suite_identity,))
        signature_payload = {
            "stage": phase,
            "owner": owner,
            "failure_classes": [failure],
            "route_action": action,
            "target_identity": target_identity,
            "toolchain_identity": toolchain_identity,
            "candidate_sha256": candidate_sha,
            "public_suite_identities": list(suites),
        }
        context_signature = _canonical_sha256(signature_payload)
        evidence_sha = typed.get("evidence_sha256")
        refs = [f"typed-outcome:{validation_id}"]
        if isinstance(evidence_sha, str) and _SHA256.fullmatch(evidence_sha):
            refs.append(f"sha256:{evidence_sha}")
        return DiagnosticEvent(
            event_id=f"diagnostic-{_canonical_sha256({'run_id': run_id, 'validation_id': validation_id, 'context_signature': context_signature})[:24]}",
            run_id=run_id,
            validation_id=validation_id,
            stage=phase,
            owner=owner,
            failure_classes=(failure,),
            severities=("error",),
            route_action=action,
            repair_scope=_repair_scope(action),
            evidence_refs=tuple(refs),
            target_identity=target_identity,
            toolchain_identity=toolchain_identity,
            candidate_sha256=candidate_sha,
            public_suite_identities=suites,
            physical_tool_launched=bool(typed.get("tool_launched") or typed.get("cosim_launched")),
            evidence_complete=bool(typed.get("evidence_complete", evidence_sha is not None)),
            context_signature=context_signature,
            created_at=created_at or datetime.now(timezone.utc).isoformat(),
            source_kind="typed_runtime_outcome",
            metadata={
                "projector_version": self.projector_version,
                "reason_code": _safe_code(typed.get("reason_code")),
                "returncode": typed.get("returncode"),
                "timed_out": typed.get("timed_out") is True,
                "owner_authority": _safe_code(typed.get("owner_authority")),
            },
        )


def _repair_scope(action: str) -> str:
    return {
        "repair_candidate": "candidate_only",
        "repair_testbench": "testbench_only",
        "fix_toolchain": "external_toolchain",
        "fix_configuration": "external_configuration",
        "review_mixed": "none_review",
        "review_unknown": "none_abstain",
    }.get(action, "none_review")


def _target_projection(target: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    safe = {
        key: target.get(key)
        for key in ("name", "device", "clock_period_ns", "parser_profile")
        if target.get(key) is not None
    }
    toolchain = {
        key: target.get(key)
        for key in ("toolchain", "toolchain_version")
        if target.get(key) is not None
    }
    safe["fingerprint"] = _canonical_sha256(safe)
    toolchain["fingerprint"] = _canonical_sha256(toolchain)
    return safe, toolchain


def _suite_projection(values: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    result: list[dict[str, Any]] = []
    for raw in values:
        if not isinstance(raw, Mapping):
            raise TypeError("suite identities must be mappings")
        if _safe_code(raw.get("split"), "public") != "public":
            continue
        item = {
            "suite_id": _safe_code(raw.get("suite_id")),
            "split": "public",
            "suite_version": _optional(raw.get("suite_version"), "suite_version"),
            "source_kind": _safe_code(raw.get("source_kind")),
            "content_sha256": raw.get("content_sha256"),
            "runtime_contract_schema_version": raw.get("runtime_contract_schema_version"),
        }
        result.append(item)
    return tuple(sorted(result, key=lambda item: item["suite_id"]))
