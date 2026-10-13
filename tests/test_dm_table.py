#!/usr/bin/env python3
"""
CP1 tests: the final review shows a proven or unproven table.   python3 tests/test_dm_table.py

At the final review (phase awaiting_final_review) `ratchet.py status` lists every objective.done_means line as
proven or unproven, with the test cases linked to it (a test case's optional "proves" field) and each one's
result, and for an unproven line a plain reason: no test linked, a linked test failed or has no result, a browser
case no UI gate checked, or an open finding that names the line. A review finding names a line it contradicts in
an optional "done_means" field holding one 1-based line number or a list of them (the same shapes "proves" takes);
an open finding is one `ratchet.py deferred` still lists as left for later (not fixed, not accepted). One verdict
function feeds `status`, `status --json` and `receipt`.

Contract these tests pin (the builder follows it):
  - `status --json` has a top-level "done_means_table": a list with one entry per line, in order, each with its
    1-based "number" (or "line"), "text", "verdict" ("proven" | "unproven"), "cases" (each with "id" and "result")
    and "reasons" (a list of plain strings). For a run with no objective, or with an objective but no test linked
    to any line, the key is still there and holds a plain note (a string) or null instead of a list.
  - `receipt --json`'s "done_means" lines carry the same "verdict" and "reasons".
  - Plain `status` at the final review prints, per line, the line's full text, the word proven or unproven, the
    linked case ids with their results and the reasons. The old neutral "check: <line>" prompts are gone.
  - Mid-run, status shows no table. Nothing here blocks anything (display only).

Every case drives the real CLI (`status`, `status --json`, `receipt`, `deferred --json`) in a throwaway git
project built with tests/_tmp.py's mkdtemp and tests/test_gates.py's helpers, the same pattern as
tests/test_receipt_proof.py. Runs reach awaiting_final_review through the real commands. Per-case test results
are written straight into evidence/CPn/test-report.json (the shape _read_test_report reads) so each linked case's
state is fully controlled; hand edits of state.json (a finding naming a line, a missing gate result) go through
the re-baselined mutate() pattern of tests/test_receipt.py. A plan with no objective is approved through the
legacy-approval helper of tests/test_receipt_proof.py.

Every assertion goes through check(), so the missing feature fails cleanly instead of crashing.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP1-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                     # noqa: E402
import test_gates as g          # noqa: E402  (approve_plan, open_round, write_review, git, say, rx, ENGINE, ENV)
import test_receipt_proof as rp  # noqa: E402  (_bare_cp1, _legacy_approve, _legacy_submit, _write_report)

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

DM = [
    "A user can log in with the right password",
    "A wrong password shows an error and does not log the user in",
    "After 5 wrong attempts the account is locked for 15 minutes",
    "Locked users are told how long to wait",
]
LOCKOUT_DESC = "lockout message never shown"
ABSENT = object()  # a finding with no "done_means" field at all

REBASELINE = ("import importlib.util, sys\nfrom pathlib import Path\n"
              "s = importlib.util.spec_from_file_location('ratchet_fixture', sys.argv[1])\n"
              "m = importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
              "m.record_chain(Path(sys.argv[2]))\n")


def _spath(d, slug="f"):
    return Path(d) / f".ratchet/runs/{slug}/state.json"


def mutate(d, fn, slug="f"):
    """Edits the active run's own state.json (fixture only: a hand edit, never through the engine) and
    re-baselines Ratchet's own integrity record with its own `record_chain` (tests/test_receipt.py's pattern)."""
    p = _spath(d, slug)
    st = json.loads(p.read_text())
    fn(st)
    p.write_text(json.dumps(st))
    r = subprocess.run([sys.executable, "-c", REBASELINE, str(g.ENGINE), str(p)], cwd=d, env=g.ENV,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return st


def _objective(dm=None):
    return {"users": "people who need to sign in to the app", "goal": "sign in safely",
            "done_means": list(dm if dm is not None else DM), "out_of_scope": ["anything not listed in this plan"]}


def _case(cid, proves=None, layer="code", title=None):
    c = {"id": cid, "title": title or f"Test {cid}", "file": f"tests/{cid.lower()}.py", "layer": layer}
    if proves is not None:
        c["proves"] = proves
    return c


def _cp(i):
    return {"id": f"CP{i}", "title": f"Step {i}", "summary": "s", "done_when": ["works"],
            "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
            "tests": {"files": [f"tests/cp{i}.test.js"], "command": f"node --test tests/cp{i}.test.js"}}


def _every_line_linked(done_means, steps):
    """True when every 1-based done-means line is named in some case's "proves" (an int or a list of ints)."""
    linked = set()
    for cases in steps:
        for c in cases:
            p = c.get("proves")
            linked.update([p] if isinstance(p, int) else (p or []))
    return all(i in linked for i in range(1, len(done_means) + 1))


