# V2.3 R5 Implementation Progress

Date: 2026-09-19

This document records implementation progress only. It does not accept R5 and
does not authorize R6. The original V2.3 roadmap and frozen R5 design remain
authoritative.

## Completed Deterministic Work

- Added append-only R5 episode envelopes, lifecycle reduction, history-only
  snapshot construction, candidate-only payloads, discriminated research
  authorization, and the 500/500 budget ledger.
- Added the paired A0-A6 campaign protocol, counterbalanced runner, source/time
  holdout checks, arm reducer, and evidence inventory.
- Connected the internal profile seam to the existing `refactor` phase. The
  ordinary `refactor`, `optimize`, and `full` entrypoints remain the only
  product entrypoints; the profile is not a CLI option.
- Added a typed R5 runtime binding. A0/A1 cannot carry mutation bindings;
  A2/A3 require the appropriate authorization and existing R4 factory; A4-A6
  require snapshot and payload-manifest equality before snippets reach the
  existing Candidate prompt hook.
- R5 arms bypass the ordinary post-diagnostic Candidate loop. A0/A1 are
  observation-only; A2-A6 hand the unchanged main result to the existing R4
  controller, preserving the one-mutation and formal-validation boundary.
- Added a private typed capture of one existing `refactor` baseline and a
  product campaign executor that forks A0-A6 from that exact Candidate,
  diagnostic context, task, and formal result. Baseline usage is charged once;
  every arm receives an isolated BudgetManager, TraceRecorder, model adapter,
  validation workspace, and append-only artifact root.
- Corrected the conservative Vitis upper bound to count the common baseline's
  complete csim/csynth/cosim prefix. The runner now rejects work in A0, Vitis
  work in A1, and any arm that crosses its frozen Provider/Vitis upper bound.

## Verification

- Deterministic R5 and adjacent boundary tests: passed.
- Full repository regression: `2515` tests passed on the worktree that became
  implementation commit `169e64b4c0f24b16292eaa7125357ac8071bdfef`.
- This deterministic checkpoint consumed `0` new Provider calls and `0` new
  Vitis launches. The credential was removed from the test environment.

## Current Data Boundary

The immutable predecessor inventory reports `64` historical Provider calls and
`180` historical Vitis launches. They are predecessor evidence, not R5 budget
consumption. Full-regression output includes
`P4_0E_R1_REAL_NETWORK_SMOKE_PASSED`, but the only discovered caller is a unit
test that replaces the provider client with an in-process fake and supplies a
literal `unit-secret`; it does not cross the Provider boundary. The reconciled
R5 ledger is therefore `0/500` Provider calls and `0/500` Vitis launches.

The current inventory is not a campaign dataset: it has no frozen history/
future partition and no paired positive/inapplicable controls. The generated
`r5_dataset_contract.py` output therefore stays
`blocked_data_acquisition`, does not create a Trusted revision, and forbids a
real campaign. Historical reports or Package D canaries must not be relabeled
as an R5 future set.

The deterministic HLSRewriter source audit now covers all 20 checked-in roots.
It found 0 directly admissible cases, 13 cases that require a separately
reviewed Public/Hidden adapter, and 7 rejected cases. The common deficiencies
are implementation-including testbenches, missing process-failure oracles,
missing distinct Public/Hidden suites, and Tcl flows that run only synthesis.
The immutable audit is
`/data/agrefactor_runs/r5_p2_hlsrewritter_audit/hlsrewritter_case_audit.json`
with file SHA-256
`5b0bdf3c074cda2b73b43c80032413c809c1528c18b15b7c3dcfbe2f92be3cfb`.
No Provider or Vitis call was made. An `adapter_required` classification is
not campaign admission and does not authorize creating an oracle.

The reviewed adapter-boundary pass records all 13 adapter-required cases and
keeps all 13 blocked. It references only existing source/reference/testbench/
Tcl hashes, leaves history/future and control roles unassigned, and explicitly
forbids invented expected outputs, duplicated Public/Hidden tests, or manual
Trusted revisions. The immutable review is
`/data/agrefactor_runs/r5_p2_adapter_review/oracle_adapter_review.json` with
file SHA-256
`8db47e2243c6640f515b215fd4cd035cf5d8cd22ef137c9ceb96edb32a23b1ab`.
Its canonical internal review hash is
`59d1f7b2f64cba5994521fa94f19e529aedd715a91858c11174da73ad2f73768`.
It consumed zero Provider and zero Vitis calls.

## Next Authorized Step

