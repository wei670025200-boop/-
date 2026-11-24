# SSQ V4 PRODUCTION SPEC — DRAFT FREEZE CANDIDATE

## 1. Scope
Production-only system. It does not claim predictive edge and does not modify PP-AS-1.1.

Production mode:
`UNIFORM_MARGINAL_BALANCED_COVERAGE`

Primary goals:
1. Preserve exact single-ticket marginal uniformity.
2. Increase within-batch blue coverage.
3. Reduce redundant red exposure without unbounded rejection loops.
4. Fail closed on schedule, suspension, source and promotion-eligibility uncertainty.

## 2. Mathematical invariants

### 2.1 Single-ticket marginal
Every published 6+1 ticket must have the exact same marginal distribution as uniform sampling from the legal SSQ space.

For red construction:
- First construct deterministic abstract 6-subsets over labels {1,...,33}.
- Then draw one globally uniform permutation pi in S_33 using the production CSPRNG.
- Apply the same pi to every abstract red block.
- Randomly permute batch ticket positions after mapping.

For any fixed abstract 6-set A and any real 6-set B:
P(pi(A)=B)=1/C(33,6).

Thus every red ticket marginal is exactly uniform even if the abstract coverage optimizer is deterministic and label-biased.

For blue construction:
- Each round starts from a fresh uniform random permutation of 01..16.
- A partial round takes the first r elements.
- Therefore each ticket-position blue marginal is exactly uniform.
- Final red/blue pairing order is separately randomized.

### 2.2 Batch distribution
The batch is intentionally NOT IID.
It is a negatively correlated / balanced-coverage joint distribution.

This changes prize-frequency variance and coverage, not the jackpot probability of any individual ticket.

### 2.3 Jackpot probability
If K distinct legal tickets are purchased for one draw:
P(at least one jackpot)=K/17,721,088.
No red-overlap or blue-coverage rule changes this.

## 3. Red construction

### N <= 5
Use five or fewer disjoint abstract 6-label slices, then apply the global uniform S_33 permutation.
Invariant: pairwise red overlap = 0.

### N > 5
Use bounded abstract balanced construction:
1. Minimize current abstract label-use count.
2. Secondary objective: minimize incremental repeated-pair concentration.
3. Duplicate repair must be bounded.
4. No acceptance loop may run without a hard cap.
5. Runtime ticket cap must be explicit.

The optimizer is allowed to be deterministic because all real-number bias is removed by the final global random permutation.

## 4. Blue construction
For N <= 16:
- uniform random permutation of 01..16;
- use N distinct blue numbers.

For N > 16:
- repeat independent 16-number permutation rounds;
- all blue usage counts differ by at most one.

Consequences:
- N=16 gives full blue coverage for that draw;
- this guarantees at least one ticket has the winning blue number, not that the batch is profitable;
- production language must use PRIZE_COVERAGE, never PROFIT_GUARANTEE.

## 5. Cross-year issue handling
Forbidden:
`previous_issue = target_issue - 1`

Required:
- derive latest completed issue from at least two agreeing official sources;
- validate target schedule separately;
- issue rollover is therefore safe across calendar years.

## 6. Holiday / suspension gate
Weekday and clock-time checks are insufficient.
Production must require an official market-status field:
`suspension_status = NO_SUSPENSION_CONFIRMED`
and
`sales_open = true`.

If holiday/suspension state is unknown:
FAIL CLOSED.

## 7. Official-source policy
Production requires at least two independent official sources to agree on the latest completed draw.

The implementation must not permanently hard-code Shanghai+Sichuan as the only valid pair.
A future source registry may provide 2-of-N official-source quorum.

No third-party source can satisfy the authoritative quorum.

## 8. Promotion / draw-EV layer
Promotion uplift may enter any EV calculation only when all eligibility facts are verified from the official activity notice.

Required promotion fields:
- active
- source_verified
- source_url
- source_sha256
- fetched_at_utc
- eligible_bet_types
- eligible_prize_tiers
- min_physical_ticket_amount_rmb (nullable)
- requires_same_physical_ticket
- activity_start/end or issue scope
- stacking / exclusion rules if published

Unknown eligibility => `UNKNOWN_BLOCK_EV_UPLIFT`.

Planned eligibility is not final eligibility.
The issued physical ticket / receipt must later confirm that the purchase was structured as planned.

Never hard-code “20 RMB” or any other threshold as a permanent SSQ rule. Thresholds are promotion-specific.

## 9. Production vs research separation
PP-AS-1.1 remains frozen and failed.
No PP-AS-1.1 parameter or threshold may be changed in V4.

PP-AS-1.2 is a separate research epoch.
No PP-AS-1.2 output may affect production unless that protocol later passes both its behavior gate and economic gate.

## 10. Naming
Current V4 production route:
`UNIFORM_MARGINAL_BALANCED_COVERAGE`

If a future validated popularity filter makes blue/red selection non-uniform, the route name and protocol version must change. It must not continue to claim UNIFORM_MARGINAL.
