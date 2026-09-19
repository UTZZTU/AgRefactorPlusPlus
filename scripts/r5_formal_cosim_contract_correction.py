#!/usr/bin/env python3
"""Run the frozen R5 formal campaign corrective COSIM-contract replication."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agrefactor.campaign import ExistingRefactorR5CampaignExecutor, R5CaseSpec
from agrefactor.models import resolve_model_runtime
from agrefactor.product import run_source_command_with_r5_capture
from scripts import r5_formal_campaign as parent
from scripts.r5_history_acquisition import _baseline_args

FREEZE_ROOT = Path(
    "/data/agrefactor_runs/r5_p6_formal_public_contract_correction_v2_freeze"
)


def load_contract() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    correction = parent.load(FREEZE_ROOT / "correction_manifest.json")
    audit = parent.load(FREEZE_ROOT / "independent_protocol_audit.json")
    frozen, cases = parent.load_contract()
    if correction.get("status") != "frozen_ready_for_corrective_replication":
        raise RuntimeError("corrective replication manifest is not frozen")
    if audit.get("status") != "clean_ready_for_corrective_replication":
        raise RuntimeError("corrective replication protocol audit is not clean")
    if audit.get("manifest_sha256") != correction.get("manifest_sha256"):
        raise RuntimeError("corrective protocol audit does not bind its manifest")
    if correction.get("parent_manifest_sha256") != frozen.get("manifest_sha256"):
        raise RuntimeError("corrective replication parent manifest drift")
    return correction, frozen, cases


def run_case_repeat(
    *,
    output: Path,
    correction: Mapping[str, Any],
    frozen: Mapping[str, Any],
    case: Mapping[str, Any],
    runtime_value: Mapping[str, Any],
    runtime: Any,
    cert: Any,
    revision: Any,
    snapshot: Any,
    payload: Any,
    reduction: Any,
    repeat: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], int, int]:
    case_id = str(case["case_id"])
    contract_by_id = {
        str(item["case_id"]): item for item in correction["case_contracts"]
    }
    contract = contract_by_id[case_id]
    contract_path = ROOT / str(contract["path"])
    if hashlib.sha256(contract_path.read_bytes()).hexdigest() != contract["file_sha256"]:
        raise RuntimeError(f"{case_id} Public runtime contract hash drift")
    pair_root = output / "cases" / case_id / f"repeat-{repeat:02d}"
    args = _baseline_args(
        repo=ROOT,
        case={"paths": case["paths"], "top": case["top"]},
        runtime=runtime_value,
        output=pair_root / "common-product",
        run_id=f"{correction['manifest_id']}-{case_id}-{repeat:02d}-baseline",
        public_test_contract=contract_path,
    )
    execution = run_source_command_with_r5_capture(args)
    capture = execution.r5_common_baseline
    if capture is None or capture.source_sha256 != case["hashes"]["source"]:
        raise RuntimeError(f"{case_id} baseline identity drift")
    case_spec = R5CaseSpec(
        case_id=case_id,
        source_sha256=capture.source_sha256,
        context_signature=capture.context_signature,
        period="future",
    )
    executor = ExistingRefactorR5CampaignExecutor(
        artifact_root=pair_root / "paired",
        baseline_runner=lambda _case, _repeat, _root: capture,
        advisor_factory=parent.advisor_factory(runtime),
        integration_factory=parent.integration_factory(
            certificate_value=cert,
            revision=revision,
            snapshot=snapshot,
            payload=payload,
            reduction=reduction,
            campaign_hash=str(correction["manifest_sha256"]),
            case_id=case_id,
        ),
    )
    baseline = dict(executor.prepare_common_baseline(case_spec, repeat))
    observations: list[dict[str, Any]] = []
    provider_calls = int(baseline["provider_calls"])
    vitis_launches = int(baseline["vitis_launches"])
    for arm_index, arm in enumerate(parent.arm_schedule(case_id, repeat)):
        observation = dict(
            executor.run_arm(
                case=case_spec,
                repeat=repeat,
                arm=arm,
                baseline=baseline,
                arm_index=arm_index,
            )
        )
        observations.append(observation)
        provider_calls += int(observation["provider_calls"])
        vitis_launches += int(observation["vitis_launches"])
    return baseline, observations, provider_calls, vitis_launches


def run(output: Path, *, preflight: bool) -> dict[str, Any]:
    correction, frozen, cases = load_contract()
    state, revision, snapshot, payload, reduction = parent.load_admission()
    budget = correction["budget"]
    if preflight:
        return {"status": "ready_for_corrective_replication", "provider_calls": 0, "vitis_launches": 0}
    runtime_value = parent.load(parent.CALIBRATION_BUNDLE)["model_runtime"]
    runtime = resolve_model_runtime(
        str(runtime_value["model"]),
        family=str(runtime_value["family"]),
        base_url=str(runtime_value["base_url"]),
        api_key_env=str(runtime_value["api_key_env"]),
        reasoning_effort="auto",
        parameters=dict(runtime_value["request_parameters"]),
    )
    cert = parent.certificate()
    baselines: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    usage_provider = usage_vitis = 0
    for case in cases:
        for repeat in range(1, parent.REPEATS + 1):
            baseline, rows, used_provider, used_vitis = run_case_repeat(
                output=output,
                correction=correction,
                frozen=frozen,
                case=case,
                runtime_value=runtime_value,
                runtime=runtime,
                cert=cert,
                revision=revision,
                snapshot=snapshot,
                payload=payload,
                reduction=reduction,
                repeat=repeat,
            )
            baselines.append(baseline)
            observations.extend(rows)
            usage_provider += used_provider
            usage_vitis += used_vitis
            if usage_provider > budget["provider_upper_bound"] or usage_vitis > budget["vitis_upper_bound"]:
                raise RuntimeError("corrective replication exceeded its frozen bound")
    result: dict[str, Any] = {
        "schema_version": 1,
        "status": "ready_for_independent_audit",
        "campaign_role": "posthoc_corrective_replication",
        "future_holdout_claim": False,
        "correction_manifest_sha256": correction["manifest_sha256"],
        "parent_manifest_sha256": frozen["manifest_sha256"],
        "repository_commit": correction["repository_commit"],
        "case_ids": [case["case_id"] for case in cases],
        "repeats": parent.REPEATS,
        "baselines": baselines,
        "observations": observations,
        "provider_calls": usage_provider,
        "vitis_launches": usage_vitis,
        "provider_calls_after": budget["provider_calls_before"] + usage_provider,
        "vitis_launches_after": budget["vitis_launches_before"] + usage_vitis,
        "provider_upper_bound": budget["provider_upper_bound"],
        "vitis_upper_bound": budget["vitis_upper_bound"],
        "hidden_input_count": 0,
        "cross_arm_cache_used": False,
        "git_history_mutations": 0,
        "r5_accepted": False,
        "r6_started": False,
    }
    result["result_sha256"] = parent.sha_value(result)
    parent.write_json(output / "formal_campaign_result.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output.exists():
        raise RuntimeError(f"output directory already exists: {output}")
    output.mkdir(parents=True)
    result = run(output, preflight=args.preflight_only)
    print("R5_FORMAL_COSIM_CORRECTION_STATUS=" + str(result["status"]))
    print("PROVIDER_CALLS=" + str(result["provider_calls"]))
    print("VITIS_LAUNCHES=" + str(result["vitis_launches"]))
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
