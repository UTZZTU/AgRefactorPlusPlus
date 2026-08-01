# Stage 3.7 Product Adapters Decision Record

## Decision

S3.7 opens the normal `optimize` and `full` product commands only through the
already accepted Stage 3 qualification, state-machine and model contracts. It
does not create a second optimizer or reinterpret model output as tool fact.

## Direct optimize oracle boundary

A candidate cannot validate itself. Direct `optimize` therefore requires:

```text
independent --reference-source
explicit reference top (or an explicit default to --top)
at least one provided Public suite
at least one provided Hidden suite
```

Auto-generating the oracle after receiving the candidate is rejected because it
would make correctness evidence candidate-dependent. Public and Hidden content
must be distinct persisted suites with stable identities.

## Full-mode handoff

`full` first runs the accepted refactor pipeline. Only an accepted formal result
may produce `AcceptedOptimizationMaterial`: final refactor candidate as the
optimization baseline, original input as the independent reference, and the
exact qualified Public/Hidden suites. Missing handoff is a hard failure, not a
silent refactor-only success.

## Baseline-first qualification

Every product optimization run qualifies its baseline before constructing or
calling optimization model endpoints. A rejected baseline records a checkpoint
and Stage 3 identity, returns failure, and launches zero optimization-model
calls. This preserves `best_correct` semantics and prevents optimization from
starting with an untrusted parent.

## Policy and real gate

Normal product execution uses frozen `safe-v1`:

```text
Structural rounds=2
Bottleneck rounds=2
Pragma rounds=3
up to 3 hypotheses, select 1, execute 1 per round
```

The S3.7 acceptance gate deliberately performs one physical analysis per level.
Each rewrite remains conditional on a model-produced executable hypothesis and a
contract-valid complete-source response. Therefore the observed physical call
count is 3–6, but count is never the authority: linked typed decisions prove
analysis abstention, rewrite abstention, or candidate qualification. At least one
generated candidate must complete real qualification. This acceptance harness
does not alter the normal 2/2/3 policy.

## Execution identity correction

The root product execution identity remains the stable source/model/target/suite
identity. Stage 3 writes a linked `stage3_execution_identity.json` containing
optimizer-specific policy, state, candidate index, terminal result, physical
budget usage and safe model-call summary. Final product output merges the safe
fields without rewriting the upstream identity schema mid-run.

## Authoritative boundaries

- model hypotheses, Bottleneck classifications and Pragma actions remain
  non-authoritative;
- real qualification and typed PPA evidence remain authoritative;
- Hidden evidence is evaluation-only and excluded from model-facing artifacts;
- unknown/incomparable outcomes remain safe exits;
- no source-string or regex heuristic becomes an authoritative gate;
- rejected generated candidates are rolled back; no model correctness-repair
  loop is introduced in S3.7.

## Deferred

Multi-kernel repeats, fair `simple_iter` comparison, thinking-mode evaluation,
quality/cost ablations and stable PPA claims remain S3.8.
## Real-gate scope clarification

The target-host gate executes the normal `Stage3ProductOptimizationPhase` with
direct, independently persisted optimization material. This isolates and proves
the new product optimizer adapter, baseline qualification, all three model
levels, real candidate qualification and unified artifacts without adding a
second refactor-generation experiment to the same acceptance run.

The normal source-only `full` command is nevertheless real product code: its
refactor-to-optimizer handoff and refactor-failure stop are covered by typed
deterministic tests. S3.7 does **not** claim that the bounded semantic branch-aware gate is a
separate live source-only `full` benchmark. Multi-kernel/repeated `optimize` and
`full` executions remain S3.8 evaluation evidence.

### S3.7 v3 contract hardening

- Structural and Bottleneck rewrites explicitly preserve HLS pragma/directive ownership for the Pragma level; this prevents an earlier layer from consuming the later layer's product-gate responsibility.
- The Pragma prompt now carries an exact directive/target matrix, exact signal-field naming reminder, and complete executable/unknown JSON shapes.
- The typed INLINE contract matches Vitis HLS syntax: `{}` means ordinary `INLINE`; only `off` and `recursive` are argument modes. The invented `on` string is rejected.
- `ram_1wnr` and `ram_s2p` are accepted typed BIND_STORAGE types for the pinned Vitis HLS 2023.2 contract.
- Strict local parsing, semantic event-linked acceptance, no retry, non-authoritative model decisions, qualification, and best-correct rollback remain unchanged.
### S3.7 v7 acceptance correction

A fixed six-call gate was rejected because it made stochastic model willingness to propose an executable Pragma action a product success condition. V7 first made the Pragma branch conditional. V8 generalizes the same rule to all three levels: three analysis calls are the semantic floor, rewrites are conditional, and the acceptance run requires at least one rewrite to reach real qualification. Therefore a successful acceptance run observes four to six calls, while a normal product run may safely finish after only the three analyses. The gate never forces an edit, retries to obtain one, or substitutes static source matching for evidence.

### S3.7 v8 model-output robustness correction

A model response contract failure is not an infrastructure failure and must not
turn an otherwise qualified run into optimizer `error`. Structural, Bottleneck
and Pragma analysis/rewrite boundaries now emit typed recoverable abstentions
with stable safe reason codes. The state machine consumes the one physical call,
performs no automatic retry, creates no fake hypothesis/candidate, starts no
qualification for an invalid rewrite, preserves `best_correct`, and advances to
the next level. Transport, credential, filesystem, artifact, toolchain and
qualification exceptions remain terminal.

The real gate validates call order plus linked optimizer events rather than
assuming mandatory rewrites. Three analysis calls are mandatory; each of the
three rewrites is conditional. A valid rewrite must link to a persisted terminal
candidate and real qualification. A contract-invalid call must link to exactly
one typed abstention decision with matching error and reason codes. At least one
generated candidate must complete qualification, so the product executor path is
still exercised. No source-string or regex matcher is introduced.

### S3.7 v9 canonical candidate-index observer correction

The product checkpoint artifact is versioned as `{schema_version, candidates:[...]}`. The real-smoke observer must parse it with the public `candidate_index_from_dict` contract. A flat `{candidate_id: record}` test fixture is non-authoritative and is rejected. This correction changes only acceptance observation and tests; candidate generation, qualification, best-correct selection, model calls, and Vitis execution are unchanged.

### S3.7 closure hygiene: model-call artifact schema v2

S3.7 v8 added safe ``error_reason_codes`` to persisted model-call records but
left the shared writer's artifact schema number at v1. Closure hygiene separates
the model-call artifact version from the broader Structural model integration
version:

```text
MODEL_CALL_ARTIFACT_SCHEMA_VERSION=2
MODEL_CALL_ARTIFACT_SUPPORTED_READ_VERSIONS=1,2
```

New writers always emit v2 and v2 requires ``error_reason_codes``. The public
reader remains compatible with both historical v1 shapes: the original record
without ``error_reason_codes`` and the S3.7 v8/v9 backward-compatible v1
extension that included it. Reading v1 upgrades the in-memory record; subsequent
serialization emits v2. No prompt, model request, candidate generation,
qualification, Vitis, state-machine or product-gate semantics change.
