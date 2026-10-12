#!/usr/bin/env python3
"""
CP1 tests: Ratchet shows which changes are docs.   python3 tests/test_quick_path.py

`ratchet.py changes CPn --json` reports, read-only, what changed since a checkpoint's step started (its
approved `base_tree`): which files are docs, which are code, how many code lines changed, which test files
name each changed doc by filename, and whether the docs quick path itself is on (it needs `doc_test_command`
configured). Running it must never mutate run state, the git working tree, or create a commit.

Reuses tests/test_gates.py (`project`, `approve_plan`, `rx`, `git`, `ENGINE`, `ENV`) for the throwaway git
project and the real-CLI-subprocess way every suite in this repo drives the engine. `project()` records CP1's
`base_tree` at `approve_plan()` time as a `git write-tree` fingerprint of the *working tree* (tracked and
untracked, minus .ratchet/) -- so fixture files only need to exist on disk before `approve_plan()`, no commit
required.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP1-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                     # noqa: E402
import test_gates as g          # noqa: E402  (project, approve_plan, rx, git, ENGINE, ENV)

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

DOC_TEST_COMMAND = "doc_test_command=python3 tests/run_all.py {files}"


def _fixture_project(name, set_doc_test_command=True):
    """A throwaway project with every doc-shaped file CP1's cases need, all present before `approve_plan()` so
    they're part of CP1's recorded base_tree: a README (plus the test file that names it), a SKILL.md, an
    agents/*.md file, the default DESIGN.md, and three more docs (one named by its own test file, one named by
    a different test file, one named by no test file at all) -- plus src/a.js from test_gates's own project()
    fixture, used by the code-edit case."""
    config = [DOC_TEST_COMMAND] if set_doc_test_command else []
    d = g.project(name, cps=1, config=config)
    (d / "README.md").write_text("# Title\n\nThe original sentence.\n")
    (d / "tests" / "readme_check.test.js").write_text("// this suite's name mentions README.md on purpose\n")
    (d / "agents").mkdir(parents=True, exist_ok=True)
    (d / "agents" / "helper.md").write_text("# Helper\n\noriginal helper text.\n")
    (d / "skills" / "x").mkdir(parents=True, exist_ok=True)
    (d / "skills" / "x" / "SKILL.md").write_text("# Skill X\n\noriginal skill text.\n")
    (d / "DESIGN.md").write_text("# Design\n\noriginal design text.\n")
    (d / "docs").mkdir(parents=True, exist_ok=True)
    (d / "docs" / "guide.md").write_text("# Guide\n\noriginal guide text.\n")
    (d / "tests" / "test_guide.py").write_text("# this suite's name mentions docs/guide.md on purpose\n")
    (d / "CHANGELOG.md").write_text("# Changelog\n\noriginal changelog text.\n")
    (d / "tests" / "changelog.test.js").write_text("// this suite's name mentions CHANGELOG.md on purpose\n")
    (d / "NOTES.rst").write_text("Notes\n=====\n\noriginal notes text.\n")
    g.approve_plan(d)
    return d


def run_changes(d, cp="CP1"):
    """`ratchet.py changes CPn --json`, parsed defensively: before the `changes` subcommand exists, argparse
    refuses it (exit 2, empty stdout) rather than crashing this test file, so `data` is just {} and every
    field-level assertion below fails on its own, instead of the whole case crashing."""
    r = g.rx(d, "changes", cp, "--json")
    data = {}
    if r.stdout.strip():
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError:
            data = {}
    return r, data


def _tree_id(d):
    """The same `git write-tree` fingerprint `fingerprint()` in ratchet.py computes (tracked + untracked, minus
    .ratchet/), taken independently of the engine so CP1-T07 can prove `changes` never moves it."""
    with _tmp.mkdtemp() as td:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(td) / "index"))
        subprocess.run(["git", "read-tree", "HEAD"], cwd=d, env=env, capture_output=True)
        subprocess.run(["git", "add", "-A", "--", ".", ":(exclude).ratchet"], cwd=d, env=env, capture_output=True)
        t = subprocess.run(["git", "write-tree"], cwd=d, env=env, capture_output=True, text=True)
        return t.stdout.strip()


def _ratchet_files(d):
    base = Path(d) / ".ratchet"
    if not base.is_dir():
        return []
    return sorted(str(p.relative_to(base)) for p in base.rglob("*") if p.is_file())


# ------------------------------------------------------------------------------------------------ cases

def t01_readme_only_is_docs_only_and_names_its_test():
    d = _fixture_project("cp1-t01")
    (d / "README.md").write_text("# Title\n\nThe edited sentence.\n")
    r, data = run_changes(d)
    check("[CP1-T01] `changes CP1` exits 0 (read-only report)", r.returncode == 0, r.stdout + r.stderr)
    check("[CP1-T01] the change is reported as docs only", data.get("docs_only") is True, data)
    check("[CP1-T01] README.md is the one doc file listed", data.get("doc_files") == ["README.md"], data)
    check("[CP1-T01] no code files are listed", data.get("code_files") == [], data)
    check("[CP1-T01] tests/readme_check.test.js is listed as naming README.md",
          "tests/readme_check.test.js" in (data.get("doc_test_files") or []), data)
    check("[CP1-T01] tests/cp1.test.js (names no doc) is not listed",
          "tests/cp1.test.js" not in (data.get("doc_test_files") or []), data)


def t02_readme_plus_code_reports_code_and_line_count():
    d = _fixture_project("cp1-t02")
    (d / "README.md").write_text("# Title\n\nThe edited sentence.\n")
    (d / "src" / "a.js").write_text(
        "export const a = 11;\nexport const b = 22;\nexport const c = 33;\nexport const d = 44;\n")
    numstat = subprocess.run(["git", "diff", "--numstat"], cwd=d, capture_output=True, text=True).stdout
    check("[CP1-T02] (fixture sanity) git diff --numstat shows src/a.js as 4 added, 1 removed",
          re.search(r"^4\t1\tsrc/a\.js$", numstat, re.M) is not None, numstat)
    r, data = run_changes(d)
    check("[CP1-T02] `changes CP1` exits 0 (read-only report)", r.returncode == 0, r.stdout + r.stderr)
    check("[CP1-T02] the change is reported as touching code, not docs only", data.get("docs_only") is False, data)
    check("[CP1-T02] src/a.js is listed among the code files", "src/a.js" in (data.get("code_files") or []), data)
    check("[CP1-T02] the reported code-lines-changed count is exactly 5", data.get("code_lines_changed") == 5, data)


def t03_skill_md_alone_is_code_not_docs():
    d = _fixture_project("cp1-t03")
    (d / "skills" / "x" / "SKILL.md").write_text("# Skill X\n\nedited skill text.\n")
    r, data = run_changes(d)
    check("[CP1-T03] `changes CP1` exits 0 (read-only report)", r.returncode == 0, r.stdout + r.stderr)
    check("[CP1-T03] the change is reported as touching code, not docs only", data.get("docs_only") is False, data)
    check("[CP1-T03] skills/x/SKILL.md never appears in the doc files",
          "skills/x/SKILL.md" not in (data.get("doc_files") or []), data)
    check("[CP1-T03] skills/x/SKILL.md appears in the code files",
          "skills/x/SKILL.md" in (data.get("code_files") or []), data)


def t04_agents_md_glob_excludes_by_folder_not_name():
    d = _fixture_project("cp1-t04")
    (d / "agents" / "helper.md").write_text("# Helper\n\nedited helper text.\n")
    r, data = run_changes(d)
    check("[CP1-T04] `changes CP1` exits 0 (read-only report)", r.returncode == 0, r.stdout + r.stderr)
    check("[CP1-T04] the change is reported as touching code, not docs only", data.get("docs_only") is False, data)
    check("[CP1-T04] agents/helper.md never appears in the doc files, proving the agents/*.md glob matched",
          "agents/helper.md" not in (data.get("doc_files") or []), data)
    check("[CP1-T04] agents/helper.md appears in the code files",
          "agents/helper.md" in (data.get("code_files") or []), data)


def t05_configured_design_doc_excluded_despite_md_extension():
    d = _fixture_project("cp1-t05")
    (d / "DESIGN.md").write_text("# Design\n\nedited design text.\n")
    r, data = run_changes(d)
    check("[CP1-T05] `changes CP1` exits 0 (read-only report)", r.returncode == 0, r.stdout + r.stderr)
    check("[CP1-T05] the change is reported as touching code, not docs only", data.get("docs_only") is False, data)
    check("[CP1-T05] DESIGN.md never appears in the doc files even though it matches *.md",
          "DESIGN.md" not in (data.get("doc_files") or []), data)


def t06_no_doc_test_command_turns_quick_path_off_with_reason():
    d = _fixture_project("cp1-t06", set_doc_test_command=False)
    (d / "README.md").write_text("# Title\n\nThe edited sentence.\n")
    r, data = run_changes(d)
    check("[CP1-T06] `changes CP1` exits 0 (read-only report)", r.returncode == 0, r.stdout + r.stderr)
    check("[CP1-T06] the diff itself is still reported as docs only", data.get("docs_only") is True, data)
    qp = data.get("doc_quick_path") or {}
    check("[CP1-T06] the docs quick path is reported as off", qp.get("enabled") is False, data)
    check("[CP1-T06] the stated reason names doc_test_command as not configured",
          bool(re.search(r"doc_test_command", str(qp.get("reason") or ""), re.I)), data)


def t07_running_changes_changes_nothing():
    d = _fixture_project("cp1-t07")
    (d / "README.md").write_text("# Title\n\nThe edited sentence.\n")
    run_f = d / ".ratchet" / "runs" / "f"
    state_path, cps_path = run_f / "state.json", run_f / "checkpoints.json"

    def snapshot():
        head = g.git(d, "rev-parse", "HEAD").stdout.strip()
        return (state_path.read_bytes(), cps_path.read_bytes(), _tree_id(d), head, _ratchet_files(d))

    before = snapshot()
    r1, _ = run_changes(d)
    check("[CP1-T07] first `changes CP1` run exits 0", r1.returncode == 0, r1.stdout + r1.stderr)
    after1 = snapshot()
    r2, _ = run_changes(d)
    check("[CP1-T07] second `changes CP1` run exits 0", r2.returncode == 0, r2.stdout + r2.stderr)
    after2 = snapshot()
    check("[CP1-T07] state.json is byte-identical before and after each run",
          before[0] == after1[0] == after2[0], (len(before[0]), len(after1[0]), len(after2[0])))
    check("[CP1-T07] checkpoints.json is byte-identical before and after each run",
          before[1] == after1[1] == after2[1], (len(before[1]), len(after1[1]), len(after2[1])))
    check("[CP1-T07] the working tree's git write-tree fingerprint is unchanged",
          before[2] == after1[2] == after2[2], (before[2], after1[2], after2[2]))
    check("[CP1-T07] no commit is created", before[3] == after1[3] == after2[3], (before[3], after1[3], after2[3]))
    check("[CP1-T07] no new file appears anywhere under .ratchet/", before[4] == after1[4] == after2[4],
          "added: " + str(sorted(set(after2[4]) - set(before[4]))))


def t08_no_changes_reports_cleanly():
    d = _fixture_project("cp1-t08")
    r, data = run_changes(d)
    check("[CP1-T08] `changes CP1` exits 0 (read-only report)", r.returncode == 0, r.stdout + r.stderr)
    check("[CP1-T08] zero doc files are reported", data.get("doc_files") == [], data)
    check("[CP1-T08] zero code files are reported", data.get("code_files") == [], data)
    check("[CP1-T08] zero code lines changed are reported", data.get("code_lines_changed") == 0, data)
    check("[CP1-T08] no stray error is printed on stderr", r.stderr.strip() == "", r.stderr)


def t09_multiple_docs_matched_without_guessing():
    d = _fixture_project("cp1-t09")
    (d / "docs" / "guide.md").write_text("# Guide\n\nedited guide text.\n")
    (d / "CHANGELOG.md").write_text("# Changelog\n\nedited changelog text.\n")
    (d / "NOTES.rst").write_text("Notes\n=====\n\nedited notes text.\n")
    r, data = run_changes(d)
    check("[CP1-T09] `changes CP1` exits 0 (read-only report)", r.returncode == 0, r.stdout + r.stderr)
    check("[CP1-T09] the change is reported as docs only", data.get("docs_only") is True, data)
    check("[CP1-T09] all three edited docs are listed as doc files",
          set(data.get("doc_files") or []) == {"docs/guide.md", "CHANGELOG.md", "NOTES.rst"}, data)
    check("[CP1-T09] the doc-test-files list contains exactly the two tests that name their own doc, each once, "
          "and never because it names the other doc",
          set(data.get("doc_test_files") or []) == {"tests/test_guide.py", "tests/changelog.test.js"}, data)


def t10_non_md_doc_extension_is_still_a_doc():
    d = _fixture_project("cp1-t10")
    (d / "NOTES.rst").write_text("Notes\n=====\n\nedited notes text.\n")
    r, data = run_changes(d)
    check("[CP1-T10] `changes CP1` exits 0 (read-only report)", r.returncode == 0, r.stdout + r.stderr)
    check("[CP1-T10] the change is reported as docs only", data.get("docs_only") is True, data)
    check("[CP1-T10] NOTES.rst is listed as the doc file changed", "NOTES.rst" in (data.get("doc_files") or []),
          data)


TESTS = [
    ("CP1-T01", t01_readme_only_is_docs_only_and_names_its_test),
    ("CP1-T02", t02_readme_plus_code_reports_code_and_line_count),
    ("CP1-T03", t03_skill_md_alone_is_code_not_docs),
    ("CP1-T04", t04_agents_md_glob_excludes_by_folder_not_name),
    ("CP1-T05", t05_configured_design_doc_excluded_despite_md_extension),
    ("CP1-T06", t06_no_doc_test_command_turns_quick_path_off_with_reason),
    ("CP1-T07", t07_running_changes_changes_nothing),
    ("CP1-T08", t08_no_changes_reports_cleanly),
    ("CP1-T09", t09_multiple_docs_matched_without_guessing),
    ("CP1-T10", t10_non_md_doc_extension_is_still_a_doc),
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


# ------------------------------------------------------------------------------------------------ CP1 fix-round
# tests (adversarial round 1): appended after the planned CP1-T01..T10 cases above, which are left untouched.
# Run inline, below, before `main()` (which only knows about the original TESTS list) so their pass/fail still
# folds into this file's overall PASS/FAIL totals and exit code.


def x01_binary_code_change_reports_unknown_not_zero():
    """[CP1-X01] A code file git can't diff as text (binary) must be reported as an unknown-size change, never
    as zero changed lines, so it can never read as a small change."""
    d = _fixture_project("cp1-x01")
    (d / "src" / "logo.bin").write_bytes(bytes([0, 1, 2, 3, 255, 254, 0, 9, 8, 7]))
    r, data = run_changes(d)
    check("[CP1-X01] `changes CP1` exits 0 (read-only report)", r.returncode == 0, r.stdout + r.stderr)
    check("[CP1-X01] the binary file is listed as a code file, not a doc",
          "src/logo.bin" in (data.get("code_files") or []), data)
    check("[CP1-X01] the change is reported as unknown size, not zero",
          data.get("code_lines_unknown") is True and data.get("code_lines_changed") is None, data)


def x02_since_approval_reports_not_approved_yet():
    """[CP1-X02] Before the adversarial gate has ever recorded an approval fingerprint, `since_approval` says
    plainly that there is no approval yet, instead of silently comparing against the wrong tree."""
    d = _fixture_project("cp1-x02")
    (d / "README.md").write_text("# Title\n\nedited before any approval.\n")
    r, data = run_changes(d)
    sa = data.get("since_approval") or {}
    check("[CP1-X02] `changes CP1` exits 0 (read-only report)", r.returncode == 0, r.stdout + r.stderr)
    check("[CP1-X02] since_approval.approved is False before any adversarial approval", sa.get("approved") is False,
          data)
    check("[CP1-X02] since_approval gives a plain reason naming the adversarial gate",
          bool(re.search(r"adversarial", str(sa.get("reason") or ""), re.I)), data)
    check("[CP1-X02] since_approval reports no files while there is no approval to measure from",
          sa.get("doc_files") == [] and sa.get("code_files") == [], data)


def _approve_adversarial(d, cp="CP1"):
    """Drives `cp` through tests-written/red/implemented/behavior and one clean adversarial round (no
    findings), so its gate records the approval fingerprint `since_approval` measures from."""
    g.to_adversarial(d, cp)
    rnd = g.open_round(d, cp)
    rid = g.R0(rnd)
    path = g.write_review(d, rnd, rid, findings=[])
    r = g.rx(d, "adv-record", cp, "--reviews", path)
    assert r.returncode == 0, r.stdout + r.stderr


def x03_doc_edit_after_approval_is_docs_only_since_approval():
    """[CP1-X03] An approved code step followed by a doc edit must show that edit as docs-only in
    `since_approval`, separately from the whole step's own (code-touching) changes since base_tree."""
    d = _fixture_project("cp1-x03")
    _approve_adversarial(d)
    (d / "README.md").write_text("# Title\n\nedited after approval.\n")
    r, data = run_changes(d)
    sa = data.get("since_approval") or {}
    check("[CP1-X03] `changes CP1` exits 0 (read-only report)", r.returncode == 0, r.stdout + r.stderr)
    check("[CP1-X03] since_approval reports an approval", sa.get("approved") is True, data)
    check("[CP1-X03] since_approval reports the post-approval edit as docs only", sa.get("docs_only") is True, data)
    check("[CP1-X03] README.md is the doc file changed since approval", sa.get("doc_files") == ["README.md"], data)
    check("[CP1-X03] no code files changed since approval", sa.get("code_files") == [], data)
    check("[CP1-X03] the whole-step report (since base_tree) still shows the step touched code",
          data.get("docs_only") is False, data)


def x04_small_code_fix_after_approval_is_reported_with_its_line_count():
    """[CP1-X04] A small code fix made after approval is reported in `since_approval` with its own plain line
    count, separate from the whole step's report."""
    d = _fixture_project("cp1-x04")
    _approve_adversarial(d)
    (d / "src" / "f.js").write_text("export const f = () => 1;\nexport const g = () => 2;\n")
    r, data = run_changes(d)
    sa = data.get("since_approval") or {}
    check("[CP1-X04] `changes CP1` exits 0 (read-only report)", r.returncode == 0, r.stdout + r.stderr)
    check("[CP1-X04] since_approval reports touching code, not docs only", sa.get("docs_only") is False, data)
    check("[CP1-X04] src/f.js is listed among since_approval's code files",
          "src/f.js" in (sa.get("code_files") or []), data)
    check("[CP1-X04] since_approval's code-lines-changed count is a plain number at or under small_edit_lines (20)",
          isinstance(sa.get("code_lines_changed"), int) and sa.get("code_lines_changed") <= 20, data)
    check("[CP1-X04] since_approval does not report the change as unknown size",
          sa.get("code_lines_unknown") is False, data)


def x05_larger_code_fix_after_approval_is_reported_with_its_full_line_count():
    """[CP1-X05] A larger code fix made after approval (over the small_edit_lines ceiling) is still reported
    factually in `since_approval`, with its real line count -- `changes` itself makes no small/full decision."""
    d = _fixture_project("cp1-x05")
    _approve_adversarial(d)
    big = "\n".join(f"export const v{i} = {i};" for i in range(30))
    (d / "src" / "f.js").write_text("export const f = () => 1;\n" + big + "\n")
    r, data = run_changes(d)
    sa = data.get("since_approval") or {}
    check("[CP1-X05] `changes CP1` exits 0 (read-only report)", r.returncode == 0, r.stdout + r.stderr)
    check("[CP1-X05] src/f.js is listed among since_approval's code files",
          "src/f.js" in (sa.get("code_files") or []), data)
    check("[CP1-X05] since_approval's code-lines-changed count reflects the full edit, well over small_edit_lines "
          "(20)", isinstance(sa.get("code_lines_changed"), int) and sa.get("code_lines_changed") > 20, data)


def x06_doc_reverted_after_approval_still_names_its_test_in_since_approval():
    """[CP1-X06] A doc edited during implementation, approved, then reverted to its checkpoint-start content
    disappears from the base_tree comparison (no net change there) but must still show up -- and still name its
    test -- in since_approval, which is measured from the approval fingerprint that saw the edit."""
    d = _fixture_project("cp1-x06")
    original = (d / "README.md").read_text()
    (d / "README.md").write_text("# Title\n\nedited during implementation.\n")
    _approve_adversarial(d)
    (d / "README.md").write_text(original)  # reverted to its checkpoint-start content after approval
    r, data = run_changes(d)
    sa = data.get("since_approval") or {}
    check("[CP1-X06] `changes CP1` exits 0 (read-only report)", r.returncode == 0, r.stdout + r.stderr)
    check("[CP1-X06] README.md is reported as changed in since_approval despite matching base_tree again",
          sa.get("doc_files") == ["README.md"], data)
    check("[CP1-X06] since_approval's doc_test_files names the test that mentions README.md",
          "tests/readme_check.test.js" in (sa.get("doc_test_files") or []), data)


FIX_TESTS = [
    ("CP1-X01", x01_binary_code_change_reports_unknown_not_zero),
    ("CP1-X02", x02_since_approval_reports_not_approved_yet),
    ("CP1-X03", x03_doc_edit_after_approval_is_docs_only_since_approval),
    ("CP1-X04", x04_small_code_fix_after_approval_is_reported_with_its_line_count),
    ("CP1-X05", x05_larger_code_fix_after_approval_is_reported_with_its_full_line_count),
    ("CP1-X06", x06_doc_reverted_after_approval_still_names_its_test_in_since_approval),
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
    up by `main()` only at call time, below) rather than editing `write_report()`'s own body."""
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
# round 1 fix-round tests: appended after the CP1 fix-round block above, which is left untouched. Same inline
# pattern: run below, before `main()` (which only knows about the original TESTS list), so their pass/fail still
# folds into this file's overall PASS/FAIL totals and exit code.
import ast  # noqa: E402  (new top-level import, added outside any planned test, for the AST check below)


def x07_nested_directory_agents_md_excluded_at_any_depth():
    """[CP1-X07] A directory-shaped doc_exclude_globs entry like "agents/*.md" must exclude a matching file at
    any depth, not only directly under a root-level "agents/" folder (CP1-T04's case), so a normal per-package
    instruction-file layout (e.g. apps/bot/agents/x.md) is never misclassified as a doc."""
    d = _fixture_project("cp1-x07")
    (d / "apps" / "bot" / "agents").mkdir(parents=True, exist_ok=True)
    (d / "apps" / "bot" / "agents" / "x.md").write_text("# Agent\n\nedited agent text.\n")
    r, data = run_changes(d)
    check("[CP1-X07] `changes CP1` exits 0 (read-only report)", r.returncode == 0, r.stdout + r.stderr)
    check("[CP1-X07] the change is reported as touching code, not docs only", data.get("docs_only") is False, data)
    check("[CP1-X07] apps/bot/agents/x.md never appears in the doc files",
          "apps/bot/agents/x.md" not in (data.get("doc_files") or []), data)
    check("[CP1-X07] apps/bot/agents/x.md appears in the code files",
          "apps/bot/agents/x.md" in (data.get("code_files") or []), data)


def x08_classify_changes_reuses_the_shared_doc_code_split_helper():
    """[CP1-X08] `classify_changes` must call the one shared `_classify_doc_code` helper for its doc/code
    split rather than re-deriving its own copy of the rule, so `ratchet.py changes` and `ratchet.py complete`
    can never disagree about what counts as a doc."""
    src = g.ENGINE.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(g.ENGINE))
    fn = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "classify_changes"), None)
    check("[CP1-X08] classify_changes is found in ratchet.py", fn is not None, "classify_changes not found")
    called = {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)} if fn else set()
    check("[CP1-X08] classify_changes calls the shared _classify_doc_code helper for its doc/code split",
          "_classify_doc_code" in called, sorted(called))


FIX_TESTS2 = [
    ("CP1-X07", x07_nested_directory_agents_md_excluded_at_any_depth),
    ("CP1-X08", x08_classify_changes_reuses_the_shared_doc_code_split_helper),
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
    module-level `write_report` name again (looked up by `main()` only at call time, below)."""
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = [{"id": cid, "status": "passed" if CASE_STATUS.get(cid, False) else "failed",
              "message": "" if CASE_STATUS.get(cid, False) else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid, _ in [*TESTS, *FIX_TESTS, *FIX_TESTS2]]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


write_report = _write_report_with_fix_tests2


# ------------------------------------------------------------------------------------------------ CP2 adversarial
# round 3 fix-round test: appended after the FIX_TESTS2 block above, which is left untouched. Same inline
# pattern, numbering continuing from CP1-X08 above: run below, before `main()` (which only knows about the
# original TESTS list), so its pass/fail still folds into this file's overall PASS/FAIL totals and exit code.

def x09_mdx_is_not_a_doc_by_default():
    """[CP1-X09] MDX routinely carries executable JSX (Next.js pages, Docusaurus, Storybook), so a changed
    .mdx file must be reported as code, not a doc, under the default doc_globs."""
    d = _fixture_project("cp1-x09")
    (d / "docs" / "page.mdx").write_text("export const meta = { title: 'Page' };\n\n# Page\n")
    r, data = run_changes(d)
    check("[CP1-X09] `changes CP1` exits 0 (read-only report)", r.returncode == 0, r.stdout + r.stderr)
    check("[CP1-X09] docs/page.mdx is not reported as a doc file",
          "docs/page.mdx" not in (data.get("doc_files") or []), data)
    check("[CP1-X09] docs/page.mdx is reported as a code file",
          "docs/page.mdx" in (data.get("code_files") or []), data)


FIX_TESTS3 = [
    ("CP1-X09", x09_mdx_is_not_a_doc_by_default),
]

for _cid, _fn in FIX_TESTS3:
    CASE_STATUS.setdefault(_cid, True)
    try:
        _fn()
    except Exception as _e:  # a crash still counts as a result for this case, never a silent drop
        FAIL += 1
        CASE_STATUS[_cid] = False
        CASE_MESSAGES.setdefault(_cid, []).append(f"crashed: {_e}")
        print(f"  ✗ [{_cid}] CRASHED: {_e}")
        traceback.print_exc()


def _write_report_with_fix_tests3():
    """Same as `_write_report_with_fix_tests2` above, but over `[*TESTS, *FIX_TESTS, *FIX_TESTS2, *FIX_TESTS3]`.
    Rebinds the module-level `write_report` name again (looked up by `main()` only at call time, below)."""
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = [{"id": cid, "status": "passed" if CASE_STATUS.get(cid, False) else "failed",
              "message": "" if CASE_STATUS.get(cid, False) else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid, _ in [*TESTS, *FIX_TESTS, *FIX_TESTS2, *FIX_TESTS3]]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


write_report = _write_report_with_fix_tests3


if __name__ == "__main__":
    main()
