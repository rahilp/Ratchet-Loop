#!/usr/bin/env python3
"""
CP2 tests: find every re-point site for a moved symbol.   python3 tests/test_impact_analysis_repoint.py

Drives skills/ratchet-checkpoint-planner/scripts/impact_analysis.py's `repoint` entry point purely as
an external CLI subprocess (argv only -- never by importing it into the test process) against the
static fixture tree at tests/fixtures/impact_analysis_foo_move/, which mirrors a real symbol move:
`foo` currently lives in pkg/module_a.py and is about to move to pkg/module_b.py.

------------------------------------------------------------------------------------------------ CLI CONTRACT
This test file is this contract's first and only definition right now (CP2 does not exist as code
yet); CP3-CP7's planner prompts, helper scripts and tests must follow it exactly.

Invocation (always a plain OS subprocess, never an in-process import):

    python3 skills/ratchet-checkpoint-planner/scripts/impact_analysis.py repoint <fixture_root> \
        --module-a <dotted.module.a> --module-b <dotted.module.b> --symbol <name>

- `<fixture_root>` (positional): an absolute or relative path to the root of the tree to scan.
- `--module-a` / `--module-b`: the symbol's dotted source and destination module (e.g.
  `pkg.module_a` / `pkg.module_b`). `--symbol`: the bare symbol name (e.g. `foo`).
- No stdin is read by this subcommand.
- stdout: on a successful scan (including an empty findings list), stdout is ONE JSON object and
  nothing else (no banner, no trailing prose) -- a caller can `json.loads(the_whole_stdout)`
  directly:

      {
        "module_a": "pkg.module_a",
        "module_b": "pkg.module_b",
        "symbol": "foo",
        "findings": [
          {"file": "<fixture_root-relative path, forward slashes>",
           "line": <1-based integer line number>,
           "category": "monkeypatch" | "source-inspection" | "importer" | "manual-check-needed",
           "detail": "<optional short human-readable text>"},
          ...
        ]
      }

- exit code: 0 for every successful scan, including every file in the tree that could not be parsed
  (reported as a "manual-check-needed" finding, never silently dropped and never aborting the rest
  of the scan). A nonzero exit is reserved for a genuine CLI usage error (missing `repoint`
  subcommand, missing `<fixture_root>` argument, or a missing `--module-a`/`--module-b`/`--symbol`
  flag).
- Every finding has a non-empty `file` (a real path under `<fixture_root>`, never a bare directory
  or module name) and an integer `line` >= 1 pointing at the real statement -- never a vague
  sentence with no file/line, per done_means #1 and CP2-T10.
- A file this static pass cannot parse (non-Python, or a Python syntax error) is reported as
  "manual-check-needed", never silently dropped from the findings and never aborting the scan of
  the rest of the tree.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id":
"CP2-T01", "status": "passed"|"failed", "message": "..."}]}.
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

IMPACT_SCRIPT = ROOT / "skills" / "ratchet-checkpoint-planner" / "scripts" / "impact_analysis.py"
FIXTURE_ROOT = HERE / "fixtures" / "impact_analysis_foo_move"

MODULE_A = "pkg.module_a"
MODULE_B = "pkg.module_b"
SYMBOL = "foo"

KNOWN_CATEGORIES = {"monkeypatch", "source-inspection", "importer", "manual-check-needed"}

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

def run_repoint(root=FIXTURE_ROOT, module_a=MODULE_A, module_b=MODULE_B, symbol=SYMBOL):
    """Invokes the repoint scan as a plain OS subprocess (never an in-process import), the same way
    the checkpoint-planner agent would call it with the Bash tool. Returns (returncode, stdout,
    stderr, parsed_json_or_None)."""
    proc = subprocess.run(
        [sys.executable, str(IMPACT_SCRIPT), "repoint", str(root),
         "--module-a", module_a, "--module-b", module_b, "--symbol", symbol],
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


def marker_line(fixture_rel: str, marker: str) -> int | None:
    """The 1-based line number of the line ending in `# MARKER: <marker>` inside a fixture file, so
    the expected line a finding must point at is derived from the fixture's own source rather than a
    hard-coded number that would drift if the fixture is edited. None when the marker is not found."""
    path = FIXTURE_ROOT / fixture_rel
    needle = f"# MARKER: {marker}"
    for i, line in enumerate(path.read_text().splitlines(), start=1):
        if needle in line:
            return i
    return None


def findings(parsed) -> list:
    if not parsed or not isinstance(parsed.get("findings"), list):
        return []
    return [f for f in parsed["findings"] if isinstance(f, dict)]


def findings_for_file(parsed, fixture_rel: str) -> list:
    """Every finding whose `file` names this fixture-root-relative path, matched by the normalized
    tail of the path so the scanner is free to report it absolute or relative to the fixture root."""
    target = fixture_rel.replace("\\", "/")
    out = []
    for f in findings(parsed):
        fp = str(f.get("file") or "").replace("\\", "/")
        if fp == target or fp.endswith("/" + target):
            out.append(f)
    return out


def findings_in_category(parsed, category: str) -> list:
    return [f for f in findings(parsed) if f.get("category") == category]


# ------------------------------------------------------------------------------------------------ CP2-T01

def t01_finds_plain_attribute_assignment_monkeypatch():
    cid = "CP2-T01"
    rc, out, err, parsed = run_repoint()
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    expected_line = marker_line("test_moved_symbol.py", "plain-attribute-assignment")
    check(cid, "fixture carries the expected marker", expected_line is not None, "marker missing")
    hits = findings_for_file(parsed, "test_moved_symbol.py")
    match = [f for f in hits if f.get("line") == expected_line and f.get("category") == "monkeypatch"]
    check(cid, f"a 'monkeypatch' finding names test_moved_symbol.py at line {expected_line} "
               f"(the `module_a.foo = Mock()` statement)", bool(match), hits)


# ------------------------------------------------------------------------------------------------ CP2-T02

def t02_finds_setattr_monkeypatch():
    cid = "CP2-T02"
    rc, out, err, parsed = run_repoint()
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    expected_line = marker_line("test_moved_symbol.py", "setattr-monkeypatch")
    other_line = marker_line("test_moved_symbol.py", "plain-attribute-assignment")
    hits = findings_for_file(parsed, "test_moved_symbol.py")
    match = [f for f in hits if f.get("line") == expected_line and f.get("category") == "monkeypatch"]
    check(cid, f"a 'monkeypatch' finding names test_moved_symbol.py at line {expected_line} "
               f"(the `setattr(module_a, \"foo\", Mock())` statement)", bool(match), hits)
    check(cid, "this finding's line is distinct from the plain-assignment finding's line",
          expected_line != other_line, (expected_line, other_line))


# ------------------------------------------------------------------------------------------------ CP2-T03

def t03_finds_string_target_patch_monkeypatch():
    cid = "CP2-T03"
    rc, out, err, parsed = run_repoint()
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    expected_line = marker_line("test_moved_symbol.py", "string-target-patch")
    hits = findings_for_file(parsed, "test_moved_symbol.py")
    match = [f for f in hits if f.get("line") == expected_line and f.get("category") == "monkeypatch"]
    check(cid, f"a 'monkeypatch' finding names test_moved_symbol.py at line {expected_line} "
               f"(the `patch(\"pkg.module_a.foo\")` statement)", bool(match), hits)


# ------------------------------------------------------------------------------------------------ CP2-T04

def t04_finds_patch_object_monkeypatch():
    cid = "CP2-T04"
    rc, out, err, parsed = run_repoint()
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    expected_line = marker_line("test_moved_symbol.py", "patch-object")
    hits = findings_for_file(parsed, "test_moved_symbol.py")
    match = [f for f in hits if f.get("line") == expected_line and f.get("category") == "monkeypatch"]
    check(cid, f"a 'monkeypatch' finding names test_moved_symbol.py at line {expected_line} "
               f"(the `patch.object(module_a, \"foo\")` statement)", bool(match), hits)


# ------------------------------------------------------------------------------------------------ CP2-T05

def t05_finds_disguised_source_inspection_scan():
    cid = "CP2-T05"
    rc, out, err, parsed = run_repoint()
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    expected_line = marker_line("test_source_scan.py", "source-inspection-scan")
    hits = findings_for_file(parsed, "test_source_scan.py")
    match = [f for f in hits if f.get("line") == expected_line and f.get("category") == "source-inspection"]
    check(cid, f"a 'source-inspection' finding names test_source_scan.py at line {expected_line} "
               f"even though test_no_forbidden_helpers_in_core's own name/comments never say 'foo'",
          bool(match), hits)


# ------------------------------------------------------------------------------------------------ CP2-T06

def t06_finds_deliberately_hidden_patch_one_call_away():
    cid = "CP2-T06"
    rc, out, err, parsed = run_repoint()
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    expected_line = marker_line("helpers.py", "hidden-patch")
    helper_hits = findings_for_file(parsed, "helpers.py")
    match = [f for f in helper_hits if f.get("line") == expected_line and f.get("category") == "monkeypatch"]
    check(cid, f"a 'monkeypatch' finding names helpers.py (not just test_hidden_patch.py) at line "
               f"{expected_line}, the real patch.object() call hidden one call away inside the "
               f"helper -- a scan that only inspects test bodies directly would miss this",
          bool(match), helper_hits)


# ------------------------------------------------------------------------------------------------ CP2-T07

def t07_lists_every_importer_direct_and_attribute():
    cid = "CP2-T07"
    rc, out, err, parsed = run_repoint()
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    direct_line = marker_line("importer_direct.py", "importer-direct")
    attr_line = marker_line("importer_attr.py", "importer-attr")
    direct_hits = findings_for_file(parsed, "importer_direct.py")
    attr_hits = findings_for_file(parsed, "importer_attr.py")
    direct_match = [f for f in direct_hits if f.get("line") == direct_line and f.get("category") == "importer"]
    attr_match = [f for f in attr_hits if f.get("line") == attr_line and f.get("category") == "importer"]
    check(cid, f"an 'importer' finding names importer_direct.py at line {direct_line} "
               f"(`from pkg.module_a import foo`)", bool(direct_match), direct_hits)
    check(cid, f"a separate 'importer' finding names importer_attr.py at line {attr_line} "
               f"(the `pkg.module_a.foo(...)` attribute read)", bool(attr_match), attr_hits)


# ------------------------------------------------------------------------------------------------ CP2-T08

def t08_non_python_file_mentioning_symbol_is_manual_check_needed():
    cid = "CP2-T08"
    rc, out, err, parsed = run_repoint()
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    hits = findings_for_file(parsed, "notes.txt")
    check(cid, "notes.txt is not silently absent from the findings", bool(hits), findings(parsed))
    check(cid, "every notes.txt finding is categorized 'manual-check-needed' "
               "(never monkeypatch/source-inspection/importer)",
          bool(hits) and all(f.get("category") == "manual-check-needed" for f in hits), hits)


# ------------------------------------------------------------------------------------------------ CP2-T09

def t09_syntax_error_file_is_manual_check_needed_and_scan_continues():
    cid = "CP2-T09"
    rc, out, err, parsed = run_repoint()
    check(cid, "exits 0 (a syntax error in one fixture file does not abort the whole scan)",
          rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    broken_hits = findings_for_file(parsed, "broken.py")
    check(cid, "broken.py is reported 'manual-check-needed'",
          bool(broken_hits) and all(f.get("category") == "manual-check-needed" for f in broken_hits),
          broken_hits)
    other_categories_present = {
        "monkeypatch": bool(findings_in_category(parsed, "monkeypatch")),
        "source-inspection": bool(findings_in_category(parsed, "source-inspection")),
        "importer": bool(findings_in_category(parsed, "importer")),
    }
    check(cid, "the rest of the tree's findings (monkeypatch, source-inspection, importer) are "
               "still present in this same run's output",
          all(other_categories_present.values()), other_categories_present)


# ------------------------------------------------------------------------------------------------ CP2-T10

def t10_every_finding_carries_an_exact_file_and_line():
    cid = "CP2-T10"
    rc, out, err, parsed = run_repoint()
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    all_findings = findings(parsed)
    check(cid, "the scan produced at least one finding across the full fixture tree",
          bool(all_findings), parsed)
    bad = [f for f in all_findings
           if not isinstance(f.get("file"), str) or not f.get("file").strip()
           or not isinstance(f.get("line"), int) or isinstance(f.get("line"), bool) or f.get("line") < 1]
    check(cid, "every finding has a non-empty exact file path and an integer line number >= 1 "
               "(never a directory, a module name alone, or a line-less generic sentence)",
          not bad, bad)
    bad_category = [f for f in all_findings if f.get("category") not in KNOWN_CATEGORIES]
    check(cid, "every finding's category is one of monkeypatch/source-inspection/importer/"
               "manual-check-needed", not bad_category, bad_category)


# ------------------------------------------------------------------------------------------------ CP2-T11

def t11_substring_match_inside_another_identifier_is_never_flagged():
    cid = "CP2-T11"
    rc, out, err, parsed = run_repoint()
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    hits = findings_for_file(parsed, "decoy_substring.py")
    check(cid, "no finding names decoy_substring.py (it only has `foobar`/`barfoo`, never the "
               "standalone symbol `foo`)", not hits, hits)


# ------------------------------------------------------------------------------------------------ CP2-T12

def t12_importing_module_without_touching_symbol_is_not_an_importer_finding():
    cid = "CP2-T12"
    rc, out, err, parsed = run_repoint()
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    importer_hits = [f for f in findings_for_file(parsed, "decoy_import_only.py") if f.get("category") == "importer"]
    check(cid, "no 'importer' finding is emitted for decoy_import_only.py (`import pkg.module_a` "
               "alone, never reading/assigning/patching `.foo`, is not a reference to the symbol)",
          not importer_hits, importer_hits)


TESTS = [
    ("CP2-T01", t01_finds_plain_attribute_assignment_monkeypatch),
    ("CP2-T02", t02_finds_setattr_monkeypatch),
    ("CP2-T03", t03_finds_string_target_patch_monkeypatch),
    ("CP2-T04", t04_finds_patch_object_monkeypatch),
    ("CP2-T05", t05_finds_disguised_source_inspection_scan),
    ("CP2-T06", t06_finds_deliberately_hidden_patch_one_call_away),
    ("CP2-T07", t07_lists_every_importer_direct_and_attribute),
    ("CP2-T08", t08_non_python_file_mentioning_symbol_is_manual_check_needed),
    ("CP2-T09", t09_syntax_error_file_is_manual_check_needed_and_scan_continues),
    ("CP2-T10", t10_every_finding_carries_an_exact_file_and_line),
    ("CP2-T11", t11_substring_match_inside_another_identifier_is_never_flagged),
    ("CP2-T12", t12_importing_module_without_touching_symbol_is_not_an_importer_finding),
]


# ------------------------------------------------------------------------------------------------ CP2-X01 (not a planned case; added by the round-01 fix loop for the relative-import gap)

def x01_finds_relative_import():
    cid = "CP2-X01"
    rc, out, err, parsed = run_repoint()
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    expected_line = marker_line("pkg/consumer_relative.py", "importer-relative")
    hits = findings_for_file(parsed, "pkg/consumer_relative.py")
    match = [f for f in hits if f.get("line") == expected_line and f.get("category") == "importer"]
    check(cid, f"an 'importer' finding names pkg/consumer_relative.py at line {expected_line} "
               f"(`from .module_a import foo`, resolved against its own package context)",
          bool(match), hits)


# ------------------------------------------------------------------------------------------------ CP2-X02 (not a planned case)

def x02_finds_alias_on_leading_component_of_attribute_chain():
    cid = "CP2-X02"
    rc, out, err, parsed = run_repoint()
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    expected_line = marker_line("importer_alias_attr.py", "importer-alias-attr")
    hits = findings_for_file(parsed, "importer_alias_attr.py")
    match = [f for f in hits if f.get("line") == expected_line and f.get("category") == "importer"]
    check(cid, f"an 'importer' finding names importer_alias_attr.py at line {expected_line} "
               f"(`import pkg as p` then `p.module_a.foo()`, alias resolved on the leading name)",
          bool(match), hits)


# ------------------------------------------------------------------------------------------------ CP2-X03 (not a planned case)

def x03_finds_monkeypatch_setattr_forms_and_aliased_patch():
    cid = "CP2-X03"
    rc, out, err, parsed = run_repoint()
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    hits = findings_for_file(parsed, "test_monkeypatch_forms.py")
    module_line = marker_line("test_monkeypatch_forms.py", "monkeypatch-setattr-module")
    string_line = marker_line("test_monkeypatch_forms.py", "monkeypatch-setattr-string")
    alias_line = marker_line("test_monkeypatch_forms.py", "aliased-patch-import")
    module_match = [f for f in hits if f.get("line") == module_line and f.get("category") == "monkeypatch"]
    string_match = [f for f in hits if f.get("line") == string_line and f.get("category") == "monkeypatch"]
    alias_match = [f for f in hits if f.get("line") == alias_line and f.get("category") == "monkeypatch"]
    check(cid, f"a 'monkeypatch' finding at line {module_line} "
               f"(`monkeypatch.setattr(module_a, \"foo\", ...)`)", bool(module_match), hits)
    check(cid, f"a 'monkeypatch' finding at line {string_line} "
               f"(`monkeypatch.setattr(\"pkg.module_a.foo\", ...)`)", bool(string_match), hits)
    check(cid, f"a 'monkeypatch' finding at line {alias_line} "
               f"(`mock_patch(\"pkg.module_a.foo\")`, patch imported under an alias)",
          bool(alias_match), hits)


# ------------------------------------------------------------------------------------------------ CP2-X04 (not a planned case)

def x04_finds_literal_collection_source_inspection():
    cid = "CP2-X04"
    rc, out, err, parsed = run_repoint()
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    expected_line = marker_line("test_source_scan_literal_collection.py", "source-inspection-literal-collection")
    hits = findings_for_file(parsed, "test_source_scan_literal_collection.py")
    match = [f for f in hits if f.get("line") == expected_line and f.get("category") == "source-inspection"]
    check(cid, f"a 'source-inspection' finding at line {expected_line} "
               f"(`expected = {{'foo'}}; assert expected <= defined`)", bool(match), hits)


# ------------------------------------------------------------------------------------------------ CP2-X05 (not a planned case)

def x05_finds_combined_single_literal_path_source_inspection():
    cid = "CP2-X05"
    rc, out, err, parsed = run_repoint()
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    expected_line = marker_line("test_source_scan_combined_path.py", "source-inspection-combined-path")
    hits = findings_for_file(parsed, "test_source_scan_combined_path.py")
    match = [f for f in hits if f.get("line") == expected_line and f.get("category") == "source-inspection"]
    check(cid, f"a 'source-inspection' finding at line {expected_line} "
               f"(`TARGET = HERE / \"pkg/module_a.py\"`, a single combined path literal)",
          bool(match), hits)


TESTS.extend([
    ("CP2-X01", x01_finds_relative_import),
    ("CP2-X02", x02_finds_alias_on_leading_component_of_attribute_chain),
    ("CP2-X03", x03_finds_monkeypatch_setattr_forms_and_aliased_patch),
    ("CP2-X04", x04_finds_literal_collection_source_inspection),
    ("CP2-X05", x05_finds_combined_single_literal_path_source_inspection),
])


# ------------------------------------------------------------------------------------------------ CP2-X06 (not a planned case; added by the round-02 fix loop for the relative-module-import gap:
# `from . import module_a [as x]` and a deeper `from .. import x` were still silently missed)

def x06_finds_bare_and_deeper_relative_module_imports():
    cid = "CP2-X06"
    rc, out, err, parsed = run_repoint()
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    bare_import_line = marker_line("pkg/consumer_relative_bare.py", "importer-relative-bare-aliased")
    bare_attr_line = marker_line("pkg/consumer_relative_bare.py", "importer-relative-bare-attr")
    dotdot_import_line = marker_line("pkg/sub/consumer_relative_dotdot.py", "importer-relative-dotdot-bare")
    dotdot_patch_line = marker_line("pkg/sub/consumer_relative_dotdot.py", "monkeypatch-relative-dotdot")
    bare_hits = findings_for_file(parsed, "pkg/consumer_relative_bare.py")
    dotdot_hits = findings_for_file(parsed, "pkg/sub/consumer_relative_dotdot.py")
    bare_attr_match = [f for f in bare_hits if f.get("line") == bare_attr_line and f.get("category") == "importer"]
    dotdot_patch_match = [f for f in dotdot_hits if f.get("line") == dotdot_patch_line and f.get("category") == "monkeypatch"]
    check(cid, f"an 'importer' finding names pkg/consumer_relative_bare.py at line {bare_attr_line} "
               f"(`from . import module_a as ma` then `ma.foo()`, the bare relative module import "
               f"resolved and aliased)", bool(bare_attr_match), bare_hits)
    check(cid, f"a 'monkeypatch' finding names pkg/sub/consumer_relative_dotdot.py at line "
               f"{dotdot_patch_line} (`from .. import module_a` then `module_a.foo = ...`, a "
               f"level-2 relative module import resolved up to the grandparent package)",
          bool(dotdot_patch_match), dotdot_hits)
    check(cid, "both relative-module-import marker lines were found in the fixtures",
          None not in (bare_import_line, bare_attr_line, dotdot_import_line, dotdot_patch_line),
          (bare_import_line, bare_attr_line, dotdot_import_line, dotdot_patch_line))


# ------------------------------------------------------------------------------------------------ CP2-X07 (not a planned case; added by the round-02 fix loop for the unresolved-receiver gap:
# a monkeypatch/importer site on a receiver that cannot be resolved to module_a through any
# tracked binding -- e.g. a module loaded dynamically via importlib -- used to be silently dropped)

def x07_unresolved_receiver_is_manual_check_needed():
    cid = "CP2-X07"
    rc, out, err, parsed = run_repoint()
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    patch_line = marker_line("test_importlib_unresolved.py", "unresolved-receiver-monkeypatch")
    read_line = marker_line("test_importlib_unresolved.py", "unresolved-receiver-read")
    hits = findings_for_file(parsed, "test_importlib_unresolved.py")
    patch_match = [f for f in hits if f.get("line") == patch_line and f.get("category") == "manual-check-needed"]
    read_match = [f for f in hits if f.get("line") == read_line and f.get("category") == "manual-check-needed"]
    check(cid, f"a 'manual-check-needed' finding at line {patch_line} (`mod.foo = ...` where `mod` "
               f"came from importlib.import_module and is never guessed to be {MODULE_A})",
          bool(patch_match), hits)
    check(cid, f"a 'manual-check-needed' finding at line {read_line} (`real_foo = mod.foo`, the "
               f"same unresolved receiver read)", bool(read_match), hits)


# ------------------------------------------------------------------------------------------------ CP2-X08 (not a planned case; added by the round-02 fix loop for the general string-literal
# fallback: a dict-key inventory or regex scan over the bare symbol name, in a file already tied to
# module_a, used to be silently dropped unless it happened to be a Compare operand)

def x08_general_string_literal_fallback_in_module_a_connected_file():
    cid = "CP2-X08"
    rc, out, err, parsed = run_repoint()
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    dict_line = marker_line("test_string_literal_fallback.py", "string-literal-fallback-dict-key")
    regex_line = marker_line("test_string_literal_fallback.py", "string-literal-fallback-regex")
    hits = findings_for_file(parsed, "test_string_literal_fallback.py")
    dict_match = [f for f in hits if f.get("line") == dict_line and f.get("category") == "manual-check-needed"]
    regex_match = [f for f in hits if f.get("line") == regex_line and f.get("category") == "manual-check-needed"]
    check(cid, f"a 'manual-check-needed' finding at line {dict_line} (`{{'foo': ...}}`, a dict-key "
               f"inventory, never a Compare operand)", bool(dict_match), hits)
    check(cid, f"a 'manual-check-needed' finding at line {regex_line} (`re.search('foo', source)`, "
               f"a regex scan, never a Compare operand)", bool(regex_match), hits)


# ------------------------------------------------------------------------------------------------ CP2-X09 (not a planned case; added by the round-02 fix loop for the non-Python noise flood:
# every non-.py file used to be flagged unconditionally; now only one that mentions the symbol by
# name is, and common noise directories are skipped outright)

def x09_non_python_file_not_mentioning_symbol_is_silent_and_noise_dirs_are_skipped():
    cid = "CP2-X09"
    rc, out, err, parsed = run_repoint()
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    quiet_hits = findings_for_file(parsed, "other_notes.txt")
    check(cid, "other_notes.txt (only 'foobar'/'barfoo', never the standalone symbol) produces no "
               "finding at all", not quiet_hits, quiet_hits)
    noise_hits = [f for f in findings(parsed) if "__pycache__" in str(f.get("file") or "")]
    check(cid, "a file inside a noise directory (__pycache__) is never surfaced, even though it "
               "mentions the symbol by name", not noise_hits, noise_hits)


TESTS.extend([
    ("CP2-X06", x06_finds_bare_and_deeper_relative_module_imports),
    ("CP2-X07", x07_unresolved_receiver_is_manual_check_needed),
    ("CP2-X08", x08_general_string_literal_fallback_in_module_a_connected_file),
    ("CP2-X09", x09_non_python_file_not_mentioning_symbol_is_silent_and_noise_dirs_are_skipped),
])


# ------------------------------------------------------------------------------------------------ CP2-X10 (not a planned case; added by the round-03 fix loop for the star-import gap: `from
# pkg.module_a import *` followed by a bare `foo()` call used to produce no finding at all)

def x10_star_import_is_manual_check_needed_at_its_own_line():
    cid = "CP2-X10"
    rc, out, err, parsed = run_repoint()
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    expected_line = marker_line("consumer_star_import.py", "star-import")
    hits = findings_for_file(parsed, "consumer_star_import.py")
    match = [f for f in hits if f.get("line") == expected_line and f.get("category") == "manual-check-needed"]
    check(cid, f"a single 'manual-check-needed' finding names consumer_star_import.py at line "
               f"{expected_line} (`from pkg.module_a import *`), and nothing else", bool(match), hits)
    check(cid, "exactly one finding is produced for this file (the import line itself, not a "
               "rescan of the rest of the file for a bare `foo()` call)",
          len(hits) == 1, hits)


# ------------------------------------------------------------------------------------------------ CP2-X11 (not a planned case; added by the round-03 fix loop for the keyword-argument gap:
# setattr/monkeypatch.setattr/patch/patch.object spelled with keyword arguments instead of
# positional ones used to be silently missed. Round-04 fix for host-alt-correctness/A1: pytest's
# real MonkeyPatch.setattr signature uses the `target` keyword, not `object`, so the fixture and
# this check were corrected to the real API, and a dotted-string keyword case was added in a file
# with no other static connection to module_a.)

def x11_keyword_argument_forms_are_found():
    cid = "CP2-X11"
    rc, out, err, parsed = run_repoint()
    check(cid, "exits 0", rc == 0, (rc, out, err))
    check(cid, "parses as JSON", parsed is not None, out)
    if parsed is None:
        return
    hits = findings_for_file(parsed, "test_keyword_args.py")
    setattr_line = marker_line("test_keyword_args.py", "setattr-keyword")
    patch_line = marker_line("test_keyword_args.py", "patch-keyword-target")
    patch_object_line = marker_line("test_keyword_args.py", "patch-object-keyword-args")
    setattr_match = [f for f in hits if f.get("line") == setattr_line and f.get("category") == "monkeypatch"]
    patch_match = [f for f in hits if f.get("line") == patch_line and f.get("category") == "monkeypatch"]
    patch_object_match = [f for f in hits if f.get("line") == patch_object_line and f.get("category") == "monkeypatch"]
    check(cid, f"a 'monkeypatch' finding at line {setattr_line} "
               f"(`monkeypatch.setattr(target=module_a, name=\"foo\", value=...)`)",
          bool(setattr_match), hits)
    check(cid, f"a 'monkeypatch' finding at line {patch_line} "
               f"(`patch(target=\"pkg.module_a.foo\", new=...)`)", bool(patch_match), hits)
    check(cid, f"a 'monkeypatch' finding at line {patch_object_line} "
               f"(`patch.object(target=module_a, attribute=\"foo\")`)",
          bool(patch_object_match), hits)

    dotted_hits = findings_for_file(parsed, "test_keyword_args_dotted_string.py")
    dotted_line = marker_line("test_keyword_args_dotted_string.py", "setattr-keyword-dotted-string")
    dotted_match = [f for f in dotted_hits if f.get("line") == dotted_line and f.get("category") == "monkeypatch"]
    check(cid, f"a 'monkeypatch' finding at line {dotted_line} in test_keyword_args_dotted_string.py "
               f"(`monkeypatch.setattr(target=\"pkg.module_a.foo\", value=...)`, resolved purely via "
               "the `target` keyword's dotted-string value, with no import of module_a in that file "
               "to fall back on)",
          bool(dotted_match), dotted_hits)


TESTS.extend([
    ("CP2-X10", x10_star_import_is_manual_check_needed_at_its_own_line),
    ("CP2-X11", x11_keyword_argument_forms_are_found),
])


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
