import os, json, requests
from flow.base_agent import HLSAgentLoader
from autogen.agentchat.group import ContextVariables # type: ignore
import flow.tools as tools
from pydantic import BaseModel
from typing import Annotated, Optional, Dict, Any

from agrefactor.runtime.budget import (
    BudgetExceededError,
    BudgetManager,
    BudgetUsage,
)

HLS_SERVER_URL = os.getenv("HLS_SERVER_URL")

CSIM_TIMEOUT = 60
CSIM_COMPILE_CMD = "g++ -D__SYNTHESIS__ -I$XILINX_HLS/include -O2 -Wno-unknown-pragmas testbench.cpp orig_code.cpp refactor_code.cpp -o csim"
CSIM_CMD = "./csim"
CSIM_COMPILE_BUDGET_INCREMENT = {
    "tool_calls": 1,
    "compile_calls": 1,
}
CSIM_EXECUTE_BUDGET_INCREMENT = {
    "tool_calls": 1,
    "csim_calls": 1,
}
CSIM_FULL_PLAN_BUDGET_INCREMENT = {
    "tool_calls": 2,
    "compile_calls": 1,
    "csim_calls": 1,
}

class FilterResult(BaseModel):
    tb_updated: Annotated[str, "empty string if no changes needed; otherwise the new testbench code"]
    code_updated: Annotated[str, "empty string if no changes needed; otherwise the new refactored code"]

def make_csim_script(work_dir: str, cv: ContextVariables):
    with open(os.path.join(work_dir, "orig_code.cpp"), "w", encoding="utf-8") as f:
        f.write(cv["orig_code"])
    with open(os.path.join(work_dir, "refactor_code.cpp"), "w", encoding="utf-8") as f:
        f.write(cv["curr_code"])
    with open(os.path.join(work_dir, "testbench.cpp"), "w", encoding="utf-8") as f:
        f.write(cv["testbench"])

