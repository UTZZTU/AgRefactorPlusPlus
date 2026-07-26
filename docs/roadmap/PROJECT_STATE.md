# AgRefactor++ Current Project State

<!-- PRE_STAGE3_FINAL_CLOSURE:BEGIN -->
## Final Pre-Stage-3 closure

```text
step6_execution_commit=67546b4c015f8505a5de72bc1b57159c5c1547fe
cleanup_implementation_commit=a4ee78ff38df864cadb444c39e24c1d96cdf2527
hidden_stub_recovery_commit=03d1ae702f50e3f9ff08a1950a7127ed44feef85
hidden_testbench_contract_recovery_commit=74699c63cbbdb0e9b30daf08343cb08400216374
lightweight_hidden_tool_recovery_commit=b33fe48cccc441a149b7a613770baba612485d75
P0_STATUS=accepted
STEP6_DUAL_MODE_REAL_DFS=passed
STEP7_FINAL_P0_SMOKE=accepted
CLEANUP_DEPRECATION_AUDIT=passed
DETERMINISTIC_REGRESSION=1484/1484
DOCUMENTATION_CONSISTENCY=passed
PRE_STAGE3_CLOSED=true
STAGE3_STARTED=false
NEXT_STEP=STAGE3_PLANNING
```

Evidence:

- [`P0_REAL_DFS_DUAL_MODE_ACCEPTANCE.md`](../acceptance/pre-stage3/P0_REAL_DFS_DUAL_MODE_ACCEPTANCE.md)
- [`PRE_STAGE3_DEPRECATION_LEDGER.md`](../audits/PRE_STAGE3_DEPRECATION_LEDGER.md)
- [`PRE_STAGE3_CLEANUP_AND_CLOSURE_ACCEPTANCE.md`](../acceptance/pre-stage3/PRE_STAGE3_CLEANUP_AND_CLOSURE_ACCEPTANCE.md)
- [`PRE_STAGE3_DOCUMENTATION_CONSISTENCY_ACCEPTANCE.md`](../acceptance/pre-stage3/PRE_STAGE3_DOCUMENTATION_CONSISTENCY_ACCEPTANCE.md)

Pre-Stage-3 is closed. Stage 3 is allowed but has not started.
<!-- PRE_STAGE3_FINAL_CLOSURE:END -->

> **Historical snapshot policy:** Later package-level blocks retain their as-of acceptance state. Lines such as `PRE_STAGE3_CLOSED=false`, `P0=not run`, or `NEXT=...` inside those blocks are historical evidence, not the current project state. The current authority is the final closure block above.


<!-- P2_SOURCE_ONLY_BOOTSTRAP:BEGIN -->
## P2 source-only bootstrap

The integrated P2 package is deterministically accepted:

```text
baseline=1334
full=1346/1346
new_tests=12
patch_id=af57008cd7db13e88400418fc95ac47baf157dc7
normal_refactor_command=implemented
advanced_run_task_json=preserved
public_hidden_plan_mapping=implemented
shared_run_budget=implemented_and_precall_closed
token_cost_soft_budget=observed_only
optimize_full_execution=gated_until_stage3
Execution Identity=next
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```

Evidence:
[`P2_SOURCE_ONLY_BOOTSTRAP_ACCEPTANCE.md`](../acceptance/pre-stage3/P2_SOURCE_ONLY_BOOTSTRAP_ACCEPTANCE.md).
<!-- P2_SOURCE_ONLY_BOOTSTRAP:END -->

<!-- P2_RUNTIME_BUDGET_CORRECTION:BEGIN -->
## P2 runtime-budget correction

```text
base=f2e325c7e0302e7166b647ad30f509d538b8182c
full=1352/1352
AG2 model launches=pre-call hard-budgeted
coverage compile/CSIM/gcov=pre-call hard-budgeted
TB signature CSYNTH=shared hard budget
concurrent reservations=atomic
post-hoc Token/Cost=no launch double count
P2 frozen contract=closed
Execution Identity=active next objective
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```
<!-- P2_RUNTIME_BUDGET_CORRECTION:END -->

<!-- EXECUTION_IDENTITY_CLOSURE:BEGIN -->
## Execution Identity authority reconciliation

```text
base=bc6b1b3a82b2ece0930391981f5cc9a238cd8046
full=1372/1372
new_tests=10
actual rendered Prompt identity=closed
post-run suite qualification/coverage identity=closed
actual CostEstimate quality identity=closed
safety-ceiling rejection identity=closed
Execution Identity frozen contract=closed
P5=active next objective
P0=not run
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```

Evidence:
[`EXECUTION_IDENTITY_ACCEPTANCE.md`](../acceptance/pre-stage3/EXECUTION_IDENTITY_ACCEPTANCE.md).
<!-- EXECUTION_IDENTITY_CLOSURE:END -->

> **新对话或恢复开发时首先阅读本文档。** 权威范围见 [`ROADMAP.md`](ROADMAP.md)，目标追踪见 [`GOAL_TRACEABILITY.md`](GOAL_TRACEABILITY.md)。

## 1. 当前快照

<!-- P1_P4_FROZEN_CONTRACT_RECONCILIATION:BEGIN -->
## P1/P4 frozen-contract reconciliation

The previous `completed` labels used narrower acceptance scopes than
[`PRE_STAGE3_PRODUCTIZATION_PLAN.md`](PRE_STAGE3_PRODUCTIZATION_PLAN.md).
The mismatch is now corrected in one integrated package:

```text
baseline=1312
full=1334/1334
patch_id=c7dacd1afe4ad4e67a635f9e63d225a847aaf326
P1 frozen contract=reconciled
P4 frozen contract=reconciled
P2=completed by the later integrated source-only package
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```

Evidence:
[`P1_P4_FROZEN_CONTRACT_RECONCILIATION.md`](../audits/P1_P4_FROZEN_CONTRACT_RECONCILIATION.md).
<!-- P1_P4_FROZEN_CONTRACT_RECONCILIATION:END -->

- 当前开发分支：`stage2-general-feedback`
- 当前功能代码基线：以 `stage2-general-feedback` 当前 HEAD 为准；不再在本状态文档复制容易过期的功能 SHA
- Stage 2 closure validation baseline：`3f57371c8b58f53449064219c024ab63042a87d4`
- Stage 2 状态：**closed**；Stage 3：**planning allowed, implementation not started**
- 最新确定性测试：**1484/1484 passed**
- 最新 Stage 2.5 总结：**7 baseline、7/7 real full chains、9 faults、16 labels、23 scenario executions；跨三次独立验收累计 62/36/9/17/0，0 LLM**
- 最新完整验证链验收仍为：**broken Candidate Preflight → local FakeProvider → repaired g++ Preflight → Vitis 2023.2 CSYNTH → Public CSIM → Hidden CSIM，shared exact budget 7/4/1/2 + 1 LLM call**
- Stage 1 Core 验收：[`stage1_core_acceptance.md`](../acceptance/stage1/stage1_core_acceptance.md)
- Testbench Reliability 验收：[`stage2_acceptance.md`](../acceptance/stage2/stage2_acceptance.md)
- Stage 2.3 Runtime Evidence 验收：[`stage2_runtime_evidence_acceptance.md`](../acceptance/stage2/stage2_runtime_evidence_acceptance.md)
- 当前关键任务：**Pre-Stage-3 已关闭；下一步为 Stage 3 planning，Stage 3 尚未开始**

