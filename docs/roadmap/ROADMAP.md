# AgRefactor++ Development Roadmap

> **权威范围文档。** 后续开发、新对话、阶段验收与论文表述均以本文档为准。任何核心目标不得在没有明确决策记录、代码证据与文档更新的情况下被删除、弱化或偷换概念。

<!-- P4_0F_R5_CURRENT_ROUTE:BEGIN -->
## 当前 Pre-Stage-4 执行路线（2026-08-07 冻结）

```text
R5-D accepted at 0ca5dd99fabec1c2c003446975e28128a0926c52
→ R5-E-R1 Native CSIM/COSIM owner and typed-outcome correction
→ complete five-case R5-E rerun and independent archive audit
→ R5 accepted
→ Legacy differential batch A
→ Real-code discovery batch A
→ P4-0F-Final budget and CLI closure
→ P4-0G dynamic-v1
→ Real-code discovery batch B
→ P4-0H repeated multi-kernel authority matrix
→ P4-0I documentation closure
→ Stage 4
```

Legacy differential 和 discovery 是 bounded diagnostic lane。P0/P1 产品回归会重开 R5.x，但这两条 lane 不替代 P4-0F-Final 或 P4-0H，也不能独立证明稳定模型质量或 PPA 优势。

Authority: [P4-0F-R5 plan v2](PRE_STAGE4_P4_0F_R5_AUTHORITATIVE_EXECUTION_PLAN_V2.md).
<!-- P4_0F_R5_CURRENT_ROUTE:END -->

## 1. 项目使命

AgRefactor++ 是一个**目标环境条件化、模型可插拔、证据驱动、预算约束**的 HLS 自动修复、优化与迁移智能体。

用户指定模型/API、Memory 模式、目标 Vitis HLS 环境、器件/时钟/资源约束、验证方式与调用预算。系统必须依据真实编译、测试、csim、csynth 和报告证据，在预算内完成修复与优化，返回 `best_correct`，并保留完整可追溯轨迹。

项目必须同时覆盖两类任务：

### 模式 A：普通 C/C++ → 目标 HLS

```text
普通 C/C++ 或不可综合程序
+ TargetProfile
→ 目标环境下功能正确、可综合并经过优化的 HLS
```

### 模式 B：已有 HLS → 目标版本迁移

```text
已有 HLS
+ optional SourceProfile
+ TargetProfile
→ 目标版本下经过修复、验证、优化与 PPA 比较的 HLS
```

**版本感知迁移是不可删除的核心目标。**

## 2. 八项不可删除的核心能力

| # | 核心能力 | 最终完成含义 |
|---|---|---|
| 1 | TargetProfile | Vitis 版本、settings、工具命令、part、platform、clock、资源、flags、Tcl 和 report parser 真正控制执行，而不是只出现在 JSON 中。 |
| 2 | 双模式目标版本处理 | 同时支持普通 C/C++→目标 HLS，以及已有 HLS→目标版本迁移。source version 可选，不要求自动识别。 |
| 3 | Model API Registry | Provider-neutral；模型由用户授权选择；API key 只通过环境变量；默认 fixed policy。 |
| 4 | 分层 Prompt 适配 | 公共任务契约 + 当前阶段 + TargetProfile + 模型家族适配 + 当前证据 + gated Memory + 输出契约。 |
| 5 | 结构化反馈与证据状态机 | compile、public test、csim、csynth、timing、resource、tool error 等证据决定合法下一步。 |
| 6 | 假设驱动三级安全优化器 | Structural → Bottleneck → Pragma；cheap gate、checkpoint、rollback、cache、候选树与 `best_correct`。 |
| 7 | Memory Applicability Gate | `off/gated/always`、正负经验、适用性评分、拒绝原因、禁用条件与 Retrieval Abstention。 |
| 8 | BudgetManager | 记录并硬限制 LLM、token、cost、compile、test、csim、csynth、cosim 与 wall time；预算耗尽返回 `best_correct`。 |

详细追踪见 [`GOAL_TRACEABILITY.md`](GOAL_TRACEABILITY.md)。

<!-- PRE_STAGE3_PRODUCTIZATION_PLAN:BEGIN -->
## 2.1 Pre-Stage-3 产品化关闭合同

进入 Stage 3 前必须完成
[`PRE_STAGE3_PRODUCTIZATION_PLAN.md`](PRE_STAGE3_PRODUCTIZATION_PLAN.md)。

普通用户合同：

```bash
python -m agrefactor.cli refactor kernel.cpp   --top process_top   --model <logical-model>
```

不存在省略 `--top` 的普通模式。普通用户只选择
`refactor / optimize / full`；`--legacy / --repair-aware`
是兼容实现细节，不是产品模式。

关闭顺序：

```text
known-model compatibility profiles
→ independent Public/Hidden source contract
→ source-only bootstrap
→ Execution Identity
→ concise output
→ real DFS accepted by Stage 2 formal backend
→ cleanup/deprecation audit
→ Stage 3
```

首批静态模型范围为 DeepSeek、Kimi、GLM、MiniMax、Qwen 和
Generic OpenAI-compatible；动态未知模型探测和自动路由后置。

<!-- PRE_STAGE3_FINAL_RESULT -->
Closure result:

```text
STEP6_P0_REAL_DFS=passed
STEP7_CLEANUP_DEPRECATION_CLOSURE=passed
DOCUMENTATION_CONSISTENCY=passed
PRE_STAGE3_CLOSED=true
STAGE3_STARTED=false
NEXT_STEP=STAGE3_PLANNING
```

Stage 3 is now allowed but has not started.

Documentation reconciliation evidence:
[`PRE_STAGE3_DOCUMENTATION_CONSISTENCY_ACCEPTANCE.md`](../acceptance/pre-stage3/PRE_STAGE3_DOCUMENTATION_CONSISTENCY_ACCEPTANCE.md).

<!-- PRE_STAGE3_PRODUCTIZATION_PLAN:END -->

<!-- PRE_STAGE3_BUDGET_PRICING_REFINEMENT:BEGIN -->
### Budget product semantics before Stage 3

The productization closure must distinguish system defaults from system safety
ceilings. User-specified hard budgets may lower or raise defaults only within
those ceilings. Token and estimated cost remain observed-only soft budgets
until a separately validated reservation mechanism exists.

