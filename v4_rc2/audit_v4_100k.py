"""
audit_v4_100k.py
P1-B10: 100,000 次密码学边缘均匀性审计 + 全 1,107,568 组合精确覆盖交叉核验
"""

from __future__ import annotations

import itertools
import json
import time

from sampler_v4 import (
    BLUE_POOL_SIZE,
    RED_POOL_SIZE,
    TOTAL_RED_COMBINATIONS,
    DomainSeparatedDRBG,
    _build_abstract_red_masks,
    _mask_to_sorted_indices,
    exact_red_4plus_union_count,
    generate_v4_tickets,
)

CHI2_DF32_ALPHA_001 = 62.487
CHI2_DF15_ALPHA_001 = 37.697


def brute_force_1107568_count(red_sets: list[list[int]]) -> int:
    """暴力遍历全部 C(33,6)=1,107,568 种红球开奖组合，交叉验证精确并集算法。"""
    ticket_masks = []
    for reds in red_sets:
        m = 0
        for r in reds:
            m |= 1 << (r - 1)
        ticket_masks.append(m)

    bits = [1 << i for i in range(RED_POOL_SIZE)]
    hits = 0
    for c in itertools.combinations(bits, 6):
        draw_mask = c[0] | c[1] | c[2] | c[3] | c[4] | c[5]
        for tm in ticket_masks:
            if (draw_mask & tm).bit_count() >= 4:
                hits += 1
                break
    return hits


def run_100k_audit(trials: int = 100_000, ticket_count: int = 8) -> dict:
    t0 = time.perf_counter()
    abstract_masks, _ = _build_abstract_red_masks(ticket_count=ticket_count, max_local_search_steps=5000)
    idx_sets = [_mask_to_sorted_indices(m) for m in abstract_masks]

    drbg = DomainSeparatedDRBG()
    red_base = list(range(RED_POOL_SIZE))
    blue_base = list(range(BLUE_POOL_SIZE))

    red_freq_slot0 = [0] * RED_POOL_SIZE
    red_freq_slot_last = [0] * RED_POOL_SIZE
    blue_freq_slot0 = [0] * BLUE_POOL_SIZE

    for _ in range(trials):
        perm = drbg.fisher_yates(red_base)
        b_perm = drbg.fisher_yates(blue_base)
        for idx in idx_sets[0]:
            red_freq_slot0[perm[idx]] += 1
        for idx in idx_sets[-1]:
            red_freq_slot_last[perm[idx]] += 1
        blue_freq_slot0[b_perm[0]] += 1

    exp_red = (trials * 6) / RED_POOL_SIZE
    exp_blue = trials / BLUE_POOL_SIZE
    chi2_red_0 = sum((o - exp_red) ** 2 / exp_red for o in red_freq_slot0)
    chi2_red_last = sum((o - exp_red) ** 2 / exp_red for o in red_freq_slot_last)
    chi2_blue_0 = sum((o - exp_blue) ** 2 / exp_blue for o in blue_freq_slot0)

    # 全 1,107,568 暴力枚举交叉校验 (N=6)
    sample6 = generate_v4_tickets(6, DomainSeparatedDRBG(b"\x06" * 32))
    reds6 = [t["reds"] for t in sample6.tickets]
    fast_cnt = exact_red_4plus_union_count(reds6)
    brute_cnt = brute_force_1107568_count(reds6)

    elapsed = round(time.perf_counter() - t0, 2)
    report = {
        "trials": trials,
        "ticket_count": ticket_count,
        "elapsed_seconds": elapsed,
        "chi2_red_slot0": round(chi2_red_0, 4),
        "chi2_red_slot_last": round(chi2_red_last, 4),
        "chi2_red_threshold_p001": CHI2_DF32_ALPHA_001,
        "chi2_blue_slot0": round(chi2_blue_0, 4),
        "chi2_blue_threshold_p001": CHI2_DF15_ALPHA_001,
        "exact_union_n6_fast": fast_cnt,
        "exact_union_n6_bruteforce_1107568": brute_cnt,
        "exact_union_match": fast_cnt == brute_cnt,
        "overall_audit_pass": (
            chi2_red_0 < CHI2_DF32_ALPHA_001
            and chi2_red_last < CHI2_DF32_ALPHA_001
            and chi2_blue_0 < CHI2_DF15_ALPHA_001
            and fast_cnt == brute_cnt
        ),
    }
    return report


if __name__ == "__main__":
    result = run_100k_audit()
    print(json.dumps(result, indent=2))
    if not result["overall_audit_pass"]:
        raise SystemExit(1)
