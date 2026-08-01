# Stage 3.4 Structural Model Integration Acceptance

## Result

```text
PACKAGE=S3.4_STRUCTURAL_MODEL_INTEGRATION
BASELINE_COMMIT=7e55aae15bbae7f9bd236dd4fc4832558e806f8b
STATUS=accepted_after_required_apply_gates
S34_FOCUSED=52/52
OPTIMIZER_REGRESSION=233/233
FULL_DETERMINISTIC_REGRESSION=1741/1741
REAL_STRUCTURAL_SMOKE=accepted
REAL_MODEL_CALLS=2
REAL_NETWORK_CALLED=true
REAL_TOOL_CALLS=0
REAL_COMPILE_CALLS=0
REAL_CSIM_CALLS=0
REAL_CSYNTH_CALLS=0
REAL_VITIS_CALLED=false
PRODUCT_OPTIMIZE_FULL_ENABLED=false
NEXT_PACKAGE=S3.5_BOTTLENECK_MODEL_INTEGRATION
```

The installer retains this payload only after the deterministic suites and the
bounded real-network smoke pass. The concrete run ID, selected logical model,
token usage, generated source hash, and artifact root are written to the
external apply audit directory printed by `apply_s3_4.sh`; volatile machine
paths are intentionally not copied into this repository document.

## Accepted implementation

- deterministic Structural hypothesis layered prompt and prompt identity;
- agent-safe parent-source/evidence context with Hidden-source isolation;
- strict versioned hypothesis JSON with at most three provider-ordered items;
- adapter-owned deterministic hypothesis IDs;
- provider-neutral real model registry integration;
- complete-source Structural rewrite prompt;
- reuse of the accepted `CandidateResponseContract` for whole-source and
  top-interface protection;
- safe model-call audit stream without raw prompt/response persistence;
- observed token/cost accounting plus shared physical LLM hard-budget slots;
- explicit generated-source → qualification adapter boundary;
- S3.3 state-machine injection with exact parent source and dynamic
  network/Vitis trace flags;
- bounded two-call real model smoke and reproducible artifacts.

## Required deterministic evidence

```text
python -m unittest \
  tests.test_optimizer_structural_prompt \
  tests.test_optimizer_structural_model \
  tests.test_optimizer_structural_smoke_tool

result=52/52
```

```text
python -m unittest discover -s tests -p 'test_optimizer*.py'

result=233/233
```

```text
python -m unittest discover -s tests

result=1741/1741
```

The S3.4 focused suite covers prompt determinism, strict JSON, complete-source
validation, top-interface protection, Hidden isolation, safe artifacts,
model-usage accounting, budget preflight/failure paths, state-machine
injection, exact two-call smoke accounting, and source-path containment.

## Required real evidence

A successful installation runs:

```text
hypothesis request → strict typed hypotheses
selected hypothesis → complete replacement source
```

The successful path must report exactly two LLM calls. It must not invoke Vitis,
compile, CSIM, CSYNTH, Hidden evaluation, PPA comparison, or the product
`optimize/full` command. The smoke proves only the real model/prompt/response
integration contract.

It does not prove that the generated source is functionally correct,
synthesizable, feasible, faster, or broadly effective across kernels or models.
Those claims remain gated by S3.5–S3.8 qualification and evaluation evidence.

## Historical static-gate lesson retained

S3.4 introduces no source-string, regex, pragma-count, or loop-spelling
classifier as an authoritative Structural gate. Level ownership is explicit in
typed state. Unknown semantic properties are not guessed into rejection. Model
output shape is validated strictly, while correctness and performance remain
evidence-driven.

## Protected boundaries

- existing `refactor` behavior is unchanged;
- product `optimize/full` remains gated;
- S3.1 state/checkpoint and S3.2 qualification/PPA/cache semantics are not
  rewritten;
- S3.3 policy, selection, rollback, checkpoint, and best-pointer semantics are
  preserved;
- Bottleneck/Pragma consumers are not implemented early;
- no commit or push is performed by the package.
