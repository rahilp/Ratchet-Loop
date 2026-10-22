#!/usr/bin/env python3
"""
CP1 tests: fix rounds summarize huge deleted files.   python3 tests/test_review_diff_large_files.py

A fix round (a scoped round, built by scoped_diff_parts in skills/ratchet-adversarial-loop/scripts/external_review.py)
that deletes ONE huge file must still give the reviewer a real, honest look at the step's actual code
changes: the huge file is shown as git's own delete headers plus one honest line saying how many lines were
left out, counted as SHOWN (not omitted), so the round can still be recorded. A huge ADDED file is never
summarized: it is shown in full when it fits, or named as not shown and blocks the round, exactly as before this
lane. A huge REAL modification (not a pure, single-hunk delete) is still named as not shown and still blocks the
round, exactly as today. Any diff shape that is not a textbook pure delete (a modification disguised as one, a
second hunk, a wrong line count, an odd header, extra trailing lines) is never summarized.

Two layers, both black-box:
  (a) Library: skills/ratchet-adversarial-loop/scripts/external_review.py is loaded by path (as
      tests/test_providers.py's EXT does) and scoped_diff_parts(root, since_tree, current_tree) is called
      directly, mostly on throwaway git repos built with plain `git add -A && git write-tree` (no commits
      needed -- diff-tree works on tree objects alone). For diff shapes real git never prints (the disguised
      cases), the module's own git() is temporarily stubbed to return hand-built diff text for the `diff-tree`
      call only, so those cases still go through scoped_diff_parts and never name the private detector by name.
  (b) End to end (CP1-T02, T03, T06, T07): tests/test_fix_round.py's project/round/fake-CLI helpers (`tf`) and
      tests/test_fix_round_diff.py's extra helpers built on top of them (`tfd`: _ext_start-style project setup,
      _prompt via --dry-run, _rewrite_cli, markers) drive the real ratchet.py CLI and a fake other-company CLI
      through a blocked round 1 and a fix round, so "the round is recorded" / "recording is refused" are read
      from adv-record's exit code and the signed review's files_reviewed / omitted_files.

Every library case checks one invariant: each changed file is in exactly one of shown and omitted; a file in
shown has either its full git chunk verbatim, or (only for a textbook pure delete) git's header lines
plus one honest summary line; a file in omitted is named in the "NOT shown" note and none of its body lines
appear.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP1-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import contextlib
import importlib.util
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
import _tmp                     # noqa: E402
import test_gates as g          # noqa: E402  (write_review, rx)
import test_fix_round as tf     # noqa: E402  (project / round / fake-CLI helpers)
import test_fix_round_diff as tfd  # noqa: E402  (extra end-to-end helpers built on tf)

g.ENV["RATCHET_FAKE_LOADAVG"] = "0"
tfd.check = lambda *a, **k: check(*a, **k)  # route tfd's borrowed, tag-parameterized assertions into this file

EXT_PATH = ROOT / "skills" / "ratchet-adversarial-loop" / "scripts" / "external_review.py"

PLANNED = [f"CP1-T{i:02d}" for i in range(1, 20)]
PASS = FAIL = 0
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
ID_RE = re.compile(r"\[(CP1-T\d+)\]")


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


def _load_external_review():
    spec = importlib.util.spec_from_file_location("ratchet_external_review_cp1_test", EXT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ER = _load_external_review()
HEADER = f"# Changes since the last round actually reviewed (0000000000 → 1111111111)\n"


# ================================================================================================ helpers

def _git(d, *args):
    r = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=str(d), capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout


def _mk_repo(name):
    d = Path(_tmp.mkdtemp(prefix=f"ratchet-cp1-repo-{name}-"))
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    return d


def _snapshot(d):
    _git(d, "add", "-A")
    return _git(d, "write-tree").strip()


def _real_diff(d, since, cur):
    return _git(d, "diff-tree", "-r", "-p", "--no-color", since, cur)


def _scoped_header(since, cur):
    return f"# Changes since the last round actually reviewed ({since[:10]} → {cur[:10]})\n"


def _part_for(text, path):
    """The slice of `text` that is this one file's own part of a scoped-diff result: from its
    'diff --git a/<path> b/<path>' line up to (not including) the next file's header or the trailing
    '[diff too large...]' note, whichever comes first."""
    marker = f"diff --git a/{path} b/{path}"
    assert marker in text, (path, text[-500:])
    idx = text.index(marker)
    rest = text[idx:]
    m = re.search(r"(?m)^diff --git ", rest[1:])
    end = (m.start() + 1) if m else len(rest)
    note_m = re.search(r"\n\[diff too large", rest[:end])
    if note_m:
        end = note_m.start()
    return rest[:end]


def _header_block(one_file_diff):
    """git's own header lines for one file, up to and including the '+++ ...' line (and its newline)."""
    m = re.search(r"^\+\+\+ .*$", one_file_diff, re.M)
    assert m, one_file_diff[:300]
    return one_file_diff[: m.end()] + "\n"


def _note(paths):
    return (f"\n[diff too large: these changed files are NOT shown and were NOT reviewed by you "
            f"(do not approve without them; say so in a finding): {', '.join(paths)}]")


