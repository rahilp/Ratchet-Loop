---
name: ratchet-ui-compare
description: Runs the Ratchet UI gate — captures the reference (HTML prototype, old app screen, or design export) and the real app in the SAME state, replays the same interaction journey on both, then has two isolated reviewers (visual fidelity + interaction behaviour) that never see project instructions list every difference with severity and on-screen location. Any code-fixable difference blocks the gate. Use when comparing an implementation to a prototype or mockup, checking a screen for pixel/spacing/typography drift, verifying a migrated screen matches the original, or at Gate 2 of the Ratchet loop.
---

# Ratchet UI Compare (Gate 2)

UI equivalence can't be written down in a prompt. A human spots a title that's slightly too small or a divider that's too dark, but nobody writes those in a spec. Pixel diffing fails too, because two renderers never produce identical bytes. So this gate *looks*: a perfectionist reviewer compares screenshots taken in matched states, describes and locates every difference, and the loop fixes and re-checks until nothing fixable remains.

```bash
UIC="<this skill's base directory>/scripts"
RUN=.ratchet/runs/<slug>
```

## 1. Preconditions
- The app is running at `config.app.url` (start it with `config.app.start_command` in the background if not, and wait for the healthcheck).
- Playwright is available: `node $UIC/capture.mjs --smoke $RUN/reference/prototype.html` should print ✓ per state. If it's missing: `npm i -D playwright && npx playwright install chromium` (ask before adding a devDependency to a repo that doesn't have one; as an alternative, install it in a scratch folder and set `PLAYWRIGHT_MODULE`).
- The implementer reused the prototype's `data-testid`s. If not, selectors in the spec may differ per side, and that's allowed.

## 2. Write the capture spec `$RUN/states/CPn.json`
Schema: `ratchet-orchestrator/references/schemas.md` §5. Rules:
- **One entry per `ui_states` item** of this checkpoint. `reference.path` is usually `?state=<name>` for a prototype. `app.path` + `steps` put the real app in the *same* state (same data, same open panel, same form values). Use seed data or fixtures. Never compare "some data" against "other data".
- **Scope** = this checkpoint's `ui_scope`. The scope grows with each checkpoint: a skeleton checkpoint judges only the nav and title, even though the reference shows the full screen.
- **journey**: 2–6 steps that exercise what this checkpoint added (open dialog → submit empty → see validation). The same steps run on both sides.
- `mask` anything nondeterministic (clocks, avatars from the network, charts with live data).
- Login steps go under `login.app` (credentials come from `app-scout.json`).

## 3. Capture
```bash
node $UIC/capture.mjs --spec $RUN/states/CPn.json --out $RUN/reviews/CPn/capture
```
Before reviewing, check the output: if a side shows `fatal` or failed setup steps, fix the spec or the app state first. A broken capture wastes a review and returns INVALID.

## 4. Review with two isolated reviewers (in parallel)
Round folders are zero-padded: in the commands below `round-NN` is the current round (`round-01`, then `round-02`, and so on for each new review of the same checkpoint).
```bash
python3 $UIC/ui_review.py review \
  --manifest $RUN/reviews/CPn/capture/manifest.json \
  --scope "<ui_scope of CPn>" --cases $RUN/tests/CPn.json \
  --out $RUN/reviews/CPn/round-NN [--visual gemini,claude] [--behavior claude,codex]
```
- **Design file.** Taken from config `design_md` (default `DESIGN.md`, relative to the project root) when it exists; pass `--design <path>` only to override it.
- **Provider choice.** Each role goes to the first *available* provider in config `ui_reviewers` (default: visual gemini → claude → codex; behavior claude → gemini → codex) that is **different from the model hosting the loop**. A provider that isn't installed, has no key, or fails is skipped automatically. Gemini is a strong visual reviewer because of its spatial awareness, and it runs through `GEMINI_API_KEY`.
- **Isolation.** Every reviewer works inside a temp folder that holds only the screenshots, accessibility trees, the design file and INPUT.md: no project instructions, no code, no chat history. It judges the app against the reference, and nothing else. (Its instructions come from the `ratchet-visual-reviewer` / `ratchet-behavior-reviewer` agent files, whichever provider runs them.)
- **Host fallback (exit code 4).** If no other provider is available for a role, the script stages that role's folder and prints it. Spawn the host's own `ratchet-visual-reviewer` and/or `ratchet-behavior-reviewer` sub-agents in parallel, each told "work only from <folder>/INPUT.md; do not read anything else", and have each write its JSON to `$RUN/reviews/CPn/round-NN/<role>.json`. Then run the `combine` command it printed:
  `python3 $UIC/ui_review.py combine --inputs <visual.json> <behavior.json> --out $RUN/reviews/CPn/round-NN/combined.json --checkpoint CPn`

## 5. Record the gate
```bash
$RATCHET ui-record CPn --review $RUN/reviews/CPn/round-NN/combined.json
```
`ratchet.py` recomputes the verdict itself: any `fixable_in_code: true` difference is a **blocker**.
- **INVALID**: the states didn't match (e.g. unfulfilled vs fulfilled order). Fix the capture spec or seed data and go back to step 3. Don't touch app code for this.
- **FAIL**: spawn `ratchet-implementer` with the combined.json path. The only job is to fix the listed differences using DESIGN.md tokens. Then `$RATCHET behavior CPn` (the fix must not break behavior), then capture and review again as the next round.
- **PASS**: continue to the adversarial gate.

## What never happens here
- The orchestrator never "interprets away" a difference, or edits a review file, or tells a reviewer the fix is done. The reviewers re-look every round.
- The prototype is never changed to match the app. If the human decides the app is better, that's a human decision: record it with `ratchet.py learn`, and update the prototype and DESIGN.md explicitly.
