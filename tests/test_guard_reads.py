#!/usr/bin/env python3
"""
CP2 of the "redo the guard hook's checks under the realistic-use bar" run: read-only commands on run files
pass.   python3 tests/test_guard_reads.py

An agent can read Ratchet's run files with ordinary shell commands (after `cd` into .ratchet/, in loops and
pipelines) and write files elsewhere that merely mention a run path, while any shell write into state,
evidence or the human log is still refused. This file reuses tests/trust_helpers.py (the same throwaway-
project/`rx`/`make_env` helpers tests/test_trust_cp4.py and tests/test_guard_reserved_names.py already use) and
drives the guard through its public entry point only: `ratchet.py hook guard --host <host>` on stdin, never
decide_guard or _guard_shell directly, so a fix elsewhere in ratchet.py doesn't break this file's imports.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP2-T01",
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
ID_RE = re.compile(r"\[(CP2-[TX]\d+)\]")


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


# ---- guard driver (same shape as tests/test_guard_reserved_names.py) ------------------------------------------

def guard_bash(d, env, command, host="claude"):
    """Fire the PreToolUse guard the way that host's CLI would, for a plain shell command."""
    if host == "codex":
        payload = {"session_id": "s", "turn_id": "t", "cwd": str(d), "hook_event_name": "PreToolUse",
                   "tool_name": "Bash", "tool_input": {"command": ["bash", "-lc", command]}}
    elif host == "gemini":
        payload = {"session_id": "s", "cwd": str(d), "hook_event_name": "BeforeTool",
                   "tool_name": "run_shell_command", "tool_input": {"command": command}}
    else:
        payload = {"session_id": "s", "cwd": str(d), "hook_event_name": "PreToolUse", "tool_name": "Bash",
                   "tool_input": {"command": command}}
    return rx(d, env, "hook", "guard", "--host", host, stdin=json.dumps(payload))


def allow_table(d, env, commands, host="claude"):
    """[(label, cmd), ...] -> list of (label, returncode, stderr+stdout) for every one NOT allowed."""
    bad = []
    for label, cmd in commands:
        r = guard_bash(d, env, cmd, host=host)
        if r.returncode != 0:
            bad.append((label, r.returncode, (r.stderr + r.stdout)[:300]))
    return bad


def refuse_table(d, env, commands, msg_re, host="claude"):
    """[(label, cmd), ...] -> (not-refused, refused-but-wrong-message)."""
    bad, bad_msg = [], []
    for label, cmd in commands:
        r = guard_bash(d, env, cmd, host=host)
        if r.returncode != 2:
            bad.append((label, r.returncode, (r.stderr + r.stdout)[:300]))
        elif not re.search(msg_re, r.stderr + r.stdout, re.I):
            bad_msg.append((label, (r.stderr + r.stdout)[:300]))
    return bad, bad_msg


OWNS_MSG_RE = r"owned by ratchet\.py|ratchet's run state|RATCHET GUARD"


# ---- fixtures ------------------------------------------------------------------------------------------------

def hk_project(env):
    """A throwaway git project with `.ratchet/` a real directory (`ratchet.py init --slug hk`; no plan-submit or
    approve-plan needed, the guard only checks that `.ratchet/` exists), plus a realistic run-folder layout on
    disk written DIRECTLY (not through the guard), so the commands under test have something real to read."""
    import _tmp
    d = Path(_tmp.mkdtemp(prefix="ratchet-guard-reads-"))
    git(d, "init", "-q")
    git(d, "config", "user.email", "t@t")
    git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    r = rx(d, env, "init", "feature", "--slug", "hk")
    assert r.returncode == 0, r.stderr + r.stdout

    ratchet = d / ".ratchet"
    (ratchet / "hook-errors.log").write_text(
        "2026-10-11T00:00:00+00:00 claude guard: KeyError: 'missing'\n"
        "2026-10-11T00:00:01+00:00 claude prompt: ValueError: bad\n"
        "2026-10-11T00:00:02+00:00 claude stop: TypeError: nope\n")
    (ratchet / "human_log.jsonl").write_text(
        json.dumps({"event": "approve-plan", "session": "s1", "at": "2026-10-11T00:00:00+00:00"}) + "\n" +
        json.dumps({"event": "human-approve", "session": "s1", "at": "2026-10-11T00:00:01+00:00"}) + "\n")

    run = ratchet / "runs" / "hk"
    # init already wrote a valid state.json; keep it (a realistic, valid run state) rather than re-inventing one.
    state = json.loads((run / "state.json").read_text())
    assert state.get("slug") == "hk", state
    (run / "stop-blocks.jsonl").write_text(
        json.dumps({"at": "2026-10-11T00:00:00+00:00", "reason": "blocked"}) + "\n")
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "checkpoints": []}))

    other = ratchet / "runs" / "other"
    other.mkdir(parents=True, exist_ok=True)
    (other / "stop-blocks.jsonl").write_text(
        json.dumps({"at": "2026-10-11T00:00:01+00:00", "reason": "blocked"}) + "\n")
    return d


CMD_LIVE_A = ('cd .ratchet && wc -l hook-errors.log && ls runs/ && find runs -name stop-blocks.jsonl | xargs '
              'wc -l; tail -c 3000 hook-errors.log')
CMD_LIVE_B = ('cat .ratchet/hook-errors.log | cut -c1-600; for f in .ratchet/runs/*/stop-blocks.jsonl; do '
              'echo "== $f"; wc -l < "$f"; done 2>/dev/null')
CMD_T10A = "echo x > .ratchet/runs/hk/state.json"
CMD_T13A = "rm .ratchet/runs/hk/stop-blocks.jsonl"
CMD_T07A = "cd .ratchet && cat runs/hk/state.json > /tmp/copy.json"


# ---- CP2-T01 ----------------------------------------------------------------------------------------------------

def t01_live_command_a_allowed_all_hosts():
    env = make_env("claude")
    d = hk_project(env)
    for host in ("claude", "codex", "gemini"):
        r = guard_bash(d, env, CMD_LIVE_A, host=host)
        check(f"[CP2-T01] {host}: the live orchestrator diagnostic command (a) is allowed (exit 0)",
              r.returncode == 0, (host, r.returncode, r.stderr, r.stdout))
        check(f"[CP2-T01] {host}: no RATCHET GUARD denial in the output",
              "RATCHET GUARD" not in (r.stderr + r.stdout), r.stderr + r.stdout)


# ---- CP2-T02 ----------------------------------------------------------------------------------------------------

