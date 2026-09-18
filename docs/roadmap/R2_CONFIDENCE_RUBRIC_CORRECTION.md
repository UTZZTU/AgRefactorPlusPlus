# V2.3 R2 confidence rubric correction

Status: implementation correction complete; real provider calibration is
pending.

## Reason

The R2 shadow output contract constrained `confidence` to `low`, `medium`, or
`high`, but did not define the evidence conditions for those labels. A model
could therefore apply its own unstable interpretation even though V2.3
forbids consuming self-reported `high` directly at the R4 boundary.

## Frozen semantics

Confidence describes evidence support for the diagnostic owner, failure
class, citations, and repair scope. It does not predict repair success and
does not grant mutation authority.

- `high`: every selected field is directly supported by cited, specific
  diagnostic items; one owner and failure class are supported without a
  conflicting or equally plausible attribution. `candidate_only` also
  requires direct Candidate-side evidence and cannot be inferred from an
  aggregate failure alone.
- `medium`: specific evidence supports a leading diagnosis, but an alternative
  attribution remains plausible or discriminating evidence is incomplete.
- `low`: a non-abstaining diagnosis is only a tentative hypothesis supported
  by weak, indirect, or aggregate evidence.
- `abstain`: no non-unknown owner and class can be supported by in-scope
  citations, only an aggregate failure is available, material evidence
  conflicts, or the strict output contract cannot be satisfied.

The prompt explicitly forbids maximizing confidence to satisfy downstream
eligibility. Evidence completeness alone does not imply `high`.

## Contract and authority boundary

The prompt/output contract version advances from `r2-shadow-output-v2` to
`r2-shadow-output-v3`. Any calibration certificate or advisory bound to v2
must fail verification after this correction. The JSON output schema and
strict parser remain otherwise unchanged.

R2 remains shadow-only and default-off. A v3 `high` remains an untrusted model
label until a frozen real calibration split and acceptance policy produce an
accepted content-addressed certificate. R3 Gate, RecoveryPolicy, budget,
ledger, full revalidation, and independent audit remain required after that
certificate check.

## Next evidence

The next package must freeze its split and policy before real provider calls,
measure coverage, selective risk, citation validity, high-confidence error,
and unsafe scope with confidence intervals, and issue no certificate when the
pre-registered conditions are not met. Package D held-out cases must remain
separate from calibration.
