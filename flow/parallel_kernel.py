# parallel kernel launcher script for HLS refactoring with RAG
# 
# This script runs multiple parallel instances of flow.new with different kernels
# from the project root directory with shared environment variables.
# Each instance uses the module import format: python -m flow.new

import os, argparse, dotenv, json, subprocess, sys, shutil, time
import multiprocessing as mp
from datetime import datetime
from typing import Dict, List, Any, Tuple
from concurrent.futures import ProcessPoolExecutor, as_completed

cur_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(cur_dir, '../src'))

dotenv.load_dotenv(os.path.join(cur_dir, '../.env'), override=True)
RUN_DIR = os.getenv('RUN_DIR')


def log_message(message: str) -> None:
    """Log message with timestamp."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {message}", flush=True)


def clean_logging_directory(directory: str) -> None:
    """Clean all contents from the logging directory."""
    if os.path.exists(directory):
        for item in os.listdir(directory):
            item_path = os.path.join(directory, item)
            try:
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                else:
                    os.remove(item_path)
            except Exception as e:
                log_message(f"Warning: Failed to remove {item_path}: {str(e)}")
        log_message(f"Cleaned logging directory: {directory}")
    else:
        log_message(f"Logging directory does not exist: {directory}")


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


def parse_retry_count_from_output_file(output_dir: str, instance_id: int) -> int:
    """
    Parse retry_count from output.txt file in the output directory.
    Uses tail command to efficiently read only the last 20 lines.
    
    Args:
        output_dir: Directory containing the output.txt file
        instance_id: Instance ID for logging purposes
        
    Returns:
        Parsed retry count, or 0 if not found/failed to parse
    """
    retry_count = 0
    output_file_path = os.path.join(output_dir, "output.txt") if output_dir else None
    if output_file_path and os.path.exists(output_file_path):
        try:
            # Use tail to get last 20 lines efficiently without loading entire file
            result = subprocess.run(['tail', '-n', '20', output_file_path], 
                                  capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    line = line.strip()
                    if line.startswith("RETRY_COUNT:"):
                        try:
                            retry_count = int(line.split(":", 1)[1])
                            break
                        except (ValueError, IndexError):
                            log_message(f"Instance {instance_id}: Failed to parse retry_count from output.txt: {line}")
            else:
                log_message(f"Instance {instance_id}: tail command failed with return code {result.returncode}")
        except subprocess.TimeoutExpired:
            log_message(f"Instance {instance_id}: tail command timed out")
        except Exception as e:
            log_message(f"Instance {instance_id}: Failed to read output.txt: {str(e)}")
    else:
        if output_dir:
            log_message(f"Instance {instance_id}: output.txt not found in {output_dir}")
    
    return retry_count


def run_kernel_instance(args_dict: Dict[str, Any], instance_id: int, project_root: str, kernel_info: Tuple[str, str, str], repeat_num: int = 0, n_llm_server: int = 1) -> Dict[str, Any]:
    """
    Run a single instance of flow.new with given arguments and specific kernel.
    Retries if exit code 134 is encountered (with directory cleanup).

    Args:
        args_dict: Base arguments for flow.new
        instance_id: Unique identifier for this instance
        project_root: Path to project root directory
        kernel_info: Tuple of (kernel_path, top_function_name, kernel_name_suffix)
        repeat_num: Repeat number for this execution (0-based)
        n_llm_server: Number of LLM servers for round-robin scheduling (default: 1)

    Returns:
        Dict containing instance_id, kernel_info, repeat_num, return_code, and execution info
    """
    kernel_path, top_function_name, kernel_name_suffix = kernel_info

    # Resolve kernel_path relative to src directory
    full_kernel_path = os.path.join(src_dir, kernel_path)

    # Round-robin scheduling for multiple LLM servers
    # Calculate port offset based on instance_id
    assigned_port = None
    if n_llm_server > 1 and args_dict.get('base_url'):
        base_url = args_dict['base_url']
        # Extract base port from URL (e.g., "http://127.0.0.1:1234/v1" -> 1234)
        import re
        match = re.search(r':(\d+)', base_url)
        if match:
            base_port = int(match.group(1))
            port_offset = instance_id % n_llm_server
            new_port = base_port + port_offset
            assigned_port = new_port
            # Replace port in URL
            args_dict = args_dict.copy()  # Don't modify the original
            args_dict['base_url'] = re.sub(r':\d+', f':{new_port}', base_url)

    # Build command line arguments - run as module from project root
    # Use conda run for environment activation
    conda_env = os.environ.get('CONDA_DEFAULT_ENV')

    # Build argument string with kernel-specific arguments
    bool_flags = {'debug', 'enable_rag', 'enable_rag_update', 'reset_knowledge_db', 'remote',
                  'enable_tb_coverage_loop', 'enable_hidden_tb_eval', 'use_cached_tb_as_public'}
    arg_parts = []
    for key, value in args_dict.items():
        if key in bool_flags and value:
            arg_parts.append(f'--{key}')
        elif key not in bool_flags and value is not None:
            arg_parts.append(f'--{key}')
            arg_parts.append(str(value))

    # Add kernel-specific arguments
    arg_parts.extend(['--kernel_path', full_kernel_path])
    arg_parts.extend(['--kernel_name', top_function_name])
    # Pass the suffix so flow.new uses it as cache_key (avoids cache collisions
    # when multiple kernels share the same function name like `process_top`).
    arg_parts.extend(['--golden_tb_cache_key', kernel_name_suffix])

    args_str = " ".join(arg_parts)

    if conda_env:
        # Use conda run command
        cmd_parts = [
            "conda", "run", "-n", conda_env,
            sys.executable, "-m", "flow.new"
        ]
        cmd = " ".join(cmd_parts) + " " + args_str
    else:
        # Fallback to direct execution if no conda environment detected
        cmd_parts = [sys.executable, "-m", "flow.new"]
        cmd = " ".join(cmd_parts) + " " + args_str

    log_message(f"Instance {instance_id}: Starting execution for kernel: {top_function_name} ({full_kernel_path}) -> {kernel_name_suffix} (repeat {repeat_num})")
    if assigned_port:
        log_message(f"Instance {instance_id}: Assigned to LLM server on port {assigned_port} (round-robin: {instance_id} % {n_llm_server} = {instance_id % n_llm_server})")
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
    
    # Retry logic for exit code 134
    max_retries_134 = 3  # Limit retries to avoid infinite loops
    retry_count_134 = 0
    all_attempts = []  # Track all attempts for result gathering
    
    while retry_count_134 <= max_retries_134:
        attempt_start = datetime.now()
        
        try:
            result = subprocess.run(
                cmd,
                cwd=project_root,  # Run from project root directory
                shell=True,  # Use shell execution (inherits environment naturally)
                capture_output=True,
                text=True,
                timeout=3600  # 60 minutes timeout per instance
            )
            
            attempt_end = datetime.now()
            attempt_time = (attempt_end - attempt_start).total_seconds()
            
            # Store this attempt info
            attempt_info = {
                "attempt": retry_count_134 + 1,
                "return_code": result.returncode,
                "execution_time_seconds": attempt_time,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "start_time": attempt_start.isoformat(),
                "end_time": attempt_end.isoformat()
            }
            all_attempts.append(attempt_info)
            
            # Check if we got exit code 134 (SIGABRT)
            if (result.returncode == 134 or result.returncode == 137) and retry_count_134 < max_retries_134:
                log_message(f"Instance {instance_id}: Got exit code 134 (attempt {retry_count_134 + 1}), cleaning directory and retrying...")
                
                # Clean the logging directory if it exists
                if output_dir:
                    clean_logging_directory(output_dir)
                
                retry_count_134 += 1
                time.sleep(1)
                continue  # Retry
            else:
                # Either not 134, or we've hit max retries - return the result
                end_time = datetime.now()
                total_execution_time = (end_time - start_time).total_seconds()
                
                # Parse retry_count from output.txt in the output directory
                retry_count_from_new = parse_retry_count_from_output_file(output_dir, instance_id)
                
                if (result.returncode == 134 or result.returncode == 137) and retry_count_134 >= max_retries_134:
                    log_message(f"Instance {instance_id}: Still getting exit code 134 after {max_retries_134 + 1} attempts, giving up")
                else:
                    log_message(f"Instance {instance_id}: Completed with exit code {result.returncode} for kernel {top_function_name} -> {kernel_name_suffix} (retry_count: {retry_count_from_new})")
                
                return {
                    "instance_id": instance_id,
                    "kernel_path": full_kernel_path,
                    "top_function_name": top_function_name,
                    "kernel_name_suffix": kernel_name_suffix,
                    "repeat_num": repeat_num,
                    "retry_count": retry_count_from_new,
                    "return_code": result.returncode,
                    "execution_time_seconds": total_execution_time,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "start_time": start_time.isoformat(),
                    "end_time": end_time.isoformat(),
                    "command": cmd
                }
                
        except subprocess.TimeoutExpired:
            attempt_end = datetime.now()
            attempt_time = (attempt_end - attempt_start).total_seconds()
            total_execution_time = (attempt_end - start_time).total_seconds()
            
            log_message(f"Instance {instance_id}: Timed out after {attempt_time:.1f}s for kernel {top_function_name} -> {kernel_name_suffix}")
            
            # Try to parse retry_count from output.txt even for timeout case
            retry_count_from_new = parse_retry_count_from_output_file(output_dir, instance_id)
            
            return {
                "instance_id": instance_id,
                "kernel_path": full_kernel_path,
                "top_function_name": top_function_name,
                "kernel_name_suffix": kernel_name_suffix,
                "repeat_num": repeat_num,
                "retry_count": retry_count_from_new,
                "return_code": -1,  # Special code for timeout
                "execution_time_seconds": total_execution_time,
                "stdout": "",
                "stderr": "Process timed out",
                "start_time": start_time.isoformat(),
                "end_time": attempt_end.isoformat(),
                "command": cmd,
                "timeout": True
            }
            
        except Exception as e:
            attempt_end = datetime.now()
            total_execution_time = (attempt_end - start_time).total_seconds()
            
            log_message(f"Instance {instance_id}: Failed with exception: {str(e)} for kernel {top_function_name} -> {kernel_name_suffix}")
            
            # Try to parse retry_count from output.txt even for exception case
            retry_count_from_new = parse_retry_count_from_output_file(output_dir, instance_id)
            
            return {
                "instance_id": instance_id,
                "kernel_path": full_kernel_path,
                "top_function_name": top_function_name,
                "kernel_name_suffix": kernel_name_suffix,
                "repeat_num": repeat_num,
                "retry_count": retry_count_from_new,
                "return_code": -2,  # Special code for exception
                "execution_time_seconds": total_execution_time,
                "stdout": "",
                "stderr": str(e),
                "start_time": start_time.isoformat(),
                "end_time": attempt_end.isoformat(),
                "command": cmd,
                "exception": True
            }


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


def main():
    parser = argparse.ArgumentParser(description="Parallel kernel launcher script for HLS refactoring with RAG")
    
    # Experiment name for directory naming
    parser.add_argument('--exp_name', type=str, required=True, 
                        help="Experiment name prefix for working directories")
    
    # Kernel specification - PLACEHOLDER for list of tuples
    # TODO: Replace this with your actual kernel list
    # Example usage: --kernels_file kernels.json or hardcode in the script
    parser.add_argument('--kernels_file', type=str, required=False, 
                        help="JSON file containing list of [kernel_path, top_function_name, kernel_name_suffix] triples")
    
    # flow.new parameters (excluding kernel_path and kernel_name which come from the kernel list)
    parser.add_argument('--debug', action='store_true', default=False, help="Enable debug output")
    parser.add_argument('--knowledge_db_path', type=str, default="./knowledge_db/tmp_db", help="Path to knowledge database")
    parser.add_argument('--embedding_model', type=str, default="all-MiniLM-L6-v2", help="Embedding model name")
    parser.add_argument('--enable_rag', action='store_true', default=False, help="Enable RAG retrieval")
    parser.add_argument('--enable_rag_update', action='store_true', default=False, help="Enable RAG update")
    parser.add_argument('--reset_knowledge_db', action='store_true', default=False, help="Reset knowledge database")
    parser.add_argument('--max_retry_attempts', type=int, default=3, help="Maximum retry attempts")
    parser.add_argument('--model', type=str, default=None, help="Override LLM model for all agents")
    parser.add_argument('--remote', action='store_true', default=False, help="Use remote HLS server for csynth/csim")
    parser.add_argument('--reasoning_effort', type=str, default=None, help="Reasoning effort (low/medium/high)")
    parser.add_argument('--base_url', type=str, default=None, help="Base URL for local serving endpoint")
    parser.add_argument('--n_llm_server', type=int, default=1, help="Number of LLM servers for round-robin load balancing (default: 1)")
    
    # Parallel execution parameters
    parser.add_argument('--repeat', type=int, default=1, help="Number of times to repeat each kernel (default: 1)")
    parser.add_argument('--max_workers', type=int, default=40, help="Max worker processes (default: 40)")
    parser.add_argument('--output_prefix', type=str, default="parallel_kernel_run", help="Output file prefix")

    # Coverage-optimized public TB loop (default off → behavior unchanged)
    parser.add_argument('--enable_tb_coverage_loop', action='store_true', default=False, help="Enable iterative coverage-optimized public TB generation in each worker")
    parser.add_argument('--public_tb_rounds', type=int, default=3, help="K: max iterative rounds for public TB (used iff --enable_tb_coverage_loop)")
    parser.add_argument('--public_tb_target', type=float, default=80.0, help="Public TB early-stop coverage target in percent")
    # Hidden TB eval gate (default off → behavior unchanged)
    parser.add_argument('--enable_hidden_tb_eval', action='store_true', default=False, help="Enable post-csim hidden TB eval against a golden coverage-optimized TB")
    parser.add_argument('--hidden_tb_rounds', type=int, default=6, help="K rounds per trajectory for golden hidden TB")
    parser.add_argument('--hidden_tb_trajectories', type=int, default=3, help="M parallel trajectories for golden hidden TB")
    parser.add_argument('--hidden_tb_target', type=float, default=90.0, help="Hidden TB early-stop coverage target in percent")
    parser.add_argument('--golden_tb_cache_dir', type=str, default=None, help="Directory for golden hidden TB JSON cache (shared across workers); REQUIRED iff --enable_hidden_tb_eval to avoid races")
    parser.add_argument('--skip_hidden_tb_pregen', action='store_true', default=False, help="Skip the pre-flow hidden-TB generation (workers will lazy-generate, race-prone)")
    parser.add_argument('--hidden_pregen_workers', type=int, default=1, help="Outer thread pool size for pre-generating golden hidden TBs across kernels (each call still spawns M inner trajectories)")
    parser.add_argument('--use_cached_tb_as_public', action='store_true', default=False,
                        help="Use the cached hidden TB (from --golden_tb_cache_dir) directly as each worker's testbench, skipping TB generation entirely. Requires --golden_tb_cache_dir.")

    args = parser.parse_args()
    
    # PLACEHOLDER: Define your list of kernels here
    # Each tuple should be (kernel_path, top_function_name, kernel_name_suffix)
    # Note: kernel_path should be relative to the src/ directory
    # You can either:
    # 1. Hardcode the list below:
    kernels_list = [
        # ("benchmarks/kernel1.c", "kernel1_function", "k1"),
        # ("benchmarks/kernel2.c", "kernel2_function", "k2"),
        # ("benchmarks/kernel3.c", "kernel3_function", "k3"),
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
                        kernels_list.append((item[0], item[1], item[2]))
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
        log_message("Note: kernel paths should be relative to the src/ directory")
        return 1
    
    # Determine project root directory (should contain 'flow' directory)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_dir)  # Go up from flow to project root
    
    # Verify we're in the right place
    if not os.path.exists(os.path.join(project_root, 'flow')):
        log_message(f"Error: Could not find 'flow' directory in project root: {project_root}")
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
    
    # Prepare base arguments for flow.new instances (without kernel_path and kernel_name)
    base_flow_args = {
        'debug': args.debug,
        'knowledge_db_path': args.knowledge_db_path,
        'embedding_model': args.embedding_model,
        'enable_rag': args.enable_rag,
        'enable_rag_update': args.enable_rag_update,
        'reset_knowledge_db': args.reset_knowledge_db,
        'max_retry_attempts': args.max_retry_attempts,
        'model': args.model,
        'remote': args.remote,
        'reasoning_effort': args.reasoning_effort,
        'base_url': args.base_url,
        'enable_tb_coverage_loop': args.enable_tb_coverage_loop,
        'public_tb_rounds': args.public_tb_rounds,
        'public_tb_target': args.public_tb_target,
        'enable_hidden_tb_eval': args.enable_hidden_tb_eval,
        'hidden_tb_rounds': args.hidden_tb_rounds,
        'hidden_tb_trajectories': args.hidden_tb_trajectories,
        'hidden_tb_target': args.hidden_tb_target,
        'golden_tb_cache_dir': args.golden_tb_cache_dir,
        'use_cached_tb_as_public': args.use_cached_tb_as_public,
    }
    
    # Determine number of workers and total jobs
    n_kernels = len(kernels_list)
    n_jobs = len(job_list)
    max_workers = args.max_workers
    
    # Log conda environment information
    conda_info = get_conda_info()
    log_message(f"Starting parallel kernel execution with {n_kernels} kernels, {args.repeat} repeats each, {n_jobs} total jobs using {max_workers} workers")
    log_message(f"Project root: {project_root}")
    log_message(f"Work directory: {output_dir}")
    log_message(f"Python executable: {conda_info['python_executable']}")
    if conda_info['conda_default_env']:
        log_message(f"Conda environment: {conda_info['conda_default_env']}")
        log_message(f"Conda prefix: {conda_info['conda_prefix']}")
        log_message(f"Conda available: {conda_info['conda_available']}")
    else:
        log_message("No conda environment detected")
    log_message(f"Base parameters: {base_flow_args}")
    log_message(f"Experiment name: {args.exp_name}")
    log_message(f"Repeat count: {args.repeat}")
    if args.n_llm_server > 1 and args.base_url:
        log_message(f"LLM Load Balancing: Round-robin across {args.n_llm_server} servers")
        import re
        match = re.search(r':(\d+)', args.base_url)
        if match:
            base_port = int(match.group(1))
            log_message(f"LLM Server ports: {base_port} to {base_port + args.n_llm_server - 1}")
    elif args.base_url:
        log_message(f"LLM Server: Single instance at {args.base_url}")
    log_message(f"Kernels to process:")
    for i, (kernel_path, top_function_name, kernel_name_suffix) in enumerate(kernels_list):
        full_kernel_path = os.path.join(src_dir, kernel_path)
        if args.repeat == 1:
            log_message(f"  {i}: {top_function_name} ({full_kernel_path}) -> {args.exp_name}/{kernel_name_suffix}/")
        else:
            log_message(f"  {i}: {top_function_name} ({full_kernel_path}) -> {args.exp_name}/{kernel_name_suffix}/[0-{args.repeat-1}]/")
    log_message(f"Isolated directories created: {len(isolated_dirs)} directories")
    log_message(f"Output will be saved to: {output_file}")
    log_message("Note: All flow.new instances will use conda run for environment activation with isolated working directories")

    # Pre-generate golden hidden TBs (once per unique kernel) before fanning out workers.
    # This avoids the cache race where multiple repeats of the same kernel each
    # try to generate the golden TB in parallel.
    if args.enable_hidden_tb_eval and not args.skip_hidden_tb_pregen:
        if not args.golden_tb_cache_dir:
            log_message("ERROR: --golden_tb_cache_dir is required when --enable_hidden_tb_eval is set (without --skip_hidden_tb_pregen)")
            return 1
        os.makedirs(args.golden_tb_cache_dir, exist_ok=True)
        # Lazy import to keep startup light when flag is off.
        from flow.tools.tb_optimizer import make_golden_hidden_tb
        from flow.new import make_llm_config as _make_llm_config
        pregen_llm_config = _make_llm_config(args.model, args.reasoning_effort, args.base_url)
        log_message(
            f"Pre-generating golden hidden TBs for {len(kernels_list)} kernels into "
            f"{args.golden_tb_cache_dir} (outer pool={args.hidden_pregen_workers})"
        )
        pregen_start = datetime.now()

        def _pregen_one(k_idx_arg, kernel_tuple):
            kernel_path_a, top_function_name_a, kernel_name_suffix_a = kernel_tuple
            kpath = os.path.join(src_dir, kernel_path_a)
            try:
                with open(kpath, "r", encoding="utf-8") as kf:
                    orig_code = kf.read()
            except Exception as e:
                return (k_idx_arg, kernel_name_suffix_a, None, f"read_failed: {e}", 0.0)
            try:
                t0 = datetime.now()
                res = make_golden_hidden_tb(
                    orig_code=orig_code,
                    kernel_name=top_function_name_a,
                    M=args.hidden_tb_trajectories,
                    K=args.hidden_tb_rounds,
                    target_pct=args.hidden_tb_target,
                    llm_config=pregen_llm_config,
                    cache_dir=args.golden_tb_cache_dir,
                    cache_key=kernel_name_suffix_a,  # ← suffix keying so 3 process_top kernels don't collide
                )
                dt = (datetime.now() - t0).total_seconds()
                return (k_idx_arg, kernel_name_suffix_a, res, None, dt)
            except Exception as e:
                return (k_idx_arg, kernel_name_suffix_a, None, f"{type(e).__name__}: {e}", 0.0)

        from concurrent.futures import ThreadPoolExecutor as _TPE
        with _TPE(max_workers=max(1, args.hidden_pregen_workers)) as kex:
            futs = [
                kex.submit(_pregen_one, i, kt)
                for i, kt in enumerate(kernels_list)
            ]
            done_n = 0
            for fut in [futs[i] for i in range(len(futs))]:  # preserve submission order for logs
                k_idx, name, res, err, dt = fut.result()
                done_n += 1
                if res is not None:
                    log_message(
                        f"  [{done_n}/{len(kernels_list)}] {name}: cov={res.get('hidden_cov'):.1f}% "
                        f"traj={res.get('best_trajectory')} round={res.get('best_round')} ({dt:.1f}s)"
                    )
                else:
                    log_message(f"  [{done_n}/{len(kernels_list)}] {name}: FAILED ({err})")
        log_message(
            f"Golden hidden TB pre-generation done in "
            f"{(datetime.now()-pregen_start).total_seconds():.1f}s"
        )

    # Run parallel instances
    start_time = datetime.now()
    results = []
    interrupted = False

    try:
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            # Submit all jobs with isolated directories and specific kernels
            future_to_instance = {}
            for i, (kernel_info, isolated_dir, repeat_num) in enumerate(job_list):
                # Add isolated directory to arguments for this instance
                instance_args = base_flow_args.copy()
                instance_args['output_dir'] = isolated_dir

                future = executor.submit(
                    run_kernel_instance,
                    instance_args,
                    i,
                    project_root,
                    kernel_info,
                    repeat_num,
                    args.n_llm_server
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
    except KeyboardInterrupt:
        log_message("")
        log_message("=" * 60)
        log_message("KEYBOARD INTERRUPT DETECTED (Ctrl+C)")
        log_message("=" * 60)
        log_message("Shutting down all parallel processes...")
        interrupted = True

        # Cancel all pending futures
        cancelled_count = 0
        for future in future_to_instance:
            if not future.done():
                future.cancel()
                cancelled_count += 1

        log_message(f"Cancelled {cancelled_count} pending jobs")
        log_message(f"Collected {len(results)} completed jobs before interruption")

        # The executor context manager will handle cleanup
        # Wait a moment for processes to terminate
        time.sleep(2)
    
    end_time = datetime.now()
    total_execution_time = (end_time - start_time).total_seconds()

    # Sort results by instance_id for consistent output
    results.sort(key=lambda x: x["instance_id"])

    # Calculate kernel metrics (only from completed results)
    metrics = calculate_kernel_metrics(results) if results else {
        "total_runs": 0,
        "successful_runs": 0,
        "overall_success_rate": 0.0,
        "unique_kernels": 0,
        "kernel_metrics": {}
    }
    
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
            "n_completed": len(results),
            "max_workers": max_workers,
            "n_llm_server": args.n_llm_server,
            "work_directory": output_dir,
            "work_dir_name": work_dir_name,
            "isolated_directories": isolated_dirs,
            "kernels_file": args.kernels_file,
            "interrupted": interrupted
        },
        "parameters": base_flow_args,
        "exp_name": args.exp_name,
        "kernels": [{"kernel_path": os.path.join(src_dir, kp), "top_function_name": tfn, "kernel_name_suffix": kns} for kp, tfn, kns in kernels_list],
        "results": results,
        "metrics": metrics,
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
    if interrupted:
        log_message("PARALLEL KERNEL EXECUTION SUMMARY (INTERRUPTED)")
    else:
        log_message("PARALLEL KERNEL EXECUTION SUMMARY")
    log_message("=" * 60)
    if interrupted:
        log_message(f"Status: INTERRUPTED BY USER (Ctrl+C)")
        log_message(f"Total jobs planned: {n_jobs}")
        log_message(f"Jobs completed before interruption: {len(results)}")
        log_message(f"Jobs cancelled: {n_jobs - len(results)}")
        log_message("")
    log_message(f"Completed jobs: {metrics['total_runs']}")
    log_message(f"Successful jobs: {metrics['successful_runs']}")
    if metrics['total_runs'] > 0:
        log_message(f"Success rate (of completed): {metrics['overall_success_rate']:.2%}")
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
    
    log_message("Return code distribution:")
    for code, count in output_data["summary"]["return_code_distribution"].items():
        code_meaning = {
            0: "Success",
            1: "Failed after max retries",
            134: "SIGABRT (exit code 134)",
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
    if interrupted:
        log_message("")
        log_message("Exiting due to keyboard interrupt")
        return 130  # Standard exit code for SIGINT (128 + 2)
    elif metrics['successful_runs'] > 0:
        return 0  # At least one success
    else:
        return 1  # No successes


if __name__ == '__main__':
    import sys
    exit_code = main()
    sys.exit(exit_code)
