# V2.3 R4 Gate-authorized Candidate repair implementation

Status: implementation complete; pending external validation and independent acceptance.

This implementation adds `agrefactor.recovery.gated_candidate_repair` as an
explicit, opt-in controller. It does not replace the existing deterministic
Candidate repair loop, validation FSM, Testbench repair lane, optimizer, or
`best_correct` handling. The runtime integration exposes only a lazy builder;
the default R0-R3 path never imports or invokes R4.

## Implemented contract

The controller requires a typed R4 authorization whose ID is a canonical
SHA-256 over run/event/advisory, Gate contract, Trusted revision, canary,
Candidate-before hash, policy/ledger reservation and preserved terminal route.
It requires an operator-enabled exact-identity canary, agent-safe complete
physical evidence, a non-authoritative R2 advisory, Gate `accept` for a
`Trusted` revision, and the existing `RecoveryPolicy`/`RecoveryLedger`.

The provider callback is invoked at most once and only after a fresh kill-switch
read. The mutation callback may produce only one changed Candidate. The
validation callback must report a fresh full-prefix result; an independent
auditor callback must also return clean before `verified_positive` is possible.
Testbench hashes are compared before accepting a validation result. Any kill,
identity, validation, policy, budget, or audit conflict is represented as a
non-positive outcome and cannot mutate a revision or success pointer.

`R4Outcome.VERIFIED_POSITIVE` is an evidence classification, not model or
controller authority. A formal validator plus independent auditor establish
the positive result. R4 remains Candidate-only and one-attempt bounded.

## Not implemented by this phase

No provider is called by package application, no Vitis run is fabricated, and
no real canary is enabled. The external-validation package must later supply a
frozen fair-baseline manifest, exact implementation hashes, real provider/Vitis
counters, full-prefix evidence, and independent audit receipts. R3 temporal
memory efficacy remains owned by the V2.3-R5 paired evaluation.
