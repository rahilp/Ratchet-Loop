#!/usr/bin/env python3
"""
CP4 tests: docs-only steps skip adversarial review.   python3 tests/test_quick_path_docs_step.py

When a planned checkpoint's actual changes, since its own base_tree (not since any approval), are docs only
(CP1's `_classify_doc_code` split) and `doc_test_command` is configured, asking Ratchet to open that
checkpoint's adversarial review (`ratchet.py reviewers CPn`) must skip opening a reviewer round entirely:
instead it runs every test file that names the changed doc(s) and, if they pass, records the adversarial gate
passed directly, with the skip recorded for the commit message, `status` and the final review to show (which
files were never reviewed, and why). A step planned high risk, a step whose change also touches a non-doc
file, and a step where the docs quick path is off (`doc_test_command` unset) all still get the normal full
round. A docs-only edit that breaks a test naming that doc (even a test never named in this checkpoint's own
plan) must fail the review-open step, naming the failing test file, and `complete` must not commit.

Reuses tests/test_gates.py (`project`, `approve_plan`, `rx`, `git`, `say`, `guard`, `ENGINE`, `ENV`) for the
throwaway git project and the real-CLI-subprocess way every suite in this repo drives the engine. This file
builds its own one-checkpoint fixture (not test_gates.project()'s default) because CP4's whole point needs the
checkpoint's OWN planned test to check doc content directly -- never src/a.js -- so the diff since base_tree
really is docs only, unlike CP2/CP3's fixtures, which only go docs-only AFTER approval. `doc_test_command` is a
tiny fake Python script this file writes (never tests/run_all.py or any real suite): it logs every file path
it is called with to a log file, then fails if any doc file mentioned inside those files is missing a marker
string -- so a case can "break" a doc by deleting the marker (same pattern as tests/test_quick_path_docs.py's
CP2 fixture).

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP4-T01",
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
import test_gates as g          # noqa: E402  (project, approve_plan, rx, git, say, guard, ENGINE, ENV)

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

MARKER = "DOC_MARKER_OK"            # what the fake doc_test_command checks README.md for
INSTALL_PHRASE = "Install the thing"  # what CP1's own planned test checks README.md for, via real node:test

# A tiny fake doc_test_command (never a real test runner): logs every file path it is called with (one
# '|'-joined line per invocation) to the log path given as its first argument, then fails if any doc file
# mentioned inside those files (a plain *.md/*.mdx/*.rst/*.adoc path found in the file's own text) is missing
# MARKER. Copied from tests/test_quick_path_docs.py's CP2 fixture so both suites exercise doc_test_command the
# same way.
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
_DOC_TEST_SCRIPT = Path(_tmp.mkdtemp(prefix="ratchet-cp4-docscript-")) / "doc_test.py"
_DOC_TEST_SCRIPT.write_text(_DOC_TEST_SRC)


def _doc_test_command(log_path) -> str:
    return (f"{shlex.quote(sys.executable)} {shlex.quote(str(_DOC_TEST_SCRIPT))} "
            f"{shlex.quote(str(log_path))} {{files}}")


def _new_log():
    return Path(_tmp.mkdtemp(prefix="ratchet-cp4-doclog-")) / "log.txt"


def _out(r):
    return (r.stdout or "") + "\n" + (r.stderr or "")


def _project_base(name, risk=None, set_doc_test_command=True, extra_files=None, config=None):
    """A one-checkpoint project whose CP1 is submitted (not yet approved) with a planned test
    (tests/test_readme.test.js) that checks README.md content directly via real node:test -- never src/a.js --
    so a step that only edits README.md during the build is genuinely docs-only since base_tree, not merely
    after approval the way tests/test_quick_path_docs.py's CP2 fixtures are. `extra_files` writes any
    additional files (path -> content) before the init commit, e.g. a second, unplanned test file that also
    names README.md. Returns (project dir, doc-test-command log path or None)."""
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
    for path, content in (extra_files or {}).items():
        p = d / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "init")
    g.say(d, "please build the feature")
    r = g.rx(d, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr
    cfg_args = ["min_review_seconds=0", "providers=[]"]
    log = None
    if set_doc_test_command:
        log = _new_log()
        cfg_args.append(f"doc_test_command={_doc_test_command(log)}")
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
    return d, log


def _approved_docs_project(name, **kw):
    d, log = _project_base(name, **kw)
    g.approve_plan(d)
    return d, log


def _to_adversarial_docs(d, cp="CP1"):
    """Drives `cp` from tests-written through behavior passing, touching only README.md (never src/a.js): red
    fails because README.md lacks INSTALL_PHRASE yet; the implementation edit then adds both INSTALL_PHRASE (so
    CP1's own planned test passes) and MARKER (so a doc_test_command scan of README.md -- used by the
    docs-only skip check -- also passes) to README.md; behavior passes."""
    r = g.rx(d, "tests-written", cp)
    assert r.returncode == 0, _out(r)
    r = g.rx(d, "red", cp)
    assert r.returncode == 0, _out(r)
    (d / "README.md").write_text(f"# Title\n\n{INSTALL_PHRASE}\n\n{MARKER}\n\noriginal content, updated.\n")
    r = g.rx(d, "implemented", cp)
    assert r.returncode == 0, _out(r)
    r = g.rx(d, "behavior", cp)
    assert r.returncode == 0, _out(r)


def _state(d, slug="f"):
    return json.loads((Path(d) / f".ratchet/runs/{slug}/state.json").read_text())


def _gate(d, cp="CP1", gate="adversarial", slug="f"):
    return _state(d, slug)["checkpoints"][cp]["gates"][gate]


def _head(d):
    return g.git(d, "rev-parse", "HEAD").stdout.strip()


def _commit_message(d):
    return g.git(d, "log", "-1", "--format=%B").stdout


def _adv_round_dirs(d, cp="CP1", slug="f"):
    p = Path(d) / f".ratchet/runs/{slug}/reviews/{cp}"
    if not p.is_dir():
        return []
    return sorted(x.name for x in p.iterdir() if x.is_dir() and x.name.startswith("adv-round-"))


_DOCS_ONLY_WORDS = ("docs only", "doc only", "only docs", "only documentation", "documentation only", "doc-only")
_SKIP_WORDS = ("skip", "no review", "not reviewed", "never reviewed", "without review", "no reviewer")


def _names_docs_only_skip(text, filename) -> bool:
    """True when `text` both mentions `filename` and says, in some plain-English way, that this checkpoint's
    change was docs only and its review was skipped -- the visibility CP4-T01/T03/T04 require, matched loosely
    (several equivalent phrasings) since the exact wording is the implementation's call, not this file's."""
    if filename not in text:
        return False
    low = text.lower()
    return any(p in low for p in _DOCS_ONLY_WORDS) and any(w in low for w in _SKIP_WORDS)


_QUICK_PATH_OFF_PHRASES = ("doc_test_command", "docs quick path", "doc quick path")
_OFF_WORDS = ("off", "not configured", "no doc test", "not set", "disabled", "isn't configured", "is not set")


def _mentions_quick_path_off(text) -> bool:
    low = text.lower()
    return any(p in low for p in _QUICK_PATH_OFF_PHRASES) and any(w in low for w in _OFF_WORDS)


# ------------------------------------------------------------------------------------------------ cases

def t01_docs_only_review_opens_without_a_reviewer_round():
    d, _log = _approved_docs_project("cp4-t01")
    _to_adversarial_docs(d)
    r = g.rx(d, "reviewers", "CP1")
    out = _out(r)
    check("[CP4-T01] `reviewers CP1` succeeds for a docs-only step", r.returncode == 0, out)
    check("[CP4-T01] no adversarial round folder was created (reviews/CP1/adv-round-*)",
          _adv_round_dirs(d) == [], _adv_round_dirs(d))
    check("[CP4-T01] the adversarial gate is recorded passed, with no round and no adv-record call",
          _gate(d)["status"] == "passed", _gate(d))
    check("[CP4-T01] the skip is visible, naming README.md and 'docs only'",
          _names_docs_only_skip(out, "README.md"), out)


def t02_docs_only_step_completes_without_adv_record():
    d, _log = _approved_docs_project("cp4-t02")
    _to_adversarial_docs(d)
    r = g.rx(d, "reviewers", "CP1")
    check("[CP4-T02] `reviewers CP1` leaves the adversarial gate passed, with no round opened and no "
          "adv-record call", r.returncode == 0 and _gate(d)["status"] == "passed",
          _out(r) + "\n" + repr(_gate(d)))
    head_before = _head(d)
    r2 = g.rx(d, "complete", "CP1")
    out2 = _out(r2)
    check("[CP4-T02] `complete CP1` succeeds (exit 0) with no adv-record ever called",
          r2.returncode == 0, out2)
    check("[CP4-T02] a new commit was created",
          r2.returncode == 0 and _head(d) != head_before, (_head(d), head_before))
    check("[CP4-T02] the step is recorded done",
          r2.returncode == 0 and _state(d)["checkpoints"]["CP1"]["status"] == "done",
          _state(d)["checkpoints"]["CP1"])
    check("[CP4-T02] no error about a missing or unpassed adversarial gate appeared",
          r2.returncode == 0 and ("adversarial gate" not in out2.lower() or "passed" in out2.lower()), out2)


def t03_commit_message_names_the_skip_and_the_unreviewed_file():
    d, _log = _approved_docs_project("cp4-t03")
    _to_adversarial_docs(d)
    r = g.rx(d, "reviewers", "CP1")
    r2 = g.rx(d, "complete", "CP1")
    msg = _commit_message(d)
    check("[CP4-T03] the commit message says the review was skipped because the step changed only docs, "
          "and names README.md as not reviewed",
          r.returncode == 0 and r2.returncode == 0 and _names_docs_only_skip(msg, "README.md"),
          f"reviewers: {_out(r)}\ncomplete: {_out(r2)}\ncommit message: {msg}")


def t04_status_and_final_review_both_show_the_skip():
    d, _log = _approved_docs_project("cp4-t04")
    _to_adversarial_docs(d)
    r = g.rx(d, "reviewers", "CP1")
    r2 = g.rx(d, "complete", "CP1")
    setup_ok = r.returncode == 0 and r2.returncode == 0
    sj = g.rx(d, "status", "--json")
    sj_out = _out(sj)
    check("[CP4-T04] `status --json` right after CP1 completes names the skip and README.md",
          setup_ok and _names_docs_only_skip(sj_out, "README.md"),
          f"setup ok={setup_ok} reviewers={_out(r)} complete={_out(r2)}\n{sj_out}")
    st = g.rx(d, "status")
    st_out = _out(st)
    check("[CP4-T04] `status` (text), once the run reaches its final review, names the same skip and README.md",
          setup_ok and _names_docs_only_skip(st_out, "README.md"),
          f"setup ok={setup_ok}\n{st_out}")


def t05_docs_plus_code_change_gets_the_normal_full_review():
    d, _log = _approved_docs_project("cp4-t05")
    _to_adversarial_docs(d)
    (d / "src" / "a.js").write_text("export const a = 2;\n")
    r = g.rx(d, "reviewers", "CP1", "--json")
    out = _out(r)
    check("[CP4-T05] `reviewers CP1 --json` succeeds when docs and code both changed", r.returncode == 0, out)
    data = json.loads(r.stdout) if r.returncode == 0 else {}
    check("[CP4-T05] a normal round opens with real reviewer entries",
          bool(data.get("reviewers")), data)
    check("[CP4-T05] an adversarial round folder was created", _adv_round_dirs(d) != [], _adv_round_dirs(d))
    check("[CP4-T05] the adversarial gate is not marked passed by a skip",
          _gate(d)["status"] != "passed", _gate(d))
    status = g.rx(d, "status")
    check("[CP4-T05] `status` does not claim the review was skipped for docs only",
          not _names_docs_only_skip(_out(status), "README.md"), _out(status))


def t06_docs_only_edit_breaking_an_unplanned_doc_test_fails_and_does_not_commit():
    d, _log = _approved_docs_project(
        "cp4-t06",
        extra_files={"tests/doc-table.test.js": "// this suite's name mentions README.md on purpose\n"})
    _to_adversarial_docs(d)
    gate_before = dict(_gate(d))
    head_before = _head(d)
    (d / "README.md").write_text(f"# Title\n\n{INSTALL_PHRASE}\n\nthe marker is gone now.\n")
    r = g.rx(d, "reviewers", "CP1")
    out = _out(r)
    check("[CP4-T06] `reviewers CP1` fails when the docs-only edit breaks an unplanned doc-checking test",
          r.returncode != 0, out)
    check("[CP4-T06] the failure names the unplanned test file that checks README.md",
          "tests/doc-table.test.js" in out, out)
    check("[CP4-T06] the adversarial gate's stored status is unchanged",
          _gate(d)["status"] == gate_before["status"], (_gate(d), gate_before))
    r2 = g.rx(d, "complete", "CP1")
    check("[CP4-T06] `complete CP1` also fails", r2.returncode != 0, _out(r2))
    check("[CP4-T06] CP1 does not commit: HEAD is unchanged", _head(d) == head_before, (_head(d), head_before))


def t07_high_risk_docs_only_step_still_gets_its_full_review():
    d, _log = _approved_docs_project("cp4-t07", risk="high")
    _to_adversarial_docs(d)
    r = g.rx(d, "reviewers", "CP1", "--json")
    out = _out(r)
    check("[CP4-T07] `reviewers CP1 --json` succeeds for a high-risk docs-only step", r.returncode == 0, out)
    data = json.loads(r.stdout) if r.returncode == 0 else {}
    check("[CP4-T07] a normal round opens with real reviewer entries even though the diff is docs only",
          bool(data.get("reviewers")), data)
    check("[CP4-T07] an adversarial round folder was created", _adv_round_dirs(d) != [], _adv_round_dirs(d))
    check("[CP4-T07] the adversarial gate is not marked passed by a skip",
          _gate(d)["status"] != "passed", _gate(d))


def t08_docs_quick_path_off_still_gets_the_full_review():
    d, _log = _approved_docs_project("cp4-t08", set_doc_test_command=False)
    _to_adversarial_docs(d)
    r = g.rx(d, "reviewers", "CP1", "--json")
    out_json = _out(r)
    check("[CP4-T08] `reviewers CP1 --json` succeeds when the docs quick path is off", r.returncode == 0, out_json)
    data = json.loads(r.stdout) if r.returncode == 0 else {}
    check("[CP4-T08] a normal round opens with real reviewer entries", bool(data.get("reviewers")), data)
    check("[CP4-T08] the adversarial gate is not marked passed by a skip", _gate(d)["status"] != "passed", _gate(d))
    r2 = g.rx(d, "reviewers", "CP1")
    status = g.rx(d, "status")
    combined = _out(r2) + "\n" + _out(status)
    check("[CP4-T08] status or the round output says why: the docs quick path is off",
          _mentions_quick_path_off(combined), combined)


def t09_plan_summary_tells_the_user_about_both_quick_paths():
    d = g.project("cp4-t09", cps=1)  # plan submitted, not yet approved
    r = g.rx(d, "plan-summary")
    out = _out(r)
    low = out.lower()
    check("[CP4-T09] `plan-summary` says a step whose actual changes are docs only skips adversarial review",
          any(p in low for p in _DOCS_ONLY_WORDS) and "skip" in low and "review" in low, out)
    check("[CP4-T09] `plan-summary` says a doc edit made after review gets its naming tests re-run "
          "instead of a new review round",
          "after" in low and "review" in low
          and ("re-run" in low or "rerun" in low or "re run" in low or "naming test" in low)
          and ("new round" in low or "new review" in low or "reopen" in low or "without a new" in low), out)


TESTS = [
    ("CP4-T01", t01_docs_only_review_opens_without_a_reviewer_round),
    ("CP4-T02", t02_docs_only_step_completes_without_adv_record),
    ("CP4-T03", t03_commit_message_names_the_skip_and_the_unreviewed_file),
    ("CP4-T04", t04_status_and_final_review_both_show_the_skip),
    ("CP4-T05", t05_docs_plus_code_change_gets_the_normal_full_review),
    ("CP4-T06", t06_docs_only_edit_breaking_an_unplanned_doc_test_fails_and_does_not_commit),
    ("CP4-T07", t07_high_risk_docs_only_step_still_gets_its_full_review),
    ("CP4-T08", t08_docs_quick_path_off_still_gets_the_full_review),
    ("CP4-T09", t09_plan_summary_tells_the_user_about_both_quick_paths),
]

# Case ids a later fix round appends to (kept empty now; a fix round adds its own entries here, never editing
# the planned tests above).
FIX_TESTS: list[tuple[str, object]] = []

# ------------------------------------------------------------------------------------------- round 1 fix tests
# Appended after the planned CP4-T01..T09 cases above, which are left untouched. Each new fixture below is its
# own standalone function (never a change to `_project_base`/`_approved_docs_project` above) so every existing
# line in this file stays exactly as it was.

# A doc_test_command stand-in that mutates a tracked code file as a side effect (a generator, a snapshot
# updater), still exits 0 regardless of the doc content it was handed, so a case can assert that the
# docs-only skip refuses to mark the gate "passed" on a tree that is not the one it actually checked. Same
# pattern as tests/test_quick_path_docs.py's CP2-X02/X03 fixture (`_mutating_doc_test_command`), copied here
# rather than imported so importing this file never runs that other file's own module-level test execution.
_CP4_MUTATE_SRC = (
    "import sys, pathlib\n"
    "target = pathlib.Path(sys.argv[1])\n"
    "target.write_text(target.read_text() + '\\n// mutated by doc test\\n')\n"
    "sys.exit(0)\n"
)
_CP4_MUTATE_SCRIPT = Path(_tmp.mkdtemp(prefix="ratchet-cp4-mutate-")) / "mutate.py"
_CP4_MUTATE_SCRIPT.write_text(_CP4_MUTATE_SRC)


def _cp4_mutating_doc_test_command(target="src/a.js"):
    return (f"{shlex.quote(sys.executable)} {shlex.quote(str(_CP4_MUTATE_SCRIPT))} "
            f"{shlex.quote(target)} {{files}}")


def _project_base_with_reviewers(name, reviewers, risk=None):
    """The same one-checkpoint, docs-only-since-base_tree project shape `_project_base` above builds, but with
    CP1's own `reviewers` pinned in the plan -- a new, standalone fixture (never a change to `_project_base`)
    since S2 needs a checkpoint that names its own reviewer list, which `_project_base` never sets."""
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
    r = g.rx(d, "config", "--set", "min_review_seconds=0", "providers=[]",
             f"doc_test_command={_doc_test_command(log)}")
    assert r.returncode == 0, r.stdout + r.stderr
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cp1 = {"id": "CP1", "title": "Update the README", "summary": "s",
           "done_when": ["README names the install steps"],
           "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": risk or "normal",
           "reviewers": reviewers,
           "tests": {"files": ["tests/test_readme.test.js"], "command": "node --test tests/test_readme.test.js"}}
    plan = {"feature": "x", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": [cp1]}
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
    (run / "checkpoints.json").write_text(json.dumps(plan))
    r = g.rx(d, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    g.approve_plan(d)
    return d, log


def x01_mutating_doc_test_command_refuses_the_docs_only_skip():
    """[CP4-X01] host-alt-standards/S1, host-alt-correctness/A1: a doc_test_command that mutates a tracked
    code file as a side effect must never let that mutation be folded into the fingerprint the docs-only skip
    stamps "passed". `reviewers CP1` must refuse (die), leave the adversarial gate's stored status unchanged
    (never "passed"), and name the doc test command as the cause -- the same guard `complete`'s CP2 doc quick
    path already uses."""
    d, _log = _project_base("cp4-x01", set_doc_test_command=False,
                            config=[f"doc_test_command={_cp4_mutating_doc_test_command()}"])
    g.approve_plan(d)
    _to_adversarial_docs(d)
    gate_before = dict(_gate(d))
    r = g.rx(d, "reviewers", "CP1")
    out = _out(r)
    check("[CP4-X01] `reviewers CP1` refuses when the doc test command mutates a tracked file",
          r.returncode != 0, out)
    check("[CP4-X01] the refusal names the doc test command as the cause",
          "doc test command" in out.lower(), out)
    check("[CP4-X01] the adversarial gate's stored status is unchanged and never marked passed",
          _gate(d)["status"] == gate_before["status"] and _gate(d)["status"] != "passed",
          (_gate(d), gate_before))


def x02_pinned_reviewers_checkpoint_keeps_its_normal_round_even_when_docs_only():
    """[CP4-X02] host-alt-standards/S2: a checkpoint that pins its own `reviewers` list never takes the
    docs-only skip -- the same rule `_is_small_fix_round` already applies for CP3's small-fix downgrade. Even
    though CP1's only change since base_tree is docs only (README.md), its own normal full round, naming the
    pinned reviewer, must still open."""
    d, _log = _project_base_with_reviewers("cp4-x02", ["host-standards"])
    _to_adversarial_docs(d)
    r = g.rx(d, "reviewers", "CP1", "--json")
    out = _out(r)
    check("[CP4-X02] `reviewers CP1 --json` succeeds for a pinned-reviewer docs-only step", r.returncode == 0, out)
    data = json.loads(r.stdout) if r.returncode == 0 else {}
    check("[CP4-X02] a normal round opens (not the docs-only skip): reviewer entries are present",
          bool(data.get("reviewers")), data)
    check("[CP4-X02] the round names the pinned reviewer id",
          any("host-standards" in rr.get("id", "") for rr in data.get("reviewers", [])), data)
    check("[CP4-X02] an adversarial round folder was created (the step was not skipped)",
          _adv_round_dirs(d) != [], _adv_round_dirs(d))
    check("[CP4-X02] the adversarial gate is not marked passed by a skip",
          _gate(d)["status"] != "passed", _gate(d))


def x03_docs_only_skip_prints_valid_json_with_json_flag():
    """[CP4-X03] host-alt-standards/S3: `ratchet.py reviewers CPn --json` must print valid, parseable JSON on
    the docs-only skip path too, the same `--json` contract every other branch of this command honors, naming
    the files skipped and the doc tests run instead of a plain-English sentence."""
    d, _log = _approved_docs_project("cp4-x03")
    _to_adversarial_docs(d)
    r = g.rx(d, "reviewers", "CP1", "--json")
    out = _out(r)
    check("[CP4-X03] `reviewers CP1 --json` succeeds for a docs-only step", r.returncode == 0, out)
    try:
        data = json.loads(r.stdout)
        parsed_ok = True
    except (json.JSONDecodeError, ValueError):
        data = {}
        parsed_ok = False
    check("[CP4-X03] stdout is valid, parseable JSON (not plain text)", parsed_ok, out)
    check("[CP4-X03] the JSON names the checkpoint and the docs-only skip, with README.md among its files",
          parsed_ok and data.get("checkpoint") == "CP1"
          and "README.md" in (data.get("docs_only_skip") or {}).get("files", []), data)
    check("[CP4-X03] the adversarial gate is recorded passed",
          _gate(d)["status"] == "passed", _gate(d))


FIX_TESTS.extend([
    ("CP4-X01", x01_mutating_doc_test_command_refuses_the_docs_only_skip),
    ("CP4-X02", x02_pinned_reviewers_checkpoint_keeps_its_normal_round_even_when_docs_only),
    ("CP4-X03", x03_docs_only_skip_prints_valid_json_with_json_flag),
])


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
