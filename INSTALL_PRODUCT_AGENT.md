# Use the Xpense product agent

PRODUCT_AGENT.md is a complete standalone reference with YAML name/description, verified architecture, exact ledger signatures, AST-derived model fields and the feature grill -> PRD -> tickets -> TDD workflow. FEATURE_ROADMAP.md contains five self-contained next-feature prompts. No roadmap feature has been implemented by generating these documents.

1. Claude skills: copy PRODUCT_AGENT.md to .claude/skills/xpense-product-strategist/SKILL.md in the intended repository or personal skill directory. The folder and entry filename are required; dropping an arbitrarily named file beside skill folders is not equivalent.
2. Cursor rules: create .cursor/rules/xpense-product.mdc, use Cursor's rule frontmatter (description plus its activation setting), and copy the Markdown body below PRODUCT_AGENT.md's opening YAML into it. The contract is standalone; no external skill availability is assumed.
3. Custom GPT: attach PRODUCT_AGENT.md as a Knowledge file and tell the GPT to use it for Xpense product strategy and to perform the documented workflow. The full schema exceeds a short instruction field; do not truncate it to fit. With no repository connection it can produce grounded drafts, but cannot claim current code or tests were verified.
4. Codex or another skill loader: copy it as the loader's required SKILL.md entry point. Existing app-specific instructions and explicit user directions still take precedence; this document does not grant external posting or deployment authority.
5. Start with one complete prompt from FEATURE_ROADMAP.md. Read the generated PRD/tickets and the stated assumptions. Where slash commands are unavailable, the agent must execute the documented local equivalent rather than claim an invocation occurred.

This task generated the artifact only; it did not change personal agent settings, post issues, migrate a database or deploy the app. Source remains authoritative after future changes.
