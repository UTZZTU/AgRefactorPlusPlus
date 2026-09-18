# V2.3 R5 Implementation Progress

Date: 2026-09-19

This document records implementation progress only. It does not accept R5 and
does not authorize R6. The original V2.3 roadmap and frozen R5 design remain
authoritative.

## Completed Deterministic Work

- Added append-only R5 episode envelopes, lifecycle reduction, history-only
  snapshot construction, candidate-only payloads, discriminated research
  authorization, and the 500/500 budget ledger.
- Added the paired A0-A6 campaign protocol, counterbalanced runner, source/time
  holdout checks, arm reducer, and evidence inventory.
- Connected the internal profile seam to the existing `refactor` phase. The
  ordinary `refactor`, `optimize`, and `full` entrypoints remain the only
  product entrypoints; the profile is not a CLI option.
- Added a typed R5 runtime binding. A0/A1 cannot carry mutation bindings;
  A2/A3 require the appropriate authorization and existing R4 factory; A4-A6
  require snapshot and payload-manifest equality before snippets reach the
  existing Candidate prompt hook.
- R5 arms bypass the ordinary post-diagnostic Candidate loop. A0/A1 are
  observation-only; A2-A6 hand the unchanged main result to the existing R4
  controller, preserving the one-mutation and formal-validation boundary.
- Added a private typed capture of one existing `refactor` baseline and a
  product campaign executor that forks A0-A6 from that exact Candidate,
  diagnostic context, task, and formal result. Baseline usage is charged once;
  every arm receives an isolated BudgetManager, TraceRecorder, model adapter,
  validation workspace, and append-only artifact root.
- Corrected the conservative Vitis upper bound to count the common baseline's
  complete csim/csynth/cosim prefix. The runner now rejects work in A0, Vitis
  work in A1, and any arm that crosses its frozen Provider/Vitis upper bound.

## Verification

- Deterministic R5 and adjacent boundary tests: passed.
- Full repository regression: `2515` tests passed on the worktree that became
  implementation commit `169e64b4c0f24b16292eaa7125357ac8071bdfef`.
- This deterministic checkpoint consumed `0` new Provider calls and `0` new
  Vitis launches. The credential was removed from the test environment.

## Current Data Boundary

The immutable predecessor inventory reports `64` historical Provider calls and
`180` historical Vitis launches. They are predecessor evidence, not R5 budget
consumption. Full-regression output includes
`P4_0E_R1_REAL_NETWORK_SMOKE_PASSED`, but the only discovered caller is a unit
test that replaces the provider client with an in-process fake and supplies a
literal `unit-secret`; it does not cross the Provider boundary. The reconciled
R5 ledger is therefore `0/500` Provider calls and `0/500` Vitis launches.

The current inventory is not a campaign dataset: it has no frozen history/
future partition and no paired positive/inapplicable controls. The generated
`r5_dataset_contract.py` output therefore stays
`blocked_data_acquisition`, does not create a Trusted revision, and forbids a
real campaign. Historical reports or Package D canaries must not be relabeled
as an R5 future set.

The deterministic HLSRewriter source audit now covers all 20 checked-in roots.
It found 0 directly admissible cases, 13 cases that require a separately
reviewed Public/Hidden adapter, and 7 rejected cases. The common deficiencies
are implementation-including testbenches, missing process-failure oracles,
missing distinct Public/Hidden suites, and Tcl flows that run only synthesis.
The immutable audit is
`/data/agrefactor_runs/r5_p2_hlsrewritter_audit/hlsrewritter_case_audit.json`
with file SHA-256
`5b0bdf3c074cda2b73b43c80032413c809c1528c18b15b7c3dcfbe2f92be3cfb`.
No Provider or Vitis call was made. An `adapter_required` classification is
not campaign admission and does not authorize creating an oracle.

The reviewed adapter-boundary pass records all 13 adapter-required cases and
keeps all 13 blocked. It references only existing source/reference/testbench/
Tcl hashes, leaves history/future and control roles unassigned, and explicitly
forbids invented expected outputs, duplicated Public/Hidden tests, or manual
Trusted revisions. The immutable review is
`/data/agrefactor_runs/r5_p2_adapter_review/oracle_adapter_review.json` with
file SHA-256
`8db47e2243c6640f515b215fd4cd035cf5d8cd22ef137c9ceb96edb32a23b1ab`.
Its canonical internal review hash is
`59d1f7b2f64cba5994521fa94f19e529aedd715a91858c11174da73ad2f73768`.
It consumed zero Provider and zero Vitis calls.

