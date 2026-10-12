#!/usr/bin/env python3
"""
CP3 of the "redo the guard hook's checks under the realistic-use bar" run: python one-liners that only read
pass.   python3 tests/test_guard_python_reads.py

An agent can inspect a run file with a short python3 -c one-liner, a python3 heredoc, or `python3 -m
json.tool` piped or given a single file argument, as long as the code only reads; the same forms written
to a file, renamed, deleted or copied over are still refused. This file reuses tests/trust_helpers.py (the
same throwaway-project/`rx`/`make_env` helpers tests/test_trust_cp4.py and tests/test_guard_reads.py already
use) and drives the guard through its public entry point only: `ratchet.py hook guard --host <host>` on
stdin, never decide_guard or _guard_shell directly, so a fix elsewhere in ratchet.py doesn't break this
file's imports.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP3-T01",
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
import trust_helpers as tt  # noqa: E402

ENGINE = tt.ENGINE
rx = tt.rx
git = tt.git
make_env = tt.make_env

PASS = FAIL = 0
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
ID_RE = re.compile(r"\[(CP3-[TX]\d+)\]")


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


# ---- guard driver (same shape as tests/test_guard_reads.py) ---------------------------------------------------

def guard_bash(d, env, command, host="claude"):
    """Fire the PreToolUse guard the way that host's CLI would, for a plain shell command."""
    payload = {"session_id": "s", "cwd": str(d), "hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": command}}
    return rx(d, env, "hook", "guard", "--host", host, stdin=json.dumps(payload))


OWNS_MSG_RE = r"owned by ratchet\.py|ratchet's run state|RATCHET GUARD"


# ---- fixtures ---------------------------------------------------------------------------------------------------

def x_project(env):
    """A throwaway git project with an active Ratchet run at .ratchet/runs/x (`ratchet.py init --slug x`), its
    state.json set to phase "building" directly on disk (not through the guard), plus a human_log.jsonl with
    one existing line and an evidence file under runs/x/evidence/CP1/behavior.json - the three protected-file
    targets CP3's cases read from and try to write over."""
    import _tmp
    d = Path(_tmp.mkdtemp(prefix="ratchet-guard-pyreads-"))
    git(d, "init", "-q")
    git(d, "config", "user.email", "t@t")
    git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    r = rx(d, env, "init", "feature", "--slug", "x")
    assert r.returncode == 0, r.stderr + r.stdout

    run = d / ".ratchet" / "runs" / "x"
    state = json.loads((run / "state.json").read_text())
    state["phase"] = "building"
    (run / "state.json").write_text(json.dumps(state))

    (d / ".ratchet" / "human_log.jsonl").write_text(
        json.dumps({"event": "approve-plan", "session": "s1", "at": "2026-10-11T00:00:00+00:00"}) + "\n")

    evidence_dir = run / "evidence" / "CP1"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "behavior.json").write_text(json.dumps({"status": "PASS"}))
    return d


def state_phase(d):
    return json.loads((d / ".ratchet" / "runs" / "x" / "state.json").read_text())["phase"]


def state_text(d):
    return (d / ".ratchet" / "runs" / "x" / "state.json").read_text()


def human_log_text(d):
    return (d / ".ratchet" / "human_log.jsonl").read_text()


def evidence_text(d):
    return (d / ".ratchet" / "runs" / "x" / "evidence" / "CP1" / "behavior.json").read_text()


# ---- CP3-T01 -------------------------------------------------------------------------------------------------

def t01_cd_then_python_dash_c_read_allowed():
    env = make_env("claude")
    d = x_project(env)
    cmd = "cd .ratchet/runs/x && python3 -c \"import json; d=json.load(open('state.json')); print(d['phase'])\""
    r = guard_bash(d, env, cmd)
    check("[CP3-T01] cd into the run folder then a python3 -c one-liner that only reads state.json is allowed "
          "(exit 0)", r.returncode == 0, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T01] no RATCHET GUARD denial in the output", "RATCHET GUARD" not in (r.stderr + r.stdout),
          r.stderr + r.stdout)


# ---- CP3-T02 -------------------------------------------------------------------------------------------------

def t02_cd_then_python_heredoc_read_allowed():
    env = make_env("claude")
    d = x_project(env)
    cmd = ("cd .ratchet/runs/x && python3 <<'PY'\n"
           "import json\n"
           "print(json.load(open('state.json'))['phase'])\n"
           "PY")
    r = guard_bash(d, env, cmd)
    check("[CP3-T02] the same read, as a python3 heredoc after cd, is allowed (exit 0)", r.returncode == 0,
          (r.returncode, r.stderr, r.stdout))
    check("[CP3-T02] no RATCHET GUARD denial in the output", "RATCHET GUARD" not in (r.stderr + r.stdout),
          r.stderr + r.stdout)


# ---- CP3-T03 -------------------------------------------------------------------------------------------------

def t03_pipe_into_json_tool_allowed():
    env = make_env("claude")
    d = x_project(env)
    r = guard_bash(d, env, "cd .ratchet/runs/x && cat state.json | python3 -m json.tool")
    check("[CP3-T03] piping a run file into python3 -m json.tool is allowed (exit 0)", r.returncode == 0,
          (r.returncode, r.stderr, r.stdout))
    check("[CP3-T03] no RATCHET GUARD denial in the output", "RATCHET GUARD" not in (r.stderr + r.stdout),
          r.stderr + r.stdout)


# ---- CP3-T04 -------------------------------------------------------------------------------------------------

