# Stage 3 — Safe Three-Level Optimizer

## 状态

```text
PLANNING_FROZEN=true
IMPLEMENTATION_STARTED=true
S3_1_CANDIDATE_STATE_FOUNDATION=accepted
CURRENT_PACKAGE=S3.2_QUALIFICATION_AND_PPA_EVIDENCE
AUTHORITATIVE_CONTRACT=STAGE3_IMPLEMENTATION_CONTRACT.md
```

本文保留高层研究设计。直接编码、参数、schema、状态机和验收边界以 [Stage 3 Frozen Implementation Contract](STAGE3_IMPLEMENTATION_CONTRACT.md) 为准。

## 目标

替换 Legacy `opt.simple_iter` 的产品角色，建立一个：

- correctness first；
- evidence driven；
- hypothesis driven；
- bounded；
- traceable；
- checkpointed；
- rollback-safe；
- cache-aware；
- 始终保护 `best_correct` 的三级优化器。

## 三级顺序

```text
Level 1 Structural Optimization
→ Level 2 Bottleneck Repair
→ Level 3 Pragma Tuning
```

### Structural

处理算法结构、循环组织、函数边界、数据布局、内存访问顺序、局部缓存、producer/consumer 和数据流结构。

### Bottleneck

依据真实 CSYNTH/report evidence 处理 II、loop dependency、memory port contention、critical path、resource bottleneck、unknown loop bound 和 dataflow stall 风险。

### Pragma

最后才调整 PIPELINE、UNROLL、ARRAY_PARTITION、DATAFLOW、INLINE、BIND/RESOURCE 等指令。

## 不允许退化为

```text
把报告发给模型
→ 模型整份改写
→ 继续综合
```

每个 executed candidate 必须对应一个记录完整的因果假设、修改范围和验证计划。

## 核心不变量

- baseline 先通过资格门禁；
- 功能失败候选不能进入 PPA 比较；
- CSYNTH 失败候选不能成为 `best_correct`；
- PPA 退化候选不能覆盖当前 best；
- 每个候选可追溯到 parent 和 hypothesis；
- 预算耗尽停止生成新候选；
- 始终持久化并返回当前 `best_correct`；
- cache 只能在完整 execution identity 相同的情况下命中；
- Hidden 内容不得进入优化模型 Prompt。

## 实施顺序

```text
S3.1 Candidate state/checkpoint/best_correct — accepted
S3.2 Qualification and PPA evidence adapters — next
S3.3 Deterministic hypothesis and policy state machine
S3.4 Model-backed Structural level
S3.5 Bottleneck level
S3.6 Pragma level
S3.7 optimize/full product adapters
S3.8 Multi-kernel real acceptance and simple_iter comparison
```

不得把 S3.1–S3.8 合并成一次大提交。
