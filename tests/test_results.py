#!/usr/bin/env python3
"""
CP6 tests, kept separate from the slow trust suites (tests/test_trust_cp*.py): the test gate reads each planned test
case's own result (JUnit XML or Ratchet's small JSON report) instead of trusting the process exit code alone, so a
test that crashes, gets skipped, or silently drops out of the report cannot pass a checkpoint.

    python3 tests/test_results.py

Reuses tests/trust_helpers.py's low-level helpers (rx, git, make_env, js, project plumbing) for building throwaway
projects and driving the CP5 ask-then-yes approval flow, imported as a module so none of its own tests run here.
Every project uses a tiny stdlib-only Python "fixture" script as its test command (no real test framework, no
network, no sleeps): it reads FX_MODE (and FX_FORMAT) from the environment and writes the report Ratchet is
expected to pass it in RATCHET_TEST_REPORT.
"""
import json
import re
import sys
import _tmp
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import trust_helpers as tt  # noqa: E402  (rx, git, make_env, js, now_dt, cp3_state, engine helpers)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


rx = tt.rx
git = tt.git
js = tt.js
make_env = tt.make_env
now_dt = tt.now_dt

FIXTURE_REL = "tests/cp6_fixture.py"
CASE_A = "CP1-T01"  # the planned case under test in most fixtures
CASE_B = "CP1-T02"  # a control case that always reports "passed" when a report is written at all

# A deliberately non-f-string, non-raw template: every brace and quote in it is literal text for test_results.py's
# own parser. The single substitution (__IDS__) is done with str.replace, so the fixture's own use of f-strings,
# %-formatting and braces at ITS run time (inside the subprocess python3 that executes it) needs no escaping here.
FIXTURE_TEMPLATE = """#!/usr/bin/env python3
# Ratchet CP6 test fixture (not a real test runner). Case ids covered: __COMMENT__
import json
import os
import sys

IDS = __IDS__
MODE = os.environ.get("FX_MODE", "all-pass")
FMT = os.environ.get("FX_FORMAT", "json")
REPORT = os.environ.get("RATCHET_TEST_REPORT")


def _passed(i):
    return {"id": i, "status": "passed", "message": ""}


def _write_json(cases):
    if not REPORT:
        return
    with open(REPORT, "w") as f:
        json.dump({"cases": cases}, f)


def _write_junit(cases):
    if not REPORT:
        return
    parts = ["<testsuite>"]
    for c in cases:
        parts.append('<testcase classname="fx" name="[' + c["id"] + '] case">')
        if c["status"] == "failed":
            parts.append('<failure message="' + c.get("message", "fail") + '"></failure>')
        elif c["status"] == "error":
            parts.append('<error message="' + c.get("message", "error") + '"></error>')
        elif c["status"] == "skipped":
            parts.append("<skipped></skipped>")
        parts.append("</testcase>")
    parts.append("</testsuite>")
    with open(REPORT, "w") as f:
        f.write("".join(parts))


def _write(cases):
    if FMT == "junit":
        _write_junit(cases)
    else:
        _write_json(cases)


TARGET = IDS[0]
OTHERS = IDS[1:]

if MODE == "all-pass":
    _write([_passed(i) for i in IDS])
    sys.exit(0)
if MODE == "fail":
    cases = [{"id": TARGET, "status": "failed", "message": "assertion: expected 1 to equal 2"}] + [_passed(i) for i in OTHERS]
    _write(cases)
    sys.exit(1)
if MODE == "false-pass":
    # a suite that does NOT fail the process on an assertion failure: the report says failed, exit code is 0.
    cases = [{"id": TARGET, "status": "failed", "message": "assertion: expected 1 to equal 2"}] + [_passed(i) for i in OTHERS]
    _write(cases)
    sys.exit(0)
if MODE == "crash":
    cases = [{"id": TARGET, "status": "error", "message": "ImportError: missing module"}] + [_passed(i) for i in OTHERS]
    _write(cases)
    raise RuntimeError("simulated crash: missing import")
if MODE == "crash-nowrite":
    raise RuntimeError("simulated crash before any report is written")
if MODE == "skip":
    cases = [{"id": TARGET, "status": "skipped", "message": "skipped"}] + [_passed(i) for i in OTHERS]
    _write(cases)
    sys.exit(0)
if MODE == "missing":
    _write([_passed(i) for i in OTHERS])
    sys.exit(0)
if MODE == "no-report":
    sys.exit(0)
if MODE == "malformed":
    if REPORT:
        with open(REPORT, "w") as f:
            f.write('{"cases": [not valid json' if FMT != "junit" else "<testsuite><testcase")
    sys.exit(0)
if MODE == "print-path":
    print("REPORT_PATH=" + str(REPORT))
    sys.exit(0)
sys.exit(0)
"""


def fixture_source(ids):
    comment = " ".join(f"[{i}]" for i in ids)
    return FIXTURE_TEMPLATE.replace("__IDS__", repr(list(ids))).replace("__COMMENT__", comment)


def write_fixture(d, ids):
    (Path(d) / FIXTURE_REL).write_text(fixture_source(ids))


