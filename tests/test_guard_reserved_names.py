#!/usr/bin/env python3
"""
CP1 of the "redo the guard hook's checks under the realistic-use bar" run: notes mentioning reserved names
are not refused.   python3 tests/test_guard_reserved_names.py

An agent can write a brief, a note or a commit message that mentions Ratchet's reserved variables
(RATCHET_HUMAN_OVERRIDE, RATCHET_ROLE, RATCHET_HOME, RATCHET_DIRECTOR_KEY_FILE) or its hook command in data or
prose, while actually setting one of those variables or calling the hook is still refused. This file reuses
tests/trust_helpers.py (the same throwaway-project/`rx`/`make_env` helpers tests/test_trust_cp4.py already uses)
and drives the guard through its public entry point only: `ratchet.py hook guard --host <host>` on stdin, never
decide_guard or _reserved_env_problem directly, so a fix elsewhere in ratchet.py doesn't break this file's imports.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP1-T01",
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
project = tt.project

PASS = FAIL = 0
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
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


# ---- guard driver ------------------------------------------------------------------------------------------------

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


# ---- fixtures -----------------------------------------------------------------------------------------------------

FP1_BRIEF = (
    "cat > /tmp/brief.md <<'EOF'\n"
    "RATCHET=\"python3 /home/rahil/.claude/skills/ratchet-orchestrator/scripts/ratchet.py\"\n"
    "Set RATCHET_HUMAN_OVERRIDE only at your own terminal; RATCHET_ROLE is for the director.\n"
    "Do not export, source, local or eval a reserved name.\n"
    "EOF"
)


# ---- CP1-T01 --------------------------------------------------------------------------------------------------

def t01_live_brief_heredoc_allowed():
    env = make_env("claude")
    d = project(env)
    r = guard_bash(d, env, FP1_BRIEF)
    check("[CP1-T01] writing the live example brief to /tmp (heredoc with RATCHET= and reserved-name prose) "
          "is allowed (exit 0)", r.returncode == 0, (r.returncode, r.stderr, r.stdout))
    check("[CP1-T01] the allow carries no RATCHET GUARD refusal message",
          "RATCHET GUARD" not in (r.stderr + r.stdout), r.stderr + r.stdout)


# ---- CP1-T02 --------------------------------------------------------------------------------------------------

def t02_notes_mentioning_names_allowed():
    env = make_env("claude")
    d = project(env)
    bad = allow_table(d, env, [
        ("printf note", "printf '%s\\n' \"RATCHET_HUMAN_OVERRIDE=1 is for humans\" > /tmp/n.md"),
        ("echo note", "echo \"RATCHET_ROLE=director is for the director only\" > /tmp/note.txt"),
        ("git commit message", "git commit --allow-empty -m 'guard: refuse export RATCHET_HUMAN_OVERRIDE=1'"),
    ])
    check("[CP1-T02] a note, echo, printf or commit message that only names a reserved variable is allowed "
          "(exit 0), every one writing outside .ratchet/", bad == [], bad)


# ---- CP1-T03 --------------------------------------------------------------------------------------------------

def t03_grep_patterns_naming_reserved_allowed():
    env = make_env("claude")
    d = project(env)
    (d / "tests").mkdir(exist_ok=True)
    (d / "tests" / "sample.py").write_text("# Calling `ratchet.py hook guard` yourself is refused.\n"
                                            "# RATCHET_ROLE must never be set by an agent.\n")
    bad = allow_table(d, env, [
        ("grep alternation pattern", "grep -E 'RATCHET_ROLE|RATCHET_HOME' tests/sample.py"),
        ("grep backtick-quoted phrase", "grep -rn \"Calling \\`ratchet.py hook guard\\`\" tests/"),
        ("for-loop command substitution", "for f in $(ls tests/*.py); do grep -c 'hook guard' \"$f\"; done"),
    ])
    check("[CP1-T03] a grep/search pattern naming a reserved variable or 'hook guard', even next to a $(...) or "
          "backtick elsewhere in the command, is allowed (exit 0)", bad == [], bad)


# ---- CP1-T04 --------------------------------------------------------------------------------------------------

def t04_plain_reads_and_nonhook_use_allowed():
    env = make_env("claude")
    d = project(env)
    bad = allow_table(d, env, [
        ("echo $VAR", "echo $RATCHET_ROLE"),
        ("printenv", "printenv RATCHET_HOME"),
        ("assign engine path then run non-hook command",
         'RATCHET="python3 /home/rahil/.claude/skills/ratchet-orchestrator/scripts/ratchet.py"; $RATCHET status'),
    ])
    check("[CP1-T04] plainly reading a reserved variable, or assigning RATCHET to the engine path and running a "
          "non-hook command with it, is allowed (exit 0)", bad == [], bad)


# ---- CP1-T05 --------------------------------------------------------------------------------------------------

def t05_setting_human_override_still_refused():
    env = make_env("claude")
    d = project(env, approved=False)
    bad, bad_msg = refuse_table(d, env, [
        ("export", "export RATCHET_HUMAN_OVERRIDE=1; python3 ratchet.py human-approve"),
        ("declare -x", "declare -x RATCHET_HUMAN_OVERRIDE=1; ratchet.py human-approve"),
        ("typeset", "typeset RATCHET_HUMAN_OVERRIDE=1; ratchet.py human-approve"),
        ("eval indirect", 'V=RATCHET_HUMAN_OVERRIDE; eval "$V=1 ratchet.py human-approve"'),
        ("python3 os.environ", "python3 -c \"import os,subprocess; os.environ['RATCHET_HUMAN_OVERRIDE']='1'; "
                               "subprocess.run(['python3','ratchet.py','human-approve'])\""),
    ], r"override.*(person|terminal)|terminal.*person")
    check("[CP1-T05] every direct and indirect way of setting RATCHET_HUMAN_OVERRIDE is still refused (exit 2)",
          bad == [], bad)
    check("[CP1-T05] each refusal names the override as reserved for a person at their own terminal",
          bad_msg == [], bad_msg)


# ---- CP1-T06 --------------------------------------------------------------------------------------------------

def t06_setting_role_still_refused_even_before_ls():
    env = make_env("claude")
    d = project(env, approved=False)
    bad, bad_msg = refuse_table(d, env, [
        ("leading assignment before ls (today's gap)", "RATCHET_ROLE=director ls"),
        ("export", "export RATCHET_ROLE=director; python3 ratchet.py status"),
        ("declare -x", "declare -x RATCHET_ROLE=director; ratchet.py director-key"),
        ("typeset", "typeset RATCHET_ROLE=director; ratchet.py director-sign approve-plan"),
        ("eval indirect", 'V=RATCHET_ROLE; eval "$V=director ratchet.py director-key"'),
        ("python3 os.environ", "python3 -c \"import os,subprocess; os.environ['RATCHET_ROLE']='director'; "
                               "subprocess.run(['python3','ratchet.py','director-key'])\""),
    ], r"director session|RATCHET_ROLE")
    check("[CP1-T06] every way of setting RATCHET_ROLE is still refused (exit 2), including a leading reserved "
          "assignment in front of a read-only program like ls (closes today's gap)", bad == [], bad)
    check("[CP1-T06] each refusal names RATCHET_ROLE or the director session",
          bad_msg == [], bad_msg)


# ---- CP1-T07 --------------------------------------------------------------------------------------------------

def t07_setting_home_still_refused():
    env = make_env("claude")
    d = project(env, approved=False)
    bad, bad_msg = refuse_table(d, env, [
        ("env NAME=value", "env RATCHET_HOME=./evil-home ratchet.py status"),
        ("export", "export RATCHET_HOME=./evil-home; python3 ratchet.py status"),
        ("declare -x", "declare -x RATCHET_HOME=./evil-home; ratchet.py status"),
        ("typeset", "typeset RATCHET_HOME=./evil-home; ratchet.py status"),
        ("eval indirect", 'V=RATCHET_HOME; eval "$V=./evil-home ratchet.py status"'),
    ], r"per-user folder|RATCHET_HOME")
    check("[CP1-T07] every way of setting RATCHET_HOME is still refused (exit 2)", bad == [], bad)
    check("[CP1-T07] each refusal names RATCHET_HOME or Ratchet's per-user folder",
          bad_msg == [], bad_msg)


# ---- CP1-T08 --------------------------------------------------------------------------------------------------

def t08_setting_director_key_file_still_refused():
    env = make_env("claude")
    d = project(env, approved=False)
    bad, bad_msg = refuse_table(d, env, [
        ("declare -x", "declare -x RATCHET_DIRECTOR_KEY_FILE=./evil.key; ratchet.py director-sign approve-plan"),
        ("typeset", "typeset RATCHET_DIRECTOR_KEY_FILE=./evil.key; ratchet.py director-sign approve-plan"),
        ("eval indirect",
         'V=RATCHET_DIRECTOR_KEY_FILE; eval "$V=./evil.key ratchet.py director-sign approve-plan"'),
        ("python3 os.environ",
         "python3 -c \"import os,subprocess; os.environ['RATCHET_DIRECTOR_KEY_FILE']='./evil.key'; "
         "subprocess.run(['python3','ratchet.py','director-sign','approve-plan'])\""),
    ], r"director key|RATCHET_DIRECTOR_KEY_FILE")
    check("[CP1-T08] every way of setting RATCHET_DIRECTOR_KEY_FILE is still refused (exit 2)", bad == [], bad)
    check("[CP1-T08] each refusal names the director key path",
          bad_msg == [], bad_msg)


# ---- CP1-T09 --------------------------------------------------------------------------------------------------

def t09_heredoc_run_as_code_still_refused():
    env = make_env("claude")
    d = project(env, approved=False)
    bad, _bad_msg = refuse_table(d, env, [
        ("heredoc fed to bash",
         "bash <<'EOF'\nexport RATCHET_ROLE=director\npython3 ratchet.py status\nEOF"),
        ("heredoc fed to python3",
         "python3 - <<'EOF'\nimport os\nos.environ['RATCHET_HUMAN_OVERRIDE']='1'\nEOF"),
        ("bash -c with inline export",
         "bash -c 'export RATCHET_ROLE=director; python3 ratchet.py status'"),
    ], r".")
    check("[CP1-T09] a heredoc body fed to bash or python, or a bash -c string, that sets a reserved variable is "
          "still refused (exit 2); unlike CP1-T01's heredoc, which only writes a file and is never run", bad == [],
          bad)


# ---- CP1-T10 --------------------------------------------------------------------------------------------------

def t10_hook_call_still_refused():
    env = make_env("claude")
    d = project(env, approved=False)
    bad, bad_msg = refuse_table(d, env, [
        ("direct call", "python3 ratchet.py hook guard --host claude"),
        ("through $RATCHET",
         'RATCHET="python3 /home/rahil/.claude/skills/ratchet-orchestrator/scripts/ratchet.py"; '
         '$RATCHET hook prompt --host claude'),
        ("inside $(...) next to a harmless mention",
         'echo "testing hook guard" && $(python3 ratchet.py hook guard --host claude)'),
    ], r"hooks are called by your agent CLI|not by you")
    check("[CP1-T10] calling ratchet.py's own hook command directly, through $RATCHET, or inside a $(...) "
          "substitution is still refused (exit 2), even with a harmless mention elsewhere in the command",
          bad == [], bad)
    check("[CP1-T10] each refusal carries the existing 'hooks are called by your agent CLI, not by you' message",
          bad_msg == [], bad_msg)


# ---- CP1-T11 --------------------------------------------------------------------------------------------------

def t11_cp4_forms_no_regression():
    env = make_env("claude")
    d = project(env, approved=False)
    forms = ["export RATCHET_ROLE=director; python3 ratchet.py status",
             "env RATCHET_ROLE=director python3 ratchet.py status",
             "(RATCHET_ROLE=director python3 ratchet.py status)",
             "RATCHET_HUMAN_OVERRIDE=1 python3 ratchet.py approve-plan",
             "export RATCHET_HUMAN_OVERRIDE=1; python3 ratchet.py human-approve",
             "env FOO=1 RATCHET_HUMAN_OVERRIDE=1 python3 ratchet.py human-approve"]
    res = [(f, guard_bash(d, env, f)) for f in forms]
    check("[CP1-T11] every form tests/test_trust_cp4.py's CP4-T05..T08 and CP4-X05 proved refused is still "
          "refused (exit 2); no regression from this checkpoint's fix",
          all(r.returncode == 2 for _f, r in res), [(f, r.returncode) for f, r in res if r.returncode != 2])


# ---- CP1-T12 --------------------------------------------------------------------------------------------------

def t12_second_host_dialect_agrees():
    env = make_env("claude")
    d = project(env, approved=False)
    allow_cmd = FP1_BRIEF
    refuse_cmd = "export RATCHET_ROLE=director; python3 ratchet.py status"
    for host in ("codex", "gemini"):
        r_allow = guard_bash(d, env, allow_cmd, host=host)
        check(f"[CP1-T12] {host}: the live FP1 heredoc brief is allowed (exit 0), same verdict as claude",
              r_allow.returncode == 0, (host, r_allow.returncode, r_allow.stderr, r_allow.stdout))
        r_refuse = guard_bash(d, env, refuse_cmd, host=host)
        check(f"[CP1-T12] {host}: setting RATCHET_ROLE before a command is refused (exit 2), same verdict as "
              "claude", r_refuse.returncode == 2, (host, r_refuse.returncode, r_refuse.stderr, r_refuse.stdout))


# ---- CP1-T13 --------------------------------------------------------------------------------------------------

def t13_docs_say_what_the_guard_allows_and_refuses():
    root = ENGINE.parents[1]  # .../skills/ratchet-orchestrator
    repo_root = ENGINE.parents[3]  # the project root (this worktree)
    readme = (repo_root / "README.md").read_text()
    skill = (root / "SKILL.md").read_text()
    schemas = (root / "references" / "schemas.md").read_text()
    docs = readme + "\n" + skill + "\n" + schemas
    allow_stmt = re.search(
        r"mention(s|ing)?[^.\n]{0,200}(reserved|RATCHET_HUMAN_OVERRIDE|RATCHET_ROLE|RATCHET_HOME|"
        r"RATCHET_DIRECTOR_KEY_FILE)[^.\n]{0,200}allow|allow[^.\n]{0,200}mention(s|ing)?[^.\n]{0,200}"
        r"(reserved|RATCHET_HUMAN_OVERRIDE|RATCHET_ROLE|RATCHET_HOME|RATCHET_DIRECTOR_KEY_FILE)",
        docs, re.I)
    refuse_stmt = re.search(
        r"(setting|set(s)?)[^.\n]{0,200}(reserved|RATCHET_HUMAN_OVERRIDE|RATCHET_ROLE|RATCHET_HOME|"
        r"RATCHET_DIRECTOR_KEY_FILE)[^.\n]{0,200}(refus|block)|hook[^.\n]{0,100}(refus|block)",
        docs, re.I)
    check("[CP1-T13] README, SKILL.md or schemas.md state that a command which only mentions Ratchet's reserved "
          "variable names, paths or hook command in data or text is allowed",
          allow_stmt is not None, "no allow-on-mention sentence found in README/SKILL.md/schemas.md")
    check("[CP1-T13] the same docs state that actually setting a reserved variable or calling the hook directly "
          "is still refused", refuse_stmt is not None,
          "no still-refused sentence found in README/SKILL.md/schemas.md")


# ---- CP1-T14 --------------------------------------------------------------------------------------------------

def t14_quoted_mention_near_substitution_allowed_real_call_still_refused():
    env = make_env("claude")
    d = project(env)
    bad = allow_table(d, env, [
        ("commit subject naming the hook, unrelated $(cat) body",
         "git commit -m 'Document ratchet.py hook guard' -m \"$(cat /tmp/body.txt)\""),
        ("heredoc note saved to /tmp mentioning the full hook command",
         "cat > /tmp/hookbrief.md <<'EOF'\nDocument that calling ratchet.py hook guard yourself is refused.\nEOF"),
    ])
    check("[CP1-T14] a commit message quoting the hook command, or a heredoc note saved to /tmp mentioning it, "
          "is allowed (exit 0) even with an unrelated command substitution elsewhere in the same command",
          bad == [], bad)
    d2 = project(env, approved=False)
    r = guard_bash(d2, env,
                    'echo "Document ratchet.py hook guard" && $(python3 ratchet.py hook guard --host claude)')
    check("[CP1-T14] a real hook call inside $(...) next to a harmless quoted mention of the same words is "
          "still refused (exit 2)", r.returncode == 2, (r.returncode, r.stderr, r.stdout))
    check("[CP1-T14] that refusal carries the existing 'hooks are called by your agent CLI, not by you' message",
          re.search(r"hooks are called by your agent CLI|not by you", r.stderr + r.stdout, re.I) is not None,
          r.stderr + r.stdout)


# ---- CP1-T15 --------------------------------------------------------------------------------------------------

RESERVED_NAMES = ["RATCHET_HUMAN_OVERRIDE", "RATCHET_ROLE", "RATCHET_HOME", "RATCHET_DIRECTOR_KEY_FILE"]
RESERVED_MSG_RE = (r"override.*(person|terminal)|terminal.*person|director session|RATCHET_ROLE|"
                    r"per-user folder|RATCHET_HOME|director key|RATCHET_DIRECTOR_KEY_FILE")


def t15_printf_v_and_echo_piped_to_shell_still_refused():
    env = make_env("claude")
    d = project(env, approved=False)
    forms = []
    for name in RESERVED_NAMES:
        forms.append((f"printf -v {name}", f"printf -v {name} x"))
        forms.append((f"echo export {name} | bash",
                       f'echo "export {name}=x" | bash'))
        forms.append((f"echo {name} | sh", f"echo {name}=x | sh"))
    bad, bad_msg = refuse_table(d, env, forms, RESERVED_MSG_RE)
    check("[CP1-T15] printf -v targeting a reserved name, and an echo note of a reserved assignment piped to "
          "bash or sh, are still refused (exit 2) for every reserved variable", bad == [], bad)
    check("[CP1-T15] each refusal names the reserved variable it belongs to", bad_msg == [], bad_msg)

    d2 = project(env)
    ok = allow_table(d2, env, [
        ("git commit note", 'git commit --allow-empty -m "RATCHET_ROLE note"'),
        ("echo note to /tmp", 'echo "RATCHET_ROLE is for the director only" > /tmp/cp1t15.txt'),
        ("live check-in command",
         'RATCHET="python3 /home/rahil/.claude/skills/ratchet-orchestrator/scripts/ratchet.py" && $RATCHET '
         'check-in CP1 --decision simplify --why "skip the echo exemption whenever the command runs code '
         '(an interpreter, eval, source or dot)"'),
    ])
    check("[CP1-T15] a commit note, an echo note to /tmp, and the live check-in command naming the hook "
          "exemption in prose are still allowed (exit 0)", ok == [], ok)


# ---- CP1-T16 --------------------------------------------------------------------------------------------------

def t16_eval_in_prose_allowed_five_refused_forms_held():
    env = make_env("claude")
    d = project(env)
    ok = allow_table(d, env, [
        ("commit message naming eval and a reserved assignment",
         "git commit --allow-empty -m 'Prevent eval from setting RATCHET_ROLE=director'"),
        ("echo note naming eval and a reserved assignment",
         "echo 'Never eval RATCHET_ROLE=director' > /tmp/cp1t16-echo.md"),
        ("printf note naming eval and a reserved assignment",
         "printf '%s\\n' 'Never eval RATCHET_ROLE=director' > /tmp/cp1t16-printf.md"),
        ("heredoc note saved to /tmp naming eval and a reserved assignment",
         "cat > /tmp/cp1t16-heredoc.md <<'EOF'\nNever eval RATCHET_ROLE=director directly.\nEOF"),
    ])
    check("[CP1-T16] a commit message, an echo note, a printf note or a heredoc note saved to /tmp that names "
          "'eval' together with a reserved assignment, in its own quoted or heredoc text, is allowed (exit 0)",
          ok == [], ok)

    d2 = project(env, approved=False)
    forms = []
    for name in RESERVED_NAMES:
        forms.append((f"echo export {name} | bash (T16)", f'echo "export {name}=x" | bash'))
        forms.append((f"echo {name} | sh (T16)", f"echo {name}=x | sh"))
        forms.append((f"printf -v {name} (T16)", f"printf -v {name} x"))
        forms.append((f"eval export {name} (T16)", f'eval "export {name}=x"'))
        forms.append((f"bash -c export {name} (T16)", f"bash -c 'export {name}=x'"))
    bad, _bad_msg = refuse_table(d2, env, forms, RESERVED_MSG_RE)
    check("[CP1-T16] all five easy forms (piped to bash, piped to sh, printf -v, eval, bash -c) stay refused "
          "(exit 2) for every reserved variable, unaffected by the eval-in-prose allowance above",
          bad == [], bad)


# ---- CP1-T17 --------------------------------------------------------------------------------------------------

def t17_one_rule_redesign_must_refuse_and_must_allow():
    env = make_env("claude")
    d = project(env, approved=False)
    forms = {
        "export X=1": "export {n}=1",
        "X=1 cmd": "{n}=1 ls",
        "env X=1 cmd": "env {n}=1 ls",
        "env -S 'X=1 cmd'": "env -S '{n}=1 ls'",
        "declare -x X=1": "declare -x {n}=1",
        "typeset X=1": "typeset {n}=1",
        "printf -v X x": "printf -v {n} x",
        "printf -v 'X' x": "printf -v '{n}' x",
        'export "X=1"': 'export "{n}=1"',
        'eval "export X=1"': 'eval "export {n}=1"',
        "bash -c 'export X=1'": "bash -c 'export {n}=1'",
        "python3 -c os.environ": "python3 -c \"import os; os.environ['{n}']='1'\"",
        'echo "export X=1" | bash': 'echo "export {n}=1" | bash',
        "bash here-string": 'bash <<< "export {n}=1"',
    }
    cases = [(f"{label} ({name})", tpl.format(n=name)) for label, tpl in forms.items() for name in RESERVED_NAMES]
    bad, _bad_msg = refuse_table(d, env, cases, RESERVED_MSG_RE)
    check("[CP1-T17] every must-refuse form of the one-rule redesign (export/leading assign/env/env -S/declare/"
          "typeset/printf -v, bare or quoted/eval/bash -c/python3 -c os.environ/piped-to-bash/here-string) is "
          "still refused (exit 2), for every reserved variable", bad == [], bad)

    bad2, _bad_msg2 = refuse_table(d, env, [
        ("heredoc fed to bash", "bash <<'EOF'\nexport RATCHET_ROLE=director\nEOF"),
        ("heredoc fed to python3",
         "python3 - <<'EOF'\nimport os\nos.environ['RATCHET_HUMAN_OVERRIDE']='1'\nEOF"),
        ("pieces-with-eval", 'V=RATCHET_ROLE; eval "$V=director ratchet.py director-key"'),
    ], RESERVED_MSG_RE)
    check("[CP1-T17] a heredoc fed to bash or python, and the existing pieces-built-then-eval'd form, are "
          "still refused (exit 2)", bad2 == [], bad2)

    d2 = project(env)
    ok = allow_table(d2, env, [
        ("commit subject naming export+reserved, unrelated $(cat) body",
         "git commit -m 'Refuse export RATCHET_ROLE=director' -m \"$(cat /tmp/cp1t17-body.txt)\""),
        ("echo note appended to a file", "echo 'RATCHET_ROLE=x' >> /tmp/cp1t17-notes.md"),
        ("heredoc note saved to /tmp naming export+reserved",
         "cat > /tmp/cp1t17-brief.md <<'EOF'\nexport RATCHET_ROLE=director is refused.\nEOF"),
        ("python3 running a script file with a quoted reserved-looking argument",
         'python3 /home/rahil/.claude/skills/ratchet-orchestrator/scripts/ratchet.py learn '
         '"never set RATCHET_ROLE=director in a lane"'),
        ("live triage command with a quoted reserved-looking --why",
         'RATCHET="python3 /home/rahil/.claude/skills/ratchet-orchestrator/scripts/ratchet.py" && $RATCHET '
         'triage CP1 A1=fix:"RATCHET_HOME=x was allowed"'),
    ])
    check("[CP1-T17] a commit message, an echo note, a heredoc note, a script-file python3 call and the live "
          "triage command, each naming export/eval/a reserved-looking assignment only in their own quoted or "
          "heredoc text, are allowed (exit 0)", ok == [], ok)


# ---- CP1-T18 --------------------------------------------------------------------------------------------------

def t18_every_leading_assignment_checked_not_just_the_first():
    env = make_env("claude")
    d = project(env, approved=False)
    cases = []
    for prog in ("ls", "cat", "python3"):
        for name in RESERVED_NAMES:
            cases.append((f"FOO=1 {name}=director {prog}", f"FOO=1 {name}=director {prog}"))
            cases.append((f"env FOO=1 {name}=director {prog}", f"env FOO=1 {name}=director {prog}"))
    bad, _bad_msg = refuse_table(d, env, cases, RESERVED_MSG_RE)
    check("[CP1-T18] an ordinary assignment before a reserved one in the same leading sequence is still "
          "refused (exit 2), whatever program (ls, cat, python3) follows, with or without a leading `env`",
          bad == [], bad)

    d2 = project(env)
    ok = allow_table(d2, env, [("two ordinary leading assignments before ls", "FOO=1 BAR=2 ls")])
    check("[CP1-T18] two ordinary (non-reserved) leading assignments before ls are allowed (exit 0)",
          ok == [], ok)


# ---- CP1-T19 --------------------------------------------------------------------------------------------------

def t19_ordinary_interpreter_options_before_inline_code_still_refused():
    env = make_env("claude")
    d = project(env, approved=False)
    forms = {
        "python3 -u -c": lambda n: f"python3 -u -c \"__import__('os').environ.update({n}='director')\"",
        "python3 -X dev -c": lambda n: f"python3 -X dev -c \"__import__('os').environ.update({n}='director')\"",
        "bash -x -c": lambda n: f"bash -x -c 'export {n}=director'",
        "node --no-warnings -e": lambda n: f'node --no-warnings -e "process.env[\'{n}\']=\'1\'"',
    }
    cases = [(f"{label} ({name})", fn(name)) for label, fn in forms.items() for name in RESERVED_NAMES]
    bad, _bad_msg = refuse_table(d, env, cases, RESERVED_MSG_RE)
    check("[CP1-T19] python3 -u -c, python3 -X dev -c, bash -x -c and node --no-warnings -e (ordinary "
          "interpreter options before the inline-code option) still count as inline code and are refused "
          "(exit 2) for every reserved variable", bad == [], bad)

    d2 = project(env)
    ok = allow_table(d2, env, [
        ("python3 with an ordinary option before a script file",
         'python3 -u /home/rahil/.claude/skills/ratchet-orchestrator/scripts/ratchet.py learn '
         '"never set RATCHET_ROLE=director"'),
    ])
    check("[CP1-T19] an interpreter run with an ordinary option before a real script file path (not -c/-e) "
          "is still allowed (exit 0)", ok == [], ok)


TESTS = [
    ("CP1-T01", t01_live_brief_heredoc_allowed),
    ("CP1-T02", t02_notes_mentioning_names_allowed),
    ("CP1-T03", t03_grep_patterns_naming_reserved_allowed),
    ("CP1-T04", t04_plain_reads_and_nonhook_use_allowed),
    ("CP1-T05", t05_setting_human_override_still_refused),
    ("CP1-T06", t06_setting_role_still_refused_even_before_ls),
    ("CP1-T07", t07_setting_home_still_refused),
    ("CP1-T08", t08_setting_director_key_file_still_refused),
    ("CP1-T09", t09_heredoc_run_as_code_still_refused),
    ("CP1-T10", t10_hook_call_still_refused),
    ("CP1-T11", t11_cp4_forms_no_regression),
    ("CP1-T12", t12_second_host_dialect_agrees),
    ("CP1-T13", t13_docs_say_what_the_guard_allows_and_refuses),
    ("CP1-T14", t14_quoted_mention_near_substitution_allowed_real_call_still_refused),
    ("CP1-T15", t15_printf_v_and_echo_piped_to_shell_still_refused),
    ("CP1-T16", t16_eval_in_prose_allowed_five_refused_forms_held),
    ("CP1-T17", t17_one_rule_redesign_must_refuse_and_must_allow),
    ("CP1-T18", t18_every_leading_assignment_checked_not_just_the_first),
    ("CP1-T19", t19_ordinary_interpreter_options_before_inline_code_still_refused),
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


# ---- CP1-T20 (adversarial round-07 fix: a hook call hidden behind bash -c/sh -c/python3 -c is not quoted data) ----

def t20_hook_call_hidden_behind_inline_code_still_refused():
    env = make_env("claude")
    d = project(env, approved=False)
    bad, bad_msg = refuse_table(d, env, [
        ("bash -c with a single-quoted hook call inside $(...)",
         "result=$(bash -c 'python3 ratchet.py hook guard --host claude')"),
        ("sh -c with a double-quoted hook call inside $(...)",
         'x=$(sh -c "python3 /p/ratchet.py hook stop")'),
        ("python3 -c building the call as a subprocess.run argv list",
         "out=$(python3 -c \"import subprocess; subprocess.run(['python3','ratchet.py','hook','prompt'])\")"),
    ], r"hooks are called by your agent CLI|not by you")
    check("[CP1-T20] a real hook call hidden inside bash -c/sh -c/python3 -c inline code, itself inside an "
          "unreadable $(...) substitution, is still refused (exit 2) even though its quoted argument would "
          "otherwise look like harmless data", bad == [], bad)
    check("[CP1-T20] each refusal carries the existing 'hooks are called by your agent CLI, not by you' message",
          bad_msg == [], bad_msg)

    ok = allow_table(d, env, [
        ("commit message mentioning the hook, unrelated $(cat) body",
         "git commit -m 'Document ratchet.py hook guard' -m \"$(cat /tmp/body.txt)\""),
    ])
    check("[CP1-T20] the CP1-T14 commit-message mention stays allowed (exit 0): quoted text with no inline "
          "code run in the same command is still just data", ok == [], ok)


TESTS.append(("CP1-T20", t20_hook_call_hidden_behind_inline_code_still_refused))


if __name__ == "__main__":
    main()
