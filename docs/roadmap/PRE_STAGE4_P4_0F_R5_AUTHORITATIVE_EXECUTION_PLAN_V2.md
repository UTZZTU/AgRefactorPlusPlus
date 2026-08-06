# AgRefactor++ P4-0F-R5 权威执行计划 v2

**生成时间：** 2026-08-07
**权威类型：** 当前执行入口、正式状态、诊断 lane 合同、新对话接力源
**仓库：** `UTZZTU/AgRefactorPlusPlus`
**要求分支：** `stage2-general-feedback`
**本次文档同步前的行为 checkpoint：** `0ca5dd99fabec1c2c003446975e28128a0926c52`
**R5-D 远端冻结分支：** `p4-0f-r5-d-accepted-20260807`

> 本计划用于记录和约束后续工作。写入本计划本身不代表 R5、P4-0F、Pre-Stage-4 或 Stage 4 已完成。

## 00_START_HERE - 当前执行入口

当前唯一下一实现包：

```text
P4-0F-R5-E-R1
Native CSIM/COSIM Candidate Ownership
and Phase-scoped Typed Outcome Transport Correction
```

后续主线冻结为：

```text
R5-E-R1 局部修复
→ focused tests + 完整确定性回归
→ 新 Git checkpoint
→ 新 campaign root 下从头重跑 5 个 R5-E no-model canary
→ no-model gate 全过后才运行真实 DeepSeek provider diagnostic
→ 独立归档审计
→ R5 accepted
→ Legacy differential batch A
→ Real-code discovery batch A
→ P4-0F-Final：模式预算、Full reserves、CLI truthfulness
→ P4-0G dynamic-v1
→ Real-code discovery batch B
→ P4-0H repeated multi-kernel authority matrix
→ P4-0I 文档同步和 Pre-Stage-4 closure
→ 仅当 0F、0G、0H、0I 全部 accepted 后进入 Stage 4
```

不得因为一次确定性回归通过或单个 canary 成功而跳过任一步骤。

## 01_CURRENT_FORMAL_STATE - 当前正式状态

```text
branch=stage2-general-feedback
behavior_head=0ca5dd99fabec1c2c003446975e28128a0926c52
behavior_checkpoint=p4-0f-r5-d-accepted-20260807
complete_deterministic_regression=2268/2268

P4_0A=accepted
P4_0B=accepted
P4_0B_R=accepted
P4_0C=accepted
P4_0D=accepted_with_later_R5_corrections
P4_0E=accepted
P4_0E_R1=accepted

P4_0F_R5_D_IMPLEMENTATION_ACCEPTED=true
P4_0F_R5_D_EVIDENCE_ARCHIVE_VERIFIED=true
P4_0F_R5_D_COMMIT=0ca5dd99fabec1c2c003446975e28128a0926c52
P4_0F_R5_D_SHADOW_REGRESSION=2268/2268
P4_0F_R5_D_REAL_REGRESSION=2268/2268
P4_0F_R5_D_CHANGED_PATHS_VERIFIED=23/23

P4_0F_R5_E_V1_STATUS=failed_package_harness_before_campaign
P4_0F_R5_E_V2_BASELINE_REGRESSION=2268/2268
P4_0F_R5_E_V2_CAMPAIGN_OBSERVABILITY_VERIFIED=true
P4_0F_R5_E_V2_BASELINE_REAL_VITIS_VERIFIED=true
P4_0F_R5_E_V2_PUBLIC_CSIM_TESTBENCH_RECOVERY_HYBRID=true
P4_0F_R5_E_V2_PUBLIC_COSIM_TESTBENCH_RECOVERY_HYBRID=true
P4_0F_R5_E_V2_PUBLIC_CSIM_CANDIDATE_RECOVERY=false
P4_0F_R5_E_V2_PUBLIC_COSIM_CANDIDATE_RECOVERY=false
P4_0F_R5_E_V2_PROVIDER_DIAGNOSTIC=not_run_by_stop_rule
P4_0F_R5_E_RUNTIME_GATES_PASSED=false
P4_0F_R5_ACCEPTED=false

P4_0F_COMPLETE=false
PRE_STAGE4_COMPLETE=false
STAGE4_ALLOWED=false
NEXT_IMPLEMENTATION_PACKAGE=P4-0F-R5-E-R1
```

## 02_AUTHORITY_AND_FROZEN_DECISIONS - 权威顺序和冻结决策

发生冲突时按以下顺序裁决：

1. 独立复核过的真实执行归档；
2. 当前 checkpoint 的已提交代码和 typed schema；
3. 本 v2 权威计划和 machine-readable state；
4. 阶段 decision record 与 acceptance；
5. 历史包和聊天总结。

