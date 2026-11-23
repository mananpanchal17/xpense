# Xpense: five next features grounded in the current repository

These are proposals, not implemented capabilities. Order reflects current risks and workflow gaps. Effort is relative to this Flask application; impact is user value, not a financial forecast. None requires React, Zod, paid AI, bank credentials or a new frontend framework.

| Priority | Feature | Effort | Impact | Existing gap |
| --- | --- | --- | --- | --- |
| 1 | Bill-aware safe-to-spend runway | Med | High | A month-end forecast exists, but not a user-defined next-income horizon with an explainable daily allowance |
| 2 | Duplicate-aware CSV review and safe confirmation | High | High | Import validates atomically, but repeated files create duplicates and have no review step |
| 3 | Undo and auditable transaction corrections | Med | High | Standalone entries can be permanently deleted; linked edits are blocked without a correction flow |
| 4 | Statement reconciliation with explained differences | High | High | Accounts have balances/adjustments, but no owned statement matching or reconciliation history |
| 5 | Next-month budget planner with a no-write preview | Med | High | Budgets are entered category by category; history and known obligations do not assist allocation |

## 1. Bill-aware safe-to-spend runway

**What it solves:** Existing month-end projections do not answer how much can safely be spent before the next expected income date. An explicit next-payday horizon, minimum cash buffer and bill schedule can produce an explainable daily allowance using selected actual account balances rather than lifetime net cash flow. Cached currency estimates and unconverted obligations must remain visible.

**Exact touchpoints:** app/services.py `account_summary(user_id)`, `commitment_summary(user_id,reference=None)`, `commitment_occurrences(commitment,start,end)`, `exchange_rate(base,quote)`, `financial_forecast(user_id,reference=None)`; Account/Commitment/CommitmentOccurrence in app/models.py plus a proposed planning-preferences model only if persistence is needed; a proposed RunwayForm in app/forms.py; app/reports/routes.py `dashboard`; app/templates/reports/dashboard.html and a focused planning form; existing native dialog/motion controllers. Use app/ledger.py `period_totals(user_id,start=None,end=None)` only for labelled history, not as a substitute for present account balances. Add new runway tests and extend planning-currency/integration tests. Effort Med; impact High. The INR commitment conversion correction belongs to the current integration work, not this proposed feature.

**Copy-paste implementation prompt:**

```text
Build an explainable bill-aware safe-to-spend runway for Xpense's current Flask/Jinja/WTForms app. Follow PRODUCT_AGENT.md and DESIGN.md. Proceed autonomously through a local grill-equivalent, docs/product/spending-runway/PRD.md, at most five vertical tickets and red-green implementation; do not migrate to React/Zod or ask routine approval questions.

Inspect app/services.py account_summary(user_id), commitment_summary(user_id,reference=None), commitment_occurrences(commitment,start,end), exchange_rate(base,quote), financial_forecast; Account/Commitment/CommitmentOccurrence models; app/reports/routes.py dashboard and reports/dashboard.html. Add an owned planning form for the next expected income date, chosen available accounts and a minimum cash buffer. Derive the horizon from those explicit values and known unpaid obligations, not guessed salary dates or lifetime income-minus-expenses. Exclude linked account aliases and accounts the user omits. The runway is a planning estimate, not a bank guarantee or permission to spend.

Use integer native paise and labelled cached INR conversion, respecting current unconverted_count warnings. Show included cash, obligations, buffer, days and daily allowance as an equation that reconciles exactly. Explain missing rates, partially paid or already-posted obligations, overdrawn accounts and zero/negative allowance without inventing income. Keep posted-transaction snapshot rules separate from current estimated rates; do not move real money or create ledger entries from a what-if calculation.

First write tests for an explicit worked horizon, exact rounding residue, missing/inverse FX, excluded aliases, already paid commitments, zero days and another user's records. Preserve Asia/Kolkata date boundaries, CSRF and current posting behavior. Reuse Zinc surfaces, a compact executive summary, tabular figures, 160ms interruptible/reduced-motion-aware native dialogs and44px mobile targets. Run the complete pytest suite and affected Node/browser checks. Deliver full code, PRD/tickets, exact results and limitations; do not deploy or touch a personal database.
```

## 2. Duplicate-aware CSV review and safe confirmation

**What it solves:** A repeated bank file currently creates duplicate expenses. A preview can show exact parsed rows, candidate matches, counts and validation errors before any write, then confirm once even after a timeout or simultaneous retry. Similar legitimate purchases must not be silently discarded.

**Exact touchpoints:** app/expenses/routes.py `import_csv`, app/ledger.py `minor`, `save_transaction(row,form,user_id)`, `filtered_transactions`, `csv_safe`, `csv_plain`; app/forms.py ImportForm/TransactionForm; app/models.py Transaction/ActivityLog plus proposed ImportBatch and imported-row provenance constraints; additive upgrades in app/__init__.py; app/templates/expenses/import.html and proposed review template; app/static/js/ledger-filters.js snapshot refresh after confirmed writes. Tests/test_expenses.py import/export tests and tests/test_integration_flow.py are public-flow foundations. Effort High; impact High. No bank sync is implied.

