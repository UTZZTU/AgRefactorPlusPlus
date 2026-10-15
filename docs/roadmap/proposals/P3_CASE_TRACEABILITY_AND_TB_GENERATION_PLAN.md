# P3 补充实施计划：授权追溯与 Public 生成轨迹

> - 版本：评审稿 v0.1
> - 日期：2026-10-16
> - 状态：已按授权实施并通过定向回归；未提交、未推送
> - 服务器目标路径：`/data/AgRefactor/docs/roadmap/proposals/P3_CASE_TRACEABILITY_AND_TB_GENERATION_PLAN.md`
> - 主计划：`P3_CASE_CAPTURE_AND_EXPERIENCE_DERIVATION_PLAN.md`

## 目标

补齐 P3 执行后留下的两处追溯缺口：

1. 让每条已派发的 `CaseAttempt` 能指向真实的本地授权/资格决定，并把额度或 RecoveryLedger 决定另行记录，避免用诊断事件或本地生成的占位 ID 冒充授权来源。
2. 让 Public `tb_optimizer` 的 Testbench/Stub 生成修正按请求关联到具体尝试、组件差异和验证结果；目前只保留 trajectory 聚合结果，无法可靠整理经验。

目标是补齐记录与关联，不改变现有责任判断、授权边界、模型调用顺序、trajectory 数、额度、修复行为或验证合同。Hidden-generation 继续留在任务内，不进入 Case/Experience Store。

## 实施前代码事实（审计基线）

| 位置 | 当前行为 | 对补充计划的影响 |
|---|---|---|
| `agrefactor/runtime/repair_phase.py` | `CaseRecorder` 在 Candidate Repair Phase 中创建，路径位于该 phase 的 `cases/` 工件下。 | 初始 Public Testbench/Stub 生成发生在更早的 `flow/new.py`，目前没有共享的 Case 生命周期。 |
| `agrefactor/runtime/candidate_repair_integration.py` | Candidate route 已选定后创建 repair loop；正式 Public Testbench 路径会先通过 `auto_public_bounded` 门，再在 `before_repair` 中预留 RecoveryLedger 并记录派发。 | 这两条路径已经有实际资格/路由对象，可作为授权快照的来源；须把来源对象序列化并与 Attempt 关联。 |
| `agrefactor/repair/candidate_loop.py` | `dispatch_observer` 在 `model_adapter.generate()` 前调用；Candidate 的 `RecoveryLedger.reserve()` 在模型返回提案后调用，之后才进入提案处理。 | Candidate 的派发资格/路由决定与稍后的 RecoveryLedger 决定是两个不同事件，不能混写成同一个“授权事件”。P3 不移动 `reserve()`，以免改变行为和额度时序。 |
| `agrefactor/recovery/policy.py` | `RecoveryLedger.reserve()` 返回 `RecoveryDecision`；ledger event 有递增 `sequence`，但没有独立稳定 `event_id`。 | 需要用 Case/Attempt 稳定标识持久化实际决策快照，并单独保存 ledger 决定引用；不能只记录一个无法解析的序号。 |
| `flow/new.py` 与 `flow/tools/tb_optimizer.py` | Public `gen_tb_with_coverage()` 在 Candidate 生成前执行；一个 Public generation 调用可并发运行多个 trajectory。`make_golden_hidden_tb()` 在 Candidate 生成之后独立执行。 | Public generation 需要一个上游 Case 接点和并发安全记录；Hidden generation 必须继续隔离。 |
| `flow/tools/tb_optimizer.py` | `record_artifact_event()` 目前只累计 request/response/contract-check 计数；trajectory round 保存最终 Testbench、Stub 与资格结果。`_request_cpp_artifact()` 的回调只传事件名，不提供逐请求改动和验证关联。 | 需要窄化扩展请求级观察；不能从最终 round 倒推中间请求各自改了什么、是否通过。 |
| `flow/tools/tb_optimizer.py` | `TestbenchGenerationExhausted` 提供 code-free 的 trajectory/diagnostic 摘要；Public 与 Hidden 都可产生该异常。 | 可复用现有摘要构造 Public generation Case 事件；必须再次校验 `split == public`，不可把 Hidden 摘要或引用写入跨案例存储。 |

上述事实来自服务器当前工作树；工作树含有 P3 未提交修改和既有备份/临时文件。本计划的后续实施须在该状态上增量修改，不覆盖或清理无关内容。

