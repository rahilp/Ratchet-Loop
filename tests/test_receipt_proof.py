#!/usr/bin/env python3
"""
CP3 tests: show which tests prove each done-means item.   python3 tests/test_receipt_proof.py

Test plan cases (tests/CPn.json) can carry an optional "proves": [<1-based done_means line numbers>] field,
the spec_ref precedent's sibling. `ratchet.py receipt` lists every objective.done_means line next to the test
case(s) that prove it (id, title, file) and whether that step's own test report marks it passed or failed; a
browser-layer case is shown as "checked by the UI gate"; a line no case proves reads "no test linked" literally.
A plan with no "proves" fields at all, or no objective at all (approved before either existed), still prints a
receipt instead of crashing. `plan-submit` refuses a case whose "proves" number is not a 1-based done_means line,
naming the case. Since the done-means-proven run's CP2, `plan-submit` also refuses a NEW plan whose done_means
line no case proves, naming the line (CP3-T11 below asserts that rule; it used to assert the old heads-up-only
rule and was rewritten on purpose, not weakened). So every case here that submits through the real plan-submit
links every done_means line (`_fill` adds a filler case for any line the case under test leaves alone), and the
two cases about how the receipt renders an UNLINKED line (CP3-T03, CP3-T07) build an older plan, submitted before
that rule existed, with `_legacy_submit` (the engine's own state writes, skipping only plan-submit's checks).

Reuses tests/test_gates.py (`git`, `say`, `rx`, `approve_plan`, `ENGINE`, `ENV`) for the throwaway git projects,
the same real-CLI-subprocess pattern every suite in this repo drives the engine with (see tests/test_receipt.py,
tests/test_limits.py). The per-case test report (`evidence/CPn/test-report.json`) is written directly, in the
exact shape `_read_test_report` already reads (the checkpoint's own fixture note); CP3-T08's plan with no
`objective` key at all is fabricated with the engine's own pure helper functions (`_plan_hash`, `new_cp_state`,
`approval_snapshot`, `fingerprint`), the same technique tests/test_limits.py's CP1-T09 legacy-approval fixture
uses, since a real `plan-submit` would (rightly) refuse a plan with no objective today.

Every case that depends on the "proves" feature (not yet built) calls `receipt`/`plan-submit`/`plan-summary`
through the real CLI subprocess, so a missing feature is reported through `check()` as a clean fail, never an
uncaught crash.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP3-T01",
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
import test_gates as g          # noqa: E402  (git, say, rx, approve_plan, ENGINE, ENV)

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP3-[TX]\d+[a-z]?)\]")


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

DONE_MEANS = [
    "A user can log in with the right password",
    "A wrong password shows an error and does not log the user in",
    "After 5 wrong attempts in a row, the account is locked for 15 minutes",
]


def _objective(done_means=None):
    return {"users": "people who need to sign in to the app", "goal": "sign in safely",
            "done_means": list(done_means) if done_means is not None else list(DONE_MEANS),
            "out_of_scope": ["anything not listed in this plan"]}


def _bare_cp1(d):
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        g.git(d, *c)
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "init")
    g.say(d, "please build the feature")
    r = g.rx(d, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr
    g.rx(d, "config", "--set", "min_review_seconds=0", "providers=[]")
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    return run


def _fill(cases, done_means=None, layer="code"):
    """`cases` plus one filler case per done_means line that none of `cases` proves (ids CP1-T91, CP1-T92,
    ...: never an id a case under test uses), so a plan built for a receipt check links every line the way
    plan-submit requires since CP2 of the done-means-proven run. The filler never carries a test-report entry,
    so it changes nothing under the line the case under test is about. A case that drives the step through the
    real tests-written/red/behavior commands uses layer="browser" fillers, which those commands never look
    for in the step's test file."""
    dm = list(done_means) if done_means is not None else list(DONE_MEANS)
    proved = set()
    for c in cases:
        p = c.get("proves")
        for v in (p if isinstance(p, list) else [p]):
            if isinstance(v, int) and not isinstance(v, bool):
                proved.add(v)
    return list(cases) + [{"id": f"CP1-T9{k}", "title": f"Done-means line {k} is covered",
                           "file": "tests/test_login_more.py", "layer": layer, "proves": [k]}
                          for k in range(1, len(dm) + 1) if k not in proved]


