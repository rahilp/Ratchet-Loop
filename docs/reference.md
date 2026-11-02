# Ratchet Loop reference

[← Back to the README](../README.md) · [Using Ratchet](guide.md) · [How it works](how-it-works.md)

## Reference (commands the agent runs)

Everything above is done by talking to your agent. This section is for people typing at a terminal, and it lists what the agent runs on your behalf. `ratchet.py` lives in `skills/ratchet-orchestrator/scripts/`, alongside seven smaller modules it imports from; see the Module map in `skills/ratchet-orchestrator/references/modules.md` for what each one owns.

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
│   │   ├── scripts/provider_health.py the shared provider test results and benches (provider-health.json)
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
./install.sh [--host claude|codex|gemini|copilot|opencode|all] [--project]
```

| Form | What it does |
|---|---|
| `./install.sh` (same as `--host auto`) | installs for the agent CLI running the script, detected from its environment; Claude Code if it can't tell (OpenCode is never detected) |
| `--host claude` | Claude copy in `~/.claude` (skills and agents) plus Claude hooks |
| `--host codex` / `gemini` / `copilot` | shared copy in `~/.ratchet-loop` (or `$RATCHET_HOME`) plus that CLI's hooks; the skills are also linked into `~/.codex/skills`, `~/.gemini/skills` or `~/.copilot/skills` when that folder's CLI is set up |
| `--host opencode` | the shared copy in `~/.ratchet-loop` (or `$RATCHET_HOME`) plus one OpenCode plugin file, `ratchet.js`, in `~/.config/opencode/plugins/` (or in `.opencode/plugins/` in the repo with `--project`). OpenCode is only installed by this explicit form |
| `--host all` | the Claude copy, the shared copy, and hooks for four CLIs (Claude Code, Codex, Gemini, Copilot); OpenCode is not part of it |
| `--project` | Claude's copy and every CLI's hook config go into the current repo (`.claude/`, `.codex/`, `.gemini/`, `.github/hooks/`, `.opencode/plugins/`) instead of your home folder. The shared skills copy for Codex, Gemini and Copilot still goes to `~/.ratchet-loop` (or `$RATCHET_HOME`), and the project hook files point at it |

It is safe to run twice: skill folders are replaced, hooks are not duplicated, and your own hooks and settings are left alone. Claude Code and Codex can also load the repo as a plugin through their manifests (`.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`). Hooks only: `ratchet.py install-hooks --host <cli> [--scope project|user] [--dry-run] [--uninstall]`.

OpenCode is not detected on its own, so install it with `install-hooks --host opencode`. With `--scope project` (the default for `install-hooks`), the OpenCode plugin goes in `.opencode/plugins/ratchet.js` in the repo. With `--scope user`, it goes in `~/.config/opencode/plugins/ratchet.js`. A folder set by `OPENCODE_CONFIG_DIR` or `XDG_CONFIG_HOME` moves that user path. Running the OpenCode install again changes nothing when the plugin is up to date. Uninstalling the OpenCode plugin removes only Ratchet's own file. In OpenCode's plugins folder, a file of the same name that is not Ratchet's is never overwritten or removed. `ratchet.py doctor` lists OpenCode and says if its plugin is installed. Restart OpenCode after installing so it loads the plugin.

### Commands

| Command | Purpose |
|---|---|
| `ratchet.py doctor [--json]` | which CLIs have Ratchet hooks and when each last fired |
| `ratchet.py status [--json]`, `plan-summary` | plain-text state; the plan step by step, with a `Review budget:` line (rounds the plan allows against the run-wide limit; a round limit is not a time budget) and the same `Projected finish:` line `status` prints |
| `ratchet.py changes CPn [--json]` | read-only: which changes since the step started are docs, which are code and how many code lines changed, which test files name each changed doc, and whether `doc_test_command` is configured |
| `ratchet.py brief [CPn] --role implementer\|fixer\|test-writer\|reviewer\|scout\|planner` | a sub-agent's opening text: the plan's objective (word for word as `plan-summary` shows it, or a plain no-objective note), then that role's own rules; the orchestrator pastes this at the top of every spawn prompt. For the planner, it also ends with a "What earlier work says" section: recent learnings, findings earlier runs saved for later and nobody accepted yet, and limits the user already accepted, most relevant first then newest, each part capped with a count and a pointer to the full file (`.ratchet/learnings.md`, or a run's `state.json` under `.ratchet/runs/`). It also lists the related recorded specs for the parts being changed (OpenSpec, including Ratchet's own running records; finished Spec Kit features; done BMAD stories), each with its title, a one-line purpose and its path; no earlier work at all gets one plain line instead |
| `ratchet.py view` | the viewer link (live URL if a server runs, else a `file://` link) |
| `ratchet.py view --serve [--open]` | live page on a free port (4477 and up) at `http://127.0.0.1:<port>/` |
| `ratchet.py view --serve --bind 0.0.0.0 [--public-host my-box.local]` | share `http://<host>:<port>/?t=<token>` (requests without the token get 403) |
| `ratchet.py view --stop` | stop the viewer server |
| `ratchet.py pause --reason "..."`, `resume [--claim]`, `cancel` | pause, resume, cancel (resume-claim and cancel are asked about first) |
| `ratchet.py wait --for "..." [--minutes N]` | the run's own chat waits on background reviewers or sub-agents: until then (default 30 minutes, 1 to 120) it may end its turn without a blocked stop |
| `ratchet.py set-review end\|each\|none` | how often you check in |
| `ratchet.py reviewer-status [--reset <id>]` | the host, who is available, and retrying one early (`--reset` clears that provider's shared bench for every lane) |
| `ratchet.py providers [--discover] [--check [id]] [--add <id> --preset <p> --model <m> [--prefer-for <lens>[,<lens>]] [--variant <level|none>]] [--remove <id>]` | agent tools that can review: find, test, add or remove them. `--check` also accepts a reviewer name such as `codex-correctness`; an unknown name is refused with the list of known provider ids. A per-use or unknown-cost model (`providers --discover --json`'s `paid_options`) needs `ask paid-provider` answered first; `--add` for one stops, with no request sent, until it is answered |
| `ratchet.py ask paid-provider <tool> <model>` | ask the user whether to spend money on a per-use or unknown-cost model before it is ever used; only their own exact reply, `pay per use: <model>`, counts (never the usual yes words), and only then does `providers --add` send the one test request and add it |
| `ratchet.py models [--json]` | what each role asked for versus what is really used (sub-agent roles, `orchestrator`, `director`, `reviewer`, the UI review lenses), which level asked (`builtin`, `global`, `project`) and why they differ; read-only, never contacts a provider |
| `ratchet.py spec detect`, `spec import <openspec\|speckit\|bmad\|auto> <id>`, `spec status` | build from an OpenSpec change, Spec Kit feature or BMAD story; which scenarios are proven so far; `spec keep on\|off` records the user's answer about a running spec |
| `ratchet.py deferred` | review findings noted for later instead of blocking (minor ones, and any blocker/major/unknown-severity one with no `user_impact`), plus, in their own section first, any findings you accepted yourself, with your reason and who accepted them, and a 'fixed later in the run' section for ones `mark-fixed` closed |
| `ratchet.py mark-fixed CPn <ref>="<how>"` | record that a finding left for later was really fixed: refused unless its file changed since the round that raised it AND a later round was recorded on that changed code |
| `ratchet.py receipt [--json]` | one proof receipt to paste into the pull request at the final review: each step's title, commit and elapsed time, the run's own elapsed time (no cost or tokens), who reviewed each step (host-company fallbacks marked, with why when recorded), findings accepted, left for later or fixed later in the run, and every objective done-means line marked proven or unproven next to the test case(s) that prove it (or "no test linked") |
| `ratchet.py shortcuts [<name>] [--json]` | for a guard-like change: runs each shortcut table in `tests/shortcuts/` whose watched files this run changed against the code the run started from and the current code, and shows which rows got worse, stayed open, got fixed or errored (a crash or odd output is an error, never a refusal; rows the run started with cannot be dropped or flipped). It runs by itself when the last step completes, the final review, `status` and the receipt show the result, and it never blocks the finish. A test case with layer `shortcuts` links a table to done-means lines. Details in `schemas.md` section 14 |
| `ratchet.py ask accept-finding CPn F1,F2`, `accept-finding CPn` | ask the user to accept named open (blocking) findings of a blocked round, with their own reason (`accept: <reason>`); record it once they reply |
| `ratchet.py ask accept-line <n>[,<n>]`, `accept-line [--director-token <t> --reason "<r>"]` | at the final review, ask the user to accept named unproven done-means lines (by the table's numbers) with their own reason (`accept: <reason>`); record it once they reply (a director token with `--reason` in an enrolled lane); accepted lines show as accepted in the final-review table and the receipt and no longer hold the finish |
| `ratchet.py ask raise-limit CPn`, `raise-limit CPn` | ask the user to raise a blocked step's own review-round limit by one round, with their own reason (`raise: <reason>`); offered at most once per step, ever, whatever `config.json`'s `max_adversarial_rounds` is later edited to; in a director-enrolled lane the director signs it instead (`raise-limit CPn --director-token <t> --reason "..."`) |
| `ratchet.py triage CPn <ref>=fix:"<reason>"` / `<ref>=rebut:"<reason>"` | whenever a round blocks: decide fix or rebut for every blocking finding, even a single one, before the next round opens; never accept/defer/waive |
| `ratchet.py check-in CPn [--decision simplify\|change\|accept\|stop] [--why "<reasoning>"]` | when reviews drag, see (or decide) the check-in against the plan's objective; `accept` is always refused here |
| `ratchet.py merged` | between steps, after merging main into the lane with git (`git merge --no-ff main`, finished and committed): re-runs the earlier steps' checks on the merged code (the regression command when set, else each finished step's own tests), makes the merge the starting point of the next step so its reviewers and commit see only its own changes (a file that differs from git's own clean merge of the two parents, such as a conflict resolved by hand, was reviewed by no one, so it stays in the next step's review and commit and the output names it; a file both sides edited in different places that git merged on its own is not one of them, and a merge made with `-X` or `-s` options may show files as resolved by hand, which is safe; a merge with such files is accepted only while a step is starting to build, and while a step review or the final review waits it is refused with the commit to undo it to, so the agent undoes it, gets the review approved and merges again once the next step has started, or at the final review asks for a step of its own through feedback; a merge git did on its own is accepted in every phase; needs git 2.38 or newer), and accepts standards, design-file and config-rule changes that are exactly main's version (it names each). It asks nothing and approves nothing: anything else that changed (the plan, test plans, the spec, a hand-resolved rule) still needs the user's yes. Refused in the middle of a step (finish it first), when no finished merge is at HEAD, when tracked files have uncommitted edits, when files git does not track yet exist outside `.ratchet/` (commit them in a reviewed step, delete them, or add them to `.git/info/exclude`; `.gitignore` does not work, and interpreter litter like `__pycache__` is ignored), or when the lane added, changed or removed files since the last step finished (those never went through the gates; when `commit_per_checkpoint` is false `complete` records the reviewed tree and `merged` compares with it, so a hand commit of exactly the reviewed work is accepted, and a step that finished without a recorded tree gets a plain refusal instead of an unchecked pass). A path that differs from the step's starting tree counts as a lane commit only if some commit since the step's starting commit touched it (stray untracked files that were never committed are ignored once deleted); a lane whose history no longer includes that starting commit, or a run that did not record it, is refused plainly. Known limit: reviewed work that was never committed and then deleted from disk is not a lane commit, so only the earlier steps' re-run checks guard it). If an earlier step's checks fail on the merged code the run stops and says which; to recover, undo the merge with git (`git reset --keep <the merge's first parent, short sha>`, which refuses instead of discarding edits to the run's files), fix what broke, and once the user lets the run go on, merge main again and run `merged` (a fix committed on top of the merge is not accepted). Run it in the background when a regression suite is set. The guard steers the merge to happen between steps: in the middle of a step it refuses `git merge` and `git pull` with a plain message to finish the step (`complete`) first; between steps it allows them, and while a merge is in progress it lets plain `git add`, `git checkout --theirs/--ours` and `git commit` touch Ratchet's shared files (`.ratchet/config.json`, `standards.md`, `learnings.md`, `.gitignore`) so a conflict there can be resolved. Merge with `git merge --no-ff main`, and `git fetch` then `git merge --no-ff <branch>` instead of `git pull`, so the merge leaves a merge commit. A plain merge or pull that only fast-forwards the lane leaves none, so it cannot be told from the step's own work: Ratchet notices it from HEAD's reflog, and `next` and `tests-written` then say to undo it with `git reset --keep <the old short sha>` and redo it with `--no-ff`. After a merge lands (one in which main only removed files counts too), `next` says to run `merged` first and `tests-written` refuses until it has. A second merge in a step after a hand-resolved one was accepted is refused by the guard (finish the step first); if one lands anyway, `next`, `tests-written` and `merged` name `git reset --keep <its first parent>`. |
| `ratchet.py occurrences <kind>` | the real code where reviewers found a kind of mistake |
| `ratchet.py repeats` | kinds of finding seen twice or more that could become an automatic check |
| `ratchet.py guardrail --kind <k> --title <t> --why <w> --check-command "<c> {target}" --target <dir> --bad-example <fixture> --test-command <c> --test-file <f>` | after you pick the "automatic check" option: add the step that builds it |
| `ratchet.py lanes --scan <dir> [--json]` | each lane's phase, progress and whether it waits on a director |
| `ratchet.py lanes --serve --scan <dir> [--bind 0.0.0.0]` | one dashboard for every lane |
| `ratchet.py config [--scope global\|project\|effective] [--sources] [--global] [--set key=value] [--unset key]` | show or change settings in `.ratchet/config.json` (reviewer roster, retry caps, `regression_command`, `ui_globs`, app URL), or with `--global` the machine-wide file; one role's model is one `agents` line, `--set agents.<role>=<model>`, and `--unset agents.<role>` goes back |
| `ratchet.py ci-export [--stdout]` | write (or update) `.github/workflows/ratchet.yml`, the GitHub Actions workflow that keeps the run's mechanical checks running on every push and pull request; refuses to replace a hand-written file at that path; `--stdout` prints the workflow instead of writing it |

Ratchet's own guard has a shortcut table, `tests/shortcuts/guard.json` (run by `tests/shortcuts/guard_runner.py`, checked by `tests/test_shortcuts_guard.py`): a fixed list of shortcut commands the guard should refuse, next to everyday read-only commands it should allow. A run that changes the engine (`ratchet.py`) shows the table's result at the final review. The table watches `ratchet.py` plus `spec_sources.py` and `provider_health.py` (the engine imports both, so the comparison copy cannot load without them). It covers only the shell check (`_guard_shell`): refusals of commands that set Ratchet's reserved variables come from the hook layer (`decide_guard`), which this table does not run, so a clean result does not show those hold. Rows are only ever added; a row is never removed, reworded or flipped, and a row the guard still gets wrong stays listed as still open.

The viewer server only serves the viewer, its live data and the prototype, never the rest of `.ratchet/`. Its URL is saved in `.ratchet/viewer.json`.

Director-signed approvals: the director's session runs in the director role (see the orchestrator's SKILL.md), creates a key once with `ratchet.py director-key`, then enrolls each lane with `ratchet.py --root <lane> director-enroll`. In an enrolled lane chat messages stop counting as approvals; approvals need `ratchet.py --root <lane> director-sign <action>` and `--director-token <t>`. The actions are `approve-plan`, `human-approve`, `feedback`, `approve-changes`, `cancel`, `set-review`, `hooks-waive`, `ui-respec`, `guardrail --kind <kind>`, `accept-finding`, `raise-limit` and `choice`. `director-sign raise-limit CPn --reason "..."` signs one more review round for a step blocked at its review limit, at most once per step; the lane applies it with `raise-limit CPn --director-token <t> --reason "..."` (the same reason, which is saved to learnings.md as a chat raise saves it). `director-sign choice <letter>` signs one of the lettered options the lane put up; the lane applies it with `choice <letter> --director-token <t>`, and the pick unlocks exactly what a developer's pick would. Order matters for the actions that answer a question: the lane runs its `ask` first (`ask raise-limit CPn`, `ask choice --options ...`, `ask accept-finding CPn <ids>`), without showing it to the user as a chat question, and the director signs after it; a token is bound to that pause, so a token signed before the `ask`, or before an `ask` run again, is stale and needs a fresh signature. A chat `raise: ...` reply or a typed letter approves nothing in an enrolled lane. Builder sessions cannot read the key or sign. The director's per-user folder (`RATCHET_HOME`) and key file must be the same ones the lane's hooks use: Ratchet refuses to run when they differ. `RATCHET_ROLE=director` alone is not enough either: the director commands run only where Ratchet's guard hook recorded a director session, so run the director's agent CLI with Ratchet's hooks, in its own folder (not a lane's), and reach lanes with `--root <lane>`. A builder that sets the variable, even inside a script, is refused. A missing record is refused too, whenever Ratchet's hooks are installed or have run there (restart the director's agent CLI so its hooks record the role). Only when no Ratchet hooks are in use at all is the variable taken at its word, and the command says so, because nothing else is enforced then either. The record of the director's session is written by Ratchet's hook command, so a script deliberately written to cheat, running as your own user, can still forge it by calling that command directly with made-up input; this check stops easy shortcuts, not a deliberate forgery. Sign and apply in one step: for the routine approvals the director can do everything in one call, `ratchet.py --root <lane> director-sign approve-changes --apply`, `director-sign accept-finding CPn --ids F1,F2 --reason "..." --apply`, `director-sign raise-limit CPn --reason "..." --apply` and `director-sign choice <letter> --apply`. The order is always the same, ask, then sign, then apply: with `--apply` Ratchet runs the question step the action needs itself (`ask approve-changes`, `ask accept-finding`, `ask raise-limit`; a lettered choice is signed from the options the lane registered with `ask choice`), signs the token and applies it, all under one hold of the lane's state lock, so the token cannot go stale in between and two applies at once never both apply. It works from every phase the action applies to, including a lane that paused itself for the director (all four are accepted while the lane is paused: the request is judged against the phase it paused from and applied there, and the lane stays paused afterwards, so `resume` carries on where the approval left it). It refuses, changing nothing: a lane that is not enrolled (it takes the developer's approval), a builder session (director-only, and the guard blocks it), the wrong phase (not blocked, no open findings, no lettered question waiting, plan not yet approved), a missing `--reason`, an id that is not open, a letter not offered; and applying the same request twice records nothing twice (findings already accepted, a raise already spent, nothing changed since approval). `--apply` is refused for everything else the director signs (`approve-plan`, `human-approve`, `feedback`, `cancel`, `set-review`, `hooks-waive`, `ui-respec`, `guardrail`): those stay a token the lane applies, and `--apply` approves nothing there. The old separate three steps (the lane's `ask`, `director-sign <action>` for the token, then the lane's command with `--director-token`) still work exactly as before. In an enrolled lane, `next`, `status`, `status --json` and the refusals name the director's `director-sign ... --apply` command (or the token flow) as the next action; they never tell the agent to ask the user.

**The developer's policy for routine approvals (`director-policy`).** Director only. The developer sets once, through their agent, which routine approvals the director may sign without asking: `ratchet.py director-policy` shows the rules (`--json` prints `{"rules": {...}, "changes": [...]}`), `director-policy --set <action>=<rule>` sets one and `director-policy --clear <action>` removes it. The rules are a fixed menu: `approve-changes=test-files` (only when every change is to a test file; a changed plan, test plan, rule or reference file is refused; Python test files only, and only when the whole module is unchanged apart from comments and formatting (its syntax tree is compared with the red copy), so a changed helper, import or module-level line asks the developer; other languages always ask the developer, because Ratchet cannot prove a planned test intact there), `raise-limit=max:N` (N of 1 or more signed raises across the whole run, each step still raised at most once; the run-wide review budget is never reset), `choice=no-unlock` (only a lettered option that unlocks no locked file and adds no guardrail) and `accept-finding=max:minor` or `max:major` (never a blocker: one finding above the limit refuses the whole acceptance). The policy is one file per user, `director-policy.json`, in `RATCHET_HOME` next to the director key (`director.key`), never inside a project; only the director session changes it, every change is logged in the file with its time, and the guard keeps builder sessions from reading or writing it. With no policy file `director-sign` works exactly as before. With one, every routine signature (`director-sign <action>` with or without `--apply`) is checked before a token is printed or anything is asked: a covered one is signed and its rule is recorded as `policy_rule` on the log entry (`changes_approved`, `round_limit_raised`, `choice_made`, `finding_accepted`) and shown by `receipt` (on both paths: `director-sign <action> --apply` records it in the same call; the plain `director-sign <action>` checks the rule before it prints a token, and the lane's command that applies the token checks the policy again under its own lock, so a policy that changed meanwhile, a used-up `raise-limit` or a lane that changed since is refused there, and records `policy_rule` on the same log entry; with no policy file nothing changes); one no rule covers is refused with a plain message to ask the developer, changing nothing. A damaged or unreadable policy file refuses every routine approval and names `director-policy.json` and the developer, instead of acting like no policy; `director-policy --set` rewrites it from scratch and `--clear` replaces it with a policy with no rules (either way the repair is logged). Clearing the last rule leaves a policy with no rules, which covers nothing: every routine approval asks the developer, and the dated change history stays in the file. `director-policy --remove` deletes the file (logged in `director-policy-removed.jsonl` beside it): only that goes back to no policy at all. `raise-limit` counts only raises the director signed (a raise the developer gave by chat does not count). A policy can never cover plan approval (`approve-plan`), scope changes (`feedback`), final approval (`human-approve`), `cancel`, review settings (`set-review`), hooks waivers (`hooks-waive`), `ui-respec` or paid providers: Ratchet refuses such a rule, and those approvals work exactly as before whether a policy exists or not.

Starting several lanes from one request (director only): `ratchet.py --root <main> lanes-start --request "<text>" --lane NAME=FEATURE [--lane ...] [--name N] [--json]` makes, for each lane, a branch named NAME from main's HEAD, a plain git worktree next to the main checkout, the lane's enrollment, and a brief file; it records the request in `.ratchet/requests/<name>.json` and prints each lane's start command. Like integrate, it supports a Ratchet project at the top of its git repository only (a project in a subfolder is refused; start its lanes by hand). Every refusal (a bad or repeated lane name, an existing branch or folder, a main with uncommitted project files, an unusable `lane_start_command`) comes before anything is created. The lane has no run yet: its agent runs `init` itself. Ratchet never launches an agent CLI; it only prints the start command from your `lane_start_command` setting (an argument list with `{worktree}`, `{brief}`, `{name}` and `{model}`; `{model}` is the model you chose for `agents.orchestrator` and is empty when you chose none (lanes-start then adds a plain note that the model argument is empty because no orchestrator model is chosen), and a command without `{model}` prints exactly as before), or plain words. Main's uncommitted Ratchet settings are copied into each worktree as uncommitted changes, and main gains no commit. Paseo is at most one optional way to launch a lane; nothing here depends on it.

Tracking a request's lanes (any session, no director needed): `ratchet.py lanes --request <name> [--json]`, run from the main checkout or from inside any lane's worktree, lists the lanes of a request made by `lanes-start`, in the request's order: phase (`not_started` before the lane's `init`), steps done of total, who it waits on and since when, whether it is merged into main, and `integrate` as the next director action for a finished lane that is not merged yet. A lane whose worktree was removed is said plainly (recreate it or drop the lane; a merged one needs nothing). Merged is decided by git alone: the lane branch's tip is in main's history and the branch has at least one commit of its own (a brand-new lane with no commits is not merged), so a hand merge counts and `integrations.jsonl` never changes it. It changes nothing. Plain `lanes --scan` is unchanged.

Merging a finished lane into main (director only, like `director-sign` and `director-enroll`): `ratchet.py --root <main> integrate <lane folder or branch>` merges a finished lane into local main, and with `--dry-run` only checks it. The check covers that the lane's run is finished, that the lane and main have no uncommitted project changes and no merge in progress, and that main's own run is not building in the middle of a step or blocked, nor paused from building or blocked (resume it until it is between steps; a done, cancelled or not-yet-approved run does not stop it). Known limits are refused plainly, with the advice to merge that lane by hand: a Ratchet project that is not at the top of its git repository; a main checkout whose git config sets `core.worktree`; a project with git submodules; a project whose installed dependencies link back into main (workspace links, an editable install, Composer's autoloader), which integrate cannot test yet; and a lane that changes a dependency manifest or lockfile (package.json, lockfiles, pyproject.toml, requirements files, Gemfile, composer.json, Cargo.toml, go.mod and their locks), because the suite would run with main's installed dependencies, which do not match the lane's. It prints the commits it would bring to main, which of Ratchet's own shared files differ, which uncommitted Ratchet files on main it would carry across (never committing them) and the suite it would run. The dry run changes nothing and runs no git command that writes or pushes. The real run makes the merge in a scratch clone of main, runs the project's `regression_command` there (it refuses when none is set), and only if the suite passes on that exact merge commit and main has not moved does it fast-forward main to it: one merge commit naming the lane and its run, nothing else committed, nothing pushed. The scratch clone uses main's local git config and runs main's hooks, so the merge commit is made as it would be in main; right before main moves, integrate re-checks main under its state lock (same tip and branch, its run between steps, no new uncommitted project files). A failing suite, a suite that rewrites tracked files before passing, a conflict in a project file, main moving or changing during the suite, or an untracked or ignored file the merge would overwrite leaves main exactly as it was and says what to do next; a lane already in main is a no-op. Ratchet's own shared files (`.ratchet/config.json`, `learnings.md`, `standards.md` and `.gitignore` under `.ratchet/`) are merged by Ratchet's rules inside the scratch copy, not by git, so no one resolves a run config conflict by hand: config.json is merged key by key against the merge base (a setting changed on one side only takes that side; a setting changed on both sides keeps main's and is listed; the lane-local reviewer, provider and run settings, `review_depth`, `reviewers`, `providers` with its paid_ok_at consent, `host_provider`, `host_models`, `role_models`, `ui_reviewers`, `approver`, `human_review`, `max_adversarial_rounds`, `max_adversarial_rounds_run`, `unavailable_ttl_hours`, `probe_providers`, `probe_ttl_minutes`, `probe_timeout_sec`, `quota_retest_minutes` and `provider_share`, always keep main's value and are listed; the guardrail `checks` keep main's entries plus every entry the lane added that main lacks, matched by kind (the way a guardrail step identifies its check; by command when an entry has no kind), so no check is lost, and a check the lane strengthened or removed while main left it alone takes the lane's change; every other key, and any unknown one, is project-wide); learnings.md and the Ratchet .gitignore are merged line by line against the version the lane branched from, whether or not git could merge them cleanly: the lines added on both sides are kept (main's lines, then the lane's new ones) and a line either side deleted stays deleted; standards.md takes the side that changed it, and when both sides changed it (even in different places, where git would merge them) integrate stops with main unchanged and says to decide which wording to keep; the dry run reports both standards stops too. Every lane setting not taken is named in the output and in the record line's `not_taken` list. Main's uncommitted changes to those four files are never committed and never refused: integrate saves their bytes next to its lock, sets those paths back to main's tip for the fast-forward only, then re-applies the saved copy by the same rules (base = main's old tip, ours = the uncommitted copy, theirs = the merged result) and leaves the result uncommitted; where the uncommitted value and the merge both changed a setting, the uncommitted value wins and is listed; a file that had staged changes comes back unstaged (the output and the record say which; a staged version that differed from the working copy is saved first in a folder of its own that integrate never removes, named in the output and in the record as `staged_saved`), and a staged removal is staged again; a re-apply the rules cannot do (an uncommitted standards.md and the merge both changed it) stops before main moves, or, when a file cannot be written back after it moved, moves main back and then restores the saved bytes (when main cannot be moved back, the attempt is recorded as merged, nothing is written over the file and the message names the saved copy). The saved bytes are written back only while main is still at the tip they were saved against; once main holds the merge, a copy from before it is never copied over the file: it is kept in a folder of its own and the message says how to re-add only the developer's own edits. After a kill, the next integrate follows the same rule. Main's own run never asks approve-changes for a rule or reference file the integrate changed: one that matched the approved baseline before becomes the new baseline (logged as `integrated_lane`); one already changed before stays as it was. Every attempt is appended to `.ratchet/integrations.jsonl`, even one killed mid-run: the next integrate records it first (as merged, with a note, when main's history already holds the merge, without changing main's own run; otherwise as rolled back).

The person's override: if you ever need to approve or accept something yourself, outside the chat, run the command in your own terminal with `RATCHET_HUMAN_OVERRIDE=1` set. Ratchet shows a short code in that terminal and goes ahead only after you type it back. An agent's shell has no terminal, so setting the variable there approves nothing. Each use is written to `.ratchet/human_log.jsonl`; if Ratchet can't write that line, it refuses the override and changes nothing. Like the other protections, this stops easy shortcuts, not a script deliberately written to cheat: one running as your own user that starts its own pseudo-terminal, or forges Ratchet's records by calling its hook command directly, can still get past it.

### Running the tests

For the full regression run, use one command. It runs every `tests/test_*.py` suite at the same time and finishes in a few minutes instead of running them one after another:

```
python3 tests/run_all.py
```

It runs half as many suites at a time as the machine has CPU cores, at least 2, because each suite starts processes of its own and one suite per core was slower (`--workers N` changes that), and stops a suite that runs longer than 15 minutes (`--timeout SECONDS`). On a busy machine that limit stretches: it is `--timeout` times the machine's one-minute load average divided by its CPU count, checked again while a suite runs (a load spike extends the limit, and a load drop never takes back time already granted: the highest limit reached stays in force), never less than `--timeout`, and never more than `--max-stretch` times it (default 4; `--max-stretch 1` turns the stretch off), so a suite that is only slow because the machine is busy is not failed for it. When a suite ran past its `--timeout` or was stopped at a stretched limit, the summary says so, with the factor, both limits (the highest reached) and the load. Each suite gets its own empty temp folder, and a suite that leaves anything in it is reported as failed even when its own checks passed. When a suite ends, run_all.py also stops every process it started that is still running, including one it detached into its own session, by a tag (`RATCHET_RUN_ALL_TAG`) each suite's processes inherit; on a system without `/proc` it can only stop the suite's process group, and its summary says so. The slowest suites start first: run_all.py remembers how long each suite took last time in a small file in your user cache folder (`~/.cache/ratchet-loop/`, never in the repository) and, for a suite it has not timed yet, guesses from the file's size. At the end it shows the last lines of output of every failed suite, a table of every suite and its time, slowest first, and a `N passed, M failed` line counting suites. It exits non-zero when any suite failed or none were found.

Each test file is a standalone script; none needs a framework, so you can also run one suite on its own. Every suite makes its temp folders through `tests/_tmp.py`, which removes them, and stops any background server the suite started, when the suite ends, whether it passes, fails a check or crashes. The one exception is `tests/test_cleanup.py`, which tests that helper and so makes and removes its own throwaway folders without it. `tests/test_runner.py` tests run_all.py itself against tiny made-up suites and never runs the real ones. A few harness scripts are deliberately named outside the `test_*.py` pattern so `run_all.py`'s own directory scan never re-discovers and re-runs them as one more suite: `tests/measure_suite_baseline.py` times a given list of real suites (see `evidence/baseline_timings.json` in schemas.md), and `tests/confirm_full_suite.py` runs the real `python3 tests/run_all.py` once end to end and checks that it stays fast and 100% green, then separately checks `.ratchet/standards.md` for the written-down in-process-conversion pattern.

```
python3 tests/test_accept_finding.py
python3 tests/test_agents_config.py
python3 tests/test_agents_docs.py
python3 tests/test_agents_launch.py
python3 tests/test_agents_reviewers.py
python3 tests/test_agents_session.py
python3 tests/test_brief.py
python3 tests/test_brief_guard.py
python3 tests/test_checkin.py
python3 tests/test_ci_export.py
python3 tests/test_ci_export_docs.py
python3 tests/test_ci_export_shapes.py
python3 tests/test_ci_export_write.py
python3 tests/test_cleanup.py
python3 tests/test_complete_foreground.py
python3 tests/test_config_layers.py
python3 tests/test_config_scopes.py
python3 tests/test_converge.py
python3 tests/test_dedup_unknown_model.py
python3 tests/test_deferred_fixed.py
python3 tests/test_digest.py
python3 tests/test_digest_docstring.py
python3 tests/test_digest_specs.py
python3 tests/test_director_apply.py
python3 tests/test_director_apply_extra.py
python3 tests/test_director_policy.py
python3 tests/test_director_policy_extra.py
python3 tests/test_director_sign_extra.py
python3 tests/test_director_sign_more.py
python3 tests/test_discover.py
python3 tests/test_discover_omp.py
python3 tests/test_discover_paid.py
python3 tests/test_dm_accept.py
python3 tests/test_dm_accept_extra.py
python3 tests/test_dm_findings.py
python3 tests/test_dm_findings_extra.py
python3 tests/test_dm_finish.py
python3 tests/test_dm_finish_extra.py
python3 tests/test_dm_plan.py
python3 tests/test_dm_plan_extend.py
python3 tests/test_dm_table.py
python3 tests/test_docs.py
python3 tests/test_docs_accuracy.py
python3 tests/test_flaky.py
python3 tests/test_feedback_fixes.py
python3 tests/test_ffl_provider_add.py
python3 tests/test_ffl_probe_memory.py
python3 tests/test_ffl_probe_extra.py
python3 tests/test_ffl_provider_shape.py
python3 tests/test_ffl_company_view.py
python3 tests/test_ffl_role_report.py
python3 tests/test_ffl_wording.py
python3 tests/test_ffl_migration.py
python3 tests/test_fix_round.py
python3 tests/test_fix_round_carry_bench.py
python3 tests/test_fix_round_diff.py
python3 tests/test_fix_round_diff_fixes.py
python3 tests/test_fix_round_fixes.py
python3 tests/test_gates.py
python3 tests/test_guard_python_reads.py
python3 tests/test_guard_reads.py
python3 tests/test_guard_reserved_names.py
python3 tests/test_guard_writes.py
python3 tests/test_guardrails.py
python3 tests/test_hook_codex_contract.py
python3 tests/test_hook_codex_payloads.py
python3 tests/test_hook_list_payload.py
python3 tests/test_hook_opencode_docs.py
python3 tests/test_hook_opencode_guard.py
python3 tests/test_hook_opencode_install.py
python3 tests/test_hook_opencode_prompt.py
python3 tests/test_hook_opencode_scope.py
python3 tests/test_hook_opencode_smoke.py
python3 tests/test_hook_opencode_stop.py
python3 tests/test_hook_opencode_stop_extra.py
python3 tests/test_hooks.py
python3 tests/test_hooks_status.py
python3 tests/test_hooks_waiver.py
python3 tests/test_impact_analysis_additive.py
python3 tests/test_impact_analysis_docs.py
python3 tests/test_impact_analysis_e2e.py
python3 tests/test_impact_analysis_mode.py
python3 tests/test_impact_analysis_parity_hazard.py
python3 tests/test_impact_analysis_repoint.py
python3 tests/test_impact_analysis_registration.py
python3 tests/test_install.py
python3 tests/test_integrate_check.py
python3 tests/test_integrate_files.py
python3 tests/test_integrate_merge.py
python3 tests/test_integrate_phases.py
python3 tests/test_integrate_safety.py
python3 tests/test_lanes_start.py
python3 tests/test_lanes_start_extra.py
python3 tests/test_lanes_request.py
python3 tests/test_lane_added_tests.py
python3 tests/test_limits.py
python3 tests/test_load_determinism.py
python3 tests/test_load_timeouts.py
python3 tests/test_load_timeouts_fixes.py
python3 tests/test_merge_guard.py
python3 tests/test_merge_main.py
python3 tests/test_modularize_cp2.py
python3 tests/test_modularize_cp3.py
python3 tests/test_modularize_cp4.py
python3 tests/test_modularize_cp5.py
python3 tests/test_modularize_cp6.py
python3 tests/test_modularize_cp7.py
python3 tests/test_modularize_cp8.py
python3 tests/test_modularize_cp9.py
python3 tests/test_modularize_inventory.py
python3 tests/test_models_explain.py
python3 tests/test_objective.py
python3 tests/test_opencode_big.py
python3 tests/test_plan_change.py
python3 tests/test_providers.py
python3 tests/test_question_waiting.py
python3 tests/test_quick_path.py
python3 tests/test_quick_path_docs.py
python3 tests/test_quick_path_docs_step.py
python3 tests/test_quick_path_records.py
python3 tests/test_quick_path_small.py
python3 tests/test_quota_check.py
python3 tests/test_quota_refill.py
python3 tests/test_quota_reset.py
python3 tests/test_quota_share.py
python3 tests/test_quota_shared.py
python3 tests/test_quota_spread.py
python3 tests/test_receipt.py
python3 tests/test_receipt_proof.py
python3 tests/test_related_tests.py
python3 tests/test_related_tests_fixes.py
python3 tests/test_results.py
python3 tests/test_review_diff_full_rounds.py
python3 tests/test_review_diff_full_rounds_fixes.py
python3 tests/test_review_diff_large_files.py
python3 tests/test_review_diff_large_files_fixes.py
python3 tests/test_reviewers_pin_reopen.py
python3 tests/test_scoped_diff_priority.py
python3 tests/test_review_objective.py
python3 tests/test_run_all_dedupe.py
python3 tests/test_run_cmd_env.py
python3 tests/test_runner.py
python3 tests/test_schedule.py
python3 tests/test_shortcuts.py
python3 tests/test_shortcuts_extra.py
python3 tests/test_shortcuts_guard.py
python3 tests/test_runner_load.py
python3 tests/test_runner_load_concurrency.py
python3 tests/test_runner_load_fixes.py
python3 tests/test_signing.py
python3 tests/test_since_tree_rejected_round.py
python3 tests/test_specs.py
python3 tests/test_split_check.py
python3 tests/test_stale_lock.py
python3 tests/test_status_plain.py
python3 tests/test_stop_on_change.py
python3 tests/test_stop_on_change_fixes.py
python3 tests/test_stretched_timeout_cleanup.py
python3 tests/test_trust_cp1.py
python3 tests/test_trust_cp3.py
python3 tests/test_trust_cp4.py
python3 tests/test_trust_cp5_a.py
python3 tests/test_trust_cp5_b.py
python3 tests/test_trust_cp5_c.py
python3 tests/test_trust_cp5_d.py
python3 tests/test_trust_cp5_e.py
python3 tests/test_trust_cp5_regression.py
python3 tests/test_ui_capture_lint.py
python3 tests/test_ui_capture_stub.py
python3 tests/test_ui_capture_viewport.py
python3 tests/test_ui_gate_fidelity_docs.py
python3 tests/test_ui_insufficient_evidence.py
python3 tests/test_ui_review_precheck.py
python3 tests/test_viewer.py
python3 tests/test_waiting.py
```
