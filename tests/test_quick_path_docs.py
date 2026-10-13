#!/usr/bin/env python3
"""
CP2 tests: doc edits after review skip re-review.   python3 tests/test_quick_path_docs.py

When only doc files changed after a step's reviewers approved, `ratchet.py complete` must commit the step
without a new adversarial round, re-running only the test files that name the edited doc(s), and record the
edit as not re-reviewed (commit message, status, final review). Any code change in the mix (including a
SKILL.md/agents/*.md edit, which never counts as docs) still marks the gates stale exactly as before this
feature. The full regression suite is skipped at `complete` only when nothing but docs changed since it last
passed; any real code change since then still runs it.

Reuses tests/test_gates.py (`project`, `approve_plan`, `to_adversarial`, `open_round`, `R0`, `write_review`,
`rx`, `git`, `say`, `ENGINE`, `ENV`) for the throwaway git project and the real-CLI-subprocess way every suite
in this repo drives the engine. `doc_test_command` is a tiny fake Python script this file writes (never
tests/run_all.py or any real suite): it logs every file path it is called with to a log file (so a case can
tell exactly which test files ran), then fails if any doc file mentioned inside those files is missing a
marker string -- so a case can "break" a doc by deleting the marker.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP2-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import re
import shlex
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                     # noqa: E402
import test_gates as g          # noqa: E402  (project, approve_plan, to_adversarial, open_round, R0, write_review, rx, git, say)

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

MARKER = "DOC_MARKER_OK"

# A tiny fake doc_test_command: logs every file path it is called with (one '|'-joined line per invocation) to
# the log path given as its first argument, then fails if any doc file mentioned inside those files (a plain
# *.md/*.mdx/*.rst/*.adoc path found in the file's own text) is missing MARKER. Never a real test runner.
_DOC_TEST_SRC = (
    "import sys, re, pathlib\n"
    "log_path = pathlib.Path(sys.argv[1])\n"
    "files = sys.argv[2:]\n"
    "with log_path.open('a') as lf:\n"
    "    lf.write('|'.join(files) + chr(10))\n"
    "bad = []\n"
    "for f in files:\n"
    "    try:\n"
    "        text = pathlib.Path(f).read_text()\n"
    "    except OSError:\n"
    "        continue\n"
    "    for m in re.finditer(r'[\\w./-]+\\.(?:md|mdx|rst|adoc)', text):\n"
    "        doc = pathlib.Path(m.group(0))\n"
    "        if doc.exists() and 'DOC_MARKER_OK' not in doc.read_text():\n"
    "            bad.append(f + ' -> ' + str(doc))\n"
    "if bad:\n"
    "    print('doc marker missing: ' + '; '.join(bad))\n"
    "    sys.exit(1)\n"
    "sys.exit(0)\n"
)
_DOC_TEST_SCRIPT = Path(_tmp.mkdtemp(prefix="ratchet-cp2-docscript-")) / "doc_test.py"
_DOC_TEST_SCRIPT.write_text(_DOC_TEST_SRC)

# A regression_command stand-in that always exits 0 but counts its own invocations in a counter file, so a
# case can assert whether the full suite ran again without depending on timing (test_flaky.py's
# flaky_cmd_from_outcomes()/counter-file pattern, reused here for counting invocations, not outcomes).
_COUNTER_SRC = (
    "import sys\n"
    "from pathlib import Path\n"
    "p = Path(sys.argv[1])\n"
    "n = int(p.read_text() or '0') + 1\n"
    "p.write_text(str(n))\n"
    "sys.exit(0)\n"
)
_COUNTER_SCRIPT = Path(_tmp.mkdtemp(prefix="ratchet-cp2-counter-")) / "counter_cmd.py"
_COUNTER_SCRIPT.write_text(_COUNTER_SRC)


def _doc_test_command(log_path) -> str:
    return (f"{shlex.quote(sys.executable)} {shlex.quote(str(_DOC_TEST_SCRIPT))} "
            f"{shlex.quote(str(log_path))} {{files}}")


def _new_log():
    return Path(_tmp.mkdtemp(prefix="ratchet-cp2-doclog-")) / "log.txt"


def _counting_regression_command():
    counter = Path(_tmp.mkdtemp(prefix="ratchet-cp2-ctr-")) / "n"
    counter.write_text("0")
    cmd = f"{shlex.quote(sys.executable)} {shlex.quote(str(_COUNTER_SCRIPT))} {shlex.quote(str(counter))}"
    return cmd, counter


def _fixture_project(name, set_doc_test_command=True):
    """A one-checkpoint project with every doc-shaped file CP2's single-checkpoint cases need, all present
    before approve_plan(): a README (plus the test file that names it), a second doc (docs/notes.md) named by
    its own test file, a third doc (docs/unmatched.md) that no test file names, a plain test file that names no
    doc at all (proving the file-name match is per-doc, never guessed), and a SKILL.md (never a doc, however it
    is edited). Returns (project dir, doc-test-command log path)."""
    log = _new_log()
    config = [f"doc_test_command={_doc_test_command(log)}"] if set_doc_test_command else []
    d = g.project(name, cps=1, config=config)
    (d / "README.md").write_text(f"# Title\n\n{MARKER}\n\nThe original sentence.\n")
    (d / "tests" / "readme_check.test.js").write_text("// this suite's name mentions README.md on purpose\n")
    (d / "docs").mkdir(parents=True, exist_ok=True)
    (d / "docs" / "notes.md").write_text(f"# Notes\n\n{MARKER}\n\noriginal notes text.\n")
    (d / "tests" / "test_notes.py").write_text("# this suite's name mentions docs/notes.md on purpose\n")
    (d / "tests" / "other.test.js").write_text("// this suite's name mentions no doc at all\n")
    (d / "docs" / "unmatched.md").write_text(f"# Unmatched\n\n{MARKER}\n\noriginal unmatched text.\n")
    (d / "skills" / "x").mkdir(parents=True, exist_ok=True)
    (d / "skills" / "x" / "SKILL.md").write_text("# Skill X\n\noriginal skill text.\n")
    g.approve_plan(d)
    return d, log


def _approve_cp1(d, cp="CP1"):
    """Drives `cp` through tests-written/red/implemented/behavior and one clean adversarial round (no
    findings), so both of its required gates record an approval fingerprint -- the 'already passed and been
    approved' state every case starts from."""
    g.to_adversarial(d, cp)
    rnd = g.open_round(d, cp)
    rid = g.R0(rnd)
    path = g.write_review(d, rnd, rid, findings=[])
    r = g.rx(d, "adv-record", cp, "--reviews", path)
    assert r.returncode == 0, r.stdout + r.stderr


