"""Durable, ordered facts shared by one formal root run."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Any, Callable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_copy(value: Any) -> Any:
    return json.loads(
        json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
    )


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(_canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class RootRunRecordStore:
    """Persist one canonical event/Attempt timeline and Case references."""

    schema_version = 1

    def __init__(
        self,
        root: str | os.PathLike[str],
        run_id: str,
        *,
        clock: Callable[[], str] = _now,
    ) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be non-empty")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._root = Path(root)
        self._path = self._root / "root_run_record.json"
        self._run_id = run_id.strip()
        self._clock = clock
        self._lock = RLock()
        self._root.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            self._state = json.loads(self._path.read_text(encoding="utf-8"))
            if self._state.get("schema_version") != self.schema_version:
                raise ValueError("unsupported root run record schema")
            if self._state.get("run_id") != self._run_id:
                raise ValueError("root run record belongs to a different run")
        else:
            self._state = {
                "schema_version": self.schema_version,
                "run_id": self._run_id,
                "lifecycle": {
                    "status": "running",
                    "started_at": self._clock(),
                    "finished_at": None,
                },
                "next_sequence": 1,
                "events": {},
                "attempts": {},
                "cases": {},
                "relation_history": [],
                "attribution_decisions": {},
            }
            self._write()
        self._state.setdefault("validation_sessions", {})
        self._state.setdefault("validation_evidence", {})
        self._state.setdefault("attribution_decisions", {})

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def path(self) -> Path:
        return self._path

    def record_event(self, event: Mapping[str, Any]) -> bool:
        if not isinstance(event, Mapping):
            raise TypeError("event must be a mapping")
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("event requires a stable event_id")
        stage = str(event.get("stage", event.get("failed_stage", ""))).casefold()
        if (
            event.get("evidence_view") not in {None, "agent_safe"}
            or event.get("split", "public") != "public"
            or event.get("visibility", "public") not in {"public", "public_generation"}
            or "hidden" in stage
            or event.get("hidden_content_persisted") is True
        ):
            raise ValueError("only agent-safe, non-Hidden events can be recorded")
        payload = _json_copy(dict(event))
        payload.setdefault("evidence_view", "agent_safe")
        payload.setdefault("hidden_content_persisted", False)
        with self._lock:
            existing = self._state["events"].get(event_id)
            if existing is not None:
                if _canonical(existing["payload"]) != _canonical(payload):
                    raise ValueError(f"conflicting root event identity: {event_id}")
                return False
            sequence = self._next_sequence()
            self._state["events"][event_id] = {
                "sequence": sequence,
                "payload": payload,
                "relation": {"state": "unassigned", "case_ids": []},
            }
            self._write()
            return True

    def record_validation_session(self, session: Mapping[str, Any]) -> bool:
        if not isinstance(session, Mapping):
            raise TypeError("session must be a mapping")
        session_id = session.get("validation_id")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session requires a stable validation_id")
        if session.get("root_run_id") != self._run_id:
            raise ValueError("session root_run_id must match this root run")
        if (
            session.get("evidence_view") != "agent_safe"
            or session.get("hidden_content_persisted") is True
        ):
            raise ValueError("only agent-safe validation sessions can be recorded")
        attempt_id = session.get("attempt_id")
        if attempt_id is not None:
            if not isinstance(attempt_id, str):
                raise TypeError("session attempt_id must be a string or null")
            if self.attempt_record(attempt_id)["dispatch_state"] != "dispatched":
                raise ValueError("validation session Attempt must be dispatched")
        copied = _json_copy(dict(session))
        with self._lock:
            existing = self._state["validation_sessions"].get(session_id)
            if existing is not None:
                if _canonical(existing["payload"]) != _canonical(copied):
                    raise ValueError(
                        f"conflicting validation session identity: {session_id}"
                    )
                return False
            sequence = self._next_sequence()
            self._state["validation_sessions"][session_id] = {
                "sequence": sequence,
                "payload": copied,
            }
            self._write()
            return True

    def record_validation_evidence(self, evidence: Mapping[str, Any]) -> bool:
        if not isinstance(evidence, Mapping):
            raise TypeError("evidence must be a mapping")
        evidence_id = evidence.get("evidence_id")
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise ValueError("evidence requires a stable evidence_id")
        stage = str(evidence.get("stage", "")).casefold()
        state = str(evidence.get("state", "")).casefold()
        if (
            evidence.get("evidence_view") != "agent_safe"
            or evidence.get("split", "public") != "public"
            or evidence.get("hidden_content_persisted") is True
            or "hidden" in stage
            or "hidden" in state
        ):
            raise ValueError("Hidden validation evidence cannot be recorded")
        if evidence.get("root_run_id") != self._run_id:
            raise ValueError("evidence root_run_id must match this root run")
        validation_id = evidence.get("validation_id")
        if (
            not isinstance(validation_id, str)
            or validation_id not in self._state["validation_sessions"]
        ):
            raise ValueError("evidence must reference a recorded validation session")
        attempt_id = evidence.get("attempt_id")
        if attempt_id is not None:
            if not isinstance(attempt_id, str):
                raise TypeError("evidence attempt_id must be a string or null")
            if self.attempt_record(attempt_id)["dispatch_state"] != "dispatched":
                raise ValueError("evidence Attempt must be dispatched")
        copied = _json_copy(dict(evidence))
        with self._lock:
            existing = self._state["validation_evidence"].get(evidence_id)
            if existing is not None:
                if _canonical(existing["payload"]) != _canonical(copied):
                    raise ValueError(
                        f"conflicting validation evidence identity: {evidence_id}"
                    )
                return False
            sequence = self._next_sequence()
            self._state["validation_evidence"][evidence_id] = {
                "sequence": sequence,
                "payload": copied,
            }
            self._write()
            return True

    def append_attempt_evidence_ref(
        self, attempt_id: str, evidence_id: str
    ) -> bool:
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise ValueError("evidence_id must be non-empty")
        with self._lock:
            attempt = self._attempt(attempt_id)
            if attempt["dispatch_state"] != "dispatched":
                raise ValueError("only a dispatched Attempt can reference evidence")
            if evidence_id not in self._state["validation_evidence"]:
                raise KeyError(evidence_id)
            refs = attempt["details"].setdefault(
                "validation_evidence_refs", []
            )
            if evidence_id in refs:
                return False
            refs.append(evidence_id)
            self._write()
            return True

    def store_validation_artifact(self, content: bytes) -> dict[str, Any]:
        if not isinstance(content, bytes):
            raise TypeError("content must be bytes")
        digest = sha256(content).hexdigest()
        artifact_root = self._root / "validation_artifacts"
        if artifact_root.is_symlink():
            raise ValueError("validation artifact root must not be a symlink")
        path = artifact_root / digest[:2] / digest
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.parent.is_symlink() or path.is_symlink():
            raise ValueError("validation artifact path must not be a symlink")
        os.chmod(artifact_root, 0o700)
        os.chmod(path.parent, 0o700)
        if path.exists():
            if path.read_bytes() != content:
                raise ValueError("content-addressed validation artifact conflict")
        else:
            handle, temporary = tempfile.mkstemp(
                prefix=f".{digest}.", suffix=".tmp", dir=path.parent
            )
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                if path.exists():
                    if path.read_bytes() != content:
                        raise ValueError(
                            "content-addressed validation artifact conflict"
                        )
                    os.unlink(temporary)
                else:
                    os.replace(temporary, path)
                    os.chmod(path, 0o600)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return {
            "artifact_ref": f"sha256:{digest}",
            "sha256": digest,
            "size_bytes": len(content),
        }

    def read_validation_artifact(self, artifact_ref: str) -> bytes:
        if (
            not isinstance(artifact_ref, str)
            or not artifact_ref.startswith("sha256:")
        ):
            raise ValueError("unsupported validation artifact reference")
        digest = artifact_ref[7:]
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("invalid validation artifact reference")
        artifact_root = self._root / "validation_artifacts"
        path = artifact_root / digest[:2] / digest
        if (
            artifact_root.is_symlink()
            or path.parent.is_symlink()
            or path.is_symlink()
        ):
            raise ValueError("validation artifact path must not be a symlink")
        content = path.read_bytes()
        if sha256(content).hexdigest() != digest:
            raise ValueError("validation artifact digest mismatch")
        return content

    def validation_sessions(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            values = sorted(
                self._state["validation_sessions"].values(),
                key=lambda item: item["sequence"],
            )
            return tuple(_json_copy(item["payload"]) for item in values)

    def validation_evidence(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            values = sorted(
                self._state["validation_evidence"].values(),
                key=lambda item: item["sequence"],
            )
            evidence = []
            for item in values:
                payload = _json_copy(item["payload"])
                if (
                    payload.get("record_type") != "evidence_disposition"
                    and payload.get("schema_version") != self.schema_version
                ):
                    payload["legacy_evidence_incomplete"] = True
                evidence.append(payload)
            return tuple(evidence)

    def prepare_attempt(self, attempt_id: str, payload: Mapping[str, Any]) -> bool:
        if not isinstance(attempt_id, str) or not attempt_id.strip():
            raise ValueError("attempt_id must be non-empty")
        if not isinstance(payload, Mapping):
            raise TypeError("attempt payload must be a mapping")
        if payload.get("attempt_id") != attempt_id:
            raise ValueError("attempt payload must match its stable ID")
        copied = _json_copy(dict(payload))
        with self._lock:
            existing = self._state["attempts"].get(attempt_id)
            if existing is not None:
                if _canonical(existing["payload"]) != _canonical(copied):
                    raise ValueError(f"conflicting root Attempt identity: {attempt_id}")
                return False
            self._state["attempts"][attempt_id] = {
                "sequence": None,
                "dispatch_state": "prepared",
                "prepared_at": self._clock(),
                "dispatched_at": None,
                "payload": copied,
                "details": {},
                "relation": {"state": "unassigned", "case_ids": []},
            }
            self._write()
            return True

    def mark_attempt_dispatched(self, attempt_id: str) -> bool:
        with self._lock:
            attempt = self._attempt(attempt_id)
            if attempt["dispatch_state"] == "dispatched":
                return False
            if attempt["dispatch_state"] != "prepared":
                raise ValueError("only a prepared Attempt can be dispatched")
            attempt["dispatch_state"] = "dispatched"
            attempt["dispatched_at"] = self._clock()
            attempt["sequence"] = self._next_sequence()
            self._write()
            return True

    def cancel_prepared_attempt(self, attempt_id: str, reason: str) -> bool:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("cancellation reason must be non-empty")
        with self._lock:
            attempt = self._attempt(attempt_id)
            if attempt["dispatch_state"] == "not_dispatched":
                return False
            if attempt["dispatch_state"] != "prepared":
                raise ValueError("a dispatched Attempt cannot be cancelled")
            attempt["dispatch_state"] = "not_dispatched"
            attempt["cancel_reason"] = reason.strip()
            attempt["cancelled_at"] = self._clock()
            self._write()
            return True

    def update_attempt(self, attempt_id: str, details: Mapping[str, Any]) -> bool:
        if not isinstance(details, Mapping):
            raise TypeError("details must be a mapping")
        copied = _json_copy(dict(details))
        with self._lock:
            attempt = self._attempt(attempt_id)
            if attempt["dispatch_state"] != "dispatched":
                raise ValueError("only a dispatched Attempt can be updated")
            current = attempt["details"]
            for key, value in copied.items():
                if key in current and _canonical(current[key]) != _canonical(value):
                    raise ValueError(f"conflicting Attempt result field: {key}")
            changed = any(key not in current for key in copied)
            if not changed:
                return False
            current.update(copied)
            self._write()
            return True

    def attempt_record(self, attempt_id: str) -> dict[str, Any]:
        with self._lock:
            return _json_copy(self._attempt(attempt_id))

    def register_case(self, case_id: str, *, case_type: str = "formal") -> bool:
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("case_id must be non-empty")
        if not isinstance(case_type, str) or not case_type.strip():
            raise ValueError("case_type must be non-empty")
        with self._lock:
            existing = self._state["cases"].get(case_id)
            if existing is not None:
                if existing["case_type"] != case_type:
                    raise ValueError(f"conflicting Case identity: {case_id}")
                return False
            self._state["cases"][case_id] = {
                "case_type": case_type,
                "created_at": self._clock(),
            }
            self._write()
            return True

    def set_case_relation(
        self,
        record_type: str,
        record_id: str,
        *,
        state: str,
        case_ids: tuple[str, ...] | list[str] = (),
        basis: str,
    ) -> bool:
        if record_type not in {"event", "attempt"}:
            raise ValueError("record_type must be event or attempt")
        if state not in {"unassigned", "candidate", "confirmed", "shared"}:
            raise ValueError("unsupported Case relation state")
        ids = tuple(dict.fromkeys(case_ids))
        if state == "unassigned" and ids:
            raise ValueError("unassigned relation cannot name Cases")
        if state == "candidate" and not ids:
            raise ValueError("candidate relation requires at least one Case")
        if state == "confirmed" and len(ids) != 1:
            raise ValueError("confirmed relation requires exactly one Case")
        if state == "shared" and len(ids) < 2:
            raise ValueError("shared relation requires multiple Cases")
        if not isinstance(basis, str) or not basis.strip():
            raise ValueError("relation basis must be non-empty")
        with self._lock:
            collection = "events" if record_type == "event" else "attempts"
            record = self._state[collection].get(record_id)
            if record is None:
                raise KeyError(record_id)
            missing = set(ids) - set(self._state["cases"])
            if missing:
                raise KeyError(f"unknown Case references: {sorted(missing)}")
            relation = {"state": state, "case_ids": list(ids), "basis": basis}
            if _canonical(record["relation"]) == _canonical(relation):
                return False
            self._state["relation_history"].append(
                {
                    "record_type": record_type,
                    "record_id": record_id,
                    "previous": _json_copy(record["relation"]),
                    "current": relation,
                    "recorded_at": self._clock(),
                }
            )
            record["relation"] = relation
            self._write()
            return True

    def case_items(self, case_id: str) -> dict[str, tuple[str, ...]]:
        with self._lock:
            if case_id not in self._state["cases"]:
                raise KeyError(case_id)
            result = {"events": [], "attempts": []}
            for kind in result:
                collection = self._state[kind]
                for record_id, record in collection.items():
                    if (
                        kind == "attempts"
                        and record["dispatch_state"] != "dispatched"
                    ):
                        continue
                    if case_id in record["relation"].get("case_ids", ()):
                        result[kind].append(record_id)
            return {key: tuple(value) for key, value in result.items()}

    def record_relations(self, record_type: str, record_id: str) -> dict[str, Any]:
        collection = "events" if record_type == "event" else "attempts"
        with self._lock:
            record = self._state[collection].get(record_id)
            if record is None:
                raise KeyError(record_id)
            return _json_copy(record["relation"])

    def record_attribution_decision(
        self, decision_id: str, payload: Mapping[str, Any]
    ) -> bool:
        if not isinstance(decision_id, str) or not decision_id.strip():
            raise ValueError("decision_id must be non-empty")
        if not isinstance(payload, Mapping) or payload.get("decision_id") != decision_id:
            raise ValueError("decision payload must match its stable ID")
        if payload.get("root_run_id") != self._run_id:
            raise ValueError("decision root_run_id must match this root run")
        if (
            payload.get("evidence_view") != "agent_safe"
            or payload.get("hidden_content_persisted") is not False
        ):
            raise ValueError("only agent-safe attribution decisions can be recorded")
        copied = _json_copy(dict(payload))
        with self._lock:
            history = self._state["attribution_decisions"].setdefault(
                decision_id, []
            )
            if history and _canonical(history[-1]["payload"]) == _canonical(copied):
                return False
            history.append(
                {"recorded_at": self._clock(), "payload": copied}
            )
            self._write()
            return True

    def attribution_decisions(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            values = []
            for history in self._state["attribution_decisions"].values():
                values.extend(history)
            values.sort(key=lambda item: item["recorded_at"])
            return tuple(_json_copy(item["payload"]) for item in values)

    def timeline(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            items = []
            for record_id, event in self._state["events"].items():
                items.append({"sequence": event["sequence"], "kind": "event", "id": record_id})
            for record_id, attempt in self._state["attempts"].items():
                if attempt["dispatch_state"] == "dispatched":
                    items.append({"sequence": attempt["sequence"], "kind": "attempt", "id": record_id})
            for record_id, record in self._state.get(
                "validation_sessions", {}
            ).items():
                items.append(
                    {
                        "sequence": record["sequence"],
                        "kind": "validation_session",
                        "id": record_id,
                    }
                )
            for record_id, record in self._state.get(
                "validation_evidence", {}
            ).items():
                items.append(
                    {
                        "sequence": record["sequence"],
                        "kind": "validation_evidence",
                        "id": record_id,
                    }
                )
            return tuple(sorted(items, key=lambda item: item["sequence"]))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return _json_copy(self._state)

    def finish(self, status: str) -> None:
        if not isinstance(status, str) or not status.strip():
            raise ValueError("status must be non-empty")
        with self._lock:
            lifecycle = self._state["lifecycle"]
            if lifecycle["status"] != "running":
                if lifecycle["status"] == status:
                    return
                raise ValueError("root run lifecycle is already final")
            lifecycle["status"] = status
            lifecycle["finished_at"] = self._clock()
            self._write()

    def _next_sequence(self) -> int:
        sequence = self._state["next_sequence"]
        self._state["next_sequence"] = sequence + 1
        return sequence

    def _attempt(self, attempt_id: str) -> dict[str, Any]:
        attempt = self._state["attempts"].get(attempt_id)
        if attempt is None:
            raise KeyError(attempt_id)
        return attempt

    def _write(self) -> None:
        _atomic_json(self._path, self._state)
