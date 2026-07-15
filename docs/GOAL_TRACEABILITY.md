# AgRefactor++ Goal Traceability

> 本文档回答：最初目标是否还在、在哪个 Stage 实现、目前完成到什么程度、下一证据是什么。

## 1. 核心目标追踪表

| 目标 | 主要 Stage | 当前实现 | 仍缺内容 | 当前证据 |
|---|---|---|---|---|
| TargetProfile | Stage 1/5 | default/override、legacy propagation、actual command、version gate、part、clock、flags、Tcl、effective profile、invocation evidence | named profiles、settings/executable 自包含、platform/resources/parser、provenance、多版本/多 kernel | [`stage1_target_profile_acceptance.md`](stage1_target_profile_acceptance.md) |
| 双模式版本处理 | Stage 5 | refactor/optimize/full 数据结构预留 | migrate mode、SourceProfile、source baseline、migration report | 至少一组真实 source→target |
| Model API Registry | Stage 1 | Registry、OpenAI-compatible Provider、DeepSeek 验证 | 更多 provider profiles、用户授权模型池 | 不改主流程即可接入授权模型 |
| 分层 Prompt | Stage 2 | testbench repair prompt 与 evidence injection 的局部实现 | Shared Prompt Builder、stage layer、TargetProfile layer、family adapter | 多模型/多阶段真实工具结果 |
| 结构化反馈/状态机 | Stage 2 | TestbenchPreflight evidence | general parser、完整 state machine | 多类错误分类并驱动合法动作 |
| 安全三级优化器 | Stage 3 | legacy `simple_iter` baseline | hypothesis、3 levels、checkpoint、rollback、cache、best_correct | 多 kernel 与 baseline 对照 |
| Memory Applicability Gate | Stage 4 | legacy RAG 正负 trial | schema、score、abstention、off/gated/always | 负迁移与弃权实验 |
| BudgetManager | Stage 1/3 | token/cost core、repair calls、wall-time core；compile/csynth/csim aggregate+specific hard limits、pre-call block、exact-once accounting、UnifiedRunner/legacy propagation、real Vitis csynth smoke、real local csim smoke | public-test/cosim 真实语义审计与预算决策；真实完整工具链总验收；Stage 3 budget exhaustion 返回 best_correct | [`stage1_csynth_budget_acceptance.md`](stage1_csynth_budget_acceptance.md)、[`stage1_compile_csim_budget_acceptance.md`](stage1_compile_csim_budget_acceptance.md) |

## 2. TargetProfile 当前边界

已经能够声明：

> 在 commit `717fdef` 上，TargetProfile 本地执行核心已真实控制 Vitis 2023.2 的 executable、版本、part、clock、compile flags 和 Tcl，并留下可审计证据。

仍不能声明：

- 支持任意 Vitis 版本；
- 支持任意 source→target 迁移；
- 支持任意器件；
- 支持任意 kernel；
- per-task executable/settings 已完全配置化。

## 3. 防止概念偷换

```text
TargetProfile 一次真实运行成功 ≠ 任意版本支持
TaskSpec 有 version 字段 ≠ 版本迁移
RAG 检索存在 ≠ Memory Applicability Gate
simple_iter 能循环 ≠ 安全三级优化器
204 个确定性测试 ≠ 204 个真实 kernel
一次 PPA 改善 ≠ 稳定优化收益
compile/csynth/csim hard budget 已生效 ≠ Stage 1 已整体关闭
```

## 4. 完成声明检查表

1. 数据结构是否存在？
2. 是否接入真实主流程？
3. 是否控制实际工具行为？
4. 是否有失败路径测试？
5. 是否有真实端到端证据？
6. 是否覆盖超过一个构造样例？
7. 文档是否同步？
8. 当前限制是否明确？

任一关键项为否，应表述为“部分完成”“核心完成”或“尚未验证”。

## 5. 当前下一任务

compile、csynth 与本地 csim 已完成底层、统一入口和真实工具 smoke 验收。

当前下一任务：

```text
audit public-test/cosim canonical actions
→ confirm real external launches
→ define a dedicated budget only when semantically meaningful
→ deterministic/real-tool evidence
→ final real Preflight → Vitis csynth → csim acceptance
```