def _setup(name, steps, dm=None, objective=True, approve=True):
    """A throwaway git project with one checkpoint per entry of `steps` (each a list of test-plan cases), a real
    objective with `dm` done-means lines, plan-submitted and (by default) approved."""
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-dm-{name}-"))
    run = rp._bare_cp1(d)
    g.rx(d, "config", "--set", "checkin_round=99")
    plan = {"feature": "x", "checkpoints": [_cp(i) for i in range(1, len(steps) + 1)]}
    if objective:
        plan["objective"] = _objective(dm)
    for i, cases in enumerate(steps, 1):
        (run / "tests" / f"CP{i}.json").write_text(json.dumps({"cases": cases}))
    (run / "checkpoints.json").write_text(json.dumps(plan))
    if objective and not _every_line_linked(plan["objective"]["done_means"], steps):
        # CP2's refusal rule: plan-submit now refuses a plan with a done-means line no case proves. The cases
        # here that deliberately leave a line unlinked (T09 with no "proves" at all, T13, the _mixed_run cases
        # with line 2 unlinked, and the others linking only some lines) model an older plan, submitted before
        # that rule existed, so they go through the legacy path tests/test_receipt_proof.py uses; a fully
        # linked plan still goes through the real plan-submit.
        rp._legacy_submit(d)
    else:
        r = g.rx(d, "plan-submit")
        assert r.returncode == 0, r.stdout + r.stderr
    if approve:
        g.approve_plan(d)
    return d


def _finding(fid, desc, dm=ABSENT):
    f = {"id": fid, "severity": "minor", "kind": f"kind-{fid.lower()}", "file": "src/a.js", "description": desc}
    if dm is not ABSENT:
        f["done_means"] = dm
    return f


def _step(d, i, cases, findings=None):
    """Drives step CP<i> to complete through the real commands. Every code case id gets a passing test in the
    step's test file (f() returns i, so each later step is genuinely red until src/f.js is edited). The one
    adversarial round records `findings` (minor ones at a low-risk step are deferred, never blocking)."""
    cp = f"CP{i}"
    body = "".join(f"t('[{c['id']}] works', () => a.equal(f(), {i}));\n" for c in cases if c.get("layer") == "code")
    (d / f"tests/{cp.lower()}.test.js").write_text(
        "import t from 'node:test'; import a from 'node:assert'; import { f } from '../src/f.js';\n" + body)
    assert g.rx(d, "tests-written", cp).returncode == 0
    r = g.rx(d, "red", cp)
    assert r.returncode == 0, r.stdout + r.stderr
    (d / "src/f.js").write_text(f"export const f = () => {i};\n")
    assert g.rx(d, "implemented", cp).returncode == 0
    r = g.rx(d, "behavior", cp)
    assert r.returncode == 0, r.stdout + r.stderr
    rnd = g.open_round(d, cp)
    # CP3's rule: a finding that names a done-means line blocks its step at any severity, so a line-naming minor
    # can no longer reach the left-for-later list through adv-record. The minor is recorded WITHOUT done_means
    # (deferred by the low-risk rule as before), and _force_dm() then adds the line number onto the recorded
    # deferred entry in state through mutate(), which is all this checkpoint's table reads.
    p = g.write_review(d, rnd, g.R0(rnd), [{k: v for k, v in f.items() if k != "done_means"}
                                           for f in (findings or [])])
    r = g.rx(d, "adv-record", cp, "--reviews", p)
    assert r.returncode == 0, r.stdout + r.stderr
    r = g.rx(d, "complete", cp)
    assert r.returncode == 0, r.stdout + r.stderr


def _finished(name, steps, reports=None, findings=None, dm=None):
    """Every step driven to complete (phase awaiting_final_review). `reports` is {cp: [{"id", "status"}]} written
    as that step's per-case test report after the run; `findings` is {cp: [finding, ...]}."""
    d = _setup(name, steps, dm=dm)
    for i, cases in enumerate(steps, 1):
        _step(d, i, cases, (findings or {}).get(f"CP{i}"))
    st = json.loads(_spath(d).read_text())
    assert st["phase"] == "awaiting_final_review", st["phase"]
    for cp, entries in (reports or {}).items():
        rp._write_report(d, cp, entries)
    _force_dm(d, findings)
    return d


def _force_dm(d, findings):
    """Makes sure each deferred entry carries exactly the "done_means" value its finding was written with, whether
    or not adv-record kept the field (this checkpoint only reads it; setting it is a later step's job)."""
    want = {}
    for cp, fl in (findings or {}).items():
        for f in fl:
            want[(cp, f["id"])] = f.get("done_means", ABSENT)
    if not want:
        return

    def fn(st):
        for cp, c in st["checkpoints"].items():
            for f in c.get("deferred") or []:
                v = want.get((cp, f.get("id")), ABSENT)
                if v is ABSENT:
                    f.pop("done_means", None)
                else:
                    f["done_means"] = v
    mutate(d, fn)


def _passed(*ids):
    return [{"id": i, "status": "passed"} for i in ids]


def _status(d):
    r = g.rx(d, "status")
    return r


def _sjson(d):
    r = g.rx(d, "status", "--json")
    try:
        return r, json.loads(r.stdout)
    except Exception:
        return r, {}


def _table(d):
    r, j = _sjson(d)
    return j.get("done_means_table") if isinstance(j, dict) else None


def _segs(out, dm=None):
    """The slice of `out` for each done-means line: from its full text up to the next other line's text (the last
    one up to the status footer, 'All N steps' or 'Run:'), so a per-line check never reads a neighbour."""
    dm = dm if dm is not None else DM
    res = []
    for i, text in enumerate(dm):
        start = out.find(text)
        if start == -1:
            res.append("")
            continue
        ends = [out.find(x, start + 1) for j, x in enumerate(dm) if j != i]
        ends = [e for e in ends if e != -1]
        if i == len(dm) - 1 or not ends:
            for marker in ("\nAll ", "\nRun: "):
                k = out.find(marker, start + 1)
                if k != -1:
                    ends.append(k)
        res.append(out[start:min(ends) if ends else len(out)])
    return res


