# Phase 6 integration verification

The application uses Flask, Jinja and WTForms. React/Zod instructions do not apply to this stack. Money enters as decimal form strings, is validated server-side, is stored as integer hundredths, and is formatted at the UI boundary.

## Changes

- Added `tests/test_integration_flow.py` with 14 CSRF-enabled integration cases. Authentication is established before enabling CSRF; tested requests use real tokens parsed from the global composer and real Flask routes, database writes, templates and chart JSON.
- Verified `0.10` and `0.20` store `10` and `20`, total `30`, render `INR 0.10` / `INR 0.20` and dashboard `INR 0.30`, and reach charts as `0.3`.
- Verified malformed/nonfinite/overprecision/nonpositive amounts, foreign category ownership, and invalid CSRF reject without transaction writes.
- Verified splits and refunds conserve cent amounts, render linked entries, expose decimal remaining refund amounts and native currency, and remove the refund action once exhausted.
- Fixed individual native planning amounts in recurring, calendar and commitments templates. Linked USD amounts previously displayed an INR label. They now use the account currency, with INR for unassigned records. Three route-render regression cases failed on missing USD labels before this fix and passed afterward.

## Audit

No raw minor-unit amount display found across templates. Amounts use `money`, `money_currency`, `money_value`, or explicit division by 100 for editable decimal data attributes. Other minor-unit references are arithmetic or comparisons. No Python, JavaScript or template code was removed as orphaned in this phase. Parent independently removed proven unused CSS selectors.

The followup also corrected mixed-currency commitment aggregates. `commitment_summary` converts due_month, paid_month, remaining, overdue, monthly_fixed and annual_recurring to integer INR hundredths using cached direct or reciprocal rates, reused once per source currency per call. Unassigned accounts remain INR. Missing-rate commitments contribute nothing to INR totals and are counted once each in `unconverted_count`; the commitments page and overview display the exclusion warning. Native occurrences and individual labels remain unchanged. Summary metadata now includes `currency: "INR"` and `unconverted_count`.

## Verification

- `pytest tests/test_integration_flow.py -p no:cacheprovider --basetemp .qa/phase6-final -q`: **14 passed** in 13.53 seconds.
- All **26 Jinja templates compiled successfully** using an in-memory test app.
- WTForms emitted 55 existing deprecation warnings about tuple SelectField choices.
- A full-suite run was started before the currency fix, but its process handle became unavailable after an environment/context update. Its result is not claimed. Parent owns the final full-suite run after Phase 7.
- All test databases were temporary; no personal environment file or database was changed.

## Commitment aggregate followup

- Added `tests/test_planning_currency.py`: direct USD/INR 83 conversion, reciprocal cached pair, mixed INR/USD totals, missing-rate exclusion counted per commitment, native occurrence preservation, and visible warnings on commitments/overview.
- Observed all four new cases fail on incorrect native aggregate values before changing production code.
- After the fix, `pytest tests/test_planning_currency.py tests/test_commitments.py tests/test_integration_flow.py -p no:cacheprovider --basetemp .qa/planning-currency-green -q`: **21 passed**, 67 existing WTForms deprecation warnings, in 29.96 seconds.
- All **27 current Jinja templates compiled successfully** after the followup and concurrent Phase 7 additions.
- Parent owns final full-suite verification of the integrated phases.

Interpreter: `C:/Users/MANAN/Downloads/Expense_Manager/expense_manager/.venv/Scripts/python.exe`; test-only SECRET_KEY and PYTHONDONTWRITEBYTECODE=1.
