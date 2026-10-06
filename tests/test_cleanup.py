#!/usr/bin/env python3
"""
CP1 tests: test suites delete their temp folders.   python3 tests/test_cleanup.py

Proves a new tests/_tmp.py helper exists and every tests/test_*.py suite uses it instead of raw
tempfile.mkdtemp/mkstemp, so running a suite -- alone or all together, passing, failing on purpose,
crashing, or calling sys.exit() directly -- leaves no ratchet-* temp folders behind and stops any
background server it started.

Kept fast on purpose: no full regression run. Instead:
  (a) an AST scan of every tests/test_*.py file for raw tempfile.mkdtemp/mkstemp calls outside the
      helper module itself (tests/_tmp.py),
  (b) small child *subprocesses* built around the as-yet-unwritten helper (one each for: a normal
      clean exit, a deliberately failing check, an uncaught exception, a direct sys.exit(), using
      the folder without ever entering it as a context manager, and a crashed background server),
      each run with TMPDIR pointed at a private, empty sandbox directory that this file creates and
      removes itself (never via the helper under test, so before/after comparison is independent of
      whatever the helper does),
  (c) one quick real suite (tests/test_hooks.py) run end to end with a private TMPDIR, asserting
      nothing is left,
  (d) the checkpoint's own two-command sequence, run the same way,
  (e) a git-diff check that no existing check(...) line in any suite that existed at the pinned base
      commit was touched.

Assumed public contract for tests/_tmp.py (this file is the spec for it -- the implementation must
satisfy exactly this, nothing here is a stub):
  - `_tmp.mkdtemp(prefix="...")` creates a fresh directory under the normal tempfile location
    (honouring $TMPDIR) and returns an object whose str() is that directory's path.
  - That object supports the context-manager protocol: `with _tmp.mkdtemp(...) as d:` removes the
    directory (and anything tracked on it, see below) when the `with` block is left, whether
    normally, via sys.exit() raised inside the block, or via an uncaught exception.
  - If the object is never used as a context manager, the directory (and anything tracked on it) is
    still removed at process exit via `atexit`.
  - The object has a `.track(proc)` method that registers a `subprocess.Popen` (or anything with
    `.poll()` / `.terminate()` / `.wait()`) so it is terminated whenever the directory is cleaned up
    (context-manager exit or atexit).

This suite writes a per-test JSON report to $RATCHET_TEST_REPORT when that env var is set:
{"cases": [{"id": "CP1-T01", "status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HELPER_PATH = HERE / "_tmp.py"
BASE_COMMIT = "a24a31d26ae2ac4a598fc3e0c05a336f6304454e"

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP1-[TX]\d+)\]")


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


# ---- sandbox plumbing (this file's own temp handling; never tempfile.mkdtemp/mkstemp) -------------


def make_private_tmpdir(tag: str) -> Path:
    """An empty, uniquely-named sandbox directory for a child process's TMPDIR, built by this test
    itself (not via the helper under test), so before/after comparisons are meaningful on their own."""
    d = Path(tempfile.gettempdir()) / f"ratchet-cp1-priv-{tag}-{os.getpid()}-{uuid.uuid4().hex}"
    d.mkdir(parents=True)
    return d


def cleanup_private(d: Path):
    shutil.rmtree(d, ignore_errors=True)


def listing(d) -> list:
    return sorted(p.name for p in Path(d).iterdir())


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


CHILD_PRELUDE = (
    "import sys, os\n"
    "from pathlib import Path\n"
    "import _tmp\n"
    "print('IMPORTED')\n"
)


def run_child(tag: str, body: str, timeout: int = 30):
    """Write `body` (appended to CHILD_PRELUDE) to a self-cleaning scratch file and run it as a
    subprocess with TMPDIR set to a fresh private sandbox. Returns (CompletedProcess, private_dir).
    The caller owns `private_dir` and must remove it (this only writes the throwaway script)."""
    private = make_private_tmpdir(tag)
    with tempfile.TemporaryDirectory(prefix=f"ratchet-cp1-scratch-{tag}-") as scratch:
        script = Path(scratch) / "child.py"
        script.write_text(CHILD_PRELUDE + body)
        env = dict(os.environ)
        env["TMPDIR"] = str(private)
        existing_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(HERE) + (os.pathsep + existing_pp if existing_pp else "")
        try:
            r = subprocess.run([sys.executable, str(script)], cwd=str(ROOT), capture_output=True,
                                text=True, env=env, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            r = subprocess.CompletedProcess(e.cmd, -1, (e.stdout or b"").decode("utf-8", "replace")
                                             if isinstance(e.stdout, bytes) else (e.stdout or ""),
                                             ((e.stderr or "") + "\nTIMEOUT"))
    return r, private


# ---- CP1-T01: no raw tempfile.mkdtemp/mkstemp outside the helper ----------------------------------


def find_raw_calls(path: Path):
    hits = []
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = None
        if isinstance(f, ast.Attribute) and f.attr in ("mkdtemp", "mkstemp"):
            if isinstance(f.value, ast.Name) and f.value.id == "tempfile":
                name = f.attr
        elif isinstance(f, ast.Name) and f.id in ("mkdtemp", "mkstemp"):
            name = f.id
        if name:
            hits.append((path.name, node.lineno, name))
    return hits


def t01_no_raw_calls_outside_helper():
    print("CP1-T01: no suite creates a temp folder outside the shared helper")
    hits = []
    for f in sorted(ROOT.glob("tests/test_*.py")):
        hits.extend(find_raw_calls(f))
    check("[CP1-T01] no tests/test_*.py file calls tempfile.mkdtemp/mkstemp directly",
          not hits, hits[:10])


# ---- CP1-T02..T06: single-process child scenarios --------------------------------------------------


def t02_normal_clean_exit():
    print("CP1-T02: helper removes its folder on a normal clean exit")
    body = (
        "with _tmp.mkdtemp(prefix='ratchet-cp1-t02-') as d:\n"
        "    print('CREATED')\n"
        "    (Path(str(d)) / 'f.txt').write_text('x')\n"
        "print('DONE')\n"
    )
    r, private = run_child("t02", body)
    try:
        ran = all(m in r.stdout for m in ("IMPORTED", "CREATED", "DONE"))
        check("[CP1-T02] child using the helper's context-manager form runs to completion",
              r.returncode == 0 and ran, (r.returncode, r.stdout, r.stderr[-300:]))
        check("[CP1-T02] the private TMPDIR is empty again after a normal clean exit",
              ran and listing(private) == [], listing(private))
    finally:
        cleanup_private(private)


def t03_failing_check_exits_nonzero():
    print("CP1-T03: helper removes its folder when a check fails on purpose")
    body = (
        "with _tmp.mkdtemp(prefix='ratchet-cp1-t03-') as d:\n"
        "    print('CREATED')\n"
        "    (Path(str(d)) / 'f.txt').write_text('x')\n"
        "    ok = False\n"
        "    if not ok:\n"
        "        print('CHECK_FAILED')\n"
        "        sys.exit(1)\n"
    )
    r, private = run_child("t03", body)
    try:
        ran = all(m in r.stdout for m in ("IMPORTED", "CREATED", "CHECK_FAILED"))
        check("[CP1-T03] child exits non-zero after a simulated failing check inside the with-block",
              r.returncode == 1 and ran, (r.returncode, r.stdout, r.stderr[-300:]))
        check("[CP1-T03] the private TMPDIR is still empty after the check-failure exit",
              ran and listing(private) == [], listing(private))
    finally:
        cleanup_private(private)


def t04_uncaught_exception():
    print("CP1-T04: helper removes its folder when the process crashes with an uncaught exception")
    body = (
        "with _tmp.mkdtemp(prefix='ratchet-cp1-t04-') as d:\n"
        "    print('CREATED')\n"
        "    (Path(str(d)) / 'f.txt').write_text('x')\n"
        "    raise RuntimeError('CP1-T04 simulated crash')\n"
    )
    r, private = run_child("t04", body)
    try:
        ran = "IMPORTED" in r.stdout and "CREATED" in r.stdout
        check("[CP1-T04] child crashes with an uncaught exception",
              r.returncode != 0 and ran and "RuntimeError" in r.stderr
              and "CP1-T04 simulated crash" in r.stderr, (r.returncode, r.stdout, r.stderr[-300:]))
        check("[CP1-T04] the private TMPDIR is still empty after the crash",
              ran and listing(private) == [], listing(private))
    finally:
        cleanup_private(private)


def t05_direct_sys_exit():
    print("CP1-T05: helper removes its folder when the process calls sys.exit() directly")
    body = (
        "with _tmp.mkdtemp(prefix='ratchet-cp1-t05-') as d:\n"
        "    print('CREATED')\n"
        "    (Path(str(d)) / 'f.txt').write_text('x')\n"
        "print('AFTER_WITH')\n"
        "sys.exit(1)\n"
    )
    r, private = run_child("t05", body)
    try:
        ran = all(m in r.stdout for m in ("IMPORTED", "CREATED", "AFTER_WITH"))
        check("[CP1-T05] child calls sys.exit(1) directly after the with-block already exited",
              r.returncode == 1 and ran, (r.returncode, r.stdout, r.stderr[-300:]))
        check("[CP1-T05] the private TMPDIR is still empty after the direct sys.exit",
              ran and listing(private) == [], listing(private))
    finally:
        cleanup_private(private)


def t06_atexit_without_context_manager():
    print("CP1-T06: a folder created without the context-manager form is still cleaned up via atexit")
    body = (
        "d = _tmp.mkdtemp(prefix='ratchet-cp1-t06-')\n"
        "print('CREATED')\n"
        "(Path(str(d)) / 'f.txt').write_text('x')\n"
        "print('DONE')\n"
    )
    r, private = run_child("t06", body)
    try:
        ran = all(m in r.stdout for m in ("IMPORTED", "CREATED", "DONE"))
        check("[CP1-T06] child returns normally without ever using the context-manager form",
              r.returncode == 0 and ran, (r.returncode, r.stdout, r.stderr[-300:]))
        check("[CP1-T06] atexit still removed the folder, leaving the private TMPDIR empty",
              ran and listing(private) == [], listing(private))
    finally:
        cleanup_private(private)


def t07_crashed_background_server():
    print("CP1-T07: a background server started through the helper is stopped when its owner crashes")
    body = (
        "import subprocess, time\n"
        "d = _tmp.mkdtemp(prefix='ratchet-cp1-t07-')\n"
        "print('CREATED')\n"
        "proc = subprocess.Popen([sys.executable, '-m', 'http.server', '0'], cwd=str(d),\n"
        "                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "d.track(proc)\n"
        "print('SERVER_PID:%d' % proc.pid)\n"
        "time.sleep(0.3)\n"
        "raise RuntimeError('CP1-T07 simulated crash')\n"
    )
    r, private = run_child("t07", body, timeout=20)
    try:
        ran = "IMPORTED" in r.stdout and "CREATED" in r.stdout
        pid = None
        for line in r.stdout.splitlines():
            if line.startswith("SERVER_PID:"):
                pid = int(line.split(":", 1)[1])
        check("[CP1-T07] child starts and tracks a background server, then crashes",
              r.returncode != 0 and ran and pid is not None, (r.returncode, r.stdout, r.stderr[-300:]))
        server_dead = False
        if pid is not None:
            for _ in range(30):
                if not pid_alive(pid):
                    server_dead = True
                    break
                time.sleep(0.1)
        check("[CP1-T07] the tracked background server is no longer running after the crash",
              ran and pid is not None and server_dead, pid)
        check("[CP1-T07] the private TMPDIR is empty after the crash",
              ran and listing(private) == [], listing(private))
    finally:
        cleanup_private(private)


# ---- CP1-T08/T09: real-suite and whole-command leak checks -----------------------------------------


def t08_real_suite_leaves_nothing():
    print("CP1-T08: a real suite run with a private temp dir leaves nothing behind")
    private = make_private_tmpdir("t08")
    try:
        before = listing(private)
        env = dict(os.environ)
        env["TMPDIR"] = str(private)
        r = subprocess.run([sys.executable, str(HERE / "test_hooks.py")], cwd=str(ROOT),
                            capture_output=True, text=True, env=env, timeout=120)
        after = listing(private)
        check("[CP1-T08] tests/test_hooks.py runs to completion (pass or fail) under a private TMPDIR",
              r.returncode in (0, 1), (r.returncode, r.stderr[-300:]))
        check("[CP1-T08] the private TMPDIR has the same contents before and after test_hooks.py",
              after == before, (before, after))
    finally:
        cleanup_private(private)


def t09_checkpoint_command_leaves_nothing():
    print("CP1-T09: running the checkpoint's own command leaves no stray temp folders either")
    if os.environ.get("RATCHET_CP1_T09_NESTED") == "1":
        # This file is itself part of the command under test, so the subprocess below re-runs this
        # whole suite. Without this guard that nested run would spawn the same command again, and
        # again, forever. The nested run's own report is discarded (RATCHET_TEST_REPORT is unset for
        # it below), so skipping its own copy of T09 here does not hide anything from the top-level
        # report.
        check("[CP1-T09] nested invocation: not re-run to avoid recursing the whole command", True)
        return
    private = make_private_tmpdir("t09")
    try:
        before = listing(private)
        env = dict(os.environ)
        env["TMPDIR"] = str(private)
        env["RATCHET_CP1_T09_NESTED"] = "1"
        env.pop("RATCHET_TEST_REPORT", None)
        r1 = subprocess.run([sys.executable, str(HERE / "test_cleanup.py")], cwd=str(ROOT),
                             capture_output=True, text=True, env=env, timeout=180)
        if r1.returncode == 0:
            r2 = subprocess.run([sys.executable, str(HERE / "test_hooks.py")], cwd=str(ROOT),
                                 capture_output=True, text=True, env=env, timeout=120)
        else:
            r2 = None
        after = listing(private)
        both_finished = r1.returncode in (0, 1) and (r2 is None or r2.returncode in (0, 1))
        check("[CP1-T09] the checkpoint command runs to completion (pass or fail) under a private TMPDIR",
              both_finished, (r1.returncode, r1.stderr[-300:], None if r2 is None else r2.returncode))
        check("[CP1-T09] the private TMPDIR is empty afterward; the test harness itself does not leak",
              after == before, (before, after))
    finally:
        cleanup_private(private)


# ---- CP1-T10: no existing check() line was touched --------------------------------------------------


def t10_no_existing_check_touched():
    print("CP1-T10: no existing check() assertion was weakened, skipped or removed")
    ls = subprocess.run(["git", "ls-tree", "-r", "--name-only", BASE_COMMIT, "--", "tests"],
                         cwd=str(ROOT), capture_output=True, text=True)
    setup_ok = ls.returncode == 0
    existing = [ln for ln in ls.stdout.splitlines() if re.match(r"tests/test_.*\.py$", ln)]
    check("[CP1-T10] setup: the pinned base commit and its tests/test_*.py files are readable via git",
          setup_ok and existing, (ls.returncode, ls.stderr[-300:]))
    bad = []
    for f in existing:
        d = subprocess.run(["git", "diff", BASE_COMMIT, "--", f], cwd=str(ROOT),
                            capture_output=True, text=True)
        for line in d.stdout.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                continue
            if (line.startswith("+") or line.startswith("-")) and "check(" in line:
                bad.append((f, line.strip()[:200]))
    check("[CP1-T10] no added or removed line in a pre-existing suite contains a call to check(",
          setup_ok and not bad, bad[:10])


TESTS = [
    ("CP1-T01", t01_no_raw_calls_outside_helper),
    ("CP1-T02", t02_normal_clean_exit),
    ("CP1-T03", t03_failing_check_exits_nonzero),
    ("CP1-T04", t04_uncaught_exception),
    ("CP1-T05", t05_direct_sys_exit),
    ("CP1-T06", t06_atexit_without_context_manager),
    ("CP1-T07", t07_crashed_background_server),
    ("CP1-T08", t08_real_suite_leaves_nothing),
    ("CP1-T09", t09_checkpoint_command_leaves_nothing),
    ("CP1-T10", t10_no_existing_check_touched),
]


# ---- CP1 adversarial round 1: TemporaryDirectory/NamedTemporaryFile and tempfile aliases count too ------------

RAW_TEMP_CALLS = ("mkdtemp", "mkstemp", "TemporaryDirectory", "NamedTemporaryFile")
# test_cleanup.py tests the helper itself, so it builds its own sandboxes without it (see its docstring).
X01_EXEMPT = {"test_cleanup.py"}


def find_raw_temp_calls(source: str, filename: str = "<src>"):
    """(file, line, name) for every call that makes a temp folder or file through the tempfile module under any
    alias: `tempfile.TemporaryDirectory()`, `import tempfile as t; t.mkdtemp()`, `from tempfile import
    NamedTemporaryFile as N; N()` and the like."""
    tree = ast.parse(source, filename=filename)
    modules, bare = set(), {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "tempfile":
                    modules.add(a.asname or "tempfile")
        elif isinstance(node, ast.ImportFrom) and node.module == "tempfile":
            for a in node.names:
                if a.name in RAW_TEMP_CALLS:
                    bare[a.asname or a.name] = a.name
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (isinstance(f, ast.Attribute) and f.attr in RAW_TEMP_CALLS and isinstance(f.value, ast.Name)
                and f.value.id in modules):
            hits.append((filename, node.lineno, f.attr))
        elif isinstance(f, ast.Name) and f.id in bare:
            hits.append((filename, node.lineno, bare[f.id]))
    return hits


def x01_no_raw_temp_folders_any_alias():
    print("CP1-X01: no suite makes a temp folder with TemporaryDirectory, NamedTemporaryFile or an aliased tempfile")
    hits = []
    for f in sorted(ROOT.glob("tests/test_*.py")):
        if f.name not in X01_EXEMPT:
            hits.extend(find_raw_temp_calls(f.read_text(encoding="utf-8"), f.name))
    check("[CP1-X01] no tests/test_*.py suite calls tempfile.mkdtemp/mkstemp/TemporaryDirectory/NamedTemporaryFile "
          "under any name", not hits, hits[:10])
    planted = {
        "import tempfile\nwith tempfile.TemporaryDirectory() as d:\n    pass\n": "TemporaryDirectory",
        "import tempfile as cp2_tempfile\nd = cp2_tempfile.TemporaryDirectory()\n": "TemporaryDirectory",
        "import tempfile as t\nf = t.NamedTemporaryFile(delete=False)\n": "NamedTemporaryFile",
        "from tempfile import mkdtemp as m\nd = m()\n": "mkdtemp",
        "def f():\n    import tempfile as tf\n    return tf.mkstemp()\n": "mkstemp",
    }
    missed = [src for src, name in planted.items() if [h[2] for h in find_raw_temp_calls(src)] != [name]]
    check("[CP1-X01] teeth: a planted TemporaryDirectory, NamedTemporaryFile or aliased mkdtemp/mkstemp call is found",
          not missed, missed)
    clean = ("import tempfile\nimport _tmp\nwith _tmp.mkdtemp() as d:\n    pass\n"
             "x = tempfile.gettempdir()\n")
    check("[CP1-X01] teeth: the helper's own mkdtemp and tempfile.gettempdir() are not flagged",
          find_raw_temp_calls(clean) == [], find_raw_temp_calls(clean))


def x02_child_script_folders_go_with_the_suite():
    print("CP1-X02: a temp folder a suite's child script makes and leaves behind goes with the suite, even on a crash")
    body = (
        "import subprocess\n"
        "print('SESSION:' + str.__str__(_tmp.SESSION))\n"
        "code = (\"import tempfile; d = tempfile.mkdtemp(prefix='ratchet-cp1-x02-'); \"\n"
        "        \"open(d + '/f.txt', 'w').write('x'); print(d)\")\n"
        "r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=30)\n"
        "print('GRANDCHILD:' + r.stdout.strip())\n"
        "raise RuntimeError('CP1-X02 simulated crash')\n"
    )
    r, private = run_child("x02", body)
    try:
        lines = dict(ln.split(":", 1) for ln in r.stdout.splitlines() if ln.startswith(("SESSION:", "GRANDCHILD:")))
        session, grandchild = lines.get("SESSION", ""), lines.get("GRANDCHILD", "")
        inside = bool(session and grandchild) and os.path.realpath(grandchild).startswith(
            os.path.realpath(session) + os.sep) and os.path.realpath(os.path.dirname(session)) == os.path.realpath(private)
        check("[CP1-X02] a child script (like test_docs.py's engine probes) makes its folder inside the suite's helper "
              "folder, then the suite crashes", r.returncode != 0 and "CP1-X02 simulated crash" in r.stderr and inside,
              (r.returncode, r.stdout, r.stderr[-300:]))
        check("[CP1-X02] the private TMPDIR is empty after the crash, child script's folder included",
              inside and listing(private) == [], listing(private))
    finally:
        cleanup_private(private)


X03_STDLIB_FALLBACK = {"__future__", "atexit", "os", "shutil", "signal", "stat", "subprocess", "sys", "tempfile",
                       "threading", "pathlib"}


def x03_helper_is_stdlib_only():
    print("CP1-X03: tests/_tmp.py imports only the standard library (tests/test_docs.py, held to the stdlib, loads it)")
    names = set(getattr(sys, "stdlib_module_names", ())) or X03_STDLIB_FALLBACK
    mods = []
    for n in ast.walk(ast.parse(HELPER_PATH.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Import):
            mods += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            mods.append(n.module or "")
    bad = [m for m in mods if m.split(".")[0] not in names]
    check("[CP1-X03] tests/_tmp.py imports only the standard library", mods and not bad, bad or mods)


TESTS.append(("CP1-X01", x01_no_raw_temp_folders_any_alias))
TESTS.append(("CP1-X02", x02_child_script_folders_go_with_the_suite))
TESTS.append(("CP1-X03", x03_helper_is_stdlib_only))


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
