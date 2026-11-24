from __future__ import annotations

import hashlib
import json
import math
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

RED_COUNT = 33
RED_PER_TICKET = 6
BLUE_COUNT = 16
PRICE_PER_SINGLE_RMB = 2
MAX_TICKETS_PER_RUN = 10_000

MODE = "UNIFORM_MARGINAL_BALANCED_COVERAGE"
RNG_ID = "OS_CSPRNG_256_SEED_SHA256_COUNTER_REJECTION_V1"
RED_CONSTRUCTION_ID = "ABSTRACT_BALANCED_BLOCKS_THEN_GLOBAL_UNIFORM_S33_PERMUTATION_V1"
BLUE_CONSTRUCTION_ID = "BALANCED_WITHOUT_REPLACEMENT_ROUNDS_V1"


class V4Error(ValueError):
    pass


class HashDRBG:
    """Domain-separated SHA-256 counter stream seeded from 256 OS-CSPRNG bits."""

    def __init__(self, seed: bytes, domain: bytes):
        if len(seed) != 32:
            raise V4Error("seed must be exactly 32 bytes")
        self._key = hashlib.sha256(seed + b"|" + domain).digest()
        self._counter = 0

    def _word256(self) -> int:
        if self._counter >= (1 << 128):
            raise V4Error("DRBG counter exhausted")
        msg = self._key + self._counter.to_bytes(16, "big")
        self._counter += 1
        return int.from_bytes(hashlib.sha256(msg).digest(), "big")

    def randbelow(self, n: int) -> int:
        if not isinstance(n, int) or n <= 0:
            raise V4Error("randbelow requires positive integer")
        space = 1 << 256
        limit = space - (space % n)
        # Rejection probability is < n/2^256. The cap is defensive, not statistical.
        for _ in range(1024):
            v = self._word256()
            if v < limit:
                return v % n
        raise RuntimeError("DRBG rejection cap exceeded")

    def shuffle(self, values: list[Any]) -> None:
        for i in range(len(values) - 1, 0, -1):
            j = self.randbelow(i + 1)
            values[i], values[j] = values[j], values[i]


def _pair_delta(pair_counts: list[list[int]], chosen: list[int], x: int) -> int:
    # Increment in sum(pair_count^2) if x is added beside every already chosen label.
    d = 0
    for y in chosen:
        a, b = sorted((x, y))
        c = pair_counts[a][b]
        d += 2 * c + 1
    return d


def _block_increment_score(
    block: tuple[int, ...],
    counts: list[int],
    pair_counts: list[list[int]],
) -> tuple[int, int, tuple[int, ...]]:
    after = counts.copy()
    for x in block:
        after[x] += 1
    count_range = max(after) - min(after)

    pair_delta = 0
    for i in range(len(block)):
        for j in range(i + 1, len(block)):
            a, b = block[i], block[j]
            c = pair_counts[a][b]
            pair_delta += 2 * c + 1
    return count_range, pair_delta, block


def _repair_duplicate_block(
    block: tuple[int, ...],
    seen: set[tuple[int, ...]],
    counts: list[int],
    pair_counts: list[list[int]],
) -> tuple[int, ...]:
    candidates: list[tuple[tuple[int, int, tuple[int, ...]], tuple[int, ...]]] = []
    block_set = set(block)
    for old in block:
        for new in range(RED_COUNT):
            if new in block_set:
                continue
            repaired = tuple(sorted((block_set - {old}) | {new}))
            if repaired in seen:
                continue
            candidates.append((_block_increment_score(repaired, counts, pair_counts), repaired))
    if candidates:
        candidates.sort(key=lambda z: z[0])
        return candidates[0][1]

    # Bounded deterministic fallback. It is intentionally label-biased here;
    # the final global uniform S_33 permutation removes real-number bias.
    tested = 0
    for a in range(RED_COUNT - 5):
        for b in range(a + 1, RED_COUNT - 4):
            for c in range(b + 1, RED_COUNT - 3):
                for d in range(c + 1, RED_COUNT - 2):
                    for e in range(d + 1, RED_COUNT - 1):
                        for f in range(e + 1, RED_COUNT):
                            tested += 1
                            cand = (a, b, c, d, e, f)
                            if cand not in seen:
                                return cand
                            if tested >= 100_000:
                                raise RuntimeError("bounded duplicate-repair search exhausted")
    raise RuntimeError("no unique abstract red block available")


