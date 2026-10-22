#!/usr/bin/env python3
"""
CP2 tests: full rounds summarize deletes, deprioritize huge adds.   python3 tests/test_review_diff_full_rounds.py

Extends CP1's rule (huge deleted files get an honest one-line summary; huge added files are NEVER summarized)
into collect_diff() in skills/ratchet-adversarial-loop/scripts/external_review.py -- the text built for a FULL
(unscoped) review round, in BOTH its branches (round 1, with no prev_round_tree; a later round, with the
"files changed since the previous round" priority/rest split). Rule under test:

  - A diff under MAX_DIFF (180,000 chars) is untouched: reviewers see exactly what they see today.
  - Over MAX_DIFF: a textbook single-hunk pure DELETE chunk may always be summarized (git's own header lines
    verbatim, plus one honest "[file deleted; N lines of former content omitted]" line), exactly as
    scoped_diff_parts already does for fix rounds -- only as many deletes as needed are summarized, biggest
    first. A pure ADD chunk is NEVER summarized, however big: real changed files (any modification, and a
    summarized delete) are packed into the budget first; a pure add is packed in only after them, and if more
    than one add needs cutting, the smallest fits first (the biggest is cut last). A cut or left-out file
    (an add that didn't fit, or an ordinary too-large modification) is named in the same "left out or cut
    short" note either way -- never claimed as shown, never given a special tag for being an add. In a later
    round the "files changed since the previous round come first" guarantee stays a GROUP boundary: within
    that group, adds still sort after non-adds (an add that changed since the last round can still land ahead
    of older, unrelated content, just behind that round's own real changes).

Two layers:
  (a) Library (most cases): skills/ratchet-adversarial-loop/scripts/external_review.py is loaded by path (via
      tests/test_review_diff_large_files.py's already-loaded module, reused here as the one `ER`) and
      collect_diff(root, base_tree, current_tree, prev_round_tree) is called directly, on throwaway git repos
      built with `git add -A && git write-tree` (no commits needed). For diff shapes real git never prints
      (CP2-T12, CP2-T14), ER.git is temporarily stubbed for the `diff-tree ... -p ...` call only (never the
      `--name-only` call a later round also makes, which still hits real git on a real small repo).
  (b) End to end (CP2-T18): tests/test_gates.py's project()/approve_plan() and tests/test_providers.py's
      fake_bin()/env_with()/open_round(), plus tests/test_flaky.py's _prompt_dry_run(), drive the real
      ratchet.py CLI and external_review.py --dry-run, so the prompt a reviewer actually receives is read.
  CP2-T19 imports tests/test_flaky.py as committed and reruns its CP6-T16/T17/T18/X10 checks unedited, routing
  their own `check()` calls into this file's counters for the duration, then diffs the AST of the functions and
  fixtures they use against `git merge-base HEAD main` to prove this lane did not need to touch them.

Writes a per-test JSON report to RATCHET_TEST_REPORT when set: {"cases": [{"id": "CP2-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                                 # noqa: E402
import test_review_diff_large_files as t1   # noqa: E402  (ER, repo/content builders)
import test_gates as g                      # noqa: E402  (project, approve_plan, write_tests, rx)
import test_providers as tp                 # noqa: E402  (fake_bin, env_with, open_round, ids)
import test_flaky as tfl                    # noqa: E402  (CP6-T16..T18/X10, _prompt_dry_run)

ER = t1.ER
_mk_repo = t1._mk_repo
_snapshot = t1._snapshot
_git = t1._git
_delete_lines = t1._delete_lines
_add_lines = t1._add_lines
_text = t1._text
_delete_chunk_exact_len = t1._delete_chunk_exact_len

PASS = FAIL = 0
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
ID_RE = re.compile(r"\[(CP2-T\d+)\]")
PLANNED = [f"CP2-T{i:02d}" for i in range(1, 21)]


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


BASE_ID, CUR_ID = "0000000000", "1111111111"
HEADER2 = f"# Changes in this checkpoint ({BASE_ID} → {CUR_ID})\n"


# ================================================================================================ content/diff helpers

def _padded(prefix, n, digits=0, width=40):
    out = []
    for i in range(n):
        tag = f"{i:0{digits}d}" if digits else str(i)
        s = f"{prefix}{tag}"
        if len(s) < width:
            s += " " * (width - len(s))
        out.append(s)
    return "\n".join(out) + "\n"


def _fixture_text(n=25000):
    """aaa-fixture.txt's content: a pure add fixture, 'FIXTURE-LINE-<5-digit n>' padded to 40 chars. The very
    last line carries ZQ-HIDDEN-9f2b -- a marker deep enough past the cut budget that it must NEVER reach the
    prompt once this file is cut (director decision #4: a cut add's hidden tail content never leaks, whatever
    the cut-note says)."""
    body = _padded("FIXTURE-LINE-", n, digits=5)
    lines = body.split("\n")
    lines[-2] = "ZQ-HIDDEN-9f2b (never shown once this file is cut)"
    return "\n".join(lines)


def _vendor_text(n=25000):
    """aaa-vendor.js's content: a pure delete fixture, 'VENDOR-LINE-<n>' padded to 40 chars."""
    return _padded("VENDOR-LINE-", n)


def _rewrite_body(n, tag, width=40):
    out = []
    for i in range(n):
        s = f"{tag}-{i:05d}"
        if len(s) < width:
            s += " " * (width - len(s))
        out.append(s)
    return "\n".join(out) + "\n"


def _rewrite_body_changed(n, tag, marker, width=40):
    out = []
    for i in range(n):
        s = f"{tag}-{i:05d} CHANGED"
        if i in (0, n - 1):
            s += f" {marker}"
        if len(s) < width:
            s += " " * (width - len(s))
        out.append(s)
    return "\n".join(out) + "\n"


def _real(d, a, b):
    """git's own `diff-tree -r -p --no-color` output for a full (unscoped) round -- the exact call
    collect_diff makes, so expected text is always built from git's own bytes."""
    return _git(d, "diff-tree", "-r", "-p", "--no-color", a, b)


def _chunkmap(real_diff_text):
    return dict(ER._diff_file_chunks(real_diff_text))


def _header(base, cur):
    return f"# Changes in this checkpoint ({base[:10]} → {cur[:10]})\n"


def _priority_note(files):
    names = ", ".join(files) if files else "(none)"
    return f"# Files changed since the previous review round ({names}): their diffs come first below.\n"


def _cut_note(paths):
    return (f"\n[diff truncated at {ER.MAX_DIFF} chars — left out or cut short (ask about these if they "
            f"matter): {', '.join(paths)}]")


def _flag_note():
    return f"\n\n[diff truncated at {ER.MAX_DIFF} chars — flag anything you could not see]"


def _tail(text, marker):
    """`text` from `marker` onward, or '' when `marker` is absent (never raises) -- so a case that fails
    today because the expected note/marker isn't produced yet still runs to completion instead of crashing."""
    i = text.find(marker)
    return text[i:] if i != -1 else ""


def _summary_for(chunk):
    """The oracle for 'a pure delete, summarized': CP1's own, already-tested _pure_add_delete/_summarize_pure,
    called on a real git chunk -- never a reimplementation of that formatting."""
    pure = ER._pure_add_delete(chunk)
    assert pure and pure[0] == "deleted", (pure, chunk[:200])
    return ER._summarize_pure(chunk, *pure)


