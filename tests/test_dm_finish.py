#!/usr/bin/env python3
"""
CP4 tests: unproven lines block the finish.   python3 tests/test_dm_finish.py

At the final review the run cannot be approved as finished while a done-means line is unproven (a linked test
failed or has no result) or has an open finding naming it: `ask human-approve` refuses without printing a
question, `human-approve` refuses from a chat reply and from a director token alike, naming the blocking line by
number and full text with its reason; `status`, `next` and `status --json` carry the same block and name the ways
forward (feedback; mark-fixed for a finding) without ever telling the agent to ask for approval; a run set to need
no final review stops at the final review instead of finishing; review-each-step mode blocks only the approval
that would finish the run. The lines checked are the APPROVED ones: editing checkpoints.json, feedback /
plan-extend, approve-changes and `merged` cannot remove or reword an approved line (feedback may append one). A
refused approval leaves every review counter untouched, and the review-limit unblock path is not affected.
Runs approved before this change finish as before, with a plain note. The docs say all of this.

Every case drives the real CLI in a throwaway git project built with tests/_tmp.py's mkdtemp and the helpers of
tests/test_gates.py (approve_plan, open_round, write_review, rx, say, git), tests/test_receipt_proof.py
(_write_report, _no_objective_project), tests/test_dm_table.py (mutate, _segs, _verdict), tests/test_dm_plan.py
(_names_line, _mentions_line_number), tests/test_limits.py (_drive_to_cap) and tests/trust_helpers.py (make_env,
rx: the director lane, with its own director key in a temp HOME). Per-case test results are written straight into
evidence/CPn/test-report.json (the shape _read_test_report reads) before the step completes, so the result is in
place at the moment the finish is decided. Every assertion goes through check(), so the missing feature fails
cleanly instead of crashing.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP4-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                      # noqa: E402
import test_gates as g           # noqa: E402  (approve_plan, open_round, write_review, R0, rx, say, git, HOME)
import test_receipt_proof as rp  # noqa: E402  (_write_report, _no_objective_project)
import test_dm_table as dt       # noqa: E402  (mutate, _segs, _verdict, _table)
import test_dm_plan as dp        # noqa: E402  (_names_line, _mentions_line_number)
import test_limits as tl         # noqa: E402  (_drive_to_cap: the review-limit block)
import trust_helpers as th       # noqa: E402  (make_env, rx, git, now_dt: the director lane)

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP4-[TX]\d+[a-z]?)\]")


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

LINE1 = "A saved note can be read back"
LINE2 = "A note longer than 200 characters is refused with a message"
LINE3 = "A note can be deleted"
REWORDED = "A long note is refused"
DM2 = [LINE1, LINE2]
DM3 = [LINE1, LINE2, LINE3]
FIND_DESC = "the length message is never shown to the user"
YES = "yes, go ahead"
FINISH_QUESTION = "mark the finished work as approved and close this run"
LOW_REASON = "low-risk step: only blocker and major findings block"
# the keys the engine's approval_snapshot had before this run added its marker: a legacy approval keeps only these
OLD_SNAPSHOT_KEYS = ("plan", "objective", "cps", "tests", "rules", "files", "custom_providers", "spec", "host",
                     "human_review")

# wording the block must carry (status, next, refusals) and the wording that must be absent
BLOCKED_RE = re.compile(r"cannot be (?:marked |approved as )?(?:done|finished)|can't be (?:marked |approved as )?"
                        r"(?:done|finished)|cannot (?:be )?finish|can't (?:be )?finish|not be marked done", re.I)
NOT_FINISHED_RE = re.compile(BLOCKED_RE.pattern + r"|not finished|is not done", re.I)
ASK_INSTRUCTION_RE = re.compile(r"ask human-approve|run `?ratchet\.py human-approve", re.I)
NOTHING_WAITING_RE = re.compile(r"nothing (?:is )?waiting|not waiting (?:on|for)|no (?:question|reply) (?:is )?"
                                r"(?:waiting|pending)|waits? on nothing|no question is open", re.I)
LEGACY_NOTE_RE = re.compile(r"approved before", re.I)
LEGACY_NO_STOP_RE = re.compile(r"(?:do|does|did) not (?:stop|block|hold)|without (?:stopping|blocking)|"
                               r"(?:never|not) (?:stopped|blocked|held)|are shown but", re.I)


def _out(r):
    return (r.stdout or "") + (r.stderr or "")


def _clean(r):
    return r.returncode == 0 and "Traceback" not in _out(r)


def _state(d, slug="f"):
    return json.loads((Path(d) / f".ratchet/runs/{slug}/state.json").read_text())


def _events(st):
    return [e.get("event") for e in (st.get("history") or []) if isinstance(e, dict)]


def _human_gates(st):
    return {c: ((s.get("gates") or {}).get("human") or {}).get("status") for c, s in (st.get("checkpoints") or {}).items()}


def _counters(st):
    """Every review counter a refused approval must leave alone (learnings 2026-10-10)."""
    cps = st.get("checkpoints") or {}
    return {"adv_attempts": {c: ((s.get("gates") or {}).get("adversarial") or {}).get("attempts") for c, s in cps.items()},
            "adv_rounds": {c: len(((s.get("gates") or {}).get("adversarial") or {}).get("rounds") or []) for c, s in cps.items()},
            "beh_attempts": {c: ((s.get("gates") or {}).get("behavior") or {}).get("attempts") for c, s in cps.items()},
            "cap_raised_to": {c: ((s.get("gates") or {}).get("adversarial") or {}).get("cap_raised_to") for c, s in cps.items()},
            "adv_rounds_total": st.get("adv_rounds_total"), "regression_flaky_used": st.get("regression_flaky_used")}


def _objective(dm):
    return {"users": "people who keep short notes in the app", "goal": "save and read notes safely",
            "done_means": list(dm), "out_of_scope": ["anything not listed in this plan"]}


def _case(cid, proves, file=None):
    return {"id": cid, "title": f"Test {cid}", "file": file or f"tests/{cid.lower()}.py", "layer": "code",
            "proves": list(proves)}


def _cp(i):
    return {"id": f"CP{i}", "title": f"Step {i}", "summary": "s", "done_when": ["works"],
            "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
            "tests": {"files": [f"tests/cp{i}.test.js"], "command": f"node --test tests/cp{i}.test.js"}}


FIN2_STEPS = [[_case("CP1-T01", [1]), _case("CP1-T02", [2])]]
FIN3_STEPS = [[_case("CP1-T01", [1])], [_case("CP2-T01", [2]), _case("CP2-T02", [3])]]
PASSED2 = [{"id": "CP1-T01", "status": "passed"}, {"id": "CP1-T02", "status": "passed"}]
FAILED2 = [{"id": "CP1-T01", "status": "passed"}, {"id": "CP1-T02", "status": "failed"}]


def _init_project(name, human_review="end", branch=None, extra_files=None, config=None):
    """A throwaway git project with a run initialised (not planned yet): (project dir, run dir). `human_review`
    is given to `init` (end | each | none), the way a real run sets it."""
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-dmfin-{name}-"))
    init = ["init", "-q"] + (["-b", branch] if branch else [])
    for c in (init, ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        g.git(d, *c)
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    for path, content in (extra_files or {}).items():
        p = d / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "init")
    g.say(d, "please build the feature")
    args = ["init", "feature", "--slug", "f"] + (["--human-review", human_review] if human_review != "end" else [])
    r = g.rx(d, *args)
    assert r.returncode == 0, _out(r)
    r = g.rx(d, "config", "--set", "min_review_seconds=0", "providers=[]", "checkin_round=99", *(config or []))
    assert r.returncode == 0, _out(r)
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    return d, run


def _write_plan(run, steps, dm, cps=None):
    plan = {"feature": "notes", "objective": _objective(dm),
            "checkpoints": cps or [_cp(i) for i in range(1, len(steps) + 1)]}
    for i, cases in enumerate(steps, 1):
        (run / "tests" / f"CP{i}.json").write_text(json.dumps({"cases": cases}))
    (run / "checkpoints.json").write_text(json.dumps(plan))


def _approved(name, steps=FIN2_STEPS, dm=DM2, human_review="end", branch=None, extra_files=None, config=None,
              cps=None):
    """A project whose plan (every done-means line linked) is submitted through the real plan-submit and approved."""
    d, run = _init_project(name, human_review=human_review, branch=branch, extra_files=extra_files, config=config)
    _write_plan(run, steps, dm, cps=cps)
    r = g.rx(d, "plan-submit")
    assert r.returncode == 0, _out(r)
    g.approve_plan(d)
    return d


def _write_step_tests(d, i, cases):
    body = "".join(f"t('[{c['id']}] works', () => a.equal(f(), {i}));\n" for c in cases)
    (d / f"tests/cp{i}.test.js").write_text(
        "import t from 'node:test'; import a from 'node:assert'; import { f } from '../src/f.js';\n" + body)


def _build(d, i, cases, report=None, findings=None, complete=True):
    """Drives CP<i> through tests-written, red, implemented, behavior and one adversarial round (`findings` are
    recorded as written: a minor on a low-risk step is deferred, never blocking). `report` (a list of {id, status})
    is written as the step's per-case test report BEFORE complete runs, so it is in place at the moment the
    finish is decided. Returns complete's result, or None when complete=False."""
    cp = f"CP{i}"
    _write_step_tests(d, i, cases)
    r = g.rx(d, "tests-written", cp)
    assert r.returncode == 0, _out(r)
    r = g.rx(d, "red", cp)
    assert r.returncode == 0, _out(r)
    (d / "src/f.js").write_text(f"export const f = () => {i};\n")
    r = g.rx(d, "implemented", cp)
    assert r.returncode == 0, _out(r)
    r = g.rx(d, "behavior", cp)
    assert r.returncode == 0, _out(r)
    rnd = g.open_round(d, cp)
    p = g.write_review(d, rnd, g.R0(rnd), list(findings or []))
    r = g.rx(d, "adv-record", cp, "--reviews", p)
    assert r.returncode == 0, _out(r)
    if report is not None:
        rp._write_report(d, cp, report)
    if not complete:
        return None
    return g.rx(d, "complete", cp)