def construct_abstract_red_blocks(ticket_count: int) -> list[tuple[int, ...]]:
    """
    Deterministic abstract construction. Real labels are not used here.

    N<=5: disjoint six-label slices.
    N>5: lexicographically minimize label-use count first and repeated-pair
    concentration second. Every loop is bounded; there is no acceptance while-loop.
    """
    if not isinstance(ticket_count, int) or ticket_count < 1:
        raise V4Error("ticket_count must be a positive integer")
    if ticket_count > MAX_TICKETS_PER_RUN:
        raise V4Error(f"ticket_count exceeds runtime cap {MAX_TICKETS_PER_RUN}")

    if ticket_count <= 5:
        return [
            tuple(range(i * RED_PER_TICKET, (i + 1) * RED_PER_TICKET))
            for i in range(ticket_count)
        ]

    counts = [0] * RED_COUNT
    pair_counts = [[0] * RED_COUNT for _ in range(RED_COUNT)]
    blocks: list[tuple[int, ...]] = []
    seen: set[tuple[int, ...]] = set()

    for _ in range(ticket_count):
        chosen: list[int] = []
        for _slot in range(RED_PER_TICKET):
            options = []
            for x in range(RED_COUNT):
                if x in chosen:
                    continue
                options.append((counts[x], _pair_delta(pair_counts, chosen, x), x))
            _, _, x = min(options)
            chosen.append(x)

        block = tuple(sorted(chosen))
        if block in seen:
            block = _repair_duplicate_block(block, seen, counts, pair_counts)

        seen.add(block)
        blocks.append(block)
        for x in block:
            counts[x] += 1
        for i in range(RED_PER_TICKET):
            for j in range(i + 1, RED_PER_TICKET):
                a, b = sorted((block[i], block[j]))
                pair_counts[a][b] += 1

    return blocks


def _mapped_red_tickets(blocks: list[tuple[int, ...]], seed: bytes) -> list[list[int]]:
    # A single uniform global permutation pi in S_33 is applied to all abstract blocks.
    # For every fixed abstract 6-set, pi(set) is exactly uniform over C(33,6).
    labels = list(range(1, RED_COUNT + 1))
    map_rng = HashDRBG(seed, b"SSQ-V4|RED-GLOBAL-S33-PERM")
    map_rng.shuffle(labels)

    mapped = [sorted(labels[x] for x in block) for block in blocks]

    # Randomize ticket position separately so deterministic abstract block order is hidden.
    order_rng = HashDRBG(seed, b"SSQ-V4|RED-TICKET-ORDER")
    order_rng.shuffle(mapped)
    return mapped


def _balanced_blue_sequence(ticket_count: int, seed: bytes) -> list[int]:
    """
    Every complete round covers 01..16 exactly once.
    A partial round is a uniform sample without replacement.
    Hence all blue usage counts differ by at most one.
    """
    rng = HashDRBG(seed, b"SSQ-V4|BLUE-BALANCED-ROUNDS")
    out: list[int] = []
    remaining = ticket_count
    while remaining:
        round_values = list(range(1, BLUE_COUNT + 1))
        rng.shuffle(round_values)
        take = min(remaining, BLUE_COUNT)
        out.extend(round_values[:take])
        remaining -= take

    pair_rng = HashDRBG(seed, b"SSQ-V4|BLUE-PAIRING-ORDER")
    pair_rng.shuffle(out)
    return out


def _red_usage(tickets: Iterable[list[int]]) -> dict[int, int]:
    counts = {i: 0 for i in range(1, RED_COUNT + 1)}
    for t in tickets:
        for x in t:
            counts[x] += 1
    return counts


def _blue_usage(blues: Iterable[int]) -> dict[int, int]:
    counts = {i: 0 for i in range(1, BLUE_COUNT + 1)}
    for x in blues:
        counts[x] += 1
    return counts