def _mod_chunk(path, old_line, new_line, index_a="aaaaaaa", index_b="bbbbbbb", mode="100644"):
    """A hand-built, textbook single-hunk MODIFICATION chunk (never a pure add/delete) for `path`."""
    header = [f"diff --git a/{path} b/{path}", f"index {index_a}..{index_b} {mode}",
              f"--- a/{path}", f"+++ b/{path}", "@@ -1,1 +1,1 @@"]
    body = [f"-{old_line}", f"+{new_line}"]
    return "\n".join(header + body) + "\n"


def _add_chunk_exact_len(path, total_chars):
    """A textbook single-line pure-ADD chunk for `path`, padded so its own text is exactly `total_chars`
    characters long (mirrors t1._delete_chunk_exact_len for the add side)."""
    head = [f"diff --git a/{path} b/{path}", "new file mode 100644", "index 0000000..b0b0b0b",
            "--- /dev/null", f"+++ b/{path}", "@@ -0,0 +1,1 @@"]
    prefix = "\n".join(head) + "\n+"
    pad = total_chars - len(prefix) - 1
    assert pad >= 0, (total_chars, len(prefix))
    return prefix + ("a" * pad) + "\n"


import contextlib  # noqa: E402


@contextlib.contextmanager
def _stub_full_diff(diff_text):
    """Replaces ER.git with a stub that returns `diff_text` ONLY for collect_diff's `diff-tree ... -p ...`
    call, so a diff shape real git never prints can still go through collect_diff. Any other call (in
    particular the later round's `diff-tree --name-only` priority lookup) passes through to real git
    untouched, so it can still run against a real small repo."""
    orig = ER.git

    def fake(root, *args):
        if args and args[0] == "diff-tree" and "-p" in args:
            return diff_text
        return orig(root, *args)

    ER.git = fake
    try:
        yield
    finally:
        ER.git = orig


def _app_repo(prefix="app"):
    """A tiny real repo with only src/app.py, used for the later-round `--name-only` lookup in the
    hand-built-chunk cases (CP2-T12): its own two trees differ ONLY in src/app.py."""
    d = _mk_repo(f"cp2-{prefix}")
    (d / "src").mkdir()
    (d / "src/app.py").write_text("line1\n")
    prev = _snapshot(d)
    (d / "src/app.py").write_text("line2\n")
    cur = _snapshot(d)
    return d, prev, cur


# ================================================================================================ CP2-T01..T03 (adds)

def t01_first_round_real_change_shown_fixture_cut_last():
    d = _mk_repo("cp2t01")
    (d / "src").mkdir()
    (d / "src/app.py").write_text("def run():\n    return 1\n")
    base = _snapshot(d)
    (d / "src/app.py").write_text("def run():\n    return 2  # REAL-CHANGE-7c1e\n")
    (d / "aaa-fixture.txt").write_text(_fixture_text(25000))
    cur = _snapshot(d)
    real = _real(d, base, cur)
    cm = _chunkmap(real)
    header = _header(base, cur)
    app_c, fix_c = cm["src/app.py"], cm["aaa-fixture.txt"]
    remaining = ER.MAX_DIFF - len(header) - len(app_c)
    expected = header + app_c + fix_c[:remaining] + _cut_note(["aaa-fixture.txt"])
    out = ER.collect_diff(d, base, cur, None)
    check("[CP2-T01] output equals header + src/app.py's full chunk + the fixture cut at the remaining budget "
          "+ the note naming exactly aaa-fixture.txt",
          out == expected, (len(out), len(expected)))
    check("[CP2-T01] REAL-CHANGE-7c1e is present", "REAL-CHANGE-7c1e" in out, "")
    check("[CP2-T01] the fixture part starts with git's own add headers, shown as far as it fits",
          _tail(out, "diff --git a/aaa-fixture.txt").startswith(fix_c[:remaining]), "")
    check("[CP2-T01] no '[file added' or '[file deleted' line appears",
          "[file added" not in out and "[file deleted" not in out, "")
    note = _tail(out, "left out or cut short")
    check("[CP2-T01] the note names the fixture and never claims it was summarized or shown",
          "aaa-fixture.txt" in note and "summar" not in note.lower() and "shown" not in note.lower(), note)
    check("[CP2-T01] no 'previous review round' text (this is round 1)",
          "previous review round" not in out, "")
    check("[CP2-T01] director decision #4 (safety): ZQ-HIDDEN-9f2b, near the very end of the fixture's real "
          "content (well past the cut budget), never reaches the prompt text -- a cut file is cut, never "
          "mistaken for shown",
          "ZQ-HIDDEN-9f2b" in fix_c and "ZQ-HIDDEN-9f2b" not in out, "")


def t02_later_round_real_changes_ahead_of_earlier_add():
    d = _mk_repo("cp2t02")
    (d / "src").mkdir()
    (d / "src/app.py").write_text("def run():\n    return 1\n")
    (d / "src/other.py").write_text("x = 1\ny = 2\nz = 3\n")
    base = _snapshot(d)
    (d / "aaa-fixture.txt").write_text(_fixture_text(25000))
    (d / "src/other.py").write_text("x = 1\ny = 22  # OTHER-CHANGE-3b9d\nz = 3\n")
    round1 = _snapshot(d)
    (d / "src/app.py").write_text("def run():\n    return 2  # REAL-CHANGE-7c1e\n")
    round2 = _snapshot(d)
    real = _real(d, base, round2)
    cm = _chunkmap(real)
    header = _header(base, round2)
    note = _priority_note(["src/app.py"])
    app_c, other_c, fix_c = cm["src/app.py"], cm["src/other.py"], cm["aaa-fixture.txt"]
    remaining = ER.MAX_DIFF - len(header) - len(note) - len(app_c) - len(other_c)
    expected = header + note + app_c + other_c + fix_c[:remaining] + _cut_note(["aaa-fixture.txt"])
    out = ER.collect_diff(d, base, round2, prev_round_tree=round1)
    check("[CP2-T02] output equals header + the priority note + src/app.py + src/other.py + the fixture cut "
          "+ the note naming only aaa-fixture.txt",
          out == expected, (len(out), len(expected)))
    check("[CP2-T02] OTHER-CHANGE-3b9d is present", "OTHER-CHANGE-3b9d" in out, "")
    tail = _tail(out, "left out or cut short")
    check("[CP2-T02] src/other.py is not named in the note", "src/other.py" not in tail, tail)
    check("[CP2-T02] the fixture has no summary line and the note never says it was shown",
          "[file added" not in out and "shown" not in tail.lower(), tail)