def t04_json_tool_single_argument_no_cd_allowed():
    env = make_env("claude")
    d = x_project(env)
    r = guard_bash(d, env, "python3 -m json.tool .ratchet/runs/x/state.json")
    check("[CP3-T04] python3 -m json.tool given the run file as its only argument, with no cd and no output-"
          "file argument, is allowed (exit 0): it is read-only", r.returncode == 0,
          (r.returncode, r.stderr, r.stdout))
    check("[CP3-T04] no RATCHET GUARD denial in the output", "RATCHET GUARD" not in (r.stderr + r.stdout),
          r.stderr + r.stdout)


# ---- CP3-T05 -------------------------------------------------------------------------------------------------

def t05_python_dash_c_direct_path_no_cd_allowed():
    env = make_env("claude")
    d = x_project(env)
    cmd = "python3 -c \"import json; print(json.load(open('.ratchet/runs/x/state.json'))['phase'])\""
    r = guard_bash(d, env, cmd)
    check("[CP3-T05] a python3 -c one-liner naming the run path directly, with no cd, is allowed (exit 0): "
          "the exact over-blocked live form, now passing without a cd", r.returncode == 0,
          (r.returncode, r.stderr, r.stdout))
    check("[CP3-T05] no RATCHET GUARD denial in the output", "RATCHET GUARD" not in (r.stderr + r.stdout),
          r.stderr + r.stdout)


# ---- CP3-T06 -------------------------------------------------------------------------------------------------

def t06_append_human_log_from_python_still_refused():
    env = make_env("claude")
    d = x_project(env)
    before = human_log_text(d)
    cmd = "python3 -c \"open('.ratchet/human_log.jsonl', 'a').write('{}\\n')\""
    r = guard_bash(d, env, cmd)
    check("[CP3-T06] appending to the human log from python is still refused (exit 2)", r.returncode == 2,
          (r.returncode, r.stderr, r.stdout))
    check("[CP3-T06] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-T06] human_log.jsonl is unchanged", human_log_text(d) == before,
          (before, human_log_text(d)))


# ---- CP3-T07 -------------------------------------------------------------------------------------------------

def t07_write_text_heredoc_still_refused():
    env = make_env("claude")
    d = x_project(env)
    before = state_text(d)
    cmd = ("python3 <<'PY'\n"
           "from pathlib import Path\n"
           "Path('.ratchet/runs/x/state.json').write_text('{}')\n"
           "PY")
    r = guard_bash(d, env, cmd)
    check("[CP3-T07] rewriting state.json with write_text from a python heredoc is still refused (exit 2)",
          r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T07] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-T07] state.json is unchanged", state_text(d) == before, (before, state_text(d)))


# ---- CP3-T08 -------------------------------------------------------------------------------------------------

def t08_bare_write_call_on_r_plus_mode_still_refused():
    env = make_env("claude")
    d = x_project(env)
    before = state_text(d)
    cmd = "python3 -c \"f=open('.ratchet/runs/x/state.json', 'r+'); f.write('{}')\""
    r = guard_bash(d, env, cmd)
    check("[CP3-T08] a bare .write( call on a file opened with mode 'r+' is still refused (exit 2) on the "
          ".write( marker alone, even though the open() mode itself is not w/a/x", r.returncode == 2,
          (r.returncode, r.stderr, r.stdout))
    check("[CP3-T08] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-T08] state.json is unchanged", state_text(d) == before, (before, state_text(d)))


# ---- CP3-T09 -------------------------------------------------------------------------------------------------

def t09_json_dump_forges_evidence_still_refused():
    env = make_env("claude")
    d = x_project(env)
    before = evidence_text(d)
    cmd = ("python3 -c \"import json; json.dump({'status': 'PASS'}, "
           "open('.ratchet/runs/x/evidence/CP1/behavior.json', 'w'))\"")
    r = guard_bash(d, env, cmd)
    check("[CP3-T09] forging gate evidence with json.dump is still refused (exit 2)", r.returncode == 2,
          (r.returncode, r.stderr, r.stdout))
    check("[CP3-T09] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-T09] the evidence file is unchanged", evidence_text(d) == before, (before, evidence_text(d)))


# ---- CP3-T10 -------------------------------------------------------------------------------------------------

def t10_os_remove_still_refused():
    env = make_env("claude")
    d = x_project(env)
    state_path = d / ".ratchet" / "runs" / "x" / "state.json"
    cmd = "python3 -c \"import os; os.remove('.ratchet/runs/x/state.json')\""
    r = guard_bash(d, env, cmd)
    check("[CP3-T10] deleting state.json with os.remove is still refused (exit 2)", r.returncode == 2,
          (r.returncode, r.stderr, r.stdout))
    check("[CP3-T10] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-T10] state.json still exists", state_path.exists(), str(state_path))


# ---- CP3-T11 -------------------------------------------------------------------------------------------------

def t11_shutil_copy_overwrite_still_refused():
    env = make_env("claude")
    d = x_project(env)
    before = state_text(d)
    cmd = "python3 -c \"import shutil; shutil.copy('/tmp/fake_state.json', '.ratchet/runs/x/state.json')\""
    r = guard_bash(d, env, cmd)
    check("[CP3-T11] overwriting state.json with shutil.copy is still refused (exit 2)", r.returncode == 2,
          (r.returncode, r.stderr, r.stdout))
    check("[CP3-T11] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-T11] state.json is unchanged", state_text(d) == before, (before, state_text(d)))


# ---- CP3-T12 -------------------------------------------------------------------------------------------------

