#!/usr/bin/env python3
"""
CP7 tests: review scripts sign the reviews they write.   python3 tests/test_signing.py

Kept separate from the trust suites (tests/test_trust_cp*.py) and tests/test_results.py (per-test report reading),
even though it reuses their low-level helpers (rx, git, js, make_env, now_dt, project building) by importing
tests/trust_helpers.py as a module, the way tests/test_results.py already does.

Every project here runs hermetically: a private HOME / RATCHET_HOME (tests/trust_helpers.py's make_env), agent-CLI
env vars scrubbed, and a private PATH holding only a handful of real tools plus, where a test needs an "other
provider" reviewer, a FAKE shell script standing in for that CLI (tests/test_providers.py's fake_bin pattern). No
real reviewer CLI is ever invoked; external_review.py and ui_review.py are the real scripts under test, run
against the fake CLI or (for host sub-agent reviews) simulated entirely through `ratchet.py hook guard`.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set (Ratchet's own gate uses this file
to grade this checkpoint): {"cases": [{"id": "CP7-T01", "status": "passed"|"failed", "message": "..."}]}.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import _tmp
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import trust_helpers as tt  # noqa: E402  (rx, git, js, make_env, now_dt, engine, cp3_* project helpers)

EXT = ROOT / "skills" / "ratchet-adversarial-loop" / "scripts" / "external_review.py"
UI_REVIEW = ROOT / "skills" / "ratchet-ui-compare" / "scripts" / "ui_review.py"

rx = tt.rx
git = tt.git
js = tt.js
make_env = tt.make_env
now_dt = tt.now_dt
engine = tt.engine
cp3_project = tt.cp3_project
cp3_ui_project = tt.cp3_ui_project
cp3_pass_behavior = tt.cp3_pass_behavior
cp3_open_round = tt.cp3_open_round
cp3_R0 = tt.cp3_R0
cp3_write_review = tt.cp3_write_review
cp3_fp = tt.cp3_fp
cp3_ui_round_dir = tt.cp3_ui_round_dir
cp3_write_combined = tt.cp3_write_combined
cp3_state = tt.cp3_state

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP7-[TX]\d+)\]")


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


# ---- fake "other provider" CLI (never a real reviewer CLI) -----------------------------------------------------

FAKE_TEMPLATE = r"""#!/bin/sh
# fake agent CLI: answers the connectivity probe, otherwise prints a fixed findings reply.
me=$(basename "$0")
here=$(dirname "$0")
out=""
args="$*"
while [ $# -gt 0 ]; do
  case "$1" in --output-last-message) out="$2"; shift 2;; *) shift;; esac
done
input=""
if [ ! -t 0 ]; then input=$(cat); fi
case "$input $args" in
  *"connectivity check"*) reply="RATCHET-PROBE-OK";;
  *) reply='__REPLY__';;
