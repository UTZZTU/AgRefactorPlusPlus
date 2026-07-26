# Stage 1 — Shared Infrastructure

## 1. 目标

为 refactor、optimizer、Memory、budget 与 migration 建立统一、可测试、可追踪的共享架构。

Stage 1 的关闭条件是：

```text
TargetProfile 真正控制一次真实 Vitis run
+
BudgetManager 真正控制真实工具调用
```

TargetProfile 和 BudgetManager 已在真实 DFS 的 Preflight → Vitis csynth → csim 共享预算全链路中完成验收，因此 Stage 1 Core 已关闭。

## 2. 已实现的共享底座

- TaskSpec、TargetProfile、RunMode；
- provider-neutral model request/response；
- Model Registry；
- OpenAI-compatible Provider；
- Evaluator/Evidence 基础接口；
- UnifiedRunner、Phase、RunResult、RunContext；
- TraceRecorder；
- BudgetManager core；
- `python -m agrefactor.cli validate-task/run`；
- Legacy Refactor Adapter；
- known AutoGen + repair usage 合并与 artifact 去重；
- CLI、adapter、module-entrypoint regression tests。

## 3. TargetProfile 本地执行核心：已验收

当前链路：

```text
TaskSpec.target
→ LegacyRefactorAdapter
→ flow.new
→ ContextVariables.target_profile
→ flow.tools.general
→ flow.tools.csynth
→ target-aware vitis.tcl
→ selected vitis-run
→ version gate
→ real csynth
```

已实现：

- 默认 profile 与局部覆盖；
- `clock_period_ns`；
- `clock_frequency_mhz` → period 换算；
- period/frequency 冲突拒绝；
- `compile_flags` replace；
- `append_compile_flags` append；
- part/device；
- target-aware Tcl quoting；
- `AGREFACTOR_VITIS_RUN` 环境覆盖；
- executable path resolution；
- `vitis-run --version` probe；
- requested/actual strict match；
- mismatch/probe timeout/probe failure/unparseable 阻断；
- local effective target evidence；
- local csynth invocation evidence；
- remote non-default target 显式拒绝。

真实验收：

```text
Commit: 717fdef
Tests: 153/153
Run: /data/agrefactor_runs/stage1_target_profile_real_vitis_20260715_141118
Vitis: 2023.2
Device: xcu200-fsgd2104-2-e
Clock: 4.0 ns
Estimated Fmax: 342.47 MHz
Result: REAL_VITIS_SMOKE_PASSED=1
```

详见 [`stage1_target_profile_acceptance.md`](../acceptance/stage1/stage1_target_profile_acceptance.md)。

## 4. 多版本显式指定

单版本机器可以依赖加载后的 PATH。

多版本机器必须显式协调：

1. task 中的 `target.toolchain_version`；
2. `AGREFACTOR_VITIS_RUN` 指向同一版本的 launcher；
3. 建议 source 同一版本的 `settings64.sh`。

示例：

```bash
source /data/Xilinx/Vitis/2024.1/settings64.sh
export AGREFACTOR_VITIS_RUN=/data/Xilinx/Vitis/2024.1/bin/vitis-run
python -m agrefactor.cli run task-2024.1.json --legacy
```

系统会探测：

```bash
/data/Xilinx/Vitis/2024.1/bin/vitis-run --version
```

requested 与 actual 不一致时，csynth 前阻断。

当前只有 2023.2 完成真实 csynth 验收；其他版本必须单独验证。

## 5. Stage 1 Core 最终状态

### 5.1 Tool hard budget：已完成

统一契约：

```text
pre-call hard check
→ allow or block
→ real tool launch
→ exact-once accounting
→ structured evidence
```

#### compile/csynth/csim：已完成真实验收

- aggregate `max_tool_calls/tool_calls`；
- 专项 `max_compile_calls/compile_calls`、`max_csynth_calls/csynth_calls`、`max_csim_calls/csim_calls`；
- `limit=0` 在 version probe 前阻断；
- `limit=1` 第一次允许，第二次在 probe 前阻断；
- success/failure/timeout/launch exception 计一次；
- version mismatch 不消耗真实 csynth；
- UnifiedRunner/Legacy/local normal/HeteroRF 链路贯通；
- bounded remote tool budget 显式拒绝；
- 204/204 确定性测试；
- Vitis 2023.2 真实 csynth smoke；
- 真实本地 csim smoke；
- 真实 DFS Preflight → Vitis → csim 全链路通过；
- 精确使用量 `4 tool / 2 compile / 1 csynth / 1 csim`。

验收见 [`stage1_core_acceptance.md`](../acceptance/stage1/stage1_core_acceptance.md)。

public test 作为 Stage 2/3 的评测角色，由 compile/csim 执行，不新增独立预算。原始项目无活跃 cosim，当前不属于 Core；后续新增 RTL co-simulation 时再独立设计。

Stage 3 仍需实现预算耗尽时停止新候选并返回 `best_correct`。

### 5.2 TargetProfile Hardening

Stage 1 Hardening 分两批推进，不重新打开已经关闭的 Stage 1 Core。

#### Batch A：Stage 2.7 中完成，进入 Stage 3 前必须具备

- stable named target profiles；
- per-profile executable；
- per-profile settings script；
- report parser profile；
- effective value provenance；
- basic resource-limit schema；
- target/model/`.env.example` 无 secret 稳定模板。

#### Batch B：进入 Stage 5 前完成

- 更多真实 Vitis 版本；
- 更多器件与 platform；
- 版本特定 parser 差异；
- source/target profile 扩展；
- 多版本、多器件、更多真实 kernel 交叉验证。

### 5.3 稳定配置模板

稳定配置模板属于 Batch A，并必须显式说明逻辑模型、provider、family
profile、fixed policy、profile executable/settings、版本探测和 provenance。

## 6. 下一实现步骤

```text
Stage 2.5 multi-type smoke
→ Stage 2.6 closure-readiness audit
→ Stage 2.7 validation/repair hardening + Stage 1 Hardening Batch A
→ Stage 2.8 Stage 2 closure
→ Stage 3 safe optimizer
→ Stage 5 前完成 Stage 1 Hardening Batch B
```

详细边界见 [`STAGE2_HARDENING_PLAN.md`](STAGE2_HARDENING_PLAN.md)。
