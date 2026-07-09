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

## 文档结构调整

- README 增加仓库结构说明，方便快速理解各目录用途。
- README 中将项目定位从“相比传统 HLS 重构流程”修正为“相比原始 AgRefactor”。
- README 主体保持快速上手导向，详细变更和环境说明分别放入 `docs/CHANGELOG.md` 与 `docs/ENVIRONMENT.md`。

---

## 运行结束 Token / Cost 统计

- 在基础 `flow.new` 流程中增加 token / cost 统计。
- 每次运行开始时清空本次 agent usage registry。
- 每个 `ConversableAgent` 创建后自动注册到 usage registry。
- 运行成功或失败结束时自动打印 `Token / Cost Summary`。
- 优先使用 `autogen.gather_usage_summary()` 汇总 usage。
- 如果 AG2 聚合失败，则回退到每个 agent 的 `get_actual_usage()` / `get_total_usage()`。
- 已使用 DeepSeek V4 Flash 最小 demo 验证统计输出正常。


---

## DeepSeek V4 Pro 端到端验证

- DeepSeek V4 Pro 已完成 DFS 最小 demo 端到端重构验证。
- 测试样例：
  - `src/heterorefactor/dfs/kernel.cpp`
  - `process_top` -> `process_top_hls`
- 成功日志：
  - `RETRY_COUNT:0`
  - `HLS refactoring with RAG completed successfully.`
- 本次 Pro 运行的 token / cost 统计：
  - Prompt tokens: 9,877
  - Completion tokens: 13,604
  - Total tokens: 23,481
  - Estimated cost: $0.016132
- 当前暂定模型分工：
  - DeepSeek V4 Flash 用于 HLS 重构 / 基础构建。
  - DeepSeek V4 Pro 用于 HLS 性能优化 / 复杂策略推理。
