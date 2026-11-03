#!/usr/bin/env python3
"""
OpenCode support, step 4: the installed Ratchet plugin's `tool.execute.before` handler stops an edit or command the
guard refuses (a hand-edit of a protected run file), before it runs and with Ratchet's reason, and when anything about
Ratchet is broken (engine missing, crash, garbage, hang, no python3, a bug in the plugin) the tool call goes through.

    python3 tests/test_hook_opencode_guard.py      # needs node on PATH; builds a throwaway git project

How a case runs. A throwaway git project (temp dir, never this repo's .ratchet/) gets a plan-submitted, approved run
`hk`, and the plugin is installed there by the public command (`ratchet.py install-hooks --host opencode`, project
scope). A small node driver, written to a separate temp folder, imports the installed plugin file, calls its exported
factory with a fake `{client, directory, worktree}` and calls the `tool.execute.before` hook the way OpenCode does:
`hook({tool, sessionID, callID}, {args})`. For every call the driver records whether the hook resolved or threw, whether
the thrown value is an `instanceof Error`, its message, whether `output.args` was left as given, and how long it took;
it also records any unhandled rejection or uncaught exception of the node process. The real engine (`python3
ratchet.py hook guard --host opencode`) answers inside the project. Fail-open cases copy the installed plugin into the
work folder with its engine path (a JSON string literal) pointed at a stub engine that is missing, crashes, prints
garbage, hangs or records what it was sent. The driver runs under plain node (the plugin must use node:child_process, not
a Bun-only API) and with its working folder OUTSIDE the project, so a plugin that judges relative paths against
process.cwd() instead of the `directory` OpenCode gave it is caught.

Every child process gets an ambient-proof environment (every CODEX_*, CLAUDE*, GEMINI_*, COPILOT_*, GITHUB_COPILOT_*,
OPENCODE_*, XDG_* and RATCHET_* variable removed; a throwaway HOME, RATCHET_HOME and XDG_CONFIG_HOME), so nothing here
touches the real ~/.config/opencode or this repo's .ratchet/. This file never loads ratchet.py as a module and never
reads another file's source text (it runs processes; the one README table fact, HOST_ENFORCEMENT, is read by running the
engine file in a child process), so it needs no test-inventory entry.

A missing `node` binary FAILS the script loudly at its start (it never skips or passes the plugin cases): see
[CP4-T21], which runs this very script with a PATH that has no node.

Teeth. Every verdict checker is shown to reject the wrong behavior: tiny mutant plugins (always blocks, never blocks,
latches a block, fails closed when the engine is missing, throws a canned message instead of the engine's) are run
through the same driver and the same checkers, and each is refused by the case it would break.

Known facts about the plan this file pins, and where it differs from the plan text:
  * [CP4-T05] The plan names a review file under reviews/ as a protected record. The engine (decide_guard) allows
    writes to reviews/ on purpose (the owner check only applies on hosts that name the sub-agent, and OpenCode does not),
    so the Claude guard exits 0 for it. The case therefore uses other protected records for the "same reason as Claude"
    comparison (human_log.jsonl, config.json, active.json, an evidence file) and pins the review file as PARITY: the
    OpenCode hook goes through exactly when the Claude guard does.
  * [CP4-T14] The plugin has no timeout override, so this case does not wait for the real 30 second limit: it runs a
    copy of the installed plugin whose `GUARD_TIMEOUT_MS = 30000` is retargeted to 1500 ms (the substitution must match
    exactly once and leave no 30000 behind), so the hang case takes about 2 s. A separate check reads the installed,
    unpatched plugin text and pins the shipped limit at exactly `GUARD_TIMEOUT_MS = 30000`.
  * [CP4-T17] The "nothing was run through a shell" marker file lives in this run's temp folder, not /tmp.
  * [CP4-T09] `glob` (a real OpenCode tool) and `list` (not an OpenCode v1.19.5 tool: the engine accepts it only as a
    harmless alias) on the .ratchet folder are pinned as parity rows (Claude's LS and Glob on it are allowed): an agent
    looking inside .ratchet must not be refused as if it were writing there. The same goes for `patch`: not an OpenCode
    tool, accepted as an alias of `apply_patch`.

Each case id appears in its check names as [CP4-Txx]. When RATCHET_TEST_REPORT is set, one JSON entry per planned case
({"cases": [{"id", "status", "message"}]}) is written there.
"""
from _docs import README_DOCS  # README.md plus the docs/ pages, read as one text
import json
import os
import re
import shutil
import subprocess
import sys
import time
import _tmp
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
README = README_DOCS
THIS_FILE = Path(__file__).resolve()
NEW_TEST_LINE = "python3 tests/test_hook_opencode_guard.py"
PLANNED = [f"CP4-T{n:02d}" for n in range(1, 22)]
PASS, FAIL = 0, 0
RESULTS = {}   # case id -> [(ok, name, detail)]
CRASHED = {}   # case id -> what crashed
NODE = shutil.which("node")
HOOK_TIME_LIMIT_S = 30   # the other hosts' limit, which the plan asks this plugin to share
HANG_TEST_LIMIT_MS = 1500   # what CP4-T14 retargets its own plugin copy's limit to, so the hang case is quick
_STRIP_PREFIXES = ("CODEX_", "OPENCODE_", "XDG_", "RATCHET_", "GEMINI_", "COPILOT_", "GITHUB_COPILOT_", "CLAUDE")


def check(name, cond, detail=""):
    global PASS, FAIL
    m = re.match(r"\[(CP4-[A-Z]\d+)\]", name)
    ok = bool(cond)
    if m:
        RESULTS.setdefault(m.group(1), []).append((ok, name, str(detail)[:300]))
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {str(detail)[:500]}")


def need_node():
    """Without node the plugin cases cannot run at all: say so plainly and stop with a non-zero exit."""
    if NODE:
        return
    print("ERROR: node is required to run this test (it loads the OpenCode plugin under node); none was found on "
          "PATH. Install node and run it again. No plugin case was run, so none counts as a pass.",
          file=sys.stderr)
    sys.exit(2)


# ----------------------------------------------------------------------------------------- the throwaway project

def git(path, *args):
    subprocess.run(["git", *args], cwd=str(path), check=True, capture_output=True)


def scrubbed_env():
    return {k: v for k, v in os.environ.items() if not k.startswith(_STRIP_PREFIXES)}


class World:
    """A throwaway git project, a throwaway HOME / RATCHET_HOME, and a work folder for the driver, plugin copies and
    stub engines (outside the project)."""

    def __init__(self):
        self.proj = Path(_tmp.mkdtemp(prefix="ratchet-ocguard-p-")).resolve()
        self.home = Path(_tmp.mkdtemp(prefix="ratchet-ocguard-h-")).resolve()
        self.rh = Path(_tmp.mkdtemp(prefix="ratchet-ocguard-r-")).resolve()
        self.work = Path(_tmp.mkdtemp(prefix="ratchet-ocguard-w-")).resolve()
        self.plugin = self.proj / ".opencode" / "plugins" / "ratchet.js"
        self.driver = self.work / "driver.mjs"
        self.counter = 0

    def env(self, **extra):
        env = scrubbed_env()
        env.update(HOME=str(self.home), RATCHET_HOME=str(self.rh), XDG_CONFIG_HOME=str(self.home / ".config"),
                   PYTHONDONTWRITEBYTECODE="1")
        env.update(extra)
        return env

    def sh(self, *args, stdin=None, timeout=180):
        return subprocess.run([sys.executable, str(ENGINE), *args], cwd=str(self.proj), input=stdin,
                              capture_output=True, text=True, env=self.env(), timeout=timeout)

    def next(self, stem):
        self.counter += 1
        return f"{stem}-{self.counter}"


WORLD = None


def world():
    """The one shared throwaway project (built on first use): an approved run `hk` and the plugin installed."""
    global WORLD
    if WORLD is not None:
        return WORLD
    w = World()
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(w.proj, *c)
    (w.proj / "a.js").write_text("x")
    (w.proj / "src").mkdir()
    (w.proj / "src" / "app.js").write_text("export const x = 1;\n")
    git(w.proj, "add", "-A")
    git(w.proj, "commit", "-qm", "init")
    r = w.sh("init", "opencode guard", "--slug", "hk")
    assert r.returncode == 0, r.stdout + r.stderr
    run = w.proj / ".ratchet" / "runs" / "hk"
    (run / "app-scout.json").write_text("{}")
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": [{
        "id": "CP1", "title": "A", "summary": "s", "done_when": ["a"],
        "gates": {"behavior": True, "ui": False, "adversarial": True},
        "tests": {"files": ["t.test.js"], "command": "true"}}]}))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    for args in (("plan-submit",), ("ask", "approve-plan")):
        r = w.sh(*args)
        assert r.returncode == 0, r.stdout + r.stderr
    import datetime as dt
    tp = w.proj.parent / f"{w.proj.name}-transcript.jsonl"   # what the agent CLI writes when the user types
    tp.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "approved"},
                              "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}) + "\n")
    r = w.sh("hook", "prompt", "--host", "claude",
             stdin=json.dumps({"cwd": str(w.proj), "prompt": "approved", "transcript_path": str(tp)}))
    assert r.returncode == 0, r.stdout + r.stderr
    r = w.sh("approve-plan")
    assert r.returncode == 0, r.stdout + r.stderr
    (run / "evidence" / "CP1").mkdir(parents=True, exist_ok=True)
    (run / "evidence" / "CP1" / "behavior-01.log").write_text("a\n")
    (run / "reviews" / "CP1" / "round-1").mkdir(parents=True, exist_ok=True)
    (run / "reviews" / "CP1" / "round-1" / "anything.json").write_text("{}")
    r = w.sh("install-hooks", "--host", "opencode")
    assert r.returncode == 0 and w.plugin.is_file(), r.stdout + r.stderr
    w.driver.write_text(DRIVER_JS, encoding="utf-8")
    WORLD = w
    return w


# ------------------------------------------------------------------------------------------------- node driver