def t03_real_modification_packed_ahead_of_earlier_add():
    d = _mk_repo("cp2t03")
    (d / "src").mkdir()
    (d / "src/app.py").write_text("def run():\n    return 1\n")
    (d / "src/zz_big.py").write_text(_rewrite_body(1200, "ZZBIG"))
    base = _snapshot(d)
    (d / "aaa-fixture.txt").write_text(_fixture_text(25000))
    (d / "src/zz_big.py").write_text(_rewrite_body_changed(1200, "ZZBIG", "MID-MOD-44e1"))
    round1 = _snapshot(d)
    (d / "src/app.py").write_text("def run():\n    return 2  # REAL-CHANGE-7c1e\n")
    round2 = _snapshot(d)

    real1 = _real(d, base, round1)
    cm1 = _chunkmap(real1)
    zz_c1, fix_c1 = cm1["src/zz_big.py"], cm1["aaa-fixture.txt"]
    out1 = ER.collect_diff(d, base, round1, None)
    check("[CP2-T03] round 1: src/zz_big.py's full chunk (both MID-MOD-44e1 markers) is present verbatim",
          zz_c1 in out1 and out1.count("MID-MOD-44e1") == 2, "")
    i_zz1, i_fix1 = out1.find("diff --git a/src/zz_big.py"), out1.find("diff --git a/aaa-fixture.txt")
    check("[CP2-T03] round 1: src/zz_big.py's chunk starts before aaa-fixture.txt's 'diff --git' line",
          i_zz1 != -1 and i_fix1 != -1 and i_zz1 < i_fix1, (i_zz1, i_fix1))
    tail1 = out1[out1.index("left out or cut short"):] if "left out or cut short" in out1 else ""
    check("[CP2-T03] round 1: the closing note names exactly aaa-fixture.txt",
          "aaa-fixture.txt" in tail1 and "src/zz_big.py" not in tail1, tail1)
    note_idx1 = out1.find("\n[diff truncated", i_fix1)
    shown_part1 = out1[i_fix1: note_idx1 if note_idx1 != -1 else len(out1)]
    check("[CP2-T03] round 1: no '[file added' line for the fixture; its shown part is a verbatim prefix",
          "[file added" not in shown_part1 and fix_c1.startswith(shown_part1), "")

    real2 = _real(d, base, round2)
    cm2 = _chunkmap(real2)
    zz_c2 = cm2["src/zz_big.py"]
    out2 = ER.collect_diff(d, base, round2, prev_round_tree=round1)
    i_note2 = out2.find("Files changed since the previous review round")
    i_app2 = out2.find("diff --git a/src/app.py")
    i_zz2 = out2.find("diff --git a/src/zz_big.py")
    i_fix2 = out2.find("diff --git a/aaa-fixture.txt")
    check("[CP2-T03] later round: src/app.py's chunk comes right after the note",
          i_app2 != -1 and i_note2 != -1 and i_app2 > i_note2, (i_note2, i_app2))
    check("[CP2-T03] later round: src/zz_big.py's full chunk (both markers) is present verbatim",
          zz_c2 in out2 and out2.count("MID-MOD-44e1") == 2, "")
    tail2 = out2[out2.index("left out or cut short"):] if "left out or cut short" in out2 else ""
    check("[CP2-T03] later round: the closing note names exactly aaa-fixture.txt",
          "aaa-fixture.txt" in tail2 and "src/zz_big.py" not in tail2 and "src/app.py" not in tail2, tail2)


# ================================================================================================ CP2-T04..T07 (deletes)

def t04_first_round_delete_summarized_and_edit_shown():
    d = _mk_repo("cp2t04")
    (d / "src").mkdir()
    (d / "src/app.py").write_text("def run():\n    return 1\n")
    (d / "aaa-vendor.js").write_text(_vendor_text(25000))
    base = _snapshot(d)
    (d / "src/app.py").write_text("def run():\n    return 2  # REAL-CHANGE-7c1e\n")
    (d / "aaa-vendor.js").unlink()
    cur = _snapshot(d)
    real = _real(d, base, cur)
    cm = _chunkmap(real)
    header = _header(base, cur)
    vendor_c, app_c = cm["aaa-vendor.js"], cm["src/app.py"]
    summary = _summary_for(vendor_c)
    expected = header + summary + app_c
    out = ER.collect_diff(d, base, cur, None)
    check("[CP2-T04] output equals header + aaa-vendor.js's summarized part + src/app.py's full chunk, no note",
          out == expected, (len(out), len(expected)))
    idx_sum = out.find(summary)
    check("[CP2-T04] REAL-CHANGE-7c1e is present and no 'VENDOR-LINE-' text or '@@' line for the vendor file",
          "REAL-CHANGE-7c1e" in out and "VENDOR-LINE-" not in out and idx_sum != -1
          and "@@" not in out[idx_sum:idx_sum + len(summary)], "")
    check("[CP2-T04] the whole output is at most MAX_DIFF chars and aaa-vendor.js is never named as cut",
          len(out) <= ER.MAX_DIFF and "aaa-vendor.js" not in out[len(header) + len(summary):]
          .replace(app_c, ""), len(out))
    text2, shown2, omitted2 = ER.scoped_diff_parts(d, base, cur)
    check("[CP2-T04] (fixture sanity) scoped_diff_parts also summarizes aaa-vendor.js",
          "aaa-vendor.js" in shown2, (shown2, omitted2))
    idx = text2.index("diff --git a/aaa-vendor.js")
    rest = text2[idx:]
    m = re.search(r"(?m)^diff --git ", rest[1:])
    scoped_part = rest[: (m.start() + 1) if m else len(rest)]
    check("[CP2-T04] the summarized part is byte-identical to scoped_diff_parts' part for the same file/trees",
          scoped_part == summary, (scoped_part[-200:], summary[-200:]))


def t05_later_round_summarizes_regardless_of_when_deleted():
    # Variant A: deleted in round 2, alongside another change
    dA = _mk_repo("cp2t05a")
    (dA / "src").mkdir()
    (dA / "src/app.py").write_text("a = 1\n")
    (dA / "src/zz_late.py").write_text("p = 1\nq = 2\n")
    (dA / "aaa-vendor.js").write_text(_vendor_text(25000))
    baseA = _snapshot(dA)
    (dA / "src/app.py").write_text("a = 11\n")
    round1A = _snapshot(dA)
    (dA / "aaa-vendor.js").unlink()
    (dA / "src/zz_late.py").write_text("p = 1\nq = 22  # LATE-CHANGE-5a0f\n")
    round2A = _snapshot(dA)
    realA = _real(dA, baseA, round2A)
    cmA = _chunkmap(realA)
    headerA = _header(baseA, round2A)
    noteA = _priority_note(["aaa-vendor.js", "src/zz_late.py"])
    summaryA = _summary_for(cmA["aaa-vendor.js"])
    expectedA = headerA + noteA + summaryA + cmA["src/zz_late.py"] + cmA["src/app.py"]
    outA = ER.collect_diff(dA, baseA, round2A, prev_round_tree=round1A)
    check("[CP2-T05] variant A equals header + note(aaa-vendor.js, src/zz_late.py) + vendor summary + "
          "zz_late's chunk + app's chunk, no truncated note",
          outA == expectedA, (len(outA), len(expectedA)))
    check("[CP2-T05] variant A: no VENDOR-LINE text and aaa-vendor.js is never named as cut",
          "VENDOR-LINE-" not in outA and "left out or cut short" not in outA, "")

    # Variant B: deleted in round 1, nothing new in round 2 except app.py
    dB = _mk_repo("cp2t05b")
    (dB / "src").mkdir()
    (dB / "src/app.py").write_text("a = 1\n")
    (dB / "aaa-vendor.js").write_text(_vendor_text(25000))
    baseB = _snapshot(dB)
    (dB / "aaa-vendor.js").unlink()
    round1B = _snapshot(dB)
    (dB / "src/app.py").write_text("a = 11\n")
    round2B = _snapshot(dB)
    realB = _real(dB, baseB, round2B)
    cmB = _chunkmap(realB)
    headerB = _header(baseB, round2B)
    noteB = _priority_note(["src/app.py"])
    summaryB = _summary_for(cmB["aaa-vendor.js"])
    expectedB = headerB + noteB + cmB["src/app.py"] + summaryB
    outB = ER.collect_diff(dB, baseB, round2B, prev_round_tree=round1B)
    check("[CP2-T05] variant B equals header + note(src/app.py) + app's chunk + vendor summary, no truncated "
          "note",
          outB == expectedB, (len(outB), len(expectedB)))
    check("[CP2-T05] variant B: no VENDOR-LINE text and aaa-vendor.js is never named as cut",
          "VENDOR-LINE-" not in outB and "left out or cut short" not in outB, "")


