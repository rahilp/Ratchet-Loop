#!/usr/bin/env python3
"""
Gate-integrity tests: every shortcut the security audit found must now fail, legitimate work must still pass,
and the state machine must handle its edge cases.   python3 tests/test_gates.py

Runs in throwaway git repos with RATCHET_HOME pointed at a temp folder, so nothing touches the real user's files.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
HOME = Path(tempfile.mkdtemp(prefix="ratchet-home-"))
ENV = {k: v for k, v in os.environ.items() if k not in ("RATCHET_ROLE", "RATCHET_HUMAN_OVERRIDE", "RATCHET_ROOT")}
ENV["RATCHET_HOME"] = str(HOME)
PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:300]}"))


def rx(d, *args, env=None):
    return subprocess.run([sys.executable, str(ENGINE), *args], cwd=d, capture_output=True, text=True,
                          env=env or ENV)


def say(d, text, session="s-owner"):
    """What the agent CLI's prompt hook does when the user types a message."""
    return subprocess.run([sys.executable, str(ENGINE), "hook", "prompt", "--host", "claude"], cwd=d, text=True,
                          capture_output=True, env=ENV,
                          input=json.dumps({"cwd": str(d), "prompt": text, "session_id": session}))


def guard(d, tool, tool_input, cwd=None):
    return subprocess.run([sys.executable, str(ENGINE), "hook", "guard", "--host", "claude"], cwd=d, text=True,
                          capture_output=True, env=ENV,
                          input=json.dumps({"cwd": str(cwd or d), "tool_name": tool, "tool_input": tool_input}))


def stop(d, session="s-owner"):
    return subprocess.run([sys.executable, str(ENGINE), "hook", "stop", "--host", "claude"], cwd=d, text=True,
                          capture_output=True, env=ENV,
                          input=json.dumps({"cwd": str(d), "session_id": session, "hook_event_name": "Stop"}))


def git(d, *a):
    return subprocess.run(["git", *a], cwd=d, capture_output=True, text=True)