def _lines_file(n, marker_first=None, marker_mid=None, marker_last=None, mid_line=None):
    mid_line = mid_line or (n // 2)
    out = []
    for i in range(n):
        tag = ""
        if marker_first is not None and i == 0:
            tag = f" {marker_first}"
        if marker_mid is not None and i == mid_line - 1:
            tag = f" {marker_mid}"
        if marker_last is not None and i == n - 1:
            tag = f" {marker_last}"
        out.append(f"row-{i:05d} padding padding{tag}")
    return "\n".join(out) + "\n"


def _code_lines(n, tag, changed=False, marker=None):
    out = []
    for i in range(n):
        tail = f"  // {marker}" if (marker and changed and i == 0) else ""
        val = f"{i}99" if changed else f"{i}"
        out.append(f"x_{tag}_{i} = {val}  # padding padding padding padding{tail}")
    return "\n".join(out) + "\n"


def _rewrite_every_second_line(path, marker):
    text = Path(path).read_text()
    lines = text.split("\n")
    trailing_blank = bool(lines) and lines[-1] == ""
    body = lines[:-1] if trailing_blank else lines
    for i in range(0, len(body), 2):
        body[i] = f"row-{i:05d} CHANGED {marker}"
    new_text = "\n".join(body) + ("\n" if trailing_blank else "")
    Path(path).write_text(new_text)


def _odd_bytes(n):
    """40,000 lines joined by literal '\\n' bytes only, some lines holding U+2028, U+0085, a form feed, a
    vertical tab or a trailing carriage return -- none of which are '\\n' to git -- and no trailing newline
    after the very last line."""
    lines = []
    for i in range(n):
        line = f"row-{i:05d} padding padding".encode()
        if i == 100:
            line += " ".encode()
        elif i == 200:
            line += "\u0085".encode()
        elif i == 300:
            line += b"\x0c"
        elif i == 400:
            line += b"\x0b"
        elif i == 500:
            line += b"\r"
        lines.append(line)
    return b"\n".join(lines)


# ---- hand-built diff-chunk builders (for shapes real git never prints) --------------------------------------

def _delete_lines(path, n):
    header = [f"diff --git a/{path} b/{path}", "deleted file mode 100644", "index a0a0a0a..0000000",
              f"--- a/{path}", "+++ /dev/null", f"@@ -1,{n} +0,0 @@"]
    body = [f"-row-{i:05d} padding padding" for i in range(n)]
    return header + body


def _add_lines(path, n):
    header = [f"diff --git a/{path} b/{path}", "new file mode 100644", "index 0000000..b0b0b0b",
              "--- /dev/null", f"+++ b/{path}", f"@@ -0,0 +1,{n} @@"]
    body = [f"+row-{i:05d} padding padding" for i in range(n)]
    return header + body


def _u2028_delete(path):
    """40,001 by the @@ header; really 40,000 '\\n'-separated lines (one line holds an embedded U+2028, which
    str.splitlines() -- but never a literal '\\n' split -- would count as a second line)."""
    body = [f"-row-{i:05d} padding padding" for i in range(39999)]
    body.append("-rowMERGED padding -ZQ-SMUGGLED")
    header = [f"diff --git a/{path} b/{path}", "deleted file mode 100644", "index a0a0a0a..0000000",
              f"--- a/{path}", "+++ /dev/null", "@@ -1,40001 +0,0 @@"]
    return header + body


def _text(lines):
    return "\n".join(lines) + "\n"


def _delete_chunk_exact_len(path, total_chars):
    """A textbook single-line pure-delete chunk for `path`, padded so its own text is exactly `total_chars`
    characters long."""
    head = [f"diff --git a/{path} b/{path}", "deleted file mode 100644", "index a0a0a0a..0000000",
            f"--- a/{path}", "+++ /dev/null", "@@ -1,1 +0,0 @@"]
    prefix = "\n".join(head) + "\n-"
    pad = total_chars - len(prefix) - 1  # -1 for the body line's own trailing newline
    assert pad >= 0, (total_chars, len(prefix))
    return prefix + ("a" * pad) + "\n"


@contextlib.contextmanager
def _stub_diff_tree(diff_text):
    """Replaces ER.git with a stub that returns `diff_text` for the `diff-tree` call scoped_diff_parts makes,
    so a diff shape real git never prints can still go through scoped_diff_parts (and the detector it calls)
    without this test ever naming that private function."""
    orig = ER.git

    def fake(root, *args):
        if args and args[0] == "diff-tree":
            return diff_text
        return orig(root, *args)

    ER.git = fake
    try:
        yield
    finally:
        ER.git = orig


SINCE_ID, CUR_ID = "0000000000", "1111111111"


def _scoped(diff_text_or_lines):
    text = diff_text_or_lines if isinstance(diff_text_or_lines, str) else _text(diff_text_or_lines)
    with _stub_diff_tree(text):
        return ER.scoped_diff_parts(Path("/nonexistent-cp1-stub-root"), SINCE_ID, CUR_ID)


def _check_omitted_stub(cid, msg, lines, path):
    text, shown, omitted = _scoped(lines)
    expected = HEADER + _note([path])
    check(f"[{cid}] {msg}: never summarized -- {path} stays omitted, named, with no summary line",
          text == expected and shown == [] and omitted == [path], (text[-300:], shown, omitted))


# ---- end-to-end project builder (reuses test_fix_round.py / test_fix_round_diff.py helpers) ------------------

def _ext_fixture(name, findings, extra_files=None, cfg=()):
    """A codex-standards + host-correctness project (test_fix_round_diff.py's _ext_start shape), with any
    `extra_files` (relative path -> str or bytes content) written before round 1 opens, then the usual three
    marker source files seeded on top."""
    depth = {"normal": ["codex-standards", "host-correctness"]}
    d, env = tf._ext_project(name, depth, [("codex", "openai", "gpt-5")], config=list(cfg),
                             replies={"codex": (tfd._reply(*findings), tfd.NO_FINDINGS)})
    for rel, content in (extra_files or {}).items():
        p = Path(d) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content)
    tfd._seed(d, env)
    return d, env, Path(env["PATH"])


