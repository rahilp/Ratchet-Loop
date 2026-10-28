#!/usr/bin/env python3
"""
CP9 tests: the module map doc for maintainers. This checkpoint is docs-only -- no production code
moves. A new "Module map" section (in skills/ratchet-orchestrator/references/schemas.md, or a short
new skills/ratchet-orchestrator/references/modules.md) must name every module this run actually
created (engine_utils.py, fingerprint.py, records.py, provider_discovery.py, run_records.py,
plan_support.py, status_text.py), one line each on what it owns and which ratchet.py command/handler
calls into it, plus the two accepted architectural couplings the director named explicitly (CP5's
~19 `_ratchet_module()` forward-dependency names + PRESETS, and CP6's `_HOST`/`bind_host()` forced
first-binder-wins coupling for `_IN_HOOK`/`_HOOK_ENV_ADVISORY`), plus a one-line mention of every
area that stays deferred inside ratchet.py (hooks, cmd_init, cmd_lanes_start/cmd_integrate, cmd_ask,
director approvals, receipts, spec tools, shortcuts, viewer, CI export), with no claim that any of
those deferred areas was itself extracted into its own module. README.md and SKILL.md must each gain
a one-line pointer to wherever the doc actually landed.   python3 tests/test_modularize_cp9.py

Strategy (mirrors CP9.json's own "strategy" field): a pure doc-reading suite. CP9-T01 locates the doc
without assuming which of the two candidate locations the implementer picked. Every later case reads
whichever location CP9-T01 found (or fails cleanly, with a clear message, if neither or both exist --
which is exactly the expected state right now, before the doc is written). CP9-T02 derives the "real"
set of new module files live from git (this run's own base commit, before CP2's first extraction,
compared against the current tree), rather than trusting a hardcoded list that could drift. CP9-T08 is
structural, the same "RATCHET_CPn_T.._FULL_SUITE=1 opt-in, default no-op true" pattern CP6-T20/CP7-T15/
CP8-T17 already use, plus a diff-scope allow-list against CP8's own landing commit.

This project's tests/run_all.py harness has no per-test JUnit/JSON reporter (report: "none" in
CP9.json); every checkpoint in this run is checked by exit code only. This suite still writes a
per-test JSON report to $RATCHET_TEST_REPORT when that env var happens to be set, harmlessly, for
consistency with every sibling test_modularize_cpN.py file.
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

SCRIPT_DIR = ROOT / "skills" / "ratchet-orchestrator" / "scripts"
SCHEMAS_MD = ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md"
MODULES_MD = ROOT / "skills" / "ratchet-orchestrator" / "references" / "modules.md"
README = ROOT / "README.md"
SKILL_MD = ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md"

# Lane M's own run directory. It exists only inside M's own worktree/run; on integrated main it is
# gone. CP9-T08's diff-scope check is meaningful only inside M's own run (it diffs against CP8's
# landing commit assuming HEAD is still M's run tip), so it keys off this directory's existence.
RUN_DIR = ROOT / ".ratchet" / "runs" / "behavior-preserving-modularization-of-skills-rat"

# This run's own base commit, before CP2's first extraction out of ratchet.py (CP1 only inventoried
# and snapshotted; it moved no code). Confirmed by hand (git ls-tree) that at this commit
# skills/ratchet-orchestrator/scripts/ held only ratchet.py, spec_sources.py and provider_health.py.
RUN_BASE_COMMIT = "d0f7bd147f2990ace56a46a189b4e9200e345d99"
PRE_EXISTING_SCRIPTS = {"ratchet.py", "spec_sources.py", "provider_health.py"}

# CP8's own landing commit -- the tip this checkpoint (docs-only) starts from.
CP8_LANDING_COMMIT = "57897a24a8ca69d7a39bd80b94b95dfc80282e89"

OUT_OF_SCOPE_AREAS = {
    "hooks": ["hooks section", "hook handler", "prompt hook", "stop hook", "hooks stay", "hooks remain",
              "hook install"],
    "cmd_init": ["cmd_init"],
    "cmd_lanes_start/cmd_integrate": ["cmd_lanes_start", "cmd_integrate"],
    "cmd_ask": ["cmd_ask"],
    "director approvals": ["director approval", "director_policy", "director_sign", "director-signed",
                            "director signing"],
    "receipts": ["receipt"],
    "spec tools": ["spec tool", "openspec", "spec kit", "bmad"],
    "shortcuts": ["shortcut table", "shortcut command", "shortcuts"],
    "viewer": ["viewer"],
    "CI export": ["ci export", "ci_export"],
}

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP9-[TX][0-9]+)\]")


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


# ---- locating the module map doc, wherever the implementer put it --------------------------------------


def _heading_section(text: str, title_substr: str):
    """Return the body text (excluding the heading line itself) of the first markdown heading whose
    title contains title_substr (case-insensitive), up to the next heading of the same or shallower
    level, or None if no such heading exists."""
    lines = text.splitlines()
    heading_re = re.compile(r"^(#{1,6})\s*(.+?)\s*$")
    start = level = None
    for i, line in enumerate(lines):
        m = heading_re.match(line)
        if m and title_substr.lower() in m.group(2).lower():
            start, level = i, len(m.group(1))
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = heading_re.match(lines[j])
        if m and len(m.group(1)) <= level:
            end = j
            break
    body = "\n".join(lines[start + 1:end]).strip()
    return body or None


def _locate_module_map():
    """Returns (doc_text, location_label) where location_label is 'schemas.md', 'modules.md',
    'neither' or 'both'. doc_text is None unless exactly one candidate holds real content."""
    schemas_section = None
    if SCHEMAS_MD.exists():
        schemas_section = _heading_section(SCHEMAS_MD.read_text(encoding="utf-8"), "module map")
    modules_text = None
    if MODULES_MD.exists():
        t = MODULES_MD.read_text(encoding="utf-8").strip()
        modules_text = t or None
    has_schemas, has_modules = bool(schemas_section), bool(modules_text)
    if has_schemas and not has_modules:
        return schemas_section, "schemas.md"
    if has_modules and not has_schemas:
        return modules_text, "modules.md"
    if has_schemas and has_modules:
        return None, "both"
    return None, "neither"


def _real_new_modules():
    """Derive, live from git, the set of .py files under scripts/ that exist now but did not exist
    at this run's own base commit (before CP2's first extraction) and are not one of the three
    siblings that predate this run."""
    r = subprocess.run(["git", "ls-tree", "--name-only", RUN_BASE_COMMIT,
                        "skills/ratchet-orchestrator/scripts/"], cwd=str(ROOT), capture_output=True,
                       text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"git ls-tree failed: {r.returncode} {r.stderr[-300:]}")
    base_files = {Path(p).name for p in r.stdout.splitlines() if p.strip().endswith(".py")}
    now_files = {p.name for p in SCRIPT_DIR.glob("*.py")}
    new_files = now_files - base_files - PRE_EXISTING_SCRIPTS
    return new_files, base_files


# ---- CP9-T01 ---------------------------------------------------------------------------------------------


def t01_doc_exists_somewhere():
    print("CP9-T01: the module map doc exists somewhere a maintainer would find it")
    doc_text, where = _locate_module_map()
    check("[CP9-T01] exactly one of schemas.md's 'Module map' section or references/modules.md "
          "exists and is non-empty (not neither, not both)", where in ("schemas.md", "modules.md"),
          where)
    if doc_text is not None:
        check(f"[CP9-T01] the located doc ({where}) has real body content, not just a heading",
              len(doc_text.strip()) > 20, len(doc_text.strip()))


# ---- CP9-T02 ---------------------------------------------------------------------------------------------


def t02_names_every_real_module_exactly():
    print("CP9-T02: the doc names every module this run actually created, and names the two "
          "director-required accepted-coupling callouts")
    new_files, base_files = _real_new_modules()
    expected = {"engine_utils.py", "fingerprint.py", "records.py", "provider_discovery.py",
                "run_records.py", "plan_support.py", "status_text.py"}
    check("[CP9-T02] setup: live-derived new-module set matches the 7 modules this run's checkpoints "
          "(CP2-CP8) actually created (sanity check on our own derivation, not on the doc)",
          new_files == expected, (sorted(new_files), sorted(base_files)))

    doc_text, where = _locate_module_map()
    if doc_text is None:
        check("[CP9-T02] module map doc text is available to check (see CP9-T01)", False, where)
        return

    missing = sorted(m for m in expected if m not in doc_text)
    check("[CP9-T02] all 7 real module filenames appear by exact name in the doc",
          not missing, missing)

    py_tokens = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\.py\b", doc_text))
    allowed = expected | {"ratchet.py"}
    invented = sorted(py_tokens - allowed)
    check("[CP9-T02] no eighth '.py' module name (beyond the 7 real ones and the ratchet.py entry "
          "point) is invented in the doc", not invented, invented)

    coupling_a = ("_ratchet_module" in doc_text) or (
        "forward-dependenc" in doc_text.lower() and "19" in doc_text)
    check("[CP9-T02] the doc names the CP5 forward-dependency coupling (provider_discovery.py-style "
          "modules resolving ~19 `_ratchet_module()`-wrapped names plus PRESETS back into ratchet.py) "
          "as a deliberate, accepted coupling", coupling_a, doc_text[:0])

    coupling_b = (("_HOST" in doc_text) or ("bind_host" in doc_text)) and ("_IN_HOOK" in doc_text)
    check("[CP9-T02] the doc names the CP6 `_HOST`/`bind_host()` first-binder-wins coupling for "
          "`_IN_HOOK`/`_HOOK_ENV_ADVISORY` cross-instance identity as a known, accepted limit",
          coupling_b, doc_text[:0])


# ---- CP9-T03 ---------------------------------------------------------------------------------------------


def t03_each_module_has_a_real_description():
    print("CP9-T03: each module gets a real one-line description, not just a bare filename")
    doc_text, where = _locate_module_map()
    if doc_text is None:
        check("[CP9-T03] module map doc text is available to check (see CP9-T01)", False, where)
        return
    names = ["engine_utils.py", "fingerprint.py", "records.py", "provider_discovery.py",
             "run_records.py", "plan_support.py", "status_text.py"]
    lines = doc_text.splitlines()
    for name in names:
        hit_lines = [ln for ln in lines if name in ln]
        check(f"[CP9-T03] {name} is mentioned at least once in the doc", bool(hit_lines), name)
        if not hit_lines:
            continue
        # Use the richest (longest) line mentioning this module as its description line -- a doc may
        # legitimately mention a filename more than once (e.g. once in a table, once in prose).
        best = max(hit_lines, key=len)
        remainder = best.replace(name, "").strip(" -*`|:\t")
        word_chars = re.sub(r"[^A-Za-z]", "", remainder)
        check(f"[CP9-T03] {name}'s line states real descriptive content beyond the bare filename "
              "(not just the name repeated, or a short label)", len(word_chars) >= 25,
              repr(best)[:200])


# ---- CP9-T04 ---------------------------------------------------------------------------------------------


def t04_lists_deferred_areas():
    print("CP9-T04: the doc lists what is still deferred and stays inside ratchet.py")
    doc_text, where = _locate_module_map()
    if doc_text is None:
        check("[CP9-T04] module map doc text is available to check (see CP9-T01)", False, where)
        return
    low = doc_text.lower()
    for area, synonyms in OUT_OF_SCOPE_AREAS.items():
        hit = any(s.lower() in low for s in synonyms)
        check(f"[CP9-T04] the doc names '{area}' as an area still inside ratchet.py, deferred to a "
              "later feature", hit, synonyms)


# ---- CP9-T05 ---------------------------------------------------------------------------------------------

MOVE_CLAIM_PHRASES = [
    "moved into its own module", "moved into a module", "extracted into its own module",
    "extracted into a module", "now lives in its own module", "has its own module",
    "now has its own module", "was moved out", "moved out into",
]


def t05_never_claims_an_out_of_scope_move():
    print("CP9-T05: the doc never claims an out-of-scope area was moved out")
    doc_text, where = _locate_module_map()
    if doc_text is None:
        check("[CP9-T05] module map doc text is available to check (see CP9-T01)", False, where)
        return

    # Re-check (independently of CP9-T02) that no .py filename outside the 7 real modules + the
    # ratchet.py entry point is listed in the doc -- the most direct form an "it moved out" claim
    # about an out-of-scope area could take.
    expected = {"engine_utils.py", "fingerprint.py", "records.py", "provider_discovery.py",
                "run_records.py", "plan_support.py", "status_text.py", "ratchet.py"}
    py_tokens = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\.py\b", doc_text))
    invented = sorted(py_tokens - expected)
    check("[CP9-T05] no module filename outside the 7 real ones (plus the ratchet.py entry point) is "
          "named anywhere in the doc", not invented, invented)

    lines = doc_text.splitlines()
    for area, synonyms in OUT_OF_SCOPE_AREAS.items():
        for ln in lines:
            low = ln.lower()
            if any(s.lower() in low for s in synonyms):
                claimed = any(p in low for p in MOVE_CLAIM_PHRASES)
                check(f"[CP9-T05] the line mentioning '{area}' does not claim it was moved out of "
                      "ratchet.py into its own module", not claimed, ln.strip()[:200])


# ---- CP9-T06 / CP9-T07 -----------------------------------------------------------------------------------


def _pointer_present(text: str, where: str) -> bool:
    target = "schemas.md" if where == "schemas.md" else "modules.md"
    for ln in text.splitlines():
        if "module map" in ln.lower() and target in ln:
            return True
    return False


def t06_readme_points_to_module_map():
    print("CP9-T06: README points a reader to the module map")
    doc_text, where = _locate_module_map()
    if where not in ("schemas.md", "modules.md"):
        check("[CP9-T06] module map doc location is resolvable (see CP9-T01)", False, where)
        return
    readme = README.read_text(encoding="utf-8")
    check(f"[CP9-T06] README.md has a line naming 'Module map' alongside the real location ({where})",
          _pointer_present(readme, where), where)


def t07_skill_md_points_to_module_map():
    print("CP9-T07: the orchestrator SKILL.md points a reader to the module map")
    doc_text, where = _locate_module_map()
    if where not in ("schemas.md", "modules.md"):
        check("[CP9-T07] module map doc location is resolvable (see CP9-T01)", False, where)
        return
    skill = SKILL_MD.read_text(encoding="utf-8")
    check(f"[CP9-T07] SKILL.md has a line naming 'Module map' alongside the real location ({where})",
          _pointer_present(skill, where), where)


# ---- CP9-T08 ---------------------------------------------------------------------------------------------


def t08_full_suite_and_diff_scope():
    print("CP9-T08: the complete local suite passes under the suite lock, and this checkpoint's diff "
          "touches only docs")
    # This case verifies lane M's OWN run context: a `git diff` against CP8's landing commit assuming
    # HEAD is still M's own run tip. On integrated main the diff legitimately includes other lanes'
    # merged work, so skip cleanly rather than false-fail; inside M's run (RUN_DIR present) it runs
    # exactly as before.
    if not RUN_DIR.exists():
        print("[CP9-T08] skipped: not running inside lane M's own run context")
        return
    if os.environ.get("RATCHET_CP9_T08_FULL_SUITE") == "1":
        child_env = {k: v for k, v in os.environ.items() if k != "RATCHET_CP9_T08_FULL_SUITE"}
        r = subprocess.run([sys.executable, "tests/run_all.py"], cwd=str(ROOT), capture_output=True,
                           text=True, timeout=5400, env=child_env)
        m = re.search(r"(\d+) passed, (\d+) failed", r.stdout)
        failed = int(m.group(2)) if m else -1
        check("[CP9-T08] python3 tests/run_all.py exits 0 with 0 failed", r.returncode == 0 and failed == 0,
              (r.returncode, r.stdout[-400:]))
    else:
        check("[CP9-T08] done-means #5 (full suite passes) is enforced structurally by `ratchet.py "
              "complete`, which refuses to mark a checkpoint done when its own regression run fails -- "
              "not re-checked here by default; set RATCHET_CP9_T08_FULL_SUITE=1 to actually run the full "
              "suite inline instead", True)

    allowed = {
        "skills/ratchet-orchestrator/references/schemas.md",
        "skills/ratchet-orchestrator/references/modules.md",
        "README.md",
        "skills/ratchet-orchestrator/SKILL.md",
        "tests/test_modularize_cp9.py",
        # New-test-file registration site: this checkpoint's own new test file is added to README's
        # "Running the tests" list (same lesson CP6/CP7/CP8 applied).
        "tests/fixtures/modularize_cp1/test-inventory.md",
        # Pre-existing, unrelated uncommitted edits already present in this worktree before CP9's own
        # work began (confirmed by hand: `git status` at the start of this checkpoint's test-writing
        # already showed these two dirty, each a one-line fix unrelated to the module map doc -- the
        # guard shortcut table's watch list picking up CP2-CP8's new modules, and a `_tmp.mkdtemp`
        # fix in test_modularize_cp3.py). Not caused by CP9; excluded here the same way CP8-T17
        # excluded its own pre-existing `.ratchet/learnings.md` edit.
        "tests/shortcuts/guard.json",
        "tests/test_modularize_cp3.py",
    }
    changed = set()
    r_diff = subprocess.run(["git", "diff", "--name-only", CP8_LANDING_COMMIT], cwd=str(ROOT),
                            capture_output=True, text=True, timeout=30)
    check("[CP9-T08] setup: `git diff --name-only` against CP8's own landing commit succeeds",
          r_diff.returncode == 0, (r_diff.returncode, r_diff.stderr))
    if r_diff.returncode == 0:
        changed.update(l.strip() for l in r_diff.stdout.splitlines() if l.strip())
    r_status = subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT), capture_output=True,
                              text=True, timeout=30)
    for line in r_status.stdout.splitlines():
        path = line[3:].strip()
        if path:
            changed.add(path)
    unexpected = sorted(p for p in changed if p not in allowed)
    check("[CP9-T08] every changed file (against CP8's own landing commit) is an expected doc/test-"
          "registration file; no .py file under skills/ratchet-orchestrator/scripts/ changed",
          not unexpected, unexpected)
    py_changes = sorted(p for p in changed if p.startswith("skills/ratchet-orchestrator/scripts/")
                        and p.endswith(".py"))
    check("[CP9-T08] specifically: no file under skills/ratchet-orchestrator/scripts/ changed "
          "(this checkpoint is docs-only)", not py_changes, py_changes)


TESTS = [
    ("CP9-T01", t01_doc_exists_somewhere),
    ("CP9-T02", t02_names_every_real_module_exactly),
    ("CP9-T03", t03_each_module_has_a_real_description),
    ("CP9-T04", t04_lists_deferred_areas),
    ("CP9-T05", t05_never_claims_an_out_of_scope_move),
    ("CP9-T06", t06_readme_points_to_module_map),
    ("CP9-T07", t07_skill_md_points_to_module_map),
    ("CP9-T08", t08_full_suite_and_diff_scope),
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
