"""
sampler_v4.py
封口：P1-B07 (N=1..15 精确红球 4+ 并集覆盖计算),
      CSPRNG 措辞修正为 CRYPTOGRAPHICALLY_UNIFORM_MARGINAL_UNDER_CSPRNG_ASSUMPTION
"""

from __future__ import annotations

import hashlib
import itertools
import math
import os
from dataclasses import dataclass
from typing import Dict, List, Sequence, Set, Tuple

RED_POOL_SIZE = 33
RED_PICK_COUNT = 6
BLUE_POOL_SIZE = 16
TOTAL_RED_COMBINATIONS = math.comb(RED_POOL_SIZE, RED_PICK_COUNT)  # 1,107,568
TOTAL_FULL_SPACE = TOTAL_RED_COMBINATIONS * BLUE_POOL_SIZE          # 17,721,088

ALGORITHM_VERSION = "SSQ_V4_BALANCED_COVERAGE_ALGO_2.0"
RNG_MODEL_DECLARATION = "CRYPTOGRAPHICALLY_UNIFORM_MARGINAL_UNDER_CSPRNG_ASSUMPTION"

P_RED_4PLUS = (
    math.comb(6, 4) * math.comb(27, 2)
    + math.comb(6, 5) * math.comb(27, 1)
    + math.comb(6, 6)
) / TOTAL_RED_COMBINATIONS


class DomainSeparatedDRBG:
    DOMAIN_TAG = b"SSQ-V4-DRBG-v2"

    def __init__(self, seed: bytes | None = None) -> None:
        if seed is None:
            seed = os.urandom(32)
        if len(seed) != 32:
            raise ValueError("Seed must be 32 bytes (256-bit).")
        self.seed = seed
        self.counter = 0

    def _next_uint256(self) -> int:
        digest = hashlib.sha256(
            self.seed + self.DOMAIN_TAG + self.counter.to_bytes(8, "big")
        ).digest()
        self.counter += 1
        return int.from_bytes(digest, "big")

    def rand_below(self, upper: int) -> int:
        if upper <= 0:
            raise ValueError("upper must be positive.")
        if upper == 1:
            return 0
        space = 1 << 256
        limit = space - (space % upper)
        while True:
            v = self._next_uint256()
            if v < limit:
                return v % upper

    def fisher_yates(self, items: Sequence[int]) -> List[int]:
        arr = list(items)
        for i in range(len(arr) - 1, 0, -1):
            j = self.rand_below(i + 1)
            arr[i], arr[j] = arr[j], arr[i]
        return arr


def _sample_blue_balls(ticket_count: int, drbg: DomainSeparatedDRBG) -> List[int]:
    blues: List[int] = []
    full_rounds, remainder = divmod(ticket_count, BLUE_POOL_SIZE)
    base = list(range(1, BLUE_POOL_SIZE + 1))
    for _ in range(full_rounds):
        blues.extend(drbg.fisher_yates(base))
    if remainder > 0:
        blues.extend(drbg.fisher_yates(base)[:remainder])
    return blues


def _mask_to_sorted_indices(mask: int) -> Tuple[int, ...]:
    return tuple(i for i in range(RED_POOL_SIZE) if (mask >> i) & 1)


