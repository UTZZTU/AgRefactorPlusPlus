# V2.3 R3 条件化正负记忆与 Applicability Gate 实现

状态：实现完成；已通过独立外部验证与独立外部验收。
实现基线：R3 设计已通过独立外部验收的 `research-roadmap-v2.3`。

## 实现范围

新增 `agrefactor/recovery/memory_gate.py` 作为现有 recovery/advisory 的相邻模块：

- 不可变、canonical SHA-256 的 `DiagnosticEpisode`；
- append-only `EpisodeStore`，重复 ID 和原地覆盖均拒绝；
- `EpisodeOutcome` 五态归因，只有完整验证链和独立审计才能形成 positive；
- 版本化 `RepairPatternRevision` 及 Quarantined/Provisional/Trusted/Deprecated/Rejected 生命周期；
- 固定顺序、fail-closed、shadow-only 的 `ApplicabilityGate`；
- 递归 Feature Firewall，拒绝 Hidden、secret、future outcome、raw response/exception 和 private reasoning；
- R3 中 repair authorization 固定为 `not_requested`，after hash 固定为空。

实现复用现有 `DiagnosticEvent`、`DiagnosticAdvisory`、identity/evidence 合同，但不修改 FSM、RecoveryPolicy、Candidate loop、Testbench 权限或主路径预算。R3 Gate 的 `accept` 只是审计结果，不是 repair 授权。

## 验证边界

包内测试为确定性合同测试，不调用模型、不启动 Vitis。独立外部验收必须在真实仓库上复核产品哈希、完整回归、shadow/main equivalence、时间/来源隔离和负例测试。R4 之前不得把 Gate 接入 Candidate repair 权限链。
