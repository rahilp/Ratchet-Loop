#!/usr/bin/env python3
"""
CP6 tests: the fix reviewer sees only the fix.   python3 tests/test_fix_round_diff.py

In a fix round (lane H CP5's one-reviewer round after a block, and lane B's small fix after an approval) the
reviewer is shown only what changed since the tree the last reviewed round saw, plus the findings the fix was
meant to fix. A first round, a fix over small_edit_lines after an approval, and a high-risk step's fix keep the
whole step's diff. A fix round also gets a shorter, diff-scaled minimum review time (never above
min_review_seconds, with a floor above zero); a full round keeps min_review_seconds.

Black-box: every case drives the real `ratchet.py` CLI in a throwaway one-checkpoint git project built with
tests/test_gates.py's helpers (through the helpers of tests/test_fix_round.py, whose `check` is pointed at THIS
file's own `check`, so their assertions land in this file's counters). What an other-company reviewer is shown is
read from the prompt `external_review.py --dry-run` writes (the exact command `reviewers --json` gives, plus
--dry-run), against a fake agent CLI on a private PATH; what a host sub-agent is told is read from `reviewers
--json` (the row's `how` plus the round's `review_rules`). Review times are set with os.utime on the review file,
never by sleeping. The engine always sees an idle machine (RATCHET_FAKE_LOADAVG=0), and every case that uses a fake
external CLI gets its own RATCHET_HOME (the machine-wide provider share record lives there).

Requirement-to-check map (planned case -> what proves it):
  CP6-T01  other-company reviewer, fix after a block: prompt has the fixer's added line, names the changed file,
           no bravo/charlie marker, no test-file content, header names the reviewed round's tree not the step start
  CP6-T02  prompt lists both open findings of the replaced round with id, severity, file, line, description, fix,
           and the rebuttal reason; no finding of an older round
  CP6-T03  round 1: the whole diff under "Changes in this checkpoint", no fix-only wording
  CP6-T04  small fix after approval: only the 2 new lines from the approved tree, no open findings listed
  CP6-T05  25-line fix after approval: full round, all three files, files changed since the last round first
  CP6-T06  high-risk step, 2-line fix after approval: full reviewer set, whole diff
  CP6-T07  second fix round: only fix B and round 2's findings; still round 3 in both counters
  CP6-T08  a fix round replaced before any review: diff measured from the last tree a reviewer saw
  CP6-T09  a new (uncommitted) file and a deleted file are shown with their content
  CP6-T10  a finding the user accepted is not listed to fix
  CP6-T11  host reviewer after a block: instruction names the prior tree, fingerprint, changed file, findings
  CP6-T12  host reviewer after a small fix: same, with no open findings
  CP6-T13  host reviewer in a full round: no fix-only instruction
  CP6-T14  2-line fix round: accepted at 10 s, refused at 0 s (min_review_seconds 20)
  CP6-T15  full round: refused at 10 s, accepted at 20 s
  CP6-T16  ~400-line fix round: refused at 10 s, accepted at 20 s
  CP6-T17  fix round with min_review_seconds 5 and 0: accepted at 5 s and 0 s
  CP6-T18  only a real small fix after approval gets the shorter minimum
  CP6-T19  a small fix that cannot get one reviewer: full diff, full minimum
  CP6-T20  a fix round's clean review must still list changed files
  CP6-T21  other-company fix review, end to end with a signed review, accepted at 10 s
  CP6-T22  README, both SKILL.md files, schemas.md and agents/ratchet-adversary.md describe it

Review times use "+0.2 s" of slack on accept cases (the file's mtime is rounded to nanoseconds, so an exact
boundary could land a hair under it); the refused times (0 s, 10 s against a 20 s minimum) have no slack.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP6-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                     # noqa: E402,F401
import test_gates as g          # noqa: E402
import test_providers as tp     # noqa: E402  (EXT: external_review.py)
import test_fix_round as tf     # noqa: E402  (project / round / fake-CLI helpers)

g.ENV["RATCHET_FAKE_LOADAVG"] = "0"

PLANNED = [f"CP6-T{i:02d}" for i in range(1, 23)]
PASS = FAIL = 0
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
ID_RE = re.compile(r"\[(CP6-T\d+)\]")


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


tf.check = check  # the borrowed helpers record their assertions here, not into test_fix_round.py's counters

ALPHA, BRAVO, CHARLIE = "ZQ-ALPHA", "ZQ-BRAVO", "ZQ-CHARLIE"
TEST_BODY = "a.equal(f(), 1)"  # the content of the step's own test file (tests/cp1.test.js)
NO_FINDINGS = '{"findings": []}'
FULL_HEADER = "Changes in this checkpoint"


# ------------------------------------------------------------------------------------------------ fixtures

def _out(r):
    return (r.stdout or "") + "\n" + (r.stderr or "")


def _finding(fid, file, line, desc, fix, severity="major"):
    return {"id": fid, "severity": severity, "file": file, "line": line, "description": desc, "fix": fix,
            "rule": "a developer hits this in normal use", "user_impact": "a developer loses work they just did"}


def _src(name, marker):
    return (f"export const {name} = 1; // {marker}\n"
            f"export const {name}_two = 2;\n"
            f"export const {name}_three = 3;\n")


def _edit(d, rel, text, env=None):
    """The fixer's change: `rel` rewritten (or created), then `behavior` re-proves the code."""
    (Path(d) / rel).write_text(text)
    r = g.rx(d, "behavior", "CP1", env=env)
    assert r.returncode == 0, _out(r)


def _append_code(d, rel, n, tag, env=None, first_marker=None):
    """Appends exactly `n` real code lines (not comments), so the measured change is exactly `n` lines."""
    with open(Path(d) / rel, "a") as f:
        for i in range(n):
            tail = f" // {first_marker}" if (first_marker and i == 0) else ""
            f.write(f"export const pad_{tag}_{i} = {i};{tail}\n")
    r = g.rx(d, "behavior", "CP1", env=env)
    assert r.returncode == 0, _out(r)


def _seed(d, env=None):
    """Three source files, each with its own marker line, on top of the step's test file."""
    for name, marker in (("alpha", ALPHA), ("bravo", BRAVO), ("charlie", CHARLIE)):
        (Path(d) / f"src/{name}.js").write_text(_src(name, marker))
    r = g.rx(d, "behavior", "CP1", env=env)
    assert r.returncode == 0, _out(r)


def _state(d):
    return json.loads((Path(d) / ".ratchet/runs/f/state.json").read_text())