def _fin2(name, failed=True, human_review="end", **kw):
    """Run FIN2 driven to its last complete: CP1-T01 passed and CP1-T02 failed (line 2 unproven) or passed.
    Returns (project dir, complete's result)."""
    d = _approved(name, human_review=human_review, **kw)
    r = _build(d, 1, FIN2_STEPS[0], report=FAILED2 if failed else PASSED2)
    return d, r


def _add_open_finding(d, cp, fid, dm, desc=FIND_DESC, file="docs/NOTES.md", fixed=None):
    """A deferred finding (the shape adv-record records for a minor on a low-risk step) added onto the step's
    own deferred list, with the optional done_means field; `fixed` makes it a fixed-later entry."""
    def fn(st):
        entry = {"id": fid, "severity": "minor", "kind": "docs-out-of-sync", "file": file, "description": desc,
                 "round": 1, "deferred_because": LOW_REASON}
        if dm is not None:
            entry["done_means"] = dm
        if fixed:
            entry["fixed"] = fixed
        st["checkpoints"][cp].setdefault("deferred", []).append(entry)
    dt.mutate(d, fn)


def _make_legacy(d):
    """Turns the approval into the shape a run approved before this change has: the snapshot keeps ONLY the keys
    approval_snapshot had before, so whatever marker the engine now adds is dropped without naming it."""
    def fn(st):
        st["approved"] = {k: v for k, v in (st.get("approved") or {}).items() if k in OLD_SNAPSHOT_KEYS}
    dt.mutate(d, fn)


def _ask_yes_approve(d):
    """What the agent does to finish: ask human-approve, the user's yes, human-approve. Returns (ask, approve)."""
    ask = g.rx(d, "ask", "human-approve")
    g.say(d, YES)
    return ask, g.rx(d, "human-approve")


def _feedback(d, text="please also let me delete a note"):
    g.say(d, text)
    return g.rx(d, "feedback", "--text", text)


def _extend_plan(run, dm, cp2_cases):
    """The planner's extend: objective.done_means set to `dm`, step CP2 appended, its test plan written."""
    plan = json.loads((run / "checkpoints.json").read_text())
    plan["objective"]["done_means"] = list(dm)
    plan["checkpoints"] = [c for c in plan["checkpoints"] if c["id"] != "CP2"] + [_cp(2)]
    (run / "checkpoints.json").write_text(json.dumps(plan))
    (run / "tests/CP2.json").write_text(json.dumps({"cases": cp2_cases}))


def _edit_done_means(run, dm):
    plan = json.loads((run / "checkpoints.json").read_text())
    plan["objective"]["done_means"] = list(dm)
    (run / "checkpoints.json").write_text(json.dumps(plan))


def _refused(tag, r, what):
    out = _out(r)
    check(f"{tag} {what} exits non-zero with no traceback", r.returncode != 0 and "Traceback" not in out,
          (r.returncode, out[-700:]))
    return out


def _names(tag, msg, n, text, where):
    check(f"{tag} {where} names line {n} by number and its full text", dp._names_line(msg, n, text), msg[-900:])


def _not_named(tag, msg, n, text, where):
    check(f"{tag} {where} does not name line {n}", text not in msg and not dp._mentions_line_number(msg, n), msg[-900:])


def _failed_reason(tag, msg, cid, where):
    check(f"{tag} {where} gives the reason: the linked test {cid} failed",
          re.search(rf"{re.escape(cid)}[^\n]{{0,80}}fail|fail[^\n]{{0,80}}{re.escape(cid)}", msg, re.I) is not None,
          msg[-900:])


def _still_waiting(tag, d, phase="awaiting_final_review", gates=True):
    st = _state(d)
    check(f"{tag} phase is still {phase}", st.get("phase") == phase, st.get("phase"))
    check(f"{tag} no 'approved' event is in history", "approved" not in _events(st), _events(st))
    if gates:
        check(f"{tag} no checkpoint's human gate is passed",
              all(v != "passed" for v in _human_gates(st).values()), _human_gates(st))


def _finished(tag, d, by="human"):
    st = _state(d)
    check(f"{tag} phase is done", st.get("phase") == "done", st.get("phase"))
    ev = [e for e in (st.get("history") or []) if isinstance(e, dict) and e.get("event") == "approved"]
    check(f"{tag} an 'approved' event by {by} is in history", any(e.get("by") == by for e in ev), ev)


def _blocked_wording(tag, out, where, line_no=2, text=LINE2, reason_id="CP1-T02"):
    check(f"{tag} {where} says the run cannot be marked done yet", BLOCKED_RE.search(out) is not None, out[-1200:])
    _names(tag, out, line_no, text, where)
    if reason_id:
        _failed_reason(tag, out, reason_id, where)
    check(f"{tag} {where} does not tell the agent to ask for approval or to run human-approve",
          ASK_INSTRUCTION_RE.search(out) is None, out[-1200:])
    check(f"{tag} {where} names feedback as a way forward", "feedback" in out, out[-1200:])


def _line1_not_blocking(tag, out, where):
    rows = [ln for ln in out.splitlines() if LINE1 in ln]
    check(f"{tag} {where} lists line 1 only as proven, never as blocking",
          bool(rows) and all(re.search(r"(?<!un)proven", ln, re.I) and "unproven" not in ln.lower()
                             and "block" not in ln.lower() for ln in rows), rows)


# ---- the director lane ----------------------------------------------------------------------------

def _dsay(d, env, text):
    tp = d / "t.jsonl"
    with tp.open("a") as f:
        f.write(json.dumps({"type": "user", "message": {"role": "user", "content": text},
                            "timestamp": th.now_dt().isoformat()}) + "\n")
    return th.rx(d, env, "hook", "prompt", "--host", "claude",
                 stdin=json.dumps({"cwd": str(d), "prompt": text, "session_id": "s-owner", "transcript_path": str(tp)}))


def _director_fin2(name, failed=True):
    """Run FIN2 in a lane with its own director key (temp HOME, never the real one), at the final review with
    CP1-T02 failed (or passed), enrolled by the director. Returns (d, builder env, director env)."""
    env = th.make_env("codex")
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-dmfin-dir-{name}-"))
    th.git(d, "init", "-q")
    th.git(d, "config", "user.email", "t@t")
    th.git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    th.git(d, "add", "-A")
    th.git(d, "commit", "-qm", "init")
    (d / ".ratchet").mkdir(exist_ok=True)
    (d / "t.jsonl").write_text("")
    _dsay(d, env, "please build the feature")
    r = th.rx(d, env, "init", "feature", "--slug", "f")
    assert r.returncode == 0, _out(r)
    th.rx(d, env, "config", "--set", "min_review_seconds=0", "providers=[]", "checkin_round=99")
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cp = dict(_cp(1), tests={"files": ["tests/cp1.test.js"], "command": "test -f src/f.js"})  # no node on the hermetic PATH
    _write_plan(run, FIN2_STEPS, DM2, cps=[cp])
    r = th.rx(d, env, "plan-submit")
    assert r.returncode == 0, _out(r)
    th.rx(d, env, "ask", "approve-plan")
    _dsay(d, env, YES)
    r = th.rx(d, env, "approve-plan")
    assert r.returncode == 0, _out(r)
    _write_step_tests(d, 1, FIN2_STEPS[0])
    assert th.rx(d, env, "tests-written", "CP1").returncode == 0
    assert th.rx(d, env, "red", "CP1").returncode == 0
    (d / "src/f.js").write_text("export const f = () => 1;\n")
    assert th.rx(d, env, "implemented", "CP1").returncode == 0
    r = th.rx(d, env, "behavior", "CP1")
    assert r.returncode == 0, _out(r)
    rnd = th.cp3_open_round(d, env)
    rid = th.cp3_R0(rnd)
    path = Path(rnd["output_dir"]) / f"{rid}.json"
    th.cp3_write_review(path, rnd, rid, findings=[])
    r = th.rx(d, env, "adv-record", "CP1", "--reviews", str(path))
    assert r.returncode == 0, _out(r)
    rp._write_report(d, "CP1", FAILED2 if failed else PASSED2)
    r = th.rx(d, env, "complete", "CP1")
    assert r.returncode == 0, _out(r)
    assert th.cp3_state(d)["phase"] == "awaiting_final_review", th.cp3_state(d)["phase"]
    denv = dict(env, RATCHET_ROLE="director")
    rk = th.rx(d, denv, "director-key")
    en = th.rx(d, denv, "director-enroll")
    assert rk.returncode == 0 and en.returncode == 0, _out(rk) + _out(en)
    return d, env, denv


def _director_token(d, denv):
    sg = th.rx(d, denv, "director-sign", "human-approve")
    return sg, (sg.stdout.strip().splitlines()[-1] if sg.stdout.strip() else "")


