# AgRefactor++ Goal Traceability

本文只保留当前目标追踪，不复制每个历史 package 的逐提交状态。历史证据由 acceptance、audit 和 history 文件承担。

## 当前总体状态

```text
Stage 0=baseline retained
Stage 1=closed
Stage 2=closed
Pre-Stage-3=closed
Stage 3 planning=frozen
Stage 3 implementation=S3.1-S3.7 accepted; S3.8 accepted only after corrected Legacy target-host matrix
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
| 分层 Prompt | Shared builder、Target/model/evidence/scope/output layers；Candidate/Testbench consumers；S3.4 Structural、S3.5 Bottleneck、S3.6 Pragma analysis/rewrite Prompt 与 identity | S3.7 product consumer 已接通；S3.8 bounded multi-kernel evaluation consumer implemented | Stage 3 | optimize/full unified prompt identity |
| 结构化反馈与状态机 | Preflight、CSYNTH、Public/Hidden、repair、owner、next action、Hidden suppression；S3.2 qualification/PPA；S3.3 deterministic engine；S3.4 Structural、S3.5 Bottleneck、S3.6 typed Pragma consumer | S3.7 product orchestration 已完成；S3.8 3×2×3 real matrix orchestration implemented | Stage 3 | full three-level internal exercise |
| Multi-type ground truth | 7 baselines、7/7 full chains、9/9 fault matrix、16/16 labels | 更广 kernel/版本/设备统计 | Stage 6 | 固定 benchmark 扩展 |
| 安全三级优化器 | S3.1 state/checkpoint；S3.2 qualification/PPA/cache；S3.3 deterministic engine；S3.4 Structural、S3.5 Bottleneck、S3.6 Pragma real model hypothesis/complete-source integration；Legacy `opt.simple_iter` 仍仅为 baseline | S3.7 optimize/full 已完成；S3.8 optimize/full/simple_iter fair evaluation implemented | Stage 3 | S3.7 internal full-chain accepted；S3.8 fixed evaluation protocol |
| Memory Applicability Gate | Legacy RAG 正负 trial 可作为 baseline | schema、score、abstention、off/gated/always | Stage 4 | 负迁移和弃权实验 |
| BudgetManager | LLM/Tool/Compile/CSIM/CSYNTH/wall-time 硬控制；Token/Cost observed-only；S3.3 fallback；S3.4–S3.6 real LLM prospective/physical accounting | S3.7 mixed model/tool product orchestration 已完成；未来 cosim | Stage 3/后续 | full-chain physical budget evidence |
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

S3.3 没有实现真实 optimization prompt/source rewrite，也没有解除产品 `optimize/full` 门禁。S3.4 已补齐 Structural model integration；S3.5 已补齐 typed evidence-driven Bottleneck model integration，二者仍不等于 correctness/PPA 或产品接线。


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
`optimize/full`.


### 已完成 S3.5

```text
typed agent-safe PPA evidence projection
raw report/Hidden evidence excluded
non-authoritative model classification
strict evidence id + signal-field linkage
unknown classification as a safe first-class outcome
evidence-linked Bottleneck hypothesis and complete-source rewrite
explicit per-level provider/executor dispatch
82/82 focused
315/315 optimizer regression
1823/1823 full deterministic regression
bounded real Bottleneck smoke: exactly 2 model calls
Vitis/compile/CSIM/CSYNTH calls=0
```

S3.5 does not claim that model classification is a tool fact or that a generated
source is correct, synthesizable, feasible, or faster. It does not use source
strings, pragma counts, warning regexes, or similar incomplete heuristics as an
authoritative Bottleneck gate. Product `optimize/full` remains gated.


### 已完成 S3.6

```text
typed Pragma directive/target/parameter policy
non-authoritative action_source=model_proposal
exact PPA evidence id + signal leaf linkage
unknown action as safe abstention
complete-source Pragma rewrite
three-level typed provider/executor dispatch
75/75 focused
382/382 optimizer regression
1890/1890 full deterministic regression
bounded real Pragma smoke: exactly 2 model calls
Vitis/compile/CSIM/CSYNTH calls=0
```

S3.6 does not claim that target references are source-backed facts, that a
directive is legal for every tool version, or that generated source is correct,
synthesizable, feasible or faster. It does not use source strings, pragma counts,
loop regexes or warning patterns as an authoritative Pragma gate. Generic
`resource` is clarified into typed `bind_storage`/`bind_op` for safe-v1. Product
`optimize/full` remains gated; S3.7 must complete an internal three-level full
chain before removing those gates.

### 已完成 S3.7

```text
normal optimize/full product adapters
direct optimize independent reference + provided Public/Hidden
full accepted-refactor typed handoff
baseline qualification before optimization model calls
shared BudgetManager/TraceRecorder across product phases
linked root + Stage 3 execution identity
unified safe model-call/output artifacts
28/28 S3.7 focused
402/402 optimizer regression
1941/1941 full deterministic regression
internal real chain: 6 LLM calls + real Vitis compile/CSIM/CSYNTH
```

S3.7 proves product wiring and one bounded full chain. It does not prove stable
PPA improvement, general model quality, multi-kernel success or superiority to
`simple_iter`; those remain S3.8.

<!-- PRE_STAGE4_PRODUCT_VALIDATION_HARDENING:BEGIN -->
## Pre-Stage-4 frozen hardening trace

The Stage 4 entry contract is now frozen in
[`PRE_STAGE4_PRODUCT_VALIDATION_HARDENING_CONTRACT.md`](PRE_STAGE4_PRODUCT_VALIDATION_HARDENING_CONTRACT.md).

| Hardening target | Current evidence boundary | Required closing evidence |
|---|---|---|
| `.env` and API-key environment | README and `.env.example` define local variables; normal CLI behavior must be made explicit | load precedence tests, missing-variable rejection, no-secret artifacts |
| Flash and Thinking | concrete Flash runtime exists; family effort mapping and historical smoke behavior are not the final role-aware contract | requested/effective/provider evidence for every call role |
| Preflight ownership | shared Preflight exists; historical Candidate compile ownership was not globally reliable | independent compile/link/symbol fault matrix |
| Native CSIM | current host differential execution must not be mislabeled as native Vitis CSIM | real `csim_design` invocation and evidence |
| COSIM | no active RTL call site or budget | real Public COSIM, timeout, ownership, cache, and budget |
| Mode budgets | one source-run profile is shared | refactor/optimize/full profiles plus Full reserves |
| CLI truthfulness | broad shared source-command surface exists | consume-or-reject audit for every command |
| Optimizer control | historical fixed `safe-v1` order exists | accepted `dynamic-v1` diagnosis/action/qualification loop |

Stage 4 remains blocked until the complete Pre-Stage-4 closure gate passes.
<!-- PRE_STAGE4_PRODUCT_VALIDATION_HARDENING:END -->

## 完成声明检查表

任何未来“已完成”声明必须同时回答：

1. 数据结构是否存在？
2. 是否接入真实主流程？
3. 是否控制真实工具行为？
4. 是否有失败路径和预算测试？
5. 是否有真实端到端 evidence？
6. artifact 是否足以复现？
7. 是否明确不能外推的边界？

S3.7 v8 hardening: all three analysis/rewrite contracts support typed no-retry abstention with safe reason codes; semantic event linkage replaces fixed-call assumptions while real qualification remains authoritative.

S3.7 v9 traceability: real-smoke candidate persistence is verified through `candidate_index_from_dict`; fixtures use `candidate_index_to_dict` and reject obsolete flat mappings.

S3.7 closure hygiene: safe model-call artifacts now write explicit schema v2; the reader accepts both historical v1 shapes and v2. This is an audit-schema governance correction only and does not change optimization authority or execution.


### S3.8 implemented pending target-host matrix

```text
3 kernel categories × 2 repeats × 3 arms = 18 real units
formal direct optimize
live source-only full
Legacy simple_iter with independent baseline/final qualification
same model/effective provider parameters/Target/suites/budgets
63/63 S3.8 focused after Legacy observer correction
405/405 optimizer regression
2007/2007 target-host full deterministic regression expected
```

S3.8 records success, PPA, invalid ratio, rollback, physical calls, and wall time.
It never treats Legacy internal feedback as correctness authority and never
claims stable superiority from two repeats.

### S3.8 V2 correction trace

The V1 real evidence showed 12 valid product-arm records but six zero-call
Legacy observer failures. V2 links fair-comparison closure to full qualification
stage-order observation, Legacy process start, safe evaluation summary, physical
model calls, and independent final qualification when a candidate exists.