Audit and freeze one additional history-only source that is independent of the
accepted Package D source and can lawfully produce a verified-positive R4
episode. Implement a reference-backed Public/Hidden adapter only if checked-in
repository artifacts support an enforceable oracle without inventing expected
outcomes or weakening failure semantics. The existing future holdout remains
unobserved. A zero-call protocol audit is required before any real acquisition;
R6 remains stopped.

## Calibration-Scope Correction

A later zero-call review found that the accepted schema-v1 R2 certificate was
statistically established only for `unsupported_construct`, while its runtime
shape exposed confidence labels without an explicit failure-class scope. The
consumer now treats every legacy schema-v1 certificate as
`unsupported_construct`-only. New schema-v2 certificates bind an explicit
failure-class list, and R4 rejects an advisory outside that list.

The v1 oracle-adapter plan was re-audited with this rule and a host syntax
preflight. The superseding audit status is `blocked_history_acquisition` at
`/data/agrefactor_runs/r5_p2_oracle_protocol_audit_superseding_v2`; its
`protocol_audit.json` file SHA-256 is
`3a0b3eff7c515e4f372c01424ce613912189fc810e172597f87c57f366f9aae6`.
The old audit remains immutable but no longer authorizes the next step. This
correction consumed zero Provider calls and zero Vitis launches.

## Calibration-Compatible V2 Dataset Boundary

The v2 plan freezes two real history positives (`Exception_E2_Filter` and
`Recursive_E2_DFS`) and a disjoint future set containing one positive and one
inapplicable-or-confusable control. All positive cases are inside the accepted
legacy certificate's narrow `unsupported_construct` scope. Public and Hidden
adapters compare the generated Candidate against the checked-in reference
source; no expected result was invented.

The adapter freeze and independent protocol audit passed without Provider or
Vitis calls. The authoritative file hashes are recorded in
`V2_3_STATE.json`. The audit status is `ready_for_history_acquisition`, while
`R5_REAL_CAMPAIGN_ALLOWED` remains false.

The history driver calls the ordinary `refactor` entrypoint internally, then
uses the real Provider-backed R2 advisor and existing R4 controller in A2. It
does not add a product CLI or a second Vitis path. Runtime context signatures
are bound only after the real baseline produces its deterministic diagnostic;
they are not guessed from the benchmark.

The provided-Testbench baseline contains six identifier calls, one
deduplicator, one planner, and one refactoring call. Therefore its frozen
Provider upper bound is 9, not 1. An A2 history attempt adds at most one R2
advisory and one R4 mutation, for 11 Provider calls and 8 Vitis launches per
attempt. The predeclared acquisition upper bound is consequently 66 Provider
calls and 48 Vitis launches for up to three fail-closed attempts on each of two
sources.

The first real history run at
`/data/agrefactor_runs/r5_p3_history_real_0a2865e_run1` exposed the old budget
error before any Vitis launch. Three attempts each consumed four Provider calls
and then failed closed at the fifth logical launch. The authoritative R5 ledger
therefore records 12 Provider calls and 0 Vitis launches. The old zero-call
protocol audit is retained as immutable evidence but is superseded for budget
authorization; a corrected zero-call audit is required before retrying history.

The corrected zero-call audit passed at
`/data/agrefactor_runs/r5_p2_oracle_protocol_audit_v3_budget_corrected_6e523d6`.
Its `protocol_audit.json` file SHA-256 is
`0b27dd5a7e576a9893346468ff2fd8622ba9b63c345c071c430cea4f2e306fa7`.
It carried the reconciled `12/0` ledger and froze one-case pilot bounds at
`60/54` and two-case formal bounds at `120/108` Provider/Vitis launches. A
subsequent real baseline proved that this Vitis model was still incomplete:
the ordinary provided-Testbench path launches Public csim, csynth, Public
cosim, and Hidden csim, for four launches per complete validation. Three
successful baselines consumed `27/12` before the stale three-launch campaign
guard failed closed. The cumulative R5 ledger is therefore `39/12`; the v3
audit is retained but superseded, and the corrected bounds are `60/72` for a
one-case pilot and `120/144` for the two-case formal campaign.

The v4 zero-call protocol audit passed at
`/data/agrefactor_runs/r5_p2_oracle_protocol_audit_v4_vitis_corrected_7784252`.
Its `protocol_audit.json` file SHA-256 is
`2bc9a298048a41d01f782e5e4bc0d810fbc66a85bed57aca70f0d3c5401a6b90`.
It carries the cumulative `39/12` ledger and the corrected `60/72` pilot and
`120/144` formal bounds. It made zero Provider calls and zero Vitis launches.

## First Outcome-Bearing History Acquisition

