# naive implementation with openai API

import os, argparse, dotenv, hashlib, json, shutil, subprocess, time
from datetime import datetime
from typing import Tuple, Optional, Dict, Any
import xml.etree.ElementTree as ET

from autogen.agentchat.group import ContextVariables

from flow.tools.csynth import make_csynth_script, resolve_csynth_command
from agrefactor.config import resolve_target_profile
from flow.tools.general import run_cmd
from opt.tools.testbench import gen_tb_prior
from opt.utils import get_model, get_response, extract_c_or_cpp_code
from opt.simple_iter.harness import (
    LEGACY_HARNESS_CONTRACT_VERSION,
    LegacyHarnessResult,
    run_legacy_harness,
)


dotenv.load_dotenv('.env', override=True)
RUN_DIR = os.getenv('RUN_DIR')
WORK_DIR = os.getenv('WORK_DIR')
RESOURCE_UTILIZATION_LIMIT = 0.8
_EVALUATION_SAFE_LOG = False


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
    """Log output while keeping S3.8 model-facing content audit-safe."""
    content = str(message)
    if _EVALUATION_SAFE_LOG:
        content = (
            f"<REDACTED_CONTENT sha256="
            f"{hashlib.sha256(content.encode('utf-8')).hexdigest()} "
            f"chars={len(content)}>"
        )
    if role:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted_message = f"[{ts}] {role.upper()}:\n{content}\n" + ("-" * 80)
    else:
        formatted_message = content

    print(formatted_message)
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "output.txt"), "a", encoding="utf-8") as f:
        f.write(f"{formatted_message}\n")


def run_csynth(
    work_dir: str,
    top_kernel_name: str,
    curr_code: str,
    timelimit: int = 300,
    *,
    target_profile: str | None = None,
):
    os.makedirs(work_dir, exist_ok=True)
    file_list = {f"{top_kernel_name}.cpp": curr_code}
    profile = resolve_target_profile(target_profile)
    make_csynth_script(
        work_dir,
        top_kernel_name,
        file_list,
        target_profile=profile,
    )
    cmd = resolve_csynth_command(profile)["command"]
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
    tb_path: str,
    *,
    reference_top_name: str,
    candidate_top_name: str,
) -> LegacyHarnessResult:
    """Run the typed, reference-isolated Legacy host harness."""

    return run_legacy_harness(
        output_dir=output_dir,
        reference_path=orig_code_path,
        candidate_path=optimized_code_path,
        testbench_path=tb_path,
        reference_top_name=reference_top_name,
        candidate_top_name=candidate_top_name,
    )

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


