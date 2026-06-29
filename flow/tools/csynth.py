import os, requests
from flow.base_agent import HLSAgentLoader
from autogen.agentchat.group import ContextVariables # type: ignore
import flow.tools as tools
from typing import Optional, Dict, Any

HLS_SERVER_URL = os.getenv("HLS_SERVER_URL")

CSYNTH_TIMEOUT = 300
ERROR_LINES = 15
CSYNTH_CMD = "vitis-run --mode hls --tcl --input_file vitis.tcl"

def get_error_msg(synth_dir: str) -> str:
    path = os.path.join(synth_dir, "csynth", "solution", "solution.log")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            log_content = f.read()
            return "\n".join(log_content.strip().splitlines()[-ERROR_LINES:])
    return ""

def make_vitis_tcl(top_kernel: str, file_list: list[str]) -> str:
    tcl_lines = []
    tcl_lines.append('open_project csynth')
    tcl_lines.append(f'set_top {top_kernel}')
    for fname in file_list:
        tcl_lines.append(f'add_files "{fname}" -cflags " -D XILINX "')
    tcl_lines.append('open_solution -flow_target vitis solution')
    tcl_lines.append('set_part xcu200-fsgd2104-2-e')
    tcl_lines.append('create_clock -period 200MHz -name default')
    tcl_lines.append('csynth_design')
    tcl_lines.append('close_project')
    tcl_lines.append('exit')
    return '\n'.join(tcl_lines)

def make_csynth_script(work_dir: str, top_kernel: str, file_list: dict[str, str]):
    for fname, fcontent in file_list.items():
        file_path = os.path.join(work_dir, fname)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(fcontent)

    tcl_content = make_vitis_tcl(top_kernel, list(file_list.keys()))
    tcl_path = os.path.join(work_dir, "vitis.tcl")
    with open(tcl_path, "w", encoding="utf-8") as f:
        f.write(tcl_content)

def run_csynth(
    work_dir: str,
    cv: ContextVariables,
    timelimit: int = CSYNTH_TIMEOUT,
):
    top_kernel_name = cv["new_kernel_name"]
    file_list = {f"{top_kernel_name}.cpp": cv["curr_code"]}
    make_csynth_script(work_dir, top_kernel_name, file_list)
    print(f">>> Synthesizing in {work_dir}... <<<")
    result = tools.general.run_cmd(work_dir, CSYNTH_CMD, timelimit)
    # Check if the synthesis tool actually ran
    if result["returncode"] != 0 and not result.get("timeout", False):
        stderr = result.get("stderr", "")
        if "not found" in stderr or "No such file" in stderr or result["returncode"] == 127:
            raise RuntimeError(f"CSYNTH_CMD failed to launch: {CSYNTH_CMD}\nstderr: {stderr[:500]}")
    rpt_path = os.path.join(work_dir, "csynth", "solution", "syn", "report", f"{top_kernel_name}_csynth.rpt")
    if result["timeout"]:
        if (not os.path.exists(rpt_path)):
            status = "timeout"
        else:
            status = "succeeded"
    elif (result["returncode"] != 0) or (not os.path.exists(rpt_path)):
        status = "csynth_failed"
    else:
        status = "succeeded"
    # Override status if synthesizability check passed (even on timeout/failure)
    log_path = os.path.join(work_dir, "csynth", "solution", "solution.log")
    if status != "succeeded" and os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            if "Finished Checking Synthesizability:" in f.read():
                status = "succeeded"
    error_msg = get_error_msg(work_dir)
    return status, error_msg


def run_csynth_remote(
    cv: ContextVariables,
    timelimit: int = CSYNTH_TIMEOUT,
):
    if not HLS_SERVER_URL:
        raise RuntimeError("HLS_SERVER_URL environment variable not set")
    payload = {
        "curr_code": cv["curr_code"],
        "new_kernel_name": cv["new_kernel_name"],
        "timelimit": timelimit,
    }
    resp = requests.post(f"{HLS_SERVER_URL}/csynth", json=payload, timeout=timelimit + 30)
    resp.raise_for_status()
    data = resp.json()
    return data["status"], data["error_msg"]


def fixing_csynth(
    cv: ContextVariables,
    status: str,
    error_msg: str,
    llm_config: Optional[Dict[str, Any]] = None
):
    fixer_loader = HLSAgentLoader("flow/agents/fixing.yaml", llm_config_override=llm_config)
    csynth_fixer = fixer_loader.load_agent("csynth_fixer")
    if status == "timeout":
        issue = f"Synthesis timeout, the last stage stdout is:\n{error_msg}\n\n"
    elif status == "csynth_failed":
        issue = f"Synthesis failed with the error message:\n{error_msg}\n\n"
    msg = issue +(
        f"Current code:\n {cv['curr_code']}\n\n"
        f"Top level kernel name: {cv['new_kernel_name']}"
    )
    resp = csynth_fixer.run(message=msg, max_turns=1)
    resp.process()
    response_content = tools.general.strip_thinking(resp.messages[1]["content"])
    cv["curr_code"] = tools.general.extract_code(response_content)[0]