#!/usr/bin/env python3
"""
CP3 tests: flag new test files needing registration.   python3 tests/test_impact_analysis_registration.py

Drives skills/ratchet-checkpoint-planner/scripts/impact_analysis.py's `registration` entry point
purely as an external CLI subprocess (argv only -- never by importing it into the test process)
against disposable temp directories plus the static fixtures under
tests/fixtures/impact_analysis_registration/. Never touches the real README.md or the real tests/
directory (per the run's notes, to stay off lane M's files and off a moving target): every input is
a fixture copy built by this file.

------------------------------------------------------------------------------------------------ CLI CONTRACT
This test file is this contract's first and only definition right now (CP3 does not exist as code
yet); later checkpoints' planner prompts, helper scripts and tests must follow it exactly.

Invocation (always a plain OS subprocess, never an in-process import):

    python3 skills/ratchet-checkpoint-planner/scripts/impact_analysis.py registration \
        <repo_root> <new_test_file_relpath> --readme-list <fixture_readme_file> --inventory <fixture_inventory_file>

- `<repo_root>` (positional): an absolute path to a directory under which `<new_test_file_relpath>`
  is resolved (`repo_root / new_test_file_relpath`), used only to read the new test file's own
  source text to decide whether it AST/source-scans another project file. It need not be the real
  repo root; a disposable fixture tree is used throughout this file.
- `<new_test_file_relpath>` (positional): a forward-slash, repo-root-relative path to the new test
  file (e.g. `tests/test_widget_thing.py`), exactly the form that would appear in the README's
  "Running the tests" list after a `python3 ` prefix, and the exact string echoed back in the
  `test_file` field and used to build the README line (never re-derived from an absolute path, and
  never normalized to a different form).
- `--readme-list <fixture_readme_file>`: path to a plain text fixture file holding zero or more
  lines, one exact "Running the tests" entry per line (e.g. `python3 tests/test_foo.py`). A line
  counts as "already registered" only on an EXACT match (after stripping surrounding whitespace)
  against `python3 <new_test_file_relpath>` -- a line merely containing the filename as a substring,
  missing the `python3 ` prefix, or using a different runner (`pytest ...`) never counts (CP3-T06).
- `--inventory <fixture_inventory_file>`: path to a plain text fixture file holding zero or more
  lines, one source-scanning suite's basename per line (e.g. `test_scan_thing.py`). A line counts as
  "already registered" only on an exact match against `<new_test_file_relpath>`'s basename.
- No stdin is read by this subcommand.
- stdout: on a successful run (including an empty items list), stdout is ONE JSON object and nothing
  else (no banner, no trailing prose) -- a caller can `json.loads(the_whole_stdout)` directly:

      {
        "test_file": "<new_test_file_relpath, exactly as given>",
        "items": [
          {"kind": "readme", "line": "python3 tests/test_widget_thing.py"},
          {"kind": "inventory", "line": "test_scan_thing.py"},
          {"kind": "manual-check-needed", "detail": "<short human-readable text>"},
          ...
        ]
      }

  `items[*].line` is present for `"readme"`/`"inventory"` items (the exact text to add) and absent
  (or null) for a `"manual-check-needed"` item, which carries `detail` instead -- emitted when the
  new test file could not be parsed (e.g. a syntax error), so whether it needs inventory
  registration cannot be decided; a parse failure NEVER silently falls back to "it doesn't scan
  source" and never silently drops the README item (CP3-T07).
- The check is read-only and idempotent: it never writes to `--readme-list`/`--inventory`, and
  running it twice in a row against the same unchanged inputs produces byte-identical `items`
  (CP3-T08).
- exit code: 0 for every successful run, including every file that could not be parsed. A nonzero
  exit is reserved for a genuine CLI usage error (missing `registration` subcommand, missing
  positional argument, or a missing `--readme-list`/`--inventory` flag).

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id":
"CP3-T01", "status": "passed"|"failed", "message": "..."}]}.
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
FIXTURES = HERE / "fixtures" / "impact_analysis_registration"

PLAIN_FIXTURE_TEXT = (FIXTURES / "plain_fixture.py").read_text()
SCANNING_FIXTURE_TEXT = (FIXTURES / "cross_file_consistency_fixture.py").read_text()
BROKEN_FIXTURE_TEXT = (FIXTURES / "broken_syntax_fixture.py").read_text()
README_BASE_TEXT = (FIXTURES / "readme_list_base.txt").read_text()
INVENTORY_BASE_TEXT = (FIXTURES / "inventory_base.txt").read_text()

# round-1 fix for codex-both/X1 (not planned cases; see CP3-X1-01/CP3-X1-02 below)
TEXT_BASED_SCAN_FIXTURE_TEXT = (FIXTURES / "text_based_scan_fixture.py").read_text()
AMBIGUOUS_READ_FIXTURE_TEXT = (FIXTURES / "ambiguous_read_fixture.py").read_text()

# round-2 fix for X1/X2 (not planned cases; see CP3-X1-03/CP3-X2-01/CP3-X2-02 below)
AST_PARSE_READ_BYTES_FIXTURE_TEXT = (FIXTURES / "ast_parse_read_bytes_fixture.py").read_text()
JSON_REPORT_CHECK_FIXTURE_TEXT = (FIXTURES / "json_report_check_fixture.py").read_text()
UNRESOLVED_EXTENSION_CHECK_FIXTURE_TEXT = (
    FIXTURES / "unresolved_extension_check_fixture.py").read_text()

# round-3 fix for codex-both/X1 (not a planned case; see CP3-X3-01 below)
GLOB_LOOP_UNRESOLVED_FIXTURE_TEXT = (FIXTURES / "glob_loop_unresolved_fixture.py").read_text()

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

def run_registration(repo_root, new_test_file_relpath, readme_list_path, inventory_path):
    """Invokes the registration check as a plain OS subprocess (never an in-process import), the
    same way the checkpoint-planner agent would call it with the Bash tool. Returns (returncode,
    stdout, stderr, parsed_json_or_None)."""
    proc = subprocess.run(
        [sys.executable, str(IMPACT_SCRIPT), "registration", str(repo_root), new_test_file_relpath,
         "--readme-list", str(readme_list_path), "--inventory", str(inventory_path)],
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
    return Path(_tmp.mkdtemp(prefix=f"ratchet-impact-registration-{prefix}-"))


def _write_test_file(repo_root, relpath, body_text):
    p = repo_root / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body_text)
    return relpath


def _fixture_file(tmp_dir, name, text):
    p = tmp_dir / name
    p.write_text(text)
    return p


def _readme_fixture(tmp_dir, extra_lines=()):
    """A disposable copy of the base README-list fixture, optionally with extra exact lines
    appended -- never the real README.md."""
    text = README_BASE_TEXT
    for line in extra_lines:
        text += line.rstrip("\n") + "\n"
    return _fixture_file(tmp_dir, "readme_list.txt", text)


def _inventory_fixture(tmp_dir, extra_lines=()):
    """A disposable copy of the base inventory fixture, optionally with extra exact lines appended
    -- never a real project inventory file."""
    text = INVENTORY_BASE_TEXT
    for line in extra_lines:
        text += line.rstrip("\n") + "\n"
    return _fixture_file(tmp_dir, "inventory.txt", text)


def items_of_kind(parsed, kind):
    return [it for it in (parsed or {}).get("items", []) if it.get("kind") == kind]


# ------------------------------------------------------------------------------------------------ CP3-T01

def t01_plain_unregistered_file_gets_only_a_readme_item():
    cid = "CP3-T01"
    root = _mkrepo("t01")
    relpath = "tests/test_widget_thing.py"
    _write_test_file(root, relpath, PLAIN_FIXTURE_TEXT)
    readme = _readme_fixture(root)
    inventory = _inventory_fixture(root)
    rc, out, err, parsed = run_registration(root, relpath, readme, inventory)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is not None:
        items = parsed.get("items", [])
        check(cid, "emits exactly one item", len(items) == 1, items)
        check(cid, "the one item is the exact README line to add",
              items == [{"kind": "readme", "line": "python3 tests/test_widget_thing.py"}], items)
        check(cid, "emits no inventory item", items_of_kind(parsed, "inventory") == [], items)


# ------------------------------------------------------------------------------------------------ CP3-T02

def t02_unregistered_source_scanning_file_gets_readme_and_inventory_items():
    cid = "CP3-T02"
    root = _mkrepo("t02")
    relpath = "tests/test_scan_thing.py"
    _write_test_file(root, relpath, SCANNING_FIXTURE_TEXT)
    readme = _readme_fixture(root)
    inventory = _inventory_fixture(root)
    rc, out, err, parsed = run_registration(root, relpath, readme, inventory)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is not None:
        items = parsed.get("items", [])
        check(cid, "emits exactly two items", len(items) == 2, items)
        check(cid, "emits the exact README line to add",
              {"kind": "readme", "line": "python3 tests/test_scan_thing.py"} in items, items)
        check(cid, "emits an inventory-registration item naming the file",
              any(it.get("kind") == "inventory" and "test_scan_thing.py" in (it.get("line") or "")
                  for it in items), items)


# ------------------------------------------------------------------------------------------------ CP3-T03

def t03_fully_registered_file_produces_no_items():
    cid = "CP3-T03"
    root = _mkrepo("t03")
    relpath = "tests/test_known_thing.py"
    _write_test_file(root, relpath, SCANNING_FIXTURE_TEXT)
    readme = _readme_fixture(root, extra_lines=["python3 tests/test_known_thing.py"])
    inventory = _inventory_fixture(root, extra_lines=["test_known_thing.py"])
    rc, out, err, parsed = run_registration(root, relpath, readme, inventory)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is not None:
        check(cid, "emits no items at all (no duplicate, no false positive)",
              parsed.get("items", []) == [], parsed.get("items"))


# ------------------------------------------------------------------------------------------------ CP3-T04

def t04_registered_in_readme_but_missing_from_inventory_gets_only_inventory_item():
    cid = "CP3-T04"
    root = _mkrepo("t04")
    relpath = "tests/test_half_registered.py"
    _write_test_file(root, relpath, SCANNING_FIXTURE_TEXT)
    readme = _readme_fixture(root, extra_lines=["python3 tests/test_half_registered.py"])
    inventory = _inventory_fixture(root)
    rc, out, err, parsed = run_registration(root, relpath, readme, inventory)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is not None:
        items = parsed.get("items", [])
        check(cid, "emits exactly one item", len(items) == 1, items)
        check(cid, "the one item is an inventory item (not a duplicate README item)",
              len(items) == 1 and items[0].get("kind") == "inventory", items)
        check(cid, "the inventory item names the file",
              len(items) == 1 and "test_half_registered.py" in (items[0].get("line") or ""), items)


# ------------------------------------------------------------------------------------------------ CP3-T05

def t05_registered_in_inventory_but_missing_from_readme_gets_only_readme_item():
    cid = "CP3-T05"
    root = _mkrepo("t05")
    relpath = "tests/test_other_half.py"
    _write_test_file(root, relpath, SCANNING_FIXTURE_TEXT)
    readme = _readme_fixture(root)
    inventory = _inventory_fixture(root, extra_lines=["test_other_half.py"])
    rc, out, err, parsed = run_registration(root, relpath, readme, inventory)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is not None:
        items = parsed.get("items", [])
        check(cid, "emits exactly one item",
              items == [{"kind": "readme", "line": "python3 tests/test_other_half.py"}], items)
        check(cid, "emits no inventory item", items_of_kind(parsed, "inventory") == [], items)


# ------------------------------------------------------------------------------------------------ CP3-T06

def t06_near_miss_readme_line_is_never_mistaken_for_registration():
    cid = "CP3-T06"
    root = _mkrepo("t06")
    relpath = "tests/test_mismatch.py"
    _write_test_file(root, relpath, PLAIN_FIXTURE_TEXT)
    readme = _readme_fixture(root, extra_lines=[
        "tests/test_mismatch.py",          # missing the "python3 " prefix
        "pytest tests/test_mismatch.py",   # wrong runner
    ])
    inventory = _inventory_fixture(root)
    rc, out, err, parsed = run_registration(root, relpath, readme, inventory)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is not None:
        items = parsed.get("items", [])
        check(cid, "a filename substring match never counts as already registered: the exact "
                   "README item is still emitted",
              items == [{"kind": "readme", "line": "python3 tests/test_mismatch.py"}], items)


# ------------------------------------------------------------------------------------------------ CP3-T07

def t07_unparseable_file_still_gets_readme_item_plus_manual_check_needed():
    cid = "CP3-T07"
    root = _mkrepo("t07")
    relpath = "tests/test_broken_syntax.py"
    _write_test_file(root, relpath, BROKEN_FIXTURE_TEXT)
    readme = _readme_fixture(root)
    inventory = _inventory_fixture(root)
    rc, out, err, parsed = run_registration(root, relpath, readme, inventory)
    check(cid, "exits 0 (a syntax error in the scanned file is not a CLI usage error)", rc == 0,
          (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is not None:
        items = parsed.get("items", [])
        check(cid, "still emits the README item (the filename is known regardless of parsing)",
              {"kind": "readme", "line": "python3 tests/test_broken_syntax.py"} in items, items)
        manual = items_of_kind(parsed, "manual-check-needed")
        check(cid, "emits exactly one manual-check-needed item instead of silently deciding it "
                   "does not scan source",
              len(manual) == 1 and bool((manual[0].get("detail") or "").strip()), items)
        check(cid, "never also emits a false-positive/false-negative inventory item",
              items_of_kind(parsed, "inventory") == [], items)
        check(cid, "emits exactly two items total", len(items) == 2, items)


# ------------------------------------------------------------------------------------------------ CP3-T08

def t08_running_twice_on_the_same_inputs_is_idempotent():
    cid = "CP3-T08"
    root = _mkrepo("t08")
    relpath = "tests/test_repeatable_thing.py"
    _write_test_file(root, relpath, SCANNING_FIXTURE_TEXT)
    readme = _readme_fixture(root)
    inventory = _inventory_fixture(root)
    rc1, out1, err1, parsed1 = run_registration(root, relpath, readme, inventory)
    rc2, out2, err2, parsed2 = run_registration(root, relpath, readme, inventory)
    check(cid, "both runs exit 0", rc1 == 0 and rc2 == 0, (rc1, rc2, err1, err2))
    check(cid, "both runs parse as JSON", parsed1 is not None and parsed2 is not None, (out1, out2))
    if parsed1 is not None and parsed2 is not None:
        items1, items2 = parsed1.get("items", []), parsed2.get("items", [])
        check(cid, "the first run emits exactly two items", len(items1) == 2, items1)
        check(cid, "the second run's items are identical to the first run's (never duplicated, "
                   "tripled, or reworded)", items2 == items1, (items1, items2))
        check(cid, "the fixture inputs were never mutated by the check",
              readme.read_text() == README_BASE_TEXT and inventory.read_text() == INVENTORY_BASE_TEXT,
              (readme.read_text(), inventory.read_text()))


# ------------------------------------------------------------------------------------- CP3-X1-01
# round-1 fix for codex-both/X1 (major): a text-based (non-ast.parse) source-inspection test must
# still be recognized as source-scanning and get an inventory item, not silently dropped.

def tx1_01_text_based_source_scan_gets_readme_and_inventory_items():
    cid = "CP3-X1-01"
    root = _mkrepo("tx101")
    relpath = "tests/test_text_scan_thing.py"
    _write_test_file(root, relpath, TEXT_BASED_SCAN_FIXTURE_TEXT)
    readme = _readme_fixture(root)
    inventory = _inventory_fixture(root)
    rc, out, err, parsed = run_registration(root, relpath, readme, inventory)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is not None:
        items = parsed.get("items", [])
        check(cid, "emits exactly two items", len(items) == 2, items)
        check(cid, "emits the exact README line to add",
              {"kind": "readme", "line": "python3 tests/test_text_scan_thing.py"} in items, items)
        check(cid, "emits an inventory-registration item naming the file (text-based scan, not "
                   "ast.parse-based, is still recognized as a source scan)",
              any(it.get("kind") == "inventory" and "test_text_scan_thing.py" in (it.get("line") or "")
                  for it in items), items)
        check(cid, "never emits a manual-check-needed item for a recognized text-based scan",
              items_of_kind(parsed, "manual-check-needed") == [], items)


# ------------------------------------------------------------------------------------- CP3-X1-02
# round-1 fix for codex-both/X1 (major): a file that reads another file's content but pairs it with
# no recognized scan/check shape must be flagged manual-check-needed, never silently decided as
# "doesn't scan source".

def tx1_02_ambiguous_read_without_recognized_check_is_manual_check_needed():
    cid = "CP3-X1-02"
    root = _mkrepo("tx102")
    relpath = "tests/test_ambiguous_read.py"
    _write_test_file(root, relpath, AMBIGUOUS_READ_FIXTURE_TEXT)
    readme = _readme_fixture(root)
    inventory = _inventory_fixture(root)
    rc, out, err, parsed = run_registration(root, relpath, readme, inventory)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is not None:
        items = parsed.get("items", [])
        check(cid, "still emits the README item",
              {"kind": "readme", "line": "python3 tests/test_ambiguous_read.py"} in items, items)
        manual = items_of_kind(parsed, "manual-check-needed")
        check(cid, "emits exactly one manual-check-needed item instead of silently deciding it "
                   "does not scan source",
              len(manual) == 1 and bool((manual[0].get("detail") or "").strip()), items)
        check(cid, "never also emits a false-positive inventory item",
              items_of_kind(parsed, "inventory") == [], items)
        check(cid, "emits exactly two items total", len(items) == 2, items)


# ------------------------------------------------------------------------------------- CP3-X1-03
# round-2 fix for X1 (major): ast.parse(TARGET.read_bytes()) must be recognized as a file-content
# read (read_bytes was missing from the recognized read-call list), so this valid ast.parse-based
# scan is not silently classified as no-scan.

def tx1_03_ast_parse_on_read_bytes_still_gets_inventory_item():
    cid = "CP3-X1-03"
    root = _mkrepo("tx103")
    relpath = "tests/test_ast_parse_read_bytes.py"
    _write_test_file(root, relpath, AST_PARSE_READ_BYTES_FIXTURE_TEXT)
    readme = _readme_fixture(root)
    inventory = _inventory_fixture(root)
    rc, out, err, parsed = run_registration(root, relpath, readme, inventory)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is not None:
        items = parsed.get("items", [])
        check(cid, "emits exactly two items", len(items) == 2, items)
        check(cid, "emits the exact README line to add",
              {"kind": "readme", "line": "python3 tests/test_ast_parse_read_bytes.py"} in items, items)
        check(cid, "emits an inventory-registration item naming the file (ast.parse on "
                   "read_bytes() is still recognized as a source scan)",
              any(it.get("kind") == "inventory"
                  and "test_ast_parse_read_bytes.py" in (it.get("line") or "") for it in items), items)
        check(cid, "never emits a manual-check-needed item for a recognized ast.parse scan",
              items_of_kind(parsed, "manual-check-needed") == [], items)


# ------------------------------------------------------------------------------------- CP3-X2-01
# round-2 fix for X2 (minor): a text-based containment check against a read target whose literal
# extension is an obviously-data one (.json) must be "no-scan", not a structural false positive
# (an ordinary behavior test reading a generated JSON report must not be flagged as a source scan).

def tx2_01_json_report_containment_check_is_not_a_source_scan():
    cid = "CP3-X2-01"
    root = _mkrepo("tx201")
    relpath = "tests/test_json_report_check.py"
    _write_test_file(root, relpath, JSON_REPORT_CHECK_FIXTURE_TEXT)
    readme = _readme_fixture(root)
    inventory = _inventory_fixture(root)
    rc, out, err, parsed = run_registration(root, relpath, readme, inventory)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is not None:
        items = parsed.get("items", [])
        check(cid, "emits exactly one item (the README item only)",
              items == [{"kind": "readme", "line": "python3 tests/test_json_report_check.py"}], items)
        check(cid, "never emits an inventory item for a JSON-report containment check",
              items_of_kind(parsed, "inventory") == [], items)
        check(cid, "never emits a manual-check-needed item either (resolved, not ambiguous)",
              items_of_kind(parsed, "manual-check-needed") == [], items)


# ------------------------------------------------------------------------------------- CP3-X2-02
# round-3 correction of the round-2 fix for X2 (major, codex-both/X1 round-3 finding): a text-based
# containment check against an unresolved/unknown read target (no ast.parse) must resolve to
# "ambiguous"/manual-check-needed, not "no-scan" -- round 2's fix wrongly folded a genuinely
# unresolved read target into the same bucket as a confirmed-data one, silently dropping exactly
# the dynamic/unresolvable-reference case the module's precision bar requires to surface.

def tx2_02_unresolved_read_target_with_text_check_is_ambiguous():
    cid = "CP3-X2-02"
    root = _mkrepo("tx202")
    relpath = "tests/test_unresolved_extension_check.py"
    _write_test_file(root, relpath, UNRESOLVED_EXTENSION_CHECK_FIXTURE_TEXT)
    readme = _readme_fixture(root)
    inventory = _inventory_fixture(root)
    rc, out, err, parsed = run_registration(root, relpath, readme, inventory)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is not None:
        items = parsed.get("items", [])
        check(cid, "emits the README item",
              {"kind": "readme",
               "line": "python3 tests/test_unresolved_extension_check.py"} in items, items)
        check(cid, "never emits an inventory item", items_of_kind(parsed, "inventory") == [], items)
        check(cid, "emits a manual-check-needed item (unresolved read target is ambiguous, not "
                   "silently no-scan)",
              len(items_of_kind(parsed, "manual-check-needed")) == 1, items)
        check(cid, "emits exactly two items total", len(items) == 2, items)


# ------------------------------------------------------------------------------------- CP3-X3-01
# round-3 fix for codex-both/X1 (major, not a planned case): the glob-loop-variable pattern (`for
# path in ROOT.rglob('*.py'): source = path.read_text(); assert OLD_NAME not in source`) is a
# genuinely unresolved read target paired with a text-based check and must resolve to
# manual-check-needed, never be silently dropped as no-scan.

def tx3_01_glob_loop_unresolved_read_target_is_manual_check_needed():
    cid = "CP3-X3-01"
    root = _mkrepo("tx301")
    relpath = "tests/test_glob_loop_unresolved.py"
    _write_test_file(root, relpath, GLOB_LOOP_UNRESOLVED_FIXTURE_TEXT)
    readme = _readme_fixture(root)
    inventory = _inventory_fixture(root)
    rc, out, err, parsed = run_registration(root, relpath, readme, inventory)
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is not None:
        items = parsed.get("items", [])
        check(cid, "emits the README item",
              {"kind": "readme", "line": "python3 tests/test_glob_loop_unresolved.py"} in items,
              items)
        check(cid, "never emits an inventory item", items_of_kind(parsed, "inventory") == [], items)
        check(cid, "emits a manual-check-needed item for the glob-loop-variable read target",
              len(items_of_kind(parsed, "manual-check-needed")) == 1, items)
        check(cid, "emits exactly two items total", len(items) == 2, items)


TESTS = [
    ("CP3-T01", t01_plain_unregistered_file_gets_only_a_readme_item),
    ("CP3-T02", t02_unregistered_source_scanning_file_gets_readme_and_inventory_items),
    ("CP3-T03", t03_fully_registered_file_produces_no_items),
    ("CP3-T04", t04_registered_in_readme_but_missing_from_inventory_gets_only_inventory_item),
    ("CP3-T05", t05_registered_in_inventory_but_missing_from_readme_gets_only_readme_item),
    ("CP3-T06", t06_near_miss_readme_line_is_never_mistaken_for_registration),
    ("CP3-T07", t07_unparseable_file_still_gets_readme_item_plus_manual_check_needed),
    ("CP3-T08", t08_running_twice_on_the_same_inputs_is_idempotent),
    ("CP3-X1-01", tx1_01_text_based_source_scan_gets_readme_and_inventory_items),
    ("CP3-X1-02", tx1_02_ambiguous_read_without_recognized_check_is_manual_check_needed),
    ("CP3-X1-03", tx1_03_ast_parse_on_read_bytes_still_gets_inventory_item),
    ("CP3-X2-01", tx2_01_json_report_containment_check_is_not_a_source_scan),
    ("CP3-X2-02", tx2_02_unresolved_read_target_with_text_check_is_ambiguous),
    ("CP3-X3-01", tx3_01_glob_loop_unresolved_read_target_is_manual_check_needed),
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
