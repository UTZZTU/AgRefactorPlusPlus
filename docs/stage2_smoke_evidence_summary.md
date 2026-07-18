# Stage 2.5 Multi-type Kernel Smoke Evidence Summary

## 1. Status

```text
Stage 2.5 status: completed
Current regression: 727/727 passed
Next stage: Stage 2.6 Closure-readiness Audit
Stage 2 status: still open
```

Stage 2.5 is complete, but Stage 2 is not closed. Closure still requires the
2.6 audit, evidence-backed 2.7 hardening, and 2.8 final synchronization.

## 2. Source milestones

| Milestone | Primary result | Tests | Physical usage |
|---|---|---:|---:|
| 2.5.1 Corpus / Ground Truth | 7 durable cases; real Preflight 7/7 | 24 / 48 / 686 | 7 tool / 7 compile / 0 csynth / 0 csim / 0 LLM |
| 2.5.2 Pass Matrix | 7/7 complete real chains accepted | 21 / 77 / 707 | 42 tool / 21 compile / 7 csynth / 14 csim / 0 LLM |
| 2.5.3 Fault Matrix | 9/9 ground-truth matches | 20 / 65 / 727 | 13 tool / 8 compile / 2 csynth / 3 csim / 0 LLM |

The test counts are regression snapshots and are not summed.

## 3. Coverage accounting

```text
7 baseline ground-truth records
+ 9 fault ground-truth records
= 16 independent labels

23 acceptance scenario executions
= 19 real-tool scenario executions
+ 4 deterministic normalized-route executions
```

Kernel types:

```text
array_map
reduction
nested_stencil
multi_output
struct_record
hls_stream
stateful
```

Fault coverage:

```text
candidate / testbench / original / toolchain / unknown / mixed
compile / csynth / public_evaluation / hidden_evaluation
repair_candidate / repair_testbench / repair_original
fix_toolchain / review_unknown / review_mixed
accepted / repair_pending / rejected / blocked / review_required
```

## 4. Budget interpretation

Cumulative physical work across the three independent acceptance runs:

```text
tool_calls=62
compile_calls=36
csynth_calls=9
csim_calls=17
llm_calls=0
```

This is **not** one shared matrix budget. Shared-budget claims remain local to
each milestone.

## 5. Real and deterministic evidence

Real tools cover seven-type Preflight, seven complete Vitis HLS 2023.2 chains,
three compile-owner failures, one Public mismatch, and one Hidden mismatch.

Deterministic normalized reports cover toolchain block, unknown synthesis
review, mixed-owner Public review, and Hidden unknown review.

No FakeProvider, real network model, or repair execution was used in Stage 2.5.

## 6. Hidden boundary

- Operator ground truth is separate from agent-safe manifests.
- Hidden identity/source is absent from the 2.5.1 agent-safe manifest.
- Hidden testbench text, markers, and labels are absent from normal results and
  traces.
- Hidden candidate failure terminates as `rejected`.
- Hidden unknown failure terminates as `review_required`.
- Hidden feedback never enters iterative repair.

## 7. What Stage 2.5 proves

- Seven committed HLS shapes with independent labels.
- Seven real complete passing chains on the current Vitis 2023.2 host.
- Machine-checkable stage order and physical budgets.
- Nine fault scenarios match expected stage, route, terminal, and budget.
- Public repair handoff and Hidden terminal/no-leakage behavior.

## 8. What Stage 2.5 does not prove

- Arbitrary HLS support or statistical attribution accuracy.
- Real network-model repair behavior.
- Formal UnifiedRunner/CLI construction.
- Stable shared repair protocol and artifact schema.
- Model-family capability profiles.
- Cross-version, device, or host support.
- Real toolchain failure or real ambiguous-CSYNTH routing in this matrix.
- Optimizer, Memory Gate, or migration capability.

## 9. Stage 2.6 audit inputs

Classify every gap as:

```text
blocking before Stage 3
or defer to later Stage
or future/external dependency
```

Audit UnifiedRunner/CLI, real network-model repair, repair protocol/schema,
Model Family Profile, response contract/parser, ground-truth finalization, and
Stage 1 Hardening Batch A.

## 10. Source artifact fingerprints

| Milestone | Artifact | Bytes | SHA-256 |
|---|---|---:|---|
| 2.5.1 | `stage2_smoke_corpus_preflight.json` | 14923 | `7facae1d56c2826eba5411bdee9f163e31139008e0bd02861444025de68c88b2` |
| 2.5.1 | `ground_truth_manifest.json` | 9967 | `196ae86d7aa722b40471e49b0376ca236a15691ab2f069a6aee7081e6e3f2fee` |
| 2.5.1 | `agent_safe_manifest.json` | 5120 | `cc2fd4d01c1fe4f77b055d7e9efdc9883e30e3c08b63b5a4a62e3034a9811f4d` |
| 2.5.2 | `stage2_smoke_pass_matrix.json` | 64662 | `b8037e4bbe0d1b69dde20468961a162ac17c24fff312a8144e56ee5cf57eaf0c` |
| 2.5.2 | `stage2_smoke_pass_matrix_artifacts.json` | 8753 | `b00e240a02b5e5f6779b6a18139bd522003a91792388951d9fa35ae927256e76` |
| 2.5.2 | `stage2_smoke_pass_matrix_summary.json` | 1266 | `8e3c2b0673bb1b92b83baa351ca0f8da7d71214334a205362163b994458a5eda` |
| 2.5.3 | `stage2_smoke_fault_matrix.json` | 58627 | `1fbb7839defa859771a680aa63508f66308fc7ea1834de4c2bf491d9a3d1b256` |
| 2.5.3 | `stage2_smoke_fault_ground_truth.json` | 13000 | `79d60c86bc118f6f15602417f22caec7afddc8ff0581cfb05ba2931fa4d80d62` |
| 2.5.3 | `stage2_smoke_fault_matrix_summary.json` | 454 | `7e64aa4a16d79f6e22f183e117507202de543c7d93f092a22e6c35359752920a` |

Machine-readable index:
[`stage2_smoke_evidence_index.json`](stage2_smoke_evidence_index.json).
