# SSQ V5 LOCAL-FIRST PRODUCTION SPEC — HARDENED

## 0. Active release after merge
Active release: `SSQ_V5_LOCAL_FIRST_HARDENED_1_0`

Default V5 entrypoint: `ssq_v5/runner_v5.py -> run_v5()`

Default V4 fallback entrypoint: `v4/runner_v4.py -> run_v4()`

Shared production safety shell: `v4/local_production_shell.py`

Local data inputs:
- `data/ssq_history.csv`
- `data/local_schedule_manifest.json`
- `data/production_ledger_genesis.json`
- frozen Phase-3 / Phase-4 artifacts under `results/`

Runtime is Local-First. Active production code performs no lottery-site HTTP requests.

## 1. Frozen model layer
The hardening release does not retune Phase 3 or Phase 4.

- Phase-3 frozen artifact: `2d8a9c0ce6e32d653311356f20d1329d9c2ed8f6c7c544899b5b6d6ccf0f957a`
- Phase-4 policy integrity summary: `41229a251054e53f64b0ae339675675f689678f90bb4331891e22d82624f9b23`
- selected policy: `P_R01_005_B06`
- V5 route: `ANTI_CROWD_SPARSE_ZONE_V1`
- fallback route: `UNIFORM_MARGINAL_BALANCED_COVERAGE`

Production gate order is strict:
`Phase-3 OOS -> CUSUM drift -> Phase-4 Policy -> V5`.

Any model-gate failure selects the V4 fallback candidate generator, but both routes still pass through the same durable transaction shell.

## 2. Shared durable transaction
Every production route uses:
`STAGE -> HASH VERIFY -> LEDGER COMMIT -> ATOMIC PUBLISH`.

Every published issue contains:
- `PRIVATE_SEED_AND_NONCE.json` with mode `0600`
- `TICKETS.json`
- `RECEIPT.json`
- `SPARSE_POOL_MANIFEST.json`
- `STAGE_MANIFEST.json`

The append-only ledger is hash chained and verified before/after commit.
A second production attempt for an already committed target issue is rejected with
`REFUSE_DUPLICATE_ISSUE`.

If a process fails after ledger commit but before publish,
`recover_staged_publish()` verifies the staged bytes and completes publication
without redrawing.

## 3. Commitment
Every V4/V5 production bundle uses canonical JSON and binds at least:
- `algorithm_version`
- `rng_model`
- `input_manifest_sha256`
- `twin_artifact_sha256`
- `policy_artifact_sha256`
- `sparse_pool_sha256`
- `circuit_breaker_status`
- `effective_route_mode`
- `rule_regime_state`
- `physical_slip_plan`
- `seed_sha256`
- all `tickets`

## 4. Local schedule and time gate
Normal SSQ schedule is frozen locally as Tue/Thu/Sun 21:15 BJT.

The 2026 official lottery-market suspension dates are frozen in
`data/local_schedule_manifest.json`.

As of 2026-11-25 the 2027 lottery-market suspension notice has not yet been
officially published. The manifest therefore marks 2027
`PENDING_OFFICIAL_PUBLICATION` and production fails closed for a 2027 target
until that local file is updated from the official notice. Tests use isolated
temporary fixtures to verify the cross-year algorithm `2026152 -> 2027001`;
they do not weaken the production fail-closed policy.

Production is forbidden when `now_utc >= draw_at_utc`.

## 5. Rule-state chain
The one-time genesis bridge is frozen through issue `2026135` in
`data/production_ledger_genesis.json`.

After genesis, every completed production issue inherits
`special_rule_active_previous` only from the previous ledger record and
persists the new state in the ledger.

The 2026 hysteresis machine remains:
- enter special at pool >= 1.5B RMB;
- while active, remain active until pool < 0.3B RMB;
- `fuyun_prize_active` follows the special-rule state.

## 6. Physical slips and promotion
The CLI creates a default exact partition of tickets into physical slips, at
most 10 single bets per slip. A caller may provide an explicit JSON plan.

Pre-purchase states are:
- `PLANNED_ELIGIBLE`
- `PLANNED_PARTIAL_ELIGIBLE`
- `PLANNED_INELIGIBLE`
- `NO_ACTIVE_PROMO`

Post-purchase confirmation uses `confirm_published_purchase()`.

If an active sixth-prize promotion lacks
`remaining_sixth_promo_rmb`, V5 fails closed to the full transactional V4
route. A negative balance is rejected as `INVALID_PROMO_REMAINING_RMB`.

## 7. History data contract
Archived rows before `2022139` may omit t3-t6/promotion fields.

The active full-tier model window from `2022139` onward must contain no missing
t3, t4, t5, t6 or promotion flag. No network backfill is performed.

## 8. Output contract
Both routes explicitly publish:
- `prize_coverage`
- `prize_coverage_note`
- `profit_guarantee = "NONE"`
- `pocket_distribution`
- `sparse_pool_sha256`

For 16 tickets, exact blue coverage yields `prize_coverage = 1.0`; this is not a
profit guarantee.

V4 CSPRNG declaration remains:
`CRYPTOGRAPHICALLY_UNIFORM_MARGINAL_UNDER_CSPRNG_ASSUMPTION`.

## 9. CLI
Standard production command:

```bash
python3 -m ssq_v5.cli_produce --tickets 2 --promo-remaining 560000000
```

Dry-run uses a temporary ledger and output directory:

```bash
python3 -m ssq_v5.cli_produce --tickets 2 --promo-remaining 560000000 --dry-run
```

Custom physical slip plans may be supplied with `--slip-plan-json FILE`.

## 10. CI acceptance
Release acceptance requires:
- V4 regression + integration: exactly 54 tests;
- frozen V5 model regression: exactly 15 tests;
- hardened production E2E boundaries: exactly 5 tests;
- active history-window integrity;
- zero HTTP dependency in the active production chain;
- obsolete heuristic V5 liability/regime selectors absent.
