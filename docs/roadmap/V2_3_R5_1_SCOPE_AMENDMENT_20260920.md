# V2.3 R5.1 P6-P9 前瞻性范围修订

日期：2026-09-20
状态：项目所有者批准，P5 checkpoint 后生效
适用分支：`research-roadmap-v2.3`
决策 ID：`v2.3-r5.1-scope-amendment-20260920-v2`

## 1. 决策地位

本文是对 `V2_3_PRE_R6_ROBUSTNESS_AND_EVIDENCE_PLAN.md` 的版本化、前瞻性修订。
它不替换或改写原路线，不否定 P0-P5，也不重新解释任何既有证据。原路线文件、
P0-P5 结果、预算 ledger、日志、manifest 和审计哈希均保持不变；冲突处仅由本文对
P6 及以后工作产生优先效力。

P5 v6 已在修订前完成并冻结。权威 checkpoint 为
`R5_1_P5_ORDINARY_REFACTOR_CHECKPOINT.json`，执行结果为 13/13 案例完成、独立审计
通过、7 个 ordinary refactor failure、6 个 regression，实际消耗 Provider 120、
Vitis 34。该结果不能因本修订被回写、重标或排除。

## 2. 核心研究范围

V2.3 继续只有一条核心研究主线：

> owner-aware evidence-gated open-world diagnosis

其不可分割的组成保持为：

- deterministic authority separation；
- calibrated abstention；
- verified positive/negative repair memory；
- safe Candidate-only repair；
- time-ordered future evaluation。

Transformation Observation Ledger 是普通成功经验的辅助治理机制，用于证明这类经验
可以被安全记录而不污染 repair memory。它不是第二条主要研究主线，也不支撑当前
“普通成功记忆提高未来修复率”的 efficacy claim。

## 3. P8 当前强制最小闭环

### 3.1 O0 Captured

O0 必须以 append-only 记录保存：

- source identity 和 Candidate identity；
- typed AST diff；
- transformation family；
- affected symbols；
- 正式 validation 结果；
- public evidence refs。

O0 禁止保存 raw provider response、private reasoning、Hidden 或 future 信息；禁止进入
产品 prompt、Applicability Gate 或自动授权；禁止计入 `verified_positive`、
memory-assisted repair success 或 Trusted repair pattern 的证据。

### 3.2 O1 Observed

O1 只能由确定性规则对 transformation 分类，并验证身份、来源、正式 validation 和
审计状态。O1 必须显式标记为 `observation`，而不是 `repair_success`。默认不得注入
产品 prompt，不得产生 R4/R5 mutation authorization，不得计入 repair-memory 生命周期
统计或 efficacy 结果。

### 3.3 安全证明义务

P8 当前验收只要求证明：

1. ordinary success 不能伪装为 repair episode；
2. O0/O1 不能晋级为 Provisional 或 Trusted repair pattern；
3. observation ledger 与 repair ledger 的 ID namespace、schema、writer、snapshot、
   authorization、统计分母和报告字段完全分离；
4. O0/O1 不进入 prompt、Gate、R4/R5 mutation authorization；
5. future/Hidden leakage、retroactive relabel 和越权授权均为零；
6. 默认关闭路径及 `refactor/optimize/full` 三个正式入口保持不变。

## 4. 推迟到 post-R6 的扩展

以下设计保留，但从 R5 接受和 R6 准入硬门槛中移除：

- O2 Ablation-supported；
- O3 Replicated；
- ordinary-success transformation 的跨案例重放；
- observation transformation 的因果消融 runner；
- observation 到 Provisional/Trusted repair pattern 的晋级；
- M-O memory efficacy campaign；
- 依靠 observation memory 证明 future repair-rate improvement。

schema 可以保留版本预留，但当前实现不得产生 O2/O3、不得从 observation 派生 repair
authorization，也不得为这些扩展消耗本轮 Provider/Vitis 预算。只有项目所有者后续
单独批准，才能启动相应工作。

## 5. P9 强制主实验矩阵

当前必须完成的主实验臂为：

