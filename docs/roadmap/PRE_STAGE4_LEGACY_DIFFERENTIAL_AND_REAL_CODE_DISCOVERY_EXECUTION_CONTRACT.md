# Legacy Differential and Real-code Discovery Execution Contract

## Authority

This document makes two previously described ideas explicit repository execution contracts.

## Lane A - Legacy differential regression

Start only after `P4_0F_R5_ACCEPTED=true` and before P4-0F-Final freezes budgets.

Compare Original AgRefactor and AgRefactor++ under identical source/top, exact model configuration, suites, TargetProfile, hard budgets, and an independent final qualification auditor. Do not compare self-reported success fields.

First cases: `dfs`, `ahocorasick`, `strassen`, `linkedlist`, and `mergesort`.

A P0/P1 product regression reopens R5.x. Model-quality differences remain diagnostic data. An unavailable Original baseline is recorded as typed `baseline_unavailable` and does not fabricate a comparison.

## Lane B1 - Real-code discovery batch A

Start after R5 acceptance and before P4-0F-Final. Focus on Refactor integration eligibility and real consumption samples. Candidate categories include recursion, dynamic memory, lists/trees, STL, aliasing, global state, and real application functions.

Every case must have a named top, controllable Public input, observable output, runnable reference, and complete CSIM/CSYNTH/COSIM contract before launch.

## Lane B2 - Real-code discovery batch B

Start after P4-0G and before P4-0H. Add matrix, image/video, cryptography, signal processing, irregular memory, and dataflow. Direct Optimize requires an independently qualified baseline. Full cases require proven Refactor eligibility and Optimize contract compatibility.

## Campaign invariants

- immutable commit/model/target/budget/recovery policy;
- independent artifact roots;
- no source changes during a campaign;
- safe case failures continue;
- false acceptance, Hidden leak, stale accepted evidence, identity mixing, budget bypass, best_correct corruption, or shared environment failure stop globally;
- product corrections require a new commit, campaign ID, and complete rerun.

Discovery is not P4-0H authority evidence and cannot be used to claim stable success rate or PPA superiority.
