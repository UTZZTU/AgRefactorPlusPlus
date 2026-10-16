"""Project candidate repair results into the P2 case and experience contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from difflib import unified_diff
from hashlib import sha256
import json
from pathlib import Path
import re
from threading import RLock
from typing import Any

from .case_record import (
    CaseAttempt,
    CaseOutcome,
    CaseStatus,
    DiagnosticCase,
    EvidenceCheckResult,
    EvidenceCheckState,
    OwnerAssessment,
    RepairEligibilitySnapshot,
)
from .case_store import CaseStore, ExperienceStore, StoredChange
from .experience_derivation import derive_experiences


_MAX_PATCH_BYTES = 16 * 1024


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, *parts: str) -> str:
    value = "\x00".join(parts).encode("utf-8")
    return f"{prefix}-{sha256(value).hexdigest()[:24]}"


def _patch(before: str, after: str, *, name: str) -> str | None:
    if before == after:
        return None
    lines = list(
        unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"a/{name}",
            tofile=f"b/{name}",
            lineterm="",
            n=3,
        )
    )
    value = "\n".join(lines) + "\n"
    return value if len(value.encode("utf-8")) <= _MAX_PATCH_BYTES else None


def _validation_contract(stage: str, component: str) -> str:
    stage_code = re.sub(
        r"[^a-z0-9_.-]+", "_", str(stage).casefold()
    ).strip("_.-")
    component_code = re.sub(
        r"[^a-z0-9_.-]+", "_", str(component).casefold()
    ).strip("_.-")
    return (
        f"agrefactor.{component_code[:48] or 'unknown'}."
        f"{stage_code[:64] or 'unknown'}.validation_v1"
    )


def _event_key(event: Mapping[str, Any]) -> str | None:
    value = event.get("event_id")
    return value if isinstance(value, str) and value else None


def _public_case_event(event: Mapping[str, Any]) -> dict[str, Any] | None:
    if (
        event.get("evidence_view") != "agent_safe"
        or event.get("split", "public") != "public"
        or "hidden" in str(event.get("stage", "")).casefold()
    ):
        return None
    visibility = event.get("visibility")
    if visibility is None:
        visibility = (
            "public_generation"
            if event.get("stage") == "public_testbench_generation"
            else "public"
        )
    if visibility not in {"public", "public_generation"}:
        return None
    projected = dict(event)
    projected["visibility"] = visibility
    return projected


def _validation_event(
    *,
    event_id: str,
    stage: str,
    accepted: bool,
    validation_id: str,
    validation_contract: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "event_id": event_id,
        "validation_id": validation_id,
        "stage": stage,
        "validation_contract": validation_contract,
        "outcome": "validated" if accepted else "validation_failed",
        "accepted": accepted,
        "evidence_view": "agent_safe",
        "hidden_content_persisted": False,
    }


class CaseRecorder:
    """Persist an open case and each dispatched attempt before recovery work."""

    def __init__(
        self,
        root: str | Path,
        validation_id: str,
        *,
        case_scope_id: str | None = None,
        experience_root: str | Path | None = None,
    ) -> None:
        self._root = Path(root)
        self._validation_id = validation_id
        self._case_scope_id = case_scope_id
        self._experience_root = (
            None if experience_root is None else Path(experience_root)
        )
        self._store = CaseStore(self._root)
        self._case: DiagnosticCase | None = None
        self._latest_event_ref: str | None = None
        self._active = False
        self._blocked = False
        self._lock = RLock()
        self._pending_generation_events: list[dict[str, Any]] = []
        self._generation_attempts: dict[str, dict[str, Any]] = {}
        self._generation_diagnostic_refs: dict[str, str] = {}

    def start_case(self, event: Mapping[str, Any]) -> bool:
        with self._lock:
            return self._start_case(event)

    def _start_case(self, event: Mapping[str, Any]) -> bool:
        if not isinstance(event, Mapping):
            return False
        event = _public_case_event(event)
        if event is None:
            return False
        event_id = _event_key(event)
        if event_id is None:
            return False
        run_id = str(event.get("run_id", self._validation_id))
        case_id = _stable_id(
            "case",
            run_id,
            self._validation_id,
            self._case_scope_id or event_id,
        )
        if self._active and self._case is not None and self._case.case_id == case_id:
            return self._record_event(event)
        try:
            existing = self._store.read_case(case_id)
        except FileNotFoundError:
            pending = (
                self._pending_generation_events
                if self._case_scope_id is not None
                else []
            )
            for pending_event in pending:
                pending_id = _event_key(pending_event)
                if pending_id is not None:
                    self._store.write_event(case_id, pending_id, pending_event)
            self._store.write_event(case_id, event_id, event)
            evidence_refs = tuple(
                str(item)
                for item in event.get("evidence_refs", ())
                if isinstance(item, str)
            ) or (event_id,)
            owner = str(event.get("owner", "unknown"))
            eligibility = ()
            if event.get("evidence_refs"):
                action = str(event.get("route_action", "unknown")).casefold()
                eligibility = (
                    RepairEligibilitySnapshot(
                        decision_ref=_stable_id(
                            "eligibility", case_id, event_id
                        ),
                        eligible=(
                            "repair" in action
                            or action == "public_csynth_bounded_trial"
                        ),
                        authorization_ref=None,
                        evidence_refs=evidence_refs,
                    ),
                )
            self._case = DiagnosticCase(
                case_id=case_id,
                created_at=str(
                    event.get("created_at")
                    or datetime.now(timezone.utc).isoformat()
                ),
                status=CaseStatus.OPEN,
                outcome=None,
                closure_reason=None,
                diagnostic_event_refs=tuple(
                    dict.fromkeys(
                        [
                            *(
                                _event_key(item)
                                for item in pending
                                if _event_key(item) is not None
                            ),
                            event_id,
                        ]
                    )
                ),
                owner_assessments=(
                    OwnerAssessment(
                        diagnostic_event_ref=event_id,
                        owner=owner,
                        evidence_refs=evidence_refs,
                    ),
                ),
                eligibility_snapshots=eligibility,
            )
            self._store.write_case(self._case)
        else:
            if event_id not in self._store.read_events(case_id):
                self._store.write_event(case_id, event_id, event)
            if existing.status is CaseStatus.CLOSED:
                self._case = existing
                self._latest_event_ref = existing.diagnostic_event_refs[-1]
                self._blocked = True
                return False
            pending = (
                self._pending_generation_events
                if self._case_scope_id is not None
                else []
            )
            for pending_event in pending:
                pending_id = _event_key(pending_event)
                if pending_id is not None:
                    self._store.write_event(case_id, pending_id, pending_event)
            attempts = tuple(
                replace(attempt, result="dispatch_incomplete")
                if attempt.result == "dispatched"
                else attempt
                for attempt in existing.attempts
            )
            event_refs = tuple(
                dict.fromkeys(
                    (
                        *existing.diagnostic_event_refs,
                        *(
                            _event_key(item)
                            for item in pending
                            if _event_key(item) is not None
                        ),
                        event_id,
                    )
                )
            )
            evidence_refs = tuple(
                str(item)
                for item in event.get("evidence_refs", ())
                if isinstance(item, str)
            ) or (event_id,)
            owners = list(existing.owner_assessments)
            if not any(
                item.diagnostic_event_ref == event_id for item in owners
            ):
                owners.append(
                    OwnerAssessment(
                        diagnostic_event_ref=event_id,
                        owner=str(event.get("owner", "unknown")),
                        evidence_refs=evidence_refs,
                    )
                )
            self._case = replace(
                existing,
                diagnostic_event_refs=event_refs,
                attempts=attempts,
                owner_assessments=tuple(owners),
            )
            self._store.update_case(self._case)

        self._latest_event_ref = self._case.diagnostic_event_refs[-1]
        self._case = self._merge_event_evidence_checks(
            self._case, event, self._latest_event_ref
        )
        self._store.update_case(self._case)
        self._active = True
        self._pending_generation_events = []
        return True

    def record_candidate_dispatch(
        self,
        attempt_number: int,
        _candidate: str,
        route_decision: Any | None = None,
    ) -> bool | None:
        with self._lock:
            return self._record_candidate_dispatch(
                attempt_number, _candidate, route_decision
            )

    def _record_candidate_dispatch(
        self, attempt_number: int, _candidate: str, route_decision: Any | None
    ) -> bool | None:
        if self._blocked:
            return False
        if not self._active or self._case is None:
            return None
        attempt_id = _stable_id(
            "attempt", self._case.case_id, str(attempt_number)
        )
        return self._record_dispatch(
            attempt_id=attempt_id,
            quota_bucket="candidate",
            repair_record_ref=f"{self._validation_id}:{attempt_number}",
            authorization_payload=self._route_decision_payload(route_decision),
        )

    def record_event(self, event: Mapping[str, Any]) -> bool:
        with self._lock:
            return self._record_event(event)

    def _record_event(self, event: Mapping[str, Any]) -> bool:
        if not isinstance(event, Mapping):
            return False
        event = _public_case_event(event)
        if event is None:
            return False
        if not self._active or self._case is None:
            return False
        event_id = _event_key(event)
        if event_id is None:
            return False
        events = self._store.read_events(self._case.case_id)
        if event_id not in events:
            self._store.write_event(self._case.case_id, event_id, event)
        refs = tuple(dict.fromkeys((*self._case.diagnostic_event_refs, event_id)))
        evidence_refs = tuple(
            str(item)
            for item in event.get("evidence_refs", ())
            if isinstance(item, str)
        ) or (event_id,)
        owners = list(self._case.owner_assessments)
        if not any(item.diagnostic_event_ref == event_id for item in owners):
            owners.append(
                OwnerAssessment(
                    diagnostic_event_ref=event_id,
                    owner=str(event.get("owner", "unknown")),
                    evidence_refs=evidence_refs,
                )
            )
        self._case = replace(
            self._case,
            diagnostic_event_refs=refs,
            owner_assessments=tuple(owners),
        )
        self._case = self._merge_event_evidence_checks(
            self._case, event, event_id
        )
        self._store.update_case(self._case)
        self._latest_event_ref = event_id
        return True

    @staticmethod
    def _merge_event_evidence_checks(
        case: DiagnosticCase,
        event: Mapping[str, Any],
        event_id: str,
    ) -> DiagnosticCase:
        checks = list(case.evidence_checks)
        known = {(item.check_id, item.evidence_refs) for item in checks}
        for item in event.get("diagnostic_items", ()):
            if not isinstance(item, Mapping):
                continue
            raw = item.get("diagnostic_check")
            if not isinstance(raw, Mapping):
                continue
            evidence_refs = (event_id,)
            identity = (raw.get("check_id"), evidence_refs)
            if identity in known:
                continue
            try:
                check = EvidenceCheckResult(
                    check_id=raw.get("check_id"),
                    result=EvidenceCheckState(raw.get("result")),
                    evidence_refs=evidence_refs,
                    summary=raw.get("summary"),
                )
            except (TypeError, ValueError):
                continue
            checks.append(check)
            known.add(identity)
        return replace(case, evidence_checks=tuple(checks)) if len(checks) != len(case.evidence_checks) else case

    def record_testbench_dispatch(
        self,
        *,
        attempt_key: str,
        repair_record_ref: str,
        authorization_payload: Mapping[str, Any] | None = None,
        ledger_event: Any | None = None,
        route_decision: Any | None = None,
        qualification: Mapping[str, Any] | None = None,
    ) -> bool | None:
        with self._lock:
            return self._record_testbench_dispatch(
                attempt_key=attempt_key,
                repair_record_ref=repair_record_ref,
                authorization_payload=authorization_payload,
                ledger_event=ledger_event,
                route_decision=route_decision,
                qualification=qualification,
            )

    def _record_testbench_dispatch(
        self,
        *,
        attempt_key: str,
        repair_record_ref: str,
        authorization_payload: Mapping[str, Any] | None,
        ledger_event: Any | None,
        route_decision: Any | None,
        qualification: Mapping[str, Any] | None,
    ) -> bool | None:
        if self._blocked:
            return False
        if not self._active or self._case is None:
            return None
        attempt_id = _stable_id(
            "attempt", self._case.case_id, attempt_key
        )
        if authorization_payload is None and route_decision is not None:
            route_payload = self._route_decision_payload(route_decision)
            assert route_payload is not None
            authorization_ref = _stable_id(
                "authorization", self._case.case_id, attempt_id
            )
            authorization_payload = {
                "decision_ref": authorization_ref,
                "decision_type": "public_testbench_repair",
                "route_decision": route_payload,
                "qualification": {
                    key: value
                    for key, value in (qualification or {}).items()
                    if key
                    in {"revision_authorization", "source_kind", "stage", "split"}
                    and isinstance(value, (str, bool, int, float, type(None)))
                },
                "eligible": True,
                "evidence_view": "agent_safe",
                "hidden_content_persisted": False,
            }
        return self._record_dispatch(
            attempt_id=attempt_id,
            quota_bucket="testbench_public",
            repair_record_ref=_stable_id("repair", repair_record_ref),
            authorization_payload=authorization_payload,
            ledger_event=ledger_event,
        )

    def testbench_dispatch_recorded(self, attempt_key: str) -> bool:
        if self._case is None:
            return False
        attempt_id = _stable_id(
            "attempt", self._case.case_id, attempt_key
        )
        return any(
            item.attempt_id == attempt_id for item in self._case.attempts
        )

    def _record_dispatch(
        self,
        *,
        attempt_id: str,
        quota_bucket: str,
        repair_record_ref: str,
        authorization_payload: Mapping[str, Any] | None = None,
        ledger_event: Any | None = None,
    ) -> bool:
        if self._blocked:
            return False
        assert self._case is not None
        if any(item.attempt_id == attempt_id for item in self._case.attempts):
            return False
        pre_action_ref = self._latest_event_ref
        if pre_action_ref is None:
            return False
        source_event = self._store.read_events(self._case.case_id).get(
            pre_action_ref, {}
        )
        evidence_refs = tuple(
            str(item)
            for item in source_event.get("evidence_refs", ())
            if isinstance(item, str)
        ) or (pre_action_ref,)
        decision_ref = _stable_id(
            "eligibility", self._case.case_id, attempt_id
        )
        authorization_ref = None
        if authorization_payload is not None:
            authorization_ref = str(authorization_payload.get("decision_ref", ""))
            if not authorization_ref:
                raise ValueError("authorization decision requires decision_ref")
            self._store.write_decision(
                self._case.case_id,
                authorization_ref,
                authorization_payload,
            )
        ledger_event_ref = self._persist_ledger_event(
            attempt_id, ledger_event
        )
        attempt = CaseAttempt(
            attempt_id=attempt_id,
            sequence_index=len(self._case.attempts),
            result="dispatched",
            pre_action_event_refs=(pre_action_ref,),
            post_validation_event_refs=(),
            authorization_ref=decision_ref,
            quota_buckets=(quota_bucket,),
            changed=None,
            ledger_event_ref=ledger_event_ref,
            repair_record_ref=repair_record_ref,
        )
        snapshot = RepairEligibilitySnapshot(
            decision_ref=decision_ref,
            eligible=True,
            authorization_ref=authorization_ref,
            evidence_refs=evidence_refs,
        )
        self._case = replace(
            self._case,
            attempts=(*self._case.attempts, attempt),
            eligibility_snapshots=(
                *self._case.eligibility_snapshots,
                snapshot,
            ),
        )
        self._store.update_case(self._case)
        return True

    @staticmethod
    def _route_decision_payload(route_decision: Any | None) -> dict[str, Any] | None:
        if route_decision is None:
            return None
        action = getattr(route_decision, "action", None)
        payload = {
            "decision_ref": str(getattr(route_decision, "decision_id", "")),
            "decision_type": "feedback_route",
            "action": str(getattr(action, "value", action or "unknown")),
            "reason": str(getattr(route_decision, "reason", "")),
            "source_report_id": str(
                getattr(route_decision, "source_report_id", "")
            ),
            "selected_feedback_ids": list(
                getattr(route_decision, "selected_feedback_ids", ())
            ),
            "evidence_view": "agent_safe",
            "hidden_content_persisted": False,
        }
        if not payload["decision_ref"] or not payload["source_report_id"]:
            raise ValueError("route decision is missing stable identifiers")
        return payload

    def _persist_ledger_event(self, attempt_id: str, ledger_event: Any | None) -> str | None:
        if ledger_event is None:
            return None
        event_payload = ledger_event.to_dict()
        decision_ref = _stable_id(
            "ledger", self._case.case_id, attempt_id, str(event_payload["sequence"])
        )
        self._store.write_decision(
            self._case.case_id,
            decision_ref,
            {
                "decision_ref": decision_ref,
                "decision_type": "recovery_ledger_event",
                "ledger_event": event_payload,
                "evidence_view": "agent_safe",
                "hidden_content_persisted": False,
            },
        )
        return decision_ref

    def record_candidate_ledger_event(
        self, attempt_number: int, ledger_event: Any
    ) -> bool:
        with self._lock:
            if self._case is None:
                return False
            attempt_id = _stable_id(
                "attempt", self._case.case_id, str(attempt_number)
            )
            for index, attempt in enumerate(self._case.attempts):
                if attempt.attempt_id == attempt_id:
                    ledger_ref = self._persist_ledger_event(
                        attempt_id, ledger_event
                    )
                    updated = replace(attempt, ledger_event_ref=ledger_ref)
                    attempts = list(self._case.attempts)
                    attempts[index] = updated
                    self._case = replace(self._case, attempts=tuple(attempts))
                    self._store.update_case(self._case)
                    return True
            return False

    def record_public_generation_diagnostic(
        self, event: Mapping[str, Any]
    ) -> bool:
        if (
            not isinstance(event, Mapping)
            or event.get("split") != "public"
            or event.get("evidence_view") != "agent_safe"
        ):
            return False
        with self._lock:
            self._generation_diagnostic_refs[
                str(event.get("trajectory_idx", "0"))
            ] = str(event["event_id"])
            started = self._start_case(event)
            return started

    def record_public_generation_request(
        self,
        event: Mapping[str, Any],
        *,
        attempt_key: str | None = None,
        component: str | None = None,
        before_code: str | None = None,
        after_code: str | None = None,
        authorization: Mapping[str, Any] | None = None,
    ) -> bool:
        if (
            not isinstance(event, Mapping)
            or event.get("split") != "public"
            or event.get("evidence_view") != "agent_safe"
        ):
            return False
        with self._lock:
            if self._case is None or not self._active:
                self._pending_generation_events.append(dict(event))
            else:
                self._append_generation_event(event)
            if (
                attempt_key is None
                or component not in {"testbench", "stub"}
                or event.get("request_role") != "repair"
                or event.get("dispatched") is not True
            ):
                return True
            attempt = self._generation_attempts.setdefault(
                attempt_key,
                {
                    "pre_action_event_ref": (
                        self._generation_diagnostic_refs.get(
                            str(event.get("trajectory_idx", "0"))
                        )
                        or self._latest_event_ref
                    ),
                    "trajectory_idx": str(event.get("trajectory_idx", "0")),
                    "components": {},
                    "requested_components": [],
                    "request_refs": [],
                    "authorization": dict(authorization or {}),
                    "result": "dispatch_incomplete",
                },
            )
            if component not in attempt["requested_components"]:
                attempt["requested_components"].append(component)
            event_id = _event_key(event)
            if event_id is not None and event_id not in attempt["request_refs"]:
                attempt["request_refs"].append(event_id)
            if before_code is not None or after_code is not None:
                prior = attempt["components"].get(component)
                attempt["components"][component] = {
                    "before": (
                        prior["before"]
                        if prior is not None
                        else before_code or ""
                    ),
                    "after": after_code if after_code is not None else "",
                }
            return True

    def record_public_generation_validation(
        self,
        attempt_key: str,
        event: Mapping[str, Any],
        *,
        result: str,
    ) -> bool:
        if (
            not isinstance(event, Mapping)
            or event.get("split") != "public"
            or event.get("evidence_view") != "agent_safe"
        ):
            return False
        with self._lock:
            if self._case is None or not self._active:
                return False
            attempt_data = self._generation_attempts.pop(attempt_key, None)
            if attempt_data is None:
                return False
            event_id = _event_key(event)
            if event_id is None:
                return False
            event = dict(event)
            components = tuple(sorted(attempt_data.get("components", {})))
            event.setdefault(
                "validation_contract",
                _validation_contract(
                    str(event.get("stage", "public_testbench_generation")),
                    "_".join(components) or "public_generation",
                ),
            )
            self._append_generation_event(event, advance=True)
            attempt_id = _stable_id(
                "attempt", self._case.case_id, attempt_key
            )
            if any(item.attempt_id == attempt_id for item in self._case.attempts):
                return False
            components = attempt_data["components"]
            changed_components = tuple(
                name
                for name, value in components.items()
                if value["before"] != value["after"]
            )
            changed = bool(changed_components)
            eligibility_ref = _stable_id(
                "eligibility", self._case.case_id, attempt_id
            )
            pre_action_event_ref = (
                attempt_data["pre_action_event_ref"]
                or self._generation_diagnostic_refs.get(
                    attempt_data["trajectory_idx"]
                )
                or self._latest_event_ref
            )
            authorization_ref = _stable_id(
                "authorization", self._case.case_id, attempt_id
            )
            auth_payload = {
                "decision_ref": authorization_ref,
                "decision_type": "public_generation_repair_gate",
                "eligible": True,
                "gate": attempt_data["authorization"],
                "evidence_view": "agent_safe",
                "hidden_content_persisted": False,
            }
            self._store.write_decision(
                self._case.case_id, authorization_ref, auth_payload
            )
            if changed:
                before_bundle = {
                    name: components[name]["before"]
                    for name in changed_components
                }
                after_bundle = {
                    name: components[name]["after"]
                    for name in changed_components
                }
                patch = "\n".join(
                    part
                    for name in changed_components
                    if (
                        part := _patch(
                            before_bundle[name],
                            after_bundle[name],
                            name=f"{name}.cpp",
                        )
                    )
                )
                if patch and len(patch.encode("utf-8")) <= _MAX_PATCH_BYTES:
                    before_text = json.dumps(
                        before_bundle, sort_keys=True, separators=(",", ":")
                    )
                    after_text = json.dumps(
                        after_bundle, sort_keys=True, separators=(",", ":")
                    )
                    self._store.write_change(
                        self._case.case_id,
                        StoredChange(
                            attempt_id=attempt_id,
                            components=changed_components,
                            before_sha256=_digest(before_text),
                            after_sha256=_digest(after_text),
                            change_fingerprint=_digest(patch),
                            patch=patch,
                        ),
                    )
            requested_components = tuple(
                dict.fromkeys(
                    (*attempt_data.get("requested_components", ()), *components)
                )
            )
            quota_buckets = tuple(
                "testbench_public" if item == "testbench" else "stub_public"
                for item in requested_components
            )
            snapshot = RepairEligibilitySnapshot(
                decision_ref=eligibility_ref,
                eligible=True,
                authorization_ref=authorization_ref,
                evidence_refs=tuple(filter(None, [pre_action_event_ref, event_id])),
            )
            case_attempt = CaseAttempt(
                attempt_id=attempt_id,
                sequence_index=len(self._case.attempts),
                result=result,
                pre_action_event_refs=(pre_action_event_ref or event_id,),
                post_validation_event_refs=(event_id,),
                authorization_ref=eligibility_ref,
                quota_buckets=quota_buckets or ("testbench_public",),
                changed=changed,
                repair_record_ref=_stable_id(
                    "generation-action", self._case.case_id, attempt_key
                ),
            )
            self._case = replace(
                self._case,
                attempts=(*self._case.attempts, case_attempt),
                eligibility_snapshots=(
                    *self._case.eligibility_snapshots,
                    snapshot,
                ),
            )
            self._store.update_case(self._case)
            return True

    def close_case(self, outcome: CaseOutcome | str, reason: str) -> bool:
        with self._lock:
            if self._case is None or self._case.status is CaseStatus.CLOSED:
                return False
            self._case = replace(
                self._case,
                status=CaseStatus.CLOSED,
                outcome=outcome,
                closure_reason=reason,
            )
            self._active = False
            self._store.update_case(self._case)
            if (
                self._case.outcome is CaseOutcome.RESOLVED
                and self._experience_root is not None
            ):
                experience_store = ExperienceStore(
                    self._experience_root
                )
                for derived in derive_experiences(
                    self._case,
                    changes=self._store.read_changes(self._case.case_id),
                    events=self._store.read_events(self._case.case_id),
                ):
                    experience_store.write(derived.record, derived.change)
            return True

    def finalize_public_generation_attempts(
        self, *, generation_id: str, result: str = "provider_error"
    ) -> int:
        with self._lock:
            attempt_keys = tuple(self._generation_attempts)
            finalized = 0
            for attempt_key in attempt_keys:
                event_id = _stable_id(
                    "event",
                    generation_id,
                    attempt_key,
                    "finalization",
                )
                event = {
                    "schema_version": 1,
                    "event_id": event_id,
                    "run_id": generation_id,
                    "validation_id": generation_id,
                    "stage": "public_testbench_generation",
                    "split": "public",
                    "component": "qualification",
                    "event_kind": "attempt_finalization",
                    "request_role": "repair",
                    "dispatched": False,
                    "outcome": result,
                    "accepted": False,
                    "evidence_view": "agent_safe",
                    "hidden_content_persisted": False,
                }
                if self.record_public_generation_validation(
                    attempt_key, event, result=result
                ):
                    finalized += 1
            return finalized

    def _append_generation_event(
        self, event: Mapping[str, Any], *, advance: bool = False
    ) -> bool:
        if (
            self._case is None
            or event.get("evidence_view") != "agent_safe"
            or event.get("split") != "public"
        ):
            return False
        event_id = _event_key(event)
        if event_id is None:
            return False
        events = self._store.read_events(self._case.case_id)
        if event_id not in events:
            self._store.write_event(self._case.case_id, event_id, event)
        refs = tuple(dict.fromkeys((*self._case.diagnostic_event_refs, event_id)))
        self._case = replace(self._case, diagnostic_event_refs=refs)
        self._store.update_case(self._case)
        if advance:
            self._latest_event_ref = event_id
        return True


def capture_candidate_recovery(
    *,
    root: str | Path,
    result: Any,
    experience_root: str | Path | None = None,
    derive_experience_records: bool = True,
) -> dict[str, Any]:
    """Persist a repair-aware candidate chain; ordinary successful runs are no-ops."""

    if not isinstance(derive_experience_records, bool):
        raise TypeError("derive_experience_records must be boolean")

    metadata = getattr(result, "metadata", {})
    raw_events = metadata.get("diagnostic_events", ()) if isinstance(metadata, Mapping) else ()
    events: dict[str, dict[str, Any]] = {}
    for event in raw_events:
        if (
            not isinstance(event, Mapping)
            or event.get("evidence_view") != "agent_safe"
            or "hidden" in str(event.get("stage", "")).casefold()
            or event.get("split", "public") != "public"
            or event.get("visibility", "public")
            not in {"public", "public_generation"}
        ):
            continue
        key = _event_key(event)
        if key is None:
            continue
        projected_event = dict(event)
        projected_event.setdefault("visibility", "public")
        events[key] = projected_event
    if not events:
        return {"case_count": 0, "experience_count": 0, "case_ids": []}

    repair_result = getattr(result, "repair_result", None)
    raw_attempts = tuple(getattr(repair_result, "attempts", ()))
    ordered_events = sorted(
        enumerate(events.values()),
        key=lambda item: (
            str(item[1].get("created_at", "")),
            item[0],
        ),
    )
    initial_event = ordered_events[0][1]
    validation_id = str(getattr(result, "validation_id", "validation"))
    run_id = str(initial_event.get("run_id", validation_id))
    case_id = _stable_id("case", run_id, validation_id, str(initial_event["event_id"]))
    event_records: dict[str, dict[str, Any]] = {key: value for key, value in events.items()}
    event_refs = [str(initial_event["event_id"])]
    attempts: list[CaseAttempt] = []
    changes: dict[str, StoredChange] = {}
    owner_assessments: list[OwnerAssessment] = []
    eligibility: list[RepairEligibilitySnapshot] = []
    latest_event_ref = event_refs[0]
    diagnostic_by_validation = {
        str(event.get("validation_id")): event for event in events.values()
    }
    for observation in tuple(getattr(result, "case_change_observations", ())):
        if (
            not isinstance(observation, Mapping)
            or observation.get("evidence_view", "agent_safe") != "agent_safe"
            or observation.get("visibility") != "public"
            or observation.get("component") != "testbench"
        ):
            continue
        pre_action_event_ref = latest_event_ref
        attempt_id = _stable_id(
            "attempt",
            case_id,
            str(observation.get("attempt_key", len(attempts))),
        )
        validation = observation.get("validation")
        post_refs: tuple[str, ...] = ()
        post_id: str | None = None
        if isinstance(validation, Mapping):
            stage = str(validation.get("stage", "unknown"))
            if "hidden" not in stage.casefold():
                diagnostic_ref = validation.get("diagnostic_event_ref")
                if isinstance(diagnostic_ref, str) and diagnostic_ref in events:
                    post_id = diagnostic_ref
                else:
                    post_id = _stable_id(
                        "validation",
                        case_id,
                        attempt_id,
                        stage,
                    )
                    event_records[post_id] = _validation_event(
                        event_id=post_id,
                        stage=stage,
                        accepted=validation.get("accepted") is True,
                        validation_id=str(
                            validation.get("validation_id", attempt_id)
                        ),
                        validation_contract=str(
                            validation.get("validation_contract")
                            or _validation_contract(stage, "testbench")
                        ),
                    )
                event_refs.append(post_id)
                post_refs = (post_id,)

        before_hash = observation.get("before_sha256")
        after_hash = observation.get("after_sha256")
        changed = (
            isinstance(before_hash, str)
            and isinstance(after_hash, str)
            and before_hash != after_hash
        )
        patch = observation.get("patch")
        change = None
        if changed and isinstance(patch, str) and patch:
            change = StoredChange(
                attempt_id=attempt_id,
                components=("testbench",),
                before_sha256=before_hash,
                after_sha256=after_hash,
                change_fingerprint=_digest(patch),
                patch=patch,
            )
            changes[attempt_id] = change

        evidence_refs = tuple(
            str(item)
            for item in events.get(pre_action_event_ref, {}).get(
                "evidence_refs", ()
            )
            if isinstance(item, str)
        ) or (pre_action_event_ref,)
        decision_ref = _stable_id("eligibility", case_id, attempt_id)
        eligibility.append(
            RepairEligibilitySnapshot(
                decision_ref=decision_ref,
                eligible=True,
                authorization_ref=None,
                evidence_refs=evidence_refs,
            )
        )
        attempts.append(
            CaseAttempt(
                attempt_id=attempt_id,
                sequence_index=len(attempts),
                result=str(observation.get("result", "dispatch_incomplete")),
                pre_action_event_refs=(pre_action_event_ref,),
                post_validation_event_refs=post_refs,
                authorization_ref=decision_ref,
                quota_buckets=("testbench_public",),
                changed=changed,
                repair_record_ref=_stable_id(
                    "repair",
                    str(observation.get("repair_record_ref", attempt_id)),
                ),
            )
        )
        if post_id is not None:
            latest_event_ref = post_id

    for index, attempt in enumerate(raw_attempts):
        pre_action_event_ref = latest_event_ref
        status = getattr(getattr(attempt, "status", None), "value", str(getattr(attempt, "status", "unknown")))
        model_called = (
            getattr(attempt, "model_response", None) is not None
            or getattr(attempt, "model_result", None) is not None
            or status not in {"budget_blocked"}
        )
        if not model_called:
            continue
        attempt_id = _stable_id("attempt", case_id, str(getattr(attempt, "attempt", index + 1)))
        before = getattr(attempt, "input_candidate", None)
        proposal = getattr(attempt, "proposal", None)
        validation = getattr(attempt, "validation_result", None)
        changed = bool(
            isinstance(before, str)
            and isinstance(proposal, str)
            and before != proposal
            and validation is not None
        )
        change = None
        if changed:
            patch = _patch(before, proposal, name="candidate.cpp")
            if patch is not None:
                change = StoredChange(
                    attempt_id=attempt_id,
                    components=("candidate",),
                    before_sha256=_digest(before),
                    after_sha256=_digest(proposal),
                    change_fingerprint=_digest(patch),
                    patch=patch,
                )
                changes[attempt_id] = change

        post_refs: tuple[str, ...] = ()
        post_id: str | None = None
        if validation is not None:
            passed = bool(getattr(validation, "passed", False))
            orchestration_validation_id = str(
                getattr(validation, "metadata", {}).get(
                    "orchestration_validation_id", ""
                )
            )
            diagnostic = diagnostic_by_validation.get(orchestration_validation_id)
            if diagnostic is not None:
                post_id = str(diagnostic["event_id"])
                post_event = dict(diagnostic)
                post_stage = post_event.get("stage")
                if isinstance(post_stage, str) and post_stage:
                    post_event.setdefault(
                        "validation_contract",
                        _validation_contract(post_stage, "candidate"),
                    )
                event_records[post_id] = post_event
                event_refs.append(post_id)
            else:
                stages = tuple(getattr(validation, "completed_stages", ()))
                safe_stages = tuple(
                    item
                    for item in stages
                    if "hidden" not in getattr(item, "value", str(item)).casefold()
                )
                stage_source = (
                    safe_stages[-1]
                    if passed and safe_stages
                    else (stages[-1] if stages else "unknown")
                )
                stage = getattr(stage_source, "value", str(stage_source))
                if "hidden" not in stage.casefold():
                    post_id = _stable_id("validation", case_id, str(getattr(attempt, "attempt", index + 1)), stage)
                    event_records[post_id] = _validation_event(
                        event_id=post_id,
                        stage=stage,
                        accepted=passed,
                        validation_id=orchestration_validation_id or validation_id,
                        validation_contract=_validation_contract(stage, "candidate"),
                    )
                    event_refs.append(post_id)
            if post_id is not None:
                post_refs = (post_id,)
        result_code = (
            "validated" if validation is not None and validation.passed
            else "validation_failed" if validation is not None
            else "provider_error" if status == "provider_error"
            else "response_rejected" if status == "response_rejected"
            else "dispatch_incomplete"
        )
        decision_ref = _stable_id("eligibility", case_id, attempt_id)
        diagnostic_refs = tuple(str(item) for item in events[pre_action_event_ref].get("evidence_refs", ()) if isinstance(item, str)) if pre_action_event_ref in events else (pre_action_event_ref,)
        eligibility.append(
            RepairEligibilitySnapshot(
                decision_ref=decision_ref,
                eligible=True,
                authorization_ref=None,
                evidence_refs=diagnostic_refs,
            )
        )
        attempts.append(
            CaseAttempt(
                attempt_id=attempt_id,
                sequence_index=len(attempts),
                result=result_code,
                pre_action_event_refs=(pre_action_event_ref,),
                post_validation_event_refs=post_refs,
                authorization_ref=decision_ref,
                quota_buckets=("candidate",),
                changed=changed,
                repair_record_ref=f"{validation_id}:{getattr(attempt, 'attempt', index + 1)}",
            )
        )
        if change is None:
            changes.pop(attempt_id, None)
        if post_id is not None:
            latest_event_ref = post_id

    if not eligibility:
        action = str(initial_event.get("route_action", "unknown")).casefold()
        evidence_refs = tuple(
            str(item)
            for item in initial_event.get("evidence_refs", ())
            if isinstance(item, str)
        )
        if evidence_refs:
            eligibility.append(
                RepairEligibilitySnapshot(
                    decision_ref=_stable_id("eligibility", case_id, str(initial_event["event_id"])),
                    eligible="repair" in action or action == "public_csynth_bounded_trial",
                    authorization_ref=None,
                    evidence_refs=evidence_refs,
                )
            )

    store = CaseStore(root)
    try:
        existing_case = store.read_case(case_id)
    except FileNotFoundError:
        existing_case = None
    if existing_case is not None:
        event_records.update(store.read_events(case_id))
        changes.update(store.read_changes(case_id))

    for event_id, event in event_records.items():
        if event_id in event_refs:
            store.write_event(case_id, event_id, event)
            if event_id in events:
                refs = tuple(
                    str(item)
                    for item in event.get("evidence_refs", ())
                    if isinstance(item, str)
                )
                owner_assessments.append(
                    OwnerAssessment(
                        diagnostic_event_ref=event_id,
                        owner=str(event.get("owner", "unknown")),
                        evidence_refs=refs,
                    )
                )
    accepted = bool(getattr(result, "accepted", False))
    status_value = getattr(getattr(result, "status", None), "value", str(getattr(result, "status", "unresolved")))
    case = DiagnosticCase(
        case_id=case_id,
        created_at=str(initial_event.get("created_at") or datetime.now(timezone.utc).isoformat()),
        status=CaseStatus.CLOSED,
        outcome=CaseOutcome.RESOLVED if accepted else CaseOutcome.UNRESOLVED,
        closure_reason="validated" if accepted else re.sub(r"[^a-z0-9_.-]+", "_", status_value.casefold()).strip("_.-") or "unresolved",
        diagnostic_event_refs=tuple(dict.fromkeys(event_refs)),
        attempts=tuple(attempts),
        owner_assessments=tuple(owner_assessments),
        eligibility_snapshots=tuple(eligibility),
    )
    for change in changes.values():
        store.write_change(case_id, change)
    if existing_case is not None and existing_case.status is CaseStatus.OPEN:
        saved_attempts = {
            attempt.attempt_id: attempt
            for attempt in existing_case.attempts
        }
        computed_attempts = {
            attempt.attempt_id: attempt for attempt in case.attempts
        }
        ordered_attempt_ids = tuple(
            dict.fromkeys(
                (
                    *(attempt.attempt_id for attempt in existing_case.attempts),
                    *(attempt.attempt_id for attempt in case.attempts),
                )
            )
        )
        merged_attempts_list = []
        for index, attempt_id in enumerate(ordered_attempt_ids):
            saved = saved_attempts.get(attempt_id)
            computed = computed_attempts.get(attempt_id)
            if saved is not None and computed is not None:
                attempt = replace(
                    saved,
                    result=computed.result,
                    post_validation_event_refs=(
                        computed.post_validation_event_refs
                    ),
                    changed=computed.changed,
                )
            else:
                attempt = saved or computed
            assert attempt is not None
            merged_attempts_list.append(
                replace(attempt, sequence_index=index)
            )
        merged_attempts = tuple(merged_attempts_list)
        owner_by_event = {
            item.diagnostic_event_ref: item
            for item in existing_case.owner_assessments
        }
        owner_by_event.update(
            {item.diagnostic_event_ref: item for item in case.owner_assessments}
        )
        eligibility_by_ref = {
            item.decision_ref: item for item in case.eligibility_snapshots
        }
        eligibility_by_ref.update(
            {
                item.decision_ref: item
                for item in existing_case.eligibility_snapshots
            }
        )
        case = replace(
            case,
            created_at=existing_case.created_at,
            diagnostic_event_refs=tuple(
                dict.fromkeys(
                    (*existing_case.diagnostic_event_refs, *case.diagnostic_event_refs)
                )
            ),
            attempts=merged_attempts,
            owner_assessments=tuple(owner_by_event.values()),
            eligibility_snapshots=tuple(eligibility_by_ref.values()),
        )
        store.update_case(case)
    else:
        store.write_case(case)

    if not derive_experience_records:
        return {
            "case_count": 1,
            "experience_count": 0,
            "case_ids": [case_id],
        }

    experience_store_root = (
        Path(experience_root)
        if experience_root is not None
        else Path(root).parent / "diagnostic_experiences"
    )
    derivation_changes = dict(changes)
    if (
        accepted
        and repair_result is not None
        and getattr(repair_result, "succeeded", False) is True
    ):
        initial_candidate = getattr(repair_result, "initial_candidate", None)
        final_candidate = getattr(repair_result, "last_validated_candidate", None)
        cursor = initial_candidate
        changed_attempt_ids: list[str] = []
        final_change_attempt_id: str | None = None
        chain_valid = isinstance(initial_candidate, str) and isinstance(final_candidate, str)
        for index, raw_attempt in enumerate(raw_attempts):
            before = getattr(raw_attempt, "input_candidate", None)
            proposal = getattr(raw_attempt, "proposal", None)
            validation = getattr(raw_attempt, "validation_result", None)
            if not chain_valid or before != cursor:
                chain_valid = False
                break
            if isinstance(proposal, str) and proposal != cursor:
                cursor = proposal
                if validation is not None:
                    attempt_id = _stable_id(
                        "attempt",
                        case_id,
                        str(getattr(raw_attempt, "attempt", index + 1)),
                    )
                    changed_attempt_ids.append(attempt_id)
                    final_change_attempt_id = attempt_id
                    if (
                        getattr(validation, "passed", False) is not True
                        and index == len(raw_attempts) - 1
                    ):
                        chain_valid = False
                        break
        if (
            chain_valid
            and cursor == final_candidate
            and changed_attempt_ids
            and final_change_attempt_id is not None
            and getattr(raw_attempts[-1].validation_result, "passed", False) is True
        ):
            cumulative_patch = _patch(
                initial_candidate,
                final_candidate,
                name="candidate.cpp",
            )
            if cumulative_patch is not None:
                derivation_changes[final_change_attempt_id] = StoredChange(
                    attempt_id=final_change_attempt_id,
                    components=("candidate",),
                    before_sha256=_digest(initial_candidate),
                    after_sha256=_digest(final_candidate),
                    change_fingerprint=_digest(cumulative_patch),
                    patch=cumulative_patch,
                    source_attempt_ids=tuple(changed_attempt_ids),
                )
    derived = derive_experiences(
        case,
        changes=derivation_changes,
        events=event_records,
    )
    experience_store = ExperienceStore(experience_store_root)
    for item in derived:
        experience_store.write(item.record, item.change)
    return {
        "case_count": 1,
        "experience_count": len(derived),
        "case_ids": [case_id],
    }