def cp6_project(env, report, cases, checks=None, command=None, report_reason=None):
    """A throwaway one-checkpoint project (mirrors tests/trust_helpers.py's cp3_project/project), approved into
    building, whose CP1 uses the stdlib-only fixture script as its test command.

    `report` is "json" / "junit" / "none" / None (the last two meaning "tests.report" is "none" with a reason, or
    the key is left out entirely, i.e. a plan from before this change)."""
    d = Path(_tmp.mkdtemp(prefix="ratchet-cp6-p-"))
    git(d, "init", "-q")
    git(d, "config", "user.email", "t@t")
    git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    tp = d / "t.jsonl"
    tp.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "please build the feature"},
                              "timestamp": now_dt().isoformat()}) + "\n")
    (d / ".ratchet").mkdir(exist_ok=True)
    rx(d, env, "hook", "prompt", "--host", "claude",
       stdin=json.dumps({"cwd": str(d), "prompt": "please build the feature", "session_id": "s-owner",
                         "transcript_path": str(tp)}))
    r = rx(d, env, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr + r.stdout
    cfg_sets = ["min_review_seconds=0", "providers=[]"]
    if checks:
        cfg_sets.append("checks=" + json.dumps(checks))
    r = rx(d, env, "config", "--set", *cfg_sets)
    assert r.returncode == 0, r.stderr + r.stdout
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    ids = [c["id"] for c in cases]
    write_fixture(d, ids)
    tests = {"files": [FIXTURE_REL], "command": command or f"python3 {FIXTURE_REL}"}
    if report in ("json", "junit"):
        tests["report"] = report
    elif report == "none":
        tests["report"] = "none"
        tests["report_reason"] = report_reason or "no structured per-test output from this command"
    # report is None: no "report" key at all, modelling a plan approved before this change.
    cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low", "tests": tests}
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                       "checkpoints": [cp]}))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": cases}))
    r = rx(d, env, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    rx(d, env, "ask", "approve-plan")
    tp.open("a").write(json.dumps({"type": "user", "message": {"role": "user", "content": "yes, go ahead"},
                                   "timestamp": now_dt().isoformat()}) + "\n")
    rx(d, env, "hook", "prompt", "--host", "claude",
       stdin=json.dumps({"cwd": str(d), "prompt": "yes, go ahead", "session_id": "s-owner",
                         "transcript_path": str(tp)}))
    r = rx(d, env, "approve-plan")
    assert r.returncode == 0, r.stderr + r.stdout
    return d, tp


def state(d):
    return tt.cp3_state(d)


def cp1(d):
    return state(d)["checkpoints"]["CP1"]


def fx_env(env, mode, fmt="json"):
    return dict(env, FX_MODE=mode, FX_FORMAT=fmt)


# ---- CP6 ----

def cp6_t01_genuine_failure_still_red():
    print("CP6: a genuine failing test still counts as red before the build")
    env = make_env("claude")
    d, tp = cp6_project(env, "json", [{"id": CASE_A, "layer": "code"}, {"id": CASE_B, "layer": "code"}])
    assert rx(d, env, "tests-written", "CP1").returncode == 0
    r = rx(d, fx_env(env, "fail"), "red", "CP1")
    check("[CP6-T01] a real per-case failure is accepted as the required red run",
          r.returncode == 0, (r.returncode, r.stdout, r.stderr))
    check("[CP6-T01] the checkpoint's step moves on to implement",
          cp1(d)["step"] == "implement", cp1(d).get("step"))


SECTIONS = [("CP6", cp6_t01_genuine_failure_still_red)]


def cp6_t02_crash_not_accepted_as_red():
    print("CP6: a crash before the build is not accepted as the required failure")
    env = make_env("claude")
    d, tp = cp6_project(env, "json", [{"id": CASE_A, "layer": "code"}, {"id": CASE_B, "layer": "code"}])
    assert rx(d, env, "tests-written", "CP1").returncode == 0
    r = rx(d, fx_env(env, "crash"), "red", "CP1")
    msg = r.stderr + r.stdout
    check("[CP6-T02] a crash (status 'error') is refused, not accepted as the required red failure",
          r.returncode != 0 and CASE_A in msg and re.search(r"crash|error", msg, re.I) is not None,
          (r.returncode, msg))
    check("[CP6-T02] the checkpoint's step stays at 'red', not 'implement'",
          cp1(d)["step"] == "red", cp1(d).get("step"))


SECTIONS.append(("CP6", cp6_t02_crash_not_accepted_as_red))


def _to_implement(env, d):
    assert rx(d, env, "tests-written", "CP1").returncode == 0
    r = rx(d, fx_env(env, "fail"), "red", "CP1")
    assert r.returncode == 0, r.stderr + r.stdout
    r = rx(d, env, "implemented", "CP1")
    assert r.returncode == 0, r.stderr + r.stdout


def cp6_t03_skip_fails_gate():
    print("CP6: a skipped planned case fails the behavior gate after the build")
    env = make_env("claude")
    d, tp = cp6_project(env, "json", [{"id": CASE_A, "layer": "code"}, {"id": CASE_B, "layer": "code"}])
    _to_implement(env, d)
    r = rx(d, fx_env(env, "skip"), "behavior", "CP1")
    msg = r.stderr + r.stdout
    check("[CP6-T03] behavior FAILS and names the skipped case id",
          r.returncode != 0 and CASE_A in msg and re.search(r"skip", msg, re.I) is not None, (r.returncode, msg))
    check("[CP6-T03] gates.behavior.status is 'failed'", cp1(d)["gates"]["behavior"]["status"] == "failed",
          cp1(d)["gates"]["behavior"])


SECTIONS.append(("CP6", cp6_t03_skip_fails_gate))


def cp6_t04_missing_case_fails_even_exit0():
    print("CP6: a planned case missing from the report fails the gate even with exit 0")
    env = make_env("claude")
    d, tp = cp6_project(env, "json", [{"id": CASE_A, "layer": "code"}, {"id": CASE_B, "layer": "code"}])
    _to_implement(env, d)
    r = rx(d, fx_env(env, "missing"), "behavior", "CP1")
    msg = r.stderr + r.stdout
    check("[CP6-T04] behavior FAILS and names the missing case id, despite exit code 0",
          r.returncode != 0 and CASE_A in msg, (r.returncode, msg))
    check("[CP6-T04] gates.behavior.status is 'failed'", cp1(d)["gates"]["behavior"]["status"] == "failed",
          cp1(d)["gates"]["behavior"])


SECTIONS.append(("CP6", cp6_t04_missing_case_fails_even_exit0))


def cp6_t05_comment_only_not_proven():
    print("CP6: a case id that only appears in a comment is not treated as proven")
    env = make_env("claude")
    # A single planned case: the fixture source always carries every id in its header comment (coverage-by-grep
    # would see it), but with FX_MODE "missing" and no other ids, the report has no entry for it at all.
    d, tp = cp6_project(env, "json", [{"id": CASE_A, "layer": "code"}])
    _to_implement(env, d)
    r = rx(d, fx_env(env, "missing"), "behavior", "CP1")
    msg = r.stderr + r.stdout
    check("[CP6-T05] behavior FAILS and names the case as not run, even though its id is in the test source",
          r.returncode != 0 and CASE_A in msg and re.search(r"not run|missing|no result|not reported", msg, re.I)
          is not None, (r.returncode, msg))
    check("[CP6-T05] gates.behavior.status is 'failed'", cp1(d)["gates"]["behavior"]["status"] == "failed",
          cp1(d)["gates"]["behavior"])


SECTIONS.append(("CP6", cp6_t05_comment_only_not_proven))


def cp6_t06_skip_added_after_red_is_flagged():
    print("CP6: a skip added after the red run is flagged even though the test file only grew")
    env = make_env("claude")
    d, tp = cp6_project(env, "json", [{"id": CASE_A, "layer": "code"}, {"id": CASE_B, "layer": "code"}])
    assert rx(d, env, "tests-written", "CP1").returncode == 0
    r = rx(d, fx_env(env, "fail"), "red", "CP1")
    assert r.returncode == 0, r.stderr + r.stdout  # red-run snapshot: CASE_A was a real failure
    assert rx(d, env, "implemented", "CP1").returncode == 0
    with (d / FIXTURE_REL).open("a") as f:
        f.write("\n# appended after the red run; no existing line above was changed or removed\n")
    r = rx(d, fx_env(env, "skip"), "behavior", "CP1")
    msg = r.stderr + r.stdout
    check("[CP6-T06] behavior FAILS, specifically naming the case as newly skipped since the red run",
          r.returncode != 0 and CASE_A in msg
          and re.search(r"skip", msg, re.I) is not None
          and re.search(r"since (the )?red( run)?|red run", msg, re.I) is not None, (r.returncode, msg))


SECTIONS.append(("CP6", cp6_t06_skip_added_after_red_is_flagged))


def cp6_t07_junit_all_pass():
    print("CP6: every planned case passing in a JUnit report passes the gate")
    env = make_env("claude")
    d, tp = cp6_project(env, "junit", [{"id": CASE_A, "layer": "code"}, {"id": CASE_B, "layer": "code"}])
    assert rx(d, env, "tests-written", "CP1").returncode == 0
    r = rx(d, fx_env(env, "fail", "junit"), "red", "CP1")
    assert r.returncode == 0, r.stderr + r.stdout
    assert rx(d, env, "implemented", "CP1").returncode == 0
    r = rx(d, fx_env(env, "all-pass", "junit"), "behavior", "CP1")
    check("[CP6-T07] behavior PASSES when the JUnit report shows every planned case passing",
          r.returncode == 0, (r.returncode, r.stdout, r.stderr))
    check("[CP6-T07] gates.behavior.status is 'passed'", cp1(d)["gates"]["behavior"]["status"] == "passed",
          cp1(d)["gates"]["behavior"])


SECTIONS.append(("CP6", cp6_t07_junit_all_pass))


def cp6_t08_json_all_pass():
    print("CP6: every planned case passing in Ratchet's JSON report passes the gate")
    env = make_env("claude")
    d, tp = cp6_project(env, "json", [{"id": CASE_A, "layer": "code"}, {"id": CASE_B, "layer": "code"}])
    _to_implement(env, d)
    r = rx(d, fx_env(env, "all-pass"), "behavior", "CP1")
    check("[CP6-T08] behavior PASSES when the JSON report shows every planned case passing",
          r.returncode == 0, (r.returncode, r.stdout, r.stderr))
    check("[CP6-T08] gates.behavior.status is 'passed'", cp1(d)["gates"]["behavior"]["status"] == "passed",
          cp1(d)["gates"]["behavior"])


SECTIONS.append(("CP6", cp6_t08_json_all_pass))


def cp6_t09_report_none_exit_code_only():
    print("CP6: a step with no report file is checked by exit code only, and status says so")
    reason = "shell smoke test writes no structured report"
    env = make_env("claude")
    d, tp = cp6_project(env, "none", [{"id": CASE_A, "layer": "code"}], command="test -f src/marker.ok",
                        report_reason=reason)
    assert rx(d, env, "tests-written", "CP1").returncode == 0
    r = rx(d, env, "red", "CP1")  # marker.ok absent: a real exit-code failure
    check("[CP6-T09] red (fail variant): a plain nonzero exit is still accepted as the required failure",
          r.returncode == 0, (r.returncode, r.stdout, r.stderr))
    assert rx(d, env, "implemented", "CP1").returncode == 0
    r = rx(d, env, "behavior", "CP1")
    check("[CP6-T09] behavior (fail variant): FAILS by exit code alone (marker not yet created)",
          r.returncode != 0, (r.returncode, r.stdout, r.stderr))
    (d / "src/marker.ok").write_text("ok")
    r = rx(d, env, "behavior", "CP1")
    check("[CP6-T09] behavior (pass variant): PASSES by exit code alone once the marker exists",
          r.returncode == 0 and cp1(d)["gates"]["behavior"]["status"] == "passed",
          (r.returncode, r.stdout, r.stderr, cp1(d)["gates"]["behavior"]))
    plain = rx(d, env, "status").stdout
    nxt = rx(d, env, "next").stdout
    for label, out in (("status", plain), ("next", nxt)):
        check(f"[CP6-T09] {label} plainly says CP1 has no per-test report, is checked by exit code only, "
              "and gives the reason",
              "CP1" in out and reason in out and re.search(r"exit code", out, re.I) is not None, out)


SECTIONS.append(("CP6", cp6_t09_report_none_exit_code_only))


def cp6_t10_legacy_plan_no_report_key():
    print("CP6: a legacy plan without a report field keeps the old exit-code check")
    env = make_env("claude")
    d, tp = cp6_project(env, None, [{"id": CASE_A, "layer": "code"}], command="test -f src/marker.ok")
    assert rx(d, env, "tests-written", "CP1").returncode == 0
    r = rx(d, env, "red", "CP1")
    check("[CP6-T10] red doesn't error about a missing report; a plain nonzero exit is accepted",
          r.returncode == 0 and re.search(r"report", r.stderr + r.stdout, re.I) is None,
          (r.returncode, r.stdout, r.stderr))
    assert rx(d, env, "implemented", "CP1").returncode == 0
    r = rx(d, env, "behavior", "CP1")
    check("[CP6-T10] behavior (fail variant): fails by exit code alone, no report demanded",
          r.returncode != 0 and re.search(r"report", r.stderr + r.stdout, re.I) is None,
          (r.returncode, r.stdout, r.stderr))
    (d / "src/marker.ok").write_text("ok")
    r = rx(d, env, "behavior", "CP1")
    check("[CP6-T10] behavior (pass variant): passes by exit code alone, no report demanded",
          r.returncode == 0 and cp1(d)["gates"]["behavior"]["status"] == "passed",
          (r.returncode, r.stdout, r.stderr, cp1(d)["gates"]["behavior"]))


SECTIONS.append(("CP6", cp6_t10_legacy_plan_no_report_key))


def cp6_t11_report_never_written_fails():
    print("CP6: a required report that was never written fails the gate, not silently trusted via exit code")
    env = make_env("claude")
    d, tp = cp6_project(env, "json", [{"id": CASE_A, "layer": "code"}])
    _to_implement(env, d)
    r = rx(d, fx_env(env, "no-report"), "behavior", "CP1")
    msg = r.stderr + r.stdout
    check("[CP6-T11] behavior FAILS: exit code 0 but no report was produced at the expected path",
          r.returncode != 0 and re.search(r"report", msg, re.I) is not None
          and re.search(r"no .*report|not (produced|written|found)", msg, re.I) is not None, (r.returncode, msg))
    check("[CP6-T11] gates.behavior.status is 'failed'", cp1(d)["gates"]["behavior"]["status"] == "failed",
          cp1(d)["gates"]["behavior"])


SECTIONS.append(("CP6", cp6_t11_report_never_written_fails))


def cp6_t12_malformed_report_fails_cleanly():
    print("CP6: a malformed report fails the gate with a clear message, not a crash")
    for report, fmt in (("json", "json"), ("junit", "junit")):
        env = make_env("claude")
        d, tp = cp6_project(env, report, [{"id": CASE_A, "layer": "code"}])
        _to_implement(env, d)
        r = rx(d, fx_env(env, "malformed", fmt), "behavior", "CP1")
        msg = r.stderr + r.stdout
        check(f"[CP6-T12] behavior FAILS cleanly on a malformed {report} report (no raw traceback)",
              r.returncode != 0 and "Traceback" not in r.stderr
              and re.search(r"malformed|unreadable|invalid|could not (read|parse)", msg, re.I) is not None,
              (r.returncode, msg))
        check(f"[CP6-T12] gates.behavior.status is 'failed' for the malformed {report} report",
              cp1(d)["gates"]["behavior"]["status"] == "failed", cp1(d)["gates"]["behavior"])


SECTIONS.append(("CP6", cp6_t12_malformed_report_fails_cleanly))


def cp6_t13_stale_report_not_reused():
    print("CP6: a stale report left over from a previous attempt is not reused")
    env = make_env("claude")
    d, tp = cp6_project(env, "json", [{"id": CASE_A, "layer": "code"}, {"id": CASE_B, "layer": "code"}])
    _to_implement(env, d)
    # Discover the exact path Ratchet passes in RATCHET_TEST_REPORT (printed by the fixture itself); this attempt
    # writes nothing and fails on exit code 0 with no report (CP6-T11's rule), but its log still names the path.
    r0 = rx(d, fx_env(env, "print-path"), "behavior", "CP1")
    log = sorted((d / ".ratchet/runs/f/evidence/CP1").glob("behavior-*.log"))[0]
    m = re.search(r"REPORT_PATH=(\S+)", log.read_text())
    assert m, f"fixture did not report its RATCHET_TEST_REPORT path: {log.read_text()!r}"
    report_path = Path(m.group(1))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({"cases": [{"id": CASE_A, "status": "passed"},
                                                  {"id": CASE_B, "status": "passed"}]}))
    r = rx(d, fx_env(env, "no-report"), "behavior", "CP1")  # exits 0, writes nothing: only the stale file remains
    msg = r.stderr + r.stdout
    check("[CP6-T13] a stale all-pass report from an earlier attempt is not treated as proof of this attempt",
          r.returncode != 0, (r0.returncode, r.returncode, msg))
    check("[CP6-T13] gates.behavior.status is 'failed', not fooled by the leftover file",
          cp1(d)["gates"]["behavior"]["status"] == "failed", cp1(d)["gates"]["behavior"])


