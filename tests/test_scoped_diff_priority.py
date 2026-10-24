#!/usr/bin/env python3
"""
CP1 tests: a scoped (fix round) review sees real changes first.   python3 tests/test_scoped_diff_priority.py

Today scoped_diff_parts (skills/ratchet-adversarial-loop/scripts/external_review.py) packs an over-budget
scoped diff by walking its chunks in plain git order with no priority: a big new file printed first by git
can use up the whole budget before a real edit or delete lower in git's order is even considered, so the
reviewer may see only a brand-new file and never the actual code change. This file proves the fix: the
fitting loop must walk every non-add (a modification, or a pure delete, summarized or not) in git's order
before any pure add, and the adds themselves smallest first (a tie keeps git's order) -- mirroring
_pack_full_round's own grouping. Shown files must still PRINT in git's own order; a diff that fits whole is
untouched; MAX_DIFF, summarization and the summary swap-back are unchanged.

Library-only, black-box: skills/ratchet-adversarial-loop/scripts/external_review.py is loaded by path (via
tests/test_review_diff_large_files.py's already-loaded module, reused here as `ER`), and
scoped_diff_parts(root, since_tree, current_tree) is called directly on throwaway git repos built with
`git add -A && git write-tree` (no commits needed). Fixture sizes are derived from ER.MAX_DIFF and from
real, measured git diff-tree output -- never a hard-coded guess -- using the empty tree's well-known id
(4b825dc642cb6eb9a060e54bf8d69288fbee4904, the same for every git repo) to measure one file's own pure-add
or pure-delete chunk overhead in isolation, then padding a single content line so that file's own chunk is
exactly the byte length a case needs. A modification's size only needs to be approximately right (git
itself, not a guess, still produces the real chunk the test compares against), so those use plain
multi-line content and are measured after the fact.

Writes a per-test JSON report to RATCHET_TEST_REPORT when set: {"cases": [{"id": "CP1-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_review_diff_large_files as t1  # noqa: E402  (ER, repo/content builders, _part_for)

ER = t1.ER
_mk_repo = t1._mk_repo
_snapshot = t1._snapshot
_git = t1._git
_part_for = t1._part_for

PASS = FAIL = 0
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
ID_RE = re.compile(r"\[(CP1-T\d+)\]")
PLANNED = [f"CP1-T{i:02d}" for i in range(1, 12)]


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'OK ' if cond else 'FAIL'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ================================================================================================ sizing helpers

# The empty tree's id is the same for every git repo (it depends only on git's object format, never on
# repo content), so diffing any blob against it always shows that blob as a pure add (or, the other way
# round, as a pure delete) -- the measurement a sizing helper needs never touches the test's own since/
# current trees.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

_ADD_OVERHEAD_CACHE: dict = {}
_DEL_OVERHEAD_CACHE: dict = {}


def _add_overhead(rel: str) -> int:
    """The exact byte overhead (every byte of a brand-new `rel`'s pure-add diff chunk except its one body
    line) -- measured for real via git on a throwaway one-line placeholder, never guessed or hard-coded."""
    if rel in _ADD_OVERHEAD_CACHE:
        return _ADD_OVERHEAD_CACHE[rel]
    d = _mk_repo("calib-add")
    p = d / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("p\n")
    _git(d, "add", "-A")
    tree = _git(d, "write-tree").strip()
    chunk = _git(d, "diff-tree", "-r", "-p", "--no-color", EMPTY_TREE, tree, "--", rel)
    overhead = len(chunk) - len("+p\n")
    _ADD_OVERHEAD_CACHE[rel] = overhead
    return overhead


def _del_overhead(rel: str) -> int:
    """Like `_add_overhead`, for a pure DELETE chunk of `rel` (the file existed, then is gone)."""
    if rel in _DEL_OVERHEAD_CACHE:
        return _DEL_OVERHEAD_CACHE[rel]
    d = _mk_repo("calib-del")
    p = d / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("p\n")
    _git(d, "add", "-A")
    tree = _git(d, "write-tree").strip()
    chunk = _git(d, "diff-tree", "-r", "-p", "--no-color", tree, EMPTY_TREE, "--", rel)
    overhead = len(chunk) - len("-p\n")
    _DEL_OVERHEAD_CACHE[rel] = overhead
    return overhead


def _sized_add_content(rel: str, total_chars: int, tag: str = "ZQ-PAD-") -> str:
    """One content line for a brand-new file `rel` so that its own pure-add diff chunk is exactly
    `total_chars` bytes -- computed from `_add_overhead`, never approximated."""
    overhead = _add_overhead(rel)
    body_len = total_chars - overhead - 2  # 2 == the body line's own '+' prefix and trailing '\n'
    assert body_len >= len(tag) + 1, (rel, total_chars, overhead, body_len)
    return tag + ("Z" * (body_len - len(tag))) + "\n"


def _sized_delete_content(rel: str, total_chars: int, tag: str = "ZQ-PAD-") -> str:
    """Like `_sized_add_content`, for the content to write at `since` so deleting `rel` produces a pure-
    delete diff chunk of exactly `total_chars` bytes."""
    overhead = _del_overhead(rel)
    body_len = total_chars - overhead - 2
    assert body_len >= len(tag) + 1, (rel, total_chars, overhead, body_len)
    return tag + ("Z" * (body_len - len(tag))) + "\n"


def _lines(prefix: str, n: int, width: int = 20) -> str:
    """n unique, numbered lines ('<prefix><5-digit i>', padded with '.' to `width` chars), each a line of
    its own -- content for a real modification fixture. No two such fixtures share a prefix, so git -M
    never pairs one's delete with another's add unless a case wants a rename."""
    out = []
    for i in range(n):
        s = f"{prefix}{i:05d}"
        if width and len(s) < width:
            s += "." * (width - len(s))
        out.append(s)
    return "\n".join(out) + "\n"


def _edit_block(text: str, start: int, count: int, marker: str) -> str:
    """`text` with `count` contiguous lines from `start` rewritten (marker appended), so git shows one
    real modification hunk there."""
    lines = text.split("\n")
    trailing = bool(lines) and lines[-1] == ""
    body = lines[:-1] if trailing else lines
    for i in range(start, start + count):
        body[i] = body[i] + marker
    return "\n".join(body) + ("\n" if trailing else "")


def _git_m(d, since, cur) -> str:
    """git's own `diff-tree -r -p -M --no-color` output -- the exact call scoped_diff_parts makes."""
    import subprocess
    r = subprocess.run(["git", "-c", "core.quotepath=off", "diff-tree", "-r", "-p", "-M", "--no-color",
                        since, cur], cwd=str(d), capture_output=True, text=True)
    assert r.returncode == 0, (r.stdout, r.stderr)
    return r.stdout


