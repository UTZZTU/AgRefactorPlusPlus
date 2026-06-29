import os, subprocess, traceback, time, re
from openai import OpenAI
from typing import List, Dict, Any
from datetime import datetime
from autogen import gather_usage_summary # type: ignore

GPT_API_PRICES_PER_1K = {
    'o3': [0.002, 0.008],
    'o3-mini': [0.0011, 0.0044],
    'gpt-4o': [0.0025, 0.01],
    '4o-mini': [0.0011, 0.0044],
    'gpt-4.1': [0.002, 0.008],
    'gpt-4.1-mini': [0.0004, 0.0016],
    'gpt-4.1-nano': [0.0001, 0.0004],
    'gpt-5': [0.00125, 0.01],
    'gpt-5-mini': [0.00025, 0.002],
    'gpt-5-nano': [0.00005, 0.0004],
}

# Google Gemini's OpenAI-compatible endpoint
_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

def print_usage(agents, run_time):
    usage = gather_usage_summary(agents)
    usage_data = usage.get('usage_including_cached_inference', {})
    total_cost = 0.0
    model_costs = {}
    for model_name, data in usage_data.items():
        if model_name == 'total_cost':
            continue
        base_model = model_name
        if '-' in model_name:
            parts = model_name.split('-')
            for i in range(len(parts), 0, -1):
                candidate = '-'.join(parts[:i])
                if candidate in GPT_API_PRICES_PER_1K:
                    base_model = candidate
                    break
        prices = GPT_API_PRICES_PER_1K.get(base_model)
        if not prices:
            prompt_price, completion_price = 0.0, 0.0
        else:
            prompt_price, completion_price = prices
        prompt_tokens = data.get('prompt_tokens', 0)
        completion_tokens = data.get('completion_tokens', 0)
        cost = (prompt_tokens / 1000) * prompt_price + (completion_tokens / 1000) * completion_price
        model_costs[model_name] = {
            "cost": cost,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": data.get('total_tokens', 0),
        }
        total_cost += cost

    date_str = run_time[:8]
    runs_dir = os.path.join(os.getenv("RUN_DIR") or "runs", date_str)
    os.makedirs(runs_dir, exist_ok=True)
    log_file_path = os.path.join(runs_dir, f"{date_str}.txt")
    daily_total_cost = 0.0
    if os.path.exists(log_file_path):
        try:
            with open(log_file_path, 'r') as f:
                first_line = f.readline().strip()
                if first_line.startswith("Total today:"):
                    daily_total_cost = float(first_line.split("$")[1])
        except (ValueError, IndexError):
            daily_total_cost = 0.0
    daily_total_cost += total_cost
    existing_entries = []
    if os.path.exists(log_file_path):
        try:
            with open(log_file_path, 'r') as f_read:
                lines = f_read.readlines()
                if len(lines) > 1:
                    existing_entries = lines[1:]
        except:
            existing_entries = []
    with open(log_file_path, 'w') as f:
        f.write(f"Total today: ${daily_total_cost:.6f}\n")
        for entry in existing_entries:
            f.write(entry)
        f.write(f"- {run_time}: ${total_cost:.6f} (total till now: ${daily_total_cost:.6f})\n")

    print("\n========= Usage Summary ========")
    print(f"Total Cost: ${total_cost:.4f}")
    for model_name, info in model_costs.items():
        print(f">>Model: {model_name}")
        print(f"  Total Cost: ${info['cost']:.6f}")
        print(f"  Prompt Tokens: {info['prompt_tokens']:,}")
        print(f"  Completion Tokens: {info['completion_tokens']:,}")
        print(f"  Total Tokens: {info['total_tokens']:,}")
    print("=" * 32)

