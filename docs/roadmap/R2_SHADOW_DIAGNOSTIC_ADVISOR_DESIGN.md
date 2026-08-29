# V2.3 R2 Shadow Diagnostic Advisor Design

状态：设计冻结；尚未实现；尚未验收。  
路线：V2.3；前置门：R0、R1-Safety、R1-Data 已接受。  
当前实现基线：R1 权威一致性修正后的 `research-roadmap-v2.3`，HEAD 前缀 `cd71447`。  
主要实证环境：Vitis HLS 2023.2。

## 1. 研究问题

R1 已经把确定性验证、Testbench 安全修复、证据投影和 Hidden 隔离边界冻结下来，但未知/混合/需要人工复核的 Public 物理失败仍只能保守停留在 review。R2 研究一个受限问题：在不授予模型状态机、成功判定或修改权限的前提下，provider-backed advisor 能否从完整的 Public physical evidence 中给出可审计的 owner、failure class 和证据引用，并且在 shadow 模式下保持与 deterministic 主路径等价。

R2 的论文对象是“受约束的诊断建议”，不是自动修复器，也不是持续学习或记忆系统。R2 的任何正面结果都只能说明在冻结的 Vitis 2023.2 数据和协议上，advisor 的诊断质量及安全降级行为可被测量；不能外推为通用 HLS 修复能力。

## 2. 真实代码基线与接线边界

当前代码已经存在以下可复用基础：

| 现有组件 | 真实职责 | R2 允许的使用方式 |
|---|---|---|
| `agrefactor/evidence/diagnostic_event.py` | 从 typed Public feedback 投影 agent-safe `DiagnosticEvent` | 作为唯一输入适配边界；不得加入 Hidden 内容 |
| `agrefactor/recovery/advisory.py` | `DiagnosticAdvisoryRequest/DiagnosticAdvisory` schema 和结果校验 | 作为输出合同；保留 `accepted=false` 不可绕过 |
| `agrefactor/recovery/policy.py` | deterministic recovery eligibility 和 advisory candidate-only policy | 只读消费；R2 不修改 FSM 权威 |
| `agrefactor/runtime/budget.py` | 硬预算和计数权威 | 增加独立 shadow reserve 的可审计消费，不创建第二预算权威 |
| `agrefactor/runtime/trace.py`、execution identity | 运行、调用和身份追踪 | 保存 shadow 与 main 分开的 identity/counter |
| `validation_orchestrator` / `candidate_repair_integration` | 固定验证顺序和终态 | R2 不改变 route/status/Candidate/best pointer |

当前普通验证接线把 `llm_advisory_mode` 设为 `off`；现有类型和测试不等于 R2 已完成。R2 实现只能增加相邻的 shadow adapter/reducer，并通过显式 feature flag 进入，默认仍关闭。

## 3. 触发合同

Advisor 只有在以下条件同时成立时才可被调用：

1. 终止阶段为 Public CSIM、CSYNTH 或 Public COSIM；
2. deterministic owner 为 unknown 或结果要求 review；
3. 已生成完整 `DiagnosticEvent`，其输入是 `agent_safe` 且含 blocking physical evidence；
4. run identity、Candidate identity、Public suite identity、Target/toolchain fingerprint 完整；
5. R1 corpus 记录及 provenance 可验证；
6. shadow token/cost/wall-time reserve 足够。

以下情形必须在 provider 调用前拒绝并保持 deterministic 结果：Preflight Testbench 失败、Hidden evaluation、PROVIDED Testbench 语义编辑、身份不完整、仅 infrastructure failure、缺少物理工具启动证据、证据不完整或 shadow reserve 不足。

## 4. 输入和输出

### 4.1 输入

输入只允许包含事件 ID、阶段/owner/failure-class 的 typed 摘要、允许引用的 Public evidence IDs、Target/toolchain 的非敏感 fingerprint、Candidate/Public suite hash 和运行身份。不得包含 source 正文、Hidden 路径/摘要/指纹、密钥、环境变量值、私有推理或未 allowlist 的原始工具日志。

### 4.2 输出

允许字段：`suspected_owner`、`suspected_failure_class`、`evidence_refs`、`repair_scope`、`confidence`、`abstain_reason`，以及仅记录意图而不执行的 `bounded_repair_intent`。

