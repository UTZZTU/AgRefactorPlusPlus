# V2.3 项目完成执行计划

> 日期：2026-09-18
> 分支：`research-roadmap-v2.3`
> 制定依据：服务器真实代码、V2.3 权威路线、R2/R3/R4 设计与实现合同、近期真实执行证据
> 文档性质：当前执行计划；记录项目负责人的 R4 acceptance，但不构成 R5/R6 acceptance
> R4 acceptance 状态同步基线：`b800c96a76d33890fd6d0cf551f708cbba72ebe5`

## 1. 执行结论

当前不应该继续制作新的 Package D，也不应该直接启动完整 A0-A6 实验。正确顺序是：

```text
R4 项目负责人验收
-> 冻结 R5 设计、数据和预算
-> 把 R3/R4 的数据结构补成真正的持续记忆运行机制
-> 把经 Gate 批准的记忆载荷接入现有 Candidate repair prompt
-> 在原 refactor/optimize/full 内部接入实验配置，默认保持关闭
-> 先做零真实调用的确定性验证
-> 再做有硬预算的小规模真实 pilot
-> 冻结并执行 R5 A0-A6 时间顺序实验
-> 独立审计和项目负责人验收 R5
-> 冻结 R6 正式实验、论文证据和 release
```

R4 已经证明了“在严格证据、权限、预算和验证边界内，单次 Candidate-only AI 修复可以安全执行或拒答”。它没有证明持续记忆有效，也没有证明开放世界错误上的总体实用性。R5 的任务不是继续修 Package D，而是把 episode、pattern lifecycle、memory snapshot、memory payload 和 campaign 变成可运行、可比较、可审计的系统。

完成 V2.3 的判据不是“所有测试都绿”或“某个 canary 修好了”，而是路线文档中的候选核心贡献都有真实 producer、consumer、运行证据、负例、独立审计和不夸大的实证结论。

## 2. 当前权威事实

### 2.1 仓库和状态

```text
repository=/data/AgRefactor
branch=research-roadmap-v2.3
R4_ACCEPTANCE_BASE_HEAD=b800c96a76d33890fd6d0cf551f708cbba72ebe5
R0_ACCEPTED=true
R1_ACCEPTED=true
R2_ACCEPTED=true
R3_ACCEPTED=true
R4_ACCEPTED=true
R4_IMPLEMENTATION_STATUS=accepted_real_canary_independent_audit_and_project_owner_acceptance
R5_STARTED=true
R5_DESIGN_STATUS=frozen_for_implementation_not_accepted
R5_DESIGN_BASE_HEAD=03313195dda0612e80f8b57213c267a3694ad7d1
R6_STARTED=false
NEXT_STEP=V2.3-R5-evidence-inventory
```

### 2.2 R4 可验收证据

```text
evidence_root=/data/package_d_v1_6_r4_evidence_run1
evidence_archive_sha256=41f24168856b18683ee47c6285bf925cc48ddea97e369a440af38fd780d26703
baseline_runs=3
canary_runs=3
canary_statuses=abstained,verified_positive,verified_positive
provider_calls=5
vitis_launches=20
critical_findings=0
environmental_findings=0
independent_audit=ready_for_manual_checkpoint
```

这组证据已由项目负责人于 2026-09-18 正式接受，但只能支持 bounded R4 claim：

- R2 advisor、R3 Gate、R4 controller、现有 orchestrator、provider registry、Vitis executor 和独立 auditor 已形成受控的 Candidate-only repair 链；
- 两个 canary 在一次 Candidate mutation 后经 fresh formal validation 成为 `verified_positive`；
- 一个 canary 因不满足校准边界而在 mutation 前安全拒答；
- 模型、Gate 和执行包均没有成功裁决权。

它不能支持以下结论：

- 持续记忆已经在跨运行学习；
- 历史经验确实提高了未来修复率；
- negative transfer 已被量化控制；
- 对所有常见或未知 HLS 错误都有效；
- A0-A6 的因果差异已经建立。

### 2.3 R4 acceptance 边界

`ready_for_user_acceptance` 不等于 `accepted`。项目负责人已于 2026-09-18 明确回复“我正式接受 R4，进入 R5。”因此允许：

1. 将 `R4_ACCEPTED` 改为 `true`；
2. 同步 authority index、state JSON、roadmap、project state 和 goal traceability；
3. 创建单独的 R4 acceptance 状态提交并 push；
4. 保持 `R5_STARTED=false` 直到 P1 design freeze 正式开始；
5. 在 P0 完成后进入 R5 设计。

执行代理、测试包、审计包和退出码都不得代替该决定。

## 3. V2.3 核心贡献与当前缺口

V2.3 的主线是：

```text
evidence-gated open-world diagnosis
+ verified continual diagnostic memory
+ safe Candidate repair
```