DRIVER_JS = r"""
import { pathToFileURL } from 'node:url';
import fs from 'node:fs';

const scen = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const problems = [];
process.on('unhandledRejection', (e) => { problems.push('unhandledRejection: ' + ((e && e.message) || String(e))); });
process.on('uncaughtException', (e) => { problems.push('uncaughtException: ' + ((e && e.message) || String(e))); });

// A spec is plain JSON, or {"$js": "<expression>"} for values JSON cannot carry (undefined, circular, huge).
function build(spec, dir) {
  if (spec && typeof spec === 'object' && !Array.isArray(spec) && Object.keys(spec).length === 1
      && typeof spec.$js === 'string') {
    return new Function('dir', 'return (' + spec.$js + ');')(dir);
  }
  return spec;
}
function snap(v) { try { return JSON.stringify(v); } catch (e) { return '<unserializable>'; } }
function fakeClient() {
  const f = () => async () => ({ data: {} });
  return { app: { log: f() }, session: { prompt: f(), promptAsync: f(), get: f(), list: f(), abort: f() }, tui: { showToast: f() } };
}
async function one(handler, hooks, c, dir) {
  const r = {};
  let input, output;
  try { input = build(c.input, dir); output = build(c.output, dir); }
  catch (e) { return { status: 'driver-error', message: String(e) }; }
  const argsOf = (o) => (o !== null && typeof o === 'object') ? o.args : undefined;
  const before = snap(argsOf(output));
  const t0 = Date.now();
  try {
    const v = await handler.call(hooks, input, output);
    r.status = 'resolved';
    r.valueIsNullish = (v === undefined || v === null);
  } catch (e) {
    r.status = 'threw';
    r.isError = e instanceof Error;
    r.thrownType = typeof e;
    r.message = (e !== null && (typeof e === 'object' || typeof e === 'string') && e.message !== undefined)
      ? e.message : String(e);
  }
  r.argsUnchanged = snap(argsOf(output)) === before;
  r.ms = Date.now() - t0;
  return r;
}

const out = [];
for (const run of scen.runs) {
  const rr = { label: run.label, factory: 'unknown', hookNames: [], results: [] };
  let hooks;
  try {
    const mod = await import(pathToFileURL(run.plugin).href);
    if (typeof mod.RatchetPlugin !== 'function') throw new Error('no exported RatchetPlugin function');
    const ctx = ('ctx' in run) ? build(run.ctx, scen.dir)
      : { client: fakeClient(), directory: scen.dir, worktree: scen.dir, project: { id: 'p' }, serverUrl: 'http://localhost:4096' };
    hooks = await mod.RatchetPlugin(ctx);
    rr.factory = 'ok';
    rr.hookNames = (hooks && typeof hooks === 'object') ? Object.keys(hooks) : [];
  } catch (e) {
    rr.factory = 'threw: ' + ((e && e.message) || String(e));
  }
  const handler = hooks && typeof hooks === 'object' ? hooks['tool.execute.before'] : undefined;
  if (typeof handler !== 'function') {
    rr.results = run.calls.map(() => ({ status: 'nohandler' }));
  } else if (run.mode === 'concurrent') {
    rr.results = await Promise.all(run.calls.map((c) => one(handler, hooks, c, scen.dir)));
  } else {
    for (const c of run.calls) rr.results.push(await one(handler, hooks, c, scen.dir));
  }
  out.push(rr);
}
// let a stray rejection or a late crash surface: the listeners above record into `problems`, and these bounded ticks
// (event-loop turns, not a fixed sleep) give pending microtasks, rejections, I/O callbacks and short timers their turn
for (let i = 0; i < 25; i++) {
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setTimeout(r, 1));
}
fs.writeFileSync(scen.out, JSON.stringify({ runs: out, problems }));
process.exit(0);
"""


