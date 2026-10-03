# Ratchet file schemas

All paths are relative to the project root. `<run>` = `.ratchet/runs/<slug>`.

Contents
1. Project layout
2. `<run>/app-scout.json` — written by ratchet-app-scout
3. `<run>/checkpoints.json` — written by ratchet-checkpoint-planner
4. `<run>/tests/CPn.json` — written by ratchet-test-planner
5. `<run>/states/CPn.json` — capture spec for the UI gate
6. UI review JSON — written by the visual / behaviour reviewers
7. Adversarial review JSON — written by ratchet-adversary
8. `.ratchet/config.json`
9. `ratchet.py status --json` / `lanes --json` — director interfaces
10. Hooks

---

## 1. Project layout

```
.ratchet/
  config.json          # loop settings (human_review, adversaries, app url, …)
  learnings.md         # EVERY agent reads this before working
  standards.md         # the written standard the adversaries enforce
  human_log.jsonl      # written by the UserPromptSubmit hook only (protected)
  active.json          # which run is active (protected)
  hook-heartbeat.json  # last time each hook fired and via which host (written by the hooks; see section 10)
  hook-errors.log      # internal hook errors (hooks fail open)
  unavailable-reviewers.json  # providers that just failed (quota, auth, timeout) and until when; skipped for the TTL
  viewer.json          # live viewer server: url, port, bind, pid, token (written by `view --serve`)
  runs/<slug>/
    state.json         # gate state — written ONLY by ratchet.py (protected). Optional field `hooks_waived`:
                       #   {at, by, prompt} = the user chose to build without hooks (see section 10)
                       #   Optional `hooks_waive_n` (int): incremented whenever a hooks-waive director token is used
                       #   and whenever `hooks_waived` is auto-cleared; folded into the hooks-waive director-token
                       #   payload, so that token is single use and a spent one is dead
    app-scout.json
    checkpoints.json
    tests/CP1.json …
    states/CP1.json …  # UI capture specs
    reference/         # prototype.html or reference screenshots
    reviews/           # scratch area where reviewers write JSON
    evidence/CP1/…     # logs, screenshots, reviews archived by ratchet.py (protected)
    viewer.html
    viewer-data.json   # what the viewer shows; its `hooks` key is the hooks object of section 10 (incl. `waived`)
DESIGN.md              # design system (path configurable)
```

## 2. app-scout.json

```json
{
  "stack": "Next.js 14 + Prisma + Postgres",
  "package_manager": "pnpm",
  "start_command": "pnpm dev",
  "url": "http://localhost:3000",
  "healthcheck": "curl -sf http://localhost:3000/api/health",
  "running": true,
  "test_runner": "vitest",
  "test_command_template": "pnpm vitest run {files}",
  "test_dir_convention": "src/**/__tests__/*.test.ts",
  "e2e_available": true,
  "ui_framework": "React + Tailwind",
  "design_md": "DESIGN.md",            
  "design_md_exists": true,
  "standards_sources": ["CONTRIBUTING.md", ".ratchet/standards.md"],
  "auth_for_ui_capture": "seed user admin@demo.test / demo1234 via /login",
  "notes": ["Leave balances live in src/server/leave/*.ts"],
  "risks": ["No seed script for managers"]
}
```

## 3. checkpoints.json

```json
{
  "feature": "Managers can approve or reject leave requests",
  "slug": "leave-approvals",
  "reference": { "type": "prototype", "paths": [".ratchet/runs/leave-approvals/reference/prototype.html"] },
  "assumptions": ["Only direct managers can approve"],
  "checkpoints": [
    {
      "id": "CP1",
      "title": "Approvals page skeleton",
      "summary": "An empty Approvals page that managers can open from the sidebar.",
      "complexity": 1,
      "risk": "low",
      "depends_on": [],
      "done_when": [
        "A manager sees 'Approvals' in the sidebar",
        "Opening it shows a page title and an empty list message",
        "Employees who are not managers do not see the link"
      ],
      "gates": { "behavior": true, "ui": true, "adversarial": true },
      "ui_scope": "Only the sidebar link, page header and empty state. Ignore the table — it is not built yet.",
      "ui_states": [
        { "name": "empty", "description": "Manager with no pending requests" }
      ],
      "tests": {
        "files": ["src/server/leave/__tests__/approvals.cp1.test.ts"],
        "command": "pnpm vitest run src/server/leave/__tests__/approvals.cp1.test.ts"
      },
      "files_likely": ["src/app/approvals/page.tsx", "src/components/Sidebar.tsx"],
      "notes": "Reuse the PageHeader component."
    }
  ]
}
```

