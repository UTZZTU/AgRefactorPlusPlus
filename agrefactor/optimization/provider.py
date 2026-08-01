"""Injected hypothesis provider contracts for Stage 3.3.

The deterministic fake provider is deliberately independent from the real
model registry and network providers.  It emits typed or mapping fixtures that
the state machine validates before selection.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Protocol, runtime_checkable

from .policy import BudgetIncrement
from .state import (
    CandidateRecord,
    HypothesisRecord,
    HypothesisRisk,
    OptimizationLevel,
)


PROVIDER_SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class HypothesisRequest:
    """Agent-safe request passed to an injected hypothesis provider."""

    run_id: str
    level: OptimizationLevel
    round_number: int
    parent_candidate: CandidateRecord
    max_hypotheses: int
    supporting_evidence_ids: tuple[str, ...] = ()
    safe_context: Mapping[str, Any] = field(default_factory=dict)
    parent_source: bytes = b""

    schema_version = PROVIDER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("run_id must not be empty")
        if not isinstance(self.level, OptimizationLevel):
            object.__setattr__(self, "level", OptimizationLevel(self.level))
        if isinstance(self.round_number, bool) or self.round_number < 1:
            raise ValueError("round_number must be positive")
        if not isinstance(self.parent_candidate, CandidateRecord):
            raise TypeError("parent_candidate must be CandidateRecord")
        if isinstance(self.max_hypotheses, bool) or self.max_hypotheses < 1:
            raise ValueError("max_hypotheses must be positive")
        evidence = tuple(self.supporting_evidence_ids)
        if not all(isinstance(item, str) and item.strip() for item in evidence):
            raise ValueError("supporting_evidence_ids must contain non-empty strings")
        if not isinstance(self.parent_source, bytes):
            raise TypeError("parent_source must be bytes")
        safe_context = dict(self.safe_context)
        _reject_unsafe_keys(safe_context)
        object.__setattr__(self, "run_id", self.run_id.strip())
        object.__setattr__(self, "supporting_evidence_ids", evidence)
        object.__setattr__(self, "safe_context", safe_context)
        object.__setattr__(self, "parent_source", self.parent_source)


@runtime_checkable
class HypothesisProvider(Protocol):
    """Minimal provider interface consumed by the deterministic engine."""

    @property
    def name(self) -> str: ...

    @property
    def budget_increment(self) -> BudgetIncrement: ...

    @property
    def uses_network(self) -> bool: ...

    def propose(
        self,
        request: HypothesisRequest,
    ) -> Sequence[HypothesisRecord | Mapping[str, Any]]: ...


class FakeHypothesisProvider:
    """Deterministic, network-free hypothesis fixture provider.

    ``fixtures`` maps ``(level, round_number)`` to a sequence.  Values may be
    typed records or raw mappings so tests can exercise malformed-input paths.
    Missing keys generate deterministic valid hypotheses.  ``None`` as a value
    means an explicit empty response for that level/round.
    """

    def __init__(
        self,
        fixtures: Mapping[
            tuple[OptimizationLevel | str, int],
            Sequence[HypothesisRecord | Mapping[str, Any]] | None,
        ]
        | None = None,
        *,
        budget_increment: BudgetIncrement | None = None,
        name: str = "fake-hypothesis-provider",
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must not be empty")
        normalized: dict[
            tuple[OptimizationLevel, int],
            tuple[HypothesisRecord | Mapping[str, Any], ...] | None,
        ] = {}
        for key, values in dict(fixtures or {}).items():
            level, round_number = key
            normalized_level = (
                level if isinstance(level, OptimizationLevel) else OptimizationLevel(level)
            )
            if isinstance(round_number, bool) or round_number < 1:
                raise ValueError("fixture round_number must be positive")
            normalized[(normalized_level, round_number)] = (
                None if values is None else tuple(values)
            )
        self._fixtures = normalized
        self._budget_increment = budget_increment or BudgetIncrement()
        self._name = name.strip()
        self._requests: list[HypothesisRequest] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def budget_increment(self) -> BudgetIncrement:
        return self._budget_increment

    @property
    def uses_network(self) -> bool:
        return False

    @property
    def requests(self) -> tuple[HypothesisRequest, ...]:
        return tuple(self._requests)

    @property
    def call_count(self) -> int:
        return len(self._requests)

    def propose(
        self,
        request: HypothesisRequest,
    ) -> Sequence[HypothesisRecord | Mapping[str, Any]]:
        if not isinstance(request, HypothesisRequest):
            raise TypeError("request must be HypothesisRequest")
        self._requests.append(request)
        key = (request.level, request.round_number)
        if key in self._fixtures:
            values = self._fixtures[key]
            return () if values is None else values[: request.max_hypotheses]
        return tuple(
            self._default_hypothesis(request, index)
            for index in range(1, request.max_hypotheses + 1)
        )

    def _default_hypothesis(
        self,
        request: HypothesisRequest,
        index: int,
    ) -> HypothesisRecord:
        identity = (
            f"{request.run_id}:{request.level.value}:{request.round_number}:"
            f"{index}:{request.parent_candidate.candidate_id}"
        )
        digest = sha256(identity.encode("utf-8")).hexdigest()
        evidence = request.supporting_evidence_ids
        if request.level is OptimizationLevel.BOTTLENECK and not evidence:
            evidence = (f"ppa-{request.parent_candidate.candidate_id}",)
        return HypothesisRecord(
            hypothesis_id=(
                f"hyp-{request.level.value}-r{request.round_number}-{index}"
            ),
            level=request.level,
            parent_candidate_id=request.parent_candidate.candidate_id,
            claim=(
                f"Deterministic {request.level.value} hypothesis "
                f"{request.round_number}.{index}"
            ),
            supporting_evidence_ids=evidence,
            expected_benefit={"metric": "latency", "direction": "decrease"},
            risk=HypothesisRisk.LOW,
            modification_scope=("candidate_source",),
            verification_plan=("preflight", "public", "csynth", "hidden"),
            model_identity={
                "provider": self.name,
                "network": False,
                "fixture": True,
            },
            prompt_identity_sha256=digest,
        )


def normalize_hypothesis(
    value: HypothesisRecord | Mapping[str, Any],
) -> HypothesisRecord:
    """Convert one provider value into the strict typed record."""

    if isinstance(value, HypothesisRecord):
        return value
    if isinstance(value, Mapping):
        return HypothesisRecord.from_dict(value)
    raise TypeError("provider hypotheses must be HypothesisRecord or mapping")


def _reject_unsafe_keys(value: Any, path: str = "safe_context") -> None:
    forbidden = {
        "hidden",
        "hidden_diagnostic",
        "hidden_report",
        "operator_full",
        "private_testbench",
        "secret",
        "api_key",
        "token",
        "password",
    }
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in forbidden or normalized.startswith("hidden_"):
                raise ValueError(f"{path} contains agent-unsafe key: {key}")
            _reject_unsafe_keys(item, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, item in enumerate(value):
            _reject_unsafe_keys(item, f"{path}[{index}]")
