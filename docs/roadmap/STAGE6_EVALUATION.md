# Stage 6 — Evaluation, Ablation, and Final Delivery

## 1. 目标

系统性证明 Stage 0–5 的方法在正确性、PPA、预算、稳定性和版本迁移上有效。Stage 6 不应再临时加入主要功能；新发现的问题回到对应 Stage 修复。

## 2. 任务类别

| 类别 | 内容 |
|---|---|
| A | 动态内存、递归、容器、复杂指针等不可综合 C/C++ |
| B | 已有 HLS compile/csynth 失败 |
| C | public test/csim 功能错误或接口不一致 |
| D | 功能正确但 latency/II/resource 差 |
| E | Memory 误检索与负迁移 |
| F | source 可运行、target 失败或 PPA 漂移 |

## 3. 实验控制

固定 kernel 集、source/target profiles、model、Prompt profile、Memory mode、random seed、retry、parallelism、budgets、scoring 和 repeats。

## 4. Baselines

current AgRefactor++ baseline、Memory off/always/gated、unified/layered Prompt、`simple_iter`、safe optimizer、fixed Flash、fixed Pro 和 full system。

## 5. Ablation

```text
A0 baseline
A1 + TargetProfile + Trace
A2 + Structured Feedback + State Machine
A3 + Layered Prompt
A4 + Safe Optimizer
A5 + Memory Gate
A6 + Hard Budget
A7 Full System
```

## 6. 指标

### Correctness

compile、public test、hidden test、csim、optional cosim、false success 和 failure attribution accuracy。

### Synthesis/PPA

csynth success、timing、latency、II、LUT/FF/BRAM/DSP 和 resource legality。

### Budget

LLM calls、input/output tokens、cost、compile/test/csim/csynth/cosim 和 wall time。

### Search

best round、rollback、rejected candidates、invalid synth ratio、cache hit 和 acceptance rate。

### Memory

acceptance、rejection、abstention、negative transfer 和 post-retrieval success。

### Stability

mean、standard deviation、worst case、success rate 和 repeated-run distribution。

## 7. 报告要求

每个任务保存 task/profile、input、baseline、candidate tree、best_correct、tool evidence、PPA、budget、memory decisions、final decision 和 failure reason。

## 8. 最终交付

- 可运行 CLI；
- profile/model/prompt/memory 配置；
- safe optimizer；
- parser/state machine；
- benchmark；
- migration samples；
- experiments；
- tables/plots；
- reproduction scripts；
- README/USAGE/config/evaluation docs；
- competition report；
- paper material；
- release/tag。

## 9. 完成标准

- 固定 benchmark；
- 重复实验；
- baseline；
- ablation；
- 统计结果；
- 可复现脚本；
- 诚实限制；
- release artifact。
