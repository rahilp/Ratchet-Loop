#!/usr/bin/env python3
"""
CP5 tests: a full review clears skipped-review notes.   python3 tests/test_quick_path_records.py

When a checkpoint's adversarial gate had its review skipped or lightened (CP2's doc_edits_unreviewed, CP3's
small_fix_review, CP4's docs_only_review_skip) and a later FULL adversarial round (every reviewer the step's own
risk names, not a small-fix or docs-only round) approves that step, Ratchet stops saying the step went
unreviewed, was docs-only-skipped, or was only checked by one reviewer as a small fix: every lighter-review
record present is cleared the moment that full round is recorded approved. A small-fix approval or a docs-only
skip never clears another record (only a FULL round's own approval does), a round that is not approved clears
nothing, and clearing a past note never suppresses a genuinely new one. skills/ratchet-orchestrator/SKILL.md's
own small-fix paragraph must say the small_edit_lines limit is counted since the step's last FULL review
approval, not simply "that approval" (which would misdescribe CP3's own add-up-across-small-fixes rule).

Reuses tests/test_gates.py (`project`, `approve_plan`, `to_adversarial`, `open_round`, `write_review`, `rx`,
`git`, `say`) for the throwaway git project and the real-CLI-subprocess way every suite in this repo drives the
engine, and copies (never imports) the CP2/CP3/CP4 quick-path suites' own fixture shapes
(tests/test_quick_path_docs.py, tests/test_quick_path_small.py, tests/test_quick_path_docs_step.py) so importing
this file never runs any of those other files' own module-level setup. Every case reaches CP1 through a REAL
adversarial round sequence (round opens, reviewers write clean or blocking reviews, `adv-record` records it) so
the clearing logic under test (cmd_adv_record's full-round-approval branch) is always exercised through the
real command. CP5-T02/T05/T07 need a checkpoint whose own doc_edits_unreviewed record is already set before
that round opens; since that record is otherwise only reachable mid-checkpoint via a guardrail-check failure
interrupting `complete` (every other path that sets it also finishes the checkpoint in the same call), those
cases prime it directly on state.json's checkpoint entry, through the `_prime_checkpoint()` helper below, before
driving the round through the real CLI -- the precondition is synthetic, the clearing behavior under test is
not. No command or app server is started; this is pure CLI/state behavior, and it never runs tests/run_all.py or
any real suite (only tiny fixture commands this file fully controls).

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP5-T01",
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
import test_gates as g          # noqa: E402  (project, approve_plan, to_adversarial, open_round, write_review, rx, git, say)

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP5-[TX]\d+[a-z]?)\]")


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


def _out(r):
    return (r.stdout or "") + "\n" + (r.stderr or "")


# ------------------------------------------------------------------------------------------------ fixtures

MARKER = "DOC_MARKER_OK"              # what the fake doc_test_command checks a doc file for
INSTALL_PHRASE = "Install the thing"  # what CP1's own planned test checks README.md for, via real node:test

# A tiny fake doc_test_command (never a real test runner): logs every file path it is called with (one
# '|'-joined line per invocation) to the log path given as its first argument, then fails if any doc file
# mentioned inside those files is missing MARKER. Copied from tests/test_quick_path_docs.py's CP2 fixture
# (and tests/test_quick_path_docs_step.py's CP4 fixture) so this file never imports either of those test files'
# own module-level setup.
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
_DOC_TEST_SCRIPT = Path(_tmp.mkdtemp(prefix="ratchet-cp5-docscript-")) / "doc_test.py"
_DOC_TEST_SCRIPT.write_text(_DOC_TEST_SRC)


def _doc_test_command(log_path) -> str:
    return (f"{shlex.quote(sys.executable)} {shlex.quote(str(_DOC_TEST_SCRIPT))} "
            f"{shlex.quote(str(log_path))} {{files}}")


def _new_log():
    return Path(_tmp.mkdtemp(prefix="ratchet-cp5-doclog-")) / "log.txt"


def _project(name, risk="normal", config=None) -> Path:
    """A one-checkpoint project (risk configurable per case) with a README present before `approve_plan()`, the
    same shape tests/test_quick_path_small.py's CP3 fixture uses for its small-fix cases."""
    d = g.project(name, cps=1, extra_cp={"risk": risk}, config=config or [])
    (d / "README.md").write_text("# Title\n\nThe original sentence.\n")
    g.approve_plan(d)
    return d