def t02_live_command_b_allowed():
    env = make_env("claude")
    d = hk_project(env)
    r = guard_bash(d, env, CMD_LIVE_B)
    check("[CP2-T02] the live orchestrator diagnostic command (b) is allowed (exit 0)",
          r.returncode == 0, (r.returncode, r.stderr, r.stdout))
    check("[CP2-T02] no RATCHET GUARD denial in the output",
          "RATCHET GUARD" not in (r.stderr + r.stdout), r.stderr + r.stdout)


# ---- CP2-T03 ----------------------------------------------------------------------------------------------------

def t03_plain_read_only_filters_allowed():
    env = make_env("claude")
    d = hk_project(env)
    bad = allow_table(d, env, [
        ("cut", "cut -c1-80 .ratchet/runs/hk/state.json"),
        ("cd + sort | uniq -c", "cd .ratchet && sort runs/hk/human_log.jsonl | uniq -c"),
        ("tr via input redirect", "tr -d '\\n' < .ratchet/runs/hk/state.json"),
        ("awk no redirect", "awk '{print $1}' .ratchet/human_log.jsonl"),
        ("cd + cat | awk", "cd .ratchet && cat runs/hk/human_log.jsonl | awk -F, '{print $1}'"),
    ])
    check("[CP2-T03] every plain read-only filter form (cut, sort|uniq -c, tr via <, awk without > or |-write, "
          "cat|awk after cd) is allowed (exit 0)", bad == [], bad)


# ---- CP2-T04 ----------------------------------------------------------------------------------------------------

def t04_sed_read_only_allowed():
    env = make_env("claude")
    d = hk_project(env)
    bad = allow_table(d, env, [
        ("sed -n (no -i)", "sed -n '1,5p' .ratchet/runs/hk/state.json"),
        ("cd + sed -n (no -i)", "cd .ratchet && sed -n '2p' human_log.jsonl"),
    ])
    check("[CP2-T04] sed used only to read (no -i), directly and after cd into .ratchet/, is allowed (exit 0)",
          bad == [], bad)


# ---- CP2-T05 ----------------------------------------------------------------------------------------------------

def t05_xargs_read_only_program_allowed():
    env = make_env("claude")
    d = hk_project(env)
    bad = allow_table(d, env, [
        ("xargs cat", "find .ratchet/runs -name '*.json' | xargs cat"),
        ("xargs wc -l", "find .ratchet/runs -name '*.json' | xargs wc -l"),
    ])
    check("[CP2-T05] xargs is allowed when the program it runs (cat, wc) is itself read-only (exit 0)",
          bad == [], bad)


# ---- CP2-T06 ----------------------------------------------------------------------------------------------------

def t06_while_read_loop_input_redirect_allowed():
    env = make_env("claude")
    d = hk_project(env)
    r = guard_bash(d, env, 'while read -r f; do wc -l "$f"; done < <(ls .ratchet/runs/hk/*.json)')
    check("[CP2-T06] a while-read loop over a process-substitution input redirect is allowed (exit 0)",
          r.returncode == 0, (r.returncode, r.stderr, r.stdout))


# ---- CP2-T07 ----------------------------------------------------------------------------------------------------

def t07_cd_then_read_and_copy_to_tmp_allowed():
    env = make_env("claude")
    d = hk_project(env)
    bad = allow_table(d, env, [
        ("cd + cat redirected to /tmp", CMD_T07A),
        ("cd + cp to /tmp", "cd .ratchet/runs/hk && cp state.json /tmp/copy2.json"),
    ])
    check("[CP2-T07] after cd into .ratchet/, reading and saving a copy to /tmp is allowed (exit 0): the read "
          "side is fine and /tmp is outside .ratchet/", bad == [], bad)


# ---- CP2-T08 ----------------------------------------------------------------------------------------------------

def t08_write_outside_ratchet_mentioning_run_path_allowed():
    env = make_env("claude")
    d = hk_project(env)
    r = guard_bash(d, env, "echo 'see .ratchet/runs/hk/state.json' > /tmp/note.txt")
    check("[CP2-T08] writing to /tmp whose text only names a run path is allowed (exit 0): the redirect target "
          "is outside .ratchet/", r.returncode == 0, (r.returncode, r.stderr, r.stdout))


# ---- CP2-T09 ----------------------------------------------------------------------------------------------------

def t09_commit_message_naming_run_path_allowed():
    env = make_env("claude")
    d = hk_project(env)
    r = guard_bash(d, env, "git commit -m 'guard: block writes to .ratchet/runs/hk/state.json'")
    check("[CP2-T09] a git commit -m message that only names a run path is allowed (exit 0)",
          r.returncode == 0, (r.returncode, r.stderr, r.stdout))


# ---- CP2-T10 ----------------------------------------------------------------------------------------------------

def t10_direct_write_into_run_file_still_refused():
    env = make_env("claude")
    d = hk_project(env)
    bad, bad_msg = refuse_table(d, env, [
        ("echo > state.json", CMD_T10A),
        ("cd + echo > state.json", "cd .ratchet/runs/hk && echo x > state.json"),
    ], OWNS_MSG_RE)
    check("[CP2-T10] a direct write into a run file is still refused (exit 2), with or without cd",
          bad == [], bad)
    check("[CP2-T10] each refusal carries the Ratchet-owns-this-file message", bad_msg == [], bad_msg)


# ---- CP2-T11 ----------------------------------------------------------------------------------------------------

def t11_sed_i_still_refused():
    env = make_env("claude")
    d = hk_project(env)
    bad, bad_msg = refuse_table(d, env, [
        ("sed -i", "sed -i 's/a/b/' .ratchet/human_log.jsonl"),
        ("cd + sed -i ''", "cd .ratchet && sed -i '' -e 's/a/b/' human_log.jsonl"),
    ], OWNS_MSG_RE)
    check("[CP2-T11] sed -i on the human log is still refused (exit 2), directly and after cd", bad == [], bad)
    check("[CP2-T11] each refusal carries the Ratchet-owns-this-file message", bad_msg == [], bad_msg)


# ---- CP2-T12 ----------------------------------------------------------------------------------------------------

def t12_cp_mv_tee_touch_truncate_still_refused():
    env = make_env("claude")
    d = hk_project(env)
    bad, bad_msg = refuse_table(d, env, [
        ("cp", "cp /tmp/x.json .ratchet/runs/hk/state.json"),
        ("mv", "mv /tmp/x.json .ratchet/runs/hk/state.json"),
        ("tee", "echo x | tee .ratchet/runs/hk/state.json"),
        ("touch", "touch .ratchet/runs/hk/state.json"),
        ("truncate", "truncate -s 0 .ratchet/runs/hk/state.json"),
        ("null redirect", ": > .ratchet/runs/hk/state.json"),
    ], OWNS_MSG_RE)
    check("[CP2-T12] cp, mv, tee, touch, truncate and a null redirect into a run file are all still refused "
          "(exit 2)", bad == [], bad)
    check("[CP2-T12] each refusal carries the Ratchet-owns-this-file message", bad_msg == [], bad_msg)