_LEGACY_SUBMIT_HELPER = Path(_tmp.mkdtemp(prefix="ratchet-proof-legacy-submit-")) / "legacy_submit.py"
_LEGACY_SUBMIT_HELPER.write_text(
    "import sys, json\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, sys.argv[2])\n"
    "import ratchet as rt\n"
    "root = Path(sys.argv[1])\n"
    "ctx = rt.Ctx(root)\n"
    "st = ctx.state()\n"
    "plan = ctx.plan()\n"
    "st['phase'] = 'awaiting_plan_approval'\n"
    "st['awaiting_since'] = rt.now()\n"
    "st.pop('pending', None)\n"
    "st['plan_hash'] = rt._plan_hash(plan, ctx)\n"
    "st['checkpoints'] = {cp['id']: st['checkpoints'].get(cp['id']) or rt.new_cp_state(cp)"
    " for cp in plan['checkpoints']}\n"
    "ctx.log(st, 'plan_submitted', hash=st['plan_hash'], count=len(plan['checkpoints']))\n"
    "ctx.save_state(st)\n"
    "print('ok')\n"
)


def _legacy_submit(d):
    """The exact state change cmd_plan_submit makes (phase awaiting_plan_approval, plan_hash, checkpoint states,
    the plan_submitted log entry), written with the engine's own helpers but skipping plan-submit's checks: a
    plan submitted before CP2 of the done-means-proven run required every done_means line to have a linked
    case. The only way such a plan (an unlinked line, or no "proves" at all) still comes to exist."""
    r = subprocess.run([sys.executable, str(_LEGACY_SUBMIT_HELPER), str(d), str(g.ENGINE.parent)],
                       cwd=d, capture_output=True, text=True, env=g.ENV)
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stdout + r.stderr


def _proof_project(name, cases, done_means=None, submit=True, legacy=False):
    """A one-checkpoint (CP1) throwaway project whose test plan carries the "proves" field this checkpoint
    adds, and whose objective.done_means is a real, specific list (not _tmp.OBJECTIVE_MIN's placeholder) so the
    receipt's done-means section has real lines to link tests against. Always returns (d, plan-submit's own
    result); when submit=True (the default) that result is also asserted to have succeeded, so every case that
    does not itself test plan-submit's refusal can ignore the second item. With legacy=True the plan is
    submitted the pre-CP2 way (_legacy_submit, never the real plan-submit, which now refuses an unlinked
    done_means line) and the second item is None."""
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-proof-{name}-"))
    run = _bare_cp1(d)
    cp1 = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
           "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
           "tests": {"files": ["tests/cp1.test.js"], "command": "node --test tests/cp1.test.js"}}
    plan = {"feature": "x", "objective": _objective(done_means), "checkpoints": [cp1]}
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": cases}))
    (run / "checkpoints.json").write_text(json.dumps(plan))
    if legacy:
        _legacy_submit(d)
        return d, None
    r = g.rx(d, "plan-submit")
    if submit:
        assert r.returncode == 0, r.stderr + r.stdout
    return d, r


def _write_report(d, cp, entries):
    """A per-case test report at evidence/CPn/test-report.json, in the exact shape _read_test_report already
    reads, written directly (not through a real `behavior` run) so each case's own last-known pass/fail state
    is fully controlled by the fixture."""
    p = d / f".ratchet/runs/f/evidence/{cp}/test-report.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"cases": entries}))


