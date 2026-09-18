# R5 Oracle Dataset V2 Plan

Date: 2026-09-19

Status: predeclared before R5 outcome observation; not campaign admission

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
