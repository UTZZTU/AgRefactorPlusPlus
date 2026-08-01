# Stage 3.5 Decision Record — Bottleneck Model Integration

## Status

```text
DECISION_STATUS=accepted
PACKAGE=S3.5_BOTTLENECK_MODEL_INTEGRATION
BASELINE=c881dea0ea68f0fbf7c2b359bd270f362827a90f
NEXT_PACKAGE=S3.6_PRAGMA_MODEL_INTEGRATION
```

## 1. Scope

S3.5 connects the frozen S3.3 Bottleneck level to real model-backed analysis
and complete-source generation. It does not enable product `optimize/full`, run
Vitis, add Pragma policy, or claim that model output is correct or faster.

The package implements:

```text
accepted parent CandidateRecord
→ typed PpaEvidence
→ agent-safe evidence projection
→ non-authoritative model classification
→ evidence-linked Bottleneck hypothesis
→ complete-source candidate generation
→ explicit qualification adapter boundary
```

## 2. Evidence authority

`PpaEvidence` remains the source-backed tool evidence. The model receives a
projection containing aggregate latency, II, clock, resource, feasibility,
parser-warning codes, report hash/profile/format, and comparison-context hash.

The projection deliberately excludes:

- raw report bytes or text;
- report-relative path;
- Hidden testbench, Hidden diagnostic, or operator-full content;
- unsupported free-form tool diagnostics.

The projection is deterministic and hashable. A model classification is stored
with:

```text
authoritative=false
classification_source=model_inference
```

It cannot replace qualification, CSYNTH evidence, or PPA comparison.

## 3. Classification contract

Allowed model classification kinds are:

```text
initiation_interval
loop_carried_dependency
memory_port_contention
critical_path
resource_bottleneck
unknown_loop_bound
dataflow_stall_risk
latency_structure
objective_constraint
unknown
```

Each non-unknown classification must cite:

- at least one supplied evidence ID;
- at least one field from the frozen signal-field allowlist;
- a low/medium/high confidence;
- an agent-safe causal claim.

`unknown` is a first-class safe result. It must use low confidence and cannot
produce an executable hypothesis. The implementation does not coerce unknown
into a guessed candidate failure or guessed Bottleneck kind.

## 4. No static Bottleneck gate

S3.5 does not inspect source text, pragma count, warning strings, loop labels, or
regular-expression matches to certify a bottleneck or block a candidate.

Source and typed PPA evidence are model inputs. Classification is an explicit,
audited inference. Qualification and subsequent real PPA evidence remain the
authority. This preserves the project lesson that incomplete recognition must
not be promoted into a blocking static gate.

## 5. Hypothesis linkage and selection

The bounded analysis response contains classifications and hypotheses in one
strict JSON object. Hypotheses:

- reference a one-based non-unknown classification;
- cite only evidence cited by that classification;
- use the frozen latency/decrease objective;
- preserve the frozen verification order;
- receive adapter-owned deterministic IDs.

The S3.3 state machine continues selecting the first valid provider-ordered
hypothesis. S3.5 does not alter safe-v1 search parameters.

## 6. Candidate generation and qualification

The selected hypothesis and its non-authoritative classification are included
in the rewrite Prompt. The model must return exactly one complete C++ source
artifact. Existing `CandidateResponseContract` enforces complete-source,
semantic-change, and top-interface rules.

A contract-valid source is not accepted by itself. `BottleneckQualificationAdapter`
remains an explicit required consumer. S3.5 smoke intentionally stops before
qualification and makes only a model-integration claim.

## 7. Per-level dispatch

S3.3 consumes one provider and one executor interface. S3.5 adds explicit
`OptimizationLevel` dispatch so Structural and Bottleneck components can
coexist without source inspection.

All delegates must expose the same prospective `BudgetIncrement`; otherwise
dispatch construction fails. This preserves exact preflight accounting before
the state machine routes a request.

## 8. Artifacts and privacy

S3.5 writes:

```text
optimizer/model_calls.jsonl
optimizer/bottlenecks/btl-*.json
optimizer/hypotheses/hyp-*.json
optimizer/candidates/cand-*/source.cpp (smoke only)
summary.json (smoke only)
```

Raw Prompt and raw response text are not persisted. Model call records contain
identity hashes, safe manifests, usage, finish reason, and error code only.

## 9. Bounded real smoke

