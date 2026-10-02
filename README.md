# Ratchet Loop

**Ratchet Loop** is a checkpoint-and-gate build workflow for AI coding agents, and it works with any model provider. Like a ratchet, it only turns one way: every checkpoint that clears its gates is locked in, and nothing slips back. You ask one skill, the **orchestrator**, to build a feature. It splits the work into small **checkpoints**, and no checkpoint moves forward until it passes four **gates**.

You use it by talking to your agent. You never need to type a command.

## Get started

Works in Claude Code, Codex, Gemini CLI and GitHub Copilot CLI. Ratchet Loop is this repo, so first get it onto your machine: clone it or download it, and note its web address or the folder you put it in. Then open your agent in your project and say, with your own address or folder path in place of the part in angle brackets:

> Please install Ratchet Loop from <this repo's address, or the path of my local clone> and set it up for this CLI, then start it on this feature: managers can approve or reject their reports' leave requests with a reason.

You give the agent the repo address or the local clone path, and the agent runs the installer from it. It works out which CLI it is running in, asks before it changes anything, installs Ratchet's hooks for that CLI, and tells you when to restart it (hooks load when the CLI starts). After the restart, say "Set up Ratchet and continue", and the agent picks up where it left off.

## Things you can say

Each of these is a plain sentence to your agent. It turns them into the right commands itself.

**Get the viewer link**

> Give me the viewer link, and open it if you can.

**See who reviews and which providers are available**

> Which reviewers and providers are available on this machine?

**Pause, resume, cancel, status**

> Pause the build, and resume it when I say so.

> Where are we?

> Cancel this run. (The agent asks you to confirm before it abandons anything.)

**Only check in at the end**

> Only check in with me at the end, not after every checkpoint.

**Install and verify hooks**

> Check that Ratchet's hooks are installed for this CLI, and install them if they are not.

**Set up a director and lanes**

> Set up a director for these three lanes, and ask me what you need to know.

**Build without hooks**

Without hooks the gates are only advice, so the agent explains that and asks first. It builds without them only after this exact reply, given in answer to its question:

> yes, go ahead without hooks

A bare "yes", "maybe" or "I guess" does not count, and this reply never approves the plan. Ratchet records your reply through its prompt hook, so this choice is only possible when that hook runs. If no Ratchet hook runs at all in your CLI, Ratchet cannot record any answer from you, not even this one, and the agent will recommend installing the hooks instead.

## What to expect in chat

- **The whole plan, in the chat.** Before anything is built you see every checkpoint: its title, what done looks like, whether it gets a UI review, its risk and who reviews it. A viewer link is offered as an optional extra.
- **Plain progress.** Short updates about what is done and what is next, with no raw JSON and no jargon.
- **Blocks are conversations.** When a gate cannot converge, the agent quotes the finding and offers two or three lettered options (A, B, C) with a recommendation. Reply with just the letter. If an option changes something you approved (the prototype, say), it says so, and picking it is your approval: you won't be asked a second time.
- **Only a clear yes approves.** "Yes", "go ahead" or "approved" counts. Silence, a question, a hedge, a bare "ok" or thumbs-up, or a yes with something else attached ("yes, but change the header", "ok cancel it") does not, and no message from another agent can stand in for you. A yes answers only the question the agent just asked: saying yes to "only check in at the end?" never approves the plan.

You are needed twice: once to approve the plan, and once to review the finished feature. Anything you ask to change becomes new checkpoints that go through every gate again, and the lesson is written to `.ratchet/learnings.md`, which every agent reads.

> An attempt is allowed to be wrong. It is not allowed to ship until it isn't.

## The four gates