冻结决策：

- correctness 和 evidence 优先于方便；
- 字符串或正则不能作为 owner 的最终权威门；
- 物理执行事实必须先转换为 typed evidence，再进入路由；
- owner 无法确定时必须 fail closed，不能静默当作 Candidate；
- Hidden automated repair 永久为 0；
- LLM advisory 不能直接接受 Candidate；
- Candidate 与 Testbench recovery 统一使用一个 RecoveryPolicy 和一个精确 ledger；
- 任何代码修复后必须使用新 commit、新 campaign ID、全批从头重跑；
- Legacy 差分和 Real-code discovery 是 bounded diagnostic lane，不替代 P4-0F-Final 或 P4-0H。

## 03_P4_0F_MISSION_AND_R5_CORRECTION - 0F 原使命与 R5 纠正

P4-0F 原使命是在稳定的真实流水线上测量 Refactor、Optimize、Full，并冻结：

- mode-specific 默认硬预算；
- Full 为 Optimize 保留的最低容量；
- LLM、tool、compile、CSIM、CSYNTH、COSIM、wall-time 的真实消耗；
- 每个公开 CLI 参数 consumed 或 explicitly rejected 的合同。

早期真实矩阵证明验证流水线还不够稳定，不能直接用异常路径消耗冻结默认预算。因此 R5 成为 reliability 与 recovery governance 纠正循环：既修已知 D1-D11，也主动暴露相邻未知缺陷。

R5 accepted 不等于 P4-0F complete。

## 04_R5_IMPLEMENTATION_AND_EVIDENCE_LEDGER - R5 实现与证据账本

### R5-A / R5-B / R5-C

此前包建立了独立 evidence audit、bug family、fault injection、source eligibility、owner boundary、campaign observability，以及统一 RecoveryPolicy 所需输入。

### R5-D 已接受 checkpoint

R5-D 冻结：

- `conservative-v1` RecoveryPolicy；
- 精确 RecoveryLedger；
- eligible Public CSIM/COSIM 的 Candidate/Testbench bounded recovery；
- Hidden repair=0；
- typed timeout classes 和 unknown-safe fallback；
- candidate-only advisory，默认关闭；
- repair 后完整 validation restart；
- shadow 和 real 两轮 2268/2268；
- 行为 checkpoint `0ca5dd99...`。

### R5-E v1 裁决

v1 先通过 2268/2268，随后错误地向位置参数 CLI 传入 `--manifest`。campaign 未启动，仓库未修改。该结果属于 package execution harness defect，不属于产品缺陷。

### R5-E v2 裁决

v2 已真实证明：

- 2268/2268；
- CampaignRunner 串行执行、heartbeat、event sequence、`shell=false`、独立 case root、fail-soft continuation；
- 一条完整 real-Vitis baseline qualification；
- Public CSIM Testbench recovery hybrid canary；
- Public COSIM Testbench recovery hybrid canary；
- 无 false acceptance、Hidden leak、secret leak、private reasoning persistence、预算绕过和仓库修改。

v2 暴露两个真实产品集成缺陷：

1. **Native CSIM phase 分类错误**：compile/link 成功，`csim.exe` 已运行，`main` 返回非 0，但 legacy result 被标为 `tb_compile_failed`，上层只能 unknown-safe 停止；
2. **COSIM typed outcome transport stale**：C pre-check 写入 pass，RTL post-check 随后失败，但 phase result 未覆盖；高层看到 physical failure + stale typed pass 后安全降级为 unknown。

provider diagnostic 因 no-model stop rule 被正确阻断，所以不能据此判断 DeepSeek 的质量或可用性。

## 05_R5_E_R1_NEXT_WORK_PACKAGE - 唯一下一修复包

R5-E-R1 是局部、收敛式修复。不得建立平行 router、state machine、recovery policy 或 canary 专用产品分支。

必须完成：

1. 基于结构化 physical facts 与 suite contract 修正 Native CSIM execution-phase classifier；
2. 让 COSIM typed outcome phase-scoped、identity-bound，或等价保证 post-check failure 必然覆盖 pre-check pass；
3. 无法确定 owner 时继续 unknown-safe；
4. 把两条真实失败固化为 historical replay；
5. 增加 compile、link、toolchain、timeout、Testbench、unknown、identity mismatch 反例；
6. 增加 stale-pass mutation-sensitive invariant；
7. 继续走已有 FeedbackRouter、ValidationStateMachine、RecoveryPolicy、RecoveryLedger；
8. focused tests + 完整回归；
9. 冻结新 commit；
10. 从新 root 重跑全部五案例；
11. 五案例全过后才运行 provider diagnostic；
12. 独立复核归档。

