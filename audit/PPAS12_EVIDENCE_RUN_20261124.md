# PP-AS-1.2 OFFICIAL EVIDENCE RUN — 2026-11-24

Status: `DATA_GATE_BLOCKED`

GitHub Actions run:

- run: `45379746971`
- head: `1da53613f3a28c8147bbd240cb86edde5826d2f3`
- result: `SUCCESS` (the acquisition/audit workflow completed)
- artifact: `14913990831`
- artifact ZIP SHA-256: `6a3a77095f7e077f17d24a7aa78ce8b3f23659f19d714d5d4b5094b50850719d`

Observed acquisition and parsing result:

- 45 Jiangsu official archive pages;
- 540 Hebei official detail pages;
- 122 Sichuan official PDF announcements preserved as raw evidence;
- 1,450 raw official evidence files in the candidate manifest;
- 1,018 parsed official source records;
- 766 complete issue candidates from `2021121` through `2026133`;
- null date / sales / sixth-prize count / sixth-prize amount fields: `0 / 0 / 0 / 0`;
- complete-table observation with no special-accounting row: `679` issues;
- explicit special-accounting row observed: `87` issues.

Deduplication and independent check:

- issues with two parsed official sources: `118`;
- field conflicts after source-display precision reconciliation: `0`;
- deterministic overlap sample: `60` issues;
- sixth-prize count agreement: `60 PASS / 0 FAIL`.

The 60-issue result is diagnostic and is not promoted to the fixed-600 audit because the exact fixed issue manifest is absent.

Fail-closed blockers:

- `FIXED_600_ISSUE_MANIFEST_MISSING`;
- `INDEPENDENT_AUDIT_BLOCKED_OR_FAILED` (the fixed-manifest scope cannot be established).

No discovered issue was dropped to force a 600-row count. The inclusive issue boundary contains 766 acquired candidates, so the exact prior 600-issue selection must be supplied byte-for-byte before freezing.

Enforced result:

- manifest status: `CANDIDATE_NOT_FROZEN`;
- PP-AS-1.2 fit runs: `0`;
- behavior gate: `NOT_RUN_DATA_GATE_NOT_PASSED`;
- economic gate: `NOT_RUN`.

Derived artifact hashes:

- `CANONICAL_CANDIDATE.jsonl`: `d4c023e8a194fee84e9609be90597be9e0f466dd2d7cd8d7e2f448b3bb8f8480`
- `DEDUP_RECONCILIATION.json`: `fe684eb3b7d53f2295d420455ea6a286c103bdd507c48720320ff629cb65c14b`
- `INDEPENDENT_OFFICIAL_AUDIT.json`: `d81d12af8126b4cbcfd1270977223bdd578b660b837c75073ef4719ff8a5d41f`
- `DATA_GATE_REPORT.json`: `7d57017696028a20e7cb43226a82f3f571252fffcd7b0b33f6a0e288334309d2`
- `EVIDENCE_MANIFEST_CANDIDATE.json`: `aa255128ab250fbc795bd36a627a6e735144ea8d2d2dee95e8e4f761a06c6f97`
- manifest body SHA-256: `3f870a379c061225995c0b852198b867be2a9edf5755e029e5d02b3d0c7c0f03`
- `SHA256SUMS.txt`: `0e4e0116d6bff838ee707f87e17c2fe8071666af24738f486b54557cca6ed415`


