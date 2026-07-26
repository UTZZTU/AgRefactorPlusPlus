# Documentation Cleanup and Stage 3 Planning Freeze Acceptance

## Scope

本包只执行：

1. 删除一次性交接和过期 Bridge/reference；
2. 合并其中长期有效经验到 history；
3. 收敛当前权威状态；
4. 分离当前产品验证与 Legacy baseline；
5. 修复 README/USAGE 命令格式；
6. 冻结可直接实施的 Stage 3 合同；
7. 记录最新真实 post-CLI smoke。

没有 Stage 3 功能实现。

## Deleted Current-Tree Files

```text
docs/reference/NEXT_CHAT_HANDOFF.md
docs/reference/PRE_STAGE3_BRIDGE.md
docs/reference/P0_TESTBENCH_REPAIR_RETRY_FEEDBACK.md
```

原内容仍保留在 Git 历史；长期有效经验已合并到：

```text
docs/history/PRE_STAGE3_TRANSITION_LESSONS.md
```

## Current Authority

```text
PROJECT_STATE=concise_current_only
GOAL_TRACEABILITY=current_table_only
REPRODUCTION_STATUS=current_product_only
LEGACY_BASELINE_STATUS=separate
PRE_STAGE3_PRODUCTIZATION_PLAN=closed_historical_contract
STAGE3_IMPLEMENTATION_CONTRACT=frozen
```

## Validation

```text
validated_at_utc=2026-07-26T19:42:01Z
full_unittest=1500/1500
markdown_links=passed
stale_reference_paths=absent
current_authority_conflict_scan=passed
real_smoke=accepted
real_smoke_run_id=post-cli-real-smoke-20260726_192331
MODEL_API_CALLED_FOR_SMOKE=true
REAL_VITIS_RUN_FOR_SMOKE=true
RUNTIME_CODE_CHANGED=false
PRE_STAGE3_CLOSED=true
STAGE3_PLANNING_FROZEN=true
STAGE3_IMPLEMENTATION_ALLOWED=true
STAGE3_IMPLEMENTATION_STARTED=false
NEXT_STEP=STAGE3_IMPLEMENTATION_STEP_1
```

## Decision

Stage 3 planning is frozen. The next allowed package is S3.1 Candidate State Foundation as defined in `STAGE3_IMPLEMENTATION_CONTRACT.md`.
