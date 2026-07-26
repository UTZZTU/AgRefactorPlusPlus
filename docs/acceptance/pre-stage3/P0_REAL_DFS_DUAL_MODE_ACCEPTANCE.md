# P0 Real DFS Source-Only Acceptance

## Step 6 dual-mode acceptance

Execution commit:

```text
67546b4c015f8505a5de72bc1b57159c5c1547fe
```

Combined evidence:

```text
/data/agrefactor_runs/pre_stage3_step_f_acceptance/combined_20260726_025632/step_f_combined_acceptance.json
```

### Lightweight

```text
run_id=p0-step-f-lightweight-20260726_022709
llm_calls=15
prompt_identity_calls=15
public_coverage_files=0
hidden_coverage_files=1
hidden_original_calls=1
hidden_candidate_calls=1
hidden_positive_gcov_records=1
deduplicator_recursion_retained=true
artifact_root=/data/agrefactor_runs/source_run_p0-step-f-lightweight-20260726_022709
work_root=/data/agrefactor_work/source_run_p0-step-f-lightweight-20260726_022709
```

### Coverage-enhanced

```text
run_id=p0-step-f-coverage-enhanced-20260726_023924
llm_calls=25
prompt_identity_calls=25
public_trajectories=2
hidden_trajectories=2
public_coverage_files=3
hidden_coverage_files=3
hidden_original_calls=2
hidden_candidate_calls=2
hidden_positive_gcov_records=2
deduplicator_recursion_retained=true
artifact_root=/data/agrefactor_runs/source_run_p0-step-f-coverage-enhanced-20260726_023924
work_root=/data/agrefactor_work/source_run_p0-step-f-coverage-enhanced-20260726_023924
```

Both profiles completed the normal source-only path with real DeepSeek,
Vitis HLS 2023.2 CSYNTH, Public validation, Hidden validation, bounded repair,
Prompt Identity parity, Hidden isolation and a clean unchanged repository.

## Step 7 final post-stabilization smoke

Cleanup/deprecation implementation commit:

```text
a4ee78ff38df864cadb444c39e24c1d96cdf2527
```

Hidden Stub recovery implementation commit:

```text
03d1ae702f50e3f9ff08a1950a7127ed44feef85
```

Initial Hidden Testbench contract recovery implementation commit:

```text
74699c63cbbdb0e9b30daf08343cb08400216374
```

Unified lightweight Hidden tool recovery implementation commit:

```text
b33fe48cccc441a149b7a613770baba612485d75
```

Final smoke (executed on the final recovery implementation commit):

```text
acceptance=/data/agrefactor_runs/pre_stage3_step_f_acceptance/lightweight_20260726_210452/acceptance_result.json
run_id=p0-step-f-lightweight-20260726_210452
llm_calls=15
prompt_identity_calls=15
hidden_original_calls=1
hidden_candidate_calls=1
hidden_positive_gcov_records=1
deduplicator_recursion_retained=true
artifact_root=/data/agrefactor_runs/source_run_p0-step-f-lightweight-20260726_210452
work_root=/data/agrefactor_work/source_run_p0-step-f-lightweight-20260726_210452
```

This final lightweight run proves that the cleanup/deprecation implementation
the bounded Hidden Stub recovery, the bounded initial Hidden Testbench
contract recovery, and the unified K=1 tool-backed recovery preserve the
normal P0 product path.
Step 6 remains the authoritative dual-profile acceptance; Step 7 adds the
required post-stabilization real smoke.

## Scope

The evidence is a real end-to-end acceptance for the upstream DFS kernel under
the recorded model, target and budget. It is not a universal claim for every
program, kernel, model, device or Vitis version.
