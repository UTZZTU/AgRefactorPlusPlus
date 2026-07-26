# Stage 1 Compile and C Simulation Hard Budget Acceptance

## 1. 验收结论

Stage 1 的 **local compile 与 C simulation 工具硬预算子项目**已经完成：

- 底层预算契约；
- legacy 主流程预算贯通；
- `UnifiedRunner` 完整工具链联合测试；
- 本地真实 `g++` 编译与生成程序执行 smoke；
- 结构化 invocation evidence；
- 预算耗尽后的启动前阻断。

允许声明：

> `UnifiedRunner` 创建的同一个 `BudgetManager` 已能够沿 legacy 主流程控制 Testbench Preflight 编译、本地 C 综合和 C simulation。Preflight 与 C simulation 的编译共享 `compile_calls`；C simulation 的真实编译和真实可执行程序启动分别精确计数。预算不足时，后续工具在真实 subprocess 启动前阻断。

不能据此声明：

- Stage 1 已整体关闭；
- public-test 已有独立、规范化的专项预算；
- cosim 已实现并具备硬预算；
- remote HLS 已能共享本地 `BudgetManager`；
- 已完成一次真实的 Preflight → Vitis csynth → csim 全链路工具运行；
- Stage 3 已能在预算耗尽时返回 `best_correct`；
- 任意工具版本、器件、kernel 或 testbench 均已验证。

## 2. 固定代码基线

关键提交：

```text
6685789 feat: add compile and csim budget schema
2c03419 feat: enforce hard local csim budgets
64b462f feat: enforce hard preflight compile budgets
18b7b18 test: verify unified full tool budget enforcement
```

验收基线：

```text
Branch: stage1-csim-compile-hard-budget
Commit: 18b7b188a26c90b804cd61a43ba924f75f2cc7f1
Deterministic tests: 204/204 passed
```

## 3. 已实现预算契约

### 3.1 预算字段

聚合工具预算：

```text
BudgetLimits.max_tool_calls
BudgetUsage.tool_calls
```

编译专项预算：

```text
BudgetLimits.max_compile_calls
BudgetUsage.compile_calls
```

C simulation 专项预算：

```text
BudgetLimits.max_csim_calls
BudgetUsage.csim_calls
```

C 综合专项预算继续保留：

```text
BudgetLimits.max_csynth_calls
BudgetUsage.csynth_calls
```

默认语义：

```text
字段省略       → unlimited
显式 null      → unlimited
显式 0         → disabled
正整数         → hard upper bound
```

### 3.2 Testbench Preflight 编译

一次真实 Preflight 编译尝试消耗：

```text
tool_calls += 1
compile_calls += 1
```

调用顺序：

```text
生成 source 与 invocation evidence
→ 静态禁止依赖检查
→ ensure_available(tool_calls=1, compile_calls=1)
→ consume(tool_calls=1, compile_calls=1)
→ real g++ launch
→ execution evidence
```

关键语义：

- 静态检查失败不启动编译器，也不消耗预算；
- `limit=0` 在 `g++` 启动前阻断；
- success、编译失败、timeout、missing compiler、launcher exception 均视为一次真实编译尝试；
- Testbench repair 的初始 Preflight 与每次修复后 Preflight 共享同一个预算实例；
- budget block 以 `BudgetExceededError` 和 `testbench_preflight_invocation.json` 暴露；
- `run_testbench_preflight()` 自己创建 evidence 目录，不依赖下层函数副作用。

### 3.3 本地 C simulation

一次完整本地 C simulation 计划包含两个真实进程：

```text
g++ compile
→ ./csim execute
```

分别消耗：

```text
g++ compile:
  tool_calls += 1
  compile_calls += 1

./csim execute:
  tool_calls += 1
  csim_calls += 1
```

完整计划的 prospective increment：

```text
tool_calls += 2
compile_calls += 1
csim_calls += 1
```

调用顺序：

```text
生成 source 与 csim_invocation.json
→ ensure_available(full plan)
→ consume(compile increment)
→ real g++ launch
→ compile evidence
→ consume(csim increment)
→ real ./csim launch
→ simulation evidence
```

关键语义：

- 完整计划预算不足时，在 `g++` 启动前整体阻断；
- 编译成功后 simulation 预算意外不足时，在 `./csim` 启动前阻断；
- 编译失败只消耗 compile，不消耗 csim；
- 编译 timeout/launcher exception 只消耗 compile；
- simulation failure/timeout/launcher exception 同时保留 compile 与 csim 的已消费次数；
- 每个真实进程只计数一次。

### 3.4 主流程贯通

```text
UnifiedRunner
→ RunContext.budget
→ LegacyRefactorAdapter
→ hls_refactor_with_rag
→ csynth_and_csim
├─ run_testbench_validation_gate
│  └─ TestbenchPreflight.compile_and_link
├─ run_csynth
└─ run_csim
```

所有本地路径接收同一个 `BudgetManager` 对象。

有 compile、csim、csynth 或 aggregate tool 硬预算时，不能共享本地 manager 的 legacy remote 路径会被显式拒绝。

## 4. 确定性测试证据

### 4.1 Schema 与原子性

覆盖：

- compile limit 独立于 csim limit；
- csim limit 独立于 compile limit；
- aggregate tool limit 可以阻断完整 csim 计划；
- 多资源 consume 失败时不发生部分更新；
- 省略限制保持 unlimited；
- exact limits 在精确达到时报告 exhausted。

### 4.2 Preflight 编译

覆盖：

