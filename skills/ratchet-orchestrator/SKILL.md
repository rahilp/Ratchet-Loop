---
name: ratchet-orchestrator
description: Runs the Ratchet build loop (a checkpoint-and-gate workflow) end to end — splits a feature, screen, migration or refactor into small ordered checkpoints, gets the human to approve the plan once, then builds each checkpoint through four hard gates (behavior tests, UI review against a prototype/reference, adversarial code review, human review) using fresh sub-agents, and only stops again for final review. Use this whenever the user says "ratchet", "orchestrator", "build this feature with the loop", "use checkpoints and gates", wants a large feature built to their app's standards, wants to migrate or rebuild a screen, or asks for a long autonomous build that must not cut corners — even if they don't name the skill.
---

# Ratchet Orchestrator

You are the orchestrator. You do almost no implementation yourself. You plan, dispatch fresh sub-agents, record gate results through `ratchet.py`, and keep your own context small. The principle:

> An attempt is allowed to be wrong. It is not allowed to ship until it isn't.

Two ideas make the loop work:
1. **Checkpoints**: small, ordered slices of the feature, smallest and simplest first, each building on the last, so a wrong early decision is caught while it's still cheap to fix.
2. **Gates**: checks that *block* progress. They are not advice. A failed gate is fixed and re-run, never argued past. A Stop hook refuses to let you end your turn while a gate is open.

The human is needed exactly **twice**: once to approve the plan, and once to review the finished feature. The exceptions are `human_review: each` mode and a gate that stays stuck past its retry limit.

## Setup (every run)

```bash
RATCHET="python3 <this skill's base directory>/scripts/ratchet.py"
```

Use the absolute path of this skill's folder. Pass the same `RATCHET` line into every sub-agent prompt. Sub-agents cannot see this skill.

Read before starting:
- `.ratchet/learnings.md` (if present). It holds past human feedback, and it outranks your defaults.
- `references/schemas.md` in this skill, which has every file format. Read it once. Sub-agents get the relevant section pasted or pointed to.

Hooks (installed with the plugin, or by `install.sh --host <cli>` / `$RATCHET install-hooks`, for Claude Code, Codex, Gemini CLI or Copilot CLI): the **stop** hook blocks you from ending your turn until `ratchet.py next` says stopping is OK. The **prompt** hook records real human messages, which `approve-plan` and `human-approve` require. The **guard** hook stops anyone hand-editing `state.json`, evidence, or the human log. If `ratchet.py` says "No human message recorded", you tried to approve on the user's behalf. Stop and wait for them. **If `$RATCHET next` shows a `warning` (see its `hooks` object: `installed`, `fresh`, `missing`)**, the gates aren't being enforced in this CLI: run `$RATCHET doctor`. If hooks are not installed, offer to run `install-hooks` (ask the user first); if they are installed but silent, tell the user to restart the agent CLI. Don't start building until a hook has fired.

## Sub-agent rules

- Spawn every agent below as a sub-agent. In Claude Code that's the Agent tool with `subagent_type` = the agent name. In other agent CLIs, use their sub-agent mechanism and give it the prompt from `agents/<name>.md` in this plugin. When installed as a plugin the name is prefixed (`ratchet-loop:ratchet-implementer`), so use whichever form your agent list shows. Each one gets a fresh context window, which is the whole point.
- Every spawn prompt includes the `RATCHET=…` line, the run folder path, the checkpoint id, the exact files to read (checkpoint entry, test plan, open findings), and "Read .ratchet/learnings.md first."
- Ask each agent for a **short** return value (≤15 lines: what changed, files touched, anything blocking). Never ask for full logs or diffs. Those stay on disk.
- Run independent agents **in parallel**: send them in one message. Examples: app-scout + checkpoint-planner; the test planners for every checkpoint; the two UI reviewers; all of a round's adversarial reviewers.
- Never implement, fix, or edit tests yourself. If you catch yourself about to, spawn the right agent instead.