def _idx(text: str, path: str) -> int:
    return text.find(f"diff --git a/{path} b/{path}")


def _part_for_rename(text: str, old_path: str, new_path: str) -> str:
    """Like t1._part_for, for a renamed file whose 'diff --git' line names a different path on each side."""
    marker = f"diff --git a/{old_path} b/{new_path}"
    assert marker in text, (old_path, new_path, text[-500:])
    idx = text.index(marker)
    rest = text[idx:]
    m = re.search(r"(?m)^diff --git ", rest[1:])
    end = (m.start() + 1) if m else len(rest)
    note_m = re.search(r"\n\[diff too large", rest[:end])
    if note_m:
        end = note_m.start()
    return rest[:end]


def _budget(since: str, cur: str) -> int:
    return ER.MAX_DIFF - len(t1._scoped_header(since, cur))


def _note_tail(text: str) -> str:
    i = text.find("diff too large")
    return text[i:] if i != -1 else ""


# ================================================================================================ CP1-T01..T04

def t01_huge_add_printed_first_no_longer_pushes_out_a_small_edit():
    d = _mk_repo("cp1t01")
    (d / "z").mkdir()
    before = _lines("EDIT-", 2000, width=20)
    (d / "z/edit.py").write_text(before)
    since = _snapshot(d)
    after = _edit_block(before, 800, 390, " ZQ-T01-EDIT")
    (d / "z/edit.py").write_text(after)
    edit_only = _snapshot(d)
    edit_chunk = _part_for(_git_m(d, since, edit_only), "z/edit.py")
    budget = _budget(since, edit_only)
    huge_target = budget - 10000
    check("[CP1-T01] (fixture sanity) the edit chunk is a real, sizeable modification (roughly 25,000 chars)",
          15000 <= len(edit_chunk) <= 35000, len(edit_chunk))
    (d / "a").mkdir()
    (d / "a/huge.txt").write_text(_sized_add_content("a/huge.txt", huge_target))
    cur = _snapshot(d)
    check("[CP1-T01] (fixture sanity) a/huge.txt fits under MAX_DIFF alone but not together with the edit",
          huge_target <= budget and huge_target + len(edit_chunk) > budget, (huge_target, budget, len(edit_chunk)))
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    check("[CP1-T01] shown == ['z/edit.py'] and omitted == ['a/huge.txt']",
          shown == ["z/edit.py"] and omitted == ["a/huge.txt"], (shown, omitted))
    check("[CP1-T01] z/edit.py's full chunk is present verbatim and ZQ-T01-EDIT is present",
          edit_chunk in text and "ZQ-T01-EDIT" in text, "")
    check("[CP1-T01] no body content of a/huge.txt (its ZQ-PAD marker) appears anywhere",
          "ZQ-PAD" not in text, "")
    check("[CP1-T01] the note names exactly a/huge.txt",
          "a/huge.txt" in _note_tail(text) and "z/edit.py" not in _note_tail(text), _note_tail(text))