**Copy-paste implementation prompt:**

```text
Add duplicate-aware CSV review and idempotent confirmation to Xpense's existing Flask/Jinja/WTForms import flow. Follow PRODUCT_AGENT.md and DESIGN.md. Autonomously inspect the repo, record assumptions through a local grill-equivalent, write docs/product/csv-review/PRD.md and at most five vertical tickets, then implement one failing public-flow test at a time.

Extend app/expenses/routes.py import_csv, app/forms.py ImportForm/TransactionForm, app/templates/expenses/import.html and a focused review template. Reuse app/ledger.py minor(value), save_transaction(row,form,user_id), filtered_transactions(user_id,args=None), csv_safe/csv_plain. Propose the smallest ImportBatch/row-provenance schema in app/models.py with additive upgrades in app/__init__.py and a unique user-scoped confirmation identity. Current imports accept legacy kind,amount,category,date,payment_method,notes and supported extended snapshot columns, cap 2MB/2000 rows, validate all rows before committing, and deliberately reject independent linked transfer/refund/adjustment rows. Preserve these contracts and reversible spreadsheet escaping.

Preview must write no ledger rows. Flag same-file duplicates, exact prior-import matches and uncertain similar purchases separately. Default suspects to excluded but allow a deliberate legitimate-repeat override; never infer a refund from a positive bank amount. Parse decimal strings to exact paise on the server, preserve historical hundredths/INR snapshot rules, and never reveal another user's match. Confirmation must revalidate ownership and create selected rows once under retries/concurrent requests; preserve current all-or-nothing semantics. Invalidate the tab-memory ledger snapshot only after confirmed writes. Test malformed decimals, unsafe formulas, unknown categories, foreign snapshots, concurrent confirmation and recovery after an uncertain response. Use compact Zinc review rows, tabular figures, 44px mobile controls and reduced-motion-aware 160ms feedback. Deliver complete changes, PRD/tickets and test/browser evidence; no production data or deployment.
```

## 3. Undo and auditable transaction corrections

**What it solves:** A mistaken standalone deletion or amount edit has no user-visible recovery history. Explicit correction/reversal records can preserve the original accounting event and provide safe undo without weakening linked split/refund/transfer protections.

**Exact touchpoints:** app/ledger.py `save_transaction`, `delete_transaction`, `assert_editable`, `reporting_entries`, `reporting_amount`, `period_totals`; app/expenses/routes.py `edit`/`delete`; app/models.py Transaction/ActivityLog plus a proposed correction/reversal link; additive upgrades in app/__init__.py; templates/macros.html row actions and expenses/composer.html; app/static/js/app.js, ledger-filters.js and motion.js confirmed-write feedback. Extend tests/test_ledger.py, test_write_boundaries.py and test_integration_flow.py. Effort Med; impact High. The linked correction model must be specified before reusing an existing entry_type.

**Copy-paste implementation prompt:**

```text
Implement auditable corrections and a safe undo flow for Xpense standalone transactions. Use the existing Flask, SQLAlchemy, WTForms, Jinja and vanilla JavaScript stack. Follow PRODUCT_AGENT.md and DESIGN.md; proceed through a recorded grill-equivalent, docs/product/transaction-undo/PRD.md, vertical tickets and TDD without routine approval pauses.

Inspect app/ledger.py save_transaction(row,form,user_id), delete_transaction(row,user_id), assert_editable(row), reporting_amount/entries, period_totals; expenses edit/delete routes; Transaction and ActivityLog models; macros.html row actions; composer.html; app.js, ledger-filters.js and motion.js. Design the smallest additive correction/reversal relationship that keeps an original event auditable and prevents double reversal under concurrent requests. Do not simply unlock edits of transfers, splits, refund-linked rows or investment funding. Initially support standalone normal transactions only, clearly explaining why protected records require their own linked flow. Specify report-date and account-balance effects with worked paise examples before coding.

Tests must show a correction changes current balances/totals once, an undo restores the specified result once, another user cannot inspect/reverse the record, replayed requests cannot double-apply, and linked-entry protections remain intact. Preserve positive native amounts, INR conversion snapshots, CSRF, exact decimal input and rollback on failure. Show confirmation and an Undo action only after the server confirms the mutation; uncertain timeouts must not trigger automatic retry. Rebuild the local snapshot after the confirmed correction. Keep Zinc, tabular money, 160ms interruptible/reduced-motion-aware feedback and 44px mobile targets. Run the full suite, affected Node tests and browser keyboard/mobile checks; deliver full code and documents without deploying or migrating personal data.
```

## 4. Statement reconciliation with explained differences

**What it solves:** The existing reconciliation action inserts an adjustment from a manually entered actual balance. A statement-review flow can first match owned account transactions, separate missing/duplicate/timing differences and show a precise variance before the user chooses any adjustment.

