# R5 Calibration Scope Correction

Date: 2026-09-19

Status: implemented and regression tested; R5 remains unaccepted

This correction closes a contract gap found before R5 history acquisition.
The accepted R2 calibration campaign used only the
`unsupported_construct` truth class, but the schema-v1 certificate encoded
only confidence labels. A consumer could therefore have treated calibrated
`high` confidence as portable to an uncalibrated failure class.

The correction preserves the accepted certificate ID and evidence. A
schema-v1 certificate now has the narrow, explicit legacy meaning
`unsupported_construct` only. Schema-v2 certificates list their calibrated
failure classes. R4 verification rejects an advisory when its failure class
is outside the certificate scope. The independent calibration auditor derives
and verifies the schema-v2 scope from truth records, and rejects a schema-v1
bundle that contains any other class.

R5 adapter manifests now predeclare the expected R2 failure class, expected
unknown/mixed review boundary, and whether deterministic repair is expected.
The zero-call protocol auditor binds those declarations to the accepted
certificate and performs a host syntax preflight. A positive case that is
outside the calibration scope, cannot reach the R2 review boundary, is already
classified for deterministic repair, or is not host-preflight clean cannot
authorize history acquisition.

The earlier v1 adapter plan was audited again without Provider or Vitis calls.
The superseding audit is stored at
`/data/agrefactor_runs/r5_p2_oracle_protocol_audit_superseding_v2`.
Its protocol status is `blocked_history_acquisition` and its file SHA-256 is
`3a0b3eff7c515e4f372c01424ce613912189fc810e172597f87c57f366f9aae6`.
It does not overwrite the earlier audit, create a Trusted revision, authorize
a real campaign, accept R5, or start R6.

The next step is to freeze a superseding real-data plan whose positive cases
are within the accepted `unsupported_construct` scope and can legitimately
reach the existing R2 unknown/review boundary.