SECTIONS.append(("CP6", cp6_t13_stale_report_not_reused))


def cp6_t14_checks_still_gate_on_exit_code():
    print("CP6: guardrail checks still gate on exit code alongside per-case test reading")
    env = make_env("claude")
    d, tp = cp6_project(env, "json", [{"id": CASE_A, "layer": "code"}],
                        checks=[{"kind": "guard", "command": "false"}])
    _to_implement(env, d)
    r = rx(d, fx_env(env, "all-pass"), "behavior", "CP1")
    msg = r.stderr + r.stdout
    check("[CP6-T14] behavior FAILS because of the failing check, even though every planned case genuinely passed",
          r.returncode != 0 and re.search(r"check", msg, re.I) is not None, (r.returncode, msg))
    check("[CP6-T14] gates.behavior.status is 'failed'", cp1(d)["gates"]["behavior"]["status"] == "failed",
          cp1(d)["gates"]["behavior"])


SECTIONS.append(("CP6", cp6_t14_checks_still_gate_on_exit_code))


OPENSPEC_MIN = {
    "openspec/changes/add-x/proposal.md": "# Add X\n\n## Why\nBecause the thing is needed.\n",
    "openspec/changes/add-x/tasks.md": "## 1. Group\n- [ ] 1.1 Do the thing\n",
    "openspec/changes/add-x/specs/x/spec.md": (
        "## ADDED Requirements\n\n### Requirement: Thing works\nThe system SHALL do the thing.\n\n"
        "#### Scenario: It works\n- **WHEN** it runs\n- **THEN** it works\n"),
}


def cp6_t15_spec_status_not_proven_on_false_exit0():
    print("CP6: spec status does not call a scenario proven on a false exit-0 pass")
    env = make_env("claude")
    d = Path(_tmp.mkdtemp(prefix="ratchet-cp6-spec-"))
    git(d, "init", "-q")
    git(d, "config", "user.email", "t@t")
    git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    for rel, text in OPENSPEC_MIN.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    tp = d / "t.jsonl"
    tp.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "please build the feature"},
                              "timestamp": now_dt().isoformat()}) + "\n")
    (d / ".ratchet").mkdir(exist_ok=True)
    rx(d, env, "hook", "prompt", "--host", "claude",
       stdin=json.dumps({"cwd": str(d), "prompt": "please build the feature", "session_id": "s-owner",
                         "transcript_path": str(tp)}))
    r = rx(d, env, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr + r.stdout
    r = rx(d, env, "config", "--set", "min_review_seconds=0", "providers=[]")
    assert r.returncode == 0, r.stderr + r.stdout
    r = rx(d, env, "spec", "import", "openspec", "add-x")
    assert r.returncode == 0, r.stderr + r.stdout
    run = d / ".ratchet/runs/f"
    plan = json.loads((run / "checkpoints.json").read_text())
    assert len(plan["checkpoints"]) == 1, plan["checkpoints"]
    cp = plan["checkpoints"][0]
    cases = json.loads((run / "tests" / f"{cp['id']}.json").read_text())["cases"]
    assert len(cases) == 1, cases
    case_id = cases[0]["id"]
    write_fixture(d, [case_id])
    cp["tests"] = {"files": [FIXTURE_REL], "command": f"python3 {FIXTURE_REL}", "report": "json"}
    plan["objective"] = _tmp.OBJECTIVE_MIN
    (run / "app-scout.json").write_text("{}")
    (run / "checkpoints.json").write_text(json.dumps(plan))
    r = rx(d, env, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    rx(d, env, "ask", "approve-plan")
    tp.open("a").write(json.dumps({"type": "user", "message": {"role": "user", "content": "yes, go ahead"},
                                   "timestamp": now_dt().isoformat()}) + "\n")
    rx(d, env, "hook", "prompt", "--host", "claude",
       stdin=json.dumps({"cwd": str(d), "prompt": "yes, go ahead", "session_id": "s-owner",
                         "transcript_path": str(tp)}))
    r = rx(d, env, "approve-plan")
    assert r.returncode == 0, r.stderr + r.stdout
    assert rx(d, env, "tests-written", cp["id"]).returncode == 0
    r = rx(d, dict(env, FX_MODE="fail"), "red", cp["id"])
    assert r.returncode == 0, r.stderr + r.stdout
    assert rx(d, env, "implemented", cp["id"]).returncode == 0
    r = rx(d, dict(env, FX_MODE="false-pass"), "behavior", cp["id"])
    msg = r.stderr + r.stdout
    check("[CP6-T15] behavior FAILS: the report says this case failed even though the process exited 0",
          r.returncode != 0, (r.returncode, msg))
    st = state(d)
    check("[CP6-T15] the checkpoint never completes", st["checkpoints"][cp["id"]].get("status") != "done",
          st["checkpoints"][cp["id"]])
    spec = js(rx(d, env, "spec", "status", "--json"))
    scenarios = (spec or {}).get("scenarios") or []
    check("[CP6-T15] spec status never marks any scenario 'proven' off this false exit-0 pass",
          scenarios != [] and all(s["state"] != "proven" for s in scenarios), spec)


SECTIONS.append(("CP6", cp6_t15_spec_status_not_proven_on_false_exit0))


# ---- CP6 adversarial round 1 (fixer) ----

def _x_planning_project(env):
    """Like cp6_project, but stops before plan-submit so a test can see plan-submit refuse a plan."""
    d = Path(_tmp.mkdtemp(prefix="ratchet-cp6-x-"))
    git(d, "init", "-q")
    git(d, "config", "user.email", "t@t")
    git(d, "config", "user.name", "t")
    (d / "tests").mkdir()
    (d / "README.md").write_text("x\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    tp = d / "t.jsonl"
    tp.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "please build the feature"},
                              "timestamp": now_dt().isoformat()}) + "\n")
    (d / ".ratchet").mkdir(exist_ok=True)
    rx(d, env, "hook", "prompt", "--host", "claude",
       stdin=json.dumps({"cwd": str(d), "prompt": "please build the feature", "session_id": "s-owner",
                         "transcript_path": str(tp)}))
    assert rx(d, env, "init", "feature", "--slug", "f").returncode == 0
    assert rx(d, env, "config", "--set", "min_review_seconds=0", "providers=[]").returncode == 0
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    write_fixture(d, [CASE_A])
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": CASE_A, "layer": "code"}]}))
    return d, run


