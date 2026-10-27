#!/usr/bin/env python3
"""
CP7 tests: prove the full pass on a replay.   python3 tests/test_impact_analysis_e2e.py

End-to-end tests of a single COMPOSED CLI entry point that wraps CP1's `classify`, CP2's `repoint`,
CP3's `registration`, CP4's `parity`/`hazard` and CP5's `merge` -- all six of
skills/ratchet-checkpoint-planner/scripts/impact_analysis.py's other existing subcommands (`full`
itself being the 7th subcommand overall) -- into one call,
run once per fixture tree against two synthetic fixtures under tests/fixtures/:
tests/fixtures/impact_analysis_m_shaped/ (shaped after lane M's own CP3-CP5 extraction pain: a helper
moving out of one module into a new sibling module, with a monkeypatch-by-name test AND a disguised
source-inspection scan of the old module, a new unregistered test file, and no standing parity gate)
and tests/fixtures/impact_analysis_greenfield/ (a checkpoint whose files_likely are all brand-new
paths, with zero references anywhere in the tree). Like every CP1-CP6 test file, this drives
impact_analysis.py purely as an external OS subprocess (argv/stdin/stdout) -- never by importing the
module into the test process.

------------------------------------------------------------------------------------------------ CLI CONTRACT
The composed `full` subcommand is implemented in impact_analysis.py (picked over `end-to-end` as the
shorter, unambiguous verb-shaped name; matches this module's other subcommand names being short
nouns/verbs: classify, repoint, registration, parity, hazard, merge). This file is the contract test
for it: every case below drives the real subcommand as a plain OS subprocess and checks its behavior
against the contract laid out below.

Usage (always a plain OS subprocess):

    python3 skills/ratchet-checkpoint-planner/scripts/impact_analysis.py full <fixture_root> \\
        --checkpoint <checkpoint_json_path>

No stdin is read. `<fixture_root>`: the root directory to scan -- the same meaning `repoint`'s
<fixture_root> and `classify`'s <repo_root> already carry. `--checkpoint <path>`: a path (absolute,
or relative to the current working directory) to a checkpoint JSON file shaped like a real
checkpoints.json entry, PLUS three extra optional keys this composed command alone reads:

    {
      "files_likely": ["<fixture-root-relative path>", ...],      // required; classify() input
      "move": null | {"symbol": "...", "module_from": "...", "module_to": "..."},  // optional; default null
      "baseline": null | {"module": "...", "startup_ms": <num>, "threshold_pct": <num>},  // optional
      "done_when": ["<existing line>", ...],                      // required
      "cases": [{"id": "...", ...}, ...],                         // required
      "tests": {"command": "<the plan's tests.command>", ...},    // optional; omitted -> hazard skipped
      "readme_list": null | "<fixture-root-relative path>",       // optional; registration's --readme-list
      "inventory": null | "<fixture-root-relative path>",         // optional; registration's --inventory
      "new_test_file": null | "<fixture-root-relative path>"      // optional; registration's 2nd positional
    }

Internally, in this fixed order, `full`:

  1. Runs `classify(<fixture_root>, files_likely)` (CP1). This decides `mode`/`reason`.
  2. When `mode != "greenfield-create"` AND `move` is not null: runs `repoint_scan(<fixture_root>,
     move.module_from, move.module_to, move.symbol)` (CP2). Otherwise `repoint` in the output is
     `null` (never an empty-but-present dict -- a caller can tell "skipped" apart from "ran and found
     nothing" this way).
  3. When `mode != "greenfield-create"` AND `new_test_file` is non-null: runs
     `registration_check(<fixture_root>, new_test_file, readme_list, inventory)` (CP3), where
     `readme_list` and `inventory` are each passed through as `None` when the checkpoint omits
     either -- registration_check emits a manual-check-needed item in place of whichever side (README
     or inventory) has no fixture path given, instead of silently skipping it, and still emits the
     other side normally. Otherwise `registration` in the output is `null`.
  4. Always runs `parity_check(move, done_when, baseline)` (CP4).
  5. When `tests.command` is present: runs `hazard_check(tests.command)` (CP4). Otherwise `hazard` is
     `{"hazard": false, "suites": [], "detail": ""}`.
  6. Builds `enumerated_items.done_when` -- one line per item below, in this order, and
     `enumerated_items.cases: []` (this composed pass never invents new planned cases, only done_when
     lines) -- then calls `merge_checkpoint({"done_when": done_when, "cases": cases},
     enumerated_items)` (CP5):
       - one line per repoint finding (skipped when step 2 was skipped): an f-string of the exact
         shape `f"Re-point {finding['file']}:{finding['line']} ({finding['category']}):
         {finding['detail']}"`.
       - one line per registration item (skipped when step 3 was skipped):
         `f"Register in the README test list: {item['line']}"` for kind "readme",
         `f"Register in the test-scan inventory: {item['line']}"` for kind "inventory",
         `f"Manual check needed (registration): {item['detail']}"` for kind "manual-check-needed".
       - one line per parity item (from step 4) whose `already_present` is `false`: `item['line']`
         verbatim (already human-readable). An item whose `already_present` is `true` contributes no
         line -- it is already covered, so adding it again would either duplicate it or, worse, rely
         on merge's own exact-text dedup to save it; this pass does not rely on that for parity.
       - one line when step 5's `hazard` is true (skipped when hazard is false): `f"Report-race
         hazard: {hazard['detail']}"`. A true hazard is not only reported in this call's own stdout
         but also persisted into the approved checkpoint's done_when list by this step.
     A successful merge (merge always succeeds for a well-formed checkpoint; `full` never sends
     `existing_done_when_override`) OVERWRITES the checkpoint JSON file at `--checkpoint` ON DISK,
     in place, replacing only its `done_when` and `cases` keys with the merge's result and leaving
     every other top-level key's (`files_likely`, `move`, `baseline`, `tests`, `readme_list`,
     `inventory`, `new_test_file`, `notes`, `id`, `_comment`, ...) VALUE unchanged -- the file itself
     is rewritten whole via `json.dumps`, so whitespace/formatting can differ even though no other
     field's value changes; this is not a literal byte-for-byte-unchanged file. This happens on EVERY
     successful invocation, unconditionally -- there is no flag to suppress it. A
     merge refusal (reason "invalid_shape", "done_when_override_mismatch" or "case_id_collision" --
     never expected to actually fire for any fixture in this file) is propagated: exit 3, the exact
     refusal JSON merge's own contract already defines on stderr, no stdout, and NO write to disk.

stdout on success: exit code 0, ONE JSON object and nothing else:

    {
      "mode": "greenfield-create" | "brownfield-modify",
      "reason": "...",
      "repoint": null | {"module_a": ..., "module_b": ..., "symbol": ..., "findings": [...]},
      "registration": null | {"test_file": ..., "items": [...]},
      "parity": {"is_extraction": bool, "items": [...]},
      "hazard": {"hazard": bool, "suites": [...], "detail": "..."},
      "merged": {"done_when": [...], "cases": [...]}
    }

Nonzero exit is reserved for: a genuine CLI usage error (exit 2 -- missing `<fixture_root>`, missing
`--checkpoint`, a `--checkpoint` file that cannot be read or does not parse as JSON, or one missing
the required `files_likely`/`done_when`/`cases` keys), or a merge refusal (exit 3, as above). No case
in this file is expected to hit either nonzero path -- both fixtures are well-formed.

Idempotence: running `full` twice in a row against the same `--checkpoint` path, with no change to
the fixture tree in between, must add nothing new the second time (CP7-T13) -- the second run's
`repoint`/`registration` findings regenerate the identical line text (merge's own exact-text dedup
catches them), and the second run's `parity` items now report `already_present: true` for any item
whose line this pass's own first run already added (so no new parity line is generated either).

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id":
"CP7-T01", "status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FIXTURES = HERE / "fixtures"
PLANNER_DOC = ROOT / "agents" / "ratchet-checkpoint-planner.md"

sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402

IMPACT_SCRIPT = ROOT / "skills" / "ratchet-checkpoint-planner" / "scripts" / "impact_analysis.py"

PASS = FAIL = 0
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}


def check(cid, name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
    if not cond:
        CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'OK' if cond else 'FAIL'} [{cid}] {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ------------------------------------------------------------------------------------------------ helpers

def fresh_fixture(name: str):
    """Copies tests/fixtures/impact_analysis_<name> into a brand-new temp directory, so `full`'s
    write-back to the checkpoint JSON (see module docstring) never mutates the committed fixture
    tree this file and every other test run shares. Returns (fixture_root, checkpoint_json_path,
    cleanup_callable)."""
    src = FIXTURES / f"impact_analysis_{name}"
    tmp = Path(_tmp.mkdtemp(prefix=f"cp7_{name}_"))
    dest = tmp / "fixture"
    shutil.copytree(src, dest)
    checkpoint_path = dest / "checkpoint.json"

    def cleanup():
        shutil.rmtree(tmp, ignore_errors=True)

    return dest, checkpoint_path, cleanup


def run_full(fixture_root, checkpoint_path):
    """Invokes the not-yet-existing `full` subcommand as a plain OS subprocess, the same way the
    checkpoint-planner agent would call it with the Bash tool. Returns (returncode, stdout, stderr,
    parsed_stdout_json_or_None). Never raises on a subprocess that exits nonzero or prints
    unparsable stdout -- both are reported as ordinary case failures below, not crashes."""
    proc = subprocess.run(
        [sys.executable, str(IMPACT_SCRIPT), "full", str(fixture_root),
         "--checkpoint", str(checkpoint_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        parsed = json.loads(proc.stdout) if proc.stdout.strip() else None
    except Exception:
        parsed = None
    return proc.returncode, proc.stdout, proc.stderr, parsed


def marker_line(path: Path, marker: str) -> "int | None":
    """The 1-based line number of the line ending in `# MARKER: <marker>` inside a fixture file, so
    the expected line a finding must point at is derived from the fixture's own source rather than a
    hard-coded number that would drift if the fixture is edited."""
    needle = f"# MARKER: {marker}"
    for i, line in enumerate(path.read_text().splitlines(), start=1):
        if needle in line:
            return i
    return None


def findings_in(parsed, key: str) -> list:
    repoint = (parsed or {}).get(key)
    if not isinstance(repoint, dict) or not isinstance(repoint.get("findings"), list):
        return []
    return [f for f in repoint["findings"] if isinstance(f, dict)]


def findings_for_file(findings: list, fixture_rel: str) -> list:
    target = fixture_rel.replace("\\", "/")
    out = []
    for f in findings:
        fp = str(f.get("file") or "").replace("\\", "/")
        if fp == target or fp.endswith("/" + target):
            out.append(f)
    return out


def load_checkpoint(path: Path) -> dict:
    return json.loads(path.read_text())


# ------------------------------------------------------------------------------------------------ CP7-T01

def t01_one_command_runs_the_whole_pass_on_the_m_shaped_fixture():
    cid = "CP7-T01"
    fixture_root, checkpoint_path, cleanup = fresh_fixture("m_shaped")
    try:
        rc, out, err, parsed = run_full(fixture_root, checkpoint_path)
        check(cid, "exits 0", rc == 0, (rc, out, err))
        check(cid, "prints one JSON object on stdout", parsed is not None, out)
        if parsed is None:
            return
        for key in ("mode", "repoint", "registration", "parity", "hazard", "merged"):
            check(cid, f"combined result carries a '{key}' key", key in parsed, parsed)
        check(cid, "repoint ran (not skipped) and found at least one finding",
              isinstance(parsed.get("repoint"), dict) and len(parsed["repoint"].get("findings", [])) > 0,
              parsed.get("repoint"))
        check(cid, "registration ran (not skipped) and returned items",
              isinstance(parsed.get("registration"), dict), parsed.get("registration"))
        check(cid, "parity ran and returned exactly 2 items",
              isinstance(parsed.get("parity"), dict) and len(parsed["parity"].get("items", [])) == 2,
              parsed.get("parity"))
        check(cid, "hazard ran and returned a verdict",
              isinstance(parsed.get("hazard"), dict) and "hazard" in parsed["hazard"], parsed.get("hazard"))
        check(cid, "no second command was required to get all four classes in one invocation", True)
    finally:
        cleanup()


# ------------------------------------------------------------------------------------------------ CP7-T02

def t02_m_shaped_fixture_is_classified_brownfield_not_greenfield():
    cid = "CP7-T02"
    fixture_root, checkpoint_path, cleanup = fresh_fixture("m_shaped")
    try:
        rc, out, err, parsed = run_full(fixture_root, checkpoint_path)
        check(cid, "exits 0", rc == 0, (rc, out, err))
        check(cid, "parses as JSON", parsed is not None, out)
        if parsed is None:
            return
        check(cid, "mode is brownfield-modify, not greenfield-create (fails safe on the mixed "
                   "new pkg/module_b.py + existing pkg/module_a.py files_likely)",
              parsed.get("mode") == "brownfield-modify", parsed.get("mode"))
        check(cid, "a non-empty reason is given", bool(parsed.get("reason")), parsed.get("reason"))
    finally:
        cleanup()


# ------------------------------------------------------------------------------------------------ CP7-T03

def t03_the_hidden_monkeypatch_repoint_site_is_in_the_combined_output():
    cid = "CP7-T03"
    fixture_root, checkpoint_path, cleanup = fresh_fixture("m_shaped")
    try:
        rc, out, err, parsed = run_full(fixture_root, checkpoint_path)
        check(cid, "exits 0", rc == 0, (rc, out, err))
        check(cid, "parses as JSON", parsed is not None, out)
        if parsed is None:
            return
        expected_line = marker_line(fixture_root / "test_moved_helper.py", "monkeypatch-by-name")
        check(cid, "fixture carries the expected marker", expected_line is not None, "marker missing")
        hits = findings_for_file(findings_in(parsed, "repoint"), "test_moved_helper.py")
        match = [f for f in hits if f.get("line") == expected_line and f.get("category") == "monkeypatch"]
        check(cid, f"a 'monkeypatch' re-point item names test_moved_helper.py at line {expected_line} "
                   "(the patch.object(module_a, \"_load_state\") statement)", bool(match), hits)
    finally:
        cleanup()


# ------------------------------------------------------------------------------------------------ CP7-T04

def t04_the_stale_source_scan_site_is_found_even_though_its_name_hides_the_move():
    cid = "CP7-T04"
    fixture_root, checkpoint_path, cleanup = fresh_fixture("m_shaped")
    try:
        rc, out, err, parsed = run_full(fixture_root, checkpoint_path)
        check(cid, "exits 0", rc == 0, (rc, out, err))
        check(cid, "parses as JSON", parsed is not None, out)
        if parsed is None:
            return
        expected_line = marker_line(fixture_root / "test_moved_helper.py", "source-inspection-scan")
        check(cid, "fixture carries the expected marker", expected_line is not None, "marker missing")
        hits = findings_for_file(findings_in(parsed, "repoint"), "test_moved_helper.py")
        match = [f for f in hits if f.get("line") == expected_line and f.get("category") == "source-inspection"]
        check(cid, f"a 'source-inspection' re-point item names test_moved_helper.py at line "
                   f"{expected_line}, even though test_no_forbidden_helpers_defined_in_core's own "
                   "name/comments never say module_a or the move", bool(match), hits)
    finally:
        cleanup()


# ------------------------------------------------------------------------------------------------ CP7-T05

def t05_readme_and_inventory_registration_items_appear_for_the_new_test_file():
    cid = "CP7-T05"
    fixture_root, checkpoint_path, cleanup = fresh_fixture("m_shaped")
    try:
        rc, out, err, parsed = run_full(fixture_root, checkpoint_path)
        check(cid, "exits 0", rc == 0, (rc, out, err))
        check(cid, "parses as JSON", parsed is not None, out)
        if parsed is None:
            return
        registration = parsed.get("registration") or {}
        items = registration.get("items", [])
        check(cid, "a 'readme' item names the exact README line to add",
              any(i.get("kind") == "readme" and i.get("line") == "python3 test_moved_helper.py"
                  for i in items), items)
        check(cid, "an 'inventory' item names the exact inventory-fixture entry to add",
              any(i.get("kind") == "inventory" and i.get("line") == "test_moved_helper.py"
                  for i in items), items)
    finally:
        cleanup()


# ------------------------------------------------------------------------------------------------ CP7-T06

def t06_startup_and_behavior_parity_items_are_added_even_though_never_asked_for():
    cid = "CP7-T06"
    fixture_root, checkpoint_path, cleanup = fresh_fixture("m_shaped")
    try:
        original = load_checkpoint(checkpoint_path)
        check(cid, "fixture sanity: the original done_when has no parity wording at all",
              not any("parity" in line.lower() for line in original.get("done_when", [])),
              original.get("done_when"))
        rc, out, err, parsed = run_full(fixture_root, checkpoint_path)
        check(cid, "exits 0", rc == 0, (rc, out, err))
        check(cid, "parses as JSON", parsed is not None, out)
        if parsed is None:
            return
        items = (parsed.get("parity") or {}).get("items", [])
        startup = [i for i in items if i.get("kind") == "startup-parity"]
        behavior = [i for i in items if i.get("kind") == "behavior-parity"]
        check(cid, "exactly one standing startup-parity item is present", len(startup) == 1, items)
        check(cid, "exactly one behavior byte-identical-snapshot item is present", len(behavior) == 1, items)
        merged_done_when = (parsed.get("merged") or {}).get("done_when", [])
        check(cid, "the startup-parity line was folded into the merged done_when",
              bool(startup) and startup[0].get("line") in merged_done_when, merged_done_when)
        check(cid, "the behavior-parity line was folded into the merged done_when",
              bool(behavior) and behavior[0].get("line") in merged_done_when, merged_done_when)
    finally:
        cleanup()


# ------------------------------------------------------------------------------------------------ CP7-T07

def t07_all_four_item_classes_come_back_before_any_implementation_code_exists():
    cid = "CP7-T07"
    fixture_root, checkpoint_path, cleanup = fresh_fixture("m_shaped")
    try:
        check(cid, "fixture sanity: the sibling module has not been created yet (the move has not "
                   "actually been made)", not (fixture_root / "pkg" / "module_b.py").exists(),
              list((fixture_root / "pkg").iterdir()))
        rc, out, err, parsed = run_full(fixture_root, checkpoint_path)
        check(cid, "the command still succeeds (exit 0)", rc == 0, (rc, out, err))
        check(cid, "parses as JSON", parsed is not None, out)
        if parsed is None:
            return
        repoint_findings = findings_in(parsed, "repoint")
        check(cid, "at least one re-point (monkeypatch) item is present",
              any(f.get("category") == "monkeypatch" for f in repoint_findings), repoint_findings)
        check(cid, "at least one stale source-scan (source-inspection) item is present",
              any(f.get("category") == "source-inspection" for f in repoint_findings), repoint_findings)
        registration_items = (parsed.get("registration") or {}).get("items", [])
        check(cid, "at least one registration item is present", bool(registration_items), registration_items)
        parity_items = (parsed.get("parity") or {}).get("items", [])
        check(cid, "at least one parity item is present", bool(parity_items), parity_items)
    finally:
        cleanup()


# ------------------------------------------------------------------------------------------------ CP7-T08

def t08_a_single_suite_tests_command_is_never_flagged_as_a_report_race_hazard():
    cid = "CP7-T08"
    fixture_root, checkpoint_path, cleanup = fresh_fixture("m_shaped")
    try:
        original = load_checkpoint(checkpoint_path)
        check(cid, "fixture sanity: tests.command runs only test_moved_helper.py",
              original.get("tests", {}).get("command") == "python3 test_moved_helper.py",
              original.get("tests"))
        rc, out, err, parsed = run_full(fixture_root, checkpoint_path)
        check(cid, "exits 0", rc == 0, (rc, out, err))
        check(cid, "parses as JSON", parsed is not None, out)
        if parsed is None:
            return
        hazard = parsed.get("hazard") or {}
        check(cid, "hazard is false for this checkpoint's single-suite tests.command",
              hazard.get("hazard") is False, hazard)
    finally:
        cleanup()


# ------------------------------------------------------------------------------------------------ CP7-T09

def t09_greenfield_fixture_is_classified_greenfield_create():
    cid = "CP7-T09"
    fixture_root, checkpoint_path, cleanup = fresh_fixture("greenfield")
    try:
        rc, out, err, parsed = run_full(fixture_root, checkpoint_path)
        check(cid, "exits 0", rc == 0, (rc, out, err))
        check(cid, "parses as JSON", parsed is not None, out)
        if parsed is None:
            return
        check(cid, "mode is greenfield-create", parsed.get("mode") == "greenfield-create", parsed.get("mode"))
    finally:
        cleanup()


# ------------------------------------------------------------------------------------------------ CP7-T10

def t10_greenfield_fixture_gets_zero_repoint_items():
    cid = "CP7-T10"
    fixture_root, checkpoint_path, cleanup = fresh_fixture("greenfield")
    try:
        rc, out, err, parsed = run_full(fixture_root, checkpoint_path)
        check(cid, "exits 0", rc == 0, (rc, out, err))
        check(cid, "parses as JSON", parsed is not None, out)
        if parsed is None:
            return
        repoint = parsed.get("repoint")
        ok = repoint is None or repoint.get("findings") == []
        check(cid, "the re-point items list is empty (repoint skipped or returned no findings)",
              ok, repoint)
    finally:
        cleanup()


# ------------------------------------------------------------------------------------------------ CP7-T11

def t11_greenfield_fixture_plan_stays_clean_of_parity_and_hazard_noise_too():
    cid = "CP7-T11"
    fixture_root, checkpoint_path, cleanup = fresh_fixture("greenfield")
    try:
        rc, out, err, parsed = run_full(fixture_root, checkpoint_path)
        check(cid, "exits 0", rc == 0, (rc, out, err))
        check(cid, "parses as JSON", parsed is not None, out)
        if parsed is None:
            return
        parity_items = (parsed.get("parity") or {}).get("items", [])
        check(cid, "parity items are empty for a plain new-file checkpoint", parity_items == [], parity_items)
        hazard = parsed.get("hazard") or {}
        check(cid, "hazard is false for a plain new-file checkpoint's single-suite tests.command",
              hazard.get("hazard") is False, hazard)
    finally:
        cleanup()


# ------------------------------------------------------------------------------------------------ CP7-T12

def t12_the_composed_pass_only_adds_to_the_m_shaped_fixtures_existing_checkpoint_json():
    cid = "CP7-T12"
    fixture_root, checkpoint_path, cleanup = fresh_fixture("m_shaped")
    try:
        original = load_checkpoint(checkpoint_path)
        original_done_when = list(original.get("done_when", []))
        original_cases = original.get("cases", [])
        original_notes = original.get("notes")
        rc, out, err, parsed = run_full(fixture_root, checkpoint_path)
        check(cid, "exits 0", rc == 0, (rc, out, err))
        if rc != 0:
            return
        after = load_checkpoint(checkpoint_path)
        after_done_when = after.get("done_when", [])
        after_cases = after.get("cases", [])
        check(cid, "every pre-existing done_when line is present, byte-for-byte, at its original index",
              after_done_when[:len(original_done_when)] == original_done_when,
              (original_done_when, after_done_when))
        check(cid, "at least one new done_when line was appended after the existing ones",
              len(after_done_when) > len(original_done_when), after_done_when)
        check(cid, "every pre-existing case is present unchanged",
              after_cases[:len(original_cases)] == original_cases, (original_cases, after_cases))
        check(cid, "the pre-existing 'notes' field on disk is untouched by the merge",
              after.get("notes") == original_notes, (original_notes, after.get("notes")))
        check(cid, "other top-level keys (files_likely) are untouched by the merge",
              after.get("files_likely") == original.get("files_likely"),
              (original.get("files_likely"), after.get("files_likely")))
    finally:
        cleanup()


# ------------------------------------------------------------------------------------------------ CP7-T13

def t13_running_the_composed_command_twice_adds_nothing_new_the_second_time():
    cid = "CP7-T13"
    fixture_root, checkpoint_path, cleanup = fresh_fixture("m_shaped")
    try:
        rc1, out1, err1, parsed1 = run_full(fixture_root, checkpoint_path)
        check(cid, "first run exits 0", rc1 == 0, (rc1, out1, err1))
        if rc1 != 0:
            return
        after_first = load_checkpoint(checkpoint_path)
        rc2, out2, err2, parsed2 = run_full(fixture_root, checkpoint_path)
        check(cid, "second run exits 0", rc2 == 0, (rc2, out2, err2))
        if rc2 != 0:
            return
        after_second = load_checkpoint(checkpoint_path)
        check(cid, "done_when is identical before and after the second merge (no duplicates added)",
              after_second.get("done_when") == after_first.get("done_when"),
              (after_first.get("done_when"), after_second.get("done_when")))
        check(cid, "cases is identical before and after the second merge (no duplicates added)",
              after_second.get("cases") == after_first.get("cases"),
              (after_first.get("cases"), after_second.get("cases")))
    finally:
        cleanup()


# ------------------------------------------------------------------------------------------------ CP7-X01
# Round-1 adversarial fix (codex-standards/X1): a non-planned case proving the widened M-shaped
# corpus's comprehensive set of runtime monkeypatch forms (bare setattr, direct attribute
# assignment, pytest's monkeypatch.setattr fixture) plus a direct importer of the moved symbol all
# appear in the combined `full` output with their exact file+line, and that their plain-language
# done_when lines survive the merge.

def x01_the_widened_monkeypatch_and_importer_corpus_is_fully_detected():
    cid = "CP7-X01"
    fixture_root, checkpoint_path, cleanup = fresh_fixture("m_shaped")
    try:
        rc, out, err, parsed = run_full(fixture_root, checkpoint_path)
        check(cid, "exits 0", rc == 0, (rc, out, err))
        check(cid, "parses as JSON", parsed is not None, out)
        if parsed is None:
            return
        repoint_findings = findings_for_file(findings_in(parsed, "repoint"), "test_moved_helper.py")
        merged_done_when = (parsed.get("merged") or {}).get("done_when", [])

        expectations = [
            ("setattr-call", "monkeypatch"),
            ("attribute-assignment", "monkeypatch"),
            ("monkeypatch-setattr", "monkeypatch"),
            ("direct-importer", "importer"),
        ]
        for marker, category in expectations:
            expected_line = marker_line(fixture_root / "test_moved_helper.py", marker)
            check(cid, f"fixture carries the {marker!r} marker", expected_line is not None, marker)
            matches = [f for f in repoint_findings
                       if f.get("line") == expected_line and f.get("category") == category]
            check(cid, f"a {category!r} re-point item names test_moved_helper.py at line "
                       f"{expected_line} for the {marker!r} form", bool(matches), repoint_findings)
            if matches:
                expected_done_when_line = (
                    f"Re-point test_moved_helper.py:{expected_line} ({category}): "
                    f"{matches[0]['detail']}"
                )
                check(cid, f"the plain-language done_when line for the {marker!r} form survived "
                           "the merge", expected_done_when_line in merged_done_when, merged_done_when)
    finally:
        cleanup()


# ------------------------------------------------------------------------------------------------ CP7-X02
# Round-1 adversarial fix (codex-standards/X2 + codex-correctness/X1): a non-planned case proving a
# brownfield checkpoint with new_test_file + readme_list but NO inventory field still gets the
# README registration done_when item (and never crashes), even when the new test file turns out to
# scan source so inventory registration itself cannot be decided.

def x02_registration_without_an_inventory_field_still_adds_the_readme_item():
    cid = "CP7-X02"
    fixture_root, checkpoint_path, cleanup = fresh_fixture("m_shaped")
    try:
        checkpoint_data = load_checkpoint(checkpoint_path)
        check(cid, "fixture sanity: the checkpoint carries an inventory field to remove",
              "inventory" in checkpoint_data, checkpoint_data)
        del checkpoint_data["inventory"]
        checkpoint_path.write_text(json.dumps(checkpoint_data))

        rc, out, err, parsed = run_full(fixture_root, checkpoint_path)
        check(cid, "exits 0 (no crash despite the missing inventory field)", rc == 0, (rc, out, err))
        check(cid, "parses as JSON", parsed is not None, out)
        if parsed is None:
            return
        registration = parsed.get("registration") or {}
        items = registration.get("items", [])
        check(cid, "registration still ran (not null) even with no inventory field",
              parsed.get("registration") is not None, parsed.get("registration"))
        check(cid, "the README item is still emitted unconditionally",
              any(i.get("kind") == "readme" and i.get("line") == "python3 test_moved_helper.py"
                  for i in items), items)
        check(cid, "no inventory item is fabricated without an inventory path",
              not any(i.get("kind") == "inventory" for i in items), items)
        merged_done_when = (parsed.get("merged") or {}).get("done_when", [])
        check(cid, "the README registration done_when line survived the merge",
              "Register in the README test list: python3 test_moved_helper.py" in merged_done_when,
              merged_done_when)
    finally:
        cleanup()


# ------------------------------------------------------------------------------------------------ CP7-X03
# Round-2 adversarial fix (codex-correctness/X1): a non-planned case proving a brownfield checkpoint
# with new_test_file present but NO readme_list field still runs registration (not null) and emits a
# manual-check-needed item for the README-registration side instead of silently skipping it, mirroring
# CP7-X02's fail-safe pattern for a missing inventory field.

def x03_registration_without_a_readme_list_field_emits_a_manual_check_needed_item():
    cid = "CP7-X03"
    fixture_root, checkpoint_path, cleanup = fresh_fixture("m_shaped")
    try:
        checkpoint_data = load_checkpoint(checkpoint_path)
        check(cid, "fixture sanity: the checkpoint carries a readme_list field to remove",
              "readme_list" in checkpoint_data, checkpoint_data)
        del checkpoint_data["readme_list"]
        checkpoint_path.write_text(json.dumps(checkpoint_data))

        rc, out, err, parsed = run_full(fixture_root, checkpoint_path)
        check(cid, "exits 0 (no crash despite the missing readme_list field)", rc == 0, (rc, out, err))
        check(cid, "parses as JSON", parsed is not None, out)
        if parsed is None:
            return
        registration = parsed.get("registration") or {}
        items = registration.get("items", [])
        check(cid, "registration still ran (not null) even with no readme_list field",
              parsed.get("registration") is not None, parsed.get("registration"))
        check(cid, "a manual-check-needed item is emitted for the README registration side",
              any(i.get("kind") == "manual-check-needed" for i in items), items)
        check(cid, "no readme item is fabricated without a readme_list path",
              not any(i.get("kind") == "readme" for i in items), items)
        merged_done_when = (parsed.get("merged") or {}).get("done_when", [])
        check(cid, "the manual-check-needed registration line survived the merge",
              any("Manual check needed (registration)" in line for line in merged_done_when),
              merged_done_when)
    finally:
        cleanup()


# ------------------------------------------------------------------------------------------------ CP7-X04
# Round-3 adversarial fix (codex-correctness/X1): a non-planned case proving that when `tests.command`
# combines two report-writing suites, the resulting report-race hazard is not only reported in `full`'s
# own stdout JSON but also actually persisted into the approved checkpoint's done_when list on disk,
# with every pre-existing done_when line still present and in its original order.

def x04_a_two_suite_tests_command_hazard_is_persisted_into_the_approved_checkpoints_done_when():
    cid = "CP7-X04"
    fixture_root, checkpoint_path, cleanup = fresh_fixture("m_shaped")
    try:
        checkpoint_data = load_checkpoint(checkpoint_path)
        original_done_when = list(checkpoint_data.get("done_when", []))
        check(cid, "fixture sanity: the checkpoint starts with at least one done_when line",
              len(original_done_when) > 0, original_done_when)
        checkpoint_data["tests"] = {"command": "python3 test_a.py && python3 test_b.py", "report": "json"}
        checkpoint_path.write_text(json.dumps(checkpoint_data))

        rc, out, err, parsed = run_full(fixture_root, checkpoint_path)
        check(cid, "exits 0", rc == 0, (rc, out, err))
        check(cid, "parses as JSON", parsed is not None, out)
        if parsed is None:
            return
        hazard = parsed.get("hazard") or {}
        check(cid, "hazard is true for a tests.command combining two report-writing suites",
              hazard.get("hazard") is True, hazard)
        check(cid, "both suites are named in the hazard's own suites list",
              hazard.get("suites") == ["test_a.py", "test_b.py"], hazard)

        persisted = load_checkpoint(checkpoint_path)
        persisted_done_when = persisted.get("done_when", [])
        for i, original in enumerate(original_done_when):
            check(cid, f"pre-existing done_when[{i}] is still present and in its original position",
                  len(persisted_done_when) > i and persisted_done_when[i] == original,
                  (i, persisted_done_when[i:i + 1], original))
        hazard_lines = [line for line in persisted_done_when if line.startswith("Report-race hazard:")]
        check(cid, "exactly one persisted done_when line starts with the hazard wording",
              len(hazard_lines) == 1, persisted_done_when)
        if hazard_lines:
            check(cid, "the persisted hazard line names both suites",
                  "test_a.py" in hazard_lines[0] and "test_b.py" in hazard_lines[0], hazard_lines[0])
    finally:
        cleanup()


# ------------------------------------------------------------------------------------------------ CP7-X05
# Round-5 adversarial fix (host-alt-standards/H1): the planner doc's `full` paragraph must warn that
# `full`'s own `merged.done_when` output is raw per-finding lines, not final plan shape, and must tell
# the planner to still write the mechanical detail into `notes` and condense the raw lines into 2-5
# plain-language done_when bullets before submitting the plan -- never copy `merged.done_when` verbatim.
# This reads agents/ratchet-checkpoint-planner.md straight off disk, the same doc-assertion style
# tests/test_impact_analysis_docs.py's CP6 cases use (squash whitespace, split into sentences, assert on
# keyword presence within the `full` paragraph), rather than driving impact_analysis.py as a subprocess.

def x05_the_full_paragraph_warns_its_own_done_when_output_is_not_final_plan_shape():
    cid = "CP7-X05"
    text = PLANNER_DOC.read_text(encoding="utf-8", errors="replace")
    squashed = re.sub(r"\s+", " ", text)
    full_sentences = [s for s in re.split(r"(?<=[.!?])\s+", squashed) if "`full`" in s or "full <fixture_root>" in s]
    check(cid, "the planner doc still mentions the `full` shortcut subcommand at all",
          bool(full_sentences), "no sentence mentioning `full` found")
    warns_not_final = [s for s in full_sentences
                        if re.search(r"\bnot\b", s, re.I)
                        and re.search(r"final|verbatim", s, re.I)]
    check(cid, "a `full`-related sentence says its done_when output is NOT final plan shape / must not be "
               "copied verbatim",
          bool(warns_not_final), f"got {full_sentences[:3]}")
    condense_sentences = [s for s in full_sentences
                           if re.search(r"condense", s, re.I)
                           and re.search(r"done_when", s, re.I)
                           and re.search(r"notes", s, re.I)]
    check(cid, "a `full`-related sentence tells the planner to condense the raw lines into done_when bullets "
               "while still writing the mechanical detail into notes",
          bool(condense_sentences), f"got {full_sentences[:3]}")
    check(cid, "PLANNER_DOC module-level path constant is available for this doc-assertion test",
          PLANNER_DOC.exists(), PLANNER_DOC)


TESTS = [
    ("CP7-T01", t01_one_command_runs_the_whole_pass_on_the_m_shaped_fixture),
    ("CP7-T02", t02_m_shaped_fixture_is_classified_brownfield_not_greenfield),
    ("CP7-T03", t03_the_hidden_monkeypatch_repoint_site_is_in_the_combined_output),
    ("CP7-T04", t04_the_stale_source_scan_site_is_found_even_though_its_name_hides_the_move),
    ("CP7-T05", t05_readme_and_inventory_registration_items_appear_for_the_new_test_file),
    ("CP7-T06", t06_startup_and_behavior_parity_items_are_added_even_though_never_asked_for),
    ("CP7-T07", t07_all_four_item_classes_come_back_before_any_implementation_code_exists),
    ("CP7-T08", t08_a_single_suite_tests_command_is_never_flagged_as_a_report_race_hazard),
    ("CP7-T09", t09_greenfield_fixture_is_classified_greenfield_create),
    ("CP7-T10", t10_greenfield_fixture_gets_zero_repoint_items),
    ("CP7-T11", t11_greenfield_fixture_plan_stays_clean_of_parity_and_hazard_noise_too),
    ("CP7-T12", t12_the_composed_pass_only_adds_to_the_m_shaped_fixtures_existing_checkpoint_json),
    ("CP7-T13", t13_running_the_composed_command_twice_adds_nothing_new_the_second_time),
    ("CP7-X01", x01_the_widened_monkeypatch_and_importer_corpus_is_fully_detected),
    ("CP7-X02", x02_registration_without_an_inventory_field_still_adds_the_readme_item),
    ("CP7-X03", x03_registration_without_a_readme_list_field_emits_a_manual_check_needed_item),
    ("CP7-X04", x04_a_two_suite_tests_command_hazard_is_persisted_into_the_approved_checkpoints_done_when),
    ("CP7-X05", x05_the_full_paragraph_warns_its_own_done_when_output_is_not_final_plan_shape),
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
            print(f"  FAIL [{cid}] CRASHED: {e}")
            traceback.print_exc()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