The closure smoke uses one repository fixture source and one typed PPA fixture
with an explicit initiation interval. Successful execution performs exactly:

```text
1 real Bottleneck analysis call
1 real complete-source rewrite call
0 Vitis
0 compile
0 CSIM
0 CSYNTH
0 Hidden evaluation
```

This proves model/evidence/classification/hypothesis/source integration only.
It does not prove the classification is true for a live report, that the source
is correct or synthesizable, or that PPA improves.

## 10. Compatibility

- S3.1 state schema is unchanged.
- S3.2 qualification/PPA/cache contracts are unchanged.
- S3.3 policy, rollback, checkpoint, resume, and best-pointer semantics are unchanged.
- S3.4 Structural Prompt and model contracts are unchanged.
- `refactor` protected files are unchanged.
- product `optimize/full` remains gated.

## D8. DeepSeek structured-output transport after real empty-content evidence

The first target-host S3.5 closure attempt produced the following evidence after
all deterministic tests passed and before any second model call:

```text
S35_FOCUSED=77/77
OPTIMIZER_REGRESSION=310/310
FULL_DETERMINISTIC_REGRESSION=1818/1818
first_call_error=OpenAICompatibleResponseError: response message content is empty
auto_rollback=passed
```

The failure came from provider transport output, not from Bottleneck evidence
classification or the strict response schema. DeepSeek's official JSON Output
documentation explicitly notes that provider-side JSON mode can occasionally
return empty `content`; DeepSeek V4 also defaults to thinking mode.

Decision:

- the frozen `BottleneckAnalysisResponseContract` remains the sole JSON/schema
  authority;
- the bounded DeepSeek smoke does not enable provider-side `response_format`
  JSON mode;
- DeepSeek smoke calls explicitly send
  `extra_body={"thinking":{"type":"disabled"}}`;
- private `reasoning_content` is never treated as the final JSON response;
- no automatic retry is added, so the successful smoke still performs exactly
  two physical LLM calls;
- other model families preserve their provider-default thinking behavior while
  remaining subject to the same local strict response contract.

This is a transport reliability correction, not a weakening of evidence,
classification, Hidden, correctness, or candidate qualification contracts.


## Output token policy alignment

The bounded S3.5 smoke reuses the accepted Stage 2 typed model-output policy:

```text
analysis max_tokens=32768
rewrite max_tokens=32768
safety ceiling=65536
```

This prevents the earlier small-output-budget failure mode without using the
legacy coverage-agent `65536` exception as the default. DeepSeek thinking remains
disabled only for this bounded contract smoke; local strict response validation
remains authoritative and no automatic retry is introduced.


## Real pricing metadata compatibility correction

A target-host v3 attempt proved that the first DeepSeek response completed normally:

```text
finish_reason=stop
prompt_tokens=2047
completion_tokens=794
total_tokens=2841
thinking_output_tokens=null
```

The subsequent failure was an S3.5 adapter defect: it accessed nonexistent
`ModelPricingSnapshot.source` and `.version` attributes. The authoritative typed
snapshot instead exposes `official_source_identity`, `official_source_url`, and
`pricing_snapshot_sha256`; existing Stage 2/S3.4 consumers record only safe estimation
status, quality, snapshot hash, currency and amount availability. S3.5 now reuses that
accepted metadata contract and adds a deterministic regression with an explicit pricing
snapshot. No token, thinking, evidence, classification, Hidden, budget or qualification
semantics are weakened.

## Target-host response-contract evidence: exact resource leaf paths

A later target-host run completed the first real model request successfully:

```text
finish_reason=stop
prompt_tokens=2047
completion_tokens=732
thinking_output_tokens=null
TOKEN_EXHAUSTION_EVIDENCE=false
```

The response then failed the local frozen schema because one classification used
`resources_used` and `resources_available` as `signal_fields`. Those names are
typed JSON containers, not scalar evidence paths. Automatically expanding them
into all resource leaves would silently broaden the model claim and weaken exact
evidence linkage, so S3.5 does not normalize or accept those aliases.

The prompt and parser now consume one shared frozen allowlist. The prompt exposes
the complete machine-readable exact-string array and explicitly states that the
container names are invalid; resource classifications must cite one or more exact
leaf paths such as `resources_used.lut` or `resources_available.dsp`. The strict
parser rejection remains unchanged. This is prompt/schema alignment, not a new
heuristic gate and not a relaxation of evidence semantics.
