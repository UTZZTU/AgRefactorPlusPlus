"""Frozen deterministic policy contracts for Stage 3.3.

This module contains no model or tool integration.  It only describes the
bounded safe-v1 search policy and prospective physical budget increments used
by injected providers/executors.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .state import OptimizationLevel


POLICY_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class BudgetIncrement:
    """Prospective physical usage for one injected invocation.

    Fake components default to zero.  Non-zero values are useful for testing
    the shared BudgetManager preflight contract and for future real adapters.
    Token/cost fields remain observed-only in the current runtime and should
    normally stay zero here.
    """

    llm_calls: int = 0
    tool_calls: int = 0
    compile_calls: int = 0
    csim_calls: int = 0
    csynth_calls: int = 0
    cosim_calls: int = 0

    def __post_init__(self) -> None:
        for name in (
            "llm_calls",
            "tool_calls",
            "compile_calls",
            "csim_calls",
            "csynth_calls",
            "cosim_calls",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    def to_kwargs(self) -> dict[str, int]:
        result = {
            "llm_calls": self.llm_calls,
            "tool_calls": self.tool_calls,
            "compile_calls": self.compile_calls,
            "csim_calls": self.csim_calls,
            "csynth_calls": self.csynth_calls,
        }
        if self.cosim_calls:
            result["cosim_calls"] = self.cosim_calls
        return result

    @property
    def is_zero(self) -> bool:
        return not any(self.to_kwargs().values())


@dataclass(frozen=True, slots=True)
class LevelPolicy:
    """Bounded policy for one optimization level."""

    max_rounds: int
    hypotheses_per_round: int
    executed_branches_per_round: int

    def __post_init__(self) -> None:
        for name in (
            "max_rounds",
            "hypotheses_per_round",
            "executed_branches_per_round",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.executed_branches_per_round > self.hypotheses_per_round:
            raise ValueError(
                "executed_branches_per_round cannot exceed hypotheses_per_round"
            )

    def to_dict(self) -> dict[str, int]:
        return {
            "max_rounds": self.max_rounds,
            "hypotheses_per_round": self.hypotheses_per_round,
            "executed_branches_per_round": self.executed_branches_per_round,
        }


@dataclass(frozen=True, slots=True)
class SafeOptimizerPolicy:
    """Typed frozen safe-v1 Stage 3 policy."""

    name: str = "safe-v1"
    search: str = "sequential_best_first"
    objective: str = "latency"
    max_executed_candidates: int = 7
    candidate_correctness_repair_attempts: int = 0
    levels: Mapping[OptimizationLevel, LevelPolicy] | None = None

    schema_version = POLICY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.name != "safe-v1":
            raise ValueError("Stage 3.3 only supports policy name='safe-v1'")
        if self.search != "sequential_best_first":
            raise ValueError(
                "Stage 3.3 only supports search='sequential_best_first'"
            )
        if self.objective != "latency":
            raise ValueError("Stage 3.3 only supports objective='latency'")
        if self.max_executed_candidates != 7:
            raise ValueError("safe-v1 max_executed_candidates must be 7")
        if self.candidate_correctness_repair_attempts != 0:
            raise ValueError(
                "safe-v1 candidate_correctness_repair_attempts must be 0"
            )
        expected = {
            OptimizationLevel.STRUCTURAL: LevelPolicy(2, 3, 1),
            OptimizationLevel.BOTTLENECK: LevelPolicy(2, 3, 1),
            OptimizationLevel.PRAGMA: LevelPolicy(3, 3, 1),
        }
        supplied = expected if self.levels is None else dict(self.levels)
        if supplied != expected:
            raise ValueError("safe-v1 level limits are frozen at 2/2/3, 3, 1")
        object.__setattr__(self, "levels", MappingProxyType(expected))

    @classmethod
    def safe_v1(cls) -> "SafeOptimizerPolicy":
        return cls()

    @property
    def level_order(self) -> tuple[OptimizationLevel, ...]:
        return (
            OptimizationLevel.STRUCTURAL,
            OptimizationLevel.BOTTLENECK,
            OptimizationLevel.PRAGMA,
        )

    def for_level(self, level: OptimizationLevel | str) -> LevelPolicy:
        try:
            normalized = (
                level if isinstance(level, OptimizationLevel) else OptimizationLevel(level)
            )
        except ValueError as exc:
            raise ValueError(f"unsupported optimization level: {level!r}") from exc
        assert self.levels is not None
        return self.levels[normalized]

    def next_level(self, level: OptimizationLevel | str) -> OptimizationLevel | None:
        normalized = (
            level if isinstance(level, OptimizationLevel) else OptimizationLevel(level)
        )
        order = self.level_order
        index = order.index(normalized)
        return None if index + 1 == len(order) else order[index + 1]

    def to_dict(self) -> dict[str, object]:
        assert self.levels is not None
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "search": self.search,
            "objective": self.objective,
            "levels": {
                level.value: self.levels[level].to_dict()
                for level in self.level_order
            },
            "max_executed_candidates": self.max_executed_candidates,
            "candidate_correctness_repair_attempts": (
                self.candidate_correctness_repair_attempts
            ),
        }