def t12_subprocess_rm_evidence_still_refused():
    env = make_env("claude")
    d = x_project(env)
    evidence_path = d / ".ratchet" / "runs" / "x" / "evidence" / "CP1" / "behavior.json"
    cmd = "python3 -c \"import subprocess; subprocess.run(['rm', '.ratchet/runs/x/evidence/CP1/behavior.json'])\""
    r = guard_bash(d, env, cmd)
    check("[CP3-T12] deleting evidence through subprocess is still refused (exit 2)", r.returncode == 2,
          (r.returncode, r.stderr, r.stdout))
    check("[CP3-T12] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-T12] the evidence file still exists", evidence_path.exists(), str(evidence_path))


# ---- CP3-T13 -------------------------------------------------------------------------------------------------

def t13_os_system_rm_human_log_still_refused():
    env = make_env("claude")
    d = x_project(env)
    log_path = d / ".ratchet" / "human_log.jsonl"
    cmd = "python3 -c \"import os; os.system('rm .ratchet/human_log.jsonl')\""
    r = guard_bash(d, env, cmd)
    check("[CP3-T13] deleting the human log through os.system is still refused (exit 2)", r.returncode == 2,
          (r.returncode, r.stderr, r.stdout))
    check("[CP3-T13] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-T13] human_log.jsonl still exists", log_path.exists(), str(log_path))


# ---- CP3-T14 -------------------------------------------------------------------------------------------------

def t14_json_tool_rewrite_in_place_still_refused():
    env = make_env("claude")
    d = x_project(env)
    before = state_text(d)
    r = guard_bash(d, env, "cd .ratchet/runs/x && python3 -m json.tool state.json state.json")
    check("[CP3-T14] python3 -m json.tool used to rewrite a run file in place (an output-file argument naming "
          "the same protected run file) is still refused (exit 2)", r.returncode == 2,
          (r.returncode, r.stderr, r.stdout))
    check("[CP3-T14] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-T14] state.json is unchanged", state_text(d) == before, (before, state_text(d)))


# ---- CP3-X01 -------------------------------------------------------------------------------------------------

def x01_full_path_python_dash_heredoc_read_allowed():
    env = make_env("claude")
    d = x_project(env)
    cmd = ("python3 - <<'EOF'\n"
           "import json\n"
           "print(json.load(open('.ratchet/runs/x/state.json')))\n"
           "EOF")
    r = guard_bash(d, env, cmd)
    check("[CP3-X01] a `python3 -` heredoc that reads a run file by its full path, with no cd, is allowed "
          "(exit 0)", r.returncode == 0, (r.returncode, r.stderr, r.stdout))
    check("[CP3-X01] no RATCHET GUARD denial in the output", "RATCHET GUARD" not in (r.stderr + r.stdout),
          r.stderr + r.stdout)


# ---- CP3-X02 -------------------------------------------------------------------------------------------------

def x02_full_path_bare_python_heredoc_read_allowed():
    env = make_env("claude")
    d = x_project(env)
    cmd = ("python3 <<'EOF'\n"
           "import json\n"
           "print(json.load(open('.ratchet/runs/x/state.json')))\n"
           "EOF")
    r = guard_bash(d, env, cmd)
    check("[CP3-X02] the same read as a bare `python3` heredoc (no dash), with no cd, is allowed (exit 0)",
          r.returncode == 0, (r.returncode, r.stderr, r.stdout))
    check("[CP3-X02] no RATCHET GUARD denial in the output", "RATCHET GUARD" not in (r.stderr + r.stdout),
          r.stderr + r.stdout)


# ---- CP3-X03 -------------------------------------------------------------------------------------------------

def x03_full_path_heredoc_read_then_redirect_still_refused():
    env = make_env("claude")
    d = x_project(env)
    before = state_text(d)
    cmd = ("python3 - <<'EOF'\n"
           "import json\n"
           "print(json.load(open('.ratchet/runs/x/state.json')))\n"
           "EOF\n"
           " > .ratchet/runs/x/state.json")
    r = guard_bash(d, env, cmd)
    check("[CP3-X03] the same read heredoc followed by a redirect into state.json is still refused (exit 2)",
          r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP3-X03] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-X03] state.json is unchanged", state_text(d) == before, (before, state_text(d)))


# ---- CP3-X04 -------------------------------------------------------------------------------------------------

def x04_full_path_heredoc_read_then_rm_still_refused():
    env = make_env("claude")
    d = x_project(env)
    state_path = d / ".ratchet" / "runs" / "x" / "state.json"
    cmd = ("python3 - <<'EOF'\n"
           "import json\n"
           "print(json.load(open('.ratchet/runs/x/state.json')))\n"
           "EOF\n"
           "rm .ratchet/runs/x/state.json")
    r = guard_bash(d, env, cmd)
    check("[CP3-X04] the same read heredoc followed by a newline and `rm state.json` is still refused (exit 2)",
          r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP3-X04] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-X04] state.json still exists", state_path.exists(), str(state_path))


# ---- CP3-X05 -------------------------------------------------------------------------------------------------

def x05_full_path_heredoc_write_open_still_refused():
    env = make_env("claude")
    d = x_project(env)
    before = state_text(d)
    cmd = ("python3 - <<'EOF'\n"
           "open('.ratchet/runs/x/state.json', 'w').write('{}')\n"
           "EOF")
    r = guard_bash(d, env, cmd)
    check("[CP3-X05] a heredoc whose body does open('.ratchet/runs/x/state.json', 'w').write('{}') is still "
          "refused (exit 2)", r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP3-X05] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-X05] state.json is unchanged", state_text(d) == before, (before, state_text(d)))


