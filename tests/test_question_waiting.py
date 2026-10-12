#!/usr/bin/env python3
"""
CP1 tests: status, status --json and next say plainly when a question is waiting for an answer.
python3 tests/test_question_waiting.py

When an agent has put a question to the user (or, in a director-enrolled lane, to the director) with
`ratchet.py ask ...` and no answer has come yet, a lane must never look like it is still building. These tests
drive the repo's own ratchet.py in throwaway git projects (like tests/test_gates.py, tests/test_accept_finding.py
and tests/test_limits.py) and pin what an agent reads:

  - plain `status` gains exactly one extra line saying the run waits for an answer, naming the kind of question
    in words that fit it, and giving the time it was asked (matched by loose regexes, not a fixed sentence);
  - `status --json` and `next` carry the same `question` entry: {kind, since, waiting_on, about}, where kind is
    the `ask` action name, since is exactly pending.asked_at from state.json, waiting_on is 'user' or 'director',
    and about is the step id for accept-finding / raise-limit / ui-respec. The entry is null (or absent) when no
    question is waiting: nothing asked yet, answered, or withdrawn;
  - reading status or next never changes the question or the run (state.json stays byte-identical);
  - the README, the orchestrator skill and schemas.md describe the line and the entry.

state.json is only ever READ here (to learn pending.asked_at); every change goes through ratchet.py commands.
No sleeps and no real reviewer CLI: each project runs with providers=[].

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP1-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "skills" / "ratchet-orchestrator" / "scripts"))
import _tmp  # noqa: E402

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP1-[TX]\d+[a-z]?)\]")
TESTS: list = []

README = ROOT / "README.md"
SKILL = ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md"
SCHEMAS = ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md"


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


# ------------------------------------------------------------------------------------------------ helpers

# Imported lazily so that a harness problem in a helper module shows up as a crash of the one case that needs it.
def _g():
    import test_gates as g
    return g


def _th():
    import trust_helpers as th
    return th


def _taf():
    import test_accept_finding as taf
    return taf


ENVS: dict[str, dict] = {}  # project folder -> the env its commands run with (projects built by trust_helpers)


def rx(d, *args):
    """Run the repo's ratchet.py in project d with the env that project was built with."""
    g = _g()
    return g.rx(d, *args, env=ENVS.get(str(d), g.ENV))


def out_of(r):
    return r.stdout + r.stderr


def state_path(d):
    return Path(d) / ".ratchet" / "runs" / "f" / "state.json"


def state_bytes(d):
    return state_path(d).read_bytes()


def pending(d):
    return json.loads(state_path(d).read_text()).get("pending")


def asked_at(d):
    p = pending(d)
    return p.get("asked_at") if p else None


def status_lines(d):
    r = rx(d, "status")
    assert r.returncode == 0, out_of(r)
    # The "Live view: file://<TMPDIR>/..." line is a path, not status's own words; a TMPDIR named after this suite would match.
    return [ln for ln in r.stdout.splitlines() if not ln.lstrip().startswith("Live view:")]


def status_json(d):
    r = rx(d, "status", "--json")
    assert r.returncode == 0, out_of(r)
    return json.loads(r.stdout)


def next_json(d):
    r = rx(d, "next")
    assert r.returncode == 0, out_of(r)
    return json.loads(r.stdout)


def question_of(obj):
    """The `question` entry of a status --json / next object, or None when it is absent or null."""
    return obj.get("question")


def waiting_lines(lines):
    """The status lines that say the run waits for an answer (they mention waiting and an answer)."""
    return [ln for ln in lines if re.search(r"wait", ln, re.I) and re.search(r"answer", ln, re.I)]


def mentions_question_at_all(lines):
    return [ln for ln in lines if re.search(r"answer|question", ln, re.I)]


def _time_forms(iso):
    """Plausible renderings of the HH:MM and the date of an ISO instant: in UTC, in the machine's local zone and
    in US Eastern (the three zones a status line could reasonably show), so the check does not pin a zone."""
    t = dt.datetime.fromisoformat(iso)
    zones = [dt.timezone.utc, t.astimezone().tzinfo]
    try:
        from zoneinfo import ZoneInfo
        zones.append(ZoneInfo("America/New_York"))
    except Exception:
        pass
    hhmm, dates = set(), set()
    for z in zones:
        x = t.astimezone(z)
        hhmm.add(x.strftime("%H:%M"))
        dates.add(x.strftime("%Y-%m-%d"))
        for mon in (x.strftime("%b"), x.strftime("%B")):
            dates.add(f"{mon} {x.day}")
            dates.add(f"{x.day} {mon}")
            dates.add(f"{mon} {x.day:02d}")
    return hhmm, dates


