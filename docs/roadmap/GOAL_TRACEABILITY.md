# AgRefactor++ Goal Traceability

本文只保留当前目标追踪，不复制每个历史 package 的逐提交状态。历史证据由 acceptance、audit 和 history 文件承担。

## 当前总体状态

```text
Stage 0=baseline retained
Stage 1=closed
Stage 2=closed
Pre-Stage-3=closed
Stage 3 planning=frozen
Stage 3 implementation=in progress; S3.1-S3.4 accepted
Stage 4=not started
Stage 5=not started
Stage 6=not started
```

## 核心目标追踪

| 核心目标 | 当前实现 | 仍缺内容 | 目标阶段 | 下一证据 |
|---|---|---|---|---|
| TargetProfile | Vitis 2023.2 committed profile、executable/settings、part、clock、compile flags、Tcl、parser identity、resource schema、per-field provenance | 多版本、更多设备/platform、版本特定 parser | Stage 5 扩展 | 至少一组真实 source/target 版本矩阵 |
| 双模式版本处理 | `refactor/optimize/full` 数据结构预留；普通 source-only refactor 已实现 | migrate mode、SourceProfile、source baseline、migration report | Stage 5 | 一组真实旧版→目标版迁移 |
| Model API Registry | Modern/Legacy/repair 统一 typed runtime；DeepSeek 真实 smoke；固定模型路径 | authorized auto pool、具体部署 reasoning mapping | 后续 hardening | 用户授权模型池与选择证据 |
| 分层 Prompt | Shared builder、Target/model/evidence/scope/output layers；Candidate/Testbench consumers；S3.4 Structural hypothesis/rewrite Prompt 与 identity | Bottleneck/Pragma Prompt consumers | Stage 3 | S3.5 Bottleneck prompt/evidence identity |
| 结构化反馈与状态机 | Preflight、CSYNTH、Public/Hidden、repair、owner、next action、Hidden suppression；S3.2 qualification/PPA；S3.3 deterministic engine；S3.4 model-backed Structural consumer | S3.5–S3.6 Bottleneck/Pragma consumers | Stage 3 | Bottleneck report-evidence model integration |
| Multi-type ground truth | 7 baselines、7/7 full chains、9/9 fault matrix、16/16 labels | 更广 kernel/版本/设备统计 | Stage 6 | 固定 benchmark 扩展 |
| 安全三级优化器 | S3.1 state/checkpoint；S3.2 qualification/PPA/cache；S3.3 deterministic engine；S3.4 real model-backed Structural hypothesis/complete-source integration；Legacy `opt.simple_iter` 仍仅为 baseline | S3.5-S3.6 Bottleneck/Pragma、S3.7 optimize/full、S3.8 evaluation | Stage 3 | S3.5 bounded Bottleneck model smoke |
| Memory Applicability Gate | Legacy RAG 正负 trial 可作为 baseline | schema、score、abstention、off/gated/always | Stage 4 | 负迁移和弃权实验 |
| BudgetManager | LLM/Tool/Compile/CSIM/CSYNTH/wall-time 硬控制；Token/Cost observed-only；S3.3 fallback；S3.4 real LLM prospective/physical accounting | 未来 cosim 与 S3.5+ mixed model/tool usage | Stage 3/后续 | bounded Bottleneck model/tool evidence |
| 版本迁移 | 长期目标保留 | 真实 source→target 修复、验证、优化和报告 | Stage 5 | migration acceptance |
| 论文评测 | 真实工具证据和审计基础已具备 | safe optimizer 与 simple_iter、公平预算、多 kernel 重复实验、消融 | Stage 6 | 固定评测协议 |

## 防止概念偷换

```text
TargetProfile 一次真实运行成功
≠ 任意版本支持

TaskSpec 有 version 字段
≠ 真实版本迁移

Legacy RAG 可检索
≠ Memory Applicability Gate

simple_iter 可循环
≠ 安全三级优化器

一次真实模型 smoke
≠ 稳定修复成功率

1500+ deterministic tests
≠ 同数量真实 kernel

一次 PPA 改善
≠ 稳定优化收益
```

## Stage 3 当前追踪

### 已冻结

- baseline qualification；
- CandidateRecord 和 HypothesisRecord；
- `best_correct`、feasibility 和 PPA 比较边界；
- candidate lineage；
- checkpoint/rollback；
- validation cache identity；
-三级状态机；
-物理硬预算与策略上限的关系；
- optimize/full 语义；
- artifact schema；
-分包顺序和验收矩阵。

详细定义见 [STAGE3_IMPLEMENTATION_CONTRACT.md](STAGE3_IMPLEMENTATION_CONTRACT.md)。

### 已完成 S3.1

```text
typed HypothesisRecord
typed CandidateRecord
OptimizerState
atomic checkpoint/recovery
baseline initial best_correct
50/50 focused tests
1558/1558 full deterministic regression
```

S3.1 没有调用模型或 Vitis，也没有实现 qualification、PPA comparator、cache、三级策略或 optimize/full。

### 已完成 S3.2

```text
independent Stage 3 qualification order
source → Preflight → Public → CSYNTH → Hidden → PPA → feasibility
typed Vitis HLS PPA evidence
frozen latency comparator
exact validation cache identity and immutable safe cache
85/85 S3.2 focused tests
135/135 optimizer regression
1643/1643 full deterministic regression
one real Vitis 2023.2 baseline replay
cache-hit replay with zero real-tool counter delta
```

S3.2 没有调用模型，没有实现多轮搜索、三级策略或正式 `optimize/full`。

### 已完成 S3.3

```text
typed frozen safe-v1 policy
structural 2 → bottleneck 2 → pragma 3
3 proposed / 1 selected / 1 executed per round
max 7 executed candidates
FakeProvider/FakeExecutor
prospective shared BudgetManager preflight
PPA decision + best pointer protection + rollback
checkpointed next action + resume deduplication
46/46 focused
181/181 optimizer regression
1689/1689 full deterministic regression
no real network model or Vitis
```

S3.3 没有实现真实 optimization prompt/source rewrite，也没有解除产品 `optimize/full` 门禁。S3.4 已补齐 Structural model integration，仍不等于 correctness/PPA 或产品接线。


### 已完成 S3.4

```text
agent-safe Structural hypothesis/rewrite layered prompts
strict versioned hypothesis JSON; max 3 provider-ordered proposals
adapter-owned deterministic hypothesis IDs
complete replacement C++ source contract
existing CandidateResponseContract top-interface protection
provider-neutral real model integration
safe model-call audit without raw prompts/responses
shared LLM budget and observed token/cost accounting
explicit generated-source → qualification boundary
52/52 focused
233/233 optimizer regression
1741/1741 full deterministic regression
bounded real Structural smoke: exactly 2 model calls
Vitis/compile/CSIM/CSYNTH calls=0
```

S3.4 does not claim that a contract-valid model source is correct,
synthesizable, feasible, or faster. It does not use incomplete static source
matching as an authoritative Structural gate and does not enable product
`optimize/full`. The next package is S3.5 Bottleneck Model Integration.

## 完成声明检查表

任何未来“已完成”声明必须同时回答：

1. 数据结构是否存在？
2. 是否接入真实主流程？
3. 是否控制真实工具行为？
4. 是否有失败路径和预算测试？
5. 是否有真实端到端 evidence？
6. artifact 是否足以复现？
7. 是否明确不能外推的边界？
