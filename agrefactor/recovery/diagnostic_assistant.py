"""Bounded, evidence-citing diagnostic advice that cannot change routing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import re
from typing import Any

from agrefactor.models import ChatMessage, ModelCallRole


_OWNERS = frozenset({"candidate", "testbench", "stub", "unknown"})
_COMMAND = re.compile(
    r"(?:^|\s)(?:sudo|bash|sh|python\w*|rm|mv|cp|find|grep|cd|source)\b|"
    r"(?:/[^\s]+|[A-Za-z]:\\[^\s]+)",
    re.IGNORECASE,
)
_FIELDS = frozenset(
    {"possible_owner", "summary", "evidence_refs", "check_ids", "abstain"}
)


class DiagnosticAssistantOutputRejected(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DiagnosticAssistantRequest:
    stage: str
    evidence: tuple[Mapping[str, Any], ...]
    experiences: tuple[str, ...] = ()
    check_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.stage, str) or not self.stage.strip():
            raise ValueError("stage must be non-empty")
        evidence = tuple(dict(item) for item in self.evidence)
        ids = tuple(item.get("evidence_id") for item in evidence)
        if any(not isinstance(item, str) or not item for item in ids):
            raise ValueError("each evidence item requires an evidence_id")
        if len(ids) != len(set(ids)):
            raise ValueError("evidence_id values must be unique")
        experiences = tuple(self.experiences)
        if any(not isinstance(item, str) or not item.strip() for item in experiences):
            raise ValueError("experiences must contain non-empty text")
        checks = tuple(self.check_ids)
        if any(not isinstance(item, str) or not item.strip() for item in checks):
            raise ValueError("check_ids must contain non-empty strings")
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "experiences", experiences)
        object.__setattr__(self, "check_ids", tuple(dict.fromkeys(checks)))


@dataclass(frozen=True, slots=True)
class DiagnosticAssistantResult:
    possible_owner: str
    summary: str
    evidence_refs: tuple[str, ...]
    check_ids: tuple[str, ...]
    abstain: bool
    status: str = "suggestion_only"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "possible_owner": self.possible_owner,
            "summary": self.summary,
            "evidence_refs": list(self.evidence_refs),
            "check_ids": list(self.check_ids),
            "abstain": self.abstain,
            "authorization_changed": False,
            "owner_changed": False,
            "commands_provided": False,
        }


class DiagnosticAssistant:
    def __init__(self, model_adapter: Any, budget: Any) -> None:
        if not callable(getattr(model_adapter, "generate_auxiliary_text", None)):
            raise TypeError("model_adapter must provide generate_auxiliary_text")
        self._model_adapter = model_adapter
        self._budget = budget

    @staticmethod
    def messages(request: DiagnosticAssistantRequest) -> tuple[ChatMessage, ...]:
        payload = {
            "stage": request.stage,
            "evidence": [dict(item) for item in request.evidence],
            "historical_experiences": list(request.experiences),
            "registered_check_ids": list(request.check_ids),
        }
        return (
            ChatMessage(
                "system",
                "Analyze only the supplied current evidence and historical references. "
                "Return one JSON object with exactly possible_owner, summary, "
                "evidence_refs, check_ids, abstain. This is advice only: do not "
                "change owner, authorization, repair budget, validation contract, "
                "or route. Do not provide commands, patches, or file paths. "
                "Use only registered check IDs and evidence IDs from the input.",
            ),
            ChatMessage(
                "user",
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )

    def diagnose(self, request: DiagnosticAssistantRequest) -> DiagnosticAssistantResult:
        messages = self.messages(request)
        try:
            response = self._model_adapter.generate_auxiliary_text(
                messages,
                budget=self._budget,
                role=ModelCallRole.BOTTLENECK_DIAGNOSIS,
                evidence={
                    "stage": request.stage,
                    "evidence_ids": [item["evidence_id"] for item in request.evidence],
                    "experience_count": len(request.experiences),
                },
            )
            return self.parse(request, response.text)
        except Exception as exc:
            return DiagnosticAssistantResult(
                possible_owner="unknown",
                summary=type(exc).__name__,
                evidence_refs=(),
                check_ids=(),
                abstain=True,
                status="abstained",
            )

    @staticmethod
    def parse(
        request: DiagnosticAssistantRequest,
        text: str,
    ) -> DiagnosticAssistantResult:
        try:
            value = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise DiagnosticAssistantOutputRejected("invalid_json") from exc
        if not isinstance(value, dict) or set(value) != _FIELDS:
            raise DiagnosticAssistantOutputRejected("fields_not_exact")
        owner = value.get("possible_owner")
        summary = value.get("summary")
        refs = value.get("evidence_refs")
        checks = value.get("check_ids")
        abstain = value.get("abstain")
        if owner not in _OWNERS:
            raise DiagnosticAssistantOutputRejected("owner_invalid")
        if (
            not isinstance(summary, str)
            or not summary.strip()
            or len(summary) > 512
            or "\n" in summary
            or _COMMAND.search(summary)
        ):
            raise DiagnosticAssistantOutputRejected("summary_invalid")
        if not isinstance(refs, list) or any(not isinstance(item, str) for item in refs):
            raise DiagnosticAssistantOutputRejected("evidence_refs_invalid")
        if len(refs) != len(set(refs)) or not set(refs).issubset(
            {item["evidence_id"] for item in request.evidence}
        ):
            raise DiagnosticAssistantOutputRejected("evidence_ref_out_of_scope")
        if not isinstance(checks, list) or any(not isinstance(item, str) for item in checks):
            raise DiagnosticAssistantOutputRejected("check_ids_invalid")
        if len(checks) != len(set(checks)) or not set(checks).issubset(request.check_ids):
            raise DiagnosticAssistantOutputRejected("check_id_out_of_scope")
        if not isinstance(abstain, bool):
            raise DiagnosticAssistantOutputRejected("abstain_invalid")
        if not abstain and not refs:
            raise DiagnosticAssistantOutputRejected("citation_required")
        if abstain:
            owner = "unknown"
            refs = []
            checks = []
        return DiagnosticAssistantResult(
            possible_owner=owner,
            summary=summary.strip(),
            evidence_refs=tuple(refs),
            check_ids=tuple(checks),
            abstain=abstain,
        )
