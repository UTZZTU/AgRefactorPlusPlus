# Pre-Stage-4 P4-0C Public Native Vitis CSIM Decision

> **Status:** implemented only after the package acceptance gates pass.
>
> P4-0C replaces the Public host-compiler differential executor with real
> Vitis HLS `csim_design` while preserving Hidden as an independent,
> operator-only host differential gate.

## Frozen behavior

```text
Source integrity
→ typed host Preflight
→ Public native Vitis HLS CSIM
→ Vitis CSYNTH
→ Hidden differential functional test
→ final decision
```

Public native CSIM uses:

```text
candidate.cpp  → add_files         (design)
reference.cpp  → add_files -tb     (functional oracle)
testbench.cpp  → add_files -tb     (Public driver)
csim_design -clean
```

The Original/reference implementation is never synthesized. It may remain
non-synthesizable and participates only as a testbench-side oracle.

## Public and Hidden boundary

Public uses `flow.tools.vitis_csim.run_vitis_csim` and produces an agent-safe
projection. Hidden continues to use `flow.tools.csim.run_csim` and remains
operator-full. Hidden source, paths and detailed diagnostics never enter model
prompts or ordinary trace evidence.

P4-0C does not enable Public-CSIM Optimize recovery. P4-0B-R remains limited to
typed Candidate-owned Preflight and CSYNTH-legality evidence.

## Budget

One Public native Vitis CSIM suite reserves and consumes:

```text
tool_calls=1
csim_calls=1
compile_calls=0
csynth_calls=0
```

The Vitis-internal compiler is not double-counted as a separate host compile.
The complete Optimize recovery prospective increment is recalculated as:

```text
Preflight: 9 tool + 6 compile
Public native CSIM: 1 tool + 1 csim per Public suite
CSYNTH: 1 tool + 1 csynth
Hidden host differential: 2 tool + 1 compile + 1 csim per Hidden suite
```

## Toolchain evidence

Before launch, native CSIM uses the existing TargetProfile command/settings
resolution and version probe. The invocation artifact records:

- requested and actual Vitis version;
- command and command provenance;
- effective TargetProfile;
- Tcl hash and source roles;
- timeout and return code;
- prospective and consumed budget;
- no model or credential evidence.

## Cache

The qualification cache pipeline identity is changed to:

```text
prestage4-native-vitis-csim-v1
```

Old qualification entries that used host Public evaluation cannot be reused.

## Acceptance boundary

P4-0C acceptance requires:

- deterministic focused tests;
- complete repository regression;
- isolated deterministic replay;
- a committed real sample executed by actual Vitis HLS `csim_design`;
- zero network LLM calls;
- exact Public-native/Hidden-host wiring for Refactor and Optimize;
- exact stage order and cache invalidation evidence.

## Compatibility migration

The P4-0C order is also authoritative for generic candidate repair and the
committed Stage 2 smoke matrix. Repair validation prefixes are derived from the
task's declared Public and Hidden suites rather than a static order.

The Stage 2 baseline physical budget is:

```text
tool_calls=5
compile_calls=2
csynth_calls=1
csim_calls=2
llm_calls=0
```

Public native CSIM owns one tool/CSIM call and zero separately counted host
compile calls.

## Deferred

P4-0C does not implement Public RTL COSIM, COSIM budget fields, `.env`,
DeepSeek Thinking policy, mode-specific budgets or `dynamic-v1`.

```text
NEXT_IMPLEMENTATION_PACKAGE=P4-0D_PUBLIC_RTL_COSIM
```