当前状态与缺口如下。

| 候选贡献 | 当前已有 | 尚未完成 |
|---|---|---|
| owner-aware open-world Diagnostic Event/Advisory | R2 typed input/output、真实校准证书、严格 parser、citation/identity 绑定 | 尚未在普通产品 `refactor` 中作为 selective fallback 接入；覆盖率仍需 R5 测量 |
| deterministic safety kernel 与 AI authority 分离 | fixed FSM、Policy、Ledger、Budget、Hidden/Testbench boundary、formal validation、auditor | R5 各 arm 必须复用同一安全核，不能为实验绕过它 |
| verified positive/negative episode | R3 shadow episode schema；R4 可写真实 repair episode | R3 `EpisodeStore` 仍是进程内字典；缺统一、持久、append-only reader/manifest 和跨运行 ingestion |
| versioned pattern lifecycle | immutable `RepairPatternRevision` 和 lifecycle enum 已存在 | 缺从真实 episode 生成 child revision 的 reducer；缺 promotion/quarantine/deprecation 运行机制 |
| Applicability Gate | ordered accept/reject/abstain 已实现 | Gate 所消费的 conflict/sparse/OOD/risk 多依赖手工 context；缺历史统计和 snapshot builder |
| 记忆影响 Candidate repair | shared Candidate prompt 已支持 `approved_memory_snippets` | R4 mutation adapter 没有接收 selected revision/snippet；authorization 没绑定 snippet manifest hash |
| time-ordered evaluation | roadmap 已定义 history -> freeze K -> future | CampaignRunner 只有通用进程执行能力，缺 arm、snapshot、split、paired budget、leakage guard 和 reducer |
| 真实语料与安全指标 | R2 calibration、R4 v1.6 和历史运行归档存在 | 尚未形成不可变 evidence inventory 和可证明无 source/time leakage 的 R5 dataset |

最关键的实现判断是：prompt 层已有 memory hook，但可信数据链没有闭合。应补下面这条桥，而不是重写 prompt 或另造一套 repair：

```text
historical R3/R4 episodes
-> deterministic lifecycle reducer
-> immutable pattern revision
-> frozen memory snapshot K
-> Applicability Gate
-> approved Candidate-only memory payload manifest
-> authorization binds revision hash + payload hash
-> existing Candidate repair prompt
-> existing formal validation and auditor
-> new immutable outcome episode
```

## 4. 不可破坏的项目边界

整个后续执行必须持续满足以下约束。

1. 产品入口始终只有 `refactor`、`optimize`、`full`，不得创建第二套 CLI。
2. 不创建第二套 Vitis 执行、验证或成功裁决流程。
3. R2-R5 作为现有 orchestrator 的内部扩展，产品默认保持 `off`。
4. 确定性已知错误优先走原 Candidate/Testbench repair；R2 只补充 unknown/review，不接管全部失败。
5. AI 不能修改 FSM、Original、Public/Hidden Testbench、Target/configuration 或成功标准。
6. Hidden source、path、digest、oracle 和 future outcome 不得进入模型或 snapshot K。
7. 模型自报 `high confidence` 不构成授权；必须通过冻结校准证书和 deterministic checks。
8. 每个 run 最多一次 R4/R5 Candidate mutation，失败不得覆盖 `best_correct`。
9. 不按单个样例日志编号堆硬编码；确定性 parser 规则必须表达稳定错误族语义并有跨样例证据。
10. 不得把所有未知错误都当作“证据不足”。对常见错误的高拒答率是能力缺口，不能包装成安全成果。
11. synthetic fixture 只证明合同和负例；R5/R6 efficacy claim 必须来自 E3/E4 真实证据。
12. 每次 phase gate 都要有 machine-readable artifact、hash、调用计数、停止原因和独立审计边界。

三个入口在 V2.3 中有明确的优先级，而不是平均投入：

```text
refactor = 当前研究主路径和 R5 A0-A6 的主要真实执行入口
full     = refactor accepted material 到现有 optimizer 的组合与衔接验证
optimize = 保留 safe-v1 的 supporting subsystem / baseline
```

V2.3 不以 optimizer 创新为目标，不实现旧文档中的 `dynamic-v1`，不追求 PPA SOTA，也不为了形式上的“三入口对称”把 R2-R5 强行接入 direct `optimize`。只有会阻断正确性、预算共享、identity、artifact governance、`best_correct` 或 `full` 交接的问题才在当前路线修复。更有特色的 optimizer 应在 V2.3 主线完成后作为独立设计与实验问题处理，不能混入当前 A0-A6 变量。

## 5. 何时才属于“证据不足”

后续实现和实验必须使用统一判定，避免“不会处理就拒答”。

### 5.1 证据充分的典型情况

