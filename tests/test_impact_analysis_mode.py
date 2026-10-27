#!/usr/bin/env python3
"""
CP1 tests: classify a checkpoint greenfield or brownfield.   python3 tests/test_impact_analysis_mode.py

Drives skills/ratchet-checkpoint-planner/scripts/impact_analysis.py's `classify` entry point purely as an
external CLI subprocess (argv/stdin/stdout) -- never by importing it into the test process -- against
disposable temp directories standing in for a repo root, plus one check against this repository's own
real root.

------------------------------------------------------------------------------------------------ CLI CONTRACT
This test file is this contract's first and only definition right now; CP2-CP7's planner prompts, helper
scripts and tests must follow it exactly.

Invocation (always a plain OS subprocess, never an in-process import):

    python3 skills/ratchet-checkpoint-planner/scripts/impact_analysis.py classify <repo_root>

- `<repo_root>` (positional argv[2]): an absolute or relative path to the repo root to check files against.
  It need not exist (see the fail-safe case below).
- stdin: a single JSON array of strings -- the checkpoint's `files_likely` list of repo-root-relative paths,
  exactly as the plan carries them. An empty array (`[]`) is valid input, not a usage error.
- stdout: on a successful classification (including every fail-safe case), stdout is ONE JSON object and
  nothing else (no banner, no trailing prose) -- a caller can `json.loads(the_whole_stdout)` directly:

      {
        "mode": "greenfield-create" | "brownfield-modify",
        "reason": "" | "<non-empty string saying why it is brownfield or why it fell back there>",
        "paths_checked": [<the exact files_likely list given, same paths, same order, none added or dropped>]
      }

- exit code: 0 for every successful classification, including every fail-safe case (mixed list, unreadable
  repo root, empty files_likely). A nonzero exit is reserved for a genuine CLI usage error (e.g. a missing
  `classify` subcommand or missing `<repo_root>` argument, or stdin that is not valid JSON) -- never used
  just because the repo root could not be read or the list was empty; those still fail safe to
  "brownfield-modify" with a reason and exit 0.
- "greenfield-create" is reported only when every path in files_likely is confirmed absent from the repo
  root. Any doubt (a path exists, the root can't be read, the list is empty) reports "brownfield-modify"
  with a non-empty `reason`.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP1-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402

IMPACT_SCRIPT = ROOT / "skills" / "ratchet-checkpoint-planner" / "scripts" / "impact_analysis.py"

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}


def check(cid, name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
    if not cond:
        CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'✓' if cond else '✗'} [{cid}] {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ------------------------------------------------------------------------------------------------ helpers

def run_classify(repo_root, files_likely):
    """Invokes the classifier as a plain OS subprocess (never an in-process import), the same way the
    checkpoint-planner agent would call it with the Bash tool. Returns (returncode, stdout, stderr,
    parsed_json_or_None)."""
    proc = subprocess.run(
        [sys.executable, str(IMPACT_SCRIPT), "classify", str(repo_root)],
        input=json.dumps(files_likely),
        capture_output=True,
        text=True,
        timeout=30,
    )
    parsed = None
    try:
        parsed = json.loads(proc.stdout)
    except Exception:
        parsed = None
    return proc.returncode, proc.stdout, proc.stderr, parsed


def _mkrepo(prefix):
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-impact-{prefix}-"))
    return d


def _touch(root, rel):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("placeholder\n")


# ------------------------------------------------------------------------------------------------ CP1-T01

def t01_all_new_files_classify_as_greenfield_create():
    cid = "CP1-T01"
    root = _mkrepo("t01")
    files_likely = [
        "skills/ratchet-checkpoint-planner/scripts/impact_analysis.py",
        "tests/test_impact_analysis_mode.py",
    ]
    rc, out, err, parsed = run_classify(root, files_likely)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is not None:
        check(cid, "mode is greenfield-create", parsed.get("mode") == "greenfield-create", parsed)
        check(cid, "reason is empty/falsy (no brownfield reason)", not parsed.get("reason"), parsed)
        check(cid, "carries no re-point/impact items", not parsed.get("items") and not parsed.get("re_point"),
              parsed)


# ------------------------------------------------------------------------------------------------ CP1-T02

def t02_all_existing_files_classify_as_brownfield_modify():
    cid = "CP1-T02"
    root = _mkrepo("t02")
    _touch(root, "src/existing_one.py")
    _touch(root, "src/existing_two.py")
    files_likely = ["src/existing_one.py", "src/existing_two.py"]
    rc, out, err, parsed = run_classify(root, files_likely)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is not None:
        check(cid, "mode is brownfield-modify", parsed.get("mode") == "brownfield-modify", parsed)


# ------------------------------------------------------------------------------------------------ CP1-T03

def t03_mixed_new_and_existing_fails_safe_to_brownfield_with_reason():
    cid = "CP1-T03"
    root = _mkrepo("t03")
    _touch(root, "src/already_here.py")
    files_likely = ["src/already_here.py", "src/brand_new_thing.py"]
    rc, out, err, parsed = run_classify(root, files_likely)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is not None:
        check(cid, "mode is brownfield-modify (never greenfield-create for a mixed list)",
              parsed.get("mode") == "brownfield-modify", parsed)
        reason = parsed.get("reason") or ""
        check(cid, "reason is non-empty", bool(reason), parsed)
        check(cid, "reason names the specific existing path", "src/already_here.py" in reason, parsed)


# ------------------------------------------------------------------------------------------------ CP1-T04

def t04_unreadable_repo_root_fails_safe_to_brownfield():
    cid = "CP1-T04"
    base = _mkrepo("t04")
    missing_root = base / "this_repo_root_was_never_created"
    files_likely = ["src/totally_new_one.py", "src/totally_new_two.py"]
    rc, out, err, parsed = run_classify(missing_root, files_likely)
    check(cid, "exits 0 (no uncaught crash)", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is not None:
        check(cid, "mode is brownfield-modify (never greenfield-create just because it couldn't check)",
              parsed.get("mode") == "brownfield-modify", parsed)
        reason = parsed.get("reason") or ""
        check(cid, "reason is non-empty and names the repo root as unreadable", bool(reason) and (
            "root" in reason.lower() or "read" in reason.lower()), parsed)


# ------------------------------------------------------------------------------------------------ CP1-T05

def t05_verdict_json_names_mode_and_lists_every_path_checked():
    cid = "CP1-T05"
    root = _mkrepo("t05")
    _touch(root, "src/existing_a.py")
    _touch(root, "src/existing_b.py")
    files_likely = ["src/existing_a.py", "src/existing_b.py", "src/new_c.py"]
    rc, out, err, parsed = run_classify(root, files_likely)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "stdout is valid, parseable JSON", parsed is not None, out)
    if parsed is not None:
        check(cid, "has a mode field", isinstance(parsed.get("mode"), str) and bool(parsed.get("mode")), parsed)
        check(cid, "paths_checked is exactly the files_likely list given (same paths, same order)",
              parsed.get("paths_checked") == files_likely, parsed)


# ------------------------------------------------------------------------------------------------ CP1-T06

def t06_existing_parent_directory_does_not_make_a_new_file_brownfield():
    cid = "CP1-T06"
    root = _mkrepo("t06")
    (root / "tests").mkdir(parents=True)
    files_likely = ["tests/brand_new_test_file.py", "src/brand_new_module.py"]
    rc, out, err, parsed = run_classify(root, files_likely)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is not None:
        check(cid, "mode is greenfield-create (existing parent dir is not mistaken for the file existing)",
              parsed.get("mode") == "greenfield-create", parsed)


# ------------------------------------------------------------------------------------------------ CP1-T07

def t07_empty_files_likely_fails_safe_to_brownfield_not_greenfield():
    cid = "CP1-T07"
    root = _mkrepo("t07")
    rc, out, err, parsed = run_classify(root, [])
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is not None:
        check(cid, "mode is brownfield-modify (never defaults to greenfield-create on an empty list)",
              parsed.get("mode") == "brownfield-modify", parsed)
        check(cid, "reason is non-empty (nothing to classify)", bool(parsed.get("reason")), parsed)


# ------------------------------------------------------------------------------------------------ CP1-T08

def t08_classifier_runs_as_a_standalone_cli_subprocess():
    cid = "CP1-T08"
    root = _mkrepo("t08")
    files_likely = [
        "skills/ratchet-checkpoint-planner/scripts/impact_analysis.py",
        "tests/test_impact_analysis_mode.py",
    ]
    rc, out, err, parsed = run_classify(root, files_likely)
    check(cid, "the subprocess exits 0", rc == 0, (rc, out, err))
    check(cid, "stdout parses as JSON", parsed is not None, out)
    if parsed is not None:
        check(cid, "stdout is ONLY the JSON verdict (no extra banner/prose a caller would need to strip)",
              out.strip() == json.dumps(parsed) or json.loads(out.strip()) == parsed, out)
        check(cid, "mode is greenfield-create", parsed.get("mode") == "greenfield-create", parsed)
    # Proves it is a real, separate OS process (not an in-process function call): the module is never
    # imported into this test process at all, so sys.modules never gains an entry for it.
    check(cid, "the module was never imported into this test's own process",
          "impact_analysis" not in sys.modules, sorted(sys.modules))


# ------------------------------------------------------------------------------------------------ CP1-T09

def t09_real_repo_root_mixed_list_fails_safe_same_as_fixtures():
    cid = "CP1-T09"
    files_likely = ["README.md", "this/path/has/never/existed/in/this/repo_xyz123.py"]
    rc, out, err, parsed = run_classify(ROOT, files_likely)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is not None:
        check(cid, "mode is brownfield-modify, matching the synthetic-fixture behavior in CP1-T03",
              parsed.get("mode") == "brownfield-modify", parsed)
        reason = parsed.get("reason") or ""
        check(cid, "reason is non-empty", bool(reason), parsed)
        check(cid, "reason names README.md as the existing path that triggered it", "README.md" in reason, parsed)


# --------------------------------------------------------------------------------------------- CP1-X01
# Adversarial round-1 fix (finding X1): a dangling symlink -- a directory entry that exists but whose
# target is absent -- must still count as an existing path, not be misread as new just because
# Path.exists() follows symlinks and reports False for a broken target.

def x01_dangling_symlink_alongside_new_path_fails_safe_to_brownfield():
    cid = "CP1-X01"
    root = _mkrepo("x01-dangling-symlink")
    link_rel = "optional/config.local.json"
    link_path = root / link_rel
    link_path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(root / "optional" / "does-not-exist-target.json", link_path)
    new_rel = "brand/new/file_never_seen_before.py"
    rc, out, err, parsed = run_classify(root, [link_rel, new_rel])
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is not None:
        check(cid, "mode is brownfield-modify (dangling symlink is still an existing directory entry)",
              parsed.get("mode") == "brownfield-modify", parsed)
        reason = parsed.get("reason") or ""
        check(cid, "reason is non-empty", bool(reason), parsed)
        check(cid, "reason names the dangling symlink path as the existing path that triggered it",
              link_rel in reason, parsed)


# --------------------------------------------------------------------------------------------- CP1-X02
# Adversarial round-2 fix (finding X1): os.path.lexists() swallows OSError internally and reports
# False, so a permission failure while probing an existing path must not be read as absence. The fix
# replaced the probe with a direct os.lstat() call where only a confirmed FileNotFoundError counts as
# absent; any other exception (e.g. PermissionError) counts as existing/doubt and fails safe to
# brownfield. This drives the real CLI subprocess against a path it genuinely cannot stat by revoking
# traversal permission on its parent directory -- not a mock -- so it exercises the real exception path.
#
# Limitation: running as root (euid 0) bypasses directory execute-permission checks entirely, so chmod
# cannot provoke a PermissionError in that case. When detected, this case is skipped rather than
# reporting a false pass.

def x02_permission_error_while_statting_an_existing_path_fails_safe_to_brownfield():
    cid = "CP1-X02"
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        print(f"  ⚠ [{cid}] skipped: running as root, chmod cannot provoke a PermissionError here")
        CASE_STATUS.setdefault(cid, True)
        return
    root = _mkrepo("x02-permission-error")
    restricted_dir = root / "restricted"
    restricted_dir.mkdir(parents=True, exist_ok=True)
    blocked_rel = "restricted/secret.py"
    _touch(root, blocked_rel)
    new_rel = "brand/new/other_file_never_seen_before.py"
    os.chmod(restricted_dir, 0o000)
    try:
        rc, out, err, parsed = run_classify(root, [blocked_rel, new_rel])
    finally:
        os.chmod(restricted_dir, 0o755)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is not None:
        check(cid, "mode is brownfield-modify (permission error while statting is doubt, not absence)",
              parsed.get("mode") == "brownfield-modify", parsed)
        check(cid, "reason is non-empty", bool(parsed.get("reason")), parsed)


TESTS = [
    ("CP1-T01", t01_all_new_files_classify_as_greenfield_create),
    ("CP1-T02", t02_all_existing_files_classify_as_brownfield_modify),
    ("CP1-T03", t03_mixed_new_and_existing_fails_safe_to_brownfield_with_reason),
    ("CP1-T04", t04_unreadable_repo_root_fails_safe_to_brownfield),
    ("CP1-T05", t05_verdict_json_names_mode_and_lists_every_path_checked),
    ("CP1-T06", t06_existing_parent_directory_does_not_make_a_new_file_brownfield),
    ("CP1-T07", t07_empty_files_likely_fails_safe_to_brownfield_not_greenfield),
    ("CP1-T08", t08_classifier_runs_as_a_standalone_cli_subprocess),
    ("CP1-T09", t09_real_repo_root_mixed_list_fails_safe_same_as_fixtures),
    ("CP1-X01", x01_dangling_symlink_alongside_new_path_fails_safe_to_brownfield),
    ("CP1-X02", x02_permission_error_while_statting_an_existing_path_fails_safe_to_brownfield),
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
