#!/usr/bin/env python3
"""
CP5 fix-round tests (round 1): `director-sign <action> ... --apply` through a pause, and a deterministic lock wait.
    python3 tests/test_director_apply_extra.py

A lane that paused itself for the director (`pause --reason "director decision needed: ..."`, as the orchestrator
skill tells it to) takes accept-finding and raise-limit with --apply as well as approve-changes and choice: the
request is judged against the phase it paused from, applied there, and the lane is parked again afterwards, so
`resume` carries on where the approval left it (CP5 round-1 finding codex-correctness/X1). A paused lane with no
such underlying state is still refused plainly, with state.json byte-identical. `pause` keeps a blocked run's own
reason across the pause (`paused_block_reason`), so the block reads the same after `resume` as before.

The lock-contention case (codex-standards/X1) is deterministic: the child's lock-wait loop is observed through a
sitecustomize module that marks the first `time.sleep` called from the engine's `_lock_pause`, so the held lock is
released only once the child is known to be waiting on it, with a bounded wait and the child always cleaned up.

Fixtures reuse tests/test_director_apply.py (planned, read-only here), tests/test_director_sign_more.py,
tests/test_limits.py, tests/test_accept_finding.py and tests/trust_helpers.py. No real reviewer CLI is called.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                              # noqa: E402,F401
import test_gates as g                   # noqa: E402
import test_limits as tl                 # noqa: E402
import test_accept_finding as taf        # noqa: E402
import trust_helpers as th               # noqa: E402
import test_director_sign_more as m      # noqa: E402
import test_director_apply as ta         # noqa: E402

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP5-[A-Z]\d+[a-z]?)\]")

REASON = ta.REASON
ACCEPT_REASON = ta.ACCEPT_REASON
PAUSE_REASON = "director decision needed: review findings"

SITECUSTOMIZE = '''\
import os, sys, time
_mark = os.environ.get("RATCHET_TEST_LOCKWAIT_MARK")
if _mark:
    _real_sleep = time.sleep

    def sleep(secs):
        # the engine's state-lock wait loop pauses through _lock_pause(): the first such pause is the signal
        if sys._getframe(1).f_code.co_name == "_lock_pause" and not os.path.exists(_mark):
            with open(_mark, "w") as f:
                f.write(str(os.getpid()))
        return _real_sleep(secs)
    time.sleep = sleep
'''


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    mm = ID_RE.search(name)
    if mm:
        cid = mm.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


out, state, adv, events, pending, learnings = m.out, m.state, m.adv, m.events, m.pending, m.learnings
ds, raw, rx = ta.ds, ta.raw, ta.rx


def pause(d, env=None):
    r = rx(d, env, "pause", "--reason", PAUSE_REASON)
    assert r.returncode == 0, out(r)
    return r


# ------------------------------------------------------------------------------------------------ X01: the lock wait

def cp5_x01_apply_waits_for_a_held_lock_deterministically():
    d, df = m.enrolled_cap_lane("x01")
    lock = Path(d) / ".ratchet/state.lock"
    args = ["director-sign", "raise-limit", "CP1", "--reason", REASON, "--apply"]
    ta._prime_guard(df, d, *args)
    site = Path(_tmp.mkdtemp(prefix="ratchet-x01-site-"))
    (site / "sitecustomize.py").write_text(SITECUSTOMIZE)
    mark = site / "lock-wait.mark"
    env = ta._director_env()
    env["PYTHONPATH"] = str(site) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["RATCHET_TEST_LOCKWAIT_MARK"] = str(mark)
    held = "12345 held-by-test"
    lock.write_text(held)
    proc = subprocess.Popen([sys.executable, str(g.ENGINE), "--root", str(d), *args], cwd=df, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not mark.exists() and proc.poll() is None:
            time.sleep(0.02)
        waiting = mark.exists() and proc.poll() is None
        check("[CP5-X01] the apply reached the lock wait while the lock was held (observed, not assumed)", waiting,
              (proc.poll(), mark.exists()))
        check("[CP5-X01] while it waits, nothing was applied and the held lock is untouched",
              adv(d).get("cap_raised_to") is None and lock.read_text() == held and not events(d, "round_limit_raised"),
              adv(d))
        lock.unlink()
        so, se = proc.communicate(timeout=120)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    check("[CP5-X01] once the lock is released the apply succeeds with its token not stale",
          proc.returncode == 0 and adv(d).get("cap_raised_to") == 3 and "stale" not in (so + se).lower(), (so, se))
    check("[CP5-X01] the raise was recorded exactly once", len(events(d, "round_limit_raised")) == 1
          and learnings(d).count(REASON) == 1, learnings(d)[-300:])
    check("[CP5-X01] the apply removed its own lock", not lock.exists(), "lock file left behind")
    check("[CP5-X01] the sitecustomize marker names the child, so the signal came from the apply itself",
          mark.exists() and mark.read_text().strip() == str(proc.pid), mark.read_text() if mark.exists() else "")


# ------------------------------------------------------------------------------------------------ X02: paused accept

def cp5_x02_accept_finding_applies_through_a_pause():
    d, env, df = ta.accept_lane("x02")
    pause(d, env)
    st0 = state(d)
    check("[CP5-X02] setup: the lane paused itself from blocked", st0.get("phase") == "paused"
          and st0.get("paused_from") == "blocked", (st0.get("phase"), st0.get("paused_from")))
    r = ds(df, d, "accept-finding", "CP1", "--ids", "F1,F2", "--reason", ACCEPT_REASON, env=env)
    check("[CP5-X02] director-sign accept-finding --apply exits 0 on the paused lane", r.returncode == 0, out(r))
    st = state(d)
    check("[CP5-X02] the lane is still paused afterwards, with the pause reason kept",
          st.get("phase") == "paused" and st.get("blocked_reason") == PAUSE_REASON, (st.get("phase"), st.get("blocked_reason")))
    deferred = taf.deferred_findings(d, 1)
    check("[CP5-X02] F1 and F2 are recorded accepted by the director",
          all((taf.by_id(deferred, f) or {}).get("accepted_by") == "director" for f in ("F1", "F2")), deferred)
    check("[CP5-X02] the adversarial gate passed for that round and no question is left pending",
          adv(d).get("status") == "passed" and not pending(d), (adv(d).get("status"), pending(d)))
    check("[CP5-X02] the accept unblocked the run underneath, so resume returns to building",
          st.get("paused_from") == "building", st.get("paused_from"))
    rr = rx(d, env, "resume")
    st2 = state(d)
    check("[CP5-X02] resume carries on in building with no block reason left",
          rr.returncode == 0 and st2.get("phase") == "building" and st2.get("blocked_reason") is None, out(rr))


# ------------------------------------------------------------------------------------------------ X03: paused raise

def cp5_x03_raise_limit_applies_through_a_pause():
    d, df = m.enrolled_cap_lane("x03")
    reason_before = state(d).get("blocked_reason")
    pause(d)
    st0 = state(d)
    check("[CP5-X03] setup: paused from blocked, the block's own reason kept aside",
          st0.get("phase") == "paused" and st0.get("paused_from") == "blocked"
          and st0.get("paused_block_reason") == reason_before, st0.get("paused_block_reason"))
    r = ds(df, d, "raise-limit", "CP1", "--reason", REASON)
    check("[CP5-X03] director-sign raise-limit --apply exits 0 on the paused lane", r.returncode == 0, out(r))
    a, st = adv(d), state(d)
    check("[CP5-X03] the cap is raised to 3 with the attempt count kept (2)", a.get("cap_raised_to") == 3
          and a.get("attempts") == 2, a)
    check("[CP5-X03] the reason is in learnings.md once",
          learnings(d).count(f"CP1's review-round limit was raised to 3 rounds: {REASON}") == 1, learnings(d)[-300:])
    check("[CP5-X03] the lane is still paused, and will resume into building (the raise unblocked it underneath)",
          st.get("phase") == "paused" and st.get("paused_from") == "building" and not pending(d),
          (st.get("phase"), st.get("paused_from")))
    rr = rx(d, None, "resume")
    check("[CP5-X03] resume returns to building", rr.returncode == 0 and state(d).get("phase") == "building", out(rr))
    r2 = tl._reviewers_after_triage(d, "CP1")
    check("[CP5-X03] `reviewers CP1` then opens round 3",
          r2.returncode == 0 and json.loads(r2.stdout).get("round") == 3, out(r2))
    again = ds(df, d, "raise-limit", "CP1", "--reason", REASON)
    check("[CP5-X03] a second raise is refused as already spent", again.returncode != 0
          and re.search(r"already", out(again)) is not None, out(again))


# ------------------------------------------------------------------------------------------------ X04: paused, nothing to apply

def cp5_x04_paused_lane_without_the_state_is_refused_unchanged():
    d, df = ta.building_lane("x04")
    pause(d)
    before = raw(d)
    r = ds(df, d, "raise-limit", "CP1", "--reason", REASON)
    check("[CP5-X04] raise-limit --apply on a lane paused from building is refused naming the pause and the block",
          r.returncode != 0 and "usage:" not in out(r) and re.search(r"paused", out(r)) is not None
          and re.search(r"not blocked", out(r)) is not None, out(r))
    check("[CP5-X04] it left state.json byte-identical", raw(d) == before, "")
    r2 = ds(df, d, "accept-finding", "CP1", "--ids", "F1", "--reason", "r")
    check("[CP5-X04] accept-finding --apply there is refused: no open findings", r2.returncode != 0
          and "usage:" not in out(r2) and re.search(r"no open", out(r2), re.I) is not None, out(r2))
    check("[CP5-X04] it left state.json byte-identical too", raw(d) == before, "")
    r3 = ds(df, d, "choice", "A")
    check("[CP5-X04] choice --apply there is refused: no lettered question", r3.returncode != 0
          and re.search(r"no lettered", out(r3), re.I) is not None and raw(d) == before, out(r3))
    rr = rx(d, None, "resume")
    check("[CP5-X04] the lane still resumes into building afterwards",
          rr.returncode == 0 and state(d).get("phase") == "building", out(rr))


# ------------------------------------------------------------------------------------------------ X05: pause keeps the block

def cp5_x05_pause_and_resume_keep_a_blocked_runs_reason():
    d, _r = tl._project_to_cap("x05", max_rounds=2, cps=1)
    st0 = state(d)
    check("[CP5-X05] setup: blocked with a reason", st0.get("phase") == "blocked" and st0.get("blocked_reason"),
          st0.get("blocked_reason"))
    pause(d)
    st1 = state(d)
    check("[CP5-X05] while paused the pause reason shows and the block's reason is kept aside",
          st1.get("blocked_reason") == PAUSE_REASON and st1.get("paused_block_reason") == st0.get("blocked_reason"),
          (st1.get("blocked_reason"), st1.get("paused_block_reason")))
    rr = rx(d, None, "resume")
    st2 = state(d)
    check("[CP5-X05] resume puts the block back exactly as it was", rr.returncode == 0 and st2.get("phase") == "blocked"
          and st2.get("blocked_reason") == st0.get("blocked_reason") and "paused_block_reason" not in st2,
          (st2.get("phase"), st2.get("blocked_reason")))
    nx = rx(d, None, "next")
    check("[CP5-X05] next still offers the block's options after the pause",
          nx.returncode == 0 and "options" in json.loads(nx.stdout), out(nx)[:300])


# ------------------------------------------------------------------------------------------------ harness

TESTS = [
    ("CP5-X01", cp5_x01_apply_waits_for_a_held_lock_deterministically),
    ("CP5-X02", cp5_x02_accept_finding_applies_through_a_pause),
    ("CP5-X03", cp5_x03_raise_limit_applies_through_a_pause),
    ("CP5-X04", cp5_x04_paused_lane_without_the_state_is_refused_unchanged),
    ("CP5-X05", cp5_x05_pause_and_resume_keep_a_blocked_runs_reason),
]


def write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = [{"id": cid, "status": "passed" if CASE_STATUS.get(cid, False) else "failed",
              "message": "" if CASE_STATUS.get(cid, False) else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid, _ in TESTS]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    global FAIL
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        print(f"\n{cid}: {fn.__name__}")
        try:
            fn()
        except Exception as e:
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