def t02_small_deleted_file_shown_in_full_gets_room_before_a_big_new_file():
    d = _mk_repo("cp1t02")
    (d / "m").mkdir()
    (d / "m/gone.txt").write_text("goodbye\n")
    since = _snapshot(d)
    (d / "m/gone.txt").unlink()
    gone_only = _snapshot(d)
    gone_chunk = _part_for(_git_m(d, since, gone_only), "m/gone.txt")
    budget = _budget(since, gone_only)
    huge_target = budget - len(gone_chunk) + 1
    pure_gone = ER._pure_add_delete(gone_chunk)
    check("[CP1-T02] (fixture sanity) m/gone.txt's chunk is a textbook pure delete whose summary would not "
          "be shorter, so it is never summarized",
          bool(pure_gone) and pure_gone[0] == "deleted"
          and len(ER._summarize_pure(gone_chunk, *pure_gone)) >= len(gone_chunk), gone_chunk)
    (d / "a").mkdir()
    (d / "a/huge.txt").write_text(_sized_add_content("a/huge.txt", huge_target))
    cur = _snapshot(d)
    check("[CP1-T02] (fixture sanity) a/huge.txt fits alone but not together with m/gone.txt's chunk",
          huge_target <= budget and huge_target + len(gone_chunk) > budget, (huge_target, budget, len(gone_chunk)))
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    check("[CP1-T02] shown == ['m/gone.txt'] and omitted == ['a/huge.txt']",
          shown == ["m/gone.txt"] and omitted == ["a/huge.txt"], (shown, omitted))
    check("[CP1-T02] m/gone.txt's part is its full git delete chunk verbatim",
          gone_chunk in text, text[:300])
    check("[CP1-T02] no body content of a/huge.txt appears and the note names exactly a/huge.txt",
          "ZQ-PAD" not in text and "a/huge.txt" in _note_tail(text) and "m/gone.txt" not in _note_tail(text),
          _note_tail(text))


