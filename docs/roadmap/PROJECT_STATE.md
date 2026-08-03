# AgRefactor++ Current Project State

> **当前状态唯一入口。** 历史 package 状态只保留在对应 acceptance、audit 和 history 文件中，不再复制到本文。

## 1. 当前快照

```text
branch=stage2-general-feedback
baseline_before_this_package=197327af79382327f2711119225d47e8ea060e00
latest_deterministic_regression=2007/2007_expected_after_S38_V2_payload
post_cli_real_smoke=accepted
post_cli_real_smoke_run_id=post-cli-real-smoke-20260726_192331
post_cli_real_smoke_artifact_root=/data/agrefactor_runs/post_cli_real_smoke_20260726_192331/artifacts
PRE_STAGE3_CLOSED=true
STAGE3_PLANNING_FROZEN=true
STAGE3_IMPLEMENTATION_ALLOWED=true
STAGE3_IMPLEMENTATION_STARTED=true
STAGE3_S3_1_CANDIDATE_STATE_FOUNDATION=accepted
STAGE3_S3_2_QUALIFICATION_AND_PPA_EVIDENCE=accepted
STAGE3_S3_3_DETERMINISTIC_OPTIMIZER_STATE_MACHINE=accepted
STAGE3_S3_4_STRUCTURAL_MODEL_INTEGRATION=accepted
STAGE3_S3_5_BOTTLENECK_MODEL_INTEGRATION=accepted
STAGE3_S3_6_PRAGMA_MODEL_INTEGRATION=accepted
STAGE3_S3_7_PRODUCT_ADAPTERS=accepted
STAGE3_S3_8_EVALUATION=accepted_only_after_corrected_legacy_matrix
stage3_s34_real_structural_smoke=accepted
stage3_s34_real_structural_smoke_claim_scope=structural_model_contract_only
stage3_s35_real_bottleneck_smoke=accepted
stage3_s35_real_bottleneck_smoke_claim_scope=bottleneck_model_contract_only
stage3_s36_real_pragma_smoke=accepted
stage3_s36_real_pragma_smoke_claim_scope=pragma_model_contract_only
stage3_s32_real_replay=accepted
stage3_s32_real_replay_artifact_root=/data/agrefactor_runs/stage3_s32_real_replay_20260730T153256Z_2390707
NEXT_STEP=STAGE3_S3_8_LEGACY_CORRECTION_MATRIX
```

最终提交 SHA 以 `stage2-general-feedback` 当前 HEAD 为准；本文不复制会因自身提交而立刻过期的最终 SHA。

## 2. 当前已验收能力

### 普通产品入口

```bash
python -m agrefactor.cli refactor \
  SOURCE \
  --top TOP \
  --model MODEL
```

已完成：

- source-only 输入；
- 用户固定模型；
-静态 ModelFamilyProfile；
- TargetProfile 真实下传；
- Public/Hidden 独立来源与多 suite；
- 自动 Testbench 生成和有限 repair；
- Candidate 生成、有限 repair 和正式 Stage 2 裁决；
- Preflight、Vitis HLS CSYNTH、Public CSIM、Hidden CSIM；
- 共享 BudgetManager、TraceRecorder 和 Execution Identity；
- 默认简洁输出和完整 artifacts；
- 当前 CLI 参数合同及真实 post-CLI smoke。

### 共享基础设施

- `TaskSpec`、`TargetProfile`、`RunMode`；
- Provider-neutral Model Registry 和 OpenAI-compatible Provider；
- 结构化 Feedback、Router、State Machine 和 Validation Orchestrator；
- Shared Layered Prompt Builder；
- Candidate/Testbench repair contracts；
- LLM、Tool、Compile、CSIM、CSYNTH 和 wall-time 硬预算；
- Token/Cost observed-only 记录；
- 真实工具调用和 suite provenance；
- operator-full / agent-safe / Hidden suppression 边界；
- Structural optimization layered Prompt、strict hypothesis JSON、complete-source model contract；
- real model call safe audit、shared LLM budget accounting 与显式 qualification adapter boundary；
- typed agent-safe PPA projection、非权威 Bottleneck classification、evidence-linked hypothesis、typed non-authoritative Pragma action 与完整三层 dispatch。

## 3. 最新真实 smoke

```text
run_id=post-cli-real-smoke-20260726_192331
model=deepseek-v4-flash
source=src/heterorefactor/dfs/kernel.cpp
top=process_top
status=accepted
csynth=passed
public=passed
hidden=passed
llm_calls=15
tool_calls=13
compile_calls=6
csim_calls=3
csynth_calls=2
actual_vitis_version=2023.2
repository_commit=f80803af65b18015bb0801c05964a6c5c2a83d52
repository_clean=true
artifact_root=/data/agrefactor_runs/post_cli_real_smoke_20260726_192331/artifacts
```

该 smoke 证明最新 CLI 参数改造后的当前代码基线仍能通过一次真实产品入口。它不是任意 kernel、模型、器件或版本的普适声明。

## 4. 当前不能宣称

- 真实 model-backed Structural/Bottleneck/Pragma hypothesis/source contract 等于已通过 correctness、综合或 PPA；
- 产品 `optimize/full` 已解除门禁，但尚无多 kernel、重复实验或稳定 PPA 收益结论；
- 任意 Vitis 版本或器件支持；
- 稳定模型修复/优化成功率；
- Legacy RAG 等于 Memory Applicability Gate；
- TaskSpec version 字段等于真实版本迁移；
- 单次 PPA 改善等于稳定优化收益；
- deterministic tests 等于真实 kernel 数量。

## 5. Stage 3 开始边界

Stage 3 规划合同已经冻结：

- [Stage 3 Frozen Implementation Contract](STAGE3_IMPLEMENTATION_CONTRACT.md)
- [Stage 3 High-Level Design](STAGE3_SAFE_OPTIMIZER.md)

已经验收的第一个实现包是：

```text
candidate state/schema
+ checkpoint persistence
+ best_correct baseline semantics
+ deterministic tests
```

S3.1–S3.7 已验收：当前具备 candidate/checkpoint foundation、独立 Stage 3 qualification、typed PPA evidence、latency comparator、exact validation cache identity、完整 deterministic safe-v1 level/round/budget/rollback/resume 状态机、real model-backed Structural/Bottleneck/Pragma contracts，以及普通 `optimize/full` Product Adapters。S3.7 强制 direct optimize 独立 reference 与 provided Public/Hidden、full accepted-refactor handoff、baseline-before-model qualification、shared budget/trace、linked Stage 3 execution identity。内部真实 gate 必须触达三层 analysis；rewrite 由 executable hypothesis 与 complete-source contract 决定，可 qualification 或 typed no-retry abstention。模型合同失败不再摧毁已有 best_correct；网络/工具/文件系统错误仍硬失败。S3.8 已实现冻结的 3-kernel × 2-repeat × 3-arm 评测器；只有目标主机完整矩阵通过后才保留并关闭 Stage 3。

<!-- PRE_STAGE4_PRODUCT_VALIDATION_HARDENING:BEGIN -->
## 5.1 Stage 4 entry blocker: frozen hardening contract

The intended Stage 4 entry is now gated by
[`PRE_STAGE4_PRODUCT_VALIDATION_HARDENING_CONTRACT.md`](PRE_STAGE4_PRODUCT_VALIDATION_HARDENING_CONTRACT.md).

Frozen targets include:

```text
default deepseek-v4-flash with user model/endpoint/API-key-env override
local .env loading without secret persistence
explicit DeepSeek Thinking with role-based medium/high mapping
typed component-level Preflight ownership
Public native Vitis CSIM → CSYNTH → Public RTL COSIM → Hidden
refactor/optimize/full budget profiles with Full Optimize reserves
truthful per-command CLI parameters
bottleneck-driven dynamic-v1 optimization
```

Current status at document freeze:

```text
PRE_STAGE4_HARDENING_DESIGN_FROZEN=true
PRE_STAGE4_HARDENING_IMPLEMENTATION_COMPLETE=false
P4_0B_TYPED_PREFLIGHT=accepted_local_validation
P4_0B_R_DESIGN_FROZEN=true
STAGE4_ALLOWED=false
NEXT_IMPLEMENTATION_PACKAGE=P4-0C_PUBLIC_NATIVE_VITIS_CSIM
```
<!-- PRE_STAGE4_PRODUCT_VALIDATION_HARDENING:END -->

<!-- PRE_STAGE4_REAL_VALIDATION_SCHEDULE:BEGIN -->
## 5.2 Frozen real-tool and network-model validation cadence

The authoritative cadence is frozen in
[`PRE_STAGE4_REAL_VALIDATION_SCHEDULE_DECISION.md`](PRE_STAGE4_REAL_VALIDATION_SCHEDULE_DECISION.md)
and section 8.1 of the
[Pre-Stage-4 hardening contract](PRE_STAGE4_PRODUCT_VALIDATION_HARDENING_CONTRACT.md).