def _build_abstract_red_masks(
    ticket_count: int, max_local_search_steps: int = 10_000
) -> Tuple[List[int], Dict[str, int]]:
    elem_count = [0] * RED_POOL_SIZE
    pair_count = [[0] * RED_POOL_SIZE for _ in range(RED_POOL_SIZE)]
    masks: List[int] = []
    used_masks: Set[int] = set()

    for _ in range(ticket_count):
        chosen: List[int] = []
        chosen_set: Set[int] = set()
        for _ in range(RED_PICK_COUNT):
            min_c = min(elem_count[i] for i in range(RED_POOL_SIZE) if i not in chosen_set)
            candidates = [
                i for i in range(RED_POOL_SIZE) if i not in chosen_set and elem_count[i] == min_c
            ]
            best = min(
                candidates,
                key=lambda v: (sum(pair_count[v][u] for u in chosen), sum(pair_count[v]), v),
            )
            chosen.append(best)
            chosen_set.add(best)

        mask = 0
        for u in chosen:
            mask |= 1 << u

        if mask in used_masks:
            broken = False
            for idx_out in chosen:
                for idx_in in sorted(
                    (i for i in range(RED_POOL_SIZE) if i not in chosen_set),
                    key=lambda i: (elem_count[i], i),
                ):
                    cand_mask = (mask ^ (1 << idx_out)) | (1 << idx_in)
                    if cand_mask not in used_masks:
                        chosen.remove(idx_out)
                        chosen.append(idx_in)
                        chosen_set.remove(idx_out)
                        chosen_set.add(idx_in)
                        mask = cand_mask
                        broken = True
                        break
                if broken:
                    break

        masks.append(mask)
        used_masks.add(mask)
        for u in chosen:
            elem_count[u] += 1
        for i in range(RED_PICK_COUNT):
            for j in range(i + 1, RED_PICK_COUNT):
                u, v = chosen[i], chosen[j]
                pair_count[u][v] += 1
                pair_count[v][u] += 1

    steps_evaluated = 0
    swaps_accepted = 0
    n = len(masks)
    improved = True
    while improved and steps_evaluated < max_local_search_steps:
        improved = False
        for a in range(n):
            if steps_evaluated >= max_local_search_steps:
                break
            for b in range(a + 1, n):
                if steps_evaluated >= max_local_search_steps:
                    break
                ma, mb = masks[a], masks[b]
                only_a, only_b = ma & ~mb, mb & ~ma
                if not only_a or not only_b:
                    continue
                a_bits = _mask_to_sorted_indices(only_a)
                b_bits = _mask_to_sorted_indices(only_b)
                best_delta = 0
                best_pair: Tuple[int, int] | None = None
                for x in a_bits:
                    for y in b_bits:
                        steps_evaluated += 1
                        new_a = (ma ^ (1 << x)) | (1 << y)
                        new_b = (mb ^ (1 << y)) | (1 << x)
                        if new_a == new_b or new_a in used_masks or new_b in used_masks:
                            if steps_evaluated >= max_local_search_steps:
                                break
                            continue
                        delta = 0
                        for k in range(n):
                            if k == a or k == b:
                                continue
                            mk = masks[k]
                            o1, o2 = (ma & mk).bit_count(), (mb & mk).bit_count()
                            n1, n2 = (new_a & mk).bit_count(), (new_b & mk).bit_count()
                            delta += (n1 * n1 + n2 * n2) - (o1 * o1 + o2 * o2)
                        if delta < best_delta:
                            best_delta = delta
                            best_pair = (new_a, new_b)
                        if steps_evaluated >= max_local_search_steps:
                            break
                    if steps_evaluated >= max_local_search_steps:
                        break
                if best_pair is not None:
                    new_a, new_b = best_pair
                    used_masks.remove(ma)
                    used_masks.remove(mb)
                    masks[a], masks[b] = new_a, new_b
                    used_masks.add(new_a)
                    used_masks.add(new_b)
                    swaps_accepted += 1
                    improved = True

    final_counts = [sum((m >> i) & 1 for m in masks) for i in range(RED_POOL_SIZE)]
    overlap_sq_sum = 0
    max_overlap = 0
    for a in range(n):
        for b in range(a + 1, n):
            ov = (masks[a] & masks[b]).bit_count()
            overlap_sq_sum += ov * ov
            if ov > max_overlap:
                max_overlap = ov

    return masks, {
        "red_count_spread": max(final_counts) - min(final_counts),
        "pairwise_overlap_sq_sum": overlap_sq_sum,
        "max_pairwise_red_overlap": max_overlap,
        "local_search_steps": steps_evaluated,
        "local_search_swaps": swaps_accepted,
    }


def exact_red_4plus_union_count(red_sets: Sequence[Sequence[int]]) -> int:
    """
    P1-B07 精确计算 |⋃_i {D in C(33,6) : |D ∩ T_i| >= 4}|：
    每注 T_i 对应恰好 5,428 个 33-bit 开奖结果掩码，取全批精确集合并集。
    与暴力枚举全部 1,107,568 种开奖结果在数学上严格恒等，N<=15 仅需约 25ms。
    """
    all_bits = [1 << i for i in range(RED_POOL_SIZE)]
    winning_draws: Set[int] = set()

    for reds in red_sets:
        t_bits = [1 << (r - 1) for r in reds]
        t_mask = 0
        for b in t_bits:
            t_mask |= b
        comp_bits = [b for b in all_bits if not (t_mask & b)]

        # 命中 6 红 (1 种)
        winning_draws.add(t_mask)

        # 命中 5 红 (6 * 27 = 162 种)
        for drop_b in t_bits:
            base5 = t_mask ^ drop_b
            for add_b in comp_bits:
                winning_draws.add(base5 | add_b)

        # 命中 4 红 (15 * 351 = 5,265 种)
        comp_pairs = [a | b for a, b in itertools.combinations(comp_bits, 2)]
        for drop_a, drop_b in itertools.combinations(t_bits, 2):
            base4 = t_mask ^ drop_a ^ drop_b
            for pair_mask in comp_pairs:
                winning_draws.add(base4 | pair_mask)

    return len(winning_draws)


