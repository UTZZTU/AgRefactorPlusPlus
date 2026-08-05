# P4-0D-R1 COSIM Integration Contract Correction Acceptance

## Verdict

```text
P4_0D_R1_COSIM_INTEGRATION_CORRECTION=accepted_real_vitis
P4_0D_R1_BEHAVIOR_COMMIT=2132cfd00323f7c217bf13258b11ba87480341ab
P4_0D_R1_ACCEPTED_RUN_ID=p4_0d_r1_cosim_correction_acceptance_20260805T055822Z_3491101
P4_0D_R1_FOCUSED_TESTS=9/9
P4_0D_R1_FULL_REGRESSION=2117/2117
P4_0D_R1_REAL_VITIS_CSIM_PREREQUISITE=passed
P4_0D_R1_REAL_VITIS_CSYNTH_PREREQUISITE=passed
P4_0D_R1_REAL_VITIS_RTL_COSIM=passed
P4_0D_R1_ORDINARY_PUBLIC_TESTBENCH_ADAPTER=true
P4_0D_R1_TYPED_PASS_AFTER_TESTBENCH_ZERO=true
P4_0D_R1_RETURNCODE_ALONE_SUFFICIENT=false
P4_0D_R1_FAILURE_OWNER_INFERRED=false
P4_0D_R1_HIDDEN_EXPOSED=false
P4_0D_R1_PRODUCT_SUMMARY_PUBLIC_COSIM_TRUTHFUL=true
P4_0D_R1_HIDDEN_NOT_RUN_TRUTHFUL=true
NEXT_IMPLEMENTATION_PACKAGE=P4-0F
PRE_STAGE4_HARDENING_IMPLEMENTATION_COMPLETE=false
STAGE4_ALLOWED=false
```

## Accepted correction

P4-0D's strict three-way Public RTL COSIM acceptance remains fail-closed: the
physical tool must return zero, the Tcl command-status evidence must report a
COSIM pass, and the typed outcome must report a pass. Tool return code alone is
still insufficient.

The producer side is now connected by a deterministic, non-model adapter. It
renames the ordinary Public Testbench `main`, invokes it through a controlled
wrapper, and writes typed pass evidence only after that Testbench returns zero.
A nonzero Testbench result never writes typed pass. Missing, malformed,
contradictory, timeout, or nonzero evidence remains rejected or review-required,
and the correction never infers a failure owner.

Hidden suites, Hidden source, and Hidden diagnostics remain outside Public
COSIM and model-visible input. Product output now distinguishes Public C-level
validation from Public RTL COSIM and reports an unexecuted Hidden suite as
`not_run` rather than `failed`.

## Invalidated measurement boundary

The earlier P4-0F 0/4 matrix is retained only as diagnostic evidence. Every run
passed Public native Vitis CSIM and CSYNTH, then reached Public RTL COSIM with a
zero tool return code and failed solely because the producer side of the typed
outcome contract was absent. It is not valid budget-freezing evidence. P4-0F
must be remeasured from the accepted P4-0D-R1 authority commit.

## Claim boundary

This correction does not establish arbitrary-kernel success, stable model
quality, PPA superiority, completion of Pre-Stage-4, or permission to enter
Stage 4.