def _cp(d):
    return _state(d)["checkpoints"]["CP1"]


def _base_tree(d):
    return _cp(d)["base_tree"]


def _age(d, path, secs):
    """Dates `path` exactly `secs` seconds after the open round's opened_at (the engine's own clock reading)."""
    opened = _cp(d)["adv_round"]["opened_at"]
    ts = dt.datetime.fromisoformat(str(opened).replace("Z", "+00:00")).timestamp() + secs
    os.utime(path, (ts, ts))


def _record_aged(d, rnd, by, age, env=None, over=None):
    """Writes the round's host reviews (`by`: reviewer id -> findings), dates each `age` seconds after the round
    opened, and runs adv-record. Returns (result, paths)."""
    paths = [g.write_review(d, rnd, rv["id"], by.get(rv["id"], []), **(over or {})) for rv in rnd["reviewers"]]
    for p in paths:
        _age(d, p, age)
    return tf._record(d, rnd, paths, env=env), paths


def _text_of_host_row(rnd, row=None):
    """What a host sub-agent is told: the row's launch instruction plus the round's review rules."""
    row = row or rnd["reviewers"][0]
    return row["how"] + "\n" + "\n".join(rnd.get("review_rules") or [])


def _prompt(d, env, row):
    """The prompt external_review.py would send for `row` (an other-company reviewer), read with --dry-run through
    the exact command `reviewers --json` gives."""
    argv = shlex.split(row["how"])
    argv[0] = sys.executable
    p = subprocess.run(argv + ["--dry-run"], cwd=d, capture_output=True, text=True, env=env)
    assert p.returncode == 0, _out(p)
    m = re.search(r"prompt written: (.+) \(\d+ chars\)", p.stdout)
    assert m, p.stdout
    f = Path(m.group(1))
    text = f.read_text()
    shutil.rmtree(f.parent, ignore_errors=True)
    return text


def _since(p, prev, cur=None):
    """The prompt measures from the tree a reviewer last saw (`prev`) to the current one (`cur`): both tree ids
    (first 10 characters) appear, or its header says so in words."""
    ids = prev[:10] in p and (cur is None or cur[:10] in p)
    return ids or re.search(r"since the (reviewed|previous|last) (review )?(round|tree)", p, re.I) is not None


def _added(p, marker):
    return any(l.startswith("+") and not l.startswith("+++") and marker in l for l in p.splitlines())


def _removed(p, marker):
    return any(l.startswith("-") and not l.startswith("---") and marker in l for l in p.splitlines())


def _rewrite_cli(b, name, std, corr=NO_FINDINGS):
    """Changes what the fake CLI answers from now on (same private PATH)."""
    script = (tf._FAKE.replace("__STANDARDS__", std.replace("'", "'\\''"))
                      .replace("__CORRECTNESS__", corr.replace("'", "'\\''"))
                      .replace("__NAME__", name))
    (b / name).write_text(script)
    (b / name).chmod(0o755)


def _reply(*findings):
    return json.dumps({"findings": list(findings)})


def _ext_start(name, findings=(), cfg=(), risk="normal", depth=None):
    """A project (inside the caller's _own_home block) whose other-company provider `codex` answers `findings` as
    codex-standards, seeded with the three source files. Returns (project, env, fake-CLI folder)."""
    depth = depth or {"normal": ["codex-standards", "host-correctness"]}
    d, env = tf._ext_project(name, depth, [("codex", "openai", "gpt-5")], risk=risk, config=list(cfg),
                             replies={"codex": (_reply(*findings), NO_FINDINGS)})
    _seed(d, env)
    return d, env, Path(env["PATH"])


def _ext_record_aged(d, rnd, env, age, expect):
    """Like tf._ext_record, with every review file dated `age` seconds after the round opened."""
    paths = []
    for rv in rnd["reviewers"]:
        if rv.get("kind") == "command":
            paths.append(tf._ext_review(d, rnd, rv["id"], env))
        else:
            paths.append(g.write_review(d, rnd, rv["id"], []))
    for p in paths:
        _age(d, p, age)
    r = tf._record(d, rnd, paths, env=env)
    assert r.returncode == expect, _out(r)
    return r, paths


def _ext_blocked_round(d, env, tag):
    """Round 1 recorded blocked by codex-standards' findings; every finding triaged fix. Returns round 1."""
    rnd1 = tf._open(d, env=env)
    check(f"[{tag}] (fixture) round 1 names codex-standards and host-correctness",
          sorted(tf._ids(rnd1)) == ["codex-standards", "host-correctness"], tf._ids(rnd1))
    tf._ext_record(d, rnd1, env)
    return rnd1


def _fix_round_host(name, min_s, fix_lines=None, tag="CP6-T14", fix="append"):
    """Host-only project: round 1 blocked by the standards reviewer (review dated well past the minimum), the
    finding triaged fix, a fix of `fix_lines` code lines made. Returns (project, fix round, reviewer id)."""
    d = tf._start(name, config=[f"min_review_seconds={min_s}", "checkin_round=99"])
    rnd1 = tf._open(d)
    rid = tf._pick(rnd1, "standards")
    r, _ = _record_aged(d, rnd1, {rid: [tf._finding("ZT-1")]}, age=min_s + 5)
    assert r.returncode == 2, _out(r)
    tf._triage(d, [f"{rid}/ZT-1"])
    if fix_lines is not None:
        _append_code(d, "src/f.js", fix_lines, name)
    rnd2 = tf._open(d)
    check(f"[{tag}] (fixture) round 2 is the one-reviewer fix round after the block",
          tf._ids(rnd2) == [rid] and rnd2.get("small_fix") is True and tf._after_block(rnd2), (tf._ids(rnd2),))
    return d, rnd2, rid


def _too_fast(r):
    return r.returncode != 0 and "too fast" in _out(r)


# ------------------------------------------------------------------------------------------------ CP6-T01..T10

