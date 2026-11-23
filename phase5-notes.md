# Phase 5 focused hardening

## Source review coverage

Reviewed every route blueprint, authentication/session/reset handling, tenant-scoped selectors and relationship lookups, shared forms, money validation, linked ledger writes, assistant context selection and provider boundary, CSV escaping, global CSRF protection, template output escaping, production configuration guards, and sample-data ownership. This was a focused defensive review, not a comprehensive penetration test. No production database or secret configuration contents were inspected.

No cross-tenant read/write path, CSRF exemption, executable assistant tool, or unescaped stored assistant output was identified in this source pass. Dynamic form choices and explicit ownership checks constrain record IDs to the authenticated user. Assistant queries supply the authenticated user ID and its provider performs no database writes or arbitrary tool calls. CSV text fields use reversible formula escaping; PDF strings are escaped before ReportLab parsing.

## Changes

- Explicit zero investment valuation remains zero rather than reverting to invested principal.
- Investment funding requires matching investment/account currencies. Automatic conversion is deliberately omitted.
- Existing investments cannot change their funding account. Funded investments also retain posted principal, currency, and purchase date; valuations and descriptive fields remain editable. This avoids a migration and protects existing account-backed records without relying on transaction notes as a link.
- Loan rates must be finite and within 0–1000 percent; expected investment returns must be finite and within -100–1000 percent. Loan tenure is an integer between 1 and 1200 months. Optional outstanding balances accept and preserve zero.
- Commitment payments serialize their occurrence update and refresh the posted transaction link before checking whether payment already exists. Rates are verified before changing payment fields or creating categories, conversion failures release the claim, and INR is the reporting target.

## Validation

Regression tests were written before these fixes. The initial run produced **22 failures and 1 passing baseline**, including a deterministic two-client commitment payment race that created two expense transactions. The new hardening cases and existing investment/commitment tests passed together: **29 passed**. After reordering rate validation ahead of payment-field mutations, both focused payment tests passed again: **2 passed, 21 deselected**. Only existing WTForms choice-format deprecation warnings remain.

The race test uses isolated temporary test databases and synthetic sessions. No deployed endpoint, external identity, mail server, API provider, or real financial record is involved.

## Not verified

Actual deployment HTTPS/proxy settings, SMTP transport/account settings, shared rate-limit storage, external exchange-rate/OpenAI behavior, archive contents and secret exclusions, dependency vulnerability advisories, and non-SQLite database behavior remain unverified. The source has production startup guards and ignores local secret/database files, but those do not prove a deployed environment or deliverable archive is configured correctly. Broader account/ledger concurrency fixes and the full-suite verification are coordinated separately.
