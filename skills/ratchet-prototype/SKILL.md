---
name: ratchet-prototype
description: Builds a single-file, clickable HTML prototype of a feature that follows the project's DESIGN.md, with every UI state switchable by URL (?state=…) and stable data-testid hooks, so it can be demoed to a human or client and used as the pixel reference for automated UI review. Use for "prototype", "mockup I can click through", "show me what it would look like before we build it", or whenever the Ratchet loop needs a UI reference and none exists.
---

# Ratchet Prototype

The prototype does two jobs. It's a **demo**: the human or client clicks through it and approves the direction before real code is written. And it's the **reference**: the UI gate compares the real app against it, state by state. Build it so both jobs are easy.

## Inputs
- `DESIGN.md` (config `design_md`). This is **mandatory**. If it's missing, stop and ask for `ratchet-design-system` to run first.
- `checkpoints.json`, for the full scope and every checkpoint's `ui_states`
- The app's existing layout (shell, sidebar, header), so the prototype sits inside what the app really looks like. Read the relevant components or a screenshot.
- `.ratchet/learnings.md`

## Hard requirements

1. **One file**: `<run>/reference/prototype.html`. Inline CSS and JS. No build step. It works from `file://` and offline. System fonts, or the exact webfont DESIGN.md names (a Google Fonts `<link>` is fine).
2. **Tokens, not guesses.** Put every color, size, radius and space in CSS variables copied from DESIGN.md, and reference the variables. Don't hard-code a hex value the design file doesn't contain.
3. **Addressable states.** Every `ui_states[].name` across all checkpoints must be reachable directly by `?state=<name>` (and optionally `#/<route>`). Render deterministic fake data per state: fixed names, fixed dates, no `Math.random()`, no `Date.now()` in rendered text.
   ```js
   const STATE = new URLSearchParams(location.search).get('state') || 'default';
   ```
   Also add a small floating state switcher, visible only with `?switcher=1`, so humans can flip states. It must never appear in UI-gate captures.
4. **Real interactions.** Buttons, forms, validation, dialogs and toasts work in-page, with plain JS state. Transitions between states happen the way they will in the app (submit → loading 600ms → success).
5. **Test hooks.** Add `data-testid` on every interactive element and major region (`nav-approvals`, `approve-btn-<id>`, `empty-state`). The implementer is told to reuse the same ids, which lets the capture script drive both sides with one selector.
6. **Fidelity to the shell.** Match the app's real chrome (sidebar width, header height, fonts) so the reviewer compares like with like. Keep out-of-scope areas simple but present.
7. **Accessibility basics**: semantic elements, labels on inputs, visible focus ring per DESIGN.md.

## Process
1. List the screens × states from the checkpoints and write them as a comment block at the top of the file (it acts as the index).
2. Build the shell, then each state from simplest to fullest, following checkpoint order.
3. Self-check by opening it headless if Playwright is available (`node <ratchet-ui-compare>/scripts/capture.mjs --smoke <file>`), or at least re-read the file. Every state renders, there are no console errors, and no `undefined` appears in the text.
4. Add a `<!-- RATCHET-PROTOTYPE states: a,b,c -->` marker line so tools can list the states.

## Output
Return ≤8 lines: the file path, the list of states with their `?state=` URLs, any design decision DESIGN.md didn't cover (these should go back into DESIGN.md), and the `data-testid` naming scheme.
