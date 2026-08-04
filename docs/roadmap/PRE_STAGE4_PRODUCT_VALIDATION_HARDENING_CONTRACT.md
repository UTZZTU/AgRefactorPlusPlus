# Pre-Stage-4 Product and Validation Hardening Contract

> **Status:** design frozen; implementation accepted through P4-0E, including
> P4-0E-R1 master-contract network evidence closure; full Pre-Stage-4 closure
> remains incomplete.
>
> **Accepted checkpoints:** P4-0A documentation freeze, P4-0B typed Preflight,
> P4-0B-R bounded Optimize Candidate recovery, P4-0C Public native Vitis CSIM,
> P4-0D Public RTL COSIM, and P4-0E model runtime/network evidence.
>
> **Current next package:** `P4-0F`.
>
> **Repository baseline originally reviewed:** `84b6fac0a00469fc9651f5f6553b50febedb21c7`
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

A Candidate-owned failure first stops that Candidate before later expensive
stages. After P4-0B-R is implemented, only explicitly eligible Preflight or
CSYNTH-legality failures may create one bounded repair descendant. Otherwise the
Candidate is rejected. In all cases `best_correct` is preserved and optimizer
continuation remains subject to policy and budget.

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

<!-- PRE_STAGE4_P4_0B_R_CONTRACT:BEGIN -->
### 4.6 Bounded Optimize Candidate recovery

Optimize and the Optimize phase of Full may perform one bounded recovery for a
root Candidate only when typed agent-safe evidence proves a Candidate-owned
Preflight or CSYNTH-legality failure.

The repair creates a new `cand-N` descendant, preserves the originating
hypothesis and explicit lineage, and restarts qualification from
Source/Preflight. It never overwrites the failed Candidate or changes
`best_correct` before complete qualification and objective comparison.

The initial contract excludes Testbench, Reference, toolchain, configuration,
unknown ownership, unisolated final link, Hidden, COSIM, PPA, timing, resource,
and non-improvement failures. Public CSIM repair is deferred until native CSIM
exists and remains default-off unless separately accepted.

See
[`PRE_STAGE4_P4_0B_R_BOUNDED_OPTIMIZE_CANDIDATE_RECOVERY_CONTRACT.md`](PRE_STAGE4_P4_0B_R_BOUNDED_OPTIMIZE_CANDIDATE_RECOVERY_CONTRACT.md).
<!-- PRE_STAGE4_P4_0B_R_CONTRACT:END -->

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
→ P4-0B-R bounded Optimize Candidate recovery
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

### 8.1 Frozen real-validation cadence

The implementation order also freezes when real tools and network models become
authoritative evidence. Deterministic tests remain necessary but never stand in
for real Vitis or real provider execution.

```text
P4-0C
  real committed sample(s)
  + real Public native Vitis CSIM invocation
  + typed invocation/evidence/order checks
  network LLM output is not an acceptance dependency;
  fixed or deterministic Candidate material is allowed to isolate CSIM

P4-0D
  real committed sample(s)
  + real Public native Vitis CSIM
  + real CSYNTH
  + real Public RTL COSIM
  + Hidden final gate
  network LLM output is still not the core acceptance dependency

P4-0E
  first post-hardening real network-model smoke
  + real sample
  + selected endpoint/API-key environment
  + .env precedence
  + Thinking/reasoning evidence
  + proof that secrets and private reasoning are not persisted

P4-0F
  measured real end-to-end runs on the stable validation pipeline
  + observed LLM/tool/compile/CSIM/CSYNTH/COSIM/wall-time consumption
  + evidence-based mode defaults and Full Optimize reserves
  no stable model-quality or PPA-superiority claim

P4-0G
  targeted real network-model Optimize and Full smoke
  + real bottleneck diagnosis/action selection
  + generated Candidate
  + complete qualification and rollback/best_correct evidence
  stochastic rewrite success is not required for every run

P4-0H
  formal repeated multi-kernel revalidation
  + real network LLM
  + real Vitis pipeline
  + Refactor/Optimize/Full
  + fault injection
  + budget exhaustion
  + infrastructure-failure accounting
  this is the authoritative Pre-Stage-4 real-evidence matrix
```

