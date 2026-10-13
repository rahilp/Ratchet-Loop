#!/usr/bin/env python3
"""
CP5 of the trust-holes run, part c: approvals answer the question Ratchet asked.
Moved verbatim from the old tests/test_trust.py; shared helpers live in tests/trust_helpers.py.

    python3 tests/test_trust_cp5_c.py
"""
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

import _tmp
import trust_helpers
from trust_helpers import (
    ENGINE, HAVE_PTY, check, cp3_R0, cp3_open_round, cp3_project, cp3_reviewer_writes, cp3_state, cp3_write_tests,
    cp4_answer_correct, cp4_find_code, cp4_human_log_lines, cp4_pty_run, cp4_say_yes, cp5_plan, cp5_say, cp5r8_sha,
    cp5v_until, cp5w_lock, cp5x_history, cp5y_first_run_plan, cp5y_guard_bash, cp5y_load_engine, cp5y_repo,
    cp5z_notes, cp5z_project_notes, git, make_env, now_dt, rx,
)

SECTIONS = []


# ---- CP5 adversarial round 5 ----
# The state lock fails safe: every failure to take it (a read-only folder, a dangling link or a folder in its place)
# waits with a pause, then refuses plainly; a stale lock is cleared by exactly one waiter; the override code is typed
# before the lock is taken; the prompt and stop hooks take the same lock; and the guard blocks only a real
# `ratchet.py hook ...` call, not text that mentions one.

# Runs the engine's own main() with a few observation points: a line in the marker file when the lock wait pauses
# ("waiting") and at every state save ("saved-locked" / "saved-unlocked": whether the lock file existed then).
# With CP5V_GATE set, each save first waits until that file exists (the test holds a command inside its lock).
_CP5V_WRAP = r"""
import importlib.util, os, pathlib, sys, time
spec = importlib.util.spec_from_file_location("ratchet_cp5_round5", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
marker = pathlib.Path(sys.argv[2])
m.STATE_LOCK_WAIT_SECONDS = float(sys.argv[3])
m.STATE_LOCK_HOOK_WAIT_SECONDS = float(sys.argv[3])
gate = os.environ.get("CP5V_GATE")
pause = m._lock_pause
def noting_pause():
    with marker.open("a") as f:
        f.write("waiting\n")
    pause()
m._lock_pause = noting_pause
save = m.Ctx.save_state
def noting_save(self, st):
    with marker.open("a") as f:
        f.write("in-save\n")
    while gate and not pathlib.Path(gate).exists():
        time.sleep(0.02)
    with marker.open("a") as f:
        f.write("saved-locked\n" if (self.ratchet / "state.lock").exists() else "saved-unlocked\n")
    save(self, st)
m.Ctx.save_state = noting_save
m.main(sys.argv[4:])
"""


def cp5v_wrap(d):
    p = Path(d) / "cp5v_wrap.py"
    p.write_text(_CP5V_WRAP)
    return p


def cp5v_start(d, env, marker, wait, *args, gate=None):
    e = dict(env, CP5V_GATE=str(gate)) if gate else env
    return subprocess.Popen([sys.executable, str(cp5v_wrap(d)), str(ENGINE), str(marker), str(wait), *args], cwd=d,
                            env=e, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)




def cp5v_lines(marker):
    return Path(marker).read_text().split() if Path(marker).exists() else []


def cp5v_refusal(m, root, wait=0.5):
    """Take the lock in process with a short wait. Returns (outcome, message, seconds, pauses, opens)."""
    import contextlib
    import io
    counts = {"pause": 0, "open": 0}
    pause, real_open = m._lock_pause, os.open

    def counting_pause():
        counts["pause"] += 1
        pause()

    def counting_open(*a, **k):
        counts["open"] += 1
        return real_open(*a, **k)

    saved = m.STATE_LOCK_WAIT_SECONDS
    m.STATE_LOCK_WAIT_SECONDS, m._lock_pause, os.open = wait, counting_pause, counting_open
    err, outcome, t0 = io.StringIO(), "entered", time.monotonic()
    try:
        with contextlib.redirect_stderr(err):
            with m._StateLock(Path(root)):
                pass
    except SystemExit as e:
        outcome = f"refused {e.code}"
    except Exception:
        outcome = "crashed " + traceback.format_exc()
    finally:
        m.STATE_LOCK_WAIT_SECONDS, m._lock_pause, os.open = saved, pause, real_open
    return outcome, err.getvalue(), time.monotonic() - t0, counts["pause"], counts["open"]


