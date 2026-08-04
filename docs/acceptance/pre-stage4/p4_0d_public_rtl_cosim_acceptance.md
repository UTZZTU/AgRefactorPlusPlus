# P4-0D Public RTL COSIM Acceptance

## Accepted state

```text
P4_0D_PUBLIC_RTL_COSIM_IMPLEMENTED=true
P4_0D_PUBLIC_RTL_COSIM_ACCEPTANCE=accepted_real_vitis
P4_0D_UNIFIED_STAGE_ORDER=preflight_public_native_csim_csynth_public_rtl_cosim_hidden
P4_0D_COSIM_POLICY_DEFAULT=required
P4_0D_COSIM_DEFAULT_TIMEOUT_S=900
P4_0D_COSIM_TIMEOUT_SAFETY_CEILING=7200
P4_0D_COSIM_REPAIR=false
P4_0D_TESTBENCH_OUTCOME_TRANSPORT=argv
P4_0D_CACHE_PIPELINE=prestage4-public-rtl-cosim-v1
P4_0D_FOCUSED_TESTS=7
P4_0D_FULL_REGRESSION=2096
P4_0D_REAL_VITIS_SMOKE=accepted
P4_0D_NETWORK_LLM_USED=false
P4_0D_ACCEPTED_RUN_ID=p4_0d_public_rtl_cosim_v16_20260804T064831Z_1709639
P4_0D_ACCEPTED_ARTIFACT_ROOT=/data/agrefactor_runs/p4_0d_public_rtl_cosim_v16_20260804T064831Z_1709639
P4_0D_ACCEPTED_COMMIT=b543604cd311eab4380987b09447842542e3214b
P4_0D_V16_PACKAGE_SHA256=338c504c29f15df2de7fc22ff42888c91dac41858eb5430da9cecf0c163a2704
P4_0D_ACCEPTANCE_ARCHIVE_SHA256=9e041d9dbafe225d71910f06e65b4ee4fadd69dd348801a7caa475fcd654deb6
P4_0D_REPOSITORY_CLOSURE=accepted
STAGE4_ALLOWED=false
NEXT_IMPLEMENTATION_PACKAGE_AT_ACCEPTANCE=P4-0E
```

This document records the accepted P4-0D result. The implementation commit is
`b543604cd311eab4380987b09447842542e3214b` on `stage2-general-feedback`; local and
`origin/stage2-general-feedback` were verified equal after push.

## Accepted implementation

The shared qualification order is:

```text
Preflight
-> Public native Vitis CSIM
-> CSYNTH
-> Public RTL COSIM
-> Hidden
```

Public RTL COSIM has a real physical Vitis HLS 2023.2 invocation, hard
`max_cosim_calls`/`cosim_calls` accounting, prospective launch checks,
`cosim_timeout_s`, typed fail-closed evidence, unknown-safe ownership,
Public/Hidden isolation, cache and execution-identity invalidation, and shared
Refactor/Optimize/Full wiring.

A Public COSIM failure terminates the current Candidate. It cannot enter
Candidate repair, Testbench repair, bounded Optimize Candidate recovery, or any
model-visible prompt. An outer Optimize search may still preserve
`best_correct` and create a new Candidate under its independent policy and
remaining budget; that is not repair of the failed COSIM Candidate.

## Accepted evidence

The target-host run at `/data/agrefactor_runs/p4_0d_public_rtl_cosim_v16_20260804T064831Z_1709639` proved:

```text
focused_tests=7/7
full_regression=2096/2096
full_regression_errors=0
full_regression_failures=0
preflight=passed
public_native_vitis_csim=passed
csynth=passed
public_rtl_cosim=passed
hidden=passed
real_vitis_used=true
actual_vitis_version=2023.2
network_llm_used=false
final_scope_verified=true
```

The Public RTL COSIM typed result recorded physical launch, no timeout, return
code zero, immutable evidence, `status=passed`, `failure_owner=none`, and
`reason_code=cosim_passed`.

## Vitis 2023.2 testbench outcome transport

The typed outcome path is passed to the self-checking C/C++ testbench through
the documented `csim_design -argv` and `cosim_design -argv` interfaces. It is
not encoded as a C preprocessor string macro. Native Public CSIM remains
compatible when no argument is supplied. A claimed COSIM pass requires a fresh
exact-schema typed outcome in addition to successful physical execution.

## Claim boundary

This acceptance is model-independent and does not claim arbitrary-kernel,
multiple-Vitis-version, stable model-quality, or stable PPA superiority. It does
not implement P4-0E through P4-0I and does not permit Stage 4.
