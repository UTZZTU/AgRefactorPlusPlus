#!/usr/bin/env python3
"""Independently audit the R5.1 P5 protocol or real campaign result."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import r5_1_source_baseline as p4


DEFAULT_PROTOCOL = ROOT / "configs" / "r5_1" / "ordinary_refactor_protocol.json"
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
FORBIDDEN_KEYS = {
    "raw_provider_response",
    "private_reasoning",
    "chain_of_thought",
    "api_key_value",
}


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
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def write_object(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def _git(repo: Path, *args: str) -> tuple[int, str]:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.returncode, completed.stdout.strip()


def _finding(findings: list[dict[str, str]], code: str, message: str, severity: str = "critical") -> None:
    findings.append({"code": code, "message": message, "severity": severity})


def validate_protocol_shape(protocol: Mapping[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if protocol.get("schema_version") != 1 or protocol.get("route") != "V2.3-R5.1-P5":
        _finding(findings, "protocol_identity_invalid", "P5 protocol identity is invalid")
    base = protocol.get("repository_base_head")
    if not isinstance(base, str) or re.fullmatch(r"[0-9a-f]{40}", base) is None:
        _finding(findings, "base_head_invalid", "P5 base head is not a full commit")
    product = protocol.get("product_contract")
    required_product = {
        "command": "refactor",
        "runtime_function": "agrefactor.product.run_source_command",
        "public_tests": "provided_frozen_p4_oracle",
        "hidden_tests": "none",
        "r5_arm": None,
        "r5_profile_selected": False,
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
        "cosim_policy": "required",
        "public_contract_port_binding": "positionally_remap_raw_parameters_and_preserve_explicit_public_global_ports",
    }
    if not isinstance(product, Mapping):
        _finding(findings, "product_contract_missing", "P5 product contract is missing")
        product = {}
    for name, expected in required_product.items():
        if product.get(name) != expected:
            _finding(findings, "product_contract_changed", f"P5 product field changed: {name}")
    runtime = protocol.get("model_runtime")
    if not isinstance(runtime, Mapping) or (
        runtime.get("model") != "deepseek-flash"
        or runtime.get("family") != "deepseek"
        or runtime.get("base_url") != "https://api.deepseek.com"
        or runtime.get("api_key_env") != "DEEPSEEK_API_KEY"
        or runtime.get("raw_response_persistence_allowed") is not False
        or runtime.get("private_reasoning_persistence_allowed") is not False
    ):
        _finding(findings, "model_runtime_changed", "P5 model or privacy contract changed")
    cases = protocol.get("cases")
    if not isinstance(cases, list) or len(cases) != 13:
        _finding(findings, "case_count_invalid", "P5 must freeze thirteen cases")
        cases = []
    ids = [item.get("case_id") for item in cases if isinstance(item, Mapping)]
    if len(ids) != len(cases) or len(ids) != len(set(ids)):
        _finding(findings, "case_identity_invalid", "P5 case IDs are missing or duplicated")
    for item in cases:
        if item.get("raw_classification") not in ALLOWED_RAW:
            _finding(findings, "raw_classification_invalid", str(item.get("case_id")))
        if item.get("eligible_as_pristine_p9_future") is not False:
            _finding(findings, "future_leakage_risk", str(item.get("case_id")))
    budget = protocol.get("budget")
    if not isinstance(budget, Mapping):
        _finding(findings, "budget_missing", "P5 budget is missing")
    else:
        reserve = budget.get("campaign_reserve")
        if reserve != {"provider_calls": 143, "vitis_launches": 104}:
            _finding(findings, "reserve_invalid", "P5 reserve must be 143/104")
        if budget.get("p5_phase_upper_bound") != {"provider_calls": 180, "vitis_launches": 110}:
            _finding(findings, "phase_cap_invalid", "P5 phase cap must be 180/110")
        if budget.get("hard_cap") != {"provider_calls": 650, "vitis_launches": 650}:
            _finding(findings, "hard_cap_invalid", "R5.1 hard cap must be 650/650")
        if budget.get("carried_forward") != {"provider_calls": 248, "vitis_launches": 198}:
            _finding(findings, "carried_ledger_invalid", "P5 must start from 248/198")
    invariants = protocol.get("invariants")
    false_fields = {
        "post_outcome_case_selection_allowed",
        "manual_source_or_test_repair_allowed",
        "public_test_reused_as_hidden",
        "p5_cases_may_be_reused_as_pristine_p9_future",
        "raw_pass_may_count_as_refactor_lift",
        "r2_r4_r5_enabled",
        "git_history_mutations_allowed",
        "r5_accepted",
        "r6_started",
    }
    if not isinstance(invariants, Mapping) or any(invariants.get(name) is not False for name in false_fields):
        _finding(findings, "invariants_invalid", "P5 fail-closed invariants changed")
    supersedes = protocol.get("supersedes")
    if not isinstance(supersedes, Mapping) or (
        supersedes.get("protocol_id") != "v2.3-r5.1-p5-ordinary-refactor-v1"
        or supersedes.get("failure_phase") != "pre_provider_product_contract_validation"
        or supersedes.get("actual_provider_calls") != 0
        or supersedes.get("actual_vitis_launches") != 0
        or supersedes.get("old_evidence_rewritten") is not False
    ):
        _finding(findings, "predecessor_reconciliation_invalid", "P5 v1 failed-run history is not preserved")
    failed_audits = protocol.get("failed_protocol_audits")
    if not isinstance(failed_audits, list) or len(failed_audits) != 1:
        _finding(findings, "failed_audit_history_invalid", "P5 must preserve its v2 failed protocol audit")
    else:
        failed = failed_audits[0]
        if not isinstance(failed, Mapping) or (
            failed.get("protocol_id") != "v2.3-r5.1-p5-ordinary-refactor-v2"
            or failed.get("provider_calls") != 0
            or failed.get("vitis_launches") != 0
            or failed.get("old_evidence_rewritten") is not False
        ):
            _finding(findings, "failed_audit_history_invalid", "P5 v2 audit identity changed")
    return findings


def _rewrite_identifier(code: str, old: str, new: str) -> tuple[str, int]:
    """Independent lexical rewrite used to reconstruct the Public adapter."""

    token = re.compile(r"[A-Za-z_]\w*")
    output: list[str] = []
    cursor = 0
    replacements = 0
    state = "code"
    while cursor < len(code):
        if state == "code" and code.startswith("//", cursor):
            output.append("//")
            cursor += 2
            state = "line"
            continue
        if state == "code" and code.startswith("/*", cursor):
            output.append("/*")
            cursor += 2
            state = "block"
            continue
        char = code[cursor]
        if state == "code" and char in {'"', "'"}:
            output.append(char)
            cursor += 1
            state = "double" if char == '"' else "single"
            continue
        if state == "code":
            match = token.match(code, cursor)
            if match:
                value = match.group(0)
                if value == old:
                    value = new
                    replacements += 1
                output.append(value)
                cursor = match.end()
                continue
        output.append(char)
        cursor += 1
        if state == "line" and char == "\n":
            state = "code"
        elif state == "block" and char == "*" and cursor < len(code) and code[cursor] == "/":
            output.append("/")
            cursor += 1
            state = "code"
        elif state in {"double", "single"}:
            if char == "\\" and cursor < len(code):
                output.append(code[cursor])
                cursor += 1
            elif (state == "double" and char == '"') or (state == "single" and char == "'"):
                state = "code"
    if state in {"block", "double", "single"}:
        raise ValueError("unterminated C/C++ lexical construct")
    return "".join(output), replacements


def _parameter_names(code: str, function: str) -> tuple[str, ...]:
    match = re.search(
        rf'^\s*(?:extern\s+"C"\s+)?[^#\n;{{}}]*'
        rf"\b{re.escape(function)}\s*\("
        rf"(?P<parameters>[^;{{}}]*)\)\s*(?:;|{{)",
        code,
        flags=re.MULTILINE,
    )
    if match is None:
        raise ValueError(f"top interface declaration not found: {function}")
    raw_parameters = match.group("parameters").strip()
    if not raw_parameters or raw_parameters == "void":
        return ()
    names: list[str] = []
    for raw in raw_parameters.split(","):
        parameter = raw.split("=", 1)[0].strip()
        name_match = re.search(
            r"\b([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*$",
            parameter,
        )
        if name_match is None:
            raise ValueError(f"unsupported top parameter: {parameter}")
        names.append(name_match.group(1))
    if len(names) != len(set(names)):
        raise ValueError("top interface repeats a parameter name")
    return tuple(names)


def _global_names(code: str) -> tuple[str, ...]:
    names: list[str] = []
    for match in re.finditer(
        r'^\s*extern\s+(?!")(?P<declaration>[^#\n;(){}]+)\s*;',
        code,
        flags=re.MULTILINE,
    ):
        declaration = match.group("declaration").strip()
        if "," in declaration or "=" in declaration:
            continue
        name_match = re.search(
            r"\b([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*$",
            declaration,
        )
        if name_match is not None:
            names.append(name_match.group(1))
    if len(names) != len(set(names)):
        raise ValueError("Candidate Public ABI repeats a global port name")
    return tuple(names)


def _adapt_depths(
    raw_design: str,
    adapted_test: str,
    raw_top: str,
    candidate_top: str,
    raw_depths: Mapping[str, Any],
) -> dict[str, int]:
    raw_names = _parameter_names(raw_design, raw_top)
    public_names = _parameter_names(adapted_test, candidate_top)
    if len(raw_names) != len(public_names):
        raise ValueError("raw and Candidate Public ABI arity differ")
    public_globals = set(_global_names(adapted_test))
    positions = {name: index for index, name in enumerate(raw_names)}
    result: dict[str, int] = {}
    for name, depth in raw_depths.items():
        if name in positions:
            target = public_names[positions[name]]
        elif name in public_globals:
            target = name
        else:
            raise ValueError(f"raw depth port is absent: {name}")
        if target in result:
            raise ValueError("depth mapping is not one-to-one")
        result[target] = int(depth)
    return result


def _host_outcome(returncode: int, stdout: str, marker: str, mismatch_codes: set[int]) -> str:
    marker_seen = marker in stdout
    if returncode == 0 and marker_seen:
        return "oracle_pass"
    if returncode in mismatch_codes and not marker_seen:
        return "candidate_mismatch"
    if returncode == 0:
        return "pass_marker_missing"
    if marker_seen:
        return "nonzero_with_pass_marker"
    return "unexpected_failure"


def _authority(
    repo: Path,
    protocol: Mapping[str, Any],
    findings: list[dict[str, str]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    authority = protocol.get("p4_authority")
    if not isinstance(authority, Mapping):
        _finding(findings, "p4_authority_missing", "P4 authority is missing")
        return None, None
    paths = {
        "plan": repo / str(authority.get("plan_path", "")),
        "materializer": repo / str(authority.get("materializer_path", "")),
        "result": Path(str(authority.get("result_path", ""))),
        "audit": Path(str(authority.get("audit_path", ""))),
    }
    for name, path in paths.items():
        expected = authority.get(f"{name}_file_sha256")
        if not path.is_file() or file_sha256(path) != expected:
            _finding(findings, "p4_authority_file_mismatch", f"{name}:{path}")
            return None, None
    try:
        plan = load_object(paths["plan"])
        result = load_object(paths["result"])
        audit = load_object(paths["audit"])
        p4.validate_plan(plan)
    except Exception as exc:
        _finding(findings, "p4_authority_unreadable", str(exc))
        return None, None
    if p4.sha_value(plan) != authority.get("plan_sha256"):
        _finding(findings, "p4_plan_identity_mismatch", "P4 plan hash changed")
    if result.get("status") != "complete" or result.get("result_sha256") != authority.get("result_sha256"):
        _finding(findings, "p4_result_identity_mismatch", "P4 result is not authoritative")
    if audit.get("status") != "passed" or audit.get("audit_sha256") != authority.get("audit_sha256"):
        _finding(findings, "p4_audit_identity_mismatch", "P4 audit is not authoritative")
    planned = {item["case_id"]: item for item in plan["cases"]}
    observed = {item["case_id"]: item for item in result.get("cases", []) if isinstance(item, Mapping)}
    selected = {item["case_id"]: item for item in protocol.get("cases", []) if isinstance(item, Mapping)}
    if set(planned) != set(observed) or set(planned) != set(selected):
        _finding(findings, "cohort_mismatch", "P4 and P5 case sets differ")
    else:
        for case_id, item in selected.items():
            if observed[case_id].get("classification") != item.get("raw_classification"):
                _finding(findings, "raw_classification_mismatch", case_id)
    return plan, result


def _audit_superseded_run(
    protocol: Mapping[str, Any], findings: list[dict[str, str]]
) -> None:
    predecessor = protocol.get("supersedes")
    if not isinstance(predecessor, Mapping):
        return
    root = Path(str(predecessor.get("failed_run_root", "")))
    result_path = root / "result.json"
    if (
        not result_path.is_file()
        or file_sha256(result_path) != predecessor.get("failed_result_file_sha256")
    ):
        _finding(findings, "predecessor_result_mismatch", str(result_path))
        return
    result = load_object(result_path)
    if result.get("result_sha256") != predecessor.get("failed_result_sha256"):
        _finding(findings, "predecessor_result_identity_mismatch", str(result_path))
        return
    cases = result.get("cases")
    if not isinstance(cases, list) or len(cases) != 3:
        _finding(findings, "predecessor_case_count_invalid", "v1 must contain three pre-entry failures")
        return
    admitted_errors = (
        "ValueError:COSIM interface depth port(s) are absent",
        "ValueError:selected R5 arm requires a complete internal runtime binding",
    )
    for case in cases:
        if (
            not isinstance(case, Mapping)
            or case.get("terminal_stage") != "pre_entrypoint_exception"
            or case.get("budget_accounting") != "conservative_per_case_upper_bound"
            or not isinstance(case.get("error"), str)
            or not case["error"].startswith(admitted_errors)
        ):
            _finding(findings, "predecessor_failure_not_pre_provider", str(case))
            continue
        product = root / "cases" / str(case.get("case_id")) / "product"
        if product.exists() or (product / "run_result.json").exists():
            _finding(findings, "predecessor_product_execution_observed", str(product))
    if result.get("provider_calls") != predecessor.get("conservative_runner_provider_charge"):
        _finding(findings, "predecessor_conservative_provider_charge_mismatch", "v1")
    if result.get("vitis_launches") != predecessor.get("conservative_runner_vitis_charge"):
        _finding(findings, "predecessor_conservative_vitis_charge_mismatch", "v1")


def _audit_failed_protocol_history(
    protocol: Mapping[str, Any], findings: list[dict[str, str]]
) -> None:
    for record in protocol.get("failed_protocol_audits", []):
        if not isinstance(record, Mapping):
            continue
        path = Path(str(record.get("path", "")))
        if not path.is_file() or file_sha256(path) != record.get("file_sha256"):
            _finding(findings, "failed_protocol_audit_file_mismatch", str(path))
            continue
        audit = load_object(path)
        codes = {
            item.get("code")
            for item in audit.get("findings", [])
            if isinstance(item, Mapping)
        }
        if (
            audit.get("status") != "failed"
            or audit.get("audit_sha256") != record.get("audit_sha256")
            or audit.get("provider_calls") != 0
            or audit.get("vitis_launches") != 0
            or "adapter_preflight_failed" not in codes
        ):
            _finding(findings, "failed_protocol_audit_identity_mismatch", str(path))


def audit_protocol(
    repo: Path,
    external_root: Path,
    protocol_path: Path,
) -> dict[str, Any]:
    protocol = load_object(protocol_path)
    findings = validate_protocol_shape(protocol)
    returncode, head = _git(repo, "rev-parse", "HEAD")
    status_code, status_text = _git(repo, "status", "--porcelain", "--untracked-files=all")
    branch_code, branch = _git(repo, "branch", "--show-current")
    if returncode or status_code or branch_code or branch != "research-roadmap-v2.3" or status_text:
        _finding(findings, "repository_not_clean", "P5 protocol audit requires the clean research branch")
    base = protocol.get("repository_base_head")
    if isinstance(base, str):
        ancestor = subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", base, head],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        if ancestor:
            _finding(findings, "base_head_not_ancestor", "P5 base is not ancestral")
    plan, _ = _authority(repo, protocol, findings)
    _audit_superseded_run(protocol, findings)
    _audit_failed_protocol_history(protocol, findings)
    compiler = shutil.which("g++")
    if compiler is None:
        _finding(findings, "compiler_missing", "g++ is required for zero-call adapter audit")
    case_audits: list[dict[str, Any]] = []
    if plan is not None and compiler is not None:
        with tempfile.TemporaryDirectory(prefix="r5-1-p5-protocol-") as temporary:
            root = Path(temporary)
            selected = {item["case_id"]: item for item in protocol["cases"]}
            for case in plan["cases"]:
                case_id = case["case_id"]
                try:
                    material = p4.materialize_case(repo, external_root, case)
                    candidate_top = case["top"] + protocol["product_contract"]["candidate_top_suffix"]
                    adapted_source, source_replacements = _rewrite_identifier(
                        material["design"], case["top"], candidate_top
                    )
                    adapted_test, test_replacements = _rewrite_identifier(
                        material["testbench"], case["top"], candidate_top
                    )
                    if source_replacements < 1 or test_replacements < 1:
                        raise ValueError("Candidate top was not bound in source and Public test")
                    adapted_depths = _adapt_depths(
                        material["design"],
                        adapted_test,
                        case["top"],
                        candidate_top,
                        case["runtime_contract"]["cosim_interface_depths"],
                    )
                    public_symbols = set(_parameter_names(adapted_test, candidate_top))
                    public_symbols.update(_global_names(adapted_test))
                    if not set(adapted_depths).issubset(public_symbols):
                        raise ValueError("adapted depth ports are absent from Candidate ABI")
                    case_root = root / case_id
                    case_root.mkdir()
                    source_path = case_root / "source.cpp"
                    test_path = case_root / "public_test.cpp"
                    binary = case_root / "host_oracle"
                    source_path.write_text(adapted_source, encoding="utf-8")
                    test_path.write_text(adapted_test, encoding="utf-8")
                    compiled = subprocess.run(
                        [compiler, "-std=c++17", "-O0", "-Wno-unknown-pragmas", str(source_path), str(test_path), "-o", str(binary)],
                        cwd=case_root,
                        check=False,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=120,
                    )
                    if compiled.returncode:
                        raise ValueError("adapted host compile failed")
                    executed = subprocess.run(
                        [str(binary)],
                        cwd=case_root,
                        check=False,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=120,
                    )
                    observed = _host_outcome(
                        executed.returncode,
                        executed.stdout,
                        case["pass_marker"],
                        set(case["runtime_contract"]["candidate_mismatch_returncodes"]),
                    )
                    expected = case["host_preflight_expected"]
                    if observed != expected:
                        raise ValueError(f"adapted host outcome {observed} != {expected}")
                    if selected[case_id]["raw_classification"] not in ALLOWED_RAW:
                        raise ValueError("selected raw classification is invalid")
                    case_audits.append(
                        {
                            "case_id": case_id,
                            "status": "passed",
                            "expected_host_outcome": expected,
                            "observed_host_outcome": observed,
                            "source_replacement_count": source_replacements,
                            "test_replacement_count": test_replacements,
                            "adapted_source_sha256": text_sha256(adapted_source),
                            "adapted_public_test_sha256": text_sha256(adapted_test),
                            "adapted_cosim_depths": adapted_depths,
                        }
                    )
                except Exception as exc:
                    _finding(findings, "adapter_preflight_failed", f"{case_id}:{exc}")
                    case_audits.append({"case_id": case_id, "status": "failed", "reason": str(exc)})
    critical = sum(item["severity"] == "critical" for item in findings)
    result = {
        "schema_version": 1,
        "audit_id": "v2.3-r5.1-p5-protocol-audit-v1",
        "status": "passed" if critical == 0 else "failed",
        "repo_head": head,
        "protocol_file_sha256": file_sha256(protocol_path),
        "protocol_sha256": canonical_sha256(protocol),
        "case_audits": case_audits,
        "findings": findings,
        "critical_findings": critical,
        "warning_findings": sum(item["severity"] == "warning" for item in findings),
        "provider_calls": 0,
        "vitis_launches": 0,
        "git_history_mutations": 0,
        "r5_accepted": False,
        "r6_started": False,
    }
    result["audit_sha256"] = canonical_sha256(result)
    return result


def _scan_forbidden(value: Any, findings: list[dict[str, str]], location: str) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if key in FORBIDDEN_KEYS and nested not in (None, False, "", [], {}):
                _finding(findings, "private_payload_persisted", f"{location}:{key}")
            _scan_forbidden(nested, findings, f"{location}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _scan_forbidden(nested, findings, f"{location}[{index}]")


def _reclassify(raw: str, succeeded: bool, changed: bool | None, infrastructure: bool) -> str:
    if infrastructure:
        return "infrastructure_failure"
    if raw == "raw_pass_all":
        if not succeeded:
            return "regression"
        return "unnecessary_rewrite" if changed else "raw_pass_control_preserved"
    if succeeded:
        return "public_refactor_lift_actionable" if raw == "raw_fail_actionable" else "public_refactor_lift_ambiguous"
    return "ordinary_refactor_failure"


def audit_result(repo: Path, protocol_path: Path, protocol_audit_path: Path, result_path: Path) -> dict[str, Any]:
    protocol = load_object(protocol_path)
    protocol_audit = load_object(protocol_audit_path)
    result = load_object(result_path)
    findings = validate_protocol_shape(protocol)
    if protocol_audit.get("status") != "passed" or protocol_audit.get("protocol_sha256") != canonical_sha256(protocol):
        _finding(findings, "protocol_audit_invalid", "P5 protocol audit is not authoritative")
    stored_seal = result.get("result_sha256")
    unsigned = {key: value for key, value in result.items() if key != "result_sha256"}
    if stored_seal != canonical_sha256(unsigned):
        _finding(findings, "result_seal_mismatch", "P5 result seal is invalid")
    if result.get("protocol_file_sha256") != file_sha256(protocol_path):
        _finding(findings, "protocol_file_mismatch", "P5 result used another protocol file")
    if result.get("protocol_audit_file_sha256") != file_sha256(protocol_audit_path):
        _finding(findings, "protocol_audit_file_mismatch", "P5 result used another audit file")
    root = result_path.parent.resolve()
    planned = {item["case_id"]: item for item in protocol["cases"]}
    cases = result.get("cases")
    observed = {item.get("case_id"): item for item in cases if isinstance(item, Mapping)} if isinstance(cases, list) else {}
    if set(observed) != set(planned) or result.get("executed_case_count") != len(planned):
        _finding(findings, "campaign_incomplete", "P5 did not execute its complete frozen cohort")
    recomputed_totals = {"provider_calls": 0, "vitis_launches": 0}
    recomputed_outcomes: Counter[str] = Counter()
    credential = os.environ.get(str(protocol["model_runtime"]["api_key_env"]), "")
    for case_id, planned_case in planned.items():
        record = observed.get(case_id)
        if not isinstance(record, Mapping):
            continue
        if record.get("eligible_as_pristine_p9_future") is not False:
            _finding(findings, "future_leakage_claim", case_id)
        inventory = record.get("evidence_inventory")
        if not isinstance(inventory, list):
            _finding(findings, "evidence_inventory_missing", case_id)
            inventory = []
        seen_paths: set[str] = set()
        for item in inventory:
            if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
                _finding(findings, "evidence_record_invalid", case_id)
                continue
            candidate = (root / item["path"]).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                _finding(findings, "evidence_path_escape", f"{case_id}:{item['path']}")
                continue
            if item["path"] in seen_paths or not candidate.is_file() or candidate.is_symlink():
                _finding(findings, "evidence_file_invalid", f"{case_id}:{item['path']}")
                continue
            seen_paths.add(item["path"])
            if file_sha256(candidate) != item.get("sha256") or candidate.stat().st_size != item.get("size_bytes"):
                _finding(findings, "evidence_hash_mismatch", f"{case_id}:{item['path']}")
            if candidate.suffix == ".json":
                try:
                    _scan_forbidden(load_object(candidate), findings, item["path"])
                except Exception as exc:
                    _finding(findings, "json_evidence_invalid", f"{item['path']}:{exc}")
            if credential and credential.encode("utf-8") in candidate.read_bytes():
                _finding(findings, "credential_value_persisted", item["path"])
        usage = record.get("budget_usage")
        if not isinstance(usage, Mapping):
            _finding(findings, "budget_usage_missing", case_id)
            continue
        llm = usage.get("llm_calls")
        vitis = usage.get("vitis_launches")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (llm, vitis)):
            _finding(findings, "budget_usage_invalid", case_id)
            continue
        recomputed_totals["provider_calls"] += llm
        recomputed_totals["vitis_launches"] += vitis
        if llm > protocol["product_contract"]["max_llm_calls_per_case"] or vitis > protocol["product_contract"]["max_vitis_launches_per_case"]:
            _finding(findings, "per_case_budget_exceeded", case_id)
        product_root = root / "cases" / case_id / "product"
        run_path = product_root / "run_result.json"
        infrastructure = not run_path.is_file()
        succeeded = False
        changed: bool | None = None
        if run_path.is_file():
            run = load_object(run_path)
            succeeded = run.get("succeeded") is True
            bootstrap_plan = load_object(product_root / "bootstrap" / "test_source_plan.json")
            formal = load_object(product_root / "bootstrap" / "formal_validation_request.json")
            public = bootstrap_plan.get("public")
            hidden = bootstrap_plan.get("hidden")
            if (
                run.get("mode") != "refactor"
                or not isinstance(public, Mapping)
                or public.get("mode") != "provided"
                or not isinstance(hidden, Mapping)
                or hidden.get("mode") != "none"
                or formal.get("r5_arm") is not None
                or formal.get("r5_binding") is not None
            ):
                _finding(findings, "entrypoint_boundary_violated", case_id)
            raw_usage = run.get("budget_usage")
            if isinstance(raw_usage, Mapping):
                actual_llm = raw_usage.get("llm_calls")
                actual_vitis = sum(
                    raw_usage.get(name, -1000000)
                    for name in ("csim_calls", "csynth_calls", "cosim_calls")
                )
                if llm != actual_llm or vitis != actual_vitis:
                    _finding(findings, "budget_recomputation_mismatch", case_id)
            final_candidate = product_root / "refactor" / "final_candidate.cpp"
            source = root / "cases" / case_id / "material" / "source.cpp"
            if final_candidate.is_file() and source.is_file():
                changed = text_sha256(final_candidate.read_text(encoding="utf-8")) != text_sha256(source.read_text(encoding="utf-8"))
        outcome = _reclassify(planned_case["raw_classification"], succeeded, changed, infrastructure)
        recomputed_outcomes[outcome] += 1
        if record.get("outcome") != outcome or outcome not in ALLOWED_OUTCOMES:
            _finding(findings, "outcome_mismatch", f"{case_id}:{record.get('outcome')}!={outcome}")
        if record.get("candidate_changed") != changed:
            _finding(findings, "candidate_change_mismatch", case_id)
    reserve = protocol["budget"]["campaign_reserve"]
    if recomputed_totals != {
        "provider_calls": result.get("provider_calls"),
        "vitis_launches": result.get("vitis_launches"),
    }:
        _finding(findings, "campaign_budget_mismatch", "P5 aggregate budget does not recompute")
    if any(recomputed_totals[name] > reserve[name] for name in recomputed_totals):
        _finding(findings, "campaign_budget_exceeded", str(recomputed_totals))
    if dict(sorted(recomputed_outcomes.items())) != result.get("outcome_counts"):
        _finding(findings, "outcome_counts_mismatch", "P5 outcome counts do not recompute")
    for name in ("git_history_mutations", "working_tree_mutations"):
        if result.get(name) != 0:
            _finding(findings, "repository_mutation", name)
    if result.get("hidden_evidence_claimed") is not False:
        _finding(findings, "hidden_claim_invalid", "P5 is explicitly Public-only")
    if result.get("r5_accepted") is not False or result.get("r6_started") is not False:
        _finding(findings, "roadmap_boundary_violated", "P5 cannot accept R5 or start R6")
    critical = sum(item["severity"] == "critical" for item in findings)
    audit = {
        "schema_version": 1,
        "audit_id": "v2.3-r5.1-p5-result-audit-v1",
        "status": "passed" if critical == 0 else "failed",
        "protocol_sha256": canonical_sha256(protocol),
        "protocol_audit_sha256": protocol_audit.get("audit_sha256"),
        "result_file_sha256": file_sha256(result_path),
        "result_sha256": result.get("result_sha256"),
        "recomputed_provider_calls": recomputed_totals["provider_calls"],
        "recomputed_vitis_launches": recomputed_totals["vitis_launches"],
        "recomputed_outcome_counts": dict(sorted(recomputed_outcomes.items())),
        "findings": findings,
        "critical_findings": critical,
        "warning_findings": sum(item["severity"] == "warning" for item in findings),
        "provider_calls": 0,
        "vitis_launches": 0,
        "git_history_mutations": 0,
        "r5_accepted": False,
        "r6_started": False,
    }
    audit["audit_sha256"] = canonical_sha256(audit)
    return audit


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    protocol_parser = subparsers.add_parser("protocol")
    protocol_parser.add_argument("--repo", type=Path, default=ROOT)
    protocol_parser.add_argument("--external-root", type=Path, required=True)
    protocol_parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    protocol_parser.add_argument("--output", type=Path, required=True)
    result_parser = subparsers.add_parser("result")
    result_parser.add_argument("--repo", type=Path, default=ROOT)
    result_parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    result_parser.add_argument("--protocol-audit", type=Path, required=True)
    result_parser.add_argument("--result", type=Path, required=True)
    result_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "protocol":
        audit = audit_protocol(args.repo.resolve(), args.external_root.resolve(), args.protocol.resolve())
        label = "R5_1_P5_PROTOCOL_AUDIT"
    else:
        audit = audit_result(
            args.repo.resolve(),
            args.protocol.resolve(),
            args.protocol_audit.resolve(),
            args.result.resolve(),
        )
        label = "R5_1_P5_RESULT_AUDIT"
    write_object(args.output.resolve(), audit)
    print(f"{label}_STATUS={audit['status']}")
    print(f"CRITICAL_FINDINGS={audit['critical_findings']}")
    print(f"WARNING_FINDINGS={audit['warning_findings']}")
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    print("R5_ACCEPTED=false")
    print("R6_STARTED=false")
    return 0 if audit["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