TESTS = [
    ("CP3-T01", t01_cd_then_python_dash_c_read_allowed),
    ("CP3-T02", t02_cd_then_python_heredoc_read_allowed),
    ("CP3-T03", t03_pipe_into_json_tool_allowed),
    ("CP3-T04", t04_json_tool_single_argument_no_cd_allowed),
    ("CP3-T05", t05_python_dash_c_direct_path_no_cd_allowed),
    ("CP3-T06", t06_append_human_log_from_python_still_refused),
    ("CP3-T07", t07_write_text_heredoc_still_refused),
    ("CP3-T08", t08_bare_write_call_on_r_plus_mode_still_refused),
    ("CP3-T09", t09_json_dump_forges_evidence_still_refused),
    ("CP3-T10", t10_os_remove_still_refused),
    ("CP3-T11", t11_shutil_copy_overwrite_still_refused),
    ("CP3-T12", t12_subprocess_rm_evidence_still_refused),
    ("CP3-T13", t13_os_system_rm_human_log_still_refused),
    ("CP3-T14", t14_json_tool_rewrite_in_place_still_refused),
    ("CP3-X01", x01_full_path_python_dash_heredoc_read_allowed),
    ("CP3-X02", x02_full_path_bare_python_heredoc_read_allowed),
    ("CP3-X03", x03_full_path_heredoc_read_then_redirect_still_refused),
    ("CP3-X04", x04_full_path_heredoc_read_then_rm_still_refused),
    ("CP3-X05", x05_full_path_heredoc_write_open_still_refused),
]


# ---- CP3-T15 (round-01 fix: pathlib.Path(...).open(mode) puts the mode first, not second) -----------------

def t15_path_open_write_mode_then_print_file_still_refused():
    env = make_env("claude")
    d = x_project(env)
    before = state_text(d)
    cmd = ("python3 -c \"import pathlib; f=pathlib.Path('.ratchet/runs/x/state.json').open('w'); "
           "print('{}', file=f); f.close()\"")
    r = guard_bash(d, env, cmd)
    check("[CP3-T15] a one-liner that does pathlib.Path(...).open('w') (mode first, not open()'s second "
          "argument) then print(..., file=f) is still refused (exit 2)", r.returncode == 2,
          (r.returncode, r.stderr, r.stdout))
    check("[CP3-T15] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-T15] state.json is unchanged", state_text(d) == before, (before, state_text(d)))


# ---- CP3-T16 -------------------------------------------------------------------------------------------------

def t16_path_open_write_mode_then_print_file_heredoc_still_refused():
    env = make_env("claude")
    d = x_project(env)
    before = state_text(d)
    cmd = ("python3 <<'PY'\n"
           "import pathlib\n"
           "f = pathlib.Path('.ratchet/runs/x/state.json').open('w')\n"
           "print('{}', file=f)\n"
           "f.close()\n"
           "PY")
    r = guard_bash(d, env, cmd)
    check("[CP3-T16] the same pathlib.Path(...).open('w') + print(..., file=f) as a heredoc body is still "
          "refused (exit 2)", r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T16] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-T16] state.json is unchanged", state_text(d) == before, (before, state_text(d)))


# ---- CP3-T17 -------------------------------------------------------------------------------------------------

def t17_path_open_append_mode_then_csv_writerow_still_refused():
    env = make_env("claude")
    d = x_project(env)
    before = human_log_text(d)
    cmd = ("python3 -c \"import csv, pathlib; f=pathlib.Path('.ratchet/human_log.jsonl').open('a'); "
           "csv.writer(f).writerow(['x']); f.close()\"")
    r = guard_bash(d, env, cmd)
    check("[CP3-T17] pathlib.Path(...).open('a') followed by csv.writer(f).writerow(...) (no .write( call "
          "at all) is still refused (exit 2)", r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T17] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-T17] human_log.jsonl is unchanged", human_log_text(d) == before,
          (before, human_log_text(d)))


# ---- CP3-T18 -------------------------------------------------------------------------------------------------

def t18_open_with_variable_mode_still_refused():
    env = make_env("claude")
    d = x_project(env)
    before = state_text(d)
    cmd = "python3 -c \"p='.ratchet/runs/x/state.json'; m='w'; f=open(p, m)\""
    r = guard_bash(d, env, cmd)
    check("[CP3-T18] open(p, m) with m a variable (not a quoted literal, so this fast path can't prove it "
          "isn't a write mode) is still refused (exit 2)", r.returncode == 2,
          (r.returncode, r.stderr, r.stdout))
    check("[CP3-T18] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-T18] state.json is unchanged", state_text(d) == before, (before, state_text(d)))


# ---- CP3-T19 -------------------------------------------------------------------------------------------------

def t19_pathlib_path_open_default_read_allowed():
    env = make_env("claude")
    d = x_project(env)
    cmd = ("python3 -c \"import json, pathlib; "
           "print(json.load(pathlib.Path('.ratchet/runs/x/state.json').open()))\"")
    r = guard_bash(d, env, cmd)
    check("[CP3-T19] pathlib.Path(...).open() with no mode argument at all, read through json.load, is "
          "allowed (exit 0)", r.returncode == 0, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T19] no RATCHET GUARD denial in the output", "RATCHET GUARD" not in (r.stderr + r.stdout),
          r.stderr + r.stdout)


# ---- CP3-T20 -------------------------------------------------------------------------------------------------