def cp5_adv_round5_lock_fails_safe():
    print("CP5 adv round 5: the state lock fails safe, the hooks take it, and the guard blocks only real hook calls")
    m = cp5y_load_engine()

    # --- X30: every failure to create the lock waits with a pause, then refuses plainly within the wait ----------
    cases = []
    root = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5v-"))
    (root / ".ratchet").mkdir()
    os.chmod(root / ".ratchet", 0o555)
    try:
        writable = os.access(root / ".ratchet", os.W_OK)
        if writable:
            print("  - [CP5-X30] read-only folder: not run: this user/OS can write to a read-only folder")
        else:
            cases.append(("a read-only Ratchet folder", cp5v_refusal(m, root)))
    finally:
        os.chmod(root / ".ratchet", 0o755)
    root = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5v-"))
    (root / ".ratchet").mkdir()
    os.symlink(str(root / "nowhere"), str(root / ".ratchet" / "state.lock"))
    cases.append(("a dangling link where the lock goes", cp5v_refusal(m, root)))
    root = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5v-"))
    (root / ".ratchet" / "state.lock").mkdir(parents=True)
    cases.append(("a folder where the lock goes", cp5v_refusal(m, root)))
    old = time.time() - 3600
    os.utime(root / ".ratchet" / "state.lock", (old, old))
    cases.append(("an old folder where the lock goes (never treated as a stale lock)", cp5v_refusal(m, root)))
    for label, (outcome, msg, secs, pauses, opens) in cases:
        check(f"[CP5-X30] {label}: refused (exit 1) with a plain message naming the lock file, no traceback",
              outcome == "refused 1" and msg.startswith("RATCHET ✗") and "state.lock" in msg
              and "Traceback" not in msg, (outcome, msg))
        check(f"[CP5-X30] {label}: refused within the wait, pausing between tries (no spin)",
              0.4 <= secs < 5 and pauses >= 1 and opens <= pauses + 1, (secs, pauses, opens))
    check("[CP5-X30] the folder in the lock's place is left alone", (root / ".ratchet" / "state.lock").is_dir())
    # the same through a real command: a dangling link where the lock goes makes approve-plan refuse plainly, not
    # hang (a read-only Ratchet folder is refused even earlier, by the hook-record check, before any lock)
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "yes")
    marker = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5v-m-")) / "marker"
    os.symlink(str(Path(d) / "nowhere"), str(cp5w_lock(d)))
    proc = cp5v_start(d, env, marker, 1, "approve-plan")
    out, err = proc.communicate(timeout=60)
    check("[CP5-X30] approve-plan with a dangling link where the lock goes refuses plainly (exit 1, no traceback)",
          proc.returncode == 1 and "lock file" in err and "Traceback" not in err and "waiting" in cp5v_lines(marker)
          and cp3_state(d)["phase"] == "awaiting_plan_approval", (proc.returncode, err[-400:]))
    os.unlink(str(cp5w_lock(d)))

    # --- X31: two waiters on one stale lock: exactly one takes it; a live lock is never cleared ------------------
    import threading
    both = []
    for _trial in range(10):
        root = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5v-"))
        (root / ".ratchet").mkdir()
        lockf = root / ".ratchet" / "state.lock"
        lockf.write_text("12345 crashed")
        os.utime(lockf, (old, old))
        locks = [m._StateLock(root, wait=0.3) for _ in range(2)]
        start = threading.Barrier(2)
        got = {}

        def take(i):
            start.wait()
            got[i] = locks[i].acquire()

        threads = [threading.Thread(target=take, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        winners = [i for i in range(2) if got.get(i) is None]
        ok_trial = (len(winners) == 1 and lockf.read_text() == locks[winners[0]].token
                    and "still changing" in str(got.get(1 - winners[0]))
                    and not (root / ".ratchet" / "state.lock.break").exists())
        if winners:
            locks[winners[0]].release()
        both.append((ok_trial, winners, got, lockf.exists()))
    check("[CP5-X31] two waiters on the same stale lock: only one takes it, the other refuses (10 races)",
          all(t[0] for t in both), [t for t in both if not t[0]][:2])
    check("[CP5-X31] ... and the one that took it removes its own lock when it is done", not any(t[3] for t in both),
          both)
    root = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5v-"))
    (root / ".ratchet").mkdir()
    (root / ".ratchet" / "state.lock").write_text("999 live")
    why = m._StateLock(root, wait=0.3).acquire()
    check("[CP5-X31] a live (fresh) lock is waited on and refused, never cleared",
          "still changing" in str(why) and (root / ".ratchet" / "state.lock").read_text() == "999 live", why)

    # --- X32 (X28 without a sleep): approve-plan waits on a held lock, then takes and removes its own ------------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "yes")
    lock = cp5w_lock(d)
    lock.write_text("12345 held-by-test")
    marker = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5v-m-")) / "marker"
    proc = cp5v_start(d, env, marker, 30, "approve-plan")
    seen = cp5v_until(lambda: "waiting" in cp5v_lines(marker))
    waiting = seen and proc.poll() is None and cp3_state(d)["phase"] == "awaiting_plan_approval"
    lock.unlink()
    out, err = proc.communicate(timeout=60)
    check("[CP5-X32] approve-plan is seen waiting on a held lock (its own wait marker), approving nothing meanwhile",
          waiting, (seen, proc.returncode, err[-300:]))
    lines = cp5v_lines(marker)
    check("[CP5-X32] ... once released it approves while holding its own lock, then removes that lock",
          proc.returncode == 0 and cp3_state(d)["phase"] == "building" and "saved-locked" in lines
          and "saved-unlocked" not in lines and not lock.exists(), (proc.returncode, err[-300:], lines))

    # --- X33: the override code is typed back before the lock is taken -------------------------------------------
    if not HAVE_PTY:
        print("  - [CP5-X33] not run: no pty on this platform (Windows)")
    else:
        env = make_env("claude")
        d, tp = cp5_plan(env)
        held = []

        def answer(master, output):
            if cp4_find_code(output) and not held:
                held.append(cp5w_lock(d).exists())
            return cp4_answer_correct(master, output)

        rc, out = cp4_pty_run(["approve-plan"], d, dict(env, RATCHET_HUMAN_OVERRIDE="1"), answer)
        check("[CP5-X33] approve-plan with the override holds no lock while it waits for the code",
              held == [False], (held, out[-300:]))
        check("[CP5-X33] ... and still approves once the code is typed back, leaving no lock",
              rc == 0 and cp3_state(d)["phase"] == "building" and not cp5w_lock(d).exists(), (rc, out[-300:]))

    # --- X34: the prompt hook never waits on, or writes, the state lock; the stop hook refuses while it's held -------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    state_file = Path(d) / ".ratchet" / "runs" / "f" / "state.json"
    before, log_before = state_file.read_bytes(), len(cp4_human_log_lines(d))
    cp5w_lock(d).write_text("12345 held-by-test")
    tp.open("a").write(json.dumps({"type": "user", "message": {"role": "user", "content": "yes"},
                                   "timestamp": now_dt().isoformat()}) + "\n")
    marker = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5v-m-")) / "marker"
    t0 = time.monotonic()
    proc = cp5v_start(d, env, marker, 0.5, "hook", "prompt", "--host", "claude")
    out, err = proc.communicate(json.dumps({"cwd": str(d), "prompt": "yes", "session_id": "s-owner",
                                            "transcript_path": str(tp)}), timeout=60)
    took = time.monotonic() - t0
    check("[CP5-X34] the prompt hook records the reply at once even with the state lock held, never waiting on it, "
          "and leaves state.json (which only the command holding the lock may write) untouched",
          proc.returncode == 0 and took < 10 and "waiting" not in cp5v_lines(marker)
          and len(cp4_human_log_lines(d)) == log_before + 1 and cp4_human_log_lines(d)[-1].get("prompt") == "yes"
          and state_file.read_bytes() == before, (proc.returncode, took, cp5v_lines(marker), err[-300:]))
    marker = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5v-m-")) / "marker"
    proc = cp5v_start(d, env, marker, 0.5, "hook", "stop", "--host", "claude")
    out, err = proc.communicate(json.dumps({"session_id": "s-owner", "cwd": str(d), "hook_event_name": "Stop",
                                            "stop_hook_active": False}), timeout=60)
    check("[CP5-X34] the stop hook can't take a held lock: it changes nothing and does not let the agent stop "
          "silently", "could not be checked" in out + err and state_file.read_bytes() == before, (out, err[-300:]))
    cp5w_lock(d).unlink()
    r = rx(d, env, "approve-plan")
    check("[CP5-X34] ... so the yes the prompt hook recorded while the lock was held still approves it, once the "
          "lock is gone: nothing it recorded was ever lost", r.returncode == 0
          and cp3_state(d)["phase"] == "building", (r.returncode, r.stderr[-200:]))
    # a message arriving while approve-plan holds the lock is recorded at once; it never waits, and is never lost
    env = make_env("claude")
    d, tp = cp5_plan(env)
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "yes")
    gate = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5v-g-")) / "go"
    m1 = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5v-m-")) / "marker"
    m2 = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5v-m-")) / "marker"
    approver = cp5v_start(d, env, m1, 30, "approve-plan", gate=gate)
    in_save = cp5v_until(lambda: "in-save" in cp5v_lines(m1))
    t0 = time.monotonic()
    hook = cp5v_start(d, env, m2, 30, "hook", "prompt", "--host", "claude")
    h_out, h_err = hook.communicate(json.dumps({"cwd": str(d), "prompt": "thanks, carry on", "session_id": "s-owner"}),
                                    timeout=60)
    hook_took = time.monotonic() - t0
    gate.write_text("go")
    a_out, a_err = approver.communicate(timeout=60)
    st = cp3_state(d)
    check("[CP5-X34] a prompt arriving while approve-plan holds the lock returns at once, never waiting on it",
          in_save and hook.returncode == 0 and hook_took < 10 and "waiting" not in cp5v_lines(m2),
          (in_save, hook_took, cp5v_lines(m2), h_err[-300:]))
    check("[CP5-X34] ... and nothing approve-plan wrote is lost; the message is still recorded",
          approver.returncode == 0 and st["phase"] == "building" and not st.get("pending")
          and len(cp5x_history(d, "plan_approved")) == 1
          and cp4_human_log_lines(d)[-1].get("prompt") == "thanks, carry on" and not cp5w_lock(d).exists(),
          (approver.returncode, a_err[-200:], st.get("phase"), st.get("pending")))

    # --- X35: the guard blocks a real `ratchet.py hook ...` call, never text that only mentions one --------------
    env = make_env("claude")
    d, tp = cp5_plan(env)
    mentions = ['grep -n "hook_prompt\\|hook-prompt" skills/ratchet-orchestrator/scripts/ratchet.py',
                'echo "the agent CLI runs ratchet.py hook prompt for you"',
                'git commit -m "say what hook-stop does"',
                "grep -rn 'hook stop' README.md  # the hook-guard section too",
                "cat SKILL.md | grep hook-guard"]
    wrongly = [(c, r.stderr[-200:]) for c in mentions for r in [cp5y_guard_bash(d, env, c, "s-owner")]
               if r.returncode != 0]
    check("[CP5-X35] grep / echo / a commit message / a comment that mention a hook are not blocked", not wrongly,
          wrongly)
    calls = ["python3 x/ratchet.py hook prompt", "$RATCHET hook-prompt --host claude",
             "env FOO=1 python3 /x/ratchet.py hook stop", "bash -c 'python3 ratchet.py hook prompt'",
             "timeout 5 python3 -u ratchet.py --root . hook guard", "$R hook prompt",
             "echo $(date) | python3 ratchet.py hook prompt", "eval 'python3 ratchet.py hook-stop'",
             "cat p.json | ./ratchet.py hook prompt --host claude"]
    missed = [(c, r.returncode) for c in calls for r in [cp5y_guard_bash(d, env, c, "s-owner")]
              if r.returncode != 2 or "called by your agent CLI" not in r.stderr]
    check("[CP5-X35] a real call of the hook command (direct, $VAR, env, bash -c, timeout, eval, or a command the "
          "guard can't read) is still blocked", not missed, missed)

    # --- X22 (behavior, in place of the private-name check above): ; && || | and newlines separate calls --------
    got = m._engine_invocations("echo hi; $RATCHET ask approve-plan && $RATCHET ask cancel || true | cat\n"
                                "$RATCHET init x")
    check("[CP5-X22] calls after ; && || | and a newline are each read as their own call",
          got == [["ask", "approve-plan"], ["ask", "cancel"], ["init", "x"]], got)


SECTIONS.append(("CP5", cp5_adv_round5_lock_fails_safe))


# ---- CP5 adversarial round 6 ----
# A long engine command (behavior runs the project's tests, for up to test_timeout_sec) holds the state lock for its
# whole run (the user's round-7 decision: serialise, don't merge — see learnings.md). So an approval made while it
# runs is never lost, but it is not applied either: it is refused at once as busy, and goes through once behavior has
# finished, on the yes the prompt hook already recorded (the hook itself never waits on the lock or writes state.json).

def cp5_adv_round6_long_command_keeps_approval():
    print("CP5 adv round 6: an approval made while behavior runs is refused as busy, then applied once it finishes")
    env = make_env("claude")
    tmp = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5u-"))
    hold, running, go = tmp / "hold", tmp / "running", tmp / "go"
    waiter = tmp / "waiter.py"  # no `sleep` on the hermetic PATH: the test command waits through Python
    waiter.write_text("import os, sys, time\nhold, running, go = sys.argv[1:4]\nif os.path.exists(hold):\n"
                      "    open(running, 'w').close()\n    while not os.path.exists(go):\n        time.sleep(0.05)\n")
    command = f"test -f src/f.js || exit 1; '{sys.executable}' '{waiter}' '{hold}' '{running}' '{go}'"
    d = cp3_project(env, extra_cp={"tests": {"files": ["tests/cp1.test.js"], "command": command}})
    tp = d / "t.jsonl"
    cp3_write_tests(d)
    assert rx(d, env, "tests-written", "CP1").returncode == 0
    assert rx(d, env, "red", "CP1").returncode == 0
    (d / "src/f.js").write_text("export const f = () => 1;\n")
    assert rx(d, env, "implemented", "CP1").returncode == 0
    r_ask = rx(d, env, "ask", "set-review", "each")
    hold.write_text("1")
    proc = subprocess.Popen([sys.executable, str(ENGINE), "behavior", "CP1"], cwd=d, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    said, r_set, set_s, mid = None, None, 99, {}
    try:
        started = cp5v_until(lambda: running.exists())
        lock_text = cp5w_lock(d).read_text() if cp5w_lock(d).exists() else ""
        said = cp5_say(d, tp, env, "yes")
        r_set, set_s = cp5r7_timed(d, env, "set-review", "each")
        mid = cp3_state(d)
    finally:
        go.write_text("go")
    out, err = proc.communicate(timeout=120)
    st = cp3_state(d)
    check("[CP5-X36] behavior holds the state lock through its whole test run, and the lock file names it",
          r_ask.returncode == 0 and started and "behavior" in lock_text.split(),
          (r_ask.stderr[-200:], started, lock_text))
    check("[CP5-X36] the user's yes typed while behavior runs is recorded at once by the prompt hook, which neither "
          "waits for the lock nor writes state.json",
          said is not None and said.returncode == 0 and cp4_human_log_lines(d)[-1].get("prompt") == "yes",
          said and said.stderr[-200:])
    check("[CP5-X36] a set-review approved while behavior runs is refused quickly as busy, and changes nothing",
          r_set is not None and cp5r7_busy(r_set, "behavior") and set_s < 15
          and mid.get("human_review") != "each" and (mid.get("pending") or {}).get("action") == "set-review",
          (set_s, r_set and r_set.stderr[-300:], mid.get("human_review"), mid.get("pending")))
    r_after = rx(d, env, "set-review", "each")
    check("[CP5-X36] once behavior has finished, that same approval goes through on the yes already recorded "
          "(nothing was ever lost), and behavior's own result is saved too, leaving no lock",
          proc.returncode == 0 and r_after.returncode == 0 and cp3_state(d).get("human_review") == "each"
          and st["checkpoints"]["CP1"]["gates"]["behavior"]["status"] == "passed"
          and len(cp5x_history(d, "behavior_passed")) == 1 and not cp5w_lock(d).exists(),
          (proc.returncode, err[-200:], r_after.stderr[-300:], st["checkpoints"]["CP1"]["gates"]["behavior"]))


SECTIONS.append(("CP5", cp5_adv_round6_long_command_keeps_approval))


# ---- CP5 adversarial round 7 ----
# Ratchet serialises instead of merging (the user's decision, learnings.md, CP5 round 7): every command that changes a
# run holds the state lock for its whole run, its test runs and regression suite included. A command that would wait
# behind a long one refuses at once as busy and changes nothing. The prompt hook never takes the lock and never
# writes state.json; the stop hook keeps its count of refused stops in its own file.

_CP5R7_WAITER = ("import os, sys, time\nhold, running, go = sys.argv[1:4]\nif os.path.exists(hold):\n"
                 "    open(running, 'w').close()\n    while not os.path.exists(go):\n        time.sleep(0.05)\n")


def cp5r7_gate(tmp, name):
    """A shell command that, while `<name>-hold` exists, notes it started and waits for `<name>-go` (no `sleep` on
    the hermetic PATH). Returns (hold, running, go, command)."""
    waiter = tmp / "waiter.py"
    waiter.write_text(_CP5R7_WAITER)
    hold, running, go = tmp / f"{name}-hold", tmp / f"{name}-running", tmp / f"{name}-go"
    return hold, running, go, f"'{sys.executable}' '{waiter}' '{hold}' '{running}' '{go}'"


def cp5r7_project(env):
    """cp3_project, with a behavior test command and a regression suite that each wait on marker files when held.
    CP1 is implemented and waiting for its behavior gate. Returns (d, tp, {"behavior": .., "regression": ..})."""
    tmp = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r7-g-"))
    beh, reg = cp5r7_gate(tmp, "behavior"), cp5r7_gate(tmp, "regression")
    d = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r7-"))
    git(d, "init", "-q")
    git(d, "config", "user.email", "t@t")
    git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    tp = tmp / "t.jsonl"  # outside the project: a message typed after a gate passed must not change the code
    tp.write_text("")
    (d / ".ratchet").mkdir(exist_ok=True)
    cp5_say(d, tp, env, "please build the feature")
    r = rx(d, env, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr + r.stdout
    rx(d, env, "config", "--set", "min_review_seconds=0", "providers=[]", f"regression_command={reg[3]}")
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
          "tests": {"files": ["tests/cp1.test.js"], "command": f"test -f src/f.js || exit 1; {beh[3]}"}}
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                       "checkpoints": [cp]}))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    r = rx(d, env, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    rx(d, env, "ask", "approve-plan")
    cp5_say(d, tp, env, "yes, go ahead")
    r = rx(d, env, "approve-plan")
    assert r.returncode == 0, r.stderr + r.stdout
    cp3_write_tests(d)
    assert rx(d, env, "tests-written", "CP1").returncode == 0
    assert rx(d, env, "red", "CP1").returncode == 0
    (d / "src/f.js").write_text("export const f = () => 1;\n")
    assert rx(d, env, "implemented", "CP1").returncode == 0
    return d, tp, {"behavior": beh[:3], "regression": reg[:3]}


def cp5r7_at_complete(d, env):
    """Behavior and adversarial gates passed: CP1 waits for `complete`."""
    r = rx(d, env, "behavior", "CP1")
    assert r.returncode == 0, r.stderr + r.stdout
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    good = cp3_reviewer_writes(d, env, rnd, rid)
    r = rx(d, env, "adv-record", "CP1", "--reviews", good)
    assert r.returncode == 0, r.stderr + r.stdout


def cp5r7_start(d, env, *args):
    return subprocess.Popen([sys.executable, str(ENGINE), *args], cwd=d, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)


def cp5r7_timed(d, env, *args):
    t0 = time.monotonic()
    r = rx(d, env, *args)
    return r, time.monotonic() - t0


def cp5r7_busy(r, holder):
    return (r.returncode != 0 and f"Ratchet is busy running {holder}" in r.stderr
            and "try again when it finishes" in r.stderr)


def cp5r7_commits(d):
    return int(git(d, "rev-list", "--count", "HEAD").stdout.strip())


_CP5R7_FAIL_AFTER_COMMIT = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("ratchet_cp5r7", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
def failing_save(self, st):
    m.die("simulated failure after the checkpoint commit")
m.Ctx.save_state = failing_save
m.main(sys.argv[2:])
"""


def cp5_adv_round7_one_command_at_a_time():
    print("CP5 adv round 7: a command that changes a run holds the state lock for its whole run; hooks never wait")
    m = cp5y_load_engine()

    # --- X38: a cancel approved while behavior's tests run is refused as busy, and works once behavior is done -----
    env = make_env("claude")
    d, tp, gates = cp5r7_project(env)
    hold, running, go = gates["behavior"]
    r_ask = rx(d, env, "ask", "cancel")
    hold.write_text("1")
    proc = cp5r7_start(d, env, "behavior", "CP1")
    lock_text, said, said_s, r_cancel, cancel_s, r_second, second_s, mid = "", None, 99, None, 99, None, 99, {}
    try:
        started = cp5v_until(lambda: running.exists())
        lock_text = cp5w_lock(d).read_text() if cp5w_lock(d).exists() else ""
        t0 = time.monotonic()
        said = cp5_say(d, tp, env, "yes")
        said_s = time.monotonic() - t0
        r_cancel, cancel_s = cp5r7_timed(d, env, "cancel")
        r_second, second_s = cp5r7_timed(d, env, "behavior", "CP1")
        mid = cp3_state(d)
    finally:
        go.write_text("go")
    out, err = proc.communicate(timeout=120)
    st = cp3_state(d)
    check("[CP5-X38] behavior holds the state lock through its whole test run, and the lock file names it",
          r_ask.returncode == 0 and started and "behavior" in lock_text.split(), (r_ask.stderr[-200:], lock_text))
    check("[CP5-X38] the user's yes typed meanwhile is recorded at once (the prompt hook neither waits nor refuses)",
          said is not None and said.returncode == 0 and said_s < 10
          and cp4_human_log_lines(d)[-1].get("prompt") == "yes", (said_s, said and said.stderr[-200:]))
    check("[CP5-X38] a cancel approved while behavior runs is refused quickly as busy, telling the agent to run it "
          "again when behavior finishes, and changes nothing",
          r_cancel is not None and cp5r7_busy(r_cancel, "behavior") and cancel_s < 15
          and mid.get("phase") == "building" and (mid.get("pending") or {}).get("action") == "cancel",
          (cancel_s, r_cancel and r_cancel.stderr[-300:], mid.get("phase")))
    r_after = rx(d, env, "cancel")
    check("[CP5-X38] once behavior has finished, the same cancel works on the yes already recorded",
          proc.returncode == 0 and r_after.returncode == 0 and cp3_state(d)["phase"] == "cancelled"
          and not cp5w_lock(d).exists(), (proc.returncode, err[-200:], r_after.stderr[-300:]))

    # --- X39: two behavior runs at once: the second refuses as busy; only the first one's result counts ---------
    g = st["checkpoints"]["CP1"]["gates"]["behavior"]
    ev = d / ".ratchet/runs/f/evidence/CP1"
    check("[CP5-X39] a second behavior run while the first is still running is refused quickly as busy",
          r_second is not None and cp5r7_busy(r_second, "behavior") and second_s < 15,
          (second_s, r_second and r_second.stderr[-300:]))
    check("[CP5-X39] ... so the gate, its history and its evidence agree: one attempt, one pass, one log",
          g.get("status") == "passed" and g.get("attempts") == 1 and len(cp5x_history(d, "behavior_passed")) == 1
          and (ev / "behavior-01.log").exists() and not (ev / "behavior-02.log").exists(),
          (g, [e.get("event") for e in st.get("history", [])][-6:]))

    # --- X40: the prompt hook never writes state.json and never waits on the lock -------------------------------
    env = make_env("claude")
    d, tp = cp5y_first_run_plan(env)  # no chat owns this run yet: the hook used to write the owner into state.json
    sp = d / ".ratchet/runs/f/state.json"
    before = sp.read_bytes()
    cp5w_lock(d).write_text(f"{os.getpid()} cp5r7-token behavior")  # a live, long command holds the lock
    marker = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r7-m-")) / "marker"
    t0 = time.monotonic()
    hook = cp5v_start(d, env, marker, 30, "hook", "prompt", "--host", "claude")
    h_out, h_err = hook.communicate(json.dumps({"cwd": str(d), "prompt": "ok, show me the plan", "session_id": "s-A",
                                                "transcript_path": str(tp)}), timeout=60)
    took = time.monotonic() - t0
    check("[CP5-X40] while a long command holds the lock, the prompt hook returns at once, never waiting on it",
          hook.returncode == 0 and took < 10 and "waiting" not in cp5v_lines(marker),
          (hook.returncode, took, cp5v_lines(marker), h_err[-300:]))
    check("[CP5-X40] ... it records the message and leaves state.json byte for byte unchanged, even as the first "
          "chat of a run no chat owns", sp.read_bytes() == before
          and cp4_human_log_lines(d)[-1].get("prompt") == "ok, show me the plan" and cp5w_lock(d).exists(),
          (cp4_human_log_lines(d)[-1:], h_err[-200:]))
    t0 = time.monotonic()
    said = cp5_say(d, tp, env, "and the plan's second step?", session="s-A")
    check("[CP5-X40] a second message, with the lock still held, is recorded the same way, state.json untouched",
          said.returncode == 0 and time.monotonic() - t0 < 10 and sp.read_bytes() == before
          and cp4_human_log_lines(d)[-1].get("prompt") == "and the plan's second step?", said.stderr[-200:])
    cp5w_lock(d).unlink()
    r = rx(d, env, "ask", "approve-plan")
    st = cp3_state(d)
    rec = cp5x_history(d, "owner_recorded")
    check("[CP5-X40] the next command, under its own lock, makes that first chat the run's owner from the hook's "
          "record", r.returncode == 0 and st.get("owner_session") == "s-A" and bool(rec)
          and rec[-1].get("via") == "prompt" and (st.get("pending") or {}).get("session") == "s-A",
          (r.returncode, r.stderr[-200:], st.get("owner_session"), rec))

    # --- X41: the stop hook counts refused stops in its own file, never in state.json ---------------------------
    env = make_env("claude")
    d = cp3_project(env)
    sp = d / ".ratchet/runs/f/state.json"
    before = sp.read_bytes()
    stop = json.dumps({"session_id": "s-owner", "cwd": str(d), "hook_event_name": "Stop", "stop_hook_active": False})
    r1 = rx(d, env, "hook", "stop", "--host", "claude", stdin=stop)
    r2 = rx(d, env, "hook", "stop", "--host", "claude", stdin=stop)
    blocks = d / ".ratchet/runs/f/stop-blocks.jsonl"
    recs = [json.loads(ln) for ln in blocks.read_text().splitlines() if ln.strip()] if blocks.exists() else []
    check("[CP5-X41] the stop hook refuses a stop mid-build, counts it in its own record file, and leaves "
          "state.json byte for byte unchanged", "RATCHET GATE" in r1.stdout + r1.stderr
          and "RATCHET GATE" in r2.stdout + r2.stderr and sp.read_bytes() == before
          and [x.get("phase") for x in recs] == ["building", "building"], (r1.stdout[-200:], recs))

    # --- X42: complete holds the lock through its regression suite --------------------------------------------
    env = make_env("claude")
    d, tp, gates = cp5r7_project(env)
    cp5r7_at_complete(d, env)
    hold, running, go = gates["regression"]
    r_ask = rx(d, env, "ask", "set-review", "each")
    cp5_say(d, tp, env, "yes")
    hold.write_text("1")
    proc = cp5r7_start(d, env, "complete", "CP1")
    lock_text, r_set, set_s, mid = "", None, 99, {}
    try:
        started = cp5v_until(lambda: running.exists())
        lock_text = cp5w_lock(d).read_text() if cp5w_lock(d).exists() else ""
        r_set, set_s = cp5r7_timed(d, env, "set-review", "each")
        mid = cp3_state(d)
    finally:
        go.write_text("go")
    out, err = proc.communicate(timeout=120)
    st = cp3_state(d)
    check("[CP5-X42] complete holds the state lock through its regression suite, and the lock file names it",
          r_ask.returncode == 0 and started and "complete" in lock_text.split(), (r_ask.stderr[-200:], lock_text))
    check("[CP5-X42] a review-mode change approved meanwhile is refused quickly as busy and changes nothing",
          r_set is not None and cp5r7_busy(r_set, "complete") and set_s < 15 and mid.get("human_review") == "end"
          and mid["checkpoints"]["CP1"]["status"] != "done", (set_s, r_set and r_set.stderr[-300:]))
    check("[CP5-X42] ... complete itself finishes and leaves no lock",
          proc.returncode == 0 and st["checkpoints"]["CP1"]["status"] == "done" and not cp5w_lock(d).exists(),
          (proc.returncode, err[-300:]))
    r_set = rx(d, env, "set-review", "each")
    check("[CP5-X42] ... after which the same change goes through on the yes already recorded",
          r_set.returncode == 0 and cp3_state(d).get("human_review") == "each", r_set.stderr[-300:])
    r_again = rx(d, env, "set-review", "none")
    check("[CP5-X42] ... and that answered question stays spent: nothing brings it back for a second use",
          r_again.returncode != 0 and not cp3_state(d).get("pending") and cp3_state(d).get("human_review") == "each"
          and len(cp5x_history(d, "review_mode_changed")) == 1, (r_again.returncode, r_again.stderr[-200:]))

    # --- X43: running complete again after it failed past its commit makes no second commit ---------------------
    env = make_env("claude")
    d, tp, gates = cp5r7_project(env)
    cp5r7_at_complete(d, env)
    base = cp5r7_commits(d)
    fail = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r7-f-")) / "fail_after_commit.py"
    fail.write_text(_CP5R7_FAIL_AFTER_COMMIT)
    r1 = subprocess.run([sys.executable, str(fail), str(ENGINE), "complete", "CP1"], cwd=d, env=env,
                        capture_output=True, text=True)
    head1 = git(d, "rev-parse", "HEAD").stdout.strip()[:7]
    check("[CP5-X43] (setup) complete made the checkpoint commit, then failed before saving, leaving no lock",
          r1.returncode != 0 and "simulated failure" in r1.stderr and cp5r7_commits(d) == base + 1
          and cp3_state(d)["checkpoints"]["CP1"]["status"] != "done" and not cp5w_lock(d).exists(),
          (r1.returncode, r1.stderr[-300:], cp5r7_commits(d), base))
    r2 = rx(d, env, "complete", "CP1")
    st = cp3_state(d)
    check("[CP5-X43] running complete again makes no second checkpoint commit and records the first one",
          r2.returncode == 0 and cp5r7_commits(d) == base + 1 and st["checkpoints"]["CP1"].get("commit") == head1
          and st["checkpoints"]["CP1"]["status"] == "done" and len(cp5x_history(d, "checkpoint_complete")) == 1,
          (r2.returncode, r2.stderr[-300:], cp5r7_commits(d), st["checkpoints"]["CP1"].get("commit"), head1))

    # --- X44: a live command's lock is never taken for a crashed one's while a test run may still be going ------
    env = make_env("claude")
    d, tp = cp5y_first_run_plan(env)
    lock = cp5w_lock(d)
    lock.write_text("12345 cp5r7-token behavior")
    t = m.DEFAULT_CONFIG["test_timeout_sec"]
    aged = time.time() - (t + 60)  # older than the longest test run, still within the margin
    os.utime(lock, (aged, aged))
    r = rx(d, env, "ask", "approve-plan")
    check("[CP5-X44] a lock last refreshed longer ago than test_timeout_sec, but within the margin, still counts "
          "as held: the command refuses as busy and the lock stays",
          cp5r7_busy(r, "behavior") and lock.exists() and not cp3_state(d).get("pending"), r.stderr[-300:])
    rx(d, env, "config", "--set", f"test_timeout_sec={t * 4}")
    aged = time.time() - (t + m.STATE_LOCK_STALE_MARGIN_SECONDS + 60)  # stale by the default, live by this config
    os.utime(lock, (aged, aged))
    r = rx(d, env, "ask", "approve-plan")
    check("[CP5-X44] a project's longer test_timeout_sec lengthens the wait before a lock counts as stale",
          cp5r7_busy(r, "behavior") and lock.exists(), r.stderr[-300:])
    aged = time.time() - (t * 4 + m.STATE_LOCK_STALE_MARGIN_SECONDS + 60)
    os.utime(lock, (aged, aged))
    r = rx(d, env, "ask", "approve-plan")
    check("[CP5-X44] a lock older than test_timeout_sec plus the margin is left from a crash: cleared, and the "
          "command runs", r.returncode == 0 and not lock.exists()
          and (cp3_state(d).get("pending") or {}).get("action") == "approve-plan", r.stderr[-300:])


SECTIONS.append(("CP5", cp5_adv_round7_one_command_at_a_time))


# ---- CP5 adversarial round 8 ----
# init takes the state lock even in a project with no Ratchet folder yet (it makes the folder first), and the
# per-user hash record's own lock, file-hashes.lock, keeps every hash when hooks append while a locked command runs.

_CP5R8_APPENDER = """
import importlib.util, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("ratchet_cp5r8", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
target, start, n = Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4])
while not start.exists():
    time.sleep(0.002)
for i in range(n):
    m.append_recorded(target, '{"i": %d}\\n' % i)
"""




def cp5r8_hashes(env):
    p = Path(env["RATCHET_HOME"]) / "file-hashes.json"
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def cp5r8_jsonl_ok(p):
    """(every line parses as JSON, the parsed lines)."""
    try:
        return True, [json.loads(ln) for ln in Path(p).read_text().splitlines() if ln.strip()]
    except Exception:
        return False, []


def cp5r8_prompt_proc(d, env, tp, text):
    tp.open("a").write(json.dumps({"type": "user", "message": {"role": "user", "content": text},
                                   "timestamp": now_dt().isoformat()}) + "\n")
    p = subprocess.Popen([sys.executable, str(ENGINE), "hook", "prompt", "--host", "claude"], cwd=d, env=env,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    p.stdin.write(json.dumps({"cwd": str(d), "prompt": text, "session_id": "s-owner", "transcript_path": str(tp)}))
    p.stdin.close()
    return p


def cp5_adv_round8_init_lock_and_hash_record_lock():
    print("CP5 adv round 8: first inits are serialised; the hash record's lock keeps every hash under a race")

    # --- X45: concurrent first-run inits in a project with no Ratchet folder: exactly one runs -----------------
    env = make_env("claude")
    d = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r8-i-"))
    git(d, "init", "-q")
    git(d, "config", "user.email", "t@t")
    git(d, "config", "user.name", "t")
    (d / "a.txt").write_text("x\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    had_folder = (d / ".ratchet").exists()
    procs = [subprocess.Popen([sys.executable, str(ENGINE), "init", f"feature {i}", "--slug", f"s{i}"], cwd=d,
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(4)]
    results = [(p,) + p.communicate(timeout=120) for p in procs]
    won = [i for i, (p, _, _) in enumerate(results) if p.returncode == 0]
    lost = [results[i] for i in range(4) if i not in won]
    runs = sorted(x.name for x in (d / ".ratchet/runs").iterdir()) if (d / ".ratchet/runs").is_dir() else []
    active = (json.loads((d / ".ratchet/active.json").read_text()) or {}).get("slug") \
        if (d / ".ratchet/active.json").exists() else None
    check("[CP5-X45] four inits started at once in a project with no Ratchet folder: exactly one starts a run",
          not had_folder and len(won) == 1 and runs == [f"s{won[0]}" if won else None] and active == runs[0],
          (won, runs, active, [(p.returncode, e[-200:]) for p, _, e in results]))
    check("[CP5-X45] ... every other init refuses plainly, naming the run already active, with no traceback",
          len(lost) == 3 and all(p.returncode != 0 and "is still active" in e and "Traceback" not in e
                                 for p, _, e in lost), [(p.returncode, e[-300:]) for p, _, e in lost])
    check("[CP5-X45] ... and no lock is left behind", not cp5w_lock(d).exists()
          and not (d / ".ratchet/state.lock.break").exists())
    r = rx(d, env, "init", "another", "--slug", "s9")
    check("[CP5-X45] a later init still refuses while that run is active (the lock changed nothing else)",
          r.returncode != 0 and "is still active" in r.stderr and not (d / ".ratchet/runs/s9").exists(),
          r.stderr[-300:])

    # --- X46: hooks and other writers race on the hash record while a locked command runs and saves ------------
    env = make_env("claude")
    d, tp, gates = cp5r7_project(env)
    hold, running, go = gates["behavior"]
    rh = Path(env["RATCHET_HOME"])
    tmp = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r8-a-"))
    script = tmp / "appender.py"
    script.write_text(_CP5R8_APPENDER)
    start = tmp / "start"
    n_app, n_lines, n_prompts, n_stops = 3, 25, 8, 3
    targets = [d / f".ratchet/runs/f/cp5r8-race-{i}.jsonl" for i in range(n_app)]
    hold.write_text("1")
    beh = cp5r7_start(d, env, "behavior", "CP1")
    started = cp5v_until(lambda: running.exists())
    appenders = [subprocess.Popen([sys.executable, str(script), str(ENGINE), str(t), str(start), str(n_lines)],
                                  cwd=d, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for t in targets]
    texts = [f"cp5r8 message {i}" for i in range(n_prompts)]
    prompts = [cp5r8_prompt_proc(d, env, tp, t) for t in texts]
    stop = json.dumps({"session_id": "s-owner", "cwd": str(d), "hook_event_name": "Stop", "stop_hook_active": False})
    stops = []
    for _ in range(n_stops):
        sp_ = subprocess.Popen([sys.executable, str(ENGINE), "hook", "stop", "--host", "claude"], cwd=d, env=env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        sp_.stdin.write(stop)
        sp_.stdin.close()
        stops.append(sp_)
    start.write_text("go")
    go.write_text("go")  # behavior saves state.json (and its hash) while the hooks and appenders still run
    p_res = [(p.wait(timeout=120), p.stderr.read()) for p in prompts]
    s_res = [(p.wait(timeout=120), p.stderr.read()) for p in stops]
    a_res = [(p.wait(timeout=120), p.stderr.read()) for p in appenders]
    b_out, b_err = beh.communicate(timeout=120)
    log_ok, log = cp5r8_jsonl_ok(d / ".ratchet/human_log.jsonl")
    said = [x.get("prompt") for x in log]
    check("[CP5-X46] (setup) behavior held the state lock while every hook and appender ran, and all finished "
          "(the stop hooks refusing the stop mid-build, exit 2, as a stop hook does)",
          started and beh.returncode == 0 and all(c == 0 for c, _ in p_res + a_res)
          and all(c == 2 and "RATCHET" in e for c, e in s_res),
          (started, beh.returncode, b_err[-200:], [(c, e[-200:]) for c, e in p_res + a_res if c != 0],
           [c for c, _ in s_res]))
    check("[CP5-X46] every message typed during the race is in human_log.jsonl exactly once, every line intact",
          log_ok and all(said.count(t) == 1 for t in texts), (log_ok, said[-10:]))
    app_ok = []
    for t in targets:
        ok_, recs = cp5r8_jsonl_ok(t)
        app_ok.append(ok_ and [x.get("i") for x in recs] == list(range(n_lines)))
    st_ok, blocks = cp5r8_jsonl_ok(d / ".ratchet/runs/f/stop-blocks.jsonl")
    check("[CP5-X46] every concurrent append_recorded line landed, in order, and the stop hooks' own record file "
          "has only whole lines", all(app_ok) and st_ok, (app_ok, st_ok, blocks[-5:]))
    rec = cp5r8_hashes(env)
    files = [d / ".ratchet/human_log.jsonl", d / ".ratchet/runs/f/state.json", *targets]
    want = {str(f.resolve()): cp5r8_sha(f) for f in files}
    got = {k: (rec or {}).get(k) for k in want}
    check("[CP5-X46] file-hashes.json is whole and holds the current hash of every file written in the race: "
          "no writer dropped another's hash", rec is not None and got == want,
          [Path(k).name for k in want if got.get(k) != want[k]])
    leftovers = sorted(x.name for x in rh.iterdir() if x.name == "file-hashes.lock" or x.name.endswith(".tmp"))
    check("[CP5-X46] ... and neither file-hashes.lock nor a half-written record file is left behind",
          leftovers == [], leftovers)
    r_status = rx(d, env, "status")
    after = cp4_say_yes(d, tp, env, text="cp5r8 after the race")
    check("[CP5-X46] the chain verifies afterwards: a command reading state.json and a prompt hook checking "
          "human_log.jsonl both go on, with no tampering reported",
          r_status.returncode == 0 and after.returncode == 0 and "outside ratchet.py" not in
          r_status.stderr + after.stderr + after.stdout and cp4_human_log_lines(d)[-1].get("prompt")
          == "cp5r8 after the race", (r_status.stderr[-300:], after.stderr[-300:]))

    # --- X47: the hash record's lock is really honoured, and a stale one is cleared ----------------------------
    lock = rh / "file-hashes.lock"
    lock.write_text("")
    t0 = time.monotonic()
    p = cp5r8_prompt_proc(d, env, tp, "cp5r8 held")
    time.sleep(2.0)  # the lock waits up to 5 seconds before going on without it
    waited = p.poll() is None and "cp5r8 held" not in [x.get("prompt") for x in cp4_human_log_lines(d)]
    lock.unlink()
    rc = p.wait(timeout=60)
    took = time.monotonic() - t0
    hl = d / ".ratchet/human_log.jsonl"
    check("[CP5-X47] while file-hashes.lock is held, the prompt hook waits and appends nothing; once it is "
          "released the message is recorded with its hash",
          waited and rc == 0 and took >= 2.0 and cp4_human_log_lines(d)[-1].get("prompt") == "cp5r8 held"
          and (cp5r8_hashes(env) or {}).get(str(hl.resolve())) == cp5r8_sha(hl), (waited, rc, took))
    lock.write_text("")
    aged = time.time() - 120
    os.utime(lock, (aged, aged))
    t0 = time.monotonic()
    r = cp4_say_yes(d, tp, env, text="cp5r8 stale")
    took = time.monotonic() - t0
    check("[CP5-X47] a file-hashes.lock left by a process that died (older than 30 seconds) is cleared at once",
          r.returncode == 0 and took < 4.5 and not lock.exists()
          and cp4_human_log_lines(d)[-1].get("prompt") == "cp5r8 stale"
          and (cp5r8_hashes(env) or {}).get(str(hl.resolve())) == cp5r8_sha(hl), (r.stderr[-300:], took))


SECTIONS.append(("CP5", cp5_adv_round8_init_lock_and_hash_record_lock))



# ---- CP5 adversarial round 9 ----
# The per-user engine-call-sessions.json (which chat ran a real `init` / `ask`) is read-modify-written by every guard
# hook and by the `init` / `ask` that use a note up. Its own lock, engine-call-sessions.lock, keeps every note when
# several write at once; when the lock can't be had, nothing is recorded and the no-note owner rules apply.

_CP5R9_NOTER = """
import importlib.util, json, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("ratchet_cp5r9", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
root, start, w, n, mode = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], int(sys.argv[5]), sys.argv[6]
(start.parent / ("ready-%s" % w)).write_text("1")  # the engine is loaded: this worker now only waits on start
while not start.exists():
    time.sleep(0.002)
got = []
for i in range(n):
    if mode == "note":
        m._note_engine_call(root, "$RATCHET ask choice --tag w%s-%d" % (w, i), "s-w%s-%d" % (w, i))
    else:
        m._ENGINE_ARGV = ["ask", "choice", "--tag", "w%s-%d" % (w, i)]
        got.append(m._noted_session(root, "ask"))
print(json.dumps(got))
"""


def cp5r9_race(script, root, env, workers, n, mode):
    start = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r9-s-")) / "start"
    procs = [subprocess.Popen([sys.executable, str(script), str(ENGINE), str(root), str(start), str(w), str(n), mode],
                              cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for w in range(workers)]
    ready = cp5v_until(lambda: all((start.parent / f"ready-{w}").exists() for w in range(workers)), timeout=120)
    assert ready, "not every race worker loaded the engine"
    start.write_text("go")
    out = []
    for p in procs:
        o, e = p.communicate(timeout=120)
        out.append((p.returncode, o, e))
    return out


def cp5r9_guard_proc(d, env, command, session):
    payload = {"session_id": session, "cwd": str(d), "hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": command}}
    p = subprocess.Popen([sys.executable, str(ENGINE), "hook", "guard", "--host", "claude"], cwd=d, env=env,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    p.stdin.write(json.dumps(payload))
    p.stdin.close()
    return p


def cp5_adv_round9_engine_notes_lock():
    print("CP5 adv round 9: the engine-call notes have their own lock; concurrent guard hooks never drop a note")
    tmp = Path(_tmp.mkdtemp(prefix="ratchet-trust-cp5r9-"))
    script = tmp / "noter.py"
    script.write_text(_CP5R9_NOTER)

    # --- X48: many guard-hook writers note calls at once; every note survives and is used once by its own call --
    env = make_env("claude")
    d = cp5y_repo()
    rh = Path(env["RATCHET_HOME"])
    workers, n = 6, 15
    res = cp5r9_race(script, d, env, workers, n, "note")
    key = f"{d.resolve()}|ask"
    notes = cp5z_notes(env).get(key) or []
    want = {(f"s-w{w}-{i}", ("ask", "choice", "--tag", f"w{w}-{i}")) for w in range(workers) for i in range(n)}
    got = [(x.get("session"), tuple(x.get("argv") or ())) for x in notes if isinstance(x, dict)]
    check("[CP5-X48] six processes noting 15 engine calls each at once: all 90 notes are in "
          "engine-call-sessions.json, each exactly once, the file whole",
          all(r[0] == 0 and "Traceback" not in r[2] for r in res) and len(got) == len(want)
          and set(got) == want, (len(got), len(want), [r[2][-200:] for r in res if r[2]]))
    leftovers = sorted(x.name for x in rh.iterdir() if x.name == "engine-call-sessions.lock" or x.name.endswith(".tmp"))
    check("[CP5-X48] ... and neither engine-call-sessions.lock nor a half-written notes file is left behind",
          leftovers == [], leftovers)
    res = cp5r9_race(script, d, env, workers, n, "use")
    outs = []
    for r in res:
        try:
            outs.append(json.loads(r[1].strip().splitlines()[-1]))
        except (ValueError, IndexError):
            outs.append(None)
    check("[CP5-X48] six processes using those notes up at once: each call gets its own chat, none gets nothing",
          outs == [[f"s-w{w}-{i}" for i in range(n)] for w in range(workers)],
          [(o or [])[:3] for o in outs])
    check("[CP5-X48] ... and every note is gone afterwards: no consumer wrote back a note another had used up",
          cp5z_project_notes(d, env) == {}, {k: len(v) for k, v in cp5z_project_notes(d, env).items()})

    # --- X49: real guard hooks in three first-run projects fire at once; each real `ask` binds its own chat -----
    env = make_env("claude")
    projects = [cp5y_first_run_plan(env)[0] for _ in range(3)]
    procs = [cp5r9_guard_proc(pd, env, f'cd "{pd}" && $RATCHET ask approve-plan', f"s-P{i}")
             for i, pd in enumerate(projects)]
    g_res = [(p.wait(timeout=60), p.stdout.read(), p.stderr.read()) for p in procs]
    check("[CP5-X49] three guard hooks noting real `ask approve-plan` calls at once all allow the call",
          all(c == 0 and "deny" not in o for c, o, _ in g_res), [(c, o[-200:], e[-200:]) for c, o, e in g_res])
    noted = {i: cp5z_project_notes(pd, env) for i, pd in enumerate(projects)}
    check("[CP5-X49] ... and all three notes survive, each naming its own chat",
          all([x.get("session") for x in (noted[i].get(f"{pd.resolve()}|ask") or [])] == [f"s-P{i}"]
              for i, pd in enumerate(projects)), noted)
    owners = []
    for i, pd in enumerate(projects):
        r = rx(pd, env, "ask", "approve-plan")
        st = cp3_state(pd)
        owners.append((r.returncode, st.get("owner_session"), (st.get("pending") or {}).get("session")))
    check("[CP5-X49] each project's real `ask approve-plan` then makes its own chat the owner and the asker",
          owners == [(0, f"s-P{i}", f"s-P{i}") for i in range(3)], owners)
    check("[CP5-X49] ... using each note up", all(cp5z_project_notes(pd, env) == {} for pd in projects),
          [cp5z_project_notes(pd, env) for pd in projects])

    # --- X50: the lock fails safe: held, nothing is noted or used up; a stale one is cleared ------------------
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    d, tp = cp5y_first_run_plan(env)
    rh.mkdir(parents=True, exist_ok=True)
    lock = rh / "engine-call-sessions.lock"
    lock.write_text("")
    t0 = time.monotonic()
    r = cp5y_guard_bash(d, env, "$RATCHET ask approve-plan", "s-B")
    took = time.monotonic() - t0
    check("[CP5-X50] while engine-call-sessions.lock is held, the guard waits briefly, allows the call and notes "
          "nothing", r.returncode == 0 and "deny" not in r.stdout and took >= 1.5
          and cp5z_project_notes(d, env) == {}, (r.returncode, r.stdout[-200:], took, cp5z_project_notes(d, env)))
    lock.unlink()
    cp5y_guard_bash(d, env, "$RATCHET ask approve-plan", "s-B")
    had = cp5z_project_notes(d, env)
    lock.write_text("")
    r = rx(d, env, "ask", "approve-plan")
    st = cp3_state(d)
    check("[CP5-X50] with the lock held, `ask` uses no note and changes none: no chat is made owner or asker, the "
          "no-note rules apply, and the note is left as it was",
          r.returncode == 0 and st.get("owner_session") is None and (st.get("pending") or {}).get("session") is None
          and had and cp5z_project_notes(d, env) == had,
          (r.returncode, r.stderr[-300:], st.get("owner_session"), had, cp5z_project_notes(d, env)))
    lock.unlink()
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    d, tp = cp5y_first_run_plan(env)
    rh.mkdir(parents=True, exist_ok=True)
    lock = rh / "engine-call-sessions.lock"
    lock.write_text("")
    aged = time.time() - 120
    os.utime(lock, (aged, aged))
    t0 = time.monotonic()
    cp5y_guard_bash(d, env, "$RATCHET ask approve-plan", "s-C")
    took = time.monotonic() - t0
    r = rx(d, env, "ask", "approve-plan")
    check("[CP5-X50] an engine-call-sessions.lock left by a process that died (older than 30 seconds) is cleared at "
          "once: the note is taken and the real `ask` binds its chat",
          not lock.exists() and r.returncode == 0 and cp3_state(d).get("owner_session") == "s-C",
          (took, lock.exists(), r.stderr[-300:], cp3_state(d).get("owner_session")))


SECTIONS.append(("CP5", cp5_adv_round9_engine_notes_lock))


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
