# R5 Oracle Dataset V2 Plan

Date: 2026-09-19

Status: frozen and zero-call audited; ready for bounded history acquisition

This plan supersedes, but does not overwrite, the v1 oracle-adapter plan. It
uses checked-in HLSRewriter cases rather than synthetic calibration fixtures.
The checked-in `kernel.cpp` is the reference program, and each Public/Hidden
adapter compares the reference top with the generated Candidate top. Expected
outputs are therefore not invented.

The predeclared split is:

- history positive: `Exception_E2_Filter`;
- history positive: `Recursive_E2_DFS`;
- future positive: `Exception_E3_Turbo_Encoder`;
- future inapplicable/confusable control: `Pointer_E3_double_pointer`.

All positive cases are predeclared as `unsupported_construct`, the only
failure class covered by the accepted legacy R2 calibration certificate. A
positive case is allowed to proceed only if real deterministic evidence reaches
the existing unknown/mixed review boundary. The predeclaration is not a claim
that a future generated Candidate will fail, that R2 will classify it, or that
R4 will repair it.

The adapter freeze and protocol audit remain zero-call operations. They must
verify source-level holdout, distinct Public/Hidden oracle identities, clean
reference-source host syntax, certificate scope, and the 500/500 global budget
before history acquisition is allowed. Future outcomes remain forbidden until
two independently validated history episodes have been reduced and the
history-only snapshot has been frozen.

This document does not create a Trusted revision, authorize a real campaign,
accept R5, or start R6.

## Frozen Evidence

The immutable adapter manifest is
`/data/agrefactor_runs/r5_p2_adapter_freeze_v2/oracle_adapter_manifest.json`.
Its file SHA-256 is
`14d1eb11c2397df99b42b849c6f0341f7cdd1b6c7cc86f4e7b4b1edca4102a7f`,
and its internal manifest SHA-256 is
`c8a5d6c743f30e52fad9c4ca407cfa3920880215d9d3bcf1f0194bc41e855f6f`.

The independent zero-call protocol audit is
`/data/agrefactor_runs/r5_p2_oracle_protocol_audit_v2/protocol_audit.json`.
Its file SHA-256 is
`0a5434805c07d1b3be9e165de6048a9a618ef78f608ebb03e1531c0b8968a0d6`,
and its internal protocol-audit SHA-256 is
`1f3a70a8a8fd42a852dce51042343103e19d24e29cb911c440dcfd80ad7551b8`.
The audit status is `ready_for_history_acquisition`; it still sets
`real_campaign_allowed=false` and `trusted_revision_creation_allowed=false`.

History acquisition is restricted to A2 through the existing `refactor`, R2,
and R4 path. Up to three attempts per history source are allowed, with a
predeclared upper bound of 18 Provider calls and 36 Vitis launches. Future
files and outcomes remain forbidden until the history episodes, lifecycle
reduction, snapshot, and memory payload have been frozen and independently
audited.
