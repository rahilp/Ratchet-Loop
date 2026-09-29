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
9. `ratchet.py status --json` / `lanes --json` — for a director

---

## 1. Project layout

```
.ratchet/
  config.json          # loop settings (human_review, adversaries, app url, …)
  learnings.md         # EVERY agent reads this before working
  standards.md         # the written standard the adversaries enforce
  human_log.jsonl      # written by the UserPromptSubmit hook only (protected)
  active.json          # which run is active (protected)
  viewer.json          # live viewer server: url, port, bind, pid, token (written by `view --serve`)
  runs/<slug>/
    state.json         # gate state — written ONLY by ratchet.py (protected)
    app-scout.json
    checkpoints.json
    tests/CP1.json …
    states/CP1.json …  # UI capture specs
    reference/         # prototype.html or reference screenshots
    reviews/           # scratch area where reviewers write JSON
    evidence/CP1/…     # logs, screenshots, reviews archived by ratchet.py (protected)
    viewer.html
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

Each reviewer writes one file; `ui_review.py combine` (or the orchestrator) merges them into `combined.json`.

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

`combined.json` = `{ "checkpoint", "reviewers": ["visual","behavior"], "verdict", "invalid_reason", "differences": [...all, each with "reviewer"] }`.

## 7. Adversarial review JSON

```json
{
  "reviewer": "codex-correctness",
  "lens": "correctness",
  "vendor": "openai",
  "checkpoint": "CP2",
  "fingerprint": "<output of ratchet.py fingerprint → id>",
  "verdict": "CHANGES_REQUESTED",
  "findings": [
    { "id": "A1", "severity": "major", "file": "src/server/leave/approve.ts", "line": 42,
      "rule": "standards.md §3 — services never import from app/",
      "description": "approve.ts imports a React component type",
      "fix": "Move the DTO type to src/server/leave/types.ts" }
  ]
}
```

`reviewer` must be the reviewer id from the config roster, because adv-record matches reviews to the required list by id. `fingerprint` is **required**: reviews of other code are rejected. Second-vendor reviews are written by `external_review.py`, which stamps `reviewer`, `vendor` and `fingerprint` itself.

A reviewer approves by returning `"findings": []`. Every finding must be fixed — there is no "minor, ignore" path. If a finding is wrong, the fixer writes a rebuttal in `fixes.md` and the NEXT adversary round decides whether to withdraw it.

## 8. .ratchet/config.json

```json
{
  "human_review": "end",          
  "host_provider": "auto",
  "reviewers": {
    "host-standards":     {"kind": "agent",   "lens": "standards",   "vendor": "host"},
    "host-correctness":   {"kind": "agent",   "lens": "correctness", "vendor": "host"},
    "host-both":          {"kind": "agent",   "lens": "both",        "vendor": "host"},
    "claude-correctness": {"kind": "command", "preset": "claude",  "lens": "correctness", "vendor": "anthropic"},
    "codex-correctness":  {"kind": "command", "preset": "codex",   "lens": "correctness", "vendor": "openai"},
    "gemini-correctness": {"kind": "command", "preset": "gemini",  "lens": "correctness", "vendor": "google"},
    "copilot-correctness":{"kind": "command", "preset": "copilot", "lens": "correctness", "vendor": "github"},
    "…-standards":        "(same four providers with lens standards)",
    "my-reviewer":        {"kind": "command", "lens": "both", "vendor": "acme",
                           "command": ["acme", "review", "{prompt_file}"], "stdin": false}
  },
  "review_depth": {
    "low":    ["host-both"],
    "normal": ["host-standards", ["codex-correctness", "gemini-correctness", "claude-correctness", "copilot-correctness"]],
    "high":   ["host-standards", "host-correctness",
               ["codex-correctness", "gemini-correctness", "claude-correctness", "copilot-correctness"],
               ["gemini-standards", "claude-standards", "codex-standards", "copilot-standards"]]
  },
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

Each `review_depth` entry is a list of **slots**: a reviewer id, or an ordered list of alternatives. At review time the first **available** alternative fills the slot (`host-*` agent reviewers are always available; a command reviewer must be a different provider from the host, have its CLI on PATH, and have no recent failure recorded in `.ratchet/unavailable-reviewers.json`). A slot of only command reviewers falls back to the host's own `host-*` reviewers automatically, same lens first. `host_provider`: `auto` detects the running agent CLI from its environment (Claude Code, Codex, Gemini CLI, Copilot CLI); set it explicitly (or `RATCHET_HOST_PROVIDER`) for anything else. `ratchet.py reviewers CPn --json` shows the resolved list with `fallback_for` and reasons. `ratchet.py reviewer-status [--reset ID]` shows or clears availability. Command placeholders: `{prompt_file}` `{prompt}` `{out_file}` `{root}`; `"stdin": true` pipes the prompt. Presets' flags may differ between CLI versions, so override `command` if needed. `approver`: `human` (chat message after the pause), `director` (signed token only), `either`. A lane enrolled with `director-enroll` is forced to `director`, whatever this key says.

## 9. Director interfaces

`ratchet.py --root <lane> status --json` (or `ratchet.py lanes --scan <dir> --json` for every lane under a folder):

```json
{"root": "/work/lanes/track2-labels", "slug": "validity-labels", "phase": "building",
 "current": "CP3", "step": "adversarial", "next": "CP3: run the ratchet-adversarial-loop skill …",
 "stop_ok": false, "waiting_on": null, "blocked_reason": null, "approval_mode": "director",
 "viewer_url": "http://10.0.0.12:4481/?t=…", "viewer_live": true,
 "adv_rounds_used": 7, "done": 2, "total": 6, "updated_at": "…",
 "checkpoints": [{"id": "CP1", "title": "…", "risk": "low", "status": "done", "step": "complete",
                  "commit": "a1b2c3d", "gates": {"behavior": {"status": "passed", "attempts": 1}, "…": {}}}]}
```

`waiting_on` is `human`, `director` or `null`. Director-only commands (they refuse unless the session has `RATCHET_ROLE=director`, and the guard hook blocks them in every other session): `director-key [--rotate]` (the key lives at `~/.config/ratchet/director.key`, or `RATCHET_DIRECTOR_KEY_FILE`), `director-enroll`, and `director-sign approve-plan|human-approve|feedback`, which prints a token for the lane's current pause. Tokens go to `approve-plan` / `human-approve` / `feedback` via `--director-token`.

## 10. Hooks

`ratchet.py hook <stop|prompt|guard> --host auto|claude|codex|gemini|copilot` is the single entry point every agent CLI calls. Written by `install-hooks` (or the plugin's `hooks/hooks.json`):

| host | config file (project / user) | stop | prompt | guard |
|---|---|---|---|---|
| claude | `.claude/settings.json` / `~/.claude/settings.json` | Stop | UserPromptSubmit | PreToolUse |
| codex | `.codex/hooks.json` / `~/.codex/hooks.json` | Stop | UserPromptSubmit | PreToolUse |
| gemini | `.gemini/settings.json` / `~/.gemini/settings.json` | AfterAgent | BeforeAgent | BeforeTool |
| copilot | `.github/hooks/ratchet.json` / `~/.copilot/hooks/ratchet.json` | agentStop | userPromptSubmitted | preToolUse |

Runtime files: `.ratchet/hook-heartbeat.json` (last time each hook fired and via which host; read by `doctor`, `next`, and `status --json` as `hooks_warning` and `hooks`) and `.ratchet/hook-errors.log` (internal errors; hooks fail open).

**`hooks` object** (in `status --json`, in `next`, and as the only key of `doctor --json`; read-only, computed for the agent CLI running the command):

| key | type | meaning |
|---|---|---|
| `host` | string | `claude`, `codex`, `gemini`, `copilot` or `unknown` (from `host_provider` / environment) |
| `installed` | bool | a project or user hook config for `host` contains Ratchet hooks |
| `heartbeat_seen` | bool | the heartbeat has an entry from `host` (any host if `host` is `unknown`) |
| `fresh` | bool | such an entry has an `at` timestamp within the last 24 hours and not more than 5 minutes in the future |
| `missing` | bool | `not installed and not fresh` |

`hooks_warning` (string or null) is null when `fresh`. Otherwise it tells the agent either that the hooks are installed but silent (restart the CLI) or that none fired yet (offer to run `install-hooks`, ask the user first). A corrupt or odd `hook-heartbeat.json` or `config.json` counts as empty here; `doctor` never raises for it.

