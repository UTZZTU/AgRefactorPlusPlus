# Stage 3.7 Product Adapters Acceptance

## Status

```text
PACKAGE=S3.7_PRODUCT_ADAPTERS_V9
BASELINE_COMMIT=197327af79382327f2711119225d47e8ea060e00
STATUS=ACCEPTED_ONLY_AFTER_TARGET_HOST_GATE
S37_FOCUSED=28/28
OPTIMIZER_REGRESSION=402/402
FULL_DETERMINISTIC_REGRESSION=1941/1941
TEST_RUNNER=stdlib_unittest
PYTEST_REQUIRED=false
REAL_ANALYSIS_CALLS=3_mandatory
REAL_REWRITE_CALLS=0_to_3_conditional
REAL_LLM_CALLS_SEMANTIC_RANGE=3_to_6
REAL_LLM_CALLS_ACCEPTED_RANGE=4_to_6
AUTOMATIC_MODEL_RETRY=false
MODEL_CALL_ARTIFACT_WRITE_SCHEMA=2
MODEL_CALL_ARTIFACT_READ_SCHEMAS=1,2
```

The implementation is retained only when the target-host installer completes
all deterministic and real-chain gates. Any failure restores the exact S3.6
baseline. The package does not create a commit or push.

## Accepted product semantics

- normal `optimize` and `full` use the frozen safe-v1 Structural → Bottleneck →
  Pragma state machine;
- direct `optimize` requires an independent reference plus provided Public and
  Hidden suites; `full` requires an accepted typed refactor handoff;
- baseline qualification completes before any optimization model call;
- a model analysis/rewrite response contract failure is a typed recoverable
  abstention, not an infrastructure error;
- the one physical call is accounted exactly, no automatic retry occurs, no
  hypothesis/candidate is fabricated, and an invalid rewrite starts no
  qualification;
- `best_correct` is preserved and the optimizer advances when possible;
- network, credential, filesystem, artifact, toolchain and qualification
  infrastructure errors remain terminal;
- model classifications/actions/hypotheses remain non-authoritative; real
  compile, Public/Hidden CSIM, CSYNTH and typed PPA remain authoritative;
- Hidden evidence never enters model-facing artifacts;
- source-string, pragma-count, loop-regex and warning-pattern heuristics are not
  authoritative gates.

## Internal real-chain gate

```text
baseline qualification
→ Structural analysis → optional rewrite → qualification or typed abstention
→ Bottleneck analysis → optional rewrite → qualification or typed abstention
→ Pragma analysis → optional rewrite → qualification or typed abstention
→ best_correct protection
→ unified artifacts
```

All three physical analysis calls are mandatory. Each rewrite is conditional on
an executable hypothesis and a contract-valid complete-source response. The semantic observer accepts 3–6 calls only after validating order and linked
optimizer decisions; the number itself is not proof. The complete acceptance run
requires at least one generated candidate to complete real qualification, so its
accepted physical range is 4–6 calls. Every valid rewrite must link to a terminal
candidate. Every invalid model response must carry safe reason codes
and link to exactly one no-retry abstention decision.

## Claim boundary

S3.7 proves product wiring, controlled model-output failure handling, one bounded
internal chain and real qualification. It does not establish multi-kernel
success, stable PPA improvement, general model quality, cross-version
portability or superiority to `simple_iter`; those remain S3.8.

V9_CANONICAL_CANDIDATE_INDEX_PARSER=true
V9_REAL_SCHEMA_FIXTURE=true
V9_OBSOLETE_FLAT_SCHEMA_REJECTED=true

MODEL_CALL_V1_WITHOUT_REASON_CODES_READ_COMPATIBLE=true
MODEL_CALL_V1_OPTIONAL_REASON_CODES_READ_COMPATIBLE=true
