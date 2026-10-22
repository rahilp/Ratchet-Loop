#!/usr/bin/env python3
"""
CP2 fix-round regression tests (adversarial round 1).   python3 tests/test_review_diff_full_rounds_fixes.py

  [CP2-X01] (findings A5, X2) CP2-T16's later-round shape (its 2000-line fixture, which fits alone), with every
            check unconditional: the closing note is there, names only aaa-fixture.txt and never says 'shown', the
            'their diffs come first below' note is there, the order is note, src/app.py, aaa-fixture.txt,
            src/zz_big.py, and the budget is pinned byte for byte (every real change, src/zz_big.py from the rest
            included, is fitted whole first; the changed-since add gets what is left, cut on a whole line).
  [CP2-X02] (findings A3, X1) with the fixture fitting and with it cut, every 'diff --git' line in the output
            starts a line of its own and the output stays within MAX_DIFF; a cut fixture ends on a whole line and
            src/zz_big.py's whole chunk follows it.
  [CP2-X03] (finding A2) a mid-size pure delete next to a 1MB pure add that is cut: the delete fits, so it is shown
            in full, never summarized; the add is cut and named; in round 1 and in a later round.
  [CP2-X04] (finding A5) CP2-T12's hand-built disguises, checked strictly: no summary line for the disguised file,
            its own marker present whenever it sits inside the shown part, and the file named in the note.
  [CP2-X05] (finding X2) a priority (changed-since) add bigger than the room left: every real change, rest files
            included, is shown whole first; the add gets only the room left after them, cut on a whole line and
            named; in CP2-T16's shape with its original 1MB fixture, and in A1's shape (new module, small rest edits).

Uses tests/test_review_diff_full_rounds.py's helpers and calls collect_diff directly.
"""
from __future__ import annotations

import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_review_diff_full_rounds as fr  # noqa: E402  (ER, repo/content builders, _stub_full_diff)

ER = fr.ER
PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'OK ' if cond else 'FAIL'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


def _t16_shape(tag, n=2000):
    """CP2-T16's repo: src/zz_big.py rewritten in round 1; aaa-fixture.txt (`n` lines: 2000 is CP2-T16's own size,
    about 84K, which fits whole; 25000 is about 1MB, more than the whole budget) added and src/app.py changed in
    round 2. Returns (out, header, note, chunk map) for the later round."""
    d = fr._mk_repo(tag)
    (d / "src").mkdir()
    (d / "src/app.py").write_text("def run():\n    return 1\n")
    (d / "src/zz_big.py").write_text(fr._rewrite_body(1200, "ZZBIG"))
    base = fr._snapshot(d)
    (d / "src/zz_big.py").write_text(fr._rewrite_body_changed(1200, "ZZBIG", "MID-MOD-44e1"))
    round1 = fr._snapshot(d)
    (d / "aaa-fixture.txt").write_text(fr._fixture_text(n))
    (d / "src/app.py").write_text("def run():\n    return 2  # REAL-CHANGE-7c1e\n")
    round2 = fr._snapshot(d)
    out = ER.collect_diff(d, base, round2, prev_round_tree=round1)
    cm = fr._chunkmap(fr._real(d, base, round2))
    return out, fr._header(base, round2), fr._priority_note(["aaa-fixture.txt", "src/app.py"]), cm


