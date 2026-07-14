"""Bounded, evidence-driven testbench repair orchestration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from agrefactor.evaluation import TestbenchPreflight
from agrefactor.evidence import (
    TestbenchFailureOwner,
    TestbenchPreflightResult,
)


class TestbenchRepairStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    EXHAUSTED = "exhausted"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class TestbenchRepairRequest:
    attempt: int
    max_attempts: int
    current_testbench: str
    original_code: str
    candidate_code: str
    preflight: TestbenchPreflightResult

    def __post_init__(self) -> None:
        if self.attempt < 1:
            raise ValueError("attempt must be at least 1")
        if self.max_attempts < self.attempt:
            raise ValueError("max_attempts must be >= attempt")
        for name in (
            "current_testbench",
            "original_code",
            "candidate_code",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")


class TestbenchRepairer(Protocol):
    def repair(self, request: TestbenchRepairRequest) -> str:
        """Return the complete repaired testbench source."""


@dataclass(frozen=True, slots=True)
class TestbenchRepairAttempt:
    index: int
    action: str
    changed: bool
    preflight: TestbenchPreflightResult
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "action": self.action,
            "changed": self.changed,
            "preflight": self.preflight.to_dict(),
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class TestbenchRepairResult:
    status: TestbenchRepairStatus
    testbench_code: str
    attempts: tuple[TestbenchRepairAttempt, ...]
    final_preflight: TestbenchPreflightResult
    reason: str
    repair_attempts_used: int
    artifact_path: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.repair_attempts_used, bool)
            or not isinstance(self.repair_attempts_used, int)
            or self.repair_attempts_used < 0
        ):
            raise ValueError(
                "repair_attempts_used must be a non-negative integer"
            )

    @property
    def succeeded(self) -> bool:
        return self.status is TestbenchRepairStatus.PASSED

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "reason": self.reason,
            "repair_attempts_used": self.repair_attempts_used,
            "testbench_code": self.testbench_code,
            "attempts": [item.to_dict() for item in self.attempts],
            "final_preflight": self.final_preflight.to_dict(),
            "artifact_path": self.artifact_path,
        }


class TestbenchRepairLoop:
    def __init__(
        self,
        *,
        preflight: TestbenchPreflight,
        repairer: TestbenchRepairer,
        max_repair_attempts: int = 2,
    ) -> None:
        if (
            isinstance(max_repair_attempts, bool)
            or not isinstance(max_repair_attempts, int)
            or max_repair_attempts < 0
        ):
            raise ValueError(
                "max_repair_attempts must be a non-negative integer"
            )
        self._preflight = preflight
        self._repairer = repairer
        self._max_repair_attempts = max_repair_attempts

    def run(
        self,
        *,
        work_dir: str | Path,
        testbench_code: str,
        original_code: str,
        candidate_code: str,
    ) -> TestbenchRepairResult:
        root = Path(work_dir)
        root.mkdir(parents=True, exist_ok=True)

        current = self._require_source("testbench_code", testbench_code)
        original = self._require_source("original_code", original_code)
        candidate = self._require_source("candidate_code", candidate_code)

        attempts: list[TestbenchRepairAttempt] = []
        repair_attempts_used = 0

        initial = self._run_preflight(
            root=root,
            index=0,
            testbench_code=current,
            original_code=original,
            candidate_code=candidate,
        )
        attempts.append(
            TestbenchRepairAttempt(
                index=0,
                action="initial_preflight",
                changed=False,
                preflight=initial,
            )
        )

        if initial.succeeded:
            return self._finish(
                root=root,
                status=TestbenchRepairStatus.PASSED,
                testbench_code=current,
                attempts=attempts,
                final_preflight=initial,
                reason="initial testbench passed preflight",
            )

        stop_reason = self._non_repairable_reason(initial)
        if stop_reason:
            return self._finish(
                root=root,
                status=TestbenchRepairStatus.FAILED,
                testbench_code=current,
                attempts=attempts,
                final_preflight=initial,
                reason=stop_reason,
            )

        if self._max_repair_attempts == 0:
            return self._finish(
                root=root,
                status=TestbenchRepairStatus.EXHAUSTED,
                testbench_code=current,
                attempts=attempts,
                final_preflight=initial,
                reason="testbench repair budget is zero",
            )

        latest = initial
        last_repair_error: str | None = None

        for attempt_number in range(
            1,
            self._max_repair_attempts + 1,
        ):
            request = TestbenchRepairRequest(
                attempt=attempt_number,
                max_attempts=self._max_repair_attempts,
                current_testbench=current,
                original_code=original,
                candidate_code=candidate,
                preflight=latest,
            )

            repair_attempts_used += 1
            try:
                proposed = self._repairer.repair(request)
            except Exception as exc:
                last_repair_error = (
                    "testbench repair provider raised "
                    f"{type(exc).__name__}: {exc}"
                )
                attempts.append(
                    TestbenchRepairAttempt(
                        index=attempt_number,
                        action="repair_provider_error",
                        changed=False,
                        preflight=latest,
                        error=last_repair_error,
                    )
                )
                continue

            if not isinstance(proposed, str) or not proposed.strip():
                last_repair_error = (
                    "testbench repair provider returned empty source"
                )
                attempts.append(
                    TestbenchRepairAttempt(
                        index=attempt_number,
                        action="repair_rejected_empty",
                        changed=False,
                        preflight=latest,
                        error=last_repair_error,
                    )
                )
                continue

            proposed = proposed.strip()
            if proposed == current.strip():
                last_repair_error = (
                    "testbench repair provider returned unchanged source"
                )
                attempts.append(
                    TestbenchRepairAttempt(
                        index=attempt_number,
                        action="repair_rejected_unchanged",
                        changed=False,
                        preflight=latest,
                        error=last_repair_error,
                    )
                )
                continue

            last_repair_error = None
            latest = self._run_preflight(
                root=root,
                index=attempt_number,
                testbench_code=proposed,
                original_code=original,
                candidate_code=candidate,
            )
            attempts.append(
                TestbenchRepairAttempt(
                    index=attempt_number,
                    action="repair_and_preflight",
                    changed=True,
                    preflight=latest,
                )
            )
            current = proposed

            if latest.succeeded:
                return self._finish(
                    root=root,
                    status=TestbenchRepairStatus.PASSED,
                    testbench_code=current,
                    attempts=attempts,
                    final_preflight=latest,
                    reason=(
                        "testbench passed after "
                        f"{attempt_number} repair attempt(s)"
                    ),
                    repair_attempts_used=repair_attempts_used,
                )

            stop_reason = self._non_repairable_reason(latest)
            if stop_reason:
                return self._finish(
                    root=root,
                    status=TestbenchRepairStatus.FAILED,
                    testbench_code=current,
                    attempts=attempts,
                    final_preflight=latest,
                    reason=stop_reason,
                    repair_attempts_used=repair_attempts_used,
                )

        reason = (
            "testbench remained invalid after "
            f"{self._max_repair_attempts} repair attempt(s)"
        )
        if last_repair_error is not None:
            reason = (
                "testbench repair budget exhausted after "
                f"{self._max_repair_attempts} attempt(s); "
                f"last repair error: {last_repair_error}"
            )

        return self._finish(
            root=root,
            status=TestbenchRepairStatus.EXHAUSTED,
            testbench_code=current,
            attempts=attempts,
            final_preflight=latest,
            reason=reason,
            repair_attempts_used=repair_attempts_used,
        )

    def _run_preflight(
        self,
        *,
        root: Path,
        index: int,
        testbench_code: str,
        original_code: str,
        candidate_code: str,
    ) -> TestbenchPreflightResult:
        return self._preflight.compile_and_link(
            work_dir=root / f"attempt_{index:02d}",
            testbench_code=testbench_code,
            original_code=original_code,
            candidate_code=candidate_code,
        )

    @staticmethod
    def _non_repairable_reason(
        preflight: TestbenchPreflightResult,
    ) -> str | None:
        if preflight.failure_owner is TestbenchFailureOwner.TESTBENCH:
            if preflight.next_action == "repair_testbench":
                return None
        return (
            "preflight failure is not owned by the testbench: "
            f"owner={preflight.failure_owner.value}, "
            f"next_action={preflight.next_action}"
        )

    @staticmethod
    def _require_source(name: str, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must not be empty")
        return value

    @staticmethod
    def _finish(
        *,
        root: Path,
        status: TestbenchRepairStatus,
        testbench_code: str,
        attempts: list[TestbenchRepairAttempt],
        final_preflight: TestbenchPreflightResult,
        reason: str,
        repair_attempts_used: int = 0,
    ) -> TestbenchRepairResult:
        artifact_path = root / "testbench_repair.json"
        result = TestbenchRepairResult(
            status=status,
            testbench_code=testbench_code,
            attempts=tuple(attempts),
            final_preflight=final_preflight,
            reason=reason,
            repair_attempts_used=repair_attempts_used,
            artifact_path=str(artifact_path),
        )
        artifact_path.write_text(
            json.dumps(
                result.to_dict(),
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return result
