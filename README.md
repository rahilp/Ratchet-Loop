# Ratchet Loop

**Ratchet Loop** is a checkpoint-and-gate build workflow for AI coding agents, and it works with any model provider. Like a ratchet, it only turns one way: every checkpoint that clears its gates is locked in, and nothing slips back. You ask one skill, the **orchestrator**, to build a feature. It splits the work into small **checkpoints**, and no checkpoint moves forward until it passes four **gates**:

| # | Gate | What proves it | Enforced by |
|---|---|---|---|
| 1 | **Behavior** | Planned tests, written *before* the code, must first fail and then pass | `ratchet.py` runs the tests itself and checks every planned case ID is still there |
| 2 | **UI** | App matches the prototype/reference in the *same state*, visually and interactively | Two isolated reviewers (visual + behavior), from a provider other than the host when available, list every difference; any code-fixable one blocks |
| 3 | **Adversarial** | Reviewers from a provider other than the host when available (otherwise the host's own sub-agents) assume the code is wrong; a fixer fixes everything they find | Loop runs until **every** required reviewer returns zero findings on the *current* code; checkpoint risk decides how many reviewers |
| 4 | **You** | You use the feature | Approval only counts if a real user message arrived after the loop paused |

You're needed **twice**: once to approve the checkpoint plan in the viewer, and once to review the finished feature. Anything you ask to change becomes new checkpoints that go through all the gates again, and the lesson is written to `.ratchet/learnings.md`, which every agent reads.

> An attempt is allowed to be wrong. It is not allowed to ship until it isn't.

## What's inside

```
ratchet-loop/
├── .claude-plugin/plugin.json
├── hooks/hooks.json                 Stop (Ralph-style exit 2), UserPromptSubmit (human log), PreToolUse (guard)
├── skills/
│   ├── ratchet-orchestrator/          ← the one skill you prompt
│   │   ├── SKILL.md
│   │   ├── scripts/ratchet.py         state engine: checkpoints, gates, fingerprints, evidence, viewer, hooks
│   │   ├── assets/viewer.html       checkpoint viewer (plan review + live progress)
│   │   └── references/schemas.md    every file format
│   ├── ratchet-tdd-planner/           plan tests before code (code vs browser layer, edge cases)
│   ├── ratchet-design-md/             create/extract DESIGN.md (exact tokens, component states)
│   ├── ratchet-prototype/             single-file clickable HTML prototype with ?state= switching
│   ├── ratchet-ui-compare/            Gate 2: capture.mjs (Playwright) + ui_review.py (isolated reviewers)
│   └── ratchet-adversarial-loop/      Gate 3: adversary ↔ fixer loop
└── agents/
    ratchet-app-scout            ratchet-checkpoint-planner   ratchet-test-planner
    ratchet-design-system        ratchet-prototyper           ratchet-test-writer
    ratchet-implementer          ratchet-test-runner          ratchet-visual-reviewer
    ratchet-behavior-reviewer    ratchet-adversary            ratchet-fixer
```

## Install

Ratchet runs under any of these agent CLIs. The hooks that enforce the gates are wired into each one's own hook system.

| Agent CLI | Install | Hooks it uses |
|---|---|---|
| **Claude Code** | `claude --plugin-dir /path/to/ratchet-loop` (or `/plugin install` from a marketplace) · or `./install.sh --host claude` | `Stop` · `UserPromptSubmit` · `PreToolUse` |
| **Codex** | install the folder as a Codex plugin (it ships `.codex-plugin/plugin.json`) · or `./install.sh --host codex` | `Stop` · `UserPromptSubmit` · `PreToolUse` |
| **Gemini CLI** | `./install.sh --host gemini` | `AfterAgent` · `BeforeAgent` · `BeforeTool` |
| **GitHub Copilot CLI** | `./install.sh --host copilot` | `agentStop` · `userPromptSubmitted` · `preToolUse` |

Add `--project` to install for the current repo only (`.claude/`, `.codex/`, `.gemini/`, `.github/hooks/`), or use `--host all`. If the skills are already on disk, `ratchet.py install-hooks --host <cli> [--scope project|user] [--dry-run] [--uninstall]` just (re)writes the hooks. It's safe to run twice, and it leaves your own hooks and settings alone.

**Check it's live:** `python3 <skills>/ratchet-orchestrator/scripts/ratchet.py doctor` shows which CLIs have Ratchet hooks and when each hook last fired in this project (`doctor --json` prints just the `hooks` object: `host`, `installed`, `heartbeat_seen`, `fresh`, `missing`). If no hook has fired in the last 24 hours, `ratchet.py next` adds a warning telling the agent to offer to install the hooks (nothing installed) or to ask you to restart the CLI (installed but silent). If you'd rather build without hooks, say so: the agent runs `ask hooks-waive`, which prints a question naming the exact reply that counts (\"yes, go ahead without hooks\"; also accepted: \"yes skip the hooks\", \"yes build without hooks\", \"go ahead without hooks\", \"skip the hooks\", \"proceed without hooks\", case and punctuation ignored, and a trailing \"please\" is also ignored). Any other reply, including a bare \"yes\", is not consent. After that reply the agent runs `hooks-waive`, `status` says hooks were skipped at your request (the checks are then only advice), and nothing is ever blocked. The note clears once hooks are installed or fire.

**Requirements:** Python 3.8+, git (for fingerprints and per-checkpoint commits), and Node 18+ plus Playwright for the UI gate (`npm i -D playwright && npx playwright install chromium`). Optional, for reviewers from other providers: any of the `claude`, `codex`, `gemini` or `copilot` CLIs, and/or `GEMINI_API_KEY` for Gemini as a UI reviewer. With none installed, the host's own sub-agents do every review.

## Use

```
Use the ratchet orchestrator to add leave approvals: managers can approve or reject
their reports' leave requests with a reason, and employees see the outcome.
```

1. The orchestrator starts `ratchet-app-scout` and `ratchet-checkpoint-planner` in parallel. The planner may ask you a few questions.
2. If there's UI and no reference, it creates `DESIGN.md` (if missing), then an HTML prototype, and plans the tests.
3. **The viewer opens.** Review the sequence, the "done when" lines, the planned tests and the prototype. Reply `approve` or ask for changes.
4. It builds checkpoint by checkpoint: tests → red → implement → Gate 1 → Gate 2 → Gate 3 → commit. The Stop hook won't let it quit halfway.
5. **Final review.** Try the feature. Approve, or describe changes. Changes become new checkpoints.

### The viewer link
The orchestrator puts the viewer link in its message at both stops: plan approval and final review. You can also get it yourself (`ratchet.py` lives in `skills/ratchet-orchestrator/scripts/`):

| You want | Run | You get |
|---|---|---|
| The link | `ratchet.py view` | the live URL if a server is running, otherwise a `file://` link to `.ratchet/runs/<feature>/viewer.html` |
| A live page that updates as gates pass | `ratchet.py view --serve [--open]` | starts in the background on a free port (4477 and up) and prints `http://127.0.0.1:<port>/` |
| To open it from another machine | `ratchet.py view --serve --bind 0.0.0.0 [--public-host my-box.local]` | `http://<host>:<port>/?t=<token>`: share the whole URL, since requests without the token get 403 |
| To stop it | `ratchet.py view --stop` | |
| Every lane on one page (director) | `ratchet.py lanes --serve --scan <dir> [--bind 0.0.0.0]` | one dashboard with each lane's phase, progress and what it's waiting on, linking into each lane's viewer through the same port |

The server only serves the viewer, its live data and the prototype, never the rest of `.ratchet/` (state, evidence, logs). The URL is saved in `.ratchet/viewer.json` and reported as `viewer_url` by `ratchet.py status --json` and `lanes --json`.

Status any time: `ratchet.py status` (plain text, `--json` for tools); `ratchet.py plan-summary` shows the plan step by step.

### Modes
- `--human-review end` (default): 2 stops.
- `--human-review each`: review every checkpoint.
- `--human-review none`: autonomous after plan approval, with gates just as strict. You get a stack of commits with evidence.
- Logic-only work (API, refactor, migration) skips the UI gate. For screen migrations, point the reference at the old screen instead of a prototype.

Settings live in `.ratchet/config.json` (`ratchet.py config --set key=value`): the reviewer roster and `review_depth`, retry and quota caps, `regression_command`, `ui_globs`, the approver mode, and the app URL and start command.

### Works with any provider
**The host** is whichever model runs the loop: Claude, GPT/Codex, Gemini, a Copilot model, or anything else. Ratchet detects the common agent CLIs from their environment, or you set `host_provider` in `.ratchet/config.json`. Nothing in the loop is tied to one provider. What changes with the host is who counts as a *different* provider.

**Reviewers from a different provider, when there is one.** Every checkpoint gets a `risk` (`low` / `normal` / `high`) in the plan, which you see at approval. Risk decides **how many** independent reviewers must approve, never how strict they are: every finding is fixed.

| Risk | Review slots (first available wins) |
|---|---|
| low (skeletons, copy, renames) | host, both lenses |
| normal | host standards · Codex → Gemini → Claude → Copilot correctness |
| high (auth, money, data, deletion, locks) | host standards · host correctness · Codex → Gemini → Claude → Copilot correctness · Gemini → Claude → Codex → Copilot standards |

A provider is skipped when it's **the same as the host** (the host's own sub-agents already cover that model family), isn't installed, or just failed on quota, auth or a timeout. Failed providers are skipped for 6 hours (`unavailable_ttl_hours`). When no other provider is left, the host's own sub-agents fill the slot, so the loop never stalls and the reviewer count holds. `ratchet.py reviewer-status` shows the host and who's available, and `--reset <id>` retries one early. Substitutions are logged, so you can see when a checkpoint was reviewed by the host's model only.

The UI gate works the same way: config `ui_reviewers` lists providers per role (visual: Gemini → Claude → Codex; behavior: Claude → Gemini → Codex), skipping the host and anything unavailable, with the host's own reviewer sub-agents as the fallback.

**Hooks on every host.** All four CLIs call the same entry point, `ratchet.py hook <stop|prompt|guard> --host auto`. It works out the host from the payload's shape, normalizes it (camelCase vs snake_case, `toolArgs` sent as a JSON string, Codex's `apply_patch` and list-form shell commands, Gemini's `write_file` / `run_shell_command`), runs one shared decision, and answers in that host's dialect: exit 2 plus a reason for Claude Code, Codex and Gemini, and JSON (`decision: block` / `permissionDecision: deny`) for Copilot. Gemini's `AfterAgent` block retries the agent's turn with the reason as feedback, which is the same effect as a Stop block. On internal errors the hooks **fail open** and log to `.ratchet/hook-errors.log`, because Copilot denies every tool call when a `preToolUse` hook crashes. `tests/test_hooks.py` replays each CLI's documented payloads. Sub-agent definitions (`agents/*.md`) are Claude Code's format; on other hosts the orchestrator reads the same prompts from `agents/` and hands them to that CLI's sub-agents.

`external_review.py` hands the other provider's CLI the full diff, the standards and the learnings, runs it read-only, and normalizes its answer into findings stamped with the code fingerprint. Built-in presets cover the `claude`, `codex`, `gemini` and `copilot` CLIs (flags can differ between CLI versions, so override `command` if a preset doesn't match yours). Add any other provider as a `command` entry in the `reviewers` roster and name it in a slot. Two quota guards block and escalate rather than lowering the bar: `max_adversarial_rounds` (per checkpoint) and `max_adversarial_rounds_run` (whole run).

