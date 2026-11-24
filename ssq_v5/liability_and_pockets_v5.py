"""
liability_and_pockets_v5.py
Phase 4:
1. 全空间 1,107,568 精确 Combinadic 容斥汉明邻域赔付敞口引擎（< 3 秒，0 近似误差）
2. 与 V4 2026滞回规则状态对齐的盘面水位路由器（Regime Switcher）
3. 正交稀疏口袋切分与池内 CSPRNG 球面/口袋双重分散抽样器
"""

from __future__ import annotations

import itertools
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np

from db_and_sentiment_v5 import sha256_bytes

# 预构建 Combinadic 查找表 COMB[n, k] (0 <= n <= 33, 0 <= k <= 6)
_COMB_LUT = np.zeros((34, 7), dtype=np.int32)
for _n in range(34):
    for _k in range(min(_n, 6) + 1):
        _COMB_LUT[_n, _k] = math.comb(_n, _k)

NUM_SUBSETS_5 = math.comb(33, 5)  # 237,336
NUM_SUBSETS_4 = math.comb(33, 4)  # 40,920

# 6选5 与 6选4 的列切片模板
_COLS_5 = tuple(tuple(j for j in range(6) if j != drop) for drop in range(6))
_COLS_4 = tuple(itertools.combinations(range(6), 4))


