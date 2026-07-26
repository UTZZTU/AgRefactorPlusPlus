# Stage 2.6 Closure-readiness Audit

## 1. Decision

```text
Stage 2.6 status: completed
Stage 2 status: still open
Satisfied audit items: 4
Blocking before Stage 3: 5
Deferred: 4
Future/external: 4
Next: Stage 2.7.1 Repair Protocol and Artifact Schema
```

Stage 2.6 is an audit-only milestone. It does not implement the fixes.

## 2. Evidence baseline

- Repository baseline: `44ae3fc56e34cbf415108ce42c64569a0ff1cd98`.
- Current regression: `727/727`.
- Stage 2.5: 7 baselines, 7/7 real full chains, 9 fault scenarios, 16 independent labels.
- Hidden pass/fail and no-leakage evidence is complete.
- No real network-model call was made by Stage 2.5.

## 3. Blocking before Stage 3

### B-01 — Formal repair-aware UnifiedRunner / CLI construction

**Decision:** Blocker.

**Impact:** Stage 3 cannot rely on a single non-bypassable correctness gate while the formal entry point can only use the legacy refactor adapter.

**Acceptance criteria:**

- TaskSpec -> CLI/UnifiedRunner -> repair-aware phase -> local handler factory -> validation/repair orchestration
- one exact BudgetManager and TraceRecorder are shared
- complete safe run result and artifact manifest are written
- legacy mode remains explicit and cannot masquerade as the new path

### B-02 — Shared Testbench/Candidate repair protocol and artifact schema

**Decision:** Blocker.

**Impact:** Stage 3 candidate lineage and later audit cannot depend on two incompatible attempt identities and incomplete artifacts.

**Acceptance criteria:**

- shared attempt_id, proposal_id, artifact role, prompt manifest, model response, observed usage, validation summary, stop reason, terminal status, and artifact manifest
- executors remain separate; only protocol and artifact schema are aligned
- operator-only and agent-safe fields are explicit
- atomic manifest writing and deterministic JSON schema tests

### B-03 — Minimal ModelFamilyProfile and capability tags

**Decision:** Blocker.

**Impact:** The first real-model smoke cannot document why thinking tags, strict completion, or code-specialized defaults are handled safely.

**Acceptance criteria:**

- typed capability tags: reasoning_model, code_specialized, strict_instruction, thinking_tag_possible, strict_completion
- safe default parameters and family instruction rendering
- no automatic model routing and no vendor-name branching in core control flow
- fixed user-selected model remains the default policy

### B-04 — Stage 1 Hardening Batch A

**Decision:** Blocker.

**Impact:** Stage 3 would otherwise generate and compare candidates without a stable, self-describing target execution contract.

**Acceptance criteria:**

- stable named target profiles
- per-profile executable and settings path
- parser profile identity
- effective-value provenance per field
- basic resource-limit schema
- no-secret target/model configuration templates
- existing Vitis 2023.2 behavior remains backward compatible

### B-05 — One real network-model candidate-repair closed-loop smoke

**Decision:** Blocker with an explicit user-credential dependency.

**Impact:** Provider behavior, real token usage, invalid output, exceptions, and model-family handling remain unverified on the new candidate-repair path.

**Acceptance criteria:**

- user explicitly selects an OpenAI-compatible logical model and supplies the API key via environment variable
- real candidate-owned failure -> real request -> strict contract -> bounded repair -> real local validation
- accepted or trustworthy terminal failure is allowed; successful repair is not fabricated
- prompt, response, usage, exception/contract status, validation, and Hidden boundary are recorded
- no secret value is written to repository or artifacts

## 4. Satisfied / verification-only

