#!/usr/bin/env python3
"""Freshly validate one exact, previously authorized R5 Candidate."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence
import zipfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from agrefactor.config import (
    EvaluationSplit,
    RunMode,
    TaskSpec,
    TestSuiteSpec,
    resolve_target_profile,
)
from agrefactor.recovery.r5_authorized_revalidation import (
    R5AuthorizedRevalidationBundle,
    verify_authorized_revalidation_plan,
)
from agrefactor.recovery.r5_historical_candidate import (
    canonical_sha256,
    file_sha256,
)
from agrefactor.runtime import BudgetLimits, BudgetManager, RunContext, TraceRecorder
from agrefactor.runtime.candidate_repair_integration import (
    CandidateValidationPlanRequest,
    LocalCandidateValidationHandlerFactory,
)
from agrefactor.runtime.validation_orchestrator import ValidationOrchestrator


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PRIVATE_TAGS = ("<think", "</think", "<reasoning", "</reasoning")
_RAW_FIELDS = frozenset(
    {
        "private_reasoning",
        "provider_response",
        "raw_exception",
        "raw_message",
        "raw_provider_response",
        "reasoning_content",
        "response_content",
        "secret_value",
    }
)


class R5AuthorizedRevalidationExecutionError(RuntimeError):
    """Raised when the frozen zero-Provider execution cannot proceed."""


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise R5AuthorizedRevalidationExecutionError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise R5AuthorizedRevalidationExecutionError("JSON root is invalid")
    return value


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise R5AuthorizedRevalidationExecutionError("git command failed")
    return completed.stdout.strip()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _assert_safe(value: Any) -> None:
    def visit(item: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(item, Mapping):
            for raw_key, child in item.items():
                key = str(raw_key)
                lowered = key.casefold()
                if lowered in _RAW_FIELDS:
                    raise R5AuthorizedRevalidationExecutionError(
                        "raw private field entered evidence: " + ".".join((*path, key))
                    )
                if lowered.endswith("_persisted") and child not in (False, "false"):
                    raise R5AuthorizedRevalidationExecutionError(
                        "unsafe persistence flag entered evidence: "
                        + ".".join((*path, key))
                    )
                visit(child, (*path, key))
        elif isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                visit(child, (*path, str(index)))
        elif isinstance(item, str) and any(
            tag in item.casefold() for tag in _PRIVATE_TAGS
        ):
            raise R5AuthorizedRevalidationExecutionError(
                "private reasoning tag entered evidence"
            )

    visit(value)


def _seal(root: Path) -> tuple[str, str]:
    selected = [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.name
        not in {"evidence.zip", "evidence.zip.sha256", "evidence_content_manifest.json"}
        and not path.is_symlink()
    ]
    content = {
        path.relative_to(root).as_posix(): file_sha256(path) for path in selected
    }
    content_path = root / "evidence_content_manifest.json"
    _atomic_json(content_path, {"schema_version": 1, "files": content})
    archive = root / "evidence.zip"
    with zipfile.ZipFile(
        archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as evidence_zip:
        for path in (*selected, content_path):
            relative = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(relative, (2026, 9, 19, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            evidence_zip.writestr(info, path.read_bytes())
    archive_sha = file_sha256(archive)
    (root / "evidence.zip.sha256").write_text(
        f"{archive_sha}  evidence.zip\n", encoding="utf-8"
    )
    return archive_sha, file_sha256(content_path)


def _suite_ids(bundle: R5AuthorizedRevalidationBundle) -> tuple[str, str]:
    stem = re.sub(r"[^a-z0-9]+", "-", bundle.case_id.casefold()).strip("-")
    return (f"public-{stem}-revalidation", f"hidden-{stem}-revalidation")


def _physical_vitis_launches(usage: Mapping[str, Any]) -> int:
    """Count physical Vitis processes, excluding the Hidden host csim."""

    public_native_csim = min(int(usage.get("csim_calls", 0)), 1)
    return (
        public_native_csim
        + int(usage.get("csynth_calls", 0))
        + int(usage.get("cosim_calls", 0))
    )


def _task(repository: Path, bundle: R5AuthorizedRevalidationBundle, run_id: str) -> TaskSpec:
    public_id, hidden_id = _suite_ids(bundle)
    public_path = repository / bundle.paths["public_test"]
    hidden_path = repository / bundle.paths["hidden_test"]
    return TaskSpec(
        task_id=run_id + ".formal",
        kernel_path=str(repository / bundle.paths["reference"]),
        kernel_name=bundle.candidate_top,
        target=resolve_target_profile("vitis-2023.2-default"),
        mode=RunMode.REFACTOR,
        testbench_path=str(public_path),
        test_suites=(
            TestSuiteSpec(
                suite_id=public_id,
                split=EvaluationSplit.PUBLIC,
                suite_version="r5-authorized-revalidation-v1",
                case_count=1,
                testbench_path=str(public_path),
                runtime_contract=bundle.public_runtime_contract,
            ),
            TestSuiteSpec(
                suite_id=hidden_id,
                split=EvaluationSplit.HIDDEN,
                suite_version="r5-authorized-revalidation-v1",
                case_count=3,
                testbench_path=str(hidden_path),
            ),
        ),
    )


def _validate_audit(
    audit: Mapping[str, Any],
    *,
    audit_path: Path,
    plan_path: Path,
    state_path: Path,
    repository_head: str,
) -> None:
    unsigned = {key: value for key, value in audit.items() if key != "audit_sha256"}
    if (
        audit.get("status") != "ready_for_authorized_candidate_revalidation"
        or audit.get("repository_head") != repository_head
        or audit.get("plan_file_sha256") != file_sha256(plan_path)
        or audit.get("state_file_sha256") != file_sha256(state_path)
        or audit.get("audit_sha256") != canonical_sha256(unsigned)
        or audit.get("provider_calls") != 0
        or audit.get("vitis_launches") != 0
        or audit.get("provider_call_upper_bound") != 0
        or audit.get("vitis_launch_upper_bound") != 3
        or audit.get("original_authorization_verified") is not True
        or audit.get("sealed_candidate_verified") is not True
        or audit.get("prior_configuration_failure_verified") is not True
        or audit.get("prior_revalidation_budget_failure_verified") is not True
        or audit.get("future_files_read") is not False
        or audit.get("future_outcomes_observed") is not False
        or audit.get("critical_finding_count") != 0
        or audit.get("findings") != []
        or _SHA256.fullmatch(file_sha256(audit_path)) is None
    ):
        raise R5AuthorizedRevalidationExecutionError(
            "zero-call protocol audit is incompatible"
        )


def execute(
    *,
    repository: Path,
    plan_path: Path,
    audit_path: Path,
    state_path: Path,
    output: Path,
    run_id: str,
) -> dict[str, Any]:
    repository = repository.expanduser().resolve()
    plan_path = plan_path.expanduser().resolve()
    audit_path = audit_path.expanduser().resolve()
    state_path = state_path.expanduser().resolve()
    output = output.expanduser().resolve()
    if output.exists():
        raise R5AuthorizedRevalidationExecutionError(
            "output directory must not already exist"
        )
    if _git(repository, "status", "--porcelain", "--untracked-files=all"):
        raise R5AuthorizedRevalidationExecutionError("execution requires a clean branch")
    head = _git(repository, "rev-parse", "HEAD")
    plan = _load(plan_path)
    state = _load(state_path)
    audit = _load(audit_path)
    bundle = verify_authorized_revalidation_plan(repository, plan)
    _validate_audit(
        audit,
        audit_path=audit_path,
        plan_path=plan_path,
        state_path=state_path,
        repository_head=head,
    )
    if (
        state.get("R5_CONSUMED_PROVIDER_CALLS") != 104
        or state.get("R5_CONSUMED_VITIS_LAUNCHES") != 59
        or state.get("R5_ACCEPTED") is not False
        or state.get("R6_STARTED") is not False
    ):
        raise R5AuthorizedRevalidationExecutionError("roadmap state drifted")
    output.mkdir(parents=True)
    task = _task(repository, bundle, run_id)
    public_id, hidden_id = _suite_ids(bundle)
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "repository_head": head,
        "plan_file_sha256": file_sha256(plan_path),
        "plan_sha256": bundle.plan_sha256,
        "protocol_audit_file_sha256": file_sha256(audit_path),
        "protocol_audit_sha256": audit["audit_sha256"],
        "state_file_sha256": file_sha256(state_path),
        "case_identity": bundle.identity(),
        "target_profile": task.target.to_dict(),
        "provider_call_upper_bound": 0,
        "vitis_launch_upper_bound": 3,
        "fresh_validation": True,
        "original_episode_mutated": False,
        "future_files_read": False,
        "future_outcomes_observed": False,
        "r5_accepted": False,
        "r6_started": False,
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    _atomic_json(output / "revalidation_manifest.json", manifest)
    budget = BudgetManager(
        BudgetLimits(
            max_llm_calls=0,
            max_tool_calls=24,
            max_compile_calls=16,
            # Public native Vitis csim and Hidden host differential csim share
            # the generic csim counter even though only the former launches Vitis.
            max_csim_calls=2,
            max_csynth_calls=1,
            max_cosim_calls=1,
            max_tokens=0,
            max_cost_usd=0,
            max_wall_time_s=3600,
        )
    )
    context = RunContext(
        run_id=run_id,
        task=task,
        budget=budget,
        trace=TraceRecorder(
            run_id, task_id=task.task_id, output_path=output / "trace.jsonl"
        ),
    )
    plan_request = CandidateValidationPlanRequest(
        task=task,
        candidate_code=bundle.candidate_code,
        original_code=bundle.reference_code,
        preflight_testbench_code=bundle.public_test_code,
        suite_testbench_codes={
            public_id: bundle.public_test_code,
            hidden_id: bundle.hidden_test_code,
        },
        attempt=0,
        validation_id=run_id + ".revalidation",
        reference_top_function=bundle.reference_top,
        candidate_top_function=bundle.candidate_top,
    )
    handlers = LocalCandidateValidationHandlerFactory(
        output / "work",
        csynth_timelimit=1200,
        csim_timelimit=1200,
        cosim_timelimit=1200,
        cosim_policy="required",
    ).build(plan_request)
    started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    outcome = ValidationOrchestrator(handlers).run_detailed(
        context, validation_id=run_id + ".revalidation"
    )
    completed = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    usage = budget.snapshot().to_dict()
    vitis_launches = _physical_vitis_launches(usage)
    result = {
        "schema_version": 1,
        "status": "ready_for_independent_audit",
        "run_id": run_id,
        "manifest_sha256": manifest["manifest_sha256"],
        "started_at": started,
        "completed_at": completed,
        "candidate_before_sha256": bundle.candidate_before_sha256,
        "candidate_after_sha256": bundle.candidate_after_sha256,
        "authorization_id": bundle.authorization["authorization_id"],
        "source_episode_id": bundle.source_episode["episode_id"],
        "source_episode_sha256": bundle.source_episode_sha256,
        "fresh_validation": True,
        "validation_accepted": outcome.result.accepted,
        "validation": outcome.to_dict(),
        "budget_usage": usage,
        "provider_calls": int(usage["llm_calls"]),
        "vitis_launches": vitis_launches,
        "original_episode_mutated": False,
        "raw_provider_response_persisted": False,
        "private_reasoning_persisted": False,
        "future_files_read": False,
        "future_outcomes_observed": False,
        "self_declared_verified_positive": False,
        "trusted_revision_created": False,
        "r5_accepted": False,
        "r6_started": False,
    }
    if result["provider_calls"] != 0 or result["vitis_launches"] > 3:
        raise R5AuthorizedRevalidationExecutionError("frozen budget was exceeded")
    result["result_sha256"] = canonical_sha256(result)
    _assert_safe(result)
    _atomic_json(output / "revalidation_result.json", result)
    archive_sha, content_sha = _seal(output)
    print("R5_AUTHORIZED_REVALIDATION_STATUS=ready_for_independent_audit")
    print("VALIDATION_ACCEPTED=" + str(result["validation_accepted"]).lower())
    print("PROVIDER_CALLS=0")
    print(f"VITIS_LAUNCHES={vitis_launches}")
    print("EVIDENCE_ARCHIVE_SHA256=" + archive_sha)
    print("EVIDENCE_CONTENT_MANIFEST_SHA256=" + content_sha)
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--protocol-audit", type=Path, required=True)
    parser.add_argument(
        "--state", type=Path, default=Path("docs/roadmap/V2_3_STATE.json")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    execute(
        repository=args.repo,
        plan_path=args.plan,
        audit_path=args.protocol_audit,
        state_path=args.state,
        output=args.output,
        run_id=args.run_id,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