**Exact touchpoints:** app/accounts/routes.py `adjust` and account routes; app/services.py `account_balance(account)`/`account_summary(user_id)`; app/ledger.py `filtered_transactions`/`minor`/`save_transaction` as appropriate; BalanceAdjustmentForm and a proposed StatementReviewForm in app/forms.py; Account/Transaction plus a proposed owned ReconciliationSession model in app/models.py; additive upgrades in app/__init__.py; templates/accounts/adjust.html and proposed statement review; existing delegated dialog/motion helpers. Extend tests/test_accounts.py and new statement-reconciliation tests. Effort High; impact High. Depends on the CSV preview/idempotency primitives only if shared import support is chosen, not on installing a new parser framework.

**Copy-paste implementation prompt:**

```text
Build statement reconciliation for Xpense account ledgers, starting from the existing manual adjustment flow. Stay on Flask/Jinja/WTForms and follow PRODUCT_AGENT.md plus DESIGN.md. Execute a local grill-equivalent, docs/product/statement-reconciliation/PRD.md, at most five vertical tickets and test-first implementation autonomously.

Read app/accounts/routes.py adjust, app/services.py account_balance(account)/account_summary(user_id), app/ledger.py filtered_transactions(user_id,args=None) and minor(value), Account/Transaction models, BalanceAdjustmentForm and templates/accounts/adjust.html. Add the smallest user-owned reconciliation session and additive upgrade if persistence is necessary. Accept an owned account, statement period, native currency opening/closing balances and bounded statement rows. Preview exact matches and explain unmatched, duplicate and timing differences; ambiguous matches require explicit resolution. Never treat an exchange-rate estimate as a bank balance or silently import statement lines as expenses.

No adjustment is posted during preview. Only explicit confirmation may create one labelled adjustment, excluded from income/expense/budget reporting, with atomic/idempotent writes and preserved transaction history. Tests cover a worked native-paise balance equation, transfers and fees, refund/split history, overlapping/replayed sessions, wrong account currency, malformed values, another user's account and rollback on a failed confirmation. Preserve CSRF and bounded uploads. Reuse compact rows, existing money_currency formatting, neutral Zinc surfaces, 44px mobile controls and interruptible 160ms reduced-motion-aware dialogs. Deliver full code, PRD/tickets and complete test/browser evidence; do not connect to a bank, read credentials, deploy, or alter a personal database.
```

## 5. Next-month budget planner with a no-write preview

**What it solves:** Users currently save monthly category budgets individually. A transparent draft can combine historical category spending with known obligations and savings priorities, show why each limit was suggested, and let the user confirm selected allocations once. It does not enable automatic rollover.

**Exact touchpoints:** app/ledger.py `category_rollup(user_id,start,end)`, `period_totals(user_id,start=None,end=None)`, `budget_status(user_id,month)`; services.py `commitment_summary`, `goal_metrics`, `today`, `month_end`; expenses/routes.py `budgets`; forms.py BudgetForm plus proposed BudgetPlanForm; models.py Budget/SavingsGoal/Commitment; templates/expenses/budgets.html and reports/dashboard.html pacing; app/static/js/app.js/motion.js. Extend tests/test_expenses.py budget-upsert tests and new planner tests. Effort Med; impact High. Reuse the current INR commitment conversion and missing-rate warnings; do not use an unconverted obligation as INR.

**Copy-paste implementation prompt:**

```text
Add a next-month budget draft and review flow to the existing Xpense monthly budgets. Follow PRODUCT_AGENT.md and DESIGN.md on the current Flask/SQLAlchemy/Jinja/WTForms app. Autonomously record assumptions, write docs/product/budget-planner/PRD.md and vertical tickets, then implement with failing public-flow tests first. Verify current mixed-currency commitment conversions and visible missing-rate warnings before consuming the planning totals; never sum native amounts as INR.

Use app/ledger.py category_rollup(user_id,start,end), period_totals(user_id,start=None,end=None), budget_status(user_id,month); app/services.py commitment_summary, goal_metrics, today and month_end; expenses budgets route; BudgetForm; Budget, SavingsGoal and Commitment models; budgets.html and dashboard.html pacing. Produce an editable no-write draft for the next Asia/Kolkata calendar month using a clearly explained recent-history window and known obligations. Make suggested amounts integer paise with transparent rationale and deterministic rounding. New users and missing history get honest empty guidance, not fabricated income or budgets. Keep existing no-carryover behavior: copying an allocation is an explicit choice, never automatic rollover.

Confirmation upserts only approved owned category/month limits in one transaction and must not alter transactions or savings balances. Tests cover exact allocation totals, rounding residue, zero/missing income, refunds in history, month/year/leap boundaries, duplicate confirmation and cross-user categories. Show the draft, differences and remaining unallocated income before saving. Use the existing Zinc executive summary, semantic budget thresholds, tabular figures, 160ms reduced-motion-aware feedback and 44px mobile controls. Run full pytest and affected Node/browser checks. Deliver complete code, documents and results; no personal-data migration, background scheduling or deployment.
```
