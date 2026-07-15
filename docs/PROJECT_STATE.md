# AgRefactor++ Current Project State

> **新对话或恢复开发时首先阅读本文档。** 权威范围见 [`ROADMAP.md`](ROADMAP.md)，目标追踪见 [`GOAL_TRACEABILITY.md`](GOAL_TRACEABILITY.md)。

## 1. 当前快照

- 当前开发分支：`stage1-csynth-hard-budget`
- 当前代码基线：`eb1575a84fd41c1f2269da6977e25d6cdb084f74`
- 最新确定性测试：**169/169 passed**
- 最新真实工具验收：**UnifiedRunner → shared BudgetManager → real Vitis 2023.2 csynth → second call blocked before probe**
- TargetProfile 验收记录：[`stage1_target_profile_acceptance.md`](stage1_target_profile_acceptance.md)
- csynth hard budget 验收记录：[`stage1_csynth_budget_acceptance.md`](stage1_csynth_budget_acceptance.md)
- Testbench Reliability 验收记录：[`stage2_acceptance.md`](stage2_acceptance.md)
- 当前关键任务：**将相同硬预算契约扩展到剩余工具，暂不开始 Stage 3**

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

### Stage 2 Testbench Reliability 核心

- structured preflight；
- failure stage/kind/owner/next-action；
- bounded testbench-only repair；
- ABI/linkage 约束；
- private global gate；
- repair artifacts 与 usage 合并；
- 一个真实状态型 kernel 的 unified CLI → DeepSeek → Vitis E2E。

## 3. 未完成

### Stage 1 阻塞项

1. compile/public-test/csim/cosim 的完整计数与硬限制；
2. 剩余工具的 pre-call check、exact-once accounting 与 evidence；
3. 预算耗尽时停止后续候选并在 Stage 3 返回 `best_correct`；
4. TargetProfile stable named profiles；
5. per-profile executable/settings；
6. platform、resource limits、report parser profile；
7. effective profile 每字段 provenance。

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

csynth hard budget 已完成验收。下一步先只读审计剩余工具调用图，再一次只选择一个工具复制同一契约。

推荐顺序：

```text
A. 审计 compile/public-test/csim/cosim call sites
B. 固定下一个工具的 budget key、limit、usage 与 evidence
C. 在任何真实 subprocess/tool launch 前 hard check
D. 对真实尝试执行 exact-once accounting
E. 覆盖 limit=0、limit=1、failure、timeout、exception
F. 贯通 UnifiedRunner 与 legacy 主流程
G. 完成确定性测试和一次真实工具 smoke
```

不要一次同时修改 compile、public-test、csim 和 cosim。

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
- Stage 1 已经关闭；
- csynth hard budget 等于所有工具预算已完成；
- 169 个测试等于 169 个真实 kernel。

## 7. 新对话阅读顺序

```text
1. docs/PROJECT_STATE.md
2. docs/ROADMAP.md
3. docs/GOAL_TRACEABILITY.md
4. docs/STAGE1_INFRASTRUCTURE.md
5. docs/stage1_target_profile_acceptance.md
6. docs/stage1_csynth_budget_acceptance.md
7. docs/STAGE2_EVIDENCE_LOOP.md
8. docs/stage2_acceptance.md
9. docs/REPRODUCTION_STATUS.md
10. docs/USAGE.md
11. git log
```