Model Profiles must carry official pricing provenance and estimation quality so
P5 can report estimated cost without presenting it as a final provider bill.
<!-- PRE_STAGE3_BUDGET_PRICING_REFINEMENT:END -->

## 3. 全局不可违反的规则

### 3.1 用户决定模型

默认 `model_policy=fixed`。系统不得擅自换模型。只有用户显式提供 `allowed_models` 并启用 `auto`，系统才可在授权范围内选择。

### 3.2 用户决定 Memory 模式

正式保留：

```text
memory_mode=off
memory_mode=gated
memory_mode=always
```

系统不能把 `off` 静默改成 `gated`，也不能把 `gated` 退化成“始终注入”。

### 3.3 Correctness First

```text
功能未通过
→ 禁止进入 PPA 优化
```

任何 compile/public test/csim 失败的候选都不能因为某项综合指标好看而被接受。

### 3.4 best_correct 不可破坏

必须维护：

```text
baseline
current_candidate
best_correct
best_ppa
```

失败候选、异常候选、PPA 退化候选都不能覆盖 `best_correct`。

### 3.5 工具调用也属于预算

预算不仅包括 token，还包括 compile、public test、csim、csynth、cosim 和 wall time。

### 3.6 TargetProfile 必须真实驱动工具

下面这种情况不算完成：

```text
TaskSpec 显示 2023.2 / xcu200 / 5 ns
但 Tcl 和实际工具仍使用固定旧值
```

必须持久化 effective profile、实际命令、Tcl、part、clock 与 parser 证据。

### 3.7 Memory 必须能够拒绝

Memory 是可选证据，不是强制 Prompt 装饰。低置信度时必须允许 Retrieval Abstention。

### 3.8 所有候选必须可追溯

每个候选至少记录：

```text
parent
生成模型
Prompt profile
因果假设
修改范围
使用的 Memory
工具证据
PPA
预算
接受/拒绝/回滚原因
```

### 3.9 只对真实验证能力标记“支持”

- 类存在，不等于完成；
- 参数存在，不等于真正下传；
- 单 kernel 成功，不等于普适；
- 一次迁移成功，不等于支持任意版本对；
- 单次 PPA 改善，不等于稳定优化收益。

### 3.10 远期研究不挤占当前关键路径

以下方向保留接口，但不作为当前 Stage 0–6 的核心交付：

- XRT→AVED 等平台/运行时迁移；
- repository-level HLS migration；
- Host/Kernel/Build/Runtime/Platform 联合迁移；
- AST/Clang/MLIR 技能自动生成；
- 完整版本知识图谱；
- 大规模自动模型路由；
- 任意程序形式化等价证明。

## 4. Stage 0 — 基线冻结

### 4.1 目标

证明原始 AgRefactor 与当前 AgRefactor++ 基础能力在真实环境中可运行，并留下可重复基线。

### 4.2 应完成内容

- 固定可运行 commit；
- 固定 Ubuntu、Python、Vitis HLS 环境；
- 固定模型/API 配置；
- 复现 `flow.new`；
- 验证基础 RAG；
- 验证 `flow.parallel_kernel` 框架；
- 验证 `opt.simple_iter`；
- 保存日志、上下文、代码、token、cost、csim、csynth 和报告；
- 明确区分原项目已有、AgRefactor++ 新增、当前环境真实验证、仅存在代码但未验证。

### 4.3 当前已完成

- Ubuntu 22.04、Python 3.10、Vitis HLS 2023.2；
- DeepSeek V4 Flash/Pro OpenAI-compatible；
- 单 kernel 真实重构；
- RAG 成功/失败 trial 写入与检索；
- `flow.parallel_kernel` 调度框架部分验证；
- `opt.simple_iter` 真实多轮 baseline；
- 基础 token/cost；
- HeteroRefactor 因 ROSE/EDG 外部依赖暂停。

### 4.4 当前限制

- 真实端到端样例仍主要集中于 DFS `process_top`；
- parallel 的有限成功率不是正式 benchmark；
- `simple_iter` 只是线性 baseline；
- coverage/hidden TB 尚未成为稳定主验证路径；
- 版本迁移尚未实现。

### 4.5 完成判定

Stage 0 作为复现基线**基本完成**。后续可追加证据，但不能把未验证模块回写成“已验证”。

详细文档：[`STAGE0_BASELINE.md`](STAGE0_BASELINE.md)。

## 5. Stage 1 — 共享基础设施

### 5.1 目标

建立修复、优化、Memory 与迁移共同使用的共享底座，避免在 legacy `flow/`、`opt/` 中继续形成不一致逻辑。

### 5.2 必须包含

#### TargetProfile

最终控制：

```text
Vitis version
settings64.sh
tool command
part/device
platform
clock period
resource limits
compile flags
Tcl generation
report parser
```

#### Model Registry

- provider-neutral；
- API key 只通过环境变量；
- fixed policy 默认；
- auto 仅在用户授权模型池中工作。

#### TaskSpec 与统一 CLI

```text
refactor
optimize
full
后续 migrate
```

#### Trace

统一记录阶段、证据、模型调用、候选、决策与结果。

#### BudgetManager

记录并硬限制：

```text
LLM calls
input/output tokens
cost
compile
public test
csim
csynth
cosim
wall time
```

这里的工具预算按真实执行语义解释：

- `public test` 是测试集角色和反馈可见性，不是新的物理进程；当前由
  `compile_calls`、`csim_calls` 和 `tool_calls` 约束真实执行；
- 原始 AgRefactor 没有活跃的 RTL cosim 调用，因此 Stage 1 Core 不创建
  空的 `cosim_calls`；以后加入真实 `cosim_design` 或等价调用时，再实现
  专项预算与证据；
- 不得为了补齐名词清单而创建没有真实 call site 的伪预算字段。

### 5.3 Stage 1 Core：已完成并关闭

已完成的共享底座：

- `TaskSpec`、`TargetProfile`、`RunMode`；
- Model Registry 与 OpenAI-compatible Provider；
- Evaluator/Evidence 基础接口；
- `UnifiedRunner`、`RunContext` 与统一 CLI；
- `TraceRecorder`；
- Budget core；
- Legacy Refactor Adapter；
- AutoGen 与 testbench repair known usage 合并。

