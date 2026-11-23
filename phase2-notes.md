# Phase 2 ledger notes

Implemented in the copied Xpense 2 workspace only; no production database, secrets, or original application was accessed.

- `app/ledger.py` owns strict decimal-to-minor-unit validation, tenant transaction filtering, common INR totals and category rollups, budget usage/remaining/percentage, normal transaction persistence, split/refund writes, linked-entry edit protection, and reversible CSV spreadsheet escaping.
- Reports, charts, budget checks, intelligence queries and the assistant use saved INR `base_amount_minor`; only legacy INR/null-currency entries fall back to their native amounts. Foreign rows without snapshots are excluded and counted through `unconverted_count` in template context and chart JSON. Native account currencies and legacy JPY hundredths are preserved.
- Splits preserve the original as `split_parent`, create linked expense children, and allocate integer INR amounts cumulatively so native and base totals conserve exactly. Split parents are excluded from account balances and ordinary transaction lists/reports. All linked entries are protected against ordinary edit/delete.
- Refunds reference their original expense, add money back to its account, reduce expenses on the refund date, and cumulatively conserve the original INR snapshot. Ownership, positive amounts, refund dates and the remaining native refund ceiling are checked. Split/refund writes lock and refresh the original with a no-op UPDATE before checking links/totals, including on SQLite.
- Nullable `parent_id` and `refund_of_id` columns are included in the existing additive `upgrade-db` command. No destructive migration runs automatically.
- Same-currency transfers reject mismatched destination amounts. Source balance checks include the fee. Account summaries use three batched queries instead of several per account; account balances ignore split parents. Cached reciprocal currency rates are supported with their original date and an inverse source marker.
- CSV accepts legacy six-column files and optional currency/snapshot/rate metadata; extended normal-transaction exports round trip snapshot values and escaped text. Independently imported transfer/refund/adjustment entries are rejected because links cannot safely be recreated from isolated rows. This is transaction exchange, not a full account/linked-ledger backup.
- Profile currency changes are locked once accounts or transactions exist, preserving existing empty-profile behavior. Account currency cannot relabel an opening balance or posted history. Report formatting is always INR; native money formatting remains separate.
- Recurring forecasts convert pending native payments into INR using cached direct/inverse rates; unknown currencies are omitted and counted in `forecast.unconverted_recurring_count`. PDF subtotals exclude transfer/adjustment legs consistently.

UI/API integration:

- `POST /expenses/<id>/split`: JSON `{ "parts": [{ "category_id": 1, "amount": "12.34", "notes": "optional" }, ...] }`. Between 2 and 50 positive parts. Returns 201 with child IDs; validation returns 400 with `error`. Linked-entry conflict is 409.
- `POST /expenses/<id>/refund`: JSON or form `amount`, `date` (ISO date), optional `notes`. Returns 201 with refund ID, validation 400. Both endpoints retain global CSRF protection; send `X-CSRFToken` for JSON.
- `budget_status` adds `remaining`; existing `spent`, `percent`, `level`, `budget` remain available.
- `unconverted_count` and `reporting_currency` are global template context. Chart JSON includes `unconverted_count`.

Verification history:

- Initial eight regression cases failed against the copied source, then passed after implementation.
- Currency/profile locks, assistant conversion and missing foreign snapshot tests failed before fixes.
- Formula-like CSV note round trip failed before reversible escaping fix; subsequent targeted run: 14 passed.
- First full suite: 56 passed, 7 failed in 131.13s. All failures were template macro context errors (`today`/`action_form`) in concurrently updated UI; parent corrected all imports with context. Failing tests: `test_dashboard_renders_with_account`, `test_registration_hash_login_logout`, `test_expense_crud_and_exact_money`, `test_isolation_for_records_and_category_form`, `test_search_filter_sort_and_invalid_ranges`, `test_budget_thresholds_and_upsert`, `test_dashboard_reports_and_charts`.
- Fresh reviewer regressions reproduced inverse-rate save failure, incorrect PDF transfer/adjustment subtotals, unconverted recurring forecasts, concurrent overrefunds, and concurrent duplicate split children before fixes. Final targeted/full results are appended below.

Known follow-up for Phase 5: serialize ordinary delete against split/refund mutation, validate combined transfer amount+fee against the minor-unit maximum, and show the explicit missing-recurring-rate forecast warning in the UI.

Final Phase 2 targeted run: 19 passed, 33 existing WTForms deprecation warnings in 67.88s. Included real two-connection concurrent refund and split regressions; each reproduced double-saves before serialization and passed after locking.

Second full-suite run after ledger reviewer fixes: 66 passed, 7 failed, 141 dependency deprecation warnings in 271.83s while parent changed UI/composer context. Failures: `test_global_composer_has_labels_csrf_and_keyboard_hint` (CSRF test fixture changed during run); `test_expense_crud_and_exact_money`, `test_search_filter_sort_and_invalid_ranges`, `test_budget_thresholds_and_upsert`, `test_dashboard_reports_and_charts`, `test_xss_is_escaped_and_headers`, `test_real_csrf_registration_login_and_transaction` (all six referenced `refund_totals` before the running process had parent's new context). All 19 ledger regressions passed in this run. A fresh expenses-only run after parent UI changes passed all 15 tests, with 55 existing WTForms deprecation warnings in 74.35s. Parent owns final whole-suite validation once all phases settle.

Parent subsequently added locked `ledger.delete_transaction` and a combined transfer fee maximum/source lock, superseding those follow-up notes. No backend implementation edits were made by the Phase 2 agent after its final 19-test green run. Temporary implementation scripts were removed.
