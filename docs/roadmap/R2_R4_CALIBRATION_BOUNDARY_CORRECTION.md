# V2.3 R2-to-R4 calibration boundary correction

Status: implementation correction complete; real calibration certificate and
revised R4 external validation are pending.

## Observed evidence

Package D v1.3.2 ran three real Vitis 2023.2 canaries. Vitis emitted exact
`HLS 214-194` diagnostics for undefined `operator new[]` and `operator
delete[]`, followed by aggregate `HLS 214-135 Syn check fail!` diagnostics.
The deterministic CSYNTH parser classified all three records as
`unknown_fallback`, while the R2 advisor consistently identified unsupported
dynamic allocation with medium confidence. R4 correctly abstained, so no R4
mutation provider or formal revalidation ran.

The run exposed two separate contract defects:

1. `HLS 214-194` is stable candidate-owned evidence and belongs in the
   deterministic parser and existing Candidate repair path. It is not an
   appropriate unknown/review fixture for an R4 canary.
2. The R4 consumer tested the model's self-reported `confidence == high`
   directly. R2 had a frozen calibration evaluator, but no content-addressed
   calibration certificate was verified at the R2-to-R4 boundary. This
   contradicted the V2.3 requirement that model-reported confidence alone must
   never enter the Gate.

## Implemented correction

- Recognize only the reviewed `HLS 214-194` undefined `new[]`/`delete[]`
  forms as candidate-owned `unsupported_construct` evidence. Other message
  IDs and unreviewed forms remain fail-closed unknowns.
- Treat only the exact `HLS 214-135 Syn check fail!` form as an aggregate
  source-synthesis failure, suppressing it only when a specific blocking
  diagnostic is present.
- Extend the existing R2 calibration reducer with high-confidence sample and
  error counts.
- Add a pre-frozen calibration acceptance policy, content-addressed
  certificate, and runtime verification result. The certificate binds the
  frozen split/report/policy, provider and model identity, input contract,
  output contract and strict parser.
- Require R4 to verify the certificate before the Applicability Gate. A
  missing, rejected, mismatched or confidence-inapplicable certificate
  abstains before mutation.
- Require every Trusted repair revision to cite calibration evidence, and
  require the selected revision to cite the active certificate.
- Distinguish a missing eligible R2 event from multiple eligible R2 events.

## Preserved boundaries

The correction adds no CLI, orchestrator, Candidate loop, Vitis executor,
auditor or success authority. `refactor`, `optimize` and `full` remain the only
normal product entrypoints. R2 remains shadow-only and default-off; R4 remains
candidate-only, canary-gated, one-attempt and default-off. Provider and Vitis
execution are not part of the deterministic application package.

## Required next evidence

Before Package D resumes, a separate executable validation package must freeze
an identity-complete calibration split and policy before provider execution,
run the existing R2 advisor, issue a certificate only if all thresholds and
confidence intervals pass, and independently audit the certificate. The next
R4 canary must use a genuine unknown/review diagnostic rather than
`HLS 214-194`, cite the accepted certificate from its Trusted revision, and
retain all existing privacy, provenance, budget and full-revalidation gates.