仅完成代码修复不能接受 R5-E，完整重跑和归档审计是强制门。

## 06_ARCHITECTURE_NON_REGRESSION - 架构不回退

主架构保持：

```text
physical execution facts
→ typed evidence
→ deterministic owner/category authority
→ FeedbackRouter
→ ValidationStateMachine
→ RecoveryPolicy + BudgetManager + RecoveryLedger
→ bounded repair
→ complete validation restart
→ independent auditor
```

E-R1 可以补 adapter 或 typed transport，但不能复制下游 authority。严格审计使可见缺陷数量增加，不等于允许无 consumer 地扩张架构。

## 07_LEGACY_DIFFERENTIAL_REGRESSION_LANE - Legacy 差分回归

Legacy 差分现在是正式 diagnostic lane。

### 目的

比较：

```text
A. Original AgRefactor
B. AgRefactor++ full typed-evidence product flow
```

它不是立即证明论文优越性，而是发现：

- PlusPlus 是否丢失 Legacy 的重构能力；
- 新严格验证是否揭露 Legacy 假成功；
- typed parser/owner 是否制造假失败；
- 新系统的预算、回滚和证据成本。

### 启动门与时序

Batch A 仅在以下条件后启动：

```text
R5-E runtime gates passed
+ R5-E archive independently verified
+ P4_0F_R5_ACCEPTED=true
```

它在 P4-0F-Final 冻结预算之前执行。可以作为 bounded lane 与 P4-0F-Final 准备并行，但发现 P0/P1 产品回归时必须重开 R5.x，并在修复前阻断预算冻结。

### 公平比较合同

两条 arm 必须使用相同：

- source/top；
- exact model、endpoint、model-family、reasoning 参数；
- Public/Hidden suites；
- TargetProfile；
- hard budgets；
- independent final qualification auditor。

不能直接比较各自的 `success` 字段。

### 第一批案例

```text
dfs
ahocorasick
strassen
linkedlist
mergesort
```

若 Original AgRefactor 在当前环境不可运行，记录 typed `baseline_unavailable`，不能伪造结果，也不能仅因该环境缺失阻塞 R5/P4-0F。

### 结果解释

- Legacy pass / PlusPlus fail：可能是真回归、合同不适配或 PlusPlus 假失败；
- Legacy fail / PlusPlus pass：新流程产生真实改善候选；
- Legacy pass / PlusPlus strict qualification fail：Legacy 可能弱验证或假成功；
- 都失败：能力边界、模型失败或共同环境问题。

单纯模型质量差异保留为数据；P0/P1 产品回归重开 R5.x。

## 08_REAL_CODE_DISCOVERY_CAMPAIGN - 真实代码工程发现

Real-code discovery 是正式的 pre-P4-0H 诊断程序，和 P4-0H authority matrix 分离。

### 全局 campaign 规则

- 固定 commit/model/target/budget/recovery policy；
- 每个 run 独立 artifact root；
- campaign 内不修改代码；
- Candidate/model/unsupported/no-improvement 等 safe failure 继续收集；
- false acceptance、Hidden leak、stale accepted evidence、预算绕过、best_correct corruption、identity mixing、共享 Vitis/API 故障全局停止；
- 修复后用新 commit、新 campaign ID、全批重跑。

### Batch A：R5 后、P4-0F-Final 前

Batch A 在 R5 accepted 后、P4-0F-Final 冻结预算前执行，先聚焦 Refactor 与 integration eligibility：

- 递归；
- 动态内存；
- 链表/树；
- STL 容器；
- 指针/别名；
- 全局状态；
- 真实应用函数。

每类 2-3 个候选，只有在明确 top、可控 Public 输入、可观察输出、可运行 reference、完整 CSIM/CSYNTH/COSIM 合同后才可进入。

Batch A 输出：

- 新系统 bug replay；
- P4-0F-Final 所需真实消耗样本；
- integration eligibility corpus；
- 后续 authority kernel 候选。

P0/P1 产品问题重开 R5.x；Candidate generation failure、bounded repair 未成功、正确 unsupported、no improvement、safe inconclusive 不自动阻塞 P4-0F-Final。

### Batch B：P4-0G 后、P4-0H 前

Batch B 在 dynamic-v1 后、正式 matrix 前扩展 Optimize 类别：

- 矩阵；
- 图像/视频；
- 密码学；
- 信号处理；
- irregular memory；
- dataflow。