def compute_exact_batch_coverage(
    red_sets: Sequence[Sequence[int]], blues: Sequence[int]
) -> Tuple[float, int, str]:
    """
    P1-B07 精确批次中奖覆盖率：
    - N >= 16 (全16蓝覆盖): 精确 1.0
    - N <= 15 (蓝球无放回): P = N/16 + (1 - N/16) * (exact_union_count / 1,107,568)
    """
    n = len(red_sets)
    distinct_blues = len(set(blues))
    if distinct_blues == BLUE_POOL_SIZE:
        return 1.0, TOTAL_RED_COMBINATIONS, "EXACT_FULL_BLUE_COVERAGE_NO_PROFIT_GUARANTEE"
    if distinct_blues != n:
        raise RuntimeError("INVARIANT_VIOLATION: Blues must be distinct when N < 16.")

    union_cnt = exact_red_4plus_union_count(red_sets)
    if n <= 5:
        expected_cnt = n * 5428
        if union_cnt != expected_cnt:
            raise RuntimeError(
                f"EXACT_COVERAGE_SANITY_FAIL: disjoint union {union_cnt} != {expected_cnt}"
            )
    u_prob = union_cnt / TOTAL_RED_COMBINATIONS
    exact_prob = (n / BLUE_POOL_SIZE) + ((BLUE_POOL_SIZE - n) / BLUE_POOL_SIZE) * u_prob
    return exact_prob, union_cnt, "EXACT_ENUMERATED_BATCH_COVERAGE"


@dataclass(frozen=True)
class V4SamplingOutput:
    tickets: List[Dict[str, object]]
    prize_coverage: float
    exact_red_4plus_union_count: int
    prize_coverage_note: str
    first_prize_space_share: str
    coverage_diagnostics: Dict[str, int]


def generate_v4_tickets(
    ticket_count: int,
    drbg: DomainSeparatedDRBG,
    max_local_search_steps: int = 10_000,
) -> V4SamplingOutput:
    if not isinstance(ticket_count, int) or ticket_count < 1:
        raise ValueError("ticket_count must be a positive integer.")
    if ticket_count > TOTAL_FULL_SPACE:
        raise ValueError("ticket_count exceeds total legal SSQ space.")

    blues = _sample_blue_balls(ticket_count, drbg)
    if ticket_count <= 5:
        shuffled_reds = drbg.fisher_yates(list(range(1, RED_POOL_SIZE + 1)))
        red_sets = [
            sorted(shuffled_reds[i * RED_PICK_COUNT : (i + 1) * RED_PICK_COUNT])
            for i in range(ticket_count)
        ]
        diagnostics = {
            "red_count_spread": 1,
            "pairwise_overlap_sq_sum": 0,
            "max_pairwise_red_overlap": 0,
            "local_search_steps": 0,
            "local_search_swaps": 0,
        }
    else:
        abstract_masks, diagnostics = _build_abstract_red_masks(
            ticket_count=ticket_count,
            max_local_search_steps=max_local_search_steps,
        )
        perm_s33 = drbg.fisher_yates(list(range(1, RED_POOL_SIZE + 1)))
        red_sets = [
            sorted(perm_s33[idx] for idx in _mask_to_sorted_indices(mask))
            for mask in abstract_masks
        ]
        order = drbg.fisher_yates(list(range(ticket_count)))
        red_sets = [red_sets[i] for i in order]

    tickets: List[Dict[str, object]] = []
    seen_full: Set[Tuple[Tuple[int, ...], int]] = set()
    for reds, blue in zip(red_sets, blues):
        key = (tuple(reds), blue)
        if key in seen_full:
            raise RuntimeError("INVARIANT_VIOLATION: Duplicate full 6+1 ticket detected.")
        seen_full.add(key)
        tickets.append(
            {
                "reds": reds,
                "blue": blue,
                "formatted": f"{' '.join(f'{r:02d}' for r in reds)} + {blue:02d}",
            }
        )

    prize_cov, union_cnt, cov_note = compute_exact_batch_coverage(red_sets, blues)
    return V4SamplingOutput(
        tickets=tickets,
        prize_coverage=round(prize_cov, 10),
        exact_red_4plus_union_count=union_cnt,
        prize_coverage_note=cov_note,
        first_prize_space_share=f"{ticket_count} / {TOTAL_FULL_SPACE}",
        coverage_diagnostics=diagnostics,
    )
