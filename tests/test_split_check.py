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
import copy
import json
import os
import re
import subprocess
import sys
import traceback
from collections import Counter, defaultdict
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
# The commit where CP2's split landed. Only T02's helper-*body* comparison reads the new side from this commit
# (trust_helpers.py and the split files, for the one check that diffs a definition's body verbatim): reading
# that comparison live meant a later, legitimate edit to a shared fixture (e.g. a later run's CP1 adding a
# required plan objective to tests/trust_helpers.py or test_trust_cp5_c.py) read as a "changed body" and failed
# this historical structural proof, even though nothing about the split regressed. Every other check here
# (T01's check-call preservation, T03/T04's grouping, T05's import smoke test, T06/T07's import-repoint
# precondition and full-suite run, T08's README mapping, T09's openspec mapping) reads the *current* working
# tree for the new side, so deleting a migrated check or breaking today's README/spec/import mapping still
# fails here and now, not just at the moment of the split.
CP2_COMMIT = "5f394c1d2197a96f5754a8f14a75ee3f83206ceb"
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


def show_at(commit: str, relpath: str) -> str:
    """A file's content as of `commit`, read with `git show` instead of the working tree."""
    r = subprocess.run(["git", "show", f"{commit}:{relpath}"], cwd=str(ROOT),
                        capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"git show failed: {r.returncode} {r.stderr[-300:]}")
    return r.stdout


def exists_at(commit: str, relpath: str) -> bool:
    """Whether `relpath` existed as a blob at `commit`, checked with `git cat-file -e`."""
    r = subprocess.run(["git", "cat-file", "-e", f"{commit}:{relpath}"], cwd=str(ROOT),
                        capture_output=True, text=True, timeout=30)
    return r.returncode == 0


def split_files() -> list:
    """The new files the split is expected to produce: tests/trust_helpers.py and any tests/test_trust_cp*.py
    (CP5 may be divided into as many parts as the build settles on, e.g. test_trust_cp5_a.py / _b.py)."""
    files = sorted(ROOT.glob("tests/test_trust_cp*.py"))
    return files


def split_files_pinned() -> list:
    """The relative paths (as strings) of the new test_trust_cp*.py files as they existed right after CP2's
    split (CP2_COMMIT). Used only by T02's helper-body comparison, so a later legitimate edit to a split
    file's body does not make that one historical body-diff fail."""
    r = subprocess.run(["git", "ls-tree", "-r", "--name-only", CP2_COMMIT, "--", "tests/"], cwd=str(ROOT),
                        capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"git ls-tree failed: {r.returncode} {r.stderr[-300:]}")
    names = [n for n in r.stdout.splitlines() if n]
    return sorted(n for n in names if re.fullmatch(r"tests/test_trust_cp.*\.py", n))


def split_files_current() -> list:
    """The relative paths (as strings) of the new test_trust_cp*.py files in the *current* working tree. Every
    check here except T02 uses this (not split_files_pinned()), so deleting a migrated check or breaking
    today's README/spec/import mapping is caught today, not just as of the CP2 split commit."""
    return sorted(str(p.relative_to(ROOT)) for p in split_files())


def read_current(relpath: str) -> str:
    """A file's content read live from the working tree -- the current-tree counterpart to show_at()."""
    return (ROOT / relpath).read_text()


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


def parse_check_call_parts(call_text: str):
    """Parse one `check(...)` call's exact source text into (case_id_or_None, message_text,
    cond_node). `case_id` is the `[CPn-Txx]`/`[CPn-Xxx]` prefix pulled out of the message, when
    there is one. `message_text` is the verbatim source of the first argument (the whole thing,
    including every concatenated string literal), used together with `case_id` as the matching
    key: two check(...) calls are "the same case" only when both their id (or, lacking one, their
    whole message) AND their message text line up, so CP5-X60's four distinct sub-checks are
    never compared against each other's condition. Returns None if `call_text` isn't a bare
    `check(...)` call (should not happen -- collect_check_call_texts only ever yields those)."""
    try:
        tree = ast.parse(call_text, mode="eval")
    except SyntaxError:
        return None
    call = tree.body
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "check"):
        return None
    if not call.args:
        return None
    msg_node = call.args[0]
    msg_text = (ast.get_source_segment(call_text, msg_node) or "").strip()
    m = re.search(r"\[(CP\d+-[TX]\d+)\]", msg_text)
    case_id = m.group(1) if m else None
    cond_node = call.args[1] if len(call.args) > 1 else None
    return case_id, msg_text, cond_node


