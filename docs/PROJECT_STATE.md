# AgRefactor++ Current Project State

> **新对话或恢复开发时首先阅读本文档。** 权威范围见 [`ROADMAP.md`](ROADMAP.md)，目标追踪见 [`GOAL_TRACEABILITY.md`](GOAL_TRACEABILITY.md)。

## 1. 当前快照

- 当前开发分支：`stage2-general-feedback`
- 当前功能代码基线：`dc44be344f9bf9bae3eb8e43675fb49f0c017708`
- 最新确定性测试：**574/574 passed**
- 最新真实工具验收：**Preflight g++ → Vitis 2023.2 CSYNTH → Public CSIM → Hidden CSIM，shared exact budget 6/3/1/2**
- Stage 1 Core 验收：[`stage1_core_acceptance.md`](stage1_core_acceptance.md)
- Testbench Reliability 验收：[`stage2_acceptance.md`](stage2_acceptance.md)
- Stage 2.3 Runtime Evidence 验收：[`stage2_runtime_evidence_acceptance.md`](stage2_runtime_evidence_acceptance.md)
- 当前关键任务：**Stage 2.4.1、2.4.2 和 2.4.3.1 已完成；下一步 Candidate Model Adapter / Response Contract**
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

## 3. 未完成

### Stage 1 Hardening（不阻塞 Core 关闭）

1. TargetProfile stable named profiles；
2. per-profile executable/settings；
3. platform、resource limits、report parser profile；
4. effective profile 每字段 provenance；
5. 更多 Vitis 版本、器件和真实 kernel。

Stage 3 仍需实现预算耗尽时停止新候选并返回 `best_correct`。

### Stage 2 剩余项

1. Candidate Model Adapter / Response Contract；
2. bounded Candidate Repair Loop 与 ValidationOrchestrator 接入；
3. Stage 2.5 多类型真实 kernel smoke matrix；
4. Stage 2.6 最终文档、复现和关闭审查。

当前还没有：

- 新 runtime orchestrator 驱动的真实模型 candidate repair；
- CLI/UnifiedRunner 对 validation handlers 的正式构造；
- 多类型 kernel 的普适性证据。
### 后续 Stage

- Stage 3 Safe Three-Level Optimizer：未开始；
- Stage 4 Memory Applicability Gate：未开始；
- Stage 5 Target Version Extension / Real Migration：未开始；
- Stage 6 System Evaluation：未开始。

## 4. 当前下一任务

Stage 2.1–2.3、Stage 2.4.1、2.4.2 和 2.4.3.1 已完成。
下一步：

```text
A. Candidate Model Adapter / Response Contract
B. 保持 provider-neutral，不启动自动 repair loop
C. 定义完整 candidate C++ replacement 的响应解析与拒绝契约
D. 保持 Hidden evidence 永不进入模型 Prompt
E. 后续单独实现 bounded Candidate Repair Loop
F. 后续再接入 ValidationOrchestrator
G. Stage 2.5 多类型真实 kernel smoke matrix
H. Stage 2.6 最终文档与关闭审查
```

当前不提前进入 Stage 3，也不把 Prompt Policy 表述为
CandidateGenerator、模型修复闭环或真实模型验收已经完成。

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
- 204 个测试等于 204 个真实 kernel。

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
10. docs/stage2_acceptance.md
11. docs/REPRODUCTION_STATUS.md
12. docs/USAGE.md
13. git log
```
