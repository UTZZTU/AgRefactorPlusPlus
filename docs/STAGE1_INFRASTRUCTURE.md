# Stage 1 — Shared Infrastructure

## 1. 目标

为 refactor、optimizer、Memory、budget 与 migration 建立统一、可测试、可追踪的共享架构。

Stage 1 的关闭条件是：

```text
TargetProfile 真正控制一次真实 Vitis run
+
BudgetManager 真正控制真实工具调用
```

第一项已经完成，第二项仍是当前阻塞项。

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

详见 [`stage1_target_profile_acceptance.md`](stage1_target_profile_acceptance.md)。

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

## 5. 尚未完成

### 5.1 Tool hard budget

必须分别记录和限制：

```text
compile
public_test
csim
csynth
cosim
wall_time
```

每个工具调用的统一契约：

```text
pre-call hard check
→ allow or block
→ real tool launch
→ post-call accounting
→ structured evidence
```

当前先做 csynth：

- `max_csynth_calls=0`：版本探测和 Vitis 均不得启动；
- `max_csynth_calls=1`：第一次允许，第二次阻断；
- timeout/failure/exception：只要真实启动就计一次；
- mismatch 在真正 csynth 之前发生，需要明确预算语义并测试；
- 预算阻断不能伪装成综合失败；
- 后续优化阶段预算耗尽要返回 `best_correct`。

### 5.2 TargetProfile 后续配置化

仍需：

- stable named target profiles；
- per-profile executable；
- per-profile settings script；
- platform；
- resource limits；
- report parser profile；
- effective value provenance；
- 更多真实 Vitis 版本、器件和 kernel 验证。

### 5.3 稳定配置模板

- target profile examples；
- model registry examples；
- 不含 secret 的 `.env.example`；
- 多版本显式选择说明。

## 6. 下一实现步骤

```text
1. 只读审计 BudgetManager 与 csynth call graph
2. 固定 csynth budget contract
3. 确定性测试
4. 接入 local run_csynth
5. 持久化 budget evidence
6. 真实 Vitis budget smoke
7. 再扩展 csim/public-test/compile/cosim
```