esac
if [ -n "$out" ]; then printf '%s' "$reply" > "$out"; fi
printf '%s\n' "$reply"
"""


def fake_bin(name, reply_json):
    b = Path(_tmp.mkdtemp(prefix="ratchet-sign-fakebin-"))
    for n in ("python3", "git", "node", "sh", "env", "cat", "dirname", "basename"):
        p = shutil.which(n)
        if p:
            (b / n).symlink_to(p)
    script = FAKE_TEMPLATE.replace("__REPLY__", reply_json.replace("\\", "\\\\").replace("'", "'\\''"))
    (b / name).write_text(script)
    (b / name).chmod(0o755)
    return b


def with_fake_provider(env, reply_json, pid="codex", preset="codex"):
    """Points PATH at a fake `pid` CLI and returns a new env with it; no real CLI is ever reachable."""
    b = fake_bin(pid, reply_json)
    return dict(env, PATH=str(b))


def use_codex_provider(d, env):
    r = rx(d, env, "config", "--set", 'providers=[{"id":"codex","preset":"codex"}]')
    assert r.returncode == 0, r.stderr + r.stdout


def run_external(d, env, rid, out, fixes=None):
    cmd = [sys.executable, str(EXT), "--reviewer", rid, "--cp", "CP1", "--out", str(out)]
    if fixes:
        cmd += ["--fixes", str(fixes)]
    return subprocess.run(cmd, cwd=d, capture_output=True, text=True, env=env)


def run_combine(d, env, inputs, out, checkpoint, manifest):
    cmd = [sys.executable, str(UI_REVIEW), "combine", "--inputs", *[str(i) for i in inputs],
           "--out", str(out), "--checkpoint", checkpoint, "--manifest", str(manifest)]
    return subprocess.run(cmd, cwd=d, capture_output=True, text=True, env=env)


def guard(d, env, tool, tool_input, host="claude", agent=None, cwd=None):
    """What fires when a real tool call is about to run: `ratchet.py hook guard --host X`. Returns the engine's
    CompletedProcess (returncode 0 = allowed, nonzero = blocked, for every non-Copilot host)."""
    payload = {"cwd": str(cwd or d), "tool_name": tool, "tool_input": tool_input}
    if agent:
        payload.update(agent_type=agent, agent_id="a-" + agent)
    return rx(d, env, "hook", "guard", "--host", host, stdin=json.dumps(payload))


# ---- two-checkpoint project (T05: a round's secret can't cross checkpoints) -------------------------------------

def project2(env):
    d = Path(_tmp.mkdtemp(prefix="ratchet-sign-p2-"))
    git(d, "init", "-q")
    git(d, "config", "user.email", "t@t")
    git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    tp = d / "t.jsonl"
    tp.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "please build the feature"},
                              "timestamp": now_dt().isoformat()}) + "\n")
    (d / ".ratchet").mkdir(exist_ok=True)
    rx(d, env, "hook", "prompt", "--host", "claude",
       stdin=json.dumps({"cwd": str(d), "prompt": "please build the feature", "session_id": "s-owner",
                         "transcript_path": str(tp)}))
    r = rx(d, env, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr + r.stdout
    rx(d, env, "config", "--set", "min_review_seconds=0", "providers=[]")
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cps = []
    for i in (1, 2):
        cp = {"id": f"CP{i}", "title": f"Step {i}", "summary": "s", "done_when": ["works"],
              "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
              "tests": {"files": [f"tests/cp{i}.test.js"], "command": f"test -f src/f{i}.js"}}
        cps.append(cp)
        (run / "tests" / f"CP{i}.json").write_text(json.dumps({"cases": [{"id": f"CP{i}-T01", "layer": "code"}]}))
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                       "checkpoints": cps}))
    r = rx(d, env, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    rx(d, env, "ask", "approve-plan")
    tp.open("a").write(json.dumps({"type": "user", "message": {"role": "user", "content": "yes, go ahead"},
                                   "timestamp": now_dt().isoformat()}) + "\n")
    rx(d, env, "hook", "prompt", "--host", "claude",
       stdin=json.dumps({"cwd": str(d), "prompt": "yes, go ahead", "session_id": "s-owner",
                         "transcript_path": str(tp)}))
    r = rx(d, env, "approve-plan")
    assert r.returncode == 0, r.stderr + r.stdout
    return d


def write_tests2(d, cp, idx):
    (d / f"tests/cp{idx}.test.js").write_text(
        "import t from 'node:test'; import a from 'node:assert';\n"
        f"t('[{cp}-T01] works', () => a.equal(1, 1));\n")


def pass_behavior2(d, env, cp, idx):
    write_tests2(d, cp, idx)
    assert rx(d, env, "tests-written", cp).returncode == 0
    assert rx(d, env, "red", cp).returncode == 0
    (d / f"src/f{idx}.js").write_text("export const f = () => 1;\n")
    assert rx(d, env, "implemented", cp).returncode == 0
    r = rx(d, env, "behavior", cp)
    assert r.returncode == 0, r.stderr + r.stdout


def open_round(d, env, cp):
    r = rx(d, env, "reviewers", cp, "--json")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def record_host_review(d, env, rnd, findings=None, host="claude", confirm=True, cp="CP1", files_reviewed=None):
    """Drives a host sub-agent review end to end: (optionally) the guard approves the write, the file is written,
    adv-record is given it. Returns (path, adv-record CompletedProcess)."""
    rid = cp3_R0(rnd)
    path = Path(rnd["output_dir"]) / f"{rid}.json"
    if confirm:
        g = guard(d, env, "Write", {"file_path": str(path)}, host=host, agent="ratchet-adversary")
        assert g.returncode == 0, (g.returncode, g.stdout, g.stderr)
    over = {"files_reviewed": files_reviewed} if files_reviewed else {}
    cp3_write_review(path, rnd, rid, findings=findings or [], **over)
    r = rx(d, env, "adv-record", cp, "--reviews", str(path))
    return path, r


# ---- RATCHET_HOME secret discovery (T09-T11): find whatever Ratchet newly wrote under its own per-user folder --

def home_files(home):
    base = Path(home)
    if not base.exists():
        return {}
    out = {}
    for p in base.rglob("*"):
        if p.is_file():
            try:
                out[str(p)] = p.read_bytes()
            except OSError:
                pass
    return out


TOKEN_RE = re.compile(rb"[0-9a-fA-F]{24,}|[A-Za-z0-9_-]{24,}")

# file-hashes.json is ratchet.py's own (pre-existing, unrelated) tamper-evidence store: it legitimately rewrites
# on almost every command (new content hashes for files it guards) and its keys embed the project's own tmp-dir
# path, so diffing it would make every one of these tests flaky regardless of CP7. Not what this checkpoint signs.
IGNORED_HOME_FILES = {"file-hashes.json"}


def new_tokens(before, after, exclude, project_dir=None):
    """Byte tokens that appear in a file under `after` that is new or changed since `before`, excluding any token
    equal to (or contained in / containing) something already known to be public (the nonce, the fingerprint, or
    the project's own directory path)."""
    ex = [e.encode() for e in exclude if e]
    pd = str(project_dir).encode() if project_dir else b""
    found = set()
    for path, data in after.items():
        if Path(path).name in IGNORED_HOME_FILES:
            continue
        if before.get(path) != data:
            for m in TOKEN_RE.finditer(data):
                tok = m.group(0)
                if any(tok == e or tok in e or e in tok for e in ex):
                    continue
                if pd and (tok in pd or pd in tok):
                    continue
                found.add(tok)
    return found


# ---- CP7-T01 ----

def t01_handtyped_refused():
    print("CP7-T01: a hand-typed review with the right nonce/fingerprint is refused as unsigned")
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    path = cp3_write_review(Path(rnd["output_dir"]) / f"{rid}.json", rnd, rid, findings=[])
    r = rx(d, env, "adv-record", "CP1", "--reviews", path)
    msg = r.stdout + r.stderr
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    check("[CP7-T01] a hand-typed review copying the real nonce and fingerprint is refused, naming it unsigned",
          r.returncode != 0 and re.search(r"unsigned|not signed|no signature|missing signature", msg, re.I) is not None,
          (r.returncode, msg))
    check("[CP7-T01] the gate does not pass off the back of it",
          cp1["gates"]["adversarial"].get("status") != "passed"
          and not (cp1.get("adv_round") or {}).get("recorded"), cp1["gates"]["adversarial"])


# ---- CP7-T02 ----

def t02_external_review_accepted():
    print("CP7-T02: a review written by external_review.py for its own round is accepted")
    env = make_env("claude")
    env = with_fake_provider(env, json.dumps({"findings": []}))
    d = cp3_project(env)
    use_codex_provider(d, env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    check("[CP7-T02] setup: the round's reviewer is the fake command provider",
          rid.startswith("codex"), rnd["reviewers"])
    out = Path(rnd["output_dir"]) / f"{rid}.json"
    rp = run_external(d, env, rid, out)
    assert rp.returncode == 0, rp.stderr + rp.stdout
    r = rx(d, env, "adv-record", "CP1", "--reviews", str(out))
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    check("[CP7-T02] adv-record accepts the script-signed review and the round passes",
          r.returncode == 0 and cp1["gates"]["adversarial"]["status"] == "passed",
          (r.returncode, r.stderr, cp1["gates"]["adversarial"]))


# ---- CP7-T03 ----

def t03_ui_combine_accepted():
    print("CP7-T03: the combined result ui_review.py's combine step produces for a round is accepted for that round")
    env = make_env("claude")
    d = cp3_ui_project(env)
    cp3_pass_behavior(d, env)
    fp = cp3_fp(d, env)
    manifest = ui_manifest(d, fp)
    assert run_stage(d, env, manifest).returncode == 0  # opens round 1, same as a reviewer sub-agent would find it
    round1 = cp3_ui_round_dir(d, 1)
    visual = round1 / "visual.json"
    behavior = round1 / "behavior.json"
    for path, agent in ((visual, "ratchet-visual-reviewer"), (behavior, "ratchet-behavior-reviewer")):
        g = guard(d, env, "Write", {"file_path": str(path)}, agent=agent)  # the reviewer sub-agent's own write, seen
        assert g.returncode == 0, g.stderr + g.stdout
    write_role(visual, "visual")
    write_role(behavior, "behavior")
    combined = round1 / "combined.json"
    rp = run_combine(d, env, [visual, behavior], combined, "CP1", manifest)
    assert rp.returncode == 0, rp.stderr + rp.stdout
    r = rx(d, env, "ui-record", "CP1", "--review", str(combined))
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    check("[CP7-T03] ui-record accepts the script-combined review built from real reviewer provenance (passes on "
          "its merits, not refused for lacking a signature)",
          r.returncode == 0 and cp1["gates"]["ui"]["status"] == "passed",
          (r.returncode, r.stderr, cp1["gates"]["ui"]))


# ---- CP7-T04 ----

def t04_stale_round_signature_does_not_revalidate():
    print("CP7-T04: a signed review from a closed round does not validate against a new round")
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    rnd1 = cp3_open_round(d, env)
    path1, r1 = record_host_review(d, env, rnd1, findings=[])
    assert r1.returncode == 0, r1.stderr + r1.stdout
    (d / "src/f.js").write_text("export const f = () => 1; // tweak, forces a new round\n")
    rnd2 = cp3_open_round(d, env)
    check("[CP7-T04] setup: the code tweak opened a fresh round 2",
          rnd2["round"] == rnd1["round"] + 1 and rnd2["fingerprint"] != rnd1["fingerprint"], (rnd1, rnd2))
    rid2 = cp3_R0(rnd2)
    copied = Path(rnd2["output_dir"]) / f"{rid2}.json"
    shutil.copy(path1, copied)  # byte for byte, including whatever signing fields round 1's file carries
    r2 = rx(d, env, "adv-record", "CP1", "--reviews", str(copied))
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    check("[CP7-T04] the stale round-1 file is refused for round 2 (nonce no longer matches, signature doesn't "
          "re-validate), and round 2 itself is never recorded",
          r2.returncode != 0 and not (cp1.get("adv_round") or {}).get("recorded"),
          (r2.returncode, r2.stdout + r2.stderr, (cp1.get("adv_round") or {}).get("recorded")))


# ---- CP7-T05 ----

def t05_secret_does_not_cross_checkpoints():
    print("CP7-T05: a round's secret cannot sign for a different checkpoint")
    env = make_env("claude")
    d = project2(env)
    pass_behavior2(d, env, "CP1", 1)
    rnd1 = open_round(d, env, "CP1")
    path1, r1 = record_host_review(d, env, rnd1, findings=[], cp="CP1", files_reviewed=["src/f1.js"])
    assert r1.returncode == 0, r1.stderr + r1.stdout
    assert rx(d, env, "complete", "CP1").returncode == 0
    pass_behavior2(d, env, "CP2", 2)
    rnd2 = open_round(d, env, "CP2")
    rid2 = cp3_R0(rnd2)
    body = json.loads(path1.read_text())
    body.update(checkpoint="CP2", fingerprint=rnd2["fingerprint"], round_nonce=rnd2["round_nonce"], reviewer=rid2)
    relabeled = Path(rnd2["output_dir"]) / f"{rid2}.json"
    relabeled.write_text(json.dumps(body))
    r2 = rx(d, env, "adv-record", "CP2", "--reviews", str(relabeled))
    cp2 = cp3_state(d)["checkpoints"]["CP2"]
    check("[CP7-T05] a review signed for CP1's round, relabelled with CP2's checkpoint/fingerprint/nonce, is "
          "refused as unsigned for CP2's round",
          r2.returncode != 0 and cp2["gates"]["adversarial"].get("status") != "passed",
          (r2.returncode, r2.stdout + r2.stderr))


# ---- CP7-T06 ----

def t06_tamper_breaks_signature():
    print("CP7-T06: editing a signed review after signing breaks the signature")
    env = make_env("claude")
    finding = {"id": "F1", "severity": "minor", "file": "src/f.js", "description": "name could be clearer"}
    env = with_fake_provider(env, json.dumps({"findings": [finding]}))
    d = cp3_project(env)
    use_codex_provider(d, env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    out = Path(rnd["output_dir"]) / f"{rid}.json"
    rp = run_external(d, env, rid, out)
    assert rp.returncode == 0, rp.stderr + rp.stdout
    body = json.loads(out.read_text())
    assert body.get("findings"), body
    body["findings"][0]["description"] = body["findings"][0]["description"] + "!"  # one character changed
    out.write_text(json.dumps(body))
    r = rx(d, env, "adv-record", "CP1", "--reviews", str(out))
    msg = r.stdout + r.stderr
    check("[CP7-T06] a one-character edit to a signed review is refused as unsigned/tampered, naming the file, "
          "even though nonce/fingerprint/checkpoint still match",
          r.returncode != 0 and str(out) in msg
          and re.search(r"unsigned|tamper|signature", msg, re.I) is not None, (r.returncode, msg))


# ---- CP7-T07 ----

def t07_host_subagent_confirmed_on_claude():
    print("CP7-T07: a host sub-agent review is accepted on Claude Code, and its writer is recorded as confirmed")
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    check("[CP7-T07] setup: the round's reviewer is the host's own sub-agent",
          cp3_R0(rnd).startswith("host"), rnd["reviewers"])
    path, r = record_host_review(d, env, rnd, findings=[], host="claude", confirm=True)
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    status = rx(d, env, "status").stdout
    blob = json.dumps(cp1)
    check("[CP7-T07] adv-record accepts the confirmed host sub-agent review and the round passes",
          r.returncode == 0 and cp1["gates"]["adversarial"]["status"] == "passed",
          (r.returncode, r.stderr, cp1["gates"]["adversarial"]))
    check("[CP7-T07] neither status nor the recorded evidence calls this review 'not independently confirmed'",
          "not independently confirmed" not in status and "not independently confirmed" not in blob,
          (status, blob))


# ---- CP7-T08 ----

def t08_host_subagent_unconfirmed_elsewhere():
    print("CP7-T08: a host sub-agent review on a non-Claude CLI is accepted but marked not independently confirmed")
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    # the guard call that would confirm the writer is made as Codex would make it: Codex's hook payload never
    # carries agent_type, and even when it does, decide_guard does not trust identity on this host.
    path, r = record_host_review(d, env, rnd, findings=[], host="codex", confirm=True)
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    status_j = js(rx(d, env, "status", "--json"))
    blob = json.dumps(cp1)
    check("[CP7-T08] adv-record still accepts the review (the run is never stalled for lack of confirmation)",
          r.returncode == 0 and cp1["gates"]["adversarial"]["status"] == "passed",
          (r.returncode, r.stderr, cp1["gates"]["adversarial"]))
    check("[CP7-T08] `status --json` says this review is not independently confirmed",
          "not independently confirmed" in json.dumps(status_j), status_j)
    check("[CP7-T08] the round's recorded evidence also says it is not independently confirmed",
          "not independently confirmed" in blob, blob)


# ---- CP7-T09 ----

def t09_secret_never_printed():
    print("CP7-T09: the round's secret is never printed")
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    home = env["RATCHET_HOME"]
    before = home_files(home)
    rnd = cp3_open_round(d, env)
    after = home_files(home)
    secrets = new_tokens(before, after, {rnd["fingerprint"], rnd["round_nonce"]}, project_dir=d)
    check("[CP7-T09] opening the round creates a per-round secret under RATCHET_HOME, distinct from the public "
          "nonce and fingerprint", len(secrets) > 0, sorted(after.keys()))
    plain = rx(d, env, "reviewers", "CP1").stdout
    as_json = rx(d, env, "reviewers", "CP1", "--json").stdout
    check("[CP7-T09] the round's secret value appears in neither plain nor --json `reviewers` output",
          all(s not in plain.encode() and s not in as_json.encode() for s in secrets), sorted(secrets))


# ---- CP7-T10 ----

def t10_secret_outside_project():
    print("CP7-T10: the round's secret is stored outside the project folder")
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    home = env["RATCHET_HOME"]
    before = home_files(home)
    rnd = cp3_open_round(d, env)
    after = home_files(home)
    secrets = new_tokens(before, after, {rnd["fingerprint"], rnd["round_nonce"]}, project_dir=d)
    check("[CP7-T10] setup: a round secret was found under RATCHET_HOME to search for",
          len(secrets) > 0, sorted(after.keys()))
    project_blob = b"\n".join(p.read_bytes() for p in Path(d).rglob("*") if p.is_file())
    check("[CP7-T10] none of the round's secret value(s) appear anywhere under the project directory tree "
          "(including .ratchet/)", all(s not in project_blob for s in secrets), sorted(secrets))
    check("[CP7-T10] the secret only exists under RATCHET_HOME (it was found there, by construction)",
          all(any(s in data for data in after.values()) for s in secrets), sorted(secrets))


# ---- CP7-T11 ----

def t11_secret_gone_after_record():
    print("CP7-T11: the round's secret is gone once the round is recorded")
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    home = env["RATCHET_HOME"]
    before = home_files(home)
    rnd = cp3_open_round(d, env)
    after_open = home_files(home)
    secrets = new_tokens(before, after_open, {rnd["fingerprint"], rnd["round_nonce"]}, project_dir=d)
    check("[CP7-T11] setup: a round secret exists right after the round opens", len(secrets) > 0,
          sorted(after_open.keys()))
    path, r = record_host_review(d, env, rnd, findings=[], host="claude", confirm=True)
    assert r.returncode == 0, r.stderr + r.stdout
    after_record = home_files(home)
    check("[CP7-T11] once the round is recorded, none of its secret value(s) can still be found under "
          "RATCHET_HOME (so the already-accepted review file can't be replayed against a later round)",
          all(all(s not in data for data in after_record.values()) for s in secrets), sorted(secrets))


# ---- CP7-T12 ----

def t12_signed_review_with_findings_still_flows():
    print("CP7-T12: signed reviews with real findings still flow through normally")
    env = make_env("claude")
    finding = {"id": "F1", "severity": "major", "file": "src/f.js", "line": 1,
               "description": "off-by-one in the loop bound", "rule": "loop bounds must be checked",
               "fix": "use < not <=", "user_impact": "a shopper's total is wrong by one unit"}
    env = with_fake_provider(env, json.dumps({"findings": [finding]}))
    d = cp3_project(env)
    use_codex_provider(d, env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    out = Path(rnd["output_dir"]) / f"{rid}.json"
    rp = run_external(d, env, rid, out)
    assert rp.returncode == 0, rp.stderr + rp.stdout
    r = rx(d, env, "adv-record", "CP1", "--reviews", str(out))
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    check("[CP7-T12] a signed review carrying a real major finding is accepted as a review (not rejected for "
          "being signed) and blocks the gate",
          r.returncode == 2 and cp1["gates"]["adversarial"]["status"] == "failed",
          (r.returncode, r.stderr, cp1["gates"]["adversarial"]))
    open_findings = Path(d) / ".ratchet/runs/f/evidence/CP1/adversarial/round-01/open-findings.json"
    found = json.loads(open_findings.read_text()) if open_findings.exists() else []
    check("[CP7-T12] the finding is recorded for the fixer, exactly as the severity rules require",
          any(f.get("id") == "F1" and f.get("severity") == "major" for f in found), found)


# ---- CP7-T13 ----

def t13_handtyped_ui_review_refused():
    print("CP7-T13: a hand-typed combined UI review is refused the same way as an unsigned adversarial review")
    env = make_env("claude")
    d = cp3_ui_project(env)
    cp3_pass_behavior(d, env)
    fp = cp3_fp(d, env)
    round1 = cp3_ui_round_dir(d, 1)
    combined = round1 / "combined.json"
    cp3_write_combined(combined, fp, compared={"spec_sha256": "s1", "scope": "all"}, diffs=[])
    r = rx(d, env, "ui-record", "CP1", "--review", str(combined))
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    msg = r.stdout + r.stderr
    check("[CP7-T13] a hand-typed combined.json with a plausible verdict and fingerprint is refused as unsigned",
          r.returncode != 0 and cp1["gates"]["ui"].get("status") != "passed"
          and re.search(r"unsigned|not signed|no signature|missing signature", msg, re.I) is not None,
          (r.returncode, msg))


# ---- CP7-T14 ----

def t14_readme_review_checks_row():
    print("CP7-T14: the README's review-checks row matches what each CLI now enforces")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    check("[CP7-T14] README says a script-signed review is required, or a host sub-agent review is accepted",
          re.search(r"script-signed review|signed review", readme, re.I) is not None, readme[:0])
    check("[CP7-T14] README says only Claude Code confirms the sub-agent writer",
          re.search(r"only Claude Code confirms", readme, re.I) is not None, readme[:0])
    check("[CP7-T14] README says the other CLIs' reviews are marked 'not independently confirmed'",
          "not independently confirmed" in readme, readme[:0])
    check("[CP7-T14] README states the protection is against a different process on the same machine, not the "
          "operating system",
          re.search(r"different process on the same machine.{0,40}not.{0,10}(the )?operating system", readme, re.I)
          is not None, readme[:0])


TESTS = [
    ("CP7-T01", t01_handtyped_refused),
    ("CP7-T02", t02_external_review_accepted),
    ("CP7-T03", t03_ui_combine_accepted),
    ("CP7-T04", t04_stale_round_signature_does_not_revalidate),
    ("CP7-T05", t05_secret_does_not_cross_checkpoints),
    ("CP7-T06", t06_tamper_breaks_signature),
    ("CP7-T07", t07_host_subagent_confirmed_on_claude),
    ("CP7-T08", t08_host_subagent_unconfirmed_elsewhere),
    ("CP7-T09", t09_secret_never_printed),
    ("CP7-T10", t10_secret_outside_project),
    ("CP7-T11", t11_secret_gone_after_record),
    ("CP7-T12", t12_signed_review_with_findings_still_flows),
    ("CP7-T13", t13_handtyped_ui_review_refused),
    ("CP7-T14", t14_readme_review_checks_row),
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
        except Exception as e:  # a crash still counts as a result for this case, not a silent drop
            global FAIL
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


# ---- CP7 adversarial round 1: added cases (CP7-X01 .. CP7-X17), registered in TESTS below ------------------------

GO_WITHOUT_HOOKS = "yes, go ahead without hooks"
DIFFS = [{"id": "D1", "severity": "minor", "location": "x", "description": "issue", "fixable_in_code": True}]


def x_project_at(d, env):
    """cp3_project's steps, run at a given folder (X01 starts a new run at the same path as an abandoned one)."""
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    git(d, "init", "-q")
    git(d, "config", "user.email", "t@t")
    git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    tp = d / "t.jsonl"
    tp.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "please build the feature"},
                              "timestamp": now_dt().isoformat()}) + "\n")
    (d / ".ratchet").mkdir(exist_ok=True)
    rx(d, env, "hook", "prompt", "--host", "claude",
       stdin=json.dumps({"cwd": str(d), "prompt": "please build the feature", "session_id": "s-owner",
                         "transcript_path": str(tp)}))
    r = rx(d, env, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr + r.stdout
    rx(d, env, "config", "--set", "min_review_seconds=0", "providers=[]")
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
          "tests": {"files": ["tests/cp1.test.js"], "command": "test -f src/f.js"}}
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                       "checkpoints": [cp]}))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
    r = rx(d, env, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    rx(d, env, "ask", "approve-plan")
    say_x(d, env, "yes, go ahead")
    r = rx(d, env, "approve-plan")
    assert r.returncode == 0, r.stderr + r.stdout
    return d


def say_x(d, env, text):
    """The user types `text` in the project's (cp3_project-style) chat: transcript line plus the prompt hook."""
    tp = Path(d) / "t.jsonl"
    with tp.open("a") as f:
        f.write(json.dumps({"type": "user", "message": {"role": "user", "content": text},
                            "timestamp": now_dt().isoformat()}) + "\n")
    return rx(d, env, "hook", "prompt", "--host", "claude",
              stdin=json.dumps({"cwd": str(d), "prompt": text, "session_id": "s-owner", "transcript_path": str(tp)}))


def raw_fake_bin(name, body):
    """A fake CLI whose whole script body is given (no reply escaping), on a PATH holding only a few real tools."""
    b = Path(_tmp.mkdtemp(prefix="ratchet-sign-rawbin-"))
    for n in ("python3", "git", "node", "sh", "env", "cat", "dirname", "basename"):
        p = shutil.which(n)
        if p:
            (b / n).symlink_to(p)
    (b / name).write_text("#!/bin/sh\n" + body)
    (b / name).chmod(0o755)
    return b


def ui_manifest(d, fp=None, where=None):
    """A capture manifest under reviews/CP1/ (where capture.mjs writes it)."""
    folder = where or (Path(d) / ".ratchet/runs/f/reviews/CP1/capture")
    folder.mkdir(parents=True, exist_ok=True)
    m = folder / "manifest.json"
    body = {"checkpoint": "CP1", "review_scope": "all", "spec_sha256": "s1", "states": []}
    if fp:
        body["fingerprint"] = fp
    m.write_text(json.dumps(body))
    return m


def run_stage(d, env, manifest):
    r = subprocess.run([sys.executable, str(UI_REVIEW), "stage", "--manifest", str(manifest), "--scope", "all"],
                       cwd=d, capture_output=True, text=True, env=env)
    if r.returncode == 0:
        for tmp in json.loads(r.stdout).values():
            shutil.rmtree(tmp, ignore_errors=True)
    return r


def write_role(path, role, diffs=None):
    Path(path).write_text(json.dumps({"reviewer": role, "verdict": "FAIL" if diffs else "PASS",
                                      "differences": diffs or []}))
    return Path(path)


def viewer_rounds(d):
    p = Path(d) / ".ratchet/runs/f/viewer-data.json"
    data = json.loads(p.read_text()) if p.exists() else {}
    return ((data.get("reviews") or {}).get("CP1") or {}).get("rounds") or []


def x01_abandoned_round_record_not_reused():
    print("CP7-X01: an abandoned round's writer record never confirms a review in a new run at the same path")
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    rnd1 = cp3_open_round(d, env)
    rid = cp3_R0(rnd1)
    g = guard(d, env, "Write", {"file_path": str(Path(rnd1["output_dir"]) / f"{rid}.json")}, agent="ratchet-adversary")
    assert g.returncode == 0, g.stderr
    for name in (".ratchet", ".git", "src", "tests"):  # the round is abandoned; the run is started over, same slug
        shutil.rmtree(d / name)
    (d / "t.jsonl").unlink()
    x_project_at(d, env)
    cp3_pass_behavior(d, env)
    rnd2 = cp3_open_round(d, env)
    check("[CP7-X01] setup: the new run reaches the same round, reviewer and folder",
          rnd2["round"] == rnd1["round"] and cp3_R0(rnd2) == rid and rnd2["output_dir"] == rnd1["output_dir"],
          (rnd1, rnd2))
    path = cp3_write_review(Path(rnd2["output_dir"]) / f"{rid}.json", rnd2, rid, findings=[])  # no guard call
    r = rx(d, env, "adv-record", "CP1", "--reviews", path)
    cp1 = cp3_state(d)["checkpoints"]["CP1"]
    check("[CP7-X01] a review the guard never saw written is refused as unsigned, not confirmed by the old record",
          r.returncode != 0 and "unsigned" in (r.stdout + r.stderr) and not (cp1.get("adv_round") or {}).get("recorded"),
          (r.returncode, r.stdout + r.stderr))


def x02_signed_review_refused_in_other_round():
    print("CP7-X02: a review external_review.py signed for round 1, re-stamped for round 2, is refused")
    env = with_fake_provider(make_env("claude"), json.dumps({"findings": []}))
    d = cp3_project(env)
    use_codex_provider(d, env)
    cp3_pass_behavior(d, env)
    rnd1 = cp3_open_round(d, env)
    rid = cp3_R0(rnd1)
    out1 = Path(rnd1["output_dir"]) / f"{rid}.json"
    rp = run_external(d, env, rid, out1)
    assert rp.returncode == 0, rp.stderr + rp.stdout
    (d / "src/f.js").write_text("export const f = () => 1; // tweak, forces a new round\n")
    rnd2 = cp3_open_round(d, env)
    check("[CP7-X02] setup: round 2 is open with the same reviewer", rnd2["round"] == 2 and cp3_R0(rnd2) == rid,
          rnd2)
    body = json.loads(out1.read_text())
    check("[CP7-X02] setup: the round-1 review is signed", isinstance(body.get("signature"), dict), body)
    body.update(round_nonce=rnd2["round_nonce"], fingerprint=rnd2["fingerprint"])  # signature kept as it was
    out2 = Path(rnd2["output_dir"]) / f"{rid}.json"
    out2.write_text(json.dumps(body))
    r = rx(d, env, "adv-record", "CP1", "--reviews", str(out2))
    msg = r.stdout + r.stderr
    check("[CP7-X02] refused because its signature does not match round 2",
          r.returncode != 0 and "signature that does not match" in msg
          and not (cp3_state(d)["checkpoints"]["CP1"].get("adv_round") or {}).get("recorded"), (r.returncode, msg))


def x03_ui_combined_other_round_and_edit():
    print("CP7-X03: a signed combined UI review is refused in another round, and after a one-character edit")
    env = make_env("claude")
    d = cp3_ui_project(env)
    cp3_pass_behavior(d, env)
    fp = cp3_fp(d, env)
    c1 = tt.cp3_signed_combined(d, env, fp, diffs=DIFFS)
    r1 = rx(d, env, "ui-record", "CP1", "--review", str(c1))
    check("[CP7-X03] setup: round 1 is recorded (fails on its difference)", r1.returncode == 2, (r1.returncode, r1.stderr))
    copied = cp3_ui_round_dir(d, 2) / "combined.json"
    shutil.copy(c1, copied)
    r2 = rx(d, env, "ui-record", "CP1", "--review", str(copied))
    ui = cp3_state(d)["checkpoints"]["CP1"]["gates"]["ui"]
    check("[CP7-X03] round 1's signed combined.json does not count for round 2",
          r2.returncode != 0 and re.search(r"unsigned|does not count", r2.stdout + r2.stderr) is not None
          and ui["attempts"] == 1,
          (r2.returncode, r2.stdout + r2.stderr, ui))
    c2 = tt.cp3_signed_combined(d, env, fp, n=2)
    old = cp3_ui_round_dir(d, 2) / "old.json"
    shutil.copy(c1, old)
    r2b = rx(d, env, "ui-record", "CP1", "--review", str(old))
    check("[CP7-X03] with round 2 open (its own secret exists), round 1's signed file is refused as unsigned",
          r2b.returncode != 0 and "unsigned" in (r2b.stdout + r2b.stderr), (r2b.returncode, r2b.stderr))
    body = json.loads(Path(c2).read_text())
    body["summary"] = body["summary"] + "!"
    Path(c2).write_text(json.dumps(body))
    r3 = rx(d, env, "ui-record", "CP1", "--review", str(c2))
    check("[CP7-X03] a one-character edit to round 2's signed combined.json is refused",
          r3.returncode != 0 and re.search(r"unsigned|changed after", r3.stdout + r3.stderr) is not None
          and cp3_state(d)["checkpoints"]["CP1"]["gates"]["ui"]["status"] != "passed", (r3.returncode, r3.stderr))


def x04_combine_refuses_unproven_inputs():
    print("CP7-X04: combine signs only per-role reviews whose origin checks out")
    env = make_env("claude")
    d = cp3_ui_project(env)
    cp3_pass_behavior(d, env)
    fp = cp3_fp(d, env)
    round1 = cp3_ui_round_dir(d, 1)
    manifest = ui_manifest(d, fp)
    out = round1 / "combined.json"
    tmp = Path(_tmp.mkdtemp(prefix="ratchet-sign-x04-"))
    ra = run_combine(d, env, [write_role(tmp / "visual.json", "visual"), write_role(tmp / "behavior.json", "behavior")],
                     out, "CP1", manifest)
    check("[CP7-X04] hand-typed inputs outside the round folder are refused and nothing is written",
          ra.returncode != 0 and not out.exists() and "this round's folder" in ra.stderr, (ra.returncode, ra.stderr))
    assert run_stage(d, env, manifest).returncode == 0
    v, b = write_role(round1 / "visual.json", "visual"), write_role(round1 / "behavior.json", "behavior")
    rb = run_combine(d, env, [v, b], out, "CP1", manifest)
    check("[CP7-X04] hand-typed inputs in the round folder (no reviewer write seen) are refused, naming the Write "
          "tool", rb.returncode != 0 and not out.exists() and "Write tool" in rb.stderr, (rb.returncode, rb.stderr))
    g = guard(d, env, "Write", {"file_path": str(v)})  # the main session, not a reviewer sub-agent
    assert g.returncode == 0, g.stderr
    rc = run_combine(d, env, [v, b], out, "CP1", manifest)
    check("[CP7-X04] a per-role file the guard saw the main session write is refused, saying so",
          rc.returncode != 0 and not out.exists() and "main session" in rc.stderr, (rc.returncode, rc.stderr))
    other = Path(_tmp.mkdtemp(prefix="ratchet-sign-x04m-")) / "manifest.json"
    other.write_text(manifest.read_text())
    for p, agent in ((b, "ratchet-behavior-reviewer"),):
        assert guard(d, env, "Write", {"file_path": str(p)}, agent=agent).returncode == 0
    rd = run_combine(d, env, [b], out, "CP1", other)
    check("[CP7-X04] a capture manifest from outside reviews/CP1/ is refused",
          rd.returncode != 0 and not out.exists() and "manifest" in rd.stderr, (rd.returncode, rd.stderr))


def x05_combine_accepts_reviewer_written_inputs():
    print("CP7-X05: combine accepts per-role reviews the guard saw the reviewer sub-agents write")
    env = make_env("claude")
    d = cp3_ui_project(env)
    cp3_pass_behavior(d, env)
    fp = cp3_fp(d, env)
    c = tt.cp3_signed_combined(d, env, fp)
    body = json.loads(Path(c).read_text())
    r = rx(d, env, "ui-record", "CP1", "--review", str(c))
    ui = cp3_state(d)["checkpoints"]["CP1"]["gates"]["ui"]
    check("[CP7-X05] the combined review is signed, not marked unconfirmed, and ui-record passes it",
          isinstance(body.get("signature"), dict) and "unconfirmed" not in body and r.returncode == 0
          and ui["status"] == "passed" and "unconfirmed" not in ui, (r.returncode, r.stderr, body, ui))


def x06_signing_failure_is_loud():
    print("CP7-X06: when ui_review.py can't sign, it exits non-zero with a plain message and writes nothing")
    env = make_env("claude")
    d = cp3_ui_project(env)
    cp3_pass_behavior(d, env)
    fp = cp3_fp(d, env)
    round1 = cp3_ui_round_dir(d, 1)
    keys = Path(env["RATCHET_HOME"]) / "review-keys"
    if keys.is_dir():
        shutil.rmtree(keys)
    keys.parent.mkdir(parents=True, exist_ok=True)
    keys.write_text("not a folder")
    out = round1 / "combined.json"
    r = run_combine(d, env, [write_role(round1 / "visual.json", "visual"), write_role(round1 / "behavior.json", "behavior")],
                    out, "CP1", ui_manifest(d, fp))
    check("[CP7-X06] an unwritable secret folder: non-zero exit, a plain message, no traceback, no combined.json",
          r.returncode != 0 and "review-signing secret" in r.stderr and "Traceback" not in r.stderr
          and not out.exists(), (r.returncode, r.stderr))
    env2 = make_env("claude")
    d2 = cp3_project(env2)  # CP1 has no UI gate
    cp3_pass_behavior(d2, env2)
    out2 = Path(d2) / ".ratchet/runs/f/reviews/CP1/round-01/combined.json"
    out2.parent.mkdir(parents=True, exist_ok=True)
    r2 = run_combine(d2, env2, [write_role(out2.parent / "visual.json", "visual")], out2, "CP1",
                     ui_manifest(d2, cp3_fp(d2, env2)))
    check("[CP7-X06] a step with no UI gate: non-zero exit, says so, no combined.json",
          r2.returncode != 0 and "UI gate" in r2.stderr and "Traceback" not in r2.stderr and not out2.exists(),
          (r2.returncode, r2.stderr))


FAKE_UI_CODEX = r"""out=""
while [ $# -gt 0 ]; do
  case "$1" in --output-last-message) out="$2"; shift 2;; *) shift;; esac
done
if [ ! -t 0 ]; then cat > /dev/null; fi
if [ -n "$out" ]; then printf '%s' '{"reviewer":"x","verdict":"PASS","differences":[]}' > "$out"; fi
"""


def x07_review_signs_role_files():
    print("CP7-X07: `ui_review.py review` signs each role's file; combine accepts them and refuses an edited one")
    env = dict(make_env("claude"), PATH=str(raw_fake_bin("codex", FAKE_UI_CODEX)))
    d = cp3_ui_project(env)
    cp3_pass_behavior(d, env)
    round1 = cp3_ui_round_dir(d, 1)
    manifest = ui_manifest(d)
    r = subprocess.run([sys.executable, str(UI_REVIEW), "review", "--manifest", str(manifest), "--out", str(round1),
                        "--scope", "all", "--visual", "codex", "--behavior", "codex"],
                       cwd=d, capture_output=True, text=True, env=env)
    roles = {k: json.loads((round1 / f"{k}.json").read_text()) for k in ("visual", "behavior")
             if (round1 / f"{k}.json").exists()}
    check("[CP7-X07] review exits 0 and both role files carry its signature",
          r.returncode == 0 and len(roles) == 2 and all(isinstance(x.get("signature"), dict) for x in roles.values()),
          (r.returncode, r.stderr, roles))
    out = round1 / "combined.json"
    rc = run_combine(d, env, [round1 / "visual.json", round1 / "behavior.json"], out, "CP1", manifest)
    check("[CP7-X07] combine accepts the script-signed role files", rc.returncode == 0, rc.stderr)
    edited = dict(roles.get("visual") or {}, provider="codeX")
    ep = Path(_tmp.mkdtemp(prefix="ratchet-sign-x07-")) / "visual.json"
    ep.write_text(json.dumps(edited))
    re_ = run_combine(d, env, [ep, round1 / "behavior.json"], round1 / "combined-edited.json", "CP1", manifest)
    check("[CP7-X07] a one-character edit to a signed role file is refused by combine",
          re_.returncode != 0 and "signature does not match" in re_.stderr
          and not (round1 / "combined-edited.json").exists(), (re_.returncode, re_.stderr))
    rr = rx(d, env, "ui-record", "CP1", "--review", str(out))
    check("[CP7-X07] ui-record passes the combined review", rr.returncode == 0, (rr.returncode, rr.stderr))


def x08_unconfirmed_on_codex():
    print("CP7-X08: on Codex a host review with no writer record is accepted and shown as not independently confirmed")
    env = make_env("codex")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    path = cp3_write_review(Path(rnd["output_dir"]) / f"{rid}.json", rnd, rid, findings=[])  # no guard call
    r = rx(d, env, "adv-record", "CP1", "--reviews", path)
    g = cp3_state(d)["checkpoints"]["CP1"]["gates"]["adversarial"]
    status = rx(d, env, "status")
    sj = js(rx(d, env, "status", "--json")) or {}
    gates = [c.get("gates", {}).get("adversarial", {}) for c in sj.get("checkpoints", [])]
    check("[CP7-X08] accepted, and the state says why it is not independently confirmed",
          r.returncode == 0 and g["status"] == "passed"
          and any("not independently confirmed" in x and "did not record who wrote it" in x
                  for x in g.get("unconfirmed") or []), (r.returncode, r.stderr, g))
    check("[CP7-X08] plain `status` says it", "not independently confirmed" in status.stdout, status.stdout)
    check("[CP7-X08] `status --json` lists it on the gate", any(x.get("unconfirmed") for x in gates), sj)
    check("[CP7-X08] viewer data marks the round's reviewer", any(rid in (x.get("unconfirmed") or [])
                                                                  for x in viewer_rounds(d)), viewer_rounds(d))


def x09_unconfirmed_under_waiver():
    print("CP7-X09: on Claude Code with the hooks waived, a host review is accepted and says the hooks were waived")
    env = make_env("claude")
    d = cp3_project(env)
    a = rx(d, env, "ask", "hooks-waive")
    say_x(d, env, GO_WITHOUT_HOOKS)
    w = rx(d, env, "hooks-waive")
    check("[CP7-X09] setup: the waiver is recorded", a.returncode == 0 and w.returncode == 0, (a.stderr, w.stderr))
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    path = cp3_write_review(Path(rnd["output_dir"]) / f"{rid}.json", rnd, rid, findings=[])
    r = rx(d, env, "adv-record", "CP1", "--reviews", path)
    g = cp3_state(d)["checkpoints"]["CP1"]["gates"]["adversarial"]
    status = rx(d, env, "status").stdout
    check("[CP7-X09] accepted, marked not independently confirmed because the hooks are waived (state and status)",
          r.returncode == 0 and any("waived" in x for x in g.get("unconfirmed") or [])
          and "not independently confirmed" in status and "waived" in status, (r.returncode, r.stderr, g, status))


def x10_claude_main_session_and_shell_writes_refused():
    print("CP7-X10: on Claude Code a main-session write, or a reviewer's shell write, is refused with the reason")
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    path = Path(rnd["output_dir"]) / f"{rid}.json"
    assert guard(d, env, "Write", {"file_path": str(path)}).returncode == 0  # no sub-agent marks yet: allowed
    cp3_write_review(path, rnd, rid, findings=[])
    r = rx(d, env, "adv-record", "CP1", "--reviews", str(path))
    msg = r.stdout + r.stderr
    check("[CP7-X10] the main session's own write is refused as unsigned, naming the main session",
          r.returncode != 0 and "unsigned" in msg and "main session" in msg, (r.returncode, msg))
    env2 = make_env("claude")
    d2 = cp3_project(env2)
    cp3_pass_behavior(d2, env2)
    rnd2 = cp3_open_round(d2, env2)
    rid2 = cp3_R0(rnd2)
    path2 = Path(rnd2["output_dir"]) / f"{rid2}.json"
    g = guard(d2, env2, "Bash", {"command": f"cat > {path2} <<'EOF'\n{{}}\nEOF"}, agent="ratchet-adversary")
    check("[CP7-X10] setup: the guard lets the reviewer's shell write through", g.returncode == 0, g.stderr)
    cp3_write_review(path2, rnd2, rid2, findings=[])
    r2 = rx(d2, env2, "adv-record", "CP1", "--reviews", str(path2))
    msg2 = r2.stdout + r2.stderr
    check("[CP7-X10] a reviewer's shell-written file is refused, telling it to use the Write tool",
          r2.returncode != 0 and "Write tool" in msg2 and "shell" in msg2, (r2.returncode, msg2))


def x11_incidental_signature_field_falls_through():
    print("CP7-X11: a confirmed host review with a stray, malformed 'signature' field is still accepted")
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    path = Path(rnd["output_dir"]) / f"{rid}.json"
    assert guard(d, env, "Write", {"file_path": str(path)}, agent="ratchet-adversary").returncode == 0
    cp3_write_review(path, rnd, rid, findings=[], signature="see schemas.md")
    r = rx(d, env, "adv-record", "CP1", "--reviews", str(path))
    check("[CP7-X11] accepted through the writer record", r.returncode == 0
          and cp3_state(d)["checkpoints"]["CP1"]["gates"]["adversarial"]["status"] == "passed", (r.returncode, r.stderr))


def x12_lone_surrogate_does_not_crash():
    print("CP7-X12: a reviewer reply with a lone surrogate escape is signed and recorded without a crash")
    reply = '{"findings":[{"id":"F1","severity":"minor","file":"src/f.js","description":"emoji cut \\ud83d here"}]}'
    body = (r"""out=""; args="$*"
while [ $# -gt 0 ]; do case "$1" in --output-last-message) out="$2"; shift 2;; *) shift;; esac; done
input=""; if [ ! -t 0 ]; then input=$(cat); fi
case "$input $args" in *"connectivity check"*) reply="RATCHET-PROBE-OK";; *) reply='__R__';; esac
if [ -n "$out" ]; then printf '%s' "$reply" > "$out"; fi
printf '%s\n' "$reply"
""").replace("__R__", reply)
    env = dict(make_env("claude"), PATH=str(raw_fake_bin("codex", body)))
    d = cp3_project(env)
    use_codex_provider(d, env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    out = Path(rnd["output_dir"]) / f"{rid}.json"
    rp = run_external(d, env, rid, out)
    check("[CP7-X12] external_review.py signs and writes it (no traceback)",
          rp.returncode == 0 and out.exists() and "Traceback" not in rp.stderr, (rp.returncode, rp.stderr))
    r = rx(d, env, "adv-record", "CP1", "--reviews", str(out))
    check("[CP7-X12] adv-record checks its signature and records the round (no traceback)",
          r.returncode in (0, 2) and "Traceback" not in r.stderr
          and (cp3_state(d)["checkpoints"]["CP1"].get("adv_round") or {}).get("recorded"), (r.returncode, r.stderr))
    import importlib.util
    spec = importlib.util.spec_from_file_location("ratchet_engine_x12", ROOT / "skills/ratchet-orchestrator/scripts/ratchet.py")
    eng = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(eng)
    try:
        signed = eng.sign_review({"secret": "ab" * 32}, {"findings": [{"description": "cut \ud83d"}], "fingerprint": "f"},
                                 "nonce", "test")
        ok = eng.review_signed({"secret": "ab" * 32}, signed, "nonce")
    except Exception as e:  # noqa: BLE001
        ok = repr(e)
    check("[CP7-X12] signing and checking a review that still holds a lone surrogate works", ok is True, ok)


def x13_failed_save_keeps_round_secret():
    print("CP7-X13: a record that fails before the state is saved keeps the round's secret, so a retry works")
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    dest = Path(d) / ".ratchet/runs/f/evidence/CP1/adversarial/round-01"
    dest.mkdir(parents=True, exist_ok=True)
    dest.chmod(0o500)
    try:
        path, r1 = record_host_review(d, env, rnd, findings=[])
    finally:
        dest.chmod(0o700)
    check("[CP7-X13] setup: adv-record fails on the unwritable evidence folder", r1.returncode != 0, r1.stderr)
    r2 = rx(d, env, "adv-record", "CP1", "--reviews", str(path))
    check("[CP7-X13] re-running adv-record on the same review succeeds", r2.returncode == 0
          and cp3_state(d)["checkpoints"]["CP1"]["gates"]["adversarial"]["status"] == "passed", (r2.returncode, r2.stderr))
    env = make_env("claude")
    d = cp3_ui_project(env)
    cp3_pass_behavior(d, env)
    c = tt.cp3_signed_combined(d, env, cp3_fp(d, env))
    dest = Path(d) / ".ratchet/runs/f/evidence/CP1/ui/round-01"
    dest.mkdir(parents=True, exist_ok=True)
    dest.chmod(0o500)
    try:
        u1 = rx(d, env, "ui-record", "CP1", "--review", str(c))
    finally:
        dest.chmod(0o700)
    u2 = rx(d, env, "ui-record", "CP1", "--review", str(c))
    check("[CP7-X13] ui-record: fails once on the evidence folder, then the same signed review passes",
          u1.returncode != 0 and u2.returncode == 0
          and cp3_state(d)["checkpoints"]["CP1"]["gates"]["ui"]["status"] == "passed", (u1.stderr, u2.stderr))


def x14_unrecorded_writer_is_logged():
    print("CP7-X14: a writer the guard could not record is logged, and the refusal says so")
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    keys = list((Path(env["RATCHET_HOME"]) / "review-keys").glob("*.json"))
    check("[CP7-X14] setup: one round record exists", len(keys) == 1, keys)
    keys[0].write_text("{ damaged")
    path = Path(rnd["output_dir"]) / f"{rid}.json"
    g = guard(d, env, "Write", {"file_path": str(path)}, agent="ratchet-adversary")
    log = Path(d) / ".ratchet/hook-errors.log"
    check("[CP7-X14] the guard still lets the write through and logs that it could not record the writer",
          g.returncode == 0 and log.exists() and "ReviewWriterNotRecorded" in log.read_text(), (g.stderr,))
    cp3_write_review(path, rnd, rid, findings=[])
    r = rx(d, env, "adv-record", "CP1", "--reviews", str(path))
    check("[CP7-X14] the refusal says the guard could not record who wrote the file",
          r.returncode != 0 and "could not record who wrote" in (r.stdout + r.stderr), (r.returncode, r.stderr))


FAKE_MARK = r"""here=$(dirname "$0"); out=""; args="$*"
while [ $# -gt 0 ]; do case "$1" in --output-last-message) out="$2"; shift 2;; *) shift;; esac; done
input=""; if [ ! -t 0 ]; then input=$(cat); fi
case "$input $args" in *"connectivity check"*) printf 'RATCHET-PROBE-OK\n'; exit 0;; esac
: > "$here/ran"
if [ -n "$out" ]; then printf '%s' '{"findings":[]}' > "$out"; fi
printf '%s\n' '{"findings":[]}'
"""


def x15_external_review_checks_key_first():
    print("CP7-X15: external_review.py checks the round's secret before it runs the reviewer")
    b = raw_fake_bin("codex", FAKE_MARK)
    env = dict(make_env("claude"), PATH=str(b))
    d = cp3_project(env)
    use_codex_provider(d, env)
    cp3_pass_behavior(d, env)
    rnd = cp3_open_round(d, env)
    rid = cp3_R0(rnd)
    for k in (Path(env["RATCHET_HOME"]) / "review-keys").glob("*"):
        k.unlink()
    if (b / "ran").exists():
        (b / "ran").unlink()
    rp = run_external(d, env, rid, Path(rnd["output_dir"]) / f"{rid}.json")
    check("[CP7-X15] it exits with the missing-secret message and the reviewer CLI never ran",
          rp.returncode != 0 and "secret is missing" in rp.stderr and not (b / "ran").exists(),
          (rp.returncode, rp.stderr, (b / "ran").exists()))


def x16_ui_unconfirmed_on_codex():
    print("CP7-X16: on Codex, host UI reviewers' files count but are shown as not independently confirmed")
    env = make_env("codex")
    d = cp3_ui_project(env)
    cp3_pass_behavior(d, env)
    fp = cp3_fp(d, env)
    round1 = cp3_ui_round_dir(d, 1)
    manifest = ui_manifest(d, fp)
    assert run_stage(d, env, manifest).returncode == 0
    out = round1 / "combined.json"
    rc = run_combine(d, env, [write_role(round1 / "visual.json", "visual"),
                              write_role(round1 / "behavior.json", "behavior")], out, "CP1", manifest)
    body = json.loads(out.read_text()) if out.exists() else {}
    check("[CP7-X16] combine signs it and marks both roles not independently confirmed",
          rc.returncode == 0 and len(body.get("unconfirmed") or []) == 2, (rc.returncode, rc.stderr, body))
    r = rx(d, env, "ui-record", "CP1", "--review", str(out))
    ui = cp3_state(d)["checkpoints"]["CP1"]["gates"]["ui"]
    status = rx(d, env, "status").stdout
    check("[CP7-X16] ui-record passes it, and state and status say it is not independently confirmed",
          r.returncode == 0 and ui.get("unconfirmed") and "UI review visual: not independently confirmed" in status,
          (r.returncode, r.stderr, ui, status))


def x17_round_secret_open_until_saved():
    print("CP7-X17: `reviewers` keeps a replaced round's record until the new round is saved (same process order)")
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    cp3_open_round(d, env)
    (d / "src/f.js").write_text("export const f = () => 1; // tweak\n")
    cp3_open_round(d, env)
    keys = list((Path(env["RATCHET_HOME"]) / "review-keys").glob("*.json"))
    check("[CP7-X17] after round 2 opens, only round 2's record is left", len(keys) == 1, keys)


TESTS.extend([
    ("CP7-X01", x01_abandoned_round_record_not_reused),
    ("CP7-X02", x02_signed_review_refused_in_other_round),
    ("CP7-X03", x03_ui_combined_other_round_and_edit),
    ("CP7-X04", x04_combine_refuses_unproven_inputs),
    ("CP7-X05", x05_combine_accepts_reviewer_written_inputs),
    ("CP7-X06", x06_signing_failure_is_loud),
    ("CP7-X07", x07_review_signs_role_files),
    ("CP7-X08", x08_unconfirmed_on_codex),
    ("CP7-X09", x09_unconfirmed_under_waiver),
    ("CP7-X10", x10_claude_main_session_and_shell_writes_refused),
    ("CP7-X11", x11_incidental_signature_field_falls_through),
    ("CP7-X12", x12_lone_surrogate_does_not_crash),
    ("CP7-X13", x13_failed_save_keeps_round_secret),
    ("CP7-X14", x14_unrecorded_writer_is_logged),
    ("CP7-X15", x15_external_review_checks_key_first),
    ("CP7-X16", x16_ui_unconfirmed_on_codex),
    ("CP7-X17", x17_round_secret_open_until_saved),
])


# ---- CP7 adversarial round 2: added cases (CP7-X18 .. CP7-X21), registered in TESTS below ------------------------

NO_REVIEWER_CLI = "exit 1\n"  # a PATH with no other-provider reviewer CLI: every role falls back to host sub-agents


def run_review(d, env, manifest, out, *extra):
    return subprocess.run([sys.executable, str(UI_REVIEW), "review", "--manifest", str(manifest), "--out", str(out),
                           "--scope", "all", "--visual", "codex", "--behavior", "codex", *extra],
                          cwd=d, capture_output=True, text=True, env=env)


def reviewers_write_roles(d, env, folder):
    """The host's ratchet-visual-reviewer and ratchet-behavior-reviewer sub-agents each write their file (guard seen)."""
    paths = []
    for role in ("visual", "behavior"):
        p = Path(folder) / f"{role}.json"
        g = guard(d, env, "Write", {"file_path": str(p)}, agent=f"ratchet-{role}-reviewer")
        assert g.returncode == 0, g.stderr + g.stdout
        paths.append(write_role(p, role))
    return paths


def x18_handtyped_manifest_refused():
    print("CP7-X18: combine refuses a capture manifest ui_review.py did not sign, even one under reviews/CP1/")
    env = make_env("claude")
    d = cp3_ui_project(env)
    cp3_pass_behavior(d, env)
    fp = cp3_fp(d, env)
    round1 = cp3_ui_round_dir(d, 1)
    manifest = ui_manifest(d)
    assert run_stage(d, env, manifest).returncode == 0
    signed = json.loads(manifest.read_text())
    check("[CP7-X18] setup: `stage` stamps the manifest with the code version and signs it",
          signed.get("fingerprint") == fp and isinstance(signed.get("signature"), dict), signed)
    v, b = reviewers_write_roles(d, env, round1)
    out = round1 / "combined.json"
    hand = round1 / "m.json"
    g = guard(d, env, "Write", {"file_path": str(hand)})  # the main session may write it: it is not a guarded name
    hand.write_text(json.dumps({"checkpoint": "CP1", "fingerprint": fp, "review_scope": "all", "spec_sha256": "s1",
                                "states": []}))
    ra = run_combine(d, env, [v, b], out, "CP1", hand)
    check("[CP7-X18] a hand-typed manifest under reviews/CP1/ is refused, saying ui_review.py did not write it, and "
          "nothing is written", g.returncode == 0 and ra.returncode != 0 and not out.exists()
          and "manifest" in ra.stderr and "not written by" in ra.stderr and "Traceback" not in ra.stderr,
          (g.returncode, ra.returncode, ra.stderr))
    (d / "src/f.js").write_text("export const f = () => 1; // changed after the reviewers looked\n")
    copy = dict(signed, fingerprint=cp3_fp(d, env))
    edited = round1 / "m2.json"
    edited.write_text(json.dumps(copy))
    rb = run_combine(d, env, [v, b], out, "CP1", edited)
    check("[CP7-X18] a copy of the signed manifest with its fingerprint changed to the new code is refused",
          rb.returncode != 0 and not out.exists() and "not written by" in rb.stderr, (rb.returncode, rb.stderr))
    (d / "src/f.js").write_text("export const f = () => 1;\n")
    rc = run_combine(d, env, [v, b], out, "CP1", manifest)
    check("[CP7-X18] the manifest `stage` signed is accepted (control)", rc.returncode == 0 and out.exists(), rc.stderr)


def x19_rereview_invalidates_old_role_files():
    print("CP7-X19: after `review` stamps the manifest again, role files written for the old manifest are refused")
    env = dict(make_env("claude"), PATH=str(raw_fake_bin("not-a-reviewer", NO_REVIEWER_CLI)))
    d = cp3_ui_project(env)
    cp3_pass_behavior(d, env)
    round1 = cp3_ui_round_dir(d, 1)
    manifest = ui_manifest(d)
    r1 = run_review(d, env, manifest, round1)
    check("[CP7-X19] setup: with no other provider, `review` hands both roles to host sub-agents (exit 4)",
          r1.returncode == 4, (r1.returncode, r1.stderr))
    v, b = reviewers_write_roles(d, env, round1)
    first = round1 / "combined-first.json"
    rc = run_combine(d, env, [v, b], first, "CP1", manifest)
    check("[CP7-X19] setup: the role files count for the manifest they were written against", rc.returncode == 0,
          rc.stderr)
    (d / "src/f.js").write_text("export const f = () => 1; // changed after the reviewers looked\n")
    r2 = run_review(d, env, manifest, round1)
    assert r2.returncode == 4, (r2.returncode, r2.stderr)
    out = round1 / "combined.json"
    rs = run_combine(d, env, [v, b], out, "CP1", manifest)
    check("[CP7-X19] after `review` runs again, combining the old role files is refused with a plain message",
          rs.returncode != 0 and not out.exists() and "earlier capture manifest" in rs.stderr
          and "Traceback" not in rs.stderr, (rs.returncode, rs.stderr))
    old_manifest = round1 / "old-manifest.json"
    old_manifest.write_text(json.dumps(dict(json.loads(manifest.read_text()), stamp="0" * 32)))
    ro = run_combine(d, env, [v, b], out, "CP1", old_manifest)
    check("[CP7-X19] an older or re-stamped copy of the manifest does not bring them back",
          ro.returncode != 0 and not out.exists(), (ro.returncode, ro.stderr))
    # the same for role files `review` itself wrote and signed
    env2 = dict(make_env("claude"), PATH=str(raw_fake_bin("codex", FAKE_UI_CODEX)))
    d2 = cp3_ui_project(env2)
    cp3_pass_behavior(d2, env2)
    r21 = cp3_ui_round_dir(d2, 1)
    m2 = ui_manifest(d2)
    assert run_review(d2, env2, m2, r21).returncode == 0
    keep = Path(_tmp.mkdtemp(prefix="ratchet-sign-x19-"))
    for role in ("visual", "behavior"):
        shutil.copy(r21 / f"{role}.json", keep / f"{role}.json")
    assert run_review(d2, env2, m2, r21).returncode == 0
    rk = run_combine(d2, env2, [keep / "visual.json", keep / "behavior.json"], r21 / "combined-old.json", "CP1", m2)
    check("[CP7-X19] script-signed role files from the earlier `review` are refused too",
          rk.returncode != 0 and not (r21 / "combined-old.json").exists() and "earlier capture manifest" in rk.stderr,
          (rk.returncode, rk.stderr))


def x20_standalone_review_outside_a_run():
    print("CP7-X20: outside a Ratchet run, ui_review.py still compares, and marks the result unsigned")
    b = raw_fake_bin("codex", FAKE_UI_CODEX)
    env = dict(make_env("claude"), PATH=str(b))
    d = Path(_tmp.mkdtemp(prefix="ratchet-sign-x20-"))
    git(d, "init", "-q")
    m = d / "shots" / "manifest.json"
    m.parent.mkdir()
    m.write_text(json.dumps({"viewport": {"width": 100, "height": 100}, "states": []}))  # no checkpoint either
    out = d / "out"
    r = run_review(d, env, m, out)
    comb = json.loads((out / "combined.json").read_text()) if (out / "combined.json").exists() else {}
    check("[CP7-X20] `review` outside a run exits 0 and writes a combined result marked unsigned, with no signature",
          r.returncode == 0 and comb.get("verdict") == "PASS" and "unsigned" in str(comb.get("unsigned"))
          and "ui-record" in str(comb.get("unsigned")) and "signature" not in comb and "UNSIGNED" in r.stdout,
          (r.returncode, r.stderr, comb))
    check("[CP7-X20] it creates no Ratchet folder in the project", not (d / ".ratchet").exists(),
          sorted(p.name for p in d.iterdir()))
    env_host = dict(make_env("claude"), PATH=str(raw_fake_bin("not-a-reviewer", NO_REVIEWER_CLI)))
    out2 = d / "out2"
    r2 = run_review(d, env_host, m, out2)
    v, bh = write_role(out2 / "visual.json", "visual"), write_role(out2 / "behavior.json", "behavior")
    c = subprocess.run([sys.executable, str(UI_REVIEW), "combine", "--inputs", str(v), str(bh), "--out",
                        str(out2 / "combined.json"), "--manifest", str(m)], cwd=d, capture_output=True, text=True,
                       env=env_host)
    comb2 = json.loads((out2 / "combined.json").read_text()) if (out2 / "combined.json").exists() else {}
    check("[CP7-X20] with host fallback (exit 4), `combine` outside a run merges the files, unsigned",
          r2.returncode == 4 and c.returncode == 0 and comb2.get("unsigned") and "signature" not in comb2
          and comb2.get("reviewers") == ["visual", "behavior"], (r2.returncode, r2.stderr, c.returncode, c.stderr))


SAVE_FAILS_ON_ROUND_2 = (
    "import importlib.util, sys\n"
    "spec = importlib.util.spec_from_file_location('ratchet_engine', sys.argv[1])\n"
    "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
    "real = m.Ctx.save_state\n"
    "def save_state(self, st):\n"
    "    rnd = ((st.get('checkpoints') or {}).get('CP1') or {}).get('adv_round') or {}\n"
    "    if rnd.get('round') == 2:\n"
    "        raise OSError('simulated failed save of round 2')\n"
    "    return real(self, st)\n"
    "m.Ctx.save_state = save_state\n"
    "m.main(['reviewers', 'CP1'])\n"
)


def x21_reviewers_failed_save_keeps_old_round():
    print("CP7-X21: when `reviewers` can't save the new round, the replaced round's record is still there and works")
    env = make_env("claude")
    d = cp3_project(env)
    cp3_pass_behavior(d, env)
    rnd1 = cp3_open_round(d, env)
    keys = Path(env["RATCHET_HOME"]) / "review-keys"
    before = sorted(p.name for p in keys.glob("*.json"))
    original = (d / "src/f.js").read_text()
    (d / "src/f.js").write_text("export const f = () => 1; // tweak\n")
    r = subprocess.run([sys.executable, "-c", SAVE_FAILS_ON_ROUND_2, str(tt.ENGINE)], cwd=d, capture_output=True,
                       text=True, env=env)
    check("[CP7-X21] setup: `reviewers` fails while saving the new round 2",
          r.returncode != 0 and "simulated failed save of round 2" in r.stderr, (r.returncode, r.stderr[-400:]))
    after = sorted(p.name for p in keys.glob("*.json"))
    check("[CP7-X21] round 1's record is still there (it is dropped only after the new round is saved)",
          len(before) == 1 and after == before, (before, after))
    (d / "src/f.js").write_text(original)
    _path, rr = record_host_review(d, env, rnd1, findings=[])
    check("[CP7-X21] a round-1 review the guard saw the reviewer write still records",
          rr.returncode == 0 and cp3_state(d)["checkpoints"]["CP1"]["gates"]["adversarial"]["status"] == "passed",
          (rr.returncode, rr.stderr))


TESTS.extend([
    ("CP7-X18", x18_handtyped_manifest_refused),
    ("CP7-X19", x19_rereview_invalidates_old_role_files),
    ("CP7-X20", x20_standalone_review_outside_a_run),
    ("CP7-X21", x21_reviewers_failed_save_keeps_old_round),
])


if __name__ == "__main__":
    main()
