# P1 模型运行时审计决策账本

> **状态：** P1-A、P1-B0、P1-B1、P1-B2、P1-B3、P1-B4A 已完成；P1-B4B estimation/native accounting 为当前唯一活跃实现项
> **审计基线：** `8b543267a88ed63d343bd633cf29cd6edf9c4127`  
> **人工复核日期：** 2026-07-22  
> **作用：** 保存自动审计发现、人工修正、新增发现、阶段归属和关闭证据。  
> **上位合同：** [`PRE_STAGE3_PRODUCTIZATION_PLAN.md`](PRE_STAGE3_PRODUCTIZATION_PLAN.md)

## 1. 证据边界

原始只读审计：

```text
/data/agrefactor_runs/pre_stage3_p1_model_budget_pricing_audit_20260722_132816
```

证据摘要：

```text
branch = stage2-general-feedback
HEAD = 8b543267a88ed63d343bd633cf29cd6edf9c4127
scanned files = 166
parameter/symbol occurrences = 1597
Python parse errors = 0
automated findings = 14
raw confirmed = 13
raw needs_review = 1
repository modified = false
model API called = false
Vitis run = false
```

文件哈希：

```text
report.md  sha256=2d2d3176355c9fbd449f3dbce952317fd4608fac6aef73e980a28627a4539bbe
audit.json sha256=b2181c114baea6ee0e3dfea014c9a4a1e30e7ea61740c7180c3f518834e664d2
```

原始审计是不可变证据；本账本记录人工复核后的最终决策。两者冲突时，不改写
原始审计，而是在这里明确修正原因。

## 2. 人工复核修正

### 2.1 F03：由 `needs_review` 修正为 `confirmed`

`CandidateModelAdapter` 已经真实执行：

```text
ModelFamilyProfile.safe_default_parameters
→ ModelSpec.default_parameters
→ adapter call parameters
→ ModelRequest.parameters
```

现有测试也验证了参数优先级和 Provider 实际收到的参数。因此 P1 必须复用
`ModelFamilyProfile.merge_parameters()`，不得创建第二套参数合并器。

### 2.2 F14：结论保留，自动证据行修正

F14 的结论正确：`BudgetUsage` 已包含 P5 所需的累计调用次数、Token、Cost 和
elapsed time。但自动报告中的部分证据误引用了 `BudgetLimits.max_*` 行。

正确证据对象是：

```text
agrefactor/runtime/budget.py::BudgetUsage
llm_calls
tool_calls
compile_calls
csim_calls
csynth_calls
tokens
cost_usd
elapsed_s
```

P5 后续必须消费同一个 `BudgetUsage` snapshot，不从日志重新计算。

### 2.3 新增 F15：费用数据结构被 USD 字段锁死

当前：

```text
TokenUsage.cost_usd
BudgetUsage.cost_usd
BudgetLimits.max_cost_usd
```

但冻结产品合同要求保留模型官方原生币种，并且当前不做汇率转换。人民币或其他
币种的估算值不能塞入 `cost_usd`。

P1-B 必须新增结构化费用对象，至少表达：

```text
amount
currency
estimation_quality
pricing_snapshot_hash
assumptions
```

旧 `cost_usd` 在 consumer 迁移期间兼容保留，不能直接删除，也不能作为多币种
正式事实来源。

## 3. 最终发现与处置表