# ---- CP2-T13 ----------------------------------------------------------------------------------------------------

def t13_rm_on_run_file_still_refused():
    env = make_env("claude")
    d = hk_project(env)
    bad, bad_msg = refuse_table(d, env, [
        ("rm", CMD_T13A),
        ("cd + rm", "cd .ratchet/runs/hk && rm state.json"),
    ], OWNS_MSG_RE)
    check("[CP2-T13] rm on a run file is still refused (exit 2), directly and after cd", bad == [], bad)
    check("[CP2-T13] each refusal carries the Ratchet-owns-this-file message", bad_msg == [], bad_msg)


# ---- CP2-T14 ----------------------------------------------------------------------------------------------------

def t14_append_to_human_log_still_refused():
    env = make_env("claude")
    d = hk_project(env)
    r = guard_bash(d, env, "cat extra.jsonl >> .ratchet/human_log.jsonl")
    check("[CP2-T14] appending to the human log is still refused (exit 2)", r.returncode == 2,
          (r.returncode, r.stderr, r.stdout))
    check("[CP2-T14] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)


# ---- CP2-T15 ----------------------------------------------------------------------------------------------------

def t15_jq_edit_then_replace_still_refused():
    env = make_env("claude")
    d = hk_project(env)
    r = guard_bash(d, env, "jq '.phase=\"done\"' .ratchet/runs/hk/state.json > /tmp/s.json && mv /tmp/s.json "
                           ".ratchet/runs/hk/state.json")
    check("[CP2-T15] jq redirected to /tmp then mv'd back onto a run file is refused (exit 2): the mv half's "
          "target is a protected run file", r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP2-T15] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)


# ---- CP2-T16 ----------------------------------------------------------------------------------------------------

def t16_find_delete_exec_still_refused():
    env = make_env("claude")
    d = hk_project(env)
    bad, bad_msg = refuse_table(d, env, [
        ("find -delete", "find .ratchet/runs/hk -name '*.jsonl' -delete"),
        ("find -exec rm", "find .ratchet/runs/hk -name 'state.json' -exec rm {} \\;"),
    ], OWNS_MSG_RE)
    check("[CP2-T16] find -delete and find -exec rm on run files are still refused (exit 2)", bad == [], bad)
    check("[CP2-T16] each refusal carries the Ratchet-owns-this-file message", bad_msg == [], bad_msg)


# ---- CP2-T17 ----------------------------------------------------------------------------------------------------

def t17_find_pipe_xargs_rm_closes_the_gap():
    env = make_env("claude")
    d = hk_project(env)
    r = guard_bash(d, env, "find .ratchet | xargs rm")
    check("[CP2-T17] find .ratchet | xargs rm is refused (exit 2), closing the gap named in this step's notes "
          "(this exact form was allowed before this step)", r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP2-T17] the refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I)), r.stderr + r.stdout)


# ---- CP2-T18 ----------------------------------------------------------------------------------------------------

def t18_codex_list_payload_agrees():
    env = make_env("claude")
    d = hk_project(env)
    r_allow = guard_bash(d, env, CMD_LIVE_A, host="codex")
    check("[CP2-T18] codex's Bash-command-as-list payload: the allowed form (CP2-T01's command) still passes "
          "(exit 0)", r_allow.returncode == 0, (r_allow.returncode, r_allow.stderr, r_allow.stdout))
    r_refuse = guard_bash(d, env, CMD_T10A, host="codex")
    check("[CP2-T18] codex's Bash-command-as-list payload: the refused form (CP2-T10's write) is still denied "
          "(exit 2)", r_refuse.returncode == 2, (r_refuse.returncode, r_refuse.stderr, r_refuse.stdout))


# ---- CP2-T19 ----------------------------------------------------------------------------------------------------

def t19_gemini_payload_agrees():
    env = make_env("claude")
    d = hk_project(env)
    r_allow = guard_bash(d, env, CMD_T07A, host="gemini")
    check("[CP2-T19] gemini's run_shell_command payload: the allowed form (CP2-T07's cd-then-copy) still passes "
          "(exit 0)", r_allow.returncode == 0, (r_allow.returncode, r_allow.stderr, r_allow.stdout))
    r_refuse = guard_bash(d, env, CMD_T13A, host="gemini")
    check("[CP2-T19] gemini's run_shell_command payload: the refused form (CP2-T13's rm) is still denied "
          "(exit 2)", r_refuse.returncode == 2, (r_refuse.returncode, r_refuse.stderr, r_refuse.stdout))


# ---- CP2-T20 ----------------------------------------------------------------------------------------------------

def t20_reserved_variable_refusal_not_loosened_by_read_only_allowance():
    env = make_env("claude")
    d = hk_project(env)
    r = guard_bash(d, env, "cd .ratchet && RATCHET_HOME=./evil ls")
    check("[CP2-T20] a reserved-variable assignment in front of an otherwise read-only, in-.ratchet command is "
          "still refused (exit 2)", r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP2-T20] the refusal names the reserved variable (RATCHET_HOME), not a generic read-only pass",
          bool(re.search(r"RATCHET_HOME|per-user folder", r.stderr + r.stdout, re.I)), r.stderr + r.stdout)


# ---- CP2-T21 ----------------------------------------------------------------------------------------------------