def _cond_shape(node):
    """An ast.dump() of `node` with every bare Name blanked out to one placeholder, so a cosmetic
    rename of a local variable (e.g. `src` -> `src_with_records`) compares equal while every
    other piece of structure -- operators, boolop clause count, constants, attribute names,
    call targets -- still has to match exactly. Returns None for a missing condition."""
    if node is None:
        return None

    class _Blank(ast.NodeTransformer):
        def visit_Name(self, n):
            return ast.copy_location(ast.Name(id="_VAR_", ctx=ast.Load()), n)

    shadow = copy.deepcopy(node)
    _Blank().visit(shadow)
    return ast.dump(shadow, annotate_fields=False)


def _literal_set(node):
    """The set of constant values in a List/Set/Tuple-of-constants literal, or None if `node`
    isn't one. Used only to notice a comparator literal that grew wider (more values accepted)."""
    if isinstance(node, (ast.List, ast.Set, ast.Tuple)) and all(isinstance(e, ast.Constant) for e in node.elts):
        return {e.value for e in node.elts}
    return None


def _looks_weakened(pinned_node, current_node):
    """True when `current_node`'s condition looks like a real loosening of `pinned_node`'s --
    more inputs now make it pass -- not just a cosmetic rename (ruled out already by the caller
    via _cond_shape before this runs). Three concrete, auditable signals, chosen so a renamed
    identifier never trips them: an `and` turned into `or`, an `and`-chain losing one of its
    operands, an `==` broadened into an `in`, a `not` dropped from around an otherwise-unchanged
    expression, or an `in`-comparator's literal set growing to accept more values. Anything else
    that merely differs structurally is deliberately left unflagged -- this guard only chases the
    specific loosening shapes above, not every possible rewrite (see the CP5 brief)."""
    if isinstance(pinned_node, ast.BoolOp) and isinstance(current_node, ast.BoolOp):
        if isinstance(pinned_node.op, ast.And) and isinstance(current_node.op, ast.Or):
            return True
        if isinstance(pinned_node.op, ast.And) and isinstance(current_node.op, ast.And):
            if len(current_node.values) < len(pinned_node.values):
                return True
    if isinstance(pinned_node, ast.UnaryOp) and isinstance(pinned_node.op, ast.Not):
        if not (isinstance(current_node, ast.UnaryOp) and isinstance(current_node.op, ast.Not)):
            if _cond_shape(pinned_node.operand) == _cond_shape(current_node):
                return True
    if isinstance(pinned_node, ast.Compare) and isinstance(current_node, ast.Compare):
        if len(pinned_node.ops) == 1 and len(current_node.ops) == 1:
            pop, cop = pinned_node.ops[0], current_node.ops[0]
            if isinstance(pop, ast.Eq) and isinstance(cop, ast.In):
                return True
            if isinstance(pop, ast.In) and isinstance(cop, ast.In):
                p_set = _literal_set(pinned_node.comparators[0])
                c_set = _literal_set(current_node.comparators[0])
                if p_set is not None and c_set is not None and p_set < c_set:
                    return True
    return False


def _group_by_key(calls: Counter):
    """Group a Counter of check(...) call source texts by their (id-or-message) pairing key: the
    case id when there is one, else the whole message text, always paired with the message text so
    two same-id sub-checks with different messages (e.g. CP5-X60's four) stay distinct. The value is
    the list of each call's condition AST node, one per occurrence. Shared by both the "missing"
    sub-check (find_missing_checks) and the "weakened" sub-check (find_weakened_checks) so the two
    pair pinned-vs-current the exact same way -- a reworded-but-present check must look the same to
    both. A call that does not parse as a bare check(...) is skipped."""
    grouped = defaultdict(list)
    for text, n in calls.items():
        parsed = parse_check_call_parts(text)
        if parsed is None:
            continue
        case_id, msg_text, cond_node = parsed
        key = (case_id, msg_text) if case_id else (None, msg_text)
        grouped[key].extend([cond_node] * n)
    return grouped


