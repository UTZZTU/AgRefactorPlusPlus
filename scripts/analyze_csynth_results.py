#!/usr/bin/env python3
"""
Script to analyze csynth results from parallel kernel runs.

This script:
1. Reads parallel_kernel_run JSON files from two directories
2. Counts statuses from csynth_csim_history across all runs
3. Reruns csynth for successful designs and parses performance/resource data
"""

import os
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Any, Optional
from collections import defaultdict
import sys
import argparse
import shutil
import dotenv  # type: ignore

# Add parent directory to path to import flow modules
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, base_dir)

from autogen.agentchat.group import ContextVariables
import flow.tools as tools

# Load environment variables from project root
dotenv.load_dotenv(os.path.join(base_dir, '.env'), override=True)
WORK_DIR = os.getenv('WORK_DIR')  # base dir holding run outputs to analyze


def find_parallel_kernel_run_json(base_dir: str) -> Optional[str]:
    """Find the parallel_kernel_run JSON file in the directory."""
    base_path = Path(base_dir)
    if not base_path.exists():
        return None
    
    json_files = list(base_path.glob("parallel_kernel_run*.json"))
    if not json_files:
        return None
    
    # Return the most recent one if multiple exist
    return str(max(json_files, key=lambda p: p.stat().st_mtime))


