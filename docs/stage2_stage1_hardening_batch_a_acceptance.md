# Stage 2.7.3 Stage 1 Hardening Batch A Acceptance

## Status

```text
411d1e2b37ae6e620c0b759b98f7e8277cb851c4
feat: harden target execution profiles

24/24 targeted
816/816 full unittest
```

## Stable named profile

The committed registry contains exactly:

```text
vitis-2023.2-default
```

Its existing device, 5 ns clock, `-D XILINX` flag and `vitis-run` command remain
backward compatible. No second Vitis version or device matrix was added.

## Execution contract

The effective TargetProfile records:

```text
executable
settings_path
parser_profile
resource_limits
field_provenance
```

Executable and settings environment overrides remain explicit and their provenance is
recorded in `csynth_invocation.json`. A settings path produces a sourced `bash -lc`
launcher and matching version probe.

## Provenance and parser identity

`effective_target_profile.json` schema version 2 separates effective values from
per-field provenance. CSYNTH invocation and agent-safe stage metadata expose parser
identity, basic resource limits and safe provenance labels without exposing command
paths to the model prompt.

## Secret boundary

The committed target JSON rejects credential-like keys. `.env.example` contains a blank
`OPENAI_API_KEY=` entry and no credential value.

## Execution class

```text
deterministic acceptance: true
network model executed: false
real tool executed: false
additional Vitis versions added: false
Batch B executed: false
```

Acceptance directory:

```text
/data/agrefactor_runs/stage2_7_3_stage1_hardening_batch_a_20260719_190809/acceptance
```

Next:

```text
Stage 2.7.4 Formal Repair-aware UnifiedRunner / CLI
```
