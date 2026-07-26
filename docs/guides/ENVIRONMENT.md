
# 环境与复现说明

## 已验证基础环境

| 项目 | 内容 |
|---|---|
| 操作系统 | Ubuntu 22.04 LTS |
| Python | 3.10 |
| HLS 工具 | Vitis HLS 2023.2 |
| 普通 CLI | `python -m agrefactor.cli refactor` |
| 已完成真实网络 smoke 的具体模型 | `deepseek-v4-flash` |
| API 类型 | OpenAI-compatible |

具体真实验收范围以 acceptance 文档为准，不把单个模型/内核的验收扩大为整个模型家族或全部 kernel 的保证。

## Python 环境

```bash
conda create -n agrefactor python=3.10
conda activate agrefactor
pip install -r requirements.txt
```

仓库从根目录直接运行，不要求 `pip install -e .`。

## API 配置

```bash
cp .env.example .env
```

使用 `deepseek-v4-flash`：

```bash
DEEPSEEK_API_KEY=your-deepseek-api-key
RUN_DIR=/your/path/to/agrefactor_runs
WORK_DIR=/your/path/to/agrefactor_work
```

其他没有具体静态运行记录的 OpenAI-compatible 模型默认读取：

```bash
OPENAI_API_KEY=your-compatible-api-key
```

也可显式指定：

```bash
--base-url https://provider.example/v1
--api-key-env PROVIDER_API_KEY
```

`.env` 已被 Git 忽略。不要提交真实凭证。

## Vitis HLS 2023.2

```bash
source /your/path/to/Xilinx/Vitis/2023.2/settings64.sh
which vitis-run
vitis-run --version
```

多版本机器建议显式设置：

```bash
export AGREFACTOR_VITIS_RUN=/your/path/to/Xilinx/Vitis/2023.2/bin/vitis-run
export AGREFACTOR_VITIS_SETTINGS=/your/path/to/Xilinx/Vitis/2023.2/settings64.sh
```

系统会检查 requested/actual toolchain version；不一致时在 CSYNTH 前阻断。

## 最小复现

```bash
python -m agrefactor.cli refactor   src/heterorefactor/dfs/kernel.cpp   --top process_top   --model deepseek-v4-flash   --public-tests auto   --hidden-tests auto
```

默认模型请求 timeout 为 240 秒；默认单次 CSIM/CSYNTH timeout 分别为 120/600 秒。完整参数见 [CLI 参数参考](CLI_PARAMETER_REFERENCE.md)。

## 输出目录

默认持久与临时根目录来自：

```text
RUN_DIR / AGREFACTOR_RUN_ROOT
WORK_DIR / AGREFACTOR_WORK_ROOT
```

单次运行可以通过：

```bash
--output-dir /absolute/path/to/empty_artifact_directory
```

指定精确持久 artifact 目录。临时工具工作仍保留在 work root。