The next immutable history run is
`/data/agrefactor_runs/r5_p3_history_real_75ef26c_run1`. Its first frozen
history case, `Exception_E2_Filter`, completed three ordinary `refactor`
baselines. Every baseline was formally accepted after exactly 9 Provider calls
and 4 Vitis launches, and none produced a Diagnostic Event. The actual added
usage is therefore `27/12`, making the authoritative cumulative R5 ledger
`66/24`.

This is a data result, not an R2/R4 success. Because the initial Candidate was
already accepted, there is no legal R2 input and no R4 mutation or repair
episode. The run must not be relabeled as a verified-positive R4 episode. The
first case is unsuitable for the predeclared positive-history acquisition
role under the current generated-Candidate protocol. The second history case
and every future case remain unobserved.

The run-local `33/24` ledger is retained as immutable evidence but is
superseded by the per-run `run_result.json` records for global budget
accounting. The reconciliation is recorded in
`docs/roadmap/R5_HISTORY_BASELINE_OUTCOME_RECONCILIATION.json`. Before another
real call, the history acquisition driver and protocol audit must be revised
so that a data-insufficient case is preserved without aborting discovery of
the remaining pre-frozen history case. This revision may not inject a defect,
forge an episode, read future outcomes, or treat a checked-in legacy Candidate
as valid R5 evidence without an explicit provenance and product-path design.

The continuation implementation now classifies an accepted baseline with no
Diagnostic Event as `accepted_without_diagnostic`, charges only authoritative
actual usage, and preserves it as data-insufficient rather than invoking R2 or
R4. It continues across frozen history cases and emits
`insufficient_history_evidence` unless two independent verified-positive R4
episodes actually exist.

A zero-call continuation audit passed at
`/data/agrefactor_runs/r5_p3_history_continuation_audit_v1_5e9eca9`. It
authorizes only the still-unobserved `Recursive_E2_DFS` history case, with a
maximum of `33` Provider calls and `24` Vitis launches. The audit reserves the
complete `60/72` future pilot, `120/144` future formal campaign, and 10% of
the then-remaining global budget for recovery. Its file SHA-256 is
`5ca2752b0950f84e0766ca96c8fb072e359c26e1203341fd4e7c9e47f6b8aa81`;
its internal canonical hash is
`7bca0ab9e5b9bf14c648293434e1c75bd28245d9586f5871a4d972313be52cf4`.
It made zero Provider calls and zero Vitis launches and does not authorize a
Trusted revision, future execution, R5 acceptance, or R6.

The authorized continuation ran at
`/data/agrefactor_runs/r5_p3_history_continuation_real_a156709_run1` and
consumed an actual `27` Provider calls and `9` Vitis launches, bringing the
global R5 ledger to `93/33`. All three generated Candidates passed Public csim
and csynth, then stopped at Public cosim with the typed safe summary
`cosim_failed_without_typed_owner`. R2 rejected each event as
`diagnostic_item_not_actionable` before a Provider request, so R4 made no
mutation and no episode was created.

This result invalidates the predeclared `unsupported_construct` positive role
for `Recursive_E2_DFS` under the generated-Candidate protocol. It does not
justify weakening the R2 contract: the observed evidence identifies neither a
Candidate-attributable failure nor a bounded repair scope. Both frozen history
cases are now exhausted with zero eligible positive episodes, while all future
cases and outcomes remain unobserved. The machine-readable reconciliation is
`docs/roadmap/R5_HISTORY_CONTINUATION_OUTCOME_RECONCILIATION.json` with file
SHA-256
`5546893b0e66311c6c92d7f3d368336cb48a37bf05eb48d39bf72fed8dec825e`.
R5 must return to P2 and freeze an expanded history-only pool before another
real call.

## Audited R4 Predecessor Import

The accepted Package D v1.6 archive was subsequently imported through the
strict R5 predecessor boundary at repository head `d617860`. The importer
verified the authoritative archive hash, archive/content-manifest/extracted
file equality, the separate file-only audit, R4 episode and provenance hashes,
the accepted calibration certificate, changed Candidate, and fresh formal
validation before appending two agent-safe envelopes.

The immutable result is
`/data/agrefactor_runs/r5_p3_r4_predecessor_import_d617860/predecessor_import_result.json`
with file SHA-256
`7162230e3f342052d1ef2a0a583658643ae64001e28fbe913a09b497f5cd58d4`.
The independent audit is
`/data/agrefactor_runs/r5_p3_r4_predecessor_import_d617860/independent_predecessor_import_audit.json`
with file SHA-256
`35cb4f5732194e0ffa089b233e8160f85f8f13d3ef493f53e82a10a35bab28ce`.
It reports zero critical findings and used a separate Python process with
file-only input. The ledger manifest file SHA-256 is
`1a60fb01bfcc035f89be55442c8e4acbade65fc874181f5085a830993d4a364b`.

