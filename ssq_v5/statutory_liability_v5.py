"""
statutory_liability_v5.py
Phase 4 statutory-RMB exposure engine.

Important terminology:
- This module does NOT claim E[min(X,C)] == min(E[X],C).
- Outputs are explicitly named PLUG_IN_STATUTORY_RMB_EXPOSURE: statutory payout exposure
  obtained by inserting model-predicted winner counts into the bound local rule table.
- All prize/rate/cap constants are loaded from a hash-bound local rule JSON file.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

import numpy as np

EXPOSURE_LABEL = "PLUG_IN_STATUTORY_RMB_EXPOSURE"

_COMB_LUT = np.zeros((34, 7), dtype=np.int32)
for _n in range(34):
    for _k in range(min(_n, 6) + 1):
        _COMB_LUT[_n, _k] = math.comb(_n, _k)

NUM_SUBSETS_5 = math.comb(33, 5)
NUM_SUBSETS_4 = math.comb(33, 4)
NUM_SUBSETS_3 = math.comb(33, 3)

_COLS_5 = tuple(tuple(j for j in range(6) if j != drop) for drop in range(6))
_COLS_4 = tuple(itertools.combinations(range(6), 4))
_COLS_3 = tuple(itertools.combinations(range(6), 3))


@dataclass(frozen=True)
class StatutoryRuleTable:
    rule_document_id: str
    rule_document_sha256: str
    prize_return_ratio: float
    tier3_rmb: float
    tier4_rmb: float
    tier5_rmb: float
    tier6_rmb: float
    fuyun_rmb: float
    low_pool_threshold_rmb: float
    low_pool_t1_share: float
    low_pool_t2_share: float
    high_pool_t1_part_a_share: float
    high_pool_t1_part_b_share: float
    high_pool_t2_share: float
    single_part_cap_rmb: float
    promo_sixth_extra_rmb_per_ticket: float
    exposure_label: str = EXPOSURE_LABEL


def load_statutory_rule_table(path: str | Path) -> StatutoryRuleTable:
    p = Path(path)
    raw = p.read_bytes()
    doc = json.loads(raw.decode("utf-8"))
    sha = hashlib.sha256(raw).hexdigest()
    return StatutoryRuleTable(
        rule_document_id=str(doc["schema"]),
        rule_document_sha256=sha,
        prize_return_ratio=float(doc["prize_return_ratio"]),
        tier3_rmb=float(doc["tier3_rmb"]),
        tier4_rmb=float(doc["tier4_rmb"]),
        tier5_rmb=float(doc["tier5_rmb"]),
        tier6_rmb=float(doc["tier6_rmb"]),
        fuyun_rmb=float(doc["fuyun_rmb"]),
        low_pool_threshold_rmb=float(doc["low_pool_threshold_rmb"]),
        low_pool_t1_share=float(doc["low_pool_t1_share"]),
        low_pool_t2_share=float(doc["low_pool_t2_share"]),
        high_pool_t1_part_a_share=float(doc["high_pool_t1_part_a_share"]),
        high_pool_t1_part_b_share=float(doc["high_pool_t1_part_b_share"]),
        high_pool_t2_share=float(doc["high_pool_t2_share"]),
        single_part_cap_rmb=float(doc["single_part_cap_rmb"]),
        promo_sixth_extra_rmb_per_ticket=float(doc["promo_sixth_extra_rmb_per_ticket"]),
        exposure_label=str(doc.get("exposure_label", EXPOSURE_LABEL)),
    )


@dataclass(frozen=True)
class ExactNeighborhoodTensors:
    d6: np.ndarray
    n5: np.ndarray
    n4: np.ndarray
    n3: np.ndarray
    n_le_2: np.ndarray
    total_d_sum: float


def compute_exact_all_neighborhoods(
    combos_1based: np.ndarray,
    red_density: np.ndarray,
) -> ExactNeighborhoodTensors:
    """
    Exact combinadic inclusion-exclusion over all C(33,6)=1,107,568 red outcomes.

      N5     = sum S5 - 6 D
      N4     = sum S4 - 5 N5 - 15 D
      N3     = sum S3 - 4 N4 - 10 N5 - 20 D
      N<=2   = sum(D) - D - N5 - N4 - N3
    """
    if combos_1based.shape != (math.comb(33, 6), 6):
        raise ValueError("combos_1based must contain the full C(33,6) universe.")
    d = np.asarray(red_density, dtype=np.float64)
    if d.shape != (math.comb(33, 6),):
        raise ValueError("red_density has wrong shape.")
    if np.any(~np.isfinite(d)) or np.any(d < 0):
        raise ValueError("red_density must be finite and non-negative.")

    c0 = combos_1based.astype(np.int32, copy=False) - 1
    total_d = float(np.sum(d))

    sum_s5 = np.zeros(NUM_SUBSETS_5, dtype=np.float64)
    idx5_cache = []
    for cols in _COLS_5:
        idx5 = (
            _COMB_LUT[c0[:, cols[0]], 1]
            + _COMB_LUT[c0[:, cols[1]], 2]
            + _COMB_LUT[c0[:, cols[2]], 3]
            + _COMB_LUT[c0[:, cols[3]], 4]
            + _COMB_LUT[c0[:, cols[4]], 5]
        )
        idx5_cache.append(idx5)
        sum_s5 += np.bincount(idx5, weights=d, minlength=NUM_SUBSETS_5)
    raw_s5 = np.zeros_like(d)
    for idx5 in idx5_cache:
        raw_s5 += sum_s5[idx5]
    n5 = np.maximum(0.0, raw_s5 - 6.0 * d)

    sum_s4 = np.zeros(NUM_SUBSETS_4, dtype=np.float64)
    idx4_cache = []
    for cols in _COLS_4:
        idx4 = (
            _COMB_LUT[c0[:, cols[0]], 1]
            + _COMB_LUT[c0[:, cols[1]], 2]
            + _COMB_LUT[c0[:, cols[2]], 3]
            + _COMB_LUT[c0[:, cols[3]], 4]
        )
        idx4_cache.append(idx4)
        sum_s4 += np.bincount(idx4, weights=d, minlength=NUM_SUBSETS_4)
    raw_s4 = np.zeros_like(d)
    for idx4 in idx4_cache:
        raw_s4 += sum_s4[idx4]
    n4 = np.maximum(0.0, raw_s4 - 5.0 * n5 - 15.0 * d)

    sum_s3 = np.zeros(NUM_SUBSETS_3, dtype=np.float64)
    idx3_cache = []
    for cols in _COLS_3:
        idx3 = (
            _COMB_LUT[c0[:, cols[0]], 1]
            + _COMB_LUT[c0[:, cols[1]], 2]
            + _COMB_LUT[c0[:, cols[2]], 3]
        )
        idx3_cache.append(idx3)
        sum_s3 += np.bincount(idx3, weights=d, minlength=NUM_SUBSETS_3)
    raw_s3 = np.zeros_like(d)
    for idx3 in idx3_cache:
        raw_s3 += sum_s3[idx3]
    n3 = np.maximum(0.0, raw_s3 - 4.0 * n4 - 10.0 * n5 - 20.0 * d)

    n_le_2 = np.maximum(0.0, total_d - d - n5 - n4 - n3)
    return ExactNeighborhoodTensors(d6=d, n5=n5, n4=n4, n3=n3, n_le_2=n_le_2, total_d_sum=total_d)


def _single_blue_plugin_exposure(
    nb: ExactNeighborhoodTensors,
    q_b: float,
    sales_rmb: float,
    pool_before_rmb: float,
    special_rule_active: bool,
    fuyun_prize_active: bool,
    promo_sixth_extra_rmb_per_ticket: float,
    remaining_sixth_promo_rmb: float,
    rule: StatutoryRuleTable,
) -> np.ndarray:
    """
    Plug-in statutory RMB exposure conditional on one physical blue outcome.
    Winner-count expectations are inserted into statutory amount/cap rules.
    """
    if not (0.0 <= q_b <= 1.0):
        raise ValueError("q_b must be in [0,1].")
    sales_tickets = float(sales_rmb) / 2.0
    scale = sales_tickets / max(nb.total_d_sum, 1e-12)
    not_qb = 1.0 - q_b

    w1 = scale * nb.d6 * q_b
    w2 = scale * nb.d6 * not_qb
    w3 = scale * nb.n5 * q_b
    w4 = scale * (nb.n5 * not_qb + nb.n4 * q_b)
    w5 = scale * (nb.n4 * not_qb + nb.n3 * q_b)
    w6 = scale * nb.n_le_2 * q_b

    t3_rmb = rule.tier3_rmb * w3
    t4_rmb = rule.tier4_rmb * w4
    t5_rmb = rule.tier5_rmb * w5
    base_t6_rmb = rule.tier6_rmb * w6

    promo_budget = max(0.0, float(remaining_sixth_promo_rmb))
    promo_extra_each = max(0.0, float(promo_sixth_extra_rmb_per_ticket))
    if promo_budget > 0.0 and promo_extra_each > 0.0:
        promo_t6_rmb = np.minimum(promo_extra_each * w6, promo_budget)
    else:
        promo_t6_rmb = np.zeros_like(w6)

    if fuyun_prize_active:
        w_fuyun = scale * nb.n3 * not_qb
        fuyun_rmb = rule.fuyun_rmb * w_fuyun
    else:
        fuyun_rmb = np.zeros_like(w6)

    fixed_rmb = t3_rmb + t4_rmb + t5_rmb + base_t6_rmb + promo_t6_rmb + fuyun_rmb
    high_tier_fund = np.maximum(0.0, rule.prize_return_ratio * float(sales_rmb) - fixed_rmb)

    use_high_pool_rule = bool(special_rule_active) or float(pool_before_rmb) >= rule.low_pool_threshold_rmb
    if use_high_pool_rule:
        t1_part_a = np.minimum(
            rule.high_pool_t1_part_a_share * high_tier_fund + float(pool_before_rmb),
            rule.single_part_cap_rmb * w1,
        )
        t1_part_b = np.minimum(
            rule.high_pool_t1_part_b_share * high_tier_fund,
            rule.single_part_cap_rmb * w1,
        )
        t1_rmb = t1_part_a + t1_part_b
        t2_rmb = np.minimum(
            rule.high_pool_t2_share * high_tier_fund,
            rule.single_part_cap_rmb * w2,
        )
    else:
        t1_rmb = np.minimum(
            rule.low_pool_t1_share * high_tier_fund + float(pool_before_rmb),
            rule.single_part_cap_rmb * w1,
        )
        t2_rmb = np.minimum(
            rule.low_pool_t2_share * high_tier_fund,
            rule.single_part_cap_rmb * w2,
        )

    return fixed_rmb + t1_rmb + t2_rmb


def compute_statutory_rmb_liability_surface(
    nb: ExactNeighborhoodTensors,
    blue_density: np.ndarray,
    weird_mask: np.ndarray,
    sales_rmb: float,
    pool_before_rmb: float,
    special_rule_active: bool,
    fuyun_prize_active: bool,
    remaining_sixth_promo_rmb: float,
    rule: StatutoryRuleTable,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Returns:
      red_plugin_statutory_rmb_exposure: shape=(1,107,568,)
      blue_plugin_statutory_rmb_exposure: shape=(16,)

    Red exposure averages over the 16 equally likely physical blue outcomes.
    Blue exposure averages over the full uniform physical red-outcome universe.
    """
    q_blues = np.asarray(blue_density, dtype=np.float64)
    if q_blues.shape != (16,) or np.any(q_blues < 0) or not np.isfinite(q_blues).all():
        raise ValueError("blue_density must be 16 finite non-negative values.")
    q_blues = q_blues / float(np.sum(q_blues))

    red_expected = np.zeros_like(nb.d6, dtype=np.float64)
    blue_expected = np.zeros(16, dtype=np.float64)

    promo_extra = rule.promo_sixth_extra_rmb_per_ticket if remaining_sixth_promo_rmb > 0 else 0.0
    for b_idx in range(16):
        vec = _single_blue_plugin_exposure(
            nb=nb,
            q_b=float(q_blues[b_idx]),
            sales_rmb=sales_rmb,
            pool_before_rmb=pool_before_rmb,
            special_rule_active=special_rule_active,
            fuyun_prize_active=fuyun_prize_active,
            promo_sixth_extra_rmb_per_ticket=promo_extra,
            remaining_sixth_promo_rmb=remaining_sixth_promo_rmb,
            rule=rule,
        )
        red_expected += vec / 16.0
        blue_expected[b_idx] = float(np.mean(vec))

    red_expected = np.where(np.asarray(weird_mask, dtype=bool), np.inf, red_expected)
    return red_expected, blue_expected