## 补充 A：Attempt 的授权追溯

### 记录合同

- `CaseAttempt.authorization_ref` 继续指向该 Attempt 对应的 `RepairEligibilitySnapshot.decision_ref`。
- `RepairEligibilitySnapshot.authorization_ref` 填入一个可读取的、持久化的本地授权决定快照引用。快照记录决定来源、阶段、组件、可见性、决定结果、适用的 owner/资格字段及其证据引用；不复制 prompt、模型响应或完整源码。
- RecoveryLedger 的额度/策略决定单独作为关联记录保存。`CaseAttempt.ledger_event_ref` 已存在，直接复用该字段指向可读取的 ledger 决定快照；不新增重复的 `recovery_decision_ref`。
- 拒绝、额度不足或资格失败仍然不是 `CaseAttempt`；如现有流程已能提供该拒绝决定，可作为 Case event 留痕。不得为了记录而把拒绝请求伪装成已派发 Attempt。
- Attempt、授权快照和 ledger 决定均使用可从 `case_id`、`attempt_id` 与事件类型稳定复算的 ID；快照内容冲突时拒绝覆盖，遵循现有 CaseStore 幂等约定。

### 各路径的来源

**Candidate：** 派发前的实际 `FeedbackRouteDecision` 是当前 provider 请求的路由/资格来源，应在现有 `dispatch_observer` 调用时快照。它与修复前 `DiagnosticEvent` 分开存放。Candidate `RecoveryLedger.reserve()` 发生在模型已返回提案之后；其 `RecoveryDecision` 作为后续政策/额度检查结果另行链接，不能声称它授权了此前的 provider 调用。

**正式 Public Testbench：** 保存路由决定、`testbench_revision_authorization == auto_public_bounded` 的本地资格结果，以及 `before_repair` 中实际返回的 RecoveryDecision。此路径的 ledger reserve 位于 TestbenchRepairLoop 派发之前，可以与 Attempt 的授权快照准确关联。

若某路径无法提供真实的决定对象或足够字段，`authorization_ref` 保持缺失并使测试失败，先查清调用边界；不得退回到“根据诊断事件 ID 推测授权”或仅生成一个空壳 decision ID。实现时优先复用现有 route、资格和 ledger 对象，仅增加快照/引用传递。

### 明确不做

- 不将 `RecoveryLedger.reserve()` 前移到 Candidate 模型请求之前。
- 不改变 Candidate、Testbench 或 Stub 的修复资格、拒绝条件、quota bucket、额度数值和调用顺序。
- 不让授权快照授予新权限；它只记录现有流程真实作出的决定。

## 补充 B：Public `tb_optimizer` 逐请求 Case 记录

### Case 边界建议

- 范围限于生成型 Public 路径 `gen_tb_with_coverage()` / `optimize_tb_public()` 及其 Testbench/Stub 资格修正；不含外部提供 Testbench 的人工修正，也不含 `make_golden_hidden_tb()`。
- 成功且没有发生阻塞诊断的 Public 生成不创建 Case。第一次出现 agent-safe、明确阻塞的 Public qualification/contract 诊断时，创建一个 generation Case，并把现有 code-free 诊断摘要作为初始事件；之后是否获得修复资格只决定是否产生 Attempt，不决定是否保留这个 Case。
- 同一个 Public generation 调用中的多个并发 trajectory 视为同一待解决生成问题下的并列尝试，使用 `trajectory_idx` 和请求序号区分。若最终有一个 trajectory 通过完整资格合同并被接受，Case 以 resolved 结案；若最终 `TestbenchGenerationExhausted`，以 unresolved 结案。某个 trajectory 的失败不能单独把整个 Case 结为 unresolved。
- 同一运行后续出现的独立 Candidate/CSYNTH/CSIM/COSIM 阻塞仍由后续阶段另建 Case，不并入已结案的生成 Case。

### 请求、Attempt 与改动的区分