Both verified-positive episodes share source SHA-256
`ab13af723ba6d4ee3ff26cd61935720936eec3722b54893c267344081c3d5ecc`
but have two context signatures. They therefore establish a real
`Provisional` revision, not `Trusted`: R5 still needs one verified-positive
episode from a second independent source. The import consumed zero Provider
calls and zero Vitis launches, so the authoritative cumulative ledger remains
`93/33`. The machine-readable reconciliation is
`docs/roadmap/R5_R4_PREDECESSOR_IMPORT_RECONCILIATION.json`. R5 remains
unaccepted, the real future campaign remains disabled, and R6 remains stopped.

## Pre-existing History Second-Source Attempt

A separately frozen pre-existing `Recursive_E2_DFS` Candidate was exercised
through the existing Candidate validator, Provider-backed R2 advisor, R4
controller, and Vitis backend at repository head `c6e9521`. Public host and
Hidden host preflights passed before execution; future files and outcomes were
not read. The immutable run is
`/data/agrefactor_runs/r5_p3_preexisting_history_real_c6e9521`.

The real csynth diagnostic was `HLS 214-139` recursion. R2 attributed it to
the Candidate, selected `unsupported_construct` and `candidate_only`, but
reported only `medium` confidence. The accepted calibration certificate does
not authorize that confidence label, so R4 correctly abstained with
`r2_calibration_unverified`, performed no mutation, and created no episode.
The high-confidence gate was not relaxed.

The sealed archive SHA-256 is
`768a305a60b7b94c3e52f5ab753f3d001efeb46bb119deafbdf98eb76cd7935f`.
A separate file-only process independently reproduced
`clean_safe_calibration_abstention` with zero critical findings at
`/data/agrefactor_runs/r5_p3_preexisting_history_result_audit_c6e9521/independent_audit.json`.
Its file SHA-256 is
`d6bfc899a4e506502abaa05b7a6237b729712ec2099e9328c07396dcca88134f`
and canonical audit SHA-256 is
`de161431d5fb0861bd8b2800a8d7b10ae7d998f998d06ab2929c6f325e3a256f`.

The attempt consumed one Provider call and two Vitis launches. The cumulative
R5 ledger is now `94/500` Provider calls and `35/500` Vitis launches. This
source has not yet supplied a verified-positive episode; the imported
predecessor revision remains `Provisional`, the real future campaign remains
disabled, R5 remains unaccepted, and R6 remains stopped. The machine-readable
reconciliation is
`docs/roadmap/R5_PREEXISTING_HISTORY_SAFE_ABSTENTION_RECONCILIATION.json`.

Before another real call, freeze and independently zero-call audit a bounded
continuation for this same source. It may run at most two additional attempts,
must stop after the first verified-positive episode, may add at most four
Provider calls and twelve Vitis launches, and may not weaken the accepted R2
confidence/calibration boundary.

## First Bounded Continuation Attempt

The bounded continuation was zero-call audited at repository head `bd1c894`
and authorized at most two additional attempts with a first-positive stop. The
first continuation attempt consumed one Provider call and two Vitis launches.
R2 returned a calibrated `high` advisory for Candidate-owned
`unsupported_construct`, so R4 correctly crossed the calibration gate. The
mutation Provider was never called: the real `R5CandidatePromptFactory`
failed its local evidence-view contract before the Provider boundary.

The runner stopped immediately with `stopped_inconclusive`; it did not consume
the second authorized attempt. The sealed attempt archive SHA-256 is
`94f4db44837dffce36b1d7b255b1e4570c74d0431e96bf0cb58d0e7f7cc24101`.
The committed file-only auditor reports `clean_stopped_inconclusive`, zero
critical findings, and one blocking finding at
`/data/agrefactor_runs/r5_p3_preexisting_history_continuation_result_audit_5cd2c17/independent_audit.json`.
Its file SHA-256 is
`b832706762e6f3e6968c8a276da275b1e6eaaad52d23439e0df2d60fa260eb6d`
and canonical audit SHA-256 is
`999b40b9a1b119e1dbf0c5547ab1d860e7b7322a3da80771b5c04f60f5164987`.

The product-path defect was fixed at `5cd2c17`: R5 prompt reconstruction now
preserves the agent-safe evidence metadata; a Candidate owner can be projected
from R2 only when the advisory is calibration-verified and the deterministic
owner is still unknown; and R5/R3 now consume the formal
`suspected_failure_class` field with a legacy fallback. The relevant R3/R5
regression set passed (`7 + 135` tests).

