# PP-AS-1.2 — Blue Popularity / Jackpot Sharing Pre-Registration

Status: PRE-REGISTERED RESEARCH PROTOCOL — NOT PRODUCTION AUTHORIZATION

This protocol is a new research epoch. It does not amend, reopen, or supersede the failed PP-AS-1.1 result.

## 1. Research question

Does the observed popularity of the drawn blue number contain a stable, prospectively reproducible crowding signal, and if so, is avoiding crowded blue numbers economically material for first-prize conditional payout?

The protocol explicitly separates:

1. BEHAVIOR GATE — is crowding predictable/reproducible?
2. ECONOMIC GATE — would exploiting the crowding signal materially improve jackpot-conditional expected payout?

Both gates must pass before any production popularity filter may be proposed.

Statistical significance alone is insufficient.

## 2. Scope and non-goals

Not a draw-prediction model.
Not a claim that any blue number is more/less likely to be drawn.
Not allowed to change the marginal draw probability assigned to blue balls.

The researched quantity is bettor-selection crowding conditional on which blue number is drawn.

## 3. Data window

Initial evidence target: the existing audited 600-draw SSQ historical window, extended only as necessary to add official sixth-prize counts and promotion metadata for the same issue set.

Every row must retain:
- issue
- draw date
- drawn blue
- total sales RMB
- sixth-prize winning-ticket count
- promotion / sixth-prize bonus status
- official source URL
- raw response SHA-256
- fetch timestamp

Rows with unresolved promotion classification or unverifiable sixth-prize count are excluded by a pre-specified data-quality rule and reported, never silently imputed.

## 4. Blue crowding proxy

Raw proxy:

B_t = sixth_prize_count_t / (sales_rmb_t / 2)

This is not asserted to equal the exact share of bettors who selected the drawn blue number, because:
- tickets with the drawn blue and sufficiently many red matches are paid at a higher tier;
- multiple / system bets and multipliers can alter ticket-count interpretation;
- promotion mechanics can change reported sixth-prize counts or payouts.

Therefore B_t is treated as a high-signal proxy for realized blue crowding, not a literal bettor-share measurement.

Primary analysis excludes or separately models promotion periods that mechanically alter sixth-prize counting/payout semantics.

## 5. Behavior model

### 5.1 Primary low-degree model
Because ~600 draws give only ~37.5 realizations per blue number on average, the primary model is deliberately low-dimensional.

Primary predictors:
- blue-number fixed effect (16 levels, regularized / hierarchical shrinkage)
- calendar-year or rule-regime fixed effect
- log(total ticket count) only if residual diagnostics show scale dependence
- promotion-status control

No omission, streak, hot/cold, lunar, zodiac, deep-learning, or high-order dynamic features are allowed in the primary gate.

### 5.2 Optional dynamic extension
Dynamic features may be analyzed only after the primary gate is locked and only as descriptive secondary research.
They cannot rescue a failed primary behavior gate in this protocol.

## 6. Behavior-gate split

Chronological split:
- first 200 valid rows: initial estimation
- remaining >=400 valid rows: true OOS evaluation

Model is refit on an expanding window at a fixed, pre-declared cadence.

Primary OOS outputs:
1. rank correlation between predicted crowding and realized B_t;
2. top-vs-bottom predicted crowding ratio;
3. calibration slope / monotonicity across predicted-crowding quantiles.

Behavior gate PASS requires ALL:
- OOS Spearman rho >= 0.50;
- two-sided block-bootstrap 95% CI lower bound for rho > 0;
- top predicted crowding quintile / bottom quintile realized B ratio >= 1.10;
- monotone realized crowding across predicted quintiles in at least 4 of 5 adjacent steps;
- no single issue contributes >20% of the absolute OOS loss/improvement statistic.

Block bootstrap uses 12-draw moving blocks, 2,000 replicates, with a seed committed before model fitting.

## 7. Robustness and influence

No post-hoc trimming.

