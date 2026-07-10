# 变更记录

本文档记录 AgRefactor++ 相对于原始 AgRefactor 的主要代码与文档修改。原项目已有但仅在本地完成复现的功能，统一记录在 `docs/REPRODUCTION_STATUS.md`，避免把“复现验证”误写成“新增功能”。

## 未发布

### 文档

- 精简主 README，使其只保留项目定位、已验证能力、快速开始和文档入口。
- 新增 `docs/USAGE.md`，集中维护单 kernel、RAG、批量实验和 optimization 命令。
- 新增 `docs/REPRODUCTION_STATUS.md`，区分已验证、部分验证、暂未验证和暂停功能。
- 修正安装说明：仓库当前没有 `setup.py` 或 `pyproject.toml`，不再建议执行 `pip install -e .`。
- 明确版本感知迁移仍是研究方向，而不是当前已经完整实现的能力。
- 明确 HeteroRefactor 不是主流程必需依赖，当前因外部 EDG binary 不可用而暂停。

### 复现状态同步

- 补充 RAG 成功/失败 trial 写入与检索的验证状态。
- 补充 `flow.parallel_kernel` 的小规模框架验证和稳定性限制。
- 补充 `opt.simple_iter` 的多轮综合反馈优化与最佳设计保存验证。
- 将 coverage/hidden TB、remote HLS/MCP 等代码路径标记为尚未纳入稳定主验证基线。

## DeepSeek 与 OpenAI-compatible 适配

- 修复 AG2/AutoGen 0.11.x 下 `LLMConfig` 初始化方式变化导致的兼容问题。
- 保留 OpenAI-compatible provider 的 `api_type="openai"` 配置路径。
- 支持通过 `OPENAI_BASE_URL` 接入 DeepSeek 等兼容服务。
- 保留 Gemini 的 `api_type="google"` 配置路径。
- 使用 DeepSeek V4 Flash 和 Pro 完成 DFS 最小样例端到端验证。

## Structured output 与 token 配置

- 将不适用于 DeepSeek 的 Python/Pydantic `response_format` 转换为 JSON mode：

  ```json
  {"type": "json_object"}
  ```

- 为 thinking 模型设置更大的默认 token 预算，减少只返回 reasoning、没有最终 content 的情况。
- 增加 DeepSeek V4 Flash / Pro 的价格元数据，用于消除 unknown-model warning 和提供粗略成本统计。
- 价格元数据不应视为长期准确报价。

## Identifier JSON 解析增强

- 新增 `_parse_identified_items()`。
- 同时兼容：

  ```json
  {"identified_items": []}
  ```

  和：

  ```json
  []
  ```

- 对异常 JSON 类型提供更明确的错误信息。

## RAG memory 解析增强

- 增强 RAG agent 对模型输出的解析能力。
- 兼容代码块、对象、列表等更灵活的 JSON 包装形式。
- 忽略本地实验生成的临时 RAG store，避免把运行数据库误提交到 Git。

## Token / Cost 汇总

- 在 `flow.new` 开始时重置本次 usage registry。
- agent 创建后注册到统一 usage registry。
- 在流程成功或失败退出前打印 `Token / Cost Summary`。
- 优先使用 AG2 聚合接口；失败时回退到单 agent usage。
- 已在 DeepSeek Flash 和 Pro 的最小重构实验中验证输出。

## 文档中文化与结构调整

- 将项目名统一为 AgRefactor++。
- README 主体改为中文并删除重复的中英文说明。
- 将环境说明拆分到 `docs/ENVIRONMENT.md`。
- 将详细代码修改拆分到 `docs/CHANGELOG.md`。
- README 聚焦新用户第一次运行所需内容。