def t20_pathlib_path_open_explicit_read_mode_allowed():
    env = make_env("claude")
    d = x_project(env)
    cmd = ("python3 -c \"import pathlib; "
           "print(pathlib.Path('.ratchet/runs/x/state.json').open('r').read())\"")
    r = guard_bash(d, env, cmd)
    check("[CP3-T20] pathlib.Path(...).open('r') (explicit read mode, first and only argument) is allowed "
          "(exit 0)", r.returncode == 0, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T20] no RATCHET GUARD denial in the output", "RATCHET GUARD" not in (r.stderr + r.stdout),
          r.stderr + r.stdout)


# ---- CP3-T21 -------------------------------------------------------------------------------------------------

def t21_plain_print_without_file_kwarg_allowed():
    env = make_env("claude")
    d = x_project(env)
    cmd = "python3 -c \"import json; d=json.load(open('.ratchet/runs/x/state.json')); print(d)\""
    r = guard_bash(d, env, cmd)
    check("[CP3-T21] a plain print(x) call with no file= keyword is allowed (exit 0): print( alone is not "
          "a write marker", r.returncode == 0, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T21] no RATCHET GUARD denial in the output", "RATCHET GUARD" not in (r.stderr + r.stdout),
          r.stderr + r.stdout)


TESTS.extend([
    ("CP3-T15", t15_path_open_write_mode_then_print_file_still_refused),
    ("CP3-T16", t16_path_open_write_mode_then_print_file_heredoc_still_refused),
    ("CP3-T17", t17_path_open_append_mode_then_csv_writerow_still_refused),
    ("CP3-T18", t18_open_with_variable_mode_still_refused),
    ("CP3-T19", t19_pathlib_path_open_default_read_allowed),
    ("CP3-T20", t20_pathlib_path_open_explicit_read_mode_allowed),
    ("CP3-T21", t21_plain_print_without_file_kwarg_allowed),
])


# ---- CP3-T22 (round-02 adversarial: _mask_quoted_seps did not mask a literal newline inside a quoted span,
# so a multi-line python3 -c read was shredded by _segments/the fast path and refused) ---------------------

def t22_multiline_python_dash_c_after_cd_allowed():
    env = make_env("claude")
    d = x_project(env)
    cmd = ("cd .ratchet/runs/x && python3 -c '\n"
           "import json\n"
           "d = json.load(open(\"state.json\"))\n"
           "print(d[\"phase\"])\n"
           "'")
    r = guard_bash(d, env, cmd)
    check("[CP3-T22] cd into the run folder then a python3 -c one-liner whose own code is spread over "
          "several lines inside its single quotes is allowed (exit 0)", r.returncode == 0,
          (r.returncode, r.stderr, r.stdout))
    check("[CP3-T22] no RATCHET GUARD denial in the output", "RATCHET GUARD" not in (r.stderr + r.stdout),
          r.stderr + r.stdout)


# ---- CP3-T23 -------------------------------------------------------------------------------------------------

def t23_multiline_python_dash_c_full_path_no_cd_allowed():
    env = make_env("claude")
    d = x_project(env)
    cmd = ("python3 -c '\n"
           "import json\n"
           "d = json.load(open(\".ratchet/runs/x/state.json\"))\n"
           "print(d[\"phase\"])\n"
           "'")
    r = guard_bash(d, env, cmd)
    check("[CP3-T23] the same multi-line python3 -c read, naming the run path directly with no cd at all, "
          "is allowed (exit 0)", r.returncode == 0, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T23] no RATCHET GUARD denial in the output", "RATCHET GUARD" not in (r.stderr + r.stdout),
          r.stderr + r.stdout)


# ---- CP3-X06 -------------------------------------------------------------------------------------------------

def x06_multiline_python_dash_c_write_mode_still_refused():
    env = make_env("claude")
    d = x_project(env)
    before = state_text(d)
    cmd = ("python3 -c '\n"
           "import json\n"
           "f = open(\".ratchet/runs/x/state.json\", \"w\")\n"
           "f.write(json.dumps({}))\n"
           "'")
    r = guard_bash(d, env, cmd)
    check("[CP3-X06] the same multi-line python3 -c shape, but whose body opens the run file with mode 'w', "
          "is still refused (exit 2)", r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP3-X06] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-X06] state.json is unchanged", state_text(d) == before, (before, state_text(d)))


# ---- CP3-X07 -------------------------------------------------------------------------------------------------

def x07_multiline_quoted_note_then_unquoted_rm_still_refused():
    env = make_env("claude")
    d = x_project(env)
    before = state_text(d)
    cmd = ("echo 'first line of the note\n"
           "second line of the note'\n"
           "rm .ratchet/runs/x/state.json")
    r = guard_bash(d, env, cmd)
    check("[CP3-X07] a quoted multi-line note followed on a new, unquoted line by rm on a run file is still "
          "refused (exit 2): the quoted newline must not swallow the real statement boundary after it",
          r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP3-X07] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-X07] state.json is unchanged", state_text(d) == before, (before, state_text(d)))


# ---- CP3-X08 -------------------------------------------------------------------------------------------------

def x08_multiline_quoted_note_then_export_role_still_refused():
    env = make_env("claude")
    d = x_project(env)
    role_var = "RATCHET_" + "ROLE"  # built in pieces so a dev shell's own older guard copy never sees it whole
    cmd = ("echo 'first line of the note\n"
           "second line of the note'\n"
           f"export {role_var}=director")
    r = guard_bash(d, env, cmd)
    check("[CP3-X08] a quoted multi-line note followed on a new, unquoted line by export RATCHET_ROLE is "
          "still refused (exit 2)", r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP3-X08] the refusal names RATCHET_ROLE or the director session",
          bool(re.search(r"director session|RATCHET_ROLE", r.stderr + r.stdout)), r.stderr + r.stdout)


