# parallel evaluation script for opt.simple_iter.main
# 
# This script runs multiple parallel instances of opt.simple_iter.main with different kernels.
# Kernels are provided via a JSON file containing [kernel_path, top_function_name, kernel_name_suffix] triples.

import os, argparse, dotenv, json, subprocess, sys, shutil, time
from datetime import datetime
from typing import Dict, List, Any, Tuple, Optional
from concurrent.futures import ProcessPoolExecutor, as_completed

cur_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(cur_dir, '../../src'))

dotenv.load_dotenv(os.path.join(cur_dir, '../../.env'), override=True)
RUN_DIR = os.getenv('RUN_DIR')
BEST_DESIGN_FILENAME = "best_design.json"


def log_message(message: str) -> None:
    """Log message with timestamp."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {message}", flush=True)


def get_conda_info() -> Dict[str, str]:
    """Get conda environment information."""
    info = {}
    info['conda_default_env'] = os.environ.get('CONDA_DEFAULT_ENV', '')
    info['conda_prefix'] = os.environ.get('CONDA_PREFIX', '')
    info['python_executable'] = sys.executable
    
    # Check if conda is available
    try:
        result = subprocess.run(['conda', '--version'], capture_output=True, text=True, timeout=5)
        info['conda_available'] = result.returncode == 0
        info['conda_version'] = result.stdout.strip() if result.returncode == 0 else ''
    except:
        info['conda_available'] = False
        info['conda_version'] = ''
    
    return info


def read_best_design_file(output_dir: Optional[str]) -> Optional[Dict[str, Any]]:
    """Read the best_design.json emitted by opt.simple_iter.main, if present."""
    if not output_dir:
        return None
    path = os.path.join(output_dir, BEST_DESIGN_FILENAME)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log_message(f"Warning: failed to read best design from {path}: {e}")
        return None


def average_resource_utilization(designs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute average resource utilization from a list of best-design dictionaries."""
    totals: Dict[str, Dict[str, float]] = {}
    counts: Dict[str, Dict[str, int]] = {}
    for design in designs:
        for res_name, util in design.get("resources", {}).items():
            fraction = util.get("fraction")
            percentage = util.get("percentage")
            totals.setdefault(res_name, {"fraction": 0.0, "percentage": 0.0})
            counts.setdefault(res_name, {"fraction": 0, "percentage": 0})
            if fraction is not None:
                totals[res_name]["fraction"] += fraction
                counts[res_name]["fraction"] += 1
            if percentage is not None:
                totals[res_name]["percentage"] += percentage
                counts[res_name]["percentage"] += 1
    averages: Dict[str, Any] = {}
    for res_name in totals:
        frac_count = counts[res_name]["fraction"]
        pct_count = counts[res_name]["percentage"]
        avg_fraction = totals[res_name]["fraction"] / frac_count if frac_count else None
        avg_percentage = totals[res_name]["percentage"] / pct_count if pct_count else None
        averages[res_name] = {
            "average_fraction": avg_fraction,
            "average_percentage": avg_percentage,
            "samples": max(frac_count, pct_count)
        }
    return averages


