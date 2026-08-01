"""Typed per-level dispatch for injected Stage 3 providers and executors.

S3.5 needs Structural and Bottleneck model components to coexist behind the
single provider/executor interfaces consumed by the deterministic state
machine.  Dispatch is explicit by ``OptimizationLevel``; it performs no source
inspection and requires identical prospective budget increments so preflight
accounting remains exact before a request is routed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .execution import (
    CandidateExecutionRequest,
    CandidateExecutionResult,
    CandidateExecutor,
)
from .policy import BudgetIncrement
from .provider import HypothesisProvider, HypothesisRequest
from .state import HypothesisRecord, OptimizationLevel


DISPATCH_SCHEMA_VERSION = 1


class LevelDispatchHypothesisProvider:
    def __init__(
        self,
        providers: Mapping[OptimizationLevel | str, HypothesisProvider],
        *,
        name: str = "level-dispatch-hypothesis-provider",
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must not be empty")
        normalized: dict[OptimizationLevel, HypothesisProvider] = {}
        for raw_level, provider in dict(providers).items():
            level = (
                raw_level
                if isinstance(raw_level, OptimizationLevel)
                else OptimizationLevel(raw_level)
            )
            if level in normalized:
                raise ValueError(f"duplicate provider level: {level.value}")
            if not isinstance(provider, HypothesisProvider):
                raise TypeError(
                    f"provider for {level.value} does not satisfy HypothesisProvider"
                )
            normalized[level] = provider
        if not normalized:
            raise ValueError("providers must not be empty")
        increments = {provider.budget_increment for provider in normalized.values()}
        if len(increments) != 1:
            raise ValueError(
                "all dispatched providers must have identical budget_increment"
            )
        self._providers = normalized
        self._budget_increment = next(iter(increments))
        self._name = name.strip()

    @property
    def name(self) -> str:
        return self._name

    @property
    def budget_increment(self) -> BudgetIncrement:
        return self._budget_increment

    @property
    def uses_network(self) -> bool:
        return any(provider.uses_network for provider in self._providers.values())

    @property
    def levels(self) -> tuple[OptimizationLevel, ...]:
        return tuple(sorted(self._providers, key=lambda item: item.value))

    def propose(
        self,
        request: HypothesisRequest,
    ) -> Sequence[HypothesisRecord | Mapping[str, Any]]:
        if not isinstance(request, HypothesisRequest):
            raise TypeError("request must be HypothesisRequest")
        try:
            provider = self._providers[request.level]
        except KeyError as exc:
            raise ValueError(
                f"no hypothesis provider configured for {request.level.value}"
            ) from exc
        return provider.propose(request)


class LevelDispatchCandidateExecutor:
    def __init__(
        self,
        executors: Mapping[OptimizationLevel | str, CandidateExecutor],
        *,
        name: str = "level-dispatch-candidate-executor",
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must not be empty")
        normalized: dict[OptimizationLevel, CandidateExecutor] = {}
        for raw_level, executor in dict(executors).items():
            level = (
                raw_level
                if isinstance(raw_level, OptimizationLevel)
                else OptimizationLevel(raw_level)
            )
            if level in normalized:
                raise ValueError(f"duplicate executor level: {level.value}")
            if not isinstance(executor, CandidateExecutor):
                raise TypeError(
                    f"executor for {level.value} does not satisfy CandidateExecutor"
                )
            normalized[level] = executor
        if not normalized:
            raise ValueError("executors must not be empty")
        increments = {executor.budget_increment for executor in normalized.values()}
        if len(increments) != 1:
            raise ValueError(
                "all dispatched executors must have identical budget_increment"
            )
        self._executors = normalized
        self._budget_increment = next(iter(increments))
        self._name = name.strip()

    @property
    def name(self) -> str:
        return self._name

    @property
    def budget_increment(self) -> BudgetIncrement:
        return self._budget_increment

    @property
    def uses_network(self) -> bool:
        return any(executor.uses_network for executor in self._executors.values())

    @property
    def uses_vitis(self) -> bool:
        return any(executor.uses_vitis for executor in self._executors.values())

    @property
    def levels(self) -> tuple[OptimizationLevel, ...]:
        return tuple(sorted(self._executors, key=lambda item: item.value))

    def execute(self, request: CandidateExecutionRequest) -> CandidateExecutionResult:
        if not isinstance(request, CandidateExecutionRequest):
            raise TypeError("request must be CandidateExecutionRequest")
        try:
            executor = self._executors[request.level]
        except KeyError as exc:
            raise ValueError(
                f"no candidate executor configured for {request.level.value}"
            ) from exc
        return executor.execute(request)
