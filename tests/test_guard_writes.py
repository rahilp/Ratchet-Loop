#!/usr/bin/env python3
"""
CP4 of the "redo the guard hook's checks under the realistic-use bar" run: redirect and loop writes into run
files refused.   python3 tests/test_guard_writes.py

An agent that writes into a run's state, evidence or human log through any shell redirect form, a process
substitution, uniq's output operand or a loop over a .ratchet listing is refused, while every read form CP1-CP3
allow still passes. This file reuses tests/trust_helpers.py (the same throwaway-project/`rx`/`make_env` helpers
tests/test_trust_cp4.py and tests/test_guard_reads.py already use) and drives the guard through its public entry
point only: `ratchet.py hook guard --host <host>` on stdin, never decide_guard or _guard_shell directly, so a fix
elsewhere in ratchet.py doesn't break this file's imports.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP4-T01",
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
ID_RE = re.compile(r"\[(CP4-[TX]\d+)\]")


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


# ---- fixtures ---------------------------------------------------------------------------------------------

def hk_project(env):
    """A throwaway git project with `.ratchet/` a real directory (`ratchet.py init --slug hk`), plus a
    realistic run-folder layout on disk written DIRECTLY (not through the guard): a valid state.json (from
    init), an evidence file under evidence/CP1/, a human_log.jsonl, and the hook-errors.log / stop-blocks.jsonl
    shape tests/test_guard_reads.py's CMD_LIVE_A/CMD_LIVE_B fixtures need (CP4-T10 reuses those exact
    commands). Also writes two harmless files outside .ratchet/ (new.json, README.md) that write-form cases use
    as a source to read from."""
    import _tmp
    d = Path(_tmp.mkdtemp(prefix="ratchet-guard-writes-"))
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
        "2026-10-12T00:00:00+00:00 claude guard: KeyError: 'missing'\n")
    (ratchet / "human_log.jsonl").write_text(
        json.dumps({"event": "approve-plan", "session": "s1", "at": "2026-10-12T00:00:00+00:00"}) + "\n" +
        json.dumps({"event": "human-approve", "session": "s1", "at": "2026-10-12T00:00:01+00:00"}) + "\n")

    run = ratchet / "runs" / "hk"
    # init already wrote a valid state.json; keep it (a realistic, valid run state) rather than re-inventing one.
    state = json.loads((run / "state.json").read_text())
    assert state.get("slug") == "hk", state
    (run / "stop-blocks.jsonl").write_text(
        json.dumps({"at": "2026-10-12T00:00:00+00:00", "reason": "blocked"}) + "\n")
    (run / "evidence" / "CP1").mkdir(parents=True, exist_ok=True)
    (run / "evidence" / "CP1" / "x.json").write_text(json.dumps({"gate": "behavior", "verdict": "PASS"}))

    other = ratchet / "runs" / "other"
    other.mkdir(parents=True, exist_ok=True)
    (other / "stop-blocks.jsonl").write_text(
        json.dumps({"at": "2026-10-12T00:00:01+00:00", "reason": "blocked"}) + "\n")

    (d / "new.json").write_text(json.dumps({"ok": True}))
    (d / "README.md").write_text("# scratch\n")
    return d


# One entry per protected target kind: a plain path from the project root, and the cd-folder/relative-path pair
# an agent would use after `cd`-ing into the owning folder.
def targets(d):
    return [
        ("state.json", ".ratchet/runs/hk/state.json", ".ratchet/runs/hk", "state.json"),
        ("evidence/CP1/x.json", ".ratchet/runs/hk/evidence/CP1/x.json", ".ratchet/runs/hk", "evidence/CP1/x.json"),
        ("human_log.jsonl", ".ratchet/human_log.jsonl", ".ratchet", "human_log.jsonl"),
    ]


def redirect_cases(d, form_label, make_cmd):
    """make_cmd(src, tgt) -> a command string using an absolute `src` and a target path `tgt`. Returns one
    (label, cmd) pair per target kind, for both the plain path and after `cd` into its owning folder."""
    src = str(d / "new.json")
    out = []
    for tname, plain, cd_dir, cd_rel in targets(d):
        out.append((f"{form_label} -> {tname} (plain)", make_cmd(src, plain)))
        out.append((f"{form_label} -> {tname} (after cd)", f"cd {cd_dir} && " + make_cmd(src, cd_rel)))
    return out


CMD_LIVE_A = ('cd .ratchet && wc -l hook-errors.log && ls runs/ && find runs -name stop-blocks.jsonl | xargs '
              'wc -l; tail -c 3000 hook-errors.log')
CMD_LIVE_B = ('cat .ratchet/hook-errors.log | cut -c1-600; for f in .ratchet/runs/*/stop-blocks.jsonl; do '
              'echo "== $f"; wc -l < "$f"; done 2>/dev/null')


# ---- CP4-T01 ------------------------------------------------------------------------------------------------

REDIRECT_FORMS = [
    ("&>", lambda src, tgt: f"jq . {src} &> {tgt}"),
    ("&>>", lambda src, tgt: f"jq . {src} &>> {tgt}"),
    ("2>", lambda src, tgt: f"cat {src} 2> {tgt}"),
    ("2>>", lambda src, tgt: f"cat {src} 2>> {tgt}"),
    ("1>", lambda src, tgt: f"cat {src} 1> {tgt}"),
    ("1>>", lambda src, tgt: f"cat {src} 1>> {tgt}"),
    (">|", lambda src, tgt: f"cat {src} >| {tgt}"),
    (">&", lambda src, tgt: f"cat {src} >& {tgt}"),
]


def t01_fd_numbered_and_combined_redirects_refused():
    env = make_env("claude")
    d = hk_project(env)
    cases = []
    for form_label, make_cmd in REDIRECT_FORMS:
        cases += redirect_cases(d, form_label, make_cmd)
    bad, bad_msg = refuse_table(d, env, cases, OWNS_MSG_RE)
    check("[CP4-T01] every fd-numbered or combined-stream redirect form (&>, &>>, 2>, 2>>, 1>, 1>>, >|, >&) "
          "into a run file is refused (exit 2), plain and after cd, against all three target kinds", bad == [],
          bad)
    check("[CP4-T01] each refusal carries the Ratchet-owns-this-file message", bad_msg == [], bad_msg)


# ---- CP4-T02 ------------------------------------------------------------------------------------------------

def t02_process_substitution_writer_refused():
    env = make_env("claude")
    d = hk_project(env)
    readme = str(d / "README.md")
    cases = redirect_cases(d, "process-subst tee", lambda src, tgt: f"cat {readme} > >(tee {tgt})")
    bad, bad_msg = refuse_table(d, env, cases, OWNS_MSG_RE)
    check("[CP4-T02] `cat ... > >(tee <target>)` onto a run file is refused (exit 2), plain and after cd, "
          "against all three target kinds: a process substitution anywhere makes the command not provably "
          "read-only, and tee inside it is judged as its own writer", bad == [], bad)
    check("[CP4-T02] each refusal carries the Ratchet-owns-this-file message", bad_msg == [], bad_msg)


# ---- CP4-T03 ------------------------------------------------------------------------------------------------

def t03_uniq_dash_stdin_or_second_operand_refused():
    env = make_env("claude")
    d = hk_project(env)
    src = str(d / "new.json")
    cases = (redirect_cases(d, "uniq - (stdin marker)", lambda s, tgt: f"cat {s} | uniq - {tgt}") +
             redirect_cases(d, "uniq (second operand)", lambda s, tgt: f"uniq {s} {tgt}"))
    bad, bad_msg = refuse_table(d, env, cases, OWNS_MSG_RE)
    check("[CP4-T03] `uniq - <target>` and `uniq <src> <target>` onto a run file are refused (exit 2), plain "
          "and after cd, against all three target kinds: uniq's second operand (with or without a leading -) "
          "is a write target, not a second input", bad == [], bad)
    check("[CP4-T03] each refusal carries the Ratchet-owns-this-file message", bad_msg == [], bad_msg)


# ---- CP4-T04 ------------------------------------------------------------------------------------------------

def t04_loop_over_ratchet_listing_writing_via_loop_variable_refused():
    env = make_env("claude")
    d = hk_project(env)
    bad, bad_msg = refuse_table(d, env, [
        ("find | while read, echo > loop var",
         'find .ratchet/runs -name state.json | while read f; do echo x > "$f"; done'),
        ("find | while read, rm loop var",
         'find .ratchet/runs -name state.json | while read f; do rm "$f"; done'),
        ("for-loop glob, echo > loop var",
         'for v in .ratchet/runs/hk/evidence/CP1/*.json; do echo x > "$v"; done'),
    ], OWNS_MSG_RE)
    check("[CP4-T04] a loop over a .ratchet listing whose body writes through its loop variable is refused "
          "(exit 2), whatever the writer (echo redirect, rm) or the loop shape (while read, for)", bad == [],
          bad)
    check("[CP4-T04] each refusal carries the Ratchet-owns-this-file message", bad_msg == [], bad_msg)


# ---- CP4-T05 ------------------------------------------------------------------------------------------------

def t05_director_probe_rows_agree():
    """Copies /tmp/guardlane_director_probe.py's own fixture shape and command dict verbatim (plain redirect,
    quoted redirect target, &>/2>/1> redirects, tee, process-subst tee, uniq stdin operand, cp -rt combined, a
    while-read loop, sed -i, a python write, and the READ cat row), but fires every row through the guard's
    public entry point (`ratchet.py hook guard --host claude`) instead of calling `_guard_shell` directly."""
    import _tmp
    env = make_env("claude")
    d = Path(_tmp.mkdtemp(prefix="ratchet-guard-writes-probe-"))
    git(d, "init", "-q")
    git(d, "config", "user.email", "t@t")
    git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    (d / "README.md").write_text("# scratch\n")
    rd = ".ratchet"
    (d / rd).mkdir(exist_ok=True)
    (d / rd / "runs" / "hk" / "evidence").mkdir(parents=True)
    (d / rd / "runs" / "hk" / "state.json").write_text("{}")
    (d / rd / "active.json").write_text(json.dumps({"slug": "hk"}))
    S = f"{rd}/runs/hk/state.json"
    rows = [
        ("plain redirect", f"echo x > {S}", "refuse"),
        ("quoted redirect target", f"jq . /tmp/s > \"{S}\"", "refuse"),
        ("&> redirect", f"jq . /tmp/new.json &> {S}", "refuse"),
        ("2> redirect", f"cat /tmp/new.json 2> {S}", "refuse"),
        ("1> redirect", f"cat x 1> {S}", "refuse"),
        ("tee", f"cat /tmp/n | tee {S}", "refuse"),
        ("process subst tee", f"cat README.md > >(tee {S})", "refuse"),
        ("uniq stdin operand", f"cat /tmp/new | uniq - {S}", "refuse"),
        ("cp -rt combined", f"cp -rt {rd}/runs/hk /tmp/state.json", "refuse"),
        ("while read loop", f"find {rd}/runs -name state.json | while read f; do echo x > \"$f\"; done",
         "refuse"),
        ("sed -i", f"sed -i s/a/b/ {S}", "refuse"),
        ("python write", f"python3 -c \"open('{S}','w').write('x')\"", "refuse"),
        ("READ cat (should allow)", f"cat {S}", "allow"),
    ]
    bad = []
    for label, cmd, expect in rows:
        r = guard_bash(d, env, cmd)
        got = "refuse" if r.returncode == 2 else ("allow" if r.returncode == 0 else f"exit{r.returncode}")
        if got != expect:
            bad.append((label, expect, got, (r.stderr + r.stdout)[:200]))
    check("[CP4-T05] every row of the director's probe gets the live guard's matching verdict (every writer "
          "row refused, the READ cat row allowed), fired through `ratchet.py hook guard`", bad == [], bad)


# ---- CP4-T06 ------------------------------------------------------------------------------------------------

def t06_codex_list_payload_agrees_on_redirect_write():
    env = make_env("claude")
    d = hk_project(env)
    src = str(d / "new.json")
    cmd = f"jq . {src} &> .ratchet/runs/hk/state.json"
    r_claude = guard_bash(d, env, cmd, host="claude")
    r_codex = guard_bash(d, env, cmd, host="codex")
    check("[CP4-T06] claude's string payload refuses the &> redirect write (exit 2)", r_claude.returncode == 2,
          (r_claude.returncode, r_claude.stderr, r_claude.stdout))
    check("[CP4-T06] codex's Bash-command-as-list payload refuses the identical command the same way (exit 2)",
          r_codex.returncode == 2, (r_codex.returncode, r_codex.stderr, r_codex.stdout))
    check("[CP4-T06] codex's refusal carries the Ratchet-owns-this-file message",
          bool(re.search(OWNS_MSG_RE, r_codex.stderr + r_codex.stdout, re.I)), r_codex.stderr + r_codex.stdout)


# ---- CP4-T07 ------------------------------------------------------------------------------------------------

def t07_stderr_only_and_fd_duplication_on_read_only_command_allowed():
    env = make_env("claude")
    d = hk_project(env)
    bad = allow_table(d, env, [
        ("2>/dev/null", "jq . .ratchet/runs/hk/state.json 2>/dev/null"),
        ("2>/dev/null then 2>&1", "jq . .ratchet/runs/hk/state.json 2>/dev/null 2>&1"),
        ("ending >&2", "jq . .ratchet/runs/hk/state.json >&2"),
    ])
    check("[CP4-T07] stderr-only (2>/dev/null) and fd-duplication (2>&1, >&2) suffixes on an otherwise "
          "read-only command stay allowed (exit 0): none of /dev/null or an fd duplication is a write into "
          ".ratchet/", bad == [], bad)


# ---- CP4-T08 ------------------------------------------------------------------------------------------------

def t08_read_and_redirect_copy_to_tmp_allowed():
    env = make_env("claude")
    d = hk_project(env)
    bad = allow_table(d, env, [
        ("plain path", "cat .ratchet/runs/hk/state.json > /tmp/s.json"),
        ("after cd", "cd .ratchet && cat runs/hk/state.json > /tmp/s.json"),
    ])
    check("[CP4-T08] reading a run file and redirecting the copy to /tmp stays allowed (exit 0), plain path "
          "and after cd into .ratchet/: the redirect's target is outside .ratchet/", bad == [], bad)


# ---- CP4-T09 ------------------------------------------------------------------------------------------------

def t09_uniq_single_run_file_read_allowed():
    env = make_env("claude")
    d = hk_project(env)
    r = guard_bash(d, env, "uniq .ratchet/human_log.jsonl")
    check("[CP4-T09] `uniq .ratchet/human_log.jsonl` (one operand, no -) stays allowed (exit 0): one operand "
          "and no `-` is a read, not a writer", r.returncode == 0, (r.returncode, r.stderr, r.stdout))


# ---- CP4-T10 ------------------------------------------------------------------------------------------------

def t10_oct11_diagnostic_loops_still_allowed():
    env = make_env("claude")
    d = hk_project(env)
    r_a = guard_bash(d, env, CMD_LIVE_A)
    check("[CP4-T10] the Oct 11 orchestrator diagnostic command (a) (cd + xargs wc -l over a find listing) "
          "stays allowed (exit 0)", r_a.returncode == 0, (r_a.returncode, r_a.stderr, r_a.stdout))
    check("[CP4-T10] command (a) carries no RATCHET GUARD denial",
          "RATCHET GUARD" not in (r_a.stderr + r_a.stdout), r_a.stderr + r_a.stdout)
    r_b = guard_bash(d, env, CMD_LIVE_B)
    check("[CP4-T10] the Oct 11 orchestrator diagnostic command (b) (a for-loop whose body only reads through "
          "its loop variable) stays allowed (exit 0)", r_b.returncode == 0, (r_b.returncode, r_b.stderr,
                                                                              r_b.stdout))
    check("[CP4-T10] command (b) carries no RATCHET GUARD denial",
          "RATCHET GUARD" not in (r_b.stderr + r_b.stdout), r_b.stderr + r_b.stdout)


# ---- CP4-T11 ------------------------------------------------------------------------------------------------

def t11_jq_read_with_stderr_silenced_allowed():
    env = make_env("claude")
    d = hk_project(env)
    bad = allow_table(d, env, [
        ("plain path", "jq . .ratchet/runs/hk/state.json 2>/dev/null"),
        ("after cd", "cd .ratchet/runs/hk && jq . state.json 2>/dev/null"),
    ])
    check("[CP4-T11] `jq . <state file> 2>/dev/null` stays allowed (exit 0), plain path and after cd into the "
          "run folder", bad == [], bad)


# ---- CP4-T12 ------------------------------------------------------------------------------------------------

def t12_docs_state_the_redirect_and_loop_rule():
    root = ENGINE.parents[1]  # .../skills/ratchet-orchestrator
    repo_root = ENGINE.parents[3]  # the project root (this worktree)
    readme = (repo_root / "README.md").read_text()
    skill = (root / "SKILL.md").read_text()
    schemas = (root / "references" / "schemas.md").read_text()
    docs = readme + "\n" + skill + "\n" + schemas

    redirect_stmt = re.search(
        r"(&>|>\||process substitution|fd-numbered)[^.\n]{0,250}(write|refus|block)|"
        r"(write|refus|block)[^.\n]{0,250}(&>|>\||process substitution|fd-numbered)",
        docs, re.I)
    check("[CP4-T12] README, SKILL.md or schemas.md state that every output-redirect form (fd-numbered, &>, "
          ">|, >&word, process substitution) targeting a run file is a write and is refused",
          redirect_stmt is not None,
          "no redirect-form-is-a-write sentence found in README/SKILL.md/schemas.md")

    uniq_stmt = re.search(r"uniq[^.\n]{0,200}(second operand|writer|write)", docs, re.I)
    check("[CP4-T12] README, SKILL.md or schemas.md state that uniq's second operand or a bare - makes it a "
          "writer", uniq_stmt is not None,
          "no uniq-second-operand-is-a-writer sentence found in README/SKILL.md/schemas.md")

    loop_stmt = re.search(
        r"loop[^.\n]{0,250}(loop variable|whatever[^.\n]{0,50}variable)[^.\n]{0,150}(refus|block)|"
        r"(refus|block)[^.\n]{0,250}loop[^.\n]{0,150}(loop variable|whatever[^.\n]{0,50}variable)",
        docs, re.I)
    check("[CP4-T12] README, SKILL.md or schemas.md state that a loop over a .ratchet listing whose body "
          "writes through its loop variable is refused whatever the writer or variable name",
          loop_stmt is not None,
          "no loop-over-a-.ratchet-listing-is-refused sentence found in README/SKILL.md/schemas.md")


TESTS = [
    ("CP4-T01", t01_fd_numbered_and_combined_redirects_refused),
    ("CP4-T02", t02_process_substitution_writer_refused),
    ("CP4-T03", t03_uniq_dash_stdin_or_second_operand_refused),
    ("CP4-T04", t04_loop_over_ratchet_listing_writing_via_loop_variable_refused),
    ("CP4-T05", t05_director_probe_rows_agree),
    ("CP4-T06", t06_codex_list_payload_agrees_on_redirect_write),
    ("CP4-T07", t07_stderr_only_and_fd_duplication_on_read_only_command_allowed),
    ("CP4-T08", t08_read_and_redirect_copy_to_tmp_allowed),
    ("CP4-T09", t09_uniq_single_run_file_read_allowed),
    ("CP4-T10", t10_oct11_diagnostic_loops_still_allowed),
    ("CP4-T11", t11_jq_read_with_stderr_silenced_allowed),
    ("CP4-T12", t12_docs_state_the_redirect_and_loop_rule),
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


# ---- CP4-X01 (round-01 fix: loop write destination-vs-source, newline/while-until header shapes) -----------

def x01_loop_write_destination_vs_source_and_header_shapes():
    """[CP4-X01] Round-01 adversarial findings on the loop check: a loop variable used only as a write's
    SOURCE (`cp "$f" /tmp/x`) must stay allowed (X1/X2, codex-standards/codex-correctness); a newline-
    separated `while`/`do`/`done` header, and a `while IFS= read -r VAR` header (A1/B1, host-alt-*), and an
    `until` loop, must all still be caught."""
    env = make_env("claude")
    d = hk_project(env)
    R = ".ratchet"
    run = f"{R}/runs/hk"

    refused_cases = [
        ("find | while IFS= read -r, echo > var",
         f'find {R}/runs -name state.json | while IFS= read -r f; do echo x > "$f"; done'),
        ("find | while read (multi-line header), echo > var",
         f'find {R}/runs -name state.json | while read f\ndo\n  echo x > "$f"\ndone'),
        ("for-loop glob, cp INTO loop var",
         f'for f in {R}/runs/*/state.json; do cp /tmp/new.json "$f"; done'),
        ("ls | while read -r, rm var",
         f'ls {run}/evidence/CP1/* | while read -r f; do rm "$f"; done'),
        ("until loop, echo > literal run path",
         f'until false; do echo x > {run}/state.json; done'),
    ]
    bad, bad_msg = refuse_table(d, env, refused_cases, OWNS_MSG_RE)
    check("[CP4-X01] every loop writing into a run file through a loop variable (or, for the until case, "
          "directly) is still refused (exit 2): a while IFS= read -r header, a newline-separated "
          "while/do/done header, cp writing INTO the loop variable, and an until loop", bad == [], bad)
    check("[CP4-X01] each refusal carries the Ratchet-owns-this-file message", bad_msg == [], bad_msg)

    # Check-in decision (CP4 round 2): any writer program in a loop over run files is refused.
    cp_out_cmd = f'for f in {R}/runs/*/state.json; do cp "$f" /tmp/state-copy.json; done'
    r_cp_out = guard_bash(d, env, cp_out_cmd)
    check("[CP4-X01] a for-loop glob copying a run file OUT via `cp` (loop variable as cp's own SOURCE) is "
          "now refused too (exit 2): any writer program in the body is enough, whatever its arguments",
          r_cp_out.returncode == 2, (r_cp_out.returncode, r_cp_out.stderr, r_cp_out.stdout))
    check("[CP4-X01] its refusal suggests the cat-redirect alternative",
          bool(re.search(r"\bcat\b", r_cp_out.stderr + r_cp_out.stdout)),
          r_cp_out.stderr + r_cp_out.stdout)

    allowed_cases = [
        ("find | while IFS= read -r, jq read-only",
         f'find {R}/runs -name state.json | while IFS= read -r f; do jq .phase "$f"; done'),
        ("Oct 11 orchestrator diagnostic (a)", CMD_LIVE_A),
        ("Oct 11 orchestrator diagnostic (b)", CMD_LIVE_B),
        ("while IFS= read -r, cat loop var to /tmp, list via process substitution",
         f'while IFS= read -r f; do cat "$f" > /tmp/all.txt; done < <(ls {R}/runs/*/state.json)'),
    ]
    bad = allow_table(d, env, allowed_cases)
    check("[CP4-X01] every loop that only reads (jq) or redirects to a literal /tmp target (cat), or carries "
          "no write at all (the two Oct 11 diagnostics), stays allowed (exit 0)", bad == [], bad)


# ---- CP4-X02 (round-01 fix: each doc independently states every write rule) ----------------------------------

REDIRECT_DOC_RE = re.compile(
    r"(&>|>\||fd-numbered)[^.\n]{0,250}(write|refus|block)|"
    r"(write|refus|block)[^.\n]{0,250}(&>|>\||fd-numbered)", re.I)
PROC_SUBST_DOC_RE = re.compile(
    r"process substitution[^.\n]{0,250}(write|refus|block)|"
    r"(write|refus|block)[^.\n]{0,250}process substitution", re.I)
UNIQ_DOC_RE = re.compile(r"uniq[^.\n]{0,200}(second operand|writer|write)", re.I)
LOOP_DOC_RE = re.compile(
    r"loop[^.\n]{0,250}(loop variable|whatever[^.\n]{0,50}variable)[^.\n]{0,150}(refus|block)|"
    r"(refus|block)[^.\n]{0,250}loop[^.\n]{0,150}(loop variable|whatever[^.\n]{0,50}variable)", re.I)


def x02_each_doc_independently_states_every_write_rule():
    """[CP4-X02] Round-01 finding: CP4-T12 concatenates README/SKILL.md/schemas.md and checks each rule once,
    so one document stating a rule lets the other two omit it entirely. This checks each document on its
    own, for the redirect, process-substitution, uniq and loop write rules."""
    root = ENGINE.parents[1]
    repo_root = ENGINE.parents[3]
    docs = {
        "README.md": (repo_root / "README.md").read_text(),
        "SKILL.md": (root / "SKILL.md").read_text(),
        "schemas.md": (root / "references" / "schemas.md").read_text(),
    }
    rules = [("redirect", REDIRECT_DOC_RE), ("process substitution", PROC_SUBST_DOC_RE),
             ("uniq", UNIQ_DOC_RE), ("loop", LOOP_DOC_RE)]
    missing = [f"{doc_name}: {rule_name}" for doc_name, text in docs.items()
               for rule_name, rule_re in rules if not rule_re.search(text)]
    check("[CP4-X02] README.md, SKILL.md and schemas.md each independently state the redirect, process "
          "substitution, uniq and loop write rules", missing == [], missing)


TESTS.append(("CP4-X01", x01_loop_write_destination_vs_source_and_header_shapes))
TESTS.append(("CP4-X02", x02_each_doc_independently_states_every_write_rule))


# ---- CP4-X03 (round-02 fix: every pipeline stage of a loop body, a shared uniq-operand helper, and a
# narrower "inside .ratchet" cd signal for the loop check) ------------------------------------------------------

def x03_loop_pipeline_stage_uniq_redirect_and_narrow_cd_inside():
    """[CP4-X03] Round-02 adversarial findings on the loop check: every pipeline stage of a loop body must be
    checked, not just the first (A1/C2, host-alt-correctness/host-alt-standards -- `cat x | tee "$f"` was
    missed since only `cat`, the first word, was looked at); uniq's own operand count must share the fast
    path's bare-`-` fix and strip a redirect sharing the same statement before counting (X2/A2/C3, codex-*/
    host-alt-standards -- `uniq - "$f" < /tmp/new.json` miscounted `<` as uniq's second operand); and "inside
    .ratchet" for a loop must mean only the session's own cwd, a literal cd/pushd target naming .ratchet, or
    an unresolved cd/pushd target -- never any cd this fast path merely can't prove absolute-safe, which is
    also true of an ordinary relative `cd` to an unrelated folder (X1/A3/C1, codex-standards/host-alt-*)."""
    env = make_env("claude")
    d = hk_project(env)
    R = ".ratchet"
    run = f"{R}/runs/hk"

    refused_cases = [
        ("find | while read, cat piped to tee on loop var",
         f'find {R}/runs -name state.json | while read f; do cat /tmp/new.json | tee "$f"; done'),
        ("find | while read, uniq - loop var with redirected stdin",
         f'find {R}/runs -name state.json | while read f; do uniq - "$f" < /tmp/new.json; done'),
        ("cd into run folder, for-loop glob, echo > loop var",
         f'cd {run} && for f in *.json; do echo x > "$f"; done'),
    ]
    bad, bad_msg = refuse_table(d, env, refused_cases, OWNS_MSG_RE)
    check("[CP4-X03] a piped writer stage, a redirect-sharing uniq write, and a cd-into-.ratchet loop are "
          "all still refused (exit 2)", bad == [], bad)
    check("[CP4-X03] each refusal carries the Ratchet-owns-this-file message", bad_msg == [], bad_msg)

    allowed_cases = [
        ("cd into an unrelated project folder, for-loop append",
         "cd docs && for f in *.md; do printf '\\n' >> \"$f\"; done"),
        ("find | while read, cat piped to jq (read-only pipeline)",
         f'find {R}/runs -name state.json | while read f; do cat "$f" | jq .phase; done'),
        ("uniq a run file alone (no loop)", f"uniq {R}/hook-errors.log"),
    ]
    bad = allow_table(d, env, allowed_cases)
    check("[CP4-X03] a loop reached through a cd into an ordinary project folder, a read-only cat|jq "
          "pipeline over a run listing, and a plain uniq on a run file all stay allowed (exit 0)",
          bad == [], bad)


TESTS.append(("CP4-X03", x03_loop_pipeline_stage_uniq_redirect_and_narrow_cd_inside))


# ---- CP4-X04 (round-02 check-in SIMPLIFY decision: any writer program or redirect in a loop body over run
# files is refused, with no per-argument source/destination analysis any more) --------------------------------

def x04_any_writer_in_loop_body_refused_whatever_its_arguments():
    """[CP4-X04] Check-in decision (CP4 round 2): a loop proven to read run files is refused the moment any
    stage of its body writes anything, whatever the writer program's own arguments -- sponge and uniq behind
    a pipe, sed --in-place, awk -i, and cp with unrelated flags/operands are all refused; a literal-target
    redirect (cat/jq into /tmp) and the already-allowed diagnostics still pass."""
    env = make_env("claude")
    d = hk_project(env)
    R = ".ratchet"
    run = f"{R}/runs/hk"

    refused_cases = [
        ("find | while read, jq piped to sponge on loop var",
         f'find {R}/runs -name state.json | while read f; do jq . /tmp/new.json | sponge "$f"; done'),
        ("for-loop glob, sed --in-place",
         f'for f in {R}/runs/*/state.json; do sed --in-place s/a/b/ "$f"; done'),
        ("for-loop glob, awk -i inplace",
         f'for f in {R}/runs/*/state.json; do awk -i inplace \'{{print}}\' "$f"; done'),
        ("ls | while read, cp -t with unrelated flags",
         f'ls {run}/evidence/CP1/* | while read f; do cp -t {run} -- "$f"; done'),
        ("for-loop glob, echo > loop var",
         f'for f in {R}/runs/*/state.json; do echo x > "$f"; done'),
    ]
    bad, bad_msg = refuse_table(d, env, refused_cases, OWNS_MSG_RE)
    check("[CP4-X04] a piped sponge write, sed --in-place, awk -i, cp with unrelated flags, and a plain echo "
          "redirect are all refused (exit 2) in a loop whose input names a run file", bad == [], bad)
    check("[CP4-X04] each refusal carries the Ratchet-owns-this-file message", bad_msg == [], bad_msg)

    allowed_cases = [
        ("for-loop glob, cat loop var to literal /tmp target",
         f'for f in {R}/runs/*/state.json; do cat "$f" > /tmp/copy.json; done'),
        ("for-loop glob, jq append to literal /tmp target",
         f'for f in {R}/runs/*/state.json; do jq .phase "$f" >> /tmp/summary.txt; done'),
        ("Oct 11 orchestrator diagnostic (a)", CMD_LIVE_A),
        ("Oct 11 orchestrator diagnostic (b)", CMD_LIVE_B),
        ("cd into an unrelated project folder, for-loop append",
         "cd docs && for f in *.md; do printf '\\n' >> \"$f\"; done"),
        ("find | while read, cat piped to jq (read-only pipeline)",
         f'find {R}/runs -name state.json | while read f; do cat "$f" | jq .phase; done'),
    ]
    bad = allow_table(d, env, allowed_cases)
    check("[CP4-X04] a literal-/tmp-target cat/jq redirect, the two Oct 11 diagnostics, a loop reached "
          "through a cd into an ordinary folder, and a read-only cat|jq pipeline all stay allowed (exit 0)",
          bad == [], bad)


TESTS.append(("CP4-X04", x04_any_writer_in_loop_body_refused_whatever_its_arguments))


# ---- CP4-X05 (round-03 fixes: quote-mask the loop body's own redirect scan, and treat any `>(` outside
# quotes in a loop body stage as a write) -------------------------------------------------------------------

def x05_loop_body_quote_mask_and_proc_subst():
    """[CP4-X05] round-03 A1: a `>(...)` process substitution hidden inside a loop body stage (`tee`/`dd`)
    is refused even though `_redirect_targets` never sees its own target and the outer program (`cat`) isn't
    a writer program. round-03 X1/A1: a quoted `>` inside a loop body's own jq/grep text (a literal
    comparison or a literal 'a>b' pattern) is no longer mistaken for a real redirect and must stay allowed,
    including when a genuine, unrelated literal-target redirect sits later in the same stage."""
    env = make_env("claude")
    d = hk_project(env)
    R = ".ratchet"

    refused_cases = [
        ("find | while read, loop var fed into a tee process substitution",
         f'find {R}/runs -name state.json | while read f; do cat /tmp/new.json > >(tee "$f"); done'),
        ("find | while read, loop var fed into a dd process substitution",
         f'find {R}/runs -name state.json | while read f; do cat /tmp/new.json > >(dd of="$f"); done'),
    ]
    bad, bad_msg = refuse_table(d, env, refused_cases, OWNS_MSG_RE)
    check("[CP4-X05] a loop body stage hiding a tee or dd writer inside a `>(...)` process substitution is "
          "refused (exit 2) even though the outer program is a plain reader", bad == [], bad)
    check("[CP4-X05] each refusal carries the Ratchet-owns-this-file message", bad_msg == [], bad_msg)

    allowed_cases = [
        ("for-loop glob, jq filter with a quoted numeric comparison",
         f"for f in {R}/runs/*/state.json; do jq '.count > 0' \"$f\"; done"),
        ("for-loop glob, grep for a quoted literal containing '=>'",
         f"for f in {R}/runs/*/state.json; do grep -c 'a=>b' \"$f\"; done"),
        ("for-loop glob, quoted jq filter alongside a real literal-/tmp-target append",
         f"for f in {R}/runs/*/state.json; do jq 'select(.n > 1)' \"$f\" >> /tmp/summary.txt; done"),
    ]
    bad = allow_table(d, env, allowed_cases)
    check("[CP4-X05] a quoted '>' inside a loop body's own jq filter or grep pattern, alone or alongside a "
          "genuine literal-/tmp-target redirect, never counts as a write (exit 0)", bad == [], bad)


TESTS.append(("CP4-X05", x05_loop_body_quote_mask_and_proc_subst))


# ---- CP4-X06 (round-04 fixes: quote-masked loop-end `done`, a trailing-pipe newline into a loop header, the
# `(cd ...)` subshell idiom, quote-masked process substitution scanning outside loops, and heredoc-as-code
# scoped to the heredoc's own feeding program) ----------------------------------------------------------------

def x06_round04_quoted_done_subshell_cd_and_heredoc_scope():
    """[CP4-X06] round-04 A1: a `done` sitting inside a loop body's own quoted jq filter or echoed string no
    longer ends the body early, so the real write that follows it is still refused. round-04 A2: a trailing
    `|` followed by a newline before `while` still counts as the pipe-input the loop check looks for. round-04
    A3: the `(cd DIR && ...)` subshell idiom is tracked by every cd/pushd check, so a write or delete inside
    it is still refused, as on main. round-04 minor A4: a `>|` clobber redirect in a loop body isn't split at
    its own `|`. round-04 host-alt-standards A1 and host-alt-correctness A1: a `>(...)` example quoted in a
    plain note, and a heredoc fed to a non-interpreter program (`cat`) whose body merely mentions a run path
    and an interpreter word in prose, both stay allowed; a heredoc genuinely fed to `bash` with a real write
    in its body stays refused."""
    env = make_env("claude")
    d = hk_project(env)
    R = ".ratchet"

    refused_cases = [
        ("find | while read, jq rewriting the phase to the quoted word done",
         f'find {R}/runs -name state.json | while read f; do jq \'.phase="done"\' "$f" > /tmp/t && '
         f'mv /tmp/t "$f"; done'),
        ("find | while read, jq status to done piped to sponge",
         f'find {R}/runs -name state.json | while read f; do jq \'.checkpoints.CP1.status="done"\' "$f" | '
         f'sponge "$f"; done'),
        ("find | while read, echo a quoted \"all done\" over the loop var",
         f'find {R}/runs -name state.json | while read f; do echo "all done" > "$f"; done'),
        ("find piped to a while header on the next line, echo > loop var",
         f'find {R}/runs -name state.json |\nwhile read f; do\n  echo x > "$f"\ndone'),
        ("subshell cd into the run folder, echo > state.json",
         f'(cd {R}/runs/hk && echo x > state.json)'),
        ("subshell cd into the run folder, jq stderr redirected to state.json",
         f'(cd {R}/runs/hk && jq . /tmp/new.json 2> state.json)'),
        ("subshell cd into the run folder, jq rewrite through mv",
         f'(cd {R}/runs/hk && jq \'.phase="done"\' state.json > /tmp/t && mv /tmp/t state.json)'),
        ("subshell cd into the run folder, rm the human log",
         f'(cd {R}/runs/hk && rm human_log.jsonl)'),
        ("for-loop glob, a clobber redirect over the loop var",
         f'for f in {R}/runs/*/state.json; do echo x >| "$f"; done'),
    ]
    bad, bad_msg = refuse_table(d, env, refused_cases, OWNS_MSG_RE)
    check("[CP4-X06] a quoted loop-ending `done`, a trailing-pipe newline before `while`, every `(cd ...)` "
          "subshell write/delete form, and a `>|` clobber redirect in a loop body are all still refused "
          "(exit 2)", bad == [], bad)
    check("[CP4-X06] each refusal carries the Ratchet-owns-this-file message", bad_msg == [], bad_msg)

    allowed_cases = [
        ("a note quoting a >(...) example, writing only to /tmp",
         "echo 'example: cat x > >(tee .ratchet/runs/hk/state.json) is refused' > /tmp/cp4x06-note.txt"),
        ("a heredoc fed to cat whose body mentions a run path and the word sh in prose",
         "cat > /tmp/cp4x06-note2.txt << 'EOF'\nthis mentions .ratchet/runs/hk/state.json and sh somewhere "
         "in prose\nEOF"),
        ("a heredoc brief fed to cat whose body mentions bash and a run path in prose",
         "cat > /tmp/cp4x06-brief.md << 'EOF'\nRun it with bash; never edit .ratchet/runs/x/state.json\nEOF"),
    ]
    bad = allow_table(d, env, allowed_cases)
    check("[CP4-X06] a quoted process-substitution example and a heredoc fed to a plain reader whose body "
          "only mentions a run path and an interpreter word in prose both stay allowed (exit 0)", bad == [],
          bad)

    r = guard_bash(d, env, "bash << 'EOF'\necho x > .ratchet/runs/hk/state.json\nEOF")
    check("[CP4-X06] a heredoc genuinely fed to bash with a real write to a run file in its body is still "
          "refused (exit 2)", r.returncode == 2, (r.returncode, r.stderr, r.stdout))


TESTS.append(("CP4-X06", x06_round04_quoted_done_subshell_cd_and_heredoc_scope))


# ---- CP4-X07 (round-05 fixes: a loop check that gives up on a live substitution must fail closed, not
# allow; LOOP_HEAD_RE must match on quote-masked text, not raw text; a backslash-escaped separator inside
# double quotes must stay masked; a heredoc piped onward into an interpreter is still code) -----------------

def x07_round05_loop_fail_closed_masked_loop_head_escaped_pipe_and_piped_heredoc():
    """[CP4-X07] round-05 A1 (host-correctness/host-alt-correctness): a while-read loop over a .ratchet
    listing whose body holds a live `$(...)` substitution anywhere (the standard
    `tmp="$(mktemp)"; jq ... "$f" > "$tmp" && mv "$tmp" "$f"` in-place edit, or a `cp`/`echo "$(date)"` pair)
    is still refused, not let through just because the precise quote mask gave up. round-05 A2
    (host-alt-correctness): a `while ... do ... done` shape sitting inside a quoted echo argument (plain
    documentation text) is not mistaken for a real loop and stays allowed. round-05 B1 (host-alt-standards):
    a backslash-escaped `|` inside a double-quoted grep/sed alternation pattern that also happens to mention
    a `.ratchet` path in its own text stays masked, so the command is judged read-only. round-05 B2/S1
    (host-alt-standards/host-standards): a heredoc piped onward into an interpreter after the heredoc
    operator (`cat <<'EOF' | python3`, `cat <<'EOF' | bash`) is still treated as code and refused when its
    body writes a run file."""
    env = make_env("claude")
    d = hk_project(env)
    R = ".ratchet"

    refused_cases = [
        ("while-read loop, in-place jq edit via tmp=\"$(mktemp)\" and mv",
         f'ls {R}/runs/*/state.json | while read f; do tmp="$(mktemp)"; jq \'.phase="done"\' "$f" > '
         f'"$tmp" && mv "$tmp" "$f"; done'),
        ("while-read loop, cp overwrite followed by an unrelated echo \"$(date)\" statement",
         f'find {R}/runs -name state.json | while read f; do cp /tmp/new.json "$f"; echo "done $(date)"; '
         f'done'),
        ("heredoc piped into python3, writing a run file in its body",
         f"cat <<'EOF' | python3\nopen('{R}/runs/hk/state.json', 'w').write('{{}}')\nEOF"),
        ("heredoc piped into bash, writing a run file in its body",
         f"cat <<'EOF' | bash\necho x > {R}/runs/hk/state.json\nEOF"),
        # Director decision (CP4 round 6): a command naming the run folder with $( or a backtick is refused.
        ("a while-read loop that only reads, with a $(...) substitution in its own read-only body",
         f'ls {R}/runs/*/state.json | while read f; do echo "== $(basename "$f")"; wc -l < "$f"; done'),
    ]
    bad, bad_msg = refuse_table(d, env, refused_cases, OWNS_MSG_RE)
    check("[CP4-X07] a loop body with a live $(...) substitution that writes a run file, and a heredoc piped "
          "onward into python3/bash that writes a run file, are all still refused (exit 2)", bad == [], bad)
    check("[CP4-X07] each refusal carries the Ratchet-owns-this-file message", bad_msg == [], bad_msg)

    allowed_cases = [
        ("a note quoting a whole while-read loop example, writing only to /tmp",
         f"echo 'example refused: find {R}/runs -name state.json | while read f; do echo x > \"$f\"; done' "
         f"> /tmp/cp4x07-note.txt"),
        ("a double-quoted grep alternation with an escaped pipe, reading a non-run file",
         'grep -n "keep the body read-only\\|literal absolute path outside .ratchet" README.md'),
        ("a heredoc fed to cat (not piped onward) whose body mentions python3 and a run path in prose",
         f"cat <<'EOF' > /tmp/brief.md\nrun it with python3; never edit {R}/runs/x/state.json\nEOF"),
    ]
    bad = allow_table(d, env, allowed_cases)
    check("[CP4-X07] a quoted loop-shape note, a double-quoted grep with an escaped-pipe alternation, and a "
          "heredoc fed to cat that only mentions an interpreter in prose all stay allowed (exit 0)", bad == [],
          bad)


TESTS.append(("CP4-X07", x07_round05_loop_fail_closed_masked_loop_head_escaped_pipe_and_piped_heredoc))


# ---- CP4-X08 (round-06 SIMPLIFY fix: delete the raw-text loop-write fallback; fall back to a coarse quote
# mask instead, on the SAME loop checker, so a live $(...)/backtick substitution elsewhere in a loop never
# hides a uniq/sed-i/awk-i/python write, and never turns a quoted writer-program word into a false refusal)

def x08_round06_coarse_mask_replaces_raw_text_loop_fallback():
    """[CP4-X08] round-06 A1 (host-alt-correctness): a loop whose body writes through `uniq`, `sed -i`,
    `awk -i` or a python write, paired with an unrelated live `$(date)`/`$(echo ...)` substitution elsewhere
    in the same body, is refused -- the old raw-text fallback only recognised LOOP_WRITER_PROGS/redirects and
    missed all four of these. round-06 C1/X1/X2 (host-alt-standards/codex-standards/codex-correctness): a
    loop with a live substitution whose body only quotes a short writer-program-shaped word (as grep/echo
    DATA, not a command) stays allowed; the old fallback scanned raw, unmasked text and refused on the
    coincidence."""
    env = make_env("claude")
    d = hk_project(env)
    R = ".ratchet"

    refused_cases = [
        ("while-read loop, uniq overwrite paired with an unrelated echo \"$(date)\" stamp",
         f'find {R}/runs -name state.json | while read f; do uniq - "$f"; echo "stamp $(date)"; done'),
        ("while-read loop, sed -i edit whose own substitution is the in-place script argument",
         f'find {R}/runs -name state.json | while read f; do sed -i "s/a/$(echo b)/" "$f"; done'),
        ("while-read loop, awk -i inplace edit paired with an unrelated echo \"$(date)\" stamp",
         f'find {R}/runs -name state.json | while read f; do awk -i inplace \'{{print}}\' "$f"; '
         f'echo "$(date)"; done'),
        ("while-read loop, a python one-liner opening the run file for write, paired with an unrelated "
         "echo \"$(date)\" stamp",
         f'find {R}/runs -name state.json | while read f; do python3 -c "open(\\"$f\\", \'w\').write(\'x\')"; '
         f'echo "$(date)"; done'),
        ("for-loop glob, cp overwrite paired with an unrelated echo \"done $(date)\" statement",
         f"find {R}/runs -name '*.json' | while read f; do cp \"$f\" /tmp/backup/; echo \"done $(date)\"; done"),
        # Director decision (CP4 round 6): a command naming the run folder with $( or a backtick is refused.
        ("for-loop over run files, echo with a live basename substitution, grep for a quoted 'rm' pattern",
         f'for f in {R}/runs/*/stop-blocks.jsonl; do echo "== $(basename "$f")"; grep -n \'rm\' "$f"; done'),
        # Director decision (CP4 round 6): a command naming the run folder with $( or a backtick is refused.
        ("while-read loop, echo with a live basename substitution, grep for a quoted 'cp' pattern",
         f'find {R}/runs -name state.json | while read f; do echo "== $(basename "$f")"; '
         f'grep -n \'cp\' "$f"; done'),
    ]
    bad, bad_msg = refuse_table(d, env, refused_cases, OWNS_MSG_RE)
    check("[CP4-X08] a loop body writing through uniq, sed -i, awk -i, or a python write, each paired with "
          "an unrelated live $(...) substitution elsewhere in the same body, is still refused (exit 2)",
          bad == [], bad)
    check("[CP4-X08] each refusal carries the Ratchet-owns-this-file message", bad_msg == [], bad_msg)
    # Director decision (CP4 round 6): both of this test's former allowed_cases named the run folder and
    # carried a $(...) substitution, so they moved into refused_cases above; no allowed cases remain here.


TESTS.append(("CP4-X08", x08_round06_coarse_mask_replaces_raw_text_loop_fallback))


# ---- CP4-X09 (round-06 fix: a command naming the run folder with a hidden command substitution inside a
# quoted "$(...)" is refused outright, with no parsing, instead of being judged read-only or allowed through
# a loop body) -------------------------------------------------------------------------------------------

def x09_round06_quoted_substitution_naming_run_folder_refused():
    """[CP4-X09] Director decision (CP4 round 6): a command that names Ratchet's run folder (a literal
    `.ratchet` path segment anywhere in the raw command text) and contains `$(` or a backtick anywhere,
    quoted or not, is refused outright, with a message naming the substitution rule, even when the
    substitution sits inside a loop body's echo/cat/jq argument that would otherwise look read-only. A
    command with no `.ratchet` mention at all, or one with a mention but no substitution, is unaffected."""
    env = make_env("claude")
    d = hk_project(env)
    R = ".ratchet"

    refused_cases = [
        ("for-loop glob, cp hidden inside a quoted echo $(...) substitution",
         f'for f in {R}/runs/*/state.json; do echo "$(cp /tmp/x "$f")"; done'),
        ("while-read loop, rm hidden inside a quoted echo $(...) substitution",
         f'ls {R}/runs/*/state.json | while read f; do echo "x $(rm -f "$f")"; done'),
        ("cat piped into jq whose filter argument is itself a $(...) substitution",
         f'cat {R}/runs/hk/state.json | jq "$(echo .phase)"'),
    ]
    bad, bad_msg = refuse_table(d, env, refused_cases, r"command substitution")
    check("[CP4-X09] a command naming the run folder with a hidden cp/rm/jq-filter $(...) substitution "
          "anywhere is refused (exit 2), whether in a loop body or a plain pipeline", bad == [], bad)
    check("[CP4-X09] each refusal's message names the substitution rule", bad_msg == [], bad_msg)

    allowed_cases = [
        ("plain cat read of a run file, no substitution",
         f"cat {R}/runs/hk/state.json"),
        ("jq read of a run file, no substitution",
         f"jq .phase {R}/runs/hk/state.json"),
        ("for-loop glob, plain jq read through the loop variable, no substitution",
         f'for f in {R}/runs/*/state.json; do jq .phase "$f"; done'),
        ("a $(...) substitution with no .ratchet mention anywhere",
         'echo "$(date)" > /tmp/stamp.txt'),
    ]
    bad = allow_table(d, env, allowed_cases)
    check("[CP4-X09] a run-file read with no substitution, a loop reading a run file through its loop "
          "variable with no substitution, and a $(...) substitution naming no .ratchet path all stay "
          "allowed (exit 0)", bad == [], bad)


TESTS.append(("CP4-X09", x09_round06_quoted_substitution_naming_run_folder_refused))


# ---- CP4-X10 (round-07 fix: a `$(`/backtick inside a single-quoted span, or inside a quoted-delimiter
# heredoc body, does not count as a live substitution; one that is unquoted, double-quoted, or inside an
# unquoted-delimiter heredoc body still does) -------------------------------------------------------------

def x10_round07_inert_quoted_substitution_text_allowed():
    """[CP4-X10] Director decision (CP4 round 7, Codex correctness and standards findings): bash never
    expands a `$(`/backtick that sits inside a single-quoted span or inside the body of a heredoc whose own
    delimiter is quoted (`<<'EOF'`, `<<"EOF"`). A Markdown brief, a quoted heredoc note, or a single-quoted
    commit message that merely mentions a run path in backticks must stay allowed. A `$(`/backtick that is
    unquoted, sits inside double quotes, or sits inside an UNQUOTED-delimiter heredoc body still counts and
    stays refused."""
    env = make_env("claude")
    d = hk_project(env)
    R = ".ratchet"

    allowed_cases = [
        ("single-quote-delimited heredoc body with a Markdown-backticked run path",
         f"cat > /tmp/brief.md <<'EOF'\nRead `{R}/runs/hk/state.json` before continuing.\nEOF"),
        ("double-quote-delimited heredoc body with a Markdown-backticked run path",
         f'cat > /tmp/brief.md <<"EOF"\nRead `{R}/runs/hk/state.json` before continuing.\nEOF'),
        ("single-quoted git commit message with a backticked run path",
         f"git commit -m 'note: read `{R}/runs/hk/state.json` first'"),
        ("single-quoted echo text with a backticked run path, redirected outside .ratchet/",
         f"echo 'see `{R}/runs/hk/state.json`' > /tmp/n.md"),
    ]
    bad = allow_table(d, env, allowed_cases)
    check("[CP4-X10] a Markdown-backticked run path inside a quoted-delimiter heredoc body, or inside a "
          "single-quoted commit message or echo argument, stays allowed (exit 0): bash never expands it",
          bad == [], bad)

    refused_cases = [
        ("for-loop glob, cp hidden inside a quoted echo $(...) substitution",
         f'for f in {R}/runs/*/state.json; do echo "$(cp /tmp/x "$f")"; done'),
        ("cat piped into jq whose filter argument is itself a double-quoted $(...) substitution",
         f'cat {R}/runs/hk/state.json | jq "$(echo .phase)"'),
        ("unquoted-delimiter heredoc body with a live $(...) substitution",
         f"cat > /tmp/b.md <<EOF\n$(rm {R}/runs/hk/state.json)\nEOF"),
        ("bare, unquoted backticks around a run-file read",
         f"echo `cat {R}/runs/hk/state.json`"),
    ]
    bad, bad_msg = refuse_table(d, env, refused_cases, r"command substitution")
    check("[CP4-X10] a double-quoted $(...) substitution, an unquoted-delimiter heredoc body's live "
          "substitution, and bare unquoted backticks around a run-file read, each naming the run folder, "
          "all stay refused (exit 2)", bad == [], bad)
    check("[CP4-X10] each refusal's message names the substitution rule", bad_msg == [], bad_msg)


TESTS.append(("CP4-X10", x10_round07_inert_quoted_substitution_text_allowed))


if __name__ == "__main__":
    main()
