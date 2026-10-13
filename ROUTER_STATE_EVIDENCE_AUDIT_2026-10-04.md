# Router/state/candidate evidence audit (read-only)

This audit was performed against the current server worktree after the P3/P4
artifact changes. No production file was changed for this audit.

## Composer fixture update

`tests/test_csynth_feedback_composer.py` now supplies a valid
`expected_report` identity in its completed invocation fixture. It also has a
regression case proving that an empty diagnostic log cannot suppress a success
when report identity is absent. The module passes 17 tests.

## Evidence propagation findings

### 1. Candidate owner has a permissive direct route

`agrefactor/evaluation/feedback_routing.py:381-382` maps every
`FeedbackOwner.CANDIDATE` item directly to `REPAIR_CANDIDATE`. Unlike the
qualified-unknown branch at lines 386-392, it does not require explicit
`owner_authority`, `repair_eligible`, `evidence_complete`, or `tool_launched`
metadata.

`agrefactor/repair/candidate_loop.py:1298-1310` repeats the same asymmetry:
an item whose owner is Candidate is accepted without checking those evidence
fields. The special Unknown path does check all of them.

The current CSYNTH artifact evaluator is safe for its own diagnostics: it
sets Candidate only when completion, non-zero return, current log identity,
and exact source provenance/hash all hold (`agrefactor/evaluation/csynth_artifact_feedback.py:188-221`). This does not prove that every other producer of a Candidate-owned `FeedbackItem` carries the same contract.

**Risk:** a legacy or alternate producer that sets `owner=Candidate` without
the explicit evidence metadata can still enter the repair route. This is the
main remaining default/evidence gap.

**Minimal fix proposal for the root integrator:** introduce one shared
`candidate_owner_proven(item)` predicate and use it in both the route and
candidate-loop validation. It should require the producer's accepted typed
authority, `evidence_complete is True`, and `tool_launched is True`; preserve
the existing qualified-unknown predicate as a separate, stricter branch.
Run a shadow report first to enumerate existing Candidate items that would be
rejected before changing the final action.

### 2. State-machine defaults are fail-open if called with a hand-built route

`agrefactor/evaluation/validation_state.py:488-505` defaults missing route
metadata to `deterministic_proven`, `physical_tool_launched=True`, and
`evidence_complete=True`. This is compatible with old deterministic route
objects, but it can turn missing evidence into an apparently authorized
`RecoveryRequest` if a caller constructs a repair route directly.

The normal router supplies `evidence_complete=True` for a qualified unknown
route (`feedback_routing.py:267-274`), so this default is not needed for that
new path. A safe compatibility migration is to emit a warning/telemetry when
any of these keys are absent, then change defaults to unknown/false after all
route producers have been upgraded. Do not silently change this in isolation,
because older tests and typed routes may rely on the compatibility behavior.

### 3. Candidate loop re-materializes positive facts

`agrefactor/repair/candidate_loop.py:820-825` reserves a recovery request with
`physical_tool_launched=True` and `evidence_complete=True` rather than copying
the route facts. This is safe only after route validation is strict; otherwise
it can erase a missing/false fact during the final ledger reservation.

The minimal change is to copy the already validated route metadata, or assert
the predicate before reservation. Keep the route as the source of truth rather
than inventing facts in the loop.

### 4. DiagnosticEvent projection is mostly fail-closed

`agrefactor/evidence/diagnostic_event.py:459-462` computes physical execution
and evidence completeness from report/item facts; missing item facts produce
false. This is the desired behavior for operator reports. The typed-outcome
projection at lines 561-562 still defaults `evidence_complete` to whether an
evidence hash exists. A hash proves artifact identity, but by itself does not
prove owner resolution or semantic completion. Typed producers should provide
an explicit boolean; absent value should remain false.

### 5. Catalog safety review

`agrefactor/evaluation/diagnostic_catalog.py` correctly rejects direct `owner`
and `route` fields, forces `owner_policy=resolve_from_evidence`, validates HLS
codes/patterns, and strips repair actions for Hidden matches. Categories with
repair actions must request `tool_launched` plus at least one execution/source
evidence field. The remaining enforcement belongs to the evidence resolver;
the catalog itself should stay declarative.

## Verification

- `tests.test_csynth_feedback_composer`: 17 passed.
- CSYNTH artifact/stage/adapter plus P3 tests: 109 passed in the combined
  focused run before this audit.
- No Vitis, model API, or real case was run.
