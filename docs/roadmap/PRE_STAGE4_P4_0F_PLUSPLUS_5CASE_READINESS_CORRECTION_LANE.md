# Pre-Stage-4 P4-0F PlusPlus 5-Case Readiness Correction Lane

## 1. Authority and purpose

This document freezes the correction route discovered after the interrupted
Legacy Differential Batch A v1.1 diagnostic campaign.

It supplements, but does not rewrite, the existing
`PRE_STAGE4_LEGACY_DIFFERENTIAL_AND_REAL_CODE_DISCOVERY_EXECUTION_CONTRACT.md`.

Frozen product authority at lane entry:

```text
branch = stage2-general-feedback
HEAD   = 37cdf62f8149cb29a2ba156e309d718ddf9c7f05
parent = 80cb3480eb622ab36ee0e85fcf93d39a81cb2688

P4_0F_R5_ACCEPTED=true
P4_0F_P0_BUDGET_TERMINAL_CHECKPOINT_VERIFIED=true

P4_0F_COMPLETE=false
PRE_STAGE4_COMPLETE=false
STAGE4_ALLOWED=false
```

The purpose of this lane is to make the **AgRefactor++ product itself** ready
for a fresh five-case refactor campaign before resuming Original-vs-PlusPlus
differential comparison.

The five readiness cases remain:

```text
dfs
ahocorasick
strassen
linkedlist
mergesort
```

No diagnostic result from Legacy Differential v1/v1.1 is acceptance evidence.

## 2. Naming rule

The correction steps below use the prefix `PR` for **PlusPlus Readiness**.

They are intentionally **not** named P4-0F R1/R2/... because the accepted
P4-0F R5 chain is already frozen and must not be confused with this correction
lane.

Frozen order:

```text
PR-R1  DFS existing-candidate typed CSIM/COSIM replay
PR-R2  General candidate symbol-isolation / ownership correction
PR-R3  Large-generation output / truncation / accounting correction
PR-R4  PlusPlus-only five-case campaign harness correction
PR-R5  Fresh PlusPlus-only five-case campaign
PR-R6  Stage0-era Original authority recovery
PR-R7  Fresh Legacy Differential Batch A v2
```

After PR-R7 acceptance, resume:

```text
Real-code discovery batch A
→ P4-0F-Final
```

## 3. Global invariants

All PR steps must preserve:

- evidence-first, correctness-first, typed evidence, unknown-safe;
- regex/static string evidence is never an authoritative gate;
- no hidden-suite details may enter model prompts;
- no Git worktree/history mutation is performed by execution packages;
- product code corrections require focused regression + full regression;
- every real campaign after a product correction uses a fresh root;
- a false acceptance, identity mix, Hidden leak, budget bypass, or repository
  mutation stops the campaign globally;
- already accepted P4-0F R5 and P0 evidence is not retroactively invalidated
  unless new evidence contradicts its original scope.

## 4. PR-R1 — DFS existing-candidate typed replay

### Input authority

Use the already generated DFS candidate from the v1.1 diagnostic archive:

```text
diagnostic archive SHA256 =
27d068183810e351ef8e3e683799cc70d00869b235d1d6ce058d6a857313711e

candidate SHA256 =
a4ce166be58e5afe7692e6442fea16579bdb5e432a84e18e882a903753b5c625
```

Reference source:

```text
src/heterorefactor/dfs/kernel.cpp
Git blob = 8bcc391e648c18620a7b9d0cc6c11f655d379031
reference top = process_top
candidate top = process_top_hls
```

Reuse the exact v1.1 Public fixture:

```text
fixture SHA256 = 78ffaafd1abf54439a9d55769ec93d7b7c9a63b8b24a24a6c1d47157604b3a19
```

but attach the explicit Public runtime contract:

```json
{
  "schema_version": 1,
  "kind": "public_differential_self_check_v1",
  "candidate_mismatch_returncodes": [1]
}
```

PR-R1 performs **zero model calls**.

It must run a fresh staged host preflight, typed native Vitis CSIM, and the
full Vitis RTL COSIM chain (which contains its own CSIM and CSYNTH
prerequisites).

### PR-R1 branch A — replay passes

If typed native CSIM and typed RTL COSIM both pass:

```text
P4_0F_PR_R1_TYPED_REPLAY_PASSED=true
P4_0F_PR_R1_DFS_PRODUCT_CORRECTION_REQUIRED=false
NEXT=PR-R2
```

Interpretation: the v1.1 DFS rejection was caused by the comparison harness
failing to carry the Public runtime contract. Do not patch the DFS product path
for this symptom.

### PR-R1 branch B — deterministic RTL candidate mismatch

If CSIM passes but COSIM returns:

```text
failure_owner=candidate
owner_authority=deterministic_proven
reason_code=public_rtl_mismatch
```

then:

```text
P4_0F_PR_R1_DFS_RTL_MISMATCH_PROVEN=true
P4_0F_PR_R1_DFS_PRODUCT_CORRECTION_REQUIRED=true
NEXT=PR-R1B
```

`PR-R1B` is a general Public CSIM-vs-RTL-COSIM consistency diagnosis and
correction lane. It must not hard-code DFS.

PR-R2 is blocked until PR-R1B is accepted.

### PR-R1 branch C — unknown/toolchain/configuration

Any other COSIM failure is not candidate proof:

```text
P4_0F_PR_R1_UNKNOWN_OR_TOOLCHAIN=true
NEXT=PR-R1C
```

`PR-R1C` must repair the general evidence/toolchain/configuration boundary
before proceeding.

## 5. PR-R2 — General candidate symbol isolation and ownership

The Aho-Corasick diagnostic exposed a **general** collision class, not an
Aho-specific rule.

Required product behavior:

1. reference and candidate compile independently;
2. required reference/candidate top ABI remains authoritative;
3. inspect object-symbol evidence for non-top external symbol collisions;
4. if candidate-owned symbols collide with the frozen reference and ownership
   is deterministically provable, emit a typed candidate-owned failure;
5. route to bounded candidate repair;
6. never infer ownership from linker text alone.

The generic repair contract should tell the candidate generator/repairer that
candidate-local helper functions and globals must use internal linkage,
anonymous namespaces, or unique candidate-local names when the differential
validator links reference and candidate together.

Required synthetic regressions include:

```text
helper-function collision
global-variable collision
same name / different type
same name / different signature
static helper does not collide
anonymous-namespace helper does not collide
required top ABI remains unchanged
ambiguous ownership remains UNKNOWN
```

No Aho-specific function or symbol name may appear in the authoritative
implementation gate.

## 6. PR-R3 — Large-generation output, truncation, and accounting

At `37cdf62f8149cb29a2ba156e309d718ddf9c7f05` the shared family output policy is:

```text
artifact default = 32768 tokens
family safety ceiling = 65536 tokens
```

The Strassen diagnostic ended with a provider response whose completion token
count reached exactly 32768 and no candidate was extracted.

### Proposed target policy

For the exact verified DeepSeek V4 Flash concrete-model record:

```text
candidate default output        = 150000
candidate-repair default output = 150000
concrete-model safety ceiling   = 300000
```

These values are a **proposal frozen by this route**, not an unconditional
family-wide claim.

Before implementation acceptance PR-R3 must verify that the selected provider
deployment supports the requested bound. If the provider reports a lower
maximum, the effective concrete-model limit must remain at or below verified
provider capability.

Do **not** raise the shared family policy for Kimi/GLM/MiniMax/Qwen/generic
OpenAI-compatible models merely because DeepSeek V4 Flash supports a larger
output.

### Required evidence correction

Provider calls must be recorded even when candidate extraction fails.

At minimum persist safe evidence for:

```text
requested_max_tokens
prompt_tokens
completion_tokens
total_tokens
finish_reason (when provider supplies it)
response_content_present
candidate_extraction_status
candidate_extraction_reason
provider_call_observed
```

Do not persist private chain-of-thought/reasoning text.

A provider output exhausted at its effective limit and lacking a complete
candidate must produce a typed generation/truncation outcome rather than only:

```text
ValueError: generation-only backend returned no candidate_code
```

Accounting invariant:

```text
actual provider calls == product provider-call evidence count
```

including terminal failed generation.

## 7. PR-R4 — PlusPlus-only campaign harness correction

Before the next five-case campaign:

### Public runtime contract

Every deterministic Public differential fixture must carry the explicit typed
runtime contract. Testbench source text or return-code conventions are not
authority by themselves.

### Vitis hard-budget wrapper

The wrapper must bind through the actual product authority:

```text
REAL_VITIS_RUN=<resolved real executable>
AGREFACTOR_VITIS_RUN=<shared budget/evidence wrapper>
```

Changing `PATH` alone is insufficient when `AGREFACTOR_VITIS_RUN` is already
set.

The wrapper evidence and product typed usage must be reconciled before campaign
acceptance.

### Model fairness evidence

Record separately:

```text
requested project reasoning effort
effective project reasoning effort
effective provider reasoning effort
```

For the current DeepSeek policy, project `medium` may legitimately map to
provider `high`; this is not a product defect.

## 8. PR-R5 — Fresh PlusPlus-only five-case campaign

Only after PR-R1 (or PR-R1B/R1C), PR-R2, PR-R3, and PR-R4 are accepted.

Run:

```text
dfs
ahocorasick
strassen
linkedlist
mergesort
```

through the real AgRefactor++ `refactor` product flow.

A product success may use bounded recovery. The first generated candidate does
not need to be perfect.

Acceptance is based on the final typed product qualification, not on whether
attempt 1 passed.

Required formal result:

```text
P4_0F_PR_R5_PLUSPLUS_5CASE_CAMPAIGN_ACCEPTED=true
```

before Original differential work resumes.

## 9. PR-R6 — Recover Stage0-era Original authority

Do this only after PR-R5.

Historical Git candidate already identified for later audit:

```text
08f18c82d7bcd35d407b18c703f139f3e3e52697
Update documentation for reproduced workflows
```

Its historical documentation states that DFS single-kernel refactor and
DeepSeek V4 Flash / Pro end-to-end execution had been reproduced.

This commit is a **search lead, not yet frozen Original authority**.

PR-R6 must identify the exact Stage0-complete / pre-Stage1 commit whose real
Original-style `flow.new` path was modified for DeepSeek and had executable
reproduction evidence. Freeze that exact tree and dependency contract.

## 10. PR-R7 — Fresh Legacy Differential Batch A v2

Only after PR-R5 and PR-R6.

Compare the recovered historical Original authority against the accepted
PlusPlus checkpoint under a corrected fairness harness.

Original runtime dependency incompatibility must be typed as
`baseline_unavailable_runtime_dependency`; repository clone/import alone is
not sufficient to claim that the Original arm is available.

## 11. Current formal state

At document creation:

```text
P4_0F_R5_ACCEPTED=true
P4_0F_P0_BUDGET_TERMINAL_CHECKPOINT_VERIFIED=true

P4_0F_PR_ROUTE_FROZEN=true

P4_0F_PR_R1_ACCEPTED=false
P4_0F_PR_R2_ACCEPTED=false
P4_0F_PR_R3_ACCEPTED=false
P4_0F_PR_R4_ACCEPTED=false
P4_0F_PR_R5_PLUSPLUS_5CASE_CAMPAIGN_ACCEPTED=false
P4_0F_PR_R6_ORIGINAL_AUTHORITY_FROZEN=false
P4_0F_PR_R7_LEGACY_DIFFERENTIAL_ACCEPTED=false

P4_0F_COMPLETE=false
PRE_STAGE4_COMPLETE=false
STAGE4_ALLOWED=false
```

No later step may silently reorder this route. A change requires an explicit
decision record explaining the new evidence that justified the change.
