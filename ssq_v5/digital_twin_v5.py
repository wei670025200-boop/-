"""
digital_twin_v5.py
Phase 3: 全国投注盘面数字孪生（向量化 1,107,568 全空间特征 + 严格样本外全奖级校准）
依赖：numpy
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, FrozenSet, List, Sequence, Tuple

import numpy as np

from db_and_sentiment_v5 import (
    HistoricalDrawRecord,
    VerifiedSentimentSnapshot,
    extract_calendar_pulse_numbers,
)

TOTAL_RED_COMBINATIONS = math.comb(33, 6)  # 1,107,568

# 双色球官方严格理论概率
P_TIER4 = (math.comb(6, 5) * math.comb(27, 1) * 15 + math.comb(6, 4) * math.comb(27, 2) * 1) / (
    TOTAL_RED_COMBINATIONS * 16
)
P_TIER5 = (math.comb(6, 4) * math.comb(27, 2) * 15 + math.comb(6, 3) * math.comb(27, 3) * 1) / (
    TOTAL_RED_COMBINATIONS * 16
)
# 命中蓝球且红球 <= 2 个才落入六等奖（红球 >= 3 个升级为五等奖及以上）
P_RED_LE_2 = (
    math.comb(6, 0) * math.comb(27, 6)
    + math.comb(6, 1) * math.comb(27, 5)
    + math.comb(6, 2) * math.comb(27, 4)
) / TOTAL_RED_COMBINATIONS
P_TIER6 = (1.0 / 16.0) * P_RED_LE_2  # ≈ 0.0588925466

# 特征名称冻结列表（顺序即参数向量顺序，严禁运行期暗改）
RED_FEATURE_NAMES: Tuple[str, ...] = (
    "all_birthday_le31",      # 散户层：6红全在01-31
    "month_cluster_excess",   # 散户层：01-12超额数量
    "lucky_tail_ratio",       # 散户层：尾数6/8/9占比
    "shrink_default_box",     # 工业化缩水层：AC/和值/跨度/奇偶/三区标准框交集
    "calendar_pulse_hits",    # 日历层：当日公历/节假日命中数
    "prev_repeat_hits",       # 走势磁铁：上期重号命中数
    "prev_neighbor_hits",     # 走势磁铁：上期±1斜连邻号命中数
    "omit_chase_hits",        # 走势磁铁：散户追冷拐点(遗漏12-20或Top2极冷)命中数
    "duplex_pair_coupling",   # 复式团块层：连号对+同尾对+重号对+双胆耦合度
    "sentiment_hot_hits",     # 实时舆情层：共识热胆命中数
    "sentiment_kill_hits",    # 实时舆情层：共识绝杀命中数
)

BLUE_FEATURE_NAMES: Tuple[str, ...] = (
    "blue_is_birthday_le12",  # 静态偏好：01-12月份号
    "blue_is_lucky_6_8_9",    # 静态偏好：06/08/09/16吉利尾
    "blue_prev_repeat",       # 动态效应：上期重蓝
    "blue_omit_chase_zone",   # 动态效应：遗漏>=20或Top2极冷追冷蓝
    "blue_parity_flip",       # 动态效应：与上期奇偶交替
    "blue_sentiment_hot",     # 实时舆情：共识热蓝
    "blue_sentiment_kill",    # 实时舆情：共识绝杀蓝
)


def generate_all_red_combinations_1based() -> np.ndarray:
    """生成全部 1,107,568 组有序红球组合，shape=(1107568, 6), dtype=uint8，值域 1..33。"""
    arr = np.fromiter(
        itertools.chain.from_iterable(itertools.combinations(range(1, 34), 6)),
        dtype=np.uint8,
        count=TOTAL_RED_COMBINATIONS * 6,
    )
    return arr.reshape(TOTAL_RED_COMBINATIONS, 6)


@dataclass(frozen=True)
class DynamicDrawContext:
    """严格只由开奖前 (< t) 已知信息构造的当期上下文，杜绝任何未来泄露。"""
    target_issue: str
    draw_date_bjt: str
    prev_reds: Tuple[int, ...]
    prev_blue: int
    red_omissions: Tuple[int, ...]   # 长度 33，索引 0 对应红球 01
    blue_omissions: Tuple[int, ...]  # 长度 16，索引 0 对应蓝球 01
    calendar_pulses: FrozenSet[int]
    hot_reds: FrozenSet[int]
    kill_reds: FrozenSet[int]
    hot_blues: FrozenSet[int]
    kill_blues: FrozenSet[int]
    double_dan_pairs: FrozenSet[Tuple[int, int]]
    historical_jackpot_blacklist: FrozenSet[Tuple[int, int, int, int, int, int]]


def build_causal_context_from_history(
    history_before_t: Sequence[HistoricalDrawRecord],
    target_issue: str,
    draw_date_bjt: str,
    sentiment: VerifiedSentimentSnapshot | None = None,
    jackpot_blowout_threshold: int = 30,
) -> DynamicDrawContext:
    """严格仅消费 t 期以前的历史开奖记录构造第 t 期动态特征上下文。"""
    if not history_before_t:
        raise ValueError("history_before_t must be non-empty.")

    red_omit = [len(history_before_t)] * 33
    blue_omit = [len(history_before_t)] * 16
    seen_r = set()
    seen_b = set()

    for lag, rec in enumerate(reversed(history_before_t)):
        for r in rec.reds:
            if r not in seen_r:
                red_omit[r - 1] = lag
                seen_r.add(r)
        if rec.blue not in seen_b:
            blue_omit[rec.blue - 1] = lag
            seen_b.add(rec.blue)
        if len(seen_r) == 33 and len(seen_b) == 16:
            break

    last = history_before_t[-1]
    # 历史上单期头奖井喷 >= 阈值注的明星形态进入永久高拥挤守号黑名单
    blacklist = frozenset(
        rec.reds for rec in history_before_t if rec.tier1_count >= jackpot_blowout_threshold
    )

    return DynamicDrawContext(
        target_issue=target_issue,
        draw_date_bjt=draw_date_bjt,
        prev_reds=last.reds,
        prev_blue=last.blue,
        red_omissions=tuple(red_omit),
        blue_omissions=tuple(blue_omit),
        calendar_pulses=extract_calendar_pulse_numbers(draw_date_bjt),
        hot_reds=sentiment.consensus_hot_reds if sentiment else frozenset(),
        kill_reds=sentiment.consensus_kill_reds if sentiment else frozenset(),
        hot_blues=sentiment.consensus_hot_blues if sentiment else frozenset(),
        kill_blues=sentiment.consensus_kill_blues if sentiment else frozenset(),
        double_dan_pairs=sentiment.consensus_double_dan_pairs if sentiment else frozenset(),
        historical_jackpot_blacklist=blacklist,
    )


def extract_red_features_matrix(
    combos_1based: np.ndarray,
    ctx: DynamicDrawContext,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    向量化提取任意批量红球组合 (M, 6) 的 11 维筹码特征矩阵 + 反身性怪号保护掩码。
    返回:
      X: shape=(M, 11), float64
      weird_reflexive_mask: shape=(M,), bool (True 表示极端猎奇/反生日过载/历史明星黑名单，禁止落入稀疏池)
    """
    c = combos_1based.astype(np.int16, copy=False)
    m = c.shape[0]
    x = np.zeros((m, len(RED_FEATURE_NAMES)), dtype=np.float64)

    # 1. 静态散户偏好
    x[:, 0] = (c[:, 5] <= 31).astype(np.float64)
    month_cnt = np.sum(c <= 12, axis=1)
    x[:, 1] = np.maximum(0, month_cnt - 2).astype(np.float64) / 4.0

    tails = c % 10
    lucky_cnt = np.sum((tails == 6) | (tails == 8) | (tails == 9), axis=1)
    x[:, 2] = lucky_cnt.astype(np.float64) / 6.0

    # 2. 工业化缩水软件标准框交集（和值 90-120, 跨度 20-28, 奇偶 2:4~4:2, 三区不断区, 连号<=1）
    r_sum = np.sum(c, axis=1)
    span = c[:, 5] - c[:, 0]
    odd_cnt = np.sum((c & 1) == 1, axis=1)
    z1 = np.sum(c <= 11, axis=1)
    z2 = np.sum((c >= 12) & (c <= 22), axis=1)
    z3 = np.sum(c >= 23, axis=1)
    diffs = np.diff(c, axis=1)
    adj_pairs = np.sum(diffs == 1, axis=1)

    in_shrink_box = (
        (r_sum >= 90) & (r_sum <= 120)
        & (span >= 20) & (span <= 28)
        & (odd_cnt >= 2) & (odd_cnt <= 4)
        & (z1 >= 1) & (z2 >= 1) & (z3 >= 1)
        & (adj_pairs <= 1)
    )
    x[:, 3] = in_shrink_box.astype(np.float64)

    # 3. 当日日历脉冲、走势磁铁与遗漏追冷查找表 (长度 34, 索引 1..33)
    cal_lut = np.zeros(34, dtype=np.float64)
    for n in ctx.calendar_pulses:
        cal_lut[n] = 1.0

    prev_lut = np.zeros(34, dtype=np.float64)
    neigh_lut = np.zeros(34, dtype=np.float64)
    for n in ctx.prev_reds:
        prev_lut[n] = 1.0
        if n > 1:
            neigh_lut[n - 1] = 1.0
        if n < 33:
            neigh_lut[n + 1] = 1.0

    omit_arr = np.asarray(ctx.red_omissions, dtype=np.int32)
    top2_omit_thresh = int(np.sort(omit_arr)[-2])
    omit_chase_lut = np.zeros(34, dtype=np.float64)
    for idx0, om in enumerate(omit_arr):
        if (12 <= om <= 20) or (om >= top2_omit_thresh and om >= 8):
            omit_chase_lut[idx0 + 1] = 1.0

    hot_lut = np.zeros(34, dtype=np.float64)
    for n in ctx.hot_reds:
        hot_lut[n] = 1.0
    kill_lut = np.zeros(34, dtype=np.float64)
    for n in ctx.kill_reds:
        kill_lut[n] = 1.0

    x[:, 4] = np.sum(cal_lut[c], axis=1) / 6.0
    x[:, 5] = np.sum(prev_lut[c], axis=1) / 6.0
    x[:, 6] = np.sum(neigh_lut[c], axis=1) / 6.0
    x[:, 7] = np.sum(omit_chase_lut[c], axis=1) / 6.0

    # 4. 复式/胆拖二元团块耦合度（连号对 + 同尾对 + 重号对 + 舆情双胆对）
    # 计算每组6球中相同尾数的配对数
    same_tail_pairs = np.zeros(m, dtype=np.float64)
    for t_val in range(10):
        cnt_t = np.sum(tails == t_val, axis=1)
        same_tail_pairs += (cnt_t * (cnt_t - 1)) * 0.5

    prev_hits_raw = np.sum(prev_lut[c], axis=1)
    prev_pairs = prev_hits_raw * np.maximum(0.0, prev_hits_raw - 1.0) * 0.5

    dan_pair_hits = np.zeros(m, dtype=np.float64)
    if ctx.double_dan_pairs:
        for a, b in ctx.double_dan_pairs:
            has_a = np.any(c == a, axis=1)
            has_b = np.any(c == b, axis=1)
            dan_pair_hits += (has_a & has_b).astype(np.float64)

    x[:, 8] = (adj_pairs + 0.5 * same_tail_pairs + prev_pairs + 2.0 * dan_pair_hits) / 10.0
    x[:, 9] = np.sum(hot_lut[c], axis=1) / 6.0
    x[:, 10] = np.sum(kill_lut[c], axis=1) / 6.0

    # 5. 反身性怪号保护掩码（明确作为硬安全规则，防止线性模型把猎奇怪号误判为冷门）
    strict_ap = np.all(diffs == diffs[:, [0]], axis=1)
    all_same_parity = (odd_cnt == 0) | (odd_cnt == 6)
    extreme_half = (c[:, 5] <= 16) | (c[:, 0] >= 17)
    anti_birthday_overload = np.sum(c >= 30, axis=1) >= 3
    weird_mask = (adj_pairs >= 4) | strict_ap | all_same_parity | extreme_half | anti_birthday_overload

    if ctx.historical_jackpot_blacklist:
        for blk in ctx.historical_jackpot_blacklist:
            blk_arr = np.asarray(blk, dtype=np.int16)
            weird_mask |= np.all(c == blk_arr, axis=1)

    return x, weird_mask


