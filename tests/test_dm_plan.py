#!/usr/bin/env python3
"""
CP2 tests: plans must link every done-means line.   python3 tests/test_dm_plan.py

`ratchet.py plan-submit` refuses a plan whose objective.done_means has a line that no test-plan case's "proves"
names, as a "Plan invalid" refusal that names each unlinked line by its 1-based number and its exact text and
tells the agent to add a case proving it (the "proves" field). A plan where every line is linked submits as
before, and `plan-summary` shows under each done-means line the case ids that prove it. A plan approved before
this rule (fabricated with the engine's own pure helpers, skipping plan-submit) keeps working: plan-summary
carries a plain note for an unlinked line, status and next run, feedback and plan-extend still work. The test
planner's SKILL.md, its agent file, schemas.md, README and `brief --role test-planner` all carry the rule.

Every case drives the real CLI in a throwaway git project built with tests/_tmp.py's mkdtemp and the helpers of
tests/test_gates.py and tests/test_receipt_proof.py (_bare_cp1, _legacy_approve, _dm_segment), the same pattern
as tests/test_dm_table.py. Every assertion goes through check(), so the missing feature fails cleanly instead of
crashing.

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
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                     # noqa: E402
import test_gates as g          # noqa: E402  (approve_plan, open_round, write_review, git, say, rx, ENGINE, ENV)
import test_receipt_proof as rp  # noqa: E402  (_bare_cp1, _legacy_approve, _dm_segment, DONE_MEANS)

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP2-[TX]\d+[a-z]?)\]")


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

DM = list(rp.DONE_MEANS)  # 1 login works, 2 wrong password, 3 lockout after 5 wrong attempts
LINE1, LINE2, LINE3 = DM

# The generic role line every agent without a custom brief prints today (ratchet.py _GENERIC_ROLE_RULES).
GENERIC_RULES = ("Rules: follow your own agent instructions for this role. Serve the objective above. Make "
                 "the smallest change for the job. Don't run the full test suite unless asked.")


def _objective(dm=None):
    return {"users": "people who need to sign in to the app", "goal": "sign in safely",
            "done_means": list(dm if dm is not None else DM), "out_of_scope": ["anything not listed in this plan"]}


def _case(cid, proves=None, layer="code", title=None):
    c = {"id": cid, "title": title or f"Test {cid}", "file": f"tests/{cid.lower()}.py", "layer": layer}
    if proves is not None:
        c["proves"] = proves
    return c


def _cp(i, behavior=True):
    cp = {"id": f"CP{i}", "title": f"Step {i}", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": behavior, "ui": not behavior, "adversarial": True}, "risk": "low",
          "tests": {"files": [f"tests/cp{i}.test.js"], "command": f"node --test tests/cp{i}.test.js"}}
    if not behavior:
        cp["behavior_skip_reason"] = "docs only; checked by the UI gate"
        cp["ui_scope"] = "all"
        cp["ui_states"] = [{"name": "empty"}]
    return cp


def _project(name):
    """A throwaway git project with a run in the planning phase and no plan written yet: (project dir, run dir)."""
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-dmplan-{name}-"))
    run = rp._bare_cp1(d)
    return d, run


def _write_plan(run, steps, dm=None, objective=True, cps=None):
    """Writes checkpoints.json (one checkpoint per entry of `steps`) and tests/CPn.json for every entry whose case
    list is not None (None: that step has no test plan file at all). `cps` overrides the checkpoint dicts."""
    plan = {"feature": "x", "checkpoints": cps or [_cp(i) for i in range(1, len(steps) + 1)]}
    if objective:
        plan["objective"] = _objective(dm)
    for i, cases in enumerate(steps, 1):
        p = run / "tests" / f"CP{i}.json"
        if cases is None:
            if p.exists():
                p.unlink()
        else:
            p.write_text(json.dumps({"cases": cases}))
    (run / "checkpoints.json").write_text(json.dumps(plan))


def _submit(name, steps, dm=None, objective=True, cps=None):
    d, run = _project(name)
    _write_plan(run, steps, dm=dm, objective=objective, cps=cps)
    r = g.rx(d, "plan-submit")
    return d, run, r


def _state(d):
    return json.loads((d / ".ratchet/runs/f/state.json").read_text())


def _out(r):
    return r.stdout + r.stderr


def _clean(r):
    return r.returncode == 0 and "Traceback" not in _out(r)


def _names_line(msg, i, text=None):
    """The refusal (or note) names done-means line `i` by number ("line 3", "done_means line 3", "line 3 of 3",
    "#3", "3.") and, when `text` is given, also carries that line's exact text."""
    by_number = re.search(rf"(?:line|done.means|#)\s*{i}\b|(?<![\d.-]){i}\.\s", msg, re.I) is not None
    return by_number and (text is None or text in msg)


