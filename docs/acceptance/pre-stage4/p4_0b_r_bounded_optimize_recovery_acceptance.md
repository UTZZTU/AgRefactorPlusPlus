# P4-0B-R Bounded Optimize Candidate Recovery Acceptance

> Installed only after focused tests, deterministic lineage replay and the
> complete regression pass on the target checkout.

```text
P4_0B_R_IMPLEMENTED=true
P4_0B_R_R1_PREFLIGHT_RECOVERY=accepted
P4_0B_R_R2_CSYNTH_LEGALITY_RECOVERY=accepted
P4_0B_R_MAX_RECOVERIES_PER_ROOT_CANDIDATE=1
P4_0B_R_PUBLIC_CSIM_REPAIR=false
P4_0B_R_HIDDEN_REPAIR=false
P4_0B_R_PPA_REPAIR=false
P4_0B_R_FOCUSED_TESTS=22
P4_0B_R_PRODUCT_INTEGRATION_TESTS=1
P4_0B_R_SHADOW_FULL_REGRESSION=2066
P4_0B_R_EXPECTED_FULL_REGRESSION=2066
P4_0B_R_PARENT_COMMIT=717efb78e4dd53fbe1fdc14d7db78632c227ea1a
NEXT_IMPLEMENTATION_PACKAGE=P4-0C_PUBLIC_NATIVE_VITIS_CSIM
```

## Accepted behavior

- Candidate-owned eligible Preflight or CSYNTH-legality failures may launch one
  Candidate-only model recovery.
- The failed Candidate is retained; the repaired source is a new `cand-N`.
- The descendant preserves the originating hypothesis and explicit parent
  lineage.
- Every repaired source restarts complete Optimize qualification.
- `best_correct` changes only after complete qualification and objective
  comparison.
- Budget, provider, response, validation, non-improvement and ineligible
  outcomes preserve the incumbent.
- Hidden, Public-CSIM and PPA repair are not implemented.

## Evidence

```text
focused_tests.log
product_integration_test.log
p4_0b_r_replay.json
full_regression.log
git_diff_check.log
prepared_file_manifest.json
SUCCESS
```

Deterministic tests establish orchestration semantics. They do not claim stable
network-model recovery rates or stable PPA improvement.
