from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class TestbenchStage(str, Enum):
    STATIC_CHECK = "static_check"
    COMPILE_LINK = "compile_link"
    RUN = "run"


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
    COMPILE_TIMEOUT = "compile_timeout"
    COMPILER_NOT_FOUND = "compiler_not_found"
    RUNTIME_ERROR = "runtime_error"
    RUN_TIMEOUT = "run_timeout"
    OUTPUT_MISMATCH = "output_mismatch"
    FALLBACK_MISMATCH = "fallback_mismatch"
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
class TestbenchPreflightResult:
    status: TestbenchPreflightStatus
    stage: TestbenchStage
    failure_kind: TestbenchFailureKind
    return_code: int | None
    command: tuple[str, ...]
    diagnostics: tuple[TestbenchDiagnostic, ...] = ()
    stdout: str = ""
    stderr: str = ""
    artifacts: tuple[str, ...] = ()
    duration_s: float = 0.0

    @property
    def succeeded(self) -> bool:
        return self.status is TestbenchPreflightStatus.PASSED

    @property
    def next_action(self) -> str:
        if self.succeeded:
            return "continue_validation"
        if self.failure_kind in {
            TestbenchFailureKind.FORBIDDEN_INTERNAL_DEPENDENCY,
            TestbenchFailureKind.UNDECLARED_TYPE,
            TestbenchFailureKind.UNDECLARED_SYMBOL,
            TestbenchFailureKind.SYNTAX_ERROR,
            TestbenchFailureKind.LINK_ERROR,
        }:
            return "repair_testbench"
        return "inspect_testbench_failure"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "stage": self.stage.value,
            "failure_kind": self.failure_kind.value,
            "return_code": self.return_code,
            "command": list(self.command),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "stdout": self.stdout,
            "stderr": self.stderr,
            "artifacts": list(self.artifacts),
            "duration_s": self.duration_s,
            "next_action": self.next_action,
        }
