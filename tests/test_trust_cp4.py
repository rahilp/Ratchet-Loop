#!/usr/bin/env python3
"""
CP4 of the trust-holes run: agent-set overrides no longer bypass checks.
Moved verbatim from the old tests/test_trust.py; shared helpers live in tests/trust_helpers.py.

    python3 tests/test_trust_cp4.py
"""
import json
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

import _tmp
import trust_helpers
from trust_helpers import (
    ENGINE, HAVE_PTY, check, cp3_state, cp4_answer_correct, cp4_awaiting_review, cp4_find_code, cp4_human_log_lines,
    cp4_pty_run, cp4_say_yes, cp4_unapproved_plan, cp4x_guard_tool, engine, engine_env, errors_path, fire_guard,
    install, js, make_env, now_dt, project, prompt_payload, rx, stop_payload,
)

SECTIONS = []


# ---- CP4 ----
# Agent-set overrides no longer bypass checks. RATCHET_HUMAN_OVERRIDE only works through a real controlling
# terminal where the person types back a short code Ratchet prints; RATCHET_ROLE / RATCHET_HOME /
# RATCHET_DIRECTOR_KEY_FILE cannot be smuggled past the guard by indirect shell tricks (declare -x, typeset, an
# indirect eval, or a python3 -c os.environ assignment); and even a value that slipped past the guard entirely must
# still be ignored by the engine itself. Pty-driven cases use Python's own os.forkpty (never the test runner's own
# tty, which is not a real terminal under CI) and skip, with a clear "not run" line, only on platforms with no pty
# (Windows) - they never silently pass.





def cp4_wrong_code(code):
    return code[:-1] + ("1" if code[-1] != "1" else "2")






def cp4_answer_wrong(master, output):
    code = cp4_find_code(output)
    if not code:
        return False
    os.write(master, (cp4_wrong_code(code) + "\n").encode())
    return True


def cp4_close_after_code(master, output):
    return "close" if cp4_find_code(output) else False




def cp4_tamper_fixture(env):
    """state.json hand-edited outside ratchet.py: the fixture accept-state exists for."""
    d = project(env)
    sp = Path(d) / ".ratchet/runs/f/state.json"
    st = json.loads(sp.read_text())
    st["tampered_marker"] = "edited by hand, not by ratchet.py"
    sp.write_text(json.dumps(st))
    return d






def cp4_guard_bash(d, env, command):
    payload = {"session_id": "s", "cwd": str(d), "hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": command}}
    return rx(d, env, "hook", "guard", "--host", "claude", stdin=json.dumps(payload))




def cp4_override_needs_terminal():
    print("CP4: RATCHET_HUMAN_OVERRIDE only works through a real terminal, with the printed code typed back")

    # --- T01: no tty at all (plain pipes, as an agent's own Bash tool call would be): the bare override approves
    # and accepts nothing, for both human-approve and accept-state -------------------------------------------------
    env = make_env("claude")
    d = cp4_awaiting_review(env)
    phase_before = cp3_state(d)["phase"]
    r = rx(d, dict(env, RATCHET_HUMAN_OVERRIDE="1"), "human-approve")
    phase_after = cp3_state(d)["phase"]
    check("[CP4-T01] human-approve: the bare override with no tty is refused and approves nothing",
          r.returncode != 0 and phase_after == phase_before
          and re.search(r"person.*(own )?terminal|terminal.*person", r.stderr + r.stdout, re.I) is not None,
          (r.returncode, r.stderr, phase_before, phase_after))

    env2 = make_env("claude")
    d2 = cp4_tamper_fixture(env2)
    r2 = rx(d2, dict(env2, RATCHET_HUMAN_OVERRIDE="1"), "accept-state")
    check("[CP4-T01] accept-state: the bare override with no tty is refused, nothing accepted as a new baseline",
          r2.returncode != 0
          and re.search(r"person.*(own )?terminal|terminal.*person", r2.stderr + r2.stdout, re.I) is not None,
          (r2.returncode, r2.stderr))
    r3 = rx(d2, env2, "status")
    check("[CP4-T01] accept-state: the hand-edited state is still refused afterwards (never silently accepted)",
          r3.returncode != 0, (r3.returncode, r3.stderr))

    # --- T02: a real pty, the exact printed code typed back: approves, records the override, and advances ----------
    if not HAVE_PTY:
        print("  - [CP4-T02] not run: no pty on this platform (Windows)")
    else:
        env = make_env("claude")
        d = cp4_awaiting_review(env)
        phase_before = cp3_state(d)["phase"]
        rc, out = cp4_pty_run(["human-approve"], d, dict(env, RATCHET_HUMAN_OVERRIDE="1"), cp4_answer_correct)
        st = cp3_state(d)
        last_log = cp4_human_log_lines(d)[-1] if cp4_human_log_lines(d) else {}
        check("[CP4-T02] typing back the exact printed code approves, advances past the pause, and the record "
              "notes the override",
              rc == 0 and cp4_find_code(out) is not None and st["phase"] != phase_before
              and last_log.get("by") == "human" and "override" in json.dumps(last_log).lower(),
              (rc, out[-300:], phase_before, st.get("phase"), last_log))

    # --- T03: typing back a wrong code rejects the override: nothing approved, still waiting ------------------------
    if not HAVE_PTY:
        print("  - [CP4-T03] not run: no pty on this platform (Windows)")
    else:
        env = make_env("claude")
        d = cp4_awaiting_review(env)
        phase_before = cp3_state(d)["phase"]
        rc, out = cp4_pty_run(["human-approve"], d, dict(env, RATCHET_HUMAN_OVERRIDE="1"), cp4_answer_wrong)
        st = cp3_state(d)
        check("[CP4-T03] a wrong code dies with a plain message; the run is still waiting, nothing approved",
              rc != 0 and cp4_find_code(out) is not None and st["phase"] == phase_before
              and "Traceback" not in out, (rc, out[-300:], phase_before, st.get("phase")))

    # --- T04: the terminal goes away (EOF) before any reply: never falls back to the bare env var as consent --------
    if not HAVE_PTY:
        print("  - [CP4-T04] not run: no pty on this platform (Windows)")
    else:
        env = make_env("claude")
        d = cp4_awaiting_review(env)
        phase_before = cp3_state(d)["phase"]
        rc, out = cp4_pty_run(["human-approve"], d, dict(env, RATCHET_HUMAN_OVERRIDE="1"), cp4_close_after_code)
        st = cp3_state(d)
        check("[CP4-T04] EOF before any reply approves nothing; the bare env var alone is never treated as consent",
              rc != 0 and st["phase"] == phase_before, (rc, out[-300:], phase_before, st.get("phase")))


SECTIONS.append(("CP4", cp4_override_needs_terminal))


