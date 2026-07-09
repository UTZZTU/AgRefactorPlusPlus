# AgRefactor++

AgRefactor++ 是一个面向 **Vitis HLS** 的版本感知型 **LLM4HLS 智能体**，目标是自动完成 HLS 代码重构、修复与跨版本迁移。

相比传统 HLS 重构流程，AgRefactor++ 不只关注把普通 C/C++ 程序转换为可综合 HLS 代码，还进一步引入目标 Vitis HLS 版本约束，使系统能够根据用户指定的工具链版本生成更合适的 HLS 工程。项目当前已完成基础流程复现，并适配 DeepSeek API 作为大模型后端。后续将围绕 Vitis HLS 多版本知识库、编译/综合反馈驱动修复、可复用 AST/Clang 迁移规则以及跨版本 HLS 迁移测试集继续建设。

在竞赛和实验阶段，AgRefactor++ 将优先支持若干固定 Vitis HLS 版本之间的迁移与适配，并逐步扩展到更复杂的版本迁移和平台迁移场景。

---

## 项目能做什么

- **HLS 兼容性重构**：识别递归、动态内存、全局状态、不可综合控制流等问题，并重构为更适合 HLS 的代码。
- **LLM 智能体流程**：通过测试生成、不可综合结构识别、重构规划、代码生成、综合反馈修复等阶段完成自动化重构。
- **Vitis HLS 工具链闭环**：调用 Vitis HLS 进行编译、仿真与综合，并利用工具反馈继续修复代码。
- **多模型后端接入**：支持 OpenAI-compatible API，并已适配 DeepSeek V4 Flash 作为可用的大模型后端。
- **版本感知迁移方向**：后续将引入目标 Vitis HLS 版本知识，使重构结果更贴合指定版本的工具链行为。

---

## 当前状态

当前仓库已经完成：

- 基础 `flow.new` 单 kernel 重构流程复现。
- Vitis HLS 2023.2 环境下的最小样例验证。
- DeepSeek V4 Flash 后端的端到端运行验证。
- AG2/AutoGen 新版本下的 LLM 配置兼容修复。
- DeepSeek 结构化输出、thinking mode token 预算、模型价格元数据等兼容修复。
- identifier 阶段 JSON 输出格式的鲁棒解析。

详细变更记录见：

```text
docs/CHANGELOG.md
```

环境与依赖说明见：

```text
docs/ENVIRONMENT.md
```

---

## 快速开始

下面的命令使用占位路径，请根据自己的机器修改。

### 1. 克隆项目

```bash
git clone git@github.com:UTZZTU/AgRefactorPlusPlus.git
cd AgRefactorPlusPlus
```

如果你使用 HTTPS：

```bash
git clone https://github.com/UTZZTU/AgRefactorPlusPlus.git
cd AgRefactorPlusPlus
```

### 2. 准备 Python 环境

推荐使用 Python 3.10。

```bash
conda create -n agrefactor python=3.10
conda activate agrefactor
```

根据仓库中的实际依赖文件安装依赖：

```bash
if [ -f requirements.txt ]; then
  pip install -r requirements.txt
fi

pip install -e .
```

如果你想复现当前已验证环境，可以参考：

```text
docs/ENVIRONMENT.md
docs/environment/conda-env.yml
docs/environment/pip-freeze.txt
```

### 3. 配置 Vitis HLS

请先安装 Vitis HLS。当前已验证版本是 Vitis HLS 2023.2。

不要直接照抄 `/data/...` 这类本机路径，请改成你自己的安装路径。例如：

```bash
export VITIS_HLS_SETTINGS=/your/path/to/Xilinx/Vitis_HLS/2023.2/settings64.sh
source "$VITIS_HLS_SETTINGS"
```

确认 `vitis_hls` 可用：

```bash
which vitis_hls
vitis_hls -version
```

### 4. 配置大模型 API

创建 `.env`：

```bash
cp .env.example .env
```

使用 DeepSeek V4 Flash 时，可写成：

```bash
OPENAI_API_KEY=sk-your-key-here
OPENAI_BASE_URL=https://api.deepseek.com
RUN_DIR=/your/path/to/agrefactor_runs
WORK_DIR=/your/path/to/agrefactor_work
```

说明：

- `OPENAI_API_KEY` 填你的模型服务 API key。
- `OPENAI_BASE_URL` 用于接入 OpenAI-compatible API，例如 DeepSeek。
- 如果使用 OpenAI 官方 API，可以按实际情况移除或修改 `OPENAI_BASE_URL`。
- `RUN_DIR` 和 `WORK_DIR` 请设置为你有读写权限的目录。

### 5. 运行最小 demo

进入项目目录并激活环境：

```bash
cd /your/path/to/AgRefactorPlusPlus
conda activate agrefactor
source "$VITIS_HLS_SETTINGS"
```

运行 DFS 示例：

```bash
python -m flow.new \
  --kernel_path src/heterorefactor/dfs/kernel.cpp \
  --kernel_name process_top \
  --model deepseek-v4-flash \
  --reasoning_effort low \
  --base_url https://api.deepseek.com \
  --debug
```

查看最新输出：

```bash
latest=$(ls -td "$RUN_DIR"/$(date +%Y%m%d)/* | head -n 1)
echo "$latest"
tail -n 120 "$latest/output.txt"
```

如果成功，日志末尾会出现类似信息：

```text
HLS refactoring with RAG completed successfully.
```

说明：这里的 `with RAG` 是原 AgRefactor 流程中的固定输出文本，不代表你一定启用了 RAG。

---

## 支持的大模型后端

当前代码层面支持以下类型的大模型后端：

- **DeepSeek V4 Flash**：已完成最小 demo 端到端测试。
- **DeepSeek V4 Pro**：已完成配置层适配，完整流程需要进一步测试。
- **OpenAI-compatible API**：可通过 `OPENAI_BASE_URL` 接入。
- **OpenAI 官方 API**：保留 OpenAI-compatible 配置路径。
- **Gemini**：保留原有 Gemini 配置路径。

---

## 常用命令

运行单 kernel 重构：

```bash
python -m flow.new \
  --kernel_path <path/to/kernel.cpp> \
  --kernel_name <top_function_name> \
  --model <model_name> \
  --base_url <openai_compatible_base_url> \
  --debug
```

查看最近一次运行结果：

```bash
latest=$(ls -td "$RUN_DIR"/$(date +%Y%m%d)/* | head -n 1)
echo "$latest"
tail -n 120 "$latest/output.txt"
```

常见输出文件包括：

```text
output.txt
context_final.json
csim_*/refactor_code.cpp
csim_*/testbench.cpp
csynth_*/process_top_hls.cpp
csynth_*/csynth/solution/syn/report/*_csynth.rpt
```

---

## 文档结构

```text
README.md                 项目入口与快速上手
docs/CHANGELOG.md         变更记录
docs/ENVIRONMENT.md       环境、依赖与复现说明
docs/environment/         当前验证环境导出文件
```

---

## 项目来源与致谢

AgRefactor++ 基于原始 AgRefactor 项目修改而来，保留并继承其 HLS 自动重构智能体流程。  
使用、引用或分发本仓库时，请同时尊重原项目的论文引用、license 与作者贡献。

原项目：

```text
https://github.com/Williamzou0123/AgRefactor
```
