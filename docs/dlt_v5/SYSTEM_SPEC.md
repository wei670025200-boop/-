# 大乐透 DLT V5 生产系统总规范

基线：`main / f2227559e771222a937d4d909f0dc929f0a74804`（本文件由该基线补档，不改生产代码或冻结数据）。

## 1. 生产边界
- 生产授权层：`PHASE4_STATUS = UNLOCKED`。
- 原始 OOS twin 保持 `phase4_status = LOCKED`，不得改写。
- 一次性真实 OOS：Run `45608321723`，已消耗，永久禁止重跑。
- 训练前缀：696 期，`22041 / 2022-04-16` 至 `26134 / 2026-11-23`。
- 训练前缀 SHA-256：`35d0b8ffc906e435743aef9ef1a1069625cedd52380c013bfb50c421a4aa21bb`。
- V5 验证作用域：`1 <= ticket_count <= 18`；超出作用域统一回落 V4。

## 2. 法定规则与赔付
实现：`src/dlt_v5/statutory_liability.py`；配置：`data/dlt_statutory_rule.json`。
- 2026-02-02 / 26014 起使用七奖级。
- 奖池快照阈值 8 亿元，无滞回；低于阈值固定奖为 5000/300/150/15/5，达到或超过阈值为 6666/380/200/18/7。
- 一等奖、二等奖各自聚合封顶 1 亿元。
- 基本投注 2 元；追加投注 3 元；追加奖金按基本一等奖/二等奖的 80% 计算。
- `promo_balance_remaining_rmb` 只截断额外派奖部分，不能截断基础法定赔付。
- 负数人民币、负数派奖余额、NaN/Inf 均硬拒绝。
- `profit_guarantee = NONE`。

## 3. 数学与覆盖核心
- `src/dlt_v5/combinadic.py`：5/35 组合排名、反排名、重叠壳层精确计数。
- `src/dlt_v5/balanced_coverage.py`：
  - HMAC-SHA256 DomainDRBG。
  - 拒绝采样 `randbelow`，避免取模偏差。
  - 前区全局 `S_35` 置换，`N>7` 使用七注分块模板并最小化交叠。
  - 后区 12 球一因子分解形成 66 对完整循环。
  - `EXACT_ENUMERATED_BATCH_COVERAGE` 对前区 324,632 个结果与后区 66 对进行精确联合覆盖枚举。
  - 仅声明覆盖率，不声明盈利；`profit_guarantee = NONE`。

## 4. V5 特征与冻结模型
实现：`src/dlt_v5/prereg_core.py`、`digital_twin.py`、`prequential_rank.py`、`frozen_protocol.py`。
- 前区 12 维特征：
  `all_birthday_le31, month_cluster_excess, lucky_tail_ratio, taboo_tail_4_ratio, mult5_ratio, shrink_default_box, calendar_pulse_hits, prev_repeat_hits, prev_neighbor_hits, omit_chase_hits, adj_pair_ratio, same_tail_pair_ratio`。
- 公历节日脉冲：固定节日日期前后 ±3 天，合并开奖月/日后去重。
- 前区 5 条硬排除：连续相邻对>=3、等差数列、全同奇偶、极端半区、32–35 高位过载。
- 后区 9 维特征 + 12 球 EB 偏置。
- Ridge：前区 L2 alpha 15.0，后区 L2 alpha 12.0；响应 Winsor 2%/98%；EB prior strength 20。
- Candidate 0：前区 eligible 组合模型分数升序的 1%–5% 分位带；后区 K=18。
- 前区 eligible 数固定为 292,887；平分按字典序打破。
- 后区 66 对按模型分数升序并以组合字典序打破平分。

## 5. 数据契约
实现：`src/dlt_v5/history_contract.py`、`history_validation.py`、`training_prefix.py`。
- 历史 CSV 固定 22 列：
  `issue,date,f1,f2,f3,f4,f5,b1,b2,sales_rmb,pool_after_rmb,g1,g1_add,g2,g2_add,g3,g4,g5,g6,g7,rule_epoch,promo_active`。
