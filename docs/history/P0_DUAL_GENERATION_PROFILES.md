# P0 Step C: Dual Testbench Generation Profiles

## Baseline

```text
BASE_HEAD=439e8db69b5115932a291038fc3e8017363747df
ACTIVE_STEP=C
DEFAULT_LLM_CALLS=32
```

## Profiles

### `lightweight` (default)

The normal source-only command uses one direct Public Testbench generation pass
and one held-out trajectory/round when the corresponding split is automatic.
No coverage loop is launched for Public generation.

### `coverage-enhanced` (explicit)

The user must select this profile explicitly. Public generation runs the
configured number of coverage rounds and independent trajectories, selects the
best qualified trajectory, and retains per-round coverage artifacts. Automatic
held-out generation uses the same trajectory count and the existing enhanced
round budget.

## CLI

```text
--test-generation-profile lightweight|coverage-enhanced
--public-coverage-rounds N
--test-generation-trajectories N
```

The profile, requested values, effective Legacy settings, and selected strategy
are persisted in the source request and run metadata. Public/Hidden direction
remains one-way and the default model-call ceiling remains unchanged.

```text
STEP_C=completed
ACTIVE_STEP=D
MODEL_API_CALLED=false
VITIS_RUN=false
P0_STATUS=active_retry_required
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```