def compute_single_ticket_rmb_breakdown(
    nb: ExactNeighborhoodTensors,
    row_idx: int,
    blue_1based: int,
    blue_density: np.ndarray,
    sales_rmb: float,
    pool_before_rmb: float,
    special_rule_active: bool,
    fuyun_prize_active: bool,
    remaining_sixth_promo_rmb: float,
    rule: StatutoryRuleTable,
) -> Dict[str, float | str]:
    q_blues = np.asarray(blue_density, dtype=np.float64)
    q_blues = q_blues / float(np.sum(q_blues))
    q_b = float(q_blues[blue_1based - 1])
    not_qb = 1.0 - q_b
    scale = (float(sales_rmb) / 2.0) / max(nb.total_d_sum, 1e-12)

    d6 = float(nb.d6[row_idx])
    n5 = float(nb.n5[row_idx])
    n4 = float(nb.n4[row_idx])
    n3 = float(nb.n3[row_idx])
    n_le_2 = float(nb.n_le_2[row_idx])

    w1 = scale * d6 * q_b
    w2 = scale * d6 * not_qb
    w3 = scale * n5 * q_b
    w4 = scale * (n5 * not_qb + n4 * q_b)
    w5 = scale * (n4 * not_qb + n3 * q_b)
    w6 = scale * n_le_2 * q_b
    w_fy = scale * n3 * not_qb if fuyun_prize_active else 0.0

    t3 = rule.tier3_rmb * w3
    t4 = rule.tier4_rmb * w4
    t5 = rule.tier5_rmb * w5
    t6_base = rule.tier6_rmb * w6
    promo = min(
        max(0.0, rule.promo_sixth_extra_rmb_per_ticket) * w6,
        max(0.0, float(remaining_sixth_promo_rmb)),
    )
    fy = rule.fuyun_rmb * w_fy
    fixed = t3 + t4 + t5 + t6_base + promo + fy
    high_fund = max(0.0, rule.prize_return_ratio * float(sales_rmb) - fixed)

    if special_rule_active or float(pool_before_rmb) >= rule.low_pool_threshold_rmb:
        t1 = min(rule.high_pool_t1_part_a_share * high_fund + float(pool_before_rmb), rule.single_part_cap_rmb * w1)
        t1 += min(rule.high_pool_t1_part_b_share * high_fund, rule.single_part_cap_rmb * w1)
        t2 = min(rule.high_pool_t2_share * high_fund, rule.single_part_cap_rmb * w2)
    else:
        t1 = min(rule.low_pool_t1_share * high_fund + float(pool_before_rmb), rule.single_part_cap_rmb * w1)
        t2 = min(rule.low_pool_t2_share * high_fund, rule.single_part_cap_rmb * w2)

    return {
        "exposure_label": EXPOSURE_LABEL,
        "tier1_rmb": round(t1, 2),
        "tier2_rmb": round(t2, 2),
        "tier3_rmb": round(t3, 2),
        "tier4_rmb": round(t4, 2),
        "tier5_rmb": round(t5, 2),
        "tier6_base_rmb": round(t6_base, 2),
        "tier6_promo_extra_rmb": round(promo, 2),
        "fuyun_rmb": round(fy, 2),
        "total_plugin_statutory_rmb_exposure": round(fixed + t1 + t2, 2),
    }