Rules enforced by `ratchet.py plan-submit`:
- ids are `CP1…CPn`, in build order; `depends_on` may only point backwards
- `title` ≤ 8 words; `summary` one plain-language sentence
- `done_when` non-empty, written so a non-engineer can check it
- `gates.adversarial` is always `true`
- `gates.behavior:false` requires `behavior_skip_reason`
- `gates.ui:true` requires `ui_scope` and ≥1 `ui_states`
- `gates.behavior:true` requires `tests.command` (and `tests.files`)
- optional `ui_skip_reason` shown in the viewer when `ui:false`
- `risk`: `low` | `normal` (default) | `high`, which picks the adversarial reviewer set from config `review_depth`
- optional `reviewers`: an explicit list of reviewer ids that overrides `review_depth` for this checkpoint

Feedback (EXTEND mode) only appends new checkpoints with the next free ids; completed checkpoints are never edited or removed.

## 4. tests/CPn.json

```json
{
  "checkpoint": "CP2",
  "strategy": "Service-level tests against the leave module with an in-memory DB",
  "fixtures": ["manager Mia with direct report Eli", "Eli has 10 days balance"],
  "cases": [
    { "id": "CP2-T01", "title": "Manager approves a pending request",
      "given": "Eli has a pending 2-day request", "when": "Mia approves it",
      "then": "status is APPROVED and Eli's balance is 8", "layer": "code", "edge": false },
    { "id": "CP2-T04", "title": "Manager cannot approve someone outside their team",
      "given": "Zoe reports to another manager", "when": "Mia approves Zoe's request",
      "then": "a Forbidden error is returned and nothing changes", "layer": "code", "edge": true },
    { "id": "CP2-T07", "title": "Approve button shows a spinner while saving",
      "layer": "browser", "edge": false,
      "note": "Visual/interaction — checked by the UI gate behaviour reviewer, not in code" }
  ]
}
```

`layer`: `code` (unit/integration/API/CLI — MUST be automated and tagged with its ID in the test name) or `browser` (only checkable by clicking — handed to the UI gate). `ratchet.py tests-written` and `ratchet.py behavior` fail if any `code` case ID is missing from `tests.files`.

## 5. states/CPn.json — capture spec (UI gate)

Consumed by `ratchet-ui-compare/scripts/capture.mjs`.

```json
{
  "checkpoint": "CP1",
  "viewport": { "width": 1280, "height": 800 },
  "targets": {
    "reference": ".ratchet/runs/leave-approvals/reference/prototype.html",
    "app": "http://localhost:3000"
  },
  "login": { "app": [ {"goto": "/login"}, {"fill": "#email", "value": "mia@demo.test"},
                      {"fill": "#password", "value": "demo1234"}, {"click": "button[type=submit]"},
                      {"waitFor": "text=Dashboard"} ] },
  "states": [
    {
      "name": "empty",
      "scope": "Sidebar link, page header, empty state only",
      "reference": { "path": "?state=empty#/approvals", "steps": [] },
      "app":       { "path": "/approvals", "steps": [ {"waitFor": "text=No requests"} ] },
      "journey": [ {"hover": "[data-testid=nav-approvals]"}, {"click": "[data-testid=nav-approvals]"} ],
      "full_page": false,
      "mask": ["[data-testid=clock]"]
    }
  ]
}
```

Step verbs: `goto`, `click`, `fill`+`value`, `press`, `hover`, `select`+`value`, `check`, `uncheck`, `wait` (ms), `waitFor` (selector), `scroll` (selector or `bottom`). The same `journey` is replayed on both targets for the behaviour reviewer, with a screenshot after every step.

## 6. UI review JSON

Each reviewer writes one file (only that reviewer may write it); `ui_review.py` merges them into `combined.json` (only the script may write it).

```json
{
  "reviewer": "visual",
  "checkpoint": "CP1",
  "verdict": "FAIL",
  "invalid_reason": null,
  "scope": "Sidebar link, page header, empty state only",
  "differences": [
    { "id": "V1", "state": "empty", "severity": "major",
      "location": "page header, top-left, ~24px below nav",
      "description": "Title is ~20px in the app vs ~28px in the reference (about 70% of the size)",
      "expected": "28px semibold per DESIGN.md heading-lg", "actual": "20px regular",
      "fixable_in_code": true, "suggested_fix": "Use <PageHeader size='lg'>" }
  ],
  "notes": "Proportions judged against each screenshot's width."
}
```

