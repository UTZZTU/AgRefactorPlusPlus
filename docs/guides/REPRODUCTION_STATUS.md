# 当前复现与验证状态

本文只描述当前产品入口和当前证据。原始 AgRefactor/Legacy 复现历史见
[LEGACY_BASELINE_STATUS.md](LEGACY_BASELINE_STATUS.md)。确定性测试、单次真实工具
smoke 和单次真实 provider smoke 的证据边界必须分开解释。

## 当前权威基线

```text
branch=stage2-general-feedback
behavior_commit_p4_0e=eabb2b7e7f5123f3e3f90fe6b6aa0f4a16c6c4a7
network_evidence_closure_commit=81804dff2c846b4f79d636cc412fca5b33eca8eb
latest_deterministic_regression=2108/2108
stage4_allowed=false
next_implementation_package=P4-0F
```

P4-0E authority-state synchronization提交本身以当前分支 HEAD 为准，不在本文复制会因自身提交立即变化的 SHA。

## 当前验证环境

| 项目 | 已验证环境 |
|---|---|
| 操作系统 | Ubuntu 22.04 LTS |
| Python | 3.10 |
| HLS 工具 | Vitis HLS 2023.2 |
| 网络模型 | `deepseek-v4-flash`，OpenAI-compatible endpoint |
| 默认模型 endpoint | `https://api.deepseek.com` |
| 默认 credential env | `DEEPSEEK_API_KEY` |
| 默认 TargetProfile | `vitis-2023.2-default` |
| 代表性器件 | `xcu200-fsgd2104-2-e` |

## 1. 当前普通产品入口

```bash
python -m agrefactor.cli refactor SOURCE --top TOP
python -m agrefactor.cli optimize CANDIDATE --top TOP \
  --reference-source ORIGINAL \
  --public-test PUBLIC.cpp --hidden-test HIDDEN.cpp
python -m agrefactor.cli full SOURCE --top TOP
```

当前已实现：

- source-only Refactor、direct Optimize 和 Full handoff；
- 默认 `deepseek-v4-flash`，用户 model/family/endpoint/API-key-env 覆盖；
- 调用 CWD `.env`，`override=False`，typed missing-credential gate；
- role-specific `auto` reasoning 和显式 DeepSeek Thinking；
- secret、`.env` 内容和 private reasoning suppression；
- typed Preflight ownership；
- bounded Optimize Candidate recovery；
- `Preflight → Public native Vitis CSIM → CSYNTH → Public RTL COSIM → Hidden`；
- hard LLM/tool/compile/CSIM/CSYNTH/COSIM/wall-time 预算；
- `safe-v1`、checkpoint、rollback、cache 和 `best_correct`；
- Execution Identity、safe trace 和持久 artifacts。

## 2. 确定性回归

```text
full_unittest=2108/2108
failures=0
errors=0
skipped=0
```

其中 P4-0E-R1 新增 4 项 focused tests，证明 committed network smoke 的
repository identity、shared `BudgetManager`、prelaunch check、exact-once LLM
accounting、artifact identity 和安全持久化合同。确定性测试不等同于真实网络、真实
Vitis 或任意 kernel 证据。

## 3. 最新真实 Vitis 资格链

```text
run_id=p4_0d_public_rtl_cosim_v16_20260804T064831Z_1709639
commit=b543604cd311eab4380987b09447842542e3214b
status=accepted_real_vitis
preflight=passed
public_native_vitis_csim=passed
csynth=passed
public_rtl_cosim=passed
hidden=passed
actual_vitis_version=2023.2
network_llm_used=false
full_regression=2096/2096
```

该运行证明 P4-0D 固定样例上的真实五阶段 Vitis/Hidden 链、COSIM timeout、
ownership、cache identity 和硬预算。它不是网络模型或多 kernel 稳定性证据。

## 4. P4-0E 真实网络模型证据

### 4.1 Model runtime smoke

```text
run_id=p4_0e_model_runtime_v9_20260804T123830Z_3215756
artifact_root=/data/agrefactor_runs/p4_0e_model_runtime_v9_20260804T123830Z_3215756
behavior_commit=eabb2b7e7f5123f3e3f90fe6b6aa0f4a16c6c4a7
focused=8/8
full=2104/2104
model=deepseek-v4-flash
thinking=true
provider_effort=max
network_smoke=passed
secret_values_persisted=false
private_reasoning_persisted=false
```

### 4.2 Master-contract network evidence closure

```text
run_id=p4_0e_r1_network_evidence_v2_20260804T141054Z_3612651
artifact_root=/data/agrefactor_runs/p4_0e_r1_network_evidence_v2_20260804T141054Z_3612651
commit=81804dff2c846b4f79d636cc412fca5b33eca8eb
focused=4/4
full=2108/2108
shared_budget_manager=true
max_llm_calls=1
physical_provider_calls=1
exact_once_llm_accounting=true
artifact_identity_sha256=db6d4996d71ba2a6bfe99beb804f7ad1826684ff2b737220933f17061e2b7c2d
artifact_file_sha256=0211f48cb908bf3bb76ec3edd3c7465828b320fb4da211debd7e0c08e40d31c3
secret_values_persisted=false
dotenv_contents_persisted=false
private_reasoning_persisted=false
hidden_exposed_to_model=false
```

该 smoke 证明默认模型 transport、`.env`/credential、Thinking/reasoning、安全
metadata、共享硬 LLM 预算和精确 commit/artifact identity。它不证明生成代码正确、
任意 kernel 成功、稳定模型质量、优化成功率或 PPA superiority。

## 5. Pre-Stage-4 当前状态

```text
P4_0A_DOCUMENTATION_CONTRACT=accepted
P4_0B_TYPED_PREFLIGHT=accepted
P4_0B_R_BOUNDED_OPTIMIZE_RECOVERY=accepted
P4_0C_PUBLIC_NATIVE_VITIS_CSIM=accepted_real_vitis
P4_0D_PUBLIC_RTL_COSIM=accepted_real_vitis
P4_0E_MODEL_RUNTIME=accepted_real_network
P4_0E_R1_NETWORK_EVIDENCE_CLOSURE=accepted
PRE_STAGE4_HARDENING_IMPLEMENTATION_COMPLETE=false
NEXT_IMPLEMENTATION_PACKAGE=P4-0F
STAGE4_ALLOWED=false
```

P4-0F 仍需基于真实端到端测量决定 mode-specific defaults 和 Full Optimize
reserves，并校准每个命令的 truthful CLI surface。P4-0G、P4-0H 和 P4-0I 仍未完成。

## 6. 不能外推的结论

- 单个 committed sample 不代表任意 kernel；
- Vitis 2023.2 单环境不代表任意版本或器件；
- 单次真实 provider 成功不代表稳定模型质量；
- 2108 个确定性测试不代表 2108 个真实 kernel；
- `safe-v1` 不等于计划中的 `dynamic-v1`；
- 当前统一 source-run budget 不等于已验证的 mode-specific budgets；
- Legacy RAG 不等于 Stage 4 Memory Applicability Gate；
- 单次 PPA 改善不等于稳定优化收益；
- 当前证据不关闭 Stage 4 entry criteria。