def _ext_review_and_record(d, rnd, env, age):
    """Runs the real reviewer(s) for `rnd` (command-kind through external_review.py; any host reviewer written
    clean), ages each review file, then adv-record. Never hard-asserts on adv-record's result, so a wrong guess
    about its exit code fails this case's own checks instead of crashing the whole test file."""
    paths = []
    for rv in rnd["reviewers"]:
        if rv.get("kind") == "command":
            paths.append(tf._ext_review(d, rnd, rv["id"], env))
        else:
            paths.append(g.write_review(d, rnd, rv["id"], []))
    for p in paths:
        tfd._age(d, p, age)
    r = tf._record(d, rnd, paths, env=env)
    return r, paths


# ================================================================================================ CP1-T01..T05

def t01_delete_1mb_file_shows_headers_and_one_line():
    d = _mk_repo("t01")
    (d / "data").mkdir()
    (d / "data/big.txt").write_text(_lines_file(40000, marker_first="ZQ-FIRST", marker_mid="ZQ-MIDDLE",
                                                 marker_last="ZQ-LAST", mid_line=20000))
    since = _snapshot(d)
    (d / "data/big.txt").unlink()
    cur = _snapshot(d)
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    check("[CP1-T01] data/big.txt is in shown and omitted is empty",
          shown == ["data/big.txt"] and omitted == [], (shown, omitted))
    if "data/big.txt" not in shown:
        check("[CP1-T01] data/big.txt's part can be inspected (it was not shown at all)", False,
              "data/big.txt was omitted, not shown -- the rest of this case's checks do not apply")
        return
    real = _real_diff(d, since, cur)
    header_block = _header_block(real)
    part = _part_for(text, "data/big.txt")
    check("[CP1-T01] its part starts with git's own delete headers, copied verbatim",
          part.startswith(header_block), (part[:400], header_block))
    rest = part[len(header_block):]
    check("[CP1-T01] no '@@' line and no '-' body line for it appear",
          "@@" not in rest and not re.search(r"(?m)^-", rest), rest)
    check("[CP1-T01] the summary names data/big.txt, says deleted and gives 40000 (or 40,000) lines, "
          "without saying 'shown'/'reviewed'",
          "data/big.txt" in part and re.search(r"delet", rest, re.I)
          and ("40000" in rest or "40,000" in rest) and "shown" not in rest.lower()
          and "reviewed" not in rest.lower(), rest)
    check("[CP1-T01] ZQ-FIRST, ZQ-MIDDLE and ZQ-LAST are absent",
          not any(mk in text for mk in ("ZQ-FIRST", "ZQ-MIDDLE", "ZQ-LAST")), "")
    check("[CP1-T01] there is no 'NOT shown' note", "NOT shown" not in text, text[-300:])
    check("[CP1-T01] the whole text is under MAX_DIFF", len(text) < ER.MAX_DIFF, len(text))


def t02_fix_round_deleting_a_1mb_file_can_be_recorded():
    with tf._own_home():
        fnd = tfd._finding("ZD-1", "src/alpha.js", 2, "DESC-D1 input is not checked", "FIX-D1 check it")
        d, env, b = _ext_fixture("cp1t02", [fnd], {"data/big.txt": _lines_file(40000)})
        rnd1 = tfd._ext_blocked_round(d, env, "CP1-T02")
        tf._triage(d, ["codex-standards/ZD-1"], env=env)
        (Path(d) / "data/big.txt").unlink()
        tfd._edit(d, "src/alpha.js",
                  tfd._src("alpha", tfd.ALPHA).replace("alpha_two = 2;", "alpha_two = 22; // ZQ-BIGFIX"), env)
        rnd2 = tf._open(d, env=env)
        check("[CP1-T02] round 2 is the one-reviewer fix round after the block",
              tf._ids(rnd2) == ["codex-standards"] and rnd2.get("small_fix") is True, tf._ids(rnd2))
        p = tfd._prompt(d, env, rnd2["reviewers"][0])
        check("[CP1-T02] the prompt contains the delete summary line for data/big.txt",
              "data/big.txt" in p and re.search(r"delet", p, re.I) and ("40000" in p or "40,000" in p),
              p[-2000:])
        check("[CP1-T02] the prompt shows the alpha.js change", tfd._added(p, "ZQ-BIGFIX"), p[-800:])
        check("[CP1-T02] the prompt has no 'NOT shown' note", "NOT shown" not in p, p[-500:])
        tfd._rewrite_cli(b, "codex", tfd.NO_FINDINGS)
        r, paths = _ext_review_and_record(d, rnd2, env, 60)
        check("[CP1-T02] adv-record exits 0 and the round is recorded (not refused)",
              r.returncode == 0 and tf._gate(d).get("status") == "passed",
              (r.returncode, tfd._out(r), tf._gate(d).get("status")))
        rev = json.loads(Path(paths[0]).read_text())
        check("[CP1-T02] the signed review has no omitted_files and files_reviewed includes both changed files",
              not rev.get("omitted_files")
              and {"data/big.txt", "src/alpha.js"} <= set(rev.get("files_reviewed") or []),
              rev.get("files_reviewed"))


