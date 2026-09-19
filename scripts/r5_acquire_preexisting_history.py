#!/usr/bin/env python3
"""Acquire one R5 history episode from a frozen pre-existing Candidate."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
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
from agrefactor.models import CandidateModelAdapter, resolve_model_runtime
from agrefactor.recovery import (
    CalibrationCertificate,
    ProviderBackedShadowDiagnosticAdvisor,
    R4CanaryManifest,
    ShadowReserve,
)
from agrefactor.recovery.r4_provenance import canonical_artifact_sha256
from agrefactor.recovery.r5_historical_candidate import (
    R5HistoricalCandidateBundle,
    canonical_sha256,
    file_sha256,
    text_sha256,
    verify_historical_candidate_plan,
)
from agrefactor.runtime import BudgetLimits, BudgetManager, RunContext, TraceRecorder
from agrefactor.runtime.candidate_repair_integration import (
    CandidateRepairOrchestrationRequest,
    CandidateRepairValidationOrchestrator,
    LocalCandidateValidationHandlerFactory,
)
from agrefactor.runtime.r4_integration import CandidateModelR4MutationAdapter
from agrefactor.runtime.r5_integration import (
    ExistingOrchestratorR5Integration,
    R5CandidatePromptFactory,
    R5IntegrationConfig,
)
from agrefactor.runtime.r5_profile import resolve_r5_profile


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


class PreexistingHistoryAcquisitionError(RuntimeError):
    """Raised when real history acquisition cannot proceed safely."""


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise PreexistingHistoryAcquisitionError(
            f"invalid JSON evidence: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise PreexistingHistoryAcquisitionError(
            f"JSON root is not an object: {path}"
        )
    return value


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise PreexistingHistoryAcquisitionError(
            "git command failed: " + " ".join(args)
        )
    return completed.stdout.strip()


def _load_certificate(bundle: Mapping[str, Any]) -> CalibrationCertificate:
    raw = bundle.get("certificate")
    if not isinstance(raw, Mapping):
        raise PreexistingHistoryAcquisitionError(
            "calibration bundle lacks a certificate"
        )
    value = dict(raw)
    value.pop("schema_version", None)
    certificate_id = value.pop("certificate_id", "")
    certificate = CalibrationCertificate(
        certificate_id=str(certificate_id),
        **value,
    )
    if not certificate.accepted:
        raise PreexistingHistoryAcquisitionError(
            "calibration certificate is not accepted"
        )
    if certificate.failure_class_scope != ("unsupported_construct",):
        raise PreexistingHistoryAcquisitionError(
            "calibration certificate has the wrong failure-class scope"
        )
    return certificate


def _validate_protocol_audit(
    *,
    audit: Mapping[str, Any],
    audit_path: Path,
    plan_path: Path,
    state_path: Path,
    repository_head: str,
) -> None:
    unsigned = {key: value for key, value in audit.items() if key != "audit_sha256"}
    if (
        audit.get("status") != "ready_for_preexisting_history_acquisition"
        or audit.get("repository_head") != repository_head
        or audit.get("plan_file_sha256") != file_sha256(plan_path)
        or audit.get("state_file_sha256") != file_sha256(state_path)
        or audit.get("audit_sha256") != canonical_sha256(unsigned)
        or audit.get("provider_calls") != 0
        or audit.get("vitis_launches") != 0
        or audit.get("provider_call_upper_bound") != 2
        or audit.get("vitis_launch_upper_bound") != 6
        or audit.get("source_independence_verified") is not True
        or audit.get("future_files_read") is not False
        or audit.get("future_outcomes_observed") is not False
        or audit.get("critical_finding_count") != 0
        or audit.get("findings") != []
    ):
        raise PreexistingHistoryAcquisitionError(
            "zero-call protocol audit is incompatible"
        )
    if not re.fullmatch(r"[0-9a-f]{64}", file_sha256(audit_path)):
        raise PreexistingHistoryAcquisitionError("protocol audit file hash is invalid")


def load_preflight(
    *,
    repository: Path,
    plan_path: Path,
    audit_path: Path,
    state_path: Path,
) -> dict[str, Any]:
    repository = repository.expanduser().resolve()
    plan_path = plan_path.expanduser().resolve()
    audit_path = audit_path.expanduser().resolve()
    state_path = state_path.expanduser().resolve()
    head = _git(repository, "rev-parse", "HEAD")
    branch = _git(repository, "branch", "--show-current")
    status = _git(repository, "status", "--porcelain", "--untracked-files=all")
    if branch != "research-roadmap-v2.3" or status:
        raise PreexistingHistoryAcquisitionError(
            "history acquisition requires the clean R5 branch"
        )
    state = _load(state_path)
    plan = _load(plan_path)
    plan_budget = plan.get("budget")
    if not isinstance(plan_budget, Mapping):
        raise PreexistingHistoryAcquisitionError("plan budget is missing")
    if (
        state.get("R4_ACCEPTED") is not True
        or state.get("R5_STARTED") is not True
        or state.get("R5_ACCEPTED") is not False
        or state.get("R6_STARTED") is not False
        or state.get("R5_REAL_CAMPAIGN_ALLOWED") is not False
        or state.get("R5_CONSUMED_PROVIDER_CALLS")
        != plan_budget.get("provider_calls_before")
        or state.get("R5_CONSUMED_VITIS_LAUNCHES")
        != plan_budget.get("vitis_launches_before")
        or state.get("R5_PROVIDER_CALL_HARD_CAP") != 500
        or state.get("R5_VITIS_LAUNCH_HARD_CAP") != 500
        or state.get("R5_PREDECESSOR_LIFECYCLE") != "Provisional"
    ):
        raise PreexistingHistoryAcquisitionError(
            "roadmap state does not permit pre-existing history acquisition"
        )
    candidate = verify_historical_candidate_plan(repository, plan)
    audit = _load(audit_path)
    _validate_protocol_audit(
        audit=audit,
        audit_path=audit_path,
        plan_path=plan_path,
        state_path=state_path,
        repository_head=head,
    )
    calibration_path = Path(str(plan["calibration_bundle"]))
    if file_sha256(calibration_path) != plan.get("calibration_bundle_file_sha256"):
        raise PreexistingHistoryAcquisitionError(
            "calibration bundle hash mismatch"
        )
    calibration_bundle = _load(calibration_path)
    certificate = _load_certificate(calibration_bundle)
    if certificate.certificate_id != plan.get("calibration_certificate_id"):
        raise PreexistingHistoryAcquisitionError(
            "calibration certificate identity mismatch"
        )
    runtime = calibration_bundle.get("model_runtime")
    if not isinstance(runtime, Mapping) or not isinstance(
        runtime.get("request_parameters"), Mapping
    ):
        raise PreexistingHistoryAcquisitionError(
            "calibration model runtime is incomplete"
        )
    api_key_env = runtime.get("api_key_env")
    if not isinstance(api_key_env, str) or not os.environ.get(api_key_env):
        raise PreexistingHistoryAcquisitionError(
            "selected Provider credential is missing"
        )
    return {
        "repository": repository,
        "repository_head": head,
        "state": state,
        "state_path": state_path,
        "plan": plan,
        "plan_path": plan_path,
        "audit": audit,
        "audit_path": audit_path,
        "candidate": candidate,
        "calibration_bundle": calibration_bundle,
        "calibration_path": calibration_path,
        "certificate": certificate,
        "model_runtime": dict(runtime),
        "budget": {
            key: int(plan_budget[key])
            for key in (
                "provider_calls_before",
                "vitis_launches_before",
                "provider_call_upper_bound",
                "vitis_launch_upper_bound",
            )
        },
    }


def _target_fingerprints(target: Mapping[str, Any]) -> tuple[str, str]:
    target_value = {
        key: target.get(key)
        for key in ("name", "device", "clock_period_ns", "parser_profile")
        if target.get(key) is not None
    }
    toolchain_value = {
        key: target.get(key)
        for key in ("toolchain", "toolchain_version")
        if target.get(key) is not None
    }
    return (
        canonical_artifact_sha256(target_value),
        canonical_artifact_sha256(toolchain_value),
    )


def build_acquisition_manifest(
    preflight: Mapping[str, Any],
    *,
    run_id: str,
) -> dict[str, Any]:
    candidate: R5HistoricalCandidateBundle = preflight["candidate"]
    runtime = preflight["model_runtime"]
    budget = preflight.get("budget")
    if not isinstance(budget, Mapping):
        budget = {
            "provider_calls_before": 93,
            "vitis_launches_before": 33,
            "provider_call_upper_bound": 2,
            "vitis_launch_upper_bound": 6,
        }
    target = resolve_target_profile("vitis-2023.2-default").to_dict()
    target_sha, toolchain_sha = _target_fingerprints(target)
    prompt_contract = {
        "factory": "R5CandidatePromptFactory",
        "arm": "A2",
        "memory_mode": "none",
        "editable_artifact": "candidate_kernel",
        "success_authority": "formal_validation_and_independent_file_audit",
    }
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "manifest_id": str(
            preflight.get(
                "manifest_id",
                "v2.3-r5-preexisting-history-acquisition-v1",
            )
        ),
        "status": "frozen_before_real_execution",
        "run_id": run_id,
        "repository_head": preflight["repository_head"],
        "repository_branch": "research-roadmap-v2.3",
        "plan_file_sha256": file_sha256(preflight["plan_path"]),
        "plan_sha256": candidate.plan_sha256,
        "protocol_audit_file_sha256": file_sha256(preflight["audit_path"]),
        "protocol_audit_sha256": preflight["audit"]["audit_sha256"],
        "case_identity": candidate.to_identity(),
        "calibration_bundle_file_sha256": file_sha256(
            preflight["calibration_path"]
        ),
        "calibration_certificate_id": preflight["certificate"].certificate_id,
        "model_runtime": {
            key: runtime.get(key)
            for key in (
                "provider",
                "model",
                "family",
                "base_url",
                "api_key_env",
                "request_parameters",
            )
        },
        "target_profile": target,
        "target_identity": target_sha,
        "toolchain_identity": toolchain_sha,
        "prompt_contract": prompt_contract,
        "prompt_contract_sha256": canonical_artifact_sha256(prompt_contract),
        "arm": "A2",
        "authorization_mode": "advisor_only",
        "memory_mode": "none",
        "maximum_candidate_mutations": 1,
        "provider_calls_before": int(budget["provider_calls_before"]),
        "vitis_launches_before": int(budget["vitis_launches_before"]),
        "provider_call_upper_bound": int(budget["provider_call_upper_bound"]),
        "vitis_launch_upper_bound": int(budget["vitis_launch_upper_bound"]),
        "future_holdout_case_ids": list(
            preflight["plan"]["future_holdout_case_ids"]
        ),
        "future_files_read": False,
        "future_outcomes_observed": False,
        "r5_accepted": False,
        "r6_started": False,
    }
    continuation = preflight.get("continuation")
    if isinstance(continuation, Mapping):
        manifest["continuation"] = dict(continuation)
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    return manifest


def _task(
    *,
    repository: Path,
    candidate: R5HistoricalCandidateBundle,
    run_id: str,
) -> TaskSpec:
    public_path = repository / candidate.paths["public_test"]
    hidden_path = repository / candidate.paths["hidden_test"]
    public_suite, hidden_suite = _suite_ids(candidate)
    return TaskSpec(
        task_id=run_id + ".formal",
        kernel_path=str(repository / candidate.paths["reference"]),
        kernel_name=candidate.candidate_top,
        target=resolve_target_profile("vitis-2023.2-default"),
        mode=RunMode.REFACTOR,
        testbench_path=str(public_path),
        test_suites=(
            TestSuiteSpec(
                suite_id=public_suite,
                split=EvaluationSplit.PUBLIC,
                suite_version="r5-preexisting-history-v1",
                case_count=1,
                testbench_path=str(public_path),
                runtime_contract={
                    "schema_version": 1,
                    "kind": "public_differential_self_check_v1",
                    "candidate_mismatch_returncodes": [1],
                },
            ),
            TestSuiteSpec(
                suite_id=hidden_suite,
                split=EvaluationSplit.HIDDEN,
                suite_version="r5-preexisting-history-v1",
                case_count=3,
                testbench_path=str(hidden_path),
            ),
        ),
    )


def _suite_ids(candidate: R5HistoricalCandidateBundle) -> tuple[str, str]:
    stem = re.sub(r"[^a-z0-9]+", "-", candidate.case_id.casefold()).strip("-")
    if not stem:
        raise PreexistingHistoryAcquisitionError("case ID cannot form suite IDs")
    return (f"public-{stem}-history", f"hidden-{stem}-history")


def _assert_safe(value: Any, *, secret: str | None) -> None:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if secret and secret in serialized:
        raise PreexistingHistoryAcquisitionError(
            "provider credential entered persisted evidence"
        )

    def visit(item: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(item, Mapping):
            for raw_key, child in item.items():
                key = str(raw_key)
                lowered = key.casefold()
                if lowered in _RAW_FIELDS:
                    raise PreexistingHistoryAcquisitionError(
                        "raw private field entered evidence: " + ".".join((*path, key))
                    )
                if lowered.endswith("_persisted") and child not in (False, "false"):
                    raise PreexistingHistoryAcquisitionError(
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
            raise PreexistingHistoryAcquisitionError(
                "private reasoning tag entered persisted evidence"
            )

    visit(value)


def _safe_result(
    *,
    orchestration: Any,
    manifest: Mapping[str, Any],
    budget: Mapping[str, Any],
    started_at: str,
    completed_at: str,
) -> dict[str, Any]:
    metadata = orchestration.metadata
    integration = metadata.get("r4_integration")
    status = (
        str(integration.get("status", "inconclusive"))
        if isinstance(integration, Mapping)
        else "inconclusive"
    )
    return {
        "schema_version": 1,
        "status": status,
        "run_id": manifest["run_id"],
        "manifest_sha256": manifest["manifest_sha256"],
        "started_at": started_at,
        "completed_at": completed_at,
        "orchestration_status": orchestration.status.value,
        "last_validation_state": orchestration.last_validation_state.value,
        "main_result_accepted": orchestration.accepted,
        "initial_candidate_sha256": text_sha256(orchestration.initial_candidate),
        "final_main_candidate_sha256": text_sha256(orchestration.final_candidate),
        "diagnostic_events": list(metadata.get("diagnostic_events", ())),
        "r2_shadow_diagnostics": list(
            metadata.get("r2_shadow_diagnostics", ())
        ),
        "r5_integration": None if integration is None else dict(integration),
        "budget_usage": dict(budget),
        "provider_calls": int(budget["llm_calls"]),
        "vitis_launches": sum(
            int(budget[name])
            for name in ("csim_calls", "csynth_calls", "cosim_calls")
        ),
        "future_files_read": False,
        "future_outcomes_observed": False,
        "raw_provider_response_persisted": False,
        "private_reasoning_persisted": False,
        "secret_values_persisted": False,
        "accepted_by_acquisition_script": False,
        "r5_accepted": False,
        "r6_started": False,
    }


def _seal(root: Path) -> tuple[str, str]:
    selected = [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.name not in {
            "evidence.zip",
            "evidence.zip.sha256",
            "evidence_content_manifest.json",
        }
        and not path.is_symlink()
    ]
    content = {
        path.relative_to(root).as_posix(): file_sha256(path)
        for path in selected
    }
    content_path = root / "evidence_content_manifest.json"
    _atomic_json(content_path, {"schema_version": 1, "files": content})
    archive = root / "evidence.zip"
    with zipfile.ZipFile(
        archive,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as evidence_zip:
        for path in (*selected, content_path):
            relative = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(relative, (2026, 9, 19, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            evidence_zip.writestr(info, path.read_bytes())
    archive_sha = file_sha256(archive)
    sidecar = root / "evidence.zip.sha256"
    sidecar.write_text(f"{archive_sha}  evidence.zip\n", encoding="utf-8")
    return archive_sha, file_sha256(content_path)


def acquire(
    *,
    preflight: Mapping[str, Any],
    output: Path,
    run_id: str,
) -> dict[str, Any]:
    output = output.expanduser().resolve()
    if output.exists():
        raise PreexistingHistoryAcquisitionError(
            "output directory must not already exist"
        )
    output.mkdir(parents=True)
    manifest = build_acquisition_manifest(preflight, run_id=run_id)
    _atomic_json(output / "acquisition_manifest.json", manifest)
    candidate: R5HistoricalCandidateBundle = preflight["candidate"]
    runtime_value = preflight["model_runtime"]
    runtime = resolve_model_runtime(
        str(runtime_value["model"]),
        family=str(runtime_value["family"]),
        base_url=str(runtime_value["base_url"]),
        api_key_env=str(runtime_value["api_key_env"]),
        reasoning_effort="auto",
        parameters=dict(runtime_value["request_parameters"]),
    )
    task = _task(
        repository=preflight["repository"],
        candidate=candidate,
        run_id=run_id,
    )
    limits = BudgetLimits(
        max_llm_calls=int(manifest["provider_call_upper_bound"]),
        max_tool_calls=40,
        max_compile_calls=30,
        max_csim_calls=3,
        max_csynth_calls=2,
        max_cosim_calls=1,
        max_tokens=16384,
        max_cost_usd=20.0,
        max_wall_time_s=7200.0,
    )
    budget = BudgetManager(limits)
    context = RunContext(
        run_id=run_id,
        task=task,
        budget=budget,
        trace=TraceRecorder(
            run_id,
            task_id=task.task_id,
            output_path=output / "trace.jsonl",
        ),
    )
    model_adapter = CandidateModelAdapter(
        registry=runtime.registry,
        effective_config=runtime.effective_config,
    )
    handler_factory = LocalCandidateValidationHandlerFactory(
        output / "work",
        csynth_timelimit=1200,
        csim_timelimit=1200,
        cosim_timelimit=1200,
        cosim_policy="required",
    )
    public_suite, hidden_suite = _suite_ids(candidate)
    request = CandidateRepairOrchestrationRequest(
        initial_candidate=candidate.isolated_candidate_code,
        original_code=candidate.reference_code,
        preflight_testbench_code=candidate.public_test_code,
        suite_testbench_codes={
            public_suite: candidate.public_test_code,
            hidden_suite: candidate.hidden_test_code,
        },
        prompt_public_testbench_code=candidate.public_test_code,
        max_attempts=1,
        family_instruction=runtime.effective_config.family_instruction,
        reference_top_function=candidate.reference_top,
        candidate_top_function=candidate.candidate_top,
        recovery_profile="conservative-v1",
        llm_advisory_mode="candidate-only",
        r5_arm="A2",
        approved_memory_snippets=(),
    )
    provider = runtime.registry.get_provider(
        runtime.effective_config.provider_name
    )
    shadow = ProviderBackedShadowDiagnosticAdvisor(
        provider=provider,
        model=runtime.effective_config.to_model_spec(),
        budget=budget,
        request_parameters=runtime.effective_config.parameters,
        reserve=ShadowReserve(
            max_calls=1,
            max_tokens=8192,
            max_cost_usd=10.0,
            max_wall_time_s=600,
        ),
        trace=context.trace,
    )
    mutation = CandidateModelR4MutationAdapter(
        model_adapter=model_adapter,
        prompt_factory=R5CandidatePromptFactory(
            request=request,
            approved_memory_snippets=(),
        ),
        budget=budget,
    )
    provisional = R4CanaryManifest(
        manifest_id=str(
            preflight.get(
                "canary_manifest_id",
                "v2.3-r5-preexisting-history-recursive-e2-dfs-v1",
            )
        ),
        manifest_sha256="0" * 64,
        enabled=True,
        operator_enabled=True,
        case_ids=(candidate.case_id,),
        source_sha256=candidate.source_sha256,
        target_identity=manifest["target_identity"],
        toolchain_identity=manifest["toolchain_identity"],
        parser_identity=str(manifest["target_profile"]["parser_profile"]),
        model_identity=model_adapter.model_spec.model,
        prompt_sha256=manifest["prompt_contract_sha256"],
        allowed_stage="csynth",
        max_repairs_per_run=1,
        expires_at="2099-01-01T00:00:00Z",
    )
    canary = replace(
        provisional,
        manifest_sha256=canonical_artifact_sha256(provisional.to_dict()),
    )
    execution_identity = {
        "run_id": run_id + ".initial-validation",
        "case_id": candidate.case_id,
        "stage": "csynth",
        "identity_complete": True,
        "hidden_input_count": 0,
        "secret_present": False,
        "private_reasoning_present": False,
        "source_sha256": candidate.source_sha256,
        "target_identity": manifest["target_identity"],
        "toolchain_identity": manifest["toolchain_identity"],
        "parser_identity": manifest["target_profile"]["parser_profile"],
        "model_identity": model_adapter.model_spec.model,
        "prompt_sha256": manifest["prompt_contract_sha256"],
    }
    integration = ExistingOrchestratorR5Integration(
        R5IntegrationConfig(
            profile=resolve_r5_profile("A2"),
            canary=canary,
            execution_identity=execution_identity,
            calibration_certificate=preflight["certificate"],
            episode_ledger_root=str(output / "ledger"),
            campaign_manifest_sha256=manifest["manifest_sha256"],
            mutation_adapter=mutation,
            validation_wall_time_s=1200.0,
        )
    )
    started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    orchestration = CandidateRepairValidationOrchestrator(
        model_adapter=model_adapter,
        handler_factory=handler_factory,
        shadow_advisor=shadow,
        r4_integration=integration,
    ).run(context, request, validation_id=run_id)
    completed = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    budget_value = budget.snapshot().to_dict()
    result = _safe_result(
        orchestration=orchestration,
        manifest=manifest,
        budget=budget_value,
        started_at=started,
        completed_at=completed,
    )
    if (
        result["provider_calls"] > manifest["provider_call_upper_bound"]
        or result["vitis_launches"] > manifest["vitis_launch_upper_bound"]
    ):
        raise PreexistingHistoryAcquisitionError(
            "real acquisition exceeded its frozen upper bound"
        )
    result["result_sha256"] = canonical_sha256(result)
    secret = os.environ.get(str(runtime_value["api_key_env"]))
    _assert_safe(result, secret=secret)
    _atomic_json(output / "acquisition_result.json", result)
    archive_sha, content_sha = _seal(output)
    print("R5_PREEXISTING_HISTORY_STATUS=" + str(result["status"]))
    print(f"PROVIDER_CALLS={result['provider_calls']}")
    print(f"VITIS_LAUNCHES={result['vitis_launches']}")
    print("EVIDENCE_ARCHIVE_SHA256=" + archive_sha)
    print("EVIDENCE_CONTENT_MANIFEST_SHA256=" + content_sha)
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--plan",
        type=Path,
        default=Path(
            "configs/r5/preexisting_history/recursive_e2_dfs_plan.json"
        ),
    )
    parser.add_argument("--protocol-audit", type=Path, required=True)
    parser.add_argument(
        "--state",
        type=Path,
        default=Path("docs/roadmap/V2_3_STATE.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--run-id",
        default="r5-preexisting-history-recursive-e2-dfs-01",
    )
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args(argv)
    preflight = load_preflight(
        repository=args.repo,
        plan_path=args.plan,
        audit_path=args.protocol_audit,
        state_path=args.state,
    )
    manifest = build_acquisition_manifest(preflight, run_id=args.run_id)
    print("R5_PREEXISTING_HISTORY_PREFLIGHT=passed")
    print("PROVIDER_CALL_UPPER_BOUND=2")
    print("VITIS_LAUNCH_UPPER_BOUND=6")
    if args.preflight_only:
        print("PROVIDER_CALLS=0")
        print("VITIS_LAUNCHES=0")
        print("R5_ACCEPTED=false")
        print("R6_STARTED=false")
        return 0
    acquire(
        preflight=preflight,
        output=args.output,
        run_id=str(manifest["run_id"]),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
