# 环境与复现说明

本文档记录 AgRefactor++ 当前已验证环境。不同机器的安装位置不同，因此示例统一使用占位路径，不写死本机 `/data/...` 路径。

## 已验证环境

| 项目 | 内容 |
|---|---|
| 操作系统 | Ubuntu 22.04 LTS |
| Python | 3.10 |
| HLS 工具 | Vitis HLS 2023.2 |
| 已验证模型 | `deepseek-v4-flash`、`deepseek-v4-pro` |
| API 类型 | OpenAI-compatible |
| DeepSeek Base URL | `https://api.deepseek.com` |
| 最小样例 | `src/heterorefactor/dfs/kernel.cpp` |
| 可选容器工具 | Apptainer 1.5.2；主流程不依赖 |

## Python 环境

```bash
conda create -n agrefactor python=3.10
conda activate agrefactor
pip install -r requirements.txt
```

本仓库当前没有 Python package metadata，因此不要求执行 `pip install -e .`。从仓库根目录使用 `python -m ...` 即可。

环境导出文件位于：

```text
docs/environment/conda-env.yml
docs/environment/pip-freeze.txt
```

这些文件用于最大限度复现实验环境，但不保证所有依赖在其他时间或平台上仍能完全一致安装。

## Vitis HLS

```bash
export VITIS_HLS_SETTINGS=/your/path/to/Xilinx/Vitis_HLS/2023.2/settings64.sh
source "$VITIS_HLS_SETTINGS"
```

检查：

```bash
which vitis_hls
vitis_hls -version
```

当前文档中的复现结论以 Vitis HLS 2023.2 为准。其他版本可能出现命令、报告格式、综合结果或支持语法差异。

## API 配置

复制示例文件：

```bash
cp .env.example .env
```

推荐配置：

```bash
OPENAI_API_KEY=sk-your-key-here
OPENAI_BASE_URL=https://api.deepseek.com
RUN_DIR=/your/path/to/agrefactor_runs
WORK_DIR=/your/path/to/agrefactor_work
```

注意：

- `.env` 已被 Git 忽略，不要把真实 API key 提交到仓库。
- `RUN_DIR` 保存日志、上下文、代码和 HLS 工程输出。
- `WORK_DIR` 保存中间文件。
- 使用其他 OpenAI-compatible 服务时，替换 `OPENAI_BASE_URL` 和模型名。
- 模型价格元数据只适合粗略统计，服务商价格变化后应及时更新。

## 环境检查

```bash
python --version
which python
which vitis_hls
vitis_hls -version

test -n "$RUN_DIR" && echo "RUN_DIR=$RUN_DIR"
test -n "$WORK_DIR" && echo "WORK_DIR=$WORK_DIR"
```

验证 DeepSeek API 连通性前，确认 `.env` 已正确加载。`flow.new` 使用 `python-dotenv` 从仓库根目录读取 `.env`。

## 最小复现

```bash
python -m flow.new \
  --kernel_path src/heterorefactor/dfs/kernel.cpp \
  --kernel_name process_top \
  --model deepseek-v4-flash \
  --reasoning_effort low \
  --base_url https://api.deepseek.com \
  --debug
```

成功标志：

```text
HLS refactoring with RAG completed successfully.
```

## 可选依赖

### Apptainer

Apptainer 仅用于外部工具容器和相关实验，不是 `flow.new`、RAG、`flow.parallel_kernel` 或 `opt.simple_iter` 的必需依赖。

### HeteroRefactor

当前不建议配置 `HETEROREFACTOR_DIR`，除非已经自行解决 ROSE/EDG 依赖。主流程默认不传 `--hetero_enabled`。

### Remote HLS / MCP

仓库保留远程 HLS 服务和 MCP 相关脚本，但当前基础复现应优先使用本地 Vitis HLS，减少额外变量。

## 模型使用建议

```text
基础重构和流程调试：DeepSeek V4 Flash
复杂重构和优化实验：DeepSeek V4 Pro
```

推理强度需要结合服务端支持、任务难度、耗时和费用决定。不要把某次实验使用的档位当成所有任务的固定默认值。
