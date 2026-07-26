# AgRefactor++ Goal Traceability

> 本文档回答：最初目标是否还在、在哪个 Stage 实现、目前完成到什么程度、下一证据是什么。


<!-- PRE_STAGE3_FINAL_CLOSURE -->
## Pre-Stage-3 final traceability

```text
Step 0-5=completed
Step 6 P0 real DFS dual-mode=passed
Step 7 cleanup/deprecation/final smoke=passed
DOCUMENTATION_CONSISTENCY=passed
PRE_STAGE3_CLOSED=true
STAGE3_STARTED=false
NEXT_STEP=STAGE3_PLANNING
```

The structural-feedback row's pending P0 evidence is now satisfied by
[`P0_REAL_DFS_DUAL_MODE_ACCEPTANCE.md`](../acceptance/pre-stage3/P0_REAL_DFS_DUAL_MODE_ACCEPTANCE.md).
The next implementation stage is Stage 3 Safe Three-Level Optimizer planning;
no Stage 3 implementation is included in this closure.

Documentation state reconciliation evidence:
[`PRE_STAGE3_DOCUMENTATION_CONSISTENCY_ACCEPTANCE.md`](../acceptance/pre-stage3/PRE_STAGE3_DOCUMENTATION_CONSISTENCY_ACCEPTANCE.md).

> **Historical snapshot policy:** Embedded P1/P4/P2/Execution-Identity/P5 acceptance blocks preserve the status that was true when each package closed. Their `PRE_STAGE3_CLOSED=false` or `P0=next/not run` lines are historical and do not override the final traceability block above.


## 1. 核心目标追踪表

| 目标 | 主要 Stage | 当前实现 | 仍缺内容 | 当前证据 |
|---|---|---|---|---|
| TargetProfile | Stage 1/2/5 | 2.7.3 已完成 committed named profile、executable/settings、parser identity、resource schema、per-field provenance；保持 Vitis 2023.2 默认兼容 | Batch B 多版本/设备/platform 与 Stage 5 source/target | [`stage2_stage1_hardening_batch_a_acceptance.md`](../acceptance/stage2/stage2_stage1_hardening_batch_a_acceptance.md) |
| 双模式版本处理 | Stage 5 | refactor/optimize/full 数据结构预留 | migrate mode、SourceProfile、source baseline、migration report | 至少一组真实 source→target |
| Model API Registry | Stage 1/2 | P1-A/B/C/D 已完成：Modern、Legacy、repair 统一运行时，且 `deepseek-v4-flash` 已完成单次有界真实网络 smoke、native CNY accounting 与第二次调用硬阻断 | 无；后续由 P4/P2/P5/P0 消费 | [`P1D_BOUNDED_NETWORK_SMOKE_ACCEPTANCE.md`](../acceptance/pre-stage3/P1D_BOUNDED_NETWORK_SMOKE_ACCEPTANCE.md) |
| 分层 Prompt | Stage 2 | Shared builder、candidate/testbench consumers、typed family instruction、formal CLI 与 strict contract 已完成；真实 proposal evidence 未证明需要放宽 | Stage 2 已关闭；后续仅按新真实证据 harden | [`stage2_closure_acceptance.md`](../acceptance/stage2/stage2_closure_acceptance.md) |
| 结构化反馈/状态机 | Stage 2 | Public/Hidden 路由、终态隔离与 agent-safe redaction 已完成；P4 进一步接入明确 source identity、revision、SHA-256、运行内容一致性和多 suite provenance | P0 经最终 source-only 入口做真实 DFS 复验 | [`P4_TEST_SOURCE_PROVENANCE_ACCEPTANCE.md`](../acceptance/pre-stage3/P4_TEST_SOURCE_PROVENANCE_ACCEPTANCE.md) |
| Multi-type Smoke / Independent Ground Truth | Stage 2 | 7 baselines、7/7 full chains、9/9 faults、16/16 labels，并经 2.7.7/2.8 closure audit | 扩大版本、器件和 kernel 统计覆盖 | [`stage2_closure_acceptance.md`](../acceptance/stage2/stage2_closure_acceptance.md) |
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