- 每次实际 provider 请求都写一个轻量 request event：稳定 request ID、trajectory、组件、request role、派发状态及结果状态；不保存原始 prompt 或原始模型响应。
- 只有由 repair-eligible 的 Public qualification/contract 诊断触发、通过现有本地门且实际派发的 `repair` 请求，才成为 `CaseAttempt`。初始生成、普通 refinement、格式重试作为同一流程的 request event，不伪装成独立的修复动作；格式重试不新增经验动作。
- Testbench 与 Stub 分别保存修复前后 SHA-256、受限 diff 工件引用及其对应的合同/coverage 验证结果。响应被拒绝或没有合法代码差异时仍可记录请求结果，但不生成改动经验。
- 一个明确的联合修复动作若同时修改 Testbench 和 Stub，记录一个 Attempt，并在组件/差异条目中分别列出两者；分别触发的动作则分别记录。多个底层 provider 请求可以作为该逻辑 Attempt 的子事件，避免把“一个联合动作”重复算成多个逻辑动作。
- Attempt 的正负结果必须来自该次 proposal 的局部合同检查或其后明确关联的 qualification/coverage 验证。不得把最终 trajectory 成功倒灌成每个中间请求都成功，也不得从最后一轮源码推测先前请求差异。

### 并发与存储

- 一个 generation Case 下的 Public trajectories 可能并发写入。CaseStore/CaseRecorder 必须为同一 Case 的分配序号、写 Attempt 和更新事件引用提供原子串行化；如果现有 Store 不具备该保证，先补 focused concurrency test，再选择最小的锁或追加日志方案。
- Attempt ID 由 generation Case、trajectory、逻辑动作序号和动作类型稳定生成；request event ID 另含组件及 provider-request 序号。恢复或重复汇总时幂等合并，不重复派发或重复创建经验。
- 复用 `tb_optimizer` 已写出的 Public trajectory/round 工件作为可验证来源；若要为中间 proposal 新增差异工件，只存经过筛选的 Public Testbench/Stub 代码差异和 hash，不存完整模型对话。
- Case 的写入落在该 Public generation 所属 run artifact 下，之后由原有经验整理器处理；不写入全局诊断经验目录，除非该 Case 按现有规则结案且确有可验证动作/验证关联。

### Hidden 隔离

- 不向 `make_golden_hidden_tb()` 传入跨 Case 的 `CaseRecorder` 或 ExperienceStore。
- `split` 必须由调用路径的可信本地参数指定，不接受模型输出或 trajectory 摘要自行声明 Public。
- Hidden generation 的 prompt、代码、diff、诊断/验证反馈、trajectory 与缓存引用均不写入 Case、Experience 或 Public 提示词；既有任务内资格流程和 `max_repairs` 不变。
- 增加反向测试：即使调用者错误地给 Hidden 结果附上 Public 标记，投影/记录器仍拒绝写入。

## 预计代码接点

- `agrefactor/evidence/case_record.py`：若需要区分 eligibility snapshot 与后续 RecoveryDecision，为其增加最小可选引用字段并验证序列化兼容。
- `agrefactor/evidence/case_capture.py`、`case_store.py`：接收真实决定快照；提供并发安全的 idempotent Case 事件/Attempt 追加。
- `agrefactor/repair/candidate_loop.py`、`agrefactor/runtime/candidate_repair_integration.py`：从已有对象把 Candidate/Testbench 的 pre-dispatch route/qualification 决定和实际 ledger 决定分别传入 recorder。
- `flow/new.py`、`flow/tools/tb_optimizer.py`：仅为 Public generated Testbench/Stub qualification 传入可选 recorder/observer；基于真实资格失败创建 Case；按请求捕获状态、改动和验证；不改 Hidden 调用。
- 测试：扩展 `tests/test_case_capture.py`、`tests/test_candidate_repair_loop.py`、`tests/test_unified_candidate_repair_phase.py`、`tests/test_testbench_repair.py`，并新增窄范围的 `tb_optimizer` generation event/case 测试。

只有实施前代码走查证明某个字段或接点不存在时，才扩大 schema 或新增薄适配层；不重写 CaseStore、RecoveryLedger、trajectory runner 或修复引擎。

## 实施步骤与验收

