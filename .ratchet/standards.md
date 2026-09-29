> DRAFT — inferred by ratchet-app-scout; review and edit.

# Standards for the Ratchet Loop plugin

1. The engine is a single stdlib-only Python 3.8+ script; add no third-party imports. Example: skills/ratchet-orchestrator/scripts/ratchet.py (`from __future__ import annotations`).
2. Gate state changes only through `ratchet.py` subcommands (`cmd_*`), never by hand-editing state.json, evidence or the human log. Example: cmd_behavior in ratchet.py.
3. Gates verify evidence themselves (run tests, recompute verdicts); never trust an agent's claim of PASS. Example: cmd_behavior, cmd_ui_record, cmd_adv_record.
4. Every gate pass is stamped with the code fingerprint and goes stale when code changes. Example: cmd_fingerprint.
5. Human approval is accepted only from a real user message recorded by the UserPromptSubmit hook; no agent message, env var or flag may impersonate it. Example: cmd_human_approve.
6. The PreToolUse hook must block hand-edits to protected state, nested agent sessions and director key access. Example: hooks/hooks.json and the `hook` handler in ratchet.py.
7. Stay provider-neutral: no code path or doc may assume Claude is the host; use `host_provider` and the reviewer-selection logic. Example: cmd_reviewers in ratchet.py.
8. Every hook payload dialect (Claude, Codex, Gemini, Copilot) must be handled with a test in tests/test_hooks.py.
9. A new provider or host is added by extending the adapter tables, not by branching in gate logic.
10. When no other-provider reviewer is available, fall back to host sub-agents and log the substitution; never stall or lower the bar. Example: cmd_reviewer_status.
11. Errors exit through `die(msg, code)` with an actionable message; no bare tracebacks for user errors. Example: `die` in ratchet.py.
12. Persist state via `save_state` on the run object, not ad-hoc json writes. Example: `save_state` in ratchet.py.
13. Subprocess calls use argument lists (no shell=True with interpolated input) and explicit timeouts for external CLIs. Example: skills/ratchet-adversarial-loop/scripts/external_review.py.
14. Secrets (viewer token, director key) never appear in process lists, logs or served files. Example: "token not visible in the process list" in tests/test_viewer.py.
15. The viewer serves only whitelisted run artifacts; tests must assert what is NOT served. Example: tests/test_viewer.py.
16. Viewer/dashboard HTML is a single self-contained file with no external network dependencies. Example: skills/ratchet-orchestrator/assets/viewer.html.
17. Background servers must be stoppable (`--stop`) and leave no process behind; tests verify this.
18. Skills are markdown with YAML frontmatter (`name`, `description` with trigger phrases); the name matches the folder. Example: skills/ratchet-tdd-planner/SKILL.md.
19. Agents are markdown in agents/ with frontmatter `name`, `description`, `tools`; name is `ratchet-<role>`. Example: agents/ratchet-fixer.md.
20. Agents state a bounded return format (e.g. "Return ≤10 lines") and stay in their role; reviewers do not fix, fixers do not review.
21. Data file shapes are defined once in skills/ratchet-orchestrator/references/schemas.md; change the schema doc together with the code that reads/writes it.
22. Tests are never weakened, skipped or deleted; planned test IDs (`[CPn-X01]`) must remain present.
23. Tests are runnable scripts (`python3 tests/test_x.py`) using a `check(name, cond)` helper, exit non-zero on failure, and build a throwaway project in a temp dir. Example: tests/test_hooks.py.
24. Tests must not touch the real .ratchet/ of the repo; use tempfile projects and clean up spawned servers.
25. Behavior changes to ratchet.py need a test in tests/ in the same change.
26. README.md and the plugin manifests (.claude-plugin/plugin.json, .codex-plugin/plugin.json) stay in sync with what the skills and agents actually do.
27. UI reviewers are isolated (screenshots, a11y trees, DESIGN.md only); never pass code, CLAUDE.md or chat to them. Example: skills/ratchet-ui-compare/scripts/ui_review.py.
28. Never commit .ratchet/ run state or __pycache__.