def _write_json(path: str, payload: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(
            payload,
            stream,
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        stream.write("\n")


def _budget_usage_to_dict(usage: BudgetUsage) -> dict[str, Any]:
    return {
        "llm_calls": usage.llm_calls,
        "tool_calls": usage.tool_calls,
        "compile_calls": usage.compile_calls,
        "csim_calls": usage.csim_calls,
        "csynth_calls": usage.csynth_calls,
        "tokens": usage.tokens,
        "cost_usd": usage.cost_usd,
        "elapsed_s": usage.elapsed_s,
    }


def _blocked_budget_evidence(
    exc: BudgetExceededError,
    *,
    checkpoint: str,
    requested_increment: dict[str, int],
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "checkpoint": checkpoint,
        "requested_increment": dict(requested_increment),
        "resource": exc.resource,
        "limit": exc.limit,
        "attempted": exc.attempted,
    }


def _build_csim_invocation(
    *,
    work_dir: str,
    timelimit: int,
    budget: BudgetManager | None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "phase": "csim",
        "work_dir": os.path.abspath(work_dir),
        "source_files": [
            "testbench.cpp",
            "orig_code.cpp",
            "refactor_code.cpp",
        ],
        "compile_command": CSIM_COMPILE_CMD,
        "simulation_command": CSIM_CMD,
        "timeout_seconds": timelimit,
        "budget": {
            "status": (
                "not_configured"
                if budget is None
                else "pending"
            ),
            "planned_increment": dict(
                CSIM_FULL_PLAN_BUDGET_INCREMENT
            ),
            "compile": {
                "status": (
                    "not_configured"
                    if budget is None
                    else "pending"
                ),
                "requested_increment": dict(
                    CSIM_COMPILE_BUDGET_INCREMENT
                ),
            },
            "simulation": {
                "status": (
                    "not_configured"
                    if budget is None
                    else "pending"
                ),
                "requested_increment": dict(
                    CSIM_EXECUTE_BUDGET_INCREMENT
                ),
            },
        },
        "compile_execution": {
            "status": "pending",
            "returncode": None,
            "timeout": False,
        },
        "simulation_execution": {
            "status": "pending",
            "returncode": None,
            "timeout": False,
        },
    }


def run_csim(
    work_dir: str,
    cv: ContextVariables,
    timelimit: int = CSIM_TIMEOUT,
    *,
    budget: BudgetManager | None = None,
):
    make_csim_script(work_dir, cv)
    invocation_path = os.path.join(
        work_dir,
        "csim_invocation.json",
    )
    invocation = _build_csim_invocation(
        work_dir=work_dir,
        timelimit=timelimit,
        budget=budget,
    )
    _write_json(invocation_path, invocation)

    if budget is not None:
        try:
            budget.ensure_available(
                **CSIM_FULL_PLAN_BUDGET_INCREMENT
            )
            usage_before = budget.snapshot()
        except BudgetExceededError as exc:
            invocation["budget"].update(
                _blocked_budget_evidence(
                    exc,
                    checkpoint="before_csim_plan",
                    requested_increment=(
                        CSIM_FULL_PLAN_BUDGET_INCREMENT
                    ),
                )
            )
            invocation["compile_execution"]["status"] = (
                "blocked_by_budget"
            )
            invocation["simulation_execution"]["status"] = (
                "blocked_by_budget"
            )
            _write_json(invocation_path, invocation)
            raise

        invocation["budget"].update(
            {
                "status": "available",
                "checkpoint": "before_csim_plan",
                "usage_before": _budget_usage_to_dict(
                    usage_before
                ),
            }
        )
        _write_json(invocation_path, invocation)

        try:
            usage_after_compile = budget.consume(
                **CSIM_COMPILE_BUDGET_INCREMENT
            )
        except BudgetExceededError as exc:
            invocation["budget"].update(
                _blocked_budget_evidence(
                    exc,
                    checkpoint="before_compile_launch",
                    requested_increment=(
                        CSIM_COMPILE_BUDGET_INCREMENT
                    ),
                )
            )
            invocation["compile_execution"]["status"] = (
                "blocked_by_budget"
            )
            invocation["simulation_execution"]["status"] = (
                "not_started"
            )
            _write_json(invocation_path, invocation)
            raise

        invocation["budget"]["status"] = (
            "partially_consumed"
        )
        invocation["budget"]["compile"] = {
            "status": "consumed",
            "checkpoint": "before_compile_launch",
            "requested_increment": dict(
                CSIM_COMPILE_BUDGET_INCREMENT
            ),
            "usage_after": _budget_usage_to_dict(
                usage_after_compile
            ),
        }
        _write_json(invocation_path, invocation)

    try:
        compile_res = tools.general.run_cmd(
            work_dir,
            CSIM_COMPILE_CMD,
            timelimit,
        )
    except Exception as exc:
        invocation["compile_execution"] = {
            "status": "launch_error",
            "returncode": None,
            "timeout": False,
            "error_type": type(exc).__name__,
            "error": str(exc)[:4000],
        }
        invocation["simulation_execution"]["status"] = (
            "not_started"
        )
        _write_json(invocation_path, invocation)
        raise

    invocation["compile_execution"] = {
        "status": "completed",
        "returncode": compile_res.get("returncode"),
        "timeout": bool(compile_res.get("timeout", False)),
    }
    _write_json(invocation_path, invocation)

    if compile_res["returncode"] != 0:
        invocation["simulation_execution"]["status"] = (
            "skipped_after_compile_failure"
        )
        _write_json(invocation_path, invocation)
        return "tb_compile_failed", compile_res["stderr"]

    if budget is not None:
        try:
            usage_after_csim = budget.consume(
                **CSIM_EXECUTE_BUDGET_INCREMENT
            )
        except BudgetExceededError as exc:
            invocation["budget"].update(
                _blocked_budget_evidence(
                    exc,
                    checkpoint="before_csim_launch",
                    requested_increment=(
                        CSIM_EXECUTE_BUDGET_INCREMENT
                    ),
                )
            )
            invocation["simulation_execution"]["status"] = (
                "blocked_by_budget"
            )
            _write_json(invocation_path, invocation)
            raise

        invocation["budget"]["status"] = "consumed"
        invocation["budget"]["simulation"] = {
            "status": "consumed",
            "checkpoint": "before_csim_launch",
            "requested_increment": dict(
                CSIM_EXECUTE_BUDGET_INCREMENT
            ),
            "usage_after": _budget_usage_to_dict(
                usage_after_csim
            ),
        }
        invocation["budget"]["usage_after"] = (
            _budget_usage_to_dict(usage_after_csim)
        )
        _write_json(invocation_path, invocation)

    try:
        run_res = tools.general.run_cmd(
            work_dir,
            CSIM_CMD,
            timelimit,
        )
    except Exception as exc:
        invocation["simulation_execution"] = {
            "status": "launch_error",
            "returncode": None,
            "timeout": False,
            "error_type": type(exc).__name__,
            "error": str(exc)[:4000],
        }
        _write_json(invocation_path, invocation)
        raise

    invocation["simulation_execution"] = {
        "status": "completed",
        "returncode": run_res.get("returncode"),
        "timeout": bool(run_res.get("timeout", False)),
    }
    _write_json(invocation_path, invocation)

    if run_res["returncode"] != 0:
        # Merge stdout+stderr so the csim_fixer sees all diagnostic output,
        # even when the TB prints mismatch info to stdout instead of stderr
        # (violating testbench.yaml convention but common in practice).
        combined = (
            run_res.get("stdout", "")
            + "\n"
            + run_res.get("stderr", "")
        ).strip()
        return "csim_failed", combined[-4000:]
    return "succeeded", ""


def run_csim_remote(
    cv: ContextVariables,
    timelimit: int = CSIM_TIMEOUT,
):
    if not HLS_SERVER_URL:
        raise RuntimeError("HLS_SERVER_URL environment variable not set")
    payload = {
        "orig_code": cv["orig_code"],
        "curr_code": cv["curr_code"],
        "testbench": cv["testbench"],
        "timelimit": timelimit,
    }
    resp = requests.post(f"{HLS_SERVER_URL}/csim", json=payload, timeout=timelimit + 30)
    resp.raise_for_status()
    data = resp.json()
    return data["status"], data["error_msg"]


def fixing_csim(
    cv: ContextVariables,
    status: str,
    error_msg: str,
    llm_config: Optional[Dict[str, Any]] = None
):
    fixer_loader = HLSAgentLoader("flow/agents/fixing.yaml", llm_config_override=llm_config)
    csim_fixer = fixer_loader.load_agent("csim_fixer")
    if status == "tb_compile_failed":
        issue = f"Testbench compilation failed with the error message:\n{error_msg}\n\n"
    elif status == "csim_failed":
        issue = f"Simulation failed with the error message:\n{error_msg}\n\n"
    else:
        issue = f"Simulation failed with unknown error:\n{error_msg}\n\n"
    msg = issue + (
        f"Testbench code:\n{cv['testbench']}\n\n"
        f"Refactored HLS code:\n{cv['curr_code']}\n\n"
        f"Top level kernel name: {cv['new_kernel_name']}"
    )
    resp = csim_fixer.run(message=msg, max_turns=1)
    resp.process()
    try:
        response_content = json.loads(tools.general.strip_thinking(resp.messages[1]["content"]))
        code_tb = tools.general.extract_code(response_content["tb_updated"])[0]
        code_curr = tools.general.extract_code(response_content["code_updated"])[0]
        # Do NOT allow fixer to modify testbench — use ground truth TB only
        # if len(code_tb) > 10:
        #     cv["testbench"] = code_tb
        if len(code_curr) > 10:
            cv["curr_code"] = code_curr
    except:
        pass