def t06_huge_delete_and_huge_add_together():
    d = _mk_repo("cp2t06")
    (d / "src").mkdir()
    (d / "src/app.py").write_text("def run():\n    return 1\n")
    (d / "aaa-vendor.js").write_text(_vendor_text(25000))
    base = _snapshot(d)
    (d / "aaa-vendor.js").unlink()
    (d / "aaa-fixture.txt").write_text(_fixture_text(25000))
    (d / "src/app.py").write_text("def run():\n    return 2  # REAL-CHANGE-7c1e\n")
    round1 = _snapshot(d)
    (d / "src/app.py").write_text("def run():\n    return 3  # REAL-CHANGE-7c1e-v2\n")
    round2 = _snapshot(d)

    for label, out, has_app_marker in (
        ("round 1", ER.collect_diff(d, base, round1, None), "REAL-CHANGE-7c1e"),
        ("later round", ER.collect_diff(d, base, round2, prev_round_tree=round1), "REAL-CHANGE-7c1e-v2"),
    ):
        real = _real(d, base, round1 if label == "round 1" else round2)
        cm = _chunkmap(real)
        summary = _summary_for(cm["aaa-vendor.js"])
        fix_c, app_c = cm["aaa-fixture.txt"], cm["src/app.py"]
        check(f"[CP2-T06] {label}: aaa-vendor.js appears only as its summary part",
              summary in out and "VENDOR-LINE-" not in out, "")
        i_app = out.find("diff --git a/src/app.py")
        i_fix = out.find("diff --git a/aaa-fixture.txt")
        check(f"[CP2-T06] {label}: src/app.py's full chunk appears and precedes aaa-fixture.txt's 'diff --git' "
              "line",
              app_c in out and i_app != -1 and i_fix != -1 and i_app < i_fix, (i_app, i_fix))
        check(f"[CP2-T06] {label}: {has_app_marker} is present", has_app_marker in out, "")
        tail = out[out.index("left out or cut short"):] if "left out or cut short" in out else ""
        check(f"[CP2-T06] {label}: the closing note names exactly aaa-fixture.txt, never aaa-vendor.js",
              "aaa-fixture.txt" in tail and "aaa-vendor.js" not in tail, tail)
        check(f"[CP2-T06] {label}: aaa-fixture.txt's shown part is a verbatim prefix of its own chunk, never "
              "a '[file added' line",
              "[file added" not in out and out[i_fix:i_fix + 200] == fix_c[:200], "")
        check(f"[CP2-T06] {label}: the output is at most MAX_DIFF chars plus the note",
              len(out) <= ER.MAX_DIFF + len(tail) + 50, len(out))


def t07_only_as_many_deletes_summarized_as_needed():
    d = _mk_repo("cp2t07")
    (d / "src").mkdir()
    (d / "src/app.py").write_text("a = 1\n")
    (d / "aaa-a.txt").write_text(_padded("AFILE-LINE-", 25000))
    (d / "bbb-b.txt").write_text(_padded("BFILE-LINE-", 2400))
    base = _snapshot(d)
    (d / "aaa-a.txt").unlink()
    (d / "bbb-b.txt").unlink()
    (d / "src/app.py").write_text("a = 11\n")
    round1 = _snapshot(d)
    (d / "src/app.py").write_text("a = 111\n")
    round2 = _snapshot(d)

    for label, out, trees in (("round 1", ER.collect_diff(d, base, round1, None), (base, round1)),
                               ("later round", ER.collect_diff(d, base, round2, prev_round_tree=round1),
                                (base, round2))):
        real = _real(d, *trees)
        cm = _chunkmap(real)
        a_summary = _summary_for(cm["aaa-a.txt"])
        check(f"[CP2-T07] {label}: aaa-a.txt (biggest) is summarized",
              a_summary in out and "AFILE-LINE-" not in out, "")
        check(f"[CP2-T07] {label}: bbb-b.txt's full chunk (every '-' line) appears verbatim",
              cm["bbb-b.txt"] in out, "")
        check(f"[CP2-T07] {label}: src/app.py's chunk is present and there is no truncated note",
              cm["src/app.py"] in out and "left out or cut short" not in out
              and "flag anything you could not see" not in out, "")


# ================================================================================================ CP2-T08..T10 (modifications)

def t08_first_round_huge_modification_cut_exactly_as_today():
    d = _mk_repo("cp2t08")
    (d / "src").mkdir()
    (d / "src/app.py").write_text("a = 1\n")
    (d / "big.txt").write_text(_rewrite_body(6000, "BIGTXT"))
    base = _snapshot(d)
    (d / "src/app.py").write_text("a = 11\n")
    (d / "big.txt").write_text(_rewrite_body_changed(6000, "BIGTXT", "HUGE-MOD-9c2b"))
    cur = _snapshot(d)
    real = _real(d, base, cur)
    header = _header(base, cur)
    expected = (header + real)[: ER.MAX_DIFF] + _flag_note()
    out = ER.collect_diff(d, base, cur, None)
    check("[CP2-T08] output equals (header + git's full diff-tree output)[:MAX_DIFF] + the generic flag note, "
          "byte for byte",
          out == expected, (len(out), len(expected)))
    check("[CP2-T08] no '[file deleted' or '[file added' line appears",
          "[file deleted" not in out and "[file added" not in out, "")


def t09_later_round_huge_modification_cut_and_named_exactly_as_today():
    d = _mk_repo("cp2t09")
    (d / "src").mkdir()
    (d / "src/app.py").write_text("a = 1\n")
    (d / "big.txt").write_text(_rewrite_body(6000, "BIGTXT"))
    base = _snapshot(d)
    (d / "big.txt").write_text(_rewrite_body_changed(6000, "BIGTXT", "HUGE-MOD-9c2b"))
    round1 = _snapshot(d)
    (d / "src/app.py").write_text("a = 11\n")
    round2 = _snapshot(d)
    real = _real(d, base, round2)
    cm = _chunkmap(real)
    header = _header(base, round2)
    note = _priority_note(["src/app.py"])
    app_c, big_c = cm["src/app.py"], cm["big.txt"]
    remaining = ER.MAX_DIFF - len(header) - len(note) - len(app_c)
    expected = header + note + app_c + big_c[:remaining] + _cut_note(["big.txt"])
    out = ER.collect_diff(d, base, round2, prev_round_tree=round1)
    check("[CP2-T09] output equals header + note(src/app.py) + app's chunk + big.txt cut at exactly the "
          "remaining budget + the note naming big.txt, byte for byte",
          out == expected, (len(out), len(expected)))
    check("[CP2-T09] big.txt has no summary line", "[file deleted" not in out and "omitted]" not in out, "")


