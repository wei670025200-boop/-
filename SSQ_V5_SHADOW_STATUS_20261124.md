# SSQ V5 SHADOW INTEGRATION STATUS — 2026-11-24

## Decision

V5 Phase 2–4 code is accepted for **SHADOW RESEARCH ONLY**.

Production route `ANTI_CROWD_SPARSE_ZONE_V1` remains hard-locked.
The active production release remains `SSQ_V4_RC2_1`.

## What was integrated

- `ssq_v5/db_and_sentiment_v5.py`
- `ssq_v5/digital_twin_v5.py`
- `ssq_v5/liability_and_pockets_v5.py`
- `ssq_v5/runner_v5.py`
- `ssq_v5/test_v5_pipeline.py`
- isolated GitHub Actions acceptance workflow

## RC2.1 compatibility fixes

V5 now inherits all current V4-RC2.1 hard gates:

1. 2-of-N official raw-byte quorum.
2. Regular schedule evidence plus full-year suspension evidence.
3. Promotion-rule evidence plus current promotion-status evidence.
4. Previous special-rule state inherited from the append-only ledger; caller override conflicts are rejected.
5. V5 ledger records persist `special_rule_active` so inheritance remains continuous across issues.
6. Physical-slip validation and structured EV context remain inherited from RC2.1.

No frozen V4 source was modified.

## Additional policy gate

The supplied Phase 4 research package contains pre-registered but still human-selected constants, including:

- liability weights `0.50 / 0.20 / 0.30`;
- red sparse percentile bands such as `2–8%`, `1–5%`, `0.5–2.5%`;
- blue keep-count defaults `10 / 8 / 6`.

These are not yet selected or validated by real expanding-window OOS data. Therefore:

`V5_POLICY_GATE_STATUS = UNVERIFIED_FIXED_THRESHOLDS`

A requested V5 production run is forced back to V4 until this gate is replaced by a hash-bound, data-selected policy artifact that passes preregistered OOS acceptance.

Safety lock order:

1. OOS gate
2. CUSUM drift gate
3. policy-parameter gate
4. only then V5 production eligibility

## CI acceptance

Shadow acceptance workflow passed:

- Python compile: PASS
- frozen V4 SHA256 manifest: PASS
- Phase 2 sentiment/raw-evidence tests: PASS
- deterministic calendar test: PASS
- reflexivity/weird-combo exclusion: PASS
- synthetic expanding-window signal/noise OOS gate test: PASS
- exact inclusion-exclusion neighborhood engine vs brute force: PASS
- CUSUM normal/drift tests: PASS
- RC2.1 runner fallback integration: PASS

Final suite: **7 / 7 PASS**.

## Remaining production blockers

### B-V5-01 — real canonical history missing

Need a continuous 600+ draw SSQ Canonical V2 dataset with, per issue:

- issue/date
- 6 red + 1 blue
- sales
- post-draw pool
- tier 1–6 winning counts
- promotion fields where applicable
- source provenance / raw-byte hash where required

Synthetic fixtures prove code behavior only; they do not satisfy the OOS evidence gate.

### B-V5-02 — real sentiment parsers not registered

The current sentiment registry/parser contract is test-grade standardized JSON. Production requires real raw-page parsers for the frozen channels or a revised preregistered channel set.

### B-V5-03 — Phase 4 routing constants not data-selected

Liability weights, sparse bands, and blue keep-counts must be selected by a preregistered training procedure using training folds only, frozen before each OOS target, and accepted only if the future/OOS gate passes.

## Current state

- V4 production: ACTIVE / unchanged
- V5 Phase 2–3: SHADOW READY
- V5 exact neighborhood engine: SHADOW READY
- V5 production routing: BLOCKED
- reason: REAL_OOS_DATA + REAL_SENTIMENT_PARSERS + DATA_SELECTED_POLICY_ARTIFACT_REQUIRED
