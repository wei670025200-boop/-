"""
rules_and_ev_v4.py
封口：P1-B03 (2026新规15亿进/3亿退滞回状态机 + 福运奖),
      P1-B04 (删除人为DRAW_EV_SCORE，输出结构化DRAW_EV_CONTEXT),
      P1-B05 (physical_slip_plan 精确分票 + 出票后RECEIPT_CONFIRMED升级)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Set

from registry_and_parsers_v4 import (
    GateBlockedError,
    PromoEvidence,
    ScheduleEvidence,
    parse_issue_tuple,
)

BJT = timezone(timedelta(hours=8))
POOL_HIGH_TIER_RMB = 100_000_000.0
POOL_SPECIAL_ENTER_RMB = 1_500_000_000.0
POOL_SPECIAL_EXIT_RMB = 300_000_000.0


def verify_schedule_and_continuity(
    target_issue: str,
    draw_at_utc_str: str,
    latest_completed_draw: Dict[str, Any],
    schedule: ScheduleEvidence,
    now_utc: datetime | None = None,
) -> datetime:
    """使用官方 Schedule Manifest 核验开奖日历、休市日、开奖前时间锁与跨年期号连续性。"""
    draw_dt_utc = datetime.fromisoformat(draw_at_utc_str.replace("Z", "+00:00"))
    if draw_dt_utc.tzinfo is None:
        raise GateBlockedError("MISSING_TIMEZONE", "draw_at_utc must be timezone-aware.")
    draw_dt_utc = draw_dt_utc.astimezone(timezone.utc)
    draw_dt_bjt = draw_dt_utc.astimezone(BJT)

    if draw_dt_bjt.weekday() not in schedule.valid_draw_weekdays or (
        draw_dt_bjt.hour,
        draw_dt_bjt.minute,
        draw_dt_bjt.second,
    ) != schedule.draw_time_bjt:
        raise GateBlockedError(
            "TARGET_DRAW_SCHEDULE_MISMATCH",
            f"Draw BJT {draw_dt_bjt.isoformat()} violates official schedule manifest.",
        )

    target_date_str = draw_dt_bjt.strftime("%Y-%m-%d")
    if not schedule.suspension_coverage_start_bjt or not schedule.suspension_coverage_end_bjt:
        raise GateBlockedError(
            "SUSPENSION_EVIDENCE_REQUIRED",
            "Annual official suspension evidence is not bound to the schedule.",
        )
    cov_start = date.fromisoformat(schedule.suspension_coverage_start_bjt)
    cov_end = date.fromisoformat(schedule.suspension_coverage_end_bjt)
    if not (cov_start <= draw_dt_bjt.date() <= cov_end):
        raise GateBlockedError(
            "SUSPENSION_COVERAGE_MISSING",
            f"Target date {target_date_str} lies outside verified suspension coverage "
            f"{cov_start.isoformat()}..{cov_end.isoformat()}.",
        )
    if target_date_str in schedule.suspended_dates_bjt:
        raise GateBlockedError(
            "DRAW_DATE_SUSPENDED",
            f"Target date {target_date_str} is in official suspension schedule.",
        )

    current_utc = now_utc or datetime.now(timezone.utc)
    if current_utc >= draw_dt_utc:
        raise GateBlockedError(
            "DRAW_ALREADY_STARTED_OR_PAST",
            f"now={current_utc.isoformat()} >= draw_at_utc={draw_dt_utc.isoformat()}",
        )

    prev_year, prev_seq = parse_issue_tuple(str(latest_completed_draw["issue"]))
    tgt_year, tgt_seq = parse_issue_tuple(target_issue)
    prev_date = date.fromisoformat(str(latest_completed_draw["draw_date_bjt"]))
    tgt_date = draw_dt_bjt.date()

    if tgt_date <= prev_date:
        raise GateBlockedError("PREV_ISSUE_CHAIN_BROKEN", "Target date <= latest draw date.")

    if tgt_year == prev_year:
        if tgt_seq != prev_seq + 1:
            raise GateBlockedError("PREV_ISSUE_CHAIN_BROKEN", "Same-year issue gap detected.")
    elif tgt_year == prev_year + 1:
        if tgt_seq != 1:
            raise GateBlockedError("PREV_ISSUE_CHAIN_BROKEN", "Cross-year first issue must be 001.")
    else:
        raise GateBlockedError("PREV_ISSUE_CHAIN_BROKEN", "Year jump exceeds 1.")

    cursor = prev_date + timedelta(days=1)
    while cursor < tgt_date:
        if not (cov_start <= cursor <= cov_end):
            raise GateBlockedError(
                "SUSPENSION_COVERAGE_MISSING",
                f"Continuity date {cursor.isoformat()} lies outside verified suspension coverage.",
            )
        if (
            cursor.weekday() in schedule.valid_draw_weekdays
            and cursor.isoformat() not in schedule.suspended_dates_bjt
        ):
            raise GateBlockedError(
                "PREV_ISSUE_CHAIN_BROKEN",
                f"Missing completed draw on {cursor.isoformat()} before {tgt_date}.",
            )
        cursor += timedelta(days=1)

    return draw_dt_utc


@dataclass(frozen=True)
class RuleRegimeState:
    special_rule_active_previous: bool
    special_rule_active: bool
    transition: str
    rule_regime: str
    fuyun_prize_active: bool
    first_prize_rule: str
    second_prize_rule: str
    fixed_prize_bonus_rule: str


def evaluate_2026_rule_state_machine(
    pool_after_prev_draw_rmb: float,
    special_rule_active_previous: bool,
) -> RuleRegimeState:
    """
    P1-B03: 2026-02-01 现行双色球滞回规则状态机：
    - 非特别期且上期开奖后奖池 >= 15亿 -> ENTER_SPECIAL（开启特别规定与福运奖）
    - 已在特别期且上期开奖后奖池 >= 3亿 -> KEEP_SPECIAL（保持特别规定，不因跌破15亿退出）
    - 已在特别期且上期开奖后奖池 < 3亿 -> EXIT_SPECIAL（退出特别规定，回落常规档）
    """
    if not isinstance(special_rule_active_previous, bool):
        raise GateBlockedError(
            "MISSING_SPECIAL_RULE_PREV_STATE",
            "special_rule_active_previous must be an explicit boolean from verified state chain.",
        )

    if special_rule_active_previous:
        if pool_after_prev_draw_rmb >= POOL_SPECIAL_EXIT_RMB:
            return RuleRegimeState(
                special_rule_active_previous=True,
                special_rule_active=True,
                transition="KEEP_SPECIAL",
                rule_regime="SPECIAL_RULE_2026_ACTIVE",
                fuyun_prize_active=True,
                first_prize_rule="SPECIAL_2026_POOL_RULE_CAP_10M_PROVISIONS",
                second_prize_rule="SPECIAL_2026_SECOND_PRIZE_FLOATING_WITH_CAP",
                fixed_prize_bonus_rule="FUYUN_PRIZE_ACTIVE",
            )
        transition = "EXIT_SPECIAL"
        active_now = False
    else:
        if pool_after_prev_draw_rmb >= POOL_SPECIAL_ENTER_RMB:
            return RuleRegimeState(
                special_rule_active_previous=False,
                special_rule_active=True,
                transition="ENTER_SPECIAL",
                rule_regime="SPECIAL_RULE_2026_ACTIVE",
                fuyun_prize_active=True,
                first_prize_rule="SPECIAL_2026_POOL_RULE_CAP_10M_PROVISIONS",
                second_prize_rule="SPECIAL_2026_SECOND_PRIZE_FLOATING_WITH_CAP",
                fixed_prize_bonus_rule="FUYUN_PRIZE_ACTIVE",
            )
        transition = "STAY_NORMAL"
        active_now = False

    if pool_after_prev_draw_rmb >= POOL_HIGH_TIER_RMB:
        regime = "NORMAL_POOL_100M_TO_1500M"
        first_rule = "HIGH_POOL_TWO_PART_MAX_5M_EACH_THEORETICAL_10M_CAP"
    else:
        regime = "NORMAL_POOL_UNDER_100M"
        first_rule = "LOW_POOL_SINGLE_PART_MAX_5M_CAP"

    return RuleRegimeState(
        special_rule_active_previous=special_rule_active_previous,
        special_rule_active=active_now,
        transition=transition,
        rule_regime=regime,
        fuyun_prize_active=False,
        first_prize_rule=first_rule,
        second_prize_rule="STANDARD_FLOATING_MAX_5M_CAP",
        fixed_prize_bonus_rule="STANDARD_FIXED_PRIZES_ONLY",
    )


def validate_physical_slip_plan(
    physical_slip_plan: List[Dict[str, Any]],
    ticket_count: int,
    budget_rmb: int,
) -> List[Dict[str, Any]]:
    """
    P1-B05: 验证真实物理出票分票计划：
    - ticket_indices 必须精确划分 0..ticket_count-1，无遗漏无重叠
    - 每张票 amount_rmb 必须等于 2 * len(ticket_indices)
    - 总金额必须等于 budget_rmb
    """
    if not isinstance(physical_slip_plan, list) or not physical_slip_plan:
        raise GateBlockedError("MISSING_PHYSICAL_SLIP_PLAN", "physical_slip_plan must be non-empty.")

    seen_slip_ids: Set[str] = set()
    covered_indices: Set[int] = set()
    total_amount = 0
    normalized: List[Dict[str, Any]] = []

    for slip in physical_slip_plan:
        slip_id = str(slip.get("slip_id", "")).strip()
        if not slip_id or slip_id in seen_slip_ids:
            raise GateBlockedError("INVALID_SLIP_ID", f"Duplicate or empty slip_id: {slip_id!r}")
        seen_slip_ids.add(slip_id)

        indices = slip.get("ticket_indices")
        if not isinstance(indices, list) or not indices or any(not isinstance(i, int) for i in indices):
            raise GateBlockedError("INVALID_SLIP_TICKET_INDICES", f"Bad indices in slip {slip_id}")
        if len(set(indices)) != len(indices):
            raise GateBlockedError("DUPLICATE_INDEX_IN_SLIP", f"Duplicate ticket index in slip {slip_id}")
        if any(i < 0 or i >= ticket_count for i in indices):
            raise GateBlockedError("SLIP_INDEX_OUT_OF_RANGE", f"Index out of range in slip {slip_id}")
        if covered_indices.intersection(indices):
            raise GateBlockedError("OVERLAPPING_SLIP_INDICES", f"Ticket index assigned to >1 slip: {slip_id}")
        covered_indices.update(indices)

        bet_type = str(slip.get("bet_type", "SINGLE"))
        amount_rmb = slip.get("amount_rmb")
        expected_amount = 2 * len(indices)
        if not isinstance(amount_rmb, int) or amount_rmb != expected_amount:
            raise GateBlockedError(
                "SLIP_AMOUNT_MISMATCH",
                f"Slip {slip_id}: amount_rmb={amount_rmb} != 2 * {len(indices)}",
            )
        total_amount += amount_rmb
        normalized.append(
            {
                "slip_id": slip_id,
                "bet_type": bet_type,
                "ticket_indices": sorted(indices),
                "amount_rmb": amount_rmb,
            }
        )

    if len(covered_indices) != ticket_count or total_amount != budget_rmb:
        raise GateBlockedError(
            "SLIP_PLAN_INCOMPLETE_PARTITION",
            f"Covered {len(covered_indices)}/{ticket_count} tickets, amount {total_amount}/{budget_rmb}.",
        )
    return normalized


def build_draw_ev_context(
    pool_after_prev_draw_rmb: float,
    regime: RuleRegimeState,
    promo: PromoEvidence,
    validated_slip_plan: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    P1-B04 & P1-B05: 仅输出结构化 DRAW_EV_CONTEXT，绝不伪造人为 EV 分数。
    开奖前资格状态最高只到 PLANNED_ELIGIBLE。
    """
    if not promo.active_for_target_issue:
        eligibility = "NO_ACTIVE_PROMO"
        eligible_slip_ids: List[str] = []
        ineligible_slip_ids: List[str] = []
    else:
        eligible_slip_ids = []
        ineligible_slip_ids = []
        for slip in validated_slip_plan:
            if (
                slip["bet_type"] in promo.allowed_bet_types
                and slip["amount_rmb"] >= promo.min_single_slip_rmb
            ):
                eligible_slip_ids.append(slip["slip_id"])
            else:
                ineligible_slip_ids.append(slip["slip_id"])

        if len(eligible_slip_ids) == len(validated_slip_plan):
            eligibility = "PLANNED_ELIGIBLE"
        elif eligible_slip_ids:
            eligibility = "PLANNED_PARTIAL_ELIGIBLE"
        else:
            eligibility = "PLANNED_INELIGIBLE"

    fixed_rules = [regime.fixed_prize_bonus_rule]
    if promo.sixth_prize_double_active and promo.remaining_sixth_promo_rmb > 0:
        fixed_rules.append("PROMO_SIXTH_PRIZE_DOUBLE_IF_SLIP_ELIGIBLE")
    second_rule = regime.second_prize_rule
    if promo.second_prize_special_active:
        second_rule = f"{second_rule}+PROMO_SECOND_PRIZE_SPECIAL_IF_SLIP_ELIGIBLE"

    return {
        "rule_regime": regime.rule_regime,
        "rule_transition": regime.transition,
        "special_rule_active_previous": regime.special_rule_active_previous,
        "special_rule_active": regime.special_rule_active,
        "fuyun_prize_active": regime.fuyun_prize_active,
        "pool_rmb": pool_after_prev_draw_rmb,
        "promo_active": promo.active_for_target_issue,
        "promo_eligibility": eligibility,
        "planned_eligible_slip_ids": eligible_slip_ids,
        "planned_ineligible_slip_ids": ineligible_slip_ids,
        "first_prize_rule": regime.first_prize_rule,
        "second_prize_rule": second_rule,
        "fixed_prize_bonus_rule": " | ".join(fixed_rules),
    }


