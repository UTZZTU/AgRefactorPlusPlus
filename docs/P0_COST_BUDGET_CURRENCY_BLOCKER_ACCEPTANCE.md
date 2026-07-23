# P0 Cost-Budget Currency Blocker Correction

## Authority and observed failure

The sole authority remains `PRE_STAGE3_PRODUCTIZATION_PLAN.md`,
sections 7.4 and 10.1. The first real P0 driver exposed this ordinary
source-CLI failure before any model or tool launch:

```text
Command failed: cost_budget_currency requires cost_budget
```

A model pricing snapshot may declare its native currency even when the
user has not declared a soft Cost budget. The runtime budget contract
must therefore persist `cost_budget_currency` only when
`--cost-budget` is actually present.

## Corrected contract

```text
pricing snapshot present + no --cost-budget
-> cost_budget=None
-> cost_budget_currency=None
-> normal source run continues

pricing snapshot present + --cost-budget X
-> cost_budget=X
-> cost_budget_currency=pricing snapshot currency
```

Token/Cost remain observed-only and non-blocking. This correction does
not run or close P0, close Pre-Stage-3, or start Stage 3.

## Deterministic evidence

```text
base_head=ef17750b412a77ddd9f71b8c9cd5aa063c4b26ba
new_tests=3
zero-LLM ordinary CLI probe=required
model_api_called=false
vitis_run=false
P0=active, retry required after this correction
PRE_STAGE3_CLOSED=false
STAGE3_STARTED=false
```
