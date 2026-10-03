# Ratchet Loop

AI coding agents are fast, but they'll tell you something works when it doesn't. Ratchet Loop makes them prove it.

You describe a feature. Your agent splits it into small **checkpoints** and builds them one at a time. Each checkpoint has to get through four **gates** before the next one starts: its tests pass, it looks like the design, a second AI (from another company if you have one installed) tries to break it and can't, and you sign off at the end. If a gate fails, the agent fixes the problem and tries again. It can't argue its way past.

You don't type any commands. You talk to your agent like normal, and it runs Ratchet for you.

## Step 1: Install Ratchet (once)

Ratchet works in Claude Code, Codex, Gemini CLI and GitHub Copilot CLI. You install it once per computer, not once per project. After that it's available in every project you open.

**1. Download this repo.** Clone it or download the zip. Remember where you put it (or copy its web address).

**2. Ask your agent to install it.** Open your agent (in any folder) and paste this, with your own address or folder path in the angle brackets:

> Please install Ratchet Loop from <this repo's address, or the path of my local clone> and set it up for this CLI.

The agent will ask before it changes anything. Part of the setup is adding Ratchet's hooks to your CLI. Hooks are what stop the agent from skipping a gate, so they matter.

**3. Restart your CLI.** Hooks only load when the CLI starts, so the agent will ask you to close it and open it again. Do that, then say:

> Set up Ratchet and continue

The agent checks everything works and tells you it's ready.

The hooks do nothing in projects where you're not using Ratchet, so they won't get in the way of your other work. If you use more than one of these CLIs, ask each one to install Ratchet for itself.

Prefer to keep Ratchet's setup inside a single project instead? Ask your agent to "install Ratchet Loop for this project only".

You'll need your project in a git repository, and Python 3, which most computers already have. If you're building screens or pages, Ratchet also uses Node and Playwright to take screenshots. The agent will offer to set those up if they're missing.

## Step 2: Build something

Any time you want something built, open your agent in your project folder and say:

> Use Ratchet to build this feature: <describe it in a sentence or two>.

A real one looks like this:

> Use Ratchet to build this feature: managers can approve or reject their team's leave requests, with a reason.

That's all you have to do. The agent takes it from there and asks you when it needs you.

## What happens next

First the agent does some quick checks on its own. Are the hooks working? Which other AI tools are installed that could review the work? Has your team already written a spec for this? If anything needs your OK, like starting a new git branch, it asks.

Then it shows you the plan, right in the chat. You'll see each step, what "done" means for it, whether its look will be checked, how risky it is and who will review it. Read it. Say "yes" to start, or tell it what to change. If the feature has new screens, you also get a clickable prototype to look at first, so you can see what it'll look like before anything is built.

Now you can walk away. For each step the agent writes the tests first and makes sure they fail. Then it writes the code until they pass. Then the reviewers go looking for problems, and anything they find gets fixed and checked again. Every finished step becomes its own git commit, and you get a one-line update as each one lands.

If a step gets stuck, the agent comes back to you. It quotes the problem and gives you two or three options, labelled A, B and C, with the one it recommends. Just reply with the letter.

When everything is built, it tells you what to try. Try it. Say "approved" if you're happy. If you're not, say what's wrong in your own words. Your changes become new steps, and they go through the same gates.

So you're needed twice: once to OK the plan and once at the end. If you'd rather check every step, or not be asked at all after the plan, just say so.

## More things to ask for

Some other requests that work well:

> Use Ratchet to build this feature: customers can save items to a wishlist and get an email when one goes on sale.

> Use Ratchet to rebuild the old settings screen in the new design. The old screen is at src/legacy/Settings.tsx.

> Use Ratchet to refactor the billing module so it no longer talks to the database directly. Nothing the user sees should change.

Keep each request to one feature. Something you can explain in a few sentences usually turns into three to eight steps. If it's bigger than that, split it.

## Things you can say while it works

You can say any of these at any time, in your own words.