def confirm_physical_slips_post_purchase(
    planned_slip_plan: List[Dict[str, Any]],
    actual_printed_slips: List[Dict[str, Any]],
    promo: PromoEvidence,
) -> Dict[str, Any]:
    """
    P1-B05 出票后复核：对比真实出票凭证与物理分票计划，满足条件才升级为 RECEIPT_CONFIRMED_ELIGIBLE。
    """
    if not promo.active_for_target_issue:
        return {"promo_eligibility": "NO_ACTIVE_PROMO", "confirmed_slip_ids": []}

    planned_map = {s["slip_id"]: s for s in planned_slip_plan}
    confirmed: List[str] = []
    mismatched: List[str] = []

    for actual in actual_printed_slips:
        sid = str(actual.get("slip_id", ""))
        plan = planned_map.get(sid)
        receipt_sha = str(actual.get("terminal_receipt_sha256", ""))
        if (
            plan is None
            or len(receipt_sha) != 64
            or int(actual.get("printed_amount_rmb", -1)) != plan["amount_rmb"]
            or str(actual.get("printed_bet_type", "")) != plan["bet_type"]
            or int(actual.get("printed_amount_rmb", 0)) < promo.min_single_slip_rmb
            or str(actual.get("printed_bet_type", "")) not in promo.allowed_bet_types
        ):
            mismatched.append(sid)
        else:
            confirmed.append(sid)

    if len(confirmed) == len(planned_slip_plan) and not mismatched:
        status = "RECEIPT_CONFIRMED_ELIGIBLE"
    elif confirmed:
        status = "RECEIPT_CONFIRMED_PARTIAL_ELIGIBLE"
    else:
        status = "RECEIPT_MISMATCH_INELIGIBLE"

    return {
        "promo_eligibility": status,
        "confirmed_slip_ids": confirmed,
        "mismatched_slip_ids": mismatched,
    }
