#!/usr/bin/env python3
"""
CP1 fix-round regression tests (adversarial round 1).   python3 tests/test_review_diff_large_files_fixes.py

  [CP1-X01] a diff that fits whole under MAX_DIFF, with many tiny new files and a real modification, is returned
            exactly as git printed it (no summary anywhere, nothing omitted) -- the summaries must never push a
            diff that fits over the limit (findings A1, X1).
  [CP1-X02] the same with one short new file and a real modification, in both git orders (finding X1).
  [CP1-X03] in a diff that is over MAX_DIFF, a tiny pure add whose summary would be LONGER than its own chunk is
            never summarized, so it cannot push a real modification out (finding A1).
  [CP1-X04] a big file moved AND edited in the same round is never shown as a pure delete plus a pure add summary
            with the edit hidden: the edit reaches the reviewer, or the file is omitted and blocks (finding A3).

Uses the planned file's repo helpers (tests/test_review_diff_large_files.py) and calls scoped_diff_parts directly.
"""
from __future__ import annotations

import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_review_diff_large_files as base  # noqa: E402  (ER, _mk_repo, _snapshot, _real_diff, _scoped_header)

ER = base.ER
PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'OK ' if cond else 'FAIL'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


def _fit_mod_to(d, since_files, cur_files, mod_path, target_total):
    """Writes the `since` and `cur` trees (dicts path -> content) and grows mod_path's new one-line body until
    len(header) + len(raw diff) == target_total. Returns (since, cur, raw)."""
    def build(pad):
        for p in list(d.iterdir()):
            if p.name != ".git":
                _rm(p)
        for p, c in since_files.items():
            _put(d / p, c)
        since = base._snapshot(d)
        for p in list(d.iterdir()):
            if p.name != ".git":
                _rm(p)
        for p, c in cur_files.items():
            _put(d / p, c)
        _put(d / mod_path, "new " + "m" * pad + "\n")
        cur = base._snapshot(d)
        raw = base._real_diff(d, since, cur)
        return since, cur, raw
    since, cur, raw = build(10)
    pad = 10 + target_total - (len(base._scoped_header(since, cur)) + len(raw))
    since, cur, raw = build(pad)
    assert len(base._scoped_header(since, cur)) + len(raw) == target_total, (len(raw), target_total)
    return since, cur, raw