def persist_best_design(
    output_dir: str,
    best_design: Optional[Dict[str, Any]],
    limit: float = RESOURCE_UTILIZATION_LIMIT,
    *,
    no_best_reason: str = "No design met latency/resource constraints",
) -> None:
    """Write the best design and selected source for downstream evaluation."""
    os.makedirs(output_dir, exist_ok=True)
    payload: Dict[str, Any]
    if best_design:
        payload = dict(best_design)
        round_dir = payload.get("round_dir")
        if isinstance(round_dir, str) and round_dir:
            source = os.path.join(output_dir, round_dir, "optimized_code.cpp")
            if os.path.isfile(source):
                selected = os.path.join(output_dir, "best_candidate.cpp")
                shutil.copyfile(source, selected)
                payload["best_candidate_path"] = selected
    else:
        payload = {
            "found": False,
            "resource_limit": limit,
            "reason": no_best_reason,
        }
    out_path = os.path.join(output_dir, "best_design.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")
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
    if _EVALUATION_SAFE_LOG:
        print(
            "<REDACTED_FEEDBACK sha256="
            + hashlib.sha256(user_message.encode("utf-8")).hexdigest()
            + f" chars={len(user_message)}>"
        )
    else:
        print(user_message)
    return user_message


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--kernel_path', type=str, required=True)
    parser.add_argument('--reference_path', type=str, default=None, help='Independent reference source used only by the host harness')
    parser.add_argument('--reference_top_name', type=str, default='original_top', help='Required strong reference symbol')
    parser.add_argument('--top_name', type=str, required=True)
    parser.add_argument('--config_path', type=str, default=None)
    parser.add_argument('--gen_bench_prior', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument('--model', type=str, default='gpt-5.1')
    parser.add_argument('--iterations', type=int, default=4, help='Number of iterations per run')
    parser.add_argument('--output_dir', type=str, default=None, help='Isolated working directory (optional)')
    parser.add_argument('--add_raw_rpt', action=argparse.BooleanOptionalAction, default=True, help='Include truncated raw report in feedback')
    parser.add_argument('--reasoning_effort', type=str, default='high', help='Requested reasoning effort (default: high)')
    parser.add_argument('--provider_reasoning_effort', type=str, default=None, help='Effective provider reasoning value used for fair evaluation')
    parser.add_argument('--max_output_tokens', type=int, default=None, help='Effective provider output-token ceiling')
    parser.add_argument('--testbench_path', type=str, default=None, help='Provided evaluation testbench; avoids model-generated testbench')
    parser.add_argument('--target', type=str, default='vitis-2023.2-default', help='Named TargetProfile used for synthesis')
    parser.add_argument('--base_url', type=str, default=None, help='Optional OpenAI-compatible endpoint')
    parser.add_argument('--api_key_env', type=str, default=None, help='Credential environment variable')
    parser.add_argument('--max_model_attempts', type=int, default=None, help='Maximum physical attempts for each model request')
    parser.add_argument('--csynth_timeout_s', type=int, default=600, help='Per-CSYNTH timeout')
    parser.add_argument('--evaluation_mode', action='store_true', help='Write bounded S3.8 Legacy-arm metadata')
    args = parser.parse_known_args()[0]
    global _EVALUATION_SAFE_LOG
    _EVALUATION_SAFE_LOG = bool(args.evaluation_mode)

    kernel_path = args.kernel_path
    top_name = args.top_name
    vendor = 'gemini' if 'gemini' in args.model else 'openai'
    model_name = args.model
    provider_reasoning_effort = (
        args.provider_reasoning_effort
        if args.provider_reasoning_effort is not None
        else args.reasoning_effort
    )
    agent_model_config = {
        "api_type": "google" if vendor == "gemini" else "openai",
        "model": model_name,
        "reasoning_effort": provider_reasoning_effort,
    }
    client = get_model(
        vendor,
        api_key_env=args.api_key_env,
        base_url=args.base_url,
    )
    started_at = time.monotonic()
    model_calls = 0
    completed_rounds = 0
    compile_calls = 0
    csim_calls = 0
    csynth_calls = 0
    tool_calls = 0
    model_output_abstentions = 0
    model_output_reason_counts: Dict[str, int] = {}
    synthesis_successes = 0
    harness_attempts = 0
    harness_passes = 0
    harness_failure_counts: Dict[str, int] = {}
    
    # Use provided output_dir or fall back to default
    if args.output_dir:
        output_dir = args.output_dir
        print(f'Using isolated working directory: {output_dir}')
    else:
        output_dir = os.path.join(RUN_DIR, datetime.now().strftime("%Y%m%d"), datetime.now().strftime("%H%M%S"))
        print(f'Using default working directory: {output_dir}')
    
    os.makedirs(output_dir, exist_ok=True)  # ensure trace/log/outputs can be written

    reference_path = args.reference_path
    if args.evaluation_mode and not reference_path:
        raise ValueError("--reference_path is required in evaluation mode")
    if reference_path is None:
        reference_path = args.kernel_path
    reference_path = os.path.abspath(reference_path)
    if not os.path.isfile(reference_path):
        raise FileNotFoundError(f"reference source not found: {reference_path}")
    reference_top_name = args.reference_top_name.strip()
    if not reference_top_name:
        raise ValueError("reference_top_name must not be empty")

    def write_evaluation_summary(status: str) -> None:
        if not args.evaluation_mode:
            return
        best_path = os.path.join(output_dir, "best_candidate.cpp")
        evaluation = {
            "schema_version": 3,
            "arm": "simple-iter",
            "status": status,
            "model": model_name,
            "target": args.target,
            "requested_reasoning_effort": args.reasoning_effort,
            "provider_reasoning_effort": provider_reasoning_effort,
            "max_output_tokens": args.max_output_tokens,
            "requested_iterations": args.iterations,
            "completed_rounds": completed_rounds,
            "model_calls": model_calls,
            "tool_calls": tool_calls,
            "compile_calls": compile_calls,
            "csim_calls": csim_calls,
            "csynth_calls": csynth_calls,
            "automatic_model_retry": (
                args.max_model_attempts is None or args.max_model_attempts > 1
            ),
            "max_model_attempts": args.max_model_attempts,
            "provided_testbench": bool(args.testbench_path),
            "reference_path_provided": bool(args.reference_path),
            "reference_isolated": True,
            "reference_top_name": reference_top_name,
            "candidate_top_name": top_name,
            "harness_contract_version": LEGACY_HARNESS_CONTRACT_VERSION,
            "harness_contract_activated": True,
            "model_output_abstentions": model_output_abstentions,
            "model_output_reason_counts": dict(sorted(model_output_reason_counts.items())),
            "synthesis_successes": synthesis_successes,
            "harness_attempts": harness_attempts,
            "harness_passes": harness_passes,
            "harness_failure_counts": dict(sorted(harness_failure_counts.items())),
            "best_candidate_harness_validated": bool(
                best_path and os.path.isfile(best_path) and harness_passes > 0
            ),
            "wall_time_s": time.monotonic() - started_at,
            "best_candidate_path": best_path if os.path.isfile(best_path) else None,
            "raw_prompt_response_persisted": False,
            "hidden_evidence_exposed": False,
        }
        temporary = os.path.join(output_dir, ".simple_iter_evaluation.tmp")
        final = os.path.join(output_dir, "simple_iter_evaluation.json")
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump(evaluation, stream, indent=2, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, final)

    write_evaluation_summary("running")

    with open(kernel_path, 'r') as f:
        curr_code = f.read()
        
    if args.config_path:
        with open(args.config_path, 'r') as f:
            obj = json.load(f)
        top_name = obj['new_top_name']
        top_signature = obj['top_signature']
    else:
        top_signature = None
        
    if args.testbench_path:
        tb_path = os.path.abspath(args.testbench_path)
        if not os.path.isfile(tb_path):
            raise FileNotFoundError(f"provided testbench not found: {tb_path}")
        shutil.copyfile(tb_path, os.path.join(output_dir, "tb.cpp"))
        initial_prompt = f"""\
```
{curr_code}
```

Give me the optimized HLS code. The top level function name must remain: {top_name}. Preserve its exact interface. Define exactly one strong `{top_name}` function in every build. Do not define `{reference_top_name}`, do not use weak aliases, and do not hide the top function behind conditional compilation. Provide a self-contained code in one ```c++ ... ``` block.
"""
    elif args.gen_bench_prior:
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
        model_calls += 1
        write_evaluation_summary("model_call_started")
        resp = get_response(
            client,
            model_name,
            messages,
            reasoning_effort=provider_reasoning_effort,
            max_attempts=args.max_model_attempts,
            max_tokens=args.max_output_tokens,
            safe_errors=args.evaluation_mode,
        )

        # log assistant/model output
        response_content = resp.content if isinstance(resp.content, str) else ""
        log_output(output_dir, response_content, "assistant")
        messages.append({"role": "assistant", "content": response_content})
        completed_rounds += 1

        # A malformed/empty model response is a no-retry Legacy abstention for
        # this iteration, not a process crash.
        try:
            curr_code = extract_c_or_cpp_code(response_content)
        except ValueError:
            reason = (
                "empty_model_content"
                if not response_content.strip()
                else "missing_or_ambiguous_code_fence"
            )
            model_output_abstentions += 1
            model_output_reason_counts[reason] = (
                model_output_reason_counts.get(reason, 0) + 1
            )
            user_msg = (
                "The previous response was not a usable C/C++ candidate. "
                "Return exactly one fenced ```c++ ... ``` block defining only "
                f"the strong top function {top_name}."
            )
            messages.append({"role": "user", "content": user_msg})
            log_output(output_dir, f"MODEL_OUTPUT_ABSTAINED: {reason}", "tool")
            write_evaluation_summary("model_output_abstained")
            continue

        csynth_calls += 1
        tool_calls += 1
        write_evaluation_summary("csynth_started")
        csynth_status, error_msg, rpt_path, xml_path = run_csynth(
            os.path.join(output_dir, f"round_{t}"),
            top_name,
            curr_code,
            timelimit=args.csynth_timeout_s,
            target_profile=args.target,
        )
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
            synthesis_successes += 1
            round_dir = os.path.join(output_dir, f"round_{t}")
            with open(os.path.join(round_dir, 'optimized_code.cpp'), 'w') as f:
                f.write(curr_code)
            log_output(output_dir, f"Synthesizability success at round {t}", "tool")
            shutil.copy(reference_path, os.path.join(round_dir, 'orig_code.cpp'))
            shutil.copy(os.path.join(output_dir, "tb.cpp"), os.path.join(round_dir, "tb.cpp"))
            harness_attempts += 1
            harness_result = run_tb(
                round_dir,
                'orig_code.cpp',
                'optimized_code.cpp',
                'tb.cpp',
                reference_top_name=reference_top_name,
                candidate_top_name=top_name,
            )
            compile_calls += harness_result.compile_calls
            csim_calls += harness_result.csim_calls
            tool_calls += harness_result.tool_calls
            if harness_result.passed:
                harness_passes += 1
            else:
                harness_failure_counts[harness_result.reason_code] = (
                    harness_failure_counts.get(harness_result.reason_code, 0) + 1
                )
            write_evaluation_summary("candidate_tested")
            if not harness_result.passed:
                user_msg = (
                    "The candidate synthesized but failed the isolated host harness. "
                    f"reason_code={harness_result.reason_code}; "
                    f"failure_owner={harness_result.failure_owner}. "
                    f"Define only one strong {top_name} and never define "
                    f"{reference_top_name}."
                )
                if harness_result.message:
                    log_output(output_dir, harness_result.message)
                    user_msg += f" Tool output: {harness_result.message}"
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
                
    if best_design is not None:
        no_best_reason = "best candidate selected"
    elif model_calls > 0 and model_output_abstentions == model_calls:
        no_best_reason = "All model outputs abstained before candidate synthesis"
    elif harness_attempts > 0 and harness_passes == 0:
        no_best_reason = "No candidate passed the reference-isolated host harness"
    elif harness_passes > 0:
        no_best_reason = "No harness-passing design met latency/resource selection"
    elif synthesis_successes == 0:
        no_best_reason = "No model candidate completed synthesis"
    else:
        no_best_reason = "No Legacy candidate was selected"
    persist_best_design(
        output_dir,
        best_design,
        no_best_reason=no_best_reason,
    )
    write_evaluation_summary("completed")
    return 0


if __name__ == '__main__':
    import sys
    exit_code = main()
    sys.exit(exit_code)