def t03_big_deleted_file_starting_summarized_gets_room_before_a_big_new_file():
    d = _mk_repo("cp1t03")
    (d / "m").mkdir()
    (d / "m/old_data.txt").write_text(_lines("row-", 2500, width=9))
    since = _snapshot(d)
    (d / "m/old_data.txt").unlink()
    del_only = _snapshot(d)
    full_chunk = _part_for(_git_m(d, since, del_only), "m/old_data.txt")
    pure = ER._pure_add_delete(full_chunk)
    check("[CP1-T03] (fixture sanity) m/old_data.txt's chunk is a textbook pure delete",
          bool(pure) and pure[0] == "deleted", full_chunk[:200])
    summary = ER._summarize_pure(full_chunk, *pure)
    check("[CP1-T03] (fixture sanity) the summary is shorter than the full chunk, so it starts summarized",
          len(summary) < len(full_chunk), (len(summary), len(full_chunk)))
    budget = _budget(since, del_only)
    huge_target = budget - len(summary) + 1
    (d / "a").mkdir()
    (d / "a/huge.txt").write_text(_sized_add_content("a/huge.txt", huge_target))
    cur = _snapshot(d)
    check("[CP1-T03] (fixture sanity) a/huge.txt fits alone but not together with even the one-line summary",
          huge_target <= budget and huge_target + len(summary) > budget, (huge_target, budget, len(summary)))
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    check("[CP1-T03] m/old_data.txt is shown and a/huge.txt is omitted",
          "m/old_data.txt" in shown and "m/old_data.txt" not in omitted
          and "a/huge.txt" in omitted and "a/huge.txt" not in shown, (shown, omitted))
    check("[CP1-T03] m/old_data.txt's part is either its full chunk verbatim or the header lines plus the "
          "one-line summary",
          (full_chunk in text) or (summary in text), text[:300])
    check("[CP1-T03] no body content of a/huge.txt appears and the note names exactly a/huge.txt",
          "ZQ-PAD" not in text and "a/huge.txt" in _note_tail(text) and "m/old_data.txt" not in _note_tail(text),
          _note_tail(text))


def t04_competing_new_files_smallest_in_first_then_smaller_ones_after_the_cut_one():
    d = _mk_repo("cp1t04")
    (d / "z").mkdir()
    before = _lines("EDIT4-", 700, width=20)
    (d / "z/edit.py").write_text(before)
    since = _snapshot(d)
    after = _edit_block(before, 300, 80, " ZQ-T04-EDIT")
    (d / "z/edit.py").write_text(after)
    edit_only = _snapshot(d)
    edit_chunk = _part_for(_git_m(d, since, edit_only), "z/edit.py")
    ez = len(edit_chunk)
    budget = _budget(since, edit_only)
    mid, small1, small2 = 60000, 10000, 8000
    big = budget - mid - small1 - ez
    check("[CP1-T04] (fixture sanity) the computed b/big.txt size is well over the three smaller targets "
          "put together (it is meant to be the biggest)",
          big > mid and big > 0, (big, mid, small1, small2, ez))
    tags = {"big.txt": "ZQ-T04-BIG-", "mid.txt": "ZQ-T04-MID-", "small1.txt": "ZQ-T04-SMALL1-",
            "small2.txt": "ZQ-T04-SMALL2-"}
    for b, d2, dd in (("b", "big.txt", big), ("c", "mid.txt", mid), ("d", "small1.txt", small1),
                      ("e", "small2.txt", small2)):
        (d / b).mkdir()
        (d / b / d2).write_text(_sized_add_content(f"{b}/{d2}", dd, tag=tags[d2]))
    cur = _snapshot(d)
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    check("[CP1-T04] omitted == ['b/big.txt'] exactly",
          omitted == ["b/big.txt"], omitted)
    check("[CP1-T04] shown == ['c/mid.txt', 'd/small1.txt', 'e/small2.txt', 'z/edit.py'] in git order",
          shown == ["c/mid.txt", "d/small1.txt", "e/small2.txt", "z/edit.py"], shown)
    for p in ("c/mid.txt", "d/small1.txt", "e/small2.txt"):
        check(f"[CP1-T04] {p}'s full chunk is present verbatim",
              p in shown and tags[p.split('/')[1]] in _part_for(text, p), "")
    check("[CP1-T04] no body content of b/big.txt appears anywhere", tags["big.txt"] not in text, "")
    check("[CP1-T04] ZQ-T04-EDIT is present and the note names only b/big.txt",
          "ZQ-T04-EDIT" in text and _note_tail(text).strip().rstrip("]").endswith("b/big.txt"), _note_tail(text))


# ================================================================================================ CP1-T05, T06