def _mentions_line_number(msg, i):
    return re.search(rf"\bline\s*{i}\b", msg, re.I) is not None


def _refused_unlinked(name, r, line_no, text):
    msg = _out(r)
    check(f"{name} plan-submit exits non-zero with 'Plan invalid' and no traceback",
          r.returncode != 0 and "Plan invalid" in msg and "Traceback" not in msg, (r.returncode, msg))
    check(f"{name} the refusal names done-means line {line_no} by number and by its text",
          _names_line(msg, line_no, text), msg)


def _legacy_project(name, steps, dm=None):
    """A run whose plan (line 3 unlinked, or whatever `steps` says) was approved before this rule existed:
    the post-approval state is fabricated with the engine's own pure helpers, skipping plan-submit entirely."""
    d, run = _project(name)
    _write_plan(run, steps, dm=dm)
    rp._legacy_approve(d)
    return d, run


# ------------------------------------------------------------------------------------------------ cases

def cp2_t01_unlinked_line_is_refused_naming_it():
    """[CP2-T01] A plan with a done-means line no test proves is refused, naming the line."""
    d, run, r = _submit("t01", [[_case("CP1-T01", [1]), _case("CP1-T02", [2])]])
    _refused_unlinked("[CP2-T01]", r, 3, LINE3)
    msg = _out(r)
    check("[CP2-T01] the refusal tells the agent to add a case proving it (names the \"proves\" field)",
          "proves" in msg, msg)
    check("[CP2-T01] the refusal does not name line 1 (its text or 'line 1')",
          LINE1 not in msg and not _mentions_line_number(msg, 1), msg)
    check("[CP2-T01] the refusal does not name line 2 (its text or 'line 2')",
          LINE2 not in msg and not _mentions_line_number(msg, 2), msg)


def cp2_t02_refused_plan_changes_nothing_and_cannot_be_approved():
    """[CP2-T02] A refused plan changes nothing and cannot be approved."""
    d, run, r = _submit("t02", [[_case("CP1-T01", [1]), _case("CP1-T02", [2])]])
    check("[CP2-T02] fixture: plan-submit was refused", r.returncode != 0, _out(r))
    st = _state(d)
    check("[CP2-T02] state.json phase is still 'planning'", st.get("phase") == "planning", st.get("phase"))
    check("[CP2-T02] no plan_hash was stored", not st.get("plan_hash"), st.get("plan_hash"))
    events = [e.get("event") for e in st.get("history") or [] if isinstance(e, dict)]
    check("[CP2-T02] no plan_submitted entry was logged", "plan_submitted" not in events, events)
    g.rx(d, "ask", "approve-plan")
    g.say(d, "yes, go ahead")
    ra = g.rx(d, "approve-plan")
    check("[CP2-T02] approve-plan is refused (exit non-zero, no traceback)",
          ra.returncode != 0 and "Traceback" not in _out(ra), (ra.returncode, _out(ra)))
    st = _state(d)
    check("[CP2-T02] after the approval attempt the phase is still 'planning', never building",
          st.get("phase") == "planning" and not st.get("approved"), (st.get("phase"), st.get("approved")))