_LEGACY_HELPER = Path(_tmp.mkdtemp(prefix="ratchet-proof-legacy-")) / "legacy_approve.py"
_LEGACY_HELPER.write_text(
    "import sys, json\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, sys.argv[2])\n"
    "import ratchet as rt\n"
    "root = Path(sys.argv[1])\n"
    "ctx = rt.Ctx(root)\n"
    "st = ctx.state()\n"
    "plan = ctx.plan()\n"
    "st['plan_hash'] = rt._plan_hash(plan, ctx)\n"
    "st['checkpoints'] = {cp['id']: rt.new_cp_state(cp) for cp in plan['checkpoints']}\n"
    "st.pop('pending', None)\n"
    "st['phase'] = 'awaiting_plan_approval'\n"
    "st['approved'] = rt.approval_snapshot(ctx, st)\n"
    "st['phase'] = 'building'\n"
    "st['current'] = plan['checkpoints'][0]['id']\n"
    "first = st['checkpoints'][st['current']]\n"
    "first['status'] = 'in_progress'\n"
    "first['started_at'] = rt.now()\n"
    "first['base_tree'] = rt.fingerprint(ctx.root)['id']\n"
    "st['awaiting_since'] = None\n"
    "ctx.save_state(st)\n"
    "print('ok')\n"
)


def _legacy_approve(d):
    """Fabricates the exact post-approval state cmd_plan_submit + cmd_approve_plan would have written, using the
    engine's own pure helper functions, but skipping plan-submit's own objective_problems() check: the one
    history a real plan-submit can never produce today (a plan with no "objective" key at all, approved before
    that field existed). Same technique tests/test_limits.py's CP1-T09 legacy-approval fixture uses."""
    r = subprocess.run([sys.executable, str(_LEGACY_HELPER), str(d), str(g.ENGINE.parent)],
                       cwd=d, capture_output=True, text=True, env=g.ENV)
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stdout + r.stderr


def _no_objective_project(name):
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-proof-{name}-"))
    run = _bare_cp1(d)
    cp1 = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
           "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
           "tests": {"files": ["tests/cp1.test.js"], "command": "node --test tests/cp1.test.js"}}
    plan = {"feature": "x", "checkpoints": [cp1]}  # deliberately: no "objective" key at all
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
    (run / "checkpoints.json").write_text(json.dumps(plan))
    _legacy_approve(d)
    return d


def _dm_segment(out: str, dm_lines, i: int) -> str:
    """The slice of `out` from done_means[i]'s own text up to whichever OTHER done_means line's text comes next
    (or the end), so per-line assertions never accidentally read a neighbouring line's own test-case entries."""
    start = out.find(dm_lines[i])
    if start == -1:
        return ""
    others = [out.find(x, start + 1) for j, x in enumerate(dm_lines) if j != i]
    others = [o for o in others if o != -1 and o > start]
    end = min(others) if others else len(out)
    return out[start:end]


def _window(text: str, needle: str, span: int = 200) -> str:
    idx = text.find(needle)
    return "" if idx == -1 else text[idx: idx + span]


# ------------------------------------------------------------------------------------------------ cases

TESTS = []


def t01_line_shows_proving_test_and_pass():
    cases = [{"id": "CP1-T01", "title": "Login succeeds with the right password",
              "file": "tests/test_login.py", "layer": "code", "proves": [1]}]
    d, _ = _proof_project("t01", _fill(cases))  # CP2: every other done_means line gets a filler link
    _write_report(d, "CP1", [{"id": "CP1-T01", "status": "passed"}])
    r = g.rx(d, "receipt")
    check("[CP3-T01] receipt exits 0", r.returncode == 0, r.stdout + r.stderr)
    out = r.stdout
    check("[CP3-T01] done-means line 1's own text appears", DONE_MEANS[0] in out, out)
    seg = _dm_segment(out, DONE_MEANS, 0)
    check("[CP3-T01] CP1-T01's id appears under line 1", "CP1-T01" in seg, seg)
    check("[CP3-T01] CP1-T01's title appears under line 1",
          "Login succeeds with the right password" in seg, seg)
    check("[CP3-T01] CP1-T01's file appears under line 1", "tests/test_login.py" in seg, seg)
    check("[CP3-T01] CP1-T01 is marked passed under line 1", "passed" in seg.lower(), seg)
    check("[CP3-T01] CP1-T01 is never marked failed or 'no test linked' under line 1",
          "failed" not in seg.lower() and "no test linked" not in seg.lower(), seg)


