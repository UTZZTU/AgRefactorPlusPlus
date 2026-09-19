#!/usr/bin/env python3
"""Run the bounded one-case R5 pilot through the existing refactor seam."""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agrefactor.campaign import ExistingRefactorR5CampaignExecutor, R5Arm, R5CaseSpec
from agrefactor.models import resolve_model_runtime
from agrefactor.product import run_source_command_with_r5_capture
from agrefactor.recovery import (
    CalibrationCertificate,
    ProviderBackedShadowDiagnosticAdvisor,
    R4CanaryManifest,
    R5MemoryPayload,
    R5PatternRevision,
    R5MemorySnapshot,
    ShadowReserve,
)
from agrefactor.recovery.pattern_lifecycle import Lifecycle, LifecycleReduction
from agrefactor.recovery.r5_memory_payload import render_candidate_memory_snippets
from agrefactor.recovery.r4_provenance import canonical_artifact_sha256
from agrefactor.runtime import BudgetLimits
from agrefactor.runtime.r4_integration import CandidateModelR4MutationAdapter
from agrefactor.runtime.r5_integration import ExistingOrchestratorR5Integration, R5CandidatePromptFactory, R5IntegrationConfig
from agrefactor.runtime.r5_profile import resolve_r5_profile
from scripts.r5_history_acquisition import _baseline_args, build_execution_identity_and_canary

ADAPTER_MANIFEST = Path("/data/agrefactor_runs/r5_p2_adapter_freeze_v2/oracle_adapter_manifest.json")
PROTOCOL_AUDIT = Path("/data/agrefactor_runs/r5_p5_bounded_pilot_protocol_audit_732852d/protocol_audit.json")
CALIBRATION_BUNDLE = Path("/data/agrefactor_runs/v23_r2_real_calibration_identity_v2_20260918/calibration_bundle.json")
ADMISSION_ROOT = Path("/data/agrefactor_runs/r5_p3_authorized_revalidation_admission_a55a9bb")
ADMISSION_AUDIT = Path("/data/agrefactor_runs/r5_p3_authorized_revalidation_admission_audit_a55a9bb/independent_audit.json")
PILOT_CASE_ID = "Exception_E3_Turbo_Encoder"
PILOT_PROVIDER_CAP = 60
PILOT_VITIS_CAP = 72
REPEATS = 3


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("JSON object required: " + str(path))
    return value


def sha_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha_value(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + chr(10), encoding="utf-8")


def git_head() -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()


def git_is_ancestor(commit: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", commit, "HEAD"],
        check=False,
        capture_output=True,
    )
    return completed.returncode == 0


