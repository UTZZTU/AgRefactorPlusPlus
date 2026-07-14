# AgRefactor++ Current Project State

> 新对话或恢复开发时首先阅读本文档。详细目标与完成标准见 [`ROADMAP.md`](ROADMAP.md)，目标追踪见 [`GOAL_TRACEABILITY.md`](GOAL_TRACEABILITY.md)。

## 快照

- 当前分支：`stage2-testbench-reliability`
- 当前重点：补齐 Stage 1/2 遗留项，暂不开始 Stage 3。
- 最新确定性测试：110/110。
- 最新真实验收：统一 CLI → DeepSeek → testbench preflight/repair → Vitis csynth → 项目 csim。
- 验收文档：[`stage2_acceptance.md`](stage2_acceptance.md)。

## 已完成

- Stage 0 复现基线基本完成；
- Stage 1 共享类型、模型层、Runner、CLI、Trace、Budget core、Adapter 与已知 usage 合并完成；
- Stage 2 Testbench Reliability 核心完成并真实验收；
- 原始竞赛方案已归档到 `docs/proposals/`；
- Stage 0–6 详细路线和八项目标已经文档化。

## 未完成

1. TargetProfile 尚未完整控制 legacy Vitis settings/tool/part/clock/flags/Tcl/parser；
2. compile/public-test/csim/csynth/cosim 预算尚未完整记录与硬限制；
3. 通用 VitisFeedbackParser 尚未完成；
4. 完整 Evidence State Machine 尚未完成；
5. Shared Layered Prompt Builder 尚未完成；
6. 真实端到端仍主要覆盖一个状态型 kernel；
7. Stage 3 Safe Optimizer、Stage 4 Memory Gate、Stage 5 Migration 均未开始。

## 下一项工作

**返回 Stage 1，使 TargetProfile 从“描述性元数据”变成“实际控制工具的配置”。**

追踪：

```text
TaskSpec / TargetProfile
→ LegacyRefactorAdapter
→ flow.new
→ csynth/csim tooling
→ generated Tcl and actual command
```

依次处理 settings/tool selection、part/device、clock、compile flags、parser selection、effective profile artifact 和 mismatch tests。

## 不可删除目标

TargetProfile、普通 C/C++ 与已有 HLS 双模式、Stage 5 真实版本迁移、用户指定模型与 Model Registry、分层 Prompt、结构化反馈与状态机、安全三级优化器、Memory Applicability Gate、模型/工具/时间硬预算。

## 新对话阅读顺序

```text
1. docs/PROJECT_STATE.md
2. docs/ROADMAP.md
3. docs/GOAL_TRACEABILITY.md
4. 当前 Stage 文档
5. docs/REPRODUCTION_STATUS.md
6. docs/USAGE.md
7. 最新 acceptance
8. git log
```

不得根据类名、参数名或单次成功推断完成状态。
