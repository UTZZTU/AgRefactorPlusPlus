# Stage 2.7.6 Evidence-gated Contract/Parser Delta and Ground-truth Revalidation

## Status

```text
code_baseline=b1a787ab0e41b382fec25973968e2b162a500f85
contract_delta_required=false
parser_delta_required=false
code_delta_applied=false
ground_truth_labels=16/16
baseline_full_chains=7/7
fault_matrix_matches=9/9
```

## Real-evidence review

Source evidence:

```text
/data/agrefactor_runs/stage2_7_5_real_network_candidate_repair_20260719_211334
```

The Stage 2.7.5 model response passed the strict CandidateResponseContract and was
replayed through real g++ Preflight.

```text
replayed_preflight_failure_kind=link_error
replayed_preflight_failure_owner=unknown
```

The proposal still satisfies the deterministic structural contract: one complete C++
replacement, one required top definition, exact interface, no `main`, changed content,
and no commentary or diff. Compile and semantic correctness remain the responsibility
of real validation; the response contract is not expanded into a second compiler.

## Parser decision

```text
real_csynth_invocations_in_2_7_5=0
parser_delta_required=false
```

No CSYNTH log exists from the real-model smoke, so it cannot justify a new Vitis
diagnostic parser rule. Existing unknown diagnostics remain blocking `UNKNOWN`.

## Independent ground truth

```text
baseline_labels=7
fault_labels=9
total_labels=16
matches=16/16
```

The labels remain manually authored and are not derived from runtime predictions.

## Re-executed scenarios

```text
baseline real full chains=7
fault real local chains=5
fault deterministic routes=4
total real scenarios=12
total deterministic scenarios=4
```

Every baseline traversed:

```text
Preflight → Vitis 2023.2 CSYNTH → Public CSIM → Hidden CSIM → accepted
```

The fault matrix rechecked owner, failure stage, route, terminal state, exact budget,
and Hidden suppression.

## Physical usage

```text
pass matrix=42/21/7/14/0
fault matrix=13/8/2/3/0
combined arithmetic=55/29/9/17/0
```

The combined figure is not one shared budget. Each runner maintained its own exact
hard budget.

## Safety and scope

```text
network model executed=false
llm calls=0
hidden leakage=false
optimizer executed=false
feature commit created=false
```

This milestone does not claim arbitrary HLS coverage, statistical routing accuracy,
multi-version Vitis support, or Stage 3 optimization.

## Artifacts

```text
/data/agrefactor_runs/stage2_7_6_evidence_gated_ground_truth_revalidation_20260719_215820/acceptance
```

Key files:

```text
stage2_7_6_evidence_gate.json
stage2_7_6_ground_truth_revalidation_summary.json
stage2_7_6_pass_matrix.json
stage2_7_6_fault_matrix.json
stage2_7_6_independent_ground_truth.json
stage2_7_6_agent_safe_index.json
```

Next:

```text
Stage 2.7.7 Cross-stage Regression and Stage 2.8 Handoff
```
