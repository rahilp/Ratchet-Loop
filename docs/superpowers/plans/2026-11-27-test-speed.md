# Test Speed (Lane T) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make required Ratchet test paths (`red` / `behavior` / `complete` and this repo’s `run_all`) finish sooner without changing gate, checkpoint, or testing outcomes.

**Architecture:** Execution-first. Keep every gate’s commands and pass/fail/timeout/edit-under-test rules. Cut idle wakeups in `_wait_stretched` / `run_all` polling, keep suite-watch fingerprint rules identical, then (Phase T2) raise safe `run_all` parallelism and trim this-repo heavy suites without dropping checks.

**Tech Stack:** Python 3 stdlib; `ratchet_engine.py` (`_Watch`, `_wait_stretched`, `_run_cmd`); `tests/run_all.py`; existing suites `test_stop_on_change.py`, `test_load_timeouts.py`, `test_stretched_timeout_cleanup.py`.

## Global Constraints

- Preserve USP: gates, checkpoints, required testing behavior unchanged.
- No related-replaces-full; no coverage cuts; no soft flaky-re-run / stop-on-edit policy.
- Emitted runnable paths still name `ratchet.py` (Lane S).
- AI/harness-only copy; commits as Rahil Pirani (`rahil@piranifam.com`).
- Priority: product loops (T1) first; this-repo `run_all` wall-clock (T2) second.
- Timings are informational (PR notes), not a hard CI gate.

## File map

| File | Responsibility |
|------|----------------|
| `tests/test_test_speed.py` | Lane T guard: wait/poll wakeups, stretch=1 single-wait, watch outcomes unchanged |
| `skills/ratchet-orchestrator/scripts/ratchet_engine.py` | `_wait_stretched` wake scheduling; optional stretch skip when `max_stretch==1` |
| `tests/run_all.py` | Matching poll wake scheduling; careful worker default / load cap |
| `tests/test_stop_on_change.py` / `tests/test_load_timeouts.py` | Must stay green (behavior pins) |
| Heavy suites (T2) | Fixture/nested-reentry trim only where assertions stay |
| `docs/superpowers/specs/2026-11-27-test-speed-design.md` | Status → approved / implemented |
| schemas/how-it-works | Only if user-visible watch/timeout wording must match |

---

### Task 1: Guard suite for wait/poll wakeups (failing first)

**Files:**
- Create: `tests/test_test_speed.py`
- Test: `python3 tests/test_test_speed.py`

**Interfaces:**
- Consumes: `_wait_stretched` / `_Watch` behavior via engine (in-process or subprocess as other trust suites do)
- Produces: failing checks that pin (a) fewer wakeups when `load_stretch_max=1`, (b) stop-on-edit still fires, (c) timeout still fires

- [ ] **Step 1: Write the failing guard suite**

Create `tests/test_test_speed.py` that:

1. Loads the engine the same way `trust_helpers` / `test_gates` do (`spec_from_file_location` on `ratchet_engine.py`).
2. Pins **T-T01**: with `max_stretch=1.0` and no watch, a mock process that exits after ~0.05s must not call stretch/load more than a small bound (e.g. ≤2), and wall time for the wait must be ≪ `N * RUN_POLL_SECONDS` for a long timeout (prove we are not polling every 0.25s for the whole timeout).
3. Pins **T-T02**: with a watch `every=30` and `max_stretch=1.0`, while the process is still running for ~1s, the wait loop must not spin at 0.25s forever — wake count bounded by ~deadline/watch schedule, not `1/0.25`.
4. Pins **T-T03**: existing stop-on-edit contract still holds via a thin call into the real `_Watch.changed` / a tiny `behavior` fixture (or re-use helpers from `test_stop_on_change`) — edit under test still stops; unchanged tree does not.
5. Pins **T-T04**: timeout still kills the group when the deadline passes (`max_stretch=1`).

Use `check()` / PASS/FAIL tally like other suites. Do **not** assert a specific absolute wall-clock for CI flakiness; assert wakeup/call counts and correctness.

- [ ] **Step 2: Run guard — expect fail on T-T01/T-T02 against current polling**

