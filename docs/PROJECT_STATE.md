# AgRefactor++ Current Project State

> **新对话或恢复开发时首先阅读本文档。** 权威范围见 [`ROADMAP.md`](ROADMAP.md)，目标追踪见 [`GOAL_TRACEABILITY.md`](GOAL_TRACEABILITY.md)。

## 1. 当前快照

- 当前开发分支：`stage1-csim-compile-hard-budget`
- 当前代码基线：`6d6f608e402d13827faa837de7e1e8674ecf12b6`
- 最新确定性测试：**204/204 passed**
- 最新真实工具验收：**real DFS → Preflight g++ → Vitis 2023.2 csynth → csim g++ → real executable，shared exact budget 4/2/1/1**
- TargetProfile 验收记录：[`stage1_target_profile_acceptance.md`](stage1_target_profile_acceptance.md)
- csynth hard budget 验收记录：[`stage1_csynth_budget_acceptance.md`](stage1_csynth_budget_acceptance.md)
- compile/csim hard budget 验收记录：[`stage1_compile_csim_budget_acceptance.md`](stage1_compile_csim_budget_acceptance.md)
- Stage 1 Core 总验收：[`stage1_core_acceptance.md`](stage1_core_acceptance.md)
- Testbench Reliability 验收记录：[`stage2_acceptance.md`](stage2_acceptance.md)
- 当前关键任务：**Stage 1 Core 已关闭；下一步在 Stage 2 固定 public/hidden test 角色与证据，然后进入 Stage 3 受控 API 重构闭环**

## 2. 已完成

### Stage 0

- Ubuntu 22.04、Python 3.10、Vitis HLS 2023.2 复现环境；
- DeepSeek V4 Flash/Pro OpenAI-compatible；
- `flow.new`、RAG、有限 `flow.parallel_kernel`、`opt.simple_iter` 基线；
- HeteroRefactor 因 ROSE/EDG 外部依赖暂停。

### Stage 1 共享基础设施

- `TaskSpec`、`TargetProfile`、`RunMode`；
- Model Registry 与 OpenAI-compatible Provider；
- Evaluator/Evidence 基础接口；
- `BudgetManager` core；
- `TraceRecorder`；
- `UnifiedRunner` 与 CLI；
- Legacy Refactor Adapter；
- AutoGen 与 testbench repair 已知 usage 合并。

### Stage 1 TargetProfile 本地执行核心

已经真实完成并验收：

```text
TaskSpec.target
→ LegacyRefactorAdapter
→ flow.new ContextVariables
→ target-aware Tcl
→ selected vitis-run
→ requested/actual version verification
→ real Vitis csynth
```

具体能力：

- 默认 profile：`vitis-2023.2-default`；
- `device`、clock period/frequency、compile flags replace/append；
- target-aware `set_part`、`create_clock`、`add_files -cflags`；
- `AGREFACTOR_VITIS_RUN` 可选 executable 覆盖；
- `vitis-run --version` 严格匹配；
- mismatch、probe failure、timeout、unparseable 在 csynth 前阻断；
- remote non-default target 显式拒绝，避免静默丢配置；
- `effective_target_profile.json`；
- `csynth_invocation.json`；
- Vitis 2023.2 真实 smoke：
  `/data/agrefactor_runs/stage1_target_profile_real_vitis_20260715_141118`；
- 实际器件：`xcu200-fsgd2104-2-e`；
- 实际目标时钟：`4.0 ns`；
- 语义 compile flag 已由 `#error` guard 验证；
- Estimated Fmax：`342.47 MHz`。

### Stage 1 csynth Hard Budget：已验收

已完成真实链路：

```text
UnifiedRunner
→ RunContext.budget
→ LegacyRefactorAdapter
→ hls_refactor_with_rag
→ csynth_and_csim
→ run_csynth
→ real vitis-run
```

关键结论：

- aggregate `max_tool_calls/tool_calls` 与专项 `max_csynth_calls/csynth_calls`；
- pre-version-probe hard check；
- pre-launch exact-once consume；
- success/failure/timeout/launch exception 计一次；
- mismatch 不计真实 csynth；
- local normal/HeteroRF 路径共享同一预算实例；
- bounded remote tool budget 显式拒绝；
- 真实 Vitis 2023.2 smoke：
  `/data/agrefactor_runs/stage1_real_vitis_csynth_budget_smoke_20260715_184955`；
- 第一次真实综合成功并生成 report；
- 第二次在 version probe 前阻断；
- final usage：`tool_calls=1`、`csynth_calls=1`。

详见 [`stage1_csynth_budget_acceptance.md`](stage1_csynth_budget_acceptance.md)。


### Stage 1 Compile 与 C Simulation Hard Budget：已验收

完整确定性链路：

```text
UnifiedRunner
→ RunContext.budget
→ LegacyRefactorAdapter
→ Testbench Preflight
→ run_csynth
→ run_csim
```

关键结论：

- aggregate `max_tool_calls/tool_calls`；
- `max_compile_calls/compile_calls`；
- `max_csim_calls/csim_calls`；
- Preflight 与 csim 编译共享 compile 额度；
- csim 完整计划在 `g++` 前 prospective hard check；
- compile 与 simulation 分别在真实启动前 exact-once consume；
- success/failure/timeout/launcher exception 语义已覆盖；
- 完整联合精确额度：`4 tool / 2 compile / 1 csynth / 1 csim`；
- 确定性测试：`204/204 passed`；
- 真实本地 csim smoke：
  `/data/agrefactor_runs/stage1_real_local_csim_budget_smoke_20260715_215055`；