1. **授权对象追踪。** 为 Candidate 和正式 Public Testbench 写调用顺序测试，证明被引用的对象就是派发时实际使用的本地决定；ledger 决定另有引用。拒绝/额度耗尽分支不产生 Attempt。验收：每个已派发 P3 核心路径 Attempt 的授权引用都能解析，且不会解析到诊断事件。
2. **Public generation Case 接线。** 在 flow/new 与 optimizer 边界延迟创建 Case，首次观察到阻塞诊断时创建；资格拒绝仍保留 Case 但不产生 Attempt。没有阻塞诊断的运行不产生空 Case。验收：`gen_tb_with_coverage` 成功和 `TestbenchGenerationExhausted` 两种终态分别正确结案。
3. **逐请求记录与经验输入。** 用 mock provider/qualification 验证每个 `repair` 请求与 proposal diff、局部验证结果一一关联；格式重试只产生 request event；联合 Testbench/Stub 动作只产生一个逻辑 Attempt、同时记录两个组件。验收：未知/缺失关联不会生成经验。
4. **并发与 Hidden 边界。** 使用 `M > 1` 的 mock trajectories 并发写同一 Case，断言 Attempt/event 无丢失、ID 稳定、重复汇总幂等；验证 `make_golden_hidden_tb()` 无跨案例写入。
5. **回归。** 先跑新增纯本地测试，再跑现有 P3、Case/Experience、Candidate/Testbench 和 Hidden 边界定向测试，最后检查 `git diff --check`。所有用例用 mock，不调用外部模型或 Vitis，也不向共享全局 ExperienceStore 写测试数据。

总体通过标准是：现有 P3 两条核心修复路径都能追溯到真实授权决定；Public generation 的每个真实修复动作能还原其所属 Case、provider 请求、组件差异及对应资格结果；正常无故障运行不创建空记录；Hidden 数据不跨任务；既有运行结果、额度和权限行为不改变。

## 逐文件实施清单与验收命令

以下是四项设计口径确认后的执行范围。按序实施；每一步先有单元测试，再接运行时调用点。实现若发现真实调用顺序与上表不符，暂停该接点并报告证据，不顺手改修复行为。

| 文件 | 具体改动 | 验收重点 |
|---|---|---|
| `agrefactor/evidence/case_record.py` | 复用已有可选 `CaseAttempt.ledger_event_ref`；不增加重复字段。保持 `authorization_ref` 只指向 `RepairEligibilitySnapshot.decision_ref`。 | 旧 Case JSON 可读取；既有 ledger 引用继续往返稳定。 |
| `agrefactor/evidence/case_capture.py`、`case_store.py` | 复用 CaseStore 的事件持久化，增加保存/读取本地决定快照的窄接口；提供 `record_dispatch` 所需的实际 authorization payload 和后续 ledger payload。快照 ID 从 Case/Attempt/决定类型稳定生成；同 ID 内容冲突拒绝覆盖。为共享 Public generation Case 的追加操作加锁。 | 派发资格快照与 diagnostic event 分开；重复记录幂等；多线程追加不丢事件、sequence_index 不重复。 |
| `agrefactor/runtime/candidate_repair_integration.py` | 在 Candidate 派发前把当时实际使用的 `FeedbackRouteDecision`/资格元数据交给 recorder；在 ledger reserve 返回或拒绝后，将新增的真实 `RecoveryLedgerEvent.decision` 单独关联到该 Attempt。正式 Public Testbench 同样保存 `auto_public_bounded` 判断和 reserve 结果。 | Candidate route decision 与 ledger decision 有不同引用；Testbench reserve 被拒绝时不记已派发 Attempt；保留现有门和调用顺序。 |
| `agrefactor/repair/candidate_loop.py` | 仅扩展 dispatch observer 的可选上下文，使 CaseRecorder 能记录本次请求使用的实际 route decision；当前 observer 拒绝重放的行为不变。记录 post-response ledger decision 时不移动 `reserve()`。 | 原 observer 调用兼容；拒绝时不调用 provider；允许时先按现顺序派发，再记录后续 ledger 决定。 |
| `flow/new.py` | 在 Public generation 的既有 artifact root 下创建可选 recorder/observer，并只传给 Public `gen_tb_with_coverage()`；收集最终轨迹和异常摘要以结案。外部提供 Testbench 路径和 `make_golden_hidden_tb()` 不传 recorder。 | Public 生成阻塞能结案；正常无阻塞不留空 Case；Hidden 与外部输入路径没有 recorder。 |
| `flow/tools/tb_optimizer.py` | 添加可选的 Public-only request observer；区分 provider request event、format retry 和 repair Attempt；对每次 repair proposal 记录 Testbench/Stub hash/diff 与紧随其后的 contract/coverage outcome，并由现有 trajectory qualification 决定最终 Case outcome。多 trajectory 使用稳定 trajectory/request ID。 | 不从最终 round 倒推中间 proposal；format retry 只有 request event；联合 TB/Stub 修改只建一个逻辑 Attempt；`M > 1` 可并发写。 |
| `tests/test_case_record.py`、`tests/test_case_capture.py` | 增加授权快照引用、RecoveryDecision 独立引用、拒绝后不造 Attempt、并发及幂等用例。 | Case/Attempt 记录可以从 JSON 中完整追溯，不需依赖运行时内存对象。 |
| `tests/test_candidate_repair_loop.py`、`tests/test_unified_candidate_repair_phase.py`、`tests/test_testbench_repair.py` | 增加 Candidate/Testbench 实际授权对象传递、ledger 决定引用和原调用顺序断言。 | 既有资格拒绝、额度拒绝和 observer 阻止派发语义保持不变。 |
| 新增 `tests/test_public_tb_generation_case_capture.py` | 用 mock provider、mock qualification 和并发 trajectories 覆盖 Public generation Case、每次 repair proposal 差异/验证关联、最终通过/耗尽结案、格式重试以及 TB+Stub 联合动作。 | 不启动 Vitis、不访问外部模型、不写共享经验目录；重复汇总不重复生成。 |
| `tests/test_p0_hidden_boundary.py`、`tests/test_test_generation_exhaustion_contract.py` | 增加拒绝 Hidden-generation Case/Experience 投影，以及 Public/Hidden 分支不串线的断言。 | 即使错误地把 Hidden 摘要传给 Public observer，也不能写到跨案例存储。 |

