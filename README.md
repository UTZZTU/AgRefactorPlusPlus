# AgRefactor

<!-- AGREFACTORPP_OVERVIEW_START -->

## AgRefactor++ v0.1

**AgRefactor++** 是基于原始 AgRefactor 项目的兼容性增强版本。  
本项目保留 AgRefactor 原有的自动化 HLS 重构流程，同时针对新版 AG2/AutoGen 以及 DeepSeek V4 等 OpenAI-compatible 大模型接口做了实际适配。

当前 v0.1 版本的重点是：让 AgRefactor 的基础单 kernel 重构流程可以在 Vitis HLS 2023.2 + DeepSeek V4 Flash 环境下稳定复现。

### AgRefactor++ 新增内容

AgRefactor++ v0.1 主要加入了以下改动：

- 兼容 AG2/AutoGen 0.11.x 中 `LLMConfig(config_dict)` 的初始化方式。
- 保留 OpenAI-compatible provider 的配置路径。
- 保留 Gemini 的 `api_type="google"` 配置路径。
- 支持通过 `OPENAI_BASE_URL` 接入 DeepSeek 等 OpenAI-compatible API。
- 针对 DeepSeek 修复 Python/Pydantic `response_format` 不兼容问题，将结构化输出转换为 JSON mode。
- 针对 DeepSeek V4 thinking mode 增大默认 `max_tokens`，避免模型只输出 reasoning 内容而没有最终 `content`。
- 为 DeepSeek V4 Flash / Pro 添加默认价格元数据，避免 AG2 输出 unknown-model cost warning。
- 增强 identifier 阶段的 JSON 解析鲁棒性，同时兼容：
  - `{"identified_items": [...]}`
  - `[...]`

### 已验证环境

当前 v0.1 版本已经在以下环境中完成最小 demo 验证：

| 组件 | 版本 / 设置 |
|---|---|
| 操作系统 | Ubuntu 22.04 LTS |
| Python | 3.10 |
| Conda 环境 | `agrefactor` |
| HLS 工具 | Vitis HLS 2023.2 |
| 已端到端测试的大模型 | DeepSeek V4 Flash |
| DeepSeek Base URL | `https://api.deepseek.com` |
| 最小测试样例 | `src/heterorefactor/dfs/kernel.cpp` |
| 测试 kernel | `process_top` -> `process_top_hls` |

最小 demo 成功时，`output.txt` 末尾应出现：

```text
HLS refactoring with RAG completed successfully.
```

> 说明：本次最小验证未开启 RAG、HeteroRefactor、batch mode 和 optimization。日志中的 `with RAG` 是原 AgRefactor 运行流程中的固定输出信息。

### 使用 DeepSeek V4 Flash 快速运行

创建或修改 `.env`：

```bash
OPENAI_API_KEY=sk-your-key-here
OPENAI_BASE_URL=https://api.deepseek.com
RUN_DIR=/data/agrefactor_runs
WORK_DIR=/data/agrefactor_work
```

加载 Vitis HLS 环境并激活 Python 环境：

```bash
source /data/agrefactor_vitis_env.sh

eval "$(conda shell.bash hook)"
conda activate agrefactor

cd /data/AgRefactor
```

运行最小 demo：

```bash
python -m flow.new \
  --kernel_path src/heterorefactor/dfs/kernel.cpp \
  --kernel_name process_top \
  --model deepseek-v4-flash \
  --reasoning_effort low \
  --base_url https://api.deepseek.com \
  --debug
```

查看最新运行日志：

```bash
latest=$(ls -td /data/agrefactor_runs/$(date +%Y%m%d)/* | head -n 1)
echo "$latest"
tail -n 120 "$latest/output.txt"
```

### Provider 兼容状态

| Provider / 模型族 | 当前状态 | 说明 |
|---|---|---|
| DeepSeek V4 Flash | 已端到端测试 | 最小单 kernel demo 已在 Vitis HLS 2023.2 下通过。 |
| DeepSeek V4 Pro | 配置层兼容 | LLM config 路径已支持，完整 HLS 流程需要后续单独测试。 |
| OpenAI-compatible API | 配置层兼容 | 使用 `api_type="openai"`，可通过 `base_url` 接入兼容接口。 |
| OpenAI 官方 API | 配置层兼容 | 需要有效 OpenAI API key 和对应模型名后再做完整测试。 |
| Gemini | 配置层兼容 | 保留原有 `api_type="google"` 路径。 |

### v0.1 已知限制

- 当前验证结果是最小单 kernel demo，不代表已经完整复现论文中的所有实验。
- RAG、HeteroRefactor、批量实验和优化流程还需要后续分别验证。
- 当前已经消除了 AG2 的 unknown-model cost warning，但还没有在每次运行结束时打印总 token 和总费用统计。
- DeepSeek 价格可能随官方调整而变化；如果需要精确费用统计，应在 config 中显式覆盖 `price` 字段。
- DeepSeek V4 Flash 已完成端到端测试；OpenAI/Gemini 目前主要是配置层兼容，仍需使用对应 API key 做完整运行验证。

### 项目来源与致谢

AgRefactor++ 基于原始 AgRefactor 项目修改而来。  
使用、引用或分发本仓库时，请保留原项目说明、论文引用和 license 信息。

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