## Next Authorized Step

Implement reference-backed adapters only where existing repository artifacts
can produce distinct, enforceable Public/Hidden checks without inventing an
expected outcome or weakening failure semantics. Rejected cases remain outside
the dataset. Freeze history/future and paired control roles before observing
new outcomes, and only after adapter identity, complete Target/toolchain/
parser/model identity, temporal provenance, and source-level holdout are
proven. Re-run the inventory and dataset contract, then perform a zero-call
protocol audit before the bounded real pilot. R6 remains stopped.

## Calibration-Scope Correction

A later zero-call review found that the accepted schema-v1 R2 certificate was
statistically established only for `unsupported_construct`, while its runtime
shape exposed confidence labels without an explicit failure-class scope. The
consumer now treats every legacy schema-v1 certificate as
`unsupported_construct`-only. New schema-v2 certificates bind an explicit
failure-class list, and R4 rejects an advisory outside that list.

The v1 oracle-adapter plan was re-audited with this rule and a host syntax
preflight. The superseding audit status is `blocked_history_acquisition` at
`/data/agrefactor_runs/r5_p2_oracle_protocol_audit_superseding_v2`; its
`protocol_audit.json` file SHA-256 is
`3a0b3eff7c515e4f372c01424ce613912189fc810e172597f87c57f366f9aae6`.
The old audit remains immutable but no longer authorizes the next step. This
correction consumed zero Provider calls and zero Vitis launches.

## Calibration-Compatible V2 Dataset Boundary

The v2 plan freezes two real history positives (`Exception_E2_Filter` and
`Recursive_E2_DFS`) and a disjoint future set containing one positive and one
inapplicable-or-confusable control. All positive cases are inside the accepted
legacy certificate's narrow `unsupported_construct` scope. Public and Hidden
adapters compare the generated Candidate against the checked-in reference
source; no expected result was invented.

The adapter freeze and independent protocol audit passed without Provider or
Vitis calls. The authoritative file hashes are recorded in
`V2_3_STATE.json`. The audit status is `ready_for_history_acquisition`, while
`R5_REAL_CAMPAIGN_ALLOWED` remains false.

The history driver calls the ordinary `refactor` entrypoint internally, then
uses the real Provider-backed R2 advisor and existing R4 controller in A2. It
does not add a product CLI or a second Vitis path. Runtime context signatures
are bound only after the real baseline produces its deterministic diagnostic;
they are not guessed from the benchmark.

The provided-Testbench baseline contains six identifier calls, one
deduplicator, one planner, and one refactoring call. Therefore its frozen
Provider upper bound is 9, not 1. An A2 history attempt adds at most one R2
advisory and one R4 mutation, for 11 Provider calls and 8 Vitis launches per
attempt. The predeclared acquisition upper bound is consequently 66 Provider
calls and 48 Vitis launches for up to three fail-closed attempts on each of two
sources.

The first real history run at
`/data/agrefactor_runs/r5_p3_history_real_0a2865e_run1` exposed the old budget
error before any Vitis launch. Three attempts each consumed four Provider calls
and then failed closed at the fifth logical launch. The authoritative R5 ledger
therefore records 12 Provider calls and 0 Vitis launches. The old zero-call
protocol audit is retained as immutable evidence but is superseded for budget
authorization; a corrected zero-call audit is required before retrying history.

The corrected zero-call audit passed at
`/data/agrefactor_runs/r5_p2_oracle_protocol_audit_v3_budget_corrected_6e523d6`.
Its `protocol_audit.json` file SHA-256 is
`0b27dd5a7e576a9893346468ff2fd8622ba9b63c345c071c430cea4f2e306fa7`.
It carried the reconciled `12/0` ledger and froze one-case pilot bounds at
`60/54` and two-case formal bounds at `120/108` Provider/Vitis launches. A
subsequent real baseline proved that this Vitis model was still incomplete:
the ordinary provided-Testbench path launches Public csim, csynth, Public
cosim, and Hidden csim, for four launches per complete validation. Three
successful baselines consumed `27/12` before the stale three-launch campaign
guard failed closed. The cumulative R5 ledger is therefore `39/12`; the v3
audit is retained but superseded, and the corrected bounds are `60/72` for a
one-case pilot and `120/144` for the two-case formal campaign.