def cp2_t03_every_unlinked_line_is_named():
    """[CP2-T03] Every unlinked line is named, not just the first."""
    d, run, r = _submit("t03", [[_case("CP1-T01", [2])]])
    _refused_unlinked("[CP2-T03]", r, 1, LINE1)
    msg = _out(r)
    check("[CP2-T03] the refusal also names line 3 by number and by its text", _names_line(msg, 3, LINE3), msg)
    check("[CP2-T03] the refusal does not name line 2 (its text or 'line 2')",
          LINE2 not in msg and not _mentions_line_number(msg, 2), msg)


def cp2_t04_fully_linked_plan_submits_as_before():
    """[CP2-T04] A plan whose every line is linked submits as before."""
    steps = [[_case("CP1-T01", [1]), _case("CP1-T02", [2])], [_case("CP2-T01", [3])]]
    d, run, r = _submit("t04", steps)
    check("[CP2-T04] plan-submit exits 0 with no traceback", _clean(r), _out(r))
    check("[CP2-T04] it prints 'Plan valid (2 checkpoints)'", "Plan valid (2 checkpoints)" in r.stdout, r.stdout)
    st = _state(d)
    check("[CP2-T04] state.json phase is 'awaiting_plan_approval'", st.get("phase") == "awaiting_plan_approval",
          st.get("phase"))
    check("[CP2-T04] a plan_hash was stored", bool(st.get("plan_hash")), st.get("plan_hash"))
    check("[CP2-T04] the VIEWER line still prints", "VIEWER:" in r.stdout, r.stdout)


def cp2_t05_one_case_many_lines_and_one_line_many_cases():
    """[CP2-T05] One case can link several lines, and a line can have several cases."""
    d, run, r = _submit("t05a", [[_case("CP1-T01", [1, 2, 3])]])
    check("[CP2-T05] a single case proving [1, 2, 3] is enough: plan-submit exits 0", _clean(r), _out(r))
    check("[CP2-T05] (a) the phase is 'awaiting_plan_approval'", _state(d).get("phase") == "awaiting_plan_approval",
          _state(d).get("phase"))
    steps = [[_case("CP1-T01", [1]), _case("CP1-T02", [2, 3])], [_case("CP2-T01", [1])]]
    d2, run2, r2 = _submit("t05b", steps)
    check("[CP2-T05] several cases per line are fine: plan-submit exits 0", _clean(r2), _out(r2))
    check("[CP2-T05] (b) the phase is 'awaiting_plan_approval'",
          _state(d2).get("phase") == "awaiting_plan_approval", _state(d2).get("phase"))


def cp2_t06_plan_summary_lists_case_ids_per_line():
    """[CP2-T06] plan-summary shows each done-means line with the case ids that prove it."""
    steps = [[_case("CP1-T01", [1]), _case("CP1-T02", [2])], [_case("CP2-T01", [2, 3]), _case("CP2-T02", [1])]]
    d, run, r = _submit("t06", steps)
    check("[CP2-T06] fixture: plan-submit exits 0", _clean(r), _out(r))
    g.approve_plan(d)
    r = g.rx(d, "plan-summary")
    check("[CP2-T06] plan-summary exits 0 with no traceback", _clean(r), _out(r))
    out = r.stdout
    segs = [rp._dm_segment(out, DM, i) for i in range(3)]
    # the last line's segment runs to the end of the output; cut it at the next block so its ids are its own
    cut = segs[2].find("Out of scope")
    if cut != -1:
        segs[2] = segs[2][:cut]
    check("[CP2-T06] line 1 lists CP1-T01 and CP2-T02", "CP1-T01" in segs[0] and "CP2-T02" in segs[0], segs[0])
    check("[CP2-T06] line 1 lists no other case", "CP1-T02" not in segs[0] and "CP2-T01" not in segs[0], segs[0])
    check("[CP2-T06] line 2 lists CP1-T02 and CP2-T01", "CP1-T02" in segs[1] and "CP2-T01" in segs[1], segs[1])
    check("[CP2-T06] line 2 lists no other case", "CP1-T01" not in segs[1] and "CP2-T02" not in segs[1], segs[1])
    check("[CP2-T06] line 3 lists CP2-T01", "CP2-T01" in segs[2], segs[2])
    check("[CP2-T06] line 3 lists no other case",
          all(x not in segs[2] for x in ("CP1-T01", "CP1-T02", "CP2-T02")), segs[2])
    check("[CP2-T06] no 'Heads up ... no test case proving it' appears for any line",
          not re.search(r"heads up|no test case proving", out, re.I), out)


