# P3 实施计划：案例记录与自动经验整理

> - 版本：评审稿 v0.1
> - 日期：2026-10-15
> - 状态：供评审；尚未获 P3 实施授权
> - 代码基线：服务器 `/data/AgRefactor`，原始基线分支 `backup/server-current-20261013`，提交 `5721150f401f2bdec93518b5ec95c9261da3d3d0`；P2 实现已完成，并随本次备份提交纳入该分支
> - 对应大纲：`docs/roadmap/后续项目推进计划_证据驱动诊断与经验库_讨论稿_v0.3.md` 第七节 P3

## 目标

在 P2 已建立的数据合同上，接入一次阻塞故障的完整记录：诊断事件、实际派发的修复尝试、每次尝试后的验证结果，以及最终结案状态。案例结案后由本地确定性代码整理经验，允许成功案例生成正向经验，也允许已解决案例中的明确动作级失败生成范围狭窄的负向经验。整理可以重跑且不会重复写入。

P3 不接入经验检索、提示词注入或 AI 辅助诊断；不改变目录规则、owner 判断、修复资格、验证合同或现有修复路由；不把 P2 的额度 API 接入生产调用者。它为后续 P4/P5/P6 提供可追溯的数据，不提前实施那些阶段的行为。

## P2 基线与代码依据

P2 已新增 `DiagnosticCase`、`CaseAttempt`、`EvidenceCheckResult`、`OwnerAssessment`、`RepairEligibilitySnapshot` 与 `ExperienceRecord` 合同，以及 `RecoveryLedger` 的组件额度预留/提交接口。此前 P2 实施验证记录为 99 项通过；2026-10-15 按 7 个指定模块复跑 87 项通过。额度接口目前没有真实修复入口调用者，P3 不更改这点。

| 现有代码 | P3 复用方式 | 当前缺口 |
|---|---|---|
| `agrefactor/evidence/case_record.py` | 使用 P2 的 Case/Attempt/Experience 类型和验证；保持 owner assessment 与授权快照分离 | 没有持久化存储和运行期生命周期管理 |
| `agrefactor/evidence/diagnostic_event.py` | `DiagnosticEvent` 是已有 agent-safe 结构化诊断，且拒绝正式 Hidden evaluation | 事件目前主要在修复流程结束时聚合写出，未关联到 Case |
| `agrefactor/repair/protocol.py`、`agrefactor/repair/artifacts.py` | 复用 agent-safe `RepairAttemptRecord`、`RepairRunRecord` 和原子 JSON 工件写入 | 安全修复记录不含实际代码差异；不得把 prompt 或模型响应复制到经验库 |
| `agrefactor/repair/candidate_loop.py` | 每次 Candidate 修复已有 before/after 候选代码、proposal 和验证结果，可在内存边界计算差异 | 缺少供 CaseRecorder 使用的逐次观察点 |
| `agrefactor/runtime/candidate_repair_integration.py` | 汇总 Candidate 验证、Public Testbench 修复、诊断事件和最终状态，是主要 Case 生命周期边界 | 只在 `_finish` 聚合元数据，没有 Case open/append/close |
| `agrefactor/runtime/repair_phase.py` | 复用阶段工件 manifest 和原子写入约定 | 当前 manifest 没有 Case/Experience 工件引用 |
| `flow/tools/tb_optimizer.py`、`agrefactor/testing/testbench_repair.py` | 分别覆盖生成资格修正与正式 Testbench 修复，可作为 Public/Hidden-generation 与 Stub 的局部适配点 | trajectory 计数仍属局部实现；P3 只记录动作，不统一额度 |

P2 工作区中的 `case_record.py` 已实现正/负经验的基本约束：来源案例必须已结案且解决；正向来源需有改变并通过验证的 attempt；负向来源需有改变且明确验证失败的 attempt。P3 在此基础上定义“何时自动整理”和安全持久化，不另造一套 Case/Experience schema。

## 最新一批运行记录的参照

只读抽样了 `/data/agrefactor_runs` 中按最近修改时间排序的前 8 组运行目录：`contract_observation_repair_20261012`、`leetcode_serial_current_20261012`、`feedback_precision_repair_20261011`、`app_type_decl_timeout_repair_20261011`、`app_current_default_serial_20261010`、`ctc_tb_qr_repair_20261010`、`hlsrewritter_helper_contract_repair_20261010` 和 `hlsrewritter_current_default_20261010`。统计只使用不含 Hidden 路径的 `agent_safe` repair/diagnostic 工件；没有读取 `orchestration_result.json`、原始日志、完整源码、模型回复或 Hidden 内容。