- Vitis 物理执行身份完整，stage、parser、source/test hash 可验证；
- agent-safe diagnostic items 含可引用的实际诊断内容，而不是只有汇总错误；
- owner/scope 能由确定性证据直接给出，或由已校准 R2 advisory 在允许词表内给出；
- advisory 引用的 evidence refs 存在、内容一致、没有 Hidden/secret；
- 若使用 memory，revision 在 snapshot K 中、Gate 通过、payload hash 与 authorization 一致；
- Policy、Ledger、Budget、canary 和一次修复限制全部满足。

### 5.2 必须拒答或阻断的情况

- execution/model/prompt/toolchain/parser identity 不完整或不匹配；
- 引用越界、诊断内容为空、只有无根因信息的汇总错误；
- owner、scope 或多个同强度证据互相冲突；
- OOD、样本稀疏或校准边界未覆盖，且没有足够 deterministic evidence；
- memory revision 已 quarantine/deprecated，或 future/source leakage 检查失败；
- 请求涉及 Hidden/Testbench/Original/configuration 等越权对象；
- provider/tool/environment failure 无法排除；
- 预算不足以完成 mutation 后的完整正式重验证。

### 5.3 实用性约束

R5 protocol 必须预先定义“常见错误族”。对每个纳入 primary evaluation 的常见错误族：

- 至少要有独立 source 的 history 与 future 样本；
- 至少包含正例和易混淆负例/不适用例；
- 不允许因可修复的数据 plumbing 缺陷而 100% abstain；
- 若全部 abstain，R5 对该错误族判为未建立能力，而不是判为安全成功；
- 真实歧义、OOD 和越权导致的 abstain 单独报告，并保留为正确安全行为。

确定性规则可以包含 Vitis error code 作为 evidence anchor，但授权必须依赖错误族、stage、owner、diagnostic content 和 identity 的组合，不能只比较一条编号。

## 6. 总体依赖链

```text
P0  R4 用户验收与权威状态同步
 |
 v
P1  R5 设计/arm/统计/预算协议冻结
 |
 v
P2  真实 evidence inventory 与 history/future/source split 冻结
 |
 +---------------------+
 |                     |
 v                     v
P3  持久 episode ledger/outcome reducer
 |                     |
 v                     |
P4  pattern lifecycle/snapshot builder/leakage guard
 |                     |
 +----------+----------+
            v
P5  Gate-approved memory payload -> R4 prompt 闭环
            |
            v
P6  原三入口内部接入与 A0-A6 research profile
            |
            v
P7  零真实调用的确定性验证和全量回归
            |
            v
P8  小规模真实 pilot + 独立审计
            |
            v
P9  冻结 R5 A0-A6 正式 campaign + R5 用户验收
            |
            v
P10 R6 code/data/protocol freeze -> 正式实验 -> 论文/release
```

P1 与 P2 可以在信息收集上交错，但 design 和 dataset 都冻结后才能进入真实 pilot。P3-P5 可以先分别实现合同测试，但集成验收必须按依赖顺序执行。P7 通过前禁止 P8；P8 出现 critical finding 时禁止 P9。

## 7. 分阶段执行流程

### P0：关闭 R4 的人工验收门

目标：把“技术证据已就绪”和“项目负责人已接受”分开处理。

输入：

- HEAD `b800c96a...`；
- `/data/package_d_v1_6_r4_evidence_run1`；
- archive SHA256 `41f241...703`；
- clean independent audit；
- 项目负责人明确接受 R4 的消息。

动作：

1. 再次核对 HEAD、clean worktree、archive hash、audit status 和调用计数；
2. 记录项目负责人的明确 acceptance，不允许代理自签；
3. 仅更新 current-authority blocks 和 machine-readable state；
4. 修正 `GOAL_TRACEABILITY.md` 顶部等当前状态漂移，但保留明确标注的历史快照；
5. 运行 state consistency 检查；
6. 创建单独 commit 并 push。

退出条件：

```text
R4_ACCEPTED=true
R5_STARTED=false
NEXT_STEP=V2.3-R5-design-freeze
```

项目负责人已经明确接受；本计划与同一状态提交完成 P0 记录。该 acceptance 不建立 R3 temporal memory efficacy，也不授权启动 R6。

### P1：冻结 R5 设计、消融和预算协议

目标：在写运行代码前，先保证 A0-A6 真正只改变目标机制。

新增权威产物：

```text
docs/roadmap/R5_CONTINUAL_MEMORY_CAMPAIGN_DESIGN.md
docs/roadmap/V2_3_R5_DESIGN.json
```

必须冻结：

- episode envelope、pattern revision、memory payload 和 snapshot schema/version；
- promotion、quarantine、deprecation 的阈值来源；
- A0-A6 的逐维开关；
- history/calibration/future/source holdout；
- primary/secondary endpoints；
- invalid/inconclusive/environmental failure 归类；
- repeats、seed、顺序、timeout、parallelism；
- provider/Vitis 上界估算和全局 hard cap；
- stop rule 和 independent auditor contract。