def t21_docs_say_what_the_guard_allows_and_still_refuses():
    root = ENGINE.parents[1]  # .../skills/ratchet-orchestrator
    repo_root = ENGINE.parents[3]  # the project root (this worktree)
    readme = (repo_root / "README.md").read_text()
    skill = (root / "SKILL.md").read_text()
    schemas = (root / "references" / "schemas.md").read_text()
    docs = readme + "\n" + skill + "\n" + schemas

    read_allow_stmt = re.search(
        r"read-only[^.\n]{0,200}\.ratchet[^.\n]{0,250}(allow|pass)|"
        r"(allow|pass)[^.\n]{0,250}read-only[^.\n]{0,200}\.ratchet",
        docs, re.I)
    check("[CP2-T21] README, SKILL.md or schemas.md state that read-only commands on .ratchet/ run files "
          "(including after cd, in loops and pipelines) are allowed", read_allow_stmt is not None,
          "no read-only-on-.ratchet-is-allowed sentence found in README/SKILL.md/schemas.md")

    mention_allow_stmt = re.search(
        r"(writ(e|ing)|outside)[^.\n]{0,250}(mention|name)[^.\n]{0,200}(run path|allow)|"
        r"(mention|name)[^.\n]{0,200}run path[^.\n]{0,200}allow",
        docs, re.I)
    check("[CP2-T21] README, SKILL.md or schemas.md state that writes outside .ratchet/ whose text merely "
          "mentions a run path are allowed", mention_allow_stmt is not None,
          "no mention-only-write-is-allowed sentence found in README/SKILL.md/schemas.md")

    refuse_stmt = re.search(
        r"(write|writing)[^.\n]{0,100}(state|evidence|human log|human_log)[^.\n]{0,150}(refus|block)|"
        r"(state|evidence|human log|human_log)[^.\n]{0,150}(still\s+)?(refus|block)",
        docs, re.I)
    check("[CP2-T21] README, SKILL.md or schemas.md state that any shell write into state, evidence or the "
          "human log is still refused", refuse_stmt is not None,
          "no still-refused-on-run-files sentence found in README/SKILL.md/schemas.md")


# ---- CP2-T22 ----------------------------------------------------------------------------------------------------

def t22_write_option_and_multi_target_forms_still_refused():
    """CP2 round-01 adversarial findings (write-option class): a program this guard treats as read-only is only
    read-only without its own write option/flag, and an in-place editor or chained find action can write to more
    than the single argument the earlier heuristic checked."""
    env = make_env("claude")
    d = hk_project(env)
    bad, bad_msg = refuse_table(d, env, [
        ("sort -o", "sort -o .ratchet/runs/hk/state.json x"),
        ("sort --output=", "sort --output=.ratchet/runs/hk/state.json x"),
        ("uniq in out", "uniq x .ratchet/runs/hk/state.json"),
        ("awk -i inplace", "awk -i inplace '{print}' .ratchet/runs/hk/state.json"),
        ("find chained -exec", "find .ratchet -name x -exec echo {} \\; -exec rm {} \\;"),
        ("find -fprint", "find .ratchet -fprint .ratchet/runs/hk/state.json"),
        ("sed -i multiple files", "sed -i s/a/b/ .ratchet/runs/hk/state.json /tmp/other"),
        ("cd + xargs cp (stdin destination)", "cd .ratchet/runs/hk && echo state.json | xargs cp /tmp/f"),
    ], OWNS_MSG_RE)
    check("[CP2-T22] every write-option, multi-target or stdin-destination form is still refused (exit 2)",
          bad == [], bad)
    check("[CP2-T22] each refusal carries the Ratchet-owns-this-file message", bad_msg == [], bad_msg)


# ---- CP2-T23 ----------------------------------------------------------------------------------------------------

def t23_plain_forms_of_the_same_programs_still_allowed():
    """The plain read-only form of each program touched by CP2-T22's fix is still allowed; only the write
    option/flag/stdin-destination form is refused."""
    env = make_env("claude")
    d = hk_project(env)
    bad = allow_table(d, env, [
        ("sort, no -o", "sort .ratchet/runs/hk/state.json"),
        ("uniq, one file", "uniq .ratchet/hook-errors.log"),
        ("awk, no -i", "awk '{print $1}' .ratchet/runs/hk/state.json"),
        ("find -exec cat (read-only program)", "find .ratchet -name '*.json' -exec cat {} \\;"),
        ("sed -n (no -i)", "sed -n '1,5p' .ratchet/runs/hk/state.json"),
        ("cd + xargs wc -l (read-only inner program)", "cd .ratchet && ls | xargs wc -l"),
    ])
    check("[CP2-T23] the plain read-only form of sort, uniq, awk, find -exec, sed -n and xargs over a read-only "
          "program is still allowed (exit 0)", bad == [], bad)


