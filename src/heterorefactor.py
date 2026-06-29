import os, dotenv, subprocess, shutil, json, tempfile
from typing import Tuple, Dict, Any, List
from sys import stderr # type: ignore
from autogen.agentchat.group import ContextVariables # type: ignore

dotenv.load_dotenv('.env', override=True)
RUN_DIR = os.getenv('RUN_DIR')

def run_cmd(
    work_dir: str,
    cmd: str,
    timelimit: int
) -> dict:
    try:
        result = subprocess.run(
            cmd, cwd=work_dir, timeout=timelimit, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8",  errors="replace"
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timeout": False
        }
    except subprocess.TimeoutExpired as e:
        return {
            "returncode": None,
            "stdout": e.stdout if hasattr(e, "stdout") and e.stdout is not None else "",
            "stderr": e.stderr if hasattr(e, "stderr") and e.stderr is not None else "",
            "timeout": True
        }

dotenv.load_dotenv('.env', override=True)
HETEROREFACTOR_TIMEOUT = 30
HETEROREFACTOR_DIR = os.getenv('HETEROREFACTOR_DIR')
HLS_TIMEOUT = 300
REMOVE_HETERO_DIR = True
SCRATCH_DIR = os.getenv('SCRATCH_DIR') or tempfile.gettempdir()

def make_heterorefactor_script(run_dir: str):
    sh_path = os.path.join(run_dir, "heterorefactor.sh")
    with open(sh_path, "w") as sh_file:
        sh_file.write("#!/bin/bash\n")
        sh_file.write(f"cd {HETEROREFACTOR_DIR}/heterorefactor\n")
        sh_file.write("./heterorefactor/refactoring/build/heterorefactor -rec -u $1 $2\n")


def call_heterorefactor(run_dir: str, cv: ContextVariables):
    with open(os.path.join(run_dir, "tmp.cpp"), "w") as f:
        f.write(cv["curr_code"])
    make_heterorefactor_script(run_dir)

    cmd = f"apptainer exec {HETEROREFACTOR_DIR}/heterorefactor.sif bash {os.path.join(run_dir, 'heterorefactor.sh')} {os.path.join(run_dir, 'refactored_code.cpp')} {os.path.join(run_dir, 'tmp.cpp')}"
    stderr = run_cmd(run_dir, cmd, HETEROREFACTOR_TIMEOUT)
    print(stderr["stderr"])
    print(stderr["stdout"])
    os.remove(os.path.join(run_dir, "tmp.cpp"))
    os.remove(os.path.join(run_dir, "heterorefactor.sh"))

    return os.path.exists(os.path.join(run_dir, "refactored_code.cpp"))


def call_heterorefactor_on_file(work_dir: str, input_cpp_path: str, output_cpp_path: str) -> bool:
    print(f"[hetero] Refactor start: input={input_cpp_path} -> output={output_cpp_path}")
    make_heterorefactor_script(work_dir)
    cmd = (
        f"apptainer exec {HETEROREFACTOR_DIR}/heterorefactor.sif "
        f"bash {os.path.join(work_dir, 'heterorefactor.sh')} "
        f"{output_cpp_path} {input_cpp_path}"
    )
    res = run_cmd(work_dir, cmd, HETEROREFACTOR_TIMEOUT)
    print(f"[hetero] Refactor done: returncode={res['returncode']} timeout={res['timeout']}")
    try:
        os.remove(os.path.join(work_dir, "heterorefactor.sh"))
    except FileNotFoundError:
        pass
    return os.path.exists(output_cpp_path)


def copy_vitis_tcl_to_dir(target_dir: str) -> str:
    script_dir = os.path.dirname(__file__)
    src_tcl = os.path.join(script_dir, "vitis.tcl")
    dst_tcl = os.path.join(target_dir, "vitis.tcl")
    shutil.copy(src_tcl, dst_tcl)
    return dst_tcl


