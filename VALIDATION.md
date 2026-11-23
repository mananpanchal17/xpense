# Verification · 1 November 2026

Verified with Python 3.14.7 on the final workspace source. The original downloaded application and personal database were not changed.

## Automated results

- Full `python -m pytest -q`: **128 passed, 336 warnings in 190.88s**. Evidence: .qa/phases6-8-verified.log. Includes 14 real-CSRF integration cases, four snapshot/filter cases and four planning-currency cases, alongside the existing ledger/security/domain regressions.
- All Python source parses and all 27 Jinja templates compile. Every app/static/js JavaScript file passes `node --check`.
- Node controller checks pass: interrupt/reopen/confirmed-removal/reduced-motion/handle gestures; exact local money/date/search/sort filtering; edits made while snapshots load; signed refund chart data.
- Product-agent quick validation passes. AST comparison confirms all 19 model classes and 195 complete column declarations. Baseline/informed feature scenarios and final independent scoped review found no blockers.
- Final desktop 1440px/mobile 390px browser inspector: zero findings, zero console errors and zero viewport overflow. Screenshots/report: .qa/phases6-8-preview/.

## Browser and performance evidence

Native browser checks covered keyboard composer open, amount focus, Escape, mobile bottom alignment, 44px close target, filter sheet open/close, category/date changes and URL persistence. The final mobile ledger puts transaction type beneath its description, keeping native amounts immediately visible; more actions remain horizontally scrollable.

Warmed computation plus DOM updates measured 1.9–2.5ms over 73 synthetic rows. The 2,000-row pure filtering kernel p95 was 0.737ms on the final check. These are local measurements, not guarantees for network requests, paint or chart rendering. Current dashboard charts have no date/category switch controls and already load once per page, with in-place theme updates; no React render work exists here.

Snapshots are private, no-store, bounded to 2,000 rows and 2 MB of escaped HTML, retained only in tab memory. URL parameters persist filter choices; incomplete/unavailable snapshots use the server form. No offline write queue exists. Other-tab changes require refresh. OS reduced-motion switching and actual browser swipe simulation were not exercised; those branches pass the real-controller Node harness.

## Financial and deployment limits

Transaction reports use saved INR snapshots. Planning/portfolio totals are labelled estimates from cached FX; missing conversions are excluded with visible warnings. Native account and occurrence amounts remain native. Historical JPY hundredths are preserved. No actual banking provider, SMTP/Redis production service or live deployment was tested.

The 336 existing dependency warnings concern WTForms tuple choices and Flask-Login utcnow. They are not test failures. PRELAUNCH.md records upgrade and deployment requirements. SOURCE_CODE.md is historical; app/, tests/ and the regenerated PATCHED_COMPONENTS.md describe the delivered code.
