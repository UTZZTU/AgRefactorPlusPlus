"""Public-only RTL COSIM validation with typed, unknown-safe evidence."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from agrefactor.config import EvaluationSplit, TargetProfile
from agrefactor.evidence import (
    FeedbackCategory,
    FeedbackItem,
    FeedbackOwner,
    FeedbackReport,
    FeedbackSeverity,
    FeedbackStage,
)
from agrefactor.runtime.budget import BudgetExceededError
from agrefactor.recovery import classify_public_timeout

from .runner import RunContext


CosimExecutor = Callable[..., Mapping[str, Any]]
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_POLICIES = frozenset({"required", "off"})
_ALLOWED_FAILURES = frozenset(
    {
        "candidate_rtl_functional_failure",
        "public_testbench_failure",
        "toolchain_failure",
        "configuration_failure",
        "timeout",
        "ownership_unknown",
        "budget_exhausted",
    }
)
_EXPECTED_OWNER = {
    "candidate_rtl_functional_failure": "candidate",
    "public_testbench_failure": "testbench",
    "toolchain_failure": "toolchain",
    "configuration_failure": "configuration",
    "timeout": None,
    "ownership_unknown": "unknown",
    "budget_exhausted": "configuration",
}
_OWNER = {
    "candidate_rtl_functional_failure": FeedbackOwner.CANDIDATE,
    "public_testbench_failure": FeedbackOwner.TESTBENCH,
    "toolchain_failure": FeedbackOwner.TOOLCHAIN,
    "configuration_failure": FeedbackOwner.CONFIGURATION,
    "timeout": FeedbackOwner.UNKNOWN,
    "ownership_unknown": FeedbackOwner.UNKNOWN,
    "budget_exhausted": FeedbackOwner.CONFIGURATION,
}
_CATEGORY = {
    "candidate_rtl_functional_failure": FeedbackCategory.FUNCTIONAL_MISMATCH,
    "public_testbench_failure": FeedbackCategory.RUNTIME_CRASH,
    "toolchain_failure": FeedbackCategory.TOOLCHAIN_FAILURE,
    "configuration_failure": FeedbackCategory.INVALID_CONFIGURATION,
    "timeout": FeedbackCategory.TIMEOUT,
    "ownership_unknown": FeedbackCategory.UNKNOWN,
    "budget_exhausted": FeedbackCategory.BUDGET_EXHAUSTED,
}


def validate_cosim_policy(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("cosim_policy must be a string")
    cleaned = value.strip().casefold()
    if cleaned not in _ALLOWED_POLICIES:
        raise ValueError("cosim_policy must be required or off")
    return cleaned


@dataclass(frozen=True, slots=True)
class CosimStageInputs:
    """Explicit inputs for one Public RTL COSIM validation stage."""

    work_dir: str | os.PathLike[str]
    original_code: str
    candidate_code: str
    suite_testbench_codes: Mapping[str, str]
    candidate_top_function: str
    target_profile: TargetProfile
    timelimit: int
    policy: str = "required"

    def __post_init__(self) -> None:
        try:
            raw_root = os.fspath(self.work_dir)
        except TypeError as exc:
            raise TypeError("work_dir must be path-like") from exc
        if not isinstance(raw_root, str) or not raw_root.strip():
            raise ValueError("work_dir must not be empty")

        for name in (
            "original_code",
            "candidate_code",
            "candidate_top_function",
        ):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise TypeError(f"{name} must be a string")
            if not value.strip():
                raise ValueError(f"{name} must not be empty")

        if not isinstance(self.target_profile, TargetProfile):
            raise TypeError("target_profile must be TargetProfile")
        if (
            isinstance(self.timelimit, bool)
            or not isinstance(self.timelimit, int)
        ):
            raise TypeError("timelimit must be an integer")
        if self.timelimit <= 0:
            raise ValueError("timelimit must be positive")

        if not isinstance(self.suite_testbench_codes, Mapping):
            raise TypeError("suite_testbench_codes must be a mapping")
        suites: dict[str, str] = {}
        for raw_suite_id, raw_code in self.suite_testbench_codes.items():
            if not isinstance(raw_suite_id, str):
                raise TypeError("suite IDs must be strings")
            suite_id = raw_suite_id.strip()
            if not suite_id:
                raise ValueError("suite ID must not be empty")
            if not isinstance(raw_code, str):
                raise TypeError("suite testbench code must be a string")
            if not raw_code.strip():
                raise ValueError(f"suite code must not be empty: {suite_id}")
            if suite_id in suites:
                raise ValueError(f"duplicate normalized suite ID: {suite_id}")
            suites[suite_id] = raw_code
        if not suites:
            raise ValueError(
                "Public RTL COSIM requires at least one Public suite"
            )

        object.__setattr__(self, "work_dir", Path(raw_root).expanduser())
        object.__setattr__(self, "suite_testbench_codes", suites)
        object.__setattr__(
            self,
            "candidate_top_function",
            self.candidate_top_function.strip(),
        )
        object.__setattr__(self, "policy", validate_cosim_policy(self.policy))


class CosimValidationStageHandler:
    """Run every task-declared Public suite through real RTL COSIM.

    The stage accepts no Hidden suite. Candidate/Testbench ownership is
    authoritative only when the executor returns the structured protocol pair.
    A claimed pass is also fail-closed unless physical launch and immutable
    evidence are present. Public COSIM repair is suppressed by the validation
    state policy, not by report metadata alone.
    """

    handler_version = 2
    semantics_version = 2
    source = "public_rtl_cosim"

    def __init__(
        self,
        inputs: CosimStageInputs,
        *,
        executor: CosimExecutor | None = None,
    ) -> None:
        if not isinstance(inputs, CosimStageInputs):
            raise TypeError("inputs must be CosimStageInputs")
        self._inputs = inputs
        if executor is None:
            from flow.tools.vitis_cosim import run_vitis_cosim

            executor = run_vitis_cosim
        if not callable(executor):
            raise TypeError("executor must be callable")
        self._executor = executor

    @property
    def inputs(self) -> CosimStageInputs:
        return self._inputs

    def __call__(self, context: RunContext) -> FeedbackReport:
        if not isinstance(context, RunContext):
            raise TypeError("context must be RunContext")

        suites = tuple(
            suite
            for suite in context.task.test_suites
            if suite.split is EvaluationSplit.PUBLIC
        )
        if not suites:
            raise ValueError("task has no Public suite for RTL COSIM")
        declared_ids = tuple(suite.suite_id for suite in suites)
        supplied_ids = tuple(self._inputs.suite_testbench_codes)
        missing = tuple(item for item in declared_ids if item not in supplied_ids)
        extra = tuple(item for item in supplied_ids if item not in declared_ids)
        if missing or extra:
            raise ValueError(
                "Public RTL COSIM suite mapping mismatch: "
                f"missing={list(missing)} extra={list(extra)}"
            )

        base_metadata = {
            "evidence_view": "agent_safe",
            "physical_execution": self._inputs.policy == "required",
            "shared_budget": True,
            "stage_handler_version": self.handler_version,
            "semantics_version": self.semantics_version,
            "cosim_policy": self._inputs.policy,
            "public_rtl_cosim": True,
            "native_vitis_cosim": self._inputs.policy == "required",
            "declared_suite_ids": list(declared_ids),
            "declared_suite_count": len(declared_ids),
            "hidden_input_count": 0,
            "hidden_evidence_exposed": False,
            "repair_allowed": "policy_controlled",
            "evaluation_split": EvaluationSplit.PUBLIC.value,
            "feedback_visible_to_agent": True,
            "execution_policy": "public_cosim_fail_fast",
            "suite_work_dir_layout": "public_cosim/suite_NNN",
        }

        summaries: list[dict[str, Any]] = []
        if self._inputs.policy == "off":
            for index, suite in enumerate(suites, start=1):
                summary = {
                    "suite_id": suite.suite_id,
                    "status": "skipped",
                    "failure_kind": None,
                    "failure_owner": "none",
                    "reason_code": "cosim_policy_off",
                    "timed_out": False,
                    "returncode": None,
                    "tool_launched": False,
                    "cosim_launched": False,
                    "evidence_sha256": None,
                }
                summaries.append(summary)
                self._write_suite_identity_evidence(
                    self._suite_work_dir(index),
                    suite_id=suite.suite_id,
                    suite_version=suite.suite_version,
                    outcome=summary,
                )
            return FeedbackReport(
                report_id=f"{context.run_id}.public-cosim.off",
                source=self.source,
                items=(),
                source_evidence={"suite_summaries": summaries},
                metadata={
                    **base_metadata,
                    "cosim_skipped": True,
                    "attempted_suite_ids": [],
                    "attempted_suite_count": 0,
                    "stopped_early": False,
                    "failure_kind": None,
                    "failure_owner": None,
                    "next_action": "continue_validation",
                },
            )

        items: list[FeedbackItem] = []
        attempted_suite_ids: list[str] = []
        for index, suite in enumerate(suites, start=1):
            suite_id = suite.suite_id
            attempted_suite_ids.append(suite_id)
            work = self._suite_work_dir(index)
            try:
                raw = self._executor(
                    work_dir=work,
                    original_code=self._inputs.original_code,
                    candidate_code=self._inputs.candidate_code,
                    testbench_code=self._inputs.suite_testbench_codes[suite_id],
                    candidate_top_function=(
                        self._inputs.candidate_top_function
                    ),
                    target_profile=self._inputs.target_profile,
                    timelimit=self._inputs.timelimit,
                    budget=context.budget,
                    suite_id=suite_id,
                    runtime_contract=suite.runtime_contract,
                )
                outcome = _normalize_outcome(
                    raw,
                    runtime_contract=suite.runtime_contract,
                )
            except BudgetExceededError as exc:
                outcome = {
                    "status": "blocked",
                    "failure_kind": "budget_exhausted",
                    "failure_owner": "configuration",
                    "reason_code": "cosim_budget_exhausted",
                    "timed_out": False,
                    "returncode": None,
                    "tool_launched": False,
                    "cosim_launched": False,
                    "evidence_sha256": None,
                    "budget_resource": exc.resource,
                }
            except Exception as exc:  # noqa: BLE001 - unknown-safe boundary.
                outcome = {
                    "status": "error",
                    "failure_kind": "ownership_unknown",
                    "failure_owner": "unknown",
                    "reason_code": "cosim_executor_exception",
                    "timed_out": False,
                    "returncode": None,
                    "tool_launched": False,
                    "cosim_launched": False,
                    "evidence_sha256": None,
                    "error_type": type(exc).__name__,
                }

            summary = _safe_summary(suite_id, outcome)
            summaries.append(summary)
            self._write_suite_identity_evidence(
                work,
                suite_id=suite_id,
                suite_version=suite.suite_version,
                outcome=summary,
            )
            if outcome["status"] == "passed":
                continue

            kind = outcome["failure_kind"]
            items.append(
                FeedbackItem(
                    feedback_id=f"{context.run_id}.public-cosim.{index}",
                    stage=FeedbackStage.COSIM,
                    category=_CATEGORY[kind],
                    severity=(
                        FeedbackSeverity.FATAL
                        if kind
                        in {
                            "toolchain_failure",
                            "configuration_failure",
                            "timeout",
                            "budget_exhausted",
                        }
                        else FeedbackSeverity.ERROR
                    ),
                    owner=(
                        {
                            "candidate": FeedbackOwner.CANDIDATE,
                            "testbench": FeedbackOwner.TESTBENCH,
                            "toolchain": FeedbackOwner.TOOLCHAIN,
                            "configuration": FeedbackOwner.CONFIGURATION,
                            "unknown": FeedbackOwner.UNKNOWN,
                        }.get(str(outcome.get("failure_owner")), _OWNER[kind])
                    ),
                    summary=outcome["reason_code"],
                    source=self.source,
                    evidence_ref=outcome.get("evidence_sha256"),
                    metadata=summary,
                )
            )
            break

        return FeedbackReport(
            report_id=f"{context.run_id}.public-cosim",
            source=self.source,
            items=tuple(items),
            source_evidence={"suite_summaries": summaries},
            metadata={
                **base_metadata,
                "cosim_skipped": False,
                "attempted_suite_ids": attempted_suite_ids,
                "attempted_suite_count": len(attempted_suite_ids),
                "stopped_early": len(attempted_suite_ids) < len(suites),
                "failure_kind": (
                    None if not items else summaries[-1]["failure_kind"]
                ),
                "failure_owner": (
                    None if not items else summaries[-1]["failure_owner"]
                ),
                "next_action": (
                    "continue_validation"
                    if not items
                    else (
                        "repair_candidate"
                        if summaries[-1].get("failure_owner") == "candidate"
                        else (
                            "repair_testbench"
                            if summaries[-1].get("failure_owner") == "testbench"
                            else "review_unknown"
                        )
                    )
                ),
            },
        )

    def _suite_work_dir(self, index: int) -> Path:
        return Path(self._inputs.work_dir) / f"suite_{index:03d}"

    def _write_suite_identity_evidence(
        self,
        work_dir: Path,
        *,
        suite_id: str,
        suite_version: str | None,
        outcome: Mapping[str, Any],
    ) -> None:
        payload = {
            "schema_version": 1,
            "evidence_view": "operator_full",
            "phase": "public_rtl_cosim",
            "suite_id": suite_id,
            "suite_version": suite_version,
            "split": EvaluationSplit.PUBLIC.value,
            "policy": self._inputs.policy,
            "evaluation_status": outcome.get("status"),
            "failure_kind": outcome.get("failure_kind"),
            "failure_owner": outcome.get("failure_owner"),
            "reason_code": outcome.get("reason_code"),
            "tool_launched": outcome.get("tool_launched") is True,
            "cosim_launched": outcome.get("cosim_launched") is True,
            "evidence_sha256": outcome.get("evidence_sha256"),
            "hidden_input_count": 0,
            "hidden_evidence_exposed": False,
            "repair_allowed": outcome.get("repair_eligible") is True,
            "timeout_class": outcome.get("timeout_class"),
            "owner_authority": outcome.get("owner_authority"),
            "post_completion_process_linger": (
                outcome.get("post_completion_process_linger") is True
            ),
            "command_completion_proven": (
                outcome.get("command_completion_proven") is True
            ),
            "process_exit_observed": outcome.get("process_exit_observed"),
            "completion_authority": outcome.get("completion_authority"),
        }
        _atomic_json(
            work_dir / "cosim_suite_identity_evidence.json",
            payload,
        )


def _normalize_outcome(
    value: Mapping[str, Any],
    *,
    runtime_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("COSIM executor result must be a mapping")

    raw_status = value.get("status")
    evidence_sha = value.get("evidence_sha256")
    if not isinstance(evidence_sha, str) or _SHA256_RE.fullmatch(evidence_sha) is None:
        evidence_sha = None

    if raw_status == "passed":
        physically_proven = (
            value.get("tool_launched") is True
            and value.get("cosim_launched") is True
            and value.get("timed_out") is not True
            and value.get("returncode") == 0
            and evidence_sha is not None
        )
        post_completion_proven = (
            value.get("reason_code")
            == "cosim_passed_post_completion_process_linger"
            and value.get("tool_launched") is True
            and value.get("cosim_launched") is True
            and value.get("timed_out") is True
            and value.get("post_completion_process_linger") is True
            and value.get("command_completion_proven") is True
            and value.get("process_exit_observed") is False
            and value.get("completion_authority")
            == "fresh_tcl_status_and_identity_bound_typed_outcome_v1"
            and evidence_sha is not None
        )
        if physically_proven or post_completion_proven:
            return {
                "status": "passed",
                "failure_kind": None,
                "failure_owner": "none",
                "reason_code": (
                    "cosim_passed_post_completion_process_linger"
                    if post_completion_proven
                    else "cosim_passed"
                ),
                "timed_out": post_completion_proven,
                "returncode": (
                    value.get("returncode")
                    if isinstance(value.get("returncode"), int)
                    and not isinstance(value.get("returncode"), bool)
                    else None
                ),
                "tool_launched": True,
                "cosim_launched": True,
                "evidence_sha256": evidence_sha,
                "post_completion_process_linger": post_completion_proven,
                "command_completion_proven": (
                    True if post_completion_proven else False
                ),
                "process_exit_observed": (
                    False if post_completion_proven else True
                ),
                "completion_authority": (
                    value.get("completion_authority")
                    if post_completion_proven
                    else None
                ),
            }
        return {
            "status": "failed",
            "failure_kind": "ownership_unknown",
            "failure_owner": "unknown",
            "reason_code": "cosim_pass_missing_typed_execution_evidence",
            "timed_out": False,
            "returncode": (
                value.get("returncode")
                if isinstance(value.get("returncode"), int)
                and not isinstance(value.get("returncode"), bool)
                else None
            ),
            "tool_launched": value.get("tool_launched") is True,
            "cosim_launched": value.get("cosim_launched") is True,
            "evidence_sha256": evidence_sha,
            "post_completion_process_linger": False,
            "command_completion_proven": False,
            "process_exit_observed": False,
            "completion_authority": None,
        }

    kind = value.get("failure_kind")
    owner = value.get("failure_owner")
    timeout = None
    if kind == "timeout" or value.get("timed_out") is True:
        timeout = classify_public_timeout(value, stage="public_cosim")
        kind = "timeout"
        owner = timeout.owner.value
    elif (
        kind not in _ALLOWED_FAILURES
        or owner != _EXPECTED_OWNER.get(kind)
        or (
            kind == "candidate_rtl_functional_failure"
            and (
                value.get("owner_authority") != "deterministic_proven"
                or not isinstance(runtime_contract, Mapping)
                or runtime_contract.get("schema_version") != 1
                or runtime_contract.get("kind")
                != "public_differential_self_check_v1"
                or not isinstance(
                    runtime_contract.get("candidate_mismatch_returncodes"),
                    (list, tuple),
                )
                or value.get("testbench_returncode")
                not in runtime_contract.get("candidate_mismatch_returncodes", ())
            )
        )
    ):
        kind = "ownership_unknown"
        owner = "unknown"

    status = str(raw_status or "error")
    if status not in {"failed", "blocked", "error"}:
        status = "error"
    reason = value.get("reason_code")
    if not isinstance(reason, str) or not reason.strip():
        reason = "cosim_ownership_unknown"

    return {
        "status": status,
        "failure_kind": kind,
        "failure_owner": owner,
        "reason_code": reason.strip()[:300],
        "timed_out": value.get("timed_out") is True,
        "returncode": (
            value.get("returncode")
            if isinstance(value.get("returncode"), int)
            else None
        ),
        "tool_launched": value.get("tool_launched") is True,
        "cosim_launched": value.get("cosim_launched") is True,
        "evidence_sha256": evidence_sha,
        "timeout_class": (
            None if timeout is None else timeout.timeout_class.value
        ),
        "testbench_returncode": (
            value.get("testbench_returncode")
            if isinstance(value.get("testbench_returncode"), int)
            and not isinstance(value.get("testbench_returncode"), bool)
            else None
        ),
        "owner_authority": (
            value.get("owner_authority")
            if timeout is None
            else timeout.owner_authority
        ),
        "repair_eligible": (
            (
                kind == "candidate_rtl_functional_failure"
                and owner == "candidate"
                and value.get("owner_authority") == "deterministic_proven"
            )
            if timeout is None
            else timeout.repair_eligible
        ),
        "advisory_eligible": (
            False if timeout is None else timeout.advisory_eligible
        ),
        "evidence_complete": (
            False if timeout is None else timeout.evidence_complete
        ),
    }


def _safe_summary(
    suite_id: str,
    outcome: Mapping[str, Any],
) -> dict[str, Any]:
    evidence_sha = outcome.get("evidence_sha256")
    return {
        "suite_id": suite_id,
        "status": outcome.get("status"),
        "failure_kind": outcome.get("failure_kind"),
        "failure_owner": outcome.get("failure_owner"),
        "reason_code": outcome.get("reason_code"),
        "timed_out": outcome.get("timed_out") is True,
        "returncode": (
            outcome.get("returncode")
            if isinstance(outcome.get("returncode"), int)
            else None
        ),
        "tool_launched": outcome.get("tool_launched") is True,
        "cosim_launched": outcome.get("cosim_launched") is True,
        "timeout_class": outcome.get("timeout_class"),
        "owner_authority": outcome.get("owner_authority"),
        "repair_eligible": outcome.get("repair_eligible") is True,
        "advisory_eligible": outcome.get("advisory_eligible") is True,
        "evidence_complete": outcome.get("evidence_complete") is True,
        "post_completion_process_linger": (
            outcome.get("post_completion_process_linger") is True
        ),
        "command_completion_proven": (
            outcome.get("command_completion_proven") is True
        ),
        "process_exit_observed": outcome.get("process_exit_observed"),
        "completion_authority": outcome.get("completion_authority"),
        "evidence_sha256": (
            evidence_sha
            if isinstance(evidence_sha, str)
            and _SHA256_RE.fullmatch(evidence_sha) is not None
            else None
        ),
    }


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.loads(
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        )
    )
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(
                encoded,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
