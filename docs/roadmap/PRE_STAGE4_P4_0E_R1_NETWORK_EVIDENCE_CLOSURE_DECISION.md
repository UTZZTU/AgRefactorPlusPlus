# Pre-Stage-4 P4-0E-R1 Network Evidence Closure Decision

> **Design source:** `PRE_STAGE4_PRODUCT_VALIDATION_HARDENING_CONTRACT.md`.
>
> **Authoritative implementation parent:**
> `eabb2b7e7f5123f3e3f90fe6b6aa0f4a16c6c4a7`.
>
> **Behavior boundary:** evidence harness correction only; no model-runtime,
> optimizer, budget-default, CLI-surface, Vitis, or Stage-4 behavior change.

## Reason

The P4-0E implementation proved default DeepSeek V4 Flash selection, invocation
CWD `.env` loading, typed credential gating, role-specific Thinking/reasoning,
secret suppression, private-reasoning suppression, and a real network transport
smoke. The Pre-Stage-4 master contract additionally requires every real-network
run to use one shared hard `BudgetManager` and retain exact repository commit and
artifact identity.

P4-0E-R1 closes only those two horizontal evidence requirements.

## Frozen correction

The committed smoke must:

1. run only from a clean `stage2-general-feedback` checkout at an exact expected
   commit;
2. prove the committed sample is tracked and record its Git blob and SHA-256;
3. create one shared `BudgetManager` with `max_llm_calls=1`;
4. prospectively check `llm_calls=1` before provider launch;
5. consume exactly once immediately before the physical provider attempt;
6. record observed token/cost usage without changing the LLM-call count;
7. retain run ID, artifact root, package-manifest identity, repository identity,
   model/endpoint/API-key environment name, safe call policy, and artifact
   identity hash;
8. persist no credential value, `.env` contents, raw provider error, raw private
   reasoning, or Hidden material.

The correction does not select P4-0F mode defaults or Full reserves. It does not
claim model quality, arbitrary-kernel support, optimization success, or PPA
superiority.

## Acceptance order

```text
exact parent + clean branch
→ full tracked shadow patch
→ 4 focused deterministic tests
→ complete regression 2108/2108
→ real checkout patch and same tests
→ local correction commit
→ clean exact committed HEAD verification
→ 4 focused tests + 2108/2108 on committed HEAD
→ one real budgeted network smoke
→ artifact/hash verification
→ manual push
→ P4-0E authority-state synchronization
```

Stage 4 remains forbidden.
