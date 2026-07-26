# Stage 2 Audit, Hardening, and Closure Plan

> **路线决策记录。** 本文档固定 Stage 2.5 之后的审计、补强和关闭顺序，
> 并规定 Stage 1 Hardening 的分批时机。除非有新的代码、测试或真实工具证据，
> 不得静默删除、扩大或跨 Stage 偷换这里的职责。

## 1. 固定路线

```text
Stage 2.5 Multi-type Kernel Smoke Matrix
→ Stage 2.6 Closure-readiness Audit
→ Stage 2.7 Cross-stage Validation and Repair Hardening
→ Stage 2.8 Final Documentation and Stage 2 Closure（已完成）
→ Stage 3 Safe Three-Level Optimizer
```

Stage 2 不在 2.5 后立即关闭。先用真实多类型案例暴露问题，再审计，再修复
证据证明的阻塞项，最后才关闭。

## 2. Stage 2.5：Smoke 与独立 Ground Truth

内部顺序：

```text
2.5.1 Smoke Corpus / Ground Truth Contract（已完成）
→ 2.5.2 Real Full-chain Pass Matrix（已完成）
→ 2.5.3 Fault / Ownership / Hidden Matrix（已完成）
→ 2.5.4 Evidence Summary（已完成）
```

2.5.1 功能提交：

```text
ca991c372f9f40f7e592136b12af774dd985c0fa
feat: add Stage 2 smoke corpus
```

2.5.1–2.5.4 已完成：七类 baseline、7/7 real full chain、九场景
fault matrix、16 条独立标签和统一 evidence index。当前 `727/727`。
跨三次独立验收累计 `62/36/9/17/0`，但不是一次共享预算。
下一步只执行 Stage 2.6 Audit，不提前执行 2.7 Hardening。

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

## 3. Stage 2.6：Closure-readiness Audit — 已完成

审计输出：

```text
satisfied=4
blocking_before_stage3=5
defer=4
future_or_external=4
```

五个 Stage 3 blocker：

- B-01 formal repair-aware UnifiedRunner / CLI；
- B-02 shared Testbench/Candidate repair Protocol / artifact schema；
- B-03 minimal ModelFamilyProfile；
- B-04 Stage 1 Hardening Batch A；
- B-05 one real network-model candidate-repair smoke。

审计还确认：

- CandidateResponseContract 在当前七类 interface 上没有失败证据；
- CSYNTH parser 未识别诊断继续 UNKNOWN，不需猜测式扩张；
- 16-label ground-truth corpus 已满足 Stage 2，要在 2.7 重验证；
- executor merge、Batch B、统计 benchmark、migration 均不阻塞 Stage 3。

详细文件级证据见
[`STAGE2_CLOSURE_READINESS_AUDIT.md`](STAGE2_CLOSURE_READINESS_AUDIT.md)。

## 4. Stage 2.7：冻结执行顺序

```text
2.7.1 Repair Protocol and Artifact Schema（已完成）
→ 2.7.2 Minimal ModelFamilyProfile（已完成）
→ 2.7.3 Stage 1 Hardening Batch A（已完成）
→ 2.7.4 Formal Repair-aware UnifiedRunner / CLI（已完成）
→ 2.7.5 Real Network-model Candidate Repair Smoke（已完成）
→ 2.7.6 Evidence-gated Contract/Parser Delta + Ground-truth Revalidation（已完成）
→ 2.7.7 Cross-stage Regression and Stage 2.8 Handoff（已完成）
```

### 4.1 2.7.1 Repair Protocol and Artifact Schema — 已完成

```text
ae1042fc77efe5c87a85a5f4954a7c0a951f2045
feat: add shared repair protocol artifacts
```

已定义 versioned shared vocabulary：

```text
attempt_id
proposal_id
artifact_role
prompt_manifest
model_response
observed_usage
validation_summary
stop_reason
terminal_status
artifact_manifest
```

Testbench 与 Candidate executors 保持分离；共享层只定义公共 envelope，
并通过 CandidateRepairPayload / TestbenchRepairPayload 保留路径特有字段。
Protocol 层不调用模型、工具或 ValidationOrchestrator，也不成为第二个
orchestrator。现有 legacy JSON 保持兼容，共享 artifacts 采用原子写入。

Stage 2.7 是有限收尾阶段：只处理 B-01～B-05，以及 2.7.5 真实 smoke
直接证明的新 blocker。2.7.7 只做回归与 2.8 handoff，不新增功能；2.8 的
真实模型关闭条件是“真实调用和可信记录”，不是“必须成功修复”。

验收见
[`stage2_repair_protocol_acceptance.md`](../acceptance/stage2/stage2_repair_protocol_acceptance.md)。

### 4.2 2.7.2 Minimal ModelFamilyProfile — 已完成

```text
a9ec856540940f1767fe245a3c662468293fda5b
feat: add minimal model family profiles
```

已增加 capability tags 与安全默认参数：

```text
reasoning_model
code_specialized
strict_instruction
thinking_tag_possible
strict_completion
```