<!-- PRE_STAGE3_PRODUCTIZATION_PLAN:BEGIN -->
## 1.1 Frozen Pre-Stage-3 productization plan

权威详细计划：
[`PRE_STAGE3_PRODUCTIZATION_PLAN.md`](PRE_STAGE3_PRODUCTIZATION_PLAN.md)。

关键决定：

- 普通 source 命令必须显式提供 `--top`；
- 普通产品命令为 `refactor / optimize / full`；
- `TaskSpec` 由普通入口内部生成并作为复现合同保存；
- 普通用户不选择 `--legacy / --repair-aware`；
- 首批静态模型家族为 DeepSeek、Kimi、GLM、MiniMax、Qwen 和
  Generic OpenAI-compatible；
- Public/Hidden 独立选择来源并支持多个 suite；
- 默认输出简洁，完整证据进入 artifacts；
- Execution Identity 是关闭前必交付；
- P0 必须经普通 source-only 入口和 Stage 2 正式后端；
- P0 后执行弃用清理，再进入 Stage 3。
<!-- PRE_STAGE3_PRODUCTIZATION_PLAN:END -->
<!-- PRE_STAGE3_BUDGET_PRICING_REFINEMENT:BEGIN -->
## Budget defaults, ceilings, and pricing

Frozen decisions:

- hard budgets have separate system defaults and system safety ceilings;
- a user may override the default only within the safety interval;
- Token and Cost are observed-only soft budgets in the current closure scope;
- `max_llm_calls` is the primary model-execution hard control;
- P1 stores official pricing provenance and estimation quality;
- P5 displays Token, LLM, compile, CSIM, CSYNTH, cost and wall time usage.
<!-- PRE_STAGE3_BUDGET_PRICING_REFINEMENT:END -->

<!-- P1_MODEL_RUNTIME_AUDIT_DECISIONS:BEGIN -->
## P1 consumer audit and manual review

Completed against `8b543267a88ed63d343bd633cf29cd6edf9c4127`:

- 166 files scanned;
- 1597 model/budget/pricing references;
- 14 automated findings;
- F03 confirmed after manual review;
- F14 conclusion retained with corrected evidence;
- F15 added for native-currency versus `cost_usd` compatibility;
- decision ledger:
  [`P1_MODEL_RUNTIME_AUDIT_DECISIONS.md`](../audits/P1_MODEL_RUNTIME_AUDIT_DECISIONS.md).

P1-A static model compatibility completed deterministic acceptance at `e9f4a51744ce44c04236466450b8af85ebf9be9c` with **889/889** tests. Evidence: [`P1A_STATIC_MODEL_COMPATIBILITY_ACCEPTANCE.md`](../acceptance/pre-stage3/P1A_STATIC_MODEL_COMPATIBILITY_ACCEPTANCE.md).
P1-B0 pricing/cost consumer audit completed at `24918d6fcfe1250043cd6a72082456241fa4679e`: 461 tracked files, 972 occurrences, 8 automated findings, 2 manual amendments, 5 readable official snapshots and 1 unreadable official page. Evidence: [`P1B0_PRICING_CONSUMER_AUDIT_DECISIONS.md`](../audits/P1B0_PRICING_CONSUMER_AUDIT_DECISIONS.md).
P1-B1 typed pricing/native-currency schema completed deterministic acceptance at `bb219ea9e3049b4f5959c9dbb9c0e585875afd82` with **920/920** tests and patch ID `c793e3d1402bf63977e7a25d3ce829d46416fab2`. Evidence: [`P1B1_TYPED_PRICING_SCHEMA_ACCEPTANCE.md`](../acceptance/pre-stage3/P1B1_TYPED_PRICING_SCHEMA_ACCEPTANCE.md).
P1-B2 official concrete-model pricing snapshots completed deterministic acceptance at `571c51fcc250592a21bf40b3831b7dccfc6400aa` with **950/950** tests, 5 source records, 6 verified snapshots and patch ID `d0babc3b57dbdef9370786b7e11d0cc39b93760e`. Evidence: [`P1B2_OFFICIAL_PRICING_SNAPSHOTS_ACCEPTANCE.md`](../acceptance/pre-stage3/P1B2_OFFICIAL_PRICING_SNAPSHOTS_ACCEPTANCE.md).
P1-B3 provider-neutral usage-to-cost estimator completed deterministic acceptance with implementation commit `1c6c7efc9160c104319d4cc01a9b96c3ae0d082e`, correction commit `2296a18f09aa478afcdc5cc9652b4d9166a44149` and **993/993** final tests. Evidence: [`P1B3_COST_ESTIMATOR_ACCEPTANCE.md`](../acceptance/pre-stage3/P1B3_COST_ESTIMATOR_ACCEPTANCE.md).
P1-B4A usage normalization and shared serialization completed deterministic acceptance at `ae276f3df79685a7edd36dc6b06c7d82d5784e7a` with **1016/1016** tests and patch ID `89db552f6660c8e5fa9ac2a67deb21909ae25ae3`. Evidence: [`P1B4A_USAGE_NORMALIZATION_SERIALIZATION_ACCEPTANCE.md`](../acceptance/pre-stage3/P1B4A_USAGE_NORMALIZATION_SERIALIZATION_ACCEPTANCE.md).
P1-B4B explicit estimation and native-cost accounting completed deterministic acceptance at `f650478e842e9020c23489adb407b1b50f1c4438` with **1052/1052** tests and patch ID `5360788b724a9c6d6fcebff107943436efb8a510`. P1-B is now closed. Evidence: [`P1B4B_NATIVE_COST_ACCOUNTING_ACCEPTANCE.md`](../acceptance/pre-stage3/P1B4B_NATIVE_COST_ACCOUNTING_ACCEPTANCE.md).
P1-C1 typed effective model resolution completed deterministic acceptance at `3137a9cdbaf0201ed2ee3f5a28225121ceb04d56` with **1089/1089** tests and patch ID `4a37e161da17664a073761837ce944ea7eff749d`. Evidence: [`P1C1_TYPED_EFFECTIVE_MODEL_CONFIG_ACCEPTANCE.md`](../acceptance/pre-stage3/P1C1_TYPED_EFFECTIVE_MODEL_CONFIG_ACCEPTANCE.md).
P1-C2 modern consumer migration completed deterministic acceptance at `4a39ed894da4d04e3d46772c7b2f5d400ed98093` with **1119/1119** tests and patch ID `01d5e3c292b82e9fb58a8c9f14b02c7a90b5a9c9`. Evidence: [`P1C2_MODERN_CONSUMER_MIGRATION_ACCEPTANCE.md`](../acceptance/pre-stage3/P1C2_MODERN_CONSUMER_MIGRATION_ACCEPTANCE.md).
P1-C3A typed Legacy translation completed deterministic acceptance at `c14650b2a474478cd82c0a9d1798fdd9b80d971b` with **1153/1153** tests and patch ID `b5302f1d3205042b01884e9be4c4e9c0095fb380`. Evidence: [`P1C3A_TYPED_LEGACY_TRANSLATION_ACCEPTANCE.md`](../acceptance/pre-stage3/P1C3A_TYPED_LEGACY_TRANSLATION_ACCEPTANCE.md).
P1-C3B generic AG2 loader policy completed deterministic acceptance at `343d23c5b811f7c529991450b0952299f460c820` with **1184/1184** tests and patch ID `4e4597fb64f4dc3dab29a6b51228143586cb174c`. Evidence: [`P1C3B_GENERIC_LOADER_POLICY_ACCEPTANCE.md`](../acceptance/pre-stage3/P1C3B_GENERIC_LOADER_POLICY_ACCEPTANCE.md).
P1-C3C1 typed AG2 usage summary completed deterministic acceptance at `d2f085b3cabefef87e8aa5099bdb1c2a8ce32b7d` with **1220/1220** tests and patch ID `f5ecbba1271868d84d1ad5b8482c50926a013c6f`. Evidence: [`P1C3C1_TYPED_USAGE_SUMMARY_ACCEPTANCE.md`](../acceptance/pre-stage3/P1C3C1_TYPED_USAGE_SUMMARY_ACCEPTANCE.md).
P1-C3C2 integrated Legacy usage accounting completed at `f0c06c32771916bb6ad3bd68eb4ac21473dcd41b` with **1250/1250** tests and patch ID `6f77f6146e64a341623ac9e21a591f5a7e4cd7bd`. P1-C4 parity then closed P1-C with **1275/1275** tests. Evidence: [`P1C_RUNTIME_CLOSURE_ACCEPTANCE.md`](../acceptance/pre-stage3/P1C_RUNTIME_CLOSURE_ACCEPTANCE.md).
P1-D bounded DeepSeek network smoke completed for `deepseek-v4-flash` with one real API call, native CNY accounting and a verified second-call hard block. P1 is now complete. Evidence: [`P1D_BOUNDED_NETWORK_SMOKE_ACCEPTANCE.md`](../acceptance/pre-stage3/P1D_BOUNDED_NETWORK_SMOKE_ACCEPTANCE.md).
P4 Public/Hidden test-source provenance completed with **1312/1312** deterministic tests and patch ID `bd85479221d8729c9aad23df6a91ccfaf4d7333b`. Evidence: [`P4_TEST_SOURCE_PROVENANCE_ACCEPTANCE.md`](../acceptance/pre-stage3/P4_TEST_SOURCE_PROVENANCE_ACCEPTANCE.md).
Pre-Stage-3 is closed; the next activity is **Stage 3 planning**.
<!-- P1_MODEL_RUNTIME_AUDIT_DECISIONS:END -->