<!-- EXECUTION_IDENTITY_CLOSURE:BEGIN -->
## Execution Identity authority reconciliation evidence

```text
base=bc6b1b3a82b2ece0930391981f5cc9a238cd8046
full=1372/1372
actual rendered Prompt changes enter identity=true
post-run qualified suite provenance enters identity=true
actual CostEstimate quality enters identity=true
safety-ceiling rejected requests persist identity=true
Execution Identity frozen contract=closed
NEXT=P5 concise output and log capture
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```
<!-- EXECUTION_IDENTITY_CLOSURE:END -->

<!-- P1_MODEL_RUNTIME_AUDIT_DECISIONS:BEGIN -->
## 1.2 P1 audit closure and active evidence

Step 0 consumer audit is complete. The final findings, corrections, F15 addition,
P1-A–P1-D split and acceptance gates are recorded in
[`P1_MODEL_RUNTIME_AUDIT_DECISIONS.md`](../audits/P1_MODEL_RUNTIME_AUDIT_DECISIONS.md).

P1-A deterministic acceptance is complete at `e9f4a51744ce44c04236466450b8af85ebf9be9c` with **889/889** tests.
Evidence: [`P1A_STATIC_MODEL_COMPATIBILITY_ACCEPTANCE.md`](../acceptance/pre-stage3/P1A_STATIC_MODEL_COMPATIBILITY_ACCEPTANCE.md).

P1-B0 read-only pricing/cost audit is complete at `24918d6fcfe1250043cd6a72082456241fa4679e`.
Evidence:
[`P1B0_PRICING_CONSUMER_AUDIT_DECISIONS.md`](../audits/P1B0_PRICING_CONSUMER_AUDIT_DECISIONS.md).

P1-B1 deterministic acceptance is complete at `bb219ea9e3049b4f5959c9dbb9c0e585875afd82` with
**920/920** tests. Evidence:
[`P1B1_TYPED_PRICING_SCHEMA_ACCEPTANCE.md`](../acceptance/pre-stage3/P1B1_TYPED_PRICING_SCHEMA_ACCEPTANCE.md).

P1-B2 deterministic acceptance is complete at `571c51fcc250592a21bf40b3831b7dccfc6400aa` with **950/950** tests, 5 source records and 6 verified concrete-model snapshots. Evidence: [`P1B2_OFFICIAL_PRICING_SNAPSHOTS_ACCEPTANCE.md`](../acceptance/pre-stage3/P1B2_OFFICIAL_PRICING_SNAPSHOTS_ACCEPTANCE.md).

P1-B3 deterministic acceptance is complete with implementation commit
`1c6c7efc9160c104319d4cc01a9b96c3ae0d082e`, correction commit `2296a18f09aa478afcdc5cc9652b4d9166a44149` and
**993/993** final tests. Evidence:
[`P1B3_COST_ESTIMATOR_ACCEPTANCE.md`](../acceptance/pre-stage3/P1B3_COST_ESTIMATOR_ACCEPTANCE.md).

P1-B4A deterministic acceptance is complete at `ae276f3df79685a7edd36dc6b06c7d82d5784e7a` with **1016/1016** tests and patch ID `89db552f6660c8e5fa9ac2a67deb21909ae25ae3`. Evidence:
[`P1B4A_USAGE_NORMALIZATION_SERIALIZATION_ACCEPTANCE.md`](../acceptance/pre-stage3/P1B4A_USAGE_NORMALIZATION_SERIALIZATION_ACCEPTANCE.md).

P1-B4B deterministic acceptance is complete at `f650478e842e9020c23489adb407b1b50f1c4438` with **1052/1052** tests and patch ID `5360788b724a9c6d6fcebff107943436efb8a510`. P1-B is closed. Evidence:
[`P1B4B_NATIVE_COST_ACCOUNTING_ACCEPTANCE.md`](../acceptance/pre-stage3/P1B4B_NATIVE_COST_ACCOUNTING_ACCEPTANCE.md).