- `verdict`: `PASS` | `FAIL` | `INVALID` (screenshots are not the same state/section — orchestrator must recapture)
- `severity`: `blocker` | `major` | `minor` | `nit`
- ratchet.py ignores the self-reported PASS/FAIL and recomputes: any difference with `fixable_in_code: true` blocks (nits too, unless `ui_block_on_nits:false`).

`combined.json` = `{ "checkpoint", "fingerprint", "compared": {"spec_sha256", "scope"}, "reviewers": ["visual","behavior"], "verdict", "invalid_reason", "differences": [...all, each with "reviewer"] }`. `compared` records what the round compared: the hash of `states/CPn.json` (stamped into the capture manifest by `capture.mjs`) and the scope given to the reviewers. After a FAIL round both are frozen: `ui-record` refuses a later round that compared something else until the gate passes, unless the user agreed to a change (`ask ui-respec CPn`, then `ui-respec CPn`).

## 7. Adversarial review JSON

```json
{
  "reviewer": "codex-correctness",
  "lens": "correctness",
  "vendor": "openai",
  "checkpoint": "CP2",
  "fingerprint": "<output of ratchet.py fingerprint → id>",
  "round_nonce": "<the nonce printed by ratchet.py reviewers CPn for this round>",
  "files_reviewed": ["src/server/leave/approve.ts", "src/server/leave/types.ts"],
  "verdict": "CHANGES_REQUESTED",
  "findings": [
    { "id": "A1", "severity": "major", "file": "src/server/leave/approve.ts", "line": 42,
      "rule": "standards.md §3 — services never import from app/",
      "kind": "service-imports-app",
      "description": "approve.ts imports a React component type",
      "fix": "Move the DTO type to src/server/leave/types.ts" }
  ]
}
```

`reviewer` must be the reviewer id from the config roster, because adv-record matches reviews to the required list by id. `fingerprint` is **required**: reviews of other code are rejected. `round_nonce` is **required** and must match the round opened by `ratchet.py reviewers CPn`, so a review from an earlier round, or one written without launching a reviewer, is rejected. `files_reviewed` lists the files the reviewer actually read; a review with no findings must list at least one of the files this checkpoint changed, or it is rejected. A review file written less than `min_review_seconds` (config, default 20) after its round opened is rejected as too fast to be real. Other-provider reviews are written by `external_review.py`, which stamps `reviewer`, `vendor`, `fingerprint`, `round_nonce` and `files_reviewed` itself.

A reviewer approves by returning `"findings": []`. Which findings block is set by config `blocking_severities` (by risk) and `scope_later_rounds`; findings that don't block are recorded for the final review (`ratchet.py deferred`), never silently dropped. If a finding is wrong, the fixer writes a rebuttal in `fixes.md` and the NEXT adversary round decides whether to withdraw it.

## 8. .ratchet/config.json

```json
{
  "human_review": "end",          
  "host_provider": "auto",
  "providers": [
    {"id": "codex",  "preset": "codex"},
    {"id": "gemini", "preset": "gemini"},
    {"id": "claude", "preset": "claude"},
    {"id": "cursor", "preset": "cursor", "model": "gpt-5", "for_risk": ["low", "normal", "high"]},
    {"id": "acme",   "company": "acme", "model": "acme-large",
                     "command": ["acme", "review", "--model", "{model}", "{prompt_file}"], "stdin": false}
  ],
  "reviewers": {
    "host-standards":       {"kind": "agent", "lens": "standards",   "vendor": "host"},
    "host-correctness":     {"kind": "agent", "lens": "correctness", "vendor": "host"},
    "host-both":            {"kind": "agent", "lens": "both",        "vendor": "host"},
    "host-alt-standards":   {"kind": "agent", "lens": "standards",   "vendor": "host", "alt": true},
    "host-alt-correctness": {"kind": "agent", "lens": "correctness", "vendor": "host", "alt": true},
    "host-alt-both":        {"kind": "agent", "lens": "both",        "vendor": "host", "alt": true}
  },
  "review_depth": {
    "low":    ["any-both"],
    "normal": ["any-standards", "any-correctness"],
    "high":   ["any-standards", "any-correctness", "any-correctness", "any-standards"]
  },
  "blocking_severities": {"low": ["blocker", "major"], "normal": "all", "high": "all"},
  "scope_later_rounds": true,
  "role_models": {"ratchet-implementer": "strong", "ratchet-test-writer": "standard", "ratchet-test-runner": "light", "…": "…"},
  "host_models": {"anthropic": {"strong": "inherit", "standard": "sonnet", "light": "haiku", "alternates": ["sonnet", "opus"]}},
  "checks": [{"kind": "service-imports-app", "command": "npx depcruise src --validate", "title": "Guardrail: services stay independent", "added": "2026-10-03", "from": "<slug>/CP7"}],
  "probe_providers": true,
  "probe_ttl_minutes": 120,
  "unavailable_ttl_hours": 6,
  "max_adversarial_rounds": 6,
  "max_adversarial_rounds_run": 30,
  "approver": "human",
  "ui_reviewers": {"visual": ["gemini", "claude", "codex"], "behavior": ["claude", "gemini", "codex"]},
  "regression_command": "pnpm test",
  "commit_per_checkpoint": true,
  "design_md": "DESIGN.md",
  "standards": [".ratchet/standards.md", "CONTRIBUTING.md"],
  "app": { "start_command": "pnpm dev", "url": "http://localhost:3000", "healthcheck": null }
}
```

`human_review`: `end` (default — you review once all checkpoints pass), `each` (review every checkpoint), `none` (fully autonomous after plan approval; gates stay just as strict). Other keys and defaults: see `DEFAULT_CONFIG` in `scripts/ratchet.py`.

**Providers.** Each entry in `providers` is an agent CLI that can review: a `preset` (`claude`, `codex`, `gemini`, `copilot`, `cursor`, `opencode`) or its own `command`, plus an optional `model` and `company`. The company is whose model it runs; when not given it is worked out from the model name (claude/opus/sonnet → anthropic, gpt/codex → openai, gemini → google, grok → xai, glm → zhipu, qwen, deepseek, kimi, llama, mistral) or from a single-company preset. A provider whose company is unknown, or the same as the host's, is not used as an independent reviewer. `for_risk` limits it to some risk levels (for a smaller model). Each provider gives three reviewers: `<id>-correctness`, `<id>-standards`, `<id>-both`. `ratchet.py providers --discover` finds installed tools, tests them and adds what works; `--add` registers one (it is tested first); `--check` re-tests; `--remove` drops one. Test results are kept in `.ratchet/provider-status.json`. `providers` is not one of the rules locked at plan approval: adding a working provider mid-run only changes who is available.

**Review slots.** Each `review_depth` entry is a list of slots. `any-<lens>` means: every provider's `<id>-<lens>` in order (a company not yet reviewing this round preferred), then `host-alt-<lens>` (the host's company on a different model, from `host_models.<company>.alternates`; unavailable when the host has no alternates), then `host-<lens>`. A slot can also be a reviewer id or a list of ids as alternatives; a slot of only command reviewers falls back to `host-alt-*`, then `host-*`, same lens first. Before relying on a provider not tested in the last `probe_ttl_minutes`, `ratchet.py reviewers` sends it a test request; one that fails is skipped for `unavailable_ttl_hours`. `blocking_severities`: which severities block, by risk (`"all"`, or a list; unknown severities always block). `scope_later_rounds`: from round 2 on, minor findings outside the code changed since the previous round are recorded instead of blocking. Recorded findings: `ratchet.py deferred`. A project whose `review_depth` is still the pre-1.5 default is moved to the new default automatically. `host_provider`: `auto` detects the running agent CLI from its environment (Claude Code, Codex, Gemini CLI, Copilot CLI); set it explicitly (or `RATCHET_HOST_PROVIDER`) for anything else. `ratchet.py reviewers CPn --json` shows the resolved list with `fallback_for`, reasons, models and the round's `review_rules`. `ratchet.py reviewer-status [--reset ID]` shows or clears availability. Command placeholders: `{prompt_file}` `{prompt}` `{out_file}` `{root}` `{model}`; `"stdin": true` pipes the prompt.

**Automatic checks and guardrail steps.** Every adversarial finding may carry a `kind` (a kebab-case label for the type of mistake); only this explicit label counts. A kind found in two places (distinct checkpoint + file) is a repeat (`ratchet.py repeats`). After the user picks a guardrail option (`ask choice --guardrail "A=<kind>"`, then `choice`, at the same checkpoint) or the director signs `director-sign guardrail --kind <kind>`, `ratchet.py guardrail` inserts a checkpoint right after the current one with a `guardrail` object (`kind`, `check_command` with a `{target}` placeholder, `target`, `bad_example`, `why`) and a ready test plan. It refuses a kind that hasn't repeated or that already has a check. Before that checkpoint can complete, Ratchet runs the check on `bad_example` (must fail) and on `target` (must pass), and neither run may change a file. Ratchet also rebuilds every reviewed code version where a blocking finding of that kind was recorded (`git archive` of that version, with the step's own files laid over it and installed dependencies linked in) and runs the check there: it must fail on each. For a high-risk kind (`guardrail.high_risk`: found on a high-risk checkpoint or rated a blocker), the step is high risk, needs ≥3 files in `<variants_dir>/bad` (each must fail) and ≥2 in `<variants_dir>/good` (each must pass), written by `ratchet-variant-writer` before `tests-written`. A failed proof sends the step back to `behavior` (bounded by `max_behavior_attempts`). Then the check (with `{target}` filled in, plus `template`, `target`, `bad_example`, `test_command`, `test_files`, `high_risk`, `variants_dir`) is added to `checks` and recorded in `learnings.md`. When a later review finds a blocker or major finding of a covered kind, Ratchet adds a step with `guardrail.strengthen: true` right after the current one, automatically; it replaces that kind's check once proven against every past case plus the missed one. Every behavior gate runs each `checks` command after the tests; a failing check, or one that changes files, fails the gate. Minor findings of a covered kind are recorded instead of blocking; blocker and major findings always block. `checks` is locked at plan approval like the other rules (removing or editing a check needs the user's yes); a completed guardrail updates the lock itself.

**Models per role.** `role_models` gives each sub-agent role a tier (`strong`, `standard`, `light`) or a model name; `host_models.<company>` maps the tiers to model names for that host (`inherit` = the session's own model). A host with no entry runs every role on the session's model. `ratchet.py models` prints the table the orchestrator uses when it spawns sub-agents.

`approver`: `human` (chat message after the pause), `director` (signed token only), `either`. A lane enrolled with `director-enroll` is forced to `director`, whatever this key says.

## 9. Director interfaces

`ratchet.py --root <lane> status --json` (or `ratchet.py lanes --scan <dir> --json` for every lane under a folder):

```json
{"root": "/work/lanes/track2-labels", "slug": "validity-labels", "feature": "Validity labels",
 "phase": "building", "current": "CP3", "step": "adversarial", "next": "CP3: run the ratchet-adversarial-loop skill …",
 "stop_ok": false, "waiting_on": null, "blocked_reason": null, "blocked_reason_plain": null,
 "awaiting_since": null, "approval_mode": "director", "adv_rounds_used": 7,
 "unavailable_reviewers": {"codex": {"reason": "quota", "since": "2026-09-29T17:00:00+00:00", "until": "2026-09-29T18:00:00+00:00"}}, "host_provider": "anthropic",
 "hooks_warning": null, "hook_heartbeat": {"prompt": {"at": "…", "host": "claude"}},
 "hooks": {"host": "claude", "installed": true, "heartbeat_seen": true, "fresh": true, "missing": false},
 "viewer_url": "http://10.0.0.12:4481/?t=…", "viewer_live": true,
 "done": 2, "total": 6, "updated_at": "…",
 "checkpoints": [{"id": "CP1", "title": "…", "risk": "low", "status": "done", "step": "complete",
                  "commit": "a1b2c3d", "gates": {"behavior": {"status": "passed", "attempts": 1},
                  "human": {"status": "passed", "attempts": 1, "by": "human"}, "…": {}}}]}
```

`awaiting_since` is the time the loop began waiting on a human or director (null otherwise). `by` on a gate entry (`human` or `director`) is set when a person's approval passed it. `unavailable_reviewers` maps a skipped reviewer id to `{reason, since, until}` (why it was skipped, when, and when it may be tried again). `waiting_on` is `human`, `director` or `null`. Director-only commands (they refuse unless the session has `RATCHET_ROLE=director`, and the guard hook blocks them in every other session): `director-key [--rotate]` (the key lives at `~/.config/ratchet/director.key`, or `RATCHET_DIRECTOR_KEY_FILE`), `director-enroll`, and `director-sign approve-plan|human-approve|feedback`, which prints a token for the lane's current pause. Tokens go to `approve-plan` / `human-approve` / `feedback` via `--director-token`.

## 10. Hooks

`ratchet.py hook <stop|prompt|guard> --host auto|claude|codex|gemini|copilot` is the single entry point every agent CLI calls. Written by `install-hooks` (or the plugin's `hooks/hooks.json`):

| host | config file (project / user) | stop | prompt | guard |
|---|---|---|---|---|
| claude | `.claude/settings.json` / `~/.claude/settings.json` | Stop | UserPromptSubmit | PreToolUse |
| codex | `.codex/hooks.json` / `~/.codex/hooks.json` | Stop | UserPromptSubmit | PreToolUse |
| gemini | `.gemini/settings.json` / `~/.gemini/settings.json` | AfterAgent | BeforeAgent | BeforeTool |
| copilot | `.github/hooks/ratchet.json` / `~/.copilot/hooks/ratchet.json` | agentStop | userPromptSubmitted | preToolUse |

**Plain-language output (user-facing; never paste JSON at the user).** `ratchet.py status` (no flag) prints plain text: first line `Step N of M done: <title>` (or `All M steps are done.` / `No plan yet.`), the phase in words, what is happening now, a re-run note when a passed check went out of date, and, when blocked, the reason in quotes plus 2-3 lines `A) ...`, `B) ...`, `C) ...`. `ratchet.py plan-summary` prints, per step: title, first done-when line, `UI review: yes|no`, `Risk:` and `Reviewed by:`, plus a hooks-skipped note when waived; paste it at the plan approval stop. `next` gains `options: [{letter, text}]` (blocked phase only, same choices as the text status) and the warning/doctor text is addressed to the agent. viewer-data.json `next` gains `label` (plain sentence for the current step); `hooks.waived` drives the viewer's hooks-skipped banner. `status --json` and `lanes --json` gain `blocked_reason_plain` (the block reason as a plain sentence; unknown reasons pass through unchanged, null when not blocked; `blocked_reason` stays the raw text), viewer-data.json `state` gains the same `blocked_reason_plain`, and `next` in the blocked phase gains `reason_plain` next to the raw reason in `action`.

Runtime files: `.ratchet/hook-heartbeat.json` (last time each hook fired and via which host; read by `doctor`, `next`, and `status --json` as `hooks_warning` and `hooks`) and `.ratchet/hook-errors.log` (internal errors; hooks fail open).

**`hooks` object** (in `status --json`, in `next`, and as the only key of `doctor --json`; read-only, computed for the agent CLI running the command):

| key | type | meaning |
|---|---|---|
| `host` | string | `claude`, `codex`, `gemini`, `copilot` or `unknown` (from `host_provider` / environment) |
| `installed` | bool | a project or user hook config for `host` contains Ratchet hooks |
| `heartbeat_seen` | bool | the heartbeat has an entry from `host` (any host if `host` is `unknown`) |
| `fresh` | bool | such an entry has an `at` timestamp within the last 24 hours and not more than 5 minutes in the future |
| `missing` | bool | `not installed and not fresh` |
| `waived` | bool | (`status --json`, `next`, viewer-data `hooks`, plain `status`; not `doctor`) the user chose to build without hooks (`ask hooks-waive`, then `hooks-waive` after their reply, which must be one of a fixed allow-list of full phrases, `HOOKS_WAIVE_PHRASES` in ratchet.py, after lowercasing and dropping punctuation and a trailing \"please\": \"yes go ahead without hooks\", \"yes skip the hooks\", \"yes build without hooks\", \"go ahead without hooks\", \"skip the hooks\", \"proceed without hooks\"; anything else is refused and clears the question; stored in state.json as `hooks_waived {at, by, prompt}`, with a `hooks_waived` history event). True only while no enforcement hook is installed or seen firing: the guard and stop hooks decide, not the prompt hook (which is how the yes is recorded) and not `missing`. Once they are installed or fire, the next state save removes `hooks_waived` (history event `hooks_waive_cleared`), so it cannot resurface later without new consent. `hooks-waive` refuses to record anything while they are active. Never blocks anything |

`hooks_warning` (string or null) is null when `fresh`. Otherwise it tells the agent either that the hooks are installed but silent (restart the CLI) or that none fired yet (offer to run `install-hooks`, ask the user first). A corrupt or odd `hook-heartbeat.json` or `config.json` counts as empty here; `doctor` never raises for it.

