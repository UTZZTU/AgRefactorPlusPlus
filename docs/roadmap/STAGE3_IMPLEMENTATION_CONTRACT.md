# Stage 3 Frozen Implementation Contract

> **状态：** FROZEN
> **冻结范围：** Stage 3 安全三级优化器
> **实现状态：** 进行中；S3.1–S3.4 已验收，下一包为 S3.5
> **前置基线：** Pre-Stage-3 已关闭，最新 CLI 后真实 source-only smoke 已通过
> **变更规则：** 任何语义变更必须有明确决策记录、测试和本文更新，不能在实现中静默改写合同。

## 1. 目标与非目标

### 1.1 目标

Stage 3 实现正式 `optimize` 和 `full`，并保证：

- correctness first；
- baseline qualification；
- candidate lineage；
- hypothesis-driven changes；
- Structural → Bottleneck → Pragma 顺序；
- checkpoint/rollback；
- exact validation cache；
- bounded strategy；
-共享物理硬预算；
- `best_correct` 不可破坏；
-完整 artifacts 和 Execution Identity；
-与 Legacy `opt.simple_iter` 公平对照。

### 1.2 非目标

Stage 3 不实现：

- Memory Applicability Gate；
-版本迁移；
-自动模型池；
-动态未知模型探测；
- repository-level migration；
- RTL cosim 主路径；
-多版本 TargetProfile 矩阵；
-形式化等价证明；
-大规模 beam search；
-无当前 consumer 的新 registry/schema。

## 2. 命令语义

### 2.1 `refactor`

保持当前行为，不在 Stage 3 中改变其正式 correctness/validation 合同。

### 2.2 `optimize`

输入被视为已有 HLS baseline：

```bash
python -m agrefactor.cli optimize \
  kernel_hls.cpp \
  --top process_top_hls \
  --model MODEL
```

在任何优化模型调用前，baseline 必须完成 Stage 3 baseline qualification。baseline 不合格时：

- 不生成优化假设；
- 不进入三级优化；
- 返回结构化 rejected/blocked 结果；
- 保留资格失败 artifacts；
- 不把 optimize 静默退化为 refactor。

### 2.3 `full`

严格执行：

```text
refactor
→ formal accepted candidate
→ freeze that candidate as optimization baseline
→ optimize
```

refactor 未 accepted 时不得进入 optimize。两个阶段共享一个 run-level BudgetManager、TraceRecorder 和最终 artifact root，但各自保留独立 phase artifacts。

## 3. Baseline Qualification

Baseline 必须满足：

1. 源码和 top contract 有效；
2. Preflight compile/link 通过；
3. 所有 Public suites 通过；
4. Vitis HLS CSYNTH 成功；
5. 所有 Hidden suites 通过；
6. 实际 TargetProfile、toolchain version 和 suites identity 完整；
7. Execution Identity 可验证；
8. 至少形成一份可比较的 CSYNTH report。

顺序采用 cheap-to-expensive 原则：

```text
static/task checks
→ Preflight
→ Public CSIM
→ CSYNTH
→ Hidden CSIM
```

Stage 3 不全局改写 Stage 2 已验收的 refactor backend 顺序；优化器通过独立 qualification orchestration 复用现有 handlers。

## 4. 核心术语

### 4.1 Functionally correct

候选通过：

```text
Preflight
+ all Public suites
+ all Hidden suites
```

### 4.2 Synthesizable

候选在 effective TargetProfile 下完成真实 CSYNTH，并获得有效 report evidence。

### 4.3 Best correct

`best_correct` 必须同时：

- functionally correct；
- synthesizable；
- toolchain/Target/suite identity 完整；
-没有 blocking configuration/tool error；
-来自当前 run 的 baseline 或其可追溯后代。

`best_correct` 不要求一定优于 baseline。baseline qualification 成功后，baseline 是初始 `best_correct`。

### 4.4 Feasible for objective

候选满足当前 objective 的硬约束。Stage 3 v1：

- TargetProfile resource limit 为 `null` 时不创建伪限制；
-存在明确 resource limit 时，超过限制的候选仍可记录为 `best_correct` 候选，但不能成为 `best_ppa`；
-若整个 run 没有 objective-feasible candidate，最终状态为 `no_feasible_candidate`，同时仍返回 `best_correct` 作为安全恢复产物。

### 4.5 Best PPA

`best_ppa` 是当前 objective 下最优且 objective-feasible 的 `best_correct`。

## 5. 初始 Objective Contract

Stage 3 v1 只正式支持：

```text
optimization_objective=latency
```

其他 objective 名称暂不开放，不能静默映射。

比较前提：

- same effective TargetProfile；
- same actual toolchain fingerprint；
- same Public/Hidden suite identity；
- valid CSYNTH reports；
-候选均为 `best_correct`；
-候选满足 objective hard constraints。

Latency comparator：

1. lower worst-case/maximum latency cycles；
2. lower initiation interval when both available；
3. lower maximum resource utilization ratio；
4. lower achieved clock period；
5. lower candidate sequence number作为确定性 tie-break。

缺少首要 latency 指标的候选不得用次级指标冒充可比较结果。

## 6. Stage Levels

```text
structural
bottleneck
pragma
```

### 6.1 Structural

允许修改：

-算法和循环结构；
-函数边界；
-数据布局；
-内存访问顺序；
-局部缓存；
-producer/consumer；
-数据流结构。

禁止只通过批量添加 pragma 伪装成 Structural。

### 6.2 Bottleneck

只有 baseline 或当前 best 的有效 CSYNTH/report evidence 存在时进入。

允许针对：

- II；
-loop-carried dependency；
-memory port contention；
-critical path；
-resource bottleneck；
-unknown loop bound；
-dataflow stall/deadlock risk。

每个 hypothesis 必须引用具体 evidence id。

### 6.3 Pragma

Structural 和 Bottleneck 均完成或明确无合法 hypothesis 后才能进入。

允许：

```text
PIPELINE
UNROLL
ARRAY_PARTITION
DATAFLOW
INLINE
BIND / RESOURCE
```

不得恢复已经被前级证明无效的候选状态。

## 7. Frozen Safe-v1 Policy Profile

Stage 3 首个正式 policy profile：

```text
name=safe-v1
search=sequential_best_first
objective=latency

structural:
  max_rounds=2
  hypotheses_per_round=3
  executed_branches_per_round=1

bottleneck:
  max_rounds=2
  hypotheses_per_round=3
  executed_branches_per_round=1

pragma:
  max_rounds=3
  hypotheses_per_round=3
  executed_branches_per_round=1

max_executed_candidates=7
candidate_correctness_repair_attempts=0
```

说明：

- 模型可提出 3 个 hypothesis，但策略每轮只执行 1 个；
- v1 不在单个 optimization candidate 内嵌额外 correctness repair loop；
-失败候选直接拒绝并回滚；
-物理 BudgetManager 比策略上限优先；
-任一硬预算先耗尽时立即停止后续生成。

这些是安全初始默认值，不是论文最优参数结论。

## 8. Hypothesis Schema

每个 hypothesis 必须包含：

```json
{
  "schema_version": 1,
  "hypothesis_id": "hyp-...",
  "level": "structural|bottleneck|pragma",
  "parent_candidate_id": "cand-...",
  "claim": "因果假设",
  "supporting_evidence_ids": ["evidence-..."],
  "expected_benefit": {
    "metric": "latency_cycles",
    "direction": "decrease"
  },
  "risk": "low|medium|high",
  "modification_scope": ["..."],
  "verification_plan": ["preflight", "public", "csynth", "hidden"],
  "model_identity": {},
  "prompt_identity_sha256": "..."
}
```

禁止：

- 无 parent；
-无 evidence 的 Bottleneck hypothesis；
-没有 modification scope；
-没有 verification plan；
-将 Hidden 内容写入 claim 或 Prompt evidence。

## 9. Candidate Record Schema

每个 executed candidate 必须有：

```json
{
  "schema_version": 1,
  "candidate_id": "cand-0001",
  "sequence": 1,
  "parent_candidate_id": "baseline",
  "hypothesis_id": "hyp-...",
  "level": "structural",
  "source_sha256": "...",
  "source_artifact": "candidates/cand-0001/source.cpp",
  "status": "generated|validating|accepted|rejected|blocked|error",
  "correctness": {},
  "synthesis": {},
  "ppa": {},
  "budget_before": {},
  "budget_after": {},
  "decision": {},
  "created_at_utc": "..."
}
```

状态只能单向推进；不能把 rejected candidate 改回 accepted。

## 10. Optimizer State Schema

持久化：

```json
{
  "schema_version": 1,
  "run_id": "...",
  "policy_profile": "safe-v1",
  "objective": "latency",
  "baseline_candidate_id": "baseline",
  "current_candidate_id": "...",
  "best_correct_candidate_id": "...",
  "best_ppa_candidate_id": "...",
  "current_level": "structural",
  "current_round": 1,
  "executed_candidate_count": 0,
  "terminal_status": null,
  "checkpoint_sequence": 0
}
```

`best_correct_candidate_id` 在 baseline qualification 成功后不得为空。

## 11. Candidate Validation Gate

每个候选按以下顺序：

```text
source/schema validation
→ Preflight
→ Public suites
→ CSYNTH
→ Hidden suites
→ PPA extraction
→ objective feasibility
→ compare/update best
```

规则：

- Preflight/Public 失败：拒绝，不运行 CSYNTH；
- CSYNTH 失败：拒绝，不运行 Hidden；
- Hidden 失败：拒绝，禁止向模型暴露内容；
- PPA report 无法解析：`review_required` 或 `blocked`，不更新 best；
- PPA 退化：记录 accepted-correct，但不更新 best_ppa；
-任何 blocking tool/configuration failure 停止当前 branch；
- unknown 不能猜测为可修复 candidate failure。

## 12. Decision Schema

每个候选结束时写入：

```json
{
  "schema_version": 1,
  "candidate_id": "...",
  "decision": "update_best|keep_best|reject|block|review_required",
  "correctness_passed": true,
  "synthesis_passed": true,
  "objective_feasible": true,
  "comparison": {
    "better": false,
    "reason": "latency_not_improved"
  },
  "rollback_to_candidate_id": "best-correct-id",
  "reason_codes": ["..."]
}
```

## 13. Checkpoint and Rollback

### 13.1 Checkpoint

原子写入：

```text
optimizer/state.json
optimizer/candidate_index.json
optimizer/checkpoints/checkpoint-XXXX.json
optimizer/best_correct.cpp
optimizer/best_ppa.cpp (when available)
```

Checkpoint 时机：

- baseline qualification 后；
-每个候选终态后；
-best_correct/best_ppa 更新后；
-进入新 level 前；
-预算耗尽或异常终止前。

### 13.2 Rollback

Rollback 不修改历史 candidate artifact，只改变 current pointer。

失败候选不得覆盖：

```text
best_correct.cpp
best_ppa.cpp
```

## 14. Validation Cache

只缓存 validation/tool evidence，不缓存模型生成决策。

Cache key 必须至少包含：

```text
candidate source SHA-256
effective TargetProfile identity
actual toolchain fingerprint
Public/Hidden suite identities and content hashes
validation pipeline/schema version
compile flags
clock/part
parser profile
```

以下任一变化必须 cache miss：

- source；
- Target field；
- actual toolchain；
- suite content/provenance；
- compile flags；
- parser profile；
- validation schema。

Cache hit 必须写 trace，并计为 0 次真实工具 launch。

## 15. Budget Contract

### 15.1 物理权威

沿用共享 BudgetManager：

```text
LLM calls
Tool calls
Compile calls
CSIM calls
CSYNTH calls
wall time
```

Token/Cost 继续 observed-only。

### 15.2 策略上限

Policy rounds、hypothesis count 和 max executed candidates 是策略上限，不替代物理预算。

### 15.3 耗尽行为

任何硬预算不足时：

1. 不启动下一真实调用；
2. 当前 candidate 标记 blocked；
3. 写 checkpoint；
4. 停止生成新 hypothesis/candidate；
5. 返回 `best_correct`；
6. final status=`budget_exhausted_with_best_correct`；
7. 若 baseline qualification 前耗尽，则没有 best_correct，状态为 blocked。

## 16. Artifact Layout

```text
<artifact_root>/
  full_result.json
  execution_identity.json
  trace.jsonl
  model_calls.json
  tool_calls.json
  optimizer/
    policy.json
    state.json
    candidate_index.json
    hypothesis_index.json
    decisions.jsonl
    best_correct.cpp
    best_ppa.cpp
    checkpoints/
    hypotheses/
      hyp-...json
    candidates/
      baseline/
      cand-0001/
        source.cpp
        candidate.json
        decision.json
        correctness.json
        synthesis.json
        ppa.json
        evidence_refs.json
```

不得把完整 Hidden Testbench 或 Hidden diagnostic 复制进模型-facing optimizer artifacts。

## 17. Execution Identity Extension

Stage 3 identity 必须新增：

- optimizer policy profile/version；
- objective；
-baseline source identity；
-candidate lineage aggregate hash；
-hypothesis aggregate hash；
-best_correct identity；
-best_ppa identity；
-cache identity/version；
-terminal optimizer status；
-physical budget usage；
-strategy counters。

现有 source/model/target/prompt/suite/toolchain/repository identity 继续保留。

## 18. CLI Initial Surface

Stage 3 v1 普通 CLI 只新增：

```text
--optimizer-profile safe-v1
--optimization-objective latency
```

二者都有唯一正式值，其他值明确拒绝。Level-specific rounds、beam、cache path、resource override 不在 v1 普通 CLI 暴露。

高级实验参数以后只能在 typed profile 中添加，并要求：

- 当前 consumer；
-默认值；
-安全上限；
-Execution Identity；
-文档和验收。

## 19. Terminal Status

允许：

```text
accepted_improved
accepted_no_improvement
budget_exhausted_with_best_correct
no_feasible_candidate
baseline_rejected
blocked
review_required
error
```

`accepted_*` 必须有完整 `best_correct`。

## 20. Implementation Packages

### S3.1 Candidate State Foundation

状态：**ACCEPTED**（50/50 focused，1558/1558 full deterministic；无模型/Vitis 调用）。

只实现：

- typed HypothesisRecord；
- typed CandidateRecord；
- OptimizerState；
-checkpoint atomic writer；
-best_correct baseline；
-schema/serialization tests。

不调用模型或 Vitis。

### S3.2 Qualification and PPA Evidence

状态：**ACCEPTED**（85/85 S3.2 focused，135/135 optimizer regression，1643/1643 full deterministic；一次现有 baseline 的真实 Vitis HLS 2023.2 replay accepted；模型调用 0）。

- baseline/candidate qualification orchestration；
-PPA report adapter；
-comparator；
-cache identity；
-deterministic tool fixtures；
-一次现有真实 baseline replay。

实现说明：S3.2 使用独立 qualification orchestration 复用 Stage 2 handlers，但不改写 Stage 2 已验收顺序；补充 `CandidateRecord` budget snapshot allowlist，使正常 `tokens` usage 可持久化，同时继续拒绝未知/secret-like budget 字段。

### S3.3 Deterministic Optimizer State Machine

状态：**ACCEPTED**（46/46 S3.3 focused，181/181 optimizer regression，1689/1689 full deterministic；真实模型/网络/Vitis/CSIM/CSYNTH 调用均为 0）。

- typed frozen `safe-v1` policy 与 Structural → Bottleneck → Pragma transitions；
- 2/2/3 rounds、每轮 3 提 1 执行、总 executed candidates 上限 7；
- injected provider/executor protocols、deterministic FakeProvider/FakeExecutor；
- provider-order first-valid selection、malformed/unsafe hypothesis 拒绝；
- shared BudgetManager prospective preflight、budget exhaustion fallback；
- PPA improve/regress/infeasible/incomparable decision、best pointer protection 与 rollback；
- immutable hypothesis artifacts、decision audit stream、checkpointed next action 与 resume 去重；
- 无真实网络模型或 Vitis，未解除产品 `optimize/full` 门禁。

精确消歧见 `STAGE3_S33_DECISION_RECORD.md`。

### S3.4 Structural Model Integration

状态：**ACCEPTED**（52/52 S3.4 focused，233/233 optimizer regression，1741/1741 full deterministic；bounded real model smoke 使用精确 2 次 LLM 调用，真实网络=true，Vitis/compile/CSIM/CSYNTH=0）。

- agent-safe optimization layered Prompt 与 deterministic prompt identity；
- strict versioned Structural hypothesis JSON、最多 3 个 provider-ordered hypotheses 与 adapter-owned IDs；
- complete-source Structural rewrite contract，复用已验收 `CandidateResponseContract` 保护完整源码、semantic change 和 top interface；
- provider-neutral real model registry integration、observed token/cost 与 shared LLM hard-budget accounting；
- safe model-call artifacts，不持久化 raw Prompt/response 或 Hidden/operator-full 内容；
- generated source 与 qualification/PPA 之间保留显式 adapter boundary；
- S3.3 state machine 获得 exact parent source 和 injected network/Vitis trace flags，policy/rollback/checkpoint/best semantics 不变；
- bounded smoke 只证明 model/prompt/response contract，不宣称 correctness、synthesis、feasibility 或 PPA improvement；
- 不使用不完整静态字符串/正则匹配作为 Structural 权威门禁；
- 未解除产品 `optimize/full` 门禁，未提前实现 Bottleneck/Pragma。

精确消歧见 `STAGE3_S34_DECISION_RECORD.md`。

### S3.5 Bottleneck

- report evidence references；
-bottleneck classification；
-真实 bounded smoke。

### S3.6 Pragma

- pragma scope/policy；
-真实 bounded smoke。

### S3.7 Product Adapters

-真实 `optimize`；
-真实 `full`；
-refactor failure gate；
-统一 output/identity。

### S3.8 Evaluation

-多 kernel；
-相同模型/Target/budget/repeats；
-与 `simple_iter` 比较；
-正确性、成功率、latency/II/resource、invalid ratio、rollback、calls 和 wall time。

## 21. Acceptance Matrix

必须覆盖：

- baseline rejected；
-baseline accepted becomes initial best_correct；
-Preflight failure prevents CSYNTH；
-Public failure prevents CSYNTH；
-CSYNTH failure prevents Hidden；
-Hidden failure does not leak；
-PPA regression keeps old best；
-PPA improvement updates best；
-resource constraint blocks best_ppa；
-budget exhaustion returns best_correct；
-cache hit avoids real call accounting；
-cache miss on source/target/suite/toolchain change；
-checkpoint atomic recovery；
-parent/hypothesis lineage complete；
-optimize/full gating；
-multi-kernel real Vitis；
-simple_iter fair comparison。

## 22. Change Control

合同改变必须：

1. 新增明确 decision record；
2. 说明触发变更的真实 evidence；
3. 更新本文和 Goal Traceability；
4. 更新 schema/version；
5. 添加 migration 或兼容策略；
6. 完整回归；
7. 必要时重新做真实 acceptance。

不得因为某个模型输出不方便而弱化 correctness、Hidden、budget 或 best_correct 不变量。