def t03_fix_round_adding_a_huge_file_is_never_summarized():
    # library
    d = _mk_repo("t03lib")
    (d / "src").mkdir()
    (d / "src/app.py").write_text("line1\nline2\nline3\nline4\nline5\n")
    since = _snapshot(d)
    (d / "src/app.py").write_text("line1\nCHANGED  # ZQ-REAL-FIX\nline3\nline4\nline5\n")
    (d / "vendor").mkdir()
    (d / "vendor/blob.js").write_text(_lines_file(30000, marker_first="ZQ-ADD-FIRST", marker_last="ZQ-ADD-LAST"))
    cur = _snapshot(d)
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    check("[CP1-T03] library: src/app.py is shown and vendor/blob.js is omitted (a pure add is never summarized)",
          shown == ["src/app.py"] and omitted == ["vendor/blob.js"], (shown, omitted))
    real = _real_diff(d, since, cur)
    app_real = _part_for(real, "src/app.py")
    check("[CP1-T03] library: src/app.py's full chunk (with ZQ-REAL-FIX) is present verbatim",
          app_real in text and "ZQ-REAL-FIX" in text, "")
    check("[CP1-T03] library: vendor/blob.js is named in the NOT-shown note, with no '[file added' summary line",
          "NOT shown" in text and "vendor/blob.js" in text[text.index("NOT shown"):]
          and "[file added" not in text and "diff --git a/vendor/blob.js" not in text, text[-400:])
    check("[CP1-T03] library: ZQ-ADD-FIRST and ZQ-ADD-LAST are absent",
          "ZQ-ADD-FIRST" not in text and "ZQ-ADD-LAST" not in text, "")
    # end to end: adding a huge file as part of a fix round after a block
    with tf._own_home():
        fnd = tfd._finding("ZE-1", "src/alpha.js", 2, "DESC-E1 the handler is unsafe", "FIX-E1 make it safe")
        d2, env, b = _ext_fixture("cp1t03e2e", [fnd])
        tfd._ext_blocked_round(d2, env, "CP1-T03")
        tf._triage(d2, ["codex-standards/ZE-1"], env=env)
        (Path(d2) / "vendor").mkdir()
        (Path(d2) / "vendor/blob.js").write_text(_lines_file(30000))
        tfd._edit(d2, "src/alpha.js",
                  tfd._src("alpha", tfd.ALPHA).replace("alpha_two = 2;", "alpha_two = 22; // ZQ-ADDFIX"), env)
        rnd2 = tf._open(d2, env=env)
        check("[CP1-T03] end to end: round 2 is the one-reviewer fix round after the block",
              tf._ids(rnd2) == ["codex-standards"] and rnd2.get("small_fix") is True, tf._ids(rnd2))
        p = tfd._prompt(d2, env, rnd2["reviewers"][0])
        idx = p.find("NOT shown")
        check("[CP1-T03] end to end: the prompt names vendor/blob.js after 'NOT shown' and has no "
              "'[file added' summary line",
              idx != -1 and "vendor/blob.js" in p[idx:] and "[file added" not in p, p[-800:])
        tfd._rewrite_cli(b, "codex", tfd.NO_FINDINGS)
        r, paths = _ext_review_and_record(d2, rnd2, env, 60)
        rev = json.loads(Path(paths[0]).read_text())
        check("[CP1-T03] end to end: the signed review has omitted_files == ['vendor/blob.js']",
              rev.get("omitted_files") == ["vendor/blob.js"], rev.get("omitted_files"))
        check("[CP1-T03] end to end: adv-record refuses, naming vendor/blob.js, and the round is not passed",
              r.returncode != 0 and "vendor/blob.js" in tfd._out(r) and tf._gate(d2).get("status") != "passed",
              (r.returncode, tfd._out(r), tf._gate(d2).get("status")))


def t04_huge_add_is_omitted_not_summarized():
    d = _mk_repo("t04")
    (d / "src").mkdir()
    (d / "src/a.py").write_text("a1\na2\na3\n")
    (d / "src/c.py").write_text(_code_lines(700, "c"))
    since = _snapshot(d)
    (d / "src/a.py").write_text("a1\nCHANGED  # ZQ-A\na3\n")
    (d / "gen").mkdir()
    (d / "gen/out.json").write_text(_lines_file(7000))
    (d / "src/c.py").write_text(_code_lines(700, "c", changed=True, marker="ZQ-C"))
    cur = _snapshot(d)
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    real = _real_diff(d, since, cur)
    check("[CP1-T04] (fixture sanity) gen/out.json's own chunk alone is over MAX_DIFF",
          len(_scoped_header(since, cur)) + len(_part_for(real, "gen/out.json")) > ER.MAX_DIFF, len(real))
    check("[CP1-T04] gen/out.json is omitted (a pure add is never summarized) and src/a.py, src/c.py are shown",
          omitted == ["gen/out.json"] and shown == ["src/a.py", "src/c.py"], (shown, omitted))
    a_real, c_real = _part_for(real, "src/a.py"), _part_for(real, "src/c.py")
    check("[CP1-T04] src/a.py's and src/c.py's full git chunks appear verbatim (ZQ-A and ZQ-C present)",
          a_real in text and c_real in text and "ZQ-A" in text and "ZQ-C" in text, "")
    check("[CP1-T04] the text is byte for byte header + src/a.py + src/c.py + the NOT-shown note naming "
          "gen/out.json, with no '[file added' summary line",
          text == _scoped_header(since, cur) + a_real + c_real + _note(["gen/out.json"])
          and "[file added" not in text, text[-300:])
    check("[CP1-T04] the whole text is at most MAX_DIFF characters", len(text) <= ER.MAX_DIFF, len(text))


def t05_huge_modification_still_named_and_blocks():
    d = _mk_repo("t05")
    (d / "data").mkdir()
    (d / "src").mkdir()
    (d / "data/big.txt").write_text(_lines_file(40000))
    (d / "src/app.py").write_text("a = 1\nb = 2\n")
    since = _snapshot(d)
    _rewrite_every_second_line(d / "data/big.txt", "ZQ-MOD")
    (d / "src/app.py").write_text("a = 11\nb = 2\n")
    cur = _snapshot(d)
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    header = _scoped_header(since, cur)
    real = _real_diff(d, since, cur)
    app_chunk = _part_for(real, "src/app.py")
    expected = header + app_chunk + _note(["data/big.txt"])
    check("[CP1-T05] shown is ['src/app.py'] and omitted is ['data/big.txt']",
          shown == ["src/app.py"] and omitted == ["data/big.txt"], (shown, omitted))
    check("[CP1-T05] the text is byte for byte header + src/app.py's chunk + today's NOT-shown note",
          text == expected, (text[-300:], expected[-300:]))
    check("[CP1-T05] no ZQ-MOD marker and no summary line for data/big.txt appear anywhere",
          "ZQ-MOD" not in text and not re.search(r"\b\d[\d,]*\s+lines?\b.{0,40}(omitted|left out)", text, re.I), "")


