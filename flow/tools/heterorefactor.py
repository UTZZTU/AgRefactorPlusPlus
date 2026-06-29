import os, dotenv, time, shutil
from autogen.agentchat.group import ContextVariables
from flow.tools.general import run_cmd

cur_dir = os.path.dirname(os.path.abspath(__file__))
dotenv.load_dotenv(os.path.join(cur_dir, '../../.env'), override=True)

HETEROREFACTOR_TIMEOUT = 300
# Extra slack for the full instrumented pipeline (instrument + compile + run + invariant + refactor).
HETEROREFACTOR_RECURSIVE_TIMEOUT = 600
# this directory should contain heterorefactor.sif and heterorefactor/ repo
HETEROREFACTOR_DIR = os.getenv('HETEROREFACTOR_DIR') # dir containing heterorefactor.sif + heterorefactor/ repo

def make_heterorefactor_script(run_dir: str):
    sh_path = os.path.join(run_dir, "heterorefactor.sh")
    with open(sh_path, "w") as sh_file:
        sh_file.write("#!/bin/bash\n")
        sh_file.write(f"cd {HETEROREFACTOR_DIR}/heterorefactor\n")
        sh_file.write("./heterorefactor/refactoring/build/heterorefactor -rec -u $1 $2\n")

def call_heterorefactor(run_dir: str, cv: ContextVariables, debug: int = 0):
    with open(os.path.join(run_dir, "tmp.cpp"), "w") as f:
        f.write(cv["curr_code"])
    print("create at", os.path.join(run_dir, "tmp.cpp"))
    make_heterorefactor_script(run_dir)

    cmd = f"apptainer exec {HETEROREFACTOR_DIR}/heterorefactor.sif bash {os.path.join(run_dir, 'heterorefactor.sh')} {os.path.join(run_dir, 'refactored_code.cpp')} {os.path.join(run_dir, 'tmp.cpp')}"
    res = run_cmd(run_dir, cmd, HETEROREFACTOR_TIMEOUT)
    time.sleep(0.5)
    if debug:
        if res["returncode"] != 0:
            print(res['stdout'])
            print(res['stderr'])
    # os.remove(os.path.join(run_dir, "tmp.cpp"))
    # os.remove(os.path.join(run_dir, "heterorefactor.sh"))

    return os.path.exists(os.path.join(run_dir, "refactored_code.cpp"))


# ---------------------------------------------------------------------------
# Invariant-guided heterorefactor for recursive kernels.
#
# TODO(known issue): the fresh `.ivr` produced by step 4 below uses a symbol ID
# (e.g., `rec LLIwTjHVXwXQR 2`) that does NOT match the symbol ID heterorefactor's
# analyzer reconstructs from the same kernel AST (e.g., `rec L5457R__L5458R 2`
# in upstream's pre-computed invariants). Result: the analyzer silently ignores
# the recfile and falls back to brute-force, which hangs on non-trivial recursive
# kernels (e.g., strassen). The upstream Makefile presumably works because its
# instrument.exe is built in a self-consistent ROSE env that produces a matching
# tag. Until we figure out the right mangling/instrumentation flags here,
# `call_heterorefactor_recursive` is only useful as a reference pipeline; use
# upstream pre-computed `.ivr` files when available. See `scripts/compute_ablation_tables.py`
# `PAPER_OVERRIDES_PREPROC_HETERO` for the workaround we apply in the rebuttal table.
# ---------------------------------------------------------------------------
#
# The base wrapper above runs `heterorefactor -rec -u out in`. Without a `-recfile`
# invariant profile, ROSE's recursive analyzer brute-force-searches the call
# graph, which times out on non-trivial recursive kernels (e.g., strassen).
#
# The upstream Makefile (heterorefactor/experiments/Recursive/commons/Makefile.inc)
# uses a 5-step pipeline:
#   1) heterorefactor -instrument <profile> -u kernel_instrument.cpp kernel.cpp
#   2) g++ -o instrument.exe kernel_instrument.cpp testbed.cpp
#   3) testdata | ./instrument.exe > /dev/null      # writes <profile>
#   4) awk -f generate_invariant.awk <profile> > invariant.ivr
#   5) heterorefactor -rec -recfile invariant.ivr -u refactored.cpp kernel.cpp
# This function implements the same pipeline through the apptainer image so
# callers can run it on their own kernel + (testbed, data_generator) fixtures.
# ---------------------------------------------------------------------------

