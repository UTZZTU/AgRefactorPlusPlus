"""Frozen Stage 3.8 multi-kernel evaluation protocol.

This module does not implement an optimizer.  It materializes an immutable
multi-kernel corpus, builds a fair run matrix for the accepted product
``optimize``/``full`` commands and Legacy ``simple_iter``, independently
qualifies external Legacy candidates, and aggregates only typed artifacts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import statistics
from typing import Any

from agrefactor.config import (
    EvaluationSplit,
    RunMode,
    TaskSpec,
    resolve_target_profile,
)
from agrefactor.optimization import (
    CandidateQualificationRequest,
    CandidateRecord,
    CandidateStatus,
    QualificationEvidenceCache,
    QualificationStage,
    Stage3QualificationOrchestrator,
    ValidationCacheIdentity,
    build_toolchain_fingerprint,
    suite_identity_from_file,
)
from agrefactor.runtime import BudgetLimits, BudgetManager, RunContext, TraceRecorder, file_sha256
from agrefactor.smoke import STAGE2_SMOKE_CASES


S38_PROTOCOL_SCHEMA_VERSION = 1
S38_RUN_RECORD_SCHEMA_VERSION = 3
S38_SUPPORTED_RUN_RECORD_READ_VERSIONS = (1, 2, 3)
S38_REPORT_SCHEMA_VERSION = 3
DEFAULT_S38_CASE_IDS = ("array-map", "reduction", "nested-stencil")
DEFAULT_S38_ARMS = ("safe-optimize", "source-full", "simple-iter")


class S38EvaluationInfrastructureError(RuntimeError):
    """Typed S3.8 evaluator/observer infrastructure failure."""


class S38QualificationObserverError(S38EvaluationInfrastructureError):
    """Raised when persisted qualification evidence violates its typed order."""


_REQUIRED_STAGE_ORDER = (
    "source",
    "preflight",
    "public",
    "csynth",
    "hidden",
    "ppa",
    "feasibility",
)


class S38Arm(str, Enum):
    SAFE_OPTIMIZE = "safe-optimize"
    SOURCE_FULL = "source-full"
    SIMPLE_ITER = "simple-iter"


@dataclass(frozen=True, slots=True)
class S38Protocol:
    model: str
    target: str = "vitis-2023.2-default"
    case_ids: tuple[str, ...] = DEFAULT_S38_CASE_IDS
    repeats: int = 2
    arms: tuple[S38Arm, ...] = tuple(S38Arm(item) for item in DEFAULT_S38_ARMS)
    reasoning_effort: str = "medium"
    provider_reasoning_effort: str | None = None
    max_output_tokens: int | None = None
    model_family: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    max_llm_calls: int = 14
    max_tool_calls: int = 128
    max_compile_calls: int = 48
    max_csim_calls: int = 32
    max_csynth_calls: int = 16
    max_wall_time_s: float = 7200.0
    csim_timeout_s: int = 180
    csynth_timeout_s: int = 900
    simple_iter_iterations: int = 14
    schema_version: int = field(default=S38_PROTOCOL_SCHEMA_VERSION, init=False)

    def __post_init__(self) -> None:
        model = _required_text(self.model, "model")
        target = _required_text(self.target, "target")
        case_ids = tuple(_required_id(item, "case_id") for item in self.case_ids)
        if len(case_ids) < 3 or len(set(case_ids)) != len(case_ids):
            raise ValueError("S3.8 requires at least three distinct kernels")
        known = {case.case_id for case in STAGE2_SMOKE_CASES}
        unknown = sorted(set(case_ids) - known)
        if unknown:
            raise ValueError(f"unknown Stage 2 smoke cases: {unknown}")
        repeats = _positive_int(self.repeats, "repeats")
        if repeats < 2:
            raise ValueError("S3.8 acceptance requires at least two repeats")
        arms = tuple(item if isinstance(item, S38Arm) else S38Arm(item) for item in self.arms)
        if set(arms) != set(S38Arm):
            raise ValueError("S3.8 acceptance requires optimize, full, and simple_iter")
        if len(arms) != len(set(arms)):
            raise ValueError("arms must be unique")
        effort = _required_text(self.reasoning_effort, "reasoning_effort").lower()
        if effort not in {"low", "medium", "high"}:
            raise ValueError("reasoning_effort must be low, medium, or high")
        for name in (
            "max_llm_calls",
            "max_tool_calls",
            "max_compile_calls",
            "max_csim_calls",
            "max_csynth_calls",
            "csim_timeout_s",
            "csynth_timeout_s",
            "simple_iter_iterations",
        ):
            _positive_int(getattr(self, name), name)
        wall = _positive_number(self.max_wall_time_s, "max_wall_time_s")
        if self.simple_iter_iterations > self.max_llm_calls:
            raise ValueError("simple_iter_iterations exceeds shared LLM-call ceiling")
        if self.simple_iter_iterations + 2 > self.max_csynth_calls:
            raise ValueError("simple_iter_iterations plus independent qualifications exceed CSYNTH ceiling")
        if self.simple_iter_iterations + 6 > self.max_compile_calls:
            raise ValueError("simple_iter worst-case compile calls exceed shared ceiling")
        if self.simple_iter_iterations + 4 > self.max_csim_calls:
            raise ValueError("simple_iter worst-case CSIM calls exceed shared ceiling")
        if (3 * self.simple_iter_iterations) + 12 > self.max_tool_calls:
            raise ValueError("simple_iter worst-case tool calls exceed shared ceiling")
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "case_ids", case_ids)
        object.__setattr__(self, "repeats", repeats)
        object.__setattr__(self, "arms", arms)
        provider_effort = _optional_text(self.provider_reasoning_effort)
        if provider_effort is not None:
            provider_effort = provider_effort.lower()
        output_tokens = (
            None
            if self.max_output_tokens is None
            else _positive_int(self.max_output_tokens, "max_output_tokens")
        )
        object.__setattr__(self, "reasoning_effort", effort)
        object.__setattr__(self, "provider_reasoning_effort", provider_effort)
        object.__setattr__(self, "max_output_tokens", output_tokens)
        object.__setattr__(self, "max_wall_time_s", wall)
        object.__setattr__(self, "model_family", _optional_text(self.model_family))
        object.__setattr__(self, "base_url", _optional_text(self.base_url))
        object.__setattr__(self, "api_key_env", _optional_text(self.api_key_env))

    @property
    def run_count(self) -> int:
        return len(self.case_ids) * self.repeats * len(self.arms)

    @property
    def identity_sha256(self) -> str:
        return _mapping_sha256(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "model": self.model,
            "model_family": self.model_family,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "target": self.target,
            "case_ids": list(self.case_ids),
            "repeats": self.repeats,
            "arms": [item.value for item in self.arms],
            "reasoning_effort": self.reasoning_effort,
            "effective_model_request": {
                "provider_reasoning_effort": self.provider_reasoning_effort,
                "max_output_tokens": self.max_output_tokens,
            },
            "budgets": {
                "max_llm_calls": self.max_llm_calls,
                "max_tool_calls": self.max_tool_calls,
                "max_compile_calls": self.max_compile_calls,
                "max_csim_calls": self.max_csim_calls,
                "max_csynth_calls": self.max_csynth_calls,
                "max_wall_time_s": self.max_wall_time_s,
            },
            "timeouts": {
                "csim_timeout_s": self.csim_timeout_s,
                "csynth_timeout_s": self.csynth_timeout_s,
            },
            "simple_iter_iterations": self.simple_iter_iterations,
            "fairness": {
                "same_model": True,
                "same_target": True,
                "same_public_hidden_suites": True,
                "same_hard_budget_ceilings": True,
                "same_effective_model_request_parameters": True,
                "same_repeats": True,
                "legacy_candidate_independently_qualified": True,
                "hidden_exposed_to_models": False,
            },
        }


@dataclass(frozen=True, slots=True)
class S38RunSpec:
    sequence: int
    case_id: str
    repeat_index: int
    arm: S38Arm
    run_id: str

    def __post_init__(self) -> None:
        _positive_int(self.sequence, "sequence")
        case_id = _required_id(self.case_id, "case_id")
        repeat = _positive_int(self.repeat_index, "repeat_index")
        arm = self.arm if isinstance(self.arm, S38Arm) else S38Arm(self.arm)
        expected = f"s38-{case_id}-r{repeat}-{arm.value}"
        if self.run_id != expected:
            raise ValueError(f"run_id must be {expected}")
        object.__setattr__(self, "case_id", case_id)
        object.__setattr__(self, "repeat_index", repeat)
        object.__setattr__(self, "arm", arm)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "case_id": self.case_id,
            "repeat_index": self.repeat_index,
            "arm": self.arm.value,
            "run_id": self.run_id,
        }


def build_s38_run_matrix(protocol: S38Protocol) -> tuple[S38RunSpec, ...]:
    if not isinstance(protocol, S38Protocol):
        raise TypeError("protocol must be S38Protocol")
    values: list[S38RunSpec] = []
    sequence = 1
    for repeat in range(1, protocol.repeats + 1):
        for case_id in protocol.case_ids:
            for arm in protocol.arms:
                values.append(
                    S38RunSpec(
                        sequence=sequence,
                        case_id=case_id,
                        repeat_index=repeat,
                        arm=arm,
                        run_id=f"s38-{case_id}-r{repeat}-{arm.value}",
                    )
                )
                sequence += 1
    return tuple(values)


def materialize_s38_corpus(root: str | os.PathLike[str], case_ids: Sequence[str]) -> dict[str, Any]:
    destination = Path(root).expanduser().resolve()
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"corpus root is not empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    lookup = {case.case_id: case for case in STAGE2_SMOKE_CASES}
    kernels: list[dict[str, Any]] = []
    for case_id in case_ids:
        case = lookup[case_id]
        case_root = destination / case_id
        direct_root = case_root / "direct"
        full_root = case_root / "full"
        direct_root.mkdir(parents=True)
        full_root.mkdir(parents=True)
        direct_paths = {
            "baseline": _write_text(direct_root / "baseline.cpp", case.candidate_code),
            "reference": _write_text(direct_root / "reference.cpp", case.original_code),
            "public": _write_text(direct_root / "public.cpp", case.public_testbench_code),
            "hidden": _write_text(direct_root / "hidden.cpp", case.hidden_testbench_code),
        }
        full_candidate_top = "original_top_hls"
        full_public = _replace_symbol(case.public_testbench_code, case.kernel_name, full_candidate_top)
        full_hidden = _replace_symbol(case.hidden_testbench_code, case.kernel_name, full_candidate_top)
        full_paths = {
            "source": _write_text(full_root / "source.cpp", case.original_code),
            "public": _write_text(full_root / "public.cpp", full_public),
            "hidden": _write_text(full_root / "hidden.cpp", full_hidden),
        }
        kernels.append(
            {
                "case_id": case.case_id,
                "kernel_type": case.kernel_type.value,
                "tags": list(case.tags),
                "direct": {
                    "top_function": case.kernel_name,
                    "reference_top_function": "original_top",
                    **{name: str(path) for name, path in direct_paths.items()},
                },
                "full": {
                    "top_function": "original_top",
                    "expected_candidate_top_function": full_candidate_top,
                    **{name: str(path) for name, path in full_paths.items()},
                },
                "suite_sha256": {
                    "direct_public": file_sha256(direct_paths["public"]),
                    "direct_hidden": file_sha256(direct_paths["hidden"]),
                    "full_public": file_sha256(full_paths["public"]),
                    "full_hidden": file_sha256(full_paths["hidden"]),
                },
                "hidden_marker_sha256": sha256(case.hidden_secret_marker.encode("utf-8")).hexdigest(),
            }
        )
    manifest = {
        "schema_version": S38_PROTOCOL_SCHEMA_VERSION,
        "source": "committed_stage2_smoke_corpus",
        "claim_scope": "three_or_more_distinct_real_vitis_kernels",
        "kernel_count": len(kernels),
        "kernels": kernels,
    }
    manifest["manifest_sha256"] = _mapping_sha256(manifest)
    _write_json(destination / "corpus_manifest.json", manifest)
    return manifest


def qualify_external_candidate(
    *,
    output_root: str | os.PathLike[str],
    run_id: str,
    candidate_path: str | os.PathLike[str],
    top_function: str,
    reference_path: str | os.PathLike[str],
    reference_top_function: str,
    public_test_path: str | os.PathLike[str],
    hidden_test_path: str | os.PathLike[str],
    target_name: str,
    csim_timeout_s: int,
    csynth_timeout_s: int,
    max_wall_time_s: float,
) -> dict[str, Any]:
    """Qualify a Legacy-generated candidate with the Stage 3 authority chain."""

    from agrefactor.product.stage3_optimizer import build_direct_optimization_material
    from agrefactor.runtime import (
        CsimStageInputs,
        CsimValidationStageHandler,
        CsynthStageInputs,
        CsynthValidationStageHandler,
        PreflightStageInputs,
        PreflightValidationStageHandler,
    )
    from flow.tools.csynth import probe_csynth_version, resolve_csynth_command

    root = Path(output_root).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"qualification root is not empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    candidate = Path(candidate_path).expanduser().resolve()
    reference = Path(reference_path).expanduser().resolve()
    public = Path(public_test_path).expanduser().resolve()
    hidden = Path(hidden_test_path).expanduser().resolve()
    target = resolve_target_profile(target_name)
    material = build_direct_optimization_material(
        source_path=candidate,
        top_function=top_function,
        reference_source_path=reference,
        reference_top_function=reference_top_function,
        public_test_paths=(public,),
        hidden_test_paths=(hidden,),
        target=target,
    )
    resolution = resolve_csynth_command(target)
    probe = probe_csynth_version(resolution, target.toolchain_version)
    if probe.get("status") not in {"matched", "detected"}:
        raise S38EvaluationInfrastructureError(
            f"Vitis toolchain observation failed: {probe.get('status')}"
        )
    toolchain_manifest = {
        "toolchain": target.toolchain,
        "requested_version": target.toolchain_version,
        "actual_version": probe.get("actual"),
        "resolved_executable": resolution.get("resolved_executable"),
        "probe_status": probe.get("status"),
        "probe_stdout_sha256": sha256(str(probe.get("stdout") or "").encode("utf-8")).hexdigest(),
        "probe_stderr_sha256": sha256(str(probe.get("stderr") or "").encode("utf-8")).hexdigest(),
    }
    fingerprint = build_toolchain_fingerprint(toolchain_manifest)
    suite_identities = tuple(
        suite_identity_from_file(
            suite_id=item.suite_id,
            split=item.split.value,
            path=str(item.testbench_path),
            suite_version=item.suite_version,
            source_identity=(None if item.source is None else item.source.to_dict()),
        )
        for item in material.suites
    )
    cache_identity = ValidationCacheIdentity.build(
        source_sha256=file_sha256(candidate),
        effective_target=target.to_effective_dict(),
        toolchain_fingerprint_sha256=fingerprint,
        suites=suite_identities,
        compile_flags=target.compile_flags,
        clock_period_ns=target.clock_period_ns,
        device=str(target.device),
        parser_profile=target.parser_profile,
    )
    budget = BudgetManager(
        BudgetLimits(
            max_llm_calls=0,
            max_tool_calls=6,
            max_compile_calls=3,
            max_csim_calls=2,
            max_csynth_calls=1,
            max_wall_time_s=float(max_wall_time_s),
        )
    )
    task = material.task
    trace = TraceRecorder(run_id, task_id=task.task_id, output_path=root / "trace.jsonl")
    context = RunContext(run_id=run_id, task=task, budget=budget, trace=trace)
    candidate_code = candidate.read_text(encoding="utf-8")
    reference_code = reference.read_text(encoding="utf-8")
    public_code = public.read_text(encoding="utf-8")
    hidden_code = hidden.read_text(encoding="utf-8")
    work = root / "qualification"
    handlers = {
        QualificationStage.PREFLIGHT: PreflightValidationStageHandler(
            PreflightStageInputs(
                work_dir=work / "preflight",
                testbench_code=public_code,
                original_code=reference_code,
                candidate_code=candidate_code,
            )
        ),
        QualificationStage.PUBLIC: CsimValidationStageHandler(
            CsimStageInputs(
                work_dir=work / "public",
                original_code=reference_code,
                candidate_code=candidate_code,
                suite_testbench_codes={"public-1": public_code},
                timelimit=int(csim_timeout_s),
            ),
            split=EvaluationSplit.PUBLIC,
        ),
        QualificationStage.CSYNTH: CsynthValidationStageHandler(
            CsynthStageInputs(
                work_dir=work / "csynth",
                candidate_code=candidate_code,
                timelimit=int(csynth_timeout_s),
            )
        ),
        QualificationStage.HIDDEN: CsimValidationStageHandler(
            CsimStageInputs(
                work_dir=work / "hidden",
                original_code=reference_code,
                candidate_code=candidate_code,
                suite_testbench_codes={"hidden-1": hidden_code},
                timelimit=int(csim_timeout_s),
            ),
            split=EvaluationSplit.HIDDEN,
        ),
    }
    record = CandidateRecord(
        candidate_id="baseline",
        sequence=0,
        parent_candidate_id=None,
        hypothesis_id=None,
        level=None,
        source_sha256=file_sha256(candidate),
        source_artifact="candidates/baseline/source.cpp",
        status=CandidateStatus.GENERATED,
        budget_before=budget.snapshot().to_dict(),
        created_at_utc=_utc_now(),
    )
    request = CandidateQualificationRequest(
        qualification_id=f"{run_id}.external",
        candidate=record,
        source_path=candidate,
        ppa_work_dir=work / "csynth",
        top_function=top_function,
        cache_identity=cache_identity,
        resource_limits=target.resource_limits.to_dict(),
    )
    result = Stage3QualificationOrchestrator(
        handlers,
        cache=QualificationEvidenceCache(root / "validation_cache"),
    ).run(context, request)
    trace.write_json(root / "trace.json")
    _write_json(root / "qualification_result.json", result.to_dict())
    _write_json(root / "cache_identity.json", cache_identity.to_dict())
    _write_json(root / "toolchain_manifest.json", toolchain_manifest)
    if result.ppa is not None:
        _write_json(root / "ppa.json", result.ppa.to_dict())
    usage = budget.snapshot().to_dict()
    summary = {
        "schema_version": S38_RUN_RECORD_SCHEMA_VERSION,
        "run_id": run_id,
        "accepted": bool(result.accepted),
        "qualification_status": result.status.value,
        "terminal_stage": result.steps[-1].stage.value,
        "terminal_outcome": result.steps[-1].outcome.value,
        "failure_kind": result.steps[-1].metadata.get("failure_kind"),
        "failure_owner": result.steps[-1].metadata.get("failure_owner"),
        "correctness_passed": bool(result.correctness_passed),
        "synthesis_passed": bool(result.synthesis_passed),
        "objective_feasible": result.objective_feasible,
        "stage_order": [item.stage.value for item in result.steps],
        "stage_outcomes": {item.stage.value: item.outcome.value for item in result.steps},
        "reason_codes": sorted({code for item in result.steps for code in item.reason_codes}),
        "ppa": None if result.ppa is None else result.ppa.to_dict(),
        "budget_usage": usage,
        "model_calls": 0,
        "hidden_exposed_to_model": False,
        "artifact_root": str(root),
    }
    if tuple(summary["stage_order"]) != _REQUIRED_STAGE_ORDER[: len(summary["stage_order"])]:
        raise S38QualificationObserverError(
            f"qualification stage order mismatch: {summary['stage_order']}"
        )
    _write_json(root / "summary.json", summary)
    return summary


def legacy_record_has_execution_evidence(
    record: Mapping[str, Any] | None,
) -> bool:
    """Return whether one Legacy record proves a valid v3 comparison."""

    if not isinstance(record, Mapping):
        return False
    if record.get("schema_version") != S38_RUN_RECORD_SCHEMA_VERSION:
        return False
    required_true = (
        "independent_baseline_qualification_observed",
        "legacy_execution_started",
        "legacy_process_completed",
        "legacy_evaluation_artifact_observed",
        "legacy_reference_supplied",
        "legacy_reference_isolated",
        "legacy_harness_evidence_observed",
    )
    if any(record.get(name) is not True for name in required_true):
        return False
    if record.get("legacy_harness_contract_version") != 1:
        return False
    if int(record.get("llm_calls") or 0) < 1:
        return False
    if record.get("accepted") is True or int(record.get("qualified_candidate_count") or 0) > 0:
        return record.get("independent_final_qualification_observed") is True
    return True

def aggregate_s38_records(
    records: Sequence[Mapping[str, Any]],
    *,
    protocol: S38Protocol,
) -> dict[str, Any]:
    normalized = [dict(item) for item in records]
    matrix = build_s38_run_matrix(protocol)
    expected = {item.run_id: item for item in matrix}
    expected_ids = set(expected)
    actual_ids = [str(item.get("run_id")) for item in normalized]
    actual_id_set = set(actual_ids)
    missing = sorted(expected_ids - actual_id_set)
    unexpected = sorted(actual_id_set - expected_ids)
    duplicate_ids = sorted(
        {run_id for run_id in actual_ids if actual_ids.count(run_id) > 1}
    )
    contract_issues: list[str] = []
    for item in normalized:
        run_id = str(item.get("run_id"))
        spec = expected.get(run_id)
        if spec is None:
            continue
        expected_fields = {
            "case_id": spec.case_id,
            "repeat_index": spec.repeat_index,
            "arm": spec.arm.value,
            "protocol_identity_sha256": protocol.identity_sha256,
        }
        for key, expected_value in expected_fields.items():
            if item.get(key) != expected_value:
                contract_issues.append(
                    f"{run_id}:{key}:expected={expected_value!r}:actual={item.get(key)!r}"
                )
        schema_version = item.get("schema_version")
        if schema_version not in S38_SUPPORTED_RUN_RECORD_READ_VERSIONS:
            contract_issues.append(
                f"{run_id}:unsupported_schema_version:{schema_version!r}"
            )
        if spec.arm is S38Arm.SIMPLE_ITER:
            if schema_version != S38_RUN_RECORD_SCHEMA_VERSION:
                contract_issues.append(
                    f"{run_id}:legacy_record_requires_schema_v3"
                )
            if item.get("independent_baseline_qualification_observed") is not True:
                contract_issues.append(
                    f"{run_id}:legacy_baseline_qualification_not_observed"
                )
            if item.get("legacy_execution_started") is not True:
                contract_issues.append(
                    f"{run_id}:legacy_execution_not_started"
                )
            if item.get("legacy_evaluation_artifact_observed") is not True:
                contract_issues.append(
                    f"{run_id}:legacy_evaluation_artifact_not_observed"
                )
            if item.get("legacy_process_completed") is not True:
                contract_issues.append(
                    f"{run_id}:legacy_process_not_completed"
                )
            if item.get("legacy_reference_supplied") is not True:
                contract_issues.append(
                    f"{run_id}:legacy_reference_not_supplied"
                )
            if item.get("legacy_reference_isolated") is not True:
                contract_issues.append(
                    f"{run_id}:legacy_reference_not_isolated"
                )
            if item.get("legacy_harness_contract_version") != 1:
                contract_issues.append(
                    f"{run_id}:legacy_harness_contract_invalid"
                )
            if item.get("legacy_harness_evidence_observed") is not True:
                contract_issues.append(
                    f"{run_id}:legacy_harness_evidence_not_observed"
                )
            if int(item.get("llm_calls") or 0) < 1:
                contract_issues.append(
                    f"{run_id}:legacy_physical_model_call_not_observed"
                )
            if item.get("accepted") is True or int(item.get("qualified_candidate_count") or 0) > 0:
                if item.get("independent_final_qualification_observed") is not True:
                    contract_issues.append(
                        f"{run_id}:legacy_final_qualification_not_observed"
                    )
        if item.get("hidden_exposed_to_model") is True:
            contract_issues.append(f"{run_id}:hidden_exposed_to_model")
        if item.get("automatic_model_retry") is True:
            contract_issues.append(f"{run_id}:automatic_model_retry")
        if item.get("raw_prompt_response_persisted") is True:
            contract_issues.append(f"{run_id}:raw_prompt_response_persisted")

    by_arm: dict[str, list[dict[str, Any]]] = {
        arm.value: [] for arm in protocol.arms
    }
    by_kernel: dict[str, list[dict[str, Any]]] = {
        case_id: [] for case_id in protocol.case_ids
    }
    for item in normalized:
        arm = str(item.get("arm"))
        case_id = str(item.get("case_id"))
        if arm in by_arm:
            by_arm[arm].append(item)
        if case_id in by_kernel:
            by_kernel[case_id].append(item)

    arm_metrics: dict[str, Any] = {}
    for arm, items in by_arm.items():
        accepted = [item for item in items if item.get("accepted") is True]
        latencies = _record_metric_values(accepted, "ppa", "latency_cycles_max")
        initiation_intervals = _record_metric_values(
            accepted, "ppa", "initiation_interval_max"
        )
        resource_ratios = _record_metric_values(
            accepted, "ppa", "max_resource_utilization_ratio"
        )
        improvement_ratios = [
            value
            for item in accepted
            for value in [_latency_improvement_ratio(item)]
            if value is not None
        ]
        arm_metrics[arm] = {
            "planned_runs": len(protocol.case_ids) * protocol.repeats,
            "observed_runs": len(items),
            "accepted_runs": len(accepted),
            "success_rate": 0.0 if not items else len(accepted) / len(items),
            "latency_cycles_max": _distribution(latencies),
            "latency_improvement_ratio": _distribution(improvement_ratios),
            "initiation_interval_max": _distribution(initiation_intervals),
            "max_resource_utilization_ratio": _distribution(resource_ratios),
            "llm_calls": _record_distribution(items, "llm_calls"),
            "tool_calls": _record_distribution(items, "tool_calls"),
            "compile_calls": _record_distribution(items, "compile_calls"),
            "csim_calls": _record_distribution(items, "csim_calls"),
            "csynth_calls": _record_distribution(items, "csynth_calls"),
            "wall_time_s": _record_distribution(items, "wall_time_s"),
            "invalid_candidate_ratio": _record_distribution(
                items, "invalid_candidate_ratio"
            ),
            "rollback_count": _record_distribution(items, "rollback_count"),
            "infrastructure_failures": sum(
                1
                for item in items
                if item.get("failure_class") == "infrastructure"
            ),
            "candidate_failures": sum(
                1 for item in items if item.get("failure_class") == "candidate"
            ),
            "invalid_candidate_runs": sum(
                1 for item in items if item.get("failure_class") == "candidate"
            ),
            "rollback_observed_runs": sum(
                1 for item in items if int(item.get("rollback_count") or 0) > 0
            ),
            "best_correct_protected_runs": sum(
                1 for item in items if item.get("best_correct_protected") is True
            ),
        }

    kernel_metrics: dict[str, Any] = {}
    for case_id, items in by_kernel.items():
        kernel_metrics[case_id] = {
            "planned_runs": protocol.repeats * len(protocol.arms),
            "observed_runs": len(items),
            "accepted_runs_by_arm": {
                arm.value: sum(
                    1
                    for item in items
                    if item.get("arm") == arm.value
                    and item.get("accepted") is True
                )
                for arm in protocol.arms
            },
            "real_csynth_observed": any(
                int(item.get("csynth_calls") or 0) > 0 for item in items
            ),
        }

    complete = (
        not missing
        and not unexpected
        and not duplicate_ids
        and len(normalized) == protocol.run_count
    )
    legacy_records = by_arm[S38Arm.SIMPLE_ITER.value]
    legacy_execution_observed_runs = sum(
        1 for item in legacy_records if legacy_record_has_execution_evidence(item)
    )
    legacy_baseline_qualification_observed_runs = sum(
        1
        for item in legacy_records
        if item.get("independent_baseline_qualification_observed") is True
    )
    legacy_process_completed_runs = sum(
        1 for item in legacy_records if item.get("legacy_process_completed") is True
    )
    legacy_reference_isolated_runs = sum(
        1 for item in legacy_records if item.get("legacy_reference_isolated") is True
    )
    legacy_harness_validated_runs = sum(
        1
        for item in legacy_records
        if item.get("legacy_harness_contract_version") == 1
        and item.get("legacy_harness_evidence_observed") is True
    )
    legacy_required_runs = len(protocol.case_ids) * protocol.repeats
    legacy_comparison_executed = bool(
        legacy_execution_observed_runs == legacy_required_runs
        and legacy_baseline_qualification_observed_runs == legacy_required_runs
        and legacy_process_completed_runs == legacy_required_runs
        and legacy_reference_isolated_runs == legacy_required_runs
        and legacy_harness_validated_runs == legacy_required_runs
    )
    evaluation_valid = (
        complete
        and not contract_issues
        and all(
            arm_metrics[arm.value]["observed_runs"]
            == len(protocol.case_ids) * protocol.repeats
            for arm in protocol.arms
        )
        and all(
            kernel_metrics[case_id]["observed_runs"]
            == protocol.repeats * len(protocol.arms)
            for case_id in protocol.case_ids
        )
        and legacy_comparison_executed
    )
    report = {
        "schema_version": S38_REPORT_SCHEMA_VERSION,
        "supported_run_record_schema_versions": list(
            S38_SUPPORTED_RUN_RECORD_READ_VERSIONS
        ),
        "protocol_identity_sha256": protocol.identity_sha256,
        "claim_scope": "bounded_multi_kernel_repeated_evaluation",
        "minimum_acceptance_profile": {
            "kernel_count": 3,
            "repeats": 2,
            "arms": [item.value for item in S38Arm],
        },
        "planned_run_count": protocol.run_count,
        "observed_run_count": len(normalized),
        "complete_matrix": complete,
        "evaluation_valid": evaluation_valid,
        "missing_run_ids": missing,
        "unexpected_run_ids": unexpected,
        "duplicate_run_ids": duplicate_ids,
        "record_contract_issues": sorted(contract_issues),
        "kernel_count": len(protocol.case_ids),
        "repeats": protocol.repeats,
        "arms": arm_metrics,
        "kernels": kernel_metrics,
        "legacy_simple_iter_required_runs": legacy_required_runs,
        "legacy_simple_iter_execution_observed_runs": (
            legacy_execution_observed_runs
        ),
        "legacy_simple_iter_baseline_qualification_observed_runs": (
            legacy_baseline_qualification_observed_runs
        ),
        "legacy_simple_iter_process_completed_runs": legacy_process_completed_runs,
        "legacy_simple_iter_reference_isolated_runs": legacy_reference_isolated_runs,
        "legacy_simple_iter_harness_validated_runs": legacy_harness_validated_runs,
        "legacy_simple_iter_comparison_executed": legacy_comparison_executed,
        "stable_superiority_claimed": False,
        "limitations": [
            "bounded committed smoke corpus, not broad external benchmark",
            "two repeats are sufficient for stage acceptance but not publication-grade statistics",
            "model stochasticity and provider service conditions remain external variables",
            "simple_iter uses a reference-isolated typed host harness for feedback and is still authoritatively post-qualified",
            "no statistical significance or stable superiority claim is made",
        ],
        "created_at_utc": _utc_now(),
    }
    report["report_sha256"] = _mapping_sha256(report)
    return report


def _record_metric_values(
    records: Sequence[Mapping[str, Any]],
    *path: str,
) -> list[float]:
    values: list[float] = []
    for item in records:
        value = _metric(item, *path)
        number = _numeric(value)
        if number is not None:
            values.append(number)
    return values


def _record_distribution(
    records: Sequence[Mapping[str, Any]],
    key: str,
) -> dict[str, Any]:
    return _distribution(
        [
            number
            for item in records
            for number in [_numeric(item.get(key))]
            if number is not None
        ]
    )


def _latency_improvement_ratio(record: Mapping[str, Any]) -> float | None:
    baseline = _numeric(_metric(record, "baseline_ppa", "latency_cycles_max"))
    final = _numeric(_metric(record, "ppa", "latency_cycles_max"))
    if baseline is None or final is None or baseline <= 0:
        return None
    return (baseline - final) / baseline


def _replace_symbol(code: str, old: str, new: str) -> str:
    import re

    replaced, count = re.subn(rf"\b{re.escape(old)}\b", new, code)
    if count < 1:
        raise ValueError(f"testbench does not reference expected symbol: {old}")
    return replaced


def _write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")
    return path


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _mapping_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(value), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")
    if any(char in value for char in ("\x00", "\r", "\n")):
        raise ValueError(f"{name} must not contain control newlines")
    return value.strip()


def _required_id(value: Any, name: str) -> str:
    import re

    cleaned = _required_text(value, name)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", cleaned) is None:
        raise ValueError(f"{name} is not a safe identifier")
    return cleaned


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return _required_text(value, "optional text")


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _positive_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a positive number")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return numeric


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _metric(value: Mapping[str, Any], *path: str) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _distribution(values: Sequence[float]) -> dict[str, Any]:
    clean = [float(item) for item in values if math.isfinite(float(item))]
    if not clean:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "stdev": None,
            "min": None,
            "max": None,
        }
    return {
        "count": len(clean),
        "mean": statistics.fmean(clean),
        "median": statistics.median(clean),
        "stdev": 0.0 if len(clean) < 2 else statistics.stdev(clean),
        "min": min(clean),
        "max": max(clean),
    }
