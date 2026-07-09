# 变更记录

本文件记录 AgRefactor++ 相对于原始 AgRefactor 的主要修改。  
README 只保留上手所需内容，具体变更放在这里维护。

---

## README 中文化与文档重构

- 将 README 主体整理为中文说明。
- 将项目名统一为 **AgRefactor++**。
- 删除中英文混排造成的重复说明。
- 将环境说明从 README 中拆出到 `docs/ENVIRONMENT.md`。
- 将版本/提交变更从 README 中拆出到 `docs/CHANGELOG.md`。
- README 主体聚焦项目思想、快速开始、模型后端和常用命令。

---

## Provider-compatible LLM config

- 修复 AG2/AutoGen 0.11.x 下 `LLMConfig` 初始化方式变化导致的兼容问题。
- 将原来的 `LLMConfig(**config_dict)` 调整为 `LLMConfig(config_dict)`。
- 保留 OpenAI-compatible provider 的 `api_type="openai"` 配置路径。
- 保留 Gemini 的 `api_type="google"` 配置路径。
- 支持通过 `OPENAI_BASE_URL` 接入 DeepSeek 等 OpenAI-compatible API。

---

## DeepSeek V4 适配

- 已使用 DeepSeek V4 Flash 完成最小 demo 端到端测试。
- 针对 DeepSeek structured output，将 Python/Pydantic `response_format` 转换为 JSON mode：
  - `{"type": "json_object"}`
- 为 DeepSeek V4 thinking mode 设置更大的默认 `max_tokens`，避免只返回 reasoning 内容而不返回最终 `content`。
- 添加 DeepSeek V4 Flash / Pro 的默认价格元数据，避免 AG2 输出 unknown-model cost warning。
- 当前默认价格仅用于消除 warning 与粗略估算；如果需要精确成本统计，应按模型服务商最新价格覆盖 config 中的 `price` 字段。

---

## Identifier JSON 解析增强

- 原 AgRefactor 默认 identifier agent 返回：
  - `{"identified_items": [...]}`
- 在 OpenAI-compatible 模型中，实际可能返回裸 JSON list：
  - `[...]`
- AgRefactor++ 增加 `_parse_identified_items()`，同时兼容上述两种格式。
- 对异常 JSON 类型给出更明确的错误信息，方便后续调试。

---

## 已完成的最小验证

当前已验证：

- 基础 `flow.new` 单 kernel 流程可以运行。
- 示例 kernel：`src/heterorefactor/dfs/kernel.cpp`
- 原始 kernel：`process_top`
- 目标 kernel：`process_top_hls`
- 大模型后端：DeepSeek V4 Flash
- HLS 工具：Vitis HLS 2023.2
- 成功日志：`HLS refactoring with RAG completed successfully.`

---

## 后续计划

- Vitis HLS 多版本知识库。
- 编译/综合反馈驱动的多轮修复。
- 可复用 AST/Clang 迁移规则。
- 跨版本 HLS 迁移测试集。
- 固定版本之间的 HLS 工程迁移与适配。
- 更完整的 RAG、HeteroRefactor、batch mode、optimization 流程验证。
- 运行结束后的 token 与费用统计。


---

## DeepSeek V4 Pro 当前状态

- DeepSeek V4 Pro 的 API 调用与 LLM 配置路径已经打通。
- 在当前 DFS 最小 demo 中，Pro 能进入 agent 生成、修复与 HLS 调用流程。
- 当前端到端 HLS 闭环未稳定通过，日志显示自动修复达到最大重试次数后失败。
- 因此 README 中暂不宣称 Pro 已完成端到端验证，而是标记为“API 调用与配置层已打通，完整 HLS 闭环仍需继续优化与验证”。


---

## 文档结构调整

- README 增加仓库结构说明，方便快速理解各目录用途。
- README 中将项目定位从“相比传统 HLS 重构流程”修正为“相比原始 AgRefactor”。
- README 主体保持快速上手导向，详细变更和环境说明分别放入 `docs/CHANGELOG.md` 与 `docs/ENVIRONMENT.md`。
