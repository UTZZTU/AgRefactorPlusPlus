"""Adapt the existing local csim executor to suite-aware evidence."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from agrefactor.config import (
    TestSourceProvenance,
    TestSuiteSpec,
    resolve_test_source,
)
from agrefactor.evidence import (
    TestEvaluationEvidence,
    TestEvaluationStatus,
)
from agrefactor.runtime.budget import BudgetManager
from agrefactor.runtime.trace import TraceRecorder


LegacyCsimExecutor = Callable[..., tuple[str, str]]


@dataclass(frozen=True, slots=True)
class CsimSuiteEvaluationResult:
    """Preserve the legacy result while exposing structured evidence."""

    legacy_status: str
    diagnostic: str
    evidence: TestEvaluationEvidence

    @property
    def succeeded(self) -> bool:
        """Return whether csim reported a successful evaluation."""

        return self.evidence.status is TestEvaluationStatus.PASSED

    def to_legacy_result(self) -> tuple[str, str]:
        """Return the unchanged legacy ``(status, diagnostic)`` tuple."""

        return self.legacy_status, self.diagnostic


class CsimSuiteEvaluator:
    """Run one suite through the existing local csim implementation."""

    def __init__(
        self,
        *,
        executor: LegacyCsimExecutor | None = None,
    ) -> None:
        self._executor = executor

    def evaluate(
        self,
        *,
        work_dir: str | Path,
        context_variables: Any,
        suite: TestSuiteSpec,
        timelimit: int = 60,
        budget: BudgetManager | None = None,
        trace: TraceRecorder | None = None,
    ) -> CsimSuiteEvaluationResult:
        """Execute csim and attach verified suite source identity."""

        if not isinstance(suite, TestSuiteSpec):
            raise TypeError("suite must be a TestSuiteSpec")
        if isinstance(timelimit, bool) or not isinstance(timelimit, int):
            raise TypeError("timelimit must be an integer")
        if timelimit <= 0:
            raise ValueError("timelimit must be positive")
        if budget is not None and not isinstance(budget, BudgetManager):
            raise TypeError("budget must be a BudgetManager or null")
        if trace is not None and not isinstance(trace, TraceRecorder):
            raise TypeError("trace must be a TraceRecorder or null")

        source_provenance = self._resolve_source(
            suite,
            context_variables,
        )

        executor = self._executor or self._load_default_executor()
        raw_result = executor(
            str(work_dir),
            context_variables,
            timelimit,
            budget=budget,
        )
        legacy_status, diagnostic = self._validate_legacy_result(
            raw_result
        )

        invocation_path = Path(work_dir) / "csim_invocation.json"
        invocation = self._read_invocation(invocation_path)
        evidence = self._build_evidence(
            suite=suite,
            legacy_status=legacy_status,
            diagnostic=diagnostic,
            invocation=invocation,
            invocation_path=invocation_path,
            source_provenance=source_provenance,
        )

        if trace is not None:
            trace.record_test_evaluation(evidence)

        return CsimSuiteEvaluationResult(
            legacy_status=legacy_status,
            diagnostic=diagnostic,
            evidence=evidence,
        )

    @staticmethod
    def _resolve_source(
        suite: TestSuiteSpec,
        context_variables: Any,
    ) -> TestSourceProvenance | None:
        if suite.source is None:
            return None
        if suite.testbench_path is None:
            raise ValueError(
                "suite source provenance requires testbench_path"
            )
        try:
            execution_content = context_variables["testbench"]
        except (KeyError, TypeError) as exc:
            raise ValueError(
                "context_variables must contain the exact "
                "testbench source for a provenance-enabled suite"
            ) from exc
        if not isinstance(execution_content, str):
            raise TypeError(
                "context_variables['testbench'] must be a string"
            )
        return resolve_test_source(
            suite.source,
            suite.testbench_path,
            execution_content=execution_content,
        )

    @staticmethod
    def _load_default_executor() -> LegacyCsimExecutor:
        from flow.tools.csim import run_csim

        return run_csim

    @staticmethod
    def _validate_legacy_result(
        value: Any,
    ) -> tuple[str, str]:
        if (
            not isinstance(value, tuple)
            or len(value) != 2
            or not all(isinstance(item, str) for item in value)
        ):
            raise TypeError(
                "csim executor must return a two-string tuple"
            )

        status = value[0].strip()
        diagnostic = value[1].strip()
        if not status:
            raise ValueError("csim executor status must not be empty")
        return status, diagnostic

    @staticmethod
    def _read_invocation(path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        return payload if isinstance(payload, dict) else None

    @classmethod
    def _build_evidence(
        cls,
        *,
        suite: TestSuiteSpec,
        legacy_status: str,
        diagnostic: str,
        invocation: Mapping[str, Any] | None,
        invocation_path: Path,
        source_provenance: TestSourceProvenance | None,
    ) -> TestEvaluationEvidence:
        status, summary = cls._normalize_status(legacy_status)

        passed_cases = 0
        case_counts_complete = False
        if (
            status is TestEvaluationStatus.PASSED
            and suite.case_count is not None
        ):
            passed_cases = suite.case_count
            case_counts_complete = True

        compile_execution = cls._execution_summary(
            invocation,
            "compile_execution",
        )
        simulation_execution = cls._execution_summary(
            invocation,
            "simulation_execution",
        )

        timed_out = bool(
            compile_execution.get("timeout", False)
            or simulation_execution.get("timeout", False)
        )
        return_code = cls._select_return_code(
            compile_execution,
            simulation_execution,
        )

        details: dict[str, Any] = {
            "executor": "flow.tools.csim.run_csim",
            "legacy_status": legacy_status,
            "case_counts_complete": case_counts_complete,
            "compile_execution": compile_execution,
            "simulation_execution": simulation_execution,
        }
        if diagnostic:
            details["diagnostic"] = diagnostic

        artifacts = (
            (str(invocation_path),)
            if invocation_path.is_file()
            else ()
        )

        return TestEvaluationEvidence(
            suite=suite,
            status=status,
            passed_cases=passed_cases,
            failed_cases=0,
            timed_out=timed_out,
            return_code=return_code,
            summary=summary,
            details=details,
            artifacts=artifacts,
            source_provenance=source_provenance,
        )

    @staticmethod
    def _normalize_status(
        legacy_status: str,
    ) -> tuple[TestEvaluationStatus, str]:
        if legacy_status == "succeeded":
            return (
                TestEvaluationStatus.PASSED,
                "CSIM evaluation passed",
            )
        if legacy_status == "csim_failed":
            return (
                TestEvaluationStatus.FAILED,
                "CSIM evaluation failed",
            )
        if legacy_status == "tb_compile_failed":
            return (
                TestEvaluationStatus.ERROR,
                "CSIM testbench compilation failed",
            )
        return (
            TestEvaluationStatus.ERROR,
            "CSIM evaluation returned an unknown status",
        )

    @staticmethod
    def _execution_summary(
        invocation: Mapping[str, Any] | None,
        key: str,
    ) -> dict[str, Any]:
        if invocation is None:
            return {}

        value = invocation.get(key)
        if not isinstance(value, Mapping):
            return {}

        summary: dict[str, Any] = {}
        for field in ("status", "returncode", "timeout"):
            item = value.get(field)
            if item is not None:
                summary[field] = item
        return summary

    @staticmethod
    def _select_return_code(
        compile_execution: Mapping[str, Any],
        simulation_execution: Mapping[str, Any],
    ) -> int | None:
        simulation_code = simulation_execution.get("returncode")
        simulation_status = simulation_execution.get("status")
        simulation_timed_out = (
            simulation_execution.get("timeout") is True
        )

        simulation_was_attempted = (
            simulation_timed_out
            or simulation_status in {"completed", "launch_error"}
        )
        if simulation_was_attempted:
            if (
                isinstance(simulation_code, int)
                and not isinstance(simulation_code, bool)
            ):
                return simulation_code
            return None

        compile_code = compile_execution.get("returncode")
        if (
            isinstance(compile_code, int)
            and not isinstance(compile_code, bool)
        ):
            return compile_code

        return None