TargetProfile 本地执行核心已真实完成：

- default profile 与 partial override；
- Vitis version 与实际 `vitis-run --version` 校验；
- `AGREFACTOR_VITIS_RUN` executable override；
- part/device、clock、compile flags；
- target-aware Tcl；
- requested/actual version mismatch 在 csynth 前阻断；
- `effective_target_profile.json`；
- `csynth_invocation.json`；
- remote non-default target 显式拒绝；
- Vitis HLS 2023.2 真实 csynth 验收。

BudgetManager 的活跃本地工具硬控制已完成：

- aggregate `max_tool_calls/tool_calls`；
- `max_compile_calls/compile_calls`；
- `max_csynth_calls/csynth_calls`；
- `max_csim_calls/csim_calls`；
- prospective/pre-call hard check；
- real launch 前 exact-once accounting；
- success、failure、timeout、launch exception 语义；
- UnifiedRunner → LegacyRefactorAdapter → legacy tool flow 使用同一个
  `BudgetManager`；
- 预算不足时在真实工具启动前阻断；
- structured invocation evidence。

确定性验收：

```text
204/204 passed
```

真实代表性 kernel 总验收：

```text
src/heterorefactor/dfs/kernel.cpp
→ UnifiedRunner
→ shared BudgetManager
→ real Testbench Preflight g++
→ real Vitis HLS 2023.2 csynth
→ real csim g++
→ real generated ./csim
→ exhausted-budget extra Preflight blocked before g++
```

精确预算使用量：

```text
tool_calls=4
compile_calls=2
csynth_calls=1
csim_calls=1
REAL_DFS_FULL_CHAIN_BUDGET_READY=1
```

运行目录：

```text
/data/agrefactor_runs/
stage1_real_dfs_full_chain_budget_v2_20260716_111358
```

该验收使用真实上游 DFS 原始代码和确定性 synthesis-safe reference
candidate，没有调用 LLM API，因此证明的是 Stage 1 工具与预算基础设施，
不代表智能体已经自动重构 DFS。

验收记录：

- [`stage1_target_profile_acceptance.md`](../acceptance/stage1/stage1_target_profile_acceptance.md)；
- [`stage1_csynth_budget_acceptance.md`](../acceptance/stage1/stage1_csynth_budget_acceptance.md)；
- [`stage1_compile_csim_budget_acceptance.md`](../acceptance/stage1/stage1_compile_csim_budget_acceptance.md)；
- [`stage1_core_acceptance.md`](../acceptance/stage1/stage1_core_acceptance.md)。

### 5.4 Stage 1 Hardening 与跨 Stage 边界

下列内容保留在路线图中，但不阻塞 Stage 1 Core 关闭。

#### TargetProfile Hardening

1. stable named target profiles；
2. per-profile executable；
3. per-profile settings script；
4. platform；
5. resource limits；
6. report parser profile；
7. per-field effective-value provenance；
8. 更多 Vitis 版本、器件和真实 kernel 验证。

当前多版本显式方式仍是：

```text
TaskSpec.target.toolchain_version
+
AGREFACTOR_VITIS_RUN=/path/to/matching/vitis-run
```

#### Model Registry Hardening

- 当前 provider-neutral registry 和用户显式指定模型路径已完成；
- `model_policy=fixed` 的正式配置契约仍需稳定化；
- `auto` 只能在用户授权的 `allowed_models` 内工作，仍需后续实现和验收。

#### TaskSpec、CLI 与 Trace 的后续接入

- `TaskSpec`/`UnifiedRunner` 已具备 `refactor/optimize/full` 结构；
- 当前真实 legacy CLI 只接入 `refactor`；
- `optimize/full` 的真实 adapter、候选 checkpoint、rollback 和候选树属于
  Stage 3；
- 当前 Trace 已覆盖运行、阶段、预算和工具证据；
- parent、假设、接受/拒绝、回滚、`best_correct` 等候选级轨迹属于 Stage 3。

#### Test 与 cosim 的边界

- public/hidden test split、suite identity、coverage 和 feedback visibility
  属于 Stage 2；
- public test 的物理执行继续由 compile/csim 预算控制；
- 当前不创建独立 `public_test_calls`；
- cosim 不在原项目活跃路径内，可在后期作为 RTL co-simulation 能力单独建设。

#### Budget exhaustion 与 `best_correct`

Stage 1 已能在预算不足时阻断后续真实工具调用。

以下候选级语义属于 Stage 3：

```text
预算耗尽
→ 停止生成新候选
→ 保留 checkpoint
→ 返回 best_correct
```

#### 稳定配置模板

仍需补充：

- target profile examples；
- model registry examples；
- 不含 secret 的稳定配置模板。

### 5.5 完成判定

Stage 1 Core 的关闭条件是：

```text
TargetProfile 真正控制一次真实 Vitis run
+
BudgetManager 真正控制所需活跃本地工具调用
+
真实代表性 kernel 完成共享预算全链路
```

上述三项已经在 commit `e37496f` 前后形成完整代码、测试、真实工具证据
和验收文档，因此：

> **Stage 1 Core 已关闭。**

这不等于：

- Stage 1 Hardening 已全部完成；
- 支持任意 Vitis 版本、器件或 kernel；
- 真实 `optimize/full/migrate` adapter 已完成；
- LLM 智能体已经自动重构 DFS；
- Stage 3 的 `best_correct`、候选树和安全优化器已完成。

下一主线：

```text
Stage 2.6 Closure-readiness Audit
→ Stage 2.7 Cross-stage Validation and Repair Hardening
→ Stage 2.8 Final Documentation and Stage 2 Closure
→ Stage 3 Safe Three-Level Optimizer
```

详细验收文档：
[`stage1_core_acceptance.md`](../acceptance/stage1/stage1_core_acceptance.md)。

## 6. Stage 2 — 结构化证据闭环

### 6.1 完整范围

Stage 2 不等于 testbench repair。完整范围是：