def _append_lines(path: Path, n: int, tag: str):
    """Appends exactly `n` new line-comments to `path` (0 removed), so the code-lines-changed measurement since
    an approved tree is exactly `n` added lines. Copied from tests/test_quick_path_small.py's CP3 fixture."""
    with open(path, "a") as f:
        for i in range(n):
            f.write(f"// filler-{tag}-{i}\n")


def _approve_round1(d, cp="CP1"):
    """Drives `cp` through tests-written/red/implemented/behavior and one clean, full adversarial round (no
    findings, every reviewer the round names), so the gate is genuinely 'approved' and last_full_review_fingerprint
    is recorded -- the 'already passed and fully approved' state most CP5 cases start from."""
    g.to_adversarial(d, cp)
    rnd = g.open_round(d, cp)
    paths = [g.write_review(d, rnd, rv["id"], findings=[]) for rv in rnd["reviewers"]]
    r = g.rx(d, "adv-record", cp, "--reviews", *paths)
    assert r.returncode == 0, _out(r)
    return rnd


def _open_and_approve_round(d, cp, rnd):
    """Writes a clean review (no findings) from every reviewer a round names, then records it. Works for any
    reviewer count (1 for a small-fix round, 2 for a normal-risk full round)."""
    paths = [g.write_review(d, rnd, rv["id"], findings=[]) for rv in rnd["reviewers"]]
    r = g.rx(d, "adv-record", cp, "--reviews", *paths)
    assert r.returncode == 0, _out(r)
    return r


def _state(d, slug="f"):
    return json.loads((Path(d) / f".ratchet/runs/{slug}/state.json").read_text())


def _cps(d, cp="CP1", slug="f") -> dict:
    """The checkpoint's own state entry (gates, doc_edits_unreviewed, small_fix_review, docs_only_review_skip,
    last_full_review_fingerprint, ...), read back read-only."""
    return _state(d, slug)["checkpoints"][cp]


def _gate(d, cp="CP1", gate="adversarial", slug="f"):
    return _cps(d, cp, slug)["gates"][gate]


_REBASELINE = ("import importlib.util, sys\nfrom pathlib import Path\n"
               "s = importlib.util.spec_from_file_location('ratchet_fixture', sys.argv[1])\n"
               "m = importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
               "m.record_chain(Path(sys.argv[2]))\n")