The cumulative R5 ledger is now `95/500` Provider calls and `37/500` Vitis
launches. The imported revision remains `Provisional`, no new positive episode
was created, the future holdout remains unobserved, and the real campaign stays
disabled. A new zero-call resume protocol must bind this audited failure and
the fix before the one remaining attempt is used.

## Post-fix Resume Outcome

The remaining `Recursive_E2_DFS` attempt was frozen and zero-call audited at
repository head `4b2296a`. The protocol authorized exactly attempt 3, at most
two Provider calls and six Vitis launches, one Candidate mutation, and no
change to the accepted `high` calibration threshold. Public and Hidden host
adapter checks passed, and the two future cases remained unread.

The immutable run is
`/data/agrefactor_runs/r5_p3_preexisting_history_resume_real_4b2296a`.
It consumed one Provider call and two Vitis launches. R2 again returned
`medium`, so R4 safely abstained with `r2_calibration_unverified`; no mutation
or repair episode was created. A separate file-only auditor reports
`clean_exhausted_safe_abstention`, zero critical findings, and zero blocking
findings at
`/data/agrefactor_runs/r5_p3_preexisting_history_resume_result_audit_4b2296a/independent_audit.json`.
Its file SHA-256 is
`8857638476959172f3872fa82cd5a5bf39047cfb306fe33559a405170d733622`
and canonical audit SHA-256 is
`3be59d597512cb9788a04e857752c4273c4774c36f1ed0114311952c0ccd955c`.

The source is now exhausted under its frozen three-attempt boundary. This
clean abstention is not positive support and cannot promote the imported
revision. The cumulative R5 ledger is `96/500` Provider calls and `39/500`
Vitis launches. The predecessor revision remains `Provisional`, the real
future campaign remains disabled, R5 remains unaccepted, and R6 remains
stopped. The machine-readable reconciliation is
`docs/roadmap/R5_PREEXISTING_HISTORY_RESUME_OUTCOME_RECONCILIATION.json`
with file SHA-256
`527b35974ae85421e71bbebd1479e80878555a3240f59a8998b4ccd92bc8fed9`.
The next real-call prerequisite is a zero-call freeze and audit of a new,
independent history-only source; the existing future holdout cannot be reused.

## Expanded History Source And Isolation Fix

`Recursive_E1_linked_list` was frozen as an additional pre-R5 history-only
source based on its initial-repository identity and `unsupported_construct`
family before any new Provider or Vitis outcome was observed. Its source
SHA-256 is
`673c644da2e7b718b0ba50763d185cd599a69acc223927b1d730bbc51022b489`,
which is distinct from the imported predecessor source and all earlier
pre-existing-history sources. The two existing future cases remained unread.

The first expanded-history run at repository head `1190773` consumed one R2
Provider call and two Vitis launches. R2 produced a calibrated `high`
Candidate-only `unsupported_construct` advisory, but the mutation Provider was
not called. The historical symbol-isolation adapter used preprocessor aliases;
Vitis resolved the renamed top while the deterministic Candidate response
contract, correctly operating without a C preprocessor, could not observe its
definition. R4 stopped with `pre_provider_model_adapter_failure`, zero
mutations, and an unchanged Candidate.

A separate file-only auditor reports
`clean_pre_provider_model_adapter_failure`, zero critical findings, and one
blocking finding at
`/data/agrefactor_runs/r5_p3_expanded_history_result_audit_1190773/independent_audit.json`.
Its file SHA-256 is
`669d431211330d17697d307e649b929a532f9823da0b8a6c0ff44a1b5c6ad75f`
and canonical audit SHA-256 is
`e5d25df4ad1f6498550585e99600433909f012c5746214a69229f9f0536c29f0`.

The adapter was corrected at `39d8c56` by adding content-addressed materialized
identifier isolation v2. It changes symbol names mechanically in the frozen
Candidate text, so both the compiler and deterministic response contract see
the same Candidate ABI; it does not change Candidate logic. Isolation v1
remains available for reproducing prior evidence. The cumulative R5 ledger is
now `97/500` Provider calls and `41/500` Vitis launches. The machine-readable
reconciliation is
`docs/roadmap/R5_EXPANDED_HISTORY_ISOLATION_FAILURE_RECONCILIATION.json`
with file SHA-256
`89db1fa9ccd893253114c5829cd1259e41f47fef472afd25c312cc04842181e4`.
The corrected source retains two bounded attempts, subject to a new zero-call
audit; no Trusted revision or future campaign has been authorized.

