# P0 Step F Lightweight Audit Decisions

## Scope

This record follows the first real `lightweight` DFS acceptance run. The run
completed successfully, but the held-out generated Testbench used a local
semantic reference instead of deriving expected outputs from an actual call to
the Original top. Original-source gcov therefore recorded zero executed lines.

## Immediate correction

The held-out generation path now has three aligned requirements:

1. The Prompt requires an actual Original-top call and prohibits substituting a
   Testbench-owned semantic reference, golden model, oracle algorithm, or copied
   implementation.
2. The structural Testbench contract requires at least one Original-top call
   when the Candidate ABI is externally frozen for held-out generation.
3. Generation-time qualification rejects a held-out Testbench when gcov reports
   executable Original-source lines but zero executed Original lines.

The Public lightweight generation contract remains unchanged. In particular,
the observed Public C/C++ linkage repair remains available as a baseline for
future memory experiments.

## Preserved original AgRefactor architecture

The following original architecture remains unchanged:

- `system_identifier`
- `recursion_identifier`
- `heap_based_identifier`
- `stack_based_identifier`
- `pointer_identifier`
- `others_identifier`
- LLM `deduplicator`

In the DFS run, the Deduplicator removed recursion findings, but the outcome was
not affected because the Planner received the complete source code as well as
the identified-item list, and the Refactoring Worker received the complete
source and generated Plan. The Planner rediscovered the recursive structure
from source. This redundancy prevented failure in this case; it is not evidence
that over-removal is safe for every kernel.

## Deduplicator Prompt hardening

The original LLM Deduplicator Agent, call structure, response schema, and
downstream processing remain unchanged. Its Prompt is hardened to use
conservative duplicate removal:

- default to retaining findings;
- remove an item only when a retained item clearly represents the same concrete
  construct, source symbol/location, and non-synthesizable category;
- never collapse related, causal, overlapping, or same-refactoring-strategy
  findings;
- never use an umbrella `other_necessary_items` entry to replace distinct
  discoveries;
- keep both entries whenever identity is uncertain.

This is a low-risk mitigation rather than a formal guarantee. The next real
lightweight rerun must inspect the resulting deduplicated list to verify that
recursion and other distinct findings are retained.

## Deferred optimization backlog

These are observations only and are not part of the current correction:

- Add an optional compact single-Agent identification profile while preserving
  the specialized multi-Agent profile.
- Evaluate a conservative deterministic deduplication mode that removes only
  exact or provenance-equivalent duplicates.
- Consider deterministic construction of the Public Testbench-aligned
  instruction.
- Consider deterministic construction of ABI-only empty Candidate stubs.
- Keep the Public linkage Prompt unchanged until memory and Prompt-only control
  experiments can distinguish their effects.
- Revisit semantic qualification Stub generation only after broader real-run
  evidence shows a correctness, portability, or budget problem.
- Add per-provider-call token deltas so short outputs can be separated from
  large accumulated input histories.

## Acceptance consequence

The first lightweight run remains useful audit evidence but is not the final
Step F1 acceptance result. Lightweight must be rerun after this correction.
The rerun must show:

- accepted CSYNTH, Public, and Hidden validation;
- held-out Testbench calls both Original and Candidate;
- held-out Original gcov `lines_hit > 0`;
- no Hidden-to-Public/Candidate reverse data flow;
- Prompt Identity call count equals budget LLM-call usage;
- repository commit identity and clean status are complete.
