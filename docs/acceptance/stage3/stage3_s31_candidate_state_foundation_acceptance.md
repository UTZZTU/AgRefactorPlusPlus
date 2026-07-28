# Stage 3.1 Candidate State Foundation Acceptance

## Status

```text
pre_commit_baseline=72f87ca5e828dd9ac6f244e254c298c13d6897fb
branch=stage2-general-feedback
focused_tests=50/50
full_unittest=1558/1558
independent_contract_audit=passed
payload_hashes=5/5
stage3_implementation_started=true
s3_1_accepted=true
next_package=S3.2_QUALIFICATION_AND_PPA_EVIDENCE
```

The closure commit is the commit containing this document; no self-invalidating
final SHA is copied here.

## Accepted scope

S3.1 contains only:

- typed `HypothesisRecord`;
- typed `CandidateRecord`;
- typed `OptimizerState`;
- atomic checkpoint writer and recovery;
- baseline-as-initial-`best_correct` semantics after qualification;
- schema, serialization, state, path, hash and recovery tests.

## Implementation files

```text
176264f1f094a81ab96b215b145df1da1b90aca7c8bff890142a75ebf4d64914  agrefactor/optimization/__init__.py
e37ca0ed6733e906f7a33345336f8db802ce426938b4be268c8c460042118feb  agrefactor/optimization/state.py
f3ce12e3a883a03a7c3c3ed43c5191c35f05b8406bc922eb528cb89957c0d159  agrefactor/optimization/checkpoint.py
6f56adfe655451c1050ebbae8f916108a397d93624601a6a9dc1f49615d3cc81  tests/test_optimizer_state.py
5a57d187cf1de6658d8e931a5b9974efc44a5dc75da285acce35d7fbb63310c9  tests/test_optimizer_checkpoint.py
```

## Evidence

```text
focused=Ran 50 tests in 0.120s
full=Ran 1558 tests in 4.449s
model_api_called=false
vitis_called=false
real_csim=false
real_csynth=false
tracked_stage2_code_modified=false
independent_black_box_checkpoint_recovery=passed
latest_complete_checkpoint_authoritative=true
rejected_candidate_overwrites_best_correct=false
```

Local closeout evidence:

```text
/data/agrefactor_runs/stage3_s31_closeout_20260728T164026Z_2882466
```

The local path is supporting evidence, not a portable repository dependency.

## Safety and boundaries

- Existing Stage 2 backend, CLI, runner, providers and Vitis handlers are unchanged.
- Hidden/operator-full material is not an allowed model-facing hypothesis value.
- Candidate states are monotonic; terminal candidates cannot return to accepted.
- Checkpoint projections are non-authoritative; the immutable complete checkpoint
  marker is written last and recovery rebuilds projections from it.
- Candidate source artifacts are hash checked and constrained to optimizer-root
  relative paths; symlinked artifacts are rejected.
- Deterministic tests are not presented as real model or real Vitis acceptance.

## Explicit non-goals preserved

S3.1 does not implement:

- baseline/candidate qualification orchestration;
- PPA report parsing or comparison;
- validation cache;
- optimizer level/round/budget state machine;
- model hypothesis generation;
- real `optimize` or `full` product execution;
- Stage 4 Memory Gate or Stage 5 migration.

## Acceptance decision

```text
S3.1_CANDIDATE_STATE_FOUNDATION=accepted
STAGE3_IMPLEMENTATION_STARTED=true
NEXT_PACKAGE=S3.2_QUALIFICATION_AND_PPA_EVIDENCE
```
