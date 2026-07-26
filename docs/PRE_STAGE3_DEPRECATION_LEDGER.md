# Pre-Stage-3 Deprecation Ledger

## Contract

Every known cleanup object is classified using the authoritative vocabulary:

```text
keep
wrap
deprecate
delete_after_P0
needs_evidence
```

Audit repository head:

```text
b33fe48cccc441a149b7a613770baba612485d75
```

Audit summary:

```text
tracked_files_seen=538
runtime_files_scanned=118
objects=11
blocking_findings=0
```

### three disabled static Testbench hard-blocker helpers

- Classification: `delete_after_P0`
- Status: `completed`
- Evidence: No retired helper or active control token remains in runtime files. The historical failure-kind string is audited separately and has no producer or decision authority.
### legacy forbidden_internal_dependency failure-kind vocabulary

- Classification: `keep`
- Status: `compatibility_evidence_only`
- Evidence: Exactly one occurrence remains: the TestbenchFailureKind enum value used for historical evidence compatibility. No other runtime occurrence, producer, comparison, gate, or control-flow reference is permitted. Testbench next_action is owner-driven.
### tests that only protect retired heuristics

- Classification: `keep`
- Status: `reviewed`
- Evidence: No test asserts the retired behavior as blocking. Any retained references serve regression coverage for non-blocking behavior.
### duplicate real-DFS acceptance/recovery runners

- Classification: `delete_after_P0`
- Status: `completed`
- Evidence: No acceptance/recovery runner is tracked in active runtime roots; external operator scripts remain outside the repository.
### normal-user --legacy / --repair-aware flags

- Classification: `deprecate`
- Status: `completed`
- Evidence: Both selectors remain accepted only by the advanced run entrypoint, emit DeprecationWarning, and their option rows and deprecated spellings are absent from the complete help surface. Normal source-only refactor/optimize/full commands do not expose them.
### default full terminal JSON

- Classification: `delete_after_P0`
- Status: `completed`
- Evidence: Normal source commands use the P5 output renderer and default concise mode. Full RunResult JSON is retained only for the advanced TaskSpec reproduction entrypoint.
### TaskSpec.testbench_path and test_suites overlap

- Classification: `wrap`
- Status: `compatibility_period`
- Evidence: test_suites is authoritative for multi-suite formal evaluation; testbench_path remains a compatibility/preflight fallback for the advanced task-file entrypoint.
### duplicate model-parameter interpretation entrypoints

- Classification: `wrap`
- Status: `completed`
- Evidence: EffectiveModelConfig/ModelRegistry is authoritative for normal product execution. Legacy fields are translated only inside the compatibility adapter for advanced reproduction.
### LegacyRefactorSettings fields without consumers

- Classification: `keep`
- Status: `no_unused_fields`
- Evidence: Every compatibility field has an active validation, translation, or adapter consumer.
### duplicate raw logs

- Classification: `wrap`
- Status: `canonical_artifact_capture`
- Evidence: P5 owns stdout/stderr artifact capture; compatibility backend streams are captured rather than treated as a second public output authority.
### temporary acceptance-only configuration and scaffolding

- Classification: `delete_after_P0`
- Status: `completed`
- Evidence: No P0/Step-F acceptance-only naming or wiring remains in active runtime roots.


## Decision

No object remains in `needs_evidence`, and no blocking finding remains.
Historical acceptance documents are retained as evidence. Compatibility
components are retained only where an active consumer or an advanced
reproduction contract still exists.
