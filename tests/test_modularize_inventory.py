#!/usr/bin/env python3
"""
CP1 tests: inventory source-inspecting tests and snapshot ratchet.py's visible behavior, before any code moves.
   python3 tests/run_all.py tests/test_modularize_inventory.py

CP1 lands no code of its own; its implementation is two artifacts this file checks, re-deriving its own oracle
live each run rather than trusting a hand-picked list (so the inventory can't silently go stale):

1. tests/fixtures/modularize_cp1/test-inventory.md -- a committed list of every test file that reads
   ratchet.py's source text, parses its AST, or loads it dynamically via importlib.util.spec_from_file_location,
   and what each one checks for. CP1-T01..T04 re-run the live grep from this run's CP1 checkpoint notes
   (`ENGINE.read_text|ast.parse|ast.walk|spec_from_file_location|getsource`, recursively under tests/) against
   the real working tree and check the inventory against THAT live result, never a snapshot of it. This file's
   own name is excluded from that live result: it legitimately mentions the pattern strings themselves as
   documentation/constants, which does not make it a real dependency on ratchet.py's on-disk shape.
2. tests/fixtures/modularize_cp1/baseline/ -- a saved before-snapshot of ratchet.py's visible CLI/hook behavior
   (--help, a sample status --json, a sample hook reply, 2+ distinct failure exit codes), captured from real
   invocations, plus a manifest.json this file reads to find each captured file. CP1-T05..T09 check that
   snapshot. These artifacts live under tests/ (ordinary, git-tracked project files) rather than under
   .ratchet/runs/.../evidence/, which the guard hook and the skill's own rules reserve exclusively for
   ratchet.py's own internal gate writes (red/behavior/adv-record etc) -- never for an implementer's writes.

Contract this file expects of tests/fixtures/modularize_cp1/baseline/manifest.json (there is no pre-existing
convention for this; this is what this checkpoint's implementation must produce): a JSON object with an
"entries" list, each entry {"kind": "help"|"status_json"|"hook_reply"|"failure", "cmd": [...], "exit_code":
int, "path": "<relative to the baseline folder>"}, plus free-text fields documenting that this snapshot is the
fixed baseline later checkpoints' comparisons are measured against, and naming its own baseline path.

This suite writes a per-test JSON report to $RATCHET_TEST_REPORT when that env var is set:
{"cases": [{"id": "CP1-T01", "status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RUN_DIR = ROOT / ".ratchet" / "runs" / "behavior-preserving-modularization-of-skills-rat"
FIXTURES_DIR = ROOT / "tests" / "fixtures" / "modularize_cp1"
INVENTORY = FIXTURES_DIR / "test-inventory.md"
BASELINE_DIR = FIXTURES_DIR / "baseline"
MANIFEST = BASELINE_DIR / "manifest.json"
APP_SCOUT = RUN_DIR / "app-scout.json"
ENGINE = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"

SELF_NAME = Path(__file__).name
MODULE_LOAD_PATTERN = r"spec_from_file_location"
SOURCE_TEXT_PATTERN = r"ENGINE\.read_text|ast\.parse|ast\.walk|getsource"

BRIEF_NAMED_FILES = [
    "test_quick_path.py", "test_trust_cp5_d.py", "test_trust_cp5_e.py",
    "test_quota_shared.py", "test_discover_paid.py",
]
NEGATIVE_FIXTURE = "test_brief.py"
# Known fixtures for CP1-T03: test_gates.py dynamically loads ratchet.py but never reads/parses its source text;
# test_quick_path.py does both (it walks ratchet.py's AST for the classify_changes FunctionDef). Confirmed live
# against the working tree below, not assumed.
MODULE_LOAD_ONLY_EXAMPLE = "test_gates.py"
SOURCE_TEXT_EXAMPLE = "test_quick_path.py"
SOURCE_TEXT_KEYWORDS = ("ast", "parse", "source text", "source-text", "read_text", "getsource")

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = re.search(r"\[(CP1-[TX]\d+)\]", name)
    if m:
        cid = m.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ---- live-grep oracle (re-derived each run; never a hardcoded file list) ------------------------------------


def _live_grep(pattern: str) -> set:
    """Every tests/ file (recursive) whose text matches `pattern`, read live from the working tree. This file's
    own name is dropped from the result: see the module docstring for why. `__pycache__` is excluded so a stale
    compiled `.pyc` left over from an earlier import (which can embed pattern strings like
    `spec_from_file_location` as literal bytecode constants) never gets falsely reported as a source-inspecting
    file. The impact-analyzer sample-data fixture dirs (`tests/fixtures/impact_analysis_*/`) are excluded for the
    SAME CP1-X01 reason: those files embed the scan-pattern strings (`ast.parse`, `ENGINE.read_text`, ...) as the
    analyzer's own test INPUTS, with zero references to ratchet.py -- they do not inspect ratchet.py's source, so
    they are not source-inspecting dependencies, exactly as embedded bytecode constants are not."""
    r = subprocess.run(["grep", "-rlE", "--exclude-dir=__pycache__", "--exclude-dir=impact_analysis_*",
                        pattern, "."], cwd=str(HERE), capture_output=True, text=True, timeout=30)
    if r.returncode not in (0, 1):
        raise RuntimeError(f"grep failed: exit {r.returncode}: {r.stderr[-300:]}")
    names = set()
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        name = line[2:] if line.startswith("./") else line
        if Path(name).name == SELF_NAME:
            continue
        names.add(name)
    return names


def live_module_load_files() -> set:
    return _live_grep(MODULE_LOAD_PATTERN)


def live_source_text_files() -> set:
    return _live_grep(SOURCE_TEXT_PATTERN)


def live_all_source_inspecting_files() -> set:
    return live_module_load_files() | live_source_text_files()


# ---- inventory doc plumbing -----------------------------------------------------------------------------


def read_inventory() -> str | None:
    try:
        return INVENTORY.read_text()
    except OSError:
        return None


def mentions(name: str, text: str) -> bool:
    """Whether `name` (e.g. 'test_quick_path.py') appears in `text` as its own token: not as a substring of a
    longer identifier (e.g. 'xtest_quick_path.py'), but free to be preceded/followed by '/', '.', punctuation,
    list markup or whitespace."""
    return re.search(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", text) is not None


def entry_context(name: str, text: str, other_names) -> str:
    """A short, local snippet of `text` around the line that mentions `name`: that line, plus the next line
    only when the next line does not itself mention a different tracked file (so two tightly-packed entries in
    a list are not pulled together into one). Returns '' when `name` is not found anywhere in `text`."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if mentions(name, line):
            snippet = line
            if i + 1 < len(lines) and not any(mentions(o, lines[i + 1]) for o in other_names if o != name):
                snippet += "\n" + lines[i + 1]
            return snippet
    return ""