这 8 组中有 7 组包含所检查的结构化工件，共 68 条 `RepairRunRecord`、82 次 Attempt 和 40 条 DiagnosticEvent：Candidate 16 个 repair run/30 次 Attempt，Testbench 52 个 repair run/52 次 Attempt。Candidate repair run 有 9 个终态成功、7 个额度耗尽；Testbench repair run 有 46 个终态成功、6 个失败。DiagnosticEvent 覆盖 Preflight 6 条、CSYNTH 3 条、Public Evaluation 20 条和 Public COSIM 11 条；owner 为 Candidate 的 15 条、`unknown` 的 25 条。这里统计的是修复记录和事件，不等于 Case 数量。

抽样路径展示了需要覆盖的形态：

- `/data/agrefactor_runs/leetcode_serial_current_20261012/work_break_ii/refactor/repair_artifacts/repair_run.json`：Candidate 连续 3 次有差异的 Attempt 后耗尽；同一 refactor 运行另有多条 Public COSIM 诊断事件且 owner 为 `unknown`。应形成一个 unresolved Case，保留诊断时序，不生成 Experience。
- `/data/agrefactor_runs/app_current_default_serial_20261010/idct_generic/refactor/repair_artifacts/repair_run.json`：Candidate 有一次实际差异且终态成功。若其前后源码差异与通过验证能可靠关联，应生成 resolved Case 和正向经验。
- `/data/agrefactor_runs/contract_observation_repair_20261012/remove_invalid_parentheses/bootstrap/public_testbench_repair/repair_artifacts/repair_run.json`：Testbench repair run 终态成功，但记录的 `changed` 为 false。仅凭终态成功不能生成改动经验；P3 必须对照真实 before/after 工件验证是否有差异，若差异或验证关联不可核验则不生成 Experience。

这批数据修正了 P3 的关键实现要求：旧 Testbench `RepairAttemptRecord.changed` 字段和 repair run 终态都不能单独作为“改动通过/失败”的事实来源。派发请求照常记录为 Attempt；是否有改动必须以 P3 在运行时对受修组件的 before/after 内容计算出的 hash/diff 为准，是否成功必须关联到该次或累计改动之后的实际验证事件。若旧运行没有可验证的源码差异或事件关联，只作为结构样例，不导入经验库。

## 案例生命周期

1. **创建条件。** 只有当前运行出现被现有诊断/验证流程识别的阻塞故障时才创建 Case。全程成功、没有阻塞故障的运行不生成 Case。创建时保留当前 owner，包括 `unknown`；可选责任线索只作为线索保存。
2. **案例边界。** 一个 Case 覆盖同一根运行中的一条连续故障恢复链，允许跨 CSYNTH、CSIM、COSIM 和其中实际触发的修复组件继续追加事件与尝试。当前阻塞被最终验证接受后结案；如果修复被拒绝、没有修复资格、验证/安全前提失效、预算耗尽或运行以其他失败状态结束，则结案为 unresolved，并记录具体结束原因。新启动的根运行创建新 Case；只有使用同一持久化运行身份恢复同一个 open Case 时才沿用原 `case_id`。
3. **Attempt 口径。** 只有通过本地资格门并实际派发给 repairer/provider 的修复请求才是 CaseAttempt。派发前被拒绝、预检本身、资格检查或未调用模型的诊断步骤都不是 Attempt。派发边界先以稳定 `attempt_id` 写入 `dispatched` 状态，收到结果和验证后更新同一条 Attempt；如果进程在派发后中断，恢复时保留该未完成 Attempt 并标记为中断/结果未知，不自动重放同一请求。请求已派发但最终无文件差异仍记录 Attempt；实际差异由 P3 对比受修组件的 before/after 内容计算，不只沿用旧记录的 `changed` 值。无差异不得生成正/负改动经验。
4. **逐次关联。** 每个 Attempt 关联修复前最近的诊断事件、派发授权引用、组件/可见性类别、修复调用记录引用，以及其后实际运行的验证事件。验证失败只更新这次 Attempt；不把单次失败直接等同于案例失败，也不把 owner 的变化压成一个最终标签。
5. **结案。** 只有系统现有验证合同对本次要求范围给出最终通过时才标记 resolved。结案状态由本地流程结果确定，不由 AI 或经验推断。owner 可以在 resolved Case 中仍为 unknown；不得为生成经验而补造根因。