```text
2.1 Public/Hidden Test Roles and Evidence
2.2 General Feedback and Validation Strategy
2.3 Runtime Evidence-loop Integration
2.4 Shared Layered Prompt Builder
2.5 Multi-type Kernel Smoke Matrix
2.6 Closure-readiness Audit
2.7 Cross-stage Validation and Repair Hardening
2.8 Final Documentation and Closure
```

### 6.2 Stage 2.1：Public/Hidden 评测角色与证据 — 核心完成

已经完成：

- `TestSuiteSpec`、suite identity、version、case count 与 split；
- Public feedback 可见、Hidden feedback 不可见；
- `TaskSpec` 携带多个 Public/Hidden suites；
- operator-full 与 agent-safe 测试证据；
- Hidden 证据默认脱敏；
- split-aware trace；
- CSIM legacy result → suite evidence；
- Public/Hidden 多 suite feedback composition；
- Hidden operator-only composition；
- Public candidate failure 可进入 candidate repair 路由。

Public/Hidden 是评测角色，不新增伪造的
`public_test_calls/hidden_test_calls`。物理执行继续由
`tool_calls/compile_calls/csim_calls` 表示。

### 6.3 Stage 2.2：通用反馈与验证策略 — 核心完成

已经完成：

- 通用 Feedback Schema；
- Preflight / CSYNTH / Test Evaluation adapters；
- CSYNTH invocation 与 artifact 证据；
- 确定性 CSYNTH diagnostic parser；
- operator-full / agent-safe feedback views；
- CSYNTH 与测试 feedback composers；
- deterministic Feedback Router；
- Validation State Machine；
- Validation Feedback Coordinator；
- budget、toolchain、configuration、task input、testbench、candidate、
  original、unknown 与 mixed 路由；
- Hidden candidate/testbench/original failure 终止且不向 agent 暴露；
- Unknown 不被猜测为 candidate repair。

### 6.4 Stage 2.3：真实运行时证据链接入 — 核心完成

已经完成：

```text
ValidationOrchestrator
→ real Preflight handler
→ real CSYNTH handler
→ real Public CSIM handler
→ real Hidden CSIM handler
```

运行时属性：

- 所有 handler 共享同一个 `RunContext`；
- 共享同一个 `BudgetManager` 和 `TraceRecorder`；
- Public suites 按声明顺序执行并收集非终止反馈；
- Hidden suites 在首个 blocking result 处 fail-fast；
- Public agent feedback 去除路径、命令和 operator artifact；
- Hidden operator report 在协调与 trace 边界整体抑制；
- 预算不足在真实工具启动前阻断；
- runtime 高层 integration 使用 lazy exports，避免 package import cycle；
- `ValidationOrchestrator` 保持 handler-agnostic；
- `UnifiedRunner`、CLI、repair 和 model prompt 尚未与新链路耦合。

确定性验收：

```text
531/531 passed
```

真实 Vitis HLS 2023.2 验收：

```text
real g++ Preflight
→ real Vitis CSYNTH
→ real Public CSIM
→ real Hidden CSIM
→ accepted
```

精确物理预算：

```text
tool_calls=6
compile_calls=3
csynth_calls=1
csim_calls=2
```

还验证了：

- Public 通过、Hidden-only mismatch → `rejected`；
- Hidden diagnostic 不进入普通 result/trace；
- `max_csim_calls=0` 在 compile 前阻断；
- zero-budget usage 为 0/0/0；
- 不创建 fake Public/Hidden 计数器。

验收目录：

```text
/data/agrefactor_runs/
stage2_real_csim_handler_resume5_20260717_184240
```

详细记录：
[`stage2_runtime_evidence_acceptance.md`](../acceptance/stage2/stage2_runtime_evidence_acceptance.md)。

### 6.5 Stage 2.4：Shared Layered Prompt Builder — 已完成

Stage 2.4 的正式范围已经完成，没有额外规划的 `2.4.4` 或
`2.4.4.1`。为了控制实现规模，内部拆分为：

```text
2.4.1 Shared Layered Prompt Core
2.4.2 Testbench Repair Migration
2.4.3.1 Candidate Repair Prompt Policies
2.4.3.2 Candidate Model Adapter / Response Contract
2.4.3.3 Bounded Candidate Repair Loop
2.4.3.4 Safe ValidationOrchestrator Integration
```

共享 Prompt 已包含：

```text
公共系统不变量
+ TaskSpec/任务契约
+ 当前验证阶段
+ effective TargetProfile
+ 模型家族适配入口
+ agent-safe 当前证据
+ 允许修改范围
+ 历史尝试摘要
+ caller-approved Memory snippets
+ 输出格式与禁止事项
```

首批消费者已经接入：

- testbench repair；
- candidate compile repair；
- CSYNTH candidate repair；
- Public CSIM mismatch repair。

Candidate repair 链已经形成：

```text
agent-safe repair handoff
→ shared layered Prompt
→ provider-neutral CandidateModelAdapter
→ strict complete-replacement contract
→ bounded repair loop
→ changed candidate 从 Preflight 重新验证
→ real CSYNTH / Public CSIM / Hidden CSIM
```

最新功能提交：

```text
dd0ee927a5dac6691180c0772661cd90befe64ea
feat: integrate candidate repair orchestration
```

最新确定性测试为 `662/662 passed`。真实本地验收完成：

```text
broken Candidate
→ real g++ Preflight
→ local deterministic FakeProvider
→ repaired g++ Preflight
→ real Vitis HLS 2023.2 CSYNTH
→ real Public CSIM
→ real Hidden CSIM
→ accepted
```

精确预算为：

```text
tool_calls=7
compile_calls=4
csynth_calls=1
csim_calls=2
llm_calls=1
tokens=60
cost_usd=0.02
```

Hidden evidence 不进入模型 Prompt、普通结果或普通 trace。该验收使用
本地 FakeProvider，不等于真实网络模型 API，也不证明任意 kernel 支持。

### 6.6 Stage 2.5：多类型 kernel smoke matrix — 已完成

Stage 2.5 已完成：

```text
2.5.1 Smoke Corpus / Ground Truth Contract
→ 2.5.2 Real Full-chain Pass Matrix
→ 2.5.3 Fault / Ownership / Hidden Matrix
→ 2.5.4 Evidence Summary
```

证据：