def cp4_guard_indirect_env_tricks():
    print("CP4: the guard blocks indirect shell tricks that set a reserved env var, not just a literal VAR=")

    def table(commands, msg_re):
        env = make_env("claude")
        d = project(env, approved=False)
        bad, bad_msg = [], []
        for label, cmd in commands:
            r = cp4_guard_bash(d, env, cmd)
            if r.returncode != 2:
                bad.append((label, r.returncode, r.stderr))
            elif not re.search(msg_re, r.stderr + r.stdout, re.I):
                bad_msg.append((label, r.stderr))
        return bad, bad_msg

    # --- T05: RATCHET_HUMAN_OVERRIDE ---------------------------------------------------------------------------------
    bad, bad_msg = table([
        ("declare -x", "declare -x RATCHET_HUMAN_OVERRIDE=1; ratchet.py human-approve"),
        ("typeset", "typeset RATCHET_HUMAN_OVERRIDE=1; ratchet.py human-approve"),
        ("eval indirect", 'V=RATCHET_HUMAN_OVERRIDE; eval "$V=1 ratchet.py human-approve"'),
        ("python3 os.environ", "python3 -c \"import os,subprocess; os.environ['RATCHET_HUMAN_OVERRIDE']='1'; "
                               "subprocess.run(['python3','ratchet.py','human-approve'])\""),
    ], r"override.*(person|terminal)|terminal.*person")
    check("[CP4-T05] every indirect way of setting RATCHET_HUMAN_OVERRIDE is refused by the guard (exit 2)",
          bad == [], bad)
    check("[CP4-T05] each refusal names the override as reserved for a person at their own terminal",
          bad_msg == [], bad_msg)

    # --- T06: RATCHET_ROLE ----------------------------------------------------------------------------------------
    bad, bad_msg = table([
        ("declare -x", "declare -x RATCHET_ROLE=director; ratchet.py director-key"),
        ("typeset", "typeset RATCHET_ROLE=director; ratchet.py director-sign approve-plan"),
        ("eval indirect", 'V=RATCHET_ROLE; eval "$V=director ratchet.py director-key"'),
        ("python3 os.environ", "python3 -c \"import os,subprocess; os.environ['RATCHET_ROLE']='director'; "
                               "subprocess.run(['python3','ratchet.py','director-key'])\""),
    ], r"director session|RATCHET_ROLE")
    check("[CP4-T06] every indirect way of setting RATCHET_ROLE is refused by the guard (exit 2)", bad == [], bad)
    check("[CP4-T06] each refusal names RATCHET_ROLE/director commands as reserved for the director session",
          bad_msg == [], bad_msg)

    # --- T07: RATCHET_HOME -----------------------------------------------------------------------------------------
    bad, bad_msg = table([
        ("declare -x", "declare -x RATCHET_HOME=./evil-home; ratchet.py status"),
        ("typeset", "typeset RATCHET_HOME=./evil-home; ratchet.py status"),
        ("eval indirect", 'V=RATCHET_HOME; eval "$V=./evil-home ratchet.py status"'),
        ("python3 os.environ", "python3 -c \"import os,subprocess; os.environ['RATCHET_HOME']='./evil-home'; "
                               "subprocess.run(['python3','ratchet.py','status'])\""),
    ], r"per-user folder|RATCHET_HOME")
    check("[CP4-T07] every indirect way of setting RATCHET_HOME is refused by the guard (exit 2)", bad == [], bad)
    check("[CP4-T07] each refusal names Ratchet's per-user folder as off-limits to builder sessions",
          bad_msg == [], bad_msg)

    # --- T08: RATCHET_DIRECTOR_KEY_FILE ----------------------------------------------------------------------------
    bad, bad_msg = table([
        ("declare -x", "declare -x RATCHET_DIRECTOR_KEY_FILE=./evil.key; ratchet.py director-sign approve-plan"),
        ("typeset", "typeset RATCHET_DIRECTOR_KEY_FILE=./evil.key; ratchet.py director-sign approve-plan"),
        ("eval indirect", 'V=RATCHET_DIRECTOR_KEY_FILE; eval "$V=./evil.key ratchet.py director-sign approve-plan"'),
        ("python3 os.environ", "python3 -c \"import os,subprocess; os.environ['RATCHET_DIRECTOR_KEY_FILE']="
                               "'./evil.key'; subprocess.run(['python3','ratchet.py','director-sign','approve-plan'])\""),
    ], r"director key|RATCHET_DIRECTOR_KEY_FILE")
    check("[CP4-T08] every indirect way of setting RATCHET_DIRECTOR_KEY_FILE is refused by the guard (exit 2)",
          bad == [], bad)
    check("[CP4-T08] each refusal says the director key path cannot be redirected by a builder session",
          bad_msg == [], bad_msg)

    # --- T14: the existing literal-override guard case is not weakened ----------------------------------------------
    env = make_env("claude")
    d = project(env, approved=False)
    r = cp4_guard_bash(d, env, "RATCHET_HUMAN_OVERRIDE=1 python3 ratchet.py approve-plan")
    check("[CP4-T14] the literal inline-override form is still refused exactly as before CP4 (additive, not weaker)",
          r.returncode == 2, (r.returncode, r.stderr))


SECTIONS.append(("CP4", cp4_guard_indirect_env_tricks))


def cp4_tampered_session_ignored():
    print("CP4: a tampered role/home that slipped past the guard is still ignored by the engine, and a disagreeing "
          "RATCHET_HOME is refused")

    # --- T09: a fresh RATCHET_HOME the test fully controls, no legitimate director key or hook history --------------
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    fake_home = Path(_tmp.mkdtemp(prefix="ratchet-trust-fakehome-"))
    fake_env = dict(env, RATCHET_ROLE="director", RATCHET_HOME=str(fake_home))
    rx(d, fake_env, "director-key")
    rx(d, fake_env, "director-enroll")
    sg = rx(d, fake_env, "director-sign", "approve-plan")
    forged = sg.stdout.strip().splitlines()[-1] if sg.returncode == 0 and sg.stdout.strip() else "forged-token"
    r = rx(d, env, "approve-plan", "--director-token", forged)
    phase_after_token = cp3_state(d)["phase"]
    check("[CP4-T09] a director token forged from a fresh, unrecorded RATCHET_HOME does not approve the plan",
          r.returncode != 0 and phase_after_token == "awaiting_plan_approval",
          (r.returncode, r.stderr, phase_after_token))
    rx(d, env, "ask", "approve-plan")
    r2 = cp4_say_yes(d, tp, env, text="yes, go ahead")
    assert r2.returncode == 0, r2.stderr + r2.stdout
    r3 = rx(d, env, "approve-plan")
    phase_after_chat = cp3_state(d)["phase"]
    check("[CP4-T09] the lane was not enrolled by the attempt: a normal chat yes from the real user still approves it",
          r3.returncode == 0 and phase_after_chat == "building", (r3.returncode, r3.stderr, phase_after_chat))

    # --- T10: a RATCHET_HOME that disagrees with what the hooks recorded is refused ---------------------------------
    env = make_env("claude")
    d = project(env)  # install + fire_claude already ran the hooks against this project with env's RATCHET_HOME (H1)
    h2 = Path(_tmp.mkdtemp(prefix="ratchet-trust-h2-"))
    env2 = dict(env, RATCHET_HOME=str(h2))
    r = rx(d, env2, "status")
    check("[CP4-T10] a later command under a different RATCHET_HOME (H2) is refused, not silently run against H2",
          r.returncode != 0
          and re.search(r"per-user folder|RATCHET_HOME", r.stderr + r.stdout, re.I) is not None
          and re.search(r"match|differ|disagree|mismatch", r.stderr + r.stdout, re.I) is not None,
          (r.returncode, r.stderr, env["RATCHET_HOME"], str(h2)))

    # --- T11: the SAME RATCHET_HOME the hooks recorded keeps working (no false positive) ------------------------------
    r2 = rx(d, env, "status")
    check("[CP4-T11] the same, legitimate RATCHET_HOME the hooks used keeps working exactly as before",
          r2.returncode == 0 and "Traceback" not in r2.stderr, (r2.returncode, r2.stderr))


SECTIONS.append(("CP4", cp4_tampered_session_ignored))


def cp4_director_and_override_regressions():
    print("CP4: a real director session still works, and a real person at a real tty can still use the override")

    # --- T12: a real director session enrolls a lane and signs an approval ------------------------------------------
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    denv = dict(env, RATCHET_ROLE="director")
    rk = rx(d, denv, "director-key")
    en = rx(d, denv, "director-enroll")
    sg = rx(d, denv, "director-sign", "approve-plan")
    tok = sg.stdout.strip().splitlines()[-1] if sg.stdout.strip() else ""
    check("[CP4-T12] a real director session creates the key, enrolls the lane, and signs a token",
          rk.returncode == 0 and en.returncode == 0 and sg.returncode == 0 and bool(tok),
          (rk.stderr, en.stderr, sg.stderr))
    r = rx(d, env, "approve-plan", "--director-token", tok)
    check("[CP4-T12] the director-signed token is accepted by approve-plan and advances the run",
          r.returncode == 0 and cp3_state(d)["phase"] == "building", (r.returncode, r.stderr, cp3_state(d)["phase"]))

    # --- T13: a person at their own terminal can still use the override for accept-state -----------------------------
    if not HAVE_PTY:
        print("  - [CP4-T13] not run: no pty on this platform (Windows)")
    else:
        env = make_env("claude")
        d = cp4_tamper_fixture(env)
        rc, out = cp4_pty_run(["accept-state"], d, dict(env, RATCHET_HUMAN_OVERRIDE="1"), cp4_answer_correct)
        r = rx(d, env, "status")
        check("[CP4-T13] typing back the printed code accepts the hand-edited state as the new baseline",
              rc == 0 and cp4_find_code(out) is not None and r.returncode == 0,
              (rc, out[-300:], r.returncode, r.stderr))


SECTIONS.append(("CP4", cp4_director_and_override_regressions))


# ---- CP4 adversarial round 1: RATCHET_ROLE gets a structural backstop, one reserved-variable detector ----------



def cp4x_read(p):
    try:
        return Path(p).read_text().strip()
    except OSError:
        return ""