def t01_other_provider_after_a_block_sees_only_the_fix():
    with tf._own_home():
        d, env, _b = _ext_start("e01", [_finding("ZA-1", "src/alpha.js", 2, "DESC-ALPHA input is not checked",
                                                  "FIX-ALPHA check it")])
        rnd1 = _ext_blocked_round(d, env, "CP6-T01")
        tf._triage(d, ["codex-standards/ZA-1"], env=env)
        _edit(d, "src/alpha.js", _src("alpha", ALPHA).replace("alpha_two = 2;", "alpha_two = 22; // ZQ-NEW-ALPHA"), env)
        rnd2 = tf._open(d, env=env)
        check("[CP6-T01] reviewers --json marks a one-reviewer fix round for codex-standards",
              tf._ids(rnd2) == ["codex-standards"] and rnd2.get("small_fix") is True and tf._after_block(rnd2),
              (tf._ids(rnd2), rnd2.get("small_fix"), tf._kind(rnd2)))
        p = _prompt(d, env, rnd2["reviewers"][0])
        check("[CP6-T01] the prompt shows the fixer's change (ZQ-NEW-ALPHA as an added line)",
              _added(p, "ZQ-NEW-ALPHA"), p[-1500:])
        check("[CP6-T01] the prompt names src/alpha.js as changed by the fix", "src/alpha.js" in p)
        check("[CP6-T01] the prompt has neither the bravo nor the charlie marker",
              BRAVO not in p and CHARLIE not in p, [m for m in (BRAVO, CHARLIE) if m in p])
        check("[CP6-T01] the prompt does not hold the step's test file content", TEST_BODY not in p)
        check("[CP6-T01] its header is not 'Changes in this checkpoint' from the step's start",
              FULL_HEADER not in p and _base_tree(d)[:10] not in p, _base_tree(d)[:10])
        check("[CP6-T01] its header measures from the reviewed round's tree to the current one",
              _since(p, rnd1["fingerprint"], rnd2["fingerprint"]),
              (rnd1["fingerprint"][:10], rnd2["fingerprint"][:10]))


def _finding_windows(p, fid):
    """The text around each occurrence of finding id `fid` in prompt `p` (the finding's own listing)."""
    return [p[max(0, m.start() - 400): m.end() + 600] for m in re.finditer(re.escape(fid), p)]


def t02_that_reviewer_is_told_the_findings_to_fix():
    with tf._own_home():
        fb = _finding("ZB-1", "src/bravo.js", 4217, "DESC-B the handler skips validation", "FIX-B validate it")
        fa = _finding("ZA-2", "src/alpha.js", 4218, "DESC-A the value is not escaped", "FIX-A escape it")
        d, env, _b = _ext_start("e02", [fb, fa], cfg=["checkin_round=99"])
        _ext_blocked_round(d, env, "CP6-T02")
        g.rx(d, "triage", "CP1", "codex-standards/ZB-1=rebut:REASON-B the path is unreachable for users", env=env)
        g.rx(d, "triage", "CP1", 'codex-standards/ZA-2=fix:"a developer really hits this"', env=env)
        _edit(d, "src/alpha.js", _src("alpha", ALPHA).replace("alpha_two = 2;", "alpha_two = 22;"), env)
        rnd2 = tf._open(d, env=env)
        check("[CP6-T02] (fixture) round 2 is codex-standards' fix round",
              tf._ids(rnd2) == ["codex-standards"] and rnd2.get("small_fix") is True and tf._after_block(rnd2),
              tf._ids(rnd2))
        p = _prompt(d, env, rnd2["reviewers"][0])
        for fid, desc, fix, file, line in (("ZB-1", "DESC-B", "FIX-B", "src/bravo.js", "4217"),
                                           ("ZA-2", "DESC-A", "FIX-A", "src/alpha.js", "4218")):
            wins = _finding_windows(p, fid)
            check(f"[CP6-T02] the prompt lists {fid} with its description, fix, severity, file and line",
                  any(desc in w and fix in w and "major" in w and file in w and line in w for w in wins),
                  f"{len(wins)} windows; description present: {desc in p}, fix present: {fix in p}")
        check("[CP6-T02] the prompt carries the rebuttal reason for the rebutted finding", "REASON-B" in p)


def t03_a_full_round_still_gets_the_whole_diff():
    with tf._own_home():
        d, env, _b = _ext_start("e03")
        rnd1 = tf._open(d, env=env)
        row = next(rv for rv in rnd1["reviewers"] if rv.get("kind") == "command")
        p = _prompt(d, env, row)
        check("[CP6-T03] the prompt is headed 'Changes in this checkpoint' from the step's start",
              FULL_HEADER in p and _base_tree(d)[:10] in p, p[:200])
        check("[CP6-T03] it holds all three source files' changes",
              all(_added(p, m) for m in (ALPHA, BRAVO, CHARLIE)), [m for m in (ALPHA, BRAVO, CHARLIE) if m not in p])
        check("[CP6-T03] it holds the test file's content too", TEST_BODY in p)
        low = p.lower()
        check("[CP6-T03] it has no fix-only header and no findings-to-fix section",
              "findings to fix" not in low and "meant to fix" not in low and "fix round" not in low
              and "since the reviewed round" not in low, "")


def _approved_ext(name, cfg=(), risk="normal", depth=None):
    d, env, b = _ext_start(name, cfg=cfg, risk=risk, depth=depth)
    rnd1 = tf._open(d, env=env)
    tf._ext_record(d, rnd1, env, expect=0)
    return d, env, rnd1


def t04_a_small_fix_after_approval_is_just_those_lines():
    with tf._own_home():
        d, env, rnd1 = _approved_ext("e04")
        _append_code(d, "src/bravo.js", 2, "e04", env, first_marker="ZQ-TWO-LINE")
        rnd2 = tf._open(d, env=env)
        check("[CP6-T04] reviewers --json: small_fix true with one reviewer",
              rnd2.get("small_fix") is True and len(rnd2["reviewers"]) == 1 and tf._after_approval(rnd2),
              (rnd2.get("small_fix"), tf._ids(rnd2), tf._kind(rnd2)))
        row = rnd2["reviewers"][0]
        check("[CP6-T04] (fixture) the one reviewer is an other-company reviewer", row.get("kind") == "command", row)
        p = _prompt(d, env, row)
        check("[CP6-T04] the prompt shows the ZQ-TWO-LINE change", _added(p, "ZQ-TWO-LINE"), p[-1200:])
        check("[CP6-T04] it is measured from the approved tree: no alpha or charlie marker, not the step's start",
              ALPHA not in p and CHARLIE not in p and _since(p, rnd1["fingerprint"])
              and FULL_HEADER not in p, [m for m in (ALPHA, CHARLIE) if m in p])
        check("[CP6-T04] it says there are no open findings to fix",
              re.search(r"no (open )?(blocking )?findings", p, re.I) is not None, "")


