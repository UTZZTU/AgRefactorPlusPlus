# AgRefactor++ Goal Traceability

> 本文档回答：最初目标是否还在、在哪个 Stage 实现、目前完成到什么程度、下一证据是什么。

## 1. 核心目标追踪表

| 目标 | 主要 Stage | 当前实现 | 仍缺内容 | 当前证据 |
|---|---|---|---|---|
| TargetProfile | Stage 1/5 | default/override、legacy propagation、actual command、version gate、part、clock、flags、Tcl、effective profile、invocation evidence | named profiles、settings/executable 自包含、platform/resources/parser、provenance、多版本/多 kernel | [`stage1_target_profile_acceptance.md`](stage1_target_profile_acceptance.md) |
| 双模式版本处理 | Stage 5 | refactor/optimize/full 数据结构预留 | migrate mode、SourceProfile、source baseline、migration report | 至少一组真实 source→target |
| Model API Registry | Stage 1 | Registry、OpenAI-compatible Provider、DeepSeek 验证 | 更多 provider profiles、用户授权模型池 | 不改主流程即可接入授权模型 |
| 分层 Prompt | Stage 2 | SharedLayeredPromptBuilder、TaskSpec/TargetProfile/stage/family/evidence/scope/history/approved-memory/output layers；testbench 与 candidate compile/CSYNTH/Public-CSIM consumers；provider-neutral adapter、bounded loop 与 safe Orchestrator integration | 真实网络模型闭环 smoke、UnifiedRunner/CLI 正式构造、多类型 kernel 与 Stage 2.6 最终同步 | [`PROJECT_STATE.md`](PROJECT_STATE.md) |
| 结构化反馈/状态机 | Stage 2 | Public/Hidden evidence、通用 Feedback Schema、adapters/parser/views/composers、router、state machine、coordinator、real handlers、ValidationOrchestrator 与 bounded candidate-repair orchestration | 多类型 kernel smoke、UnifiedRunner/CLI 正式构造、真实网络模型 smoke、最终 Stage 2 closure | [`PROJECT_STATE.md`](PROJECT_STATE.md) |
| 安全三级优化器 | Stage 3 | legacy `simple_iter` baseline | hypothesis、3 levels、checkpoint、rollback、cache、best_correct | 多 kernel 与 baseline 对照 |
| Memory Applicability Gate | Stage 4 | legacy RAG 正负 trial | schema、score、abstention、off/gated/always | 负迁移与弃权实验 |
| BudgetManager | Stage 1/3 | token/cost、LLM/tool/compile/csynth/csim hard limits、pre-call block、real launch exact-once accounting、UnifiedRunner/legacy propagation；真实 DFS 工具链与 Stage 2 repair-aware validation 共享预算通过 | Stage 3 budget exhaustion 停止新候选并返回 best_correct | [`PROJECT_STATE.md`](PROJECT_STATE.md) |

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
662 个确定性测试 ≠ 662 个真实 kernel
一次 PPA 改善 ≠ 稳定优化收益
Stage 1 Core 已关闭 ≠ Stage 1 Hardening 已完成，也 ≠ API 智能体已自动重构 DFS
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

Stage 2.1–2.4 已形成结构化反馈、共享 Prompt、bounded repair 和
真实本地工具链重验证闭环。Stage 2 尚未关闭。

当前下一任务：

```text
Stage 2.5 multi-type real-kernel smoke + independent ground truth
→ Stage 2.6 closure-readiness audit
→ Stage 2.7 cross-stage validation and repair hardening
→ Stage 2.8 final documentation and closure
→ Stage 3 safe optimizer
```

当前不把本地 FakeProvider 表述为真实网络模型，也不把单一小型 kernel
验收表述为任意 HLS 程序支持。Stage 2.7 的类别已经锁定，但具体修复项必须
由 Stage 2.5 的真实 smoke 和 Stage 2.6 的独立审计提供证据。详细计划见
[`STAGE2_HARDENING_PLAN.md`](STAGE2_HARDENING_PLAN.md)。