def x01_t16_shape_checked_unconditionally_with_its_budget():
    out, header, note, cm = _t16_shape("cp2x01")
    app_c, fix_c, zz_c = cm["src/app.py"], cm["aaa-fixture.txt"], cm["src/zz_big.py"]
    check("[CP2-X01] (fixture sanity) the whole diff is over MAX_DIFF, so it is packed, and the fixture alone fits",
          len(header) + len(note) + len(app_c) + len(fix_c) + len(zz_c) > ER.MAX_DIFF
          and len(header) + len(note) + len(app_c) + len(fix_c) < ER.MAX_DIFF
          and len(header) + len(note) + len(app_c) + len(zz_c) < ER.MAX_DIFF, "")
    tail = fr._tail(out, "left out or cut short")
    check("[CP2-X01] the closing note is there, names only aaa-fixture.txt, and never says 'shown'",
          tail != "" and "aaa-fixture.txt" in tail and "src/zz_big.py" not in tail and "shown" not in tail.lower(),
          tail)
    check("[CP2-X01] the 'their diffs come first below' note is there", "their diffs come first below" in out, "")
    i_note = out.find("Files changed since the previous review round")
    i_app = out.find("diff --git a/src/app.py")
    i_fix = out.find("diff --git a/aaa-fixture.txt")
    i_zz = out.find("diff --git a/src/zz_big.py")
    check("[CP2-X01] the order is the note, src/app.py, aaa-fixture.txt, src/zz_big.py (every one present)",
          -1 not in (i_note, i_app, i_fix, i_zz) and i_note < i_app < i_fix < i_zz, (i_note, i_app, i_fix, i_zz))
    room = ER.MAX_DIFF - len(header) - len(note) - len(app_c) - len(zz_c)
    part = fix_c[:room]
    part = part[:part.rfind("\n") + 1]
    expected = header + note + app_c + part + zz_c + fr._cut_note(["aaa-fixture.txt"])
    check("[CP2-X01] budget: every real change (src/app.py, and src/zz_big.py from the rest) is shown whole; the "
          "changed-since add aaa-fixture.txt gets only the room left, cut back to a whole line, byte for byte",
          out == expected and part != "" and len(part) <= room, (len(out), len(expected)))


def x02_cut_add_followed_by_a_chunk_ends_on_a_whole_line():
    for n, label in ((2000, "fixture fits"), (25000, "fixture cut")):
        out, header, note, cm = _t16_shape(f"cp2x02-{n}", n)
        starts = [m.start() for m in re.finditer("diff --git ", out)]
        check(f"[CP2-X02] {label}: every 'diff --git' in the output starts a line of its own",
              starts and all(s > 0 and out[s - 1] == "\n" for s in starts),
              [out[max(0, s - 60):s + 30] for s in starts if s == 0 or out[s - 1] != "\n"][:2])
        tail = fr._tail(out, "\n[diff truncated")
        check(f"[CP2-X02] {label}: the output, without its closing note, is within MAX_DIFF",
              tail != "" and len(out) - len(tail) <= ER.MAX_DIFF, (len(out), len(tail)))
    # the cut fixture gets only the room left after every real change; src/zz_big.py is printed after it, so the
    # fixture ends on a whole line and nothing is glued onto its cut line
    i_fix = out.find("diff --git a/aaa-fixture.txt")
    i_zz = out.find("diff --git a/src/zz_big.py")
    shown = out[i_fix:i_zz] if -1 not in (i_fix, i_zz) and i_fix < i_zz else ""
    check("[CP2-X02] fixture cut: its shown part is a verbatim prefix of its chunk ending on a whole line, and "
          "src/zz_big.py's whole chunk follows it, then only the closing note",
          shown != "" and cm["aaa-fixture.txt"].startswith(shown) and shown.endswith("\n")
          and "diff --git" not in shown[1:] and out[i_zz:len(out) - len(tail)] == cm["src/zz_big.py"],
          shown[-80:])


def _auth_text(n=500):
    return "".join(f"def check_{i}(user):  # AUTHCHECK-{i:03d} legacy guard\n" for i in range(n))