- 七类 committed baseline；
- 7 条 baseline ground truth；
- 7/7 real Preflight；
- 7/7 real Preflight → Vitis 2023.2 CSYNTH → Public → Hidden；
- 9 条 fault ground truth；
- 16 条独立标签；
- 23 次验收场景执行，其中 19 次真实工具、4 次确定性路由；
- 当前 `727/727` full unittest。

跨三个独立验收运行的累计物理使用：

```text
62 tool / 36 compile / 9 csynth / 17 csim / 0 LLM
```

该累计值不是一次共享预算。统一证据：

- [`stage2_smoke_evidence_summary.md`](stage2_smoke_evidence_summary.md)；
- [`stage2_smoke_evidence_index.json`](../stage2_smoke_evidence_index.json)。

Stage 2.5 不证明任意 HLS、统计归属准确率、真实网络模型修复或跨版本支持。

### 6.7 Stage 2.6：Closure-readiness Audit — 已完成

审计结论：

```text
satisfied=4
blocking_before_stage3=5
defer=4
future_or_external=4
```

进入 Stage 3 前的五个 blocker：

1. 正式 repair-aware UnifiedRunner / CLI 构造；
2. Testbench/Candidate 统一 repair Protocol 与 artifact schema；
3. 最小 ModelFamilyProfile / capability tags；
4. Stage 1 Hardening Batch A；
5. 一次用户指定真实网络模型 candidate-repair 闭环。

CandidateResponseContract 新语法与 CSYNTH parser 新规则没有被 Stage 2.5
证据证明有缺陷，因此保持 evidence-gated；Ground-truth corpus 已满足 Stage 2
独立标签要求，只在 2.7 重验证。

详见：

- [`STAGE2_CLOSURE_READINESS_AUDIT.md`](STAGE2_CLOSURE_READINESS_AUDIT.md)；
- [`stage2_closure_readiness_audit.json`](../stage2_closure_readiness_audit.json)。

### 6.8 Stage 2.7：Cross-stage Validation and Repair Hardening — 已完成

冻结顺序：

```text
2.7.1 Repair Protocol and Artifact Schema（已完成）
→ 2.7.2 Minimal ModelFamilyProfile（已完成）
→ 2.7.3 Stage 1 Hardening Batch A（已完成）
→ 2.7.4 Formal Repair-aware UnifiedRunner / CLI（已完成）
→ 2.7.5 Real Network-model Candidate Repair Smoke（已完成）
→ 2.7.6 Evidence-gated Contract/Parser Delta + Ground-truth Revalidation（已完成）
→ 2.7.7 Cross-stage Regression and Stage 2.8 Handoff（已完成）
```

2.7.1 已完成 shared repair envelope、typed payload 与原子 artifacts。

2.7.2 功能提交：

```text
a9ec856540940f1767fe245a3c662468293fda5b
feat: add minimal model family profiles
```

2.7.2 已完成：

- vendor-neutral `ModelCapabilityTag`；
- typed `ModelFamilyProfile`；
- 五个最小 capability tags；
- 无 credential 的 safe default parameters；
- `profile < ModelSpec < call override` 参数优先级；
- Registry 对用户固定逻辑模型解析 profile；
- Candidate/Testbench 共用 profile instruction 与安全 manifest；
- legacy family string / family instruction 兼容；
- Response Contract、模型选择和工具链均未放宽或改变。

验收见
[`stage2_model_family_profile_acceptance.md`](../acceptance/stage2/stage2_model_family_profile_acceptance.md)。

Stage 2.7.3 功能提交：

```text
411d1e2b37ae6e620c0b759b98f7e8277cb851c4
feat: harden target execution profiles
```

已完成一个稳定 committed named profile、per-profile executable/settings、
parser identity、resource-limit schema 和逐字段 provenance；保持 Vitis 2023.2
默认行为，不增加第二版本或设备矩阵。验收见
[`stage2_stage1_hardening_batch_a_acceptance.md`](../acceptance/stage2/stage2_stage1_hardening_batch_a_acceptance.md)。

Stage 2.7.4 功能提交：

```text
7e9aef66ba062b25465f6552f9bf346b8ed5eb86
feat: add formal repair-aware runner phase
```

正式 CLI 现在通过显式 `--repair-aware` 构造：

```text
TaskSpec
→ UnifiedRunner
→ CandidateRepairPhase
→ CandidateRepairValidationOrchestrator
→ LocalCandidateValidationHandlerFactory
→ versioned safe run/phase/repair artifacts
```

一个 UnifiedRunner run 只创建一个 BudgetManager 和一个 TraceRecorder；
legacy、dry-run 与 repair-aware 三种模式显式互斥。验收见
[`stage2_repair_aware_cli_acceptance.md`](../acceptance/stage2/stage2_repair_aware_cli_acceptance.md)。

Stage 2.7.5 已在代码基线 `7407da78b9371e853b44a201828ce4b9251fad8f` 上完成一次真实
OpenAI-compatible network-model Candidate repair smoke：

```text
model=deepseek-v4-flash
response_model=deepseek-v4-flash
base_url=https://api.deepseek.com
attempt_status=validation_failed
orchestration_status=validation_terminal
repair_stop_reason=terminal_feedback
response_contract=accepted
total_tokens=1106
outcome=可信 terminal failure（validation_failed）
```

真实初始 g++ Preflight、模型 request/response/usage、strict contract、共享预算、
bounded terminal result 与 Hidden 无泄漏均有 artifacts。验收见
[`stage2_real_network_candidate_repair_smoke.md`](stage2_real_network_candidate_repair_smoke.md)。

Stage 2.7.6 已在代码基线 `b1a787ab0e41b382fec25973968e2b162a500f85` 上完成 evidence gate：

```text
replayed_preflight_failure_kind=link_error
replayed_preflight_failure_owner=unknown
contract_delta_required=false
parser_delta_required=false
code_delta_applied=false
```

真实 proposal 仍满足现有 CandidateResponseContract；随后失败属于真实 Preflight
验证域。2.7.5 没有 CSYNTH invocation，因此不能推出 parser 新规则。

随后重新执行七条 baseline 全链与九条 fault/ownership/Hidden 场景：