| Agent | Job |
|---|---|
| `ratchet-app-scout` | Is the app running? Stack, test runner, start command, URL, login for captures. Writes `app-scout.json` and drafts `.ratchet/standards.md` if missing |
| `ratchet-checkpoint-planner` | Feature → `checkpoints.json` (PLAN or EXTEND mode). May return QUESTIONS |
| `ratchet-test-planner` | One checkpoint → `tests/CPn.json` (uses the `ratchet-tdd-planner` skill) |
| `ratchet-design-system` | Writes/updates `DESIGN.md` (uses the `ratchet-design-md` skill) |
| `ratchet-prototyper` | Single-file clickable HTML prototype with switchable states (uses the `ratchet-prototype` skill) |
| `ratchet-test-writer` | Writes the planned tests. They must fail first |
| `ratchet-implementer` | Writes the code for ONE checkpoint, or fixes behavior/UI failures |
| `ratchet-test-runner` | Runs `$RATCHET behavior CPn` and returns a concise diagnosis |
| `ratchet-visual-reviewer` / `ratchet-behavior-reviewer` | Isolated UI gate reviewers (normally launched by `ui_review.py`, used as sub-agents only as a fallback) |
| `ratchet-adversary` | Assumes the code is wrong and hunts for problems against the standards (lens: `standards` or `correctness`) |
| `ratchet-fixer` | Fixes every adversarial finding and nothing else |

## Phase A: Plan (ends at human stop #1)

1. `$RATCHET init "<feature in the user's words>"`. Add `--human-review each|none` if the user asked for per-checkpoint review or full autonomy.
2. **In parallel**, spawn:
   - `ratchet-app-scout`: make sure the app runs, fill `app-scout.json`, set `.ratchet/config.json` `app.*` / `regression_command` via `$RATCHET config --set`, and draft `.ratchet/standards.md` if no written standard exists.
   - `ratchet-checkpoint-planner` (PLAN mode) with the feature request verbatim, plus any reference the user gave (old screen path, docs, screenshots, Figma export).
3. If the planner returns `QUESTIONS:`, ask the user with AskUserQuestion (recall memory first per their preferences), then re-spawn the planner with the answers. Keep this short. It's a clarification, not the review.
4. **Reference for the UI gate.** If any checkpoint has `gates.ui: true`:
   - The user gave a reference (existing screen, design, screenshots)? Put its path or URL in `checkpoints.json → reference` and move on.
   - Otherwise, a prototype is needed. If `DESIGN.md` (config `design_md`) is missing, spawn `ratchet-design-system` first. Then spawn `ratchet-prototyper` with the checkpoints list. It writes `<run>/reference/prototype.html` with a `?state=` switcher covering every `ui_states` name.
5. **In parallel**: one `ratchet-test-planner` per checkpoint that has `gates.behavior: true`.
6. **Start the live viewer, then submit the plan.**
   - `$RATCHET view --serve`. It starts in the background on a free port (or reuses the one already running) and prints `Ratchet viewer started: <URL>`. The page refreshes itself as gates pass, for the whole run.
   - If the user isn't on the machine running the loop (cloud session, SSH, container, a director's worktree), use `$RATCHET view --serve --bind 0.0.0.0` instead. Add `--public-host <name>` when the machine has a name the user can reach (LAN, Tailscale). The URL then carries a `?t=` access token: give the user the whole URL.
   - `$RATCHET plan-submit --open` validates the plan and prints `VIEWER: <URL>`. Fix any validation errors by re-spawning the planner with the error list.
7. **STOP #1.** Tell the user, in 4–6 lines: how many checkpoints, the one-line sequence, which ones get a UI review, and **the viewer link as a clickable URL on its own line** (the `VIEWER:` line; the prototype opens from inside the viewer). Then: "Open the viewer, then reply *approve* or tell me what to change." If you only have a `file://` link and the user may be on another machine, say so and offer the `--bind 0.0.0.0` URL. End your turn. The Stop hook allows it in this phase.
8. When the user replies:
   - Approves → `$RATCHET approve-plan`.
   - Asks for changes → re-spawn the planner (and test planner and prototyper as needed) with the feedback, run `$RATCHET plan-submit` again, and stop again. Also `$RATCHET learn "<durable preference>"` if the feedback is a general preference, not a one-off.