## 自动经验整理规则

经验整理只处理已结案 Case，并采用结构化字段和实际改动差异，不调用 LLM。

- **正向经验：** Case 已 resolved；所选改动经 before/after hash/diff 证明确实改变了受修组件；验证事件明确关联该次改动或累计改动，并记录实际通过的阶段/合同。修复 run 的 `succeeded` 状态不足以单独生成经验。经验描述“这些改动在这些条件下通过了这些验证”，不宣称它是唯一根因或适用于所有相似错误。
- **负向经验：** Case 最终 resolved；某一具体 Attempt 的 before/after diff 非空，且其后关联的验证明确未通过。负向内容只记录该 Attempt 的精确差异指纹、当时条件和失败验证，不推广成“Candidate 修复无效”“某类策略永远失败”等结论。后续成功的 Attempt 单独形成正向经验；中间失败不会抹掉最终成功。若日志只有失败状态、没有可核验 diff 与验证引用，则只保存 Case/Attempt，不生成负向经验。
- **不产生可检索经验：** unresolved Case、`unknown` 且没有可验证动作结果、权限拒绝、无改动请求、只有预检、证据引用不完整、动作与验证无法一一关联的记录。Case 本身仍可留存，供以后人工复核或补充证据。
- **经验摘要与改动片段：** `action_summary` 由可核验的组件、动作记录和改动范围生成，不进行根因生成。为 P4 保留真实关键 diff hunk 与 SHA-256 指纹，来源为实际修复前后代码；不把整个项目、完整 prompt、模型回复、绝对工作目录或完整源文件放进跨案例经验库。`ExperienceRecord.evidence_refs` 引用独立的改动工件，避免复制到多处。
- **验证范围：** 每条经验保存验证阶段、合同/报告引用、源码版本 hash、源 Case/Attempt ID 和适用条件。只验证过 CSIM 的改动不能标记为 CSYNTH/COSIM 已验证。
- **幂等去重：** `experience_id` 由来源 Case、来源 Attempt 集合、正/负极性、源码版本、验证引用和改动指纹确定。相同结案的重复整理得到相同 ID；内容相同则 no-op，内容冲突则报错并保留已有记录，不覆盖旧经验。案例、事件和 Attempt 写入也按稳定 ID 去重。

## 存储与安全

复用现有 agent-safe 投影、hash 和原子 JSON 写入方式；不直接复用旧 R3/R5 episode/lifecycle schema。建议目录按现有根运行产物定位，JSON 中只存稳定 ID 和 hash，不存任意服务器路径：

```text
<root-run-artifact>/cases/<case_id>/case.json
<root-run-artifact>/cases/<case_id>/events/<event_id>.json
<root-run-artifact>/cases/<case_id>/changes/<attempt_id>.json
${AGREFACTOR_RUN_DIR}/diagnostic_experiences/records/<experience_id>.json
${AGREFACTOR_RUN_DIR}/diagnostic_experiences/changes/<change_fingerprint>.json
```

Case 更新采用同目录临时文件、flush/fsync、原子替换；同一 Case 的更新串行化，按 `event_id`/`attempt_id` 幂等追加。Experience 与 diff 工件采用稳定 ID 的 create-if-absent；重跑发现相同内容则复用，发现不一致则拒绝覆盖。普通成功运行不创建空目录或空记录。

**Hidden 边界：** 正式 Hidden evaluation 事件继续由 `DiagnosticEvent` 投影器拒绝。Hidden-generation 的局部修正可由既有任务流程继续执行，但其生成代码、响应、差异、反馈和轨迹不得进入跨 Case Experience Store，也不能出现在后续提示词数据中；如任务本地为恢复需要保留账务/尝试状态，只保留现有流程允许的任务内引用和摘要，且不跨任务读取。Public/Candidate 的经验只使用已允许的 agent-safe 数据。P3 不改变 Hidden 资格或修复权限。

## 预计改动范围