TESTS.extend([
    ("CP3-T22", t22_multiline_python_dash_c_after_cd_allowed),
    ("CP3-T23", t23_multiline_python_dash_c_full_path_no_cd_allowed),
    ("CP3-X06", x06_multiline_python_dash_c_write_mode_still_refused),
    ("CP3-X07", x07_multiline_quoted_note_then_unquoted_rm_still_refused),
    ("CP3-X08", x08_multiline_quoted_note_then_export_role_still_refused),
])


def write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = [{"id": cid, "status": "passed" if CASE_STATUS.get(cid, False) else "failed",
              "message": "" if CASE_STATUS.get(cid, False) else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid, _ in TESTS]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


# ---- CP3-T24 (round-03 adversarial: PY_OPEN_CALL_RE excluded parens inside open()'s own arguments, so a
# nested call like open(Path(...), 'w') hid its own mode literal from the old position-aware check) --------

def t24_open_path_ctor_write_mode_still_refused():
    env = make_env("claude")
    d = x_project(env)
    before = state_text(d)
    cmd = ("python3 -c \"from pathlib import Path; "
           "open(Path('.ratchet/runs/hk/state.json'), 'w')\"")
    r = guard_bash(d, env, cmd)
    check("[CP3-T24] open(Path('.ratchet/runs/hk/state.json'), 'w') is still refused (exit 2)",
          r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T24] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-T24] state.json is unchanged", state_text(d) == before, (before, state_text(d)))


# ---- CP3-T25 -------------------------------------------------------------------------------------------------

def t25_open_path_ctor_write_mode_close_still_refused():
    env = make_env("claude")
    d = x_project(env)
    before = state_text(d)
    cmd = ("python3 -c \"from pathlib import Path; "
           "open(Path('.ratchet/runs/hk/state.json'), 'w').close()\"")
    r = guard_bash(d, env, cmd)
    check("[CP3-T25] open(Path('.ratchet/runs/hk/state.json'), 'w').close() is still refused (exit 2)",
          r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T25] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-T25] state.json is unchanged", state_text(d) == before, (before, state_text(d)))


# ---- CP3-T26 -------------------------------------------------------------------------------------------------

def t26_open_os_path_join_append_mode_still_refused():
    env = make_env("claude")
    d = x_project(env)
    before = state_text(d)
    cmd = ("python3 -c \"import os; "
           "open(os.path.join('.ratchet','runs','hk','state.json'), \\\"a\\\")\"")
    r = guard_bash(d, env, cmd)
    check("[CP3-T26] open(os.path.join('.ratchet','runs','hk','state.json'), \"a\") is still refused "
          "(exit 2)", r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T26] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-T26] state.json is unchanged", state_text(d) == before, (before, state_text(d)))


# ---- CP3-T27 -------------------------------------------------------------------------------------------------

def t27_path_open_mode_kwarg_binary_write_still_refused():
    env = make_env("claude")
    d = x_project(env)
    before = state_text(d)
    cmd = ("python3 -c \"from pathlib import Path; "
           "Path('.ratchet/runs/hk/state.json').open(mode='wb')\"")
    r = guard_bash(d, env, cmd)
    check("[CP3-T27] Path('.ratchet/runs/hk/state.json').open(mode='wb') is still refused (exit 2)",
          r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T27] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-T27] state.json is unchanged", state_text(d) == before, (before, state_text(d)))


# ---- CP3-T28 -------------------------------------------------------------------------------------------------

def t28_walrus_assigned_open_write_plus_mode_still_refused():
    env = make_env("claude")
    d = x_project(env)
    before = state_text(d)
    cmd = ("python3 -c \"with open(p := '.ratchet/runs/hk/state.json', 'w+') as f: pass\"")
    r = guard_bash(d, env, cmd)
    check("[CP3-T28] with open(p := '.ratchet/runs/hk/state.json', 'w+') as f: pass is still refused "
          "(exit 2)", r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T28] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-T28] state.json is unchanged", state_text(d) == before, (before, state_text(d)))


# ---- CP3-T29 -------------------------------------------------------------------------------------------------

def t29_open_path_ctor_no_mode_read_allowed():
    env = make_env("claude")
    d = x_project(env)
    cmd = ("python3 -c \"from pathlib import Path; "
           "print(open(Path('.ratchet/runs/hk/state.json')).read())\"")
    r = guard_bash(d, env, cmd)
    check("[CP3-T29] open(Path('.ratchet/runs/hk/state.json')).read() is allowed (exit 0)",
          r.returncode == 0, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T29] no RATCHET GUARD denial in the output", "RATCHET GUARD" not in (r.stderr + r.stdout),
          r.stderr + r.stdout)


# ---- CP3-T30 -------------------------------------------------------------------------------------------------

def t30_open_os_path_join_read_mode_allowed():
    env = make_env("claude")
    d = x_project(env)
    cmd = ("python3 -c \"import os; "
           "print(open(os.path.join('.ratchet','runs','hk','state.json'), 'r').read())\"")
    r = guard_bash(d, env, cmd)
    check("[CP3-T30] open(os.path.join('.ratchet','runs','hk','state.json'), 'r').read() is allowed "
          "(exit 0)", r.returncode == 0, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T30] no RATCHET GUARD denial in the output", "RATCHET GUARD" not in (r.stderr + r.stdout),
          r.stderr + r.stdout)


