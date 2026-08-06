"""Bounded, evidence-driven testbench repair orchestration."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from agrefactor.config import (
    DEFAULT_TESTBENCH_REPAIR_ATTEMPTS,
    TaskSpec,
    validate_repair_attempts,
)
from agrefactor.evaluation import TestbenchPreflight
from agrefactor.evidence import (
    TestbenchFailureOwner,
    TestbenchPreflightResult,
)
from agrefactor.repair import (
    RepairArtifactRole,
    RepairArtifactWriter,
    RepairAttemptRecord,
    RepairModelObservation,
    RepairObservedUsage,
    RepairRunRecord,
    RepairTerminalStatus,
    TestbenchRepairPayload,
    repair_attempt_id,
    repair_proposal_id,
)
from agrefactor.runtime.budget import (
    BudgetManager,
    BudgetUsage,
)



def _default_testbench_repair_task() -> TaskSpec:
    """Return a path-neutral compatibility task for direct callers."""

    return TaskSpec(
        task_id="testbench-repair",
        kernel_path="candidate.cpp",
        kernel_name="candidate",
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
    prior_attempt_summaries: tuple[str, ...] = ()
    task: TaskSpec = field(
        default_factory=_default_testbench_repair_task
    )

    def __post_init__(self) -> None:
        validate_repair_attempts(
            self.max_attempts,
            field_name="max_attempts",
        )
        if self.attempt < 1:
            raise ValueError("attempt must be at least 1")
        if self.max_attempts < self.attempt:
            raise ValueError("max_attempts must be >= attempt")
        if not isinstance(self.task, TaskSpec):
            raise TypeError(
                "TestbenchRepairRequest.task must be a TaskSpec"
            )
        summaries = tuple(self.prior_attempt_summaries)
        if not all(
            isinstance(item, str) and item.strip()
            for item in summaries
        ):
            raise ValueError(
                "prior_attempt_summaries must contain "
                "only non-empty strings"
            )
        object.__setattr__(
            self,
            "prior_attempt_summaries",
            tuple(item.strip() for item in summaries),
        )
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
    model_observation: RepairModelObservation = field(
        default_factory=RepairModelObservation
    )
    observed_usage: RepairObservedUsage = field(
        default_factory=RepairObservedUsage
    )

    def __post_init__(self) -> None:
        if (
            isinstance(self.index, bool)
            or not isinstance(self.index, int)
            or self.index < 0
        ):
            raise ValueError(
                "index must be a non-negative integer"
            )
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

    def to_protocol_record(
        self,
        run_id: str,
    ) -> RepairAttemptRecord:
        attempt_id = repair_attempt_id(
            run_id,
            self.index,
        )
        terminal = None
        if self.preflight.succeeded:
            terminal = RepairTerminalStatus.SUCCEEDED
        elif self.preflight.status.value == "error":
            terminal = RepairTerminalStatus.ERROR
        validation_summary = {
            "status": self.preflight.status.value,
            "stage": self.preflight.stage.value,
            "failure_kind": (
                self.preflight.failure_kind.value
            ),
            "failure_owner": (
                self.preflight.failure_owner.value
            ),
            "return_code": self.preflight.return_code,
            "next_action": self.preflight.next_action,
        }
        error_type = None
        if self.error:
            error_type = "repair_error"
        return RepairAttemptRecord(
            attempt_id=attempt_id,
            proposal_id=(
                repair_proposal_id(attempt_id)
                if self.changed
                else None
            ),
            artifact_role=RepairArtifactRole.TESTBENCH,
            sequence_index=self.index,
            action=self.action,
            status=self.action,
            changed=self.changed,
            model_observation=self.model_observation,
            observed_usage=self.observed_usage,
            payload=TestbenchRepairPayload(
                preflight_summary=validation_summary,
                legacy_preflight_artifact_available=True,
            ),
            terminal_status=terminal,
            evidence_view="agent_safe",
            operator_artifact_available=True,
            error_type=error_type,
            error_message=self.error,
        )

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
    repair_run: RepairRunRecord
    repair_run_path: str
    repair_attempt_paths: tuple[str, ...]
    repair_artifact_manifest_path: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.repair_attempts_used, bool)
            or not isinstance(self.repair_attempts_used, int)
            or self.repair_attempts_used < 0
        ):
            raise ValueError(
                "repair_attempts_used must be a non-negative integer"
            )
        if not isinstance(
            self.repair_run,
            RepairRunRecord,
        ):
            raise TypeError(
                "repair_run must be RepairRunRecord"
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
        max_repair_attempts: int = (
            DEFAULT_TESTBENCH_REPAIR_ATTEMPTS
        ),
    ) -> None:
        validate_repair_attempts(
            max_repair_attempts,
            field_name="max_repair_attempts",
            allow_zero=True,
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
        budget: BudgetManager | None = None,
        task: TaskSpec | None = None,
        original_top_function: str | None = None,
        candidate_top_function: str | None = None,
    ) -> TestbenchRepairResult:
        root = Path(work_dir)
        root.mkdir(parents=True, exist_ok=True)

        current = self._require_source("testbench_code", testbench_code)
        original = self._require_source("original_code", original_code)
        candidate = self._require_source("candidate_code", candidate_code)
        original_top = self._optional_top(
            "original_top_function", original_top_function
        )
        candidate_top = self._optional_top(
            "candidate_top_function", candidate_top_function
        )

        if task is None:
            task = _default_testbench_repair_task()
        elif not isinstance(task, TaskSpec):
            raise TypeError("task must be a TaskSpec or None")

        run_id = f"{task.task_id}.testbench-repair"
        attempts: list[TestbenchRepairAttempt] = []
        repair_attempts_used = 0

        initial_before = self._budget_snapshot(budget)
        initial = self._run_preflight(
            root=root,
            index=0,
            testbench_code=current,
            original_code=original,
            candidate_code=candidate,
            budget=budget,
            original_top_function=original_top,
            candidate_top_function=candidate_top,
        )
        initial_after = self._budget_snapshot(budget)
        attempts.append(
            TestbenchRepairAttempt(
                index=0,
                action="initial_preflight",
                changed=False,
                preflight=initial,
                observed_usage=(
                    RepairObservedUsage.from_observations(
                        initial_before,
                        initial_after,
                    )
                ),
            )
        )

        if initial.succeeded:
            return self._finish(
                root=root,
                run_id=run_id,
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
                run_id=run_id,
                status=TestbenchRepairStatus.FAILED,
                testbench_code=current,
                attempts=attempts,
                final_preflight=initial,
                reason=stop_reason,
            )

        if self._max_repair_attempts == 0:
            return self._finish(
                root=root,
                run_id=run_id,
                status=TestbenchRepairStatus.EXHAUSTED,
                testbench_code=current,
                attempts=attempts,
                final_preflight=initial,
                reason="testbench repair budget is zero",
            )

        latest = initial
        last_repair_error: str | None = None
        prior_attempt_summaries: list[str] = []

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
                prior_attempt_summaries=tuple(
                    prior_attempt_summaries
                ),
                task=task,
            )

            repair_attempts_used += 1
            attempt_before = self._budget_snapshot(budget)
            audit_count_before = self._audit_event_count()
            try:
                proposed = self._repairer.repair(request)
            except Exception as exc:
                last_repair_error = (
                    "testbench repair provider raised "
                    f"{type(exc).__name__}: {exc}"
                )
                observation = self._new_model_observation(
                    audit_count_before
                )
                attempts.append(
                    TestbenchRepairAttempt(
                        index=attempt_number,
                        action="repair_provider_error",
                        changed=False,
                        preflight=latest,
                        error=last_repair_error,
                        model_observation=observation,
                        observed_usage=(
                            RepairObservedUsage.from_observations(
                                attempt_before,
                                self._budget_snapshot(budget),
                                observation,
                            )
                        ),
                    )
                )
                prior_attempt_summaries.append(
                    self._prior_attempt_summary(
                        attempt_number,
                        last_repair_error,
                        include_detail=(
                            type(exc).__name__
                            == "TestbenchRepairResponseError"
                        ),
                    )
                )
                continue

            if not isinstance(proposed, str) or not proposed.strip():
                last_repair_error = (
                    "testbench repair provider returned empty source"
                )
                observation = self._new_model_observation(
                    audit_count_before
                )
                attempts.append(
                    TestbenchRepairAttempt(
                        index=attempt_number,
                        action="repair_rejected_empty",
                        changed=False,
                        preflight=latest,
                        error=last_repair_error,
                        model_observation=observation,
                        observed_usage=(
                            RepairObservedUsage.from_observations(
                                attempt_before,
                                self._budget_snapshot(budget),
                                observation,
                            )
                        ),
                    )
                )
                prior_attempt_summaries.append(
                    self._prior_attempt_summary(
                        attempt_number,
                        last_repair_error,
                    )
                )
                continue

            proposed = proposed.strip()
            if proposed == current.strip():
                last_repair_error = (
                    "testbench repair provider returned unchanged source"
                )
                observation = self._new_model_observation(
                    audit_count_before
                )
                attempts.append(
                    TestbenchRepairAttempt(
                        index=attempt_number,
                        action="repair_rejected_unchanged",
                        changed=False,
                        preflight=latest,
                        error=last_repair_error,
                        model_observation=observation,
                        observed_usage=(
                            RepairObservedUsage.from_observations(
                                attempt_before,
                                self._budget_snapshot(budget),
                                observation,
                            )
                        ),
                    )
                )
                prior_attempt_summaries.append(
                    self._prior_attempt_summary(
                        attempt_number,
                        last_repair_error,
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
                budget=budget,
                original_top_function=original_top,
                candidate_top_function=candidate_top,
            )
            observation = self._new_model_observation(
                audit_count_before
            )
            attempts.append(
                TestbenchRepairAttempt(
                    index=attempt_number,
                    action="repair_and_preflight",
                    changed=True,
                    preflight=latest,
                    model_observation=observation,
                    observed_usage=(
                        RepairObservedUsage.from_observations(
                            attempt_before,
                            self._budget_snapshot(budget),
                            observation,
                        )
                    ),
                )
            )
            current = proposed

            if latest.succeeded:
                return self._finish(
                    root=root,
                    run_id=run_id,
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
                    run_id=run_id,
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
            run_id=run_id,
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
        budget: BudgetManager | None,
        original_top_function: str | None,
        candidate_top_function: str | None,
    ) -> TestbenchPreflightResult:
        kwargs = {
            "work_dir": root / f"attempt_{index:02d}",
            "testbench_code": testbench_code,
            "original_code": original_code,
            "candidate_code": candidate_code,
        }
        if original_top_function is not None:
            kwargs["original_top_function"] = original_top_function
        if candidate_top_function is not None:
            kwargs["candidate_top_function"] = candidate_top_function
        if budget is not None:
            kwargs["budget"] = budget
        return self._preflight.compile_and_link(**kwargs)

    @staticmethod
    def _prior_attempt_summary(
        attempt_number: int,
        error: str,
        *,
        include_detail: bool = True,
    ) -> str:
        prefix = (
            f"Attempt {attempt_number} was rejected before "
            "testbench preflight. "
        )
        if not include_detail:
            return (
                prefix
                + "The provider failed before producing an "
                "acceptable replacement; do not repeat the same "
                "response shape."
            )
        compact = " ".join(str(error).split())
        return prefix + compact[:2000]

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
    def _optional_top(name: str, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError(f"{name} must be a string or None")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{name} must not be empty")
        return cleaned

    @staticmethod
    def _require_source(name: str, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must not be empty")
        return value

    @staticmethod
    def _budget_snapshot(
        budget: BudgetManager | None,
    ) -> BudgetUsage | None:
        return None if budget is None else budget.snapshot()

    def _audit_event_count(self) -> int:
        events = getattr(
            self._repairer,
            "audit_events",
            (),
        )
        try:
            return len(tuple(events))
        except TypeError:
            return 0

    def _new_model_observation(
        self,
        previous_count: int,
    ) -> RepairModelObservation:
        events = getattr(
            self._repairer,
            "audit_events",
            (),
        )
        try:
            normalized = tuple(events)
        except TypeError:
            return RepairModelObservation()
        if (
            len(normalized) == previous_count + 1
            and isinstance(
                normalized[-1],
                RepairModelObservation,
            )
        ):
            return normalized[-1]
        return RepairModelObservation()

    @staticmethod
    def _finish(
        *,
        root: Path,
        run_id: str,
        status: TestbenchRepairStatus,
        testbench_code: str,
        attempts: list[TestbenchRepairAttempt],
        final_preflight: TestbenchPreflightResult,
        reason: str,
        repair_attempts_used: int = 0,
    ) -> TestbenchRepairResult:
        terminal = {
            TestbenchRepairStatus.PASSED: (
                RepairTerminalStatus.SUCCEEDED
            ),
            TestbenchRepairStatus.FAILED: (
                RepairTerminalStatus.FAILED
            ),
            TestbenchRepairStatus.EXHAUSTED: (
                RepairTerminalStatus.EXHAUSTED
            ),
            TestbenchRepairStatus.ERROR: (
                RepairTerminalStatus.ERROR
            ),
        }[status]
        run_record = RepairRunRecord(
            run_id=run_id,
            artifact_role=RepairArtifactRole.TESTBENCH,
            terminal_status=terminal,
            stop_reason=reason,
            attempts=tuple(
                item.to_protocol_record(run_id)
                for item in attempts
            ),
            metadata={
                "repair_attempts_used": repair_attempts_used,
                "legacy_artifact_available": True,
            },
        )
        shared = RepairArtifactWriter(
            root / "repair_artifacts"
        ).write(run_record)

        artifact_path = root / "testbench_repair.json"
        result = TestbenchRepairResult(
            status=status,
            testbench_code=testbench_code,
            attempts=tuple(attempts),
            final_preflight=final_preflight,
            reason=reason,
            repair_attempts_used=repair_attempts_used,
            artifact_path=str(artifact_path),
            repair_run=run_record,
            repair_run_path=shared.run_record_path,
            repair_attempt_paths=shared.attempt_paths,
            repair_artifact_manifest_path=(
                shared.artifact_manifest_path
            ),
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
