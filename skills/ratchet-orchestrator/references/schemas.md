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
11. Building from a spec tool (OpenSpec, Spec Kit, BMAD)

---

## 1. Project layout

```
.ratchet/
  config.json          # loop settings (human_review, adversaries, app url, …)
  learnings.md         # EVERY agent reads this before working
  standards.md         # the written standard the adversaries enforce
  human_log.jsonl      # written by the UserPromptSubmit hook, plus ratchet.py's override audit lines (log_override, section 10) (protected)
  active.json          # which run is active (protected)
  hook-heartbeat.json  # last time each hook fired and via which host (liveness only, never crash state; written by the hooks; see section 10)
  hook-env.json        # the per-user folder and director key file the hooks use, and the role of the session that last ran a command here (written by the hooks only; see section 10)
  hook-env/            # one record per hook run (its start time, and a guard's session role, in the file name) that hook-env.json combines (written by the hooks only; see section 10)
  hook-errors.log      # internal hook errors (hooks fail open)
  hook-crashes/        # per host and hook: empty marker files for its last crash and last clean run since (written by the hooks; see section 10)
  hook-crashes-unsaved.log  # fallback: crashes whose hook-crashes/ marker could not be saved (written by the hooks; see section 10)
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
    reference/         # prototype.html or reference screenshots; spec-source.json when built from a spec (section 11)
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
- on a run built from a spec (section 11): every spec task ref is in some checkpoint's `spec_refs`, every scenario or acceptance-criterion ref has a test case with that `spec_ref` (or is in `spec_refs` of a UI-gated checkpoint), every ref exists in the spec, and anything left out is in the top-level `spec_unmapped` as `{"ref", "reason"}`

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

On a run built from a spec, a case carries `"spec_ref": "<scenario ref>"` (or a list) for the scenario or acceptance criterion it proves.

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

`combined.json` = `{ "checkpoint", "fingerprint", "compared": {"spec_sha256", "scope"}, "reviewers": ["visual","behavior"], "verdict", "invalid_reason", "differences": [...all, each with "reviewer"] }`. `compared` records what the round compared: the hash of `states/CPn.json` (stamped into the capture manifest by `capture.mjs`) and the scope given to the reviewers. `ui-record` only accepts a `combined.json` whose real location (symlinks and `..` resolved) is directly inside the round's own folder, `reviews/CPn/round-NN/`, where NN is one more than the UI rounds recorded so far; a file written anywhere else, copied in from an earlier round's folder, or linked in from outside is refused with a message naming the right folder. After a FAIL round both are frozen: `ui-record` refuses a later round that compared something else until the gate passes, unless the user agreed to a change (`ask ui-respec CPn`, then `ui-respec CPn`). A correctly-located `combined.json` is also capped at `MAX_REVIEW_BYTES` (8 MiB): one over the cap on disk, or one that grows past it while being read, is refused outright and never read into memory, naming the limit.

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

`reviewer` must be the reviewer id from the config roster, because adv-record matches reviews to the required list by id. `fingerprint` is **required**: reviews of other code are rejected. `round_nonce` is **required** and must match the round opened by `ratchet.py reviewers CPn`, so a review from an earlier round, or one written without launching a reviewer, is rejected. `files_reviewed` lists the files the reviewer actually read; a review with no findings must list at least one of the files this checkpoint changed, or it is rejected. Each review file must sit directly inside the round's own folder, `reviews/CPn/adv-round-NN/` (printed by `ratchet.py reviewers CPn`, and `output_dir` in its `--json`); its real location is checked with symlinks and `..` resolved, so a file written anywhere else, copied in from an earlier round's folder, or linked in from outside is refused. A review file written less than `min_review_seconds` (config, default 20) after its round opened is rejected as too fast to be real. A correctly-located review file is also capped at `MAX_REVIEW_BYTES` (8 MiB): one over the cap on disk, or one that grows past it while being read, is refused outright and never read into memory, naming the limit. Other-provider reviews are written by `external_review.py`, which stamps `reviewer`, `vendor`, `fingerprint`, `round_nonce` and `files_reviewed` itself.

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

**Automatic checks and guardrail steps.** Every adversarial finding may carry a `kind` (a kebab-case label for the type of mistake); only this explicit label counts. A kind found in two places (distinct checkpoint + file) is a repeat (`ratchet.py repeats`). After the user picks a guardrail option (`ask choice --guardrail "A=<kind>"`, then `choice`, at the same checkpoint) or the director signs `director-sign guardrail --kind <kind>`, `ratchet.py guardrail` inserts a checkpoint right after the current one with a `guardrail` object (`kind`, `check_command` with a `{target}` placeholder, `target`, `bad_example`, `why`) and a ready test plan. It refuses a kind that hasn't repeated or that already has a check. Before that checkpoint can complete, Ratchet runs the check on `bad_example` (must fail) and on `target` (must pass), and neither run may change a file. Ratchet also rebuilds every reviewed code version where a blocking finding of that kind was recorded and acted on (the flagged file changed in the next version; a disproved finding doesn't count), using `git archive` of that version with links skipped, the step's own files laid over it and installed dependencies linked in, and runs the check there: it must fail on each. Every case a check was proven on is kept on its `checks` entry (`cases`) and re-run when it is strengthened, so no past case can be dropped; a case that can't be rebuilt fails the proof instead of being skipped, and a check that changes the installed dependencies fails it too. For a high-risk kind (`guardrail.high_risk`: found on a high-risk checkpoint or rated a blocker), the step is high risk, needs ≥3 files in `<variants_dir>/bad` (each must fail) and ≥2 in `<variants_dir>/good` (each must pass), written by `ratchet-variant-writer` before `tests-written`. A failed proof sends the step back to `behavior` (bounded by `max_behavior_attempts`). Then the check (with `{target}` filled in, plus `template`, `target`, `bad_example`, `test_command`, `test_files`, `high_risk`, `variants_dir`) is added to `checks` and recorded in `learnings.md`. When a later review finds a blocker or major finding of a covered kind, Ratchet adds a step with `guardrail.strengthen: true` right after the current one, automatically; it replaces that kind's check once proven against every past case plus the missed one. Every behavior gate runs each `checks` command after the tests; a failing check, or one that changes files, fails the gate. Minor findings of a covered kind are recorded instead of blocking; blocker and major findings always block. `checks` is locked at plan approval like the other rules (removing or editing a check needs the user's yes); a completed guardrail updates the lock itself.

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
 "hook_errors": [],
 "viewer_url": "http://10.0.0.12:4481/?t=…", "viewer_live": true,
 "done": 2, "total": 6, "updated_at": "…",
 "checkpoints": [{"id": "CP1", "title": "…", "risk": "low", "status": "done", "step": "complete",
                  "commit": "a1b2c3d", "gates": {"behavior": {"status": "passed", "attempts": 1},
                  "human": {"status": "passed", "attempts": 1, "by": "human"}, "…": {}}}]}
```

`awaiting_since` is the time the loop began waiting on a human or director (null otherwise). `by` on a gate entry (`human` or `director`) is set when a person's approval passed it. `unavailable_reviewers` maps a skipped reviewer id to `{reason, since, until}` (why it was skipped, when, and when it may be tried again). `waiting_on` is `human`, `director` or `null`. Director-only commands (they refuse unless the session has `RATCHET_ROLE=director` and the hooks recorded a director session in its folder, see `guard_role` in section 10; the guard hook blocks them in every other session): `director-key [--rotate]` (the key lives at `~/.config/ratchet/director.key`, or `RATCHET_DIRECTOR_KEY_FILE`), `director-enroll`, and `director-sign approve-plan|human-approve|feedback`, which prints a token for the lane's current pause. Like every other command, they refuse to run when their per-user folder (`RATCHET_HOME`) or director key file differs from the one the lane's hooks recorded in `.ratchet/hook-env.json` (section 10), so a session that points them at a folder or key of its own making enrolls and signs nothing. Tokens go to `approve-plan` / `human-approve` / `feedback` via `--director-token`.

## 10. Hooks

`ratchet.py hook <stop|prompt|guard> --host auto|claude|codex|gemini|copilot` is the single entry point every agent CLI calls. Written by `install-hooks` (or the plugin's `hooks/hooks.json`):

| host | config file (project / user) | stop | prompt | guard |
|---|---|---|---|---|
| claude | `.claude/settings.json` / `~/.claude/settings.json` | Stop | UserPromptSubmit | PreToolUse |
| codex | `.codex/hooks.json` / `~/.codex/hooks.json` | Stop | UserPromptSubmit | PreToolUse |
| gemini | `.gemini/settings.json` / `~/.gemini/settings.json` | AfterAgent | BeforeAgent | BeforeTool |
| copilot | `.github/hooks/ratchet.json` / `~/.copilot/hooks/ratchet.json` | agentStop | userPromptSubmitted | preToolUse |

**Plain-language output (user-facing; never paste JSON at the user).** `ratchet.py status` (no flag) prints plain text: first line `Step N of M done: <title>` (or `All M steps are done.` / `No plan yet.`), the phase in words, what is happening now, a re-run note when a passed check went out of date, and, when blocked, the reason in quotes plus 2-3 lines `A) ...`, `B) ...`, `C) ...`. `ratchet.py plan-summary` prints, per step: title, first done-when line, `UI review: yes|no`, `Risk:` and `Reviewed by:`, plus a hooks-skipped note when waived; paste it at the plan approval stop. `next` gains `options: [{letter, text}]` (blocked phase only, same choices as the text status) and the warning/doctor text is addressed to the agent. viewer-data.json `next` gains `label` (plain sentence for the current step); `hooks.waived` drives the viewer's hooks-skipped banner. `status --json` and `lanes --json` gain `blocked_reason_plain` (the block reason as a plain sentence; unknown reasons pass through unchanged, null when not blocked; `blocked_reason` stays the raw text), viewer-data.json `state` gains the same `blocked_reason_plain`, and `next` in the blocked phase gains `reason_plain` next to the raw reason in `action`.

Runtime files: `.ratchet/hook-heartbeat.json` (last time each hook fired and via which host; read by `doctor`, `next`, and `status --json` as `hooks_warning` and `hooks`) and `.ratchet/hook-errors.log` (internal errors; hooks fail open; one line per crash, `<iso time> <host> <event>: <ExceptionType>: <message>`; read by `status`, `next` and `doctor` as `hook_errors`), and `.ratchet/hook-crashes/` (a folder of empty marker files named `<host>.<event>.<crash|clean>.<microseconds since 1970>.<unique suffix>`, written only by `run_hook`: a crash creates a `crash` marker; a clean run of that same hook on that same host creates a `clean` marker, stamped with the time that run started, only when it resolves a crash recorded before it began. A crash is unresolved while no `clean` marker of that hook and host is newer than its newest `crash` marker. Each run only creates its own marker with an exclusive create and then deletes markers it supersedes (that hook's older crash markers; for a clean run, also its older clean markers), so no file is ever read, changed and written back, and concurrent hooks cannot erase each other's records on any OS. It does not age out, so a crash older than the 24-hour `hook_errors` window still keeps `enforcing` false until that hook runs cleanly. Other file names are ignored; a missing folder means no crash was recorded. Fail safe: when a crash marker cannot be saved, `run_hook` appends `<iso time> <host> <event>` to `.ratchet/hook-crashes-unsaved.log`, and readers count that line as a `crash` marker at that time (never deleted; a newer `clean` marker resolves it, and a clean run that cannot save its marker resolves nothing). When `hook-crashes/` exists but cannot be listed (not a folder, no permission), or `hook-crashes-unsaved.log` exists but cannot be read, every hook counts as broken (`enforcing` and `prompt_hook` false) and `hooks_warning`, plain `status` and `doctor` say the crash records cannot be read. Fail closed on writes too: a hook counts as working only while Ratchet can save its crash state. Readers prove it with a real write (they create and delete a `.write-probe.*` file in `hook-crashes/`, or in `.ratchet/` before that folder exists); while that fails, every hook counts as broken. Crash state is only ever written as per-run exclusive-create marker files or appended lines; no crash-state path reads, changes and rewrites a shared file, so concurrent hooks cannot erase each other's crashes. `hook-heartbeat.json` is rewritten whole by each hook (to a temp file renamed into place, so no reader ever sees it half-written), but it holds liveness only: a concurrent rewrite can at most roll an entry back to an older time, which can only make a hook look less healthy. When a crash can be saved in neither `hook-crashes/` nor `hook-crashes-unsaved.log`, `run_hook` prints a plain, non-blocking message to the host; while writes are refused, `hooks_warning`, plain `status` and `doctor` say the crash records cannot be saved. Known limit: such a crash leaves no lasting record (at most a `hook-errors.log` line, which counts until that hook runs cleanly again, for at most 24 hours), so once writes work again it no longer counts; hooks count as broken only while writes are refused). Known limit: when `hook-crashes/` is not a real folder (for example a broken symlink) and every fallback write (`hook-crashes-unsaved.log` and `hook-errors.log`) also fails at that moment, that crash may go unrecorded entirely; this is an accepted limit, not a bug.

**`.ratchet/hook-env.json`** (written only by `run_hook`, before it decides anything; the guard refuses agent writes to it): `{"home": "<resolved RATCHET_HOME>", "key_file": "<resolved director key file>", "guard_role": "director" | "builder", "at": "<iso>", "host": "<host>"}`. A hook runs with the agent CLI's own environment, not with a shell the agent controls, so this is the per-user folder (where the integrity hashes, enrollments and director key live) and the key file Ratchet trusts for the project. Ratchet does not check who called its hook command, though: a script deliberately written to cheat, running as the same OS user, can run `ratchet.py hook guard` itself with made-up input and environment and so forge this record (and `director-folders.json`). That is an accepted limit (see the known limit under "Variables a builder session may read but never set"): these records stop an agent from stumbling past a gate or taking an easy shortcut, not a deliberate same-user forgery. `guard_role` is written by the guard hook only (it runs right before every command any session runs): `director` when the agent CLI that last ran a command here was started with `RATCHET_ROLE=director`, `builder` for any other session; absent until a guard has run here. No hook reads this file, changes it and writes it back, so concurrent hooks never drop each other's newer values: each hook run first saves its own record in `.ratchet/hook-env/`, a file named `hook-env.json.<guard|env>.<start time in microseconds>.<director|builder|->.<pid>-<random>` (the role only for a guard run) holding `{"home", "key_file", "at", "host"}`, renamed into place whole; it removes the records its own supersedes (a guard run every older record, any other run older non-guard records). Then it makes `hook-env.json` agree with the records, rewriting it in one rename only when `home`, `key_file` or `guard_role` differ, and checks again after each rewrite: `home` and `key_file` come from the newest record, `guard_role` from the newest guard record (newest by when the hook run started; when a director and a builder guard record started in the same microsecond, the builder's counts as the newer one, so a tie never favours the director), so a hook that started first but finished last cannot restore an older role or folder. The director check reads the role from the newest guard record directly whenever `hook-env.json` exists (falling back to the file's `guard_role` when there are no records); a folder of records that can't be listed counts as an unreadable record. A hook that can't save its record, or can't get `hook-env.json` to agree, counts as a failed write (below). Every other `ratchet.py` command (and every integrity check of `state.json` or `human_log.jsonl`) refuses with a plain message when its own `RATCHET_HOME` or `RATCHET_DIRECTOR_KEY_FILE` resolves to something else, instead of silently using a folder that holds no integrity record or a key of an agent's own making. The director commands (`director-key`, `director-enroll`, `director-sign`) also need the hooks to agree on the role, because `RATCHET_ROLE=director` in a process's environment proves nothing on its own (a builder could set it inside a script the guard never sees). They run only when the session's own folder (the working directory, not `--root`) is a Ratchet project whose `guard_role` is `director`, or a folder with no `.ratchet/` that a director session's guard recorded in the per-user `director-folders.json` (`{"<resolved folder>": "<iso first seen>"}`, written by the guard only). Otherwise, when the own folder's record, or failing that the target project's, shows `builder` (or cannot be read), they refuse with a plain message. A missing record is never read as permission: when neither place has a director record and Ratchet's hooks are in use, the commands refuse and say to restart the director's agent CLI so its hooks record the role. "In use" means installed for any agent CLI (user scope, or the own folder's or the lane's project config; any CLI, not only the detected one, because host detection reads variables a script can set), or delivered without that config (a plugin) and seen enforcing: a guard or stop hook has run in the own folder's project or the lane, or a hook failure there is still unresolved. The one exception is a setup where Ratchet's hooks are not in use at all: then nothing records or enforces anything, `RATCHET_ROLE=director` is taken at its word, and the command says so on stderr. So the director's agent CLI needs Ratchet's hooks and its own folder, and reaches lanes with `--root <lane>`. A hook that cannot save this record (or `director-folders.json`) does not continue silently: it records the failure like a hook crash (a marker in `hook-crashes/`, or the `hook-crashes-unsaved.log` fallback, plus a `HookRecordError` line in `hook-errors.log`), so the hook counts as broken until a later run saves the record, and it says so in its output; its decision about the command itself still stands. For the folder check, a missing file means no hook has run here yet, and nothing is compared, unless the hooks are in use and a hook may have failed to save it (an unresolved hook crash, or crash state Ratchet can't read or save): then every command refuses with a plain message until a hook writes it again. An existing record is not trusted either while a hook failure in that project is unresolved (a crash, or a record a hook could not save: a failed write leaves the old record, which may still name a director) or while Ratchet can't read or save its crash state. Then the folder check refuses every command, and the director commands refuse when this holds for the session's own folder or the target project, until that hook runs cleanly. `status`, `next`, `doctor`, `view` and `install-hooks` only read or repair hooks, so they still run in every one of these cases and say on stderr what is wrong. The reads exit 1 when the record is missing, unreadable or names another folder. A record without `home` or `key_file`, or an unreadable one, is refused with a message to delete it (the next hook run writes it again). To move the folder on purpose, restart the agent CLI with the new `RATCHET_HOME`, so its hooks record it.

**Variables a builder session may read but never set.** `RATCHET_HUMAN_OVERRIDE`, `RATCHET_ROLE`, `RATCHET_HOME` and `RATCHET_DIRECTOR_KEY_FILE`. Besides a literal `VAR=`, the guard refuses shell commands that set one through `export`, `declare`/`typeset`/`local`/`readonly`, `setenv` or fish `set -x`, a dict or keyword entry (`os.environ[...] = `, `env={...}`, `dict(os.environ, VAR=...)`), `putenv`-style calls, or indirectly (the name kept in another variable or built from pieces, then used by `eval`, `env -S`, `printf -v`, `${!v}`, sourcing a file, or an `os.environ` write). Plain reads (`$VAR`, `printenv VAR`, `grep VAR` in docs, `os.environ.get`) stay allowed. This is best-effort: a script written to a file first and run later is not seen by the guard, which is why the engine also distrusts each of these values itself: `RATCHET_HOME` and `RATCHET_DIRECTOR_KEY_FILE` must match what the hooks recorded (the folder check above), `RATCHET_ROLE=director` counts only where the guard recorded a director session (`guard_role` and `director-folders.json` above), and `RATCHET_HUMAN_OVERRIDE` needs the terminal code below. Known limit: a script that changes into the director's own folder before running a director command passes the role check, and a script running as the same OS user can edit Ratchet's protected files, read the director key file directly, or forge Ratchet's hook records (`hook-env.json`, `director-folders.json`, the crash markers) by running Ratchet's own hook command with made-up input; no check inside Ratchet can stop that. This is an accepted limit (the user's decision): Ratchet's protections stop an agent from stumbling past a gate or taking an easy shortcut, not a deliberate same-user forgery. The guard blocks the direct routes (writes under `.ratchet/`, any command naming the key file or the per-user folder), and keeping lanes and the director in separate folders keeps the role check meaningful.

**`RATCHET_HUMAN_OVERRIDE=1`** is for a person at their own terminal: it never approves anything on its own. The command (`human-approve`, `approve-plan` and the other approvals, `accept-state`, `init --force` over an active run, `choice` without transcript checks) opens the real terminal (`/dev/tty`; `CONIN$`/`CONOUT$` on Windows), prints a six-digit code there and reads it back, in the same process. No terminal (an agent's shell tool runs on pipes), a wrong code, end of input or no answer within 5 minutes (on every OS; on Windows the console is read on a separate thread that is given up after that time) means refused with a plain message, and nothing is recorded. A confirmed override appends `{"at", "by": "human", "override": true, "action", "via"}` to `human_log.jsonl`; such lines are audit records only and never count as a chat reply. The line is written before anything else changes: when it can't be written (the log or `.ratchet/` not writable, a full disk), the command refuses with a plain message and nothing is approved, accepted as a baseline, replaced or recorded as a choice, so an override is never used without its audit line (the line is written by the override check itself, so no command can use the override without it). Known limit: an agent that can start its own pseudo-terminal (pty) and read the code from it could still answer; the guard refusing commands that set the variable covers the usual route, and the record shows every use.

**`hooks` object** (in `status --json`, in `next`, and as the only key of `doctor --json`; read-only, computed for the agent CLI running the command):

| key | type | meaning |
|---|---|---|
| `host` | string | `claude`, `codex`, `gemini`, `copilot` or `unknown` (from `host_provider` / environment) |
| `installed` | bool | a project or user hook config for `host` contains Ratchet hooks |
| `heartbeat_seen` | bool | the heartbeat has an entry from `host` (any host if `host` is `unknown`) |
| `fresh` | bool | such an entry has an `at` timestamp within the last 24 hours and not more than 5 minutes in the future |
| `enforcing` | bool | the guard or stop heartbeat from `host` is fresh, and neither of those hooks broke on its last run. A recent `hook_errors` entry for that event and host, logged at or after its heartbeat (or with no heartbeat for that event), means it crashed, even when the heartbeat is fresh; so does an unresolved crash in `hook-crashes/`, whatever its age. Crashes are judged per (host, event): only a heartbeat or clean run from the host that crashed clears that crash, and with `host` `unknown` any host's unresolved guard or stop crash keeps this false. The next clean run of that hook on that host makes it count again |
| `prompt_hook` | bool | the prompt heartbeat from `host` is fresh and the prompt hook did not break on its last run (it only records the user's replies; it enforces nothing) |
| `missing` | bool | `not installed and not enforcing` |
| `waived` | bool | (`status --json`, `next`, viewer-data `hooks`, plain `status`; not `doctor`) the user chose to build without hooks (`ask hooks-waive`, then `hooks-waive` after their reply, which must be one of a fixed allow-list of full phrases, `HOOKS_WAIVE_PHRASES` in ratchet.py, after lowercasing and dropping punctuation and a trailing \"please\": \"yes go ahead without hooks\", \"yes skip the hooks\", \"yes build without hooks\", \"go ahead without hooks\", \"skip the hooks\", \"proceed without hooks\"; anything else is refused and clears the question; stored in state.json as `hooks_waived {at, by, prompt}`, with a `hooks_waived` history event). True only while no enforcement hook is installed or seen firing: the guard and stop hooks decide, not the prompt hook (which is how the yes is recorded) and not `missing`. Once they are installed or fire, the next state save removes `hooks_waived` (history event `hooks_waive_cleared`), so it cannot resurface later without new consent. `hooks-waive` refuses to record anything while they are active. Never blocks anything |

`hooks_warning` (string or null) is null when `enforcing`. Otherwise it tells the agent that a safety hook broke on its last run (run `doctor`, fix the cause, tell the user), that the hooks are installed but silent (restart the CLI), or that none fired yet (offer to run `install-hooks`, ask the user first).

**`hook_errors`** (list, in `status --json` and `next`; `[]` when there are none): hook crashes from `hook-errors.log` in the last 24 hours (and not more than 5 minutes in the future), from the same `host` as the `hooks` object (every host when it is `unknown`), newest first, at most 3. Each entry is `{event, host, at, message}`: `event` is `guard`, `stop` or `prompt`, `at` is the ISO time the crash was logged, and `message` is the logged `ExceptionType: message`, cut to 300 characters (for the agent; never pasted at the user). There is nothing to dismiss: entries age out after 24 hours. Plain `status` turns them into two plain lines (which check failed, how long ago, whether it has run cleanly since, and to ask the agent to run the doctor check), never the raw error text; `doctor` lists them with the raw text. A corrupt or unreadable log, or any line not in the format above, counts as no error. Every other hook that is still unresolved for `host` (in `hook-crashes/` whatever its age, or in the log with no clean run since) gets its own plain line in `status`, naming that hook and its own crash time and consequence, so a newer resolved error never stands in for an older unresolved one (when the list is empty, these are the only lines, followed by asking the agent to run the doctor check); `doctor` lists those crashes.

A corrupt or odd `hook-heartbeat.json` or `config.json` counts as empty here; `doctor` never raises for it.

## 11. Building from a spec tool (OpenSpec, Spec Kit, BMAD)

`ratchet.py spec detect [--json]` lists open items: OpenSpec changes (`openspec/changes/<id>/`, not `archive/`), Spec Kit features (`specs/<NNN-name>/` with `.specify/`; the current one from `SPECIFY_FEATURE_DIRECTORY`, `.specify/feature.json` or `SPECIFY_FEATURE`), BMAD stories (v4: `devStoryLocation` in `bmad-core/core-config.yaml`, default `docs/stories/{epic}.{story}.*.md`; v6: `_bmad-output/implementation-artifacts/{epic}-{story}-*.md` with `sprint-status.yaml`), skipping finished ones.

`ratchet.py spec import <openspec|speckit|bmad|auto> <id…> [--force]` (while planning) writes `reference/spec-source.json` (`{"items": [{"tool", "id", "path"}], "imported_at"}`), a draft `checkpoints.json` and `tests/CPn.json` (unless a plan exists; `--force` replaces it), and adds the tool's standards files to config `standards` (OpenSpec `openspec/config.yaml` / `project.md`, Spec Kit `.specify/memory/constitution.md`, BMAD `docs/architecture/coding-standards.md` or `docs/architecture.md`). `auto` picks the only open item (or the only one matching the ids given).

Refs (stable ids the plan points at):

| Tool | Scenario / criterion (needs a test) | Requirement | Task (needs a step) |
|---|---|---|---|
| OpenSpec | `<change>#req:<capability>/<requirement-slug>/sc:<scenario-slug>` (each `#### Scenario:` under ADDED/MODIFIED) | `<change>#req:<cap>/<slug>` | `<change>#task:1.1` |
| Spec Kit | `<feature>#US1/AS2` (user story acceptance scenarios); `<feature>#FR-001` (a functional requirement is tested itself) | `<feature>#US1` | `<feature>#T012` |
| BMAD | `<story>#AC2` | same | `<story>#task:1` (subtasks `#task:1.2` follow their task) |

Draft plan: one checkpoint per OpenSpec task group (each requirement goes with the group that talks about the same things), per Spec Kit user story (setup tasks in the first, FRs in the last), per BMAD story. Each checkpoint gets `spec_refs` and a `spec` block (`source`, `requirements` with their scenarios) that reviewers see. The draft has no test command, so the planner must refine it before `plan-submit`.

Locking: the spec's requirements, scenarios and task list (not task ticks or status) are part of the plan approval; changing them mid-build stops the run like any other locked file.

Write-back: when a checkpoint completes, Ratchet ticks its `spec_refs` tasks (`- [ ]` → `- [x]`; a BMAD task ticks its subtasks) and commits those files with the checkpoint. When the last checkpoint touching a BMAD story completes, the story's status becomes `Done` (v4) / `done` (v6, also in `sprint-status.yaml`) and a `### Ratchet verification` note (steps, commits, the test proving each acceptance criterion) goes under `## QA Results`. When the run is approved (or finishes in `none` mode), each OpenSpec change whose tasks are all ticked is archived with `openspec archive <id> -y` and committed on its own; without the CLI the change stays open and Ratchet says so. Config `spec_archive: false` turns archiving off.

No spec tool: when a run is approved in a project where `spec detect` finds nothing and config `spec_keep` is unset, `human-approve` (or the last `complete` in `none` mode) prints an `Offer:` line, and the agent asks the user once. `ratchet.py spec keep on|off` records the answer in config `spec_keep`. With it on, every approved run writes `openspec/specs/<run slug>/spec.md` in OpenSpec's format (a `### Requirement:` per completed step with `The system SHALL…` and its done-when list, a `#### Scenario:` per planned test with GIVEN/WHEN/THEN and a `<!-- proven by test CPn-Tnn in <file>, commit <id> -->` note; guardrail steps are left out) and commits it with the config on its own. Answering `on` after a finished run records that run straight away.

`ratchet.py spec status [--json]`: per scenario, its tests, steps and state (`not planned`, `planned`, `proven` once every step covering it is done, `left out: <reason>`), and which tasks are ticked. `viewer-data.json` has the same under `spec`.