def _state(d, slug="f"):
    return json.loads((Path(d) / f".ratchet/runs/{slug}/state.json").read_text())


def _gate(d, cp="CP1", gate="adversarial", slug="f"):
    return _state(d, slug)["checkpoints"][cp]["gates"][gate]


def _head(d):
    return g.git(d, "rev-parse", "HEAD").stdout.strip()


def _commit_message(d):
    return g.git(d, "log", "-1", "--format=%B").stdout


def _out(r):
    return (r.stdout or "") + "\n" + (r.stderr or "")


_SKIP_PHRASES = ("not re-reviewed", "not reviewed", "skipped review", "review skipped",
                  "edited after review", "without review", "no new review", "not re-review")


def _names_skipped_review(text, filename) -> bool:
    """True when `text` both mentions `filename` and says, in some plain-English way, that it was edited after
    review and not re-reviewed -- the visibility CP2-T03/T07 require, matched loosely (several equivalent
    phrasings) since the exact wording is the implementation's call, not this file's."""
    if filename not in text:
        return False
    low = text.lower()
    return any(p in low for p in _SKIP_PHRASES)


# ------------------------------------------------------------------------------------------------ cases

def t01_doc_edit_after_approval_does_not_reopen_review():
    d, _log = _fixture_project("cp2-t01")
    _approve_cp1(d)
    attempts_before = _gate(d)["attempts"]
    head_before = _head(d)
    (d / "README.md").write_text(f"# Title\n\n{MARKER}\n\nThe edited sentence, checked by no test.\n")
    r = g.rx(d, "complete", "CP1")
    check("[CP2-T01] `complete CP1` succeeds after a doc-only edit made after approval",
          r.returncode == 0, _out(r))
    check("[CP2-T01] a new commit was created", _head(d) != head_before, (_head(d), head_before))
    check("[CP2-T01] the adversarial gate's stored status stays 'passed', never 'stale'",
          _gate(d)["status"] == "passed", _gate(d))
    check("[CP2-T01] the behavior gate's stored status stays 'passed', never 'stale'",
          _gate(d, gate="behavior")["status"] == "passed", _gate(d, gate="behavior"))
    check("[CP2-T01] no new adversarial round was opened (attempts unchanged)",
          _gate(d)["attempts"] == attempts_before, (_gate(d)["attempts"], attempts_before))


def t02_doc_edit_breaking_a_doc_checked_test_fails_complete_naming_the_test():
    d, _log = _fixture_project("cp2-t02")
    _approve_cp1(d)
    head_before = _head(d)
    gate_before = dict(_gate(d))
    (d / "README.md").write_text("# Title\n\nThe marker is gone now.\n")
    r = g.rx(d, "complete", "CP1")
    out = _out(r)
    check("[CP2-T02] `complete CP1` fails when the doc-checked test fails", r.returncode != 0, out)
    check("[CP2-T02] the error names the specific test file that checks README.md",
          "tests/readme_check.test.js" in out, out)
    check("[CP2-T02] CP1 does not commit: HEAD is unchanged", _head(d) == head_before, (_head(d), head_before))
    check("[CP2-T02] the adversarial gate's stored status is unchanged ('passed')",
          _gate(d)["status"] == gate_before["status"] == "passed", (_gate(d), gate_before))


