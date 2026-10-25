#!/usr/bin/env python3
"""
CP2 tests: a reviewers pin change reopens an open adversarial round.   python3 tests/test_reviewers_pin_reopen.py

Editing a checkpoint's `reviewers` pin in checkpoints.json while its adversarial round is open but not yet
recorded, and accepting the edit with `approve-changes`, makes the NEXT `reviewers` call open a new round with
the pinned reviewers -- instead of silently reshowing the stale round the way today's code does (cmd_reviewers'
reopen condition only ever looks at whether the code's fingerprint changed). Asking again with no pin, or with
the same pin the round already opened with, must keep showing the very same round (same round number, same
reviewer ids, same nonce) -- these are controls that already pass today, proving this is a surgical addition, not
a trigger-happy one. A round from before this change, with no stored `reviewers_pin` field of its own, is compared
by its actual current reviewer ids against the step's live pin instead: a real difference reopens it just like
any other round, and restating the same reviewers it already has (in any order) does not.

Black-box: every case drives the real `ratchet.py` CLI in a throwaway one-checkpoint git project, reusing
tests/test_fix_round.py's own project/round helpers (`_start`, `_open`, `_ids`, `_write_code`, `_block`,
`_triage`, `_counters`, `_cps`, `_pick`, `_lens`, `_kind`, `_after_block`, `_finding`, `_out`) the way its
docstring describes, imported here as `fr`. No internal Python function of ratchet.py is ever called directly.
A pin edit is made by rewriting CP1's `reviewers` field directly in `.ratchet/runs/f/checkpoints.json`, then
run through the real approval flow Ratchet requires for any plan/test-plan edit after approval: `ask
approve-changes`, a recorded "yes, go ahead" reply (`test_gates.py`'s `say`), then `approve-changes`
(tests/test_dm_finish_extra.py's `_approve_changes` pattern). `reviewers CP1 --json`'s own round facts (round
number, round_nonce, the `reviewers` row list) are read straight from its JSON; the stored facts (nonce, kind,
small_fix, fix_round, reviewers ids) are read from state.json's checkpoints.CP1.adv_round, and the step's and
run's review-round counters from gates.adversarial.attempts / adv_rounds_total (`fr._counters`). Every call in
this file sees an idle machine (`g.ENV["RATCHET_FAKE_LOADAVG"] = "0"`, set once by importing test_fix_round) and
providers=[] (set by `g.project`), so only the host-* reviewers ever exist here and no agent CLI is ever
launched.

Requirement-to-check map (planned case -> what proves it):
  CP2-T01  pinning an open, unreviewed two-reviewer round to one other reviewer (host-both) and approving it
           makes the next `reviewers --json` call open round 2 naming only host-both; neither original reviewer
           is listed; state's adv_round agrees, with a new nonce.
  CP2-T02  asking again twice with nothing edited keeps showing round 1, same two ids, same nonce/opened_at,
           same attempts/adv_rounds_total.
  CP2-T03  a step pinned from the start keeps round 1 open when an unrelated field (summary) changes but the
           pin is restated unchanged: same round, same ids, same nonce, same counters.
  CP2-T04  an unpinned step similarly keeps round 1 when an unrelated field changes and no reviewers key is
           ever added: no pin before, no pin now counts as unchanged.
  CP2-T05  after CP2-T01's pin reopens round 2, asking again (twice) does not keep reopening: same round 2,
           same nonce, and no adv-round-03 folder is ever created.
  CP2-T06  removing an existing pin (deleting the reviewers key) and approving it reopens the round with the
           step's normal two-lens default set, and the old pinned id is gone.
  CP2-T07  a pin change that replaces an unreviewed round costs nothing extra: attempts/adv_rounds_total after
           the reopen equal their values right after round 1 first opened (the existing free-replacement rule).
  CP2-T08  editing the pin in checkpoints.json WITHOUT running approve-changes opens no new round at all.
  CP2-T09  a round with no stored pin field (as if opened before this change) stays open with nothing edited or
           with the pin restated unchanged, but reopens -- new round, new nonce -- when the pin is changed to
           different reviewers and approved.
  CP2-T10  a recorded, blocked round that is then pinned and fixed opens its next round through today's own
           fingerprint-change path, exactly honoring the new pin (not a fix round): control proving the new pin
           reaches the fix-round path uninterrupted.
  CP2-T11  a code change and a pin change made together still only open exactly one new round, naming the pin.
  CP2-T12  an open, unreviewed fix round with no pin edit keeps its own one reviewer when asked again, and still
           carries that reviewer forward into round 3 on a further code change (regression control, unrelated to
           pins).
  CP2-T13  pinning to a different reviewer during an open, unreviewed fix round opens the next round naming the
           pinned reviewer, not the one the fix round carried; it is not recorded as a fix round.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP2-T01",
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
import test_fix_round as fr     # noqa: E402  (_start, _open, _ids, _write_code, _block, _triage, _counters,
                                 #              _cps, _pick, _lens, _kind, _after_block, _finding, _out)
import test_dm_table as dt      # noqa: E402  (mutate: a re-baselined hand edit of state.json)

PLANNED = [f"CP2-T{i:02d}" for i in range(1, 14)]
PASS = FAIL = 0
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
ID_RE = re.compile(r"\[(CP2-[TX]\d+)\]")

SLUG = "f"
OLD_ADV_ROUND_KEYS = {"round", "nonce", "fingerprint", "opened_at", "reviewers", "narrowed", "small_fix", "kind",
                      "fix_round", "prev_round_tree", "substitutions", "resolved", "recorded", "failed_providers",
                      "failed_reviewers"}


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


def _edit_summary(d, text, slug=SLUG, cp="CP1"):
    """An unrelated checkpoints.json field edit, used as a control: it must never itself reopen a round."""
    p = _plan_path(d, slug)
    plan = json.loads(p.read_text())
    for c in plan["checkpoints"]:
        if c["id"] == cp:
            c["summary"] = text
    p.write_text(json.dumps(plan))


def _approve_changes(d):
    """The flow every plan/test-plan edit after approval needs before the next engine call reads it
    (tests/test_dm_finish_extra.py's `_approve_changes`): `ask approve-changes`, a recorded "yes, go ahead"
    reply, then `approve-changes` itself."""
    ask = g.rx(d, "ask", "approve-changes")
    g.say(d, "yes, go ahead")
    return ask, g.rx(d, "approve-changes")


def _pin_and_approve(d, pin):
    _pin(d, pin)
    return _approve_changes(d)


def _strip_new_adv_round_keys(d, slug=SLUG, cp="CP1"):
    """Drops every key on state.json's checkpoints.<cp>.adv_round that is not one of the fields a round held
    before this change, so the round looks exactly like one opened by yesterday's code (no stored pin field of
    its own). Uses test_dm_table's re-baselined `mutate` (never a plain file write): Ratchet signs state.json
    and refuses to trust a hand edit that skips the re-baseline."""
    def drop(st):
        adv = st["checkpoints"][cp].get("adv_round") or {}
        for k in [k for k in list(adv.keys()) if k not in OLD_ADV_ROUND_KEYS]:
            del adv[k]
    dt.mutate(d, drop, slug=slug)


def _strip_approved_reviewers(d, slug=SLUG):
    """Drops state.json's `approved.reviewers` map entirely, so the run's approval snapshot looks exactly like
    one written before that field existed (a real pre-upgrade run's shape), rather than merely stripping the
    round's own keys (`_strip_new_adv_round_keys`). Combined with that helper, this is the actual legacy shape
    every real upgraded run has on its first `approve-changes` under the new code (H6/S3)."""
    def drop(st):
        (st.get("approved") or {}).pop("reviewers", None)
    dt.mutate(d, drop, slug=slug)


# ------------------------------------------------------------------------------------------------ CP2-T01..T12

def t01_pin_to_one_reviewer_opens_a_new_round_naming_only_it():
    d = fr._start("cp2t01")
    rnd1 = fr._open(d)
    ids1 = fr._ids(rnd1)
    check("[CP2-T01] (fixture) round 1 of a normal-risk step names two reviewers (one standards, one "
          "correctness lens)", len(ids1) == 2 and sorted(fr._lens(i) for i in ids1) == ["correctness", "standards"],
          ids1)
    nonce1 = (fr._cps(d).get("adv_round") or {}).get("nonce")
    ask, ac = _pin_and_approve(d, ["host-both"])
    check("[CP2-T01] approve-changes succeeds after the pin edit", ac.returncode == 0, fr._out(ac))
    rnd2 = fr._open(d)
    check("[CP2-T01] the next reviewers call opens round 2, not round 1", rnd2.get("round") == 2, rnd2.get("round"))
    check("[CP2-T01] round 2's reviewers are exactly [\"host-both\"]", fr._ids(rnd2) == ["host-both"], fr._ids(rnd2))
    check("[CP2-T01] neither of round 1's two reviewers is listed",
          not (set(ids1) & set(fr._ids(rnd2))), (ids1, fr._ids(rnd2)))
    adv2 = fr._cps(d).get("adv_round") or {}
    check("[CP2-T01] state.json's adv_round.round is 2 with the same single reviewer",
          adv2.get("round") == 2 and adv2.get("reviewers") == ["host-both"], adv2)
    check("[CP2-T01] round 2's nonce differs from round 1's",
          bool(adv2.get("nonce")) and adv2.get("nonce") != nonce1, (nonce1, adv2.get("nonce")))


def t02_asking_again_with_no_change_shows_the_same_round():
    d = fr._start("cp2t02")
    rnd1 = fr._open(d)
    check("[CP2-T02] (fixture) round 1 names two reviewers", len(rnd1["reviewers"]) == 2, fr._ids(rnd1))
    adv1 = fr._cps(d).get("adv_round") or {}
    nonce1, opened_at1 = adv1.get("nonce"), adv1.get("opened_at")
    before = fr._counters(d)
    for n in (2, 3):
        r = fr._open(d)
        check(f"[CP2-T02] call {n} still reports round 1", r.get("round") == 1, r.get("round"))
        check(f"[CP2-T02] call {n} names the same two reviewer ids in the same order",
              fr._ids(r) == fr._ids(rnd1), (fr._ids(rnd1), fr._ids(r)))
        adv = fr._cps(d).get("adv_round") or {}
        check(f"[CP2-T02] after call {n}, the adv_round nonce and opened_at are unchanged",
              adv.get("nonce") == nonce1 and adv.get("opened_at") == opened_at1,
              (nonce1, adv.get("nonce"), opened_at1, adv.get("opened_at")))
        check(f"[CP2-T02] after call {n}, attempts and adv_rounds_total are the same numbers as after the "
              "first call", fr._counters(d) == before, (before, fr._counters(d)))


def t03_a_restated_unchanged_pin_does_not_reopen():
    d = fr._start("cp2t03", extra_cp={"reviewers": ["host-standards", "host-correctness"]})
    rnd1 = fr._open(d)
    check("[CP2-T03] (fixture) round 1 names exactly the pinned pair",
          fr._ids(rnd1) == ["host-standards", "host-correctness"], fr._ids(rnd1))
    nonce1 = (fr._cps(d).get("adv_round") or {}).get("nonce")
    before = fr._counters(d)
    _edit_summary(d, "a reworded summary, nothing else")
    ask, ac = _approve_changes(d)
    check("[CP2-T03] the unrelated summary edit is accepted with approve-changes", ac.returncode == 0, fr._out(ac))
    rnd2 = fr._open(d)
    check("[CP2-T03] still round 1 with the same two reviewer ids",
          rnd2.get("round") == 1 and fr._ids(rnd2) == ["host-standards", "host-correctness"],
          (rnd2.get("round"), fr._ids(rnd2)))
    adv2 = fr._cps(d).get("adv_round") or {}
    check("[CP2-T03] the adv_round nonce is unchanged", adv2.get("nonce") == nonce1, (nonce1, adv2.get("nonce")))
    check("[CP2-T03] attempts / adv_rounds_total are unchanged (the reopen keys on the pin, not any plan change)",
          fr._counters(d) == before, (before, fr._counters(d)))


def t04_an_unpinned_steps_unrelated_plan_edit_keeps_its_round():
    d = fr._start("cp2t04")
    rnd1 = fr._open(d)
    adv1 = fr._cps(d).get("adv_round") or {}
    nonce1 = adv1.get("nonce")
    _edit_summary(d, "a reworded summary, still unpinned")
    ask, ac = _approve_changes(d)
    check("[CP2-T04] the summary edit is accepted with approve-changes", ac.returncode == 0, fr._out(ac))
    rnd2 = fr._open(d)
    check("[CP2-T04] still round 1 (no pin before and no pin now counts as unchanged)",
          rnd2.get("round") == 1, rnd2.get("round"))
    check("[CP2-T04] the same two reviewer ids", fr._ids(rnd2) == fr._ids(rnd1), (fr._ids(rnd1), fr._ids(rnd2)))
    adv2 = fr._cps(d).get("adv_round") or {}
    check("[CP2-T04] the same nonce", adv2.get("nonce") == nonce1, (nonce1, adv2.get("nonce")))


def t05_after_a_pin_reopens_asking_again_does_not_keep_reopening():
    d = fr._start("cp2t05")
    fr._open(d)
    ask, ac = _pin_and_approve(d, ["host-both"])
    check("[CP2-T05] the pin change is accepted", ac.returncode == 0, fr._out(ac))
    rnd2 = fr._open(d)
    check("[CP2-T05] (fixture) the pin change opened round 2 naming only host-both",
          rnd2.get("round") == 2 and fr._ids(rnd2) == ["host-both"], (rnd2.get("round"), fr._ids(rnd2)))
    nonce2 = rnd2["round_nonce"]
    for n in (2, 3):
        r = fr._open(d)
        check(f"[CP2-T05] extra call {n} still reports round 2 naming exactly host-both",
              r.get("round") == 2 and fr._ids(r) == ["host-both"], (r.get("round"), fr._ids(r)))
        check(f"[CP2-T05] extra call {n} keeps round 2's nonce", r.get("round_nonce") == nonce2,
              (nonce2, r.get("round_nonce")))
    round3_dir = Path(d) / f".ratchet/runs/{SLUG}/reviews/CP1/adv-round-03"
    check("[CP2-T05] no round 3 folder (reviews/CP1/adv-round-03) exists", not round3_dir.exists(), str(round3_dir))


def t06_removing_a_pin_reopens_with_the_steps_default_reviewers():
    d = fr._start("cp2t06", extra_cp={"reviewers": ["host-both"]})
    rnd1 = fr._open(d)
    check("[CP2-T06] (fixture) round 1 names only host-both", fr._ids(rnd1) == ["host-both"], fr._ids(rnd1))
    _pin(d, None)  # delete the reviewers key entirely
    ask, ac = _approve_changes(d)
    check("[CP2-T06] removing the pin is accepted with approve-changes", ac.returncode == 0, fr._out(ac))
    rnd2 = fr._open(d)
    check("[CP2-T06] the next call opens round 2", rnd2.get("round") == 2, rnd2.get("round"))
    ids2 = fr._ids(rnd2)
    check("[CP2-T06] round 2 names two reviewers", len(ids2) == 2, ids2)
    lenses = sorted(fr._lens(i) for i in ids2)
    check("[CP2-T06] one is a standards lens and one a correctness lens",
          lenses == ["correctness", "standards"], ids2)
    check("[CP2-T06] host-both is not among them", "host-both" not in ids2, ids2)


def t07_a_pin_reopen_of_an_unreviewed_round_costs_no_round():
    d = fr._start("cp2t07")
    fr._open(d)
    before = fr._counters(d)
    ask, ac = _pin_and_approve(d, ["host-both"])
    check("[CP2-T07] the pin change is accepted", ac.returncode == 0, fr._out(ac))
    rnd2 = fr._open(d)
    check("[CP2-T07] round 2 still opens", rnd2.get("round") == 2, rnd2.get("round"))
    check("[CP2-T07] round 2 names only host-both", fr._ids(rnd2) == ["host-both"], fr._ids(rnd2))
    check("[CP2-T07] attempts and adv_rounds_total are the same numbers as after round 1 opened",
          fr._counters(d) == before, (before, fr._counters(d)))


def t08_an_unapproved_pin_edit_opens_no_round():
    d = fr._start("cp2t08")
    rnd1 = fr._open(d)
    adv1 = fr._cps(d).get("adv_round") or {}
    nonce1 = adv1.get("nonce")
    before = fr._counters(d)
    _pin(d, ["host-both"])  # edited but approve-changes is never run
    r = g.rx(d, "reviewers", "CP1", "--json")
    adv2 = fr._cps(d).get("adv_round") or {}
    check("[CP2-T08] no round 2 is opened: state's adv_round is still round 1",
          adv2.get("round") == 1, adv2.get("round"))
    check("[CP2-T08] still the same two reviewers and the same nonce",
          adv2.get("reviewers") == adv1.get("reviewers") and adv2.get("nonce") == nonce1,
          (adv1.get("reviewers"), adv2.get("reviewers"), nonce1, adv2.get("nonce")))
    check("[CP2-T08] attempts / adv_rounds_total are unchanged", fr._counters(d) == before,
          (before, fr._counters(d)))
    check("[CP2-T08] the call is refused for the unapproved plan change, or still prints round 1",
          r.returncode != 0 or json.loads(r.stdout).get("round") == 1, fr._out(r))


def t09_an_old_shape_round_reopens_on_a_differing_pin():
    d = fr._start("cp2t09", extra_cp={"reviewers": ["host-standards", "host-correctness"]})
    rnd1 = fr._open(d)
    check("[CP2-T09] (fixture) round 1 names the pinned pair",
          fr._ids(rnd1) == ["host-standards", "host-correctness"], fr._ids(rnd1))
    nonce1 = (fr._cps(d).get("adv_round") or {}).get("nonce")
    _strip_new_adv_round_keys(d)
    # (a) nothing edited
    rndA = g.rx(d, "reviewers", "CP1", "--json")
    assert rndA.returncode == 0, fr._out(rndA)
    rndA = json.loads(rndA.stdout)
    check("[CP2-T09] (a) nothing edited: still round 1 with host-standards and host-correctness",
          rndA.get("round") == 1 and fr._ids(rndA) == ["host-standards", "host-correctness"],
          (rndA.get("round"), fr._ids(rndA)))
    check("[CP2-T09] (a) the same nonce", rndA.get("round_nonce") == nonce1, (nonce1, rndA.get("round_nonce")))
    # (c) the pin is restated as the SAME reviewers the legacy round already has (reordered), approved: no reopen
    ask, ac = _pin_and_approve(d, ["host-correctness", "host-standards"])
    check("[CP2-T09] (c) restating the unchanged pin is accepted with approve-changes", ac.returncode == 0,
          fr._out(ac))
    rndC = g.rx(d, "reviewers", "CP1", "--json")
    assert rndC.returncode == 0, fr._out(rndC)
    rndC = json.loads(rndC.stdout)
    check("[CP2-T09] (c) still round 1 with host-standards and host-correctness (same pin, no reopen)",
          rndC.get("round") == 1 and fr._ids(rndC) == ["host-standards", "host-correctness"],
          (rndC.get("round"), fr._ids(rndC)))
    check("[CP2-T09] (c) the same nonce", rndC.get("round_nonce") == nonce1, (nonce1, rndC.get("round_nonce")))
    # (b) the pin is changed to a different reviewer and approved: the legacy round reopens
    ask, ac = _pin_and_approve(d, ["host-both"])
    check("[CP2-T09] (b) the pin change is accepted with approve-changes", ac.returncode == 0, fr._out(ac))
    rndB = g.rx(d, "reviewers", "CP1", "--json")
    assert rndB.returncode == 0, fr._out(rndB)
    rndB = json.loads(rndB.stdout)
    check("[CP2-T09] (b) a differing pin reopens the legacy round: round 2 naming only host-both",
          rndB.get("round") == 2 and fr._ids(rndB) == ["host-both"], (rndB.get("round"), fr._ids(rndB)))
    check("[CP2-T09] (b) round 2's nonce differs from round 1's",
          bool(rndB.get("round_nonce")) and rndB.get("round_nonce") != nonce1,
          (nonce1, rndB.get("round_nonce")))


def t10_a_recorded_blocked_round_re_pinned_before_the_fix_opens_with_the_new_pin():
    d = fr._start("cp2t10")
    rnd1 = fr._open(d)
    check("[CP2-T10] (fixture) round 1 names two reviewers", len(rnd1["reviewers"]) == 2, fr._ids(rnd1))
    rid = fr._pick(rnd1, "correctness")
    fr._block(d, rnd1, {rid: [fr._finding("B1")]})
    before = fr._counters(d)
    ask, ac = _pin_and_approve(d, ["host-both"])
    check("[CP2-T10] the pin change is accepted", ac.returncode == 0, fr._out(ac))
    fr._write_code(d, 30, "cp2t10")
    rnd2 = fr._open(d)
    check("[CP2-T10] round 2 opens (exactly one new round)", rnd2.get("round") == 2, rnd2.get("round"))
    check("[CP2-T10] round 2 names exactly host-both", fr._ids(rnd2) == ["host-both"], fr._ids(rnd2))
    check("[CP2-T10] it is not a fix round: kind is null", rnd2.get("kind") is None, rnd2.get("kind"))
    check("[CP2-T10] it is not a fix round: fix_round is null", rnd2.get("fix_round") is None, rnd2.get("fix_round"))
    check("[CP2-T10] attempts rose by exactly 1 from round 1's value",
          fr._counters(d) == (before[0] + 1, before[1] + 1), (before, fr._counters(d)))
    rnd3 = fr._open(d)
    check("[CP2-T10] no round 3 opens from calling reviewers again", rnd3.get("round") == 2, rnd3.get("round"))


def t11_a_code_change_and_a_pin_change_together_open_exactly_one_new_round():
    d = fr._start("cp2t11")
    rnd1 = fr._open(d)
    check("[CP2-T11] (fixture) round 1 names two reviewers, unreviewed", len(rnd1["reviewers"]) == 2, fr._ids(rnd1))
    fr._write_code(d, 30, "cp2t11")
    ask, ac = _pin_and_approve(d, ["host-both"])
    check("[CP2-T11] the pin change is accepted", ac.returncode == 0, fr._out(ac))
    rnd2 = fr._open(d)
    check("[CP2-T11] the first call reports round 2 naming exactly host-both",
          rnd2.get("round") == 2 and fr._ids(rnd2) == ["host-both"], (rnd2.get("round"), fr._ids(rnd2)))
    nonce2 = rnd2["round_nonce"]
    rnd2b = fr._open(d)
    check("[CP2-T11] the second call reports the same round 2 and nonce (the code change and the pin change "
          "do not each add a round)", rnd2b.get("round") == 2 and rnd2b.get("round_nonce") == nonce2,
          (rnd2b.get("round"), nonce2, rnd2b.get("round_nonce")))


def t12_an_open_unreviewed_fix_round_keeps_its_reviewer_and_carries_it_forward():
    d = fr._start("cp2t12")
    rnd1 = fr._open(d)
    rid = fr._pick(rnd1, "standards")
    fr._block(d, rnd1, {rid: [fr._finding("F1")]})
    fr._write_code(d, 30, "cp2t12a")
    rnd2 = fr._open(d)
    check("[CP2-T12] (fixture) round 2 is a one-reviewer fix round naming the standards reviewer",
          rnd2.get("round") == 2 and fr._ids(rnd2) == [rid] and fr._after_block(rnd2),
          (rnd2.get("round"), fr._ids(rnd2), fr._kind(rnd2)))
    nonce2 = rnd2["round_nonce"]
    before = fr._counters(d)
    # (a) nothing edited
    rnd2a = fr._open(d)
    check("[CP2-T12] (a) still round 2, same single reviewer, same nonce",
          rnd2a.get("round") == 2 and fr._ids(rnd2a) == [rid] and rnd2a.get("round_nonce") == nonce2,
          (rnd2a.get("round"), fr._ids(rnd2a), nonce2, rnd2a.get("round_nonce")))
    # (b) a further code edit
    fr._write_code(d, 34, "cp2t12b")
    rnd3 = fr._open(d)
    check("[CP2-T12] (b) round 3 opens as a fix round naming the same single reviewer",
          rnd3.get("round") == 3 and fr._ids(rnd3) == [rid], (rnd3.get("round"), fr._ids(rnd3)))
    check("[CP2-T12] (b) kind after a block and fix_round set", fr._after_block(rnd3) and bool(rnd3.get("fix_round")),
          (fr._kind(rnd3), rnd3.get("fix_round")))
    check("[CP2-T12] (b) attempts / adv_rounds_total equal their values before (b)", fr._counters(d) == before,
          (before, fr._counters(d)))


def t13_a_pin_during_an_open_fix_round_opens_with_the_pinned_reviewer():
    d = fr._start("cp2t13")
    rnd1 = fr._open(d)
    rid = fr._pick(rnd1, "standards")
    fr._block(d, rnd1, {rid: [fr._finding("F1")]})
    fr._write_code(d, 30, "cp2t13a")
    rnd2 = fr._open(d)
    check("[CP2-T13] (fixture) round 2 is a one-reviewer fix round naming the standards reviewer",
          rnd2.get("round") == 2 and fr._ids(rnd2) == [rid] and fr._after_block(rnd2),
          (rnd2.get("round"), fr._ids(rnd2), fr._kind(rnd2)))
    ask, ac = _pin_and_approve(d, ["host-both"])
    check("[CP2-T13] the pin change is accepted", ac.returncode == 0, fr._out(ac))
    rnd3 = fr._open(d)
    check("[CP2-T13] the next reviewers call opens round 3, not round 2", rnd3.get("round") == 3, rnd3.get("round"))
    check("[CP2-T13] round 3 names exactly the pinned reviewer, not the carried one",
          fr._ids(rnd3) == ["host-both"], (rid, fr._ids(rnd3)))
    check("[CP2-T13] round 3 is not a fix round: kind is null", rnd3.get("kind") is None, fr._kind(rnd3))
    check("[CP2-T13] round 3 is not a fix round: fix_round is null", rnd3.get("fix_round") is None,
          rnd3.get("fix_round"))
    adv3 = fr._cps(d).get("adv_round") or {}
    check("[CP2-T13] state.json agrees: round 3, reviewers == [host-both], kind/fix_round null",
          adv3.get("round") == 3 and adv3.get("reviewers") == ["host-both"] and adv3.get("kind") is None
          and adv3.get("fix_round") is None, adv3)


# ------------------------------------------------------------------------------------------- CP2-X01, CP2-X02
# Extra checks added after the round-2 adversarial review (host-alt-correctness/C3, host-alt-standards/S2, S3):
# not part of the checkpoint's planned CP2-T01..T13 cases, so they are not in PLANNED above.

def x01_a_reordered_but_otherwise_unchanged_pin_does_not_reopen():
    """host-alt-correctness/C3, host-alt-standards/S2: the pin comparison is order-insensitive, so restating the
    same two ids in a different order must not reopen the round or discard counters."""
    d = fr._start("cp2x01", extra_cp={"reviewers": ["host-standards", "host-correctness"]})
    rnd1 = fr._open(d)
    check("[CP2-X01] (fixture) round 1 names the pinned pair in the configured order",
          fr._ids(rnd1) == ["host-standards", "host-correctness"], fr._ids(rnd1))
    nonce1 = (fr._cps(d).get("adv_round") or {}).get("nonce")
    before = fr._counters(d)
    ask, ac = _pin_and_approve(d, ["host-correctness", "host-standards"])  # same two ids, reordered
    check("[CP2-X01] the reordered pin is accepted with approve-changes", ac.returncode == 0, fr._out(ac))
    rnd2 = fr._open(d)
    check("[CP2-X01] still round 1: a reorder of the same pin is not a pin change",
          rnd2.get("round") == 1, rnd2.get("round"))
    adv2 = fr._cps(d).get("adv_round") or {}
    check("[CP2-X01] the same nonce", adv2.get("nonce") == nonce1, (nonce1, adv2.get("nonce")))
    check("[CP2-X01] attempts / adv_rounds_total are unchanged", fr._counters(d) == before,
          (before, fr._counters(d)))


def x02_legacy_pin_note_appears_only_while_a_legacy_round_with_a_live_pin_is_re_shown():
    """host-alt-standards/S3: legacy_pin_note is present (both --json and text) while a legacy round is
    re-shown unchanged with a live pin, and absent once that round is replaced by the pin-driven reopen."""
    d = fr._start("cp2x02", extra_cp={"reviewers": ["host-standards", "host-correctness"]})
    fr._open(d)
    _strip_new_adv_round_keys(d)
    rA = g.rx(d, "reviewers", "CP1", "--json")
    assert rA.returncode == 0, fr._out(rA)
    jA = json.loads(rA.stdout)
    check("[CP2-X02] (a) legacy round re-shown unchanged: legacy_pin_note is present in --json",
          bool(jA.get("legacy_pin_note")), jA.get("legacy_pin_note"))
    rAtext = g.rx(d, "reviewers", "CP1")
    check("[CP2-X02] (a) the text output also prints the legacy-pin line",
          "predates reviewers-pin tracking" in fr._out(rAtext), fr._out(rAtext))
    ask, ac = _pin_and_approve(d, ["host-both"])
    check("[CP2-X02] (b) the pin change is accepted", ac.returncode == 0, fr._out(ac))
    rB = g.rx(d, "reviewers", "CP1", "--json")
    assert rB.returncode == 0, fr._out(rB)
    jB = json.loads(rB.stdout)
    check("[CP2-X02] (b) the pin change reopened the round (round 2, host-both)",
          jB.get("round") == 2 and fr._ids(jB) == ["host-both"], (jB.get("round"), fr._ids(jB)))
    check("[CP2-X02] (b) legacy_pin_note is absent on the fresh round that replaced the legacy one",
          "legacy_pin_note" not in jB, jB.get("legacy_pin_note"))
    rBtext = g.rx(d, "reviewers", "CP1")
    check("[CP2-X02] (b) the text output no longer prints the legacy-pin line",
          "predates reviewers-pin tracking" not in fr._out(rBtext), fr._out(rBtext))


CASES = [
    ("CP2-T01", t01_pin_to_one_reviewer_opens_a_new_round_naming_only_it),
    ("CP2-T02", t02_asking_again_with_no_change_shows_the_same_round),
    ("CP2-T03", t03_a_restated_unchanged_pin_does_not_reopen),
    ("CP2-T04", t04_an_unpinned_steps_unrelated_plan_edit_keeps_its_round),
    ("CP2-T05", t05_after_a_pin_reopens_asking_again_does_not_keep_reopening),
    ("CP2-T06", t06_removing_a_pin_reopens_with_the_steps_default_reviewers),
    ("CP2-T07", t07_a_pin_reopen_of_an_unreviewed_round_costs_no_round),
    ("CP2-T08", t08_an_unapproved_pin_edit_opens_no_round),
    ("CP2-T09", t09_an_old_shape_round_reopens_on_a_differing_pin),
    ("CP2-T10", t10_a_recorded_blocked_round_re_pinned_before_the_fix_opens_with_the_new_pin),
    ("CP2-T11", t11_a_code_change_and_a_pin_change_together_open_exactly_one_new_round),
    ("CP2-T12", t12_an_open_unreviewed_fix_round_keeps_its_reviewer_and_carries_it_forward),
    ("CP2-T13", t13_a_pin_during_an_open_fix_round_opens_with_the_pinned_reviewer),
    ("CP2-X01", x01_a_reordered_but_otherwise_unchanged_pin_does_not_reopen),
    ("CP2-X02", x02_legacy_pin_note_appears_only_while_a_legacy_round_with_a_live_pin_is_re_shown),
]


# ------------------------------------------------------------------------------------------- CP2-X03, CP2-X04
# Extra checks added after the round-3 adversarial review (host-alt-correctness/H1): a legacy round (no
# reviewers_pin key) must never reopen just because its resolved reviewers differ from the live pin (narrowing or
# substitution), and a real approved pin edit on lane R's exact shape reopens it on the very next call.

def x03_a_narrowed_legacy_round_with_an_unchanged_pin_does_not_reopen():
    """host-alt-correctness/H1 (T-A): pinned from the start, round 2 is a fix round narrowed to the one lens with
    an open finding (a subset of the pin). Stripped to legacy shape with no approve-changes at all, asking again
    must keep that same round and nonce."""
    d = fr._start("cp2x03", extra_cp={"reviewers": ["host-standards", "host-correctness"]})
    rnd1 = fr._open(d)
    rid = fr._pick(rnd1, "standards")
    fr._block(d, rnd1, {rid: [fr._finding("F1")]})
    fr._write_code(d, 30, "cp2x03")
    rnd2 = fr._open(d)
    check("[CP2-X03] (fixture) round 2 is narrowed to a subset of the unchanged pin",
          rnd2.get("round") == 2 and fr._ids(rnd2) == [rid], (rnd2.get("round"), fr._ids(rnd2)))
    nonce2 = rnd2["round_nonce"]
    _strip_new_adv_round_keys(d)
    r = g.rx(d, "reviewers", "CP1", "--json")
    assert r.returncode == 0, fr._out(r)
    j = json.loads(r.stdout)
    check("[CP2-X03] still round 2 naming the same narrowed reviewer",
          j.get("round") == 2 and fr._ids(j) == [rid], (j.get("round"), fr._ids(j)))
    check("[CP2-X03] the same nonce", j.get("round_nonce") == nonce2, (nonce2, j.get("round_nonce")))


def x04_lane_r_shape_one_pin_and_approve_reopens_on_the_next_call():
    """host-alt-correctness/H1 (T-B): a legacy round on an UNPINNED checkpoint; pinning it to a different
    reviewer and approving once makes the very next reviewers call open a new round with the pinned reviewer."""
    d = fr._start("cp2x04")
    rnd1 = fr._open(d)
    ids1 = fr._ids(rnd1)
    nonce1 = rnd1["round_nonce"]
    _strip_new_adv_round_keys(d)
    ask, ac = _pin_and_approve(d, ["host-both"])
    check("[CP2-X04] the pin change is accepted with approve-changes", ac.returncode == 0, fr._out(ac))
    r = g.rx(d, "reviewers", "CP1", "--json")
    assert r.returncode == 0, fr._out(r)
    j = json.loads(r.stdout)
    check("[CP2-X04] the single next call opens round 2 naming only host-both",
          j.get("round") == 2 and fr._ids(j) == ["host-both"] and "host-both" not in ids1,
          (j.get("round"), ids1, fr._ids(j)))
    check("[CP2-X04] a new nonce", bool(j.get("round_nonce")) and j.get("round_nonce") != nonce1,
          (nonce1, j.get("round_nonce")))


CASES += [
    ("CP2-X03", x03_a_narrowed_legacy_round_with_an_unchanged_pin_does_not_reopen),
    ("CP2-X04", x04_lane_r_shape_one_pin_and_approve_reopens_on_the_next_call),
]


# ------------------------------------------------------------------------------------------------- CP2-X05
# Extra check added after the final cross-vendor review (codex X1): approve-changes on an UNRELATED checkpoint
# edit must not stamp a narrowed legacy round's resolved reviewers as its pin baseline (a false reopen next call).

def x05_an_unrelated_approved_edit_on_a_narrowed_legacy_round_does_not_reopen():
    """codex X1: pinned to two reviewers, round 2 narrowed to one, stripped to legacy shape; an approved summary
    edit (pin untouched) must leave the next reviewers call on the same round and nonce."""
    d = fr._start("cp2x05", extra_cp={"reviewers": ["host-standards", "host-correctness"]})
    rnd1 = fr._open(d)
    rid = fr._pick(rnd1, "standards")
    fr._block(d, rnd1, {rid: [fr._finding("F1")]})
    fr._write_code(d, 30, "cp2x05")
    rnd2 = fr._open(d)
    check("[CP2-X05] (fixture) round 2 is narrowed to a subset of the unchanged pin",
          rnd2.get("round") == 2 and fr._ids(rnd2) == [rid], (rnd2.get("round"), fr._ids(rnd2)))
    nonce2 = rnd2["round_nonce"]
    _strip_new_adv_round_keys(d)
    _edit_summary(d, "an unrelated summary edit")
    ask, ac = _approve_changes(d)
    check("[CP2-X05] the unrelated edit is accepted with approve-changes", ac.returncode == 0, fr._out(ac))
    r = g.rx(d, "reviewers", "CP1", "--json")
    assert r.returncode == 0, fr._out(r)
    j = json.loads(r.stdout)
    check("[CP2-X05] still round 2 naming the same narrowed reviewer",
          j.get("round") == 2 and fr._ids(j) == [rid], (j.get("round"), fr._ids(j)))
    check("[CP2-X05] the same nonce", j.get("round_nonce") == nonce2, (nonce2, j.get("round_nonce")))


CASES += [
    ("CP2-X05", x05_an_unrelated_approved_edit_on_a_narrowed_legacy_round_does_not_reopen),
]


# ------------------------------------------------------------------------------------------- CP2-X06, CP2-X07
# Extra checks added after two independent adversarial reviewers (host-alt-standards, host-alt-correctness)
# found that cmd_approve_changes' legacy-pin recovery must still reopen a narrowed/substituted legacy round
# when the pin genuinely changed to a reviewer the round never had -- the round's own (un-substituted) reviewers
# are no longer a subset of the new live pin, so the mismatch must stamp a baseline that reopens next call,
# never leave the round legacy (which would reproduce the original deadlock this whole run exists to fix: lane R
# CP4 round 8's exact shape).

def x06_a_narrowed_legacy_round_pinned_to_a_genuinely_different_reviewer_reopens_on_the_next_call():
    """host-alt-standards / host-alt-correctness: pinned to two reviewers from the start, round 2 narrowed by a
    block+fix to one of them, stripped to legacy shape (no reviewers_pin key), then pinned to a THIRD reviewer
    that was never in the original two-reviewer pin. Only the round's own new keys are stripped here, so the
    approval snapshot's `approved.reviewers` map (with the original two-reviewer pin recorded for this
    checkpoint) is still present -- this drives the PRIMARY, stored-baseline branch, not the no-baseline one:
    the stored pin differs from the live pin, so this proves that branch stamps a baseline and the round reopens
    on the very next call, rather than staying legacy forever."""
    d = fr._start("cp2x06", extra_cp={"reviewers": ["host-standards", "host-correctness"]})
    rnd1 = fr._open(d)
    rid = fr._pick(rnd1, "standards")
    fr._block(d, rnd1, {rid: [fr._finding("F1")]})
    fr._write_code(d, 30, "cp2x06")
    rnd2 = fr._open(d)
    check("[CP2-X06] (fixture) round 2 is narrowed to a subset of the original two-reviewer pin",
          rnd2.get("round") == 2 and fr._ids(rnd2) == [rid], (rnd2.get("round"), fr._ids(rnd2)))
    nonce2 = rnd2["round_nonce"]
    _strip_new_adv_round_keys(d)
    ask, ac = _pin_and_approve(d, ["host-both"])
    check("[CP2-X06] pinning to a genuinely different reviewer is accepted with approve-changes",
          ac.returncode == 0, fr._out(ac))
    r = fr._open(d)
    check("[CP2-X06] the very next reviewers call reopens the round (round number rises above 2)",
          (r.get("round") or 0) > 2, r.get("round"))
    check("[CP2-X06] the new round names exactly the newly pinned reviewer",
          fr._ids(r) == ["host-both"], fr._ids(r))
    check("[CP2-X06] a fresh round_nonce (different from the narrowed round's)",
          bool(r.get("round_nonce")) and r.get("round_nonce") != nonce2, (nonce2, r.get("round_nonce")))


def x07_approve_changes_never_mutates_a_recorded_legacy_rounds_own_stored_data():
    """Control for the X06 fix: the legacy-pin stamping block only ever applies to an open, UNRECORDED round
    (`not rnd.get("recorded")`). A round that was already recorded (blocked and triaged) must come out of
    approve-changes with its own adv_round data byte-for-byte unchanged, even when the live pin genuinely
    changes in the same approval -- no reviewers_pin key appears on it, and nothing else about it moves either."""
    d = fr._start("cp2x07", extra_cp={"reviewers": ["host-standards", "host-correctness"]})
    rnd1 = fr._open(d)
    rid = fr._pick(rnd1, "standards")
    fr._block(d, rnd1, {rid: [fr._finding("F1")]})
    _strip_new_adv_round_keys(d)
    before = dict(fr._cps(d).get("adv_round") or {})
    check("[CP2-X07] (fixture) the recorded round has no reviewers_pin key of its own",
          "reviewers_pin" not in before, before)
    ask, ac = _pin_and_approve(d, ["host-both"])
    check("[CP2-X07] the pin change is accepted with approve-changes", ac.returncode == 0, fr._out(ac))
    after = dict(fr._cps(d).get("adv_round") or {})
    check("[CP2-X07] still no reviewers_pin key on the recorded round", "reviewers_pin" not in after, after)
    check("[CP2-X07] the recorded round's own stored data is byte-for-byte unchanged by approve-changes",
          after == before, (before, after))


CASES += [
    ("CP2-X06", x06_a_narrowed_legacy_round_pinned_to_a_genuinely_different_reviewer_reopens_on_the_next_call),
    ("CP2-X07", x07_approve_changes_never_mutates_a_recorded_legacy_rounds_own_stored_data),
]


# ------------------------------------------------------------------------------------------- CP2-X08..X10
# CP4 round-2 H6/S3: every case above builds its run with the new code, so state.json's `approved.reviewers`
# map is always present -- even the "legacy round" cases only strip the round's own keys, never the approval
# snapshot. A real legacy round only exists in a run whose first `approve-changes` under this code finds no
# `reviewers` map at all (an approval written by older code), which is the ambiguous branch H4/S4/X1/codex X1
# found false-reopening. These three cases combine `_strip_new_adv_round_keys` with the new
# `_strip_approved_reviewers` helper to build that real shape and drive it through the fixed ambiguous branch.
# CP4 round-3 codex-standards/X3 then proved the containment-based "narrowed vs equality" heuristic this branch
# used has a real gap (a narrowed round's pin can grow or swap while still containing the narrowed subset, and
# containment alone misses it). The final design drops that heuristic entirely: ANY non-empty live pin on a
# no-baseline legacy round unconditionally stamps reviewers_pin=None, so the branch always reopens once (free
# only while the round's folder holds no review file yet; otherwise it costs an attempt like any other replaced
# reviewed round) and is exact afterward. X08 below reflects that: it now reopens on every pin-adjacent
# approve-changes, not just a genuine pin change.

def x08_a_narrowed_real_legacy_round_with_an_unchanged_pin_reopens_once_then_is_exact():
    """H4/S4/X1/codex X1's shape, under the final design (CP4 round-3 codex-standards/X3): containment was
    proven to miss a genuine pin change on a narrowed round (e.g. [std,cor] narrowed to [std], pin becomes
    [std,host-both]: {std} subset-of {std,host-both}, so containment wrongly says "unchanged"). The fix drops
    that heuristic: ANY approve-changes on a no-baseline legacy round with a non-empty live pin unconditionally
    stamps reviewers_pin=None, so the very next `reviewers` call always reopens once -- even on an unrelated
    summary edit with the pin itself untouched -- naming the step's full current (unnarrowed) pin. This is the
    one-time safe default, free only while the round's folder holds no review file yet (CP2-T07's own scope);
    after this reopen the fresh round stamps a real baseline, so every later approve-changes on this checkpoint
    is exact (see X09 for the "genuinely changed pin" case this reopen also always covers)."""
    d = fr._start("cp2x08", extra_cp={"reviewers": ["host-standards", "host-correctness"]})
    rnd1 = fr._open(d)
    rid = fr._pick(rnd1, "standards")
    fr._block(d, rnd1, {rid: [fr._finding("F1")]})
    fr._write_code(d, 30, "cp2x08")
    rnd2 = fr._open(d)
    check("[CP2-X08] (fixture) round 2 is narrowed to a subset of the unchanged pin",
          rnd2.get("round") == 2 and fr._ids(rnd2) == [rid], (rnd2.get("round"), fr._ids(rnd2)))
    nonce2 = rnd2["round_nonce"]
    _strip_new_adv_round_keys(d)
    _strip_approved_reviewers(d)
    _edit_summary(d, "an unrelated summary edit (real legacy shape)")
    ask, ac = _approve_changes(d)
    check("[CP2-X08] the unrelated edit is accepted with approve-changes", ac.returncode == 0, fr._out(ac))
    r = g.rx(d, "reviewers", "CP1", "--json")
    assert r.returncode == 0, fr._out(r)
    j = json.loads(r.stdout)
    check("[CP2-X08] the safe default reopens the round once (round number rises above 2)",
          (j.get("round") or 0) > 2, j.get("round"))
    check("[CP2-X08] the new round names the step's full, unnarrowed two-reviewer pin",
          sorted(fr._ids(j)) == ["host-correctness", "host-standards"], fr._ids(j))
    check("[CP2-X08] a fresh round_nonce (different from the narrowed round's)",
          bool(j.get("round_nonce")) and j.get("round_nonce") != nonce2, (nonce2, j.get("round_nonce")))


def x09_a_narrowed_real_legacy_round_pinned_to_a_genuinely_different_reviewer_reopens():
    """The X06 shape, but with the approval snapshot's `reviewers` map also dropped (the real legacy shape):
    pinned to two reviewers, round 2 narrowed to one by a block+fix, stripped to legacy with no approved.reviewers
    at all, then pinned to a THIRD reviewer never in the original pin. The round's own (un-substituted)
    reviewer is not a subset of the new live pin, so the next call must reopen it -- never leave it legacy
    forever, which would reproduce the original deadlock bug for exactly the population (a real upgraded run)
    this checkpoint is about."""
    d = fr._start("cp2x09", extra_cp={"reviewers": ["host-standards", "host-correctness"]})
    rnd1 = fr._open(d)
    rid = fr._pick(rnd1, "standards")
    fr._block(d, rnd1, {rid: [fr._finding("F1")]})
    fr._write_code(d, 30, "cp2x09")
    rnd2 = fr._open(d)
    check("[CP2-X09] (fixture) round 2 is narrowed to a subset of the original two-reviewer pin",
          rnd2.get("round") == 2 and fr._ids(rnd2) == [rid], (rnd2.get("round"), fr._ids(rnd2)))
    nonce2 = rnd2["round_nonce"]
    _strip_new_adv_round_keys(d)
    _strip_approved_reviewers(d)
    ask, ac = _pin_and_approve(d, ["host-both"])
    check("[CP2-X09] pinning to a genuinely different reviewer is accepted with approve-changes",
          ac.returncode == 0, fr._out(ac))
    r = fr._open(d)
    check("[CP2-X09] the very next reviewers call reopens the round (round number rises above 2)",
          (r.get("round") or 0) > 2, r.get("round"))
    check("[CP2-X09] the new round names exactly the newly pinned reviewer",
          fr._ids(r) == ["host-both"], fr._ids(r))
    check("[CP2-X09] a fresh round_nonce (different from the narrowed round's)",
          bool(r.get("round_nonce")) and r.get("round_nonce") != nonce2, (nonce2, r.get("round_nonce")))


def x10_control_a_narrowed_real_legacy_round_with_the_pin_removed_does_not_reopen():
    """CONTROL, not a fix: documents the accepted, information-theoretic migration limit (CP4 H5/S5, closed via
    a director-signed accept-finding, not by code). Same real legacy shape as X08/X09, but the live pin is
    REMOVED entirely (`reviewers` key deleted) instead of changed. A never-pinned round's own reviewers are
    structurally identical to a pinned-then-removed round's, so pin removal on a no-baseline legacy round is
    undetectable no matter what the fix guesses; the final design's unconditional stamp only fires when the
    live pin is non-empty, so an empty live pin leaves this round's reviewers_pin untouched and it does NOT
    reopen even though the pin genuinely went away. This must keep passing as a green control: if a future
    change makes this reopen, that is a deliberate decision to close H5/S5, and it should show up here as a
    changed test, not a silent drift."""
    d = fr._start("cp2x10", extra_cp={"reviewers": ["host-standards", "host-correctness"]})
    rnd1 = fr._open(d)
    rid = fr._pick(rnd1, "standards")
    fr._block(d, rnd1, {rid: [fr._finding("F1")]})
    fr._write_code(d, 30, "cp2x10")
    rnd2 = fr._open(d)
    check("[CP2-X10] (fixture) round 2 is narrowed to a subset of the original two-reviewer pin",
          rnd2.get("round") == 2 and fr._ids(rnd2) == [rid], (rnd2.get("round"), fr._ids(rnd2)))
    nonce2 = rnd2["round_nonce"]
    _strip_new_adv_round_keys(d)
    _strip_approved_reviewers(d)
    ask, ac = _pin_and_approve(d, None)
    check("[CP2-X10] removing the pin is accepted with approve-changes", ac.returncode == 0, fr._out(ac))
    r = g.rx(d, "reviewers", "CP1", "--json")
    assert r.returncode == 0, fr._out(r)
    j = json.loads(r.stdout)
    check("[CP2-X10] (accepted limit) still round 2 naming the same narrowed reviewer, even with no live pin",
          j.get("round") == 2 and fr._ids(j) == [rid], (j.get("round"), fr._ids(j)))
    check("[CP2-X10] the same nonce", j.get("round_nonce") == nonce2, (nonce2, j.get("round_nonce")))


CASES += [
    ("CP2-X08", x08_a_narrowed_real_legacy_round_with_an_unchanged_pin_reopens_once_then_is_exact),
    ("CP2-X09", x09_a_narrowed_real_legacy_round_pinned_to_a_genuinely_different_reviewer_reopens),
    ("CP2-X10", x10_control_a_narrowed_real_legacy_round_with_the_pin_removed_does_not_reopen),
]


# ------------------------------------------------------------------------------------------- CP2-X11, CP2-X12
# CP4 round-2 H6/S3 (continued): X08-X10 above all start from a checkpoint pinned from the start, so none of
# them exercises an UNPINNED checkpoint on the real legacy shape (no approved.reviewers map at all) -- the exact
# shape lane R's never-pinned checkpoints take on their very first post-upgrade approve-changes.

def x11_a_never_pinned_real_legacy_round_keeps_its_round_on_an_unrelated_edit():
    """H6/S3: an unpinned checkpoint's round 1, with the round's own keys stripped to legacy shape AND
    approved.reviewers dropped entirely (no prior pin baseline of any kind, for this checkpoint or the run).
    An approved, unrelated summary edit must not reopen it: same round, same reviewer ids, same nonce."""
    d = fr._start("cp2x11")
    rnd1 = fr._open(d)
    ids1 = fr._ids(rnd1)
    nonce1 = (fr._cps(d).get("adv_round") or {}).get("nonce")
    _strip_new_adv_round_keys(d)
    _strip_approved_reviewers(d)
    _edit_summary(d, "an unrelated summary edit (never-pinned, real legacy shape)")
    ask, ac = _approve_changes(d)
    check("[CP2-X11] the unrelated edit is accepted with approve-changes", ac.returncode == 0, fr._out(ac))
    r = g.rx(d, "reviewers", "CP1", "--json")
    assert r.returncode == 0, fr._out(r)
    j = json.loads(r.stdout)
    check("[CP2-X11] still round 1 with the same reviewer ids", j.get("round") == 1 and fr._ids(j) == ids1,
          (j.get("round"), ids1, fr._ids(j)))
    check("[CP2-X11] the same nonce", j.get("round_nonce") == nonce1, (nonce1, j.get("round_nonce")))


def x12_a_never_pinned_real_legacy_round_reopens_once_a_pin_is_added():
    """H6/S3: same never-pinned, real legacy shape as X11. Pinning the checkpoint to a reviewer it never had
    and approving makes the very next reviewers call reopen with exactly that reviewer, a fresh nonce."""
    d = fr._start("cp2x12")
    rnd1 = fr._open(d)
    ids1 = fr._ids(rnd1)
    nonce1 = (fr._cps(d).get("adv_round") or {}).get("nonce")
    _strip_new_adv_round_keys(d)
    _strip_approved_reviewers(d)
    ask, ac = _pin_and_approve(d, ["host-both"])
    check("[CP2-X12] pinning the never-pinned checkpoint is accepted with approve-changes", ac.returncode == 0,
          fr._out(ac))
    r = g.rx(d, "reviewers", "CP1", "--json")
    assert r.returncode == 0, fr._out(r)
    j = json.loads(r.stdout)
    check("[CP2-X12] the next reviewers call opens a new round naming only host-both",
          (j.get("round") or 0) > 1 and fr._ids(j) == ["host-both"] and "host-both" not in ids1,
          (j.get("round"), ids1, fr._ids(j)))
    check("[CP2-X12] a fresh nonce", bool(j.get("round_nonce")) and j.get("round_nonce") != nonce1,
          (nonce1, j.get("round_nonce")))


CASES += [
    ("CP2-X11", x11_a_never_pinned_real_legacy_round_keeps_its_round_on_an_unrelated_edit),
    ("CP2-X12", x12_a_never_pinned_real_legacy_round_reopens_once_a_pin_is_added),
]


# ------------------------------------------------------------------------------------------------- CP2-X13
# The final design drops containment/narrowed-vs-equality heuristics entirely: on a no-baseline legacy round
# (approved.reviewers dropped entirely), ANY non-empty live pin unconditionally stamps reviewers_pin=None, so
# the round always reopens once regardless of whether the new pin happens to contain the old one. This case
# covers a round pinned directly from round-open and never narrowed (its own reviewers exactly mirror the pin
# at open time), with the pin then GROWN (a reviewer added) -- proving the unconditional stamp still reopens it
# even though the round's reviewers remain a SUBSET of the new, wider pin.

def x13_a_non_narrowed_real_legacy_round_pinned_directly_reopens_when_the_pin_grows():
    """Pinned from the very start to a single reviewer (host-standards); round 1 opens with exactly that
    reviewer and is never narrowed (no block, no fix round). Stripped to the real legacy shape (round's own new
    keys dropped AND approved.reviewers dropped entirely), then the pin is grown to add a second reviewer
    (host-correctness) alongside the original one. The round's own reviewer (host-standards) is still a SUBSET
    of the new pin, so a containment-based test would wrongly call this unchanged; the final design's
    unconditional None stamp (any non-empty live pin on a no-baseline round reopens it) reopens it regardless,
    naming both pinned reviewers."""
    d = fr._start("cp2x13", extra_cp={"reviewers": ["host-standards"]})
    rnd1 = fr._open(d)
    ids1 = fr._ids(rnd1)
    check("[CP2-X13] (fixture) round 1 is pinned directly to the single reviewer, not narrowed",
          rnd1.get("round") == 1 and ids1 == ["host-standards"] and not rnd1.get("narrowed"),
          (rnd1.get("round"), ids1, rnd1.get("narrowed")))
    nonce1 = (fr._cps(d).get("adv_round") or {}).get("nonce")
    _strip_new_adv_round_keys(d)
    _strip_approved_reviewers(d)
    ask, ac = _pin_and_approve(d, ["host-standards", "host-correctness"])
    check("[CP2-X13] growing the pin is accepted with approve-changes", ac.returncode == 0, fr._out(ac))
    r = g.rx(d, "reviewers", "CP1", "--json")
    assert r.returncode == 0, fr._out(r)
    j = json.loads(r.stdout)
    check("[CP2-X13] the very next reviewers call reopens the round (round number rises above 1)",
          (j.get("round") or 0) > 1, j.get("round"))
    check("[CP2-X13] the new round names exactly the grown pin",
          sorted(fr._ids(j)) == ["host-correctness", "host-standards"], fr._ids(j))
    check("[CP2-X13] a fresh round_nonce (different from round 1's)",
          bool(j.get("round_nonce")) and j.get("round_nonce") != nonce1, (nonce1, j.get("round_nonce")))


CASES += [
    ("CP2-X13", x13_a_non_narrowed_real_legacy_round_pinned_directly_reopens_when_the_pin_grows),
]


# ------------------------------------------------------------------------------------------------- CP2-X14
# CP4 round-3 codex-standards/X3's exact counter-example: a narrowed round whose new pin still CONTAINS the
# narrowed reviewer, but adds a different one and drops the one that got narrowed away. The old containment
# check ("is the round's own un-substituted reviewer still a subset of the live pin?") says yes here, and
# wrongly calls this unchanged -- this is the gap that made the final design drop containment entirely.

def x14_a_narrowed_real_legacy_round_pinned_to_an_added_reviewer_that_still_contains_the_narrowed_one_reopens():
    """Pinned to two reviewers, round 2 narrowed to host-standards by a block+fix (the real legacy shape, same
    as X08-X10). The pin is then changed to [host-standards, host-both]: host-standards (the narrowed
    survivor) stays in the pin, host-correctness is dropped, and host-both is added. Containment alone would
    say {host-standards} subset-of {host-standards, host-both} and call this "unchanged" -- codex-standards/X3's
    proof that the old heuristic has a real gap. The final, unconditional safe default must still reopen this,
    naming exactly the new two-reviewer pin."""
    d = fr._start("cp2x14", extra_cp={"reviewers": ["host-standards", "host-correctness"]})
    rnd1 = fr._open(d)
    rid = fr._pick(rnd1, "standards")
    fr._block(d, rnd1, {rid: [fr._finding("F1")]})
    fr._write_code(d, 30, "cp2x14")
    rnd2 = fr._open(d)
    check("[CP2-X14] (fixture) round 2 is narrowed to a subset of the original two-reviewer pin",
          rnd2.get("round") == 2 and fr._ids(rnd2) == [rid], (rnd2.get("round"), fr._ids(rnd2)))
    nonce2 = rnd2["round_nonce"]
    _strip_new_adv_round_keys(d)
    _strip_approved_reviewers(d)
    ask, ac = _pin_and_approve(d, ["host-standards", "host-both"])
    check("[CP2-X14] pinning to a containing-but-different pair is accepted with approve-changes",
          ac.returncode == 0, fr._out(ac))
    r = g.rx(d, "reviewers", "CP1", "--json")
    assert r.returncode == 0, fr._out(r)
    j = json.loads(r.stdout)
    check("[CP2-X14] the very next reviewers call reopens the round (round number rises above 2)",
          (j.get("round") or 0) > 2, j.get("round"))
    check("[CP2-X14] the new round names exactly the new pin",
          sorted(fr._ids(j)) == ["host-both", "host-standards"], fr._ids(j))
    check("[CP2-X14] a fresh round_nonce (different from the narrowed round's)",
          bool(j.get("round_nonce")) and j.get("round_nonce") != nonce2, (nonce2, j.get("round_nonce")))


CASES += [
    ("CP2-X14", x14_a_narrowed_real_legacy_round_pinned_to_an_added_reviewer_that_still_contains_the_narrowed_one_reopens),
]


# ------------------------------------------------------------------------------------------------- CP2-X15
# CP4 round-4 omp-glm-standards/X1: the stamping loop in cmd_approve_changes must cover EVERY checkpoint with an
# open, unrecorded, pin-less round, not only the checkpoint(s) named in this particular approval's own diff --
# the snapshot rewrite at the end of cmd_approve_changes records every checkpoint's live `reviewers` into
# `approved.reviewers` unconditionally, so a checkpoint left out of the stamping loop would have its live pin
# silently baked into that new baseline the first time ANY other checkpoint is approved, permanently losing its
# one-time migration reopen.

def x15_an_edit_to_a_different_checkpoint_still_stamps_this_checkpoints_open_legacy_round():
    """Two checkpoints, CP1 and CP2, sharing the real no-baseline legacy shape (approved.reviewers dropped
    entirely from state.json -- a run-wide shape, not per-checkpoint). CP1 is pinned from the start and has an
    open, unrecorded legacy round (its own new adv_round keys stripped too, so it has no reviewers_pin of its
    own). The approve-changes call in this test edits only CP2 (an unrelated summary field) -- CP1 is never
    named in this approval's diff. CP1's round must still get its safe-default reopen on the very next
    `reviewers CP1` call: the stamping loop must not be scoped to only the checkpoint(s) this particular
    approval happened to touch."""
    d = g.project("cp2x15", cps=2, extra_cp={"risk": "normal", "reviewers": ["host-standards", "host-correctness"]})
    g.approve_plan(d)
    g.to_adversarial(d, "CP1")
    rnd1 = fr._open(d)
    check("[CP2-X15] (fixture) CP1's round 1 is pinned directly to both reviewers, not narrowed",
          rnd1.get("round") == 1 and sorted(fr._ids(rnd1)) == ["host-correctness", "host-standards"]
          and not rnd1.get("narrowed"), (rnd1.get("round"), fr._ids(rnd1), rnd1.get("narrowed")))
    nonce1 = (fr._cps(d).get("adv_round") or {}).get("nonce")
    _strip_new_adv_round_keys(d)
    _strip_approved_reviewers(d)
    _edit_summary(d, "an unrelated summary edit on CP2, not CP1", cp="CP2")
    ask, ac = _approve_changes(d)
    check("[CP2-X15] CP2's unrelated edit is accepted with approve-changes", ac.returncode == 0, fr._out(ac))
    r = g.rx(d, "reviewers", "CP1", "--json")
    assert r.returncode == 0, fr._out(r)
    j = json.loads(r.stdout)
    check("[CP2-X15] CP1's round still reopens even though this approval only named CP2 (round rises above 1)",
          (j.get("round") or 0) > 1, j.get("round"))
    check("[CP2-X15] the new round names exactly CP1's pinned pair",
          sorted(fr._ids(j)) == ["host-correctness", "host-standards"], fr._ids(j))
    check("[CP2-X15] a fresh round_nonce (different from round 1's)",
          bool(j.get("round_nonce")) and j.get("round_nonce") != nonce1, (nonce1, j.get("round_nonce")))


CASES += [
    ("CP2-X15", x15_an_edit_to_a_different_checkpoint_still_stamps_this_checkpoints_open_legacy_round),
]


# ------------------------------------------------------------------------------------------------- CP2-X16
# CP4 round-5 omp-glm-correctness/X1: the same stamping loop (`_stamp_legacy_round_baselines`) must run from
# every command that writes the unconditional approval snapshot -- `approve-plan`, `plan-extend` and
# `approve-changes` -- not only the `approve-changes` call site CP2-X15 already covers. This drives
# `plan-extend` itself as the trigger.

def x16_plan_extend_also_stamps_a_different_checkpoints_open_legacy_round():
    """`plan-extend` cannot be reached until the run's only checkpoint (CP1) is already `complete`: `feedback`
    only runs at `awaiting_checkpoint_review` / `awaiting_final_review` / `done`, and `complete` requires every
    required gate -- including `adversarial` -- to already be `passed` (skills/ratchet-orchestrator/scripts/
    ratchet.py cmd_complete, ~line 14195-14233). A checkpoint whose own round is genuinely still open and
    unrecorded can therefore never coexist with a phase that allows `plan-extend` to run, unlike CP2-X15's
    approve-changes case where CP1 is still mid-build. So this case drives CP1 through one real, clean round and
    `complete` first, then rolls its already-recorded round back (`_strip_new_adv_round_keys`, plus an explicit
    drop of `recorded` this case adds, both via `dt.mutate`) to the open, pin-less legacy shape
    `_stamp_legacy_round_baselines` is written for. Dropping `recorded` is also the only way to isolate the
    helper's own effect here from `cmd_reviewers`' separate, unrelated rule that an ALREADY-recorded round always
    opens fresh on the next ask regardless of any pin (ratchet.py cmd_reviewers, the `rnd.get("recorded")` arm of
    its reopen condition) -- left alone, that rule would reopen the round on its own and make this case pass
    whether or not `_stamp_legacy_round_baselines` ever ran.

    `plan-extend` also independently refuses any edit to an EXISTING checkpoint's own config, including its
    `reviewers` field (cmd_plan_extend's `edited` check, keyed on the whole checkpoint's hash) -- so CP1's pin is
    never rewritten here, unlike CP2-X15/X10's "genuinely different reviewer" edits. Instead this relies on the
    same unconditional, no-stored-baseline stamp CP2-X15 itself exercises: `_strip_approved_reviewers` drops
    `approved.reviewers` entirely, so the stamping loop has no prior pin on record for CP1 and stamps
    `reviewers_pin = None` unconditionally.

    The proof itself is read straight from state.json rather than from a further `reviewers CP1` call: once
    `plan-extend` appends CP2, `st["current"]` moves to CP2 and `_cur` refuses `reviewers CP1` outright ("CP1 is
    not the current checkpoint (CP2). Checkpoints run strictly in order.", ratchet.py `_cur`) -- a checkpoint
    plan-extend has moved past can never be asked about again through the CLI, confirmed by running this case
    before this paragraph was written and hitting exactly that refusal. So this checks the one place the stamp's
    effect is still observable: CP1's own `adv_round.reviewers_pin` in state.json, absent before `plan-extend`
    (stripped above) and stamped to `None` by it."""
    d = fr._start("cp2x16", extra_cp={"reviewers": ["host-standards", "host-correctness"]})
    rnd1 = fr._open(d)
    check("[CP2-X16] (fixture) CP1's round 1 is pinned directly to both reviewers, not narrowed",
          rnd1.get("round") == 1 and sorted(fr._ids(rnd1)) == ["host-correctness", "host-standards"]
          and not rnd1.get("narrowed"), (rnd1.get("round"), fr._ids(rnd1), rnd1.get("narrowed")))
    nonce1 = (fr._cps(d).get("adv_round") or {}).get("nonce")
    fr._approve(d, rnd1)
    r = g.rx(d, "complete", "CP1")
    check("[CP2-X16] (fixture) CP1 completes cleanly", r.returncode == 0, fr._out(r))

    _strip_new_adv_round_keys(d)
    _strip_approved_reviewers(d)

    def _drop_recorded(st):
        (st["checkpoints"]["CP1"].get("adv_round") or {}).pop("recorded", None)
    dt.mutate(d, _drop_recorded)

    before_adv = fr._cps(d).get("adv_round") or {}
    check("[CP2-X16] (fixture) CP1's round is now legacy-shaped: open (not recorded), no reviewers_pin key",
          not before_adv.get("recorded") and "reviewers_pin" not in before_adv, before_adv)

    run = Path(d) / ".ratchet/runs" / SLUG
    plan = json.loads((run / "checkpoints.json").read_text())
    plan["checkpoints"].append({
        "id": "CP2", "title": "Step 2", "summary": "s", "done_when": ["works"],
        "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
        "tests": {"files": ["tests/cp2.test.js"], "command": "node --test tests/cp2.test.js"}})
    (run / "checkpoints.json").write_text(json.dumps(plan))
    (run / "tests" / "CP2.json").write_text(json.dumps({"cases": [{"id": "CP2-T01", "layer": "code"}]}))

    g.say(d, "can we also add a second step")
    fb = g.rx(d, "feedback", "--text", "also add a second step")
    check("[CP2-X16] feedback is recorded", fb.returncode == 0, fr._out(fb))
    pe = g.rx(d, "plan-extend")
    check("[CP2-X16] plan-extend succeeds (the new checkpoint is added, CP1 itself was never edited)",
          pe.returncode == 0, fr._out(pe))

    no_longer_current = g.rx(d, "reviewers", "CP1", "--json")
    check("[CP2-X16] (ground truth) CP1 is no longer the current checkpoint after plan-extend, so `reviewers "
          "CP1` itself is refused -- this is why the proof below reads state.json instead of calling it again",
          no_longer_current.returncode != 0 and "not the current checkpoint" in fr._out(no_longer_current),
          fr._out(no_longer_current))

    after_adv = fr._cps(d).get("adv_round") or {}
    check("[CP2-X16] plan-extend's own call to _stamp_legacy_round_baselines stamped CP1's legacy round "
          "(reviewers_pin now present, set to None, the safe-default stamp for a no-baseline checkpoint)",
          "reviewers_pin" in after_adv and after_adv.get("reviewers_pin") is None, after_adv)
    check("[CP2-X16] stamping never touched anything else about the round (still round 1, same nonce)",
          after_adv.get("round") == before_adv.get("round") and after_adv.get("nonce") == before_adv.get("nonce")
          and after_adv.get("nonce") == nonce1, (before_adv, after_adv, nonce1))


CASES += [
    ("CP2-X16", x16_plan_extend_also_stamps_a_different_checkpoints_open_legacy_round),
]


# ------------------------------------------------------------------------------------------------- CP2-X17
# CP4 round-6 host-alt-correctness/A1: the real-world lane-R deadlock. A checkpoint's pin was edited and approved
# entirely under the OLD engine (before `cmd_reviewers` itself called `_stamp_legacy_round_baselines`): by the
# time the new code runs, `approved.cps`'s hash for this checkpoint already matches the live checkpoint (nothing
# pending), the round is legacy (no `reviewers_pin` key) and `approved.reviewers` is missing entirely.
# `approve-changes` early-returns "Nothing has changed since approval." before it ever reaches the stamping
# call, so it is the only command left that always runs -- `reviewers` itself -- that must be able to stamp and
# reopen the round on its own, on this same call, with no approve-changes round-trip needed.

def x17_lane_r_shape_reviewers_itself_reopens_when_approve_changes_cannot_help():
    """The exact deadlock CP4 exists to fix: a legacy round (no `reviewers_pin` key), no `approved.reviewers`
    baseline at all, and an approval snapshot whose checkpoint hash ALREADY matches the live checkpoint (the pin
    was approved once already, as a real pre-upgrade run's history would have it -- here, simply never edited
    since the plan itself was approved with this pin already in place, so nothing is pending). `approve-changes`
    finds nothing pending and refuses to help; only `reviewers`, which now runs the same stamp unconditionally
    before deciding whether to replace the round, can reopen it."""
    d = fr._start("cp2x17", extra_cp={"reviewers": ["host-correctness"]})
    rnd1 = fr._open(d)
    check("[CP2-X17] (fixture) round 1 opens pinned to the single configured reviewer",
          rnd1.get("round") == 1 and fr._ids(rnd1) == ["host-correctness"], (rnd1.get("round"), fr._ids(rnd1)))
    nonce1 = (fr._cps(d).get("adv_round") or {}).get("nonce")
    _strip_new_adv_round_keys(d)
    _strip_approved_reviewers(d)
    before_adv = fr._cps(d).get("adv_round") or {}
    check("[CP2-X17] (fixture) the round is now legacy-shaped: open, not recorded, no reviewers_pin key",
          not before_adv.get("recorded") and "reviewers_pin" not in before_adv, before_adv)
    st_now = json.loads((Path(d) / f".ratchet/runs/{SLUG}/state.json").read_text())
    check("[CP2-X17] (fixture) no approved.reviewers baseline exists at all (the pre-upgrade shape)",
          "reviewers" not in (st_now.get("approved") or {}), st_now.get("approved"))
    noop = g.rx(d, "approve-changes")
    check("[CP2-X17] approve-changes alone cannot help: nothing is pending, so it no-ops",
          noop.returncode == 0 and "Nothing has changed since approval." in fr._out(noop), fr._out(noop))
    r = g.rx(d, "reviewers", "CP1", "--json")
    assert r.returncode == 0, fr._out(r)
    j = json.loads(r.stdout)
    check("[CP2-X17] `reviewers` itself reopens the round on this same call (round number rises)",
          (j.get("round") or 0) > 1, j.get("round"))
    check("[CP2-X17] the fresh round names exactly the pinned reviewer",
          fr._ids(j) == ["host-correctness"], fr._ids(j))
    after_nonce = (fr._cps(d).get("adv_round") or {}).get("nonce")
    check("[CP2-X17] a fresh nonce (different from the legacy round's own)",
          bool(after_nonce) and after_nonce != nonce1, (nonce1, after_nonce))


def x18_has_baseline_legacy_pin_message_names_only_an_approved_change_to_this_checkpoint():
    """host-alt-correctness/A3: CP2-X02 only checks the shared "predates reviewers-pin tracking" phrase both
    legacy_pin_note wordings contain. This checks the full, exact has_baseline wording (approved.reviewers
    already carries this checkpoint's own baseline, the pin itself unchanged): it must name only "an approved
    change to this checkpoint's own reviewers field", and must never claim approve-plan or plan-extend could
    help (neither can run while this checkpoint's round is open and current)."""
    d = fr._start("cp2x18", extra_cp={"reviewers": ["host-standards", "host-correctness"]})
    fr._open(d)
    _strip_new_adv_round_keys(d)
    r = g.rx(d, "reviewers", "CP1", "--json")
    assert r.returncode == 0, fr._out(r)
    j = json.loads(r.stdout)
    note = j.get("legacy_pin_note") or ""
    check("[CP2-X18] the has-baseline wording names an approved change to this checkpoint's own reviewers field",
          "only an approved change to this checkpoint's own reviewers field will stamp a baseline here" in note,
          note)
    check("[CP2-X18] it never names approve-plan or plan-extend as a way to help",
          "approve-plan" not in note and "plan-extend" not in note, note)


def x19_a_recorded_legacy_round_is_replaced_with_no_stale_legacy_pin_note():
    """host-alt-correctness/A3: the `not rnd.get("recorded")` gate on legacy_pin_note. A recorded (blocked)
    legacy round always gets replaced the moment `reviewers` is called again, whatever its pin shape, so the
    note (which only ever describes a round being RE-SHOWN as-is) must never appear on the fresh round that
    replaces it."""
    d = fr._start("cp2x19", extra_cp={"reviewers": ["host-standards", "host-correctness"]})
    rnd1 = fr._open(d)
    rid = fr._pick(rnd1, "standards")
    fr._block(d, rnd1, {rid: [fr._finding("F1")]})
    _strip_new_adv_round_keys(d)
    before_adv = fr._cps(d).get("adv_round") or {}
    check("[CP2-X19] (fixture) the round is recorded and legacy-shaped (no reviewers_pin key)",
          bool(before_adv.get("recorded")) and "reviewers_pin" not in before_adv, before_adv)
    r = g.rx(d, "reviewers", "CP1", "--json")
    assert r.returncode == 0, fr._out(r)
    j = json.loads(r.stdout)
    check("[CP2-X19] the round is replaced (round number rises above the recorded round's)",
          (j.get("round") or 0) > (before_adv.get("round") or 0), (before_adv.get("round"), j.get("round")))
    check("[CP2-X19] no legacy_pin_note on the fresh round that replaced the recorded one",
          "legacy_pin_note" not in j, j.get("legacy_pin_note"))


CASES += [
    ("CP2-X17", x17_lane_r_shape_reviewers_itself_reopens_when_approve_changes_cannot_help),
    ("CP2-X18", x18_has_baseline_legacy_pin_message_names_only_an_approved_change_to_this_checkpoint),
    ("CP2-X19", x19_a_recorded_legacy_round_is_replaced_with_no_stale_legacy_pin_note),
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