def _verdict(seg):
    if re.search(r"unproven", seg, re.I):
        return "unproven"
    if re.search(r"(?<!un)proven", seg, re.I):
        return "proven"
    return None


def _has_id(text, ident):
    return bool(re.search(r"(?<![A-Za-z0-9])" + re.escape(ident) + r"(?![0-9])", text))


def _near(seg, cid, word):
    """`cid` and `word` on one line of `seg`, in either order."""
    return bool(re.search(rf"{re.escape(cid)}[^\n]*{word}|{word}[^\n]*{re.escape(cid)}", seg, re.I))


def _num(e):
    for k in ("number", "line", "n"):
        if isinstance(e.get(k), int):
            return e[k]
    return None


def _rtext(e):
    return " | ".join(str(x) for x in (e.get("reasons") or []))


def _case_result(e, cid):
    for c in e.get("cases") or []:
        if c.get("id") == cid:
            return str(c.get("result"))
    return None


def _clean(r):
    return r.returncode == 0 and "Traceback" not in (r.stdout + r.stderr)


# ------------------------------------------------------------------------------------------------ cases

def cp1_t01_all_lines_proven_with_tests_and_results():
    """[CP1-T01] Final review lists every done-means line as proven, with its tests and results."""
    steps = [[_case("CP1-T01", [1]), _case("CP1-T02", [2]), _case("CP1-T03", [2]), _case("CP1-T04", [3, 4])]]
    d = _finished("t01", steps, reports={"CP1": _passed("CP1-T01", "CP1-T02", "CP1-T03", "CP1-T04")})
    r = _status(d)
    out = r.stdout
    check("[CP1-T01] status exits 0 with no traceback", _clean(r), r.stdout + r.stderr)
    segs = _segs(out)
    for i, t in enumerate(DM):
        check(f"[CP1-T01] line {i + 1}'s full text appears", t in out, out)
        check(f"[CP1-T01] line {i + 1} is marked proven", _verdict(segs[i]) == "proven", segs[i])
    check("[CP1-T01] the word 'unproven' appears nowhere", "unproven" not in out.lower(), out)
    check("[CP1-T01] line 1 names CP1-T01 as passed", _near(segs[0], "CP1-T01", "passed"), segs[0])
    check("[CP1-T01] line 2 names CP1-T02 as passed", _near(segs[1], "CP1-T02", "passed"), segs[1])
    check("[CP1-T01] line 2 names CP1-T03 as passed", _near(segs[1], "CP1-T03", "passed"), segs[1])
    check("[CP1-T01] CP1-T04 appears under line 3", _near(segs[2], "CP1-T04", "passed"), segs[2])
    check("[CP1-T01] CP1-T04 also appears under line 4", _near(segs[3], "CP1-T04", "passed"), segs[3])
    check("[CP1-T01] the old 'check: <line>' prompts are gone", "check:" not in out, out)
    check("[CP1-T01] the heading no longer tells the agent to judge each line itself",
          "check each against" not in out.lower() and "judge" not in out.lower(), out)


def _mixed_run(name, with_finding_on_line_1=False):
    """Line 1 linked to a passed case; line 2 linked to no case; line 3 linked to CP1-T03 whose report entry
    says failed; line 4 linked to CP1-T04 with no report entry and no recorded behavior-gate result."""
    steps = [[_case("CP1-T01", [1]), _case("CP1-T03", [3]), _case("CP1-T04", [4])]]
    findings = {"CP1": [_finding("S1", LOCKOUT_DESC, 1)]} if with_finding_on_line_1 else None
    d = _finished(name, steps, reports={"CP1": [{"id": "CP1-T01", "status": "passed"},
                                                {"id": "CP1-T03", "status": "failed"}]}, findings=findings)

    def fn(st):
        st["checkpoints"]["CP1"]["gates"]["behavior"]["status"] = "pending"
    mutate(d, fn)
    return d


def cp1_t02_unproven_line_says_why():
    """[CP1-T02] An unproven line says why: no test linked, a failed test, or no result."""
    d = _mixed_run("t02")
    r = _status(d)
    out = r.stdout
    check("[CP1-T02] status exits 0 with no traceback", _clean(r), r.stdout + r.stderr)
    segs = _segs(out)
    check("[CP1-T02] line 1 is proven", _verdict(segs[0]) == "proven", segs[0])
    check("[CP1-T02] line 2 is unproven", _verdict(segs[1]) == "unproven", segs[1])
    check("[CP1-T02] line 2's reason reads 'no test linked'", "no test linked" in segs[1].lower(), segs[1])
    check("[CP1-T02] line 3 is unproven", _verdict(segs[2]) == "unproven", segs[2])
    check("[CP1-T02] line 3's reason names CP1-T03 and that it failed", _near(segs[2], "CP1-T03", "failed"),
          segs[2])
    check("[CP1-T02] line 3 never says 'no test linked'", "no test linked" not in segs[2].lower(), segs[2])
    check("[CP1-T02] line 4 is unproven", _verdict(segs[3]) == "unproven", segs[3])
    check("[CP1-T02] line 4's reason names CP1-T04 and that it has no result",
          _near(segs[3], "CP1-T04", "no result"), segs[3])
    check("[CP1-T02] line 4 is not described as failed or unlinked",
          "failed" not in segs[3].lower() and "no test linked" not in segs[3].lower(), segs[3])
    tbl = _table(d)
    check("[CP1-T02] status --json carries a 4-entry table", isinstance(tbl, list) and len(tbl) == 4, tbl)
    if isinstance(tbl, list) and len(tbl) == 4:
        reasons = [_rtext(e) for e in tbl]
        check("[CP1-T02] the reasons of lines 2, 3 and 4 are three different texts",
              len({reasons[1], reasons[2], reasons[3]}) == 3 and all(reasons[1:]), reasons)
        check("[CP1-T02] line 1 has no reasons", not tbl[0].get("reasons"), tbl[0])