def carries_time(line, iso):
    """The line shows the date and the HH:MM at which the question was asked (loose: any common rendering)."""
    hhmm, dates = _time_forms(iso)
    return any(h in line for h in hhmm) and any(x in line for x in dates)


def plan_submitted_project(name):
    """A one-step project whose plan is submitted but not approved (phase awaiting_plan_approval)."""
    return _g().project(name)


def building_project(name):
    """The same project with the plan approved (phase building, CP1 at its tests step)."""
    g = _g()
    d = g.project(name)
    g.approve_plan(d)
    return d


def reply_yes(d, text="yes, go ahead"):
    _g().say(d, text)


def blocked_round_project(name, host="claude"):
    """A step whose first review round left F1 and F2 open (the fixture of tests/test_accept_finding.py).
    Returns (d, env)."""
    taf = _taf()
    if host == "claude":
        d, env, tp, rnd, rid = taf.blocked_round(name)
    else:
        d, env, tp, rnd, rid = taf._blocked_round_unconfirmed(name)
    ENVS[str(d)] = env
    return d, env


def state_f(d):
    """state.json path for projects built by trust_helpers (slug 'f' too)."""
    return state_path(d)


# ------------------------------------------------------------------------------------------------ CP1-T01

def t01_plan_question_shows_in_status_status_json_and_next():
    d = plan_submitted_project("t01")
    before = status_lines(d)
    r = rx(d, "ask", "approve-plan")
    check("[CP1-T01] (setup) `ask approve-plan` is accepted", r.returncode == 0, out_of(r))
    since = asked_at(d)
    check("[CP1-T01] (setup) state.json records pending.asked_at", bool(since), since)
    after = status_lines(d)
    new = [ln for ln in after if ln not in before]
    wl = waiting_lines(new)
    check("[CP1-T01] plain status gains exactly one line saying the run waits for the user's answer",
          len(wl) == 1, ("new lines:", new))
    line = wl[0] if wl else ""
    check("[CP1-T01] that line names the plan question", bool(re.search(r"plan", line, re.I)), line)
    check("[CP1-T01] that line gives the time the question was asked (date and HH:MM)",
          bool(since) and carries_time(line, since), (line, since))
    sj, nx = status_json(d), next_json(d)
    q_s, q_n = question_of(sj), question_of(nx)
    check("[CP1-T01] status --json has a question entry of kind 'approve-plan'",
          isinstance(q_s, dict) and q_s.get("kind") == "approve-plan", q_s)
    check("[CP1-T01] question.since is exactly pending.asked_at",
          isinstance(q_s, dict) and q_s.get("since") == since, (q_s, since))
    check("[CP1-T01] question.waiting_on is 'user'", isinstance(q_s, dict) and q_s.get("waiting_on") == "user", q_s)
    check("[CP1-T01] question has an 'about' key", isinstance(q_s, dict) and "about" in q_s, q_s)
    check("[CP1-T01] next carries the same question entry as status --json",
          isinstance(q_n, dict) and q_n == q_s, (q_n, q_s))


TESTS.append(("CP1-T01", t01_plan_question_shows_in_status_status_json_and_next))


# ------------------------------------------------------------------------------------------------ CP1-T02

def t02_question_shows_while_phase_still_reads_building():
    d = building_project("t02")
    before = status_lines(d)
    sj0 = status_json(d)
    r = rx(d, "ask", "set-review", "each")
    check("[CP1-T02] (setup) `ask set-review each` is accepted", r.returncode == 0, out_of(r))
    sj, nx = status_json(d), next_json(d)
    check("[CP1-T02] phase still reads 'building' (the new entry is additive)", sj.get("phase") == "building",
          sj.get("phase"))
    check("[CP1-T02] the existing waiting_on is still null", sj.get("waiting_on") is None and
          sj0.get("waiting_on") is None, (sj0.get("waiting_on"), sj.get("waiting_on")))
    new = [ln for ln in status_lines(d) if ln not in before]
    wl = waiting_lines(new)
    check("[CP1-T02] plain status has a new line saying a question is waiting for an answer",
          len(wl) == 1, ("new lines:", new))
    check("[CP1-T02] that line names the review-frequency question",
          bool(wl) and bool(re.search(r"review", wl[0], re.I)), wl)
    q_s, q_n = question_of(sj), question_of(nx)
    check("[CP1-T02] status --json question has kind 'set-review' and waiting_on 'user'",
          isinstance(q_s, dict) and q_s.get("kind") == "set-review" and q_s.get("waiting_on") == "user", q_s)
    check("[CP1-T02] next carries the same question entry",
          isinstance(q_n, dict) and q_n == q_s, (q_n, q_s))


