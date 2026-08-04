# Pre-Stage-4 P4-0C Public Native Vitis CSIM Acceptance

```text
P4_0C_PUBLIC_NATIVE_VITIS_CSIM_IMPLEMENTED=true
P4_0C_UNIFIED_STAGE_ORDER=true
P4_0C_PUBLIC_BACKEND=native_vitis
P4_0C_HIDDEN_BACKEND=host_differential
P4_0C_NETWORK_LLM_USED=false
P4_0C_FOCUSED_TESTS=23
P4_0C_FULL_REGRESSION=2089
P4_0C_REAL_VITIS_SMOKE=accepted
P4_0C_ACCEPTED_RUN_ID=p4_0c_public_native_vitis_csim_v5_20260803T154951Z_2040804
P4_0C_ACCEPTED_COMMIT=d61004f056e585199177891d576f83070f4dbdbb
P4_0C_REPOSITORY_CLOSURE=accepted
P4_0C_CACHE_PIPELINE=prestage4-native-vitis-csim-v1
P4_0C_PUBLIC_CSIM_OPTIMIZE_RECOVERY=false
P4_0C_CANDIDATE_REPAIR_PREFIX=task_aware
P4_0C_STAGE2_SMOKE_ORDER=preflight_public_csynth_hidden
P4_0C_STAGE2_SMOKE_BUDGET=5_tool_2_compile_1_csynth_2_csim
NEXT_IMPLEMENTATION_PACKAGE_AT_ACCEPTANCE=P4-0D_PUBLIC_RTL_COSIM
```

## Accepted implementation

The Public stage runs real Vitis HLS `csim_design`. Candidate source is a design
file; Original/reference and the Public testbench are testbench-side files.
Hidden remains the existing independent host differential executor and is not
converted into a model-visible Vitis stage.

The Refactor validation state machine and the Optimize qualification
orchestrator both execute:

```text
Preflight → Public native Vitis CSIM → CSYNTH → Hidden
```

## Evidence required in the package report

```text
focused_tests.log
p4_0c_replay.json
full_regression.log
real_native_vitis_smoke.json
real_native_vitis_smoke.log
prepared_file_manifest.json
```

The real smoke must show:

```text
passed=true
network_llm_used=false
real_vitis_used=true
execution_backend=native_vitis
tool_calls=1
csim_calls=1
compile_calls=0
csynth_calls=0
```

No generalized model-quality, arbitrary-kernel or stable-PPA claim follows from
this single real tool-stage smoke.