TESTS.append(("CP3-T01", t01_line_shows_proving_test_and_pass))


def t02_failed_test_shown_as_failed():
    cases = [{"id": "CP1-T02", "title": "Wrong password shows an error",
              "file": "tests/test_login.py", "layer": "code", "proves": [2]}]
    d, _ = _proof_project("t02", _fill(cases))  # CP2: every other done_means line gets a filler link
    _write_report(d, "CP1", [{"id": "CP1-T02", "status": "failed"}])
    r = g.rx(d, "receipt")
    check("[CP3-T02] receipt exits 0", r.returncode == 0, r.stdout + r.stderr)
    out = r.stdout
    seg = _dm_segment(out, DONE_MEANS, 1)
    check("[CP3-T02] CP1-T02's id appears under line 2 (not hidden)", "CP1-T02" in seg, seg)
    check("[CP3-T02] CP1-T02 is marked failed under line 2", "failed" in seg.lower(), seg)
    check("[CP3-T02] CP1-T02 is never shown as passed or 'no test linked' under line 2",
          "passed" not in seg.lower() and "no test linked" not in seg.lower(), seg)


TESTS.append(("CP3-T02", t02_failed_test_shown_as_failed))


def t03_unlinked_line_says_no_test_linked():
    cases = [{"id": "CP1-T01", "title": "Login succeeds with the right password",
              "file": "tests/test_login.py", "layer": "code", "proves": [1]}]
    d, _ = _proof_project("t03", cases, legacy=True)  # an older plan: a new one with an unlinked line is refused since CP2
    _write_report(d, "CP1", [{"id": "CP1-T01", "status": "passed"}])
    r = g.rx(d, "receipt")
    check("[CP3-T03] receipt exits 0 (an unlinked done-means line never crashes it)", r.returncode == 0,
          r.stdout + r.stderr)
    out = r.stdout
    check("[CP3-T03] done-means line 3's own text appears", DONE_MEANS[2] in out, out)
    seg = _dm_segment(out, DONE_MEANS, 2)
    check("[CP3-T03] line 3 reads 'no test linked', literally", "no test linked" in seg.lower(), seg)
    check("[CP3-T03] line 3 never names a made-up test id (CP1-T02/CP1-T03 do not exist in any test plan)",
          "CP1-T02" not in seg and "CP1-T03" not in seg, seg)


TESTS.append(("CP3-T03", t03_unlinked_line_says_no_test_linked))


def t04_two_cases_proving_same_line_both_listed():
    cases = [
        {"id": "CP1-T01", "title": "Login succeeds with the right password",
         "file": "tests/test_login.py", "layer": "code", "proves": [1]},
        {"id": "CP1-T04", "title": "Login also succeeds from the mobile client",
         "file": "tests/test_login_mobile.py", "layer": "code", "proves": [1]},
    ]
    d, _ = _proof_project("t04", _fill(cases))  # CP2: every other done_means line gets a filler link
    _write_report(d, "CP1", [{"id": "CP1-T01", "status": "passed"}, {"id": "CP1-T04", "status": "failed"}])
    r = g.rx(d, "receipt")
    check("[CP3-T04] receipt exits 0", r.returncode == 0, r.stdout + r.stderr)
    out = r.stdout
    seg = _dm_segment(out, DONE_MEANS, 0)
    check("[CP3-T04] CP1-T01 is listed under line 1", "CP1-T01" in seg, seg)
    check("[CP3-T04] CP1-T04 is ALSO listed under the same line 1", "CP1-T04" in seg, seg)
    check("[CP3-T04] CP1-T01's own file appears", "tests/test_login.py" in seg, seg)
    check("[CP3-T04] CP1-T04's own file appears", "tests/test_login_mobile.py" in seg, seg)
    check("[CP3-T04] CP1-T01's own pass state (passed) is shown",
          bool(re.search(r"CP1-T01[^\n]*passed|passed[^\n]*CP1-T01", seg, re.I)), seg)
    check("[CP3-T04] CP1-T04's own pass state (failed) is shown",
          bool(re.search(r"CP1-T04[^\n]*failed|failed[^\n]*CP1-T04", seg, re.I)), seg)


