#!/usr/bin/env python3
"""
CP5 round-1 fixes: which findings start a fix round, and never a round with no reviewer.
                                                                   python3 tests/test_fix_round_fixes.py

Two findings from CP5's first review round:
  [CP5-X01..X03] (codex-standards/X1, codex-correctness/X1, major) the post-block fix round must follow the
      findings that ACTUALLY blocked the recorded round, not a fixed severity filter. At normal and high risk
      every severity blocks, so an ordinary first round can block on minor findings alone; once those are
      triaged `fix` and the fixer has run, the next round must still be the one-reviewer fix round. At low risk
      minor findings do not block, so such a round is an approval and never starts a fix round.
  [CP5-X04] (host-alt-correctness/A2, minor) when the fix round's own pick does not come back as exactly one
      reviewer, the round must be re-resolved, never opened with no reviewer at all. `resolve_reviewers` never
      returns an empty set for a round (its last alternative is always a host sub-agent), so the guard itself is
      not reachable from a working setup; what is reachable, and what this case proves, is the invariant it
      protects: a fix round whose reviewer's CLI has vanished still opens with exactly one usable reviewer and
      is still a fix round, and the round can be recorded and approved.

Black-box, in tests/test_fix_round.py's own style and with its helpers (that file is frozen; it is imported, not
changed): throwaway one-checkpoint git projects, host reviews written into the round's own folder, other-company
reviews produced by external_review.py against a fake agent CLI on a private PATH with its own RATCHET_HOME, no
real agent CLI launched and tests/run_all.py never run.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set.
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
import test_gates as g          # noqa: E402  (project, approve_plan, to_adversarial, write_review, rx)
import test_fix_round as fr     # noqa: E402  (_start, _open, _block, _triage, _write_code, _ext_project, ...)

PASS = FAIL = 0
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
ID_RE = re.compile(r"\[(CP5-X\d+)\]")


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


# The borrowed helpers (fr._check_fix_round, fr._check_not_fix_round) record through test_fix_round's own
# `check`, which would update THAT module's counters and case results, so a failed assertion inside one of them
# would neither fail this run nor show in this file's report (CP5 round 2, codex-standards/X1). Point that one
# recorder at this file's own `check` for the whole run, so every assertion this file makes, borrowed or not,
# counts in its exit status and its JSON report. tests/test_fix_round.py itself is untouched: it binds `check`
# at call time, and nothing of it runs here but these helpers.
fr.check = check


def _minor(fid, file="src/f.js"):
    """A minor finding: it blocks a normal- or high-risk round (every severity blocks there) and is deferred,
    never blocking, at low risk (`blocking_severities.low` is blocker and major)."""
    return dict(fr._finding(fid, severity="minor", file=file),
                description="the new path leaves a stray debug line behind")


def x01_a_round_that_blocked_only_on_minors_opens_a_fix_round():
    d = fr._start("x01", config=["checkin_round=99"])
    rnd1 = fr._open(d)
    rid = fr._pick(rnd1, "correctness")
    check("[CP5-X01] (fixture) round 1 of a normal-risk step names two reviewers", len(rnd1["reviewers"]) == 2,
          fr._ids(rnd1))
    fr._block(d, rnd1, {rid: [_minor("M1")]})  # asserts the round blocked (exit 2) on that minor alone
    fr._write_code(d, 30, "x01")
    rnd2 = fr._open(d)
    fr._check_fix_round("CP5-X01", d, rnd2, rid, "a round that blocked on a minor finding only")
    check("[CP5-X01] the round after it is round 2", rnd2.get("round") == 2, rnd2.get("round"))
    txt = fr._reviewers(d).stdout or ""
    check("[CP5-X01] the plain text calls it a fix round with one reviewer and names the reviewer",
          "fix round" in txt.lower() and "one reviewer" in txt.lower() and rid in txt, txt[:400])


def x02_a_low_risk_round_whose_minors_did_not_block_starts_no_fix_round():
    d = fr._start("x02", risk="low", config=["checkin_round=99"])
    rnd1 = fr._open(d)
    paths = [g.write_review(d, rnd1, rv["id"], [_minor("M1")]) for rv in rnd1["reviewers"]]
    r = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    check("[CP5-X02] (fixture) at low risk a minor finding does not block: the round is approved",
          r.returncode == 0 and fr._gate(d).get("status") == "passed", fr._out(r)[-300:])
    fr._write_code(d, 30, "x02")
    rnd2 = fr._open(d)
    fr._check_not_fix_round("CP5-X02", rnd2, "a 30-line edit after a round whose minors did not block")
    check("[CP5-X02] its text has no fix-round wording",
          "fix round" not in (fr._reviewers(d).stdout or "").lower(), (fr._reviewers(d).stdout or "")[:300])


def x03_a_high_risk_round_blocking_only_on_minors_gets_one_reviewer():
    d = fr._start("x03", risk="high", config=["checkin_round=99"])
    rnd1 = fr._open(d)
    check("[CP5-X03] (fixture) round 1 of a high-risk step names four reviewers", len(rnd1["reviewers"]) == 4,
          fr._ids(rnd1))
    by_slot = sorted(rnd1["reviewers"], key=lambda rv: rv["slot"])
    blockers = [by_slot[1]["id"], by_slot[3]["id"]]
    fr._block(d, rnd1, {blockers[0]: [_minor("M1")], blockers[1]: [_minor("M2")]})
    fr._write_code(d, 60, "x03")
    rnd2 = fr._open(d)
    check("[CP5-X03] round 2 names exactly one reviewer, even at high risk and after a 60-line fix",
          len(rnd2["reviewers"]) == 1, fr._ids(rnd2))
    check("[CP5-X03] it is one of the two reviewers whose minor findings blocked, the lower slot of them",
          fr._ids(rnd2) == [blockers[0]], (fr._ids(rnd2), blockers))
    check("[CP5-X03] it is marked small_fix and follows a block",
          rnd2.get("small_fix") is True and fr._after_block(rnd2),
          (rnd2.get("small_fix"), fr._kind(rnd2) or "no kind field"))


def _provider_binary(d, pid):
    cfg = json.loads((Path(d) / ".ratchet/config.json").read_text())
    for p in cfg.get("providers") or []:
        if p.get("id") == pid:
            return Path((p.get("command") or [""])[0])
    raise AssertionError(f"no {pid} provider in {cfg.get('providers')}")


def x04_a_fix_round_never_opens_with_no_reviewer():
    with fr._own_home():
        d, env = fr._ext_project("x04", {"normal": ["codex-standards", "host-correctness"]},
                                 [("codex", "openai", "gpt-5")],
                                 replies={"codex": (json.dumps({"findings": [fr._finding("S1")]}),
                                                    fr._NO_FINDINGS)})
        rnd1 = fr._open(d, env=env)
        fr._ext_record(d, rnd1, env)
        fr._triage(d, ["codex-standards/S1"], env=env)
        fr._write_code(d, 30, "x04", env=env)
        _provider_binary(d, "codex").unlink()  # the reviewer's CLI is gone: its slot must still be filled
        rnd2 = fr._open(d, env=env)
        got = fr._ids(rnd2)
        check("[CP5-X04] the round opens with exactly one reviewer, never an empty reviewer set",
              len(got) == 1 and bool(got[0]), got)
        check("[CP5-X04] the stand-in keeps the standards lens and is not codex-standards",
              fr._lens(got[0]) == "standards" and got[0] != "codex-standards", got)
        check("[CP5-X04] it is still a fix round after a block",
              rnd2.get("small_fix") is True and fr._after_block(rnd2),
              (rnd2.get("small_fix"), fr._kind(rnd2) or "no kind field"))
        r = fr._record(d, rnd2, [g.write_review(d, rnd2, got[0], [])], env=env)
        check("[CP5-X04] that one reviewer's clean review is recorded and passes the gate",
              r.returncode == 0 and fr._gate(d).get("status") == "passed", fr._out(r)[-300:])


CASES = [
    ("CP5-X01", x01_a_round_that_blocked_only_on_minors_opens_a_fix_round),
    ("CP5-X02", x02_a_low_risk_round_whose_minors_did_not_block_starts_no_fix_round),
    ("CP5-X03", x03_a_high_risk_round_blocking_only_on_minors_gets_one_reviewer),
    ("CP5-X04", x04_a_fix_round_never_opens_with_no_reviewer),
]


def main():
    for cid, fn in CASES:
        print(f"{cid}: {fn.__name__}")
        try:
            fn()
        except Exception:
            check(f"[{cid}] ran to the end without an unexpected error", False, traceback.format_exc())
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
