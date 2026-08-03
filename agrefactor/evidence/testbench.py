from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class TestbenchStage(str, Enum):
    STATIC_CHECK = "static_check"
    COMPILE_LINK = "compile_link"
    RUN = "run"


class TestbenchPreflightComponent(str, Enum):
    TESTBENCH = "testbench"
    REFERENCE = "reference"
    CANDIDATE = "candidate"
    SYMBOL_CHECK = "symbol_check"
    LINK = "link"
    TOOLCHAIN = "toolchain"
    CONFIGURATION = "configuration"


class TestbenchPreflightReasonCode(str, Enum):
    PASSED = "passed"
    TESTBENCH_COMPILE_FAILED = "testbench_compile_failed"
    REFERENCE_COMPILE_FAILED = "reference_compile_failed"
    CANDIDATE_COMPILE_FAILED = "candidate_compile_failed"
    CANDIDATE_TOP_MISSING = "candidate_top_missing"
    REFERENCE_TOP_MISSING = "reference_top_missing"
    INTERFACE_MISMATCH = "interface_mismatch"
    LINK_FAILED = "link_failed"
    TOOLCHAIN_FAILED = "toolchain_failed"
    CONFIGURATION_FAILED = "configuration_failed"
    OWNERSHIP_UNKNOWN = "ownership_unknown"


class TestbenchPreflightSubstage(str, Enum):
    TESTBENCH_COMPILE = "testbench_compile"
    REFERENCE_COMPILE = "reference_compile"
    CANDIDATE_COMPILE = "candidate_compile"
    TESTBENCH_SYMBOL_CHECK = "testbench_symbol_check"
    REFERENCE_SYMBOL_CHECK = "reference_symbol_check"
    CANDIDATE_SYMBOL_CHECK = "candidate_symbol_check"
    REFERENCE_INTERFACE_CHECK = "reference_interface_check"
    CANDIDATE_INTERFACE_CHECK = "candidate_interface_check"
    LINK = "link"


class TestbenchPreflightSubstepStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class TestbenchPreflightStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


class TestbenchFailureKind(str, Enum):
    NONE = "none"
    FORBIDDEN_INTERNAL_DEPENDENCY = "forbidden_internal_dependency"
    UNDECLARED_TYPE = "undeclared_type"
    UNDECLARED_SYMBOL = "undeclared_symbol"
    SYNTAX_ERROR = "syntax_error"
    LINK_ERROR = "link_error"
    LINKAGE_MISMATCH = "linkage_mismatch"
    COMPILE_TIMEOUT = "compile_timeout"
    COMPILER_NOT_FOUND = "compiler_not_found"
    RUNTIME_ERROR = "runtime_error"
    RUN_TIMEOUT = "run_timeout"
    OUTPUT_MISMATCH = "output_mismatch"
    FALLBACK_MISMATCH = "fallback_mismatch"
    UNKNOWN = "unknown"


class TestbenchFailureOwner(str, Enum):
    NONE = "none"
    TESTBENCH = "testbench"
    ORIGINAL = "original"
    CANDIDATE = "candidate"
    TOOLCHAIN = "toolchain"
    CONFIGURATION = "configuration"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class TestbenchDiagnostic:
    kind: TestbenchFailureKind
    message: str
    file: str | None = None
    line: int | None = None
    column: int | None = None
    raw: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, TestbenchFailureKind):
            object.__setattr__(
                self,
                "kind",
                TestbenchFailureKind(str(self.kind)),
            )
        message = self.message.strip()
        if not message:
            raise ValueError("diagnostic message must not be empty")
        object.__setattr__(self, "message", message)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["kind"] = self.kind.value
        return payload


@dataclass(frozen=True, slots=True)
class TestbenchPreflightSubstep:
    substage: TestbenchPreflightSubstage
    component: TestbenchPreflightComponent
    status: TestbenchPreflightSubstepStatus
    command: tuple[str, ...]
    return_code: int | None
    failure_kind: TestbenchFailureKind = TestbenchFailureKind.NONE
    stdout: str = ""
    stderr: str = ""
    artifact: str | None = None
    duration_s: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "substage",
            self.substage
            if isinstance(self.substage, TestbenchPreflightSubstage)
            else TestbenchPreflightSubstage(str(self.substage)),
        )
        object.__setattr__(
            self,
            "component",
            self.component
            if isinstance(self.component, TestbenchPreflightComponent)
            else TestbenchPreflightComponent(str(self.component)),
        )
        object.__setattr__(
            self,
            "status",
            self.status
            if isinstance(self.status, TestbenchPreflightSubstepStatus)
            else TestbenchPreflightSubstepStatus(str(self.status)),
        )
        object.__setattr__(
            self,
            "failure_kind",
            self.failure_kind
            if isinstance(self.failure_kind, TestbenchFailureKind)
            else TestbenchFailureKind(str(self.failure_kind)),
        )
        object.__setattr__(self, "command", tuple(self.command))
        if self.duration_s < 0:
            raise ValueError("substep duration_s must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "substage": self.substage.value,
            "component": self.component.value,
            "status": self.status.value,
            "command": list(self.command),
            "return_code": self.return_code,
            "failure_kind": self.failure_kind.value,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "artifact": self.artifact,
            "duration_s": self.duration_s,
        }