- 新增 `agrefactor/evidence/case_store.py`：Case 与事件/改动工件的原子持久化、恢复 open Case、ID 去重。
- 新增 `agrefactor/evidence/experience_derivation.py`：无模型调用的正/负经验整理及幂等 ID 生成。
- 最小调整 `agrefactor/evidence/case_record.py`：若实现发现当前 `evidence_refs` 不能无歧义引用 diff 工件，再增加显式 `change_artifact_ref` 并升级 schema；默认优先沿用现有字段，避免无必要 schema 变更。
- 更新 `agrefactor/evidence/__init__.py`：导出实际需要的 store/deriver 类型。
- 调整 `agrefactor/repair/candidate_loop.py` 与 `agrefactor/runtime/candidate_repair_integration.py`：在实际派发与验证结果边界向 CaseRecorder 提供每次 Attempt 和差异；在已有终结路径统一 close Case。
- 调整 `agrefactor/testing/testbench_repair.py` / `agrefactor/runtime/candidate_repair_integration.py`：为正式 Public Testbench 修复沿用同一 Case 和逐次验证引用。
- 为 `flow/tools/tb_optimizer.py` 加最窄的观察接点：记录 Public Testbench/Stub 生成资格修正的实际动作；Hidden-generation 仅留任务本地状态，不生成跨案例差异/Experience。不得改变 trajectory、修复次数或资格合同。
- 调整 `agrefactor/runtime/repair_phase.py`：把 Case/Experience 工件引用加入现有 run artifact manifest；保留既有诊断事件和修复工件。
- 新增聚焦测试；不删除、不重命名、不激活旧 R3/R5 代码。

实现时若发现 `tb_optimizer.py` 无法通过窄观察接点取得可靠的 Public/Stub 改动与验证关联，应先完成 Candidate 与正式 Public Testbench 两条路径，记录缺口并暂停扩展；不得以猜测内容生成经验或绕过 Hidden 边界。

## 实施步骤

1. **先完成存储测试。** 添加 CaseStore/ExperienceStore 的原子更新、open Case 加载、重复事件/Attempt 去重、重启读取和并发更新测试；证明现有 P2 dataclass 可以往返序列化。
2. **接 Candidate 生命周期。** 从首次阻塞诊断创建 Case；逐次记录实际派发的 Candidate 修复和验证；最终通过/未解决分别结案。使用真实 CaseRecord/RepairAttemptRecord，不复制模型响应。
3. **接正式 Testbench 修复。** 将 Public Testbench 修复挂到同一故障 Case；验证 `preflight` 或资格拒绝不会生成 Attempt，实际派发后才记录。Testbench 可见性与组件类别只用于准确记录，不创建新权限。
4. **接生成资格修正与 Stub 的安全记录。** 在能从现有 trajectory 结果可靠提取 Public 改动的前提下，记录 Public Testbench/Stub action；组合变更作为一个逻辑 Attempt、组件字段同时指出两个目标。Hidden-generation 仅本任务内保留现有必要状态，不导出经验。此步骤只增加观察/记录，不改局部计数。
5. **添加经验整理。** Case close 后调用确定性 deriver；只处理满足上节规则的 action/verification 对；写入变更 diff 工件和 ExperienceRecord。通过重复 close/重复 deriver 证明不重复生成。
6. **连接输出并回归。** 将 case/experience 引用加入产物 manifest；跑新增定向测试和现有 Candidate/Testbench/Hidden 边界回归。检查 `git diff --check`、工作区差异和写出的安全 payload。

## 验收条件

