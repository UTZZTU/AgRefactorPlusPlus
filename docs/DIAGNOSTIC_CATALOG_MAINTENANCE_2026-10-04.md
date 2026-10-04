# 诊断目录维护说明

运行时事实来源为服务器 `/data/AgRefactor/configs/diagnostic_catalog.json`；本说明不参与运行时匹配。

## 添加错误规则

已有类别增加编号时，只增加一条 `rules` 记录，例如：

```json
{
  "id": "hls_new_unsupported_form",
  "match": {"hls_codes": ["HLS 999-123"]},
  "stage": "csynth",
  "category": "unsupported_construct",
  "owner_policy": "resolve_from_evidence",
  "priority": 100,
  "notes": "示例编号；实际新增须来自已复核日志"
}
```

此示例不在生产目录中。`id` 和类别键使用小写字母、数字、下划线，稳定 ID 不随说明文字改变。HLS code 必须包含 `HLS ` 前缀。`patterns` 是大小写不敏感正则表达式的数组；数组内任一模式即可匹配。如果同时提供编号与文本模式，则两者都必须满足。

匹配按 stage、evaluation_split 和 priority 限定。高优先级先匹配；相同优先级命中不同类别会报目录冲突。可静态识别的相同编号或相同模式冲突在加载时拒绝，任意正则交集无法完整静态判定，因此运行时也会检查冲突。不要使用能匹配空字符串的正则或没有任何匹配条件的规则。

## 添加细分类

新错误只是在现有类别中增加一种表达形式时，复用已有类别。只有语义区别值得长期记录时才增加类别。无需新增 Python 枚举的细分类写法如下：

```json
"new_unsupported_form": {
  "feedback_category": "unsupported_construct",
  "description": "新发现的构造限制",
  "evidence_requirements": ["tool_launched", "execution_completed"],
  "allowed_actions": ["review_unknown", "repair_candidate_if_proven"]
}
```

随后规则的 `category` 引用 `new_unsupported_form`。系统保存 `category_id=new_unsupported_form`，正式反馈仍使用 `FeedbackCategory.UNSUPPORTED_CONSTRUCT`。这样新增细分类通常只编辑 JSON；若确实需要新阶段、责任策略、正式 FeedbackCategory 或修复动作，则必须修改对应代码和测试，不能靠一个类别词创造执行能力。

## 字段及权限

- `stage`：复用 FeedbackStage，包括 input、configuration、static_check、compile、link、test、csim、csynth、cosim、toolchain。
- `evaluation_split`：可选，public、hidden 或 all，默认 all；Hidden 最终评估仍由现有 RecoveryPolicy 隔离。
- `owner_policy`：当前只允许 resolve_from_evidence。
- `evidence_requirements`：raw_diagnostic、tool_launched、execution_completed、source_location、compile_unit_provenance、original_isolation、runtime_contract、invocation_identity、report_identity。
- `allowed_actions`：review_unknown、repair_candidate_if_proven、repair_testbench_if_proven、fix_configuration，均是分类说明，不直接执行路由。
- `confidence`：high、partial、aggregate、unknown，表示分类可信程度，不等于责任已证实。

目录不能直接声明 owner 或 route，也不能增加任意证据字段或动作词。Hidden 专用规则不允许 agent repair；同一通用类别用于 Hidden 分类时，返回结果会去掉 Candidate/Testbench repair 描述。Hidden 生成资格检查继续走现有生成合同，Hidden 最终结果不能进入 Candidate prompt。

文本模式只负责分类。最终 owner 仍由执行完成记录、编译单元/行级 provenance、源码 hash、typed runtime contract、Original isolation 等现有证据机制决定。`unknown` 不等于诊断被丢弃：原始安全文本、stage、编号、diagnostic_id、category_id 和 evidence_fingerprint 会保留供复核。本次没有实现记忆模块或自动提升规则。

## 提交前检查

在服务器激活环境后执行：

```bash
source /data/agrefactorpp_env.sh
cd /data/AgRefactor
python -m unittest tests.test_diagnostic_catalog tests.test_csynth_diagnostic_parser tests.test_catalog_diagnostic_projection -q
```

新规则应至少用实际脱敏诊断验证一个匹配例和一个不应匹配例，并回放相关历史日志。规则不得包含案例名、函数名或特定目录放行条件。新增编号不需要改 parser 的 if/elif；新增责任判断机制才需要工程实现。