TESTS = [
    ("CP2-T01", t01_live_command_a_allowed_all_hosts),
    ("CP2-T02", t02_live_command_b_allowed),
    ("CP2-T03", t03_plain_read_only_filters_allowed),
    ("CP2-T04", t04_sed_read_only_allowed),
    ("CP2-T05", t05_xargs_read_only_program_allowed),
    ("CP2-T06", t06_while_read_loop_input_redirect_allowed),
    ("CP2-T07", t07_cd_then_read_and_copy_to_tmp_allowed),
    ("CP2-T08", t08_write_outside_ratchet_mentioning_run_path_allowed),
    ("CP2-T09", t09_commit_message_naming_run_path_allowed),
    ("CP2-T10", t10_direct_write_into_run_file_still_refused),
    ("CP2-T11", t11_sed_i_still_refused),
    ("CP2-T12", t12_cp_mv_tee_touch_truncate_still_refused),
    ("CP2-T13", t13_rm_on_run_file_still_refused),
    ("CP2-T14", t14_append_to_human_log_still_refused),
    ("CP2-T15", t15_jq_edit_then_replace_still_refused),
    ("CP2-T16", t16_find_delete_exec_still_refused),
    ("CP2-T17", t17_find_pipe_xargs_rm_closes_the_gap),
    ("CP2-T18", t18_codex_list_payload_agrees),
    ("CP2-T19", t19_gemini_payload_agrees),
    ("CP2-T20", t20_reserved_variable_refusal_not_loosened_by_read_only_allowance),
    ("CP2-T21", t21_docs_say_what_the_guard_allows_and_still_refuses),
    ("CP2-T22", t22_write_option_and_multi_target_forms_still_refused),
    ("CP2-T23", t23_plain_forms_of_the_same_programs_still_allowed),
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


# ---- CP2-X01..X12 (round 2 regression: forms the adversarial fix-list named by exact command) -----------------
# Appended after the red run; every existing line above is unchanged. New tests only, registered into TESTS
# below (not inside it) so they run as part of `python3 tests/test_guard_reads.py` without touching the
# original TESTS list literal.

def x01_python_dash_c_json_dump_still_refused():
    env = make_env("claude")
    d = hk_project(env)
    r = guard_bash(d, env, "python3 -c \"import json; d={}; json.dump(d, open('.ratchet/runs/hk/state.json'"
                           ",'w'))\"")
    check("[CP2-X01] a one-line python3 -c that json.dumps into state.json is refused (exit 2)",
          r.returncode == 2, (r.returncode, r.stderr, r.stdout))


def x02_node_dash_e_writefilesync_still_refused():
    env = make_env("claude")
    d = hk_project(env)
    r = guard_bash(d, env, "node -e \"const fs=require('fs'); fs.writeFileSync('.ratchet/human_log.jsonl',"
                           "'x')\"")
    check("[CP2-X02] a one-line node -e that writeFileSync's over the human log is refused (exit 2)",
          r.returncode == 2, (r.returncode, r.stderr, r.stdout))


def x03_for_loop_sed_i_on_loop_variable_still_refused():
    env = make_env("claude")
    d = hk_project(env)
    r = guard_bash(d, env, 'for f in .ratchet/runs/*/state.json; do sed -i s/a/b/ "$f"; done')
    check("[CP2-X03] a for-loop that sed -i's every file its own list names is refused (exit 2)",
          r.returncode == 2, (r.returncode, r.stderr, r.stdout))


def x04_for_loop_redirect_on_loop_variable_still_refused():
    env = make_env("claude")
    d = hk_project(env)
    r = guard_bash(d, env, 'for f in .ratchet/runs/*/state.json; do echo x > "$f"; done')
    check("[CP2-X04] a for-loop that redirects into every file its own list names is refused (exit 2)",
          r.returncode == 2, (r.returncode, r.stderr, r.stdout))


def x05_while_read_rm_on_loop_variable_still_refused():
    env = make_env("claude")
    d = hk_project(env)
    r = guard_bash(d, env, 'while read f; do rm "$f"; done < <(ls .ratchet/runs/hk/*.json)')
    check("[CP2-X05] a while-read loop that rm's every file a process substitution over .ratchet/ lists is "
          "refused (exit 2)", r.returncode == 2, (r.returncode, r.stderr, r.stdout))


def x06_cp_target_directory_flag_still_refused():
    env = make_env("claude")
    d = hk_project(env)
    r = guard_bash(d, env, "cp -t .ratchet/runs/hk/evidence /tmp/f.json")
    check("[CP2-X06] cp -t into a run folder is refused (exit 2)", r.returncode == 2,
          (r.returncode, r.stderr, r.stdout))


def x07_mv_target_directory_flag_still_refused():
    env = make_env("claude")
    d = hk_project(env)
    r = guard_bash(d, env, "mv -t .ratchet/runs/hk /tmp/state.json")
    check("[CP2-X07] mv -t into a run folder is refused (exit 2)", r.returncode == 2,
          (r.returncode, r.stderr, r.stdout))


def x08_sed_long_in_place_flag_still_refused():
    env = make_env("claude")
    d = hk_project(env)
    r = guard_bash(d, env, "sed --in-place s/a/b/ .ratchet/human_log.jsonl")
    check("[CP2-X08] sed --in-place on the human log is refused (exit 2)", r.returncode == 2,
          (r.returncode, r.stderr, r.stdout))


def x09_find_fprint_write_action_still_refused():
    env = make_env("claude")
    d = hk_project(env)
    r = guard_bash(d, env, "find /tmp -maxdepth 0 -fprint .ratchet/runs/hk/state.json")
    check("[CP2-X09] find -fprint onto a run file is refused (exit 2) however harmless its search path",
          r.returncode == 2, (r.returncode, r.stderr, r.stdout))


def x10_cp_quoted_destination_still_refused():
    env = make_env("claude")
    d = hk_project(env)
    r = guard_bash(d, env, 'cp /tmp/x ".ratchet/runs/hk/state.json"')
    check("[CP2-X10] cp into a run file is refused (exit 2) even when the destination argument is quoted",
          r.returncode == 2, (r.returncode, r.stderr, r.stdout))


def x11_cd_out_of_ratchet_then_ordinary_command_allowed():
    env = make_env("claude")
    d = hk_project(env)
    r = guard_bash(d, env, "cd .ratchet && ls; cd /tmp && npm --version")
    # Option A (user decision at CP2's review limit): no folder tracking, so this falls back to the strict check.
    check("[CP2-X11] cd into .ratchet/ then back out to an unrelated directory, then an ordinary command "
          "there, is refused (exit 2), telling the agent to run commands that change files in a separate call",
          r.returncode == 2 and "separate call" in (r.stderr + r.stdout), (r.returncode, r.stderr, r.stdout))


def x12_uniq_check_chars_option_value_allowed():
    env = make_env("claude")
    d = hk_project(env)
    r = guard_bash(d, env, "uniq -w 20 .ratchet/hook-errors.log")
    check("[CP2-X12] uniq -w with its numeric option value is allowed (exit 0): the value is not miscounted "
          "as a second file", r.returncode == 0, (r.returncode, r.stderr, r.stdout))


TESTS.extend([
    ("CP2-X01", x01_python_dash_c_json_dump_still_refused),
    ("CP2-X02", x02_node_dash_e_writefilesync_still_refused),
    ("CP2-X03", x03_for_loop_sed_i_on_loop_variable_still_refused),
    ("CP2-X04", x04_for_loop_redirect_on_loop_variable_still_refused),
    ("CP2-X05", x05_while_read_rm_on_loop_variable_still_refused),
    ("CP2-X06", x06_cp_target_directory_flag_still_refused),
    ("CP2-X07", x07_mv_target_directory_flag_still_refused),
    ("CP2-X08", x08_sed_long_in_place_flag_still_refused),
    ("CP2-X09", x09_find_fprint_write_action_still_refused),
    ("CP2-X10", x10_cp_quoted_destination_still_refused),
    ("CP2-X11", x11_cd_out_of_ratchet_then_ordinary_command_allowed),
    ("CP2-X12", x12_uniq_check_chars_option_value_allowed),
])


# ---- CP2-X13..X18 (round-03 adversarial fix-list: narrower fast-path pass-through) -----------------------------
# Appended after CP2-X01..X12; every existing line above is unchanged. New tests only, registered into TESTS
# below (not inside it).

def guard_bash_cwd(d, env, command, cwd, host="claude"):
    """Same as guard_bash, but with the hook payload's own `cwd` set to `cwd` instead of the project root (to
    exercise the guard when the agent's shell is already sitting inside a run folder)."""
    payload = {"session_id": "s", "cwd": str(cwd), "hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": command}}
    return rx(d, env, "hook", "guard", "--host", host, stdin=json.dumps(payload))


def x13_sed_write_commands_still_refused():
    env = make_env("claude")
    d = hk_project(env)
    bad, bad_msg = refuse_table(d, env, [
        ("sed -n 1w (no space)", "sed -n '1w.ratchet/runs/hk/state.json' /tmp/in.txt"),
        ("sed -n pw (substitute + write)", "sed -n 's/x/y/pw .ratchet/runs/hk/state.json' /tmp/in.txt"),
        ("sed e (run the result)", "sed 's/a/b/e' /tmp/in.txt .ratchet/runs/hk/state.json"),
    ], OWNS_MSG_RE)
    check("[CP2-X13] sed's w, pw and e commands onto or naming a run file are still refused (exit 2)",
          bad == [], bad)
    check("[CP2-X13] each refusal carries the Ratchet-owns-this-file message", bad_msg == [], bad_msg)


