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
- Full repository regression: `2494` tests passed at implementation commit
  `75a4b49c4e5256f5a7ff36c2adc455d47851dba6`.
- This deterministic checkpoint consumed `0` new Provider calls and `0` new
  Vitis launches. The credential was removed from the test environment.

## Current Data Boundary

The immutable predecessor inventory reports `64` historical Provider calls and
`180` historical Vitis launches. They are predecessor evidence, not R5 budget
consumption. One earlier full-regression invocation unintentionally reached a
legacy conditional network smoke after the R5 authorization started. It is
therefore charged to R5 even though it was not an efficacy experiment. The
reconciled R5 ledger is `1/500` Provider calls and `0/500` Vitis launches.

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

## Next Authorized Step

Design and review adapters only for the 13 `adapter_required` cases. An
adapter may expose an existing oracle but must not invent expected outcomes or
weaken failure semantics. Rejected cases remain outside the dataset. Freeze
history/future and paired control roles only after adapter identity, complete
Target/toolchain/parser/model identity, temporal provenance, and source-level
holdout are proven. Re-run the inventory and dataset contract, then perform a
zero-call protocol audit before the bounded real pilot. R6 remains stopped.