def extract_blue_features_matrix(ctx: DynamicDrawContext) -> np.ndarray:
    """为 01..16 全部 16 个蓝球构造特征矩阵，shape=(16, 7), float64。"""
    b_nums = np.arange(1, 17, dtype=np.int16)
    xb = np.zeros((16, len(BLUE_FEATURE_NAMES)), dtype=np.float64)

    xb[:, 0] = (b_nums <= 12).astype(np.float64)
    xb[:, 1] = np.isin(b_nums, [6, 8, 9, 16]).astype(np.float64)
    xb[:, 2] = (b_nums == ctx.prev_blue).astype(np.float64)

    om = np.asarray(ctx.blue_omissions, dtype=np.int32)
    top2_b = int(np.sort(om)[-2])
    xb[:, 3] = ((om >= 20) | ((om >= top2_b) & (om >= 12))).astype(np.float64)
    xb[:, 4] = ((b_nums & 1) != (ctx.prev_blue & 1)).astype(np.float64)
    xb[:, 5] = np.isin(b_nums, list(ctx.hot_blues)).astype(np.float64)
    xb[:, 6] = np.isin(b_nums, list(ctx.kill_blues)).astype(np.float64)
    return xb


# ==============================================================================
# 稳健 L2 线性校准器（Winsorized Ridge Regression，零外部 ML 库依赖）
# ==============================================================================