def x14_awk_variable_write_target_still_refused():
    env = make_env("claude")
    d = hk_project(env)
    bad, bad_msg = refuse_table(d, env, [
        ("awk f=var operand", "awk '{print > f}' f=.ratchet/runs/hk/state.json /tmp/in.txt"),
        ("awk -v f=var", "awk -v f=.ratchet/runs/hk/state.json '{print > f}' /tmp/in.txt"),
    ], OWNS_MSG_RE)
    check("[CP2-X14] awk writing through a variable naming a run file is still refused (exit 2) whether the "
          "variable is a bare operand or set with -v", bad == [], bad)
    check("[CP2-X14] each refusal carries the Ratchet-owns-this-file message", bad_msg == [], bad_msg)


def x15_cp_destination_flag_and_substitution_still_refused():
    env = make_env("claude")
    d = hk_project(env)
    bad, bad_msg = refuse_table(d, env, [
        ("cp -vt", "cp -vt .ratchet/runs/hk/ /tmp/s"),
        ("cp --target", "cp --target .ratchet/runs/hk/ /tmp/s"),
        ("cp dest from substitution", 'cp /tmp/state.bak "$(find .ratchet/runs -name state.json)"'),
    ], OWNS_MSG_RE)
    check("[CP2-X15] cp's destination-directory flags, and a destination built from a command substitution, "
          "are still refused (exit 2) onto a run file", bad == [], bad)
    check("[CP2-X15] each refusal carries the Ratchet-owns-this-file message", bad_msg == [], bad_msg)


def x16_for_loop_and_find_exec_writer_still_refused():
    env = make_env("claude")
    d = hk_project(env)
    bad, bad_msg = refuse_table(d, env, [
        ("for d in runs/*/; do cd; rm; cd -; done",
         'for d in .ratchet/runs/*/; do cd "$d"; rm -f human_log.jsonl; cd -; done'),
        ("find -exec sed -i", "find .ratchet/runs -name human_log.jsonl -exec sed -i '$d' {} +"),
        ("find -exec cp", "find .ratchet/runs -name x -exec cp {} .ratchet/runs/hk/ \\;"),
    ], OWNS_MSG_RE)
    check("[CP2-X16] a for-loop that cd's into each run folder to rm a file, and find -exec sed -i / find -exec "
          "cp onto a run file, are still refused (exit 2)", bad == [], bad)
    check("[CP2-X16] each refusal carries the Ratchet-owns-this-file message", bad_msg == [], bad_msg)


def x17_shell_already_inside_run_folder_still_refused():
    env = make_env("claude")
    d = hk_project(env)
    run = d / ".ratchet" / "runs" / "hk"
    bad = []
    for label, cmd in [("rm", "rm state.json"), ("echo >", "echo x > state.json"),
                        ("sed -i", "sed -i s/a/b/ state.json")]:
        r = guard_bash_cwd(d, env, cmd, run)
        if r.returncode != 2:
            bad.append((label, r.returncode, (r.stderr + r.stdout)[:300]))
    check("[CP2-X17] with the agent's shell already inside the run folder, rm / echo > / sed -i on a bare "
          "filename are still refused (exit 2) even with no .ratchet token in the command text",
          bad == [], bad)


def x18_run_folder_found_via_substitution_or_cwd_read_allowed():
    env = make_env("claude")
    d = hk_project(env)
    run = d / ".ratchet" / "runs" / "hk"
    # Director decision (CP4 round 6): a command naming the run folder with $( or a backtick is refused, so
    # "RUN=$(find ...) && cat" moved from allowed to refused below.
    r = guard_bash(d, env, 'RUN=$(find .ratchet/runs -maxdepth 1 -type d -name hk) && cat "$RUN/state.json"')
    check("[CP2-X18] a run folder found via an unquoted multi-word $(...) capture is refused (exit 2) because "
          "it names the run folder and contains a command substitution", r.returncode == 2,
          (r.returncode, r.stderr, r.stdout))
    bad = allow_table(d, env, [
        ("cat run file redirected to /tmp", "cat .ratchet/runs/hk/state.json > /tmp/y"),
    ])
    check("[CP2-X18] a plain read redirected to /tmp is allowed (exit 0)", bad == [], bad)
    bad_cwd = []
    for label, cmd in [("cat", "cat state.json"), ("ls", "ls")]:
        r = guard_bash_cwd(d, env, cmd, run)
        if r.returncode != 0:
            bad_cwd.append((label, r.returncode, (r.stderr + r.stdout)[:300]))
    check("[CP2-X18] with the agent's shell already inside the run folder, a plain read (cat, ls) is still "
          "allowed (exit 0)", bad_cwd == [], bad_cwd)


TESTS.extend([
    ("CP2-X13", x13_sed_write_commands_still_refused),
    ("CP2-X14", x14_awk_variable_write_target_still_refused),
    ("CP2-X15", x15_cp_destination_flag_and_substitution_still_refused),
    ("CP2-X16", x16_for_loop_and_find_exec_writer_still_refused),
    ("CP2-X17", x17_shell_already_inside_run_folder_still_refused),
    ("CP2-X18", x18_run_folder_found_via_substitution_or_cwd_read_allowed),
])


# ---- CP2-X19..X23 (round-04 adversarial fix-list: quoted separators, one read-only list, every commit -m, a
# note outside .ratchet that only names a run path) ---------------------------------------------------------
# Appended after CP2-X13..X18; every existing line above is unchanged. New tests only, registered into TESTS
# below (not inside it).

def x19_quoted_pipe_reads_allowed():
    env = make_env("claude")
    d = hk_project(env)
    bad = allow_table(d, env, [
        ("jq filter with a quoted |", "jq '.gates | keys' .ratchet/runs/hk/state.json"),
        ("cat piped into a jq filter with a quoted |", "cat .ratchet/runs/hk/state.json | jq '.a | .b'"),
        ("grep -E with a quoted alternation", "grep -E 'a|b' .ratchet/runs/hk/state.json"),
        ("awk -F with a quoted separator", "awk -F'|' '{print $1}' .ratchet/runs/hk/state.json"),
    ])
    check("[CP2-X19] jq/grep/awk forms whose own quoted filter, pattern or separator holds a | are allowed "
          "(exit 0): a quoted | ; or & is not a statement separator", bad == [], bad)
    run = d / ".ratchet" / "runs" / "hk"
    bad_cwd = []
    for label, cmd in [("jq quoted |, cwd inside", "jq '.a | .b' state.json"),
                        ("grep -E quoted alternation, cwd inside", "grep -E 'a|b' state.json")]:
        r = guard_bash_cwd(d, env, cmd, run)
        if r.returncode != 0:
            bad_cwd.append((label, r.returncode, (r.stderr + r.stdout)[:300]))
    check("[CP2-X19] the same quoted-| forms are allowed (exit 0) with the agent's shell already inside the "
          "run folder", bad_cwd == [], bad_cwd)