- 无故障的正常运行不会生成 Case；阻塞故障可以创建并结案，即使没有获得修复资格。
- 一条跨阶段修复链只有一个 Case；每个真实派发 Attempt 均按顺序关联修复前诊断、授权引用和修复后验证；预检、资格检查和派发前拒绝不伪装成 Attempt。
- 最新批次中的 Candidate 3 次改动后耗尽样例可重建为一个 unresolved Case，保留阶段和 `unknown`/owner 变化，且不生成 Experience。
- Candidate 改动后通过时，Case resolved；正向经验引用经实际 before/after 差异确认的改动和验证范围。明确失败的中间改动只能生成精确动作级负向经验；不得将一次中间失败推广成整体策略失败。
- Testbench repair run 即使终态为 `succeeded`，若无法从 before/after 工件确认真实差异和验证关联，也不生成正向经验；不得依赖旧 `changed` 字段或终态标签猜测。
- 无改动、无验证关联、引用缺失或根因未知且无动作结果时不生成经验；Case 中的 `unknown` 不被自动改写。
- 结案和经验整理重复执行不创建重复记录；Case reopen 使用原 `case_id`，同一 Attempt 不重复加入；新根运行获得新 Case ID。
- 正式 Hidden evaluation 反馈不会进入 Case/Experience；Hidden-generation 的代码、diff、响应和反馈不会离开任务作用域或进入跨案例库。
- Testbench Public 修复与允许的生成资格修正能记录为 Testbench 动作；涉及 Stub 时同时记录两个组件，但 P3 不改其额度。所有现有权限和修复行为保持不变。
- 旧 RecoveryLedger/repair loop 行为和 P2 99 项测试集不回归；P3 新增代码不调用 LLM、不访问网络、不执行服务器 shell 收集证据。

## 验证计划

先运行新增纯本地测试和现有 P2/修复协议回归；再跑现有 Candidate 与 Testbench 集成测试，以及 Hidden-generation 只用于边界/隔离断言的定向测试。P3 不需要调用 Vitis/外部模型来验证经验整理。

候选测试集（具体命令在实施前依实际测试模块名确认；服务器命令先 source 环境文件）：

```bash
source /data/agrefactorpp_env.sh
cd /data/AgRefactor
PYTHONPATH=tests python -m unittest \
  tests.test_case_record \
  tests.test_case_store \
  tests.test_experience_derivation \
  tests.test_candidate_repair_loop \
  tests.test_candidate_repair_integration \
  tests.test_testbench_repair \
  tests.test_lightweight_hidden_testbench_contract_recovery \
  tests.test_lightweight_hidden_stub_recovery \
  tests.test_hidden_generated_differential_recovery \
  tests.test_recovery_authority \
  tests.test_repair_protocol
git diff --check
```

验收报告同时列出 P2 原有 99 项通过、P3 新增/受影响测试数、正向/负向/不生成经验的用例结果，以及检查过的 Hidden 投影边界。最新批次选出的脱敏结构样例应覆盖 Candidate 成功、Candidate 耗尽、多阶段/owner 变化、Testbench 终态成功但无可验证差异、无修复 Attempt 等情形。只引用事件类别、尝试数量、结局和 hash，不把模型响应或 Hidden 内容复制进 fixtures。

## 风险与评审点

- **真实源码差异和安全修复记录分离。** 现有 `RepairAttemptRecord` 不含代码 diff。P3 必须从运行中的 before/after 候选生成最小 diff 工件，并只通过稳定引用挂接；禁止从含 prompt/模型响应的完整 orchestration JSON 直接导入 Experience Store。
- **跨阶段事件是否属于同一 Case。** 本稿建议以一次根运行中的连续阻塞恢复链为一个 Case，以首次阻塞时落盘的 ID 作为恢复标识；不同根运行始终新建 Case。若实际入口不能恢复原根运行身份，则先让 CaseStore 的 ID/状态合同落地，不能仅因 task/sample 相同而合并案例。
- **经验表述的边界。** 自动整理只做可验证事实的结构化汇总。负向记录表达“这个精确差异没有通过该验证”，不宣称它导致根因；后续检索是否把某种负向记录列为参考，属于 P4 的准入与排序设计。
- **P3 与 P6 的账本依赖。** P3 记录授权/额度类别引用，但不接入新组件账本，不把 `quota_buckets` 的描述性字段当成真实扣额。P6 才负责实际修复请求统一扣账和恢复计数。
- **Testbench trajectory 接入范围。** `tb_optimizer` 生成资格修正路径数据结构更分散。若观察接点无法证明实际改动与验证结果的关联，就将该路径留在既有本地记录中，不生成经验，并在实现前回报范围差异，不猜测归因。

**请重点评审：** 是否接受以“根运行中的连续阻塞恢复链”为 Case 边界；是否接受 P3 的正/负经验只描述动作与验证结果、不生成根因；是否接受 Hidden-generation 只保留任务内状态、不进入跨案例经验库。这三点确认后即可按上述步骤实施，P3 结束后再单独准备 P4 计划。
