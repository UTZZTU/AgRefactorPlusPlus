# Stage 2.5.2 Real Full-chain Pass Matrix Acceptance

## 1. Scope

Stage 2.5.2 validates every committed Stage 2 baseline through the real local
validation chain. It adds a reusable matrix runner rather than copying source
code into a one-time acceptance harness.

Feature commit:

```text
71f317b85227604a3959db725ae33b074d66824e
feat: add Stage 2 smoke pass matrix runner
```

## 2. Reusable runner contract

The runner:

- consumes `STAGE2_SMOKE_CASES` in stable order;
- uses one shared `BudgetManager` for the matrix;
- creates a fresh handler set for every case;
- executes Preflight, CSYNTH, Public CSIM, and Hidden CSIM;
- requires all four stages and final `accepted`;
- requires exact per-case physical usage;
- rejects Hidden markers, Hidden testbench code, and ground-truth data in safe
  results or traces;
- performs no model call and no repair.

## 3. Deterministic validation

```text
21/21 targeted passed
77/77 smoke/orchestration related passed
707/707 full unittest passed
```

## 4. Real local matrix

Kernel types:

```text
array_map
reduction
nested_stencil
multi_output
struct_record
hls_stream
stateful
```

Every case executed:

```text
real g++ Preflight
→ real Vitis HLS 2023.2 CSYNTH
→ real Public CSIM
→ real Hidden CSIM
→ accepted
```

Result:

```text
accepted cases = 7/7
```

Per-case exact usage:

```text
tool_calls=6
compile_calls=3
csynth_calls=1
csim_calls=2
llm_calls=0
tokens=0
cost_usd=0.0
```

Shared matrix total:

```text
tool_calls=42
compile_calls=21
csynth_calls=7
csim_calls=14
llm_calls=0
tokens=0
cost_usd=0.0
```

## 5. Stage order and evidence views

Every case recorded:

```text
preflight          agent_safe
csynth             agent_safe
public_evaluation  agent_safe
hidden_evaluation  operator_full
accepted
```

Hidden secret markers and Hidden testbench source are absent from serialized
matrix results and all normal traces.

## 6. Artifacts

Acceptance directory:

```text
/data/agrefactor_runs/stage2_5_2_real_full_chain_pass_matrix_20260719_001400/acceptance
```

Top-level artifacts:

```text
stage2_smoke_pass_matrix.json
stage2_smoke_pass_matrix_artifacts.json
stage2_smoke_pass_matrix_summary.json
matrix_work/traces/<case-id>.jsonl
matrix_work/traces/<case-id>.json
matrix_work/validation/<validation-id>/attempt_000/...
```

Each case retains real Preflight, CSYNTH, Public CSIM, and Hidden CSIM
invocation evidence.

## 7. Evidence boundary

This milestone proves:

- the seven committed baseline shapes pass the complete current local chain;
- struct and `hls::stream` cases reach real Vitis synthesis and both CSIM roles;
- stateful behavior passes Public and Hidden sequential checks;
- one shared budget produces exact per-case deltas and exact matrix totals;
- the current normal result/trace boundary suppresses Hidden source content.

It does not prove:

- arbitrary HLS program support;
- correctness outside the committed tests;
- failure-owner or route accuracy;
- Hidden failure terminal behavior;
- real or fake model repair;
- support for other Vitis versions, devices, or hosts.

Next milestone:

```text
Stage 2.5.3 Fault / Ownership / Hidden Matrix
```