def t10_huge_modification_and_huge_add_together():
    d = _mk_repo("cp2t10")
    (d / "src").mkdir()
    (d / "src/app.py").write_text("a = 1\n")
    (d / "big.txt").write_text(_rewrite_body(6000, "BIGTXT"))
    base = _snapshot(d)
    (d / "big.txt").write_text(_rewrite_body_changed(6000, "BIGTXT", "HUGE-MOD-9c2b"))
    (d / "aaa-fixture.txt").write_text(_fixture_text(25000))
    round1 = _snapshot(d)
    (d / "src/app.py").write_text("a = 11\n")
    round2 = _snapshot(d)

    real1 = _real(d, base, round1)
    cm1 = _chunkmap(real1)
    header1 = _header(base, round1)
    remaining1 = ER.MAX_DIFF - len(header1)
    expected1 = header1 + cm1["big.txt"][:remaining1] + _cut_note(["big.txt", "aaa-fixture.txt"])
    out1 = ER.collect_diff(d, base, round1, None)
    check("[CP2-T10] round 1 equals header + big.txt cut at the remaining budget + the note naming "
          "'big.txt, aaa-fixture.txt' in that order, byte for byte",
          out1 == expected1, (len(out1), len(expected1)))
    check("[CP2-T10] round 1: neither file gets a summary line",
          "[file deleted" not in out1 and "[file added" not in out1, "")

    real2 = _real(d, base, round2)
    cm2 = _chunkmap(real2)
    header2 = _header(base, round2)
    note2 = _priority_note(["src/app.py"])
    app_c2 = cm2["src/app.py"]
    remaining2 = ER.MAX_DIFF - len(header2) - len(note2) - len(app_c2)
    expected2 = header2 + note2 + app_c2 + cm2["big.txt"][:remaining2] + _cut_note(["big.txt", "aaa-fixture.txt"])
    out2 = ER.collect_diff(d, base, round2, prev_round_tree=round1)
    check("[CP2-T10] later round equals header + note(src/app.py) + app's chunk in full + big.txt cut + the "
          "note naming 'big.txt, aaa-fixture.txt', byte for byte",
          out2 == expected2, (len(out2), len(expected2)))


# ================================================================================================ CP2-T11, T12 (disguises)

def t11_disguised_modifications_never_summarized():
    variants = [
        ("trunc", "zzz-trunc.txt", _padded("TRUNC-LINE-", 25000), ""),
        ("grown", "zzz-grown.txt", "", _padded("GROWN-LINE-", 25000)),
        ("onel", "zzz-onel.txt", _padded("ONEL-LINE-", 25000), "ONE-DIFFERENT-LINE\n"),
    ]
    for tag, path, before, after in variants:
        d = _mk_repo(f"cp2t11{tag}")
        (d / "src").mkdir()
        (d / "src/app.py").write_text("def run():\n    return 1\n")
        (d / "aaa-vendor.js").write_text(_vendor_text(25000))
        (d / path).write_text(before)
        base = _snapshot(d)
        (d / "aaa-vendor.js").unlink()
        (d / "src/app.py").write_text("def run():\n    return 2  # REAL-CHANGE-7c1e\n")
        (d / path).write_text(after)
        round1 = _snapshot(d)
        (d / "src/app.py").write_text("def run():\n    return 3  # REAL-CHANGE-7c1e-v2\n")
        round2 = _snapshot(d)

        for label, out, trees in (("round 1", ER.collect_diff(d, base, round1, None), (base, round1)),
                                   ("later round", ER.collect_diff(d, base, round2, prev_round_tree=round1),
                                    (base, round2))):
            real = _real(d, *trees)
            cm = _chunkmap(real)
            summary = _summary_for(cm["aaa-vendor.js"])
            disguised_c = cm[path]
            check(f"[CP2-T11] {tag}/{label}: aaa-vendor.js is its summary part",
                  summary in out and "VENDOR-LINE-" not in out, "")
            idx_d = out.find(f"diff --git a/{path}")
            shown_part = ""
            if idx_d != -1:
                end_d = out.find("\n[diff truncated", idx_d)
                shown_part = out[idx_d: end_d if end_d != -1 else len(out)]
            check(f"[CP2-T11] {tag}/{label}: {path}'s shown part (if any) is a verbatim prefix of git's own "
                  "chunk for it, with no invented '[file deleted'/'[file added' line",
                  "[file deleted" not in shown_part and "[file added" not in shown_part
                  and (shown_part == "" or disguised_c.startswith(shown_part)),
                  (len(shown_part), len(disguised_c)))
            tail = _tail(out, "left out or cut short")
            check(f"[CP2-T11] {tag}/{label}: {path} is named in the closing 'left out or cut short' note",
                  path in tail, tail)
            check(f"[CP2-T11] {tag}/{label}: src/app.py's full chunk is present",
                  cm["src/app.py"] in out, "")


def t12_hand_built_disguises_never_summarized():
    def disguised(name, path, n=25000):
        if name == "x":
            lines = _delete_lines(path, n)
            lines[6 + 1] = "+SNEAKY-ADD-1d4e"
        elif name == "y":
            lines = _delete_lines(path, n) + ["@@ -25001,1 +0,0 @@", "-SNEAKY-HUNK-8f2a"]
        elif name == "z":
            lines = _add_lines(path, n)
            lines[6 + 1] = "-SNEAKY-MINUS-2e7a"
        elif name == "w":
            base = _delete_lines(path, n)
            base[6 + 1] = "-SNEAKY-COUNT-6b3d"
            lines = base + ["-extra-row-making-25001"]
        else:  # "v"
            base = _delete_lines(path, n)
            base[6 + 1] = "-SNEAKY-SIM-0a9f"
            lines = base[:1] + ["similarity index 100%"] + base[1:]
        return _text(lines)

    markers = {"x": "SNEAKY-ADD-1d4e", "y": "SNEAKY-HUNK-8f2a", "z": "SNEAKY-MINUS-2e7a",
               "w": "SNEAKY-COUNT-6b3d", "v": "SNEAKY-SIM-0a9f"}
    paths = {"x": "data/x.txt", "y": "data/y.txt", "z": "vendor/z.js", "w": "data/w.txt", "v": "data/v.txt"}
    vendor_chunk = _text(_delete_lines("aaa-vendor.js", 25000))
    app_chunk = _mod_chunk("src/app.py", "    return 1", "    return 2  # REAL-CHANGE-7c1e")

    for name in "xyzwv":
        path = paths[name]
        full_text = vendor_chunk + app_chunk + disguised(name, path)
        marker = markers[name]

        with _stub_full_diff(full_text):
            out1 = ER.collect_diff(Path("/nonexistent-cp2-stub-root"), BASE_ID, CUR_ID, None)
        d, prev, cur = _app_repo(name)
        with _stub_full_diff(full_text):
            out2 = ER.collect_diff(d, "aaaaaaaaaa", cur, prev_round_tree=prev)

        for label, out in (("round 1", out1), ("later round", out2)):
            idx_d = out.find(f"diff --git a/{path}")
            shown_part = ""
            if idx_d != -1:
                end_d = out.find("\n[diff truncated", idx_d)
                shown_part = out[idx_d: end_d if end_d != -1 else len(out)]
            check(f"[CP2-T12] {name}/{label}: {path} gets no '[file deleted' or '[file added' line",
                  "[file deleted" not in shown_part and "[file added" not in shown_part
                  or (shown_part.count("[file deleted") == 0 and shown_part.count("[file added") == 0), "")
            tail = out[out.index("left out or cut short"):] if "left out or cut short" in out else ""
            check(f"[CP2-T12] {name}/{label}: {marker} (the disguised chunk's own marker) is present",
                  marker in out or path in tail, out[-300:])
            check(f"[CP2-T12] {name}/{label}: {path} is named in the closing note",
                  path in tail, tail)
            check(f"[CP2-T12] {name}/{label}: the textbook delete next to it IS summarized (the summarizer "
                  "is active in this call)",
                  "VENDOR-LINE-" not in out and "omitted]" in out, out[:400])
            check(f"[CP2-T12] {name}/{label}: src/app.py's chunk is present in full",
                  "REAL-CHANGE-7c1e" in out and "@@ -1,1 +1,1 @@" in out, "")