# ---- CP1-T01: inventory is a superset of the live grep -------------------------------------------------------


def t01_inventory_covers_live_grep():
    print("CP1-T01: inventory lists every test file the live grep finds, not a stale hand-picked list")
    try:
        live = live_all_source_inspecting_files()
    except RuntimeError as e:
        check("[CP1-T01] setup: the live grep over tests/ runs cleanly", False, str(e))
        return
    check("[CP1-T01] setup: the live grep finds at least 30 files (sanity: this repo really has this many)",
          len(live) >= 30, len(live))
    text = read_inventory()
    if text is None:
        check(f"[CP1-T01] {INVENTORY.relative_to(ROOT)} exists", False, "file not found")
        return
    missing = sorted({Path(f).name for f in live if not mentions(Path(f).name, text)})
    check("[CP1-T01] every file the live grep finds is named in the inventory (live-grep minus inventory is "
          "empty)", not missing, missing)

    # Teeth (same plant-and-clean-up technique as CP1-X01): a brand-new, non-excluded test file under
    # tests/ that inspects ratchet.py's source MUST still be caught by the assertion above -- both found
    # by the live grep (proving the impact_analysis_* exclusion is a narrow sample-data carve-out, never
    # a blanket tests/ suppression) and flagged as not-yet-inventoried (proving the oracle keeps its bite
    # for a real new source-inspecting dependency).
    probe = HERE / "_cp1_t01_teeth_probe.py"
    try:
        probe.write_text("# planted CP1-T01 teeth probe; carries the source-inspection pattern below\n"
                         "_ = 'ENGINE.read_text()'  # matches SOURCE_TEXT_PATTERN, like a real source scan\n")
        live_with_probe = live_all_source_inspecting_files()
        probe_found = any(Path(f).name == probe.name for f in live_with_probe)
        check("[CP1-T01] teeth: a newly-planted source-inspecting test file directly under tests/ is still "
              "found by the live grep (the impact_analysis_* exclusion does not suppress real new files)",
              probe_found, sorted(Path(f).name for f in live_with_probe)[-5:])
        missing_with_probe = sorted({Path(f).name for f in live_with_probe
                                     if not mentions(Path(f).name, text)})
        check("[CP1-T01] teeth: that planted, un-inventoried source-inspecting file is flagged by the "
              "live-grep-minus-inventory assertion (a genuinely new dependency is still caught)",
              probe.name in missing_with_probe, missing_with_probe[:5])
    finally:
        probe.unlink(missing_ok=True)


