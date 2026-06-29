import os, dotenv, asyncio, tempfile, shutil, argparse, base64
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional
from contextlib import asynccontextmanager
import uvicorn

@asynccontextmanager
async def lifespan(app: FastAPI):
    global worker_semaphore
    worker_semaphore = asyncio.Semaphore(MAX_WORKERS)
    os.makedirs(WORK_ROOT, exist_ok=True)
    yield

app = FastAPI(lifespan=lifespan)

dotenv.load_dotenv('.env', override=True)
WORK_ROOT = os.getenv("HLS_WORK_ROOT") or os.path.join(tempfile.gettempdir(), "agrefactor_remote_hls_work")
MAX_WORKERS = int(os.getenv("HLS_MAX_WORKERS", "4"))
worker_semaphore: asyncio.Semaphore = None

CSIM_TIMEOUT = 60
CSYNTH_TIMEOUT = 300
ERROR_LINES = 15
CSIM_COMPILE_CMD = "g++ -I$XILINX_HLS/include -O2 -Wno-unknown-pragmas testbench.cpp orig_code.cpp refactor_code.cpp -o csim"
CSIM_CMD = "./csim"
CSYNTH_CMD = "vitis_hls -f vitis.tcl"


class CsimRequest(BaseModel):
    orig_code: str
    curr_code: str
    testbench: str
    timelimit: int = CSIM_TIMEOUT


class CsynthRequest(BaseModel):
    curr_code: str
    new_kernel_name: str
    timelimit: int = CSYNTH_TIMEOUT


class CsynthFolderRequest(BaseModel):
    files: dict[str, str]  # filename -> content
    tcl_script: str
    top_function: str
    timelimit: int = CSYNTH_TIMEOUT


class CsimFolderRequest(BaseModel):
    files: dict[str, str]  # filename -> content (text files)
    binary_files: dict[str, str] = {}  # filename -> content (base64 encoded binary files)
    compile_command: str  # g++ compile command with relative paths
    timelimit: int = CSIM_TIMEOUT


class CsynthAndCsimRequest(BaseModel):
    orig_code: str
    curr_code: str
    testbench: str
    new_kernel_name: str
    code_for_hetero: str = ""
    first_time: bool = True


class CsimResponse(BaseModel):
    status: str
    error_msg: str


class CsynthResponse(BaseModel):
    status: str
    error_msg: str


class CsynthFolderResponse(BaseModel):
    status: str
    error_msg: str
    report: str = ""


class CsimFolderResponse(BaseModel):
    status: str
    error_msg: str
    stdout: str = ""


class CsynthAndCsimResponse(BaseModel):
    kill_other: bool
    first_task: Optional[str]
    first_res: Optional[tuple[str, str]]
    second_task: Optional[str]
    second_res: Optional[tuple[str, str]]


def run_cmd_sync(work_dir: str, cmd: str, timelimit: int) -> dict:
    import subprocess, signal
    try:
        process = subprocess.Popen(
            cmd, cwd=work_dir, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace",
            preexec_fn=os.setsid
        )
        try:
            process.wait(timeout=timelimit)
            stdout, stderr = process.communicate()
            return {"returncode": process.returncode, "stdout": stdout, "stderr": stderr, "timeout": False}
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                try:
                    process.wait(5)
                except subprocess.TimeoutExpired:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            try:
                stdout, stderr = process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                stdout = stderr = ""
            return {"returncode": None, "stdout": stdout, "stderr": stderr, "timeout": True}
    except Exception as e:
        return {"returncode": -1, "stdout": "", "stderr": f"Error starting process: {str(e)}", "timeout": False}


def make_csim_files(work_dir: str, orig_code: str, curr_code: str, testbench: str):
    with open(os.path.join(work_dir, "orig_code.cpp"), "w", encoding="utf-8") as f:
        f.write(orig_code)
    with open(os.path.join(work_dir, "refactor_code.cpp"), "w", encoding="utf-8") as f:
        f.write(curr_code)
    with open(os.path.join(work_dir, "testbench.cpp"), "w", encoding="utf-8") as f:
        f.write(testbench)


