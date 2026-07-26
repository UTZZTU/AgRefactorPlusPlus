# Pre-Stage-3 Transition Lessons

> **历史材料。** 本文合并原一次性交接文档、Pre-Stage-3 Bridge 和单次 Testbench repair retry 备忘中的长期有效经验。它不包含当前执行状态。

## 1. 通用项目原则

- 核心包、类名、配置和流程保持通用；
-规则抽象为 TaskSpec、TargetProfile、Budget、Evaluation role、Feedback、State 和 Policy；
-当前场景是首个可执行配置，不是系统唯一用途；
- correctness → trustworthy evidence → legal action → bounded iteration → optimization；
- Hidden evidence 不能进入模型 Prompt、普通结果或普通 trace；
- deterministic tests 不能冒充真实模型/Vitis 验收。

## 2. Pre-Stage-3 Bridge 的有效经验

### 小步、证据驱动

- 先修复真实失败，不为假设性问题扩建系统；
-优先修改现有组件；
-只有重复真实失败证明结构无法支持要求时才新增顶层子系统；
-不要创建没有 consumer 的 registry、schema 或 budget；
-保持 retry、generation rounds 和 acceptance bounded。

### 真实生成与正式裁决分离

Legacy 生成可以提供 Candidate/Testbench，但：

```text
legacy success
≠ formal accepted
```

最终裁决必须来自正式 Preflight、CSYNTH、Public/Hidden validation 和完整 Execution Identity。

## 3. Model/Framework 参数失配

真实运行曾出现 Provider、AutoGen/AG2 和 logical reasoning 参数不一致。长期结论：

- 用户层语义与 Provider 参数必须分离；
-静态 Profile 只记录有证据的映射；
-不因单模型失败引入自动路由；
-具体模型/部署 capability 需要独立验收。

## 4. Testbench/Stub 协议

真实 DFS 失败证明：

- separate translation unit 需要完整 forward declarations；
-每轮重新生成 Testbench 时，相关 stub 必须同步生成；
-empty/malformed model output 不能进入 C++ extraction；
-preservation contract 必须显式记录 required declarations、macros 和 minimum call counts；
-不能使用静态启发式替代真实 compile/run/coverage/Vitis evidence。

## 5. Retry Feedback

一次真实 Testbench repair 中，第二次请求没有携带第一次 response-contract rejection，因此不构成 evidence-driven refinement。

长期修正规则：

- bounded retry 必须携带安全 prior-attempt summary；
-response-contract failure、empty/unchanged response 要进入下一次反馈；
-Hidden 内容仍排除；
-deterministic preservation validator 保持权威。

当前 repair 默认值和上限以 [CLI 参数参考](../guides/CLI_PARAMETER_REFERENCE.md) 为准，不使用历史文档中的旧数字。

## 6. 为什么删除原 reference 文件

原文件分别是：

- 某次新聊天的仓库快照；
-已经关闭的临时阶段桥；
-单次失败修复备忘。

它们包含过期 HEAD、测试数、阶段状态和参数值，继续放在当前 reference 栏目会误导人和模型。Git 历史仍保留原文，长期有效经验已合并到本文和正式 acceptance。
