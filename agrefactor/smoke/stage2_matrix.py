"""Stable schemas for the Stage 2 multi-type smoke corpus."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import re
from typing import Any

from agrefactor.config import (
    EvaluationSplit,
    TargetProfile,
    TaskSpec,
    TestSuiteSpec,
    default_target_profile,
)


class Stage2SmokeKernelType(str, Enum):
    """Minimum kernel shapes required by the Stage 2 roadmap."""

    ARRAY_MAP = "array_map"
    REDUCTION = "reduction"
    NESTED_STENCIL = "nested_stencil"
    MULTI_OUTPUT = "multi_output"
    STRUCT_RECORD = "struct_record"
    HLS_STREAM = "hls_stream"
    STATEFUL = "stateful"


class Stage2SmokeScenarioKind(str, Enum):
    """Whether a case is a passing baseline or an injected fault."""

    BASELINE = "baseline"
    INJECTED_FAULT = "injected_fault"


class Stage2SmokeGroundTruthOwner(str, Enum):
    """Independent owner labels; these are not runtime predictions."""

    NONE = "none"
    CANDIDATE = "candidate"
    TESTBENCH = "testbench"
    ORIGINAL = "original"
    EVALUATOR = "evaluator"
    TOOLCHAIN = "toolchain"
    UNKNOWN = "unknown"
    MIXED = "mixed"


class Stage2SmokeGroundTruthStage(str, Enum):
    """Independent failure-stage labels used by smoke scenarios."""

    NONE = "none"
    STATIC_CHECK = "static_check"
    COMPILE = "compile"
    LINK = "link"
    CSYNTH = "csynth"
    PUBLIC_EVALUATION = "public_evaluation"
    HIDDEN_EVALUATION = "hidden_evaluation"
    CONFIGURATION = "configuration"


class Stage2SmokeExpectedRoute(str, Enum):
    """Expected control action independent of the system under test."""

    ADVANCE = "advance"
    REPAIR_CANDIDATE = "repair_candidate"
    REPAIR_TESTBENCH = "repair_testbench"
    REPAIR_ORIGINAL = "repair_original"
    EXTERNAL_REMEDIATION = "external_remediation"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"
    REJECTED = "rejected"


class Stage2SmokeExpectedTerminalState(str, Enum):
    """Expected terminal or handoff state for one scenario."""

    ACCEPTED = "accepted"
    REPAIR_PENDING = "repair_pending"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    REVIEW_REQUIRED = "review_required"


class Stage2SmokeHiddenVisibility(str, Enum):
    """Expected Hidden-data boundary."""

    OPERATOR_ONLY_NEVER_AGENT = "operator_only_never_agent"


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty")
    return cleaned


def _enum(value: Any, enum_type: type[Enum], field_name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except ValueError as exc:
        raise ValueError(f"unsupported {field_name}: {value!r}") from exc


def _non_negative_int(value: int, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return value


def _non_negative_float(value: float, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be numeric")
    normalized = float(value)
    if normalized < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return normalized


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Stage2SmokeBudgetExpectation:
    """Exact physical usage expected for one baseline validation chain."""

    tool_calls: int
    compile_calls: int
    csynth_calls: int
    csim_calls: int
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
            object.__setattr__(
                self,
                name,
                _non_negative_int(getattr(self, name), name),
            )
        object.__setattr__(
            self,
            "cost_usd",
            _non_negative_float(self.cost_usd, "cost_usd"),
        )

    def to_dict(self) -> dict[str, int | float]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Stage2SmokeGroundTruth:
    """Manually authored label; never derived from runtime output."""

    case_id: str
    kernel_type: Stage2SmokeKernelType
    scenario_kind: Stage2SmokeScenarioKind
    injected_fault: str
    ground_truth_owner: Stage2SmokeGroundTruthOwner
    ground_truth_stage: Stage2SmokeGroundTruthStage
    expected_route: Stage2SmokeExpectedRoute
    expected_terminal_state: Stage2SmokeExpectedTerminalState
    hidden_visibility_expectation: Stage2SmokeHiddenVisibility

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "case_id",
            _required_text(self.case_id, "case_id"),
        )
        object.__setattr__(
            self,
            "kernel_type",
            _enum(
                self.kernel_type,
                Stage2SmokeKernelType,
                "kernel_type",
            ),
        )
        object.__setattr__(
            self,
            "scenario_kind",
            _enum(
                self.scenario_kind,
                Stage2SmokeScenarioKind,
                "scenario_kind",
            ),
        )
        object.__setattr__(
            self,
            "injected_fault",
            _required_text(self.injected_fault, "injected_fault"),
        )
        object.__setattr__(
            self,
            "ground_truth_owner",
            _enum(
                self.ground_truth_owner,
                Stage2SmokeGroundTruthOwner,
                "ground_truth_owner",
            ),
        )
        object.__setattr__(
            self,
            "ground_truth_stage",
            _enum(
                self.ground_truth_stage,
                Stage2SmokeGroundTruthStage,
                "ground_truth_stage",
            ),
        )
        object.__setattr__(
            self,
            "expected_route",
            _enum(
                self.expected_route,
                Stage2SmokeExpectedRoute,
                "expected_route",
            ),
        )
        object.__setattr__(
            self,
            "expected_terminal_state",
            _enum(
                self.expected_terminal_state,
                Stage2SmokeExpectedTerminalState,
                "expected_terminal_state",
            ),
        )
        object.__setattr__(
            self,
            "hidden_visibility_expectation",
            _enum(
                self.hidden_visibility_expectation,
                Stage2SmokeHiddenVisibility,
                "hidden_visibility_expectation",
            ),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "case_id": self.case_id,
            "kernel_type": self.kernel_type.value,
            "scenario_kind": self.scenario_kind.value,
            "injected_fault": self.injected_fault,
            "ground_truth_owner": self.ground_truth_owner.value,
            "ground_truth_stage": self.ground_truth_stage.value,
            "expected_route": self.expected_route.value,
            "expected_terminal_state": (
                self.expected_terminal_state.value
            ),
            "hidden_visibility_expectation": (
                self.hidden_visibility_expectation.value
            ),
        }


@dataclass(frozen=True, slots=True)
class Stage2SmokeCase:
    """One immutable source bundle plus independent expected behavior."""

    case_id: str
    kernel_type: Stage2SmokeKernelType
    task_id: str
    kernel_name: str
    original_code: str
    candidate_code: str
    preflight_testbench_code: str
    public_testbench_code: str
    hidden_testbench_code: str
    hidden_secret_marker: str
    ground_truth: Stage2SmokeGroundTruth
    expected_budget: Stage2SmokeBudgetExpectation
    public_suite_id: str = "public-main"
    hidden_suite_id: str = "hidden-final"
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "case_id",
            "task_id",
            "kernel_name",
            "original_code",
            "candidate_code",
            "preflight_testbench_code",
            "public_testbench_code",
            "hidden_testbench_code",
            "hidden_secret_marker",
            "public_suite_id",
            "hidden_suite_id",
        ):
            object.__setattr__(
                self,
                name,
                _required_text(getattr(self, name), name),
            )
        object.__setattr__(
            self,
            "kernel_type",
            _enum(
                self.kernel_type,
                Stage2SmokeKernelType,
                "kernel_type",
            ),
        )
        if not isinstance(self.ground_truth, Stage2SmokeGroundTruth):
            raise TypeError(
                "ground_truth must be Stage2SmokeGroundTruth"
            )
        if not isinstance(
            self.expected_budget,
            Stage2SmokeBudgetExpectation,
        ):
            raise TypeError(
                "expected_budget must be Stage2SmokeBudgetExpectation"
            )
        if self.ground_truth.case_id != self.case_id:
            raise ValueError(
                "ground_truth.case_id must match case_id"
            )
        if self.ground_truth.kernel_type is not self.kernel_type:
            raise ValueError(
                "ground_truth.kernel_type must match kernel_type"
            )
        if self.public_suite_id == self.hidden_suite_id:
            raise ValueError(
                "public_suite_id and hidden_suite_id must differ"
            )
        if not re.fullmatch(r"[A-Za-z_]\w*", self.kernel_name):
            raise ValueError(
                "kernel_name must be a C/C++ identifier"
            )
        if self.kernel_name not in self.candidate_code:
            raise ValueError(
                "candidate_code must contain kernel_name"
            )
        if self.hidden_secret_marker not in self.hidden_testbench_code:
            raise ValueError(
                "hidden_testbench_code must contain hidden_secret_marker"
            )
        for safe_name in (
            "original_code",
            "candidate_code",
            "preflight_testbench_code",
            "public_testbench_code",
        ):
            if self.hidden_secret_marker in getattr(self, safe_name):
                raise ValueError(
                    f"{safe_name} must not contain hidden_secret_marker"
                )
        if not isinstance(self.tags, tuple):
            raise TypeError("tags must be a tuple")
        normalized_tags = tuple(
            _required_text(item, "tags") for item in self.tags
        )
        object.__setattr__(self, "tags", normalized_tags)

    @property
    def suite_testbench_codes(self) -> dict[str, str]:
        return {
            self.public_suite_id: self.public_testbench_code,
            self.hidden_suite_id: self.hidden_testbench_code,
        }

    @property
    def source_bundle(self) -> dict[str, str]:
        return {
            "original_code": self.original_code,
            "candidate_code": self.candidate_code,
            "preflight_testbench_code": (
                self.preflight_testbench_code
            ),
            "public_testbench_code": self.public_testbench_code,
            "hidden_testbench_code": self.hidden_testbench_code,
        }

    def build_task(
        self,
        *,
        target: TargetProfile | None = None,
    ) -> TaskSpec:
        if target is not None and not isinstance(
            target,
            TargetProfile,
        ):
            raise TypeError("target must be TargetProfile or None")
        return TaskSpec(
            task_id=self.task_id,
            kernel_path=f"{self.case_id}/candidate.cpp",
            kernel_name=self.kernel_name,
            target=target or default_target_profile(),
            test_suites=(
                TestSuiteSpec(
                    suite_id=self.public_suite_id,
                    split=EvaluationSplit.PUBLIC,
                    suite_version="stage2-smoke-v1",
                    case_count=1,
                ),
                TestSuiteSpec(
                    suite_id=self.hidden_suite_id,
                    split=EvaluationSplit.HIDDEN,
                    suite_version="stage2-smoke-v1",
                    case_count=1,
                ),
            ),
        )

    def operator_manifest(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "case_id": self.case_id,
            "kernel_type": self.kernel_type.value,
            "task_id": self.task_id,
            "kernel_name": self.kernel_name,
            "public_suite_id": self.public_suite_id,
            "hidden_suite_id": self.hidden_suite_id,
            "tags": list(self.tags),
            "source_sha256": {
                name: _sha256(value)
                for name, value in self.source_bundle.items()
            },
            "ground_truth": self.ground_truth.to_dict(),
            "expected_budget": self.expected_budget.to_dict(),
        }

    def agent_safe_manifest(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "case_id": self.case_id,
            "kernel_type": self.kernel_type.value,
            "task_id": self.task_id,
            "kernel_name": self.kernel_name,
            "public_suite_id": self.public_suite_id,
            "hidden_suite_count": 1,
            "tags": list(self.tags),
            "source_sha256": {
                "original_code": _sha256(self.original_code),
                "candidate_code": _sha256(self.candidate_code),
                "preflight_testbench_code": _sha256(
                    self.preflight_testbench_code
                ),
                "public_testbench_code": _sha256(
                    self.public_testbench_code
                ),
            },
        }


def load_stage2_smoke_cases() -> tuple[Stage2SmokeCase, ...]:
    """Return the manually authored immutable Stage 2 corpus."""

    from .stage2_corpus import STAGE2_SMOKE_CASES

    return STAGE2_SMOKE_CASES


def get_stage2_smoke_case(case_id: str) -> Stage2SmokeCase:
    """Return one case by stable identifier."""

    normalized = _required_text(case_id, "case_id")
    for case in load_stage2_smoke_cases():
        if case.case_id == normalized:
            return case
    raise KeyError(f"unknown Stage 2 smoke case: {normalized}")