def find_missing_checks(pinned_calls: Counter, combined: Counter):
    """The ORIGINAL check(...) calls that were genuinely dropped: a pinned call whose (id-or-message)
    key has NO counterpart anywhere in the current files. Pairs pinned-vs-current by the SAME key
    find_weakened_checks uses (via _group_by_key), rather than by exact call text, so a check that
    still exists under the same id/message but with a renamed identifier / reworded condition (e.g.
    CP5-X65's `src` -> `src_with_records` rename) is NOT a false drop -- it is present, and
    find_weakened_checks already guards it against being loosened. Only a key with no current
    counterpart at all is a real drop. Returns a sorted list of the dropped checks' id-or-message
    strings."""
    pinned_by_key = _group_by_key(pinned_calls)
    current_by_key = _group_by_key(combined)
    missing = [key for key in pinned_by_key if key not in current_by_key]
    return sorted(str(key[0] or key[1]) for key in missing)


def find_weakened_checks(pinned_calls: Counter, combined: Counter):
    """The real integrity property this file's softened [CP2-T01] "extra" assertion enforces: no
    ORIGINAL check(...) call was dropped or weakened. A pinned call with no same-id/same-message
    counterpart anywhere in `combined` is not this function's job -- the "missing" assertion right
    above it in t01 (find_missing_checks) already catches a call that vanished with nothing
    resembling it left behind. This function instead pairs up pinned and current calls that share the
    same case id (or, when there is none, the same message text) AND the same message text, and flags
    only the pairs whose condition looks structurally loosened per _looks_weakened -- a brand-new
    check with no pinned counterpart, or a reviewed re-point whose condition is the pinned one modulo
    a renamed identifier, are both left alone. Returns a list of (case_id_or_message,
    pinned_cond_dump)."""
    pinned_by_key = _group_by_key(pinned_calls)
    current_by_key = _group_by_key(combined)

    findings = []
    for key, pinned_nodes in pinned_by_key.items():
        current_nodes = current_by_key.get(key)
        if not current_nodes:
            continue  # no same-id/same-message counterpart at all: "missing" above already covers this
        remaining = list(current_nodes)
        for p in pinned_nodes:
            if p is None:
                continue
            p_shape = _cond_shape(p)
            match_idx = next((i for i, c in enumerate(remaining) if _cond_shape(c) == p_shape), None)
            if match_idx is not None:
                remaining.pop(match_idx)  # identical modulo renames: unchanged, or a safe re-point
                continue
            if not remaining:
                continue
            candidate = remaining.pop(0)
            if _looks_weakened(p, candidate):
                findings.append((key[0] or key[1], ast.dump(p, annotate_fields=False)[:120]))
    return findings


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

    relpaths = split_files_current()
    per_file = {}
    for relpath in relpaths:
        fname = Path(relpath).name
        try:
            src = read_current(relpath)
        except OSError as e:
            per_file[fname] = Counter()
            check(f"[CP2-T01] {fname} is readable at the CP2 split commit", False, str(e))
            continue
        try:
            per_file[fname] = Counter(collect_check_call_texts(src, fname))
        except SyntaxError as e:
            per_file[fname] = Counter()
            check(f"[CP2-T01] {fname} parses as valid Python", False, str(e))

    combined = Counter()
    for c in per_file.values():
        combined += c

    missing = find_missing_checks(pinned_calls, combined)
    owners = {}
    for fname, c in per_file.items():
        for text in c:
            owners.setdefault(text, set()).add(fname)
    split_across = {t: fs for t, fs in owners.items() if len(fs) > 1 and pinned_calls.get(t, 0) <= 1}

    check("[CP2-T01] no check(...) call from the pinned original is missing from the new files "
          "(paired by id/message, so a check reworded via a renamed identifier under the same "
          "id/message is present, not a false drop; a wholly-dropped id/message is still caught)",
          not missing, missing[:5])
    # Softened on purpose (see the CP5 brief): a brand-new check(...) call, or a reviewed re-point
    # of an existing one (e.g. a renamed local variable inside an otherwise-equivalent condition),
    # must not fail this guard -- only a check that was dropped (already caught by "missing" above)
    # or silently weakened (same id/message, an easier-to-pass condition) should.
    weakened = find_weakened_checks(pinned_calls, combined)
    check("[CP2-T01] no original check(...) call was silently weakened in the new files (brand-new "
          "checks and reviewed re-points are fine; a clause dropped or an operator loosened under "
          "the same id/message is not)",
          not weakened, weakened[:5])
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