def _x_submit(env, d, run, tests):
    cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low", "tests": tests}
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                       "checkpoints": [cp]}))
    return rx(d, env, "plan-submit")


def cp6_x01_plan_submit_checks_report_field():
    print("CP6 (fixer): plan-submit refuses a bad tests.report and a 'none' without a reason")
    env = make_env("claude")
    d, run = _x_planning_project(env)
    base = {"files": [FIXTURE_REL], "command": f"python3 {FIXTURE_REL}"}
    for bad in ("xml", "JUnit", ""):
        r = _x_submit(env, d, run, dict(base, report=bad))
        check(f"[CP6-X01] plan-submit refuses tests.report {bad!r} and says which values are allowed",
              r.returncode != 0 and "tests.report must be junit, json or none" in r.stderr, (r.returncode, r.stderr))
    for reason in (None, "", "   "):
        tests = dict(base, report="none")
        if reason is not None:
            tests["report_reason"] = reason
        r = _x_submit(env, d, run, tests)
        check(f"[CP6-X01] plan-submit refuses tests.report none with report_reason {reason!r}",
              r.returncode != 0 and "report_reason" in r.stderr, (r.returncode, r.stderr))
    r = _x_submit(env, d, run, dict(base, report="none", report_reason="a shell smoke test"))
    check("[CP6-X01] plan-submit accepts tests.report none with a reason", r.returncode == 0, (r.returncode, r.stderr))


SECTIONS.append(("CP6", cp6_x01_plan_submit_checks_report_field))


def cp6_x02_exit_code_only_is_said_plainly():
    print("CP6 (fixer): a step checked by exit code only says so in plan-summary, status and next")
    env = make_env("claude")
    d, tp = cp6_project(env, None, [{"id": CASE_A, "layer": "code"}], command="test -f src/marker.ok")
    s = rx(d, env, "plan-summary").stdout
    check("[CP6-X02] plan-summary: a step whose plan has no report key is shown as checked by exit code only",
          re.search(r"Tests: .*exit code only", s) is not None, s)
    st = rx(d, env, "status").stdout
    check("[CP6-X02] status (building): says the step is checked by exit code only",
          re.search(r"CP1.*exit code only", st) is not None, st)
    nj = js(rx(d, env, "next")) or {}
    check("[CP6-X02] next: test_check says the step is checked by exit code only",
          "exit code only" in str(nj.get("test_check")), nj.get("test_check"))
    reason = "the smoke script prints no per-test output"
    d2, _ = cp6_project(env, "none", [{"id": CASE_A, "layer": "code"}], command="true", report_reason=reason)
    s = rx(d2, env, "plan-summary").stdout
    check("[CP6-X02] plan-summary: a report none step shows exit code only with its reason",
          re.search(r"Tests: .*exit code only", s) is not None and reason in s, s)
    d3, _ = cp6_project(env, "junit", [{"id": CASE_A, "layer": "code"}])
    s = rx(d3, env, "plan-summary").stdout
    st3 = rx(d3, env, "status").stdout
    check("[CP6-X02] a junit step shows no exit-code-only line in plan-summary or status",
          "exit code only" not in s and "exit code only" not in st3, s + st3)


SECTIONS.append(("CP6", cp6_x02_exit_code_only_is_said_plainly))


def cp6_x03_early_exit_added_after_red_is_caught():
    print("CP6 (fixer): a return, skip or exit inserted into an existing test after red stops the gate")
    env = make_env("claude")
    d, tp = cp6_project(env, "json", [{"id": CASE_A, "layer": "code"}, {"id": CASE_B, "layer": "code"}])
    _to_implement(env, d)
    f = d / FIXTURE_REL
    orig = f.read_text()
    # the inserted lines are built from pieces so this file's own text never holds them whole
    variants = [
        ("a bare return as the first line of an existing body", "def _write_junit(cases):\n", "    re" + "turn\n"),
        ("an early exit at the top of an existing block", 'if MODE == "fail":\n', "    sys." + "exit(0)\n"),
        ("a skip marker before an existing function", None, "@pytest.mark." + "skip\n"),
    ]
    for label, anchor, line in variants:
        if anchor:
            new = orig.replace(anchor, anchor + line, 1)
        else:
            new = orig.replace("def _passed(i):\n", line + "def _passed(i):\n", 1)
        assert new != orig
        f.write_text(new)
        r = rx(d, fx_env(env, "all-pass"), "behavior", "CP1")
        msg = r.stderr + r.stdout
        check(f"[CP6-X03] {label}: behavior FAILS and names the test file, though every case reports passed",
              r.returncode != 0 and FIXTURE_REL in msg and "ends it early" in msg, (r.returncode, msg[-500:]))
    body_gone = orig.replace('    return {"id": i, "status": "passed", "message": ""}\n', "    pa" + "ss\n", 1)
    assert body_gone != orig
    f.write_text(body_gone)
    r = rx(d, fx_env(env, "all-pass"), "behavior", "CP1")
    check("[CP6-X03] replacing an existing body with pass is caught as a changed line",
          r.returncode != 0 and FIXTURE_REL in r.stderr and "since the red run" in r.stderr, r.stderr[-400:])
    f.write_text(orig + "\n\ndef _new_helper(x):\n    if x:\n        re" + "turn x\n    re" + "turn None\n")
    r = rx(d, fx_env(env, "all-pass"), "behavior", "CP1")
    check("[CP6-X03] a new helper appended after red may return freely: behavior passes",
          r.returncode == 0, (r.returncode, r.stderr[-400:]))


SECTIONS.append(("CP6", cp6_x03_early_exit_added_after_red_is_caught))


# A second stdlib-only fixture that writes a fixed report body per FX_MODE (JUnit or JSON, some with a UTF-8
# byte-order mark). It writes nothing when RATCHET_TEST_REPORT is not set, and always exits 0.
X_REPORTS = {
    "pytest-red": '<testsuite><testcase classname="tests.test_x" name="test_cp1_t01_rejects"><failure/></testcase>'
                  '<testcase classname="tests.test_x" name="test_other"><properties><property name="case" '
                  'value="CP1-T02"/></properties><failure/></testcase></testsuite>',
    "pytest-green": '<testsuite><testcase classname="tests.test_x" name="test_cp1_t01_rejects"/>'
                    '<testcase classname="tests.test_x" name="test_other"><properties><property name="case" '
                    'value="CP1-T02"/></properties></testcase></testsuite>',
    "go-green": '<testsuite><testcase classname="pkg" name="TestCP1T01Rejects"/>'
                '<testcase classname="pkg" name="TestCp1_T02"/></testsuite>',
    "near-miss": '<testsuite><testcase classname="x" name="test_cp11_t01"/><testcase classname="x" '
                 'name="test_cp1_t011"/><testcase classname="x" name="test_cp1_t02"/></testsuite>',
    "bom-json-red": '\ufeff{"cases": [{"id": "CP1-T01", "status": "failed"}, {"id": "CP1-T02", "status": "passed"}]}',
    "bom-json-green": '\ufeff{"cases": [{"id": "CP1-T01", "status": "passed"}, {"id": "CP1-T02", "status": "passed"}]}',
    "bom-junit-green": '\ufeff<testsuite><testcase name="[CP1-T01] a"/><testcase name="[CP1-T02] b"/></testsuite>',
    "unrelated-skip": '{"cases": [{"id": "CP1-T01", "status": "passed"}, {"id": "CP1-T02", "status": "passed"}, '
                      '{"id": "test_unrelated_optional_dep_case", "status": "skipped"}]}',
}