def t05_an_over_limit_fix_after_approval_keeps_the_full_round():
    with tf._own_home():
        d, env, _rnd1 = _approved_ext("e05")
        _append_code(d, "src/bravo.js", 25, "e05", env)
        rnd2 = tf._open(d, env=env)
        check("[CP6-T05] reviewers --json: small_fix false and the step's whole set of two reviewers",
              rnd2.get("small_fix") is not True and len(rnd2["reviewers"]) == 2,
              (rnd2.get("small_fix"), tf._ids(rnd2)))
        row = next(rv for rv in rnd2["reviewers"] if rv.get("kind") == "command")
        p = _prompt(d, env, row)
        check("[CP6-T05] the prompt holds all three files' diffs under the full header",
              FULL_HEADER in p and all(_added(p, m) for m in (ALPHA, BRAVO, CHARLIE)), "")
        a, b_ = p.find("diff --git a/src/alpha.js"), p.find("diff --git a/src/bravo.js")
        check("[CP6-T05] the file changed since the last round (bravo) comes first",
              0 <= b_ < a, (b_, a))


def t06_a_high_risk_fix_after_approval_keeps_the_full_round():
    with tf._own_home():
        depth = {"normal": ["codex-standards", "host-correctness"],
                 "high": ["codex-standards", "codex-correctness", "host-standards", "host-correctness"]}
        d, env, _rnd1 = _approved_ext("e06", risk="high", depth=depth)
        _append_code(d, "src/bravo.js", 2, "e06", env)
        rnd2 = tf._open(d, env=env)
        check("[CP6-T06] the round is the step's full four-reviewer set, not a fix round",
              rnd2.get("small_fix") is not True and len(rnd2["reviewers"]) == 4,
              (rnd2.get("small_fix"), tf._ids(rnd2)))
        row = next(rv for rv in rnd2["reviewers"] if rv["id"] == "codex-standards")
        p = _prompt(d, env, row)
        check("[CP6-T06] the prompt holds the whole step's diff",
              FULL_HEADER in p and all(_added(p, m) for m in (ALPHA, BRAVO, CHARLIE)), "")


def t07_a_second_fix_round_shows_only_the_second_fix():
    with tf._own_home():
        f1 = _finding("ZR-1", "src/alpha.js", 2, "DESC-R1 first problem", "FIX-R1 fix the first")
        f2 = _finding("ZR-2", "src/bravo.js", 3, "DESC-R2 second problem", "FIX-R2 fix the second")
        d, env, b = _ext_start("e07", [f1], cfg=["checkin_round=99"])
        rnd1 = _ext_blocked_round(d, env, "CP6-T07")
        tf._triage(d, ["codex-standards/ZR-1"], env=env)
        _edit(d, "src/alpha.js", _src("alpha", ALPHA).replace("alpha_two = 2;", "alpha_two = 2; // ZQ-FIX-A"), env)
        rnd2 = tf._open(d, env=env)
        check("[CP6-T07] (fixture) round 2 is the fix round", rnd2["round"] == 2 and rnd2.get("small_fix") is True,
              rnd2.get("round"))
        _rewrite_cli(b, "codex", _reply(f2))
        tf._ext_record(d, rnd2, env)
        tf._triage(d, ["codex-standards/ZR-2"], env=env)
        _edit(d, "src/bravo.js", _src("bravo", BRAVO).replace("bravo_two = 2;", "bravo_two = 2; // ZQ-FIX-B"), env)
        rnd3 = tf._open(d, env=env)
        check("[CP6-T07] round 3 is a one-reviewer fix round", rnd3["round"] == 3 and tf._ids(rnd3) == ["codex-standards"]
              and rnd3.get("small_fix") is True and tf._after_block(rnd3), (rnd3["round"], tf._ids(rnd3)))
        check("[CP6-T07] it still counts as round 3 toward the step and the run",
              tf._counters(d) == (3, 3), tf._counters(d))
        p = _prompt(d, env, rnd3["reviewers"][0])
        check("[CP6-T07] the prompt shows fix B and round 2's open finding",
              _added(p, "ZQ-FIX-B") and "DESC-R2" in p and "ZR-2" in p, "")
        check("[CP6-T07] it does not show fix A or round 1's finding",
              "ZQ-FIX-A" not in p and "DESC-R1" not in p and "ZR-1" not in p,
              [m for m in ("ZQ-FIX-A", "DESC-R1", "ZR-1") if m in p])
        check("[CP6-T07] it is measured from round 2's tree, not round 1's",
              _since(p, rnd2["fingerprint"], rnd3["fingerprint"]) and rnd1["fingerprint"][:10] not in p, "")


def t08_edits_made_before_anyone_reviewed_are_all_shown():
    with tf._own_home():
        d, env, _b = _ext_start("e08", [_finding("ZA-1", "src/alpha.js", 2, "DESC-ALPHA bad", "FIX-ALPHA good")])
        rnd1 = _ext_blocked_round(d, env, "CP6-T08")
        tf._triage(d, ["codex-standards/ZA-1"], env=env)
        _edit(d, "src/alpha.js", _src("alpha", ALPHA).replace("alpha_two = 2;", "alpha_two = 22; // ZQ-NEW-ALPHA"), env)
        rnd2 = tf._open(d, env=env)
        before = tf._counters(d)
        _append_code(d, "src/charlie.js", 1, "e08", env, first_marker="ZQ-LATE")
        rnd2b = tf._open(d, env=env)
        check("[CP6-T08] the replaced round costs nothing and is still a fix round",
              tf._counters(d) == before and rnd2b.get("small_fix") is True and tf._after_block(rnd2b),
              (rnd2b["round"], before, tf._counters(d)))
        p = _prompt(d, env, rnd2b["reviewers"][0])
        check("[CP6-T08] the prompt shows both the alpha fix and the late charlie edit",
              _added(p, "ZQ-NEW-ALPHA") and _added(p, "ZQ-LATE"), p[-1500:])
        check("[CP6-T08] it omits bravo.js", BRAVO not in p and "src/bravo.js" not in p)
        check("[CP6-T08] it is measured from the last tree a reviewer saw (round 1), not the unreviewed round 2",
              _since(p, rnd1["fingerprint"]) and rnd2["fingerprint"][:10] not in p and FULL_HEADER not in p, "")