#### A0-A6 必须采用的可识别语义

| Arm | Advisor | Memory | Repair | 说明 |
|---|---|---|---|---|
| A0 | off | none | off | 初始 Candidate generation 保留；“off”仅指初始 Candidate 之后无自动 LLM repair |
| A1 | shadow | none | off | 测诊断增益，不改变主路径 |
| A2 | on | none | Candidate-only once | 测 advisor + repair；不得伪造 Trusted revision |
| A3 | on | similarity-only | Candidate-only once | 同 A2 安全核，加入 Legacy-style retrieval，不声称 Gate 安全性 |
| A4 | on | positive-only gated | Candidate-only once | 只用历史正支持和 exact exclusions |
| A5 | on | positive+negative gated | Candidate-only once | 加入 conflict/negative-transfer abstention |
| A6 | on | full lifecycle gated | Candidate-only once | 加入 promotion/quarantine/deprecation 与 frozen snapshot |

当前 `R4CandidateRepairAuthorization` 强制要求 `Gate accept + Trusted revision`，不能直接表达 A2/A3。P1 必须设计一个 discriminated research authorization seam：

- 不修改或削弱已验收的 R4 authorization 语义；
- A2 绑定 calibration/advisory/policy/ledger/budget 和明确的 `memory_mode=none`；
- A3 绑定 retrieval manifest，但不得把 similarity 当作 Gate accept；
- A4-A6 继续要求 Gate 和 revision/payload hash；
- 所有 arm 复用同一个 mutation controller、一次限制、formal validation 和 auditor。

禁止为 A2 创建假 revision 或把空 memory 冒充 Trusted memory，否则 A2-A6 不可因果解释。

#### 预算冻结

用户在 2026-09-18 将总授权上限扩展为真实 provider 500 次、Vitis 500 次；该授权替代先前的 200/200 上限。开始 R5 前必须先从授权发生后的 machine-readable artifacts 重建已消耗计数，已经发生的调用继续计入总账，不能假定 500 次仍全部可用。

预算估算器必须计算：

```text
provider_upper_bound =
  sum(case x arm x repeat 的 initial generation + advisor + possible mutation)

vitis_upper_bound =
  sum(case x arm x repeat 的 main formal prefix + possible repair revalidation)
```

执行规则：

- deterministic phases 的真实调用计数必须为 0；
- pilot 和正式 campaign 共用同一全局 ledger；
- 先为失败重跑和 independent audit 保留安全余量；
- 若估算上界超过剩余授权，必须暂停并请求调整预算或协议；
- 不得通过静默减少 repeats、删除困难案例或跳过 arm 来伪造完成。

P1 退出条件：设计 JSON canonical hash 固定，所有 arm 差异可由 machine-readable validator 证明，预算公式可对具体 dataset 给出上界。

### P2：建立真实 evidence inventory 和 campaign dataset

目标：先知道有哪些可用事实，再决定哪些结论能做。

输入至少包括：

- R1 corpus/evidence 归档和 receipt；
- accepted identity-complete R2 calibration bundle；
- Package D v1.6 baseline/canary/episode/audit；
- 仓库 committed deterministic fixtures；
- 可验证的历史真实 Vitis runs。

动作：

1. 建立 immutable evidence index，不移动或修改原归档；
2. 对每条记录写入 source hash、test provenance、Target、toolchain/parser、model/prompt、time、owner/failure family、E-level；
3. 标注 synthetic、real、invalid、duplicate 和 superseded；
4. 只从时间点 K 以前的合格记录构建 history；
5. 按 source hash 去重后再做 source-level holdout；
6. future 集在 snapshot K 冻结后保持不可见；
7. common deterministic、common open-world 和真正 OOD 分层；
8. 对每个 primary family 验证 history/future/negative-control 是否足够。

新增产物建议：

```text
artifacts/r5/evidence_inventory.json
artifacts/r5/dataset_manifest.json
artifacts/r5/split_manifest.json
artifacts/r5/inventory_audit.json
```

仓库中已有 corpus schema/writer，但当前没有在仓库或 `/data` 浅层发现可直接作为 R5 权威输入的 `corpus_manifest.json`。因此 P2 是实际工作，不得用三个 Package D canary 或 synthetic fixtures 冒充完整 dataset。

停止条件：

- 找不到 R1 权威归档或 hash 无法验证；
- source/time split 会泄漏；
- primary family 没有足够独立样本；
- episode 无法与 execution identity 对齐。

遇到上述情况先补数据或缩小且重写 claim，经协议重新冻结后再继续，不得事后移动样本。