def write_x_fixture(d):
    src = ("# Ratchet CP6 fixer fixture: [CP1-T01] [CP1-T02]\nimport os\nREPORTS = " + repr(X_REPORTS) +
           "\nREPORT = os.environ.get('RATCHET_TEST_REPORT')\nif REPORT:\n"
           "    with open(REPORT, 'w', encoding='utf-8') as f:\n        f.write(REPORTS[os.environ['FX_MODE']])\n")
    (Path(d) / FIXTURE_REL).write_text(src)


def _x_report_project(env, report):
    d, tp = cp6_project(env, report, [{"id": CASE_A, "layer": "code"}, {"id": CASE_B, "layer": "code"}])
    write_x_fixture(d)
    assert rx(d, env, "tests-written", "CP1").returncode == 0
    return d


def cp6_x04_junit_ids_in_any_usual_spelling():
    print("CP6 (fixer): JUnit case ids are found in pytest, Go and property spellings")
    env = make_env("claude")
    d = _x_report_project(env, "junit")
    r = rx(d, fx_env(env, "pytest-red"), "red", "CP1")
    check("[CP6-X04] red accepts pytest-style names (test_cp1_t01_...) and an id in a JUnit property",
          r.returncode == 0, (r.returncode, r.stderr[-400:]))
    assert rx(d, env, "implemented", "CP1").returncode == 0
    r = rx(d, fx_env(env, "near-miss"), "behavior", "CP1")
    check("[CP6-X04] cp11_t01 and cp1_t011 are not taken for CP1-T01 (it did not run)",
          r.returncode != 0 and CASE_A in r.stderr and "did not run" in r.stderr, r.stderr[-400:])
    r = rx(d, fx_env(env, "go-green"), "behavior", "CP1")
    check("[CP6-X04] behavior passes with Go-style names (TestCP1T01..., TestCp1_T02)",
          r.returncode == 0, (r.returncode, r.stderr[-400:]))
    r = rx(d, fx_env(env, "pytest-green"), "behavior", "CP1")
    check("[CP6-X04] behavior passes with pytest-style names and a property id",
          r.returncode == 0 and cp1(d)["gates"]["behavior"]["status"] == "passed", (r.returncode, r.stderr[-400:]))


SECTIONS.append(("CP6", cp6_x04_junit_ids_in_any_usual_spelling))


def cp6_x05_junit_error_skip_missing():
    print("CP6 (fixer): JUnit <error>, <skipped> and a missing case are read like their JSON twins")
    env = make_env("claude")
    d, tp = cp6_project(env, "junit", [{"id": CASE_A, "layer": "code"}, {"id": CASE_B, "layer": "code"}])
    assert rx(d, env, "tests-written", "CP1").returncode == 0
    r = rx(d, fx_env(env, "crash", "junit"), "red", "CP1")
    check("[CP6-X05] a JUnit <error> is refused at red as a crash, not the required failure",
          r.returncode != 0 and CASE_A in r.stderr and "crashed" in r.stderr and cp1(d)["step"] == "red",
          (r.returncode, r.stderr[-400:]))
    r = rx(d, fx_env(env, "fail", "junit"), "red", "CP1")
    assert r.returncode == 0, r.stderr
    assert rx(d, env, "implemented", "CP1").returncode == 0
    r = rx(d, fx_env(env, "skip", "junit"), "behavior", "CP1")
    check("[CP6-X05] a JUnit <skipped> planned case fails behavior and is named",
          r.returncode != 0 and CASE_A in r.stderr and "skipped" in r.stderr, r.stderr[-400:])
    r = rx(d, fx_env(env, "missing", "junit"), "behavior", "CP1")
    check("[CP6-X05] a planned case missing from a JUnit report fails behavior despite exit 0",
          r.returncode != 0 and CASE_A in r.stderr and "did not run" in r.stderr, r.stderr[-400:])


SECTIONS.append(("CP6", cp6_x05_junit_error_skip_missing))


def cp6_x06_bom_and_unrelated_skip():
    print("CP6 (fixer): a report with a UTF-8 BOM is read; an unrelated skipped entry does not fail the gate")
    env = make_env("claude")
    d = _x_report_project(env, "json")
    r = rx(d, fx_env(env, "bom-json-red"), "red", "CP1")
    check("[CP6-X06] red reads a JSON report that starts with a UTF-8 BOM", r.returncode == 0,
          (r.returncode, r.stderr[-400:]))
    assert rx(d, env, "implemented", "CP1").returncode == 0
    r = rx(d, fx_env(env, "bom-json-green"), "behavior", "CP1")
    check("[CP6-X06] behavior reads a JSON report with a BOM", r.returncode == 0, (r.returncode, r.stderr[-400:]))
    r = rx(d, fx_env(env, "bom-junit-green"), "behavior", "CP1")
    check("[CP6-X06] behavior reads a JUnit report with a BOM", r.returncode == 0, (r.returncode, r.stderr[-400:]))
    r = rx(d, fx_env(env, "unrelated-skip"), "behavior", "CP1")
    check("[CP6-X06] a skipped entry that is not a planned case does not fail behavior",
          r.returncode == 0 and "newly skipped" not in r.stderr, (r.returncode, r.stderr[-400:]))


SECTIONS.append(("CP6", cp6_x06_bom_and_unrelated_skip))


def cp6_x07_report_path_stays_in_the_project():
    print("CP6 (fixer): the report path is absolute and inside the project; no test leaves files in the repo")
    env = make_env("claude")
    d, tp = cp6_project(env, "json", [{"id": CASE_A, "layer": "code"}])
    _to_implement(env, d)
    rx(d, fx_env(env, "print-path"), "behavior", "CP1")
    log = sorted((d / ".ratchet/runs/f/evidence/CP1").glob("behavior-*.log"))[0].read_text()
    m = re.search(r"REPORT_PATH=(\S+)", log)
    p = Path(m.group(1)) if m else None
    check("[CP6-X07] RATCHET_TEST_REPORT is an absolute path inside the project",
          p is not None and p.is_absolute() and Path(d).resolve() in p.resolve().parents, log[-300:])
    repo = HERE.parent
    check("[CP6-X07] no stray 'None' file in the repo root or the current folder",
          not (repo / "None").exists() and not (Path.cwd() / "None").exists(), sorted(x.name for x in repo.iterdir()))


SECTIONS.append(("CP6", cp6_x07_report_path_stays_in_the_project))


# ---- CP6 adversarial round 2: planned tests are frozen structurally after the red run ----

# A real (if tiny) Python test file: the case ids appear only inside the test functions, so each planned test is
# its own def. The runner at the bottom names report entries after the functions (test_cp1_t01 matches CP1-T01).
FROZEN_PY = '''import json
import os
import sys


def value():
    return 5


def test_cp1_t01():
    """[CP1-T01] the value is five"""
    x = value()
    assert x == 5


def test_cp1_t02():
    """[CP1-T02] this file can be read"""
    with open(__file__) as fh:
        data = fh.read()
    assert data


REPORT = os.environ.get("RATCHET_TEST_REPORT")
NAMES = sorted(n for n in list(globals()) if n.startswith("test_"))
FAIL_FIRST = os.environ.get("FX_MODE") == "fail"
if REPORT:
    with open(REPORT, "w") as f:
        json.dump({"cases": [{"id": n, "status": "failed" if FAIL_FIRST and n == NAMES[0] else "passed"}
                             for n in NAMES]}, f)
sys.exit(1 if FAIL_FIRST else 0)
'''


def cp6_x08_planned_python_test_is_frozen_after_red():
    print("CP6 (fixer, round 2): a planned Python test can't change or be redefined after red; new code can be added")
    env = make_env("claude")
    d, tp = cp6_project(env, "json", [{"id": CASE_A, "layer": "code"}, {"id": CASE_B, "layer": "code"}])
    f = d / FIXTURE_REL
    f.write_text(FROZEN_PY)
    _to_implement(env, d)
    r = rx(d, fx_env(env, "all-pass"), "behavior", "CP1")
    check("[CP6-X08] baseline: the untouched test file passes behavior", r.returncode == 0,
          (r.returncode, r.stderr[-400:]))
    ret = "re" + "turn"  # built from pieces so this file's own text never holds the inserted lines whole
    variants = [
        ("an `if True:` then `return` nested before the assertion", "test_cp1_t01",
         FROZEN_PY.replace("    x = value()\n", "    if True:\n        " + ret + "\n    x = value()\n", 1)),
        ("a return two levels deep before the assertion", "test_cp1_t01",
         FROZEN_PY.replace("    x = value()\n", "    if os:\n        if os:\n            " + ret + "\n    x = value()\n", 1)),
        ("a return at the end of a nested block, before a dedented assertion", "test_cp1_t02",
         FROZEN_PY.replace("        data = fh.read()\n", "        data = fh.read()\n        " + ret + "\n", 1)),
        ("a same-named empty test appended", "test_cp1_t01",
         FROZEN_PY + "\n\ndef test_cp1_t01():\n    pa" + "ss\n"),
        ("a same-named assignment appended", "test_cp1_t01", FROZEN_PY + "\ntest_cp1_t01 = lambda: None\n"),
    ]
    for label, name, new in variants:
        assert new != FROZEN_PY
        f.write_text(new)
        r = rx(d, fx_env(env, "all-pass"), "behavior", "CP1")
        msg = r.stderr + r.stdout
        check(f"[CP6-X08] {label}: behavior is refused, naming the test and saying it can't change after its first run",
              r.returncode != 0 and FIXTURE_REL in msg and name in msg and "can't change after its first run" in msg
              and "approve-changes" in msg, (r.returncode, msg[-600:]))
    added = FROZEN_PY.replace("def test_cp1_t01():", "def helper(x):\n    if x:\n        " + ret + " x\n    " + ret
                              + " 0\n\n\ndef test_cp1_t01():", 1)
    added += "\n\ndef test_cp1_t03_extra():\n    if True:\n        " + ret + "\n    assert value() == 5\n"
    f.write_text(added)
    r = rx(d, fx_env(env, "all-pass"), "behavior", "CP1")
    check("[CP6-X08] a new helper between planned tests and a new test at the end are accepted: behavior passes",
          r.returncode == 0, (r.returncode, r.stderr[-400:]))


