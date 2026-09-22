#!/usr/bin/env python3
"""Run the frozen Stage III internal-inheritance campaign.

This script governs inputs and evidence around the existing product entry point.
It does not implement another refactor or Vitis execution path.
"""

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
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agrefactor.compat.legacy_refactor import build_effective_legacy_llm_config
from agrefactor.config import (
    EvaluationSplit,
    RunMode,
    TaskSpec,
    TestSuiteSpec,
    resolve_target_profile,
)
from agrefactor.models import ModelCallRole, resolve_model_runtime
from agrefactor.runtime import (
    BudgetLimits,
    BudgetManager,
    CandidateValidationPlanRequest,
    LocalCandidateValidationHandlerFactory,
    RunContext,
    TraceRecorder,
    ValidationOrchestrator,
)
from flow.base_agent import HLSAgentLoader
from flow.inflight_tb.checks import extract_hls_decl_from_tb, synthesize_echo_stub
from flow.tools import tb_optimizer
from flow.tools.testbench import _build_testbench_request
from scripts.inheritance_stage2_preflight import canonical_sha256, file_sha256
from scripts.r5_1_source_baseline import (
    REFERENCE_STUB,
    _physical_for_state,
    _stage_statuses,
    classify_terminal,
)


DEFAULT_INVENTORY = ROOT / "configs" / "inheritance" / "inheritance_manifest_v1.json"
DEFAULT_PROTOCOL = ROOT / "configs" / "inheritance" / "stage3" / "protocol.json"
TARGET_PATH = ROOT / "configs" / "targets" / "vitis-2023.2-default.json"
EXECUTION = {
    "candidate_top_suffix": "_hls",
    "cosim_policy": "required",
    "hidden_tests": "none",
    "memory_enabled": False,
    "mode": "refactor",
    "optimize_enabled": False,
    "r2_r4_enabled": False,
}
MODEL = {
    "api_key_env": "DEEPSEEK_API_KEY",
    "base_url": "https://api.deepseek.com",
    "family": "deepseek",
    "name": "deepseek-flash",
}
HISTORICAL_STAGE2 = {"provider_calls": 60, "vitis_launches": 23}
CARRIED_STAGE2B = {"provider_calls": 18, "vitis_launches": 14}
FORBIDDEN_PERSISTED_KEYS = {
    "raw_provider_response",
    "private_reasoning",
    "chain_of_thought",
    "api_key_value",
}


class Stage3Error(RuntimeError):
    pass


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise Stage3Error(f"JSON root must be an object: {path}")
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
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    if completed.returncode:
        raise Stage3Error(completed.stderr.strip() or "git command failed")
    return completed.stdout.strip()


