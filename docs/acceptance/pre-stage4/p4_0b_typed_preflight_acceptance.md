# Pre-Stage-4 P4-0B Typed Preflight Acceptance

## Verdict

```text
P4_0B_TYPED_PREFLIGHT=accepted_local_validation
BASE_COMMIT=11df86f199b8da03ed83baf9119841b3610cdad4
ACCEPTED_AT_UTC=2026-08-03T10:26:52.929965Z
FOCUSED_TEST_COUNT=64
FULL_REGRESSION_TEST_COUNT=2044
CAND2_REPLAY_PASSED=true
INVOCATION_EXECUTION_COMPATIBILITY=v1
DIRECT_REPLAY_ENTRYPOINT_VERIFIED=true
STAGED_BUDGET_ACCOUNTING=v1
NATIVE_VITIS_CSIM_CHANGED=false
COSIM_ADDED=false
OPTIMIZER_POLICY_CHANGED=false
```

## Accepted staged behavior

```text
Testbench independent compile
→ reference independent compile
→ Candidate independent compile
→ object top-symbol checks
→ reference LTO interface probe
→ Candidate LTO interface probe
→ final full link
```

Candidate compile, missing-top, and Candidate interface failures are typed as
Candidate-owned before final link. Stage 3 rejects that Candidate and does not
launch its later Public/CSYNTH/Hidden handlers. Existing `best_correct` and
rollback behavior remains authoritative.

Unknown final-link ownership remains unknown-safe.

## Invocation compatibility

```text
execution.status=completed
TestbenchPreflightResult.status=failed
reason_code=<typed failure>
failed_component=<typed component>
substep.status=failed
substep.returncode=<nonzero>
```

## Direct replay-tool entrypoint

```text
entrypoint=python tools/p4_0b_preflight_replay.py
repository_root_bootstrap=true
direct_subprocess_regression=true
```

## Staged budget accounting

```text
prospective_plan_check=before_first_launch
consume_only_physically_launched_substeps=true
compatibility_no_top_full_pass=tool:4,compile:4
dual_top_full_pass=tool:9,compile:6
first_compile_failure_after_capacity_check=tool:1,compile:1
testbench_repair_failed_then_passed=tool:5,compile:5
legacy_preflight_csynth_csim_full_chain=tool:7,compile:5
```

This is physical accounting for P4-0B. P4-0F still owns product budget profiles,
ceilings, mode defaults, and Full reserve policy.

## Historical failure-shape replay

```json
{
  "candidate_id": "cand-2",
  "failed_component": "candidate",
  "failure_owner": "candidate",
  "historical_source_recovered": false,
  "later_validation_started": false,
  "launched_substages": [
    "testbench_compile",
    "reference_compile",
    "candidate_compile"
  ],
  "link_started": false,
  "next_action": "repair_candidate",
  "passed": true,
  "reason_code": "candidate_compile_failed",
  "reason_codes": [
    "candidate_compile_failed"
  ],
  "replay_id": "s38-nested-stencil-r1-safe-optimize.cand-2",
  "replay_kind": "deterministic_equivalent_failure_shape",
  "route_action": "repair_candidate",
  "schema_version": 1,
  "status": "failed"
}
```

## Toolchain

```text
compiler=g++ (Ubuntu 11.4.0-1ubuntu1~22.04.3) 11.4.0
symbol_tool=GNU nm (GNU Binutils for Ubuntu) 2.38
```

## Test evidence

- `focused_tests.log`
- `full_regression.log`
- `cand2_replay.json`
- `python_compile.log`
- `git_diff_check.log`

## Diff scope

```text
agrefactor/evaluation/preflight_feedback.py        |  31 +++
 agrefactor/evaluation/preflight_feedback_view.py   |   6 +
 agrefactor/evaluation/testbench_preflight.py       |  34 ++-
 agrefactor/evidence/__init__.py                    |  10 +
 agrefactor/evidence/testbench.py                   | 232 +++++++++++++++++++++
 agrefactor/optimization/qualification.py           |  18 ++
 agrefactor/product/source_bootstrap.py             |   4 +
 agrefactor/product/stage3_optimizer.py             |   6 +
 agrefactor/runtime/candidate_repair_integration.py |  41 ++++
 agrefactor/runtime/preflight_stage.py              |  49 ++++-
 ...STAGE4_PRODUCT_VALIDATION_HARDENING_CONTRACT.md |  18 ++
 docs/roadmap/PROJECT_STATE.md                      |  17 ++
 tests/test_p0_public_testbench_repair_routing.py   |  10 +-
 tests/test_p2_source_only_bootstrap.py             |   9 +-
 tests/test_preflight_compile_budget.py             |  28 +--
 tests/test_preflight_stage.py                      |   4 +-
 ...ified_compile_csynth_csim_budget_integration.py |  52 ++---
 17 files changed, 519 insertions(+), 50 deletions(-)
```

## Claim boundary

P4-0B proves staged host-compiler ownership, routing, early termination, and
physical budget accounting. It does not prove native Vitis CSIM, RTL COSIM,
functional equivalence, synthesizability, PPA improvement, model quality, or
`dynamic-v1`. P4-0C remains next.

<!-- PRE_STAGE4_P4_0B_REPOSITORY_CLOSURE:BEGIN -->
## Repository closure handoff

```text
P4_0B_REPOSITORY_CLOSURE=accepted
P4_0B_ACCEPTED_COMMIT=717efb78e4dd53fbe1fdc14d7db78632c227ea1a
FOCUSED_TEST_COUNT=64
FULL_REGRESSION_TEST_COUNT=2044
NEXT_PRE_STAGE4_PACKAGE_AT_ACCEPTANCE=P4-0B-R_BOUNDED_OPTIMIZE_CANDIDATE_RECOVERY
```

The repository closure records the accepted P4-0B implementation commit. The
bounded Optimize recovery package remained a separate later behavior package
and did not enlarge the P4-0B claim.
<!-- PRE_STAGE4_P4_0B_REPOSITORY_CLOSURE:END -->
