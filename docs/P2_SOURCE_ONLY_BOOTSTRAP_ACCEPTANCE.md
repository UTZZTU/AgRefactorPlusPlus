# P2 Source-only Bootstrap Acceptance

## Authority and scope

The sole implementation authority is
[`PRE_STAGE3_PRODUCTIZATION_PLAN.md`](PRE_STAGE3_PRODUCTIZATION_PLAN.md).

This is one integrated P2 package. It does not create P2 sub-stages, does not
implement the Stage-3 optimizer, does not close Execution Identity or P5, and
does not run the final real DFS P0 acceptance.

## Evidence

```text
base_head=07d2fc291287c41c0391915b2ef604e599f1a458
baseline_tests=1334
full_regression=1346/1346
new_tests=12
product_patch_id=af57008cd7db13e88400418fc95ac47baf157dc7
artifact_dir=/data/agrefactor_runs/pre_stage3_p2_source_bootstrap_20260723_213917
model_api_called=false
real_csim=false
csynth=false
vitis_run=false
```

## Accepted P2 product contract

```text
source + explicit --top + fixed --model
-> internally managed work/artifact paths
-> normalized source TaskSpec
-> exact effective model configuration
-> effective TargetProfile
-> independent Public/Hidden TestSourcePlan
-> system default / safety ceiling / user hard-budget resolution
-> observed-only Token/Cost soft budgets
-> generation-only reuse of the existing refactor backend
-> initial candidate and test materialization
-> normalized formal TaskSpec
-> Stage-2 formal validation and bounded repair
```

The generation bridge is explicitly not an adjudicator. A legacy generation
success only permits formal validation to start; accepted/rejected remains the
Stage-2 result.

The Public and Hidden selections map into the already reconciled P4 contract.
Repeated provided paths create multiple suites. Mixed split selections derive
`hybrid` automatically. Hidden source is never selected as the prompt-facing
testbench.

The normal `refactor` command is executable. `optimize` and `full` are present
in the frozen ordinary CLI namespace but intentionally reject execution until
the Safe Three-Level Optimizer begins in Stage 3; no placeholder success is
fabricated.

## Correct next status

```text
P1 frozen contract=reconciled
P4 frozen contract=reconciled
P2 source-only bootstrap=deterministically accepted
Execution Identity=next, not closed
P5=not closed
P0 final real DFS=not run
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```

## Runtime budget correction

The integrated P2 package received one contract correction against the same
authority document. Legacy generation now reserves every AG2 model launch before
`agent.run`, coverage compilation/CSIM/gcov launches are budgeted before process
start, testbench signature CSYNTH receives the same run budget, and
`BudgetManager` reservations are thread-safe for concurrent generation.
Post-hoc usage normalization records Token/Cost only and does not replay LLM
launch counts.

```text
correction_base_head=f2e325c7e0302e7166b647ad30f509d538b8182c
full_regression=1352/1352
artifact_dir=/data/agrefactor_runs/pre_stage3_p2_runtime_budget_20260723_232420
legacy_generation_pre_call_llm_budget=closed
coverage_compile_csim_tool_budget=closed
tb_signature_csynth_budget=closed
concurrent_budget_reservation=atomic
posthoc_llm_double_count=false
P2 frozen contract=closed
Execution Identity=next, not started
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```