def t03_skipped_review_is_visible_in_commit_status_and_final_review():
    d, _log = _fixture_project("cp2-t03")
    _approve_cp1(d)
    (d / "README.md").write_text(f"# Title\n\n{MARKER}\n\nThe edited sentence, checked by no test.\n")
    r = g.rx(d, "complete", "CP1")
    check("[CP2-T03] `complete CP1` succeeds", r.returncode == 0, _out(r))
    msg = _commit_message(d)
    check("[CP2-T03] the commit message names README.md as edited after review and not re-reviewed",
          _names_skipped_review(msg, "README.md"), msg)
    # This is a one-checkpoint run: `complete` on its last (only) checkpoint moves the run straight to
    # awaiting_final_review, so the very next `status` call shows both the ordinary status line for CP1 and the
    # run's final review text in the same output.
    status = g.rx(d, "status")
    sout = _out(status)
    check("[CP2-T03] `ratchet.py status` shows the same line for CP1",
          _names_skipped_review(sout, "README.md"), sout)
    check("[CP2-T03] once the run's last checkpoint completes, the final review text also lists it",
          _names_skipped_review(sout, "README.md"), sout)


def t04_doc_plus_code_edit_after_approval_is_still_fully_stale():
    d, _log = _fixture_project("cp2-t04")
    _approve_cp1(d)
    head_before = _head(d)
    (d / "README.md").write_text(f"# Title\n\n{MARKER}\n\nThe edited sentence.\n")
    (d / "src" / "a.js").write_text("export const a = 2;\n")
    r = g.rx(d, "complete", "CP1")
    out = _out(r)
    check("[CP2-T04] `complete CP1` refuses when a doc edit is combined with a code edit",
          r.returncode != 0, out)
    check("[CP2-T04] the error says code changed and a new review is needed",
          bool(re.search(r"code", out, re.I)) and bool(re.search(r"review", out, re.I)), out)
    check("[CP2-T04] the adversarial gate is marked stale", _gate(d)["status"] == "stale", _gate(d))
    check("[CP2-T04] nothing commits: HEAD is unchanged", _head(d) == head_before, (_head(d), head_before))


def t05_skill_md_edit_is_never_treated_as_a_doc_edit():
    d, _log = _fixture_project("cp2-t05")
    _approve_cp1(d)
    head_before = _head(d)
    (d / "skills" / "x" / "SKILL.md").write_text("# Skill X\n\nedited skill text.\n")
    r = g.rx(d, "complete", "CP1")
    out = _out(r)
    check("[CP2-T05] `complete CP1` refuses a SKILL.md edit exactly like a code edit (CP2-T04's outcome)",
          r.returncode != 0, out)
    check("[CP2-T05] the adversarial gate is marked stale", _gate(d)["status"] == "stale", _gate(d))
    check("[CP2-T05] nothing commits: HEAD is unchanged", _head(d) == head_before, (_head(d), head_before))


def t06_only_the_test_files_that_name_the_edited_doc_are_re_run():
    d, log = _fixture_project("cp2-t06")
    _approve_cp1(d)
    (d / "docs" / "notes.md").write_text(f"# Notes\n\n{MARKER}\n\nedited notes text.\n")
    r = g.rx(d, "complete", "CP1")
    check("[CP2-T06] `complete CP1` succeeds after editing only docs/notes.md", r.returncode == 0, _out(r))
    logged = log.read_text() if log.exists() else ""
    check("[CP2-T06] the test file that names docs/notes.md was run", "tests/test_notes.py" in logged, logged)
    check("[CP2-T06] the test file that names no doc was never run", "tests/other.test.js" not in logged, logged)


def t07_doc_edit_with_no_naming_test_still_completes():
    d, log = _fixture_project("cp2-t07")
    _approve_cp1(d)
    (d / "docs" / "unmatched.md").write_text(f"# Unmatched\n\n{MARKER}\n\nedited unmatched text.\n")
    r = g.rx(d, "complete", "CP1")
    check("[CP2-T07] `complete CP1` succeeds even though no test file names docs/unmatched.md",
          r.returncode == 0, _out(r))
    logged = log.read_text() if log.exists() else ""
    check("[CP2-T07] no test command ran for a doc no test file names", logged.strip() == "", logged)
    msg = _commit_message(d)
    check("[CP2-T07] the commit still records the doc as edited after review and not re-reviewed",
          _names_skipped_review(msg, "docs/unmatched.md"), msg)