### Running many lanes under a director
Ratchet runs the loop inside one lane (one worktree). A director above it can run many lanes in parallel:
- `ratchet.py lanes --scan <dir> [--json]` shows each lane's phase, progress, and whether it's `waiting on director`.
- `ratchet.py --root <lane> status --json` gives one lane's full machine-readable state.
- **Director-signed approvals.** Start the director's session with `RATCHET_ROLE=director`, run `ratchet.py director-key` once, then `ratchet.py --root <lane> director-enroll` for each lane. In an enrolled lane, chat messages stop counting as approvals (a builder's "user" messages come from the director). Plan approval, final review and unblocking need `ratchet.py --root <lane> director-sign <action>` → `--director-token <t>`. A token only works for that lane's current pause, and approvals are logged as `by: director`. Builder sessions can't read the key or run the signing commands (guard hook).

Merge order, quota routing across vendors, and project-specific checks (cost budgets, eval locks) belong to the director, not to Ratchet.

## What makes the gates real (not just prompts)

- **Evidence, not claims.** Agents don't report "tests pass"; `ratchet.py` runs them. Reviewers don't report "PASS"; `ratchet.py` recomputes the verdict from their findings.
- **Red first.** Tests that pass before the feature exists are rejected as vacuous.
- **No deleting tests.** Every planned case ID must still be in the test files at each behavior run.
- **Code fingerprints.** Every gate pass is stamped with a hash of the working tree. Change the code afterwards and the pass goes stale: behavior always re-runs, UI re-runs if UI files changed, and the adversaries re-review.
- **INVALID comparisons.** Reviewers reject screenshot pairs that aren't in the same state, so the capture is fixed rather than the app "fixed" to match a different state.
- **Isolated reviewers.** UI reviewers run as new `claude -p` sessions in a temp folder containing only screenshots, accessibility trees and DESIGN.md. They never see CLAUDE.md, code or chat.
- **Protected state.** A PreToolUse hook blocks hand-edits to `state.json`, evidence, the human log and a lane's director enrollment. It also blocks nested agent sessions (`claude -p`, `codex exec`, `gemini -p`, `copilot -p`) and inline `RATCHET_HUMAN_OVERRIDE` that could impersonate a human, and any access to the director key or signing commands from a non-director session.
- **Stop hook.** Exit code 2 feeds the next required action back to the agent (the Ralph-loop trick). Stopping is allowed only at the two review points, when blocked, or when paused. There's a safety valve after 40 blocked stops without progress.
- **Retry limits → you.** When a gate won't converge, the loop blocks and asks you instead of lowering the bar.