## 2. 已完成

### Stage 0

- Ubuntu 22.04、Python 3.10、Vitis HLS 2023.2 复现环境；
- DeepSeek V4 Flash/Pro OpenAI-compatible；
- `flow.new`、RAG、有限 `flow.parallel_kernel`、`opt.simple_iter` 基线；
- HeteroRefactor 因 ROSE/EDG 外部依赖暂停。

### Stage 1 共享基础设施

- `TaskSpec`、`TargetProfile`、`RunMode`；
- Model Registry 与 OpenAI-compatible Provider；
- Evaluator/Evidence 基础接口；
- `BudgetManager` core；
- `TraceRecorder`；
- `UnifiedRunner` 与 CLI；
- Legacy Refactor Adapter；
- AutoGen 与 testbench repair 已知 usage 合并。

### Stage 1 TargetProfile 本地执行核心

已经真实完成并验收：

```text
TaskSpec.target
→ LegacyRefactorAdapter
→ flow.new ContextVariables
→ target-aware Tcl
→ selected vitis-run
→ requested/actual version verification
→ real Vitis csynth
```

具体能力：

- 默认 profile：`vitis-2023.2-default`；
- `device`、clock period/frequency、compile flags replace/append；
- target-aware `set_part`、`create_clock`、`add_files -cflags`；
- `AGREFACTOR_VITIS_RUN` 可选 executable 覆盖；
- `vitis-run --version` 严格匹配；
- mismatch、probe failure、timeout、unparseable 在 csynth 前阻断；
- remote non-default target 显式拒绝，避免静默丢配置；
- `effective_target_profile.json`；
- `csynth_invocation.json`；
- Vitis 2023.2 真实 smoke：
  `/data/agrefactor_runs/stage1_target_profile_real_vitis_20260715_141118`；
- 实际器件：`xcu200-fsgd2104-2-e`；
- 实际目标时钟：`4.0 ns`；
- 语义 compile flag 已由 `#error` guard 验证；
- Estimated Fmax：`342.47 MHz`。

### Stage 1 csynth Hard Budget：已验收

已完成真实链路：

```text
UnifiedRunner
→ RunContext.budget
→ LegacyRefactorAdapter
→ hls_refactor_with_rag
→ csynth_and_csim
→ run_csynth
→ real vitis-run
```

关键结论：

- aggregate `max_tool_calls/tool_calls` 与专项 `max_csynth_calls/csynth_calls`；
- pre-version-probe hard check；
- pre-launch exact-once consume；
- success/failure/timeout/launch exception 计一次；
- mismatch 不计真实 csynth；
- local normal/HeteroRF 路径共享同一预算实例；
- bounded remote tool budget 显式拒绝；
- 真实 Vitis 2023.2 smoke：
  `/data/agrefactor_runs/stage1_real_vitis_csynth_budget_smoke_20260715_184955`；
- 第一次真实综合成功并生成 report；
- 第二次在 version probe 前阻断；
- final usage：`tool_calls=1`、`csynth_calls=1`。

详见 [`stage1_csynth_budget_acceptance.md`](../acceptance/stage1/stage1_csynth_budget_acceptance.md)。


### Stage 1 Compile 与 C Simulation Hard Budget：已验收

完整确定性链路：

```text
UnifiedRunner
→ RunContext.budget
→ LegacyRefactorAdapter
→ Testbench Preflight
→ run_csynth
→ run_csim
```

关键结论：

- aggregate `max_tool_calls/tool_calls`；
- `max_compile_calls/compile_calls`；
- `max_csim_calls/csim_calls`；
- Preflight 与 csim 编译共享 compile 额度；
- csim 完整计划在 `g++` 前 prospective hard check；
- compile 与 simulation 分别在真实启动前 exact-once consume；
- success/failure/timeout/launcher exception 语义已覆盖；
- 完整联合精确额度：`4 tool / 2 compile / 1 csynth / 1 csim`；
- 确定性测试：`204/204 passed`；
- 真实本地 csim smoke：
  `/data/agrefactor_runs/stage1_real_local_csim_budget_smoke_20260715_215055`；
- 第一次真实 `g++` 与生成的 `./csim` 执行成功；
- 第二次在 `g++` 前阻断；
- final usage：`tool_calls=2`、`compile_calls=1`、`csim_calls=1`。

详见 [`stage1_compile_csim_budget_acceptance.md`](../acceptance/stage1/stage1_compile_csim_budget_acceptance.md)。


### Stage 1 Core：已关闭

真实 DFS 总链路：

```text
real upstream DFS original
→ UnifiedRunner
→ shared BudgetManager
→ Preflight compile
→ Vitis HLS 2023.2 csynth
→ csim compile
→ generated executable
```

精确结果：

```text
tool_calls=4
compile_calls=2
csynth_calls=1
csim_calls=1
REAL_DFS_FULL_CHAIN_BUDGET_READY=1
```

运行目录：

```text
/data/agrefactor_runs/stage1_real_dfs_full_chain_budget_v2_20260716_111358
```

本次未调用 LLM API；候选为确定性可综合参考实现。该验收证明工具执行与预算基础设施可承载真实 DFS，不证明智能体已经自动重构 DFS。

范围决策：

- public test 是 Stage 2/3 的评测角色，不新增物理工具预算；
- cosim 在原项目无活跃实现，不阻塞 Stage 1 Core；
- named profiles、executable/settings、platform/resources/parser/provenance 继续作为 Hardening。

