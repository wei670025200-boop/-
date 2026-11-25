# DLT_V5_PREREG_CLEANROOM_1.0 — final user addendum

Pre-data user freeze; Phase 4 LOCKED. Supersedes provisional OOS gate and holiday mapping in the original specification. No real OOS or real award data accessed in this change.

- Calendar pulse: union of draw Gregorian month and day and the following fixed holiday numbers within +/-3 calendar days (including cross-year boundaries): 01-01:[1]; 02-14:[2,14]; 05-01:[5,1]; 06-01:[6,1]; 07-01:[7,1]; 08-01:[8,1]; 10-01:[10,1]; 11-11:[11]; 12-25:[12,25]. Deduplicate, restrict to 1..35, divide front hits by 5.
- OOS: equally weighted relative front/back MSE improvements, paired 12-period MBB B=5000, 95% lower bound >0; both individual mean improvements >0 and at least one individual 95% lower bound >0. Spearman is diagnostic, not a gate.
- Policy: Score_f=mean(y_f on W)-mean(y_f on W where r_t in Band_f); Score_b=mean(y_b on W)-mean(y_b on W where b_t in Pool_b); Primary=0.5*Score_f+0.5*Score_b. Inner front_hits>=5, back_hits>=15; skip ineligible candidates. Outer front_hits>=10, back_hits>=30, valid MBB resample fraction>=0.95 and 95% lower bound>0. SPARSE_CAPTURE_LIFT diagnostic only.
- CUSUM: e_t=0.5*abs(y_f-yhat_f)+0.5*abs(y_b-yhat_b); mu0 and sigma0 frozen from pre-cutoff Phase 3 OOS residuals, sigma0>=1e-6. One-sided Page-CUSUM S_t=max(0,S_prev+(e_t-mu0)/sigma0-0.5); h=5.0; evaluate latest 20 trajectory values; any >=5.0 => TRIPPED.
- Six SHA-256 integrity digests (NOT digital signatures): history_prefix_sha256, twin_artifact_sha256, statutory_rule_sha256, prereg_spec_sha256, candidate_grid_sha256, oos_trace_sha256; integrity_summary_sha256 binds the six. git_commit_sha provenance only, not equality to future HEAD.
- Prior choices for ridge response winsorization, EB residual shrinkage and omission tie order are user-approved. R19 isolated; no main merge; bootstrap and real OOS forbidden until Stage 1 complete.

Implementation conventions declared before data: combined and policy paired bootstrap seeds 2026112503 and 2026112504; noncircular blocks and percentile intervals; candidate ties follow original fixed grid order; policy evaluation uses only prior OOS trace rows for selection, not current outcome. The exact Phase 3 cutoff issue and live ticket count must be provided by the validated caller; no fabricated cutoff or silently assumed batch size.