def _cleanup_hls_artifacts(target_dir: str) -> None:
    print(f"[hls] Cleaning artifacts in {target_dir}")
    project_dir = os.path.join(target_dir, "csyn")
    if os.path.isdir(project_dir):
        shutil.rmtree(project_dir, ignore_errors=True)
    log_file = os.path.join(target_dir, "vitis_hls.log")
    try:
        if os.path.isfile(log_file):
            os.remove(log_file)
    except FileNotFoundError:
        pass
    tcl_file = os.path.join(target_dir, "vitis.tcl")
    try:
        if os.path.isfile(tcl_file):
            os.remove(tcl_file)
    except FileNotFoundError:
        pass


def run_vitis_hls_in_dir(target_dir: str, timelimit: int = HLS_TIMEOUT) -> bool:
    print(f"[hls] Running Vitis HLS in {target_dir}")
    result = run_cmd(target_dir, "vitis_hls -f vitis.tcl", timelimit)
    success = (result["returncode"] == 0) and (not result["timeout"])
    print(f"[hls] Done in {target_dir}: returncode={result['returncode']} timeout={result['timeout']} success={success}")
    _cleanup_hls_artifacts(target_dir)
    return success


def _cleanup_dir(path: str) -> None:
    if os.path.isdir(path):
        print(f"[clean] Removing directory {path}")
        shutil.rmtree(path, ignore_errors=True)


def build_with_hls_or_heterorefactor(folder_path: str) -> Tuple[bool, bool]:
    abs_dir = os.path.abspath(folder_path)
    kernel_path = os.path.join(abs_dir, "kernel.cpp")
    if not os.path.exists(kernel_path):
        return (False, False)

    base_name = os.path.basename(abs_dir.rstrip(os.sep)) or "case"
    scratch_root = os.path.join(SCRATCH_DIR, "agrefactor", "single", base_name)
    _cleanup_dir(scratch_root)
    os.makedirs(scratch_root, exist_ok=True)

    scratch_orig = os.path.join(scratch_root, "orig")
    os.makedirs(scratch_orig, exist_ok=True)
    shutil.copy(kernel_path, os.path.join(scratch_orig, "kernel.cpp"))
    try:
        write_vitis_tcl_with_top(scratch_orig, "top")
    except Exception as e:
        print(f"Failed to write vitis.tcl in {scratch_orig}: {e}", file=stderr)
        _cleanup_dir(scratch_root)
        return (False, False)

    if run_vitis_hls_in_dir(scratch_orig):
        _cleanup_dir(scratch_root)
        return (True, True)

    hetero_dir = os.path.join(scratch_root, "hetero")
    os.makedirs(hetero_dir, exist_ok=True)
    hetero_kernel_path = os.path.join(hetero_dir, "kernel.cpp")

    refactor_ok = call_heterorefactor_on_file(hetero_dir, kernel_path, hetero_kernel_path)
    if not refactor_ok or (not os.path.exists(hetero_kernel_path)):
        _cleanup_dir(scratch_root)
        return (False, False)

    try:
        write_vitis_tcl_with_top(hetero_dir, "top")
    except Exception as e:
        print(f"Failed to write vitis.tcl in {hetero_dir}: {e}", file=stderr)
        _cleanup_dir(scratch_root)
        return (False, False)

    if run_vitis_hls_in_dir(hetero_dir):
        _cleanup_dir(scratch_root)
        return (False, True)
    _cleanup_dir(scratch_root)
    return (False, False)