The first materialized-isolation attempt at repository head `451d7c1`
eliminated the adapter failure. Its Public and csynth stages were physically
executed and R2 again identified Candidate-owned `unsupported_construct`, but
reported `medium` confidence. R4 therefore safely abstained before mutation.
The file-only audit at
`/data/agrefactor_runs/r5_p3_expanded_history_materialized_result_audit_451d7c1/independent_audit.json`
reports `clean_safe_calibration_abstention`, zero critical findings, and zero
blocking findings. Its file SHA-256 is
`8d5ac56b5d3b13738033684093957d4d83f39669fd95b092d9e111381e70dda3`
and canonical audit SHA-256 is
`aafeb46594a3fd936b61ee0ab97dc5c7ccdce90d8a24aa2fb73464b7b5d51e7a`.

This attempt consumed one Provider call and two Vitis launches, bringing the
cumulative R5 ledger to `98/500` and `43/500`. It produced no positive episode
and does not promote the predecessor revision. The machine-readable
reconciliation is
`docs/roadmap/R5_EXPANDED_HISTORY_MATERIALIZED_ABSTENTION_RECONCILIATION.json`
with file SHA-256
`88c9749ef13cd5f3322097e7636e4bbf1dd0656ea906314942b23eacd4e7e9de`.
Exactly one attempt remains for this source and requires a new zero-call audit.

## Expanded History Final Attempt And Next Source Freeze

The final pre-authorized `Recursive_E1_linked_list` attempt ran at repository
head `b871aa4`. It consumed one Provider call and two Vitis launches. R2 again
reported `medium` confidence for the Candidate-owned `unsupported_construct`
diagnosis, so the accepted calibration certificate correctly prevented a
mutation. No repair episode was created and the confidence threshold was not
weakened.

The sealed archive SHA-256 is
`a2ed0905d51877920b14fac7eadbbd81e7a112bf051a7093c22329ef6c59f75f`.
A separate file-only process reproduced
`clean_safe_calibration_abstention`, with zero critical and zero blocking
findings, at
`/data/agrefactor_runs/r5_p3_expanded_history_final_result_audit_b871aa4/independent_audit.json`.
Its file SHA-256 is
`20af0fc5944e06ff9c2c408bc6b215c2fc7635e4e127d65c13413527b612cab1`
and canonical audit SHA-256 is
`e03068e400085a86abcf91fdc330da5a6bc7c8c8c5aac2ac0162ee70a0786734`.
The source is exhausted after three bounded attempts. The cumulative ledger is
now `99/500` Provider calls and `45/500` Vitis launches. The machine-readable
reconciliation is
`docs/roadmap/R5_EXPANDED_HISTORY_FINAL_ABSTENTION_RECONCILIATION.json`.

Before any further real outcome was observed, `Pointer_E1_DNN` was frozen as
the next independent history-only source. It existed in the initial repository
commit and contains direct Candidate-side dynamic-allocation constructs, while
its source SHA-256
`68aa374f623fd2509c68eb8cf92d08b542fce998a4517336a30b8bb98ec033b4`
is distinct from every observed history source. The adapter uses materialized
symbol isolation only; it does not change Candidate logic, invent expected
outputs, read either future holdout, or authorize a Trusted revision. Its
plan is
`configs/r5/preexisting_history/pointer_e1_dnn_expanded_plan.json`. A clean
zero-call protocol audit remains mandatory before one bounded acquisition.

## Pointer Deterministic Boundary And Quicksort Freeze

The frozen `Pointer_E1_DNN` source passed its zero-call protocol audit at
repository head `7a379c4`. The audit is
`/data/agrefactor_runs/r5_p3_pointer_e1_dnn_protocol_audit_7a379c4/protocol_audit.json`;
its file SHA-256 is
`d11e6b136f21101a1b2861cde2455d2b37a86d8e27ad6e040cf9591cd5cd6a59`
and its canonical audit SHA-256 is
`87ca49ff825b220a10e0fbfeaf759e0d5971bd0f9b3dd0b88939ffdf4501f750`.

The real run at
`/data/agrefactor_runs/r5_p3_pointer_e1_dnn_real_7a379c4` consumed zero
Provider calls and two Vitis launches. Vitis emitted two exact `HLS 214-194`
dynamic-allocation errors. The deterministic parser established Candidate
ownership, `unsupported_construct`, `candidate_only`, and `high` confidence
before R2. R2 therefore correctly rejected the event as
`owner_not_unknown_or_review`; R4 did not mutate and no episode was created.

