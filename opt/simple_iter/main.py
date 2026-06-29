# naive implementation with openai API

import os, argparse, dotenv, json, shutil, shlex, subprocess
from datetime import datetime
from typing import Tuple, Optional, Dict, Any
import xml.etree.ElementTree as ET

from autogen.agentchat.group import ContextVariables

from flow.tools.csynth import make_csynth_script
from flow.tools.general import run_cmd
from opt.tools.testbench import gen_tb_prior
from opt.utils import get_model, get_response, extract_c_or_cpp_code


dotenv.load_dotenv('.env', override=True)
RUN_DIR = os.getenv('RUN_DIR')
WORK_DIR = os.getenv('WORK_DIR')
RESOURCE_UTILIZATION_LIMIT = 0.8


def remove_hls_pragmas(code: str) -> str:
    """
    Remove any line that starts with '#pragma HLS' (allowing leading spaces).
    """
    # return "\n".join(
    #     line for line in code.splitlines()
    #     if not re.match(r"^\s*#\s*pragma\s+HLS\b", line)
    # )

    # Brady: not removing comments
    return code


def log_output(output_dir: str, message: str, role: str = None) -> None:
    """Log message to both stdout and output.txt for capture by parallel_eval.py"""
    if role:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted_message = f"[{ts}] {role.upper()}:\n{message}\n" + ("-" * 80)
    else:
        formatted_message = message
    
    print(formatted_message)
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "output.txt"), "a", encoding="utf-8") as f:
        f.write(f"{formatted_message}\n")


def run_csynth(
    work_dir: str,
    top_kernel_name: str,
    curr_code: str,
    timelimit: int = 300,
):
    os.makedirs(work_dir, exist_ok=True)
    file_list = {f"{top_kernel_name}.cpp": curr_code}
    make_csynth_script(work_dir, top_kernel_name, file_list)
    cmd = "vitis-run --mode hls --tcl vitis.tcl"
    print(f">>> Synthesizing in {work_dir}... <<<")
    result = run_cmd(work_dir, cmd, timelimit)
    rpt_path = os.path.join(work_dir, "csynth", "solution", "syn", "report", f"{top_kernel_name}_csynth.rpt")
    xml_path = os.path.join(work_dir, "csynth", "solution", "syn", "report", f"{top_kernel_name}_csynth.xml")
    log_path = os.path.join(work_dir, "csynth", "solution", "solution.log")
    
    def get_error_msg(path: str) -> str:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
            # Index of first ERROR line (if any)
            first_err_idx = next((i for i, ln in enumerate(lines) if ln.startswith("ERROR:")), None)
            kept: list[str] = []
            if first_err_idx is not None:
                # Keep up to 5 lines before the first ERROR
                start = max(0, first_err_idx - 5)
                kept.extend(lines[start:first_err_idx])
                # Keep all lines that begin with "ERROR:" (one-line per error)
                kept.extend([ln for ln in lines[first_err_idx:] if ln.startswith("ERROR:")])
            # Join; if no ERROR lines exist, return empty string
            return "\n".join(kept).strip()
        return ""
    
    csynth_error_msg = get_error_msg(log_path)
    if result["timeout"]:
        csynth_status = "timeout"
        return csynth_status, csynth_error_msg, None, None
    elif (result["returncode"] != 0) or (not os.path.exists(rpt_path)):
        csynth_status = "csynth_failed"
        return csynth_status, csynth_error_msg, None, None
    else:
        csynth_status = "succeeded"
        return csynth_status, csynth_error_msg, rpt_path, xml_path