def _finding_run(name, findings, reports=None, steps=None):
    steps = steps or [[_case("CP1-T01", [1]), _case("CP1-T02", [2]), _case("CP1-T03", [3]),
                       _case("CP1-T04", [4])]]
    if reports is None:
        reports = {"CP1": _passed("CP1-T01", "CP1-T02", "CP1-T03", "CP1-T04")}
    return _finished(name, steps, reports=reports, findings=findings)


def cp1_t03_minor_finding_naming_a_line_makes_it_unproven():
    """[CP1-T03] A minor finding that names a line makes it unproven even when every test passes."""
    d = _finding_run("t03", {"CP1": [_finding("S1", LOCKOUT_DESC, 3)]})
    dj = json.loads(g.rx(d, "deferred", "--json").stdout or "[]")
    check("[CP1-T03] fixture: S1 is left for later in a real round, not blocking",
          [f["id"] for f in dj if not f.get("accepted") and not f.get("fixed")] == ["S1"], dj)
    r = _status(d)
    out = r.stdout
    check("[CP1-T03] status exits 0 with no traceback", _clean(r), r.stdout + r.stderr)
    segs = _segs(out)
    check("[CP1-T03] line 3 is unproven although its case passed", _verdict(segs[2]) == "unproven", segs[2])
    check("[CP1-T03] line 3's reason names the finding S1", _has_id(segs[2], "S1"), segs[2])
    check("[CP1-T03] line 3's reason carries the finding's description", LOCKOUT_DESC in segs[2], segs[2])
    for i in (0, 1, 3):
        check(f"[CP1-T03] line {i + 1} stays proven", _verdict(segs[i]) == "proven", segs[i])
        check(f"[CP1-T03] line {i + 1} mentions no finding",
              not _has_id(segs[i], "S1") and LOCKOUT_DESC not in segs[i], segs[i])


def cp1_t04_every_reason_at_once_and_fixed_or_accepted_stops_counting():
    """[CP1-T04] A line shows every reason at once; a fixed or accepted finding stops counting."""
    d = _finding_run("t04", {"CP1": [_finding("S1", LOCKOUT_DESC, 3)]},
                     reports={"CP1": [{"id": "CP1-T01", "status": "passed"}, {"id": "CP1-T02", "status": "passed"},
                                      {"id": "CP1-T03", "status": "failed"}, {"id": "CP1-T04", "status": "passed"}]})

    def left_ids():
        rows = json.loads(g.rx(d, "deferred", "--json").stdout or "[]")
        return {f["id"] for f in rows if not f.get("accepted") and not f.get("fixed")}

    def line3():
        tbl = _table(d)
        return tbl[2] if isinstance(tbl, list) and len(tbl) == 4 else {}

    def reason_ids(e):
        return {i for i in ("S1",) if _has_id(_rtext(e), i)}

    seg_open = _segs(_status(d).stdout)[2]
    e = line3()
    check("[CP1-T04] open: deferred --json lists S1 as left for later", left_ids() == {"S1"}, left_ids())
    check("[CP1-T04] open: line 3 text shows the failed test", _near(seg_open, "CP1-T03", "failed"), seg_open)
    check("[CP1-T04] open: line 3 text shows the open finding", _has_id(seg_open, "S1"), seg_open)
    check("[CP1-T04] open: line 3's reasons list the failed test", "CP1-T03" in _rtext(e) and "failed" in _rtext(e),
          e)
    check("[CP1-T04] open: line 3's reasons list the open finding too", _has_id(_rtext(e), "S1"), e)
    check("[CP1-T04] open: the table's open findings equal the left-for-later set",
          reason_ids(e) == left_ids(), (reason_ids(e), left_ids()))

    def fix(st):
        for f in st["checkpoints"]["CP1"]["deferred"]:
            if f.get("id") == "S1":
                f["fixed"] = {"round": 2, "how": "showed the lockout message"}
    mutate(d, fix)
    seg = _segs(_status(d).stdout)[2]
    e = line3()
    check("[CP1-T04] fixed: S1 is no longer left for later", left_ids() == set(), left_ids())
    check("[CP1-T04] fixed: line 3 text keeps the failed test", _near(seg, "CP1-T03", "failed"), seg)
    check("[CP1-T04] fixed: line 3 text drops the finding", not _has_id(seg, "S1") and LOCKOUT_DESC not in seg, seg)
    check("[CP1-T04] fixed: line 3's reasons keep only the failed test",
          "CP1-T03" in _rtext(e) and not _has_id(_rtext(e), "S1"), e)
    check("[CP1-T04] fixed: the table and deferred --json agree", reason_ids(e) == left_ids(),
          (reason_ids(e), left_ids()))

    def accept(st):
        for f in st["checkpoints"]["CP1"]["deferred"]:
            if f.get("id") == "S1":
                f.pop("fixed", None)
                f.update(accepted=True, reason="the message ships next release", accepted_by="user",
                         accepted_at="2026-01-01T00:00:00+00:00")
    mutate(d, accept)
    seg = _segs(_status(d).stdout)[2]
    e = line3()
    check("[CP1-T04] accepted: S1 is no longer left for later", left_ids() == set(), left_ids())
    check("[CP1-T04] accepted: line 3 text keeps the failed test", _near(seg, "CP1-T03", "failed"), seg)
    check("[CP1-T04] accepted: line 3 text drops the finding reason", LOCKOUT_DESC not in seg, seg)
    check("[CP1-T04] accepted: line 3's reasons keep only the failed test",
          "CP1-T03" in _rtext(e) and not _has_id(_rtext(e), "S1"), e)
    check("[CP1-T04] accepted: the table and deferred --json agree", reason_ids(e) == left_ids(),
          (reason_ids(e), left_ids()))