def _ensure_extern_c(src: str, entry: str = "process_top") -> str:
    """Wrap the entrypoint function in `extern "C"` if not already.

    Upstream testbeds declare the entrypoint with C linkage; refactor inputs
    without `extern "C"` cause undefined-reference link errors. This is a
    lightweight string-level patch: prepend `extern "C" ` to the entry's
    definition signature. Idempotent: if `extern "C"` already appears anywhere
    in the source, leave it alone.
    """
    if 'extern "C"' in src:
        return src
    import re
    # Match the entry definition line. Tolerant of return type / whitespace.
    pat = re.compile(rf'^(\s*)(\w[\w\s\*&]*\s+){re.escape(entry)}\s*\(', re.MULTILINE)
    return pat.sub(rf'\1extern "C" \2{entry}(', src, count=1)


def call_heterorefactor_recursive(
    run_dir: str,
    cv: ContextVariables,
    testbed_src: str,
    data_generator_sh: str,
    test_size: str = "1024",
    entry_name: str = "process_top",
    debug: int = 0,
):
    """Invariant-guided recursive heterorefactor.

    Args:
        run_dir: scratch dir (writable inside apptainer; usually the kernel's work dir)
        cv: must contain cv["curr_code"]  → written as tmp.cpp
        testbed_src: path to a testbed.cpp that links against the kernel's
                     `process_top` (or whatever the entrypoint is) and reads test
                     inputs from stdin. Copied into run_dir as testbed.cpp.
        data_generator_sh: path to a shell script that prints test data to stdout
                           when called as `data_generator.sh <size>`. Copied into
                           run_dir as data_generator.sh.
        test_size: argument to data_generator (e.g. "1024"); influences invariant.
        debug: print stdout/stderr on failure.

    Returns:
        True iff `refactored_code.cpp` exists in run_dir after the pipeline.
    """
    # Stage the kernel + fixtures into run_dir so apptainer can see them all.
    patched_src = _ensure_extern_c(cv["curr_code"], entry=entry_name)
    with open(os.path.join(run_dir, "tmp.cpp"), "w") as f:
        f.write(patched_src)
    shutil.copy(testbed_src,        os.path.join(run_dir, "testbed.cpp"))
    shutil.copy(data_generator_sh,  os.path.join(run_dir, "data_generator.sh"))
    os.chmod(os.path.join(run_dir, "data_generator.sh"), 0o755)

    profile        = os.path.join(run_dir, "hetero-profile")
    kernel         = os.path.join(run_dir, "tmp.cpp")
    testbed        = os.path.join(run_dir, "testbed.cpp")
    instrumented   = os.path.join(run_dir, "kernel_instrument.cpp")
    instrument_exe = os.path.join(run_dir, "instrument.exe")
    invariant      = os.path.join(run_dir, "invariant.ivr")
    refactored     = os.path.join(run_dir, "refactored_code.cpp")

    sh_path = os.path.join(run_dir, "heterorefactor_recursive.sh")
    with open(sh_path, "w") as f:
        f.write(f"""#!/bin/bash
set -e
cd {HETEROREFACTOR_DIR}/heterorefactor

# 1) Generate instrumented version of the kernel (writes profile at runtime).
./heterorefactor/refactoring/build/heterorefactor -instrument {profile} -u {instrumented} {kernel}

# 2) Compile instrument.exe = instrumented kernel + testbed harness.
g++ -o {instrument_exe} {instrumented} {testbed}

# 3) Run with test data; this writes the recursion profile to {profile}.
bash {os.path.join(run_dir, 'data_generator.sh')} {test_size} | {instrument_exe} > /dev/null

# 4) Derive the invariant file from the profile.
awk -f ./heterorefactor/instrumentation/recursive/generate_invariant.awk {profile} > {invariant}

# 5) Final invariant-guided refactor.
./heterorefactor/refactoring/build/heterorefactor -rec -recfile {invariant} -u {refactored} {kernel}
""")
    os.chmod(sh_path, 0o755)

    cmd = f"apptainer exec {HETEROREFACTOR_DIR}/heterorefactor.sif bash {sh_path}"
    res = run_cmd(run_dir, cmd, HETEROREFACTOR_RECURSIVE_TIMEOUT)
    time.sleep(0.5)
    if debug or res["returncode"] != 0:
        print(f"--- heterorefactor recursive stdout (last 1500) ---\n{(res.get('stdout') or '')[-1500:]}")
        print(f"--- heterorefactor recursive stderr (last 1500) ---\n{(res.get('stderr') or '')[-1500:]}")

    return os.path.exists(refactored)