def make_vitis_tcl(top_kernel: str, file_list: list[str]) -> str:
    tcl_lines = [
        'open_project csynth',
        f'set_top {top_kernel}',
    ]
    for fname in file_list:
        tcl_lines.append(f'add_files "{fname}" -cflags " -D XILINX "')
    tcl_lines += [
        'open_solution -flow_target vitis solution',
        'set_part xcu200-fsgd2104-2-e',
        'create_clock -period 200MHz -name default',
        'csynth_design',
        'close_project',
        'exit'
    ]
    return '\n'.join(tcl_lines)


def make_csynth_files(work_dir: str, curr_code: str, new_kernel_name: str):
    file_path = os.path.join(work_dir, f"{new_kernel_name}.cpp")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(curr_code)
    tcl_content = make_vitis_tcl(new_kernel_name, [f"{new_kernel_name}.cpp"])
    with open(os.path.join(work_dir, "vitis.tcl"), "w", encoding="utf-8") as f:
        f.write(tcl_content)


def get_error_msg(synth_dir: str) -> str:
    path = os.path.join(synth_dir, "csynth", "solution", "solution.log")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            log_content = f.read()
            return "\n".join(log_content.strip().splitlines()[-ERROR_LINES:])
    return ""


def do_csim(work_dir: str, orig_code: str, curr_code: str, testbench: str, timelimit: int) -> tuple[str, str]:
    make_csim_files(work_dir, orig_code, curr_code, testbench)
    compile_res = run_cmd_sync(work_dir, CSIM_COMPILE_CMD, timelimit)
    if compile_res["returncode"] != 0:
        return "tb_compile_failed", compile_res["stderr"]
    run_res = run_cmd_sync(work_dir, CSIM_CMD, timelimit)
    if run_res["returncode"] != 0:
        return "csim_failed", run_res["stderr"]
    return "succeeded", ""


def do_csynth(work_dir: str, curr_code: str, new_kernel_name: str, timelimit: int) -> tuple[str, str]:
    make_csynth_files(work_dir, curr_code, new_kernel_name)
    result = run_cmd_sync(work_dir, CSYNTH_CMD, timelimit)
    rpt_path = os.path.join(work_dir, "csynth", "solution", "syn", "report", f"{new_kernel_name}_csynth.rpt")
    if result["timeout"]:
        status = "timeout" if not os.path.exists(rpt_path) else "succeeded"
    elif result["returncode"] != 0 or not os.path.exists(rpt_path):
        status = "csynth_failed"
    else:
        status = "succeeded"
    error_msg = get_error_msg(work_dir)
    return status, error_msg


def do_csynth_folder(
    work_dir: str,
    files: dict[str, str],
    tcl_script: str,
    top_function: str,
    timelimit: int
) -> tuple[str, str, str]:
    """
    Run HLS synthesis with a folder of source files and a custom TCL script.
    Returns (status, error_msg, report_content).
    """
    # Write all source files to work directory
    for fname, fcontent in files.items():
        file_path = os.path.join(work_dir, fname)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(fcontent)

    # Write TCL script
    tcl_path = os.path.join(work_dir, "vitis.tcl")
    with open(tcl_path, "w", encoding="utf-8") as f:
        f.write(tcl_script)

    # Run synthesis
    result = run_cmd_sync(work_dir, CSYNTH_CMD, timelimit)

    # Check for report file
    rpt_path = os.path.join(work_dir, "csynth", "solution", "syn", "report", f"{top_function}_csynth.rpt")

    if result["timeout"]:
        status = "timeout" if not os.path.exists(rpt_path) else "succeeded"
    elif result["returncode"] != 0 or not os.path.exists(rpt_path):
        status = "csynth_failed"
    else:
        status = "succeeded"

    error_msg = get_error_msg(work_dir)

    # Read report if available
    report_content = ""
    if os.path.exists(rpt_path):
        with open(rpt_path, "r", encoding="utf-8", errors="replace") as f:
            report_content = f.read()

    return status, error_msg, report_content