def compute_exact_neighborhood_densities(
    combos_1based: np.ndarray,
    red_density: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    通过 Combinadic 双射 + 容斥原理，精确计算全部 1,107,568 组红球的：
      - neighbor_5: 精确 5 红邻域 (162 组周边组合) 密度总和
      - neighbor_4: 精确 4 红邻域 (5,265 组周边组合) 密度总和
    数学上与 O(1.1M * 5428) 暴力计算严格恒等，耗时约 2 秒。
    """
    c0 = combos_1based.astype(np.int32, copy=False) - 1  # 转 0..32
    d = red_density.astype(np.float64, copy=False)

    # ---- Step 1: 精确 5 红邻域 ----
    sum_s5 = np.zeros(NUM_SUBSETS_5, dtype=np.float64)
    for cols in _COLS_5:
        idx5 = (
            _COMB_LUT[c0[:, cols[0]], 1]
            + _COMB_LUT[c0[:, cols[1]], 2]
            + _COMB_LUT[c0[:, cols[2]], 3]
            + _COMB_LUT[c0[:, cols[3]], 4]
            + _COMB_LUT[c0[:, cols[4]], 5]
        )
        sum_s5 += np.bincount(idx5, weights=d, minlength=NUM_SUBSETS_5)

    raw_s5 = np.zeros_like(d)
    for cols in _COLS_5:
        idx5 = (
            _COMB_LUT[c0[:, cols[0]], 1]
            + _COMB_LUT[c0[:, cols[1]], 2]
            + _COMB_LUT[c0[:, cols[2]], 3]
            + _COMB_LUT[c0[:, cols[3]], 4]
            + _COMB_LUT[c0[:, cols[4]], 5]
        )
        raw_s5 += sum_s5[idx5]

    neighbor_5 = np.maximum(0.0, raw_s5 - 6.0 * d)

    # ---- Step 2: 精确 4 红邻域 ----
    sum_s4 = np.zeros(NUM_SUBSETS_4, dtype=np.float64)
    for cols in _COLS_4:
        idx4 = (
            _COMB_LUT[c0[:, cols[0]], 1]
            + _COMB_LUT[c0[:, cols[1]], 2]
            + _COMB_LUT[c0[:, cols[2]], 3]
            + _COMB_LUT[c0[:, cols[3]], 4]
        )
        sum_s4 += np.bincount(idx4, weights=d, minlength=NUM_SUBSETS_4)

    raw_s4 = np.zeros_like(d)
    for cols in _COLS_4:
        idx4 = (
            _COMB_LUT[c0[:, cols[0]], 1]
            + _COMB_LUT[c0[:, cols[1]], 2]
            + _COMB_LUT[c0[:, cols[2]], 3]
            + _COMB_LUT[c0[:, cols[3]], 4]
        )
        raw_s4 += sum_s4[idx4]

    neighbor_4 = np.maximum(0.0, raw_s4 - 5.0 * neighbor_5 - 15.0 * d)
    return neighbor_5, neighbor_4


def compute_red_liability_score(
    red_density: np.ndarray,
    neighbor_5: np.ndarray,
    neighbor_4: np.ndarray,
    weird_mask: np.ndarray,
) -> np.ndarray:
    """
    结合双色球官方固定奖与浮动奖的归一化赔付结构计算红球综合赔付敞口：
    - 精确层(6红): 均值归一化至 1.0
    - 5红邻域(三等奖3000元/四等奖200元): 除以162归一化至 1.0
    - 4红邻域(四等奖200元/五等奖10元): 除以5265归一化至 1.0
    权重按双色球官方各层期望赔付占比设定（头二浮动敞口 50%，5红固定敞口 20%，4红固定敞口 30%）；
    反身性怪号掩码强制置为 +inf，绝对禁止进入稀疏池。
    """
    n6_norm = red_density
    n5_norm = neighbor_5 / 162.0
    n4_norm = neighbor_4 / 5265.0
    liability = 0.50 * n6_norm + 0.20 * n5_norm + 0.30 * n4_norm
    liability = np.where(weird_mask, np.inf, liability)
    return liability


@dataclass(frozen=True)
class RegimeRoutingPolicy:
    regime_name: str
    red_percentile_low: float
    red_percentile_high: float
    blue_keep_count: int


def select_regime_policy(
    pool_after_prev_rmb: float,
    special_rule_active: bool,
    promo_active: bool,
    ticket_count: int,
) -> RegimeRoutingPolicy:
    """严格对齐 Phase 1 修正后的 2026 滞回规则状态机，按盘面水位路由稀疏池分位区间。"""
    if special_rule_active or promo_active:
        base_blue_keep = 10
        p_lo, p_hi = 2.0, 8.0
        name = "HIGH_POOL_OR_PROMO_BALANCED_LIABILITY"
    elif pool_after_prev_rmb < 100_000_000.0:
        base_blue_keep = 6
        p_lo, p_hi = 0.5, 2.5
        name = "DEEP_SPARSE_POOL_ACCUMULATION"
    else:
        base_blue_keep = 8
        p_lo, p_hi = 1.0, 5.0
        name = "NORMAL_LOW_LIABILITY_SPARSE"

    # 若单期投注数大于蓝球裁剪上限，自动扩充至 min(16, max(base_blue_keep, ticket_count)) 保证蓝球无放回可行
    blue_keep = 16 if ticket_count >= 16 else max(base_blue_keep, ticket_count)
    return RegimeRoutingPolicy(
        regime_name=name,
        red_percentile_low=p_lo,
        red_percentile_high=p_hi,
        blue_keep_count=blue_keep,
    )


def _compute_orthogonal_pocket_key(reds: Sequence[int]) -> str:
    """按 和值段 × 断区状态 × 奇偶偏态 × 跨度段 切分正交结构口袋。"""
    r_sum = sum(reds)
    sum_b = "S_LO" if r_sum < 85 else ("S_HI" if r_sum > 125 else "S_MID")

    z1 = sum(1 for r in reds if r <= 11)
    z2 = sum(1 for r in reds if 12 <= r <= 22)
    z3 = sum(1 for r in reds if r >= 23)
    if z1 == 0:
        z_code = "DROP_Z1"
    elif z2 == 0:
        z_code = "DROP_Z2"
    elif z3 == 0:
        z_code = "DROP_Z3"
    else:
        z_code = "NO_DROP"

    odd_cnt = sum(1 for r in reds if r & 1)
    odd_b = "ODD_LO" if odd_cnt <= 2 else ("ODD_HI" if odd_cnt >= 4 else "ODD_33")

    span = reds[-1] - reds[0]
    span_b = "SP_NARROW" if span < 20 else ("SP_WIDE" if span > 28 else "SP_MID")
    return f"{sum_b}|{z_code}|{odd_b}|{span_b}"


@dataclass(frozen=True)
class FrozenSparsePool:
    regime: RegimeRoutingPolicy
    candidate_red_indices: np.ndarray     # 落在目标分位区间的红球行索引
    candidate_blues: Tuple[int, ...]      # 保留的低拥挤蓝球 (1..16)
    pockets: Dict[str, List[int]]         # pocket_key -> [combo_row_idx, ...]
    manifest_dict: Dict[str, Any]
    manifest_sha256: str


def build_frozen_sparse_pool(
    combos_1based: np.ndarray,
    red_liability: np.ndarray,
    blue_density: np.ndarray,
    policy: RegimeRoutingPolicy,
) -> FrozenSparsePool:
    """切出冻结稀疏候选池与正交结构口袋，并计算可审计 SPARSE_POOL_SHA256。"""
    valid_mask = np.isfinite(red_liability)
    valid_scores = red_liability[valid_mask]
    if valid_scores.size < 1000:
        raise RuntimeError("SPARSE_POOL_DEGENERATE: Too few finite red candidates.")

    lo_val, hi_val = np.percentile(valid_scores, [policy.red_percentile_low, policy.red_percentile_high])
    pool_idx = np.flatnonzero(valid_mask & (red_liability >= lo_val) & (red_liability <= hi_val))
    if pool_idx.size < 100:
        # 极端平分兜底：按排序直接截取对应排名段
        order = np.argsort(red_liability)
        n_valid = int(np.sum(valid_mask))
        s_i = int(n_valid * policy.red_percentile_low / 100.0)
        e_i = max(s_i + 100, int(n_valid * policy.red_percentile_high / 100.0))
        pool_idx = order[s_i:e_i]

    # 蓝球按拥挤度升序保留最冷清的 blue_keep_count 个
    blue_order = np.argsort(blue_density)  # 0..15
    candidate_blues = tuple(int(b + 1) for b in blue_order[: policy.blue_keep_count])

    pockets_map: Dict[str, List[int]] = defaultdict(list)
    for row_i in pool_idx.tolist():
        key = _compute_orthogonal_pocket_key(combos_1based[row_i].tolist())
        pockets_map[key].append(row_i)

    # 过滤掉样本过小(<5)的碎片口袋（除非总口袋数不足）
    filtered_pockets = {k: v for k, v in pockets_map.items() if len(v) >= 5}
    if not filtered_pockets:
        filtered_pockets = dict(pockets_map)

    sorted_pockets = {k: sorted(v) for k, v in sorted(filtered_pockets.items())}
    manifest_dict = {
        "schema": "SSQ_V5_SPARSE_POOL_MANIFEST_1.0",
        "regime_name": policy.regime_name,
        "red_percentile_band": [policy.red_percentile_low, policy.red_percentile_high],
        "red_pool_size": int(pool_idx.size),
        "candidate_blues": list(candidate_blues),
        "pocket_counts": {k: len(v) for k, v in sorted_pockets.items()},
        "pool_index_digest": sha256_bytes(pool_idx.astype(np.int32).tobytes()),
    }
    manifest_sha = sha256_bytes(
        json.dumps(manifest_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )

    return FrozenSparsePool(
        regime=policy,
        candidate_red_indices=pool_idx,
        candidate_blues=candidate_blues,
        pockets=sorted_pockets,
        manifest_dict=manifest_dict,
        manifest_sha256=manifest_sha,
    )


def sample_from_sparse_pockets(
    combos_1based: np.ndarray,
    red_liability: np.ndarray,
    sparse_pool: FrozenSparsePool,
    ticket_count: int,
    drbg: Any,  # 兼容 V4 DomainSeparatedDRBG（要求具备 fisher_yates(seq) 方法）
    max_candidates_per_ticket: int = 256,
) -> List[Dict[str, Any]]:
    """
    池内 CSPRNG 双重分散抽样（有界执行，绝不死循环）：
    1. 蓝球：在候选稀疏蓝池内按轮无放回 CSPRNG 洗牌
    2. 口袋：CSPRNG 洗牌正交口袋列表轮转分配，确保多注覆盖不同稀疏结构分支
    3. 红球：在指定口袋内对 CSPRNG 随机抽检的最多 256 个候选，优先取与已选红球最大重合 <= 2（小注数优先 0 重合）的组合
    """
    # 1. 稀疏蓝球无放回分轮洗牌
    blue_pool = list(sparse_pool.candidate_blues)
    blues: List[int] = []
    full_r, rem = divmod(ticket_count, len(blue_pool))
    for _ in range(full_r):
        blues.extend(drbg.fisher_yates(blue_pool))
    if rem > 0:
        blues.extend(drbg.fisher_yates(blue_pool)[:rem])

    # 2. 正交口袋无放回分轮洗牌
    pocket_keys = list(sparse_pool.pockets.keys())
    assigned_pockets: List[str] = []
    pf, pr = divmod(ticket_count, len(pocket_keys))
    for _ in range(pf):
        assigned_pockets.extend(drbg.fisher_yates(pocket_keys))
    if pr > 0:
        assigned_pockets.extend(drbg.fisher_yates(pocket_keys)[:pr])

    chosen_masks: List[int] = []
    seen_full: set[Tuple[Tuple[int, ...], int]] = set()
    tickets: List[Dict[str, Any]] = []

    finite_liab = red_liability[np.isfinite(red_liability)]
    n_finite = float(finite_liab.size)

    for i in range(ticket_count):
        pkey = assigned_pockets[i]
        blue = blues[i]
        candidates = sparse_pool.pockets[pkey]
        # 有界随机抽检（若口袋较大，仅采样前 max_candidates_per_ticket 个）
        shuffled_cands = drbg.fisher_yates(candidates)[:max_candidates_per_ticket]

        best_row = -1
        best_score = (999, 999)  # (max_overlap, sum_overlap_sq)
        for row_idx in shuffled_cands:
            reds = tuple(int(x) for x in combos_1based[row_idx].tolist())
            if (reds, blue) in seen_full:
                continue
            mask = 0
            for r in reds:
                mask |= 1 << (r - 1)
            if not chosen_masks:
                best_row = row_idx
                break
            overlaps = [(mask & cm).bit_count() for cm in chosen_masks]
            sc = (max(overlaps), sum(o * o for o in overlaps))
            if sc < best_score:
                best_score = sc
                best_row = row_idx
                if sc[0] == 0:
                    break

        if best_row < 0:
            raise RuntimeError("SPARSE_SAMPLER_EXHAUSTED: Cannot find distinct 6+1 combo.")

        reds_list = [int(x) for x in combos_1based[best_row].tolist()]
        seen_full.add((tuple(reds_list), blue))
        mask = 0
        for r in reds_list:
            mask |= 1 << (r - 1)
        chosen_masks.append(mask)

        liab_val = float(red_liability[best_row])
        pct = float(np.sum(finite_liab <= liab_val) / n_finite * 100.0)
        tickets.append(
            {
                "reds": reds_list,
                "blue": blue,
                "formatted": f"{' '.join(f'{r:02d}' for r in reds_list)} + {blue:02d}",
                "pocket_key": pkey,
                "liability_percentile": round(pct, 4),
            }
        )

    return tickets

