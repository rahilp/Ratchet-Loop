# Test speed (Lane T) — design

Date: 2026-11-27  
Status: approved; implemented  
Approach: execution-first (same gates; make required runs finish sooner)

## Who this is for

Ratchet is run by **AI agents** and an **AI harness**. People use the product through that harness. Lane T speeds the **test paths those agents already run** (`red`, `behavior`, `complete`, and project `related_test_command` / `regression_command`), without teaching anyone to open a terminal or changing the gate/checkpoint model.

## Context

After Lane S (engine split) and Lane D (director redesign), the product contract is stable. What still hurts:

- **Product day-to-day:** every project pays for `_run_cmd`, suite watch, load-stretch polling, related-then-full at `complete`, and automatic re-runs. Wall-clock on those paths is agent/harness latency.
- **This repo:** full `tests/run_all.py` is ~60 minutes with default workers; a few suites dominate (`test_merge_main`, `test_hooks_waiver`, `test_feedback_fixes`, `test_ui_gate_fidelity_docs`, …).

Lane T does **not** reopen director identity, tokens, or parallel ownership. It does not replace Lane U (measured UI gate).

## Goals (plain language)

1. **Keep Ratchet’s USP.** Gates, checkpoints, and required testing behavior stay the same: same classes of commands, same pass/fail/timeout/edit-under-test outcomes.
2. **Speed product loops first.** Make `red` / `behavior` / `complete` (related + regression + re-runs) finish sooner for any project using Ratchet.
3. **Speed this repo’s full suite second.** Shorter `run_all` wall-clock without dropping checks.

## Non-goals

- Skipping or deferring full regression when related tests passed.
- Shrinking default coverage or softening flaky-re-run / stop-on-edit policy.
- Changing checkpoint, review, or director semantics.
- Lane U (measured UI gate).
- Extracting a new test framework or rewriting the suite inventory for its own sake.

## Approach

**Mostly execution-only:** same gates and commands; make required runs finish sooner. Light scheduling wins only when they clearly do not weaken coverage (e.g. nested suite re-entry already covered by `RATCHET_RUN_ALL` — not “related replaces full”).

Rejected as primary:

- **Scheduling-first** (change when heavy work runs) — highest risk to USP.
- **Phased both with equal weight** — still useful as a secondary phase for this repo, but product execution wins come first.

## Architecture

Lane T stays inside existing test plumbing; no new gate types.

| Surface | Role |
|---------|------|
| Engine runner (`_run_cmd`, suite watch / fingerprint looks, load-stretch polling) | Every project’s `red` / `behavior` / `complete` path |
| Project runners via config | Unchanged commands (`regression_command`, `related_test_command`); engine waits/watch get cheaper for everyone |
| This repo `tests/run_all.py` + heavy suites | Phase T2 only: workers/ordering and suite internals without dropping assertions |

**Levers**

1. **Less idle wait** — suite-watch / load-stretch polls that do not burn wall-clock when the command is already done or the tree is unchanged.
2. **Cheaper tree checks** — reuse the watch’s private index; avoid redundant full fingerprints mid-command; **same** stop-on-edit rules.
3. **Process/runner efficiency** — `run_all` worker defaults and suite ordering on real machines; cut double work *inside* a required run (nested re-entry), not by skipping gates.
4. **Measure** — before/after timings for a product loop and for full `run_all`; recorded in PR notes. No hard CI gate on a made-up latency number beyond “noticeably faster, same outcomes.”

**Invariant:** every gate still runs the same classes of commands and treats pass/fail/timeout/edit-under-test the same way.

## Phases

### Phase T1 — product loops (primary)

Ship engine-side runner/watch improvements that every project feels on `red` / `behavior` / `complete`.

Prove with before/after timings on a throwaway project path and on Ratchet’s own related/`complete` path. Outcomes (pass/fail/timeout/edit-under-test) stay identical on the suites that pin those behaviors.

### Phase T2 — this-repo `run_all` (secondary)

After T1: raise effective parallelism where safe; trim known multi-minute suites without dropping checks (fixture reuse, less nested re-entry, same assertions). Target: clearly shorter full `run_all` wall-clock on a typical cloud box; still `N passed, 0 failed`.

## Testing & proof

- Keep/extend suites that pin `_run_cmd`, suite watch (edit under test → stop, no false pass), load-stretch, related-then-full, and flaky re-run — same exits and evidence shape.
- Timing harness (informational): before/after for a small product loop and for full `run_all`; in PR notes, not a hard CI gate.
- Adversarial brief: find any change that skips, softens, or reorders a required gate.

## Risks

| Risk | Mitigation |
|------|------------|
| Faster watch misses an edit | Same fingerprint rules; existing stop-on-change suites must stay green |
| More `run_all` workers → flakes/OOM | Cap by load (existing stretch logic); raise carefully; flake suites stay real |
| “Optimization” drops a nested check | Diff assertions/ids; no silent suite shrink in T2 |
| Docs drift from behavior | Update schemas/how-it-works only when internals change in user-visible timing/watch wording |

## Implementation notes (for the plan)

- Stay in `ratchet_engine.py` / existing modules (`fingerprint.py`, etc.) and `tests/run_all.py` unless a tiny helper is clearly justified.
- Prefer failing-first guard checks for any watch/runner invariant that T might break.
- Commits authored as Rahil Pirani (same as Lanes S/D).
- AI/harness-only: no “user opens a terminal” wording in new docs.

## Done when

- Gate/checkpoint/testing contract unchanged (docs/schemas only if wording must match faster internals).
- Product paths measurably faster; full suite faster as a second win.
- Focused + full suite green; adversarial review clean on “did we weaken a gate?”
- Design status → `approved; implemented` after T1+T2 land.

## Relationship to other lanes

| Lane | Relation |
|------|----------|
| S | Foundation (launcher + engine). T must keep emitted paths naming `ratchet.py`. |
| D | Done. T does not change director/ownership. |
| U | Stays last; not in T. |
