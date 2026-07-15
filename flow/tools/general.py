import os, sys, json, dotenv, multiprocessing, re, subprocess, signal, time, requests # type: ignore
from datetime import datetime
from typing import Optional, Dict, Any
from autogen.agentchat.group import ContextVariables # type: ignore
import flow.tools as tools
from agrefactor.evaluation import TestbenchPreflight
from agrefactor.runtime.budget import BudgetManager
from agrefactor.testing import TestbenchRepairLoop

dotenv.load_dotenv('.env', override=True)
RUN_DIR = os.getenv('RUN_DIR')
HLS_SERVER_URL = os.getenv("HLS_SERVER_URL")
CSYNTH_AND_CSIM_TIMEOUT = 600

def run_cmd(
    work_dir: str,
    cmd: str,
    timelimit: int
) -> dict:
    try:
        process = subprocess.Popen(
            cmd, cwd=work_dir, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace",
            preexec_fn=os.setsid
        )
        
        try:
            # Wait for completion with timeout
            process.wait(timeout=timelimit)
            # Get the output after completion
            stdout, stderr = process.communicate()
            return {
                "returncode": process.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "timeout": False
            }
        except subprocess.TimeoutExpired:
            print(f"Command timeout after {timelimit}s. Killing the process group...")
            # First try graceful termination
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                try:
                    process.wait(5)  # Give it 5 seconds to terminate gracefully
                except subprocess.TimeoutExpired:
                    # Force kill if still running
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                # Process group doesn't exist or already terminated
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            
            # Get whatever output we can
            try:
                stdout, stderr = process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                stdout = stderr = ""
            
            return {
                "returncode": None,
                "stdout": stdout,
                "stderr": stderr,
                "timeout": True
            }
            
    except Exception as e:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"Error starting process: {str(e)}",
            "timeout": False
        }


def strip_thinking(s: str) -> str:
    """Remove <think>...</think> blocks from model responses (e.g., Qwen3)."""
    pattern = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)
    return pattern.sub("", s).strip()


def extract_code(s: str):
    pattern = re.compile(
        r"(?:```|''')\s*(?:cpp|c\+\+|hpp|h\+\+)?\s*(.*?)(?:```|''')",
        re.DOTALL | re.IGNORECASE
    )
    matches = pattern.findall(s)
    if matches:
        return [m.strip() for m in matches]
    else:
        return [s]


def save_context(stage: str, context_variables: ContextVariables, run_dir: Optional[str] = None):
    if run_dir is None:
        run_dir = os.getcwd()
    context_file = os.path.join(run_dir, f"context_{stage}.json")
    context_dict = {}
    for key, value in context_variables.data.items():
        try:
            json.dumps(value)
            context_dict[key] = value
        except (TypeError, ValueError):
            context_dict[key] = str(value)
    with open(context_file, "w", encoding="utf-8") as f:
        json.dump(context_dict, f, indent=2, ensure_ascii=False)

def save_code(context_variables: ContextVariables, output_file: str):
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(context_variables["curr_code"])


def create_output_dir(output_dir: Optional[str] = None):
    if output_dir is None:
        output_dir = os.path.join(RUN_DIR, datetime.now().strftime("%Y%m%d"), datetime.now().strftime("%H%M%S"))
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def create_log_and_redirect(output_dir: str):
    log_path = os.path.join(output_dir, "output.txt")
    print(f"Logging to {log_path}")
    log_file = open(log_path, "w", encoding="utf-8")
    sys.stdout = log_file
    sys.stderr = log_file


def run_testbench_preflight(
    output_dir: str,
    cv: ContextVariables,
):
    timestamp = datetime.now().strftime("%H%M%S_%f")
    work_dir = os.path.join(
        output_dir,
        f"testbench_preflight_{timestamp}",
    )
    result = TestbenchPreflight().compile_and_link(
        work_dir=work_dir,
        testbench_code=cv["testbench"],
        original_code=cv["orig_code"],
        candidate_code=cv["curr_code"],
    )

    payload = result.to_dict()
    payload["gate_decision"] = (
        "continue_to_csynth"
        if result.succeeded
        else "stop_before_csynth"
    )
    evidence_path = os.path.join(
        work_dir,
        "testbench_preflight.json",
    )
    with open(evidence_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)

    payload["evidence_path"] = evidence_path
    cv["testbench_preflight"] = payload

    print(f"TESTBENCH_PREFLIGHT_STATUS:{payload['status']}")
    print(f"TESTBENCH_PREFLIGHT_KIND:{payload['failure_kind']}")
    print(f"TESTBENCH_PREFLIGHT_OWNER:{payload['failure_owner']}")
    print(f"TESTBENCH_PREFLIGHT_NEXT_ACTION:{payload['next_action']}")

    return result


