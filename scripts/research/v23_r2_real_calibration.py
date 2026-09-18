#!/usr/bin/env python3
"""Run pre-registered real-Vitis R2 calibration without adding a CLI entry."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping

from agrefactor.config import EvaluationSplit, RunMode, TaskSpec, TestSuiteSpec, resolve_target_profile
from agrefactor.models import CandidateModelAdapter, resolve_model_runtime
from agrefactor.recovery.shadow_advisor import (
    CalibrationAcceptancePolicy,
    ProviderBackedShadowDiagnosticAdvisor,
    ShadowReserve,
    build_shadow_request,
    certify_calibration,
    diagnostic_event_from_dict,
    evaluate_calibration,
    freeze_calibration_protocol,
    run_shadow_diagnostics,
)
from agrefactor.runtime import BudgetLimits, BudgetManager, RunContext, TraceRecorder
from agrefactor.runtime.candidate_repair_integration import (
    CandidateRepairOrchestrationRequest,
    CandidateRepairValidationOrchestrator,
    LocalCandidateValidationHandlerFactory,
)


MANIFEST_PATH = Path("docs/roadmap/V2_3_R2_REAL_CALIBRATION.json")
CASE_PREFIX = "v23-r2-calibration-"

REFERENCE_SOURCE = """void vector_add_reference(const int a[4], const int b[4], int c[4]) {
    for (int i = 0; i < 4; ++i) c[i] = a[i] + b[i];
}
"""

PUBLIC_TESTBENCH = r'''#include <fstream>
extern "C" void vector_add(const int[4], const int[4], int[4]);
void vector_add_reference(const int[4], const int[4], int[4]);

static void write_outcome(const char *path, const char *status,
                          const char *kind, const char *owner,
                          const char *reason) {
    if (path == nullptr || path[0] == '\0') return;
    std::ofstream out(path);
    if (!out) return;
    out << "{\"schema_version\":1,\"status\":\"" << status
        << "\",\"failure_kind\":\"" << kind
        << "\",\"failure_owner\":\"" << owner
        << "\",\"reason_code\":\"" << reason << "\"}\n";
}

int main(int argc, char **argv) {
    const char *outcome = argc >= 2 ? argv[1] : nullptr;
    int a[4] = {1, 2, 3, 4};
    int b[4] = {5, 6, 7, 8};
    int got[4] = {};
    int expected[4] = {};
    vector_add(a, b, got);
    vector_add_reference(a, b, expected);
    for (int i = 0; i < 4; ++i) {
        if (got[i] != expected[i]) {
            write_outcome(outcome, "failed", "functional_mismatch",
                          "candidate", "public_vector_add_mismatch");
            return 2;
        }
    }
    write_outcome(outcome, "passed", "", "none", "csim_passed");
    return 0;
}
'''


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode:
        raise RuntimeError("git command failed: " + " ".join(args))
    return completed.stdout.strip()


def repository_identity(repo: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    head = git(repo, "rev-parse", "HEAD")
    branch = git(repo, "branch", "--show-current")
    status = git(repo, "status", "--porcelain", "--untracked-files=all")
    base = str(manifest["implementation_base_head"])
    ancestor = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", base, head],
        check=False,
    ).returncode == 0
    product_diff = subprocess.run(
        ["git", "-C", str(repo), "diff", "--quiet", f"{base}..{head}", "--", "agrefactor"],
        check=False,
    ).returncode
    if branch != "research-roadmap-v2.3" or status or not ancestor or product_diff != 0:
        raise RuntimeError("repository identity or calibration product baseline mismatch")
    return {
        "head": head,
        "branch": branch,
        "clean": True,
        "implementation_base_head": base,
        "product_code_unchanged_since_base": True,
    }


def case_specs(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    selection = manifest["selection"]
    result = []
    for family in selection["ordered_generator_families"]:
        for variant in range(1, int(selection["variants_per_family"]) + 1):
            result.append({
                "case_id": f"{CASE_PREFIX}{family}-{variant:02d}",
                "family": family,
                "variant": variant,
            })
    return result


def candidate_source(family: str, variant: int) -> str:
    marker = f"constexpr int calibration_marker = {variant};\n    (void)calibration_marker;"
    prelude = ""
    body = ""
    if family == "recursion":
        prelude = """static int recursive_identity(int value, unsigned depth) {
    return depth == 0 ? value : recursive_identity(value, depth - 1);
}
"""
        body = """unsigned depth = a[i] < -1000000 ? calibration_marker : 0;
        c[i] = recursive_identity(a[i] + b[i], depth);"""
    elif family == "function_pointer":
        prelude = """typedef int (*binary_operation)(int, int);
