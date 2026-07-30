#!/usr/bin/env python3
"""Run one explicit real Stage 3.2 baseline qualification replay.

This is an acceptance tool, not the product optimize/full CLI. It never invokes
an LLM and writes into a new evidence directory without mutating prior runs.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

from agrefactor.config import (
    EvaluationSplit,
    RunMode,
    TaskSpec,
    TestSuiteSpec,
    resolve_target_profile,
)
from agrefactor.optimization import (
    CandidateQualificationRequest,
    CandidateRecord,
    CandidateStatus,
    OptimizerCheckpointWriter,
    OptimizerState,
    QualificationEvidenceCache,
    QualificationStage,
    Stage3QualificationOrchestrator,
    ValidationCacheIdentity,
    build_toolchain_fingerprint,
    initialize_qualified_baseline,
    suite_identity_from_file,
)
from agrefactor.runtime import (
    BudgetLimits,
    BudgetManager,
    CsimStageInputs,
    CsimValidationStageHandler,
    CsynthStageInputs,
    CsynthValidationStageHandler,
    PreflightStageInputs,
    PreflightValidationStageHandler,
    RunContext,
    TraceRecorder,
    file_sha256,
    validate_execution_identity_bundle,
)
from flow.tools.csynth import (
    probe_csynth_version,
    resolve_csynth_command,
)
from agrefactor.smoke import STAGE2_SMOKE_CASES


EXPECTED_BASELINE = "9e55601f873e46e6edf83b5092970e47fbe132c0"


def main() -> int:
    args = parse_args()
    repo = Path(args.repo).expanduser().resolve()
    require_repo(repo)
    output_root = (
        Path(args.output_root).expanduser().resolve()
        if args.output_root
        else default_output_root()
    )
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(f"output root is not empty: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)

    if args.smoke_case_id:
        smoke = load_smoke_case_material(
            repo,
            output_root,
            args.smoke_case_id,
        )
        source_artifact_root = smoke["source_artifact_root"]
        source_run_execution_id = None
        source_baseline_kind = "committed_stage2_smoke_case"
        source_smoke_case_id = smoke["case_id"]
        candidate_path = smoke["candidate_path"]
        original_path = smoke["original_path"]
        preflight_testbench_path = smoke["preflight_testbench_path"]
        candidate_code = smoke["candidate_code"]
        original_code = smoke["original_code"]
        preflight_testbench_code = smoke["preflight_testbench_code"]
        top_function = args.top or smoke["top_function"]
        target_profile = smoke["target_profile"]
        suites = smoke["suites"]
        suite_codes = smoke["suite_codes"]
        suite_identities = smoke["suite_identities"]
        hidden_source_paths = smoke["hidden_source_paths"]
        hidden_forbidden_tokens = smoke["hidden_forbidden_tokens"]
        bundle: dict[str, Any] = {}
    else:
        source_artifact_root = (
            Path(args.source_run_artifact_root).expanduser().resolve()
            if args.source_run_artifact_root
            else discover_source_artifact_root(repo)
        )
        if not source_artifact_root.is_dir():
            raise FileNotFoundError(
                f"source run artifact root not found: {source_artifact_root}"
            )
        identity_path = source_artifact_root / "execution_identity.json"
        bundle = read_json(identity_path)
        validate_execution_identity_bundle(bundle, require_accepted_ready=True)
        source_material = discover_source_material(source_artifact_root, bundle)
        candidate_path = source_material["candidate_path"]
        original_path = source_material["original_path"]
        preflight_testbench_path = source_material["preflight_testbench_path"]
        candidate_code = candidate_path.read_text(encoding="utf-8")
        original_code = original_path.read_text(encoding="utf-8")
        preflight_testbench_code = preflight_testbench_path.read_text(
            encoding="utf-8"
        )

        csynth_material = discover_csynth_material(
            source_artifact_root,
            candidate_path,
            bundle,
        )
        top_function = args.top or csynth_material["top_function"]
        target_profile = resolve_target_profile(csynth_material["target_profile"])
        suites, suite_codes, suite_identities = load_suites(bundle)
        hidden_source_paths = tuple(
            Path(item["testbench_path"]).expanduser().resolve()
            for item in bundle.get("suites", ())
            if item.get("split") == "hidden"
            and isinstance(item.get("testbench_path"), str)
        )
        hidden_forbidden_tokens = ()
        source_run_execution_id = bundle.get("execution_id")
        source_baseline_kind = "accepted_product_run"
        source_smoke_case_id = None

    if target_profile.device is None:
        raise ValueError("real replay TargetProfile has no device")

    public_suites = tuple(
        suite for suite in suites if suite.split is EvaluationSplit.PUBLIC
    )
    hidden_suites = tuple(
        suite for suite in suites if suite.split is EvaluationSplit.HIDDEN
    )
    if not public_suites:
        raise ValueError("real S3.2 replay requires at least one Public suite")
    if not hidden_suites:
        raise ValueError("real S3.2 replay requires at least one Hidden suite")

    toolchain_manifest = observe_toolchain(target_profile)
    toolchain_fingerprint = build_toolchain_fingerprint(toolchain_manifest)
    effective_target = target_profile.to_effective_dict()
    cache_identity = ValidationCacheIdentity.build(
        source_sha256=file_sha256(candidate_path),
        effective_target=effective_target,
        toolchain_fingerprint_sha256=toolchain_fingerprint,
        suites=suite_identities,
        compile_flags=target_profile.compile_flags,
        clock_period_ns=target_profile.clock_period_ns,
        device=target_profile.device,
        parser_profile=target_profile.parser_profile,
    )

    replay_run_id = output_root.name
    task = TaskSpec(
        task_id=f"{replay_run_id}.task",
        kernel_path=str(candidate_path),
        kernel_name=top_function,
        target=target_profile,
        mode=RunMode.OPTIMIZE,
        testbench_path=str(preflight_testbench_path),
        test_suites=suites,
    )
    suite_count = len(suites)
    budget = BudgetManager(
        BudgetLimits(
            max_llm_calls=0,
            # Preflight and CSYNTH consume one tool call each. Each CSIM
            # suite consumes two aggregate tool calls: compile and execute.
            max_tool_calls=2 + (2 * suite_count),
            max_compile_calls=1 + suite_count,
            max_csim_calls=suite_count,
            max_csynth_calls=1,
            max_wall_time_s=float(args.max_wall_time_s),
        )
    )
    trace = TraceRecorder(
        replay_run_id,
        task_id=task.task_id,
        output_path=output_root / "trace.jsonl",
    )
    context = RunContext(
        run_id=replay_run_id,
        task=task,
        budget=budget,
        trace=trace,
    )

    work = output_root / "qualification"
    public_codes = {
        suite.suite_id: suite_codes[suite.suite_id]
        for suite in public_suites
    }
    hidden_codes = {
        suite.suite_id: suite_codes[suite.suite_id]
        for suite in hidden_suites
    }
    handlers = {
        QualificationStage.PREFLIGHT: PreflightValidationStageHandler(
            PreflightStageInputs(
                work_dir=work / "preflight",
                testbench_code=preflight_testbench_code,
                original_code=original_code,
                candidate_code=candidate_code,
            )
        ),
        QualificationStage.PUBLIC: CsimValidationStageHandler(
            CsimStageInputs(
                work_dir=work / "public",
                original_code=original_code,
                candidate_code=candidate_code,
                suite_testbench_codes=public_codes,
                timelimit=int(args.csim_timelimit_s),
            ),
            split=EvaluationSplit.PUBLIC,
        ),
        QualificationStage.CSYNTH: CsynthValidationStageHandler(
            CsynthStageInputs(
                work_dir=work / "csynth",
                candidate_code=candidate_code,
                timelimit=int(args.csynth_timelimit_s),
            )
        ),
        QualificationStage.HIDDEN: CsimValidationStageHandler(
            CsimStageInputs(
                work_dir=work / "hidden",
                original_code=original_code,
                candidate_code=candidate_code,
                suite_testbench_codes=hidden_codes,
                timelimit=int(args.csim_timelimit_s),
            ),
            split=EvaluationSplit.HIDDEN,
        ),
    }

    baseline = CandidateRecord(
        candidate_id="baseline",
        sequence=0,
        parent_candidate_id=None,
        hypothesis_id=None,
        level=None,
        source_sha256=file_sha256(candidate_path),
        source_artifact="candidates/baseline/source.cpp",
        status=CandidateStatus.GENERATED,
        budget_before=budget.snapshot().to_dict(),
        created_at_utc=utc_now(),
    )
    optimizer_root = output_root / "optimizer"
    checkpoint_writer = OptimizerCheckpointWriter(optimizer_root)
    checkpoint_writer.write_candidate_source(baseline, candidate_path.read_bytes())
    cache = QualificationEvidenceCache(output_root / "validation_cache")
    request = CandidateQualificationRequest(
        qualification_id=f"{replay_run_id}.baseline",
        candidate=baseline,
        source_path=candidate_path,
        ppa_work_dir=work / "csynth",
        top_function=top_function,
        cache_identity=cache_identity,
        resource_limits=target_profile.resource_limits.to_dict(),
    )
    orchestrator = Stage3QualificationOrchestrator(
        handlers,
        cache=cache,
    )
    result = orchestrator.run(context, request)
    terminal_baseline = result.apply_to_candidate(baseline)
    state = initialize_qualified_baseline(
        OptimizerState.initial(run_id=replay_run_id),
        terminal_baseline,
        result,
    )

    # Persist the primary qualification evidence before checkpoint/cache work so
    # a later integration failure can never hide the real qualification result.
    trace.write_json(output_root / "trace.json")
    write_json(output_root / "qualification_result.json", result.to_dict())
    write_json(output_root / "cache_identity.json", cache_identity.to_dict())
    write_json(output_root / "toolchain_manifest.json", toolchain_manifest)
    if result.ppa is not None:
        write_json(output_root / "ppa.json", result.ppa.to_dict())

    checkpoint = checkpoint_writer.write_checkpoint(
        state,
        {"baseline": terminal_baseline},
    )

    cache_result = None
    cache_zero_launch = False
    if result.cacheable:
        counters_before_cache = hard_counters(budget.snapshot().to_dict())
        cache_request = CandidateQualificationRequest(
            qualification_id=f"{replay_run_id}.baseline.cache-replay",
            candidate=baseline,
            source_path=candidate_path,
            ppa_work_dir=work / "csynth",
            top_function=top_function,
            cache_identity=cache_identity,
            resource_limits=target_profile.resource_limits.to_dict(),
        )
        cache_result = orchestrator.run(context, cache_request)
        counters_after_cache = hard_counters(budget.snapshot().to_dict())
        cache_zero_launch = counters_before_cache == counters_after_cache
        write_json(
            output_root / "cache_hit_replay_result.json",
            cache_result.to_dict(),
        )

    trace.write_json(output_root / "trace.json")

    safe_file_list = [
        output_root / "qualification_result.json",
        output_root / "cache_identity.json",
        output_root / "trace.json",
        output_root / "trace.jsonl",
    ]
    if cache_result is not None:
        safe_file_list.append(output_root / "cache_hit_replay_result.json")
    if result.ppa is not None:
        safe_file_list.append(output_root / "ppa.json")
    hidden_leak_scan = scan_safe_files(
        tuple(safe_file_list),
        hidden_paths=hidden_source_paths,
        extra_forbidden=hidden_forbidden_tokens,
    )

    step_by_stage = {item.stage: item for item in result.steps}

    final_budget_usage = budget.snapshot().to_dict()
    observed_csim_calls = int(final_budget_usage.get("csim_calls", 0))
    public_csim_floor = len(public_suites)
    hidden_csim_floor = len(public_suites) + len(hidden_suites)
    physical_execution = {
        QualificationStage.PREFLIGHT: completed_invocation_count(
            work / "preflight",
            "testbench_preflight_invocation.json",
        ) >= 1,
        QualificationStage.PUBLIC: (
            completed_invocation_count(
                work / "public",
                "csim_invocation.json",
            ) >= len(public_suites)
            or observed_csim_calls >= public_csim_floor
        ),
        QualificationStage.CSYNTH: completed_invocation_count(
            work / "csynth",
            "csynth_invocation.json",
        ) >= 1,
        QualificationStage.HIDDEN: (
            completed_invocation_count(
                work / "hidden",
                "csim_invocation.json",
            ) >= len(hidden_suites)
            or observed_csim_calls >= hidden_csim_floor
        ),
    }

    def physically_executed(stage: QualificationStage) -> bool:
        step = step_by_stage.get(stage)
        return bool(
            step is not None
            and step.outcome.value not in {"skipped", "cache_hit"}
            and physical_execution.get(stage, False)
        )

    accepted = bool(
        result.accepted
        and result.correctness_passed
        and result.synthesis_passed
        and result.ppa is not None
        and result.objective_feasible is True
        and cache_result is not None
        and cache_result.cache_hit
        and cache_zero_launch
        and not hidden_leak_scan
        and checkpoint.state.best_correct_candidate_id == "baseline"
        and checkpoint.state.best_ppa_candidate_id == "baseline"
    )
    summary = {
        "schema_version": 1,
        "run_id": replay_run_id,
        "status": "accepted" if accepted else "failed",
        "source_run_artifact_root": str(source_artifact_root),
        "source_run_execution_id": source_run_execution_id,
        "source_baseline_kind": source_baseline_kind,
        "source_smoke_case_id": source_smoke_case_id,
        "source_candidate_sha256": baseline.source_sha256,
        "top_function": top_function,
        "target_profile": target_profile.name,
        "requested_vitis_version": target_profile.toolchain_version,
        "actual_vitis_version": toolchain_manifest["actual_version"],
        "stage_order": [item.stage.value for item in result.steps],
        "stage_outcomes": {
            item.stage.value: {
                "outcome": item.outcome.value,
                "route_action": (
                    None
                    if item.route_action is None
                    else item.route_action.value
                ),
                "reason_codes": list(item.reason_codes),
                "blocking": item.source_blocking,
            }
            for item in result.steps
        },
        "qualification_status": result.status.value,
        "qualification_reason_codes": list(
            result.decision.get("reason_codes", [])
        ),
        "correctness_passed": result.correctness_passed,
        "synthesis_passed": result.synthesis_passed,
        "objective_feasible": result.objective_feasible,
        "latency_cycles_max": (
            None if result.ppa is None else result.ppa.latency_cycles_max
        ),
        "initiation_interval_max": (
            None
            if result.ppa is None
            else result.ppa.initiation_interval_max
        ),
        "budget_usage": final_budget_usage,
        "model_api_called": False,
        "real_preflight": physically_executed(QualificationStage.PREFLIGHT),
        "real_public_csim": physically_executed(QualificationStage.PUBLIC),
        "real_csynth": physically_executed(QualificationStage.CSYNTH),
        "real_hidden_csim": physically_executed(QualificationStage.HIDDEN),
        "cache_hit_replay_attempted": cache_result is not None,
        "cache_hit_replay": (
            False if cache_result is None else cache_result.cache_hit
        ),
        "cache_hit_real_tool_delta_zero": cache_zero_launch,
        "hidden_safe_file_leak_findings": hidden_leak_scan,
        "best_correct_candidate_id": (
            checkpoint.state.best_correct_candidate_id
        ),
        "best_ppa_candidate_id": checkpoint.state.best_ppa_candidate_id,
        "checkpoint_path": str(checkpoint.checkpoint_path),
        "cache_key_sha256": cache_identity.cache_key_sha256,
        "comparison_context_identity_sha256": (
            cache_identity.comparison_context_identity_sha256
        ),
    }
    write_json(output_root / "replay_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"S32_REAL_REPLAY_ACCEPTED={str(accepted).lower()}")
    print(f"S32_REAL_REPLAY_DIR={output_root}")
    return 0 if accepted else 2



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="/data/AgRefactor")
    parser.add_argument("--source-run-artifact-root")
    parser.add_argument("--smoke-case-id")
    parser.add_argument("--output-root")
    parser.add_argument("--top")
    parser.add_argument("--csynth-timelimit-s", type=int, default=600)
    parser.add_argument("--csim-timelimit-s", type=int, default=120)
    parser.add_argument("--max-wall-time-s", type=float, default=1800.0)
    return parser.parse_args()


def require_repo(repo: Path) -> None:
    if not (repo / ".git").exists():
        raise FileNotFoundError(f"not a Git repository: {repo}")
    import subprocess

    branch = subprocess.check_output(
        ["git", "-C", str(repo), "branch", "--show-current"],
        text=True,
    ).strip()
    head = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    if branch != "stage2-general-feedback":
        raise RuntimeError(f"unexpected branch: {branch}")
    if head != EXPECTED_BASELINE:
        raise RuntimeError(
            "real replay must run before the S3.2 commit on baseline "
            f"{EXPECTED_BASELINE}; observed {head}"
        )


def discover_source_artifact_root(repo: Path) -> Path:
    text = (repo / "docs/roadmap/PROJECT_STATE.md").read_text(encoding="utf-8")
    match = re.search(
        r"^post_cli_real_smoke_artifact_root=(.+)$",
        text,
        flags=re.MULTILINE,
    )
    if match is None:
        raise ValueError("PROJECT_STATE has no post_cli_real_smoke_artifact_root")
    return Path(match.group(1).strip()).expanduser().resolve()


def discover_source_material(
    artifact_root: Path,
    bundle: dict[str, Any],
) -> dict[str, Path]:
    candidates = bundle.get("candidates") or {}
    final = candidates.get("final") or candidates.get("initial") or {}
    raw_candidate = final.get("path")
    if not isinstance(raw_candidate, str):
        raise ValueError("source execution identity has no final candidate path")
    candidate = Path(raw_candidate).expanduser().resolve()
    if not candidate.is_file():
        raise FileNotFoundError(f"prior accepted candidate is missing: {candidate}")
    expected_sha = final.get("sha256")
    if expected_sha != file_sha256(candidate):
        raise ValueError("prior accepted candidate SHA-256 no longer matches")

    matches: list[tuple[Path, Path, Path]] = []
    for invocation_path in artifact_root.rglob("testbench_preflight_invocation.json"):
        try:
            invocation = read_json(invocation_path)
        except Exception:
            continue
        execution = invocation.get("execution") or {}
        if execution.get("status") != "completed" or execution.get("returncode") != 0:
            continue
        parent = invocation_path.parent
        original = parent / "orig_code.cpp"
        replay_candidate = parent / "refactor_code.cpp"
        testbench = parent / "testbench.cpp"
        if not all(path.is_file() for path in (original, replay_candidate, testbench)):
            continue
        if file_sha256(replay_candidate) == expected_sha:
            matches.append((original, replay_candidate, testbench))
    if matches:
        matches.sort(key=lambda item: tuple(str(path) for path in item))
        original, replay_candidate, testbench = matches[-1]
        return {
            "original_path": original.resolve(),
            "candidate_path": replay_candidate.resolve(),
            "preflight_testbench_path": testbench.resolve(),
        }

    source = bundle.get("source") or {}
    raw_source = source.get("path")
    if not isinstance(raw_source, str):
        raise ValueError("source execution identity has no original source path")
    original = Path(raw_source).expanduser().resolve()
    public_paths = [
        Path(item["testbench_path"]).expanduser().resolve()
        for item in bundle.get("suites", ())
        if item.get("split") == "public"
        and isinstance(item.get("testbench_path"), str)
    ]
    if not original.is_file() or not public_paths or not public_paths[0].is_file():
        raise FileNotFoundError(
            "could not recover a successful prior Preflight source trio"
        )
    return {
        "original_path": original,
        "candidate_path": candidate,
        "preflight_testbench_path": public_paths[0],
    }


def discover_csynth_material(
    artifact_root: Path,
    candidate_path: Path,
    bundle: dict[str, Any],
) -> dict[str, Any]:
    candidate_sha = file_sha256(candidate_path)
    matches: list[dict[str, Any]] = []
    for invocation_path in artifact_root.rglob("csynth_invocation.json"):
        try:
            invocation = read_json(invocation_path)
        except Exception:
            continue
        execution = invocation.get("execution") or {}
        verification = invocation.get("toolchain_version_verification") or {}
        if execution.get("status") != "completed" or execution.get("returncode") != 0:
            continue
        if verification.get("status") not in {"matched", "detected"}:
            continue
        source_files = invocation.get("source_files") or []
        source_matches = False
        for name in source_files:
            path = invocation_path.parent / str(name)
            if path.is_file() and file_sha256(path) == candidate_sha:
                source_matches = True
                break
        if source_matches:
            matches.append(invocation)
    if matches:
        invocation = matches[-1]
        return {
            "top_function": invocation["top_kernel"],
            "target_profile": invocation["target_profile"],
        }
    task = ((bundle.get("normalized_task") or {}).get("value") or {})
    target = (bundle.get("target") or {}).get("value") or {}
    profile = target.get("profile", target)
    top = task.get("kernel_name") or task.get("top_function")
    if not isinstance(top, str) or not top.strip():
        raise ValueError("could not discover prior accepted CSYNTH top function")
    return {"top_function": top, "target_profile": profile}


def load_suites(
    bundle: dict[str, Any],
) -> tuple[tuple[TestSuiteSpec, ...], dict[str, str], tuple[Any, ...]]:
    specs: list[TestSuiteSpec] = []
    codes: dict[str, str] = {}
    identities = []
    for raw in bundle.get("suites", ()):
        if raw.get("evaluation_status") != "passed":
            raise ValueError(
                f"source suite was not accepted: {raw.get('suite_id')}"
            )
        path_value = raw.get("testbench_path")
        if not isinstance(path_value, str):
            raise ValueError("source suite has no testbench path")
        path = Path(path_value).expanduser().resolve()
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(f"suite testbench is missing: {path}")
        suite_id = str(raw["suite_id"])
        split = EvaluationSplit(str(raw["split"]))
        specs.append(
            TestSuiteSpec(
                suite_id=suite_id,
                split=split,
                testbench_path=str(path),
                suite_version=raw.get("suite_version"),
            )
        )
        codes[suite_id] = path.read_text(encoding="utf-8")
        identities.append(
            suite_identity_from_file(
                suite_id=suite_id,
                split=split.value,
                path=path,
                suite_version=raw.get("suite_version"),
                source_identity={
                    "source_id": raw.get("source_id"),
                    "source_revision": raw.get("source_revision"),
                    "source_kind": raw.get("source_kind"),
                    "qualification_status": raw.get("qualification_status"),
                    "evaluation_status": raw.get("evaluation_status"),
                },
            )
        )
    if not specs:
        raise ValueError("source execution identity has no suites")
    return tuple(specs), codes, tuple(identities)



def load_smoke_case_material(
    repo: Path,
    output_root: Path,
    case_id: str,
) -> dict[str, Any]:
    normalized = case_id.strip()
    if not normalized:
        raise ValueError("smoke case id must not be empty")
    matches = tuple(
        item for item in STAGE2_SMOKE_CASES if item.case_id == normalized
    )
    if len(matches) != 1:
        available = ", ".join(item.case_id for item in STAGE2_SMOKE_CASES)
        raise ValueError(
            f"unknown Stage 2 smoke case {normalized!r}; available: {available}"
        )
    case = matches[0]
    material_root = output_root / "source_material"
    material_root.mkdir(parents=True, exist_ok=False)
    candidate_path = material_root / "candidate.cpp"
    original_path = material_root / "original.cpp"
    preflight_path = material_root / "preflight.cpp"
    public_path = material_root / "public_suite.cpp"
    hidden_path = material_root / "operator_suite.cpp"
    for path, content in (
        (candidate_path, case.candidate_code),
        (original_path, case.original_code),
        (preflight_path, case.preflight_testbench_code),
        (public_path, case.public_testbench_code),
        (hidden_path, case.hidden_testbench_code),
    ):
        path.write_text(content, encoding="utf-8")

    public_id = f"stage2-smoke-{case.case_id}-public"
    hidden_id = f"stage2-smoke-{case.case_id}-hidden"
    suites = (
        TestSuiteSpec(
            suite_id=public_id,
            split=EvaluationSplit.PUBLIC,
            suite_version="stage2-smoke-v1",
            testbench_path=str(public_path),
        ),
        TestSuiteSpec(
            suite_id=hidden_id,
            split=EvaluationSplit.HIDDEN,
            suite_version="stage2-smoke-v1",
            testbench_path=str(hidden_path),
        ),
    )
    suite_codes = {
        public_id: case.public_testbench_code,
        hidden_id: case.hidden_testbench_code,
    }
    corpus_path = repo / "agrefactor/smoke/stage2_corpus.py"
    source_identity = {
        "source_kind": "committed_stage2_smoke_case",
        "case_id": case.case_id,
        "kernel_type": case.kernel_type.value,
        "corpus_sha256": file_sha256(corpus_path),
        "ground_truth_terminal": case.ground_truth.expected_terminal_state.value,
    }
    suite_identities = (
        suite_identity_from_file(
            suite_id=public_id,
            split=EvaluationSplit.PUBLIC.value,
            path=public_path,
            suite_version="stage2-smoke-v1",
            source_identity={**source_identity, "split": "public"},
        ),
        suite_identity_from_file(
            suite_id=hidden_id,
            split=EvaluationSplit.HIDDEN.value,
            path=hidden_path,
            suite_version="stage2-smoke-v1",
            source_identity={**source_identity, "split": "hidden"},
        ),
    )
    return {
        "case_id": case.case_id,
        "source_artifact_root": corpus_path.resolve(),
        "candidate_path": candidate_path.resolve(),
        "original_path": original_path.resolve(),
        "preflight_testbench_path": preflight_path.resolve(),
        "candidate_code": case.candidate_code,
        "original_code": case.original_code,
        "preflight_testbench_code": case.preflight_testbench_code,
        "top_function": case.kernel_name,
        "target_profile": resolve_target_profile(None),
        "suites": suites,
        "suite_codes": suite_codes,
        "suite_identities": suite_identities,
        "hidden_source_paths": (hidden_path.resolve(),),
        "hidden_forbidden_tokens": (case.hidden_secret_marker,),
    }


def completed_invocation_count(root: Path, filename: str) -> int:
    count = 0
    if not root.is_dir():
        return 0
    for path in root.rglob(filename):
        try:
            payload = read_json(path)
        except Exception:
            continue
        execution = payload.get("execution") or {}
        if not isinstance(execution, dict):
            continue
        status = execution.get("status")
        returncode = execution.get("returncode")
        if status == "completed" and returncode in {0, None}:
            count += 1
    return count


def observe_toolchain(target_profile: Any) -> dict[str, Any]:
    resolution = resolve_csynth_command(target_profile)
    verification = probe_csynth_version(
        resolution,
        target_profile.toolchain_version,
    )
    if verification.get("status") not in {"matched", "detected"}:
        raise RuntimeError(
            "Vitis toolchain pre-observation failed: "
            f"{verification.get('status')}"
        )
    version_text = (
        str(verification.get("stdout") or "")
        + "\n"
        + str(verification.get("stderr") or "")
    )
    executable = resolution.get("resolved_executable")
    settings = resolution.get("resolved_settings_path")
    return {
        "schema_version": 1,
        "profile_name": target_profile.name,
        "requested_version": target_profile.toolchain_version,
        "actual_version": verification.get("actual"),
        "verification_status": verification.get("status"),
        "command_source": resolution.get("command_source"),
        "probe_source": resolution.get("probe_source"),
        "resolved_executable": executable,
        "resolved_executable_sha256": (
            file_sha256(executable)
            if isinstance(executable, str) and Path(executable).is_file()
            else None
        ),
        "resolved_settings_path": settings,
        "resolved_settings_sha256": (
            file_sha256(settings)
            if isinstance(settings, str) and Path(settings).is_file()
            else None
        ),
        "version_output_sha256": sha256(version_text.encode("utf-8")).hexdigest(),
        "parser_profile": target_profile.parser_profile,
        "effective_target_sha256": sha256(
            json.dumps(
                target_profile.to_effective_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }


def scan_safe_files(
    paths: tuple[Path, ...],
    *,
    hidden_paths: tuple[Path, ...],
    extra_forbidden: tuple[str, ...] = (),
) -> list[str]:
    findings: list[str] = []
    forbidden = [str(path) for path in hidden_paths]
    forbidden.extend(path.name for path in hidden_paths)
    forbidden.extend(
        [
            "HIDDEN_DETAIL_MUST_NOT_LEAK",
            "hidden_testbench",
            "raw_diagnostic",
        ]
    )
    forbidden.extend(token for token in extra_forbidden if token)
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for token in forbidden:
            if token and token in text:
                findings.append(f"{path.name}:{token}")
    return findings


def hard_counters(value: dict[str, Any]) -> dict[str, Any]:
    return {
        name: value.get(name)
        for name in (
            "llm_calls",
            "tool_calls",
            "compile_calls",
            "csim_calls",
            "csynth_calls",
        )
    }


def default_output_root() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(
        f"/data/agrefactor_runs/stage3_s32_real_replay_{stamp}_{os.getpid()}"
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"JSON file must contain an object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - acceptance tool prints a clear terminal.
        print(f"S32_REAL_REPLAY_ACCEPTED=false", file=sys.stderr)
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise
