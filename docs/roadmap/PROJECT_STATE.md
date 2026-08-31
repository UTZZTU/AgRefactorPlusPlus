# AgRefactor++ Current Project State

<!-- V2_3_CURRENT_AUTHORITY_STATE:BEGIN -->
## V2.3 当前权威快照（2026-08-29）

text_route=V2.3
branch=research-roadmap-v2.3
repository_checkout_head_prefix=2bc253a
implementation_head=52d7d0097627ff1f92c3f384170bd1fd4771ada7
behavior_parent_head=5ef7fa9a6011534362a2094e159eee75c672619c
primary_vitis=2023.2
R0_ACCEPTED=true
R0_DOCUMENT_SYNC_APPLIED=true
R0_DOCUMENT_SYNC_PENDING_EXTERNAL_AUDIT=false
R1_SAFETY=accepted_independent_external_review
R1_DATA=accepted_independent_external_review
R1_ACCEPTED=true
R2_STARTED=true
R2_IMPLEMENTATION_STATUS=accepted_independent_external_review
R3_STARTED=false
R4_STARTED=false
R5_STARTED=false
R6_STARTED=false
STAGE4_ALLOWED=false
PACKAGE_SELF_ACCEPTANCE=false
R2_DESIGN_STATUS=accepted_independent_external_review
R2_DESIGN_ACCEPTED=true
R2_ACCEPTED=true
NEXT_STEP=V2.3-R3-design-only

V2.2 remains historical evidence. V2.3 is the current standalone route on the isolated branch.
<!-- V2_3_CURRENT_AUTHORITY_STATE:END -->

> **当前状态唯一入口。** 本文件顶部 V2.3 区块是当前入口；下方旧 Pre-Stage-4 内容仅作历史证据，不是当前执行指针。

## 历史 Pre-Stage-4 快照（非 V2.3 当前路线）

```text
branch=stage2-general-feedback
behavior_head_before_documentation_sync=0ca5dd99fabec1c2c003446975e28128a0926c52
behavior_checkpoint=p4-0f-r5-d-accepted-20260807
latest_deterministic_regression=2268/2268
latest_real_validation=p4_0f_r5_e_v2_no_model_real_vitis
latest_real_validation_status=diagnostic_failed_safe_unknown
latest_real_validation_run_id=p4_0f_r5_e_20260806T172137Z_60901

P4_0A_DOCUMENTATION_CONTRACT=accepted
P4_0B_TYPED_PREFLIGHT=accepted
P4_0B_R_BOUNDED_OPTIMIZE_RECOVERY=accepted
P4_0C_PUBLIC_NATIVE_VITIS_CSIM=accepted_with_R5_E_R1_owner_correction_pending
P4_0D_PUBLIC_RTL_COSIM=accepted_with_R5_E_R1_transport_correction_pending
P4_0E_MODEL_RUNTIME=accepted
P4_0E_R1_NETWORK_EVIDENCE_CLOSURE=accepted

P4_0F_R5_D_IMPLEMENTATION_ACCEPTED=true
P4_0F_R5_D_EVIDENCE_ARCHIVE_VERIFIED=true
P4_0F_R5_D_COMMIT=0ca5dd99fabec1c2c003446975e28128a0926c52
P4_0F_R5_E_V1=failed_package_harness_before_campaign
P4_0F_R5_E_V2_BASELINE_REGRESSION=2268/2268
P4_0F_R5_E_V2_CAMPAIGN_OBSERVABILITY=true
P4_0F_R5_E_V2_BASELINE_REAL_VITIS=true
P4_0F_R5_E_V2_TESTBENCH_RECOVERY_CASES=2/2
P4_0F_R5_E_V2_CANDIDATE_RECOVERY_CASES=0/2
P4_0F_R5_E_V2_PROVIDER_DIAGNOSTIC=not_run_by_stop_rule
P4_0F_R5_E_RUNTIME_GATES_PASSED=false
P4_0F_R5_ACCEPTED=false

LEGACY_DIFFERENTIAL_BATCH_A=planned_after_R5_before_P4_0F_FINAL
REAL_CODE_DISCOVERY_BATCH_A=planned_after_R5_before_P4_0F_FINAL
REAL_CODE_DISCOVERY_BATCH_B=planned_after_P4_0G_before_P4_0H

P4_0F_COMPLETE=false
PRE_STAGE4_COMPLETE=false
STAGE4_ALLOWED=false
NEXT_STEP=P4-0F-R5-E-R1
```

## 1.1 当前裁决与下一步

R5-D 已在 `0ca5dd99...` 冻结。R5-E v2 证明 campaign/heartbeat、baseline real Vitis 和两条 Testbench hybrid recovery 可运行，同时暴露 Native CSIM phase 分类与 COSIM phase-scoped typed outcome 两个真实缺口。系统安全停在 unknown/review，没有 false acceptance，也没有运行 provider diagnostic。

唯一下一实现包是 [P4-0F-R5-E-R1](PRE_STAGE4_P4_0F_R5_E_R1_DECISION_RECORD.md)。修复后必须新建 checkpoint，并从头重跑五案例 R5-E；归档独立通过后才能把 R5 标为 accepted。

R5 accepted 后先执行 Legacy differential batch A 与 Real-code discovery batch A，再进入 P4-0F-Final。扩展 discovery batch B 位于 P4-0G 后、P4-0H 前。

## 2. 当前已验收能力

### 普通产品入口

```bash
python -m agrefactor.cli refactor \
  SOURCE \
  --top TOP
```

已完成：

- source-only 输入；
- 默认 `deepseek-v4-flash` 与用户 fixed model/family/endpoint/API-key-env 覆盖；
- 调用 CWD `.env`、typed credential gate、role-specific Thinking/reasoning；
- 静态 ModelFamilyProfile；
- TargetProfile 真实下传；
- Public/Hidden 独立来源与多 suite；
- 自动 Testbench 生成和有限 repair；
- Candidate 生成、有限 repair 和正式 Stage 2 裁决；
- Preflight、Public native Vitis CSIM、Vitis CSYNTH、Public RTL COSIM、Hidden differential；
- 共享 BudgetManager、TraceRecorder 和 Execution Identity；
- 默认简洁输出和完整 artifacts；
- 当前 CLI 参数合同、P4-0D 真实 Vitis 链与 P4-0E/R1 真实网络证据。

### 共享基础设施

- `TaskSpec`、`TargetProfile`、`RunMode`；
- Provider-neutral Model Registry 和 OpenAI-compatible Provider；
- 结构化 Feedback、Router、State Machine 和 Validation Orchestrator；
- Shared Layered Prompt Builder；
- Candidate/Testbench repair contracts；
- LLM、Tool、Compile、CSIM、CSYNTH、COSIM 和 wall-time 硬预算；
- Token/Cost observed-only 记录；
- 真实工具调用和 suite provenance；
- operator-full / agent-safe / Hidden suppression 边界；
- Structural optimization layered Prompt、strict hypothesis JSON、complete-source model contract；
- real model call safe audit、shared LLM budget accounting 与显式 qualification adapter boundary；
- typed agent-safe PPA projection、非权威 Bottleneck classification、evidence-linked hypothesis、typed non-authoritative Pragma action 与完整三层 dispatch。

## 3. 最新真实验证

### 3.1 P4-0E-R1 committed network evidence closure

```text
run_id=p4_0e_r1_network_evidence_v2_20260804T141054Z_3612651
status=accepted_real_network
commit=81804dff2c846b4f79d636cc412fca5b33eca8eb
focused_tests=4/4
full_regression=2108/2108
shared_budget_manager=true
max_llm_calls=1
physical_provider_calls=1
exact_once_llm_accounting=true
artifact_identity_sha256=db6d4996d71ba2a6bfe99beb804f7ad1826684ff2b737220933f17061e2b7c2d
artifact_file_sha256=0211f48cb908bf3bb76ec3edd3c7465828b320fb4da211debd7e0c08e40d31c3
secret_values_persisted=false
dotenv_contents_persisted=false
private_reasoning_persisted=false
hidden_exposed_to_model=false
artifact_root=/data/agrefactor_runs/p4_0e_r1_network_evidence_v2_20260804T135924Z_3568987
```

### 3.2 Latest complete real Vitis qualification chain

```text
run_id=p4_0d_public_rtl_cosim_v16_20260804T064831Z_1709639
status=accepted_real_vitis
commit=b543604cd311eab4380987b09447842542e3214b
preflight=passed
public_native_vitis_csim=passed
csynth=passed
public_rtl_cosim=passed
hidden=passed
actual_vitis_version=2023.2
network_llm_used=false
artifact_root=/data/agrefactor_runs/p4_0d_public_rtl_cosim_v16_20260804T064831Z_1709639
```

The network smoke proves transport, safe configuration, shared LLM budget and
identity only. The P4-0D run proves the committed-sample Vitis pipeline only.
Neither is an arbitrary-kernel, stable model-quality or stable-PPA claim.

## 4. 当前不能宣称

- 真实 model-backed Structural/Bottleneck/Pragma hypothesis/source contract 等于已通过 correctness、综合或 PPA；
- 产品 `optimize/full` 已解除门禁，但尚无多 kernel、重复实验或稳定 PPA 收益结论；
- 任意 Vitis 版本或器件支持；
- 稳定模型修复/优化成功率；
- Legacy RAG 等于 Memory Applicability Gate；
- TaskSpec version 字段等于真实版本迁移；
- 单次 PPA 改善等于稳定优化收益；
- deterministic tests 等于真实 kernel 数量。

## 5. Stage 3 开始边界

Stage 3 规划合同已经冻结：

- [Stage 3 Frozen Implementation Contract](STAGE3_IMPLEMENTATION_CONTRACT.md)
- [Stage 3 High-Level Design](STAGE3_SAFE_OPTIMIZER.md)

已经验收的第一个实现包是：

```text
candidate state/schema
+ checkpoint persistence
+ best_correct baseline semantics
+ deterministic tests
```

S3.1–S3.7 已验收：当前具备 candidate/checkpoint foundation、独立 Stage 3 qualification、typed PPA evidence、latency comparator、exact validation cache identity、完整 deterministic safe-v1 level/round/budget/rollback/resume 状态机、real model-backed Structural/Bottleneck/Pragma contracts，以及普通 `optimize/full` Product Adapters。S3.7 强制 direct optimize 独立 reference 与 provided Public/Hidden、full accepted-refactor handoff、baseline-before-model qualification、shared budget/trace、linked Stage 3 execution identity。内部真实 gate 必须触达三层 analysis；rewrite 由 executable hypothesis 与 complete-source contract 决定，可 qualification 或 typed no-retry abstention。模型合同失败不再摧毁已有 best_correct；网络/工具/文件系统错误仍硬失败。S3.8 已实现冻结的 3-kernel × 2-repeat × 3-arm 评测器；只有目标主机完整矩阵通过后才保留并关闭 Stage 3。

<!-- PRE_STAGE4_PRODUCT_VALIDATION_HARDENING:BEGIN -->
## 5.1 Stage 4 entry blocker: frozen hardening contract

The intended Stage 4 entry remains gated by
[`PRE_STAGE4_PRODUCT_VALIDATION_HARDENING_CONTRACT.md`](PRE_STAGE4_PRODUCT_VALIDATION_HARDENING_CONTRACT.md).

```text
PRE_STAGE4_HARDENING_DESIGN_FROZEN=true
PRE_STAGE4_HARDENING_IMPLEMENTATION_COMPLETE=false
P4_0A_DOCUMENTATION_CONTRACT=accepted_document_freeze
P4_0B_TYPED_PREFLIGHT=accepted
P4_0B_R_BOUNDED_OPTIMIZE_RECOVERY=accepted
P4_0C_PUBLIC_NATIVE_VITIS_CSIM=accepted_real_vitis
P4_0D_PUBLIC_RTL_COSIM=accepted_real_vitis
P4_0E_MODEL_RUNTIME=accepted_real_network
P4_0E_ACCEPTED_BEHAVIOR_COMMIT=eabb2b7e7f5123f3e3f90fe6b6aa0f4a16c6c4a7
P4_0E_ACCEPTED_RUN_ID=p4_0e_model_runtime_v9_20260804T123830Z_3215756
P4_0E_R1_NETWORK_EVIDENCE_CLOSURE=accepted
P4_0E_R1_ACCEPTED_COMMIT=81804dff2c846b4f79d636cc412fca5b33eca8eb
P4_0E_R1_ACCEPTED_RUN_ID=p4_0e_r1_network_evidence_v2_20260804T141054Z_3612651
P4_0E_REPOSITORY_CLOSURE=accepted
P4_0E_AUTHORITY_STATE_SYNC=accepted
NEXT_IMPLEMENTATION_PACKAGE=P4-0F
STAGE4_ALLOWED=false
```
<!-- PRE_STAGE4_PRODUCT_VALIDATION_HARDENING:END -->

<!-- PRE_STAGE4_REAL_VALIDATION_SCHEDULE:BEGIN -->
## 5.2 Frozen real-tool and network-model validation cadence

The authoritative cadence remains frozen in
[`PRE_STAGE4_REAL_VALIDATION_SCHEDULE_DECISION.md`](PRE_STAGE4_REAL_VALIDATION_SCHEDULE_DECISION.md)
and section 8.1 of the master hardening contract.

```text
PRE_STAGE4_REAL_VALIDATION_SCHEDULE_FROZEN=true
P4_0C_REAL_NATIVE_VITIS_CSIM_REQUIRED=true
P4_0C_REAL_VALIDATION=accepted
P4_0D_REAL_CSIM_CSYNTH_COSIM_HIDDEN_REQUIRED=true
P4_0D_REAL_VALIDATION=accepted
P4_0E_FIRST_POST_HARDENING_NETWORK_LLM_SMOKE=accepted
P4_0E_R1_SHARED_BUDGET_AND_IDENTITY_EVIDENCE=accepted
P4_0F_MEASURED_REAL_RUNS_FOR_BUDGET_DEFAULTS=true
P4_0G_NETWORK_LLM_OPTIMIZE_FULL_SMOKE=true
P4_0H_FORMAL_MULTI_KERNEL_NETWORK_LLM_VITIS_REVALIDATION=true
P4_0H_AUTHORITATIVE_PRE_STAGE4_REAL_EVIDENCE_MATRIX=true
DETERMINISTIC_TESTS_DO_NOT_EQUAL_REAL_END_TO_END=true
HISTORICAL_STAGE3_REAL_SMOKE_DOES_NOT_PROVE_NEW_PIPELINE=true
NEXT_IMPLEMENTATION_PACKAGE=P4-0F
```

P4-0F must now measure real Refactor/Optimize/Full consumption on the stable
pipeline before selecting mode-specific defaults or Full Optimize reserves.
<!-- PRE_STAGE4_REAL_VALIDATION_SCHEDULE:END -->

## 6. 当前权威文档

1. [ROADMAP.md](ROADMAP.md)
2. [GOAL_TRACEABILITY.md](GOAL_TRACEABILITY.md)
3. [Pre-Stage-4 hardening contract](PRE_STAGE4_PRODUCT_VALIDATION_HARDENING_CONTRACT.md)
4. [Pre-Stage-4 real-validation schedule](PRE_STAGE4_REAL_VALIDATION_SCHEDULE_DECISION.md)
5. [P4-0E decision record](PRE_STAGE4_P4_0E_DECISION_RECORD.md)
6. [P4-0E acceptance](../acceptance/pre-stage4/p4_0e_model_runtime_acceptance.md)
7. [P4-0E-R1 decision record](PRE_STAGE4_P4_0E_R1_NETWORK_EVIDENCE_CLOSURE_DECISION.md)
8. [P4-0E-R1 acceptance](../acceptance/pre-stage4/p4_0e_r1_network_evidence_closure_acceptance.md)
9. [P4-0E authority-state synchronization](../acceptance/pre-stage4/p4_0e_authority_state_sync_acceptance.md)
10. [P4-0D decision record](PRE_STAGE4_P4_0D_DECISION_RECORD.md)
11. [P4-0D acceptance](../acceptance/pre-stage4/p4_0d_public_rtl_cosim_acceptance.md)
12. [P4-0D authority-state synchronization](../acceptance/pre-stage4/p4_0d_authority_state_sync_acceptance.md)
13. [CLI parameter reference](../guides/CLI_PARAMETER_REFERENCE.md)
14. [Reproduction status](../guides/REPRODUCTION_STATUS.md)
15. [Stage 3 implementation contract](STAGE3_IMPLEMENTATION_CONTRACT.md)
16. [Stage 3.8 evaluation acceptance](../acceptance/stage3/stage3_s38_evaluation_acceptance.md)

## 7. 工程原则

- 通用项目命名，不引入赛事绑定词；
- correctness first；
- Hidden evidence 不进入模型 Prompt、普通结果或普通 trace；
- 不用 deterministic tests 冒充真实工具验收；
- 只实现有当前 consumer 的 schema、registry 和 budget；
- Stage 3 每个包先冻结范围、再实现、再做确定性与真实证据验收。

S3.7 v8 hardening: all three model levels use typed no-retry analysis/rewrite abstention with safe reason codes; acceptance correlates calls, decisions, candidates and qualification rather than requiring stochastic rewrites; product qualification authority is unchanged.

S3.7 v9 observer correction: acceptance reads the canonical versioned candidate-index artifact through the product parser; real fixtures use the same serializer and obsolete flat fixtures are rejected.

S3.7 closure hygiene: the status header now explicitly marks Product Adapters accepted, the acceptance package label is V9, and safe model-call artifacts write schema v2 with documented v1/v2 read compatibility. Product behavior and the S3.8 next-package boundary are unchanged.


S3.8 implementation boundary: the frozen target-host matrix is 3 committed
kernel categories × 2 repeats × direct optimize/live source-only full/Legacy
simple_iter. Legacy inputs and outputs are independently qualified, all arms
share model/Target/budget/provider parameters, and no stable-superiority claim
is permitted. The repository payload is retained only after the complete matrix
has zero infrastructure failures and exercises real Vitis on every kernel.

## S3.8 V2 correction state

The first 18-record target-host run is retained as diagnostic evidence, not
Stage 3 closure: all six `simple-iter` records were observer RuntimeErrors before
Legacy model execution. The twelve `safe-optimize`/`source-full` records remain
valid and immutable. V2 must rerun only the six Legacy units under the same
protocol identity and pass the strengthened fair-comparison gate.

<!-- PRE_STAGE4_P4_0B_TYPED_PREFLIGHT:BEGIN -->
## P4-0B typed Preflight accepted

```text
BASE_COMMIT=11df86f199b8da03ed83baf9119841b3610cdad4
P4_0B_TYPED_PREFLIGHT_IMPLEMENTED=true
P4_0B_TYPED_PREFLIGHT_ACCEPTANCE=accepted_local_validation
P4_0B_FOCUSED_TESTS=64
P4_0B_FULL_REGRESSION_TESTS=2044
P4_0B_ACCEPTED_COMMIT=717efb78e4dd53fbe1fdc14d7db78632c227ea1a
STAGE4_ALLOWED=false
NEXT_IMPLEMENTATION_PACKAGE_AT_ACCEPTANCE=P4-0B-R_BOUNDED_OPTIMIZE_CANDIDATE_RECOVERY
```

P4-0B replaces mixed compile/link ownership guessing with independent
Testbench/reference/Candidate compilation, object symbol checks, component LTO
interface probes, final link, typed reasons, unknown-safe routing, and physical
staged budget accounting. See the
[P4-0B decision record](PRE_STAGE4_P4_0B_DECISION_RECORD.md) and
[acceptance](../acceptance/pre-stage4/p4_0b_typed_preflight_acceptance.md).
<!-- PRE_STAGE4_P4_0B_TYPED_PREFLIGHT:END -->

<!-- PRE_STAGE4_P4_0B_R_CONTRACT:BEGIN -->
## P4-0B-R bounded Optimize Candidate recovery accepted

```text
P4_0B_R_DESIGN_FROZEN=true
P4_0B_R_IMPLEMENTED=true
P4_0B_R_R1_PREFLIGHT_RECOVERY=accepted
P4_0B_R_R2_CSYNTH_LEGALITY_RECOVERY=accepted
MAX_REPAIRS_PER_ROOT_OPTIMIZE_CANDIDATE=1
PUBLIC_CSIM_REPAIR=false
HIDDEN_REPAIR=false
PPA_REPAIR=false
P4_0B_R_FOCUSED_TESTS=22
P4_0B_R_FULL_REGRESSION=2066
P4_0B_R_ACCEPTED_COMMIT=fd95204e6702649de662804754e64e96fb5edad4
STAGE4_ALLOWED=false
NEXT_IMPLEMENTATION_PACKAGE_AT_ACCEPTANCE=P4-0C_PUBLIC_NATIVE_VITIS_CSIM
```

The implementation retains failed Candidate records, creates a new contiguous
repair descendant, restarts complete Optimize qualification, uses one shared
BudgetManager and changes `best_correct` only after full qualification and PPA
comparison.
<!-- PRE_STAGE4_P4_0B_R_CONTRACT:END -->

<!-- PRE_STAGE4_P4_0C_NATIVE_VITIS_CSIM:BEGIN -->
## P4-0C Public native Vitis CSIM accepted

```text
P4_0C_PUBLIC_NATIVE_VITIS_CSIM_IMPLEMENTED=true
P4_0C_PUBLIC_NATIVE_VITIS_CSIM_ACCEPTANCE=accepted_real_vitis
P4_0C_UNIFIED_STAGE_ORDER=preflight_public_native_csim_csynth_hidden
P4_0C_PUBLIC_BACKEND=native_vitis
P4_0C_HIDDEN_BACKEND=host_differential
P4_0C_NETWORK_LLM_USED=false
P4_0C_FOCUSED_TESTS=23
P4_0C_FULL_REGRESSION=2089
P4_0C_CACHE_PIPELINE=prestage4-native-vitis-csim-v1
P4_0C_CANDIDATE_REPAIR_PREFIX=task_aware
P4_0C_STAGE2_SMOKE_ORDER=preflight_public_csynth_hidden
P4_0C_STAGE2_SMOKE_BUDGET=5_tool_2_compile_1_csynth_2_csim
P4_0C_ACCEPTED_COMMIT=d61004f056e585199177891d576f83070f4dbdbb
P4_0C_REPOSITORY_CLOSURE=accepted
STAGE4_ALLOWED=false
NEXT_IMPLEMENTATION_PACKAGE_AT_ACCEPTANCE=P4-0D_PUBLIC_RTL_COSIM
```

The accepted real smoke uses a committed model-independent sample and
actual Vitis HLS `csim_design`. It proves the new Public tool stage and
ordering only; it is not a network-model, arbitrary-kernel or
stable-optimization claim.
<!-- PRE_STAGE4_P4_0C_NATIVE_VITIS_CSIM:END -->

<!-- PRE_STAGE4_P4_0D_PUBLIC_RTL_COSIM:BEGIN -->
## P4-0D Public RTL COSIM accepted

```text
P4_0D_PUBLIC_RTL_COSIM_IMPLEMENTED=true
P4_0D_PUBLIC_RTL_COSIM_ACCEPTANCE=accepted_real_vitis
P4_0D_UNIFIED_STAGE_ORDER=preflight_public_native_csim_csynth_public_rtl_cosim_hidden
P4_0D_COSIM_POLICY_DEFAULT=required
P4_0D_COSIM_DEFAULT_TIMEOUT_S=900
P4_0D_COSIM_TIMEOUT_SAFETY_CEILING=7200
P4_0D_COSIM_REPAIR=false
P4_0D_TESTBENCH_OUTCOME_TRANSPORT=argv
P4_0D_CACHE_PIPELINE=prestage4-public-rtl-cosim-v1
P4_0D_FOCUSED_TESTS=7
P4_0D_FULL_REGRESSION=2096
P4_0D_REAL_VITIS_SMOKE=accepted
P4_0D_NETWORK_LLM_USED=false
P4_0D_ACCEPTED_RUN_ID=p4_0d_public_rtl_cosim_v16_20260804T064831Z_1709639
P4_0D_ACCEPTED_COMMIT=b543604cd311eab4380987b09447842542e3214b
P4_0D_REPOSITORY_CLOSURE=accepted
STAGE4_ALLOWED=false
NEXT_IMPLEMENTATION_PACKAGE_AT_ACCEPTANCE=P4-0E
```

The accepted target-host run proves exact focused/full regression and the real
Vitis 2023.2 chain `Preflight -> Public native Vitis CSIM -> CSYNTH -> Public
RTL COSIM -> Hidden` with `network_llm_used=false`. It does not close later
Pre-Stage-4 packages and does not permit Stage 4.
<!-- PRE_STAGE4_P4_0D_PUBLIC_RTL_COSIM:END -->

<!-- PRE_STAGE4_P4_0E_MODEL_RUNTIME:BEGIN -->
## P4-0E model runtime and network evidence accepted

```text
P4_0E_MODEL_RUNTIME_IMPLEMENTED=true
P4_0E_MODEL_RUNTIME_ACCEPTANCE=accepted_real_network
P4_0E_DEFAULT_MODEL=deepseek-v4-flash
P4_0E_REASONING_DEFAULT=auto
P4_0E_DOTENV_OVERRIDE=false
P4_0E_DEEPSEEK_THINKING=true
P4_0E_FOCUSED_TESTS=8
P4_0E_FULL_REGRESSION=2104
P4_0E_ACCEPTED_RUN_ID=p4_0e_model_runtime_v9_20260804T123830Z_3215756
P4_0E_ACCEPTED_COMMIT=eabb2b7e7f5123f3e3f90fe6b6aa0f4a16c6c4a7
P4_0E_R1_NETWORK_EVIDENCE_CLOSURE=accepted
P4_0E_R1_ACCEPTED_RUN_ID=p4_0e_r1_network_evidence_v2_20260804T141054Z_3612651
P4_0E_R1_ACCEPTED_COMMIT=81804dff2c846b4f79d636cc412fca5b33eca8eb
P4_0E_R1_FOCUSED_TESTS=4
P4_0E_R1_FULL_REGRESSION=2108
P4_0E_R1_SHARED_BUDGET_MANAGER=true
P4_0E_R1_LLM_CALLS=1
P4_0E_R1_ARTIFACT_IDENTITY_SHA256=db6d4996d71ba2a6bfe99beb804f7ad1826684ff2b737220933f17061e2b7c2d
P4_0E_REPOSITORY_CLOSURE=accepted
P4_0E_AUTHORITY_STATE_SYNC=accepted
NEXT_IMPLEMENTATION_PACKAGE=P4-0F
STAGE4_ALLOWED=false
```

P4-0E closes model defaults, local `.env`, credentials, Thinking/reasoning,
safe evidence and legacy YAML defaults. P4-0E-R1 closes the master-contract
shared-budget and exact commit/artifact identity requirements. P4-0F and later
behavior remains pending.
<!-- PRE_STAGE4_P4_0E_MODEL_RUNTIME:END -->

<!-- PRE_STAGE4_P4_0D_R1_COSIM_CORRECTION:BEGIN -->
## P4-0D-R1 accepted COSIM integration correction

```text
P4_0D_R1_COSIM_INTEGRATION_CORRECTION=accepted_real_vitis
P4_0D_R1_BEHAVIOR_COMMIT=2132cfd00323f7c217bf13258b11ba87480341ab
P4_0D_R1_ACCEPTED_RUN_ID=p4_0d_r1_cosim_correction_acceptance_20260805T055822Z_3491101
P4_0D_R1_FOCUSED_TESTS=9/9
P4_0D_R1_FULL_REGRESSION=2117/2117
P4_0D_R1_TYPED_PASS_AFTER_TESTBENCH_ZERO=true
P4_0D_R1_RETURNCODE_ALONE_SUFFICIENT=false
P4_0D_R1_FAILURE_OWNER_INFERRED=false
P4_0D_R1_HIDDEN_EXPOSED=false
P4_0F_PRIOR_MATRIX_VALID_FOR_BUDGET_FREEZE=false
NEXT_IMPLEMENTATION_PACKAGE=P4-0F
PRE_STAGE4_HARDENING_IMPLEMENTATION_COMPLETE=false
STAGE4_ALLOWED=false
```
<!-- PRE_STAGE4_P4_0D_R1_COSIM_CORRECTION:END -->


<!-- R1_EXTERNAL_ACCEPTANCE:BEGIN -->
## R1 external acceptance receipt

```text
R1_ACCEPTED=true
review_authority=independent_external_review
reviewed_at=2026-08-25T14:08:59+08:00
result_archive=agrefactor_r1_consolidated_implementation_validation_v1_20260825T060024Z_1639926.tar.gz
result_archive_sha256=d93088ec50ae7f44105cc5acf87673fcc33e3c53c4d10ca35ef9f174c852fc29
full_regression=2342/2342
model_provider_calls=0
new_real_vitis_phase_runs=0
hidden_source_reads=0
package_self_acceptance=false
R2_STARTED=false
```

R1 acceptance was applied from the external decision whose SHA-256 is
`15c7b0e4f167596906a379722bb3a6ca065b18e96dd435d0ea60f18d5eafe55f`.  This repository script did not review or accept its
own result.  R2 still requires a separately designed bounded plan.
<!-- R1_EXTERNAL_ACCEPTANCE:END -->

<!-- V2_3_R0_EXTERNAL_ACCEPTANCE:BEGIN -->
R0 external acceptance reviewed archive: `agrefactor_v23_r0_correction_finalize_20260828T144812Z_2495321.tar.gz`
archive_sha256=4209d6330f0d9089cc92c97d25ba1112b460a0e5201f11298f99047da0304f77
review_authority=independent_external_review
R0_ACCEPTED=true
R1_SAFETY=pending_reconciliation
R1_DATA=pending_reconciliation
PACKAGE_SELF_ACCEPTANCE=false
<!-- V2_3_R0_EXTERNAL_ACCEPTANCE:END -->

<!-- V2_3_R1_EXTERNAL_ACCEPTANCE:BEGIN -->
R1-Safety reconciliation: accepted by independent external review
R1-Data reconciliation: accepted by independent external review
reviewed_archive=agrefactor_v23_r1_safety_data_comprehensive_v1_20260829T044722Z_2189779.tar.gz
reviewed_archive_sha256=41337c668f049e533d2a12ad627cbb2fe5bbeeabb12e693992ccf155f3dd7732
R1_ACCEPTED=true
R2_STARTED=false
STAGE4_ALLOWED=false
PACKAGE_SELF_ACCEPTANCE=false
<!-- V2_3_R1_EXTERNAL_ACCEPTANCE:END -->

<!-- V2_3_R2_IMPLEMENTATION:BEGIN -->
## V2.3 R2 implementation receipt

R2 shadow diagnostic implementation is present on the `2bc253a` baseline.
The default feature state remains off. Deterministic fake-provider tests verify
the contract and safety boundaries but do not constitute real provider or
Vitis evidence.

```text
R2_STARTED=true
R2_IMPLEMENTATION_STATUS=implemented_pending_external_audit
R2_ACCEPTED=false
REAL_PROVIDER_EVIDENCE=pending
REAL_VITIS_R2_EVIDENCE=pending
PACKAGE_SELF_ACCEPTANCE=false
NEXT_STEP=V2.3-R2-external-validation
```
<!-- V2_3_R2_IMPLEMENTATION:END -->

<!-- V2_3_R2_EXTERNAL_ACCEPTANCE:BEGIN -->
R2 external validation archive: `agrefactor_v23_r2_shadow_diagnostic_external_validation_correction_v7_20260830T143607Z_614600.tar.gz`
archive_sha256=d0b1147596bc8e14695608ca74ce4f719e67f0419279e27eb4f745cc1dabea6c
R2_EXTERNAL_VALIDATION=true
R2_ACCEPTED=true
R2_IMPLEMENTATION_STATUS=accepted_independent_external_review
R3_STARTED=false
NEXT_STEP=V2.3-R3-design-only
independent_audit_state=r2-accepted-independent-external-review
acceptance_run_id=agrefactor_v23_r2_shadow_diagnostic_external_acceptance_v1_20260830T163722Z_1474641
PACKAGE_SELF_ACCEPTANCE=false
<!-- V2_3_R2_EXTERNAL_ACCEPTANCE:END -->

R3_DESIGN_STATUS=design_frozen
R3_ACCEPTED=false
NEXT_STEP=V2.3-R3-implementation

<!-- V2_3_R3_EXTERNAL_ACCEPTANCE:BEGIN -->
R3_DESIGN_STATUS=accepted_independent_external_review
R3_ACCEPTED=true
R3_STARTED=false
NEXT_STEP=V2.3-R3-implementation
acceptance_run_id=agrefactor_v23_r3_conditioned_memory_gate_external_acceptance_20260831T103638Z_2061801
PACKAGE_SELF_ACCEPTANCE=false
<!-- V2_3_R3_EXTERNAL_ACCEPTANCE:END -->

<!-- V2_3_R3_IMPLEMENTATION:BEGIN -->
R3_IMPLEMENTATION_STATUS=accepted_independent_external_review
R3_ACCEPTED=true
R3_STARTED=true
NEXT_STEP=V2.3-R4-design-only
PACKAGE_SELF_ACCEPTANCE=false
<!-- V2_3_R3_IMPLEMENTATION:END -->

<!-- V2_3_R3_IMPLEMENTATION_EXTERNAL_ACCEPTANCE:BEGIN -->
R3_EXTERNAL_VALIDATION=clean
R3_ACCEPTED=true
R3_IMPLEMENTATION_STATUS=accepted_independent_external_review
R3_STARTED=true
NEXT_STEP=V2.3-R4-design-only
validation_archive=agrefactor_v23_r3_conditioned_memory_gate_external_validation_20260831T130943Z_3145805.tar.gz
validation_archive_sha256=401b7369f8f3125018ffee17fbcbb2fad9fe26a034cd1c8aab1829d865e42ca8
acceptance_run_id=agrefactor_v23_r3_conditioned_memory_gate_independent_external_acceptance_20260831T134704Z_3407698
PACKAGE_SELF_ACCEPTANCE=false
<!-- V2_3_R3_IMPLEMENTATION_EXTERNAL_ACCEPTANCE:END -->
