# Pre-Stage-3 Bridge

## Status

Stage 2 remains closed. Stage 3 is allowed but has not started. Before the
optimizer is implemented, the following bridge items must be solved one by one:

```text
P0 Real DFS end-to-end acceptance
P1 Adaptive Model Profile Registry
P2 Source-only Refactor Bootstrap Contract
P3 Test Qualification Contract
P4 Provided / Auto / Hybrid test policy
P5 User-facing quiet output and verbosity policy
```

## Real DFS evidence so far

### Failure A: model/framework parameter mismatch

```text
requested reasoning_effort=max
installed AutoGen accepted=xhigh
normalization=max -> xhigh
```

This is evidence for P1: a model profile must cover provider-native values,
framework aliases, supported parameters and response behavior. A model's
self-report is advisory; empirical probes remain authoritative.

### Failure B: generated testbench/stub protocol

The stopped run:

```text
/data/agrefactor_runs/pre_stage3_real_dfs_end_to_end_v2_20260720_145405
```

proved three defects:

1. a delegating stub called the original function without a forward declaration
   although it was compiled as a separate translation unit;
2. later coverage rounds regenerated the testbench but reused the broken
   first-round stub;
3. empty/malformed responses could enter the C++ extraction path.

The feature commit `53045b4cdc6c262e0be5cdcddedae0d302908812` hardens these paths:

```text
strict single fenced C++ artifact
empty/commentary/prompt-echo rejection
one bounded format retry
original forward-declaration injection
testbench + matching stub regeneration per round
repeated identical failure early stop
qualified-only Public/Hidden selection
operator-side per-round debug artifacts
max -> xhigh reasoning normalization
```

Forensic artifacts:

```text
/data/agrefactor_runs/pre_stage3_tb_generation_hardening_v4_20260720_164057/acceptance
```

## Next real DFS rerun

The next real run uses `deepseek-v4-flash` first. It must not claim success
until all of the following pass:

```text
source-only model bootstrap
qualified test generation
independent reference pass
negative-control detection
formal repair-aware Preflight
real Vitis HLS 2023.2 CSYNTH
qualified Public CSIM
qualified Hidden CSIM
no Hidden/credential leakage
```

## User-facing output policy

Acceptance scripts may retain detailed logs during development, but normal user
commands must default to concise phase/status output. Full unit-test lines,
agent transcripts and tool logs belong in run artifacts. A later CLI milestone
must provide explicit `--verbose` / `--debug` controls rather than printing
hundreds of `... ok` lines by default.