不做自动模型路由，用户固定模型仍是唯一权威选择。Profile 只影响安全默认
参数、通用 Prompt instruction 和审计 manifest；Response Contract 不可绕过。
验收见
[`stage2_model_family_profile_acceptance.md`](../acceptance/stage2/stage2_model_family_profile_acceptance.md)。

### 4.3 2.7.3 Stage 1 Hardening Batch A — 已完成

```text
411d1e2b37ae6e620c0b759b98f7e8277cb851c4
feat: harden target execution profiles
```

已完成 committed named target profile、per-profile executable/settings、
parser identity、effective provenance、basic resource schema 和无 secret 模板。
保持现有 Vitis 2023.2 行为，未增加 Batch B 多版本/设备矩阵。验收见
[`stage2_stage1_hardening_batch_a_acceptance.md`](../acceptance/stage2/stage2_stage1_hardening_batch_a_acceptance.md)。

### 4.4 2.7.4 Formal Repair-aware UnifiedRunner / CLI — 已完成

```text
7e9aef66ba062b25465f6552f9bf346b8ed5eb86
feat: add formal repair-aware runner phase
```

正式入口构造：

```text
TaskSpec
→ CLI / UnifiedRunner
→ repair phase
→ LocalCandidateValidationHandlerFactory
→ CandidateRepairValidationOrchestrator
→ complete safe artifacts
```

已由正式 `--repair-aware` CLI 接线并写入 versioned run/phase/repair manifests；
一个 UnifiedRunner run 共享一个 budget 和一个 trace。验收见
[`stage2_repair_aware_cli_acceptance.md`](../acceptance/stage2/stage2_repair_aware_cli_acceptance.md)。

### 4.5 2.7.5 Real Network-model Smoke — 已完成

```text
code_baseline=7407da78b9371e853b44a201828ce4b9251fad8f
model=deepseek-v4-flash
response_model=deepseek-v4-flash
model_calls=1
total_tokens=1106
attempt_status=validation_failed
orchestration_status=validation_terminal
response_contract=accepted
outcome=可信 terminal failure（validation_failed）
```

用户固定 OpenAI-compatible model/API 已完成一次真实调用。模型不必修复成功；
本次 request、response、usage、contract、预算、真实 Preflight/后续验证和 Hidden
边界均有可信 artifacts。验收见
[`stage2_real_network_candidate_repair_smoke.md`](stage2_real_network_candidate_repair_smoke.md)。

### 4.6 2.7.6 Evidence-gated Delta — 已完成

```text
code_baseline=b1a787ab0e41b382fec25973968e2b162a500f85
replayed_preflight_failure_kind=link_error
replayed_preflight_failure_owner=unknown
contract_delta_required=false
parser_delta_required=false
code_delta_applied=false
ground_truth_labels=16/16
```

2.7.5 的真实 response 仍满足当前 CandidateResponseContract；失败由真实
Preflight 捕获。没有 CSYNTH evidence，因此 parser 未修改。随后重跑 7/7
baseline full chains 与 9/9 fault matrix。验收见
[`stage2_evidence_gated_ground_truth_revalidation.md`](stage2_evidence_gated_ground_truth_revalidation.md)。

### 4.7 2.7.7 Cross-stage Regression — 已完成

```text
code_baseline=5d9ca6b76162f30e6a33c76d933ebb0021955baf
related_tests=389/389
full_unittest=836/836
evidence_milestones=8/8
blockers_satisfied=5/5
artifact_manifests_validated=8
closure_checklist=9/10
ready_for_stage2_8=true
stage2_closed=false
```

已完成 blocker 验收、acceptance/commit/evidence 索引、manifest/hash 核对和
Stage 2.8 frozen handoff。2.7.7 未新增功能、未调用网络模型、未重新执行 Vitis
CSYNTH/CSIM，也未关闭 Stage 2。验收见
[`stage2_hardening_acceptance.md`](../acceptance/stage2/stage2_hardening_acceptance.md)。

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

### Stage 2.8 final closure status

```text
satisfied=10/10
pending=none
related_tests=389/389
full_unittest=836/836
stage2_closed=true
stage3_allowed=true
```

C-09 已通过 README、`docs/history/CHANGELOG.md`、USAGE、REPRODUCTION_STATUS、
ROADMAP、Goal Traceability、Project State、Handoff、Evidence/Hardening 和
formal closure acceptance 的同步完成。详细记录见
[`stage2_closure_acceptance.md`](../acceptance/stage2/stage2_closure_acceptance.md)。

## 8. 执行原则

- Stage 2.8 已完成，当前进入 Stage 3 Safe Three-Level Optimizer；
- 2.5 evidence summary 是 2.6 的主要入口；
- 2.6 已完成分类；2.7.1 只实现 shared protocol/artifacts；
- 有限矩阵不能外推为任意 HLS 或统计准确率；
- 2.6 先审计，再冻结文件级任务；
- 2.7 只修证据证明的阻塞项；
- 2.8 已完成并正式关闭 Stage 2；
- Stage 3 现在允许开始，但必须从安全优化器契约和 correctness gate 开始。