def t09_a_new_file_and_a_deleted_file_are_shown_correctly():
    with tf._own_home():
        d, env, _b = _ext_start("e09", [_finding("ZA-1", "src/alpha.js", 2, "DESC-ALPHA bad", "FIX-ALPHA good")])
        _ext_blocked_round(d, env, "CP6-T09")
        tf._triage(d, ["codex-standards/ZA-1"], env=env)
        (Path(d) / "src/delta.js").write_text("export const delta = 4; // ZQ-NEWFILE\n")
        (Path(d) / "src/charlie.js").unlink()
        _edit(d, "src/alpha.js", _src("alpha", ALPHA).replace("alpha_two = 2;", "alpha_two = 22;"), env)
        rnd2 = tf._open(d, env=env)
        check("[CP6-T09] (fixture) round 2 is the fix round", tf._ids(rnd2) == ["codex-standards"]
              and rnd2.get("small_fix") is True, tf._ids(rnd2))
        p = _prompt(d, env, rnd2["reviewers"][0])
        check("[CP6-T09] src/delta.js is shown as a new file with its content",
              "src/delta.js" in p and _added(p, "ZQ-NEWFILE"), p[-1500:])
        check("[CP6-T09] src/charlie.js is shown as removed, with its old content as removed lines",
              "src/charlie.js" in p and _removed(p, CHARLIE), p[-1500:])
        check("[CP6-T09] bravo.js's unchanged content is omitted", BRAVO not in p)


def t10_an_accepted_finding_is_not_listed_to_fix():
    with tf._own_home():
        acc = _finding("ZA-1", "src/alpha.js", 2, "DESC-ACCEPTED known limit", "FIX-ACCEPTED leave it")
        fix = _finding("ZB-1", "src/bravo.js", 2, "DESC-FIXED real problem", "FIX-FIXED change it")
        d, env, _b = _ext_start("e10", [acc, fix], cfg=["checkin_round=99"])
        _ext_blocked_round(d, env, "CP6-T10")
        a = g.rx(d, "ask", "accept-finding", "CP1", "codex-standards/ZA-1", env=env)
        assert a.returncode == 0, _out(a)
        g.say(d, "accept: a known limit we live with in this product")
        r = g.rx(d, "accept-finding", "CP1", env=env)
        assert r.returncode == 0, _out(r)
        tf._triage(d, ["codex-standards/ZB-1"], env=env)
        _edit(d, "src/bravo.js", _src("bravo", BRAVO).replace("bravo_two = 2;", "bravo_two = 22;"), env)
        rnd2 = tf._open(d, env=env)
        check("[CP6-T10] (fixture) round 2 is codex-standards' fix round", tf._ids(rnd2) == ["codex-standards"]
              and rnd2.get("small_fix") is True, tf._ids(rnd2))
        p = _prompt(d, env, rnd2["reviewers"][0])
        check("[CP6-T10] the finding that was fixed is listed to fix", "DESC-FIXED" in p and "ZB-1" in p, "")
        check("[CP6-T10] the accepted finding is absent", "DESC-ACCEPTED" not in p and "ZA-1" not in p,
              [m for m in ("DESC-ACCEPTED", "ZA-1") if m in p])


# ------------------------------------------------------------------------------------------------ CP6-T11..T13

def _host_blocked(name, tag):
    d = tf._start(name, config=["checkin_round=99"])
    _seed(d)
    rnd1 = tf._open(d)
    rid = tf._pick(rnd1, "correctness")
    tf._block(d, rnd1, {rid: [_finding("ZC-1", "src/alpha.js", 2, "DESC-C1 value is not validated",
                                       "FIX-C1 validate")]})
    return d, rnd1, rid


def t11_a_host_reviewer_after_a_block_is_told_to_look_only_at_the_fix():
    d, rnd1, rid = _host_blocked("h11", "CP6-T11")
    _edit(d, "src/alpha.js", _src("alpha", ALPHA).replace("alpha_two = 2;", "alpha_two = 22; // ZQ-NEW-ALPHA"))
    rnd2 = tf._open(d)
    check("[CP6-T11] reviewers --json: one reviewer, small_fix true, after a block",
          tf._ids(rnd2) == [rid] and rnd2.get("small_fix") is True and tf._after_block(rnd2),
          (tf._ids(rnd2), rnd2.get("small_fix"), tf._kind(rnd2)))
    t = _text_of_host_row(rnd2)
    check("[CP6-T11] the instruction names the previously reviewed tree (at least 10 characters)",
          rnd1["fingerprint"][:10] in t, rnd1["fingerprint"][:10])
    check("[CP6-T11] it names the round's own fingerprint", rnd2["fingerprint"][:10] in t, "")
    check("[CP6-T11] it names src/alpha.js as the changed file, and not the other files",
          "src/alpha.js" in t and "src/bravo.js" not in t and "src/charlie.js" not in t, t[-800:])
    check("[CP6-T11] it lists the open finding's id and description", "ZC-1" in t and "DESC-C1" in t, t[-800:])
    check("[CP6-T11] it does not name the step's starting tree", _base_tree(d)[:10] not in t, _base_tree(d)[:10])


def t12_a_host_reviewer_after_a_small_fix_is_told_the_same():
    d = tf._start("h12")
    _seed(d)
    rnd1 = tf._open(d)
    tf._approve(d, rnd1)
    _append_code(d, "src/bravo.js", 2, "h12", first_marker="ZQ-TWO-LINE")
    rnd2 = tf._open(d)
    check("[CP6-T12] reviewers --json: small_fix true after an approval",
          rnd2.get("small_fix") is True and len(rnd2["reviewers"]) == 1 and tf._after_approval(rnd2),
          (rnd2.get("small_fix"), tf._kind(rnd2)))
    t = _text_of_host_row(rnd2)
    check("[CP6-T12] the instruction names the approved tree", rnd1["fingerprint"][:10] in t, rnd1["fingerprint"][:10])
    check("[CP6-T12] it names the current fingerprint and src/bravo.js as the changed file",
          rnd2["fingerprint"][:10] in t and "src/bravo.js" in t and "src/alpha.js" not in t, t[-800:])
    check("[CP6-T12] it says there are no open findings",
          re.search(r"no (open )?(blocking )?findings", t, re.I) is not None, t[-800:])
    check("[CP6-T12] it does not name the step's starting tree", _base_tree(d)[:10] not in t, "")