def do_csim_folder(
    work_dir: str,
    files: dict[str, str],
    compile_command: str,
    timelimit: int,
    binary_files: dict[str, str] = None
) -> tuple[str, str, str]:
    """
    Run C simulation with a folder of source files and a custom g++ compile command.
    The compile command should use relative file paths and produce an executable named 'csim'.
    Supports nested directories (e.g., data/input_0.bin).
    Returns (status, error_msg, stdout).
    """
    if binary_files is None:
        binary_files = {}

    # Write all text source files to work directory (with nested path support)
    for fname, fcontent in files.items():
        file_path = os.path.join(work_dir, fname)
        # Create parent directories if needed
        os.makedirs(os.path.dirname(file_path), exist_ok=True) if os.path.dirname(fname) else None
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(fcontent)

    # Write all binary files (base64 decoded) with nested path support
    for fname, fcontent_b64 in binary_files.items():
        file_path = os.path.join(work_dir, fname)
        # Create parent directories if needed
        if os.path.dirname(fname):
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "wb") as f:
            f.write(base64.b64decode(fcontent_b64))

    # Run compilation
    compile_res = run_cmd_sync(work_dir, compile_command, timelimit)
    if compile_res["timeout"]:
        return "compile_timeout", "Compilation timed out", ""
    if compile_res["returncode"] != 0:
        return "compile_failed", compile_res["stderr"], ""

    # Check that csim executable was created
    csim_path = os.path.join(work_dir, "csim")
    if not os.path.exists(csim_path):
        return "compile_failed", "Compilation did not produce 'csim' executable. Ensure your compile command outputs to '-o csim'.", ""

    # Run the simulation
    run_res = run_cmd_sync(work_dir, "./csim", timelimit)
    if run_res["timeout"]:
        return "csim_timeout", "Simulation timed out", run_res["stdout"]
    if run_res["returncode"] != 0:
        return "csim_failed", run_res["stderr"], run_res["stdout"]

    return "succeeded", "", run_res["stdout"]


def do_csynth_and_csim(
    work_dir: str, orig_code: str, curr_code: str, testbench: str,
    new_kernel_name: str, code_for_hetero: str, first_time: bool
) -> tuple[bool, Optional[str], Optional[tuple], Optional[str], Optional[tuple]]:

    if code_for_hetero and first_time:
        hetero_csynth_dir = os.path.join(work_dir, "csynth_for_hetero")
        os.makedirs(hetero_csynth_dir, exist_ok=True)
        status, _ = do_csynth(hetero_csynth_dir, code_for_hetero, new_kernel_name, CSYNTH_TIMEOUT)
        if status != "succeeded":
            code_for_hetero = ""

    if code_for_hetero:
        hetero_csim_dir = os.path.join(work_dir, "csim_for_hetero")
        os.makedirs(hetero_csim_dir, exist_ok=True)
        status, _ = do_csim(hetero_csim_dir, orig_code, code_for_hetero, testbench, CSIM_TIMEOUT)
        if status == "succeeded":
            return True, None, None, None, None

    csynth_dir = os.path.join(work_dir, "csynth")
    os.makedirs(csynth_dir, exist_ok=True)
    csynth_res = do_csynth(csynth_dir, curr_code, new_kernel_name, CSYNTH_TIMEOUT)
    first_task, first_res = "csynth", csynth_res

    if first_res[0] != "succeeded":
        return True, first_task, first_res, None, None

    csim_dir = os.path.join(work_dir, "csim")
    os.makedirs(csim_dir, exist_ok=True)
    csim_res = do_csim(csim_dir, orig_code, curr_code, testbench, CSIM_TIMEOUT)
    return False, first_task, first_res, "csim", csim_res


