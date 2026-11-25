# NEXT_SESSION_HANDOFF

## 当前正式基线
- Repository: `wei670025200-boop/-`
- Production branch: `main`
- Pre-document-closeout main: `f2227559e771222a937d4d909f0dc929f0a74804`
- DLT V5 production state: `UNLOCKED`
- Real OOS Run: `45608321723` — CONSUMED, DO NOT RERUN
- History: 696 rows, through `26134 / 2026-11-23`
- History/prefix SHA-256: `35d0b8ffc906e435743aef9ef1a1069625cedd52380c013bfb50c421a4aa21bb`
- Policy: Candidate 0, front 1%–5%, back K=18
- Validated ticket scope: 1–18
- Scope overflow reason: `POLICY_SCOPE_EXCEEDED_FALLBACK_V4`
- Integrity summary: `58a8273667123a5f9bdc0efb699d9d4c8ec1a64b584defb7d8404eecd78ad6d1`

## 不可触碰
1. 不重跑 Run 45608321723。
2. 不改 OOS 参数、预注册、accepted twin。
3. 不改写 26134 及以前的 `data/dlt_history.csv`。
4. 不改 `data/dlt_real_oos_once_claim.json`。
5. 不把 N>18 强行路由到 V5。
6. 不重新引入 bootstrap/probe 网络工具。
7. 不修改 SSQ 或任何非 DLT 隔离模块。

## 核心文件
- `run_dlt_v5.py`：生产 CLI。
- `src/dlt_v5/production_v5.py`：Phase 4 凭证、四锁 validator、Candidate 0 factory。
- `src/dlt_v5/runner_v5.py`：四锁和 V5/V4 路由。
- `src/dlt_v5/engine_v4.py`：统一原子事务壳。
- `src/dlt_v5/prereg_core.py`：特征、硬排除、策略网格、超参。
- `src/dlt_v5/frozen_protocol.py`：节日脉冲、OOS 门、完整性规则。
- `src/dlt_v5/history_contract.py`：新旧奖级映射与双边界。
- `src/dlt_v5/training_prefix.py`：训练前缀锁。
- `data/dlt_v5_production_artifact.json`：生产授权凭证。
- `data/dlt_history.csv`：历史账本。
- `data/dlt_real_oos_once_claim.json`：一次性 OOS durable claim。
- `data/dlt_local_schedule_manifest.json`：离线开奖日历。

## 已验证基线
在代码 blob 与 main 完全一致的本地隔离副本上：
- `python -m unittest discover -s tests -p 'test_dlt*.py' -v` -> `Ran 169 tests`, `OK`
- `python audits/dlt_static_network_scan.py` -> 35 files, hits `[]`, pass true
- N=5 dry-run -> 四锁 PASS，`DLT_V5`，10 元，反身性怪号 0
- N=20 dry-run -> `DLT_V4_BALANCED_COVERAGE_FALLBACK`，原因 `POLICY_SCOPE_EXCEEDED_FALLBACK_V4`，40 元

## 下一期维护
开奖后只允许追加官方 22 列历史行。追加后先运行 history validation、169+ regression、zero-network scan，再出下一期。
2027 年前必须补 `data/dlt_local_schedule_manifest.json` 的官方休市表；未登记年份必须 Fail-Closed。
