# P4-0F-R5-E-R1 Decision Record

## Status

```text
state=next_implementation_package
behavior_base=0ca5dd99fabec1c2c003446975e28128a0926c52
R5_D=accepted
R5_E=not_accepted
P4_0F_COMPLETE=false
STAGE4_ALLOWED=false
```

## Evidence that opened R5-E-R1

R5-E v2 launched all five no-model cases. Baseline and both hybrid Testbench-recovery cases passed. The two Candidate-recovery cases safely stopped at unknown ownership.

The first root cause is Native CSIM phase misclassification: compile/link succeeded and simulation returned nonzero, but the lower adapter reported Testbench compile failure.

The second root cause is stale COSIM typed evidence: a C pre-check pass remained after the RTL post-check failed.

Neither case produced false acceptance or an unsafe repair.

## Frozen scope

- repair the existing CSIM adapter and COSIM typed transport;
- retain the existing FeedbackRouter, ValidationStateMachine, RecoveryPolicy, RecoveryLedger, and complete restart semantics;
- add historical replays and negative counterexamples;
- rerun the complete campaign after a new checkpoint;
- block the real provider case until the no-model gate passes.

## Non-goals

- no second router, state machine, or recovery policy;
- no canary-specific product behavior;
- no string/regex authority gate;
- no Hidden recovery;
- no P4-0F budget freeze;
- no dynamic-v1 or P4-0H work.