def objects() -> tuple[dict[str, Any], Any, Any, Any, Any]:
    smoke_path = ROOT / "scripts" / "r5_a0_a6_wiring_smoke.py"
    spec = importlib.util.spec_from_file_location("r5_wiring_smoke_for_pilot", smoke_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Trusted admission helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state, _manifest, snapshot_value, payload_value, revision_value = module._load_admission()
    revision, snapshot, payload, reduction = module._objects(snapshot_value, payload_value, revision_value)
    audit = load(ADMISSION_AUDIT)
    if audit.get("status") != "clean_verified_positive_lifecycle_admission":
        raise RuntimeError("Trusted admission audit is not clean")
    return state, revision, snapshot, payload, reduction


def certificate() -> CalibrationCertificate:
    raw = load(CALIBRATION_BUNDLE)["certificate"]
    value = dict(raw)
    certificate_id = value.pop("certificate_id")
    value.pop("schema_version", None)
    return CalibrationCertificate(certificate_id=certificate_id, **value)


def load_case() -> dict[str, Any]:
    manifest = load(ADAPTER_MANIFEST)
    cases = {item.get("case_id"): item for item in manifest.get("cases", []) if isinstance(item, Mapping)}
    case = cases.get(PILOT_CASE_ID)
    if not isinstance(case, dict) or case.get("period") != "future" or case.get("control_role") != "positive":
        raise RuntimeError("pilot case is not the frozen future positive")
    return case


def load_protocol() -> dict[str, Any]:
    audit = load(PROTOCOL_AUDIT)
    budget = load(PROTOCOL_AUDIT.parent / "budget_plan.json")
    if audit.get("status") != "ready_for_history_acquisition":
        raise RuntimeError("zero-call protocol audit is not clean")
    for key in ("provider_calls", "vitis_launches", "git_history_mutations"):
        if audit.get(key) != 0:
            raise RuntimeError("protocol audit has nonzero calls")
    if (
        budget.get("pilot_provider_upper_bound") != PILOT_PROVIDER_CAP
        or budget.get("pilot_vitis_upper_bound") != PILOT_VITIS_CAP
        or budget.get("fits_hard_caps_with_recovery_reserve") is not True
        or budget.get("budget_plan_sha256") != audit.get("budget_plan_sha256")
    ):
        raise RuntimeError("pilot budget plan is incompatible")
    audit["_budget_plan"] = budget
    return audit


def build_pilot_manifest(case: Mapping[str, Any], state: Mapping[str, Any], protocol: Mapping[str, Any]) -> dict[str, Any]:
    budget = protocol["_budget_plan"]
    value = {
        "schema_version": 1,
        "pilot_id": "v2.3-r5-bounded-pilot-exception-e3-turbo-encoder-v1",
        "status": "frozen_before_pilot_outcome_observation",
        "repository_head": git_head(),
        "authoritative_state_head": state.get("head"),
        "implementation_head": state.get("implementation_head"),
        "r4_accepted": state.get("R4_ACCEPTED") is True,
        "r5_accepted": state.get("R5_ACCEPTED") is False,
        "r6_started": state.get("R6_STARTED") is False,
        "dataset_manifest_sha256": protocol["dataset_manifest_sha256"],
        "split_manifest_sha256": protocol["split_manifest_sha256"],
        "protocol_audit_sha256": protocol["protocol_audit_sha256"],
        "adapter_manifest_file_sha256": sha_bytes(ADAPTER_MANIFEST),
        "case_id": case["case_id"],
        "period": case["period"],
        "control_role": case["control_role"],
        "source_sha256": case["hashes"]["source"],
        "public_test_sha256": case["hashes"]["public_test"],
        "hidden_test_sha256": case["hashes"]["hidden_test"],
        "top": case["top"],
        "candidate_top": case["candidate_top"],
        "future_outcomes_observed_before_start": False,
        "future_files_executed_before_start": False,
        "arm_order": [arm.value for arm in R5Arm],
        "counterbalanced_schedule": True,
        "repeats": REPEATS,
        "context_signature_binding": "derived_from_each_common_baseline_before_arm_execution",
        "trusted_revision_sha256": state["R5_AUTHORIZED_CANDIDATE_REVALIDATION_REVISION_SHA256"],
        "trusted_snapshot_sha256": state["R5_AUTHORIZED_CANDIDATE_REVALIDATION_SNAPSHOT_SHA256"],
        "trusted_payload_sha256": state["R5_AUTHORIZED_CANDIDATE_REVALIDATION_PAYLOAD_SHA256"],
        "provider_used_before": state["R5_CONSUMED_PROVIDER_CALLS"],
        "vitis_used_before": state["R5_CONSUMED_VITIS_LAUNCHES"],
        "provider_upper_bound": PILOT_PROVIDER_CAP,
        "vitis_upper_bound": PILOT_VITIS_CAP,
        "provider_recovery_reserve": budget["provider_recovery_reserve"],
        "vitis_recovery_reserve": budget["vitis_recovery_reserve"],
        "budget_plan_file_sha256": sha_bytes(PROTOCOL_AUDIT.parent / "budget_plan.json"),
        "protocol_audit_file_sha256": sha_bytes(PROTOCOL_AUDIT),
        "trusted_admission_audit_sha256": sha_bytes(ADMISSION_AUDIT),
        "future_holdout_outcomes_read": False,
    }
    value["pilot_manifest_sha256"] = sha_value(value)
    return value


def advisor_factory(runtime):
    def factory(capture, arm, context):
        del capture, arm
        provider = runtime.registry.get_provider(runtime.effective_config.provider_name)
        return ProviderBackedShadowDiagnosticAdvisor(
            provider=provider,
            model=runtime.effective_config.to_model_spec(),
            budget=context.budget,
            request_parameters=runtime.effective_config.parameters,
            reserve=ShadowReserve(max_calls=1, max_tokens=8192, max_cost_usd=10.0, max_wall_time_s=600),
            trace=context.trace,
        )
    return factory


def integration_factory(*, certificate_value, revision, snapshot, payload, reduction, pilot_hash):
    def factory(capture, arm, context, episode_root, model_adapter):
        identity, canary = build_execution_identity_and_canary(
            capture=capture, case_id=PILOT_CASE_ID, certificate=certificate_value
        )
        profile = resolve_r5_profile(arm.value)
        kwargs: dict[str, Any] = {}
        memory_payloads: tuple[R5MemoryPayload, ...] = ()
        retrieval = None
        approved: tuple[str, ...] = ()
        if arm is R5Arm.A3:
            retrieval = sha_value({"pilot": pilot_hash, "arm": "A3", "case": PILOT_CASE_ID})
            kwargs["retrieval_manifest_sha256"] = retrieval
            approved = (
                json.dumps(
                    {"retrieval_manifest_sha256": retrieval, "memory_mode": "similarity_only"},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        if arm in {R5Arm.A4, R5Arm.A5, R5Arm.A6}:
            memory_payloads = (payload,)
            kwargs.update(memory_snapshot=snapshot, revision=revision, lifecycle_reduction=reduction, memory_payloads=memory_payloads)
            approved = render_candidate_memory_snippets(memory_payloads)
        mutation = CandidateModelR4MutationAdapter(
            model_adapter=model_adapter,
            prompt_factory=R5CandidatePromptFactory(
                request=capture.formal_request,
                approved_memory_snippets=approved,
            ),
            budget=context.budget,
        )
        return ExistingOrchestratorR5Integration(
            R5IntegrationConfig(
                profile=profile,
                canary=canary,
                execution_identity=identity,
                calibration_certificate=certificate_value,
                episode_ledger_root=str(episode_root),
                campaign_manifest_sha256=pilot_hash,
                mutation_adapter=mutation,
                validation_wall_time_s=1200.0,
                **kwargs,
            )
        )
    return factory


def arm_schedule(case_id: str, repeat: int) -> tuple[R5Arm, ...]:
    offset = int(sha_value({"case_id": case_id, "repeat": repeat})[:8], 16) % len(R5Arm)
    arms = tuple(R5Arm)
    return arms[offset:] + arms[:offset]


def run(output: Path, *, preflight: bool) -> dict[str, Any]:
    state, revision, snapshot, payload, reduction = objects()
    protocol = load_protocol()
    case = load_case()
    implementation_head = state.get("implementation_head")
    if not isinstance(implementation_head, str) or not git_is_ancestor(implementation_head):
        raise RuntimeError("authoritative implementation head is not an ancestor of HEAD")
    if state.get("R5_ACCEPTED") is not False or state.get("R6_STARTED") is not False:
        raise RuntimeError("R5/R6 state does not permit pilot")
    pilot = build_pilot_manifest(case, state, protocol)
    write_json(output / "pilot_manifest.json", pilot)
    if preflight:
        return {"status": "ready_for_real_bounded_pilot", "pilot_manifest_sha256": pilot["pilot_manifest_sha256"], "provider_calls": 0, "vitis_launches": 0}

    runtime_value = load(CALIBRATION_BUNDLE)["model_runtime"]
    runtime = resolve_model_runtime(
        str(runtime_value["model"]),
        family=str(runtime_value["family"]),
        base_url=str(runtime_value["base_url"]),
        api_key_env=str(runtime_value["api_key_env"]),
        reasoning_effort="auto",
        parameters=dict(runtime_value["request_parameters"]),
    )
    cert = certificate()
    observations: list[dict[str, Any]] = []
    baselines: list[dict[str, Any]] = []
    usage_provider = 0
    usage_vitis = 0
    for repeat in range(1, REPEATS + 1):
        pair_root = output / "pairs" / ("repeat-%02d" % repeat)
        product_root = pair_root / "common-product"
        args = _baseline_args(
            repo=ROOT,
            case=case,
            runtime=runtime_value,
            output=product_root,
            run_id="%s-baseline-%02d" % (pilot["pilot_id"], repeat),
        )
        execution = run_source_command_with_r5_capture(args)
        capture = execution.r5_common_baseline
        if capture is None:
            raise RuntimeError("ordinary refactor did not expose common baseline")
        case_spec = R5CaseSpec(
            case_id=PILOT_CASE_ID,
            source_sha256=capture.source_sha256,
            context_signature=capture.context_signature,
            period="future",
        )
        executor = ExistingRefactorR5CampaignExecutor(
            artifact_root=pair_root / "paired",
            baseline_runner=lambda case_value, repeat_value, artifact_root: capture,
            advisor_factory=advisor_factory(runtime),
            integration_factory=integration_factory(
                certificate_value=cert,
                revision=revision,
                snapshot=snapshot,
                payload=payload,
                reduction=reduction,
                pilot_hash=pilot["pilot_manifest_sha256"],
            ),
        )
        baseline = executor.prepare_common_baseline(case_spec, repeat)
        baselines.append(dict(baseline))
        usage_provider += int(baseline["provider_calls"])
        usage_vitis += int(baseline["vitis_launches"])
        for arm_index, arm in enumerate(arm_schedule(PILOT_CASE_ID, repeat)):
            observation = executor.run_arm(
                case=case_spec,
                repeat=repeat,
                arm=arm,
                baseline=baseline,
                arm_index=arm_index,
            )
            observations.append(dict(observation))
            usage_provider += int(observation["provider_calls"])
            usage_vitis += int(observation["vitis_launches"])
            if usage_provider > PILOT_PROVIDER_CAP or usage_vitis > PILOT_VITIS_CAP:
                raise RuntimeError("pilot exceeded frozen upper bound")
    result = {
        "schema_version": 1,
        "status": "ready_for_independent_audit",
        "pilot_manifest_sha256": pilot["pilot_manifest_sha256"],
        "repository_head": pilot["repository_head"],
        "case_id": PILOT_CASE_ID,
        "repeats": REPEATS,
        "baselines": baselines,
        "observations": observations,
        "provider_calls": usage_provider,
        "vitis_launches": usage_vitis,
        "provider_calls_after": pilot["provider_used_before"] + usage_provider,
        "vitis_launches_after": pilot["vitis_used_before"] + usage_vitis,
        "provider_cap": PILOT_PROVIDER_CAP,
        "vitis_cap": PILOT_VITIS_CAP,
        "future_outcomes_observed": True,
        "future_files_executed": True,
        "cross_arm_cache_used": False,
        "hidden_input_count": 0,
        "git_history_mutations": 0,
        "r5_accepted": False,
        "r6_started": False,
    }
    result["result_sha256"] = sha_value(result)
    write_json(output / "pilot_result.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError("output directory already exists")
    output.mkdir(parents=True)
    result = run(output, preflight=args.preflight_only)
    print("R5_BOUNDED_PILOT_STATUS=" + result["status"])
    print("R5_BOUNDED_PILOT_REASON=one_case_future_positive_through_existing_refactor_r4_path")
    print("PROVIDER_CALLS=" + str(result["provider_calls"]))
    print("VITIS_LAUNCHES=" + str(result["vitis_launches"]))
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