### P3：实现持久、append-only episode ledger 与 outcome reducer

目标：让“持续记忆”从进程内数据结构变成跨运行事实源。

现状：

- `DiagnosticEpisode` 适合 R3 shadow，且明确禁止 repair authorization/after hash；
- `R4RepairEpisode` 可记录实际 mutation 与 formal outcome；
- `EpisodeStore` 只保存当前进程内的 `dict`；
- 两种 episode 尚无统一的只读 inventory/manifest 层。

实现原则：

1. 保留 R3 shadow 和 R4 repair 两种 schema，不强行合并字段；
2. 增加 typed envelope，明确 `episode_kind`、schema version、payload hash、execution identity hash；
3. file-backed ledger 只能 append，写入使用原子 rename 和 collision check；
4. reader 校验 canonical hash、lineage、schema、identity、authority 和 artifact refs；
5. manifest 只保存允许的 agent-safe summary，不保存 raw provider response/private reasoning；
6. reducer 把 outcome 归为 verified_positive、verified_negative、abstained、inconclusive、invalid_evidence；
7. verified_negative 必须同时证明 failure attribution 和 environment/toolchain/identity exclusion；
8. invalid/inconclusive 永不参与 promotion；
9. future/Hidden firewall 在 ingestion 前执行，违规记录隔离且 revision quarantine。

实现落点应靠近现有 `agrefactor/recovery/`，复用 R4 的 `append_only_write` 和 canonical hashing，不另建数据库服务。

测试必须覆盖：重复 ID、hash mismatch、截断写、schema migration、lineage cycle、R3/R4 混合读取、非法 outcome promotion、Hidden/secret/raw reasoning 注入、source/time 越界和并发 append。

P3 退出条件：重启进程后能从只读 manifest 重建相同 episode 集和 hash；零 provider/Vitis 调用；原 R3/R4 tests 保持通过。

### P4：实现 pattern lifecycle reducer 和 snapshot builder

目标：让历史正负 outcome 真正决定 pattern 的适用范围和生命周期。

新增运行能力：

- 从 P3 ledger 选择时间点 K 以前的 eligible episodes；
- 按 failure family、stage、owner、target/toolchain/interface/test 条件聚合；
- 计算 independent context、positive/negative、false repair、unsafe scope、citation validity；
- 生成 immutable child revision，不原地修改 parent；
- 根据 P1 冻结阈值执行 Quarantined -> Provisional -> Trusted；
- false repair、negative transfer、authority violation 或 leakage 触发 quarantine/deprecation；
- 生成 snapshot K、exclusion manifest、conflict/sparsity/OOD context；
- snapshot reader 只按 snapshot 内容解析，不读取 future 目录。

阈值不得散落在 reducer 源码里。它们必须来自 P1 的 versioned lifecycle policy，并在 revision 中记录 `threshold_source`。

当前 `SnapshotRevisionSource` 只按 stage/owner 找唯一 revision。P4 必须补充：

- failure family 和 evidence predicate 匹配；
- exact exclusions 优先于 similarity；
- 多个同强度 candidate revision 的 conflict abstain；
- negative support 和 source independence；
- OOD/sparse 的稳定 reason codes；
- selection trace 和 rejected candidate hashes。

若 history 无法产生至少一个满足协议的 Trusted revision，不允许手工造一个 Trusted revision来跑 A4-A6。应将其记录为 data insufficiency，返回 P2 或调整并重新冻结研究问题。

P4 退出条件：给定同一 ledger 和 policy，revision DAG、snapshot K、Gate context 和 hashes 完全可重建；future/source leakage tests 全部为零。

### P5：闭合 memory payload 到现有 Candidate repair 的可信链

目标：使 A4-A6 与 A2 的区别不只是“Gate 是否允许”，而是修复模型确实消费经过审计的历史经验。

新增最小 typed payload 建议包含：

```text
snippet_id
schema_version
revision_id / revision_sha256
repair_intent_or_recipe
supported_when
avoid_when
candidate_only_scope
source_episode_hashes
evidence_refs
payload_sha256
```

约束：

- payload 只包含 agent-safe、Candidate-only 的修复意图/recipe；
- 不包含 Hidden、Testbench、Original mutation、future outcome、raw response 或 private reasoning；
- snippet 内容不能塞进 `supported_when` 等条件字段冒充 schema；
- authorization 必须同时绑定 revision hash、snapshot hash 和 payload manifest hash；
- Gate 只批准 snapshot K 中的 payload；
- mutation adapter 将批准后的 snippets 传给已有 `CandidateRepairPromptInputs.approved_memory_snippets`；
- prompt manifest 和 trace 记录 snippet count/hash，不记录被禁止的原始内容；
- auditor 复核 prompt manifest、authorization 和 payload hash 一致；
- snippet 只影响 proposal，不获得成功权威。