> Where are we?

> Pause the build, and resume it when I say so.

> Cancel this run.

The agent double-checks before it cancels anything.

> Only check in with me at the end, not after every checkpoint.

> Give me the viewer link, and open it if you can.

The viewer is a web page that shows the plan, every test and every review as it happens. You don't need it, since everything also shows up in the chat, but some people like to watch.

> Which reviewers and providers are available on this machine?

> Check that Ratchet's hooks are installed for this CLI, and install them if they are not.

> Set up a director for these three lanes, and ask me what you need to know.

That last one is for running several features at once, each in its own copy of the project, with one session keeping an eye on all of them.

### Saying yes, and saying no

The agent only moves forward on a clear yes. "Yes", "go ahead" and "approved" all count. A thumbs-up, a "sure, I guess", or a yes with a "but" on the end doesn't. In those cases it'll ask again. And a yes only answers the question it just asked. Saying yes to "only check in at the end?" doesn't also approve the plan.

You can also tell it to build without hooks, but then the gates are only suggestions. It will explain that and ask first, and it only goes ahead if you reply with exactly this:

> yes, go ahead without hooks

That reply only counts if Ratchet's prompt hook is running, because that hook is how Ratchet hears you. If none of its hooks run in your CLI at all, the agent will tell you to install them.

## If your team writes specs (OpenSpec, Spec Kit, BMAD)

If your project already uses OpenSpec, GitHub Spec Kit or the BMAD Method, Ratchet builds from the spec you've written instead of making up its own plan:

> Use Ratchet to build the add-dark-mode change.

> Use Ratchet to build story 1.3.

The spec says what to build. Ratchet makes sure it was actually built. Every scenario in the spec gets a test that has to pass, every task gets assigned to a step, and if anything is left out, the plan tells you why. As steps pass, Ratchet ticks off the matching tasks in your spec files. A finished BMAD story is marked done, with a note listing the tests that prove it. When you approve an OpenSpec change, it's archived using OpenSpec's own tool. If there's only one open change, feature or story, you don't even have to name it.

No spec tool? That's fine. The plan you approve is the spec. After your first finished feature, the agent will ask once if you'd like it to keep a simple record of what's been built: one file per feature, with each rule linked to the test that proves it. Yes or no, it remembers your answer.

## Common questions

**How long does it take?** A small feature usually takes tens of minutes, with the agent working by itself. Bigger ones take longer. You don't have to watch.

**Is it expensive?** It uses more AI time than letting the agent just write code, because it checks the work. Ratchet tries to keep that down: low-risk steps get one reviewer, small nitpicks don't hold them up, and later review rounds focus on what was just changed.

**Do I need other AI tools installed?** No. Without them, reviews come from your own agent's company, using a different model when it can. With another company's tool installed and signed in (Codex, Gemini, Copilot, Cursor, OpenCode and others), that tool reviews first. A second company tends to catch things the first one misses.

**The agent says the hooks aren't working.** Close the CLI, open it again in the same folder, and say "Set up Ratchet and continue". Still not working? Ask it to check that Ratchet's hooks are installed for this CLI and install them if not.

**Where does my code go?** Into your project, like any other change, with one git commit per finished step. If you're on your main branch or have unsaved changes, the agent offers to start a new branch first. Ratchet keeps its own notes in a `.ratchet/` folder.

**Can I stop halfway?** Yes. Say "pause". When you're ready, say "carry on", even in a new chat. Nothing is lost.

**What if I don't like the result?** Say so at the end, the same way you'd tell a colleague. Your feedback turns into new steps, and the agent remembers the lesson for next time.

**What if the reviewers keep finding the same mistake?** The agent will offer to add an automatic check for it, like a lint rule, so it gets caught in seconds from then on. You decide.

## How it works

You don't need any of this to use Ratchet. It's here if you want to know why the gates hold.

### The four gates

| # | Gate | What has to be true | How it's enforced |
|---|---|---|---|
| 1 | **Tests** | Tests written *before* the code fail first, then pass | Ratchet runs the tests itself and checks none were deleted |
| 2 | **Looks right** | The screen matches the design or the old screen, in the same state | Two separate reviewers compare screenshots and list every difference; any fixable difference blocks |
| 3 | **Review** | Reviewers try to break the code and find nothing serious | Reviewers keep going until none of them has a blocking finding on the current code |
| 4 | **You** | You try it and say yes | Only a real message from you counts |

Work with nothing to look at, like an API change or a refactor, skips gate 2. For rebuilding an old screen, gate 2 compares against the old screen instead of a new design.

### How screens are checked

Tests can tell you a button saves the form. They can't tell you the button is the wrong shade of blue, or that the heading is a bit too small. People notice those things straight away, and nobody ever writes them down in a spec. Comparing screenshots pixel by pixel doesn't work either, because two browsers never draw exactly the same pixels. So Ratchet has a reviewer look at the screen, the way a fussy designer would.

It starts with something to compare against. If you're rebuilding an old screen, that's the old screen. If you've got a design or screenshots, it uses those. If you've got neither, the agent makes a clickable prototype before any real code is written: a single web page in your app's style that you can click through when you review the plan. To keep the prototype in your app's style, Ratchet keeps a `DESIGN.md` file with your colours, fonts, spacing and components. If you don't have one, the agent writes it from your existing app.

When a step changes something you can see, Ratchet opens both the reference and your real app in a browser, puts them in the same state (same data, same dialog open, same form filled in), clicks through the same few actions on each, and takes screenshots along the way. Then two reviewers look at the results:

- one checks how it **looks**: layout, spacing, colours, text, alignment;
- one checks how it **behaves**: what happens when you click, type, submit or get an error.

They're picked from a different AI company than the one building, if one is available. They only get the screenshots, the page structure and the design file. They never see the code or the chat, so they can't be told "it's fine". They list every difference and where it is on the screen. Any difference that can be fixed in code blocks the step. The agent fixes it, the tests run again, and the screens are compared again, until nothing fixable is left.

Some things keep it honest:

- Each step is only judged on what it built so far. The first step might only be the page header, so the reviewers ignore the table that doesn't exist yet.
- If the two screenshots aren't showing the same thing (say, an empty order next to a full one), the comparison is thrown out and redone. The app isn't changed to match the wrong picture.
- Once a comparison has found problems, the agent can't quietly change what gets compared, or tell the reviewers a difference is "expected". It has to ask you.
- The prototype is never changed to match the app. If you decide the app looks better, that's your call, and the agent will ask.

### Who reviews the work

The model running your agent is called the host. It could be Claude, GPT, Gemini, a Copilot model or something else. Ratchet doesn't care which. What matters is that the reviewer is someone *different*.

So for every review, Ratchet tries these in order:

1. A model from another company, through any AI tool installed on your machine that works (Codex, Gemini, Claude, Copilot, Cursor, OpenCode, or a command you add).
2. A different model from the same company, for example Sonnet reviewing work done by Opus.
3. The same model that did the work, only if nothing else is available.

Each step has a risk level, which you see in the plan. Risk decides how many reviewers look (one for low, two for normal, four for high). It never changes who gets asked first. On low-risk steps, only serious findings block.

At the start of each run the agent checks which AI tools are installed and sends each one a tiny test request. A tool that's signed out or out of credit gets skipped for six hours, and the next one takes its place. When nothing else works, the host's own sub-agents fill the slot so the build never stalls. On a high-risk step that can mean fewer distinct reviewers than four. Substitutions are logged, the plan and viewer say who reviewed each round, and you're told up front if every review will come from the same company that did the build.

Each role also gets its own model. Planning, coding, fixing and reviewing use a strong model. Lighter jobs like planning tests use a cheaper one, and running tests the cheapest. You can change this in `.ratchet/config.json`.

### Mistakes that keep coming back

Reviewers label every finding with the kind of mistake it is. When the same kind shows up a second time, the agent offers to turn it into an automatic check, like a lint rule or an architecture test. Say yes and the check is built as its own small step, through every gate. Before it counts, Ratchet runs it against a deliberately bad example and against the real code where the mistake was found. It has to catch both and pass on today's code. For high-risk mistakes (security, data, money), a separate writer who never sees the check first writes disguised versions of the mistake, and the check has to catch those too. If a reviewer later finds a case the check missed, Ratchet strengthens it automatically and tells you.

### Why the agent can't cheat

- **Ratchet checks, it doesn't take anyone's word.** The agent doesn't get to say "tests pass". Ratchet runs them. Reviewers don't get to say "approved". Ratchet works out the verdict from what they found.
- **Tests come first.** A test that passes before the feature exists is rejected, because it isn't testing anything.
- **Tests can't disappear.** Every planned test has to still be there each time the tests run.
- **Any change resets the gates.** Every gate result is tied to the exact code it checked. Change the code afterwards and the gates run again.
- **Same state, or it doesn't count.** If two screenshots being compared aren't showing the same thing, the comparison is thrown out and redone.
- **Isolated UI reviewers.** The provider comes from `ui_reviewers` in the config: any supported tool other than the host, with the host's own sub-agents as the fallback. They work in a temp folder that holds only the screenshots, accessibility trees and the design file. They never see your project's agent instructions, the code or the chat.
- **Protected records.** A hook blocks anyone from editing Ratchet's records by hand. In Claude Code, which tells Ratchet which sub-agent is acting, it goes further: only the right reviewer can write each review, and during the build the agent in charge can't edit your code itself. Only the sub-agent doing that job can.
- **No quitting early.** Another hook stops the agent from ending its turn until a gate is passed, unless it's waiting for you, stuck, or paused.

### Using it with Codex, Gemini or Copilot

Claude Code finds Ratchet's skills on its own. For the non-Claude CLIs (Codex, Gemini CLI and Copilot CLI), the installer puts one shared copy in `~/.ratchet-loop/skills` (or `$RATCHET_HOME/skills` if you set that). Your CLI needs to be able to see it, for example with the skill folders linked into its own skills folder. The agent can do this for you. Just ask it to "link the Ratchet skills into this CLI's skills folder".

The hooks work the same on all four CLIs. If a hook itself breaks, it lets the action through and writes the error to `.ratchet/hook-errors.log`, rather than blocking everything.

**Full requirements:** Python 3.8+, git, and Node 18+ with Playwright for checking screens. Other AI tools are optional (`claude`, `codex`, `gemini`, `copilot` and others); without them, your agent's own sub-agents do every review.

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
│   │   ├── scripts/spec_sources.py    reads OpenSpec, Spec Kit and BMAD files; ticks tasks back
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
| `ratchet.py spec detect`, `spec import <openspec\|speckit\|bmad\|auto> <id>`, `spec status` | build from an OpenSpec change, Spec Kit feature or BMAD story; which scenarios are proven so far; `spec keep on\|off` records the user's answer about a running spec |
| `ratchet.py deferred` | minor review findings noted for later instead of blocking |
| `ratchet.py occurrences <kind>` | the real code where reviewers found a kind of mistake |
| `ratchet.py repeats` | kinds of finding seen twice or more that could become an automatic check |
| `ratchet.py guardrail --kind <k> --title <t> --why <w> --check-command "<c> {target}" --target <dir> --bad-example <fixture> --test-command <c> --test-file <f>` | after you pick the "automatic check" option: add the step that builds it |
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
python3 tests/test_guardrails.py
python3 tests/test_hooks.py
python3 tests/test_hooks_status.py
python3 tests/test_hooks_waiver.py
python3 tests/test_install.py
python3 tests/test_providers.py
python3 tests/test_specs.py
python3 tests/test_status_plain.py
python3 tests/test_viewer.py
```