TESTS.append(("CP1-T02", t02_question_shows_while_phase_still_reads_building))


# ------------------------------------------------------------------------------------------------ CP1-T03

def t03_hooks_question_gets_its_time_from_asked_at():
    d = building_project("t03")
    before = status_lines(d)
    r = rx(d, "ask", "hooks-waive")
    check("[CP1-T03] (setup) `ask hooks-waive` is accepted", r.returncode == 0, out_of(r))
    since = asked_at(d)
    sj, nx = status_json(d), next_json(d)
    q_s, q_n = question_of(sj), question_of(nx)
    check("[CP1-T03] (setup) a hooks question leaves awaiting_since unset",
          sj.get("awaiting_since") is None, sj.get("awaiting_since"))
    check("[CP1-T03] question.kind is 'hooks-waive'", isinstance(q_s, dict) and q_s.get("kind") == "hooks-waive", q_s)
    check("[CP1-T03] question.since equals pending.asked_at (not null, not awaiting_since)",
          isinstance(q_s, dict) and bool(since) and q_s.get("since") == since, (q_s, since))
    check("[CP1-T03] next shows the same since",
          isinstance(q_n, dict) and bool(since) and q_n.get("since") == since, (q_n, since))
    new = [ln for ln in status_lines(d) if ln not in before]
    wl = waiting_lines(new)
    check("[CP1-T03] the plain line also carries a time",
          len(wl) == 1 and bool(since) and carries_time(wl[0], since), (new, since))


TESTS.append(("CP1-T03", t03_hooks_question_gets_its_time_from_asked_at))


# ------------------------------------------------------------------------------------------------ CP1-T04

def _kind_case(label, d, kind, about, word_re, seen):
    """The question waiting in project d reports `kind`, names `about` when given, and its plain line fits."""
    q = question_of(status_json(d))
    seen.add(q.get("kind") if isinstance(q, dict) else None)
    check(f"[CP1-T04] {label}: question.kind is '{kind}'", isinstance(q, dict) and q.get("kind") == kind, q)
    if about:
        check(f"[CP1-T04] {label}: question.about names {about}", isinstance(q, dict) and q.get("about") == about, q)
    wl = waiting_lines(status_lines(d))
    check(f"[CP1-T04] {label}: exactly one plain line says a question waits for an answer",
          len(wl) == 1, wl)
    check(f"[CP1-T04] {label}: that line names the kind in fitting words ({word_re})",
          bool(wl) and bool(re.search(word_re, wl[0], re.I)), wl)
    return q


def t04_each_kind_reports_its_own_kind():
    seen = set()

    # choice, cancel and approve-changes share one project: each question is withdrawn before the next is asked
    d = building_project("t04-kinds")
    r = rx(d, "ask", "choice", "--options", "A,B")
    check("[CP1-T04] (setup) `ask choice --options A,B` is accepted", r.returncode == 0, out_of(r))
    _kind_case("choice", d, "choice", None, r"option|choice", seen)
    rx(d, "ask", "withdraw")

    r = rx(d, "ask", "cancel")
    check("[CP1-T04] (setup) `ask cancel` is accepted", r.returncode == 0, out_of(r))
    _kind_case("cancel", d, "cancel", None, r"cancel", seen)
    rx(d, "ask", "withdraw")

    cp_file = Path(d) / ".ratchet" / "runs" / "f" / "checkpoints.json"
    plan = json.loads(cp_file.read_text())
    plan["checkpoints"][0]["title"] = "Step 1 renamed after approval"
    cp_file.write_text(json.dumps(plan))
    r = rx(d, "ask", "approve-changes")
    check("[CP1-T04] (setup) `ask approve-changes` is accepted after the approved plan file was edited",
          r.returncode == 0 and "no question needed" not in out_of(r).lower(), out_of(r))
    _kind_case("approve-changes", d, "approve-changes", None, r"change", seen)

    d, env = blocked_round_project("t04-finding")
    r = rx(d, "ask", "accept-finding", "CP1", "F1,F2")
    check("[CP1-T04] (setup) `ask accept-finding CP1 F1,F2` is accepted", r.returncode == 0, out_of(r))
    _kind_case("accept-finding", d, "accept-finding", "CP1", r"finding", seen)

    try:
        import test_limits as tl
        d, _r = tl._project_to_cap("t04-limit", max_rounds=1)
        ok_setup = True
    except Exception as e:  # the round-limit fixture is optional per the plan; say so rather than skip silently
        ok_setup, d = False, None
        print(f"  note: raise-limit fixture not reachable: {e}")
    if ok_setup:
        r = rx(d, "ask", "raise-limit", "CP1")
        check("[CP1-T04] (setup) `ask raise-limit CP1` is accepted at the round limit", r.returncode == 0, out_of(r))
        _kind_case("raise-limit", d, "raise-limit", "CP1", r"limit", seen)

    want = {"choice", "cancel", "approve-changes", "accept-finding"} | ({"raise-limit"} if ok_setup else set())
    check("[CP1-T04] every kind reported its own name (a stub that always says 'approve-plan' fails)",
          seen == want, (seen, want))