Run: `python3 tests/test_test_speed.py`  
Expected: T-T01 and/or T-T02 FAIL (current `_wait_stretched` polls every `RUN_POLL_SECONDS=0.25`); T-T03/T-T04 may already PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_test_speed.py
git -c user.name="Rahil Pirani" -c user.email="rahil@piranifam.com" commit -m "test: Lane T guard suite for wait/poll wakeups (failing first)"
```

---

### Task 2: Smarter `_wait_stretched` wake schedule (engine)

**Files:**
- Modify: `skills/ratchet-orchestrator/scripts/ratchet_engine.py` (`_wait_stretched`, ~8607–8668; keep `RUN_POLL_SECONDS` as the stretch re-read interval when stretch can grow)
- Test: `tests/test_test_speed.py`, `tests/test_stop_on_change.py`, `tests/test_load_timeouts.py`

**Interfaces:**
- Consumes: `proc`, `timeout`, `max_stretch`, optional `_Watch`
- Produces: same return tuple `(timed_out, limit, note, changed)`; same kill/stop/final-look semantics

- [ ] **Step 1: Implement wake scheduling**

In `_wait_stretched`:

1. If `max_stretch <= 1.0` (no stretch growth possible): do **not** call `_stretch_factor` every loop. Keep `limit = float(timeout)`. Wake only for: process exit, watch look (`next_look`), or deadline.
2. If `max_stretch > 1.0`: keep stretch re-reads, but compute `wait_for = min(RUN_POLL_SECONDS, limit - elapsed, time_until_next_look_or_inf)` so a 30s watch does not force a wake every 0.25s solely for stretch when the next useful event is later — still re-read stretch at least every `RUN_POLL_SECONDS` while stretch can grow (load spikes must still extend the deadline).
3. Preserve: peak limit never shrinks; final look when command ends without timeout; no final look on timeout kill; `_stop_group` / `_kill_group` paths unchanged.

Sketch (illustrative — match existing style/comments):

```python
def _wait_stretched(proc, timeout, max_stretch, watch=None):
    t0 = time.monotonic()
    peak = None
    timed_out = False
    limit = float(timeout)
    next_look = t0 + watch.every if watch else None
    changed = []
    can_stretch = float(max_stretch) > 1.0
    while True:
        if can_stretch:
            factor, load, cpus = _stretch_factor(max_stretch)
            if timeout * factor > limit:
                limit = timeout * factor
                peak = (factor, load, cpus)
        now = time.monotonic()
        elapsed = now - t0
        if elapsed >= limit:
            timed_out = True
            _kill_group(proc)
            break
        if watch and now >= next_look:
            changed = watch.changed()
            if changed:
                watch.found_at_end = False
                _stop_group(proc)
                break
            next_look = time.monotonic() + watch.every
        # How long until the next useful event:
        until_deadline = limit - (time.monotonic() - t0)
        until_look = (next_look - time.monotonic()) if watch else float("inf")
        if can_stretch:
            slice_ = min(RUN_POLL_SECONDS, until_deadline, until_look)
        else:
            slice_ = min(until_deadline, until_look)
        slice_ = max(0.0, slice_)
        try:
            proc.wait(timeout=slice_ if slice_ > 0 else 0.0)
            break
        except subprocess.TimeoutExpired:
            continue
    # ... unchanged final-look + note logic ...
```

- [ ] **Step 2: Re-run guards**

Run:

```bash
python3 tests/test_test_speed.py
python3 tests/test_stop_on_change.py
python3 tests/test_load_timeouts.py
```

Expected: all PASS (or only known flakes with solo re-run green).

- [ ] **Step 3: Commit**

```bash
git add skills/ratchet-orchestrator/scripts/ratchet_engine.py tests/test_test_speed.py
git -c user.name="Rahil Pirani" -c user.email="rahil@piranifam.com" commit -m "perf(engine): wake _wait_stretched on events, not fixed 0.25s spin"
```

---

### Task 3: Align `tests/run_all.py` suite wait polling

**Files:**
- Modify: `tests/run_all.py` (`run_with_stretched_timeout`, `Runner.wait` / equivalent poll loop; `POLL_SECONDS`)
- Test: `tests/test_runner_load.py`, `tests/test_load_timeouts_fixes.py`, `tests/test_stretched_timeout_cleanup.py`, `tests/test_test_speed.py` (add a small pin if run_all exposes a testable helper)

**Interfaces:**
- Same stretch semantics as today when `max_stretch > 1`; when `max_stretch == 1`, wait without 0.25s busy polling for the whole timeout

- [ ] **Step 1: Mirror engine wake logic in run_all**

Apply the same event-based `communicate`/`wait` slice: if stretch cannot grow, wait until deadline (or process exit); if it can, re-read at most every `POLL_SECONDS` but do not wake more often than needed for the deadline.

- [ ] **Step 2: Run runner/load suites**

```bash
python3 tests/test_runner_load.py
python3 tests/test_runner_load_fixes.py
python3 tests/test_stretched_timeout_cleanup.py
python3 tests/test_load_timeouts_fixes.py
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/run_all.py tests/test_test_speed.py
git -c user.name="Rahil Pirani" -c user.email="rahil@piranifam.com" commit -m "perf(run_all): event-based stretch wait when max-stretch allows"
```

---

### Task 4: Product-loop before/after measurement (informational)

**Files:**
- Create or extend: `tests/test_test_speed.py` (optional timing helper) and/or a short note in the PR body
- No production behavior change required beyond T1 already landed

**Interfaces:**
- Consumes: throwaway project via existing helpers (`test_gates` / trust helpers); `behavior` or `complete` with a short sleep fake command and `suite_watch_seconds` large enough that watch looks are rare

- [ ] **Step 1: Record a small product-loop timing**

Script (inline in the suite or a one-off command in the PR notes):

1. Init throwaway project with a regression/behavior command that sleeps ~2s then exits 0.
2. Set `suite_watch_seconds=30`, `load_stretch_max=1`, `test_timeout_sec=60`.
3. Time one `behavior` (or equivalent `_run_cmd` path) wall-clock.
4. Print before/after in suite output or PR body (informational only).

- [ ] **Step 2: Commit any harness used for the measurement**

```bash
git add tests/test_test_speed.py
git -c user.name="Rahil Pirani" -c user.email="rahil@piranifam.com" commit -m "test: informational product-loop timing for Lane T"
```

---

### Task 5: Phase T2 — `run_all` workers under low load

**Files:**
- Modify: `tests/run_all.py` (`default_workers`, load cap block ~609–628)
- Test: `tests/test_runner_load.py` / fixes (worker floor/cap pins)

**Interfaces:**
- Default remains safe: never raise workers above a documented formula; load still caps downward

- [ ] **Step 1: Choose a conservative default**

Options (pick one in code + test + docs docstring of `run_all`):

- Keep `max(2, cpus // 2)` but allow `--workers` docs to show measured sweet spot; **or**
- Raise idle default to `max(2, (cpus * 3) // 4)` only when load/cpus ≤ 1.0 at start, else keep half.

Prefer the second only if `test_runner_load*` can pin the idle vs busy behavior with `RATCHET_FAKE_LOADAVG` / `RATCHET_FAKE_CPUS`.

- [ ] **Step 2: Tests + run**

```bash
python3 tests/test_runner_load.py
python3 tests/test_runner_load_fixes.py
python3 tests/test_runner_load_concurrency.py
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/run_all.py tests/test_runner_load*.py
git -c user.name="Rahil Pirani" -c user.email="rahil@piranifam.com" commit -m "perf(run_all): safer higher worker default when machine is idle"
```

---

### Task 6: Phase T2 — trim top heavy suites without dropping checks

**Files:**
- Modify as needed (examples from last green run): `tests/test_merge_main.py`, `tests/test_hooks_waiver.py`, `tests/test_feedback_fixes.py`, `tests/test_ui_gate_fidelity_docs.py`
- Only when a clear nested re-entry / redundant subprocess / repeated fixture init is proven

**Interfaces:**
- Same check ids and messages; use `RATCHET_RUN_ALL` dedupe where nested suites re-run covered children

- [ ] **Step 1: Profile top offenders**

```bash
python3 tests/run_all.py tests/test_merge_main.py tests/test_hooks_waiver.py tests/test_feedback_fixes.py tests/test_ui_gate_fidelity_docs.py
```

Note wall times. For each, find nested `run_all` / duplicate inits / sleep pads.

- [ ] **Step 2: One suite at a time — optimize + keep check count**

For each suite changed:

1. Record `N passed` before.
2. Apply minimal change (dedupe nested runs, reuse fixture, remove accidental sleeps).
3. Re-run solo; `N passed` must not drop; no check id removed.

- [ ] **Step 3: Commit per suite (or small batch)**

```bash
git add tests/test_<name>.py
git -c user.name="Rahil Pirani" -c user.email="rahil@piranifam.com" commit -m "perf(tests): speed <name> without dropping checks"
```

---

### Task 7: Full suite + adversarial + design status

**Files:**
- Modify: `docs/superpowers/specs/2026-11-27-test-speed-design.md` (status → `approved; implemented`)
- Modify: this plan checkboxes
- Docs/schemas only if watch/timeout wording must change

- [ ] **Step 1: Run full suite**

```bash
PYTHONUNBUFFERED=1 python3 tests/run_all.py
```

Expected: `N passed, 0 failed (suites)`.

- [ ] **Step 2: Adversarial review**

Dispatch a different model. Brief: find any change that skips, softens, or reorders a required gate; misses an edit under test; or lengthens timeouts in a way that hides failures.

Fix Critical/Important; re-run focused suites; re-review until clean.

- [ ] **Step 3: Mark design implemented; final commit; push; update PR**

```bash
git add docs/superpowers/specs/2026-11-27-test-speed-design.md docs/superpowers/plans/2026-11-27-test-speed.md
git -c user.name="Rahil Pirani" -c user.email="rahil@piranifam.com" commit -m "docs: Lane T approved; implemented — suite green"
git push -u origin HEAD
```

---

## Spec coverage (self-review)

| Spec item | Task |
|-----------|------|
| Execution-first / same gates | Global + Tasks 1–3 |
| Product loops first | Tasks 1–4 |
| This-repo `run_all` second | Tasks 5–6 |
| Measure informational | Task 4 |
| Stop-on-edit / timeout pins | Tasks 1–2 + existing suites |
| Adversarial “weaken a gate?” | Task 7 |
| No related-replaces-full | Global (no task adds it) |
| Lane S launcher invariant | Global |

## Placeholder scan

None intentional. Task 6 names offenders from the last green run; exact edits depend on profiling in Step 1 of that task (allowed: discovery then minimal code).