# ---- CP3-T31 -------------------------------------------------------------------------------------------------

def t31_json_load_open_binary_read_mode_allowed():
    env = make_env("claude")
    d = x_project(env)
    cmd = "python3 -c \"import json; print(json.load(open('.ratchet/runs/hk/state.json', 'rb')))\""
    r = guard_bash(d, env, cmd)
    check("[CP3-T31] json.load(open('.ratchet/runs/hk/state.json', 'rb')) is allowed (exit 0)",
          r.returncode == 0, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T31] no RATCHET GUARD denial in the output", "RATCHET GUARD" not in (r.stderr + r.stdout),
          r.stderr + r.stdout)


TESTS.extend([
    ("CP3-T24", t24_open_path_ctor_write_mode_still_refused),
    ("CP3-T25", t25_open_path_ctor_write_mode_close_still_refused),
    ("CP3-T26", t26_open_os_path_join_append_mode_still_refused),
    ("CP3-T27", t27_path_open_mode_kwarg_binary_write_still_refused),
    ("CP3-T28", t28_walrus_assigned_open_write_plus_mode_still_refused),
    ("CP3-T29", t29_open_path_ctor_no_mode_read_allowed),
    ("CP3-T30", t30_open_os_path_join_read_mode_allowed),
    ("CP3-T31", t31_json_load_open_binary_read_mode_allowed),
])


# ---- CP3-T32 (round-04 adversarial: fileinput.input(..., inplace=True) is a realistic in-place edit that
# PY_WRITE_MARKER_RE missed) --------------------------------------------------------------------------------

def t32_fileinput_inplace_still_refused():
    env = make_env("claude")
    d = x_project(env)
    before = state_text(d)
    cmd = ("python3 -c \"import fileinput; "
           "[print(l, end='') for l in fileinput.input('.ratchet/runs/hk/state.json', inplace=True)]\"")
    r = guard_bash(d, env, cmd)
    check("[CP3-T32] fileinput.input('.ratchet/runs/hk/state.json', inplace=True) is still refused "
          "(exit 2)", r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T32] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-T32] state.json is unchanged", state_text(d) == before, (before, state_text(d)))


# ---- CP3-T33 -------------------------------------------------------------------------------------------------

def t33_os_chmod_still_refused():
    env = make_env("claude")
    d = x_project(env)
    before = state_text(d)
    cmd = "python3 -c \"import os; os.chmod('.ratchet/runs/hk/state.json', 0o777)\""
    r = guard_bash(d, env, cmd)
    check("[CP3-T33] os.chmod('.ratchet/runs/hk/state.json', 0o777) is still refused (exit 2)",
          r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T33] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-T33] state.json is unchanged", state_text(d) == before, (before, state_text(d)))


# ---- CP3-T34 -------------------------------------------------------------------------------------------------

def t34_os_symlink_still_refused():
    env = make_env("claude")
    d = x_project(env)
    before = state_text(d)
    cmd = "python3 -c \"import os; os.symlink('/tmp/x', '.ratchet/runs/hk/state.json')\""
    r = guard_bash(d, env, cmd)
    check("[CP3-T34] os.symlink('/tmp/x', '.ratchet/runs/hk/state.json') is still refused (exit 2)",
          r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T34] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-T34] state.json is unchanged", state_text(d) == before, (before, state_text(d)))


# ---- CP3-T35 -------------------------------------------------------------------------------------------------

def t35_os_open_wronly_trunc_still_refused():
    env = make_env("claude")
    d = x_project(env)
    before = state_text(d)
    cmd = "python3 -c \"import os; os.open('.ratchet/runs/hk/state.json', os.O_WRONLY|os.O_TRUNC)\""
    r = guard_bash(d, env, cmd)
    check("[CP3-T35] os.open('.ratchet/runs/hk/state.json', os.O_WRONLY|os.O_TRUNC) is still refused "
          "(exit 2)", r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T35] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-T35] state.json is unchanged", state_text(d) == before, (before, state_text(d)))


# ---- CP3-T36 -------------------------------------------------------------------------------------------------

def t36_json_load_open_still_allowed():
    env = make_env("claude")
    d = x_project(env)
    cmd = "python3 -c \"import json; print(json.load(open('.ratchet/runs/hk/state.json'))['phase'])\""
    r = guard_bash(d, env, cmd)
    check("[CP3-T36] json.load(open('.ratchet/runs/hk/state.json'))['phase'] is still allowed (exit 0)",
          r.returncode == 0, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T36] no RATCHET GUARD denial in the output", "RATCHET GUARD" not in (r.stderr + r.stdout),
          r.stderr + r.stdout)


TESTS.extend([
    ("CP3-T32", t32_fileinput_inplace_still_refused),
    ("CP3-T33", t33_os_chmod_still_refused),
    ("CP3-T34", t34_os_symlink_still_refused),
    ("CP3-T35", t35_os_open_wronly_trunc_still_refused),
    ("CP3-T36", t36_json_load_open_still_allowed),
])


# ---- CP3-T37 (round-05 adversarial: PY_WRITE_MARKER_RE matches only dotted names, so a plain or aliased
# import of os/shutil/subprocess called undotted passed the read-only fast path) ---------------------------