The separate file-only audit reports
`clean_pre_r2_deterministic_boundary`, zero critical findings, and one
blocking boundary finding at
`/data/agrefactor_runs/r5_p3_pointer_e1_dnn_result_audit_7a379c4/independent_audit.json`.
Its file SHA-256 is
`0dd47bb0e8d9bb0654971b3833ad8f8f1b8f058f847749e99a0edb10cddfb012`
and canonical audit SHA-256 is
`ba29150983628c68a81cf7e5753eb5315a6d13d93275ef369ee207fa4202a7a5`.
This is a valid pre-R2 recovery boundary result, not an R2/R4 history
positive. The cumulative R5 ledger is now `99/500` Provider calls and
`47/500` Vitis launches.

Before observing another real outcome, `C2HLSC_quicksort` was frozen as the
next independent history-only source. It existed in the initial repository,
contains bounded-array recursion without dynamic allocation, and is distinct
from every observed history source. Its checked-in `qs.cpp` is the executable
reference oracle and `kernel.cpp` is the pre-R5 Candidate. Public and Hidden
tests compare their output arrays; materialized symbol isolation changes only
linkage identifiers. This source is ordered ahead of `mergesort`, whose
dynamic allocation is already likely to belong to the deterministic pre-R2
boundary. The quicksort plan is
`configs/r5/preexisting_history/c2hlsc_quicksort_expanded_plan.json`. A clean
zero-call protocol audit is mandatory before one bounded real attempt. Future
holdouts remain unread, R5 remains unaccepted, and R6 remains stopped.

## Quicksort Response-Contract Failure And General Fix

The quicksort zero-call audit passed at
`/data/agrefactor_runs/r5_p3_c2hlsc_quicksort_protocol_audit_98a72cc/protocol_audit.json`.
Its file SHA-256 is
`fbaf2a46068ec9872e0bcfc1b7c9814c4e25e1e4a0fbd2e97ca2be121b2fb599`
and canonical audit SHA-256 is
`9d92af0dd18ac63f2fba962ac4a436350de4f364d1d024fc000704a6a564c0fe`.
Both Public and Hidden host differential checks passed without a Provider or
Vitis call.

The first real quicksort attempt at
`/data/agrefactor_runs/r5_p3_c2hlsc_quicksort_real_98a72cc` entered the full
R2-to-R4 path. The real `HLS 214-139` recursion diagnostic was initially
unknown/review evidence; R2 returned a calibrated `high`, Candidate-only
`unsupported_construct` advisory. R4 authorized one mutation Provider call,
but the returned Candidate failed the deterministic response contract before
mutation or formal validation. The attempt consumed two Provider calls and
two Vitis launches and produced an inconclusive episode with an unchanged
Candidate.

A separate file-only audit reports
`clean_provider_or_response_contract_failure`, zero critical findings, and
one blocking finding at
`/data/agrefactor_runs/r5_p3_c2hlsc_quicksort_result_audit_98a72cc/independent_audit.json`.
Its file SHA-256 is
`513d31499086b77328f260efcdfc9e2e1874a712283b02722faae1a8e65d9d06`
and canonical audit SHA-256 is
`b627d3542464c31061f09a663deaf0aaf0d06a0f8de67953164f73be697d0f7e`.

The general mutation boundary now carries only stable, agent-safe response
reason codes when a Candidate response fails; raw output and private reasoning
remain absent. R5 prompts also bind the exact current Candidate top-level name
and declaration text that the deterministic response contract enforces. This
aligns the prompt with the existing validator without weakening the validator
or adding a quicksort-specific rule. The source retains two bounded attempts,
but a new post-fix plan and zero-call audit are required before either is used.
The cumulative R5 ledger is `101/500` Provider calls and `49/500` Vitis
launches. R5 remains unaccepted and R6 remains stopped.

The remaining quicksort attempts are now frozen in
`configs/r5/preexisting_history/c2hlsc_quicksort_post_fix_resume_plan.json`.
The plan binds the failed archive, independent audit, reconciliation, general
fix commit `2b66276`, unchanged Candidate and oracle identities, and the
`101/49` cumulative ledger. It authorizes at most two more attempts and retains
the first-positive stop. A new zero-call protocol audit remains mandatory.

## Quicksort COSIM Interface Contract Reconciliation

The second bounded quicksort attempt ran at repository head `ef0fa9d`. R2
again produced a calibrated `high`, Candidate-only `unsupported_construct`
advisory, and R4 performed exactly one mutation. The resulting Candidate hash
is `ffee44eba1bd9896f061ccb14d049a9569d82bd9daa98fce061b0aec0958c3c7`.
It passed the Public csim and csynth stages. Public RTL COSIM did not start,
because Vitis required an explicit depth for the `gmem` MAXI interface and the
research acquisition adapter had supplied only the legacy schema-1 runtime
contract. This is a validation configuration failure, not a Candidate failure
or a verified-positive episode.