def cp2_t07_fix_and_submit_again():
    """[CP2-T07] The agent can fix the plan and submit again."""
    d, run, r = _submit("t07", [[_case("CP1-T01", [1]), _case("CP1-T02", [2])]])
    check("[CP2-T07] fixture: the first plan-submit is refused", r.returncode != 0, _out(r))
    (run / "tests/CP1.json").write_text(json.dumps({"cases": [
        _case("CP1-T01", [1]), _case("CP1-T02", [2]), _case("CP1-T03", [3])]}))
    r2 = g.rx(d, "plan-submit")
    check("[CP2-T07] the second plan-submit exits 0 with no traceback", _clean(r2), _out(r2))
    check("[CP2-T07] the phase becomes 'awaiting_plan_approval'",
          _state(d).get("phase") == "awaiting_plan_approval", _state(d).get("phase"))


def cp2_t08_empty_or_missing_proves_is_no_link():
    """[CP2-T08] An empty or missing 'proves' does not count as a link."""
    base = [_case("CP1-T01", [1]), _case("CP1-T02", [2])]
    variants = {
        "an empty list": _case("CP1-T03", [], title="Lockout after 5 wrong attempts"),
        "no proves key": _case("CP1-T03", None, title="Lockout after 5 wrong attempts"),
        "a title that only mentions the lockout": _case("CP1-T03", None, title=LINE3),
    }
    for label, extra in variants.items():
        d, run, r = _submit("t08", [base + [extra]])
        _refused_unlinked(f"[CP2-T08] ({label})", r, 3, LINE3)


def cp2_t09_scalar_and_browser_case_count_as_links():
    """[CP2-T09] A bare number and a browser-layer case both count as links."""
    steps = [[_case("CP1-T01", 1), _case("CP1-T02", [2], layer="browser"), _case("CP1-T03", [3])]]
    d, run, r = _submit("t09", steps)
    check("[CP2-T09] plan-submit exits 0 with no traceback", _clean(r), _out(r))
    check("[CP2-T09] the phase is 'awaiting_plan_approval'",
          _state(d).get("phase") == "awaiting_plan_approval", _state(d).get("phase"))


def cp2_t10_bad_number_and_missing_link_reported_together():
    """[CP2-T10] A bad number and a missing link are both reported together."""
    d, run, r = _submit("t10", [[_case("CP1-T01", [9]), _case("CP1-T02", [1])]])
    msg = _out(r)
    check("[CP2-T10] plan-submit exits non-zero with 'Plan invalid' and no traceback",
          r.returncode != 0 and "Plan invalid" in msg and "Traceback" not in msg, (r.returncode, msg))
    check("[CP2-T10] the refusal names the out-of-range number for its case (existing proves_problems wording)",
          "CP1-T01" in msg and re.search(r"proves 9 is not a done_means line", msg) is not None, msg)
    check("[CP2-T10] the same refusal names done-means line 2 by number and text", _names_line(msg, 2, LINE2), msg)
    check("[CP2-T10] the same refusal names done-means line 3 by number and text", _names_line(msg, 3, LINE3), msg)


