#!/usr/bin/env python3
"""Run the frozen R5.1 P5 ordinary-refactor discovery campaign."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agrefactor.cli import build_parser
from agrefactor.product import run_source_command
from scripts import r5_1_source_baseline as p4


DEFAULT_PROTOCOL = ROOT / "configs" / "r5_1" / "ordinary_refactor_protocol.json"
STATE_PATH = ROOT / "docs" / "roadmap" / "V2_3_STATE.json"
ALLOWED_RAW = {"raw_pass_all", "raw_fail_actionable", "raw_fail_ambiguous"}
ALLOWED_OUTCOMES = {
    "public_refactor_lift_actionable",
    "public_refactor_lift_ambiguous",
    "raw_pass_control_preserved",
    "unnecessary_rewrite",
    "regression",
    "ordinary_refactor_failure",
    "infrastructure_failure",
}
FORBIDDEN_PERSISTED_KEYS = {
    "raw_provider_response",
    "private_reasoning",
    "chain_of_thought",
    "api_key_value",
}


class P5Error(RuntimeError):
    """Raised when P5 cannot proceed without weakening its frozen contract."""


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_sha256(value: str) -> str:
    return hashlib.sha256((value.rstrip() + "\n").encode("utf-8")).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise P5Error(f"JSON root must be an object: {path}")
    return value


def write_object(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        + "\n",
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
        raise P5Error("git command failed: " + " ".join(args))
    return completed.stdout.strip()


def is_ancestor(repo: Path, older: str, newer: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", older, newer],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def validate_protocol(protocol: Mapping[str, Any]) -> None:
    if protocol.get("schema_version") != 1 or protocol.get("route") != "V2.3-R5.1-P5":
        raise P5Error("unsupported P5 protocol")
    base = protocol.get("repository_base_head")
    if not isinstance(base, str) or re.fullmatch(r"[0-9a-f]{40}", base) is None:
        raise P5Error("repository_base_head must be a full git commit")
    product = protocol.get("product_contract")
    if not isinstance(product, Mapping):
        raise P5Error("product_contract is missing")
    expected_product = {
        "command": "refactor",
        "runtime_function": "agrefactor.product.run_source_command",
        "public_tests": "provided_frozen_p4_oracle",
        "hidden_tests": "none",
        "r5_arm": "A0",
        "r2_shadow": False,
        "r4_integration": False,
        "repair_memory": "none",
        "max_testbench_repairs": 1,
        "max_candidate_repairs": 1,
        "max_llm_calls_per_case": 11,
        "max_csim_calls_per_case": 3,
        "max_csynth_calls_per_case": 3,
        "max_cosim_calls_per_case": 2,
        "max_vitis_launches_per_case": 8,
    }
    for name, expected in expected_product.items():
        if product.get(name) != expected:
            raise P5Error(f"product contract changed: {name}")
    runtime = protocol.get("model_runtime")
    if not isinstance(runtime, Mapping) or runtime.get("api_key_env") != "DEEPSEEK_API_KEY":
        raise P5Error("model runtime is not frozen")
    if runtime.get("raw_response_persistence_allowed") is not False or runtime.get(
        "private_reasoning_persistence_allowed"
    ) is not False:
        raise P5Error("model persistence boundary is unsafe")
    cases = protocol.get("cases")
    if not isinstance(cases, list) or len(cases) != 13:
        raise P5Error("P5 must contain exactly thirteen discovery cases")
    case_ids = [item.get("case_id") for item in cases if isinstance(item, Mapping)]
    if len(case_ids) != len(cases) or len(case_ids) != len(set(case_ids)):
        raise P5Error("P5 case identities must be unique")
    for item in cases:
        if item.get("raw_classification") not in ALLOWED_RAW:
            raise P5Error("P5 case has an invalid raw classification")
        if item.get("eligible_as_pristine_p9_future") is not False:
            raise P5Error("P5 observation cannot remain a pristine P9 future case")
    budget = protocol.get("budget")
    if not isinstance(budget, Mapping):
        raise P5Error("P5 budget is missing")
    reserve = budget.get("campaign_reserve")
    phase = budget.get("p5_phase_upper_bound")
    hard = budget.get("hard_cap")
    carried = budget.get("carried_forward")
    if reserve != {"provider_calls": 143, "vitis_launches": 104}:
        raise P5Error("P5 reserve changed")
    if phase != {"provider_calls": 180, "vitis_launches": 110}:
        raise P5Error("P5 phase cap changed")
    if hard != {"provider_calls": 650, "vitis_launches": 650}:
        raise P5Error("R5.1 hard cap changed")
    if carried != {"provider_calls": 248, "vitis_launches": 198}:
        raise P5Error("P5 carried-forward ledger changed")
    if len(cases) * product["max_llm_calls_per_case"] != reserve["provider_calls"]:
        raise P5Error("P5 Provider reserve does not cover the frozen cohort")
    if len(cases) * product["max_vitis_launches_per_case"] != reserve["vitis_launches"]:
        raise P5Error("P5 Vitis reserve does not cover the frozen cohort")
    if any(reserve[name] > phase[name] for name in reserve):
        raise P5Error("P5 reserve exceeds its phase cap")
    if any(carried[name] + reserve[name] > hard[name] for name in reserve):
        raise P5Error("P5 reserve exceeds the cumulative hard cap")
    invariants = protocol.get("invariants")
    if not isinstance(invariants, Mapping) or any(
        invariants.get(name) is not False
        for name in (
            "post_outcome_case_selection_allowed",
            "manual_source_or_test_repair_allowed",
            "public_test_reused_as_hidden",
            "p5_cases_may_be_reused_as_pristine_p9_future",
            "raw_pass_may_count_as_refactor_lift",
            "r2_r4_r5_enabled",
            "git_history_mutations_allowed",
            "r5_accepted",
            "r6_started",
        )
    ):
        raise P5Error("P5 invariants changed")


def load_authority(repo: Path, protocol: Mapping[str, Any]) -> dict[str, Any]:
    authority = protocol["p4_authority"]
    plan_path = repo / str(authority["plan_path"])
    materializer_path = repo / str(authority["materializer_path"])
    result_path = Path(str(authority["result_path"]))
    audit_path = Path(str(authority["audit_path"]))
    expected_files = (
        (plan_path, authority["plan_file_sha256"]),
        (materializer_path, authority["materializer_file_sha256"]),
        (result_path, authority["result_file_sha256"]),
        (audit_path, authority["audit_file_sha256"]),
    )
    for path, expected in expected_files:
        if not path.is_file() or file_sha256(path) != expected:
            raise P5Error(f"P4 authority file mismatch: {path}")
    plan = load_object(plan_path)
    result = load_object(result_path)
    audit = load_object(audit_path)
    p4.validate_plan(plan)
    if p4.sha_value(plan) != authority["plan_sha256"]:
        raise P5Error("P4 plan identity mismatch")
    if result.get("status") != "complete" or result.get("result_sha256") != authority["result_sha256"]:
        raise P5Error("P4 result is not authoritative")
    if audit.get("status") != "passed" or audit.get("audit_sha256") != authority["audit_sha256"]:
        raise P5Error("P4 independent audit is not authoritative")
    planned = {item["case_id"]: item for item in plan["cases"]}
    observed = {item["case_id"]: item for item in result["cases"]}
    selected = {item["case_id"]: item for item in protocol["cases"]}
    if set(planned) != set(observed) or set(planned) != set(selected):
        raise P5Error("P4/P5 cohort identity mismatch")
    for case_id, selected_case in selected.items():
        if observed[case_id].get("classification") != selected_case["raw_classification"]:
            raise P5Error(f"raw classification changed: {case_id}")
    return {"plan": plan, "result": result, "audit": audit, "planned": planned, "observed": observed}


def rewrite_cpp_identifier(code: str, old: str, new: str) -> tuple[str, int]:
    """Replace one C/C++ identifier while preserving comments and literals."""

    if not re.fullmatch(r"[A-Za-z_]\w*", old) or not re.fullmatch(r"[A-Za-z_]\w*", new):
        raise P5Error("top names must be C/C++ identifiers")
    output: list[str] = []
    index = 0
    replacements = 0
    state = "code"
    while index < len(code):
        current = code[index]
        following = code[index + 1] if index + 1 < len(code) else ""
        if state == "code":
            if current == "/" and following == "/":
                output.extend((current, following))
                index += 2
                state = "line_comment"
                continue
            if current == "/" and following == "*":
                output.extend((current, following))
                index += 2
                state = "block_comment"
                continue
            if current in {'"', "'"}:
                output.append(current)
                index += 1
                state = "string" if current == '"' else "character"
                continue
            if current.isalpha() or current == "_":
                end = index + 1
                while end < len(code) and (code[end].isalnum() or code[end] == "_"):
                    end += 1
                token = code[index:end]
                if token == old:
                    token = new
                    replacements += 1
                output.append(token)
                index = end
                continue
            output.append(current)
            index += 1
            continue
        output.append(current)
        index += 1
        if state == "line_comment" and current == "\n":
            state = "code"
        elif state == "block_comment" and current == "*" and following == "/":
            output.append(following)
            index += 1
            state = "code"
        elif state in {"string", "character"}:
            if current == "\\" and index < len(code):
                output.append(code[index])
                index += 1
            elif (state == "string" and current == '"') or (
                state == "character" and current == "'"
            ):
                state = "code"
    if state in {"block_comment", "string", "character"}:
        raise P5Error("unterminated C/C++ lexical construct")
    return "".join(output), replacements


def _protocol_audit_authorized(
    repo: Path,
    protocol: Mapping[str, Any],
    audit_path: Path,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    if not audit_path.is_file():
        raise P5Error("P5 protocol audit is missing")
    audit = load_object(audit_path)
    if audit.get("status") != "passed" or audit.get("protocol_sha256") != canonical_sha256(protocol):
        raise P5Error("P5 protocol audit did not pass")
    if state.get("R5_1_P5_PROTOCOL_AUDIT_FILE_SHA256") != file_sha256(audit_path):
        raise P5Error("P5 protocol audit differs from roadmap state")
    if state.get("R5_1_P5_PROTOCOL_AUDIT_SHA256") != audit.get("audit_sha256"):
        raise P5Error("P5 protocol audit identity differs from roadmap state")
    audit_head = audit.get("repo_head")
    current = git(repo, "rev-parse", "HEAD")
    if not isinstance(audit_head, str) or not is_ancestor(repo, audit_head, current):
        raise P5Error("P5 protocol audit is not ancestral to the current repository")
    return audit


def _validate_repository(repo: Path, protocol: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, str]:
    head = git(repo, "rev-parse", "HEAD")
    branch = git(repo, "branch", "--show-current")
    status = git(repo, "status", "--porcelain", "--untracked-files=all")
    if branch != "research-roadmap-v2.3" or status:
        raise P5Error("P5 requires the clean research-roadmap-v2.3 branch")
    if not is_ancestor(repo, str(protocol["repository_base_head"]), head):
        raise P5Error("P5 repository does not descend from its frozen base")
    required = {
        "R4_ACCEPTED": True,
        "R5_STARTED": True,
        "R5_ACCEPTED": False,
        "R6_STARTED": False,
        "R5_1_P4_COMPLETE": True,
        "R5_1_REAL_CALLS_ALLOWED": True,
        "R5_1_CUMULATIVE_PROVIDER_CALLS": 248,
        "R5_1_CUMULATIVE_VITIS_LAUNCHES": 198,
        "R5_1_PROVIDER_CALL_HARD_CAP": 650,
        "R5_1_VITIS_LAUNCH_HARD_CAP": 650,
    }
    for name, expected in required.items():
        if state.get(name) != expected:
            raise P5Error(f"roadmap state does not authorize P5: {name}")
    return {"head": head, "branch": branch, "status": status}


def build_product_args(
    *,
    protocol: Mapping[str, Any],
    source: Path,
    public_test: Path,
    public_contract: Path,
    top: str,
    product_root: Path,
    run_id: str,
) -> argparse.Namespace:
    product = protocol["product_contract"]
    runtime = protocol["model_runtime"]
    argv = [
        "refactor",
        str(source),
        "--top",
        top,
        "--model",
        str(runtime["model"]),
        "--model-family",
        str(runtime["family"]),
        "--base-url",
        str(runtime["base_url"]),
        "--api-key-env",
        str(runtime["api_key_env"]),
        "--public-test",
        str(public_test),
        "--public-test-contract",
        str(public_contract),
        "--hidden-tests",
        "none",
        "--max-testbench-repairs",
        str(product["max_testbench_repairs"]),
        "--max-candidate-repairs",
        str(product["max_candidate_repairs"]),
        "--max-llm-calls",
        str(product["max_llm_calls_per_case"]),
        "--max-tool-calls",
        "64",
        "--max-compile-calls",
        "64",
        "--max-csim-calls",
        str(product["max_csim_calls_per_case"]),
        "--max-csynth-calls",
        str(product["max_csynth_calls_per_case"]),
        "--max-cosim-calls",
        str(product["max_cosim_calls_per_case"]),
        "--max-wall-time-s",
        str(product["max_wall_time_s_per_case"]),
        "--cosim-policy",
        str(product["cosim_policy"]),
        "--output-dir",
        str(product_root),
        "--run-id",
        run_id,
        "--json",
    ]
    args = build_parser().parse_args(argv)
    setattr(args, "_r5_arm_override", "A0")
    return args


def _usage(run_result: Mapping[str, Any], product: Mapping[str, Any]) -> dict[str, Any]:
    raw = run_result.get("budget_usage")
    if not isinstance(raw, Mapping):
        raise P5Error("product run lacks budget usage")
    names = ("llm_calls", "csim_calls", "csynth_calls", "cosim_calls")
    values: dict[str, int] = {}
    for name in names:
        value = raw.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise P5Error(f"invalid product budget usage: {name}")
        values[name] = value
    values["vitis_launches"] = sum(values[name] for name in names[1:])
    if values["llm_calls"] > product["max_llm_calls_per_case"]:
        raise P5Error("product exceeded the frozen Provider bound")
    if values["csim_calls"] > product["max_csim_calls_per_case"]:
        raise P5Error("product exceeded the frozen CSIM bound")
    if values["csynth_calls"] > product["max_csynth_calls_per_case"]:
        raise P5Error("product exceeded the frozen CSYNTH bound")
    if values["cosim_calls"] > product["max_cosim_calls_per_case"]:
        raise P5Error("product exceeded the frozen COSIM bound")
    return values


def _entrypoint_evidence(product_root: Path, run_result: Mapping[str, Any]) -> dict[str, Any]:
    plan = load_object(product_root / "bootstrap" / "test_source_plan.json")
    request = load_object(product_root / "bootstrap" / "formal_validation_request.json")
    metadata = run_result.get("metadata")
    phases = run_result.get("phases")
    phase_metadata = {}
    if isinstance(phases, list) and phases and isinstance(phases[0], Mapping):
        candidate = phases[0].get("metadata")
        if isinstance(candidate, Mapping):
            phase_metadata = dict(candidate)
    if not isinstance(metadata, Mapping):
        raise P5Error("product metadata is missing")
    hidden = plan.get("hidden")
    public = plan.get("public")
    binding = request.get("r5_binding")
    if (
        run_result.get("mode") != "refactor"
        or metadata.get("execution_mode") != "source_bootstrap"
        or not isinstance(public, Mapping)
        or public.get("mode") != "provided"
        or not isinstance(hidden, Mapping)
        or hidden.get("mode") != "none"
        or request.get("r5_arm") != "A0"
        or not isinstance(binding, Mapping)
        or binding.get("r4_integration_bound") is not False
        or binding.get("memory_payload_count") != 0
    ):
        raise P5Error("product run escaped the ordinary-refactor A0 boundary")
    return {
        "mode": run_result["mode"],
        "execution_mode": metadata["execution_mode"],
        "public_mode": public["mode"],
        "hidden_mode": hidden["mode"],
        "r5_arm": request["r5_arm"],
        "r4_integration_bound": binding["r4_integration_bound"],
        "memory_payload_count": binding["memory_payload_count"],
        "formal_validation_started": phase_metadata.get("formal_validation_started"),
        "orchestration_status": phase_metadata.get("orchestration_status"),
        "last_validation_state": phase_metadata.get("last_validation_state"),
        "failed_stage": phase_metadata.get("failed_stage"),
    }


def classify_outcome(raw: str, succeeded: bool, candidate_changed: bool | None, infrastructure: bool) -> str:
    if infrastructure:
        return "infrastructure_failure"
    if raw == "raw_pass_all":
        if not succeeded:
            return "regression"
        return "unnecessary_rewrite" if candidate_changed else "raw_pass_control_preserved"
    if succeeded and raw == "raw_fail_actionable":
        return "public_refactor_lift_actionable"
    if succeeded and raw == "raw_fail_ambiguous":
        return "public_refactor_lift_ambiguous"
    return "ordinary_refactor_failure"


def _evidence_inventory(root: Path, campaign_root: Path) -> list[dict[str, Any]]:
    allowed = {".json", ".jsonl", ".log", ".xml", ".rpt", ".cpp"}
    records = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file() or path.suffix not in allowed:
            continue
        records.append(
            {
                "path": path.relative_to(campaign_root).as_posix(),
                "sha256": file_sha256(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return records


def run_campaign(
    *,
    repo: Path,
    external_root: Path,
    output: Path,
    protocol_path: Path,
    protocol_audit_path: Path,
) -> dict[str, Any]:
    protocol = load_object(protocol_path)
    validate_protocol(protocol)
    if not protocol_path.samefile(repo / "configs" / "r5_1" / "ordinary_refactor_protocol.json"):
        raise P5Error("P5 must use the committed protocol path")
    state = load_object(repo / STATE_PATH.relative_to(ROOT))
    repository = _validate_repository(repo, protocol, state)
    authority = load_authority(repo, protocol)
    audit = _protocol_audit_authorized(repo, protocol, protocol_audit_path, state)
    runtime = protocol["model_runtime"]
    if not os.environ.get(str(runtime["api_key_env"])):
        raise P5Error("selected Provider credential is missing")
    if output.exists():
        raise P5Error("P5 output directory must not already exist")
    output.mkdir(parents=True)
    protocol_sha = canonical_sha256(protocol)
    namespace = canonical_sha256(
        {"protocol_sha256": protocol_sha, "output": str(output.resolve())}
    )[:12]
    selected = {item["case_id"]: item for item in protocol["cases"]}
    product = protocol["product_contract"]
    totals = {"provider_calls": 0, "vitis_launches": 0}
    cases: list[dict[str, Any]] = []
    consecutive_infrastructure_failures = 0
    for case_id in [item["case_id"] for item in protocol["cases"]]:
        case = authority["planned"][case_id]
        case_root = output / "cases" / case_id
        material_root = case_root / "material"
        material_root.mkdir(parents=True)
        material = p4.materialize_case(repo, external_root, case)
        source_path = material_root / "source.cpp"
        test_path = material_root / "public_test.cpp"
        contract_path = material_root / "public_contract.json"
        source_path.write_text(material["design"], encoding="utf-8")
        candidate_top = str(case["top"]) + str(product["candidate_top_suffix"])
        adapted_test, replacement_count = rewrite_cpp_identifier(
            material["testbench"], str(case["top"]), candidate_top
        )
        if replacement_count < 1:
            raise P5Error(f"Public adapter did not bind Candidate top: {case_id}")
        test_path.write_text(adapted_test, encoding="utf-8")
        write_object(contract_path, dict(case["runtime_contract"]))
        product_root = case_root / "product"
        run_id = f"r5-1-p5-{case_id}-{namespace}"
        run_result: dict[str, Any] | None = None
        error: str | None = None
        infrastructure = False
        try:
            args = build_product_args(
                protocol=protocol,
                source=source_path,
                public_test=test_path,
                public_contract=contract_path,
                top=str(case["top"]),
                product_root=product_root,
                run_id=run_id,
            )
            run_source_command(args)
            run_result = load_object(product_root / "run_result.json")
            usage = _usage(run_result, product)
            entrypoint = _entrypoint_evidence(product_root, run_result)
        except Exception as exc:
            error = type(exc).__name__ + ":" + str(exc)
            result_path = product_root / "run_result.json"
            if result_path.is_file():
                run_result = load_object(result_path)
                usage = _usage(run_result, product)
                entrypoint = _entrypoint_evidence(product_root, run_result)
            else:
                infrastructure = True
                usage = {
                    "llm_calls": product["max_llm_calls_per_case"],
                    "csim_calls": product["max_csim_calls_per_case"],
                    "csynth_calls": product["max_csynth_calls_per_case"],
                    "cosim_calls": product["max_cosim_calls_per_case"],
                    "vitis_launches": product["max_vitis_launches_per_case"],
                }
                entrypoint = None
        totals["provider_calls"] += int(usage["llm_calls"])
        totals["vitis_launches"] += int(usage["vitis_launches"])
        reserve = protocol["budget"]["campaign_reserve"]
        if any(totals[name] > reserve[name] for name in totals):
            raise P5Error("P5 campaign exceeded its frozen reserve")
        succeeded = bool(run_result is not None and run_result.get("succeeded") is True)
        final_candidate = product_root / "refactor" / "final_candidate.cpp"
        candidate_sha = file_sha256(final_candidate) if final_candidate.is_file() else None
        candidate_changed = (
            None
            if not final_candidate.is_file()
            else text_sha256(final_candidate.read_text(encoding="utf-8"))
            != text_sha256(material["design"])
        )
        outcome = classify_outcome(
            selected[case_id]["raw_classification"],
            succeeded,
            candidate_changed,
            infrastructure,
        )
        if outcome not in ALLOWED_OUTCOMES:
            raise AssertionError("unexpected P5 outcome")
        consecutive_infrastructure_failures = (
            consecutive_infrastructure_failures + 1 if infrastructure else 0
        )
        case_record = {
            "case_id": case_id,
            "source_id": case["source_id"],
            "algorithm_family": case["algorithm_family"],
            "p4_partition": case["partition"],
            "raw_classification": selected[case_id]["raw_classification"],
            "discovery_role": selected[case_id]["discovery_role"],
            "eligible_as_pristine_p9_future": False,
            "run_id": run_id,
            "material_identity": {
                **material["identity"],
                "adapted_public_test_sha256": text_sha256(adapted_test),
                "candidate_top": candidate_top,
                "top_identifier_replacement_count": replacement_count,
                "public_contract_sha256": canonical_sha256(case["runtime_contract"]),
            },
            "product_succeeded": succeeded,
            "product_status": None if run_result is None else run_result.get("status"),
            "terminal_stage": (
                "pre_entrypoint_exception"
                if entrypoint is None
                else entrypoint.get("last_validation_state")
                or entrypoint.get("failed_stage")
                or entrypoint.get("orchestration_status")
                or "unknown"
            ),
            "entrypoint_evidence": entrypoint,
            "candidate_sha256": candidate_sha,
            "candidate_changed": candidate_changed,
            "outcome": outcome,
            "error": error,
            "budget_usage": usage,
            "budget_accounting": (
                "conservative_per_case_upper_bound" if run_result is None else "authoritative_product_usage"
            ),
            "evidence_inventory": _evidence_inventory(case_root, output),
        }
        write_object(case_root / "p5_case_result.json", case_record)
        cases.append(case_record)
        write_object(
            output / "budget_ledger.json",
            {
                **totals,
                "campaign_reserve": reserve,
                "accounting": "actual_or_conservative_upper_bound",
            },
        )
        print(f"R5_1_P5_CASE={case_id} OUTCOME={outcome}", flush=True)
        if consecutive_infrastructure_failures >= 3:
            break
    repo_after = {
        "head": git(repo, "rev-parse", "HEAD"),
        "status": git(repo, "status", "--porcelain", "--untracked-files=all"),
    }
    outcome_counts = Counter(item["outcome"] for item in cases)
    result = {
        "schema_version": 1,
        "campaign_id": "v2.3-r5.1-p5-ordinary-refactor-v1",
        "status": "ready_for_independent_audit",
        "evidence_root": str(output.resolve()),
        "protocol_file_sha256": file_sha256(protocol_path),
        "protocol_sha256": protocol_sha,
        "protocol_audit_file_sha256": file_sha256(protocol_audit_path),
        "protocol_audit_sha256": audit["audit_sha256"],
        "repo_head_before": repository["head"],
        "repo_head_after": repo_after["head"],
        "cases": cases,
        "planned_case_count": len(protocol["cases"]),
        "executed_case_count": len(cases),
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "provider_calls": totals["provider_calls"],
        "vitis_launches": totals["vitis_launches"],
        "git_history_mutations": int(repository["head"] != repo_after["head"]),
        "working_tree_mutations": int(repository["status"] != repo_after["status"]),
        "stopped_after_consecutive_infrastructure_failures": consecutive_infrastructure_failures >= 3,
        "raw_provider_response_persisted": False,
        "private_reasoning_persisted": False,
        "hidden_evidence_claimed": False,
        "r5_accepted": False,
        "r6_started": False,
    }
    result["result_sha256"] = canonical_sha256(result)
    write_object(output / "result.json", result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--external-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--protocol-audit", type=Path, required=True)
    args = parser.parse_args(argv)
    result = run_campaign(
        repo=args.repo.resolve(),
        external_root=args.external_root.resolve(),
        output=args.output.resolve(),
        protocol_path=args.protocol.resolve(),
        protocol_audit_path=args.protocol_audit.resolve(),
    )
    print(f"R5_1_P5_STATUS={result['status']}")
    print(f"R5_1_P5_EXECUTED_CASES={result['executed_case_count']}")
    print(f"PROVIDER_CALLS={result['provider_calls']}")
    print(f"VITIS_LAUNCHES={result['vitis_launches']}")
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