SECTIONS.append(("CP6", cp6_x08_planned_python_test_is_frozen_after_red))


FROZEN_JS = """import t from 'node:test';
import a from 'node:assert';

describe('suite', () => {
  it('[CP1-T01] works', () => {
    const x = f();
    a.equal(x, 1);
  });

  it('[CP1-T02] other', () => {
    a.ok(f());
  });
});
"""

FROZEN_GO = """package x

func TestCP1T01(t *testing.T) {
\tif f() != 1 {
\t\tt.Fatal("no")
\t}
}

func TestCP1T02(t *testing.T) {
\tif f() == 0 {
\t\tt.Fatal("zero")
\t}
}
"""


def cp6_x09_planned_test_block_is_frozen_in_other_languages():
    print("CP6 (fixer, round 2): a planned JS or Go test block can't change or be duplicated after red")
    m = tt.engine()
    ids = [CASE_A, CASE_B]
    ret = "re" + "turn"
    refused = [
        ("JS: `if (true) {` / `return;` / `}` split over lines inside a planned test", "tests/a.test.js", FROZEN_JS,
         FROZEN_JS.replace("    const x = f();\n", "    if (true) {\n      " + ret + ";\n    }\n    const x = f();\n", 1),
         "[CP1-T01] works"),
        ("JS: a one-line `if (true) { return; }` inside a planned test", "tests/a.test.js", FROZEN_JS,
         FROZEN_JS.replace("    a.ok(f());\n", "    if (true) { " + ret + "; }\n    a.ok(f());\n", 1),
         "[CP1-T02] other"),
        ("JS: a same-named empty test appended", "tests/a.test.js", FROZEN_JS,
         FROZEN_JS[:-4] + "\n  it('[CP1-T01] works', () => {});\n});\n", "[CP1-T01] works"),
        ("Go: a return before the assertion of a planned test", "x_test.go", FROZEN_GO,
         FROZEN_GO.replace("\tif f() != 1 {", "\t" + ret + "\n\tif f() != 1 {", 1), "TestCP1T01"),
        ("Go: a same-named empty test appended", "x_test.go", FROZEN_GO,
         FROZEN_GO + "\nfunc TestCP1T02(t *testing.T) {}\n", "TestCP1T02"),
        ("JS: a new helper inserted at the top of the file (inside the red content)", "tests/a.test.js", FROZEN_JS,
         "function helper() { " + ret + " 1; }\n" + FROZEN_JS, "[CP1-T01] works"),
        ("JS: a new test inserted inside the describe (before the red content's last line)", "tests/a.test.js",
         FROZEN_JS, FROZEN_JS[:-4] + "\n  it('[CP1-T03] new', () => { " + ret + "; });\n});\n", "[CP1-T01] works"),
    ]
    for label, rel, old, new, name in refused:
        assert new != old
        got = m._frozen_test_problems(rel, old, new, ids)
        check(f"[CP6-X09] {label}: refused, naming the test", bool(got) and name in got[0], got)
    accepted = [
        ("JS: a new test (with a return) and a new helper appended at the end", "tests/a.test.js", FROZEN_JS,
         FROZEN_JS + "\nfunction helper() { " + ret + " 1; }\n\nit('[CP1-T03] new', () => { " + ret + "; });\n"),
        ("JS: a new test reusing a case id under a new name, appended at the end", "tests/a.test.js", FROZEN_JS,
         FROZEN_JS + "\nit('[CP1-T01] an extra edge', () => a.ok(true));\n"),
        ("Go: a new test function", "x_test.go", FROZEN_GO, FROZEN_GO + "\nfunc TestNew(t *testing.T) { " + ret + " }\n"),
    ]
    for label, rel, old, new, in accepted:
        got = m._frozen_test_problems(rel, old, new, ids)
        check(f"[CP6-X09] {label}: accepted", got == [], got)


SECTIONS.append(("CP6", cp6_x09_planned_test_block_is_frozen_in_other_languages))


# ---- CP6 adversarial round 3: outside Python a planned id must be on its test's opening line ----

def _x_files_project(env, files):
    """A one-checkpoint project in building whose CP1 (one planned case, CASE_A) has the given tests.files."""
    d, run = _x_planning_project(env)
    r = _x_submit(env, d, run, {"files": files, "command": "true"})
    assert r.returncode == 0, r.stderr + r.stdout
    tp = d / "t.jsonl"
    rx(d, env, "ask", "approve-plan")
    tp.open("a").write(json.dumps({"type": "user", "message": {"role": "user", "content": "yes, go ahead"},
                                   "timestamp": now_dt().isoformat()}) + "\n")
    rx(d, env, "hook", "prompt", "--host", "claude",
       stdin=json.dumps({"cwd": str(d), "prompt": "yes, go ahead", "session_id": "s-owner",
                         "transcript_path": str(tp)}))
    r = rx(d, env, "approve-plan")
    assert r.returncode == 0, r.stderr + r.stdout
    return d


INNER_GO = """package x

func TestSomething(t *testing.T) {
\tsetup()
\t// case CP1-T01
\tassertResult(t, result, 5)
}
"""

INNER_JS = """describe('suite', () => {
  it('does the thing', () => {
    setup();
    // CP1-T01: the value is five
    a.equal(f(), 5);
  });
});
"""


def cp6_x10_id_only_in_a_comment_is_refused_at_tests_written():
    print("CP6 (fixer, round 3): outside Python, an id only in a comment is refused at tests-written and red")
    env = make_env("claude")
    d = _x_files_project(env, ["tests/a.test.js", "tests/x_test.go"])
    js_f, go_f = d / "tests/a.test.js", d / "tests/x_test.go"
    bad = [
        ("JS: the id only in a comment inside the test body", js_f, INNER_JS),
        ("Go: the id only in a comment inside the test body", go_f, INNER_GO),
        ("Go: the id only in a comment directly above the func", go_f,
         "package x\n\n// [CP1-T01] the value is five\nfunc TestFive(t *testing.T) {\n\tassertResult(t, f(), 5)\n}\n"),
        ("JS: the id only in a trailing comment on a code line", js_f,
         "it('does the thing', () => {\n  a.equal(f(), 5); // [CP1-T01]\n});\n"),
    ]
    for label, f, text in bad:
        for p in (js_f, go_f):
            if p.exists():
                p.unlink()
        f.write_text(text)
        r = rx(d, env, "tests-written", "CP1")
        msg = r.stderr + r.stdout
        check(f"[CP6-X10] {label}: tests-written is refused with a plain message",
              r.returncode != 0 and CASE_A in msg and "only inside a comment" in msg
              and "line that opens its test" in msg, (r.returncode, msg[-500:]))
        check(f"[CP6-X10] {label}: the step stays at tests", cp1(d)["step"] == "tests", cp1(d).get("step"))
    go_f.unlink(missing_ok=True)
    js_f.write_text(INNER_JS.replace("it('does the thing'", "it('[CP1-T01] does the thing'", 1))
    r = rx(d, env, "tests-written", "CP1")
    check("[CP6-X10] JS: the id in the it(...) name on its opening line is accepted",
          r.returncode == 0 and cp1(d)["step"] == "red", (r.returncode, r.stderr[-400:]))
    js_f.unlink()
    go_f.write_text(INNER_GO.replace("TestSomething", "TestCP1_T01", 1).replace("\t// case CP1-T01\n", "", 1))
    r = rx(d, env, "tests-written", "CP1")
    check("[CP6-X10] Go: func TestCP1_T01 alone (no hyphenated id anywhere) is accepted",
          r.returncode == 0 and cp1(d)["step"] == "red", (r.returncode, r.stderr[-400:]))
    go_f.write_text(INNER_GO)
    r = rx(d, env, "red", "CP1")
    msg = r.stderr + r.stdout
    check("[CP6-X10] red refuses too when the id was moved into a comment after tests-written",
          r.returncode != 0 and "only inside a comment" in msg and cp1(d)["step"] == "red", (r.returncode, msg[-400:]))


SECTIONS.append(("CP6", cp6_x10_id_only_in_a_comment_is_refused_at_tests_written))