- **S-01 Multi-type real validation, independent ground truth, Hidden isolation, and exact budget evidence** — Satisfied for Stage 2 closure readiness.
- **S-02 Candidate Response Contract for the committed Stage 2.5 corpus** — No evidence-proven blocker. Preserve current strict contract and change it only if the real-model smoke exposes a concrete failure.
- **S-03 Conservative CSYNTH diagnostic behavior** — No evidence-proven rule-expansion blocker. Unknown diagnostics already remain blocking UNKNOWN rather than being guessed.
- **S-04 Ground-truth corpus finalization for Stage 2** — The 16-label corpus and machine-readable evidence index satisfy the Stage 2 independent-label requirement. Stage 2.7 must revalidate, not invent new labels.

## 5. Deferred

- **D-01 Broad CandidateResponseContract grammar expansion** — Defer. Apply only a minimal delta if B-05 exposes a reproducible contract failure. Target: Stage 2.7 evidence-gated delta or later.
- **D-02 New CSYNTH diagnostic classification rules** — Defer. Parser-profile wiring belongs to B-04, but new semantic rules require new real logs. Target: Stage 2.7 evidence-gated delta or Stage 6.
- **D-03 Merging Testbench and Candidate repair executors** — Defer. Align protocol/artifacts under B-02; do not create a second orchestrator or force executor unification. Target: Not required for Stage 2 closure.
- **D-04 Stage 1 Hardening Batch B: more Vitis versions, devices, platforms, and cross-product validation** — Defer. Target: Before Stage 5 migration.

## 6. Future / external

- **F-01 Natural reproduction of a real toolchain outage and real ambiguous CSYNTH diagnostics** — External/evidence-dependent. Deterministic routing coverage is sufficient for Stage 2; retain new real logs when naturally observed.
- **F-02 Statistical owner/route accuracy over a large benchmark** — Future research. Target: Stage 6.
- **F-03 Cross-version migration, repository migration, ROSE/EDG restoration, and XRT-to-AVED platform migration** — Outside the Stage 2 critical path. Target: Stage 5/6 or external dependency.
- **F-04 Automatic model routing, arbitrary-program support, and formal semantic equivalence** — Future research; do not add to Stage 2.7.

## 7. Frozen Stage 2.7 sequence

```text
2.7.1 Repair Protocol and Artifact Schema
→ 2.7.2 Minimal ModelFamilyProfile
→ 2.7.3 Stage 1 Hardening Batch A
→ 2.7.4 Formal Repair-aware UnifiedRunner / CLI
→ 2.7.5 Real Network-model Candidate Repair Smoke
→ 2.7.6 Evidence-gated Contract/Parser Delta + Ground-truth Revalidation
→ 2.7.7 Cross-stage Regression and Stage 2.8 Handoff
```

### 2.7.1 — Repair Protocol and Artifact Schema

Classification: `blocker`.

Primary file scope:

- `agrefactor/repair/protocol.py (new)`
- `agrefactor/repair/artifacts.py (new)`
- `agrefactor/repair/candidate_loop.py`
- `agrefactor/runtime/candidate_repair_integration.py`
- `agrefactor/testing/testbench_repair.py`
- `agrefactor/testing/model_testbench_repairer.py`
- `agrefactor/repair/__init__.py`
- `agrefactor/testing/__init__.py`
- `tests/test_repair_protocol.py (new)`
- `tests/test_candidate_repair_loop.py`
- `tests/test_candidate_repair_integration.py`
- `tests/test_testbench_repair.py`
- `tests/test_model_testbench_repairer.py`

Acceptance: Both repair paths emit the same versioned attempt/proposal/artifact vocabulary without merging executors.

### 2.7.2 — Minimal ModelFamilyProfile

Classification: `blocker`.

Primary file scope:

- `agrefactor/models/family.py (new)`
- `agrefactor/models/base.py`
- `agrefactor/models/registry.py`
- `agrefactor/models/candidate_adapter.py`
- `agrefactor/testing/model_testbench_repairer.py`
- `agrefactor/prompts/layered.py`
- `agrefactor/models/__init__.py`
- `tests/test_model_family_profile.py (new)`
- `tests/test_candidate_model_adapter.py`
- `tests/test_model_testbench_repairer.py`
- `tests/test_layered_prompt_builder.py`

