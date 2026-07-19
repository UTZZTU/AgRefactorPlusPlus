"""Versioned, agent-safe vocabulary shared by repair executors."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
import json
from math import isfinite
import re
from typing import Any

from agrefactor.models import ModelResponse
from agrefactor.runtime.budget import BudgetUsage


REPAIR_PROTOCOL_SCHEMA_VERSION = 1
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class RepairArtifactRole(str, Enum):
    CANDIDATE = "candidate"
    TESTBENCH = "testbench"


class RepairTerminalStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXHAUSTED = "exhausted"
    ERROR = "error"
    BLOCKED = "blocked"
    TERMINAL = "terminal"


def _required_id(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    if _ID_RE.fullmatch(cleaned) is None:
        raise ValueError(
            f"{name} must contain only alphanumeric, '.', '_', ':', or '-' characters"
        )
    return cleaned


def _required_text(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    return cleaned


def _optional_text(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string or None")
    cleaned = value.strip()
    return cleaned or None


def _json_mapping(
    value: Mapping[str, Any],
    name: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        )
        copied = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{name} must contain finite JSON-serializable data"
        ) from exc
    if not isinstance(copied, dict):
        raise TypeError(f"{name} must normalize to an object")
    return copied


def model_response_to_safe_dict(
    response: ModelResponse | None,
) -> dict[str, Any] | None:
    if response is None:
        return None
    if not isinstance(response, ModelResponse):
        raise TypeError("response must be ModelResponse or None")
    return {
        "text": response.text,
        "model": response.model,
        "usage": {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
            "cost_usd": response.usage.cost_usd,
        },
        "finish_reason": response.finish_reason,
        "metadata": dict(response.metadata),
    }


@dataclass(frozen=True, slots=True)
class RepairModelObservation:
    prompt_manifest: Mapping[str, Any] = field(default_factory=dict)
    model_response: Mapping[str, Any] | None = None
    model_call_observed: bool = False

    def __post_init__(self) -> None:
        prompt = _json_mapping(
            self.prompt_manifest,
            "prompt_manifest",
        )
        response = self.model_response
        if response is not None:
            response = _json_mapping(
                response,
                "model_response",
            )
        if not isinstance(self.model_call_observed, bool):
            raise TypeError("model_call_observed must be boolean")
        if response is not None and not self.model_call_observed:
            raise ValueError(
                "model_response requires model_call_observed=true"
            )
        object.__setattr__(self, "prompt_manifest", prompt)
        object.__setattr__(self, "model_response", response)

    @classmethod
    def from_response(
        cls,
        *,
        prompt_manifest: Mapping[str, Any],
        response: ModelResponse | None,
        model_call_observed: bool,
    ) -> "RepairModelObservation":
        return cls(
            prompt_manifest=prompt_manifest,
            model_response=model_response_to_safe_dict(response),
            model_call_observed=model_call_observed,
        )

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "prompt_manifest": dict(self.prompt_manifest),
            "model_response": (
                None
                if self.model_response is None
                else dict(self.model_response)
            ),
            "model_call_observed": self.model_call_observed,
        }


@dataclass(frozen=True, slots=True)
class RepairObservedUsage:
    tool_calls: int = 0
    compile_calls: int = 0
    csynth_calls: int = 0
    csim_calls: int = 0
    llm_calls: int = 0
    tokens: int = 0
    cost_usd: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "tool_calls",
            "compile_calls",
            "csynth_calls",
            "csim_calls",
            "llm_calls",
            "tokens",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError(
                    f"{name} must be a non-negative integer"
                )
        if (
            isinstance(self.cost_usd, bool)
            or not isinstance(self.cost_usd, (int, float))
            or not isfinite(float(self.cost_usd))
            or self.cost_usd < 0
        ):
            raise ValueError(
                "cost_usd must be a finite non-negative number"
            )
        object.__setattr__(
            self,
            "cost_usd",
            float(self.cost_usd),
        )

    @classmethod
    def from_observations(
        cls,
        before: BudgetUsage | None,
        after: BudgetUsage | None,
        model: RepairModelObservation | None = None,
    ) -> "RepairObservedUsage":
        if (before is None) != (after is None):
            raise ValueError(
                "before and after must both be BudgetUsage or both be None"
            )
        deltas = {
            "tool_calls": 0,
            "compile_calls": 0,
            "csynth_calls": 0,
            "csim_calls": 0,
            "llm_calls": 0,
            "tokens": 0,
            "cost_usd": 0.0,
        }
        if before is not None:
            if not isinstance(before, BudgetUsage):
                raise TypeError("before must be BudgetUsage or None")
            if not isinstance(after, BudgetUsage):
                raise TypeError("after must be BudgetUsage or None")
            for name in (
                "tool_calls",
                "compile_calls",
                "csynth_calls",
                "csim_calls",
                "llm_calls",
                "tokens",
            ):
                delta = getattr(after, name) - getattr(before, name)
                if delta < 0:
                    raise ValueError(
                        f"observed budget delta is negative for {name}"
                    )
                deltas[name] = delta
            cost_delta = after.cost_usd - before.cost_usd
            if cost_delta < -1e-12:
                raise ValueError(
                    "observed budget delta is negative for cost_usd"
                )
            deltas["cost_usd"] = max(0.0, cost_delta)

        if model is not None:
            if not isinstance(model, RepairModelObservation):
                raise TypeError(
                    "model must be RepairModelObservation or None"
                )
            if model.model_call_observed:
                deltas["llm_calls"] = max(
                    deltas["llm_calls"],
                    1,
                )
            response = model.model_response
            if response is not None:
                usage = response.get("usage", {})
                if not isinstance(usage, Mapping):
                    raise TypeError(
                        "model response usage must be a mapping"
                    )
                total_tokens = usage.get("total_tokens", 0)
                if (
                    isinstance(total_tokens, bool)
                    or not isinstance(total_tokens, int)
                    or total_tokens < 0
                ):
                    raise ValueError(
                        "model response total_tokens is invalid"
                    )
                deltas["tokens"] = max(
                    deltas["tokens"],
                    total_tokens,
                )
                response_cost = usage.get("cost_usd")
                if response_cost is not None:
                    if (
                        isinstance(response_cost, bool)
                        or not isinstance(
                            response_cost,
                            (int, float),
                        )
                        or not isfinite(float(response_cost))
                        or response_cost < 0
                    ):
                        raise ValueError(
                            "model response cost_usd is invalid"
                        )
                    deltas["cost_usd"] = max(
                        float(deltas["cost_usd"]),
                        float(response_cost),
                    )

        return cls(**deltas)

    def to_dict(self) -> dict[str, int | float]:
        return {
            "tool_calls": self.tool_calls,
            "compile_calls": self.compile_calls,
            "csynth_calls": self.csynth_calls,
            "csim_calls": self.csim_calls,
            "llm_calls": self.llm_calls,
            "tokens": self.tokens,
            "cost_usd": self.cost_usd,
        }


def repair_attempt_id(
    run_id: str,
    sequence_index: int,
) -> str:
    run = _required_id(run_id, "run_id")
    if (
        isinstance(sequence_index, bool)
        or not isinstance(sequence_index, int)
        or sequence_index < 0
    ):
        raise ValueError(
            "sequence_index must be a non-negative integer"
        )
    return f"{run}.attempt-{sequence_index:03d}"


def repair_proposal_id(attempt_id: str) -> str:
    return f"{_required_id(attempt_id, 'attempt_id')}.proposal"


@dataclass(frozen=True, slots=True)
class CandidateRepairPayload:
    validation_summary: Mapping[str, Any]
    model_result_available: bool = False

    payload_type = "candidate_repair"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "validation_summary",
            _json_mapping(
                self.validation_summary,
                "validation_summary",
            ),
        )
        if not isinstance(
            self.model_result_available,
            bool,
        ):
            raise TypeError(
                "model_result_available must be boolean"
            )

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "validation_summary": dict(
                self.validation_summary
            ),
            "model_result_available": (
                self.model_result_available
            ),
        }


@dataclass(frozen=True, slots=True)
class TestbenchRepairPayload:
    preflight_summary: Mapping[str, Any]
    legacy_preflight_artifact_available: bool = True

    payload_type = "testbench_repair"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "preflight_summary",
            _json_mapping(
                self.preflight_summary,
                "preflight_summary",
            ),
        )
        if not isinstance(
            self.legacy_preflight_artifact_available,
            bool,
        ):
            raise TypeError(
                "legacy_preflight_artifact_available "
                "must be boolean"
            )

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "preflight_summary": dict(
                self.preflight_summary
            ),
            "legacy_preflight_artifact_available": (
                self.legacy_preflight_artifact_available
            ),
        }


RepairAttemptPayload = (
    CandidateRepairPayload
    | TestbenchRepairPayload
)


@dataclass(frozen=True, slots=True)
class RepairAttemptRecord:
    attempt_id: str
    proposal_id: str | None
    artifact_role: RepairArtifactRole
    sequence_index: int
    action: str
    status: str
    changed: bool
    model_observation: RepairModelObservation
    observed_usage: RepairObservedUsage
    payload: RepairAttemptPayload
    stop_reason: str | None = None
    terminal_status: RepairTerminalStatus | None = None
    evidence_view: str = "agent_safe"
    operator_artifact_available: bool = False
    error_type: str | None = None
    error_message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = REPAIR_PROTOCOL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        attempt_id = _required_id(
            self.attempt_id,
            "attempt_id",
        )
        proposal_id = self.proposal_id
        if proposal_id is not None:
            proposal_id = _required_id(
                proposal_id,
                "proposal_id",
            )
        role = (
            self.artifact_role
            if isinstance(self.artifact_role, RepairArtifactRole)
            else RepairArtifactRole(str(self.artifact_role))
        )
        if (
            isinstance(self.sequence_index, bool)
            or not isinstance(self.sequence_index, int)
            or self.sequence_index < 0
        ):
            raise ValueError(
                "sequence_index must be a non-negative integer"
            )
        action = _required_text(self.action, "action")
        status = _required_text(self.status, "status")
        if not isinstance(self.changed, bool):
            raise TypeError("changed must be boolean")
        if not isinstance(
            self.model_observation,
            RepairModelObservation,
        ):
            raise TypeError(
                "model_observation must be RepairModelObservation"
            )
        if not isinstance(
            self.observed_usage,
            RepairObservedUsage,
        ):
            raise TypeError(
                "observed_usage must be RepairObservedUsage"
            )
        if not isinstance(
            self.payload,
            (
                CandidateRepairPayload,
                TestbenchRepairPayload,
            ),
        ):
            raise TypeError(
                "payload must be a typed repair payload"
            )
        if (
            role is RepairArtifactRole.CANDIDATE
            and not isinstance(
                self.payload,
                CandidateRepairPayload,
            )
        ):
            raise ValueError(
                "candidate attempts require "
                "CandidateRepairPayload"
            )
        if (
            role is RepairArtifactRole.TESTBENCH
            and not isinstance(
                self.payload,
                TestbenchRepairPayload,
            )
        ):
            raise ValueError(
                "testbench attempts require "
                "TestbenchRepairPayload"
            )
        stop_reason = _optional_text(
            self.stop_reason,
            "stop_reason",
        )
        terminal = self.terminal_status
        if terminal is not None and not isinstance(
            terminal,
            RepairTerminalStatus,
        ):
            terminal = RepairTerminalStatus(str(terminal))
        if self.evidence_view != "agent_safe":
            raise ValueError(
                "repair protocol records must be agent_safe"
            )
        if not isinstance(
            self.operator_artifact_available,
            bool,
        ):
            raise TypeError(
                "operator_artifact_available must be boolean"
            )
        error_type = _optional_text(
            self.error_type,
            "error_type",
        )
        error_message = _optional_text(
            self.error_message,
            "error_message",
        )
        metadata = _json_mapping(
            self.metadata,
            "metadata",
        )
        if self.schema_version != REPAIR_PROTOCOL_SCHEMA_VERSION:
            raise ValueError(
                "unsupported repair protocol schema_version"
            )
        object.__setattr__(self, "attempt_id", attempt_id)
        object.__setattr__(self, "proposal_id", proposal_id)
        object.__setattr__(self, "artifact_role", role)
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "stop_reason",
            stop_reason,
        )
        object.__setattr__(
            self,
            "terminal_status",
            terminal,
        )
        object.__setattr__(
            self,
            "error_type",
            error_type,
        )
        object.__setattr__(
            self,
            "error_message",
            error_message,
        )
        object.__setattr__(self, "metadata", metadata)

    def to_safe_dict(self) -> dict[str, Any]:
        observation = self.model_observation.to_safe_dict()
        return {
            "schema_version": self.schema_version,
            "attempt_id": self.attempt_id,
            "proposal_id": self.proposal_id,
            "artifact_role": self.artifact_role.value,
            "sequence_index": self.sequence_index,
            "action": self.action,
            "status": self.status,
            "changed": self.changed,
            "prompt_manifest": observation["prompt_manifest"],
            "model_response": observation["model_response"],
            "model_call_observed": observation[
                "model_call_observed"
            ],
            "observed_usage": self.observed_usage.to_dict(),
            "payload_type": self.payload.payload_type,
            "payload": self.payload.to_safe_dict(),
            "stop_reason": self.stop_reason,
            "terminal_status": (
                None
                if self.terminal_status is None
                else self.terminal_status.value
            ),
            "evidence_view": self.evidence_view,
            "operator_artifact_available": (
                self.operator_artifact_available
            ),
            "error_type": self.error_type,
            "error_message": self.error_message,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class RepairRunRecord:
    run_id: str
    artifact_role: RepairArtifactRole
    terminal_status: RepairTerminalStatus
    stop_reason: str
    attempts: tuple[RepairAttemptRecord, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    evidence_view: str = "agent_safe"
    schema_version: int = REPAIR_PROTOCOL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        run_id = _required_id(self.run_id, "run_id")
        role = (
            self.artifact_role
            if isinstance(self.artifact_role, RepairArtifactRole)
            else RepairArtifactRole(str(self.artifact_role))
        )
        terminal = (
            self.terminal_status
            if isinstance(
                self.terminal_status,
                RepairTerminalStatus,
            )
            else RepairTerminalStatus(
                str(self.terminal_status)
            )
        )
        stop_reason = _required_text(
            self.stop_reason,
            "stop_reason",
        )
        attempts = tuple(self.attempts)
        if not all(
            isinstance(item, RepairAttemptRecord)
            for item in attempts
        ):
            raise TypeError(
                "attempts must contain RepairAttemptRecord"
            )
        ids = [item.attempt_id for item in attempts]
        if len(ids) != len(set(ids)):
            raise ValueError("attempt IDs must be unique")
        indices = [
            item.sequence_index
            for item in attempts
        ]
        if indices != sorted(indices):
            raise ValueError(
                "attempt sequence_index values must be ordered"
            )
        if len(indices) != len(set(indices)):
            raise ValueError(
                "attempt sequence_index values must be unique"
            )
        if any(
            item.artifact_role is not role
            for item in attempts
        ):
            raise ValueError(
                "all attempts must use the run artifact role"
            )
        if self.evidence_view != "agent_safe":
            raise ValueError(
                "repair run records must be agent_safe"
            )
        metadata = _json_mapping(
            self.metadata,
            "metadata",
        )
        if self.schema_version != REPAIR_PROTOCOL_SCHEMA_VERSION:
            raise ValueError(
                "unsupported repair protocol schema_version"
            )
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "artifact_role", role)
        object.__setattr__(
            self,
            "terminal_status",
            terminal,
        )
        object.__setattr__(
            self,
            "stop_reason",
            stop_reason,
        )
        object.__setattr__(self, "attempts", attempts)
        object.__setattr__(self, "metadata", metadata)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "artifact_role": self.artifact_role.value,
            "terminal_status": self.terminal_status.value,
            "stop_reason": self.stop_reason,
            "attempt_count": len(self.attempts),
            "attempts": [
                item.to_safe_dict()
                for item in self.attempts
            ],
            "evidence_view": self.evidence_view,
            "metadata": dict(self.metadata),
        }