def x03_delete_that_fits_is_shown_in_full_next_to_a_cut_add():
    d = fr._mk_repo("cp2x03")
    (d / "src").mkdir()
    (d / "src/app.py").write_text("def run():\n    return 1\n")
    (d / "src/auth_legacy.py").write_text(_auth_text())
    base = fr._snapshot(d)
    (d / "src/app.py").write_text("def run():\n    return 2  # REAL-CHANGE-7c1e\n")
    (d / "src/auth_legacy.py").unlink()
    (d / "zfixture.txt").write_text(fr._fixture_text(25000))
    round1 = fr._snapshot(d)
    (d / "src/app.py").write_text("def run():\n    return 3  # REAL-CHANGE-7c1e-v2\n")
    round2 = fr._snapshot(d)

    cm1 = fr._chunkmap(fr._real(d, base, round1))
    check("[CP2-X03] (fixture sanity) src/auth_legacy.py is a textbook pure delete whose summary is shorter",
          ER._pure_add_delete(cm1["src/auth_legacy.py"]) == ("deleted", 500)
          and len(fr._summary_for(cm1["src/auth_legacy.py"])) < len(cm1["src/auth_legacy.py"]), "")
    header1 = fr._header(base, round1)
    app1, auth1, fix1 = cm1["src/app.py"], cm1["src/auth_legacy.py"], cm1["zfixture.txt"]
    room1 = ER.MAX_DIFF - len(header1) - len(app1) - len(auth1)
    expected1 = header1 + app1 + auth1 + fix1[:room1] + fr._cut_note(["zfixture.txt"])
    out1 = ER.collect_diff(d, base, round1, None)

    cm2 = fr._chunkmap(fr._real(d, base, round2))
    header2 = fr._header(base, round2)
    note2 = fr._priority_note(["src/app.py"])
    app2, auth2, fix2 = cm2["src/app.py"], cm2["src/auth_legacy.py"], cm2["zfixture.txt"]
    room2 = ER.MAX_DIFF - len(header2) - len(note2) - len(app2) - len(auth2)
    expected2 = header2 + note2 + app2 + auth2 + fix2[:room2] + fr._cut_note(["zfixture.txt"])
    out2 = ER.collect_diff(d, base, round2, prev_round_tree=round1)

    for label, out, expected, auth_c in (("round 1", out1, expected1, auth1), ("later round", out2, expected2, auth2)):
        check(f"[CP2-X03] {label}: src/auth_legacy.py's full chunk (all 500 AUTHCHECK lines) is shown, with no "
              "'[file deleted' line",
              auth_c in out and out.count("AUTHCHECK-") == 500 and "[file deleted" not in out, out.count("AUTHCHECK-"))
        tail = fr._tail(out, "left out or cut short")
        check(f"[CP2-X03] {label}: zfixture.txt is cut and named, src/auth_legacy.py is not named",
              "zfixture.txt" in tail and "src/auth_legacy.py" not in tail, tail)
        check(f"[CP2-X03] {label}: output equals the header (and note), src/app.py, the full delete, the fixture "
              "cut at the room left, and the note, byte for byte",
              out == expected, (len(out), len(expected)))


def _disguised(name, path, n=25000):
    """The same five hand-built disguises CP2-T12 builds (its own helper is local to that test)."""
    if name == "x":
        lines = fr._delete_lines(path, n)
        lines[6 + 1] = "+SNEAKY-ADD-1d4e"
    elif name == "y":
        lines = fr._delete_lines(path, n) + ["@@ -25001,1 +0,0 @@", "-SNEAKY-HUNK-8f2a"]
    elif name == "z":
        lines = fr._add_lines(path, n)
        lines[6 + 1] = "-SNEAKY-MINUS-2e7a"
    elif name == "w":
        base = fr._delete_lines(path, n)
        base[6 + 1] = "-SNEAKY-COUNT-6b3d"
        lines = base + ["-extra-row-making-25001"]
    else:  # "v"
        base = fr._delete_lines(path, n)
        base[6 + 1] = "-SNEAKY-SIM-0a9f"
        lines = base[:1] + ["similarity index 100%"] + base[1:]
    return fr._text(lines)


