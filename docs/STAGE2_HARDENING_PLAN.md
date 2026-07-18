# Stage 2 Audit, Hardening, and Closure Plan

> **路线决策记录。** 本文档固定 Stage 2.5 之后的审计、补强和关闭顺序，
> 并规定 Stage 1 Hardening 的分批时机。除非有新的代码、测试或真实工具证据，
> 不得静默删除、扩大或跨 Stage 偷换这里的职责。

## 1. 固定路线

```text
Stage 2.5 Multi-type Kernel Smoke Matrix
→ Stage 2.6 Closure-readiness Audit
→ Stage 2.7 Cross-stage Validation and Repair Hardening
→ Stage 2.8 Final Documentation and Stage 2 Closure
→ Stage 3 Safe Three-Level Optimizer
```

Stage 2 不在 2.5 后立即关闭。先用真实多类型案例暴露问题，再审计，再修复
证据证明的阻塞项，最后才关闭。

## 2. Stage 2.5：Smoke 与独立 Ground Truth

内部顺序：

```text
2.5.1 Smoke Corpus / Ground Truth Contract（已完成）
→ 2.5.2 Real Full-chain Pass Matrix
→ 2.5.3 Fault / Ownership / Hidden Matrix
→ 2.5.4 Evidence Summary
```

2.5.1 功能提交：

```text
ca991c372f9f40f7e592136b12af774dd985c0fa
feat: add Stage 2 smoke corpus
```

2.5.1 已固定七类 immutable source bundle、独立 baseline labels、
operator/agent-safe manifests，并完成 `7/7` real g++ Preflight。
下一步只执行 2.5.2，不提前执行 2.7 Hardening。

最低类型：

- array map；
- reduction；
- nested loop / stencil；
- multi-output；
- `ap_int` 或 struct；
- `hls::stream`；
- stateful kernel。

每个案例至少记录：

```text
case_id
kernel_type
injected_fault
ground_truth_owner
ground_truth_stage
expected_route
expected_terminal_state
hidden_visibility_expectation
```

系统自己的 owner、route 或 terminal 预测不能作为 ground truth。

## 3. Stage 2.6：Closure-readiness Audit

Stage 2.6 只做证据审计，不宣布关闭，也不做无边界重构。它必须输出：

1. 进入 Stage 3 前必须修复的阻塞项；
2. 可推迟到 Stage 4、5、6 的内容；
3. 远期研究或外部依赖；
4. 每个阻塞项的证据、影响、修改范围和验收标准；
5. Stage 2.7 的最终文件级计划。

重点审计：

- UnifiedRunner / CLI 正式主入口；
- 真实网络模型 candidate repair；
- run artifact 完整性；
- Testbench/Candidate repair Protocol 与 schema 漂移；
- Model Family 薄适配；
- Candidate Response Contract；
- CSYNTH diagnostic parser；
- Hidden 隔离；
- 模型与工具预算；
- Stage 1 Hardening Batch A。

## 4. Stage 2.7：固定工作类别

具体代码任务由 2.5/2.6 证据决定，但类别固定。

### 4.1 UnifiedRunner / CLI 正式接入

```text
TaskSpec
→ UnifiedRunner / CLI
→ Handler Factory
→ repair-aware ValidationOrchestrator
→ complete run artifacts
```

### 4.2 真实网络模型闭环

至少一次用户显式指定的 OpenAI-compatible 模型：

```text
real candidate-owned failure
→ real model request
→ strict response contract
→ bounded repair
→ real Preflight / CSYNTH / Public / Hidden validation
→ accepted or trustworthy terminal failure
```

模型不必必然修好，但模型选择、usage、异常、非法输出、预算、artifact 和
Hidden 隔离必须真实验收。

### 4.3 Repair Protocol 与 Artifact Schema 对齐

Testbench Repair 与 Candidate Repair 不强行合并执行器，但统一：

```text
attempt identity
proposal identity
prompt manifest
model response
observed usage
validation summary
stop reason
terminal status
artifact manifest
```

### 4.4 最小 Model Family Profile

使用 capability/profile，不使用厂商硬编码：

```text
reasoning_model
code_specialized
strict_instruction
thinking_tag_possible
strict_completion
```

只做薄适配和安全默认参数，不做自动模型路由。

### 4.5 Contract 与 Parser Hardening

只修复 2.5 corpus 证明的问题，包括复杂顶层签名、`typedef`/`using`、
namespace、attributes、`ap_int`、struct、stream，以及常见 synthesis
diagnostics。不能确定时继续 `unknown → review_required`。

### 4.6 Ground-truth Corpus 固化

把 2.5 案例固化为可重复标注集，为 Stage 6 的 owner、route、Memory
applicability 和 expected termination 评估提供独立标签。

### 4.7 Stage 1 Hardening Batch A

进入 Stage 3 前完成：

- stable named TargetProfile；
- per-profile executable/settings；
- report parser profile；
- effective value provenance；
- basic resource-limit schema；
- target/model/`.env.example` 无 secret 稳定模板。

## 5. Stage 1 Hardening Batch B

进入 Stage 5 前完成，不阻塞 Stage 3：

- 更多真实 Vitis 版本；
- 更多器件与 platform；
- 版本特定 parser 差异；
- source/target profile 扩展；
- 多版本、多器件、多 kernel 交叉验证。

## 6. 明确不属于 Stage 2.7

### Stage 3

- `best_correct` / `best_ppa`；
- candidate tree；
- PPA ranking；
- checkpoint / rollback / cache；
- Structural → Bottleneck → Pragma optimizer。

### Stage 4

- Memory `use / reject / abstain`；
- `off / gated / always` 的真实决策。

### Stage 5

- SourceProfile；
- source→target 真实迁移；
- 自动 SourceProfile 识别。

### Stage 6 或远期

- 大规模 benchmark 统计结论；
- ROSE/EDG HeteroRefactor 恢复；
- repository-level migration；
- XRT→AVED 等 Host/Runtime/Platform 联合迁移。

## 7. Stage 2.8 关闭条件

只有以下全部成立，才能声明 Stage 2 closed：

1. 2.5 多类型真实工具 smoke 完成；
2. 独立 ground truth 完成；
3. 2.6 审计的 Stage 3 blockers 全部解决或明确移出关键路径；
4. UnifiedRunner/CLI 能构造正式 validation/repair 链；
5. 至少一次真实网络模型 repair 闭环；
6. Hidden pass/fail 和无泄漏案例通过；
7. Repair Protocol/artifact schema 稳定；
8. Stage 1 Hardening Batch A 完成；
9. README、USAGE、REPRODUCTION_STATUS、CHANGELOG、ROADMAP、acceptance、
   smoke、PROJECT_STATE 和 NEXT_CHAT_HANDOFF 同步；
10. 文档继续区分 deterministic、FakeProvider、real model 和 real tools。

## 8. 执行原则

- 当前只进入 Stage 2.5.2；
- 2.5.2 必须复用 2.5.1 已提交 corpus；
- 2.5 记录问题，不提前大修 2.7；
- 2.6 先审计，再冻结文件级任务；
- 2.7 只修证据证明的阻塞项；
- 2.8 才宣布 Stage 2 关闭；
- Stage 2 未关闭前不进入 Stage 3。