def cp1_t05_finding_shapes_and_bad_line_numbers():
    """[CP1-T05] A finding naming a line in either shape counts; a bad line number is ignored plainly."""
    fl = [_finding("S1", "first finding text", 2), _finding("S2", "second finding text", [1, 4]),
          _finding("S3", "third finding text", 9), _finding("S4", "fourth finding text", "two"),
          _finding("S5", "fifth finding text")]
    d = _finding_run("t05", {"CP1": fl})
    r = _status(d)
    check("[CP1-T05] status exits 0 with no traceback", _clean(r), r.stdout + r.stderr)
    segs = _segs(r.stdout)
    want = {0: "unproven", 1: "unproven", 2: "proven", 3: "unproven"}
    for i, v in want.items():
        check(f"[CP1-T05] line {i + 1} is {v}", _verdict(segs[i]) == v, segs[i])
    check("[CP1-T05] line 1 names S2", _has_id(segs[0], "S2"), segs[0])
    check("[CP1-T05] line 2 names S1", _has_id(segs[1], "S1"), segs[1])
    check("[CP1-T05] line 4 names S2", _has_id(segs[3], "S2"), segs[3])
    check("[CP1-T05] line 3 names no finding", not any(_has_id(segs[2], s) for s in ("S1", "S2", "S3", "S4", "S5")),
          segs[2])
    rj, j = _sjson(d)
    check("[CP1-T05] status --json exits 0 with no traceback", _clean(rj), rj.stdout + rj.stderr)
    tbl = j.get("done_means_table")
    check("[CP1-T05] status --json carries a 4-entry table", isinstance(tbl, list) and len(tbl) == 4, tbl)
    if isinstance(tbl, list) and len(tbl) == 4:
        check("[CP1-T05] json verdicts are unproven, unproven, proven, unproven",
              [e.get("verdict") for e in tbl] == ["unproven", "unproven", "proven", "unproven"], tbl)
        check("[CP1-T05] json reasons name S2, S1, nothing, S2",
              _has_id(_rtext(tbl[0]), "S2") and _has_id(_rtext(tbl[1]), "S1") and not tbl[2].get("reasons")
              and _has_id(_rtext(tbl[3]), "S2"), tbl)
        every = " ".join(_rtext(e) for e in tbl)
        check("[CP1-T05] S3, S4 and S5 mark nothing", not any(_has_id(every, s) for s in ("S3", "S4", "S5")),
              every)


def cp1_t06_status_json_carries_the_same_table():
    """[CP1-T06] status --json carries the same table the text shows."""
    d = _mixed_run("t06", with_finding_on_line_1=True)
    rj, j = _sjson(d)
    check("[CP1-T06] status --json exits 0 with no traceback", _clean(rj), rj.stdout + rj.stderr)
    tbl = j.get("done_means_table")
    check("[CP1-T06] 'done_means_table' has 4 entries", isinstance(tbl, list) and len(tbl) == 4, tbl)
    check("[CP1-T06] status --json still has phase, next and objective",
          j.get("phase") == "awaiting_final_review" and "next" in j and isinstance(j.get("objective"), dict), list(j))
    if not (isinstance(tbl, list) and len(tbl) == 4):
        return
    out = _status(d).stdout
    segs = _segs(out)
    for i, e in enumerate(tbl):
        check(f"[CP1-T06] entry {i + 1} has its 1-based number", _num(e) == i + 1, e)
        check(f"[CP1-T06] entry {i + 1} has the line's text", e.get("text") == DM[i], e)
        check(f"[CP1-T06] entry {i + 1} verdict is 'proven' or 'unproven'", e.get("verdict") in ("proven", "unproven"),
              e)
        check(f"[CP1-T06] entry {i + 1} verdict equals the text's", e.get("verdict") == _verdict(segs[i]),
              (e.get("verdict"), segs[i]))
        ids = set(re.findall(r"CP\d+-T\d+", _rtext(e))) | ({"S1"} if _has_id(_rtext(e), "S1") else set())
        check(f"[CP1-T06] entry {i + 1}'s reasons name only ids the text shows",
              all(_has_id(segs[i], x) for x in ids), (ids, segs[i]))
    check("[CP1-T06] entry 1 lists CP1-T01 with its result 'passed'", _case_result(tbl[0], "CP1-T01") == "passed",
          tbl[0])
    check("[CP1-T06] entry 3 lists CP1-T03 with its result 'failed'", _case_result(tbl[2], "CP1-T03") == "failed",
          tbl[2])
    check("[CP1-T06] entry 1 reason names the open finding S1", _has_id(_rtext(tbl[0]), "S1"), tbl[0])
    check("[CP1-T06] entry 2 has no linked case and says 'no test linked'",
          not tbl[1].get("cases") and "no test linked" in _rtext(tbl[1]).lower(), tbl[1])
    check("[CP1-T06] entry 3 reason names CP1-T03 as failed",
          "CP1-T03" in _rtext(tbl[2]) and "failed" in _rtext(tbl[2]), tbl[2])
    check("[CP1-T06] entry 4 reason names CP1-T04 and no result",
          "CP1-T04" in _rtext(tbl[3]) and "no result" in _rtext(tbl[3]).lower(), tbl[3])