TESTS.append(("CP1-T04", t04_each_kind_reports_its_own_kind))


# ------------------------------------------------------------------------------------------------ CP1-T05

def t05_director_lane_question_waits_for_the_director():
    d, env = blocked_round_project("t05", host="codex")
    denv = dict(env, RATCHET_ROLE="director")
    th = _th()
    rk = th.rx(d, denv, "director-key")
    en = th.rx(d, denv, "director-enroll")
    check("[CP1-T05] (setup) director-key and director-enroll succeed", rk.returncode == 0 and en.returncode == 0,
          (rk.stderr, en.stderr))
    before = status_lines(d)
    r = rx(d, "ask", "accept-finding", "CP1", "F1,F2")
    check("[CP1-T05] (setup) `ask accept-finding` still works in a director lane", r.returncode == 0, out_of(r))
    sj, nx = status_json(d), next_json(d)
    q_s, q_n = question_of(sj), question_of(nx)
    check("[CP1-T05] question.waiting_on is 'director' (not 'user')",
          isinstance(q_s, dict) and q_s.get("waiting_on") == "director", q_s)
    check("[CP1-T05] question.kind is 'accept-finding'", isinstance(q_s, dict) and q_s.get("kind") == "accept-finding",
          q_s)
    check("[CP1-T05] next says waiting_on 'director' too",
          isinstance(q_n, dict) and q_n.get("waiting_on") == "director", q_n)
    new = [ln for ln in status_lines(d) if ln not in before]
    wl = waiting_lines(new)
    check("[CP1-T05] plain status has one new line saying the run waits for an answer",
          len(wl) == 1, ("new lines:", new))
    line = wl[0] if wl else ""
    check("[CP1-T05] the line says the director's answer is awaited", bool(re.search(r"director", line, re.I)), line)
    check("[CP1-T05] the line does not say the user's answer is awaited", not re.search(r"\buser", line, re.I), line)


TESTS.append(("CP1-T05", t05_director_lane_question_waits_for_the_director))


# ------------------------------------------------------------------------------------------------ CP1-T06

def t06_question_disappears_once_answered():
    d = plan_submitted_project("t06")
    rx(d, "ask", "approve-plan")
    check("[CP1-T06] (setup) the question is waiting before the reply",
          isinstance(question_of(status_json(d)), dict), question_of(status_json(d)))
    reply_yes(d)
    r = rx(d, "approve-plan")
    check("[CP1-T06] (setup) `approve-plan` succeeds after the user's yes", r.returncode == 0, out_of(r))
    lines = status_lines(d)
    check("[CP1-T06] plain status has no waiting-question line", not waiting_lines(lines) and
          not mentions_question_at_all(lines), lines)
    sj, nx = status_json(d), next_json(d)
    check("[CP1-T06] status --json has question null or absent", question_of(sj) is None, question_of(sj))
    check("[CP1-T06] next has question null or absent", question_of(nx) is None, question_of(nx))
    check("[CP1-T06] no pending key remains in state.json", "pending" not in json.loads(state_path(d).read_text()),
          pending(d))


TESTS.append(("CP1-T06", t06_question_disappears_once_answered))


# ------------------------------------------------------------------------------------------------ CP1-T07