A2 必须传空 memory；A3 传 similarity retrieval manifest；A4 只传 positive-only gated payload；A5 加入 negative/exclusion guidance；A6 使用完整 lifecycle 后的 payload。相同 Candidate、advisory、model、prompt template、seed 和 budget 下，只改变该机制维度。

P5 测试重点：hash substitution、stale snapshot、未授权 snippet、多个 snippet 顺序、Hidden 注入、negative exclusion 丢失、A2 非空 memory、prompt manifest 不一致、mutation failure 的 provider accounting。

P5 退出条件：集成测试能证明 model-visible prompt 与 authorization 精确一致，且 full validation/auditor 仍是唯一成功权威。

### P6：以 `refactor` 为主接入现有三入口体系

目标：把研究功能接入真实产品流程但不替换原流程。R5 research profiles 的主要功能消费者是 `refactor`；`full` 复用该 refactor phase；direct `optimize` 只承担 supporting subsystem 的兼容和无回归责任。

#### `refactor`

```text
initial generation / Public TB / Hidden-after-Candidate
-> existing formal validation
-> deterministic known Candidate/Testbench recovery first
-> only unknown/review Public evidence enters R2
-> calibrated advisory or abstain
-> optional memory retrieval/Gate according to internal research profile
-> at most one Candidate-only mutation
-> existing fresh formal prefix + Hidden terminal evaluation
-> independent evidence audit
```

#### `full`

复用同一 refactor phase。只有 refactor 得到 accepted material 后才进入现有 optimizer；必须验证 Candidate、reference、Target、Public/Hidden suites、provenance、BudgetManager 和 TraceRecorder 的交接。memory/advisor 权限不得泄漏为 optimizer 的额外修改权限。`full` 在当前路线的重点是组合正确性，不是新增一套研究机制。

#### direct `optimize`

保留现有 qualification、`safe-v1` optimizer 和 recovery 语义。R5 不强制把 refactor 专用的 R2-R4 repair 注入 direct optimize；不实现 `dynamic-v1`，不新增 optimizer-specific memory arm，也不把优化结果纳入当前论文的主贡献。必须做回归，证明 internal profile 不会创建旁路、污染 cache 或改变默认行为。若发现阻断正确性或 `full` 交接的缺陷，只做有证据的最小修复；更大范围的 optimizer 调试留在 V2.3 主线完成之后。

配置规则：

- 不新增第四个命令，不复制 CLI，不新增第二套 Vitis flow；
- 产品默认仍为 `off`；
- research package/runner 通过冻结的内部 manifest 构造现有 request/API；R5 profiles 只作用于 `refactor` 以及 `full` 的 refactor 子阶段；
- `off`、`shadow`、`advisor-repair`、`similarity-only`、`positive-gated`、`positive-negative-gated`、`full-lifecycle` 是内部 profile，不是新产品入口；
- config 进入 execution identity 和 artifact hash；未知字段 fail closed。

真实入口合同测试至少包括：

1. known Candidate error：原 deterministic repair，R2 call=0；
2. known Testbench error：原 TB repair，R2 call=0；
3. unknown + insufficient evidence：abstain，main result/candidate unchanged；
4. calibrated high + no memory A2：一次 Candidate mutation；
5. calibrated high + Gate accept A4-A6：一次带 hash-bound snippets 的 mutation；
6. Gate conflict/OOD：mutation=0；
7. Hidden terminal failure：model exposure=0；
8. `full` 成功交接 optimizer，失败不交接；
9. direct `optimize` 默认行为无回归。

P6 退出条件：三个原入口仍是唯一入口；`refactor` 可按冻结 profile 完成 selective R2-R5 路径；`full` 的 refactor-to-optimize handoff 正确；direct `optimize` 保持 `safe-v1` 且无回归；默认关闭时行为/状态/预算/trace 等价；启用 research profile 时只在允许节点产生差异。

### P7：确定性验证和全量回归

目标：在消耗真实预算前关闭所有合同、接线和污染风险。

测试层级：

1. schema/unit：ledger、reducer、revision、snapshot、payload、authorization；
2. negative/security：Hidden、secret、private reasoning、hash substitution、stale/future/source leakage；
3. arm-difference：逐字段证明 A0-A6 只有冻结机制开关不同；
4. orchestrator integration：route、FSM、Policy、Ledger、Budget、best_correct、one-attempt；
5. three-entry integration：`refactor`、`optimize`、`full`；
6. campaign：case isolation、counterbalanced order、resume、timeout、fail-soft、global budget；
7. full deterministic regression。

必须输出：

```text
PROVIDER_CALLS=0
VITIS_LAUNCHES=0
GIT_HISTORY_MUTATIONS=0
PACKAGE_SELF_ACCEPTANCE=false
```

