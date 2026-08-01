# AgRefactor++

[English](README.md) | [简体中文](README.zh-CN.md)

**A target-conditioned, model-pluggable, evidence-driven, and budget-constrained agent for HLS repair, optimization, and migration.**

AgRefactor++ turns ordinary C/C++ or an existing HLS design into a correct,
synthesizable, and traceable implementation for a user-specified Vitis HLS
target. It uses real compile, simulation, synthesis, timing, and resource
evidence to decide what the system may do next, and it preserves the best
correct design when exploration stops.

The project is designed as a general research and engineering system. Its core
interfaces and workflows are not tied to one benchmark, competition, model, or
Vitis release.

## Two task modes

### Mode A: ordinary C/C++ to target HLS

```text
ordinary or non-synthesizable C/C++
+ TargetProfile
→ functionally correct, synthesizable, and optimized target HLS
```

The current normal product entrypoint implements the source-only refactoring
foundation for this mode.

### Mode B: existing HLS to a target version

```text
existing HLS
+ optional SourceProfile
+ TargetProfile
→ repaired, validated, optimized, and PPA-compared target-version HLS
```

Version-aware migration is a non-removable project goal. The migration runtime
is planned for a later stage; a source version is optional and is not required
to be detected automatically.

## Eight core capabilities

These capabilities define the long-term product contract. They must not be
replaced by benchmark-specific shortcuts or metadata-only implementations.

| # | Capability | What it must provide |
|---:|---|---|
| 1 | **TargetProfile** | Vitis version, settings, executable, part, platform, clock, resource constraints, compile flags, Tcl generation, and report parsing must control real execution. |
| 2 | **Dual-mode target-version processing** | Support both ordinary C/C++ → target HLS and existing HLS → target-version migration. `SourceProfile` remains optional. |
| 3 | **Model API Registry** | Provider-neutral model selection, user-authorized models, environment-only credentials, and a fixed-model default policy. |
| 4 | **Layered Prompt adaptation** | Compose the task contract, current stage, target profile, model-family adaptation, current evidence, gated memory, and output contract. |
| 5 | **Structured feedback and evidence state machine** | Compile, Public tests, CSIM, CSYNTH, timing, resources, and tool errors determine legal next actions. |
| 6 | **Hypothesis-driven three-level safe optimizer** | Explore Structural → Bottleneck → Pragma hypotheses with cheap gates, checkpoints, rollback, cache, candidate lineage, and `best_correct`. |
| 7 | **Memory Applicability Gate** | Support `off`, `gated`, and `always`; score applicability, retain positive and negative experience, explain rejection, and abstain when memory is unsafe or irrelevant. |
| 8 | **BudgetManager** | Track and constrain LLM, token, cost, compile, test, CSIM, CSYNTH, cosim, and wall time; return `best_correct` when exploration must stop. |

## What is available now

The current normal command is:

```bash
python -m agrefactor.cli refactor \
  SOURCE.cpp \
  --top TOP_FUNCTION \
  --model MODEL_ID
```

The present implementation includes the source-only refactoring path,
TargetProfile-driven Vitis execution, fixed model profiles, layered prompts,
independent Public/Hidden validation, bounded Testbench and Candidate repair,
structured terminal results, Execution Identity, artifact manifests, and shared
budget accounting.

The safe-v1 three-level optimizer and its `optimize/full` product adapters are
available. Memory Applicability Gate and version-migration runtime remain
separate later roadmap stages.

For the exact implementation and validation boundary, see:

- [Current project state](docs/roadmap/PROJECT_STATE.md)
- [Goal traceability](docs/roadmap/GOAL_TRACEABILITY.md)
- [Reproduction and validation status](docs/guides/REPRODUCTION_STATUS.md)

## How the current refactoring path works

```text
source + explicit top function + TargetProfile + selected model
→ generate and qualify Public tests
→ generate Candidate
→ generate and isolate Hidden tests
→ preflight and bounded repair
→ real Vitis HLS CSYNTH
→ Public and Hidden CSIM
→ accepted, structured rejected, or infrastructure error
```

Correctness comes first. A Candidate that fails compile, Public validation, or
CSIM cannot be accepted because another metric looks better. Hidden Testbench
source is not exposed to generation or repair models.

## Install

### Prerequisites

- Python 3.10
- Vitis HLS 2023.2 for the currently validated environment
- An OpenAI-compatible model endpoint
- Local directories for persistent artifacts and temporary HLS work

### Setup

```bash
git clone https://github.com/UTZZTU/AgRefactorPlusPlus.git
cd AgRefactorPlusPlus

conda create -n agrefactor python=3.10 -y
conda activate agrefactor
pip install -r requirements.txt
cp .env.example .env
```

Configure credentials and paths in the local `.env` file. Do not commit real
API keys or private paths.

Example:

