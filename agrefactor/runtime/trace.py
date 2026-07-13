"""Structured, append-only run traces for AgRefactor++."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """One immutable event in an AgRefactor++ run trace."""

    sequence: int
    timestamp: str
    event: str
    phase: str | None
    status: str | None
    message: str | None
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""

        return asdict(self)


class TraceRecorder:
    """Record ordered events and optionally persist each event as JSONL."""

    def __init__(
        self,
        run_id: str,
        *,
        task_id: str | None = None,
        output_path: str | Path | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._run_id = self._clean_required("run_id", run_id)
        self._task_id = self._clean_optional(task_id)
        self._clock = clock
        self._events: list[TraceEvent] = []
        self._output_path = Path(output_path) if output_path is not None else None

        if self._output_path is not None:
            self._output_path.parent.mkdir(parents=True, exist_ok=True)
            if self._output_path.exists() and self._output_path.stat().st_size > 0:
                raise FileExistsError(
                    f"Trace output already exists and is not empty: "
                    f"{self._output_path}"
                )

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def task_id(self) -> str | None:
        return self._task_id

    @property
    def events(self) -> tuple[TraceEvent, ...]:
        """Return an immutable snapshot of all recorded events."""

        return tuple(self._events)

    def record(
        self,
        event: str,
        *,
        phase: str | None = None,
        status: str | None = None,
        message: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> TraceEvent:
        """Append one event and persist it immediately when configured."""

        event_name = self._clean_required("event", event)
        clean_phase = self._clean_optional(phase)
        clean_status = self._clean_optional(status)
        clean_message = self._clean_optional(message)
        clean_metadata = self._normalize_metadata(metadata)

        timestamp = self._clock()
        if timestamp.tzinfo is None:
            raise ValueError("Trace clock must return a timezone-aware datetime")

        trace_event = TraceEvent(
            sequence=len(self._events) + 1,
            timestamp=timestamp.astimezone(timezone.utc).isoformat(),
            event=event_name,
            phase=clean_phase,
            status=clean_status,
            message=clean_message,
            metadata=clean_metadata,
        )

        self._events.append(trace_event)

        if self._output_path is not None:
            payload = {
                "run_id": self._run_id,
                "task_id": self._task_id,
                **trace_event.to_dict(),
            }
            with self._output_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
                file.write("\n")

        return trace_event

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of the complete trace."""

        return {
            "run_id": self._run_id,
            "task_id": self._task_id,
            "events": [event.to_dict() for event in self._events],
        }

    def write_json(self, path: str | Path) -> Path:
        """Write the complete trace snapshot as formatted JSON."""

        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(
                self.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return destination

    @staticmethod
    def _clean_required(name: str, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{name} must be a string")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{name} must not be empty")
        return cleaned

    @staticmethod
    def _clean_optional(value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("Optional trace text fields must be strings or None")
        cleaned = value.strip()
        return cleaned or None

    @staticmethod
    def _normalize_metadata(
        metadata: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if metadata is None:
            return {}

        copied = dict(metadata)
        try:
            serialized = json.dumps(copied, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise TypeError("Trace metadata must be JSON-serializable") from exc

        return json.loads(serialized)