def _fit_winsorized_ridge(
    x: np.ndarray,
    y: np.ndarray,
    l2_alpha: float = 10.0,
    winsor_pct: Tuple[float, float] = (2.0, 98.0),
) -> Tuple[float, np.ndarray]:
    """截尾抗倍投离群值的 L2 正则闭式回归，返回 (intercept, weights)。"""
    lo, hi = np.percentile(y, winsor_pct)
    y_clean = np.clip(y, lo, hi)
    n, d = x.shape
    x_mean = np.mean(x, axis=0)
    y_mean = float(np.mean(y_clean))
    xc = x - x_mean
    yc = y_clean - y_mean

    gram = xc.T @ xc + l2_alpha * np.eye(d, dtype=np.float64)
    rhs = xc.T @ yc
    w = np.linalg.solve(gram, rhs)
    intercept = y_mean - float(x_mean @ w)
    return intercept, w


@dataclass(frozen=True)
class CalibratedTwinParameters:
    model_version: str
    trained_through_issue: str
    training_draw_count: int
    red_intercept: float
    red_weights: Tuple[float, ...]
    blue_intercept: float
    blue_weights: Tuple[float, ...]
    blue_static_shrinkage: Tuple[float, ...]  # 16个蓝球的经验贝叶斯静态偏置(仅由训练集估计)
    oos_gate_status: str                      # "PASS" | "TWIN_OOS_GATE_FAILED" | "UNVERIFIED"
    oos_metrics: Dict[str, Any]
    artifact_sha256: str