def cp2_t11_step_with_no_test_plan_file():
    """[CP2-T11] A line proved only by a step with no test plan file is refused without a crash."""
    cps = [_cp(1), _cp(2, behavior=False)]
    d, run, r = _submit("t11", [[_case("CP1-T01", [1]), _case("CP1-T02", [2])], None], cps=cps)
    check("[CP2-T11] fixture: CP2 has no test plan file", not (run / "tests/CP2.json").exists(), "")
    _refused_unlinked("[CP2-T11]", r, 3, LINE3)
    (run / "tests/CP1.json").write_text(json.dumps({"cases": [
        _case("CP1-T01", [1]), _case("CP1-T02", [2]), _case("CP1-T03", [3])]}))
    r2 = g.rx(d, "plan-submit")
    check("[CP2-T11] when CP1 proves all three lines the same two-step plan submits (exit 0, no traceback)",
          _clean(r2), _out(r2))


def cp2_t12_identical_lines_told_apart_by_number():
    """[CP2-T12] Two lines with identical wording are told apart by number."""
    d, run, r = _submit("t12", [[_case("CP1-T01", [1])]], dm=["Works", "Works"])
    msg = _out(r)
    check("[CP2-T12] plan-submit is refused ('Plan invalid', no traceback)",
          r.returncode != 0 and "Plan invalid" in msg and "Traceback" not in msg, (r.returncode, msg))
    check("[CP2-T12] the refusal names line 2 by number", _names_line(msg, 2, "Works"), msg)
    check("[CP2-T12] the refusal does not call line 1 unlinked", not _mentions_line_number(msg, 1), msg)
    (run / "tests/CP1.json").write_text(json.dumps({"cases": [_case("CP1-T01", [1, 2])]}))
    r2 = g.rx(d, "plan-submit")
    check("[CP2-T12] a plan proving both lines submits (exit 0)", _clean(r2), _out(r2))


def cp2_t13_no_objective_refused_for_objective_only():
    """[CP2-T13] A plan with no objective is refused for the objective only, not for links."""
    d, run, r = _submit("t13", [[_case("CP1-T01", [1])]], objective=False)
    msg = _out(r)
    check("[CP2-T13] plan-submit is refused with the existing objective message and no traceback",
          r.returncode != 0 and "Traceback" not in msg and re.search(r"objective: missing", msg) is not None,
          (r.returncode, msg))
    check("[CP2-T13] no done-means linking message (there are no lines to link)",
          not re.search(r"no (linked |)test case|not (linked|proved)|unlinked|done.means line \d", msg, re.I), msg)


def cp2_t14_legacy_plan_keeps_working_with_a_note():
    """[CP2-T14] A plan approved before this change, with an unlinked line, still works with a plain note."""
    d, run = _legacy_project("t14", [[_case("CP1-T01", [1]), _case("CP1-T02", [2])]])
    r = g.rx(d, "plan-summary")
    check("[CP2-T14] plan-summary exits 0 with no traceback", _clean(r), _out(r))
    out = r.stdout
    m = re.search(r"no (linked |)test case|not (linked|proved)|unlinked|no test (case )?prov", out, re.I)
    check("[CP2-T14] plan-summary carries a plain note that a line has no linked test case yet", m is not None, out)
    window = out[max(0, (m.start() if m else 0) - 300): (m.end() if m else 0) + 300]
    line = out[out.rfind("\n", 0, m.start()) + 1: out.find("\n", m.end())] if m else ""
    check("[CP2-T14] the note names line 3 by number and by its text", _names_line(window, 3, LINE3), window)
    check("[CP2-T14] the note is worded as a note, not a refusal (no 'refus', 'invalid' or 'block' on its line)",
          bool(line) and not re.search(r"refus|invalid|block", line, re.I), line)
    rs = g.rx(d, "status")
    check("[CP2-T14] status exits 0 with no traceback", _clean(rs), _out(rs))
    check("[CP2-T14] status does not say the run is blocked", not re.search(r"\bblocked\b", rs.stdout, re.I), rs.stdout)
    rj = g.rx(d, "status", "--json")
    try:
        phase = json.loads(rj.stdout).get("phase")
    except Exception:
        phase = None
    check("[CP2-T14] status --json says the run is building", phase == "building", (phase, rj.stdout[:300]))
    rn = g.rx(d, "next")
    check("[CP2-T14] next exits 0 with no traceback", _clean(rn), _out(rn))
    check("[CP2-T14] next does not say the run is blocked", not re.search(r"\bblocked\b", rn.stdout, re.I), rn.stdout)


