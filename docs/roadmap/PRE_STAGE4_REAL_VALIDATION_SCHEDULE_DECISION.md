# Pre-Stage-4 Real Validation Schedule Decision

> **Status:** frozen.
>
> **Parent commit:** `fd95204e6702649de662804754e64e96fb5edad4`.
>
> This record clarifies when real committed samples, real Vitis execution and
> real network-model calls become required evidence across P4-0C through P4-0H.

## Decision

```text
P4-0C → real native Vitis CSIM, model-independent acceptance core
P4-0D → real CSIM/CSYNTH/COSIM/Hidden, model-independent acceptance core
P4-0E → first post-hardening real network-model smoke
P4-0F → measured real runs for budget defaults and Full reserves
P4-0G → targeted real network-model Optimize/Full smoke
P4-0H → formal repeated multi-kernel network-model + Vitis matrix
```

## Why the schedule is split

A package should first prove the behavior it introduces without mixing
unrelated stochastic failures into its authority boundary.

P4-0C and P4-0D introduce new tool stages and ordering. Their acceptance must
therefore isolate real Vitis invocation, stage evidence, timeout, ownership,
cache and budget behavior. A deterministic or fixed Candidate is allowed for
that purpose.

P4-0E changes provider defaults, credential loading and reasoning policy. It is
the first point at which the post-hardening branch must call the selected real
network endpoint and prove safe configuration evidence.

P4-0F needs measured real consumption rather than invented budget
multiplication. P4-0G is the first targeted proof that a real network model can
diagnose and generate an Optimize/Full Candidate that enters the complete
qualified pipeline.

P4-0H remains the only authoritative repeated multi-kernel closure matrix. It
must not be the first post-hardening network call, and earlier smoke success
must not be promoted into a general stability or PPA-superiority claim.

## Evidence boundaries

All real runs must retain:

- exact repository commit and immutable artifacts;
- exact selected model, endpoint and API-key environment-variable name;
- shared hard budget usage;
- real Vitis command/version/target evidence;
- Public/Hidden provenance and Hidden suppression;
- typed failures and infrastructure-failure accounting;
- no secret value, `.env` contents or private reasoning persistence.

Historical Stage 3 real evidence remains historical evidence for its original
contracts. It does not close any P4-0C through P4-0H acceptance requirement.

## Next implementation package

```text
NEXT_IMPLEMENTATION_PACKAGE=P4-0C_PUBLIC_NATIVE_VITIS_CSIM
```