# ================================================================================================ CP1-T06..T08

def t06_huge_modification_still_blocks_end_to_end():
    with tf._own_home():
        fnd = tfd._finding("ZF-1", "src/alpha.js", 2, "DESC-F1 the handler is unsafe", "FIX-F1 make it safe")
        d, env, b = _ext_fixture("cp1t06", [fnd], {"data/big.txt": _lines_file(40000)})
        rnd1 = tfd._ext_blocked_round(d, env, "CP1-T06")
        tf._triage(d, ["codex-standards/ZF-1"], env=env)
        _rewrite_every_second_line(Path(d) / "data/big.txt", "ZQ-MOD")
        tfd._edit(d, "src/alpha.js",
                  tfd._src("alpha", tfd.ALPHA).replace("alpha_two = 2;", "alpha_two = 22; // ZQ-MODFIX"), env)
        rnd2 = tf._open(d, env=env)
        check("[CP1-T06] round 2 is the one-reviewer fix round after the block",
              tf._ids(rnd2) == ["codex-standards"] and rnd2.get("small_fix") is True, tf._ids(rnd2))
        p = tfd._prompt(d, env, rnd2["reviewers"][0])
        idx = p.find("NOT shown")
        check("[CP1-T06] the prompt names data/big.txt after 'NOT shown'",
              idx != -1 and "data/big.txt" in p[idx:], p[-800:])
        check("[CP1-T06] the prompt does not hold a summary line claiming data/big.txt's content was shown",
              not re.search(r"data/big\.txt.{0,200}(deleted|added).{0,120}(left out|omitted)", p, re.S), "")
        tfd._rewrite_cli(b, "codex", tfd.NO_FINDINGS)
        r, paths = _ext_review_and_record(d, rnd2, env, 60)
        rev = json.loads(Path(paths[0]).read_text())
        check("[CP1-T06] the signed review has omitted_files == ['data/big.txt'] and files_reviewed "
              "== ['src/alpha.js']",
              rev.get("omitted_files") == ["data/big.txt"] and rev.get("files_reviewed") == ["src/alpha.js"],
              (rev.get("omitted_files"), rev.get("files_reviewed")))
        check("[CP1-T06] adv-record exits non-zero, its output names data/big.txt and says such a review "
              "never counts",
              r.returncode != 0 and "data/big.txt" in tfd._out(r), tfd._out(r))
        check("[CP1-T06] the round is not recorded as passed",
              tf._gate(d).get("status") != "passed", tf._gate(d).get("status"))


def t07_delete_and_modification_in_same_round():
    with tf._own_home():
        fnd = tfd._finding("ZG-1", "src/alpha.js", 2, "DESC-G1 the handler is unsafe", "FIX-G1 make it safe")
        d, env, b = _ext_fixture("cp1t07", [fnd], {"data/old.txt": _lines_file(40000),
                                                     "data/keep.txt": _lines_file(40000)})
        rnd1 = tfd._ext_blocked_round(d, env, "CP1-T07")
        tf._triage(d, ["codex-standards/ZG-1"], env=env)
        (Path(d) / "data/old.txt").unlink()
        _rewrite_every_second_line(Path(d) / "data/keep.txt", "ZQ-KEEP")
        tfd._edit(d, "src/alpha.js",
                  tfd._src("alpha", tfd.ALPHA).replace("alpha_two = 2;", "alpha_two = 22; // ZQ-MIXFIX"), env)
        rnd2 = tf._open(d, env=env)
        check("[CP1-T07] round 2 is the one-reviewer fix round after the block",
              tf._ids(rnd2) == ["codex-standards"] and rnd2.get("small_fix") is True, tf._ids(rnd2))
        text, shown, omitted = ER.scoped_diff_parts(Path(d), rnd1["fingerprint"], rnd2["fingerprint"])
        check("[CP1-T07] library: data/old.txt is shown (headers plus one summary line), not omitted",
              "data/old.txt" in shown and "data/old.txt" not in omitted, (shown, omitted))
        check("[CP1-T07] library: omitted is exactly ['data/keep.txt'] and the NOT-shown note names only it",
              omitted == ["data/keep.txt"] and "NOT shown" in text
              and "data/old.txt" not in text[text.index("NOT shown"):], (omitted, text[-300:]))
        tfd._rewrite_cli(b, "codex", tfd.NO_FINDINGS)
        r, paths = _ext_review_and_record(d, rnd2, env, 60)
        rev = json.loads(Path(paths[0]).read_text())
        check("[CP1-T07] end to end: omitted_files is exactly ['data/keep.txt']",
              rev.get("omitted_files") == ["data/keep.txt"], rev.get("omitted_files"))
        check("[CP1-T07] end to end: adv-record refuses, naming data/keep.txt only",
              r.returncode != 0 and "data/keep.txt" in tfd._out(r), tfd._out(r))


