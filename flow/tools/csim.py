import os, json, requests
from flow.base_agent import HLSAgentLoader
from autogen.agentchat.group import ContextVariables # type: ignore
import flow.tools as tools
from pydantic import BaseModel
from typing import Annotated, Optional, Dict, Any

HLS_SERVER_URL = os.getenv("HLS_SERVER_URL")

CSIM_TIMEOUT = 60
CSIM_COMPILE_CMD = "g++ -D__SYNTHESIS__ -I$XILINX_HLS/include -O2 -Wno-unknown-pragmas testbench.cpp orig_code.cpp refactor_code.cpp -o csim"
CSIM_CMD = "./csim"

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

def run_csim(
    work_dir: str,
    cv: ContextVariables,
    timelimit: int = CSIM_TIMEOUT,
):
    make_csim_script(work_dir, cv)
    compile_res = tools.general.run_cmd(work_dir, CSIM_COMPILE_CMD, timelimit)
    if compile_res["returncode"] != 0:
        return "tb_compile_failed", compile_res["stderr"]
    run_res = tools.general.run_cmd(work_dir, CSIM_CMD, timelimit)
    if run_res["returncode"] != 0:
        # Merge stdout+stderr so the csim_fixer sees all diagnostic output,
        # even when the TB prints mismatch info to stdout instead of stderr
        # (violating testbench.yaml convention but common in practice).
        combined = (run_res.get("stdout", "") + "\n" + run_res.get("stderr", "")).strip()
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