def t05_shown_files_still_print_in_gits_own_order():
    d = _mk_repo("cp1t05")
    (d / "z").mkdir()
    (d / "m").mkdir()
    before = _lines("EDIT5-", 700, width=20)
    (d / "z/edit.py").write_text(before)
    (d / "m/gone.txt").write_text("bye bye\n")
    since = _snapshot(d)
    after = _edit_block(before, 300, 80, " ZQ-T05-EDIT")
    (d / "z/edit.py").write_text(after)
    (d / "m/gone.txt").unlink()
    mid_only = _snapshot(d)
    real_mid = _git_m(d, since, mid_only)
    edit_chunk = _part_for(real_mid, "z/edit.py")
    gone_chunk = _part_for(real_mid, "m/gone.txt")
    ez, g = len(edit_chunk), len(gone_chunk)
    budget = _budget(since, mid_only)
    mid, small1, small2 = 60000, 10000, 8000
    big = budget - mid - small1 - (ez + g)
    check("[CP1-T05] (fixture sanity) b/big.txt is sized well bigger than the other new files",
          big > mid and big > 0, (big, ez, g))
    tags5 = {"big.txt": "ZQ-T05-BIG-", "mid.txt": "ZQ-T05-MID-", "small1.txt": "ZQ-T05-SMALL1-",
             "small2.txt": "ZQ-T05-SMALL2-"}
    for b, d2, dd in (("b", "big.txt", big), ("c", "mid.txt", mid), ("d", "small1.txt", small1),
                      ("e", "small2.txt", small2)):
        (d / b).mkdir()
        (d / b / d2).write_text(_sized_add_content(f"{b}/{d2}", dd, tag=tags5[d2]))
    cur = _snapshot(d)
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    expected = ["c/mid.txt", "d/small1.txt", "e/small2.txt", "m/gone.txt", "z/edit.py"]
    check("[CP1-T05] shown == the expected five files, in git's own order",
          shown == expected, shown)
    check("[CP1-T05] omitted == ['b/big.txt']", omitted == ["b/big.txt"], omitted)
    check("[CP1-T05] no body content of b/big.txt appears anywhere", tags5["big.txt"] not in text, "")
    idxs = [_idx(text, p) for p in expected]
    check("[CP1-T05] in the text, each shown file's 'diff --git' line appears strictly after the previous "
          "one's -- git's own print order, not the order they were fitted",
          all(i != -1 for i in idxs) and idxs == sorted(idxs), idxs)
    check("[CP1-T05] the text starts with the scoped header and the note comes after the last shown chunk",
          text.startswith(t1._scoped_header(since, cur))
          and text.find("diff too large") > idxs[-1], (idxs, text.find("diff too large")))


def t06_whole_diff_that_fits_is_printed_exactly_as_git_prints_it():
    d = _mk_repo("cp1t06")
    (d / "r").mkdir()
    (d / "m").mkdir()
    (d / "q").mkdir()
    (d / "r/old.py").write_text("\n".join(f"line{i}" for i in range(20)) + "\n")
    (d / "m/mod.py").write_text("a = 1\nb = 2\n")
    (d / "q/gone.txt").write_text("temp\n")
    since = _snapshot(d)
    (d / "r/old.py").unlink()
    (d / "y").mkdir()
    old_lines = [f"line{i}" for i in range(20)]
    old_lines[10] = "line10  # ZQ-T06-RENAME"
    (d / "y/moved.py").write_text("\n".join(old_lines) + "\n")
    (d / "m/mod.py").write_text("a = 11\nb = 2\n")
    (d / "q/gone.txt").unlink()
    (d / "a").mkdir()
    (d / "a/new.txt").write_text("brand new\n")
    cur = _snapshot(d)
    real = _git_m(d, since, cur)
    header = t1._scoped_header(since, cur)
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    check("[CP1-T06] text equals the scoped header plus git's own -M diff-tree output, byte for byte",
          text == header + real, (len(text), len(header + real)))
    expected_shown = [f for f, _c in ER._diff_file_chunks(real)]
    check("[CP1-T06] shown is every changed path in git's order (the rename by its new name) and omitted "
          "is empty",
          shown == expected_shown and omitted == [], (shown, omitted, expected_shown))
    check("[CP1-T06] the rename shows as one chunk naming both the old and new path",
          "rename from r/old.py" in text and "rename to y/moved.py" in text and "ZQ-T06-RENAME" in text, "")
    check("[CP1-T06] no '[file deleted' summary line and no 'diff too large' note appear",
          "[file deleted" not in text and "diff too large" not in text, "")


