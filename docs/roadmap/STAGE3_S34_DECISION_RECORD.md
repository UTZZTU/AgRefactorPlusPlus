# Stage 3.4 Structural Model Integration Decision Record

## Status

```text
DECISION_STATUS=accepted_by_S3.4_apply_gate
BASELINE=7e55aae15bbae7f9bd236dd4fc4832558e806f8b
PACKAGE=S3.4_STRUCTURAL_MODEL_INTEGRATION
NEXT_PACKAGE=S3.5_BOTTLENECK_MODEL_INTEGRATION
```

This record resolves the implementation details left intentionally open by the
frozen Stage 3 contract. It does not change the Structural → Bottleneck →
Pragma order, correctness-first qualification, Hidden boundary, shared budget,
checkpoint, rollback, or product CLI gate.

## 1. Scope

S3.4 adds exactly two model-backed Structural operations:

1. propose at most three typed Structural hypotheses as strict JSON;
2. generate one complete replacement C++ source for the selected hypothesis.

S3.4 does not implement the Bottleneck or Pragma model consumers, does not run
the product `optimize/full` commands, and does not make a model response a
correctness or PPA verdict.

## 2. Explicit level ownership; no heuristic Structural gate

The Structural level is determined by the typed optimizer state and the typed
`HypothesisRecord.level`. It is not inferred from source strings, regular
expressions, pragma counts, loop spellings, or other incomplete static
patterns.

Static or textual information may be included as advisory evidence in a later
package, but an incomplete matcher must not become an authoritative blocking
gate. A valid model response is accepted only against the explicit response
schema and complete-source contract. Whether the generated program is correct,
synthesizable, feasible, or improved remains the responsibility of the
qualification/PPA path.

## 3. Two-call split

Hypothesis proposal and source generation are separate calls. This preserves a
causal record before code exists and prevents the implementation from
degenerating into an untracked whole-source rewrite.

Successful bounded real smoke accounting is therefore exact:

```text
hypothesis_model_calls=1
rewrite_model_calls=1
total_llm_calls=2
tool_calls=0
compile_calls=0
csim_calls=0
csynth_calls=0
```

A launched call consumes its physical LLM slot even when transport, parsing, or
response-contract validation fails. Prospective budget exhaustion stops before
the provider boundary and consumes no new call.

## 4. Hypothesis response contract

The model returns strict JSON with exactly these top-level fields:

```json
{
  "schema_version": 1,
  "hypotheses": []
}
```

Each item contains exactly:

- `claim`;
- `expected_benefit` with frozen `latency/decrease` semantics;
- `risk`;
- non-empty `modification_scope`;
- ordered `verification_plan` equal to
  `preflight → public → csynth → hidden`.

The model does not choose candidate or hypothesis IDs. The adapter assigns
stable IDs from the explicit level and round. Provider order is the priority
order consumed by the S3.3 first-valid policy.

Empty hypotheses are legal and mean that the provider proposed no executable
Structural branch for that round. Malformed output is a model-contract error;
it is not silently repaired or guessed.

## 5. Complete-source response contract

The rewrite response must contain exactly one complete fenced C++ source and no
commentary or patch/diff wrapper. The existing accepted
`CandidateResponseContract` remains authoritative for:

- complete replacement extraction;
- UTF-8/non-empty source;
- top-function interface preservation;
- semantic change from the parent;
- rejection of patch-like or executable-testbench output.

This is an output-shape and interface contract, not a claim that a textual
matcher can certify Structural semantics or program correctness.

## 6. Prompt and Hidden boundary

S3.4 uses the shared `LayeredPrompt` representation and records a deterministic
prompt identity. Model-facing data contains the task contract, TargetProfile,
model-family instruction, parent source, typed hypothesis/evidence identifiers,
and agent-safe context only.

Hidden test sources, operator-full reports, secrets, API keys, and private
oracle content are rejected before prompt construction and before safe artifact
serialization. Raw prompts and raw model responses are not persisted by the
S3.4 model-call artifact writer; only safe manifests, hashes, usage, finish
reason, status, and error type are retained.

## 7. Qualification boundary

`StructuralModelCandidateGenerator` returns only generated source plus model
metadata. `StructuralModelCandidateExecutor` requires an explicit injected
`StructuralQualificationAdapter` before it can return a
`CandidateExecutionResult` to the optimizer state machine.

Therefore:

```text
valid_model_output != qualified_candidate
valid_model_output != best_correct
valid_model_output != PPA_improvement
```

The real S3.4 smoke deliberately stops after the model contract. It launches no
compiler, CSIM, CSYNTH, Vitis, Hidden evaluation, or PPA comparison and records
`claim_scope=structural_model_contract_only`.

## 8. State-machine compatibility

`HypothesisRequest.parent_source` is an additive, backward-compatible field
with an empty-byte default. Fake providers and executors remain network/tool
free. The state machine supplies the exact checkpointed parent source to the
provider and derives network/Vitis trace flags from injected components.

S3.3 policy, round limits, selection, candidate IDs, rollback, checkpoint,
resume, and best-pointer decisions are unchanged.

## 9. Product boundary

The frozen CLI names remain registered, but product `optimize/full` execution
continues to be rejected by the existing Stage-3 product gate. S3.4 changes no
CLI or product bootstrap file. Product adapters remain S3.7.

## 10. Acceptance

S3.4 is accepted only when the one-step installer leaves the worktree changed
after all of the following pass in the target environment:

```text
S3.4 focused=52/52
optimizer regression=233/233
full deterministic regression=1741/1741
bounded real Structural smoke=accepted
real model calls=2
real Vitis/compile/CSIM/CSYNTH calls=0
protected refactor and product-gate files unchanged
```

Failure at any gate restores the exact S3.3 baseline files.