def cp4_adv_round1_role_backstop():
    print("CP4 adv round 1: a builder's script that sets RATCHET_ROLE gets nothing; a real director still works")

    # --- X01: the reviewer's exact bypass: write a script, then run it with a clean-looking command --------------
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    out = Path(_tmp.mkdtemp(prefix="ratchet-trust-x01-"))
    script = out / "x.sh"
    body = ("export RATCHET_ROLE=director\n"
            + "".join(f'"{sys.executable}" "{ENGINE}" {cmd} > "{out}/{name}.out" 2> "{out}/{name}.err"; '
                      f'echo $? > "{out}/{name}.rc"\n'
                      for name, cmd in (("key", "director-key"), ("enroll", "director-enroll"),
                                        ("sign", "director-sign approve-plan"))))
    w = cp4x_guard_tool(d, env, "Write", {"file_path": str(script), "content": body})
    script.write_text(body)  # what the Write tool would do once the guard let it through
    g = cp4x_guard_tool(d, env, "Bash", {"command": f"sh {script}"})
    check("[CP4-X01] the guard sees nothing in the outer commands (Write of the script, then `sh <script>`)",
          w.returncode == 0 and g.returncode == 0, (w.returncode, w.stderr, g.returncode, g.stderr))
    subprocess.run(["sh", str(script)], cwd=d, env=env, capture_output=True, text=True, timeout=120)
    rcs = {n: cp4x_read(out / f"{n}.rc") for n in ("key", "enroll", "sign")}
    errs = " ".join(cp4x_read(out / f"{n}.err") for n in ("key", "enroll", "sign"))
    check("[CP4-X01] director-key, director-enroll and director-sign run from the builder's script are all refused",
          all(rc not in ("", "0") for rc in rcs.values()), (rcs, errs[:300]))
    check("[CP4-X01] each refusal says plainly why (the hooks did not record a director session here)",
          errs.count("refused") >= 3 and re.search(r"not (started as|treated as) the director", errs) is not None,
          errs[:400])
    check("[CP4-X01] no director key was created and the lane was not enrolled by the script",
          not (Path(env["RATCHET_HOME"]) / "director.key").exists()
          and not (Path(d) / ".ratchet" / "director.json").exists())
    tok = (cp4x_read(out / "sign.out").splitlines() or ["forged-token"])[-1] or "forged-token"
    r = rx(d, env, "approve-plan", "--director-token", tok)
    check("[CP4-X01] the follow-up `approve-plan --director-token` (plain env) is refused; the run still waits",
          r.returncode != 0 and cp3_state(d)["phase"] == "awaiting_plan_approval",
          (r.returncode, r.stderr, cp3_state(d)["phase"]))

    # --- X02: the same script in Python, run from a folder with no .ratchet/ and aimed at the lane with --root ----
    elsewhere = Path(_tmp.mkdtemp(prefix="ratchet-trust-x02-"))
    py = elsewhere / "x.py"
    py_body = ("import os, subprocess, sys\n"
               "os.environ['RATCHET_ROLE'] = 'director'\n"
               f"for a in (['director-key'], ['director-enroll'], ['director-sign', 'approve-plan']):\n"
               f"    r = subprocess.run([sys.executable, {str(ENGINE)!r}, '--root', {str(d)!r}, *a],"
               " capture_output=True, text=True)\n"
               "    print(a[0], r.returncode)\n")
    py.write_text(py_body)
    g = cp4x_guard_tool(d, env, "Bash", {"command": f"cd {elsewhere} && python3 x.py"})
    pr = subprocess.run([sys.executable, str(py)], cwd=elsewhere, env=env, capture_output=True, text=True,
                        timeout=120)
    codes = dict(ln.split() for ln in pr.stdout.splitlines() if len(ln.split()) == 2)
    check("[CP4-X02] from a folder with no hook record, `--root <lane>` director commands in a script are refused",
          g.returncode == 0 and len(codes) == 3 and all(v != "0" for v in codes.values())
          and not (Path(d) / ".ratchet" / "director.json").exists(), (g.returncode, pr.stdout, pr.stderr[-300:]))
    rx(d, env, "ask", "approve-plan")
    r2 = cp4_say_yes(d, tp, env, text="yes, go ahead")
    r3 = rx(d, env, "approve-plan")
    check("[CP4-X02] the lane is still not enrolled: the real user's chat yes approves it as before",
          r2.returncode == 0 and r3.returncode == 0 and cp3_state(d)["phase"] == "building",
          (r3.returncode, r3.stderr))

    # --- X03: a real director session in its own folder (no .ratchet/), with hooks, still drives a lane via --root ---
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    b = cp4x_guard_tool(d, env, "Bash", {"command": "ls"})  # the lane's builder has run commands in the lane
    home_dir = Path(_tmp.mkdtemp(prefix="ratchet-trust-director-"))
    denv = dict(env, RATCHET_ROLE="director")  # the director's agent CLI was started with it: its hooks have it
    steps = []
    for args in (["director-key"], ["director-enroll"], ["director-sign", "approve-plan"]):
        cmd = f"python3 {ENGINE} --root {d} {' '.join(args)}"
        g = cp4x_guard_tool(home_dir, denv, "Bash", {"command": cmd}, session="s-director")
        steps.append((g.returncode, rx(home_dir, denv, "--root", str(d), *args)))
    tok = steps[-1][1].stdout.strip().splitlines()[-1] if steps[-1][1].stdout.strip() else ""
    check("[CP4-X03] a real director session (own folder, hooks on) creates the key, enrolls the lane and signs",
          b.returncode == 0 and all(gr == 0 and s.returncode == 0 for gr, s in steps) and bool(tok),
          [(gr, s.returncode, s.stderr[-200:]) for gr, s in steps])
    r = rx(d, env, "approve-plan", "--director-token", tok)
    check("[CP4-X03] its token is accepted by approve-plan in the lane and advances the run",
          r.returncode == 0 and cp3_state(d)["phase"] == "building", (r.returncode, r.stderr))

    # --- X04: a director whose own folder is a Ratchet project (its guard recorded the director role there) --------
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    cp4x_guard_tool(d, env, "Bash", {"command": "ls"})
    dproj = Path(_tmp.mkdtemp(prefix="ratchet-trust-dproj-"))
    (dproj / ".ratchet").mkdir()
    denv = dict(env, RATCHET_ROLE="director")
    g = cp4x_guard_tool(dproj, denv, "Bash", {"command": f"python3 {ENGINE} --root {d} director-key"},
                        session="s-director")
    rec = json.loads((dproj / ".ratchet" / "hook-env.json").read_text())
    rk = rx(dproj, denv, "--root", str(d), "director-key")
    en = rx(dproj, denv, "--root", str(d), "director-enroll")
    sg = rx(dproj, denv, "--root", str(d), "director-sign", "approve-plan")
    tok = sg.stdout.strip().splitlines()[-1] if sg.stdout.strip() else ""
    r = rx(d, env, "approve-plan", "--director-token", tok)
    check("[CP4-X04] the director's own project records guard_role director, and --root lane commands work from it",
          g.returncode == 0 and rec.get("guard_role") == "director" and rk.returncode == 0 and en.returncode == 0
          and sg.returncode == 0 and r.returncode == 0 and cp3_state(d)["phase"] == "building",
          (rec, rk.stderr, en.stderr, sg.stderr, r.stderr))
    lane_rec = json.loads((Path(d) / ".ratchet" / "hook-env.json").read_text())
    check("[CP4-X04] the lane's record still shows its own builder session (the director never wrote it)",
          lane_rec.get("guard_role") == "builder", lane_rec)

    # --- X05: one reserved-variable detector; what the removed regexes covered is still refused -------------------
    env = make_env("claude")
    d = project(env)
    forms = ["export RATCHET_ROLE=director; python3 ratchet.py status",
             "env RATCHET_ROLE=director python3 ratchet.py status",
             "(RATCHET_ROLE=director python3 ratchet.py status)",
             "RATCHET_HUMAN_OVERRIDE=1 python3 ratchet.py approve-plan",
             "export RATCHET_HUMAN_OVERRIDE=1; python3 ratchet.py human-approve",
             "env FOO=1 RATCHET_HUMAN_OVERRIDE=1 python3 ratchet.py human-approve"]
    res = [(f, cp4_guard_bash(d, env, f)) for f in forms]
    check("[CP4-X05] every form the old RATCHET_ROLE / RATCHET_HUMAN_OVERRIDE regexes caught is still refused",
          all(r.returncode == 2 for _f, r in res), [(f, r.returncode) for f, r in res if r.returncode != 2])
    m = engine()
    check("[CP4-X05] the guard has one reserved-variable detector (the old duplicate regexes are gone)",
          not hasattr(m, "ROLE_SET_RE") and not hasattr(m, "OVERRIDE_SET_RE") and hasattr(m, "_reserved_env_problem"))

    # --- X06: the schema doc matches the code ---------------------------------------------------------------------
    doc = (ENGINE.parents[1] / "references" / "schemas.md").read_text()
    tree = next((ln for ln in doc.splitlines() if ln.strip().startswith("human_log.jsonl")), "")
    check("[CP4-X06] schemas.md's file tree names both writers of human_log.jsonl (the prompt hook and log_override)",
          "log_override" in tree and "hook only" not in tree, tree)
    para = next((p for p in doc.split("\n\n") if p.startswith("**Variables a builder session may read")), "")
    check("[CP4-X06] schemas.md says how the engine itself distrusts RATCHET_ROLE (the hook-recorded role)",
          "guard_role" in para and "RATCHET_ROLE" in para, para[-400:])


