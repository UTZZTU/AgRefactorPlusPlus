# P4-0D Authority-State Synchronization Acceptance

> **Scope:** documentation authority synchronization only.
>
> **Behavior change:** none.
>
> **Authoritative implementation parent:** `b543604cd311eab4380987b09447842542e3214b`.

## Verdict

```text
P4_0D_AUTHORITY_STATE_SYNCHRONIZED=true
P4_0D_PUBLIC_RTL_COSIM=accepted_real_vitis
P4_0D_ACCEPTED_COMMIT=b543604cd311eab4380987b09447842542e3214b
P4_0D_ACCEPTED_RUN_ID=p4_0d_public_rtl_cosim_v16_20260804T064831Z_1709639
P4_0D_FULL_REGRESSION=2096
P4_0D_FOCUSED_TESTS=7
P4_0D_REAL_VITIS_SMOKE=accepted
P4_0D_NETWORK_LLM_USED=false
P4_0D_REPOSITORY_CLOSURE=accepted
PRE_STAGE4_HARDENING_IMPLEMENTATION_COMPLETE=false
STAGE4_ALLOWED=false
NEXT_IMPLEMENTATION_PACKAGE=P4-0E
```

## Reconciled authority surfaces

This package synchronizes six documentation surfaces:

1. The hardening-contract header states that implementation is accepted through
   P4-0D while full Pre-Stage-4 closure remains incomplete.
2. `PROJECT_STATE.md`, the current-state single entry point, records the exact
   2096-test P4-0D baseline, accepted real Vitis run, accepted commit, repository
   closure, and P4-0E as the current next behavior package.
3. The P4-0D acceptance records actual focused/full counts, run ID, artifact
   root, package/archive hashes, accepted commit, and repository closure rather
   than future requirements.
4. The P4-0C acceptance labels its P4-0D handoff as
   `NEXT_IMPLEMENTATION_PACKAGE_AT_ACCEPTANCE` so it remains historical.
5. The frozen real-validation schedule labels its P4-0C handoff as
   `NEXT_IMPLEMENTATION_PACKAGE_AT_DECISION` so it is not mistaken for current
   project state.
6. This acceptance records the documentation-only synchronization boundary.

## Evidence relied upon

No product code, tests, Vitis invocation, provider call, budget behavior, or
runtime artifact is changed by this synchronization. It relies on already
accepted immutable evidence:

```text
accepted_behavior_commit=b543604cd311eab4380987b09447842542e3214b
accepted_run_id=p4_0d_public_rtl_cosim_v16_20260804T064831Z_1709639
focused_tests=7/7
full_regression=2096/2096
real_preflight=passed
real_public_native_vitis_csim=passed
real_csynth=passed
real_public_rtl_cosim=passed
real_hidden=passed
network_llm_used=false
acceptance_archive_sha256=9e041d9dbafe225d71910f06e65b4ee4fadd69dd348801a7caa475fcd654deb6
```

## Claim boundary

P4-0D is closed. Pre-Stage-4 as a whole is not closed. P4-0E through P4-0I
remain pending and Stage 4 remains forbidden.