TESTS.append(("CP3-T04", t04_two_cases_proving_same_line_both_listed))


def t05_one_case_proving_two_lines_appears_under_both():
    cases = [{"id": "CP1-T05", "title": "Login works and lockout eventually applies",
              "file": "tests/test_login.py", "layer": "code", "proves": [1, 3]}]
    d, _ = _proof_project("t05", _fill(cases))  # CP2: every other done_means line gets a filler link
    _write_report(d, "CP1", [{"id": "CP1-T05", "status": "passed"}])
    r = g.rx(d, "receipt")
    check("[CP3-T05] receipt exits 0", r.returncode == 0, r.stdout + r.stderr)
    out = r.stdout
    seg1 = _dm_segment(out, DONE_MEANS, 0)
    seg3 = _dm_segment(out, DONE_MEANS, 2)
    check("[CP3-T05] CP1-T05 appears under done-means line 1", "CP1-T05" in seg1, seg1)
    check("[CP3-T05] CP1-T05 ALSO appears under done-means line 3", "CP1-T05" in seg3, seg3)


TESTS.append(("CP3-T05", t05_one_case_proving_two_lines_appears_under_both))


def t06_browser_case_checked_by_ui_gate():
    cases = [{"id": "CP1-T06", "title": "Wrong password error shows on screen",
              "file": "tests/test_login_ui.spec.js", "layer": "browser", "proves": [2]}]
    d, _ = _proof_project("t06", _fill(cases))  # CP2: every other done_means line gets a filler link
    # no per-case test report entry for CP1-T06: browser cases are never run by `ratchet.py behavior`
    r = g.rx(d, "receipt")
    check("[CP3-T06] receipt exits 0", r.returncode == 0, r.stdout + r.stderr)
    out = r.stdout
    seg = _dm_segment(out, DONE_MEANS, 1)
    check("[CP3-T06] CP1-T06 appears under done-means line 2", "CP1-T06" in seg, seg)
    win = _window(seg, "CP1-T06", 200)
    check("[CP3-T06] CP1-T06 is marked checked by the UI gate", bool(re.search(r"ui gate", win, re.I)), win)
    check("[CP3-T06] CP1-T06 is never shown as passed, failed or 'no test linked'",
          not re.search(r"\bpassed\b|\bfailed\b", win, re.I) and "no test linked" not in win.lower(), win)


TESTS.append(("CP3-T06", t06_browser_case_checked_by_ui_gate))


def t07_no_proves_fields_at_all_still_prints_receipt():
    cases = [{"id": "CP1-T01", "title": "Login succeeds with the right password",
              "file": "tests/test_login.py", "layer": "code"}]  # no "proves" key anywhere: an old plan
    d, _ = _proof_project("t07", cases, legacy=True)  # an older plan: a new one with an unlinked line is refused since CP2
    _write_report(d, "CP1", [{"id": "CP1-T01", "status": "passed"}])
    r = g.rx(d, "receipt")
    check("[CP3-T07] receipt exits 0 on a plan with no 'proves' fields at all", r.returncode == 0,
          r.stdout + r.stderr)
    out = r.stdout
    for i in range(3):
        seg = _dm_segment(out, DONE_MEANS, i)
        check(f"[CP3-T07] done-means line {i + 1} reads 'no test linked' (no case claims it)",
              "no test linked" in seg.lower(), seg)
    check("[CP3-T07] a plain note (beyond the 3 per-line notices) explains these tests were never linked",
          out.lower().count("linked") >= 4, out)
    check("[CP3-T07] nothing crashes (no traceback)", "Traceback" not in (r.stdout + r.stderr),
          r.stdout + r.stderr)


TESTS.append(("CP3-T07", t07_no_proves_fields_at_all_still_prints_receipt))