# ================================================================================================ CP1-T07, T08

def t07_two_new_files_same_size_the_one_git_lists_first_wins_the_last_slot():
    d = _mk_repo("cp1t07")
    (d / "z").mkdir()
    before = _lines("EDIT7-", 60, width=20)
    (d / "z/edit.py").write_text(before)
    since = _snapshot(d)
    after = _edit_block(before, 20, 10, " ZQ-T07-EDIT")
    (d / "z/edit.py").write_text(after)
    edit_only = _snapshot(d)
    ez = len(_part_for(_git_m(d, since, edit_only), "z/edit.py"))
    budget = _budget(since, edit_only)
    rem = budget - ez
    t_size = rem // 2 + 1
    check("[CP1-T07] (fixture sanity) one of the two equal-size new files fits alongside the edit but "
          "both together do not",
          t_size <= rem and 2 * t_size > rem, (t_size, rem))
    (d / "a").mkdir()
    (d / "b").mkdir()
    (d / "a/one.txt").write_text(_sized_add_content("a/one.txt", t_size))
    (d / "b/two.txt").write_text(_sized_add_content("b/two.txt", t_size))
    cur = _snapshot(d)
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    check("[CP1-T07] shown == ['a/one.txt', 'z/edit.py'] and omitted == ['b/two.txt'] (a size tie keeps "
          "git's order, as the full-round packer does)",
          shown == ["a/one.txt", "z/edit.py"] and omitted == ["b/two.txt"], (shown, omitted))
    check("[CP1-T07] ZQ-T07-EDIT is present", "ZQ-T07-EDIT" in text, "")


def t08_moved_and_edited_file_counts_as_a_real_change_ahead_of_new_files():
    d = _mk_repo("cp1t08")
    (d / "m").mkdir()
    # A big (but still under git's 50% rename-similarity threshold) contiguous edit, so the rename+edit
    # chunk is itself over half the budget. That is what makes this fixture actually discriminate: a
    # competing new file can be sized SMALLER than the rename chunk (so a bug that sorted the rename among
    # the pure adds by size would pick the smaller file first) and still, together with the rename chunk,
    # be too big to fit -- unlike a new file sized near the whole budget, which would be picked last by
    # size either way and so would pass this test whether or not the rename is misclassified.
    base_lines = [f"orig{i:04d} " + ("padding " * 4) for i in range(3000)]
    (d / "m/old.py").write_text("\n".join(base_lines) + "\n")
    since = _snapshot(d)
    (d / "m/old.py").unlink()
    (d / "y").mkdir()
    moved_text = _edit_block("\n".join(base_lines) + "\n", 50, 1300, " ZQ-T08-MOVED")
    (d / "y/moved.py").write_text(moved_text)
    moved_only = _snapshot(d)
    rename_chunk = _part_for_rename(_git_m(d, since, moved_only), "m/old.py", "y/moved.py")
    check("[CP1-T08] (fixture sanity) git pairs the move as one rename chunk",
          "rename from m/old.py" in rename_chunk and "rename to y/moved.py" in rename_chunk, rename_chunk[:300])
    pure = ER._pure_add_delete(rename_chunk)
    check("[CP1-T08] (fixture sanity) the rename+edit chunk is not classified as a pure add",
          not (pure and pure[0] == "added"), pure)
    budget = _budget(since, moved_only)
    check("[CP1-T08] (fixture sanity) the rename chunk fits the budget alone and is itself over half of "
          "it, so a file smaller than it can still be too big to fit alongside it",
          len(rename_chunk) <= budget and len(rename_chunk) * 2 > budget, (len(rename_chunk), budget))
    # The only range that is both smaller than the rename chunk and, combined with it, over budget; its
    # midpoint is always valid once the rename chunk is itself over half the budget (asserted above).
    lower = budget - len(rename_chunk) + 1
    upper = len(rename_chunk) - 1
    huge_target = (lower + upper) // 2
    (d / "a").mkdir()
    (d / "a/huge.txt").write_text(_sized_add_content("a/huge.txt", huge_target))
    cur = _snapshot(d)
    check("[CP1-T08] (fixture sanity) a/huge.txt is smaller than the rename chunk but, together with it, "
          "still over budget",
          huge_target < len(rename_chunk) and huge_target + len(rename_chunk) > budget,
          (huge_target, len(rename_chunk), budget))
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    check("[CP1-T08] y/moved.py is shown and a/huge.txt is omitted",
          "y/moved.py" in shown and "y/moved.py" not in omitted
          and "a/huge.txt" in omitted and "a/huge.txt" not in shown, (shown, omitted))
    check("[CP1-T08] y/moved.py's part is its full rename chunk verbatim, naming the old path",
          rename_chunk in text and "rename from m/old.py" in text, text[:300])
    check("[CP1-T08] no body content of a/huge.txt appears and the note names exactly a/huge.txt",
          "ZQ-PAD" not in text and "a/huge.txt" in _note_tail(text) and "y/moved.py" not in _note_tail(text),
          _note_tail(text))


