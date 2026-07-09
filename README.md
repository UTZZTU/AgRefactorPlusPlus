# AgRefactor

<!-- AGREFACTORPP_OVERVIEW_START -->

## AgRefactor++ v0.1

**AgRefactor++** is a provider-compatibility upgrade of the original AgRefactor project.  
It keeps the original AgRefactor workflow while adding practical compatibility fixes for newer AG2/AutoGen versions and OpenAI-compatible LLM providers such as DeepSeek V4.

This repository currently focuses on making the basic AgRefactor single-kernel refactoring flow reproducible with Vitis HLS and DeepSeek V4 Flash.

### What is new in AgRefactor++

AgRefactor++ v0.1 adds:

- Compatibility with AG2/AutoGen 0.11.x `LLMConfig(config_dict)` initialization.
- Provider-neutral LLM configuration construction.
- Preserved OpenAI-compatible and Gemini configuration paths.
- DeepSeek/OpenAI-compatible endpoint support through `OPENAI_BASE_URL`.
- DeepSeek structured-output compatibility by converting Python/Pydantic `response_format` objects to JSON mode.
- Larger default `max_tokens` for DeepSeek V4 thinking-mode responses.
- DeepSeek price metadata to avoid AG2 unknown-model cost warnings.
- More robust identifier parsing that accepts both:
  - `{"identified_items": [...]}`
  - `[...]`

### Tested environment

The current v0.1 validation was performed with:

| Component | Version / Setting |
|---|---|
| OS | Ubuntu 22.04 LTS |
| Python | 3.10 |
| Conda env | `agrefactor` |
| Vitis HLS | 2023.2 |
| LLM provider tested end-to-end | DeepSeek V4 Flash |
| DeepSeek base URL | `https://api.deepseek.com` |
| Minimal benchmark tested | `src/heterorefactor/dfs/kernel.cpp` |
| Kernel tested | `process_top` -> `process_top_hls` |

Expected success message for the validated minimal demo:

```text
HLS refactoring with RAG completed successfully.
```

> Note: In this minimal validation, RAG, HeteroRefactor, batch mode, and optimization were not enabled. The success message is the original AgRefactor runtime message.

### Quick start with DeepSeek V4 Flash

Create or edit `.env`:

```bash
OPENAI_API_KEY=sk-your-key-here
OPENAI_BASE_URL=https://api.deepseek.com
RUN_DIR=/data/agrefactor_runs
WORK_DIR=/data/agrefactor_work
```

Load Vitis HLS and activate the Python environment:

```bash
source /data/agrefactor_vitis_env.sh

eval "$(conda shell.bash hook)"
conda activate agrefactor

cd /data/AgRefactor
```

Run the minimal demo:

```bash
python -m flow.new \
  --kernel_path src/heterorefactor/dfs/kernel.cpp \
  --kernel_name process_top \
  --model deepseek-v4-flash \
  --reasoning_effort low \
  --base_url https://api.deepseek.com \
  --debug
```

Check the latest output:

```bash
latest=$(ls -td /data/agrefactor_runs/$(date +%Y%m%d)/* | head -n 1)
echo "$latest"
tail -n 120 "$latest/output.txt"
```

### Provider compatibility status

| Provider / Model family | Status | Notes |
|---|---|---|
| DeepSeek V4 Flash | Tested end-to-end | Minimal single-kernel demo passed with Vitis HLS 2023.2. |
| DeepSeek V4 Pro | Config-compatible | LLM config path is supported; full HLS run should be tested separately. |
| OpenAI-compatible APIs | Config-compatible | Uses `api_type="openai"` and optional `base_url`. |
| OpenAI official API | Config-compatible | Requires a valid OpenAI API key and model selection. |
| Gemini | Config-compatible | Existing `api_type="google"` path is preserved. |

### Known limitations in v0.1

- The validated result is a minimal single-kernel run, not a full reproduction of every paper experiment.
- RAG, HeteroRefactor, batch experiments, and optimization flows still need separate validation.
- Price metadata removes AG2's unknown-model cost warning, but AgRefactor++ does not yet print a full total-cost summary at the end of each run.
- Provider prices may change; override the `price` field in config when exact cost tracking matters.
- DeepSeek V4 Flash has been tested end-to-end; OpenAI/Gemini compatibility is currently verified at the configuration level unless separately tested.

### Attribution

AgRefactor++ is derived from the original AgRefactor project.  
Please keep the original project attribution, paper citation, and license information when using or redistributing this repository.

<!-- AGREFACTORPP_OVERVIEW_END -->


**A self-evolving agentic workflow for HLS compatibility and performance.**

AgRefactor takes a C/C++ program and a user-specified top-level function and
automatically produces a synthesizable Vitis HLS implementation, then
(optionally) optimizes it for hardware performance. It combines:

- a **multi-agent refactoring pipeline** (test generation → identification →
  planning → refactoring → synthesis/simulation → analyze-and-fix loop);
