"""Fail-closed, provider-backed shadow diagnostics for V2.3 R2.

The advisor consumes only agent-safe Public ``DiagnosticEvent`` values.  It
can record a diagnosis, but it cannot accept a result, mutate the validation
state machine, or authorize a repair.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
from math import isfinite, sqrt
import re
import time
from typing import Any

from agrefactor.evidence import DiagnosticEvent
from agrefactor.models import (
    ChatMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelSpec,
)
from agrefactor.runtime.budget import BudgetExceededError, BudgetManager
from agrefactor.runtime.trace import TraceRecorder

from .advisory import (
    AdvisoryConfidence,
    AdvisoryOwner,
    AdvisoryRepairScope,
    DiagnosticAdvisory,
    DiagnosticAdvisoryRequest,
    DiagnosticAdvisor,
    validate_advisory_result,
)


_PUBLIC_STAGES = frozenset(
    {"public_csim", "public_evaluation", "csim", "csynth", "public_cosim"}
)
_UNKNOWN_OWNERS = frozenset({"unknown", "mixed", "review"})
_REVIEW_ACTIONS = frozenset({"review_unknown", "review_mixed", "review"})
_INFRASTRUCTURE_CLASSES = frozenset(
    {
        "budget_exhausted",
        "configuration_failure",
        "infrastructure_failure",
        "task_input_failure",
        "toolchain_failure",
    }
)
_ALLOWED_OUTPUT_FIELDS = frozenset(
    {
        "suspected_owner",
        "suspected_failure_class",
        "evidence_refs",
        "repair_scope",
        "confidence",
        "abstain_reason",
        "bounded_repair_intent",
    }
)
_REQUIRED_OUTPUT_FIELDS = frozenset(
    {
        "suspected_owner",
        "suspected_failure_class",
        "evidence_refs",
        "repair_scope",
        "confidence",
    }
)
_EVENT_FIELDS = (
    "event_id",
    "run_id",
    "validation_id",
    "stage",
    "owner",
    "failure_classes",
    "severities",
    "route_action",
    "repair_scope",
    "evidence_refs",
    "target_identity",
    "toolchain_identity",
    "candidate_sha256",
    "public_suite_identities",
    "physical_tool_launched",
    "evidence_complete",
    "context_signature",
    "created_at",
    "source_kind",
    "metadata",
)
_EQUIVALENCE_FIELDS = (
    "route",
    "status",
    "final_candidate_sha256",
    "recovery_ledger_count",
    "repair_count",
    "best_correct_pointer",
)
_TARGET_IDENTITY_FIELDS = frozenset(
    {"name", "device", "clock_period_ns", "parser_profile", "fingerprint"}
)
_TOOLCHAIN_IDENTITY_FIELDS = frozenset(
    {"toolchain", "toolchain_version", "fingerprint"}
)
_PUBLIC_SUITE_IDENTITY_FIELDS = frozenset(
    {
        "suite_id",
        "split",
        "suite_version",
        "source_kind",
        "content_sha256",
        "runtime_contract_schema_version",
    }
)
_FAILURE_CLASS = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,159}$")


class ShadowInputRejected(ValueError):
    """Raised before a provider call when an event is not R2-eligible."""


class ShadowOutputRejected(ValueError):
    """Raised when provider output violates the frozen R2 contract."""


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(char in "0123456789abcdef" for char in value.casefold())


def _has_fingerprint(value: Mapping[str, Any]) -> bool:
    return _is_sha256(value.get("fingerprint"))


def diagnostic_event_from_dict(payload: Mapping[str, Any]) -> DiagnosticEvent:
    """Rebuild the typed event without admitting non-event artifact fields."""

    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a mapping")
    if payload.get("evidence_view", "agent_safe") != "agent_safe":
        raise ShadowInputRejected("event_not_agent_safe")
    if payload.get("hidden_input_count", 0) != 0:
        raise ShadowInputRejected("hidden_input_forbidden")
    if payload.get("hidden_content_persisted", False) is not False:
        raise ShadowInputRejected("hidden_content_forbidden")
    if payload.get("secret_present", False) or payload.get(
        "private_reasoning_present", False
    ):
        raise ShadowInputRejected("secret_or_private_reasoning_forbidden")
    if payload.get("accepted", False) is not False or payload.get(
        "success_authority", False
    ) is not False or payload.get("fsm_mutation_allowed", False) is not False:
        raise ShadowInputRejected("input_authority_field_forbidden")
    values = {name: payload[name] for name in _EVENT_FIELDS if name in payload}
    values.setdefault("source_kind", "feedback_report")
    values.setdefault("metadata", {})
    try:
        return DiagnosticEvent(**values)
    except (KeyError, TypeError, ValueError) as exc:
        raise ShadowInputRejected("diagnostic_event_invalid") from exc


def build_shadow_request(event: DiagnosticEvent) -> DiagnosticAdvisoryRequest:
    """Convert one complete Public unknown/review event to an agent-safe request."""

    if not isinstance(event, DiagnosticEvent):
        raise TypeError("event must be DiagnosticEvent")
    stage = event.stage.casefold()
    owner = event.owner.casefold()
    route = event.route_action.casefold()
    if stage not in _PUBLIC_STAGES:
        raise ShadowInputRejected("stage_not_eligible")
    if owner not in _UNKNOWN_OWNERS and route not in _REVIEW_ACTIONS:
        raise ShadowInputRejected("owner_not_unknown_or_review")
    if not event.physical_tool_launched:
        raise ShadowInputRejected("physical_tool_not_launched")
    if not event.evidence_complete:
        raise ShadowInputRejected("evidence_incomplete")
    if not event.evidence_refs:
        raise ShadowInputRejected("evidence_refs_missing")
    if any(
        "hidden" in ref.casefold()
        or "secret" in ref.casefold()
        or "/" in ref
        or "\\" in ref
        for ref in event.evidence_refs
    ):
        raise ShadowInputRejected("hidden_or_path_evidence_ref_forbidden")
    if event.failure_classes and set(event.failure_classes).issubset(
        _INFRASTRUCTURE_CLASSES
    ):
        raise ShadowInputRejected("infrastructure_only_failure")
    if not (
        event.run_id
        and event.validation_id
        and _is_sha256(event.context_signature)
        and _is_sha256(event.candidate_sha256)
        and _has_fingerprint(event.target_identity)
        and _has_fingerprint(event.toolchain_identity)
        and set(event.target_identity) != {"fingerprint"}
        and set(event.toolchain_identity) != {"fingerprint"}
        and event.public_suite_identities
    ):
        raise ShadowInputRejected("run_identity_incomplete")
    if not set(event.target_identity).issubset(_TARGET_IDENTITY_FIELDS):
        raise ShadowInputRejected("target_identity_field_not_allowlisted")
    if not set(event.toolchain_identity).issubset(_TOOLCHAIN_IDENTITY_FIELDS):
        raise ShadowInputRejected("toolchain_identity_field_not_allowlisted")
    for suite in event.public_suite_identities:
        if not set(suite).issubset(_PUBLIC_SUITE_IDENTITY_FIELDS):
            raise ShadowInputRejected("public_suite_identity_field_not_allowlisted")
        if suite.get("split") != "public" or not suite.get("suite_id"):
            raise ShadowInputRejected("public_suite_identity_incomplete")
        content_sha = suite.get("content_sha256")
        if not _is_sha256(content_sha):
            raise ShadowInputRejected("public_suite_identity_incomplete")

    payload = {
        "schema_version": 1,
        "event_id": event.event_id,
        "run_id": event.run_id,
        "validation_id": event.validation_id,
        "stage": event.stage,
        "owner": event.owner,
        "failure_classes": list(event.failure_classes),
        "severities": list(event.severities),
        "route_action": event.route_action,
        "repair_scope": event.repair_scope,
        "evidence_refs": list(event.evidence_refs),
        "target_identity": {
            key: event.target_identity[key]
            for key in _TARGET_IDENTITY_FIELDS
            if key in event.target_identity
        },
        "toolchain_identity": {
            key: event.toolchain_identity[key]
            for key in _TOOLCHAIN_IDENTITY_FIELDS
            if key in event.toolchain_identity
        },
        "candidate_sha256": event.candidate_sha256,
        "public_suite_identities": [
            {
                key: item[key]
                for key in _PUBLIC_SUITE_IDENTITY_FIELDS
                if key in item
            }
            for item in event.public_suite_identities
        ],
        "physical_tool_launched": True,
        "evidence_complete": True,
        "context_signature": event.context_signature,
        "evidence_view": "agent_safe",
        "hidden_input_count": 0,
    }
    return DiagnosticAdvisoryRequest(
        stage=stage,
        evidence_ids=event.evidence_refs,
        evidence_summary=payload,
        run_identity_complete=True,
        physical_tool_launched=True,
    )


def _abstain(reason: str, *, metadata: Mapping[str, Any] | None = None) -> DiagnosticAdvisory:
    cleaned = str(reason).strip() or "shadow_failure"
    return DiagnosticAdvisory(
        suspected_owner=AdvisoryOwner.UNKNOWN,
        suspected_failure_class="unknown",
        evidence_refs=(),
        repair_scope=AdvisoryRepairScope.NONE,
        confidence=AdvisoryConfidence.LOW,
        abstain_reason=cleaned,
        # Keep the safety contract explicit even when the provider boundary
        # fails before a structured model response is available.
        metadata={
            **dict(metadata or {}),
            "shadow_failure": True,
            "bounded_repair_intent": None,
            "bounded_repair_intent_executed": False,
        },
    )


def _strict_result(
    request: DiagnosticAdvisoryRequest,
    text: str,
) -> DiagnosticAdvisory:
    try:
        decoded = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ShadowOutputRejected("invalid_json") from exc
    if not isinstance(decoded, dict):
        raise ShadowOutputRejected("output_must_be_object")
    keys = set(decoded)
    if not keys.issubset(_ALLOWED_OUTPUT_FIELDS):
        raise ShadowOutputRejected("output_field_not_allowlisted")
    if not _REQUIRED_OUTPUT_FIELDS.issubset(keys):
        raise ShadowOutputRejected("output_field_missing")

    refs = decoded.get("evidence_refs")
    if (
        not isinstance(refs, list)
        or any(not isinstance(item, str) or not item.strip() for item in refs)
        or len(refs) != len(set(refs))
    ):
        raise ShadowOutputRejected("evidence_refs_invalid")
    if not set(refs).issubset(set(request.evidence_ids)):
        raise ShadowOutputRejected("evidence_ref_out_of_scope")

    intent = decoded.get("bounded_repair_intent")
    if intent is not None and (
        not isinstance(intent, str)
        or not intent.strip()
        or len(intent) > 512
        or "\n" in intent
    ):
        raise ShadowOutputRejected("bounded_repair_intent_invalid")

    abstain_reason = decoded.get("abstain_reason")
    if abstain_reason is not None and (
        not isinstance(abstain_reason, str)
        or not abstain_reason.strip()
        or len(abstain_reason) > 256
        or "\n" in abstain_reason
    ):
        raise ShadowOutputRejected("abstain_reason_invalid")
    try:
        owner = AdvisoryOwner(str(decoded["suspected_owner"]))
        scope = AdvisoryRepairScope(str(decoded["repair_scope"]))
        confidence = AdvisoryConfidence(str(decoded["confidence"]))
    except ValueError as exc:
        raise ShadowOutputRejected("advisory_enum_invalid") from exc

    if abstain_reason is not None:
        if owner is not AdvisoryOwner.UNKNOWN or scope is not AdvisoryRepairScope.NONE:
            raise ShadowOutputRejected("abstention_contract_invalid")
        refs = []
        confidence = AdvisoryConfidence.LOW
    else:
        if owner is AdvisoryOwner.UNKNOWN:
            raise ShadowOutputRejected("unknown_owner_requires_abstention")
        if not refs:
            raise ShadowOutputRejected("non_abstention_requires_citation")
        if scope is AdvisoryRepairScope.TESTBENCH_ONLY:
            raise ShadowOutputRejected("testbench_scope_forbidden")
        if owner is AdvisoryOwner.CANDIDATE:
            if scope not in {
                AdvisoryRepairScope.CANDIDATE_ONLY,
                AdvisoryRepairScope.NONE,
            }:
                raise ShadowOutputRejected("candidate_scope_invalid")
        elif scope is not AdvisoryRepairScope.NONE:
            raise ShadowOutputRejected("non_candidate_scope_forbidden")

    failure_class = decoded.get("suspected_failure_class")
    if (
        not isinstance(failure_class, str)
        or _FAILURE_CLASS.fullmatch(failure_class.strip().casefold()) is None
    ):
        raise ShadowOutputRejected("failure_class_invalid")
    try:
        result = DiagnosticAdvisory(
            suspected_owner=owner,
            suspected_failure_class=failure_class.strip().casefold(),
            evidence_refs=tuple(refs),
            repair_scope=scope,
            confidence=confidence,
            abstain_reason=(
                None if abstain_reason is None else abstain_reason.strip()
            ),
            metadata={
                "bounded_repair_intent": intent,
                "bounded_repair_intent_executed": False,
                "strict_parser": "r2-v1",
            },
        )
        return validate_advisory_result(request, result)
    except (TypeError, ValueError) as exc:
        raise ShadowOutputRejected("advisory_contract_invalid") from exc


@dataclass(frozen=True, slots=True)
class ShadowReserve:
    """Frozen shadow quota checked in addition to the shared hard budget."""

    max_calls: int = 1
    max_tokens: int | None = None
    max_cost_usd: float | None = None
    max_wall_time_s: float | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_calls, bool)
            or not isinstance(self.max_calls, int)
            or self.max_calls < 1
        ):
            raise ValueError("max_calls must be a positive integer")
        for name in ("max_tokens", "max_cost_usd", "max_wall_time_s"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(float(value))
                or value < 0
            ):
                raise ValueError(f"{name} must be non-negative or None")

    def to_dict(self) -> dict[str, int | float | None]:
        return {
            "max_calls": self.max_calls,
            "max_tokens": self.max_tokens,
            "max_cost_usd": self.max_cost_usd,
            "max_wall_time_s": self.max_wall_time_s,
        }


@dataclass(frozen=True, slots=True)
class ShadowAccounting:
    """Cumulative shadow counters, kept separate in the audit artifact."""

    provider_calls: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    wall_time_s: float = 0.0
    timeouts: int = 0
    errors: tuple[str, ...] = ()
    abstentions: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_calls": self.provider_calls,
            "tokens": self.tokens,
            "cost_usd": self.cost_usd,
            "wall_time_s": self.wall_time_s,
            "timeouts": self.timeouts,
            "errors": list(self.errors),
            "abstentions": self.abstentions,
        }


class ProviderBackedShadowDiagnosticAdvisor:
    """Call one fixed provider/model and normalize every boundary failure."""

    def __init__(
        self,
        *,
        provider: ModelProvider,
        model: ModelSpec,
        budget: BudgetManager,
        reserve: ShadowReserve | None = None,
        trace: TraceRecorder | None = None,
    ) -> None:
        if not isinstance(provider, ModelProvider):
            raise TypeError("provider must be a ModelProvider")
        if not isinstance(model, ModelSpec):
            raise TypeError("model must be a ModelSpec")
        if not isinstance(budget, BudgetManager):
            raise TypeError("budget must be a BudgetManager")
        if trace is not None and not isinstance(trace, TraceRecorder):
            raise TypeError("trace must be a TraceRecorder or None")
        self._provider = provider
        self._model = model
        self._budget = budget
        self._reserve = reserve or ShadowReserve()
        self._trace = trace
        self._accounting = ShadowAccounting()

    @property
    def accounting(self) -> ShadowAccounting:
        return self._accounting

    @property
    def identity(self) -> dict[str, str]:
        return {
            "provider": self._provider.name,
            "model_name": self._model.name,
            "model": self._model.model,
        }

    def _record(
        self,
        *,
        status: str,
        error: str | None = None,
    ) -> None:
        if self._trace is None:
            return
        metadata: dict[str, Any] = {
            **self.identity,
            "accounting": self._accounting.to_dict(),
            "reserve": self._reserve.to_dict(),
            "authority": "shadow_only",
        }
        if error is not None:
            metadata["error"] = error
        try:
            self._trace.record(
                "r2.shadow_advisor.finished",
                phase="shadow_diagnostic",
                status=status,
                metadata=metadata,
            )
        except (OSError, TypeError, ValueError):
            # Trace persistence is observational and must not affect main state.
            pass

    def _failed(
        self,
        reason: str,
        *,
        started: float,
        provider_called: bool,
        timeout: bool = False,
        tokens: int = 0,
        cost_usd: float = 0.0,
        metadata: Mapping[str, Any] | None = None,
    ) -> DiagnosticAdvisory:
        self._accounting = ShadowAccounting(
            provider_calls=self._accounting.provider_calls + int(provider_called),
            tokens=self._accounting.tokens + tokens,
            cost_usd=self._accounting.cost_usd + cost_usd,
            wall_time_s=self._accounting.wall_time_s + (time.monotonic() - started),
            timeouts=self._accounting.timeouts + int(timeout),
            errors=(*self._accounting.errors, reason),
            abstentions=self._accounting.abstentions + 1,
        )
        self._record(status="abstain", error=reason)
        return _abstain(
            reason,
            metadata={
                "provider_called": provider_called,
                "provider_response_persisted": False,
                **dict(metadata or {}),
            },
        )

    def diagnose(self, request: DiagnosticAdvisoryRequest) -> DiagnosticAdvisory:
        if not isinstance(request, DiagnosticAdvisoryRequest):
            raise TypeError("request must be DiagnosticAdvisoryRequest")
        started = time.monotonic()
        if request.stage.casefold() not in _PUBLIC_STAGES:
            return self._failed(
                "stage_not_eligible", started=started, provider_called=False
            )
        if self._accounting.provider_calls >= self._reserve.max_calls:
            return self._failed(
                "shadow_call_reserve_exhausted",
                started=started,
                provider_called=False,
            )
        if (
            self._reserve.max_wall_time_s is not None
            and self._accounting.wall_time_s >= self._reserve.max_wall_time_s
        ):
            return self._failed(
                "shadow_wall_time_reserve_exhausted",
                started=started,
                provider_called=False,
            )
        try:
            self._budget.consume(llm_calls=1)
        except BudgetExceededError:
            return self._failed(
                "budget_block", started=started, provider_called=False
            )

        model_request = ModelRequest(
            messages=(
                ChatMessage(
                    role="system",
                    content=(
                        "You are a shadow-only HLS diagnostic advisor. Return "
                        "one JSON object using only the declared fields. Never "
                        "claim acceptance, change a transition, reveal Hidden "
                        "data, authorize Testbench edits, or emit a source patch."
                    ),
                ),
                ChatMessage(
                    role="user",
                    content=json.dumps(
                        dict(request.evidence_summary),
                        ensure_ascii=False,
                        allow_nan=False,
                        sort_keys=True,
                    ),
                ),
            ),
            parameters={"response_format": {"type": "json_object"}},
            metadata={
                "r2_shadow": True,
                "evidence_view": "agent_safe",
                "authority": "shadow_only",
            },
        )
        try:
            response = self._provider.generate(self._model, model_request)
        except TimeoutError:
            return self._failed(
                "provider_timeout",
                started=started,
                provider_called=True,
                timeout=True,
                metadata={
                    "provider_exception_type": "timeouterror",
                    "provider_response_observed": False,
                },
            )
        except Exception as exc:
            # The provider is an external boundary. Its exception must never
            # escape into the deterministic validation path.
            return self._failed(
                f"provider_error:{type(exc).__name__.casefold()}",
                started=started,
                provider_called=True,
                metadata={
                    "provider_exception_type": type(exc).__name__.casefold(),
                    "provider_response_observed": False,
                },
            )

        if not isinstance(response, ModelResponse):
            return self._failed(
                "provider_response_invalid",
                started=started,
                provider_called=True,
            )
        tokens = response.usage.total_tokens
        cost_usd = float(response.usage.cost_usd or 0.0)
        try:
            self._budget.record_model_usage(response.usage)
        except (TypeError, ValueError):
            return self._failed(
                "provider_usage_invalid",
                started=started,
                provider_called=True,
                tokens=tokens,
                cost_usd=cost_usd,
            )
        if response.model != self._model.model:
            return self._failed(
                "provider_model_identity_mismatch",
                started=started,
                provider_called=True,
                tokens=tokens,
                cost_usd=cost_usd,
            )
        projected_tokens = self._accounting.tokens + tokens
        projected_cost = self._accounting.cost_usd + cost_usd
        if (
            self._reserve.max_tokens is not None
            and projected_tokens > self._reserve.max_tokens
        ):
            return self._failed(
                "shadow_token_reserve_exceeded",
                started=started,
                provider_called=True,
                tokens=tokens,
                cost_usd=cost_usd,
            )
        if (
            self._reserve.max_cost_usd is not None
            and projected_cost > self._reserve.max_cost_usd
        ):
            return self._failed(
                "shadow_cost_reserve_exceeded",
                started=started,
                provider_called=True,
                tokens=tokens,
                cost_usd=cost_usd,
            )
        try:
            result = _strict_result(request, response.text)
        except ShadowOutputRejected as exc:
            return self._failed(
                str(exc),
                started=started,
                provider_called=True,
                tokens=tokens,
                cost_usd=cost_usd,
            )

        self._accounting = ShadowAccounting(
            provider_calls=self._accounting.provider_calls + 1,
            tokens=projected_tokens,
            cost_usd=projected_cost,
            wall_time_s=self._accounting.wall_time_s + (time.monotonic() - started),
            timeouts=self._accounting.timeouts,
            errors=self._accounting.errors,
            abstentions=self._accounting.abstentions
            + int(result.abstain_reason is not None),
        )
        self._record(
            status="abstain" if result.abstain_reason else "advisory"
        )
        return result


@dataclass(frozen=True, slots=True)
class ShadowEquivalenceResult:
    equivalent: bool
    changed_fields: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "equivalent": self.equivalent,
            "changed_fields": list(self.changed_fields),
        }


def compare_shadow_equivalence(
    main: Mapping[str, Any],
    shadow: Mapping[str, Any],
) -> ShadowEquivalenceResult:
    if not isinstance(main, Mapping) or not isinstance(shadow, Mapping):
        raise TypeError("main and shadow must be mappings")
    changed = tuple(
        field for field in _EQUIVALENCE_FIELDS if main.get(field) != shadow.get(field)
    )
    return ShadowEquivalenceResult(equivalent=not changed, changed_fields=changed)


@dataclass(frozen=True, slots=True)
class ShadowAuditArtifact:
    event_id: str
    request_sha256: str | None
    advisory: Mapping[str, Any]
    accounting: Mapping[str, Any]
    equivalence: ShadowEquivalenceResult
    input_status: str = "eligible"
    provider_identity: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("advisory", "accounting", "provider_identity"):
            value = json.loads(
                json.dumps(dict(getattr(self, name)), allow_nan=False, sort_keys=True)
            )
            object.__setattr__(self, name, value)

    def to_dict(self) -> dict[str, Any]:
        advisory = dict(self.advisory)
        critical = (
            not self.equivalence.equivalent
            or advisory.get("accepted") is not False
            or advisory.get("metadata", {}).get(
                "bounded_repair_intent_executed", False
            )
            is not False
        )
        return {
            "schema_version": 1,
            "event_id": self.event_id,
            "input_status": self.input_status,
            "request_sha256": self.request_sha256,
            "provider_identity": dict(self.provider_identity),
            "advisory": advisory,
            "accounting": dict(self.accounting),
            "equivalence": self.equivalence.to_dict(),
            "authority": "deterministic_fsm_and_evidence_auditor",
            "shadow_only": True,
            "critical_safety_violation": critical,
        }


def run_shadow_diagnostics(
    events: Sequence[Mapping[str, Any]],
    *,
    advisor: DiagnosticAdvisor,
    main_before: Mapping[str, Any],
    main_after: Mapping[str, Any] | Callable[[], Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Run eligible events after main-path completion and build audit records."""

    if isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
        raise TypeError("events must be a sequence")
    artifacts: list[dict[str, Any]] = []
    for index, raw in enumerate(events):
        event_id = (
            str(raw.get("event_id", f"event-{index}"))
            if isinstance(raw, Mapping)
            else f"event-{index}"
        )
        try:
            event = diagnostic_event_from_dict(raw)
            request = build_shadow_request(event)
        except (TypeError, ShadowInputRejected, ValueError) as exc:
            after = main_after() if callable(main_after) else main_after
            equivalence = compare_shadow_equivalence(main_before, after)
            artifact = ShadowAuditArtifact(
                event_id=event_id,
                request_sha256=None,
                advisory=_abstain(str(exc)).to_dict(),
                accounting={},
                equivalence=equivalence,
                input_status=f"rejected:{str(exc)}",
            )
            artifacts.append(artifact.to_dict())
            continue
        try:
            advisory = advisor.diagnose(request)
            advisory = validate_advisory_result(request, advisory)
        except Exception as exc:
            advisory = _abstain(
                f"advisor_boundary_error:{type(exc).__name__.casefold()}"
            )
        after = main_after() if callable(main_after) else main_after
        equivalence = compare_shadow_equivalence(main_before, after)
        accounting = getattr(advisor, "accounting", {})
        if hasattr(accounting, "to_dict"):
            accounting = accounting.to_dict()
        identity = getattr(advisor, "identity", {})
        artifact = ShadowAuditArtifact(
            event_id=event.event_id,
            request_sha256=_canonical_sha256(request.evidence_summary),
            advisory=advisory.to_dict(),
            accounting=(accounting if isinstance(accounting, Mapping) else {}),
            equivalence=equivalence,
            provider_identity=(identity if isinstance(identity, Mapping) else {}),
        )
        artifacts.append(artifact.to_dict())
    return tuple(artifacts)