def summarize_best_designs(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Summarize best design information per kernel:
    - average best latency across repeats
    - best of best (lowest latency)
    - worst of best (highest latency among qualifying best designs)
    """
    per_kernel: Dict[str, Dict[str, Any]] = {}
    for result in results:
        kernel_suffix = result["kernel_name_suffix"]
        per_kernel.setdefault(kernel_suffix, {"designs": []})
        best_design = result.get("best_design")
        if not isinstance(best_design, dict):
            continue
        if not best_design.get("found"):
            continue
        if not best_design.get("all_resource_within_limit", False):
            continue
        latency = best_design.get("worst_realtime_latency")
        if latency is None:
            continue
        per_kernel[kernel_suffix]["designs"].append({
            "latency": latency,
            "latency_unit": best_design.get("latency_unit"),
            "resources": best_design.get("resource_utilization", {}),
            "repeat_num": result.get("repeat_num"),
            "instance_id": result.get("instance_id"),
            "output_dir": result.get("output_dir"),
            "round_dir": best_design.get("round_dir"),
            "iteration": best_design.get("iteration"),
        })

    summary: Dict[str, Any] = {}
    for kernel_suffix, data in per_kernel.items():
        designs = data["designs"]
        if not designs:
            summary[kernel_suffix] = {"count": 0}
            continue
        latencies = [d["latency"] for d in designs if d.get("latency") is not None]
        avg_latency = sum(latencies) / len(latencies) if latencies else None
        best_design = min(designs, key=lambda d: d["latency"]) if latencies else None
        worst_design = max(designs, key=lambda d: d["latency"]) if latencies else None
        summary[kernel_suffix] = {
            "count": len(latencies),
            "average_best_latency": avg_latency,
            "latency_unit": best_design.get("latency_unit") if best_design else None,
            "average_resource_utilization": average_resource_utilization(designs),
            "best_of_best": best_design,
            "worst_of_best": worst_design,
        }
    return summary


def calculate_kernel_metrics(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calculate metrics from kernel execution results.
    """
    total_runs = len(results)
    successful_runs = sum(1 for r in results if r["return_code"] == 0)
    
    # Group results by kernel_name_suffix
    kernel_results = {}
    for result in results:
        kernel_suffix = result["kernel_name_suffix"]
        if kernel_suffix not in kernel_results:
            kernel_results[kernel_suffix] = []
        kernel_results[kernel_suffix].append(result)
    
    # Calculate per-kernel metrics
    kernel_metrics = {}
    for kernel_suffix, kernel_res in kernel_results.items():
        successful = sum(1 for r in kernel_res if r["return_code"] == 0)
        successful_kernel_runs = [r for r in kernel_res if r["return_code"] == 0]
        successful_retry_count = sum(r.get("retry_count", 0) for r in successful_kernel_runs)
        kernel_metrics[kernel_suffix] = {
            "total_attempts": len(kernel_res),
            "successful_attempts": successful,
            "success_rate": successful / len(kernel_res) if kernel_res else 0.0,
            "average_execution_time": sum(r.get("execution_time_seconds", 0) for r in kernel_res) / len(kernel_res) if kernel_res else 0,
            "average_retry_count": successful_retry_count / successful if successful > 0 else 0
        }
    
    return {
        "total_runs": total_runs,
        "successful_runs": successful_runs,
        "overall_success_rate": successful_runs / total_runs if total_runs > 0 else 0.0,
        "unique_kernels": len(kernel_results),
        "kernel_metrics": kernel_metrics
    }


def run_kernel_instance(args_dict: Dict[str, Any], instance_id: int, project_root: str, kernel_info: Tuple[str, str, str], repeat_num: int = 0) -> Dict[str, Any]:
    """
    Run a single instance of opt.simple_iter.main with given arguments and specific kernel.
    
    Args:
        args_dict: Base arguments for opt.simple_iter.main
        instance_id: Unique identifier for this instance
        project_root: Path to project root directory
        kernel_info: Tuple of (kernel_path, top_function_name, kernel_name_suffix)
        repeat_num: Repeat number for this execution (0-based)
    
    Returns:
        Dict containing instance_id, kernel_info, repeat_num, return_code, and execution info
    """
    kernel_path, top_function_name, kernel_name_suffix = kernel_info
    
    # Build command line arguments - run as module from project root
    conda_env = os.environ.get('CONDA_DEFAULT_ENV')
    
    # Build argument string with kernel-specific arguments
    arg_parts = []
    for key, value in args_dict.items():
        if key in ['gen_bench_prior', 'add_raw_rpt']:
            if value is True:
                arg_parts.append(f'--{key}')
            elif value is False:
                arg_parts.append(f'--no-{key}')
        elif value is not None:
            arg_parts.append(f'--{key}')
            arg_parts.append(str(value))
    
    # Add kernel-specific arguments
    arg_parts.extend(['--kernel_path', kernel_path])
    arg_parts.extend(['--top_name', top_function_name])
    
    args_str = " ".join(arg_parts)
    
    if conda_env:
        # Use conda run command
        cmd_parts = [
            "conda", "run", "-n", conda_env,
            sys.executable, "-m", "opt.simple_iter.main"
        ]
        cmd = " ".join(cmd_parts) + " " + args_str
    else:
        # Fallback to direct execution if no conda environment detected
        cmd_parts = [sys.executable, "-m", "opt.simple_iter.main"]
        cmd = " ".join(cmd_parts) + " " + args_str
    
    log_message(f"Instance {instance_id}: Starting execution for kernel: {top_function_name} ({kernel_path}) -> {kernel_name_suffix} (repeat {repeat_num})")
    if conda_env:
        log_message(f"Instance {instance_id}: Using conda environment: {conda_env} (conda run)")
    else:
        log_message(f"Instance {instance_id}: No conda environment detected (direct execution)")
    
    # Log isolated directory if provided
    output_dir = args_dict.get('output_dir')
    if output_dir:
        log_message(f"Instance {instance_id}: Using isolated working directory: {output_dir}")
    
    log_message(f"Instance {instance_id}: Command: {cmd}")
    start_time = datetime.now()
    
    try:
        result = subprocess.run(
            cmd,
            cwd=project_root,  # Run from project root directory
            shell=True,  # Use shell execution (inherits environment naturally)
            capture_output=True,
            text=True,
            timeout=3600  # 60 minutes timeout per instance
        )
        
        end_time = datetime.now()
        execution_time = (end_time - start_time).total_seconds()
        
        best_design = read_best_design_file(output_dir)
        
        log_message(f"Instance {instance_id}: Completed with exit code {result.returncode} for kernel {top_function_name} -> {kernel_name_suffix}")
        
        return {
            "instance_id": instance_id,
            "kernel_path": kernel_path,
            "top_function_name": top_function_name,
            "kernel_name_suffix": kernel_name_suffix,
            "repeat_num": repeat_num,
            "return_code": result.returncode,
            "execution_time_seconds": execution_time,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "command": cmd,
            "output_dir": output_dir,
            "best_design": best_design,
        }
        
    except subprocess.TimeoutExpired:
        end_time = datetime.now()
        execution_time = (end_time - start_time).total_seconds()
        
        log_message(f"Instance {instance_id}: Timed out after {execution_time:.1f}s for kernel {top_function_name} -> {kernel_name_suffix}")
        
        return {
            "instance_id": instance_id,
            "kernel_path": kernel_path,
            "top_function_name": top_function_name,
            "kernel_name_suffix": kernel_name_suffix,
            "repeat_num": repeat_num,
            "return_code": -1,  # Special code for timeout
            "execution_time_seconds": execution_time,
            "stdout": "",
            "stderr": "Process timed out",
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "command": cmd,
            "output_dir": output_dir,
            "timeout": True
        }
        
    except Exception as e:
        end_time = datetime.now()
        execution_time = (end_time - start_time).total_seconds()
        
        log_message(f"Instance {instance_id}: Failed with exception: {str(e)} for kernel {top_function_name} -> {kernel_name_suffix}")
        
        return {
            "instance_id": instance_id,
            "kernel_path": kernel_path,
            "top_function_name": top_function_name,
            "kernel_name_suffix": kernel_name_suffix,
            "repeat_num": repeat_num,
            "return_code": -2,  # Special code for exception
            "execution_time_seconds": execution_time,
            "stdout": "",
            "stderr": str(e),
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "command": cmd,
            "output_dir": output_dir,
            "exception": True
        }


def main():
    parser = argparse.ArgumentParser(description="Parallel kernel evaluation script for opt.simple_iter.main")
    
    # Experiment name for directory naming
    parser.add_argument('--exp_name', type=str, required=True, 
                        help="Experiment name prefix for working directories")
    
    # Kernel specification
    parser.add_argument('--kernels_file', type=str, required=False, 
                        help="JSON file containing list of [kernel_path, top_function_name, kernel_name_suffix] triples")
    
    # opt.simple_iter.main parameters (excluding kernel_path and top_name which come from the kernel list)
    parser.add_argument('--config_path', type=str, default=None, help="Config file path")
    parser.add_argument('--gen_bench_prior', action=argparse.BooleanOptionalAction, default=True, help="Generate benchmark prior (default: True)")
    parser.add_argument('--model', type=str, default='gpt-5.1', help="Model to use")
    parser.add_argument('--iterations', type=int, default=4, help="Number of iterations per instance")
    parser.add_argument('--add_raw_rpt', action=argparse.BooleanOptionalAction, default=True, help="Include raw report in feedback (default: True)")
    parser.add_argument('--reasoning_effort', type=str, default='high', help="Reasoning effort for the model")
    
    # Parallel execution parameters
    parser.add_argument('--repeat', type=int, default=1, help="Number of times to repeat each kernel (default: 1)")
    parser.add_argument('--max_workers', type=int, default=40, help="Max worker processes (default: 40)")
    parser.add_argument('--output_prefix', type=str, default="parallel_opt_simple_iter", help="Output file prefix")
    
    args = parser.parse_args()
    
    # PLACEHOLDER: Define your list of kernels here
    # Each tuple should be (kernel_path, top_function_name, kernel_name_suffix)
    kernels_list: List[Tuple[str, str, str]] = [
        # ("benchmarks/kernel1.c", "kernel1_function", "k1"),
        # ("benchmarks/kernel2.c", "kernel2_function", "k2"),
        # Add your kernel tuples here...
    ]
    
    # 2. Or load from a JSON file if provided:
    if args.kernels_file:
        if not os.path.exists(args.kernels_file):
            log_message(f"Error: Kernels file not found: {args.kernels_file}")
            return 1
        
        try:
            with open(args.kernels_file, 'r') as f:
                loaded_kernels = json.load(f)
                
            # Convert to list of tuples - only support list of 3-element lists
            if isinstance(loaded_kernels, list):
                kernels_list = []
                for item in loaded_kernels:
                    if isinstance(item, list) and len(item) == 3:
                        kernels_list.append((os.path.join(src_dir, item[0]), item[1], item[2]))
                    else:
                        log_message(f"Error: Invalid kernel format in file: {item}")
                        log_message("Expected format: [kernel_path, top_function_name, kernel_name_suffix]")
                        return 1
                        
        except json.JSONDecodeError as e:
            log_message(f"Error: Invalid JSON in kernels file: {e}")
            return 1
        except Exception as e:
            log_message(f"Error: Failed to load kernels file: {e}")
            return 1
    
    # Validate that we have kernels to run
    if not kernels_list:
        log_message("Error: No kernels specified. Please either:")
        log_message("1. Hardcode kernels in the kernels_list variable in the script")
        log_message("2. Provide a --kernels_file with kernel specifications")
        log_message("")
        log_message("Kernel file format (JSON):")
        log_message('  [["benchmarks/kernel1.c", "kernel1_function", "k1"], ["benchmarks/kernel2.c", "kernel2_function", "k2"]]')
        return 1
    
    # Determine project root directory (should contain 'opt' directory)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(current_dir))  # Go up from opt/simple_iter to project root
    
    # Verify we're in the right place
    if not os.path.exists(os.path.join(project_root, 'opt')):
        log_message(f"Error: Could not find 'opt' directory in project root: {project_root}")
        log_message("Please run this script from the correct project directory.")
        return 1
    
    # Create output directory and file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Use exp_name for work directory
    work_dir_name = args.exp_name
    log_message(f"Using experiment-based work directory: {work_dir_name}")
    output_dir = os.path.join(RUN_DIR, work_dir_name)
    try:
        shutil.rmtree(output_dir)
    except:
        pass
    os.makedirs(output_dir, exist_ok=True)
    
    output_file = os.path.join(output_dir, f"{args.output_prefix}_{timestamp}.json")
    
    # Create isolated working directories for each kernel run within the experiment directory
    isolated_dirs = []
    job_list = []  # List of (kernel_info, isolated_dir, repeat_num) tuples
    
    for kernel_idx, (kernel_path, top_function_name, kernel_name_suffix) in enumerate(kernels_list):
        for repeat_num in range(args.repeat):
            if args.repeat == 1:
                # Single run: use kernel_name_suffix directly
                isolated_dir = os.path.join(output_dir, kernel_name_suffix)
            else:
                # Multiple runs: add repeat number subdirectory
                isolated_dir = os.path.join(output_dir, kernel_name_suffix, str(repeat_num))
            
            os.makedirs(isolated_dir, exist_ok=True)
            isolated_dirs.append(isolated_dir)
            job_list.append(((kernel_path, top_function_name, kernel_name_suffix), isolated_dir, repeat_num))
    
    # Prepare base arguments for opt.simple_iter.main instances (without kernel_path and top_name)
    base_main_args = {
        'config_path': args.config_path,
        'gen_bench_prior': args.gen_bench_prior,
        'model': args.model,
        'iterations': args.iterations,
        'add_raw_rpt': args.add_raw_rpt,
        'reasoning_effort': args.reasoning_effort,
    }
    
    # Determine number of workers and total jobs
    n_kernels = len(kernels_list)
    n_jobs = len(job_list)
    max_workers = args.max_workers
    
    # Log conda environment information
    conda_info = get_conda_info()
    log_message(f"Starting parallel kernel evaluation with {n_kernels} kernels, {args.repeat} repeats each, {n_jobs} total jobs using {max_workers} workers")
    log_message(f"Project root: {project_root}")
    log_message(f"Work directory: {output_dir}")
    log_message(f"Python executable: {conda_info['python_executable']}")
    if conda_info['conda_default_env']:
        log_message(f"Conda environment: {conda_info['conda_default_env']}")
        log_message(f"Conda prefix: {conda_info['conda_prefix']}")
        log_message(f"Conda available: {conda_info['conda_available']}")
    else:
        log_message("No conda environment detected")
    log_message(f"Base parameters: {base_main_args}")
    log_message(f"Experiment name: {args.exp_name}")
    log_message(f"Repeat count: {args.repeat}")
    log_message(f"Kernels to process:")
    for i, (kernel_path, top_function_name, kernel_name_suffix) in enumerate(kernels_list):
        if args.repeat == 1:
            log_message(f"  {i}: {top_function_name} ({kernel_path}) -> {args.exp_name}/{kernel_name_suffix}/")
        else:
            log_message(f"  {i}: {top_function_name} ({kernel_path}) -> {args.exp_name}/{kernel_name_suffix}/[0-{args.repeat-1}]/")
    log_message(f"Isolated directories created: {len(isolated_dirs)} directories")
    log_message(f"Output will be saved to: {output_file}")
    log_message("Note: All opt.simple_iter.main instances will use conda run for environment activation with isolated working directories")
    
    # Run parallel instances
    start_time = datetime.now()
    results = []
    
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        # Submit all jobs with isolated directories and specific kernels
        future_to_instance = {}
        for i, (kernel_info, isolated_dir, repeat_num) in enumerate(job_list):
            # Add isolated directory to arguments for this instance
            instance_args = base_main_args.copy()
            instance_args['output_dir'] = isolated_dir
            
            future = executor.submit(
                run_kernel_instance, 
                instance_args, 
                i, 
                project_root,
                kernel_info,
                repeat_num
            )
            future_to_instance[future] = i
            time.sleep(1)
        
        # Collect results as they complete
        for future in as_completed(future_to_instance):
            result = future.result()
            results.append(result)
            
            # Log progress
            completed = len(results)
            log_message(f"Progress: {completed}/{n_jobs} completed")
    
    end_time = datetime.now()
    total_execution_time = (end_time - start_time).total_seconds()
    
    # Sort results by instance_id for consistent output
    results.sort(key=lambda x: x["instance_id"])
    
    # Calculate kernel metrics
    metrics = calculate_kernel_metrics(results)
    best_design_summary = summarize_best_designs(results)
    
    # Prepare final output
    output_data = {
        "experiment_info": {
            "timestamp": timestamp,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "total_execution_time_seconds": total_execution_time,
            "n_kernels": n_kernels,
            "repeat_count": args.repeat,
            "n_jobs": n_jobs,
            "max_workers": max_workers,
            "work_directory": output_dir,
            "work_dir_name": work_dir_name,
            "isolated_directories": isolated_dirs,
            "kernels_file": args.kernels_file
        },
        "parameters": base_main_args,
        "exp_name": args.exp_name,
        "kernels": [{"kernel_path": kp, "top_function_name": tfn, "kernel_name_suffix": kns} for kp, tfn, kns in kernels_list],
        "results": results,
        "metrics": metrics,
        "best_design_summary": best_design_summary,
        "summary": {
            "return_code_distribution": {},
            "average_execution_time": sum(r.get("execution_time_seconds", 0) for r in results) / len(results) if results else 0,
            "average_retry_count": sum(r.get("retry_count", 0) for r in results if r["return_code"] == 0) / sum(1 for r in results if r["return_code"] == 0) if any(r["return_code"] == 0 for r in results) else 0
        }
    }
    
    # Calculate return code distribution
    for result in results:
        code = result["return_code"]
        output_data["summary"]["return_code_distribution"][code] = output_data["summary"]["return_code_distribution"].get(code, 0) + 1
    
    # Save results to JSON
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
    
    # Print summary
    log_message("=" * 60)
    log_message("PARALLEL KERNEL EVALUATION SUMMARY")
    log_message("=" * 60)
    log_message(f"Total jobs: {metrics['total_runs']}")
    log_message(f"Successful jobs: {metrics['successful_runs']}")
    log_message(f"Overall success rate: {metrics['overall_success_rate']:.2%}")
    log_message(f"Unique kernels processed: {metrics['unique_kernels']}")
    log_message(f"Repeat count per kernel: {args.repeat}")
    log_message("")
    
    # Per-kernel statistics
    log_message("Per-kernel results:")
    for kernel_suffix, kernel_stats in metrics['kernel_metrics'].items():
        success_rate = kernel_stats['success_rate']
        avg_time = kernel_stats['average_execution_time']
        avg_retry = kernel_stats['average_retry_count']
        log_message(f"  {args.exp_name}/{kernel_suffix}: {success_rate:.2%} success rate, {avg_time:.1f}s avg time, {avg_retry:.1f} avg retry count (successful only)")
    log_message("")

    log_message("Best-design statistics (resource-util < 0.8 and min realtime latency):")
    for kernel_suffix, kernel_stats in best_design_summary.items():
        count = kernel_stats.get("count", 0)
        if count == 0:
            log_message(f"  {args.exp_name}/{kernel_suffix}: no qualifying best designs captured")
            continue
        avg_latency = kernel_stats.get("average_best_latency")
        unit = kernel_stats.get("latency_unit") or ""
        log_message(f"  {args.exp_name}/{kernel_suffix}: {count}/{args.repeat} runs have qualifying best designs; avg best latency: {avg_latency:.4f} {unit}" if avg_latency is not None else f"  {args.exp_name}/{kernel_suffix}: {count}/{args.repeat} runs have qualifying best designs; avg best latency: n/a")
        best_best = kernel_stats.get("best_of_best")
        worst_best = kernel_stats.get("worst_of_best")
        if best_best:
            log_message(f"    Best of best: repeat {best_best.get('repeat_num')} latency {best_best.get('latency')} {unit}")
        if worst_best:
            log_message(f"    Worst of best: repeat {worst_best.get('repeat_num')} latency {worst_best.get('latency')} {unit}")
    log_message("")
    
    log_message("Return code distribution:")
    for code, count in output_data["summary"]["return_code_distribution"].items():
        code_meaning = {
            0: "Success",
            1: "TB compilation failed", 
            2: "Code incorrect",
            3: "Synthesis failed",
            -1: "Timeout",
            -2: "Exception"
        }.get(code, f"Unknown ({code})")
        log_message(f"  {code} ({code_meaning}): {count}")
    log_message("")
    
    # Log overall retry statistics
    overall_avg_retry = output_data["summary"]["average_retry_count"]
    log_message(f"Overall average retry count (successful runs only): {overall_avg_retry:.2f}")
    log_message("")
    
    log_message(f"Total execution time: {total_execution_time:.1f} seconds")
    log_message(f"Average time per job: {output_data['summary']['average_execution_time']:.1f} seconds")
    log_message(f"Work directory: {output_dir}")
    log_message(f"Isolated directories: {len(isolated_dirs)} directories created")
    log_message(f"Results saved to: {output_file}")
    
    # Exit with appropriate code
    if metrics['successful_runs'] > 0:
        return 0  # At least one success
    else:
        return 1  # No successes


if __name__ == '__main__':
    import sys
    exit_code = main()
    sys.exit(exit_code)
