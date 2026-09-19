#!/usr/bin/env python3
"""Run the frozen two-case, three-repeat R5 A0-A6 campaign."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agrefactor.campaign import ExistingRefactorR5CampaignExecutor, R5Arm, R5CaseSpec
from agrefactor.models import resolve_model_runtime
from agrefactor.product import run_source_command_with_r5_capture
from agrefactor.recovery import CalibrationCertificate, ProviderBackedShadowDiagnosticAdvisor, ShadowReserve
from agrefactor.runtime.r4_integration import CandidateModelR4MutationAdapter
from agrefactor.runtime.r5_integration import ExistingOrchestratorR5Integration, R5CandidatePromptFactory, R5IntegrationConfig
from agrefactor.runtime.r5_profile import resolve_r5_profile
from scripts.r5_history_acquisition import _baseline_args, build_execution_identity_and_canary

FREEZE_ROOT = Path("/data/agrefactor_runs/r5_p6_formal_future_freeze_preflight")
CALIBRATION_BUNDLE = Path("/data/agrefactor_runs/v23_r2_real_calibration_identity_v2_20260918/calibration_bundle.json")
ADMISSION_AUDIT = Path("/data/agrefactor_runs/r5_p3_authorized_revalidation_admission_audit_a55a9bb/independent_audit.json")
REPEATS = 3


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def sha_value(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_admission() -> tuple[Any, Any, Any, Any, Any]:
    smoke_path = ROOT / "scripts" / "r5_a0_a6_wiring_smoke.py"
    spec = importlib.util.spec_from_file_location("r5_formal_admission", smoke_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load admission helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state, _manifest, snapshot_value, payload_value, revision_value = module._load_admission()
    revision, snapshot, payload, reduction = module._objects(snapshot_value, payload_value, revision_value)
    if load(ADMISSION_AUDIT).get("status") != "clean_verified_positive_lifecycle_admission":
        raise RuntimeError("accepted history audit is not clean")
    return state, revision, snapshot, payload, reduction


def certificate() -> CalibrationCertificate:
    raw = dict(load(CALIBRATION_BUNDLE)["certificate"])
    certificate_id = raw.pop("certificate_id")
    raw.pop("schema_version", None)
    return CalibrationCertificate(certificate_id=certificate_id, **raw)


def load_contract() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = load(FREEZE_ROOT / "formal_future_manifest.json")
    audit = load(FREEZE_ROOT / "independent_protocol_audit.json")
    if manifest.get("status") != "frozen_ready_for_formal_campaign" or audit.get("status") != "clean_ready_for_formal_campaign":
        raise RuntimeError("formal future contract is not clean")
    if audit.get("manifest_sha256") != manifest.get("manifest_sha256"):
        raise RuntimeError("formal future audit does not bind manifest")
    cases = manifest.get("future_cases")
    if not isinstance(cases, list) or len(cases) != 2:
        raise RuntimeError("formal future manifest does not contain two cases")
    return manifest, [dict(case) for case in cases]


def advisor_factory(runtime: Any):
    def factory(capture: Any, arm: Any, context: Any):
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


def integration_factory(*, certificate_value: Any, revision: Any, snapshot: Any, payload: Any, reduction: Any, campaign_hash: str, case_id: str):
    def factory(capture: Any, arm: Any, context: Any, episode_root: Path, model_adapter: Any):
        identity, canary = build_execution_identity_and_canary(capture=capture, case_id=case_id, certificate=certificate_value)
        profile = resolve_r5_profile(arm.value)
        approved: tuple[str, ...] = ()
        kwargs: dict[str, Any] = {}
        if arm is R5Arm.A3:
            retrieval = sha_value({"campaign": campaign_hash, "arm": "A3", "case": case_id})
            kwargs["retrieval_manifest_sha256"] = retrieval
            approved = (json.dumps({"retrieval_manifest_sha256": retrieval, "memory_mode": "similarity_only"}, sort_keys=True, separators=(",", ":")),)
        if arm in {R5Arm.A4, R5Arm.A5, R5Arm.A6}:
            from agrefactor.recovery.r5_memory_payload import render_candidate_memory_snippets
            kwargs.update(memory_snapshot=snapshot, revision=revision, lifecycle_reduction=reduction, memory_payloads=(payload,))
            approved = render_candidate_memory_snippets((payload,))
        mutation = CandidateModelR4MutationAdapter(
            model_adapter=model_adapter,
            prompt_factory=R5CandidatePromptFactory(request=capture.formal_request, approved_memory_snippets=approved),
            budget=context.budget,
        )
        return ExistingOrchestratorR5Integration(R5IntegrationConfig(
            profile=profile, canary=canary, execution_identity=identity,
            calibration_certificate=certificate_value, episode_ledger_root=str(episode_root),
            campaign_manifest_sha256=campaign_hash, mutation_adapter=mutation,
            validation_wall_time_s=1200.0, **kwargs,
        ))
    return factory


def arm_schedule(case_id: str, repeat: int) -> tuple[R5Arm, ...]:
    offset = int(sha_value({"case_id": case_id, "repeat": repeat})[:8], 16) % len(R5Arm)
    arms = tuple(R5Arm)
    return arms[offset:] + arms[:offset]


def run_case_repeat(*, output: Path, manifest: Mapping[str, Any], case: Mapping[str, Any], runtime_value: Mapping[str, Any], runtime: Any, cert: Any, revision: Any, snapshot: Any, payload: Any, reduction: Any, repeat: int) -> tuple[dict[str, Any], list[dict[str, Any]], int, int]:
    case_id = str(case["case_id"])
    pair_root = output / "cases" / case_id / f"repeat-{repeat:02d}"
    args = _baseline_args(repo=ROOT, case={"paths": case["paths"], "top": case["top"]}, runtime=runtime_value, output=pair_root / "common-product", run_id=f"{manifest['manifest_id']}-{case_id}-{repeat:02d}-baseline")
    execution = run_source_command_with_r5_capture(args)
    capture = execution.r5_common_baseline
    if capture is None or capture.source_sha256 != case["hashes"]["source"]:
        raise RuntimeError(f"{case_id} baseline identity drift")
    case_spec = R5CaseSpec(case_id=case_id, source_sha256=capture.source_sha256, context_signature=capture.context_signature, period="future")
    executor = ExistingRefactorR5CampaignExecutor(
        artifact_root=pair_root / "paired",
        baseline_runner=lambda _case, _repeat, _root: capture,
        advisor_factory=advisor_factory(runtime),
        integration_factory=integration_factory(certificate_value=cert, revision=revision, snapshot=snapshot, payload=payload, reduction=reduction, campaign_hash=str(manifest["manifest_sha256"]), case_id=case_id),
    )
    baseline = dict(executor.prepare_common_baseline(case_spec, repeat))
    observations: list[dict[str, Any]] = []
    provider_calls, vitis_launches = int(baseline["provider_calls"]), int(baseline["vitis_launches"])
    for arm_index, arm in enumerate(arm_schedule(case_id, repeat)):
        observation = dict(executor.run_arm(case=case_spec, repeat=repeat, arm=arm, baseline=baseline, arm_index=arm_index))
        observations.append(observation)
        provider_calls += int(observation["provider_calls"])
        vitis_launches += int(observation["vitis_launches"])
    return baseline, observations, provider_calls, vitis_launches


def run(output: Path, *, preflight: bool) -> dict[str, Any]:
    manifest, cases = load_contract()
    state, revision, snapshot, payload, reduction = load_admission()
    if state.get("R5_CONSUMED_PROVIDER_CALLS") != manifest["budget"]["ledger"]["provider_used"] or state.get("R5_CONSUMED_VITIS_LAUNCHES") != manifest["budget"]["ledger"]["vitis_used"]:
        raise RuntimeError("authoritative budget changed after freeze")
    if preflight:
        return {"status": "ready_for_formal_campaign", "provider_calls": 0, "vitis_launches": 0}
    runtime_value = load(CALIBRATION_BUNDLE)["model_runtime"]
    runtime = resolve_model_runtime(str(runtime_value["model"]), family=str(runtime_value["family"]), base_url=str(runtime_value["base_url"]), api_key_env=str(runtime_value["api_key_env"]), reasoning_effort="auto", parameters=dict(runtime_value["request_parameters"]))
    cert = certificate()
    baselines: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    usage_provider = usage_vitis = 0
    for case in cases:
        for repeat in range(1, REPEATS + 1):
            baseline, rows, used_provider, used_vitis = run_case_repeat(output=output, manifest=manifest, case=case, runtime_value=runtime_value, runtime=runtime, cert=cert, revision=revision, snapshot=snapshot, payload=payload, reduction=reduction, repeat=repeat)
            baselines.append(baseline)
            observations.extend(rows)
            usage_provider += used_provider
            usage_vitis += used_vitis
            if usage_provider > manifest["budget"]["provider_upper_bound"] or usage_vitis > manifest["budget"]["vitis_upper_bound"]:
                raise RuntimeError("formal campaign exceeded frozen upper bound")
    result = {
        "schema_version": 1,
        "status": "ready_for_independent_audit",
        "manifest_sha256": manifest["manifest_sha256"],
        "repository_commit": manifest["repository_commit"],
        "case_ids": [case["case_id"] for case in cases],
        "repeats": REPEATS,
        "baselines": baselines,
        "observations": observations,
        "provider_calls": usage_provider,
        "vitis_launches": usage_vitis,
        "provider_calls_after": manifest["budget"]["ledger"]["provider_used"] + usage_provider,
        "vitis_launches_after": manifest["budget"]["ledger"]["vitis_used"] + usage_vitis,
        "provider_upper_bound": manifest["budget"]["provider_upper_bound"],
        "vitis_upper_bound": manifest["budget"]["vitis_upper_bound"],
        "future_outcomes_observed": True,
        "future_files_executed": True,
        "hidden_input_count": 0,
        "cross_arm_cache_used": False,
        "git_history_mutations": 0,
        "r5_accepted": False,
        "r6_started": False,
    }
    result["result_sha256"] = sha_value(result)
    write_json(output / "formal_campaign_result.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError(f"output directory already exists: {output}")
    output.mkdir(parents=True)
    result = run(output, preflight=args.preflight_only)
    print("R5_FORMAL_CAMPAIGN_STATUS=" + result["status"])
    print("PROVIDER_CALLS=" + str(result["provider_calls"]))
    print("VITIS_LAUNCHES=" + str(result["vitis_launches"]))
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
