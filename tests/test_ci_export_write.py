#!/usr/bin/env python3
"""
CP2 tests: write the workflow file without clobbering.   python3 tests/test_ci_export_write.py

Drives `ratchet.py ci-export` (writing) and `ratchet.py ci-export --stdout` (printing) as real subprocesses
against throwaway temp git projects, the same `project`/`rx` subprocess pattern tests/test_ci_export.py (CP1)
uses. Each case compares the file ci-export writes at .github/workflows/ratchet.yml against what --stdout
prints for the same project state, and checks exit code, message text, byte content, mtime and git status.
Fixture helpers here are copied from tests/test_ci_export.py rather than imported, so the two files stay
independent (tests/test_ci_export.py's own comment makes the same choice for CP1).

There is no PyYAML in this stdlib-only project; every comparison is plain byte/string equality, never a YAML
parse.

Writes a per-test JSON report to $RATCHET_TEST_REPORT when that env var is set:
{"cases": [{"id": "CP2-T01", "status": "passed"|"failed"|"error"|"skipped", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402

ENGINE = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
ENV = {k: v for k, v in os.environ.items() if k not in ("RATCHET_ROLE", "RATCHET_HUMAN_OVERRIDE", "RATCHET_ROOT")}
ENV["RATCHET_HOME"] = str(_tmp.mkdtemp(prefix="ratchet-ci-write-home-"))
ENV["RATCHET_HOST_PROVIDER"] = "anthropic"

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
CASE_CHECKS: dict[str, int] = {}
CASE_SKIPPED: set = set()
ID_RE = re.compile(r"\[(CP2-[TXF]\d+[a-z]?)\]")


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_CHECKS[cid] = CASE_CHECKS.get(cid, 0) + 1
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ------------------------------------------------------------------------------------------------ fixtures

def git(d, *a):
    return subprocess.run(["git", *a], cwd=d, capture_output=True, text=True)


def rx(d, *args):
    return subprocess.run([sys.executable, str(ENGINE), *args], cwd=d, capture_output=True, text=True, env=ENV)


def project(name, config=None, files=None) -> Path:
    """A fresh throwaway git repo, committed, with a .ratchet/config.json written only through
    `ratchet.py config --set` (never hand-edited), per standard 2/24."""
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-ciw-{name}-"))
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(d, *c)
    (d / "src").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    for rel, text in (files or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    if config:
        r = rx(d, "config", "--set", *config)
        assert r.returncode == 0, r.stderr + r.stdout
    return d


def set_cfg(key, value) -> str:
    """One "key=<json>" entry for `config --set`."""
    return f"{key}={json.dumps(value)}"


CHECK1 = {"kind": "lint-no-console", "title": "No console.log left in", "command": "true # check-one",
          "added": "2026-01-01", "from": "x/CP1"}
CHECK2 = {"kind": "arch-no-import-cycle", "title": "No import cycles", "command": "true # check-two",
          "added": "2026-01-01", "from": "x/CP1"}

WORKFLOW_PATH = ".github/workflows/ratchet.yml"


def workflow_of(d: Path) -> Path:
    return d / ".github" / "workflows" / "ratchet.yml"


# ------------------------------------------------------------------------------------------------ cases

def t01_first_run_matches_stdout_and_creates_dirs():
    d = project("t01", config=[set_cfg("regression_command", "true # regression")])
    assert not (d / ".github").exists()
    r = rx(d, "ci-export")
    check("[CP2-T01] ci-export (no --stdout) exits 0 on a fresh project with a regression command",
          r.returncode == 0, r.stderr + r.stdout)
    wf = workflow_of(d)
    check("[CP2-T01] .github directory was created", (d / ".github").is_dir(), d)
    check("[CP2-T01] .github/workflows directory was created", (d / ".github" / "workflows").is_dir(), d)
    check("[CP2-T01] .github/workflows/ratchet.yml was created", wf.is_file(), wf)
    stdout_r = rx(d, "ci-export", "--stdout")
    check("[CP2-T01] --stdout still exits 0 for the same project state", stdout_r.returncode == 0,
          stdout_r.stderr + stdout_r.stdout)
    check("[CP2-T01] the written file's bytes equal --stdout's bytes for the same project, byte-for-byte",
          wf.exists() and wf.read_bytes() == stdout_r.stdout.encode(),
          (wf.read_bytes()[:200] if wf.exists() else None, stdout_r.stdout[:200]))


def t02_rerun_after_added_check_updates_in_place():
    d = project("t02", config=[set_cfg("regression_command", "true # regression")])
    r1 = rx(d, "ci-export")
    check("[CP2-T02] the first ci-export run exits 0", r1.returncode == 0, r1.stderr + r1.stdout)
    wf = workflow_of(d)
    before = wf.read_bytes() if wf.exists() else b""
    check("[CP2-T02] the first run actually wrote the workflow file", wf.exists(), wf)
    r_cfg = rx(d, "config", "--set", set_cfg("checks", [CHECK1]))
    assert r_cfg.returncode == 0, r_cfg.stderr + r_cfg.stdout
    r2 = rx(d, "ci-export")
    check("[CP2-T02] the second ci-export run (after a check is added) exits 0", r2.returncode == 0,
          r2.stderr + r2.stdout)
    after = wf.read_bytes() if wf.exists() else b""
    stdout_r = rx(d, "ci-export", "--stdout")
    check("[CP2-T02] the updated file now differs from the first run's bytes",
          after != before, (before[:200], after[:200]))
    check("[CP2-T02] the updated file includes a step for the newly added check",
          b"true # check-one" in after, after)
    check("[CP2-T02] the updated file's bytes equal --stdout's bytes for the updated project",
          after == stdout_r.stdout.encode(), (after[:200], stdout_r.stdout[:200]))
    wf_dir = d / ".github" / "workflows"
    siblings = list(wf_dir.iterdir()) if wf_dir.is_dir() else []
    check("[CP2-T02] no second file or backup is left in .github/workflows (exactly one entry)",
          len(siblings) == 1 and siblings[0].name == "ratchet.yml", [p.name for p in siblings])


def t03_hand_written_workflow_never_replaced():
    d = project("t03", config=[set_cfg("regression_command", "true # regression")])
    (d / ".github" / "workflows").mkdir(parents=True)
    wf = workflow_of(d)
    handwritten = "name: My Own CI\non:\n  push:\n" + "jobs:\n  build:\n    runs-on: ubuntu-latest\n    steps: []\n"
    wf.write_text(handwritten)
    before = wf.read_bytes()
    r = rx(d, "ci-export")
    check("[CP2-T03] ci-export exits non-zero when a hand-written workflow already exists at the path",
          r.returncode != 0, r.returncode)
    after = wf.read_bytes()
    check("[CP2-T03] the hand-written file's bytes are completely unchanged", after == before,
          (before, after))
    combined = (r.stdout + r.stderr).lower()
    check("[CP2-T03] the message plainly says it won't replace a hand-written file",
          "won't replace" in combined or "will not replace" in combined, r.stdout + r.stderr)
    check("[CP2-T03] the message suggests --stdout", "--stdout" in (r.stdout + r.stderr),
          r.stdout + r.stderr)


def t04_directory_at_path_refused():
    d = project("t04", config=[set_cfg("regression_command", "true # regression")])
    wf = workflow_of(d)
    wf.mkdir(parents=True)
    (wf / "inside.txt").write_text("do not touch\n")
    r = rx(d, "ci-export")
    check("[CP2-T04] a directory at the workflow path is refused, not overwritten",
          r.returncode != 0, r.returncode)
    check("[CP2-T04] the directory still exists as a directory", wf.is_dir(), wf)
    check("[CP2-T04] the directory's contents are unchanged",
          wf.is_dir() and (wf / "inside.txt").read_text() == "do not touch\n", wf)
    combined = r.stdout + r.stderr
    check("[CP2-T04] no Python traceback is printed", "Traceback" not in combined, combined)
    check("[CP2-T04] a plain, non-empty message is printed", combined.strip() != "", combined)


def t05_symlink_at_path_refused_not_followed():
    d = project("t05", config=[set_cfg("regression_command", "true # regression")])
    target = d / "outside-target.txt"
    target.write_text("original target contents\n")
    (d / ".github" / "workflows").mkdir(parents=True)
    wf = workflow_of(d)
    os.symlink(target, wf)
    r = rx(d, "ci-export")
    check("[CP2-T05] a symlink at the workflow path is refused, not followed",
          r.returncode != 0, r.returncode)
    check("[CP2-T05] the path is still a symlink", wf.is_symlink(), wf)
    check("[CP2-T05] the symlink still points at its original target",
          wf.is_symlink() and os.readlink(wf) == str(target), wf)
    check("[CP2-T05] the target file outside .github/ is completely unmodified",
          target.read_text() == "original target contents\n", target.read_text())
    combined = r.stdout + r.stderr
    check("[CP2-T05] no Python traceback is printed", "Traceback" not in combined, combined)


def t06_unchanged_rerun_is_byte_identical_and_says_up_to_date():
    d = project("t06", config=[set_cfg("regression_command", "true # regression"),
                                set_cfg("checks", [CHECK1])])
    r1 = rx(d, "ci-export")
    check("[CP2-T06] the first ci-export run exits 0", r1.returncode == 0, r1.stderr + r1.stdout)
    wf = workflow_of(d)
    check("[CP2-T06] the first run actually wrote the workflow file", wf.exists(), wf)
    before_bytes = wf.read_bytes() if wf.exists() else b""
    before_mtime = wf.stat().st_mtime_ns if wf.exists() else None
    time.sleep(1.1)  # clear filesystem mtime granularity so a naive always-rewrite would be caught
    r2 = rx(d, "ci-export")
    after_bytes = wf.read_bytes() if wf.exists() else b""
    after_mtime = wf.stat().st_mtime_ns if wf.exists() else None
    check("[CP2-T06] a rerun with nothing changed exits 0", r2.returncode == 0, r2.stderr + r2.stdout)
    check("[CP2-T06] the file's bytes are exactly unchanged", after_bytes == before_bytes,
          (before_bytes[:200], after_bytes[:200]))
    check("[CP2-T06] the file's modification time is exactly unchanged (no rewrite happened)",
          before_mtime is not None and after_mtime == before_mtime, (before_mtime, after_mtime))
    check("[CP2-T06] the message says the workflow is already up to date",
          "up to date" in (r2.stdout + r2.stderr).lower(), r2.stdout + r2.stderr)


def t07_nothing_to_run_writes_no_file():
    d = project("t07")  # no .ratchet config at all: no regression_command, no checks, no runs/
    r = rx(d, "ci-export")
    check("[CP2-T07] a project with nothing to run exits non-zero", r.returncode != 0, r.returncode)
    check("[CP2-T07] no .github/workflows/ratchet.yml is created", not workflow_of(d).exists(), d)
    check("[CP2-T07] no .github directory is created either", not (d / ".github").exists(), d)
    combined = r.stdout + r.stderr
    check("[CP2-T07] a plain, non-empty message is printed", combined.strip() != "", combined)
    check("[CP2-T07] no Python traceback is printed", "Traceback" not in combined, combined)


def t08_never_stages_or_commits():
    d = project("t08", config=[set_cfg("regression_command", "true # regression")])
    head_before = git(d, "rev-parse", "HEAD").stdout.strip()
    staged_before = git(d, "diff", "--cached", "--name-only").stdout
    status_before = git(d, "status", "--porcelain").stdout
    r = rx(d, "ci-export")
    check("[CP2-T08] ci-export exits 0 while creating the workflow for the first time",
          r.returncode == 0, r.stderr + r.stdout)
    check("[CP2-T08] the workflow file now exists", workflow_of(d).exists(), d)
    head_after = git(d, "rev-parse", "HEAD").stdout.strip()
    staged_after = git(d, "diff", "--cached", "--name-only").stdout
    status_after = git(d, "status", "--porcelain").stdout
    check("[CP2-T08] HEAD is exactly what it was before the run (no commit was made)",
          head_after == head_before, (head_before, head_after))
    check("[CP2-T08] the index has no staged changes before or after (no git add ran)",
          staged_before.strip() == "" and staged_after.strip() == "",
          (staged_before, staged_after))
    check("[CP2-T08] git status shows the new .github path as untracked ('??'), never staged ('A ')",
          "?? .github" in status_after and "A  .github" not in status_after
          and "A\t.github" not in status_after, status_after)


def t09_github_as_plain_file_refused_gracefully():
    d = project("t09", config=[set_cfg("regression_command", "true # regression")])
    (d / ".github").write_text("not a directory, just a file\n")
    before = (d / ".github").read_bytes()
    r = rx(d, "ci-export")
    check("[CP2-T09] ci-export exits non-zero when .github already exists as a plain file",
          r.returncode != 0, r.returncode)
    combined = r.stdout + r.stderr
    check("[CP2-T09] no Python traceback is printed", "Traceback" not in combined, combined)
    check("[CP2-T09] a plain, non-empty message is printed", combined.strip() != "", combined)
    after = (d / ".github").read_bytes()
    check("[CP2-T09] the .github file is left byte-for-byte unchanged", after == before, (before, after))
    check("[CP2-T09] .github is still a plain file, not a directory", (d / ".github").is_file(), d)


TESTS = [
    ("CP2-T01", t01_first_run_matches_stdout_and_creates_dirs),
    ("CP2-T02", t02_rerun_after_added_check_updates_in_place),
    ("CP2-T03", t03_hand_written_workflow_never_replaced),
    ("CP2-T04", t04_directory_at_path_refused),
    ("CP2-T05", t05_symlink_at_path_refused_not_followed),
    ("CP2-T06", t06_unchanged_rerun_is_byte_identical_and_says_up_to_date),
    ("CP2-T07", t07_nothing_to_run_writes_no_file),
    ("CP2-T08", t08_never_stages_or_commits),
    ("CP2-T09", t09_github_as_plain_file_refused_gracefully),
]


# ---- CP2 adversarial round 1 fixes: BOM/CRLF marker recognition, OSError handling, symlinked parents ------

def f01_bom_and_crlf_converted_workflow_still_recognized_and_updated():
    """[CP2-F01] a file Ratchet wrote, then converted to CRLF line endings and/or given a leading UTF-8 BOM
    (what git's core.autocrlf and some Windows editors do on checkout/save), is still recognized as Ratchet's
    own on the next ci-export, and is updated in place to the canonical (LF, no-BOM) text rather than being
    refused as a hand-written file."""
    d = project("f01", config=[set_cfg("regression_command", "true # regression")])
    r1 = rx(d, "ci-export")
    check("[CP2-F01] the first ci-export run exits 0", r1.returncode == 0, r1.stderr + r1.stdout)
    wf = workflow_of(d)
    original = wf.read_bytes()
    converted = b"\xef\xbb\xbf" + original.replace(b"\n", b"\r\n")  # BOM + CRLF, as autocrlf/PowerShell would leave it
    wf.write_bytes(converted)
    r_cfg = rx(d, "config", "--set", set_cfg("checks", [CHECK1]))
    assert r_cfg.returncode == 0, r_cfg.stderr + r_cfg.stdout
    r2 = rx(d, "ci-export")
    check("[CP2-F01] re-running ci-export on a BOM+CRLF-converted file Ratchet wrote exits 0",
          r2.returncode == 0, r2.stderr + r2.stdout)
    combined = (r2.stdout + r2.stderr).lower()
    check("[CP2-F01] the message never claims the file was not written by Ratchet",
          "was not written by ratchet" not in combined, r2.stdout + r2.stderr)
    stdout_r = rx(d, "ci-export", "--stdout")
    check("[CP2-F01] the file is updated to the canonical LF/no-BOM text, matching --stdout",
          wf.read_bytes() == stdout_r.stdout.encode(), (wf.read_bytes()[:200], stdout_r.stdout[:200]))


def f02_permission_denied_dies_cleanly_no_traceback():
    """[CP2-F02] when the write into .github/workflows fails with an OSError (e.g. a read-only directory),
    ci-export dies with a plain, actionable message naming the path, prints no Python traceback, and leaves
    nothing half-written."""
    d = project("f02", config=[set_cfg("regression_command", "true # regression")])
    workflows_dir = d / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    wf = workflow_of(d)
    mode_before = workflows_dir.stat().st_mode
    os.chmod(workflows_dir, 0o555)
    probe = workflows_dir / "probe.tmp"
    try:
        probe.touch()
        enforced = False  # permission bits not enforced for this user (e.g. root): nothing meaningful to test
        probe.unlink()
    except OSError:
        enforced = True
    try:
        if not enforced:
            print("  - [CP2-F02] not run: this OS or user ignores directory write permissions")
            return
        r = rx(d, "ci-export")
        check("[CP2-F02] ci-export exits non-zero when it cannot write into a read-only .github/workflows",
              r.returncode != 0, r.returncode)
        combined = r.stdout + r.stderr
        check("[CP2-F02] no Python traceback is printed", "Traceback" not in combined, combined)
        check("[CP2-F02] the message names the workflow path",
              str(wf) in combined or "ratchet.yml" in combined, combined)
        check("[CP2-F02] the message is a plain, non-empty actionable error", combined.strip() != "", combined)
        check("[CP2-F02] no workflow file was created", not wf.exists(), wf)
        os.chmod(workflows_dir, mode_before)
        leftovers = [p.name for p in workflows_dir.iterdir()]
        check("[CP2-F02] no half-written temp file is left behind in the directory", leftovers == [], leftovers)
    finally:
        os.chmod(workflows_dir, mode_before)


def f03_symlinked_github_parent_refused_not_followed():
    """[CP2-F03] a symlink at .github or at .github/workflows itself (not just at the ratchet.yml leaf) is
    refused with the same won't-replace message, and nothing is written through it to a target outside the
    project."""
    d = project("f03", config=[set_cfg("regression_command", "true # regression")])
    outside = Path(_tmp.mkdtemp(prefix="ratchet-ciw-f03-outside-"))
    os.symlink(outside, d / ".github")
    r = rx(d, "ci-export")
    check("[CP2-F03] ci-export exits non-zero when .github itself is a symlink",
          r.returncode != 0, r.returncode)
    check("[CP2-F03] .github is still a symlink, not replaced", (d / ".github").is_symlink(), d)
    check("[CP2-F03] nothing was written into the symlink's target outside the project",
          list(outside.iterdir()) == [], list(outside.iterdir()))
    combined = r.stdout + r.stderr
    check("[CP2-F03] no Python traceback is printed", "Traceback" not in combined, combined)
    check("[CP2-F03] the message plainly says it won't replace, with the --stdout suggestion",
          ("won't replace" in combined or "will not replace" in combined) and "--stdout" in combined, combined)

    d2 = project("f03b", config=[set_cfg("regression_command", "true # regression")])
    (d2 / ".github").mkdir()
    outside2 = Path(_tmp.mkdtemp(prefix="ratchet-ciw-f03b-outside-"))
    os.symlink(outside2, d2 / ".github" / "workflows")
    r2 = rx(d2, "ci-export")
    check("[CP2-F03] ci-export exits non-zero when .github/workflows itself is a symlink",
          r2.returncode != 0, r2.returncode)
    check("[CP2-F03] .github/workflows is still a symlink, not replaced",
          (d2 / ".github" / "workflows").is_symlink(), d2)
    check("[CP2-F03] nothing was written into that symlink's target outside the project either",
          list(outside2.iterdir()) == [], list(outside2.iterdir()))


TESTS.extend([
    ("CP2-F01", f01_bom_and_crlf_converted_workflow_still_recognized_and_updated),
    ("CP2-F02", f02_permission_denied_dies_cleanly_no_traceback),
    ("CP2-F03", f03_symlinked_github_parent_refused_not_followed),
])


# ---- CP2 adversarial round 2 fix: lone surrogate in a check command must not break the up-to-date check ---

def f04_lone_surrogate_in_check_command_rerun_is_up_to_date():
    """[CP2-F04] a check whose command contains a lone surrogate (as a reviewer's reply might produce) is
    rendered and written on the first ci-export run, and a second run with nothing changed still compares
    equal and reports up to date, rather than crashing on a strict UTF-8 encode."""
    surrogate_check = {"kind": "lint-surrogate", "title": "Check with a lone surrogate in its command",
                        "command": "true # \ud83d surrogate-check", "added": "2026-01-01", "from": "x/CP2"}
    d = project("f04", config=[set_cfg("regression_command", "true # regression"),
                                set_cfg("checks", [surrogate_check])])
    r1 = rx(d, "ci-export")
    check("[CP2-F04] the first ci-export run (lone surrogate in a check command) exits 0",
          r1.returncode == 0, r1.stderr + r1.stdout)
    combined1 = r1.stdout + r1.stderr
    check("[CP2-F04] no Python traceback is printed on the first run", "Traceback" not in combined1, combined1)
    wf = workflow_of(d)
    check("[CP2-F04] the first run actually wrote the workflow file", wf.exists(), wf)
    r2 = rx(d, "ci-export")
    check("[CP2-F04] the second ci-export run (nothing changed) exits 0", r2.returncode == 0,
          r2.stderr + r2.stdout)
    combined2 = r2.stdout + r2.stderr
    check("[CP2-F04] no Python traceback is printed on the second run", "Traceback" not in combined2, combined2)
    check("[CP2-F04] the second run says the workflow is already up to date",
          "up to date" in combined2.lower(), combined2)


TESTS.extend([
    ("CP2-F04", f04_lone_surrogate_in_check_command_rerun_is_up_to_date),
])


# ---- CP2 adversarial round 3 fix: --stdout bytes must equal the written file's bytes, even for non-ASCII or a
# lone surrogate in a check command ----------------------------------------------------------------------------

def _rx_bytes(d, *args):
    """Like rx(), but captures stdout as raw bytes (no text decoding), so a comparison against the written
    file's bytes is byte-for-byte, never affected by the subprocess's own text decoding of its stdout."""
    return subprocess.run([sys.executable, str(ENGINE), *args], cwd=d, capture_output=True, env=ENV)


def f05_stdout_bytes_equal_written_bytes_for_non_ascii_and_surrogate_commands():
    """[CP2-F05] --stdout prints exactly the bytes ci-export writes to .github/workflows/ratchet.yml, for a
    plain command, a non-ASCII command, and a check command holding a lone surrogate, byte-for-byte."""
    cases = [
        ("f05-plain", "true # plain command", None),
        ("f05-nonascii", "echo café ✓", None),
        ("f05-surrogate", "true # regression", {"kind": "lint-surrogate-f05",
                                                  "title": "Check with a lone surrogate",
                                                  "command": "true # \ud83d check", "added": "2026-01-01",
                                                  "from": "x/CP2"}),
    ]
    for name, regression_command, surrogate_check in cases:
        cfg = [set_cfg("regression_command", regression_command)]
        if surrogate_check:
            cfg.append(set_cfg("checks", [surrogate_check]))
        d = project(name, config=cfg)
        r = rx(d, "ci-export")
        check(f"[CP2-F05] ci-export exits 0 for {name}", r.returncode == 0, r.stderr + r.stdout)
        wf = workflow_of(d)
        written = wf.read_bytes() if wf.exists() else b""
        stdout_r = _rx_bytes(d, "ci-export", "--stdout")
        check(f"[CP2-F05] --stdout exits 0 for {name}", stdout_r.returncode == 0, stdout_r.stderr)
        check(f"[CP2-F05] --stdout bytes equal the written file's bytes for {name}, byte-for-byte",
              written == stdout_r.stdout, (written[:200], stdout_r.stdout[:200]))


TESTS.extend([
    ("CP2-F05", f05_stdout_bytes_equal_written_bytes_for_non_ascii_and_surrogate_commands),
])


def write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return

    def status(cid):
        if cid in CASE_SKIPPED:
            return "skipped"
        return "passed" if CASE_STATUS.get(cid, False) else "failed"

    cases = [{"id": cid, "status": status(cid),
              "message": "not run: it made no checks" if status(cid) == "skipped"
              else "" if status(cid) == "passed" else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid, _ in TESTS]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crash in one case still counts as a result for that case, not a crash of the file
            global FAIL
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
        else:
            if not CASE_CHECKS.get(cid):
                CASE_SKIPPED.add(cid)
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