Before fitting, define the robust primary response treatment as:
- winsorize log(B_t + epsilon) at the 1st and 99th percentile estimated ONLY from the current training window;
- epsilon fixed at 1e-9.

Sensitivity analyses:
- no winsorization;
- Huber loss;
- leave-one-out maximum influence.

Sensitivity analyses are descriptive and cannot replace the primary result.

## 8. Economic model

The economic gate concerns ONLY first-prize conditional payout.

Lower fixed prize tiers do not create a blue-number sharing benefit.
Second prize does not depend on matching the blue number.

For every historical issue, reconstruct the current-rule-compatible first-prize payout environment using official:
- pre-draw pool
- current sales
- first-prize winner count
- applicable prize-rule regime
- applicable promotion regime
- official per-first-prize payout when published

The model must distinguish:
- capped periods where an additional co-winner would not reduce per-winner payout;
- sharing-sensitive periods where payout changes with winner count / distributable amount;
- promotion-specific first-prize mechanics.

No hard-coded assumption that “high pool always means 10M”.

## 9. Counterfactual crowd-sharing model

The economic analysis is conditional on the event that our ticket matches all 6 red numbers and the blue number.

For each OOS issue:
1. use the behavior model to estimate relative crowding for all 16 blue choices;
2. compare the actual-drawn-blue crowding percentile with a policy that selects among the least-crowded eligible blue choices;
3. map crowding differences to a pre-registered co-winner-count model;
4. recompute first-prize payout using the applicable official prize formula / published rule regime.

Because historical data do not identify exact blue-specific jackpot co-winner counts directly, the mapping from blue crowding to jackpot co-winner count must be explicitly parameterized and stress-tested.

Primary mapping is conservative:
- elasticity of jackpot co-winner count to blue crowding = 1.0;
- total expected co-winner count anchored to observed issue-level first-prize winner frequency.

Sensitivity elasticities: 0.5 and 1.5, descriptive only.

## 10. Economic gate

Primary statistic:

Conditional Jackpot Payout Uplift =
E[payout under cold-blue policy | jackpot]
/
E[payout under uniform-blue policy | jackpot]
- 1

Economic gate PASS requires ALL:
- OOS conditional uplift >= 2.0%;
- 95% block-bootstrap CI lower bound > 0;
- uplift remains >= 1.0% under conservative elasticity 0.5;
- at least 25% of OOS issues are genuinely sharing-sensitive under the reconstructed prize regime;
- no single issue contributes >20% of total modeled uplift.

If behavior gate passes but economic gate fails:
status = BEHAVIOR_SIGNAL_ONLY_NO_PRODUCTION_VALUE.

If economic gate cannot be identified credibly from available official data:
status = ECONOMIC_GATE_UNIDENTIFIED, not PASS.

## 11. Production eligibility

PP-AS-1.2 cannot affect V4 production unless:
- behavior gate PASS;
- economic gate PASS;
- independent code review PASS;
- new production protocol/version is created;
- route name changes away from UNIFORM_MARGINAL if blue probabilities become non-uniform.

At N=16 with complete blue coverage, a blue popularity ranking has no selection effect and MUST NOT change the batch.

At N<16, any popularity-biased blue selection would break uniform blue marginality and therefore requires a new production route and explicit user-facing disclosure.

## 12. Multiple testing / model search

Primary model and thresholds above are frozen before first official PP-AS-1.2 fit.

No alternate model may replace a failed primary model within PP-AS-1.2.
Any materially different model requires PP-AS-1.3 or a separately pre-registered epoch.

## 13. Failure exits

- DATA_BLOCKED
- BEHAVIOR_GATE_FAILED
- BEHAVIOR_SIGNAL_ONLY_NO_PRODUCTION_VALUE
- ECONOMIC_GATE_UNIDENTIFIED
- ECONOMIC_GATE_FAILED
- QUALIFIED_FOR_SEPARATE_PRODUCTION_REVIEW

The final state QUALIFIED_FOR_SEPARATE_PRODUCTION_REVIEW is not itself permission to activate a filter.