### 服务器验收命令

每条服务器命令先 source 项目环境。新增测试完成后执行：

```bash
source /data/agrefactorpp_env.sh
cd /data/AgRefactor
PYTHONPATH=tests python -m unittest \
  test_case_record \
  test_case_store \
  test_experience_derivation \
  test_case_capture \
  test_candidate_repair_loop \
  test_unified_candidate_repair_phase \
  test_testbench_repair \
  test_public_tb_generation_case_capture \
  test_test_generation_exhaustion_contract \
  test_tb_optimizer_hardening \
  test_p0_hidden_boundary \
  test_p0_dual_generation_profiles
git diff --check
```

必须同时通过新增测试和上述定向回归；只要 Hidden 隔离、既有拒绝路径、额度或修复调用顺序有变化，就不算通过。结束时单独报告实际通过数、任何未运行的检查和 `git status --short`；不 commit/push，除非用户另行授权。

## 已确认决策

1. Candidate 的 pre-dispatch `FeedbackRouteDecision` 与后续 `RecoveryDecision` 分开记录；不移动 Candidate 的 ledger reserve。
2. 同一个 Public generation 调用中的并发 trajectory 共用一个 Case，由最终 generation 结果结案。
3. 格式重试只记为 request event，不单独记修复 Attempt 或经验动作。
4. 补充范围限定为生成型 Public Testbench/Stub；Hidden generation 与外部提供 Testbench 资格检查维持现状。

上述四项设计决策已由用户确认：Candidate 路由与 RecoveryLedger 决定分开记录；同次 Public generation 的并发 trajectory 共用一个 Case；格式重试只记 request event；补充范围先限定生成型 Public Testbench/Stub。

## 实施结果（2026-10-16）

- `CaseAttempt` 复用已有 `ledger_event_ref`，没有增加重复字段。Candidate 的派发路由/资格决定与后续 RecoveryLedger 决定分别快照并关联；Public Testbench 修复保存其实际 Public 资格结果及 reserve 结果。
- Public `tb_optimizer` 生成路径将并发 trajectory 关联到同一 Case。provider 请求、格式重试、Testbench/Stub proposal 差异、合成探针和 coverage 验证结果分别记录；联合 Testbench/Stub 修正保留一个逻辑 Attempt，并记录所有实际派发组件的额度类别，即使没有文件差异也计入对应组件。
- Hidden-generation 不传入 Public recorder，Hidden 轨迹及内容仍不进入跨案例 Case/Experience Store。修复资格、调用顺序、局部 `max_repairs` 额度行为和验证合同未改变；`tb_optimizer` 尚未接入统一 RecoveryLedger。
- 服务器定向回归 227 项通过，`git diff --check` 通过。覆盖 Case/Experience、Candidate/Testbench、Public generation 与 Hidden 边界；未调用外部模型或 Vitis。
- 实施前服务器文件备份位于 `/data/AgRefactor/backups/P3_SUPPLEMENT_TRACE_REVIEW_20261016_01/`。当前分支为 `backup/server-current-20261013`；本次未 commit 或 push。