```bash
DEEPSEEK_API_KEY=your-api-key
RUN_DIR=/absolute/path/to/agrefactor_runs
WORK_DIR=/absolute/path/to/agrefactor_work
```

Load Vitis before running:

```bash
source /path/to/Xilinx/Vitis/2023.2/settings64.sh
export AGREFACTOR_VITIS_RUN=/path/to/Xilinx/Vitis/2023.2/bin/vitis-run
```

## Quick start

### Lightweight test generation

```bash
python -m agrefactor.cli refactor \
  src/heterorefactor/dfs/kernel.cpp \
  --top process_top \
  --model deepseek-v4-flash \
  --test-generation-profile lightweight \
  --public-tests auto \
  --hidden-tests auto
```

### Coverage-enhanced test generation

```bash
python -m agrefactor.cli refactor \
  src/heterorefactor/ahocorasick/kernel.cpp \
  --top process_top \
  --model deepseek-v4-flash \
  --test-generation-profile coverage-enhanced \
  --public-coverage-rounds 2 \
  --hidden-coverage-rounds 3 \
  --public-generation-trajectories 2 \
  --hidden-generation-trajectories 2 \
  --public-tests auto \
  --hidden-tests auto
```

### Direct safe optimization

```bash
python -m agrefactor.cli optimize candidate.cpp \
  --top candidate_top \
  --reference-source original.cpp \
  --reference-top original_top \
  --public-test public_tb.cpp \
  --hidden-test hidden_tb.cpp \
  --model deepseek-v4-flash \
  --optimizer-profile safe-v1 \
  --optimization-objective latency
```

Direct optimize requires an independent reference and provided Public/Hidden
suites. `full` runs the accepted refactor pipeline first, then optimizes its
accepted candidate with the exact qualified suites:

Model analysis or rewrite output that does not satisfy its typed contract is a
controlled, no-retry abstention for that level: the optimizer preserves
`best_correct`, records safe reason codes, and continues when possible. Network,
credential, filesystem, toolchain, and qualification infrastructure failures
remain hard errors. Contract validation never certifies source applicability or
PPA; real compile, Public/Hidden CSIM, CSYNTH, and typed PPA remain authoritative.

```bash
python -m agrefactor.cli full kernel.cpp \
  --top kernel_top \
  --model deepseek-v4-flash \
  --public-tests auto \
  --hidden-tests auto
```

Persist one run at an exact location with `--output-dir`. View the complete
contract with:

```bash
python -m agrefactor.cli refactor --help
```

See the [CLI parameter reference](docs/guides/CLI_PARAMETER_REFERENCE.md) for
all options and safety ceilings.

## Results and artifacts

A persistent run records the information needed to understand and reproduce its
verdict, including:

- the effective model, target, test-source, and budget contracts;
- generated and final Candidate artifacts;
- Public/Hidden provenance without exposing Hidden source to the model;
- compile, CSIM, CSYNTH, timing, and resource evidence when available;
- structured traces, prompt identities, budget usage, and Execution Identity;
- the reason a Candidate was accepted, rejected, repaired, or rolled back.

Important files include `full_result.json`, `execution_identity.json`, and
`run_artifact_manifest.json`.

Provider-reported token usage is accumulated when available. Missing usage is
reported as unavailable rather than fabricated. Monetary estimates are emitted
only when a matching pricing snapshot exists.

## Repository layout

| Path | Purpose |
|---|---|
| `agrefactor/` | Product CLI, contracts, orchestration, validation, budgets, model and target profiles, and artifacts |
| `flow/` | Generation agents, test generation, compatibility bridge, and supporting tools |
| `src/` | Example and evaluation C/C++ programs |
| `tests/` | Deterministic contract, integration, and regression tests |
| `docs/roadmap/` | Mission, eight core capabilities, stages, and frozen implementation contracts |
| `docs/guides/` | Usage, CLI, reproduction, and operator guidance |
| `docs/acceptance/` | Package-level and real-execution acceptance evidence |

## Roadmap

The project proceeds in small, independently validated stages:

1. shared target, model, prompt, feedback, validation, and budget foundations;
2. trustworthy source-only refactoring;
3. hypothesis-driven safe optimization;
4. applicability-gated self-evolving memory;
5. target-version-aware HLS migration;
6. fixed-protocol multi-kernel evaluation and ablation.

The authoritative roadmap is [docs/roadmap/ROADMAP.md](docs/roadmap/ROADMAP.md).

## Relationship to AgRefactor

AgRefactor++ is an independently evolving extension of the original
[AgRefactor](https://github.com/Williamzou0123/AgRefactor) research codebase.
It preserves upstream attribution while developing a more explicit,
product-oriented execution and validation architecture.

Use, citation, and redistribution must respect the original project, its paper,
its authors, and all applicable licensing terms.