def t08_no_objective_still_prints_rest_of_receipt():
    d = _no_objective_project("t08")
    r = g.rx(d, "receipt")
    check("[CP3-T08] receipt exits 0 even though the plan has no objective at all", r.returncode == 0,
          r.stdout + r.stderr)
    check("[CP3-T08] nothing crashes (no traceback)", "Traceback" not in (r.stdout + r.stderr),
          r.stdout + r.stderr)
    out = r.stdout
    check("[CP3-T08] the rest of the receipt still prints (the step's own title)", "Step 1" in out, out)
    check("[CP3-T08] the run's whole-time line still prints", "Whole run time" in out, out)
    check("[CP3-T08] a plain note says this run has no objective to show done-means proof for",
          bool(re.search(r"no objective", out, re.I)), out)


TESTS.append(("CP3-T08", t08_no_objective_still_prints_rest_of_receipt))


def t09_plan_submit_refuses_out_of_range_proves():
    cases = [{"id": "CP1-T07", "title": "Some test", "file": "tests/test_login.py", "layer": "code",
              "proves": [9]}]
    d, r = _proof_project("t09", cases, submit=False)
    check("[CP3-T09] plan-submit exits non-zero for a proves number past the end of done_means",
          r.returncode != 0, r.stdout + r.stderr)
    msg = r.stdout + r.stderr
    check("[CP3-T09] the refusal names the offending case (CP1-T07)", "CP1-T07" in msg, msg)
    check("[CP3-T09] the refusal says 9 is not a done_means line", "9" in msg and "done_means" in msg.lower(),
          msg)


TESTS.append(("CP3-T09", t09_plan_submit_refuses_out_of_range_proves))


def t10_plan_submit_refuses_zero_proves():
    cases = [{"id": "CP1-T08", "title": "Some other test", "file": "tests/test_login.py", "layer": "code",
              "proves": [0]}]
    d, r = _proof_project("t10", cases, submit=False)
    check("[CP3-T10] plan-submit exits non-zero for proves: [0] (not a valid 1-based line)",
          r.returncode != 0, r.stdout + r.stderr)
    msg = r.stdout + r.stderr
    check("[CP3-T10] the refusal names the offending case (CP1-T08)", "CP1-T08" in msg, msg)
    check("[CP3-T10] the refusal rejects 0 as not a done_means line", "0" in msg and "done_means" in msg.lower(),
          msg)


TESTS.append(("CP3-T10", t10_plan_submit_refuses_zero_proves))


