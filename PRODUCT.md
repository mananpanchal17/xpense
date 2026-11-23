# Xpense product context

Xpense is a personal finance ledger built with Flask, SQLAlchemy, Jinja, Bootstrap and Chart.js. Its primary job is recording daily transactions quickly and understanding available money, monthly spending and upcoming obligations. Preserve the established routes, account forms and ledger behavior.

Reporting uses INR. Accounts retain their native currencies and transactions retain recorded exchange snapshots. Never imply a cached conversion is a live bank balance. Calendar-month budgets use Asia/Kolkata and do not carry unused money forward. Refunds reduce spending on the refund date; transfers and split entries retain their ledger relationships.

The overview answers: how much money has flowed in and out, and how is this month's spending progressing against the plan? The main action is adding a transaction, with a keyboard shortcut and a normal route fallback. Follow-up questions are answered by recent transactions, category budgets, reports, planning signals and optional account details.

People use this tool for short daily checks and longer account reconciliation, on desktop and phone, in daylight and low light. Both Zinc themes must remain equally legible. The interface is an operating tool: density, predictable controls and accurate figures take priority over decoration.

Do not invent financial data or fill missing values with reassuring examples. Sample data is an explicit action for empty accounts. Loading, validation failure, empty and unavailable chart states must be visible and recoverable. No deployment, destructive migration or architecture rewrite is part of this design work.
