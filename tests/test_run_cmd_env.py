#!/usr/bin/env python3
"""
CP1 tests: `_run_cmd`'s child env never leaks the current run's own context vars (RATCHET_ROOT above all) into
the test/regression/check subprocess it launches, while vars a child legitimately needs still reach it.
   python3 tests/test_run_cmd_env.py

Calls `_run_cmd` directly (the same direct in-process call style skills/.../ratchet.py:8038 and
tests/test_digest_docstring.py's `import ratchet as rt` use) with a trivial helper script that dumps its own
os.environ to a file, so no real suite (test_install.py, a full tests/run_all.py pass) is ever shelled out to
by this file -- running those against this repo's own worktree with RATCHET_ROOT ambient is exactly the leak
this checkpoint fixes, and doing so here would still risk writing to this worktree's real .claude/settings.json
on unpatched code.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP1-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import sys
import traceback
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "skills" / "ratchet-orchestrator" / "scripts"))
import _tmp  # noqa: E402

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP1-[TX]\d+[a-z]?)\]")


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


# ------------------------------------------------------------------------------------------------ fixtures / helpers

def _dump_env_via_run_cmd(extra_env: dict) -> dict:
    """Runs a trivial helper script through `_run_cmd` with `extra_env` added to this process's own env for the
    call, and returns the dict the child subprocess actually saw (read back from a file the child wrote
    itself) -- never the real test suites, never this worktree's real .claude/settings.json."""
    import ratchet as rt  # noqa: E402  (imported late so a missing module never blocks collection)

    d = Path(_tmp.mkdtemp(prefix="ratchet-run-cmd-env-"))
    out = d / "child-env.json"
    dumper = d / "dump_env.py"
    dumper.write_text("import json, os\njson.dump(dict(os.environ), open(os.environ['CHILD_ENV_OUT'], 'w'))\n")
    cmd = f"{sys.executable} {dumper}"
    ctx = types.SimpleNamespace(root=d, config=lambda: {})
    old = {k: os.environ.get(k) for k in list(extra_env) + ["CHILD_ENV_OUT"]}
    os.environ.update(extra_env)
    os.environ["CHILD_ENV_OUT"] = str(out)
    try:
        code, _out, _dur = rt._run_cmd(ctx, cmd, d / "run_cmd.log", 30)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    log = d / "run_cmd.log"
    assert code == 0, f"child command failed (exit {code}): {log.read_text() if log.exists() else ''}"
    return json.loads(out.read_text())


# ------------------------------------------------------------------------------------------------ CP1-T01

def t01_leak_vars_do_not_reach_the_child():
    probe = {
        "RATCHET_ROOT": "/tmp/should-never-be-seen-by-a-child",
        "RATCHET_ROLE": "director",
        "RATCHET_HOME": "/tmp/should-never-be-seen-by-a-child/home",
        "RATCHET_HUMAN_OVERRIDE": "1",
        "RATCHET_DIRECTOR_KEY_FILE": "/tmp/should-never-be-seen-by-a-child/director.key",
    }
    seen = _dump_env_via_run_cmd(probe)
    for k in probe:
        check(f"[CP1-T01] child env does not contain {k}", k not in seen, seen.get(k))


# ------------------------------------------------------------------------------------------------ CP1-T02

def t02_needed_vars_still_reach_the_child():
    probe = {
        "RATCHET_RUN_ALL": "1",
        "RATCHET_RUN_ALL_SUITES": "test_a.py,test_b.py",
        "RATCHET_RUN_ALL_TAG": "abc123",
    }
    seen = _dump_env_via_run_cmd(probe)
    for k, v in probe.items():
        check(f"[CP1-T02] child env keeps {k}", seen.get(k) == v, seen.get(k))
    for k in ("PATH", "HOME"):
        check(f"[CP1-T02] child env keeps {k} from the parent", seen.get(k) == os.environ.get(k), seen.get(k))
    check("[CP1-T02] child env sets CI=1 (as _run_cmd always does)", seen.get("CI") == "1", seen.get("CI"))
    check("[CP1-T02] child env sets FORCE_COLOR=0 (as _run_cmd always does)",
          seen.get("FORCE_COLOR") == "0", seen.get("FORCE_COLOR"))


TESTS = [
    ("CP1-T01", t01_leak_vars_do_not_reach_the_child),
    ("CP1-T02", t02_needed_vars_still_reach_the_child),
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
        except Exception as e:  # a crash still counts as a result for this case, never a silent drop
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
