#!/usr/bin/env python3
"""
CP1 tests: the planner's brief says what earlier work learned.   python3 tests/test_digest.py

`ratchet.py brief --role planner` ends with a short "What earlier work says" section: the project's recent
learnings.md bullets, the findings earlier (non-active) runs saved for later that nobody accepted yet, and the
limits the user already accepted (an accepted finding, or a learnings.md "Accepted limit (...)" bullet), newest
and most relevant to the new feature first, each section capped with a count of how many more there are and the
path to the full file. Every other role's brief is unchanged. A project with no earlier work gets one plain line
instead. This never touches state.json, active.json or learnings.md (brief stays read-only).

Reuses tests/test_gates.py (project, approve_plan, to_adversarial, open_round, write_review, R0, rx, say, git,
ENV, ENGINE) for the throwaway git projects and the real-CLI-subprocess way every suite in this repo drives the
engine. Earlier (non-active) run folders are fabricated directly under a temp project's .ratchet/runs/<slug>/
with hand-written state.json files -- a test fixture, never this repo's own .ratchet -- matching tests/CP1.json's
own strategy and the implementer's plan to read them with ratchet.py's plain record reader rather than its
chain-verified state accessor.

The exact wording below ("What earlier work says", "Learnings:", "Open findings (not fixed):", "Accepted
limits:", a line naming how many more items there are plus "learnings.md"/"state.json") is this suite's own
pick for substance the implementer must produce; see the report for the full list.

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
import test_gates as g          # noqa: E402  (project, approve_plan, to_adversarial, open_round, write_review, ...)

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP1-[TX]\d+[a-z]?)\]")

HEADING = "What earlier work says"
LEARN_HEAD = "Learnings:"
OPEN_HEAD = "Open findings (not fixed):"
ACCEPTED_HEAD = "Accepted limits:"


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

def _project_with_feature(name, feature, cps=1):
    """Like test_gates.project(), but with a feature text the test controls (state.json's own `feature` field
    comes straight from `init`'s positional argument), needed for the relevance-ordering cases."""
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-digest-{name}-"))
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        g.git(d, *c)
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "init")
    g.say(d, "please build the feature")
    r = g.rx(d, "init", feature, "--slug", "f")
    assert r.returncode == 0, r.stderr
    g.rx(d, "config", "--set", "min_review_seconds=0", "providers=[]")
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    plan = {"feature": feature, "objective": _tmp.OBJECTIVE_MIN, "checkpoints": []}
    for i in range(1, cps + 1):
        cp = {"id": f"CP{i}", "title": f"Step {i}", "summary": "s", "done_when": ["works"],
              "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
              "tests": {"files": [f"tests/cp{i}.test.js"], "command": f"node --test tests/cp{i}.test.js"}}
        plan["checkpoints"].append(cp)
        (run / "tests" / f"CP{i}.json").write_text(json.dumps({"cases": [{"id": f"CP{i}-T01", "layer": "code", "proves": [1]}]}))
    (run / "checkpoints.json").write_text(json.dumps(plan))
    r = g.rx(d, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    return d


def _finding(fid, severity, description, round_no=1, reviewer="codex-correctness", accepted=False,
             accepted_by=None, reason=None):
    f = {"id": fid, "reviewer": reviewer, "severity": severity, "description": description, "round": round_no}
    if accepted:
        f.update(accepted=True, accepted_by=accepted_by or "rahil", reason=reason or "accepted for this test")
    return f


def _write_earlier_run(d, slug, findings_by_cp=None, updated_at=None, mtime=None, bad_json=False, no_state=False):
    """A fabricated earlier (non-active) run folder directly under .ratchet/runs/<slug>/: the history this
    checkpoint's tests build by hand instead of running a whole plan end to end (tests/CP1.json's own strategy).
    This never goes through the engine, matching the implementation's own plan to read these with ratchet.py's
    plain record reader, never its chain-verified state accessor -- so it is never a real tamper."""
    run = d / ".ratchet/runs" / slug
    run.mkdir(parents=True, exist_ok=True)
    if no_state:
        return run
    p = run / "state.json"
    if bad_json:
        p.write_text("{this is not valid json at all")
        return run
    cps = {cp: {"deferred": fs} for cp, fs in (findings_by_cp or {}).items()}
    state = {"slug": slug, "feature": f"an earlier feature for {slug}", "phase": "done",
              "checkpoints": cps, "updated_at": updated_at or "2026-01-01T00:00:00+00:00"}
    p.write_text(json.dumps(state))
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return run


def _append_learning_lines(d, *lines):
    p = d / ".ratchet/learnings.md"
    with p.open("a") as f:
        for l in lines:
            f.write(l.rstrip("\n") + "\n")


def _section(output):
    """Everything from the 'What earlier work says' heading to the end of brief's output, or '' if absent."""
    idx = output.find(HEADING)
    return output[idx:] if idx >= 0 else ""


# ------------------------------------------------------------------------------------------------ the one richly-fixtured project (T01-T03, T08-T11, T13)

def _rich_project():
    d = g.project("digest-rich", cps=1)
    old_open = _finding("F1", "major", "RICHOPEN the retry queue can silently drop a job")
    old_acc = _finding("F2", "minor", "RICHACC csv export ignores locale settings", accepted=True,
                        accepted_by="rahil", reason="low risk, accepted for now")
    _write_earlier_run(d, "run-old", {"CP2": [old_open], "CP1": [old_acc]},
                       updated_at="2026-01-01T00:00:00+00:00", mtime=1700000000)
    _append_learning_lines(
        d,
        "- (2026-01-02, human) RICHLEARN keep retries idempotent across the queue.",
        "- (2026-01-03, human) Accepted limit (user decision): RICHLIMIT large CSV exports may run slowly on "
        "the free tier.",
        "- (2026-01-04, rahil, accept-finding) Accepted finding run-old/CP1/round-1/codex-correctness/F2 (CP1, "
        "minor): RICHACC csv export ignores locale settings. Reason: low risk, accepted for now",
    )
    newer_open = _finding("F3", "major", "RICHNEWER the login page shows a stale avatar")
    _write_earlier_run(d, "run-newer", {"CP3": [newer_open]},
                       updated_at="2026-02-01T00:00:00+00:00", mtime=1710000000)
    _write_earlier_run(d, "run-damaged", bad_json=True)
    _write_earlier_run(d, "run-empty", no_state=True)
    return d


_RICH = None


def rich():
    global _RICH
    if _RICH is None:
        _RICH = _rich_project()
    return _RICH


# ------------------------------------------------------------------------------------------------ cases

def t01_planner_brief_ends_with_earlier_work_section():
    d = rich()
    r = g.rx(d, "brief", "CP1", "--role", "planner")
    check("[CP1-T01] `brief CP1 --role planner` exits 0", r.returncode == 0, r.stdout + r.stderr)
    out = r.stdout
    idx = out.find(HEADING)
    check(f"[CP1-T01] the output contains a '{HEADING}' heading", idx >= 0, out)
    rules_idx = out.find("Rules:")
    check("[CP1-T01] the section comes after the objective and the planner's own Rules: text",
          idx >= 0 and 0 <= rules_idx < idx, out)
    sec = _section(out)
    check("[CP1-T01] a recent learnings.md line appears in the section",
          "RICHLEARN" in sec or "RICHLIMIT" in sec, sec)
    check("[CP1-T01] the open deferred finding's description appears in the section",
          "RICHOPEN" in sec, sec)


def t02_open_finding_worded_as_saved_for_later_not_fixed():
    d = rich()
    out = g.rx(d, "brief", "CP1", "--role", "planner").stdout
    sec = _section(out)
    open_idx = sec.find(OPEN_HEAD)
    acc_idx = sec.find(ACCEPTED_HEAD)
    item_idx = sec.find("RICHOPEN")
    check(f"[CP1-T02] the section has an '{OPEN_HEAD}' part", open_idx >= 0, sec)
    check("[CP1-T02] the open finding's description is listed under that part",
          open_idx >= 0 and item_idx > open_idx, sec)
    check("[CP1-T02] the open finding never shows up under 'Accepted limits:'",
          item_idx >= 0 and not (acc_idx >= 0 and item_idx > acc_idx), sec)
    check("[CP1-T02] the open finding is never claimed as fixed",
          re.search(r"RICHOPEN.{0,80}\bfixed\b", sec, re.I) is None, sec)


def t03_accepted_findings_and_limit_bullets_both_listed_once():
    d = rich()
    out = g.rx(d, "brief", "CP1", "--role", "planner").stdout
    sec = _section(out)
    acc_idx = sec.find(ACCEPTED_HEAD)
    check(f"[CP1-T03] the section has an '{ACCEPTED_HEAD}' part", acc_idx >= 0, sec)
    after = sec[acc_idx:] if acc_idx >= 0 else ""
    check("[CP1-T03] the accepted finding (RICHACC) appears once, under accepted limits",
          after.count("RICHACC") == 1, after)
    check("[CP1-T03] the accepted-limit learnings bullet (RICHLIMIT) appears once, under accepted limits",
          after.count("RICHLIMIT") == 1, after)


def t04_relevant_item_comes_before_newer_unrelated_item():
    feature = "Add CSV export to the monthly reports page"
    d = _project_with_feature("t04", feature)
    old = _finding("A1", "major", "MATCHOLD csv export truncates rows over ten thousand")
    new = _finding("A2", "major", "MATCHNEW the login avatar flickers on hover")
    _write_earlier_run(d, "run-old", {"CP1": [old]}, updated_at="2026-01-01T00:00:00+00:00", mtime=1700000000)
    _write_earlier_run(d, "run-newer", {"CP1": [new]}, updated_at="2026-02-01T00:00:00+00:00", mtime=1710000000)
    out = g.rx(d, "brief", "CP1", "--role", "planner").stdout
    sec = _section(out)
    io, inew = sec.find("MATCHOLD"), sec.find("MATCHNEW")
    check("[CP1-T04] both items appear in the section", io >= 0 and inew >= 0, sec)
    check("[CP1-T04] the older item sharing words with the feature is listed before the newer unrelated item",
          io >= 0 and inew >= 0 and io < inew, sec)


def t05_newest_first_with_no_relevance_match():
    feature = "Add a dark mode toggle to the settings screen"
    d = _project_with_feature("t05", feature)
    old = _finding("B1", "major", "FALLBACKOLD zzqqx something unrelated entirely")
    new = _finding("B2", "major", "FALLBACKNEW yyqqw another unrelated thing")
    _write_earlier_run(d, "run-old", {"CP1": [old]}, updated_at="2026-01-01T00:00:00+00:00", mtime=1700000000)
    _write_earlier_run(d, "run-newer", {"CP1": [new]}, updated_at="2026-02-01T00:00:00+00:00", mtime=1710000000)
    out = g.rx(d, "brief", "CP1", "--role", "planner").stdout
    sec = _section(out)
    iold, inew = sec.find("FALLBACKOLD"), sec.find("FALLBACKNEW")
    check("[CP1-T05] both items appear in the section", iold >= 0 and inew >= 0, sec)
    check("[CP1-T05] with no relevance match, the newer item is listed first",
          iold >= 0 and inew >= 0 and inew < iold, sec)


def t06_sections_capped_with_count_and_path():
    d = _project_with_feature("t06", "a totally unrelated feature about nothing in particular")
    learn_lines = [f"- (2026-01-{i + 1:02d}, human) CAPLEARN{i:02d} unique learning line number {i}."
                   for i in range(10)]
    _append_learning_lines(d, *learn_lines)
    open_findings = [_finding(f"O{i}", "major", f"CAPFIND{i:02d} unique open finding number {i}")
                     for i in range(10)]
    _write_earlier_run(d, "run-caps", {"CP1": open_findings}, updated_at="2026-01-01T00:00:00+00:00")
    acc_findings = [_finding(f"C{i}", "minor", f"CAPACC{i:02d} unique accepted item number {i}", accepted=True,
                              accepted_by="rahil", reason="accepted for cap test") for i in range(10)]
    _write_earlier_run(d, "run-caps-acc", {"CP2": acc_findings}, updated_at="2026-01-02T00:00:00+00:00")
    out = g.rx(d, "brief", "CP1", "--role", "planner").stdout
    sec = _section(out)

    learn_shown = sum(1 for i in range(10) if f"CAPLEARN{i:02d}" in sec)
    find_shown = sum(1 for i in range(10) if f"CAPFIND{i:02d}" in sec)
    acc_shown = sum(1 for i in range(10) if f"CAPACC{i:02d}" in sec)

    check("[CP1-T06] the learnings part is capped (shows a few, not all 10)", 0 < learn_shown < 10, sec)
    check("[CP1-T06] the open-findings part is capped (shows a few, not all 10)", 0 < find_shown < 10, sec)
    check("[CP1-T06] the accepted-limits part is capped (shows a few, not all 10)", 0 < acc_shown < 10, sec)
    check("[CP1-T06] a 'N more' count plus the learnings.md path appears for the trimmed learnings part",
          re.search(r"\d+\s+more.{0,160}learnings\.md|learnings\.md.{0,160}\d+\s+more", sec, re.I | re.S)
          is not None, sec)
    check("[CP1-T06] a 'N more' count plus a state.json path appears for a trimmed findings/limits part",
          re.search(r"\d+\s+more.{0,200}state\.json|state\.json.{0,200}\d+\s+more", sec, re.I | re.S)
          is not None, sec)


def t07_no_earlier_work_gets_one_plain_line():
    d = g.project("digest-fresh", cps=1)
    r = g.rx(d, "brief", "CP1", "--role", "planner")
    check("[CP1-T07] `brief` exits 0 in a project with no earlier work", r.returncode == 0, r.stdout + r.stderr)
    sec = _section(r.stdout)
    check(f"[CP1-T07] the output still carries the '{HEADING}' heading", bool(sec), r.stdout)
    rest = sec[len(HEADING):]
    lines = [l for l in rest.splitlines() if l.strip()]
    check("[CP1-T07] the note mentions there is no earlier work",
          len(lines) >= 1 and re.search(r"no earlier work", lines[0], re.I) is not None, rest)
    check("[CP1-T07] the section is exactly one plain line, with no empty sub-headings",
          len(lines) == 1, rest)


def t08_every_other_roles_brief_is_unchanged():
    d = rich()
    roles = ["implementer", "fixer", "test-writer", "reviewer", "scout"]
    markers = ["RICHOPEN", "RICHACC", "RICHLEARN", "RICHLIMIT", "RICHNEWER", HEADING]
    for role in roles:
        r = g.rx(d, "brief", "CP1", "--role", role)
        check(f"[CP1-T08] `brief CP1 --role {role}` exits 0", r.returncode == 0, r.stdout + r.stderr)
        for mk in markers:
            check(f"[CP1-T08] role '{role}' brief does not contain '{mk}'", mk not in r.stdout, r.stdout)


def t09_accepted_finding_not_duplicated_with_its_own_marker():
    d = rich()
    out = g.rx(d, "brief", "CP1", "--role", "planner").stdout
    sec = _section(out)
    acc_idx = sec.find(ACCEPTED_HEAD)
    after = sec[acc_idx:] if acc_idx >= 0 else sec
    check("[CP1-T09] RICHACC appears exactly once in the accepted-limits part, not once per source",
          after.count("RICHACC") == 1, after)


def t10_damaged_earlier_run_skipped_not_fatal():
    d = rich()
    r = g.rx(d, "brief", "CP1", "--role", "planner")
    out = r.stdout + r.stderr
    check("[CP1-T10] brief exits 0 even with a damaged earlier run's state.json", r.returncode == 0, out)
    check("[CP1-T10] no traceback leaks into the output", "Traceback" not in out, out)
    sec = _section(r.stdout)
    check("[CP1-T10] the readable run's item still appears in the section", "RICHOPEN" in sec, sec)


def t11_run_folder_with_no_state_json_does_not_crash():
    d = rich()
    r = g.rx(d, "brief", "CP1", "--role", "planner")
    check("[CP1-T11] brief exits 0 with a run folder that has no state.json at all",
          r.returncode == 0, r.stdout + r.stderr)
    sec = _section(r.stdout)
    check("[CP1-T11] the section is still produced from the other, readable sources",
          "RICHOPEN" in sec and "RICHNEWER" in sec, sec)


def t12_active_runs_own_finding_not_shown_back_to_it():
    d = g.project("digest-t12", cps=1)
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = g.open_round(d)
    minor = [{"id": "M1", "severity": "minor", "file": "src/f.js",
              "description": "ACTIVEOWN the retry queue needs a comment"}]
    r = g.rx(d, "adv-record", "CP1", "--reviews", g.write_review(d, rnd, g.R0(rnd), minor))
    check("[CP1-T12] setup: the active run records its own minor (deferred) finding",
          r.returncode == 0, r.stdout + r.stderr)
    other = _finding("V1", "major", "OTHERVISIBLE the password reset email never arrives")
    _write_earlier_run(d, "run-visible", {"CP1": [other]}, updated_at="2026-01-01T00:00:00+00:00")
    out = g.rx(d, "brief", "CP1", "--role", "planner").stdout
    sec = _section(out)
    check("[CP1-T12] the active run's own finding is not shown back to it as earlier work",
          "ACTIVEOWN" not in sec, sec)
    check("[CP1-T12] another run folder's finding still appears",
          "OTHERVISIBLE" in sec, sec)


def t13_brief_stays_read_only():
    d = rich()
    files = {
        "state.json": d / ".ratchet/runs/f/state.json",
        "active.json": d / ".ratchet/active.json",
        "learnings.md": d / ".ratchet/learnings.md",
    }
    before = {k: p.read_bytes() for k, p in files.items() if p.exists()}
    before_listing = sorted(str(p) for p in (d / ".ratchet").rglob("*"))
    r1 = g.rx(d, "brief", "CP1", "--role", "planner")
    r2 = g.rx(d, "brief", "--role", "planner")
    check("[CP1-T13] both calls exit 0", r1.returncode == 0 and r2.returncode == 0, r1.stderr + r2.stderr)
    after = {k: p.read_bytes() for k, p in files.items() if p.exists()}
    check("[CP1-T13] state.json is byte-for-byte unchanged", after.get("state.json") == before.get("state.json"), "")
    check("[CP1-T13] active.json is byte-for-byte unchanged",
          after.get("active.json") == before.get("active.json"), "")
    check("[CP1-T13] learnings.md is byte-for-byte unchanged",
          after.get("learnings.md") == before.get("learnings.md"), "")
    after_listing = sorted(str(p) for p in (d / ".ratchet").rglob("*"))
    check("[CP1-T13] no new file (a lock or otherwise) was left behind under .ratchet/",
          after_listing == before_listing, (before_listing, after_listing))


def t14_docs_point_at_the_section_and_the_full_files():
    planner_md = (ROOT / "agents" / "ratchet-checkpoint-planner.md").read_text()
    skill = (ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md").read_text()
    readme = (ROOT / "README.md").read_text()
    schemas = (ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md").read_text()
    check("[CP1-T14] the checkpoint-planner agent describes the 'What earlier work says' section",
          re.search(r"what earlier work says", planner_md, re.I) is not None, "")
    check("[CP1-T14] the checkpoint-planner agent no longer *only* says to read learnings.md first",
          re.search(r"Read `\.ratchet/learnings\.md` first\.\s*Then read the feature request", planner_md)
          is None, "")
    check("[CP1-T14] the orchestrator skill describes the section",
          re.search(r"what earlier work says", skill, re.I) is not None, "")
    check("[CP1-T14] README describes the section",
          re.search(r"what earlier work says", readme, re.I) is not None, "")
    check("[CP1-T14] schemas.md describes the section",
          re.search(r"what earlier work says", schemas, re.I) is not None, "")
    check("[CP1-T14] the docs point at the full files behind the section (learnings.md and state.json/runs)",
          all(re.search(r"learnings\.md", t) and re.search(r"state\.json|runs/", t)
              for t in (skill, readme, schemas)), "")


# ------------------------------------------------------------------------------------------------ CP1-X01
# (adversarial round 01 fix, S1+A1): the Accepted limits section ranked a state.json item by its run file's mtime
# and a learnings.md bullet by bullet index -- two incompatible scales where the mtime (a huge epoch number) always
# dominated the index (a small count), so a state.json item always looked "newest" no matter its real date. This
# reproduces that exactly: a run file with an OLD mtime vs a learnings.md bullet with a genuinely NEWER date head.

def x01_mixed_scale_recency_uses_one_comparable_unit():
    feature = "a totally unrelated feature about nothing in particular"
    d = _project_with_feature("x01", feature)
    old = _finding("Z1", "minor", "MIXOLD stale accepted item from an old run", accepted=True,
                    accepted_by="rahil", reason="old")
    _write_earlier_run(d, "run-mixold", {"CP1": [old]}, updated_at="2020-01-01T00:00:00+00:00", mtime=1577836800)
    _append_learning_lines(
        d, "- (2026-09-30, human) Accepted limit (user decision): MIXNEW a genuinely newer accepted limit "
           "than the old run's mtime-based item.")
    out = g.rx(d, "brief", "CP1", "--role", "planner").stdout
    sec = _section(out)
    acc_idx = sec.find(ACCEPTED_HEAD)
    after = sec[acc_idx:] if acc_idx >= 0 else sec
    iold, inew = after.find("MIXOLD"), after.find("MIXNEW")
    check("[CP1-X01] both items appear in Accepted limits", iold >= 0 and inew >= 0, after)
    check("[CP1-X01] the genuinely newer learnings.md bullet (real date 2026-09-30) ranks before the older run's "
          "accepted item (mtime 2020-01-01): recency compares on one real-date scale, not mtime vs bullet-index",
          iold >= 0 and inew >= 0 and inew < iold, after)


# ------------------------------------------------------------------------------------------------ CP1-X02
# (adversarial round 01 fix, G1): within one earlier run, every deferred finding shares that run file's single
# mtime, so a stable sort used to keep the oldest ones and hide the newest. This reproduces that with two findings
# in the same run and checkpoint, differing only by round number, and checks the later round sorts first.

def x02_newest_round_first_within_one_run_when_mtime_ties():
    feature = "a totally unrelated feature about nothing in particular"
    d = _project_with_feature("x02", feature)
    old_round = _finding("W1", "major", "TIEOLD an older round finding in the same run", round_no=1)
    new_round = _finding("W2", "major", "TIENEW a later round finding in the same run", round_no=9)
    _write_earlier_run(d, "run-tie", {"CP1": [old_round, new_round]}, updated_at="2026-01-01T00:00:00+00:00")
    out = g.rx(d, "brief", "CP1", "--role", "planner").stdout
    sec = _section(out)
    open_idx = sec.find(OPEN_HEAD)
    after = sec[open_idx:] if open_idx >= 0 else sec
    iold, inew = after.find("TIEOLD"), after.find("TIENEW")
    check("[CP1-X02] both items appear in Open findings", iold >= 0 and inew >= 0, after)
    check("[CP1-X02] with the run's findings sharing one mtime (no individual timestamp of their own), the later "
          "round's finding is listed before the earlier round's, not left in file order",
          iold >= 0 and inew >= 0 and inew < iold, after)


TESTS = [
    ("CP1-T01", t01_planner_brief_ends_with_earlier_work_section),
    ("CP1-T02", t02_open_finding_worded_as_saved_for_later_not_fixed),
    ("CP1-T03", t03_accepted_findings_and_limit_bullets_both_listed_once),
    ("CP1-T04", t04_relevant_item_comes_before_newer_unrelated_item),
    ("CP1-T05", t05_newest_first_with_no_relevance_match),
    ("CP1-T06", t06_sections_capped_with_count_and_path),
    ("CP1-T07", t07_no_earlier_work_gets_one_plain_line),
    ("CP1-T08", t08_every_other_roles_brief_is_unchanged),
    ("CP1-T09", t09_accepted_finding_not_duplicated_with_its_own_marker),
    ("CP1-T10", t10_damaged_earlier_run_skipped_not_fatal),
    ("CP1-T11", t11_run_folder_with_no_state_json_does_not_crash),
    ("CP1-T12", t12_active_runs_own_finding_not_shown_back_to_it),
    ("CP1-T13", t13_brief_stays_read_only),
    ("CP1-T14", t14_docs_point_at_the_section_and_the_full_files),
    ("CP1-X01", x01_mixed_scale_recency_uses_one_comparable_unit),
    ("CP1-X02", x02_newest_round_first_within_one_run_when_mtime_ties),
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
