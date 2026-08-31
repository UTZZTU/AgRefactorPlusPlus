# V2.3 R3 条件化正负记忆与 Applicability Gate 设计

状态：设计冻结；尚未实现；已通过独立外部验收。
路线：V2.3；前置门：R0、R1-Safety、R1-Data、R2 已接受。
实现基线：R2 shadow-only advisor 已接受的 `research-roadmap-v2.3`。

## 1. 目标与非目标

R3 为 shadow-only 研究层：把 R2 的确定性诊断、advisory、证据引用和验证结果记录为不可变 `DiagnosticEpisode`，形成带正负归因的版本化 `RepairPatternRevision`，并计算仅供审计的 Applicability Gate 决策。R3 不执行 repair，不写 Candidate，不修改 RecoveryLedger、best_correct、deterministic FSM、Testbench 权限或主路径预算。

实现必须复用现有 `DiagnosticEventProjector`、`DiagnosticAdvisory`、`RecoveryPolicy`、`BudgetManager`、`TraceRecorder` 和 identity/evidence 合同；新增 store、aggregator、gate 时只能作为相邻模块，不重做主流程。

## 2. 不可变 Episode 合同

每个 episode 是一次 pattern revision 在一个具体 context 上的 application。建议采用冻结的 typed record（实现时可用 `dataclass(frozen=True)` 或等价不可变结构），序列化后以 canonical JSON 计算 `episode_hash`。必需字段：

- `schema_version`, `episode_id`, `created_at`, `parent_episode_id`, `lineage`；
- `event_ref`, `execution_identity`, `request`, `context_signature`（target/toolchain/parser/interface/stage/scope）；
- deterministic diagnosis（owner、failure class、reason codes、evidence refs）；
- advisory summary（仅允许白名单字段，`accepted` 恒为 false）；
- retrieved revision ids、gate decision/reasons/evidence refs；
- repair authorization（R3 必须为 `not_requested`）；
- before/after identity hashes（R3 after 必须为空或 unchanged）；
- full revalidation reference、budget delta、outcome、outcome attribution refs；
- `episode_hash` 和 `writer_version`。

任何写入后更新都产生新 episode 和 `parent_episode_id`，不得原地覆盖。缺少 identity、hash、authority 或 evidence contract 时只能写 `invalid_evidence`，不得参与 promotion。

## 3. Outcome 归因

- `verified_positive`：已授权的合法 Candidate change、完整真实验证链通过、semantic contract 未削弱、identity 完整且独立 auditor clean；模型声明、compile pass 或单个 return code 均不足以产生该结果。
- `verified_negative`：确有 Gate/Policy 授权，失败可归因于该 revision，并排除 toolchain、environment、identity 和 infrastructure。
- `abstained`：advisor 或 Gate 拒答/不授权，未执行 repair。
- `inconclusive`：预算、工具、超时、identity 或证据冲突导致无法归因。
- `invalid_evidence`：Hidden/secret 泄漏、hash/provenance/authority/artifact contract 违规。

R3 运行中通常只会产生 `abstained`、`inconclusive` 或 advisory calibration 记录；不得把 shadow 观察伪装成 repair outcome。

## 4. Pattern revision 与生命周期

`RepairPatternRevision` 必须包含：`revision_id`、`parent_revision_id`、`revision_hash`、`supported_when`、`avoid_when`、target/toolchain/interface/test exclusions、required evidence predicates、positive/negative episode refs、calibration refs、created_at、lifecycle。

生命周期固定为 `Quarantined -> Provisional -> Trusted -> Deprecated`，任一阶段可转 `Rejected`。样本不足、冲突或 citation 无法验证时只能保持 Quarantined/Provisional。promotion 前必须从历史窗口或预注册 calibration split 读取并冻结以下阈值，禁止查看 future evaluation window 后调参：

`min_positive_episode_count`, `min_independent_context_count`, `max_attributable_negative_rate`, `max_false_repair_rate`, `max_unsafe_scope_rate`, `min_evidence_citation_validity`, `min_calibration_requirement`, `deprecation_window`。

设计契约只记录阈值来源、版本和哈希；若来源缺失，值为 `null` 且 promotion 禁止。

## 5. Applicability Gate 固定顺序

Gate 输入只能来自 agent-safe typed fields，输出只能是 `accept`、`reject` 或 `abstain`，并附稳定 reason codes、revision ids 和 evidence refs。顺序不可改变：

1. Hidden/secret/identity incomplete：hard reject；
2. role/stage/scope 不支持：reject；
3. exact target/toolchain/parser/interface/test exclusions；
4. evidence completeness；
5. `avoid_when`；
6. positive/negative support；
7. conflict、sparsity、OOD；
8. calibrated risk threshold；
9. 写入 decision、reasons、evidence refs 和 gate contract hash。

embedding similarity 不能替代 exact exclusion；同强度正负冲突必须 abstain。R3 的 `accept` 仍是 shadow 观察，不得被解释为 repair authorization。

## 6. Feature firewall 与泄漏防护

允许 feature：target/toolchain/parser identity、stage、deterministic owner/failure class、agent-safe contract/hash、历史窗口 episode statistics。禁止：Hidden content/oracle/path、secret、future outcome、private reasoning、raw provider response、raw exception、similarity-only authorization，以及从 aggregate 反推 owner/failure class、scope 或授权。

Legacy cache 必须按 execution identity 隔离；source-level holdout、时间切分和 case isolation 在聚合前执行。任何 firewall violation 产生 `invalid_evidence` 并 quarantine 相关 revision。

## 7. 设计验收计划

设计包验收只验证契约和文档写入，不声称 R3 accepted。实现前必须准备负例测试：episode immutability、lineage、A-positive/B-negative、action attribution、deprecated/rejected、sample sparsity、time leakage、source-level holdout、Legacy cache isolation、Hidden/secret firewall、conflict/OOD abstention、R3 不触发 repair/FSM 的回归证明。实现验收还需 shadow/main deterministic equivalence、无 provider/Vitis/Git 副作用和独立外部审计。

## 8. 与后续阶段的边界

只有 R3 设计和实现分别通过独立验收后，R4 才可在 Policy、Ledger、budget reserve、canary 和 kill switch 同时证明的前提下开放 Candidate-only repair。R5 才进行多案例、时间顺序和因果可识别消融；R6 才进入正式实验、论文和发布。
