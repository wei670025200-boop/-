# SSQ V4-RC2 FINAL ACCEPTANCE

Date: 2026-11-24

## Decision
Local engineering/code freeze gate: PASS.

Production route:
UNIFORM_MARGINAL_BALANCED_COVERAGE

Predictive edge claim:
NONE.

Popularity filter:
BLOCKED.

## Regression / integration
Final local clean-directory suite:
- 51 / 51 PASS
- 0 failures
- 0 errors
- runtime ~0.215 s

The suite includes the original 42 V4-RC2 blocker regressions plus real/production integration checks for:
- Shanghai official raw HTML parsing;
- Sichuan official raw HTML parsing;
- independent Shanghai + Sichuan 2-of-2 quorum;
- Shanghai official draw schedule parsing;
- previous-issue special-rule state inheritance from append-only ledger;
- caller conflict with ledger-inherited rule state is rejected;
- annual suspension notice expansion;
- missing annual suspension evidence fails closed;
- wrong-year suspension coverage fails closed;
- only empirically validated draw sources are production-enabled.

## Real official-source validation
Validated from raw official evidence collected by GitHub Actions:
- SH_FLCP: production_enabled = true
- SC_FLCP: production_enabled = true

Both independently parsed the same latest test draw:
- issue: 2026134
- draw date: 2026-11-22
- reds: 04 05 10 17 21 32
- blue: 08
- sales: 435,713,586 RMB
- pool after draw: 1,154,982,023 RMB

Registered but not yet production-enabled:
- CWL_CENTRAL
- GD_FLCP

Their parsers may remain in the codebase as disabled fallbacks until independently validated against stable current raw official evidence.

## Distribution audit
Final local 100,000-trial audit:
- ticket_count: 8
- red slot 0 chi-square: 18.6222 < 62.487
- red last slot chi-square: 36.6525 < 62.487
- blue slot 0 chi-square: 14.4842 < 37.697
- all tests use alpha = 0.001 thresholds specified by the RC2 protocol

Exact coverage crosscheck:
- fast exact union N=6: 32,568
- brute-force C(33,6)=1,107,568 enumeration: 32,568
- difference: 0
- overall_audit_pass: true

## Production evidence gates
Required before a real production draw:
1. >=2 production-enabled independent official sources must agree.
2. Weekly draw schedule evidence must be raw-byte + SHA bound.
3. Full-year official suspension evidence must cover the target date.
4. Promotion evidence, when used, must be official raw-byte + SHA bound.
5. Physical slip plan must exactly partition all generated tickets.
6. Previous special-rule state is inherited from the previous issue ledger when present.
7. First migration/bootstrap state, if no previous ledger state exists, is explicit and commitment-bound.
8. Production transaction remains STAGE -> HASH VERIFY -> LEDGER COMMIT -> ATOMIC PUBLISH.
9. No issue may be redrawn after ledger commit.

## Source bundle
Frozen source tar SHA-256:
363c53aa713ba7119aec9833bad2ba250e97217247cb5fe969de82bcd6eccb03

The repository acceptance workflow reconstructs this exact bundle, verifies the SHA, reruns the 51-test suite and the 100k audit, then publishes the tested source into v4_rc2/.