| ID | 最终状态 | 发现 | 最终归属 | 关闭证据 |
|---|---|---|---|---|
| F01 | confirmed | Repair-aware 未把 reasoning 输入传给 Candidate adapter | P1-C | Legacy 与 Stage 2 使用同一 effective config 的测试 |
| F02 | confirmed | 显式未知 family 会退化为空 Profile | P1-A | 未知显式 family 在 Provider 前失败 |
| F03 | confirmed-after-review | 现有参数 merge seam 可复用 | P1-A invariant | precedence 与 Provider-request 测试 |
| F04 | confirmed | Provider 应保持纯传输层 | P1-A invariant | Provider 无 vendor-specific 参数分支 |
| F05 | confirmed | HLSAgentLoader 含 DeepSeek 专用兼容逻辑 | P1-C | shared config 接入后 Legacy parity |
| F06 | confirmed | Legacy 价格形成第二事实来源 | P1-B/P1-C | 统一 pricing snapshot 后旧 helper 退出权威路径 |
| F07 | confirmed | 普通 CLI 仍暴露 legacy/repair-aware | P2/P0 | 新入口完成后再弃用，P1 不删除 |
| F08 | confirmed | BudgetManager/BudgetUsage 是可复用 run-level 核心 | P2/P5 invariant | 不新增第二计数器 |
| F09 | confirmed | BudgetLimits 无 default/ceiling/user provenance | P2 | Budget Profile/Resolver 验收 |
| F10 | confirmed | Token/Cost 只能事后 observed accounting | current invariant | `observed_only`, `blocking=false` |
| F11 | confirmed | Legacy 定价硬编码且仅覆盖 DeepSeek | P1-B/P1-C | 官方 snapshot 与迁移测试 |
| F12 | confirmed | 正式 Provider 当前不计算费用 | P1-B | verified/unavailable/approximate 三路径测试 |
| F13 | confirmed | 当前无正式 pricing provenance schema | P1-B | typed schema、source、date、hash |
| F14 | confirmed-evidence-corrected | P5 可直接消费 BudgetUsage | P5 | summary/JSON 与 snapshot 一致 |
| F15 | confirmed-manual | `cost_usd` 与原生币种合同冲突 | P1-B | 多币种结构与旧字段兼容测试 |

## 4. 不可破坏的架构规则

1. 只有一份 authoritative effective model configuration。
2. 参数优先级保持：
   `family default < model default < explicit call/user override`。
3. 显式未知 family 必须报错；未指定 family 才能使用 Generic/Neutral。
4. `OpenAICompatibleProvider` 只负责传输、凭证解析和响应规范化。
5. 兼容、alias、reasoning map/omit/reject 在 Provider 启动前完成。
6. Pricing metadata 与 request parameters 分离。
7. Token/Cost 当前是 observed-only 软预算，不阻断流程。
8. 不自动做币种转换，不把非 USD 金额写入 `cost_usd`。
9. `BudgetManager/BudgetUsage` 保持唯一 run-level 用量事实来源。
10. P1 不删除 `--legacy / --repair-aware`，也不实现普通 source-only CLI。

## 5. P1 实施拆分

### P1-A：静态模型兼容核心——deterministic acceptance 已完成

目标：

```text
known family profiles
verification status
reasoning low/medium/high policy
supported/rejected/aliased parameter policy
strict unknown-family behavior
existing merge seam activation
```

首批 family：

```text
DeepSeek
Kimi
GLM
MiniMax
Qwen
Generic OpenAI-compatible
```

允许的主要代码范围：

```text
agrefactor/models/base.py
agrefactor/models/family.py
agrefactor/models/registry.py
agrefactor/models/candidate_adapter.py
agrefactor/models/__init__.py
agrefactor/models/known_profiles.py
```

允许的测试范围：

```text
tests/test_model_family_profile.py
tests/test_model_registry.py
tests/test_candidate_model_adapter.py
tests/test_known_model_profiles.py
```

P1-A 不加入真实价格数字，不修改 CLI、Legacy、BudgetManager 或 Provider。

### P1-B：官方价格与费用结构

目标：

```text
typed PricingSnapshot
typed CostEstimate
official source identity
retrieval/effective date
currency and billing unit
cache-hit/cache-miss/input/output/tier rules
verification status
stable snapshot hash
unavailable/unverified/approximate behavior
cost_usd compatibility migration
```

价格只从模型提供方官方资料获取。不同 API model ID 分别记录，不能只按 family
记录一个价格。

### P1-C：Stage 2 与 Legacy 统一接线

目标：

```text
one EffectiveModelConfig
→ CandidateModelAdapter
→ LegacyRefactorSettings translation
→ HLSAgentLoader
```

先建立 parity 证据，再删除或降级 Legacy DeepSeek patch。不得先删 consumer
仍在使用的代码。

### P1-D：验收与真实 smoke

顺序：

```text
targeted deterministic tests
→ full deterministic regression
→ one bounded DeepSeek network smoke
```

静态声明不等于网络验证。只有真实 smoke 通过的具体模型/profile 才能标记
`network_smoke_verified`。

## 6. P1-A 验收门槛