- a **self-evolving long-term memory** that accumulates strategic and factual
  knowledge across tasks and retrieves it for unseen programs;
- **tool integration** with HeteroRefactor via an LLM *Tool Specialist*; and
- a **performance-optimization agent** that drives Vitis HLS, fast latency
  estimation, and source-to-source transformation tools.

This repository releases the **flow**. Large experimental artifacts (full run
logs, pre-built memory stores, raw coverage data) are not committed; they are
available on request (see [docs/results](docs/results/) for summaries).

---

## Repository layout

| Path | What it is |
|------|------------|
| `flow/` | Refactoring flow: agents (`agents/*.yaml`), tools (`tools/*.py`), long-term memory (`rag/`), and entry points (`new.py`, `parallel_kernel.py`, `parallel_eval.py`). |
| `flow/inflight_tb/` | Optional engineer↔rater testbench loop (paper appendix). |
| `opt/` | Performance-optimization agent (`opt/simple_iter/main.py`). |
| `src/` | Benchmark suite + baselines (`app/`, `heterorefactor/`, `leetcode/`, `hlsrewritter/`, `opt/`). |
| `scripts/` | Reproduction & infrastructure helpers (paper tables, coverage, remote HLS server, vLLM serving). |
| `containers/` | Apptainer recipe + build guide for the HeteroRefactor toolchain. |
| `knowledge_db/` | Memory-store location (stores not committed; see its README). |
| `docs/` | Paper PDF and result summaries (`docs/results/`). |

The flow logic is intentionally unchanged from the research code; only paths
and secrets have been externalized to environment variables.

---

## Prerequisites

- **Python 3.10+**
- **Vitis HLS 2023.2** on `PATH` (`vitis_hls`), with `$XILINX_HLS/include`
  available to `g++` (used for C-simulation). *Licensed; install on the host.*
- **Apptainer** — only for HeteroRefactor tool integration (see
  [containers/README.md](containers/README.md)).
- **CUDA GPU** — recommended (not required) for fast memory embeddings.
- An **OpenAI-compatible LLM API key** (OpenAI, or a Gemini/vLLM endpoint).

## Install

```bash
conda create -n agrefactor python=3.10 -y && conda activate agrefactor
pip install -r requirements.txt
cp .env.example .env        # then edit .env (see below)
```

## Configure

Edit `.env` (loaded automatically). Minimum to run the base flow:

```bash
OPENAI_API_KEY=sk-...
RUN_DIR=/abs/path/to/agrefactor/runs   # where run outputs/logs are written
```

Add `HETEROREFACTOR_DIR` to enable the tool path. See `.env.example` for the
full list.

---

## Quick start

### 1. Refactor one kernel into synthesizable HLS

```bash
python -m flow.new \
    --kernel_path src/heterorefactor/dfs/kernel.cpp \
    --kernel_name process_top \
    --model gpt-5-mini \
    --reasoning_effort low \
    --debug
```

Outputs land in `$RUN_DIR/<...>`: `output.txt` (full log, ends with
`RETRY_COUNT:<n>`), `context_*.json` snapshots per stage, and per-iteration
`csynth_*/` / `csim_*/` artifacts.

### 2. Add long-term memory (the paper's self-evolving RAG)

```bash
python -m flow.new \
    --kernel_path src/app/libjpeg/encode_one_block.cpp \
    --kernel_name encode_one_block \
    --model gpt-5-mini \
    --enable_rag \
    --knowledge_db_path knowledge_db/your_store \
    --debug
```

`--enable_rag` retrieves plans/critiques from the memory store;
`--enable_rag_update` records the current trial back into it. See
[knowledge_db/README.md](knowledge_db/README.md) to build or obtain a store.

### 3. Add the HeteroRefactor tool path (greedy tool + Tool Specialist)

```bash
python -m flow.new \
    --kernel_path src/heterorefactor/dfs/kernel.cpp \
    --kernel_name process_top \
    --model gpt-5-mini \
    --hetero_enabled --debug
```

Requires `HETEROREFACTOR_DIR` set and the container built.

### 4. Optimize a synthesizable design for performance

```bash
python -m opt.simple_iter.main \
    --kernel_path src/opt/prometheus/2mm_extra_large.cpp \
    --top_name top \
    --model gpt-5 \
    --reasoning_effort high
```

### 5. Scale out (many kernels / repeats → pass@K, success rate)

```bash
python -m flow.parallel_kernel \
    --exp_name my_eval \
    --kernels_file flow/test_kernels.json \
    --model gpt-5-mini \
    --enable_rag --repeat 20 --max_workers 20
```

---

## Notes & limitations

- **Vitis HLS is required** for synthesis/simulation; it is host-installed and
  not containerized.
- The repo is **fully self-contained** — no undeclared external packages. The
  testbench coverage / hidden-TB rater is the self-contained AG2 implementation
  in `flow/tools/tb_optimizer.py`.