A separate file-only audit reports
`clean_cosim_interface_depth_configuration_failure`, zero critical findings,
and one blocking configuration finding. Its output is
`/data/agrefactor_runs/r5_p3_c2hlsc_quicksort_post_fix_result_audit_1fe1206`,
with file SHA-256
`8b6df6976b753ec4f858fa7ef51b5b0fdd0e8e08430df8084de8e91092c56228`
and canonical audit SHA-256
`b0599bac6100207518b8d1e74dc895c6948b41d6a4bdec7c00b2cc4b7fbba1ea`.
The attempt consumed two Provider calls and five Vitis launches, bringing the
cumulative R5 ledger to `103/500` and `54/500`.

The general acquisition boundary now accepts a frozen schema-2 Public runtime
contract and passes its validated `cosim_interface_depths` to the existing
Candidate validation orchestrator. The fix neither changes Candidate logic nor
adds a second Vitis flow. The machine-readable reconciliation is
`docs/roadmap/R5_C2HLSC_QUICKSORT_COSIM_INTERFACE_RECONCILIATION.json`.
Exactly one quicksort attempt remains. It is frozen in
`configs/r5/preexisting_history/c2hlsc_quicksort_post_cosim_fix_resume_plan.json`
with `arr: 9`, derived from the fixed Public array contract. A clean zero-call
protocol audit is mandatory before that attempt. R5 remains unaccepted, no
Trusted revision has been created, future holdouts remain unread, and R6
remains stopped.

## Quicksort Final Safe Abstention

The third and final bounded quicksort Provider attempt ran at repository head
`da58b45`. Its zero-call protocol audit first passed with Public and Hidden
host oracle checks, the frozen schema-2 runtime contract, the accepted
calibration certificate, and the full parent evidence chain intact. The real
attempt then consumed one Provider call and two Vitis launches. R2 identified
Candidate-only `unsupported_construct` with a bounded iterative-rewrite intent,
but reported `medium` confidence. The accepted calibration gate therefore
stopped before mutation. The threshold was not weakened.

A separate file-only audit reports `clean_safe_calibration_abstention`, zero
critical findings, and zero blocking findings at
`/data/agrefactor_runs/r5_p3_c2hlsc_quicksort_final_result_audit_da58b45/independent_audit.json`.
Its file SHA-256 is
`6b26fcac656297e754ff1e584377d236dcdf9681659d834f5c173428c543b574`
and canonical audit SHA-256 is
`589cb6588381257978ab8de9acd623c1381e8f7afeda5d29be4b89ed9a629cd9`.
The quicksort source has now consumed all three bounded Provider attempts. Its
total is five Provider calls and nine Vitis launches; the cumulative R5 ledger
is `104/500` and `56/500`.

The second attempt's changed Candidate remains sealed and content-addressed.
It was legally authorized and passed Public csim and csynth, but it is not a
verified positive because its required COSIM never started. R5 may next design
a zero-Provider, fresh formal revalidation protocol for that exact Candidate.
Such a protocol must bind the original authorization and Candidate hash, rerun
the complete required prefix with the corrected runtime contract, receive a
separate clean audit, and leave the original episode immutable. Until those
conditions are implemented and independently verified, no new positive
episode or lifecycle promotion is permitted. Future holdouts remain unread,
R5 remains unaccepted, and R6 remains stopped.

## Authorized Candidate Revalidation Freeze

The exact changed Candidate from the second quicksort attempt is frozen for a
zero-Provider fresh revalidation. This is not another repair attempt: the
Candidate bytes, original A2 authorization, source episode, source archive,
prior configuration-failure audit, reference, Public test, Hidden test, and
schema-2 runtime contract are all content-addressed before execution. The plan
is
`configs/r5/preexisting_history/c2hlsc_quicksort_authorized_candidate_revalidation_plan.json`.

The execution reuses `LocalCandidateValidationHandlerFactory` and
`ValidationOrchestrator`, so it does not add a product entrypoint or a second
Vitis flow. Provider use is forbidden. At most three physical Vitis launches
are allowed: Public csim, csynth, and Public RTL COSIM; Hidden validation keeps
the existing host-differential path. A separate file-only auditor must verify
the full fresh prefix, Candidate identity, semantic preservation, original
episode immutability, and zero critical findings. The executor cannot declare
itself positive, create a Trusted revision, authorize the future campaign,
accept R5, or start R6.