static int add_primary(int x, int y) { return x + y; }
static int add_alternate(int x, int y) { return x + y; }
"""
        body = """binary_operation operation =
            a[i] == 2147483647 ? add_alternate : add_primary;
        c[i] = operation(a[i], b[i]);"""
    elif family == "exception":
        body = """try {
            if (a[i] == -2147483647) throw calibration_marker;
            c[i] = a[i] + b[i];
        } catch (...) {
            c[i] = a[i] + b[i];
        }"""
    elif family == "virtual_dispatch":
        prelude = """class AdditionBase {
public:
    virtual int apply(int x, int y) const = 0;
    virtual ~AdditionBase() {}
};
class Addition final : public AdditionBase {
public:
    int apply(int x, int y) const override { return x + y; }
};
"""
        body = """Addition implementation;
        const AdditionBase *operation = &implementation;
        c[i] = operation->apply(a[i], b[i]);"""
    elif family == "runtime_stack_allocation":
        prelude = "#include <alloca.h>\n"
        body = """int length = 4 + (a[0] == -2147483647 ? calibration_marker : 0);
        int *temporary = static_cast<int *>(alloca(sizeof(int) * length));
        temporary[i] = a[i] + b[i];
        c[i] = temporary[i];"""
    elif family == "variable_length_array":
        body = """int length = 4 + (a[0] == -2147483647 ? calibration_marker : 0);
        int temporary[length];
        temporary[i] = a[i] + b[i];
        c[i] = temporary[i];"""
    elif family == "rtti":
        prelude = """class RttiBase { public: virtual ~RttiBase() {} };