def t08_emptied_or_filled_tracked_file_is_a_modification():
    d = _mk_repo("t08")
    (d / "data").mkdir()
    (d / "data/big.txt").write_text(_lines_file(40000))
    (d / "data/empty.txt").write_text("")
    since = _snapshot(d)
    (d / "data/big.txt").write_text("")
    (d / "data/empty.txt").write_text(_lines_file(40000))
    cur = _snapshot(d)
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    header = _scoped_header(since, cur)
    check("[CP1-T08] both files are omitted, in git's order, neither shown",
          omitted == ["data/big.txt", "data/empty.txt"] and shown == [], (shown, omitted))
    check("[CP1-T08] the text equals today's header + the NOT-shown note (no summary line for either)",
          text == header + _note(["data/big.txt", "data/empty.txt"]), text[-400:])


# ================================================================================================ CP1-T09..T14

def t09_delete_with_one_added_line_slipped_in():
    path = "data/x.txt"
    base = _delete_lines(path, 40000)
    idx = 6 + 19999
    v1 = list(base)
    v1[idx] = "+ZQ-SMUGGLED"
    _check_omitted_stub("CP1-T09", "a '+' line slipped into line 20,000 of the delete", v1, path)
    v2 = list(base)
    v2[idx] = " ZQ-SMUGGLED"
    _check_omitted_stub("CP1-T09", "a ' ' context line slipped into line 20,000 of the delete", v2, path)


def t10_add_with_one_removed_line_slipped_in():
    path = "vendor/y.js"
    base = _add_lines(path, 40000)
    idx = 6 + 19999
    v = list(base)
    v[idx] = "-ZQ-SMUGGLED"
    _check_omitted_stub("CP1-T10", "a '-' line slipped into the add", v, path)


def t11_second_hunk_disqualifies():
    path = "data/x.txt"
    base = _delete_lines(path, 40000)
    extra_minus = ["@@ -40001,5 +0,0 @@"] + [f"-extra-{i}" for i in range(5)]
    extra_smuggled = ["@@ -40001,5 +0,0 @@", "+ZQ-SMUGGLED"] + [f"-extra-{i}" for i in range(4)]
    _check_omitted_stub("CP1-T11", "delete with a second all-'-' hunk", base + extra_minus, path)
    _check_omitted_stub("CP1-T11", "delete with a second hunk holding a smuggled '+' line",
                        base + extra_smuggled, path)
    apath = "vendor/y.js"
    abase = _add_lines(apath, 40000)
    aextra = ["@@ -0,0 +30001,5 @@"] + [f"+extra-{i}" for i in range(5)]
    _check_omitted_stub("CP1-T11", "add with a second hunk", abase + aextra, apath)


def t12_line_count_mismatch_disqualifies():
    path = "data/x.txt"
    base = _delete_lines(path, 40000)
    _check_omitted_stub("CP1-T12", "delete header says 40000 but the body has 40001 lines",
                        base + ["-extra-line"], path)
    _check_omitted_stub("CP1-T12", "delete header says 40000 but the body has 39999 lines",
                        base[:-1], path)
    apath = "vendor/y.js"
    abase = _add_lines(apath, 30000)
    _check_omitted_stub("CP1-T12", "add header says 30000 but the body has 30001 lines",
                        abase + ["+extra-line"], apath)
    _check_omitted_stub("CP1-T12", "delete header says 40001, matching real '\\n'-separated lines only if "
                        "U+2028 were (wrongly) treated as a line break", _u2028_delete(path), path)


def t13_header_oddities_disqualify():
    path = "data/x.txt"
    base = _delete_lines(path, 40000)
    _check_omitted_stub("CP1-T13", "a 'similarity index 100%' line present",
                        base[:1] + ["similarity index 100%"] + base[1:], path)
    _check_omitted_stub("CP1-T13", "'rename from'/'rename to' instead of 'deleted file mode'",
                        base[:1] + ["rename from data/x.txt", "rename to data/z.txt"] + base[2:], path)
    _check_omitted_stub("CP1-T13", "'copy from'/'copy to' instead of 'deleted file mode'",
                        base[:1] + ["copy from data/x.txt", "copy to data/z.txt"] + base[2:], path)
    _check_omitted_stub("CP1-T13", "'old mode'/'new mode' lines present",
                        base[:1] + ["old mode 100644", "new mode 100755"] + base[1:], path)
    _check_omitted_stub("CP1-T13", "'deleted file mode' appears twice",
                        base[:2] + [base[1]] + base[2:], path)
    _check_omitted_stub("CP1-T13", "both 'new file mode' and 'deleted file mode' present",
                        base[:1] + ["new file mode 100644"] + base[1:], path)
    _check_omitted_stub("CP1-T13", "no index line", base[:2] + base[3:], path)
    v_plusb = list(base)
    v_plusb[4] = f"+++ b/{path}"
    _check_omitted_stub("CP1-T13", "'+++ b/x' instead of '+++ /dev/null'", v_plusb, path)
    v_other = list(base)
    v_other[3] = "--- a/data/other.txt"
    _check_omitted_stub("CP1-T13", "'--- a/other.txt' names a different path than the diff --git line",
                        v_other, path)
    apath = "vendor/y.js"
    abase = _add_lines(apath, 40000)
    v_addpath = list(abase)
    v_addpath[3] = f"--- a/{apath}"
    _check_omitted_stub("CP1-T13", "add with '--- a/x' instead of '--- /dev/null'", v_addpath, apath)


