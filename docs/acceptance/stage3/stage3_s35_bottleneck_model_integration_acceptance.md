# Stage 3.5 Bottleneck Model Integration Acceptance

## Result

```text
PACKAGE=S3.5_BOTTLENECK_MODEL_INTEGRATION
STATUS=accepted
BASELINE=c881dea0ea68f0fbf7c2b359bd270f362827a90f
S35_FOCUSED=82/82
OPTIMIZER_REGRESSION=315/315
FULL_DETERMINISTIC_REGRESSION=1823/1823
REAL_BOTTLENECK_SMOKE=accepted
REAL_LLM_CALLS=2
ANALYSIS_JSON_AUTHORITY=local_strict_response_contract
ANALYSIS_PROVIDER_JSON_MODE=false
THINKING_MODE_CONTROL=disabled
NETWORK_CALLED=true
VITIS_CALLED=false
COMPILE_CALLED=false
CSIM_CALLED=false
CSYNTH_CALLED=false
NEXT_PACKAGE=S3.6_PRAGMA_MODEL_INTEGRATION
```

## Accepted capability

- typed agent-safe projection from accepted-parent `PpaEvidence`;
- raw report and Hidden evidence exclusion;
- strict model classification/hypothesis JSON;
- evidence-ID and signal-field linkage;
- non-authoritative classification artifacts;
- safe `unknown` result with no executable branch;
- complete-source Bottleneck generation with top-interface protection;
- explicit generated-source → qualification boundary;
- shared LLM budget and observed token/cost accounting;
- level-explicit provider/executor dispatch;
- deterministic, immutable, agent-safe artifacts;
- bounded two-call real-model smoke.

## Deterministic coverage

The 79 focused tests cover:

- evidence projection validation and deterministic identity;
- rejection of unaccepted/missing/malformed PPA parents;
- omission of raw report paths/content and Hidden evidence;
- strict JSON and exact field sets;
- classification kinds, confidence, evidence and signal allowlists;
- `unknown` semantics;
- classification/hypothesis linkage;
- immutable artifacts and unsafe-text rejection;
- provider audit on transport/parse/contract failures;
- complete-source and top-interface contracts;
- qualification delegation;
- two-call state-machine Bottleneck step;
- zero rewrite call on budget exhaustion;
- explicit per-level dispatch and budget compatibility;
- real-smoke tool path, budget, summary, and fixture contracts.

## Real smoke claim boundary

The real smoke uses a typed PPA fixture rather than launching Vitis. It proves
that the configured real model can consume the safe evidence projection,
return strict classifications/hypotheses, and produce one contract-valid
complete source in exactly two model calls.

It does **not** claim:

- a model classification is an authoritative tool fact;
- a real CSYNTH report was classified;
- the candidate is functionally correct;
- the candidate synthesizes;
- objective feasibility or PPA improvement;
- multi-kernel stability;
- product `optimize/full` availability.

## Historical-failure guard

No source-string, pragma-count, warning-regex, or incomplete static recognizer
is used as a Bottleneck blocking gate. Insufficient evidence remains `unknown`
or advances safely; it is not force-classified.

## Protected boundaries

```text
refactor behavior unchanged=true
optimize/full product gate unchanged=true
S3.1-S3.4 contracts preserved=true
Pragma model integration implemented=false
commit created by installer=false
push performed by installer=false
```

## Structured-output transport evidence

A pre-closure v1 attempt passed all then-current deterministic tests but the
first DeepSeek JSON-mode call returned an empty `content` and the installer
restored the exact S3.4 baseline. The accepted package keeps the strict local
JSON/schema contract authoritative, disables DeepSeek thinking for the bounded
smoke, omits provider-side JSON mode, and does not add hidden retries. Thus the
accepted path still uses exactly two physical LLM calls.


## Output budget closure

The formal bounded smoke uses the Stage 2 accepted output standard for both
physical model calls: `max_tokens=32768`, with `65536` as the enforced safety
ceiling. This is separate from the run-level observed token accounting.


## Pricing metadata regression closure

The bounded real-call path is additionally gated by a deterministic test that attaches
an explicit `ModelPricingSnapshot`, performs one valid Bottleneck analysis response,
and verifies the accepted typed estimation metadata fields. Nonexistent ad-hoc
`source/version` attributes are forbidden. The observed target-host v3 failure occurred
after a normal `finish_reason=stop` response and was not a max-token exhaustion event.