def _prime_checkpoint(d, cp="CP1", slug="f", **fields):
    """CP5-T02/T05/T07's own synthetic precondition (per the test plan's fixture note): doc_edits_unreviewed is
    otherwise only reachable mid-checkpoint via a guardrail-check failure interrupting `complete` (every other
    path that sets it also finishes the checkpoint in the same call), so it cannot be reached through a normal
    CLI sequence in a test fixture. This helper primes it (or any other checkpoint-state field) directly on this
    throwaway temp project's own state.json, through this one small helper, and is used ONLY to reach that
    precondition -- the clearing behavior every case actually tests is always driven afterward through the real
    `reviewers`/`adv-record` CLI, never through this helper. Re-baselines the engine's own integrity record with
    its own `record_chain` afterward (what `accept-state` does), the same way tests/test_status_plain.py's
    `mutate()` and tests/test_brief_guard.py's `_force_phase_blocked()` do, so this direct edit doesn't make a
    later read of this run's state look tampered with."""
    import subprocess
    p = Path(d) / f".ratchet/runs/{slug}/state.json"
    st = json.loads(p.read_text())
    st["checkpoints"][cp].update(fields)
    p.write_text(json.dumps(st))
    r = subprocess.run([sys.executable, "-c", _REBASELINE, str(g.ENGINE), str(p)], cwd=d, env=g.ENV,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + r.stdout


def _head(d):
    return g.git(d, "rev-parse", "HEAD").stdout.strip()


def _commit_message(d):
    return g.git(d, "log", "-1", "--format=%B").stdout


_SKIP_PHRASES = ("not re-reviewed", "not reviewed", "skipped review", "review skipped",
                  "edited after review", "without review", "no new review", "not re-review")


def _names_skipped_review(text, filename) -> bool:
    """True when `text` both mentions `filename` and says, in some plain-English way, that it was edited after
    review and not re-reviewed -- matched loosely since the exact wording is the implementation's call, not this
    file's. Copied from tests/test_quick_path_docs.py's CP2 fixture."""
    if filename not in text:
        return False
    low = text.lower()
    return any(p in low for p in _SKIP_PHRASES)


_SMALL_FIX_RE = re.compile(r"small[\s-]*fix", re.I)
_ONE_REVIEWER_RE = re.compile(r"\b(one|1|single)\b[^.\n]{0,24}review", re.I)


def _names_small_fix_review(text, reviewer_id) -> bool:
    """Copied from tests/test_quick_path_small.py's CP3 fixture: True when `text` names `reviewer_id`, calls the
    review a 'small fix' and says it was done by one reviewer."""
    if reviewer_id not in text:
        return False
    return bool(_SMALL_FIX_RE.search(text)) and bool(_ONE_REVIEWER_RE.search(text))


_DOCS_ONLY_WORDS = ("docs only", "doc only", "only docs", "only documentation", "documentation only", "doc-only")
_SKIP_WORDS = ("skip", "no review", "not reviewed", "never reviewed", "without review", "no reviewer")


def _names_docs_only_skip(text, filename) -> bool:
    """Copied from tests/test_quick_path_docs_step.py's CP4 fixture: True when `text` both mentions `filename`
    and says, in some plain-English way, that this checkpoint's change was docs only and its review was
    skipped."""
    if filename not in text:
        return False
    low = text.lower()
    return any(p in low for p in _DOCS_ONLY_WORDS) and any(w in low for w in _SKIP_WORDS)


def _docs_only_project(name, risk=None, config=None):
    """A one-checkpoint project whose CP1 is submitted (and approved) with a planned test
    (tests/test_readme.test.js) that checks README.md content directly via real node:test -- never src/a.js --
    so a step that only edits README.md during the build is genuinely docs-only since base_tree. CP4's own
    fixture shape (tests/test_quick_path_docs_step.py's `_project_base`/`_approved_docs_project`), duplicated
    here rather than imported so importing this file never runs that other file's own module-level test
    execution. Returns (project dir, doc-test-command log path)."""
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-{name}-"))
    for c_ in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        g.git(d, *c_)
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src" / "a.js").write_text("export const a = 1;\n")
    (d / "README.md").write_text("# Title\n\noriginal sentence.\n")
    (d / "tests" / "test_readme.test.js").write_text(
        "import t from 'node:test'; import a from 'node:assert'; import fs from 'node:fs';\n"
        f"t('[CP1-T01] README names the install steps', () => a.ok(fs.readFileSync('README.md', 'utf8')"
        f".includes('{INSTALL_PHRASE}')));\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "init")
    g.say(d, "please build the feature")
    r = g.rx(d, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr
    log = _new_log()
    cfg_args = ["min_review_seconds=0", "providers=[]", f"doc_test_command={_doc_test_command(log)}"]
    cfg_args += list(config or [])
    r = g.rx(d, "config", "--set", *cfg_args)
    assert r.returncode == 0, r.stdout + r.stderr
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cp1 = {"id": "CP1", "title": "Update the README", "summary": "s",
           "done_when": ["README names the install steps"],
           "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": risk or "normal",
           "tests": {"files": ["tests/test_readme.test.js"], "command": "node --test tests/test_readme.test.js"}}
    if cp1["risk"] == "high":
        cp1["risk_reason"] = _tmp.RISK_REASON_MIN
    plan = {"feature": "x", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": [cp1]}
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
    (run / "checkpoints.json").write_text(json.dumps(plan))
    r = g.rx(d, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    g.approve_plan(d)
    return d, log


def _to_adversarial_docs(d, cp="CP1"):
    """Drives `cp` from tests-written through behavior passing, touching only README.md (never src/a.js): red
    fails because README.md lacks INSTALL_PHRASE yet; the implementation edit adds both INSTALL_PHRASE (so
    CP1's own planned test passes) and MARKER (so a doc_test_command scan of README.md also passes) to
    README.md; behavior passes. Copied from tests/test_quick_path_docs_step.py's CP4 fixture."""
    r = g.rx(d, "tests-written", cp)
    assert r.returncode == 0, _out(r)
    r = g.rx(d, "red", cp)
    assert r.returncode == 0, _out(r)
    (d / "README.md").write_text(f"# Title\n\n{INSTALL_PHRASE}\n\n{MARKER}\n\noriginal content, updated.\n")
    r = g.rx(d, "implemented", cp)
    assert r.returncode == 0, _out(r)
    r = g.rx(d, "behavior", cp)
    assert r.returncode == 0, _out(r)


SKILL_PATH = ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md"


# ------------------------------------------------------------------------------------------------ cases

def t01_full_review_clears_a_docs_only_skip_note():
    d, _log = _docs_only_project("cp5-t01")
    _to_adversarial_docs(d)
    r0 = g.rx(d, "reviewers", "CP1")
    check("[CP5-T01] (fixture sanity) the first `reviewers CP1` call is CP4's own docs-only skip (gate passed, "
          "no round, docs_only_review_skip recorded naming README.md)",
          r0.returncode == 0 and _gate(d)["status"] == "passed"
          and "README.md" in (_cps(d).get("docs_only_review_skip") or {}).get("files", []),
          _out(r0) + "\n" + repr(_cps(d)))
    (d / "src" / "a.js").write_text("export const a = 2;\n")
    b = g.rx(d, "behavior", "CP1")
    assert b.returncode == 0, _out(b)
    rnd = g.open_round(d, "CP1")
    check("[CP5-T01] (fixture sanity) editing a src file next opens the step's normal full round, never a "
          "one-reviewer small-fix round (no recorded FULL-review baseline yet)",
          rnd.get("small_fix") is not True and len(rnd["reviewers"]) == 2, rnd)
    _open_and_approve_round(d, "CP1", rnd)
    r = g.rx(d, "complete", "CP1")
    check("[CP5-T01] `complete CP1` succeeds", r.returncode == 0, _out(r))
    msg = _commit_message(d)
    check("[CP5-T01] the commit message no longer says README.md's review was docs-only skipped",
          not _names_docs_only_skip(msg, "README.md"), msg)
    status = g.rx(d, "status")
    sout = _out(status)
    check("[CP5-T01] `status` (and, once reached, the run's final review) no longer says README.md's review "
          "was docs-only skipped", not _names_docs_only_skip(sout, "README.md"), sout)


def t02_full_review_clears_a_doc_edited_after_review_note():
    d = _project("cp5-t02", risk="normal")
    _approve_round1(d)
    check("[CP5-T02] (fixture sanity) round 1's full approval recorded last_full_review_fingerprint",
          bool(_cps(d).get("last_full_review_fingerprint")), _cps(d))
    # CP5-T02 (see _prime_checkpoint's own docstring): doc_edits_unreviewed primed directly on this temp
    # project's state.json only to reach the otherwise-unreachable mid-checkpoint precondition; the clearing
    # behavior under test is driven entirely through the real `reviewers`/`adv-record` CLI below.
    _prime_checkpoint(d, "CP1", doc_edits_unreviewed=["docs/notes.md"])
    check("[CP5-T02] (fixture sanity) doc_edits_unreviewed is primed before the next round opens",
          _cps(d).get("doc_edits_unreviewed") == ["docs/notes.md"], _cps(d))
    _append_lines(d / "src" / "f.js", 25, "t02")
    b = g.rx(d, "behavior", "CP1")
    assert b.returncode == 0, _out(b)
    rnd = g.open_round(d, "CP1")
    check("[CP5-T02] (fixture sanity) the 25-line code edit opens the step's normal full round, not a "
          "one-reviewer small-fix round", rnd.get("small_fix") is not True and len(rnd["reviewers"]) == 2, rnd)
    _open_and_approve_round(d, "CP1", rnd)
    check("[CP5-T02] right after `adv-record` records this full round approved, doc_edits_unreviewed is "
          "empty/absent", not _cps(d).get("doc_edits_unreviewed"), _cps(d))
    r = g.rx(d, "complete", "CP1")
    check("[CP5-T02] `complete CP1` succeeds with no further edits", r.returncode == 0, _out(r))
    msg = _commit_message(d)
    check("[CP5-T02] the commit message no longer says any doc was edited after review and not re-reviewed",
          not _names_skipped_review(msg, "docs/notes.md"), msg)
    status = g.rx(d, "status")
    check("[CP5-T02] `status` no longer says any doc was edited after review and not re-reviewed",
          not _names_skipped_review(_out(status), "docs/notes.md"), _out(status))


def t03_full_review_clears_a_small_fix_review_note():
    d = _project("cp5-t03", risk="normal")
    _approve_round1(d)
    _append_lines(d / "src" / "f.js", 5, "t03a")
    b = g.rx(d, "behavior", "CP1")
    assert b.returncode == 0, _out(b)
    rnd2 = g.open_round(d, "CP1")
    check("[CP5-T03] (fixture sanity) the 5-line fix opens a one-reviewer small-fix round",
          rnd2.get("small_fix") is True and len(rnd2["reviewers"]) == 1, rnd2)
    _open_and_approve_round(d, "CP1", rnd2)
    check("[CP5-T03] (fixture sanity) small_fix_review is recorded after that round's approval",
          bool(_cps(d).get("small_fix_review")), _cps(d))
    _append_lines(d / "src" / "f.js", 25, "t03b")
    b2 = g.rx(d, "behavior", "CP1")
    assert b2.returncode == 0, _out(b2)
    rnd3 = g.open_round(d, "CP1")
    check("[CP5-T03] (fixture sanity) a further code edit NOT small on its own since round 1's last FULL "
          "approval opens the step's normal full round",
          rnd3.get("small_fix") is not True and len(rnd3["reviewers"]) == 2, rnd3)
    _open_and_approve_round(d, "CP1", rnd3)
    check("[CP5-T03] the checkpoint's small_fix_review record is gone", not _cps(d).get("small_fix_review"),
          _cps(d))
    r = g.rx(d, "complete", "CP1")
    check("[CP5-T03] `complete CP1` succeeds", r.returncode == 0, _out(r))
    msg = _commit_message(d)
    check("[CP5-T03] the commit message no longer says the step was reviewed as a small fix by one reviewer",
          not _SMALL_FIX_RE.search(msg), msg)
    status = g.rx(d, "status")
    check("[CP5-T03] `status` no longer says the step was reviewed as a small fix by one reviewer",
          not _SMALL_FIX_RE.search(_out(status)), _out(status))


def t04_doc_edited_after_the_clearing_round_is_freshly_flagged():
    d, _log = _docs_only_project("cp5-t04")
    _to_adversarial_docs(d)
    r0 = g.rx(d, "reviewers", "CP1")
    assert r0.returncode == 0, _out(r0)  # CP4's own docs-only skip, same setup as CP5-T01
    (d / "src" / "a.js").write_text("export const a = 2;\n")
    b = g.rx(d, "behavior", "CP1")
    assert b.returncode == 0, _out(b)
    rnd = g.open_round(d, "CP1")
    _open_and_approve_round(d, "CP1", rnd)  # a full round clears the earlier docs-only-skip note (CP5-T01)
    check("[CP5-T04] (fixture sanity) CP1 is not yet completed", _cps(d)["status"] != "done", _cps(d))
    (d / "docs").mkdir(parents=True, exist_ok=True)
    (d / "docs" / "other.md").write_text("# Other\n\na doc never part of that full round's own diff.\n")
    r = g.rx(d, "complete", "CP1")
    check("[CP5-T04] `complete CP1` succeeds via the doc quick path", r.returncode == 0, _out(r))
    msg = _commit_message(d)
    check("[CP5-T04] the commit message names docs/other.md as edited after review and not re-reviewed",
          _names_skipped_review(msg, "docs/other.md"), msg)
    status = g.rx(d, "status")
    check("[CP5-T04] `status` names docs/other.md as edited after review and not re-reviewed",
          _names_skipped_review(_out(status), "docs/other.md"), _out(status))


def t05_a_small_fix_approval_never_clears_a_skipped_review_note():
    d = _project("cp5-t05", risk="normal")
    _approve_round1(d)
    # CP5-T05 (see _prime_checkpoint's own docstring): doc_edits_unreviewed primed directly on this temp
    # project's state.json only to reach the otherwise-unreachable mid-checkpoint precondition; what a small-fix
    # approval does and does not clear is driven entirely through the real `reviewers`/`adv-record` CLI below.
    _prime_checkpoint(d, "CP1", doc_edits_unreviewed=["docs/notes.md"])
    _append_lines(d / "src" / "f.js", 5, "t05")
    b = g.rx(d, "behavior", "CP1")
    assert b.returncode == 0, _out(b)
    rnd = g.open_round(d, "CP1")
    check("[CP5-T05] (fixture sanity) the 5-line fix opens a one-reviewer small-fix round",
          rnd.get("small_fix") is True and len(rnd["reviewers"]) == 1, rnd)
    _open_and_approve_round(d, "CP1", rnd)
    cps = _cps(d)
    check("[CP5-T05] doc_edits_unreviewed is unchanged: still names docs/notes.md",
          cps.get("doc_edits_unreviewed") == ["docs/notes.md"], cps)
    check("[CP5-T05] small_fix_review is now set alongside it", bool(cps.get("small_fix_review")), cps)


def t06_a_round_that_is_not_approved_clears_nothing():
    d = _project("cp5-t06", risk="normal")
    _approve_round1(d)
    _prime_checkpoint(d, "CP1", doc_edits_unreviewed=["docs/notes.md"])
    _append_lines(d / "src" / "f.js", 25, "t06")
    b = g.rx(d, "behavior", "CP1")
    assert b.returncode == 0, _out(b)
    rnd = g.open_round(d, "CP1")
    check("[CP5-T06] (fixture sanity) the 25-line fix opens the step's normal full round",
          rnd.get("small_fix") is not True and len(rnd["reviewers"]) == 2, rnd)
    blocker = [{"id": "B1", "severity": "blocker", "file": "src/f.js", "description": "unsafe write",
                "user_impact": "a user's saved data could be silently corrupted"}]
    paths = [g.write_review(d, rnd, rnd["reviewers"][0]["id"], blocker),
             g.write_review(d, rnd, rnd["reviewers"][1]["id"], findings=[])]
    radv = g.rx(d, "adv-record", "CP1", "--reviews", *paths)
    check("[CP5-T06] `adv-record` reports the round NOT approved (a blocking finding remains)",
          radv.returncode != 0, _out(radv))
    check("[CP5-T06] doc_edits_unreviewed is exactly what it was before this round opened",
          _cps(d).get("doc_edits_unreviewed") == ["docs/notes.md"], _cps(d))


def t07_one_full_approval_clears_every_lighter_review_record_at_once():
    d = _project("cp5-t07", risk="normal")
    _approve_round1(d)
    # CP5-T07 (see _prime_checkpoint's own docstring): doc_edits_unreviewed primed directly, alongside a REAL
    # small_fix_review set by driving an actual approved one-reviewer small-fix round through the CLI just below.
    _prime_checkpoint(d, "CP1", doc_edits_unreviewed=["docs/notes.md"])
    _append_lines(d / "src" / "f.js", 5, "t07a")
    b = g.rx(d, "behavior", "CP1")
    assert b.returncode == 0, _out(b)
    rnd2 = g.open_round(d, "CP1")
    check("[CP5-T07] (fixture sanity) the first fix opens a one-reviewer small-fix round",
          rnd2.get("small_fix") is True and len(rnd2["reviewers"]) == 1, rnd2)
    _open_and_approve_round(d, "CP1", rnd2)
    cps = _cps(d)
    check("[CP5-T07] (fixture sanity) both doc_edits_unreviewed and small_fix_review are present at once",
          bool(cps.get("doc_edits_unreviewed")) and bool(cps.get("small_fix_review")), cps)
    _append_lines(d / "src" / "f.js", 25, "t07b")
    b2 = g.rx(d, "behavior", "CP1")
    assert b2.returncode == 0, _out(b2)
    rnd3 = g.open_round(d, "CP1")
    check("[CP5-T07] (fixture sanity) a further, not-small code edit opens the step's normal full round",
          rnd3.get("small_fix") is not True and len(rnd3["reviewers"]) == 2, rnd3)
    _open_and_approve_round(d, "CP1", rnd3)
    cps2 = _cps(d)
    check("[CP5-T07] doc_edits_unreviewed is gone", not cps2.get("doc_edits_unreviewed"), cps2)
    check("[CP5-T07] small_fix_review is gone", not cps2.get("small_fix_review"), cps2)
    r = g.rx(d, "complete", "CP1")
    check("[CP5-T07] `complete CP1` afterward succeeds", r.returncode == 0, _out(r))
    msg = _commit_message(d)
    check("[CP5-T07] the commit message names neither record",
          not _names_skipped_review(msg, "docs/notes.md") and not _SMALL_FIX_RE.search(msg), msg)
    status = g.rx(d, "status")
    check("[CP5-T07] `status` names neither record",
          not _names_skipped_review(_out(status), "docs/notes.md") and not _SMALL_FIX_RE.search(_out(status)),
          _out(status))


def t08_skill_md_measures_the_small_fix_limit_against_the_last_full_review_approval():
    text = SKILL_PATH.read_text()
    m = re.search(r"^\*\*The small-fix one-reviewer round\.\*\*.*$", text, re.M)
    check("[CP5-T08] (fixture sanity) SKILL.md has the small-fix one-reviewer round paragraph", m is not None,
          "paragraph not found")
    para = m.group(0) if m else ""
    # Narrow to the clause that actually states the small_edit_lines limit itself (the parenthetical right
    # after "small_edit_lines", up to its own closing paren) -- never the later, unrelated sentence about the
    # round opening "instead of the step's full reviewer set", whose own "full reviewer" would otherwise also
    # satisfy a looser since-...-full-...-review/approval match without ever naming the limit's own baseline.
    clause_m = re.search(r"small_edit_lines.*?\)", para, re.S)
    check("[CP5-T08] (fixture sanity) the paragraph's small_edit_lines clause (its own parenthetical) is found",
          clause_m is not None, para)
    clause = clause_m.group(0) if clause_m else ""
    check("[CP5-T08] that clause no longer says the limit is counted simply 'since that approval' (which would "
          "misdescribe CP3's own add-up-across-small-fixes rule)",
          not bool(re.search(r"\bsince\s+that\s+approval\b", clause, re.I)), clause)
    check("[CP5-T08] that clause says the small_edit_lines limit is counted against changed lines since the "
          "step's LAST FULL review approval, naming 'full' review/approval as the baseline",
          bool(re.search(r"\bsince\b[^.\n]{0,40}\blast\b[^.\n]{0,20}\bfull\b[^.\n]{0,30}\b(review|approval)\b",
                         clause, re.I)), clause)


TESTS = [
    ("CP5-T01", t01_full_review_clears_a_docs_only_skip_note),
    ("CP5-T02", t02_full_review_clears_a_doc_edited_after_review_note),
    ("CP5-T03", t03_full_review_clears_a_small_fix_review_note),
    ("CP5-T04", t04_doc_edited_after_the_clearing_round_is_freshly_flagged),
    ("CP5-T05", t05_a_small_fix_approval_never_clears_a_skipped_review_note),
    ("CP5-T06", t06_a_round_that_is_not_approved_clears_nothing),
    ("CP5-T07", t07_one_full_approval_clears_every_lighter_review_record_at_once),
    ("CP5-T08", t08_skill_md_measures_the_small_fix_limit_against_the_last_full_review_approval),
]

# Case ids a later fix round appends to (kept empty now; a fix round adds its own entries here, never editing
# the planned tests above), the same pattern tests/test_quick_path_docs_step.py and its sibling suites use.
FIX_TESTS: list[tuple[str, object]] = []


def write_report():
    import os
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    all_cases = [*TESTS, *FIX_TESTS]
    cases = [{"id": cid, "status": "passed" if CASE_STATUS.get(cid, False) else "failed",
              "message": "" if CASE_STATUS.get(cid, False) else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid, _ in all_cases]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    for cid, fn in [*TESTS, *FIX_TESTS]:
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