def t14_only_one_trailing_no_newline_marker_is_allowed():
    path = "data/x.txt"
    base = _delete_lines(path, 40000)
    marker = r"\ No newline at end of file"
    valid = base + [marker]
    text, shown, omitted = _scoped(valid)
    header_block = "\n".join(valid[:5]) + "\n"
    part = _part_for(text, path) if path in shown else ""
    rest = part[len(header_block):] if part else ""
    check("[CP1-T14] exactly one trailing marker: the file is summarized (shown, headers plus one line, "
          "40000 lines)",
          path in shown and path not in omitted and part.startswith(header_block) and "@@" not in rest
          and ("40000" in rest or "40,000" in rest), part[-300:])
    _check_omitted_stub("CP1-T14", "two trailing 'No newline' markers", base + [marker, marker], path)
    _check_omitted_stub("CP1-T14", "a 'No newline' marker in the middle of the body",
                        base[:6 + 20000] + [marker] + base[6 + 20000:], path)
    _check_omitted_stub("CP1-T14", "one extra ' context' line after the body", base + [" extra context line"],
                        path)
    _check_omitted_stub("CP1-T14", "one extra 'garbage' line after the body", base + ["garbage"], path)


# ================================================================================================ CP1-T15..T19

def t15_real_delete_with_unusual_characters_counts_right():
    d = _mk_repo("t15")
    (d / "data").mkdir()
    (d / "data/odd.txt").write_bytes(_odd_bytes(40000))
    since = _snapshot(d)
    (d / "data/odd.txt").unlink()
    cur = _snapshot(d)
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    check("[CP1-T15] data/odd.txt is in shown, omitted is empty",
          shown == ["data/odd.txt"] and omitted == [], (shown, omitted))
    real = _real_diff(d, since, cur)
    m = re.search(r"@@ -1,(\d+) \+0,0 @@", real)
    check("[CP1-T15] (fixture sanity) git's own header reports 40000 lines",
          m is not None and m.group(1) == "40000", real[:300])
    if "data/odd.txt" not in shown:
        check("[CP1-T15] data/odd.txt's summary line can be inspected (it was not shown at all)", False,
              "data/odd.txt was omitted, not shown")
        return
    part = _part_for(text, "data/odd.txt")
    check("[CP1-T15] its summary line gives 40000 (or 40,000) lines, the count git's own header gives",
          "40000" in part or "40,000" in part, part[-300:])


def t16_small_round_is_unchanged():
    d = _mk_repo("t16")
    (d / "src").mkdir()
    (d / "src/alpha.js").write_text("line1\nline2\nline3\n")
    (d / "src/gone.js").write_text("\n".join(f"g{i}" for i in range(10)) + "\n")
    since = _snapshot(d)
    (d / "src/alpha.js").write_text("line1\nCHANGED\nline3\n")
    (d / "src/new.js").write_text("\n".join(f"n{i}" for i in range(10)) + "\n")
    (d / "src/gone.js").unlink()
    cur = _snapshot(d)
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    real = _real_diff(d, since, cur)
    header = _scoped_header(since, cur)
    expected_shown = [f for f, _ in ER._diff_file_chunks(real)]
    check("[CP1-T16] text is byte for byte the header plus git diff-tree's raw output",
          text == header + real, (text[:200], (header + real)[:200]))
    check("[CP1-T16] shown lists the three files in git's order and omitted is empty",
          shown == expected_shown and omitted == [], (shown, omitted, expected_shown))
    check("[CP1-T16] the new file's '+' lines and the deleted file's '-' lines are present in full",
          all(f"+{l}" in text for l in ("n0", "n5", "n9")) and all(f"-{l}" in text for l in ("g0", "g5", "g9")),
          "")
    check("[CP1-T16] no summary line appears",
          not re.search(r"\d[\d,]*\s+lines?\b.{0,40}(omitted|left out|not included)", text, re.I), "")


def t17_under_limit_shown_in_full_and_boundary_is_exact():
    d = _mk_repo("t17")
    (d / "data").mkdir()
    (d / "src").mkdir()
    (d / "data/big.txt").write_text(_lines_file(3700))
    (d / "src/edit.py").write_text("a = 1\nb = 2\n")
    since = _snapshot(d)
    (d / "data/big.txt").unlink()
    (d / "src/edit.py").write_text("a = 11\nb = 2\n")
    cur = _snapshot(d)
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    header = _scoped_header(since, cur)
    real = _real_diff(d, since, cur)
    check("[CP1-T17] a 100KB pure delete well under MAX_DIFF: header + the whole diff unchanged, nothing "
          "omitted or summarized",
          text == header + real and omitted == [] and sorted(shown) == ["data/big.txt", "src/edit.py"]
          and len(text) < ER.MAX_DIFF, (len(text), shown, omitted))
    path = "data/boundary.txt"
    total_a = ER.MAX_DIFF - len(HEADER)
    chunk_a = _delete_chunk_exact_len(path, total_a)
    check("[CP1-T17] (fixture sanity) header + chunk_a is exactly MAX_DIFF characters",
          len(HEADER) + len(chunk_a) == ER.MAX_DIFF, len(HEADER) + len(chunk_a))
    text_a, shown_a, omitted_a = _scoped(chunk_a)
    check("[CP1-T17] exactly at MAX_DIFF: shown in full, no summary, omitted empty",
          text_a == HEADER + chunk_a and shown_a == [path] and omitted_a == [], len(text_a))
    chunk_b = _delete_chunk_exact_len(path, total_a + 1)
    text_b, shown_b, omitted_b = _scoped(chunk_b)
    header_block_b = "\n".join(chunk_b.split("\n")[:5]) + "\n"
    part_b = _part_for(text_b, path) if path in shown_b else ""
    rest_b = part_b[len(header_block_b):] if part_b else ""
    check("[CP1-T17] MAX_DIFF + 1: the pure delete is summarized (shown) instead of omitted, as today's code "
          "would do",
          path in shown_b and path not in omitted_b and part_b.startswith(header_block_b) and "@@" not in rest_b,
          (shown_b, omitted_b, part_b[-200:]))