def _compute_tier_targets(rec: HistoricalDrawRecord) -> Tuple[float, float]:
    """
    返回当期高信噪比对数拥挤度目标：
    - y_red: log(0.5 * R_t^(4) + 0.5 * R_t^(5))
    - y_blue: log(B_t^adj)，基于六等奖升级校正后的蓝球拥挤度比值
    """
    n_tkt = rec.sales_tickets
    r4 = max(rec.tier4_count, 1) / (n_tkt * P_TIER4)
    r5 = max(rec.tier5_count, 1) / (n_tkt * P_TIER5)
    y_red = math.log(0.5 * r4 + 0.5 * r5)

    b_adj = max(rec.tier6_count, 1) / (n_tkt * P_TIER6)
    y_blue = math.log(b_adj)
    return y_red, y_blue


def _moving_block_bootstrap_ci(
    deltas: np.ndarray,
    block_size: int = 12,
    n_boot: int = 2000,
    seed: int = 20261124,
) -> Tuple[float, float, float]:
    """12期移动块 Bootstrap 计算 95% 置信区间（固定确定性种子，便于审计复现）。"""
    n = len(deltas)
    rng = np.random.default_rng(seed)
    n_blocks = int(math.ceil(n / block_size))
    max_start = max(1, n - block_size + 1)
    means = np.empty(n_boot, dtype=np.float64)

    for b in range(n_boot):
        starts = rng.integers(0, max_start, size=n_blocks)
        sample = np.concatenate([deltas[s : s + block_size] for s in starts])[:n]
        means[b] = float(np.mean(sample))

    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(np.mean(deltas)), float(lo), float(hi)


