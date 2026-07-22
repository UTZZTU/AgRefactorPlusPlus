import os, dotenv, concurrent.futures, argparse, copy  # type: ignore
from autogen.agentchat.group import ContextVariables  # type: ignore
from typing import Optional, Dict, Any
import flow.tools as tools
from flow.rag.rag_integration import KnowledgeManager
from flow.base_agent import reset_agrefactorpp_usage_registry, print_agrefactorpp_usage_summary
from agrefactor.runtime.budget import BudgetManager
from agrefactor.testing import (
    build_openai_compatible_testbench_repairer,
)

dotenv.load_dotenv('.env', override=True)
RUN_DIR = os.getenv('RUN_DIR')   # base dir for run outputs/logs, e.g. "$AGREFACTOR_ROOT/runs"
WORK_DIR = os.getenv('WORK_DIR') # optional scratch dir for intermediate work
MAX_RETRY_ATTEMPTS = 3


_AUTOGEN_REASONING_EFFORTS = frozenset(
    {"none", "low", "minimal", "medium", "high", "xhigh"}
)
_REASONING_EFFORT_ALIASES = {"max": "xhigh"}


def normalize_reasoning_effort(
    reasoning_effort: Optional[str],
) -> Optional[str]:
    if reasoning_effort is None:
        return None
    if not isinstance(reasoning_effort, str):
        raise TypeError("reasoning_effort must be a string or None")
    cleaned = reasoning_effort.strip().lower()
    if not cleaned:
        return None
    normalized = _REASONING_EFFORT_ALIASES.get(cleaned, cleaned)
    if normalized not in _AUTOGEN_REASONING_EFFORTS:
        allowed = ", ".join(
            sorted(
                _AUTOGEN_REASONING_EFFORTS
                | set(_REASONING_EFFORT_ALIASES)
            )
        )
        raise ValueError(
            f"Unsupported reasoning_effort {reasoning_effort!r}; "
            f"expected one of: {allowed}"
        )
    return normalized


