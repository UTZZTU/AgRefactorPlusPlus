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