除了现有 test suite，还应新增一个 machine-readable protocol validator，检查：arm 差异、dataset/snapshot hash、预算上界、state transition 和 authority document 一致性。

P7 退出条件：全量回归 clean、独立 file-only audit clean、无文档/状态漂移、worktree 只含预期改动。

### P8：小规模真实 pilot

目标：用最小真实调用发现 execution contract 问题，不做 efficacy 宣称。

执行顺序：

1. 运行预算估算器并冻结 pilot manifest；
2. 先对一个合格 case 跑 A0-A6 wiring smoke；
3. 再选少量 history/future/source-held-out case；
4. counterbalanced arm order，固定 model/provider/prompt/seed/timeout；
5. 每个结果立即写 append-only evidence，并由独立进程审计；
6. 失败按 classification 进入最小修复环，不能直接扩大样例特判。

建议 pilot 上限只在预算 ledger 证明可容纳时启用，并为正式 R5 保留大部分预算。具体次数由 P1 估算器和剩余额度决定，不在本文凭空承诺。

故障分类和处理：

| 类型 | 处理 |
|---|---|
| provider/model identity drift | 阻断；重新校准或固定 provider identity，不放宽 parser |
| Vitis/environment failure | 标 environmental/inconclusive；修环境后重跑，不记 negative memory |
| evidence contract 缺陷 | 修通用 producer/consumer contract，补相邻链测试 |
| sample-specific parser 缺陷 | 先证明稳定错误族；否则保持 unknown，不按编号特判 |
| authority/leakage/hash violation | critical；kill switch、quarantine、停止 pilot |
| high abstention on common family | 返回 P2/P3/P4 查数据和证据，不把它包装成安全成功 |
| budget upper bound exceeded | 停止并请求新的明确授权 |

pilot 退出条件：所有 arm 可执行或按协议拒答；无 critical finding；计数与 ledger 一致；没有发现需要改变 RQ/arm 定义的设计缺陷。若 arm 定义要变，回 P1 重新冻结，旧 pilot 只作调试证据。

### P9：执行并验收 R5 A0-A6 campaign

前提：P1-P8 全部通过，dataset/snapshot/code commit 均冻结。

执行要求：

- 同一 paired case 的 source/test/Target/tool/model/prompt/seed/timeout/parallelism/repeats 相同；
- arm 顺序 counterbalanced，case workspace/cache 隔离；
- history T0..Tk 构建 snapshot K，future Tk+1..Tn 在 K 冻结后执行；
- future outcome 只进入下一周期 ledger，不回写当前 K；
- case-level 与 source-level holdout 同时报告；
- 每个 arm 的实际 provider/Vitis/token/cost/wall time 单独计数；
- stop rule 在 campaign 开始前固定，critical safety finding 立即停止。

必须报告的结果：

- owner/failure-class、unknown detection、citation validity、calibration；
- attempted、verified positive/negative、full-chain pass；
- false repair、negative transfer、semantic weakening；
- Gate accept/reject/abstain、conflict、sparsity、OOD；
- promotion、quarantine、deprecation、revision churn；
- invalid/inconclusive/environmental/infrastructure failure；
- Hidden/secret/private reasoning leak 和 authority violation；
- provider/Vitis/tokens/cost/tool/wall-time 分布与 worst case。

R5 不是以“修复率越高越好”单一裁决。必须同时证明：

1. A2 对 A1 的 repair 增量可解释；
2. A3 对 A2 的 similarity memory 影响可解释；
3. A4 对 A3 的 positive Gate 影响可解释；
4. A5 对 A4 的 negative/conflict 治理影响可解释；
5. A6 对 A5 的 lifecycle 长期影响可解释；
6. safety critical 指标没有被平均性能掩盖。

R5 完成门：protocol/hash/snapshot 可重建；A0-A6 差异清楚；time/source/cache leakage 为零；预算公平；独立 auditor clean；项目负责人明确接受。代理不能自行接受 R5。

### P10：R6 正式实验、论文和 release 冻结

R6 只能在 R5 被项目负责人接受后开始。

先冻结：

- code commit/tag 和 authority index；
- corpus/episode/revision/snapshot；
- source/test/Target/toolchain/model/prompt identity；
- arms/budget/seed/timeout/repeats；
- inclusion/exclusion/invalid evidence/stop rules；
- statistical method 和 machine-readable reducer。

然后执行正式 repeated real matrix，生成可由 machine-readable artifacts 重建的表格和图。R6 不再在看到结果后修改机制；若发现实现 bug，正式 experiment freeze 失效，修复后必须产生新 protocol/version 并完整重跑受影响矩阵。

R6 最终产物：

```text
frozen release tag
authority index
dataset/snapshot/arm manifests
raw-safe evidence archive + SHA256
independent audit
machine-readable metrics
table/plot rebuild scripts
paper claim-to-evidence matrix
negative results and non-claims
```

