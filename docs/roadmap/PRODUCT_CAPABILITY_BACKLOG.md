
# Product Capability Backlog

本文档记录已经明确延期、但不能遗忘的产品能力。它不是当前执行指针，也不改变 `PRE_STAGE3_CLOSED=true` 和 `STAGE3_STARTED=false`。

## 1. 模型与 Provider 能力

### 具体模型/部署的 reasoning 映射

当前普通 CLI 统一提供 `low/medium/high`，默认 `medium`；真实参数仍沿用现有 Profile。后续需要按“具体 model ID + provider/deployment + API format”建立能力记录，包括：

- graded effort；
- thinking enable/disable；
- provider-managed reasoning；
- 不同 API 的参数名；
- requested/effective/provider 参数证据。

### Authorized auto model pool

后续实现：

- `fixed` 与显式授权 `auto` 策略；
- allowed model pool；
- fallback/routing policy；
- 选择原因和成本/能力证据；
- 不得未经授权替换用户模型。

### 模型请求参数

`temperature/top_p/max_tokens/seed/stop` 等暂不开放。只有在 typed capability、默认值、安全上限和 Execution Identity 完整后再考虑产品化。

## 2. Stage 3 优化器参数

Stage 3 规划时统一设计：

- optimization objective；
- structural/bottleneck/pragma budgets；
- hypothesis/beam limits；
- checkpoint、rollback 与 cache policy；
- feasibility、acceptance 和 best-candidate policy；
- resource utilization constraints。

TargetProfile 已具备 BRAM/DSP/FF/LUT/URAM limit schema，但普通 CLI 暂不开放，留到优化器合同统一处理。

## 3. Coverage policy

当前固定：

```text
Public coverage target = 80%
Hidden coverage target = 90%
```

后续讨论是否只开放 Public target、是否通过 operator/evaluation profile 管理 Hidden target，以及如何防止用户降低 Hidden 证据强度。

## 4. Memory/RAG

Stage 4 再产品化：

- memory mode；
- knowledge database；
- retrieval top-k；
- applicability/confidence gate；
- update/retention policy；
- retrieval evidence。

## 5. Version migration

Stage 5 统一设计：

- source toolchain/profile；
- target toolchain/profile；
- migration mode；
- repository-level migration；
- compatibility validation；
- migration evidence and rollback。

## 6. Future runtime budgets

保留新增：

- cosim call budget；
- cosim timeout；
- cosim evidence and failure ownership。

当前 compile/CSIM/CSYNTH/tool/LLM/wall-time 维度保持不变。

## 7. Deferred advanced configuration

暂不开放：

-完整 Target executable/settings/parser overrides；
- resource-limit CLI；
- separate generation/Testbench-repair/Candidate-repair models；
- advanced suite provenance authoring；
- per-run temporary work directory override。