def t07_question_disappears_once_withdrawn_and_new_one_gets_new_time():
    d = building_project("t07")
    rx(d, "ask", "set-review", "each")
    first = asked_at(d)
    check("[CP1-T07] (setup) the first question is recorded", bool(first), first)
    check("[CP1-T07] (setup) the first question shows in status --json",
          isinstance(question_of(status_json(d)), dict), question_of(status_json(d)))
    r = rx(d, "ask", "withdraw")
    check("[CP1-T07] (setup) `ask withdraw` succeeds", r.returncode == 0, out_of(r))
    lines = status_lines(d)
    check("[CP1-T07] after the withdraw the plain line is gone", not waiting_lines(lines) and
          not mentions_question_at_all(lines), lines)
    check("[CP1-T07] after the withdraw status --json question is null or absent",
          question_of(status_json(d)) is None, question_of(status_json(d)))
    check("[CP1-T07] after the withdraw next question is null or absent",
          question_of(next_json(d)) is None, question_of(next_json(d)))
    rx(d, "ask", "cancel")
    second = asked_at(d)
    q = question_of(status_json(d))
    check("[CP1-T07] the new question's kind is 'cancel'", isinstance(q, dict) and q.get("kind") == "cancel", q)
    check("[CP1-T07] the new question has its own, later asked_at",
          bool(first) and bool(second) and second > first, (first, second))
    check("[CP1-T07] question.since is the new asked_at, never the old time",
          isinstance(q, dict) and q.get("since") == second and q.get("since") != first, (q, first, second))


TESTS.append(("CP1-T07", t07_question_disappears_once_withdrawn_and_new_one_gets_new_time))


# ------------------------------------------------------------------------------------------------ CP1-T08

def t08_run_with_no_waiting_question_prints_what_it_printed_before():
    d = building_project("t08")
    plain_a = status_lines(d)
    sj_a = status_json(d)
    check("[CP1-T08] with nothing asked, plain status mentions no waiting question or awaited answer",
          not mentions_question_at_all(plain_a), plain_a)
    check("[CP1-T08] with nothing asked, status --json has question null or absent",
          question_of(sj_a) is None, question_of(sj_a))
    rx(d, "ask", "set-review", "each")
    plain_c = status_lines(d)
    sj_c = status_json(d)
    extra = waiting_lines(plain_c)
    check("[CP1-T08] with a question waiting, plain status has exactly one more line than without",
          len(plain_c) == len(plain_a) + 1 and len(extra) == 1, (len(plain_a), len(plain_c), extra))
    rest = list(plain_c)
    if extra and extra[0] in rest:
        rest.remove(extra[0])
    check("[CP1-T08] every other line is unchanged", rest == plain_a, (plain_a, plain_c))
    for key in ("phase", "waiting_on", "stop_ok", "done", "total"):
        check(f"[CP1-T08] status --json key '{key}' is unchanged by a waiting question",
              key in sj_a and sj_a.get(key) == sj_c.get(key), (sj_a.get(key), sj_c.get(key)))
    check("[CP1-T08] with a question waiting, status --json has it (so the unchanged-keys check above is not "
          "vacuous)", isinstance(question_of(sj_c), dict), question_of(sj_c))
    rx(d, "ask", "withdraw")
    plain_b = status_lines(d)
    check("[CP1-T08] after ask + withdraw, plain status equals the nothing-asked output", plain_b == plain_a,
          (plain_a, plain_b))


TESTS.append(("CP1-T08", t08_run_with_no_waiting_question_prints_what_it_printed_before))


# ------------------------------------------------------------------------------------------------ CP1-T09

def t09_reading_status_does_not_change_question_or_run():
    d = plan_submitted_project("t09")
    rx(d, "ask", "approve-plan")
    since = asked_at(d)
    snapshot = state_bytes(d)
    sinces = []
    for _ in range(3):
        status_lines(d)
        sj = status_json(d)
        nx = next_json(d)
        sinces.append((question_of(sj) or {}).get("since"))
        sinces.append((question_of(nx) or {}).get("since"))
    check("[CP1-T09] question.since is the same, and the real asked_at, on every read",
          bool(since) and sinces == [since] * 6, (since, sinces))
    check("[CP1-T09] state.json is byte-identical after the reads", state_bytes(d) == snapshot, "state.json changed")


TESTS.append(("CP1-T09", t09_reading_status_does_not_change_question_or_run))


# ------------------------------------------------------------------------------------------------ CP1-T10

def t10_plan_waiting_to_be_asked_is_not_yet_a_waiting_question():
    d = plan_submitted_project("t10")
    sj, nx = status_json(d), next_json(d)
    lines = status_lines(d)
    check("[CP1-T10] the existing waiting_on is still 'human'", sj.get("waiting_on") == "human", sj.get("waiting_on"))
    check("[CP1-T10] the existing phase is still awaiting_plan_approval", sj.get("phase") == "awaiting_plan_approval",
          sj.get("phase"))
    check("[CP1-T10] status --json question is null or absent (no question has been put to anyone)",
          question_of(sj) is None, question_of(sj))
    check("[CP1-T10] next question is null or absent", question_of(nx) is None, question_of(nx))
    check("[CP1-T10] plain status has no question line", not waiting_lines(lines) and
          not mentions_question_at_all(lines), lines)
    check("[CP1-T10] (setup) no pending question exists in state.json", pending(d) is None, pending(d))


