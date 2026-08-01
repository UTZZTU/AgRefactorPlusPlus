# Stage 3.6 Decision Record — Pragma Model Integration

## Status

```text
DECISION_STATUS=accepted
PACKAGE=S3.6_PRAGMA_MODEL_INTEGRATION
BASELINE=f5a46d62cca864828e6d1ec3bbe7c5b2ef200f8a
NEXT_PACKAGE=S3.7_PRODUCT_ADAPTERS
```

## 1. Scope

S3.6 connects the frozen S3.3 Pragma level to real model-backed action planning
and complete-source generation. It does not enable product `optimize/full`, run
Vitis, or claim that a directive is legal, correct, synthesizable, or beneficial.

The package implements:

```text
accepted parent CandidateRecord
→ typed agent-safe PPA projection
→ non-authoritative typed Pragma action
→ evidence-linked Pragma hypothesis
→ complete-source candidate generation
→ explicit qualification adapter boundary
```

## 2. Evidence and action authority

S3.6 reuses the accepted S3.5 `BottleneckEvidenceView` projection rather than
introducing a second PPA schema. The model receives aggregate latency, II,
clock, resources, feasibility, parser-warning codes and report identity hashes,
but not raw report text, report paths, Hidden evidence or operator-full data.

A model action is persisted with:

```text
authoritative=false
action_source=model_proposal
```

The action does not prove that `target_ref` exists, that a directive is legal in
the effective Vitis version, or that the expected PPA effect will occur.

## 3. Frozen safe-v1 directive scope

Allowed action kinds are:

```text
pipeline
unroll
array_partition
dataflow
inline
bind_storage
bind_op
unknown
```

Each non-unknown action must contain:

- a compatible typed `target_kind`;
- an agent-safe descriptive `target_ref`;
- directive-specific typed parameters;
- at least one supplied PPA evidence ID;
- at least one exact leaf from the frozen signal-field allowlist;
- low/medium/high confidence and an agent-safe claim.

`unknown` is a first-class safe result. It must use low confidence, target kind
`unknown`, null `target_ref`, empty parameters/evidence/signals, and cannot
produce an executable hypothesis.

## 4. Small correction: BIND versus generic RESOURCE

The high-level design used the shorthand `BIND / RESOURCE`. Safe-v1 resolves
that ambiguity into typed modern directive families:

```text
bind_storage
bind_op
```

Generic `resource` is not accepted as an S3.6 action because it does not identify
whether the consumer is storage or operation binding and would require
version-dependent free-form interpretation. This is a contract clarification,
not removal of the original binding objective. Future version-aware support may
add a typed legacy resource action through normal change control.

## 5. Directive-specific parameter contract

S3.6 validates only schema-level parameter legality:

- `pipeline`: optional positive `ii`, optional boolean `rewind`;
- `unroll`: optional positive `factor`, optional boolean `skip_exit_check`;
- `array_partition`: exact type plus factor/dimension rules;
- `dataflow`: no parameters in safe-v1;
- `inline`: exact mode `on|off|recursive`;
- `bind_storage`: typed storage type/implementation and optional latency;
- `bind_op`: typed operation/implementation and optional latency.

The parser does not inspect source text to prove target existence or directive
placement. Those remain generation and downstream qualification concerns.

## 6. No static Pragma gate

S3.6 does not count `#pragma` lines, search loop text, match symbols with regular
expressions, or infer directive legality from source strings. The complete source
is model context only. A missing or ambiguous target must lead to `unknown` or a
candidate later rejected by authoritative validation, not a guessed static gate.

This preserves the project rule established after earlier incomplete static
recognition gates produced false positives.

## 7. Hypothesis linkage and generation

The strict analysis response contains `actions` and `hypotheses` in one JSON
object. Hypotheses:

- reference one non-unknown action by one-based index;
- cite only evidence already cited by that action;
- retain the frozen latency/decrease objective;
- retain the frozen verification order;
- receive adapter-owned deterministic IDs.

The selected action is embedded in `HypothesisRecord.model_identity` and passed
to the rewrite Prompt. Existing `CandidateResponseContract` remains the authority
for complete source, semantic change and exact top-interface preservation.

## Prompt module import boundary correction

S3.6 removes the eager runtime import from `agrefactor.prompts.optimization`
into the optimizer package initializer. Hypothesis types are validated lazily at
request construction, preserving the same strict runtime type/level checks while
allowing the prompt module to be imported first in a fresh process. This is an
import-boundary correction only; it does not change optimizer policy or evidence
semantics.

## 8. Three-level dispatch

The existing typed dispatch remains source-independent and now has deterministic
coverage for all three levels:

```text
Structural
Bottleneck
Pragma
```

All delegates must still expose identical prospective `BudgetIncrement`; S3.6
does not alter safe-v1 rounds, first-valid selection, rollback, checkpoint,
resume or best-pointer semantics.

## 9. Bounded real smoke

Successful closure performs exactly:

```text
1 real Pragma action/hypothesis call
1 real complete-source rewrite call
0 Vitis
0 compile
0 CSIM
0 CSYNTH
0 Hidden evaluation
```

It uses the accepted Stage 2 output policy:

```text
analysis max_tokens=32768
rewrite max_tokens=32768
safety ceiling=65536
```

For the bounded DeepSeek smoke, provider JSON mode remains disabled and thinking
is explicitly disabled, matching the accepted S3.5 transport correction. The
local strict response contract remains authoritative and no hidden retry is
added.

## 10. Product boundary and S3.7 correction

S3.6 does not open product `optimize/full`. Before S3.7 may remove those gates,
its closure must include an internal end-to-end optimizer exercise covering:

```text
baseline qualification
→ Structural
→ Bottleneck
→ Pragma
→ candidate qualification/PPA comparison
→ rollback and best_correct protection
→ unified final artifacts
```

That exercise is an S3.7 entry/closure condition, not an S3.6 implementation.
It supplements the existing route without changing the three-level architecture.

## 11. Compatibility

- S3.1 state and lineage schemas are unchanged.
- S3.2 qualification/PPA/cache contracts are unchanged.
- S3.3 policy, rollback, checkpoint, resume and best semantics are unchanged.
- S3.4 Structural contracts are unchanged.
- S3.5 Bottleneck evidence/classification contracts are unchanged.
- `refactor` protected files are unchanged.
- product `optimize/full` remains gated.
