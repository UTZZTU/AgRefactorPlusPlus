# Stage 3.8 Multi-Kernel Evaluation Decision Record

## Decision

S3.8 evaluates the already accepted Stage 3 product paths. It does not create a
new optimizer and does not change the frozen `safe-v1` policy. The bounded
acceptance matrix is:

```text
3 distinct committed kernels
× 2 independent repeats
× 3 arms
= 18 real experiment units
```

The default kernels are `array-map`, `reduction`, and `nested-stencil`. They
exercise distinct kernel categories from the committed Stage 2 smoke corpus.
This is a bounded stage-acceptance corpus, not a broad external benchmark.

## Evaluation arms

### Safe direct optimize

Runs the normal product command with an existing HLS baseline, an independent
reference source, and distinct persisted Public and Hidden suites.

### Live source-only full

Runs the normal `full` command from the original source. The accepted refactor
candidate must be handed to the normal optimizer through the typed
`AcceptedOptimizationMaterial` contract. This is the live source-only full path
that S3.7 deliberately did not claim as a separate benchmark.

### Legacy simple_iter

Runs `opt.simple_iter` only as a comparison baseline. Its provided Public
feedback testbench is not correctness authority. Before each Legacy run, the
input baseline is independently qualified. The selected Legacy candidate is
then independently qualified with the same reference, Public suite, Hidden
suite, TargetProfile, toolchain observation, validation order, and typed PPA
adapter used by Stage 3.

## Fairness contract

Every arm in one protocol uses the same:

- concrete model and OpenAI-compatible endpoint;
- effective provider reasoning and output-token parameters;
- TargetProfile and observed Vitis toolchain;
- kernel-specific Public and Hidden suite identities;
- repeat count;
- per-run hard ceilings.

The acceptance ceilings are:

```text
LLM calls=14
Tool calls=128
Compile calls=48
CSIM calls=32
CSYNTH calls=16
Wall time=7200 seconds
CSIM timeout=180 seconds
CSYNTH timeout=900 seconds
```

Legacy uses at most 14 iterations. Its worst-case internal launches plus the
independent baseline/final qualifications remain inside the same physical
ceilings. A single wall-time deadline covers all three Legacy portions.
Automatic model retry is disabled for every arm.

## Authority and privacy

- compile, Public/Hidden CSIM, CSYNTH, and typed PPA remain authoritative;
- model output and Legacy internal testbench feedback are non-authoritative;
- Hidden evidence is never exposed to a model;
- S3.8 Legacy evaluation logs persist hashes and lengths, not raw model prompts,
  raw model responses, or reasoning;
- candidate code and operator tool artifacts remain available for audit;
- candidate failures are evaluation data, while transport, credential,
  filesystem, missing runtime, and toolchain failures are infrastructure errors.

## Metrics

The canonical report records per arm and per kernel:

- correctness/qualification success rate;
- latency and latency-improvement distributions;
- initiation interval and resource-utilization distributions;
- invalid candidate ratio;
- rollback observations and `best_correct` protection;
- LLM/tool/compile/CSIM/CSYNTH calls;
- wall time;
- infrastructure and candidate failures.

Two repeats are sufficient for this bounded stage gate but not for statistical
significance. S3.8 never claims stable superiority over `simple_iter`.

## Retention gate

The package is retained only when:

1. all 18 planned units have one contract-valid record;
2. no infrastructure failure occurred;
3. at least one direct `optimize` run is accepted;
4. at least one live source-only `full` run is accepted;
5. real CSYNTH is observed for all three kernels;
6. record identities, no-retry, Hidden, and raw-model-content boundaries pass;
7. all six Legacy units prove baseline qualification, actual Legacy execution,
   a safe evaluation artifact, and at least one physical model call.

A candidate failure does not by itself invalidate the evaluation. Missing or
corrupt records, unsafe evidence, an infrastructure failure, or failure to
exercise both product paths prevents S3.8 closure. Partial artifacts are kept
outside the repository and may be resumed under the same protocol identity.
Accepted and candidate-failure records remain frozen evaluation observations;
infrastructure-failure records and interrupted directories without a terminal
record are archived under `failed_attempts/` and rerun on resume.

## Claim boundary

Acceptance closes the bounded Stage 3 implementation/evaluation route for the
pinned model, TargetProfile, toolchain, corpus, budgets, and repeat count. It
does not prove arbitrary-kernel generalization, cross-version portability,
publication-grade significance, or stable PPA superiority.

## V2 evidence-triggered correction

The first V1 target-host matrix revealed an observer defect rather than a
Legacy candidate outcome. `qualify_external_candidate` emitted the accepted
Stage 3 order `source, preflight, public, csynth, hidden, ppa, feasibility`, but
the S3.8 observer compared it with a truncated four-stage tuple. All six Legacy
units therefore stopped before the Legacy process and recorded zero model/tool
calls. Treating those RuntimeErrors as candidate failures made the original
retention predicate incomplete.

V2 keeps the protocol identity unchanged so the twelve real product records
remain immutable. It separates protocol, run-record, and report schema
versions; supports product run-record v1 plus corrected Legacy run-record v2;
adds targeted `--resume --retry-arm simple-iter`; and makes actual Legacy
execution a mandatory report and stage-acceptance predicate. Observer and
record-contract defects are infrastructure failures.
