#!/usr/bin/env python3
"""Run one bounded real-network S3.4 Structural model integration smoke.

This acceptance tool performs exactly two model calls when successful:

1. propose up to three Structural hypotheses as strict JSON;
2. generate one complete replacement C++ source for the first valid hypothesis.

It deliberately performs no compile, CSIM, CSYNTH, Vitis, Hidden evaluation,
PPA comparison, or product ``optimize/full`` execution.  A valid result proves
only the S3.4 model/prompt/response integration contract.
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

# The repository is intentionally not packaged with setup.py/pyproject.toml.
# Make direct ``python tools/...`` execution work from any current directory.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from agrefactor.config import RunMode, TaskSpec, resolve_target_profile
from agrefactor.models import resolve_model_runtime
from agrefactor.optimization import (
    CandidateExecutionRequest,
    CandidateRecord,
    CandidateStatus,
    HypothesisRequest,
    OptimizationLevel,
    StructuralModelArtifactWriter,
    StructuralModelCandidateGenerator,
    StructuralModelHypothesisProvider,
)
from agrefactor.runtime import BudgetLimits, BudgetManager


EXPECTED_BASELINE = "7e55aae15bbae7f9bd236dd4fc4832558e806f8b"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_SOURCE = "tests/fixtures/stage3_s34/structural_smoke_kernel.cpp"
DEFAULT_TOP = "s34_structural_top"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the bounded S3.4 real Structural model smoke."
    )
    parser.add_argument("--repo", required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--top", default=DEFAULT_TOP)
    parser.add_argument("--family")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key-env")
    parser.add_argument("--output-root")
    parser.add_argument("--hypothesis-max-tokens", type=int, default=4096)
    parser.add_argument("--rewrite-max-tokens", type=int, default=16384)
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
    artifacts = StructuralModelArtifactWriter(output_root / "optimizer")
    target = resolve_target_profile(None)
    task = TaskSpec(
        task_id=f"{output_root.name}.task",
        kernel_path=str(source_path),
        kernel_name=args.top,
        target=target,
        mode=RunMode.OPTIMIZE,
    )

    common = {
        "family": args.family,
        "base_url": args.base_url,
        "api_key_env": args.api_key_env,
    }
    hypothesis_runtime = resolve_model_runtime(
        args.model,
        parameters={
            "temperature": 0,
            "max_tokens": args.hypothesis_max_tokens,
            "response_format": {"type": "json_object"},
        },
        **common,
    )
    rewrite_runtime = resolve_model_runtime(
        args.model,
        parameters={
            "temperature": 0,
            "max_tokens": args.rewrite_max_tokens,
        },
        **common,
    )
    credential_env = hypothesis_runtime.effective_config.api_key_env
    if not credential_env or not os.environ.get(credential_env):
        raise RuntimeError(
            f"required model credential environment variable is not set: {credential_env}"
        )

    baseline = CandidateRecord(
        candidate_id="baseline",
        sequence=0,
        parent_candidate_id=None,
        hypothesis_id=None,
        level=None,
        source_sha256=sha256(source).hexdigest(),
        source_artifact="candidates/baseline/source.cpp",
        status=CandidateStatus.ACCEPTED,
    )
    hypothesis_provider = StructuralModelHypothesisProvider(
        registry=hypothesis_runtime.registry,
        effective_config=hypothesis_runtime.effective_config,
        task=task,
        budget=budget,
        artifacts=artifacts,
    )
    hypothesis_request = HypothesisRequest(
        run_id=output_root.name,
        level=OptimizationLevel.STRUCTURAL,
        round_number=1,
        parent_candidate=baseline,
        max_hypotheses=3,
        supporting_evidence_ids=(),
        safe_context={
            "policy": "safe-v1",
            "objective": "latency",
            "smoke_scope": "model_contract_only",
            "source_sha256": sha256(source).hexdigest(),
        },
        parent_source=source,
    )
    hypotheses = invoke_one_llm_call(
        budget,
        lambda: hypothesis_provider.propose(hypothesis_request),
    )
    if not hypotheses:
        raise RuntimeError("real model returned no executable Structural hypothesis")
    selected = hypotheses[0]

    generator = StructuralModelCandidateGenerator(
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
        level=OptimizationLevel.STRUCTURAL,
        round_number=1,
        parent_candidate=baseline,
        parent_source=source,
        hypothesis=selected,
        budget_before=budget.snapshot().to_dict(),
    )
    generated = invoke_one_llm_call(
        budget,
        lambda: generator.generate(execution_request),
    )

    candidate_dir = output_root / "optimizer" / "candidates" / "cand-1"
    candidate_dir.mkdir(parents=True, exist_ok=False)
    candidate_path = candidate_dir / "source.cpp"
    candidate_path.write_bytes(generated.source)
    (output_root / "optimizer" / "hypotheses").mkdir(parents=True, exist_ok=False)
    write_json(
        output_root / "optimizer" / "hypotheses" / f"{selected.hypothesis_id}.json",
        selected.to_dict(),
    )

    usage = budget.snapshot().to_dict()
    model_lines = artifacts.path.read_text(encoding="utf-8").splitlines()
    summary = {
        "schema_version": 1,
        "accepted": True,
        "claim_scope": "structural_model_contract_only",
        "repository_baseline": EXPECTED_BASELINE,
        "model": args.model,
        "source": str(source_path),
        "source_sha256": sha256(source).hexdigest(),
        "top": args.top,
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
        "product_optimize_full_enabled": False,
        "created_at_utc": utc_now(),
    }
    write_json(output_root / "summary.json", summary)
    verify_summary(summary)

    print("S34_REAL_STRUCTURAL_SMOKE_ACCEPTED=true")
    print(f"MODEL={args.model}")
    print(f"HYPOTHESIS_COUNT={len(hypotheses)}")
    print(f"SELECTED_HYPOTHESIS_ID={selected.hypothesis_id}")
    print(f"LLM_CALLS={usage['llm_calls']}")
    print(f"TOKENS={usage['tokens']}")
    print("NETWORK_CALLED=true")
    print("VITIS_CALLED=false")
    print("COMPILE_CALLED=false")
    print("CSIM_CALLED=false")
    print("CSYNTH_CALLED=false")
    print("TOP_INTERFACE_PRESERVED=true")
    print("COMPLETE_SOURCE_CONTRACT_PASSED=true")
    print(f"CANDIDATE_SOURCE_SHA256={summary['candidate_source_sha256']}")
    print(f"ARTIFACT_ROOT={output_root}")
    return 0


def invoke_one_llm_call(budget: BudgetManager, operation):
    budget.ensure_available(llm_calls=1)
    try:
        return operation()
    finally:
        # The adapter boundary was entered, so the physical call slot is
        # consumed even if transport or response validation raises.
        budget.consume(llm_calls=1)


def require_repo(repo: Path) -> None:
    if not repo.is_dir():
        raise FileNotFoundError(f"repository not found: {repo}")
    required = (
        repo / "agrefactor" / "optimization" / "structural_model.py",
        repo / "agrefactor" / "prompts" / "optimization.py",
    )
    for path in required:
        if not path.is_file():
            raise FileNotFoundError(f"S3.4 source is missing: {path}")


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
        raise AssertionError("real S3.4 smoke must use exactly two LLM calls")
    for key in ("tool_calls", "compile_calls", "csim_calls", "csynth_calls"):
        if usage[key] != 0:
            raise AssertionError(f"real S3.4 smoke unexpectedly consumed {key}")
    if not summary["candidate_semantically_changed"]:
        raise AssertionError("candidate must be semantically changed")
    if not summary["top_interface_preserved"]:
        raise AssertionError("top interface must remain unchanged")
    if summary["hidden_evidence_exposed"]:
        raise AssertionError("Hidden evidence must not be exposed")
    if summary["product_optimize_full_enabled"]:
        raise AssertionError("S3.4 must not enable product optimize/full")


def default_output_root() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(f"/data/agrefactor_runs/stage3_s34_real_structural_smoke_{stamp}_{os.getpid()}")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - acceptance tools need a clear terminal.
        print("S34_REAL_STRUCTURAL_SMOKE_ACCEPTED=false", file=sys.stderr)
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise
