# AgRefactor++ Current Project State

> **当前状态唯一入口。** 历史 package 状态只保留在对应 acceptance、audit 和 history 文件中，不再复制到本文。

## 1. 当前快照

```text
branch=stage2-general-feedback
baseline_before_this_package=c881dea0ea68f0fbf7c2b359bd270f362827a90f
latest_deterministic_regression=1823/1823
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
stage3_s34_real_structural_smoke=accepted
stage3_s34_real_structural_smoke_claim_scope=structural_model_contract_only
stage3_s35_real_bottleneck_smoke=accepted
stage3_s35_real_bottleneck_smoke_claim_scope=bottleneck_model_contract_only
stage3_s32_real_replay=accepted
stage3_s32_real_replay_artifact_root=/data/agrefactor_runs/stage3_s32_real_replay_20260730T153256Z_2390707
NEXT_STEP=STAGE3_IMPLEMENTATION_STEP_6
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
- typed agent-safe PPA projection、非权威 Bottleneck classification、evidence-linked hypothesis 与 per-level dispatch。

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

- 真实 model-backed Structural/Bottleneck hypothesis/source contract 等于已通过 correctness、综合或 PPA；
- 产品 `optimize/full` 已解除门禁；
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

S3.1–S3.5 已验收：当前具备 candidate/checkpoint foundation、独立 Stage 3 qualification、typed PPA evidence、latency comparator、exact validation cache identity、完整 deterministic safe-v1 level/round/budget/rollback/resume 状态机，以及 real model-backed Structural 和 Bottleneck hypothesis/complete-source contracts。S3.5 新增 typed agent-safe PPA projection、明确非权威的 model classification、unknown 安全退化和 evidence-linked rewrite；bounded smoke 只证明两次真实模型调用的合同闭环，不等于候选正确、已综合、PPA 改善、产品 `optimize/full` 或多 kernel 优化。下一包严格为 S3.6 Pragma Model Integration。

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
14. [最新真实产品 smoke acceptance](../acceptance/pre-stage3/POST_CLI_REAL_SMOKE_ACCEPTANCE.md)

## 7. 工程原则

- 通用项目命名，不引入赛事绑定词；
- correctness first；
- Hidden evidence 不进入模型 Prompt、普通结果或普通 trace；
- 不用 deterministic tests 冒充真实工具验收；
- 只实现有当前 consumer 的 schema、registry 和 budget；
- Stage 3 每个包先冻结范围、再实现、再做确定性与真实证据验收。