- zero compile limit blocks before launch；
- zero total tool limit blocks before launch；
- static check consumes zero；
- success consumes once；
- compile failure consumes once；
- timeout consumes once；
- missing compiler consumes once。

### 4.3 本地 C simulation

覆盖：

- zero csim limit blocks before compile；
- total tool limit blocks full plan；
- compile failure consumes only compile；
- compile timeout consumes only compile；
- compile launcher exception consumes only compile；
- simulation failure consumes both；
- simulation launcher exception consumes both；
- success consumes compile and csim once；
- evidence 包含全部资源字段。

### 4.4 Legacy 预算贯通

覆盖：

- Adapter 传递 `RunContext.budget` 的对象身份；
- Preflight direct path 收到同一预算实例；
- repair loop 的全部 Preflight 复用同一实例；
- normal 与 HeteroRF csim 路径收到同一实例；
- bounded remote compile/csim budget 提前拒绝；
- `csynth_and_csim` 将预算传给 validation gate、csynth 与 csim。

### 4.5 完整工具链联合测试

测试链路：

```text
UnifiedRunner
→ LegacyRefactorAdapter
→ Testbench Preflight
→ run_csynth
→ run_csim
```

该测试不 mock `run_csynth()` 或 `run_csim()` 的 Python 预算实现，只模拟外部工具进程和综合报告。

覆盖 6 个场景：

1. `max_compile_calls=0`
   Preflight 前阻断全部后续工具。

2. `max_csynth_calls=0`
   Preflight 消耗一次 compile，csynth 在 version probe 前阻断。

3. `max_tool_calls=1`
   Preflight 用完 aggregate 额度，csynth 在 probe 前阻断。

4. `max_compile_calls=1`
   Preflight 与 csynth 完成，csim 在其编译前因共享 compile 额度耗尽而阻断。

5. `max_tool_calls=3`
   Preflight 与 csynth 完成，csim 的完整两进程计划因 aggregate 额度不足而阻断。

6. 精确完整额度：

```text
max_tool_calls=4
max_compile_calls=2
max_csynth_calls=1
max_csim_calls=1
```

全链路成功，最终使用量：

```text
tool_calls=4
compile_calls=2
csynth_calls=1
csim_calls=1
```

完整确定性测试：

```text
204/204 passed
```

## 5. 真实本地 C simulation smoke

运行目录：

```text
/data/agrefactor_runs/stage1_real_local_csim_budget_smoke_20260715_215055
```

环境：

```text
Python: /home/user/anaconda3/envs/agrefactor/bin/python
g++: /usr/bin/g++
g++ version: Ubuntu 11.4.0
Vitis HLS settings: /data/Xilinx/Vitis_HLS/2023.2/settings64.sh
Host executable format: ELF 64-bit x86-64
```

预算：

```text
max_tool_calls=2
max_compile_calls=1
max_csim_calls=1
```

第一次调用真实执行：

```text
run_csim
→ /usr/bin/g++
→ generated ./csim
→ execute ./csim
```

真实结果：

```text
FIRST_REAL_CSIM_SUCCEEDED=1
FIRST_BINARY_EXECUTABLE=1
FINAL_TOOL_CALLS=2
FINAL_COMPILE_CALLS=1
FINAL_CSIM_CALLS=1
```

第二次调用共享同一个已耗尽的 `BudgetManager`：

```text
SECOND_CSIM_BLOCKED_BEFORE_COMPILE=1
SECOND_BINARY_ABSENT=1
PHASE_RESOURCE=tool_calls
```

最终 marker：

```text
REAL_LOCAL_CSIM_BUDGET_SMOKE_READY=1
WORKTREE_CLEAN=1
SMOKE_STATUS=0
```

`RESULT_STATUS=error` 是预期结果，因为该 smoke 故意发起第二次超预算调用，用于验证 `UnifiedRunner` 将 `BudgetExceededError` 规范化为 error phase；它不表示第一次真实 csim 失败。

关键证据：

```text
smoke_summary.json
smoke_summary.txt
trace.jsonl
first_real_csim/csim_invocation.json
first_real_csim/csim
second_blocked_csim/csim_invocation.json
first_real_csim_binary_file.txt
```

## 6. 当前剩余边界

Stage 1 工具预算仍需处理：

```text
public_test
cosim
```

其中：

- 当前没有规范化、独立的 public-test tool action；部分 public test 行为通过 testbench/preflight/csim 间接执行；
- 当前 local core 没有活跃的 cosim 主流程；
- 在添加预算字段前，需要先确定真实 call site 与语义，不能为了补齐名称而制造空接口。

还需完成一次真实全链路验收：

```text
real Preflight g++
→ real Vitis csynth
→ real csim g++
→ real ./csim
→ one shared BudgetManager
```

还需在后续 Stage 3 验证：

```text
预算耗尽
→ 安全停止生成新候选
→ 保留并返回 best_correct
```

TargetProfile 的 named profiles、per-profile executable/settings、platform/resources/parser/provenance 和多版本验证也仍未完成。

## 7. 下一任务

先对 public-test 与 cosim 做一次只读能力审计：

```text
identify canonical action
→ identify real external launch
→ decide whether a dedicated budget key is meaningful
→ define pre-call hard check
→ define exact-once accounting
→ deterministic tests
→ real tool smoke when an active local path exists
```

在不存在真实工具动作时，不创建仅为满足清单的伪预算字段。

完成剩余工具审计后，再执行 Stage 1 的真实完整工具链总验收。