def _collect_testbench_repair_usage(
    testbench_repairer,
    *,
    start_index: int = 0,
):
    responses = tuple(
        getattr(testbench_repairer, "responses", ())
    )[start_index:]
    prompt_tokens = 0
    completion_tokens = 0
    costs = []
    models = []

    for response in responses:
        usage = getattr(response, "usage", None)
        if usage is None:
            continue
        prompt_tokens += int(
            getattr(usage, "prompt_tokens", 0)
        )
        completion_tokens += int(
            getattr(usage, "completion_tokens", 0)
        )
        cost = getattr(usage, "cost_usd", None)
        if cost is not None:
            costs.append(float(cost))
        model = getattr(response, "model", None)
        if isinstance(model, str) and model:
            models.append(model)

    return {
        "calls": len(responses),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cost_usd": (
            sum(costs)
            if len(costs) == len(responses)
            else None
        ),
        "models": models,
    }


def run_testbench_validation_gate(
    output_dir: str,
    cv: ContextVariables,
    *,
    testbench_repairer=None,
    max_testbench_repair_attempts: int = 0,
):
    if (
        isinstance(max_testbench_repair_attempts, bool)
        or not isinstance(max_testbench_repair_attempts, int)
        or max_testbench_repair_attempts < 0
    ):
        raise ValueError(
            "max_testbench_repair_attempts must be a "
            "non-negative integer"
        )

    if (
        testbench_repairer is None
        or max_testbench_repair_attempts == 0
    ):
        return run_testbench_preflight(output_dir, cv)

    response_start = len(
        tuple(
            getattr(
                testbench_repairer,
                "responses",
                (),
            )
        )
    )

    timestamp = datetime.now().strftime("%H%M%S_%f")
    work_dir = os.path.join(
        output_dir,
        f"testbench_repair_{timestamp}",
    )
    result = TestbenchRepairLoop(
        preflight=TestbenchPreflight(),
        repairer=testbench_repairer,
        max_repair_attempts=max_testbench_repair_attempts,
    ).run(
        work_dir=work_dir,
        testbench_code=cv["testbench"],
        original_code=cv["orig_code"],
        candidate_code=cv["curr_code"],
    )

    payload = result.to_dict()
    payload["gate_decision"] = (
        "continue_to_csynth"
        if result.succeeded
        else "stop_before_csynth"
    )
    payload["model_usage"] = _collect_testbench_repair_usage(
        testbench_repairer,
        start_index=response_start,
    )

    with open(
        result.artifact_path,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            payload,
            file,
            indent=2,
            ensure_ascii=False,
        )

    cv["testbench_repair"] = payload

    preflight_payload = result.final_preflight.to_dict()
    preflight_payload["gate_decision"] = payload["gate_decision"]
    preflight_payload["repair_artifact_path"] = (
        result.artifact_path
    )
    cv["testbench_preflight"] = preflight_payload

    if result.succeeded:
        cv["testbench"] = result.testbench_code

    print(f"TESTBENCH_REPAIR_STATUS:{payload['status']}")
    print(
        "TESTBENCH_REPAIR_ATTEMPTS:"
        f"{payload['repair_attempts_used']}"
    )
    print(
        "TESTBENCH_REPAIR_GATE_DECISION:"
        f"{payload['gate_decision']}"
    )
    print(
        "TESTBENCH_PREFLIGHT_STATUS:"
        f"{preflight_payload['status']}"
    )
    print(
        "TESTBENCH_PREFLIGHT_OWNER:"
        f"{preflight_payload['failure_owner']}"
    )

    return result.final_preflight


def is_terminal_validation_failure(
    failed_task: Optional[str],
) -> bool:
    return failed_task == "testbench_preflight"


