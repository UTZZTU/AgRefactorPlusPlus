# parallel evaluation script for HLS refactoring with RAG
# 
# This script runs multiple parallel instances of flow.new
# from the project root directory with shared environment variables.
# Each instance uses the module import format: python -m flow.new

import os, argparse, dotenv, json, subprocess, sys, shutil, time
import multiprocessing as mp
from datetime import datetime
from typing import Dict, List, Any
from concurrent.futures import ProcessPoolExecutor, as_completed

cur_dir = os.path.dirname(os.path.abspath(__file__))

dotenv.load_dotenv(os.path.join(cur_dir, '../.env'), override=True)
RUN_DIR = os.getenv('RUN_DIR')


def log_message(message: str) -> None:
    """Log message with timestamp."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {message}")


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


def run_new_instance(args_dict: Dict[str, Any], instance_id: int, project_root: str) -> Dict[str, Any]:
    """
    Run a single instance of flow.new with given arguments.
    Retries if exit code 134 is encountered (with directory cleanup).
    
    Returns:
        Dict containing instance_id, return_code, and execution info
    """
    # Build command line arguments - run as module from project root
    # Use conda run for environment activation
    conda_env = os.environ.get('CONDA_DEFAULT_ENV')
    
    # Build argument string
    arg_parts = []
    for key, value in args_dict.items():
        if key in ['debug', 'enable_rag', 'enable_rag_update', 'reset_knowledge_db'] and value:
            arg_parts.append(f'--{key}')
        elif key not in ['debug', 'enable_rag', 'enable_rag_update', 'reset_knowledge_db'] and value is not None:
            arg_parts.append(f'--{key}')
            arg_parts.append(str(value))
    
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
    
    log_message(f"Instance {instance_id}: Starting execution")
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
                
                if (result.returncode == 134 or result.returncode == 137) and retry_count_134 >= max_retries_134:
                    log_message(f"Instance {instance_id}: Still getting exit code 134 after {max_retries_134 + 1} attempts, giving up")
                else:
                    log_message(f"Instance {instance_id}: Completed with exit code {result.returncode}")
                
                return {
                    "instance_id": instance_id,
                    "return_code": result.returncode,
                    "execution_time_seconds": total_execution_time,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "start_time": start_time.isoformat(),
                    "end_time": end_time.isoformat(),
                    "command": cmd,
                    "retry_attempts_134": retry_count_134,
                    "all_attempts": all_attempts
                }
                
        except subprocess.TimeoutExpired:
            attempt_end = datetime.now()
            attempt_time = (attempt_end - attempt_start).total_seconds()
            total_execution_time = (attempt_end - start_time).total_seconds()
            
            log_message(f"Instance {instance_id}: Timed out after {attempt_time:.1f}s")
            
            return {
                "instance_id": instance_id,
                "return_code": -1,  # Special code for timeout
                "execution_time_seconds": total_execution_time,
                "stdout": "",
                "stderr": "Process timed out",
                "start_time": start_time.isoformat(),
                "end_time": attempt_end.isoformat(),
                "command": cmd,
                "timeout": True,
                "retry_attempts_134": retry_count_134,
                "all_attempts": all_attempts
            }
            
        except Exception as e:
            attempt_end = datetime.now()
            total_execution_time = (attempt_end - start_time).total_seconds()
            
            log_message(f"Instance {instance_id}: Failed with exception: {str(e)}")
            
            return {
                "instance_id": instance_id,
                "return_code": -2,  # Special code for exception
                "execution_time_seconds": total_execution_time,
                "stdout": "",
                "stderr": str(e),
                "start_time": start_time.isoformat(),
                "end_time": attempt_end.isoformat(),
                "command": cmd,
                "exception": True,
                "retry_attempts_134": retry_count_134,
                "all_attempts": all_attempts
            }


def calculate_pass_at_k(results: List[Dict[str, Any]], k: int) -> Dict[str, Any]:
    """
    Calculate pass@K metric from results.
    
    Pass@K means: given K attempts, what's the probability of at least one success?
    Success is defined as return_code == 0.
    """
    total_runs = len(results)
    successful_runs = sum(1 for r in results if r["return_code"] == 0)
    
    # Calculate pass@K for the specified k value
    if k <= total_runs:
        # For pass@k, we calculate the probability of at least one success in k attempts
        # This is 1 - (probability of all k attempts failing)
        failure_rate = (total_runs - successful_runs) / total_runs
        pass_at_k_val = 1 - (failure_rate ** k)
    else:
        # If k > total_runs, we can't calculate pass@k meaningfully
        pass_at_k_val = None
    
    return {
        "total_runs": total_runs,
        "successful_runs": successful_runs,
        "success_rate": successful_runs / total_runs if total_runs > 0 else 0.0,
        "pass_at_k": pass_at_k_val,
        "k": k
    }


def main():
    parser = argparse.ArgumentParser(description="Parallel evaluation script for HLS refactoring with RAG")
    
    # flow.new parameters
    parser.add_argument('--kernel_path', type=str, required=True, help="Path to the kernel source code file")
    parser.add_argument('--kernel_name', type=str, required=True, help="Name of the kernel function")
    parser.add_argument('--debug', action='store_true', default=False, help="Enable debug output")
    parser.add_argument('--knowledge_db_path', type=str, default="./knowledge_db/tmp_db", help="Path to knowledge database")
    parser.add_argument('--embedding_model', type=str, default="all-MiniLM-L6-v2", help="Embedding model name")
    parser.add_argument('--enable_rag', action='store_true', default=False, help="Enable RAG retrieval")
    parser.add_argument('--enable_rag_update', action='store_true', default=False, help="Enable RAG update")
    parser.add_argument('--reset_knowledge_db', action='store_true', default=False, help="Reset knowledge database")
    parser.add_argument('--max_retry_attempts', type=int, default=3, help="Maximum retry attempts")
    
    # Parallel execution parameters
    parser.add_argument('--n_parallel', type=int, default=10, help="Number of parallel runs")
    parser.add_argument('--pass_k', type=int, default=10, help="K value for pass@K metric calculation")
    parser.add_argument('--max_workers', type=int, default=None, help="Max worker processes (default: CPU count)")
    parser.add_argument('--output_prefix', type=str, default="parallel_flow_eval", help="Output file prefix")
    parser.add_argument('--work_dir', type=str, default=None, 
                        help="Custom work directory name (default: YYYYMMDD timestamp)")
    
    args = parser.parse_args()
    
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
    
    # Use custom work_dir if specified, otherwise use date-based directory
    if args.work_dir:
        work_dir_name = args.work_dir
        log_message(f"Using custom work directory: {work_dir_name}")
    else:
        work_dir_name = datetime.now().strftime("%Y%m%d")
        log_message(f"Using default date-based work directory: {work_dir_name}")
    
    output_dir = os.path.join(RUN_DIR, work_dir_name)
    os.makedirs(output_dir, exist_ok=True)
    
    output_file = os.path.join(output_dir, f"{args.output_prefix}_{timestamp}.json")
    
    # Create isolated working directories for each parallel run
    isolated_dirs = []
    for i in range(args.n_parallel):
        isolated_dir = os.path.join(output_dir, f"run_{i:03d}")
        os.makedirs(isolated_dir, exist_ok=True)
        isolated_dirs.append(isolated_dir)
    
    # Prepare base arguments for flow.new instances (without output_dir, will be added per instance)
    base_flow_args = {
        'kernel_path': args.kernel_path,
        'kernel_name': args.kernel_name,
        'debug': args.debug,
        'knowledge_db_path': args.knowledge_db_path,
        'embedding_model': args.embedding_model,
        'enable_rag': args.enable_rag,
        'enable_rag_update': args.enable_rag_update,
        'reset_knowledge_db': args.reset_knowledge_db,
        'max_retry_attempts': args.max_retry_attempts
    }
    
    # Determine number of workers
    max_workers = args.max_workers or min(args.n_parallel, mp.cpu_count())
    
    # Log conda environment information
    conda_info = get_conda_info()
    log_message(f"Starting parallel HLS refactoring evaluation with {args.n_parallel} runs using {max_workers} workers")
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
    log_message(f"Isolated directories created: {len(isolated_dirs)} directories")
    log_message(f"Output will be saved to: {output_file}")
    log_message("Note: All flow.new instances will use conda run for environment activation with isolated working directories")
    
    # Run parallel instances
    start_time = datetime.now()
    results = []
    
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        # Submit all jobs with isolated directories
        future_to_instance = {}
        for i in range(args.n_parallel):
            # Add isolated directory to arguments for this instance
            instance_args = base_flow_args.copy()
            instance_args['output_dir'] = isolated_dirs[i]
            future = executor.submit(run_new_instance, instance_args, i, project_root)
            future_to_instance[future] = i
            time.sleep(1)
        
        # Collect results as they complete
        for future in as_completed(future_to_instance):
            result = future.result()
            results.append(result)
            
            # Log progress
            completed = len(results)
            log_message(f"Progress: {completed}/{args.n_parallel} completed")
    
    end_time = datetime.now()
    total_execution_time = (end_time - start_time).total_seconds()
    
    # Sort results by instance_id for consistent output
    results.sort(key=lambda x: x["instance_id"])
    
    # Calculate pass@K metrics
    metrics = calculate_pass_at_k(results, args.pass_k)
    
    # Prepare final output
    output_data = {
        "experiment_info": {
            "timestamp": timestamp,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "total_execution_time_seconds": total_execution_time,
            "n_parallel": args.n_parallel,
            "max_workers": max_workers,
            "work_directory": output_dir,
            "work_dir_name": work_dir_name,
            "custom_work_dir": args.work_dir is not None,
            "isolated_directories": isolated_dirs
        },
        "parameters": base_flow_args,
        "results": results,
        "metrics": metrics,
        "summary": {
            "return_code_distribution": {},
            "average_execution_time": sum(r.get("execution_time_seconds", 0) for r in results) / len(results) if results else 0,
            "retry_134_stats": {
                "instances_with_retries": sum(1 for r in results if r.get("retry_attempts_134", 0) > 0),
                "total_retry_attempts": sum(r.get("retry_attempts_134", 0) for r in results),
                "max_retries_per_instance": max((r.get("retry_attempts_134", 0) for r in results), default=0)
            }
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
    log_message("HLS REFACTORING PARALLEL EVALUATION SUMMARY")
    log_message("=" * 60)
    log_message(f"Total runs: {metrics['total_runs']}")
    log_message(f"Successful runs: {metrics['successful_runs']}")
    log_message(f"Success rate: {metrics['success_rate']:.2%}")
    log_message("")
    if metrics['pass_at_k'] is not None:
        log_message(f"Pass@{metrics['k']}: {metrics['pass_at_k']:.2%}")
    else:
        log_message(f"Pass@{metrics['k']}: Cannot calculate (k > total_runs)")
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
    
    # Log retry statistics
    retry_stats = output_data["summary"]["retry_134_stats"]
    if retry_stats["instances_with_retries"] > 0:
        log_message("Exit code 134 retry statistics:")
        log_message(f"  Instances that required retries: {retry_stats['instances_with_retries']}")
        log_message(f"  Total retry attempts: {retry_stats['total_retry_attempts']}")
        log_message(f"  Max retries per instance: {retry_stats['max_retries_per_instance']}")
        log_message("")
    
    log_message(f"Total execution time: {total_execution_time:.1f} seconds")
    log_message(f"Average time per instance: {output_data['summary']['average_execution_time']:.1f} seconds")
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
