#!/usr/bin/env python3
"""
CP3 tests: a carried fix-round pick respects a fresh pin.   python3 tests/test_fix_round_carry_bench.py

Mainly (CP3-T01 through CP3-T04, CP3-T06): a fix round that is replaced before anyone reviewed it (an open,
unreviewed one-reviewer round after a block) only carries its earlier reviewer forward when the checkpoint is
still unpinned. Once the checkpoint is pinned to specific reviewer(s) -- even to the exact reviewer the fix
round already had, and even though the pre-pin reviewer stays fully available (on PATH, not benched, not over
its provider share) -- the next `reviewers` call must resolve through the plain pinned path instead, dropping
the fix round's own small_fix/kind entirely, consistent with the documented rule that a pin opts a checkpoint
out of fix-round mechanics altogether (CP5-T10). As a control (CP3-T05), with no pin at all, today's
already-working bench stand-in path (tests/test_fix_round.py's CP5-T22/T26) must keep working unchanged.

Black-box, in tests/test_fix_round.py's own style and with its helpers (that file is frozen; it is imported, not
changed, the way tests/test_fix_round_fixes.py and tests/test_reviewers_pin_reopen.py both do): throwaway
one-checkpoint git projects, codex-standards registered as a command-kind provider against a fake CLI on a
private PATH with its own RATCHET_HOME (`fr._own_home` / `fr._ext_project`), host-correctness reviewed through
the real guard hook. A pin is made the same way tests/test_reviewers_pin_reopen.py makes one: rewriting CP1's
`reviewers` field directly in `.ratchet/runs/f/checkpoints.json`, then the real approval flow any plan edit needs
(`ask approve-changes`, a recorded "yes, go ahead" reply, `approve-changes`). No internal Python function of
ratchet.py is ever called directly; `reviewers CP1 --json`'s own round facts are read straight from its JSON,
the stored facts from state.json's checkpoints.CP1.adv_round, and the counters from
gates.adversarial.attempts / adv_rounds_total (`fr._counters`).

Requirement-to-check map (planned case -> what proves it):
  CP3-T01  round 2 is an open, unreviewed fix round naming only codex-standards; pinned to host-correctness and
           approved; a further code edit, `behavior CP1`, `reviewers CP1 --json`: the new round names exactly
           host-correctness, codex-standards nowhere in it, even though codex-standards stays fully available.
  CP3-T02  the same pin, with NO further code edit at all: the very next `reviewers CP1 --json` call alone names
           host-correctness, never codex-standards.
  CP3-T03  pinned to codex-standards itself (the exact reviewer the fix round already had) and approved; a
           further code edit: the new round still names codex-standards, but is no longer small_fix / after a
           block -- resolved the plain pinned way, not through the carry-forward.
  CP3-T04  control, no pin at all: the fixer rewrites the code again; the ordinary carry-forward is unchanged --
           same reviewer (codex-standards), same slot (1), still small_fix true with the after-a-block kind.
  CP3-T05  control, no pin: codex-standards is benched, the code is reverted back to exactly the tree round 1
           reviewed, `behavior CP1` runs: the new round still names exactly one reviewer, standards lens, not
           codex-standards, still small_fix true with the after-a-block kind (today's already-working bench
           path, tests/test_fix_round.py's CP5-T22/T26, must not regress).
  CP3-T06  the CP3-T01 sequence costs no extra round: attempts/adv_rounds_total right after the pin-driven round
           opens equal what they were right after round 2 first opened, and asking again with nothing further
           changed returns the same round number, same reviewer and the same nonce, with no new round folder.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP3-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_gates as g          # noqa: E402  (rx, say)
import test_fix_round as fr     # noqa: E402  (_own_home, _ext_project, _ext_record, _open, _write_code, _triage,
                                 #              _ids, _lens, _kind, _after_block, _counters, _cps, _out, _finding,
                                 #              _NO_FINDINGS)

PLANNED = [f"CP3-T{i:02d}" for i in range(1, 7)]
PASS = FAIL = 0
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
ID_RE = re.compile(r"\[(CP3-T\d+)\]")

SLUG = "f"


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'OK ' if cond else 'FAIL'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ------------------------------------------------------------------------------------------------ fixtures

def _plan_path(d, slug=SLUG):
    return Path(d) / f".ratchet/runs/{slug}/checkpoints.json"


def _pin(d, pin, slug=SLUG, cp="CP1"):
    """Rewrites `cp`'s `reviewers` field in checkpoints.json: a list sets the pin, None deletes the key."""
    p = _plan_path(d, slug)
    plan = json.loads(p.read_text())
    for c in plan["checkpoints"]:
        if c["id"] == cp:
            if pin is None:
                c.pop("reviewers", None)
            else:
                c["reviewers"] = list(pin)
    p.write_text(json.dumps(plan))


