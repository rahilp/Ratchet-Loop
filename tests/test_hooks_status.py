#!/usr/bin/env python3
"""
Hooks-status tests (CP1): `doctor --json`, and the `hooks` object in `status --json` and `next`.
    python3 tests/test_hooks_status.py

Everything runs in throwaway git projects with a temp HOME / RATCHET_HOME, so nothing touches the real user's files.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import _tmp
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
PASS = FAIL = 0
KEYS = {"host", "installed", "heartbeat_seen", "fresh", "enforcing", "prompt_hook", "missing"}
VENDOR = {"claude": "anthropic", "codex": "openai", "gemini": "google", "copilot": "github"}
SCRUB = ("RATCHET_ROLE", "RATCHET_HUMAN_OVERRIDE", "RATCHET_ROOT", "RATCHET_HOST_PROVIDER", "CLAUDECODE",
         "CLAUDE_CODE_ENTRYPOINT", "CODEX_SANDBOX", "CODEX_THREAD_ID", "CODEX_SANDBOX_NETWORK_DISABLED",
         "GEMINI_CLI", "COPILOT_CLI", "GITHUB_COPILOT_CLI", "CODEX_HOME", "COPILOT_HOME")


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:300]}"))


def make_env(host=None):
    home = Path(_tmp.mkdtemp(prefix="ratchet-hs-home-"))
    env = {k: v for k, v in os.environ.items() if k not in SCRUB}
    env.update(HOME=str(home), RATCHET_HOME=str(home / "rh"), CODEX_HOME=str(home / "codexhome"),
               COPILOT_HOME=str(home / "copilothome"))
    if host:
        env["RATCHET_HOST_PROVIDER"] = VENDOR[host]
    return env


def rx(d, env, *args, stdin=None):
    return subprocess.run([sys.executable, str(ENGINE), *args], cwd=d, capture_output=True, text=True,
                          env=env, input=stdin)


def git(d, *a):
    return subprocess.run(["git", *a], cwd=d, capture_output=True, text=True)


def js(r):
    """Parse stdout as JSON; None if the command failed or stdout is not pure JSON."""
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except Exception:
        return None


def hooks_of(r):
    j = js(r)
    return j.get("hooks") if isinstance(j, dict) else None


def prompt_payload(host, d):
    cwd = str(d)
    return {
        "claude": {"session_id": "s", "cwd": cwd, "hook_event_name": "UserPromptSubmit", "prompt": "hello"},
        "codex": {"session_id": "s", "turn_id": "t1", "cwd": cwd, "hook_event_name": "UserPromptSubmit",
                  "prompt": "hello", "model": "gpt-5-codex"},
        "gemini": {"session_id": "s", "cwd": cwd, "hook_event_name": "BeforeAgent", "prompt": "hello"},
        "copilot": {"sessionId": "s", "timestamp": 1, "cwd": cwd, "prompt": "hello"},
    }[host]


def stop_payload(host, d):
    p = dict(prompt_payload(host, d))
    p.pop("prompt", None)
    if "hook_event_name" in p:
        p["hook_event_name"] = {"claude": "Stop", "codex": "Stop", "gemini": "AfterAgent"}[host]
    return p


def fire(d, env, host, prompt_only=False):
    """A normal turn: the prompt hook fires, then (unless prompt_only) the stop hook, which enforces."""
    r = rx(d, env, "hook", "prompt", "--host", host, stdin=json.dumps(prompt_payload(host, d)))
    if not prompt_only:
        r = rx(d, env, "hook", "stop", "--host", host, stdin=json.dumps(stop_payload(host, d)))
    return r


def hb_path(d):
    return Path(d) / ".ratchet" / "hook-heartbeat.json"


def clear_hb(d):
    hb_path(d).unlink() if hb_path(d).exists() else None


def project(env, approved=True):
    d = Path(_tmp.mkdtemp(prefix="ratchet-hs-p-"))
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(d, *c)
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    fire(d, env, "claude") if (d / ".ratchet").is_dir() else None
    tp = d / "t.jsonl"
    tp.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "please build the feature"},
                              "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}) + "\n")
    # the request that starts the run must come from a recorded user message; .ratchet may not exist yet
    (d / ".ratchet").mkdir(exist_ok=True)
    rx(d, env, "hook", "prompt", "--host", "claude",
       stdin=json.dumps({"cwd": str(d), "prompt": "please build the feature", "session_id": "s-owner",
                         "transcript_path": str(tp)}))
    r = rx(d, env, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr + r.stdout
    if approved:
        run = d / ".ratchet/runs/f"
        rx(d, env, "config", "--set", "min_review_seconds=0")
        (run / "app-scout.json").write_text("{}")
        cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
              "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
              "tests": {"files": ["tests/cp1.test.js"], "command": "node --test tests/cp1.test.js"}}
        (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                           "checkpoints": [cp]}))
        (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
        r = rx(d, env, "plan-submit")
        assert r.returncode == 0, r.stderr + r.stdout
        rx(d, env, "ask", "approve-plan")  # CP5: an approval answers the question Ratchet printed
        tp.open("a").write(json.dumps({"type": "user", "message": {"role": "user", "content": "yes, go ahead"},
                                       "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}) + "\n")
        rx(d, env, "hook", "prompt", "--host", "claude",
           stdin=json.dumps({"cwd": str(d), "prompt": "yes, go ahead", "session_id": "s-owner",
                             "transcript_path": str(tp)}))
        r = rx(d, env, "approve-plan")
        assert r.returncode == 0, r.stderr + r.stdout
    clear_hb(d)  # the setup prompts fired the hook; tests start from a clean heartbeat
    return d


def install(d, env, host, scope="project"):
    r = rx(d, env, "install-hooks", "--host", host, "--scope", scope)
    assert r.returncode == 0, r.stderr + r.stdout
    return r


def old_iso(hours):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)).isoformat(timespec="microseconds")


def snapshot(d, home):
    files = {}
    for base, skip in ((Path(d), (".git", ".ratchet")), (Path(home), ())):
        for p in sorted(base.rglob("*")):
            if p.is_file() and not any(s in p.relative_to(base).parts for s in skip):
                files[str(p)] = p.read_bytes()
    return files


def main():
    print("Missing / installed / fresh")
    env = make_env("claude")
    d = project(env)
    st = js(rx(d, env, "status", "--json"))
    h = st.get("hooks") if isinstance(st, dict) else None
    check("[CP1-T01] no hooks: status --json hooks == missing object",
          h == {"host": "claude", "installed": False, "heartbeat_seen": False, "fresh": False, "enforcing": False,
                "prompt_hook": False, "missing": True}
          and all(type(h[k]) is bool for k in KEYS - {"host"}), h)

    nx = js(rx(d, env, "next"))
    check("[CP1-T02] next carries the same missing object and keeps 'warning'",
          isinstance(nx, dict) and (nx.get("hooks") or {}).get("missing") is True
          and nx["hooks"].get("host") == "claude" and "warning" in nx, nx)

    env_c = make_env("codex")
    d = project(env_c)
    r = rx(d, env_c, "doctor", "--json")
    j = js(r)
    check("[CP1-T03] doctor --json is pure JSON for codex, missing",
          isinstance(j, dict) and j.get("hooks") == {"host": "codex", "installed": False, "heartbeat_seen": False,
                                                     "fresh": False, "enforcing": False, "prompt_hook": False,
                                                     "missing": True}, r.stdout[:200] + r.stderr[:100])

    env = make_env("claude")
    d = project(env)
    install(d, env, "claude")
    hd, hs = hooks_of(rx(d, env, "doctor", "--json")), hooks_of(rx(d, env, "status", "--json"))
    want = {"host": "claude", "installed": True, "heartbeat_seen": False, "fresh": False, "enforcing": False,
            "prompt_hook": False, "missing": False}
    check("[CP1-T04] installed, never fired: not fresh, not missing (doctor and status)", hd == want and hs == want,
          (hd, hs))

    fire(d, env, "claude")
    hs = hooks_of(rx(d, env, "status", "--json"))
    check("[CP1-T05] installed and fired just now: fresh",
          hs == {"host": "claude", "installed": True, "heartbeat_seen": True, "fresh": True, "enforcing": True,
                 "prompt_hook": True, "missing": False}, hs)

    hb = json.loads(hb_path(d).read_text())
    for e in hb.values():
        e["at"] = old_iso(48)
        e["host"] = "claude"
    hb_path(d).write_text(json.dumps(hb))
    hd, hs = hooks_of(rx(d, env, "doctor", "--json")), hooks_of(rx(d, env, "status", "--json"))
    want = {"host": "claude", "installed": True, "heartbeat_seen": True, "fresh": False, "enforcing": False,
            "prompt_hook": False, "missing": False}
    check("[CP1-T06] fired 48h ago: seen, not fresh, not missing", hd == want and hs == want, (hd, hs))

    print("Other hosts, plugins, dialects")
    env = make_env("claude")
    d = project(env)
    install(d, env, "claude")
    fire(d, env, "codex")  # a codex heartbeat lands in this project
    h = hooks_of(rx(d, env, "status", "--json")) or {}
    a = (h.get("host"), h.get("heartbeat_seen"), h.get("fresh"))
    env2 = dict(env, RATCHET_HOST_PROVIDER="openai")
    h2 = hooks_of(rx(d, env2, "status", "--json")) or {}
    check("[CP1-T07] a codex heartbeat does not count for claude, and does for codex",
          a == ("claude", False, False) and h2.get("host") == "codex" and h2.get("heartbeat_seen") is True
          and h2.get("fresh") is True and h2.get("installed") is False, (h, h2))

    env = make_env("claude")
    d = project(env)
    fire(d, env, "claude")  # plugin-delivered hooks: firing, but no install-hooks config anywhere
    h = hooks_of(rx(d, env, "status", "--json"))
    check("[CP1-T08] plugin-delivered hooks that fire are not missing",
          h == {"host": "claude", "installed": False, "heartbeat_seen": True, "fresh": True, "enforcing": True,
                "prompt_hook": True, "missing": False}, h)

    env = make_env("claude")
    d = project(env)
    fire(d, env, "claude", prompt_only=True)  # only the prompt hook runs: replies are recorded, nothing is enforced
    h = hooks_of(rx(d, env, "status", "--json")) or {}
    nx = js(rx(d, env, "next")) or {}
    check("[CP1-T08b] only the prompt hook fires: not enforcing, still missing, and next warns (doctor and next agree)",
          h.get("enforcing") is False and h.get("prompt_hook") is True and h.get("missing") is True
          and "warning" in nx and (nx.get("hooks") or {}).get("missing") is True, (h, nx.get("warning")))

    ok = True
    detail = []
    for host, scope in (("claude", "project"), ("codex", "user"), ("gemini", "project"), ("copilot", "user")):
        env = make_env(host)
        d = project(env, approved=False)
        before = hooks_of(rx(d, env, "doctor", "--json"))
        install(d, env, host, scope)
        fire(d, env, host)
        after = hooks_of(rx(d, env, "doctor", "--json"))
        good = (isinstance(before, dict) and before.get("missing") is True and before.get("host") == host
                and isinstance(after, dict) and after.get("host") == host and after.get("installed") is True
                and after.get("heartbeat_seen") is True and after.get("fresh") is True)
        ok &= bool(good)
        detail.append((host, before, after))
    check("[CP1-T09] all four host dialects report installed + seen + fresh (missing before)", ok, detail)

    print("Robustness")
    env = make_env()
    d = project(env)
    outs = [rx(d, env, "status", "--json"), rx(d, env, "next"), rx(d, env, "doctor", "--json")]
    js_ = [js(r) for r in outs]
    hs = [(j.get("hooks") if isinstance(j, dict) else None) for j in js_]
    check("[CP1-T10] unknown host: exit 0, valid JSON, all keys, string host, boolean missing",
          all(r.returncode == 0 for r in outs) and all(isinstance(h, dict) and set(h) == KEYS
                                                       and isinstance(h["host"], str)
                                                       and isinstance(h["missing"], bool) for h in hs),
          [(r.returncode, r.stderr[:80]) for r in outs])

    env = make_env("claude")
    d = project(env, approved=False)
    st, nx = js(rx(d, env, "status", "--json")), js(rx(d, env, "next"))
    check("[CP1-T11] planning phase: status and next both expose hooks; no 'warning' added to next",
          isinstance(st, dict) and st.get("phase") == "planning" and (st.get("hooks") or {}).get("missing") is True
          and isinstance(nx, dict) and (nx.get("hooks") or {}).get("missing") is True and "warning" not in nx,
          (st and st.get("phase"), nx))

    env = make_env("claude")
    d = project(env)
    r1 = rx(d, env, "doctor")
    install(d, env, "claude")
    fire(d, env, "claude")
    r2 = rx(d, env, "doctor")
    try:
        json.loads(r1.stdout)
        is_json = True
    except Exception:
        is_json = False
    lines2 = r2.stdout.splitlines()
    check("[CP1-T12] plain doctor text output unchanged",
          not is_json and r1.stdout.startswith("Host running this command:")
          and all(h in r1.stdout for h in ("claude", "codex", "gemini", "copilot")) and "not installed" in r1.stdout
          and "No Ratchet hook has fired in this project yet" in r1.stdout
          and "project:" in r2.stdout and ".claude" in r2.stdout
          and any("last prompt" in l and "Z" in l for l in lines2)
          and "No Ratchet hook has fired" not in r2.stdout, r1.stdout[:200] + "\n---\n" + r2.stdout[:300])

    env = make_env("claude")
    d = project(env)
    st, nx = js(rx(d, env, "status", "--json")), js(rx(d, env, "next"))
    plain = rx(d, env, "status")
    old_status = {"phase": str, "checkpoints": list, "hooks_warning": str, "hook_heartbeat": dict}
    old_next = {"phase": str, "warning": str}
    check("[CP1-T13] old status/next keys kept with same types; hooks purely additive; plain status unchanged",
          isinstance(st, dict) and isinstance(nx, dict)
          and all(isinstance(st.get(k), t) for k, t in old_status.items())
          and all(isinstance(nx.get(k), t) for k, t in old_next.items())
          and isinstance(st.get("hooks"), dict) and isinstance(nx.get("hooks"), dict)
          and "Run:" in plain.stdout and "Phase:" in plain.stdout, (st and list(st), nx and list(nx)))

    env = make_env("claude")
    d = project(env)
    install(d, env, "claude")
    fire(d, env, "claude")
    home = env["HOME"]
    watched = [d / ".ratchet/runs/f/state.json", d / ".ratchet/human_log.jsonl", hb_path(d)]
    before = {str(p): p.read_bytes() for p in watched}
    tree = snapshot(d, home)
    rcs = []
    for _ in range(2):
        for args in (("doctor", "--json"), ("status", "--json"), ("next",)):
            rcs.append(rx(d, env, *args).returncode)
    after = {str(p): p.read_bytes() for p in watched}
    check("[CP1-T14] reading hooks status changes nothing (state, human log, heartbeat, no new config files)",
          all(c == 0 for c in rcs) and before == after and snapshot(d, home) == tree, rcs)

    env = make_env("claude")
    d = project(env)
    outs = []
    for content in ("{not json", json.dumps({"prompt": "oops", "stop": {"host": "claude"}, "guard": ["x"]}),
                    json.dumps(["not", "a", "dict"])):
        hb_path(d).write_text(content)
        outs.append((rx(d, env, "status", "--json"), rx(d, env, "doctor", "--json")))
    good = all(r.returncode == 0 and isinstance(hooks_of(r), dict) and set(hooks_of(r)) == KEYS
               and hooks_of(r)["fresh"] is False for pair in outs for r in pair)
    check("[CP1-T15] corrupt or odd heartbeat file: exit 0, valid hooks object, fresh false", good,
          [(r.returncode, r.stderr[-120:]) for pair in outs for r in pair])

    env = make_env("claude")
    d = project(env)
    fire(d, env, "claude")
    try:
        e = json.loads(hb_path(d).read_text()).get("prompt")
        at = dt.datetime.fromisoformat(e["at"].replace("Z", "+00:00"))
        if at.tzinfo is None:
            at = at.replace(tzinfo=dt.timezone.utc)
        near = abs((dt.datetime.now(dt.timezone.utc) - at).total_seconds()) < 120
        good = near and e.get("host") == "claude"
    except Exception as ex:
        good, e = False, repr(ex)
    check("[CP1-T16] hook heartbeat records an ISO timestamp near now and host claude", good, e)

    env = make_env("claude")
    d = project(env)
    outs = []
    for content in ("{not json", json.dumps({"prompt": "oops", "stop": {"host": 1}, "guard": ["x"]}),
                    json.dumps({"prompt": {"at": 5, "host": "claude"}}), json.dumps(["a"])):
        hb_path(d).write_text(content)
        outs.append(rx(d, env, "doctor"))
    check("[CP1-T17] plain doctor tolerates odd heartbeat entries (exit 0, no traceback)",
          all(r.returncode == 0 and "Traceback" not in r.stderr for r in outs), [(r.returncode, r.stderr[-150:]) for r in outs])

    env = make_env("claude")
    d = project(env)
    (d / ".ratchet" / "config.json").write_text("{not json")
    r1, r2 = rx(d, env, "doctor", "--json"), rx(d, env, "doctor")
    check("[CP1-T18] corrupt config.json: doctor --json still prints a valid hooks object and plain doctor exits 0",
          js(r1) is not None and set(hooks_of(r1) or {}) == KEYS and r2.returncode == 0, (r1.stderr[-150:], r2.stderr[-150:]))

    env = make_env("claude")
    d = project(env)
    hb_path(d).mkdir()
    rs = [rx(d, env, "doctor"), rx(d, env, "doctor", "--json"), rx(d, env, "status"), rx(d, env, "next")]
    check("[CP1-T24] heartbeat path is a directory: no traceback anywhere; plain doctor says unreadable",
          all("Traceback" not in r.stderr for r in rs) and "hook-heartbeat.json unreadable" in rs[0].stdout,
          [r.stderr[-150:] for r in rs])

    env = make_env("claude")
    d = project(env)
    future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3650)).isoformat()
    hb_path(d).write_text(json.dumps({"prompt": {"at": future, "host": "claude"}}))
    h = hooks_of(rx(d, env, "doctor", "--json")) or {}
    check("[CP1-T19] future-dated heartbeat is not fresh", h.get("fresh") is False and h.get("heartbeat_seen") is True, h)

    env = make_env("claude")
    d = project(env)
    rx(d, env, "config", "--set", "host_provider=openai")
    env.pop("RATCHET_HOST_PROVIDER", None)
    r = rx(d, env, "doctor")
    h = hooks_of(rx(d, env, "doctor", "--json")) or {}
    check("[CP1-T20] plain doctor and doctor --json report the same host (config host_provider)",
          h.get("host") == "codex" and "Host running this command: codex" in r.stdout, (h, r.stdout[:120]))

    env = make_env("claude")
    d = project(env)
    hb_path(d).write_text("{not json")
    (d / ".ratchet" / "config.json").write_text("{not json")
    r1, r2 = rx(d, env, "doctor", "--json"), rx(d, env, "doctor")
    check("[CP1-T21] corrupt heartbeat/config: quiet stderr; plain doctor names the corrupt heartbeat file",
          r1.stderr.strip() == "" and "hook-heartbeat.json unreadable" in r2.stdout and "No Ratchet hook has fired" not in r2.stdout,
          (r1.stderr[-150:], r2.stdout[-200:]))

    env = make_env("claude")
    d = project(env)
    future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3650)).isoformat()
    hb_path(d).write_text(json.dumps({"guard": {"at": future, "host": "claude"}}))
    r = rx(d, env, "doctor")
    check("[CP1-T22] plain doctor annotates a future-dated heartbeat", "future-dated" in r.stdout, r.stdout[-200:])

    env = make_env("claude")
    d = project(env)
    rx(d, env, "config", "--set", "host_provider=weirdvendor")
    env.pop("RATCHET_HOST_PROVIDER", None)
    hb_path(d).write_text(json.dumps({"guard": {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "host": "codex"}}))
    h = hooks_of(rx(d, env, "doctor", "--json")) or {}
    check("[CP1-T23] unknown host: documented behavior, any-host heartbeat counts as fresh",
          h.get("host") == "unknown" and h.get("fresh") is True, h)

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
