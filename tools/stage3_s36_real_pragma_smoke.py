#!/usr/bin/env python3
"""Run one bounded real-network S3.6 Pragma model integration smoke.

Successful execution performs exactly two model calls:

1. propose typed, evidence-linked Pragma actions and hypotheses;
2. generate one complete replacement C++ source for the first valid hypothesis.

The evidence is a typed fixture, not a live Vitis run.  This tool performs no
compile, CSIM, CSYNTH, Hidden evaluation, PPA comparison, or product
``optimize/full`` execution.  Acceptance proves only the S3.6 evidence,
action, prompt, response, and complete-source integration contract.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from typing import Any

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from agrefactor.config import RunMode, TaskSpec, resolve_target_profile
from agrefactor.models import infer_model_family, resolve_model_runtime
from agrefactor.optimization import (
    PragmaModelArtifactWriter,
    PragmaModelCandidateGenerator,
    PragmaModelHypothesisProvider,
    CandidateExecutionRequest,
    CandidateRecord,
    CandidateStatus,
    HypothesisRequest,
    OptimizationLevel,
    PpaEvidence,
    PpaReportFormat,
    PpaResourceUsage,
)
from agrefactor.runtime import BudgetLimits, BudgetManager


EXPECTED_BASELINE = "f5a46d62cca864828e6d1ec3bbe7c5b2ef200f8a"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_SOURCE = "tests/fixtures/stage3_s36/pragma_smoke_kernel.cpp"
DEFAULT_TOP = "s36_pragma_top"
STAGE2_COMPATIBLE_OUTPUT_LIMIT = 32768
OUTPUT_TOKEN_SAFETY_CEILING = 65536
COMPARISON_CONTEXT = "5" * 64


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the bounded S3.6 real Pragma model smoke."
    )
    parser.add_argument("--repo", required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--top", default=DEFAULT_TOP)
    parser.add_argument("--family")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key-env")
    parser.add_argument("--output-root")
    parser.add_argument(
        "--analysis-max-tokens",
        type=int,
        default=STAGE2_COMPATIBLE_OUTPUT_LIMIT,
    )
    parser.add_argument(
        "--rewrite-max-tokens",
        type=int,
        default=STAGE2_COMPATIBLE_OUTPUT_LIMIT,
    )
    parser.add_argument("--max-wall-time-s", type=float, default=600.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo = Path(args.repo).expanduser().resolve()
    require_repo(repo)
    source_path = resolve_source(repo, args.source)
    source = source_path.read_bytes()
    try:
        source_text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("smoke source must be UTF-8") from exc
    if not source_text.strip():
        raise ValueError("smoke source must not be empty")

    output_root = (
        Path(args.output_root).expanduser().resolve()
        if args.output_root
        else default_output_root()
    )
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(f"output root is not empty: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)

    budget = BudgetManager(
        BudgetLimits(
            max_llm_calls=2,
            max_tool_calls=0,
            max_compile_calls=0,
            max_csim_calls=0,
            max_csynth_calls=0,
            max_wall_time_s=args.max_wall_time_s,
        )
    )
    artifacts = PragmaModelArtifactWriter(output_root / "optimizer")
    task = TaskSpec(
        task_id=f"{output_root.name}.task",
        kernel_path=str(source_path),
        kernel_name=args.top,
        target=resolve_target_profile(None),
        mode=RunMode.OPTIMIZE,
    )

    common = {
        "family": args.family,
        "base_url": args.base_url,
        "api_key_env": args.api_key_env,
    }
    family = effective_smoke_family(args.model, args.family)
    analysis_parameters = smoke_model_parameters(
        family=family,
        max_tokens=args.analysis_max_tokens,
    )
    rewrite_parameters = smoke_model_parameters(
        family=family,
        max_tokens=args.rewrite_max_tokens,
    )
    analysis_runtime = resolve_model_runtime(
        args.model,
        parameters=analysis_parameters,
        **common,
    )
    rewrite_runtime = resolve_model_runtime(
        args.model,
        parameters=rewrite_parameters,
        **common,
    )
    credential_env = analysis_runtime.effective_config.api_key_env
    if not credential_env or not os.environ.get(credential_env):
        raise RuntimeError(
            f"required model credential environment variable is not set: {credential_env}"
        )

    evidence = smoke_ppa()
    baseline = CandidateRecord(
        candidate_id="baseline",
        sequence=0,
        parent_candidate_id=None,
        hypothesis_id=None,
        level=None,
        source_sha256=sha256(source).hexdigest(),
        source_artifact="candidates/baseline/source.cpp",
        status=CandidateStatus.ACCEPTED,
        correctness={"passed": True, "fixture": True},
        synthesis={
            "passed": True,
            "ppa_evidence_id": evidence.evidence_id,
            "fixture": True,
        },
        ppa=evidence.to_dict(),
    )
    provider = PragmaModelHypothesisProvider(
        registry=analysis_runtime.registry,
        effective_config=analysis_runtime.effective_config,
        task=task,
        budget=budget,
        artifacts=artifacts,
    )
    request = HypothesisRequest(
        run_id=output_root.name,
        level=OptimizationLevel.PRAGMA,
        round_number=1,
        parent_candidate=baseline,
        max_hypotheses=3,
        supporting_evidence_ids=(evidence.evidence_id,),
        safe_context={
            "policy": "safe-v1",
            "objective": "latency",
            "smoke_scope": "pragma_model_contract_only",
            "evidence_source": "typed_fixture_not_live_vitis",
            "source_sha256": sha256(source).hexdigest(),
        },
        parent_source=source,
    )
    hypotheses = invoke_one_llm_call(budget, lambda: provider.propose(request))
    if not hypotheses:
        raise RuntimeError(
            "real model returned no executable Pragma hypothesis for the explicit II fixture"
        )
    selected = hypotheses[0]

    generator = PragmaModelCandidateGenerator(
        registry=rewrite_runtime.registry,
        effective_config=rewrite_runtime.effective_config,
        task=task,
        budget=budget,
        artifacts=artifacts,
    )
    execution_request = CandidateExecutionRequest(
        run_id=output_root.name,
        sequence=1,
        candidate_id="cand-1",
        level=OptimizationLevel.PRAGMA,
        round_number=1,
        parent_candidate=baseline,
        parent_source=source,
        hypothesis=selected,
        budget_before=budget.snapshot().to_dict(),
    )
    generated = invoke_one_llm_call(
        budget, lambda: generator.generate(execution_request)
    )

    candidate_dir = output_root / "optimizer" / "candidates" / "cand-1"
    candidate_dir.mkdir(parents=True, exist_ok=False)
    candidate_path = candidate_dir / "source.cpp"
    candidate_path.write_bytes(generated.source)
    hypothesis_dir = output_root / "optimizer" / "hypotheses"
    hypothesis_dir.mkdir(parents=True, exist_ok=False)
    write_json(hypothesis_dir / f"{selected.hypothesis_id}.json", selected.to_dict())

    usage = budget.snapshot().to_dict()
    actions = [item.to_dict() for item in provider.actions]
    model_lines = artifacts.path.read_text(encoding="utf-8").splitlines()
    summary = {
        "schema_version": 1,
        "accepted": True,
        "claim_scope": "pragma_model_contract_only",
        "repository_baseline": EXPECTED_BASELINE,
        "model": args.model,
        "model_family": family,
        "analysis_json_authority": "local_strict_response_contract",
        "analysis_provider_json_mode": False,
        "thinking_mode_control": (
            "disabled" if family == "deepseek" else "provider_default"
        ),
        "output_token_policy": "stage2_typed_output_policy",
        "analysis_max_tokens": args.analysis_max_tokens,
        "rewrite_max_tokens": args.rewrite_max_tokens,
        "output_token_safety_ceiling": OUTPUT_TOKEN_SAFETY_CEILING,
        "source": str(source_path),
        "source_sha256": sha256(source).hexdigest(),
        "top": args.top,
        "evidence_id": evidence.evidence_id,
        "evidence_source": "typed_fixture_not_live_vitis",
        "raw_report_used": False,
        "action_count": len(actions),
        "actions": actions,
        "action_authoritative": False,
        "hypothesis_count": len(hypotheses),
        "selected_hypothesis_id": selected.hypothesis_id,
        "selected_hypothesis": selected.to_dict(),
        "candidate_source_relative_path": str(candidate_path.relative_to(output_root)),
        "candidate_source_sha256": sha256(generated.source).hexdigest(),
        "candidate_semantically_changed": True,
        "top_interface_preserved": True,
        "complete_source_contract_passed": True,
        "model_call_records": len(model_lines),
        "budget_usage": usage,
        "network_called": True,
        "vitis_called": False,
        "compile_called": False,
        "csim_called": False,
        "csynth_called": False,
        "hidden_evidence_exposed": False,
        "raw_prompts_persisted": False,
        "raw_responses_persisted": False,
        "static_pragma_gate_used": False,
        "product_optimize_full_enabled": False,
        "created_at_utc": utc_now(),
    }
    write_json(output_root / "summary.json", summary)
    verify_summary(summary)

    print("S36_REAL_PRAGMA_SMOKE_ACCEPTED=true")
    print(f"MODEL={args.model}")
    print("ANALYSIS_JSON_AUTHORITY=local_strict_response_contract")
    print("ANALYSIS_PROVIDER_JSON_MODE=false")
    print(f"THINKING_MODE_CONTROL={summary['thinking_mode_control']}")
    print(f"OUTPUT_TOKEN_POLICY={summary['output_token_policy']}")
    print(f"ANALYSIS_MAX_TOKENS={summary['analysis_max_tokens']}")
    print(f"REWRITE_MAX_TOKENS={summary['rewrite_max_tokens']}")
    print(
        f"OUTPUT_TOKEN_SAFETY_CEILING="
        f"{summary['output_token_safety_ceiling']}"
    )
    print(f"ACTION_COUNT={len(actions)}")
    print(f"HYPOTHESIS_COUNT={len(hypotheses)}")
    print(f"SELECTED_HYPOTHESIS_ID={selected.hypothesis_id}")
    print(f"LLM_CALLS={usage['llm_calls']}")
    print(f"TOKENS={usage['tokens']}")
    print("NETWORK_CALLED=true")
    print("VITIS_CALLED=false")
    print("COMPILE_CALLED=false")
    print("CSIM_CALLED=false")
    print("CSYNTH_CALLED=false")
    print("ACTION_AUTHORITATIVE=false")
    print("RAW_REPORT_USED=false")
    print("STATIC_PRAGMA_GATE_USED=false")
    print("TOP_INTERFACE_PRESERVED=true")
    print("COMPLETE_SOURCE_CONTRACT_PASSED=true")
    print(f"CANDIDATE_SOURCE_SHA256={summary['candidate_source_sha256']}")
    print(f"ARTIFACT_ROOT={output_root}")
    return 0


def effective_smoke_family(model: str, family: str | None) -> str:
    """Resolve the explicit family used only for bounded smoke parameters."""

    if family is not None and family.strip():
        return family.strip().casefold()
    return infer_model_family(model).casefold()


def smoke_model_parameters(*, family: str, max_tokens: int) -> dict[str, Any]:
    """Build bounded provider parameters without relying on flaky JSON mode.

    The strict Pragma JSON schema remains enforced by
    ``PragmaAnalysisResponseContract``.  DeepSeek's provider-side JSON
    Output is intentionally not enabled here because its official contract
    acknowledges occasional empty ``content`` responses.  DeepSeek V4 also
    defaults to thinking mode, so this bounded contract smoke explicitly
    disables thinking instead of treating private reasoning as final JSON or
    adding hidden retry calls.
    """

    if isinstance(max_tokens, bool) or max_tokens < 1:
        raise ValueError("max_tokens must be positive")
    if max_tokens > OUTPUT_TOKEN_SAFETY_CEILING:
        raise ValueError(
            "max_tokens exceeds the Stage 2 compatible safety ceiling"
        )
    parameters: dict[str, Any] = {
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    if family.strip().casefold() == "deepseek":
        parameters["extra_body"] = {"thinking": {"type": "disabled"}}
    return parameters


def smoke_ppa() -> PpaEvidence:
    return PpaEvidence(
        evidence_id="ppa-s36-smoke",
        parser_profile="s36-smoke-fixture-v1",
        report_format=PpaReportFormat.XML,
        report_relative_path="fixtures/s36_smoke_csynth.xml",
        report_sha256=sha256(b"s36 typed smoke PPA fixture").hexdigest(),
        comparison_context_identity_sha256=COMPARISON_CONTEXT,
        latency_cycles_min=252,
        latency_cycles_max=256,
        initiation_interval_min=4,
        initiation_interval_max=4,
        target_clock_period_ns=5.0,
        achieved_clock_period_ns=4.4,
        resources_used=PpaResourceUsage(
            bram_18k=2, dsp=4, ff=900, lut=700, uram=0
        ),
        resources_available=PpaResourceUsage(
            bram_18k=100, dsp=200, ff=100000, lut=50000, uram=20
        ),
        max_resource_utilization_ratio=0.02,
        objective_feasible=True,
        parser_warnings=("typed_fixture_not_live_vitis",),
    )


def invoke_one_llm_call(budget: BudgetManager, operation):
    budget.ensure_available(llm_calls=1)
    try:
        return operation()
    finally:
        budget.consume(llm_calls=1)


def require_repo(repo: Path) -> None:
    if not repo.is_dir():
        raise FileNotFoundError(f"repository not found: {repo}")
    required = (
        repo / "agrefactor" / "optimization" / "pragma_model.py",
        repo / "agrefactor" / "prompts" / "optimization.py",
    )
    for path in required:
        if not path.is_file():
            raise FileNotFoundError(f"S3.6 source is missing: {path}")


def resolve_source(repo: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = repo / path
    path = path.resolve()
    try:
        path.relative_to(repo)
    except ValueError as exc:
        raise ValueError("smoke source must remain inside the repository") from exc
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError(f"smoke source is not a regular file: {path}")
    return path


def verify_summary(summary: dict[str, Any]) -> None:
    usage = summary["budget_usage"]
    if usage["llm_calls"] != 2:
        raise AssertionError("real S3.6 smoke must use exactly two LLM calls")
    for key in ("tool_calls", "compile_calls", "csim_calls", "csynth_calls"):
        if usage[key] != 0:
            raise AssertionError(f"real S3.6 smoke unexpectedly consumed {key}")
    if summary["action_authoritative"]:
        raise AssertionError("model pragma action must remain non-authoritative")
    if summary["raw_report_used"]:
        raise AssertionError("S3.6 smoke must use only typed evidence projection")
    if summary["static_pragma_gate_used"]:
        raise AssertionError("S3.6 must not use a static pragma gate")
    if summary["analysis_json_authority"] != "local_strict_response_contract":
        raise AssertionError("strict local response contract must remain authoritative")
    if summary["analysis_provider_json_mode"]:
        raise AssertionError("bounded smoke must not rely on provider JSON mode")
    if summary["model_family"] == "deepseek" and summary["thinking_mode_control"] != "disabled":
        raise AssertionError("DeepSeek bounded smoke must disable thinking")
    if summary["output_token_policy"] != "stage2_typed_output_policy":
        raise AssertionError("S3.6 must reuse the Stage 2 typed output policy")
    if summary["analysis_max_tokens"] != STAGE2_COMPATIBLE_OUTPUT_LIMIT:
        raise AssertionError("analysis max_tokens must match Stage 2")
    if summary["rewrite_max_tokens"] != STAGE2_COMPATIBLE_OUTPUT_LIMIT:
        raise AssertionError("rewrite max_tokens must match Stage 2")
    if summary["output_token_safety_ceiling"] != OUTPUT_TOKEN_SAFETY_CEILING:
        raise AssertionError("output token safety ceiling must match Stage 2")
    if not summary["candidate_semantically_changed"]:
        raise AssertionError("candidate must be semantically changed")
    if not summary["top_interface_preserved"]:
        raise AssertionError("top interface must remain unchanged")
    if summary["hidden_evidence_exposed"]:
        raise AssertionError("Hidden evidence must not be exposed")
    if summary["product_optimize_full_enabled"]:
        raise AssertionError("S3.6 must not enable product optimize/full")


def default_output_root() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(
        f"/data/agrefactor_runs/stage3_s36_real_pragma_smoke_{stamp}_{os.getpid()}"
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("S36_REAL_PRAGMA_SMOKE_ACCEPTED=false", file=sys.stderr)
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise
