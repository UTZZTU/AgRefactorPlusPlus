# Stage 2 — Structured Evidence Loop

## 1. Purpose

Stage 2 builds a correctness-first, evidence-driven validation and repair
control plane. It is broader than testbench repair.

The full Stage 2 scope is:

```text
Testbench Reliability
+ Public/Hidden Test Roles
+ General Feedback
+ Evidence-driven State Strategy
+ Runtime Evidence-loop Integration
+ Shared Layered Prompt Builder
+ Multi-type Kernel Smoke Matrix
+ Final Documentation and Closure
```

## 2. Milestone map

| Milestone | Status | Accepted meaning |
|---|---|---|
| 2.1 Public/Hidden test roles and evidence | Core complete | Suite identity, split, feedback visibility, redaction, tracing, and multi-suite composition |
| 2.2 General feedback and validation strategy | Core complete | Generic feedback schema, adapters, parser, views, composers, router, states, transitions, and coordinator |
| 2.3 Runtime evidence-loop integration | Core complete | Real Preflight, CSYNTH, Public CSIM, Hidden CSIM, shared budget, safe trace, and orchestration |
| 2.4 Shared Layered Prompt Builder | Complete | Shared contract and consumers, provider-neutral candidate adapter, strict response contract, bounded repair, and safe validation re-entry |
| 2.5 Multi-type Kernel Smoke Matrix | In progress | 2.5.1 corpus and 2.5.2 seven-type real full-chain pass matrix complete; fault/ownership/Hidden matrix remains |
| 2.6 Closure-readiness Audit | Not started | Evidence-based gap classification without premature closure |
| 2.7 Cross-stage Validation and Repair Hardening | Not started | Resolve proven Stage 3 blockers and complete Stage 1 Hardening Batch A |
| 2.8 Final documentation and closure | Not started | Final reproducibility synchronization and formal Stage 2 closure |

Stage 2 is not closed until 2.5–2.8 are complete. Stage 2.4 is complete; no
separate 2.4.4 or 2.4.4.1 milestone is defined by the authoritative roadmap.

## 3. Early foundation: Testbench Reliability

The first Stage 2 implementation was a deep but narrow Testbench Reliability
path:

- compile/link preflight;
- structured failure stage, kind, owner, and next action;
- testbench/candidate/original ownership;
- conservative implementation-private dependency gate;
- ABI/linkage classification;
- bounded testbench-only model repair;
- output and preservation contracts;
- repair artifacts and known usage;
- one real unified CLI + DeepSeek + Vitis stateful-kernel validation.

This work remains active and is not discarded. It now serves as:

- the Testbench Reliability branch of the full validation system;
- the first ownership/routing prototype;
- the first stage-specific prompt and constrained repair prototype.

Acceptance:
[`stage2_acceptance.md`](stage2_acceptance.md).

## 4. Stage 2.1: Public/Hidden test roles and evidence

Completed capabilities include:

- `TestSuiteSpec` and `EvaluationSplit`;
- Public feedback visibility and Hidden non-visibility;
- suite metadata in `TaskSpec`;
- full operator evidence and redacted agent evidence;
- split-aware test evaluation tracing;
- CSIM result adaptation to suite evidence;
- Public/Hidden multi-suite feedback composition;
- Hidden operator-only composition;
- deterministic suite ordering and duplicate checks.

Public and Hidden are roles, not separate physical budget counters.

## 5. Stage 2.2: General feedback and validation strategy

Completed core components include:

- generic `FeedbackCategory`, `FeedbackOwner`, `FeedbackStage`, and severity;
- `FeedbackItem` and `FeedbackReport`;
- Preflight feedback adapter and agent-safe view;
- CSYNTH invocation adapter, diagnostic parser, composer, artifact evaluator,
  and agent-safe view;
- Test Evaluation feedback adapter and split-aware composer;
- deterministic feedback router;
- validation states and transition policy;
- validation feedback coordinator.

Key routing principles:

```text
budget exhausted → blocked
toolchain/config/task input → external remediation
testbench-owned public failure → repair_testbench
candidate-owned public failure → repair_candidate
original-owned failure → repair_original
unknown or mixed ownership → review_required
hidden blocking candidate/testbench/original failure → rejected
```

Unknown failures are not guessed into candidate repair.

## 6. Stage 2.3: Runtime evidence-loop integration

The runtime chain now supports:

```text
PreflightValidationStageHandler
→ CsynthValidationStageHandler
→ CsimValidationStageHandler(PUBLIC)
→ CsimValidationStageHandler(HIDDEN)
```

All handlers share one `RunContext`.

### Preflight

- executes real local compile/link preflight;
- consumes exact shared compile/tool budget;
- maps structured ownership and diagnostics;
- produces an agent-safe report;
- blocks before launch when budget is zero.

### CSYNTH

- executes the existing real Vitis CSYNTH path;
- reuses invocation and log artifacts;
- verifies requested/actual Vitis version;
- parses deterministic known diagnostics;
- preserves unknown failures as unknown;
- produces an agent-safe report.

### Public CSIM

- executes all declared Public suites in declaration order;
- collects candidate/testbench failures until a terminal budget,
  infrastructure, configuration, or timeout blocker;
- maps deterministic `csim_failed` to candidate-owned functional mismatch;
- removes absolute host paths and operator artifacts from agent feedback.

### Hidden CSIM

- executes declared Hidden suites in order;
- fails fast at the first blocking Hidden result;
- returns operator-full evidence to the coordinator;
- suppresses Hidden source identifiers and selected feedback from normal
  orchestration output and trace;
- never provides Hidden diagnostics to iterative model feedback.

### Runtime package boundary

High-level runtime integrations are lazy-loaded. Low-level budget, runner, and
trace services remain eager. This prevents package initialization cycles when
evaluation modules depend on low-level runtime services.

### Acceptance

- deterministic regression: `531/531 passed`;
- real Vitis HLS 2023.2;
- real Preflight → CSYNTH → Public CSIM → Hidden CSIM;
- exact shared physical usage: `6 tool / 3 compile / 1 csynth / 2 csim`;
- real Hidden-only mismatch rejection;
- zero CSIM budget blocked before compilation;
- safe Public and Hidden trace boundaries.

Detailed record:
[`stage2_runtime_evidence_acceptance.md`](stage2_runtime_evidence_acceptance.md).

## 7. Current limitations

- the repair-aware runtime acceptance kernel is deterministic and small;
- seven-type real full-chain pass diversity is established on one Vitis 2023.2 host, but fault/ownership diversity remains open;
- `UnifiedRunner` and CLI do not yet construct the new validation handlers;
- candidate repair is integrated with real local handlers, but its model-side
  acceptance still uses a deterministic local FakeProvider rather than a real
  network model API;
- testbench repair remains on its existing bounded repair path rather than
  being merged into the candidate repair controller;
- Hidden details remain operator evidence and never enter model prompts;
- semantic preservation is not formally proved;
- one Vitis version and one host environment do not imply general support.

## 8. Stage 2.4 completion record

Stage 2.4 is complete. Its implementation was intentionally decomposed into:

```text
2.4.1 Shared Layered Prompt Core
2.4.2 Testbench Repair Migration
2.4.3.1 Candidate Repair Prompt Policies
2.4.3.2 Candidate Model Adapter / Response Contract
2.4.3.3 Bounded Candidate Repair Loop
2.4.3.4 Safe ValidationOrchestrator Integration
```

The shared prompt contains:

```text
system invariants
+ task contract
+ current validation stage
+ effective TargetProfile
+ model-family adaptation input
+ agent-safe structured evidence
+ permitted modification scope
+ prior-attempt summary
+ caller-approved Memory snippets
+ output contract
```

Completed consumers:

- testbench repair;
- candidate compile repair;
- CSYNTH candidate repair;
- Public CSIM functional-mismatch repair.

Changed candidates re-enter validation from Preflight. Hidden evidence is
excluded from model prompts, normal results, and normal traces.

Latest feature commit:

```text
dd0ee927a5dac6691180c0772661cd90befe64ea
feat: integrate candidate repair orchestration
```

Acceptance:

- deterministic regression: `662/662 passed`;
- real broken-candidate g++ Preflight;
- deterministic local FakeProvider repair;
- real repaired g++ Preflight;
- real Vitis HLS 2023.2 CSYNTH;
- real Public CSIM and Hidden CSIM;
- exact usage: `7 tool / 4 compile / 1 csynth / 2 csim / 1 LLM`.

This is not a real network-model acceptance or a multi-type kernel proof.

## 8.1 Stage 2.5 progress

Internal sequence:

```text
2.5.1 Smoke Corpus / Ground Truth Contract
→ 2.5.2 Real Full-chain Pass Matrix
→ 2.5.3 Fault / Ownership / Hidden Matrix
→ 2.5.4 Evidence Summary
```

### 8.1.1 Stage 2.5.1 completed

Feature commit:

```text
ca991c372f9f40f7e592136b12af774dd985c0fa
feat: add Stage 2 smoke corpus
```

The repository now contains seven immutable baseline cases:

```text
array map
reduction
nested stencil
multi-output
struct record
hls::stream
stateful
```

Ground truth is manually authored and independent from runtime owner, route,
or terminal output.

Validation:

- `24/24` targeted tests;
- `48/48` smoke/response-contract regression tests;
- `686/686` full unittest;
- real g++ Preflight compile/link: `7/7 passed`;
- exact usage: `7 tool / 7 compile / 0 csynth / 0 csim / 0 LLM`;
- agent-safe manifests contain neither ground truth nor Hidden identity/secret.

Acceptance directory:

```text
/data/agrefactor_runs/stage2_5_1_smoke_corpus_20260718_232154/acceptance
```

This milestone does not prove seven-type Vitis synthesis, Public/Hidden CSIM,
fault ownership accuracy, or model repair. Stage 2.5.2 is next.

Detailed record:
[`stage2_smoke_corpus_acceptance.md`](stage2_smoke_corpus_acceptance.md).

### 8.1.2 Stage 2.5.2 completed

Feature commit:

```text
71f317b85227604a3959db725ae33b074d66824e
feat: add Stage 2 smoke pass matrix runner
```

The reusable runner executes the committed corpus through one shared physical
budget and refuses non-accepted results, stage-order drift, budget drift, or
Hidden content in safe results/traces.

Validation:

- `21/21` targeted tests;
- `77/77` related smoke/orchestration tests;
- `707/707` full unittest;
- seven real Vitis HLS 2023.2 full chains accepted;
- per case: `6 tool / 3 compile / 1 csynth / 2 csim / 0 LLM`;
- total: `42 tool / 21 compile / 7 csynth / 14 csim / 0 LLM`;
- exact stage order: Preflight → CSYNTH → Public → Hidden;
- Hidden steps remain operator-full and suppressed from safe result/trace.

Acceptance directory:

```text
/data/agrefactor_runs/stage2_5_2_real_full_chain_pass_matrix_20260719_001400/acceptance
```

This proves seven committed baseline shapes pass on the current host. It does
not prove arbitrary HLS support, fault ownership accuracy, Hidden-failure
behavior, or model repair.

Detailed record:
[`stage2_smoke_pass_matrix_acceptance.md`](stage2_smoke_pass_matrix_acceptance.md).

## 8.2 Stage 2.6–2.8 decision

```text
2.5 Multi-type Kernel Smoke Matrix
→ 2.6 Closure-readiness Audit
→ 2.7 Cross-stage Validation and Repair Hardening
→ 2.8 Final Documentation and Closure
→ Stage 3 Safe Optimizer
```

Stage 2.6 audits first. Stage 2.7 fixes only evidence-backed blockers. Stage
2.8 is the only milestone allowed to declare Stage 2 closed. See
[`STAGE2_HARDENING_PLAN.md`](STAGE2_HARDENING_PLAN.md).

## 9. Stage 2 closure standard

Stage 2 closes only when:

```text
2.1 Public/Hidden roles and evidence complete
+ 2.2 General feedback/state strategy complete
+ 2.3 Runtime evidence-loop integration complete
+ 2.4 Shared Layered Prompt Builder integrated
+ 2.5 Multi-type real-kernel smoke and independent ground truth completed
+ 2.6 Closure-readiness Audit completed
+ 2.7 Evidence-backed hardening completed
+ 2.8 Final documentation and acceptance synchronized
```