1. 六个静态 family Profile 可解析。
2. 显式未知 family 在 Provider 启动前失败。
3. 未指定 family 使用 Generic/Neutral。
4. reasoning `low/medium/high` 由 Profile 映射、忽略或拒绝。
5. alias 冲突和 rejected 参数明确失败。
6. 参数 precedence 保持不变。
7. CandidateModelAdapter 继续使用现有 merge seam。
8. Provider 不增加 vendor-specific 分支。
9. manifest 和 artifacts 不包含凭证。
10. 所有既有模型测试和完整回归通过。
11. 不调用真实模型、C/C++、CSIM、CSYNTH 或 Vitis。
12. 变更范围只属于 P1-A。

## 7. 明确不属于当前 P1-A

```text
官方价格数值采集
费用估算接线
Legacy 参数迁移
normal refactor/optimize/full CLI
Public/Hidden source contract
Budget default/ceiling/user resolver
P5 terminal output
P0 real DFS
dynamic model probing
automatic routing
hard Token/Cost enforcement
currency conversion
legacy flag deletion
```

## 8. 推进与更新规则

每完成一个 P1 子包，必须在本账本追加：

```text
commit
changed files
targeted tests
full regression
real network/tool evidence（若该子包要求）
remaining findings
newly discovered findings
```

发现与原决策冲突的新证据时，新增 amendment，不静默删除旧记录。

## 8.1 P1-A consumer-test scope amendment

Implementation preflight found that strict unknown-family behavior affects four
existing deterministic fixtures that used the ad-hoc family name `reasoning`,
and that the existing OpenAI-compatible testbench factory emits the historical
family spelling `openai`.

This is consumer migration evidence, not a product-scope expansion. P1-A may
therefore also update only these compatibility tests:

```text
tests/test_candidate_repair_loop.py
tests/test_legacy_testbench_repair_integration.py
tests/test_model_testbench_repairer.py
tests/test_testbench_repair_factory.py
```

The three `reasoning` fixtures must explicitly register their local typed
Profile. The historical `openai` spelling is retained only as an alias to the
canonical `generic-openai-compatible` Profile. Arbitrary unknown family names
remain rejected before Provider execution.

## 8.2 P1-A deterministic acceptance

Formal evidence:
[`P1A_STATIC_MODEL_COMPATIBILITY_ACCEPTANCE.md`](P1A_STATIC_MODEL_COMPATIBILITY_ACCEPTANCE.md)

```text
implementation_commit=e9f4a51744ce44c04236466450b8af85ebf9be9c
baseline_unittest=873/873
p1a_unittest=889/889
test_delta=+16
consumer_audit=passed
patch_id=bc4e6a58447f86129dcf54ed536f3456fc8a9a04
real_model_or_vitis_acceptance=0
```

F02 is closed. F03 and F04 remain accepted architecture invariants.
F01, F05, F06, F11, F12, F13 and F15 continue in P1-B/P1-C.

## 8.3 P1-B0 pricing consumer audit and P1-B1 freeze

Formal decisions:
[`P1B0_PRICING_CONSUMER_AUDIT_DECISIONS.md`](P1B0_PRICING_CONSUMER_AUDIT_DECISIONS.md)

```text
audit_head=24918d6fcfe1250043cd6a72082456241fa4679e
tracked_files=461
occurrences=972
cost_pricing_occurrences=190
automated_findings=8
manual_amendments=2
official_snapshots_retrieved=5
official_pages_unreadable=1
repository_modified=false
```

P1B0-F09 records the wider runtime migration surface. P1B0-F10 separates raw
source and canonical pricing hashes and corrects the nonexistent
`tests/test_model_api.py` path.

P1-B1 is restricted to typed schema and backwards-compatible `TokenUsage`
extension. Numeric prices, estimator wiring, Budget, serialization, Legacy,
CLI and P5 remain outside P1-B1.

## 8.4 P1-B1 deterministic acceptance

Formal evidence:
[`P1B1_TYPED_PRICING_SCHEMA_ACCEPTANCE.md`](P1B1_TYPED_PRICING_SCHEMA_ACCEPTANCE.md)

```text
implementation_commit=bb219ea9e3049b4f5959c9dbb9c0e585875afd82
baseline_unittest=889/889
p1b1_unittest=920/920
new_tests=31
patch_id=c793e3d1402bf63977e7a25d3ce829d46416fab2
compileall_recovery=passed
provider_budget_legacy_modified=false
official_numeric_prices_added=false
estimator_implemented=false
```

P1B0-F01, F07 and F10 are closed at the typed-schema level. P1B0-F06 is
partially closed because optional token categories now exist, while Provider
normalization remains P1-B3. Runtime serialization and native-currency
consumer migration remain P1-B4/P1-C.