def x04_hand_built_disguises_checked_strictly():
    markers = {"x": "SNEAKY-ADD-1d4e", "y": "SNEAKY-HUNK-8f2a", "z": "SNEAKY-MINUS-2e7a",
               "w": "SNEAKY-COUNT-6b3d", "v": "SNEAKY-SIM-0a9f"}
    paths = {"x": "data/x.txt", "y": "data/y.txt", "z": "vendor/z.js", "w": "data/w.txt", "v": "data/v.txt"}
    vendor_chunk = fr._text(fr._delete_lines("aaa-vendor.js", 25000))
    app_chunk = fr._mod_chunk("src/app.py", "    return 1", "    return 2  # REAL-CHANGE-7c1e")
    for name in "xyzwv":
        path, marker = paths[name], markers[name]
        disguised = _disguised(name, path)
        full_text = vendor_chunk + app_chunk + disguised
        with fr._stub_full_diff(full_text):
            out1 = ER.collect_diff(Path("/nonexistent-cp2-x04"), fr.BASE_ID, fr.CUR_ID, None)
        d, prev, cur = fr._app_repo(f"x04{name}")
        with fr._stub_full_diff(full_text):
            out2 = ER.collect_diff(d, "aaaaaaaaaa", cur, prev_round_tree=prev)
        for label, out in (("round 1", out1), ("later round", out2)):
            idx = out.find(f"diff --git a/{path}")
            end = out.find("\n[diff truncated", idx) if idx != -1 else -1
            shown = out[idx:end] if -1 not in (idx, end) else ""
            check(f"[CP2-X04] {name}/{label}: {path} is shown as a verbatim prefix of its own chunk",
                  shown != "" and disguised.startswith(shown), (idx, end))
            check(f"[CP2-X04] {name}/{label}: {path}'s shown part has no '[file deleted' or '[file added' line",
                  "[file deleted" not in shown and "[file added" not in shown, "")
            in_shown = disguised.find(marker) < len(shown)
            check(f"[CP2-X04] {name}/{label}: {marker} is in the output exactly when it sits inside the shown part "
                  f"({'inside' if in_shown else 'past the cut'})",
                  (marker in out) == in_shown, (in_shown, marker in out))
            if name != "y":
                check(f"[CP2-X04] {name}/{label}: {marker}, near the top of the chunk, is in the output",
                      marker in out, "")
            tail = fr._tail(out, "left out or cut short")
            check(f"[CP2-X04] {name}/{label}: {path} is named in the closing note", path in tail, tail)