- 旧九奖级映射：g3=t3+t4，g4=t5，g5=t6+t7，g6=t8，g7=t9。
- 新七奖级映射：g3..g7=t3..t7。
- 日期边界 `2026-02-02` 与期号边界 `26014` 必须同时一致，否则 `RULE_EPOCH_CONFLICT`。
- 训练前缀哈希只绑定到 `26134`；在末尾合法追加新期不会改变训练前缀摘要，修改既有前缀会失败。

## 6. 四锁、路由与作用域
实现：`src/dlt_v5/runner_v5.py`、`production_v5.py`。
四锁按固定顺序独立计算：
1. `DATA_GATE`
2. `OOS_GATE`
3. `DRIFT_GATE`
4. `POLICY_GATE`

生产路由：
- `1..18` 注且四锁全为 PASS -> `DLT_V5`。
- 任一锁 FAIL/TRIPPED -> `DLT_V4_BALANCED_COVERAGE_FALLBACK`。
- `ticket_count > 18` -> `DLT_V4_BALANCED_COVERAGE_FALLBACK`，原因码 `POLICY_SCOPE_EXCEEDED_FALLBACK_V4`。
- 任何回落仍通过同一个 `DLTV4Engine.execute()` 事务壳。

## 7. 统一事务壳
实现：`src/dlt_v5/engine_v4.py`。
唯一生产入口为 `DLTV4Engine.execute()`，不存在独立 `run_local()` 旁路。
事务阶段：
`STAGE -> HASH VERIFY -> LEDGER COMMIT -> ATOMIC PUBLISH`。

安全特性：
- 本地互斥锁。
- 同期防重。
- 账本哈希链。
- Ed25519 commitment 签名。
- 私钥和归档 seed 在 POSIX 下要求 0600。
- `recover_staged_publish()` 可从四阶段注入崩溃后恢复，且不重抽 seed。
- `--dry-run` 在隔离临时目录执行完整事务阶段，不污染生产账本。
- 2027 未登记日历时 `SCHEDULE_YEAR_UNREGISTERED` Fail-Closed。

## 8. Phase 4 冻结凭证
文件：`data/dlt_v5_production_artifact.json`。
- approved_run_id: `45608321723`
- trained_through_issue: `26134`
- Candidate 0: front 1%–5%, back K=18
- CUSUM: mu0=0.14129187480618388, sigma0=0.09946808483447686, k=0.5, h=5.0, window=20
- 完整性总摘要：`58a8273667123a5f9bdc0efb699d9d4c8ec1a64b584defb7d8404eecd78ad6d1`
- `digital_signature: false`（六项摘要是 SHA-256 完整性摘要，不是外部数字签名）。

六项 SHA-256：
- history_prefix: `35d0b8ffc906e435743aef9ef1a1069625cedd52380c013bfb50c421a4aa21bb`
- twin_artifact: `86aaabe6ac82ac135f25eb3d3b5770f428a1a1b964f307569b8f3545cd2f27c8`
- statutory_rule: `c459e89a1b1f59e67300a10f7f6a48d3b9a149d08a2ed75c61420d25768f395c`
- prereg_spec: `38b04284be97335870908ba4e165ccd712e709667f395fcf99c853d3cb60aff5`
- candidate_grid: `4581d1c0d1d72bc2f971b5858ef913bac5451724977c5016f1c3719df232e652`
- oos_trace: `7eee9656f97d04e1f2155d5247810b6ceaf2e7cea970eb27136f741a84f77a13`

## 9. Local-First
- 当前生产 Python 表面无联网依赖。
- 临时 `tools/bootstrap_once/`、一次性建库/探针 Workflow 已从 main 删除。
- 任何未来版本不得把数据抓取重新放回生产路径。
