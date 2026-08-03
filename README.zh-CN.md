# AgRefactor++

[English](README.md) | [简体中文](README.zh-CN.md)

**一个面向 HLS 自动修复、优化与迁移的目标环境条件化、模型可插拔、证据驱动、预算约束型智能体。**

AgRefactor++ 接收普通 C/C++ 程序或已有 HLS 设计，并根据用户指定的 Vitis HLS
目标环境生成、修复和验证代码。系统使用真实的编译、仿真、综合、时序与资源证据
决定下一步操作；当探索必须停止时，系统应保留并返回当前最好的正确设计
`best_correct`。

本项目定位为通用、可长期维护的研究与工程系统。核心接口、目录和主流程不绑定某个
benchmark、竞赛、模型或 Vitis 版本。

## 两类任务模式

### 模式 A：普通 C/C++ → 目标 HLS

```text
普通 C/C++ 或不可综合程序
+ TargetProfile
→ 目标环境下功能正确、可综合并经过优化的 HLS
```

当前正式产品入口已经实现该模式的 source-only 重构基础。

### 模式 B：已有 HLS → 目标版本迁移

```text
已有 HLS
+ optional SourceProfile
+ TargetProfile
→ 目标版本下经过修复、验证、优化与 PPA 对比的 HLS
```

版本感知迁移是不可删除的核心目标。迁移运行时将在后续阶段实现；源版本信息是可选的，
系统不要求自动识别源版本。

## 八项不可删除的核心能力

以下八项能力构成项目的长期产品合同。它们不能被 benchmark 特判、模型特判或
“只写入 JSON 但不控制真实执行”的实现替代。

| # | 核心能力 | 最终必须做到什么 |
|---:|---|---|
| 1 | **TargetProfile** | Vitis 版本、settings、工具命令、part、platform、clock、资源约束、编译 flags、Tcl 生成和 report parser 必须真正控制执行。 |
| 2 | **双模式目标版本处理** | 同时支持普通 C/C++ → 目标 HLS，以及已有 HLS → 目标版本迁移。`SourceProfile` 保持可选。 |
| 3 | **Model API Registry** | Provider-neutral；模型由用户授权选择；API key 只通过环境变量提供；默认使用 fixed model policy。 |
| 4 | **分层 Prompt 适配** | 组合公共任务合同、当前阶段、TargetProfile、模型家族适配、当前证据、gated Memory 和输出合同。 |
| 5 | **结构化反馈与证据状态机** | Compile、Public tests、CSIM、CSYNTH、timing、resource 和 tool error 等证据决定合法下一步。 |
| 6 | **假设驱动三级安全优化器** | 按 Structural → Bottleneck → Pragma 探索，并提供 cheap gate、checkpoint、rollback、cache、候选谱系和 `best_correct`。 |
| 7 | **Memory Applicability Gate** | 支持 `off`、`gated`、`always`；记录正负经验，评估适用性，解释拒绝原因，并在经验不安全或不相关时主动弃权。 |
| 8 | **BudgetManager** | 记录并约束 LLM、token、cost、compile、test、CSIM、CSYNTH、cosim 和 wall time；探索停止时返回 `best_correct`。 |

## 当前可以使用什么

当前正式普通命令是：

```bash
python -m agrefactor.cli refactor \
  SOURCE.cpp \
  --top TOP_FUNCTION \
  --model MODEL_ID
```

当前实现已经包含 source-only 重构路径、TargetProfile 驱动的 Vitis 执行、固定模型
Profile、分层 Prompt、独立 Public/Hidden 验证、有限 Testbench/Candidate repair、
结构化终态、Execution Identity、artifact manifest 和共享预算记录。

安全三级优化器、Memory Applicability Gate 和版本迁移运行时属于独立后续阶段，
README 不把这些规划能力描述为已经完成。

精确的实现边界和验证证据见：

- [当前项目状态](docs/roadmap/PROJECT_STATE.md)
- [核心目标追踪](docs/roadmap/GOAL_TRACEABILITY.md)
- [复现与验证状态](docs/guides/REPRODUCTION_STATUS.md)

## 当前重构路径如何工作

```text
源代码 + 明确 top 函数 + TargetProfile + 用户选择的模型
→ 生成并认证 Public tests
→ 生成 Candidate
→ 生成并隔离 Hidden tests
→ Preflight 与有限 repair
→ 真实 Vitis HLS CSYNTH
→ Public 与 Hidden CSIM
→ accepted、structured rejected 或 infrastructure error
```

项目坚持 correctness first。Compile、Public validation 或 CSIM 失败的 Candidate，
不能因为其他指标较好而被接受。Hidden Testbench 源码不会暴露给生成模型或修复模型。

## 安装

### 环境要求

- Python 3.10。
- 当前已验证环境使用 Vitis HLS 2023.2。
- 一个 OpenAI-compatible 模型端点。
- 用于持久 artifacts 与临时 HLS 工作目录的本地存储空间。

### 安装步骤

```bash
git clone https://github.com/UTZZTU/AgRefactorPlusPlus.git
cd AgRefactorPlusPlus

conda create -n agrefactor python=3.10 -y
conda activate agrefactor
pip install -r requirements.txt
cp .env.example .env
```