def cp2_t15_legacy_run_takes_feedback_and_extends():
    """[CP2-T15] An older approved run can still take feedback and add a step."""
    d, run = _legacy_project("t15", [[_case("CP1-T01", [1])]])  # lines 2 and 3 unlinked
    g.to_adversarial(d, "CP1")
    rnd = g.open_round(d, "CP1")
    p = g.write_review(d, rnd, g.R0(rnd), [])
    adv = g.rx(d, "adv-record", "CP1", "--reviews", p)
    check("[CP2-T15] fixture: the adversarial gate passes on the legacy run", adv.returncode == 0, _out(adv))
    rc = g.rx(d, "complete", "CP1")
    check("[CP2-T15] fixture: CP1 completes on the legacy run", rc.returncode == 0, _out(rc))
    g.rx(d, "ask", "human-approve")
    g.say(d, "approved")
    hr = g.rx(d, "human-approve")
    check("[CP2-T15] fixture: the final approval is accepted", hr.returncode == 0, _out(hr))
    g.say(d, "can we also add a logout button")
    rf = g.rx(d, "feedback", "--text", "add a logout button")
    check("[CP2-T15] feedback exits 0 on the legacy run", _clean(rf), _out(rf))
    plan = json.loads((run / "checkpoints.json").read_text())
    plan["checkpoints"].append(_cp(2))
    (run / "checkpoints.json").write_text(json.dumps(plan))
    (run / "tests/CP2.json").write_text(json.dumps({"cases": [_case("CP2-T01")]}))  # proves nothing
    re_ = g.rx(d, "plan-extend")
    check("[CP2-T15] plan-extend exits 0 with no traceback (the rule is a plan-submit check only)", _clean(re_),
          _out(re_))
    st = _state(d)
    check("[CP2-T15] CP2 is started as before (current CP2, phase building)",
          st.get("current") == "CP2" and st.get("phase") == "building"
          and (st.get("checkpoints", {}).get("CP2") or {}).get("status") == "in_progress",
          (st.get("current"), st.get("phase")))


def _paras(text):
    return re.split(r"\n\s*\n", text)


