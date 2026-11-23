# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Xpense: a personal finance ledger (transactions, budgets, recurring rules, accounts and transfers, commitments/EMIs, investments, savings goals, notifications, reports, no AI assistant). Flask application factory + SQLAlchemy on SQLite, server-rendered Jinja templates, Bootstrap and Chart.js from jsDelivr, small vanilla JS in `app/static/js`.

This folder (`xpense-copy`) is a copy of the original project at `C:\Users\MANAN\OneDrive\Documents\ChatGPT\Xpense 2`. That original serves http://127.0.0.1:5055 through a Windows scheduled task. Never edit or delete files in the original. This copy runs on port **5056**. The scheduled task `Xpense Copy Local Website` starts it at sign-in through `tools/start-local.ps1`, which loops and restarts the server if it exits. Stop it with `tools/stop-local.ps1` before running `start-copy.bat` or anything else on 5056. Templates reload automatically in preview mode. Python edits need a server restart, which you can do by killing the python process on 5056; the loop brings it back.

## Commands

All commands run from this folder and use the local `.venv` (Python 3.14).

```powershell
.\start-copy.bat                                   # preview on http://127.0.0.1:5056 (auto-login, sample ledger)
.\.venv\Scripts\python.exe -m pytest -q            # full suite, ~2.5 min, 159 tests
.\.venv\Scripts\python.exe -m pytest -q tests/test_ledger.py::test_name   # single test
.\.venv\Scripts\python.exe -m compileall -q app config.py run.py         # syntax check
.\.venv\Scripts\python.exe -m flask --app run init-db | upgrade-db | generate-recurring | refresh-rates
```

There is no linter or build step.

Two ways to run:
- **Preview mode** (`tools/preview.py`, what the 5055 site and `start-copy.bat` use): hard-coded secret key, database `.qa/preview-v2.db`, seeds a `preview@example.com` user if it is missing, and a `before_request` hook logs **every request** in as that user. Rate limiting and SMTP are off. `XPENSE_PREVIEW_PORT` sets the port. This is loopback-only and is not a real login.
- **Real app** (`run.py`): needs `.env` (copy `.env.example`) with a `SECRET_KEY` of at least 32 characters, otherwise `create_app` raises. Default DB is `instance/expenses.db`. When `APP_ENV=production`, startup also requires `COOKIE_SECURE=true`, `MAIL_MODE=smtp`, an HTTPS `APP_BASE_URL` and a non-memory rate-limit store.

Tests (`tests/conftest.py`) build a fresh temporary SQLite app per test. Most disable CSRF; `test_security.py` and `test_hardening.py` turn it back on. Put `--basetemp` outside the project if you don't want pytest scratch folders here.

## Architecture

- `app/__init__.py`: `create_app(test_config)` registers the blueprints (`auth`, `expenses`, `reports`, `insights`, `accounts`, `investments`, `backup`), adds the security headers/CSP, error pages and CLI commands, and defines the shared template context (unread notifications, composer categories/accounts, refund totals, `action_form`). It also defines `additive_schema_upgrade()`.
- **No migration tool.** Schema changes are made by `db.create_all()` plus guarded `ALTER TABLE ... ADD COLUMN` in `additive_schema_upgrade()`. New columns must be nullable or defaulted and added there, or existing databases break.
- `app/ledger.py` is the money boundary. Amounts are **integer minor units** (`amount_minor`, hundredths, even for JPY), parsed with `Decimal` via `minor()`. Reporting is always INR: use `base_amount_minor` (the saved INR snapshot); legacy INR rows fall back to `amount_minor`; foreign rows with no snapshot count as 0 and are surfaced through `unconverted_count`. Only `entry_type` `normal` and `refund` count toward reports and budgets (refunds are negative). Transfer legs and adjustments are excluded. Totals must go through `reporting_amount()` / `reporting_entries()` instead of summing `amount_minor` directly. Callers own commit/rollback so linked writes (transfer pairs, splits, refunds, audit log) stay atomic.
- `app/services.py`: shared business logic (ownership lookups, budget totals, recurrence with month-end anchoring and catch-up, insights/forecast/health score, reset tokens, exchange-rate refresh). Dates for "this month" and recurrence use `APP_TIMEZONE` (Asia/Kolkata).
- **Tenant scoping:** every query on user data filters by `current_user.id`, and form category/account choices are per user. Keep this in any new route; `test_write_boundaries.py` and `test_security.py` check it.
- `app/forms.py`: WTForms with server-side validation. All POSTs are CSRF-protected (Flask-WTF). Search and report filters are GET.
- Commitments and EMIs live in `insights/routes.py` and the `Commitment`/`CommitmentOccurrence` models. Paying an occurrence creates exactly one linked expense. Recurring expenses and credit-card bills are older, separate features and have not been migrated into commitments.
- Templates: `base.html` (sidebar shell and keyboard composer, `C` opens Add transaction) and `macros.html` (forms, tables, summary cards). JS is plain files: `app.js` (composer, dialogs), `ledger-filters.js` (client-side transaction filtering), `charts.js` (fetches `/…/data` JSON endpoints), `motion.js`, `theme.js`. The CSP only allows scripts from `'self'` and cdn.jsdelivr.net, so no inline scripts.
- The `money` Jinja filter always prints INR. Use `money_currency` for an entry's own currency.
- Xpense AI was removed on 23 Nov 2026. Its `AssistantConversation`/`AssistantMessage` models stay in `models.py` only so existing history rows are kept and deleted with their user.

## Reference docs in this folder

`README.md` (features, semantics, deploy), `SCHEMA.md` (tables), `DESIGN.md`/`PRODUCT.md` (UI rules), `VALIDATION.md` (what was verified), `FEATURE_ROADMAP.md` (pending features), `phase*-notes.md` (change history from the Codex sessions).

## Feature status

Done: everything in README "Features and behaviour", plus INR ledger refactor, splits/refunds, transfers with fee checks, keyboard composer, fintech redesign, mobile sheets, the calendar month grid, the EMI list with sorting and reminders, and separate Add pages for commitments, investments and recurring expenses.

Pending (FEATURE_ROADMAP.md and the last Codex review):
- Safe-to-spend runway
- Duplicate-aware CSV import preview
- Undo/auditable corrections
- Statement reconciliation
- Next-month budget planner
- Full backup/restore
- Investment redemption flow
- Mobile transaction action sheet
- Notifications outside the app

The last Codex session started fixes for EMI partial payments, savings-goal corrections, zero-allowed form fields, the AI's "last month" range and summary labels, but its outcome was never recorded. Check each one before assuming it is fixed.