详见 [`stage1_core_acceptance.md`](../acceptance/stage1/stage1_core_acceptance.md)。

### Stage 2.1–2.3 核心

早期 Testbench Reliability：

- structured preflight；
- failure stage/kind/owner/next-action；
- bounded testbench-only repair；
- ABI/linkage 与 preservation contract；
- private global gate；
- repair artifacts 与 usage 合并；
- 一个真实状态型 kernel 的 unified CLI → DeepSeek → Vitis E2E。

后续通用化与运行时接入：

- Public/Hidden suite schema、evidence、redaction、trace 与 composition；
- 通用 Feedback Schema、adapters、parser、views、composers；
- deterministic router、state machine、transition 与 coordinator；
- generic `ValidationOrchestrator`；
- real Preflight / CSYNTH / Public CSIM / Hidden CSIM handlers；
- shared exact physical budget；
- Hidden result/trace suppression；
- runtime lazy integration exports；
- 531 个确定性测试；
- 真实 Vitis 2023.2 全验证链，预算 `6 tool / 3 compile / 1 csynth / 2 csim`。

### Stage 2.4.3.1 Candidate Repair Prompt Policies

已经完成。

功能提交：

```text
dc44be344f9bf9bae3eb8e43675fb49f0c017708
feat: add candidate repair prompt policies
```

测试：

```text
20/20 targeted passed
574/574 full passed
```

完成：

- 新增 `CandidateRepairPromptInputs`；
- 公开三个确定性构建函数：Compile、CSYNTH、Public CSIM；
- 三个入口共享一个私有 policy implementation 和
  `SharedLayeredPromptBuilder` renderer；
- candidate kernel 是唯一 editable artifact；
- original program 始终只读；
- Compile 与 CSYNTH 的 Public testbench 可选，存在时只读；
- Public CSIM 的 Public testbench 必须存在且只读；
- 只接受 blocking、candidate-owned、stage-matching、
  agent-safe feedback；
- Hidden、operator-full、wrong owner 和 wrong stage 被拒绝；
- CSYNTH Prompt 显式携带 effective TargetProfile；
- 不调用模型、网络、编译器、CSIM、CSYNTH 或 Vitis；
- 未实现 Candidate Model Adapter、响应解析、repair loop
  或 orchestrator 模型接入。

确定性验收目录：

```text
/data/agrefactor_runs/stage2_candidate_prompt_policies_acceptance_recovery_20260718_035345
```

本验收证明 Prompt Policy 的确定性、安全边界和结构契约，
不是真实模型或真实工具链 candidate repair 验收。

### Stage 2.4.3.2 Candidate Model Adapter / Response Contract

已经完成。

功能提交：

```text
37a3577a59cf823def82591ae285a9d85f7fbe67
feat: add candidate model adapter
```

测试：

```text
24/24 targeted passed
56/56 related regression passed
598/598 full passed
```

完成：

- 新增 provider-neutral `CandidateModelAdapter`；
- 通过既有 `ModelRegistry` 解析一个用户指定的固定逻辑模型；
- 消费已经构建好的 candidate `LayeredPrompt`，不重复建设 Prompt renderer；
- 合并 `ModelSpec.default_parameters` 与调用方显式参数；
- 保存 prompt、normalized `ModelResponse`、token、cost 和结果审计记录；
- 只接受一个带 C++ 语言标记的完整 fenced replacement；
- 拒绝空响应、commentary、多代码块、错误语言、patch/diff 和空代码块；
- 拒绝缺失、重命名、重复或改变顶层接口的 candidate；
- 拒绝新增 `main` 和语义未变化的 candidate；
- 使用 lazy package export，保持 `agrefactor.prompts` / `agrefactor.models` 任意导入顺序；
- 不执行 compile、CSIM、CSYNTH、Vitis、repair loop 或 orchestrator 状态转移。

确定性 FakeProvider 验收目录：

```text
/data/agrefactor_runs/stage2_candidate_model_adapter_20260718_161157
```

该验收证明 Model Registry 调用边界、响应解析、接口保护和 usage 审计可工作；
它不是真实网络模型 API、真实 candidate 修复循环或真实工具链验收。

### Stage 2.4.3.3 Bounded Candidate Repair Loop

已经完成。

功能提交：

```text
b7010fc1969e53432bb95a35b519cd8c118347ff
feat: add bounded candidate repair loop
```

测试：

```text
32/32 targeted passed
71/71 related regression passed
630/630 full passed
```

完成：

- 新增独立 `agrefactor.repair` 控制层，不修改 Validation Handler 或 Orchestrator；
- 每轮只生成一个 candidate，并使用显式 `max_attempts` 限制尝试；
- 入口要求 blocking、agent-safe、`route=repair_candidate` 和 candidate ownership；
- Public CSIM 额外要求 public split、agent-visible feedback 和 Public testbench；
- Hidden、operator-full、mixed、unknown、toolchain、configuration、evaluator、task input、testbench 和 original failure 不启动循环；
- Compile、CSYNTH、Public CSIM proposal 分别强制从合法 Preflight 前缀重新验证；
- 具体后续验证计划由注入 validator 决定，validator 复用同一个 BudgetManager；
- Provider 启动前 prospective check，紧邻启动 exact-once 计 `llm_calls`；
- Provider exception 计调用但不伪造 token/cost；非法响应保留真实 response usage；
- 新增 `BudgetManager.record_observed(...)` 记录调用后才能获知的 token/cost；
- 维护 `initial_candidate`、`current_candidate`、`last_validated_candidate` 和 `last_proposal`；
- 未验证或验证失败 proposal 不覆盖 `last_validated_candidate`；
- 暂未引入 `best_correct`、`best_ppa`、candidate tree、PPA 排序或 Stage 3 优化。

确定性 FakeProvider/FakeValidator 验收目录：

```text
/data/agrefactor_runs/stage2_bounded_candidate_repair_loop_20260718_174524/acceptance
```

该验收验证有界控制、合法验证前缀、预算语义和状态保留；它不是真实网络模型、真实工具链修复或 ValidationOrchestrator 集成验收。

### Stage 2.4.3.4 Safe ValidationOrchestrator Integration

已经完成。

功能提交：

```text
dd0ee927a5dac6691180c0772661cd90befe64ea
feat: integrate candidate repair orchestration
```

测试：

```text
32/32 targeted passed
107/107 related regression passed
662/662 full passed
```

完成：

- 保留既有 `ValidationOrchestrator.run(...)` 兼容接口，并新增仅供内部合法 handoff 使用的 `run_detailed(...)`；
- 普通 `ValidationOrchestrationResult` 和 trace 继续不包含 Hidden 原始报告；
- 新增 `CandidateRepairValidationOrchestrator`，组合既有 ValidationOrchestrator、Coordinator / State Machine 和 BoundedCandidateRepairLoop；
- 初始 candidate 与每个 proposal 都由 handler factory 构造全新的验证 Handler；
- changed candidate 始终从 Preflight 开始完整重入，不复用旧 candidate 的前置证据；
- Handler 继续只验证，不导入 CandidateModelAdapter 或 BoundedCandidateRepairLoop；
- Candidate repair 只在 agent-safe、blocking、`repair_candidate` handoff 下启动；
- testbench、original、unknown、mixed、toolchain、configuration 和 Hidden failure 不进入模型修复；
- Hidden failure 保持 operator-only terminal，内部原始报告不进入普通结果、trace 或下一轮 Prompt；
- repair 失败或耗尽时，对外 `final_candidate` 回退到初始 candidate，不采用未验证 proposal；
- 新增本地真实 Handler factory，复用同一 `BudgetManager` 和 `TraceRecorder`；
- 未修改 UnifiedRunner / CLI，也未进入 Stage 3、Memory 或版本迁移。

