# Pre-Stage-4 P4-0B Decision Record — Global Typed Preflight

## Status

```text
DESIGN_SOURCE=PRE_STAGE4_PRODUCT_VALIDATION_HARDENING_CONTRACT.md
IMPLEMENTATION_BASE=11df86f199b8da03ed83baf9119841b3610cdad4
PACKAGE=P4-0B
BEHAVIOR_SCOPE=host_preflight_only
NATIVE_VITIS_CSIM_CHANGED=false
CSYNTH_ORDER_CHANGED=false
COSIM_ADDED=false
OPTIMIZER_POLICY_CHANGED=false
```

## Problem

The historical preflight launched Testbench, reference, and Candidate in one
compiler/linker command. Compile diagnostics with a source path could often be
assigned, but final link diagnostics and mixed failures could not be assigned
reliably. The S3.8 `cand-2` failure therefore reached `review_unknown` even
though the Candidate source was the responsible component.

Adding more stderr filename or regex rules would not make ownership
architecturally reliable. P4-0B changes the physical execution topology instead.

## Accepted execution topology

```text
compile Testbench → testbench.o
compile reference → orig_code.o
compile Candidate → refactor_code.o
→ inspect Testbench/reference/Candidate symbols with nm
→ run Testbench+reference LTO interface probe
→ run Testbench+Candidate LTO interface probe
→ final full link
```

Each compile unit has a predetermined owner. Top-symbol checks are based on
object-file evidence. ABI checks are separate LTO probe invocations, so a
Candidate ABI failure is owned by the Candidate probe rather than inferred from
the final mixed link.

The final link is still authoritative for cross-component integration. A final
link failure that is not already isolated remains `link_failed` plus
`ownership_unknown`; it is never silently assigned to Candidate.

## Typed reasons

```text
testbench_compile_failed
reference_compile_failed
candidate_compile_failed
candidate_top_missing
reference_top_missing
interface_mismatch
link_failed
toolchain_failed
configuration_failed
ownership_unknown
```

The first reason is the primary reason code. Additional reason codes preserve
unknown-safe context, for example:

```text
link_failed + ownership_unknown
toolchain_failed + ownership_unknown
```

## Routing

```text
Candidate compile/top/interface failure
→ owner=candidate
→ route=repair_candidate
→ current Candidate stops before later validation

Reference compile/top failure
→ owner=original
→ inspect_original / repair_original according to the caller

Testbench compile or reference-side public ABI mismatch
→ owner=testbench
→ bounded Testbench repair where allowed

Tool launch/timeout
→ owner=toolchain
→ block source repair

Invalid top configuration
→ owner=configuration
→ fix_configuration

Unisolated final link failure
→ owner=unknown
→ review_unknown
```

For Stage 3 optimization, a Candidate-owned preflight failure becomes a rejected
Candidate. Existing optimizer state-machine behavior then preserves
`best_correct`, skips Public/CSYNTH/Hidden for that Candidate, and continues only
when policy and budget permit. P4-0B does not add a same-level retry or change
`safe-v1` search order.

## Budget semantics

P4-0B performs prospective validation before the first physical launch.

Without explicit top names:

```text
4 tool calls
4 compile calls
```

With both reference and Candidate top contracts:

```text
9 tool calls
6 compile calls
```

The six compiler-family calls are three independent compilations, two LTO
interface probes, and one final link. The three additional tool calls are object
symbol inspections. Only launched work is consumed, but enough budget for the
complete requested staged preflight must be available before the first launch.

This is an intentional evidence-correctness change. Mode-specific defaults are
not changed in P4-0B; they remain P4-0F work.

## Evidence and privacy

The operator invocation artifact retains commands, physical substeps, return
codes, budget snapshots, and paths. Agent-safe feedback retains only typed
reason, owner, failed component, substep count, and sanitized diagnostics. Raw
commands, stdout/stderr, artifacts, absolute paths, and source text are not
copied into agent-safe reports.

## Explicit non-goals

P4-0B does not:

- turn the current host differential runner into native Vitis CSIM;
- add RTL COSIM;
- change CSYNTH or Hidden order;
- implement Flash/Thinking changes;
- implement Refactor/Optimize/Full budget profiles;
- implement `dynamic-v1`;
- claim that host preflight proves functional correctness, synthesizability, or
  PPA improvement.

## Invocation execution-status compatibility

```text
P4_0B_INVOCATION_EXECUTION_COMPATIBILITY=v1
ordinary_nonzero_process_return=execution.status:completed
typed_preflight_result=failed
typed_reason_code=authoritative
failed_component=authoritative
substep_status_and_returncode=authoritative
```

`execution.status` retains its pre-P4-0B meaning: it distinguishes a normally
completed subprocess from launch, timeout, evaluator, or budget failures. It is
not the correctness verdict. Candidate/Testbench/Reference ownership and the
Preflight verdict come from typed result fields and staged evidence.

## Direct replay-tool entrypoint

```text
P4_0B_DIRECT_REPLAY_ENTRYPOINT=v1
entrypoint=python tools/p4_0b_preflight_replay.py
repository_root_source=Path(__file__).resolve().parents[1]
direct_subprocess_regression=true
```

The replay tool is a repository command-line utility and must work when invoked
by file path. It resolves the repository root from its own location before
importing `agrefactor`; correctness does not depend on a caller-provided
`PYTHONPATH`.

## Staged Preflight budget accounting

```text
P4_0B_STAGED_BUDGET_ACCOUNTING=v1
prospective_plan_check=before_first_launch
consume_only_physically_launched_substeps=true
compatibility_no_top_full_pass=tool:4,compile:4
dual_top_full_pass=tool:9,compile:6
first_compile_failure_after_capacity_check=tool:1,compile:1
testbench_repair_failed_then_passed=tool:5,compile:5
legacy_preflight_csynth_csim_full_chain=tool:7,compile:5
```

P4-0B changes the physical call graph, so tests that asserted the former
single-process Preflight accounting are migrated to staged accounting. This is
not a budget-profile change: P4-0F still owns mode defaults, ceilings, and Full
reserve policy. P4-0B only records and enforces the calls it actually launches.

<!-- PRE_STAGE4_P4_0B_REPOSITORY_CLOSURE:BEGIN -->
## Repository closure boundary

```text
P4_0B_ACCEPTANCE=accepted_local_validation
P4_0B_FOCUSED_TESTS=64
P4_0B_FULL_REGRESSION_TESTS=2044
P4_0B_CAND2_REPLAY=passed
P4_0B_REPOSITORY_CLOSURE=prepared_for_commit
NEXT_PRE_STAGE4_PACKAGE=P4-0B-R_BOUNDED_OPTIMIZE_CANDIDATE_RECOVERY
```

The next package is separate from P4-0B. P4-0B-R may reuse typed
`repair_candidate` ownership, but P4-0B itself does not execute Optimize
Candidate repair.
<!-- PRE_STAGE4_P4_0B_REPOSITORY_CLOSURE:END -->