Acceptance: Typed capability tags influence only safe defaults/instructions; fixed user model selection remains authoritative.

### 2.7.3 — Stage 1 Hardening Batch A

Classification: `blocker`.

Primary file scope:

- `agrefactor/config/target.py`
- `agrefactor/config/target_profiles.py (new)`
- `configs/targets/vitis-2023.2-default.json (new)`
- `flow/tools/csynth.py`
- `agrefactor/runtime/csynth_stage.py`
- `.env.example`
- `tests/test_target_profile.py`
- `tests/test_csynth_execution_evidence.py`
- `tests/test_csynth_tcl.py`
- `tests/test_csynth_version_verification.py`

Acceptance: Named profile controls executable/settings/parser/resources and records effective provenance while preserving the existing 2023.2 default.

### 2.7.4 — Formal Repair-aware UnifiedRunner / CLI

Classification: `blocker`.

Primary file scope:

- `agrefactor/runtime/repair_phase.py (new)`
- `agrefactor/runtime/runner.py`
- `agrefactor/runtime/candidate_repair_integration.py`
- `agrefactor/runtime/__init__.py`
- `agrefactor/cli.py`
- `tests/test_unified_candidate_repair_phase.py (new)`
- `tests/test_candidate_repair_integration.py`
- `tests/test_cli.py`

Acceptance: The formal CLI constructs the real local validation/repair chain with one budget, one trace, and complete versioned artifacts.

### 2.7.5 — Real Network-model Candidate Repair Smoke

Classification: `blocker_acceptance`.

Primary file scope:

- `scripts/acceptance/stage2_real_candidate_repair_smoke.py (new)`
- `docs/stage2_real_model_repair_acceptance.md (new after execution)`

Acceptance: One user-selected OpenAI-compatible network call is recorded and followed by real local validation; success or trustworthy terminal failure is accepted.

### 2.7.6 — Evidence-gated Contract/Parser Delta and Ground-truth Revalidation

Classification: `conditional_or_verification`.

Primary file scope:

- `agrefactor/models/candidate_adapter.py (only if 2.7.5 proves a contract defect)`
- `agrefactor/evaluation/csynth_diagnostics.py (only if new real logs prove a parser defect)`
- `tests/test_candidate_model_adapter.py`
- `tests/test_csynth_diagnostic_parser.py`
- `tests/test_stage2_smoke_matrix.py`
- `tests/test_stage2_smoke_fault_matrix.py`

Acceptance: No speculative rules; the 16 labels remain independent and all existing smoke contracts pass.

### 2.7.7 — Cross-stage Regression and Stage 2.8 Handoff

Classification: `verification`.

Primary file scope:

- `docs/roadmap/PROJECT_STATE.md`
- `docs/reference/NEXT_CHAT_HANDOFF.md`
- `docs/roadmap/STAGE2_HARDENING_PLAN.md`
- `docs/acceptance/stage2/stage2_hardening_acceptance.md (new)`

Acceptance: All blocker criteria pass, real/deterministic evidence remains distinguished, and Stage 2.8 receives a frozen closure checklist.

## 8. Explicit non-goals

- No Stage 3 optimizer implementation.
- No Memory applicability logic.
- No migration or SourceProfile implementation.
- No automatic model routing.
- No speculative CandidateResponseContract or CSYNTH parser expansion.
- No claim of arbitrary HLS or statistical attribution accuracy.

## 9. Closure consequence

Stage 2 cannot close until all five blockers are accepted or explicitly removed from the Stage 3 critical path with new evidence. Stage 2.8 remains the sole formal closure milestone.

Machine-readable audit:
[`stage2_closure_readiness_audit.json`](../stage2_closure_readiness_audit.json).