只在本地编辑 `.env`。不要提交真实 API key 或私有路径。

示例：

```bash
DEEPSEEK_API_KEY=your-api-key
RUN_DIR=/absolute/path/to/agrefactor_runs
WORK_DIR=/absolute/path/to/agrefactor_work
```

运行前加载 Vitis：

```bash
source /path/to/Xilinx/Vitis/2023.2/settings64.sh
export AGREFACTOR_VITIS_RUN=/path/to/Xilinx/Vitis/2023.2/bin/vitis-run
```

## 快速开始

### Lightweight 测试生成

```bash
python -m agrefactor.cli refactor \
  src/heterorefactor/dfs/kernel.cpp \
  --top process_top \
  --model deepseek-v4-flash \
  --test-generation-profile lightweight \
  --public-tests auto \
  --hidden-tests auto
```

### Coverage-enhanced 测试生成

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

### 直接安全优化

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

直接 optimize 必须提供独立 reference 和 provided Public/Hidden suites。`full`
先运行已验收的 refactor 流程，再使用同一组已验证 suites 优化 accepted candidate：

模型 analysis 或 rewrite 输出若不满足 typed contract，会成为该层的可控、
不重试 abstention：优化器保留 `best_correct`、记录安全原因码，并在可行时继续。
网络、凭据、文件系统、工具链和 qualification 基础设施错误仍然硬失败。合同校验
不会证明源码适用性或 PPA；真实 compile、Public/Hidden CSIM、CSYNTH 与 typed PPA
仍是权威裁决。

```bash
python -m agrefactor.cli full kernel.cpp \
  --top kernel_top \
  --model deepseek-v4-flash \
  --public-tests auto \
  --hidden-tests auto
```

通过 `--output-dir` 可以把单次运行保存到精确目录。查看完整参数合同：

```bash
python -m agrefactor.cli refactor --help
```

所有参数与安全上限见
[CLI 参数参考](docs/guides/CLI_PARAMETER_REFERENCE.md)。

## 结果与 artifacts

一次持久化运行会记录理解和复现最终裁决所需的信息，包括：

- 有效的模型、Target、测试来源与预算合同。
- 生成 Candidate 与最终 Candidate。
- Public/Hidden 来源信息，同时确保 Hidden 源码不进入模型 Prompt。
- 可用的 Compile、CSIM、CSYNTH、timing 和 resource 证据。
- 结构化 trace、Prompt identity、预算 usage 与 Execution Identity。
- Candidate 被接受、拒绝、修复或回滚的原因。

关键文件包括 `full_result.json`、`execution_identity.json` 和
`run_artifact_manifest.json`。

当 Provider 返回 token usage 时，系统会累计真实观测数据。Provider 没有返回的数据
会被标记为 unavailable，而不是由系统伪造。只有存在匹配价格快照时，系统才会输出
费用估算。

## 仓库结构

| 路径 | 用途 |
|---|---|
| `agrefactor/` | 产品 CLI、合同、编排、验证、预算、模型与 Target Profile、artifacts |
| `flow/` | 生成 Agent、测试生成、兼容桥接与支持工具 |
| `src/` | 示例与评测 C/C++ 程序 |
| `tests/` | 确定性合同、集成与回归测试 |
| `docs/roadmap/` | 项目使命、八项核心能力、阶段路线与冻结实施合同 |
| `docs/guides/` | 使用、CLI、复现与 operator 指南 |
| `docs/acceptance/` | 分包验收与真实执行证据 |

## 路线

项目按照可独立验收的小阶段推进：

1. 建立 Target、模型、Prompt、反馈、验证和预算共享底座。
2. 完成可信的 source-only 自动重构。
3. 实现假设驱动的三级安全优化。
4. 实现带适用性门控的自进化 Memory。
5. 实现目标版本感知的 HLS 迁移。
6. 采用固定协议开展多 kernel 评测与消融实验。

权威路线见 [docs/roadmap/ROADMAP.md](docs/roadmap/ROADMAP.md)。

## 与原始 AgRefactor 的关系

AgRefactor++ 是原始
[AgRefactor](https://github.com/Williamzou0123/AgRefactor)
研究代码库的独立持续演化扩展。本项目保留对原始贡献的署名，同时开发更明确、
更适合长期维护的执行与验证架构。

使用、引用或分发本仓库时，应尊重原项目论文、作者贡献和所有适用的许可条款。

## Stage 3.8 评测

S3.8 包运行 direct `optimize`、真实 source-only `full` 与独立 qualification 的
Legacy `simple_iter`。默认验收矩阵为三个不同 kernel × 两次重复 × 三个 arm，
报告 correctness、PPA、invalid ratio、rollback、调用数和 wall time，不宣称
稳定优于基线。

S3.8 V2 修正：首次目标主机运行保留了有效产品路径证据，但暴露了 Legacy
qualification 观察器缺陷。只有 6 个 `simple_iter` 单元重跑并证明真实模型调用及
独立 qualification 后，Stage 3 才能关闭。