def make_llm_config(
    model: Optional[str],
    reasoning_effort: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not model:
        return None
    api_type = "google" if "gemini" in model.lower() else "openai"
    config: Dict[str, Any] = {"model": model, "api_type": api_type}
    normalized = normalize_reasoning_effort(reasoning_effort)
    if normalized:
        config["reasoning_effort"] = normalized
    if base_url:
        config["base_url"] = base_url
    return config


def resolve_runtime_llm_config(
    model: Optional[str],
    reasoning_effort: Optional[str] = None,
    base_url: Optional[str] = None,
    llm_config_override: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    if llm_config_override is not None:
        if not isinstance(llm_config_override, dict):
            raise TypeError(
                "llm_config_override must be a dictionary or None"
            )
        return copy.deepcopy(llm_config_override)
    return make_llm_config(
        model,
        reasoning_effort,
        base_url,
    )


def debug_print(debug: int, msg: str):
    if debug >= 1:
        print(f"=============== {msg} ===============")

def hls_refactor_with_rag(
    kernel_path: str,
    kernel_name: str,
    knowledge_db_path: str = "./knowledge_db/tmp_db",
    embedding_model: str = "all-MiniLM-L6-v2",
    enable_rag: bool = False,
    enable_rag_update: bool = False,
    reset_knowledge_db: bool = False,
    output_dir: Optional[str] = None,
    max_retry_attempts: int = MAX_RETRY_ATTEMPTS,
    hetero_enabled: bool = False,
    debug: int = 0,
    model: Optional[str] = None,
    remote: bool = False,
    reasoning_effort: Optional[str] = None,
    base_url: Optional[str] = None,
    llm_config_override: Optional[Dict[str, Any]] = None,
    effective_model_config_manifest: Optional[Dict[str, Any]] = None,
    family_instruction: Optional[str] = None,
    model_configuration_source: str = "legacy_compatibility",
    target_profile: Optional[Dict[str, Any]] = None,
    budget: Optional[BudgetManager] = None,
    enable_testbench_repair: bool = False,
    max_testbench_repair_attempts: int = 2,
    testbench_repair_model: Optional[str] = None,
    testbench_repair_api_key_env: str = "OPENAI_API_KEY",
    external_testbench: Optional[str] = None,
    external_tb_instruction: Optional[str] = None,
    external_kernel_name: Optional[str] = None,
    # Coverage-optimized TB loop (default off → behavior unchanged).
    enable_tb_coverage_loop: bool = False,
    public_tb_rounds: int = 3,
    public_tb_target: float = 80.0,
    # Hidden TB eval gate (default off → behavior unchanged).
    enable_hidden_tb_eval: bool = False,
    hidden_tb_rounds: int = 6,
    hidden_tb_trajectories: int = 3,
    hidden_tb_target: float = 90.0,
    golden_tb_cache_dir: Optional[str] = None,
    golden_tb_cache_key: Optional[str] = None,  # if None, defaults to kernel_name in make_golden_hidden_tb
    use_cached_tb_as_public: bool = False,  # Skip TB gen entirely; use cached hidden TB as the agent's testbench.
):
    reset_agrefactorpp_usage_registry()
    llm_config = resolve_runtime_llm_config(
        model,
        reasoning_effort,
        base_url,
        llm_config_override,
    )
    if (
        effective_model_config_manifest is not None
        and not isinstance(
            effective_model_config_manifest,
            dict,
        )
    ):
        raise TypeError(
            "effective_model_config_manifest must be "
            "a dictionary or None"
        )
    if (
        family_instruction is not None
        and not isinstance(family_instruction, str)
    ):
        raise TypeError(
            "family_instruction must be a string or None"
        )
    if (
        not isinstance(model_configuration_source, str)
        or not model_configuration_source.strip()
    ):
        raise ValueError(
            "model_configuration_source must not be empty"
        )

    if max_testbench_repair_attempts < 0:
        raise ValueError(
            "max_testbench_repair_attempts must not be negative"
        )
    if target_profile is not None and not isinstance(
        target_profile,
        dict,
    ):
        raise TypeError("target_profile must be a dictionary")
    if budget is not None and not isinstance(
        budget,
        BudgetManager,
    ):
        raise TypeError("budget must be a BudgetManager or None")
    if remote and budget is not None and (
        budget.limits.max_tool_calls is not None
        or budget.limits.max_compile_calls is not None
        or budget.limits.max_csim_calls is not None
        or budget.limits.max_csynth_calls is not None
    ):
        raise ValueError(
            "hard tool budgets currently require local execution; "
            "the remote HLS path cannot share the BudgetManager"
        )

    testbench_repairer = None
    if enable_testbench_repair:
        if remote:
            raise ValueError(
                "testbench repair currently supports local "
                "validation only"
            )
        if max_testbench_repair_attempts < 1:
            raise ValueError(
                "enabled testbench repair requires at least "
                "one repair attempt"
            )
        repair_model = testbench_repair_model or model
        if not repair_model:
            raise ValueError(
                "enabled testbench repair requires "
                "testbench_repair_model or model"
            )
        testbench_repairer = (
            build_openai_compatible_testbench_repairer(
                model=repair_model,
                base_url=base_url,
                api_key_env=testbench_repair_api_key_env,
            )
        )

    output_dir = tools.general.create_output_dir(output_dir)
    tools.general.create_log_and_redirect(output_dir)
    
    with open(kernel_path, "r", encoding="utf-8") as f:
        source_code = f.read()

    cv = ContextVariables(data={
        "orig_code": source_code,     # original source code
        "kernel_name": kernel_name,   # kernel function name
        "hetero": "",                 # whether heterogeneous programming is used
        "curr_code": source_code,     # current code
        "code_for_hetero": "",        # current code for hetero tool to refactor
        "new_kernel_name": "",        # new kernel function name
        "target_profile": dict(target_profile or {}),
        "effective_model_config": copy.deepcopy(
            effective_model_config_manifest
        ),
        "model_family_instruction": (
            family_instruction or ""
        ),
        "model_configuration_source": (
            model_configuration_source
        ),
        "identified_items": [],       # identified items
        "items_hetero": [],           # identified items for heterorefactor
        "testbench": "",              # generated testbench
        "tb_aligned_instruction": "", # generated instruction aligned with the testbench
        "plan": "",                   # generated plan
        "plan_hetero": "",            # generated plan for hetero tool
        "csynth_csim_history": [],    # history of synthesis and simulation results
        "csynth_error_msg": "",       # error message
    })

    if hetero_enabled:
        debug_print(debug, "HeteroRefactor Checking")
        if tools.heterorefactor.call_heterorefactor(output_dir, cv):
            with open(os.path.join(output_dir, "refactored_code.cpp"), "r", encoding="utf-8") as f:
                cv["curr_code"] = f.read()
            status, error_msg = tools.csynth.run_csynth(
                output_dir,
                cv,
                budget=budget,
            )
            if status != "succeeded":
                cv["curr_code"] = source_code
            else:
                cv["hetero"] = "succeeded by hetero before main refactoring"
                tools.general.save_context("final", cv, output_dir)
                return True, cv

    debug_print(debug, f"Starting HLS test with RAG for: {kernel_path}")
    debug_print(debug, f"Output directory: {output_dir}")
    debug_print(debug, f"RAG enabled: {enable_rag}")
    debug_print(debug, f"RAG update enabled: {enable_rag_update}")
    debug_print(debug, f"Reset knowledge DB: {reset_knowledge_db}")
    debug_print(debug, f"Knowledge DB path: {knowledge_db_path}")

    if external_testbench:
        # Use provided testbench instead of generating one
        cv["testbench"] = external_testbench
        cv["tb_aligned_instruction"] = external_tb_instruction or ""
        cv["new_kernel_name"] = external_kernel_name or kernel_name + "_hls"
        debug_print(debug, "Using external testbench (skipping generation)")
        with concurrent.futures.ThreadPoolExecutor() as executor:
            debug_print(debug, "Identification")
            identification_future = tools.identifying.identify_non_synthesizable_items(
                cv, knowledge_db_path, embedding_model, enable_rag, hetero_enabled, reset_knowledge_db, executor, debug, llm_config
            )
            cv["identified_items"], cv["items_hetero"] = identification_future.result()
    elif use_cached_tb_as_public:
        # Load the cached hidden TB (e.g. from golden_tb_rater_flow/) and use it
        # directly as the agent's public testbench. Skips the testbench generation
        # phase entirely (no LLM call, deterministic).
        import json as _json
        if not golden_tb_cache_dir or not golden_tb_cache_key:
            raise ValueError("--use_cached_tb_as_public requires both --golden_tb_cache_dir and --golden_tb_cache_key")
        cache_path = os.path.join(golden_tb_cache_dir, f"{golden_tb_cache_key}.json")
        debug_print(debug, f"Loading cached hidden TB as public TB from: {cache_path}")
        with open(cache_path) as _f:
            _golden = _json.load(_f)
        cv["testbench"] = _golden.get("hidden_tb", "")
        # Derive new_kernel_name from the canonical _hls decl in the cache,
        # falling back to <kernel_name>_hls.
        _decl = _golden.get("hidden_hls_decl_verbatim", "")
        import re as _re
        _m = _re.search(r'\b(\w+_hls)\s*\(', _decl)
        cv["new_kernel_name"] = _m.group(1) if _m else f"{kernel_name}_hls"
        # Build a minimal instruction so the refactor agent knows what to do.
        cv["tb_aligned_instruction"] = (
            f"Implement `{cv['new_kernel_name']}` matching the forward declaration "
            f"in the provided testbench. The testbench will pass golden vs your "
            f"implementation through csim; your function must produce outputs "
            f"that match the golden invocation on the same inputs.\n\n"
            f"Pinned `_hls` declaration (use this signature character-for-character):\n"
            f"```cpp\n{_decl}\n```"
        ) if _decl else (
            f"Implement `{cv['new_kernel_name']}` matching the forward declaration "
            f"in the provided testbench."
        )
        debug_print(debug, f"Cached TB loaded: cov={_golden.get('hidden_cov')}, "
                          f"new_kernel_name={cv['new_kernel_name']}, "
                          f"tb_len={len(cv['testbench'])}")
        with concurrent.futures.ThreadPoolExecutor() as executor:
            debug_print(debug, "Identification")
            identification_future = tools.identifying.identify_non_synthesizable_items(
                cv, knowledge_db_path, embedding_model, enable_rag, hetero_enabled, reset_knowledge_db, executor, debug, llm_config
            )
            cv["identified_items"], cv["items_hetero"] = identification_future.result()
    else:
        # If hidden TB eval is enabled, ensure golden hidden TB is available before TB gen
        # so we can propagate its sig spec into public TB generation.
        hidden_sig_spec_for_public: Optional[str] = None
        pinned_hls_decl_for_public: Optional[str] = None
        if enable_hidden_tb_eval:
            debug_print(debug, "Loading/generating golden hidden TB")
            golden = tools.tb_optimizer.make_golden_hidden_tb(
                orig_code=cv["orig_code"],
                kernel_name=cv["kernel_name"],
                M=hidden_tb_trajectories,
                K=hidden_tb_rounds,
                target_pct=hidden_tb_target,
                llm_config=llm_config,
                cache_dir=golden_tb_cache_dir,
                cache_key=golden_tb_cache_key,
            )
            hidden_sig_spec_for_public = golden.get("hidden_sig_spec")
            pinned_hls_decl_for_public = golden.get("hidden_hls_decl_verbatim")
            debug_print(debug, f"Hidden TB cov={golden.get('hidden_cov')}, sig_spec_len={len(hidden_sig_spec_for_public or '')}, pinned_decl_len={len(pinned_hls_decl_for_public or '')}")

        with concurrent.futures.ThreadPoolExecutor() as executor:
            debug_print(debug, "Testbench Generation")
            if enable_tb_coverage_loop:
                tb_future = executor.submit(
                    tools.tb_optimizer.gen_tb_with_coverage,
                    cv, llm_config, public_tb_rounds, public_tb_target, hidden_sig_spec_for_public, pinned_hls_decl_for_public,
                )
            else:
                tb_future = executor.submit(tools.testbench.gen_tb_prior, cv, llm_config)
            debug_print(debug, "Identification")
            identification_future = tools.identifying.identify_non_synthesizable_items(
                cv, knowledge_db_path, embedding_model, enable_rag, hetero_enabled, reset_knowledge_db, executor, debug, llm_config
            )
            cv["testbench"], cv["tb_aligned_instruction"], cv["new_kernel_name"] = tb_future.result()
            cv["identified_items"], cv["items_hetero"] = identification_future.result()
    tools.general.save_context("tbgen_and_identifying", cv, output_dir)

    debug_print(debug, "Planning")
    cv["plan"], cv["plan_hetero"] = tools.planning.generate_plan(
        cv, knowledge_db_path, embedding_model, enable_rag, hetero_enabled, debug, llm_config
    )
    tools.general.save_context("planning", cv, output_dir)

    debug_print(debug, "Refactoring")
    cv["curr_code"], cv["code_for_hetero"] = tools.refactoring.refactor_code(cv, hetero_enabled, llm_config)
    tools.general.save_context("refactoring", cv, output_dir)

    debug_print(debug, "Synthesis & Simulation & Iteration")
    retry_count = 0
    if hetero_enabled:
        if tools.heterorefactor.call_heterorefactor(output_dir, ContextVariables(data={"curr_code": cv["code_for_hetero"]})):
            with open(os.path.join(output_dir, "refactored_code.cpp"), "r", encoding="utf-8") as f:
                cv["code_for_hetero"] = f.read()
    while retry_count <= max_retry_attempts:
        if remote:
            kill_other, first_task, first_res, second_task, second_res = tools.general.csynth_and_csim_remote(cv, retry_count == 0)
        else:
            kill_other, first_task, first_res, second_task, second_res = tools.general.csynth_and_csim(
                output_dir,
                cv,
                retry_count == 0,
                testbench_repairer=testbench_repairer,
                max_testbench_repair_attempts=(
                    max_testbench_repair_attempts
                    if enable_testbench_repair
                    else 0
                ),
                budget=budget,
            )
        if kill_other and (not first_task) and (not second_task):
            status = "succeeded by hetero"
            cv["hetero"] = "succeeded by hetero"
            error_msg = ""
            failed_task = None
        elif first_res[0] == "succeeded" and second_res[0] == "succeeded":
            status = "succeeded"
            error_msg = ""
            failed_task = None
        else:
            status = first_res[0] if kill_other else second_res[0]
            error_msg = first_res[1] if kill_other else second_res[1]
            cv["csynth_error_msg"] = error_msg
            failed_task = first_task if kill_other else second_task
        cv["csynth_csim_history"].append({
            "status": status,
            "testbench_code": cv["testbench"],
            "refactored_code": cv["curr_code"],
            "code_for_hetero": cv["code_for_hetero"],
            "error_msg": error_msg,
            "testbench_preflight": cv.get("testbench_preflight"),
            "testbench_repair": cv.get("testbench_repair"),
        })
        if enable_rag_update:
            knowledge_manager = KnowledgeManager(
                db_path=knowledge_db_path,
                embedding_model=embedding_model,
                reset_db=reset_knowledge_db,
                debug=debug,
                llm_config=llm_config
            )
            trial_id = knowledge_manager.record_trial_outcome(
                context_variables=cv,
                synthesis_status=status
            )
            debug_print(debug, f"Recorded trial outcome: {trial_id}")
            stats = knowledge_manager.get_database_stats()
            debug_print(debug, f"Knowledge DB stats: {stats}")
        if status == "succeeded" or (status == "succeeded by hetero" and hetero_enabled):
            break
        if tools.general.is_terminal_validation_failure(failed_task):
            debug_print(
                debug,
                "Stopping kernel retry: testbench preflight must be "
                "repaired independently",
            )
            break
        retry_count += 1
        if retry_count > max_retry_attempts:
            break
        tools.general.try_fixing(cv, failed_task, status, error_msg, llm_config)
        tools.general.save_context(f"csynth_csim_iteration_{retry_count}", cv, output_dir)

    # Hidden TB eval gate (eval-only; never triggers extra refactor work)
    final_succeeded = (
        status == "succeeded"
        or (status == "succeeded by hetero" and hetero_enabled)
    )
    if enable_hidden_tb_eval and final_succeeded:
        debug_print(debug, "Hidden TB eval")
        golden = tools.tb_optimizer.make_golden_hidden_tb(
            orig_code=cv["orig_code"],
            kernel_name=cv["kernel_name"],
            M=hidden_tb_trajectories,
            K=hidden_tb_rounds,
            target_pct=hidden_tb_target,
            llm_config=llm_config,
            cache_dir=golden_tb_cache_dir,
            cache_key=golden_tb_cache_key,  # ← FIX: must use same cache_key as first call
        )
        hidden_eval_dir = os.path.join(output_dir, "hidden_eval")
        eval_result = tools.tb_hidden_eval.eval_against_hidden_tb(
            orig_code=cv["orig_code"],
            refactor_code=cv["curr_code"],
            hidden_tb=golden["hidden_tb"],
            work_dir=hidden_eval_dir,
        )
        cv["pass_hidden"] = eval_result["passed"]
        cv["hidden_failure_kind"] = eval_result["failure_kind"]
        cv["cov_hidden"] = eval_result["cov_hidden"]
        cv["hidden_lines_total"] = eval_result["lines_total"]
        cv["hidden_lines_hit"] = eval_result["lines_hit"]
        if cv.get("csynth_csim_history"):
            cv["csynth_csim_history"][-1]["pass_hidden"] = eval_result["passed"]
            cv["csynth_csim_history"][-1]["hidden_failure_kind"] = eval_result["failure_kind"]
            cv["csynth_csim_history"][-1]["cov_hidden"] = eval_result["cov_hidden"]
        debug_print(
            debug,
            f"HIDDEN_EVAL: passed={eval_result['passed']} kind={eval_result['failure_kind']} cov={eval_result['cov_hidden']}",
        )
        print(f"PASS_HIDDEN:{int(eval_result['passed'])}")
        print(f"HIDDEN_FAILURE_KIND:{eval_result['failure_kind']}")

    tools.general.save_context("final", cv, output_dir)

    # Output retry count for capture by parallel_kernel.py
    print(f"RETRY_COUNT:{retry_count}")

    return final_succeeded, cv
    
def main():
    parser = argparse.ArgumentParser(description="Run HLS refactoring with RAG.")
    parser.add_argument("--kernel_path", type=str, help="Path to the kernel source code file.")
    parser.add_argument("--kernel_name", type=str, help="Name of the kernel function.")
    parser.add_argument("--debug", action="store_true", help="Enable debug output.")
    parser.add_argument("--output_dir", type=str, default=None, help="Isolated working directory (optional)")
    parser.add_argument("--knowledge_db_path", type=str, default="./knowledge_db/tmp_db", help="Path to knowledge database")
    parser.add_argument("--embedding_model", type=str, default="all-MiniLM-L6-v2", help="Embedding model name")
    parser.add_argument("--enable_rag", action="store_true", default=False, help="Enable RAG retrieval")
    parser.add_argument("--enable_rag_update", action="store_true", default=False, help="Enable RAG update")
    parser.add_argument("--reset_knowledge_db", action="store_true", default=False, help="Reset knowledge database")
    parser.add_argument("--max_retry_attempts", type=int, default=MAX_RETRY_ATTEMPTS, help="Maximum retry attempts")
    parser.add_argument("--hetero_enabled", action="store_true", default=False, help="Enable heterogeneous programming")
    parser.add_argument("--model", type=str, default=None, help="Override LLM model for all agents")
    parser.add_argument("--remote", action="store_true", default=False, help="Use remote HLS server for csynth/csim")
    parser.add_argument("--reasoning_effort", type=str, default=None, help="Reasoning effort (low/medium/high)")
    parser.add_argument("--base_url", type=str, default=None, help="Base URL for local serving endpoint")
    parser.add_argument("--enable_testbench_repair", action="store_true", default=False, help="Enable bounded testbench-only repair before synthesis")
    parser.add_argument("--max_testbench_repair_attempts", type=int, default=2, help="Independent testbench repair budget")
    parser.add_argument("--testbench_repair_model", type=str, default=None, help="Optional dedicated model for testbench repair; defaults to --model")
    parser.add_argument("--testbench_repair_api_key_env", type=str, default="OPENAI_API_KEY", help="Environment variable containing the testbench repair API key")
    parser.add_argument("--external_testbench_file", type=str, default=None, help="Path to external testbench file (bypass TB generation)")
    parser.add_argument("--external_tb_instruction", type=str, default=None, help="TB-aligned instruction for external testbench")
    parser.add_argument("--external_kernel_name", type=str, default=None, help="HLS top function name for external testbench")
    # Coverage-optimized public TB loop (default off → behavior unchanged)
    parser.add_argument("--enable_tb_coverage_loop", action="store_true", default=False, help="Enable iterative coverage-optimized public TB generation")
    parser.add_argument("--public_tb_rounds", type=int, default=3, help="K: max iterative rounds for public TB (used iff --enable_tb_coverage_loop)")
    parser.add_argument("--public_tb_target", type=float, default=80.0, help="Public TB early-stop coverage target in percent")
    # Hidden TB eval gate (default off → behavior unchanged)
    parser.add_argument("--enable_hidden_tb_eval", action="store_true", default=False, help="Enable post-csim hidden TB eval against a golden coverage-optimized TB")
    parser.add_argument("--hidden_tb_rounds", type=int, default=6, help="K rounds per trajectory for golden hidden TB (used iff --enable_hidden_tb_eval)")
    parser.add_argument("--hidden_tb_trajectories", type=int, default=3, help="M parallel trajectories for golden hidden TB")
    parser.add_argument("--hidden_tb_target", type=float, default=90.0, help="Hidden TB early-stop coverage target in percent")
    parser.add_argument("--golden_tb_cache_dir", type=str, default=None, help="Directory to cache golden hidden TB JSON per kernel; if unset, regenerate each time")
    parser.add_argument("--golden_tb_cache_key", type=str, default=None, help="Cache filename key (e.g., kernel_name_suffix) to avoid collisions when multiple kernels share the same function name; defaults to --kernel_name")
    parser.add_argument("--use_cached_tb_as_public", action="store_true", default=False,
                        help="Use the cached hidden TB (from --golden_tb_cache_dir/--golden_tb_cache_key) directly as the agent's testbench, skipping TB generation entirely. Requires both --golden_tb_cache_dir and --golden_tb_cache_key.")
    args = parser.parse_args()
    success, _ = hls_refactor_with_rag(
        kernel_path=args.kernel_path,
        kernel_name=args.kernel_name,
        knowledge_db_path=args.knowledge_db_path,
        embedding_model=args.embedding_model,
        enable_rag=args.enable_rag,
        enable_rag_update=args.enable_rag_update,
        reset_knowledge_db=args.reset_knowledge_db,
        output_dir=args.output_dir,
        max_retry_attempts=args.max_retry_attempts,
        hetero_enabled=args.hetero_enabled,
        debug=1 if args.debug else 0,
        model=args.model,
        remote=args.remote,
        reasoning_effort=args.reasoning_effort,
        base_url=args.base_url,
        enable_testbench_repair=args.enable_testbench_repair,
        max_testbench_repair_attempts=(
            args.max_testbench_repair_attempts
        ),
        testbench_repair_model=args.testbench_repair_model,
        testbench_repair_api_key_env=(
            args.testbench_repair_api_key_env
        ),
        external_testbench=open(args.external_testbench_file).read() if args.external_testbench_file else None,
        external_tb_instruction=args.external_tb_instruction,
        external_kernel_name=args.external_kernel_name,
        enable_tb_coverage_loop=args.enable_tb_coverage_loop,
        public_tb_rounds=args.public_tb_rounds,
        public_tb_target=args.public_tb_target,
        enable_hidden_tb_eval=args.enable_hidden_tb_eval,
        hidden_tb_rounds=args.hidden_tb_rounds,
        hidden_tb_trajectories=args.hidden_tb_trajectories,
        hidden_tb_target=args.hidden_tb_target,
        golden_tb_cache_dir=args.golden_tb_cache_dir,
        golden_tb_cache_key=args.golden_tb_cache_key,
        use_cached_tb_as_public=args.use_cached_tb_as_public,
    )
    if success:
        print("HLS refactoring with RAG completed successfully.")
        print_agrefactorpp_usage_summary()
        return 0
    else:
        print("HLS refactoring with RAG failed after maximum retry attempts.")
        print_agrefactorpp_usage_summary()
        return 1

if __name__ == "__main__":
    import sys
    exit_code = main()
    sys.exit(exit_code)