# ---- the docs-only last step (lane B's quick path) --------------------------------------------------

NOTES_PHRASE = "LONG NOTES ARE REFUSED"
DOCS_TEST = ("import t from 'node:test'; import a from 'node:assert'; import fs from 'node:fs';\n"
             "t('[CP2-T01] the notes doc says long notes are refused', () => "
             f"a.ok(fs.readFileSync('docs/NOTES.md', 'utf8').includes('{NOTES_PHRASE}')));\n")


def _docs_run(name, human_review="end", cp2_failed=True):
    """Run FIN2-docs: CP1 is the code step (both lines linked, both passed); CP2, the last step, changes only
    docs/NOTES.md and its review is skipped by the docs-only path; CP2-T01 (proves line 2, a docs test naming
    docs/NOTES.md, committed before the run) is recorded failed or passed. Returns (d, complete CP2's result)."""
    doc_cmd = f"doc_test_command={shlex.quote(sys.executable)} -c pass {{files}}"
    steps = [FIN2_STEPS[0], [_case("CP2-T01", [2], file="tests/cp2.test.js")]]
    d = _approved(name, steps=steps, human_review=human_review, config=[doc_cmd],
                  extra_files={"docs/NOTES.md": "# notes\n", "tests/cp2.test.js": DOCS_TEST})
    r = _build(d, 1, steps[0], report=PASSED2)
    assert r.returncode == 0, _out(r)
    assert _state(d)["phase"] == "building" and _state(d)["current"] == "CP2", _state(d)["phase"]
    r = g.rx(d, "tests-written", "CP2")
    assert r.returncode == 0, _out(r)
    r = g.rx(d, "red", "CP2")
    assert r.returncode == 0, _out(r)
    (d / "docs/NOTES.md").write_text(f"# notes\n\n{NOTES_PHRASE}: a note over 200 characters gets a message.\n")
    assert g.rx(d, "implemented", "CP2").returncode == 0
    r = g.rx(d, "behavior", "CP2")
    assert r.returncode == 0, _out(r)
    rp._write_report(d, "CP2", [{"id": "CP2-T01", "status": "failed" if cp2_failed else "passed"}])
    rv = g.rx(d, "reviewers", "CP2")
    gate = _state(d)["checkpoints"]["CP2"]["gates"]["adversarial"]
    assert rv.returncode == 0 and gate.get("status") == "passed" and "skipped" in _out(rv), _out(rv) + repr(gate)
    return d, g.rx(d, "complete", "CP2")


# ---- a merge of main into the lane ---------------------------------------------------------------