def ensure_empty(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise Stage3Error(f"output must be empty or absent: {path}")
    path.mkdir(parents=True, exist_ok=True)


def safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.")
    if not cleaned:
        raise Stage3Error("case id cannot be normalized")
    return cleaned.lower()


def evidence_inventory(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        records.append(
            {
                "relative_path": path.relative_to(root).as_posix(),
                "sha256": file_sha256(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return records


def _assert_safe_json(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).casefold() in FORBIDDEN_PERSISTED_KEYS:
                raise Stage3Error(f"forbidden persisted key: {path}.{key}")
            _assert_safe_json(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_safe_json(item, f"{path}[{index}]")


def prepare_protocol(repo: Path, inventory_path: Path, output: Path) -> dict[str, Any]:
    inventory = load_object(inventory_path)
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in inventory.get("files", []):
        if not isinstance(item, Mapping):
            continue
        if item.get("inheritance_testable") is not True:
            continue
        if item.get("source_family") == "heterorefactor":
            continue
        source_path = str(item.get("path", ""))
        top = str(item.get("top", ""))
        source = repo / source_path
        if not source.is_file() or not top:
            raise Stage3Error(f"inventory case is incomplete: {source_path}")
        case_id = safe_id(source_path.removeprefix("src/").rsplit(".", 1)[0])
        if case_id in seen:
            raise Stage3Error(f"duplicate case id: {case_id}")
        seen.add(case_id)
        cases.append(
            {
                "algorithm_family": item.get("algorithm_family"),
                "case_id": case_id,
                "d3_family_members": list(item.get("d3_family_members", [])),
                "dependencies_resolvable": item.get("dependencies", {}).get("resolvable") is True,
                "duplicate_refs": list(item.get("duplicate_refs", [])),
                "source_family": item.get("source_family"),
                "source_path": source_path,
                "source_sha256": file_sha256(source),
                "top": top,
            }
        )
    counts = Counter(item["source_family"] for item in cases)
    if counts != Counter({"app": 13, "c2hlsc": 9, "hlsrewritter": 20, "leetcode": 10, "opt": 4}):
        raise Stage3Error(f"unexpected Stage III cohort: {dict(counts)}")
    protocol: dict[str, Any] = {
        "budget": {"provider_calls_hard_cap": 600, "vitis_launches_hard_cap": 600},
        "carried_stage2b": CARRIED_STAGE2B,
        "cases": cases,
        "cohort_counts": dict(sorted(counts.items())),
        "execution": EXECUTION,
        "historical_stage2": HISTORICAL_STAGE2,
        "inventory": {
            "inventory_id": inventory.get("inventory_id"),
            "inventory_sha256": file_sha256(inventory_path),
            "path": inventory_path.relative_to(repo).as_posix(),
        },
        "model": MODEL,
        "protocol_id": "v2.3-inheritance-first-stage3-v1",
        "route": "V2.3-INHERITANCE-FIRST-STAGE-III",
        "schema_version": 1,
        "target_profile": {
            "name": "vitis-2023.2-default",
            "path": TARGET_PATH.relative_to(repo).as_posix(),
            "sha256": file_sha256(TARGET_PATH),
        },
        "timeouts_s": {"cosim": 1200, "csim": 180, "csynth": 900, "host": 120},
    }
    protocol["cohort_sha256"] = canonical_sha256(cases)
    write_object(output, protocol)
    return protocol


def validate_protocol(repo: Path, protocol_path: Path) -> dict[str, Any]:
    protocol = load_object(protocol_path)
    if protocol.get("schema_version") != 1 or protocol.get("route") != "V2.3-INHERITANCE-FIRST-STAGE-III":
        raise Stage3Error("unsupported Stage III protocol")
    if protocol.get("execution") != EXECUTION or protocol.get("model") != MODEL:
        raise Stage3Error("Stage III execution boundary changed")
    if protocol.get("budget") != {"provider_calls_hard_cap": 600, "vitis_launches_hard_cap": 600}:
        raise Stage3Error("Stage III budget changed")
    if protocol.get("carried_stage2b") != CARRIED_STAGE2B:
        raise Stage3Error("Stage II-B carried ledger changed")
    cases = protocol.get("cases")
    if not isinstance(cases, list) or len(cases) != 56:
        raise Stage3Error("Stage III must contain exactly 56 cases")
    if canonical_sha256(cases) != protocol.get("cohort_sha256"):
        raise Stage3Error("Stage III cohort hash mismatch")
    ids: set[str] = set()
    for item in cases:
        case_id = item.get("case_id")
        if not isinstance(case_id, str) or case_id in ids:
            raise Stage3Error("Stage III case identities are not unique")
        ids.add(case_id)
        source = repo / str(item.get("source_path"))
        if not source.is_file() or file_sha256(source) != item.get("source_sha256"):
            raise Stage3Error(f"source identity mismatch: {case_id}")
        if not isinstance(item.get("top"), str) or not item["top"]:
            raise Stage3Error(f"missing top: {case_id}")
    target = protocol["target_profile"]
    if file_sha256(repo / target["path"]) != target["sha256"]:
        raise Stage3Error("target profile hash mismatch")
    return protocol


def _extract_response_content(response: Any) -> str:
    response.process()
    messages = getattr(response, "messages", None)
    if not isinstance(messages, list) or not messages or not isinstance(messages[-1], Mapping):
        raise Stage3Error("testbench agent returned no terminal message")
    content = messages[-1].get("content")
    if not isinstance(content, str) or not content.strip():
        raise Stage3Error("testbench agent returned empty content")
    return content


def generate_tests(repo: Path, protocol_path: Path, output: Path, family: str | None) -> int:
    protocol = validate_protocol(repo, protocol_path)
    ensure_empty(output)
    runtime = resolve_model_runtime(
        MODEL["name"],
        family=MODEL["family"],
        base_url=MODEL["base_url"],
        api_key_env=MODEL["api_key_env"],
        reasoning_effort="auto",
    )
    llm_config = build_effective_legacy_llm_config(
        runtime.effective_config,
        ModelCallRole.PUBLIC_TEST_GENERATION,
    )
    records: list[dict[str, Any]] = []
    for case in protocol["cases"]:
        if family is not None and case["source_family"] != family:
            continue
        case_root = output / "cases" / case["case_id"]
        case_root.mkdir(parents=True)
        source = (repo / case["source_path"]).read_text(encoding="utf-8")
        request = _build_testbench_request(source, case["top"])
        budget = BudgetManager(BudgetLimits(max_llm_calls=1, max_wall_time_s=900))
        status = "blocked"
        reason = None
        try:
            loader = HLSAgentLoader(
                repo / "flow" / "agents" / "testbench.yaml",
                llm_config_override=llm_config,
                budget=budget,
            )
            response = loader.load_agent("tb_creator").run(message=request, max_turns=1)
            raw = _extract_response_content(response)
            testbench = tb_optimizer._extract_one_cpp_block(
                raw,
                artifact_kind="testbench",
                required_symbol=f"{case['top']}_hls",
            )
            tb_optimizer.validate_testbench_top_contract(
                testbench,
                case["top"],
                f"{case['top']}_hls",
                require_original_call=True,
            )
            public = case_root / "public.cpp"
            public.write_text(testbench.rstrip() + "\n", encoding="utf-8")
            status = "generated"
        except Exception as exc:
            reason = f"{type(exc).__name__}:{str(exc)[:500]}"
        usage = budget.snapshot().to_dict()
        record = {
            "case_id": case["case_id"],
            "model_config_sha256": canonical_sha256(runtime.effective_config.to_manifest()),
            "provider_calls": int(usage["llm_calls"]),
            "request_sha256": hashlib.sha256(request.encode("utf-8")).hexdigest(),
            "status": status,
            "reason": reason,
        }
        if status == "generated":
            record["public_path"] = public.relative_to(output).as_posix()
            record["public_sha256"] = file_sha256(public)
        records.append(record)
        write_object(
            output / "partial_result.json",
            {
                "cases": records,
                "provider_calls": sum(item["provider_calls"] for item in records),
                "status": "running",
            },
        )
    result = {
        "branch": git(repo, "branch", "--show-current"),
        "cases": records,
        "family_filter": family,
        "git_history_mutations": 0,
        "model": runtime.effective_config.to_manifest(),
        "protocol_file_sha256": file_sha256(protocol_path),
        "provider_calls": sum(item["provider_calls"] for item in records),
        "repo_head": git(repo, "rev-parse", "HEAD"),
        "schema_version": 1,
        "status": "complete",
        "vitis_launches": 0,
    }
    _assert_safe_json(result)
    result["result_sha256"] = canonical_sha256(result)
    write_object(output / "result.json", result)
    (output / "partial_result.json").unlink(missing_ok=True)
    write_object(output / "artifact_manifest.json", {"files": evidence_inventory(output / "cases"), "schema_version": 1})
    print("INHERITANCE_STAGE3_GENERATION_STATUS=complete")
    print(f"PROVIDER_CALLS={result['provider_calls']}")
    print("VITIS_LAUNCHES=0")
    return 0


def _parse_declaration(declaration: str, function_name: str) -> tuple[str, list[tuple[str, str, str]]]:
    match = re.match(
        rf'^\s*(?P<return>(?:extern\s+"C"\s+)?[\w\s\*&:<>]+?)\s+{re.escape(function_name)}\s*\((?P<params>.*)\)\s*$',
        declaration,
        re.DOTALL,
    )
    if match is None:
        raise Stage3Error("unsupported_candidate_declaration")
    raw_params = match.group("params").strip()
    params: list[tuple[str, str, str]] = []
    if raw_params and raw_params != "void":
        for raw in re.split(r",(?![^<]*>)", raw_params):
            text = raw.strip()
            name_match = re.search(r"\b([A-Za-z_]\w*)\s*((?:\[[^\]]*\]\s*)*)$", text)
            if name_match is None:
                raise Stage3Error("unsupported_or_unnamed_parameter")
            name = name_match.group(1)
            params.append((text[: text.rfind(name)].strip(), name, text))
    return match.group("return").strip(), params


def synthesize_forwarding_stub(declaration: str, original: str, candidate: str) -> str:
    return_type, params = _parse_declaration(declaration, candidate)
    original_declaration = re.sub(rf"\b{re.escape(candidate)}\b", original, declaration, count=1)
    clean_return = return_type.replace('extern "C"', "").strip()
    call = f"{original}({', '.join(item[1] for item in params)})"
    statement = f"{call}; return;" if clean_return == "void" else f"return {call};"
    return f"{original_declaration};\n{declaration} {{ {statement} }}\n"


def _source_oracle(testbench: str, declaration: str, original: str, candidate: str) -> str:
    without_candidate = testbench.replace(declaration + ";", "", 1)
    if without_candidate == testbench:
        without_candidate = testbench.replace(declaration, "", 1)
    return re.sub(rf"\b{re.escape(candidate)}\b", original, without_candidate)


def _contract(declaration: str, candidate: str) -> dict[str, Any]:
    _, params = _parse_declaration(declaration, candidate)
    depths: dict[str, int] = {}
    for parameter_type, name, full in params:
        if "*" not in parameter_type and "[" not in full:
            continue
        dimensions = [int(value) for value in re.findall(r"\[\s*(\d+)\s*\]", full)]
        depth = 1
        for value in dimensions:
            depth *= value
        depths[name] = max(1, depth if dimensions else 4096)
    value: dict[str, Any] = {
        "candidate_mismatch_returncodes": [1],
        "kind": "public_differential_self_check_v1",
        "schema_version": 1,
    }
    if depths:
        value["schema_version"] = 2
        value["cosim_interface_depths"] = dict(sorted(depths.items()))
    return value


def _compile(command: list[str], cwd: Path, timeout: int) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "stderr": completed.stderr[-4000:],
        "stdout": completed.stdout[-4000:],
        "valid": completed.returncode == 0,
    }


def _run_binary(path: Path, cwd: Path, timeout: int) -> dict[str, Any]:
    completed = subprocess.run(
        [str(path)],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    return {
        "returncode": completed.returncode,
        "stderr": completed.stderr[-4000:],
        "stdout": completed.stdout[-4000:],
    }


def preflight(repo: Path, protocol_path: Path, generation: Path, output: Path) -> int:
    protocol = validate_protocol(repo, protocol_path)
    generated = load_object(generation / "result.json")
    ensure_empty(output)
    compiler = shutil.which("g++")
    if compiler is None:
        raise Stage3Error("g++ is required")
    generated_cases = {item["case_id"]: item for item in generated["cases"]}
    records: list[dict[str, Any]] = []
    timeout = int(protocol["timeouts_s"]["host"])
    for case in protocol["cases"]:
        record: dict[str, Any] = {"case_id": case["case_id"], "provider_calls": 0, "vitis_launches": 0}
        generated_case = generated_cases.get(case["case_id"])
        if not generated_case or generated_case.get("status") != "generated":
            record.update({"status": "blocked", "reason": "testbench_generation_failed"})
            records.append(record)
            continue
        case_root = output / "cases" / case["case_id"]
        case_root.mkdir(parents=True)
        public_source = generation / generated_case["public_path"]
        public = public_source.read_text(encoding="utf-8")
        source = repo / case["source_path"]
        candidate = f"{case['top']}_hls"
        try:
            declaration = extract_hls_decl_from_tb(public, candidate)
            if not declaration:
                raise Stage3Error("candidate_declaration_missing")
            _, params = _parse_declaration(declaration, candidate)
            if any("**" in item[0] or "std::" in item[0] for item in params):
                raise Stage3Error("unsupported_complex_public_abi")
            forwarding = synthesize_forwarding_stub(declaration, case["top"], candidate)
            source_oracle = _source_oracle(public, declaration, case["top"], candidate)
            public_path = case_root / "public.cpp"
            forwarding_path = case_root / "forwarding_stub.cpp"
            source_oracle_path = case_root / "source_oracle.cpp"
            contract_path = case_root / "contract.json"
            public_path.write_text(public.rstrip() + "\n", encoding="utf-8")
            forwarding_path.write_text(forwarding, encoding="utf-8")
            source_oracle_path.write_text(source_oracle.rstrip() + "\n", encoding="utf-8")
            write_object(contract_path, _contract(declaration, candidate))
            differential_bin = case_root / "differential_forwarding"
            differential_compile = _compile(
                [compiler, "-std=c++17", "-O0", "-Wno-unknown-pragmas", str(source), str(public_path), str(forwarding_path), "-o", str(differential_bin)],
                case_root,
                timeout,
            )
            differential_runs = []
            if differential_compile["valid"]:
                differential_runs = [_run_binary(differential_bin, case_root, timeout) for _ in range(2)]
            source_bin = case_root / "source_oracle"
            source_compile = _compile(
                [compiler, "-std=c++17", "-O0", "-Wno-unknown-pragmas", str(source), str(source_oracle_path), "-o", str(source_bin)],
                case_root,
                timeout,
            )
            source_runs = []
            if source_compile["valid"]:
                source_runs = [_run_binary(source_bin, case_root, timeout) for _ in range(2)]
            mismatch = None
            for variant in range(max(1, len(params) + 1)):
                mismatch_path = case_root / f"mismatch_stub_{variant}.cpp"
                mismatch_path.write_text(
                    synthesize_echo_stub(declaration, return_variant_idx=variant).stub_code,
                    encoding="utf-8",
                )
                mismatch_bin = case_root / f"differential_mismatch_{variant}"
                mismatch_compile = _compile(
                    [compiler, "-std=c++17", "-O0", "-Wno-unknown-pragmas", str(source), str(public_path), str(mismatch_path), "-o", str(mismatch_bin)],
                    case_root,
                    timeout,
                )
                mismatch_run = _run_binary(mismatch_bin, case_root, timeout) if mismatch_compile["valid"] else None
                if mismatch_run is not None and mismatch_run["returncode"] == 1:
                    mismatch = {"compile": mismatch_compile, "path": mismatch_path.name, "run": mismatch_run, "variant": variant}
                    break
            stable = (
                differential_compile["valid"]
                and len(differential_runs) == 2
                and all(item["returncode"] == 0 for item in differential_runs)
                and differential_runs[0] == differential_runs[1]
                and source_compile["valid"]
                and len(source_runs) == 2
                and all(item["returncode"] == 0 for item in source_runs)
                and source_runs[0] == source_runs[1]
                and mismatch is not None
            )
            record.update(
                {
                    "contract_path": contract_path.relative_to(output).as_posix(),
                    "contract_sha256": file_sha256(contract_path),
                    "differential_compile": differential_compile,
                    "differential_runs": differential_runs,
                    "forwarding_stub_sha256": file_sha256(forwarding_path),
                    "mismatch": mismatch,
                    "public_path": public_path.relative_to(output).as_posix(),
                    "public_sha256": file_sha256(public_path),
                    "source_compile": source_compile,
                    "source_oracle_path": source_oracle_path.relative_to(output).as_posix(),
                    "source_oracle_sha256": file_sha256(source_oracle_path),
                    "source_runs": source_runs,
                    "status": "qualified" if stable else "blocked",
                    "reason": None if stable else "host_oracle_preflight_failed",
                }
            )
        except Exception as exc:
            record.update({"status": "blocked", "reason": f"{type(exc).__name__}:{str(exc)[:500]}"})
        records.append(record)
        write_object(output / "partial_result.json", {"cases": records, "status": "running"})
    result = {
        "cases": records,
        "generation_result_sha256": file_sha256(generation / "result.json"),
        "git_history_mutations": 0,
        "protocol_file_sha256": file_sha256(protocol_path),
        "provider_calls": 0,
        "qualified_count": sum(item["status"] == "qualified" for item in records),
        "schema_version": 1,
        "status": "complete",
        "vitis_launches": 0,
    }
    write_object(output / "result.json", result)
    (output / "partial_result.json").unlink(missing_ok=True)
    write_object(output / "artifact_manifest.json", {"files": evidence_inventory(output / "cases"), "schema_version": 1})
    print("INHERITANCE_STAGE3_PREFLIGHT_STATUS=complete")
    print(f"QUALIFIED={result['qualified_count']}")
    print("PROVIDER_CALLS=0")
    print("VITIS_LAUNCHES=0")
    return 0


def source_baseline(repo: Path, protocol_path: Path, preflight_root: Path, output: Path) -> int:
    protocol = validate_protocol(repo, protocol_path)
    preflight_result = load_object(preflight_root / "result.json")
    ensure_empty(output)
    cases_by_id = {item["case_id"]: item for item in protocol["cases"]}
    records: list[dict[str, Any]] = []
    target = resolve_target_profile(protocol["target_profile"]["name"])
    for asset in preflight_result["cases"]:
        if asset.get("status") != "qualified":
            continue
        case = cases_by_id[asset["case_id"]]
        case_root = output / "cases" / case["case_id"]
        case_root.mkdir(parents=True)
        source = (repo / case["source_path"]).read_text(encoding="utf-8")
        testbench = (preflight_root / asset["source_oracle_path"]).read_text(encoding="utf-8")
        contract = load_object(preflight_root / asset["contract_path"])
        suite_id = f"stage3-{case['case_id']}-source"
        suite = TestSuiteSpec(
            suite_id=suite_id,
            split=EvaluationSplit.PUBLIC,
            suite_version="inheritance-stage3-v1",
            case_count=1,
            testbench_path=f"frozen:{asset['source_oracle_path']}",
            runtime_contract=contract,
        )
        task = TaskSpec(
            task_id=f"inheritance-stage3-{case['case_id']}-source",
            kernel_path=case["source_path"],
            kernel_name=case["top"],
            target=target,
            mode=RunMode.REFACTOR,
            test_suites=(suite,),
        )
        budget = BudgetManager(BudgetLimits(max_llm_calls=0, max_tool_calls=11, max_compile_calls=5, max_csim_calls=1, max_csynth_calls=1, max_cosim_calls=1, max_tokens=0, max_cost_usd=0))
        run_id = f"inheritance-stage3-{case['case_id']}-source"
        trace = TraceRecorder(run_id, task_id=task.task_id, output_path=case_root / "trace.jsonl")
        context = RunContext(run_id=run_id, task=task, budget=budget, trace=trace)
        factory = LocalCandidateValidationHandlerFactory(
            case_root / "work",
            csim_timelimit=int(protocol["timeouts_s"]["csim"]),
            csynth_timelimit=int(protocol["timeouts_s"]["csynth"]),
            cosim_timelimit=int(protocol["timeouts_s"]["cosim"]),
            cosim_policy="required",
        )
        request = CandidateValidationPlanRequest(
            task=task,
            candidate_code=source,
            original_code=REFERENCE_STUB,
            preflight_testbench_code=testbench,
            suite_testbench_codes={suite_id: testbench},
            attempt=0,
            validation_id=run_id,
            reference_top_function=None,
            candidate_top_function=case["top"],
        )
        outcome = None
        error = None
        try:
            outcome = ValidationOrchestrator(factory.build(request)).run_detailed(context, validation_id=run_id)
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)[:1000]}
        usage = budget.snapshot().to_dict()
        if outcome is None:
            classification = "infrastructure_failure"
            stages = {"S0_compile": "unknown", "S1_public_csim": "not_run", "S2_csynth": "not_run", "S3_public_cosim": "not_run"}
            terminal_state = "execution_error"
            validation = None
            feedback = None
        else:
            terminal_state = outcome.terminal_state.value
            report = outcome.terminal_report
            owners = set() if report is None else {item.owner.value for item in report.items}
            classification = classify_terminal(
                accepted=outcome.result.accepted,
                terminal_state=terminal_state,
                owners=owners,
                physical_execution=_physical_for_state(terminal_state, usage),
            )
            stages = _stage_statuses(outcome)
            validation = outcome.to_dict()
            feedback = None if report is None else report.to_dict()
        record = {
            "budget_usage": usage,
            "case_id": case["case_id"],
            "classification": classification,
            "execution_error": error,
            "source_sha256": case["source_sha256"],
            "stage_statuses": stages,
            "terminal_feedback": feedback,
            "terminal_state": terminal_state,
            "validation": validation,
        }
        write_object(case_root / "case_result.json", record)
        records.append(record)
        write_object(output / "partial_result.json", {"cases": records, "status": "running"})
    vitis = sum(item["budget_usage"].get(name, 0) for item in records for name in ("csim_calls", "csynth_calls", "cosim_calls"))
    result = {
        "cases": records,
        "git_history_mutations": 0,
        "preflight_result_sha256": file_sha256(preflight_root / "result.json"),
        "provider_calls": 0,
        "schema_version": 1,
        "status": "complete",
        "vitis_launches": vitis,
    }
    write_object(output / "result.json", result)
    (output / "partial_result.json").unlink(missing_ok=True)
    write_object(output / "artifact_manifest.json", {"files": evidence_inventory(output / "cases"), "schema_version": 1})
    print("INHERITANCE_STAGE3_SOURCE_BASELINE_STATUS=complete")
    print("PROVIDER_CALLS=0")
    print(f"VITIS_LAUNCHES={vitis}")
    return 0


def _run_usage(root: Path) -> tuple[int, int]:
    path = root / "run_result.json"
    if not path.is_file():
        return 0, 0
    usage = load_object(path).get("budget_usage", {})
    return int(usage.get("llm_calls", 0)), sum(int(usage.get(name, 0)) for name in ("csim_calls", "csynth_calls", "cosim_calls"))


def run_refactors(repo: Path, protocol_path: Path, generation_root: Path, preflight_root: Path, baseline_root: Path, output: Path, run_root: Path, family: str | None) -> int:
    protocol = validate_protocol(repo, protocol_path)
    ensure_empty(output)
    generated = load_object(generation_root / "result.json")
    preflight_result = load_object(preflight_root / "result.json")
    baseline = load_object(baseline_root / "result.json")
    generated_calls = int(generated["provider_calls"])
    baseline_vitis = int(baseline["vitis_launches"])
    cases = {item["case_id"]: item for item in protocol["cases"]}
    baseline_cases = {item["case_id"]: item for item in baseline["cases"]}
    records: list[dict[str, Any]] = []
    for asset in preflight_result["cases"]:
        if asset.get("status") != "qualified":
            continue
        case = cases[asset["case_id"]]
        if family is not None and case["source_family"] != family:
            continue
        consumed_provider = CARRIED_STAGE2B["provider_calls"] + generated_calls + sum(item["provider_calls"] for item in records)
        consumed_vitis = CARRIED_STAGE2B["vitis_launches"] + baseline_vitis + sum(item["vitis_launches"] for item in records)
        if consumed_provider + 9 > 600 or consumed_vitis + 8 > 600:
            raise Stage3Error("campaign budget cannot reserve the next formal run")
        run_id = f"inheritance-stage3-{case['case_id']}-v1"
        root = run_root / f"inheritance_stage3_refactor_{case['case_id']}_v1"
        if root.exists():
            raise Stage3Error(f"versioned run root already exists: {root}")
        public = preflight_root / asset["public_path"]
        contract = preflight_root / asset["contract_path"]
        command = [
            sys.executable,
            "-m",
            "agrefactor.cli",
            "refactor",
            str((repo / case["source_path"]).resolve()),
            "--top",
            case["top"],
            "--model",
            MODEL["name"],
            "--model-family",
            MODEL["family"],
            "--base-url",
            MODEL["base_url"],
            "--api-key-env",
            MODEL["api_key_env"],
            "--target",
            protocol["target_profile"]["name"],
            "--public-test",
            str(public.resolve()),
            "--public-test-contract",
            str(contract.resolve()),
            "--hidden-tests",
            "none",
            "--max-testbench-repairs",
            "1",
            "--max-candidate-repairs",
            "1",
            "--max-llm-calls",
            "9",
            "--max-csim-calls",
            "3",
            "--max-csynth-calls",
            "3",
            "--max-cosim-calls",
            "2",
            "--max-wall-time-s",
            "14400",
            "--csim-timeout-s",
            str(protocol["timeouts_s"]["csim"]),
            "--csynth-timeout-s",
            str(protocol["timeouts_s"]["csynth"]),
            "--cosim-timeout-s",
            str(protocol["timeouts_s"]["cosim"]),
            "--cosim-policy",
            "required",
            "--output-dir",
            str(root),
            "--run-id",
            run_id,
            "--json",
        ]
        completed = subprocess.run(command, cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15000, check=False)
        (output / "logs").mkdir(parents=True, exist_ok=True)
        (output / "logs" / f"{case['case_id']}.stdout.log").write_text(completed.stdout, encoding="utf-8")
        (output / "logs" / f"{case['case_id']}.stderr.log").write_text(completed.stderr, encoding="utf-8")
        provider_calls, vitis_launches = _run_usage(root)
        run_result = load_object(root / "run_result.json") if (root / "run_result.json").is_file() else {}
        identity = load_object(root / "execution_identity.json") if (root / "execution_identity.json").is_file() else {}
        candidate = identity.get("candidates", {}).get("final", {})
        source_passed = baseline_cases.get(case["case_id"], {}).get("classification") == "raw_pass_all"
        succeeded = (
            completed.returncode == 0
            and run_result.get("status") == "succeeded"
            and run_result.get("succeeded") is True
            and identity.get("completeness", {}).get("accepted_ready") is True
            and candidate.get("sha256") not in (None, case["source_sha256"])
        )
        classification = "ordinary_refactor_success" if succeeded else ("regression" if source_passed else "blocked")
        record = {
            "case_id": case["case_id"],
            "classification": classification,
            "command_returncode": completed.returncode,
            "provider_calls": provider_calls,
            "run_result_sha256": file_sha256(root / "run_result.json") if (root / "run_result.json").is_file() else None,
            "run_root": str(root),
            "source_baseline_passed": source_passed,
            "vitis_launches": vitis_launches,
        }
        records.append(record)
        write_object(output / "partial_result.json", {"cases": records, "status": "running"})
    result = {
        "cases": records,
        "family_filter": family,
        "generation_provider_calls": generated_calls,
        "git_history_mutations": 0,
        "provider_calls": sum(item["provider_calls"] for item in records),
        "schema_version": 1,
        "status": "complete",
        "vitis_launches": sum(item["vitis_launches"] for item in records),
    }
    write_object(output / "result.json", result)
    (output / "partial_result.json").unlink(missing_ok=True)
    print("INHERITANCE_STAGE3_REFACTOR_STATUS=complete")
    print(f"PROVIDER_CALLS={result['provider_calls']}")
    print(f"VITIS_LAUNCHES={result['vitis_launches']}")
    return 0


def seal(repo: Path, protocol_path: Path, generation: Path, preflight_root: Path, baseline: Path, campaigns: list[Path], checkpoint: Path) -> int:
    protocol = validate_protocol(repo, protocol_path)
    ensure_empty(checkpoint)
    generation_result = load_object(generation / "result.json")
    preflight_result = load_object(preflight_root / "result.json")
    baseline_result = load_object(baseline / "result.json")
    campaign_results = [load_object(path / "result.json") for path in campaigns]
    baseline_cases = {
        item["case_id"]: item for item in baseline_result.get("cases", [])
    }
    cases: list[dict[str, Any]] = []
    for item in preflight_result["cases"]:
        if item.get("status") == "blocked":
            cases.append(
                {
                    "case_id": item["case_id"],
                    "classification": "adapter_or_oracle_invalid",
                    "provider_calls": 0,
                    "vitis_launches": 0,
                    "preflight_reason": item.get("reason"),
                    "run_root": None,
                    "run_result_sha256": None,
                }
            )
    for path, result in zip(campaigns, campaign_results):
        for item in result["cases"]:
            record = dict(item)
            if (
                record.get("classification") == "blocked"
                and baseline_cases.get(record.get("case_id"), {}).get("classification")
                == "raw_pass_all"
            ):
                record["classification"] = "unnecessary_rewrite"
            record["campaign_result_sha256"] = file_sha256(path / "result.json")
            cases.append(record)
    total_provider = CARRIED_STAGE2B["provider_calls"] + int(generation_result["provider_calls"]) + sum(int(item["provider_calls"]) for item in campaign_results)
    total_vitis = CARRIED_STAGE2B["vitis_launches"] + int(baseline_result["vitis_launches"]) + sum(int(item["vitis_launches"]) for item in campaign_results)
    manifest = {
        "budget": {
            "historical_stage2": HISTORICAL_STAGE2,
            "stage2b_and_stage3_provider_calls": total_provider,
            "stage2b_and_stage3_vitis_launches": total_vitis,
            "hard_cap": protocol["budget"],
        },
        "cases": cases,
        "checkpoint_id": "v2.3-inheritance-first-stage3-checkpoint-v1",
        "evidence": {
            "baseline_result_sha256": file_sha256(baseline / "result.json"),
            "baseline_root": str(baseline),
            "campaign_roots": [str(item) for item in campaigns],
            "generation_result_sha256": file_sha256(generation / "result.json"),
            "generation_root": str(generation),
            "preflight_result_sha256": file_sha256(preflight_root / "result.json"),
            "preflight_root": str(preflight_root),
        },
        "git": {"branch": git(repo, "branch", "--show-current"), "head": git(repo, "rev-parse", "HEAD"), "history_mutations": 0},
        "outcome": dict(Counter(item["classification"] for item in cases)),
        "preflight": {
            "blocked": sum(item["status"] == "blocked" for item in preflight_result["cases"]),
            "qualified": preflight_result["qualified_count"],
        },
        "protocol_file_sha256": file_sha256(protocol_path),
        "schema_version": 1,
    }
    if total_provider > 600 or total_vitis > 600:
        raise Stage3Error("sealed campaign exceeds its hard budget")
    _assert_safe_json(manifest)
    manifest["campaign_manifest_sha256"] = canonical_sha256(manifest)
    write_object(checkpoint / "campaign_manifest.json", manifest)
    print("INHERITANCE_STAGE3_SEAL_STATUS=complete")
    print(f"PROVIDER_CALLS={total_provider}")
    print(f"VITIS_LAUNCHES={total_vitis}")
    print(f"CAMPAIGN_MANIFEST_SHA256={file_sha256(checkpoint / 'campaign_manifest.json')}")
    return 0


def audit(repo: Path, protocol_path: Path, checkpoint: Path) -> int:
    protocol = validate_protocol(repo, protocol_path)
    manifest_path = checkpoint / "campaign_manifest.json"
    if (checkpoint / "audit_result.json").exists():
        raise Stage3Error("audit output already exists")
    manifest = load_object(manifest_path)
    failures: list[str] = []

    def check(name: str, condition: bool) -> None:
        if not condition:
            failures.append(name)

    check("schema", manifest.get("schema_version") == 1)
    check("protocol_hash", manifest.get("protocol_file_sha256") == file_sha256(protocol_path))
    check("branch", manifest.get("git", {}).get("branch") == "research-roadmap-v2.3")
    check("execution_boundary", protocol.get("execution") == EXECUTION)
    budget = manifest.get("budget", {})
    check("provider_cap", int(budget.get("stage2b_and_stage3_provider_calls", 601)) <= 600)
    check("vitis_cap", int(budget.get("stage2b_and_stage3_vitis_launches", 601)) <= 600)
    evidence = manifest.get("evidence", {})
    for name in ("generation", "preflight", "baseline"):
        root = Path(evidence[f"{name}_root"])
        result = root / "result.json"
        check(f"{name}_result", result.is_file() and file_sha256(result) == evidence[f"{name}_result_sha256"])
    for item in manifest.get("cases", []):
        if item.get("run_root") is None:
            check(
                f"{item['case_id']}:blocked_record",
                item.get("classification") == "adapter_or_oracle_invalid",
            )
            continue
        root = Path(item["run_root"])
        result = root / "run_result.json"
        check(f"{item['case_id']}:run_result", result.is_file() and file_sha256(result) == item["run_result_sha256"])
        if item["classification"] == "ordinary_refactor_success":
            run = load_object(result)
            identity = load_object(root / "execution_identity.json")
            check(f"{item['case_id']}:succeeded", run.get("status") == "succeeded" and run.get("succeeded") is True)
            check(f"{item['case_id']}:accepted_ready", identity.get("completeness", {}).get("accepted_ready") is True)
            model_calls = load_object(root / "model_calls.json")
            check(f"{item['case_id']}:no_plaintext_prompt", model_calls.get("plaintext_prompts_persisted") is False)
            check(f"{item['case_id']}:no_plaintext_response", model_calls.get("plaintext_responses_persisted") is False)
    check("repository_clean", not git(repo, "status", "--porcelain", "--untracked-files=all"))
    audit_result = {
        "audit_id": "v2.3-inheritance-first-stage3-independent-audit-v1",
        "campaign_manifest_sha256": file_sha256(manifest_path),
        "critical_findings": len(failures),
        "failures": failures,
        "git_history_mutations": 0,
        "provider_calls": 0,
        "schema_version": 1,
        "status": "passed" if not failures else "failed",
        "vitis_launches": 0,
    }
    write_object(checkpoint / "audit_result.json", audit_result)
    write_object(checkpoint / "artifact_manifest.json", {"files": evidence_inventory(checkpoint), "schema_version": 1})
    print(f"INHERITANCE_STAGE3_AUDIT_STATUS={audit_result['status']}")
    print(f"CRITICAL_FINDINGS={len(failures)}")
    print(f"AUDIT_RESULT_SHA256={file_sha256(checkpoint / 'audit_result.json')}")
    print("AUDITOR_PROVIDER_CALLS=0")
    print("AUDITOR_VITIS_LAUNCHES=0")
    return 0 if not failures else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    prepare.add_argument("--output", type=Path, default=DEFAULT_PROTOCOL)
    generate = sub.add_parser("generate")
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--family", choices=("app", "c2hlsc", "hlsrewritter", "leetcode", "opt"))
    pre = sub.add_parser("preflight")
    pre.add_argument("--generation", type=Path, required=True)
    pre.add_argument("--output", type=Path, required=True)
    base = sub.add_parser("baseline")
    base.add_argument("--preflight", type=Path, required=True)
    base.add_argument("--output", type=Path, required=True)
    refactor = sub.add_parser("refactor")
    refactor.add_argument("--generation", type=Path, required=True)
    refactor.add_argument("--preflight", type=Path, required=True)
    refactor.add_argument("--baseline", type=Path, required=True)
    refactor.add_argument("--output", type=Path, required=True)
    refactor.add_argument("--run-root", type=Path, default=Path("/data/agrefactor_runs"))
    refactor.add_argument("--family", choices=("app", "c2hlsc", "hlsrewritter", "leetcode", "opt"))
    seal_parser = sub.add_parser("seal")
    seal_parser.add_argument("--generation", type=Path, required=True)
    seal_parser.add_argument("--preflight", type=Path, required=True)
    seal_parser.add_argument("--baseline", type=Path, required=True)
    seal_parser.add_argument("--campaign", type=Path, action="append", required=True)
    seal_parser.add_argument("--checkpoint", type=Path, required=True)
    audit_parser = sub.add_parser("audit")
    audit_parser.add_argument("--checkpoint", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    protocol = args.protocol.resolve()
    if args.command == "prepare":
        value = prepare_protocol(repo, args.inventory.resolve(), args.output.resolve())
        print("INHERITANCE_STAGE3_PREPARE_STATUS=complete")
        print(f"CASE_COUNT={len(value['cases'])}")
        print("PROVIDER_CALLS=0")
        print("VITIS_LAUNCHES=0")
        return 0
    if git(repo, "branch", "--show-current") != "research-roadmap-v2.3":
        raise Stage3Error("wrong branch")
    if git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise Stage3Error("repository must be clean")
    if args.command == "generate":
        return generate_tests(repo, protocol, args.output.resolve(), args.family)
    if args.command == "preflight":
        return preflight(repo, protocol, args.generation.resolve(), args.output.resolve())
    if args.command == "baseline":
        return source_baseline(repo, protocol, args.preflight.resolve(), args.output.resolve())
    if args.command == "refactor":
        return run_refactors(repo, protocol, args.generation.resolve(), args.preflight.resolve(), args.baseline.resolve(), args.output.resolve(), args.run_root.resolve(), args.family)
    if args.command == "seal":
        return seal(repo, protocol, args.generation.resolve(), args.preflight.resolve(), args.baseline.resolve(), [item.resolve() for item in args.campaign], args.checkpoint.resolve())
    return audit(repo, protocol, args.checkpoint.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
