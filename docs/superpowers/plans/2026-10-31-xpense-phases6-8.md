# Xpense phases 6–8 continuation plan
Spec: user's Phase 6–8 continuation attachment, plus current PRODUCT.md and DESIGN.md.
Architecture: keep Flask/SQLAlchemy/Jinja/WTForms/vanilla JS/Chart.js. There is no React, ledgerService JavaScript API, or Zod; document and test the real app/ledger.py API rather than inventing them.
## Phase 6
- [x] Trace real-CSRF composer -> WTForms -> ledger.minor/save_transaction -> DB -> Jinja formatted amounts and reports. Add tests/test_integration_flow.py.
- [x] Fix demonstrated interface/format regressions with red-green evidence. Purge only selectors/templates proved unused across app/tests (retain dynamic JS selectors).
## Phase 7
- [x] Motion: 160ms cubic-bezier(0.22,1,0.36,1), interruptible Web Animations, reduced-motion bypass, no spring dependency. Native modal/focus semantics preserved.
- [x] Mobile <640px: composer and transaction filters become bottom sheets with 44px controls, drag handle swipe-dismiss and keyboard/close alternatives. Busy saves cannot dismiss.
- [x] Progressive local filtering: tenant-scoped bounded data snapshot; memory only, URL filters; server fallback for partial/oversized data; shared integer formatter; safe text DOM rendering. Measure warmed computation+DOM handler; do not claim network/chart paint under10ms without evidence.
- [x] Tests for payload isolation/amount formats, filter equivalence and monetary precision, interrupt/gesture behavior; real browser desktop/mobile checks plus controller-level reduced-motion/gesture checks. OS/browser gesture simulation was not exercised.
## Phase 8
- [x] PRODUCT_AGENT.md standalone reference with actual signatures/schema, design/motion/security contracts, grill->PRD->vertical tickets->TDD workflow; graceful equivalents when slash commands unavailable.
- [x] Five repo-specific feature recommendations with exact touchpoints, effort/impact and self-contained build prompts. No implementation of roadmap features.
- [x] Validate agent with baseline vs informed scenarios, fresh code review, complete tests, updated source ZIP/patch and full-code deliverable.
Decisions: user explicitly waived approval pauses and questions. INR reports; integer paise; original downloaded project/database untouched. Use exact decimal input to server, never JavaScript float multiplication for writes. Filtering optimizes browser DOM work, not React. Financial snapshot data is never persisted in localStorage/sessionStorage. No background data posting.
