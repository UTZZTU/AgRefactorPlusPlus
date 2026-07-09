# 环境与复现说明

本文件记录 AgRefactor++ 当前已验证环境、推荐环境和依赖导出方式。  
不同机器的安装路径可能不同，因此 README 中不会写死 `/data/...` 这类本机路径。

---

## 当前已验证环境

| 项目 | 内容 |
|---|---|
| 操作系统 | Ubuntu 22.04 LTS |
| Python | 3.10 |
| HLS 工具 | Vitis HLS 2023.2 |
| 已测试模型后端 | DeepSeek V4 Flash |
| DeepSeek Base URL | `https://api.deepseek.com` |
| 最小测试样例 | `src/heterorefactor/dfs/kernel.cpp` |
| 测试 kernel | `process_top` -> `process_top_hls` |

---

## Python 环境说明

推荐使用 Conda 创建独立环境：

```bash
conda create -n agrefactor python=3.10
conda activate agrefactor
```

然后根据仓库中的依赖文件安装：

```bash
if [ -f requirements.txt ]; then
  pip install -r requirements.txt
fi

pip install -e .
```

为了便于复现，本仓库可以保存当前环境导出文件：

```text
docs/environment/conda-env.yml
docs/environment/pip-freeze.txt
```

这些文件用于说明当前实验环境中安装了哪些包。  
如果你的环境不同，应优先保证核心依赖与 AG2/AutoGen、Vitis HLS、Python 版本兼容。

---

## Vitis HLS 环境

需要先安装 Vitis HLS，并加载对应版本的 `settings64.sh`：

```bash
export VITIS_HLS_SETTINGS=/your/path/to/Xilinx/Vitis_HLS/2023.2/settings64.sh
source "$VITIS_HLS_SETTINGS"
```

检查：

```bash
which vitis_hls
vitis_hls -version
```

---

## API 配置

`.env` 示例：

```bash
OPENAI_API_KEY=sk-your-key-here
OPENAI_BASE_URL=https://api.deepseek.com
RUN_DIR=/your/path/to/agrefactor_runs
WORK_DIR=/your/path/to/agrefactor_work
```

说明：

- `.env` 不应提交到 Git。
- `RUN_DIR` 用于保存每次运行的日志、上下文和 HLS 工程输出。
- `WORK_DIR` 用于保存中间工作文件。
- 使用 OpenAI 官方 API 时，可按实际情况调整或移除 `OPENAI_BASE_URL`。
- 使用其他 OpenAI-compatible API 时，替换为对应服务商的 base URL。

---

## 最小验证命令

```bash
cd /your/path/to/AgRefactorPlusPlus

conda activate agrefactor
source "$VITIS_HLS_SETTINGS"

python -m flow.new \
  --kernel_path src/heterorefactor/dfs/kernel.cpp \
  --kernel_name process_top \
  --model deepseek-v4-flash \
  --reasoning_effort low \
  --base_url https://api.deepseek.com \
  --debug
```

成功时，`output.txt` 末尾应出现：

```text
HLS refactoring with RAG completed successfully.
```