真实工具链 + 本地 FakeProvider 验收目录：

```text
/data/agrefactor_runs/stage2_candidate_repair_orchestration_recovery_20260718_190537/real_acceptance
```

真实执行链：

```text
broken Candidate
→ real g++ Preflight（candidate-owned compile failure）
→ local deterministic FakeProvider
→ real repaired g++ Preflight
→ real Vitis HLS 2023.2 CSYNTH
→ real Public CSIM
→ real Hidden CSIM
→ accepted
```

精确预算：

```text
tool_calls=7
compile_calls=4
csynth_calls=1
csim_calls=2
llm_calls=1
tokens=60
cost_usd=0.02
```

该验收证明 ValidationOrchestrator 与 bounded Candidate Repair Loop 已在真实本地工具链上完成一次安全接入；模型仍是本地 FakeProvider，不是真实网络模型 API，也不证明任意 kernel 的修复能力。

### Stage 2.5.1 Smoke Corpus / Ground Truth Contract

已经完成。

功能提交：

```text
ca991c372f9f40f7e592136b12af774dd985c0fa
feat: add Stage 2 smoke corpus
```

稳定 corpus：

```text
array map
reduction
nested stencil
multi-output
struct record
hls::stream
stateful
```

完成：

- 新增通用 `agrefactor.smoke` schema 和 immutable corpus；
- ground truth 由人工独立标注，不从 runtime owner、route 或 terminal 推导；
- 每个 baseline 固定 source bundle、Public/Hidden suite 和 Hidden marker；
- 每个 baseline 预期完整验证预算为
  `6 tool / 3 compile / 1 csynth / 2 csim / 0 LLM`；
- operator manifest 含 ground truth 和 source SHA-256；
- agent-safe manifest 不含 ground truth、Hidden suite identity、
  Hidden source digest 或 Hidden marker；
- Candidate Response Contract 能提取七类 baseline 顶层接口；
- `24/24` targeted、`48/48` related、`686/686` full unittest 通过。

真实本地验收：

```text
七类 committed corpus
→ real g++ Preflight compile/link
→ 7/7 passed
```

精确预算：

```text
tool_calls=7
compile_calls=7
csynth_calls=0
csim_calls=0
llm_calls=0
tokens=0
cost_usd=0.0
```

验收目录：

```text
/data/agrefactor_runs/stage2_5_1_smoke_corpus_20260718_232154/acceptance
```

本阶段没有执行 Vitis CSYNTH、Public/Hidden CSIM 或模型调用。
详见
[`stage2_smoke_corpus_acceptance.md`](../acceptance/stage2/stage2_smoke_corpus_acceptance.md)。

### Stage 2.5.2 Real Full-chain Pass Matrix

已经完成。

功能提交：

```text
71f317b85227604a3959db725ae33b074d66824e
feat: add Stage 2 smoke pass matrix runner
```

新增：

- `Stage2SmokePassMatrixRunner`；
- `Stage2SmokePassCaseResult`；
- `Stage2SmokePassMatrixResult`；
- `Stage2SmokePassMatrixError`；
- `expected_stage2_smoke_pass_budget(...)`。

矩阵使用一份共享 `BudgetManager`，按 committed corpus 顺序验证：

```text
array-map
reduction
nested-stencil
multi-output
struct-record
hls-stream
stateful
```

确定性验证：

```text
21/21 targeted
77/77 related
707/707 full unittest
```

真实本地矩阵：

```text
7 × (
  real g++ Preflight
  → real Vitis HLS 2023.2 CSYNTH
  → real Public CSIM
  → real Hidden CSIM
  → accepted
)
```

每类精确预算：

```text
6 tool / 3 compile / 1 csynth / 2 csim / 0 LLM
```

矩阵总预算：

```text
42 tool / 21 compile / 7 csynth / 14 csim / 0 LLM
```

所有 case 的阶段顺序均为 Preflight、CSYNTH、Public、Hidden；
前三阶段为 agent-safe evidence，Hidden 为 operator-full 且不进入普通
result 或 trace。未调用 FakeProvider 或真实网络模型。

验收目录：

```text
/data/agrefactor_runs/stage2_5_2_real_full_chain_pass_matrix_20260719_001400/acceptance
```

详见
[`stage2_smoke_pass_matrix_acceptance.md`](../acceptance/stage2/stage2_smoke_pass_matrix_acceptance.md)。

### Stage 2.5.3 Fault / Ownership / Hidden Matrix

已经完成。

```text
a09915878aca4012a01b258d1f196ba0f18b4be5
feat: add Stage 2 fault ownership matrix
```

矩阵包含 5 个真实工具故障和 4 个确定性规范化路由故障，覆盖 candidate、
testbench、original、toolchain、unknown、mixed、Public repair handoff、
Hidden rejected/review terminal 与无泄漏。

```text
20/20 targeted
65/65 related
727/727 full unittest
9/9 ground-truth matches
13 tool / 8 compile / 2 csynth / 3 csim / 0 LLM
```

未调用模型，也未执行 repair。验收目录：

```text
/data/agrefactor_runs/stage2_5_3_fault_ownership_hidden_matrix_20260719_003933/acceptance
```

详见
[`stage2_smoke_fault_matrix_acceptance.md`](../acceptance/stage2/stage2_smoke_fault_matrix_acceptance.md)。

### Stage 2.5.4 Evidence Summary

已经完成。

```text
7 baseline + 9 fault = 16 independent labels
23 executions = 19 real + 4 deterministic
727/727 current full regression
62/36/9/17/0 cumulative separate acceptance usage
```

累计值不是一次共享预算；测试数也不相加。9 个来源产物已重新解析并记录
SHA-256。

- [`stage2_smoke_evidence_summary.md`](stage2_smoke_evidence_summary.md)；
- [`stage2_smoke_evidence_index.json`](../stage2_smoke_evidence_index.json)。

本阶段没有重新运行 Vitis、调用模型或执行 repair。

```text
/data/agrefactor_runs/stage2_5_4_evidence_summary_20260719_015045/acceptance
```

### Stage 2.6 Closure-readiness Audit

已经完成。

```text
satisfied=4
blocking_before_stage3=5
defer=4
future_or_external=4
```

五个 blocker：

1. formal repair-aware UnifiedRunner / CLI；
2. shared repair Protocol / artifact schema；
3. minimal ModelFamilyProfile；
4. Stage 1 Hardening Batch A；
5. one real network-model candidate-repair smoke。

Contract 新语法与 Parser 新规则没有 2.5 失败证据，不做无依据扩张。
Ground-truth corpus 已满足 Stage 2 要求，2.7 只重验证。

- [`STAGE2_CLOSURE_READINESS_AUDIT.md`](STAGE2_CLOSURE_READINESS_AUDIT.md)；
- [`stage2_closure_readiness_audit.json`](../stage2_closure_readiness_audit.json)。

```text
/data/agrefactor_runs/stage2_6_closure_readiness_audit_20260719_022645/acceptance
```

### Stage 2.7.1 Repair Protocol and Artifact Schema

已经完成。

```text
ae1042fc77efe5c87a85a5f4954a7c0a951f2045
feat: add shared repair protocol artifacts
```

Candidate 与 Testbench 现在共享公共信封：