def cp1_t07_receipt_gives_the_same_verdict_as_status():
    """[CP1-T07] The proof receipt gives the same verdict as status for every line."""
    d = _mixed_run("t07", with_finding_on_line_1=True)
    tbl = _table(d)
    rj = g.rx(d, "receipt", "--json")
    check("[CP1-T07] receipt --json exits 0 with no traceback", _clean(rj), rj.stdout + rj.stderr)
    try:
        lines = json.loads(rj.stdout)["done_means"]["lines"]
    except Exception:
        lines = []
    check("[CP1-T07] receipt --json has 4 done_means lines", len(lines) == 4, rj.stdout[:300])
    check("[CP1-T07] status --json has the table to compare against", isinstance(tbl, list) and len(tbl) == 4, tbl)
    if len(lines) == 4 and isinstance(tbl, list) and len(tbl) == 4:
        for i in range(4):
            check(f"[CP1-T07] line {i + 1} verdict equals status's",
                  lines[i].get("verdict") in ("proven", "unproven") and lines[i].get("verdict") == tbl[i].get("verdict"),
                  (lines[i].get("verdict"), tbl[i].get("verdict")))
            check(f"[CP1-T07] line {i + 1} reasons equal status's", lines[i].get("reasons") == tbl[i].get("reasons"),
                  (lines[i].get("reasons"), tbl[i].get("reasons")))
        check("[CP1-T07] line 1's reasons include the open finding", _has_id(_rtext(lines[0]), "S1"), lines[0])
        c3 = (lines[2].get("cases") or [{}])[0]
        check("[CP1-T07] existing per-case fields (id, title, file, result) are still there",
              c3.get("id") == "CP1-T03" and c3.get("title") == "Test CP1-T03" and c3.get("file") == "tests/cp1-t03.py"
              and ("status" in c3 or "result" in c3), c3)
    rt = g.rx(d, "receipt")
    check("[CP1-T07] plain receipt exits 0 with no traceback", _clean(rt), rt.stdout + rt.stderr)
    segs = _segs(rt.stdout)
    check("[CP1-T07] plain receipt marks line 1 unproven (the finding)", _verdict(segs[0]) == "unproven", segs[0])
    check("[CP1-T07] plain receipt names the open finding under line 1", _has_id(segs[0], "S1"), segs[0])
    check("[CP1-T07] plain receipt still lists CP1-T03 as failed", _near(segs[2], "CP1-T03", "failed"), segs[2])


def cp1_t08_no_objective_gets_a_plain_note():
    """[CP1-T08] A run with no objective gets a plain note, not a table or a crash."""
    d = Path(_tmp.mkdtemp(prefix="ratchet-dm-t08-"))
    run = rp._bare_cp1(d)
    g.rx(d, "config", "--set", "checkin_round=99")
    cases = [_case("CP1-T01")]
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": cases}))
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "checkpoints": [_cp(1)]}))  # no objective
    rp._legacy_approve(d)
    _step(d, 1, cases)
    st = json.loads(_spath(d).read_text())
    check("[CP1-T08] fixture: the legacy run reached the final review", st["phase"] == "awaiting_final_review",
          st["phase"])
    r = _status(d)
    check("[CP1-T08] status exits 0 with no traceback", _clean(r), r.stdout + r.stderr)
    check("[CP1-T08] status prints the 'no objective was stated' note", "no objective was stated" in r.stdout, r.stdout)
    check("[CP1-T08] status prints no 'unproven' word and no table",
          not re.search(r"\bunproven\b|\bproven\b", r.stdout, re.I), r.stdout)
    rj, j = _sjson(d)
    check("[CP1-T08] status --json exits 0 with no traceback", _clean(rj), rj.stdout + rj.stderr)
    check("[CP1-T08] status --json has 'done_means_table', as null or the plain note",
          "done_means_table" in j and (j["done_means_table"] is None
                                       or (isinstance(j["done_means_table"], str)
                                           and "no objective was stated" in j["done_means_table"])),
          j.get("done_means_table", "<key missing>"))
    rc = g.rx(d, "receipt")
    check("[CP1-T08] receipt exits 0 and keeps its note", _clean(rc) and "no objective was stated" in rc.stdout,
          rc.stdout + rc.stderr)
    rcj = g.rx(d, "receipt", "--json")
    check("[CP1-T08] receipt --json exits 0 with no traceback", _clean(rcj), rcj.stdout + rcj.stderr)


