# Stage 3.3 Deterministic Optimizer State Machine Decision Record

> **状态：** FROZEN FOR S3.3
> **上位合同：** `STAGE3_IMPLEMENTATION_CONTRACT.md`
> **适用范围：** 仅 S3.3 deterministic state machine；不扩展到 S3.4+ 的真实模型、源码改写或工具执行。

## 1. 不改变既有 schema

S3.3 不修改 `state.py` 的 `SCHEMA_VERSION=1`，不新增无当前 consumer 的持久化字段。下一动作继续由已有 `current_level`、`current_round`、candidate index 和 terminal status 表达。候选级 round/selection/final decision 写入现有 `CandidateRecord.decision`；完整 hypothesis 使用独立不可变 JSON artifact；非权威决策流水写入 `decisions.jsonl`。

## 2. deterministic hypothesis selection

选择规则固定为：

```text
provider 返回顺序
→ 只考虑本轮前 3 项
→ 严格 typed validation
→ level/parent/ID 去重校验
→ 选择第一个 valid hypothesis
→ 每轮最多执行 1 个
```

不按源码文本、pragma 字符串、风险评分或不完整静态模式猜测优化类别。静态启发式不作为 authoritative blocking gate。

## 3. 空或 malformed hypothesis

- `proposed`、`valid`、`invalid`、`selected`、`executed` 分开计数；
- malformed/unsafe hypothesis 在 executor 前拒绝，不创建 candidate；
- 本轮没有 valid hypothesis 表示当前 level 没有可执行分支，立即进入下一级，而不是重复消耗该 level 的剩余轮次；
- Structural/Bottleneck 明确无合法 hypothesis 后允许进入后级，符合冻结顺序合同。

## 4. best pointers 与 current pointer

### 4.1 accepted + objective feasible + PPA better

```text
best_correct = candidate
best_ppa = candidate
current = candidate
```

### 4.2 accepted + objective feasible + PPA not better

候选保留为历史 accepted record，但不覆盖任何 best pointer：

```text
best_correct = incumbent selected recovery
best_ppa = incumbent
current = best_correct
```

### 4.3 accepted + objective infeasible

- 当前 run 尚无 `best_ppa` 时，候选可更新 `best_correct` 并成为后续安全 parent；
- 已有 objective-feasible `best_ppa` 时，不让 infeasible candidate 覆盖 selected recovery path；
- 全部搜索完成仍无 `best_ppa` 时，terminal=`no_feasible_candidate`，同时返回 `best_correct`。

### 4.4 objective feasibility unknown / PPA incomparable

unknown 保持 unknown，不猜测为 feasible 或 candidate failure：

```text
terminal = review_required
current = best_correct
```

### 4.5 rejected / blocked / review / error

- rejected：只拒绝当前 candidate，回滚到 `best_correct`，可进入下一轮；
- blocked：停止 run，terminal=`blocked`；
- review_required：停止 run，terminal=`review_required`；
- error：停止 run，terminal=`error`；
- terminal candidate 不可复活。

## 5. BudgetManager 语义

- provider/executor 通过 typed `BudgetIncrement` 声明下一次物理调用增量；
- 状态机先调用共享 `BudgetManager.ensure_available()`，通过后才启动 injected invocation；
- invocation 一旦启动，即使 provider/executor 边界抛异常，也消费其声明的物理预算增量；
- FakeProvider/FakeExecutor 默认增量全为 0，因此不冒充真实 LLM、tool、CSIM 或 CSYNTH；
- 测试可显式配置非零增量验证硬预算预检，但必须标记为 deterministic simulation；
- 预算不足时不启动下一 invocation，checkpoint 后返回 `best_correct`，terminal=`budget_exhausted_with_best_correct`。

## 6. Checkpoint 时序与 resume

- 非 terminal 输入必须已有 qualified baseline 与 `best_correct`，否则在构造阶段拒绝且不启动 provider/executor；
- 初始 qualified baseline 写 checkpoint；
- 每个 terminal candidate 的 candidate index、best/current pointer 和**下一动作**在同一原子 checkpoint 中提交；
- 无有效 hypothesis 的 level transition 写 checkpoint；
- budget/blocked/review/error/final terminal 写 checkpoint；
- checkpoint marker 仍由 S3.1 writer 最后写入并保持 authoritative；
- `step()` 可执行一个确定性 round，后续新 engine 从 latest checkpoint 恢复，不重复已 checkpoint 的 candidate。

这样避免为了形式上的两个 checkpoint 留下“candidate 已执行但下一动作尚未提交”的重复调用窗口。

## 7. Artifacts 当前 consumer

S3.3 只新增：

```text
hypotheses/hyp-*.json   # typed immutable lineage artifact
decisions.jsonl         # deterministic safe decision audit stream
```

checkpoint/state/candidate/best projections 继续复用 S3.1；PPA/qualification records 继续复用 S3.2。`decisions.jsonl` 是审计投影，不取代 immutable checkpoint authority。

## 8. Terminal completion

搜索自然完成或达到 7 executed candidates 后：

- `best_ppa` 不存在且没有 unknown feasibility → `no_feasible_candidate`；
- 存在 unknown feasibility 且没有可安全裁决的 `best_ppa` → `review_required`；
- `best_ppa == baseline` → `accepted_no_improvement`；
- `best_ppa != baseline` → `accepted_improved`。

## 9. 明确非目标

S3.3 不实现真实 model provider、optimization prompt、真实源码 rewrite、真实 Vitis/CSIM/CSYNTH、Bottleneck classifier、pragma semantic detector、CLI `optimize/full`、Memory Gate、migration 或 benchmark。产品门禁继续保留到 S3.7。
