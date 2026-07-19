# Stage 2.7.2 Minimal ModelFamilyProfile Acceptance

## Status

```text
a9ec856540940f1767fe245a3c662468293fda5b
feat: add minimal model family profiles

32/32 targeted
792/792 full unittest
```

## Typed profile

The implementation adds:

```text
ModelCapabilityTag
ModelFamilyProfile
```

Supported minimal tags:

```text
reasoning_model
code_specialized
strict_instruction
thinking_tag_possible
strict_completion
```

## Fixed-model authority

The caller still selects one logical model by name. `ModelRegistry` resolves
that exact model, its provider, and its family profile. The profile never
selects, ranks, switches, retries with, or substitutes another model.

## Safe defaults

Effective request parameters use:

```text
profile safe defaults
< ModelSpec defaults
< explicit call overrides
```

Profile defaults must be finite JSON and reject credential-like keys at any
nesting level. API credentials remain environment-only.

## Prompt and contract boundary

Candidate and Testbench prompts receive the same generic profile instruction
and a non-sensitive manifest containing only profile name and capability tags.

The profile does not:

- accept an invalid model response;
- remove or bypass the CandidateResponseContract;
- weaken the TestbenchRepairContract;
- expose Hidden evidence;
- invoke tools or network services.

## Execution class

```text
deterministic profile acceptance: true
network model executed: false
real tool executed: false
automatic model routing: false
response contract relaxed: false
```

Acceptance directory:

```text
/data/agrefactor_runs/stage2_7_2_model_family_profile_v3_20260719_183938/acceptance
```

Next milestone:

```text
Stage 2.7.3 Stage 1 Hardening Batch A
```