def x05_priority_add_over_the_budget_is_cut_after_every_real_change():
    # CP2-T16's shape with the original 1MB fixture: the changed-since add is bigger than all the room left after
    # src/app.py, but src/zz_big.py (rest group; a real modification that fits) is shown whole ahead of it, and the
    # add gets only what is left after both, cut on a whole line and named
    out, header, note, cm = _t16_shape("cp2x05", 25000)
    app_c, fix_c, zz_c = cm["src/app.py"], cm["aaa-fixture.txt"], cm["src/zz_big.py"]
    room = ER.MAX_DIFF - len(header) - len(note) - len(app_c)
    check("[CP2-X05] (fixture sanity) the fixture is bigger than the room left after src/app.py, and src/zz_big.py "
          "alone would fit in that room",
          len(fix_c) > room and len(zz_c) < room, (len(fix_c), len(zz_c), room))
    part = fix_c[:room - len(zz_c)]
    part = part[:part.rfind("\n") + 1]
    expected = header + note + app_c + part + zz_c + fr._cut_note(["aaa-fixture.txt"])
    check("[CP2-X05] every real change is shown whole first: output equals the header, the note, src/app.py, "
          "aaa-fixture.txt cut back to a whole line within the room left after both real changes, src/zz_big.py's "
          "whole chunk, and the note, byte for byte",
          out == expected and part != "" and len(out) - len(fr._tail(out, "\n[diff truncated")) <= ER.MAX_DIFF,
          (len(out), len(expected)))
    check("[CP2-X05] src/zz_big.py reaches the reviewer in full: its 'diff --git' header and its MID-MOD-44e1 line",
          zz_c in out and "diff --git a/src/zz_big.py" in out and "MID-MOD-44e1" in out, "")
    tail = fr._tail(out, "left out or cut short")
    check("[CP2-X05] only the add is named in the closing note (src/zz_big.py, shown whole, is not)",
          "aaa-fixture.txt" in tail and "src/zz_big.py" not in tail, tail)
    check("[CP2-X05] the fixture's hidden tail never leaks", "ZQ-HIDDEN-9f2b" not in out, "")
    check("[CP2-X05] the add is never summarized: no '[file added' line", "[file added" not in out, "")

    # finding A1's shape: a new module (changed since round 1) bigger than the room left, plus small rest
    # modifications that all fit: the rest modifications are shown whole first, the module gets what is left
    d = fr._mk_repo("cp2x05b")
    (d / "src").mkdir()
    (d / "src/core.py").write_text("def core():\n    return 1\n")
    for k in range(5):
        (d / f"src/old{k:02d}.py").write_text(f"v = {k}\n")
    base = fr._snapshot(d)
    for k in range(5):
        (d / f"src/old{k:02d}.py").write_text(f"v = {k}  # OLD-REWRITE-{k:02d}\n")
    round1 = fr._snapshot(d)
    (d / "src/core.py").write_text("def core():\n    return 2  # CORE-CHANGE-5a1b\n")
    (d / "src/newmod.py").write_text("# INJECT-MARKER-TOP\n" + fr._rewrite_body(5000, "NEWMOD"))
    round2 = fr._snapshot(d)
    out2 = ER.collect_diff(d, base, round2, prev_round_tree=round1)
    cm2 = fr._chunkmap(fr._real(d, base, round2))
    header2, note2 = fr._header(base, round2), fr._priority_note(["src/core.py", "src/newmod.py"])
    olds = [f"src/old{k:02d}.py" for k in range(5)]
    room2 = ER.MAX_DIFF - len(header2) - len(note2) - len(cm2["src/core.py"])
    check("[CP2-X05] A1 shape (fixture sanity): src/newmod.py is a pure add bigger than the room left, and the five "
          "rest modifications together would fit in it",
          ER._pure_add_delete(cm2["src/newmod.py"])[0] == "added" and len(cm2["src/newmod.py"]) > room2
          and sum(len(cm2[f]) for f in olds) < room2, "")
    part2 = cm2["src/newmod.py"][:room2 - sum(len(cm2[f]) for f in olds)]
    part2 = part2[:part2.rfind("\n") + 1]
    expected2 = (header2 + note2 + cm2["src/core.py"] + part2 + "".join(cm2[f] for f in olds)
                 + fr._cut_note(["src/newmod.py"]))
    check("[CP2-X05] A1 shape: src/core.py, then src/newmod.py from its top (INJECT-MARKER-TOP shown) cut back to a "
          "whole line within the room the rest leaves, then every rest file's whole chunk, byte for byte; only "
          "src/newmod.py is named in the note",
          out2 == expected2 and "INJECT-MARKER-TOP" in out2 and out2.count("OLD-REWRITE-") == 5
          and all(f not in fr._tail(out2, "left out or cut short") for f in olds), (len(out2), len(expected2)))


CASES = [x01_t16_shape_checked_unconditionally_with_its_budget,
         x02_cut_add_followed_by_a_chunk_ends_on_a_whole_line,
         x03_delete_that_fits_is_shown_in_full_next_to_a_cut_add,
         x04_hand_built_disguises_checked_strictly,
         x05_priority_add_over_the_budget_is_cut_after_every_real_change]


def main():
    for fn in CASES:
        print(fn.__name__)
        try:
            fn()
        except Exception:
            check(f"[CP2-X] {fn.__name__} ran to the end without an unexpected error", False, traceback.format_exc())
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