def make_vitis_tcl(top_kernel: str, file_list: list[str]) -> str:
    tcl_lines = []
    tcl_lines.append(f'open_project csynth')
    tcl_lines.append(f'set_top {top_kernel}')
    for fname in file_list:
        tcl_lines.append(f'add_files "{fname}" -cflags " -O3 -D XILINX "')
    tcl_lines.append('open_solution -flow_target vitis solution')
    tcl_lines.append('set_part xcu200-fsgd2104-2-e')
    tcl_lines.append('create_clock -period 200MHz -name default')
    tcl_lines.append('csynth_design')
    tcl_lines.append('close_project')
    tcl_lines.append('exit')
    return '\n'.join(tcl_lines)

def make_synthesis_script(work_dir: str, top_kernel: str, file_list: dict[str, str]):
    date_time = datetime.now().strftime("%Y%m%d%H%M%S")
    dir_name = f"{date_time}"
    dir_path = os.path.join(work_dir, dir_name)
    os.makedirs(dir_path, exist_ok=True)

    for fname, fcontent in file_list.items():
        file_path = os.path.join(dir_path, fname)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(fcontent)

    tcl_content = make_vitis_tcl(top_kernel, list(file_list.keys()))
    tcl_path = os.path.join(dir_path, "vitis.tcl")
    with open(tcl_path, "w", encoding="utf-8") as f:
        f.write(tcl_content)

    return dir_path

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


def get_model(vendor: str) -> OpenAI:
    """
    Return an OpenAI client configured for the given vendor.

    Env:
      - OPENAI_API_KEY
      - GEMINI_API_KEY
    """
    v = vendor.lower().strip()

    if v == "openai":
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY is not set.")
        return OpenAI(api_key=key)

    if v == "gemini":
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set.")
        return OpenAI(api_key=key, base_url=_GEMINI_BASE_URL)

    raise ValueError(f"Unsupported vendor: {vendor!r}. Use 'gemini' or 'openai'.")


def get_response(
    client: OpenAI,
    model: str,
    messages: List[Dict[str, Any]],
    reasoning_effort: str | None = None,
):
    failed = True
    resp = None
    while failed:
        failed = False
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                reasoning_effort=reasoning_effort
            )
        except Exception as e:
            failed = True
            print(f"[get_response] Exception: {e!r}")
            traceback.print_exc()
            time.sleep(2)
    return resp.choices[0].message


_FENCE_RE = re.compile(
    r"```(?P<lang>[^\n`]*)\r?\n(?P<code>.*?)(?:\r?\n)?```",
    re.DOTALL
)

_C_ALIASES = {"c", "h"}
_CPP_ALIASES = {"cpp", "c++", "cc", "cxx", "hpp", "h++", "hxx"}


def _normalize_lang_tag(tag: str) -> str:
    """
    Normalize language tags like 'C++20', 'cpp14', 'c   ', 'cc', etc.
    Keeps only the alphabetic/+ prefix and lowercases it.
    """
    tag = (tag or "").strip().lower()
    m = re.match(r"[a-z+]+", tag)  # 'c++20' -> 'c++', 'cpp14' -> 'cpp'
    return m.group(0) if m else ""


def extract_c_or_cpp_code(text: str) -> str:
    """
    Extract a single C or C++ code block from a raw string.

    Strategy:
      1) Return the first fenced block explicitly labeled as C/C++.
      2) If none match but there is exactly one fenced block, return it.
      3) Otherwise raise ValueError.
    """
    matches = list(_FENCE_RE.finditer(text))
    if not matches:
        raise ValueError("No fenced code block found (```lang ... ```).")

    # 1) Prefer a C/C++-labeled block
    for m in matches:
        lang = _normalize_lang_tag(m.group("lang"))
        if lang in _C_ALIASES or lang in _CPP_ALIASES:
            return m.group("code").strip()

    # 2) If only one block exists, return it (assumes single code block output)
    if len(matches) == 1:
        return matches[0].group("code").strip()

    # 3) Multiple blocks and none marked as C/C++
    raise ValueError("Found code blocks, but none labeled as C/C++. Use ```c or ```cpp fences.")


if __name__ == "__main__":
    with open("src/test1.cpp", "r") as f:
        file_list = {
            "test.cpp": f.read()
        }
    make_synthesis_script("s/", "test", file_list)