```text
ground_truth_labels=16/16
baseline_full_chains=7/7
fault_matrix=9/9
real_scenarios=12
deterministic_scenarios=4
combined_usage=55/29/9/17/0
```

验收见
[`stage2_evidence_gated_ground_truth_revalidation.md`](stage2_evidence_gated_ground_truth_revalidation.md)。

Stage 2.7.7 已完成跨阶段回归和 Stage 2.8 frozen handoff：

```text
related_tests=389/389
full_unittest=836/836
evidence_milestones=8/8
blockers_satisfied=5/5
artifact_manifests=8
artifact_manifest_entries=34
closure_checklist=9/10
```

2.7.7 未新增功能、未调用网络模型、未执行新的正式 Vitis acceptance，并保持
Stage 2 open 直到 2.8。

### 6.9 Stage 2.8：最终文档、复现和关闭 — 已完成

在代码基线 `3f57371c8b58f53449064219c024ab63042a87d4` 上完成 C-09 final documentation synchronization，
同步 README、USAGE、REPRODUCTION_STATUS、CHANGELOG、ROADMAP、Goal
Traceability、Project State、Handoff、Stage 2 Evidence/Hardening 文档和正式
closure acceptance。

```text
related_tests=389/389
full_unittest=836/836
closure_checklist=10/10
stage2_closed=true
stage3_allowed=true
new_network_model_calls=0
new_vitis_csynth_calls=0
new_vitis_csim_calls=0
feature_code_changes=0
```

正式关闭记录：
[`stage2_closure_acceptance.md`](../acceptance/stage2/stage2_closure_acceptance.md)。

### 6.10 Stage 2 完成标准

```text
Testbench Reliability 完成
+ Public/Hidden roles and evidence 完成
+ General feedback/state strategy 完成
+ Runtime evidence-loop integration 完成
+ Shared Layered Prompt Builder 接入
+ Multi-type Smoke 与独立 ground truth 完成
+ Closure-readiness Audit 完成
+ 阻塞 Stage 3 的 Hardening 完成
+ 最终文档与复现同步完成
```

当前准确表述：

> **Stage 2.1–2.8 已完成，Stage 2 正式关闭。下一阶段是 Stage 3 Safe Three-Level Optimizer。
> Stage 2 的有限证据不能外推为任意 kernel、任意 Vitis 版本、稳定模型修复成功率或已完成 PPA 优化。**

详细文档：

- [`STAGE2_EVIDENCE_LOOP.md`](STAGE2_EVIDENCE_LOOP.md)；
- [`STAGE2_HARDENING_PLAN.md`](STAGE2_HARDENING_PLAN.md)；
- [`stage2_acceptance.md`](../acceptance/stage2/stage2_acceptance.md)；
- [`stage2_runtime_evidence_acceptance.md`](../acceptance/stage2/stage2_runtime_evidence_acceptance.md)；
- [`stage2_hardening_acceptance.md`](../acceptance/stage2/stage2_hardening_acceptance.md)；
- [`stage2_closure_acceptance.md`](../acceptance/stage2/stage2_closure_acceptance.md)。

## 7. Stage 3 — 安全的三级优化器

### 7.1 目标

重新实现比公开 `simple_iter` 更安全、可解释、可回滚、预算受控的优化闭环。

不能退化为：

```text
把综合报告发给模型
→ 生成整份新代码
→ 继续综合
```

### 7.2 三级顺序

#### Level 1：Structural Optimization

- 算法结构；
- 循环重写；
- 函数边界；
- 数据布局；
- 数据局部性；
- 访存结构；
- 局部缓存；
- 流水数据路径。

#### Level 2：Bottleneck Repair

基于综合证据处理：

- II；
- loop-carried dependency；
- memory port contention；
- critical path；
- resource bottleneck；
- unknown loop bound；
- dataflow stall/deadlock risk。

#### Level 3：Pragma Tuning

最后才处理：

```text
PIPELINE
UNROLL
ARRAY_PARTITION
DATAFLOW
INLINE
RESOURCE / BIND
```

### 7.3 必须实现的机制

#### 假设驱动

一次可生成多个假设，每个假设记录 hypothesis、supporting evidence、expected benefit、risk、modification scope 和 verification plan。

一个分支围绕一个因果假设，但允许一组相互依赖的修改。

#### 廉价筛选优先

```text
static check
→ compile
→ public test
→ fast csim
→ csynth
→ optional cosim
```

#### 候选管理

每个候选记录 parent、hypothesis、patch/change summary、correctness evidence、PPA、budget、accept/reject reason。

#### Checkpoint / Rollback / Cache

- 功能失败立即回滚；
- PPA 退化不更新最佳；
- 相同 code + TargetProfile 复用结果；
- 预算耗尽返回 `best_correct`。

#### UnifiedRunner 接入

真正支持 `mode=optimize` 和 `mode=full`。`full` 必须严格执行 refactor correctness → optimization。

### 7.4 完成标准

- correctness gate 不可绕过；
- 每个候选可追溯；
- 可回滚；
- 有硬预算；
- 始终保存 `best_correct`；
- 多类型 kernel 真实优化；
- 与 `simple_iter` 对照。

### 7.5 当前实施状态

```text
S3.1 Candidate State Foundation — accepted
S3.2 Qualification and PPA Evidence — accepted
S3.2 deterministic regression — 85/85 focused, 135/135 optimizer, 1643/1643 full
S3.2 real replay — Vitis HLS 2023.2 accepted; model calls=0
S3.3 deterministic state machine — 46/46 focused, 181/181 optimizer, 1689/1689 full; model/network/Vitis calls=0
S3.4 Structural model integration — 52/52 focused, 233/233 optimizer, 1741/1741 full; bounded real smoke=2 LLM calls, no Vitis/compile/CSIM/CSYNTH
S3.5 Bottleneck model integration — 82/82 focused, 315/315 optimizer, 1823/1823 full; bounded real smoke=2 LLM calls, typed PPA fixture, no Vitis/compile/CSIM/CSYNTH
S3.6 Pragma model integration — 75/75 focused, 382/382 optimizer, 1890/1890 full; bounded real smoke=2 LLM calls, typed PPA fixture, no Vitis/compile/CSIM/CSYNTH
S3.7 Product adapters — 28/28 focused, 402/402 optimizer, 1941/1941 full; internal real chain=3 mandatory analyses plus 0–3 conditional rewrites, typed no-retry abstention, and real Vitis qualification
S3.8 evaluation payload — implemented; target-host 18-unit matrix required
```