# ================================================================================================ CP1-T09..T11

def t09_over_the_limit_with_no_new_files_real_changes_still_fit_in_gits_order():
    d = _mk_repo("cp1t09")
    (d / "a").mkdir()
    (d / "b").mkdir()
    (d / "c").mkdir()
    before_a = _lines("MODA-", 2050, width=20)
    before_b = _lines("MODB-", 1900, width=20)
    before_c = _lines("MODC-", 80, width=20)
    (d / "a/mod1.py").write_text(before_a)
    (d / "b/mod2.py").write_text(before_b)
    (d / "c/mod3.py").write_text(before_c)
    since = _snapshot(d)
    (d / "a/mod1.py").write_text(_edit_block(before_a, 50, 2000, " ZQ-T09-A"))
    (d / "b/mod2.py").write_text(_edit_block(before_b, 50, 1850, " ZQ-T09-B"))
    (d / "c/mod3.py").write_text(_edit_block(before_c, 10, 60, " ZQ-T09-C"))
    cur = _snapshot(d)
    real = _git_m(d, since, cur)
    a_c, b_c, c_c = _part_for(real, "a/mod1.py"), _part_for(real, "b/mod2.py"), _part_for(real, "c/mod3.py")
    budget = _budget(since, cur)
    check("[CP1-T09] (fixture sanity) a/mod1.py fits, a/mod1.py + b/mod2.py does not, and a/mod1.py + "
          "c/mod3.py still does",
          len(a_c) <= budget and len(a_c) + len(b_c) > budget and len(a_c) + len(c_c) <= budget,
          (len(a_c), len(b_c), len(c_c), budget))
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    check("[CP1-T09] shown == ['a/mod1.py', 'c/mod3.py'] and omitted == ['b/mod2.py']",
          shown == ["a/mod1.py", "c/mod3.py"] and omitted == ["b/mod2.py"], (shown, omitted))
    check("[CP1-T09] ZQ-T09-A and ZQ-T09-C are present, ZQ-T09-B is absent",
          "ZQ-T09-A" in text and "ZQ-T09-C" in text and "ZQ-T09-B" not in text, "")


def t10_new_file_too_big_to_fit_at_all_is_omitted_and_the_edit_shows():
    d = _mk_repo("cp1t10")
    (d / "z").mkdir()
    before = _lines("EDIT10-", 60, width=20)
    (d / "z/edit.py").write_text(before)
    since = _snapshot(d)
    (d / "z/edit.py").write_text(_edit_block(before, 10, 10, " ZQ-T10-EDIT"))
    (d / "a").mkdir()
    (d / "a/enormous.txt").write_text(_sized_add_content("a/enormous.txt", ER.MAX_DIFF + 50000))
    cur = _snapshot(d)
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    check("[CP1-T10] shown == ['z/edit.py'] and omitted == ['a/enormous.txt']",
          shown == ["z/edit.py"] and omitted == ["a/enormous.txt"], (shown, omitted))
    check("[CP1-T10] ZQ-T10-EDIT is present and no body content of a/enormous.txt (not even a prefix) appears",
          "ZQ-T10-EDIT" in text and "ZQ-PAD" not in text, "")