@app.get("/health")
async def health():
    available = MAX_WORKERS - (MAX_WORKERS - worker_semaphore._value)
    return {"status": "ok", "available_workers": available, "max_workers": MAX_WORKERS}


@app.post("/csim", response_model=CsimResponse)
async def csim_endpoint(req: CsimRequest):
    async with worker_semaphore:
        work_dir = tempfile.mkdtemp(dir=WORK_ROOT, prefix="csim_")
        try:
            loop = asyncio.get_event_loop()
            status, error_msg = await loop.run_in_executor(
                None, do_csim, work_dir, req.orig_code, req.curr_code, req.testbench, req.timelimit
            )
            return CsimResponse(status=status, error_msg=error_msg)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


@app.post("/csynth", response_model=CsynthResponse)
async def csynth_endpoint(req: CsynthRequest):
    async with worker_semaphore:
        work_dir = tempfile.mkdtemp(dir=WORK_ROOT, prefix="csynth_")
        try:
            loop = asyncio.get_event_loop()
            status, error_msg = await loop.run_in_executor(
                None, do_csynth, work_dir, req.curr_code, req.new_kernel_name, req.timelimit
            )
            return CsynthResponse(status=status, error_msg=error_msg)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


@app.post("/csynth_folder", response_model=CsynthFolderResponse)
async def csynth_folder_endpoint(req: CsynthFolderRequest):
    """
    Run HLS synthesis with a folder of source files.
    Used by the MCP server for folder-based synthesis.
    """
    async with worker_semaphore:
        work_dir = tempfile.mkdtemp(dir=WORK_ROOT, prefix="csynth_folder_")
        try:
            loop = asyncio.get_event_loop()
            status, error_msg, report = await loop.run_in_executor(
                None, do_csynth_folder, work_dir, req.files, req.tcl_script,
                req.top_function, req.timelimit
            )
            return CsynthFolderResponse(status=status, error_msg=error_msg, report=report)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


@app.post("/csim_folder", response_model=CsimFolderResponse)
async def csim_folder_endpoint(req: CsimFolderRequest):
    """
    Run C simulation with a folder of source files and a custom g++ compile command.
    The compile command should use relative paths and produce an executable named 'csim'.
    Used by the MCP server for folder-based simulation.
    Supports nested directories and binary files (base64 encoded).
    """
    async with worker_semaphore:
        work_dir = tempfile.mkdtemp(dir=WORK_ROOT, prefix="csim_folder_")
        try:
            loop = asyncio.get_event_loop()
            status, error_msg, stdout = await loop.run_in_executor(
                None, do_csim_folder, work_dir, req.files, req.compile_command,
                req.timelimit, req.binary_files
            )
            return CsimFolderResponse(status=status, error_msg=error_msg, stdout=stdout)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


@app.post("/csynth_and_csim", response_model=CsynthAndCsimResponse)
async def csynth_and_csim_endpoint(req: CsynthAndCsimRequest):
    async with worker_semaphore:
        work_dir = tempfile.mkdtemp(dir=WORK_ROOT, prefix="combined_")
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, do_csynth_and_csim, work_dir, req.orig_code, req.curr_code,
                req.testbench, req.new_kernel_name, req.code_for_hetero, req.first_time
            )
            kill_other, first_task, first_res, second_task, second_res = result
            return CsynthAndCsimResponse(
                kill_other=kill_other,
                first_task=first_task,
                first_res=first_res,
                second_task=second_task,
                second_res=second_res
            )
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FastAPI server for remote HLS synthesis and simulation",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host address to bind")
    parser.add_argument("--port", type=int, default=8891, help="Port to listen on")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS, help="Max concurrent HLS jobs")
    parser.add_argument("--work-root", default=WORK_ROOT, help="Root directory for temporary work directories")
    args = parser.parse_args()

    MAX_WORKERS = args.workers
    WORK_ROOT = args.work_root

    uvicorn.run(app, host=args.host, port=args.port)