```text
attempt_id / proposal_id / artifact_role
prompt_manifest / model_response / observed_usage
payload_type / payload
stop_reason / terminal_status / evidence_view
operator_artifact_available / artifact_manifest
```

业务字段不被强行拉平：

```text
CandidateRepairPayload.validation_summary
TestbenchRepairPayload.preflight_summary
```

现有 legacy `to_dict()` 和 `testbench_repair.json` 保持兼容；共享 artifacts
写入独立目录并采用原子文件替换。协议层不调用模型、工具或 orchestrator，
两条 executor 没有合并。

```text
33/33 targeted
760/760 full unittest
network model = false
real tool = false
```

```text
/data/agrefactor_runs/stage2_7_1_repair_protocol_artifacts_v5_20260719_172510/acceptance
```

详见
[`stage2_repair_protocol_acceptance.md`](../acceptance/stage2/stage2_repair_protocol_acceptance.md)。

### Stage 2.7.2 Minimal ModelFamilyProfile

已经完成。

```text
a9ec856540940f1767fe245a3c662468293fda5b
feat: add minimal model family profiles
```

新增 vendor-neutral typed profile：

```text
ModelCapabilityTag
ModelFamilyProfile
```

固定五个 capability tags：

```text
reasoning_model
code_specialized
strict_instruction
thinking_tag_possible
strict_completion
```

Profile 只控制安全默认参数、通用 Prompt instruction 和审计 manifest。
Registry 仍由调用方给出的逻辑模型名解析模型，不自动选择或切换模型。

```text
parameter precedence:
profile safe defaults
< ModelSpec defaults
< call overrides
```

Credential-like 参数被拒绝；Response Contract、Hidden 边界和工具链均未改变。

```text
32/32 targeted
792/792 full unittest
network model = false
real tool = false
automatic routing = false
```

```text
/data/agrefactor_runs/stage2_7_2_model_family_profile_v3_20260719_183938/acceptance
```

详见
[`stage2_model_family_profile_acceptance.md`](../acceptance/stage2/stage2_model_family_profile_acceptance.md)。

### Stage 2.7.3 Stage 1 Hardening Batch A

已经完成。

```text
411d1e2b37ae6e620c0b759b98f7e8277cb851c4
feat: harden target execution profiles
```

完成一个稳定 committed `vitis-2023.2-default` profile，并将 executable、
settings path、parser profile、resource limits 和逐字段 effective provenance
写入执行契约。`.env.example` 不再包含伪 API key 值。

```text
24/24 targeted
816/816 full unittest
network model = false
real tool = false
additional Vitis versions = false
```

```text
/data/agrefactor_runs/stage2_7_3_stage1_hardening_batch_a_20260719_190809/acceptance
```

详见
[`stage2_stage1_hardening_batch_a_acceptance.md`](../acceptance/stage2/stage2_stage1_hardening_batch_a_acceptance.md)。

### Stage 2.7.4 Formal Repair-aware UnifiedRunner / CLI

已经完成。

```text
7e9aef66ba062b25465f6552f9bf346b8ed5eb86
feat: add formal repair-aware runner phase
```

新增 `CandidateRepairPhase`，正式 `--repair-aware` CLI 只负责构造已有
validation/repair 链，不复制 handler、router 或 orchestrator 逻辑。UnifiedRunner
写入 versioned `run_result.json` 与 manifest，phase bundle 继续包含 shared repair
artifacts。Deterministic acceptance 同时修正了合法 hidden-only validation plan
被固定 canonical prefix 误判为 validator error 的跨阶段契约缺口。

```text
20/20 targeted
836/836 full unittest
shared budget instances = 1
shared trace instances = 1
network model = false
real tool = false
optimizer = false
```

```text
/data/agrefactor_runs/stage2_7_4_repair_aware_cli_v2_20260719_203354/acceptance
```

详见
[`stage2_repair_aware_cli_acceptance.md`](../acceptance/stage2/stage2_repair_aware_cli_acceptance.md)。

### Stage 2.7.5 Real Network-model Candidate Repair Smoke

已经完成。

```text
code_baseline=7407da78b9371e853b44a201828ce4b9251fad8f
model=deepseek-v4-flash
response_model=deepseek-v4-flash
base_url=https://api.deepseek.com
model_calls=1
total_tokens=1106
attempt_status=validation_failed
orchestration_status=validation_terminal
repair_stop_reason=terminal_feedback
response_contract=accepted
outcome=可信 terminal failure（validation_failed）
```

该运行通过正式 `--repair-aware` CLI，从真实 candidate-owned g++ Preflight
失败进入一次真实网络模型调用。模型不被要求必须修复成功；本次证据满足
request、response、非零 usage、strict contract、bounded terminal result、
真实本地验证和 Hidden agent-safe 无泄漏。

```text
tool_calls=2
compile_calls=2
csynth_calls=0
csim_calls=0
preflight_invocations=2
csynth_invocations=0
csim_invocations=0
network model = true
real tool = true
hidden leakage = false
optimizer = false
```

```text
/data/agrefactor_runs/stage2_7_5_real_network_candidate_repair_20260719_211334/acceptance
```

详见
[`stage2_real_network_candidate_repair_smoke.md`](stage2_real_network_candidate_repair_smoke.md)。

### Stage 2.7.6 Evidence-gated Contract/Parser Delta + Ground-truth Revalidation

已经完成。

```text
code_baseline=b1a787ab0e41b382fec25973968e2b162a500f85
replayed_preflight_failure_kind=link_error
replayed_preflight_failure_owner=unknown
contract_delta_required=false
parser_delta_required=false
code_delta_applied=false
```

2.7.5 的真实 proposal 再次通过当前 CandidateResponseContract 的全部结构约束；
第二次 Preflight 失败可重复，但属于编译/语义验证职责。2.7.5 未执行 CSYNTH，
因此没有 parser delta 证据。

```text
16/16 independent labels
7/7 baseline real full chains
9/9 fault matrix matches
12 real scenarios
4 deterministic scenarios
55/29/9/17/0 combined physical usage
network model = false
hidden leakage = false
optimizer = false
```

该 combined usage 来自 pass/fault 两个精确 runner budget 的算术合计，不冒充
一个共享预算。

```text
/data/agrefactor_runs/stage2_7_6_evidence_gated_ground_truth_revalidation_20260719_215820/acceptance
```

详见
[`stage2_evidence_gated_ground_truth_revalidation.md`](stage2_evidence_gated_ground_truth_revalidation.md)。

### Stage 2.7.7 Cross-stage Regression and Stage 2.8 Handoff

已经完成。

```text
code_baseline=5d9ca6b76162f30e6a33c76d933ebb0021955baf
related_tests=389/389
full_unittest=836/836
evidence_milestones=8/8
blockers_satisfied=5/5
artifact_manifests_validated=8
artifact_manifest_entries=34
execution_classes_distinct=true
closure_checklist=9/10
pending=C-09 final documentation synchronization
ready_for_stage2_8=true
stage2_closed=false
stage3_allowed=false
```

本阶段核对了 2.5.4、2.6 和 2.7.1～2.7.6 的本地 acceptance，
将 B-01～B-05 映射到对应提交、文档、证据目录和执行类别，并验证所有发现的
versioned artifact manifest 的路径、SHA-256 与字节数。

```text
new network-model calls = 0
new Vitis CSYNTH calls = 0
new Vitis CSIM calls = 0
optimizer executed = false
feature commit created = false
```