def _put(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _rm(path):
    if path.is_dir():
        for c in path.iterdir():
            _rm(c)
        path.rmdir()
    else:
        path.unlink()


def x01_many_tiny_adds_and_a_mod_that_fit_are_unchanged():
    d = base._mk_repo("x01")
    tiny = {f"pkg/m{i:02d}/__init__.py": f"x{i}\n" for i in range(40)}
    since, cur, raw = _fit_mod_to(d, {"z_mod.py": "old\n"}, tiny, "z_mod.py", ER.MAX_DIFF - 200)
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    check("[CP1-X01] (fixture sanity) the whole diff plus header is just under MAX_DIFF",
          ER.MAX_DIFF - 400 < len(base._scoped_header(since, cur)) + len(raw) <= ER.MAX_DIFF, len(raw))
    check("[CP1-X01] text is exactly header + git's raw diff (no summary, no note)",
          text == base._scoped_header(since, cur) + raw, text[-300:])
    check("[CP1-X01] omitted is empty and all 41 files are shown",
          omitted == [] and len(shown) == 41 and "z_mod.py" in shown, (len(shown), omitted))


def x02_short_add_and_a_mod_fit_in_both_orders():
    for label, add_path, mod_path in (("add first", "a_new.py", "z_mod.py"), ("mod first", "z_new.py", "a_mod.py")):
        d = base._mk_repo("x02")
        since, cur, raw = _fit_mod_to(d, {mod_path: "old\n"}, {add_path: "x\n"}, mod_path, ER.MAX_DIFF - 20)
        text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
        check(f"[CP1-X02] {label}: text is exactly header + git's raw diff",
              text == base._scoped_header(since, cur) + raw, text[-300:])
        check(f"[CP1-X02] {label}: both files shown, omitted empty",
              sorted(shown) == sorted([add_path, mod_path]) and omitted == [], (shown, omitted))


def x03_tiny_adds_never_summarized_when_the_summary_is_longer():
    d = base._mk_repo("x03")
    big = "".join(f"row-{i:05d} padding padding\n" for i in range(40000))
    tiny = {f"a/m{i:02d}/__init__.py": f"x{i}\n" for i in range(40)}
    # the target leaves room for the delete's summary (~200 chars), not for 40 tiny summaries (~90 more each)
    d0 = base._mk_repo("x03probe")
    _put(d0 / "data/big.txt", big)
    s0 = base._snapshot(d0)
    (d0 / "data/big.txt").unlink()
    c0 = base._snapshot(d0)
    big_chunk = len(base._real_diff(d0, s0, c0))
    since, cur, raw = _fit_mod_to(d, {"data/big.txt": big, "z_mod.py": "old\n"}, tiny, "z_mod.py",
                                  ER.MAX_DIFF - 1500 + big_chunk)
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    check("[CP1-X03] (fixture sanity) the whole diff is over MAX_DIFF",
          len(base._scoped_header(since, cur)) + len(raw) > ER.MAX_DIFF, len(raw))
    check("[CP1-X03] omitted is empty: z_mod.py is shown", omitted == [] and "z_mod.py" in shown, omitted)
    check("[CP1-X03] data/big.txt is summarized (its delete line is there, no ZQ body)",
          "data/big.txt" in shown and "[file deleted; 40000 lines" in text and "row-00001 padding" not in text,
          text[-300:])
    check("[CP1-X03] no tiny add is summarized: no '[file added' line, every tiny body line is present",
          "[file added" not in text and all(f"+x{i}\n" in text for i in range(40)), "")
    check("[CP1-X03] the text is at most MAX_DIFF characters", len(text) <= ER.MAX_DIFF, len(text))


def x04_move_plus_edit_never_hides_the_edit():
    d = base._mk_repo("x04")
    lines = [f"v_{i} = {i}  # padding padding padding padding padding" for i in range(3000)]
    _put(d / "old_mod.py", "\n".join(lines) + "\n")
    since = base._snapshot(d)
    (d / "old_mod.py").unlink()
    lines[1500] = "os.system(x)  # ZQ-HIDDEN " + "p" * 80  # longer than the line it replaces: the add is the bigger side
    _put(d / "new_mod.py", "\n".join(lines) + "\n")
    cur = base._snapshot(d)
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    check("[CP1-X04] (fixture sanity) delete + add without rename detection would be over MAX_DIFF",
          len(base._real_diff(d, since, cur)) > ER.MAX_DIFF, "")
    visible = "+os.system(x)  # ZQ-HIDDEN" in text
    check("[CP1-X04] the hidden edit is shown to the reviewer, or new_mod.py is omitted (and blocks)",
          visible or "new_mod.py" in omitted, (shown, omitted, text[-300:]))
    check("[CP1-X04] new_mod.py is never presented as a clean pure-add summary",
          not re.search(r"(?m)^\+\+\+ b/new_mod\.py\n\[file added", text), text[-300:])
    check("[CP1-X04] git reports it as one move (rename from/to) with the edit in full",
          "rename from old_mod.py" in text and "rename to new_mod.py" in text and visible
          and shown == ["new_mod.py"] and omitted == [], (shown, omitted))


CASES = [x01_many_tiny_adds_and_a_mod_that_fit_are_unchanged, x02_short_add_and_a_mod_fit_in_both_orders,
         x03_tiny_adds_never_summarized_when_the_summary_is_longer, x04_move_plus_edit_never_hides_the_edit]


# ---- adversarial round 2 (finding B1, A4), tightened in round 3 (finding A6): a pure add is never summarized
#
#   [CP1-X05] a big file moved with 70% of its lines rewritten (below git's -M threshold, so a pure delete plus a
#             pure add) and an injected line, over MAX_DIFF: the injected line is shown, or the add is omitted.
#   [CP1-X06] the same with the moved file 100% rewritten (an entirely different file at the new path).
#   [CP1-X07] a large pure delete and an unrelated large pure add in one round: the add is never summarized; it
#             is omitted and named, so the round still blocks (the intended cost of the rule).
#   [CP1-X08] a lone large pure add with no deletion anywhere is never summarized: it is omitted and blocks.
#   [CP1-X09] a small move plus edit that fits is git's own -M output, shown == [new path] (finding A4).
#   [CP1-X10] a moved-and-edited file (a rename chunk) plus a large pure add: the add is never summarized.
#   [CP1-X11] a module split (finding A6): utils.py is MODIFIED down to a 10-line stub plus an import, not deleted,
#             and its other lines move into a new helpers.py with one line changed to an injected os.system call,
#             1800 lines, over MAX_DIFF: the injected line is shown, or helpers.py is omitted (and blocks).
#   [CP1-X12] the same module split with 3000 lines.

def _diff_m(d, since, cur):
    return base._git(d, "diff-tree", "-r", "-p", "-M", "--no-color", since, cur)


def _assert_add_not_hidden(cid, d, since, cur, add_path, marker):
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    check(f"[{cid}] (fixture sanity) git does not pair the two sides as a rename",
          "rename from" not in _diff_m(d, since, cur), "")
    check(f"[{cid}] (fixture sanity) the whole diff is over MAX_DIFF",
          len(base._scoped_header(since, cur)) + len(_diff_m(d, since, cur)) > ER.MAX_DIFF, "")
    visible = marker in text
    check(f"[{cid}] the injected line is shown to the reviewer, or {add_path} is omitted (and blocks)",
          visible or add_path in omitted, (shown, omitted, text[-300:]))
    check(f"[{cid}] {add_path} is never presented as a pure-add summary",
          "[file added" not in text, text[-300:])
    check(f"[{cid}] never both: omitted empty and the injected line absent",
          not (omitted == [] and not visible), (shown, omitted))
    return text, shown, omitted


def _moved_rewritten(cid, rewrite_every):
    d = base._mk_repo(cid.lower())
    lines = [f"v_{i} = {i}  # padding padding padding padding padding" for i in range(4000)]
    _put(d / "old_mod.py", "\n".join(lines) + "\n")
    since = base._snapshot(d)
    (d / "old_mod.py").unlink()
    new = [f"w_{i} = compute({i}, 'rewritten rewritten rewritten')" if rewrite_every(i) else l
           for i, l in enumerate(lines)]
    new[1500] = "os.system(x)  # ZQ-HIDDEN"
    _put(d / "new_mod.py", "\n".join(new) + "\n")
    cur = base._snapshot(d)
    return d, since, cur


def x05_move_rewritten_70_percent_never_hides_the_add():
    d, since, cur = _moved_rewritten("CP1-X05", lambda i: i % 10 < 7)
    _assert_add_not_hidden("CP1-X05", d, since, cur, "new_mod.py", "+os.system(x)  # ZQ-HIDDEN")


def x06_move_rewritten_100_percent_never_hides_the_add():
    d, since, cur = _moved_rewritten("CP1-X06", lambda i: True)
    _assert_add_not_hidden("CP1-X06", d, since, cur, "new_mod.py", "+os.system(x)  # ZQ-HIDDEN")


def x07_unrelated_large_delete_and_add_fall_back_to_blocking():
    d = base._mk_repo("x07")
    _put(d / "data/old_dump.txt", "".join(f"row-{i:05d} old dump padding padding\n" for i in range(40000)))
    _put(d / "src/app.py", "a\nb\nc\n")
    since = base._snapshot(d)
    (d / "data/old_dump.txt").unlink()
    _put(d / "vendor/new_blob.js", "".join(f"var b{i} = {i}; // ZQ-BLOB padding\n" for i in range(30000)))
    _put(d / "src/app.py", "a\nCHANGED  # ZQ-REAL-FIX\nc\n")
    cur = base._snapshot(d)
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    check("[CP1-X07] the large add is omitted and named in the NOT shown note",
          omitted == ["vendor/new_blob.js"] and "NOT shown" in text and "vendor/new_blob.js" in text.split("NOT shown")[-1],
          (shown, omitted))
    check("[CP1-X07] no '[file added' summary appears", "[file added" not in text, text[-300:])
    check("[CP1-X07] the real modification and the delete (summarized) are shown",
          "src/app.py" in shown and "ZQ-REAL-FIX" in text and "data/old_dump.txt" in shown
          and "[file deleted; 40000 lines" in text, (shown, omitted))
    check("[CP1-X07] the text is at most MAX_DIFF characters", len(text) <= ER.MAX_DIFF, len(text))


def x08_lone_large_add_is_never_summarized():
    d = base._mk_repo("x08")
    _put(d / "src/app.py", "a\nb\nc\n")
    since = base._snapshot(d)
    _put(d / "vendor/blob.js", "".join(f"var b{i} = {i}; // ZQ-BLOB padding\n" for i in range(30000)))
    _put(d / "src/app.py", "a\nCHANGED  # ZQ-REAL-FIX\nc\n")
    cur = base._snapshot(d)
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    check("[CP1-X08] src/app.py is shown and the lone large add is omitted",
          shown == ["src/app.py"] and omitted == ["vendor/blob.js"], (shown, omitted))
    check("[CP1-X08] the add is never summarized (no '[file added' line, no body) and the fix is shown",
          "[file added" not in text and "ZQ-BLOB" not in text and "ZQ-REAL-FIX" in text, text[-300:])


def x09_small_move_that_fits_is_gits_own_rename_output():
    d = base._mk_repo("x09")
    lines = [f"line {i}" for i in range(50)]
    _put(d / "a.py", "\n".join(lines) + "\n")
    since = base._snapshot(d)
    (d / "a.py").unlink()
    lines[20] = "line 20 edited  # ZQ-EDIT"
    _put(d / "b.py", "\n".join(lines) + "\n")
    cur = base._snapshot(d)
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    check("[CP1-X09] text is exactly header + git diff-tree -M output",
          text == base._scoped_header(since, cur) + _diff_m(d, since, cur), text[-300:])
    check("[CP1-X09] it is one rename chunk with the edit, shown == ['b.py'], omitted empty",
          "rename from a.py" in text and "+line 20 edited  # ZQ-EDIT" in text and shown == ["b.py"]
          and omitted == [], (shown, omitted))


def x10_rename_chunk_blocks_summarizing_an_add():
    d = base._mk_repo("x10")
    lines = [f"line {i}" for i in range(50)]
    _put(d / "a.py", "\n".join(lines) + "\n")
    since = base._snapshot(d)
    (d / "a.py").unlink()
    lines[20] = "line 20 edited"
    _put(d / "b.py", "\n".join(lines) + "\n")
    _put(d / "vendor/blob.js", "".join(f"var b{i} = {i}; // ZQ-BLOB padding\n" for i in range(30000)))
    cur = base._snapshot(d)
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    check("[CP1-X10] (fixture sanity) the move is one rename chunk", "rename from a.py" in text, text[:400])
    check("[CP1-X10] the large add is omitted, never summarized",
          omitted == ["vendor/blob.js"] and "[file added" not in text and "b.py" in shown, (shown, omitted))


CASES += [x05_move_rewritten_70_percent_never_hides_the_add, x06_move_rewritten_100_percent_never_hides_the_add,
          x07_unrelated_large_delete_and_add_fall_back_to_blocking, x08_lone_large_add_is_never_summarized,
          x09_small_move_that_fits_is_gits_own_rename_output, x10_rename_chunk_blocks_summarizing_an_add]


# ---- adversarial round 3 (finding A6): a module split hides nothing in the new module

def _module_split(cid, n):
    d = base._mk_repo(cid.lower())
    lines = [f"def f_{i}(x): return x + {i}  # padding padding padding padding" for i in range(n)]
    _put(d / "utils.py", "\n".join(lines) + "\n")
    since = base._snapshot(d)
    _put(d / "utils.py", "from helpers import *\n" + "\n".join(lines[:10]) + "\n")  # modified, not deleted
    moved = list(lines[10:])
    moved[len(moved) // 2] = "import os; os.system(x)  # ZQ-HIDDEN"
    _put(d / "helpers.py", "\n".join(moved) + "\n")
    cur = base._snapshot(d)
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    raw = _diff_m(d, since, cur)
    check(f"[{cid}] (fixture sanity) utils.py is modified, not deleted, and the whole diff is over MAX_DIFF",
          "deleted file mode" not in raw and "rename from" not in raw
          and len(base._scoped_header(since, cur)) + len(raw) > ER.MAX_DIFF, len(raw))
    visible = "+import os; os.system(x)  # ZQ-HIDDEN" in text
    check(f"[{cid}] the injected line is shown to the reviewer, or helpers.py is omitted (and blocks)",
          visible or "helpers.py" in omitted, (shown, omitted, text[-300:]))
    check(f"[{cid}] never both: omitted empty and the injected line absent",
          not (omitted == [] and not visible), (shown, omitted))
    check(f"[{cid}] helpers.py is never presented as a pure-add summary", "[file added" not in text, text[-300:])
    check(f"[{cid}] the text is at most MAX_DIFF characters", len(text) <= ER.MAX_DIFF, len(text))


def x11_module_split_1800_lines_never_hides_the_new_module():
    _module_split("CP1-X11", 1800)


def x12_module_split_3000_lines_never_hides_the_new_module():
    _module_split("CP1-X12", 3000)


CASES += [x11_module_split_1800_lines_never_hides_the_new_module,
          x12_module_split_3000_lines_never_hides_the_new_module]


def main():
    for fn in CASES:
        print(fn.__name__)
        try:
            fn()
        except Exception:
            check(f"[CP1-X] {fn.__name__} ran to the end without an unexpected error", False, traceback.format_exc())
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