def cp2_t16_docs_carry_the_rule():
    """[CP2-T16] The test planner is told every done-means line needs a linked case."""
    skill = (ROOT / "skills/ratchet-tdd-planner/SKILL.md").read_text()
    agent = (ROOT / "agents/ratchet-test-planner.md").read_text()
    schemas = (ROOT / "skills/ratchet-orchestrator/references/schemas.md").read_text()
    readme = (ROOT / "README.md").read_text()
    every_line = re.compile(r"(every|each)[^\n]{0,60}done.means[^\n]{0,40}line[^\n]{0,160}(at least one|one or more|"
                            r"a case|one case|a test case)", re.I)
    refuses = re.compile(r"plan-submit[^\n]{0,200}refus|refus[^\n]{0,200}plan-submit", re.I)
    for label, text in (("SKILL.md", skill), ("agents/ratchet-test-planner.md", agent)):
        check(f"[CP2-T16] {label} says every objective done_means line needs at least one case whose \"proves\" "
              f"names it", every_line.search(text) is not None and '"proves"' in text, "")
        check(f"[CP2-T16] {label} says plan-submit refuses a plan with an unlinked line",
              any(refuses.search(p) and re.search(r"done.means|unlinked|line", p, re.I) for p in _paras(text)), "")
    check("[CP2-T16] SKILL.md no longer says leaving \"proves\" out is the general rule",
          not re.search(r"\"proves\"[^\n]{0,80}\b(optional|may be left out|can be left out)", skill, re.I), "")
    check("[CP2-T16] schemas.md no longer says an unproven line is only a heads-up 'without refusing the plan'",
          "without refusing the plan" not in schemas, "")
    check("[CP2-T16] schemas.md documents the refusal of a plan with an unlinked done_means line",
          any('"proves"' in p and refuses.search(p) and re.search(r"no case|unlinked|not (named|linked)|no test",
                                                                     p, re.I) for p in _paras(schemas)), "")
    check("[CP2-T16] schemas.md still documents \"proves\" as 1-based next to spec_ref (CP3-T12 keeps passing)",
          re.search(r"\"proves\"[^\n]{0,200}1-based", schemas, re.I) is not None and "spec_ref" in schemas, "")
    units = []  # a table row is its own unit, so the receipt row cannot stand in for the plan-summary row
    for p in _paras(readme):
        units += p.splitlines() if p.lstrip().startswith("|") else [p]
    check("[CP2-T16] README mentions, where it describes the plan you approve or its summary (not the receipt or "
          "the final review), that every done-means line is linked to a test that proves it",
          any(re.search(r"\bplan\b", u, re.I) and not re.search(r"receipt|final review", u, re.I)
              and re.search(r"(every|each)[^\n]{0,40}done.means[^\n]{0,40}line[^\n]{0,200}(test|case)", u, re.I)
              for u in units), "")


def cp2_t17_brief_for_the_test_planner_carries_the_rule():
    """[CP2-T17] The test planner's brief carries the same rule."""
    d, run, r = _submit("t17", [[_case("CP1-T01", [1, 2, 3])]])
    check("[CP2-T17] fixture: plan-submit exits 0", _clean(r), _out(r))
    rule = re.compile(r"(every|each)[^\n]{0,60}done.means[^\n]{0,40}line[^\n]{0,160}(at least one|one or more|"
                      r"a case|one case|a test case)", re.I)
    for role in ("test-planner", "ratchet-test-planner"):
        rb = g.rx(d, "brief", "CP1", "--role", role)
        check(f"[CP2-T17] brief CP1 --role {role} exits 0", _clean(rb), _out(rb))
        out = rb.stdout
        after = out[out.find("Out of scope"):] if "Out of scope" in out else out
        check(f"[CP2-T17] --role {role}: after the objective it says every done-means line needs at least one "
              f"linked case via \"proves\"", rule.search(after) is not None and '"proves"' in after, after)
        check(f"[CP2-T17] --role {role}: it says plan-submit refuses otherwise",
              re.search(r"plan-submit[^\n]{0,200}refus|refus[^\n]{0,200}plan-submit", after, re.I) is not None, after)
        check(f"[CP2-T17] --role {role}: the generic line is no longer its brief", GENERIC_RULES not in out, out)
    for role in ("test-runner", "prototyper", "variant-writer"):
        rb = g.rx(d, "brief", "CP1", "--role", role)
        check(f"[CP2-T17] --role {role} still prints the generic line unchanged",
              rb.returncode == 0 and GENERIC_RULES in rb.stdout and "proves" not in rb.stdout, _out(rb))