| 臂 | 定义 | 当前作用 |
|---|---|---|
| `S` | raw source baseline | 建立机会资格和 refactor lift 分母 |
| `B` | ordinary refactor, R2-R5 off | 公平产品基线 |
| `D` | R2-R4 on, no memory | 测诊断与单次受控修复 |
| `M-R` | Trusted repair memory | 测已验证 repair episode memory 的增益和风险 |

`M-O` 为 optional post-R6 extension，不进入当前主要 efficacy claim，不消耗当前预算，
除非项目所有者另行批准。

M-R 结论必须满足：history/future 严格时间隔离；verified positive 和 attributable
negative 同时进入生命周期统计；Applicability Gate、revision lifecycle、snapshot 和
authorization hash 全部生效；与公平 no-memory arm 配对；单独报告 negative transfer、
false repair、wrong-object mutation 和 false success。

O0/O1 仅支持“普通经验被安全记录且不会污染 repair memory”的治理结论。

## 6. 修订后的 R5 接受门槛

原路线中与数据治理、真实机会、通用实现、安全、产品兼容、预算和可重建性有关的门槛
继续生效。以下门槛被精确替换：

- 原“完整 O0-O3 双轨机制是硬门槛”替换为“O0/O1 安全隔离闭环是硬门槛”；
- observation 必须不可晋级、不可授权、不可进入 prompt/Gate，并与 repair ledger 和
  repair efficacy 统计分离；
- 持续记忆 efficacy 的必需证据只来自 M-R 的 verified repair episode memory；
- M-O、O2/O3 和 observation-to-repair 晋级不再是 R5 接受条件；
- 若真实数据不足以建立 M-R efficacy，不得用 observation 补足，也不得扩大结论；
  应继续改进通用机制，或由项目所有者明确收窄论文主张。

## 7. 修订后的 R6 准入门槛

进入 R6 前必须满足：

- P0-P5 checkpoint 和旧证据哈希完整保留；
- P6/P7 按通用合同完成真实机会与拒答根因审计；
- P8 O0/O1 安全隔离闭环通过确定性测试和独立审计；
- P9 的 S/B/D/M-R 时间有序配对实验完成或形成诚实的、经所有者批准的收窄结论；
- repair memory 的 positive/negative lifecycle、Gate、snapshot、authorization hash 生效；
- Hidden/future leakage、false success、wrong-object mutation 和越权授权为零；
- `refactor/optimize/full` 仍是仅有的正式入口，默认关闭行为不变；
- R5 只有项目所有者可以接受，实施代理不得自行设置 `R5_ACCEPTED=true` 或启动 R6。

O2/O3、M-O、observation replay/ablation 和 observation-to-repair 晋级不再阻塞 R6。

## 8. 工程边界

- 不创建第二套 CLI、Candidate repair、Vitis runner、验证或成功裁决流程；
- P6-P9 复用现有产品 orchestrator、R2/R4 integration、repair ledger、Gate、snapshot、
  authorization 和正式 validator；
- 不按 benchmark、路径或单个 HLS 日志编号增加修复特判；
- 不降低 provenance、Hidden/future 隔离、semantic validation 或 false-repair 标准；
- 不改变 `optimize safe-v1` 的当前优先级，P9 仍以 `refactor` 为主，并保留 `full`
  组合回归。

## 9. 立即执行顺序

1. 保留并引用 P5 checkpoint，不重跑 P0-P5；
2. 对本修订、machine-readable protocol、authority index、R5 gate 和 R6 gate 执行
   零 Provider、零 Vitis 的独立一致性审计；
3. 核对原路线和 P0-P5 证据哈希未变化；
4. 进入 P6：从 P5 的 7 个 failure 和 6 个 regression 中按预登记、通用规则建立
   opportunity/root-cause cohort；
5. 之后依次推进 P7、最小 P8、P9 和 P10。

截至本修订生效前，累计实际消耗为 Provider `512/650`、Vitis `232/650`，余额为
Provider `138`、Vitis `418`。范围修订和一致性审计自身必须为 `0/0`。