def t13_a_host_reviewer_in_a_full_round_gets_no_fix_only_instruction():
    d = tf._start("h13a")
    _seed(d)
    rnd1 = tf._open(d)
    t1 = "\n".join(_text_of_host_row(rnd1, rv) for rv in rnd1["reviewers"])
    low = t1.lower()
    check("[CP6-T13] round 1 carries no previous-tree range and no findings-to-fix list",
          "findings to fix" not in low and "meant to fix" not in low and "since the reviewed round" not in low
          and "open findings" not in low, t1[-600:])
    check("[CP6-T13] round 1's review rules are the same as today's (none at normal risk)",
          (rnd1.get("review_rules") or []) == [], rnd1.get("review_rules"))
    d2 = tf._start("h13b")
    _seed(d2)
    r1 = tf._open(d2)
    tf._approve(d2, r1)
    _append_code(d2, "src/bravo.js", 25, "h13b")
    r2 = tf._open(d2)
    check("[CP6-T13] (fixture) the over-limit fix is the full two-reviewer round",
          r2.get("small_fix") is not True and len(r2["reviewers"]) == 2, tf._ids(r2))
    t2 = "\n".join(_text_of_host_row(r2, rv) for rv in r2["reviewers"])
    low2 = t2.lower()
    check("[CP6-T13] the over-limit round names no previous-tree range",
          r1["fingerprint"][:10] not in t2 and "findings to fix" not in low2 and "no open findings" not in low2,
          t2[-800:])
    check("[CP6-T13] its review rules are today's 'look hardest at the code changed since the last round'",
          any("look hardest at the code changed since the last round (src/bravo.js" in x
              for x in r2.get("review_rules") or []), r2.get("review_rules"))


# ------------------------------------------------------------------------------------------------ CP6-T14..T21

def t14_a_two_line_fix_review_in_ten_seconds_is_accepted():
    d, rnd2, rid = _fix_round_host("m14", 20, 2, "CP6-T14")
    p = g.write_review(d, rnd2, rid, [])
    _age(d, p, 0)
    r0 = tf._record(d, rnd2, [p])
    check("[CP6-T14] a review dated 0 seconds after the round opened is refused as too fast "
          "(the shorter minimum has a floor above zero)", _too_fast(r0), _out(r0))
    _age(d, p, 10.2)
    r1 = tf._record(d, rnd2, [p])
    check("[CP6-T14] a review dated 10 seconds after it opened is accepted (min_review_seconds 20)",
          r1.returncode == 0, _out(r1))


def t15_a_full_round_still_needs_the_configured_minimum():
    d = tf._start("m15", config=["min_review_seconds=20"])
    rnd1 = tf._open(d)
    check("[CP6-T15] (fixture) round 1 is the full two-reviewer round", len(rnd1["reviewers"]) == 2
          and rnd1.get("small_fix") is not True, tf._ids(rnd1))
    paths = [g.write_review(d, rnd1, rv["id"], []) for rv in rnd1["reviewers"]]
    for p in paths:
        _age(d, p, 10)
    r10 = tf._record(d, rnd1, paths)
    check("[CP6-T15] reviews dated 10 seconds after the round opened are refused as too fast", _too_fast(r10),
          _out(r10))
    for p in paths:
        _age(d, p, 20.2)
    r20 = tf._record(d, rnd1, paths)
    check("[CP6-T15] reviews dated 20 seconds after it opened are accepted", r20.returncode == 0, _out(r20))


def t16_a_bigger_fix_needs_more_review_time_than_a_tiny_one():
    d = tf._start("m16", config=["min_review_seconds=20", "checkin_round=99"])
    rnd1 = tf._open(d)
    rid = tf._pick(rnd1, "standards")
    r, _ = _record_aged(d, rnd1, {rid: [tf._finding("ZT-1")]}, age=25)
    assert r.returncode == 2, _out(r)
    tf._triage(d, [f"{rid}/ZT-1"])
    tf._write_code(d, 400, "m16")
    rnd2 = tf._open(d)
    check("[CP6-T16] (fixture) round 2 is the one-reviewer fix round for a ~400-line fix",
          tf._ids(rnd2) == [rid] and rnd2.get("small_fix") is True and tf._after_block(rnd2), tf._ids(rnd2))
    p = g.write_review(d, rnd2, rid, [])
    _age(d, p, 10)
    r10 = tf._record(d, rnd2, [p])
    check("[CP6-T16] a review dated 10 seconds after the round opened is refused (the minimum scales up "
          "with the diff)", _too_fast(r10), _out(r10))
    _age(d, p, 20.2)
    r20 = tf._record(d, rnd2, [p])
    check("[CP6-T16] a review dated 20 seconds after it opened is accepted", r20.returncode == 0, _out(r20))


def t17_the_shorter_minimum_never_exceeds_the_configured_one():
    for min_s, age in ((5, 5.2), (0, 0.2)):
        d, rnd2, rid = _fix_round_host(f"m17x{min_s}", min_s, 2, "CP6-T17")
        p = g.write_review(d, rnd2, rid, [])
        _age(d, p, age)
        r = tf._record(d, rnd2, [p])
        check(f"[CP6-T17] min_review_seconds {min_s}: a 2-line fix review dated {age - 0.2:g} seconds after the "
              "round opened is accepted (a fix round's minimum is never higher)", r.returncode == 0, _out(r))


def t18_only_a_real_small_fix_after_approval_gets_the_shorter_minimum():
    # (a) a 2-line fix after approval
    d = tf._start("m18a", config=["min_review_seconds=20"])
    r1 = tf._open(d)
    ok_, _ = _record_aged(d, r1, {}, age=25)
    assert ok_.returncode == 0, _out(ok_)
    _append_code(d, "src/f.js", 2, "m18a")
    r2 = tf._open(d)
    check("[CP6-T18] (a) a 2-line fix after approval is a one-reviewer small fix",
          r2.get("small_fix") is True and tf._after_approval(r2), (tf._ids(r2), tf._kind(r2)))
    rec, _ = _record_aged(d, r2, {}, age=10.2)
    check("[CP6-T18] (a) its review dated 10 seconds after the round opened is accepted", rec.returncode == 0,
          _out(rec))
    # (b) an over-limit fix after approval: a full round
    d = tf._start("m18b", config=["min_review_seconds=20"])
    r1 = tf._open(d)
    ok_, _ = _record_aged(d, r1, {}, age=25)
    assert ok_.returncode == 0, _out(ok_)
    _append_code(d, "src/f.js", 25, "m18b")
    r2 = tf._open(d)
    check("[CP6-T18] (b) an over-limit fix after approval is the full two-reviewer round",
          r2.get("small_fix") is not True and len(r2["reviewers"]) == 2, tf._ids(r2))
    rec, _ = _record_aged(d, r2, {}, age=10)
    check("[CP6-T18] (b) its reviews dated 10 seconds after it opened are refused as too fast", _too_fast(rec),
          _out(rec))
    # (c) a 2-line fix on a high-risk step: a full round
    d = tf._start("m18c", risk="high", config=["min_review_seconds=20"])
    r1 = tf._open(d)
    ok_, _ = _record_aged(d, r1, {}, age=25)
    assert ok_.returncode == 0, _out(ok_)
    _append_code(d, "src/f.js", 2, "m18c")
    r2 = tf._open(d)
    check("[CP6-T18] (c) a 2-line fix on a high-risk step is the full four-reviewer round",
          r2.get("small_fix") is not True and len(r2["reviewers"]) == 4, tf._ids(r2))
    rec, _ = _record_aged(d, r2, {}, age=10)
    check("[CP6-T18] (c) its reviews dated 10 seconds after it opened are refused as too fast", _too_fast(rec),
          _out(rec))


