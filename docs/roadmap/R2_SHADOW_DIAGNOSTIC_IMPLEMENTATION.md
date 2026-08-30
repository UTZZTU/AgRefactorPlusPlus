# V2.3 R2 Shadow Diagnostic Advisor Implementation

状态：实现完成，已通过独立外部验证与验收；R2 已接受。

实现基线：`research-roadmap-v2.3`，HEAD 前缀 `2bc253a`。
设计合同：`R2_SHADOW_DIAGNOSTIC_ADVISOR_DESIGN.md` 和 `V2_3_R2_DESIGN.json`。

## 1. 实现范围

R2 新增 `agrefactor/recovery/shadow_advisor.py`，并通过显式依赖注入把它接到 `CandidateRepairValidationOrchestrator`。默认 `shadow_advisor=None`，因此普通 refactor、optimize 和 full 路径保持关闭。只有调用方显式提供 advisor，且确定性流程产生身份完整的 Public unknown/review `DiagnosticEvent` 时，provider 才可能被调用。

实现包括：

- Public CSIM/CSYNTH/Public COSIM unknown/review 触发门；
- `DiagnosticEvent` 到 agent-safe request 的唯一适配边界；
- 固定 provider/model、严格 JSON allowlist、引用闭集和 scope 校验；
- provider timeout/error、预算阻断、非法输出和越权输出的 abstain 降级；
- shared `BudgetManager` 硬预算与独立 `ShadowReserve` 配额；
- provider calls、tokens、cost、wall time、timeout/error 的 shadow accounting；
- route/status/Candidate hash/RecoveryLedger/repair count/best pointer 等价性 reducer；
- provider-independent audit artifact；
- 冻结 calibration split、macro-F1、citation validity、coverage-risk、high-confidence error、unsafe scope 和 Wilson 95% interval 计算。

## 2. 权威与隔离

Shadow 调用发生在确定性验证和修复流程完成后。其返回值只写入 `metadata.r2_shadow_diagnostics`，不送回 routing、repair loop、FSM 或 acceptance 判定。`DiagnosticAdvisory.accepted` 恒为 false，`bounded_repair_intent_executed` 恒为 false。

以下输入在 provider 前拒绝：Preflight、Hidden/operator-full、非 unknown/review、身份不完整、无物理工具启动、证据不完整、infrastructure-only 和非 Public suite。以下输出统一降级：未知字段、缺字段、无效 JSON、未知枚举、越界引用、unknown owner 不 abstain、Testbench scope、非 Candidate repair scope、模型身份不一致和非法 usage。

Provider 是外部失败边界；其异常不会进入确定性主路径。Trace 写入失败同样不能改变主结果。共享 `BudgetManager` 仍是唯一硬预算权威，`ShadowReserve` 只缩小 shadow 自身允许消耗，不扩张全局预算。

## 3. 验证边界

实施包执行 focused R2 合同测试与完整回归。focused 测试使用 fake provider，不产生模型费用，不产生新的 Vitis 运行，也不能替代真实 provider/Vitis 验证。

R2 后续外部验证必须冻结 calibration record IDs/hash，并使用 R1 corpus 中 identity-complete 的 Vitis HLS 2023.2 Public unknown/review 记录。报告至少包括 owner/failure-class macro-F1、citation validity、abstention coverage-risk、high-confidence error、unsafe scope、降级率、main/shadow equivalence 和 token/cost/wall time。

## 4. 停止门

下列任一情况为 critical stop：Hidden/secret 泄漏、`accepted=true`、FSM/route/status/Candidate/RecoveryLedger/repair count/best pointer 变化、预算绕过、Testbench 授权、未冻结 calibration 或把 synthetic 结果声明为真实 provider/Vitis 证据。

本实现包不执行 Memory Gate、episode/pattern promotion、Candidate/Testbench repair、FSM mutation、toolchain migration、dynamic model routing 或 cross-version claim。独立外部验证和审计已通过，`R2_ACCEPTED=true`；R3 设计尚未开始。


## 5. 独立外部验收记录

- validation archive: `agrefactor_v23_r2_shadow_diagnostic_external_validation_correction_v7_20260830T143607Z_614600.tar.gz`
- archive SHA-256: `d0b1147596bc8e14695608ca74ce4f719e67f0419279e27eb4f745cc1dabea6c`
- real Vitis unknown/review cases: 3
- real provider calls: 3
- focused R2 tests: passed
- shadow/main equivalence: true
- Hidden source reads, repair, FSM and Git history mutations: none
- R2 status: `accepted_independent_external_review`
- next step: `V2.3-R3-design-only`
