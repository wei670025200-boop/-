# DLT V5 真实 OOS 结论与决策日志

数据来源：已存在的 GitHub Actions Run `45608321723` Artifact `dlt-approved-17500-stage2-evidence`。本文件仅整理既有结果，未重跑 OOS。

## 1. 一次性执行状态
- Run ID: `45608321723`
- Run attempt: `1`
- source commit: `fddf4ed817e5225213dc37ca2145a0f7196a3c02`
- execution marker: `REAL_OOS_EXECUTION_CONSUMED=TRUE`
- 结论：真实 OOS 机会已永久消耗，禁止二次执行。

## 2. 数据门
- rows: 696
- start: `22041 / 2022-04-16`
- end: `26134 / 2026-11-23`
- hard_gate_650: PASS
- buffer_780: BELOW_TARGET
- CSV SHA-256: `35d0b8ffc906e435743aef9ef1a1069625cedd52380c013bfb50c421a4aa21bb`
- DATA_GATE: PASS

## 3. OOS 诊断
前区：
- n=495
- baseline_mse=0.044873159170481715
- model_mse=0.03354244851660253
- relative_improvement=0.25250530302160473
- 95% MBB CI=[0.19966679774619556, 0.30761699340411863]
- block=12, B=5000, seed=2026112501

后区：
- n=495
- baseline_mse=0.025052946982406836
- model_mse=0.016739194338552307
- relative_improvement=0.33184729324229895
- 95% MBB CI=[0.2972646182189663, 0.3670092897780582]
- block=12, B=5000, seed=2026112502

综合：
- relative_improvement=0.29217629813195184
- 95% MBB CI=[0.2641888185208186, 0.3214561302271303]
- block=12, B=5000, seed=2026112503
- OOS_GATE=PASS

后区 Spearman 仅作诊断：
- rho=0.6251407901662571
- p=5.0245641750459314e-55

## 4. Policy
- primary objective: `ANTI_SHARING_CROWDING_SUPPRESSION`
- outer front_hits=19
- outer back_hits=115
- score_front=0.18031560787333187
- score_back=0.11874168371029259
- primary=0.14952864579181224
- 95% MBB CI=[0.10932377933535167, 0.18266533113654743]
- valid_resample_fraction=0.9982
- B=5000, block=12, seed=2026112504
- POLICY_GATE=PASS
- sparse_capture_lift: front=1.0674157303370786, back=1.0675105485232068（诊断，不是决策门）

最终研究参考候选：
- candidate=0
- front_band=[0.01,0.05]
- back_k=18
- full-trace score primary=0.15755459571101907
- selection counts: candidate0=355, candidate3=40，其余=0

## 5. CUSUM
冻结参考：
- mu0=0.14129187480618388
- sigma0=0.09946808483447686
- reference_rows=100
- reference_through_issue=24041
- k=0.5
- h=5.0
- window=20

真实 OOS 末值：
- last_statistic=2.2310023100107057
- DRIFT_GATE=PASS

## 6. 四门最终结果
- DATA_GATE=PASS
- OOS_GATE=PASS
- DRIFT_GATE=PASS
- POLICY_GATE=PASS

原始研究报告仍保留：
- `phase4_status=LOCKED`
- `prediction_advantage_verified=false`
- `profit_guarantee=NONE`

这三个字段不得被事后改写。生产授权通过独立的 `data/dlt_v5_production_artifact.json` 实现，其 `phase4_status=UNLOCKED`。

## 7. 完整性
- history_prefix_sha256=`35d0b8ffc906e435743aef9ef1a1069625cedd52380c013bfb50c421a4aa21bb`
- twin_artifact_sha256=`86aaabe6ac82ac135f25eb3d3b5770f428a1a1b964f307569b8f3545cd2f27c8`
- statutory_rule_sha256=`c459e89a1b1f59e67300a10f7f6a48d3b9a149d08a2ed75c61420d25768f395c`
- prereg_spec_sha256=`38b04284be97335870908ba4e165ccd712e709667f395fcf99c853d3cb60aff5`
- candidate_grid_sha256=`4581d1c0d1d72bc2f971b5858ef913bac5451724977c5016f1c3719df232e652`
- oos_trace_sha256=`7eee9656f97d04e1f2155d5247810b6ceaf2e7cea970eb27136f741a84f77a13`
- integrity_summary_sha256=`58a8273667123a5f9bdc0efb699d9d4c8ec1a64b584defb7d8404eecd78ad6d1`
- digital_signature=false

## 8. 决策日志
1. 接受 696 期洁净尾段，因为其满足硬门 650；780 仅为缓冲目标。
2. 接受 Candidate 0 为 Phase 4 生产策略：前区 1%–5%，后区 K=18。
3. 生产作用域仅授权 1–18 注；N>18 不外推研究结论，必须回落 V4。
4. CUSUM 任一时点窗口触及 h=5.0 时生产 V5 自动失效并回落 V4。
5. 不把 OOS 改善解释为“预测开奖号码优势”或“盈利保证”。
6. Run 45608321723 不得重跑；后续只能追加新期开奖数据并进行已冻结模型的漂移监控。
