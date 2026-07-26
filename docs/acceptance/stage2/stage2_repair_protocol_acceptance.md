# Stage 2.7.1 Repair Protocol and Artifact Schema Acceptance

## Status

```text
ae1042fc77efe5c87a85a5f4954a7c0a951f2045
feat: add shared repair protocol artifacts

33/33 targeted
760/760 full unittest
```

## Shared envelope and typed payloads

```text
attempt_id
proposal_id
artifact_role
prompt_manifest
model_response
model_call_observed
observed_usage
payload_type
payload
stop_reason
terminal_status
evidence_view
operator_artifact_available
artifact_manifest
```

Candidate and Testbench executors remain separate. Both emit the same safe
envelope, while business-specific fields remain typed:

```text
CandidateRepairPayload
  validation_summary
  model_result_available

TestbenchRepairPayload
  preflight_summary
  legacy_preflight_artifact_available
```

## Compatibility

- Existing Candidate/Testbench legacy `to_dict()` fields are unchanged.
- `testbench_repair.json` remains available.
- Shared records are written to a separate `repair_artifacts/` bundle.
- Candidate orchestration exposes an explicit writer for its repair result.

## Artifact integrity

Each bundle contains per-attempt JSON, `repair_run.json`, and
`artifact_manifest.json`. Files are written through same-directory temporary
files, flushed, fsynced, and atomically replaced. The manifest records
relative paths, SHA-256, byte size, and record type.

## Safety boundary

Shared artifacts are `agent_safe`. Testbench validation summaries omit command,
stdout, stderr, diagnostics, and artifact paths. The protocol records only
whether a separate operator artifact exists; it does not embed operator-only
content.

## Stage 2.7 finite-scope rule

Stage 2.7 may implement only the five blockers frozen by Stage 2.6 and a new
blocking defect directly reproduced by the Stage 2.7.5 real-model smoke.

- general usability findings go to backlog;
- statistical questions go to Stage 6;
- additional Vitis/device matrices remain Batch B before Stage 5;
- optimizer work remains Stage 3;
- Memory remains Stage 4;
- migration remains Stage 5;
- Stage 2.7.2 remains a thin capability profile and never routes models;
- Stage 2.7.3 implements Batch A only and preserves the Vitis 2023.2 default;
- Stage 2.7.4 uses a small repair-phase factory rather than assembling the
  entire low-level chain inside CLI argument handling;
- Stage 2.7.5 starts with a minimal deterministic candidate-owned compile
  error; success or trustworthy terminal failure are both valid evidence;
- Stage 2.7.7 adds no new feature;
- Stage 2.8 requires a real model call with trustworthy evidence, not a
  guaranteed successful repair.

## Execution class

```text
network model executed: false
real tool executed: false
deterministic protocol acceptance: true
executors merged: false
```

Acceptance directory:

```text
/data/agrefactor_runs/stage2_7_1_repair_protocol_artifacts_v5_20260719_172510/acceptance
```

Next milestone:

```text
Stage 2.7.2 Minimal ModelFamilyProfile
```
