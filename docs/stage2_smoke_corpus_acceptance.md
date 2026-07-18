# Stage 2.5.1 Smoke Corpus Acceptance

## 1. Scope

Stage 2.5.1 establishes a reusable seven-type source corpus and independent
ground-truth contract. It does not execute the full validation chain.

Feature commit:

```text
ca991c372f9f40f7e592136b12af774dd985c0fa
feat: add Stage 2 smoke corpus
```

## 2. Stable kernel types

```text
array_map
reduction
nested_stencil
multi_output
struct_record
hls_stream
stateful
```

Each case contains:

```text
original code
candidate code
Preflight testbench
Public testbench
Hidden testbench
Hidden secret marker
manual ground truth
expected full-chain physical budget
operator manifest
agent-safe manifest
```

Ground truth is manually authored and is not derived from runtime owner,
route, or terminal output.

## 3. Deterministic validation

```text
24/24 targeted passed
48/48 smoke + Candidate Response Contract regression passed
686/686 full unittest passed
```

The tests verify seven-type coverage, unique identities, immutable baseline
labels, exact expected budgets, Public/Hidden roles, source-role separation,
Hidden marker isolation, manifest serialization, generic naming, and top
interface extraction.

## 4. Real local Preflight acceptance

```text
seven committed corpus cases
→ real g++ compile/link Preflight
→ 7/7 passed
```

Exact usage:

```text
tool_calls=7
compile_calls=7
csynth_calls=0
csim_calls=0
llm_calls=0
tokens=0
cost_usd=0.0
```

Acceptance directory:

```text
/data/agrefactor_runs/stage2_5_1_smoke_corpus_20260718_232154/acceptance
```

Artifacts:

```text
stage2_smoke_corpus_preflight.json
ground_truth_manifest.json
agent_safe_manifest.json
work/<case-id>/testbench_preflight_invocation.json
```

## 5. Information boundary

The operator manifest contains manual labels and source digests. The
agent-safe manifest excludes:

- ground truth;
- Hidden suite identity;
- Hidden source digest;
- Hidden testbench code;
- Hidden secret marker.

## 6. Evidence boundary

This milestone proves:

- seven durable case definitions exist;
- all seven compile and link through the real Preflight path;
- independent labels and safe manifests are machine-checkable;
- struct and `hls::stream` interfaces reach real local g++ Preflight.

It does not prove:

- seven-type Vitis CSYNTH success;
- seven-type Public or Hidden CSIM success;
- fault owner/route accuracy;
- Hidden-failure terminal behavior;
- real or fake model repair ability;
- general support for arbitrary HLS programs.

Next milestone:

```text
Stage 2.5.2 Real Full-chain Pass Matrix
```