@dataclass(frozen=True, slots=True)
class TestbenchPreflightResult:
    status: TestbenchPreflightStatus
    stage: TestbenchStage
    failure_kind: TestbenchFailureKind
    failure_owner: TestbenchFailureOwner
    return_code: int | None
    command: tuple[str, ...]
    diagnostics: tuple[TestbenchDiagnostic, ...] = ()
    stdout: str = ""
    stderr: str = ""
    artifacts: tuple[str, ...] = ()
    duration_s: float = 0.0
    reason_codes: tuple[TestbenchPreflightReasonCode, ...] = ()
    failed_component: TestbenchPreflightComponent | None = None
    substeps: tuple[TestbenchPreflightSubstep, ...] = ()

    def __post_init__(self) -> None:
        status = (
            self.status
            if isinstance(self.status, TestbenchPreflightStatus)
            else TestbenchPreflightStatus(str(self.status))
        )
        stage = (
            self.stage
            if isinstance(self.stage, TestbenchStage)
            else TestbenchStage(str(self.stage))
        )
        kind = (
            self.failure_kind
            if isinstance(self.failure_kind, TestbenchFailureKind)
            else TestbenchFailureKind(str(self.failure_kind))
        )
        owner = (
            self.failure_owner
            if isinstance(self.failure_owner, TestbenchFailureOwner)
            else TestbenchFailureOwner(str(self.failure_owner))
        )
        reasons = tuple(
            item
            if isinstance(item, TestbenchPreflightReasonCode)
            else TestbenchPreflightReasonCode(str(item))
            for item in self.reason_codes
        )
        if not reasons:
            reasons = _default_preflight_reason_codes(
                status=status,
                kind=kind,
                owner=owner,
            )
        component = (
            None
            if self.failed_component is None
            else self.failed_component
            if isinstance(
                self.failed_component,
                TestbenchPreflightComponent,
            )
            else TestbenchPreflightComponent(
                str(self.failed_component)
            )
        )
        steps = tuple(self.substeps)
        if not all(
            isinstance(item, TestbenchPreflightSubstep)
            for item in steps
        ):
            raise TypeError(
                "substeps must contain TestbenchPreflightSubstep values"
            )
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "failure_kind", kind)
        object.__setattr__(self, "failure_owner", owner)
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(self, "failed_component", component)
        object.__setattr__(self, "substeps", steps)

    @property
    def reason_code(self) -> TestbenchPreflightReasonCode:
        return self.reason_codes[0]

    @property
    def succeeded(self) -> bool:
        return self.status is TestbenchPreflightStatus.PASSED

    @property
    def next_action(self) -> str:
        if self.succeeded:
            return "continue_validation"
        if self.failure_owner is TestbenchFailureOwner.TESTBENCH:
            return "repair_testbench"
        if self.failure_owner is TestbenchFailureOwner.CANDIDATE:
            return "repair_candidate"
        if self.failure_owner is TestbenchFailureOwner.ORIGINAL:
            return "inspect_original"
        if self.failure_owner is TestbenchFailureOwner.TOOLCHAIN:
            return "inspect_toolchain"
        if self.failure_owner is TestbenchFailureOwner.CONFIGURATION:
            return "inspect_configuration"
        return "inspect_compile_failure"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "stage": self.stage.value,
            "failure_kind": self.failure_kind.value,
            "failure_owner": self.failure_owner.value,
            "return_code": self.return_code,
            "command": list(self.command),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "stdout": self.stdout,
            "stderr": self.stderr,
            "artifacts": list(self.artifacts),
            "duration_s": self.duration_s,
            "reason_code": self.reason_code.value,
            "reason_codes": [
                item.value for item in self.reason_codes
            ],
            "failed_component": (
                None
                if self.failed_component is None
                else self.failed_component.value
            ),
            "ownership_resolved": (
                self.failure_owner
                is not TestbenchFailureOwner.UNKNOWN
            ),
            "substeps": [
                item.to_dict() for item in self.substeps
            ],
            "next_action": self.next_action,
        }


def _default_preflight_reason_codes(
    *,
    status: TestbenchPreflightStatus,
    kind: TestbenchFailureKind,
    owner: TestbenchFailureOwner,
) -> tuple[TestbenchPreflightReasonCode, ...]:
    if status is TestbenchPreflightStatus.PASSED:
        return (TestbenchPreflightReasonCode.PASSED,)
    if kind is TestbenchFailureKind.LINKAGE_MISMATCH:
        return (
            TestbenchPreflightReasonCode.INTERFACE_MISMATCH,
        )
    if kind is TestbenchFailureKind.LINK_ERROR:
        reasons = [TestbenchPreflightReasonCode.LINK_FAILED]
        if owner is TestbenchFailureOwner.UNKNOWN:
            reasons.append(
                TestbenchPreflightReasonCode.OWNERSHIP_UNKNOWN
            )
        return tuple(reasons)
    if owner is TestbenchFailureOwner.CANDIDATE:
        return (
            TestbenchPreflightReasonCode.CANDIDATE_COMPILE_FAILED,
        )
    if owner is TestbenchFailureOwner.ORIGINAL:
        return (
            TestbenchPreflightReasonCode.REFERENCE_COMPILE_FAILED,
        )
    if owner is TestbenchFailureOwner.TESTBENCH:
        return (
            TestbenchPreflightReasonCode.TESTBENCH_COMPILE_FAILED,
        )
    if owner is TestbenchFailureOwner.TOOLCHAIN:
        return (TestbenchPreflightReasonCode.TOOLCHAIN_FAILED,)
    if owner is TestbenchFailureOwner.CONFIGURATION:
        return (
            TestbenchPreflightReasonCode.CONFIGURATION_FAILED,
        )
    return (TestbenchPreflightReasonCode.OWNERSHIP_UNKNOWN,)