# ================================================================================================ CP2-T13, T14 (unchanged / boundary)

def t13_normal_size_step_unchanged():
    d = _mk_repo("cp2t13")
    (d / "src").mkdir()
    (d / "src/app.py").write_text("a = 1\n")
    (d / "src/other.py").write_text("b = 1\n")
    (d / "src/gone.js").write_text("\n".join(f"g{i}" for i in range(30)) + "\n")
    base = _snapshot(d)
    (d / "aaa-new.js").write_text("\n".join(f"n{i}" for i in range(20)) + "\n")
    (d / "src/gone.js").unlink()
    (d / "src/app.py").write_text("a = 11\n")
    round1 = _snapshot(d)
    (d / "src/app.py").write_text("a = 111\n")
    round2 = _snapshot(d)

    real1 = _real(d, base, round1)
    header1 = _header(base, round1)
    out1 = ER.collect_diff(d, base, round1, None)
    check("[CP2-T13] round 1 equals header + git's full diff-tree output byte for byte (aaa-new.js still "
          "first, not moved)",
          out1 == header1 + real1, (len(out1), len(header1 + real1)))

    real2 = _real(d, base, round2)
    cm2 = _chunkmap(real2)
    header2 = _header(base, round2)
    note2 = _priority_note(["src/app.py"])
    order2 = [f for f, _ in ER._diff_file_chunks(real2) if f != "src/app.py"]
    expected2 = header2 + note2 + cm2["src/app.py"] + "".join(cm2[f] for f in order2)
    out2 = ER.collect_diff(d, base, round2, prev_round_tree=round1)
    check("[CP2-T13] later round equals header + the priority note + src/app.py's chunk + the rest in git's "
          "order, byte for byte",
          out2 == expected2, (len(out2), len(expected2)))
    check("[CP2-T13] neither output has a summary line or a truncated note",
          "omitted]" not in out1 and "left out or cut short" not in out1 and "flag anything" not in out1
          and "omitted]" not in out2 and "left out or cut short" not in out2, "")


def t14_boundary_is_exact():
    chunk_a = _add_chunk_exact_len("aaa-edge.txt", ER.MAX_DIFF - len(HEADER2))
    with _stub_full_diff(chunk_a):
        out_a = ER.collect_diff(Path("/nonexistent-cp2-t14a"), BASE_ID, CUR_ID, None)
    check("[CP2-T14] (a) at exactly MAX_DIFF: output equals header + the whole add, byte for byte, no note",
          out_a == HEADER2 + chunk_a, (len(out_a), ER.MAX_DIFF))

    edge_b = _add_chunk_exact_len("aaa-edge.txt", ER.MAX_DIFF - len(HEADER2) + 1)
    app_b = _mod_chunk("src/app.py", "x = 1", "x = 2")
    with _stub_full_diff(edge_b + app_b):
        out_b = ER.collect_diff(Path("/nonexistent-cp2-t14b"), BASE_ID, CUR_ID, None)
    remaining_b = ER.MAX_DIFF - len(HEADER2) - len(app_b)
    expected_b = HEADER2 + app_b + edge_b[:remaining_b] + _cut_note(["aaa-edge.txt"])
    check("[CP2-T14] (b) one char over: output equals header + src/app.py's chunk + the add cut at the "
          "remaining budget + the note naming exactly aaa-edge.txt",
          out_b == expected_b, (len(out_b), len(expected_b)))
    check("[CP2-T14] (b) no summary line for the add", "[file added" not in out_b, "")

    chunk_c = _delete_chunk_exact_len("aaa-edge-del.txt", ER.MAX_DIFF - len(HEADER2))
    with _stub_full_diff(chunk_c):
        out_c = ER.collect_diff(Path("/nonexistent-cp2-t14c"), BASE_ID, CUR_ID, None)
    check("[CP2-T14] (c) at exactly MAX_DIFF: a pure delete equals header + the whole diff, byte for byte, no "
          "summary",
          out_c == HEADER2 + chunk_c, (len(out_c), ER.MAX_DIFF))

    chunk_d = _delete_chunk_exact_len("aaa-edge-del.txt", ER.MAX_DIFF - len(HEADER2) + 1)
    with _stub_full_diff(chunk_d):
        out_d = ER.collect_diff(Path("/nonexistent-cp2-t14d"), BASE_ID, CUR_ID, None)
    summary_d = _summary_for(chunk_d)
    check("[CP2-T14] (d) one char over: a pure delete equals header + its summary part, with no truncated "
          "note",
          out_d == HEADER2 + summary_d and "left out or cut short" not in out_d, (len(out_d), HEADER2 + summary_d))


# ================================================================================================ CP2-T15, T16 (add fit/order)

