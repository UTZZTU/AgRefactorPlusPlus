# Stage 1 csynth Hard Budget Acceptance

## 1. 验收结论

Stage 1 的 **csynth 工具硬预算子项目**已经在 Vitis 2023.2 上完成确定性测试、统一入口集成测试和真实工具 smoke。

允许声明：

> `UnifiedRunner` 创建的同一个 `BudgetManager` 已经能够沿 legacy 主流程到达 `run_csynth()`，并在真实 Vitis 版本探测和综合启动前执行硬预算检查。一次真实 csynth 尝试只计数一次；预算耗尽后的下一次调用在版本探测前阻断。

不能据此声明：

- Stage 1 已整体关闭；
- compile/public-test/csim/cosim 已具备同等硬预算；
- 预算耗尽时已在 Stage 3 返回 `best_correct`；
- remote HLS 已支持共享本地 `BudgetManager`；
- 任意 Vitis 版本、器件或 kernel 均已验证。

## 2. 固定代码基线

```text
fc8a646 feat: enforce hard csynth call budgets
9be882a feat: propagate csynth budgets through legacy flow
eb1575a test: verify unified csynth budget enforcement
```

验收基线：

```text
Branch: stage1-csynth-hard-budget
Commit: eb1575a84fd41c1f2269da6977e25d6cdb084f74
Deterministic tests: 169/169 passed
```

## 3. 已实现契约

### 3.1 预算字段

保留聚合工具预算：

```text
BudgetLimits.max_tool_calls
BudgetUsage.tool_calls
```

新增 csynth 专项预算：

```text
BudgetLimits.max_csynth_calls
BudgetUsage.csynth_calls
```

一次真实 csynth 尝试同时消耗：

```text
tool_calls += 1
csynth_calls += 1
```

### 3.2 调用顺序

```text
生成 source / Tcl / invocation evidence
→ ensure_available(tool_calls=1, csynth_calls=1)
→ Vitis version probe
→ version compatibility gate
→ consume(tool_calls=1, csynth_calls=1)
→ real vitis-run launch
→ execution evidence
```

关键语义：

- `limit=0`：在 `vitis-run --version` 之前阻断；
- version mismatch/probe failure：没有发生真实 csynth，不消耗 csynth 次数；
- 通过 version gate 后，在真实 launcher 前预记一次；
- success、nonzero failure、timeout、launcher exception 均计一次；
- 第二次超预算调用不执行 version probe，也不启动 Vitis；
- budget block 以结构化 `BudgetExceededError` 和 invocation evidence 暴露，不能伪装成普通综合失败。

### 3.3 主流程贯通

```text
UnifiedRunner
→ RunContext.budget
→ LegacyRefactorAdapter
→ hls_refactor_with_rag
→ csynth_and_csim
→ run_csynth
```

普通 csynth 路径和 HeteroRF csynth 路径均传递同一个预算实例。

有硬工具预算时，legacy remote HLS 路径会被显式拒绝，因为该路径当前不能共享本地 `BudgetManager`。

## 4. 确定性测试证据

### 4.1 底层预算行为

覆盖：

- zero csynth limit blocks probe and launch；
- zero total tool limit blocks probe；
- limit one allows first and blocks second；
- version mismatch consumes zero；
- launch exception consumes exactly once；
- timeout consumes exactly once。

### 4.2 Legacy 贯通

覆盖：

- Adapter 传递 `RunContext.budget` 的对象身份；
- `flow.new` 接受并验证 `BudgetManager`；
- local normal/HeteroRF 路径传递同一个实例；
- bounded remote tool budget 提前拒绝。

### 4.3 UnifiedRunner 集成

覆盖完整链路：

```text
UnifiedRunner
→ LegacyRefactorAdapter
→ injected legacy backend
→ run_csynth
```

结果：

- `max_csynth_calls=0`：probe 与 launch 调用数均为 0；
- `max_tool_calls=1, max_csynth_calls=1`：第一次 probe/launch 各一次，第二次均为 0；
- final usage 精确为 `tool_calls=1, csynth_calls=1`；
- 完整确定性测试：`169/169 passed`。

## 5. 真实 Vitis 2023.2 smoke

运行目录：

```text
/data/agrefactor_runs/stage1_real_vitis_csynth_budget_smoke_20260715_184955
```

环境：

```text
Launcher: /data/Xilinx/Vitis/2023.2/bin/vitis-run
Version: 2023.2
SW Build: 4026344
Device: xcu200-fsgd2104-2-e
Clock: 5.0 ns
Kernel: budget_smoke
```

真实结果：

```text
FIRST_REAL_CSYNTH_SUCCEEDED=1
SECOND_CSYNTH_BLOCKED_BEFORE_PROBE=1
FINAL_TOOL_CALLS=1
FINAL_CSYNTH_CALLS=1
REAL_VITIS_CSYNTH_BUDGET_SMOKE_READY=1
```

第一次调用：

- requested/actual version：`2023.2` matched；
- `vitis-run` return code：`0`；
- invocation execution：`completed`；
- budget status：`consumed`；
- csynth report 已生成；
- Estimated Fmax：`273.97 MHz`。

第二次调用：

- budget resource：`tool_calls`；
- attempted：`2`；
- limit：`1`；
- checkpoint：`before_version_probe`；
- execution：`blocked_by_budget`；
- toolchain verification：`pending`，证明没有执行第二次 version probe。

关键证据：

```text
smoke_summary.json
smoke.log
trace.jsonl
first_real_csynth/csynth_invocation.json
first_real_csynth/csynth/solution/syn/report/budget_smoke_csynth.rpt
second_blocked_csynth/csynth_invocation.json
```

## 6. 当前剩余边界

Stage 1 仍需按同一契约扩展：

```text
compile
public_test
csim
cosim
```

还需在后续 Stage 3 验证：

```text
预算耗尽
→ 安全停止生成新候选
→ 保留并返回 best_correct
```

TargetProfile 的 named profiles、per-profile executable/settings、platform/resources/parser/provenance 和多版本验证也仍未完成。

## 7. 下一任务

先只读审计剩余工具调用图，再一次只选择一个工具形成完整闭环：

```text
call-site audit
→ budget key/limit/usage
→ pre-call hard check
→ exact-once accounting
→ structured evidence
→ deterministic tests
→ real tool smoke
```

不要同时修改 compile、public-test、csim 和 cosim。
