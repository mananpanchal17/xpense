# Pre-launch verification and upgrade

The updated application is in Xpense 2. The downloaded source and its personal database were not changed. The production entry point remains run.py; tools/preview.py is a synthetic, loopback-only demonstration that automatically signs in its disposable preview user. Never deploy the preview launcher or its .qa database.

## Verified locally

Final full suite (2026-11-01): **128 passed, 336 warnings in 190.88s**. Command: `python -m pytest -q`. Full output: `.qa/phases6-8-verified.log`. All 27 Jinja templates compile. JavaScript syntax plus motion/filter/loading-race/signed-chart checks pass. Final 1440px/390px inspector reports zero findings and no console errors. See VALIDATION.md for scoped browser/performance evidence.

- Regression tests cover converted INR monthly totals, budget percentages/remaining, split conservation, refund date accounting and ceilings, transfer integrity, inverse rates, CSV round trips, real CSRF, malformed JSON, investment funding/zero values, loan validation and concurrent linked payments.
- Native browser: saved INR 0.30 through the real composer, verified C shortcut, initial amount focus, Escape dismissal and restored ledger rendering. The actual ledger stores 30 paise; browser number controls may expose a floating accessibility approximation.
- Final transaction screenshots at 1440 and 390 pixels report no inspector findings after correcting mobile labels and delete contrast. Screenshots are in .qa/phases6-8-preview. The preview email is explicitly allowed sample data; no personal records are shown.
- Source review covers all route blueprints, authentication/session/reset flows, tenant selection, nested record ownership, forms, CSV escaping, CSP/CSRF, assistant data tools, configuration safeguards and static assets. No real secret file or personal database was inspected.
- No new frontend framework or production dependency was introduced. Existing dependency deprecation warnings remain; deployment and external services have not been exercised.

## Before using an existing database

1. Stop the old app and make an independent backup of instance/expenses.db. Keep the original folder until the upgrade is verified.
2. Copy that backup into this project's instance folder only when ready to migrate. Use the real run.py entry point, never tools/preview.py.
3. Keep the database's original SECRET_KEY in a private .env if existing sessions must survive, or generate a new one to revoke old sessions. Do not put .env in an archive or source control.
4. Run `python -m flask --app run upgrade-db`. It adds nullable parent_id/refund_of_id and any previous feature columns; no existing tables are dropped or rebuilt. Legacy null-currency transactions are interpreted as INR; check the old ledger's actual currency before importing history. Historical JPY hundredths are preserved and are not silently reinterpreted.
5. Compare known balances, one monthly report, budgets and exported CSV against the backup. Splits/refunds protect their linked entries against independent edits; linked exports are archival and deliberately cannot be independently reimported as ordinary transactions.

## Deployment checklist

- Use a production WSGI server behind HTTPS with APP_ENV=production, COOKIE_SECURE=true, a private random SECRET_KEY of at least 32 characters and an HTTPS APP_BASE_URL.
- Configure SMTP and shared Redis rate-limit storage. Verify reset delivery, cookie attributes and rate limits in the actual deployment; repository guards cannot prove the proxy/provider configuration.
- Run `python -m pytest -q` after installing requirements. Check the current dependency advisories before exposing the app publicly. This pass did not query vulnerability feeds.
- Schedule `python -m flask --app run generate-recurring` daily; APP_TIMEZONE=Asia/Kolkata controls due dates. Rates are cached estimates; refresh them and review unconverted-entry warnings before relying on reports.
- Keep .env, instance databases, .qa, virtual environments and preview tools out of deployed/public source archives. Restrict file access and arrange regular tested database backups.

## Financial behavior

Reports and budgets use INR snapshots. Native account/transaction amounts keep their currency. Unknown conversions are excluded with visible warnings rather than guessed. Investment INR totals are estimates using cached FX; native holdings remain visible. Budgets reset on the calendar month without carryover. Refunds reduce expense totals in the refund month; income is not artificially increased. Split children preserve native and INR totals exactly. Recurring generation is catch-up and keeps its month-end anchor. Ordinary expense creation does not enforce a bank overdraft policy; transfers do check available funds including fees.

## Final review follow-up

A fresh independent source review found no new high-severity tenant or ledger issue. Its signed-category-chart finding was reproduced with the Node harness and fixed by retaining refund signs in horizontal bars. Four calendar regressions reproduced and corrected monthly/quarterly month-end anchors, yearly leap dates, end dates, and the annual multiplier. Investment native-currency label syntax was corrected after the first complete run; every Jinja template was parsed before the final rerun. Source snapshots in SOURCE_CODE.md remain historical.

## Phases 6–8 delivery

The integration suite exercises actual CSRF-protected input → exact integer storage/calculation → formatted Jinja/chart output. Recurring/calendar/commitment native labels are corrected; commitment aggregate fields convert through cached direct/reciprocal INR rates with visible missing-rate exclusions. Planning values are estimates, not historical snapshots.

The composer and filters use interruptible 160ms motion and mobile bottom sheets with 44px controls. Local transaction filtering is bounded to 2,000 rows/2 MB, retained in tab memory only, with URL persistence and server fallback. Cross-tab changes require refresh. No deployment or new frontend dependency was introduced.

PRODUCT_AGENT.md is the standalone product strategy contract; FEATURE_ROADMAP.md contains five future proposals and complete prompts, and INSTALL_PRODUCT_AGENT.md explains reuse. PATCHED_COMPONENTS.md supplies complete components and tests. The source ZIP excludes preview.py, .qa, credentials, personal databases, virtual environments and caches.