def cp6_x11_declared_test_freezes_code_before_an_inner_comment():
    print("CP6 (fixer, round 3): an id on the opening line freezes the whole test, code before an inner comment too")
    m = tt.engine()
    ret = "re" + "turn"
    go = INNER_GO.replace("TestSomething", "TestCP1_T01", 1)
    js_ = INNER_JS.replace("it('does the thing'", "it('[CP1-T01] does the thing'", 1)
    refused = [
        ("Go: an `if true {` / return / `}` before the inner comment", "x_test.go", go,
         go.replace("\tsetup()\n", "\tsetup()\n\tif true {\n\t\t" + ret + "\n\t}\n", 1), "TestCP1_T01"),
        ("JS: an `if (true) { return; }` before the inner comment", "tests/a.test.js", js_,
         js_.replace("    setup();\n", "    setup();\n    if (true) { " + ret + "; }\n", 1), "[CP1-T01] does the thing"),
    ]
    for label, rel, old, new, name in refused:
        assert new != old
        got = m._frozen_test_problems(rel, old, new, [CASE_A])
        check(f"[CP6-X11] {label}: refused, naming the test", bool(got) and name in got[0], got)
    for label, rel, old in (("Go", "x_test.go", INNER_GO), ("JS", "tests/a.test.js", INNER_JS)):
        check(f"[CP6-X11] {label}: an id only in an inner comment opens no test",
              m._test_decls(old.splitlines(), m._case_id_re(CASE_A)) == [], old)


SECTIONS.append(("CP6", cp6_x11_declared_test_freezes_code_before_an_inner_comment))


HEADER_CLASS_PY = '''# Tests for CP1-T01 and CP1-T02
import unittest


class TestValue(unittest.TestCase):
    def test_cp1_t01(self):
        x = 5
        self.assertEqual(x, 5)

    def test_cp1_t02(self):
        self.assertTrue(True)
'''


def cp6_x12_header_ids_do_not_freeze_the_whole_python_file():
    print("CP6 (fixer, round 3): ids that name test functions and also sit in a header freeze just those functions")
    m = tt.engine()
    ids = [CASE_A, CASE_B]
    ret = "re" + "turn"
    added = HEADER_CLASS_PY + "\n    def test_cp6_x01_new_case(self):\n        self.assertEqual(2 + 2, 4)\n"
    got = m._frozen_test_problems("tests/test_v.py", HEADER_CLASS_PY, added, ids)
    check("[CP6-X12] a new method added to the existing test class is accepted", got == [], got)
    early = HEADER_CLASS_PY.replace("        x = 5\n", "        if True:\n            " + ret + "\n        x = 5\n", 1)
    got = m._frozen_test_problems("tests/test_v.py", HEADER_CLASS_PY, early, ids)
    check("[CP6-X12] a return inserted into test_cp1_t01 is still refused",
          bool(got) and "test_cp1_t01" in got[0], got)


SECTIONS.append(("CP6", cp6_x12_header_ids_do_not_freeze_the_whole_python_file))


FROZEN_JAVA = """class CalcTest {
    @DisplayName("[CP1-T01] adds two numbers")
    // the main case
    @Test
    void adds() {
        assertEquals(3, add(1, 2));
    }
}
"""

FROZEN_RB = """describe "calc" do
  it "[CP1-T01] adds" do
    expect(add(1, 2)).to eq(3)
  end
end
"""


def cp6_x13_remaining_documented_freeze_rules():
    print("CP6 (fixer, round 3): header-only ids, an annotation above a declaration, and Ruby `end` are all frozen")
    m = tt.engine()
    ret = "re" + "turn"
    src = fixture_source([CASE_A, CASE_B])
    again = src + "\n\ndef _passed(i):\n    " + ret + " {}\n"
    got = m._frozen_test_problems(FIXTURE_REL, src, again, [CASE_A, CASE_B])
    check("[CP6-X13] Python, ids only in a header: an old top-level def defined again is refused",
          bool(got) and "_passed defined again" in got[0], got)
    refused = [
        ("Java: an edit inside a method whose @DisplayName (above @Test and a comment) holds the id",
         "src/CalcTest.java", FROZEN_JAVA,
         FROZEN_JAVA.replace("        assertEquals", "        if (true) " + ret + ";\n        assertEquals", 1),
         '"[CP1-T01] adds two numbers"'),
        ("Ruby: an edit inside an it ... do ... end block", "spec/calc_spec.rb", FROZEN_RB,
         FROZEN_RB.replace("    expect", "    " + ret + "\n    expect", 1), '"[CP1-T01] adds"'),
        ("Ruby: a line added before the block's own `end`", "spec/calc_spec.rb", FROZEN_RB,
         FROZEN_RB.replace("    expect(add(1, 2)).to eq(3)\n", "    expect(add(1, 2)).to eq(3)\n  sk" + "ip\n", 1),
         '"[CP1-T01] adds"'),
    ]
    for label, rel, old, new, name in refused:
        assert new != old
        got = m._frozen_test_problems(rel, old, new, [CASE_A])
        check(f"[CP6-X13] {label}: refused, naming the test", bool(got) and name in got[0], got)
    inside = FROZEN_RB.replace("  end\nend\n", "  end\n\n  it \"adds more\" do\n    " + ret + "\n  end\nend\n", 1)
    got = m._frozen_test_problems("spec/calc_spec.rb", FROZEN_RB, inside, [CASE_A])
    check("[CP6-X13] Ruby: a new test inserted before the file's last `end` (inside the red content) is refused",
          bool(got) and '"[CP1-T01] adds"' in got[0], got)
    after = FROZEN_RB + "\ndescribe \"more\" do\n  it \"adds more\" do\n    " + ret + "\n  end\nend\n"
    got = m._frozen_test_problems("spec/calc_spec.rb", FROZEN_RB, after, [CASE_A])
    check("[CP6-X13] Ruby: a new test appended after the file's last `end` is accepted", got == [], got)


SECTIONS.append(("CP6", cp6_x13_remaining_documented_freeze_rules))


# ---- CP6 adversarial round 4: outside Python the red file must stay an exact prefix (append-only) ----

ALLMAN_CS = """public class CalcTests
{
    [Fact]
    public void CP1_T01_Adds()
    {
        Assert.Equal(3, Add(1, 2));
    }
}
"""

PSR12_PHP = """<?php
final class CalcTest extends TestCase
{
    public function testCP1_T01Adds(): void
    {
        $this->assertSame(3, add(1, 2));
    }
}
"""

ATTR_CS = """public class CalcTests {
    [Fact(DisplayName = "[CP1-T01] adds")]
    public void Adds() {
        Assert.Equal(3, Add(1, 2));
    }
}
"""

TEMPLATE_JS = "it('[CP1-T01] builds query', () => {\n  const q = `\nSELECT * FROM t\nWHERE id = 1\n`;\n" \
              "  expect(run(q)).toBe(1);\n});\n"


def cp6_x14_non_python_red_file_is_append_only():
    print("CP6 (fixer, round 4): outside Python a test file with planned tests may only grow at its end")
    m = tt.engine()
    ret = "re" + "turn"
    refused = [
        ("C# Allman: an early return as the first statement of the planned method", "CalcTests.cs", ALLMAN_CS,
         ALLMAN_CS.replace("    {\n        Assert", "    {\n        " + ret + ";\n        Assert", 1)),
        ("C# Allman: the assertion edited", "CalcTests.cs", ALLMAN_CS, ALLMAN_CS.replace("Equal(3,", "Equal(4,", 1)),
        ("PHP PSR-12: an early return in the planned method", "tests/CalcTest.php", PSR12_PHP,
         PSR12_PHP.replace("    {\n        $this", "    {\n        " + ret + ";\n        $this", 1)),
        ("C# DisplayName attribute: an early return in the planned method", "CalcTests.cs", ATTR_CS,
         ATTR_CS.replace("    public void Adds() {\n", "    public void Adds() {\n        " + ret + ";\n", 1)),
        ("C# DisplayName attribute: the assertion edited", "CalcTests.cs", ATTR_CS,
         ATTR_CS.replace("Equal(3,", "Equal(4,", 1)),
        ("JS multi-line template literal: the assertion after it edited", "tests/q.test.js", TEMPLATE_JS,
         TEMPLATE_JS.replace("toBe(1)", "toBe(999)", 1)),
        ("JS multi-line template literal: an early return after it", "tests/q.test.js", TEMPLATE_JS,
         TEMPLATE_JS.replace("`;\n  expect", "`;\n  " + ret + ";\n  expect", 1)),
        ("JS: a line appended onto the end of a red file with no final newline", "tests/q.test.js",
         TEMPLATE_JS.rstrip("\n"), TEMPLATE_JS.rstrip("\n") + " it('x', () => {});\n"),
    ]
    for label, rel, old, new in refused:
        assert new != old
        got = m._frozen_test_problems(rel, old, new, [CASE_A])
        check(f"[CP6-X14] {label}: refused", bool(got) and "changed since the red run" in got[0]
              and "added at its end" in got[0], got)
    accepted = [
        ("C#: a new test class appended at the end", "CalcTests.cs", ALLMAN_CS,
         ALLMAN_CS + "\npublic class MoreTests\n{\n    [Fact]\n    public void New() { " + ret + "; }\n}\n"),
        ("PHP: a new test class appended at the end", "tests/CalcTest.php", PSR12_PHP,
         PSR12_PHP + "\nfinal class MoreTest extends TestCase\n{\n    public function testNew(): void {}\n}\n"),
        ("JS: a new test with its own id appended at the end", "tests/q.test.js", TEMPLATE_JS,
         TEMPLATE_JS + "\nit('[CP6-X01] another', () => { " + ret + "; });\n"),
    ]
    for label, rel, old, new in accepted:
        got = m._frozen_test_problems(rel, old, new, [CASE_A])
        check(f"[CP6-X14] {label}: accepted", got == [], got)
    dup = [
        ("JS: the planned test's name declared again at the end (same quotes)", "tests/q.test.js", TEMPLATE_JS,
         TEMPLATE_JS + "it('[CP1-T01] builds query', () => {});\n", "[CP1-T01] builds query"),
        ("JS: the planned test's name declared again at the end (other quotes)", "tests/q.test.js", TEMPLATE_JS,
         TEMPLATE_JS + 'it("[CP1-T01] builds query", () => {});\n', "[CP1-T01] builds query"),
        ("C#: the planned method name declared again at the end", "CalcTests.cs", ALLMAN_CS,
         ALLMAN_CS + "public partial class CalcTests { public void CP1_T01_Adds() {} }\n", "CP1_T01_Adds"),
    ]
    for label, rel, old, new, name in dup:
        got = m._frozen_test_problems(rel, old, new, [CASE_A])
        check(f"[CP6-X14] {label}: refused, naming it", bool(got) and name in got[0] and "declared again" in got[0],
              got)