SECTIONS.append(("CP4", cp4_adv_round1_role_backstop))


# ---- CP4 adversarial round 2: the hook-recorded role and folder fail closed; the Windows reader times out --------

class cp4y_deny_hook_env:
    """The OS refusing writes of .ratchet/hook-env.json (and its temp file) only, through the engine's module-level
    open(), as deny_writes does for the crash-state paths."""

    def __init__(self, m):
        self.m = m

    def __enter__(self):
        import builtins
        real = builtins.open

        def fake(file, mode="r", *a, **k):
            if isinstance(file, (str, os.PathLike)) and any(c in mode for c in "wax+") \
                    and Path(file).name.startswith("hook-env.json"):
                raise PermissionError(13, "Permission denied (test)", str(file))
            return real(file, mode, *a, **k)
        self.m.open = fake
        return self

    def __exit__(self, *exc):
        del self.m.open


def cp4y_guard_in_process(m, d, command):
    """One real builder guard run through run_hook, in this process (so the write fault applies)."""
    import contextlib
    import io
    payload = {"session_id": "s", "cwd": str(d), "hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": command}}
    out, err, real_in = io.StringIO(), io.StringIO(), sys.stdin
    saved_role = os.environ.pop("RATCHET_ROLE", None)
    sys.stdin = io.StringIO(json.dumps(payload))
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = m.run_hook("guard", "claude")
    finally:
        sys.stdin = real_in
        if saved_role is not None:
            os.environ["RATCHET_ROLE"] = saved_role
    return rc, out.getvalue(), err.getvalue()


def cp4y_director_cmds(cwd, env, root=None):
    pre = ["--root", str(root)] if root else []
    return [rx(cwd, env, *pre, *a) for a in (["director-key"], ["director-enroll"], ["director-sign", "approve-plan"])]


def cp4_adv_round2_fail_closed():
    print("CP4 adv round 2: no hook record means no director (unless no hooks are installed at all); write "
          "failures are reported, never swallowed; the Windows terminal read times out")

    # --- X07: hooks installed; the builder's guard runs but cannot save its FIRST record -------------------------
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    rec = Path(d) / ".ratchet" / "hook-env.json"
    if rec.exists():
        rec.unlink()  # no record written yet: the guard's run below is the first one
    install(d, env, "claude")
    m = engine()
    with engine_env(env), cp4y_deny_hook_env(m):
        rc, out, err = cp4y_guard_in_process(m, d, "ls")
    with engine_env(env):
        unresolved = m.unresolved_hook_crashes(Path(d), "claude")
    log = errors_path(d).read_text() if errors_path(d).exists() else ""
    check("[CP4-X07] the guard still decides (ls allowed) and its record write really failed (no hook-env.json)",
          rc == 0 and not rec.exists(), (rc, out, err))
    check("[CP4-X07] the failed write is said where the host shows hook output, not swallowed",
          "could not save" in (out + err) and "hook-env.json" in (out + err), (out, err))
    check("[CP4-X07] the failed write is recorded like a hook crash (unresolved guard crash + hook-errors.log line)",
          any(c["event"] == "guard" for c in unresolved) and "HookRecordError" in log, (unresolved, log[-300:]))
    benv = dict(env, RATCHET_ROLE="director")  # what the builder's script sets
    res = cp4y_director_cmds(d, benv)
    check("[CP4-X07] with the record missing after a failed write, director-key/enroll/sign are all refused",
          all(r.returncode != 0 for r in res) and all("refused" in r.stderr for r in res),
          [(r.returncode, r.stderr[-200:]) for r in res])
    check("[CP4-X07] no director key was created and the lane was not enrolled",
          not (Path(env["RATCHET_HOME"]) / "director.key").exists()
          and not (Path(d) / ".ratchet" / "director.json").exists())
    fake_home = Path(_tmp.mkdtemp(prefix="ratchet-trust-x07-home-"))
    r = rx(d, dict(env, RATCHET_HOME=str(fake_home)), "status")
    check("[CP4-X07] the per-user folder check fails closed too: a made-up RATCHET_HOME is refused, not trusted",
          r.returncode != 0 and "hook-env.json" in r.stderr and "missing" in r.stderr, (r.returncode, r.stderr))
    g = fire_guard(d, env)  # the writes work again: the builder's next guard run saves the record
    rec_now = json.loads(rec.read_text()) if rec.exists() else {}
    r = rx(d, env, "status")
    res = cp4y_director_cmds(d, benv)
    check("[CP4-X07] once a hook saves the record the run works again, and the builder still gets no director role",
          g.returncode == 0 and rec_now.get("guard_role") == "builder" and r.returncode == 0
          and all(x.returncode != 0 for x in res), (g.stderr, rec_now, r.stderr, [x.stderr[-150:] for x in res]))

    # --- X08: hooks installed, no record at all (never written): RATCHET_ROLE=director is not taken at its word ---
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    rec = Path(d) / ".ratchet" / "hook-env.json"
    if rec.exists():
        rec.unlink()
    install(d, env, "claude")
    denv = dict(env, RATCHET_ROLE="director")
    res = cp4y_director_cmds(d, denv)
    check("[CP4-X08] hooks installed + no hook record: director-key/enroll/sign are refused",
          all(r.returncode != 0 for r in res) and not (Path(env["RATCHET_HOME"]) / "director.key").exists(),
          [(r.returncode, r.stderr[-200:]) for r in res])
    check("[CP4-X08] the refusal says plainly what to do (hooks are installed; restart the director's agent CLI)",
          "hooks are installed" in res[0].stderr and "Restart the director's agent CLI" in res[0].stderr,
          res[0].stderr)
    spoof = cp4y_director_cmds(d, dict(denv, RATCHET_HOST_PROVIDER="openai"))
    check("[CP4-X08] claiming another agent CLI (RATCHET_HOST_PROVIDER) does not make the installed hooks vanish",
          all(r.returncode != 0 for r in spoof), [(r.returncode, r.stderr[-150:]) for r in spoof])
    elsewhere = Path(_tmp.mkdtemp(prefix="ratchet-trust-x08-"))
    res = cp4y_director_cmds(elsewhere, denv, root=d)
    check("[CP4-X08] from a folder no director hook recorded, `--root <lane>` director commands are refused too",
          all(r.returncode != 0 for r in res) and not (Path(d) / ".ratchet" / "director.json").exists(),
          [(r.returncode, r.stderr[-150:]) for r in res])
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    install(d, env, "claude", scope="user")  # installed for the user, in no project
    elsewhere = Path(_tmp.mkdtemp(prefix="ratchet-trust-x08u-"))
    res = cp4y_director_cmds(elsewhere, dict(env, RATCHET_ROLE="director"), root=d)
    check("[CP4-X08] user-scope hooks count as installed: with no director record the commands are refused",
          all(r.returncode != 0 for r in res), [(r.returncode, r.stderr[-150:]) for r in res])
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)  # hooks delivered without install-hooks config (a plugin): the guard has run
    cp4x_guard_tool(d, env, "Bash", {"command": "ls"})
    rec = Path(d) / ".ratchet" / "hook-env.json"
    if rec.exists():
        rec.unlink()
    res = cp4y_director_cmds(d, dict(env, RATCHET_ROLE="director"))
    check("[CP4-X08] hooks with no install config that have enforced here (a guard ran) count as in use: refused",
          all(r.returncode != 0 for r in res) and not (Path(env["RATCHET_HOME"]) / "director.key").exists(),
          [(r.returncode, r.stderr[-150:]) for r in res])
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)  # no hooks installed anywhere (CP4-T12's setup)
    rk = rx(d, dict(env, RATCHET_ROLE="director"), "director-key")
    check("[CP4-X08] with no hooks installed at all the variable is taken at its word, and it says so plainly",
          rk.returncode == 0 and "hooks are not installed" in rk.stderr and "taken at its word" in rk.stderr,
          (rk.returncode, rk.stderr))

    # --- X09: the Windows terminal read is bounded by OVERRIDE_TIMEOUT and refuses on expiry ----------------------
    import inspect
    import io
    import threading
    m = engine()
    rfd, wfd = os.pipe()
    blocking = os.fdopen(rfd, "r")  # nothing is ever written: readline() would wait forever
    t0 = time.time()
    got = m._read_line_with_timeout(blocking.readline, 0.3)
    check("[CP4-X09] the bounded read returns nothing once the timeout passes (no answer)",
          got is None and time.time() - t0 < 5, (got, time.time() - t0))
    check("[CP4-X09] a typed line is returned, and end of input counts as no answer",
          m._read_line_with_timeout(lambda: "123456\n", 5) == "123456\n"
          and m._read_line_with_timeout(lambda: "", 5) is None)
    src = inspect.getsource(m._open_terminal)
    check("[CP4-X09] the Windows branch reads the console through the bounded read, with OVERRIDE_TIMEOUT",
          "_read_line_with_timeout(fin.readline, OVERRIDE_TIMEOUT)" in src, src[:600])

    class NtOs:  # the engine's view of the OS, as on Windows; everything else is the real os module
        name = "nt"

        def __getattr__(self, k):
            return getattr(os, k)
    rfd2, wfd2 = os.pipe()
    conin, conout = os.fdopen(rfd2, "r"), io.StringIO()
    import builtins
    real_open = builtins.open

    def fake_open(file, mode="r", *a, **k):
        if file == "CONIN$":
            return conin
        if file == "CONOUT$":
            return conout
        return real_open(file, mode, *a, **k)
    box, err = [], io.StringIO()

    def run():
        import contextlib
        try:
            with contextlib.redirect_stderr(err):
                m.human_override("check the Windows timeout")
            box.append("approved")
        except SystemExit as e:
            box.append(("refused", e.code))
        except Exception as e:
            box.append(("error", repr(e)))
    saved_ov = os.environ.get("RATCHET_HUMAN_OVERRIDE")
    m.os, m.open, m.OVERRIDE_TIMEOUT = NtOs(), fake_open, 0.5
    os.environ["RATCHET_HUMAN_OVERRIDE"] = "1"
    try:
        th = threading.Thread(target=run, daemon=True)
        th.start()
        th.join(20)
    finally:
        m.os = os
        del m.open
        if saved_ov is None:
            os.environ.pop("RATCHET_HUMAN_OVERRIDE", None)
        else:
            os.environ["RATCHET_HUMAN_OVERRIDE"] = saved_ov
    check("[CP4-X09] on Windows an unanswered code is refused once OVERRIDE_TIMEOUT passes (it never waits forever)",
          not th.is_alive() and box and box[0][0] == "refused" and "No code was typed back" in err.getvalue()
          and not m._OVERRIDE_CONFIRMED, (th.is_alive(), box, err.getvalue()[-300:]))
    for fd in (wfd, wfd2):
        os.close(fd)


