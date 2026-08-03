#!/usr/bin/env python3
"""Run the frozen S3.8 multi-kernel/repeated evaluation matrix."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from typing import Any, Mapping

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from agrefactor.models import resolve_model_runtime

from agrefactor.evaluation.stage3_s38 import (
    DEFAULT_S38_CASE_IDS,
    S38Arm,
    S38Protocol,
    S38EvaluationInfrastructureError,
    S38QualificationObserverError,
    S38_RUN_RECORD_SCHEMA_VERSION,
    aggregate_s38_records,
    build_s38_run_matrix,
    materialize_s38_corpus,
    qualify_external_candidate,
    legacy_record_has_execution_evidence,
)


EXPECTED_BASELINE = "84b6fac0a00469fc9651f5f6553b50febedb21c7"
DEFAULT_MODEL = "deepseek-v4-flash"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Stage 3.8 real evaluation.")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--family")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key-env")
    parser.add_argument("--target", default="vitis-2023.2-default")
    parser.add_argument("--reasoning-effort", choices=("low", "medium", "high"), default="medium")
    parser.add_argument("--case-id", action="append", dest="case_ids")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--output-root")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--retry-arm",
        action="append",
        choices=tuple(item.value for item in S38Arm),
        default=[],
        help=(
            "With --resume, archive and rerun every record for this arm. "
            "Used by evidence-backed correction packages; may be repeated."
        ),
    )
    parser.add_argument(
        "--retry-invalid-legacy",
        action="store_true",
        help=(
            "With --resume, rerun only Legacy records that do not prove actual "
            "model execution under the corrected v3 Legacy harness contract."
        ),
    )
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--max-llm-calls", type=int, default=14)
    parser.add_argument("--max-tool-calls", type=int, default=128)
    parser.add_argument("--max-compile-calls", type=int, default=48)
    parser.add_argument("--max-csim-calls", type=int, default=32)
    parser.add_argument("--max-csynth-calls", type=int, default=16)
    parser.add_argument("--max-wall-time-s", type=float, default=7200.0)
    parser.add_argument("--csim-timeout-s", type=int, default=180)
    parser.add_argument("--csynth-timeout-s", type=int, default=900)
    parser.add_argument("--simple-iter-iterations", type=int, default=14)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo = Path(args.repo).expanduser().resolve()
    require_repo(repo)
    runtime = resolve_model_runtime(
        args.model,
        family=args.family,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        reasoning_effort=args.reasoning_effort,
    )
    effective = runtime.effective_config
    protocol = S38Protocol(
        model=args.model,
        model_family=(
            effective.requested_family_name
            or effective.family_profile.name
        ),
        base_url=effective.base_url,
        api_key_env=effective.api_key_env,
        target=args.target,
        case_ids=tuple(args.case_ids or DEFAULT_S38_CASE_IDS),
        repeats=args.repeats,
        reasoning_effort=args.reasoning_effort,
        provider_reasoning_effort=(
            effective.parameters.get("reasoning_effort")
            if isinstance(effective.parameters.get("reasoning_effort"), str)
            else None
        ),
        max_output_tokens=(
            effective.parameters.get("max_tokens")
            if isinstance(effective.parameters.get("max_tokens"), int)
            else None
        ),
        max_llm_calls=args.max_llm_calls,
        max_tool_calls=args.max_tool_calls,
        max_compile_calls=args.max_compile_calls,
        max_csim_calls=args.max_csim_calls,
        max_csynth_calls=args.max_csynth_calls,
        max_wall_time_s=args.max_wall_time_s,
        csim_timeout_s=args.csim_timeout_s,
        csynth_timeout_s=args.csynth_timeout_s,
        simple_iter_iterations=args.simple_iter_iterations,
    )
    retry_arms = {S38Arm(item) for item in args.retry_arm}
    if (retry_arms or args.retry_invalid_legacy) and not args.resume:
        raise ValueError("retry selection requires --resume")
    if (
        not args.plan_only
        and (not protocol.api_key_env or not os.environ.get(protocol.api_key_env))
    ):
        raise RuntimeError(
            "required model credential environment variable is not set: "
            f"{protocol.api_key_env}"
        )
    root = (
        Path(args.output_root).expanduser().resolve()
        if args.output_root
        else default_output_root()
    )
    if root.exists() and any(root.iterdir()) and not args.resume:
        raise FileExistsError(f"output root is not empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    corpus_root = root / "corpus"
    if not (corpus_root / "corpus_manifest.json").is_file():
        corpus = materialize_s38_corpus(corpus_root, protocol.case_ids)
    else:
        corpus = read_json(corpus_root / "corpus_manifest.json")
    write_json(root / "protocol.json", protocol.to_dict())
    plan = build_s38_run_matrix(protocol)
    write_json(
        root / "plan.json",
        {
            "schema_version": 1,
            "protocol_identity_sha256": protocol.identity_sha256,
            "run_count": len(plan),
            "runs": [item.to_dict() for item in plan],
        },
    )
    if args.plan_only:
        print("S38_PLAN_ONLY=true")
        print(f"PLANNED_RUNS={len(plan)}")
        print(f"KERNEL_COUNT={len(protocol.case_ids)}")
        print(f"REPEATS={protocol.repeats}")
        print(f"ARMS={','.join(item.value for item in protocol.arms)}")
        print(f"PROTOCOL_IDENTITY_SHA256={protocol.identity_sha256}")
        print(f"ARTIFACT_ROOT={root}")
        return 0

    kernel_map = {item["case_id"]: item for item in corpus["kernels"]}
    records: list[dict[str, Any]] = []
    for spec in plan:
        run_root = root / "runs" / spec.run_id
        record_path = run_root / "run_record.json"
        existing_record = read_json(record_path) if record_path.is_file() else None
        force_retry = bool(
            args.resume
            and (
                spec.arm in retry_arms
                or (
                    args.retry_invalid_legacy
                    and spec.arm is S38Arm.SIMPLE_ITER
                    and not legacy_record_has_execution_evidence(existing_record)
                )
            )
        )
        if existing_record is not None:
            if existing_record.get("protocol_identity_sha256") != protocol.identity_sha256:
                raise RuntimeError(f"resume protocol mismatch: {spec.run_id}")
            if (
                args.resume
                and not force_retry
                and existing_record.get("failure_class") != "infrastructure"
            ):
                records.append(existing_record)
                emit_progress(existing_record, resumed=True)
                continue
        resume_action = prepare_run_root(
            evaluation_root=root,
            run_root=run_root,
            resume=args.resume,
            existing_record=existing_record,
            force_retry=force_retry,
        )
        run_root.mkdir(parents=True, exist_ok=False)
        kernel = kernel_map[spec.case_id]
        started = time.monotonic()
        try:
            if spec.arm is S38Arm.SAFE_OPTIMIZE:
                record = run_safe_product(
                    repo=repo,
                    protocol=protocol,
                    spec=spec,
                    kernel=kernel,
                    run_root=run_root,
                    mode="optimize",
                )
            elif spec.arm is S38Arm.SOURCE_FULL:
                record = run_safe_product(
                    repo=repo,
                    protocol=protocol,
                    spec=spec,
                    kernel=kernel,
                    run_root=run_root,
                    mode="full",
                )
            else:
                record = run_simple_iter(
                    repo=repo,
                    protocol=protocol,
                    spec=spec,
                    kernel=kernel,
                    run_root=run_root,
                )
        except Exception as exc:
            record = base_record(protocol, spec)
            record.update(
                {
                    "accepted": False,
                    "status": "error",
                    "failure_class": classify_exception(exc),
                    "error_type": type(exc).__name__,
                    "error_message": safe_text(str(exc)),
                }
            )
        record["wall_time_s"] = time.monotonic() - started
        write_json(record_path, record)
        records.append(record)
        emit_progress(record, resumed=False)
    report = aggregate_s38_records(records, protocol=protocol)
    infra_failures = sum(
        1 for item in records if item.get("failure_class") == "infrastructure"
    )
    accepted_by_arm = {
        arm.value: sum(
            1 for item in records
            if item.get("arm") == arm.value and item.get("accepted") is True
        )
        for arm in protocol.arms
    }
    live_full_accepted = accepted_by_arm[S38Arm.SOURCE_FULL.value] > 0
    direct_optimize_accepted = accepted_by_arm[S38Arm.SAFE_OPTIMIZE.value] > 0
    vitis_kernel_ids = sorted(
        {
            str(item["case_id"])
            for item in records
            if int(item.get("csynth_calls") or 0) > 0
        }
    )
    multi_kernel_vitis = set(vitis_kernel_ids) == set(protocol.case_ids)
    legacy_comparison_executed = bool(
        report.get("legacy_simple_iter_comparison_executed") is True
    )
    stage_accepted = bool(
        report["evaluation_valid"]
        and infra_failures == 0
        and direct_optimize_accepted
        and live_full_accepted
        and multi_kernel_vitis
        and legacy_comparison_executed
    )
    report.update(
        {
            "infrastructure_failure_count": infra_failures,
            "accepted_runs_by_arm": accepted_by_arm,
            "direct_optimize_accepted": direct_optimize_accepted,
            "live_source_full_accepted": live_full_accepted,
            "real_vitis_kernel_ids": vitis_kernel_ids,
            "multi_kernel_real_vitis_observed": multi_kernel_vitis,
            "legacy_simple_iter_comparison_executed": (
                legacy_comparison_executed
            ),
            "stage3_s38_accepted": stage_accepted,
            "next_package": "STAGE4_MEMORY_APPLICABILITY_GATE" if stage_accepted else "S3.8_REMEDIATION_OR_EXTENDED_EVALUATION",
        }
    )
    report["report_sha256"] = mapping_sha256(
        {key: value for key, value in report.items() if key != "report_sha256"}
    )
    write_json(root / "evaluation_report.json", report)
    write_json(root / "run_records.json", {"schema_version": 1, "records": records})
    write_csv(root / "run_records.csv", records)
    emit_final(report, root)
    return 0 if stage_accepted else 3



def prepare_run_root(
    *,
    evaluation_root: Path,
    run_root: Path,
    resume: bool,
    existing_record: Mapping[str, Any] | None,
    force_retry: bool = False,
) -> str:
    """Prepare one run directory without silently skipping retryable failures."""

    if not run_root.exists():
        return "new"
    if not resume:
        raise FileExistsError(f"run root already exists: {run_root}")
    if (
        not force_retry
        and existing_record is not None
        and existing_record.get("failure_class") != "infrastructure"
    ):
        return "reuse_record"

    archive_root = evaluation_root / "failed_attempts"
    archive_root.mkdir(parents=True, exist_ok=True)
    suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = archive_root / f"{run_root.name}.{suffix}.{os.getpid()}"
    run_root.rename(destination)
    if force_retry:
        return "retry_forced"
    return "retry_infrastructure" if existing_record is not None else "retry_interrupted"

def run_safe_product(
    *,
    repo: Path,
    protocol: S38Protocol,
    spec,
    kernel: Mapping[str, Any],
    run_root: Path,
    mode: str,
) -> dict[str, Any]:
    artifacts = run_root / "artifacts"
    if mode == "optimize":
        material = kernel["direct"]
        command = [
            sys.executable,
            "-m",
            "agrefactor.cli",
            "optimize",
            material["baseline"],
            "--top",
            material["top_function"],
            "--reference-source",
            material["reference"],
            "--reference-top",
            material["reference_top_function"],
            "--public-test",
            material["public"],
            "--hidden-test",
            material["hidden"],
        ]
    else:
        material = kernel["full"]
        command = [
            sys.executable,
            "-m",
            "agrefactor.cli",
            "full",
            material["source"],
            "--top",
            material["top_function"],
            "--public-test",
            material["public"],
            "--hidden-test",
            material["hidden"],
        ]
    command.extend(common_product_args(protocol, artifacts, spec.run_id))
    completed = run_command(
        command,
        cwd=repo,
        log_root=run_root / "process",
        timeout=protocol.max_wall_time_s + 120,
    )
    record = base_record(protocol, spec)
    record.update(parse_product_artifacts(artifacts))
    record["return_code"] = completed.returncode
    if completed.returncode != 0 and record.get("status") != "accepted":
        record["accepted"] = False
        observed = classify_process_failure(completed)
        if observed == "infrastructure":
            record["failure_class"] = "infrastructure"
        elif not record.get("failure_class"):
            record["failure_class"] = "candidate"
    return record


def run_simple_iter(
    *,
    repo: Path,
    protocol: S38Protocol,
    spec,
    kernel: Mapping[str, Any],
    run_root: Path,
) -> dict[str, Any]:
    material = kernel["direct"]
    deadline = time.monotonic() + protocol.max_wall_time_s

    def remaining_wall() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("run_wall_budget_exhausted")
        return remaining

    baseline_qualification = qualify_external_candidate(
        output_root=run_root / "baseline_qualification",
        run_id=f"{spec.run_id}.baseline",
        candidate_path=material["baseline"],
        top_function=material["top_function"],
        reference_path=material["reference"],
        reference_top_function=material["reference_top_function"],
        public_test_path=material["public"],
        hidden_test_path=material["hidden"],
        target_name=protocol.target,
        csim_timeout_s=protocol.csim_timeout_s,
        csynth_timeout_s=protocol.csynth_timeout_s,
        max_wall_time_s=remaining_wall(),
    )
    record = base_record(protocol, spec)
    record.update(
        {
            "independent_baseline_qualification_observed": True,
            "baseline_qualification_stage_order": list(
                baseline_qualification.get("stage_order") or []
            ),
            "legacy_execution_started": False,
            "legacy_process_completed": False,
            "legacy_evaluation_artifact_observed": False,
            "legacy_reference_supplied": False,
            "legacy_reference_isolated": False,
            "legacy_harness_contract_version": None,
            "legacy_harness_evidence_observed": False,
            "independent_final_qualification_observed": False,
        }
    )
    if baseline_qualification.get("accepted") is not True:
        record.update(
            {
                "accepted": False,
                "status": "baseline_rejected",
                "failure_class": "candidate",
                "llm_calls": 0,
                "tool_calls": _usage(baseline_qualification, "tool_calls"),
                "compile_calls": _usage(baseline_qualification, "compile_calls"),
                "csim_calls": _usage(baseline_qualification, "csim_calls"),
                "csynth_calls": _usage(baseline_qualification, "csynth_calls"),
                "baseline_ppa": normalize_ppa(baseline_qualification.get("ppa")),
                "best_correct_protected": True,
                "hidden_exposed_to_model": False,
            }
        )
        return record

    legacy_root = run_root / "legacy"
    command = [
        sys.executable,
        "-m",
        "opt.simple_iter.main",
        "--kernel_path",
        material["baseline"],
        "--reference_path",
        material["reference"],
        "--reference_top_name",
        material["reference_top_function"],
        "--top_name",
        material["top_function"],
        "--model",
        protocol.model,
        "--iterations",
        str(protocol.simple_iter_iterations),
        "--output_dir",
        str(legacy_root),
        "--testbench_path",
        material["public"],
        "--no-gen_bench_prior",
        "--target",
        protocol.target,
        "--reasoning_effort",
        protocol.reasoning_effort,
        "--max_model_attempts",
        "1",
        "--csynth_timeout_s",
        str(protocol.csynth_timeout_s),
        "--evaluation_mode",
    ]
    if protocol.provider_reasoning_effort:
        command.extend(("--provider_reasoning_effort", protocol.provider_reasoning_effort))
    if protocol.max_output_tokens is not None:
        command.extend(("--max_output_tokens", str(protocol.max_output_tokens)))
    if protocol.base_url:
        command.extend(("--base_url", protocol.base_url))
    if protocol.api_key_env:
        command.extend(("--api_key_env", protocol.api_key_env))
    record["legacy_execution_started"] = True
    completed = run_command(
        command,
        cwd=repo,
        log_root=run_root / "process",
        timeout=remaining_wall(),
    )
    evaluation_path = legacy_root / "simple_iter_evaluation.json"
    evaluation = read_json(evaluation_path) if evaluation_path.is_file() else {}
    record["legacy_evaluation_artifact_observed"] = evaluation_path.is_file()
    record["legacy_process_completed"] = completed.returncode == 0
    record["legacy_reference_supplied"] = evaluation.get("reference_path_provided") is True
    record["legacy_reference_isolated"] = evaluation.get("reference_isolated") is True
    record["legacy_harness_contract_version"] = evaluation.get("harness_contract_version")
    record["legacy_harness_evidence_observed"] = bool(
        evaluation.get("schema_version") == 3
        and evaluation.get("harness_contract_activated") is True
        and evaluation.get("reference_isolated") is True
        and evaluation.get("harness_contract_version") == 1
    )
    record["legacy_model_output_abstentions"] = int(
        evaluation.get("model_output_abstentions") or 0
    )
    record["legacy_model_output_reason_counts"] = dict(
        evaluation.get("model_output_reason_counts") or {}
    )
    record["legacy_synthesis_successes"] = int(
        evaluation.get("synthesis_successes") or 0
    )
    record["legacy_harness_attempts"] = int(
        evaluation.get("harness_attempts") or 0
    )
    record["legacy_harness_passes"] = int(
        evaluation.get("harness_passes") or 0
    )
    record["legacy_harness_failure_counts"] = dict(
        evaluation.get("harness_failure_counts") or {}
    )
    best_path = legacy_root / "best_candidate.cpp"

    def combined_counts(final: Mapping[str, Any] | None = None) -> dict[str, int]:
        final_value = {} if final is None else final
        return {
            "llm_calls": int(evaluation.get("model_calls") or 0),
            "tool_calls": (
                _usage(baseline_qualification, "tool_calls")
                + _usage(final_value, "tool_calls")
                + int(evaluation.get("tool_calls") or 0)
            ),
            "compile_calls": (
                _usage(baseline_qualification, "compile_calls")
                + _usage(final_value, "compile_calls")
                + int(evaluation.get("compile_calls") or 0)
            ),
            "csim_calls": (
                _usage(baseline_qualification, "csim_calls")
                + _usage(final_value, "csim_calls")
                + int(evaluation.get("csim_calls") or 0)
            ),
            "csynth_calls": (
                _usage(baseline_qualification, "csynth_calls")
                + _usage(final_value, "csynth_calls")
                + int(evaluation.get("csynth_calls") or 0)
            ),
        }

    if completed.returncode != 0:
        counts = combined_counts()
        record.update(
            {
                "accepted": False,
                "status": "legacy_process_failed",
                "failure_class": classify_process_failure(completed),
                "return_code": completed.returncode,
                **counts,
                "automatic_model_retry": evaluation.get("automatic_model_retry"),
                "raw_prompt_response_persisted": evaluation.get(
                    "raw_prompt_response_persisted"
                ),
                "hidden_exposed_to_model": False,
                "baseline_ppa": normalize_ppa(baseline_qualification.get("ppa")),
                "best_correct_protected": True,
            }
        )
        return record
    if not best_path.is_file():
        counts = combined_counts()
        model_calls = int(evaluation.get("model_calls") or 0)
        abstentions = int(evaluation.get("model_output_abstentions") or 0)
        harness_attempts = int(evaluation.get("harness_attempts") or 0)
        harness_passes = int(evaluation.get("harness_passes") or 0)
        synthesis_successes = int(evaluation.get("synthesis_successes") or 0)
        if model_calls > 0 and abstentions == model_calls:
            status = "no_parseable_legacy_candidate"
            terminal_status = "all_model_outputs_abstained"
        elif harness_attempts > 0 and harness_passes == 0:
            status = "no_testbench_passing_candidate"
            terminal_status = "all_harness_candidates_rejected"
        elif harness_passes > 0:
            status = "no_resource_feasible_candidate"
            terminal_status = "no_harness_passing_candidate_met_objective"
        elif synthesis_successes == 0:
            status = "no_synthesizable_legacy_candidate"
            terminal_status = "all_candidates_failed_synthesis"
        else:
            status = "no_legacy_candidate"
            terminal_status = "no_best_candidate_selected"
        record.update(
            {
                "accepted": False,
                "status": status,
                "terminal_status": terminal_status,
                "failure_class": "candidate",
                "return_code": completed.returncode,
                "qualified_candidate_count": 0,
                **counts,
                "automatic_model_retry": evaluation.get("automatic_model_retry"),
                "raw_prompt_response_persisted": evaluation.get(
                    "raw_prompt_response_persisted"
                ),
                "hidden_exposed_to_model": False,
                "baseline_ppa": normalize_ppa(baseline_qualification.get("ppa")),
                "best_correct_protected": True,
            }
        )
        return record

    final_qualification = qualify_external_candidate(
        output_root=run_root / "final_qualification",
        run_id=f"{spec.run_id}.final",
        candidate_path=best_path,
        top_function=material["top_function"],
        reference_path=material["reference"],
        reference_top_function=material["reference_top_function"],
        public_test_path=material["public"],
        hidden_test_path=material["hidden"],
        target_name=protocol.target,
        csim_timeout_s=protocol.csim_timeout_s,
        csynth_timeout_s=protocol.csynth_timeout_s,
        max_wall_time_s=remaining_wall(),
    )
    record["independent_final_qualification_observed"] = True
    ppa = normalize_ppa(final_qualification.get("ppa"))
    baseline_ppa = normalize_ppa(baseline_qualification.get("ppa"))
    counts = combined_counts(final_qualification)
    qualification_status = str(
        final_qualification.get("qualification_status") or "error"
    )
    accepted = final_qualification.get("accepted") is True
    if accepted:
        record_status = "accepted"
        failure_class = None
    elif qualification_status == "rejected":
        record_status = "final_qualification_rejected"
        failure_class = "candidate"
    elif qualification_status == "review_required":
        record_status = "final_qualification_review_required"
        failure_class = "review"
    elif qualification_status == "blocked":
        record_status = "final_qualification_blocked"
        failure_class = "infrastructure"
    else:
        record_status = "final_qualification_error"
        failure_class = "infrastructure"
    record.update(
        {
            "accepted": accepted,
            "status": record_status,
            "failure_class": failure_class,
            "return_code": completed.returncode,
            "terminal_status": (
                "legacy_candidate_qualified"
                if accepted
                else record_status
            ),
            "final_qualification_status": qualification_status,
            "final_qualification_terminal_stage": final_qualification.get(
                "terminal_stage"
            ),
            "final_qualification_terminal_outcome": final_qualification.get(
                "terminal_outcome"
            ),
            "final_qualification_failure_kind": final_qualification.get(
                "failure_kind"
            ),
            "final_qualification_failure_owner": final_qualification.get(
                "failure_owner"
            ),
            "final_qualification_reason_codes": list(
                final_qualification.get("reason_codes") or []
            ),
            "ppa": ppa,
            "baseline_ppa": baseline_ppa,
            **counts,
            "automatic_model_retry": evaluation.get("automatic_model_retry"),
            "raw_prompt_response_persisted": evaluation.get(
                "raw_prompt_response_persisted"
            ),
            "qualified_candidate_count": 1,
            "rejected_candidate_count": 0 if accepted else 1,
            "invalid_candidate_ratio": 0.0 if accepted else 1.0,
            "rollback_count": 0,
            "best_correct_protected": True,
            "hidden_exposed_to_model": False,
            "artifact_root": str(run_root),
        }
    )
    if exceeds_budget(record, protocol):
        record.update(
            {
                "accepted": False,
                "status": "budget_contract_violated",
                "failure_class": "infrastructure",
            }
        )
    return record


def common_product_args(protocol: S38Protocol, artifacts: Path, run_id: str) -> list[str]:
    args = [
        "--model", protocol.model,
        "--target", protocol.target,
        "--reasoning-effort", protocol.reasoning_effort,
        "--max-llm-calls", str(protocol.max_llm_calls),
        "--max-tool-calls", str(protocol.max_tool_calls),
        "--max-compile-calls", str(protocol.max_compile_calls),
        "--max-csim-calls", str(protocol.max_csim_calls),
        "--max-csynth-calls", str(protocol.max_csynth_calls),
        "--max-wall-time-s", str(protocol.max_wall_time_s),
        "--csim-timeout-s", str(protocol.csim_timeout_s),
        "--csynth-timeout-s", str(protocol.csynth_timeout_s),
        "--output-dir", str(artifacts),
        "--run-id", run_id,
        "--json",
    ]
    if protocol.model_family:
        args.extend(("--model-family", protocol.model_family))
    if protocol.base_url:
        args.extend(("--base-url", protocol.base_url))
    if protocol.api_key_env:
        args.extend(("--api-key-env", protocol.api_key_env))
    return args


def parse_product_artifacts(root: Path) -> dict[str, Any]:
    result = read_json(root / "full_result.json") if (root / "full_result.json").is_file() else {}
    identity = read_json(root / "stage3_execution_identity.json") if (root / "stage3_execution_identity.json").is_file() else {}
    phases = result.get("phases", []) if isinstance(result.get("phases"), list) else []
    accepted = result.get("status") == "succeeded" and any(
        isinstance(item, Mapping)
        and item.get("phase") == "optimize"
        and isinstance(item.get("metadata"), Mapping)
        and item["metadata"].get("accepted") is True
        for item in phases
    )
    state = identity.get("state", {}) if isinstance(identity.get("state"), Mapping) else {}
    candidates = identity.get("candidate_index", {}) if isinstance(identity.get("candidate_index"), Mapping) else {}
    best_id = state.get("best_ppa_candidate_id") or state.get("best_correct_candidate_id")
    best = candidates.get(best_id, {}) if isinstance(best_id, str) else {}
    decisions_path = root / "optimize" / "optimizer" / "decisions.jsonl"
    decisions = read_jsonl(decisions_path)
    rollback_count = sum(
        1 for item in decisions
        if "rollback" in str(item.get("action", "")) or "keep_best" in str(item.get("action", ""))
    )
    rejected = sum(
        1 for item in candidates.values()
        if isinstance(item, Mapping) and item.get("status") in {"rejected", "blocked", "error"}
    )
    executed = int(state.get("executed_candidate_count") or 0)
    usage = identity.get("budget_usage", {}) if isinstance(identity.get("budget_usage"), Mapping) else {}
    return {
        "accepted": bool(accepted),
        "status": "accepted" if accepted else str(result.get("status") or "failed"),
        "failure_class": None if accepted else "candidate",
        "terminal_status": identity.get("terminal_status") or state.get("terminal_status"),
        "ppa": normalize_ppa(best.get("ppa") if isinstance(best, Mapping) else None),
        "baseline_ppa": normalize_ppa(
            candidates.get("baseline", {}).get("ppa")
            if isinstance(candidates.get("baseline"), Mapping)
            else None
        ),
        "llm_calls": int(usage.get("llm_calls") or 0),
        "tool_calls": int(usage.get("tool_calls") or 0),
        "compile_calls": int(usage.get("compile_calls") or 0),
        "csim_calls": int(usage.get("csim_calls") or 0),
        "csynth_calls": int(usage.get("csynth_calls") or 0),
        "qualified_candidate_count": executed,
        "rejected_candidate_count": rejected,
        "invalid_candidate_ratio": 0.0 if executed == 0 else rejected / executed,
        "rollback_count": rollback_count,
        "best_correct_protected": isinstance(state.get("best_correct_candidate_id"), str),
        "hidden_exposed_to_model": False,
        "artifact_root": str(root),
    }


def normalize_ppa(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    metrics = value.get("metrics") if isinstance(value.get("metrics"), Mapping) else value
    resources = value.get("resources_used") if isinstance(value.get("resources_used"), Mapping) else {}
    return {
        "latency_cycles_min": metrics.get("latency_cycles_min"),
        "latency_cycles_max": metrics.get("latency_cycles_max"),
        "initiation_interval_min": metrics.get("initiation_interval_min"),
        "initiation_interval_max": metrics.get("initiation_interval_max"),
        "achieved_clock_period_ns": metrics.get("achieved_clock_period_ns"),
        "max_resource_utilization_ratio": metrics.get("max_resource_utilization_ratio"),
        "resources_used": dict(resources),
        "objective_feasible": metrics.get("objective_feasible", value.get("objective_feasible")),
    }


def base_record(protocol: S38Protocol, spec) -> dict[str, Any]:
    return {
        "schema_version": S38_RUN_RECORD_SCHEMA_VERSION,
        "protocol_identity_sha256": protocol.identity_sha256,
        "run_id": spec.run_id,
        "sequence": spec.sequence,
        "case_id": spec.case_id,
        "repeat_index": spec.repeat_index,
        "arm": spec.arm.value,
        "model": protocol.model,
        "target": protocol.target,
        "budget_ceiling": protocol.to_dict()["budgets"],
        "accepted": False,
        "status": "running",
        "failure_class": None,
        "automatic_model_retry": False,
        "raw_prompt_response_persisted": False,
        "hidden_exposed_to_model": False,
        "created_at_utc": utc_now(),
    }


def run_command(command: list[str], *, cwd: Path, log_root: Path, timeout: float) -> subprocess.CompletedProcess[str]:
    log_root.mkdir(parents=True, exist_ok=True)
    write_json(log_root / "command.json", {"argv": redact_argv(command), "cwd": str(cwd), "timeout_s": timeout})
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        (log_root / "stdout.log").write_text(_decode_timeout(exc.stdout), encoding="utf-8")
        (log_root / "stderr.log").write_text(_decode_timeout(exc.stderr), encoding="utf-8")
        raise RuntimeError("subprocess_timeout") from exc
    (log_root / "stdout.log").write_text(completed.stdout, encoding="utf-8")
    (log_root / "stderr.log").write_text(completed.stderr, encoding="utf-8")
    return completed


def require_repo(repo: Path) -> None:
    if not (repo / ".git").is_dir():
        raise FileNotFoundError(f"not a Git worktree: {repo}")
    head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    if head != EXPECTED_BASELINE:
        raise RuntimeError(f"baseline mismatch: expected={EXPECTED_BASELINE} actual={head}")


def classify_exception(exc: Exception) -> str:
    if isinstance(exc, S38EvaluationInfrastructureError):
        return "infrastructure"
    if isinstance(exc, (FileNotFoundError, PermissionError, TimeoutError, subprocess.SubprocessError)):
        return "infrastructure"
    text = f"{type(exc).__name__}: {exc}".lower()
    infrastructure_tokens = (
        "credential",
        "api key",
        "connection",
        "transport",
        "subprocess_timeout",
        "vitis toolchain observation",
        "vitis executable not found",
        "toolchain mismatch",
        "baseline mismatch",
        "permission denied",
        "module not found",
    )
    return (
        "infrastructure"
        if any(token in text for token in infrastructure_tokens)
        else "candidate"
    )


def classify_process_failure(completed: subprocess.CompletedProcess[str]) -> str:
    text = f"{completed.stdout}\n{completed.stderr}".lower()
    infrastructure_tokens = (
        "missing api credential",
        "credential environment variable",
        "authentication",
        "connectionerror",
        "connect timeout",
        "read timeout",
        "subprocess_timeout",
        "modulenotfounderror",
        "permission denied",
        "toolchain observation failed",
        "requested/actual toolchain mismatch",
        "vitis-run: command not found",
        "no such file or directory: 'vitis-run'",
    )
    return (
        "infrastructure"
        if any(token in text for token in infrastructure_tokens)
        else "candidate"
    )


def exceeds_budget(record: Mapping[str, Any], protocol: S38Protocol) -> bool:
    ceilings = {
        "llm_calls": protocol.max_llm_calls,
        "tool_calls": protocol.max_tool_calls,
        "compile_calls": protocol.max_compile_calls,
        "csim_calls": protocol.max_csim_calls,
        "csynth_calls": protocol.max_csynth_calls,
    }
    return any(int(record.get(key) or 0) > ceiling for key, ceiling in ceilings.items())


def _usage(summary: Mapping[str, Any], key: str) -> int:
    usage = summary.get("budget_usage")
    return int(usage.get(key) or 0) if isinstance(usage, Mapping) else 0


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"JSON artifact must contain an object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    values = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if raw.strip():
            item = json.loads(raw)
            if isinstance(item, dict):
                values.append(item)
    return values


def mapping_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    import hashlib

    return hashlib.sha256(encoded).hexdigest()


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = (
        "run_id", "case_id", "repeat_index", "arm", "accepted", "status",
        "failure_class", "terminal_status", "llm_calls", "tool_calls",
        "compile_calls", "csim_calls", "csynth_calls", "wall_time_s",
        "qualified_candidate_count", "rejected_candidate_count",
        "invalid_candidate_ratio", "rollback_count",
    )
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def redact_argv(argv: list[str]) -> list[str]:
    redacted = []
    hide_next = False
    for item in argv:
        if hide_next:
            redacted.append("<REDACTED>")
            hide_next = False
            continue
        redacted.append(item)
        if item in {"--api-key", "--token", "--password"}:
            hide_next = True
    return redacted


def safe_text(value: str) -> str:
    text = value.replace("\x00", "")
    for marker in ("sk-", "Bearer "):
        if marker in text:
            text = text.split(marker, 1)[0] + marker + "<REDACTED>"
    return text[-4000:]


def _decode_timeout(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def default_output_root() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(f"/data/agrefactor_runs/stage3_s38_evaluation_{stamp}_{os.getpid()}")


def emit_progress(record: Mapping[str, Any], *, resumed: bool) -> None:
    print(
        "S38_RUN "
        f"run_id={record.get('run_id')} "
        f"arm={record.get('arm')} "
        f"accepted={str(record.get('accepted')).lower()} "
        f"status={record.get('status')} "
        f"resumed={str(resumed).lower()}"
    )


def emit_final(report: Mapping[str, Any], root: Path) -> None:
    print("S38_EVALUATION_COMPLETED=true")
    print(f"S38_STAGE_ACCEPTED={str(report.get('stage3_s38_accepted')).lower()}")
    print(f"PLANNED_RUNS={report.get('planned_run_count')}")
    print(f"OBSERVED_RUNS={report.get('observed_run_count')}")
    print(f"KERNEL_COUNT={report.get('kernel_count')}")
    print(f"REPEATS={report.get('repeats')}")
    print(f"INFRASTRUCTURE_FAILURES={report.get('infrastructure_failure_count')}")
    print(f"DIRECT_OPTIMIZE_ACCEPTED={str(report.get('direct_optimize_accepted')).lower()}")
    print(f"LIVE_SOURCE_FULL_ACCEPTED={str(report.get('live_source_full_accepted')).lower()}")
    print(f"MULTI_KERNEL_REAL_VITIS={str(report.get('multi_kernel_real_vitis_observed')).lower()}")
    print(
        "LEGACY_SIMPLE_ITER_COMPARISON_EXECUTED="
        f"{str(report.get('legacy_simple_iter_comparison_executed')).lower()}"
    )
    print("STABLE_SUPERIORITY_CLAIMED=false")
    print(f"ARTIFACT_ROOT={root}")
    print(f"NEXT_PACKAGE={report.get('next_package')}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("S38_EVALUATION_COMPLETED=false", file=sys.stderr)
        print(f"{type(exc).__name__}: {safe_text(str(exc))}", file=sys.stderr)
        raise