def t15_small_add_fits_whole_only_the_oversize_one_is_cut():
    d = _mk_repo("cp2t15")
    (d / "src").mkdir()
    (d / "src/gen_big.py").write_text(_rewrite_body(1800, "GENBIG"))
    base = _snapshot(d)
    (d / "src/gen_big.py").write_text(_rewrite_body_changed(1800, "GENBIG", "GEN-MOD-11aa"))
    (d / "aaa-new-small.js").write_text("\n".join(f"n{i}" for i in range(120)) + "\n")
    (d / "aab-fixture.txt").write_text(_fixture_text(25000))
    cur = _snapshot(d)
    real = _real(d, base, cur)
    cm = _chunkmap(real)
    header = _header(base, cur)
    gen_c, small_c, fix_c = cm["src/gen_big.py"], cm["aaa-new-small.js"], cm["aab-fixture.txt"]
    remaining = ER.MAX_DIFF - len(header) - len(gen_c) - len(small_c)
    expected = header + gen_c + small_c + fix_c[:remaining] + _cut_note(["aab-fixture.txt"])
    out = ER.collect_diff(d, base, cur, None)
    check("[CP2-T15] output equals header + src/gen_big.py's full chunk + aaa-new-small.js's full chunk + "
          "aab-fixture.txt cut at the remaining budget + the note naming exactly aab-fixture.txt",
          out == expected, (len(out), len(expected)))
    check("[CP2-T15] aaa-new-small.js is not named in the note and has no summary line",
          "aaa-new-small.js" not in _tail(out, "left out or cut short") and "[file added" not in out, "")

    # director decision #2: with a tiny add and a huge add both over budget, the tiny one is shown and the
    # huge one is cut and named -- never the reverse -- even when git's/the original order would put the
    # huge add FIRST (name it to sort before the tiny one alphabetically, so only size-based fitting saves it).
    d2 = _mk_repo("cp2t15b")
    (d2 / "keep.txt").write_text("keep\n")
    base2 = _snapshot(d2)
    (d2 / "aaa-huge.txt").write_text(_fixture_text(25000))
    (d2 / "zzz-tiny.js").write_text("\n".join(f"t{i}" for i in range(80)) + "\n")
    cur2 = _snapshot(d2)
    real2 = _real(d2, base2, cur2)
    cm2 = _chunkmap(real2)
    out2 = ER.collect_diff(d2, base2, cur2, None)
    tiny_c2 = cm2["zzz-tiny.js"]
    check("[CP2-T15] director decision #2: the tiny add (zzz-tiny.js) is shown in full even though the huge "
          "add (aaa-huge.txt) sorts first alphabetically",
          tiny_c2 in out2, "")
    tail2 = out2[out2.index("left out or cut short"):] if "left out or cut short" in out2 else ""
    check("[CP2-T15] director decision #2: the huge add is the one cut and named -- never the tiny one",
          "aaa-huge.txt" in tail2 and "zzz-tiny.js" not in tail2, tail2)


def t16_later_round_priority_group_boundary_with_an_add():
    d = _mk_repo("cp2t16")
    (d / "src").mkdir()
    (d / "src/app.py").write_text("def run():\n    return 1\n")
    (d / "src/zz_big.py").write_text(_rewrite_body(1200, "ZZBIG"))
    base = _snapshot(d)
    (d / "src/zz_big.py").write_text(_rewrite_body_changed(1200, "ZZBIG", "MID-MOD-44e1"))
    round1 = _snapshot(d)
    (d / "aaa-fixture.txt").write_text(_fixture_text(2000))
    (d / "src/app.py").write_text("def run():\n    return 2  # REAL-CHANGE-7c1e\n")
    round2 = _snapshot(d)
    out = ER.collect_diff(d, base, round2, prev_round_tree=round1)

    i_note = out.find("Files changed since the previous review round")
    i_app = out.find("diff --git a/src/app.py")
    i_zz = out.find("diff --git a/src/zz_big.py")
    i_fix = out.find("diff --git a/aaa-fixture.txt")
    check("[CP2-T16] src/app.py's chunk comes first after the header/note and before src/zz_big.py",
          i_app != -1 and i_zz != -1 and (i_note == -1 or i_app > i_note) and i_app < i_zz,
          (i_note, i_app, i_zz))
    check("[CP2-T16] aaa-fixture.txt is never summarized: no '[file added' line",
          "[file added" not in out, "")
    tail = out[out.index("left out or cut short"):] if "left out or cut short" in out else ""
    if tail:
        check("[CP2-T16] every cut/left-out file named in the closing note is never also claimed as shown",
              "shown" not in tail.lower(), tail)
    if "their diffs come first below" in out:
        check("[CP2-T16] the 'changed since' guarantee holds as a GROUP boundary: aaa-fixture.txt (changed "
              "since round 1) still starts before src/zz_big.py (unchanged since round 1), even though it "
              "sorts after src/app.py within that group",
              i_fix != -1 and i_fix < i_zz, (i_fix, i_zz))
    else:
        check("[CP2-T16] otherwise the note says plainly a newly added file was put after the other changes",
              re.search(r"add(ed)?.{0,80}(after|behind|later)", out, re.I) is not None, out[:300])


# ================================================================================================ CP2-T17 (fallback)

def t17_fallback_unchanged():
    d = _mk_repo("cp2t17")
    (d / "big.txt").write_text(_rewrite_body(5000, "T17"))
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "init")
    (d / "big.txt").write_text(_rewrite_body_changed(5000, "T17", "FALLBACK-MOD"))
    (d / "new_untracked.bin").write_text(_fixture_text(25000))
    out = ER.collect_diff(d, None, None)
    real = _git(d, "diff", "HEAD", "--", ".", ":(exclude).ratchet")
    expected = "# Diff against HEAD\n" + real
    if len(expected) > ER.MAX_DIFF:
        expected = expected[: ER.MAX_DIFF] + _flag_note()
    check("[CP2-T17] output equals today's fallback byte for byte",
          out == expected, (len(out), len(expected)))
    check("[CP2-T17] the untracked 1MB file is not inlined", "FIXTURE-LINE-" not in out, "")
    check("[CP2-T17] no summary line appears", "omitted]" not in out, "")


# ================================================================================================ CP2-T18 (end to end)

def t18_prompt_round1_shows_real_change_delete_summary_and_names_cut_add():
    b = tp.fake_bin("codex")
    env = tp.env_with(b)
    d = g.project("cp2t18", extra_cp={"risk": "low"})
    g.rx(d, "config", "--set", 'providers=[{"id":"codex","preset":"codex"}]', env=env)
    (Path(d) / "vendor").mkdir()
    (Path(d) / "vendor/old.js").write_text(_padded("OLD-VENDOR-", 25000))
    g.approve_plan(d)
    g.write_tests(d)
    assert g.rx(d, "tests-written", "CP1").returncode == 0
    assert g.rx(d, "red", "CP1").returncode == 0
    (Path(d) / "vendor/old.js").unlink()
    (Path(d) / "aaa-fixture.txt").write_text(_fixture_text(25000))
    (Path(d) / "src/a.js").write_text("export const a = 2; // REAL-CHANGE-7c1e\n")
    (Path(d) / "src/f.js").write_text("export const f = () => 1;\n")
    assert g.rx(d, "implemented", "CP1").returncode == 0
    assert g.rx(d, "behavior", "CP1").returncode == 0
    rnd1 = tp.open_round(d, env)
    prompt = tfl._prompt_dry_run(d, env, tp.ids(rnd1)[0])

    check("[CP2-T18] the prompt contains REAL-CHANGE-7c1e", "REAL-CHANGE-7c1e" in prompt, "")
    m = re.findall(r"\[file deleted; 25000 lines of former content omitted\]", prompt)
    check("[CP2-T18] exactly one delete-summary line for vendor/old.js",
          len(m) == 1, prompt.count("file deleted"))
    idx_null = prompt.find("vendor/old.js")
    idx_summary = prompt.find("[file deleted; 25000 lines of former content omitted]")
    check("[CP2-T18] it comes right after vendor/old.js's '+++ /dev/null' line",
          idx_null != -1 and idx_summary != -1
          and "+++ /dev/null" in prompt[idx_null:idx_summary] and idx_summary - idx_null < 400,
          (idx_null, idx_summary))
    check("[CP2-T18] no 'OLD-VENDOR-' text and no '[file added' line",
          "OLD-VENDOR-" not in prompt and "[file added" not in prompt, "")
    tail = _tail(prompt, "left out or cut short")
    check("[CP2-T18] the note names aaa-fixture.txt, not vendor/old.js or src/a.js",
          "aaa-fixture.txt" in tail and "vendor/old.js" not in tail and "src/a.js" not in tail, tail)
    check("[CP2-T18] director decision #4 (safety): ZQ-HIDDEN-9f2b, deep in aaa-fixture.txt's real content, "
          "never reaches another company's reviewer even though the file is named as cut, not shown",
          "ZQ-HIDDEN-9f2b" not in prompt, "")


