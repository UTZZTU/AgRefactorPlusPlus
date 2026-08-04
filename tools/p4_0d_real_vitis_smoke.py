#!/usr/bin/env python3
"""Target-host real Vitis CSIM→CSYNTH→Public RTL COSIM→Hidden smoke."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping

from autogen.agentchat.group import ContextVariables

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
    CsimStageInputs,
    CsimValidationStageHandler,
    PreflightStageInputs,
    PreflightValidationStageHandler,
    RunContext,
    TraceRecorder,
)
from flow.tools.csynth import run_csynth
from flow.tools.vitis_csim import run_vitis_csim
from flow.tools.vitis_cosim import run_vitis_cosim


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _required_file(root: Path, name: str) -> Path:
    path = root / name
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError(f"fixture file missing or unsafe: {path}")
    return path


def _read(root: Path, name: str) -> str:
    value = _required_file(root, name).read_text(encoding="utf-8")
    if not value.strip():
        raise ValueError(f"fixture file is empty: {name}")
    return value


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    copied = json.loads(
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(
                copied,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument(
        "--target-profile",
        default="vitis-2023.2-default",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if isinstance(args.timeout, bool) or args.timeout <= 0:
        raise ValueError("timeout must be positive")
    fixture = args.fixture.resolve()
    output = args.output.resolve()
    if output.exists() and (output.is_symlink() or not output.is_dir()):
        raise ValueError("output must be a real directory")
    output.mkdir(parents=True, exist_ok=True)

    profile = resolve_target_profile(args.target_profile)
    candidate = _read(fixture, "candidate.cpp")
    reference = _read(fixture, "reference.cpp")
    public_testbench = _read(fixture, "public_tb.cpp")
    hidden_testbench = _required_file(fixture, "hidden_tb.cpp")

    budget = BudgetManager(
        BudgetLimits(
            max_tool_calls=20,
            max_compile_calls=10,
            max_csim_calls=2,
            max_csynth_calls=2,
            max_wall_time_s=float(args.timeout * 5),
            max_cosim_calls=2,
        )
    )

    validation_context = RunContext(
        run_id="p4d-real-smoke",
        task=TaskSpec(
            task_id="p4d-real-smoke",
            kernel_path=str(_required_file(fixture, "candidate.cpp")),
            kernel_name="vector_add",
            target=profile,
            mode=RunMode.REFACTOR,
            testbench_path=str(_required_file(fixture, "public_tb.cpp")),
            test_suites=(
                TestSuiteSpec(
                    suite_id="public-smoke",
                    split=EvaluationSplit.PUBLIC,
                    testbench_path=str(
                        _required_file(fixture, "public_tb.cpp")
                    ),
                ),
                TestSuiteSpec(
                    suite_id="hidden-smoke",
                    split=EvaluationSplit.HIDDEN,
                    testbench_path=str(
                        _required_file(fixture, "hidden_tb.cpp")
                    ),
                ),
            ),
        ),
        budget=budget,
        trace=TraceRecorder("p4d-real-smoke", task_id="p4d-real-smoke"),
    )
    preflight_report = PreflightValidationStageHandler(
        PreflightStageInputs(
            work_dir=output / "preflight",
            testbench_code=public_testbench,
            original_code=reference,
            candidate_code=candidate,
            original_top_function="vector_add_reference",
            candidate_top_function="vector_add",
        )
    )(validation_context)

    csim_context = ContextVariables(
        data={
            "orig_code": reference,
            "curr_code": candidate,
            "testbench": public_testbench,
            "candidate_top_function": "vector_add",
            "target_profile": profile,
            "csim_execution_backend": "native_vitis",
        }
    )
    csim_result = run_vitis_csim(
        str(output / "public_native_csim"),
        csim_context,
        args.timeout,
        budget=budget,
    )

    csynth_root = output / "csynth"
    csynth_root.mkdir(parents=True, exist_ok=True)
    csynth_result = run_csynth(
        str(csynth_root),
        ContextVariables(
            data={
                "curr_code": candidate,
                "new_kernel_name": "vector_add",
                "target_profile": profile,
            }
        ),
        args.timeout,
        budget=budget,
    )

    cosim_result = run_vitis_cosim(
        work_dir=output / "public_rtl_cosim",
        original_code=reference,
        candidate_code=candidate,
        testbench_code=public_testbench,
        candidate_top_function="vector_add",
        target_profile=profile,
        timelimit=args.timeout,
        budget=budget,
        suite_id="public-smoke",
    )

    hidden_report = CsimValidationStageHandler(
        CsimStageInputs(
            work_dir=output / "hidden",
            original_code=reference,
            candidate_code=candidate,
            suite_testbench_codes={
                "hidden-smoke": hidden_testbench.read_text(encoding="utf-8")
            },
            timelimit=args.timeout,
            execution_backend="host_differential",
        ),
        split=EvaluationSplit.HIDDEN,
    )(validation_context)

    usage = budget.snapshot().to_dict()
    evidence_sha = cosim_result.get("evidence_sha256")
    payload = {
        "schema_version": 1,
        "real_vitis_used": True,
        "network_llm_used": False,
        "preflight_passed": not preflight_report.blocking,
        "preflight_report_id": preflight_report.report_id,
        "public_native_csim_passed": (
            isinstance(csim_result, tuple)
            and len(csim_result) == 2
            and csim_result[0] == "succeeded"
        ),
        "csynth_passed": (
            isinstance(csynth_result, tuple)
            and len(csynth_result) == 2
            and csynth_result[0] == "succeeded"
        ),
        "public_rtl_cosim_passed": (
            cosim_result.get("status") == "passed"
            and cosim_result.get("version_probe_launched") is True
            and cosim_result.get("cosim_launched") is True
            and cosim_result.get("returncode") == 0
            and isinstance(evidence_sha, str)
            and _SHA256_RE.fullmatch(evidence_sha) is not None
        ),
        "hidden_passed": not hidden_report.blocking,
        "hidden_report_id": hidden_report.report_id,
        "hidden_evidence_view": hidden_report.metadata.get("evidence_view"),
        "execution_order": [
            "preflight",
            "public_native_vitis_csim",
            "csynth",
            "public_rtl_cosim",
            "hidden",
        ],
        "budget_usage": usage,
        "cosim": cosim_result,
        "fixture_sha256": {
            name: sha256(_required_file(fixture, name).read_bytes()).hexdigest()
            for name in (
                "candidate.cpp",
                "reference.cpp",
                "public_tb.cpp",
                "hidden_tb.cpp",
            )
        },
    }
    _atomic_json(output / "real_vitis_chain.json", payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))

    accepted = (
        payload["preflight_passed"]
        and payload["public_native_csim_passed"]
        and payload["csynth_passed"]
        and payload["public_rtl_cosim_passed"]
        and payload["hidden_passed"]
        and usage.get("tool_calls") == 15
        and usage.get("compile_calls") == 7
        and usage.get("csim_calls") == 2
        and usage.get("csynth_calls") == 1
        and usage.get("cosim_calls") == 1
    )
    if not accepted:
        return 1
    print(
        "P4_0D_REAL_VITIS_CHAIN_PASSED "
        "preflight=true csim=true csynth=true cosim=true hidden=true "
        "network_llm_used=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
