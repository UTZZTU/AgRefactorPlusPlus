# Stage 3.6 Pragma Model Integration Acceptance

## Result

```text
STAGE3_S3_6_PRAGMA_MODEL_INTEGRATION=accepted
BASELINE=f5a46d62cca864828e6d1ec3bbe7c5b2ef200f8a
S36_FOCUSED=75/75
OPTIMIZER_REGRESSION=382/382
FULL_DETERMINISTIC_REGRESSION=1890/1890
REAL_PRAGMA_SMOKE=accepted
REAL_LLM_CALLS=2
NETWORK_CALLED=true
VITIS_CALLED=false
COMPILE_CALLED=false
CSIM_CALLED=false
CSYNTH_CALLED=false
ACTION_AUTHORITATIVE=false
RAW_REPORT_USED=false
STATIC_PRAGMA_GATE_USED=false
HIDDEN_EVIDENCE_EXPOSED=false
CLAIM_SCOPE=pragma_model_contract_only
PRODUCT_OPTIMIZE_FULL_GATE_UNCHANGED=true
NEXT_PACKAGE=S3.7_PRODUCT_ADAPTERS
```

## Accepted capability

S3.6 adds:

- typed `PragmaKind`, `PragmaTargetKind`, confidence and directive parameters;
- strict versioned action/hypothesis JSON response contract;
- exact typed PPA evidence linkage and leaf signal fields;
- first-class `unknown` safe abstention;
- immutable non-authoritative Pragma action artifacts;
- complete-source Pragma rewrite through the accepted candidate response contract;
- provider-neutral model registry, observed token/cost accounting and shared LLM budget;
- deterministic three-level dispatch coverage;
- fresh-process prompt-module import coverage without optimizer-package cycles;
- bounded two-call real model smoke;
- explicit qualification adapter boundary.

## Safety assertions

The acceptance does not claim:

- that a model-proposed target exists;
- that a directive is legal for every tool version;
- candidate correctness or synthesizability;
- objective feasibility or PPA improvement;
- product `optimize/full` availability;
- multi-kernel stability.

No source-string, pragma-count, loop-regex or warning-pattern gate is used as
authority. `target_ref` is an audited model description and downstream
qualification remains authoritative.

## Output policy

The real smoke uses:

```text
analysis max_tokens=32768
rewrite max_tokens=32768
safety ceiling=65536
provider JSON mode=false
DeepSeek bounded-smoke thinking=disabled
automatic retry=false
```

## Real smoke artifacts

The accepted target-host run writes:

```text
optimizer/model_calls.jsonl
optimizer/pragma_actions/pragma-*.json
optimizer/hypotheses/hyp-*.json
optimizer/candidates/cand-1/source.cpp
summary.json
```

Raw prompts, raw responses and Hidden content are not persisted.