def run_expanding_window_oos_calibration(
    records: Sequence[HistoricalDrawRecord],
    initial_train_size: int = 200,
    l2_alpha_red: float = 15.0,
    l2_alpha_blue: float = 12.0,
    blue_eb_prior_strength: float = 20.0,
    min_oos_draws: int = 300,
) -> Tuple[CalibratedTwinParameters, List[Dict[str, Any]]]:
    """
    Phase 3 严格逐期扩展窗口样本外验证（零未来泄露）：
    - 每期 t 仅用 records[:t] 训练权重与蓝球经验贝叶斯静态偏置
    - 预测第 t 期红球(四五等奖综合)与蓝球(六等奖)对数拥挤度，并与纯均匀基线(0.0)对比平方误差
    - 所有预注册门禁同时通过才授予 oos_gate_status = "PASS"，否则锁死为 "TWIN_OOS_GATE_FAILED"
    """
    total = len(records)
    if total < initial_train_size + min_oos_draws:
        raise ValueError(
            f"Need at least {initial_train_size + min_oos_draws} draws, got {total}."
        )

    # 逐期构造严格因果特征与目标（t >= 1 才有上一期）
    x_red_all = np.zeros((total, len(RED_FEATURE_NAMES)), dtype=np.float64)
    x_blue_all = np.zeros((total, len(BLUE_FEATURE_NAMES)), dtype=np.float64)
    y_red_all = np.zeros(total, dtype=np.float64)
    y_blue_all = np.zeros(total, dtype=np.float64)
    blues_all = np.zeros(total, dtype=np.int32)

    for t in range(1, total):
        ctx = build_causal_context_from_history(
            history_before_t=records[:t],
            target_issue=records[t].issue,
            draw_date_bjt=records[t].draw_date_bjt,
            sentiment=None,
        )
        xr, _ = extract_red_features_matrix(np.asarray([records[t].reds], dtype=np.uint8), ctx)
        xb_16 = extract_blue_features_matrix(ctx)
        x_red_all[t] = xr[0]
        x_blue_all[t] = xb_16[records[t].blue - 1]
        y_red_all[t], y_blue_all[t] = _compute_tier_targets(records[t])
        blues_all[t] = records[t].blue

    oos_red_delta: List[float] = []
    oos_blue_delta: List[float] = []
    oos_blue_preds: List[float] = []
    oos_blue_trues: List[float] = []
    oos_trace: List[Dict[str, Any]] = []

    for t in range(initial_train_size, total):
        tr_slice = slice(1, t)
        # 1. 红球模型拟合与预测
        r_b0, r_w = _fit_winsorized_ridge(x_red_all[tr_slice], y_red_all[tr_slice], l2_alpha=l2_alpha_red)
        pred_r = r_b0 + float(x_red_all[t] @ r_w)
        base_r = float(np.mean(y_red_all[tr_slice]))  # 因果均值基线
        se_base_r = (y_red_all[t] - base_r) ** 2
        se_mod_r = (y_red_all[t] - pred_r) ** 2
        oos_red_delta.append(se_base_r - se_mod_r)

        # 2. 蓝球经验贝叶斯静态偏置 + 动态特征拟合（严格只看 1..t-1）
        b_prior_mean = float(np.mean(y_blue_all[tr_slice]))
        eb_bias = np.zeros(16, dtype=np.float64)
        for b_val in range(1, 17):
            mask_b = blues_all[tr_slice] == b_val
            cnt_b = int(np.sum(mask_b))
            if cnt_b > 0:
                mean_b = float(np.mean(y_blue_all[tr_slice][mask_b]))
                eb_bias[b_val - 1] = (cnt_b * (mean_b - b_prior_mean)) / (cnt_b + blue_eb_prior_strength)

        y_blue_resid = y_blue_all[tr_slice] - eb_bias[blues_all[tr_slice] - 1]
        b_b0, b_w = _fit_winsorized_ridge(x_blue_all[tr_slice], y_blue_resid, l2_alpha=l2_alpha_blue)
        pred_b = b_b0 + float(x_blue_all[t] @ b_w) + float(eb_bias[records[t].blue - 1])
        se_base_b = (y_blue_all[t] - b_prior_mean) ** 2
        se_mod_b = (y_blue_all[t] - pred_b) ** 2
        oos_blue_delta.append(se_base_b - se_mod_b)
        oos_blue_preds.append(pred_b)
        oos_blue_trues.append(float(y_blue_all[t]))

        oos_trace.append(
            {
                "issue": records[t].issue,
                "y_red_true": round(float(y_red_all[t]), 6),
                "y_red_pred": round(pred_r, 6),
                "y_blue_true": round(float(y_blue_all[t]), 6),
                "y_blue_pred": round(pred_b, 6),
                "abs_log_err_combined": round(
                    0.5 * abs(y_red_all[t] - pred_r) + 0.5 * abs(y_blue_all[t] - pred_b), 6
                ),
            }
        )

    red_arr = np.asarray(oos_red_delta, dtype=np.float64)
    blue_arr = np.asarray(oos_blue_delta, dtype=np.float64)
    comb_arr = 0.5 * red_arr + 0.5 * blue_arr

    r_mean, r_lo, r_hi = _moving_block_bootstrap_ci(red_arr)
    b_mean, b_lo, b_hi = _moving_block_bootstrap_ci(blue_arr)
    c_mean, c_lo, c_hi = _moving_block_bootstrap_ci(comb_arr)

    # 蓝球样本外 Spearman 秩相关
    pb_rank = np.argsort(np.argsort(oos_blue_preds)).astype(np.float64)
    tb_rank = np.argsort(np.argsort(oos_blue_trues)).astype(np.float64)
    blue_spearman = float(np.corrcoef(pb_rank, tb_rank)[0, 1]) if len(oos_blue_preds) > 2 else 0.0

    # 预注册硬门禁：综合误差改善 95% CI 下界 > 0 且蓝球六等奖排序显著正相关
    gate_pass = (c_mean > 0.0) and (c_lo > 0.0) and (b_lo > 0.0) and (blue_spearman >= 0.25)
    gate_status = "PASS" if gate_pass else "TWIN_OOS_GATE_FAILED"

    # 使用全部历史期拟合最终冻结生产参数（供下一期 t_next 使用）
    full_slice = slice(1, total)
    final_r_b0, final_r_w = _fit_winsorized_ridge(x_red_all[full_slice], y_red_all[full_slice], l2_alpha=l2_alpha_red)
    b_prior_full = float(np.mean(y_blue_all[full_slice]))
    eb_full = np.zeros(16, dtype=np.float64)
    for b_val in range(1, 17):
        mask_b = blues_all[full_slice] == b_val
        cnt_b = int(np.sum(mask_b))
        if cnt_b > 0:
            eb_full[b_val - 1] = (cnt_b * (float(np.mean(y_blue_all[full_slice][mask_b])) - b_prior_full)) / (
                cnt_b + blue_eb_prior_strength
            )
    final_b_b0, final_b_w = _fit_winsorized_ridge(
        x_blue_all[full_slice],
        y_blue_all[full_slice] - eb_full[blues_all[full_slice] - 1],
        l2_alpha=l2_alpha_blue,
    )

    oos_metrics = {
        "oos_draws": len(oos_trace),
        "red_mse_delta_mean": round(r_mean, 6),
        "red_mse_delta_ci95": [round(r_lo, 6), round(r_hi, 6)],
        "blue_mse_delta_mean": round(b_mean, 6),
        "blue_mse_delta_ci95": [round(b_lo, 6), round(b_hi, 6)],
        "combined_mse_delta_mean": round(c_mean, 6),
        "combined_mse_delta_ci95": [round(c_lo, 6), round(c_hi, 6)],
        "blue_oos_spearman": round(blue_spearman, 6),
        "oos_mean_abs_log_err": round(
            float(np.mean([row["abs_log_err_combined"] for row in oos_trace])), 6
        ),
    }

    body = {
        "model_version": "SSQ_V5_DIGITAL_TWIN_1.0",
        "trained_through_issue": records[-1].issue,
        "training_draw_count": total,
        "red_intercept": round(final_r_b0, 8),
        "red_weights": [round(float(v), 8) for v in final_r_w],
        "blue_intercept": round(final_b_b0, 8),
        "blue_weights": [round(float(v), 8) for v in final_b_w],
        "blue_static_shrinkage": [round(float(v), 8) for v in eb_full],
        "oos_gate_status": gate_status,
        "oos_metrics": oos_metrics,
    }
    artifact_sha = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    params = CalibratedTwinParameters(
        model_version=body["model_version"],
        trained_through_issue=body["trained_through_issue"],
        training_draw_count=total,
        red_intercept=body["red_intercept"],
        red_weights=tuple(body["red_weights"]),
        blue_intercept=body["blue_intercept"],
        blue_weights=tuple(body["blue_weights"]),
        blue_static_shrinkage=tuple(body["blue_static_shrinkage"]),
        oos_gate_status=gate_status,
        oos_metrics=oos_metrics,
        artifact_sha256=artifact_sha,
    )
    return params, oos_trace


