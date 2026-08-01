#!/usr/bin/env python3
"""Run the S3.7 real product-adapter full-chain acceptance smoke.

The smoke goes through ``Stage3ProductOptimizationPhase`` and performs real
baseline/candidate qualification plus one physical analysis at each level.
Each rewrite is evidence-conditional: it may reach qualification, be omitted
because analysis produced no executable hypothesis, or safely abstain when a
model response violates the complete-source contract. At least one generated
candidate must reach real qualification so the product adapter path is covered.
Normal product execution remains frozen at safe-v1's 2/2/3 rounds; the
one-round cap is acceptance-only.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from agrefactor.config import RunMode, TaskSpec, resolve_target_profile
from agrefactor.models import infer_model_family, resolve_model_runtime
from agrefactor.optimization import (
    BOTTLENECK_MODEL_CALL_KIND_ANALYSIS,
    BOTTLENECK_MODEL_CALL_KIND_REWRITE,
    PRAGMA_MODEL_CALL_KIND_ANALYSIS,
    PRAGMA_MODEL_CALL_KIND_REWRITE,
    STRUCTURAL_MODEL_CALL_KIND_HYPOTHESIS,
    STRUCTURAL_MODEL_CALL_KIND_REWRITE,
    candidate_index_from_dict,
)
from agrefactor.product.run_output import capture_product_streams, finalize_product_artifacts
from agrefactor.product.stage3_optimizer import (
    ProductOptimizerRequest,
    Stage3ProductOptimizationPhase,
    build_direct_optimization_material,
)
from agrefactor.runtime import RunPhase, UnifiedRunner
from agrefactor.runtime.budget_profile import DEFAULT_SOURCE_RUN_BUDGET_PROFILE
from agrefactor.smoke import STAGE2_SMOKE_CASES


EXPECTED_BASELINE = "197327af79382327f2711119225d47e8ea060e00"
DEFAULT_MODEL = "deepseek-v4-flash"
OUTPUT_TOKEN_LIMIT = 32768
OUTPUT_TOKEN_SAFETY_CEILING = 65536
MIN_SEMANTIC_REAL_LLM_CALLS = 3
MIN_EXPECTED_REAL_LLM_CALLS = 4
MAX_EXPECTED_REAL_LLM_CALLS = 6
MAX_SAFE_V1_LLM_CALLS = 14
LEVEL_MODEL_CALL_KINDS = (
    (
        "structural",
        STRUCTURAL_MODEL_CALL_KIND_HYPOTHESIS,
        STRUCTURAL_MODEL_CALL_KIND_REWRITE,
    ),
    (
        "bottleneck",
        BOTTLENECK_MODEL_CALL_KIND_ANALYSIS,
        BOTTLENECK_MODEL_CALL_KIND_REWRITE,
    ),
    (
        "pragma",
        PRAGMA_MODEL_CALL_KIND_ANALYSIS,
        PRAGMA_MODEL_CALL_KIND_REWRITE,
    ),
)
REQUIRED_ANALYSIS_CALL_KINDS = tuple(item[1] for item in LEVEL_MODEL_CALL_KINDS)
CONDITIONAL_REWRITE_CALL_KINDS = tuple(item[2] for item in LEVEL_MODEL_CALL_KINDS)



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the S3.7 real product-adapter full-chain smoke."
    )
    parser.add_argument("--repo", required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--family")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key-env")
    parser.add_argument("--output-root")
    parser.add_argument("--csim-timeout-s", type=int, default=180)
    parser.add_argument("--csynth-timeout-s", type=int, default=900)
    parser.add_argument("--max-wall-time-s", type=float, default=7200.0)
    return parser.parse_args()


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
    artifact_root = output_root / "artifacts"
    work_root = output_root / "work"
    material_root = output_root / "material"
    artifact_root.mkdir(parents=True, exist_ok=False)
    work_root.mkdir(parents=True, exist_ok=False)
    material_root.mkdir(parents=True, exist_ok=False)

    case = next(item for item in STAGE2_SMOKE_CASES if item.case_id == "array-map")
    baseline_path = write_text(material_root / "candidate.cpp", case.candidate_code)
    reference_path = write_text(material_root / "original.cpp", case.original_code)
    public_path = write_text(material_root / "public.cpp", case.public_testbench_code)
    hidden_path = write_text(material_root / "hidden.cpp", case.hidden_testbench_code)

    target = resolve_target_profile(None)
    material = build_direct_optimization_material(
        source_path=baseline_path,
        top_function=case.kernel_name,
        reference_source_path=reference_path,
        reference_top_function="original_top",
        public_test_paths=(public_path,),
        hidden_test_paths=(hidden_path,),
        target=target,
    )

    family = (
        args.family.strip().casefold()
        if isinstance(args.family, str) and args.family.strip()
        else infer_model_family(args.model).casefold()
    )
    parameters: dict[str, Any] = {
        "temperature": 0,
        "max_tokens": OUTPUT_TOKEN_LIMIT,
    }
    if family == "deepseek":
        parameters["extra_body"] = {"thinking": {"type": "disabled"}}
    runtime = resolve_model_runtime(
        args.model,
        family=args.family,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        parameters=parameters,
    )
    credential_env = runtime.effective_config.api_key_env
    if not credential_env or not os.environ.get(credential_env):
        raise RuntimeError(
            f"required model credential environment variable is not set: {credential_env}"
        )

    requested = {
        "max_llm_calls": MAX_EXPECTED_REAL_LLM_CALLS,
        "max_tool_calls": 64,
        "max_compile_calls": 24,
        "max_csim_calls": 16,
        "max_csynth_calls": 8,
        "max_wall_time_s": float(args.max_wall_time_s),
    }
    budget = DEFAULT_SOURCE_RUN_BUDGET_PROFILE.resolve(user_requested=requested)
    run_id = output_root.name
    phase = Stage3ProductOptimizationPhase(
        ProductOptimizerRequest(
            run_id=run_id,
            mode=RunMode.OPTIMIZE,
            registry=runtime.registry,
            effective_model_config=runtime.effective_config,
            budget_contract=budget,
            artifact_root=artifact_root,
            work_root=work_root,
            csim_timeout_s=args.csim_timeout_s,
            csynth_timeout_s=args.csynth_timeout_s,
            optimizer_profile="safe-v1",
            optimization_objective="latency",
            acceptance_one_physical_round_per_level=True,
            direct_material=material,
        )
    )
    task = TaskSpec(
        task_id=f"{run_id}.task",
        kernel_path=str(baseline_path),
        kernel_name=case.kernel_name,
        target=target,
        mode=RunMode.OPTIMIZE,
        testbench_path=str(public_path),
        test_suites=material.suites,
    )
    runner = UnifiedRunner(
        {RunPhase.OPTIMIZE: phase},
        budget_limits=budget.to_budget_limits(),
    )
    with capture_product_streams(work_root) as captured:
        result = runner.run(
            task,
            run_id=run_id,
            trace_path=artifact_root / "trace.jsonl",
            artifact_root=artifact_root,
            run_metadata={
                "execution_mode": "stage3_s37_real_product_smoke",
                "legacy_mode": False,
                "model_selection": "user_fixed",
                "stage3_product_adapter": True,
                "smoke_case_id": case.case_id,
                "claim_scope": "single_kernel_product_adapter_entry_gate",
            },
        )
    finalize_product_artifacts(
        result,
        artifact_root=artifact_root,
        work_root=work_root,
        captured=captured,
    )
    summary = verify_result(
        result=result,
        artifact_root=artifact_root,
        hidden_marker=case.hidden_secret_marker,
        model=args.model,
        family=family,
    )
    write_json(output_root / "summary.json", summary)
    emit_summary(summary)
    return 0


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    values: list[dict[str, Any]] = []
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip():
            continue
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise TypeError(f"JSONL line must contain an object: {path}:{line_number}")
        values.append(value)
    return values


def _level_decisions(
    decisions: list[dict[str, Any]], *, level: str, event: str
) -> list[dict[str, Any]]:
    return [
        item
        for item in decisions
        if item.get("level") == level and item.get("event") == event
    ]


def _require_one_level_decision(
    decisions: list[dict[str, Any]], *, level: str, event: str
) -> dict[str, Any]:
    matches = _level_decisions(decisions, level=level, event=event)
    if len(matches) != 1:
        raise RuntimeError(
            f"{level} requires exactly one {event} decision, got {len(matches)}"
        )
    return matches[0]


def _validate_invalid_call_link(
    *,
    call: dict[str, Any],
    decision: dict[str, Any],
    level: str,
    event: str,
) -> None:
    error_code = call.get("error_code")
    detail_codes = call.get("error_reason_codes", [])
    if not isinstance(error_code, str) or not error_code:
        raise RuntimeError(f"{level} invalid call lacks a safe error_code")
    if not isinstance(detail_codes, list) or not detail_codes:
        raise RuntimeError(f"{level} invalid call lacks safe reason codes")
    metadata = decision.get("metadata", {})
    if not isinstance(metadata, dict):
        raise RuntimeError(f"{level} {event} metadata is invalid")
    if metadata.get("automatic_retry") is not False:
        raise RuntimeError(f"{level} {event} enabled automatic retry")
    if metadata.get("error_code") != error_code:
        raise RuntimeError(f"{level} {event} error linkage mismatch")
    if list(metadata.get("detail_codes", [])) != detail_codes:
        raise RuntimeError(f"{level} {event} detail linkage mismatch")


def verify_model_call_contract(
    *, calls: list[dict[str, Any]], artifact_root: Path
) -> dict[str, Any]:
    """Validate semantic coverage without forcing stochastic model work.

    Every level performs one physical analysis call. A valid analysis may omit
    rewrite when it produces no executable hypothesis. An invalid analysis or
    rewrite is accepted only when the product state machine records the exact
    typed, no-retry safe-abstention decision. A valid rewrite must be linked to
    one terminal candidate and therefore to real qualification. Call count is observed only as a broad semantic bound (3..6), never used
    as a substitute for event semantics. The complete acceptance smoke separately
    requires at least one qualified rewrite, so an accepted run uses 4..6 calls.
    """

    count = len(calls)
    if not MIN_SEMANTIC_REAL_LLM_CALLS <= count <= MAX_EXPECTED_REAL_LLM_CALLS:
        raise RuntimeError(
            "physical model call count is outside the semantic bound: "
            f"expected={MIN_SEMANTIC_REAL_LLM_CALLS}..{MAX_EXPECTED_REAL_LLM_CALLS} "
            f"actual={count}"
        )
    sequences = [item.get("sequence") for item in calls]
    if sequences != list(range(1, count + 1)):
        raise RuntimeError(f"model call sequence is not contiguous: {sequences}")

    decisions = _read_jsonl(
        artifact_root / "optimize" / "optimizer" / "decisions.jsonl"
    )
    candidate_index_path = (
        artifact_root / "optimize" / "optimizer" / "candidate_index.json"
    )
    candidate_index_payload = (
        read_json(candidate_index_path) if candidate_index_path.is_file() else None
    )
    candidate_index = (
        candidate_index_from_dict(candidate_index_payload)
        if isinstance(candidate_index_payload, dict)
        else {}
    )
    cursor = 0
    branches: dict[str, str] = {}
    valid_count = 0
    analysis_abstention_count = 0
    rewrite_abstention_count = 0
    qualified_rewrite_count = 0
    call_sequence = tuple(str(item.get("call_kind")) for item in calls)

    for level, analysis_kind, rewrite_kind in LEVEL_MODEL_CALL_KINDS:
        if cursor >= count or calls[cursor].get("call_kind") != analysis_kind:
            raise RuntimeError(
                f"missing or out-of-order {level} analysis call: {call_sequence}"
            )
        analysis = calls[cursor]
        cursor += 1

        if analysis.get("response_valid") is not True:
            decision = _require_one_level_decision(
                decisions,
                level=level,
                event="hypothesis_generation_abstained",
            )
            _validate_invalid_call_link(
                call=analysis,
                decision=decision,
                level=level,
                event="hypothesis_generation_abstained",
            )
            metadata = decision.get("metadata", {})
            if metadata.get("hypothesis_created") is not False:
                raise RuntimeError(f"{level} analysis abstention fabricated a hypothesis")
            branches[level] = "analysis_contract_abstention"
            analysis_abstention_count += 1
            continue

        valid_count += 1
        if cursor < count and calls[cursor].get("call_kind") == rewrite_kind:
            rewrite = calls[cursor]
            cursor += 1
            if rewrite.get("response_valid") is True:
                terminal = _require_one_level_decision(
                    decisions,
                    level=level,
                    event="candidate_terminal",
                )
                candidate_id = terminal.get("candidate_id")
                metadata = terminal.get("metadata", {})
                if not isinstance(candidate_id, str) or candidate_id not in candidate_index:
                    raise RuntimeError(
                        f"{level} valid rewrite lacks a persisted candidate"
                    )
                if not isinstance(metadata, dict) or not isinstance(
                    metadata.get("qualification_status"), str
                ):
                    raise RuntimeError(
                        f"{level} valid rewrite lacks terminal qualification evidence"
                    )
                valid_count += 1
                qualified_rewrite_count += 1
                branches[level] = "rewrite_qualified"
            else:
                decision = _require_one_level_decision(
                    decisions,
                    level=level,
                    event="candidate_generation_abstained",
                )
                _validate_invalid_call_link(
                    call=rewrite,
                    decision=decision,
                    level=level,
                    event="candidate_generation_abstained",
                )
                metadata = decision.get("metadata", {})
                if metadata.get("candidate_created") is not False:
                    raise RuntimeError(f"{level} rewrite abstention fabricated a candidate")
                if metadata.get("qualification_started") is not False:
                    raise RuntimeError(f"{level} rewrite abstention started qualification")
                branches[level] = "rewrite_contract_abstention"
                rewrite_abstention_count += 1
        else:
            _require_one_level_decision(
                decisions,
                level=level,
                event="round_no_executable_hypothesis",
            )
            branches[level] = "analysis_safe_abstention"

    if cursor != count:
        raise RuntimeError(
            "unexpected model call after three-level semantic coverage: "
            + ", ".join(call_sequence[cursor:])
        )

    action_root = artifact_root / "optimize" / "model" / "pragma_actions"
    action_paths = sorted(action_root.glob("*.json")) if action_root.is_dir() else []
    pragma_branch = branches["pragma"]
    if pragma_branch != "analysis_contract_abstention" and not action_paths:
        raise RuntimeError("valid Pragma analysis did not persist a typed action record")
    action_records = [read_json(path) for path in action_paths]
    for path, action in zip(action_paths, action_records):
        if action.get("authoritative") is not False:
            raise RuntimeError(f"Pragma action became authoritative: {path}")
        if action.get("action_source") != "model_proposal":
            raise RuntimeError(f"Pragma action source is not model_proposal: {path}")

    hypothesis_root = artifact_root / "optimize" / "optimizer" / "hypotheses"
    pragma_hypothesis_paths = (
        sorted(hypothesis_root.glob("hyp-pragma-*.json"))
        if hypothesis_root.is_dir()
        else []
    )
    if pragma_branch in {
        "analysis_safe_abstention",
        "analysis_contract_abstention",
    } and pragma_hypothesis_paths:
        raise RuntimeError("Pragma analysis abstention persisted an executable hypothesis")
    if pragma_branch in {
        "rewrite_qualified",
        "rewrite_contract_abstention",
    } and not pragma_hypothesis_paths:
        raise RuntimeError("Pragma rewrite branch lacks an executable hypothesis artifact")

    return {
        "model_call_kinds": list(call_sequence),
        "model_response_valid_count": valid_count,
        "analysis_abstention_count": analysis_abstention_count,
        "rewrite_abstention_count": rewrite_abstention_count,
        "controlled_model_abstention_count": (
            analysis_abstention_count + rewrite_abstention_count
        ),
        "qualified_rewrite_count": qualified_rewrite_count,
        "level_execution_branches": branches,
        "pragma_execution_branch": pragma_branch,
        "pragma_rewrite_called": pragma_branch in {
            "rewrite_qualified",
            "rewrite_contract_abstention",
        },
        "pragma_safe_abstention": pragma_branch != "rewrite_qualified",
        "pragma_action_count": len(action_paths),
        "pragma_hypothesis_count": len(pragma_hypothesis_paths),
    }

def verify_result(
    *, result, artifact_root: Path, hidden_marker: str, model: str, family: str
) -> dict[str, Any]:
    if not result.succeeded:
        raise RuntimeError(f"product smoke failed: {result.status.value}")
    if len(result.phases) != 1 or result.phases[0].phase is not RunPhase.OPTIMIZE:
        raise RuntimeError("product smoke did not execute exactly the optimize phase")
    phase = result.phases[0]
    if phase.metadata.get("accepted") is not True:
        raise RuntimeError("product optimization phase was not accepted")

    identity = read_json(artifact_root / "stage3_execution_identity.json")
    if identity.get("baseline_qualification", {}).get("status") != "accepted":
        raise RuntimeError("baseline was not accepted before model execution")
    boundaries = identity.get("boundaries", {})
    required_boundaries = {
        "baseline_qualified_before_model": True,
        "static_source_gate_used": False,
        "hidden_evidence_exposed": False,
        "model_hypotheses_authoritative": False,
        "candidate_correctness_repair_attempts": 0,
        "silent_refactor_fallback": False,
        "acceptance_one_physical_round_per_level": True,
        "normal_product_policy_unchanged": True,
    }
    for key, expected in required_boundaries.items():
        if boundaries.get(key) != expected:
            raise RuntimeError(f"boundary mismatch: {key}")

    policy = identity.get("policy", {})
    rounds = {
        key: value.get("max_rounds")
        for key, value in policy.get("levels", {}).items()
        if isinstance(value, dict)
    }
    if rounds != {"structural": 2, "bottleneck": 2, "pragma": 3}:
        raise RuntimeError(f"normal safe-v1 policy changed: {rounds}")

    calls_path = artifact_root / "optimize" / "model" / "model_calls.jsonl"
    calls = [
        json.loads(line)
        for line in calls_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    call_contract = verify_model_call_contract(calls=calls, artifact_root=artifact_root)
    kinds = set(call_contract["model_call_kinds"])
    usage_llm_calls = int(identity.get("budget_usage", {}).get("llm_calls", -1))
    if usage_llm_calls != len(calls):
        raise RuntimeError(
            "physical model call accounting mismatch: "
            f"records={len(calls)} budget_usage={usage_llm_calls}"
        )

    state = identity.get("state", {})
    best_correct = state.get("best_correct_candidate_id")
    candidates = identity.get("candidate_index", {})
    if not isinstance(best_correct, str) or best_correct not in candidates:
        raise RuntimeError("best_correct was not preserved")
    for required in (
        artifact_root / "optimize" / "final_candidate.cpp",
        artifact_root / "run_artifact_manifest.json",
        artifact_root / "full_result.json",
        artifact_root / "model_calls.json",
    ):
        if not required.is_file():
            raise FileNotFoundError(f"unified product artifact is missing: {required}")

    marker = hidden_marker.encode("utf-8")
    for path in (artifact_root / "optimize" / "model").rglob("*"):
        if path.is_file() and marker in path.read_bytes():
            raise RuntimeError(f"Hidden marker leaked into model artifact: {path}")

    usage = identity.get("budget_usage", {})
    tool_calls = int(usage.get("tool_calls", 0))
    compile_calls = int(usage.get("compile_calls", 0))
    csim_calls = int(usage.get("csim_calls", 0))
    csynth_calls = int(usage.get("csynth_calls", 0))
    if min(tool_calls, compile_calls, csim_calls, csynth_calls) <= 0:
        raise RuntimeError("real qualification tool accounting was not observed")
    executed_candidate_count = int(state.get("executed_candidate_count", -1))
    if executed_candidate_count <= 0 or call_contract["qualified_rewrite_count"] <= 0:
        raise RuntimeError(
            "no generated candidate completed real qualification for comparison"
        )
    if len(calls) < MIN_EXPECTED_REAL_LLM_CALLS:
        raise RuntimeError(
            "accepted smoke requires at least one analysis/rewrite pair: "
            f"minimum={MIN_EXPECTED_REAL_LLM_CALLS} actual={len(calls)}"
        )

    return {
        "schema_version": 1,
        "accepted": True,
        "claim_scope": "single_kernel_product_adapter_entry_gate",
        "repository_baseline": EXPECTED_BASELINE,
        "model": model,
        "model_family": family,
        "analysis_json_authority": "local_strict_response_contract",
        "analysis_provider_json_mode": False,
        "thinking_mode_control": "disabled" if family == "deepseek" else "provider_default",
        "output_token_policy": "stage2_typed_output_policy",
        "analysis_max_tokens": OUTPUT_TOKEN_LIMIT,
        "rewrite_max_tokens": OUTPUT_TOKEN_LIMIT,
        "output_token_safety_ceiling": OUTPUT_TOKEN_SAFETY_CEILING,
        "baseline_qualification_accepted": True,
        "levels_exercised": ["structural", "bottleneck", "pragma"],
        "model_call_count": len(calls),
        "expected_real_llm_calls_min": MIN_EXPECTED_REAL_LLM_CALLS,
        "expected_real_llm_calls_max": MAX_EXPECTED_REAL_LLM_CALLS,
        "required_analysis_call_kinds": list(REQUIRED_ANALYSIS_CALL_KINDS),
        "conditional_rewrite_call_kinds": list(CONDITIONAL_REWRITE_CALL_KINDS),
        "model_response_valid_count": call_contract["model_response_valid_count"],
        "analysis_abstention_count": call_contract["analysis_abstention_count"],
        "rewrite_abstention_count": call_contract["rewrite_abstention_count"],
        "controlled_model_abstention_count": call_contract["controlled_model_abstention_count"],
        "qualified_rewrite_count": call_contract["qualified_rewrite_count"],
        "level_execution_branches": call_contract["level_execution_branches"],
        "pragma_execution_branch": call_contract["pragma_execution_branch"],
        "pragma_rewrite_called": call_contract["pragma_rewrite_called"],
        "pragma_safe_abstention": call_contract["pragma_safe_abstention"],
        "pragma_action_count": call_contract["pragma_action_count"],
        "pragma_hypothesis_count": call_contract["pragma_hypothesis_count"],
        "normal_product_policy_unchanged": "safe-v1-2-2-3",
        "acceptance_physical_rounds_per_level": 1,
        "model_call_kinds": call_contract["model_call_kinds"],
        "tool_calls": tool_calls,
        "compile_calls": compile_calls,
        "csim_calls": csim_calls,
        "csynth_calls": csynth_calls,
        "best_correct_candidate_id": best_correct,
        "best_correct_protected": True,
        "candidate_comparison_observed": True,
        "final_candidate_sha256": identity.get("final_candidate", {}).get("sha256"),
        "terminal_status": identity.get("terminal_status"),
        "network_called": True,
        "vitis_called": True,
        "raw_report_used_by_model": False,
        "static_optimization_gate_used": False,
        "hidden_evidence_exposed": False,
        "correctness_repair_attempts": 0,
        "unified_artifacts": True,
        "multi_kernel_claimed": False,
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "artifact_root": str(artifact_root),
    }


def emit_summary(summary: dict[str, Any]) -> None:
    print("S37_REAL_PRODUCT_SMOKE_ACCEPTED=true")
    print(f"MODEL={summary['model']}")
    print(f"ANALYSIS_JSON_AUTHORITY={summary['analysis_json_authority']}")
    print(f"ANALYSIS_PROVIDER_JSON_MODE={str(summary['analysis_provider_json_mode']).lower()}")
    print(f"THINKING_MODE_CONTROL={summary['thinking_mode_control']}")
    print(f"OUTPUT_TOKEN_POLICY={summary['output_token_policy']}")
    print(f"ANALYSIS_MAX_TOKENS={summary['analysis_max_tokens']}")
    print(f"REWRITE_MAX_TOKENS={summary['rewrite_max_tokens']}")
    print(f"OUTPUT_TOKEN_SAFETY_CEILING={summary['output_token_safety_ceiling']}")
    print("BASELINE_QUALIFICATION_ACCEPTED=true")
    print("LEVELS_EXERCISED=structural,bottleneck,pragma")
    print(f"REAL_LLM_CALLS={summary['model_call_count']}")
    print(f"EXPECTED_REAL_LLM_CALLS_MIN={summary['expected_real_llm_calls_min']}")
    print(f"EXPECTED_REAL_LLM_CALLS_MAX={summary['expected_real_llm_calls_max']}")
    print(f"MODEL_RESPONSE_VALID_COUNT={summary['model_response_valid_count']}")
    print(f"ANALYSIS_ABSTENTION_COUNT={summary['analysis_abstention_count']}")
    print(f"REWRITE_ABSTENTION_COUNT={summary['rewrite_abstention_count']}")
    print(f"CONTROLLED_MODEL_ABSTENTION_COUNT={summary['controlled_model_abstention_count']}")
    print(f"QUALIFIED_REWRITE_COUNT={summary['qualified_rewrite_count']}")
    branches = summary["level_execution_branches"]
    print(f"STRUCTURAL_EXECUTION_BRANCH={branches['structural']}")
    print(f"BOTTLENECK_EXECUTION_BRANCH={branches['bottleneck']}")
    print(f"PRAGMA_EXECUTION_BRANCH={summary['pragma_execution_branch']}")
    print(f"PRAGMA_REWRITE_CALLED={str(summary['pragma_rewrite_called']).lower()}")
    print(f"PRAGMA_SAFE_ABSTENTION={str(summary['pragma_safe_abstention']).lower()}")
    print(f"PRAGMA_ACTION_COUNT={summary['pragma_action_count']}")
    print(f"PRAGMA_HYPOTHESIS_COUNT={summary['pragma_hypothesis_count']}")
    print("NORMAL_PRODUCT_POLICY=safe-v1-2-2-3")
    print("ACCEPTANCE_PHYSICAL_ROUNDS_PER_LEVEL=1")
    print(f"TOOL_CALLS={summary['tool_calls']}")
    print(f"COMPILE_CALLS={summary['compile_calls']}")
    print(f"CSIM_CALLS={summary['csim_calls']}")
    print(f"CSYNTH_CALLS={summary['csynth_calls']}")
    print("NETWORK_CALLED=true")
    print("VITIS_CALLED=true")
    print("BEST_CORRECT_PROTECTED=true")
    print("CANDIDATE_COMPARISON_OBSERVED=true")
    print("STATIC_OPTIMIZATION_GATE_USED=false")
    print("HIDDEN_EVIDENCE_EXPOSED=false")
    print("CORRECTNESS_REPAIR_ATTEMPTS=0")
    print("UNIFIED_ARTIFACTS=true")
    print("MULTI_KERNEL_CLAIMED=false")
    print(f"TERMINAL_STATUS={summary['terminal_status']}")
    print(f"ARTIFACT_ROOT={summary['artifact_root']}")


def require_repo(repo: Path) -> None:
    if not (repo / ".git").is_dir():
        raise FileNotFoundError(f"not a Git worktree: {repo}")
    head = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    if head != EXPECTED_BASELINE:
        raise RuntimeError(f"baseline mismatch: expected={EXPECTED_BASELINE} actual={head}")


def write_text(path: Path, value: str) -> Path:
    path.write_text(value.rstrip() + "\n", encoding="utf-8")
    return path


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"JSON artifact must contain an object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def default_output_root() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(f"/data/agrefactor_runs/stage3_s37_real_product_smoke_{stamp}_{os.getpid()}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("S37_REAL_PRODUCT_SMOKE_ACCEPTED=false", file=sys.stderr)
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise
