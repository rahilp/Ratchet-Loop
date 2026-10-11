#!/usr/bin/env python3
"""
CP1 tests: a finding fixed later in the run leaves the left-for-later list.   python3 tests/test_deferred_fixed.py

A finding recorded for later (`ratchet.py deferred`) that was really fixed afterward can be marked fixed with
`ratchet.py mark-fixed CPn <ref>="<how>"`, but only when the finding's own file changed since the round that
deferred it AND a later adversarial round was recorded on that changed code; mark-fixed is refused, with the
state unchanged, when the file never changed, when no later round reviewed the changed code, when the finding
was accepted (not deferred) instead, or when the reference does not exist. A finding re-raised in a later round
by the same reviewer with the same id must appear exactly once in `deferred` (the latest round's own copy), never
once per round; a different finding that merely shares a `kind` and `file` is kept as its own entry, never
merged. `ratchet.py status`'s final-review line counts only findings still left for later (excluding ones marked
fixed), and `deferred --json` carries a `"fixed": {"round": <n>, "how": "<reason>"}` record only on the entry
that was marked fixed. Findings the user accepted with `accept-finding` keep printing in their own section,
unaffected by any of this.

Reuses tests/test_gates.py (`project`, `approve_plan`, `write_tests`, `to_adversarial`, `open_round`, `R0`,
`write_review`, `git`, `rx`, `say`, `ENV`, `ENGINE`) for the throwaway git projects and the real-CLI-subprocess
way every suite in this repo drives the engine (see test_limits.py, which reuses the same module the same way).

Every fixture here builds a CP1 of `risk: "low"` (test_gates.project()'s own default), where
`blocking_severities.low == ["blocker", "major"]` (DEFAULT_CONFIG): a `minor` finding is always recorded for
later, never blocking, the moment it is reviewed, with a single required reviewer each round (`review_depth.low
== ["any-both"]`) — so a round always records cleanly (`adv-record` exits 0) and the checkpoint reaches its
`complete` step after the very first round. `ratchet.py reviewers`/`adv-record` both accept a checkpoint already
at its `complete` step (re-reviewing code edited after completion), which is how every fixture below opens a
later round on changed code without needing to drive the review through repeated blocking rounds first; a
`blocker` finding (which always blocks, at any risk) is used only in the one fixture that needs an accepted
finding (`_accept_fixture`), the same `ask accept-finding` / `accept-finding` flow test_limits.py's CP1-X03
case drives.

Every case that depends on `mark-fixed` (not yet built) calls it through the real CLI subprocess
(`test_gates.rx`), so a missing command is reported through `check()` as a clean fail (a non-zero exit with the
wrong message), never an uuncaught crash.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP1-T01",
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
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                     # noqa: E402
import test_gates as g          # noqa: E402  (project, approve_plan, to_adversarial, open_round, R0, write_review, ...)

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP1-[TX]\d+[a-z]?)\]")


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ------------------------------------------------------------------------------------------------ fixtures

def _deferred_json(d):
    r = g.rx(d, "deferred", "--json")
    assert r.returncode == 0, r.stdout + r.stderr  # `deferred --json` itself is existing, stable behaviour
    return json.loads(r.stdout)


def _round_with(d, cp, findings):
    """Opens the next adversarial round for `cp` and records it with exactly `findings`, asserting it is
    accepted cleanly (every fixture here uses only `minor` findings, which never block at `risk: "low"`).
    Returns the round number `adv-record` just recorded."""
    rnd = g.open_round(d, cp)
    p = g.write_review(d, rnd, g.R0(rnd), findings)
    adv = g.rx(d, "adv-record", cp, "--reviews", p)
    assert adv.returncode == 0, adv.stdout + adv.stderr
    return rnd["round"]


def _history(name):
    """CP1 driven through three recorded adversarial rounds, mirroring run 4's own CP1 deferred list (S1 on the
    engine file, S2 on README.md, both minor, deferred the moment they are reviewed at this low-risk step):
      round r1: S1 (skills/ratchet-orchestrator/scripts/ratchet.py) and S2 (README.md), both deferred.
      -- both files edited and committed --
      round r2: only S2 re-raised (same reviewer, same id), deferred again; S1 is NOT re-raised, so round r2's
      own review of the (already-edited) engine file is real proof that S1's fix held.
      -- README.md edited and committed a second time (the engine file is NOT touched again) --
      round r3: S2 re-raised again, plus S3, a genuinely different finding (different id, same `kind` and
      `file` as S2), both deferred.
    S1's one and only deferred entry stays round r1's (mark-fixed's proof is round r2, the later round that
    reviewed its already-changed file without re-raising it), and S2's latest copy is r3.
    Returns (d, r1, r2, r3)."""
    cfg = ["min_review_seconds=0", "providers=[]", "checkin_round=99"]
    d = g.project(name, cps=1, config=cfg)   # risk defaults "low": minors always deferred, never block
    g.approve_plan(d)
    g.to_adversarial(d, "CP1")
    (d / "skills" / "ratchet-orchestrator" / "scripts").mkdir(parents=True)
    (d / "skills/ratchet-orchestrator/scripts/ratchet.py").write_text("# fixture engine file\n")
    (d / "README.md").write_text("# fixture docs\noriginal text\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "add fixture files")

    s1 = {"id": "S1", "severity": "minor", "kind": "missing-blocked-state-guard",
          "file": "skills/ratchet-orchestrator/scripts/ratchet.py",
          "description": "missing the blocked-state guard"}
    s2 = {"id": "S2", "severity": "minor", "kind": "docs-out-of-sync", "file": "README.md",
          "description": "README does not mention mark-fixed"}
    r1 = _round_with(d, "CP1", [s1, s2])

    (d / "skills/ratchet-orchestrator/scripts/ratchet.py").write_text("# fixture engine file\n# added the guard\n")
    (d / "README.md").write_text("# fixture docs\nupdated text\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "add the guard, touch docs")
    r2 = _round_with(d, "CP1", [s2])  # S1 is NOT re-raised: this round's review of the edited file is the proof

    (d / "README.md").write_text("# fixture docs\nupdated text again\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "touch docs again")
    s2b = dict(s2, description="README still does not mention mark-fixed (checked again)")
    s3 = {"id": "S3", "severity": "minor", "kind": "docs-out-of-sync", "file": "README.md",
          "description": "README's command table is missing a newer command"}
    r3 = _round_with(d, "CP1", [s2b, s3])
    return d, r1, r2, r3


def _fresh_round_only(name):
    """CP1 with exactly one recorded adversarial round: S1 (docs/NOTES.md) and S2 (README.md), both minor,
    deferred the moment they are reviewed. Nothing is edited or reviewed again afterward. Returns (d, r1)."""
    cfg = ["min_review_seconds=0", "providers=[]", "checkin_round=99"]
    d = g.project(name, cps=1, config=cfg)
    g.approve_plan(d)
    g.to_adversarial(d, "CP1")
    (d / "docs").mkdir()
    (d / "docs/NOTES.md").write_text("notes\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "add notes")
    s1 = {"id": "S1", "severity": "minor", "kind": "missing-blocked-state-guard", "file": "docs/NOTES.md",
          "description": "missing a guard note"}
    s2 = {"id": "S2", "severity": "minor", "kind": "docs-out-of-sync", "file": "README.md",
          "description": "README is stale"}
    r1 = _round_with(d, "CP1", [s1, s2])
    return d, r1


def _edited_no_review(name):
    """Like `_fresh_round_only`, but README.md (S2's file) is edited and committed AFTER round r1 records S2
    deferred, and no round is ever opened or recorded on that changed code. Returns (d, r1)."""
    d, r1 = _fresh_round_only(name)
    (d / "README.md").write_text("README is stale, now edited\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "edit the doc, no new review round")
    return d, r1


def _accept_fixture(name):
    """CP1 whose first adversarial round carries a blocker (B1, always blocking at any risk) alongside two
    ordinary minor findings (S1 on docs/NOTES.md, S2 on README.md, both deferred the moment they are reviewed).
    B1 is then accepted the way test_limits.py's CP1-X03 case accepts one: `ask accept-finding` + the user's own
    `accept: <reason>` chat reply + `accept-finding`. Returns (d, r1)."""
    cfg = ["min_review_seconds=0", "providers=[]", "checkin_round=99"]
    d = g.project(name, cps=1, config=cfg)
    g.approve_plan(d)
    g.to_adversarial(d, "CP1")
    (d / "docs").mkdir()
    (d / "docs/NOTES.md").write_text("notes\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "add notes")
    b1 = {"id": "B1", "severity": "blocker", "file": "src/f.js", "description": "unsafe write",
          "user_impact": "a user's saved data could be silently corrupted"}
    s1 = {"id": "S1", "severity": "minor", "kind": "missing-blocked-state-guard", "file": "docs/NOTES.md",
          "description": "missing a guard note"}
    s2 = {"id": "S2", "severity": "minor", "kind": "docs-out-of-sync", "file": "README.md",
          "description": "README is stale"}
    rnd = g.open_round(d, "CP1")
    p = g.write_review(d, rnd, g.R0(rnd), [b1, s1, s2])
    adv = g.rx(d, "adv-record", "CP1", "--reviews", p)
    assert adv.returncode == 2, adv.stdout + adv.stderr  # B1 (blocker) blocks; S1/S2 (minor) are deferred anyway
    r1 = rnd["round"]
    of_path = d / f".ratchet/runs/f/evidence/CP1/adversarial/round-{r1:02d}/open-findings.json"
    ids = [f["id"] for f in json.loads(of_path.read_text())]
    assert ids == ["B1"], ids  # fixture sanity: only the blocker is still open
    g.rx(d, "ask", "accept-finding", "CP1", ",".join(ids))
    g.say(d, "accept: known limit for this fixture")
    racc = g.rx(d, "accept-finding", "CP1")
    assert racc.returncode == 0, racc.stdout + racc.stderr
    return d, r1


def _two_then_edit(name):
    """CP1 with S1 (docs/NOTES.md) and S2 (README.md) deferred in round r1, then docs/NOTES.md (only) is edited
    and a round r2 is recorded with an empty finding list -- S1 is NOT re-raised, so round r2's own review of the
    already-edited file is real proof the fix held (S2 stays at its single round-r1 copy). `behavior` is re-run
    (it never touches docs/NOTES.md or README.md, but the fingerprint it is stamped with covers the whole
    working tree) and `complete CP1` is then run, taking this one-checkpoint run to its final review. Returns
    (d, r1, r2)."""
    d, r1 = _fresh_round_only(name)
    (d / "docs/NOTES.md").write_text("notes\nadded the guard\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "add the guard")
    r2 = _round_with(d, "CP1", [])  # no findings: nothing re-raises S1 on the now-fixed file
    rb = g.rx(d, "behavior", "CP1")
    assert rb.returncode == 0, rb.stdout + rb.stderr
    comp = g.rx(d, "complete", "CP1")
    assert comp.returncode == 0, comp.stdout + comp.stderr
    return d, r1, r2


# ------------------------------------------------------------------------------------------------ cases

TESTS = []


def t01_fixed_and_rereviewed_moves_to_fixed_later():
    d, r1, r2, r3 = _history("t01")
    mf = g.rx(d, "mark-fixed", "CP1", 'S1="added the blocked-state guard"')
    check("[CP1-T01] mark-fixed succeeds once the file changed and a later round reviewed it",
          mf.returncode == 0, mf.stdout + mf.stderr)
    after = _deferred_json(d)
    s1_entries = [f for f in after if f.get("id") == "S1"]
    check(f"[CP1-T01] exactly one S1 entry remains (no stale round-{r1} duplicate)",
          len(s1_entries) == 1, s1_entries)
    fixed = (s1_entries[0] or {}).get("fixed") if s1_entries else None
    check(f"[CP1-T01] S1's fixed record names the later round ({r2}) it was reviewed again at",
          isinstance(fixed, dict) and fixed.get("round") == r2, fixed)
    check("[CP1-T01] S1's fixed record carries the reason given to mark-fixed",
          isinstance(fixed, dict) and "blocked-state guard" in str(fixed.get("how", "")), fixed)
    left = [f for f in after if not f.get("accepted") and not f.get("fixed")]
    check("[CP1-T01] S1 no longer appears under left-for-later",
          not any(f.get("id") == "S1" for f in left), left)


TESTS.append(("CP1-T01", t01_fixed_and_rereviewed_moves_to_fixed_later))


def t02_refused_when_file_never_changed():
    d, r1 = _fresh_round_only("t02")
    before = _deferred_json(d)
    mf = g.rx(d, "mark-fixed", "CP1", 'S1="fixed it"')
    check("[CP1-T02] mark-fixed is refused when the finding's file never changed since its round",
          mf.returncode != 0, mf.stdout + mf.stderr)
    out = (mf.stdout + mf.stderr).lower()
    check("[CP1-T02] the refusal names S1 and that its file has not changed",
          "s1" in out and ("has not changed" in out or "hasn't changed" in out or "no change" in out),
          mf.stdout + mf.stderr)
    after = _deferred_json(d)
    check("[CP1-T02] deferred is unchanged: S1 still shows under left-for-later", after == before, (before, after))


TESTS.append(("CP1-T02", t02_refused_when_file_never_changed))


def t03_refused_when_changed_code_never_reviewed_again():
    d, r1 = _edited_no_review("t03")
    before = _deferred_json(d)
    mf = g.rx(d, "mark-fixed", "CP1", 'S2="fixed the doc"')
    check("[CP1-T03] mark-fixed is refused when no later round reviewed the changed code",
          mf.returncode != 0, mf.stdout + mf.stderr)
    out = (mf.stdout + mf.stderr).lower()
    check("[CP1-T03] the refusal names S2 and that no later review round was recorded",
          "s2" in out and ("later review round" in out or "reviewed again" in out or "no later round" in out
                           or "review round" in out),
          mf.stdout + mf.stderr)
    after = _deferred_json(d)
    check("[CP1-T03] deferred is unchanged: S2 still shows under left-for-later", after == before, (before, after))


TESTS.append(("CP1-T03", t03_refused_when_changed_code_never_reviewed_again))


def t04_refused_for_accepted_finding():
    d, r1 = _accept_fixture("t04")
    before_acc = [f for f in _deferred_json(d) if f.get("accepted")]
    mf = g.rx(d, "mark-fixed", "CP1", 'B1="fixed anyway"')
    check("[CP1-T04] mark-fixed is refused for a finding the user already accepted",
          mf.returncode != 0, mf.stdout + mf.stderr)
    out = (mf.stdout + mf.stderr).lower()
    check("[CP1-T04] the refusal names B1 and that it was accepted, not deferred",
          "b1" in out and "accept" in out, mf.stdout + mf.stderr)
    after_acc = [f for f in _deferred_json(d) if f.get("accepted")]
    check("[CP1-T04] nothing in the accepted section changed", before_acc == after_acc, (before_acc, after_acc))


TESTS.append(("CP1-T04", t04_refused_for_accepted_finding))


def t05_refused_for_unknown_reference():
    d, r1 = _fresh_round_only("t05")
    before = _deferred_json(d)
    mf = g.rx(d, "mark-fixed", "CP1", 'S9="fixed it"')
    check("[CP1-T05] mark-fixed is refused for a finding reference that does not exist",
          mf.returncode != 0, mf.stdout + mf.stderr)
    out = (mf.stdout + mf.stderr).lower()
    check("[CP1-T05] the refusal names S9 and that it does not exist",
          "s9" in out and ("does not exist" in out or "no such finding" in out or "unknown" in out),
          mf.stdout + mf.stderr)
    after = _deferred_json(d)
    check("[CP1-T05] deferred is unchanged", after == before, (before, after))


TESTS.append(("CP1-T05", t05_refused_for_unknown_reference))


def t06_rereaised_finding_appears_once():
    d, r1, r2, r3 = _history("t06")
    out = g.rx(d, "deferred").stdout
    rows = _deferred_json(d)
    s2 = [f for f in rows if f.get("id") == "S2"]
    check("[CP1-T06] setup carries a reviewer id for S2", bool(s2) and s2[0].get("reviewer"), s2)
    key = f"{s2[0].get('reviewer')}/S2" if s2 else "S2"
    check("[CP1-T06] S2 is listed exactly once in `deferred`, not once per round",
          out.count(key) == 1, out)
    check(f"[CP1-T06] the one copy shown is round {r3}'s own text, not round {r1}'s stale copy",
          "checked again" in out, out)


TESTS.append(("CP1-T06", t06_rereaised_finding_appears_once))


def t07_findings_sharing_kind_and_file_both_kept():
    d, r1, r2, r3 = _history("t07")
    rows = _deferred_json(d)
    docs = [f for f in rows if f.get("kind") == "docs-out-of-sync" and "README" in str(f.get("file", ""))]
    check("[CP1-T07] S2 and S3 (same kind/file, different reviewer/id) are both kept, not merged",
          len(docs) == 2 and sorted(f.get("id") for f in docs) == ["S2", "S3"], rows)
    s2 = next((f for f in docs if f.get("id") == "S2"), None)
    s3 = next((f for f in docs if f.get("id") == "S3"), None)
    check(f"[CP1-T07] both entries keep their own round number ({r3})",
          bool(s2) and bool(s3) and s2.get("round") == r3 and s3.get("round") == r3, (s2, s3))


TESTS.append(("CP1-T07", t07_findings_sharing_kind_and_file_both_kept))


def t08_status_final_review_count_excludes_fixed():
    d, r1, r2 = _two_then_edit("t08")
    before = g.rx(d, "status").stdout
    m0 = re.search(r"(\d+) finding\(s\) were left for later", before)
    check("[CP1-T08] status shows the final-review left-for-later count before any fix",
          bool(m0), before)
    n0 = int(m0.group(1)) if m0 else -1
    mf = g.rx(d, "mark-fixed", "CP1", 'S1="added the guard"')
    check("[CP1-T08] mark-fixed succeeds for S1 in this fixture (its file changed, round 2 reviewed it)",
          mf.returncode == 0, mf.stdout + mf.stderr)
    after = g.rx(d, "status").stdout
    m1 = re.search(r"(\d+) finding\(s\) were left for later", after)
    n1 = int(m1.group(1)) if m1 else -1
    check("[CP1-T08] the final-review count drops by exactly one once S1 is marked fixed",
          m1 is not None and n1 == n0 - 1, (before, after))


TESTS.append(("CP1-T08", t08_status_final_review_count_excludes_fixed))


def t09_deferred_json_carries_fixed_only_on_the_marked_one():
    d, r1, r2, r3 = _history("t09")
    mf = g.rx(d, "mark-fixed", "CP1", 'S1="added the blocked-state guard"')
    check("[CP1-T09] mark-fixed succeeds for S1", mf.returncode == 0, mf.stdout + mf.stderr)
    rows = _deferred_json(d)
    s1 = next((f for f in rows if f.get("id") == "S1"), None)
    s2_rows = [f for f in rows if f.get("id") == "S2"]
    check("[CP1-T09] S1's entry carries a fixed record (round + reason)",
          isinstance((s1 or {}).get("fixed"), dict) and (s1 or {}).get("fixed", {}).get("round")
          and (s1 or {}).get("fixed", {}).get("how"), s1)
    check("[CP1-T09] no S2 entry carries a fixed record", all(not f.get("fixed") for f in s2_rows), s2_rows)


TESTS.append(("CP1-T09", t09_deferred_json_carries_fixed_only_on_the_marked_one))


def t10_accepted_findings_stay_in_their_own_section():
    d, r1 = _accept_fixture("t10")
    out = g.rx(d, "deferred").stdout
    check("[CP1-T10] the accepted finding's own section still prints first",
          out.strip().startswith("Findings accepted by you"), out)
    head = out.split("\n\n", 1)[0]
    check("[CP1-T10] B1 is listed under that accepted section with the fixture's reason",
          "B1" in head and "known limit for this fixture" in head, out)
    check("[CP1-T10] S1 and S2 (never accepted) still show separately under left-for-later",
          "left for later" in out and "S1" in out and "S2" in out, out)


TESTS.append(("CP1-T10", t10_accepted_findings_stay_in_their_own_section))


def t11_docs_describe_mark_fixed():
    readme = (ROOT / "README.md").read_text()
    skill = (ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md").read_text()
    schemas = (ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md").read_text()
    check("[CP1-T11] README's command table lists `mark-fixed`",
          "mark-fixed" in readme, "")
    check("[CP1-T11] the orchestrator SKILL.md tells the agent to mark a deferred finding a fix also closed",
          "mark-fixed" in skill, "")
    check("[CP1-T11] schemas.md documents the fixed record on a deferred entry",
          "mark-fixed" in schemas or '"fixed"' in schemas, "")


TESTS.append(("CP1-T11", t11_docs_describe_mark_fixed))


def _reraised_then_stops(name):
    """CP1 with S1 (docs/NOTES.md) deferred in round r1, then docs/NOTES.md is edited and committed, and round r2
    re-raises S1 again (same reviewer/id) on that edited code, remaining unfixed. Nothing is edited or reviewed
    again after r2. Returns (d, r1, r2)."""
    cfg = ["min_review_seconds=0", "providers=[]", "checkin_round=99"]
    d = g.project(name, cps=1, config=cfg)
    g.approve_plan(d)
    g.to_adversarial(d, "CP1")
    (d / "docs").mkdir()
    (d / "docs/NOTES.md").write_text("notes\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "add notes")
    s1 = {"id": "S1", "severity": "minor", "kind": "missing-blocked-state-guard", "file": "docs/NOTES.md",
          "description": "missing a guard note"}
    r1 = _round_with(d, "CP1", [s1])
    (d / "docs/NOTES.md").write_text("notes\nedited but still missing the guard\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "edit the file, guard still missing")
    r2 = _round_with(d, "CP1", [s1])
    return d, r1, r2


def x01_rereaised_round_cannot_prove_its_own_fix():
    d, r1, r2 = _reraised_then_stops("x01")
    before = _deferred_json(d)
    mf = g.rx(d, "mark-fixed", "CP1", 'S1="added the guard"')
    check("[CP1-X01] mark-fixed is refused when the only later state is the round that re-raised the finding "
          "itself (no file change or review after that round)", mf.returncode != 0, mf.stdout + mf.stderr)
    out = (mf.stdout + mf.stderr).lower()
    check(f"[CP1-X01] the refusal is checked against round {r2} (the round that LAST raised S1), not round {r1}",
          "s1" in out and str(r2) in out, mf.stdout + mf.stderr)
    after = _deferred_json(d)
    check("[CP1-X01] deferred is unchanged: S1 still shows under left-for-later", after == before, (before, after))


TESTS.append(("CP1-X01", x01_rereaised_round_cannot_prove_its_own_fix))


def _deferred_then_blocking_reraise(name):
    """CP1: S1 (docs/NOTES.md, minor) is deferred in round r1. docs/NOTES.md is then edited and committed, and
    round r2 re-raises the SAME reviewer/id, this time as a `major` (blocking) finding with a `user_impact`, so
    it blocks outright (`adv-record` exits 2) and is never added to `deferred` -- a blocking finding never is.
    Nothing is edited after round r2: S1's file matches exactly what round r2 reviewed. Returns (d, r1, r2)."""
    cfg = ["min_review_seconds=0", "providers=[]", "checkin_round=99"]
    d = g.project(name, cps=1, config=cfg)
    g.approve_plan(d)
    g.to_adversarial(d, "CP1")
    (d / "docs").mkdir()
    (d / "docs/NOTES.md").write_text("notes\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "add notes")
    s1_minor = {"id": "S1", "severity": "minor", "kind": "missing-blocked-state-guard", "file": "docs/NOTES.md",
                "description": "missing a guard note"}
    r1 = _round_with(d, "CP1", [s1_minor])
    (d / "docs/NOTES.md").write_text("notes\nedited, still missing the guard\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "edit the file, guard still missing")
    rnd = g.open_round(d, "CP1")
    s1_major = {"id": "S1", "severity": "major", "kind": "missing-blocked-state-guard", "file": "docs/NOTES.md",
                "description": "still missing the guard, now blocking",
                "user_impact": "a user relying on the missing guard could silently corrupt state"}
    p = g.write_review(d, rnd, g.R0(rnd), [s1_major])
    adv = g.rx(d, "adv-record", "CP1", "--reviews", p)
    assert adv.returncode == 2, adv.stdout + adv.stderr  # S1 now blocks; round r2 itself never resolves it
    r2 = rnd["round"]
    return d, r1, r2


def x02_blocking_reraise_cannot_prove_its_own_fix():
    d, r1, r2 = _deferred_then_blocking_reraise("x02")
    before = _deferred_json(d)
    mf = g.rx(d, "mark-fixed", "CP1", 'S1="added the guard"')
    check("[CP1-X02] mark-fixed is refused when the only later state is a round that re-raised the finding as "
          "BLOCKING (never deferred again, no file change or review after that round)",
          mf.returncode != 0, mf.stdout + mf.stderr)
    out = (mf.stdout + mf.stderr).lower()
    check(f"[CP1-X02] the refusal is checked against round {r2} (the round that raised S1 as blocking), not "
          f"round {r1} (its earlier, deferred copy)",
          "s1" in out and str(r2) in out, mf.stdout + mf.stderr)
    after = _deferred_json(d)
    check("[CP1-X02] deferred is unchanged: S1 still shows under left-for-later", after == before, (before, after))


TESTS.append(("CP1-X02", x02_blocking_reraise_cannot_prove_its_own_fix))


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
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crash still counts as a result for this case, never a silent drop
            global FAIL
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