TESTS.append(("CP1-T10", t10_plan_waiting_to_be_asked_is_not_yet_a_waiting_question))


# ------------------------------------------------------------------------------------------------ CP1-T11

def _windows(text, needle, width):
    for m in re.finditer(re.escape(needle), text):
        yield text[max(0, m.start() - width): m.end() + width]


def t11_docs_describe_the_new_line_and_entry():
    docs = {"README.md": README, "SKILL.md": SKILL, "schemas.md": SCHEMAS}
    texts = {}
    for name, p in docs.items():
        texts[name] = p.read_text(encoding="utf-8") if p.exists() else ""
        check(f"[CP1-T11] (setup) {name} exists", bool(texts[name]), p)
    for name, txt in texts.items():
        wins = list(_windows(txt, "`question`", 700))
        check(f"[CP1-T11] {name} mentions the `question` entry of status --json and next",
              bool(wins) and bool(re.search(r"status --json", txt)) and bool(re.search(r"\bnext\b", txt)),
              "no `question` entry")
        check(f"[CP1-T11] {name} describes the entry's kind and since fields",
              any(re.search(r"\bkind\b", w) and re.search(r"\bsince\b", w) for w in wins), "kind/since not described")
        check(f"[CP1-T11] {name} describes the status line saying the run waits for an answer",
              any(re.search(r"wait\w*[^.\n]{0,80}answer|answer[^.\n]{0,80}wait", w, re.I) for w in wins),
              "no waiting-for-answer line described near `question`")
    s = texts["schemas.md"]
    wins = list(_windows(s, "waiting_on", 400))
    check("[CP1-T11] schemas.md lists waiting_on values 'user' and 'director'",
          any(re.search(r"['\"`]user['\"`]", w) and re.search(r"['\"`]director['\"`]", w) for w in wins),
          "no waiting_on 'user'/'director' listing")
    qwins = list(_windows(s, "`question`", 900))
    check("[CP1-T11] schemas.md says the entry is absent or null once answered or withdrawn",
          any(re.search(r"(absent|null)", w, re.I) and re.search(r"answered", w, re.I) and
              re.search(r"withdrawn", w, re.I) for w in qwins),
          "no absent/null-when-answered note")


TESTS.append(("CP1-T11", t11_docs_describe_the_new_line_and_entry))


# ------------------------------------------------------------------------------------------------ CP1-X01..X04
# Added in fix round 1 (new checks; the planned cases above are untouched). They pin what the waiting line CLAIMS:
# only questions that really hold the run say it will not move, and a question the user already replied to is no
# longer reported as waiting (it is dropped, once a reply is recorded, even before the agent acts on it).

STUCK_RE = r"will not move|not move|stuck|cannot continue"
CARRY_ON_RE = r"carry on|keeps? (building|going)"


def one_wait_line(d):
    wl = waiting_lines(status_lines(d))
    return wl[0] if len(wl) == 1 else None


def x01_raise_limit_fixture_must_build_and_say_the_run_waits():
    try:
        import test_limits as tl
        d, _r = tl._project_to_cap("x01-limit", max_rounds=1)
    except Exception as e:
        check("[CP1-X01] the raise-limit fixture (test_limits._project_to_cap) can be built", False, e)
        return
    check("[CP1-X01] the raise-limit fixture can be built", True)
    r = rx(d, "ask", "raise-limit", "CP1")
    check("[CP1-X01] (setup) `ask raise-limit CP1` is accepted at the round limit", r.returncode == 0, out_of(r))
    sj = status_json(d)
    q = question_of(sj)
    check("[CP1-X01] the question is kind 'raise-limit' about CP1 with the run blocked",
          isinstance(q, dict) and q.get("kind") == "raise-limit" and q.get("about") == "CP1" and
          sj.get("phase") == "blocked", (q, sj.get("phase")))
    line = one_wait_line(d)
    check("[CP1-X01] its line says the run will not move until answered (a blocked run does wait)",
          bool(line) and bool(re.search(r"will not move", line)), line)