| # | Gate | What proves it | Enforced by |
|---|---|---|---|
| 1 | **Behavior** | Planned tests, written *before* the code, must first fail and then pass | The engine runs the tests itself and checks every planned case ID is still there |
| 2 | **UI** | App matches the prototype/reference in the *same state*, visually and interactively | Two isolated reviewers (visual + behavior), from a provider other than the host when available, list every difference; any code-fixable one blocks |
| 3 | **Adversarial** | Reviewers from another company's model whenever one is available (otherwise the host's company on a different model, then the host itself) assume the code is wrong; a fixer fixes what blocks | Loop runs until no required reviewer has a blocking finding on the *current* code. Risk decides how many reviewers look; on low-risk steps only blocker and major findings block, and from round 2 on minor findings outside the changed code are noted for later |
| 4 | **You** | You use the feature | Approval only counts if a real user message arrived after the loop paused |

Modes: by default you check in twice (plan and final review). You can ask to review after every checkpoint, or to go fully autonomous after plan approval; the gates stay just as strict. Logic-only work (API, refactor, migration) skips the UI gate, and for screen migrations the reference is the old screen instead of a prototype.

## Works with any provider

**The host** is whichever model runs the loop: Claude, GPT/Codex, Gemini, a Copilot model, or anything else. Ratchet detects the common agent CLIs from their environment, or you set `host_provider` in `.ratchet/config.json`. Nothing in the loop is tied to one provider. What changes with the host is who counts as a *different* provider.

**Reviewers from a different company first, at every risk level.** Every checkpoint gets a `risk` (`low` / `normal` / `high`) in the plan, which you see at approval. Risk decides **how many** reviewers look (low 1, normal 2, high 4) and, on low-risk steps, that only blocker and major findings block. It never changes **who** is asked first. Each review slot is filled, in order, by:

1. **another company's model**, through any agent CLI on your machine that works (Codex, Gemini, Claude, Copilot, Cursor, OpenCode, or any command you add);
2. **the same company on a different model** (for example Sonnet reviewing work done by Opus);
3. **the same model** that did the work, only when nothing else is available.

At the start of a run the agent checks which agent tools are installed, sends each a small test request, and uses the ones that answer, choosing a capable model from another company for tools that can run several. It tests a tool again before relying on it, so one that is signed out or out of quota is skipped at once (for 6 hours) and the next choice takes its slot. When nothing else works, the host's own sub-agents fill the slot so the loop never stalls; for high-risk steps the number of distinct reviewers can then be smaller than four, and substitutions are logged. The plan, the live viewer and the agent's updates say who reviewed each round, and you're told up front when every review will come from the same company as the build. Ask your agent "which providers are available?" and it will tell you.

**Each role has a model.** Planning, writing code, fixing and reviewing use the strong model; lighter jobs such as planning and writing tests use a cheaper one, and running tests the cheapest. The plan lists which model does what; `role_models` and `host_models` in `.ratchet/config.json` change it. Where the agent CLI can't choose a model per sub-agent, every role runs on the session's model.

**Minor findings don't loop forever.** From round 2 on, reviewers focus on the code the fixer changed: a minor finding elsewhere is noted, not fixed, while blocker and major ones still block anywhere. Everything noted for later is shown at your final review, where you decide whether any of it becomes a follow-up step.

The UI gate works the same way: the `ui_reviewers` in config are chosen per role from the providers that are available, skipping the host, with the host's own reviewer sub-agents as the fallback.

**Hooks on every host.** All four CLIs call the same entry point, which works out the host from the payload's shape and answers in that host's dialect. On internal errors the hooks **fail open** and log to `.ratchet/hook-errors.log`, because Copilot denies every tool call when a hook crashes. Sub-agent prompts in `agents/` are in Claude Code's format; on other hosts the orchestrator reads the same prompts and hands them to that CLI's sub-agents.

**Skills on non-Claude CLIs.** Claude Code finds the skills in its own folder. For the non-Claude hosts (Codex, Gemini CLI and Copilot CLI) the installer puts one shared copy in `~/.ratchet-loop/skills` (or `$RATCHET_HOME/skills` if you set that). Make those skills visible through the CLI's own skills or instructions mechanism, for example by symlinking the six skill folders into its skills folder, or by having the agent read the skill files straight from that folder. Your agent can do this for you: ask it to "link the Ratchet skills into this CLI's skills folder".

**Requirements:** Python 3.8+, git (for fingerprints and per-checkpoint commits), and Node 18+ plus Playwright for the UI gate. Optional, for reviewers from other providers: any of the `claude`, `codex`, `gemini` or `copilot` CLIs. With none installed, the host's own sub-agents do every review.

## What makes the gates real (not just prompts)

- **Evidence, not claims.** Agents don't report "tests pass"; the engine runs them. Reviewers don't report "PASS"; the engine recomputes the verdict from their findings.
- **Red first.** Tests that pass before the feature exists are rejected as vacuous.
- **No deleting tests.** Every planned case ID must still be in the test files at each behavior run.
- **Code fingerprints.** Every gate pass is stamped with a hash of the working tree. Change the code afterwards and the pass goes stale: behavior always re-runs, UI re-runs if UI files changed, and the adversaries re-review.
- **INVALID comparisons.** Reviewers reject screenshot pairs that aren't in the same state, so the capture is fixed rather than the app "fixed" to match a different state.
- **Isolated UI reviewers.** UI reviewers run in the provider chosen from config `ui_reviewers` (any supported CLI other than the host, or the host's own sub-agents as the fallback), in a temp folder with only screenshots, accessibility trees and the design file (config `design_md`). They never see the project's agent instructions, the code or the chat.
- **Protected state.** A guard hook blocks hand-edits to the run state, evidence, the human log and a lane's director enrollment, including edits made by a script fed through a heredoc. Review files belong to their reviewers: in Claude Code, which names the acting sub-agent on every tool call, only the adversary may write an adversarial review and only the visual or behaviour reviewer its UI review, and the combined UI review and capture manifest are written by the scripts alone. During the build the orchestrator can't change project files itself either: code, tests and styles are changed by the sub-agent that owns the role. After a UI round finds differences, what the UI check compares (the capture steps and scope) is frozen until it passes, so a difference can't be made to disappear by no longer looking at it; changing it needs the user's yes. It also blocks nested agent sessions and anything that could impersonate a human, and any access to the director key from a non-director session.
- **Stop hook.** It feeds the next required action back to the agent and allows stopping only at the two review points, when blocked, or when paused.
- **Retry limits go to you.** When a gate won't converge, the loop blocks and asks you instead of lowering the bar.

## Reference (commands the agent runs)

Everything above is done by talking to your agent. This section is for people typing at a terminal, and it lists what the agent runs on your behalf. `ratchet.py` lives in `skills/ratchet-orchestrator/scripts/`.

### What's inside

```
ratchet-loop/
├── .claude-plugin/plugin.json       Claude Code manifest
├── .codex-plugin/plugin.json        Codex manifest (same name and version)
├── hooks/hooks.json                 Stop, UserPromptSubmit, PreToolUse
├── install.sh                       installer for every supported CLI
├── skills/
│   ├── ratchet-orchestrator/          the one skill you prompt
│   │   ├── SKILL.md
│   │   ├── scripts/ratchet.py         state engine: checkpoints, gates, fingerprints, evidence, viewer, hooks
│   │   ├── assets/viewer.html         checkpoint viewer (plan review + live progress)
│   │   └── references/schemas.md      every file format
│   ├── ratchet-tdd-planner/           plan tests before code
│   ├── ratchet-design-md/             create/extract DESIGN.md
│   ├── ratchet-prototype/             single-file clickable HTML prototype
│   ├── ratchet-ui-compare/            Gate 2: screenshots + isolated reviewers
│   └── ratchet-adversarial-loop/      Gate 3: adversary and fixer loop
├── agents/                          reviewer and worker prompts
└── tests/                           standalone test scripts
```

### Installing by hand

```
./install.sh [--host claude|codex|gemini|copilot|all] [--project]
```

| Form | What it does |
|---|---|
| `./install.sh` (same as `--host auto`) | installs for the agent CLI running the script, detected from its environment; Claude Code if it can't tell |
| `--host claude` | Claude copy in `~/.claude` (skills and agents) plus Claude hooks |
| `--host codex` / `gemini` / `copilot` | shared copy in `~/.ratchet-loop` (or `$RATCHET_HOME`) plus that CLI's hooks; the skills are also linked into `~/.codex/skills`, `~/.gemini/skills` or `~/.copilot/skills` when that folder's CLI is set up |
| `--host all` | the Claude copy, the shared copy, and hooks for all four CLIs |
| `--project` | Claude's copy and every CLI's hook config go into the current repo (`.claude/`, `.codex/`, `.gemini/`, `.github/hooks/`) instead of your home folder. The shared skills copy for Codex, Gemini and Copilot still goes to `~/.ratchet-loop` (or `$RATCHET_HOME`), and the project hook files point at it |

It is safe to run twice: skill folders are replaced, hooks are not duplicated, and your own hooks and settings are left alone. Claude Code and Codex can also load the repo as a plugin through their manifests (`.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`). Hooks only: `ratchet.py install-hooks --host <cli> [--scope project|user] [--dry-run] [--uninstall]`.

### Commands

| Command | Purpose |
|---|---|
| `ratchet.py doctor [--json]` | which CLIs have Ratchet hooks and when each last fired |
| `ratchet.py status [--json]`, `plan-summary` | plain-text state; the plan step by step |
| `ratchet.py view` | the viewer link (live URL if a server runs, else a `file://` link) |
| `ratchet.py view --serve [--open]` | live page on a free port (4477 and up) at `http://127.0.0.1:<port>/` |
| `ratchet.py view --serve --bind 0.0.0.0 [--public-host my-box.local]` | share `http://<host>:<port>/?t=<token>` (requests without the token get 403) |
| `ratchet.py view --stop` | stop the viewer server |
| `ratchet.py pause --reason "..."`, `resume [--claim]`, `cancel` | pause, resume, cancel (resume-claim and cancel are asked about first) |
| `ratchet.py set-review end\|each\|none` | how often you check in |
| `ratchet.py reviewer-status [--reset <id>]` | the host, who is available, and retrying one early |
| `ratchet.py providers [--discover] [--check [id]] [--add <id> --preset <p> --model <m>] [--remove <id>]` | agent tools that can review: find, test, add or remove them |
| `ratchet.py models` | which model each role's sub-agent uses on this host |
| `ratchet.py deferred` | minor review findings noted for later instead of blocking |
| `ratchet.py lanes --scan <dir> [--json]` | each lane's phase, progress and whether it waits on a director |
| `ratchet.py lanes --serve --scan <dir> [--bind 0.0.0.0]` | one dashboard for every lane |
| `ratchet.py config --set key=value` | settings in `.ratchet/config.json` (reviewer roster, retry caps, `regression_command`, `ui_globs`, app URL) |

The viewer server only serves the viewer, its live data and the prototype, never the rest of `.ratchet/`. Its URL is saved in `.ratchet/viewer.json`.

Director-signed approvals: the director's session runs in the director role (see the orchestrator's SKILL.md), creates a key once with `ratchet.py director-key`, then enrolls each lane with `ratchet.py --root <lane> director-enroll`. In an enrolled lane chat messages stop counting as approvals; approvals need `ratchet.py --root <lane> director-sign <action>` and `--director-token <t>`. Builder sessions cannot read the key or sign.

### Running the tests

Each test file is a standalone script; none needs a framework.

```
python3 tests/test_docs.py
python3 tests/test_gates.py
python3 tests/test_hooks.py
python3 tests/test_hooks_status.py
python3 tests/test_hooks_waiver.py
python3 tests/test_install.py
python3 tests/test_providers.py
python3 tests/test_status_plain.py
python3 tests/test_viewer.py
```