def t08_complete_skips_the_full_suite_when_only_docs_changed_since_it_last_passed():
    """A two-checkpoint project whose CP2 test file already exists before CP1 even starts, and is trivially
    true (node:test asserting 1 == 1, approved with --allow-green-first since it needs no implementation): no
    new non-doc file ever appears for CP2, so its adversarial approval lands on the exact tree the regression
    command already passed on when CP1 completed."""
    log = _new_log()
    regression_cmd, counter = _counting_regression_command()
    d = Path(_tmp.mkdtemp(prefix="ratchet-cp2-t08-"))
    for c_ in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        g.git(d, *c_)
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src" / "a.js").write_text("export const a = 1;\n")
    (d / "README.md").write_text(f"# Title\n\n{MARKER}\n\noriginal.\n")
    (d / "tests" / "readme_check.test.js").write_text("// this suite's name mentions README.md on purpose\n")
    (d / "tests" / "cp2.test.js").write_text(
        "import t from 'node:test'; import a from 'node:assert';\n"
        "t('[CP2-T01] already true', () => a.equal(1, 1));\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "init")
    g.say(d, "please build the feature")
    r = g.rx(d, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr
    r = g.rx(d, "config", "--set", "min_review_seconds=0", "providers=[]",
             f"doc_test_command={_doc_test_command(log)}", f"regression_command={regression_cmd}")
    assert r.returncode == 0, r.stdout + r.stderr
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cp1 = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
           "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
           "tests": {"files": ["tests/cp1.test.js"], "command": "node --test tests/cp1.test.js"}}
    cp2 = {"id": "CP2", "title": "Step 2", "summary": "s", "done_when": ["works"],
           "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
           "green_first_ok": True,
           "tests": {"files": ["tests/cp2.test.js"], "command": "node --test tests/cp2.test.js"}}
    plan = {"feature": "x", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": [cp1, cp2]}
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    (run / "tests" / "CP2.json").write_text(json.dumps({"cases": [{"id": "CP2-T01", "layer": "code", "proves": [1]}]}))
    (run / "checkpoints.json").write_text(json.dumps(plan))
    r = g.rx(d, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    g.approve_plan(d)

    _approve_cp1(d, "CP1")
    r = g.rx(d, "complete", "CP1")
    assert r.returncode == 0, _out(r)
    check("[CP2-T08] (fixture sanity) the regression command ran once for CP1's own completion",
          counter.read_text() == "1", counter.read_text())

    r = g.rx(d, "tests-written", "CP2")
    assert r.returncode == 0, _out(r)
    r = g.rx(d, "red", "CP2", "--allow-green-first", "--reason", "no-op checkpoint, nothing to implement")
    assert r.returncode == 0, _out(r)
    r = g.rx(d, "implemented", "CP2")
    assert r.returncode == 0, _out(r)
    r = g.rx(d, "behavior", "CP2")
    assert r.returncode == 0, _out(r)
    rnd = g.open_round(d, "CP2")
    rid = g.R0(rnd)
    path = g.write_review(d, rnd, rid, findings=[])
    r = g.rx(d, "adv-record", "CP2", "--reviews", path)
    assert r.returncode == 0, _out(r)

    (d / "README.md").write_text(f"# Title\n\n{MARKER}\n\nedited after CP2's approval.\n")
    r = g.rx(d, "complete", "CP2")
    check("[CP2-T08] `complete CP2` succeeds", r.returncode == 0, _out(r))
    check("[CP2-T08] the regression command was not invoked again (its count stays at 1)",
          counter.read_text() == "1", counter.read_text())


def t09_complete_still_runs_the_full_suite_when_real_code_changed_since_it_last_passed():
    regression_cmd, counter = _counting_regression_command()
    d = g.project("cp2-t09", cps=2, config=[f"regression_command={regression_cmd}"])
    g.approve_plan(d)
    _approve_cp1(d, "CP1")
    r = g.rx(d, "complete", "CP1")
    assert r.returncode == 0, _out(r)
    check("[CP2-T09] (fixture sanity) the regression command ran once for CP1's own completion",
          counter.read_text() == "1", counter.read_text())

    # CP2's own gates pass at a tree that includes genuinely new non-doc code (a new src file), never reusing
    # CP1's already-passing src/f.js (which would make CP2's own red run start green, not red).
    (d / "tests" / "cp2.test.js").write_text(
        "import t from 'node:test'; import a from 'node:assert'; import { f2 } from '../src/f2.js';\n"
        "t('[CP2-T01] works', () => a.equal(f2(), 2));\n")
    r = g.rx(d, "tests-written", "CP2")
    assert r.returncode == 0, _out(r)
    r = g.rx(d, "red", "CP2")
    assert r.returncode == 0, _out(r)
    (d / "src" / "f2.js").write_text("export const f2 = () => 2;\n")
    r = g.rx(d, "implemented", "CP2")
    assert r.returncode == 0, _out(r)
    r = g.rx(d, "behavior", "CP2")
    assert r.returncode == 0, _out(r)
    rnd = g.open_round(d, "CP2")
    rid = g.R0(rnd)
    path = g.write_review(d, rnd, rid, findings=[])
    r = g.rx(d, "adv-record", "CP2", "--reviews", path)
    assert r.returncode == 0, _out(r)

    # no post-approval doc edit at all
    r = g.rx(d, "complete", "CP2")
    check("[CP2-T09] `complete CP2` succeeds", r.returncode == 0, _out(r))
    check("[CP2-T09] the regression command runs again (its count increases to 2)",
          counter.read_text() == "2", counter.read_text())


def t10_failed_doc_test_run_never_marks_gates_passed_or_commits_partial_work():
    d, _log = _fixture_project("cp2-t10")
    _approve_cp1(d)
    gate_before = dict(_gate(d))
    behavior_before = dict(_gate(d, gate="behavior"))
    head_before = _head(d)
    broken = "# Title\n\nThe marker is gone now.\n"
    (d / "README.md").write_text(broken)
    r1 = g.rx(d, "complete", "CP1")
    check("[CP2-T10] the first `complete CP1` fails on the broken doc-checked test", r1.returncode != 0, _out(r1))
    check("[CP2-T10] the adversarial gate's stored status is left exactly as it was",
          _gate(d)["status"] == gate_before["status"], (_gate(d), gate_before))
    check("[CP2-T10] the behavior gate's stored status is left exactly as it was",
          _gate(d, gate="behavior")["status"] == behavior_before["status"],
          (_gate(d, gate="behavior"), behavior_before))
    check("[CP2-T10] nothing committed: HEAD is unchanged", _head(d) == head_before, (_head(d), head_before))
    check("[CP2-T10] the working tree still shows the uncommitted doc edit",
          (d / "README.md").read_text() == broken, (d / "README.md").read_text())
    (d / "README.md").write_text(f"# Title\n\n{MARKER}\n\nfixed the marker back.\n")
    r2 = g.rx(d, "complete", "CP1")
    check("[CP2-T10] a second `complete CP1` after fixing the marker back succeeds",
          r2.returncode == 0, _out(r2))


TESTS = [
    ("CP2-T01", t01_doc_edit_after_approval_does_not_reopen_review),
    ("CP2-T02", t02_doc_edit_breaking_a_doc_checked_test_fails_complete_naming_the_test),
    ("CP2-T03", t03_skipped_review_is_visible_in_commit_status_and_final_review),
    ("CP2-T04", t04_doc_plus_code_edit_after_approval_is_still_fully_stale),
    ("CP2-T05", t05_skill_md_edit_is_never_treated_as_a_doc_edit),
    ("CP2-T06", t06_only_the_test_files_that_name_the_edited_doc_are_re_run),
    ("CP2-T07", t07_doc_edit_with_no_naming_test_still_completes),
    ("CP2-T08", t08_complete_skips_the_full_suite_when_only_docs_changed_since_it_last_passed),
    ("CP2-T09", t09_complete_still_runs_the_full_suite_when_real_code_changed_since_it_last_passed),
    ("CP2-T10", t10_failed_doc_test_run_never_marks_gates_passed_or_commits_partial_work),
]


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


# ------------------------------------------------------------------------------------------------ CP2 adversarial
# round 1 fix-round tests: appended after the planned CP2-T01..T10 cases above, which are left untouched. Run
# inline, below, before `main()` (which only knows about the original TESTS list) so their pass/fail still folds
# into this file's overall PASS/FAIL totals and exit code.

# A doc_test_command stand-in that mutates a tracked source file as a side effect (standing in for a doc-test
# runner that regenerates a tracked file or updates a snapshot) and still exits 0, so a case can assert that
# `complete` refuses to finish on the tree it ran its checks against, not the tree that mutation left behind.
_MUTATE_SRC = (
    "import sys, pathlib\n"
    "target = pathlib.Path(sys.argv[1])\n"
    "target.write_text(target.read_text() + '\\n// mutated by doc test\\n')\n"
    "sys.exit(0)\n"
)
_MUTATE_SCRIPT = Path(_tmp.mkdtemp(prefix="ratchet-cp2-mutate-")) / "mutate.py"
_MUTATE_SCRIPT.write_text(_MUTATE_SRC)


def _mutating_doc_test_command(target="src/a.js"):
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(_MUTATE_SCRIPT))} {shlex.quote(target)} {{files}}"


def _two_checkpoint_project(name, doc_cmd, regression_cmd, cp2_risk="low"):
    """The same two-checkpoint fixture as CP2-T08 (a CP2 test file that already exists before CP1 even starts,
    trivially true, so CP2's adversarial approval lands on the exact tree the regression command already
    passed on when CP1 completed), parameterised on `doc_test_command` and `regression_command` so the
    fix-round cases below can swap in the mutating doc-test stand-in."""
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-{name}-"))
    for c_ in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        g.git(d, *c_)
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src" / "a.js").write_text("export const a = 1;\n")
    (d / "README.md").write_text(f"# Title\n\n{MARKER}\n\noriginal.\n")
    (d / "tests" / "readme_check.test.js").write_text("// this suite's name mentions README.md on purpose\n")
    (d / "tests" / "cp2.test.js").write_text(
        "import t from 'node:test'; import a from 'node:assert';\n"
        "t('[CP2-T01] already true', () => a.equal(1, 1));\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "init")
    g.say(d, "please build the feature")
    r = g.rx(d, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr
    r = g.rx(d, "config", "--set", "min_review_seconds=0", "providers=[]",
             f"doc_test_command={doc_cmd}", f"regression_command={regression_cmd}")
    assert r.returncode == 0, r.stdout + r.stderr
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cp1 = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
           "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
           "tests": {"files": ["tests/cp1.test.js"], "command": "node --test tests/cp1.test.js"}}
    cp2 = {"id": "CP2", "title": "Step 2", "summary": "s", "done_when": ["works"],
           "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": cp2_risk,
           "green_first_ok": True,
           "tests": {"files": ["tests/cp2.test.js"], "command": "node --test tests/cp2.test.js"}}
    if cp2_risk == "high":
        cp2["risk_reason"] = "forces a normal adversarial review round for this fixture's doc-only step"
    plan = {"feature": "x", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": [cp1, cp2]}
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    (run / "tests" / "CP2.json").write_text(json.dumps({"cases": [{"id": "CP2-T01", "layer": "code", "proves": [1]}]}))
    (run / "checkpoints.json").write_text(json.dumps(plan))
    r = g.rx(d, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    g.approve_plan(d)
    return d


def x01_doc_edit_reviewed_in_its_own_round_is_not_labelled_unreviewed():
    """[CP2-X01] A checkpoint's only change is a doc, edited and reviewed (approved with zero findings) in its
    own normal adversarial round -- never after that approval -- on top of an earlier checkpoint whose full
    suite already passed. `_skip_full_suite`'s wider diff range (against the last full-suite pass) still finds
    that doc changed and skips the suite, but the doc was never actually skipped from review: the commit
    message and `status` must not claim it was edited after review and not re-reviewed."""
    log = _new_log()
    regression_cmd, counter = _counting_regression_command()
    d = _two_checkpoint_project("cp2-x01", _doc_test_command(log), regression_cmd, cp2_risk="high")
    _approve_cp1(d, "CP1")
    r = g.rx(d, "complete", "CP1")
    assert r.returncode == 0, _out(r)
    # The doc edit happens here, BEFORE CP2's own tests-written/red/implemented/behavior/adv-record, so
    # reviewers see, review and approve exactly this content; nothing changes after that approval.
    (d / "README.md").write_text(f"# Title\n\n{MARKER}\n\nedited before CP2's own review, and reviewed.\n")
    r = g.rx(d, "tests-written", "CP2")
    assert r.returncode == 0, _out(r)
    r = g.rx(d, "red", "CP2", "--allow-green-first", "--reason", "no-op checkpoint, nothing to implement")
    assert r.returncode == 0, _out(r)
    r = g.rx(d, "implemented", "CP2")
    assert r.returncode == 0, _out(r)
    r = g.rx(d, "behavior", "CP2")
    assert r.returncode == 0, _out(r)
    rnd = g.open_round(d, "CP2")
    # high risk needs an approving review from every reviewer the round lists, not just one.
    paths = [g.write_review(d, rnd, rv["id"], []) for rv in rnd["reviewers"]]
    r = g.rx(d, "adv-record", "CP2", "--reviews", *paths)
    assert r.returncode == 0, _out(r)
    # no edit at all after CP2's own adversarial approval
    r = g.rx(d, "complete", "CP2")
    check("[CP2-X01] `complete CP2` succeeds", r.returncode == 0, _out(r))
    msg = _commit_message(d)
    check("[CP2-X01] the commit message does not claim README.md was edited after review and not re-reviewed",
          not _names_skipped_review(msg, "README.md"), msg)
    status = g.rx(d, "status")
    sout = _out(status)
    check("[CP2-X01] `ratchet.py status` does not claim README.md was edited after review and not re-reviewed",
          not _names_skipped_review(sout, "README.md"), sout)


def x02_doc_test_command_that_mutates_a_tracked_file_refuses_completion_post_approval():
    """[CP2-X02] A doc test command that mutates a tracked file as a side effect (a generator, a snapshot
    updater) must refuse completion on the post-approval doc-edit route (CP2-T01's route), the same way a
    regression command that changes files already does -- the tree the doc test validated is not the tree
    `complete` would otherwise commit."""
    d = g.project("cp2-x02", cps=1, config=[f"doc_test_command={_mutating_doc_test_command()}"])
    (d / "README.md").write_text(f"# Title\n\n{MARKER}\n\noriginal.\n")
    (d / "tests" / "readme_check.test.js").write_text("// this suite's name mentions README.md on purpose\n")
    g.approve_plan(d)
    _approve_cp1(d)
    head_before = _head(d)
    (d / "README.md").write_text(f"# Title\n\n{MARKER}\n\nedited after approval.\n")
    r = g.rx(d, "complete", "CP1")
    out = _out(r)
    check("[CP2-X02] `complete CP1` refuses when the doc test command mutates a tracked file "
          "(post-approval route)", r.returncode != 0, out)
    check("[CP2-X02] the refusal names the doc test command as the cause",
          "doc test command" in out.lower(), out)
    check("[CP2-X02] nothing commits: HEAD is unchanged", _head(d) == head_before, (_head(d), head_before))


def x03_doc_test_command_that_mutates_a_tracked_file_refuses_completion_suite_skip():
    """[CP2-X03] Same as CP2-X02, but on the full-suite-skip route (CP2-T08's route): the doc edit happens
    before CP2's own adversarial approval (so the post-approval doc-edit route finds nothing to do), and only
    `_skip_full_suite`'s own doc-test run, replacing the full suite, mutates a tracked file. `complete` must
    still refuse."""
    regression_cmd, counter = _counting_regression_command()
    d = _two_checkpoint_project("cp2-x03", _mutating_doc_test_command(), regression_cmd, cp2_risk="high")
    _approve_cp1(d, "CP1")
    r = g.rx(d, "complete", "CP1")
    assert r.returncode == 0, _out(r)
    check("[CP2-X03] (fixture sanity) the regression command ran once for CP1's own completion",
          counter.read_text() == "1", counter.read_text())
    (d / "README.md").write_text(f"# Title\n\n{MARKER}\n\nedited before CP2's own review, and reviewed.\n")
    r = g.rx(d, "tests-written", "CP2")
    assert r.returncode == 0, _out(r)
    r = g.rx(d, "red", "CP2", "--allow-green-first", "--reason", "no-op checkpoint, nothing to implement")
    assert r.returncode == 0, _out(r)
    r = g.rx(d, "implemented", "CP2")
    assert r.returncode == 0, _out(r)
    r = g.rx(d, "behavior", "CP2")
    assert r.returncode == 0, _out(r)
    rnd = g.open_round(d, "CP2")
    # high risk needs an approving review from every reviewer the round lists, not just one.
    paths = [g.write_review(d, rnd, rv["id"], []) for rv in rnd["reviewers"]]
    r = g.rx(d, "adv-record", "CP2", "--reviews", *paths)
    assert r.returncode == 0, _out(r)
    head_before = _head(d)
    # no edit at all after CP2's own adversarial approval: the post-approval doc-edit route finds nothing, only
    # `_skip_full_suite`'s own doc-test run (replacing the full suite) is exercised.
    r = g.rx(d, "complete", "CP2")
    out = _out(r)
    check("[CP2-X03] `complete CP2` refuses when the suite-skip doc test command mutates a tracked file",
          r.returncode != 0, out)
    check("[CP2-X03] the refusal names the doc test command as the cause",
          "doc test command" in out.lower(), out)
    check("[CP2-X03] nothing commits: HEAD is unchanged", _head(d) == head_before, (_head(d), head_before))


FIX_TESTS = [
    ("CP2-X01", x01_doc_edit_reviewed_in_its_own_round_is_not_labelled_unreviewed),
    ("CP2-X02", x02_doc_test_command_that_mutates_a_tracked_file_refuses_completion_post_approval),
    ("CP2-X03", x03_doc_test_command_that_mutates_a_tracked_file_refuses_completion_suite_skip),
]

for _cid, _fn in FIX_TESTS:
    CASE_STATUS.setdefault(_cid, True)
    try:
        _fn()
    except Exception as _e:  # a crash still counts as a result for this case, never a silent drop
        FAIL += 1
        CASE_STATUS[_cid] = False
        CASE_MESSAGES.setdefault(_cid, []).append(f"crashed: {_e}")
        print(f"  ✗ [{_cid}] CRASHED: {_e}")
        traceback.print_exc()


def _write_report_with_fix_tests():
    """Same as the planned `write_report()` above, but over `[*TESTS, *FIX_TESTS]` so a fix-round case's
    pass/fail also lands in the structured JSON report. Rebinds the module-level `write_report` name (looked
    up by `main()` only at call time, above) rather than editing `write_report()`'s own body."""
    import os
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = [{"id": cid, "status": "passed" if CASE_STATUS.get(cid, False) else "failed",
              "message": "" if CASE_STATUS.get(cid, False) else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid, _ in [*TESTS, *FIX_TESTS]]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


write_report = _write_report_with_fix_tests


# ------------------------------------------------------------------------------------------------ CP2 adversarial
# round 2 fix-round tests: appended after the round-1 fix-round block above, which is left untouched. Same
# inline pattern: run below, before `main()`, so their pass/fail still folds into this file's overall
# PASS/FAIL totals and exit code.

def x04_doc_edited_between_behavior_and_adversarial_is_not_labelled_unreviewed():
    """[CP2-X04] A doc (README.md, "doc A") edited strictly between the behavior gate passing and the
    adversarial round opening is present in, and approved by, that adversarial round: it was genuinely
    reviewed. A second doc (docs/notes.md, "doc B") edited after that adversarial approval was never reviewed
    at all. `doc_edits_unreviewed` must list only doc B, the commit message and `status` must name only doc B
    as edited after review and not re-reviewed, and the doc test command must still have run for both A and B
    (the behavior-side staleness exemption covers A even though A is not recorded as unreviewed)."""
    d, log = _fixture_project("cp2-x04")
    g.to_adversarial(d, "CP1")  # behavior passes
    (d / "README.md").write_text(f"# Title\n\n{MARKER}\n\ndoc A, edited between behavior and adversarial.\n")
    rnd = g.open_round(d, "CP1")
    rid = g.R0(rnd)
    path = g.write_review(d, rnd, rid, findings=[])
    r = g.rx(d, "adv-record", "CP1", "--reviews", path)
    assert r.returncode == 0, _out(r)  # adversarial round reviewed and approved doc A's current content
    (d / "docs" / "notes.md").write_text(f"# Notes\n\n{MARKER}\n\ndoc B, edited after adversarial approval.\n")
    r = g.rx(d, "complete", "CP1")
    check("[CP2-X04] `complete CP1` succeeds", r.returncode == 0, _out(r))
    unreviewed = (_state(d)["checkpoints"]["CP1"].get("doc_edits_unreviewed") or [])
    check("[CP2-X04] doc_edits_unreviewed lists only doc B (docs/notes.md), never doc A (README.md)",
          unreviewed == ["docs/notes.md"], unreviewed)
    msg = _commit_message(d)
    check("[CP2-X04] the commit message does not name README.md as edited after review and not re-reviewed",
          not _names_skipped_review(msg, "README.md"), msg)
    check("[CP2-X04] the commit message names docs/notes.md as edited after review and not re-reviewed",
          _names_skipped_review(msg, "docs/notes.md"), msg)
    status = g.rx(d, "status")
    sout = _out(status)
    check("[CP2-X04] `ratchet.py status` does not name README.md as edited after review and not re-reviewed",
          not _names_skipped_review(sout, "README.md"), sout)
    check("[CP2-X04] `ratchet.py status` names docs/notes.md as edited after review and not re-reviewed",
          _names_skipped_review(sout, "docs/notes.md"), sout)
    logged = log.read_text() if log.exists() else ""
    check("[CP2-X04] the doc test command ran for doc A (README.md)'s own naming test",
          "tests/readme_check.test.js" in logged, logged)
    check("[CP2-X04] the doc test command ran for doc B (docs/notes.md)'s own naming test",
          "tests/test_notes.py" in logged, logged)


FIX_TESTS2 = [
    ("CP2-X04", x04_doc_edited_between_behavior_and_adversarial_is_not_labelled_unreviewed),
]

for _cid, _fn in FIX_TESTS2:
    CASE_STATUS.setdefault(_cid, True)
    try:
        _fn()
    except Exception as _e:  # a crash still counts as a result for this case, never a silent drop
        FAIL += 1
        CASE_STATUS[_cid] = False
        CASE_MESSAGES.setdefault(_cid, []).append(f"crashed: {_e}")
        print(f"  ✗ [{_cid}] CRASHED: {_e}")
        traceback.print_exc()


def _write_report_with_fix_tests2():
    """Same as `_write_report_with_fix_tests` above, but over `[*TESTS, *FIX_TESTS, *FIX_TESTS2]`. Rebinds the
    module-level `write_report` name again (looked up by `main()` only at call time, above)."""
    import os
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = [{"id": cid, "status": "passed" if CASE_STATUS.get(cid, False) else "failed",
              "message": "" if CASE_STATUS.get(cid, False) else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid, _ in [*TESTS, *FIX_TESTS, *FIX_TESTS2]]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


write_report = _write_report_with_fix_tests2


if __name__ == "__main__":
    main()
