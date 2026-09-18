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

## Verification

- Deterministic R5 and adjacent boundary tests: passed.
- Full repository regression: `2450` tests passed.
- Deterministic implementation work consumed `0` Provider calls and `0`
  Vitis launches.

## Current Data Boundary

The immutable predecessor inventory reports `64` historical Provider calls and
`180` historical Vitis launches. They are predecessor evidence, not R5 budget
consumption. The R5 ledger remains `0/500` and `0/500`.

The current inventory is not a campaign dataset: it has no frozen history/
future partition and no paired positive/inapplicable controls. The generated
`r5_dataset_contract.py` output therefore stays
`blocked_data_acquisition`, does not create a Trusted revision, and forbids a
real campaign. Historical reports or Package D canaries must not be relabeled
as an R5 future set.

## Next Authorized Step

Supply or identify a verifiable case manifest with complete source, Public /
Hidden provenance, Target, toolchain, parser, model/prompt identity, temporal
partition, source-level holdout, and positive/inapplicable controls. Re-run the
inventory and dataset contract, then perform a zero-call protocol audit before
the bounded real pilot. R6 remains stopped.