- 第一次真实 `g++` 与生成的 `./csim` 执行成功；
- 第二次在 `g++` 前阻断；
- final usage：`tool_calls=2`、`compile_calls=1`、`csim_calls=1`。

详见 [`stage1_compile_csim_budget_acceptance.md`](stage1_compile_csim_budget_acceptance.md)。


### Stage 1 Core：已关闭

真实 DFS 总链路：

```text
real upstream DFS original
→ UnifiedRunner
→ shared BudgetManager
→ Preflight compile
→ Vitis HLS 2023.2 csynth
→ csim compile
→ generated executable
```

精确结果：

```text
tool_calls=4
compile_calls=2
csynth_calls=1
csim_calls=1
REAL_DFS_FULL_CHAIN_BUDGET_READY=1
```

运行目录：

```text
/data/agrefactor_runs/stage1_real_dfs_full_chain_budget_v2_20260716_111358
```

本次未调用 LLM API；候选为确定性可综合参考实现。该验收证明工具执行与预算基础设施可承载真实 DFS，不证明智能体已经自动重构 DFS。

范围决策：

- public test 是 Stage 2/3 的评测角色，不新增物理工具预算；
- cosim 在原项目无活跃实现，不阻塞 Stage 1 Core；
- named profiles、executable/settings、platform/resources/parser/provenance 继续作为 Hardening。

详见 [`stage1_core_acceptance.md`](stage1_core_acceptance.md)。

### Stage 2 Testbench Reliability 核心

- structured preflight；
- failure stage/kind/owner/next-action；
- bounded testbench-only repair；
- ABI/linkage 约束；
- private global gate；
- repair artifacts 与 usage 合并；
- 一个真实状态型 kernel 的 unified CLI → DeepSeek → Vitis E2E。

## 3. 未完成

### Stage 1 Hardening（不阻塞 Core 关闭）

1. TargetProfile stable named profiles；
2. per-profile executable/settings；
3. platform、resource limits、report parser profile；
4. effective profile 每字段 provenance；
5. 更多 Vitis 版本、器件和真实 kernel。

Stage 3 仍需实现预算耗尽时停止新候选并返回 `best_correct`。

### Stage 2 剩余项

1. General VitisFeedbackParser；
2. Evidence-driven State Machine；
3. Shared Layered Prompt Builder；
4. 多类型真实 kernel smoke；
5. 文档与验收同步。

### 后续 Stage

- Stage 3 Safe Three-Level Optimizer：未开始；
- Stage 4 Memory Applicability Gate：未开始；
- Stage 5 Target Version Extension / Real Migration：未开始；
- Stage 6 System Evaluation：未开始。

## 4. 当前下一任务

Stage 1 Core 已关闭。下一步：

```text
A. Stage 2 定义 public/hidden test split、suite identity、feedback visibility 与 evidence
B. 完善 general feedback parser/state transitions
C. Stage 3 运行受控 DFS API 重构 smoke
D. 真实 Preflight/csynth/csim 反馈驱动至少一次候选修复
E. 实现 bounded retries、checkpoint 与 best_correct
```

public test 不新增 `public_test_calls`；cosim 后续作为可选 RTL 能力独立建设。

## 5. 多 Vitis 版本的当前显式用法

多版本机器必须同时指定：

```text
TaskSpec.target.toolchain_version
+
AGREFACTOR_VITIS_RUN=/path/to/the/matching/vitis-run
```

例如：

```bash
source /data/Xilinx/Vitis/2024.1/settings64.sh
export AGREFACTOR_VITIS_RUN=/data/Xilinx/Vitis/2024.1/bin/vitis-run
python -m agrefactor.cli run task-2024.1.json --legacy
```

requested 与 actual 不一致时，系统会在 csynth 前阻断。完整命令见 [`USAGE.md`](USAGE.md)。

## 6. 对外表述边界

允许表述：

> TargetProfile 本地执行核心已在 Vitis 2023.2 上完成一次真实 csynth 验收。

不允许表述：

- 已支持任意 Vitis 版本；
- 已支持任意版本迁移；
- 已支持任意器件或任意 kernel；
- Stage 1 Core 之外的 Hardening 已全部完成；
- Stage 1 Core 关闭等于智能体已经通过 API 自动重构 DFS；
- 204 个测试等于 204 个真实 kernel。

## 7. 新对话阅读顺序

```text
1. docs/PROJECT_STATE.md
2. docs/ROADMAP.md
3. docs/GOAL_TRACEABILITY.md
4. docs/STAGE1_INFRASTRUCTURE.md
5. docs/stage1_target_profile_acceptance.md
6. docs/stage1_csynth_budget_acceptance.md
7. docs/stage1_compile_csim_budget_acceptance.md
8. docs/stage1_core_acceptance.md
9. docs/STAGE2_EVIDENCE_LOOP.md
10. docs/stage2_acceptance.md
11. docs/REPRODUCTION_STATUS.md
12. docs/USAGE.md
13. git log
```
