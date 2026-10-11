#!/usr/bin/env python3
"""
CP1 tests: review limits cannot creep.   python3 tests/test_limits.py

A step's adversarial review round limit may be raised only once, and only with the user's own reason (an exact
`raise: <reason>` reply in the owning chat, after `ratchet.py ask raise-limit CPn`); the reason lands in
.ratchet/learnings.md and a later `config --set max_adversarial_rounds=...` never lifts the recorded cap. The
block menu the agent is offered at the round limit leads with simplify, then change the step, then stop, with
raising the limit last and accepting the open findings offered as its own plain line (never a lettered pick, so
a lettered choice plus human-approve can never stand in for the user's own `accept: <reason>` reply). A new plan
with a high-risk (`risk: "high"`) checkpoint and no `risk_reason` is refused at `plan-submit`, naming the step;
an already-approved plan that predates this rule keeps working, and `plan-extend` only ever checks the new
checkpoints it appends. The fixer and the adversarial-loop skill tell the fixer to make the smallest change that
resolves each finding, never invent a new mechanism or file the finding doesn't need, and stop for a check-in
when a proper fix needs a redesign.

Reuses tests/test_gates.py (`project`, `approve_plan`, `write_tests`, `to_adversarial`, `open_round`, `R0`,
`write_review`, `rx`, `say`, `git`, `ENV`, `ENGINE`) for the throwaway git projects and the real-CLI-subprocess,
private-RATCHET_HOME way every suite in this repo drives the engine (see test_converge.py, which reuses the same
module the same way). Round-limit cases (CP1-T01..T05, T10) drive a checkpoint through repeated blocking
adversarial rounds (a blocker finding carrying `user_impact`, the way test_converge.py's MAJOR fixture does),
triaging each one (test_gates.py's open_round() already does this automatically) and fixing src/f.js between
rounds, with `checkin_round=99` configured so a check-in never opens first (this plan is not about check-ins).
High-risk cases (CP1-T07..T09) call `plan-submit`/`plan-extend` directly. CP1-T09's "approved before this rule
existed" fixture is the one history nothing else can produce: calling plan-submit against today's (fixed) engine
for a fresh high-risk checkpoint with no risk_reason is refused by design (that is exactly CP1-T07); so it is
fabricated with the engine's own pure helper functions (`_plan_hash`, `new_cp_state`, `approval_snapshot`,
`fingerprint`), imported as a library in a tiny helper script, skipping only the new risk_reason gate itself —
the same kind of direct state.json fixture tests/test_waiting.py already uses for an otherwise-unreachable
history. Doc cases (CP1-T06, T11) read the shipped files as text.

Every case that depends on a not-yet-built command (`raise-limit`) is written so a missing/failing command is
reported through `check()` as a clean fail, never an uncaught crash: later steps are skipped with their own
explicit failing checks instead of a bare assert on something the feature under test has not built yet.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP1-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                     # noqa: E402
import test_gates as g          # noqa: E402  (project, approve_plan, write_tests, to_adversarial, open_round, ...)

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

BLOCKER = [{"id": "B1", "severity": "blocker", "file": "src/f.js", "description": "unsafe write",
            "user_impact": "a user's saved data could be silently corrupted"}]


def _reviewers_after_triage(d, cp="CP1"):
    """Same auto-triage retry as test_gates.py's open_round(), but never asserts: returns the raw
    CompletedProcess either way, so a caller can tell a genuine round-cap block from a missing triage decision
    without crashing."""
    r = g.rx(d, "reviewers", cp, "--json")
    if r.returncode != 0 and "no fix-or-rebut" in (r.stderr or ""):
        m = re.search(r"no fix-or-rebut decision: (.+?)\. Run", r.stderr)
        for ref in (m.group(1).split(", ") if m else []):
            g.rx(d, "triage", cp, f'{ref}=fix:"test fixture"')
        r = g.rx(d, "reviewers", cp, "--json")
    return r


def _drive_to_cap_safe(d, cp="CP1"):
    """Writes a blocking review (a blocker with user_impact) each round, fixes src/f.js and triages between
    rounds, until `reviewers` itself refuses to open another round. Returns that failing CompletedProcess, or
    whatever unexpected result stopped the loop early (never raises)."""
    r = _reviewers_after_triage(d, cp)
    n = 0
    while r.returncode == 0:
        rnd = json.loads(r.stdout)
        p = g.write_review(d, rnd, g.R0(rnd), BLOCKER)
        adv = g.rx(d, "adv-record", cp, "--reviews", p)
        if adv.returncode != 2:
            return adv
        st = json.loads((d / ".ratchet/runs/f/state.json").read_text())
        if st.get("phase") == "blocked":
            return adv  # this round reached the cap: stop here, never edit or re-run behavior after it
        n += 1
        (d / "src/f.js").write_text(f"export const f = () => 1; // {cp}-cap-{n}\n")
        rb = g.rx(d, "behavior", cp)
        if rb.returncode != 0:
            return rb
        if n > 20:
            return r
        r = _reviewers_after_triage(d, cp)
    return r


def _drive_to_cap(d, cp="CP1"):
    """Like _drive_to_cap_safe, but for a FRESH checkpoint just moved to its adversarial step: reaching the cap
    there is existing, already-tested engine behaviour (test_gates.py's own round-cap case), so asserting it is
    safe and a failure here is a real test-harness bug, not the feature under test."""
    r = _drive_to_cap_safe(d, cp)
    assert r.returncode != 0, f"{cp}: expected the round cap to be reached: {r.stdout}{r.stderr}"
    return r


def _project_to_cap(name, max_rounds=2, cp="CP1", cps=1, config=None):
    cfg = [f"max_adversarial_rounds={max_rounds}", "checkin_round=99", *(config or [])]
    d = g.project(name, cps=cps, config=cfg)
    g.approve_plan(d)
    g.to_adversarial(d, cp)
    r = _drive_to_cap(d, cp)
    return d, r


def _raise(d, cp="CP1", reason_text="raise: this edge case is worth one more look"):
    g.rx(d, "ask", "raise-limit", cp)
    g.say(d, reason_text)
    return g.rx(d, "raise-limit", cp)


def _bare_project_risk(name, risk="high", risk_reason=None):
    """A fresh, unsubmitted plan with one checkpoint of the given risk/risk_reason — built the same way
    test_gates.py's project() builds its throwaway git project, but stopping BEFORE plan-submit so the case
    itself can call it and inspect the result."""
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-limits-{name}-"))
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        g.git(d, *c)
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "init")
    g.say(d, "please build the feature")
    r = g.rx(d, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr  # init itself is existing, unrelated behaviour
    g.rx(d, "config", "--set", "min_review_seconds=0", "providers=[]")
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": risk,
          "tests": {"files": ["tests/cp1.test.js"], "command": "node --test tests/cp1.test.js"}}
    if risk_reason is not None:
        cp["risk_reason"] = risk_reason
    plan = {"feature": "x", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": [cp]}
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
    (run / "checkpoints.json").write_text(json.dumps(plan))
    return d


_LEGACY_HELPER = Path(_tmp.mkdtemp(prefix="ratchet-limits-legacy-")) / "legacy_approve.py"
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
    engine's own (pure) helper functions, but skipping the new risk_reason gate itself: the one history nothing
    else can produce (CP1-T07 proves plan-submit rightly refuses this checkpoint against today's fixed engine),
    an already-approved plan predating the rule. Same technique as tests/test_waiting.py's direct state.json
    fixtures for a history the CLI itself can't produce."""
    r = subprocess.run([sys.executable, str(_LEGACY_HELPER), str(d), str(g.ENGINE.parent)],
                       cwd=d, capture_output=True, text=True, env=g.ENV)
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stdout + r.stderr


def _legacy_high_risk_project(name):
    d = _bare_project_risk(name, risk="high", risk_reason=None)
    _legacy_approve(d)
    return d


# ------------------------------------------------------------------------------------------------ cases

def t01_options_lead_with_simplify_raise_last():
    d, r = _project_to_cap("t01")
    check("[CP1-T01] setup: the step blocks at its round limit the first time", r.returncode != 0, r.stdout + r.stderr)
    na = json.loads(g.rx(d, "next").stdout)
    opts = na.get("options") or []
    texts = [o.get("text", "") for o in opts]
    check("[CP1-T01] four lettered options are offered (raise not yet used)", len(opts) == 4, opts)
    check("[CP1-T01] option 1 simplifies the step",
          bool(texts) and re.search(r"implify", texts[0]) is not None, texts)
    check("[CP1-T01] option 2 changes the step",
          len(texts) > 1 and re.search(r"[Cc]hange", texts[1]) is not None, texts)
    check("[CP1-T01] option 3 stops here",
          len(texts) > 2 and re.search(r"[Ss]top", texts[2]) is not None, texts)
    check("[CP1-T01] option 4 (last) raises the limit",
          len(texts) > 3 and re.search(r"[Rr]aise", texts[3]) is not None, texts)
    check("[CP1-T01] the lettered options are sequential",
          [o.get("letter") for o in opts] == list("ABCD"[:len(opts)]), opts)
    check("[CP1-T01] accepting findings is not among the lettered options",
          not any(re.search(r"\baccept\b", t, re.I) for t in texts), texts)
    check("[CP1-T01] accepting findings is offered as its own plain line instead",
          bool(na.get("accept_note")) and "accept" in na["accept_note"].lower(), na.get("accept_note"))


def t02_raise_needs_users_own_reason():
    d, r = _project_to_cap("t02")
    check("[CP1-T02] setup: the step is blocked at its round limit", r.returncode != 0, r.stdout + r.stderr)
    rr = _raise(d, "CP1")
    check("[CP1-T02] raise-limit succeeds after the owner's exact 'raise: ...' reply",
          rr.returncode == 0, rr.stdout + rr.stderr)
    learnings = (d / ".ratchet/learnings.md").read_text()
    check("[CP1-T02] the user's own reason is appended to learnings.md",
          "this edge case is worth one more look" in learnings, learnings[-500:])
    # the cap round's own blocker is still an open finding; the CP4 triage gate needs a fix-or-rebut decision
    # for it before `reviewers` can open a further round, so triage it here as setup (same helper test_gates.py's
    # open_round() uses) rather than asserting on the untriaged state.
    r2 = _reviewers_after_triage(d, "CP1")
    check("[CP1-T02] `reviewers` can now open a further round for this step", r2.returncode == 0, r2.stdout + r2.stderr)


def t03_second_raise_not_offered():
    d, r = _project_to_cap("t03")
    rr = _raise(d, "CP1")
    r2 = _drive_to_cap_safe(d, "CP1")
    na_r = g.rx(d, "next")
    try:
        na = json.loads(na_r.stdout)
    except (json.JSONDecodeError, ValueError):
        na = {}
    opts = na.get("options") or []
    texts = [o.get("text", "") for o in opts]
    check("[CP1-T03] setup: the one raise was used and the step blocks again at its (raised) cap",
          rr.returncode == 0 and r2.returncode != 0, (rr.stdout + rr.stderr, r2.stdout + r2.stderr))
    check("[CP1-T03] raise is not offered a second time",
          not any(re.search(r"[Rr]aise", t) for t in texts), texts)
    check("[CP1-T03] only simplify, change-the-step and stop remain as lettered options (3 total)",
          len(opts) == 3, opts)
    check("[CP1-T03] accept is still offered as its own plain line, not a lettered pick",
          bool(na.get("accept_note")) and not any(re.search(r"\baccept\b", t, re.I) for t in texts),
          na.get("accept_note"))


def t04_config_edit_does_not_lift_a_raised_cap():
    d, r = _project_to_cap("t04")
    rr = _raise(d, "CP1")
    st_before = json.loads((d / ".ratchet/runs/f/state.json").read_text())
    cap = (st_before.get("checkpoints", {}).get("CP1", {}).get("gates", {}).get("adversarial") or {}).get("cap_raised_to")
    check("[CP1-T04] setup: the raise recorded the step's own cap",
          rr.returncode == 0 and isinstance(cap, int) and cap > 0, (rr.stdout + rr.stderr, cap))
    cset = g.rx(d, "config", "--set", "max_adversarial_rounds=99")
    check("[CP1-T04] setup: the config edit itself is accepted", cset.returncode == 0, cset.stdout + cset.stderr)
    r2 = _drive_to_cap_safe(d, "CP1")
    check("[CP1-T04] the step still blocks at its own recorded cap after the config edit",
          r2.returncode != 0, r2.stdout + r2.stderr)
    check("[CP1-T04] the blocked reason does not cite the edited run-wide value (99)",
          "99" not in (r2.stderr or ""), r2.stderr)
    st_after = json.loads((d / ".ratchet/runs/f/state.json").read_text())
    cap_after = (st_after.get("checkpoints", {}).get("CP1", {}).get("gates", {}).get("adversarial") or {}).get("cap_raised_to")
    check("[CP1-T04] the recorded cap itself is unchanged by the config edit", cap_after == cap, (cap, cap_after))
    cfg = json.loads(g.rx(d, "config").stdout)
    check("[CP1-T04] the run-wide config value itself did take the edit (99)",
          cfg.get("max_adversarial_rounds") == 99, cfg)


def t05_only_owners_own_exact_reply_raises():
    d, r = _project_to_cap("t05")
    check("[CP1-T05] setup: the step is blocked at its round limit", r.returncode != 0, r.stdout + r.stderr)
    g.rx(d, "ask", "raise-limit", "CP1")
    g.say(d, "raise: fine by me", session="s-other")
    r2 = g.rx(d, "raise-limit", "CP1")
    check("[CP1-T05] raise-limit refuses a reply typed in a different chat session",
          r2.returncode != 0, r2.stdout + r2.stderr)
    st = json.loads((d / ".ratchet/runs/f/state.json").read_text())
    cap = (st.get("checkpoints", {}).get("CP1", {}).get("gates", {}).get("adversarial") or {}).get("cap_raised_to")
    check("[CP1-T05] the step's round limit is unchanged (no cap recorded)", cap is None, cap)
    learnings = (d / ".ratchet/learnings.md").read_text()
    check("[CP1-T05] no reason was appended to learnings.md", "fine by me" not in learnings, learnings[-300:])


def t06_fixer_and_skill_say_smallest_fix():
    fixer = (ROOT / "agents" / "ratchet-fixer.md").read_text()
    skill = (ROOT / "skills" / "ratchet-adversarial-loop" / "SKILL.md").read_text()
    check("[CP1-T06] the fixer is told to make the smallest change that resolves each finding",
          re.search(r"smallest change", fixer, re.I) is not None, "")
    check("[CP1-T06] the fixer is told not to add a new mechanism or file unless the finding needs it",
          re.search(r"no new mechanism", fixer, re.I) is not None
          or re.search(r"new mechanism or file", fixer, re.I) is not None, "")
    check("[CP1-T06] the fixer is told to stop and say so when a proper fix needs a redesign",
          re.search(r"redesign", fixer, re.I) is not None, "")
    check("[CP1-T06] the adversarial-loop skill also states the smallest-fix rule",
          re.search(r"smallest change", skill, re.I) is not None, "")
    check("[CP1-T06] the adversarial-loop skill also tells the fixer to stop for a redesign (a check-in)",
          re.search(r"redesign", skill, re.I) is not None and re.search(r"check-?in", skill, re.I) is not None, "")


def t07_high_risk_no_reason_refused_at_submit():
    d = _bare_project_risk("t07", risk="high", risk_reason=None)
    r = g.rx(d, "plan-submit")
    check("[CP1-T07] plan-submit is refused for a high-risk step with no risk_reason",
          r.returncode != 0, r.stdout + r.stderr)
    check("[CP1-T07] the refusal names the step", "CP1" in (r.stdout + r.stderr), r.stdout + r.stderr)
    st = json.loads(g.rx(d, "status", "--json").stdout)
    check("[CP1-T07] the run does not move to awaiting_plan_approval",
          st.get("phase") != "awaiting_plan_approval", st.get("phase"))


def t08_high_risk_with_reason_accepted():
    reason = ("a wrong check here would let a corner-cutting agent skip review and ship a bypassed auth check "
              "that hurts every signed-in user")
    d = _bare_project_risk("t08", risk="high", risk_reason=reason)
    r = g.rx(d, "plan-submit")
    check("[CP1-T08] plan-submit accepts a high-risk step with a risk_reason naming the user harm",
          r.returncode == 0, r.stdout + r.stderr)
    st = json.loads(g.rx(d, "status", "--json").stdout)
    check("[CP1-T08] the run moves to awaiting_plan_approval", st.get("phase") == "awaiting_plan_approval", st.get("phase"))


def t09_already_approved_plan_keeps_working():
    d = _legacy_high_risk_project("t09")
    g.to_adversarial(d, "CP1")
    rnd = g.open_round(d, "CP1")
    # high risk needs an approving review from every reviewer the round lists (CP7-T02: 4 for high risk here),
    # not just one -- an existing, unrelated requirement of the adversarial gate itself.
    paths = [g.write_review(d, rnd, rv["id"], []) for rv in rnd["reviewers"]]
    adv = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    assert adv.returncode == 0, adv.stdout + adv.stderr  # adv-record itself is existing, stable behaviour
    r = g.rx(d, "complete", "CP1")
    check("[CP1-T09] the existing high-risk step (approved before this rule existed) keeps working end to end",
          r.returncode == 0, r.stdout + r.stderr)
    if r.returncode != 0:
        check("[CP1-T09] plan-extend refuses only the newly appended high-risk CP2, naming it", False,
              "skipped: CP1 never completed")
        return
    g.rx(d, "ask", "human-approve")
    g.say(d, "approved")
    hr = g.rx(d, "human-approve")
    assert hr.returncode == 0, hr.stdout + hr.stderr  # human-approve flow is existing, stable behaviour
    g.say(d, "can we add one more check")
    fb = g.rx(d, "feedback", "--text", "one more check")
    assert fb.returncode == 0, fb.stdout + fb.stderr  # feedback flow is existing, stable behaviour
    run = d / ".ratchet/runs/f"
    plan = json.loads((run / "checkpoints.json").read_text())
    cp2 = {"id": "CP2", "title": "Step 2", "summary": "s", "done_when": ["works"],
           "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "high",
           "tests": {"files": ["tests/cp2.test.js"], "command": "node --test tests/cp2.test.js"}}
    plan["checkpoints"].append(cp2)
    (run / "checkpoints.json").write_text(json.dumps(plan))
    (run / "tests" / "CP2.json").write_text(json.dumps({"cases": [{"id": "CP2-T01", "layer": "code"}]}))
    r2 = g.rx(d, "plan-extend")
    check("[CP1-T09] plan-extend refuses only the newly appended high-risk CP2, naming it",
          r2.returncode != 0 and "CP2" in (r2.stdout + r2.stderr), (r2.stdout, r2.stderr))
    st2 = json.loads((d / ".ratchet/runs/f/state.json").read_text())
    check("[CP1-T09] CP1 itself (already completed) is unaffected by that refusal",
          st2["checkpoints"]["CP1"]["status"] == "done" and "CP2" not in st2["checkpoints"], st2["checkpoints"].keys())


def _to_adversarial_own_source(d, cp, var, srcfile):
    """Like test_gates.py's to_adversarial(), but writes this step's own source file instead of the shared
    src/f.js every checkpoint's write_tests() otherwise points at, so a later checkpoint's `red` genuinely
    fails (the source it imports does not exist yet) instead of silently passing against another step's
    already-built file."""
    (d / f"tests/{cp.lower()}.test.js").write_text(
        "import t from 'node:test'; import a from 'node:assert'; "
        f"import {{ {var} }} from '../{srcfile}';\n"
        f"t('[{cp}-T01] works', () => a.equal({var}(), 2));\n")
    assert g.rx(d, "tests-written", cp).returncode == 0
    r = g.rx(d, "red", cp)
    assert r.returncode == 0, r.stdout + r.stderr  # genuinely red: srcfile doesn't exist yet for this step
    (d / srcfile).write_text(f"export const {var} = () => 2;\n")
    assert g.rx(d, "implemented", cp).returncode == 0
    rb = g.rx(d, "behavior", cp)
    assert rb.returncode == 0, rb.stdout + rb.stderr


def t10_raised_cap_is_scoped_to_its_own_step():
    d = g.project("t10", cps=2, config=["max_adversarial_rounds=2", "checkin_round=99"])
    g.approve_plan(d)
    g.to_adversarial(d, "CP1")
    _drive_to_cap(d, "CP1")
    rr = _raise(d, "CP1")
    r2 = _reviewers_after_triage(d, "CP1")
    completed_cp1 = False
    if rr.returncode == 0 and r2.returncode == 0:
        rnd = json.loads(r2.stdout)
        p = g.write_review(d, rnd, g.R0(rnd), [])
        if g.rx(d, "adv-record", "CP1", "--reviews", p).returncode == 0:
            completed_cp1 = g.rx(d, "complete", "CP1").returncode == 0
    check("[CP1-T10] setup: CP1's raise works and CP1 completes, so the run reaches CP2", completed_cp1, "")
    if not completed_cp1:
        check("[CP1-T10] CP2 blocks at the plan's own ordinary cap, independent of CP1's raised cap", False, "")
        check("[CP1-T10] CP2 is still offered raise as a lettered option (its own raise is unused)", False, "")
        return
    _to_adversarial_own_source(d, "CP2", "g", "src/g.js")
    r3 = _drive_to_cap(d, "CP2")
    check("[CP1-T10] CP2 blocks at the plan's own ordinary cap (2 rounds), independent of CP1",
          r3.returncode != 0 and "2" in (r3.stderr or ""), r3.stdout + r3.stderr)
    na = json.loads(g.rx(d, "next").stdout)
    opts = na.get("options") or []
    texts = [o.get("text", "") for o in opts]
    check("[CP1-T10] CP2 is still offered raise as a lettered option (its own raise is unused)",
          any(re.search(r"[Rr]aise", t) for t in texts), texts)
    check("[CP1-T10] CP2's options still total 4 (simplify, change, stop, raise)", len(opts) == 4, opts)


def t11_docs_describe_the_rules():
    readme = (ROOT / "README.md").read_text()
    orch_skill = (ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md").read_text()
    adv_skill = (ROOT / "skills" / "ratchet-adversarial-loop" / "SKILL.md").read_text()
    schemas = (ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md").read_text()
    planner = (ROOT / "agents" / "ratchet-checkpoint-planner.md").read_text()
    blob = "\n".join([readme, orch_skill, adv_skill, schemas, planner])
    check("[CP1-T11] the docs say a review limit can be raised only once per step",
          re.search(r"rais\w*.{0,60}(?:only once|once per step)", blob, re.I | re.S) is not None, "")
    check("[CP1-T11] the docs say the raise needs the user's own reason",
          re.search(r"rais\w*.{0,160}own reason", blob, re.I | re.S) is not None
          or re.search(r"own reason.{0,160}rais", blob, re.I | re.S) is not None, "")
    check("[CP1-T11] the docs say a later config edit does not lift an already-raised cap",
          re.search(r"config.{0,160}(?:not|never|doesn'?t|won'?t|can'?t).{0,60}lift", blob, re.I | re.S) is not None, "")
    check("[CP1-T11] the docs state the smallest-fix rule",
          re.search(r"smallest change", blob, re.I) is not None, "")
    check("[CP1-T11] the docs say a high-risk step needs a risk_reason naming the user harm",
          "risk_reason" in blob
          and re.search(r"high.{0,40}risk.{0,160}(?:user harm|harms?|hurt)", blob, re.I | re.S) is not None, "")
    check("[CP1-T11] the docs say normal is the default risk",
          re.search(r"normal.{0,40}(?:is|as) the default risk", blob, re.I) is not None
          or re.search(r"default risk.{0,40}normal", blob, re.I) is not None, "")


def t_x01_raise_grants_exactly_one_more_round():
    """[CP1-X01] A raise promises exactly one more round: after a step blocks at its round-limit cap and the user
    raises it once, exactly one further adversarial round must open and be recordable before the step blocks
    again (not several, which `_unblock`'s own attempt-counter reset would otherwise hand back for free)."""
    d, r = _project_to_cap("x01", max_rounds=2)
    rr = _raise(d, "CP1")
    check("[CP1-X01] setup: the raise succeeds after the step blocked at its 2-round cap",
          rr.returncode == 0, rr.stdout + rr.stderr)
    r2 = _reviewers_after_triage(d, "CP1")
    check("[CP1-X01] the one promised extra round opens", r2.returncode == 0, r2.stdout + r2.stderr)
    rnd = json.loads(r2.stdout)
    p = g.write_review(d, rnd, g.R0(rnd), BLOCKER)
    adv = g.rx(d, "adv-record", "CP1", "--reviews", p)
    check("[CP1-X01] that one round is recorded as still blocking", adv.returncode == 2, adv.stdout + adv.stderr)
    st = json.loads((d / ".ratchet/runs/f/state.json").read_text())
    check("[CP1-X01] the step is blocked again right after this single further round",
          st.get("phase") == "blocked", st.get("phase"))
    gstate = (st.get("checkpoints", {}).get("CP1", {}).get("gates", {}).get("adversarial") or {})
    check("[CP1-X01] exactly the raised cap's worth of rounds were used, no more (attempts == cap_raised_to)",
          gstate.get("attempts") == gstate.get("cap_raised_to"), gstate)
    r3 = _reviewers_after_triage(d, "CP1")
    check("[CP1-X01] a further (second) round beyond the one promised is refused",
          r3.returncode != 0, r3.stdout + r3.stderr)


def t_x02_lettered_pick_plus_human_approve_does_not_reset_attempts():
    """[CP1-X02] After a step's one raise is spent and it blocks again at the raised cap, picking a non-raise
    lettered option ("change the step") and then running human-approve must not hand the step a fresh review
    budget: `_unblock` (the one place both `human-approve` and a lettered pick's resume share) must leave this
    step's adversarial attempt count exactly as it was, and no extra round may open afterward. Only `raise-limit`,
    with the user's own reason, ever grants a step more review rounds."""
    d, r = _project_to_cap("x02", max_rounds=2)
    rr = _raise(d, "CP1")
    check("[CP1-X02] setup: the raise succeeds after the step blocked at its 2-round cap",
          rr.returncode == 0, rr.stdout + rr.stderr)
    r2 = _reviewers_after_triage(d, "CP1")
    check("[CP1-X02] setup: the one promised extra round opens", r2.returncode == 0, r2.stdout + r2.stderr)
    rnd = json.loads(r2.stdout)
    p = g.write_review(d, rnd, g.R0(rnd), BLOCKER)
    adv = g.rx(d, "adv-record", "CP1", "--reviews", p)
    check("[CP1-X02] setup: that one round is recorded as still blocking, re-hitting the raised cap",
          adv.returncode == 2, adv.stdout + adv.stderr)
    st_before = json.loads((d / ".ratchet/runs/f/state.json").read_text())
    check("[CP1-X02] setup: the run is blocked again at the raised cap",
          st_before.get("phase") == "blocked", st_before.get("phase"))
    g_before = (st_before.get("checkpoints", {}).get("CP1", {}).get("gates", {}).get("adversarial") or {})
    check("[CP1-X02] setup: attempts are at the raised cap before the lettered pick",
          g_before.get("attempts") == g_before.get("cap_raised_to") and g_before.get("attempts"), g_before)
    g.rx(d, "ask", "choice", "--options", "A,B,C")
    g.say(d, "B")  # "change the step": never the (already-spent) raise option
    r3 = g.rx(d, "choice")
    check("[CP1-X02] the pick is recorded", r3.returncode == 0, r3.stdout + r3.stderr)
    r4 = g.rx(d, "human-approve")
    check("[CP1-X02] human-approve resumes the run on the pick", r4.returncode == 0, r4.stdout + r4.stderr)
    st_after = json.loads((d / ".ratchet/runs/f/state.json").read_text())
    g_after = (st_after.get("checkpoints", {}).get("CP1", {}).get("gates", {}).get("adversarial") or {})
    check("[CP1-X02] human-approve does NOT reset this step's adversarial attempt count",
          g_after.get("attempts") == g_before.get("attempts"), (g_before, g_after))
    r5 = _reviewers_after_triage(d, "CP1")
    check("[CP1-X02] no extra review round opens: the raise was already spent and human-approve grants none",
          r5.returncode != 0, r5.stdout + r5.stderr)


TESTS = [
    ("CP1-T01", t01_options_lead_with_simplify_raise_last),
    ("CP1-T02", t02_raise_needs_users_own_reason),
    ("CP1-T03", t03_second_raise_not_offered),
    ("CP1-T04", t04_config_edit_does_not_lift_a_raised_cap),
    ("CP1-T05", t05_only_owners_own_exact_reply_raises),
    ("CP1-T06", t06_fixer_and_skill_say_smallest_fix),
    ("CP1-T07", t07_high_risk_no_reason_refused_at_submit),
    ("CP1-T08", t08_high_risk_with_reason_accepted),
    ("CP1-T09", t09_already_approved_plan_keeps_working),
    ("CP1-T10", t10_raised_cap_is_scoped_to_its_own_step),
    ("CP1-T11", t11_docs_describe_the_rules),
    ("CP1-X01", t_x01_raise_grants_exactly_one_more_round),
    ("CP1-X02", t_x02_lettered_pick_plus_human_approve_does_not_reset_attempts),
]


def t_x03_every_round_limit_path_grants_exactly_its_promised_rounds():
    """[CP1-X03] Structural check across every path offered at a step's own adversarial round-limit block, each
    on its own fresh step driven to the cap: `raise-limit` grants exactly one more round (re-asserted here
    alongside the rest, so one test proves the whole class, not just the raise path CP1-X01 already covers);
    `check-in --decision simplify --why` and `check-in --decision change --why` each reuse check-in's own
    simplify record to grant exactly one more round, once each, never a second time for the same step;
    `accept-finding` closes the gate without opening any further round; a lettered "stop" pick plus
    `human-approve`, and a bare `human-approve` with no pick at all, both grant none at all -- only the three
    dedicated commands above ever hand a step more review rounds."""
    # --- raise: +1 round, never a second one ---
    d, r = _project_to_cap("x03raise", max_rounds=2)
    rr = _raise(d, "CP1")
    check("[CP1-X03] raise: the raise itself succeeds", rr.returncode == 0, rr.stdout + rr.stderr)
    r2 = _reviewers_after_triage(d, "CP1")
    check("[CP1-X03] raise: exactly one extra round opens", r2.returncode == 0, r2.stdout + r2.stderr)
    rnd = json.loads(r2.stdout)
    p = g.write_review(d, rnd, g.R0(rnd), BLOCKER)
    adv = g.rx(d, "adv-record", "CP1", "--reviews", p)
    check("[CP1-X03] raise: that one round still blocks, re-hitting the raised cap",
          adv.returncode == 2, adv.stdout + adv.stderr)
    r3 = _reviewers_after_triage(d, "CP1")
    check("[CP1-X03] raise: no second extra round opens (the one raise is spent)",
          r3.returncode != 0, r3.stdout + r3.stderr)

    # --- check-in simplify at the round-limit block: +1 round, once ---
    d, r = _project_to_cap("x03simplify", max_rounds=2)
    cs = g.rx(d, "check-in", "CP1", "--decision", "simplify", "--why", "narrowed the step's own scope")
    check("[CP1-X03] simplify: check-in simplify succeeds with no check-in open, at the round-limit block",
          cs.returncode == 0, cs.stdout + cs.stderr)
    gst = json.loads((d / ".ratchet/runs/f/state.json").read_text())["checkpoints"]["CP1"]["gates"]["adversarial"]
    check("[CP1-X03] simplify: round_limit_grants records exactly one grant", gst.get("round_limit_grants") == 1, gst)
    r2 = _reviewers_after_triage(d, "CP1")
    check("[CP1-X03] simplify: exactly one extra round opens", r2.returncode == 0, r2.stdout + r2.stderr)
    rnd = json.loads(r2.stdout)
    p = g.write_review(d, rnd, g.R0(rnd), BLOCKER)
    adv = g.rx(d, "adv-record", "CP1", "--reviews", p)
    check("[CP1-X03] simplify: that one round still blocks, re-hitting the limit",
          adv.returncode == 2, adv.stdout + adv.stderr)
    cs2 = g.rx(d, "check-in", "CP1", "--decision", "simplify", "--why", "again")
    check("[CP1-X03] simplify: a second simplify grant for this step is refused",
          cs2.returncode != 0, cs2.stdout + cs2.stderr)
    r3 = _reviewers_after_triage(d, "CP1")
    check("[CP1-X03] simplify: no second extra round opens (the grant is spent)",
          r3.returncode != 0, r3.stdout + r3.stderr)

    # --- check-in change at the round-limit block: +1 round, once ---
    d, r = _project_to_cap("x03change", max_rounds=2)
    cc = g.rx(d, "check-in", "CP1", "--decision", "change", "--why", "redefined the step's own approach")
    check("[CP1-X03] change: check-in change succeeds with no check-in open, at the round-limit block",
          cc.returncode == 0, cc.stdout + cc.stderr)
    gst = json.loads((d / ".ratchet/runs/f/state.json").read_text())["checkpoints"]["CP1"]["gates"]["adversarial"]
    check("[CP1-X03] change: round_limit_grants records exactly one grant", gst.get("round_limit_grants") == 1, gst)
    r2 = _reviewers_after_triage(d, "CP1")
    check("[CP1-X03] change: exactly one extra round opens", r2.returncode == 0, r2.stdout + r2.stderr)
    rnd = json.loads(r2.stdout)
    p = g.write_review(d, rnd, g.R0(rnd), BLOCKER)
    adv = g.rx(d, "adv-record", "CP1", "--reviews", p)
    check("[CP1-X03] change: that one round still blocks, re-hitting the limit",
          adv.returncode == 2, adv.stdout + adv.stderr)
    cc2 = g.rx(d, "check-in", "CP1", "--decision", "change", "--why", "again")
    check("[CP1-X03] change: a second change grant for this step is refused",
          cc2.returncode != 0, cc2.stdout + cc2.stderr)
    r3 = _reviewers_after_triage(d, "CP1")
    check("[CP1-X03] change: no second extra round opens (the grant is spent)",
          r3.returncode != 0, r3.stdout + r3.stderr)

    # --- accept-finding: closes the gate, opens no further round at all ---
    d, r = _project_to_cap("x03accept", max_rounds=2)
    st_before = json.loads((d / ".ratchet/runs/f/state.json").read_text())
    cps_before = st_before["checkpoints"]["CP1"]
    round_no = cps_before["adv_round"]["round"]
    attempts_before = cps_before["gates"]["adversarial"].get("attempts")
    of_path = d / f".ratchet/runs/f/evidence/CP1/adversarial/round-{round_no:02d}/open-findings.json"
    ids = [f["id"] for f in json.loads(of_path.read_text())]
    check("[CP1-X03] accept: setup has at least one open finding to accept", bool(ids), ids)
    g.rx(d, "ask", "accept-finding", "CP1", ",".join(ids))
    g.say(d, "accept: known limit for this fixture")
    racc = g.rx(d, "accept-finding", "CP1")
    check("[CP1-X03] accept: accept-finding passes the gate without opening another round",
          racc.returncode == 0, racc.stdout + racc.stderr)
    gst = json.loads((d / ".ratchet/runs/f/state.json").read_text())["checkpoints"]["CP1"]["gates"]["adversarial"]
    check("[CP1-X03] accept: the adversarial gate is now passed", gst.get("status") == "passed", gst)
    check("[CP1-X03] accept: no extra round was opened (attempts unchanged by accepting)",
          gst.get("attempts") == attempts_before, (attempts_before, gst.get("attempts")))

    # --- a lettered "stop" pick plus human-approve: grants none ---
    d, r = _project_to_cap("x03stop", max_rounds=2)
    g.rx(d, "ask", "choice", "--options", "A,B,C")
    g.say(d, "C")  # "Stop here and keep what is already finished"
    rc = g.rx(d, "choice")
    check("[CP1-X03] stop: the pick is recorded", rc.returncode == 0, rc.stdout + rc.stderr)
    rha = g.rx(d, "human-approve")
    check("[CP1-X03] stop: human-approve resumes the run on the pick", rha.returncode == 0, rha.stdout + rha.stderr)
    r2 = _reviewers_after_triage(d, "CP1")
    check("[CP1-X03] stop: no extra review round opens (stop grants none)",
          r2.returncode != 0, r2.stdout + r2.stderr)

    # --- a bare human-approve, with no lettered pick at all: grants none ---
    d, r = _project_to_cap("x03bare", max_rounds=2)
    g.rx(d, "ask", "human-approve")
    g.say(d, "yes, go ahead")
    rha = g.rx(d, "human-approve")
    check("[CP1-X03] bare human-approve: resumes the run with no pick at all",
          rha.returncode == 0, rha.stdout + rha.stderr)
    r2 = _reviewers_after_triage(d, "CP1")
    check("[CP1-X03] bare human-approve: no extra review round opens (grants none)",
          r2.returncode != 0, r2.stdout + r2.stderr)


TESTS.append(("CP1-X03", t_x03_every_round_limit_path_grants_exactly_its_promised_rounds))

import test_accept_finding as _taf  # noqa: E402  (run_wide_cap_project: two checkpoints, run-wide cap only)


def t_x04_run_wide_cap_unblock_via_human_approve_resets_cur_own_attempts():
    """[CP1-X04] Unblocking a RUN-WIDE adversarial round cap (not CP1's own per-step round limit) with a plain
    `human-approve` must still reset CP1's own adversarial attempt count, the same as every other failing gate
    `_unblock` resets. The run-wide cap is not "cur's own round-limit block", so `_unblock`'s `preserve_adv` must
    come from `_adv_block_kind` returning "own", not the broader `_adv_round_block_reason_for`, which also matches the
    run-wide-cap reason string regardless of which checkpoint is current."""
    d, env, tp = _taf.run_wide_cap_project("x04")
    _taf.th.cp3_pass_behavior(d, env, "CP1")
    rnd = _taf.th.cp3_open_round(d, env, "CP1")  # uses the run's one allowed round
    rid = _taf.th.cp3_R0(rnd)
    path = _taf.write_round_review(d, env, rnd, rid, [_taf.F1, _taf.F2])
    r = _taf.th.rx(d, env, "adv-record", "CP1", "--reviews", path)
    check("[CP1-X04] setup: CP1's round blocks with F1,F2 open", r.returncode == 2, r.stdout + r.stderr)
    _taf.th.rx(d, env, "triage", "CP1", f"{rid}/F1=fix:known tradeoff", f"{rid}/F2=fix:known tradeoff")
    r2 = _taf.th.rx(d, env, "reviewers", "CP1", "--json")
    out2 = r2.stdout + r2.stderr
    check("[CP1-X04] setup: opening a second round for CP1 hits the RUN-WIDE cap, not CP1's own per-step one",
          r2.returncode == 2 and "run-wide" in out2.lower(), out2)
    st0 = _taf.th.cp3_state(d)
    cp1_attempts_before = st0["checkpoints"]["CP1"]["gates"]["adversarial"].get("attempts")
    check("[CP1-X04] setup: the run is blocked", st0.get("phase") == "blocked", st0.get("phase"))
    check("[CP1-X04] setup: CP1's own adversarial attempts are non-zero going into the block",
          bool(cp1_attempts_before), cp1_attempts_before)
    _taf.th.rx(d, env, "ask", "human-approve")
    _taf.reply(d, tp, env, "yes, go ahead")
    r3 = _taf.th.rx(d, env, "human-approve")
    check("[CP1-X04] a plain human-approve resumes the run past the run-wide cap",
          r3.returncode == 0, r3.stdout + r3.stderr)
    st1 = _taf.th.cp3_state(d)
    check("[CP1-X04] the run-wide round budget is reset back to 0", st1.get("adv_rounds_total", -1) == 0,
          st1.get("adv_rounds_total"))
    cp1_attempts_after = st1["checkpoints"]["CP1"]["gates"]["adversarial"].get("attempts")
    check("[CP1-X04] CP1's OWN adversarial attempts reset to 0 too: a run-wide-cap unblock is not CP1's own "
          "round-limit block, so it must grant CP1 the same fresh attempt count every other failing gate gets",
          cp1_attempts_after == 0, cp1_attempts_after)


TESTS.append(("CP1-X04", t_x04_run_wide_cap_unblock_via_human_approve_resets_cur_own_attempts))


def t_x05_raise_limit_at_the_run_wide_cap_does_not_unblock_the_run():
    """[CP1-X05] Raising CP1's OWN adversarial round-limit cap does nothing for the RUN-WIDE round cap: when the
    block is the run-wide cap (not CP1's own per-step limit), `raise-limit CP1` must still record the raised cap
    (it is a real, one-time raise of CP1's own step limit) but must NOT unblock the run -- unblocking a run-wide
    cap needs a plain `human-approve`, same as every other failing gate, since raising one step's cap does not
    touch the run-wide budget."""
    d, env, tp = _taf.run_wide_cap_project("x05")
    _taf.th.cp3_pass_behavior(d, env, "CP1")
    rnd = _taf.th.cp3_open_round(d, env, "CP1")  # uses the run's one allowed round
    rid = _taf.th.cp3_R0(rnd)
    path = _taf.write_round_review(d, env, rnd, rid, [_taf.F1, _taf.F2])
    r = _taf.th.rx(d, env, "adv-record", "CP1", "--reviews", path)
    check("[CP1-X05] setup: CP1's round blocks with F1,F2 open", r.returncode == 2, r.stdout + r.stderr)
    _taf.th.rx(d, env, "triage", "CP1", f"{rid}/F1=fix:known tradeoff", f"{rid}/F2=fix:known tradeoff")
    r2 = _taf.th.rx(d, env, "reviewers", "CP1", "--json")
    out2 = r2.stdout + r2.stderr
    check("[CP1-X05] setup: opening a second round for CP1 hits the RUN-WIDE cap, not CP1's own per-step one",
          r2.returncode == 2 and "run-wide" in out2.lower(), out2)
    st0 = _taf.th.cp3_state(d)
    check("[CP1-X05] setup: the run is blocked", st0.get("phase") == "blocked", st0.get("phase"))
    _taf.th.rx(d, env, "ask", "raise-limit", "CP1")
    _taf.reply(d, tp, env, "raise: need one more look")
    r3 = _taf.th.rx(d, env, "raise-limit", "CP1")
    out3 = r3.stdout + r3.stderr
    check("[CP1-X05] raise-limit itself succeeds (it still raises CP1's own step cap)",
          r3.returncode == 0, out3)
    check("[CP1-X05] raise-limit does NOT claim the run is unblocked",
          "no longer blocked" not in out3.lower(), out3)
    st1 = _taf.th.cp3_state(d)
    check("[CP1-X05] the run is STILL blocked: raising CP1's step cap does not resolve the run-wide cap",
          st1.get("phase") == "blocked", st1.get("phase"))
    check("[CP1-X05] the run-wide round budget was NOT reset (only a real unblock resets it)",
          st1.get("adv_rounds_total", -1) != 0, st1.get("adv_rounds_total"))


TESTS.append(("CP1-X05", t_x05_raise_limit_at_the_run_wide_cap_does_not_unblock_the_run))


def _assert_raise_limit_refused(d, label):
    """Runs the full `ask raise-limit` -> reply -> `raise-limit` sequence against CP1 of project `d` and asserts
    every step refuses, nothing is recorded, and the reason never lands in learnings.md. Shared by [CP1-X06]'s
    two scenarios (a step that is not blocked at all, and a step already done)."""
    r_ask = g.rx(d, "ask", "raise-limit", "CP1")
    check(f"[CP1-X06] {label}: ask raise-limit is refused outright",
          r_ask.returncode != 0, r_ask.stdout + r_ask.stderr)
    g.say(d, "raise: just in case we need it later")
    r_cmd = g.rx(d, "raise-limit", "CP1")
    check(f"[CP1-X06] {label}: raise-limit itself is refused too",
          r_cmd.returncode != 0, r_cmd.stdout + r_cmd.stderr)
    st = json.loads((d / ".ratchet/runs/f/state.json").read_text())
    cap = (st.get("checkpoints", {}).get("CP1", {}).get("gates", {}).get("adversarial") or {}).get("cap_raised_to")
    check(f"[CP1-X06] {label}: no cap was recorded", cap is None, cap)
    learnings = (d / ".ratchet/learnings.md").read_text()
    check(f"[CP1-X06] {label}: nothing was appended to learnings.md",
          "just in case we need it later" not in learnings, learnings[-300:])


def t_x06_raise_limit_refused_when_not_blocked_on_its_own_round_limit():
    """[CP1-X06] `ask raise-limit`/`raise-limit` must refuse outright, before recording anything, unless the
    checkpoint is actually blocked on an adversarial round limit (`_adv_block_kind` "own" or "run_wide"): a
    cooperative agent calling it on a step still mid-build, or on a step already completed, must not be able to
    spend that step's one-and-only raise on a question whose fixed wording falsely claims the step is blocked."""
    # --- a fresh checkpoint still building: nothing is blocked, no adversarial round has ever opened ---
    d1 = g.project("x06building", cps=1)
    g.approve_plan(d1)
    _assert_raise_limit_refused(d1, "still building, never blocked")

    # --- a checkpoint that already passed review and is done: nothing is blocked any more either ---
    d2 = g.project("x06done", cps=1)
    g.approve_plan(d2)
    g.to_adversarial(d2, "CP1")
    rnd = g.open_round(d2, "CP1")
    paths = [g.write_review(d2, rnd, rv["id"], []) for rv in rnd["reviewers"]]
    adv = g.rx(d2, "adv-record", "CP1", "--reviews", *paths)
    assert adv.returncode == 0, adv.stdout + adv.stderr  # passes clean, no findings: existing, stable behaviour
    comp = g.rx(d2, "complete", "CP1")
    assert comp.returncode == 0, comp.stdout + comp.stderr  # existing, stable behaviour
    st2 = json.loads((d2 / ".ratchet/runs/f/state.json").read_text())
    assert st2["checkpoints"]["CP1"]["status"] == "done", st2["checkpoints"]["CP1"]  # setup sanity check
    _assert_raise_limit_refused(d2, "already done")


TESTS.append(("CP1-X06", t_x06_raise_limit_refused_when_not_blocked_on_its_own_round_limit))


def write_report():
    import os
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