# ================================================================================================ CP2-T19 (test_flaky regression)

def t19_test_flaky_cp6_checks_unchanged():
    orig_check = tfl.check
    local = {}

    def routed(name, cond, detail=""):
        m = re.search(r"\[(CP6-[TX]\d+)\]", name)
        if m:
            cid = m.group(1)
            local[cid] = local.get(cid, True) and bool(cond)
        print(f"  (CP2-T19 via test_flaky) {'OK ' if cond else 'FAIL'} {name}"
              + ("" if cond else f"   {str(detail)[:300]}"))

    tfl.check = routed
    crashed = None
    try:
        tfl.t16_prioritizes_files_changed_since_previous_round()
        tfl.t17_names_which_files_were_cut()
        tfl.t18_round1_keeps_plain_order()
        tfl.t19_scoped_small_fix_excludes_the_filler_entirely()
    except Exception:
        crashed = traceback.format_exc()
    finally:
        tfl.check = orig_check

    expected_ids = {"CP6-T16", "CP6-T17", "CP6-T18", "CP6-X10"}
    check("[CP2-T19] CP6-T16/T17/T18/X10 ran without crashing", crashed is None, crashed or "")
    check("[CP2-T19] every one of CP6-T16/T17/T18/X10 passed with the pure-add filler, unchanged",
          crashed is None and expected_ids <= set(local) and all(local.get(c) for c in expected_ids), local)

    mb = subprocess.run(["git", "merge-base", "HEAD", "main"], cwd=str(ROOT), capture_output=True, text=True)
    names = {"t16_prioritizes_files_changed_since_previous_round", "t17_names_which_files_were_cut",
             "t18_round1_keeps_plain_order", "t19_scoped_small_fix_excludes_the_filler_entirely",
             "truncation_project", "_truncation_round1_only"}

    def dumps(src):
        out = {}
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return out
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
                out[node.name] = ast.dump(node)
        return out

    if mb.returncode == 0 and mb.stdout.strip():
        show = subprocess.run(["git", "show", f"{mb.stdout.strip()}:tests/test_flaky.py"],
                              cwd=str(ROOT), capture_output=True, text=True)
        base_d = dumps(show.stdout) if show.returncode == 0 else {}
    else:
        base_d = {}
    cur_d = dumps((ROOT / "tests/test_flaky.py").read_text())
    check("[CP2-T19] the AST of those four checks plus truncation_project/_truncation_round1_only equals the "
          "AST at git merge-base HEAD main",
          bool(base_d) and base_d == cur_d and set(base_d) == names, (sorted(base_d), sorted(cur_d)))


# ================================================================================================ CP2-T20 (docs)

def _blocks(text):
    return [b for b in re.split(r"\n\s*\n", text) if b.strip()]


def _full_round_rule_present(text):
    for para in _blocks(text):
        if not re.search(r"full\b.{0,20}round|unscoped round", para, re.I):
            continue
        if not re.search(r"delet", para, re.I):
            continue
        if not re.search(r"one line|single line", para, re.I):
            continue
        if not re.search(r"shown", para, re.I):
            continue
        if not re.search(r"\badd", para, re.I):
            continue
        if not re.search(r"never\b.{0,25}summar|not\b.{0,25}summar", para, re.I):
            continue
        if not re.search(r"ahead|before|first|priorit", para, re.I):
            continue
        if not re.search(r"\bcut\b", para, re.I):
            continue
        if not re.search(r"named", para, re.I):
            continue
        return True
    return False


def _bad_add_claim(text):
    for para in _blocks(text):
        for line in para.split("\n"):
            if not (re.search(r"\badd", line, re.I) and re.search(r"one line|summar", line, re.I)):
                continue
            if not re.search(r"delet|\bdiff\b|\bshown\b", line, re.I):
                continue  # not about the diff-summary topic at all (e.g. an unrelated use of 'add'/'one line')
            if not re.search(r"never|not\b", line, re.I):
                return True
    return False


def t20_docs_describe_the_full_round_rule():
    readme = (ROOT / "README.md").read_text()
    skill = (ROOT / "skills/ratchet-adversarial-loop/SKILL.md").read_text()
    docstring = ER.collect_diff.__doc__ or ""
    for name, text in (("README.md", readme),
                        ("skills/ratchet-adversarial-loop/SKILL.md", skill),
                        ("collect_diff.__doc__", docstring)):
        check(f"[CP2-T20] {name} has one paragraph describing the full-round rule (delete summarized/counts "
              "as shown; add never summarized; real changes ahead of adds; a cut add named like any other "
              "cut file)",
              _full_round_rule_present(text), "no matching paragraph")
        check(f"[CP2-T20] {name} never claims an added file is summarized or shown in one line in a full "
              "round without a 'never'/'not' negation",
              not _bad_add_claim(text), "a bad claim was found")


# ================================================================================================ main

CASES = [
    ("CP2-T01", t01_first_round_real_change_shown_fixture_cut_last),
    ("CP2-T02", t02_later_round_real_changes_ahead_of_earlier_add),
    ("CP2-T03", t03_real_modification_packed_ahead_of_earlier_add),
    ("CP2-T04", t04_first_round_delete_summarized_and_edit_shown),
    ("CP2-T05", t05_later_round_summarizes_regardless_of_when_deleted),
    ("CP2-T06", t06_huge_delete_and_huge_add_together),
    ("CP2-T07", t07_only_as_many_deletes_summarized_as_needed),
    ("CP2-T08", t08_first_round_huge_modification_cut_exactly_as_today),
    ("CP2-T09", t09_later_round_huge_modification_cut_and_named_exactly_as_today),
    ("CP2-T10", t10_huge_modification_and_huge_add_together),
    ("CP2-T11", t11_disguised_modifications_never_summarized),
    ("CP2-T12", t12_hand_built_disguises_never_summarized),
    ("CP2-T13", t13_normal_size_step_unchanged),
    ("CP2-T14", t14_boundary_is_exact),
    ("CP2-T15", t15_small_add_fits_whole_only_the_oversize_one_is_cut),
    ("CP2-T16", t16_later_round_priority_group_boundary_with_an_add),
    ("CP2-T17", t17_fallback_unchanged),
    ("CP2-T18", t18_prompt_round1_shows_real_change_delete_summary_and_names_cut_add),
    ("CP2-T19", t19_test_flaky_cp6_checks_unchanged),
    ("CP2-T20", t20_docs_describe_the_full_round_rule),
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