Direct Optimize 必须先有独立完整 qualified baseline；Full 只从已经证明 Refactor eligible、Optimize contract-compatible 的 kernel 中选择。

Batch B 用于筛选 P4-0H kernel，不是 P4-0H 本身，也不能用于提前宣称稳定成功率或 PPA 优势。

## 09_P4_0F_FINAL_BUDGET_AND_CLI_CLOSURE - 预算和 CLI 关闭

R5 accepted 且 diagnostic batch A 裁决后，P4-0F-Final 必须：

- 在稳定流水线上执行 measured real Refactor/Optimize/Full；
- 冻结 mode-specific defaults 和 safety ceilings；
- 冻结 Full 为 Optimize 保留的最低资源；
- 记录真实物理消耗；
- 审计每个公开 CLI 参数 consumed 或 explicitly rejected；
- 同步 state、decision、acceptance、user docs。

只有完成这些，才可考虑 `P4_0F_COMPLETE=true`。

## 10_P4_0G_P4_0H_P4_0I_AND_STAGE4_ROUTE - 后续路线

```text
P4-0F accepted
→ P4-0G dynamic-v1
→ Real-code discovery batch B
→ P4-0H repeated multi-kernel authority matrix
→ P4-0I documentation and Pre-Stage-4 closure
```

P4-0H 才是正式 repeated multi-kernel network-LLM/Vitis authority matrix。P4-0I 负责最终 claims 和 gate 同步。

只有 P4-0F、P4-0G、P4-0H、P4-0I 全部 accepted 后才允许：

```text
PRE_STAGE4_COMPLETE=true
STAGE4_ALLOWED=true
```

## 11_ACCEPTANCE_STOP_AND_RESUME_RULES - 验收、停止、恢复

R5 接受要求 D1-D11 每个 family 均具备：最小复现、bug family、失败测试、代码修复、focused/full regression、historical replay、independent auditor、必要的真实 Vitis/provider canary、文档和 policy 同步。

全局停止：

- accepted 与底层 required failure 冲突；
- Hidden detail 进入模型或公开 artifact；
- source/candidate/suite/target/tool/parser/policy/commit identity 混用；
- prospective budget 被绕过；
- 失败 Candidate 覆盖 `best_correct`；
- 共享 Vitis/API 配置故障影响全部案例。

单 run 安全停止但 campaign 可继续：

- 模型未修成；
- 单次非全局 provider failure；
- 正确 abstention/unsupported；
- no improvement；
- safe unknown/inconclusive。

恢复规则：

- 冻结失败 campaign root、commit、诊断包；
- 不覆盖或续跑旧 root；
- 修复后使用新 code identity；
- 全批从头重跑；
- 不跳过被修改的 qualification 前缀。

## 12_DOCUMENT_AND_GIT_SYNC - 文档与 Git 同步

本次 sync 只更新文档，不修改产品代码，不改写 R5-D accepted checkpoint。

推荐提交信息：

```text
docs(prestage4): sync R5 status and diagnostic lanes
```

远端文档 checkpoint：

```text
p4-0f-r5-e-r1-authority-sync-20260807
```

不使用 force push、reset、rebase、checkout、stash。

## 13_NEW_CHAT_HANDOFF_REQUIREMENTS - 新对话接力要求

新对话必须先确认：

- 当前 branch/HEAD；
- worktree clean；
- R5-D accepted checkpoint；
- authority-sync commit 和远端分支；
- R5-E v1/v2 归档与裁决；
- 正式 booleans；
- E-R1 精确范围；
- Legacy differential 与 Real-code discovery 时序；
- 包和证据 SHA。

新对话不得直接跳到 P4-0F-Final、P4-0G、P4-0H 或 Stage 4。

## 14_SOURCE_EVIDENCE_INDEX - 证据来源

本 v2 计划基于：

- R5-D v5 accepted 实现与独立归档审计；
- commit `0ca5dd99fabec1c2c003446975e28128a0926c52`；
- R5-D v5 evidence archive SHA256 `bbd0da4c7a7c142a70312ca2ad74da15ff35e484982e82f7cd37ffe5ae5f53bb`；
- R5-E v1 harness-failure 归档 SHA256 `c9aaacc58aa09b8928b70d061beaad0be6a1ffd0a6f892385dae1a385655b722`；
- R5-E v2 归档 SHA256 `5fb38e6cc01cfcab2a4237e424d692ff8c146caa00e1687981b3e8ca8138159d`：2268/2268、5 个 no-model case 全启动、3 过 2 safe fail；
- v1 权威计划中的 Legacy compare 与 Real-code discovery 章节；
- 当前仓库 typed evidence、campaign、recovery、validation、audit 合同。