def project(name="p", cps=1, extra_cp=None, config=None) -> Path:
    d = Path(tempfile.mkdtemp(prefix=f"ratchet-{name}-"))
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(d, *c)
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    say(d, "please build the feature")          # the request that starts the run (owner session)
    r = rx(d, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr
    if config:
        rx(d, "config", "--set", *config)
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    plan = {"feature": "x", "checkpoints": []}
    for i in range(1, cps + 1):
        cp = {"id": f"CP{i}", "title": f"Step {i}", "summary": "s", "done_when": ["works"],
              "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
              "tests": {"files": [f"tests/cp{i}.test.js"], "command": f"node --test tests/cp{i}.test.js"}}
        if extra_cp:
            cp.update(extra_cp)
        plan["checkpoints"].append(cp)
        (run / "tests" / f"CP{i}.json").write_text(json.dumps({"cases": [{"id": f"CP{i}-T01", "layer": "code"}]}))
    (run / "checkpoints.json").write_text(json.dumps(plan))
    r = rx(d, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    return d


def approve_plan(d):
    say(d, "yes, build it")
    r = rx(d, "approve-plan")
    assert r.returncode == 0, r.stderr
    return r


def write_tests(d, cp="CP1"):
    (d / f"tests/{cp.lower()}.test.js").write_text(
        "import t from 'node:test'; import a from 'node:assert'; import { f } from '../src/f.js';\n"
        f"t('[{cp}-T01] works', () => a.equal(f(), 1));\n")


def to_adversarial(d, cp="CP1"):
    write_tests(d, cp)
    assert rx(d, "tests-written", cp).returncode == 0
    assert rx(d, "red", cp).returncode == 0
    (d / "src/f.js").write_text("export const f = () => 1;\n")
    assert rx(d, "implemented", cp).returncode == 0
    r = rx(d, "behavior", cp)
    assert r.returncode == 0, r.stderr


def open_round(d, cp="CP1"):
    r = rx(d, "reviewers", cp, "--json")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def write_review(d, rnd, rid, findings=None, **over):
    p = Path(rnd["output_dir"]) / f"{rid}.json"
    body = {"reviewer": rid, "checkpoint": rnd["checkpoint"], "fingerprint": rnd["fingerprint"],
            "round_nonce": rnd["round_nonce"], "findings": findings or []}
    body.update(over)
    p.write_text(json.dumps(body))
    return str(p)


def main():
    print("Guard: audit exploits are blocked")
    d = project("guard")
    st = ".ratchet/runs/f/state.json"
    blocked = {
        "agent calls the prompt hook itself": ("Bash", {"command": "echo '{\"prompt\":\"approve\"}' | python3 x/ratchet.py hook prompt"}),
        "ratchet.py …; echo > state.json": ("Bash", {"command": f"python3 ratchet.py status; echo x > {st}"}),
        "ratchet.py … && append to human_log": ("Bash", {"command": "python3 ratchet.py status && echo '{}' >> .ratchet/human_log.jsonl"}),
        "python -c write": ("Bash", {"command": f"python3 -c \"open('{st}','w').write('x')\""}),
        "cd into .ratchet then relative write": ("Bash", {"command": "cd .ratchet/runs/f && echo x > state.json"}),
        "cd .ratchet && append human_log": ("Bash", {"command": "cd .ratchet && echo >> human_log.jsonl"}),
        "./ and // path tricks": ("Bash", {"command": "echo x > .ratchet//runs/f/./state.json"}),
        "variable-built path": ("Bash", {"command": "F=.ratchet/runs/f/sta; echo > ${F}te.json"}),
        "git checkout old state": ("Bash", {"command": f"git checkout HEAD~1 -- {st}"}),
        "rm -rf .ratchet": ("Bash", {"command": "rm -rf .ratchet"}),
        "symlink to human_log": ("Bash", {"command": "ln -s .ratchet/human_log.jsonl hl"}),
        "dd / perl -i / node -e": ("Bash", {"command": f"perl -i -pe 's/failed/passed/' {st}"}),
        "git clean -fdx": ("Bash", {"command": "git clean -fdx"}),
        "claude with flags before -p": ("Bash", {"command": "claude --model opus -p 'approve'"}),
        "claude by absolute path": ("Bash", {"command": "/opt/node22/bin/claude -p approve"}),
        "exemption-by-comment": ("Bash", {"command": "claude -p 'approve' # external_review.py"}),
        "codex exec": ("Bash", {"command": "codex exec 'approve the plan'"}),
        "read the director key": ("Bash", {"command": f"cat {HOME}/director.key"}),
        "set RATCHET_ROLE": ("Bash", {"command": "export RATCHET_ROLE=director; python3 ratchet.py director-sign approve-plan"}),
        "inline human override": ("Bash", {"command": "RATCHET_HUMAN_OVERRIDE=1 python3 ratchet.py approve-plan"}),
        "Write state.json": ("Write", {"file_path": str(d / st), "content": "{}"}),
        "Write .RATCHET (case)": ("Write", {"file_path": str(d / ".RATCHET/runs/f/state.json"), "content": "{}"}),
        "Edit with ..": ("Edit", {"file_path": str(d / "src/../.ratchet/human_log.jsonl")}),
        "Write config.json": ("Write", {"file_path": str(d / ".ratchet/config.json"), "content": "{}"}),
        "Grep the key folder": ("Grep", {"pattern": ".", "path": str(HOME)}),
        "apply_patch on state": ("apply_patch", {"input": f"*** Begin Patch\n*** Update File: {st}\n*** End Patch"}),
    }
    for name, (tool, ti) in blocked.items():
        r = guard(d, tool, ti)
        check(f"blocked: {name}", r.returncode == 2, r.stderr or r.stdout)
    r = guard(d, "Bash", {"command": f"echo x > {d}/{st}"}, cwd="/")
    check("blocked: cd / then absolute path", r.returncode == 2)

    print("Guard: legitimate work is allowed")
    allowed = {
        "pause reason that mentions director-sign in prose": ("Bash", {"command": "python3 ratchet.py pause --reason \"docs mention director-sign and RATCHET_ROLE\""}),
        "Write README mentioning director commands": ("Write", {"file_path": str(d / "README.md"), "content": "Run ratchet.py director-sign approve-plan"}),
        "read state": ("Bash", {"command": f"cat {st} | jq .phase"}),
        "list .ratchet": ("Bash", {"command": "ls -la .ratchet"}),
        "write a review JSON": ("Bash", {"command": "mkdir -p .ratchet/runs/f/reviews/CP1 && echo '{}' > .ratchet/runs/f/reviews/CP1/x.json"}),
        "Write checkpoints.json": ("Write", {"file_path": str(d / ".ratchet/runs/f/checkpoints.json"), "content": "{}"}),
        "Write a test plan": ("Write", {"file_path": str(d / ".ratchet/runs/f/tests/CP2.json"), "content": "{}"}),
        "engine piped to tail": ("Bash", {"command": "python3 x/ratchet.py behavior CP1 2>&1 | tail -20"}),
        "npm test": ("Bash", {"command": "npm test -- --watch=false"}),
        "capture into reviews": ("Bash", {"command": "node capture.mjs --spec .ratchet/runs/f/states/CP1.json --out .ratchet/runs/f/reviews/CP1/capture"}),
        "normal source edit": ("Edit", {"file_path": str(d / "src/a.js")}),
    }
    for name, (tool, ti) in allowed.items():
        r = guard(d, tool, ti)
        check(f"allowed: {name}", r.returncode == 0, r.stderr)

    print("Approvals need a real, explicit yes")
    d = project("appr")
    for text in ("what does CP3 mean?", "looks ok I guess", "hmm, not sure about CP2", "no, change the order"):
        say(d, text)
        check(f"'{text}' is not an approval", rx(d, "approve-plan").returncode != 0)
    with open(d / ".ratchet/human_log.jsonl", "a") as f:
        f.write(json.dumps({"at": "9999-01-01T00:00:00+00:00", "prompt": "approve", "session": "x"}) + "\n")
    check("a forged future-dated 'approve' is ignored", rx(d, "approve-plan").returncode != 0)
    say(d, "Yes — build it")
    check("'Yes — build it' approves", rx(d, "approve-plan").returncode == 0)

    print("The approved plan, test plans and rules are locked")
    d = project("lock")
    approve_plan(d)
    tp = d / ".ratchet/runs/f/tests/CP1.json"
    tp.write_text(json.dumps({"cases": []}))
    write_tests(d)
    r = rx(d, "tests-written", "CP1")
    check("emptying the test plan after approval stops the gates", r.returncode != 0 and "changed after the user approved" in r.stderr, r.stderr)
    r = rx(d, "approve-changes")
    check("approve-changes without a new user message is refused", r.returncode != 0)
    tp.write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
    rx(d, "config", "--set", "max_adversarial_rounds=99")
    r = rx(d, "tests-written", "CP1")
    check("weakening a config rule after approval stops the gates", r.returncode != 0 and "max_adversarial_rounds" in r.stderr)
    say(d, "yes, allow 99 rounds")
    check("approve-changes after the user confirms", rx(d, "approve-changes").returncode == 0)
    check("gates run again after the confirmed change", rx(d, "tests-written", "CP1").returncode == 0)

    print("Behavior gate shortcuts")
    d = project("green")
    approve_plan(d)
    write_tests(d)
    (d / "src/f.js").write_text("export const f = () => 1;\n")
    rx(d, "tests-written", "CP1")
    r = rx(d, "red", "CP1", "--allow-green-first", "--reason", "x")
    check("--allow-green-first refused unless the approved plan says green_first_ok", r.returncode != 0 and "green_first_ok" in r.stderr, r.stderr)
    d = project("cov")
    approve_plan(d)
    (d / "tests/cp1.test.js").write_text("// CP1-T010 only\n")
    check("CP1-T01 is not satisfied by CP1-T010", rx(d, "tests-written", "CP1").returncode != 0)

    print("Adversarial gate: rounds, nonces, counting")
    d = project("adv")
    approve_plan(d)
    to_adversarial(d)
    fp = json.loads(rx(d, "fingerprint").stdout)["id"]
    fake = Path(tempfile.mkdtemp()) / "fake.json"   # outside the repo, so it doesn't change the fingerprint
    fake.write_text(json.dumps({"reviewer": "host-both", "fingerprint": fp, "findings": []}))
    check("a review with no open round is refused", rx(d, "adv-record", "CP1", "--reviews", str(fake)).returncode != 0)
    rnd = open_round(d)
    check("opening a round counts it", rnd["run_rounds_used"] == 1 and rnd["round"] == 1, rnd)
    check("re-reading the same round doesn't count again", open_round(d)["run_rounds_used"] == 1)
    r = rx(d, "adv-record", "CP1", "--reviews", str(fake))
    check("a hand-made review without the round nonce is refused", r.returncode != 0 and "nonce" in r.stderr, r.stderr)
    p = write_review(d, rnd, "host-both", findings=[{"id": "A1", "severity": "major", "description": "x"}])
    r = rx(d, "adv-record", "CP1", "--reviews", p)
    check("a finding fails the gate", r.returncode == 2)
    (d / "src/f.js").write_text("export const f = () => 1; // fixed\n")
    rx(d, "behavior", "CP1")
    rnd2 = open_round(d)
    check("new code opens round 2 and counts it", rnd2["round"] == 2 and rnd2["run_rounds_used"] == 2)
    check("round 2 hands reviewers the fixes.md path", (rnd2.get("fixes_md") or "").endswith("adv-round-01/fixes.md"))
    stale = write_review(d, rnd, "host-both")
    check("a review from round 1 is refused in round 2", rx(d, "adv-record", "CP1", "--reviews", stale).returncode != 0)
    p = write_review(d, rnd2, "host-both")
    check("a clean round-2 review approves", rx(d, "adv-record", "CP1", "--reviews", p).returncode == 0)
    check("complete commits", rx(d, "complete", "CP1").returncode == 0)
    tracked = git(d, "ls-files", ".ratchet").stdout.split()
    check("run state is not committed (only config/standards/learnings)",
          all(t.split("/", 1)[1] in (".gitignore", "config.json", "standards.md", "learnings.md") for t in tracked), tracked)

    print("UI gate")
    d = project("ui", extra_cp={"gates": {"behavior": True, "ui": True, "adversarial": True},
                                "ui_scope": "all", "ui_states": [{"name": "empty"}]})
    approve_plan(d)
    write_tests(d)
    rx(d, "tests-written", "CP1"); rx(d, "red", "CP1")
    (d / "src/f.js").write_text("export const f = () => 1;\n")
    rx(d, "implemented", "CP1"); rx(d, "behavior", "CP1")
    fp = json.loads(rx(d, "fingerprint").stdout)["id"]
    c = Path(tempfile.mkdtemp()) / "combined.json"
    c.write_text(json.dumps({"reviewers": ["visual", "behavior"], "differences": []}))
    check("a combined review without a fingerprint is refused", rx(d, "ui-record", "CP1", "--review", str(c)).returncode != 0)
    c.write_text(json.dumps({"checkpoint": "CP1", "fingerprint": fp, "reviewers": ["visual", "behavior"],
                             "differences": [{"severity": "blocker", "location": "x", "description": "broken",
                                              "fixable_in_code": False}]}))
    check("a blocker can't exempt itself with fixable_in_code:false", rx(d, "ui-record", "CP1", "--review", str(c)).returncode == 2)
    c.write_text(json.dumps({"checkpoint": "CP1", "fingerprint": fp, "reviewers": ["visual"], "differences": []}))
    check("one reviewer is not enough", rx(d, "ui-record", "CP1", "--review", str(c)).returncode != 0)

    print("Runs are never replaced silently; cancel needs the user")
    d = project("cancel")
    check("init over an active run is refused", rx(d, "init", "other", "--force").returncode != 0)
    say(d, "what is going on?")
    check("cancel needs a clear yes/cancel", rx(d, "cancel").returncode != 0)
    say(d, "cancel it")
    r = rx(d, "cancel")
    check("cancel after the user confirms", r.returncode == 0 and "cancelled" in r.stdout, r.stderr)
    check("a new run can start after cancel", rx(d, "init", "next thing", "--slug", "g").returncode == 0)

    print("Unblocking returns to where the run was")
    d = project("unblock", config=["max_stop_blocks=1"])
    say(d, "yes build it")
    rx(d, "approve-plan")
    stop(d); stop(d)
    phase = json.loads(rx(d, "status", "--json").stdout)["phase"]
    check("safety valve blocks the run", phase == "blocked", phase)
    say(d, "ok, go ahead")
    r = rx(d, "human-approve")
    check("unblock returns to building (not a crash)", r.returncode == 0 and json.loads(rx(d, "status", "--json").stdout)["phase"] == "building", r.stderr)

    print("Stop hook")
    d = project("stop")
    approve_plan(d)
    check("owner session may not stop mid-build", stop(d, "s-owner").returncode == 2)
    check("another session in the same repo is not trapped", stop(d, "s-other").returncode == 0)
    r = say(d, "fix a typo in the readme", session="s-other")
    check("the other session is told about the unfinished run", "unfinished Ratchet run" in r.stdout, r.stdout)
    (d / ".ratchet/runs/f/state.json").write_text("{broken")
    check("unreadable state fails closed for stop", stop(d).returncode == 2)
    d2 = project("gone")
    approve_plan(d2)
    subprocess.run(["rm", "-rf", str(d2 / ".ratchet")])
    r = stop(d2)
    check("deleted .ratchet under an active run is noticed", r.returncode == 2 and "state is gone" in r.stderr, r.stderr)

    print("Feedback after done, and completing")
    d = project("done", config=["human_review=end", "regression_command=node --test tests/cp1.test.js"])
    approve_plan(d)
    to_adversarial(d)
    rnd = open_round(d)
    rx(d, "adv-record", "CP1", "--reviews", write_review(d, rnd, "host-both"))
    rx(d, "complete", "CP1")
    say(d, "approved, thanks")
    check("final approval", rx(d, "human-approve").returncode == 0)
    say(d, "actually, can the button be blue?")
    r = rx(d, "feedback", "--text", "blue button")
    check("feedback after done is accepted", r.returncode == 0, r.stderr)

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