# T02 runs two different checks against two different sources, on purpose:
#  (a) LIVE: does every pinned top-level definition still land, exactly once, in one of TODAY's files
#      (tests/trust_helpers.py or a test_trust_cp*.py split file, read from the working tree)? This is what
#      gives the test teeth against today's code: removing a shared helper or duplicating it into two split
#      files right now fails here, not just against a commit that can never change again.
#  (b) PINNED: is each definition's *body* still byte-for-byte what landed at CP2_COMMIT? Helper bodies may
#      legitimately evolve later (e.g. run 3's CP1 adding a plan-objective fixture), so only the body
#      comparison stays pinned to the CP2 split commit; a legitimate later edit must not fail this historical
#      diff.


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

    helper_existed = exists_at(CP2_COMMIT, "tests/trust_helpers.py")
    check("[CP2-T02] tests/trust_helpers.py exists", helper_existed)
    if not helper_existed:
        return

    # (a) LIVE: read today's working tree, not a commit, so a helper removed or duplicated today is caught
    # today. tests/trust_helpers.py is read live too (HELPERS_FILE already requires it to exist for the rest
    # of the suite to import); imports from trust_helpers.py in a cp*_ file are not re-definitions, since
    # top_level_defs() only looks at module-level FunctionDef/ClassDef/Assign nodes, never Import nodes.
    live_candidates = ["tests/trust_helpers.py"] + split_files_current()
    live_defs = {}
    for relpath in live_candidates:
        fname = Path(relpath).name
        try:
            src = read_current(relpath)
        except OSError as e:
            check(f"[CP2-T02] (live) setup: {fname} is readable", False, str(e))
            continue
        try:
            live_defs[fname] = top_level_defs(ast.parse(src, filename=fname))
        except SyntaxError as e:
            live_defs[fname] = {}
            check(f"[CP2-T02] (live) {fname} parses as valid Python", False, str(e))

    live_missing = []
    live_duplicated = []
    for defname in pinned_defs:
        owners = [fname for fname, defs in live_defs.items() if defname in defs]
        if not owners:
            live_missing.append(defname)
        elif len(owners) > 1:
            live_duplicated.append((defname, sorted(owners)))

    check("[CP2-T02] (live) every pinned top-level definition appears in one of TODAY's new files "
          "(trust_helpers.py or a test_trust_cp*.py split file, read from the working tree); none are "
          "missing entirely", not live_missing, live_missing[:10])
    check("[CP2-T02] (live) no pinned top-level definition is redefined (copied, not imported) in more than "
          "one of TODAY's new files -- a name another file needs must be imported from trust_helpers",
          not live_duplicated, live_duplicated[:10])

    # (b) PINNED: body-identity comparison against the CP2 split commit (see comment above the function).
    candidates = ["tests/trust_helpers.py"] + split_files_pinned()
    file_src = {}
    file_defs = {}
    for relpath in candidates:
        fname = Path(relpath).name
        try:
            src = show_at(CP2_COMMIT, relpath)
        except RuntimeError as e:
            check(f"[CP2-T02] setup: {fname} is readable", False, str(e))
            continue
        file_src[fname] = src
        try:
            file_defs[fname] = top_level_defs(ast.parse(src, filename=fname))
        except SyntaxError as e:
            file_defs[fname] = {}
            check(f"[CP2-T02] {fname} parses as valid Python", False, str(e))

    missing = []
    duplicated = []
    changed = []
    for defname, node in pinned_defs.items():
        pinned_seg = ast.get_source_segment(pinned, node)
        owners = [fname for fname, defs in file_defs.items() if defname in defs]
        if not owners:
            missing.append(defname)
        elif len(owners) > 1:
            duplicated.append((defname, sorted(owners)))
        else:
            fname = owners[0]
            f_seg = ast.get_source_segment(file_src[fname], file_defs[fname][defname])
            if f_seg != pinned_seg:
                changed.append((defname, fname))

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


def cp_prefixes_in_text(text: str):
    ids = re.findall(r"\[CP(\d+)-[TX]\d+\]", text)
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

    relpaths = split_files_current()
    whole_copies = []
    for relpath in relpaths:
        fname = Path(relpath).name
        try:
            text = read_current(relpath)
        except OSError as e:
            check(f"[CP2-T03] {fname} is readable at the CP2 split commit", False, str(e))
            continue
        if cp_prefixes_in_text(text) == full_prefix_set:
            whole_copies.append(fname)
    check("[CP2-T03] no single new file contains every CP id prefix from the old file (nothing just renamed it)",
          not whole_copies, whole_copies)


# ---- CP2-T04: each new file owns one CP group, and CP5 is actually split ------------------------------------


