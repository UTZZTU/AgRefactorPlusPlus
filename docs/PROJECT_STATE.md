# AgRefactor++ Current Project State

> **新对话或恢复开发时首先阅读本文档。** 权威范围见 [`ROADMAP.md`](ROADMAP.md)，目标追踪见 [`GOAL_TRACEABILITY.md`](GOAL_TRACEABILITY.md)。

## 1. 当前快照

- 当前开发分支：`stage2-general-feedback`
- 当前功能代码基线：`7e9aef66ba062b25465f6552f9bf346b8ed5eb86`
- 当前 Stage 2.7 evidence/docs 基线：`5d9ca6b76162f30e6a33c76d933ebb0021955baf`
- 最新确定性测试：**836/836 passed**
- 最新 Stage 2.5 总结：**7 baseline、7/7 real full chains、9 faults、16 labels、23 scenario executions；跨三次独立验收累计 62/36/9/17/0，0 LLM**
- 最新完整验证链验收仍为：**broken Candidate Preflight → local FakeProvider → repaired g++ Preflight → Vitis 2023.2 CSYNTH → Public CSIM → Hidden CSIM，shared exact budget 7/4/1/2 + 1 LLM call**
- Stage 1 Core 验收：[`stage1_core_acceptance.md`](stage1_core_acceptance.md)
- Testbench Reliability 验收：[`stage2_acceptance.md`](stage2_acceptance.md)
- Stage 2.3 Runtime Evidence 验收：[`stage2_runtime_evidence_acceptance.md`](stage2_runtime_evidence_acceptance.md)
- 当前关键任务：**Stage 2.7.7 Cross-stage Regression and Stage 2.8 Handoff 已完成；下一步 Stage 2.8 Final Documentation and Stage 2 Closure**
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

详见 [`stage1_csynth_budget_acceptance.md`](stage1_csynth_budget_acceptance.md)。


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

详见 [`stage1_compile_csim_budget_acceptance.md`](stage1_compile_csim_budget_acceptance.md)。


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

详见 [`stage1_core_acceptance.md`](stage1_core_acceptance.md)。

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
[`stage2_smoke_corpus_acceptance.md`](stage2_smoke_corpus_acceptance.md)。

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
[`stage2_smoke_pass_matrix_acceptance.md`](stage2_smoke_pass_matrix_acceptance.md)。

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
[`stage2_smoke_fault_matrix_acceptance.md`](stage2_smoke_fault_matrix_acceptance.md)。

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
- [`stage2_smoke_evidence_index.json`](stage2_smoke_evidence_index.json)。

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
- [`stage2_closure_readiness_audit.json`](stage2_closure_readiness_audit.json)。

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
[`stage2_repair_protocol_acceptance.md`](stage2_repair_protocol_acceptance.md)。

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
[`stage2_model_family_profile_acceptance.md`](stage2_model_family_profile_acceptance.md)。

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
[`stage2_stage1_hardening_batch_a_acceptance.md`](stage2_stage1_hardening_batch_a_acceptance.md)。

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
[`stage2_repair_aware_cli_acceptance.md`](stage2_repair_aware_cli_acceptance.md)。

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
[`stage2_hardening_acceptance.md`](stage2_hardening_acceptance.md)。

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

### Stage 2 剩余项

1. Stage 2.8 Final Documentation and Stage 2 Closure。

Stage 2.7 已完成，但 Stage 2 当前仍为 open；Stage 3 仍不允许开始。

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

下一步只做：

```text
Stage 2.8 Final Documentation and Stage 2 Closure
```

2.8 必须重新核对 2.7.7 evidence index 和 frozen closure checklist，完成
README、CHANGELOG、USAGE、REPRODUCTION_STATUS、ROADMAP、GOAL_TRACEABILITY、
PROJECT_STATE、NEXT_CHAT_HANDOFF、STAGE2_EVIDENCE_LOOP 和
STAGE2_HARDENING_PLAN 的最终同步，运行完整回归，并新增正式 closure
acceptance。

只有 2.8 的文档、测试、commit、push、local=remote 和 clean 全部通过后，
才能声明 Stage 2 closed 并进入 Stage 3。2.8 不新增 Stage 2 功能。

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

requested 与 actual 不一致时，系统会在 csynth 前阻断。完整命令见 [`USAGE.md`](USAGE.md)。

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
1. docs/PROJECT_STATE.md
2. docs/ROADMAP.md
3. docs/GOAL_TRACEABILITY.md
4. docs/STAGE1_INFRASTRUCTURE.md
5. docs/stage1_target_profile_acceptance.md
6. docs/stage1_csynth_budget_acceptance.md
7. docs/stage1_compile_csim_budget_acceptance.md
8. docs/stage1_core_acceptance.md
9. docs/STAGE2_EVIDENCE_LOOP.md
10. docs/STAGE2_HARDENING_PLAN.md
11. docs/STAGE2_CLOSURE_READINESS_AUDIT.md
12. docs/stage2_closure_readiness_audit.json
13. docs/stage2_smoke_evidence_summary.md
14. docs/stage2_smoke_evidence_index.json
15. docs/stage2_smoke_corpus_acceptance.md
16. docs/stage2_smoke_pass_matrix_acceptance.md
17. docs/stage2_smoke_fault_matrix_acceptance.md
18. docs/stage2_acceptance.md
19. docs/REPRODUCTION_STATUS.md
20. docs/USAGE.md
21. git log
```
