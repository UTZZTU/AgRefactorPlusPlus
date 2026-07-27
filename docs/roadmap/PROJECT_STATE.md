# AgRefactor++ Current Project State

> **当前状态唯一入口。** 历史 package 状态只保留在对应 acceptance、audit 和 history 文件中，不再复制到本文。

## 1. 当前快照

```text
branch=stage2-general-feedback
baseline_before_this_package=f80803af65b18015bb0801c05964a6c5c2a83d52
latest_deterministic_regression=1500/1500
post_cli_real_smoke=accepted
post_cli_real_smoke_run_id=post-cli-real-smoke-20260726_192331
post_cli_real_smoke_artifact_root=/data/agrefactor_runs/post_cli_real_smoke_20260726_192331/artifacts
PRE_STAGE3_CLOSED=true
STAGE3_PLANNING_FROZEN=true
STAGE3_IMPLEMENTATION_ALLOWED=true
STAGE3_IMPLEMENTATION_STARTED=false
NEXT_STEP=STAGE3_IMPLEMENTATION_STEP_1
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
- operator-full / agent-safe / Hidden suppression 边界。

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

- `optimize/full` 已实现；
- Stage 3 安全三级优化器已开始；
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

允许开始的第一个实现包只有：

```text
candidate state/schema
+ checkpoint persistence
+ best_correct baseline semantics
+ deterministic tests
```

不得在第一个包中同时实现模型搜索、三级策略、完整 CLI 和真实多 kernel 优化。

## 6. 当前权威文档

1. [ROADMAP.md](ROADMAP.md)
2. [GOAL_TRACEABILITY.md](GOAL_TRACEABILITY.md)
3. [STAGE3_IMPLEMENTATION_CONTRACT.md](STAGE3_IMPLEMENTATION_CONTRACT.md)
4. [CLI_PARAMETER_REFERENCE.md](../guides/CLI_PARAMETER_REFERENCE.md)
5. [REPRODUCTION_STATUS.md](../guides/REPRODUCTION_STATUS.md)
6. [最新真实 smoke acceptance](../acceptance/pre-stage3/POST_CLI_REAL_SMOKE_ACCEPTANCE.md)

## 7. 工程原则

- 通用项目命名，不引入赛事绑定词；
- correctness first；
- Hidden evidence 不进入模型 Prompt、普通结果或普通 trace；
- 不用 deterministic tests 冒充真实工具验收；
- 只实现有当前 consumer 的 schema、registry 和 budget；
- Stage 3 每个包先冻结范围、再实现、再做确定性与真实证据验收。