class RttiAddition final : public RttiBase {
public:
    int apply(int x, int y) const { return x + y; }
};
"""
        body = """RttiAddition implementation;
        RttiBase *base = &implementation;
        RttiAddition *operation = dynamic_cast<RttiAddition *>(base);
        c[i] = operation->apply(a[i], b[i]);"""
    elif family == "thread_local_storage":
        prelude = """static int add_with_thread_state(int x, int y) {
    static thread_local int bias = 0;
    return x + y + bias;
}
"""
        body = "c[i] = add_with_thread_state(a[i], b[i]);"
    else:
        raise ValueError("unknown calibration generator family")
    return f'''{prelude}extern "C" void vector_add(const int a[4], const int b[4], int c[4]) {{
#pragma HLS INTERFACE ap_memory port=a
#pragma HLS INTERFACE ap_memory port=b
#pragma HLS INTERFACE ap_memory port=c
    {marker}
    for (int i = 0; i < 4; ++i) {{
        {body}
    }}
}}
'''


def eligible_event(record: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, Any] | None:
    selection = manifest["selection"]
    events = record.get("diagnostic_events")
    if not isinstance(events, list):
        return None
    eligible = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        items = event.get("diagnostic_items")
        specific = (
            isinstance(items, list)
            and any(
                isinstance(item, Mapping)
                and item.get("parser_rule") == selection["required_parser_rule"]
                and isinstance(item.get("diagnostic_code"), str)
                and bool(item.get("diagnostic_code"))
                for item in items
            )
        )
        aggregate_only = (
            isinstance(items, list)
            and bool(items)
            and all(
                isinstance(item, Mapping)
                and item.get("parser_rule") == "aggregate_source_synthesis"
                for item in items
            )
        )
        if (
            event.get("stage") == selection["required_stage"]
            and event.get("owner") in selection["required_deterministic_owner"]
            and event.get("route_action") in selection["required_route_actions"]
            and event.get("physical_tool_launched") is True
            and event.get("evidence_complete") is True
            and specific
            and not aggregate_only
        ):
            build_shadow_request(diagnostic_event_from_dict(event))
            eligible.append(dict(event))
    return eligible[0] if len(eligible) == 1 else None


def build_task(repo: Path) -> TaskSpec:
    contract = {
        "schema_version": 1,
        "kind": "public_differential_self_check_v1",
        "candidate_mismatch_returncodes": [2],
    }
    return TaskSpec(
        task_id="v23-r2-real-calibration",
        kernel_path=str(repo / MANIFEST_PATH),
        kernel_name="vector_add",
        target=resolve_target_profile("vitis-2023.2-default"),
        mode=RunMode.REFACTOR,
        testbench_path=str(repo / MANIFEST_PATH),
        test_suites=(TestSuiteSpec(
            suite_id="public-vector-add-calibration",
            split=EvaluationSplit.PUBLIC,
            suite_version="v23-r2-calibration-v1",
            case_count=4,
            testbench_path=str(repo / MANIFEST_PATH),
            runtime_contract=contract,
        ),),
    )


def qualification_record(
    *,
    spec: Mapping[str, Any],
    source: str,
    repo: Path,
    output: Path,
    task: TaskSpec,
    model_adapter: CandidateModelAdapter,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    case_id = str(spec["case_id"])
    result_path = output / "qualification" / f"{case_id}.json"
    source_sha = sha256_text(source.rstrip() + "\n")
    if result_path.is_file():
        existing = json.loads(result_path.read_text(encoding="utf-8"))
        if existing.get("candidate_source_sha256") != source_sha:
            raise RuntimeError("qualification resume source hash mismatch")
        return existing
    budget = BudgetManager(BudgetLimits(
        max_llm_calls=0,
        max_tool_calls=12,
        max_compile_calls=8,
        max_csim_calls=2,
        max_csynth_calls=2,
        max_cosim_calls=0,
        max_wall_time_s=1800,
    ))
    context = RunContext(
        run_id=case_id,
        task=task,
        budget=budget,
        trace=TraceRecorder(case_id, task_id=task.task_id),
    )
    request = CandidateRepairOrchestrationRequest(
        initial_candidate=source,
        original_code=REFERENCE_SOURCE,
        preflight_testbench_code=PUBLIC_TESTBENCH,
        suite_testbench_codes={"public-vector-add-calibration": PUBLIC_TESTBENCH},
        prompt_public_testbench_code=PUBLIC_TESTBENCH,
        max_attempts=1,
        reference_top_function="vector_add_reference",
        candidate_top_function="vector_add",
        llm_advisory_mode="off",
    )
    result = CandidateRepairValidationOrchestrator(
        model_adapter=model_adapter,
        handler_factory=LocalCandidateValidationHandlerFactory(
            output / "work" / case_id,
            csim_timelimit=300,
            csynth_timelimit=900,
            cosim_timelimit=900,
            cosim_policy="off",
        ),
    ).run(context, request, validation_id=case_id)
    usage = budget.snapshot().to_dict()
    if usage["llm_calls"] != 0:
        raise RuntimeError("provider was called during deterministic qualification")
    metadata = dict(result.metadata)
    ledger = metadata.get("recovery_ledger")
    ledger_count = len(ledger) if isinstance(ledger, list) else 0
    main_snapshot = {
        "route": result.last_validation_state.value,
        "status": result.status.value,
        "final_candidate_sha256": sha256_text(result.final_candidate.rstrip() + "\n"),
        "recovery_ledger_count": ledger_count,
        "repair_count": int(metadata.get("repair_attempt_count", 0)),
        "best_correct_pointer": None,
    }
    record = {
        "schema_version": 1,
        **dict(spec),
        "candidate_source_sha256": source_sha,
        "truth": dict(manifest["truth"]),
        "status": result.status.value,
        "last_validation_state": result.last_validation_state.value,
        "diagnostic_events": list(metadata.get("diagnostic_events", [])),
        "main_snapshot": main_snapshot,
        "budget_usage": usage,
        "provider_calls": 0,
        "vitis_launches": usage["csim_calls"] + usage["csynth_calls"] + usage["cosim_calls"],
        "raw_provider_response_persisted": False,
        "private_reasoning_persisted": False,
    }
    record["eligible_event"] = eligible_event(record, manifest)
    atomic_json(result_path, record)
    return record


def policy_from_manifest(manifest: Mapping[str, Any]) -> CalibrationAcceptancePolicy:
    value = dict(manifest["acceptance_policy"])
    value.pop("schema_version", None)
    return CalibrationAcceptancePolicy(**value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--family", default="deepseek")
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    args = parser.parse_args()
    repo = args.repo.expanduser().resolve()
    output = args.output.expanduser().resolve()
    manifest = json.loads((repo / MANIFEST_PATH).read_text(encoding="utf-8"))
    identity = repository_identity(repo, manifest)
    if not os.environ.get(args.api_key_env):
        raise RuntimeError("selected provider credential is missing")
    output.mkdir(parents=True, exist_ok=True)
    manifest_sha = sha256_value(manifest)
    frozen_manifest = output / "frozen_manifest.json"
    if frozen_manifest.is_file():
        existing = json.loads(frozen_manifest.read_text(encoding="utf-8"))
        if existing != manifest:
            raise RuntimeError("frozen manifest mismatch on resume")
    else:
        atomic_json(frozen_manifest, manifest)

    runtime = resolve_model_runtime(
        args.model,
        family=args.family,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        reasoning_effort="auto",
        parameters={"max_tokens": 4096, "temperature": 0.0},
    )
    model_adapter = CandidateModelAdapter(
        registry=runtime.registry,
        effective_config=runtime.effective_config,
    )
    task = build_task(repo)
    qualifications = []
    vitis_launches = 0
    hard_vitis = int(manifest["hard_budgets"]["vitis_launches"])
    for spec in case_specs(manifest):
        source = candidate_source(str(spec["family"]), int(spec["variant"]))
        record = qualification_record(
            spec=spec,
            source=source,
            repo=repo,
            output=output,
            task=task,
            model_adapter=model_adapter,
            manifest=manifest,
        )
        qualifications.append(record)
        vitis_launches += int(record["vitis_launches"])
        if vitis_launches > hard_vitis:
            raise RuntimeError("Vitis launch hard budget exceeded")
        print(f"R2_CALIBRATION_QUALIFIED={spec['case_id']} eligible={record['eligible_event'] is not None}")

    selected = [item for item in qualifications if item.get("eligible_event") is not None]
    selection = manifest["selection"]
    selected = selected[: int(selection["maximum_selected_records"])]
    if len(selected) < int(selection["minimum_eligible_records"]):
        atomic_json(output / "qualification_summary.json", {
            "status": "blocked",
            "reason": "insufficient_real_vitis_eligible_records",
            "eligible": len(selected),
            "required": selection["minimum_eligible_records"],
            "provider_calls": 0,
            "vitis_launches": vitis_launches,
        })
        print("R2_REAL_CALIBRATION_STATUS=blocked")
        print("R2_REAL_CALIBRATION_REASON=insufficient_real_vitis_eligible_records")
        print("PROVIDER_CALLS=0")
        print(f"VITIS_LAUNCHES={vitis_launches}")
        print("R4_ACCEPTED=false")
        return 2

    protocol = freeze_calibration_protocol(
        "v23-r2-real-vitis-unsupported-construct-v1",
        [str(item["case_id"]) for item in selected],
    )
    policy = policy_from_manifest(manifest)
    for path, value in (
        (output / "frozen_protocol.json", protocol.to_dict()),
        (output / "frozen_policy.json", policy.to_dict()),
    ):
        if path.is_file():
            if json.loads(path.read_text(encoding="utf-8")) != value:
                raise RuntimeError("frozen calibration artifact mismatch on resume")
        else:
            atomic_json(path, value)

    calibration_records = []
    provider_calls = 0
    provider_identity = None
    hard_provider = int(manifest["hard_budgets"]["provider_calls"])
    for item in selected:
        case_id = str(item["case_id"])
        record_path = output / "provider_records" / f"{case_id}.json"
        if record_path.is_file():
            record = json.loads(record_path.read_text(encoding="utf-8"))
        else:
            if provider_calls >= hard_provider:
                raise RuntimeError("provider call hard budget exhausted")
            event = dict(item["eligible_event"])
            advisor_budget = BudgetManager(BudgetLimits(
                max_llm_calls=1,
                max_tokens=8192,
                max_cost_usd=10.0,
                max_wall_time_s=600,
            ))
            advisor = ProviderBackedShadowDiagnosticAdvisor(
                provider=runtime.registry.get_provider(runtime.effective_config.provider_name),
                model=runtime.effective_config.to_model_spec(),
                budget=advisor_budget,
                reserve=ShadowReserve(
                    max_calls=1,
                    max_tokens=8192,
                    max_cost_usd=10.0,
                    max_wall_time_s=600,
                ),
            )
            artifacts = run_shadow_diagnostics(
                [event],
                advisor=advisor,
                main_before=dict(item["main_snapshot"]),
                main_after=dict(item["main_snapshot"]),
            )
            if len(artifacts) != 1:
                raise RuntimeError("one calibration event must produce one artifact")
            artifact = artifacts[0]
            request = build_shadow_request(diagnostic_event_from_dict(event))
            record = {
                "record_id": case_id,
                "candidate_source_sha256": item["candidate_source_sha256"],
                "evidence_ids": list(request.evidence_ids),
                "advisory": artifact["advisory"],
                "truth": {
                    "owner": item["truth"]["owner"],
                    "failure_class": item["truth"]["failure_class"],
                },
                "shadow": artifact,
                "raw_provider_response_persisted": False,
                "private_reasoning_persisted": False,
            }
            atomic_json(record_path, record)
        calls = record.get("shadow", {}).get("accounting", {}).get("provider_calls")
        if calls != 1:
            raise RuntimeError("calibration provider record does not bind one call")
        provider_calls += 1
        artifact_identity = record["shadow"]["provider_identity"]
        if provider_identity is None:
            provider_identity = artifact_identity
        elif provider_identity != artifact_identity:
            raise RuntimeError("provider identity changed within calibration split")
        calibration_records.append(record)
        print(f"R2_CALIBRATION_PROVIDER_RECORD={case_id}")

    if provider_calls > hard_provider or provider_identity is None:
        raise RuntimeError("provider accounting or identity invalid")
    report = evaluate_calibration(calibration_records, protocol=protocol)
    certificate = certify_calibration(
        report,
        policy=policy,
        provider_identity=provider_identity,
    )
    bundle = {
        "schema_version": 1,
        "manifest_sha256": manifest_sha,
        "repository": identity,
        "protocol": protocol.to_dict(),
        "records": calibration_records,
        "report": report.to_dict(),
        "policy": policy.to_dict(),
        "provider_identity": provider_identity,
        "certificate": certificate.to_dict(),
        "qualification": {
            "pool_size": len(qualifications),
            "eligible_count": sum(item.get("eligible_event") is not None for item in qualifications),
            "selected_count": len(selected),
            "selection_before_provider_calls": True,
        },
        "execution": {
            "provider_calls": provider_calls,
            "vitis_launches": vitis_launches,
            "git_history_mutations": 0,
            "raw_provider_response_persisted": False,
            "private_reasoning_persisted": False,
            "hidden_content_exposed_to_model": False,
            "candidate_mutations": 0,
        },
    }
    atomic_json(output / "calibration_bundle.json", bundle)
    print("R2_REAL_CALIBRATION_STATUS=" + ("certificate_issued" if certificate.accepted else "inconclusive"))
    print("R2_REAL_CALIBRATION_REASON=" + ("none" if certificate.accepted else ",".join(certificate.reasons)))
    print(f"PROVIDER_CALLS={provider_calls}")
    print(f"VITIS_LAUNCHES={vitis_launches}")
    print(f"CALIBRATION_CERTIFICATE_ID={certificate.certificate_id}")
    print("R4_ACCEPTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