P1-C1 deterministic acceptance is complete at `3137a9cdbaf0201ed2ee3f5a28225121ceb04d56` with **1089/1089** tests and patch ID `4a37e161da17664a073761837ce944ea7eff749d`. Evidence:
[`P1C1_TYPED_EFFECTIVE_MODEL_CONFIG_ACCEPTANCE.md`](../acceptance/pre-stage3/P1C1_TYPED_EFFECTIVE_MODEL_CONFIG_ACCEPTANCE.md).

P1-C2 deterministic acceptance is complete at `4a39ed894da4d04e3d46772c7b2f5d400ed98093` with **1119/1119** tests and patch ID `01d5e3c292b82e9fb58a8c9f14b02c7a90b5a9c9`. Evidence:
[`P1C2_MODERN_CONSUMER_MIGRATION_ACCEPTANCE.md`](../acceptance/pre-stage3/P1C2_MODERN_CONSUMER_MIGRATION_ACCEPTANCE.md).

P1-C3A deterministic acceptance is complete at `c14650b2a474478cd82c0a9d1798fdd9b80d971b` with **1153/1153** tests and patch ID `b5302f1d3205042b01884e9be4c4e9c0095fb380`. Evidence:
[`P1C3A_TYPED_LEGACY_TRANSLATION_ACCEPTANCE.md`](../acceptance/pre-stage3/P1C3A_TYPED_LEGACY_TRANSLATION_ACCEPTANCE.md).

P1-C3B deterministic acceptance is complete at `343d23c5b811f7c529991450b0952299f460c820` with **1184/1184** tests and patch ID `4e4597fb64f4dc3dab29a6b51228143586cb174c`. Evidence:
[`P1C3B_GENERIC_LOADER_POLICY_ACCEPTANCE.md`](../acceptance/pre-stage3/P1C3B_GENERIC_LOADER_POLICY_ACCEPTANCE.md).

P1-C3C1 deterministic acceptance is complete at `d2f085b3cabefef87e8aa5099bdb1c2a8ce32b7d` with **1220/1220** tests and patch ID `f5ecbba1271868d84d1ad5b8482c50926a013c6f`. Evidence:
[`P1C3C1_TYPED_USAGE_SUMMARY_ACCEPTANCE.md`](../acceptance/pre-stage3/P1C3C1_TYPED_USAGE_SUMMARY_ACCEPTANCE.md).

P1-C3C2 integrated accounting completed at `f0c06c32771916bb6ad3bd68eb4ac21473dcd41b` with
**1250/1250** tests and patch ID `6f77f6146e64a341623ac9e21a591f5a7e4cd7bd`.
P1-C4 deterministic parity closed P1-C with **1275/1275** tests.
Evidence:
[`P1C_RUNTIME_CLOSURE_ACCEPTANCE.md`](../acceptance/pre-stage3/P1C_RUNTIME_CLOSURE_ACCEPTANCE.md).

P1-D bounded network smoke completed for `deepseek-v4-flash` with one real
DeepSeek API call. The concrete model is `network_smoke_verified`; observed
TokenUsage, native CNY estimated cost, credential exclusion and the prospective
second-call hard block are recorded in
[`P1D_BOUNDED_NETWORK_SMOKE_ACCEPTANCE.md`](../acceptance/pre-stage3/P1D_BOUNDED_NETWORK_SMOKE_ACCEPTANCE.md).

P4 Public/Hidden test-source provenance completed with
**1312/1312** deterministic tests and patch ID `bd85479221d8729c9aad23df6a91ccfaf4d7333b`. Evidence:
[`P4_TEST_SOURCE_PROVENANCE_ACCEPTANCE.md`](../acceptance/pre-stage3/P4_TEST_SOURCE_PROVENANCE_ACCEPTANCE.md).

Historical next evidence at the time of P1/P4 closure:

```text
P5 concise output and log capture
-> default / --json / --verbose / --debug boundaries
-> full evidence remains in artifacts
-> no Hidden leakage in ordinary output
-> no P0 or Stage 3 work yet
```
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

Pre-Stage-3 已关闭。当前下一任务是 Stage 3 Safe Three-Level Optimizer 的规划；Stage 3 实现尚未开始。权威计划见
[`PRE_STAGE3_PRODUCTIZATION_PLAN.md`](PRE_STAGE3_PRODUCTIZATION_PLAN.md)。