def t04_one_group_per_file_and_cp5_split():
    print("CP2-T04: each new file owns one checkpoint's section, and CP5 is divided across at least two files")
    relpaths = split_files_current()
    check("[CP2-T04] setup: at least one new split file exists", bool(relpaths),
          [str(ROOT / r) for r in relpaths])
    if not relpaths:
        return
    prefixes_by_file = {}
    for relpath in relpaths:
        fname = Path(relpath).name
        try:
            text = read_current(relpath)
        except OSError as e:
            check(f"[CP2-T04] {fname} is readable at the CP2 split commit", False, str(e))
            continue
        prefixes_by_file[fname] = cp_prefixes_in_text(text)

    mixed = [(fname, sorted(p)) for fname, p in prefixes_by_file.items() if len(p) > 1]
    check("[CP2-T04] no new file mixes check()s from two different CP groups",
          not mixed, mixed)

    cp5_files = [fname for fname, p in prefixes_by_file.items() if p == {"CP5"}]
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


def _repointed_to_helper(source: str, filename: str):
    tree = ast.parse(source, filename=filename)
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
    try:
        pinned_text = read_current(rel_path)
    except OSError as e:
        check(f"[{case_id}] {rel_path} is readable at the CP2 split commit", False, str(e))
        return
    ok_import = _repointed_to_helper(pinned_text, rel_path)
    check(f"[{case_id}] {rel_path}'s `import test_trust as tt` line is repointed at the new shared helper "
          "module (trust_helpers)", ok_import, pinned_text.splitlines()[:5])
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
    try:
        readme = read_current(str(README.relative_to(ROOT)))
    except OSError as e:
        check("[CP2-T08] setup: README.md is readable at the CP2 split commit", False, str(e))
        return
    check("[CP2-T08] README.md no longer lists `python3 tests/test_trust.py` as a runnable suite",
          "python3 tests/test_trust.py" not in readme, "")
    relpaths = split_files_current()
    fnames = [Path(r).name for r in relpaths]
    missing = [n for n in fnames if n not in readme]
    check("[CP2-T08] README.md's test list names every new split file",
          bool(fnames) and not missing, missing or "no split files found yet")


# ---- CP2-T09: the openspec record points each moved test id at its new file ------------------------------------


def t09_openspec_points_at_new_files():
    print("CP2-T09: the openspec record points each moved test id at its new file")
    try:
        spec = read_current(str(SPEC.relative_to(ROOT)))
    except OSError as e:
        check("[CP2-T09] setup: the openspec spec.md file is readable", False, str(e))
        return
    check("[CP2-T09] setup: the openspec spec.md file is readable", True, "")
    annotations = re.findall(r"<!-- proven by test (CP\d+-[TX]\d+) in ([^,]+), commit [^\s]+ -->", spec)
    check("[CP2-T09] setup: the spec has at least one 'proven by test ... in tests/test_trust.py' annotation to "
          "re-point", bool(annotations), len(annotations))
    still_old = [(cid, f) for cid, f in annotations if f.strip() == OLD_FILE]
    check("[CP2-T09] no annotation still names tests/test_trust.py; each names the new file that now holds it",
          not still_old, still_old[:10])


# ---- CP2-T10: the softened [CP2-T01] "extra" guard still has teeth -------------------------------------------
#
# CP2-T01's "extra" assertion was softened (see the CP5 brief) from "no check(...) call appears
# extra or reworded at all" to "no original check(...) call was dropped or weakened" via
# find_weakened_checks(). This proves that softening did not remove the guard's teeth: it is fed
# small synthetic pinned/current Counters (never the real pinned source) built to look like real
# check(...) call text, one scenario at a time.


def _counter_of(call_texts):
    return Counter(call_texts)