def t19_a_small_fix_without_one_reviewer_keeps_the_full_diff_and_minimum():
    with tf._own_home():
        depth = {"normal": ["codex-standards", "host-correctness"], "low": ["host-standards", "host-correctness"]}
        d, env, _b = _ext_start("e19", cfg=["min_review_seconds=20"], depth=depth)
        rnd1 = tf._open(d, env=env)
        _ext_record_aged(d, rnd1, env, 25, 0)
        _append_code(d, "src/bravo.js", 2, "e19", env, first_marker="ZQ-TWO-LINE")
        rnd2 = tf._open(d, env=env)
        check("[CP6-T19] the round is not labelled a small fix and keeps the step's two reviewers",
              rnd2.get("small_fix") is not True and len(rnd2["reviewers"]) == 2,
              (rnd2.get("small_fix"), tf._ids(rnd2)))
        row = next(rv for rv in rnd2["reviewers"] if rv.get("kind") == "command")
        p = _prompt(d, env, row)
        check("[CP6-T19] its prompt holds the whole step's diff under the full header",
              FULL_HEADER in p and all(_added(p, m) for m in (ALPHA, BRAVO, CHARLIE)), "")
        paths = [tf._ext_review(d, rnd2, rv["id"], env) if rv.get("kind") == "command"
                 else g.write_review(d, rnd2, rv["id"], []) for rv in rnd2["reviewers"]]
        for pth in paths:
            _age(d, pth, 10)
        r = tf._record(d, rnd2, paths, env=env)
        check("[CP6-T19] reviews dated 10 seconds after it opened are refused as too fast (min 20)",
              _too_fast(r), _out(r))
    # the same customised review_depth.low must not touch a fix round after a block
    with tf._own_home():
        depth = {"normal": ["codex-standards", "host-correctness"], "low": ["host-standards", "host-correctness"]}
        fnd = _finding("ZA-1", "src/alpha.js", 2, "DESC-ALPHA bad", "FIX-ALPHA good")
        d, env, _b = _ext_start("e19b", [fnd], cfg=["checkin_round=99"], depth=depth)
        rnd1 = tf._open(d, env=env)
        tf._ext_record(d, rnd1, env)
        tf._triage(d, ["codex-standards/ZA-1"], env=env)
        _edit(d, "src/alpha.js", _src("alpha", ALPHA).replace("alpha_two = 2;", "alpha_two = 22; // ZQ-NEW-ALPHA"), env)
        rnd2 = tf._open(d, env=env)
        check("[CP6-T19] with review_depth.low customised, the post-block fix round's one reviewer is still "
              "the blocker (codex-standards), not a review_depth.low reviewer",
              tf._ids(rnd2) == ["codex-standards"] and rnd2.get("small_fix") is True and tf._after_block(rnd2),
              (tf._ids(rnd2), rnd2.get("small_fix"), tf._kind(rnd2)))
        p2 = _prompt(d, env, rnd2["reviewers"][0])
        check("[CP6-T19] and its prompt is the fixer's diff only", _added(p2, "ZQ-NEW-ALPHA")
              and BRAVO not in p2 and CHARLIE not in p2, p2[-600:])


def t20_approving_a_fix_round_still_has_to_list_changed_files():
    d = tf._start("m20", config=["checkin_round=99"])
    rnd1 = tf._open(d)
    rid = tf._pick(rnd1, "standards")
    tf._block(d, rnd1, {rid: [tf._finding("ZT-1")]})
    tf._write_code(d, 4, "m20")
    rnd2 = tf._open(d)
    check("[CP6-T20] (fixture) round 2 is the fix round", tf._ids(rnd2) == [rid] and rnd2.get("small_fix") is True,
          tf._ids(rnd2))
    p = g.write_review(d, rnd2, rid, [], files_reviewed=[])
    r = tf._record(d, rnd2, [p])
    check("[CP6-T20] a clean review with no files_reviewed list is refused for not listing the changed files",
          r.returncode != 0 and "files_reviewed" in _out(r), _out(r))
    p = g.write_review(d, rnd2, rid, [], files_reviewed=["src/a.js"])
    r = tf._record(d, rnd2, [p])
    check("[CP6-T20] a review listing only a file nothing changed in is refused too",
          r.returncode != 0 and "files_reviewed" in _out(r), _out(r))
    p = g.write_review(d, rnd2, rid, [], files_reviewed=["src/f.js"])
    r = tf._record(d, rnd2, [p])
    check("[CP6-T20] a review listing only the file the fix changed is accepted", r.returncode == 0, _out(r))


def t21_an_other_provider_fix_review_end_to_end_is_accepted_at_ten_seconds():
    with tf._own_home():
        fnd = _finding("ZA-1", "src/alpha.js", 2, "DESC-ALPHA bad", "FIX-ALPHA good")
        d, env, b = _ext_start("e21", [fnd], cfg=["min_review_seconds=20", "checkin_round=99"])
        rnd1 = tf._open(d, env=env)
        _ext_record_aged(d, rnd1, env, 25, 2)
        tf._triage(d, ["codex-standards/ZA-1"], env=env)
        _append_code(d, "src/alpha.js", 2, "e21", env)
        _rewrite_cli(b, "codex", NO_FINDINGS)
        rnd2 = tf._open(d, env=env)
        row = rnd2["reviewers"][0]
        check("[CP6-T21] the reviewer that runs is codex-standards, an other-company command reviewer",
              tf._ids(rnd2) == ["codex-standards"] and row.get("kind") == "command" and rnd2.get("small_fix") is True
              and tf._after_block(rnd2), (tf._ids(rnd2), row.get("kind")))
        out = tf._ext_review(d, rnd2, "codex-standards", env)
        _age(d, out, 10.2)
        r = tf._record(d, rnd2, [out], env=env)
        check("[CP6-T21] adv-record accepts the review dated 10 seconds after the round opened",
              r.returncode == 0, _out(r))
        rev = json.loads(Path(out).read_text())
        sig = rev.get("signature") or {}
        check("[CP6-T21] the review is signed by external_review.py and stamped with the round's fingerprint",
              sig.get("by") == "external_review.py" and rev.get("fingerprint") == rnd2["fingerprint"], sig)
        check("[CP6-T21] its files_reviewed lists the file the fix changed", "src/alpha.js" in (rev.get("files_reviewed") or []),
              rev.get("files_reviewed"))
        g_ = _cp(d)["gates"]["adversarial"]
        verdict = (g_.get("rounds") or [{}])[-1].get("verdicts") or [{}]
        check("[CP6-T21] the round is recorded as signed and the gate passed",
              g_.get("status") == "passed" and verdict[0].get("origin") == "signed by external_review.py",
              (g_.get("status"), verdict))