def generate_balanced_batch(
    ticket_count: int,
    *,
    seed: bytes | None = None,
    request_time_utc: str | None = None,
) -> dict[str, Any]:
    if not isinstance(ticket_count, int) or ticket_count < 1:
        raise V4Error("ticket_count must be a positive integer")
    if ticket_count > MAX_TICKETS_PER_RUN:
        raise V4Error(f"ticket_count exceeds runtime cap {MAX_TICKETS_PER_RUN}")

    seed = seed if seed is not None else secrets.token_bytes(32)
    if len(seed) != 32:
        raise V4Error("seed must be exactly 32 bytes")

    abstract = construct_abstract_red_blocks(ticket_count)
    reds = _mapped_red_tickets(abstract, seed)
    blues = _balanced_blue_sequence(ticket_count, seed)

    tickets = [
        {"reds": reds[i], "blue": blues[i]}
        for i in range(ticket_count)
    ]

    keys = {(tuple(t["reds"]), t["blue"]) for t in tickets}
    if len(keys) != ticket_count:
        raise RuntimeError("duplicate complete ticket generated")

    red_counts = _red_usage(reds)
    blue_counts = _blue_usage(blues)

    if ticket_count <= 5:
        red_sets = [set(r) for r in reds]
        for i in range(ticket_count):
            for j in range(i + 1, ticket_count):
                if red_sets[i] & red_sets[j]:
                    raise RuntimeError("N<=5 red disjointness invariant violated")

    if max(blue_counts.values()) - min(blue_counts.values()) > 1:
        raise RuntimeError("blue balance invariant violated")

    rec = {
        "schema": "SSQ-V4-BALANCED-COVERAGE-BATCH-v1",
        "mode": MODE,
        "rng_id": RNG_ID,
        "red_construction_id": RED_CONSTRUCTION_ID,
        "blue_construction_id": BLUE_CONSTRUCTION_ID,
        "request_time_utc": request_time_utc or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "ticket_count": ticket_count,
        "budget_rmb": ticket_count * PRICE_PER_SINGLE_RMB,
        "tickets": tickets,
        "red_usage": red_counts,
        "blue_usage": blue_counts,
        "red_usage_range": max(red_counts.values()) - min(red_counts.values()),
        "blue_usage_range": max(blue_counts.values()) - min(blue_counts.values()),
        "blue_full_coverage": ticket_count >= BLUE_COUNT,
        "prediction_edge_claim": "NONE",
        "jackpot_probability_per_distinct_ticket": "1/17721088",
        "batch_distribution": "NON_IID_CONSTRAINED; EACH SINGLE-TICKET MARGINAL EXACTLY UNIFORM",
        "seed_sha256": hashlib.sha256(seed).hexdigest(),
    }
    canon = json.dumps(rec, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    rec["output_sha256"] = hashlib.sha256(canon).hexdigest()
    return rec


@dataclass(frozen=True)
class PromotionRule:
    active: bool
    source_verified: bool
    source_url: str | None = None
    source_sha256: str | None = None
    fetched_at_utc: str | None = None
    min_physical_ticket_amount_rmb: int | None = None
    eligible_bet_types: tuple[str, ...] = ()
    eligible_prize_tiers: tuple[str, ...] = ()
    requires_same_physical_ticket: bool = True


def build_physical_slip_plan(ticket_count: int, slip_sizes: list[int]) -> list[list[int]]:
    if sum(slip_sizes) != ticket_count or any((not isinstance(x, int) or x <= 0) for x in slip_sizes):
        raise V4Error("slip_sizes must be positive integers summing to ticket_count")
    groups: list[list[int]] = []
    cursor = 0
    for size in slip_sizes:
        groups.append(list(range(cursor, cursor + size)))
        cursor += size
    return groups


def evaluate_promotion_eligibility(
    ticket_count: int,
    slip_sizes: list[int],
    rule: PromotionRule,
    *,
    bet_type: str = "single",
) -> dict[str, Any]:
    """
    Pre-purchase eligibility planning only.
    Actual eligibility must be confirmed from the issued physical ticket/receipt.
    """
    groups = build_physical_slip_plan(ticket_count, slip_sizes)

    if not rule.active:
        return {
            "status": "NO_ACTIVE_PROMOTION",
            "eligible_ticket_indices": [],
            "requires_purchase_receipt_confirmation": False,
        }

    if not rule.source_verified or not rule.source_url or not rule.source_sha256:
        return {
            "status": "UNKNOWN_BLOCK_EV_UPLIFT",
            "reason": "promotion rule source is not verified",
            "eligible_ticket_indices": [],
            "requires_purchase_receipt_confirmation": True,
        }

    if rule.eligible_bet_types and bet_type not in rule.eligible_bet_types:
        return {
            "status": "NOT_ELIGIBLE_BET_TYPE",
            "eligible_ticket_indices": [],
            "requires_purchase_receipt_confirmation": True,
        }

    qualifying: list[int] = []
    threshold = rule.min_physical_ticket_amount_rmb
    for group in groups:
        amount = len(group) * PRICE_PER_SINGLE_RMB
        if threshold is None or amount >= threshold:
            qualifying.extend(group)

    return {
        "status": "PLANNED_ELIGIBLE" if qualifying else "PLANNED_NOT_ELIGIBLE",
        "eligible_ticket_indices": qualifying,
        "eligible_count": len(qualifying),
        "physical_slips": [
            {
                "ticket_indices": group,
                "amount_rmb": len(group) * PRICE_PER_SINGLE_RMB,
                "meets_min_amount": threshold is None or len(group) * PRICE_PER_SINGLE_RMB >= threshold,
            }
            for group in groups
        ],
        "eligible_prize_tiers": list(rule.eligible_prize_tiers),
        "requires_purchase_receipt_confirmation": True,
        "warning": "Planned eligibility is not proof of issued-ticket eligibility.",
    }


def validate_production_context(context: dict[str, Any], now_utc: datetime) -> dict[str, Any]:
    """
    Fail-closed production gate.
    Crucially, the previous issue is derived from official completed-history evidence,
    never by integer subtraction, so cross-year issue rollover is safe.
    """
    sources = context.get("official_sources") or []
    if len(sources) < 2:
        raise V4Error("at least two official sources required")

    completed = []
    for src in sources:
        if not src.get("source_verified"):
            raise V4Error("unverified official source")
        completed.append(
            (
                str(src.get("latest_completed_issue")),
                str(src.get("latest_completed_draw_date")),
                tuple(src.get("latest_completed_reds") or []),
                int(src.get("latest_completed_blue")),
            )
        )

    if len(set(completed)) != 1:
        raise V4Error("official sources disagree on latest completed draw")

    schedule = context.get("target_schedule") or {}
    target_issue = str(schedule.get("target_issue") or "")
    draw_at_raw = schedule.get("draw_at_utc")
    if not target_issue.isdigit() or not draw_at_raw:
        raise V4Error("verified target schedule is required")
    if not schedule.get("source_verified"):
        raise V4Error("target schedule source is not verified")

    draw_at = datetime.fromisoformat(draw_at_raw.replace("Z", "+00:00"))
    if draw_at.tzinfo is None:
        raise V4Error("draw_at_utc must be timezone-aware")
    if now_utc.tzinfo is None:
        raise V4Error("now_utc must be timezone-aware")
    if now_utc >= draw_at:
        raise V4Error("draw already started or passed")

    market = context.get("market_status") or {}
    if market.get("sales_open") is not True:
        raise V4Error("sales are not confirmed open")
    if market.get("suspension_status") != "NO_SUSPENSION_CONFIRMED":
        raise V4Error("holiday/suspension status not explicitly cleared")
    if not market.get("source_verified"):
        raise V4Error("market-status source is not verified")

    return {
        "status": "PASS",
        "target_issue": target_issue,
        "latest_completed_issue": completed[0][0],
        "latest_completed_draw_date": completed[0][1],
        "draw_at_utc": draw_at.isoformat(),
        "cross_year_safe": True,
        "holiday_suspend_gate": "PASS",
        "official_source_count": len(sources),
    }