def t10_softened_extra_check_still_has_teeth():
    print("CP2-T10: the softened [CP2-T01] guard still catches a drop or a weakening, and still "
          "lets a new check or a reviewed re-point through")

    # (a) DROPPED: an original check with nothing resembling it left behind in the current files.
    # Caught by the "missing" assertion (find_missing_checks) right above the softened one in t01 --
    # not by find_weakened_checks() itself, which has no same-id/same-message counterpart to compare
    # against and correctly stays silent on it (see its own docstring).
    dropped_text = 'check("[CP99-T01] alpha holds", cond_a and cond_b, "d")'
    pinned_a = _counter_of([dropped_text])
    current_a = _counter_of([])  # nothing resembling it survives the split
    check("[CP2-T10] (a) a dropped original check with no id/message counterpart anywhere is still "
          "caught, by the 'missing' assertion (find_missing_checks)",
          find_missing_checks(pinned_a, current_a) == ["CP99-T01"], find_missing_checks(pinned_a, current_a))
    check("[CP2-T10] (a) find_weakened_checks() itself does not try to judge a check with no "
          "same-id/same-message counterpart at all (that is 'missing'\\'s job, not this one's)",
          find_weakened_checks(pinned_a, current_a) == [], find_weakened_checks(pinned_a, current_a))

    # (a2) DROPPED vs REWORDED, side by side: a wholly-dropped check (no id/message counterpart) is
    # still flagged missing, while a check that survives under the SAME id/message with only a
    # renamed identifier in its condition (the real CP5-X65 `src` -> `src_with_records` case that
    # made the old exact-text "missing" sub-check false-fail on merged main) is NOT a drop.
    pinned_a2 = _counter_of([
        'check("[CP99-T07] eta holds", cond_a and cond_b, "d")',           # wholly dropped below
        'check("[CP99-T08] theta holds", "def f" in src and other, "d")',  # survives, identifier renamed
    ])
    current_a2 = _counter_of([
        # CP99-T07 gone entirely -- no id/message counterpart survives
        'check("[CP99-T08] theta holds", "def f" in src_with_records and other, "d")',  # same id/msg, renamed
    ])
    missing_a2 = find_missing_checks(pinned_a2, current_a2)
    check("[CP2-T10] (a2) a wholly-dropped check (no id/message counterpart) is still caught as "
          "missing", "CP99-T07" in missing_a2, missing_a2)
    check("[CP2-T10] (a2) a check reworded via a renamed identifier (same id/message, changed "
          "condition) is NOT flagged missing -- it is present", "CP99-T08" not in missing_a2, missing_a2)
    check("[CP2-T10] (a2) and find_weakened_checks() does not read that pure rename as a weakening",
          find_weakened_checks(pinned_a2, current_a2) == [], find_weakened_checks(pinned_a2, current_a2))

    # (b) WEAKENED: same id and message, a condition that now accepts strictly more inputs.
    pinned_b = _counter_of([
        'check("[CP99-T02] beta holds", cond_a and cond_b, "d")',
        'check("[CP99-T03] gamma holds", cond_a and cond_b and cond_c, "d")',
    ])
    current_b = _counter_of([
        'check("[CP99-T02] beta holds", cond_a or cond_b, "d")',          # and -> or
        'check("[CP99-T03] gamma holds", cond_a and cond_b, "d")',        # an AND-ed clause dropped
    ])
    found_b = find_weakened_checks(pinned_b, current_b)
    check("[CP2-T10] (b) an `and` silently turned into an `or` under the same id/message is caught "
          "as weakened", any(cid == "CP99-T02" for cid, _ in found_b), found_b)
    check("[CP2-T10] (b) an AND-ed clause silently dropped under the same id/message is caught as "
          "weakened", any(cid == "CP99-T03" for cid, _ in found_b), found_b)

    # (c) NEW: a brand-new check added with no original counterpart must pass cleanly.
    pinned_c = _counter_of(['check("[CP99-T04] delta holds", cond_a and cond_b, "d")'])
    current_c = _counter_of([
        'check("[CP99-T04] delta holds", cond_a and cond_b, "d")',          # unchanged
        'check("[CP99-T05] epsilon holds", cond_x or cond_y, "d")',         # wholly new, no pinned counterpart
    ])
    found_c = find_weakened_checks(pinned_c, current_c)
    check("[CP2-T10] (c) a brand-new check(...) call with no pinned counterpart passes cleanly "
          "(is never flagged as weakened)", found_c == [], found_c)

    # (d) RE-POINT: same id/message, a condition that is the pinned one modulo a renamed identifier
    # (e.g. a reviewed rename of a local variable the condition mentions), same operator/clause
    # structure otherwise -- must also pass cleanly.
    pinned_d = _counter_of(['check("[CP99-T06] zeta holds", src.value == 1 and other, "d")'])
    current_d = _counter_of(['check("[CP99-T06] zeta holds", src_with_records.value == 1 and other, "d")'])
    found_d = find_weakened_checks(pinned_d, current_d)
    check("[CP2-T10] (d) a reviewed re-point (same id/message, a renamed identifier inside an "
          "otherwise-equivalent condition) passes cleanly", found_d == [], found_d)


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
    ("CP2-T10", t10_softened_extra_check_still_has_teeth),
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