SECTIONS.append(("CP4", cp4_adv_round2_fail_closed))

# ---- end CP4 ----


def cp4_adv_round3_stale_record_and_recovery():
    print("CP4 adv round 3: an old director record is not trusted while a hook failure stands; status, next, "
          "doctor, view and install-hooks still run when the hook record can't be trusted")

    # --- X10: the reviewer's scenario: a director record exists, then a builder's guard can't replace it ---------
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    install(d, env, "claude")
    rec = Path(d) / ".ratchet" / "hook-env.json"
    denv = dict(env, RATCHET_ROLE="director")
    benv = dict(env, RATCHET_ROLE="director")  # what the builder's script sets (its CLI was not started with it)
    g = cp4x_guard_tool(d, denv, "Bash", {"command": "ls"}, session="s-director")
    k = rx(d, denv, "director-key")
    check("[CP4-X10] setup: a real director guard recorded the director role here, and director-key works",
          g.returncode == 0 and json.loads(rec.read_text()).get("guard_role") == "director" and k.returncode == 0,
          (g.stderr, rec.read_text() if rec.exists() else None, k.returncode, k.stderr[-200:]))
    m = engine()
    with engine_env(env), cp4y_deny_hook_env(m):
        rc, out, err = cp4y_guard_in_process(m, d, "ls")  # the builder's guard: its builder record can't be saved
    stale = json.loads(rec.read_text()) if rec.exists() else {}
    check("[CP4-X10] the builder's guard write really failed and the old director record is still on disk",
          rc == 0 and stale.get("guard_role") == "director" and "could not save" in (out + err), (rc, stale, out, err))
    res = cp4y_director_cmds(d, benv)
    check("[CP4-X10] with the stale director record, director-key/enroll/sign from the builder are all refused",
          all(r.returncode != 0 for r in res) and all("refused" in r.stderr for r in res)
          and res[2].stdout.strip() == "" and not (Path(d) / ".ratchet" / "director.json").exists(),
          [(r.returncode, r.stdout[-80:], r.stderr[-200:]) for r in res])
    check("[CP4-X10] the refusal says why plainly (a hook failed, the record may be out of date, run doctor)",
          "out of date" in res[2].stderr and "doctor" in res[2].stderr and "guard hook" in res[2].stderr,
          res[2].stderr)
    f = rx(d, env, "fingerprint")
    check("[CP4-X10] the per-user folder / key-file check does not trust the stale record either (fingerprint refused)",
          f.returncode != 0 and "out of date" in f.stderr, (f.returncode, f.stderr[-300:]))
    s = rx(d, env, "status", "--json")
    check("[CP4-X10] status --json still runs and says the hook record is not confirmed",
          s.returncode == 0 and js(s) is not None and "not confirmed" in s.stderr, (s.returncode, s.stderr[-300:]))
    g = cp4x_guard_tool(d, env, "Bash", {"command": "ls"})  # the builder's next guard run saves its record
    res = cp4y_director_cmds(d, benv)
    f = rx(d, env, "fingerprint")
    check("[CP4-X10] once the builder's guard saves its record, the run works again and the builder is no director",
          g.returncode == 0 and json.loads(rec.read_text()).get("guard_role") == "builder" and f.returncode == 0
          and all(r.returncode != 0 for r in res), (g.stderr, f.stderr[-200:], [r.stderr[-150:] for r in res]))
    g = cp4x_guard_tool(d, denv, "Bash", {"command": "ls"}, session="s-director")
    sg = rx(d, denv, "director-sign", "approve-plan")
    check("[CP4-X10] a real director guard run records the role again and director-sign works",
          g.returncode == 0 and sg.returncode == 0 and sg.stdout.strip() != "", (sg.returncode, sg.stderr[-300:]))

    # --- X10b: the director's own folder record is stale while a lane is reached with --root ---------------------
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    cp4x_guard_tool(d, env, "Bash", {"command": "ls"})
    dproj = Path(_tmp.mkdtemp(prefix="ratchet-trust-x10b-"))
    (dproj / ".ratchet").mkdir()
    denv = dict(env, RATCHET_ROLE="director")
    cp4x_guard_tool(dproj, denv, "Bash", {"command": "ls"}, session="s-director")
    with engine_env(env), cp4y_deny_hook_env(m):
        cp4y_guard_in_process(m, dproj, "ls")  # a builder session in that folder; its record write fails
    r = rx(dproj, denv, "--root", str(d), "director-key")
    check("[CP4-X10] a stale director record in the own folder (a failed write there) is refused with --root too",
          r.returncode != 0 and "refused" in r.stderr and "out of date" in r.stderr
          and not (Path(env["RATCHET_HOME"]) / "director.key").exists(), (r.returncode, r.stderr[-300:]))

    # --- X11: .ratchet/ read-only: the record is missing and the hook can't even save the crash -----------------
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    install(d, env, "claude")
    rdir = Path(d) / ".ratchet"
    if (rdir / "hook-env.json").exists():
        (rdir / "hook-env.json").unlink()
    os.chmod(rdir, 0o555)
    try:
        probe_ok = False
        try:
            (rdir / "probe").write_text("x")
            (rdir / "probe").unlink()
            probe_ok = True
        except OSError:
            pass
        if probe_ok:  # root, or an OS whose folder permissions don't stop writes: the setup can't be made here
            print("  - [CP4-X11] not run: this user/OS can write to a read-only folder, so the setup can't be made")
        else:
            g = fire_guard(d, env)
            check("[CP4-X11] setup: the guard could not save the record, nor the crash (it says to run doctor)",
                  g.returncode == 0 and not (rdir / "hook-env.json").exists() and "doctor" in g.stderr, g.stderr)
            fp = rx(d, env, "fingerprint")
            check("[CP4-X11] other commands are still refused, saying the record is missing",
                  fp.returncode != 0 and "Refused" in fp.stderr and "missing" in fp.stderr, fp.stderr[-300:])
            doc = rx(d, env, "doctor")
            check("[CP4-X11] doctor still runs: it prints its report and says plainly what is wrong",
                  "Host running this command" in doc.stdout and "hook-env.json" in doc.stderr
                  and "missing" in doc.stderr and "Traceback" not in doc.stderr, (doc.returncode, doc.stderr[-300:]))
            dj = rx(d, env, "doctor", "--json")
            st, nx = rx(d, env, "status"), rx(d, env, "next")
            ok_json = True
            for r in (dj, nx):
                try:
                    json.loads(r.stdout)
                except ValueError:
                    ok_json = False
            check("[CP4-X11] doctor --json, next and status still run and print their output, with the problem said",
                  ok_json and "Phase" in st.stdout and all("missing" in r.stderr for r in (dj, nx, st))
                  and all("Traceback" not in r.stderr for r in (dj, nx, st)),
                  [(r.returncode, r.stdout[:80], r.stderr[-150:]) for r in (dj, nx, st)])
            check("[CP4-X11] the reads exit non-zero while the record is missing (what they show is unconfirmed)",
                  all(r.returncode != 0 for r in (doc, dj, nx, st)), [r.returncode for r in (doc, dj, nx, st)])
            v = rx(d, env, "view")
            check("[CP4-X11] view still runs (prints the viewer link) and says what is wrong",
                  "file://" in v.stdout and "missing" in v.stderr and "Traceback" not in v.stderr,
                  (v.returncode, v.stdout[:100], v.stderr[-200:]))
            ih = rx(d, env, "install-hooks", "--host", "claude")
            check("[CP4-X11] install-hooks still repairs the hooks (exit 0) and says what is wrong",
                  ih.returncode == 0 and "hooks written to" in ih.stdout and "missing" in ih.stderr,
                  (ih.returncode, ih.stdout, ih.stderr[-200:]))
    finally:
        os.chmod(rdir, 0o755)
    g = fire_guard(d, env)
    st = rx(d, env, "status")
    check("[CP4-X11] once .ratchet/ is writable, the next guard run saves the record and status runs cleanly",
          g.returncode == 0 and (rdir / "hook-env.json").exists() and st.returncode == 0
          and "not confirmed" not in st.stderr, (g.stderr[-200:], st.returncode, st.stderr[-200:]))


