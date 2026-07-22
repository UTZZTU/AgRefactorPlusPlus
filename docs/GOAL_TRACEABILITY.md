# AgRefactor++ Goal Traceability

> 本文档回答：最初目标是否还在、在哪个 Stage 实现、目前完成到什么程度、下一证据是什么。

## 1. 核心目标追踪表

| 目标 | 主要 Stage | 当前实现 | 仍缺内容 | 当前证据 |
|---|---|---|---|---|
| TargetProfile | Stage 1/2/5 | 2.7.3 已完成 committed named profile、executable/settings、parser identity、resource schema、per-field provenance；保持 Vitis 2023.2 默认兼容 | Batch B 多版本/设备/platform 与 Stage 5 source/target | [`stage2_stage1_hardening_batch_a_acceptance.md`](stage2_stage1_hardening_batch_a_acceptance.md) |
| 双模式版本处理 | Stage 5 | refactor/optimize/full 数据结构预留 | migrate mode、SourceProfile、source baseline、migration report | 至少一组真实 source→target |
| Model API Registry | Stage 1/2 | Registry、OpenAI-compatible Provider、P1-C1/P1-C2/P1-C3A 已统一现代与 Legacy typed config；P1-C3B 已移除 Loader 厂商策略，1184/1184 回归通过 | P1-C3C usage bridge、P1-C4 parity、P1-D bounded smoke | [`P1C3B_GENERIC_LOADER_POLICY_ACCEPTANCE.md`](P1C3B_GENERIC_LOADER_POLICY_ACCEPTANCE.md) |
| 分层 Prompt | Stage 2 | Shared builder、candidate/testbench consumers、typed family instruction、formal CLI 与 strict contract 已完成；真实 proposal evidence 未证明需要放宽 | Stage 2 已关闭；后续仅按新真实证据 harden | [`stage2_closure_acceptance.md`](stage2_closure_acceptance.md) |
| 结构化反馈/状态机 | Stage 2 | Public/Hidden、router/state/coordinator、real handlers、formal repair-aware CLI、5/5 blockers 与 final closure 已完成 | Stage 2 已关闭；Stage 3 复用 correctness gate | [`stage2_closure_acceptance.md`](stage2_closure_acceptance.md) |
| Multi-type Smoke / Independent Ground Truth | Stage 2 | 7 baselines、7/7 full chains、9/9 faults、16/16 labels，并经 2.7.7/2.8 closure audit | 扩大版本、器件和 kernel 统计覆盖 | [`stage2_closure_acceptance.md`](stage2_closure_acceptance.md) |
| 安全三级优化器 | Stage 3 | legacy `simple_iter` baseline | hypothesis、3 levels、checkpoint、rollback、cache、best_correct | 多 kernel 与 baseline 对照 |
| Memory Applicability Gate | Stage 4 | legacy RAG 正负 trial | schema、score、abstention、off/gated/always | 负迁移与弃权实验 |
| BudgetManager | Stage 1/3 | token/cost、LLM/tool/compile/csynth/csim hard limits、pre-call block、real launch exact-once accounting、UnifiedRunner/legacy propagation；真实 DFS 工具链与 Stage 2 repair-aware validation 共享预算通过 | Stage 3 budget exhaustion 停止新候选并返回 best_correct | [`PROJECT_STATE.md`](PROJECT_STATE.md) |

<!-- PRE_STAGE3_BUDGET_PRICING_REFINEMENT:BEGIN -->
## 1.1 Budget and pricing product contract

Before Stage 3:

- P1 provides official pricing metadata, source identity and estimation quality;
- P2 resolves system defaults, safety ceilings and user hard-budget overrides;
- Token/Cost are observed-only soft budgets;
- LLM/tool/compile/CSIM/CSYNTH calls remain hard-counted resources;
- P5 reports both limits and actual usage;
- P0 validates the complete contract through the normal source-only entry.
<!-- PRE_STAGE3_BUDGET_PRICING_REFINEMENT:END -->

<!-- P1_MODEL_RUNTIME_AUDIT_DECISIONS:BEGIN -->
## 1.2 P1 audit closure and active evidence

Step 0 consumer audit is complete. The final findings, corrections, F15 addition,
P1-A–P1-D split and acceptance gates are recorded in
[`P1_MODEL_RUNTIME_AUDIT_DECISIONS.md`](P1_MODEL_RUNTIME_AUDIT_DECISIONS.md).

P1-A deterministic acceptance is complete at `e9f4a51744ce44c04236466450b8af85ebf9be9c` with **889/889** tests.
Evidence: [`P1A_STATIC_MODEL_COMPATIBILITY_ACCEPTANCE.md`](P1A_STATIC_MODEL_COMPATIBILITY_ACCEPTANCE.md).

P1-B0 read-only pricing/cost audit is complete at `24918d6fcfe1250043cd6a72082456241fa4679e`.
Evidence:
[`P1B0_PRICING_CONSUMER_AUDIT_DECISIONS.md`](P1B0_PRICING_CONSUMER_AUDIT_DECISIONS.md).

P1-B1 deterministic acceptance is complete at `bb219ea9e3049b4f5959c9dbb9c0e585875afd82` with
**920/920** tests. Evidence:
[`P1B1_TYPED_PRICING_SCHEMA_ACCEPTANCE.md`](P1B1_TYPED_PRICING_SCHEMA_ACCEPTANCE.md).

P1-B2 deterministic acceptance is complete at `571c51fcc250592a21bf40b3831b7dccfc6400aa` with **950/950** tests, 5 source records and 6 verified concrete-model snapshots. Evidence: [`P1B2_OFFICIAL_PRICING_SNAPSHOTS_ACCEPTANCE.md`](P1B2_OFFICIAL_PRICING_SNAPSHOTS_ACCEPTANCE.md).