class Out:
    """What one driver run reported."""

    def __init__(self, proc, out_path):
        self.rc, self.stdout, self.stderr = proc.returncode, proc.stdout or "", proc.stderr or ""
        self.data = None
        try:
            self.data = json.loads(Path(out_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        self.problems = (self.data or {}).get("problems") if self.data else [f"driver left no result (exit {self.rc}): "
                                                                             f"{self.stderr[-300:]}"]

    def run(self, i=0):
        runs = (self.data or {}).get("runs") or []
        return runs[i] if i < len(runs) else {"factory": "missing", "hookNames": [], "results": []}

    def result(self, i=0, j=0):
        rs = self.run(i).get("results") or []
        return rs[j] if j < len(rs) else {"status": "missing"}

    def factory_ok(self, i=0):
        return self.run(i).get("factory") == "ok"

    def has_handler(self, i=0):
        return "tool.execute.before" in (self.run(i).get("hookNames") or [])

    def clean(self):
        """The node process exited normally and reported no unhandled rejection or uncaught exception."""
        return self.rc == 0 and self.data is not None and self.problems == []


def drive(w, runs, env=None, timeout=240):
    """Load each run's plugin under node and call its tool.execute.before hook for each of the run's calls."""
    need_node()
    n = w.next("scenario")
    scen, out_path = w.work / f"{n}.json", w.work / f"{n}.out.json"
    scen.write_text(json.dumps({"dir": str(w.proj), "out": str(out_path), "runs": runs}), encoding="utf-8")
    p = subprocess.run([NODE, str(w.driver), str(scen)], cwd=str(w.work), env=env or w.env(), capture_output=True,
                       text=True, timeout=timeout)
    return Out(p, out_path)


def run_of(plugin, calls, label="run", mode="seq", **kw):
    return {"label": label, "plugin": str(plugin), "calls": calls, "mode": mode, **kw}


def oc_call(tool, args, sid="ses_1", cid="call_1"):
    """One call exactly as OpenCode makes it: hook({tool, sessionID, callID}, {args})."""
    return {"input": {"tool": tool, "sessionID": sid, "callID": cid}, "output": {"args": args}}


# ---------------------------------------------------------------------------- verdict checkers (teeth further down)

def went_through(r):
    """The hook resolved with nothing (a hook returns void): the tool call is allowed."""
    return isinstance(r, dict) and r.get("status") == "resolved" and r.get("valueIsNullish") is True


def refusal(r, msg=None):
    """The hook rejected with an Error (a throw, not a flag or a string) whose message is never blank; when `msg` is
    given, exactly that message."""
    return (isinstance(r, dict) and r.get("status") == "threw" and r.get("isError") is True
            and isinstance(r.get("message"), str) and r["message"].strip() != ""
            and (msg is None or r["message"] == msg))


def ratchet_refusal(r, target=None):
    """A refusal that is Ratchet's: it names RATCHET (or the protected file), and the args were left as given."""
    if not refusal(r):
        return False
    m = r["message"]
    return ("RATCHET" in m or (target is not None and target in m)) and r.get("argsUnchanged") is True


def pid_running(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(st) and not st.startswith("Z")


def short(r):
    return json.dumps(r)[:260] if isinstance(r, dict) else repr(r)


# ------------------------------------------------------------------------------------------ engine and stub helpers

def engine_guard(w, host, payload, raw=None):
    """`ratchet.py hook guard --host <host>` in the project: (exit code, stdout, stderr)."""
    r = w.sh("hook", "guard", "--host", host, stdin=raw if raw is not None else json.dumps(payload))
    return r.returncode, r.stdout, r.stderr


def claude_reason(w, tool, tool_input):
    """(blocked, reason) the engine gives the Claude host for one tool call: exit 2 with the reason on stderr, or 0."""
    rc, out_, err = engine_guard(w, "claude", {"tool_name": tool, "tool_input": tool_input, "cwd": str(w.proj)})
    return rc, err.rstrip("\n")


_STR_RE = re.compile(r'"(?:[^"\\\n]|\\.)*"')


def retargeted_plugin(w, stem, engine_path):
    """A copy of the installed plugin, in the work folder, whose engine path (a JSON string literal) is `engine_path`.
    Returns (path to the copy, how many literals were replaced)."""
    text = w.plugin.read_text(encoding="utf-8")
    hits = [0]

    def sub(m):
        try:
            if json.loads(m.group(0)) == str(ENGINE):
                hits[0] += 1
                return json.dumps(str(engine_path))
        except ValueError:
            pass
        return m.group(0)
    new = _STR_RE.sub(sub, text)
    d = w.work / "plugins"
    d.mkdir(exist_ok=True)
    p = d / f"{w.next(stem)}.js"
    p.write_text(new, encoding="utf-8")
    return p, hits[0]


def with_short_guard_timeout(plugin_path, ms):
    """Retarget `GUARD_TIMEOUT_MS = 30000` in the plugin copy at `plugin_path` (a copy in this run's work folder, never
    the installed file) to `ms`. Returns (how many times the 30 s constant was found, whether any `= 30000` is left,
    whether the new value is present) so the caller can prove the substitution took effect."""
    text = Path(plugin_path).read_text(encoding="utf-8")
    old, new = "const GUARD_TIMEOUT_MS = 30000;", f"const GUARD_TIMEOUT_MS = {int(ms)};"
    found = text.count(old)
    patched = text.replace(old, new)
    Path(plugin_path).write_text(patched, encoding="utf-8")
    return found, "GUARD_TIMEOUT_MS = 30000" in patched, new in patched


def wait_until_stopped(pid, cap_s=5.0, step_s=0.02):
    """True once `pid` is no longer running, polling every `step_s` for at most `cap_s` seconds."""
    end = time.time() + cap_s
    while True:
        if not pid_running(pid):
            return True
        if time.time() >= end:
            return False
        time.sleep(step_s)


def stub_engine(w, stem, body, dirname=None):
    """A stub engine script (python) in the work folder. `body` runs after a prelude that gives it note(**kw), which
    appends a JSON line to the stub's log file. Returns (script path, log path)."""
    d = w.work / "stubs" / (dirname or w.next(stem))
    d.mkdir(parents=True, exist_ok=True)
    script, log = d / "engine.py", w.work / "stubs" / f"{w.next(stem)}.log"
    prelude = ("import json, os, sys, time\nLOG = %r\n\ndef note(**kw):\n    with open(LOG, 'a') as f:\n"
               "        f.write(json.dumps(kw) + '\\n')\n\n") % str(log)
    script.write_text(prelude + body + "\n", encoding="utf-8")
    return script, log


def log_lines(log):
    try:
        return [json.loads(x) for x in Path(log).read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError):
        return []


def stub_printing(w, stem, text, code=0, err=""):
    body = f"note(argv=sys.argv)\nsys.stdin.read()\nsys.stdout.write({text!r})\nsys.stderr.write({err!r})\nsys.exit({code})"
    return stub_engine(w, stem, body)


def P(w, rel):
    return str(w.proj / rel)


def protected_write(w, **kw):
    return oc_call("write", {"filePath": P(w, ".ratchet/runs/hk/state.json"), "content": "{}"}, **kw)


PATCH_BEGIN, PATCH_END = "*** Begin Patch\n", "\n*** End Patch"


def patch_text(*lines):
    return PATCH_BEGIN + "\n".join(lines) + PATCH_END


# ------------------------------------------------------------------------------------------------------ the cases

def case_t01():
    print("[CP4-T01] a write aimed at a protected run file is stopped before it runs")
    w = world()
    state = P(w, ".ratchet/runs/hk/state.json")
    o = drive(w, [run_of(w.plugin, [oc_call("write", {"filePath": state, "content": "{}"})])])
    r = o.result()
    check("[CP4-T01] setup: the installed plugin loads under node and exposes tool.execute.before",
          o.factory_ok() and o.has_handler() and o.clean(), (o.run(), o.problems))
    check("[CP4-T01] the handler REJECTS (is stopped, not resolved) with an Error carrying a non-blank reason that "
          "mentions RATCHET or the file, and args are left unchanged", ratchet_refusal(r, "state.json"), short(r))
    rc, reason = claude_reason(w, "Write", {"file_path": state, "content": "{}"})
    check("[CP4-T01] the reason is the engine's own reason for that file (same as the Claude host's)",
          rc == 2 and reason != "" and refusal(r, reason), (rc, reason[:120], short(r)))


def case_t02():
    print("[CP4-T02] an edit aimed at a protected run file is stopped, including a relative path")
    w = world()
    ev = P(w, ".ratchet/runs/hk/evidence/CP1/behavior-01.log")
    o = drive(w, [run_of(w.plugin, [
        oc_call("edit", {"filePath": ev, "oldString": "a", "newString": "b"}),
        oc_call("edit", {"filePath": ".ratchet/runs/hk/state.json", "oldString": "a", "newString": "b"})])])
    check("[CP4-T02] setup: plugin loaded with a handler", o.factory_ok() and o.has_handler() and o.clean(),
          (o.run(), o.problems))
    check("[CP4-T02] edit of an evidence file under the absolute path is stopped with Ratchet's reason",
          ratchet_refusal(o.result(0, 0), "behavior-01.log"), short(o.result(0, 0)))
    check("[CP4-T02] edit of '.ratchet/runs/hk/state.json' (relative) is judged against the project directory the "
          "plugin was given (the driver runs elsewhere) and stopped", ratchet_refusal(o.result(0, 1), "state.json"),
          short(o.result(0, 1)))
    rel_args = {"filePath": ".ratchet/runs/hk/state.json", "oldString": "a", "newString": "b"}
    stub, log = stub_engine(w, "rel-record",
                            "note(stdin=sys.stdin.read())\nsys.stdout.write(json.dumps({'block': False}))")
    cp, _n = retargeted_plugin(w, "rel-record", stub)
    o2 = drive(w, [run_of(cp, [oc_call("edit", rel_args)])])
    lines = log_lines(log)
    try:
        sent = json.loads(lines[0]["stdin"]) if lines else {}
    except (ValueError, KeyError):
        sent = {}
    check("[CP4-T02] the engine is sent the relative path as given together with the project directory the plugin "
          "was given as cwd (the driver itself runs in another folder), so the engine judges it there",
          o2.clean() and isinstance(sent, dict) and sent.get("cwd") == str(w.proj)
          and (sent.get("tool_input") or {}).get("filePath") == ".ratchet/runs/hk/state.json", (sent, o2.problems))


def case_t03():
    print("[CP4-T03] patch tools naming a protected run file are stopped (apply_patch, and the alias patch)")
    w = world()
    state_rel, hlog_rel = ".ratchet/runs/hk/state.json", ".ratchet/human_log.jsonl"
    patches = {
        "update state (relative)": patch_text(f"*** Update File: {state_rel}", "@@", "-x", "+y"),
        "update state (absolute)": patch_text(f"*** Update File: {P(w, state_rel)}", "@@", "-x", "+y"),
        "add human_log": patch_text(f"*** Add File: {hlog_rel}", "+hello"),
        "delete state": patch_text(f"*** Delete File: {state_rel}"),
        "move into .ratchet/": patch_text("*** Update File: src/app.js", f"*** Move to: {state_rel}", "@@", "-x", "+y"),
    }
    calls, labels = [], []
    for tool in ("apply_patch", "patch"):
        for label, text in patches.items():
            calls.append(oc_call(tool, {"patchText": text}))
            labels.append(f"{tool}: {label}")
    o = drive(w, [run_of(w.plugin, calls)])
    check("[CP4-T03] setup: plugin loaded with a handler", o.factory_ok() and o.has_handler() and o.clean(),
          (o.run(), o.problems))
    for j, label in enumerate(labels):
        check(f"[CP4-T03] {label} is stopped with Ratchet's reason (patchText reaches the guard)",
              ratchet_refusal(o.result(0, j)), short(o.result(0, j)))


def case_t04():
    print("[CP4-T04] a bash command that writes a protected run file is stopped")
    w = world()
    state = P(w, ".ratchet/runs/hk/state.json")
    o = drive(w, [run_of(w.plugin, [
        oc_call("bash", {"command": f"echo '{{}}' > {state}", "description": "x"}),
        oc_call("bash", {"command": "sed -i 's/a/b/' .ratchet/human_log.jsonl"})])])
    check("[CP4-T04] setup: plugin loaded with a handler", o.factory_ok() and o.has_handler() and o.clean(),
          (o.run(), o.problems))
    check("[CP4-T04] 'echo ... > state.json' is stopped with Ratchet's reason",
          ratchet_refusal(o.result(0, 0)), short(o.result(0, 0)))
    check("[CP4-T04] \"sed -i ... .ratchet/human_log.jsonl\" is stopped with Ratchet's reason (the 'command' key "
          "reaches the guard)", ratchet_refusal(o.result(0, 1)), short(o.result(0, 1)))


def case_t05():
    print("[CP4-T05] other protected records are refused with the same reason the Claude host gets")
    w = world()
    targets = [("write", "Write", ".ratchet/human_log.jsonl", {"content": "x"}, {"content": "x"}),
               ("edit", "Edit", ".ratchet/config.json", {"oldString": "a", "newString": "b"},
                {"old_string": "a", "new_string": "b"}),
               ("write", "Write", ".ratchet/active.json", {"content": "x"}, {"content": "x"}),
               ("edit", "Edit", ".ratchet/runs/hk/evidence/CP1/behavior-01.log", {"oldString": "a", "newString": "b"},
                {"old_string": "a", "new_string": "b"})]
    review = P(w, ".ratchet/runs/hk/reviews/CP1/round-1/anything.json")
    calls = [oc_call(t, {"filePath": P(w, rel), **extra}) for t, _c, rel, extra, _x in targets]
    calls.append(oc_call("edit", {"filePath": review, "oldString": "a", "newString": "b"}))
    o = drive(w, [run_of(w.plugin, calls)])
    check("[CP4-T05] setup: plugin loaded with a handler", o.factory_ok() and o.has_handler() and o.clean(),
          (o.run(), o.problems))
    for j, (t, ctool, rel, _extra, cextra) in enumerate(targets):
        rc, reason = claude_reason(w, ctool, {"file_path": P(w, rel), **cextra})
        check(f"[CP4-T05] setup: the Claude guard refuses {rel} (exit 2 with a reason)", rc == 2 and reason != "",
              (rc, reason[:100]))
        check(f"[CP4-T05] {t} of {rel} is refused with exactly the Claude host's reason (not weaker, not a canned text)",
              refusal(o.result(0, j), reason), (short(o.result(0, j)), reason[:120]))
    rc, _reason = claude_reason(w, "Edit", {"file_path": review, "old_string": "a", "new_string": "b"})
    check("[CP4-T05] parity: a reviews/ file is writable on the Claude host (exit 0), so OpenCode lets it through too",
          rc == 0 and went_through(o.result(0, len(targets))), (rc, short(o.result(0, len(targets)))))


def case_t06():
    print("[CP4-T06] the block is a thrown Error carrying the engine's exact reason, never blank")
    w = world()
    exact = "RATCHET GATE: do not edit state.json by hand"
    multi = "RATCHET GATE: line one\nline two with \"quotes\", a tab\t and unicode \u2014 \u00e9\u4e2d"
    block_stub, _ = stub_printing(w, "block-exact", json.dumps({"block": True, "message": exact}))
    multi_stub, _ = stub_printing(w, "block-multi", json.dumps({"block": True, "message": multi}))
    blank_stub, _ = stub_printing(w, "block-blank", json.dumps({"block": True, "message": ""}))
    nomsg_stub, _ = stub_printing(w, "block-nomsg", json.dumps({"block": True}))
    ordinary = [oc_call("read", {"filePath": P(w, "src/app.js")}), oc_call("bash", {"command": "ls"})]
    runs, copies = [], []
    for stem, stub in (("exact", block_stub), ("multi", multi_stub), ("blank", blank_stub), ("nomsg", nomsg_stub)):
        cp, n = retargeted_plugin(w, f"relay-{stem}", stub)
        copies.append(n)
        runs.append(run_of(cp, ordinary, label=stem))
    o = drive(w, runs)
    check("[CP4-T06] setup: each plugin copy names the engine as a JSON string literal the test could replace",
          all(n >= 1 for n in copies), copies)
    check("[CP4-T06] setup: plugin copies loaded with a handler and the node process was clean",
          all(o.factory_ok(i) and o.has_handler(i) for i in range(4)) and o.clean(), (o.problems, o.run(0)))
    for j in range(2):
        check(f"[CP4-T06] engine says block: the handler rejects with an Error whose message is exactly the engine's "
              f"(call {j + 1}, any tool and args)", refusal(o.result(0, j), exact), short(o.result(0, j)))
        check(f"[CP4-T06] a multi-line, unicode reason is relayed exactly (call {j + 1})",
              refusal(o.result(1, j), multi), short(o.result(1, j)))
        for i, what in ((2, "an empty"), (3, "a missing")):
            check(f"[CP4-T06] {what} engine reason with block true still blocks, with a non-blank Error message "
                  f"(call {j + 1})", refusal(o.result(i, j)), short(o.result(i, j)))
    check("[CP4-T06] the handler never resolves with a value that means 'blocked'",
          all(o.result(i, j).get("status") == "threw" for i in range(4) for j in range(2)),
          [o.result(i, j).get("status") for i in range(4) for j in range(2)])
    real = drive(w, [run_of(w.plugin, [protected_write(w)])]).result()
    check("[CP4-T06] when the real engine blocks, the rejected message is non-blank", refusal(real), short(real))


def case_t07():
    print("[CP4-T07] an ordinary write outside Ratchet's records goes through")
    w = world()
    app = P(w, "src/app.js")
    calls = [oc_call("write", {"filePath": app, "content": "x"}),
             oc_call("edit", {"filePath": app, "oldString": "1", "newString": "2"}),
             oc_call("apply_patch", {"patchText": patch_text("*** Update File: src/app.js", "@@", "-x", "+y")}),
             oc_call("write", {"filePath": P(w, ".ratchet-notes.md"), "content": "notes"})]
    o = drive(w, [run_of(w.plugin, calls)])
    check("[CP4-T07] setup: plugin loaded with a handler and the process was clean",
          o.factory_ok() and o.has_handler() and o.clean(), (o.run(), o.problems))
    for j, what in enumerate(("write src/app.js", "edit src/app.js", "apply_patch updating src/app.js",
                              "write .ratchet-notes.md (next to the .ratchet folder)")):
        r = o.result(0, j)
        check(f"[CP4-T07] {what} resolves (does not throw) and leaves args as given",
              went_through(r) and r.get("argsUnchanged") is True, short(r))


def case_t08():
    print("[CP4-T08] ordinary commands and read-only tools go through")
    w = world()
    calls = [oc_call("bash", {"command": c, "description": "d"}) for c in
             ("ls -la", "git status", "python3 -m pytest tests/", "cat src/app.js")]
    names = ["bash ls -la", "bash git status", "bash python3 -m pytest tests/", "bash cat src/app.js"]
    for tool, args in (("read", {"filePath": P(w, "src/app.js")}), ("grep", {"pattern": "x", "path": P(w, "src")}),
                       ("glob", {"pattern": "**/*.js", "path": P(w, "src")}), ("list", {"path": P(w, "src")}),
                       ("task", {"description": "look", "prompt": "find x", "subagent_type": "general"})):
        calls.append(oc_call(tool, args))
        names.append(f"{tool} on ordinary arguments")
    o = drive(w, [run_of(w.plugin, calls)])
    check("[CP4-T08] setup: plugin loaded with a handler and the process was clean",
          o.factory_ok() and o.has_handler() and o.clean(), (o.run(), o.problems))
    for j, n in enumerate(names):
        check(f"[CP4-T08] {n} resolves", went_through(o.result(0, j)), short(o.result(0, j)))


def parity_rows(w):
    """(label, expect_block, opencode tool, opencode args, claude tool, claude tool_input)."""
    st, hl, ev, app = (P(w, ".ratchet/runs/hk/state.json"), P(w, ".ratchet/human_log.jsonl"),
                       P(w, ".ratchet/runs/hk/evidence/CP1/behavior-01.log"), P(w, "src/app.js"))
    rel_st, rel_hl = ".ratchet/runs/hk/state.json", ".ratchet/human_log.jsonl"
    rows = [
        ("write state.json", True, "write", {"filePath": st, "content": "{}"}, "Write", {"file_path": st}),
        ("write human_log.jsonl", True, "write", {"filePath": hl, "content": "x"}, "Write", {"file_path": hl}),
        ("edit evidence file", True, "edit", {"filePath": ev, "oldString": "a", "newString": "b"}, "Edit",
         {"file_path": ev, "old_string": "a", "new_string": "b"}),
        ("write relative state.json", True, "write", {"filePath": rel_st, "content": "x"}, "Write",
         {"file_path": rel_st}),
        ("apply_patch update state.json", True, "apply_patch",
         {"patchText": patch_text(f"*** Update File: {rel_st}", "@@", "-x", "+y")}, "Write", {"file_path": rel_st}),
        ("patch (alias) add human_log.jsonl", True, "patch", {"patchText": patch_text(f"*** Add File: {rel_hl}", "+x")},
         "Write", {"file_path": rel_hl}),
        ("patch (alias) delete state.json", True, "patch", {"patchText": patch_text(f"*** Delete File: {rel_st}")}, "Write",
         {"file_path": rel_st}),
        ("apply_patch move into .ratchet/", True, "apply_patch",
         {"patchText": patch_text("*** Update File: src/app.js", f"*** Move to: {rel_st}", "@@", "-x", "+y")}, "Write",
         {"file_path": rel_st}),
        ("bash redirect into state.json", True, "bash", {"command": f"echo '{{}}' > {st}", "description": "x"}, "Bash",
         {"command": f"echo '{{}}' > {st}"}),
        ("bash sed -i on human_log.jsonl", True, "bash", {"command": f"sed -i 's/a/b/' {rel_hl}"}, "Bash",
         {"command": f"sed -i 's/a/b/' {rel_hl}"}),
        ("read state.json", False, "read", {"filePath": st}, "Read", {"file_path": st}),
        ("bash cat state.json", False, "bash", {"command": f"cat {st}"}, "Bash", {"command": f"cat {st}"}),
        ("grep in .ratchet", False, "grep", {"pattern": "x", "path": P(w, ".ratchet")}, "Grep",
         {"pattern": "x", "path": P(w, ".ratchet")}),
        ("glob in .ratchet", False, "glob", {"pattern": "**/*.json", "path": P(w, ".ratchet")}, "Glob",
         {"pattern": "**/*.json", "path": P(w, ".ratchet")}),
        ("list (alias) the .ratchet folder", False, "list", {"path": P(w, ".ratchet")}, "LS", {"path": P(w, ".ratchet")}),
        ("write a review file", False, "write", {"filePath": P(w, ".ratchet/runs/hk/reviews/CP1/round-1/anything.json"),
                                                 "content": "{}"}, "Write",
         {"file_path": P(w, ".ratchet/runs/hk/reviews/CP1/round-1/anything.json")}),
        ("write src/app.js", False, "write", {"filePath": app, "content": "x"}, "Write", {"file_path": app}),
        ("edit src/app.js", False, "edit", {"filePath": app, "oldString": "1", "newString": "2"}, "Edit",
         {"file_path": app, "old_string": "1", "new_string": "2"}),
        ("apply_patch src/app.js", False, "apply_patch",
         {"patchText": patch_text("*** Update File: src/app.js", "@@", "-x", "+y")}, "Write",
         {"file_path": "src/app.js"}),
        ("write .ratchet-notes.md", False, "write", {"filePath": P(w, ".ratchet-notes.md"), "content": "n"}, "Write",
         {"file_path": P(w, ".ratchet-notes.md")}),
        ("bash ls -la", False, "bash", {"command": "ls -la"}, "Bash", {"command": "ls -la"}),
        ("bash git status", False, "bash", {"command": "git status"}, "Bash", {"command": "git status"}),
        ("read src/app.js", False, "read", {"filePath": app}, "Read", {"file_path": app}),
        ("grep in src", False, "grep", {"pattern": "x", "path": P(w, "src")}, "Grep",
         {"pattern": "x", "path": P(w, "src")}),
    ]
    return rows


def case_t09():
    print("[CP4-T09] OpenCode verdicts match the Claude guard's verdicts for the same action")
    w = world()
    rows = parity_rows(w)
    o = drive(w, [run_of(w.plugin, [oc_call(t, a) for _l, _e, t, a, _c, _ci in rows])])
    check("[CP4-T09] setup: plugin loaded with a handler and the process was clean",
          o.factory_ok() and o.has_handler() and o.clean(), (o.run(), o.problems))
    blocked_n = allowed_n = 0
    for j, (label, expect, _t, _a, ctool, cinput) in enumerate(rows):
        rc, _out, _err = engine_guard(w, "claude", {"tool_name": ctool, "tool_input": cinput, "cwd": str(w.proj)})
        r = o.result(0, j)
        oc_blocked = r.get("status") == "threw"
        known = r.get("status") in ("threw", "resolved")
        check(f"[CP4-T09] {label}: the Claude guard says {'block' if expect else 'allow'} (exit {2 if expect else 0})",
              rc == (2 if expect else 0), rc)
        check(f"[CP4-T09] {label}: OpenCode {'throws' if rc == 2 else 'resolves'} exactly when the Claude guard "
              f"exits {rc}", known and (oc_blocked == (rc == 2)) and (refusal(r) if rc == 2 else went_through(r)),
              short(r))
        blocked_n += rc == 2
        allowed_n += rc == 0
    check("[CP4-T09] the table really covers both verdicts (at least 8 blocked and 8 allowed actions)",
          blocked_n >= 8 and allowed_n >= 8, (blocked_n, allowed_n))


def case_t10():
    print("[CP4-T10] the engine alone understands OpenCode tool names and argument keys")
    w = world()
    base = {"hook_event_name": "tool.execute.before", "session_id": "ses_1", "call_id": "call_1", "cwd": str(w.proj)}
    st_rel = ".ratchet/runs/hk/state.json"
    cases = [
        ("write", {"filePath": P(w, st_rel), "content": "{}"}, {"filePath": P(w, "src/app.js"), "content": "x"}),
        ("edit", {"filePath": P(w, st_rel), "oldString": "a", "newString": "b"},
         {"filePath": P(w, "src/app.js"), "oldString": "a", "newString": "b"}),
        ("apply_patch", {"patchText": patch_text(f"*** Update File: {st_rel}", "@@", "-x", "+y")},
         {"patchText": patch_text("*** Update File: src/app.js", "@@", "-x", "+y")}),
        ("patch", {"patchText": patch_text(f"*** Update File: {st_rel}", "@@", "-x", "+y")},
         {"patchText": patch_text("*** Update File: src/app.js", "@@", "-x", "+y")}),
        ("bash", {"command": f"echo '{{}}' > {st_rel}"}, {"command": "echo hi > src/app.js"}),
    ]
    for tool, prot, ordinary in cases:
        rc, out_, err = engine_guard(w, "opencode", {**base, "tool_name": tool, "tool_input": prot})
        obj = parse_obj(out_)
        check(f"[CP4-T10] {tool} aimed at state.json: exit 0 and one JSON object {{block: true, message: <reason>}}",
              rc == 0 and obj is not None and obj.get("block") is True and isinstance(obj.get("message"), str)
              and obj["message"].strip() != "" and len(out_.strip().splitlines()) == 1 and "Traceback" not in err,
              (rc, out_[:150], err[:150]))
        rc, out_, err = engine_guard(w, "opencode", {**base, "tool_name": tool, "tool_input": ordinary})
        obj = parse_obj(out_)
        check(f"[CP4-T10] {tool} aimed at src/app.js: exit 0 and {{block: false}}",
              rc == 0 and obj is not None and obj.get("block") is False and "Traceback" not in err,
              (rc, out_[:150], err[:150]))
    for what, raw in (("empty stdin", ""), ("'not json'", "not json"), ("a JSON list", "[true]")):
        rc, out_, err = engine_guard(w, "opencode", None, raw=raw)
        obj = parse_obj(out_)
        check(f"[CP4-T10] malformed stdin ({what}): exit 0 and {{block: false}}",
              rc == 0 and obj is not None and obj.get("block") is False and "Traceback" not in err,
              (rc, out_[:150], err[:150]))


def parse_obj(text):
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def case_t11():
    print("[CP4-T11] the engine file is missing: the tool call goes through")
    w = world()
    missing = w.work / "no-such-folder" / "ratchet.py"
    cp, n = retargeted_plugin(w, "missing-engine", missing)
    o = drive(w, [run_of(cp, [protected_write(w)], label="missing"), run_of(w.plugin, [protected_write(w)], label="control")])
    check("[CP4-T11] setup: the copy names the engine as a JSON string literal, and no engine exists at that path",
          n >= 1 and not missing.exists(), (n, missing.exists()))
    check("[CP4-T11] setup: plugin loaded with a handler and the process was clean",
          o.factory_ok(0) and o.has_handler(0) and o.clean(), (o.run(0), o.problems))
    check("[CP4-T11] a protected write resolves when the engine path does not exist (no stale path blocks tools)",
          went_through(o.result(0, 0)), short(o.result(0, 0)))
    check("[CP4-T11] control: the same call through the working engine IS stopped (so the resolve above is the "
          "fail-open path, not a plugin that never asks)", ratchet_refusal(o.result(1, 0)), short(o.result(1, 0)))


def case_t12():
    print("[CP4-T12] the engine crashes or exits with a usage error: the tool call goes through")
    w = world()
    crash, crash_log = stub_engine(w, "crash", "note(argv=sys.argv)\nsys.stdin.read()\nraise RuntimeError('boom')")
    usage, usage_log = stub_printing(w, "usage", "", 2, "usage: ratchet.py [-h] COMMAND ...\nratchet.py: error: "
                                                        "unrecognized arguments: guard\n")
    stderr_reason, reason_log = stub_printing(w, "exit2-reason", "", 2, "RATCHET GUARD: pretend block, Claude style\n")
    stubs = [("exit 1 with a Python traceback", crash, crash_log), ("exit 2 with an argparse usage message", usage,
                                                                      usage_log),
             ("exit 2 with a reason on stderr and nothing on stdout (Claude-style block)", stderr_reason, reason_log)]
    runs, copies = [], []
    for what, stub, _log in stubs:
        cp, n = retargeted_plugin(w, "crashy", stub)
        copies.append(n)
        runs.append(run_of(cp, [protected_write(w)], label=what))
    o = drive(w, runs)
    check("[CP4-T12] setup: copies name the engine as a JSON string literal", all(n >= 1 for n in copies), copies)
    check("[CP4-T12] setup: plugin copies loaded with a handler and the process was clean",
          all(o.factory_ok(i) and o.has_handler(i) for i in range(3)) and o.clean(), (o.problems, o.run(0)))
    for i, (what, _stub, log) in enumerate(stubs):
        check(f"[CP4-T12] setup: the stub engine ({what}) was really run by the plugin", len(log_lines(log)) >= 1,
              log_lines(log))
        check(f"[CP4-T12] engine {what}: the protected write still resolves (never read as a block)",
              went_through(o.result(i, 0)), short(o.result(i, 0)))


def case_t13():
    print("[CP4-T13] the engine prints garbage: the tool call goes through")
    w = world()
    replies = [("nothing", ""), ("'not json'", "not json"), ("truncated JSON", '{"block": tr'),
               ("a JSON list '[true]'", "[true]"), ("the wrong shape {decision: block}", '{"decision": "block"}'),
               ("a wrong type {block: 'true'}", '{"block": "true", "message": "x"}'),
               ("a number {block: 1}", '{"block": 1, "message": "x"}'), ("JSON null", "null"),
               ("a bare JSON string", '"block"'), ("nested {result: {block: true}}",
                                                 '{"result": {"block": true, "message": "x"}}')]
    runs, copies, logs = [], [], []
    for what, text in replies:
        stub, log = stub_printing(w, "garbage", text)
        cp, n = retargeted_plugin(w, "garbage", stub)
        copies.append(n)
        logs.append(log)
        runs.append(run_of(cp, [protected_write(w)], label=what))
    o = drive(w, runs)
    check("[CP4-T13] setup: copies name the engine as a JSON string literal", all(n >= 1 for n in copies), copies)
    check("[CP4-T13] setup: plugin copies loaded with a handler and the process was clean",
          all(o.factory_ok(i) and o.has_handler(i) for i in range(len(replies))) and o.clean(), (o.problems, o.run(0)))
    for i, (what, _text) in enumerate(replies):
        check(f"[CP4-T13] setup: the stub engine printing {what} was really run", len(log_lines(logs[i])) >= 1,
              log_lines(logs[i]))
        check(f"[CP4-T13] engine prints {what}: the protected write resolves (only block: true is a block)",
              went_through(o.result(i, 0)), short(o.result(i, 0)))


def case_t14():
    print(f"[CP4-T14] the engine hangs past the time limit: the call goes through and the child is stopped "
          f"(a copy retargeted from the shipped {HOOK_TIME_LIMIT_S} s limit to {HANG_TEST_LIMIT_MS} ms)")
    w = world()
    shipped = w.plugin.read_text(encoding="utf-8")
    check(f"[CP4-T14] the shipped plugin's guard time limit is exactly {HOOK_TIME_LIMIT_S} s "
          f"(`const GUARD_TIMEOUT_MS = {HOOK_TIME_LIMIT_S * 1000};` appears once in the installed, unpatched plugin)",
          shipped.count(f"const GUARD_TIMEOUT_MS = {HOOK_TIME_LIMIT_S * 1000};") == 1
          and len(re.findall(r"GUARD_TIMEOUT_MS\s*=", shipped)) == 1, re.findall(r".*GUARD_TIMEOUT_MS.*", shipped)[:3])
    pidfile = w.work / "hang.pid"
    stub, log = stub_engine(w, "hang", f"note(pid=os.getpid())\nopen({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
                                      "time.sleep(600)")
    cp, n = retargeted_plugin(w, "hang", stub)
    found, left_30s, has_short = with_short_guard_timeout(cp, HANG_TEST_LIMIT_MS)
    check("[CP4-T14] setup: the copy's time limit was retargeted (the 30 s constant matched exactly once, none is "
          "left, the short one is in place)", found == 1 and not left_30s and has_short, (found, left_30s, has_short))
    pid = None
    try:
        t0 = time.time()
        o = drive(w, [run_of(cp, [protected_write(w)])], timeout=60)
        elapsed = time.time() - t0
        try:
            pid = int(pidfile.read_text().strip())
        except (OSError, ValueError):
            pid = None
        check("[CP4-T14] setup: the copy names the engine as a JSON string literal, and the hung stub really started "
              "(it recorded its pid)", n >= 1 and pid is not None, (n, pid))
        check("[CP4-T14] setup: plugin loaded with a handler and the process was clean",
              o.factory_ok() and o.has_handler() and o.clean(), (o.run(), o.problems))
        r = o.result()
        check(f"[CP4-T14] the protected write resolves (does not throw, does not wait forever) within the "
              f"{HANG_TEST_LIMIT_MS} ms limit plus a few seconds", went_through(r) and r.get("ms", 10 ** 9) <=
              HANG_TEST_LIMIT_MS + 8000, (short(r), round(elapsed, 1)))
        stopped = pid is not None and wait_until_stopped(pid)
        check("[CP4-T14] the hung engine process is no longer running afterwards (the plugin stopped it)", stopped,
              f"pid {pid} still running")
    finally:
        if pid is not None and pid_running(pid):
            try:
                os.kill(pid, 9)
            except OSError:
                pass


def case_t15():
    print("[CP4-T15] python3 cannot be started: the tool call goes through")
    w = world()
    shim = w.work / "path-with-node-only"
    shim.mkdir(exist_ok=True)
    link = shim / "node"
    if not link.exists():
        os.symlink(NODE, link)
    env = w.env(PATH=str(shim))
    check("[CP4-T15] setup: the PATH given to the driver has node but no python3",
          shutil.which("node", path=str(shim)) is not None and shutil.which("python3", path=str(shim)) is None
          and shutil.which("python", path=str(shim)) is None)
    o = drive(w, [run_of(w.plugin, [protected_write(w)])], env=env)
    check("[CP4-T15] the process did not crash and reported no unhandled rejection or uncaught exception",
          o.clean(), (o.rc, o.problems, o.stderr[-200:]))
    check("[CP4-T15] setup: plugin loaded with a handler", o.factory_ok() and o.has_handler(), o.run())
    check("[CP4-T15] the protected write resolves when spawning the engine fails with ENOENT",
          went_through(o.result()), short(o.result()))


def case_t16():
    print("[CP4-T16] a bug inside the plugin never blocks a tool call")
    w = world()
    app = "dir + '/src/app.js'"
    tool_in = {"tool": "write", "sessionID": "ses_1", "callID": "call_1"}
    ordinary_args = {"filePath": P(w, "src/app.js"), "content": "x"}
    hostile = [
        ("input undefined", {"input": {"$js": "undefined"}, "output": {"args": ordinary_args}}),
        ("input null", {"input": {"$js": "null"}, "output": {"args": ordinary_args}}),
        ("output undefined", {"input": tool_in, "output": {"$js": "undefined"}}),
        ("output null", {"input": tool_in, "output": {"$js": "null"}}),
        ("output {} with no args", {"input": tool_in, "output": {}}),
        ("args null", {"input": tool_in, "output": {"args": None}}),
        ("args a string", {"input": tool_in, "output": {"args": "just a string"}}),
        ("args a number", {"input": tool_in, "output": {"args": 42}}),
        ("args an array", {"input": tool_in, "output": {"args": [1, 2]}}),
        ("tool name missing", {"input": {"sessionID": "s", "callID": "c"}, "output": {"args": ordinary_args}}),
        ("tool name a number", {"input": {"tool": 42, "sessionID": "s", "callID": "c"},
                                "output": {"args": ordinary_args}}),
        ("tool name an object", {"input": {"tool": {"name": "write"}, "sessionID": "s", "callID": "c"},
                                 "output": {"args": ordinary_args}}),
        ("session and call ids missing", {"input": {"tool": "write"}, "output": {"args": ordinary_args}}),
        ("args circular (JSON.stringify throws)",
         {"input": tool_in, "output": {"$js": f"(() => {{ const a = {{filePath: {app}, content: 'x'}}; a.self = a; "
                                              "return {args: a}; })()"}}),
        ("args holding a 5 MB string",
         {"input": tool_in, "output": {"$js": f"({{args: {{filePath: {app}, content: 'x'.repeat(5 * 1024 * 1024)}}}})"}}),
        ("args a 5 MB string", {"input": tool_in, "output": {"$js": "({args: 'y'.repeat(5 * 1024 * 1024)})"}}),
    ]
    one_call = [oc_call("write", ordinary_args)]
    runs = [run_of(w.plugin, [c for _l, c in hostile], label="hostile")]
    runs += [run_of(w.plugin, one_call, label="ctx empty object", ctx={}),
             run_of(w.plugin, one_call, label="ctx undefined", ctx={"$js": "undefined"}),
             run_of(w.plugin, one_call, label="client and directory undefined",
                    ctx={"$js": "({client: undefined, directory: undefined, worktree: undefined})"})]
    o = drive(w, runs, timeout=300)
    check("[CP4-T16] the factory does not throw for a full, an empty, an undefined and a client-less context",
          all(o.factory_ok(i) and o.has_handler(i) for i in range(4)), [o.run(i).get("factory") for i in range(4)])
    check("[CP4-T16] the node process exits cleanly with no unhandled rejection or uncaught exception",
          o.clean(), (o.rc, o.problems, o.stderr[-200:]))
    for j, (label, _c) in enumerate(hostile):
        check(f"[CP4-T16] hostile input ({label}): the call goes through, no throw or rejection",
              went_through(o.result(0, j)), short(o.result(0, j)))
    for i, label in ((1, "ctx empty object"), (2, "ctx undefined"), (3, "client and directory undefined")):
        check(f"[CP4-T16] factory with {label}: an ordinary call goes through", went_through(o.result(i, 0)),
              short(o.result(i, 0)))


def case_t17():
    print("[CP4-T17] the plugin hands the engine the right message, without a shell")
    w = world()
    marker = w.work / "pwned-by-test"
    stub_body = ("note(argv=sys.argv, stdin=sys.stdin.read())\nsys.stdout.write(json.dumps({'block': False}))")
    stub, log = stub_engine(w, "record", stub_body, dirname="e n'g dir")
    cp, n = retargeted_plugin(w, "record", stub)
    cmd = f"echo $(touch {marker}); `id` ; \"quoted\""
    args = {"command": cmd, "description": "weird"}
    o = drive(w, [run_of(cp, [oc_call("bash", args, sid="ses_1", cid="call_1")])])
    check("[CP4-T17] setup: the copy names the engine (its path has a space and a single quote) as a JSON literal",
          n >= 1 and " " in str(stub) and "'" in str(stub), (n, str(stub)))
    check("[CP4-T17] setup: plugin loaded with a handler and the process was clean",
          o.factory_ok() and o.has_handler() and o.clean(), (o.run(), o.problems))
    lines = log_lines(log)
    check("[CP4-T17] the engine stub ran exactly once", len(lines) == 1, lines)
    rec = lines[0] if lines else {}
    check("[CP4-T17] argv after the interpreter is exactly [engine, 'hook', 'guard', '--host', 'opencode'] (the "
          "command text is not in argv)", rec.get("argv") == [str(stub), "hook", "guard", "--host", "opencode"],
          rec.get("argv"))
    try:
        sent = json.loads(rec.get("stdin", ""))
    except ValueError:
        sent = None
    check("[CP4-T17] stdin is one JSON object", isinstance(sent, dict), str(rec.get("stdin"))[:200])
    sent = sent if isinstance(sent, dict) else {}
    check("[CP4-T17] hook_event_name is 'tool.execute.before', tool_name 'bash', session_id 'ses_1', call_id "
          "'call_1'", sent.get("hook_event_name") == "tool.execute.before" and sent.get("tool_name") == "bash"
          and sent.get("session_id") == "ses_1" and sent.get("call_id") == "call_1", sent)
    check("[CP4-T17] tool_input equals the args OpenCode passed in output.args (not the input object)",
          sent.get("tool_input") == args, sent.get("tool_input"))
    check("[CP4-T17] cwd is the project directory the plugin was given", sent.get("cwd") == str(w.proj), sent.get("cwd"))
    check("[CP4-T17] nothing was run through a shell (the marker file was not created)", not marker.exists(),
          "marker exists")
    check("[CP4-T17] the call resolves", went_through(o.result()), short(o.result()))


def case_t18():
    print("[CP4-T18] a blocked call is blocked every time; a later ordinary call still goes through")
    w = world()
    ordinary = oc_call("write", {"filePath": P(w, "src/app.js"), "content": "x"}, cid="call_ok")
    calls = [protected_write(w, cid="c1"), protected_write(w, cid="c2"), protected_write(w, cid="c3"), ordinary,
             protected_write(w, cid="c5")]
    o = drive(w, [run_of(w.plugin, calls)])
    check("[CP4-T18] setup: plugin loaded with a handler and the process was clean",
          o.factory_ok() and o.has_handler() and o.clean(), (o.run(), o.problems))
    first = o.result(0, 0)
    check("[CP4-T18] the first protected write is stopped with Ratchet's reason", ratchet_refusal(first), short(first))
    for j in (1, 2, 4):
        check(f"[CP4-T18] protected write #{j + 1} is stopped with the same reason", refusal(o.result(0, j),
                                                                                           first.get("message")),
              short(o.result(0, j)))
    check("[CP4-T18] the ordinary write in between resolves (no latched block)", went_through(o.result(0, 3)),
          short(o.result(0, 3)))


def case_t19():
    print("[CP4-T19] simultaneous tool calls are judged independently")
    w = world()
    app = P(w, "src/app.js")
    pair = [protected_write(w, cid="p1"), oc_call("write", {"filePath": app, "content": "x"}, cid="o1")]
    burst = [protected_write(w, cid=f"p{i}") if i % 2 == 0 else
             oc_call("write", {"filePath": app, "content": "x"}, cid=f"o{i}") for i in range(8)]
    o = drive(w, [run_of(w.plugin, pair, label="pair", mode="concurrent"),
                  run_of(w.plugin, burst, label="burst", mode="concurrent")])
    check("[CP4-T19] setup: plugin loaded with a handler and the process was clean",
          o.factory_ok(0) and o.has_handler(0) and o.clean(), (o.run(0), o.problems))
    check("[CP4-T19] started at the same moment, the protected write rejects with Ratchet's reason",
          ratchet_refusal(o.result(0, 0)), short(o.result(0, 0)))
    check("[CP4-T19] started at the same moment, the ordinary write resolves", went_through(o.result(0, 1)),
          short(o.result(0, 1)))
    for i in range(8):
        if i % 2 == 0:
            check(f"[CP4-T19] burst call {i} (protected) rejects with Ratchet's reason",
                  ratchet_refusal(o.result(1, i)), short(o.result(1, i)))
        else:
            check(f"[CP4-T19] burst call {i} (ordinary) resolves", went_through(o.result(1, i)), short(o.result(1, i)))


# ------------------------------------------------------------------------------------------------ README (T20)

def md_table_rows(text):
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        rows.append(cells)
    return rows


def first_table(text):
    kept, started = [], False
    for ln in text.splitlines():
        if ln.strip().startswith("|"):
            started = True
            kept.append(ln)
        elif started:
            break
    return "\n".join(kept)


def cli_section(text):
    """The text under the first heading that mentions 'Using it with Codex' (to the next heading of the same or a
    higher level); headings inside code fences are ignored."""
    lines = text.splitlines()
    fenced, start, level = False, None, 0
    for i, ln in enumerate(lines):
        if ln.startswith("```"):
            fenced = not fenced
            continue
        if fenced or not ln.startswith("#"):
            continue
        lv = len(ln) - len(ln.lstrip("#"))
        if start is None:
            if re.search(r"Using it with Codex", ln):
                start, level = i + 1, lv
        elif lv <= level:
            return "\n".join(lines[start:i])
    return "\n".join(lines[start:]) if start is not None else ""


def says_yes(cell):
    return isinstance(cell, str) and re.match(r"Yes\b", cell.strip()) is not None


def host_enforcement():
    """HOST_ENFORCEMENT, read by running the engine file in a child process (never loaded into this process)."""
    code = ("import json, os, runpy, sys\nsys.path.insert(0, os.path.dirname(sys.argv[1]))\n"
            "g = runpy.run_path(sys.argv[1])\nprint(json.dumps(g['HOST_ENFORCEMENT']))\n")
    env = scrubbed_env()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with _tmp.mkdtemp(prefix="ratchet-ocguard-probe-") as cwd:
        r = subprocess.run([sys.executable, "-c", code, str(ENGINE)], cwd=cwd, capture_output=True, text=True, env=env,
                           timeout=180)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def readme_test_block():
    text = README.read_text(encoding="utf-8")
    if "### Running the tests" not in text:
        return []
    after = text.split("### Running the tests", 1)[1].splitlines()
    block, fenced = [], False
    for ln in after:
        if ln.startswith("```"):
            fenced = not fenced
        elif not fenced and ln.startswith("#"):
            break
        block.append(ln)
    return block


def inversions(items):
    return [(a, b) for a, b in zip(items, items[1:]) if a > b]


FORBIDDEN_PARTS = (("spec_from", "_file_location"), ("ENGINE.", "read_text"), ("a", "st.parse"), ("a", "st.walk"),
                   ("get", "source"))


def inventory_hits(text):
    return [a + b for a, b in FORBIDDEN_PARTS if a + b in text]


def case_t20():
    print("[CP4-T20] README: OpenCode protects Ratchet's records, and the new test file is listed")
    text = README.read_text(encoding="utf-8")
    rows = md_table_rows(first_table(cli_section(text)))
    by_name = {r[0]: r for r in rows[1:] if r}
    check("[CP4-T20] setup: the per-CLI table has an OpenCode row with five cells",
          "OpenCode" in by_name and len(by_name["OpenCode"]) == 5, list(by_name))
    cells = by_name.get("OpenCode", [])
    check("[CP4-T20] the OpenCode 'Protects Ratchet's records' cell says Yes",
          len(cells) == 5 and says_yes(cells[1]), cells[1] if len(cells) == 5 else cells)
    enf = host_enforcement()
    check("[CP4-T20] the engine's HOST_ENFORCEMENT agrees: opencode 'records' is True, so the cell and the code are "
          "in step", isinstance(enf, dict) and (enf.get("opencode") or {}).get("records") is True,
          (enf or {}).get("opencode"))
    check("[CP4-T20] teeth: a 'No, ...' cell and an empty cell are not 'Yes'",
          not says_yes("No, Ratchet's state and evidence files are not protected from hand-edits.")
          and not says_yes("") and says_yes("Yes, Ratchet's state and evidence files are protected from hand-edits."))
    block = readme_test_block()
    lst = [ln for ln in block if ln.startswith("python3 tests/")]
    check("[CP4-T20] the 'Running the tests' list holds the exact line exactly once, and it appears nowhere else",
          block.count(NEW_TEST_LINE) == 1 and text.splitlines().count(NEW_TEST_LINE) == 1, block.count(NEW_TEST_LINE))
    if NEW_TEST_LINE in lst:
        i = lst.index(NEW_TEST_LINE)
        ctx = lst[max(0, i - 1):i + 2]
        check("[CP4-T20] its previous neighbour in the list sorts before it", i == 0 or lst[i - 1] < NEW_TEST_LINE, ctx)
        check("[CP4-T20] its next neighbour in the list sorts after it", i + 1 >= len(lst) or lst[i + 1] > NEW_TEST_LINE,
              ctx)
        without = [x for x in lst if x != NEW_TEST_LINE]
        check("[CP4-T20] adding the line adds no out-of-order pair to the list",
              inversions(lst) == inversions(without), inversions(lst))
    else:
        for n in ("its previous neighbour in the list sorts before it", "its next neighbour in the list sorts after it",
                  "adding the line adds no out-of-order pair to the list"):
            check(f"[CP4-T20] {n}", False, "line missing")
    own = THIS_FILE.read_text(encoding="utf-8")
    check("[CP4-T20] this file holds none of the strings the test inventory looks for, so it needs no inventory entry",
          inventory_hits(own) == [], inventory_hits(own))
    check("[CP4-T20] teeth: the inventory-string finder does spot them in a sample that loads ratchet.py",
          inventory_hits("importlib.util.spec_from" + "_file_location('x', p)") != [])


# --------------------------------------------------------------------------------------- no node (T21) and teeth

def node_refusal_ok(rc, text):
    """A run without node: non-zero exit, a plain message that node is required, and no case reported as passed."""
    return (rc != 0 and re.search(r"\bnode\b[^\n]*\b(?:required|needed|not found|missing|install)", text, re.I) is not None
            and "✓ [CP4-" not in text and re.search(r"\d+ passed, 0 failed", text) is None
            and not re.search(r"\bskip", text, re.I))


def case_t21():
    print("[CP4-T21] the script refuses to pass without node")
    w = world()
    shim = w.work / "path-without-node"
    shim.mkdir(exist_ok=True)
    for tool in ("git", "python3"):
        real = shutil.which(tool)
        if real and not (shim / tool).exists():
            os.symlink(real, shim / tool)
    env = scrubbed_env()
    env.update(PATH=str(shim), HOME=str(w.home), PYTHONDONTWRITEBYTECODE="1")
    check("[CP4-T21] setup: the PATH given to the script has no node", shutil.which("node", path=str(shim)) is None)
    t0 = time.time()
    p = subprocess.run([sys.executable, str(THIS_FILE)], env=env, capture_output=True, text=True, timeout=240,
                       cwd=str(w.work))
    text = (p.stdout or "") + (p.stderr or "")
    check("[CP4-T21] it exits non-zero with a plain message that node is required, and reports no case as passed "
          "or skipped", node_refusal_ok(p.returncode, text), (p.returncode, text[:300]))
    check("[CP4-T21] it refuses promptly instead of running the plugin cases", time.time() - t0 < 60,
          round(time.time() - t0, 1))
    check("[CP4-T21] teeth: exit 0, a message without node, a passed-case line and a clean summary are all rejected",
          not node_refusal_ok(0, "node is required")
          and not node_refusal_ok(2, "something failed")
          and not node_refusal_ok(2, "node is required\n  ✓ [CP4-T01] x")
          and not node_refusal_ok(1, "node is required\n3 passed, 0 failed")
          and node_refusal_ok(2, "ERROR: node is required to run this test"))


# Mutant plugins: each breaks exactly one thing, to show the checkers above can fail.
MUTANT_ALWAYS = ("export const RatchetPlugin = async () => ({'tool.execute.before': async () => "
                 "{ throw new Error('RATCHET: always block'); }});\n")
MUTANT_NEVER = "export const RatchetPlugin = async () => ({'tool.execute.before': async () => {}});\n"
MUTANT_LATCH = ("let latched = false;\nexport const RatchetPlugin = async () => ({'tool.execute.before': "
                "async (input, output) => {\n  if (JSON.stringify(output.args).includes('.ratchet/')) latched = true;\n"
                "  if (latched) throw new Error('RATCHET: latched');\n}});\n")
MUTANT_CANNED = ("import { spawnSync } from 'node:child_process';\nconst E = __ENGINE__;\n"
                 "export const RatchetPlugin = async (ctx) => ({'tool.execute.before': async (input, output) => {\n"
                 "  const r = spawnSync('python3', [E, 'hook', 'guard', '--host', 'opencode'], {input: '{}', encoding: 'utf8'});\n"
                 "  let j; try { j = JSON.parse(r.stdout); } catch (e) { return; }\n"
                 "  if (j && j.block === true) throw new Error('blocked');\n}});\n")
MUTANT_CLOSED = ("import { spawnSync } from 'node:child_process';\nconst E = __ENGINE__;\n"
                 "export const RatchetPlugin = async (ctx) => ({'tool.execute.before': async (input, output) => {\n"
                 "  const r = spawnSync('python3', [E, 'hook', 'guard', '--host', 'opencode'], {input: '{}', encoding: 'utf8'});\n"
                 "  if (r.error || r.status !== 0) throw new Error('RATCHET: guard unavailable');\n"
                 "  const j = JSON.parse(r.stdout); if (j.block === true) throw new Error(j.message);\n}});\n")
MUTANT_TRUTHY = ("import { spawnSync } from 'node:child_process';\nconst E = __ENGINE__;\n"
                 "export const RatchetPlugin = async (ctx) => ({'tool.execute.before': async (input, output) => {\n"
                 "  const r = spawnSync('python3', [E, 'hook', 'guard', '--host', 'opencode'], {input: '{}', encoding: 'utf8'});\n"
                 "  let j; try { j = JSON.parse(r.stdout); } catch (e) { return; }\n"
                 "  if (j && j.block) throw new Error(j.message || 'x');\n}});\n")


def case_teeth():
    print("teeth: the checkers reject the wrong behavior (mutant plugins run through the same driver)")
    w = world()
    d = w.work / "mutants"
    d.mkdir(exist_ok=True)
    missing = str(w.work / "no-such-folder" / "ratchet.py")
    stub_block, _ = stub_printing(w, "teeth-block", json.dumps({"block": True, "message": "RATCHET GATE: exact"}))
    stub_yes, _ = stub_printing(w, "teeth-yes", json.dumps({"block": "yes", "message": "x"}))
    files = {}
    for name, src, engine in (("always", MUTANT_ALWAYS, ""), ("never", MUTANT_NEVER, ""), ("latch", MUTANT_LATCH, ""),
                              ("canned", MUTANT_CANNED, str(stub_block)), ("closed", MUTANT_CLOSED, missing),
                              ("truthy", MUTANT_TRUTHY, str(stub_yes))):
        files[name] = d / f"{name}.js"
        files[name].write_text(src.replace("__ENGINE__", json.dumps(engine)), encoding="utf-8")
    app = oc_call("write", {"filePath": P(w, "src/app.js"), "content": "x"})
    o = drive(w, [run_of(files["always"], [protected_write(w), app], label="always"),
                  run_of(files["never"], [protected_write(w)], label="never"),
                  run_of(files["latch"], [protected_write(w), app], label="latch"),
                  run_of(files["canned"], [protected_write(w)], label="canned"),
                  run_of(files["closed"], [protected_write(w)], label="closed"),
                  run_of(files["truthy"], [protected_write(w)], label="truthy")])
    check("[CP4-T01] teeth: setup: every mutant loaded under the driver", all(o.factory_ok(i) and o.has_handler(i)
                                                                               for i in range(6)), o.problems)
    check("[CP4-T01] teeth: a plugin that never blocks fails the protected-write check",
          not ratchet_refusal(o.result(1, 0)), short(o.result(1, 0)))
    check("[CP4-T01] teeth: a result with no handler, a resolve, a thrown string and a blank Error are not refusals",
          not refusal({"status": "nohandler"}) and not refusal({"status": "resolved", "valueIsNullish": True})
          and not refusal({"status": "threw", "isError": False, "message": "x"})
          and not refusal({"status": "threw", "isError": True, "message": "  "})
          and refusal({"status": "threw", "isError": True, "message": "RATCHET: x"}))
    check("[CP4-T07] teeth: a plugin that always blocks fails the ordinary-write check",
          not went_through(o.result(0, 1)) and ratchet_refusal(o.result(0, 0)),
          (short(o.result(0, 1)), short(o.result(0, 0))))
    check("[CP4-T07] teeth: no handler, a thrown Error and a resolve carrying a value are not 'went through'",
          not went_through({"status": "nohandler"}) and not went_through({"status": "threw", "isError": True,
                                                                          "message": "x"})
          and not went_through({"status": "resolved", "valueIsNullish": False})
          and went_through({"status": "resolved", "valueIsNullish": True}))
    check("[CP4-T11] teeth: a plugin that blocks when the engine is missing (fails closed) fails the fail-open check",
          not went_through(o.result(4, 0)), short(o.result(4, 0)))
    check("[CP4-T13] teeth: a plugin that treats any truthy 'block' as a block fails the garbage-reply check",
          not went_through(o.result(5, 0)), short(o.result(5, 0)))
    check("[CP4-T18] teeth: a plugin that latches a block fails the later-ordinary-call check",
          ratchet_refusal(o.result(2, 0)) and not went_through(o.result(2, 1)), (short(o.result(2, 0)),
                                                                                short(o.result(2, 1))))
    check("[CP4-T06] teeth: a plugin that throws its own canned text instead of the engine's message fails the exact "
          "message check", refusal(o.result(3, 0)) and not refusal(o.result(3, 0), "RATCHET GATE: exact"),
          short(o.result(3, 0)))
    # the child-process stop checker
    sl = subprocess.Popen(["sleep", "30"])
    try:
        alive_before = pid_running(sl.pid)
        sl.kill()
        alive_after = not wait_until_stopped(sl.pid)   # a killed child that nobody has waited for yet is a zombie
    finally:
        sl.kill()
        sl.wait()
    check("[CP4-T14] teeth: the 'engine process is stopped' checker sees a live process as running and a killed one as "
          "stopped", alive_before is True and alive_after is False, (alive_before, alive_after))


# ------------------------------------------------------------------- round-1 fixes: bash workdir, patch headers, lsp

def case_x10():
    print("[CP4-X10] OpenCode's bash `workdir` is judged like Claude's `cd <dir> &&`")
    w = world()
    run_rel, run_abs = ".ratchet/runs/hk", P(w, ".ratchet/runs/hk")
    jq = "jq '.phase=\"done\"' state.json > t && mv t state.json"
    rows = [  # (label, command, workdir, expect block)
        ("rm state.json, relative workdir", "rm state.json", run_rel, True),
        ("rm state.json, absolute workdir", "rm state.json", run_abs, True),
        ("echo > state.json, relative workdir", "echo x > state.json", run_rel, True),
        ("echo > state.json, absolute workdir", "echo x > state.json", run_abs, True),
        ("sed -i state.json, relative workdir", "sed -i 's/a/b/' state.json", run_rel, True),
        ("jq > t && mv t state.json, relative workdir", jq, run_rel, True),
        ("jq > t && mv t state.json, absolute workdir", jq, run_abs, True),
        ("workdir with a dot-dot that lands in the run folder", "rm state.json", "src/../" + run_rel, True),
        ("cat state.json, relative workdir", "cat state.json", run_rel, False),
        ("cat state.json, absolute workdir", "cat state.json", run_abs, False),
        ("ordinary write in src (workdir src)", "echo x > app.js", "src", False),
        ("ordinary write in src (absolute workdir)", "echo x > app.js", P(w, "src"), False),
    ]
    odd = (7, None, {"a": 1}, ["x"], "", "   ")   # a workdir that is not a usable string is ignored
    calls = [oc_call("bash", {"command": c, "workdir": wd}) for _l, c, wd, _e in rows]
    for bad in odd:
        calls.append(oc_call("bash", {"command": "echo x > app.js", "workdir": bad}))
        calls.append(oc_call("bash", {"command": "rm .ratchet/runs/hk/state.json", "workdir": bad}))
    o = drive(w, [run_of(w.plugin, calls)])
    check("[CP4-X10] setup: plugin loaded with a handler and the process was clean",
          o.factory_ok() and o.has_handler() and o.clean(), (o.run(), o.problems))
    nb = na = 0
    for j, (label, cmd, wd, expect) in enumerate(rows):
        rc, reason = claude_reason(w, "Bash", {"command": f"cd {wd} && {cmd}"})
        check(f"[CP4-X10] setup: Claude's `cd {wd} && ...` for {label} is {'refused' if expect else 'allowed'}",
              rc == (2 if expect else 0) and (reason != "") == expect, (rc, reason[:120]))
        r = o.result(0, j)
        if expect:   # the text is the one Claude gives when its own shell already sits in that folder
            rc2, err2 = engine_guard(w, "claude", {"tool_name": "Bash", "tool_input": {"command": cmd},
                                                   "cwd": os.path.normpath(os.path.join(str(w.proj), wd))})[::2]
            check(f"[CP4-X10] {label} is stopped with Ratchet's reason (same text Claude gets from that folder)",
                  rc2 == 2 and ratchet_refusal(r) and refusal(r, err2.rstrip("\n")), (short(r), rc2, err2[:120]))
        else:
            check(f"[CP4-X10] {label} goes through", went_through(r), short(r))
        nb += expect
        na += not expect
    check("[CP4-X10] the table covers both verdicts", nb >= 6 and na >= 3, (nb, na))
    k = len(rows)
    for i, bad in enumerate(odd):
        check(f"[CP4-X10] workdir {bad!r} is ignored: an ordinary write goes through, a protected rm is still stopped",
              went_through(o.result(0, k + 2 * i)) and ratchet_refusal(o.result(0, k + 2 * i + 1)),
              (short(o.result(0, k + 2 * i)), short(o.result(0, k + 2 * i + 1))))


def case_x11():
    print("[CP4-X11] patch header spacing variants and the lsp tool")
    w = world()
    st = ".ratchet/runs/hk/state.json"
    calls, labels = [], []
    for what, sp in (("two spaces", "  "), ("no space", ""), ("a tab", "\t"), ("one space", " ")):
        for verb in ("Add", "Update", "Delete"):
            calls.append(oc_call("apply_patch", {"patchText": patch_text(f"*** {verb} File:{sp}{st}", "@@", "-x", "+y")}))
            labels.append((f"{verb} File with {what} naming state.json", True))
        calls.append(oc_call("apply_patch", {"patchText": patch_text("*** Update File: src/app.js", f"*** Move to:{sp}{st}",
                                                                      "@@", "-x", "+y")}))
        labels.append((f"Move to with {what} naming state.json", True))
        calls.append(oc_call("apply_patch", {"patchText": patch_text(f"*** Update File:{sp}src/app.js", "@@", "-x", "+y")}))
        labels.append((f"Update File with {what} naming src/app.js", False))
    calls.append(oc_call("apply_patch", {"patchText": patch_text(f"*** Update File:  {st}  ", "@@", "-x", "+y")}))
    labels.append(("Update File with trailing spaces naming state.json", True))
    calls.append(oc_call("lsp", {"operation": "hover", "filePath": P(w, st), "line": 1, "character": 1}))
    labels.append(("lsp hover on state.json", False))
    calls.append(oc_call("write", {"filePath": P(w, st), "content": "{}"}))
    labels.append(("write of state.json", True))
    o = drive(w, [run_of(w.plugin, calls)])
    check("[CP4-X11] setup: plugin loaded with a handler and the process was clean",
          o.factory_ok() and o.has_handler() and o.clean(), (o.run(), o.problems))
    for j, (label, expect) in enumerate(labels):
        r = o.result(0, j)
        if expect:
            check(f"[CP4-X11] {label} is stopped with Ratchet's reason", ratchet_refusal(r), short(r))
        else:
            check(f"[CP4-X11] {label} goes through", went_through(r), short(r))


def case_x12():
    print("[CP4-X12] a reply with block true but a present non-string message is damaged: the call goes through")
    w = world()
    through = [("message null", '{"block": true, "message": null}'), ("message 7", '{"block": true, "message": 7}'),
               ("message {}", '{"block": true, "message": {}}'), ("message []", '{"block": true, "message": []}'),
               ("message false", '{"block": true, "message": false}')]
    refused = [("no message", '{"block": true}', None), ("an empty message", '{"block": true, "message": ""}', None),
               ("a blank message", '{"block": true, "message": "   "}', None),
               ("a real message", '{"block": true, "message": "real reason"}', "real reason")]
    runs, copies, logs = [], [], []
    for what, text in [(a, b) for a, b in through] + [(a, b) for a, b, _ in refused]:
        stub, log = stub_printing(w, "damaged", text)
        cp, n = retargeted_plugin(w, "damaged", stub)
        copies.append(n)
        logs.append(log)
        runs.append(run_of(cp, [protected_write(w)], label=what))
    o = drive(w, runs)
    total = len(through) + len(refused)
    check("[CP4-X12] setup: copies name the engine as a JSON string literal", all(n >= 1 for n in copies), copies)
    check("[CP4-X12] setup: plugin copies loaded with a handler and the process was clean",
          all(o.factory_ok(i) and o.has_handler(i) for i in range(total)) and o.clean(), (o.problems, o.run(0)))
    for i in range(total):
        check(f"[CP4-X12] setup: stub engine {i} was really run", len(log_lines(logs[i])) >= 1, log_lines(logs[i]))
    for i, (what, _text) in enumerate(through):
        check(f"[CP4-X12] block true with {what}: the protected write resolves (damaged reply, fail open)",
              went_through(o.result(i, 0)), short(o.result(i, 0)))
    for k, (what, _text, exact) in enumerate(refused):
        i = len(through) + k
        check(f"[CP4-X12] block true with {what}: still stopped with a non-blank Error"
              + (f" that is exactly {exact!r}" if exact else ""), refusal(o.result(i, 0), exact), short(o.result(i, 0)))


# ------------------------------------------------------------------------------------------------- runner

def guarded(fn, *ids):
    """A case that crashes is reported as an error for its ids (never hides the others)."""
    global FAIL
    try:
        return fn()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        FAIL += 1
        print(f"  ✗ {fn.__name__} crashed: {type(e).__name__}: {e}")
        for i in ids:
            CRASHED[i] = f"{fn.__name__} crashed: {type(e).__name__}: {e}"
        return None


def write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = []
    for cid in PLANNED:
        rs = RESULTS.get(cid, [])
        bad = [r for r in rs if not r[0]]
        if cid in CRASHED:
            status, msg = "error", CRASHED[cid]
        elif bad:
            status, msg = "failed", f"{bad[0][1]} {bad[0][2]}".strip()
        elif rs:
            status, msg = "passed", f"{len(rs)} checks passed"
        else:
            status, msg = "failed", "no check ran for this case"
        cases.append({"id": cid, "status": status, "message": msg[:400]})
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"cases": cases}, f, indent=1)


def main():
    need_node()
    started = time.time()
    plan = ((case_t01, ("CP4-T01",)), (case_t02, ("CP4-T02",)), (case_t03, ("CP4-T03",)), (case_t04, ("CP4-T04",)),
            (case_t05, ("CP4-T05",)), (case_t06, ("CP4-T06",)), (case_t07, ("CP4-T07",)), (case_t08, ("CP4-T08",)),
            (case_t09, ("CP4-T09",)), (case_t10, ("CP4-T10",)), (case_t11, ("CP4-T11",)), (case_t12, ("CP4-T12",)),
            (case_t13, ("CP4-T13",)), (case_t14, ("CP4-T14",)), (case_t15, ("CP4-T15",)), (case_t16, ("CP4-T16",)),
            (case_t17, ("CP4-T17",)), (case_t18, ("CP4-T18",)), (case_t19, ("CP4-T19",)), (case_t20, ("CP4-T20",)),
            (case_t21, ("CP4-T21",)),
            (case_x10, ("CP4-X10",)), (case_x11, ("CP4-X11",)), (case_x12, ("CP4-X12",)),
            (case_teeth, ("CP4-T01", "CP4-T06", "CP4-T07", "CP4-T11", "CP4-T13", "CP4-T14", "CP4-T18")))
    for fn, ids in plan:
        guarded(fn, *ids)
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed ({time.time() - started:.0f} s)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