详细文档：[`STAGE3_SAFE_OPTIMIZER.md`](STAGE3_SAFE_OPTIMIZER.md)。

<!-- PRE_STAGE4_PRODUCT_VALIDATION_HARDENING:BEGIN -->
### 7.6 Pre-Stage-4 Product and Validation Hardening

Before Stage 4 begins, the product must close the frozen contract in
[`PRE_STAGE4_PRODUCT_VALIDATION_HARDENING_CONTRACT.md`](PRE_STAGE4_PRODUCT_VALIDATION_HARDENING_CONTRACT.md).

The accepted implementation order is:

```text
typed Preflight
→ Public native Vitis CSIM
→ CSYNTH
→ Public RTL COSIM
→ Hidden
→ DeepSeek Flash/.env/Thinking hardening
→ mode-specific budgets and truthful CLI
→ bottleneck-driven dynamic-v1
→ complete revalidation
→ Pre-Stage-4 closure
```

This section freezes design only. None of the new behavior may be described as
implemented until its own code, deterministic tests, real-tool evidence, and
documentation synchronization are accepted.
<!-- PRE_STAGE4_PRODUCT_VALIDATION_HARDENING:END -->

## 8. Stage 4 — Memory Applicability Gate

### 8.1 目标

保留 AgRefactor 的跨任务经验，同时控制不相似经验带来的噪声与负迁移。

不能再是：

```text
检索到经验
→ 一定注入 Prompt
```

而必须是：

```text
检索
→ 适用性判断
→ 接受 / 拒绝 / 弃权
```

### 8.2 三种模式

```text
memory_mode=off
memory_mode=gated
memory_mode=always
```

三种模式必须保留，用于正式消融。

### 8.3 Memory Schema

至少保存 stage、target profile、source profile/version、code features、interface features、error signature、action、preconditions、avoid conditions、verification、PPA delta、token/tool cost 和 positive/negative outcome。

### 8.4 Gate 判断维度

- 错误阶段；
- 错误签名；
- kernel 结构；
- 接口类型；
- source/target version；
- part/clock/resource；
- 历史验证证据；
- 负面案例；
- 预期收益；
- 预算价值。

### 8.5 Retrieval Abstention

低置信度时允许不注入任何 Memory，同时记录 applicability score、accepted/rejected 和 rejection reason。

### 8.6 完成标准

- 正负经验都能保存；
- Gate 决策可解释；
- off/gated/always 可运行；
- 部分案例能正确弃权；
- 有负迁移率、成功率与成本对比；
- 证明 Gate 的收益，而不是只增加复杂度。

详细文档：[`STAGE4_MEMORY_GATE.md`](STAGE4_MEMORY_GATE.md)。

## 9. Stage 5 — 目标版本扩展与真实迁移

### 9.1 目标

正式实现：

```text
旧版本/源版本 Vitis HLS
→ 目标版本 Vitis HLS
```

这个目标不可删除。

### 9.2 双模式

#### Mode A

```text
普通 C/C++
+ target profile
→ 目标 HLS
```

#### Mode B

```text
已有 HLS
+ optional source profile
+ target profile
→ 迁移、修复、验证、优化与 PPA 比较
```

### 9.3 数据结构扩展

至少增加：

```text
source_profile
target_profile
mode=migrate
source baseline
target baseline
migration constraints
migration evidence
```

### 9.4 正式迁移流程

```text
源版本环境检查
→ 源版本 csim/csynth baseline
→ 保存源接口、pragma、行为和 PPA
→ 目标版本 direct run
→ 区分版本相关错误与普通错误
→ 检索迁移经验
→ Memory Applicability Gate
→ rollback-safe transformation
→ target correctness gate
→ target csynth
→ source/target behavior/PPA comparison
→ migration report
```

### 9.5 版本相关错误分类

至少包括 deprecated pragma、removed API、directive syntax change、library/type incompatibility、interface semantics change、Tcl command change、report schema change、default scheduling change、device/clock difference 和 ordinary synthesis error。

### 9.6 明确不做

- 自动识别源版本；
- 支持任意版本对；
- 一次覆盖所有历史版本；
- 任意程序形式化等价证明；
- 平台/运行时迁移；
- 仓库级迁移。

### 9.7 完成标准

至少：

- 第二个真实 Vitis Profile；
- 双版本环境实际可调用；
- 一个真实 source→target 样例；
- source baseline 通过；
- target migration correctness 通过；
- 输出修改规则与 PPA 比较；
- 能区分版本问题与普通代码问题。

**没有真实双版本实验，Stage 5 不得标记完成。**

详细文档：[`STAGE5_VERSION_MIGRATION.md`](STAGE5_VERSION_MIGRATION.md)。

## 10. Stage 6 — 系统评测、消融与最终交付

### 10.1 目标

Stage 6 不再新增主要模块，而是证明 Stage 0–5 的功能真实有效、稳定且可复现。

### 10.2 固定任务类别

| 类别 | 任务 |
|---|---|
| A | 动态内存、递归、容器、复杂指针等不可综合 C/C++ |
| B | 已有 HLS compile/csynth 失败 |
| C | public test/csim 功能错误或接口不一致 |
| D | 功能正确但 latency、II 或资源较差 |
| E | Memory 容易误检索或负迁移 |
| F | 源版本可运行、目标版本失败或 PPA 漂移 |

### 10.3 基线

至少比较 current AgRefactor++ baseline、Memory off/always/gated、统一/分层 Prompt、simple_iter/安全优化器、固定 Flash/Pro 和完整系统。

### 10.4 消融链

```text
A0 当前 baseline
A1 + TargetProfile + Trace
A2 + Structured Feedback + State Machine
A3 + Layered Prompt
A4 + Safe Optimizer + Checkpoint + Rollback
A5 + Memory Gate
A6 + Hard Budget
A7 Full System
```

### 10.5 指标

#### 正确性

compile、public test、hidden test、csim、optional cosim、false success rate。