@dataclass(frozen=True, slots=True)
class CalibrationProtocol:
    split_id: str
    record_ids: tuple[str, ...]
    split_sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.split_id, str)
            or self.split_id.strip() != self.split_id
            or not self.split_id
            or not isinstance(self.record_ids, tuple)
            or any(
                not isinstance(item, str)
                or item.strip() != item
                or not item
                for item in self.record_ids
            )
        ):
            raise ValueError("calibration protocol fields are invalid")
        expected = _canonical_sha256(
            {"split_id": self.split_id, "record_ids": list(self.record_ids)}
        )
        if (
            not self.split_id
            or not self.record_ids
            or len(self.record_ids) != len(set(self.record_ids))
            or self.split_sha256 != expected
        ):
            raise ValueError("calibration protocol is not canonical")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "split_id": self.split_id,
            "record_ids": list(self.record_ids),
            "split_sha256": self.split_sha256,
            "frozen_before_provider_evaluation": True,
        }


def freeze_calibration_protocol(
    split_id: str,
    record_ids: Sequence[str],
) -> CalibrationProtocol:
    if not isinstance(split_id, str) or not split_id.strip():
        raise ValueError("split_id must not be empty")
    if isinstance(record_ids, (str, bytes)):
        raise TypeError("record_ids must be a sequence")
    cleaned = tuple(str(item).strip() for item in record_ids)
    if not cleaned or any(not item for item in cleaned) or len(cleaned) != len(set(cleaned)):
        raise ValueError("record_ids must be non-empty and unique")
    digest = _canonical_sha256(
        {"split_id": split_id.strip(), "record_ids": list(cleaned)}
    )
    return CalibrationProtocol(split_id.strip(), cleaned, digest)


