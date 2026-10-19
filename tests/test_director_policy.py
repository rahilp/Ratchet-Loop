#!/usr/bin/env python3
"""
CP6 tests: routine approvals follow the developer's policy.   python3 tests/test_director_policy.py

`director-policy` (director session only) shows (`--json` prints {"rules": {...}, "changes": [...]}), sets
(`--set <action>=<rule>`) and clears (`--clear <action>`) the developer's policy, kept per user in
RATCHET_HOME/director-policy.json next to the director key. Rules are a fixed menu: approve-changes=test-files,
raise-limit=max:N (N signed raises per run), choice=no-unlock, accept-finding=max:<severity> (minor or major).
With no policy file, `director-sign` behaves as before. With a policy file, `director-sign` (with or without
--apply) signs one of the four routine approvals only when a rule covers it, records the rule as `policy_rule` on
the log entry and in `receipt`, and otherwise refuses with a plain "ask the developer" message. Plan approval,
final approval and the other human-only approvals are never covered and work as before. A damaged policy file
refuses every routine approval. A builder session can neither use the command nor reach the file.

Fixtures reuse tests/test_director_apply.py, tests/test_director_sign_more.py, tests/test_accept_finding.py and
tests/test_limits.py (their lanes; tests/test_gates.py lanes share one RATCHET_HOME, so each case resets the policy
file first). The director runs from its own folder with RATCHET_ROLE=director and reaches a lane with --root.
No real reviewer CLI is called. Every case reports through check(), so a missing command is a clean fail.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP6-T01",
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
import _tmp                              # noqa: E402,F401
import test_gates as g                   # noqa: E402
import test_limits as tl                 # noqa: E402
import test_accept_finding as taf        # noqa: E402
import trust_helpers as th               # noqa: E402
import test_director_sign_more as m      # noqa: E402
import test_director_apply as da         # noqa: E402

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP6-[TX]\d+[a-z]?)\]")

REASON = "edge case worth one more look"
ACCEPT_REASON = "known limit, tracked for later"
TOKEN_LINE = re.compile(r"^[0-9a-f]{64}$", re.M)
ASK_DEV = re.compile(r"ask the developer", re.I)
POLICY_NAME = "director-policy.json"

F_MAJ = {"id": "F1", "severity": "major", "file": "src/f.js", "description": "logs the shopper's card number",
         "user_impact": "a shopper's card number is written to a log file other staff can read"}
F_MIN = {"id": "F2", "severity": "minor", "file": "src/f.js", "description": "error text is unclear",
         "user_impact": "a shopper sees a vague message when a coupon fails"}
F_BLK = {"id": "F3", "severity": "blocker", "file": "src/f.js", "description": "crashes when the cart total is null",
         "user_impact": "a shopper's checkout crashes and they lose their cart"}


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    mm = ID_RE.search(name)
    if mm:
        cid = mm.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ------------------------------------------------------------------------------------------------ helpers

out = m.out
state = m.state
adv = m.adv
events = m.events
pending = m.pending
raw = da.raw
ds = da.ds
last_line = da.last_line


def home(env=None) -> Path:
    return Path((env or g.ENV)["RATCHET_HOME"])


def pfile(env=None) -> Path:
    return home(env) / POLICY_NAME


def reset(env=None):
    """tests/test_gates.py lanes share one RATCHET_HOME: start each case with no policy file."""
    try:
        pfile(env).unlink()
    except FileNotFoundError:
        pass


def pol(df, d, *args, env=None):
    return m.director(df, d, "director-policy", *args, env=env)


def setrules(df, d, *specs, env=None):
    """Sets each rule; returns True only if every --set succeeded."""
    ok = True
    for s in specs:
        r = pol(df, d, "--set", s, env=env)
        ok = ok and r.returncode == 0
    return ok


def policy_json(df, d, env=None):
    r = pol(df, d, "--json", env=env)
    try:
        return json.loads(r.stdout)
    except Exception:
        return None


def finding_lane(name, findings, enrolled=True):
    """A one-step lane (risk normal, so every severity blocks) whose first review round holds `findings` open.
    Returns (d, env, df)."""
    env = th.make_env("claude")
    d = th.cp3_project(env, extra_cp={"risk": "normal"})
    th.cp3_pass_behavior(d, env)
    rnd = th.cp3_open_round(d, env)
    paths = []
    for i, rv in enumerate(rnd["reviewers"]):  # risk normal needs every listed reviewer; the first holds the findings
        paths.append(taf.write_round_review(d, env, rnd, rv["id"], findings if i == 0 else []))
    r = th.rx(d, env, "adv-record", "CP1", "--reviews", *paths)
    assert r.returncode == 2, out(r)
    df = m.enroll(d, None, env=env) if enrolled else None
    return d, env, df


def accepted(d, fid):
    e = taf.by_id(taf.deferred_findings(d, 1), fid)
    return bool(e) and e.get("accepted") is True


def rule_on_finding(d, env, fid):
    """The policy_rule recorded for finding fid: on its deferred entry, or on a finding_accepted log event naming it."""
    e = taf.by_id(taf.deferred_findings(d, 1), fid) or {}
    if e.get("policy_rule"):
        return e["policy_rule"]
    for ev in events(d, "finding_accepted"):
        if fid in (ev.get("ids") or []) and ev.get("policy_rule"):
            return ev["policy_rule"]
    return None


def choice_lane(name, extra=()):
    """Enrolled lane past red with a pending lettered question: A unlocks nothing, B unlocks tests/cp1.test.js."""
    d = g.project(name)
    g.approve_plan(d)
    da.to_red(d)
    r = g.rx(d, "ask", "choice", "--options", "A,B,C", "--locked", "B=tests/cp1.test.js", *extra)
    assert r.returncode == 0, out(r)
    return d, m.enroll(d)


def mixed_lane(name, kind):
    """A lane with a changed test file AND (kind='plan') an edited checkpoint in checkpoints.json or
    (kind='config') a changed config rule."""
    d, df = da.changed_lane(name)
    if kind == "plan":
        p = d / ".ratchet/runs/f/checkpoints.json"
        plan = json.loads(p.read_text())
        plan["checkpoints"][0]["title"] = "Step 1, quietly renamed"
        p.write_text(json.dumps(plan))
    else:
        cp = d / ".ratchet/config.json"
        cfg = json.loads(cp.read_text())
        cfg["max_adversarial_rounds"] = 9
        cp.write_text(json.dumps(cfg))
    return d, df


# ------------------------------------------------------------------------------------------------ T01

def cp6_t01_no_policy_file_means_today_behavior():
    d, env, df = finding_lane("t01", [F_MAJ])
    reset(env)
    v = pol(df, d, env=env)
    check("[CP6-T01] the policy view exits 0 and says no policy is set",
          v.returncode == 0 and re.search(r"no policy", out(v), re.I) is not None, out(v))
    check("[CP6-T01] looking at the policy creates no file", not pfile(env).exists(), "file exists")
    q = th.rx(d, env, "ask", "accept-finding", "CP1", "F1")
    sg = m.director(df, d, "director-sign", "accept-finding", env=env)
    tok = last_line(sg)
    check("[CP6-T01] setup: ask accept-finding works in the enrolled lane", q.returncode == 0, out(q))
    check("[CP6-T01] director-sign prints a token as before", sg.returncode == 0 and TOKEN_LINE.match(tok) is not None,
          out(sg))
    r = th.rx(d, env, "accept-finding", "CP1", "--director-token", tok or m.MADE_UP_TOKEN, "--reason", ACCEPT_REASON)
    check("[CP6-T01] applying the token accepts the finding", r.returncode == 0 and accepted(d, "F1"), out(r))
    check("[CP6-T01] no policy_rule is recorded anywhere in the log",
          all("policy_rule" not in e for e in state(d).get("history") or []), events(d, "finding_accepted"))
    check("[CP6-T01] the policy file still does not exist", not pfile(env).exists(), "file exists")


# ------------------------------------------------------------------------------------------------ T02

def cp6_t02_set_show_clear_and_log():
    d, df = da.building_lane("t02")
    reset()
    before = subprocess.run(["git", "status", "--porcelain"], cwd=d, capture_output=True, text=True).stdout
    v0 = pol(df, d)
    check("[CP6-T02] with nothing set, the view says no policy", v0.returncode == 0
          and re.search(r"no policy", out(v0), re.I) is not None, out(v0))
    okset = setrules(df, d, "approve-changes=test-files", "raise-limit=max:2", "choice=no-unlock",
                     "accept-finding=max:minor")
    check("[CP6-T02] all four --set commands exit 0", okset, "a --set failed")
    v1 = pol(df, d)
    check("[CP6-T02] the view lists the four rules in force",
          all(s in out(v1) for s in ("approve-changes", "test-files", "raise-limit", "max:2", "choice", "no-unlock",
                                     "accept-finding", "max:minor")), out(v1))
    cl = pol(df, d, "--clear", "raise-limit")
    check("[CP6-T02] --clear raise-limit exits 0", cl.returncode == 0, out(cl))
    v2 = pol(df, d)
    check("[CP6-T02] the view no longer lists raise-limit or max:2, but still lists the other three",
          "max:2" not in out(v2) and "raise-limit" not in out(v2)
          and all(s in out(v2) for s in ("test-files", "no-unlock", "max:minor")), out(v2))
    data = policy_json(df, d)
    check("[CP6-T02] --json prints rules and changes",
          isinstance(data, dict) and "rules" in data and "changes" in data, data)
    data = data or {"rules": None, "changes": []}
    check("[CP6-T02] --json rules are exactly the three still in force",
          data["rules"] == {"approve-changes": "test-files", "choice": "no-unlock", "accept-finding": "max:minor"},
          data["rules"])
    ch = data["changes"] if isinstance(data["changes"], list) else []
    check("[CP6-T02] the change log has five entries, each with a time and the action",
          len(ch) == 5 and all(isinstance(c, dict) and str(c.get("at") or c.get("time") or "").strip()
                               and c.get("action") for c in ch), ch)
    sets = [c for c in ch if "clear" not in json.dumps(c).lower()]
    clears = [c for c in ch if "clear" in json.dumps(c).lower()]
    check("[CP6-T02] four of them are sets carrying the rule, one is the clear of raise-limit",
          len(sets) == 4 and all(c.get("rule") for c in sets) and len(clears) == 1
          and clears[0].get("action") == "raise-limit", ch)
    check("[CP6-T02] the sets are in the order made, the second being raise-limit max:2",
          len(sets) == 4 and (sets[1].get("action"), sets[1].get("rule")) == ("raise-limit", "max:2"), sets)
    check("[CP6-T02] the file is director-policy.json next to the director key",
          pfile().exists() and (home() / "director.key").exists(), str(pfile()))
    after = subprocess.run(["git", "status", "--porcelain"], cwd=d, capture_output=True, text=True).stdout
    check("[CP6-T02] the project's git status is unchanged and holds no policy file",
          before == after and not list(Path(d).rglob(POLICY_NAME)), (before, after))


# ------------------------------------------------------------------------------------------------ T03

PY_TEST = (
    "import sys\n"
    "from pathlib import Path\n"
    "\n"
    "# the feature lives in src/f.js\n"
    "def test_cp1_t01_works():  # [CP1-T01] works\n"
    "    src = Path('src/f.js')\n"
    "    assert src.exists() and '=> 1' in src.read_text()\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    try:\n"
    "        test_cp1_t01_works()\n"
    "    except AssertionError:\n"
    "        sys.exit(1)\n"
)


def comment_only_python_change_lane(name):
    """A lane whose planned tests are a Python file; after the red run only a comment line changes (AST equal)."""
    d = g.project(name, extra_cp={"tests": {"files": ["tests/test_cp1.py"], "command": "python3 tests/test_cp1.py"}})
    g.approve_plan(d)
    tf = Path(d) / "tests/test_cp1.py"
    tf.write_text(PY_TEST)
    assert g.rx(d, "tests-written", "CP1").returncode == 0
    assert g.rx(d, "red", "CP1").returncode == 0
    tf.write_text(PY_TEST.replace("# the feature lives in src/f.js", "# where the feature lives: src/f.js"))
    return d, da.m.enroll(d, None)


def cp6_t03_test_file_only_change_follows_the_rule():
    d, df = comment_only_python_change_lane("t03")
    reset()
    check("[CP6-T03] setup: the policy rule is set", setrules(df, d, "approve-changes=test-files"), "set failed")
    before = state(d)
    r = ds(df, d, "approve-changes")
    check("[CP6-T03] director-sign approve-changes --apply exits 0", r.returncode == 0, out(r))
    check("[CP6-T03] the approved baseline moved", state(d).get("approved") != before.get("approved"), "unchanged")
    ev = events(d, "changes_approved")
    check("[CP6-T03] one changes_approved event by the director with policy_rule 'approve-changes: test-files'",
          len(ev) == 1 and ev[0].get("by") == "director" and ev[0].get("policy_rule") == "approve-changes: test-files", ev)
    rc = g.rx(d, "receipt")
    check("[CP6-T03] receipt shows that rule", rc.returncode == 0 and "approve-changes: test-files" in rc.stdout, out(rc))
    da.make_impl(d)
    nxt = g.rx(d, "implemented", "CP1")
    check("[CP6-T03] the lane carries on", nxt.returncode == 0, out(nxt))


# ------------------------------------------------------------------------------------------------ T04

def cp6_t04_mixed_change_is_refused_under_the_rule():
    for kind, named in (("plan", r"checkpoints\.json|plan"), ("config", r"config|rule")):
        d, df = mixed_lane(f"t04{kind}", kind)
        reset()
        pre = g.rx(d, "implemented", "CP1")
        check(f"[CP6-T04] {kind}: setup: the lane lists changes (the build is refused until they are approved)",
              pre.returncode != 0 and re.search(r"changed after|approve-changes", out(pre)) is not None, out(pre))
        check(f"[CP6-T04] {kind}: setup: the policy rule is set", setrules(df, d, "approve-changes=test-files"), "")
        before = raw(d)
        r = ds(df, d, "approve-changes")
        check(f"[CP6-T04] {kind}: refused", r.returncode != 0, out(r))
        check(f"[CP6-T04] {kind}: the message names the non-test item and says to ask the developer",
              re.search(named, out(r), re.I) is not None and ASK_DEV.search(out(r)) is not None
              and "usage:" not in out(r), out(r))
        check(f"[CP6-T04] {kind}: baseline, log and approval stay unchanged", raw(d) == before
              and not events(d, "changes_approved"), events(d, "changes_approved"))


# ------------------------------------------------------------------------------------------------ T05

def cp6_t05_raise_limit_counts_per_run():
    d, df = m.enrolled_cap_lane("t05", cps=2)
    reset()
    check("[CP6-T05] setup: raise-limit=max:1 is set", setrules(df, d, "raise-limit=max:1"), "")
    r1 = ds(df, d, "raise-limit", "CP1", "--reason", REASON)
    check("[CP6-T05] the first raise is signed and applied (exit 0, one more round)",
          r1.returncode == 0 and adv(d, "CP1").get("cap_raised_to") == 3, out(r1))
    ev = events(d, "round_limit_raised")
    check("[CP6-T05] its log entry records policy_rule 'raise-limit: max:1 (1 of 1)'",
          len(ev) == 1 and ev[0].get("policy_rule") == "raise-limit: max:1 (1 of 1)", ev)
    # let CP1 finish, then block CP2 at its own review limit
    ready = False
    try:
        r2 = tl._reviewers_after_triage(d, "CP1")
        rnd = json.loads(r2.stdout)
        p = g.write_review(d, rnd, g.R0(rnd), [])
        ok1 = g.rx(d, "adv-record", "CP1", "--reviews", p).returncode == 0
        ok2 = ok1 and g.rx(d, "complete", "CP1").returncode == 0
        if ok2:
            tl._to_adversarial_own_source(d, "CP2", "g", "src/g.js")
            tl._drive_to_cap(d, "CP2")
            ready = state(d).get("phase") == "blocked" and state(d).get("current") == "CP2"
    except Exception as e:
        print(f"    setup crashed: {e}")
    check("[CP6-T05] setup: CP1 finished and CP2 is blocked at its own review limit", ready, state(d).get("phase"))
    if not ready:
        return
    before_raw = raw(d)
    total = state(d).get("adv_rounds_total")
    r3 = ds(df, d, "raise-limit", "CP2", "--reason", REASON)
    check("[CP6-T05] the second raise (another step, same run) is refused", r3.returncode != 0, out(r3))
    check("[CP6-T05] the refusal names the used-up rule and says to ask the developer",
          re.search(r"raise-limit", out(r3)) is not None and re.search(r"max:1", out(r3)) is not None
          and ASK_DEV.search(out(r3)) is not None and "usage:" not in out(r3), out(r3))
    check("[CP6-T05] CP2 is not raised and the run stays blocked",
          adv(d, "CP2").get("cap_raised_to") is None and state(d).get("phase") == "blocked", adv(d, "CP2"))
    check("[CP6-T05] the run-wide review budget is untouched", state(d).get("adv_rounds_total") == total
          and raw(d) == before_raw, (total, state(d).get("adv_rounds_total")))


# ------------------------------------------------------------------------------------------------ T06

def cp6_t06_choice_that_unlocks_nothing():
    d, df = choice_lane("t06a")
    reset()
    check("[CP6-T06] setup: choice=no-unlock is set", setrules(df, d, "choice=no-unlock"), "")
    r = ds(df, d, "choice", "A")
    check("[CP6-T06] option A (unlocks nothing) is signed and applied", r.returncode == 0, out(r))
    ev = events(d, "choice_made")
    check("[CP6-T06] choice_made records letter A, no file, by director, policy_rule 'choice: no-unlock'",
          len(ev) == 1 and ev[0].get("letter") == "A" and not ev[0].get("approves") and ev[0].get("by") == "director"
          and ev[0].get("policy_rule") == "choice: no-unlock", ev)
    check("[CP6-T06] the question is cleared", not pending(d), pending(d))
    # B unlocks a locked file
    d2, df2 = choice_lane("t06b")
    before = raw(d2)
    rb = ds(df2, d2, "choice", "B")
    check("[CP6-T06] option B (unlocks tests/cp1.test.js) is refused with a message to ask the developer",
          rb.returncode != 0 and ASK_DEV.search(out(rb)) is not None and "usage:" not in out(rb), out(rb))
    check("[CP6-T06] B changes nothing: no choice_made, no choice_allow, question still pending",
          raw(d2) == before and not events(d2, "choice_made") and not state(d2).get("choice_allow")
          and bool(pending(d2)), pending(d2))
    da.weaken(d2)
    ra = g.rx(d2, "approve-changes")
    check("[CP6-T06] the locked file did not become editable (approve-changes still needs a real approval)",
          not (ra.returncode == 0 and "Accepted" in ra.stdout), out(ra))
    # an option that carries a guardrail
    d3, df3 = choice_lane("t06c", extra=("--guardrail", "C=repeated-finding"))
    before3 = raw(d3)
    rc = ds(df3, d3, "choice", "C")
    check("[CP6-T06] an option that carries a guardrail is refused the same way",
          rc.returncode != 0 and ASK_DEV.search(out(rc)) is not None and raw(d3) == before3
          and not events(d3, "choice_made"), out(rc))


# ------------------------------------------------------------------------------------------------ T07

def cp6_t07_accept_finding_up_to_severity():
    d, env, df = finding_lane("t07", [F_MAJ, F_MIN])
    reset(env)
    check("[CP6-T07] setup: accept-finding=max:minor is set", setrules(df, d, "accept-finding=max:minor", env=env), "")
    before = raw(d)
    r0 = ds(df, d, "accept-finding", "CP1", "--ids", "F1", "--reason", ACCEPT_REASON, env=env)
    check("[CP6-T07] at max:minor, accepting the major F1 is refused with 'ask the developer'",
          r0.returncode != 0 and ASK_DEV.search(out(r0)) is not None and "usage:" not in out(r0), out(r0))
    check("[CP6-T07] nothing was accepted or changed", raw(d) == before and not accepted(d, "F1"), "state changed")
    check("[CP6-T07] setup: the policy is moved to max:major", setrules(df, d, "accept-finding=max:major", env=env), "")
    r = ds(df, d, "accept-finding", "CP1", "--ids", "F1,F2", "--reason", ACCEPT_REASON, env=env)
    check("[CP6-T07] at max:major, accepting F1 and F2 exits 0", r.returncode == 0, out(r))
    for fid in ("F1", "F2"):
        e = taf.by_id(taf.deferred_findings(d, 1), fid) or {}
        check(f"[CP6-T07] {fid} is accepted by the director",
              e.get("accepted") is True and e.get("accepted_by") == "director", e)
        check(f"[CP6-T07] {fid} carries policy_rule 'accept-finding: max:major'",
              rule_on_finding(d, env, fid) == "accept-finding: max:major", rule_on_finding(d, env, fid))


# ------------------------------------------------------------------------------------------------ T08

def cp6_t08_one_blocker_refuses_all():
    d, env, df = finding_lane("t08", [F_MAJ, F_BLK])
    reset(env)
    check("[CP6-T08] setup: accept-finding=max:major is set", setrules(df, d, "accept-finding=max:major", env=env), "")
    q = th.rx(d, env, "ask", "accept-finding", "CP1", "F1,F3")
    check("[CP6-T08] setup: the question is open", q.returncode == 0 and bool(pending(d)), out(q))
    before_pending = pending(d)
    before = raw(d)
    r = ds(df, d, "accept-finding", "CP1", "--ids", "F1,F3", "--reason", ACCEPT_REASON, env=env)
    check("[CP6-T08] refused, the message names F3 and says to ask the developer",
          r.returncode != 0 and "F3" in out(r) and ASK_DEV.search(out(r)) is not None and "usage:" not in out(r), out(r))
    check("[CP6-T08] neither finding is accepted", not accepted(d, "F1") and not accepted(d, "F3"), "accepted")
    check("[CP6-T08] the question is still open and the state is unchanged",
          pending(d) == before_pending and raw(d) == before, pending(d))


# ------------------------------------------------------------------------------------------------ T09

def cp6_t09_uncovered_routine_approval_is_refused():
    d, env, df = finding_lane("t09", [F_MAJ])
    reset(env)
    check("[CP6-T09] setup: only raise-limit=max:1 is set", setrules(df, d, "raise-limit=max:1", env=env), "")
    th.rx(d, env, "ask", "accept-finding", "CP1", "F1")
    before = raw(d)
    r = ds(df, d, "accept-finding", "CP1", "--ids", "F1", "--reason", ACCEPT_REASON, env=env)
    check("[CP6-T09] refused with no token printed",
          r.returncode != 0 and TOKEN_LINE.search(r.stdout) is None, out(r))
    check("[CP6-T09] the message names the missing accept-finding rule and says to ask the developer",
          re.search(r"accept-finding", out(r)) is not None and ASK_DEV.search(out(r)) is not None
          and "usage:" not in out(r), out(r))
    check("[CP6-T09] nothing was accepted", not accepted(d, "F1") and raw(d) == before, "changed")


# ------------------------------------------------------------------------------------------------ T10

def cp6_t10_plain_token_path_follows_the_policy():
    d, df = choice_lane("t10")
    reset()
    check("[CP6-T10] setup: a policy without a choice rule", setrules(df, d, "raise-limit=max:1"), "")
    before = raw(d)
    plain = m.director(df, d, "director-sign", "choice", "A")
    app = ds(df, d, "choice", "A")
    check("[CP6-T10] director-sign choice A (no --apply) is refused, no token printed",
          plain.returncode != 0 and TOKEN_LINE.search(plain.stdout) is None, out(plain))
    check("[CP6-T10] the refusal says to ask the developer and names the choice rule",
          ASK_DEV.search(out(plain)) is not None and re.search(r"choice", out(plain)) is not None
          and "usage:" not in out(plain), out(plain))
    check("[CP6-T10] --apply is refused the same way", app.returncode != 0 and ASK_DEV.search(out(app)) is not None,
          out(app))
    check("[CP6-T10] the lane is unchanged", raw(d) == before and not events(d, "choice_made"), "changed")


# ------------------------------------------------------------------------------------------------ T11

def cp6_t11_human_only_rules_are_refused():
    d, df = da.building_lane("t11")
    reset()
    check("[CP6-T11] setup: one valid rule is set", setrules(df, d, "choice=no-unlock"), "")
    snap = pfile().read_bytes() if pfile().exists() else b""
    bad = ["approve-plan=anything", "human-approve=anything", "feedback=anything", "cancel=anything",
           "set-review=anything", "hooks-waive=anything", "ui-respec=anything", "paid-provider=anything",
           "bogus-action=anything", "accept-finding=max:blocker", "raise-limit=max:0", "raise-limit=max:abc",
           "approve-changes=everything", "choice=any"]
    for spec in bad:
        r = pol(df, d, "--set", spec)
        check(f"[CP6-T11] --set {spec} is refused with a plain message",
              r.returncode != 0 and out(r).strip() != "" and "usage:" not in out(r)
              and "Traceback" not in out(r), out(r))
    after = pfile().read_bytes() if pfile().exists() else b""
    check("[CP6-T11] the policy file is byte-for-byte unchanged and no change was logged",
          bool(snap) and snap == after, (snap, after))
    data = policy_json(df, d) or {}
    check("[CP6-T11] the log still holds only the one valid set", len(data.get("changes") or []) == 1, data)


# ------------------------------------------------------------------------------------------------ T12

def cp6_t12_other_approvals_unaffected_by_a_policy():
    allfour = ("approve-changes=test-files", "raise-limit=max:2", "choice=no-unlock", "accept-finding=max:minor")
    d, df = da.plan_lane("t12a")
    reset()
    check("[CP6-T12] setup: all four rules are set", setrules(df, d, *allfour), "")
    sp = m.director(df, d, "director-sign", "approve-plan")
    tok = last_line(sp)
    check("[CP6-T12] director-sign approve-plan prints a token as without a policy",
          sp.returncode == 0 and TOKEN_LINE.match(tok) is not None, out(sp))
    rp = g.rx(d, "approve-plan", "--director-token", tok or m.MADE_UP_TOKEN)
    check("[CP6-T12] the plan is approved with no policy_rule on the log",
          rp.returncode == 0 and state(d).get("phase") == "building"
          and all("policy_rule" not in e for e in state(d).get("history") or []), out(rp))
    env = th.make_env("claude")
    dd = th.cp4_awaiting_review(env)
    dfd = m.enroll(dd, None, env=env)
    check("[CP6-T12] setup: all four rules are set for the final-review lane",
          setrules(dfd, dd, *allfour, env=env), "")
    sg = m.director(dfd, dd, "director-sign", "human-approve", env=env)
    tk = last_line(sg)
    check("[CP6-T12] director-sign human-approve prints a token as without a policy",
          sg.returncode == 0 and TOKEN_LINE.match(tk) is not None, out(sg))
    rh = th.rx(dd, env, "human-approve", "--director-token", tk or m.MADE_UP_TOKEN)
    check("[CP6-T12] the run is done and no entry carries a policy_rule",
          rh.returncode == 0 and state(dd).get("phase") == "done"
          and all("policy_rule" not in e for e in state(dd).get("history") or []), out(rh))
    # an empty policy file with no rules does not block them either
    d2, df2 = da.plan_lane("t12c")
    reset()
    check("[CP6-T12] setup: a policy with one unrelated rule", setrules(df2, d2, "raise-limit=max:1"), "")
    s2 = m.director(df2, d2, "director-sign", "approve-plan")
    check("[CP6-T12] the policy neither grants nor blocks plan approval",
          s2.returncode == 0 and TOKEN_LINE.match(last_line(s2)) is not None, out(s2))


# ------------------------------------------------------------------------------------------------ T13

def cp6_t13_builder_cannot_read_or_change_the_policy():
    d, df = da.building_lane("t13")
    reset()
    check("[CP6-T13] setup: a policy exists", setrules(df, d, "choice=no-unlock"), "")
    snap = pfile().read_bytes() if pfile().exists() else b""
    for label, args in (("show", []), ("set", ["--set", "accept-finding=max:major"]),
                        ("clear", ["--clear", "choice"])):
        r = m.not_director(df, d, "director-policy", *args)
        check(f"[CP6-T13] a builder session's director-policy ({label}) is refused as director-only",
              r.returncode != 0 and re.search(r"director", out(r), re.I) is not None and "usage:" not in out(r)
              and "no-unlock" not in out(r), out(r))
    benv = {k: v for k, v in g.ENV.items() if k != "RATCHET_ROLE"}
    p = str(pfile())
    for label, tool, inp in (("cat", "Bash", {"command": f"cat {p}"}),
                             ("redirect", "Bash", {"command": f"echo '{{}}' > {p}"}),
                             ("edit", "Edit", {"file_path": p, "old_string": "no-unlock", "new_string": "x"}),
                             ("write", "Write", {"file_path": p, "content": "{}"})):
        gr = th.cp4x_guard_tool(df, benv, tool, inp, session="s-builder")
        check(f"[CP6-T13] the guard blocks a builder's {label} of the policy file", gr.returncode == 2, out(gr))
    check("[CP6-T13] the file's bytes are unchanged", bool(snap) and pfile().read_bytes() == snap, "changed")
    sg = m.not_director(df, d, "director-sign", "approve-changes", "--apply")
    check("[CP6-T13] director-sign by the builder is still refused, no token printed",
          sg.returncode != 0 and TOKEN_LINE.search(sg.stdout) is None, out(sg))


# ------------------------------------------------------------------------------------------------ T14

def cp6_t14_damaged_policy_fails_closed():
    d, df = m.enrolled_cap_lane("t14")
    reset()
    pfile().write_text("this is not policy json {{{")
    try:
        before = raw(d)
        r = ds(df, d, "raise-limit", "CP1", "--reason", REASON)
        check("[CP6-T14] raise-limit --apply is refused", r.returncode != 0, out(r))
        check("[CP6-T14] the message says the policy file is unreadable and the developer must reset it",
              re.search(r"unreadable|damaged|corrupt|not valid", out(r), re.I) is not None
              and POLICY_NAME in out(r) and re.search(r"developer", out(r), re.I) is not None
              and "usage:" not in out(r), out(r))
        check("[CP6-T14] nothing was raised or signed",
              adv(d).get("cap_raised_to") is None and raw(d) == before and not events(d, "round_limit_raised"),
              adv(d))
        dc, dfc = da.choice_lane("t14c")
        pfile().write_text("this is not policy json {{{")
        pl = m.director(dfc, dc, "director-sign", "choice", "A")
        check("[CP6-T14] the plain token path is refused too, with no token",
              pl.returncode != 0 and TOKEN_LINE.search(pl.stdout) is None, out(pl))
    finally:
        reset()


# ------------------------------------------------------------------------------------------------ T15

def cp6_t15_receipt_tells_policy_and_chat_approvals_apart():
    d, env, df = finding_lane("t15a", [F_MAJ])
    reset(env)
    check("[CP6-T15] setup: accept-finding=max:major is set", setrules(df, d, "accept-finding=max:major", env=env), "")
    r = ds(df, d, "accept-finding", "CP1", "--ids", "F1", "--reason", ACCEPT_REASON, env=env)
    check("[CP6-T15] setup: the policy run accepted F1", r.returncode == 0 and accepted(d, "F1"), out(r))
    rc = th.rx(d, env, "receipt")
    check("[CP6-T15] the policy run's receipt lists the rule text and the director as accepter",
          rc.returncode == 0 and "accept-finding: max:major" in rc.stdout and "accepted by: director" in rc.stdout,
          out(rc))
    rj = th.rx(d, env, "receipt", "--json")
    check("[CP6-T15] receipt --json carries the rule too", "accept-finding: max:major" in rj.stdout, rj.stdout[-300:])
    # a run whose finding the developer accepted by chat reply
    d2, env2, _ = finding_lane("t15b", [F_MAJ], enrolled=False)
    tp = Path(d2) / "t.jsonl"
    q = th.rx(d2, env2, "ask", "accept-finding", "CP1", "F1")
    taf.reply(d2, tp, env2, "accept: we'll live with this for the beta")
    a = th.rx(d2, env2, "accept-finding", "CP1")
    check("[CP6-T15] setup: the chat run accepted F1 by the developer's reply",
          q.returncode == 0 and a.returncode == 0 and accepted(d2, "F1"), out(q) + out(a))
    rc2 = th.rx(d2, env2, "receipt")
    check("[CP6-T15] the chat run's receipt shows no rule and labels the acceptance as the human's",
          rc2.returncode == 0 and "accepted by: human" in rc2.stdout and "max:" not in rc2.stdout
          and "policy" not in rc2.stdout.lower(), out(rc2))


# ------------------------------------------------------------------------------------------------ T16

def _windows(text, needle, width=1800):
    return " ".join(text[max(0, mm.start() - width): mm.end() + width] for mm in re.finditer(re.escape(needle), text))


def cp6_t16_docs_describe_the_policy():
    docs = {"README.md": ROOT / "README.md",
            "SKILL.md": ROOT / "skills/ratchet-orchestrator/SKILL.md",
            "schemas.md": ROOT / "skills/ratchet-orchestrator/references/schemas.md"}
    never = (("plan approval", r"plan approval|approve-plan|plan"), ("scope", r"scope|feedback"),
             ("final approval", r"final approval|human-approve"), ("cancel", r"cancel"),
             ("review settings", r"review settings|set-review|review mode"),
             ("hooks waivers", r"hooks?[- ]waive|waivers?"), ("paid providers", r"paid provider"))
    for name, path in docs.items():
        text = path.read_text()
        near = _windows(text, "director-policy")
        check(f"[CP6-T16] {name} mentions the director-policy command", "director-policy" in text, "")
        check(f"[CP6-T16] {name} names the per-user file director-policy.json and the director key beside it",
              POLICY_NAME in text and re.search(r"director key|director\.key", near) is not None, "")
        check(f"[CP6-T16] {name} lists the four rules",
              all(s in near for s in ("test-files", "max:", "no-unlock")) and "accept-finding" in near
              and "approve-changes" in near and "raise-limit" in near, "")
        for label, rx_ in never:
            check(f"[CP6-T16] {name} says a policy can never cover {label}",
                  re.search(rx_, near, re.I) is not None, "")
        check(f"[CP6-T16] {name} mentions policy_rule", "policy_rule" in text, "")
    sch = docs["schemas.md"].read_text()
    sec9 = re.search(r"(?ms)^#+\s*9\b.*?(?=^#+\s*10\b|\Z)", sch)
    check("[CP6-T16] schemas.md section 9 lists policy_rule on the log entry",
          bool(sec9) and "policy_rule" in sec9.group(0), "")


# ------------------------------------------------------------------------------------------------ harness

TESTS = [
    ("CP6-T01", cp6_t01_no_policy_file_means_today_behavior),
    ("CP6-T02", cp6_t02_set_show_clear_and_log),
    ("CP6-T03", cp6_t03_test_file_only_change_follows_the_rule),
    ("CP6-T04", cp6_t04_mixed_change_is_refused_under_the_rule),
    ("CP6-T05", cp6_t05_raise_limit_counts_per_run),
    ("CP6-T06", cp6_t06_choice_that_unlocks_nothing),
    ("CP6-T07", cp6_t07_accept_finding_up_to_severity),
    ("CP6-T08", cp6_t08_one_blocker_refuses_all),
    ("CP6-T09", cp6_t09_uncovered_routine_approval_is_refused),
    ("CP6-T10", cp6_t10_plain_token_path_follows_the_policy),
    ("CP6-T11", cp6_t11_human_only_rules_are_refused),
    ("CP6-T12", cp6_t12_other_approvals_unaffected_by_a_policy),
    ("CP6-T13", cp6_t13_builder_cannot_read_or_change_the_policy),
    ("CP6-T14", cp6_t14_damaged_policy_fails_closed),
    ("CP6-T15", cp6_t15_receipt_tells_policy_and_chat_approvals_apart),
    ("CP6-T16", cp6_t16_docs_describe_the_policy),
]


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
    global FAIL
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        print(f"\n{cid}: {fn.__name__}")
        try:
            fn()
        except Exception as e:  # a crash still counts as a failed result for this case, never a silent drop
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