def run_tb(
    output_dir: str,
    orig_code_path: str,
    optimized_code_path: str,
    tb_path: str
) -> Tuple[int, Optional[str]]:
    """
    Compile and run 'csim' in output_dir.

    Returns:
      (1, error_log) if compilation fails (also prints the error log)
      (2, stderr)    if running ./csim returns non-zero
      (0, None)      if everything succeeds
    """
    os.makedirs(output_dir, exist_ok=True)

    # Quote paths for safety in shell
    tb_q = shlex.quote(tb_path)
    orig_q = shlex.quote(orig_code_path)
    ref_q = shlex.quote(optimized_code_path)

    compile_cmd = f"g++ -I$XILINX_HLS/include -O2 -Wno-unknown-pragmas {tb_q} {orig_q} {ref_q} -o csim"

    # 1) Compile
    compile_res = subprocess.run(
        compile_cmd,
        cwd=output_dir,
        shell=True,              # allow $XILINX_HLS expansion
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT # capture all compiler logs together
    )
    if compile_res.returncode != 0:
        error_log = compile_res.stdout or ""
        print(error_log)
        return (1, error_log)

    # 2) Run
    run_res = subprocess.run(
        "./csim",
        cwd=output_dir,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    if run_res.returncode != 0:
        # Return only stderr per requirement
        return (2, run_res.stderr or "")

    # 3) Success
    return (0, None)


def safe_int_parse(text: Optional[str]) -> Optional[int]:
    """Safely parse an integer from text, handling 'undef' and other non-numeric values."""
    if not text:
        return None
    text = text.strip().lower()
    if text in ['undef', 'undefined', 'none', '']:
        return None
    try:
        return int(text)
    except (ValueError, TypeError):
        return None


def safe_float_parse(text: Optional[str]) -> Optional[float]:
    """Safely parse a float from text, handling 'undef' and other non-numeric values."""
    if not text:
        return None
    text = text.strip().lower()
    if text in ['undef', 'undefined', 'none', '']:
        return None
    try:
        return float(text)
    except (ValueError, TypeError):
        return None


def parse_vitis_log():
    raise NotImplementedError()


def parse_xml_report(xml_path: str) -> Optional[Dict[str, Any]]:
    """Parse the csynth XML report to extract performance and resource data."""
    if not os.path.exists(xml_path):
        return None
    
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        
        result = {
            "performance": {},
            "resources": {},
            "available_resources": {},
            "resource_percentages": {}
        }
        
        # Parse performance estimates
        perf_elem = root.find(".//PerformanceEstimates")
        if perf_elem is not None:
            # Timing analysis
            timing_elem = perf_elem.find(".//SummaryOfTimingAnalysis")
            if timing_elem is not None:
                unit_elem = timing_elem.find("unit")
                period_elem = timing_elem.find("EstimatedClockPeriod")
                if period_elem is not None and period_elem.text:
                    period_value = safe_float_parse(period_elem.text)
                    if period_value is not None:
                        result["performance"]["clock_period"] = {
                            "value": period_value,
                            "unit": unit_elem.text if unit_elem is not None and unit_elem.text else "ns"
                        }
            
            # Overall latency
            latency_elem = perf_elem.find(".//SummaryOfOverallLatency")
            if latency_elem is not None:
                worst_latency_elem = latency_elem.find("Worst-caseLatency")
                worst_realtime_elem = latency_elem.find("Worst-caseRealTimeLatency")
                
                if worst_latency_elem is not None and worst_latency_elem.text:
                    latency_value = safe_int_parse(worst_latency_elem.text)
                    if latency_value is not None:
                        result["performance"]["worst_case_latency"] = {
                            "value": latency_value,
                            "unit": latency_elem.find("unit").text if latency_elem.find("unit") is not None else "clock cycles"
                        }
                
                if worst_realtime_elem is not None and worst_realtime_elem.text:
                    # Extract value and unit from "X.XXX us" format
                    realtime_text = worst_realtime_elem.text.strip()
                    if realtime_text and realtime_text.lower() not in ['undef', 'undefined']:
                        parts = realtime_text.split()
                        if len(parts) == 2:
                            value = safe_float_parse(parts[0])
                            if value is not None:
                                unit = parts[1]
                                result["performance"]["worst_case_realtime_latency"] = {
                                    "value": value,
                                    "unit": unit
                                }
        
        # Parse area estimates
        area_elem = root.find(".//AreaEstimates")
        if area_elem is not None:
            resources_elem = area_elem.find(".//Resources")
            available_elem = area_elem.find(".//AvailableResources")
            
            resource_names = ["BRAM_18K", "FF", "LUT", "URAM", "DSP"]
            
            if resources_elem is not None:
                for name in resource_names:
                    elem = resources_elem.find(name)
                    if elem is not None and elem.text:
                        value = safe_int_parse(elem.text)
                        if value is not None:
                            result["resources"][name] = value
            
            if available_elem is not None:
                for name in resource_names:
                    elem = available_elem.find(name)
                    if elem is not None and elem.text:
                        value = safe_int_parse(elem.text)
                        if value is not None:
                            result["available_resources"][name] = value
            
            # Calculate percentages
            for name in resource_names:
                if name in result["resources"] and name in result["available_resources"]:
                    used = result["resources"][name]
                    available = result["available_resources"][name]
                    if available > 0:
                        percentage = (used / available) * 100.0
                        result["resource_percentages"][name] = {
                            "used": used,
                            "available": available,
                            "percentage": percentage
                        }
        
        return result
    
    except Exception as e:
        print(f"Error parsing XML {xml_path}: {e}")
        return None


def persist_best_design(output_dir: str, best_design: Optional[Dict[str, Any]], limit: float = RESOURCE_UTILIZATION_LIMIT) -> None:
    """Write the best design (if any) to best_design.json for downstream aggregation."""
    os.makedirs(output_dir, exist_ok=True)
    payload: Dict[str, Any]
    if best_design:
        payload = best_design
    else:
        payload = {
            "found": False,
            "resource_limit": limit,
            "reason": "No design met latency/resource constraints",
        }
    out_path = os.path.join(output_dir, "best_design.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    status = "found" if best_design else "not found"
    log_output(output_dir, f"BEST_DESIGN_STATUS: {status} (saved to {out_path})", "tool")


def result_dict_to_summary(result: Dict[str, Any]):
    """
    Convert the parsed csynth XML dictionary into a readable multi-line string.
    Only reports latency, clock period, realtime latency, and resource usage.
    """
    assert result, "result dictionary is empty"

    lines: list[str] = []
    perf = result.get("performance", {})

    def add_metric(key: str, label: str):
        data = perf.get(key, {})
        if not isinstance(data, dict):
            return
        value = data.get("value")
        unit = data.get("unit")
        if value is None:
            value = "?"
        lines.append(f"{label}: {value} {unit}" if unit else f"{label}: {value}")

    add_metric("worst_case_latency", "Worst-case latency")
    add_metric("clock_period", "Clock period")
    add_metric("worst_case_realtime_latency", "Worst-case realtime latency")

    res_percentages = result.get("resource_percentages", {})
    if res_percentages:
        lines.append("Resource usage:")
        preferred_order = ["BRAM_18K", "FF", "LUT", "URAM", "DSP"]

        def add_resource(name: str, stats: Dict[str, Any]):
            used = stats.get("used")
            available = stats.get("available")
            pct = stats.get("percentage")
            if used is None or available is None or pct is None:
                lines.append(f"  {name}: data unavailable")
            else:
                lines.append(f"  {name}: {used}/{available} ({pct:.2f}%)")

        for res_name in preferred_order:
            if res_name in res_percentages:
                add_resource(res_name, res_percentages[res_name])

        # Include any other resources that may appear
        for res_name, stats in res_percentages.items():
            if res_name not in preferred_order:
                add_resource(res_name, stats)

    assert lines, "No relevant data found in result dictionary"
    return "\n".join(lines)


def parse_vitis_report(rpt_path: str, xml_path: str):
    with open(rpt_path, 'r') as f:
        raw_rpt = f.read()
        f.close()
    st = raw_rpt.find('Utilization Estimates')
    st = raw_rpt.find('+ Detail:', st + 1)
    raw_rpt_trim = raw_rpt[:st]
    result = parse_xml_report(xml_path)
    result_summary = result_dict_to_summary(result)
    return raw_rpt_trim, result_summary, result


def new_solution_message(raw_rpt: str, result_summary: str, add_raw_rpt: bool = True):
    user_message = f"""\
The synethesis and the tb success, try to further optimize. Try diverse HLS optimization techniques. If no latency is presented (?), try to resolve that by always having static loop bound (you can put break inside loop). Maintain all resource utilization below 80%.

Here is the result summary for your reference:

{result_summary}

"""
    if add_raw_rpt:
        user_message += f"Here is the raw report (trimmed):\n\n{raw_rpt}"
    print(user_message)
    return user_message


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--kernel_path', type=str, required=True)
    parser.add_argument('--top_name', type=str, required=True)
    parser.add_argument('--config_path', type=str, default=None)
    parser.add_argument('--gen_bench_prior', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument('--model', type=str, default='gpt-5.1')
    parser.add_argument('--iterations', type=int, default=4, help='Number of iterations per run')
    parser.add_argument('--output_dir', type=str, default=None, help='Isolated working directory (optional)')
    parser.add_argument('--add_raw_rpt', action=argparse.BooleanOptionalAction, default=True, help='Include truncated raw report in feedback')
    parser.add_argument('--reasoning_effort', type=str, default='high', help='Reasoning effort of the model (default: high)')
    args = parser.parse_known_args()[0]
    
    kernel_path = args.kernel_path
    top_name = args.top_name
    vendor = 'gemini' if 'gemini' in args.model else 'openai'
    model_name = args.model
    agent_model_config = {
        "api_type": "google" if vendor == "gemini" else "openai",
        "model": model_name,
        "reasoning_effort": args.reasoning_effort,
    }
    client = get_model(vendor)
    
    # Use provided output_dir or fall back to default
    if args.output_dir:
        output_dir = args.output_dir
        print(f'Using isolated working directory: {output_dir}')
    else:
        output_dir = os.path.join(RUN_DIR, datetime.now().strftime("%Y%m%d"), datetime.now().strftime("%H%M%S"))
        print(f'Using default working directory: {output_dir}')
    
    os.makedirs(output_dir, exist_ok=True)  # ensure trace/log/outputs can be written
    
    with open(kernel_path, 'r') as f:
        curr_code = f.read()
        
    if args.config_path:
        with open(args.config_path, 'r') as f:
            obj = json.load(f)
        top_name = obj['new_top_name']
        top_signature = obj['top_signature']
    else:
        top_signature = None
        
    if args.gen_bench_prior:
        context_variables = ContextVariables(data={
            "orig_code": curr_code,
            "kernel_name": top_name,
            "curr_code": curr_code
        })
        tb, tb_inst, top_name = gen_tb_prior(context_variables, model_config=agent_model_config)
        print(f'New top name is: {top_name}')
        with open(os.path.join(output_dir, 'tb.cpp'), 'w') as f:
            f.write(tb)
        initial_prompt = f"""\
```
{curr_code}
```

Give me the optimized HLS code. Try to ensure static loop bound so that HLS tool can report latency. The new top function name should be: {top_name}.

Here are some instructions you need to follow to connect your code with an existing HLS testbench:

{tb_inst}

Provide a self-contained code, in the block ```c++ ... ```.
"""
    elif top_signature is not None:
        initial_prompt = f"""\
```
{curr_code}
```
Give me the optimized HLS code. The new top function name should be: {top_name}. \
The new top function signature should be {top_signature} \
Provide a single piece of code.
"""
    else:
        initial_prompt = f"""\
```
{curr_code}
```
Give me the optimized HLS code. The top level function name should be: {top_name}. Provide a single piece of code.
"""


    messages = [{"role": "user", "content": initial_prompt}]
    # log the initial user prompt
    log_output(output_dir, initial_prompt, "user")
    best_design: Optional[Dict[str, Any]] = None
    for t in range(args.iterations):
        print(f'Running round {t}')
        resp = get_response(
            client,
            model_name,
            messages,
            reasoning_effort=args.reasoning_effort
        )

        # log assistant/model output
        log_output(output_dir, resp.content, "assistant")
        messages.append({"role": "assistant", "content": resp.content})

        # parse code block and strip any HLS pragmas
        curr_code = extract_c_or_cpp_code(resp.content)

        csynth_status, error_msg, rpt_path, xml_path = run_csynth(os.path.join(output_dir, f"round_{t}"), top_name, curr_code)
        if csynth_status == "timeout":
            user_msg = "the code timeout. try less aggressive optimizations."
            messages.append({"role": "user", "content": user_msg})
            log_output(output_dir, user_msg, "user")
        elif csynth_status == "csynth_failed":
            user_msg = f"failed synthesis with error: \n{error_msg}"
            messages.append({"role": "user", "content": user_msg})
            log_output(output_dir, user_msg, "user")
        else:
            assert csynth_status == "succeeded"
            with open(os.path.join(output_dir, f"round_{t}", 'optimized_code.cpp'), 'w') as f:
                f.write(curr_code)
            log_output(output_dir, f"Synthesizability success at round {t}", "tool")
            shutil.copy(args.kernel_path, os.path.join(output_dir, f"round_{t}", 'orig_code.cpp'))
            shutil.copy(os.path.join(output_dir, "tb.cpp"), os.path.join(output_dir, f"round_{t}", "tb.cpp"))
            ret_code, msg = run_tb(os.path.join(output_dir, f"round_{t}"), 'orig_code.cpp', 'optimized_code.cpp', 'tb.cpp')
            if ret_code != 0:
                user_msg = "able to synthesis but TB failed. The error: "
                if ret_code == 1: 
                    log_output(output_dir, '[ERROR] TB failed to compile')
                    user_msg += "TB failed to compile. "
                if ret_code == 2: 
                    log_output(output_dir, '[ERROR] code not correct')
                    user_msg += "code not correct. "
                if msg: 
                    log_output(output_dir, msg)
                    user_msg += f"error message from tb: {msg}"
                messages.append({"role": "user", "content": user_msg})
            else:
                log_output(output_dir, '[Success] Simulation success')
                # parse the solution.log and the vitis report
                raw_rpt, result_summary, result = parse_vitis_report(rpt_path, xml_path)
                perf = result.get("performance", {}) if isinstance(result, dict) else {}
                worst_rt = perf.get("worst_case_realtime_latency") if isinstance(perf, dict) else None
                if not isinstance(worst_rt, dict) or worst_rt.get("value") is None:
                    log_output(
                        output_dir,
                        f"Round {t} report missing realtime latency; cannot evaluate best design.",
                        "tool"
                    )
                else:
                    resource_pct = result.get("resource_percentages", {}) if isinstance(result, dict) else {}
                    utilization = {}
                    all_within_limit = True
                    for res_name, stats in resource_pct.items():
                        if not isinstance(stats, dict):
                            continue
                        pct = stats.get("percentage")
                        if pct is None:
                            continue
                        fraction = pct / 100.0
                        utilization[res_name] = {
                            "used": stats.get("used"),
                            "available": stats.get("available"),
                            "percentage": pct,
                            "fraction": fraction,
                        }
                        if fraction >= RESOURCE_UTILIZATION_LIMIT:
                            all_within_limit = False

                    if all_within_limit:
                        candidate = {
                            "found": True,
                            "worst_realtime_latency": worst_rt["value"],
                            "latency_unit": worst_rt.get("unit"),
                            "resource_utilization": utilization,
                            "all_resource_within_limit": True,
                            "resource_limit": RESOURCE_UTILIZATION_LIMIT,
                            "iteration": t,
                            "round_dir": f"round_{t}",
                        }
                        if (best_design is None) or (candidate["worst_realtime_latency"] < best_design["worst_realtime_latency"]):
                            best_design = candidate
                            log_output(
                                output_dir,
                                f"New best design at round {t}: "
                                f"{candidate['worst_realtime_latency']} {candidate.get('latency_unit', '')} "
                                f"(resource limit {RESOURCE_UTILIZATION_LIMIT})",
                                "tool"
                            )
                    else:
                        log_output(
                            output_dir,
                            f"Round {t} design skipped for best selection; "
                            f"resource limit {RESOURCE_UTILIZATION_LIMIT} not satisfied.",
                            "tool"
                        )
                messages.append({"role": "user", "content": new_solution_message(raw_rpt, result_summary, args.add_raw_rpt)})
                
    persist_best_design(output_dir, best_design)
    return 0


if __name__ == '__main__':
    import sys
    exit_code = main()
    sys.exit(exit_code)