def x02_gating_questions_say_the_run_waits():
    d = plan_submitted_project("x02-plan")
    rx(d, "ask", "approve-plan")
    line = one_wait_line(d)
    check("[CP1-X02] approve-plan: the line says the run will not move until answered",
          bool(line) and bool(re.search(r"will not move", line)), line)

    d = _g().project("x02-human", config=["max_stop_blocks=1"])
    _g().approve_plan(d)
    _g().stop(d)
    _g().stop(d)
    check("[CP1-X02] (setup) the safety valve blocked the run", status_json(d).get("phase") == "blocked",
          status_json(d).get("phase"))
    rx(d, "ask", "human-approve")
    line = one_wait_line(d)
    check("[CP1-X02] human-approve: the line says the run will not move until answered",
          bool(line) and bool(re.search(r"will not move", line)), line)
    rx(d, "ask", "withdraw")
    r = rx(d, "ask", "choice", "--options", "A,B")
    check("[CP1-X02] (setup) a choice is accepted in a blocked run", r.returncode == 0, out_of(r))
    line = one_wait_line(d)
    check("[CP1-X02] choice in a blocked run: the line says the run will not move until answered",
          bool(line) and bool(re.search(r"will not move", line)), line)

    d, env = blocked_round_project("x02-finding")
    rx(d, "ask", "accept-finding", "CP1", "F1,F2")
    line = one_wait_line(d)
    check("[CP1-X02] accept-finding in a blocked run: the line says the run will not move until answered",
          bool(line) and bool(re.search(r"will not move", line)), line)


def x03_questions_that_do_not_gate_never_claim_the_run_is_stuck():
    d = building_project("x03")
    cases = [("set-review", ["ask", "set-review", "each"]), ("cancel", ["ask", "cancel"]),
             ("hooks-waive", ["ask", "hooks-waive"]), ("choice (building)", ["ask", "choice", "--options", "A,B"])]
    for label, argv in cases:
        r = rx(d, *argv)
        check(f"[CP1-X03] (setup) `{' '.join(argv)}` is accepted", r.returncode == 0, out_of(r))
        line = one_wait_line(d)
        check(f"[CP1-X03] {label}: the line exists and does not say the run is stuck",
              bool(line) and not re.search(STUCK_RE, line, re.I), line)
        check(f"[CP1-X03] {label}: the line says the run can carry on", bool(line) and
              bool(re.search(CARRY_ON_RE, line, re.I)), line)
        check(f"[CP1-X03] {label}: the phase really still reads building", status_json(d).get("phase") == "building",
              status_json(d).get("phase"))
        rx(d, "ask", "withdraw")


def x04_a_question_the_user_already_replied_to_is_not_waiting():
    d = building_project("x04-no")
    rx(d, "ask", "set-review", "each")
    check("[CP1-X04] (setup) the question waits before any reply", one_wait_line(d) is not None, status_lines(d))
    _g().say(d, "no thanks")
    check("[CP1-X04] after a no, plain status has no waiting line", not waiting_lines(status_lines(d)),
          status_lines(d))
    check("[CP1-X04] after a no, status --json and next carry question null",
          question_of(status_json(d)) is None and question_of(next_json(d)) is None,
          (question_of(status_json(d)), question_of(next_json(d))))

    d = plan_submitted_project("x04-yes")
    rx(d, "ask", "approve-plan")
    t = asked_at(d)
    reply_yes(d)
    check("[CP1-X04] (setup) the agent has not acted on the yes yet (pending is still there)", asked_at(d) == t,
          pending(d))
    check("[CP1-X04] after a yes not yet acted on, plain status does not say the user's answer is awaited",
          not waiting_lines(status_lines(d)), status_lines(d))
    check("[CP1-X04] after a yes not yet acted on, question is null in status --json and next",
          question_of(status_json(d)) is None and question_of(next_json(d)) is None,
          (question_of(status_json(d)), question_of(next_json(d))))

    d = building_project("x04-other-chat")
    rx(d, "ask", "set-review", "each")
    _g().say(d, "no thanks", session="s-other")
    check("[CP1-X04] a message from another chat does not answer the question",
          one_wait_line(d) is not None, status_lines(d))


def x05_status_stays_readable_with_a_damaged_enrollment():
    d = building_project("x05")
    rx(d, "ask", "set-review", "each")
    (Path(d) / ".ratchet" / "director.json").write_text("{not json")
    before = state_bytes(d)
    r = rx(d, "status")
    check("[CP1-X05] plain status still succeeds with a damaged director.json", r.returncode == 0, out_of(r))
    line = one_wait_line(d)
    check("[CP1-X05] it still says a question waits (put to the director, the safe reading)",
          bool(line) and "director" in line, line)
    (Path(d) / ".ratchet" / "director.json").write_text(json.dumps({"sig": "not-a-signature"}))
    r = rx(d, "status")
    check("[CP1-X05] plain status still succeeds with an unsigned director.json", r.returncode == 0, out_of(r))
    check("[CP1-X05] reading changed nothing in state.json", state_bytes(d) == before, "state.json changed")


