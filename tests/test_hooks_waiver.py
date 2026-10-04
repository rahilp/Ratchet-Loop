#!/usr/bin/env python3
"""
Hooks-waiver tests (CP2): the user's choice to build without hooks is recorded only from a real, explicit reply
that came AFTER the agent asked (`ask hooks-waive`, then `hooks-waive`), and is shown by status / next / viewer data.
    python3 tests/test_hooks_waiver.py

Runs in throwaway git projects with a temp HOME / RATCHET_HOME, so nothing touches the real user's files.
The prompt hook writes a heartbeat, exactly as in real use. Only the guard/stop heartbeat is removed to model "the
enforcement hooks never fired", because the user's yes can only be recorded when the prompt hook is alive.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
SCRUB = ("RATCHET_ROLE", "RATCHET_HUMAN_OVERRIDE", "RATCHET_ROOT", "RATCHET_HOST_PROVIDER", "CLAUDECODE",
         "CLAUDE_CODE_ENTRYPOINT", "CODEX_SANDBOX", "CODEX_THREAD_ID", "CODEX_SANDBOX_NETWORK_DISABLED",
         "GEMINI_CLI", "COPILOT_CLI", "GITHUB_COPILOT_CLI", "CODEX_HOME", "COPILOT_HOME", "RATCHET_HOOKS_WAIVE")
HOME = Path(tempfile.mkdtemp(prefix="ratchet-hw-home-"))
ENV = {k: v for k, v in os.environ.items() if k not in SCRUB}
ENV.update(HOME=str(HOME), RATCHET_HOME=str(HOME / "rh"), CODEX_HOME=str(HOME / "codexhome"),
           COPILOT_HOME=str(HOME / "copilothome"), RATCHET_HOST_PROVIDER="anthropic")
TRANSCRIPTS = Path(tempfile.mkdtemp(prefix="ratchet-hw-transcripts-"))
PASS = FAIL = 0

GO = "yes, go ahead without hooks"


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:300]}"))


def rx(d, *args, env=None):
    return subprocess.run([sys.executable, str(ENGINE), *args], cwd=d, capture_output=True, text=True,
                          env=env or ENV)


def git(d, *a):
    return subprocess.run(["git", *a], cwd=d, capture_output=True, text=True)


def clear_hb(d):
    """Model "prompt hook fired, guard/stop never did": drop only the guard/stop entries."""
    p = Path(d) / ".ratchet" / "hook-heartbeat.json"
    if p.exists():
        hb = json.loads(p.read_text())
        for k in ("guard", "stop"):
            hb.pop(k, None)
        p.write_text(json.dumps(hb))


def say(d, text, session="s-owner"):
    """The user types a message: the agent CLI writes it to its transcript and calls the prompt hook."""
    tp = TRANSCRIPTS / f"{Path(d).name}-{session}.jsonl"
    with tp.open("a") as f:
        f.write(json.dumps({"type": "user", "message": {"role": "user", "content": text},
                            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}) + "\n")
    r = subprocess.run([sys.executable, str(ENGINE), "hook", "prompt", "--host", "claude"], cwd=d, text=True,
                       capture_output=True, env=ENV,
                       input=json.dumps({"cwd": str(d), "prompt": text, "session_id": session,
                                         "transcript_path": str(tp)}))
    return r


def guard(d, tool, tool_input):
    return subprocess.run([sys.executable, str(ENGINE), "hook", "guard", "--host", "claude"], cwd=d, text=True,
                          capture_output=True, env=ENV,
                          input=json.dumps({"cwd": str(d), "tool_name": tool, "tool_input": tool_input}))


def fire_stop(d, session="s-owner"):
    return subprocess.run([sys.executable, str(ENGINE), "hook", "stop", "--host", "claude"], cwd=d, text=True,
                          capture_output=True, env=ENV,
                          input=json.dumps({"cwd": str(d), "session_id": session, "hook_event_name": "Stop"}))


def project(name="p", approved=True) -> Path:
    d = Path(tempfile.mkdtemp(prefix=f"ratchet-hw-{name}-"))
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(d, *c)
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    say(d, "please build the feature")
    r = rx(d, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr
    rx(d, "config", "--set", "min_review_seconds=0")
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
          "tests": {"files": ["tests/cp1.test.js"], "command": "node --test tests/cp1.test.js"}}
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "checkpoints": [cp]}))
    r = rx(d, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    if approved:
        say(d, "yes, build it")
        r = rx(d, "approve-plan")
        assert r.returncode == 0, r.stderr + r.stdout
    clear_hb(d)
    return d


def sj(d):
    r = rx(d, "status", "--json")
    try:
        return json.loads(r.stdout) if r.returncode == 0 else {}
    except Exception:
        return {}


def hk(d):
    h = sj(d).get("hooks")
    return h if isinstance(h, dict) else {}


def waived(d):
    return bool(hk(d).get("waived"))


def history(d):
    p = d / ".ratchet/runs/f/state.json"
    return json.loads(p.read_text()).get("history", [])


def waiver_events(d):
    return [e for e in history(d) if "waiv" in str(e.get("event", "")).lower()]


def ask(d):
    return rx(d, "ask", "hooks-waive")


def full_waive(d, text=GO, *extra):
    """The whole legitimate flow: agent asks, user replies, agent records it."""
    a = ask(d)
    say(d, text)
    return a, rx(d, "hooks-waive", *extra)


def refused(r):
    """A real refusal from the command, not an argparse usage error for an unknown command."""
    return r.returncode != 0 and "usage:" not in r.stderr and "invalid choice" not in r.stderr


def out(r):
    return (r.stdout + r.stderr).lower()


def next_json(d):
    r = rx(d, "next")
    try:
        return json.loads(r.stdout) if r.returncode == 0 else {}
    except Exception:
        return {}


def main():
    print("[CP2-T01] a clear go-ahead records that hooks were skipped")
    d = project("t01")
    check("[CP2-T01] hooks not installed and not waived to start with",
          hk(d).get("installed") is False and not waived(d), hk(d))
    a, r = full_waive(d)
    check("[CP2-T01] `ask hooks-waive` is accepted and prints a question", a.returncode == 0 and "ASK THE USER" in a.stdout,
          a.stderr + a.stdout)
    check("[CP2-T01] hooks-waive exits 0 after ask + yes", r.returncode == 0, r.stderr + r.stdout)
    h = hk(d)
    check("[CP2-T01] status --json: hooks.waived truthy although the prompt hook has fired (heartbeat fresh)",
          bool(h.get("waived")) and h.get("fresh") is True and h.get("installed") is False, h)
    nh = (next_json(d).get("hooks") or {})
    check("[CP2-T01] next shows hooks.waived too", bool(nh.get("waived")), nh)
    ev = waiver_events(d)
    check("[CP2-T01] history has a waiver event carrying the user's words",
          len(ev) == 1 and GO in json.dumps(ev[0]), ev)

    print("[CP2-T02] plain status says the user skipped hooks")
    d = project("t02")
    before = rx(d, "status")
    check("[CP2-T02] before waiving, status does not say hooks were skipped",
          before.returncode == 0 and "skipped" not in out(before), before.stdout)
    full_waive(d)
    after = rx(d, "status")
    check("[CP2-T02] after waiving, status mentions hooks were skipped",
          after.returncode == 0 and "skipped" in out(after) and "hooks" in out(after), after.stdout + after.stderr)

    print("[CP2-T03] the agent alone cannot record the waiver")
    d = project("t03")
    ask(d)
    r = rx(d, "hooks-waive")
    check("[CP2-T03] refused with no user message since the question", refused(r), r.stdout)
    check("[CP2-T03] the message says it needs the user", "user" in out(r), r.stderr)
    check("[CP2-T03] waived stays falsy and no waiver event is logged",
          not waived(d) and not waiver_events(d), (hk(d), waiver_events(d)))

    print("[CP2-T04] env vars and flags cannot impersonate the user")
    d = project("t04")
    ask(d)
    env = dict(ENV, RATCHET_HOOKS_WAIVE="1")
    r1 = rx(d, "hooks-waive", env=env)
    check("[CP2-T04] RATCHET_HOOKS_WAIVE=1 does not waive", refused(r1) and not waived(d), r1.stdout + r1.stderr)
    say(d, GO)  # even with a real yes on record, a flag is not an accepted way to waive
    r2 = rx(d, "hooks-waive", "--yes")
    r3 = rx(d, "hooks-waive", "--force")
    check("[CP2-T04] --yes / --force are rejected as unknown flags and do not waive",
          r2.returncode != 0 and r3.returncode != 0 and "unrecognized" in r2.stderr and "unrecognized" in r3.stderr
          and not waived(d), (r2.stderr, r3.stderr))
    check("[CP2-T04] no waiver event is logged", not waiver_events(d), waiver_events(d))
    st = ".ratchet/runs/f/state.json"
    g1 = guard(d, "Bash", {"command": f"echo '{{\"hooks_waived\": true}}' > {st}"})
    g2 = guard(d, "Bash", {"command": "echo '{}' >> .ratchet/human_log.jsonl"})
    check("[CP2-T04] guard blocks hand-editing state.json to set the waiver", g1.returncode == 2, g1.stderr)
    check("[CP2-T04] guard blocks appending to human_log.jsonl", g2.returncode == 2, g2.stderr)

    print("[CP2-T05] a hedge or question is not a go-ahead")
    d = project("t05")
    ask(d)
    say(d, "hmm, are hooks really needed?")
    r = rx(d, "hooks-waive")
    check("[CP2-T05] refused", refused(r), r.stdout)
    check("[CP2-T05] message is actionable: asks for a plain yes", "yes" in out(r), r.stderr)
    check("[CP2-T05] waived stays falsy", not waived(d), hk(d))

    print("[CP2-T06] a cancel message does not waive")
    d = project("t06")
    phase = sj(d).get("phase")
    ask(d)
    say(d, "cancel this")
    r = rx(d, "hooks-waive")
    check("[CP2-T06] refused", refused(r), r.stdout)
    check("[CP2-T06] waived falsy and phase unchanged", not waived(d) and sj(d).get("phase") == phase == "building",
          (hk(d), sj(d).get("phase")))

    print("[CP2-T07] a yes already used for plan approval is not reused")
    d = project("t07")  # project() approved the plan with "yes, build it"
    ask(d)
    r = rx(d, "hooks-waive")
    check("[CP2-T07] refused with no newer message", refused(r) and not waived(d), r.stdout + r.stderr)

    print("[CP2-T08] installing hooks clears the skipped note")
    d = project("t08")
    full_waive(d)
    check("[CP2-T08] waived before installing", waived(d), hk(d))
    r = rx(d, "install-hooks", "--host", "claude", "--scope", "project")
    h = hk(d)
    check("[CP2-T08] install-hooks succeeds", r.returncode == 0, r.stderr + r.stdout)
    check("[CP2-T08] hooks.waived falsy and hooks.installed true", not h.get("waived") and h.get("installed") is True, h)
    check("[CP2-T08] plain status no longer says skipped", "skipped" not in out(rx(d, "status")))

    print("[CP2-T09] hooks firing after a waiver is shown consistently")
    d = project("t09")
    full_waive(d)
    check("[CP2-T09] waived before hooks fire", waived(d), hk(d))
    fire_stop(d)
    h = hk(d)
    check("[CP2-T09] heartbeat_seen true and missing false", h.get("heartbeat_seen") is True and h.get("missing") is False, h)
    check("[CP2-T09] the skipped note is not shown while hooks are firing", "skipped" not in out(rx(d, "status")))

    print("[CP2-T10] waiving twice is harmless")
    d = project("t10")
    full_waive(d)
    a, r = full_waive(d, "yes, go ahead without hooks please")
    check("[CP2-T10] second waive exits 0", r.returncode == 0, r.stderr + r.stdout)
    check("[CP2-T10] still waived", waived(d), hk(d))
    check("[CP2-T10] exactly one waiver entry in state", len(waiver_events(d)) == 1, waiver_events(d))

    print("[CP2-T11] the waiver never blocks anything")
    d = project("t11")
    ask(d)  # the question alone changes nothing about what to do next
    n1 = next_json(d)
    say(d, GO)
    rx(d, "hooks-waive")
    n2 = next_json(d)
    # "warning" is the advisory offer (install/waive); it legitimately disappears once waived, so it is excluded too
    strip = lambda n: {k: v for k, v in n.items() if k not in ("hooks", "hooks_warning", "warning")}
    check("[CP2-T11] next has a hooks object in both states", bool(n1.get("hooks")) and bool(n2.get("hooks")), (n1, n2))
    check("[CP2-T11] the waiver shows only in the hooks object", not (n1.get("hooks") or {}).get("waived")
          and bool((n2.get("hooks") or {}).get("waived")), (n1.get("hooks"), n2.get("hooks")))
    check("[CP2-T11] next action is identical apart from hooks", strip(n1) == strip(n2) and bool(n1), (strip(n1), strip(n2)))
    d = project("t11b", approved=False)
    say(d, "yes, build it")
    r = rx(d, "approve-plan")
    check("[CP2-T11] approve-plan succeeds with hooks missing and no waiver",
          r.returncode == 0 and sj(d).get("phase") == "building" and not waived(d), r.stderr + r.stdout)

    print("[CP2-T12] waiver works before plan approval and persists")
    d = project("t12", approved=False)
    check("[CP2-T12] project is awaiting plan approval", sj(d).get("phase") == "awaiting_plan_approval", sj(d).get("phase"))
    a, r = full_waive(d)
    check("[CP2-T12] hooks-waive works before plan approval", r.returncode == 0 and waived(d), r.stderr + r.stdout)
    say(d, "yes, build it")
    ap = rx(d, "approve-plan")
    check("[CP2-T12] approve-plan still works afterwards", ap.returncode == 0 and sj(d).get("phase") == "building",
          ap.stderr + ap.stdout)
    check("[CP2-T12] waived persists across approve-plan and status", waived(d), hk(d))
    vd = json.loads((d / ".ratchet/runs/f/viewer-data.json").read_text())
    vh = vd.get("hooks") or (vd.get("next") or {}).get("hooks") or {}
    check("[CP2-T12] viewer-data.json carries hooks.waived", bool(vh.get("waived")), list(vd.keys()))

    print("[CP2-T13] the waiver and existing approvals do not consume each other's message")
    d = project("t13", approved=False)
    ask(d)
    say(d, GO)
    w = rx(d, "hooks-waive")
    ap = rx(d, "approve-plan")
    if refused(ap):  # pinned alternative: one further message is needed, but approve-plan must then work
        say(d, "yes, build it")
        ap = rx(d, "approve-plan")
    check("[CP2-T13] hooks-waive succeeds and approve-plan is never blocked",
          w.returncode == 0 and ap.returncode == 0 and sj(d).get("phase") == "building", (w.stderr, ap.stderr))
    check("[CP2-T13] the waiver is still recorded", waived(d), hk(d))
    d = project("t13c")
    rx(d, "ask", "cancel")
    say(d, "yes")
    r = rx(d, "cancel")
    check("[CP2-T13] cancel behaves as before", r.returncode == 0 and sj(d).get("phase") == "cancelled", r.stderr)
    d = project("t13s")
    rx(d, "ask", "set-review", "end")
    say(d, "yes")
    r = rx(d, "set-review", "end")
    check("[CP2-T13] set-review behaves as before", r.returncode == 0, r.stderr)

    print("[CP2-T14] waiving needs a prior ask")
    d = project("t14")
    say(d, GO)  # a clear go-ahead, but the agent never asked
    r = rx(d, "hooks-waive")
    check("[CP2-T14] refused without `ask hooks-waive` first", refused(r) and not waived(d), r.stdout + r.stderr)
    check("[CP2-T14] the message tells the agent to run ask hooks-waive", "ask hooks-waive" in out(r), r.stderr)
    check("[CP2-T14] no waiver event is logged", not waiver_events(d), waiver_events(d))
    check("[CP2-T14] `ask hooks-waive` is a known action", ask(d).returncode == 0)

    print("[CP2-T15] a yes from before the ask is refused")
    d = project("t15")
    say(d, GO)  # typed BEFORE the question
    a = ask(d)
    r = rx(d, "hooks-waive")
    check("[CP2-T15] the earlier yes does not answer the later question",
          a.returncode == 0 and refused(r) and not waived(d), (a.stderr, r.stdout, r.stderr))
    say(d, GO)
    r = rx(d, "hooks-waive")
    check("[CP2-T15] a yes typed after the ask does work", r.returncode == 0 and waived(d), r.stderr + r.stdout)

    print("[CP2-T16] the real flow (prompt hook alive, guard/stop silent) shows the waiver")
    d = project("t16")
    full_waive(d)
    h = hk(d)
    check("[CP2-T16] prompt heartbeat is fresh but nothing enforces (missing true), and waived is true",
          h.get("fresh") is True and h.get("prompt_hook") is True and h.get("enforcing") is False
          and h.get("missing") is True and h.get("waived") is True, h)
    check("[CP2-T16] plain status says hooks were skipped", "skipped" in out(rx(d, "status")))
    vd = json.loads((d / ".ratchet/runs/f/viewer-data.json").read_text())
    check("[CP2-T16] viewer-data hooks.waived", (vd.get("hooks") or {}).get("waived") is True, vd.get("hooks"))
    check("[CP2-T16] next drops the re-offer warning once waived",
          not next_json(d).get("warning"), next_json(d).get("warning"))
    d = project("t16b")
    (d / ".ratchet/hook-heartbeat.json").unlink()  # nothing has fired at all, so next warns
    nw = next_json(d).get("warning") or ""
    check("[CP2-T16] before waiving, next's warning mentions ask hooks-waive", "hooks-waive" in nw, nw)

    print("[CP2-T17] enforcement hooks firing clears the waiver for good")
    d = project("t17")
    full_waive(d)
    fire_stop(d)
    check("[CP2-T17] not shown once the stop hook has fired", not waived(d), hk(d))
    rx(d, "ask", "cancel")  # any state write
    st = json.loads((d / ".ratchet/runs/f/state.json").read_text())
    check("[CP2-T17] hooks_waived is removed from state and the clearing is logged",
          "hooks_waived" not in st and any(e.get("event") == "hooks_waive_cleared" for e in st["history"]),
          list(st))
    (d / ".ratchet/hook-heartbeat.json").unlink()  # the hooks go silent / are uninstalled
    check("[CP2-T17] silence afterwards does not resurrect it", not waived(d) and "skipped" not in out(rx(d, "status")),
          hk(d))
    d = project("t17b")
    full_waive(d)
    fire_stop(d)
    hbp = d / ".ratchet/hook-heartbeat.json"
    hb = json.loads(hbp.read_text())
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=30)).isoformat()
    for k in ("guard", "stop"):
        if k in hb:
            hb[k]["at"] = old
    hbp.write_text(json.dumps(hb))
    check("[CP2-T17] even before any state write, hooks that fired after the waiver and later went silent (>24h) "
          "do not resurrect it", not waived(d), hk(d))
    r = rx(d, "install-hooks", "--host", "claude", "--scope", "project")
    rx(d, "install-hooks", "--host", "claude", "--scope", "project", "--uninstall")
    check("[CP2-T17] install then uninstall does not resurrect it", not waived(d), hk(d))

    print("[CP2-T18] installed hooks without a heartbeat: nothing to waive")
    d = project("t18")
    rx(d, "install-hooks", "--host", "claude", "--scope", "project")
    clear_hb(d)
    a, r = full_waive(d)
    check("[CP2-T18] hooks-waive records nothing while hooks are installed",
          r.returncode == 0 and "already active" in out(r) and not waived(d) and not waiver_events(d),
          (r.stdout, r.stderr, hk(d)))
    rx(d, "install-hooks", "--host", "claude", "--scope", "project", "--uninstall")
    check("[CP2-T18] uninstalling afterwards shows no waiver", not waived(d), hk(d))

    print("[CP2-T19] a yes to the hooks question is spent on that question")
    d = project("t19", approved=False)
    ask(d)
    say(d, GO)
    ap = rx(d, "approve-plan")
    check("[CP2-T19] approve-plan is refused after only ask hooks-waive + yes",
          refused(ap) and sj(d).get("phase") == "awaiting_plan_approval", (ap.stdout, ap.stderr))
    w = rx(d, "hooks-waive")
    check("[CP2-T19] the same yes still waives", w.returncode == 0 and waived(d), w.stderr + w.stdout)
    ap = rx(d, "approve-plan")
    check("[CP2-T19] and does not approve the plan afterwards either", refused(ap), (ap.stdout, ap.stderr))
    say(d, "yes, build it")
    ap = rx(d, "approve-plan")
    check("[CP2-T19] a separate yes approves the plan", ap.returncode == 0 and sj(d).get("phase") == "building",
          ap.stderr + ap.stdout)

    print("[CP2-T20] hooks-waive is a known director-sign action")
    d = project("t20")
    r = rx(d, "director-sign", "hooks-waive")
    check("[CP2-T20] director-sign accepts the action (refused only for not being the director)",
          "invalid choice" not in r.stderr and "director" in out(r), r.stderr)

    print("[CP2-T21] a no, then a later yes, does not waive")
    d = project("t21")
    ask(d)
    say(d, "no, install them first")
    say(d, "yes, build it")
    r = rx(d, "hooks-waive")
    check("[CP2-T21] refused and not waived", refused(r) and not waived(d), (r.stdout, r.stderr, hk(d)))
    r = rx(d, "hooks-waive")
    check("[CP2-T21] the question is spent: even a retry needs a new ask", refused(r) and not waived(d), r.stderr)
    a, r = full_waive(d)
    check("[CP2-T21] a fresh ask + yes still waives", r.returncode == 0 and waived(d), r.stderr + r.stdout)

    print("[CP2-T22] the first reply answers the question; a later message cannot change it")
    d = project("t22")
    ask(d)
    say(d, GO)
    say(d, "actually no, wait")
    r = rx(d, "hooks-waive")
    check("[CP2-T22] first reply was yes: recorded", r.returncode == 0 and waived(d), r.stderr + r.stdout)

    print("[CP2-T23] next offers the waiver after a real prompt with no guard/stop, and drops it once waived")
    d = project("t23")
    check("[CP2-T23] prompt hook is fresh", hk(d).get("fresh") is True, hk(d))
    nw = next_json(d).get("warning") or ""
    check("[CP2-T23] warning offers ask hooks-waive", "hooks-waive" in nw, nw)
    full_waive(d)
    check("[CP2-T23] warning gone once waived", not next_json(d).get("warning"), next_json(d))
    d = project("t23b")
    fire_stop(d)
    check("[CP2-T23] no warning once an enforcement hook has fired", not next_json(d).get("warning"), next_json(d))

    print("[CP2-T24] a director token for hooks-waive is single use")
    d = project("t24")
    DENV = dict(ENV, RATCHET_ROLE="director")
    rx(d, "director-key", env=DENV)
    en = rx(d, "director-enroll", env=DENV)
    sg = rx(d, "director-sign", "hooks-waive", env=DENV)
    tok = sg.stdout.strip().splitlines()[-1] if sg.stdout.strip() else ""
    check("[CP2-T24] token signed", en.returncode == 0 and sg.returncode == 0 and tok, (en.stderr, sg.stderr))
    r = rx(d, "hooks-waive", "--director-token", tok)
    check("[CP2-T24] first use waives", r.returncode == 0 and waived(d), r.stderr + r.stdout)
    fire_stop(d)
    rx(d, "status")  # state write clears the waiver once enforcement fires
    clear_hb(d)
    r = rx(d, "hooks-waive", "--director-token", tok)
    check("[CP2-T24] replaying the spent token is refused and does not waive", refused(r) and not waived(d),
          (r.stdout, r.stderr, hk(d)))

    print("[CP2-T26] a request to install hooks is not consent to skip them")
    for i, txt in enumerate(("yes, install them", "yes please install the hooks", "yes, enable the hooks")):
        d = project(f"t26{i}")
        ask(d)
        say(d, txt)
        r = rx(d, "hooks-waive")
        check(f"[CP2-T26] {txt!r} is refused and not waived", refused(r) and not waived(d), (r.stdout, r.stderr))
    check("[CP2-T26] the plain go-ahead still waives", full_waive(project("t26z"))[1].returncode == 0)

    print("[CP2-T27] a non-yes reply spends the question (pending is cleared)")
    d = project("t27")
    ask(d)
    say(d, "no, install them first")
    rx(d, "hooks-waive")
    pend = json.loads((d / ".ratchet/runs/f/state.json").read_text()).get("pending")
    check("[CP2-T27] pending absent after a refused non-yes", not pend, pend)

    print("[CP2-T28] each hooks_waive_n bump is pinned on its own")

    def n_of(d):
        return json.loads((d / ".ratchet/runs/f/state.json").read_text()).get("hooks_waive_n", 0)

    def token_for(d):
        denv = dict(ENV, RATCHET_ROLE="director")
        rx(d, "director-key", env=denv)
        rx(d, "director-enroll", env=denv)
        sg = rx(d, "director-sign", "hooks-waive", env=denv)
        return sg.stdout.strip().splitlines()[-1] if sg.stdout.strip() else ""

    d = project("t28")
    tok = token_for(d)
    n0 = n_of(d)
    r = rx(d, "hooks-waive", "--director-token", tok)
    check("[CP2-T28] token use waives and bumps the counter", r.returncode == 0 and waived(d) and n_of(d) == n0 + 1,
          (r.stderr, n0, n_of(d)))
    r = rx(d, "hooks-waive", "--director-token", tok)  # waiver still active, no awaiting_since: replay
    check("[CP2-T28] replaying the spent token while the waiver is active is refused", refused(r), (r.stdout, r.stderr))
    d = project("t28b")
    full_waive(d)
    n0 = n_of(d)
    fire_stop(d)
    rx(d, "status")
    check("[CP2-T28] auto-clear alone bumps the counter", not waived(d) and n_of(d) == n0 + 1, (n0, n_of(d)))

    print("[CP2-T29] a guard heartbeat older than 24h but newer than the waiver still clears it")
    d = project("t29")
    full_waive(d)
    fire_stop(d)
    hbp = d / ".ratchet/hook-heartbeat.json"
    hb = json.loads(hbp.read_text())
    for k in ("guard", "stop"):
        if k in hb:
            hb[k]["at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=30)).isoformat()
    hbp.write_text(json.dumps(hb))
    sp = d / ".ratchet/runs/f/state.json"
    stj = json.loads(sp.read_text())
    stj.setdefault("hooks_waived", {})["at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=40)).isoformat()
    sp.write_text(json.dumps(stj))
    check("[CP2-T29] waiver not applied", not waived(d), hk(d))

    print("[CP2-T30] ask hooks-waive does not take over another question")
    d = project("t30")
    r0 = rx(d, "ask", "set-review", "each")
    r = ask(d)
    check("[CP2-T30] refused while another question is pending", r0.returncode == 0 and refused(r), (r.stdout, r.stderr))
    d = project("t30b", approved=False)
    ask(d)
    say(d, "yes, build it")
    check("[CP2-T30] the first reply after the hooks question answers it, so it doesn't approve the plan",
          refused(rx(d, "approve-plan")) and sj(d).get("phase") == "awaiting_plan_approval")
    say(d, "yes, build it")
    check("[CP2-T30] the next yes approves the plan", rx(d, "approve-plan").returncode == 0 and sj(d).get("phase") == "building")
    d = project("t30c")
    ask(d)
    say(d, "yes")
    r = rx(d, "hooks-waive")
    check("[CP2-T30] a bare yes does not waive", refused(r) and not waived(d), (r.stdout, r.stderr))

    def stp(d):
        return d / ".ratchet/runs/f/state.json"

    def stj_read(d):
        return json.loads(stp(d).read_text())

    def iso_ago(h):
        return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=h)).isoformat()

    def write_beats(d, ago_h, events=("guard", "stop")):
        """Write guard/stop heartbeats by hand (host claude), without any state write."""
        hbp = d / ".ratchet/hook-heartbeat.json"
        hb = json.loads(hbp.read_text()) if hbp.exists() else {}
        for k in events:
            hb[k] = {"at": iso_ago(ago_h), "host": "claude"}
        hbp.write_text(json.dumps(hb))

    def put_waiver(d, ago_h):
        st = stj_read(d)
        st["hooks_waived"] = {"at": iso_ago(ago_h), "by": "human", "prompt": GO}
        stp(d).write_text(json.dumps(st))
        # state.json is integrity-checked: re-baseline the hand edit with the engine's own record_chain (what
        # accept-state does), so status reads it. accept-state itself now needs a person typing a code at a real
        # terminal, which a fixture cannot be.
        snippet = ("import importlib.util, sys\nfrom pathlib import Path\n"
                   "s = importlib.util.spec_from_file_location('ratchet_fixture', sys.argv[1])\n"
                   "m = importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
                   "m.record_chain(Path(sys.argv[2]))\n")
        r = subprocess.run([sys.executable, "-c", snippet, str(ENGINE), str(stp(d))], cwd=d, env=ENV,
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr + r.stdout

    print("[CP2-T31] only an exact waiver phrase counts (strict allow-list)")
    accepted = (GO, "Yes, go ahead without hooks!", "yes skip the hooks", "YES, BUILD WITHOUT HOOKS please",
                "go ahead without hooks", "skip the hooks.", "proceed without hooks", "  Yes,  skip the  hooks  Please ")
    for i, txt in enumerate(accepted):
        d = project(f"t31a{i}")
        a, r = full_waive(d, txt)
        check(f"[CP2-T31] {txt!r} waives", r.returncode == 0 and waived(d), (r.stdout, r.stderr))
    rejected = ("yes with hooks", "yes, use hooks", "yes hooks please", "yes keep the hooks", "yes run hooks",
                "yes hooks on", "yes, i want hooks", "yes go ahead and use hooks", "yes proceed without changes",
                "ok without", "yes", "yes go ahead", "yes skip them", "yes, go ahead without hooks but install them",
                "yes, go ahead without hooks, actually install them", "no, go ahead with hooks",
                "skip the hooks or not", "don't skip the hooks",
                "yes go ahead without the hooks")
    for i, txt in enumerate(rejected):
        d = project(f"t31r{i}")
        ask(d)
        say(d, txt)
        r = rx(d, "hooks-waive")
        pend = stj_read(d).get("pending")
        check(f"[CP2-T31] {txt!r} is refused, not waived, question cleared",
              refused(r) and not waived(d) and not pend, (r.stdout, r.stderr, pend))
    a = ask(d)
    check("[CP2-T31] the printed question names the exact reply that counts",
          "ASK THE USER" in a.stdout and '"yes, go ahead without hooks"' in a.stdout, a.stdout)
    d = project("t31m")
    ask(d)
    say(d, "yes")
    r = rx(d, "hooks-waive")
    check("[CP2-T31] refusal tells the agent to re-ask with the exact wording",
          "yes, go ahead without hooks" in r.stderr + r.stdout and "ask hooks-waive" in r.stderr + r.stdout,
          r.stderr)

    print("[CP2-T32] a reply that is not a waiver phrase still reaches plan approval")
    # the first reply after the hooks question answers THAT question ("Any other reply means no"), whatever it says;
    # the plan needs a reply of its own
    for i, txt in enumerate(("approve the plan without changes", "yes, build it", "yes")):
        d = project(f"t32{i}", approved=False)
        ask(d)
        say(d, txt)
        r = rx(d, "approve-plan")
        check(f"[CP2-T32] {txt!r} as the first reply after the hooks question does not approve the plan",
              refused(r) and sj(d).get("phase") == "awaiting_plan_approval", (r.stdout, r.stderr))
        say(d, txt)
        r = rx(d, "approve-plan")
        check(f"[CP2-T32] a second {txt!r} reaches approve-plan",
              r.returncode == 0 and sj(d).get("phase") == "building", (r.stdout, r.stderr))
    d = project("t32w", approved=False)
    ask(d)
    say(d, GO)
    r = rx(d, "approve-plan")
    check("[CP2-T32] the waiver phrase is eaten by the hooks question, not used to approve the plan",
          refused(r) and sj(d).get("phase") == "awaiting_plan_approval", (r.stdout, r.stderr))

    print("[CP2-T33] a waiver older than a guard/stop beat (without any state write) is not resurrected")
    d = project("t33a")
    put_waiver(d, 40)
    write_beats(d, 30)   # newer than the waiver, older than 24h: not "active", but proves it was overtaken
    check("[CP2-T33] beat between waiver and 24h ago: not waived", not waived(d), hk(d))
    d = project("t33b")
    put_waiver(d, 20)
    write_beats(d, 30)   # beat older than the waiver: the waiver stands
    check("[CP2-T33] beat older than the waiver: still waived", waived(d), hk(d))
    d = project("t33c")
    put_waiver(d, 40)
    write_beats(d, 30, events=("stop",))
    check("[CP2-T33] a stop beat alone overtakes it too", not waived(d), hk(d))

    print("[CP2-T34] ask hooks-waive never opens or resets the approval window")
    d = project("t34", approved=False)
    say(d, "yes, build it")   # typed BEFORE the hooks question
    aw0 = stj_read(d).get("awaiting_since")
    ask(d)
    check("[CP2-T34] awaiting_since unchanged by the ask", aw0 and stj_read(d).get("awaiting_since") == aw0,
          (aw0, stj_read(d).get("awaiting_since")))
    r = rx(d, "approve-plan")
    check("[CP2-T34] a plan-approving yes typed before the ask still approves", r.returncode == 0
          and sj(d).get("phase") == "building", (r.stdout, r.stderr))
    d = project("t34b")
    ask(d)
    check("[CP2-T34] no approval window is opened where none existed", stj_read(d).get("awaiting_since") is None,
          stj_read(d).get("awaiting_since"))

    print("[CP2-T35] only replies typed after the hooks question are skipped as its answer")
    d = project("t35", approved=False)
    say(d, GO)   # a waiver-looking message typed BEFORE the ask, and nothing after
    ask(d)
    r = rx(d, "approve-plan")
    # it is judged as the user's latest reply (not skipped as the hooks answer), and a hooks reply is not a plan yes
    check("[CP2-T35] the earlier message is not treated as the hooks answer", refused(r)
          and "not a clear yes" in r.stderr and "without hooks" in r.stderr
          and sj(d).get("phase") == "awaiting_plan_approval", (r.stdout, r.stderr))
    d = project("t32y", approved=False)
    ask(d)
    say(d, "yes with hooks")
    r = rx(d, "approve-plan")
    check("[CP2-T32] 'yes with hooks' is neither a waiver nor a plan approval (extra words make it unclear)",
          refused(r) and sj(d).get("phase") == "awaiting_plan_approval", (r.stdout, r.stderr))

    print("[CP2-T36] director-sign hooks-waive works in the phases where it is offered")
    for nm, appr in (("t36b", True), ("t36p", False)):
        d = project(nm, approved=appr)
        denv = dict(ENV, RATCHET_ROLE="director")
        rx(d, "director-key", env=denv)
        rx(d, "director-enroll", env=denv)
        sg = rx(d, "director-sign", "hooks-waive", env=denv)
        tok = sg.stdout.strip().splitlines()[-1] if sg.stdout.strip() else ""
        r = rx(d, "hooks-waive", "--director-token", tok)
        check(f"[CP2-T36] token signed in {sj(d).get('phase')} waives", sg.returncode == 0 and r.returncode == 0
              and waived(d), (sg.stderr, r.stderr))

    print("[CP2-T37] the user's reply is stored with the waiver")
    d = project("t37")
    full_waive(d, "Yes, skip the hooks")
    w = stj_read(d).get("hooks_waived") or {}
    check("[CP2-T37] hooks_waived.prompt holds the reply", "skip the hooks" in (w.get("prompt") or "").lower(), w)
    check("[CP2-T37] hooks_waived.by is human", w.get("by") == "human", w)

    print("[CP2-T38] a future-dated heartbeat is ignored")
    d = project("t38")
    full_waive(d)
    write_beats(d, -1)   # one hour in the future
    check("[CP2-T38] the waiver still stands", waived(d), hk(d))

    print("[CP2-T39] replies with non-ASCII, emoji or a question mark never waive")
    bad = ("skip the hooks?", "yes, skip the hooks?", "skip the hooks \U0001F44E", "\u274c skip the hooks",
           "skip the hooks \u4e0d\u8981", "\uff4e\uff4f skip the hooks", "yes skip the hooks \u2014 not",
           "yes, go ahead without hooks\u00a1", "skip th\u00e9 hooks")
    for i, txt in enumerate(bad):
        d = project(f"t39a{i}")
        ask(d)
        say(d, txt)
        r = rx(d, "hooks-waive")
        check(f"[CP2-T39] {txt!r} is refused, not waived", refused(r) and not waived(d), (r.stdout, r.stderr))
    d = project("t39ok")
    a, r = full_waive(d, "\uff39\uff25\uff33, skip the hooks!")   # fullwidth letters fold to plain ASCII via NFKC
    check("[CP2-T39] fullwidth letters that NFKC-fold to an exact phrase still waive", r.returncode == 0 and waived(d),
          (r.stdout, r.stderr))

    print("[CP2-T40] a heartbeat from another host neither clears the waiver nor suppresses the warning")
    d = project("t40")
    full_waive(d)
    write_beats(d, 0)
    hbp = d / ".ratchet/hook-heartbeat.json"
    hb = json.loads(hbp.read_text())
    for k in ("guard", "stop"):
        hb[k]["host"] = "codex"
    hbp.write_text(json.dumps(hb))
    check("[CP2-T40] other-host beats leave the waiver in place", waived(d), hk(d))
    d = project("t40w")
    hbp = d / ".ratchet/hook-heartbeat.json"
    hbp.write_text(json.dumps({k: {"at": iso_ago(0), "host": "codex"} for k in ("guard", "stop")}))
    check("[CP2-T40] other-host beats do not suppress the hooks warning", bool(next_json(d).get("warning")),
          next_json(d))
    hbp.write_text(json.dumps({k: {"at": iso_ago(0), "host": "claude"} for k in ("guard", "stop")}))
    check("[CP2-T40] same-host beats do suppress it", not next_json(d).get("warning"), next_json(d))

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
