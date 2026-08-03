# Pre-Stage-4 Product and Validation Hardening Contract

> **Status:** design frozen; implementation not yet claimed.
>
> **Repository baseline reviewed:** `84b6fac0a00469fc9651f5f6553b50febedb21c7`
>
> This contract freezes the product, validation, model, budget, and CLI changes
> that must close before Stage 4 Memory Applicability Gate begins. It supersedes
> the fixed Stage 3 optimizer ordering as the intended future product behavior,
> while retaining historical `safe-v1` only for reproducibility and comparison.

## 1. Product semantics

### 1.1 `refactor`

```text
ordinary or currently non-synthesizable C/C++
→ refactor generation and bounded repair
→ host preflight with typed failure ownership
→ Public native Vitis CSIM
→ Vitis CSYNTH
→ Public RTL COSIM
→ Hidden differential functional test
→ accepted synthesizable refactor result
```

`refactor` targets functional equivalence and synthesizability. It must not
silently become a performance optimizer.

### 1.2 `optimize`

```text
independently qualified, correct, synthesizable baseline
→ read current CSYNTH/PPA evidence
→ diagnose the current bottleneck
→ select structural, pragma, or joint structural+pragma action
→ generate one bounded candidate
→ host preflight
→ Public native Vitis CSIM
→ Vitis CSYNTH and PPA extraction
→ Public RTL COSIM
→ Hidden differential functional test
→ accept only when correct, feasible, and objectively better
→ otherwise roll back to best_correct
→ diagnose again while budget remains
```

Bottleneck diagnosis is the controller of every optimization round. It is not a
fixed second modification stage.

### 1.3 `full`

```text
complete accepted refactor
→ use refactor/final_candidate.cpp as the optimization baseline
→ run complete optimize
```

The original source remains the functional reference. `full` must never optimize
the original non-synthesizable source directly when refactor has not been
accepted.

## 2. Credential and `.env` contract

### 2.1 Default model and user override

Normal product commands default to:

```text
model_id=deepseek-v4-flash
family=deepseek
base_url=https://api.deepseek.com
api_key_env=DEEPSEEK_API_KEY
```

The following options remain available:

```text
--model MODEL_ID
--model-family FAMILY
--base-url URL
--api-key-env ENVIRONMENT_VARIABLE_NAME
```

The default `--model` value is `deepseek-v4-flash`; it is no longer required on
every normal command. A user may still select another exact model and endpoint.

### 2.2 API key handling

There is no normal `--api-key VALUE` option. Credentials are read only through
environment-variable names.

Supported usage:

```bash
# .env
DEEPSEEK_API_KEY=...
MY_MODEL_API_KEY=...

python -m agrefactor.cli full kernel.cpp \
  --top kernel_top \
  --api-key-env MY_MODEL_API_KEY
```

The normal CLI must load `.env` from the invocation working directory with
`override=False` before resolving model credentials:

```text
already-exported process environment
> local .env value
> missing credential
```

A missing selected variable causes a typed pre-launch rejection. Artifacts may
record the environment-variable name and whether it was present, but must never
record the secret value or `.env` contents.

## 3. DeepSeek V4 Flash thinking and reasoning contract

### 3.1 Normal product policy

Every model call uses internal reasoning when the concrete model/deployment
profile supports it. DeepSeek V4 Flash calls explicitly request:

```json
{
  "thinking": {
    "type": "enabled"
  }
}
```

Private reasoning content, `<think>` blocks, and provider reasoning payloads are
never persisted or exposed as product output.

### 3.2 User-facing reasoning setting

Normal CLI:

```text
--reasoning-effort auto|medium|high
default=auto
```

`auto` selects a role-specific project level:

| Call role | Project level |
|---|---|
| non-synthesizable construct identification | medium |
| Public/Hidden test generation | medium |
| deduplication and simple classification | medium |
| refactor planning | high |
| refactor source generation | high |
| Testbench repair | high |
| Candidate repair | high |
| bottleneck diagnosis | high |
| optimization action selection | high |
| optimization candidate generation | high |

DeepSeek V4 Flash mapping:

```text
project medium → provider high
project high   → provider max
legacy low     → provider high (compatibility only)
```

A user-supplied `medium` or `high` overrides role defaults for all eligible
calls. Other model families use their own typed deployment policy; unsupported
fields are omitted or rejected explicitly, never guessed.

### 3.3 Required evidence

Every call records only safe configuration evidence:

```text
call_role
model_id
provider
requested_reasoning_effort
effective_project_reasoning_effort
effective_provider_reasoning_effort
thinking_requested
thinking_effective
parameter_policy_profile
```

## 4. Unified qualification pipeline

Both `refactor` and `optimize` use:

```text
source integrity
→ host typed preflight
→ Public native Vitis CSIM
→ Vitis CSYNTH
→ Public RTL COSIM
→ Hidden differential functional test
→ final decision
```

### 4.1 Host preflight

Preflight uses independent steps rather than stderr guessing:

```text
compile Testbench-owned unit
compile Original/reference unit
compile Candidate unit
verify required top symbols/interfaces
link differential executable
```

Typed outcomes include:

```text
testbench_compile_failed
reference_compile_failed
candidate_compile_failed
candidate_top_missing
reference_top_missing
interface_mismatch
link_failed
toolchain_failed
configuration_failed
ownership_unknown
```

A Candidate-owned failure rejects only that Candidate, preserves
`best_correct`, skips later expensive stages, and allows the optimizer to
continue when policy and budget permit.

### 4.2 Public native Vitis CSIM

Public validation is a real Vitis HLS C simulation using the Public Testbench.
The current host `g++` differential executor is not called “native Vitis CSIM”.

### 4.3 Vitis CSYNTH

Only the Candidate top is synthesized. The Original/reference implementation
may remain non-synthesizable and is used only as a functional oracle.

### 4.4 Public RTL COSIM

COSIM uses the qualified Public Testbench to compare the C model and generated
RTL. It is an independent hardware-validation stage and never replaces Hidden.

Normal product default:

```text
--cosim-policy required
```

Development-only override:

```text
--cosim-policy off
```

When off, artifacts must state that complete hardware validation was not
performed.

### 4.5 Hidden differential test

Hidden remains the final independent functional gate:

```text
Original/reference behavior
↔ Candidate behavior
```

Hidden source and detailed Hidden diagnostics never enter generation, repair,
or optimization prompts. Hidden failure cannot trigger model-visible automatic
repair.

## 5. Dynamic optimizer contract

Normal future default:

```text
--optimizer-profile dynamic-v1
```

Historical comparison profile:

```text
--optimizer-profile safe-v1
```

`dynamic-v1` loop:

```text
qualify current best_correct and obtain typed PPA
→ DIAGNOSE
→ choose one action:
     structural_only
     pragma_only
     structural_and_pragma
     abstain
→ generate at most one executed branch for the round
→ QUALIFY
→ COMPARE
→ accept or roll back
→ DIAGNOSE again
```

First supported objective:

```text
--optimization-objective latency
```

Resources, clock, correctness, CSIM, CSYNTH, COSIM, and Hidden remain hard
feasibility gates.

Search controls:

```text
--max-optimization-rounds N
--max-executed-candidates N
--hypotheses-per-round N
```

- A round is one diagnosis/selection opportunity, even when it abstains.
- An executed Candidate is one source that enters qualification.
- One round may propose multiple typed hypotheses but executes at most one in
  `dynamic-v1`.

## 6. Mode-specific budget profiles

Normal automatic profile selection:

```text
refactor → refactor-default
optimize → optimize-default
full     → full-default
```

Users retain explicit overrides:

```text
--max-llm-calls
--max-tool-calls
--max-compile-calls
--max-csim-calls
--max-csynth-calls
--max-cosim-calls
--max-wall-time-s
--token-budget
--cost-budget
```

Priority:

```text
system safety ceiling
→ user explicit value
→ selected mode profile default
```

`full-default` uses one shared `BudgetManager` but reserves a minimum Optimize
allowance so Refactor cannot consume all LLM, CSIM, CSYNTH, COSIM, or wall-time
capacity. Concrete defaults and reserves are frozen only after measured real
runs, not by arbitrary multiplication.

COSIM requires real counters and prospective checks:

```text
max_cosim_calls
cosim_calls
cosim_timeout_s
```

No budget field is added before the corresponding real call site exists.

## 7. CLI truthfulness contract

A parameter shown by a command must:

```text
be parsed
→ be consumed by that command
→ change real execution
→ appear in Execution Identity
```

Otherwise it is hidden for that command or rejected explicitly.

### 7.1 Refactor-specific visible surface

Includes model/thinking, Target, Public/Hidden generation, repair, CSIM,
CSYNTH, COSIM, timeouts, budgets, and output settings. It does not show dynamic
optimizer search parameters.

### 7.2 Direct Optimize visible surface

Includes independent reference and provided Public/Hidden suites, model/thinking,
Target, dynamic optimizer controls, CSIM/CSYNTH/COSIM, budgets, and output.
Generation-only test controls are hidden or rejected unless direct Optimize
gains a real consumer.

### 7.3 Full visible surface

Includes all effective Refactor controls, all effective Optimize controls, and
the `full-default` budget profile. Every shared option has one unambiguous
effective value.

## 8. Implementation order

```text
P4-0A  freeze this documentation contract
→ P4-0B global typed Preflight repair
→ P4-0C Public native Vitis CSIM and unified stage order
→ P4-0D Public RTL COSIM, budget, timeout, evidence, ownership, cache
→ P4-0E DeepSeek Flash default, .env loading, Thinking/effort profile,
         and legacy YAML model-default cleanup
→ P4-0F mode-specific budgets, Full reserves, and truthful CLI surface
→ P4-0G dynamic-v1 bottleneck-driven optimizer
→ P4-0H full deterministic, fault-injection, real Vitis, network-model,
         budget-exhaustion, and multi-kernel revalidation
→ P4-0I documentation synchronization and Pre-Stage-4 closure
→ Stage 4 Memory Applicability Gate
```

Each package is independently diagnosable, reversible, and accepted before the
next behavior package starts.

## 9. Pre-Stage-4 closure criteria

Stage 4 may start only when all are true:

- typed Preflight ownership is proven with injected failures;
- Refactor and Optimize both use Public native Vitis CSIM, CSYNTH, Public
  COSIM, and Hidden in the frozen order;
- COSIM has real invocation evidence, timeout, ownership, cache identity, and
  hard budget accounting;
- default Flash and user model/API-key-environment overrides are truthful;
- `.env` loading is tested without secret persistence;
- every DeepSeek call has safe requested/effective Thinking evidence;
- mode-specific budget profiles and Full reserves are validated;
- CLI parameters are consumed or rejected, never silently ignored;
- `dynamic-v1` preserves `best_correct` across rejection, abstention, provider
  error, tool error, and budget exhaustion;
- historical `safe-v1` remains reproducible as a comparison profile;
- Refactor, Optimize, and Full complete fresh real evidence runs;
- authoritative roadmap, state, parameter, usage, and handoff documents match
  the implementation.

## 10. Explicitly deferred

The following do not block Stage 4:

- authorized automatic model routing;
- DeepSeek Pro availability;
- multiple Vitis version migration;
- repository-level migration;
- complete Target executable/settings/parser CLI override;
- separate user-selected models for every internal role;
- general sampling controls such as `temperature` and `top_p`;
- Stage 5 SourceProfile and migration runtime.