2.7.7 只冻结 Stage 2.8 handoff，不关闭 Stage 2。唯一未完成的 closure
condition 是 C-09：README、USAGE、REPRODUCTION_STATUS、CHANGELOG、
ROADMAP 和其余状态文档的最终同步。

```text
/data/agrefactor_runs/stage2_7_7_cross_stage_regression_handoff_20260719_224214/acceptance
```

详见
[`stage2_hardening_acceptance.md`](../acceptance/stage2/stage2_hardening_acceptance.md)。

### Stage 2.8 Final Documentation and Stage 2 Closure

已经完成。

```text
closure_validation_baseline=3f57371c8b58f53449064219c024ab63042a87d4
related_tests=389/389
full_unittest=836/836
blockers_satisfied=5/5
evidence_milestones=8/8
artifact_manifests_validated=8
artifact_manifest_entries=34
closure_checklist=10/10
stage2_closed=true
stage3_allowed=true
```

本阶段只同步全局文档并生成正式 closure acceptance；没有功能代码修改、
网络模型调用、新的正式 Vitis CSYNTH/CSIM 或 Optimizer 执行。

```text
/data/agrefactor_runs/stage2_8_final_documentation_closure_v2_20260719_233430/acceptance
```

详见
[`stage2_closure_acceptance.md`](../acceptance/stage2/stage2_closure_acceptance.md)。

## 3. 未完成

### Stage 1 Hardening（不阻塞 Core 关闭）

Batch A 在 Stage 2.7 中、进入 Stage 3 前完成：

1. stable named TargetProfile；
2. per-profile executable/settings；
3. report parser profile；
4. effective profile 每字段 provenance；
5. basic resource-limit schema；
6. 无 secret 的稳定 target/model 配置模板。

Batch B 在进入 Stage 5 前完成：

1. 更多真实 Vitis 版本；
2. 更多器件和 platform；
3. 版本特定 parser 与 source/target profile 扩展；
4. 多版本、多器件、更多真实 kernel 交叉验证。

Stage 3 仍需实现预算耗尽时停止新候选并返回 `best_correct`。

### Stage 2：已关闭

Stage 2.1–2.8 已完成；closure checklist `10/10`。Stage 3 现在允许开始，
但尚未实现任何 Stage 3 功能。

Stage 2.6 五个 blocker（5/5 已完成验收）：

- B-01：已由 Stage 2.7.4 完成并验收；
- B-02：已由 Stage 2.7.1 完成并验收；
- B-03：已由 Stage 2.7.2 完成并验收；
- B-04：已由 Stage 2.7.3 完成并验收；
- B-05：已由 Stage 2.7.5 真实 network-model smoke 完成并验收。

当前不是 blocker：

- 2.7.6 真实 evidence replay 未证明 CandidateResponseContract 缺口；
- 2.7.5 无 CSYNTH evidence，2.7.6 未猜测式扩张 parser；
- 16-label ground-truth corpus 已在当前基线重验证为 16/16。
### 后续 Stage

- Stage 3 Safe Three-Level Optimizer：未开始；
- Stage 4 Memory Applicability Gate：未开始；
- Stage 5 Target Version Extension / Real Migration：未开始；
- Stage 6 System Evaluation：未开始。

## 4. 当前下一任务

Stage 2 保持 closed，Stage 3 尚未开始。当前先完成
[`PRE_STAGE3_BRIDGE.md`](../reference/PRE_STAGE3_BRIDGE.md)：

```text
P0 Real DFS end-to-end acceptance
P1 Adaptive Model Profile Registry
P2 Source-only Refactor Bootstrap Contract
P3 Test Qualification Contract
P4 Provided / Auto / Hybrid test policy
P5 User-facing quiet output / verbosity policy
```

本次 `53045b4cdc6c262e0be5cdcddedae0d302908812` 已完成 P0 的 test-generation hardening 子项，但真实 DFS
仍未成功，不能提前宣称端到端通过。下一次真实验收默认先用
`deepseek-v4-flash`，并要求 independent qualification 与正式 Stage 2
Preflight→CSYNTH→Public→Hidden 全部 accepted。

## 5. 多 Vitis 版本的当前显式用法

多版本机器必须同时指定：

```text
TaskSpec.target.toolchain_version
+
AGREFACTOR_VITIS_RUN=/path/to/the/matching/vitis-run
```

例如：

```bash
source /data/Xilinx/Vitis/2024.1/settings64.sh
export AGREFACTOR_VITIS_RUN=/data/Xilinx/Vitis/2024.1/bin/vitis-run
python -m agrefactor.cli run task-2024.1.json --legacy
```

requested 与 actual 不一致时，系统会在 csynth 前阻断。完整命令见 [`USAGE.md`](../guides/USAGE.md)。

## 6. 对外表述边界

允许表述：

> TargetProfile 本地执行核心已在 Vitis 2023.2 上完成一次真实 csynth 验收。

不允许表述：

- 已支持任意 Vitis 版本；
- 已支持任意版本迁移；
- 已支持任意器件或任意 kernel；
- Stage 1 Core 之外的 Hardening 已全部完成；
- Stage 1 Core 关闭等于智能体已经通过 API 自动重构 DFS；
- 727 个测试等于 727 个真实 kernel。

## 7. 新对话阅读顺序

```text
1. docs/roadmap/PROJECT_STATE.md
2. docs/roadmap/ROADMAP.md
3. docs/roadmap/GOAL_TRACEABILITY.md
4. docs/roadmap/STAGE1_INFRASTRUCTURE.md
5. docs/acceptance/stage1/stage1_target_profile_acceptance.md
6. docs/acceptance/stage1/stage1_csynth_budget_acceptance.md
7. docs/acceptance/stage1/stage1_compile_csim_budget_acceptance.md
8. docs/acceptance/stage1/stage1_core_acceptance.md
9. docs/roadmap/STAGE2_EVIDENCE_LOOP.md
10. docs/roadmap/STAGE2_HARDENING_PLAN.md
11. docs/roadmap/STAGE2_CLOSURE_READINESS_AUDIT.md
12. docs/stage2_closure_readiness_audit.json
13. docs/roadmap/stage2_smoke_evidence_summary.md
14. docs/stage2_smoke_evidence_index.json
15. docs/acceptance/stage2/stage2_smoke_corpus_acceptance.md
16. docs/acceptance/stage2/stage2_smoke_pass_matrix_acceptance.md
17. docs/acceptance/stage2/stage2_smoke_fault_matrix_acceptance.md
18. docs/acceptance/stage2/stage2_acceptance.md
19. docs/guides/REPRODUCTION_STATUS.md
20. docs/guides/USAGE.md
21. git log
```

<!-- P5_CONCISE_OUTPUT_CLOSURE:BEGIN -->
## P5 concise output and log capture

```text
base=0a1d816fa1d7f738dd3757a19a243df22020caf5
full=1391/1391
new_tests=19
default/json/verbose/debug=implemented
full_result/trace/model_calls/tool_calls/stdout/stderr/manifest=implemented
Token/Cost=soft_observed_only
call_counts/wall_time=hard_effective_limits
Hidden ordinary-output leakage=false
P5 frozen contract=closed
P0=active next objective, not run
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```

Evidence:
[`P5_CONCISE_OUTPUT_ACCEPTANCE.md`](../acceptance/pre-stage3/P5_CONCISE_OUTPUT_ACCEPTANCE.md).
<!-- P5_CONCISE_OUTPUT_CLOSURE:END -->

<!-- P0_COST_BUDGET_CURRENCY_BLOCKER_CORRECTION -->
## P0 observed blocker: optional Cost budget currency