SECTIONS.append(("CP6", cp6_x14_non_python_red_file_is_append_only))


X_DUP_REPORTS = {
    "red": '<testsuite><testcase name="[CP1-T01] a"><failure/></testcase><testcase name="[CP1-T02] b"/></testsuite>',
    "dup-red": '<testsuite><testcase name="[CP1-T01] a"><failure/></testcase><testcase name="[CP1-T01] a"/>'
               '<testcase name="[CP1-T02] b"/></testsuite>',
    "dup-green": '<testsuite><testcase name="[CP1-T01] a"/><testcase name="[CP1-T01] a"/>'
                 '<testcase name="[CP1-T02] b"/></testsuite>',
    "param-green": '<testsuite><testcase name="test_cp1_t01[one]"/><testcase name="test_cp1_t01[two]"/>'
                   '<testcase name="[CP1-T02] b"/></testsuite>',
}


def cp6_x15_planned_case_reported_twice_fails_the_gate():
    print("CP6 (fixer, round 4): two results with the same name for one planned case fail red and behavior")
    env = make_env("claude")
    d, tp = cp6_project(env, "junit", [{"id": CASE_A, "layer": "code"}, {"id": CASE_B, "layer": "code"}])
    src = ("# Ratchet CP6 round-4 fixture: [CP1-T01] [CP1-T02]\nimport os\nREPORTS = " + repr(X_DUP_REPORTS) +
           "\nREPORT = os.environ.get('RATCHET_TEST_REPORT')\nif REPORT:\n"
           "    with open(REPORT, 'w', encoding='utf-8') as f:\n        f.write(REPORTS[os.environ['FX_MODE']])\n")
    (d / FIXTURE_REL).write_text(src)
    assert rx(d, env, "tests-written", "CP1").returncode == 0
    r = rx(d, fx_env(env, "dup-red"), "red", "CP1")
    msg = r.stderr + r.stdout
    check("[CP6-X15] red is refused when a planned case has two results with the same name",
          r.returncode != 0 and f"planned case {CASE_A} ran more than once" in msg and cp1(d)["step"] == "red",
          (r.returncode, msg[-400:]))
    assert rx(d, fx_env(env, "red"), "red", "CP1").returncode == 0
    assert rx(d, env, "implemented", "CP1").returncode == 0
    r = rx(d, fx_env(env, "dup-green"), "behavior", "CP1")
    msg = r.stderr + r.stdout
    check("[CP6-X15] behavior is refused when a planned case has two results with the same name",
          r.returncode != 0 and f"planned case {CASE_A} ran more than once" in msg, (r.returncode, msg[-400:]))
    r = rx(d, fx_env(env, "param-green"), "behavior", "CP1")
    check("[CP6-X15] differently named results for one case (parametrized tests, subtests) still pass behavior",
          r.returncode == 0, (r.returncode, r.stderr[-400:]))


SECTIONS.append(("CP6", cp6_x15_planned_case_reported_twice_fails_the_gate))


def cp6_x16_red_names_a_deleted_id_as_not_present():
    print("CP6 (fixer, round 4): red says a planned id deleted from every test file is not present")
    env = make_env("claude")
    d = _x_files_project(env, ["tests/a.test.js"])
    js_f = d / "tests/a.test.js"
    js_f.write_text("it('[CP1-T01] does the thing', () => {\n  a.equal(f(), 5);\n});\n")
    assert rx(d, env, "tests-written", "CP1").returncode == 0
    js_f.write_text("it('does the thing', () => {\n  a.equal(f(), 5);\n});\n")
    r = rx(d, env, "red", "CP1")
    msg = r.stderr + r.stdout
    check("[CP6-X16] red refuses an id deleted everywhere as not present, not as only inside a comment",
          r.returncode != 0 and "not present" in msg and CASE_A in msg and "only inside a comment" not in msg
          and cp1(d)["step"] == "red", (r.returncode, msg[-400:]))
    js_f.unlink()
    r = rx(d, env, "red", "CP1")
    msg = r.stderr + r.stdout
    check("[CP6-X16] red refuses a deleted test file the same way",
          r.returncode != 0 and "not present" in msg and "only inside a comment" not in msg, (r.returncode, msg[-400:]))


SECTIONS.append(("CP6", cp6_x16_red_names_a_deleted_id_as_not_present))


X_JSON_DUP_REPORTS = {
    "red": json.dumps({"cases": [{"id": CASE_A, "status": "failed"}, {"id": CASE_B, "status": "passed"}]}),
    "dup-red": json.dumps({"cases": [{"id": CASE_A, "status": "failed"}, {"id": CASE_A, "status": "failed"},
                                      {"id": CASE_B, "status": "passed"}]}),
}


def cp6_x17_json_duplicate_case_id_message_gives_correct_advice():
    print("CP6 (fixer, round 5): a JSON report where two results share a planned case's id names the duplicate "
          "and does not tell the writer to invent a new case id")
    env = make_env("claude")
    d, tp = cp6_project(env, "json", [{"id": CASE_A, "layer": "code"}, {"id": CASE_B, "layer": "code"}])
    src = ("# Ratchet CP6 round-5 fixture: [CP1-T01] [CP1-T02]\nimport os\nREPORTS = " + repr(X_JSON_DUP_REPORTS) +
           "\nREPORT = os.environ.get('RATCHET_TEST_REPORT')\nif REPORT:\n"
           "    with open(REPORT, 'w', encoding='utf-8') as f:\n        f.write(REPORTS[os.environ['FX_MODE']])\n")
    (d / FIXTURE_REL).write_text(src)
    assert rx(d, env, "tests-written", "CP1").returncode == 0
    r = rx(d, fx_env(env, "dup-red"), "red", "CP1")
    msg = r.stderr + r.stdout
    check("[CP6-X17] red names the duplicate id and tells the writer to give each result its own name, not to "
          "invent a fresh case id",
          r.returncode != 0 and f"planned case {CASE_A} ran more than once" in msg
          and f"two results are named '{CASE_A}'" in msg and "give each test its own result name" in msg
          and "give a new test its own case id)" not in msg, (r.returncode, msg[-500:]))


SECTIONS.append(("CP6", cp6_x17_json_duplicate_case_id_message_gives_correct_advice))


def cp6_x18_skip_after_red_is_named_once():
    print("CP6 (fixer, round 7): a planned case skipped after the red run is named once, and red stores no skip list")
    env = make_env("claude")
    d, tp = cp6_project(env, "json", [{"id": CASE_A, "layer": "code"}, {"id": CASE_B, "layer": "code"}])
    _to_implement(env, d)
    check("[CP6-X18] the red record keeps no unused list of skipped results",
          "skipped" not in (cp1(d).get("red") or {}), cp1(d).get("red"))
    r = rx(d, fx_env(env, "skip"), "behavior", "CP1")
    msg = r.stderr + r.stdout
    check("[CP6-X18] behavior fails and names the newly skipped case exactly once, saying it was not skipped at red",
          r.returncode != 0 and msg.count(f"{CASE_A} was skipped") == 1 and "newly skipped" not in msg
          and "not skipped at the red run" in msg, (r.returncode, msg[-400:]))


SECTIONS.append(("CP6", cp6_x18_skip_after_red_is_named_once))


def main():
    global FAIL
    for name, fn in SECTIONS:
        try:
            fn()
        except Exception:
            FAIL += 1
            print(f"  ✗ [{name}] section crashed\n{traceback.format_exc()}")
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