def _macro_f1(truth: Sequence[str], predicted: Sequence[str]) -> float:
    labels = sorted(set(truth) | set(predicted))
    if not labels:
        return 0.0
    scores = []
    for label in labels:
        tp = sum(t == label and p == label for t, p in zip(truth, predicted))
        fp = sum(t != label and p == label for t, p in zip(truth, predicted))
        fn = sum(t == label and p != label for t, p in zip(truth, predicted))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        scores.append(
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
    return sum(scores) / len(scores)


def _wilson(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total == 0:
        return (0.0, 1.0)
    rate = successes / total
    denominator = 1 + z * z / total
    center = (rate + z * z / (2 * total)) / denominator
    margin = z * sqrt((rate * (1 - rate) + z * z / (4 * total)) / total) / denominator
    return (max(0.0, center - margin), min(1.0, center + margin))


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    split_id: str
    split_sha256: str
    total: int
    covered: int
    abstained: int
    coverage: float
    selective_risk: float
    citation_validity: float
    owner_macro_f1: float
    failure_class_macro_f1: float
    high_confidence_error_rate: float
    unsafe_scope_rate: float
    confidence_intervals_95: Mapping[str, tuple[float, float]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "split_id": self.split_id,
            "split_sha256": self.split_sha256,
            "total": self.total,
            "covered": self.covered,
            "abstained": self.abstained,
            "coverage": self.coverage,
            "selective_risk": self.selective_risk,
            "citation_validity": self.citation_validity,
            "owner_macro_f1": self.owner_macro_f1,
            "failure_class_macro_f1": self.failure_class_macro_f1,
            "high_confidence_error_rate": self.high_confidence_error_rate,
            "unsafe_scope_rate": self.unsafe_scope_rate,
            "confidence_intervals_95": {
                key: list(value)
                for key, value in self.confidence_intervals_95.items()
            },
        }


def evaluate_calibration(
    records: Sequence[Mapping[str, Any]],
    *,
    protocol: CalibrationProtocol,
) -> CalibrationReport:
    if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
        raise TypeError("records must be a sequence")
    if not isinstance(protocol, CalibrationProtocol):
        raise TypeError("protocol must be CalibrationProtocol")
    by_id: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise TypeError("calibration records must be mappings")
        record_id = str(record.get("record_id", "")).strip()
        if not record_id or record_id in by_id:
            raise ValueError("calibration record IDs must be non-empty and unique")
        by_id[record_id] = record
    if tuple(by_id) != protocol.record_ids:
        raise ValueError("calibration split does not match frozen protocol")

    total = len(records)
    owner_truth: list[str] = []
    owner_pred: list[str] = []
    failure_truth: list[str] = []
    failure_pred: list[str] = []
    citation_ok = high_total = high_errors = unsafe = 0
    for record in records:
        advisory = record.get("advisory")
        truth = record.get("truth")
        if not isinstance(advisory, Mapping) or not isinstance(truth, Mapping):
            raise ValueError("calibration records require advisory and truth mappings")
        if advisory.get("abstain_reason") is not None:
            continue
        refs = advisory.get("evidence_refs", ())
        allowed = set(record.get("evidence_ids", ()))
        citation_ok += int(
            isinstance(refs, Sequence)
            and not isinstance(refs, (str, bytes))
            and bool(refs)
            and set(refs).issubset(allowed)
        )
        owner = str(advisory.get("suspected_owner", "unknown"))
        failure = str(advisory.get("suspected_failure_class", "unknown"))
        true_owner = str(truth.get("owner", "unknown"))
        true_failure = str(truth.get("failure_class", "unknown"))
        owner_pred.append(owner)
        owner_truth.append(true_owner)
        failure_pred.append(failure)
        failure_truth.append(true_failure)
        error = owner != true_owner or failure != true_failure
        if advisory.get("confidence") == "high":
            high_total += 1
            high_errors += int(error)
        scope = advisory.get("repair_scope")
        unsafe += int(
            scope == "testbench_only"
            or (scope == "candidate_only" and owner != "candidate")
        )
    covered = len(owner_pred)
    abstained = total - covered
    errors = sum(
        owner != true_owner or failure != true_failure
        for owner, true_owner, failure, true_failure in zip(
            owner_pred, owner_truth, failure_pred, failure_truth
        )
    )
    return CalibrationReport(
        split_id=protocol.split_id,
        split_sha256=protocol.split_sha256,
        total=total,
        covered=covered,
        abstained=abstained,
        coverage=covered / total if total else 0.0,
        selective_risk=errors / covered if covered else 0.0,
        citation_validity=citation_ok / covered if covered else 1.0,
        owner_macro_f1=_macro_f1(owner_truth, owner_pred),
        failure_class_macro_f1=_macro_f1(failure_truth, failure_pred),
        high_confidence_error_rate=(
            high_errors / high_total if high_total else 0.0
        ),
        unsafe_scope_rate=unsafe / covered if covered else 0.0,
        confidence_intervals_95={
            "coverage": _wilson(covered, total),
            "citation_validity": _wilson(citation_ok, covered),
            "high_confidence_error_rate": _wilson(high_errors, high_total),
            "unsafe_scope_rate": _wilson(unsafe, covered),
        },
    )
