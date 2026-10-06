#!/usr/bin/env python3
"""
CP2 tests: the slow trust test file is split into parts, nothing lost.   python3 tests/test_split_check.py

AST/text-level structural proof against the *pinned* pre-split source (`git show a24a31d:tests/test_trust.py`,
never the working tree, so later edits to the split files can't make the proof trivially pass), plus
import-smoke and full-run checks on the files that depend on the split. No logic runs change, so this file
does not re-derive trust-hole behaviour; it only proves the move was mechanical and complete.

Kept fast on purpose: every case here is either a pure AST/text comparison against the pinned commit, or a
short-circuited check that skips the one heavy subprocess (a real `python3 tests/test_results.py` /
`tests/test_signing.py` run) until the structural precondition for it (the repointed import) is already true.

This suite writes a per-test JSON report to $RATCHET_TEST_REPORT when that env var is set:
{"cases": [{"id": "CP2-T01", "status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import traceback
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402  (not used for temp folders here -- this file makes none -- imported per convention)

BASE_COMMIT = "a24a31d26ae2ac4a598fc3e0c05a336f6304454e"
# CP1 (the commit right before CP2's split work started) legitimately moved tests/test_trust.py's shared
# helpers from tempfile.mkdtemp to _tmp.mkdtemp. T02 compares helper *bodies*, so it must be pinned to this
# commit (CP2's real starting point), not to the earlier BASE_COMMIT, or a correct CP1->CP2 move reads as a
# changed body. The other cases here compare only check(...) call text / id prefixes, which CP1 did not
# touch, so they stay pinned to BASE_COMMIT.
CP1_COMMIT = "f624da15927cd62059a7710ca0b989c934befe82"
OLD_FILE = "tests/test_trust.py"
README = ROOT / "README.md"
SPEC = ROOT / "openspec/specs/close-ratchet-s-trust-holes-reviews-only-count-f/spec.md"
HELPERS_FILE = HERE / "trust_helpers.py"

# T02 no longer hand-picks which names count as "shared" by guessing section/group boundaries (rounds 1
# and 2 both found real gaps in that guesswork, e.g. CP5's adversarial-round sub-markers reading as one
# coarse "CP5" group). It instead checks every single top-level definition in the pinned source: each one
# must land, body unchanged, in exactly one new file (trust_helpers.py or a split file); landing in more
# than one, or not at all, or with a changed body, is a failure that names the definition.

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = re.search(r"\[(CP2-[TX]\d+)\]", name)
    if m:
        cid = m.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ---- pinned source plumbing --------------------------------------------------------------------------------


def pinned_source() -> str:
    r = subprocess.run(["git", "show", f"{BASE_COMMIT}:{OLD_FILE}"], cwd=str(ROOT),
                        capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"git show failed: {r.returncode} {r.stderr[-300:]}")
    return r.stdout


def cp1_pinned_source() -> str:
    """The pre-split source as CP1 left it, after helpers moved to _tmp.mkdtemp. T02 diffs helper bodies
    against this commit instead of BASE_COMMIT so CP1's legitimate move isn't read as a body change."""
    r = subprocess.run(["git", "show", f"{CP1_COMMIT}:{OLD_FILE}"], cwd=str(ROOT),
                        capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"git show failed: {r.returncode} {r.stderr[-300:]}")
    return r.stdout


def split_files() -> list:
    """The new files the split is expected to produce: tests/trust_helpers.py and any tests/test_trust_cp*.py
    (CP5 may be divided into as many parts as the build settles on, e.g. test_trust_cp5_a.py / _b.py)."""
    files = sorted(ROOT.glob("tests/test_trust_cp*.py"))
    return files


def helper_file_path():
    return HELPERS_FILE if HELPERS_FILE.exists() else None


# ---- CP2-T01: every check() call moves, unchanged, to exactly one new file -----------------------------------


def collect_check_call_texts(source: str, filename: str = "<src>"):
    tree = ast.parse(source, filename=filename)
    texts = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "check":
            seg = ast.get_source_segment(source, node)
            if seg is not None:
                texts.append(seg)
    return texts


def t01_every_check_call_moves_once():
    print("CP2-T01: every check() call from the old file shows up once, word for word, in the new files")
    try:
        pinned = pinned_source()
    except Exception as e:
        check("[CP2-T01] setup: the pinned original tests/test_trust.py is readable via git show", False, str(e))
        return
    pinned_calls = Counter(collect_check_call_texts(pinned, OLD_FILE))
    check("[CP2-T01] setup: the pinned original contains at least one check(...) call",
          sum(pinned_calls.values()) > 0, sum(pinned_calls.values()))

    files = split_files()
    per_file = {}
    for f in files:
        try:
            per_file[f] = Counter(collect_check_call_texts(f.read_text(encoding="utf-8"), f.name))
        except SyntaxError as e:
            per_file[f] = Counter()
            check(f"[CP2-T01] {f.name} parses as valid Python", False, str(e))

    combined = Counter()
    for c in per_file.values():
        combined += c

    missing = pinned_calls - combined
    extra = combined - pinned_calls
    owners = {}
    for f, c in per_file.items():
        for text in c:
            owners.setdefault(text, set()).add(f.name)
    split_across = {t: fs for t, fs in owners.items() if len(fs) > 1 and pinned_calls.get(t, 0) <= 1}

    check("[CP2-T01] no check(...) call from the pinned original is missing from the new files",
          not missing, list(missing.keys())[:5])
    check("[CP2-T01] no check(...) call appears extra or reworded in the new files (none duplicated/added)",
          not extra, list(extra.keys())[:5])
    check("[CP2-T01] no single-occurrence check(...) call landed in more than one new file",
          not split_across, list(split_across.items())[:5])


# ---- CP2-T02: shared helpers move to trust_helpers.py exactly once -------------------------------------------


def top_level_defs(tree):
    """name -> node for every module-level FunctionDef, ClassDef, and Name-target Assign (every target, so
    `PASS = FAIL = 0` yields both PASS and FAIL pointing at the same Assign node). This is every kind of
    top-level *definition*; it deliberately does not look inside Try/If bodies (e.g. the HAVE_PTY probe),
    which are top-level control flow, not a definition with a single body to compare whole."""
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            out[node.name] = node
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if names and len(names) == len(node.targets):
                for n in names:
                    out[n] = node
    return out


# Per-split-file scaffolding every new file legitimately rebuilds for itself, with a body that is correctly
# *different* in each file (its own local test registry and runner, scoped to only that file's cases): not
# a helper the split has to centralize, and not comparable to one pinned body. Confirmed structurally: this
# file's own CP2-T01 already proves every check(...) call inside each cp*_ test case moved correctly, and
# neither name is ever defined in tests/trust_helpers.py (verified by hand against the current split).
PER_FILE_SCAFFOLD_NAMES = {"SECTIONS", "main"}


def t02_shared_helpers_move_once():
    print("CP2-T02: every top-level definition from the old file lands, unchanged, in exactly one new file")
    try:
        pinned = cp1_pinned_source()
    except Exception as e:
        check("[CP2-T02] setup: the pinned original (as CP1 left it) is readable via git show", False, str(e))
        return
    tree = ast.parse(pinned, filename=OLD_FILE)

    pinned_defs = top_level_defs(tree)
    pinned_defs = {n: node for n, node in pinned_defs.items() if n not in PER_FILE_SCAFFOLD_NAMES}
    check("[CP2-T02] setup: at least one top-level definition (function, class or assignment) was found in "
          "the pinned original", len(pinned_defs) > 0, sorted(pinned_defs))

    helper_path = helper_file_path()
    check("[CP2-T02] tests/trust_helpers.py exists", helper_path is not None)
    if helper_path is None:
        return

    candidates = [helper_path] + split_files()
    file_src = {}
    file_defs = {}
    for f in candidates:
        try:
            src = f.read_text(encoding="utf-8")
        except OSError as e:
            check(f"[CP2-T02] setup: {f.name} is readable", False, str(e))
            continue
        file_src[f] = src
        try:
            file_defs[f] = top_level_defs(ast.parse(src, filename=f.name))
        except SyntaxError as e:
            file_defs[f] = {}
            check(f"[CP2-T02] {f.name} parses as valid Python", False, str(e))

    missing = []
    duplicated = []
    changed = []
    for name, node in pinned_defs.items():
        pinned_seg = ast.get_source_segment(pinned, node)
        owners = [f for f, defs in file_defs.items() if name in defs]
        if not owners:
            missing.append(name)
        elif len(owners) > 1:
            duplicated.append((name, sorted(o.name for o in owners)))
        else:
            f = owners[0]
            f_seg = ast.get_source_segment(file_src[f], file_defs[f][name])
            if f_seg != pinned_seg:
                changed.append((name, f.name))

    check("[CP2-T02] every pinned top-level definition appears in one of the new files (trust_helpers.py or "
          "a test_trust_cp*.py split file); none are missing entirely",
          not missing, missing[:10])
    check("[CP2-T02] no pinned top-level definition is redefined (copied) in more than one new file -- a "
          "name another file needs must be imported from trust_helpers, not copied",
          not duplicated, duplicated[:10])
    check("[CP2-T02] every pinned top-level definition's body is identical, verbatim, to the pinned source "
          "in the one new file that owns it (no silent divergence)",
          not changed, changed[:10])


# ---- CP2-T03: the old monolith is gone, not just duplicated ----------------------------------------------


def cp_prefixes_in_file(path: Path):
    ids = re.findall(r"\[CP(\d+)-[TX]\d+\]", path.read_text(encoding="utf-8"))
    return {f"CP{n}" for n in ids}


def t03_old_monolith_gone():
    print("CP2-T03: the old monolith is gone, not just duplicated whole into one new file")
    old_path = ROOT / OLD_FILE
    check("[CP2-T03] tests/test_trust.py no longer exists", not old_path.exists(),
          str(old_path) if old_path.exists() else "")

    try:
        pinned = pinned_source()
        full_prefix_set = {f"CP{n}" for n in re.findall(r"\[CP(\d+)-[TX]\d+\]", pinned)}
    except Exception as e:
        check("[CP2-T03] setup: pinned original readable to compute its full id-prefix set", False, str(e))
        return
    check("[CP2-T03] setup: the pinned original covers more than one CP id prefix (so a whole-file copy is "
          "detectable)", len(full_prefix_set) > 1, sorted(full_prefix_set))

    files = split_files()
    whole_copies = [f.name for f in files if cp_prefixes_in_file(f) == full_prefix_set]
    check("[CP2-T03] no single new file contains every CP id prefix from the old file (nothing just renamed it)",
          not whole_copies, whole_copies)


# ---- CP2-T04: each new file owns one CP group, and CP5 is actually split ------------------------------------


def t04_one_group_per_file_and_cp5_split():
    print("CP2-T04: each new file owns one checkpoint's section, and CP5 is divided across at least two files")
    files = split_files()
    check("[CP2-T04] setup: at least one new split file exists", bool(files), [str(f) for f in files])
    if not files:
        return
    mixed = [(f.name, sorted(cp_prefixes_in_file(f))) for f in files if len(cp_prefixes_in_file(f)) > 1]
    check("[CP2-T04] no new file mixes check()s from two different CP groups",
          not mixed, mixed)

    cp5_files = [f.name for f in files if cp_prefixes_in_file(f) == {"CP5"}]
    check("[CP2-T04] CP5 (the largest pinned section) is divided across at least two files",
          len(cp5_files) >= 2, cp5_files)


# ---- CP2-T05: every new split file imports cleanly on its own ------------------------------------------------


def t05_every_split_file_imports_cleanly():
    print("CP2-T05: every new split file imports cleanly on its own")
    files = split_files()
    if helper_file_path() is not None:
        files = files + [HELPERS_FILE]
    check("[CP2-T05] setup: at least one new split file (or the helper module) exists to import",
          bool(files), [str(f) for f in files])
    if not files:
        return
    env = dict(os.environ)
    env["PYTHONPATH"] = str(HERE)
    bad = []
    for f in files:
        modname = f.stem
        r = subprocess.run([sys.executable, "-c", f"import {modname}"], cwd=str(HERE),
                            capture_output=True, text=True, env=env, timeout=60)
        if r.returncode != 0 or re.search(r"\b(ImportError|NameError|AttributeError|ModuleNotFoundError)\b",
                                           r.stderr):
            bad.append((f.name, r.returncode, r.stderr[-300:]))
    check("[CP2-T05] every new split file imports with no ImportError/NameError/AttributeError",
          not bad, bad)


# ---- CP2-T06/T07: the two dependent suites still pass after their import is repointed -------------------------


def _repointed_to_helper(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    imports_test_trust = False
    imports_helper = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "test_trust":
                    imports_test_trust = True
                if a.name.startswith("trust_helpers"):
                    imports_helper = True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "test_trust":
                imports_test_trust = True
            if mod.startswith("trust_helpers"):
                imports_helper = True
    return (not imports_test_trust) and imports_helper


def _run_full_suite_and_check(case_id: str, rel_path: str):
    path = ROOT / rel_path
    ok_import = _repointed_to_helper(path)
    check(f"[{case_id}] {rel_path}'s `import test_trust as tt` line is repointed at the new shared helper "
          "module (trust_helpers)", ok_import, path.read_text(encoding="utf-8").splitlines()[:5])
    if not ok_import:
        # Short-circuit: do not pay for a multi-minute full run until the structural precondition holds.
        return
    r = subprocess.run([sys.executable, str(path)], cwd=str(ROOT), capture_output=True, text=True, timeout=600)
    m = re.search(r"(\d+) passed, (\d+) failed", r.stdout)
    failed = int(m.group(2)) if m else -1
    check(f"[{case_id}] python3 {rel_path} exits 0 and reports 0 failed, the same as before the split",
          r.returncode == 0 and m is not None and failed == 0,
          (r.returncode, r.stdout[-400:], r.stderr[-400:]))


def t06_test_results_still_passes():
    print("CP2-T06: test_results.py still passes after switching its helper import")
    _run_full_suite_and_check("CP2-T06", "tests/test_results.py")


def t07_test_signing_still_passes():
    print("CP2-T07: test_signing.py still passes after switching its helper import")
    _run_full_suite_and_check("CP2-T07", "tests/test_signing.py")


# ---- CP2-T08: README's test list names the new files, not the retired one ------------------------------------


def t08_readme_names_new_files():
    print("CP2-T08: README's test list names the new files, not the retired one")
    readme = README.read_text(encoding="utf-8")
    check("[CP2-T08] README.md no longer lists `python3 tests/test_trust.py` as a runnable suite",
          "python3 tests/test_trust.py" not in readme, "")
    files = split_files()
    missing = [f.name for f in files if f.name not in readme]
    check("[CP2-T08] README.md's test list names every new split file",
          bool(files) and not missing, missing or "no split files found yet")


# ---- CP2-T09: the openspec record points each moved test id at its new file ------------------------------------


def t09_openspec_points_at_new_files():
    print("CP2-T09: the openspec record points each moved test id at its new file")
    check("[CP2-T09] setup: the openspec spec.md file is readable", SPEC.exists(), str(SPEC))
    if not SPEC.exists():
        return
    spec = SPEC.read_text(encoding="utf-8")
    annotations = re.findall(r"<!-- proven by test (CP\d+-[TX]\d+) in ([^,]+), commit [^\s]+ -->", spec)
    check("[CP2-T09] setup: the spec has at least one 'proven by test ... in tests/test_trust.py' annotation to "
          "re-point", bool(annotations), len(annotations))
    still_old = [(cid, f) for cid, f in annotations if f.strip() == OLD_FILE]
    check("[CP2-T09] no annotation still names tests/test_trust.py; each names the new file that now holds it",
          not still_old, still_old[:10])


TESTS = [
    ("CP2-T01", t01_every_check_call_moves_once),
    ("CP2-T02", t02_shared_helpers_move_once),
    ("CP2-T03", t03_old_monolith_gone),
    ("CP2-T04", t04_one_group_per_file_and_cp5_split),
    ("CP2-T05", t05_every_split_file_imports_cleanly),
    ("CP2-T06", t06_test_results_still_passes),
    ("CP2-T07", t07_test_signing_still_passes),
    ("CP2-T08", t08_readme_names_new_files),
    ("CP2-T09", t09_openspec_points_at_new_files),
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
        except Exception as e:  # a crash in this file still counts as a result for this case
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