SECTIONS.append(("CP4", cp4_adv_round3_stale_record_and_recovery))


# ---- CP4 adversarial round 4: an override that can't be audited is refused; concurrent hooks lose no update ----

def cp4z_log_unwritable(d):
    """Make .ratchet/human_log.jsonl read-only (the run state stays writable). False when this user/OS can still
    append to it (root), so the setup can't be made."""
    p = Path(d) / ".ratchet" / "human_log.jsonl"
    if not p.exists():
        p.write_text("")
    os.chmod(p, 0o444)
    try:
        with open(p, "a"):
            pass
        os.chmod(p, 0o644)
        return False
    except OSError:
        return True


def cp4z_log_writable(d):
    os.chmod(Path(d) / ".ratchet" / "human_log.jsonl", 0o644)


_CP4Z_SLOW_HOOK = r"""
import importlib.util, pathlib, sys, time
spec = importlib.util.spec_from_file_location("ratchet_engine_slow_hook", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
ready, real = pathlib.Path(sys.argv[2]), m.secrets
class Slow:  # the hook's first random name is made right before it writes its record: pause there
    n = 0
    def token_hex(self, k):
        if Slow.n == 0:
            ready.write_text("x")
            time.sleep(float(sys.argv[4]))
        Slow.n += 1
        return real.token_hex(k)
    def __getattr__(self, a):
        return getattr(real, a)
m.secrets = Slow()
sys.exit(m.run_hook(sys.argv[3], "claude"))
"""


def cp4z_slow_hook(d, env, event, payload, pause=2.0):
    """A real hook process that pauses right before it saves its hook-env record (after its start time is taken):
    the exact interleaving of a hook that started first but finishes last. Returns the process once it is paused
    (or after 15 s if it never reaches the write)."""
    ready = Path(_tmp.mkdtemp(prefix="ratchet-trust-x12-")) / "ready"
    p = subprocess.Popen([sys.executable, "-c", _CP4Z_SLOW_HOOK, str(ENGINE), str(ready), event, str(pause)],
                         cwd=str(d), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True)
    p.stdin.write(json.dumps(payload))
    p.stdin.close()
    t0 = time.time()
    while not ready.exists() and p.poll() is None and time.time() - t0 < 15:
        time.sleep(0.02)
    return p


def cp4z_bash_payload(d, command="ls", session="s"):
    return {"session_id": session, "cwd": str(d), "hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": command}}