## Phase B: Build each checkpoint (no human involvement)

Loop until `$RATCHET next` reports a review phase. Always do what `next` says. It knows the step order.

**Step 1: Tests (the behavior gate's standard).**
Spawn `ratchet-test-writer` for `CPn` with `tests/CPn.json` and the checkpoint entry. For a large test plan, split the cases across 2–3 writers in parallel, each owning different test files listed in `tests.files`. Then:
`$RATCHET tests-written CPn`, which checks every planned `code` case id appears in the tests, and `$RATCHET red CPn`, which requires the tests to FAIL now.
- If red fails because the tests already pass, re-spawn the test-writer: "tests pass without the feature; make them actually exercise it." Use `--allow-green-first --reason` only for pure refactors that preserve behavior.

**Step 2: Implement.**
Spawn `ratchet-implementer` for exactly this checkpoint: checkpoint entry, test plan, `DESIGN.md` and the reference if it has UI, `.ratchet/standards.md`, learnings. Then `$RATCHET implemented CPn`.

**Step 3: Gate 1, Behavior.**
Spawn `ratchet-test-runner` (it runs `$RATCHET behavior CPn`), or run it yourself if you expect it to pass cleanly. On failure, spawn `ratchet-implementer` with the log path and the runner's diagnosis, then re-run. Tests may not be edited to make them pass. `ratchet.py` detects deleted case ids.

**Step 4: Gate 2, UI** (only if `gates.ui`). Follow the **`ratchet-ui-compare`** skill:
make sure the app is running → write `states/CPn.json` (scope limited to what this checkpoint built — the scope grows with each checkpoint) → `capture.mjs` → `ui_review.py review` (two isolated reviewers, visual + behavior, from a provider other than the host when available, that never see project instructions; exit code 4 means spawn the host's own reviewer sub-agents and `combine`) → `$RATCHET ui-record CPn --review <combined.json>`.
- INVALID means the screenshots weren't in the same state. Fix the capture spec, not the app, and redo.
- FAIL → spawn `ratchet-implementer` with the blocking differences → re-run Gate 1 (`$RATCHET behavior CPn`) → redo Gate 2.

**Step 5: Gate 3, Adversarial.** Follow the **`ratchet-adversarial-loop`** skill:
`$RATCHET reviewers CPn` lists who must review, based on the checkpoint's `risk`. A provider different from the one hosting this loop (Claude, Codex, Gemini, Copilot, via `external_review.py`) is used whenever one is available; otherwise your own `ratchet-adversary` sub-agents (`host-*`) fill those slots. If an external reviewer exits with code 3 it's unavailable: run `reviewers` again and launch its replacement. Launch them all in parallel → `$RATCHET adv-record CPn --reviews <files>` → if not approved: `ratchet-fixer` with `open-findings.json` → `$RATCHET behavior CPn` → UI gate again if UI files changed → every reviewer again. Repeat until all of them return zero findings.

**Step 6: Complete.** `$RATCHET complete CPn`. It re-verifies every gate against the *current* code fingerprint, runs the regression suite, and commits `ratchet(<slug>): CPn <title>`. If it reports stale gates, redo exactly those gates.

Then continue with whatever `$RATCHET next` says. Keep the user informed with one short line per completed checkpoint (e.g. "CP2 ✓ — 3 test rounds, 1 UI round, adversaries approved in round 2"). No step-by-step narration.

**When a gate won't converge.** Each gate has a retry limit (`max_*` in config). When it's hit, `ratchet.py` sets phase `blocked` and the Stop hook lets you stop. Tell the user plainly what's stuck, show the evidence path, and offer 2–3 concrete options. When they decide, `$RATCHET human-approve` resumes (after their message), and you act on their decision. Never lower a standard on your own.

**When the same mistake repeats.** If you see the same kind of failure twice (same UI issue, same review rule), record it: `$RATCHET learn "<rule in one line>" --source "gate:<name>"`. Every future agent will read it.

## Phase C: Human review (human stop #2)

When `next` says `awaiting_final_review` (or `awaiting_checkpoint_review` in `each` mode):
1. Make sure the app is running, and give the user the app URL and the route(s) to try.
2. Run `$RATCHET view` to get the viewer link (the live URL if the server is still up).
3. In ≤8 lines, say what was built (checkpoint titles with commit hashes), what to try (the key flows plus one edge case), and anything the gates flagged that needs human taste. Put the **viewer link on its own line**, and point to its Evidence sections.
4. End your turn. When they reply:
   - Approve → `$RATCHET human-approve`, then `$RATCHET view --stop`. Done. Give a one-line wrap-up.
   - Changes → `$RATCHET feedback --text "<their words>" --learning "<generalizable lesson>"` (repeat `--learning` for each lesson; skip it for one-off tweaks) → spawn `ratchet-checkpoint-planner` in **EXTEND** mode with the feedback → `ratchet-test-planner` for each new checkpoint → `$RATCHET plan-extend`. Then back to Phase B. New checkpoints go through **all** gates. No plan re-approval is needed: the user just told you what they want.

## Modes

- **human_review: end** (default): stops only at plan approval and final review.
- **human_review: each**: stops after every checkpoint. Early checkpoints get close attention; as learnings accumulate, suggest switching to `end`.
- **human_review: none**: autonomous after plan approval. The gates stay exactly as strict. At the end, the user gets committed checkpoints plus evidence instead of one giant diff.
- **Logic-only work** (API, refactor, data migration): the planner sets `gates.ui: false` and there is no prototype.
- **Migration / rebuild**: the reference is the old screen, not a prototype. The capture spec points `targets.reference` at the old app's URL.

## Running under a director (many lanes in parallel)

A director is an orchestrator above Ratchet that runs several lanes at once, one Ratchet run per git worktree, and owns merge order, quotas and cross-lane checks. Ratchet stays the engine *inside* each lane. Nothing about the gates changes.

- **Enrollment.** The director (a session started with `RATCHET_ROLE=director`) runs `ratchet.py director-key` once, then `ratchet.py --root <lane> director-enroll` for each lane. In an enrolled lane, chat messages **no longer count as approvals**, because the "user" messages a builder receives come from the director. Plan approval, final review and unblocking need a director token instead.
- **Approving.** The director runs `ratchet.py --root <lane> director-sign approve-plan` (or `human-approve` / `feedback`) and either runs the approval itself (`ratchet.py --root <lane> approve-plan --director-token <t>`) or sends you the token. The token only works for that lane's current pause. It's logged as `by: director`.
- **You, the lane orchestrator, never** read the key, sign, enroll, or set `RATCHET_ROLE`. The guard hook blocks it, and trying is a violation, not a workaround. When the lane pauses, end your turn with a short status. The director sees `waiting_on: director` in `ratchet.py status --json` or `ratchet.py lanes --scan <dir>`.
- **Viewer.** Start this lane's viewer with `ratchet.py view --serve` (add `--bind 0.0.0.0` if the director or the user is on another machine). Each lane gets its own port, and its URL appears as `viewer_url` in `status --json`. The director can also serve one page for every lane with `ratchet.py lanes --serve --scan <dir>`, which links into each lane's viewer through a single port.
- **Quota.** If the run-wide round cap blocks the lane, stop and report. The director decides whether to raise the cap, lower risk, or re-slice.

## Hard rules

- Never edit `state.json`, evidence, `active.json`, or `human_log.jsonl`. Never run approve commands without a real user message.
- Never skip a gate the plan turned on, and never mark something passed that `ratchet.py` didn't pass.
- Never weaken, delete, or `skip` a test to get green. Never tell a reviewer what verdict you want.
- One checkpoint at a time, in order. Parallelism happens *within* a step, never across checkpoints.
- If you truly must stop mid-build (tool outage, missing credentials), run `$RATCHET pause --reason "…"` and tell the user why.

## Recovering / resuming

Any time you're unsure where things stand, run `$RATCHET status` then `$RATCHET next`. A new session picks up from `state.json`, because nothing lives only in your context.