def score_full_space_density(
    combos_1based: np.ndarray,
    ctx: DynamicDrawContext,
    params: CalibratedTwinParameters,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    计算全空间 1,107,568 红球与 16 蓝球的相对筹码密度及反身性排除掩码：
      red_density: shape=(1107568,), 归一化均值=1.0
      blue_density: shape=(16,), 归一化均值=1.0
      weird_mask: shape=(1107568,), bool
    """
    x_red, weird_mask = extract_red_features_matrix(combos_1based, ctx)
    w_r = np.asarray(params.red_weights, dtype=np.float64)
    log_r = params.red_intercept + (x_red @ w_r)
    # 数值安全截断防止极端指数溢出
    red_raw = np.exp(np.clip(log_r, -6.0, 6.0))
    red_density = red_raw / float(np.mean(red_raw))

    x_blue = extract_blue_features_matrix(ctx)
    w_b = np.asarray(params.blue_weights, dtype=np.float64)
    eb = np.asarray(params.blue_static_shrinkage, dtype=np.float64)
    log_b = params.blue_intercept + (x_blue @ w_b) + eb
    blue_raw = np.exp(np.clip(log_b, -4.0, 4.0))
    blue_density = blue_raw / float(np.mean(blue_raw))

    return red_density, blue_density, weird_mask