P1-B3 deterministic acceptance is complete with implementation commit
`1c6c7efc9160c104319d4cc01a9b96c3ae0d082e`, correction commit `2296a18f09aa478afcdc5cc9652b4d9166a44149` and
**993/993** final tests. Evidence:
[`P1B3_COST_ESTIMATOR_ACCEPTANCE.md`](P1B3_COST_ESTIMATOR_ACCEPTANCE.md).

P1-B4A deterministic acceptance is complete at `ae276f3df79685a7edd36dc6b06c7d82d5784e7a` with **1016/1016** tests and patch ID `89db552f6660c8e5fa9ac2a67deb21909ae25ae3`. Evidence:
[`P1B4A_USAGE_NORMALIZATION_SERIALIZATION_ACCEPTANCE.md`](P1B4A_USAGE_NORMALIZATION_SERIALIZATION_ACCEPTANCE.md).

P1-B4B deterministic acceptance is complete at `f650478e842e9020c23489adb407b1b50f1c4438` with **1052/1052** tests and patch ID `5360788b724a9c6d6fcebff107943436efb8a510`. P1-B is closed. Evidence:
[`P1B4B_NATIVE_COST_ACCOUNTING_ACCEPTANCE.md`](P1B4B_NATIVE_COST_ACCOUNTING_ACCEPTANCE.md).

P1-C1 deterministic acceptance is complete at `3137a9cdbaf0201ed2ee3f5a28225121ceb04d56` with **1089/1089** tests and patch ID `4a37e161da17664a073761837ce944ea7eff749d`. Evidence:
[`P1C1_TYPED_EFFECTIVE_MODEL_CONFIG_ACCEPTANCE.md`](P1C1_TYPED_EFFECTIVE_MODEL_CONFIG_ACCEPTANCE.md).

P1-C2 deterministic acceptance is complete at `4a39ed894da4d04e3d46772c7b2f5d400ed98093` with **1119/1119** tests and patch ID `01d5e3c292b82e9fb58a8c9f14b02c7a90b5a9c9`. Evidence:
[`P1C2_MODERN_CONSUMER_MIGRATION_ACCEPTANCE.md`](P1C2_MODERN_CONSUMER_MIGRATION_ACCEPTANCE.md).

P1-C3A deterministic acceptance is complete at `c14650b2a474478cd82c0a9d1798fdd9b80d971b` with **1153/1153** tests and patch ID `b5302f1d3205042b01884e9be4c4e9c0095fb380`. Evidence:
[`P1C3A_TYPED_LEGACY_TRANSLATION_ACCEPTANCE.md`](P1C3A_TYPED_LEGACY_TRANSLATION_ACCEPTANCE.md).

P1-C3B deterministic acceptance is complete at `343d23c5b811f7c529991450b0952299f460c820` with **1184/1184** tests and patch ID `4e4597fb64f4dc3dab29a6b51228143586cb174c`. Evidence:
[`P1C3B_GENERIC_LOADER_POLICY_ACCEPTANCE.md`](P1C3B_GENERIC_LOADER_POLICY_ACCEPTANCE.md).

Current next evidence:

```text
P1-C3C currency-correct Legacy usage bridge
-> remove hard-coded usage-price fallback from authority
-> distinguish framework-reported cost from typed snapshot estimate
-> carry explicit currency, amount, quality and pricing provenance
-> record native currency through the existing Budget ledger
-> populate cost_usd only when the actual currency is USD
-> resolve the separate testbench-repair model through the shared contract
-> no Loader policy, Provider, normal CLI, P1-C4, P1-D or Stage 3 work
```

P1-C4 remains the deterministic modern/Legacy parity closure. P1-D remains a
separate bounded network smoke after deterministic P1-C closure.
<!-- P1_MODEL_RUNTIME_AUDIT_DECISIONS:END -->

## 2. TargetProfile 当前边界

已经能够声明：

> 在 Stage 1 Core 与 Stage 2.7.3 Batch A 验收中，TargetProfile 已真实控制 Vitis 2023.2 的 executable/settings、版本、part、clock、compile flags、Tcl、parser identity、resource schema 和 provenance。

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
727 个确定性测试 ≠ 727 个真实 kernel
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

Stage 2 已关闭，但 Stage 3 仍被 Pre-Stage-3 产品化收尾阻断。权威计划见
[`PRE_STAGE3_PRODUCTIZATION_PLAN.md`](PRE_STAGE3_PRODUCTIZATION_PLAN.md)。

```text
Step 0  文档冻结与只读 consumer 审计
Step 1  P1-C3B 已完成；P1-C3C currency-correct Legacy usage bridge 当前活跃
Step 2  P4 Public/Hidden 来源与 provenance
Step 3  P2 source-only bootstrap 与统一 CLI
Step 4  Execution Identity
Step 5  P5 简洁输出
Step 6  P0 真实 DFS source-only accepted
Step 7  清理、弃用与 Pre-Stage-3 Closure
Step 8  Stage 3 Safe Three-Level Optimizer
```

普通用户必须提供 source、`--top` 和 model，只看到
`refactor / optimize / full`，不选择 `--legacy / --repair-aware`。

P0 只有在最终 source-only 入口内部生成 TaskSpec、测试和初始 Candidate，
并由 Stage 2 的 Preflight → CSYNTH → Public → Hidden → bounded repair
正式链返回 `accepted` 时才完成。Legacy flow 自身的成功不能替代该证据。