```text
Step 0  文档冻结与只读 consumer 审计
Step 1  P1-A/P1-B/P1-C/P1-D 已完成；P1 整体完成
Step 2  P4 Public/Hidden 来源与 provenance 已完成
Step 3  P2 source-only bootstrap、统一 CLI 与调用前预算闭环已完成
Step 4  Execution Identity 已完成
Step 5  P5 简洁输出与日志捕获已完成
Step 6  P0 真实 DFS source-only accepted（已完成）
Step 7  清理、弃用与 Pre-Stage-3 Closure（已完成）
Step 8  Stage 3 Safe Three-Level Optimizer
```

普通用户必须提供 source、`--top` 和 model，只看到
`refactor / optimize / full`，不选择 `--legacy / --repair-aware`。

P0 只有在最终 source-only 入口内部生成 TaskSpec、测试和初始 Candidate，
并由 Stage 2 的 Preflight → CSYNTH → Public → Hidden → bounded repair
正式链返回 `accepted` 时才完成。Legacy flow 自身的成功不能替代该证据。

<!-- P1_P4_FROZEN_CONTRACT_RECONCILIATION:BEGIN -->
## P1/P4 frozen-contract reconciliation

The previous `completed` labels used narrower acceptance scopes than
[`PRE_STAGE3_PRODUCTIZATION_PLAN.md`](PRE_STAGE3_PRODUCTIZATION_PLAN.md).
The mismatch is now corrected in one integrated package:

```text
baseline=1312
full=1334/1334
patch_id=c7dacd1afe4ad4e67a635f9e63d225a847aaf326
P1 frozen contract=reconciled
P4 frozen contract=reconciled
P2=completed and runtime-budget-corrected
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```

Evidence:
[`P1_P4_FROZEN_CONTRACT_RECONCILIATION.md`](../audits/P1_P4_FROZEN_CONTRACT_RECONCILIATION.md).
<!-- P1_P4_FROZEN_CONTRACT_RECONCILIATION:END -->

<!-- P2_SOURCE_ONLY_BOOTSTRAP:BEGIN -->
## P2 source-only bootstrap trace

| Frozen objective | Implemented evidence |
|---|---|
| Ordinary source input with explicit top/model | `agrefactor.cli refactor SOURCE --top TOP --model MODEL` |
| No ordinary task/candidate/work/artifact inputs | Internally managed `SourceRunLayout` and generated TaskSpec |
| P4 source selection reuse | `TestSourcePlan` mapping with repeated provided suites and inferred overall mode |
| Reuse rather than duplicate | Existing refactor backend runs in generation-only mode |
| Formal verdict | Generated candidate enters `CandidateRepairPhase` / Stage-2 orchestration |
| Shared hard budget | One `BudgetManager` object is reused by generation and formal validation |
| Soft Token/Cost | Separate observed-only nonblocking contract |
| Advanced reproduction | Existing `run task.json` remains |
| Stage-3 boundary | optimize/full reject rather than fabricate optimizer success |

Initial acceptance: `1346/1346`; runtime-budget correction: `1352/1352`. Patch
`af57008cd7db13e88400418fc95ac47baf157dc7`. Next objective: Execution Identity.
<!-- P2_SOURCE_ONLY_BOOTSTRAP:END -->

<!-- P5_CONCISE_OUTPUT_CLOSURE:BEGIN -->
## P5 concise output closure evidence

```text
base=0a1d816fa1d7f738dd3757a19a243df22020caf5
full=1391/1391
default/json/verbose/debug=true
complete_product_artifacts=true
soft_vs_hard_budget_labels=true
Hidden ordinary-output isolation=true
P5=closed
P0=next, not run
```

Evidence: [`P5_CONCISE_OUTPUT_ACCEPTANCE.md`](../acceptance/pre-stage3/P5_CONCISE_OUTPUT_ACCEPTANCE.md).
<!-- P5_CONCISE_OUTPUT_CLOSURE:END -->
