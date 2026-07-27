# Post-CLI Real Source-Only Smoke Acceptance

## Purpose

验证 CLI 参数合同提交 `f80803af65b18015bb0801c05964a6c5c2a83d52` 之后，当前普通 source-only 产品入口仍能完成一次真实模型与 Vitis HLS 2023.2 accepted run。

## Command Contract

```text
command=python -m agrefactor.cli refactor
source=src/heterorefactor/dfs/kernel.cpp
top=process_top
model=deepseek-v4-flash
reasoning_option_omitted=true
effective_cli_default=medium
test_generation_profile=lightweight
public_tests=auto
hidden_tests=auto
max_testbench_repairs=3
max_candidate_repairs=3
max_llm_calls=32
max_tool_calls=64
max_compile_calls=24
max_csim_calls=12
max_csynth_calls=6
max_wall_time_s=7200
output_dir=explicit_exact_artifact_root
```

`--reasoning-effort`、`--csim-timeout-s` 和 `--csynth-timeout-s` 未显式提供，用于实际经过当前默认解析路径。

## Result

```text
run_id=post-cli-real-smoke-20260726_192331
started_at_utc=2026-07-26T19:23:35Z
finished_at_utc=2026-07-26T19:26:11Z
repository_commit=f80803af65b18015bb0801c05964a6c5c2a83d52
repository_clean=true
product_summary_status=accepted
unified_run_status=succeeded
final_phase_accepted=true
execution_identity_status=succeeded
csynth=passed
public=passed
hidden=passed
model=deepseek-v4-flash
model_prompt_calls=15
llm_calls=15
tool_calls=13
compile_calls=6
csim_calls=3
csynth_calls=2
actual_vitis_version=2023.2
execution_identity_accepted_ready=true
status_semantics=product accepted; UnifiedRunner and Execution Identity succeeded
credential_leak=false
hidden_model_boundary_exposure=false
artifact_root=/data/agrefactor_runs/post_cli_real_smoke_20260726_192331/artifacts
work_root=/data/agrefactor_work/source_run_post-cli-real-smoke-20260726_192331
```

## Required Artifacts

```text
full_result.json
execution_identity.json
run_artifact_manifest.json
model_calls.json
tool_calls.json
stdout.log
stderr.log
bootstrap/source_request.json
bootstrap/model_data_boundary.json
refactor/final_candidate.cpp
```

## Scope

该验收只证明：

- 当前 CLI 后代码基线；
-指定 DFS kernel；
-指定 DeepSeek 模型；
- Vitis HLS 2023.2；
-记录的预算和 TargetProfile；
-一次 accepted source-only run。

它不证明任意 kernel、任意模型、任意器件/版本或稳定优化收益。

```text
MODEL_API_CALLED=true
REAL_VITIS_RUN=true
PRE_STAGE3_CLOSED=true
STAGE3_PLANNING_FROZEN=true
STAGE3_IMPLEMENTATION_STARTED=false
```

## Status Semantics

The product summary uses `accepted` only when the unified run succeeded and the
final formal phase recorded `metadata.accepted=true`. The shared
`UnifiedRunner` terminal vocabulary is `succeeded|failed|error`, so final
Execution Identity records `execution_status=succeeded`. `accepted_ready=true`
proves that the successful run also contains the required model, toolchain,
suite, candidate and budget evidence.
