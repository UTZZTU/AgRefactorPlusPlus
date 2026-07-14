# Stage 3 — Safe Three-Level Optimizer

## 1. 目标

将公开的线性 `opt.simple_iter` baseline 替换为一个正确性优先、证据驱动、可追溯、可回滚、受预算约束的优化器。

## 2. 进入条件

Stage 3 开始前必须满足：

- Stage 1 TargetProfile 能控制真实 Vitis；
- compile/test/csim/csynth 工具预算可计数；
- Stage 2 能给出结构化 correctness 与 synthesis evidence；
- 多类型 kernel smoke 已完成；
- `best_correct` 的定义和 artifact schema 已确定。

## 3. 三级优化

### 3.1 Structural Optimization

允许处理算法结构、循环组织、数据布局、内存访问顺序、局部缓存、函数边界、producer/consumer 结构和数据流结构。每个分支必须声明因果假设和修改范围。

### 3.2 Bottleneck Repair

依据 csynth/report evidence 处理 initiation interval、loop dependency、memory port contention、critical path、resource bottleneck、unknown loop bound、dataflow stall 和 scheduling limitation。

### 3.3 Pragma Tuning

只有前两级稳定后才处理 PIPELINE、UNROLL、ARRAY_PARTITION、DATAFLOW、INLINE、BIND/RESOURCE 和参数微调。

## 4. 假设驱动搜索

每轮建议一次生成 3–5 个假设，但只执行最有希望的 1–2 个分支。每个假设包含 hypothesis、evidence、expected benefit、risk、modification scope 和 verification plan。

## 5. 候选状态

至少维护 baseline、current_candidate、best_correct、best_ppa 和 rejected_candidates。

候选 artifact 至少包含 candidate id、parent、optimization level、hypothesis、source/patch、effective TargetProfile、correctness evidence、PPA、budget、decision 和 rollback reason。

## 6. Cheap-to-expensive Gate

```text
static checks
→ compile
→ public test
→ csim
→ csynth
→ optional cosim
```

任何廉价 gate 失败时，不得继续消耗 csynth 预算。

## 7. 接受与回滚

- 功能失败：拒绝并回滚；
- csynth 失败：拒绝或进入 evidence-guided repair；
- PPA 退化：保留结果但不更新 best；
- 资源违规：不更新 best_correct；
- 相同 code + profile：复用缓存；
- 预算不足：停止生成候选并返回 best_correct。

## 8. UnifiedRunner 接入

完成 `mode=optimize` 和 `mode=full`。`full` 必须严格执行 refactor correctness → optimization，refactor 失败时不得进入 optimize。

## 9. 评测要求

至少比较 current `simple_iter` 与 safe optimizer，在相同 kernel、model、TargetProfile、预算和重复次数下比较 correctness、synthesis success、latency/II/resources、invalid synthesis ratio、rollback、token/tool calls 和 wall time。

## 10. 完成标准

- 三级顺序真实执行；
- `best_correct` 不可破坏；
- checkpoint/rollback/cache；
- hard budget；
- candidate lineage；
- 多 kernel 真实对照；
- `mode=optimize/full` 可运行；
- 文档与复现脚本。
