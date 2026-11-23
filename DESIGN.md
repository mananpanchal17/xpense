# Xpense design system

Xpense uses an achromatic ledger direction for operating screens. The user pinned a precision fintech interface: small headings, compact rows, neutral navigation and financial meaning carried by numbers. The Taste reference is Achromatic ledger; the pinned Zinc palette and native font replace its optional colour and font suggestions.

## Tokens

| Role | Light | Dark |
| --- | --- | --- |
| Canvas | #FAFAFA | #09090B |
| Surface | #FFFFFF | #09090B |
| Raised tone | #F4F4F5 | #18181B |
| Primary text | #18181B | #FAFAFA |
| Secondary text | #71717A | #A1A1AA |
| Border | #E4E4E7 | #27272A |
| Income / within budget | #047857 | #34D399 |
| Approaching limit | #92400E | #FBBF24 |
| Over limit / error | #BE123C | #FB7185 |

Primary actions use ink on the canvas colour; secondary actions use a thin neutral outline. Emerald means income or a healthy budget position, amber means approaching a limit, and crimson means an overrun or an error. Expenses use ink; an expense is not intrinsically an error. Zero, unavailable and comparative values remain neutral. Colour never replaces a label, sign or progress value.

## Typography and density

Use the native UI stack: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif. No remote fonts. Global tabular lining numerals include dates, currency, table content and chart labels. Canvas-drawn chart text must use this same family and numeric shaping in the chart configuration.

Body text is 14px with 1.5 line height. Page titles are 24px semibold; section headings are 14–15px; supporting text is 12–13px. Financial summary values are 20–26px, with net cash flow at 30px. Financial values do not wrap midway through a currency amount. No uppercase tracked kickers, decorative mono or oversized greetings.

Transaction rows are approximately 42px before optional secondary account text. Form controls are 38px on desktop; small row actions are 28px. Phone inputs use 16px text to preserve usable native editing. Data tables retain horizontal scrolling where needed and hide the supplementary payment column on narrow displays. Never truncate the amount to fit a table.

## Layout and components

The desktop sidebar is 232px with 32px navigation targets, neutral selected fill and text labels. It shares the canvas ground and has one thin border. Content has equal 20–40px gutters and a 20px layout gap. A shared executive summary strip has three or four metric columns, with neutral dividing lines rather than separate icon tiles. At 768px it becomes a two-column strip, with an odd third value spanning the row.

The overview uses a two-to-one content grid, stacking below 768px. The monthly pacing panel shows actual budget usage, labelled amounts and the real percentage. Charts occupy compact heights and use actual ledger values. All named blocks should show their header and useful first content early in the page. Long account and planning detail can live behind the labelled details disclosure.

Cards are flat, 1px bordered, with an 8px corner radius and 20px internal padding. Use no gradients, glow, neumorphic effects, tinted icon squares or resting shadows. Real floating menus and dialogs may have a restrained shadow. Category pills are small neutral labels. The shared CSS retains compatibility selectors for accounts, investments, commitments, assistant, forms, reports and dialogs.

The composer is a native modal dialog with a clear title, visible close control, prominent amount field, inline category/date/account controls, labelled validation and a submit state. Its opener keeps its regular transaction-page link as a fallback. The C shortcut hint belongs beside the action, without taking visual precedence. Keyboard behavior and error recovery are handled by the composer controller.

## Interaction and access

Every control has a visible 2px focus outline with an offset. Input errors use both a message and invalid state; pending submissions show busy status and disabled controls. Disabled controls retain recognisable shape. Empty states state what is missing and offer a concrete next action. Chart loading keeps its footprint as a quiet dashed placeholder; failed charts preserve ledger/report access.

The mobile header replaces the sidebar below 992px; the menu opens as a plain drawer with a solid scrim. The menu must remain dismissible and synchronise its expanded state. Dialog height is bounded by the visible viewport and its body scrolls. Motion is limited to brief control feedback and opening navigation; reduced motion removes travel. No content depends on an entrance animation.

## Verification boundary

This system is implemented in app/static/css/style.css and app/templates/base.html. Final page screenshots, chart colors, composer behavior and keyboard testing must be verified against the integrated app after the dashboard and composer phases are complete. Documentation does not substitute for browser evidence.

## Motion and mobile sheet contract

Composer, linked-entry dialogs, transaction filter popups and row insertion/removal use at most 160ms with cubic-bezier(0.22, 1, 0.36, 1). This user-pinned contract also covers keyboard openings. There is no page entrance or decorative motion. Only opacity and transform animate. The WAAPI controller samples computed styles before cancelling a running effect, retargets from that frame and removes inline/effect styles after settling. A superseded close or remove cannot apply a stale DOM action. Reduced motion bypasses WAAPI entirely.

Native dialogs open before their entry motion and close after their exit settles. Escape, visible close controls and backdrop clicks share one close path; pending saves block dismissal. Focus returns only after the dialog closes. Openings can interrupt exits without closing the reopened dialog. XpenseMotion exposes Promise-returning open/close/insert/remove plus reset and bindSheet; false means an operation was superseded.

Below 640px, composer, linked-entry and filter dialogs are bottom sheets, full width, at most 90dvh tall, with safe-area padding and internal scrolling. Buttons, selects and editable inputs have 44px minimum targets. Only the visible handle starts a swipe. A downward movement over 64px requests dismissal; shorter or cancelled movement returns to rest using the same restrained curve. Drag is clamped between zero and the sheet height, ignores secondary pointers, and is disabled on desktop or during a pending save. Keyboard and visible close controls remain alternatives.