def t18_summarizes_only_as_many_as_needed_biggest_first():
    d = _mk_repo("t18")
    (d / "data").mkdir()
    (d / "src").mkdir()
    (d / "data/a.txt").write_text(_lines_file(2900))
    (d / "data/b.txt").write_text(_lines_file(2500))
    (d / "src/c.py").write_text(_code_lines(700, "t18c"))
    since = _snapshot(d)
    (d / "data/a.txt").unlink()
    (d / "data/b.txt").unlink()
    (d / "src/c.py").write_text(_code_lines(700, "t18c", changed=True, marker="ZQ-T18C"))
    cur = _snapshot(d)
    text, shown, omitted = ER.scoped_diff_parts(d, since, cur)
    real = _real_diff(d, since, cur)
    check("[CP1-T18] (fixture sanity) the whole diff is over MAX_DIFF",
          len(_scoped_header(since, cur)) + len(real) > ER.MAX_DIFF, len(real))
    check("[CP1-T18] data/a.txt (biggest) is summarized: shown, with no '@@' line",
          "data/a.txt" in shown and "@@" not in _part_for(text, "data/a.txt"), "")
    check("[CP1-T18] data/b.txt and src/c.py are shown in full, and omitted is empty",
          "data/b.txt" in shown and "src/c.py" in shown and omitted == [], (shown, omitted))
    check("[CP1-T18] data/b.txt's chunk is present verbatim (not summarized)",
          _part_for(real, "data/b.txt") in text, "")
    check("[CP1-T18] src/c.py's chunk is present verbatim", _part_for(real, "src/c.py") in text, "")
    # tie: two pure deletes of exactly the same size
    d2 = _mk_repo("t18tie")
    (d2 / "data").mkdir()
    (d2 / "data/aa.txt").write_text(_lines_file(3400))
    (d2 / "data/ab.txt").write_text(_lines_file(3400))
    since2 = _snapshot(d2)
    (d2 / "data/aa.txt").unlink()
    (d2 / "data/ab.txt").unlink()
    cur2 = _snapshot(d2)
    text2, shown2, omitted2 = ER.scoped_diff_parts(d2, since2, cur2)
    real2 = _real_diff(d2, since2, cur2)
    check("[CP1-T18] tie: the file git prints first (data/aa.txt) is the one summarized",
          "data/aa.txt" in shown2 and "@@" not in _part_for(text2, "data/aa.txt"), "")
    check("[CP1-T18] tie: the other (data/ab.txt) is shown in full, and omitted is empty",
          "data/ab.txt" in shown2 and omitted2 == [] and _part_for(real2, "data/ab.txt") in text2,
          text2[-300:])


def _paragraphs(text):
    return [x for x in text.split("\n") if x.strip()]


def _some(text, *patterns):
    return any(all(re.search(p, para, re.I | re.S) for p in patterns) for para in _paragraphs(text))


SUMMARY_PATTERNS = [r"(pure|single[- ]hunk)?\s*(add(ed)?|delet(ed)?)\b", r"git'?s?\s+(own\s+)?headers?",
                    r"one line|a single line", r"lines?\s+(were\s+)?(left out|omitted|not includ)"]
COUNTS_AS_SHOWN = [r"counts?\s+as\s+shown"]
MOD_STILL_BLOCKS = [r"(real\s+)?modification", r"(too large|over the (size )?limit)", r"not\s+shown",
                    r"(stops?|blocks?)\s+the\s+round|never\s+(records?|counted|be recorded)"]


def t19_docs_describe_the_large_file_summary():
    readme = (ROOT / "README.md").read_text()
    schemas = (ROOT / "skills/ratchet-orchestrator/references/schemas.md").read_text()
    for name, text in (("README.md", readme), ("skills/ratchet-orchestrator/references/schemas.md", schemas)):
        check(f"[CP1-T19] {name} describes the honest one-line summary for a huge pure add or delete",
              _some(text, *SUMMARY_PATTERNS), "no paragraph says it")
        check(f"[CP1-T19] {name} says such a file counts as shown",
              _some(text, *COUNTS_AS_SHOWN), "no paragraph says it")
        check(f"[CP1-T19] {name} says a real modification too large to include is still named as not shown "
              "and still stops the round from being recorded",
              _some(text, *MOD_STILL_BLOCKS), "no paragraph says it")


CASES = [("CP1-T01", t01_delete_1mb_file_shows_headers_and_one_line),
         ("CP1-T02", t02_fix_round_deleting_a_1mb_file_can_be_recorded),
         ("CP1-T03", t03_fix_round_adding_a_huge_file_is_never_summarized),
         ("CP1-T04", t04_huge_add_is_omitted_not_summarized),
         ("CP1-T05", t05_huge_modification_still_named_and_blocks),
         ("CP1-T06", t06_huge_modification_still_blocks_end_to_end),
         ("CP1-T07", t07_delete_and_modification_in_same_round),
         ("CP1-T08", t08_emptied_or_filled_tracked_file_is_a_modification),
         ("CP1-T09", t09_delete_with_one_added_line_slipped_in),
         ("CP1-T10", t10_add_with_one_removed_line_slipped_in),
         ("CP1-T11", t11_second_hunk_disqualifies),
         ("CP1-T12", t12_line_count_mismatch_disqualifies),
         ("CP1-T13", t13_header_oddities_disqualify),
         ("CP1-T14", t14_only_one_trailing_no_newline_marker_is_allowed),
         ("CP1-T15", t15_real_delete_with_unusual_characters_counts_right),
         ("CP1-T16", t16_small_round_is_unchanged),
         ("CP1-T17", t17_under_limit_shown_in_full_and_boundary_is_exact),
         ("CP1-T18", t18_summarizes_only_as_many_as_needed_biggest_first),
         ("CP1-T19", t19_docs_describe_the_large_file_summary)]


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