def get_project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def write_vitis_tcl_with_top(target_dir: str, top_name: str, template_path: str = None) -> str:
    project_root = get_project_root()
    if template_path is None:
        template_path = os.path.join(project_root, "paper", "nonsyn", "vitis.tcl")
    dst_tcl = os.path.join(target_dir, "vitis.tcl")
    try:
        with open(template_path, "r") as f:
            content = f.read()
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if line.strip().startswith("set_top "):
                lines[i] = f"set_top {top_name}"
                break
        content = "\n".join(lines) + ("\n" if not content.endswith("\n") else "")
    except Exception:
        content = (
            "open_project csyn\n"
            f"set_top {top_name}\n"
            "add_files \"kernel.cpp\" -cflags \" -O3 -D XILINX \"\n"
            "open_solution -flow_target vitis solution\n"
            "set_part xcu200-fsgd2104-2-e\n"
            "create_clock -period 200MHz -name default\n"
            "csynth_design\n"
            "close_project\n"
            "exit\n"
        )
    with open(dst_tcl, "w") as f:
        f.write(content)
    return dst_tcl


def process_info_json(info_path: str, output_path: str) -> Dict[str, Any]:
    with open(info_path, "r") as f:
        data = json.load(f)

    project_root = get_project_root()
    results: List[Dict[str, Any]] = []

    def process_category(cat_name: str, cat_map: Dict[str, List[str]]):
        for kernel_key, value in cat_map.items():
            print(f"[info] Processing {cat_name}:{kernel_key}")
            try:
                code_relpath, top_name = value[0], value[1]
            except Exception:
                results.append({
                    "category": cat_name,
                    "name": kernel_key,
                    "error": "Invalid info.json entry",
                })
                continue

            src_cpp_abs = os.path.join(project_root, code_relpath)
            kernel_dir = os.path.dirname(src_cpp_abs)
            # Scratch working directory per case
            scratch_case_dir = os.path.join(SCRATCH_DIR, "agrefactor", cat_name, kernel_key)
            _cleanup_dir(scratch_case_dir)
            os.makedirs(scratch_case_dir, exist_ok=True)
            hetero_dir = scratch_case_dir
            hetero_cpp = os.path.join(hetero_dir, "kernel.cpp")

            refactor_ok = call_heterorefactor_on_file(hetero_dir, src_cpp_abs, hetero_cpp)

            if refactor_ok and os.path.exists(hetero_cpp):
                print(f"[info] Refactor OK for {kernel_key}, starting HLS in {hetero_dir}")
                write_vitis_tcl_with_top(hetero_dir, top_name)
                hls_ok = run_vitis_hls_in_dir(hetero_dir)
            else:
                print(f"[warn] Refactor failed for {kernel_key}")
                hls_ok = False

            status = {
                "name": kernel_key,
                "top": top_name,
                "source": code_relpath,
                "hetero_dir": hetero_dir,
                "refactor_ok": refactor_ok,
                "hls_ok": hls_ok
            }

            results.append(status)

            try:
                with open(output_path, "w") as f:
                    json.dump({"kernels": results}, f, indent=2)
                print(f"[info] Updated aggregated results: {output_path}")
            except Exception as e:
                print(f"[warn] Failed updating aggregated results: {e}")

            if REMOVE_HETERO_DIR:
                _cleanup_dir(scratch_case_dir)

    useful_map = data.get("useful", {})
    not_useful_map = data.get("not_useful", {})
    process_category("useful", useful_map)
    process_category("not_useful", not_useful_map)

    out = {"kernels": results}
    with open(output_path, "w") as f:
        json.dump(out, f, indent=2)
    return out


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="mode")

    p1 = subparsers.add_parser("single")
    p1.add_argument("run_dir", type=str)

    p2 = subparsers.add_parser("from-info")
    p2.add_argument("--info", type=str, default=os.path.join(os.path.dirname(__file__), "info.json"))
    p2.add_argument("--out", type=str, default=os.path.join(os.path.dirname(__file__), "results_info.json"))

    args = parser.parse_args()
    if args.mode == "single":
        print(build_with_hls_or_heterorefactor(os.path.abspath(args.run_dir)))
    elif args.mode == "from-info":
        process_info_json(os.path.abspath(args.info), os.path.abspath(args.out))
    else:
        parser.print_help()