def x20_xargs_rg_allowed():
    env = make_env("claude")
    d = hk_project(env)
    bad = allow_table(d, env, [
        ("cd .ratchet && find | xargs rg",
         "cd .ratchet && find runs -name '*.jsonl' | xargs rg x"),
    ])
    check("[CP2-X20] xargs running rg (not just grep) over files found inside .ratchet/ is allowed (exit 0): "
          "the fast and slow paths agree on one read-only program list", bad == [], bad)


def x21_every_commit_message_and_am_allowed():
    env = make_env("claude")
    d = hk_project(env)
    bad = allow_table(d, env, [
        ("git commit -m subject -m body (both naming a run path)",
         'git commit -m "fix guard" -m "blocks writes to .ratchet/runs/hk/state.json"'),
        ("git commit -am (combined add+message)",
         "git commit -am 'see .ratchet/runs/hk/state.json'"),
    ])
    check("[CP2-X21] a git commit with a second -m (subject plus body) and a git commit -am are both allowed "
          "(exit 0) when the message only names a run path", bad == [], bad)


def x22_note_outside_ratchet_after_cd_allowed():
    env = make_env("claude")
    d = hk_project(env)
    bad = allow_table(d, env, [
        ("cd elsewhere, then a note that only names a run path",
         "cd /tmp/work && echo 'brief: see .ratchet/runs/hk/state.json' > brief.md"),
    ])
    check("[CP2-X22] writing a note outside .ratchet/ after cd-ing to an unrelated directory is allowed "
          "(exit 0) even though its own text names a run path", bad == [], bad)


def x23_still_refused_forms():
    env = make_env("claude")
    d = hk_project(env)
    bad, bad_msg = refuse_table(d, env, [
        ("jq quoted | filter reading and writing back onto the run file",
         "jq '.a | .b' .ratchet/runs/hk/state.json > .ratchet/runs/hk/state.json"),
        ("grep quoted alternation writing onto the run file",
         "grep 'a|b' x > .ratchet/runs/hk/state.json"),
        ("cd into the run folder, then a relative note with no .ratchet token",
         "cd .ratchet/runs/hk && echo x > brief.md"),
        ("git commit -m whose value is a live substitution that itself redirects",
         'git commit -m "$(cat .ratchet/runs/hk/state.json > /tmp/x)"'),
        ("cd to an unresolvable variable, then a relative write with no .ratchet token",
         'cd "$d" && echo x > state.json'),
    ], OWNS_MSG_RE)
    check("[CP2-X23] a write-back onto the run file through a quoted jq/grep filter, a note written after "
          "cd-ing into a run folder, a commit message whose live substitution itself redirects, and a write "
          "after an unresolvable cd, are all still refused (exit 2)", bad == [], bad)
    check("[CP2-X23] each refusal carries the Ratchet-owns-this-file message", bad_msg == [], bad_msg)


TESTS.extend([
    ("CP2-X19", x19_quoted_pipe_reads_allowed),
    ("CP2-X20", x20_xargs_rg_allowed),
    ("CP2-X21", x21_every_commit_message_and_am_allowed),
    ("CP2-X22", x22_note_outside_ratchet_after_cd_allowed),
    ("CP2-X23", x23_still_refused_forms),
])


# ---- CP2-X24..X28 (round-05 adversarial fix-list: the slow path's unresolvable-cd flag only judges a
# redirect's own relative target, never gates a whole segment that has no mention at all, so an ordinary cd
# to a repo-root variable or a command substitution, or cd -, followed by an unrelated build or push, is not
# refused; the fast path's quote and redirect handling is quote-aware, so a variable or quoted redirect
# target, an &> or fd-prefixed redirect, a live $(...) inside a note's own quotes, and an awk script's own
# embedded `>` are all judged correctly) --------------------------------------------------------------------
# Appended after CP2-X19..X23; every existing line above is unchanged. New tests only, registered into TESTS
# below (not inside it).

CWD_INSIDE_MSG_RE = r"cd back to the project root"
CMD_INSIDE_MSG_RE = r"separate call"


def x24_variable_and_fd_redirect_targets_still_refused():
    env = make_env("claude")
    d = hk_project(env)
    bad, bad_msg = refuse_table(d, env, [
        ("echo through a variable set earlier in the command",
         'S=.ratchet/runs/hk/state.json; echo \'{}\' > "$S"'),
        ("printf through a variable built from another variable",
         'RUN=.ratchet/runs/hk; printf x > "$RUN/state.json"'),
        ("append to the human log", 'echo x >> ".ratchet/human_log.jsonl"'),
        ("&> redirect", "echo x &> .ratchet/runs/hk/state.json"),
        ("1> redirect", "echo x 1> .ratchet/runs/hk/state.json"),
        ("a live $(...) inside a note's own double quotes", 'echo "x $(rm -f .ratchet/runs/hk/state.json)"'),
    ], OWNS_MSG_RE)
    check("[CP2-X24] a quoted or variable redirect target, an &> or fd-prefixed redirect, and a live $(...) "
          "substitution inside a note's own quotes, are all still refused (exit 2)", bad == [], bad)
    check("[CP2-X24] each refusal carries the Ratchet-owns-this-file message", bad_msg == [], bad_msg)


def x25_awk_begin_print_redirect_still_refused():
    env = make_env("claude")
    d = hk_project(env)
    r = guard_bash(d, env, 'awk \'BEGIN{f=".ratchet/runs/hk/state.json"; print "{}" > f}\' x')
    check("[CP2-X25] an awk BEGIN block that assigns a run path to a variable, then prints to it, is refused "
          "(exit 2) even though the redirect sits inside the script's own quotes",
          r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP2-X25] the refusal carries the Ratchet-owns-this-file message",
          re.search(OWNS_MSG_RE, r.stderr + r.stdout, re.I) is not None, r.stderr + r.stdout)