禁止字段：`accepted` 为 true、transition、FSM node、Hidden detail、secret、private reasoning、raw source patch、Testbench authorization。任何缺字段、未知枚举、额外字段、JSON 无效、引用越界、scope 越权或 `accepted=true` 都必须归一化为 shadow failure/abstain；主路径结果不变。

模型自报 `high` 不能直接授权后续动作。confidence 只能由预先冻结的 calibration split 评估，R2 不执行 Candidate repair，故 R2 的 `bounded_repair_intent` 永远是未执行记录。

## 5. Shadow 隔离和不变量

每次运行必须分别记录 deterministic main 与 shadow 的：provider calls、tokens、cost、wall time、timeouts/errors、route/status、Candidate hash、RecoveryLedger count、repair count 和 best-correct pointer。共享 `BudgetManager` 时，调用前必须预留独立 shadow quota；quota explanation 仍是解释，不产生第二权威。

R2 的强不变量：

- shadow 开关不改变主路径 route、status、terminal decision 或 validation order；
- shadow 不写 Candidate、Testbench、Hidden，不触发 repair，不改变 best_correct；
- provider error/timeout/invalid output/越权引用均为 abstain；
- `DiagnosticAdvisory.accepted` 恒为 false；
- deterministic 与 shadow 的主结果 identity/hash 必须等价；
- auditor 可从 artifact 重建输入、输出、预算和降级原因；
- 所有 Hidden/secret/private reasoning fail-closed。

## 6. 实现边界（供下一包使用）

R2 实施包应包含但不限于：

1. `ShadowDiagnosticRequestAdapter`：从 terminal Public outcome 构造 agent-safe request；
2. provider adapter：固定 provider/model identity，禁止自动路由和 fallback 改写；
3. strict parser/validator：字段 allowlist、引用闭集、scope/owner 合同和失败归一化；
4. `ShadowAccounting`：独立 reserve、token/cost/wall-time/error 记录并接入现有 trace/identity；
5. equivalence reducer：比较 shadow on/off 的 deterministic route/status/hash/ledger/best pointer；
6. calibration evaluator：冻结 split、阈值、置信区间和 abstention coverage-risk；
7. provider-independent audit artifact：不依赖模型自报结果即可复核安全不变量。

默认 feature flag 为 off；没有新的 provider 证据时不得改变普通 `refactor`、`optimize` 或 `full` 的行为。

## 7. 评测协议

R2 必须先冻结 protocol/hash，再运行 provider。最低报告：owner macro-F1 或 balanced accuracy、failure-class macro-F1、citation validity、abstention coverage-risk、high-confidence error rate、unsafe scope proposal rate、invalid-output/timeout/provider-error 降级率、shadow/main decision equivalence，以及 token/cost/wall-time。

必须包含的负例：invalid JSON、unknown owner、错误 evidence ref、Hidden ref、Testbench scope、`accepted=true`、provider timeout、budget block。真实证据至少包含 R1 corpus 中 identity-complete 的 Vitis 2023.2 Public unknown/review records；E2 synthetic fixture 只能验证合同，不能冒充真实模型或 Vitis 结果。

主结果与 shadow 结果必须使用同一 source/test/TargetProfile/toolchain/model/prompt/seed/timeout/parallelism；只改变 shadow 开关。R2 不做 memory retrieval、pattern promotion、自动修复、跨版本实验或正式消融矩阵。

## 8. 完成门和停止规则

R2 只有在以下条件全部满足后才能接受：provider artifact、独立 shadow reserve、真实 Vitis unknown/review evidence、冻结 calibration、main/shadow equivalence、负例降级和 independent audit 均通过。任何 Hidden leak、authority violation、Candidate/hash 变化、best_correct 变化、budget bypass、未校准 high-confidence 错误或 false success 都是 critical stop，保留原始归档并禁止进入 R3。

R2 设计阶段本身的完成定义是：本文件和 `V2_3_R2_DESIGN.json` 已写入仓库，状态文档明确 `design_frozen`、`R2_STARTED=false`、`R2_ACCEPTED=false`，产品代码变更为零，且设计审计通过。该完成定义不等于 R2 实现或论文结果接受。

## 9. 非目标和后续授权

非目标包括 Memory Gate、episode/pattern lifecycle、Candidate/Testbench repair、FSM mutation、toolchain migration、dynamic model routing 和 cross-version generalization。外部接受本设计冻结后，下一步唯一合法指针为 `V2.3-R2-implementation`；实现包仍需单独执行、验收和人工提交。