def cp2_t18_shared_sample_plans_still_submit():
    """[CP2-T18] The shared sample plans the other suites build still submit."""
    dm = _tmp.OBJECTIVE_MIN.get("done_means")
    check("[CP2-T18] _tmp.OBJECTIVE_MIN has exactly one done_means line", isinstance(dm, list) and len(dm) == 1, dm)
    builds = [("project() with 1 step", lambda: g.project("dm-t18-1", cps=1)),
              ("project() with 3 steps", lambda: g.project("dm-t18-3", cps=3)),
              ("project() with a high-risk step", lambda: g.project("dm-t18-h", cps=1, extra_cp={"risk": "high"}))]
    for label, build in builds:
        try:
            d = build()
        except AssertionError as e:
            check(f"[CP2-T18] test_gates.{label} submits its plan", False, str(e)[:300])
            continue
        st = _state(d)
        check(f"[CP2-T18] test_gates.{label} returns a submitted project", st.get("phase") == "awaiting_plan_approval",
              st.get("phase"))
        for p in sorted((d / ".ratchet/runs/f/tests").glob("CP*.json")):
            cases = json.loads(p.read_text()).get("cases") or []
            check(f"[CP2-T18] test_gates.{label}: every case in {p.name} proves [1]",
                  bool(cases) and all(c.get("proves") == [1] for c in cases), cases)
    import trust_helpers as th
    try:
        d = th.project(th.make_env("claude"))
    except AssertionError as e:
        check("[CP2-T18] trust_helpers.project() submits and approves its plan", False, str(e)[:300])
        return
    st = _state(d)
    check("[CP2-T18] trust_helpers.project() returns an approved (building) project", st.get("phase") == "building",
          st.get("phase"))
    cases = json.loads((d / ".ratchet/runs/f/tests/CP1.json").read_text()).get("cases") or []
    check("[CP2-T18] trust_helpers.project(): every case proves [1]",
          bool(cases) and all(c.get("proves") == [1] for c in cases), cases)


def cp2_t19_no_suite_forgets_to_link_its_objective():
    """[CP2-T19] No suite builds a plan with an objective and forgets to link it.

    A suite may leave a plan unlinked on purpose only when the line that writes its checkpoints.json carries an
    explicit marker comment `# no proves link on purpose: <reason>` with a non-empty reason (a legacy plan that
    models a run approved before this rule existed). A suite with neither "proves" nor that marker fails."""
    files = sorted(HERE.glob("test_*.py")) + [HERE / "trust_helpers.py"]
    objective_key = re.compile(r"[\"']objective[\"']\s*:")
    marker = re.compile(r"checkpoints\.json.*#[ \t]*no proves link on purpose:[ \t]*\S")
    offenders, scanned, opted_out = [], [], []
    for f in files:
        if f.name == Path(__file__).name:
            continue
        text = f.read_text()
        writes_plan = "checkpoints.json" in text and ("OBJECTIVE_MIN" in text or objective_key.search(text))
        if not writes_plan:
            continue
        scanned.append(f.name)
        if marker.search(text):  # the marker wins over a stray quoted "proves" inside a comment or a message
            opted_out.append(f.name)
            continue
        if re.search(r"[\"']proves[\"']", text):
            continue
        offenders.append(f.name)
    check("[CP2-T19] the scan found the suites that write a plan with an objective", len(scanned) >= 10, scanned)
    check("[CP2-T19] every suite that writes a plan with an objective also writes a \"proves\" link, or opts out "
          "with an explicit reason on the line that writes the plan: "
          + (", ".join(offenders) if offenders else "none missing"), not offenders, offenders)
    check("[CP2-T19] the opt-out is used only by the known legacy-plan suite", opted_out == ["test_review_objective.py"],
          opted_out)
    # The marker rule itself must bite: a suite with neither "proves" nor the marker is an offender.
    fake = "plan = {'objective': {}}\n(run / 'checkpoints.json').write_text(json.dumps(plan))\n"
    check("[CP2-T19] a plan-writing suite with neither \"proves\" nor the marker would be flagged",
          not re.search(r"[\"']proves[\"']", fake) and not marker.search(fake), fake)
    check("[CP2-T19] a bare marker with no reason does not opt a suite out",
          not marker.search(fake.replace("(plan))\n", "(plan))  # no proves link on purpose:\n")), fake)


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
                   if callable(fn) and (m := re.match(r"(cp2_t\d+)_", name)))
    for cid, fn in tests:
        CASE_STATUS.setdefault(cid, True)
        print(f"[{cid}]")
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
