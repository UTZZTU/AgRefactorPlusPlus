# Product Capability Backlog

<!-- R0_V2_2_BACKLOG_AUTHORITY:BEGIN -->
## V2.2 backlog 权威调整（2026-08-25）

- `dynamic-v1` 不再是 Pre-Stage-4 或当前论文的强制前置；现有 `safe-v1` 作为次要能力和基线保留。
- 版本迁移、跨版本/跨工具链泛化、自动模型路由与模型权重持续学习移入未来工作。
- 当前实现顺序固定为 R0 文档对齐后再进入 R1；本次包没有实现 R1。
- 新 AI advisory 自动修复 v1 只可面向 Candidate；现有 deterministic Testbench repair 继续保留。
- Hidden 永不暴露给诊断、repair 或 memory，也不允许被修复。

本文下方旧的 frozen-contract/backlog 内容保留为历史证据；与 V2.2 冲突时以
[`RESEARCH_ROADMAP_V2_2.md`](RESEARCH_ROADMAP_V2_2.md) 和
[`R0_V2_2_ROUTE_DECISION.md`](R0_V2_2_ROUTE_DECISION.md) 为准。
<!-- R0_V2_2_BACKLOG_AUTHORITY:END -->

本文只记录不属于当前 Stage 3 首包、但不能遗忘的能力。当前执行指针见 [PROJECT_STATE.md](PROJECT_STATE.md)。

<!-- PRE_STAGE4_PRODUCT_VALIDATION_HARDENING:BEGIN -->
## Moved into the Pre-Stage-4 frozen contract

The following are no longer unsequenced future backlog items. They are mandatory
before Stage 4 and are governed by
[`PRE_STAGE4_PRODUCT_VALIDATION_HARDENING_CONTRACT.md`](PRE_STAGE4_PRODUCT_VALIDATION_HARDENING_CONTRACT.md):

- concrete DeepSeek V4 Flash Thinking and graded-effort evidence;
- default Flash with user model/endpoint/API-key-environment overrides;
- explicit local `.env` loading without secret persistence;
- real Public native Vitis CSIM;
- real Public RTL COSIM with budget, timeout, evidence, and ownership;
- mode-specific Refactor/Optimize/Full budgets and Full Optimize reserves;
- truthful Optimize/Full CLI controls;
- bottleneck-driven `dynamic-v1`.

Historical `safe-v1` remains available only for reproducibility and comparison.
<!-- PRE_STAGE4_PRODUCT_VALIDATION_HARDENING:END -->


## 已迁移到冻结 Stage 3 合同

以下内容不再是未决 backlog，已经在 [STAGE3_IMPLEMENTATION_CONTRACT.md](STAGE3_IMPLEMENTATION_CONTRACT.md) 中冻结：

- optimization objective v1；
- Structural/Bottleneck/Pragma policy；
- hypothesis/candidate schema；
- checkpoint、rollback 和 validation cache；
- best_correct/best_ppa；
- budget exhaustion；
- optimize/full 语义；
- Stage 3 artifact layout；
-初始 CLI surface；
-实施分包和验收矩阵。

## 模型与 Provider Hardening

### 具体模型/部署 reasoning 映射

后续按：

```text
model ID
+ provider/deployment
+ API format
```

建立 graded effort、thinking enable/disable、provider-managed reasoning 和 requested/effective/provider evidence。

### Authorized auto model pool

后续实现 fixed/auto、allowed_models、fallback/routing policy 和选择证据。系统不得未经授权替换用户模型。

### 模型请求参数

`temperature/top_p/max_tokens/seed/stop` 等只有在 typed capability、默认值、安全上限和 Execution Identity 完整后才能开放。

## Coverage Policy

当前固定：

```text
Public coverage target=80%
Hidden coverage target=90%
```

后续讨论是否只开放 Public target，以及如何防止降低 Hidden 证据强度。

## Stage 4 Memory/RAG

- memory mode；
- knowledge database；
- retrieval top-k；
- applicability/confidence gate；
- update/retention policy；
- retrieval evidence；
- negative memory；
- abstention。

## Stage 5 Version Migration

- source toolchain/profile；
- target toolchain/profile；
- migration mode；
- compatibility validation；
- migration evidence；
- rollback；
-更多 Vitis 版本、设备、platform 和 parser。

## Future Runtime

-真实 RTL cosim call site；
-cosim budget；
-cosim timeout；
-cosim evidence/failure ownership。

不得为了补齐名词而创建没有真实 call site 的字段。

## Deferred Advanced Configuration

-完整 Target executable/settings/parser CLI override；
-per-run temporary work directory override；
-separate generation/Testbench-repair/Candidate-repair models；
-advanced suite provenance authoring；
-大规模自动路由；
-repository-level migration。