def t11_plan_submit_refuses_unproven_line_naming_it():
    """[CP3-T11] Rewritten for the done-means-proven run's CP2 (a deliberate rule change the user approved, not
    a weakening): a NEW plan whose done_means line no case proves is refused at plan-submit, naming that line
    by number and text, instead of being accepted with a plan-summary heads-up. Linking the line makes the
    same plan submit, and plan-summary then carries no heads-up at all."""
    cases = [{"id": "CP1-T01", "title": "Login succeeds with the right password",
              "file": "tests/test_login.py", "layer": "code", "proves": [1]},
             {"id": "CP1-T02", "title": "Wrong password shows an error",
              "file": "tests/test_login.py", "layer": "code", "proves": [2]}]  # nothing proves line 3
    d, r_submit = _proof_project("t11", cases, submit=False)
    msg = r_submit.stdout + r_submit.stderr
    check("[CP3-T11] plan-submit exits non-zero for a plan whose done-means line 3 no case proves",
          r_submit.returncode != 0, msg)
    check("[CP3-T11] the refusal is a 'Plan invalid' refusal", "Plan invalid" in msg, msg)
    check("[CP3-T11] the refusal names line 3 by number", bool(re.search(r"line 3\b", msg)), msg)
    check("[CP3-T11] the refusal names line 3 by its own text (the lockout line)", DONE_MEANS[2] in msg, msg)
    check("[CP3-T11] the refusal does not name lines 1 or 2 (they are proved)",
          DONE_MEANS[0] not in msg and DONE_MEANS[1] not in msg, msg)
    check("[CP3-T11] the refusal tells the agent to add a case whose \"proves\" names the line", "proves" in msg, msg)
    st = json.loads((d / ".ratchet/runs/f/state.json").read_text())
    check("[CP3-T11] the refused plan never reaches awaiting_plan_approval",
          st.get("phase") == "planning" and not st.get("plan_hash"), (st.get("phase"), st.get("plan_hash")))
    r_approve = g.rx(d, "ask", "approve-plan")
    check("[CP3-T11] the refused plan cannot be put up for approval", r_approve.returncode != 0,
          r_approve.stdout + r_approve.stderr)
    cases[1]["proves"] = [2, 3]  # the agent links the lockout line and submits again
    (d / ".ratchet/runs/f/tests/CP1.json").write_text(json.dumps({"cases": cases}))
    r2 = g.rx(d, "plan-submit")
    check("[CP3-T11] with line 3 linked the same plan submits (exit 0)", r2.returncode == 0, r2.stdout + r2.stderr)
    st2 = json.loads((d / ".ratchet/runs/f/state.json").read_text())
    check("[CP3-T11] the phase is then awaiting_plan_approval", st2.get("phase") == "awaiting_plan_approval",
          st2.get("phase"))
    r = g.rx(d, "plan-summary")
    check("[CP3-T11] plan-summary exits 0 on the linked plan", r.returncode == 0, r.stdout + r.stderr)
    out = r.stdout
    check("[CP3-T11] plan-summary carries no heads-up about an unproven done-means line",
          not re.search(r"heads up|no (proving )?test case|not proved by (any )?test|unproven|no test (case )?proves",
                        out, re.I), out)


TESTS.append(("CP3-T11", t11_plan_submit_refuses_unproven_line_naming_it))