def t37_from_os_import_remove_still_refused():
    env = make_env("claude")
    d = x_project(env)
    state_path = d / ".ratchet" / "runs" / "x" / "state.json"
    cmd = "python3 -c \"from os import remove; remove('.ratchet/runs/x/state.json')\""
    r = guard_bash(d, env, cmd)
    check("[CP3-T37] from os import remove; remove('.ratchet/runs/x/state.json') is still refused (exit 2)",
          r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T37] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-T37] state.json still exists", state_path.exists(), str(state_path))


# ---- CP3-T38 -------------------------------------------------------------------------------------------------

def t38_from_shutil_import_rmtree_still_refused():
    env = make_env("claude")
    d = x_project(env)
    run_dir = d / ".ratchet" / "runs" / "x"
    cmd = "python3 -c \"from shutil import rmtree; rmtree('.ratchet/runs/x')\""
    r = guard_bash(d, env, cmd)
    check("[CP3-T38] from shutil import rmtree; rmtree('.ratchet/runs/x') is still refused (exit 2)",
          r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T38] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-T38] the run folder still exists", run_dir.exists(), str(run_dir))


# ---- CP3-T39 -------------------------------------------------------------------------------------------------

def t39_import_os_as_o_remove_still_refused():
    env = make_env("claude")
    d = x_project(env)
    state_path = d / ".ratchet" / "runs" / "x" / "state.json"
    cmd = "python3 -c \"import os as o; o.remove('.ratchet/runs/x/state.json')\""
    r = guard_bash(d, env, cmd)
    check("[CP3-T39] import os as o; o.remove('.ratchet/runs/x/state.json') is still refused (exit 2)",
          r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T39] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-T39] state.json still exists", state_path.exists(), str(state_path))


# ---- CP3-T40 -------------------------------------------------------------------------------------------------

def t40_from_subprocess_import_run_rm_still_refused():
    env = make_env("claude")
    d = x_project(env)
    state_path = d / ".ratchet" / "runs" / "x" / "state.json"
    cmd = "python3 -c \"from subprocess import run; run(['rm', '.ratchet/runs/x/state.json'])\""
    r = guard_bash(d, env, cmd)
    check("[CP3-T40] from subprocess import run; run(['rm', '.ratchet/runs/x/state.json']) is still refused "
          "(exit 2)", r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T40] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)
    check("[CP3-T40] state.json still exists", state_path.exists(), str(state_path))


# ---- CP3-T41 -------------------------------------------------------------------------------------------------

def t41_import_shutil_which_still_refused():
    env = make_env("claude")
    d = x_project(env)
    cmd = "cd .ratchet/runs/x && python3 -c \"import shutil; print(shutil.which('ls'))\""
    r = guard_bash(d, env, cmd)
    check("[CP3-T41] import shutil; print(shutil.which('ls')) is still refused (exit 2): accepted "
          "over-refusal, shutil imported", r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T41] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)


# ---- CP3-T42 -------------------------------------------------------------------------------------------------

def t42_import_os_json_path_join_read_still_allowed():
    env = make_env("claude")
    d = x_project(env)
    cmd = ("python3 -c \"import os, json; "
           "print(json.load(open(os.path.join('.ratchet','runs','hk','state.json'))))\"")
    r = guard_bash(d, env, cmd)
    check("[CP3-T42] import os, json; print(json.load(open(os.path.join(...)))) is still allowed (exit 0): "
          "plain import os stays a read", r.returncode == 0, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T42] no RATCHET GUARD denial in the output", "RATCHET GUARD" not in (r.stderr + r.stdout),
          r.stderr + r.stdout)


# ---- CP3-T43 -------------------------------------------------------------------------------------------------

def t43_from_pathlib_import_path_read_text_allowed():
    env = make_env("claude")
    d = x_project(env)
    cmd = ("python3 -c \"from pathlib import Path; import json; "
           "print(json.loads(Path('.ratchet/runs/x/state.json').read_text()))\"")
    r = guard_bash(d, env, cmd)
    check("[CP3-T43] from pathlib import Path; ...Path(...).read_text() is still allowed (exit 0)",
          r.returncode == 0, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T43] no RATCHET GUARD denial in the output", "RATCHET GUARD" not in (r.stderr + r.stdout),
          r.stderr + r.stdout)


# ---- CP3-T44 -------------------------------------------------------------------------------------------------

def t44_from_os_path_import_join_read_allowed():
    env = make_env("claude")
    d = x_project(env)
    cmd = ("python3 -c \"from os.path import join; "
           "print(open(join('.ratchet','runs','hk','state.json')).read())\"")
    r = guard_bash(d, env, cmd)
    check("[CP3-T44] from os.path import join; open(join(...)).read() is still allowed (exit 0)",
          r.returncode == 0, (r.returncode, r.stderr, r.stdout))
    check("[CP3-T44] no RATCHET GUARD denial in the output", "RATCHET GUARD" not in (r.stderr + r.stdout),
          r.stderr + r.stdout)


TESTS.extend([
    ("CP3-T37", t37_from_os_import_remove_still_refused),
    ("CP3-T38", t38_from_shutil_import_rmtree_still_refused),
    ("CP3-T39", t39_import_os_as_o_remove_still_refused),
    ("CP3-T40", t40_from_subprocess_import_run_rm_still_refused),
    ("CP3-T41", t41_import_shutil_which_still_refused),
    ("CP3-T42", t42_import_os_json_path_join_read_still_allowed),
    ("CP3-T43", t43_from_pathlib_import_path_read_text_allowed),
    ("CP3-T44", t44_from_os_path_import_join_read_allowed),
])


def main():
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crash still counts as a (failed) result for this case, never a silent drop
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
