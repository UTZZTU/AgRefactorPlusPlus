# Stage 2 Runtime Evidence Acceptance

## 1. Scope

This record accepts the Stage 2.3 runtime evidence-loop integration milestone. It
does not close the whole of Stage 2.

The accepted runtime chain is:

```text
Testbench Preflight
→ Vitis HLS C Synthesis
→ Public C Simulation Suites
→ Hidden C Simulation Suites
→ accepted / rejected / repair_pending / blocked / review_required
```

All handlers receive the same `RunContext`, including the same
`BudgetManager`, `TraceRecorder`, and `TaskSpec`.

## 2. Evolution covered by this milestone

### 2.1 Early Testbench Reliability foundation

The earlier Testbench Reliability work established:

- compile/link preflight;
- structured failure stage, kind, owner, and next action;
- separation of testbench-owned, candidate-owned, and original-owned failures;
- bounded testbench-only model repair;
- ABI and linkage constraints;
- preservation checks for public calls, macros, tests, seeds, and checks;
- repair artifacts and known usage accounting;
- one real unified CLI + DeepSeek + Vitis stateful-kernel validation.

That milestone is recorded separately in
[`stage2_acceptance.md`](stage2_acceptance.md).

### 2.2 General feedback and state strategy

The later generalization added:

- generic `FeedbackItem` and `FeedbackReport`;
- Preflight, CSYNTH, and Test Evaluation feedback adapters;
- deterministic CSYNTH diagnostic parsing;
- operator-full and agent-safe evidence projections;
- feedback composers;
- deterministic feedback routing;
- validation states and transitions;
- validation feedback coordination;
- public/hidden split-aware feedback composition;
- hidden source and selected-feedback suppression.

### 2.3 Runtime evidence-loop integration

The accepted runtime integration adds:

- generic `ValidationOrchestrator`;
- real Preflight stage handler;
- real CSYNTH stage handler;
- split-aware Public/Hidden CSIM stage handler;
- ordered multi-suite execution;
- Public feedback collection until a terminal external blocker;
- Hidden fail-fast execution;
- exact shared physical tool budgeting;
- agent-safe public trace projection;
- hidden operator evidence retained outside normal agent-visible results;
- budget, launch, timeout, and known failure normalization;
- lazy runtime integration exports to prevent package initialization cycles.

## 3. Accepted revisions

- Branch: `stage2-general-feedback`
- Runtime lazy-export boundary:
  `064c8b440e4eafe109dba7379cc49a12a434919b`
- Split-aware CSIM handler:
  `a354eb085700e2240dd4ace0d53fdb394d3e0e1a`
- Record generated after the real acceptance run on 2026-07-17.

Important earlier milestones in the same evolution include:

- `d91f1a3`: Testbench Reliability core;
- `10c2b21`: public/hidden suite evidence acceptance;
- `7799f2d`: validation feedback coordinator;
- `7551381`: validation runtime orchestrator;
- `8365874`: real Preflight handler;
- `a9cf272`: real CSYNTH handler;
- `f63f7a8`: split test feedback composition.

## 4. Deterministic regression

Result:

```text
531/531 tests passed
FULL_SUITE_STATUS=0
```

The suite includes dedicated tests for:

- runtime lazy import boundaries;
- Public and Hidden split handling;
- multi-suite ordering;
- Public candidate repair routing;
- Hidden fail-fast behavior;
- Hidden coordination suppression;
- exact shared budget forwarding;
- budget exhaustion before launch;
- operator-path redaction;
- suite work-directory creation before the real CSIM executor.

These are infrastructure and behavior tests. They are not 531 real kernels.

## 5. Real toolchain acceptance

Environment:

```text
Ubuntu 22.04
Python 3.10
Vitis HLS 2023.2
vitis-run=/data/Xilinx/Vitis/2023.2/bin/vitis-run
```

Run directory:

```text
/data/agrefactor_runs/
stage2_real_csim_handler_resume5_20260717_184240
```

Primary artifacts:

```text
real_csim_handler_acceptance.json
real_full_chain/trace.jsonl
real_hidden_rejection/trace.jsonl
```

### 5.1 Accepted full chain

The following chain executed with real local tools:

```text
real g++ Preflight
→ real Vitis HLS 2023.2 CSYNTH
→ real Public CSIM compile and executable
→ real Hidden CSIM compile and executable
→ accepted
```

Exact physical usage:

```text
tool_calls=6
compile_calls=3
csynth_calls=1
csim_calls=2
```

No `public_test_calls` or `hidden_test_calls` counters were invented. Public and
Hidden are evaluation roles; physical execution remains represented by
`tool_calls`, `compile_calls`, `csynth_calls`, and `csim_calls`.

### 5.2 Hidden mismatch rejection

A candidate that passes the Public suite but fails a Hidden-only input was
executed with real CSIM:

```text
Public CSIM passed
→ Hidden CSIM failed
→ final_state=rejected
```

Exact physical usage:

```text
tool_calls=4
compile_calls=2
csim_calls=2
```

The Hidden diagnostic marker was verified absent from:

- `ValidationOrchestrationResult`;
- normal `trace.jsonl`;
- selected feedback items;
- retained source report identifiers.

### 5.3 Zero-CSIM-budget blocking

With:

```text
max_csim_calls=0
```

the Public CSIM plan was blocked before compilation:

```text
tool_calls=0
compile_calls=0
csim_calls=0
compile_execution.status=blocked_by_budget
```

No CSIM binary was created.

## 6. Accepted architectural boundaries

The milestone preserves these boundaries:

- `ValidationOrchestrator` remains handler-agnostic;
- `UnifiedRunner` and CLI do not yet construct this validation chain;
- candidate repair and testbench repair are not executed by the new CSIM
  handler;
- no model prompt is built or sent;
- Hidden details do not enter iterative model feedback;
- the real acceptance kernel is deterministic and intentionally small;
- this milestone does not establish multi-kernel or multi-interface
  generality.

## 7. Remaining Stage 2 work

Stage 2 remains open. The next required milestones are:

1. Stage 2.4 Shared Layered Prompt Builder;
2. Stage 2.5 Multi-type Kernel Smoke Matrix;
3. Stage 2.6 final documentation, reproduction, and Stage 2 closure review.

The accepted Stage 2.3 runtime chain is infrastructure for later bounded repair;
it does not itself prove that an LLM can reliably repair arbitrary candidates.