## 8.5 P1-B2 deterministic acceptance

Formal evidence: [`P1B2_OFFICIAL_PRICING_SNAPSHOTS_ACCEPTANCE.md`](P1B2_OFFICIAL_PRICING_SNAPSHOTS_ACCEPTANCE.md)

```text
implementation_commit=571c51fcc250592a21bf40b3831b7dccfc6400aa
baseline_unittest=920/920
p1b2_unittest=950/950
new_tests=30
source_records=5
verified_sources=4
unreadable_sources=1
verified_snapshots=6
patch_id=d0babc3b57dbdef9370786b7e11d0cc39b93760e
currency_conversion=false
glm_numeric_price_inferred=false
provider_budget_legacy_modified=false
estimator_implemented=false
```

P1B0-F08 is closed. The official snapshot half of F05/F11 is complete; Legacy authority migration remains P1-C. F12 and Provider token-category normalization advance to P1-B3.

## 8.6 P1-B3 deterministic acceptance

Formal evidence:
[`P1B3_COST_ESTIMATOR_ACCEPTANCE.md`](P1B3_COST_ESTIMATOR_ACCEPTANCE.md)

```text
base_commit=2d9487cdedd8f15c811ef256a6a28909988438a5
implementation_commit=1c6c7efc9160c104319d4cc01a9b96c3ae0d082e
correction_commit=2296a18f09aa478afcdc5cc9652b4d9166a44149
p1b2_baseline=950/950
implementation_unittest=992/992
final_unittest=993/993
estimator_tests_added=42
export_fix_tests_added=1
implementation_patch_id=588353a2ff2107ad9a64c488e54715de9360af1f
correction_patch_id=91e17d224f49b8ee63c9999b24234776fcf70829
verified_path=true
unavailable_path=true
approximate_path=true
automatic_snapshot_selection=false
currency_conversion=false
provider_budget_legacy_modified=false
```

F12 is closed. P1B0-F03/F06 and F15 are partially closed at the
provider-neutral estimator level; Provider usage normalization, runtime
serialization and native-currency run accounting advance to P1-B4.

This acceptance also corrects the P1-B2 evidence field from the accidental
status-line substitution back to `new_tests=30`; no P1-B2 code or acceptance
claim changes.

## 8.7 P1-B4A deterministic acceptance

Formal evidence:
[`P1B4A_USAGE_NORMALIZATION_SERIALIZATION_ACCEPTANCE.md`](P1B4A_USAGE_NORMALIZATION_SERIALIZATION_ACCEPTANCE.md)

```text
parent_commit=4e9353f81c6c284a32f514811de61f0067045cbb
implementation_commit=ae276f3df79685a7edd36dc6b06c7d82d5784e7a
baseline_unittest=993/993
p1b4a_unittest=1016/1016
new_tests=23
patch_id=89db552f6660c8e5fa9ac2a67deb21909ae25ae3
provider_usage_breakdown_normalized=true
shared_model_response_serialization=true
candidate_serialization_migrated=true
repair_serialization_migrated=true
legacy_serialization_keys_preserved=true
estimator_wiring=false
budget_modified=false
runtime_runner_modified=false
legacy_modified=false
currency_conversion=false
```

P1B0-F04 is closed. Provider token-category normalization is complete for the
accepted OpenAI-compatible aliases. P1B0-F03/F06/F09 and F15 remain partially
open until P1-B4B connects an explicit pricing snapshot and extends the single
BudgetManager/BudgetUsage ledger for native-currency observed costs.

## 9. 当前下一步

```text
Step 0 documentation freeze                 completed
Step 0 read-only consumer audit              completed
Step 0 manual audit review and decision log  completed
Step 1 P1-A static model compatibility core  completed
Step 1 P1-B0 pricing consumer audit           completed
Step 1 P1-B1 typed pricing schema             completed
Step 1 P1-B2 official model snapshots         completed
Step 1 P1-B3 usage-to-cost estimator          completed
Step 1 P1-B4 compatibility migration          active
Step 1 P1-B4A usage normalization/serialization completed
Step 1 P1-B4B estimation/native accounting    active
```

P1-B4A 已完成。当前只推进 P1-B4B explicit estimation and native-cost accounting；P1-C、P1-D、P4、P2、P0 与 Stage 3 不得提前开始。
