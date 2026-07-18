# Stage 2.5.3 Fault / Ownership / Hidden Matrix Acceptance

## Scope

Nine independently labeled fault scenarios were compared with observed
validation stage, route, terminal state, budget, and Hidden visibility.

```text
a09915878aca4012a01b258d1f196ba0f18b4be5
feat: add Stage 2 fault ownership matrix
```

## Matrix

```text
candidate compile          repair_candidate  repair_pending
testbench compile          repair_testbench  repair_pending
original compile           repair_original   repair_pending
Public candidate mismatch  repair_candidate  repair_pending
Hidden candidate mismatch  repair_candidate  rejected
toolchain failure          fix_toolchain      blocked
unknown synthesis          review_unknown     review_required
mixed Public ownership     review_mixed       review_required
Hidden unknown             review_unknown     review_required
```

## Validation

```text
20/20 targeted
65/65 related
727/727 full unittest
9/9 ground-truth matches
```

Physical usage:

```text
tool_calls=13
compile_calls=8
csynth_calls=2
csim_calls=3
llm_calls=0
```

The first five scenarios use real local tools. The final four use normalized
deterministic reports so the installed Vitis environment is not intentionally
damaged. Hidden report IDs, source, testbench text, markers, and ground truth
are absent from safe results and normal traces. No model or repair controller
was invoked.

Acceptance directory:

```text
/data/agrefactor_runs/stage2_5_3_fault_ownership_hidden_matrix_20260719_003933/acceptance
```

Next milestone:

```text
Stage 2.5.4 Evidence Summary
```
