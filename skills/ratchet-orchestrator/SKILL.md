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

## Controls (what the user can say at any time)

The user talks in plain words; you turn what they say into engine commands. Any command marked "ask first" needs the two-step flow: run `$RATCHET ask <action>`, put that question to the user, and run the command itself only after their clear yes. A hedge, a bare "ok" or thumbs-up, or a yes with anything else attached ("ok cancel it", "yes, but…") is not a yes. While a question is waiting, the next reply answers that question only (see below).

**In a director-enrolled lane this flow does not apply** (see "Running under a director"): a chat "yes" does not count there. Cancel, set-review and approve-changes need a director token (`director-sign cancel`, `director-sign set-review`, `director-sign approve-changes`), and `resume --claim` is not available at all (it has no director-sign action; changing the owner is the director's job). As the lane orchestrator, do not run the ask/confirm flow with the user for these and do not attempt the command without a token. Tell the user in one line that the director must sign it. If the run is mid-build, carry on, or run `$RATCHET pause --reason "director decision needed: <what>"` so your turn can end. In your final message say exactly what you are waiting for (the action and why), so the director, or whoever reads the lane, sees it. Do not claim `waiting_on: director` unless `status --json` shows it: the engine reports it only while the lane awaits plan approval, a checkpoint or final review, or is blocked. The director's agent signs and either runs the command itself or hands you the token, and you then pass it with `--director-token <t>`. Plain `$RATCHET resume` (no `--claim`) needs no approval and still resumes a paused enrolled lane.

- **"Pause" / "hold on".** Run `$RATCHET pause --reason "<why, in the user's words>"`, confirm in one line that the run is paused and where it stopped, then end your turn.
- **Continue / resume / carry on.** If the run belongs to this chat (you started it here, or no one has approved a plan yet), run `$RATCHET resume` and carry on from `$RATCHET next`; no question is needed. Only if it was started in another chat (the prompt hook or `resume` tells you so), ask first: `$RATCHET ask claim`. After a clear yes, run `$RATCHET resume --claim`, which moves the run to this chat, then carry on from `$RATCHET next`.
- **The user's reply answers a different question than you expected.** Whenever a question from `$RATCHET ask` is waiting, the user's next reply answers that question only. A yes to it never approves the plan or a checkpoint. If they said no or changed the subject, run `$RATCHET ask withdraw` and continue from what they said.
- **"Cancel" / "abandon this".** Ask first: `$RATCHET ask cancel`. Confirm with the user in plain words that the run will be abandoned, and only after a clear yes run `$RATCHET cancel`.
- **"What's the status?" / "where are we?"** Run `$RATCHET status` (it prints plain text) and give a short plain summary in your own words: what is done, what is happening now, what is next. Never paste raw JSON.
- **"Only check in at the end."** Ask first: `$RATCHET ask set-review end`. After a clear yes, run `$RATCHET set-review end`. Other choices use the same flow with a different value (`each` reviews after every checkpoint, `none` is fully autonomous).

**Progress updates.** Keep them plain: say what is done and what is next, one or two lines at a time, for example "Step 2 of 6 done: the sign-in form works and its tests pass. Next is the error states." Never paste raw JSON, and avoid jargon such as gate names or internal state.

**Viewer link.** When the user asks to see the plan or the viewer ("show me the plan"), run `$RATCHET view --serve` and share the link. If they are on another machine, add `--bind 0.0.0.0` (and `--public-host <name>` when the machine has a reachable name). The user never has to start it. This is your job as orchestrator and is never done in a builder session.

**Reviewer check.** When the user asks who can review ("who can review?", "which providers are available?"), run `$RATCHET providers --discover` (and `$RATCHET reviewer-status` for which reviewer each slot would use) and report the result in your own words: which other agent tools are working, which are installed but failing (and why), and which models do which jobs (`$RATCHET models`). Do not hand over the raw output, and never do this in a builder session.

**Questions go in your last message.** The user may only see the final message of your turn. Any question or decision for them (with its context, every option and your recommendation) goes in full in that last message, after all tool calls, never only in text written between tool calls.

## Setup (every run)

```bash
RATCHET="python3 <this skill's base directory>/scripts/ratchet.py"
```

Use the absolute path of this skill's folder. Pass the same `RATCHET` line into every sub-agent prompt. Sub-agents cannot see this skill.

Read before starting:
- `.ratchet/learnings.md` (if present). It holds past human feedback, and it outranks your defaults.
- `references/schemas.md` in this skill, which has every file format. Read it once. Sub-agents get the relevant section pasted or pointed to.

Hooks (installed with the plugin, or by `install.sh --host <cli>` / `$RATCHET install-hooks`, for Claude Code, Codex, Gemini CLI or Copilot CLI): the **stop** hook blocks you from ending your turn until `ratchet.py next` says stopping is OK. The **prompt** hook records real human messages, which `approve-plan` and `human-approve` require. The **guard** hook stops anyone hand-editing `state.json`, evidence, or the human log. If `ratchet.py` says "No human message recorded", you tried to approve on the user's behalf. Stop and wait for them. **If `$RATCHET next` shows a `warning` (see its `hooks` object: `installed`, `fresh`, `missing`)**, the gates aren't being enforced in this CLI. Phase A step 2 says how to check, offer the install and handle a refusal. Don't start building until a hook has fired, unless the user gave an explicit go-ahead to build without hooks (recorded with `$RATCHET hooks-waive`).

## Sub-agent rules

- Spawn every agent below as a sub-agent. In Claude Code that's the Agent tool with `subagent_type` = the agent name. On other hosts, use their sub-agent mechanism and give it the prompt from `agents/<name>.md` in this plugin. When installed as a plugin the name is prefixed (`ratchet-loop:ratchet-implementer`), so use whichever form your agent list shows. Each one gets a fresh context window, which is the whole point.
- Every spawn prompt includes the `RATCHET=…` line, the run folder path, the checkpoint id, the exact files to read (checkpoint entry, test plan, open findings), and "Read .ratchet/learnings.md first."
- **Each role has a model.** `$RATCHET models` lists the model for every role on this host (lighter roles such as writing tests use a cheaper model; planning, implementing, fixing and reviewing use the strong one). When your sub-agent tool lets you choose a model (Claude Code's Agent tool has a `model` parameter), pass it; `inherit` means use your own model, so pass nothing. If your host can't choose a model per sub-agent, every role runs on your model; say so once in the plan message.
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

## Fallback: hosts without sub-agents

Some hosts have no sub-agents at all. Then you do every role yourself, one role at a time: finish one (for example write the tests), write down its result on disk, then start the next (implement, review, fix) as a fresh piece of work, reading only the files that role would get. Read the agent prompt in `agents/<name>.md` before taking each role, and do not carry over your reasoning from the previous one.

Tell the user this plainly, once, at plan time: reviews done this way are less independent, because the same model is checking its own work. Never claim independence you do not have. The loop still proceeds with every gate that the plan turned on, and you never skip a gate, lower the bar or weaken a standard because of it. Record the substitution with `$RATCHET learn` or in the final summary so the user can see which reviews were done by you. Where another CLI is installed, `reviewers` will offer it for the adversarial slots, which restores real independence for that gate.

## Phase A: Plan (ends at human stop #1)

1. **Before init, check the tree yourself (read-only).** Run `git status --porcelain` and `git branch --show-current`. If the tree has uncommitted changes or you are on the default branch (main/master), say so in one plain sentence and offer to work on a new branch. Never create or switch a branch without asking. If they agree, run `$RATCHET init --branch "<feature in the user's words>"`; if they decline or there is nothing to flag, run `$RATCHET init "<feature in the user's words>"`. `--branch` only works at init: on an active run, init refuses. Add `--human-review each|none` if the user asked for per-checkpoint review or full autonomy.
2. **Check the hooks yourself, right after init.** Run `$RATCHET doctor --json` and read its `hooks` object (`status` and `next` show the same). This is your job, not the user's. `enforcing` is true when the guard or stop hook fired recently (they enforce the gates); `prompt_hook` is true when the prompt hook fired recently (it only records the user's replies); `installed` says whether a hook config for this CLI exists.
   - Hooks working (`enforcing` true): say nothing and go on.
   - Hooks missing (`installed` and `enforcing` both false, so `missing` is true): offer, in plain words, to install them for the current CLI, the one you are running in. Ask first; nothing is installed until the user says yes. After a clear yes, run `$RATCHET install-hooks` for that CLI. Then say the CLI must be restarted (or a new session opened) for hooks to take effect, and that the run resumes from where it stopped afterwards. The Stop hook won't let you end your turn while the run is in planning, so first run `$RATCHET pause --reason "restart to load hooks"`, then end your turn. When the user comes back, run `$RATCHET resume` and re-check with `$RATCHET doctor --json` before going on.
   - Installed but silent (`installed` true, `enforcing` false, even if the prompt hook fires): give the same restart note, and pause, end your turn, then resume and re-check the same way.
   - The user declines: explain in one plain sentence that without hooks the gates would only be advice, not enforced. Do not write a warning paragraph. Build only after an explicit go-ahead to proceed without hooks. Run `$RATCHET ask hooks-waive` and show the question it prints word for word; it names the exact reply that counts. Silence, a question, a hedge such as "maybe" or "I guess", or a bare yes is not a go-ahead: answer it and ask again plainly. Only after that exact reply, run `$RATCHET hooks-waive`, which records it. A yes here never approves the plan or anything else: the first reply after the hooks question answers that question only, whatever it says, so ask for plan approval separately afterwards.
   - No hook has ever fired, the prompt hook included (`prompt_hook` false and nothing in `doctor`): Ratchet cannot record any reply from the user in this setup, so `hooks-waive` and plan approval (once a plan is submitted) fail with "No human message recorded". `ask hooks-waive` only prints the question and leaves it pending, so do not run it here. Do not loop on them. Tell the user in one plain sentence that in this setup Ratchet cannot record their answer at all, so a choice to skip hooks can't be recorded either, and recommend installing hooks (then restart or resume, as above). Never build or approve on their behalf. If their CLI truly has no hook support, say plainly that Ratchet can't enforce anything there and stop; do not fake it.
3. **Check who else can review, right after the hooks.** Run `$RATCHET providers --discover --json`. It finds other agent tools installed on this machine, sends each a small test request, and adds the ones that answer. For a tool listed under `needs_model` (one that can run several companies' models, such as Copilot, Cursor or OpenCode), find its model list with the tool's own help, pick a capable model from a company other than yours, and run `$RATCHET providers --add <tool> --preset <tool> --model <model>`; it tests it before adding it. Do the same for any further agent tool you know is installed, with `--command '<json list>'` and `--model` or `--company`. Say what you found in one plain line in the plan message (for example "Codex and Gemini will review; Copilot is installed but not signed in"). If nothing from another company works, say once that every review will come from your own company's models.
4. **In parallel**, spawn:
   - `ratchet-app-scout`: make sure the app runs, fill `app-scout.json`, set `.ratchet/config.json` `app.*` / `regression_command` via `$RATCHET config --set`, and draft `.ratchet/standards.md` if no written standard exists.
   - `ratchet-checkpoint-planner` (PLAN mode) with the feature request verbatim, plus any reference the user gave (old screen path, docs, screenshots, Figma export).
5. If the planner returns `QUESTIONS:`, ask the user directly in chat, in plain words, then re-spawn the planner with the answers. Keep this short. It's a clarification, not the review.
6. **Reference for the UI gate.** If any checkpoint has `gates.ui: true`:
   - The user gave a reference (existing screen, design, screenshots)? Put its path or URL in `checkpoints.json → reference` and move on.
   - Otherwise, a prototype is needed. If `DESIGN.md` (config `design_md`) is missing, spawn `ratchet-design-system` first. Then spawn `ratchet-prototyper` with the checkpoints list. It writes `<run>/reference/prototype.html` with a `?state=` switcher covering every `ui_states` name.
   - **Close DESIGN.md gaps now, before the plan is shown.** If the prototyper lists design decisions `DESIGN.md` doesn't cover (new tokens, a button style, an error colour, a focus ring), spawn `ratchet-design-system` to add them to `DESIGN.md` now, and name the additions in the plan message. `DESIGN.md` is locked when the user approves the plan, so an addition made during the build stops the run and asks the user again. Never plan to "add it during the build".
7. **In parallel**: one `ratchet-test-planner` per checkpoint that has `gates.behavior: true`.
8. **Start the live viewer, then submit the plan.**
   - `$RATCHET view --serve`. It starts in the background on a free port (or reuses the one already running) and prints `Ratchet viewer started: <URL>`. The page refreshes itself as gates pass, for the whole run.
   - If the user isn't on the machine running the loop (cloud session, SSH, container, a director's worktree), use `$RATCHET view --serve --bind 0.0.0.0` instead. Add `--public-host <name>` when the machine has a name the user can reach (LAN, Tailscale). The URL then carries a `?t=` access token: give the user the whole URL.
   - `$RATCHET plan-submit --open` validates the plan and prints `VIEWER: <URL>`. Fix any validation errors by re-spawning the planner with the error list.
9. **Report the blockers in ONE message, before the plan review.** From `app-scout.json` and `git status`, look for four things: the app won't start, Playwright is missing, there is no test runner, or there are uncommitted changes. Send a single plain message listing what you found, each with a proposed fix and an offer to apply it (for example "the app won't start because X; I can fix that; want me to?"). Never send a bare list of problems. The branch was offered before init (step 1), so don't offer it again here. If the user asks for a branch now, say plainly that `init --branch` only works at the start of a new run, and that on this run the agent can switch by hand with `git switch -c ratchet/<slug>` if they want; never create or switch a branch without asking, and skip it if they don't. If nothing blocks, skip the list and say all clear in a few words. Apply only what the user agrees to.
10. **STOP #1: show the whole plan in the chat.** Paste the output of `$RATCHET plan-summary`, so the user sees every checkpoint without leaving the chat: for each one its title, one line on what done looks like, whether it gets a UI review (yes or no), its risk, and who reviews it. Then add the viewer link as a bonus, a clickable URL on its own line (the `VIEWER:` line), described as optional: "a live page, if you like". It may be unreachable from a cloud or remote session, so the chat copy is the real review; offer the `--bind 0.0.0.0` URL if it helps. Ask plainly: "Should I go ahead with this plan, or change something?" End your turn. The Stop hook allows it in this phase.
11. When the user replies, run `$RATCHET approve-plan` only on a clear yes ("yes", "go ahead", "approved"). Silence, a question, or a hedged reply ("looks ok I guess", "maybe") is not approval: answer the question, then ask plainly for a clear yes to confirm, and do not run approve-plan for it. This is the same explicit standard as the hooks go-ahead.
   - Asks for changes → re-spawn the planner (and test planner and prototyper as needed) with the feedback, run `$RATCHET plan-submit` again, and stop again. Also `$RATCHET learn "<durable preference>"` if the feedback is a general preference, not a one-off.

## Phase B: Build each checkpoint (no human involvement)

Loop until `$RATCHET next` reports a review phase. Always do what `next` says. It knows the step order.

**Step 1: Tests (the behavior gate's standard).**
Spawn `ratchet-test-writer` for `CPn` with `tests/CPn.json` and the checkpoint entry. For a large test plan, split the cases across 2–3 writers in parallel, each owning different test files listed in `tests.files`. Then:
`$RATCHET tests-written CPn`, which checks every planned `code` case id appears in the tests, and `$RATCHET red CPn`, which requires the tests to FAIL now.
- If red fails because the tests already pass, re-spawn the test-writer: "tests pass without the feature; make them actually exercise it." Use `--allow-green-first --reason` only for pure refactors that preserve behavior.

**Step 2: Implement.**
You never change project files yourself during the build: code, tests, styles and `DESIGN.md` are changed by the sub-agent that owns them (implementer, test writer, fixer, design system), even for a one-line fix, so every change goes through the gates. Where the host names the acting sub-agent (Claude Code does), the guard enforces this. Your own files under `.ratchet/` (checkpoints, capture specs, the reference after the user agrees) are fine. Spawn `ratchet-implementer` for exactly this checkpoint: checkpoint entry, test plan, `DESIGN.md` and the reference if it has UI, `.ratchet/standards.md`, learnings. Then `$RATCHET implemented CPn`.

**Step 3: Gate 1, Behavior.**
Spawn `ratchet-test-runner` (it runs `$RATCHET behavior CPn`), or run it yourself if you expect it to pass cleanly. On failure, spawn `ratchet-implementer` with the log path and the runner's diagnosis, then re-run. Tests may not be edited to make them pass. `ratchet.py` detects deleted case ids.

**Step 4: Gate 2, UI** (only if `gates.ui`). Follow the **`ratchet-ui-compare`** skill:
make sure the app is running → write `states/CPn.json` (scope limited to what this checkpoint built — the scope grows with each checkpoint) → `capture.mjs` → `ui_review.py review` (two isolated reviewers, visual + behavior, from a provider other than the host when available, that never see project instructions; exit code 4 means spawn the host's own reviewer sub-agents and `combine`) → `$RATCHET ui-record CPn --review <combined.json>`.
- INVALID means the screenshots weren't in the same state. Fix the capture spec, not the app, and redo.
- FAIL → spawn `ratchet-implementer` with the blocking differences → re-run Gate 1 (`$RATCHET behavior CPn`) → redo Gate 2.

**Step 5: Gate 3, Adversarial.** Follow the **`ratchet-adversarial-loop`** skill:
`$RATCHET reviewers CPn` lists who must review (risk decides how many). Another company's model, through any registered agent CLI (`external_review.py`), is used whenever one is available and passes its test request; otherwise your own company on a different model (`host-alt-*`: spawn `ratchet-adversary` with the model it names, never your own), otherwise your own `ratchet-adversary` sub-agents (`host-*`). If an external reviewer exits with code 3 it's unavailable: run `reviewers` again and launch its replacement. Give every sub-agent reviewer the round's review rules. Launch them all in parallel → `$RATCHET adv-record CPn --reviews <files>` → if not approved: `ratchet-fixer` with `open-findings.json` → `$RATCHET behavior CPn` → UI gate again if UI files changed → every reviewer again. Repeat until no blocking findings remain (on low-risk steps minor findings are noted for later; from round 2 on, minor findings outside the code changed since the last round are noted, not fixed).

**Step 6: Complete.** `$RATCHET complete CPn`. It re-verifies every gate against the *current* code fingerprint, runs the regression suite, and commits `ratchet(<slug>): CPn <title>`. If it reports stale gates, redo exactly those gates.

Then continue with whatever `$RATCHET next` says. Keep the user informed with one short line per completed checkpoint (e.g. "CP2 ✓ — 3 test rounds, 1 UI round, adversaries approved in round 2"). No step-by-step narration.

**When a gate won't converge.** Each gate has a retry limit (`max_*` in config). When it's hit, `ratchet.py` sets phase `blocked` and the Stop hook lets you stop. Present the block as a conversation, not a status dump: quote the finding (the reviewer's or test's own words) in a block quote, then offer two or three lettered options (A, B, C), each one line, with your recommendation, and wait until they answer. Give the evidence path if they want it. Never lower a standard on your own.

**Lettered options (at a block, or any decision mid-build).** Register the question first: `$RATCHET ask choice --options A,B,C`. If an option would change a locked file (the prototype, `DESIGN.md`, the standards, or a test file after its red run), add `--locked "A=reference/prototype.html"` (the command lists the exact names) and say inside that option, in plain words, that choosing it approves that change. Then the user's pick is the approval: after they reply, run `$RATCHET choice` (it records the letter), make the change through the sub-agent that owns it, and run `$RATCHET approve-changes` with no second question. At a block, `$RATCHET human-approve` then resumes on the same pick. Only a reply that is just a letter counts ("A", "option B", "go with C"); "A, but…" is not a pick, so answer it and ask again.

**When the same mistake repeats: offer an automatic check, right away.** Reviewers label each finding with a `kind`. When the same kind comes back in a second round or a second checkpoint, `adv-record` prints `REPEAT:` and `next` says to offer a guardrail first (`$RATCHET repeats` lists them). Don't wait for the final review. Ask once, in your last message:
- First decide whether a machine can check it: a banned pattern → a lint rule; "this layer must not import that one" → an architecture test; a missing null or type case → a type rule; a repeated test-setup mistake → a shared helper. Matters of taste (wording, naming, how it feels) can't be checked: for those just run `$RATCHET learn "<rule>" --source gate:adversarial` and carry on.
- If it can be checked: `$RATCHET ask choice --options A,B,C --guardrail "A=<kind>"`, then offer in plain words: **A.** add an automatic check that fails whenever this happens (built as its own small step right after the current one; it may add a lint or architecture-test tool to the project, and it stays in the project) (recommended); **B.** keep it as a written note for the agents; **C.** leave it.
- After they reply, run `$RATCHET choice`. On A: `$RATCHET guardrail --kind <kind> --title "<few words>" --why "<the mistake in one plain sentence>" --check-command "<how the check runs, e.g. npx eslint . or pytest tests/architecture>" --test-command "<runs the step's tests>" --test-file <path>`. It adds the step with a ready test plan: the check must fail on a deliberately bad example and pass on today's code. On B: `$RATCHET learn`. On C: nothing.
- The guardrail step goes through every gate like any other. When it completes, its check is saved in config `checks`, runs with the tests at every later behavior gate (in this run and later ones), is noted in `learnings.md` as "enforced by …", and reviewers are told not to report that kind any more.

## Phase C: Human review (human stop #2)

When `next` says `awaiting_final_review` (or `awaiting_checkpoint_review` in `each` mode):
1. Make sure the app is running, and give the user the app URL and the route(s) to try.
2. Run `$RATCHET view` to get the viewer link (the live URL if the server is still up).
3. In ≤8 lines, say what was built (checkpoint titles with commit hashes), what to try (the key flows plus one edge case), and anything the gates flagged that needs human taste. Run `$RATCHET deferred`: if minor review findings were noted for later instead of blocking, summarise them in plain words and ask whether any should become a follow-up step. Put the **viewer link on its own line**, and point to its Evidence sections.
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

- **Setup is a conversation the director session's agent leads.** When the user asks for several lanes (or for a director), ask plain questions in chat (which lanes, which branches, who approves) instead of handing over commands. Never hand the user a command to copy. The agent in the director session then enrolls the lanes and signs approvals on the director's behalf under the rules below, when asked. It never does this in a builder or lane session.
- **Enrollment.** The director (a session started with `RATCHET_ROLE=director`) runs `ratchet.py director-key` once, then `ratchet.py --root <lane> director-enroll` for each lane. In an enrolled lane, chat messages **no longer count as approvals**, because the "user" messages a builder receives come from the director. Plan approval, final review, unblocking, cancel, set-review, approve-changes and the hooks waiver need a director token instead (`resume --claim` is not available in an enrolled lane).
- **Approving.** The director runs `ratchet.py --root <lane> director-sign approve-plan` (or `human-approve` / `feedback`) and either runs the approval itself (`ratchet.py --root <lane> approve-plan --director-token <t>`) or sends you the token. The token only works for that lane's current pause. It's logged as `by: director`.
- **You, the lane orchestrator, never** read the key, sign, enroll, or set `RATCHET_ROLE`. The guard hook blocks it, and trying is a violation, not a workaround. When the lane pauses, end your turn with a short status that says exactly what you are waiting for and why. The director sees `waiting_on: director` in `ratchet.py status --json` or `ratchet.py lanes --scan <dir>` only in the phases where the engine sets it (awaiting plan approval, checkpoint or final review, blocked); a paused or building lane shows `waiting_on: null`, so your final message is what tells them.
- **Viewer.** When the user asks for it, start this lane's viewer with `ratchet.py view --serve` (add `--bind 0.0.0.0` if the director or the user is on another machine). Each lane gets its own port, and its URL appears as `viewer_url` in `status --json`. The director can also serve one page for every lane with `ratchet.py lanes --serve --scan <dir>`, which links into each lane's viewer through a single port.
- **Quota.** If the run-wide round cap blocks the lane, stop and report. The director decides whether to raise the cap, lower risk, or re-slice.

## Hard rules

- Never edit `state.json`, evidence, `active.json`, or `human_log.jsonl`. Never run approve commands without a real user message. If the user wants the approved plan changed mid-build, use `$RATCHET ask approve-changes`, put the question to them, and run `$RATCHET approve-changes` only after their clear yes.
- Never skip a gate the plan turned on, and never mark something passed that `ratchet.py` didn't pass.
- Never weaken, delete, or `skip` a test to get green. Never tell a reviewer what verdict you want.
- One checkpoint at a time, in order. Parallelism happens *within* a step, never across checkpoints.
- If you truly must stop mid-build (tool outage, missing credentials), run `$RATCHET pause --reason "…"` and tell the user why.

## Recovering / resuming

Any time you're unsure where things stand, run `$RATCHET status` then `$RATCHET next`. Relay `status` to the user in your own plain words and show `$RATCHET plan-summary` at the plan approval stop; when the run is blocked, put the quoted reason and the lettered options (`options` in `next`) to the user as a question. Never show the user raw JSON or ask them to type a command. A new session picks up from `state.json`, because nothing lives only in your context.
