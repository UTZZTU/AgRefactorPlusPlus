# V2.3 Inheritance-First Stage II Checkpoint

日期：2026-09-21  
状态：完成，已通过独立证据审计；按路线停止在阶段 III 之前  
分支：`research-roadmap-v2.3`

## 范围和边界

本 checkpoint 只覆盖阶段 II 冻结的四个 HeteroRefactor pilot：

- `dfs`
- `mergesort`
- `ahocorasick`
- `strassen`

所有运行均使用正式 `refactor` 入口，`R2-R4=false`、经验系统关闭、
`optimize=false`、`full=false`，Vitis HLS 2023.2，public test only，Hidden suite 为 `none`。
没有新增 CLI、Candidate repair 流程、Vitis runner 或成功裁决流程，也没有按案例路径、
算法名称或 HLS 错误编号添加特判。

服务器会话按约定先执行：

```bash
source /data/agrefactorpp_env.sh
source ~/.config/agrefactor/provider.env
```

## 零调用冻结和基线

阶段 II 的 preflight v2 对四个案例分别验证了 source oracle、public differential oracle
两次稳定通过，以及 mismatch stub 返回码为 1；没有 Hidden/future 依赖。此前 Aho-Corasick
固定输入长度错误的 preflight v1 保留为不可变失败证据，未被覆盖。

四个 source baseline 均为：host oracle 通过、public CSIM 通过、CSYNTH 因真实递归调用失败、
COSIM 因 synthesis 失败而未运行。共同的 Vitis 证据为 `HLS 214-139 Recursive function
calls are not supported`，因此这些案例是有效的 refactor opportunity，不是空 oracle 或环境失败。

## 普通 refactor 结果

| 案例 | source baseline | 正式 refactor | Candidate | 验证 |
| --- | --- | --- | --- | --- |
| `dfs` | CSYNTH failed | succeeded | source SHA 不同 | CSIM/CSYNTH/COSIM passed |
| `mergesort` | CSYNTH failed | succeeded | source SHA 不同 | CSIM/CSYNTH/COSIM passed |
| `ahocorasick` | CSYNTH failed | succeeded | source SHA 不同 | CSIM/CSYNTH/COSIM passed |
| `strassen` | CSYNTH failed | succeeded | source SHA 不同 | CSIM/CSYNTH/COSIM passed |

四个成功运行都通过正式验证、Candidate 身份和 `accepted_ready` 审计，repair attempt 为 0，
并且只绑定冻结的 public test。`dfs` 的第一次运行因普通 refactor 错误构造 Hidden-dependent
optimization material，形成不可变失败证据；该通用产品缺陷已在 `de701e1` 修复并由第二次
运行回归通过。`strassen` 的第一次运行是 Provider timeout，第二次是 generation-only
extraction failure，第三次成功；前两次失败证据均保留。

结果计数：

- ordinary refactor success：4；
- regression：0；
- blocked：0（最终选定运行）；
- source baseline 全部通过：0；
- Provider：`60 / 200`，包含失败尝试和重试；
- Vitis launches：`23 / 200`，包含 source baseline 和所有运行尝试；
- seal/audit Provider：0；
- seal/audit Vitis：0；
- seal/audit git history mutations：0（Stage II 产品和 checkpoint 提交另行列出）。

## 证据和独立审计

不可变 campaign manifest、运行文件哈希和审计结果位于服务器：

```text
/data/agrefactor_runs/inheritance_stage2_checkpoint_v2/
```

关键摘要：

- `campaign_manifest.json` SHA-256：`d29293efb7b12674fd35027a61994644af748ecd9b5dacc11766787d6b1caf23`；
- `audit_result.json` SHA-256：`bd2c1b3f6e8e0e5f7c9227398f87da2fe2e473192d1a81b484ff1bd09600cbaf`；
- independent audit：`passed`；critical findings：0；
- audit 只读取 protocol、manifest、baseline、成功和失败运行的安全 artifact，没有 Provider/Vitis 调用；
- audit 验证了四个 source 未变、protocol/cohort/public test hashes、public-only boundary、
  formal validation 状态、Candidate 变化、toolchain identity、privacy flags、预算计数和历史未变。

`checkpoint_v1` 的审计失败结果也保留，原因是审计器初版错误读取 preflight schema；修复后的
审计器从原始两次运行数组重新计算稳定性和 mismatch，不放宽验证条件。

最终定向回归分两组执行，共 137 项通过、0 项失败。覆盖 source-only 产品入口、optimizer
边界、test generation exhaustion、public testbench repair routing、repair budget、Stage II
protocol/checkpoint、生成 profile、test source provenance、generation output accounting 和 source
boundary；回归本身未调用 Provider 或 Vitis。

## 提交和下一步

本 checkpoint 的产品/合同变更提交为：

- `de701e1`：修复 ordinary refactor 对 Hidden optimize handoff 的错误依赖；
- `0f3ea96`：加入 Stage II evidence seal/audit contract；
- `e231fb8`：修正 preflight evidence 的独立重算审计。

阶段 II 到此结束。下一步是项目所有者审阅该 checkpoint 后再决定是否进入阶段 III；本次执行
不进入阶段 III，也不启动 R2-R4、经验系统或外部数据集。