Earlier targeted real smokes are required to expose integration failures near
the package that introduces them; they do not replace P4-0H. Conversely, P4-0H
must not be the first time the post-hardening code calls a real provider.

Historical Stage 3 network/Vitis evidence remains valid for its original
contract and commit only. It does not prove the P4-0C through P4-0G pipeline.

Every real-network run continues to obey:

```text
user-selected/frozen model and endpoint
one shared hard BudgetManager
no secret persistence
no private reasoning persistence
Hidden source/details excluded from model-visible evidence
exact repository commit and artifact identity
```

## 9. Pre-Stage-4 closure criteria

Stage 4 may start only when all are true:

- typed Preflight ownership is proven with injected failures;
- bounded Optimize Candidate recovery has one-attempt lineage, restart,
  budget, Hidden-suppression, and `best_correct` evidence;
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

<!-- PRE_STAGE4_P4_0B_TYPED_PREFLIGHT:BEGIN -->
## P4-0B implementation checkpoint

The global typed Preflight implementation is tracked by:

- [`PRE_STAGE4_P4_0B_DECISION_RECORD.md`](PRE_STAGE4_P4_0B_DECISION_RECORD.md)
- [`p4_0b_typed_preflight_acceptance.md`](../acceptance/pre-stage4/p4_0b_typed_preflight_acceptance.md)

```text
P4_0B_TYPED_PREFLIGHT_IMPLEMENTED=true
P4_0B_TYPED_PREFLIGHT_ACCEPTANCE=accepted_local_validation
P4_0B_FOCUSED_TESTS=64
P4_0B_FULL_REGRESSION_TESTS=2044
NEXT_PRE_STAGE4_PACKAGE_AT_ACCEPTANCE=P4-0B-R_BOUNDED_OPTIMIZE_CANDIDATE_RECOVERY
```

P4-0B changes host Preflight only. Its repository closure also freezes the
separate P4-0B-R recovery contract; it does not implement Optimize recovery,
native Vitis CSIM, COSIM, model configuration, mode-specific budgets, or
`dynamic-v1`.
<!-- PRE_STAGE4_P4_0B_TYPED_PREFLIGHT:END -->

<!-- PRE_STAGE4_P4_0C_NATIVE_VITIS_CSIM:BEGIN -->
## P4-0C Public native Vitis CSIM checkpoint

```text
P4_0C_PUBLIC_NATIVE_VITIS_CSIM_IMPLEMENTED=true
P4_0C_UNIFIED_STAGE_ORDER=true
P4_0C_PUBLIC_BACKEND=native_vitis
P4_0C_HIDDEN_BACKEND=host_differential
P4_0C_NETWORK_LLM_USED=false
P4_0C_NEW_FOCUSED_TESTS=23
P4_0C_FULL_REGRESSION=2089
P4_0C_REAL_VITIS_SMOKE=accepted
P4_0C_CACHE_PIPELINE=prestage4-native-vitis-csim-v1
P4_0C_PUBLIC_CSIM_OPTIMIZE_RECOVERY=false
P4_0C_CANDIDATE_REPAIR_PREFIX=task_aware
P4_0C_STAGE2_SMOKE_ORDER=preflight_public_csynth_hidden
P4_0C_STAGE2_SMOKE_BUDGET=5_tool_2_compile_1_csynth_2_csim
NEXT_PRE_STAGE4_PACKAGE_AT_ACCEPTANCE=P4-0D_PUBLIC_RTL_COSIM
```

P4-0C executes the Public suite through actual Vitis HLS `csim_design`,
with Candidate as the design source and Original/reference plus Public
Testbench as `-tb` sources. Refactor and Optimize now share the order
Preflight → Public native CSIM → CSYNTH → Hidden. Hidden remains an
independent operator-only host differential gate.

See the [decision record](PRE_STAGE4_P4_0C_DECISION_RECORD.md) and
[acceptance](../acceptance/pre-stage4/p4_0c_public_native_vitis_csim_acceptance.md).
<!-- PRE_STAGE4_P4_0C_NATIVE_VITIS_CSIM:END -->

<!-- PRE_STAGE4_P4_0D_PUBLIC_RTL_COSIM:BEGIN -->
## P4-0D Public RTL COSIM checkpoint

```text
P4_0D_PUBLIC_RTL_COSIM_IMPLEMENTED=true
P4_0D_PUBLIC_RTL_COSIM_ACCEPTANCE=accepted_real_vitis
P4_0D_UNIFIED_STAGE_ORDER=preflight_public_native_csim_csynth_public_rtl_cosim_hidden
P4_0D_COSIM_POLICY_DEFAULT=required
P4_0D_COSIM_DEFAULT_TIMEOUT_S=900
P4_0D_COSIM_TIMEOUT_SAFETY_CEILING=7200
P4_0D_COSIM_REPAIR=false
P4_0D_FOCUSED_TESTS=7
P4_0D_FULL_REGRESSION=2096
P4_0D_REAL_VITIS_SMOKE=accepted
P4_0D_ACCEPTED_COMMIT=b543604cd311eab4380987b09447842542e3214b
P4_0D_REPOSITORY_CLOSURE=accepted
NEXT_PRE_STAGE4_PACKAGE_AT_ACCEPTANCE=P4-0E
STAGE4_ALLOWED=false
```

P4-0D proves the real Vitis 2023.2 chain `Preflight → Public native Vitis CSIM
→ CSYNTH → Public RTL COSIM → Hidden`, including physical invocation, timeout,
ownership, cache identity and hard COSIM accounting. Network LLM output was not
an acceptance dependency.
<!-- PRE_STAGE4_P4_0D_PUBLIC_RTL_COSIM:END -->

<!-- PRE_STAGE4_P4_0E_MODEL_RUNTIME:BEGIN -->
## P4-0E model runtime and network evidence checkpoint

```text
P4_0E_MODEL_RUNTIME_IMPLEMENTED=true
P4_0E_MODEL_RUNTIME_ACCEPTANCE=accepted_real_network
P4_0E_DEFAULT_MODEL=deepseek-v4-flash
P4_0E_DEFAULT_API_KEY_ENV=DEEPSEEK_API_KEY
P4_0E_REASONING_DEFAULT=auto
P4_0E_DOTENV_OVERRIDE=false
P4_0E_DEEPSEEK_THINKING=true
P4_0E_FOCUSED_TESTS=8
P4_0E_FULL_REGRESSION=2104
P4_0E_ACCEPTED_RUN_ID=p4_0e_model_runtime_v9_20260804T123830Z_3215756
P4_0E_ACCEPTED_BEHAVIOR_COMMIT=eabb2b7e7f5123f3e3f90fe6b6aa0f4a16c6c4a7
P4_0E_R1_NETWORK_EVIDENCE_CLOSURE=accepted
P4_0E_R1_ACCEPTED_COMMIT=81804dff2c846b4f79d636cc412fca5b33eca8eb
P4_0E_R1_FOCUSED_TESTS=4
P4_0E_R1_FULL_REGRESSION=2108
P4_0E_R1_SHARED_BUDGET_MANAGER=true
P4_0E_R1_LLM_CALLS=1
P4_0E_R1_EXACT_ONCE=true
P4_0E_REPOSITORY_CLOSURE=accepted
NEXT_PRE_STAGE4_PACKAGE=P4-0F
STAGE4_ALLOWED=false
```

P4-0E and P4-0E-R1 together prove default/override model truthfulness, CWD
`.env` precedence, typed credential gating, role-specific Thinking/reasoning,
secret/private-reasoning suppression, one real committed-sample provider call,
one shared hard `BudgetManager`, exact-once accounting, and exact repository and
artifact identity. They do not implement P4-0F budgets, P4-0G `dynamic-v1`, or
later behavior.
<!-- PRE_STAGE4_P4_0E_MODEL_RUNTIME:END -->