def x06_claim_is_put_to_the_user():
    d = building_project("x06")
    r = rx(d, "ask", "claim")
    check("[CP1-X06] (setup) `ask claim` is accepted", r.returncode == 0, out_of(r))
    q = question_of(status_json(d))
    check("[CP1-X06] a claim is waiting on the 'user' (a director cannot sign it)",
          isinstance(q, dict) and q.get("kind") == "claim" and q.get("waiting_on") == "user", q)


TESTS.append(("CP1-X01", x01_raise_limit_fixture_must_build_and_say_the_run_waits))
TESTS.append(("CP1-X02", x02_gating_questions_say_the_run_waits))
TESTS.append(("CP1-X03", x03_questions_that_do_not_gate_never_claim_the_run_is_stuck))
TESTS.append(("CP1-X04", x04_a_question_the_user_already_replied_to_is_not_waiting))
TESTS.append(("CP1-X05", x05_status_stays_readable_with_a_damaged_enrollment))
TESTS.append(("CP1-X06", x06_claim_is_put_to_the_user))


def x07_director_lane_chat_turn_keeps_the_question_shown():
    d = building_project("x07")
    rx(d, "ask", "set-review", "each")
    (Path(d) / ".ratchet" / "director.json").write_text("{not json")  # read as a director lane
    check("[CP1-X07] (setup) the question waits on the director", one_wait_line(d) is not None
          and (question_of(status_json(d)) or {}).get("waiting_on") == "director", status_lines(d))
    _g().say(d, "how is it going?")
    q = question_of(status_json(d))
    check("[CP1-X07] after a chat turn, status --json still carries the question, waiting on the director",
          isinstance(q, dict) and q.get("kind") == "set-review" and q.get("waiting_on") == "director", q)
    check("[CP1-X07] after a chat turn, next still carries the question", question_of(next_json(d)) == q,
          question_of(next_json(d)))
    check("[CP1-X07] after a chat turn, plain status still prints the waiting line", one_wait_line(d) is not None,
          status_lines(d))
    rx(d, "ask", "withdraw")
    check("[CP1-X07] a withdrawn question is gone", question_of(status_json(d)) is None, question_of(status_json(d)))


def x08_a_paused_run_does_not_claim_it_carries_on():
    d = building_project("x08")
    rx(d, "ask", "set-review", "each")
    r = rx(d, "pause", "--reason", "waiting for the director")
    check("[CP1-X08] (setup) the run pauses", r.returncode == 0 and status_json(d).get("phase") == "paused", out_of(r))
    line = one_wait_line(d)
    check("[CP1-X08] the waiting line is still printed", line is not None, status_lines(d))
    check("[CP1-X08] the line does not say the run carries on", bool(line) and not re.search(CARRY_ON_RE, line, re.I),
          line)
    q = question_of(status_json(d))
    check("[CP1-X08] stops_run is left unsaid (null)", isinstance(q, dict) and q.get("stops_run") is None, q)


def x09_a_question_from_an_earlier_phase_is_not_shown():
    d = plan_submitted_project("x09")
    rx(d, "ask", "approve-plan")
    check("[CP1-X09] (setup) the question waits at the plan approval", one_wait_line(d) is not None, status_lines(d))
    rx(d, "pause", "--reason", "something else came up")
    check("[CP1-X09] (setup) the phase moved on", status_json(d).get("phase") == "paused", status_json(d).get("phase"))
    check("[CP1-X09] the question asked at the earlier phase is not shown",
          question_of(status_json(d)) is None and question_of(next_json(d)) is None
          and not waiting_lines(status_lines(d)), (question_of(status_json(d)), status_lines(d)))


TESTS.append(("CP1-X07", x07_director_lane_chat_turn_keeps_the_question_shown))
TESTS.append(("CP1-X08", x08_a_paused_run_does_not_claim_it_carries_on))
TESTS.append(("CP1-X09", x09_a_question_from_an_earlier_phase_is_not_shown))


# ------------------------------------------------------------------------------------------------ run

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
        try:
            fn()
        except Exception as e:  # a crash still counts as a result for this case, never a silent drop
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