论文结论限制为 Vitis HLS 2023.2 上的 bounded empirical study，不声称 universal HLS repair、跨版本普适性、模型权重持续学习或稳定 PPA 优势。

## 8. 每个阶段的提交与证据纪律

未来直接在服务器推进时，每一阶段采用以下固定工作方式：

1. `source /data/agrefactorpp_env.sh`；
2. 需要真实 provider 时再 `source /home/user/.config/agrefactor/provider.env`；
3. 核对 branch、HEAD、worktree 和目标 phase；
4. 先运行最小相关测试，再运行相邻回归，最后运行全量回归；
5. 真实调用前先生成 budget estimate 和 frozen manifest；
6. 所有输出写独立 artifact root，不覆盖旧证据；
7. 审计进程只读 evidence，不调用 provider/Vitis；
8. 每个 coherent phase 一个 commit；
9. push 后核对本地/远端 HEAD；
10. 只有 phase gate 才生成正式可执行验收包，不为每个小 bug 增殖 Package；
11. 不自动删除分支、历史归档或旧证据；
12. 不在失败后用 `git reset --hard` 等破坏性方式回滚用户工作。

提交顺序建议：

```text
P0 docs: accept R4 and open R5 design gate
P1 docs: freeze R5 continual-memory campaign protocol
P2 data: freeze R5 evidence inventory and temporal split
P3 feat: add persistent typed episode ledger
P4 feat: derive versioned memory lifecycle snapshots
P5 feat: bind gated memory payloads to Candidate repair
P6 feat: add default-off R5 profiles to existing orchestration
P7 test: close deterministic R5 protocol and entrypoint regression
P8 evidence: record R5 real pilot and independent audit
P9 evidence: freeze and accept R5 A0-A6 campaign
P10 release: freeze V2.3 experiments and reproducible evidence
```

实际 message 可按实现调整，但不得把多个 acceptance gate 混在一个提交中。

## 9. 全局停止条件

出现以下任一情况必须停止当前真实执行，保留证据并先处理根因：

- provider 或 Vitis 总调用达到授权上限；
- 预算估算无法容纳完整 mutation 后重验证；
- Hidden/secret/private reasoning/raw response 进入持久证据或模型输入；
- source/time/cache leakage；
- AI 修改 Testbench/Original/configuration 或取得 success authority；
- formal result 与 auditor 结论冲突；
- git branch/HEAD/dirty state 与 frozen manifest 不符；
- 需要改变 V2.3 核心研究问题、A0-A6 定义或三个入口边界；
- 外部 provider/toolchain identity 无法安全判断；
- 为继续实验必须事后删除失败样本或放宽 primary endpoint。

普通实现 bug、测试失败、个别案例 abstain 或结果不显著不自动构成项目阻塞；应按最小范围修复、重新冻结受影响协议并继续。负结果本身是需要报告的研究结果。

## 10. 项目完成判定

只有同时满足以下条件，才能说 V2.3 项目路线完成：

1. R4、R5 均有项目负责人明确 acceptance；
2. ordinary/default-off 与三个原入口行为无回归；
3. R2 open-world diagnosis、R3/R5 continual memory 和 R4 Candidate repair 是同一真实 orchestrator 的 producer-consumer 链；
4. episode 可跨运行持久化，pattern lifecycle 可由真实正负 outcome 重建；
5. memory payload 被 Gate 批准、authorization hash 绑定并被现有 Candidate prompt 真正消费；
6. A0-A6 paired、time-ordered、source-held-out 实验可重建；
7. false repair、negative transfer、abstention、invalid evidence 和成本均如实报告；
8. independent auditor 无 critical finding；
9. R6 的表格、图和论文 claim 可从冻结 artifact 重建；
10. release tag、authority index、文档和证据 hash 一致。

## 11. 当前立即下一步

项目负责人已经正式接受 R4。本计划随 R4 authority sync 提交完成 P0；该提交之后的唯一正确动作是：

```text
P1 冻结 R5 设计、A0-A6、数据和预算协议
-> P2 冻结真实 evidence inventory 与时间/source split
-> 在零真实调用验证通过前不启动 pilot
```

当前不得：

- 自行接受 R5 或启动 R6；
- 在 P1-P7 完成前启动真实 A0-A6 campaign；
- 运行新的 provider/Vitis 调用；
- 另造 CLI/Vitis flow；
- 把 R4 的两个 positive canary 宣称为持续学习或广泛实用性；
- 继续为单个日志编号增加样例特判。

本计划在 P1 design freeze 时转化为 machine-readable milestone/checklist；后续如果真实代码或证据推翻本文假设，必须先更新事实、写明原因和影响，再调整后续步骤，不能默默偏离 V2.3 路线。
