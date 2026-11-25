# DLT V5 日常操作手册

## 1. 出号
在仓库根目录设置 `PYTHONPATH=src:.`。

基本投注（2 元/注）：
```powershell
$env:PYTHONPATH = "src;."
python run_dlt_v5.py --repo-root . --root .\dlt-production-ledger --issue <期号> --date <YYYY-MM-DD> --tickets 5
```

追加投注（3 元/注）：
```powershell
python run_dlt_v5.py --repo-root . --root .\dlt-production-ledger --issue <期号> --date <YYYY-MM-DD> --tickets 5 --additional
```

演练：
```powershell
python run_dlt_v5.py --repo-root . --root .\dlt-production-ledger --issue <期号> --date <YYYY-MM-DD> --tickets 5 --dry-run
```

`--dry-run` 执行同一事务阶段，但使用隔离临时命名空间，`production_commit=false`。

## 2. 路由判定
- N=1..18 且四锁 PASS -> `DLT_V5`。
- N>18 -> `DLT_V4_BALANCED_COVERAGE_FALLBACK`，原因 `POLICY_SCOPE_EXCEEDED_FALLBACK_V4`。
- DATA/OOS/DRIFT/POLICY 任一非 PASS -> V4 fallback。
- 不允许人工强行把 fallback 改回 V5。

## 3. 开奖后追加历史
文件：`data/dlt_history.csv`。
只能在文件末尾追加，不得重排、改写、删除既有 696 行及表头。

22 列固定格式：
```text
issue,date,f1,f2,f3,f4,f5,b1,b2,sales_rmb,pool_after_rmb,g1,g1_add,g2,g2_add,g3,g4,g5,g6,g7,rule_epoch,promo_active
```

2026 七奖级示例格式：
```text
26135,2026-11-25,01,02,03,04,05,01,02,300000000,800000000,1,0,100,20,1000,15000,60000,700000,7000000,7TIER_2026,0
```
示例只展示列格式，不代表真实开奖数据；实际值必须来自官方结果。

追加后核验：
```powershell
$env:PYTHONPATH = "src;."
python validate_dlt_history_export.py
python -m unittest discover -s tests -p "test_dlt*.py" -v
python audits/dlt_static_network_scan.py
```

训练前缀锁绑定到 26134。合法追加 26135+ 不应改变：
`35d0b8ffc906e435743aef9ef1a1069625cedd52380c013bfb50c421a4aa21bb`。
若历史前缀被改写，`TRAINING_PREFIX_MISMATCH` 必须 Fail-Closed。

## 4. CUSUM
- 基线 mu0=0.14129187480618388
- sigma0=0.09946808483447686
- k=0.5
- h=5.0
- window=20

每次新增合法历史行后，`production_v5.py` 会用冻结 twin 计算 26134 之后的新残差并更新 CUSUM。
若 `DRIFT_GATE=TRIPPED`，V5 自动回落 V4。
禁止通过重跑 OOS、重新拟合或手工重置参考值来解除熔断。

## 5. 2027 日历
文件：`data/dlt_local_schedule_manifest.json`。
当前登记到 2026；未登记年份由 `unregistered_year=FAIL_CLOSED` 保护。

2027 官方休市公告发布后：
1. 查官方财政/体彩休市公告。
2. 在 `years` 中加入 `"2027"`，填写官方 closures 区间及 source。
3. 保持 `draw_weekdays_python=[0,2,5]`，除非官方规则另有正式变更。
4. 运行所有 DLT 测试和静态扫描。
5. 用 2027 第一合法开奖日做 `--dry-run`。
6. 未补录前不得绕过 `SCHEDULE_YEAR_UNREGISTERED`。

## 6. 故障处理
- `REFUSE_DUPLICATE_ISSUE`：同一期已写入账本，禁止重复提交。
- `ISSUE_NOT_NEXT`：账本期号/日期不连续，先核对日历和上期。
- `PENDING_RECOVERY_REQUIRED`：先执行既有恢复路径，不新抽 seed。
- `SCHEDULE_YEAR_UNREGISTERED`：补官方年度休市表。
- `TRAINING_PREFIX_MISMATCH`：历史前缀被改写，停止生产并审计。
- `POLICY_SCOPE_EXCEEDED_FALLBACK_V4`：正常作用域回落，不是错误。
