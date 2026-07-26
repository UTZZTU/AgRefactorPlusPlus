# P0 Step E: Repair Budget Parameterization

## Baseline

```text
BASE_HEAD=f32ed5909bc6cc7e83174df158a99cf3eac9e9c8
BASELINE_TESTS=1449
ACTIVE_STEP=E
DEFAULT_LLM_CALLS=32
```

## Shared repair-attempt contract

Testbench and Candidate repair now use one provider-neutral attempt contract.

```text
Testbench repair default = 3
Candidate repair default = 3
valid user range = 1..10
system safety ceiling = 10
```

The normal source-only CLI exposes:

```text
--max-testbench-repairs N
--max-candidate-repairs N
```

The advanced compatibility and repair-aware entrypoints use the same defaults and
ceiling. Values outside the supported range are rejected during argument parsing
or request construction, before a provider call can be launched.

## Attempt semantics

An attempt means one model repair proposal. Initial deterministic validation is
not counted as a repair attempt.

For attempt N, the model-facing prompt receives safe summaries from attempts
1 through N-1. Hidden content, credentials, operator-only paths and complete
private logs are not introduced into those summaries.

Configured attempts are not shortened by repeated diagnostics, small edits,
unchanged responses or response similarity. The loops may still stop for:

```text
validated result
terminal/non-repairable ownership
shared hard-budget exhaustion
validator/tool error
configured attempts exhausted
```

No new no-progress heuristic is introduced.

## Evidence and compatibility

The requested/effective Testbench and Candidate repair counts are persisted in
the source request. Existing repair artifacts continue to record every attempt
and its safe Prompt manifest. The Hidden boundary, dual generation profiles,
ABI freeze and Stub ownership contracts remain unchanged.

## Status

```text
STEP_E=completed
ACTIVE_STEP=F
NEXT_STEP=F_DUAL_MODE_REAL_DFS_ACCEPTANCE
MODEL_API_CALLED=false
VITIS_RUN=false
P0_STATUS=active_retry_required
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
DEFAULT_LLM_CALLS=32
```

## Current product-contract override

The values above are the historical Step-E acceptance snapshot. The current
source-only and advanced compatibility contract supersedes only the ceiling:

```text
Testbench repair default = 3
Candidate repair default = 3
current valid user range = 1..20
current system safety ceiling = 20
```

The attempt semantics, hidden-boundary rules and bounded-loop behavior remain
unchanged. Current authority is documented in
[`CLI_PARAMETER_REFERENCE.md`](../guides/CLI_PARAMETER_REFERENCE.md).
