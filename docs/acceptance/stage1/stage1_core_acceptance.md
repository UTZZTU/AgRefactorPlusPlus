# Stage 1 Core Acceptance

## 1. 结论

AgRefactor++ 的 **Stage 1 Core 已完成并可以关闭**。

关闭范围：

```text
TaskSpec / TargetProfile / RunMode
→ UnifiedRunner / RunContext
→ shared BudgetManager
→ TraceRecorder
→ LegacyRefactorAdapter
→ real Testbench Preflight compile
→ real Vitis HLS csynth
→ real local C simulation compile and execute
→ exact hard-budget enforcement and evidence
```

本结论只关闭 Stage 1 Core，不代表以下增强已经完成：

- TargetProfile stable named profiles；
- per-profile executable/settings；
- platform/resource limits/report parser profile；
- effective value provenance；
- 任意 Vitis 版本、器件或 kernel；
- source→target 版本迁移；
- Stage 3 的 LLM 候选搜索、修复与 `best_correct`；
- Stage 4 Memory Applicability Gate。

## 2. 固定代码基线

```text
Branch: stage1-csim-compile-hard-budget
Commit: 6d6f608e402d13827faa837de7e1e8674ecf12b6
Deterministic tests: 204/204 passed
```

## 3. Stage 1 Core 关闭条件

```text
TargetProfile 真正控制一次真实 Vitis run
+
BudgetManager 真正控制真实主流程工具调用
+
至少一个真实代表性 kernel 完成共享预算全链路
```

三项现已全部满足。

### 3.1 TargetProfile

已真实控制：

- `vitis-run` executable；
- requested/actual Vitis version；
- device/part；
- target clock；
- compile flags；
- generated Tcl；
- invocation/effective-profile evidence。

真实环境：

```text
Vitis HLS 2023.2
Device: xcu200-fsgd2104-2-e
```

### 3.2 Tool hard budgets

已实现：

```text
max_tool_calls / tool_calls
max_compile_calls / compile_calls
max_csynth_calls / csynth_calls
max_csim_calls / csim_calls
```

统一契约：

```text
prospective or pre-call hard check
→ allow or block
→ exact-once consume before real launch
→ real subprocess/tool attempt
→ structured evidence
```

### 3.3 真实 DFS 全链路

真实 DFS 原始程序：

```text
src/heterorefactor/dfs/kernel.cpp
Git blob: 8bcc391e648c18620a7b9d0cc6c11f655d379031
Top function: process_top
```

运行目录：

```text
/data/agrefactor_runs/stage1_real_dfs_full_chain_budget_v2_20260716_111358
```

真实链路：

```text
UnifiedRunner
→ LegacyRefactorAdapter
→ one shared BudgetManager
→ real DFS Testbench Preflight with g++
→ real Vitis HLS 2023.2 csynth
→ real csim g++ compile
→ real generated ./csim execute
→ exhausted-budget extra Preflight blocked before g++
```

结果：

```text
RESULT_STATUS=succeeded
REAL_DFS_PREFLIGHT_SUCCEEDED=1
REAL_DFS_VITIS_CSYNTH_SUCCEEDED=1
REAL_DFS_CSIM_SUCCEEDED=1
POST_CHAIN_PREFLIGHT_BLOCKED=1
REAL_DFS_FULL_CHAIN_BUDGET_READY=1
SMOKE_STATUS=0
```

精确预算与使用量：

```text
tool_calls=4
compile_calls=2
csynth_calls=1
csim_calls=1
```

真实综合报告：

```text
real_chain/csynth_111410/csynth/solution/syn/report/
dfs_candidate_process_top_csynth.rpt
```

真实 C simulation 产物：

```text
real_chain/csim_111410/csim
ELF 64-bit x86-64 executable
```

## 4. 本次 DFS 验收的边界

原始代码是真实上游 DFS 文件。

综合和功能对照使用的是确定性 synthesis-safe reference candidate：

```text
fixed-capacity
non-recursive
iterative in-order traversal
```

该候选用于隔离并验证 Stage 1 基础设施。

本次 **没有**：

- 调用 OpenAI、DeepSeek 或其他 LLM API；
- 让智能体自动生成 DFS 重构；
- 验证多轮候选修复；
- 验证 API 预算耗尽后返回 `best_correct`；
- 声明候选是论文方法生成或达到最优 PPA。

允许声明：

> Stage 1 工具执行与预算基础设施已经能够承载真实 DFS 任务。

不允许声明：

> AgRefactor++ 智能体已经通过 API 自动重构 DFS 成功。

后者属于 Stage 3。

## 5. public test 范围决策

保留 public test 的评测语义，但不把它实现为新的物理工具或独立预算资源。

```text
public test = test-suite role and feedback visibility
csim = physical compile and execution mechanism
```

真实执行仍然是：

```text
g++ compile
→ ./csim execute
```

底层继续计数：

```text
compile_calls
csim_calls
tool_calls
```

不新增：

```text
public_test_calls
max_public_test_calls
```

public/hidden 测试应在 Stage 2 定义：

- test split；
- suite identity/version；
- case count/pass count；
- coverage；
- feedback visibility；
- public feedback 可进入 Stage 3 修复提示词；
- hidden details 不向智能体泄露，只用于最终泛化评估。

## 6. cosim 范围决策

原始 AgRefactor 的活跃主流程是：

```text
csynth
→ csim
```

没有 `run_cosim`、`cosim_design` 或 RTL co-simulation phase。

因此：

- cosim 不属于 Stage 1 Core 的完成条件；
- 当前不新增 `cosim_calls/max_cosim_calls`；
- AgRefactor++ 与原项目保持一致；
- 后续若正式加入 RTL co-simulation，再独立设计预算、evidence 和真实验收。

## 7. Stage 1 Core 与 Hardening

### Stage 1 Core：已关闭

- shared execution data structures；
- TargetProfile local execution core；
- UnifiedRunner/legacy integration；
- real compile/csynth/csim；
- hard budgets；
- exact-once accounting；
- structured evidence；
- real DFS shared-budget full-chain acceptance。

### Stage 1 Hardening：后续增强

- stable named profiles；
- per-profile executable/settings；
- platform/resource/parser profiles；
- field-level provenance；
- more versions/devices/kernels；
- stable configuration templates。

Hardening 不阻塞 Stage 2/3 开发，但相关能力在验收前不能对外宣称已支持。

## 8. 下一阶段

```text
Stage 2:
define public/hidden test roles and evidence
→ generalize feedback parsing/state transitions

Stage 3:
real DFS original kernel
→ LLM API generates candidate
→ public tests / Preflight / csynth / csim
→ evidence-driven repair
→ bounded retries and budgets
→ preserve and return best_correct
```

Stage 3 的第一个受控验收应限制 API 和工具调用次数，目标是验证闭环，而不是立即追求最优性能。