def t12_planner_skill_and_schema_document_proves():
    skill = (ROOT / "skills" / "ratchet-tdd-planner" / "SKILL.md").read_text()
    schemas = (ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md").read_text()
    check("[CP3-T12] the test planner's SKILL.md tells it to fill a case's quoted \"proves\" field",
          '"proves"' in skill, "")
    check("[CP3-T12] SKILL.md ties the quoted \"proves\" field to the objective's done_means lines",
          bool(re.search(r"\"proves\"[^\n]{0,200}done_means|done_means[^\n]{0,200}\"proves\"", skill, re.I)), "")
    check("[CP3-T12] schemas.md section 4 documents the quoted \"proves\" field (not just the English word)",
          '"proves"' in schemas, "")
    check("[CP3-T12] schemas.md documents it as optional, a list of 1-based line numbers, next to spec_ref",
          bool(re.search(r"\"proves\"[^\n]{0,200}(optional|1-based)", schemas, re.I))
          and "spec_ref" in schemas, "")


TESTS.append(("CP3-T12", t12_planner_skill_and_schema_document_proves))


# ------------------------------------------------------------------------------------------ adversarial fixes
# adv-round-01 fix-list.json: codex-standards/X1 + codex-correctness/X2 (a scalar "proves" must not crash
# plan-summary or receipt) and codex-standards/X2 + codex-correctness/X1 (an exit-code-only step's behavior
# gate result must still show for its linked done-means case).

def x01_scalar_proves_normalized_everywhere():
    """[CP3-X01] A case's "proves" written as a bare scalar int (proves: 1) instead of a list ([1]) is accepted
    by plan-submit and normalized exactly like [1] everywhere else: plan-summary and receipt both still exit 0
    and list the case under its matching done-means line, instead of crashing with a TypeError."""
    cases = [{"id": "CP1-T01", "title": "Login succeeds with the right password",
              "file": "tests/test_login.py", "layer": "code", "proves": 1}]  # scalar, not [1]
    d, r_submit = _proof_project("x01", _fill(cases))  # CP2: every other done_means line gets a filler link
    check("[CP3-X01] plan-submit accepts a scalar proves value", r_submit.returncode == 0,
          r_submit.stdout + r_submit.stderr)
    r_approve = g.approve_plan(d)
    check("[CP3-X01] approve-plan exits 0", r_approve.returncode == 0, r_approve.stdout + r_approve.stderr)
    r_sum = g.rx(d, "plan-summary")
    check("[CP3-X01] plan-summary exits 0 (never crashes on a scalar proves)", r_sum.returncode == 0,
          r_sum.stdout + r_sum.stderr)
    check("[CP3-X01] plan-summary shows no traceback", "Traceback" not in (r_sum.stdout + r_sum.stderr),
          r_sum.stdout + r_sum.stderr)
    _write_report(d, "CP1", [{"id": "CP1-T01", "status": "passed"}])
    r = g.rx(d, "receipt")
    check("[CP3-X01] receipt exits 0 (never crashes on a scalar proves)", r.returncode == 0,
          r.stdout + r.stderr)
    out = r.stdout
    seg = _dm_segment(out, DONE_MEANS, 0)
    check("[CP3-X01] CP1-T01 is listed under done-means line 1 even though proves was a bare scalar",
          "CP1-T01" in seg, seg)


TESTS.append(("CP3-X01", x01_scalar_proves_normalized_everywhere))


def x02_exit_code_only_step_shows_passed_by_exit_code():
    """[CP3-X02] A step with no per-case test report (tests.report missing: exit-code only) whose behavior gate
    actually passed: the done-means line linked to its case shows the step's tests passed, by exit code,
    instead of the misleading "no result recorded yet"."""
    cases = [{"id": "CP1-T01", "title": "Login succeeds with the right password",
              "file": "tests/test_login.py", "layer": "code", "proves": [1]}]
    d, _ = _proof_project("x02", _fill(cases, layer="browser"))  # CP2: browser fillers link lines 2 and 3
    g.approve_plan(d)
    g.to_adversarial(d, "CP1")  # real tests-written/red/implemented/behavior; this plan writes no per-case report
    r = g.rx(d, "receipt")
    check("[CP3-X02] receipt exits 0", r.returncode == 0, r.stdout + r.stderr)
    out = r.stdout
    seg = _dm_segment(out, DONE_MEANS, 0)
    check("[CP3-X02] CP1-T01 appears under done-means line 1", "CP1-T01" in seg, seg)
    win = _window(seg, "CP1-T01", 200)
    check("[CP3-X02] CP1-T01 is shown as passed, by exit code",
          bool(re.search(r"passed", win, re.I)) and bool(re.search(r"exit code", win, re.I)), win)
    check("[CP3-X02] CP1-T01 is never shown as 'no result recorded yet'",
          "no result recorded yet" not in win.lower(), win)


TESTS.append(("CP3-X02", x02_exit_code_only_step_shows_passed_by_exit_code))


def x03_one_failed_entry_among_several_shows_failed():
    """[CP3-X03] A per-case test report with two entries sharing the same planned case id (a parameterized
    test's separate instances), the first failed and the second passed: the done-means line linked to that
    case shows failed, not passed, because not every matching entry passed."""
    cases = [{"id": "CP1-T03", "title": "Login handles both password cases",
              "file": "tests/test_login.py", "layer": "code", "proves": [3]}]
    d, _ = _proof_project("x03", _fill(cases))  # CP2: every other done_means line gets a filler link
    _write_report(d, "CP1", [{"id": "CP1-T03", "status": "failed"}, {"id": "CP1-T03", "status": "passed"}])
    r = g.rx(d, "receipt")
    check("[CP3-X03] receipt exits 0", r.returncode == 0, r.stdout + r.stderr)
    out = r.stdout
    seg = _dm_segment(out, DONE_MEANS, 2)
    check("[CP3-X03] CP1-T03's id appears under line 3", "CP1-T03" in seg, seg)
    check("[CP3-X03] CP1-T03 is marked failed under line 3", "failed" in seg.lower(), seg)
    check("[CP3-X03] CP1-T03 is never shown as passed under line 3",
          "passed" not in seg.lower(), seg)


TESTS.append(("CP3-X03", x03_one_failed_entry_among_several_shows_failed))


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