def cp1_t09_objective_but_no_linked_tests_gets_a_note():
    """[CP1-T09] A plan with an objective but no linked tests gets a plain note instead of a table."""
    steps = [[_case("CP1-T01"), _case("CP1-T02")]]  # no "proves" anywhere
    d = _finished("t09", steps, reports={"CP1": _passed("CP1-T01", "CP1-T02")})
    r = _status(d)
    out = r.stdout
    check("[CP1-T09] status exits 0 with no traceback", _clean(r), r.stdout + r.stderr)
    note = re.compile(r"no tests? (?:is |are |were )?linked|(?:were|are) (?:never|not) linked|never linked", re.I)
    check("[CP1-T09] status says plainly that no tests are linked to any done-means line", bool(note.search(out)), out)
    check("[CP1-T09] status says it is an older plan", "older plan" in out.lower(), out)
    for i, t in enumerate(DM):
        check(f"[CP1-T09] status still lists done-means line {i + 1}", t in out, out)
    check("[CP1-T09] status prints no 'unproven' verdicts", "unproven" not in out.lower(), out)
    rj, j = _sjson(d)
    check("[CP1-T09] status --json exits 0 with no traceback", _clean(rj), rj.stdout + rj.stderr)
    tbl = j.get("done_means_table", "<key missing>")
    check("[CP1-T09] status --json carries the note in place of a table",
          not isinstance(tbl, list) and tbl != "<key missing>" and tbl is not None
          and bool(note.search(json.dumps(tbl))), tbl)


def cp1_t10_exit_code_only_step_still_proves_its_line():
    """[CP1-T10] A step checked by exit code only still proves its linked line, and says so."""
    steps = [[_case("CP1-T01", [1])], [_case("CP2-T01", [2])]]
    d = _finished("t10", steps)  # no tests.report in the plan, so no per-case report file exists

    def fn(st):
        st["checkpoints"]["CP2"]["gates"]["behavior"]["status"] = "failed"
    mutate(d, fn)
    r = _status(d)
    check("[CP1-T10] status exits 0 with no traceback", _clean(r), r.stdout + r.stderr)
    segs = _segs(r.stdout)
    check("[CP1-T10] line 1 is proven", _verdict(segs[0]) == "proven", segs[0])
    check("[CP1-T10] line 1's test entry says passed by exit code only",
          _near(segs[0], "CP1-T01", "passed") and "exit code only" in segs[0].lower(), segs[0])
    check("[CP1-T10] line 2 is unproven", _verdict(segs[1]) == "unproven", segs[1])
    check("[CP1-T10] line 2's reason says failed by exit code only",
          bool(re.search(r"failed,? by exit code only", segs[1], re.I)), segs[1])


def cp1_t11_browser_case_proves_only_when_the_ui_gate_passed():
    """[CP1-T11] A browser case proves its line only when the UI gate passed."""
    steps = [[_case("CP1-T01", [1]), _case("CP1-T05", [2], layer="browser")]]
    da = _finished("t11a", steps, reports={"CP1": _passed("CP1-T01")})

    def ui_passed(st):
        st["checkpoints"]["CP1"]["gates"]["ui"].update(required=True, status="passed")
    mutate(da, ui_passed)
    ra = _status(da)
    check("[CP1-T11] first run: status exits 0 with no traceback", _clean(ra), ra.stdout + ra.stderr)
    seg = _segs(ra.stdout)[1]
    check("[CP1-T11] first run: line 2 is proven", _verdict(seg) == "proven", seg)
    check("[CP1-T11] first run: the entry says checked by the UI gate",
          _near(seg, "CP1-T05", "ui gate"), seg)
    db = _finished("t11b", steps, reports={"CP1": _passed("CP1-T01")})  # ui gate: not required, skipped
    rb = _status(db)
    check("[CP1-T11] second run: status exits 0 with no traceback", _clean(rb), rb.stdout + rb.stderr)
    seg = _segs(rb.stdout)[1]
    check("[CP1-T11] second run: line 2 is unproven", _verdict(seg) == "unproven", seg)
    check("[CP1-T11] second run: the reason says no UI gate checked it", "no ui gate" in seg.lower(), seg)


def cp1_t12_line_proven_across_two_steps_needs_all_its_tests():
    """[CP1-T12] A line proven across two steps needs all of its tests; a finding in another step counts."""
    steps = [[_case("CP1-T01", [1]), _case("CP1-T03", [3])],
             [_case("CP2-T01", [1]), _case("CP2-T02", [2]), _case("CP2-T03", [3])]]
    d = _finished("t12", steps, reports={
        "CP1": _passed("CP1-T01", "CP1-T03"),
        "CP2": [{"id": "CP2-T01", "status": "failed"}, {"id": "CP2-T02", "status": "passed"},
                {"id": "CP2-T03", "status": "passed"}]},
        findings={"CP1": [_finding("S1", LOCKOUT_DESC, 2)]})
    r = _status(d)
    check("[CP1-T12] status exits 0 with no traceback", _clean(r), r.stdout + r.stderr)
    segs = _segs(r.stdout)
    check("[CP1-T12] line 1 is unproven", _verdict(segs[0]) == "unproven", segs[0])
    check("[CP1-T12] line 1 lists CP1-T01 as passed", _near(segs[0], "CP1-T01", "passed"), segs[0])
    check("[CP1-T12] line 1 lists CP2-T01 as failed", _near(segs[0], "CP2-T01", "failed"), segs[0])
    check("[CP1-T12] line 2 is unproven", _verdict(segs[1]) == "unproven", segs[1])
    check("[CP1-T12] line 2 names the finding raised in the other step", _has_id(segs[1], "S1"), segs[1])
    check("[CP1-T12] line 3 is proven", _verdict(segs[2]) == "proven", segs[2])
    check("[CP1-T12] line 3 lists both CP1-T03 and CP2-T03",
          _near(segs[2], "CP1-T03", "passed") and _near(segs[2], "CP2-T03", "passed"), segs[2])
    tbl = _table(d)
    if isinstance(tbl, list) and len(tbl) == 4:
        r1 = _rtext(tbl[0])
        check("[CP1-T12] line 1's reasons name CP2-T01 only", "CP2-T01" in r1 and "CP1-T01" not in r1, r1)
    else:
        check("[CP1-T12] status --json carries the 4-entry table", False, tbl)