# ------------------------------------------------------------------------------------------------ CP6-T22

def _read(rel):
    return (ROOT / rel).read_text()


def _paragraphs(text):
    return [x for x in text.split("\n") if x.strip()]


def _some(text, *patterns):
    """True when one paragraph (line) matches every pattern, so the words belong to one statement."""
    return any(all(re.search(p, para, re.I | re.S) for p in patterns) for para in _paragraphs(text))


SEES = [r"fix rounds?", r"(only|just)[^.\n]{0,160}(fixer'?s?|fix'?s?)\s+(own\s+)?(diff|change)",
        r"findings?[^.\n]{0,160}(meant|supposed|intended|being)\s+to\s+(fix|be fixed)|findings?\s+to\s+fix"]
MINIMUM = [r"min_review_seconds", r"fix rounds?", r"(shorter|smaller|lower|scaled|scales|scaling)",
           r"(diff|size|lines)", r"(only|never)[^.\n]{0,100}fix rounds?|fix rounds? only",
           r"full round[^.\n]{0,160}(keeps?|still needs?|still uses?|unchanged)|(keeps?|still needs?)[^.\n]{0,80}full round"]


def t22_the_docs_describe_what_a_fix_reviewer_sees_and_the_shorter_minimum():
    docs = {"README.md": _read("README.md"),
            "skills/ratchet-adversarial-loop/SKILL.md": _read("skills/ratchet-adversarial-loop/SKILL.md"),
            "skills/ratchet-orchestrator/SKILL.md": _read("skills/ratchet-orchestrator/SKILL.md"),
            "skills/ratchet-orchestrator/references/schemas.md":
                _read("skills/ratchet-orchestrator/references/schemas.md"),
            "agents/ratchet-adversary.md": _read("agents/ratchet-adversary.md")}
    for name, text in docs.items():
        check(f"[CP6-T22] {name} says a fix round's reviewer sees only the fixer's diff plus the findings it was "
              "meant to fix", _some(text, *SEES), "no paragraph says it")
    for name in ("README.md", "skills/ratchet-adversarial-loop/SKILL.md",
                 "skills/ratchet-orchestrator/references/schemas.md"):
        check(f"[CP6-T22] {name} says the shorter, diff-scaled minimum applies only to fix rounds and a full round "
              "keeps min_review_seconds", _some(docs[name], *MINIMUM), "no paragraph says it")
        check(f"[CP6-T22] {name} names the default of 20 seconds beside min_review_seconds",
              _some(docs[name], r"min_review_seconds", r"\b20\b", r"fix rounds?"), "")
    loop = docs["skills/ratchet-adversarial-loop/SKILL.md"]
    exc = r"(except|unless|other than|apart from|but for|besides)[^.\n]{0,160}fix rounds?|fix rounds?[^.\n]{0,160}(except|exception)"
    check("[CP6-T22] the adversarial-loop SKILL's 'earlier rounds' findings' line now excepts fix rounds",
          any(re.search(r"earlier rounds?'? findings", l, re.I) and re.search(exc, l, re.I) for l in loop.split("\n")),
          "")
    check("[CP6-T22] the adversarial-loop SKILL's 'full diff' line now excepts fix rounds",
          any(re.search(r"full diff", l, re.I) and re.search(exc, l, re.I) for l in loop.split("\n")), "")


CASES = [("CP6-T01", t01_other_provider_after_a_block_sees_only_the_fix),
         ("CP6-T02", t02_that_reviewer_is_told_the_findings_to_fix),
         ("CP6-T03", t03_a_full_round_still_gets_the_whole_diff),
         ("CP6-T04", t04_a_small_fix_after_approval_is_just_those_lines),
         ("CP6-T05", t05_an_over_limit_fix_after_approval_keeps_the_full_round),
         ("CP6-T06", t06_a_high_risk_fix_after_approval_keeps_the_full_round),
         ("CP6-T07", t07_a_second_fix_round_shows_only_the_second_fix),
         ("CP6-T08", t08_edits_made_before_anyone_reviewed_are_all_shown),
         ("CP6-T09", t09_a_new_file_and_a_deleted_file_are_shown_correctly),
         ("CP6-T10", t10_an_accepted_finding_is_not_listed_to_fix),
         ("CP6-T11", t11_a_host_reviewer_after_a_block_is_told_to_look_only_at_the_fix),
         ("CP6-T12", t12_a_host_reviewer_after_a_small_fix_is_told_the_same),
         ("CP6-T13", t13_a_host_reviewer_in_a_full_round_gets_no_fix_only_instruction),
         ("CP6-T14", t14_a_two_line_fix_review_in_ten_seconds_is_accepted),
         ("CP6-T15", t15_a_full_round_still_needs_the_configured_minimum),
         ("CP6-T16", t16_a_bigger_fix_needs_more_review_time_than_a_tiny_one),
         ("CP6-T17", t17_the_shorter_minimum_never_exceeds_the_configured_one),
         ("CP6-T18", t18_only_a_real_small_fix_after_approval_gets_the_shorter_minimum),
         ("CP6-T19", t19_a_small_fix_without_one_reviewer_keeps_the_full_diff_and_minimum),
         ("CP6-T20", t20_approving_a_fix_round_still_has_to_list_changed_files),
         ("CP6-T21", t21_an_other_provider_fix_review_end_to_end_is_accepted_at_ten_seconds),
         ("CP6-T22", t22_the_docs_describe_what_a_fix_reviewer_sees_and_the_shorter_minimum)]


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
