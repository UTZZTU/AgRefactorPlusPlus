# AgRefactor++ Current Project State

> **新对话或恢复开发时首先阅读本文档。** 权威范围见 [`ROADMAP.md`](ROADMAP.md)，目标追踪见 [`GOAL_TRACEABILITY.md`](GOAL_TRACEABILITY.md)。

## 1. 当前快照

- 当前开发分支：`stage1-target-profile-forwarding`
- TargetProfile 代码基线：`717fdef83a2ac96d3636461df7c733a85998ad3b`
- 最新确定性测试：**153/153 passed**
- 最新真实工具验收：**TargetProfile → target-aware Tcl → Vitis 2023.2 version gate → real csynth**
- TargetProfile 验收记录：[`stage1_target_profile_acceptance.md`](stage1_target_profile_acceptance.md)
- Testbench Reliability 验收记录：[`stage2_acceptance.md`](stage2_acceptance.md)
- 当前关键任务：**完成 Stage 1 工具硬预算，暂不开始 Stage 3**

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

1. compile/public-test/csim/csynth/cosim 的完整计数与硬限制；
2. 工具调用前预算检查；
3. 工具调用后真实消耗记录；
4. 预算耗尽时的安全停止；
5. TargetProfile stable named profiles；
6. per-profile executable/settings；
7. platform、resource limits、report parser profile；
8. effective profile 每字段 provenance。

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

先完成 **csynth hard budget**，再扩展到其他工具。

推荐顺序：

```text
A. 审计 BudgetManager 数据结构和所有 csynth call sites
B. 定义 csynth budget key、limit、usage 与 exhaustion evidence
C. 在任何版本探测和 Vitis 启动前执行 hard check
D. 调用完成后只记一次真实 csynth 消耗
E. limit=0 时确认 subprocess 完全不启动
F. limit=1 时第一次允许、第二次阻断
G. 异常、timeout、failure 仍计入一次真实调用
H. 将结果写入 trace / result evidence
I. 确定性测试后再做一次真实 Vitis budget smoke
```

不要一次同时改 compile、public test、csim、csynth、cosim。先把 csynth 的契约做正确，再复制同一模式。

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
- 153 个测试等于 153 个真实 kernel。

## 7. 新对话阅读顺序

```text
1. docs/PROJECT_STATE.md
2. docs/ROADMAP.md
3. docs/GOAL_TRACEABILITY.md
4. docs/STAGE1_INFRASTRUCTURE.md
5. docs/stage1_target_profile_acceptance.md
6. docs/STAGE2_EVIDENCE_LOOP.md
7. docs/stage2_acceptance.md
8. docs/REPRODUCTION_STATUS.md
9. docs/USAGE.md
10. git log
```