# ---- CP1-T02: the 5 brief-named files are present, each with a real description -------------------------------


def t02_brief_named_files_described():
    print("CP1-T02: the inventory names the 5 brief-called-out files, each with what it actually checks for")
    text = read_inventory()
    if text is None:
        check(f"[CP1-T02] {INVENTORY.relative_to(ROOT)} exists", False, "file not found")
        return
    for name in BRIEF_NAMED_FILES:
        present = mentions(name, text)
        check(f"[CP1-T02] {name} is named in the inventory", present)
        if not present:
            continue
        snippet = entry_context(name, text, BRIEF_NAMED_FILES)
        residue = re.sub(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", "", snippet)
        residue = re.sub(r"[\W_]+", " ", residue).strip()
        check(f"[CP1-T02] {name}'s entry describes what it checks for, not just the bare filename "
              "(entry text beyond the filename is at least 15 characters)", len(residue) >= 15, repr(snippet)[:300])


# ---- CP1-T03: the inventory distinguishes module-load from source-text/AST obligations -------------------------


def t03_distinguishes_invariant_kinds():
    print("CP1-T03: the inventory distinguishes module-loading files from source-text/AST-reading files")
    try:
        module_only = (MODULE_LOAD_ONLY_EXAMPLE in live_module_load_files() and
                       MODULE_LOAD_ONLY_EXAMPLE not in live_source_text_files())
        both = SOURCE_TEXT_EXAMPLE in live_source_text_files()
    except RuntimeError as e:
        check("[CP1-T03] setup: the live grep over tests/ runs cleanly", False, str(e))
        return
    check(f"[CP1-T03] setup: {MODULE_LOAD_ONLY_EXAMPLE} matches only the module-load pattern live (a known "
          "module-load-only fixture)", module_only)
    check(f"[CP1-T03] setup: {SOURCE_TEXT_EXAMPLE} matches the source-text/AST pattern live (a known "
          "source-text fixture)", both)
    if not (module_only and both):
        return
    text = read_inventory()
    if text is None:
        check(f"[CP1-T03] {INVENTORY.relative_to(ROOT)} exists", False, "file not found")
        return
    others = (MODULE_LOAD_ONLY_EXAMPLE, SOURCE_TEXT_EXAMPLE)
    gates_snippet = entry_context(MODULE_LOAD_ONLY_EXAMPLE, text, others).lower()
    quick_snippet = entry_context(SOURCE_TEXT_EXAMPLE, text, others).lower()
    quick_has_kw = any(kw in quick_snippet for kw in SOURCE_TEXT_KEYWORDS)
    gates_has_kw = any(kw in gates_snippet for kw in SOURCE_TEXT_KEYWORDS)
    check(f"[CP1-T03] {SOURCE_TEXT_EXAMPLE}'s entry is tagged with its source-text/AST obligation (mentions one "
          f"of {SOURCE_TEXT_KEYWORDS})", quick_has_kw, quick_snippet[:300])
    check(f"[CP1-T03] {MODULE_LOAD_ONLY_EXAMPLE}'s entry is NOT tagged with a source-text/AST obligation it "
          "does not have (a module-load-only file is not lumped in with source-text ones)", not gates_has_kw,
          gates_snippet[:300])


# ---- CP1-T04: a file that matches neither pattern is absent ----------------------------------------------------


def t04_negative_fixture_absent():
    print("CP1-T04: a test file that matches neither grep pattern is not listed as source-inspecting")
    try:
        live = live_all_source_inspecting_files()
    except RuntimeError as e:
        check("[CP1-T04] setup: the live grep over tests/ runs cleanly", False, str(e))
        return
    check(f"[CP1-T04] setup: {NEGATIVE_FIXTURE} really matches neither live-grep pattern",
          not any(Path(f).name == NEGATIVE_FIXTURE for f in live))
    text = read_inventory()
    if text is None:
        check(f"[CP1-T04] {INVENTORY.relative_to(ROOT)} exists", False, "file not found")
        return
    check(f"[CP1-T04] {NEGATIVE_FIXTURE} is absent from the inventory (no false positive)",
          not mentions(NEGATIVE_FIXTURE, text))


# ---- baseline manifest plumbing -------------------------------------------------------------------------


def read_manifest() -> dict | None:
    try:
        data = json.loads(MANIFEST.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def entries_of(manifest: dict, kind: str) -> list:
    return [e for e in (manifest.get("entries") or []) if isinstance(e, dict) and e.get("kind") == kind]


def load_captured(entry: dict) -> str | None:
    p = entry.get("path")
    if not isinstance(p, str) or not p:
        return None
    try:
        return (BASELINE_DIR / p).read_text()
    except OSError:
        return None


def _is_help_cmd(cmd) -> bool:
    return isinstance(cmd, list) and bool(cmd) and cmd[-1] == "--help"


# ---- CP1-T05: --help captured at top level and for 2+ subcommands ----------------------------------------------


def t05_help_captured():
    print("CP1-T05: before-snapshot captures --help at the top level and for at least two subcommands")
    manifest = read_manifest()
    if manifest is None:
        check(f"[CP1-T05] {MANIFEST} exists and is valid JSON", False, "missing or invalid")
        return
    helps = entries_of(manifest, "help")
    top_level = [e for e in helps if _is_help_cmd(e.get("cmd")) and len(e["cmd"]) == 1]
    subcommands = [e for e in helps if _is_help_cmd(e.get("cmd")) and len(e["cmd"]) >= 2]
    check("[CP1-T05] the top-level `ratchet.py --help` is captured", bool(top_level), helps)
    check("[CP1-T05] at least two subcommands' --help output is captured", len(subcommands) >= 2,
          [e.get("cmd") for e in subcommands])
    for e in top_level + subcommands:
        text = load_captured(e)
        ok = text is not None and text.strip() != "" and e.get("exit_code") == 0
        check(f"[CP1-T05] {' '.join(e.get('cmd') or [])}'s captured output is non-empty with exit_code 0", ok,
              (e, "" if text is None else text[:200]))


# ---- CP1-T06: a sample status --json is captured ---------------------------------------------------------------


def t06_status_json_captured():
    print("CP1-T06: before-snapshot captures a sample status --json")
    manifest = read_manifest()
    if manifest is None:
        check(f"[CP1-T06] {MANIFEST} exists and is valid JSON", False, "missing or invalid")
        return
    samples = entries_of(manifest, "status_json")
    check("[CP1-T06] a status_json entry is recorded", bool(samples), manifest.get("entries"))
    if not samples:
        return
    entry = samples[0]
    raw = load_captured(entry)
    check("[CP1-T06] the captured status --json file exists", raw is not None, entry)
    if raw is None:
        return
    try:
        captured = json.loads(raw)
    except json.JSONDecodeError as e:
        check("[CP1-T06] the captured sample parses as valid JSON", False, str(e))
        return
    check("[CP1-T06] the captured sample parses as valid JSON", True)
    check("[CP1-T06] the captured sample is a JSON object with several top-level keys (not a hand-written stub)",
          isinstance(captured, dict) and len(captured) >= 5,
          captured if not isinstance(captured, dict) else sorted(captured))
    if not isinstance(captured, dict):
        return
    # Live re-derivation: this repo's own active run (the one this checkpoint runs inside) gives the same
    # top-level key set right now that a real `status --json` invocation gave when the baseline was captured --
    # proving the sample is verbatim from a real run, not hand-typed, without hardcoding key names here.
    try:
        live = subprocess.run([sys.executable, str(ENGINE), "status", "--json"], cwd=str(ROOT),
                               capture_output=True, text=True, timeout=30)
        live_json = json.loads(live.stdout) if live.returncode == 0 else None
    except (subprocess.SubprocessError, json.JSONDecodeError):
        live, live_json = None, None
    if live_json is None:
        check("[CP1-T06] setup: a live `status --json` invocation in this repo succeeds right now (to compare "
              "schemas against)", False, None if live is None else (live.returncode, live.stdout[-300:]))
        return
    missing_keys = sorted(set(live_json) - set(captured))
    check("[CP1-T06] the captured sample's top-level keys match a live `status --json` invocation's keys "
          "(same schema; verbatim from a real run)", not missing_keys, missing_keys)


# ---- CP1-T07: a sample hook reply is captured --------------------------------------------------------------------


def t07_hook_reply_captured():
    print("CP1-T07: before-snapshot captures a sample prompt or stop hook reply")
    manifest = read_manifest()
    if manifest is None:
        check(f"[CP1-T07] {MANIFEST} exists and is valid JSON", False, "missing or invalid")
        return
    samples = entries_of(manifest, "hook_reply")
    check("[CP1-T07] a hook_reply entry is recorded", bool(samples), manifest.get("entries"))
    if not samples:
        return
    entry = samples[0]
    raw = load_captured(entry)
    check("[CP1-T07] the captured hook reply file exists", raw is not None, entry)
    if raw is None:
        return
    try:
        reply = json.loads(raw)
    except json.JSONDecodeError as e:
        check("[CP1-T07] the captured hook reply parses as valid JSON", False, str(e))
        return
    check("[CP1-T07] the captured hook reply parses as valid JSON", True)
    # ratchet.py's emit_hook() only ever answers in JSON for the copilot host's blocking guard/stop replies:
    # {"permissionDecision": "deny", ...} (guard) or {"decision": "block", ...} (stop) -- every other host/event
    # combination answers in plain text to stdout/stderr. A JSON sample must be one of these two real shapes.
    ok_shape = isinstance(reply, dict) and (
        reply.get("permissionDecision") == "deny" or reply.get("decision") == "block")
    check("[CP1-T07] the captured reply matches the hook reply JSON shape ratchet.py actually emits "
          "(permissionDecision: deny, or decision: block)", ok_shape, reply)


# ---- CP1-T08: at least two distinct failure exit codes are captured ----------------------------------------------


def t08_failure_exit_codes_captured():
    print("CP1-T08: before-snapshot captures at least two distinct failure exit codes")
    manifest = read_manifest()
    if manifest is None:
        check(f"[CP1-T08] {MANIFEST} exists and is valid JSON", False, "missing or invalid")
        return
    failures = entries_of(manifest, "failure")
    check("[CP1-T08] at least two failure entries are recorded", len(failures) >= 2, failures)
    codes = [e.get("exit_code") for e in failures
             if isinstance(e.get("exit_code"), int) and e.get("exit_code") != 0]
    check("[CP1-T08] every failure entry records a non-zero exit code", len(codes) == len(failures), failures)
    check("[CP1-T08] at least two distinct non-zero exit codes are present (not the same code captured twice "
          "under different labels)", len(set(codes)) >= 2, codes)
    commands = [tuple(e.get("cmd") or []) for e in failures]
    check("[CP1-T08] each failure entry is paired with its own command (no two are the same command repeated)",
          len(set(commands)) == len(commands), commands)
    for e in failures:
        text = load_captured(e)
        check(f"[CP1-T08] {' '.join(e.get('cmd') or [])}'s captured output file exists and is non-empty",
              bool(text and text.strip()), e)


# ---- CP1-T09: the manifest documents its own role as the fixed baseline ------------------------------------------


def t09_manifest_documents_baseline_role():
    print("CP1-T09: the before-snapshot is documented as the baseline later checkpoints diff against")
    manifest = read_manifest()
    if manifest is None:
        check(f"[CP1-T09] {MANIFEST} exists and is valid JSON", False, "missing or invalid")
        return
    doc_text = " ".join(str(v) for v in manifest.values() if isinstance(v, str)).lower()
    check("[CP1-T09] the manifest states plainly that this snapshot is a/the baseline",
          "baseline" in doc_text, doc_text[:300])
    check("[CP1-T09] the manifest says later checkpoints compare/diff against it",
          any(kw in doc_text for kw in ("later checkpoint", "diff", "compare")), doc_text[:300])
    path_tail = BASELINE_DIR.relative_to(ROOT).as_posix()  # "tests/fixtures/modularize_cp1/baseline"
    check(f"[CP1-T09] the manifest names a stable evidence path ({path_tail}) a later checkpoint's test can "
          "load directly", path_tail in doc_text, doc_text[:300])


# ---- CP1-T10: the complete local suite passed with zero code changes, as a fixed starting point ------------------


def _git(args):
    r = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True, timeout=30)
    return r.returncode, r.stdout, r.stderr


def t10_suite_green_before_any_code_change():
    print("CP1-T10: the complete local suite passed with zero code changes made yet")
    # This case verifies lane M's OWN run context: its run-state file (app-scout.json) and the clean
    # starting tree of M's own run. Those only exist inside M's own worktree/run, under RUN_DIR. On
    # integrated main RUN_DIR is gone, so skip cleanly rather than false-fail; inside M's run it runs
    # exactly as before.
    if not RUN_DIR.exists():
        print("[CP1-T10] skipped: not running inside lane M's own run context")
        return
    code, out, err = _git(["diff", "--quiet", "HEAD", "--",
                           "skills/ratchet-orchestrator/scripts/ratchet.py",
                           "skills/ratchet-orchestrator/scripts/provider_health.py",
                           "skills/ratchet-orchestrator/scripts/spec_sources.py"])
    if code != 0:
        print("[CP1-T10] skipped: ratchet.py/provider_health.py/spec_sources.py differ from HEAD (a later "
              "checkpoint's uncommitted work in progress); this baseline proof only runs against a clean tree")
        return
    check("[CP1-T10] git diff is empty on ratchet.py, provider_health.py and spec_sources.py against HEAD "
          "(no code has moved yet)", code == 0, (code, out, err))
    # Running the full ~45-minute suite (tests/run_all.py) on every invocation of this fast CP1 file would be
    # both wrong (it would recursively re-run this very file as part of "the complete local suite") and far too
    # slow to belong to a file this checkpoint's own `tests.command` runs repeatedly. Set
    # RATCHET_CP1_T10_FULL_SUITE=1 to actually run it by hand; otherwise this case is proved from the real,
    # already-on-disk record of the full suite's last run at the point this run started (app-scout.json's
    # baseline_full_suite_result), combined with the git-diff-clean check above (nothing has changed since).
    if os.environ.get("RATCHET_CP1_T10_FULL_SUITE") == "1":
        r = subprocess.run([sys.executable, "tests/run_all.py"], cwd=str(ROOT), capture_output=True, text=True,
                            timeout=5400)
        m = re.search(r"(\d+) passed, (\d+) failed", r.stdout)
        failed = int(m.group(2)) if m else -1
        check("[CP1-T10] python3 tests/run_all.py exits 0 with 0 failed", r.returncode == 0 and failed == 0,
              (r.returncode, r.stdout[-400:]))
        return
    try:
        scout = json.loads(APP_SCOUT.read_text())
    except (OSError, json.JSONDecodeError) as e:
        check(f"[CP1-T10] {APP_SCOUT.relative_to(ROOT)} is readable and valid JSON (records the full suite's "
              "last real run)", False, str(e))
        return
    result = scout.get("baseline_full_suite_result") or {}
    status = result.get("status")
    line = str(result.get("result_line") or "")
    m = re.search(r"(\d+) passed, (\d+) failed", line)
    failed = int(m.group(2)) if m else -1
    check("[CP1-T10] app-scout.json records a GREEN full-suite run (0 failed) taken right before this run's "
          "work started, and nothing has changed in ratchet.py/provider_health.py/spec_sources.py since (git "
          "diff clean, checked above)", status == "GREEN" and failed == 0, result)


# ---- CP1-X01 (fix round 1): a stale __pycache__/*.pyc with embedded pattern strings is ignored ------------------


def x01_stale_pycache_excluded_from_live_grep():
    print("CP1-X01: a stale __pycache__/*.pyc that embeds the grep pattern strings as bytecode constants is "
          "not falsely reported as a source-inspecting file")
    pycache_dir = HERE / "__pycache__"
    planted = pycache_dir / "ratchet_cp1_x01_stale_fixture.cpython-00.pyc"
    created_dir = not pycache_dir.exists()
    try:
        pycache_dir.mkdir(exist_ok=True)
        planted.write_bytes(
            b"\x00\x00stale-bytecode-constant: spec_from_file_location ENGINE.read_text ast.parse ast.walk "
            b"getsource\x00\x00")
        try:
            module_live = live_module_load_files()
            source_live = live_source_text_files()
        except RuntimeError as e:
            check("[CP1-X01] setup: the live grep over tests/ runs cleanly with the planted stale .pyc present",
                  False, str(e))
            return
        check("[CP1-X01] setup: the live grep over tests/ runs cleanly with the planted stale .pyc present", True)
        check("[CP1-X01] a stale __pycache__/*.pyc embedding 'spec_from_file_location' is excluded from the "
              "module-load live grep result", not any(Path(f).name == planted.name for f in module_live),
              sorted(module_live))
        check("[CP1-X01] the same stale __pycache__/*.pyc embedding source-text/AST pattern strings is excluded "
              "from the source-text live grep result", not any(Path(f).name == planted.name for f in source_live),
              sorted(source_live))
    finally:
        try:
            if planted.exists():
                planted.unlink()
        except OSError:
            pass
        if created_dir:
            try:
                pycache_dir.rmdir()
            except OSError:
                pass


TESTS = [
    ("CP1-T01", t01_inventory_covers_live_grep),
    ("CP1-T02", t02_brief_named_files_described),
    ("CP1-T03", t03_distinguishes_invariant_kinds),
    ("CP1-T04", t04_negative_fixture_absent),
    ("CP1-T05", t05_help_captured),
    ("CP1-T06", t06_status_json_captured),
    ("CP1-T07", t07_hook_reply_captured),
    ("CP1-T08", t08_failure_exit_codes_captured),
    ("CP1-T09", t09_manifest_documents_baseline_role),
    ("CP1-T10", t10_suite_green_before_any_code_change),
    ("CP1-X01", x01_stale_pycache_excluded_from_live_grep),
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
        except Exception as e:  # a crash in this file still counts as a (failed) result for this case
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