def csynth_and_csim(
    output_dir: str,
    cv: ContextVariables,
    first_time: bool,
    *,
    testbench_repairer=None,
    max_testbench_repair_attempts: int = 0,
    budget: BudgetManager | None = None,
):
    if cv["code_for_hetero"] != "" and first_time:
        csynth_dir_for_hetero = os.path.join(
            output_dir,
            f"csynth_for_hetero_{datetime.now().strftime('%H%M%S')}",
        )
        os.makedirs(csynth_dir_for_hetero, exist_ok=True)
        status, _ = tools.csynth.run_csynth(
            csynth_dir_for_hetero,
            cv=ContextVariables(
                data={
                    "curr_code": cv["code_for_hetero"],
                    "new_kernel_name": cv["new_kernel_name"],
                    "target_profile": cv.get("target_profile"),
                }
            ),
            budget=budget,
        )
        if status != "succeeded":
            cv["code_for_hetero"] = ""

    if cv["code_for_hetero"] != "":
        csim_dir_for_hetero = os.path.join(
            output_dir,
            f"csim_for_hetero_{datetime.now().strftime('%H%M%S')}",
        )
        os.makedirs(csim_dir_for_hetero, exist_ok=True)
        status, _ = tools.csim.run_csim(
            csim_dir_for_hetero,
            cv=ContextVariables(
                data={
                    "curr_code": cv["code_for_hetero"],
                    "orig_code": cv["orig_code"],
                    "testbench": cv["testbench"],
                }
            ),
        )
        if status == "succeeded":
            return True, None, None, None, None

    preflight_result = run_testbench_validation_gate(
        output_dir,
        cv,
        testbench_repairer=testbench_repairer,
        max_testbench_repair_attempts=(
            max_testbench_repair_attempts
        ),
    )
    if not preflight_result.succeeded:
        repair_payload = cv.get("testbench_repair")
        error_msg = preflight_result.stderr
        status = "tb_compile_failed"

        if repair_payload:
            owner = preflight_result.failure_owner.value
            repair_status = repair_payload.get("status")
            attempts_used = int(
                repair_payload.get(
                    "repair_attempts_used",
                    0,
                )
                or 0
            )

            if owner == "candidate":
                status = "candidate_compile_failed"
            elif owner == "original":
                status = "original_compile_failed"
            elif owner == "toolchain":
                status = "preflight_error"
            elif owner == "testbench" and (
                attempts_used > 0
                or repair_status in {
                    "error",
                    "exhausted",
                }
            ):
                status = "tb_repair_failed"
            else:
                status = "compile_preflight_failed"

            error_msg = repair_payload.get(
                "reason",
                error_msg,
            )
        if not error_msg and preflight_result.diagnostics:
            error_msg = preflight_result.diagnostics[0].message
        return (
            True,
            "testbench_preflight",
            (status, error_msg),
            None,
            None,
        )

    csim_dir = os.path.join(
        output_dir,
        f"csim_{datetime.now().strftime('%H%M%S')}",
    )
    os.makedirs(csim_dir, exist_ok=True)
    csynth_dir = os.path.join(
        output_dir,
        f"csynth_{datetime.now().strftime('%H%M%S')}",
    )
    os.makedirs(csynth_dir, exist_ok=True)

    csynth_res = tools.csynth.run_csynth(
        csynth_dir,
        cv,
        budget=budget,
    )
    first_task, first_res = "csynth", csynth_res

    kill_other = False
    second_task = None
    second_res = None

    if first_res[0] != "succeeded":
        kill_other = True
    else:
        csim_res = tools.csim.run_csim(csim_dir, cv)
        second_task, second_res = "csim", csim_res

    return kill_other, first_task, first_res, second_task, second_res


def csynth_and_csim_remote(cv: ContextVariables, first_time: bool):
    if not HLS_SERVER_URL:
        raise RuntimeError("HLS_SERVER_URL environment variable not set")
    tools.csynth.require_remote_default_target(cv)
    payload = {
        "orig_code": cv["orig_code"],
        "curr_code": cv["curr_code"],
        "testbench": cv["testbench"],
        "new_kernel_name": cv["new_kernel_name"],
        "code_for_hetero": cv.get("code_for_hetero", ""),
        "first_time": first_time,
    }
    resp = requests.post(f"{HLS_SERVER_URL}/csynth_and_csim", json=payload, timeout=CSYNTH_AND_CSIM_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    first_res = tuple(data["first_res"]) if data["first_res"] else None
    second_res = tuple(data["second_res"]) if data["second_res"] else None
    return data["kill_other"], data["first_task"], first_res, data["second_task"], second_res


def try_fixing(cv: ContextVariables, failed_task: str, status: str, error_msg: str, llm_config: Optional[Dict[str, Any]] = None):
    if failed_task == "csim":
        tools.csim.fixing_csim(cv, status, error_msg, llm_config=llm_config)
    else:
        tools.csynth.fixing_csynth(cv, status, error_msg, llm_config=llm_config)