def load_context_final(context_path: str) -> Optional[Dict[str, Any]]:
    """Load context_final.json file."""
    if not os.path.exists(context_path):
        return None
    
    try:
        with open(context_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading {context_path}: {e}")
        return None


def count_statuses_from_history(
    results: List[Dict[str, Any]],
    base_dir: str
) -> Dict[str, Any]:
    """Summarize statuses from csynth_csim_history across all retries.
    
    Instead of only looking at the last entry, this aggregates every entry in
    the history and reports, per status, how often that status appeared in at
    least one retry for a given run. The return value contains both counts and
    percentages relative to the number of runs that had history data.
    """
    status_counts = defaultdict(int)
    runs_with_history = 0
    
    for result in results:
        kernel_name_suffix = result.get("kernel_name_suffix")
        repeat_num = result.get("repeat_num")
        
        if kernel_name_suffix is None or repeat_num is None:
            continue
        
        context_path = os.path.join(
            base_dir, kernel_name_suffix, str(repeat_num), "context_final.json"
        )
        
        context_data = load_context_final(context_path)
        if context_data is None:
            continue
        
        csynth_csim_history = context_data.get("csynth_csim_history", [])
        if not isinstance(csynth_csim_history, list) or len(csynth_csim_history) == 0:
            continue
        
        runs_with_history += 1
        statuses_seen = set()
        
        for entry in csynth_csim_history:
            if not isinstance(entry, dict):
                continue
            status = entry.get("status", "")
            if status in ["succeeded", "succeeded by hetero"]:
                statuses_seen.add("succeed")
            elif status == "csim_failed":
                statuses_seen.add("csim_failed")
            elif status == "tb_compile_failed":
                statuses_seen.add("tb_compile_failed")
            elif status == "csynth_failed":
                statuses_seen.add("csynth_failed")
        
        for status in statuses_seen:
            status_counts[status] += 1
    
    status_percentages = {}
    if runs_with_history > 0:
        for status, count in status_counts.items():
            status_percentages[status] = (count / runs_with_history) * 100.0
    
    return {
        "total_runs": runs_with_history,
        "counts": dict(status_counts),
        "percentages": status_percentages
    }


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


def rerun_csynth_for_successful(
    context_data: Dict[str, Any],
    work_dir: str,
    top_kernel_name: str
) -> Optional[str]:
    """Rerun csynth for a successful design and return the XML report path."""
    # Get the refactored code from the last successful entry
    csynth_csim_history = context_data.get("csynth_csim_history", [])
    
    # Find the last successful entry
    last_success_code = None
    for entry in reversed(csynth_csim_history):
        if isinstance(entry, dict):
            status = entry.get("status", "")
            if status in ["succeeded", "succeeded by hetero"]:
                last_success_code = entry.get("refactored_code")
                if last_success_code:
                    break
    
    # If no successful code found, use curr_code from context
    if not last_success_code:
        return None
    
    # Create ContextVariables for csynth
    cv = ContextVariables(data={
        "curr_code": last_success_code,
        "new_kernel_name": top_kernel_name
    })
    
    # Run csynth
    try:
        status, error_msg = tools.csynth.run_csynth(work_dir, cv)
        
        if status == "succeeded":
            # Find the XML report
            xml_path = os.path.join(
                work_dir, "csynth", "solution", "syn", "report",
                f"{top_kernel_name}_csynth.xml"
            )
            if os.path.exists(xml_path):
                return xml_path
    except Exception as e:
        print(f"Error running csynth in {work_dir}: {e}")
    
    return None


def calculate_max_resource_percentage(resource_percentages: Dict[str, Dict[str, Any]]) -> float:
    """Calculate maximum resource utilization percentage across all resource types.
    
    This represents the bottleneck resource.
    """
    if not resource_percentages:
        return 0.0
    
    max_percentage = 0.0
    for res_name, res_data in resource_percentages.items():
        if isinstance(res_data, dict) and "percentage" in res_data:
            percentage = res_data["percentage"]
            if percentage > max_percentage:
                max_percentage = percentage
    
    return max_percentage


def sanitize_directory_name(name: str) -> str:
    """Sanitize directory name by removing spaces and special characters.
    
    Replaces spaces with underscores and removes characters that might cause issues.
    """
    # Replace spaces with underscores
    sanitized = name.replace(" ", "_")
    # Remove brackets and other special characters that might cause issues
    sanitized = sanitized.replace("[", "").replace("]", "")
    sanitized = sanitized.replace("(", "").replace(")", "")
    return sanitized


def clean_summaries_and_reruns(runs_dir: str, dir1: str, dir2: str) -> int:
    """Clean all summary files and csynth_rerun directories.
    
    Args:
        runs_dir: Path to the runs directory (contains summary/)
        dir1: First directory to search for csynth_rerun folders (not used, kept for API compatibility)
        dir2: Second directory to search for csynth_rerun folders (not used, kept for API compatibility)
    
    Returns:
        0 on success, 1 on error
    """
    print("=== Cleaning summaries and csynth_rerun directories ===\n")
    
    # Clean summary directory (in original runs_dir)
    summary_dir = os.path.join(runs_dir, "summary")
    if os.path.exists(summary_dir):
        print(f"Removing summary directory: {summary_dir}")
        try:
            shutil.rmtree(summary_dir)
            print(f"  ✓ Removed summary directory\n")
        except Exception as e:
            print(f"  ✗ Error removing summary directory: {e}\n")
            return 1
    else:
        print(f"Summary directory does not exist: {summary_dir}\n")
    
    # Clean csynth_rerun directories in WORK_DIR/runs
    removed_count = 0
    error_count = 0
    
    if not WORK_DIR:
        print("Warning: WORK_DIR not set in .env file, skipping csynth_rerun cleanup")
    else:
        work_runs_dir = os.path.join(WORK_DIR, "runs")
        if os.path.exists(work_runs_dir):
            print(f"Searching for csynth_rerun directories in: {work_runs_dir}")
            work_runs_path = Path(work_runs_dir)
            
            # Find all csynth_rerun directories
            csynth_rerun_dirs = list(work_runs_path.rglob("csynth_rerun"))
            
            for rerun_dir in csynth_rerun_dirs:
                rerun_path = str(rerun_dir)
                try:
                    print(f"  Removing: {rerun_path}")
                    shutil.rmtree(rerun_path)
                    removed_count += 1
                except Exception as e:
                    print(f"  ✗ Error removing {rerun_path}: {e}")
                    error_count += 1
            
            if csynth_rerun_dirs:
                print(f"  ✓ Removed {len(csynth_rerun_dirs)} csynth_rerun directories from {work_runs_dir}\n")
            else:
                print(f"  No csynth_rerun directories found in {work_runs_dir}\n")
        else:
            print(f"WORK_DIR/runs directory does not exist: {work_runs_dir}\n")
    
    print(f"\n=== Cleanup Summary ===")
    print(f"  Removed {removed_count} csynth_rerun directories")
    if error_count > 0:
        print(f"  Errors: {error_count}")
        return 1
    
    print("  ✓ Cleanup completed successfully")
    return 0


def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="Analyze csynth results from parallel kernel runs"
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean all summary files and csynth_rerun directories, then exit"
    )
    parser.add_argument(
        "--run-part1",
        dest="run_part1",
        action="store_true",
        help="Run Part 1 (status summary). Enabled by default."
    )
    parser.add_argument(
        "--skip-part1",
        dest="run_part1",
        action="store_false",
        help="Skip Part 1 (status summary)."
    )
    parser.add_argument(
        "--run-part2",
        dest="run_part2",
        action="store_true",
        help="Run Part 2 (csynth rerun and performance/resource parsing). Disabled by default."
    )
    parser.add_argument(
        "--skip-part2",
        dest="run_part2",
        action="store_false",
        help="Skip Part 2 (csynth rerun and performance/resource parsing)."
    )
    parser.set_defaults(run_part1=True, run_part2=False)
    args = parser.parse_args()
    
    # Define directories
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    runs_dir = os.path.join(base_dir, "runs")
    
    dir1 = os.path.join(runs_dir, "[paper] gpt5_mini_test_with_hybrid_rag_new")
    dir2 = os.path.join(runs_dir, "[paper] gpt5_mini_test_with_hybrid_rag_new_2")
    
    # If clean flag is set, clean and exit
    if args.clean:
        return clean_summaries_and_reruns(runs_dir, dir1, dir2)
    
    # Find JSON files
    json1_path = find_parallel_kernel_run_json(dir1)
    json2_path = find_parallel_kernel_run_json(dir2)
    
    if not json1_path or not json2_path:
        print(f"Error: Could not find parallel_kernel_run JSON files")
        print(f"  Dir1: {json1_path}")
        print(f"  Dir2: {json2_path}")
        return 1
    
    # Load JSON files
    print("Loading JSON files...")
    with open(json1_path, 'r', encoding='utf-8') as f:
        data1 = json.load(f)
    with open(json2_path, 'r', encoding='utf-8') as f:
        data2 = json.load(f)
    
    results1 = data1.get("results", [])
    results2 = data2.get("results", [])
    
    print(f"Found {len(results1)} results from dir1, {len(results2)} results from dir2")
    
    # Create summary directory (used by both parts)
    summary_dir = os.path.join(runs_dir, "summary")
    os.makedirs(summary_dir, exist_ok=True)
    
    # Part 1: Count statuses
    if args.run_part1:
        print("\n=== Part 1: Counting statuses ===")
        status_stats1 = count_statuses_from_history(results1, dir1)
        status_stats2 = count_statuses_from_history(results2, dir2)
        
        status_counts1 = status_stats1["counts"]
        status_counts2 = status_stats2["counts"]
        
        # Merge counts
        total_status_counts = defaultdict(int)
        for status, count in status_counts1.items():
            total_status_counts[status] += count
        for status, count in status_counts2.items():
            total_status_counts[status] += count
        
        runs_with_history = status_stats1["total_runs"] + status_stats2["total_runs"]
        total_status_percentages = {}
        if runs_with_history > 0:
            for status, count in total_status_counts.items():
                total_status_percentages[status] = (count / runs_with_history) * 100.0
        
        # Write status summary
        status_summary = {
            "total_results": len(results1) + len(results2),
            "runs_with_history": runs_with_history,
            "status_counts": dict(total_status_counts),
            "status_percentages": total_status_percentages,
            "breakdown": {
                "dir1": status_stats1,
                "dir2": status_stats2
            }
        }
        
        status_summary_path = os.path.join(summary_dir, "status_summary.json")
        with open(status_summary_path, 'w', encoding='utf-8') as f:
            json.dump(status_summary, f, indent=2)
        
        # Write human-readable summary
        status_readable_path = os.path.join(summary_dir, "status_summary.txt")
        with open(status_readable_path, 'w', encoding='utf-8') as f:
            f.write("=== Status Summary ===\n\n")
            f.write(f"Total results discovered: {status_summary['total_results']}\n")
            f.write(f"Runs with csynth history: {status_summary['runs_with_history']}\n\n")
            f.write("Status counts (merged from both directories):\n")
            for status, count in sorted(total_status_counts.items()):
                pct = total_status_percentages.get(status, 0.0)
                f.write(f"  {status}: {count} ({pct:.2f}%)\n")
            f.write("\nBreakdown by directory:\n")
            f.write(f"  Dir1 ({dir1}) [runs analyzed: {status_stats1['total_runs']}]:\n")
            for status, count in sorted(status_counts1.items()):
                pct = status_stats1["percentages"].get(status, 0.0)
                f.write(f"    {status}: {count} ({pct:.2f}%)\n")
            f.write(f"  Dir2 ({dir2}) [runs analyzed: {status_stats2['total_runs']}]:\n")
            for status, count in sorted(status_counts2.items()):
                pct = status_stats2["percentages"].get(status, 0.0)
                f.write(f"    {status}: {count} ({pct:.2f}%)\n")
        
        print(f"Status summary written to {status_readable_path}")
    else:
        print("\n=== Part 1: Skipped (disabled by flag) ===")
    
    # Part 2: Rerun csynth for successful designs and parse results
    if not args.run_part2:
        print("\n=== Part 2: Skipped (disabled by flag) ===")
        return 0
    
    print("\n=== Part 2: Rerunning csynth and parsing performance/resource data ===")
    
    all_results = results1 + results2
    all_dirs = [dir1] * len(results1) + [dir2] * len(results2)
    
    # Group by kernel_name_suffix
    kernel_data = defaultdict(list)
    
    # Determine which directory ID for each result
    dir_ids = ["dir1"] * len(results1) + ["dir2"] * len(results2)
    
    for result, base_dir_path, dir_id in zip(all_results, all_dirs, dir_ids):
        kernel_name_suffix = result.get("kernel_name_suffix")
        repeat_num = result.get("repeat_num")
        
        if kernel_name_suffix is None or repeat_num is None:
            continue
        
        context_path = os.path.join(
            base_dir_path, kernel_name_suffix, str(repeat_num), "context_final.json"
        )
        
        context_data = load_context_final(context_path)
        if context_data is None:
            continue
        
        # Check if there's a successful entry
        csynth_csim_history = context_data.get("csynth_csim_history", [])
        has_success = False
        for entry in csynth_csim_history:
            if isinstance(entry, dict):
                status = entry.get("status", "")
                if status in ["succeeded", "succeeded by hetero"]:
                    has_success = True
                    break
        
        if has_success:
            kernel_data[kernel_name_suffix].append({
                "repeat_num": repeat_num,
                "base_dir": base_dir_path,
                "dir_id": dir_id,
                "context_data": context_data,
                "context_path": context_path
            })
    
    print(f"Found {len(kernel_data)} kernels with successful designs")
    
    # Process each kernel
    performance_results = {}
    
    for kernel_name_suffix, instances in kernel_data.items():
        print(f"\nProcessing kernel: {kernel_name_suffix} ({len(instances)} instances)")
        
        kernel_results = []
        
        for instance in instances:
            repeat_num = instance["repeat_num"]
            base_dir_path = instance["base_dir"]
            dir_id = instance["dir_id"]
            context_data = instance["context_data"]
            
            # Get top kernel name
            top_kernel_name = context_data.get("new_kernel_name", "")
            if not top_kernel_name:
                continue
            
            # Create a work directory for rerunning csynth in WORK_DIR
            # Sanitize directory name to remove spaces and special characters
            sanitized_kernel_name = sanitize_directory_name(kernel_name_suffix)
            
            if not WORK_DIR:
                print(f"  Error: WORK_DIR not set in .env file")
                continue
            
            # Use WORK_DIR/runs/[dir_id]/[sanitized_kernel_name]/[repeat_num]/csynth_rerun
            # This avoids collisions when both dir1 and dir2 have the same kernel_name_suffix and repeat_num
            work_dir = os.path.join(
                WORK_DIR, "runs", dir_id, sanitized_kernel_name, str(repeat_num), "csynth_rerun"
            )
            os.makedirs(work_dir, exist_ok=True)
            
            print(f"  Rerunning csynth for {dir_id}/{kernel_name_suffix}/{repeat_num}...")
            xml_path = rerun_csynth_for_successful(
                context_data, work_dir, top_kernel_name
            )
            
            if xml_path and os.path.exists(xml_path):
                parsed_data = parse_xml_report(xml_path)
                if parsed_data:
                    instance_result = {
                        "repeat_num": repeat_num,
                        "base_dir": base_dir_path,
                        "work_dir": work_dir,
                        "xml_path": xml_path,
                        "performance": parsed_data.get("performance", {}),
                        "resources": parsed_data.get("resources", {}),
                        "available_resources": parsed_data.get("available_resources", {}),
                        "resource_percentages": parsed_data.get("resource_percentages", {}),
                        "max_resource_percentage": calculate_max_resource_percentage(parsed_data.get("resource_percentages", {}))
                    }
                    kernel_results.append(instance_result)
                    
                    # Print detailed results for this design
                    print(f"    ✓ Successfully parsed XML report")
                    
                    perf = parsed_data.get("performance", {})
                    resources = parsed_data.get("resources", {})
                    resource_pct = parsed_data.get("resource_percentages", {})
                    
                    # Print performance metrics
                    print(f"      Performance:")
                    if "clock_period" in perf:
                        cp = perf["clock_period"]
                        print(f"        Clock Period: {cp.get('value')} {cp.get('unit', 'ns')}")
                    if "worst_case_latency" in perf:
                        wl = perf["worst_case_latency"]
                        print(f"        Worst-case Latency: {wl.get('value')} {wl.get('unit', 'cycles')}")
                    if "worst_case_realtime_latency" in perf:
                        wrl = perf["worst_case_realtime_latency"]
                        print(f"        Worst-case Real-time Latency: {wrl.get('value')} {wrl.get('unit', 'us')}")
                    
                    # Print resource utilization
                    print(f"      Resources:")
                    for res_name in ["BRAM_18K", "DSP", "FF", "LUT", "URAM"]:
                        if res_name in resource_pct:
                            pct_info = resource_pct[res_name]
                            print(f"        {res_name}: {pct_info['used']} / {pct_info['available']} ({pct_info['percentage']:.2f}%)")
                        elif res_name in resources:
                            print(f"        {res_name}: {resources[res_name]}")
                    
                    max_pct = instance_result.get("max_resource_percentage", 0.0)
                    print(f"        Max Resource %: {max_pct:.2f}%")
                    print()
                else:
                    print(f"    ✗ Failed to parse XML report")
            else:
                print(f"    ✗ Failed to generate XML report")
        
        if kernel_results:
            performance_results[kernel_name_suffix] = kernel_results
    
    # Find best designs for each kernel
    print("\n=== Finding best designs ===")
    
    best_designs = {}
    
    for kernel_name_suffix, instances in performance_results.items():
        if not instances:
            continue
        
        # Find design with minimum realtime latency
        min_realtime_latency = None
        min_realtime_design = None
        
        for instance in instances:
            perf = instance.get("performance", {})
            realtime = perf.get("worst_case_realtime_latency", {})
            if realtime and "value" in realtime:
                value = realtime["value"]
                if min_realtime_latency is None or value < min_realtime_latency:
                    min_realtime_latency = value
                    min_realtime_design = instance
        
        # Find design with smallest resource utilization (minimum max percentage)
        min_resource_percentage = None
        min_resource_design = None
        
        for instance in instances:
            max_pct = instance.get("max_resource_percentage", 0.0)
            if min_resource_percentage is None or max_pct < min_resource_percentage:
                min_resource_percentage = max_pct
                min_resource_design = instance
        
        best_designs[kernel_name_suffix] = {
            "all_instances": instances,
            "min_realtime_latency": {
                "design": min_realtime_design,
                "latency": min_realtime_latency
            },
            "min_resource_utilization": {
                "design": min_resource_design,
                "max_resource_percentage": min_resource_percentage
            }
        }
    
    # Write performance results
    performance_json_path = os.path.join(summary_dir, "performance_results.json")
    with open(performance_json_path, 'w', encoding='utf-8') as f:
        json.dump(performance_results, f, indent=2)
    
    # Write best designs summary
    best_designs_json_path = os.path.join(summary_dir, "best_designs.json")
    with open(best_designs_json_path, 'w', encoding='utf-8') as f:
        # Create a simplified version for JSON (remove full design objects)
        simplified_best = {}
        for kernel, data in best_designs.items():
            simplified_best[kernel] = {
                "num_instances": len(data["all_instances"]),
                "min_realtime_latency": {
                    "repeat_num": data["min_realtime_latency"]["design"]["repeat_num"] if data["min_realtime_latency"]["design"] else None,
                    "latency": data["min_realtime_latency"]["latency"],
                    "performance": data["min_realtime_latency"]["design"].get("performance", {}) if data["min_realtime_latency"]["design"] else {},
                    "resources": data["min_realtime_latency"]["design"].get("resources", {}) if data["min_realtime_latency"]["design"] else {}
                },
                "min_resource_utilization": {
                    "repeat_num": data["min_resource_utilization"]["design"]["repeat_num"] if data["min_resource_utilization"]["design"] else None,
                    "max_resource_percentage": data["min_resource_utilization"]["max_resource_percentage"],
                    "performance": data["min_resource_utilization"]["design"].get("performance", {}) if data["min_resource_utilization"]["design"] else {},
                    "resources": data["min_resource_utilization"]["design"].get("resources", {}) if data["min_resource_utilization"]["design"] else {}
                }
            }
        json.dump(simplified_best, f, indent=2)
    
    # Write human-readable performance summary
    performance_readable_path = os.path.join(summary_dir, "performance_summary.txt")
    with open(performance_readable_path, 'w', encoding='utf-8') as f:
        f.write("=== Performance and Resource Summary ===\n\n")
        
        for kernel_name_suffix, data in best_designs.items():
            f.write(f"Kernel: {kernel_name_suffix}\n")
            f.write(f"  Total instances: {len(data['all_instances'])}\n\n")
            
            # Best realtime latency design
            if data["min_realtime_latency"]["design"]:
                design = data["min_realtime_latency"]["design"]
                f.write(f"  Best Realtime Latency Design (repeat_num={design['repeat_num']}):\n")
                perf = design.get("performance", {})
                if "worst_case_latency" in perf:
                    f.write(f"    Worst-case Latency: {perf['worst_case_latency'].get('value')} {perf['worst_case_latency'].get('unit')}\n")
                if "worst_case_realtime_latency" in perf:
                    f.write(f"    Worst-case Realtime Latency: {perf['worst_case_realtime_latency'].get('value')} {perf['worst_case_realtime_latency'].get('unit')}\n")
                if "clock_period" in perf:
                    f.write(f"    Clock Period: {perf['clock_period'].get('value')} {perf['clock_period'].get('unit')}\n")
                f.write(f"    Resources:\n")
                for res_name, res_value in design.get("resources", {}).items():
                    available = design.get("available_resources", {}).get(res_name, 0)
                    percentage = design.get("resource_percentages", {}).get(res_name, {}).get("percentage", 0)
                    f.write(f"      {res_name}: {res_value} / {available} ({percentage:.2f}%)\n")
                f.write("\n")
            
            # Best resource utilization design
            if data["min_resource_utilization"]["design"]:
                design = data["min_resource_utilization"]["design"]
                f.write(f"  Best Resource Utilization Design (repeat_num={design['repeat_num']}):\n")
                f.write(f"    Max Resource %: {design.get('max_resource_percentage', 0.0):.2f}%\n")
                perf = design.get("performance", {})
                if "worst_case_latency" in perf:
                    f.write(f"    Worst-case Latency: {perf['worst_case_latency'].get('value')} {perf['worst_case_latency'].get('unit')}\n")
                if "worst_case_realtime_latency" in perf:
                    f.write(f"    Worst-case Realtime Latency: {perf['worst_case_realtime_latency'].get('value')} {perf['worst_case_realtime_latency'].get('unit')}\n")
                f.write(f"    Resources:\n")
                for res_name, res_value in design.get("resources", {}).items():
                    available = design.get("available_resources", {}).get(res_name, 0)
                    percentage = design.get("resource_percentages", {}).get(res_name, {}).get("percentage", 0)
                    f.write(f"      {res_name}: {res_value} / {available} ({percentage:.2f}%)\n")
                f.write("\n")
            
            f.write("\n")
    
    print(f"\nPerformance summary written to {performance_readable_path}")
    print(f"All results written to {summary_dir}/")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
