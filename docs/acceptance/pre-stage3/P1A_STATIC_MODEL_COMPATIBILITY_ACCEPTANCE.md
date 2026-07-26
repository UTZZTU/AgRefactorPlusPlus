# P1-A Static Model Compatibility Acceptance

## Status

```text
package=P1-A
status=deterministic_accepted
implementation_parent=b6d97145b6622be97b9799ee8a14275beb925f26
implementation_commit=e9f4a51744ce44c04236466450b8af85ebf9be9c
commit_subject=feat: add static model compatibility profiles
branch=stage2-general-feedback
local_head_equals_remote_head=true
worktree_clean=true
```

P1-A is accepted as a deterministic implementation milestone. This does not claim network compatibility for every concrete model version and does not replace the later P1-D bounded real-model smoke.

## Accepted capabilities

```text
six canonical static family profiles
DeepSeek / Kimi / GLM / MiniMax / Qwen
Generic OpenAI-compatible
typed verification status
reasoning low/medium/high map/omit/reject
parameter alias and rejection policy
strict unknown-family failure before Provider
historical openai alias to generic-openai-compatible
existing parameter precedence preserved
Provider remains transport-only
credential-like parameter rejection
```

## Evidence

```text
validation_strategy=detached_git_worktree_then_exact_patch
baseline_full_unittest=873/873
p1a_full_unittest=889/889
test_delta=+16
worktree_affected_test_files=8/8 passed
main_path_affected_test_files=8/8 passed
explicit_family_consumer_audit=passed
pytest_installed_or_used=false
patch_id=bc4e6a58447f86129dcf54ed536f3456fc8a9a04
```

## Finding disposition

```text
F02 closed: explicit unknown family now fails before Provider
F03 invariant confirmed: existing merge precedence retained
F04 invariant confirmed: Provider product code unchanged
```

Still open:

```text
F01 P1-C effective reasoning/config wiring
F05 P1-C Legacy HLSAgentLoader migration
F06 P1-B/P1-C duplicated Legacy pricing
F11 P1-B/P1-C official pricing migration
F12 P1-B formal cost estimation
F13 P1-B pricing provenance schema
F15 P1-B native-currency cost structure
```

## Execution boundary

```text
real_model_api_calls=0
formal_cpp_acceptance=0
formal_csim_acceptance=0
formal_csynth_acceptance=0
formal_vitis_acceptance=0
deterministic_fixtures_may_invoke_local_tools=true
```

## Artifacts

```text
artifact_dir=/data/agrefactor_runs/pre_stage3_p1a_static_model_profiles_v3_20260722_145219
```

SHA-256:

```text
import_preflight.log  5d2459b9dd77a941f1d281e9ace4b90bfea34d2b723da4d93948a31e85616a5a
baseline_full_unittest.log  810a7f7d7354c68755b25efa56f6f14610c00465e74ac439540a0b13e1c68223
model_family_literals.tsv  92b5217749ae800971fd6b24eaf28d99eaab6abdb33941d84c9227ddd59b7e0e
p1a.patch  b2a5591cf3aa11f0d2ab49f1b406f51b0f8bfe1c5ab3908f80ada72b8e38ef42
worktree_targeted_unittest.log  0ca4f9da87b482c7237663a1efe76eb801fd9524d3a77b80364ce5a93d4c7478
worktree_full_unittest.log  743da284a7a142808fd020396509fe714ba836b3fa1625b7b7d837cc9b4355af
main_staged.patch  b2a5591cf3aa11f0d2ab49f1b406f51b0f8bfe1c5ab3908f80ada72b8e38ef42
main_targeted_unittest.log  f0abe57e3a9a909bb052b49d7611530acacb3e6d27ec3167b91351620e9de602
staged_stat.txt  629dc14b5d4a6435e7cb1e6244db4170be0050808388c9bdf0311722b3bf03a3
```

## Preserved exclusions

```text
official price values
cost estimator wiring
Legacy product-path migration
normal refactor/optimize/full CLI
Budget default/ceiling/user resolver
P5 output
P0 DFS
dynamic model probing
automatic routing
currency conversion
Provider vendor branches
```

## Next

```text
P1-A deterministic acceptance=completed
P1-B official pricing and native-currency cost structure=active
P1-C unified effective config=pending
P1-D bounded real-model smoke=pending
```
