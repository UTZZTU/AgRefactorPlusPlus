# Stage 2 Test-Suite Identity and Evidence Acceptance

## Status

**Accepted on 2026-07-16.**

This document closes the test-suite identity and evidence milestone of
Stage 2. It does not close the complete Stage 2 roadmap.

Accepted implementation commit:

```text
ddc1f6925dbe35e4dd79508d87b8ce553292e6f2
```

Accepted branch:

```text
stage2-test-suite-evidence
```

## Accepted scope

The milestone establishes the following generic capabilities:

1. `EvaluationSplit.PUBLIC` and `EvaluationSplit.HIDDEN`.
2. `TestSuiteSpec` with stable suite identity, optional version,
   declared case count, testbench location, and split-derived feedback
   visibility.
3. Backward-compatible `TaskSpec.test_suites` metadata while retaining
   the legacy `testbench_path` field.
4. `TestEvaluationEvidence` with separate:
   - complete operator/evaluator evidence;
   - agent-safe evidence.
5. Mandatory hidden-suite redaction for the agent-safe view.
6. Split-aware trace recording whose default is `agent_safe`.
7. `CsimSuiteEvaluator`, which adapts the existing local
   `flow.tools.csim.run_csim` result to suite-aware evidence.
8. Reuse of the same physical compile/csim executor and the same
   `BudgetManager`.
9. Preservation of the legacy `(status, diagnostic)` csim return
   contract.
10. Kernel-agnostic schema and adapter behavior.

## Public and hidden semantics

### Public suite

Public-suite diagnostic details may be exposed to an agent. The
agent-safe representation therefore retains public details and
artifacts.

### Hidden suite

The complete operator representation may retain the hidden testbench
location, diagnostic output, case data, and artifact locations.

The agent-safe representation must not expose:

- hidden testbench paths or source;
- hidden inputs;
- expected values;
- actual values;
- hidden case identifiers;
- raw hidden diagnostics;
- hidden artifact paths.

The safe representation retains only suite identity, split,
non-sensitive aggregate counts, normalized status, timeout/return-code
metadata, and a generic summary.

## Real local csim acceptance

Acceptance run:

```text
/data/agrefactor_runs/stage2_real_csim_suite_acceptance_20260716_183610
```

The run used the real local path:

```text
g++ compile
→ ./csim
→ CsimSuiteEvaluator
→ TestEvaluationEvidence
→ split-aware TraceRecorder
```

It did not call an LLM and did not run csynth.

### Public result

```text
legacy_status=succeeded
evidence_status=passed
declared_cases=3
passed_cases=3
failed_cases=0
```

### Hidden result

The real hidden suite intentionally produced a candidate mismatch:

```text
legacy_status=csim_failed
evidence_status=failed
```

The complete operator evidence retained the mismatch diagnosis.

The agent-safe evidence and the persisted agent-safe JSONL trace were
checked against explicit secret markers. Neither output contained the
hidden case identifier, input, expected value, actual value, hidden
testbench path, or hidden artifact path.

### Budget result

The public and hidden executions shared one hard budget:

```text
tool_calls=4
compile_calls=2
csim_calls=2
csynth_calls=0
```

A third suite evaluation was blocked before compile launch. The blocked
attempt did not increase any usage counter.

Public/hidden remains an evaluation role. No
`public_test_calls` or `hidden_test_calls` physical counter exists.

## Deterministic regression evidence

The implementation and real acceptance run were followed by:

```text
270/270 deterministic tests passed
```

The repository worktree remained clean after the read-only acceptance
run.

## Accepted commits

```text
16d3971 feat: add test suite evaluation schema
c727345 feat: attach test suite metadata to tasks
fc45f3d feat: add test evaluation evidence redaction
b837179 feat: add split-aware test evidence tracing
ddc1f69 feat: adapt csim results to test suite evidence
```

## Explicit non-claims

This milestone does **not** claim that the following work is complete:

- general Vitis/compile/csim/csynth feedback parsing;
- a unified failure taxonomy across all tools;
- an evidence-driven state machine;
- layered prompt construction;
- candidate generation or candidate repair;
- hidden-suite execution through every legacy entry point;
- deterministic process isolation for every stateful kernel;
- multi-type real-kernel validation;
- complete Stage 2 acceptance;
- Stage 3 API-driven refactoring or optimization.

## Next milestone

The next Stage 2 milestone is a general feedback schema and parser that
normalizes compile, preflight, csim, and csynth evidence without losing
failure ownership, stage, severity, or raw operator evidence.