```text
PRE_STAGE4_REAL_VALIDATION_SCHEDULE_FROZEN=true
P4_0C_REAL_NATIVE_VITIS_CSIM_REQUIRED=true
P4_0C_NETWORK_LLM_ACCEPTANCE_DEPENDENCY=false
P4_0D_REAL_CSIM_CSYNTH_COSIM_HIDDEN_REQUIRED=true
P4_0D_NETWORK_LLM_ACCEPTANCE_DEPENDENCY=false
P4_0E_FIRST_POST_HARDENING_NETWORK_LLM_SMOKE=true
P4_0F_MEASURED_REAL_RUNS_FOR_BUDGET_DEFAULTS=true
P4_0G_NETWORK_LLM_OPTIMIZE_FULL_SMOKE=true
P4_0H_FORMAL_MULTI_KERNEL_NETWORK_LLM_VITIS_REVALIDATION=true
P4_0H_AUTHORITATIVE_PRE_STAGE4_REAL_EVIDENCE_MATRIX=true
DETERMINISTIC_TESTS_DO_NOT_EQUAL_REAL_END_TO_END=true
HISTORICAL_STAGE3_REAL_SMOKE_DOES_NOT_PROVE_NEW_PIPELINE=true
NEXT_IMPLEMENTATION_PACKAGE=P4-0C_PUBLIC_NATIVE_VITIS_CSIM
```

P4-0C and P4-0D isolate and establish the real validation toolchain before
stochastic model output becomes an acceptance dependency. P4-0E introduces the
first new-baseline network-provider smoke; P4-0G exercises real model-backed
Optimize/Full behavior; P4-0H is the repeated multi-kernel closure matrix.
<!-- PRE_STAGE4_REAL_VALIDATION_SCHEDULE:END -->

## 6. 当前权威文档

1. [ROADMAP.md](ROADMAP.md)
2. [GOAL_TRACEABILITY.md](GOAL_TRACEABILITY.md)
3. [STAGE3_IMPLEMENTATION_CONTRACT.md](STAGE3_IMPLEMENTATION_CONTRACT.md)
4. [CLI_PARAMETER_REFERENCE.md](../guides/CLI_PARAMETER_REFERENCE.md)
5. [REPRODUCTION_STATUS.md](../guides/REPRODUCTION_STATUS.md)
6. [S3.1 Candidate State Foundation acceptance](../acceptance/stage3/stage3_s31_candidate_state_foundation_acceptance.md)
7. [S3.2 Qualification and PPA Evidence acceptance](../acceptance/stage3/stage3_s32_qualification_ppa_acceptance.md)
8. [S3.3 Deterministic Optimizer State Machine acceptance](../acceptance/stage3/stage3_s33_deterministic_optimizer_state_machine_acceptance.md)
9. [S3.3 decision record](STAGE3_S33_DECISION_RECORD.md)
10. [S3.4 Structural Model Integration acceptance](../acceptance/stage3/stage3_s34_structural_model_integration_acceptance.md)
11. [S3.4 decision record](STAGE3_S34_DECISION_RECORD.md)
12. [S3.5 Bottleneck Model Integration acceptance](../acceptance/stage3/stage3_s35_bottleneck_model_integration_acceptance.md)
13. [S3.5 decision record](STAGE3_S35_DECISION_RECORD.md)
14. [S3.6 Pragma Model Integration acceptance](../acceptance/stage3/stage3_s36_pragma_model_integration_acceptance.md)
15. [S3.6 decision record](STAGE3_S36_DECISION_RECORD.md)
16. [S3.7 Product Adapters acceptance](../acceptance/stage3/stage3_s37_product_adapters_acceptance.md)
17. [S3.7 decision record](STAGE3_S37_DECISION_RECORD.md)
18. [S3.8 evaluation acceptance](../acceptance/stage3/stage3_s38_evaluation_acceptance.md)
19. [S3.8 decision record](STAGE3_S38_DECISION_RECORD.md)
18. [最新真实产品 smoke acceptance](../acceptance/pre-stage3/POST_CLI_REAL_SMOKE_ACCEPTANCE.md)

## 7. 工程原则

- 通用项目命名，不引入赛事绑定词；
- correctness first；
- Hidden evidence 不进入模型 Prompt、普通结果或普通 trace；
- 不用 deterministic tests 冒充真实工具验收；
- 只实现有当前 consumer 的 schema、registry 和 budget；
- Stage 3 每个包先冻结范围、再实现、再做确定性与真实证据验收。

S3.7 v8 hardening: all three model levels use typed no-retry analysis/rewrite abstention with safe reason codes; acceptance correlates calls, decisions, candidates and qualification rather than requiring stochastic rewrites; product qualification authority is unchanged.

S3.7 v9 observer correction: acceptance reads the canonical versioned candidate-index artifact through the product parser; real fixtures use the same serializer and obsolete flat fixtures are rejected.

S3.7 closure hygiene: the status header now explicitly marks Product Adapters accepted, the acceptance package label is V9, and safe model-call artifacts write schema v2 with documented v1/v2 read compatibility. Product behavior and the S3.8 next-package boundary are unchanged.