#### 综合/PPA

csynth success、timing、latency、II、LUT、FF、BRAM、DSP、resource legality。

#### 预算

LLM calls、input/output tokens、cost、compile/test/csim/csynth/cosim、wall time。

#### 搜索质量

best candidate round、rollback、invalid synthesis ratio、cache hit、candidate acceptance rate。

#### Memory

Gate acceptance、abstention、rejection、negative transfer、retrieval 后成功率变化。

#### 稳定性

多次重复均值、标准差、最坏结果和成功率。

### 10.6 最终交付

- 稳定 CLI；
- TargetProfile/Model/Prompt/Memory 配置；
- 安全优化器；
- Feedback Parser 与 State Machine；
- benchmark 和迁移样例；
- baseline、消融与重复实验；
- 运行报告；
- README、USAGE、配置与复现说明；
- 竞赛报告；
- 论文材料；
- 可复现脚本；
- release/tag。

论文整理与发布属于 Stage 6 后半段，不再增加 Stage 7。

详细文档：[`STAGE6_EVALUATION.md`](STAGE6_EVALUATION.md)。

## 11. 八项目标与 Stage 对应关系

| 初始目标 | 主要阶段 | 后续使用阶段 |
|---|---|---|
| TargetProfile | Stage 1 | Stage 2–6 |
| 双模式与版本迁移 | Stage 5 | Stage 1 预留、Stage 4 Gate、Stage 6 评测 |
| Model API Registry | Stage 1 | Stage 2–6 |
| 分层 Prompt | Stage 2 | Stage 3–6 |
| 结构化反馈与状态机 | Stage 2 | Stage 3–6 |
| 三级安全优化器 | Stage 3 | Stage 5–6 |
| Memory Applicability Gate | Stage 4 | Stage 5–6 |
| BudgetManager | Stage 1 基础、Stage 3 硬控制 | Stage 2–6 |

没有一项被删除。

## 12. 当前权威状态

| Stage | 当前状态 |
|---|---|
| Stage 0 | 基线能力保留；真实端到端样例仍主要集中于 DFS |
| Stage 1 | Core 已关闭；Hardening 按后续真实需求推进 |
| Stage 2 | 已关闭；Pre-Stage-3 产品化合同已关闭 |
| Stage 3 | S3.1-S3.7 已验收；S3.8 implemented and accepted only after target-host 18-unit matrix |
| Stage 4 | 未开始；当前 RAG 不等于 Memory Applicability Gate |
| Stage 5 | 未开始；当前 TargetProfile 不等于真实版本迁移 |
| Stage 6 | 未开始；尚未形成系统 benchmark、消融和重复实验 |

## 13. 当前执行顺序

Stage 3 当前严格按冻结实现包推进：

1. S3.1 Candidate State Foundation — 已验收；
2. S3.2 Qualification and PPA Evidence — 已验收；
3. S3.3 Deterministic Optimizer State Machine — 已验收；
4. S3.4 Structural Model Integration — 已验收；
5. S3.5 Bottleneck Model Integration — 已验收；
6. S3.6 Pragma Model Integration — 已验收；
7. S3.7 产品适配 — 已验收；
8. S3.8 多 kernel 真实验收与 `simple_iter` 公平对照 — 已实现；目标主机矩阵通过后关闭。

S3.3 已保持 FakeProvider/FakeExecutor 与确定性 fixtures；S3.4 接入 Structural hypothesis/complete-source；S3.5 接入 typed PPA projection、非权威 Bottleneck classification 与 evidence-linked rewrite；S3.6 接入 typed non-authoritative Pragma action、strict directive policy 与 complete-source rewrite，三者均以两次模型调用、零 Vitis 的 bounded smoke 验收。不得把后续 S3.7–S3.8 合并，不得把 contract-valid source、model classification 或 pragma action 冒充 correctness/PPA/tool fact；S3.7 已完成内部三层全链路演练并接通产品；S3.8 仍必须独立完成多 kernel、重复实验与公平 `simple_iter` 对照。

Stage 1 Hardening 不单独阻塞主线；当 Stage 2–5 的真实功能依赖某项
Hardening 时，先补齐并做真实验收，再继续对应 Stage。

## 14. 新对话接续协议

新对话或长时间中断后，依次阅读：

1. `docs/roadmap/PROJECT_STATE.md`
2. `docs/roadmap/ROADMAP.md`
3. `docs/roadmap/GOAL_TRACEABILITY.md`
4. 当前 Stage 文档
5. `docs/guides/REPRODUCTION_STATUS.md`
6. `docs/guides/USAGE.md`
7. 最新 acceptance
8. Git history

不得仅根据类名、参数名或一次成功运行推断完成状态。

## 15. 路线变更规则

任何涉及核心目标、阶段边界、完成标准或下一工作顺序的变更，必须同时更新：

- `ROADMAP.md`
- `GOAL_TRACEABILITY.md`
- `PROJECT_STATE.md`
- 对应 Stage 文档
- `CHANGELOG.md`
- 必要时更新原方案补充说明

禁止只在聊天中改变核心目标。

S3.7 v8 hardening: model output contract failures are typed no-retry abstentions, best_correct is preserved, and acceptance correlates semantic call/decision/candidate/qualification evidence instead of forcing rewrites.

S3.7 v9 correction: the real-smoke observer and fixtures share the canonical versioned candidate-index serializer/parser, eliminating a false negative where qualified candidates were persisted but not recognized.


S3.8 v1 freezes a bounded 18-unit matrix: array-map/reduction/nested-stencil,
two repeats, and safe-optimize/source-full/simple-iter. All arms share the same
model, effective provider parameters, Target, suites, hard ceilings, and no-retry
policy. Legacy is independently qualified before and after optimization. Stage
retention requires a complete matrix, zero infrastructure failures, accepted
direct optimize and live full evidence, and real CSYNTH on every kernel. No
stable-superiority claim is allowed.

### S3.8 V2 correction note

The first V1 target-host run cannot close S3.8 because all six Legacy units
failed in the qualification observer before `simple_iter` model execution. V2
retains the product evidence and requires a targeted six-unit Legacy rerun with
physical-call provenance before the route advances to Stage 4.