def x26_ordinary_cd_then_build_or_push_allowed():
    env = make_env("claude")
    d = hk_project(env)
    bad = allow_table(d, env, [
        ("cd to a variable then make", 'cd "$REPO" && make test'),
        ("cd to a command substitution then npm", 'cd "$(git rev-parse --show-toplevel)" && npm test'),
        ("cd - then git push", "cd - && git push"),
    ])
    check("[CP2-X26] an ordinary cd to a repo-root variable or command substitution, or cd -, followed by an "
          "unrelated build or push with no redirect at all, is allowed (exit 0): an unresolvable cd target "
          "never by itself refuses a command that writes nothing", bad == [], bad)


def x27_note_after_cd_with_quoted_punctuation_allowed():
    env = make_env("claude")
    d = hk_project(env)
    bad = allow_table(d, env, [
        ("double-quoted note with a ; inside it",
         'cd /tmp/work && echo "note; see .ratchet/runs/hk/state.json" > brief.md'),
        ("single-quoted note with a ; inside it",
         "cd /tmp/work && echo 'note; see .ratchet/runs/hk/state.json' > brief.md"),
    ])
    check("[CP2-X27] a note written after cd-ing to an unrelated absolute directory, whose own quoted text "
          "holds a ; and names a run path, is allowed (exit 0) via the fast path", bad == [], bad)


def x28_refusal_messages_pinned():
    env = make_env("claude")
    d = hk_project(env)
    run = d / ".ratchet" / "runs" / "hk"
    r1 = guard_bash_cwd(d, env, "echo x > state.json", run)
    check("[CP2-X28] with the shell already inside the run folder, the refusal names cd-ing back to the "
          "project root", r1.returncode == 2 and bool(re.search(CWD_INSIDE_MSG_RE, r1.stderr + r1.stdout, re.I)),
          (r1.returncode, r1.stderr, r1.stdout))
    r2 = guard_bash(d, env, "cd .ratchet/runs/hk && echo x > brief.md")
    check("[CP2-X28] with a cd into the run folder earlier in the same command, the refusal points at a "
          "separate call after cd-ing back out",
          r2.returncode == 2 and bool(re.search(CMD_INSIDE_MSG_RE, r2.stderr + r2.stdout, re.I)),
          (r2.returncode, r2.stderr, r2.stdout))


TESTS.extend([
    ("CP2-X24", x24_variable_and_fd_redirect_targets_still_refused),
    ("CP2-X25", x25_awk_begin_print_redirect_still_refused),
    ("CP2-X26", x26_ordinary_cd_then_build_or_push_allowed),
    ("CP2-X27", x27_note_after_cd_with_quoted_punctuation_allowed),
    ("CP2-X28", x28_refusal_messages_pinned),
])


# ---- CP2-X29 (orchestrator check finding: a quoted redirect target into a run file was allowed because the
# quote characters weren't stripped before the .ratchet check) -----------------------------------------------
# Appended after CP2-X24..X28; every existing line above is unchanged. New tests only, registered into TESTS
# below (not inside it).

def x29_quoted_redirect_target_onto_run_file_still_refused():
    env = make_env("claude")
    d = hk_project(env)
    bad, bad_msg = refuse_table(d, env, [
        ("jq redirected onto its own run file, double-quoted",
         'jq . .ratchet/runs/hk/state.json > ".ratchet/runs/hk/state.json"'),
        ("cat redirected onto the human log, single-quoted",
         "cat /tmp/x > '.ratchet/human_log.jsonl'"),
        ("append onto evidence, double-quoted",
         'echo x >> ".ratchet/runs/hk/evidence/CP1/behavior.json"'),
    ], OWNS_MSG_RE)
    check("[CP2-X29] a quoted redirect target that resolves into a run file is still refused (exit 2) whether "
          "single- or double-quoted", bad == [], bad)
    check("[CP2-X29] each refusal carries the Ratchet-owns-this-file message", bad_msg == [], bad_msg)


def x30_quoted_redirect_target_outside_ratchet_still_allowed():
    env = make_env("claude")
    d = hk_project(env)
    bad = allow_table(d, env, [
        ("jq redirected to a quoted /tmp path", 'jq . .ratchet/runs/hk/state.json > "/tmp/out.json"'),
        ("note redirected to a single-quoted /tmp path", "echo \"see .ratchet\" > '/tmp/note.md'"),
    ])
    check("[CP2-X30] a quoted redirect target outside .ratchet/ is still allowed (exit 0)", bad == [], bad)


TESTS.extend([
    ("CP2-X29", x29_quoted_redirect_target_onto_run_file_still_refused),
    ("CP2-X30", x30_quoted_redirect_target_outside_ratchet_still_allowed),
])


# ---- CP2-X31/X32 (adversarial round-06 finding: a redirect was judged only against the command's FINAL cd
# state, so a write followed later in the same command by an absolute cd back out passed the fast path) ------
# Appended after CP2-X29/X30; every existing line above is unchanged. New tests only, registered into TESTS
# below (not inside it).

def x31_write_then_cd_out_of_run_folder_still_refused():
    env = make_env("claude")
    d = hk_project(env)
    bad, bad_msg = refuse_table(d, env, [
        ("write then absolute cd out, same statement sequence",
         "cd .ratchet/runs/hk && echo '{}' > state.json; cd /tmp"),
        ("write then cd - back out",
         "cd .ratchet/runs/hk && echo x > state.json && cd -"),
        ("pushd/write/popd",
         "pushd .ratchet/runs/hk && echo x > state.json; popd"),
        ("two cds into the run folder then an absolute cd out",
         "cd .ratchet && cd runs/hk && echo x > state.json; cd /tmp"),
    ], OWNS_MSG_RE)
    check("[CP2-X31] a shell write into a run file is still refused even when a later cd in the same command "
          "leaves the run folder", bad == [], bad)
    check("[CP2-X31] each refusal carries the Ratchet-owns-this-file message", bad_msg == [], bad_msg)


def x32_note_or_read_after_cd_still_allowed():
    env = make_env("claude")
    d = hk_project(env)
    bad = allow_table(d, env, [
        ("a note after an absolute cd elsewhere, mentioning a run path in its own quoted text",
         'cd /tmp/work && echo "note; see .ratchet/runs/hk/state.json" > brief.md'),
        ("cd into .ratchet to read, then an absolute cd out to write unrelated output",
         "cd .ratchet && ls runs; cd /tmp && echo done > /tmp/x"),
        ("a plain read redirected out while the shell is still inside the run folder",
         "cd .ratchet/runs/hk && cat state.json > /tmp/copy.json"),
    ])
    check("[CP2-X32] a note elsewhere or a read redirected out is still allowed after cd-ing around .ratchet/",
          bad == [], bad)


TESTS.extend([
    ("CP2-X31", x31_write_then_cd_out_of_run_folder_still_refused),
    ("CP2-X32", x32_note_or_read_after_cd_still_allowed),
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