def _merge_main_in(d):
    """main forks from the lane's last step commit with a docs file and one config-rule change, and is merged
    into the lane with --no-ff (the shape `merged` accepts)."""
    g.git(d, "checkout", "-q", "-b", "main")
    (d / "docs").mkdir(exist_ok=True)
    (d / "docs/FROM_MAIN.md").write_text("# from main\n")
    cfgp = d / ".ratchet/config.json"
    c = json.loads(cfgp.read_text())
    c["max_adversarial_rounds"] = 5
    cfgp.write_text(json.dumps(c, indent=2) + "\n")
    g.git(d, "add", "-A")
    r = g.git(d, "commit", "-qm", "M1 from main")
    assert r.returncode == 0, r.stdout + r.stderr
    r = g.git(d, "checkout", "-q", "lane")
    assert r.returncode == 0, r.stderr
    r = g.git(d, "merge", "--no-ff", "-q", "-m", "Merge main into lane", "main")
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(g.git(d, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()) == 3


# ------------------------------------------------------------------------------------------------ cases

def cp4_t01_failed_line_cannot_be_approved_as_done():
    """[CP4-T01] A finished run with a failed done-means line cannot be approved as done."""
    d, r = _fin2("t01")
    check("[CP4-T01] fixture: the run is at the final review", _state(d)["phase"] == "awaiting_final_review", _out(r))
    ask, ha = _ask_yes_approve(d)
    aout = _refused("[CP4-T01]", ask, "ask human-approve")
    check("[CP4-T01] ask human-approve prints no ASK THE USER question", "ASK THE USER" not in aout, aout)
    check("[CP4-T01] ask human-approve leaves no pending question",
          (_state(d).get("pending") or {}).get("action") != "human-approve", _state(d).get("pending"))
    hout = _refused("[CP4-T01]", ha, "human-approve")
    for where, msg in (("the ask refusal", aout), ("the human-approve refusal", hout)):
        _names("[CP4-T01]", msg, 2, LINE2, where)
        _failed_reason("[CP4-T01]", msg, "CP1-T02", where)
        _not_named("[CP4-T01]", msg, 1, LINE1, where)
    _still_waiting("[CP4-T01]", d)


def cp4_t02_yes_given_before_the_line_went_unproven_does_not_finish():
    """[CP4-T02] A yes given before the line went unproven does not finish the run."""
    d, _ = _fin2("t02", failed=False)
    ask = g.rx(d, "ask", "human-approve")
    check("[CP4-T02] fixture: with everything proven the question is asked", _clean(ask) and "ASK THE USER" in ask.stdout,
          _out(ask))
    g.say(d, YES)
    _add_open_finding(d, "CP1", "S2", [2])   # after the yes, before human-approve
    ha = g.rx(d, "human-approve")
    hout = _refused("[CP4-T02]", ha, "human-approve")
    _names("[CP4-T02]", hout, 2, LINE2, "the refusal")
    check("[CP4-T02] the refusal names the open finding (step and finding id)",
          re.search(r"CP1\S*S2|S2[^\n]{0,40}CP1", hout) is not None and FIND_DESC in hout, hout[-900:])
    _still_waiting("[CP4-T02]", d)


def cp4_t03_director_cannot_sign_a_finish_past_an_unproven_line():
    """[CP4-T03] The director cannot sign a finish past an unproven line either."""
    d, env, denv = _director_fin2("t03")
    sg, tok = _director_token(d, denv)
    check("[CP4-T03] setup: director-sign human-approve prints a token", sg.returncode == 0 and bool(tok), _out(sg))
    r = th.rx(d, env, "human-approve", "--director-token", tok)
    out = _refused("[CP4-T03]", r, "human-approve --director-token")
    _names("[CP4-T03]", out, 2, LINE2, "the refusal")
    _failed_reason("[CP4-T03]", out, "CP1-T02", "the refusal")
    check("[CP4-T03] the refusal does not tell the agent to ask the user to reply yes",
          not re.search(r"reply (?:with )?(?:yes|one of)|ask human-approve|listed yes", out, re.I), out[-900:])
    st = th.cp3_state(d)
    check("[CP4-T03] phase stays awaiting_final_review", st.get("phase") == "awaiting_final_review", st.get("phase"))
    check("[CP4-T03] no 'approved' event", "approved" not in _events(st), _events(st))
    check("[CP4-T03] gates.human is not passed", all(v != "passed" for v in _human_gates(st).values()), _human_gates(st))


def cp4_t04_director_approval_still_finishes_a_proven_run():
    """[CP4-T04] The director's approval still finishes a run whose every line is proven."""
    d, env, denv = _director_fin2("t04", failed=False)
    _dsay(d, env, YES)
    chat = th.rx(d, env, "human-approve")
    cout = _refused("[CP4-T04]", chat, "a chat yes without a token")
    check("[CP4-T04] the chat refusal is the director-enrolled message",
          re.search(r"director[- ]enrolled|director token|director-sign", cout, re.I) is not None, cout[-600:])
    check("[CP4-T04] the chat refusal is not a done-means block", BLOCKED_RE.search(cout) is None and LINE2 not in cout,
          cout[-600:])
    sg, tok = _director_token(d, denv)
    check("[CP4-T04] setup: director-sign human-approve prints a token", sg.returncode == 0 and bool(tok), _out(sg))
    r = th.rx(d, env, "human-approve", "--director-token", tok)
    check("[CP4-T04] human-approve --director-token succeeds", _clean(r), _out(r))
    st = th.cp3_state(d)
    check("[CP4-T04] phase is done", st.get("phase") == "done", st.get("phase"))
    ev = [e for e in (st.get("history") or []) if isinstance(e, dict) and e.get("event") == "approved"]
    check("[CP4-T04] an 'approved' event by director is in history", any(e.get("by") == "director" for e in ev), ev)


def cp4_t05_status_and_next_name_the_lines_and_the_ways_forward():
    """[CP4-T05] While the finish is blocked, status and next name the lines and the ways forward, and never
    tell the agent to ask for approval."""
    d, _ = _fin2("t05")
    for cmd in ("status", "next"):
        r = g.rx(d, cmd)
        out = _out(r)
        check(f"[CP4-T05] (failed test) {cmd} exits 0 with no traceback", _clean(r), out[-600:])
        _blocked_wording("[CP4-T05] (failed test)", out, cmd)
    sout = _out(g.rx(d, "status"))
    check("[CP4-T05] (failed test) status prints no waiting-for-the-user's-answer line",
          "Waiting for the user's answer" not in sout and "Waiting for the director's answer" not in sout, sout[-900:])
    check("[CP4-T05] (failed test) status says plainly that nothing is waiting on a reply",
          NOTHING_WAITING_RE.search(sout) is not None, sout[-900:])
    _line1_not_blocking("[CP4-T05] (failed test)", sout, "status")
    d2, _ = _fin2("t05f", failed=False)
    _add_open_finding(d2, "CP1", "S2", [2])
    for cmd in ("status", "next"):
        r = g.rx(d2, cmd)
        out = _out(r)
        check(f"[CP4-T05] (open finding) {cmd} exits 0 with no traceback", _clean(r), out[-600:])
        _blocked_wording("[CP4-T05] (open finding)", out, cmd, reason_id=None)
        check(f"[CP4-T05] (open finding) {cmd} names the finding S2 as the reason",
              re.search(r"(?<![A-Za-z0-9])S2(?![0-9])", out) is not None and FIND_DESC in out, out[-1200:])
        check(f"[CP4-T05] (open finding) {cmd} names mark-fixed as the way to close it", "mark-fixed" in out, out[-1200:])


def cp4_t06_status_json_carries_the_block_for_director_and_agent():
    """[CP4-T06] status --json carries the block so the director and the agent read one source."""
    d, _ = _fin2("t06")
    rj = g.rx(d, "status", "--json")
    j = json.loads(rj.stdout) if _clean(rj) else {}
    check("[CP4-T06] (user lane) status --json exits 0 and parses", _clean(rj) and isinstance(j, dict), _out(rj)[-400:])
    check("[CP4-T06] (user lane) the old keys are still present",
          all(k in j for k in ("phase", "next", "stop_ok", "waiting_on", "objective", "approval_mode")), list(j))
    nxt = str(j.get("next", ""))
    check("[CP4-T06] (user lane) next in the JSON says the run cannot be marked done and names line 2",
          BLOCKED_RE.search(nxt) is not None and dp._names_line(nxt, 2, LINE2), nxt)
    check("[CP4-T06] (user lane) next in the JSON gives the reason (CP1-T02 failed)",
          re.search(r"CP1-T02[^\n]{0,80}fail", nxt, re.I) is not None, nxt)
    check("[CP4-T06] (user lane) next carries no ask-for-approval instruction", ASK_INSTRUCTION_RE.search(nxt) is None, nxt)
    q = j.get("question")
    check("[CP4-T06] (user lane) no waiting human-approve question is reported",
          q is None or (isinstance(q, dict) and q.get("kind") != "human-approve"), q)
    rn = g.rx(d, "next")
    check("[CP4-T06] (user lane) `next` says the same, with no ask instruction",
          _clean(rn) and BLOCKED_RE.search(_out(rn)) is not None and ASK_INSTRUCTION_RE.search(_out(rn)) is None,
          _out(rn)[-900:])
    dd, env, denv = _director_fin2("t06")
    rj2 = th.rx(dd, env, "status", "--json")
    j2 = json.loads(rj2.stdout) if _clean(rj2) else {}
    check("[CP4-T06] (director lane) status --json exits 0 and parses", _clean(rj2) and isinstance(j2, dict), _out(rj2)[-400:])
    check("[CP4-T06] (director lane) the old keys are still present, approval_mode director",
          all(k in j2 for k in ("phase", "next", "stop_ok", "waiting_on", "objective", "approval_mode"))
          and j2.get("approval_mode") == "director", (list(j2), j2.get("approval_mode")))
    n2 = str(j2.get("next", ""))
    check("[CP4-T06] (director lane) next names line 2 with its reason and says the run cannot be marked done",
          BLOCKED_RE.search(n2) is not None and dp._names_line(n2, 2, LINE2)
          and re.search(r"CP1-T02[^\n]{0,80}fail", n2, re.I) is not None, n2)
    check("[CP4-T06] (director lane) next carries no 'director-sign human-approve' and no ask instruction",
          "director-sign human-approve" not in n2 and ASK_INSTRUCTION_RE.search(n2) is None, n2)
    q2 = j2.get("question")
    check("[CP4-T06] (director lane) no waiting human-approve question is reported",
          q2 is None or (isinstance(q2, dict) and q2.get("kind") != "human-approve"), q2)
    rn2 = th.rx(dd, env, "next")
    check("[CP4-T06] (director lane) `next` carries no 'director-sign human-approve' instruction",
          _clean(rn2) and "director-sign human-approve" not in _out(rn2) and BLOCKED_RE.search(_out(rn2)) is not None,
          _out(rn2)[-900:])


def cp4_t07_proven_run_approval_works_as_before():
    """[CP4-T07] When every line is proven, the approval question and the approval work as before."""
    d, _ = _fin2("t07", failed=False)
    nout = _out(g.rx(d, "next"))
    check("[CP4-T07] before approval, next still tells the agent to run ask human-approve", "ask human-approve" in nout,
          nout[-900:])
    sout = _out(g.rx(d, "status"))
    check("[CP4-T07] before approval, status still asks for the final review (approve or ask for changes)",
          re.search(r"final review", sout, re.I) is not None and "approve" in sout, sout[-900:])
    for cmd, out in (("next", nout), ("status", sout)):
        check(f"[CP4-T07] before approval, {cmd} carries no blocked wording", BLOCKED_RE.search(out) is None, out[-900:])
    ask, ha = _ask_yes_approve(d)
    check("[CP4-T07] ask prints the existing question", _clean(ask) and FINISH_QUESTION in ask.stdout, _out(ask))
    check("[CP4-T07] human-approve exits 0", _clean(ha), _out(ha))
    _finished("[CP4-T07]", d)


def cp4_t08_no_final_review_mode_stops_at_the_final_review_when_a_line_is_unproven():
    """[CP4-T08] A run set to need no final review stops at the final review when a line is unproven."""
    d, r = _fin2("t08", human_review="none")
    out = _out(r)
    check("[CP4-T08] complete CP1 exits 0 for the step itself", _clean(r), out[-900:])
    st = _state(d)
    check("[CP4-T08] the step itself is done", st["checkpoints"]["CP1"].get("status") == "done", st["checkpoints"]["CP1"].get("status"))
    check("[CP4-T08] phase is awaiting_final_review, not done", st.get("phase") == "awaiting_final_review", st.get("phase"))
    check("[CP4-T08] complete says the run is not finished", NOT_FINISHED_RE.search(out) is not None, out[-900:])
    _names("[CP4-T08]", out, 2, LINE2, "complete's output")
    _failed_reason("[CP4-T08]", out, "CP1-T02", "complete's output")
    active = json.loads((g.HOME / "active-runs.json").read_text()) if (g.HOME / "active-runs.json").exists() else {}
    check("[CP4-T08] the run is still the project's active run (not unregistered)",
          str(Path(d).resolve()) in active, list(active))
    check("[CP4-T08] no CI-export hint and no spec-finish note is printed",
          "ci-export" not in out and "Spec:" not in out, out[-900:])
    sout = _out(g.rx(d, "status"))
    _blocked_wording("[CP4-T08]", sout, "status")


def cp4_t09_no_final_review_mode_still_finishes_when_every_line_is_proven():
    """[CP4-T09] A run set to need no final review still finishes straight away when every line is proven."""
    d, r = _fin2("t09", failed=False, human_review="none")
    out = _out(r)
    check("[CP4-T09] complete CP1 exits 0", _clean(r), out[-900:])
    check("[CP4-T09] phase is done, as before", _state(d).get("phase") == "done", _state(d).get("phase"))
    check("[CP4-T09] no blocked wording is printed", NOT_FINISHED_RE.search(out) is None and LINE2 not in out, out[-900:])


def cp4_t10_each_mode_blocks_only_the_last_steps_approval():
    """[CP4-T10] In review-each-step mode the last step's approval is blocked, earlier steps are not."""
    d = _approved("t10", steps=FIN3_STEPS, dm=DM3, human_review="each")
    r = _build(d, 1, FIN3_STEPS[0], report=[{"id": "CP1-T01", "status": "passed"}])
    st = _state(d)
    check("[CP4-T10] fixture: CP1 waits for its review", _clean(r) and st.get("phase") == "awaiting_checkpoint_review"
          and st.get("review_target") == "CP1", (st.get("phase"), st.get("review_target")))
    ask, ha = _ask_yes_approve(d)
    check("[CP4-T10] CP1's approval question is asked (lines 2 and 3 have no result yet, which is no blocker here)",
          _clean(ask) and "ASK THE USER" in ask.stdout, _out(ask))
    check("[CP4-T10] CP1's approval succeeds", _clean(ha), _out(ha))
    st = _state(d)
    check("[CP4-T10] the run moves on to CP2", st.get("phase") == "building" and st.get("current") == "CP2",
          (st.get("phase"), st.get("current")))
    if st.get("current") != "CP2":
        return
    r2 = _build(d, 2, FIN3_STEPS[1], report=[{"id": "CP2-T01", "status": "passed"}, {"id": "CP2-T02", "status": "failed"}])
    st = _state(d)
    check("[CP4-T10] fixture: CP2 waits for its review", _clean(r2) and st.get("phase") == "awaiting_checkpoint_review"
          and st.get("review_target") == "CP2", (st.get("phase"), st.get("review_target")))
    g.rx(d, "ask", "human-approve")
    g.say(d, YES)
    ha2 = g.rx(d, "human-approve")
    out = _refused("[CP4-T10]", ha2, "CP2's approval (which would finish the run)")
    _names("[CP4-T10]", out, 3, LINE3, "the refusal")
    _not_named("[CP4-T10]", out, 1, LINE1, "the refusal")
    _not_named("[CP4-T10]", out, 2, LINE2, "the refusal")
    st = _state(d)
    check("[CP4-T10] phase stays awaiting_checkpoint_review with review_target CP2",
          st.get("phase") == "awaiting_checkpoint_review" and st.get("review_target") == "CP2",
          (st.get("phase"), st.get("review_target")))
    check("[CP4-T10] CP2's human gate is not passed", _human_gates(st).get("CP2") != "passed", _human_gates(st))


def cp4_t11_editing_the_done_means_lines_after_approval_does_not_clear_the_block():
    """[CP4-T11] Editing the done-means lines after approval does not clear the block."""
    for tag, dm, name in (("(line 2 dropped)", [LINE1], "t11a"), ("(line 2 reworded)", [LINE1, REWORDED], "t11b")):
        d, _ = _fin2(name)
        _edit_done_means(d / ".ratchet/runs/f", dm)
        ask, ha = _ask_yes_approve(d)
        hout = _refused(f"[CP4-T11] {tag}", ha, "human-approve")
        _names(f"[CP4-T11] {tag}", hout, 2, LINE2, "the refusal")
        check(f"[CP4-T11] {tag} the reworded text never appears as the blocking line", REWORDED not in hout, hout[-900:])
        sr = g.rx(d, "status")
        sout = _out(sr)
        check(f"[CP4-T11] {tag} status exits 0 with no traceback", _clean(sr), sout[-600:])
        _names(f"[CP4-T11] {tag}", sout, 2, LINE2, "status")
        check(f"[CP4-T11] {tag} status says the run cannot be marked done", BLOCKED_RE.search(sout) is not None, sout[-900:])
        check(f"[CP4-T11] {tag} the table does not shrink: both approved lines are listed",
              LINE1 in sout and LINE2 in sout, sout[-900:])
        check(f"[CP4-T11] {tag} the reworded text is not shown as a blocking line", REWORDED not in sout, sout[-900:])
        _still_waiting(f"[CP4-T11] {tag}", d)


def cp4_t12_feedback_cannot_remove_reword_or_reorder_an_approved_line():
    """[CP4-T12] Feedback cannot remove, reword or reorder an approved done-means line."""
    # each try on its own copy of the run: a try the engine wrongly accepts would otherwise move the run on and
    # hide what the next try does
    tries = (("(a) drops line 2", "t12a", [LINE1], ((2, LINE2),)),
             ("(b) rewords line 2", "t12b", [LINE1, REWORDED], ((2, LINE2),)),
             ("(c) swaps lines 1 and 2", "t12c", [LINE2, LINE1], ((1, LINE1), (2, LINE2))))
    for tag, name, dm, named in tries:
        d, _ = _fin2(name, failed=False)
        run = d / ".ratchet/runs/f"
        rf = _feedback(d)
        check(f"[CP4-T12] {tag} fixture: feedback is taken (phase planning_feedback)",
              _clean(rf) and _state(d).get("phase") == "planning_feedback", _out(rf))
        _extend_plan(run, dm, [_case("CP2-T01", [1])])
        r = g.rx(d, "plan-extend")
        out = _refused(f"[CP4-T12] {tag}", r, "plan-extend")
        check(f"[CP4-T12] {tag} the refusal names the changed line by number and its approved text",
              any(dp._names_line(out, n, t) for n, t in named), out[-900:])
        st = _state(d)
        check(f"[CP4-T12] {tag} nothing was registered: no CP2 in state, phase still planning_feedback",
              "CP2" not in (st.get("checkpoints") or {}) and st.get("phase") == "planning_feedback",
              (st.get("phase"), list(st.get("checkpoints") or {})))
    d, _ = _fin2("t12d", failed=False)
    run = d / ".ratchet/runs/f"
    rf = _feedback(d)
    check("[CP4-T12] (d) fixture: feedback is taken", _clean(rf) and _state(d).get("phase") == "planning_feedback", _out(rf))
    _extend_plan(run, DM2, [_case("CP2-T01", [1])])
    r = g.rx(d, "plan-extend")
    check("[CP4-T12] (d) with the objective untouched, plan-extend succeeds", _clean(r), _out(r))
    st = _state(d)
    check("[CP4-T12] (d) CP2 is registered and building",
          "CP2" in (st.get("checkpoints") or {}) and st.get("phase") == "building" and st.get("current") == "CP2",
          (st.get("phase"), st.get("current")))


def _extended_with_line3(name, cp2_failed):
    d, _ = _fin2(name, failed=False)
    run = d / ".ratchet/runs/f"
    rf = _feedback(d)
    assert _clean(rf), _out(rf)
    _extend_plan(run, DM3, [_case("CP2-T01", [3])])
    re_ = g.rx(d, "plan-extend")
    r2 = _build(d, 2, [_case("CP2-T01", [3])],
                report=[{"id": "CP2-T01", "status": "failed" if cp2_failed else "passed"}]) if _clean(re_) else None
    return d, re_, r2


def cp4_t13_feedback_may_append_a_line_held_to_the_finish_rule():
    """[CP4-T13] Feedback may add a new done-means line at the end, and the new line is then held to the finish
    rule."""
    d, re_, r2 = _extended_with_line3("t13", cp2_failed=False)
    check("[CP4-T13] plan-extend with line 3 appended (CP2-T01 proves it) succeeds", _clean(re_), _out(re_))
    check("[CP4-T13] CP2 completes and the run is back at the final review",
          r2 is not None and _clean(r2) and _state(d).get("phase") == "awaiting_final_review",
          (_out(r2)[-600:] if r2 else "", _state(d).get("phase")))
    tbl = dt._table(d)
    check("[CP4-T13] the new final review reads three lines", isinstance(tbl, list) and len(tbl) == 3, tbl)
    if isinstance(tbl, list) and len(tbl) == 3:
        check("[CP4-T13] line 3 is proven by CP2-T01's passed case",
              tbl[2].get("text") == LINE3 and tbl[2].get("verdict") == "proven"
              and dt._case_result(tbl[2], "CP2-T01") == "passed", tbl[2])
    sout = _out(g.rx(d, "status"))
    check("[CP4-T13] status carries no blocked wording", BLOCKED_RE.search(sout) is None, sout[-900:])
    ask, ha = _ask_yes_approve(d)
    check("[CP4-T13] the finish is not blocked on line 3: human-approve exits 0", _clean(ask) and _clean(ha), _out(ha))
    _finished("[CP4-T13]", d)
    d2, re2, r22 = _extended_with_line3("t13f", cp2_failed=True)
    check("[CP4-T13] (failed copy) plan-extend and CP2 complete", _clean(re2) and r22 is not None and _clean(r22),
          _out(re2) + (_out(r22)[-400:] if r22 else ""))
    _, ha2 = _ask_yes_approve(d2)
    hout = _refused("[CP4-T13] (failed copy)", ha2, "human-approve")
    _names("[CP4-T13] (failed copy)", hout, 3, LINE3, "the refusal")
    _failed_reason("[CP4-T13] (failed copy)", hout, "CP2-T01", "the refusal")
    _still_waiting("[CP4-T13] (failed copy)", d2)


def cp4_t14_approving_another_plan_change_cannot_sneak_in_an_edited_line():
    """[CP4-T14] Approving some other plan change cannot sneak in an edited done-means line."""
    d, _ = _fin2("t14")
    run = d / ".ratchet/runs/f"
    _edit_done_means(run, [LINE1, REWORDED])
    r = g.rx(d, "config", "--set", "max_adversarial_rounds=5")
    assert r.returncode == 0, _out(r)
    ask = g.rx(d, "ask", "approve-changes")
    check("[CP4-T14] fixture: ask approve-changes has something to show", _clean(ask) and "ASK THE USER" in ask.stdout, _out(ask))
    g.say(d, YES)
    ac = g.rx(d, "approve-changes")
    check("[CP4-T14] approve-changes succeeds for the rule change", _clean(ac), _out(ac))
    approved_dm = ((_state(d).get("approved") or {}).get("objective") or {}).get("done_means")
    check("[CP4-T14] the approved done-means lines are still the original ones", approved_dm == DM2, approved_dm)
    sout = _out(g.rx(d, "status"))
    _names("[CP4-T14]", sout, 2, LINE2, "status")
    check("[CP4-T14] status does not show the reworded text as a line", REWORDED not in sout, sout[-900:])
    _, ha = _ask_yes_approve(d)
    hout = _refused("[CP4-T14]", ha, "human-approve")
    _names("[CP4-T14]", hout, 2, LINE2, "the refusal")
    check("[CP4-T14] the refusal never names the reworded text", REWORDED not in hout, hout[-900:])
    _still_waiting("[CP4-T14]", d)


def cp4_t15_closing_the_open_finding_is_a_way_forward():
    """[CP4-T15] Closing the open finding is a way forward: the run can then be approved."""
    d = _approved("t15", extra_files={"docs/NOTES.md": "notes\n"})
    s1 = {"id": "S1", "severity": "minor", "kind": "missing-blocked-state-guard", "file": "docs/NOTES.md",
          "description": FIND_DESC}
    _build(d, 1, FIN2_STEPS[0], findings=[s1], complete=False)
    (d / "docs/NOTES.md").write_text("notes\nthe length message is documented now\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "document the message")
    rb = g.rx(d, "behavior", "CP1")
    assert rb.returncode == 0, _out(rb)
    rnd2 = g.open_round(d, "CP1")
    paths = [g.write_review(d, rnd2, rid, []) for rid in {rv["id"] for rv in rnd2["reviewers"]}]
    ra = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    assert ra.returncode == 0, _out(ra)
    rp._write_report(d, "CP1", PASSED2)
    rc = g.rx(d, "complete", "CP1")
    assert rc.returncode == 0 and _state(d)["phase"] == "awaiting_final_review", _out(rc)

    def name_line(st):
        for f in st["checkpoints"]["CP1"].get("deferred") or []:
            if f.get("id") == "S1":
                f["done_means"] = [2]
    dt.mutate(d, name_line)
    rows = json.loads(g.rx(d, "deferred", "--json").stdout or "[]")
    check("[CP4-T15] fixture: S1 is left for later, naming line 2",
          any(f.get("id") == "S1" and f.get("done_means") == [2] and not f.get("fixed") for f in rows), rows)
    sout = _out(g.rx(d, "status"))
    check("[CP4-T15] before mark-fixed, status says the run cannot be marked done and names S1",
          BLOCKED_RE.search(sout) is not None and re.search(r"(?<![A-Za-z0-9])S1(?![0-9])", sout) is not None, sout[-1200:])
    early = g.rx(d, "ask", "human-approve")
    check("[CP4-T15] before mark-fixed, ask human-approve is refused", early.returncode != 0, _out(early))
    mf = g.rx(d, "mark-fixed", "CP1", 'S1="documented the message, confirmed by the later clean round"')
    check("[CP4-T15] mark-fixed closes S1 (exit 0)", _clean(mf), _out(mf))
    sout2 = _out(g.rx(d, "status"))
    check("[CP4-T15] after mark-fixed, status shows no blocked wording", BLOCKED_RE.search(sout2) is None, sout2[-1200:])
    ask, ha = _ask_yes_approve(d)
    check("[CP4-T15] ask human-approve prints the question", _clean(ask) and FINISH_QUESTION in ask.stdout, _out(ask))
    check("[CP4-T15] human-approve exits 0", _clean(ha), _out(ha))
    _finished("[CP4-T15]", d)


def cp4_t16_feedback_is_open_while_blocked_and_does_not_clear_the_block():
    """[CP4-T16] Feedback is still open while the finish is blocked, and does not clear the block by itself."""
    d, _ = _fin2("t16")
    run = d / ".ratchet/runs/f"
    rf = _feedback(d)
    check("[CP4-T16] feedback exits 0 while blocked and moves to planning_feedback",
          _clean(rf) and _state(d).get("phase") == "planning_feedback", (_out(rf), _state(d).get("phase")))
    if _state(d).get("phase") != "planning_feedback":
        return
    _extend_plan(run, DM2, [_case("CP2-T01", [1])])
    re_ = g.rx(d, "plan-extend")
    check("[CP4-T16] plan-extend with the objective unchanged succeeds", _clean(re_), _out(re_))
    if not _clean(re_):
        return
    r2 = _build(d, 2, [_case("CP2-T01", [1])], report=[{"id": "CP2-T01", "status": "passed"}])
    check("[CP4-T16] after CP2 completes the run is back at the final review",
          _clean(r2) and _state(d).get("phase") == "awaiting_final_review", (_out(r2)[-500:], _state(d).get("phase")))
    tbl = dt._table(d)
    check("[CP4-T16] the table is recomputed: CP2-T01 is listed under line 1 as passed",
          isinstance(tbl, list) and len(tbl) == 2 and dt._case_result(tbl[0], "CP2-T01") == "passed", tbl)
    check("[CP4-T16] line 2 is still unproven (its failed result is unchanged)",
          isinstance(tbl, list) and len(tbl) == 2 and tbl[1].get("verdict") == "unproven"
          and dt._case_result(tbl[1], "CP1-T02") == "failed", tbl)
    _, ha = _ask_yes_approve(d)
    hout = _refused("[CP4-T16]", ha, "human-approve")
    _names("[CP4-T16]", hout, 2, LINE2, "the refusal")
    _still_waiting("[CP4-T16]", d, gates=False)


def cp4_x01_refused_approval_leaves_every_counter_untouched():
    """[CP4-X01] A refused approval leaves the review budgets and every other counter untouched."""
    d, _ = _fin2("x01")
    before = _counters(_state(d))
    n0 = len(_state(d).get("history") or [])
    check("[CP4-X01] fixture: the counters have known values (one review round recorded)",
          before["adv_attempts"].get("CP1") == 1 and before["adv_rounds_total"] == 1, before)
    ask, ha = _ask_yes_approve(d)
    for what, r in (("ask human-approve", ask), ("human-approve after a yes", ha)):
        check(f"[CP4-X01] {what} is refused", r.returncode != 0, _out(r)[-400:])
    st = _state(d)
    check("[CP4-X01] after the chat refusals every counter equals its recorded value", _counters(st) == before,
          (before, _counters(st)))
    new = _events(st)[n0:]
    check("[CP4-X01] the only new history lines are refusals: no 'approved' or 'unblocked' event",
          "approved" not in new and "unblocked" not in new, new)
    dd, env, denv = _director_fin2("x01")
    dbefore = _counters(th.cp3_state(dd))
    dn0 = len(th.cp3_state(dd).get("history") or [])
    _, tok = _director_token(dd, denv)
    r = th.rx(dd, env, "human-approve", "--director-token", tok)
    check("[CP4-X01] the director-token human-approve is refused", r.returncode != 0, _out(r)[-400:])
    dst = th.cp3_state(dd)
    check("[CP4-X01] after the director refusal every counter equals its recorded value", _counters(dst) == dbefore,
          (dbefore, _counters(dst)))
    dnew = _events(dst)[dn0:]
    check("[CP4-X01] (director lane) no 'approved' or 'unblocked' event", "approved" not in dnew and "unblocked" not in dnew, dnew)
    dn = _approved("x01n", human_review="none")
    _build(dn, 1, FIN2_STEPS[0], report=FAILED2, complete=False)
    nbefore = _counters(_state(dn))
    nn0 = len(_state(dn).get("history") or [])
    rc = g.rx(dn, "complete", "CP1")
    check("[CP4-X01] (none mode) complete CP1 exits 0 and the run is held at the final review",
          _clean(rc) and _state(dn).get("phase") == "awaiting_final_review", (_out(rc)[-400:], _state(dn).get("phase")))
    nst = _state(dn)
    check("[CP4-X01] (none mode) every counter equals its recorded value after the held complete",
          _counters(nst) == nbefore, (nbefore, _counters(nst)))
    nnew = _events(nst)[nn0:]
    check("[CP4-X01] (none mode) no 'approved' or 'unblocked' event", "approved" not in nnew and "unblocked" not in nnew, nnew)


def cp4_x02_review_limit_block_can_still_be_unblocked():
    """[CP4-X02] A run stuck at a review-limit block can still be unblocked; the finish rule applies only to
    finishing."""
    d = _approved("x02", config=["max_adversarial_rounds=2"])
    _write_step_tests(d, 1, FIN2_STEPS[0])
    assert g.rx(d, "tests-written", "CP1").returncode == 0
    assert g.rx(d, "red", "CP1").returncode == 0
    (d / "src/f.js").write_text("export const f = () => 1;\n")
    assert g.rx(d, "implemented", "CP1").returncode == 0
    r = g.rx(d, "behavior", "CP1")
    assert r.returncode == 0, _out(r)
    tl._drive_to_cap(d, "CP1")
    st = _state(d)
    check("[CP4-X02] fixture: the run is blocked at the review limit, mid-build", st.get("phase") == "blocked"
          and st.get("current") == "CP1", (st.get("phase"), st.get("blocked_reason")))
    ask = g.rx(d, "ask", "human-approve")
    check("[CP4-X02] ask human-approve prints the carry-on question", _clean(ask) and "carry on" in ask.stdout, _out(ask))
    g.say(d, YES)
    ha = g.rx(d, "human-approve")
    out = _out(ha)
    check("[CP4-X02] human-approve unblocks the run (exit 0)", _clean(ha), out[-600:])
    st = _state(d)
    check("[CP4-X02] phase is building and history has 'unblocked'",
          st.get("phase") == "building" and "unblocked" in _events(st), (st.get("phase"), _events(st)[-4:]))
    check("[CP4-X02] no finish-blocked wording is shown", BLOCKED_RE.search(out) is None and LINE2 not in out, out[-600:])


def cp4_x03_finding_naming_no_line_and_a_fixed_one_never_block():
    """[CP4-X03] A finding that names no done-means line, and a proven line, never block the finish."""
    d = _approved("x03")
    n1 = {"id": "N1", "severity": "minor", "file": "src/f.js", "description": "needs a comment"}
    r = _build(d, 1, FIN2_STEPS[0], report=PASSED2, findings=[n1])
    assert _clean(r) and _state(d)["phase"] == "awaiting_final_review", _out(r)
    _add_open_finding(d, "CP1", "F2", [2], fixed={"round": 1, "how": "showed the message"})
    rows = json.loads(g.rx(d, "deferred", "--json").stdout or "[]")
    check("[CP4-X03] fixture: N1 (no line) is left for later; F2 (line 2) is fixed later",
          any(f.get("id") == "N1" and not f.get("fixed") and f.get("done_means") is None for f in rows)
          and any(f.get("id") == "F2" and f.get("fixed") for f in rows), rows)
    sout = _out(g.rx(d, "status"))
    check("[CP4-X03] status still prints the left-for-later count", "1 finding(s) were left for later" in sout, sout[-900:])
    check("[CP4-X03] status carries no blocked wording", BLOCKED_RE.search(sout) is None, sout[-900:])
    ask, ha = _ask_yes_approve(d)
    check("[CP4-X03] ask human-approve prints the question", _clean(ask) and FINISH_QUESTION in ask.stdout, _out(ask))
    check("[CP4-X03] human-approve exits 0", _clean(ha), _out(ha))
    _finished("[CP4-X03]", d)


def cp4_x04_older_plan_finishes_as_before_with_a_plain_note():
    """[CP4-X04] A run whose plan was approved before this change finishes as before, with a plain note."""
    d = _approved("x04")
    _make_legacy(d)
    r = _build(d, 1, FIN2_STEPS[0], report=FAILED2)
    assert _clean(r) and _state(d)["phase"] == "awaiting_final_review", _out(r)
    _add_open_finding(d, "CP1", "S2", [2])
    sr = g.rx(d, "status")
    sout = _out(sr)
    check("[CP4-X04] status exits 0 with no traceback", _clean(sr), sout[-600:])
    check("[CP4-X04] status shows the done-means lines", LINE1 in sout and LINE2 in sout, sout[-900:])
    check("[CP4-X04] status shows no blocked wording", BLOCKED_RE.search(sout) is None, sout[-900:])
    check("[CP4-X04] status carries one plain note: approved before unproven lines blocked the finish, shown but "
          "not stopping it", LEGACY_NOTE_RE.search(sout) is not None and LEGACY_NO_STOP_RE.search(sout) is not None,
          sout[-900:])
    ask, ha = _ask_yes_approve(d)
    check("[CP4-X04] ask human-approve prints the question", _clean(ask) and FINISH_QUESTION in ask.stdout, _out(ask))
    check("[CP4-X04] human-approve exits 0 with no traceback", _clean(ha), _out(ha))
    _finished("[CP4-X04]", d)


def cp4_x05_older_none_mode_run_and_no_objective_run_still_finish():
    """[CP4-X05] An older run set to need no final review, or with no objective at all, still finishes."""
    d = _approved("x05a", human_review="none")
    _make_legacy(d)
    r = _build(d, 1, FIN2_STEPS[0], report=FAILED2)
    out = _out(r)
    check("[CP4-X05] (a) complete CP1 exits 0 with no traceback", _clean(r), out[-900:])
    check("[CP4-X05] (a) phase is done, as before", _state(d).get("phase") == "done", _state(d).get("phase"))
    check("[CP4-X05] (a) complete prints the plain note", LEGACY_NOTE_RE.search(out) is not None, out[-900:])
    check("[CP4-X05] (a) complete shows no blocked wording", NOT_FINISHED_RE.search(out) is None, out[-900:])
    d2 = rp._no_objective_project("dmfin-x05b")
    dt._step(d2, 1, [{"id": "CP1-T01", "layer": "code"}])
    check("[CP4-X05] (b) fixture: the no-objective run reached the final review",
          _state(d2).get("phase") == "awaiting_final_review", _state(d2).get("phase"))
    sr = g.rx(d2, "status")
    check("[CP4-X05] (b) status prints the existing 'no objective was stated' note, no traceback",
          _clean(sr) and "no objective was stated" in sr.stdout, _out(sr)[-600:])
    ask, ha = _ask_yes_approve(d2)
    check("[CP4-X05] (b) ask human-approve prints the question", _clean(ask) and FINISH_QUESTION in ask.stdout, _out(ask))
    check("[CP4-X05] (b) human-approve exits 0, no traceback, no refusal", _clean(ha), _out(ha))
    _finished("[CP4-X05] (b)", d2)


def cp4_x06_legacy_run_stays_legacy_through_feedback_plan_extend_and_approve_changes():
    """[CP4-X06] A legacy run that goes through feedback and plan-extend stays legacy."""
    d = _approved("x06")
    _make_legacy(d)
    run = d / ".ratchet/runs/f"
    r = _build(d, 1, FIN2_STEPS[0], report=FAILED2)
    assert _clean(r) and _state(d)["phase"] == "awaiting_final_review", _out(r)
    rf = _feedback(d)
    check("[CP4-X06] feedback is taken", _clean(rf), _out(rf))
    _extend_plan(run, DM2, [_case("CP2-T01", [2])])
    re_ = g.rx(d, "plan-extend")
    check("[CP4-X06] plan-extend succeeds", _clean(re_), _out(re_))
    r2 = _build(d, 2, [_case("CP2-T01", [2])], report=[{"id": "CP2-T01", "status": "passed"}]) if _clean(re_) else None
    check("[CP4-X06] CP2 completes and the run is at the final review again",
          r2 is not None and _clean(r2) and _state(d).get("phase") == "awaiting_final_review",
          (_out(r2)[-400:] if r2 else "", _state(d).get("phase")))
    sout = _out(g.rx(d, "status"))
    check("[CP4-X06] status carries the plain note and no blocked wording (line 2's CP1-T02 is still failed)",
          LEGACY_NOTE_RE.search(sout) is not None and BLOCKED_RE.search(sout) is None, sout[-900:])
    ask, ha = _ask_yes_approve(d)
    check("[CP4-X06] ask human-approve prints the question", _clean(ask) and FINISH_QUESTION in ask.stdout, _out(ask))
    check("[CP4-X06] human-approve finishes the run (exit 0)", _clean(ha), _out(ha))
    _finished("[CP4-X06]", d)
    # approve-changes on a legacy run keeps it legacy too
    d2 = _approved("x06c")
    _make_legacy(d2)
    r = _build(d2, 1, FIN2_STEPS[0], report=FAILED2)
    assert _clean(r) and _state(d2)["phase"] == "awaiting_final_review", _out(r)
    assert g.rx(d2, "config", "--set", "max_adversarial_rounds=5").returncode == 0
    g.rx(d2, "ask", "approve-changes")
    g.say(d2, YES)
    ac = g.rx(d2, "approve-changes")
    check("[CP4-X06] (approve-changes copy) approve-changes succeeds", _clean(ac), _out(ac))
    sout2 = _out(g.rx(d2, "status"))
    check("[CP4-X06] (approve-changes copy) status still carries the plain note and no block",
          LEGACY_NOTE_RE.search(sout2) is not None and BLOCKED_RE.search(sout2) is None, sout2[-900:])
    ask2, ha2 = _ask_yes_approve(d2)
    check("[CP4-X06] (approve-changes copy) the run finishes as before", _clean(ask2) and _clean(ha2), _out(ha2))
    _finished("[CP4-X06] (approve-changes copy)", d2)


def _paragraphs(text):
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


FINISH_BLOCK_DOC_RE = re.compile(
    r"(?:cannot|can't|can not|never|not|refus\w*|block\w*|held)[^.\n]{0,160}"
    r"(?:marked (?:as )?(?:done|finished)|approved as (?:done|finished)|finish(?:ed|es)? the run|"
    r"close(?:d|s)? (?:this|the) run|be finished|be done|be approved as done|mark(?:ed)? (?:the )?(?:run|work|it) "
    r"(?:as )?(?:done|approved|finished))", re.I)


def _says_blocked(text):
    """Some paragraph says the run cannot be marked done (finished, approved as done) while a done-means line is
    unproven or has an open finding against it: the finish-block sentence plus both 'unproven' and 'finding'."""
    for p in _paragraphs(text):
        low = p.lower()
        if "unproven" in low and "finding" in low and FINISH_BLOCK_DOC_RE.search(p):
            return True
    return False


def _says_no_ask_while_blocked(text):
    """A sentence ties not asking (or asking only) for the approval to the finish being blocked / lines proven."""
    return re.search(r"(?:not|never|don't|do not|no)[^.\n]{0,80}\bask(?:s|ed|ing)?\b[^.\n]{0,120}(?:approv|human-approve)"
                     r"[^.\n]{0,200}(?:unproven|blocked|open finding|proven)"
                     r"|(?:unproven|blocked|open finding)[^.\n]{0,200}(?:not|never|don't|do not|no)[^.\n]{0,80}\bask(?:s|ed|ing)?\b"
                     r"[^.\n]{0,120}(?:approv|human-approve)"
                     r"|\bask(?:s|ed|ing)?\b[^.\n]{0,80}(?:approv|human-approve)[^.\n]{0,120}only (?:when|if|once|after)"
                     r"[^.\n]{0,160}(?:proven|unproven|block)",
                     text, re.I) is not None


def _says_older_runs_note(text):
    """A paragraph says a run approved before this change keeps working (finishes / is not blocked) with a note,
    the 'approved before' sentence itself speaking of the finish or unproven lines."""
    for p in _paragraphs(text):
        low = p.lower()
        if "note" in low and re.search(r"approved before[^.\n]{0,250}(?:unproven|finish|marked done|the finish)"
                                       r"|(?:unproven|finish|marked done)[^.\n]{0,250}approved before", p, re.I):
            return True
    return False


PHASE_C_CONDITION_RE = re.compile(
    r"(?:if|while|when)[^.\n]{0,60}(?:any|a|some|one)[^.\n]{0,40}lines?[^.\n]{0,80}(?:unproven|open finding)"
    r"|unproven[^.\n]{0,160}(?:do not|don't|never|cannot|can't|not)[^.\n]{0,80}\bask"
    r"|(?:do not|don't|never)[^.\n]{0,80}\bask[^.\n]{0,200}(?:unproven|blocked)"
    r"|only (?:when|if|once|after)[^.\n]{0,120}(?:every line|all lines|each line|no line|nothing)[^.\n]{0,80}(?:proven|unproven|block)"
    r"|(?:unless|until)[^.\n]{0,120}(?:unproven|blocked|proven)", re.I)
SCHEMAS_APPROVED_LINES_RE = re.compile(
    r"(?:table|verdict|finish|block\w*)[^.\n]{0,200}(?:approved|approval snapshot)[^.\n]{0,120}(?:done.means|lines)"
    r"[^.\n]{0,200}(?:edit|read|checkpoints\.json|chang|rewor)"
    r"|(?:approved|approval snapshot)[^.\n]{0,120}(?:done.means|lines)[^.\n]{0,200}(?:table|verdict|finish|block\w*)"
    r"[^.\n]{0,200}(?:edit|read|checkpoints\.json|chang|rewor)"
    r"|(?:edit|checkpoints\.json|chang|rewor)[^.\n]{0,200}(?:approved|approval snapshot)[^.\n]{0,120}(?:done.means|lines)"
    r"[^.\n]{0,200}(?:table|verdict|finish|block\w*)", re.I)
SCHEMAS_FEEDBACK_RE = re.compile(
    r"(?:feedback|plan-extend)[^.\n]{0,200}(?:cannot|can't|never|not|refus\w*)[^.\n]{0,120}"
    r"(?:remove|reword|drop|change|edit|reorder)[^.\n]{0,80}(?:line|done.means)"
    r"|(?:line|done.means)[^.\n]{0,120}(?:cannot|can't|never|not|refus\w*)[^.\n]{0,120}"
    r"(?:removed|reworded|dropped|changed|edited|reordered)[^.\n]{0,160}(?:feedback|plan-extend)"
    r"|(?:remove|reword|drop|reorder)\w*[^.\n]{0,120}(?:approved|done.means) lines?[^.\n]{0,160}(?:feedback|plan-extend)"
    r"[^.\n]{0,120}(?:refus|cannot|can't|never)", re.I)


def cp4_x07_docs_tell_agents_about_the_blocked_finish_and_the_old_run_note():
    """[CP4-X07] The docs tell agents about the blocked finish and the old-run note."""
    files = {"README.md": ROOT / "README.md",
             "SKILL.md": ROOT / "skills/ratchet-orchestrator/SKILL.md",
             "schemas.md": ROOT / "skills/ratchet-orchestrator/references/schemas.md"}
    text = {k: p.read_text() for k, p in files.items()}
    for k, t in text.items():
        check(f"[CP4-X07] {k} says the run cannot be marked done while a done-means line is unproven or has an open "
              "finding", _says_blocked(t), "")
        check(f"[CP4-X07] {k} says the agent does not ask the user to approve while the finish is blocked",
              _says_no_ask_while_blocked(t), "")
        check(f"[CP4-X07] {k} says runs approved before the change keep working with a plain note",
              _says_older_runs_note(t), "")
    skill = text["SKILL.md"]
    i = skill.find("Phase C")
    phase_c = skill[i:] if i != -1 else ""
    paras = [p for p in _paragraphs(phase_c) if "ask human-approve" in p]
    check("[CP4-X07] SKILL.md's Phase C has an ask human-approve instruction",
          bool(paras), "no Phase C paragraph mentions ask human-approve")
    check("[CP4-X07] SKILL.md's Phase C ask-then-human-approve instruction is conditioned on the finish not being blocked",
          bool(paras) and any(PHASE_C_CONDITION_RE.search(p) for p in paras), paras[:1])
    sch = text["schemas.md"]
    check("[CP4-X07] schemas.md says the approved done-means lines are the ones the table and the finish check read, "
          "not an edited checkpoints.json", SCHEMAS_APPROVED_LINES_RE.search(sch) is not None, "")
    check("[CP4-X07] schemas.md says feedback cannot remove or reword an approved line",
          SCHEMAS_FEEDBACK_RE.search(sch) is not None, "")


def cp4_t17_docs_only_last_step_cannot_finish_past_an_unproven_line():
    """[CP4-T17] A last step that only edits docs, and so skips adversarial review, still cannot finish the run
    past an unproven line."""
    d, r = _docs_run("t17e", human_review="end", cp2_failed=True)
    out = _out(r)
    check("[CP4-T17] (end) complete CP2 exits 0 and reaches awaiting_final_review",
          _clean(r) and _state(d).get("phase") == "awaiting_final_review", (out[-500:], _state(d).get("phase")))
    ask, ha = _ask_yes_approve(d)
    aout = _refused("[CP4-T17] (end)", ask, "ask human-approve")
    hout = _refused("[CP4-T17] (end)", ha, "human-approve")
    for where, msg in (("the ask refusal", aout), ("the human-approve refusal", hout)):
        _names("[CP4-T17] (end)", msg, 2, LINE2, where)
        _failed_reason("[CP4-T17] (end)", msg, "CP2-T01", where)
    _still_waiting("[CP4-T17] (end)", d)
    dn, rn = _docs_run("t17n", human_review="none", cp2_failed=True)
    nout = _out(rn)
    check("[CP4-T17] (none) complete CP2 exits 0 but leaves the phase awaiting_final_review, not done",
          _clean(rn) and _state(dn).get("phase") == "awaiting_final_review", (nout[-500:], _state(dn).get("phase")))
    check("[CP4-T17] (none) complete carries the blocked wording naming line 2",
          NOT_FINISHED_RE.search(nout) is not None and dp._names_line(nout, 2, LINE2), nout[-900:])
    dp2, rp2 = _docs_run("t17p", human_review="end", cp2_failed=False)
    check("[CP4-T17] (end, proven) complete CP2 reaches the final review", _clean(rp2)
          and _state(dp2).get("phase") == "awaiting_final_review", _state(dp2).get("phase"))
    ask2, ha2 = _ask_yes_approve(dp2)
    check("[CP4-T17] (end, proven) the same docs-only run finishes as before", _clean(ask2) and _clean(ha2), _out(ha2))
    _finished("[CP4-T17] (end, proven)", dp2)
    dn2, rn2 = _docs_run("t17np", human_review="none", cp2_failed=False)
    check("[CP4-T17] (none, proven) complete CP2 finishes the run as before",
          _clean(rn2) and _state(dn2).get("phase") == "done", (_out(rn2)[-400:], _state(dn2).get("phase")))


def cp4_t18_merging_main_between_steps_does_not_change_the_lines_the_finish_reads():
    """[CP4-T18] Merging main between steps does not change the done-means lines the finish reads."""
    for tag, reword in (("(plain)", False), ("(line 2 reworded before the merge)", True)):
        d, _ = _fin2("t18" + ("r" if reword else "p"), branch="lane")
        if reword:
            _edit_done_means(d / ".ratchet/runs/f", [LINE1, REWORDED])
        before_st = _state(d)
        before_dm = (before_st.get("approved") or {}).get("objective", {}).get("done_means")
        before_counts = _counters(before_st)
        before_rule = (before_st.get("approved") or {}).get("rules", {}).get("max_adversarial_rounds")
        _merge_main_in(d)
        rm = g.rx(d, "merged")
        check(f"[CP4-T18] {tag} merged accepts the merge", _clean(rm), _out(rm)[-600:])
        st = _state(d)
        check(f"[CP4-T18] {tag} the config rule from main moved into the approved baseline",
              (st.get("approved") or {}).get("rules", {}).get("max_adversarial_rounds") != before_rule
              and "Nothing has changed" in _out(g.rx(d, "ask", "approve-changes")), (st.get("approved") or {}).get("rules"))
        check(f"[CP4-T18] {tag} the approved done-means lines are the same before and after merged",
              before_dm == DM2 and (st.get("approved") or {}).get("objective", {}).get("done_means") == DM2,
              (before_dm, (st.get("approved") or {}).get("objective")))
        check(f"[CP4-T18] {tag} every review counter is the same before and after merged", _counters(st) == before_counts,
              (before_counts, _counters(st)))
        sout = _out(g.rx(d, "status"))
        _names(f"[CP4-T18] {tag}", sout, 2, LINE2, "status")
        check(f"[CP4-T18] {tag} status says the run cannot be marked done", BLOCKED_RE.search(sout) is not None, sout[-900:])
        check(f"[CP4-T18] {tag} status never shows the reworded text", REWORDED not in sout, sout[-900:])
        _, ha = _ask_yes_approve(d)
        hout = _refused(f"[CP4-T18] {tag}", ha, "human-approve")
        _names(f"[CP4-T18] {tag}", hout, 2, LINE2, "the refusal")
        check(f"[CP4-T18] {tag} the refusal never names the reworded text", REWORDED not in hout, hout[-900:])
        _still_waiting(f"[CP4-T18] {tag}", d)
    dp_, _ = _fin2("t18c", failed=False, branch="lane")
    _merge_main_in(dp_)
    rm = g.rx(dp_, "merged")
    check("[CP4-T18] (proven copy) merged accepts the merge", _clean(rm), _out(rm)[-600:])
    ask, ha = _ask_yes_approve(dp_)
    check("[CP4-T18] (proven copy) the run still approves and finishes after merged", _clean(ask) and _clean(ha), _out(ha))
    _finished("[CP4-T18] (proven copy)", dp_)


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
                   if callable(fn) and (m := re.match(r"(cp4_[tx]\d+)_", name)))
    only = set(sys.argv[1:])
    for cid, fn in tests:
        if only and cid not in only:
            continue
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
    write_report([cid for cid, _ in tests if not only or cid in only])
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
