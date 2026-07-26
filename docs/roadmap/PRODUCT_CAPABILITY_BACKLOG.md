# Product Capability Backlog

本文只记录不属于当前 Stage 3 首包、但不能遗忘的能力。当前执行指针见 [PROJECT_STATE.md](PROJECT_STATE.md)。

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