The first real P0 driver found that the ordinary source CLI passed the
selected model's pricing currency even when `--cost-budget` was omitted,
violating `EffectiveRunBudget`'s paired optional-field invariant. The
correction persists a Cost-budget currency only when the user declares a
Cost budget. Deterministic and zero-LLM CLI evidence is recorded in
[`P0_COST_BUDGET_CURRENCY_BLOCKER_ACCEPTANCE.md`](../acceptance/pre-stage3/P0_COST_BUDGET_CURRENCY_BLOCKER_ACCEPTANCE.md).

```text
P0_STATUS=active_retry_required
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```

<!-- P0_PORTABLE_IDENTIFYING_JSON_BLOCKER_CORRECTION -->
## P0 observed blocker: portable identifying JSON output

The real DFS run exposed a typed structured-output request unsupported
by the selected OpenAI-compatible endpoint. Because identification is
already consumed through `json.loads()`, its static AG2 agent contract
now uses portable `json_object` output with explicit JSON shapes. The
`HLSAgentLoader` remains vendor-neutral. Evidence is recorded in
[`P0_PORTABLE_IDENTIFYING_JSON_BLOCKER_ACCEPTANCE.md`](../acceptance/pre-stage3/P0_PORTABLE_IDENTIFYING_JSON_BLOCKER_ACCEPTANCE.md).

```text
P0_STATUS=active_retry_required
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```

<!-- P0_PUBLIC_TESTBENCH_REPAIR_AND_OUTPUT_LIMITS -->
## P0 observed blocker: Public Testbench repair routing

A real DFS run reached formal validation with a synthesizable Candidate,
but the generated Public Testbench depended on implementation-private
globals. Source bootstrap now executes the independent Public Testbench
repair loop, records derived provenance, and keeps Hidden content out
of prompts. The four known-family artifact output limits are 32768 with
the existing 65536 safety ceiling. Evidence is recorded in
[`P0_PUBLIC_TESTBENCH_REPAIR_AND_OUTPUT_LIMITS_ACCEPTANCE.md`](../acceptance/pre-stage3/P0_PUBLIC_TESTBENCH_REPAIR_AND_OUTPUT_LIMITS_ACCEPTANCE.md).

```text
P0_STATUS=active_retry_required
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```

<!-- P0_PROMPT_IDENTITY_RECONCILIATION -->
## P0 observed evidence gap: Testbench repair Prompt Identity

A real DFS run accepted the Candidate with CSYNTH, Public and Hidden
passing, but Prompt Identity recorded 29 calls while the shared budget
correctly recorded 30. The missing call was the single Public Testbench
repair. Source bootstrap now adds every safe Testbench repair audit event
to the unified Prompt Identity without persisting plaintext or Hidden
content. See [`P0_PROMPT_IDENTITY_RECONCILIATION.md`](../audits/P0_PROMPT_IDENTITY_RECONCILIATION.md).

```text
P0_STATUS=active_retry_required
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```

<!-- P0_TESTBENCH_REPAIR_RETRY_FEEDBACK -->
## P0 observed Testbench repair retry gap

A real DFS run used both Public Testbench repair attempts, but the
second prompt did not receive the first deterministic contract
rejection. Repair requests now carry safe prior-attempt summaries,
and every prompt explicitly lists required declarations, macros and
minimum call counts. The deterministic contract and then-current two-attempt bound were unchanged by that correction. Step E later supersedes the product defaults with the shared 3-attempt, 10-ceiling contract. See
[`P0_TESTBENCH_REPAIR_RETRY_FEEDBACK.md`](../reference/P0_TESTBENCH_REPAIR_RETRY_FEEDBACK.md).

```text
P0_STATUS=active_retry_required
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```
<!-- P0_GENERATION_REPAIR_STABILIZATION_PLAN -->
## P0 生成与修复稳定化执行计划

P0 真实 DFS 运行暴露出的生成、Hidden、启发式与 repair 问题，已冻结到
[`P0_GENERATION_REPAIR_STABILIZATION_PLAN.md`](../history/P0_GENERATION_REPAIR_STABILIZATION_PLAN.md)。

```text
ACTIVE_STEP=A
P0_STATUS=active_retry_required
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
DEFAULT_LLM_CALLS=32
```


<!-- P0_STEP_A_HEURISTIC_AUTHORITY_REMOVAL -->
## P0 Step A completed

Heuristic failure fingerprints, private-dependency guesses and broad
failing-Testbench preservation rules no longer block real tools.
See [`P0_HEURISTIC_AUTHORITY_REMOVAL.md`](../history/P0_HEURISTIC_AUTHORITY_REMOVAL.md).

```text
STEP_A=completed
ACTIVE_STEP=B
DEFAULT_LLM_CALLS=32
P0_STATUS=active_retry_required
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```


<!-- P0_STEP_B_HIDDEN_BOUNDARY_CORRECTION -->
## P0 Step B completed

The data direction is now Public-to-Hidden only. Candidate generation precedes
held-out generation, and `model_data_boundary.json` provides fail-closed
evidence. See
[`P0_HIDDEN_BOUNDARY_CORRECTION.md`](../history/P0_HIDDEN_BOUNDARY_CORRECTION.md).

```text
STEP_B=completed
ACTIVE_STEP=C
DEFAULT_LLM_CALLS=32
P0_STATUS=active_retry_required
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```



<!-- P0_STEP_C_DUAL_GENERATION_PROFILES -->
## P0 Step C completed

The source-only product entrypoint now defaults to a low-call `lightweight`
Testbench-generation path. Iterative Public coverage and multi-trajectory
selection require explicit `coverage-enhanced` selection. See
[`P0_DUAL_GENERATION_PROFILES.md`](../history/P0_DUAL_GENERATION_PROFILES.md).

```text
STEP_C=completed
ACTIVE_STEP=D
DEFAULT_LLM_CALLS=32
P0_STATUS=active_retry_required
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```

<!-- P0_STEP_D_TESTBENCH_STUB_PROMPT_REFINEMENT -->
## P0 Step D completed

Testbench and Stub generation now follow one black-box ownership contract.
The first qualified round freezes Public ABI/macros, coverage-only refinement
reuses the matching Stub, and real tool diagnostics route Testbench, Stub and
ABI failures without exposing Hidden content. See
[`P0_TESTBENCH_STUB_PROMPT_REFINEMENT.md`](../history/P0_TESTBENCH_STUB_PROMPT_REFINEMENT.md).

```text
STEP_D=completed
ACTIVE_STEP=E
DEFAULT_LLM_CALLS=32
P0_STATUS=active_retry_required
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```

<!-- P0_STEP_E_REPAIR_BUDGET_PARAMETERIZATION -->
## P0 Step E completed

Testbench and Candidate repair now share default 3-attempt budgets, a user range
of 1..10 and a safety ceiling of 10. Normal and advanced CLIs reject invalid
values before provider launch, while both loops retain all prior safe summaries
and do not add a no-progress early stop. See
[`P0_REPAIR_BUDGET_PARAMETERIZATION.md`](../history/P0_REPAIR_BUDGET_PARAMETERIZATION.md).

```text
STEP_E=completed
ACTIVE_STEP=F
DEFAULT_TESTBENCH_REPAIRS=3
DEFAULT_CANDIDATE_REPAIRS=3
REPAIR_SAFETY_CEILING=10
DEFAULT_LLM_CALLS=32
P0_STATUS=active_retry_required
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```
