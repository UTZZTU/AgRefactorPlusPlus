#!/usr/bin/env python3
"""Run the frozen R5.1 P4 raw-source baseline through existing validators."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agrefactor.config import (
    EvaluationSplit,
    RunMode,
    TaskSpec,
    TestSuiteSpec,
    resolve_target_profile,
)
from agrefactor.runtime import (
    BudgetLimits,
    BudgetManager,
    CandidateValidationPlanRequest,
    LocalCandidateValidationHandlerFactory,
    RunContext,
    TraceRecorder,
    ValidationOrchestrator,
)


DEFAULT_PLAN = ROOT / "configs" / "r5_1" / "source_baseline_protocol.json"
ALLOWED_ROOTS = {"repository", "external"}
ALLOWED_DECISIONS = {"admit", "external-only"}
ALLOWED_PARTITIONS = {"history", "future"}
ALLOWED_HOST_PREFLIGHT_EXPECTATIONS = {"oracle_pass", "candidate_mismatch"}
ALLOWED_CLASSIFICATIONS = {
    "raw_pass_all",
    "raw_fail_actionable",
    "raw_fail_ambiguous",
    "oracle_or_adapter_invalid",
    "infrastructure_failure",
}
MAX_MARKER_BYTES = 256
REFERENCE_STUB = (
    "// R5.1 source baseline has no comparison reference implementation.\n"
)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha_value(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _safe_relative(value: Any) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"invalid relative path: {value!r}")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"path escapes frozen root: {value}")
    return path


def _required_sha(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _validate_assembly(value: Any, case_id: str, role: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{case_id}.{role} must be an object")
    parts = value.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError(f"{case_id}.{role}.parts must be non-empty")
    for record in parts:
        if not isinstance(record, Mapping) or record.get("root") not in ALLOWED_ROOTS:
            raise ValueError(f"invalid source component: {case_id}.{role}")
        _safe_relative(record.get("path"))
        _required_sha(record.get("sha256"), f"{case_id}.{role}.sha256")
        if record.get("root") == "external":
            _safe_relative(record.get("checkout_dir"))
            commit = record.get("commit")
            if not isinstance(commit, str) or len(commit) != 40:
                raise ValueError(f"invalid external commit: {case_id}.{role}")
        elif set(record) != {"root", "path", "sha256"}:
            raise ValueError(f"repository component has unexpected fields: {case_id}")
    strips = value.get("strip_exact_lines")
    if not isinstance(strips, list) or len(strips) != len(set(strips)):
        raise ValueError(f"invalid strip_exact_lines: {case_id}.{role}")
    for line in strips:
        if (
            not isinstance(line, str)
            or not line.strip()
            or line != line.strip()
            or "\n" in line
            or "\r" in line
        ):
            raise ValueError(f"invalid stripped line: {case_id}.{role}")


def validate_plan(plan: Mapping[str, Any]) -> None:
    if plan.get("schema_version") != 2:
        raise ValueError("unsupported source baseline schema")
    if plan.get("route") != "V2.3-R5.1-P4":
        raise ValueError("unexpected source baseline route")
    target = plan.get("target_profile")
    if not isinstance(target, Mapping) or target.get("name") != "vitis-2023.2-default":
        raise ValueError("source baseline target is not frozen")
    _safe_relative(target.get("path"))
    _required_sha(target.get("sha256"), "target_profile.sha256")

    timeouts = plan.get("timeouts_s")
    if not isinstance(timeouts, Mapping) or set(timeouts) != {
        "host", "csim", "csynth", "cosim"
    }:
        raise ValueError("timeouts_s must define the complete stage set")
    for name, value in timeouts.items():
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"invalid timeout: {name}")

    budget = plan.get("budget")
    if not isinstance(budget, Mapping):
        raise ValueError("budget must be an object")
    hard = budget.get("hard_cap")
    carried = budget.get("carried_forward")
    phase = budget.get("p4_phase_upper_bound")
    reserve = budget.get("campaign_reserve")
    recovery = budget.get("unallocated_recovery_buffer")
    expected = (
        (hard, 650, 650),
        (carried, 248, 111),
        (phase, 0, 170),
        (recovery, 42, 59),
    )
    for record, provider, vitis in expected:
        if not isinstance(record, Mapping) or record.get("provider_calls") != provider or record.get("vitis_launches") != vitis:
            raise ValueError("budget authority does not match the R5.1 route")
    if not isinstance(reserve, Mapping) or reserve.get("provider_calls") != 0:
        raise ValueError("P4 source baseline cannot reserve Provider calls")

    cases = plan.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a non-empty list")
    case_ids: set[str] = set()
    sources: set[str] = set()
    family_partitions: dict[str, str] = {}
    for case in cases:
        if not isinstance(case, Mapping):
            raise ValueError("case must be an object")
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id or case_id in case_ids:
            raise ValueError(f"case_id must be unique: {case_id!r}")
        case_ids.add(case_id)
        source_id = case.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            raise ValueError(f"missing source_id: {case_id}")
        sources.add(source_id)
        if case.get("decision") not in ALLOWED_DECISIONS:
            raise ValueError(f"case is not approved for external execution: {case_id}")
        if case.get("partition") not in ALLOWED_PARTITIONS:
            raise ValueError(f"invalid partition: {case_id}")
        family = case.get("algorithm_family")
        if not isinstance(family, str) or not family:
            raise ValueError(f"missing algorithm family: {case_id}")
        previous = family_partitions.setdefault(family, case["partition"])
        if previous != case["partition"]:
            raise ValueError(f"semantic family crosses history/future: {family}")
        top = case.get("top")
        if not isinstance(top, str) or not top.strip() or top != top.strip():
            raise ValueError(f"invalid top: {case_id}")
        if not isinstance(case.get("known_prior_outcome"), bool):
            raise ValueError(f"known_prior_outcome must be boolean: {case_id}")
        if case.get("host_preflight_expected") not in ALLOWED_HOST_PREFLIGHT_EXPECTATIONS:
            raise ValueError(f"invalid host preflight expectation: {case_id}")
        _validate_assembly(case.get("design"), case_id, "design")
        _validate_assembly(case.get("public_testbench"), case_id, "public_testbench")
        marker = case.get("pass_marker")
        if (
            not isinstance(marker, str)
            or not marker.strip()
            or marker != marker.strip()
            or "\n" in marker
            or "\r" in marker
            or len(marker.encode("utf-8")) > MAX_MARKER_BYTES
        ):
            raise ValueError(f"invalid pass marker: {case_id}")
        contract = case.get("runtime_contract")
        if not isinstance(contract, Mapping) or set(contract) != {
            "schema_version",
            "kind",
            "candidate_mismatch_returncodes",
            "cosim_interface_depths",
        }:
            raise ValueError(f"invalid runtime contract shape: {case_id}")
        if contract.get("schema_version") != 2 or contract.get("kind") != "public_differential_self_check_v1":
            raise ValueError(f"invalid runtime contract identity: {case_id}")
        codes = contract.get("candidate_mismatch_returncodes")
        if not isinstance(codes, list) or not codes or any(
            isinstance(code, bool) or not isinstance(code, int) or code <= 0 or code > 255
            for code in codes
        ):
            raise ValueError(f"invalid mismatch return codes: {case_id}")
        depths = contract.get("cosim_interface_depths")
        if not isinstance(depths, Mapping) or not depths or any(
            not isinstance(port, str)
            or not port
            or isinstance(depth, bool)
            or not isinstance(depth, int)
            or depth <= 0
            for port, depth in depths.items()
        ):
            raise ValueError(f"invalid COSIM interface depths: {case_id}")

    expected_vitis = len(cases) * 3
    invariants = plan.get("invariants")
    if not isinstance(invariants, Mapping):
        raise ValueError("invariants must be an object")
    if invariants.get("provider_calls") != 0 or invariants.get("maximum_vitis_launches_per_case") != 3:
        raise ValueError("source baseline call invariants are invalid")
    if invariants.get("expected_vitis_upper_bound") != expected_vitis:
        raise ValueError("source baseline Vitis upper bound is stale")
    validation_budget = invariants.get("validation_budget_per_case")
    if validation_budget != {
        "tool_calls": 11,
        "compile_calls": 5,
        "csim_calls": 1,
        "csynth_calls": 1,
        "cosim_calls": 1,
    }:
        raise ValueError("per-case validation budget does not cover the frozen stage plan")
    if reserve.get("vitis_launches") != expected_vitis or expected_vitis > phase.get("vitis_launches"):
        raise ValueError("source baseline reserve does not cover the frozen cases")
    if len(sources) < 3 or len(family_partitions) < 4:
        raise ValueError("P4 pilot must cover at least three sources and four families")
    if carried["provider_calls"] + recovery["provider_calls"] > hard["provider_calls"]:
        raise ValueError("Provider recovery buffer exceeds the hard cap")
    if carried["vitis_launches"] + expected_vitis + recovery["vitis_launches"] > hard["vitis_launches"]:
        raise ValueError("Vitis reserve exceeds the hard cap")


def _run(command: list[str], *, cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def _git_snapshot(repo: Path) -> dict[str, str]:
    head = _run(["git", "rev-parse", "HEAD"], cwd=repo, timeout=30)
    status = _run(["git", "status", "--porcelain"], cwd=repo, timeout=30)
    if head.returncode != 0 or status.returncode != 0:
        raise RuntimeError(f"git identity unavailable: {repo}")
    return {"head": head.stdout.strip(), "status": status.stdout.strip()}


def _resolve_component(
    repo: Path,
    external_root: Path,
    record: Mapping[str, Any],
) -> tuple[Path, dict[str, Any]]:
    relative = _safe_relative(record["path"])
    if record["root"] == "repository":
        base = repo
        checkout = None
        commit = None
    else:
        checkout = str(record["checkout_dir"])
        base = external_root / _safe_relative(checkout)
        snapshot = _git_snapshot(base)
        if snapshot["head"] != record["commit"]:
            raise ValueError(f"external_commit_mismatch:{checkout}")
        if snapshot["status"]:
            raise ValueError(f"external_checkout_dirty:{checkout}")
        commit = record["commit"]
    path = base / relative
    digest = sha_file(path) if path.is_file() and not path.is_symlink() else None
    if digest != record["sha256"]:
        raise ValueError(f"source_hash_mismatch:{record['root']}:{relative}")
    identity = {
        "root": record["root"],
        "checkout_dir": checkout,
        "commit": commit,
        "path": relative.as_posix(),
        "sha256": digest,
    }
    return path, identity


def _assemble(
    repo: Path,
    external_root: Path,
    assembly: Mapping[str, Any],
) -> tuple[str, list[dict[str, Any]], list[str]]:
    strips = set(assembly["strip_exact_lines"])
    observed = Counter()
    chunks: list[str] = []
    identities: list[dict[str, Any]] = []
    for record in assembly["parts"]:
        path, identity = _resolve_component(repo, external_root, record)
        text = path.read_bytes().replace(b"\r\n", b"\n").decode("utf-8")
        lines: list[str] = []
        for line in text.splitlines():
            cleaned = line.strip()
            if cleaned in strips:
                observed[cleaned] += 1
                continue
            lines.append(line)
        chunks.append("\n".join(lines).rstrip() + "\n")
        identities.append(identity)
    missing = sorted(line for line in strips if observed[line] == 0)
    if missing:
        raise ValueError("declared stripped lines were not observed: " + ", ".join(missing))
    code = "\n".join(chunks)
    return code, identities, sorted(observed.elements())


def materialize_case(
    repo: Path,
    external_root: Path,
    case: Mapping[str, Any],
) -> dict[str, Any]:
    design, design_parts, design_strips = _assemble(
        repo, external_root, case["design"]
    )
    testbench, test_parts, test_strips = _assemble(
        repo, external_root, case["public_testbench"]
    )
    return {
        "design": design,
        "testbench": testbench,
        "identity": {
            "design_parts": design_parts,
            "testbench_parts": test_parts,
            "design_stripped_lines": design_strips,
            "testbench_stripped_lines": test_strips,
            "design_sha256": hashlib.sha256(design.encode("utf-8")).hexdigest(),
            "testbench_sha256": hashlib.sha256(testbench.encode("utf-8")).hexdigest(),
        },
    }


def _ensure_empty_output(output: Path) -> None:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"output must be empty or absent: {output}")
    output.mkdir(parents=True, exist_ok=True)


def classify_host_preflight(
    *,
    expected: str,
    compile_returncode: int,
    run_returncode: int | None,
    pass_marker_observed: bool,
    mismatch_returncodes: set[int],
) -> tuple[bool, str]:
    if compile_returncode != 0:
        observed = "compile_failed"
    elif run_returncode is None:
        observed = "not_run"
    elif run_returncode == 0 and pass_marker_observed:
        observed = "oracle_pass"
    elif run_returncode in mismatch_returncodes and not pass_marker_observed:
        observed = "candidate_mismatch"
    elif run_returncode == 0:
        observed = "pass_marker_missing"
    elif pass_marker_observed:
        observed = "nonzero_with_pass_marker"
    else:
        observed = "unexpected_failure"
    return observed == expected, observed


def run_host_preflight(
    repo: Path,
    external_root: Path,
    output: Path,
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    validate_plan(plan)
    _ensure_empty_output(output)
    target_path = repo / _safe_relative(plan["target_profile"]["path"])
    if sha_file(target_path) != plan["target_profile"]["sha256"]:
        raise ValueError("target_profile_hash_mismatch")
    compiler = shutil.which("g++")
    if compiler is None:
        raise RuntimeError("g++ is required for source baseline preflight")
    compiler_version = _run([compiler, "--version"], cwd=output, timeout=30)
    repo_before = _git_snapshot(repo)
    if repo_before["status"]:
        raise ValueError("repository_must_be_clean_before_preflight")
    cases: list[dict[str, Any]] = []
    for case in plan["cases"]:
        material = materialize_case(repo, external_root, case)
        case_root = output / case["case_id"]
        case_root.mkdir()
        design_path = case_root / "design.cpp"
        test_path = case_root / "public_testbench.cpp"
        binary = case_root / "host_oracle"
        design_path.write_text(material["design"], encoding="utf-8")
        test_path.write_text(material["testbench"], encoding="utf-8")
        compiled = _run(
            [
                compiler,
                "-std=c++17",
                "-O0",
                "-Wall",
                "-Wextra",
                "-Wno-unknown-pragmas",
                str(design_path),
                str(test_path),
                "-o",
                str(binary),
            ],
            cwd=case_root,
            timeout=plan["timeouts_s"]["host"],
        )
        executed = None
        if compiled.returncode == 0:
            executed = _run(
                [str(binary)],
                cwd=case_root,
                timeout=plan["timeouts_s"]["host"],
            )
        stdout = "" if executed is None else executed.stdout
        stderr = "" if executed is None else executed.stderr
        (case_root / "compile.stdout").write_text(compiled.stdout, encoding="utf-8")
        (case_root / "compile.stderr").write_text(compiled.stderr, encoding="utf-8")
        (case_root / "run.stdout").write_text(stdout, encoding="utf-8")
        (case_root / "run.stderr").write_text(stderr, encoding="utf-8")
        pass_marker_observed = case["pass_marker"] in stdout
        passed, observed = classify_host_preflight(
            expected=case["host_preflight_expected"],
            compile_returncode=compiled.returncode,
            run_returncode=None if executed is None else executed.returncode,
            pass_marker_observed=pass_marker_observed,
            mismatch_returncodes=set(
                case["runtime_contract"]["candidate_mismatch_returncodes"]
            ),
        )
        cases.append(
            {
                "case_id": case["case_id"],
                "source_id": case["source_id"],
                "algorithm_family": case["algorithm_family"],
                "partition": case["partition"],
                "status": "passed" if passed else "failed",
                "expected_outcome": case["host_preflight_expected"],
                "observed_outcome": observed,
                "compile_returncode": compiled.returncode,
                "run_returncode": None if executed is None else executed.returncode,
                "pass_marker_observed": pass_marker_observed,
                "material_identity": material["identity"],
            }
        )
    repo_after = _git_snapshot(repo)
    result = {
        "schema_version": 1,
        "preflight_id": "v2.3-r5.1-p4-source-baseline-host-preflight-v1",
        "status": "passed" if all(case["status"] == "passed" for case in cases) else "failed",
        "plan_sha256": sha_value(plan),
        "repo_head": repo_before["head"],
        "compiler": compiler,
        "compiler_version": compiler_version.stdout.splitlines()[0],
        "cases": cases,
        "provider_calls": 0,
        "vitis_launches": 0,
        "git_history_mutations": int(repo_before["head"] != repo_after["head"]),
        "working_tree_mutations": int(repo_before["status"] != repo_after["status"]),
        "r5_accepted": False,
        "r6_started": False,
    }
    result["result_sha256"] = sha_value(result)
    _write_json(output / "host_preflight.json", result)
    return result


def _stage_statuses(outcome: Any) -> dict[str, str]:
    statuses = {"S0_host_oracle": "passed", "S1_public_csim": "not_run", "S2_csynth": "not_run", "S3_public_cosim": "not_run"}
    mapping = {
        "public_evaluation": "S1_public_csim",
        "csynth": "S2_csynth",
        "public_cosim": "S3_public_cosim",
    }
    for step in outcome.result.steps:
        name = mapping.get(step.state.value)
        if name is None:
            continue
        statuses[name] = "failed" if step.source_blocking else "passed"
    return statuses


def classify_terminal(
    *,
    accepted: bool,
    terminal_state: str,
    owners: set[str],
    physical_execution: bool,
) -> str:
    if accepted:
        return "raw_pass_all"
    if terminal_state == "preflight":
        return "oracle_or_adapter_invalid"
    if "toolchain" in owners:
        return "infrastructure_failure"
    if owners & {"testbench", "configuration", "evaluator"}:
        return "oracle_or_adapter_invalid"
    if "candidate" in owners and physical_execution:
        return "raw_fail_actionable"
    return "raw_fail_ambiguous"


def _physical_for_state(state: str, usage: Mapping[str, Any]) -> bool:
    field = {
        "preflight": "compile_calls",
        "public_evaluation": "csim_calls",
        "csynth": "csynth_calls",
        "public_cosim": "cosim_calls",
    }.get(state)
    return bool(field is not None and usage.get(field, 0) > 0)


def _evidence_inventory(case_root: Path, output: Path) -> list[dict[str, Any]]:
    allowed_suffixes = {".json", ".jsonl", ".log", ".xml", ".rpt"}
    records: list[dict[str, Any]] = []
    for path in sorted(case_root.rglob("*")):
        if path.is_symlink() or not path.is_file() or path.suffix not in allowed_suffixes:
            continue
        records.append(
            {
                "path": path.relative_to(output).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size_bytes": path.stat().st_size,
            }
        )
    return records


def _validate_preflight(
    preflight: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    if preflight.get("status") != "passed" or preflight.get("plan_sha256") != sha_value(plan):
        raise ValueError("host_preflight_not_authoritative")
    if preflight.get("provider_calls") != 0 or preflight.get("vitis_launches") != 0:
        raise ValueError("host_preflight_made_external_calls")
    cases = preflight.get("cases")
    if not isinstance(cases, list):
        raise ValueError("host_preflight_cases_missing")
    indexed = {case.get("case_id"): case for case in cases if isinstance(case, Mapping)}
    if len(indexed) != len(plan["cases"]):
        raise ValueError("host_preflight_case_count_mismatch")
    return indexed


def run_source_baseline(
    repo: Path,
    external_root: Path,
    output: Path,
    plan: Mapping[str, Any],
    preflight_path: Path,
) -> dict[str, Any]:
    validate_plan(plan)
    preflight = load_object(preflight_path)
    preflight_cases = _validate_preflight(preflight, plan)
    _ensure_empty_output(output)
    repo_before = _git_snapshot(repo)
    if repo_before["status"]:
        raise ValueError("repository_must_be_clean_before_source_baseline")
    target_path = repo / _safe_relative(plan["target_profile"]["path"])
    if sha_file(target_path) != plan["target_profile"]["sha256"]:
        raise ValueError("target_profile_hash_mismatch")
    target = resolve_target_profile(plan["target_profile"]["name"])
    cases: list[dict[str, Any]] = []
    for case in plan["cases"]:
        material = materialize_case(repo, external_root, case)
        prior = preflight_cases[case["case_id"]]
        if prior.get("status") != "passed" or prior.get("material_identity") != material["identity"]:
            raise ValueError(f"preflight_material_mismatch:{case['case_id']}")
        case_root = output / "cases" / case["case_id"]
        case_root.mkdir(parents=True)
        suite_id = f"{case['case_id']}.public"
        suite = TestSuiteSpec(
            suite_id=suite_id,
            split=EvaluationSplit.PUBLIC,
            suite_version="r5.1-p4-v1",
            case_count=case["public_case_count"],
            testbench_path=f"frozen:{case['case_id']}",
            runtime_contract=case["runtime_contract"],
        )
        task = TaskSpec(
            task_id=case["case_id"],
            kernel_path=f"frozen:{case['case_id']}:raw-source",
            kernel_name=case["top"],
            target=target,
            mode=RunMode.REFACTOR,
            test_suites=(suite,),
        )
        validation_budget = plan["invariants"]["validation_budget_per_case"]
        budget = BudgetManager(
            BudgetLimits(
                max_llm_calls=0,
                max_tool_calls=validation_budget["tool_calls"],
                max_compile_calls=validation_budget["compile_calls"],
                max_csim_calls=validation_budget["csim_calls"],
                max_csynth_calls=validation_budget["csynth_calls"],
                max_cosim_calls=validation_budget["cosim_calls"],
                max_tokens=0,
                max_cost_usd=0,
            )
        )
        trace = TraceRecorder(
            f"{case['case_id']}.source-baseline",
            task_id=case["case_id"],
            output_path=case_root / "trace.jsonl",
        )
        context = RunContext(
            run_id=f"{case['case_id']}.source-baseline",
            task=task,
            budget=budget,
            trace=trace,
        )
        factory = LocalCandidateValidationHandlerFactory(
            case_root / "work",
            csim_timelimit=plan["timeouts_s"]["csim"],
            csynth_timelimit=plan["timeouts_s"]["csynth"],
            cosim_timelimit=plan["timeouts_s"]["cosim"],
            cosim_policy="required",
        )
        request = CandidateValidationPlanRequest(
            task=task,
            candidate_code=material["design"],
            original_code=REFERENCE_STUB,
            preflight_testbench_code=material["testbench"],
            suite_testbench_codes={suite_id: material["testbench"]},
            attempt=0,
            validation_id=f"{case['case_id']}.source-baseline",
            reference_top_function=None,
            candidate_top_function=case["top"],
        )
        execution_error = None
        outcome = None
        try:
            outcome = ValidationOrchestrator(factory.build(request)).run_detailed(
                context,
                validation_id=f"{case['case_id']}.source-baseline",
            )
        except Exception as exc:
            execution_error = {"type": type(exc).__name__, "message": str(exc)[:1000]}
        usage = budget.snapshot().to_dict()
        if outcome is None:
            classification = "infrastructure_failure"
            terminal_state = "execution_error"
            owners: set[str] = set()
            categories: list[str] = []
            stages = {
                "S0_host_oracle": "passed",
                "S1_public_csim": "not_run",
                "S2_csynth": "not_run",
                "S3_public_cosim": "not_run",
            }
            validation = None
            terminal_feedback = None
        else:
            terminal_state = outcome.terminal_state.value
            terminal_feedback = outcome.terminal_report
            owners = (
                set()
                if terminal_feedback is None
                else {item.owner.value for item in terminal_feedback.items}
            )
            categories = (
                []
                if terminal_feedback is None
                else sorted({item.category.value for item in terminal_feedback.items})
            )
            classification = classify_terminal(
                accepted=outcome.result.accepted,
                terminal_state=terminal_state,
                owners=owners,
                physical_execution=_physical_for_state(terminal_state, usage),
            )
            stages = _stage_statuses(outcome)
            validation = outcome.to_dict()
            terminal_feedback = (
                None if terminal_feedback is None else terminal_feedback.to_dict()
            )
        if classification not in ALLOWED_CLASSIFICATIONS:
            raise AssertionError("unexpected source baseline classification")
        cases.append(
            {
                "case_id": case["case_id"],
                "source_id": case["source_id"],
                "source_tier": case["source_tier"],
                "decision": case["decision"],
                "partition": case["partition"],
                "algorithm_family": case["algorithm_family"],
                "known_prior_outcome": case["known_prior_outcome"],
                "role": case["role"],
                "material_identity": material["identity"],
                "classification": classification,
                "terminal_state": terminal_state,
                "failure_owners": sorted(owners),
                "failure_families": categories,
                "stage_statuses": stages,
                "budget_usage": usage,
                "validation": validation,
                "terminal_feedback": terminal_feedback,
                "execution_error": execution_error,
                "evidence_inventory": _evidence_inventory(case_root, output),
            }
        )
    repo_after = _git_snapshot(repo)
    provider_calls = sum(case["budget_usage"]["llm_calls"] for case in cases)
    vitis_launches = sum(
        case["budget_usage"]["csim_calls"]
        + case["budget_usage"]["csynth_calls"]
        + case["budget_usage"]["cosim_calls"]
        for case in cases
    )
    classifications = Counter(case["classification"] for case in cases)
    stage_reach = {
        stage: sum(case["stage_statuses"][stage] != "not_run" for case in cases)
        for stage in ("S0_host_oracle", "S1_public_csim", "S2_csynth", "S3_public_cosim")
    }
    result = {
        "schema_version": 1,
        "run_id": "v2.3-r5.1-p4-source-baseline-v1",
        "status": "complete",
        "evidence_root": str(output.resolve()),
        "plan_sha256": sha_value(plan),
        "preflight_file_sha256": sha_file(preflight_path),
        "preflight_result_sha256": preflight["result_sha256"],
        "repo_head": repo_before["head"],
        "target_profile": target.to_dict(),
        "cases": cases,
        "classification_counts": dict(sorted(classifications.items())),
        "stage_reach": stage_reach,
        "provider_calls": provider_calls,
        "vitis_launches": vitis_launches,
        "git_history_mutations": int(repo_before["head"] != repo_after["head"]),
        "working_tree_mutations": int(repo_before["status"] != repo_after["status"]),
        "r5_accepted": False,
        "r6_started": False,
    }
    result["result_sha256"] = sha_value(result)
    _write_json(output / "result.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "run"):
        child = subparsers.add_parser(name)
        child.add_argument("--repo", type=Path, default=ROOT)
        child.add_argument("--external-root", type=Path, required=True)
        child.add_argument("--output", type=Path, required=True)
        child.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
        if name == "run":
            child.add_argument("--preflight", type=Path, required=True)
    args = parser.parse_args()
    plan = load_object(args.plan)
    if args.command == "preflight":
        result = run_host_preflight(
            args.repo.resolve(),
            args.external_root.resolve(),
            args.output.resolve(),
            plan,
        )
        print(f"R5_1_P4_HOST_PREFLIGHT_STATUS={result['status']}")
        print(f"R5_1_P4_HOST_PREFLIGHT_CASES={len(result['cases'])}")
        print("PROVIDER_CALLS=0")
        print("VITIS_LAUNCHES=0")
        return 0 if result["status"] == "passed" else 1
    result = run_source_baseline(
        args.repo.resolve(),
        args.external_root.resolve(),
        args.output.resolve(),
        plan,
        args.preflight.resolve(),
    )
    print(f"R5_1_P4_SOURCE_BASELINE_STATUS={result['status']}")
    print(f"R5_1_P4_SOURCE_BASELINE_CASES={len(result['cases'])}")
    print(f"PROVIDER_CALLS={result['provider_calls']}")
    print(f"VITIS_LAUNCHES={result['vitis_launches']}")
    print(f"CLASSIFICATIONS={json.dumps(result['classification_counts'], sort_keys=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
