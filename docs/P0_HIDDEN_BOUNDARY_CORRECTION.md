# P0 Step B: One-Way Hidden Evaluation Boundary

## Baseline

```text
BASE_HEAD=ab680d0f6abdda9956de8c18250036eca1fb487e
ACTIVE_STEP=B
DEFAULT_LLM_CALLS=32
```

## Closed reverse channels

- Public generation no longer accepts `hidden_sig_spec`.
- Public generation no longer accepts a Hidden-derived declaration.
- Cached held-out Testbenches cannot be used as Public Testbenches.
- Candidate generation completes before held-out generation starts.
- Testbench and Candidate repair receive only Public evidence.

## Frozen Public ABI

The Public Testbench establishes the Candidate declaration. Its normalized
verbatim form and SHA-256 are persisted before Candidate generation. Held-out
generation receives only Original source and this frozen Public-derived ABI.

## Evidence

`bootstrap/model_data_boundary.json` records generation order, allowed inputs,
forbidden reverse-input lists, Public ABI identity, and completeness. Product
metadata derives `hidden_testbench_exposed_to_model` from this manifest with
fail-closed semantics.

```text
STEP_B=completed
ACTIVE_STEP=C
MODEL_API_CALLED=false
VITIS_RUN=false
P0_STATUS=active_retry_required
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```
