# SSQ V4-RC2.1 FINAL ACCEPTANCE

Date: 2026-11-24

## Final decision

**ACTIVE PRODUCTION RELEASE: SSQ_V4_RC2_1**

Live repository entrypoint:
`v4/runner_v4.py`

Validated implementation:
`v4_rc2/`

Production route:
`UNIFORM_MARGINAL_BALANCED_COVERAGE`

Predictive edge claim:
`NONE`

Popularity filter:
`BLOCKED`

## Frozen-code acceptance

Hosted freeze acceptance run:
`45394838990` — PASS

Verified:
- `v4_rc2/SHA256SUMS.txt`: PASS
- real SH/SC raw evidence fetch: PASS
- 54 / 54 regression + real integration tests: PASS
- 100,000-trial audit: PASS
- exact 1,107,568 red-draw brute-force crosscheck: PASS

Freeze acceptance audit:
- red slot 0 chi-square: 28.9907 < 62.487
- red last slot chi-square: 13.5187 < 62.487
- blue slot 0 chi-square: 8.2595 < 37.697
- exact union N=6 fast: 32,568
- exact union N=6 brute force: 32,568
- difference: 0

## Fresh Production E2E

Target issue:
`2026135`

Combined Fresh E2E run:
`45394446046` — PASS

Pre-draw official evidence:
- SH_FLCP draw evidence SHA-256:
  `a1cfd33368b0e8e21b5a90dbf0ec7d5084898b5764caf9ce33ed5fa65bff6af9`
- SC_FLCP draw evidence SHA-256:
  `ffb58eec77655a02799dc77434bc6d9c0ebb69f03d8a115803d709e543d83717`
- MOF annual suspension evidence:
  verified and hash-bound
- GZ_MZJ promotion-rule evidence:
  verified and hash-bound
- SH_FLCP current promotion-status evidence:
  verified, tied to latest completed issue 2026134, and hash-bound

Production transaction:
- latest completed issue: 2026134
- target issue: 2026135
- ticket count: 10
- budget: 20 RMB
- physical plan: one 20 RMB SINGLE slip containing all 10 singles
- rule state: STAY_NORMAL
- previous special-rule state: false
- promotion: active
- planned promotion eligibility: PLANNED_ELIGIBLE
- STAGE: PASS
- HASH VERIFY: PASS
- LEDGER COMMIT: PASS
- ATOMIC PUBLISH: PASS
- duplicate issue / no-redraw guard: PASS
- profit guarantee: NONE

Fresh E2E commitment SHA-256:
`48c64e2d374d07da86042a4fdc4e09809c9f51d22a9de9f072e68546e32ee70b`

Fresh E2E ledger record SHA-256:
`cc345b2c85a75d8a55462d7930b0d46fe52898ef3fc4ca2731a11a3e79da3fdd`

## Promotion evidence hardening

RC2.1 separates:
1. promotion rule evidence;
2. current promotion-status evidence.

During an active promotion, current status evidence is mandatory and must correspond to the latest completed issue. A positive verified sixth-prize doubling balance is required before that bonus is represented in the EV context.

The current status evidence SHA is separately included in the production commitment and receipt.

## Cutover

The previous V4 draft runner is preserved as:
`v4/legacy_runner_draft.py`

The live production entrypoint now resolves to RC2.1.

Isolated cutover smoke:
`45395722788` — PASS

The live entrypoint:
- reports `ACTIVE_RELEASE = SSQ_V4_RC2_1`;
- resolves implementation to `v4_rc2`;
- exports the controlled production transaction API;
- does not export the old ungated `generate_balanced_batch` draft API.

## Remaining operational condition

The software cutover is complete.

If the user actually purchases under a promotion, planned eligibility must still be confirmed from the issued physical ticket / terminal receipt. Until then:
`PLANNED_ELIGIBLE` is not the same as `RECEIPT_CONFIRMED_ELIGIBLE`.

This remaining condition is about promotion eligibility, not V4 release activation.