def _approve_changes(d, env=None):
    """The flow every plan edit after approval needs before the next engine call reads it
    (tests/test_reviewers_pin_reopen.py's `_approve_changes`): `ask approve-changes`, a recorded "yes, go ahead"
    reply, then `approve-changes` itself."""
    ask = g.rx(d, "ask", "approve-changes", env=env)
    g.say(d, "yes, go ahead")
    return ask, g.rx(d, "approve-changes", env=env)


def _pin_and_approve(d, pin, env=None):
    _pin(d, pin)
    return _approve_changes(d, env=env)


def _setup_blocked_fix_round(tag):
    """Round 1 (codex-standards + host-correctness, unpinned) blocked by codex-standards/S1 only, triaged fix,
    then a 30-line fix to src/f.js: round 2 opens as a one-reviewer fix round naming codex-standards, open and
    unreviewed (no review written). Returns (project dir, env, round 2's `reviewers --json`)."""
    d, env = fr._ext_project(tag, {"normal": ["codex-standards", "host-correctness"]},
                              [("codex", "openai", "gpt-5")],
                              replies={"codex": (json.dumps({"findings": [fr._finding("S1")]}), fr._NO_FINDINGS)})
    rnd1 = fr._open(d, env=env)
    fr._ext_record(d, rnd1, env)
    fr._triage(d, ["codex-standards/S1"], env=env)
    fr._write_code(d, 30, tag, env=env)
    rnd2 = fr._open(d, env=env)
    return d, env, rnd2


def _revert_code(d, env=None):
    """Restores src/f.js to exactly the tree round 1 reviewed (g.project's own original content, before any
    fixer edit), then runs `behavior` so the engine recomputes the fingerprint."""
    (Path(d) / "src/f.js").write_text("export const f = () => 1;\n")
    r = g.rx(d, "behavior", "CP1", env=env)
    assert r.returncode == 0, fr._out(r)


def _check_fixture_round2(case, rnd2):
    check(f"[{case}] (fixture) round 2 is an open, unreviewed one-reviewer fix round naming codex-standards",
          rnd2.get("round") == 2 and fr._ids(rnd2) == ["codex-standards"] and fr._after_block(rnd2),
          (rnd2.get("round"), fr._ids(rnd2), fr._kind(rnd2) or "no kind field"))


# ------------------------------------------------------------------------------------------------ CP3-T01..T06

def t01_a_fresh_pin_beats_the_carry_forward_with_a_further_code_edit():
    with fr._own_home():
        d, env, rnd2 = _setup_blocked_fix_round("cp3t01")
        _check_fixture_round2("CP3-T01", rnd2)
        ask, ac = _pin_and_approve(d, ["host-correctness"], env=env)
        check("[CP3-T01] the pin to host-correctness is accepted with approve-changes", ac.returncode == 0,
              fr._out(ac))
        fr._write_code(d, 32, "cp3t01b", env=env)  # a further edit on top of round 2's own 30-line fix
        rnd3 = fr._open(d, env=env)
        check("[CP3-T01] the new round names exactly host-correctness", fr._ids(rnd3) == ["host-correctness"],
              fr._ids(rnd3))
        check("[CP3-T01] codex-standards appears nowhere in it, even though it is still on PATH and not benched",
              "codex-standards" not in fr._ids(rnd3) and "codex-standards" not in json.dumps(rnd3), rnd3)


def t02_the_pin_alone_reopens_the_carried_round_with_no_further_code_edit():
    with fr._own_home():
        d, env, rnd2 = _setup_blocked_fix_round("cp3t02")
        _check_fixture_round2("CP3-T02", rnd2)
        ask, ac = _pin_and_approve(d, ["host-correctness"], env=env)
        check("[CP3-T02] the pin to host-correctness is accepted with approve-changes", ac.returncode == 0,
              fr._out(ac))
        rnd3 = fr._open(d, env=env)  # no further code edit at all
        check("[CP3-T02] the round that comes back names exactly host-correctness, never codex-standards",
              fr._ids(rnd3) == ["host-correctness"], fr._ids(rnd3))
        check("[CP3-T02] it is a new round, not round 2 re-shown", rnd3.get("round") != rnd2.get("round"),
              (rnd2.get("round"), rnd3.get("round")))


def t03_a_pin_to_the_same_reviewer_still_drops_fix_round_status():
    with fr._own_home():
        d, env, rnd2 = _setup_blocked_fix_round("cp3t03")
        _check_fixture_round2("CP3-T03", rnd2)
        ask, ac = _pin_and_approve(d, ["codex-standards"], env=env)
        check("[CP3-T03] the (no-op-looking) pin to codex-standards itself is accepted", ac.returncode == 0,
              fr._out(ac))
        fr._write_code(d, 33, "cp3t03b", env=env)
        rnd3 = fr._open(d, env=env)
        check("[CP3-T03] the new round still names codex-standards", fr._ids(rnd3) == ["codex-standards"],
              fr._ids(rnd3))
        check("[CP3-T03] it is NOT marked small_fix", rnd3.get("small_fix") is not True, rnd3.get("small_fix"))
        check("[CP3-T03] it has no after-a-block kind", not fr._after_block(rnd3),
              fr._kind(rnd3) or "no kind field")
        adv3 = fr._cps(d).get("adv_round") or {}
        check("[CP3-T03] state.json agrees: resolved the plain pinned way, kind/fix_round both null",
              adv3.get("reviewers") == ["codex-standards"] and adv3.get("kind") is None
              and adv3.get("fix_round") is None, adv3)


def t04_control_no_pin_the_ordinary_carry_forward_is_unchanged():
    with fr._own_home():
        d, env, rnd2 = _setup_blocked_fix_round("cp3t04")
        _check_fixture_round2("CP3-T04", rnd2)
        fr._write_code(d, 34, "cp3t04b", env=env)
        rnd3 = fr._open(d, env=env)
        check("[CP3-T04] the new round names exactly codex-standards, with no fallback",
              fr._ids(rnd3) == ["codex-standards"], fr._ids(rnd3))
        check("[CP3-T04] it is in slot 1", rnd3["reviewers"][0].get("slot") == 1, rnd3["reviewers"][0])
        check("[CP3-T04] it is marked small_fix true with the after-a-block kind",
              rnd3.get("small_fix") is True and fr._after_block(rnd3),
              (rnd3.get("small_fix"), fr._kind(rnd3) or "no kind field"))


def t05_control_no_pin_a_benched_carried_reviewer_still_gets_a_stand_in():
    with fr._own_home():
        d, env, rnd2 = _setup_blocked_fix_round("cp3t05")
        _check_fixture_round2("CP3-T05", rnd2)
        m = g.rx(d, "reviewer-status", "--mark", "codex-standards", "--reason", "quota exceeded", env=env)
        assert m.returncode == 0, fr._out(m)
        _revert_code(d, env=env)  # back to exactly the tree round 1 reviewed: a genuine fingerprint change
        rnd3 = fr._open(d, env=env)
        check("[CP3-T05] the new round names exactly one reviewer", len(rnd3["reviewers"]) == 1, fr._ids(rnd3))
        got = fr._ids(rnd3)[0]
        check("[CP3-T05] the stand-in has lens standards and is not codex-standards",
              fr._lens(got) == "standards" and got != "codex-standards", got)
        check("[CP3-T05] it is still marked small_fix true with the after-a-block kind",
              rnd3.get("small_fix") is True and fr._after_block(rnd3),
              (rnd3.get("small_fix"), fr._kind(rnd3) or "no kind field"))


def t06_replacing_an_unreviewed_round_via_a_fresh_pin_costs_no_extra_round():
    with fr._own_home():
        d, env, rnd2 = _setup_blocked_fix_round("cp3t06")
        _check_fixture_round2("CP3-T06", rnd2)
        before = fr._counters(d)  # read just after round 2 opened
        ask, ac = _pin_and_approve(d, ["host-correctness"], env=env)
        check("[CP3-T06] the pin is accepted with approve-changes", ac.returncode == 0, fr._out(ac))
        rnd3 = fr._open(d, env=env)
        check("[CP3-T06] the pinned round opens naming host-correctness", fr._ids(rnd3) == ["host-correctness"],
              fr._ids(rnd3))
        check("[CP3-T06] attempts and adv_rounds_total equal their values right after round 2 opened",
              fr._counters(d) == before, (before, fr._counters(d)))
        round3_num, nonce3 = rnd3.get("round"), rnd3.get("round_nonce")
        rnd3b = fr._open(d, env=env)  # asking again, nothing further changed
        check("[CP3-T06] asking again returns the same round number and the same reviewer",
              rnd3b.get("round") == round3_num and fr._ids(rnd3b) == ["host-correctness"],
              (round3_num, rnd3b.get("round"), fr._ids(rnd3b)))
        check("[CP3-T06] asking again keeps the same round nonce (no churn)",
              rnd3b.get("round_nonce") == nonce3, (nonce3, rnd3b.get("round_nonce")))
        extra_round_dir = Path(d) / f".ratchet/runs/{SLUG}/reviews/CP1/adv-round-{round3_num + 1:02d}"
        check("[CP3-T06] no further round folder is created by asking again", not extra_round_dir.exists(),
              str(extra_round_dir))


CASES = [
    ("CP3-T01", t01_a_fresh_pin_beats_the_carry_forward_with_a_further_code_edit),
    ("CP3-T02", t02_the_pin_alone_reopens_the_carried_round_with_no_further_code_edit),
    ("CP3-T03", t03_a_pin_to_the_same_reviewer_still_drops_fix_round_status),
    ("CP3-T04", t04_control_no_pin_the_ordinary_carry_forward_is_unchanged),
    ("CP3-T05", t05_control_no_pin_a_benched_carried_reviewer_still_gets_a_stand_in),
    ("CP3-T06", t06_replacing_an_unreviewed_round_via_a_fresh_pin_costs_no_extra_round),
]


def main():
    for cid, fn in CASES:
        print(f"{cid}: {fn.__name__}")
        try:
            fn()
        except Exception:
            check(f"[{cid}] ran to the end without an unexpected error", False, traceback.format_exc())
    for cid in PLANNED:
        if cid not in CASE_STATUS:
            check(f"[{cid}] recorded at least one check", False, "no check ran for this case")
    print(f"\n{PASS} passed, {FAIL} failed")
    rep = os.environ.get("RATCHET_TEST_REPORT")
    if rep:
        cases = [{"id": c, "status": "passed" if ok else "failed",
                  "message": "; ".join(CASE_MESSAGES.get(c, []))[:2000]}
                 for c, ok in sorted(CASE_STATUS.items())]
        Path(rep).write_text(json.dumps({"cases": cases}, indent=1))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