def cp4z_hook_proc(d, env, event, payload):
    p = subprocess.Popen([sys.executable, str(ENGINE), "hook", event, "--host", "claude"], cwd=str(d), env=env,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    p.stdin.write(json.dumps(payload))
    p.stdin.close()
    return p


def cp4z_view(d):
    try:
        return json.loads((Path(d) / ".ratchet" / "hook-env.json").read_text())
    except (OSError, ValueError):
        return {}


def cp4_adv_round4_audit_and_concurrency():
    print("CP4 adv round 4: an override whose audit line can't be written is refused; concurrent hooks never "
          "restore an older role or drop a field")

    # --- X12: the override is confirmed, but human_log.jsonl can't take its audit line: refused, nothing changes --
    if not HAVE_PTY:
        print("  - [CP4-X12] not run: no pty on this platform (Windows)")
    else:
        env = make_env("claude")
        d = cp4_awaiting_review(env)
        sp = Path(d) / ".ratchet/runs/f/state.json"
        before = sp.read_text()
        if not cp4z_log_unwritable(d):
            print("  - [CP4-X12] not run: this user/OS can write to a read-only file, so the setup can't be made")
        else:
            try:
                rc, out = cp4_pty_run(["human-approve"], d, dict(env, RATCHET_HUMAN_OVERRIDE="1"),
                                      cp4_answer_correct)
            finally:
                cp4z_log_writable(d)
            check("[CP4-X12] human-approve: a confirmed override whose audit line can't be written is refused, "
                  "with a plain message, and approves nothing (state.json unchanged)",
                  rc != 0 and cp4_find_code(out) is not None and "audit line" in out and "nothing was approved" in out
                  and sp.read_text() == before and "Traceback" not in out, (rc, out[-400:]))
            check("[CP4-X12] human-approve: no override line was logged for the refused approval",
                  not any(ln.get("override") for ln in cp4_human_log_lines(d)), cp4_human_log_lines(d)[-2:])

        env = make_env("claude")
        d = cp4_tamper_fixture(env)
        if not cp4z_log_unwritable(d):
            print("  - [CP4-X12] not run: this user/OS can write to a read-only file, so the setup can't be made")
        else:
            try:
                rc, out = cp4_pty_run(["accept-state"], d, dict(env, RATCHET_HUMAN_OVERRIDE="1"), cp4_answer_correct)
            finally:
                cp4z_log_writable(d)
            r = rx(d, env, "status")
            check("[CP4-X12] accept-state: refused when its audit line can't be written; the hand-edited state is "
                  "still not accepted as a baseline",
                  rc != 0 and cp4_find_code(out) is not None and "audit line" in out and r.returncode != 0
                  and "changed outside ratchet.py" in r.stderr, (rc, out[-300:], r.returncode, r.stderr[-200:]))

        env = make_env("claude")
        d = project(env)
        active = (Path(d) / ".ratchet" / "active.json").read_text()
        if not cp4z_log_unwritable(d):
            print("  - [CP4-X12] not run: this user/OS can write to a read-only file, so the setup can't be made")
        else:
            try:
                rc, out = cp4_pty_run(["init", "other feature", "--slug", "g", "--force"], d,
                                      dict(env, RATCHET_HUMAN_OVERRIDE="1"), cp4_answer_correct)
            finally:
                cp4z_log_writable(d)
            check("[CP4-X12] init --force: refused when its audit line can't be written; the active run is not "
                  "replaced",
                  rc != 0 and cp4_find_code(out) is not None and "audit line" in out
                  and (Path(d) / ".ratchet" / "active.json").read_text() == active
                  and not (Path(d) / ".ratchet/runs/g").exists(), (rc, out[-300:]))

    # --- X13: a director guard that started first but saves last does not restore the director role -------------
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    install(d, env, "claude")
    g0 = cp4x_guard_tool(d, env, "Bash", {"command": "ls"})
    denv = dict(env, RATCHET_ROLE="director")
    slow = cp4z_slow_hook(d, denv, "guard", cp4z_bash_payload(d, session="s-director"))
    b = cp4x_guard_tool(d, env, "Bash", {"command": "ls"})  # the builder's guard: started later, finishes first
    slow_out = slow.communicate(timeout=60)
    view = cp4z_view(d)
    m = engine()
    with engine_env(env):
        role = m._recorded_guard_role(Path(d))
    check("[CP4-X13] setup: both guards ran cleanly, the director's one finishing last",
          g0.returncode == 0 and b.returncode == 0 and slow.returncode == 0, (b.stderr[-200:], slow_out))
    check("[CP4-X13] the newest guard (the builder's) wins: hook-env.json and the engine both say builder",
          view.get("guard_role") == "builder" and role == "builder", (view, role))
    res = cp4y_director_cmds(d, denv)  # what the builder's script sets
    check("[CP4-X13] so the builder's RATCHET_ROLE=director gets nothing: director-key/enroll/sign are refused",
          all(r.returncode != 0 for r in res) and not (Path(env["RATCHET_HOME"]) / "director.key").exists(),
          [(r.returncode, r.stderr[-150:]) for r in res])

    # --- X13: an older prompt hook (another folder, so it must save) never drops the newer guard's role ----------
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    install(d, env, "claude")
    denv = dict(env, RATCHET_ROLE="director")
    g0 = cp4x_guard_tool(d, denv, "Bash", {"command": "ls"}, session="s-director")
    other = dict(env, RATCHET_HOME=str(Path(_tmp.mkdtemp(prefix="ratchet-trust-x13h-")) / "rh"))
    slow = cp4z_slow_hook(d, other, "prompt", prompt_payload(d))
    b = cp4x_guard_tool(d, env, "Bash", {"command": "ls"})
    slow_out = slow.communicate(timeout=60)
    view = cp4z_view(d)
    with engine_env(env):
        role = m._recorded_guard_role(Path(d))
        home = m._current_hook_env()["home"]
    check("[CP4-X13] an older prompt hook finishing last keeps the newer guard's builder role and folder",
          g0.returncode == 0 and b.returncode == 0 and slow.returncode == 0 and view.get("guard_role") == "builder"
          and role == "builder" and view.get("home") == home, (view, role, home, slow_out))
    res = cp4y_director_cmds(d, denv)
    check("[CP4-X13] and the builder's script still gets no director role from it",
          all(r.returncode != 0 for r in res), [(r.returncode, r.stderr[-150:]) for r in res])

    # --- X13: many hook processes at once: the newest guard's role always wins and no field is lost --------------
    env = make_env("claude")
    d, tp = cp4_unapproved_plan(env)
    install(d, env, "claude")
    denv = dict(env, RATCHET_ROLE="director")
    homes = {}
    with engine_env(env):
        homes[m._current_hook_env()["home"]] = 1
    with engine_env(other):
        homes[m._current_hook_env()["home"]] = 1

    def burst(guard_env, guards, session):
        procs = [cp4z_hook_proc(d, guard_env, "guard", cp4z_bash_payload(d, session=session)) for _ in range(guards)]
        procs += [cp4z_hook_proc(d, other, "prompt", prompt_payload(d)) for _ in range(6)]
        procs += [cp4z_hook_proc(d, env, "stop", stop_payload(d)) for _ in range(4)]
        return [(p.wait(timeout=120), p.stderr.read()) for p in procs]

    rounds = []
    for guard_env, want, n, session in ((denv, "director", 6, "s-director"), (env, "builder", 1, "s"),
                                        (denv, "director", 1, "s-director"), (env, "builder", 1, "s")):
        outs = burst(guard_env, n, session)
        view = cp4z_view(d)
        with engine_env(env):
            role = m._recorded_guard_role(Path(d))
        rounds.append((want, all(rc == 0 for rc, _e in outs), view, role,
                       [e[-150:] for rc, e in outs if "could not save" in e]))
    check("[CP4-X13] every concurrent hook ran cleanly and none failed to save its record",
          all(r[1] and not r[4] for r in rounds), [(r[0], r[1], r[4][:2]) for r in rounds])
    check("[CP4-X13] after each burst the role is the newest guard's (never an older one restored)",
          all(r[2].get("guard_role") == r[0] and r[3] == r[0] for r in rounds),
          [(r[0], r[2].get("guard_role"), r[3]) for r in rounds])
    check("[CP4-X13] no field is lost: hook-env.json always holds home, key_file and guard_role, home one of the "
          "folders the hooks really use",
          all(r[2].get("home") in homes and r[2].get("key_file") and r[2].get("guard_role") for r in rounds),
          [r[2] for r in rounds])
    log = errors_path(d).read_text() if errors_path(d).exists() else ""
    folder = Path(d) / ".ratchet" / "hook-env"
    left = sorted(os.listdir(folder)) if folder.is_dir() else []
    check("[CP4-X13] no hook logged a HookRecordError, and superseded records and temp files are cleaned up",
          "HookRecordError" not in log and not any(n.endswith(".tmp") for n in left) and len(left) <= 12
          and not any(n.endswith(".tmp") for n in os.listdir(Path(d) / ".ratchet")), (log[-300:], left))


SECTIONS.append(("CP4", cp4_adv_round4_audit_and_concurrency))


# ---- CP4 adversarial round 5: the audit line is part of the override itself; same-microsecond guard ties --------

def cp4w_choice_pending(env):
    """A building run with a `choice` question open and the user's reply "B" logged by the prompt hook, but with no
    transcript to check it against: without the override, `choice` refuses it; with it, `choice` records B."""
    d = project(env)
    r = rx(d, env, "ask", "choice", "--options", "A,B,C")
    assert r.returncode == 0, r.stderr + r.stdout
    time.sleep(0.01)
    rx(d, env, "hook", "prompt", "--host", "claude",
       stdin=json.dumps({"cwd": str(d), "prompt": "B", "session_id": "s-owner"}))
    return d


def cp4w_snapshot(d, env):
    """Every file Ratchet keeps for this project (.ratchet/, except the human log the test makes read-only) and
    in the per-user folder (integrity records), as bytes: a refused command must leave all of them as they were."""
    out = {}
    for base in (Path(d) / ".ratchet", Path(env["RATCHET_HOME"])):
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and p.name != "human_log.jsonl":
                try:
                    out[str(p)] = p.read_bytes()
                except OSError:
                    out[str(p)] = None
    return out


# Every command that accepts RATCHET_HUMAN_OVERRIDE, by the human_override call site it reaches in ratchet.py:
# _require_approval (human-approve, approve-plan), cmd_accept_state, cmd_init --force and cmd_choice.
CP4W_OVERRIDE_CMDS = [
    ("human-approve", lambda env: cp4_awaiting_review(env), ["human-approve"], "human-approve"),
    ("approve-plan", lambda env: cp4_unapproved_plan(env)[0], ["approve-plan"], "approve-plan"),
    ("accept-state", lambda env: cp4_tamper_fixture(env), ["accept-state"], "accept-state"),
    ("init --force", lambda env: project(env), ["init", "other feature", "--slug", "g", "--force"], "init --force"),
    ("choice", lambda env: cp4w_choice_pending(env), ["choice"], "choice"),
]


def cp4_adv_round5_override_audit_class():
    print("CP4 adv round 5: every command that takes the override writes its audit line, or is refused with nothing "
          "changed; on a same-microsecond tie the builder's guard record wins")
    import ast
    m = engine()

    # --- X14 (structural, code): every human_override call names what to audit; log_override has one caller ------
    tree = ast.parse(ENGINE.read_text(encoding="utf-8"))
    calls, log_callers = [], []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "human_override":
                    kw = {k.arg for k in node.keywords}
                    calls.append((fn.name, len(node.args) >= 3 or {"ctx", "action"} <= kw
                                  or (len(node.args) == 2 and "action" in kw)))
                elif node.func.id == "log_override":
                    log_callers.append(fn.name)
    sites = {f for f, _ok in calls}
    check("[CP4-X14] every human_override call site in ratchet.py passes the ctx and the action to audit",
          calls and all(ok for _f, ok in calls), calls)
    check("[CP4-X14] the call sites are the ones this test drives (approvals, accept-state, init, choice)",
          sites == {"_require_approval", "cmd_accept_state", "cmd_init", "cmd_choice"}, sites)
    check("[CP4-X14] the audit is part of the override: log_override is only called from human_override",
          log_callers == ["human_override"], log_callers)

    # --- X14 (in process): a confirmed override with no audit target, or an unwritable log, never returns True ----
    saved = (m._OVERRIDE_CONFIRMED, os.environ.get("RATCHET_HUMAN_OVERRIDE"))
    env = make_env("claude")
    d = project(env)
    results, unwritable = [], False
    try:
        m._OVERRIDE_CONFIRMED = True
        os.environ["RATCHET_HUMAN_OVERRIDE"] = "1"
        with engine_env(env):
            ctx = m.Ctx(Path(d))
            for args in ((), (ctx,), (ctx, "")):
                try:
                    results.append(("returned", m.human_override("test an unaudited use", *args)))
                except SystemExit:
                    results.append(("refused",))
            unwritable = cp4z_log_unwritable(d)
            try:
                if unwritable:
                    try:
                        results.append(("returned", m.human_override("test", ctx, "x14-unwritable")))
                    except SystemExit:
                        results.append(("refused",))
            finally:
                cp4z_log_writable(d)
            results.append(("returned", m.human_override("test", ctx, "x14-ok")))
    finally:
        m._OVERRIDE_CONFIRMED = saved[0]
        if saved[1] is None:
            os.environ.pop("RATCHET_HUMAN_OVERRIDE", None)
        else:
            os.environ["RATCHET_HUMAN_OVERRIDE"] = saved[1]
    acts = [ln.get("action") for ln in cp4_human_log_lines(d) if ln.get("override")]
    check("[CP4-X14] a confirmed override with no ctx or no action is refused, never used unaudited",
          results[:3] == [("refused",)] * 3, results)
    check("[CP4-X14] a confirmed override whose log can't be written is refused (in process)",
          not unwritable or results[3] == ("refused",), results)
    check("[CP4-X14] a confirmed, audited override returns True after writing exactly its own audit line",
          results[-1] == ("returned", True) and acts == ["x14-ok"], (results, acts))

    if not HAVE_PTY:
        print("  - [CP4-X14] command tests not run: no pty on this platform (Windows)")
    else:
        # --- X14: each command, used with a confirmed override, writes its audit line and succeeds ---------------
        for name, make, args, action in CP4W_OVERRIDE_CMDS:
            env = make_env("claude")
            d = make(env)
            n0 = len([ln for ln in cp4_human_log_lines(d) if ln.get("override")])
            rc, out = cp4_pty_run(args, d, dict(env, RATCHET_HUMAN_OVERRIDE="1"), cp4_answer_correct)
            new = [ln for ln in cp4_human_log_lines(d) if ln.get("override")][n0:]
            check(f"[CP4-X14] {name}: a confirmed override succeeds and leaves exactly one audit line naming it",
                  rc == 0 and len(new) == 1 and str(new[0].get("action", "")).startswith(action)
                  and new[0].get("by") == "human", (rc, out[-300:], new))
            if name == "choice":
                st = json.loads((Path(d) / ".ratchet/runs/f/state.json").read_text())
                check("[CP4-X14] choice: the override really skipped the transcript check and recorded option B",
                      "The user picked option B" in out and not st.get("pending"), (out[-300:], st.get("pending")))

        # --- X14: the same commands with an unwritable log are refused and change nothing --------------------------
        for name, make, args, action in CP4W_OVERRIDE_CMDS:
            env = make_env("claude")
            d = make(env)
            before = cp4w_snapshot(d, env)
            hl = Path(d) / ".ratchet" / "human_log.jsonl"
            log_before = hl.read_bytes() if hl.exists() else b""
            if not cp4z_log_unwritable(d):
                print(f"  - [CP4-X14] {name}: not run: this user/OS can write to a read-only file")
                continue
            try:
                rc, out = cp4_pty_run(args, d, dict(env, RATCHET_HUMAN_OVERRIDE="1"), cp4_answer_correct)
            finally:
                cp4z_log_writable(d)
            after = cp4w_snapshot(d, env)
            changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
            check(f"[CP4-X14] {name}: refused with a plain message when its audit line can't be written, and "
                  "nothing Ratchet keeps changed",
                  rc != 0 and cp4_find_code(out) is not None and "audit line" in out and "Traceback" not in out
                  and not changed and hl.read_bytes() == log_before, (rc, out[-300:], changed))

        env = make_env("claude")
        d = cp4w_choice_pending(env)
        r = rx(d, env, "choice")
        check("[CP4-X14] choice without the override still refuses a reply it can't match to the transcript",
              r.returncode != 0 and "Couldn't confirm" in (r.stderr + r.stdout), (r.returncode, r.stderr[-200:]))

    # --- D2: two guard records with the same start microsecond: the builder's wins, whatever the names say -------
    folder_root = Path(_tmp.mkdtemp(prefix="ratchet-trust-d2-"))
    folder = folder_root / ".ratchet" / "hook-env"
    folder.mkdir(parents=True)
    rec = json.dumps({"home": "/h", "key_file": "/k", "at": "x", "host": "claude"})
    outcomes = []
    for dtag, btag in (("zzzz", "0000"), ("0000", "zzzz")):
        for n in os.listdir(folder):
            os.unlink(folder / n)
        (folder / f"hook-env.json.guard.1000.director.1-{dtag}").write_text(rec)
        (folder / f"hook-env.json.guard.1000.builder.1-{btag}").write_text(rec)
        (folder / "hook-env.json.guard.999.director.1-aaaa").write_text(rec)
        (folder_root / ".ratchet" / "hook-env.json").write_text(json.dumps({"home": "/h", "key_file": "/k",
                                                                            "guard_role": "director"}))
        recs = m._hook_env_records(folder_root)
        outcomes.append((recs[-1][2], m._combined_hook_env(recs).get("guard_role"),
                         m._recorded_guard_role(folder_root)))
    check("[CP4-D2] same-microsecond director and builder guard records: the builder's is the newest, whichever "
          "random token each name holds",
          outcomes == [("builder", "builder", "builder")] * 2, outcomes)

    env = make_env("claude")
    roles = []
    for first, second in (("director", "builder"), ("builder", "director")):
        d = Path(_tmp.mkdtemp(prefix="ratchet-trust-d2p-"))
        (d / ".ratchet").mkdir()
        at = now_dt().isoformat(timespec="microseconds")
        saved_role = os.environ.get("RATCHET_ROLE")
        ok_all = True
        try:
            with engine_env(env):
                for role in (first, second):
                    if role == "director":
                        os.environ["RATCHET_ROLE"] = "director"
                    else:
                        os.environ.pop("RATCHET_ROLE", None)
                    ok_all = m.record_hook_env(d, "claude", "guard", started=at) and ok_all
                view = json.loads((d / ".ratchet" / "hook-env.json").read_text())
                roles.append((ok_all, view.get("guard_role"), m._recorded_guard_role(d)))
        finally:
            if saved_role is None:
                os.environ.pop("RATCHET_ROLE", None)
            else:
                os.environ["RATCHET_ROLE"] = saved_role
    check("[CP4-D2] record_hook_env: a director and a builder guard with the same start time leave the builder "
          "role, in either finishing order",
          roles == [(True, "builder", "builder")] * 2, roles)


SECTIONS.append(("CP4", cp4_adv_round5_override_audit_class))


def main():
    for name, fn in SECTIONS:
        try:
            fn()
        except Exception:  # one broken section must not hide the others, but it still fails the run
            trust_helpers.FAIL += 1
            print(f"  ✗ [{name}] section crashed\n{traceback.format_exc()}")
    print(f"\n{trust_helpers.PASS} passed, {trust_helpers.FAIL} failed")
    sys.exit(1 if trust_helpers.FAIL else 0)


if __name__ == "__main__":
    main()