def cp1_t13_table_appears_only_at_the_final_review():
    """[CP1-T13] The table appears only at the final review."""
    steps = [[_case("CP1-T01", [1])], [_case("CP2-T01")]]
    d = _setup("t13", steps)
    _step(d, 1, steps[0])
    rp._write_report(d, "CP1", _passed("CP1-T01"))
    st = json.loads(_spath(d).read_text())
    check("[CP1-T13] fixture: step 1 done and the run is not at the final review", st["phase"] != "awaiting_final_review",
          st["phase"])
    r = _status(d)
    out = r.stdout
    check("[CP1-T13] mid-run: status exits 0 with no traceback", _clean(r), r.stdout + r.stderr)
    check("[CP1-T13] mid-run: no done-means line text is printed", not any(t in out for t in DM), out)
    check("[CP1-T13] mid-run: no proven or unproven word anywhere",
          not re.search(r"\bunproven\b|\bproven\b", out, re.I), out)
    _step(d, 2, steps[1])
    rp._write_report(d, "CP1", _passed("CP1-T01"))
    r = _status(d)
    out = r.stdout
    check("[CP1-T13] final review: status exits 0 with no traceback", _clean(r), r.stdout + r.stderr)
    segs = _segs(out)
    check("[CP1-T13] final review: line 1 is shown proven", _verdict(segs[0]) == "proven", segs[0])
    check("[CP1-T13] final review: the unlinked lines are shown unproven",
          all(_verdict(segs[i]) == "unproven" for i in (1, 2, 3)), segs)


def cp1_t14_docs_describe_the_table():
    """[CP1-T14] The docs describe the table in every place that described the old prompts."""
    files = {"README.md": ROOT / "README.md",
             "SKILL.md": ROOT / "skills/ratchet-orchestrator/SKILL.md",
             "schemas.md": ROOT / "skills/ratchet-orchestrator/references/schemas.md"}
    text = {k: p.read_text() for k, p in files.items()}
    pair = re.compile(r"(?<!un)\bproven\b[^\n]{0,80}\bunproven\b|\bunproven\b[^\n]{0,80}(?<!un)\bproven\b", re.I)
    for k, t in text.items():
        paras = [p for p in re.split(r"\n\s*\n", t) if re.search(r"final.review|final_review", p, re.I)]
        hit = [p for p in paras if pair.search(p) and re.search(r"\btests?\b", p, re.I)
               and re.search(r"reason|why", p, re.I)]
        check(f"[CP1-T14] {k} says the final review shows each line proven or unproven with its tests and the reason",
              bool(hit), "no paragraph about the final review has proven/unproven + tests + reason")
        check(f"[CP1-T14] {k} no longer claims the final review carries 'no met or not-met claim'",
              "no met or not-met claim" not in t, "")
    sch = text["schemas.md"]
    check("[CP1-T14] schemas.md names status --json's 'done_means_table'", "done_means_table" in sch, "")
    check("[CP1-T14] schemas.md documents the finding's optional 'done_means' field (1-based line number(s))",
          any("finding" in l.lower() and "done_means" in l and re.search(r"1-based|line number", l, re.I)
              for l in sch.splitlines()), "")
    check("[CP1-T14] schemas.md names the verdict fields ('verdict' and 'reasons')",
          '"verdict"' in sch and '"reasons"' in sch, "")
    skill = text["SKILL.md"]
    check("[CP1-T14] SKILL.md no longer says status prints \"check:\" prompts", '"check:"' not in skill, "")
    check("[CP1-T14] SKILL.md no longer tells the agent to assess each line itself",
          "Assess each one" not in skill and not re.search(r"assess each", skill, re.I), "")
    check("[CP1-T14] schemas.md no longer says lines print as 'check: <line>'", "check: <line>" not in sch, "")


# ------------------------------------------------------------------------------------------------ runner

def write_report(ids):
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = [{"id": cid, "status": "passed" if CASE_STATUS.get(cid, False) else "failed",
              "message": "" if CASE_STATUS.get(cid, False) else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid in ids]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    global FAIL
    tests = sorted((m.group(1).upper().replace("_", "-"), fn) for name, fn in globals().items()
                   if callable(fn) and (m := re.match(r"(cp1_t\d+)_", name)))
    for cid, fn in tests:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crash still counts as a failed result for this case, never a silent drop
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
    write_report([cid for cid, _ in tests])
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
