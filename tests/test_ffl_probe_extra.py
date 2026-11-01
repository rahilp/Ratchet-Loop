#!/usr/bin/env python3
"""
CP5 round-2 regression tests of the fast-follow cleanups run: a chosen model's remembered pass.
   python3 tests/test_ffl_probe_extra.py

A passed test request of a chosen model (agents.reviewer = codex/gpt-5.1) is kept in the shared provider-health.json
under `codex:gpt-5.1` and reused while it is fresh (see tests/test_ffl_probe_memory.py). Two holes were found:
  * [CP5-X10] `reviewer-status --reset <reviewer id>` says the provider "will be tried again" but cleared only the
    provider's own-model key, so the chosen-model pass survived the reset and the next round sent no test request;
  * [CP5-X11] an entry dated in the future counted as a fresh pass (and was never replaced), so a clock mistake or a
    hand-edited date silenced the test request for as long as the date stayed ahead. A few seconds of clock skew must
    still be trusted.
Cases (X10, X11 each hold several checks; the guards pass on the old code, the reset and future-dated checks fail on it):
  * X10: after a reset the next round-opening call sends a new test request (count 2); without a reset it sends none
    (count stays 1); the reset leaves an unrelated provider's entry untouched;
  * X11: a pass dated two days ahead is not fresh (a request is sent, with a window of 0 and with a window of 10 minutes)
    and is replaced by a pass dated now; a just-written pass is trusted; a pass dated a few seconds ahead is trusted
    with a large window.

Everything runs in-process with the helpers of tests/test_ffl_probe_memory.py (throwaway git projects, one private
RATCHET_HOME per case, stub agent CLIs that log every request). No network, no real agent CLI, nothing is spent, and the
machine's real provider-health.json is never touched. The shared key is written out literally (`codex:gpt-5.1`).

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP5-X10",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_config_layers as cl                             # noqa: E402
import test_ffl_probe_memory as pm                          # noqa: E402  (build(), reviewers(), seed(), sent(), ...)

CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
PASS = FAIL = 0
ID_RE = re.compile(r"\[(CP5-[TX]\d+)\]")

K51 = "codex:gpt-5.1"
ROUND_OPENER = "codex-both"


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(f"{name}: {str(detail)[:200]}")
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


def reset(d, env, rid):
    return cl.run(d, env, "reviewer-status", "--reset", rid)


# ------------------------------------------------------------------------------------------------ X10

def x10_reset_clears_a_chosen_models_remembered_pass():
    env, stubs = pm.new_env()
    a = pm.build(env, pm.provider_cfg("codex/gpt-5.1"))
    r1, d1 = pm.reviewers(a, env)
    check("[CP5-X10] setup: round 1 opens on the chosen codex model with one test request on gpt-5.1 and a shared pass",
          pm.clean_run(r1) and pm.ids(d1) == [ROUND_OPENER] and pm.sent(stubs) == ["gpt-5.1"]
          and pm.entry(env, K51).get("ok") is True, (pm.cl.text(r1)[-200:], pm.sent(stubs), pm.record(env)))

    # Guard: without a reset the next round sends nothing (the fix must not over-clear).
    pm.change_code(a, env, "no reset")
    r2, d2 = pm.reviewers(a, env)
    check("[CP5-X10] without a reset, round 2 opens and sends no further test request (count stays 1)",
          pm.clean_run(r2) and pm.ids(d2) == [ROUND_OPENER] and pm.sent(stubs) == ["gpt-5.1"], pm.sent(stubs))

    # An unrelated provider's entry, written fresh, must survive the reset untouched.
    gem = {"ok": True, "at": pm.iso(pm.utcnow()), "detail": "gemini earlier pass"}
    pm.seed(env, "gemini", gem)

    rr = reset(a, env, ROUND_OPENER)
    check("[CP5-X10] reviewer-status --reset codex-both exits 0, says it will be tried again, no traceback",
          rr.returncode == 0 and "will be tried again" in cl.text(rr) and "Traceback" not in cl.text(rr), cl.text(rr)[-300:])
    check("[CP5-X10] the reset removed the chosen-model pass: codex:gpt-5.1 no longer holds ok true",
          pm.entry(env, K51).get("ok") is not True, pm.record(env))
    check("[CP5-X10] the reset left the unrelated gemini entry exactly as it was",
          pm.entry(env, "gemini") == gem, pm.record(env))

    pm.change_code(a, env, "after reset")
    r3, d3 = pm.reviewers(a, env)
    check("[CP5-X10] after the reset the next round opens on the chosen codex model",
          pm.clean_run(r3) and pm.ids(d3) == [ROUND_OPENER] and pm.models(d3) == ["gpt-5.1"], (cl.text(r3)[-300:], d3))
    check("[CP5-X10] after the reset the next round-opening call sends a new test request (count 2)",
          pm.sent(stubs) == ["gpt-5.1", "gpt-5.1"], pm.sent(stubs))
    check("[CP5-X10] and that new pass is recorded again under codex:gpt-5.1",
          pm.entry(env, K51).get("ok") is True, pm.record(env))


# ------------------------------------------------------------------------------------------------ X11

def x11_a_future_dated_pass_is_not_fresh_but_a_little_skew_is():
    # (a) a pass dated two days ahead, window 0: a request is sent and the entry is replaced by one dated now.
    for ttl in (0, 10):
        env, stubs = pm.new_env()
        pm.seed(env, K51, {"ok": True, "at": pm.iso(pm.utcnow() + dt.timedelta(days=2)), "detail": "dated ahead"})
        a = pm.build(env, pm.provider_cfg("codex/gpt-5.1", probe_ttl_minutes=ttl))
        before = pm.utcnow()
        r, d = pm.reviewers(a, env)
        check(f"[CP5-X11] window {ttl}: the round opens on the chosen codex model",
              pm.clean_run(r) and pm.ids(d) == [ROUND_OPENER], (cl.text(r)[-300:], pm.ids(d)))
        check(f"[CP5-X11] window {ttl}: an entry dated two days ahead is not a fresh pass, so one test request is sent",
              pm.sent(stubs) == ["gpt-5.1"], pm.sent(stubs))
        t = pm.when(pm.entry(env, K51))
        check(f"[CP5-X11] window {ttl}: the future-dated entry was replaced by a pass dated from this call",
              t is not None and before - dt.timedelta(seconds=1) <= t <= pm.utcnow() + dt.timedelta(seconds=1),
              pm.record(env))

    # (b) guard: a normal just-written pass is trusted by the next round (default window).
    env, stubs = pm.new_env()
    a = pm.build(env, pm.provider_cfg("codex/gpt-5.1"))
    pm.reviewers(a, env)
    pm.change_code(a, env, "just written")
    r, d = pm.reviewers(a, env)
    check("[CP5-X11] a pass the engine just wrote is still trusted: the next round sends no further request",
          pm.clean_run(r) and pm.ids(d) == [ROUND_OPENER] and pm.sent(stubs) == ["gpt-5.1"], pm.sent(stubs))

    # (c) guard: a few seconds of clock skew is still trusted with a large window.
    env, stubs = pm.new_env()
    skewed = pm.iso(pm.utcnow() + dt.timedelta(seconds=5))
    pm.seed(env, K51, {"ok": True, "at": skewed, "detail": "slightly ahead"})
    a = pm.build(env, pm.provider_cfg("codex/gpt-5.1", probe_ttl_minutes=600))
    r, d = pm.reviewers(a, env)
    check("[CP5-X11] a pass dated 5 seconds ahead is trusted with a 600 minute window: no test request, entry unchanged",
          pm.clean_run(r) and pm.ids(d) == [ROUND_OPENER] and pm.sent(stubs) == []
          and pm.entry(env, K51).get("at") == skewed, (pm.sent(stubs), pm.entry(env, K51)))


TESTS = [
    ("CP5-X10", x10_reset_clears_a_chosen_models_remembered_pass),
    ("CP5-X11", x11_a_future_dated_pass_is_not_fresh_but_a_little_skew_is),
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
        try:
            fn()
        except Exception as e:  # a crash still counts as a failed result for this case, never a silent drop
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