S3.8 implementation boundary: the frozen target-host matrix is 3 committed
kernel categories × 2 repeats × direct optimize/live source-only full/Legacy
simple_iter. Legacy inputs and outputs are independently qualified, all arms
share model/Target/budget/provider parameters, and no stable-superiority claim
is permitted. The repository payload is retained only after the complete matrix
has zero infrastructure failures and exercises real Vitis on every kernel.

## S3.8 V2 correction state

The first 18-record target-host run is retained as diagnostic evidence, not
Stage 3 closure: all six `simple-iter` records were observer RuntimeErrors before
Legacy model execution. The twelve `safe-optimize`/`source-full` records remain
valid and immutable. V2 must rerun only the six Legacy units under the same
protocol identity and pass the strengthened fair-comparison gate.

<!-- PRE_STAGE4_P4_0B_TYPED_PREFLIGHT:BEGIN -->
## P4-0B typed Preflight accepted

```text
BASE_COMMIT=11df86f199b8da03ed83baf9119841b3610cdad4
P4_0B_TYPED_PREFLIGHT_IMPLEMENTED=true
P4_0B_TYPED_PREFLIGHT_ACCEPTANCE=accepted_local_validation
P4_0B_FOCUSED_TESTS=64
P4_0B_FULL_REGRESSION_TESTS=2044
STAGE4_ALLOWED=false
NEXT_IMPLEMENTATION_PACKAGE=P4-0C_PUBLIC_NATIVE_VITIS_CSIM
```

P4-0B replaces mixed compile/link ownership guessing with independent
Testbench/reference/Candidate compilation, object symbol checks, component LTO
interface probes, final link, typed reasons, unknown-safe routing, and physical
staged budget accounting. See the
[P4-0B decision record](PRE_STAGE4_P4_0B_DECISION_RECORD.md) and
[acceptance](../acceptance/pre-stage4/p4_0b_typed_preflight_acceptance.md).
<!-- PRE_STAGE4_P4_0B_TYPED_PREFLIGHT:END -->

<!-- PRE_STAGE4_P4_0B_R_CONTRACT:BEGIN -->
## P4-0B-R bounded Optimize Candidate recovery accepted

```text
P4_0B_R_DESIGN_FROZEN=true
P4_0B_R_IMPLEMENTED=true
P4_0B_R_R1_PREFLIGHT_RECOVERY=accepted
P4_0B_R_R2_CSYNTH_LEGALITY_RECOVERY=accepted
MAX_REPAIRS_PER_ROOT_OPTIMIZE_CANDIDATE=1
PUBLIC_CSIM_REPAIR=false
HIDDEN_REPAIR=false
PPA_REPAIR=false
P4_0B_R_FOCUSED_TESTS=22
P4_0B_R_FULL_REGRESSION=2066
STAGE4_ALLOWED=false
NEXT_IMPLEMENTATION_PACKAGE=P4-0C_PUBLIC_NATIVE_VITIS_CSIM
```

The implementation retains failed Candidate records, creates a new contiguous
repair descendant, restarts complete Optimize qualification, uses one shared
BudgetManager and changes `best_correct` only after full qualification and PPA
comparison.
<!-- PRE_STAGE4_P4_0B_R_CONTRACT:END -->

<!-- PRE_STAGE4_P4_0C_NATIVE_VITIS_CSIM:BEGIN -->
## P4-0C Public native Vitis CSIM accepted

```text
P4_0C_PUBLIC_NATIVE_VITIS_CSIM_IMPLEMENTED=true
P4_0C_PUBLIC_NATIVE_VITIS_CSIM_ACCEPTANCE=accepted_real_vitis
P4_0C_UNIFIED_STAGE_ORDER=preflight_public_native_csim_csynth_hidden
P4_0C_PUBLIC_BACKEND=native_vitis
P4_0C_HIDDEN_BACKEND=host_differential
P4_0C_NETWORK_LLM_USED=false
P4_0C_FOCUSED_TESTS=23
P4_0C_FULL_REGRESSION=2089
P4_0C_CACHE_PIPELINE=prestage4-native-vitis-csim-v1
P4_0C_CANDIDATE_REPAIR_PREFIX=task_aware
P4_0C_STAGE2_SMOKE_ORDER=preflight_public_csynth_hidden
P4_0C_STAGE2_SMOKE_BUDGET=5_tool_2_compile_1_csynth_2_csim
STAGE4_ALLOWED=false
NEXT_IMPLEMENTATION_PACKAGE=P4-0D_PUBLIC_RTL_COSIM
```

The accepted real smoke uses a committed model-independent sample and
actual Vitis HLS `csim_design`. It proves the new Public tool stage and
ordering only; it is not a network-model, arbitrary-kernel or
stable-optimization claim.
<!-- PRE_STAGE4_P4_0C_NATIVE_VITIS_CSIM:END -->