def t11_real_change_fits_leaving_modest_room_smaller_new_file_gets_it():
    d = _mk_repo("cp1t11")
    (d / "a").mkdir()
    before = _lines("MOD11-", 3000, width=20)
    (d / "a/mod1.py").write_text(before)
    since = _snapshot(d)
    after = _edit_block(before, 10, 2970, " ZQ-T11-MOD")
    (d / "a/mod1.py").write_text(after)
    mod_only = _snapshot(d)
    mod1_chunk = _part_for(_git_m(d, since, mod_only), "a/mod1.py")
    budget = _budget(since, mod_only)
    check("[CP1-T11] (fixture sanity) a/mod1.py's own chunk fits but leaves only modest room behind it",
          len(mod1_chunk) <= budget and budget - len(mod1_chunk) < 20000, (len(mod1_chunk), budget))
    remaining = budget - len(mod1_chunk)
    tiny_target = max(200, remaining // 4)
    medium_target = remaining * 3
    (d / "b").mkdir()
    (d / "c").mkdir()
    (d / "b/tiny.txt").write_text(_sized_add_content("b/tiny.txt", tiny_target, tag="ZQ-T11-TINY-"))
    (d / "c/medium.txt").write_text(_sized_add_content("c/medium.txt", medium_target, tag="ZQ-T11-MEDIUM-"))
    cur = _snapshot(d)
    check("[CP1-T11] (fixture sanity) b/tiny.txt fits in what is left after a/mod1.py but c/medium.txt does not",
          tiny_target <= remaining and medium_target > remaining, (tiny_target, medium_target, remaining))
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    check("[CP1-T11] shown == ['a/mod1.py', 'b/tiny.txt'] and omitted == ['c/medium.txt']",
          shown == ["a/mod1.py", "b/tiny.txt"] and omitted == ["c/medium.txt"], (shown, omitted))
    check("[CP1-T11] ZQ-T11-MOD and ZQ-T11-TINY- are present, no body content of c/medium.txt appears",
          "ZQ-T11-MOD" in text and "ZQ-T11-TINY-" in text and "ZQ-T11-MEDIUM-" not in text, "")


# ================================================================================================ main

CASES = [
    ("CP1-T01", t01_huge_add_printed_first_no_longer_pushes_out_a_small_edit),
    ("CP1-T02", t02_small_deleted_file_shown_in_full_gets_room_before_a_big_new_file),
    ("CP1-T03", t03_big_deleted_file_starting_summarized_gets_room_before_a_big_new_file),
    ("CP1-T04", t04_competing_new_files_smallest_in_first_then_smaller_ones_after_the_cut_one),
    ("CP1-T05", t05_shown_files_still_print_in_gits_own_order),
    ("CP1-T06", t06_whole_diff_that_fits_is_printed_exactly_as_git_prints_it),
    ("CP1-T07", t07_two_new_files_same_size_the_one_git_lists_first_wins_the_last_slot),
    ("CP1-T08", t08_moved_and_edited_file_counts_as_a_real_change_ahead_of_new_files),
    ("CP1-T09", t09_over_the_limit_with_no_new_files_real_changes_still_fit_in_gits_order),
    ("CP1-T10", t10_new_file_too_big_to_fit_at_all_is_omitted_and_the_edit_shows),
    ("CP1-T11", t11_real_change_fits_leaving_modest_room_smaller_new_file_gets_it),
]


def main():
    for cid, fn in CASES:
        print(f"{cid}: {fn.__name__}")
        try:
            fn()
        except Exception:
            check(f"[{cid}] ran to the end without an unexpected error", False, traceback.format_exc())
    for cid in PLANNED:
        if cid not in CASE_STATUS:
            check(f"[{cid}] recorded at least one check", False, "no check ran for this case")
    print(f"\n{PASS} passed, {FAIL} failed")
    rep = os.environ.get("RATCHET_TEST_REPORT")
    if rep:
        cases = [{"id": c, "status": "passed" if ok else "failed",
                  "message": "; ".join(CASE_MESSAGES.get(c, []))[:2000]}
                 for c, ok in sorted(CASE_STATUS.items())]
        Path(rep).write_text(json.dumps({"cases": cases}, indent=1))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
