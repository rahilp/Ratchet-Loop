#!/usr/bin/env python3
"""
OpenCode support, step 5: on OpenCode, a message the user really typed is recorded as their reply (and Ratchet's
guidance for the turn reaches the agent), while a message Ratchet's own plugin sends, a sub-agent's message, a nested
reviewer's prompt, or an `opencode run` the agent starts from the shell is never recorded and approves nothing.

    python3 tests/test_hook_opencode_prompt.py      # needs node on PATH; builds throwaway git projects

Why this file is adversarial. OpenCode calls the plugin's `chat.message` hook for EVERY user-role message, including the
ones the plugin itself sends with `client.session.prompt` (the stop re-prompt of the next step), and the hook carries no
"typed by a human" marker. A wrong check here would let Ratchet record a message the developer never typed as their
reply, and a plan or a final approval could be given without them. So two INDEPENDENT layers are each pinned alone:
  1. the plugin: a per-load random token sits on the fixed first line `[RATCHET PLUGIN TURN - NOT USER INPUT] <token>`
     of every text the plugin sends itself, and a `chat.message` whose text carries this load's token is never
     forwarded as a human message (checked with a spy `python3` that logs every engine call: "never forwarded" is
     told apart from "the engine dropped it");
  2. the engine: the payload field `ratchet_plugin_turn: true` AND the fixed first-line prefix on its own make
     `hook prompt --host opencode` record nothing (checked by calling the engine directly, with the plugin bypassed).
Child sessions (a sub-agent's prompt, looked up with `client.session.get`), nested reviewers
(RATCHET_NESTED_REVIEW=1), the `opencode run` shell route, dedup, hostile text, and "a re-prompt never restarts the stop
hook's count" are each their own case.

How a case runs. A throwaway git project (temp dir, never this repo's .ratchet/) gets a plan-submitted run `hk` and the
plugin is installed there by the public command (`ratchet.py install-hooks --host opencode`). A small node driver,
written to a separate temp folder, imports the installed plugin file under plain node, calls its exported factory with a
fake `{client, directory, worktree}` (client.session.get answers from a table, or rejects, throws, answers an error...)
and calls the `chat.message` / `tool.execute.before` hooks the way OpenCode does. The REAL engine answers inside the
project. A spy `python3` placed first on the node process's PATH appends {argv, stdin, stdout, the parent process's
name} of every engine call to a log and then runs the real python3, so a test can see exactly what the plugin forwarded
and that it started the engine with an argument list (parent `node`, never `sh -c`). What was recorded is read from the
project's .ratchet/human_log.jsonl; whether it counts is read from `ratchet.py approve-plan` (exit code and text).
Engine-only cases call `ratchet.py hook prompt|guard|stop --host ...` straight as a subprocess.

The plugin's own marked text comes from its single marked-text seam: a non-hook key on the Hooks object that OpenCode
ignores (the builder names it in the plugin's header comment). This file does not know the key's name: the driver
finds it as the function-valued key that is not a known OpenCode hook name and that, called with a reason string,
returns a string holding the fixed prefix. Contract pinned here: `seam(reason: string) -> string | Promise<string>`.

Every child process gets an ambient-proof environment (every CODEX_*, CLAUDE*, GEMINI_*, COPILOT_*, GITHUB_COPILOT_*,
OPENCODE_*, XDG_* and RATCHET_* variable removed; a throwaway HOME, RATCHET_HOME and XDG_CONFIG_HOME), so nothing here
touches the real ~/.config/opencode or this repo's .ratchet/. This file never loads ratchet.py as a module and never
reads its source text (it runs processes; the README table fact HOST_ENFORCEMENT is read by running the engine file in a
child process; README.md and schemas.md are read as plain text for [CP5-T28..T30]), so it needs no test-inventory
entry. No sleeps: a handler is awaited, with a hard cap. A missing `node` FAILS the script loudly at its start.

Teeth. Every verdict is shown to be able to fail: mutant plugins (records everything, never records, forwards the
plugin's own turns, uses a static token, records child sessions, no dedup, no guidance) are run through the same driver
and the same predicates, next to a reference plugin that passes, and each mutant is refused by the case it would break.

Where this file differs from, or narrows, the plan text:
  * [CP5-T04] "a project whose run is finished": finishing a run through the public commands needs the very reply
    recording this step builds, so the second project is a run in its building phase with no question waiting, where
    the engine also has nothing to say (an empty message).
  * [CP5-T02] needs the engine to let an OpenCode reply approve although OpenCode gives the hook no transcript file:
    today `approve-plan` answers "Couldn't confirm that reply came from the user (no transcript to check it against)".
  * [CP5-T20] and [CP5-T21] are control cases for the engine's shell rule: T20 passes before the rule is added (it
    guards against an over-broad rule), T21's first half (each launch is still refused) passes today too.
  * [CP5-T30] the plan expects an inventory entry; this file reads README.md and schemas.md as plain text only and never
    ratchet.py's source or module, so (like tests/test_hook_opencode_guard.py) it needs none: the case checks that
    this file holds none of the strings the inventory test looks for.

Each case id appears in its check names as [CP5-Txx]. When RATCHET_TEST_REPORT is set, one JSON entry per planned case
({"cases": [{"id", "status", "message"}]}) is written there.
"""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import _tmp
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
README = ROOT / "README.md"
SCHEMAS = ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md"
THIS_FILE = Path(__file__).resolve()
NEW_TEST_LINE = "python3 tests/test_hook_opencode_prompt.py"
PLANNED = [f"CP5-T{n:02d}" for n in range(1, 31)]
PASS, FAIL = 0, 0
RESULTS = {}   # case id -> [(ok, name, detail)]
CRASHED = {}   # case id -> what crashed
NODE = shutil.which("node")
PREFIX = "[RATCHET PLUGIN TURN - NOT USER INPUT]"
NOREC = "No human message recorded"
_STRIP_PREFIXES = ("CODEX_", "OPENCODE_", "XDG_", "RATCHET_", "GEMINI_", "COPILOT_", "GITHUB_COPILOT_", "CLAUDE")
UUID_SAMPLE = "123e4567-e89b-42d3-a456-426614174000"


def check(name, cond, detail=""):
    global PASS, FAIL
    m = re.match(r"\[(CP5-[A-Z]\d+)\]", name)
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


def scrubbed_env():
    return {k: v for k, v in os.environ.items() if not k.startswith(_STRIP_PREFIXES)}


def git(path, *args):
    subprocess.run(["git", *args], cwd=str(path), check=True, capture_output=True)


# ------------------------------------------------------------------------------------- scratch space and worlds

class Scratch:
    """What every case shares: a throwaway HOME / RATCHET_HOME and a work folder (driver, spy, stubs, plugin copies)."""

    def __init__(self):
        self.home = Path(_tmp.mkdtemp(prefix="ratchet-ocprompt-h-")).resolve()
        self.rh = Path(_tmp.mkdtemp(prefix="ratchet-ocprompt-r-")).resolve()
        self.work = Path(_tmp.mkdtemp(prefix="ratchet-ocprompt-w-")).resolve()
        self.spybin = self.work / "spybin"
        self.driver = self.work / "driver.mjs"
        self.counter = 0
        self.spybin.mkdir()
        shim = self.spybin / "python3"
        shim.write_text(SPY_PY.replace("__REAL__", sys.executable), encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self.driver.write_text(DRIVER_JS, encoding="utf-8")

    def next(self, stem):
        self.counter += 1
        return f"{stem}-{self.counter}"


SCRATCH = None


def scratch():
    global SCRATCH
    if SCRATCH is None:
        SCRATCH = Scratch()
    return SCRATCH


class World:
    """One throwaway git project (a copy of a template, so each case starts clean)."""

    def __init__(self, proj):
        self.proj = Path(proj).resolve()
        self.s = scratch()
        self.plugin = self.proj / ".opencode" / "plugins" / "ratchet.js"

    def env(self, **extra):
        env = scrubbed_env()
        env.update(HOME=str(self.s.home), RATCHET_HOME=str(self.s.rh), XDG_CONFIG_HOME=str(self.s.home / ".config"),
                   PYTHONDONTWRITEBYTECODE="1")
        env.update(extra)
        return env

    def sh(self, *args, stdin=None, env_extra=None, timeout=180):
        return subprocess.run([sys.executable, str(ENGINE), *args], cwd=str(self.proj), input=stdin,
                              capture_output=True, text=True, env=self.env(**(env_extra or {})), timeout=timeout)


def _checkpoints(run):
    (run / "app-scout.json").write_text("{}")
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": [{
        "id": "CP1", "title": "A", "summary": "s", "done_when": ["a"],
        "gates": {"behavior": True, "ui": False, "adversarial": True},
        "tests": {"files": ["t.test.js"], "command": "true"}}]}))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))


TEMPLATES = {}


def _must(r):
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    return r


def template(kind):
    """kind 'waiting': a plan-submitted run with `ask approve-plan` printed (the typed reply approves the plan).
    kind 'building': an approved run in its building phase with an open gate, max_stop_blocks 2, no question waiting.
    kind 'bare': a git project with no .ratchet folder. Built once, copied per case."""
    if kind in TEMPLATES:
        return TEMPLATES[kind]
    s = scratch()
    base = Path(_tmp.mkdtemp(prefix=f"ratchet-ocprompt-t-{kind}-")).resolve()
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(base, *c)
    (base / "a.js").write_text("x")
    git(base, "add", "-A")
    git(base, "commit", "-qm", "init")
    w = World(base)
    if kind != "bare":
        _must(w.sh("init", "opencode prompt", "--slug", "hk"))
        _checkpoints(base / ".ratchet" / "runs" / "hk")
        _must(w.sh("plan-submit"))
        _must(w.sh("install-hooks", "--host", "opencode"))
        _must(w.sh("ask", "approve-plan"))
        if kind == "building":
            import datetime as dt
            tp = s.work / f"{base.name}-transcript.jsonl"   # what the agent CLI writes when the user types
            tp.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "approved"},
                                      "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}) + "\n")
            _must(w.sh("hook", "prompt", "--host", "claude",
                       stdin=json.dumps({"cwd": str(base), "prompt": "approved", "transcript_path": str(tp)})))
            _must(w.sh("approve-plan"))
            _must(w.sh("config", "--set", "max_stop_blocks=2"))
    TEMPLATES[kind] = base
    return base


def fresh(kind="waiting"):
    """A new clean copy of a template, as a World."""
    src = template(kind)
    dst = Path(_tmp.mkdtemp(prefix=f"ratchet-ocprompt-p-{kind}-")).resolve()
    shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True)
    return World(dst)


# ------------------------------------------------------------------------------- spy python3 and the node driver

SPY_PY = r'''#!__REAL__
import json, os, subprocess, sys
data = sys.stdin.buffer.read()
p = subprocess.run(["__REAL__"] + sys.argv[1:], input=data, capture_output=True)
try:
    with open("/proc/%d/comm" % os.getppid()) as f:
        parent = f.read().strip()
except OSError:
    parent = ""
rec = {"argv": sys.argv[1:], "stdin": data.decode("utf-8", "replace"), "stdout": p.stdout.decode("utf-8", "replace"),
       "rc": p.returncode, "parent": parent}
with open(os.environ["OCSPY_LOG"], "a") as f:
    f.write(json.dumps(rec) + "\n")
sys.stdout.buffer.write(p.stdout)
sys.stderr.buffer.write(p.stderr)
sys.exit(p.returncode)
'''

DRIVER_JS = r"""
import { pathToFileURL } from 'node:url';
import fs from 'node:fs';

const scen = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const problems = [];
process.on('unhandledRejection', (e) => { problems.push('unhandledRejection: ' + ((e && e.message) || String(e))); });
process.on('uncaughtException', (e) => { problems.push('uncaughtException: ' + ((e && e.message) || String(e))); });
const vars = {};
let loads = 0;
const KNOWN = new Set(['event', 'config', 'tool', 'auth', 'provider', 'chat.message', 'chat.params', 'chat.headers',
  'permission.ask', 'command.execute.before', 'tool.execute.before', 'tool.execute.after', 'tool.definition',
  'shell.env', 'dispose']);

// A spec is plain JSON, with {"$js": "<expression>"} for values JSON cannot carry (undefined, huge strings) and
// {"$var": name, "path": [...]} for a value an earlier step saved (returned as the live object, never cloned).
function build(spec) {
  if (Array.isArray(spec)) return spec.map(build);
  if (spec && typeof spec === 'object') {
    if (typeof spec.$js === 'string' && Object.keys(spec).length === 1) return new Function('vars', 'return (' + spec.$js + ');')(vars);
    if ('$var' in spec) { let v = vars[spec.$var]; for (const k of (spec.path || [])) v = (v === undefined || v === null) ? undefined : v[k]; return v; }
    const o = {}; for (const k of Object.keys(spec)) o[k] = build(spec[k]); return o;
  }
  return spec;
}
function sn(v) {
  if (v === undefined) return '<undefined>';
  try { return JSON.parse(JSON.stringify(v)); } catch (e) { return '<unserializable>'; }
}
function cap(p, ms) {
  let t; const timer = new Promise((_, rej) => { t = setTimeout(() => rej(new Error('driver cap: handler did not settle in ' + ms + ' ms')), ms); });
  return Promise.race([p, timer]).finally(() => clearTimeout(t));
}
function fakeClient(run, calls) {
  const noop = (name) => async (o) => { calls.push({ fn: name, opts: sn(o) }); return { data: {} }; };
  const g = run.get || { mode: 'table', table: {} };
  const get = (opts) => {
    calls.push({ fn: 'session.get', opts: sn(opts) });
    switch (g.mode) {
      case 'throw': throw new Error('session.get threw synchronously');
      case 'reject': return Promise.reject(new Error('session.get rejected'));
      case 'error': return Promise.resolve({ error: { name: 'NotFoundError' } });
      case 'undefined': return Promise.resolve({ data: undefined });
      case 'nonobject': return Promise.resolve('not an object');
      default: {
        const id = opts && opts.path && opts.path.id;
        const t = g.table || {};
        return Promise.resolve(id in t ? t[id] : ('default' in g ? g.default : { error: { name: 'NotFoundError' } }));
      }
    }
  };
  return { app: { log: noop('app.log') }, session: { get, prompt: noop('session.prompt'), promptAsync: noop('session.promptAsync'),
    list: noop('session.list'), abort: noop('session.abort') }, tui: { showToast: noop('tui.showToast') } };
}
async function doChat(hooks, step) {
  const handler = hooks['chat.message'];
  if (typeof handler !== 'function') return { status: 'nohandler' };
  let input, output;
  try { input = build(step.input); output = build(step.output); } catch (e) { return { status: 'driver-error', message: String(e) }; }
  const isObj = output !== null && typeof output === 'object';
  const origParts = isObj ? output.parts : undefined;
  const origMessage = isObj ? output.message : undefined;
  const origFirst = Array.isArray(origParts) ? origParts[0] : undefined;
  const origFirstJson = JSON.stringify(sn(origFirst));
  const r = { partsBefore: sn(origParts), messageBefore: sn(origMessage) };
  const t0 = Date.now();
  try {
    const v = await cap(Promise.resolve(handler.call(hooks, input, output)), 60000);
    r.status = 'resolved'; r.valueIsNullish = (v === undefined || v === null);
  } catch (e) {
    r.status = 'threw';
    r.message = (e !== null && (typeof e === 'object' || typeof e === 'string') && e.message !== undefined) ? e.message : String(e);
  }
  r.partsAfter = sn(isObj ? output.parts : undefined);
  r.messageAfter = sn(isObj ? output.message : undefined);
  r.sameParts = isObj ? output.parts === origParts : true;
  r.sameMessage = isObj ? output.message === origMessage : true;
  r.sameFirst = Array.isArray(origParts) ? (origParts[0] === origFirst && JSON.stringify(sn(origParts[0])) === origFirstJson) : true;
  r.ms = Date.now() - t0;
  if (step.save) vars[step.save] = sn(isObj ? output.parts : undefined);
  if (step.saveOutput) vars[step.saveOutput] = output;
  return r;
}
async function doGuard(hooks, step) {
  const handler = hooks['tool.execute.before'];
  if (typeof handler !== 'function') return { status: 'nohandler' };
  let input, output;
  try { input = build(step.input); output = build(step.output); } catch (e) { return { status: 'driver-error', message: String(e) }; }
  const r = {};
  try {
    const v = await cap(Promise.resolve(handler.call(hooks, input, output)), 60000);
    r.status = 'resolved'; r.valueIsNullish = (v === undefined || v === null);
  } catch (e) {
    r.status = 'threw'; r.isError = e instanceof Error;
    r.message = (e !== null && (typeof e === 'object' || typeof e === 'string') && e.message !== undefined) ? e.message : String(e);
  }
  return r;
}
async function doSeam(hooks, rr, step) {
  if (!rr.seam) return { status: 'noseam' };
  let reason;
  try { reason = build(step.reason); } catch (e) { return { status: 'driver-error', message: String(e) }; }
  try {
    const v = await cap(Promise.resolve(hooks[rr.seam].call(hooks, reason)), 30000);
    if (step.save) vars[step.save] = v;
    return { status: 'resolved', text: v, type: typeof v };
  } catch (e) {
    return { status: 'threw', message: String((e && e.message) || e) };
  }
}

const out = [];
for (const run of scen.runs) {
  const calls = [];
  const rr = { label: run.label, factory: 'unknown', hookNames: [], seam: null, steps: [], calls };
  let hooks;
  try {
    const mod = await import(pathToFileURL(run.plugin).href + '?load=' + (loads++));
    if (typeof mod.RatchetPlugin !== 'function') throw new Error('no exported RatchetPlugin function');
    const ctx = { client: fakeClient(run, calls), directory: scen.dir, worktree: scen.dir, project: { id: 'p' }, serverUrl: 'http://localhost:4096' };
    hooks = await mod.RatchetPlugin(ctx);
    rr.factory = 'ok';
    rr.hookNames = (hooks && typeof hooks === 'object') ? Object.keys(hooks) : [];
    for (const k of rr.hookNames) {
      if (typeof hooks[k] !== 'function' || KNOWN.has(k) || k.startsWith('experimental.')) continue;
      try {
        const v = await cap(Promise.resolve(hooks[k].call(hooks, 'probe')), 30000);
        if (typeof v === 'string' && v.includes(scen.prefix)) { rr.seam = k; break; }
      } catch (e) { void e; }
    }
  } catch (e) {
    rr.factory = 'threw: ' + ((e && e.message) || String(e));
  }
  for (const step of run.steps) {
    if (!hooks || typeof hooks !== 'object') { rr.steps.push({ status: 'nohooks' }); continue; }
    if (step.op === 'chat') rr.steps.push(await doChat(hooks, step));
    else if (step.op === 'guard') rr.steps.push(await doGuard(hooks, step));
    else if (step.op === 'seam') rr.steps.push(await doSeam(hooks, rr, step));
    else rr.steps.push({ status: 'driver-error', message: 'unknown op ' + step.op });
  }
  out.push(rr);
}
// let a stray rejection or a late crash surface: bounded event-loop turns (not a fixed sleep)
for (let i = 0; i < 25; i++) {
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setTimeout(r, 1));
}
fs.writeFileSync(scen.out, JSON.stringify({ runs: out, problems }));
process.exit(0);
"""


class Out:
    """What one driver run reported, plus the spy log of every engine call the plugin made."""

    def __init__(self, proc, out_path, spy_path):
        self.rc, self.stdout, self.stderr = proc.returncode, proc.stdout or "", proc.stderr or ""
        self.data = None
        try:
            self.data = json.loads(Path(out_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        self.problems = (self.data or {}).get("problems") if self.data else [f"driver left no result (exit {self.rc}): "
                                                                             f"{self.stderr[-300:]}"]
        self.spy = []
        try:
            for ln in Path(spy_path).read_text(encoding="utf-8").splitlines():
                if ln.strip():
                    self.spy.append(json.loads(ln))
        except (OSError, ValueError):
            pass

    def run(self, i=0):
        runs = (self.data or {}).get("runs") or []
        return runs[i] if i < len(runs) else {"factory": "missing", "hookNames": [], "steps": [], "calls": []}

    def step(self, i=0, j=0):
        ss = self.run(i).get("steps") or []
        return ss[j] if j < len(ss) else {"status": "missing"}

    def loaded(self, i=0, hook="chat.message"):
        return self.run(i).get("factory") == "ok" and hook in (self.run(i).get("hookNames") or [])

    def seam(self, i=0):
        return self.run(i).get("seam")

    def clean(self):
        """The node process exited normally and reported no unhandled rejection or uncaught exception."""
        return self.rc == 0 and self.data is not None and self.problems == []

    def gets(self, i=0):
        return [c for c in (self.run(i).get("calls") or []) if c.get("fn") == "session.get"]

    def prompt_calls(self):
        """Every `hook prompt` engine call the plugin made: (argv, parsed stdin payload, record)."""
        res = []
        for rec in self.spy:
            argv = rec.get("argv") or []
            if "hook" in argv and "prompt" in argv:
                try:
                    payload = json.loads(rec.get("stdin") or "")
                except ValueError:
                    payload = None
                res.append((argv, payload, rec))
        return res

    def prompts_sent(self):
        return [p.get("prompt") if isinstance(p, dict) else None for _a, p, _r in self.prompt_calls()]


def drive(w, runs, env_extra=None, timeout=240):
    """Load each run's plugin under node and replay its steps; the spy python3 is first on PATH."""
    need_node()
    s = w.s
    n = s.next("scenario")
    scen, out_path, spy_path = s.work / f"{n}.json", s.work / f"{n}.out.json", s.work / f"{n}.spy.log"
    scen.write_text(json.dumps({"dir": str(w.proj), "out": str(out_path), "prefix": PREFIX, "runs": runs}), encoding="utf-8")
    env = w.env(PATH=str(s.spybin) + os.pathsep + os.environ.get("PATH", ""), OCSPY_LOG=str(spy_path), **(env_extra or {}))
    p = subprocess.run([NODE, str(s.driver), str(scen)], cwd=str(s.work), env=env, capture_output=True, text=True,
                       timeout=timeout)
    return Out(p, out_path, spy_path)


TOP = {"ses_top": {"data": {"id": "ses_top"}},
       "ses_child": {"data": {"id": "ses_child", "parentID": "ses_top"}}}


def run_of(plugin, steps, label="run", get=None):
    return {"label": label, "plugin": str(plugin), "steps": steps, "get": get or {"mode": "table", "table": TOP}}


_pid = [0]


def part(text, sid="ses_top", mid="msg_1", **kw):
    """One text part as OpenCode builds it: {id, sessionID, messageID, type:'text', text}."""
    _pid[0] += 1
    p = {"id": f"prt_{_pid[0]}", "type": "text", "text": text}
    if sid is not None:
        p["sessionID"] = sid
    if mid is not None:
        p["messageID"] = mid
    p.update(kw)
    return p


def chat(parts, sid="ses_top", mid="msg_1", **kw):
    """One chat.message call exactly as OpenCode makes it: hook(input{sessionID, messageID?, agent, model}, output{message, parts})."""
    inp = {"agent": "build", "model": {"providerID": "p", "modelID": "m"}}
    msg = {"role": "user", "time": {"created": 1}}
    if sid is not None:
        inp["sessionID"] = sid
        msg["sessionID"] = sid
    if mid is not None:
        inp["messageID"] = mid
        msg["id"] = mid
    return {"op": "chat", "input": inp, "output": {"message": msg, "parts": parts}, **kw}


def typed(text, sid="ses_top", mid="msg_1", **kw):
    return chat([part(text, sid, mid)], sid, mid, **kw)


def seam_step(reason, save=None):
    return {"op": "seam", "reason": reason, **({"save": save} if save else {})}


def var(name, *path):
    return {"$var": name, "path": list(path)}


def js(expr):
    return {"$js": expr}


def guard_step(tool, args, sid="ses_top", cid="call_1"):
    return {"op": "guard", "input": {"tool": tool, "sessionID": sid, "callID": cid}, "output": {"args": args}}


# ------------------------------------------------------------------------------------------- engine-side helpers

def log_entries(w):
    p = w.proj / ".ratchet" / "human_log.jsonl"
    try:
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError):
        return []


def prompts_logged(w):
    return [e.get("prompt") for e in log_entries(w)]


def payload(w, text, sid="ses_top", **kw):
    return {"hook_event_name": "chat.message", "session_id": sid, "cwd": str(w.proj), "prompt": text, **kw}


def engine_prompt(w, pl, env_extra=None):
    """`ratchet.py hook prompt --host opencode` run directly: (exit code, parsed reply or None, stdout)."""
    r = w.sh("hook", "prompt", "--host", "opencode", stdin=json.dumps(pl), env_extra=env_extra)
    try:
        reply = json.loads(r.stdout)
    except ValueError:
        reply = None
    return r.returncode, reply, r.stdout


def one_json_reply(rc, reply, stdout):
    return rc == 0 and isinstance(reply, dict) and len([x for x in stdout.splitlines() if x.strip()]) == 1


def approve_plan(w):
    r = w.sh("approve-plan")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def run_state(w):
    try:
        return json.loads((w.proj / ".ratchet" / "runs" / "hk" / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def not_approved(w):
    """approve-plan refuses because no human message was recorded (a recorded one would answer differently)."""
    rc, text = approve_plan(w)
    return rc != 0 and NOREC in text, (rc, text[-200:])


def engine_guard(w, host, tool, command, session="ses_top"):
    """`ratchet.py hook guard --host <host>` for one shell call: (blocked, message)."""
    pl = {"tool_name": tool, "tool_input": {"command": command}, "cwd": str(w.proj)}
    if host == "opencode":
        pl["session_id"] = session
    r = w.sh("hook", "guard", "--host", host, stdin=json.dumps(pl))
    if host == "opencode":
        try:
            reply = json.loads(r.stdout)
        except ValueError:
            return None, (r.stdout + r.stderr)
        if not isinstance(reply, dict):
            return None, r.stdout
        return reply.get("block") is True, str(reply.get("message", ""))
    return r.returncode == 2, (r.stderr or "").strip()


# ------------------------------------------------------------------------------------- shared predicates (teeth below)

def first_line(t):
    return t.split("\n", 1)[0] if isinstance(t, str) else ""


def marker_token(t):
    """The per-load token on the first line of a plugin-marked text, or None when the text is not marked that way."""
    fl = first_line(t)
    if not fl.startswith(PREFIX) or len(fl) > 200:
        return None
    toks = re.findall(r"[0-9A-Za-z-]{20,}", fl[len(PREFIX):])
    return max(toks, key=len) if toks else None


def marked_ok(t, reason=None):
    """A text the plugin sends itself: the fixed prefix and a token (20+ chars) on the first line, the reason after."""
    if not isinstance(t, str) or marker_token(t) is None:
        return False
    rest = t.split("\n", 1)[1] if "\n" in t else ""
    return reason is None or reason in rest


def parts_unchanged(r):
    return (isinstance(r, dict) and r.get("status") == "resolved" and r.get("partsAfter") == r.get("partsBefore")
            and r.get("sameParts") is True and r.get("sameFirst") is True)


def guidance_appended(r, sid, mid):
    """The step appended exactly one synthetic text part AFTER the original parts, in place; returns that part or None."""
    if not (isinstance(r, dict) and r.get("status") == "resolved" and r.get("sameParts") is True
            and r.get("sameFirst") is True and r.get("sameMessage") is True):
        return None
    before, after = r.get("partsBefore"), r.get("partsAfter")
    if not (isinstance(before, list) and isinstance(after, list) and len(after) == len(before) + 1
            and after[:len(before)] == before):
        return None
    g = after[-1]
    ok = (isinstance(g, dict) and g.get("type") == "text" and g.get("synthetic") is True
          and isinstance(g.get("id"), str) and g["id"] != "" and g.get("sessionID") == sid
          and g.get("messageID") == mid and isinstance(g.get("text"), str) and g["text"] != "")
    return g if ok else None


def forwarded_marker(o):
    """Did the plugin hand any text carrying the fixed prefix to the engine?"""
    return any(isinstance(p, str) and PREFIX in p for p in o.prompts_sent())


def short(x):
    return json.dumps(x)[:260] if isinstance(x, (dict, list)) else repr(x)[:260]


# ------------------------------------------------------------------------------------------------------ the cases

def case_t01():
    print("[CP5-T01] a message the user types is recorded as their reply")
    w = fresh()
    o = drive(w, [run_of(w.plugin, [typed("approved", mid="msg_1")])])
    r = o.step()
    check("[CP5-T01] setup: the installed plugin loads under node and exposes chat.message",
          o.loaded() and o.clean(), (o.run().get("factory"), o.run().get("hookNames"), o.problems))
    check("[CP5-T01] the handler resolves", r.get("status") == "resolved", short(r))
    ents = log_entries(w)
    check("[CP5-T01] human_log.jsonl gains exactly one entry: prompt 'approved', host 'opencode', session 'ses_top'",
          len(ents) == 1 and ents[0].get("prompt") == "approved" and ents[0].get("host") == "opencode"
          and ents[0].get("session") == "ses_top", ents)
    calls = o.prompt_calls()
    check("[CP5-T01] exactly one `hook prompt --host opencode` engine call was made", len(calls) == 1
          and calls[0][0][1:] == ["hook", "prompt", "--host", "opencode"], [c[0] for c in calls])
    pl = calls[0][1] if calls else None
    check("[CP5-T01] its stdin JSON carries prompt 'approved', session_id 'ses_top' and the project cwd",
          isinstance(pl, dict) and pl.get("prompt") == "approved" and pl.get("session_id") == "ses_top"
          and pl.get("cwd") == str(w.proj), pl)
    check("[CP5-T01] a typed message is never flagged as the plugin's own (no ratchet_plugin_turn true)",
          isinstance(pl, dict) and pl.get("ratchet_plugin_turn") is not True, pl)
    check("[CP5-T01] the engine was started with an argument list (parent process is node, never a shell)",
          len(calls) == 1 and calls[0][2].get("parent", "").startswith("node"), [c[2].get("parent") for c in calls])
    odd_dir = "stub dir ; echo $HOME 'q' x"   # a shell string would split this path apart and never reach the stub
    stub, slog = stub_engine(w, "t01-argv", "note(argv=sys.argv, stdin=sys.stdin.read())\n"
                             "sys.stdout.write(json.dumps({'block': False, 'message': ''}))", dirname=odd_dir)
    cp, n = retargeted_plugin(w, "t01-argv", stub)
    o2 = drive(w, [run_of(cp, [typed("approved", mid="msg_s")])])
    seen = log_lines(slog)
    try:
        said = json.loads(seen[0]["stdin"]).get("prompt") if seen else None
    except (ValueError, KeyError, AttributeError):
        said = None
    check("[CP5-T01] an engine path holding spaces, a semicolon, $HOME and quotes still reaches the engine whole, as "
          "separate arguments (never a shell command line)", n >= 1 and o2.clean() and len(seen) == 1
          and seen[0].get("argv") == [str(stub), "hook", "prompt", "--host", "opencode"] and said == "approved",
          (n, seen, o2.problems))


def case_t02():
    print("[CP5-T02] a typed 'approved' really approves the plan on OpenCode")
    w = fresh()
    rc0, text0 = approve_plan(w)
    check("[CP5-T02] control: approve-plan BEFORE any message was typed exits non-zero with 'No human message recorded'",
          rc0 != 0 and NOREC in text0, (rc0, text0[-200:]))
    o = drive(w, [run_of(w.plugin, [typed("approved")])])
    check("[CP5-T02] setup: plugin loaded with chat.message", o.loaded() and o.clean(), (o.run(), o.problems))
    rc, text = approve_plan(w)
    check("[CP5-T02] approve-plan now exits 0 (the typed reply counts although OpenCode gives the hook no transcript "
          "file)", rc == 0, (rc, text[-300:]))
    check("[CP5-T02] the plan is approved (the run left awaiting_plan_approval)",
          run_state(w).get("phase") not in (None, "awaiting_plan_approval", "planning"), run_state(w).get("phase"))


def case_t03():
    print("[CP5-T03] Ratchet's guidance for the turn reaches the agent as an added message part")
    w = fresh()
    o = drive(w, [run_of(w.plugin, [typed("approved", mid="msg_g")])])
    r = o.step()
    check("[CP5-T03] setup: plugin loaded with chat.message", o.loaded() and o.clean(), (o.run(), o.problems))
    g = guidance_appended(r, "ses_top", "msg_g")
    check("[CP5-T03] exactly one extra part, appended AFTER the original, in place: type text, synthetic true, a "
          "non-empty id, the input session, the message id; original part and message object untouched",
          g is not None, short(r))
    calls = o.prompt_calls()
    reply_msg = None
    if calls:
        try:
            reply_msg = json.loads(calls[0][2].get("stdout") or "").get("message")
        except (ValueError, AttributeError):
            pass
    check("[CP5-T03] the part's text is exactly the engine's reply message, and that message is not empty",
          g is not None and isinstance(reply_msg, str) and reply_msg != "" and g.get("text") == reply_msg,
          (reply_msg, g))
    check("[CP5-T03] the guidance names the waiting question (approve-plan) so the agent can act on it",
          g is not None and "approve-plan" in g.get("text", ""), g)


def case_t04():
    print("[CP5-T04] no guidance means no extra part")
    wb = fresh("bare")
    wk = fresh("building")
    o = drive(wb, [run_of(wk.plugin, [typed("hello there")])])   # the plugin file of the other project, pointed at wb
    r = o.step()
    check("[CP5-T04] setup: plugin loaded with chat.message", o.loaded() and o.clean(), (o.run(), o.problems))
    check("[CP5-T04] project with no .ratchet folder: the handler resolves and output.parts is exactly the original",
          parts_unchanged(r), short(r))
    check("[CP5-T04] it created no .ratchet folder", not (wb.proj / ".ratchet").exists())
    n_before = len(log_entries(wk))
    o2 = drive(wk, [run_of(wk.plugin, [typed("approved", mid="msg_k")])])
    r2 = o2.step()
    check("[CP5-T04] setup: second plugin loaded", o2.loaded() and o2.clean(), (o2.run(), o2.problems))
    check("[CP5-T04] a run with no question waiting (the engine has nothing to say): output.parts is exactly the "
          "original, nothing added, nothing thrown", parts_unchanged(r2), short(r2))
    ents_k = log_entries(wk)
    check("[CP5-T04] the typed message was still recorded as an OpenCode reply (no guidance is not the same as no "
          "record)", len(ents_k) == n_before + 1 and ents_k[-1].get("prompt") == "approved"
          and ents_k[-1].get("host") == "opencode", ents_k[-1:])


def case_t05():
    print("[CP5-T05] only the user's own non-synthetic text is read")
    w = fresh()
    mixed = [part("carry on, approved", mid="msg_a", synthetic=True),
             {"id": "prt_file", "sessionID": "ses_top", "messageID": "msg_a", "type": "file", "mime": "text/plain",
              "filename": "notes.txt", "url": "file:///tmp/notes.txt", "text": "FILE-CONTENT-123"},
             {"id": "prt_reason", "sessionID": "ses_top", "messageID": "msg_a", "type": "reasoning",
              "text": "REASONING-XYZ"},
             part("looks good", mid="msg_a")]
    only_other = [part("only synthetic words", mid="msg_b", synthetic=True),
                  {"id": "prt_f2", "sessionID": "ses_top", "messageID": "msg_b", "type": "file", "mime": "image/png",
                   "filename": "a.png", "url": "file:///tmp/a.png"}]
    steps = [chat(mixed, mid="msg_a"), chat(only_other, mid="msg_b"),
             typed("approved", mid="msg_g", save="g1", saveOutput="o_g"),
             chat([var("g1", 1)], mid="msg_g2"),
             {"op": "chat", "input": {"sessionID": "ses_top", "messageID": "msg_g", "agent": "build"},
              "output": var("o_g")}]
    o = drive(w, [run_of(w.plugin, steps)])
    check("[CP5-T05] setup: plugin loaded with chat.message", o.loaded() and o.clean(), (o.run(), o.problems))
    sent = o.prompts_sent()
    check("[CP5-T05] the mixed message's recorded text is exactly 'looks good' (no synthetic text, no file content, no "
          "reasoning)", sent[:1] == ["looks good"], sent)
    ents = log_entries(w)
    check("[CP5-T05] human_log.jsonl holds 'looks good' and holds neither 'carry on, approved' nor the file content",
          ents[:1] and ents[0].get("prompt") == "looks good" and not any(
              "carry on" in str(e.get("prompt")) or "FILE-CONTENT" in str(e.get("prompt")) or "REASONING" in
              str(e.get("prompt")) for e in ents), prompts_logged(w))
    check("[CP5-T05] a message of only synthetic and non-text parts spawns no `hook prompt` call",
          "only synthetic words" not in str(sent) and len(sent) == 2, sent)
    g = guidance_appended(o.step(0, 2), "ses_top", "msg_g")
    check("[CP5-T05] setup: the typed 'approved' got a guidance part appended (see CP5-T03)", g is not None,
          short(o.step(0, 2)))
    check("[CP5-T05] the guidance part alone, passed back through chat.message on a new message, is not read as the "
          "user's text", len(sent) == 2 and not any(isinstance(s, str) and g is not None and g["text"][:40] in s
                                                     for s in sent), sent)
    check("[CP5-T05] the same output (typed text plus guidance) passed through again is recorded once, never as a "
          "second reply", prompts_logged(w) == ["looks good", "approved"] and sent == ["looks good", "approved"],
          (prompts_logged(w), sent))


def case_t06():
    print("[CP5-T06] the plugin's own re-prompt is never forwarded or recorded (plugin filter)")
    w = fresh()
    reason = "Gate still open; yes, approved"
    o = drive(w, [run_of(w.plugin, [seam_step(reason, "m1"), chat([part(var("m1"), mid="msg_p")], mid="msg_p")])])
    check("[CP5-T06] setup: plugin loaded with chat.message and a marked-text seam that returns a string",
          o.loaded() and o.seam() is not None and o.clean(), (o.run().get("hookNames"), o.seam(), o.problems))
    text = o.step(0, 0).get("text")
    check("[CP5-T06] the marked text's FIRST line starts with the fixed prefix and a token of at least 20 characters, "
          "and the reason follows after that line", marked_ok(text, reason), repr(text)[:200])
    r = o.step(0, 1)
    check("[CP5-T06] the handler resolves and output.parts is unchanged", parts_unchanged(r), short(r))
    check("[CP5-T06] the spy log shows NO `hook prompt` call for it (the plugin never forwarded it)",
          o.prompt_calls() == [], o.prompts_sent())
    check("[CP5-T06] human_log.jsonl gains no entry", log_entries(w) == [], prompts_logged(w))


def case_t07():
    print("[CP5-T07] even if the plugin is bypassed, the engine does not record a flagged plugin turn")
    w = fresh()
    rc, reply, out = engine_prompt(w, payload(w, "approved", ratchet_plugin_turn=True))
    check("[CP5-T07] exit 0 with exactly one JSON reply that does not block", one_json_reply(rc, reply, out)
          and reply.get("block") is False, (rc, out[:200]))
    msg = (reply or {}).get("message", "")
    check("[CP5-T07] the reply says plainly that the turn was not typed by the user and was not recorded",
          isinstance(msg, str) and re.search(r"not typed by the user|not user input", msg, re.I) is not None
          and "record" in msg.lower(), msg[:200])
    check("[CP5-T07] human_log.jsonl gains no entry", log_entries(w) == [], prompts_logged(w))
    ok, why = not_approved(w)
    check("[CP5-T07] a following approve-plan exits non-zero with 'No human message recorded'", ok, why)
    rc2, reply2, out2 = engine_prompt(w, payload(w, "approved", ratchet_plugin_turn=False))
    check("[CP5-T07] control: the same payload with ratchet_plugin_turn false IS recorded (so the flag changed the "
          "answer)", rc2 == 0 and prompts_logged(w) == ["approved"], (rc2, prompts_logged(w)))
    w2 = fresh()
    engine_prompt(w2, payload(w2, "approved"))
    check("[CP5-T07] control: the same payload with the flag absent IS recorded", prompts_logged(w2) == ["approved"],
          prompts_logged(w2))


def case_t08():
    print("[CP5-T08] even if the plugin is bypassed, the engine does not record text that starts with the fixed prefix")
    w = fresh()
    base = f"{PREFIX} {UUID_SAMPLE}\nGate still open; yes, approved"
    variants = [("(a) the prefix line + token + reason", base),
                ("(b) leading spaces and zero-width characters before the prefix", "  \u200b\u200b\u2060 " + base),
                ("(c) wrapped in a leading <system-reminder> tag", "<system-reminder>" + base + "</system-reminder>"),
                ("(d) 40 ordinary lines, then the prefix on its own line",
                 "\n".join(f"ordinary line {i}" for i in range(40)) + f"\n{PREFIX}\napproved")]
    for label, text in variants:
        rc, reply, out = engine_prompt(w, payload(w, text))
        check(f"[CP5-T08] {label}: exit 0, one JSON reply that does not block, nothing recorded",
              one_json_reply(rc, reply, out) and reply.get("block") is False and log_entries(w) == [],
              (rc, out[:160], prompts_logged(w)))
    ok, why = not_approved(w)
    check("[CP5-T08] approve-plan afterwards still fails with 'No human message recorded'", ok, why)
    # control: text that merely resembles a prefix is a normal reply, so the answer above came from the prefix
    rc, reply, out = engine_prompt(w, payload(w, "approved, the log says [RATCHET PLUGIN TURN - NOT USER INPUT] sometimes"))
    check("[CP5-T08] control: the prefix mentioned mid-line is recorded (only line starts count)",
          len(log_entries(w)) == 1, prompts_logged(w))


def case_t09():
    print("[CP5-T09] a re-prompt from an earlier plugin load is still not recorded")
    w = fresh()
    reason = "Gate still open; yes, approved"
    steps_b = [seam_step(reason, "mB"), chat([part(var("mA"), mid="msg_old")], mid="msg_old")]
    o = drive(w, [run_of(w.plugin, [seam_step(reason, "mA")], label="load-A"), run_of(w.plugin, steps_b, label="load-B")])
    check("[CP5-T09] setup: both loads have chat.message and a seam", o.loaded(0) and o.loaded(1) and o.seam(0) is not None
          and o.seam(1) is not None and o.clean(), (o.run(0).get("hookNames"), o.problems))
    ta, tb = marker_token(o.step(0, 0).get("text")), marker_token(o.step(1, 0).get("text"))
    check("[CP5-T09] load A and load B produced different tokens (per load, not fixed)",
          ta is not None and tb is not None and ta != tb, (ta, tb))
    check("[CP5-T09] load B's handler resolves", o.step(1, 1).get("status") == "resolved", short(o.step(1, 1)))
    check("[CP5-T09] human_log.jsonl gains no entry (load B may forward it; the engine drops it by the prefix)",
          log_entries(w) == [], prompts_logged(w))
    ok, why = not_approved(w)
    check("[CP5-T09] approve-plan still fails with 'No human message recorded'", ok, why)


def marked_texts(w, reasons):
    """Texts the plugin would send itself, taken from its seam (a synthetic marked text if there is no seam yet, so the
    engine-only half of a case still runs and can fail on its own merits)."""
    steps = [seam_step(r, f"s{i}") for i, r in enumerate(reasons)]
    o = drive(w, [run_of(w.plugin, steps)])
    texts = []
    for i, r in enumerate(reasons):
        t = o.step(0, i).get("text")
        texts.append(t if isinstance(t, str) else f"{PREFIX} {UUID_SAMPLE}\n{r}")
    return o, texts


def case_t10():
    print("[CP5-T10] a history that holds only re-prompts can never approve the plan")
    w = fresh()
    reasons = ["approved", "yes, approve the plan"]
    o0, texts = marked_texts(w, reasons)
    steps = [chat([part(t, mid=f"msg_p{i}")], mid=f"msg_p{i}") for i, t in enumerate(texts)]
    o = drive(w, [run_of(w.plugin, steps)])
    check("[CP5-T10] setup: plugin loaded with chat.message and a seam", o.loaded() and o0.seam() is not None
          and o.clean(), (o.run().get("hookNames"), o0.seam(), o.problems))
    for t in texts:   # straight to the engine: with the flag, and with the prefix alone
        engine_prompt(w, payload(w, t, ratchet_plugin_turn=True))
        engine_prompt(w, payload(w, t))
    ok, why = not_approved(w)
    check("[CP5-T10] approve-plan exits non-zero and says 'No human message recorded'", ok, why)
    check("[CP5-T10] the plan is still unapproved", run_state(w).get("phase") == "awaiting_plan_approval",
          run_state(w).get("phase"))
    check("[CP5-T10] no human_log.jsonl entry exists (plugin route and both engine routes)", log_entries(w) == [],
          prompts_logged(w))


def stop_answer(w, sid="ses_top"):
    r = w.sh("hook", "stop", "--host", "opencode", stdin=json.dumps({"hook_event_name": "session.idle",
                                                                      "session_id": sid, "cwd": str(w.proj)}))
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {"unparsed": r.stdout[-200:], "rc": r.returncode}


def capped(w):
    """A building project whose stop hook has refused twice (max_stop_blocks 2): the cap is reached."""
    a, b = stop_answer(w), stop_answer(w)
    return a, b


def case_t11():
    print("[CP5-T11] a re-prompt never restarts the stop hook's count; a typed message does")
    reason = "Gate still open; yes, approved"
    p1, p1b, p2, p3 = fresh("building"), fresh("building"), fresh("building"), fresh("building")
    firsts = [capped(x) for x in (p1, p1b, p2, p3)]
    check("[CP5-T11] setup: in all four copies the first two stops were refused (the cap of 2 is reached)",
          all(a.get("block") is True and b.get("block") is True for a, b in firsts), firsts)
    o = drive(p1, [run_of(p1.plugin, [seam_step(reason, "m1"), chat([part(var("m1"), mid="msg_p")], mid="msg_p")])])
    t = o.step(0, 0).get("text")
    text = t if isinstance(t, str) else f"{PREFIX} {UUID_SAMPLE}\n{reason}"
    check("[CP5-T11] setup: the plugin loaded with chat.message and a seam", o.loaded() and o.seam() is not None
          and o.clean(), (o.run().get("hookNames"), o.seam(), o.problems))
    engine_prompt(p1b, payload(p1b, text, ratchet_plugin_turn=True))
    o3 = drive(p3, [run_of(p3.plugin, [typed("keep going please", mid="msg_t")])])
    ans = {n: stop_answer(x) for n, x in (("plugin re-prompt", p1), ("engine, flagged re-prompt", p1b),
                                           ("no message", p2), ("typed message", p3))}
    check("[CP5-T11] setup: the control (no message) is the safety valve: the stop is let through, not blocked",
          ans["no message"].get("block") is False and "safety valve" in str(ans["no message"].get("message")),
          ans["no message"])
    check("[CP5-T11] a re-prompt through the plugin changes nothing: the same stop answer as with no message",
          ans["plugin re-prompt"] == ans["no message"], (ans["plugin re-prompt"], ans["no message"]))
    check("[CP5-T11] a flagged re-prompt sent straight to the engine changes nothing either (it cannot reset its own "
          "cap, so a re-prompt loop stays capped)", ans["engine, flagged re-prompt"] == ans["no message"],
          (ans["engine, flagged re-prompt"], ans["no message"]))
    check("[CP5-T11] a TYPED message does restart the count: the stop is blocked again (a different answer)",
          o3.loaded() and ans["typed message"].get("block") is True and ans["typed message"] != ans["no message"],
          (o3.run().get("hookNames"), ans["typed message"]))


def case_t12():
    print("[CP5-T12] a human who types Ratchet's marker is not recorded (safe side)")
    w = fresh()
    a = f"{PREFIX} approved"
    b = f"approved\n{PREFIX} please"
    o = drive(w, [run_of(w.plugin, [typed(a, mid="msg_a"), typed(b, mid="msg_b")])])
    check("[CP5-T12] setup: plugin loaded with chat.message", o.loaded() and o.clean(), (o.run(), o.problems))
    check("[CP5-T12] nothing is recorded for either typed text", log_entries(w) == [], prompts_logged(w))
    ok, why = not_approved(w)
    check("[CP5-T12] approve-plan fails with 'No human message recorded'", ok, why)
    for j, label in ((0, "prefix first"), (1, "prefix on the second line")):
        g = guidance_appended(o.step(0, j), "ses_top", "msg_a" if j == 0 else "msg_b")
        check(f"[CP5-T12] {label}: the agent is told (guidance part) to ask the user to send it again",
              g is not None and re.search(r"send it again", g.get("text", ""), re.I) is not None, short(o.step(0, j)))
    rc, reply, out = engine_prompt(w, payload(w, a))
    check("[CP5-T12] the engine's own reply tells the agent to ask the user to send it again",
          one_json_reply(rc, reply, out) and reply.get("block") is False
          and re.search(r"send it again", reply.get("message", ""), re.I) is not None, out[:240])


def case_t13():
    print("[CP5-T13] a normal message that only mentions the marker words is still recorded in full")
    w = fresh()
    texts = ["approved. btw why does it say [RATCHET PLUGIN TURN - NOT USER INPUT] in the log?",
             "approved, ratchet_plugin_turn is a field I saw",
             "approved [RATCHET PLUGIN TURN",
             f"approved {UUID_SAMPLE}"]
    steps = [typed(t, mid=f"msg_n{i}") for i, t in enumerate(texts)]
    o = drive(w, [run_of(w.plugin, steps)])
    check("[CP5-T13] setup: plugin loaded with chat.message", o.loaded() and o.clean(), (o.run(), o.problems))
    check("[CP5-T13] every one is recorded verbatim (none dropped by the plugin or by the engine)",
          prompts_logged(w) == texts, prompts_logged(w))
    check("[CP5-T13] every one was forwarded to the engine (the plugin's drop is keyed on its own token only)",
          o.prompts_sent() == texts, o.prompts_sent())


def case_t14():
    print("[CP5-T14] a sub-agent's prompt (child session) is not recorded")
    w = fresh()
    o = drive(w, [run_of(w.plugin, [typed("approved", sid="ses_child", mid="msg_c")])])
    r = o.step()
    check("[CP5-T14] setup: plugin loaded with chat.message", o.loaded() and o.clean(), (o.run(), o.problems))
    check("[CP5-T14] the handler resolves and output.parts is unchanged", parts_unchanged(r), short(r))
    check("[CP5-T14] human_log.jsonl gains no entry", log_entries(w) == [], prompts_logged(w))
    ok, why = not_approved(w)
    check("[CP5-T14] approve-plan still fails with 'No human message recorded'", ok, why)
    gets = o.gets()
    ids = [((json.loads(g["opts"]) if isinstance(g.get("opts"), str) else g.get("opts")) or {}) for g in gets]
    check("[CP5-T14] the session lookup was made for the input's sessionID (path.id 'ses_child')",
          any(isinstance(i, dict) and (i.get("path") or {}).get("id") == "ses_child" for i in ids), gets)


def case_t15():
    print("[CP5-T15] if the session cannot be looked up, nothing is recorded but the message is never broken")
    w = fresh()
    modes = [("rejects", {"mode": "reject"}), ("throws synchronously", {"mode": "throw"}),
             ("answers {error}", {"mode": "error"}), ("answers {data: undefined}", {"mode": "undefined"}),
             ("answers a non-object", {"mode": "nonobject"})]
    runs = [run_of(w.plugin, [typed(f"approved-{i}", mid=f"msg_f{i}")], label=lbl, get=g)
            for i, (lbl, g) in enumerate(modes)]
    o = drive(w, runs)
    check("[CP5-T15] setup: every load has chat.message and the node process was clean",
          all(o.loaded(i) for i in range(len(modes))) and o.clean(), (o.problems, o.run(0).get("hookNames")))
    for i, (lbl, _g) in enumerate(modes):
        r = o.step(i, 0)
        check(f"[CP5-T15] session.get {lbl}: the handler resolves and output.parts is unchanged", parts_unchanged(r),
              short(r))
    check("[CP5-T15] human_log.jsonl gains no entry in any variant (an unknown session is not trusted as the user's)",
          log_entries(w) == [], prompts_logged(w))


def case_t16():
    print("[CP5-T16] a top-level session with an empty parentID is treated as the user's chat")
    w = fresh()
    variants = [("no parentID", {"data": {"id": "ses_top"}}), ("parentID null", {"data": {"parentID": None}}),
                ("parentID ''", {"data": {"parentID": ""}})]
    runs = [run_of(w.plugin, [typed(f"ok-{i}", mid=f"msg_t{i}")], label=lbl,
                   get={"mode": "table", "table": {"ses_top": resp}}) for i, (lbl, resp) in enumerate(variants)]
    o = drive(w, runs)
    check("[CP5-T16] setup: every load has chat.message and the node process was clean",
          all(o.loaded(i) for i in range(3)) and o.clean(), (o.problems, o.run(0).get("hookNames")))
    ents = log_entries(w)
    check("[CP5-T16] each typed message is recorded exactly once (only a real parent id excludes a session)",
          [e.get("prompt") for e in ents] == ["ok-0", "ok-1", "ok-2"] and all(e.get("session") == "ses_top" for e in ents),
          ents)


def case_t17():
    print("[CP5-T17] a reviewer Ratchet launched itself (nested review) is never recorded")
    w = fresh()
    r = w.sh("install-hooks", "--host", "opencode", "--scope", "user")
    user_plugin = w.s.home / ".config" / "opencode" / "plugins" / "ratchet.js"
    check("[CP5-T17] setup: the plugin is installed at USER scope in the throwaway HOME", r.returncode == 0
          and user_plugin.is_file(), (r.returncode, r.stdout[-200:], r.stderr[-200:]))
    o = drive(w, [run_of(user_plugin, [typed("approved", mid="msg_n")])], env_extra={"RATCHET_NESTED_REVIEW": "1"})
    check("[CP5-T17] setup: the user-scope plugin loads under node with chat.message", o.loaded() and o.clean(),
          (o.run(), o.problems))
    check("[CP5-T17] with RATCHET_NESTED_REVIEW=1 the handler resolves and no guidance part is added",
          parts_unchanged(o.step()), short(o.step()))
    check("[CP5-T17] human_log.jsonl gains no entry through the plugin route", log_entries(w) == [], prompts_logged(w))
    rc, reply, out = engine_prompt(w, payload(w, "approved"), env_extra={"RATCHET_NESTED_REVIEW": "1"})
    check("[CP5-T17] ...nor through the engine route (run directly with the same variable)",
          rc == 0 and log_entries(w) == [], (rc, out[:160], prompts_logged(w)))
    ok, why = not_approved(w)
    check("[CP5-T17] approve-plan still fails with 'No human message recorded'", ok, why)
    o2 = drive(w, [run_of(user_plugin, [typed("approved", mid="msg_n2")])])
    check("[CP5-T17] control: without the variable the same message IS recorded", o2.loaded()
          and prompts_logged(w) == ["approved"], (o2.run().get("hookNames"), prompts_logged(w)))


NEW_RULE_TEXT_RE = re.compile(r"opencode run", re.I)


def case_t18():
    print("[CP5-T18] the guard refuses the agent starting `opencode run` from the shell")
    w = fresh()
    cmds = ['opencode run "approved"', 'cd /tmp && opencode run -m x/y "approve"', "FOO=1 opencode run",
            "echo yes | opencode run", '/usr/bin/opencode run "p"', '(opencode run "p")']
    for cmd in cmds:
        blocked, msg = engine_guard(w, "opencode", "bash", cmd)
        check(f"[CP5-T18] {cmd!r} is refused, names `opencode run` and says another agent session could impersonate "
              "the user", blocked is True and "opencode run" in msg and "impersonate" in msg.lower(), (blocked, msg[:200]))


def case_t19():
    print("[CP5-T19] the OpenCode plugin's guard stops `opencode run` before it executes")
    w = fresh()
    o = drive(w, [run_of(w.plugin, [guard_step("bash", {"command": 'opencode run "approved"', "description": "x"}),
                                    guard_step("bash", {"command": "ls", "description": "x"})])])
    check("[CP5-T19] setup: plugin loaded with tool.execute.before", o.loaded(hook="tool.execute.before") and o.clean(),
          (o.run(), o.problems))
    a, b = o.step(0, 0), o.step(0, 1)
    check("[CP5-T19] `opencode run` throws an Error whose message names `opencode run`",
          a.get("status") == "threw" and a.get("isError") is True and "opencode run" in str(a.get("message")), short(a))
    check("[CP5-T19] an ordinary command ('ls') in the same harness resolves", b.get("status") == "resolved", short(b))


def blocked_by_opencode_rule(blocked, msg):
    """Refused on account of the new rule: a block whose text names `opencode run`."""
    return blocked is True and NEW_RULE_TEXT_RE.search(msg or "") is not None


def case_t20():
    print("[CP5-T20] harmless opencode commands and look-alike words are still allowed")
    w = fresh()
    cmds = ["opencode --version", "opencode models", "ls opencode-notes", "git log --oneline", "python3 ratchet.py status",
            'git commit -m "opencode run notes"']
    for cmd in cmds:
        blocked, msg = engine_guard(w, "opencode", "bash", cmd)
        check(f"[CP5-T20] {cmd!r} is not blocked on account of the opencode rule",
              blocked is not None and not blocked_by_opencode_rule(blocked, msg), (blocked, msg[:160]))
    check("[CP5-T20] teeth: a block that names `opencode run` is told apart from a pass and from an older reason",
          blocked_by_opencode_rule(True, "RATCHET GUARD: ... claude -p, opencode run ...")
          and not blocked_by_opencode_rule(False, "") and not blocked_by_opencode_rule(True, "RATCHET GUARD: old reason")
          and not blocked_by_opencode_rule(None, "garbage"))


def case_t21():
    print("[CP5-T21] the other agents' shell launches are still refused, now naming `opencode run` too")
    w = fresh()
    for host, tool in (("claude", "Bash"), ("codex", "Bash"), ("opencode", "bash")):
        for cmd in ("claude -p x", "codex exec x", "gemini -p x", "copilot -p x"):
            blocked, msg = engine_guard(w, host, tool, cmd)
            check(f"[CP5-T21] {host}: {cmd!r} is still blocked and the refusal text names `opencode run` too",
                  blocked is True and "opencode run" in msg, (blocked, msg[:200]))


def stub_engine(w, stem, body, dirname="stubs"):
    d = w.s.work / dirname
    d.mkdir(exist_ok=True)
    script, log = d / f"{w.s.next(stem)}.py", d / f"{w.s.next(stem)}.log"
    prelude = ("import json, os, sys\nLOG = %r\n\ndef note(**kw):\n    with open(LOG, 'a') as f:\n"
               "        f.write(json.dumps(kw) + '\\n')\n\n") % str(log)
    script.write_text(prelude + body + "\n", encoding="utf-8")
    return script, log


def stub_printing(w, stem, text, code=0, err=""):
    return stub_engine(w, stem, f"note(argv=sys.argv)\nsys.stdin.read()\nsys.stdout.write({text!r})\n"
                                f"sys.stderr.write({err!r})\nsys.exit({code})")


def log_lines(log):
    try:
        return [json.loads(x) for x in Path(log).read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError):
        return []


_STR_RE = re.compile(r'"(?:[^"\\\n]|\\.)*"')


def retargeted_plugin(w, stem, engine_path, src=None):
    """A copy of the installed plugin, in the work folder, whose engine path (a JSON string literal) is `engine_path`.
    Returns (path to the copy, how many literals were replaced)."""
    text = Path(src or w.plugin).read_text(encoding="utf-8")
    hits = [0]

    def sub(m):
        try:
            if json.loads(m.group(0)) == str(ENGINE):
                hits[0] += 1
                return json.dumps(str(engine_path))
        except ValueError:
            pass
        return m.group(0)
    d = w.s.work / "plugins"
    d.mkdir(exist_ok=True)
    p = d / f"{w.s.next(stem)}.js"
    p.write_text(_STR_RE.sub(sub, text), encoding="utf-8")
    return p, hits[0]


def case_t22():
    print("[CP5-T22] a broken engine fails open: the message goes through and nothing is recorded")
    w = fresh()
    missing = str(w.s.work / "no-such-folder" / "ratchet.py")
    garbage, g_log = stub_printing(w, "garbage", "this is not json")
    array, a_log = stub_printing(w, "array", "[1, 2]")
    crash, c_log = stub_printing(w, "crash", "", code=1, err="Traceback (most recent call last): boom")
    weird, e_log = stub_printing(w, "weird", json.dumps({"block": "yes", "message": 42}))
    good, ok_log = stub_printing(w, "good", json.dumps({"block": False, "message": "hello guidance"}))
    cases = [("(a) the engine path does not exist", missing, None), ("(b) garbage on stdout, exit 0", garbage, g_log),
             ("(c) a JSON array", array, a_log), ("(d) exit 1 with a traceback", crash, c_log),
             ("(e) {block:'yes', message:42}", weird, e_log)]
    runs, copies = [], []
    for i, (_l, eng, _lg) in enumerate(cases):
        cp, n = retargeted_plugin(w, f"broken-{i}", eng)
        copies.append(n)
        runs.append(run_of(cp, [typed("approved", mid=f"msg_b{i}")]))
    gp, gn = retargeted_plugin(w, "good", good)
    runs.append(run_of(gp, [typed("approved", mid="msg_ok")]))
    o = drive(w, runs)
    check("[CP5-T22] setup: each plugin copy names the engine as a JSON string literal", all(n >= 1 for n in copies)
          and gn >= 1, copies)
    check("[CP5-T22] setup: every copy has chat.message and the node process was clean (no unhandled rejection)",
          all(o.loaded(i) for i in range(len(runs))) and o.clean(), (o.problems, o.run(0).get("hookNames")))
    for i, (lbl, _eng, lg) in enumerate(cases):
        r = o.step(i, 0)
        check(f"[CP5-T22] {lbl}: the handler resolves and output.parts is unchanged (no garbage part)",
              parts_unchanged(r), short(r))
        if lg is not None:
            check(f"[CP5-T22] {lbl}: the stub engine was really asked", len(log_lines(lg)) >= 1, log_lines(lg))
    check("[CP5-T22] nothing is recorded in human_log.jsonl", log_entries(w) == [], prompts_logged(w))
    g = guidance_appended(o.step(len(cases), 0), "ses_top", "msg_ok")
    check("[CP5-T22] control: a valid stub reply {block:false, message:'hello guidance'} IS appended as the guidance "
          "part (so the unchanged results above are not vacuous)", g is not None and g.get("text") == "hello guidance",
          short(o.step(len(cases), 0)))


def case_t23():
    print("[CP5-T23] odd input never breaks the message or records anything")
    w = fresh()
    base_in = {"sessionID": "ses_top", "messageID": "msg_o", "agent": "build"}

    def raw(inp, out):
        return {"op": "chat", "input": inp, "output": out}
    msg = {"id": "msg_o", "sessionID": "ses_top", "role": "user"}
    odd = [("output.parts undefined", raw(base_in, {"message": msg})),
           ("output.parts null", raw(base_in, {"message": msg, "parts": None})),
           ("output undefined", raw(base_in, js("undefined"))),
           ("input with no sessionID", raw({"messageID": "msg_o"}, {"message": {"id": "msg_o"},
                                                                  "parts": [part("approved", sid=None, mid="msg_o")]})),
           ("a text part whose text is a number", raw(base_in, {"message": msg, "parts": [part(42, mid="msg_o")]})),
           ("a text part whose text is null", raw(base_in, {"message": msg, "parts": [part(None, mid="msg_o")]})),
           ("only a reasoning part", raw(base_in, {"message": msg, "parts": [
               {"id": "prt_r", "sessionID": "ses_top", "messageID": "msg_o", "type": "reasoning", "text": "approved"}]})),
           ("an empty text part", raw(base_in, {"message": msg, "parts": [part("", mid="msg_o")]})),
           ("input undefined", raw(js("undefined"), {"message": msg, "parts": [part("approved", mid="msg_o")]}))]
    lenient = {"mode": "table", "table": TOP, "default": {"data": {}}}   # any other id looks like a top-level session
    o = drive(w, [run_of(w.plugin, [s for _l, s in odd], get=lenient)])
    check("[CP5-T23] setup: plugin loaded with chat.message and the node process was clean (no unhandled rejection)",
          o.loaded() and o.clean(), (o.run(), o.problems))
    for j, (lbl, _s) in enumerate(odd):
        r = o.step(0, j)
        check(f"[CP5-T23] {lbl}: the handler resolves without throwing", r.get("status") == "resolved", short(r))
    sent = o.prompts_sent()
    check("[CP5-T23] no `hook prompt` call carries a non-string or empty prompt",
          all(isinstance(p, str) and p.strip() != "" for p in sent), sent)
    check("[CP5-T23] nothing is recorded in human_log.jsonl", log_entries(w) == [], prompts_logged(w))
    w2 = fresh()
    frozen = js("({message: {id: 'msg_z', sessionID: 'ses_top'}, parts: Object.freeze([{id: 'prt_z', sessionID: 'ses_top', "
                "messageID: 'msg_z', type: 'text', text: 'approved'}])})")
    o2 = drive(w2, [run_of(w2.plugin, [{"op": "chat", "input": {"sessionID": "ses_top", "messageID": "msg_z"},
                                         "output": frozen}])])
    check("[CP5-T23] a frozen output.parts (adding the guidance part would throw inside the handler): the handler still "
          "resolves and the node process stays clean", o2.loaded() and o2.step().get("status") == "resolved"
          and o2.clean(), (o2.step(), o2.problems))


def case_t24():
    print("[CP5-T24] the same message delivered twice is recorded once; two real sends are two replies")
    w = fresh()
    steps = [typed("approved", mid="msg_7"), typed("approved", mid="msg_7"), typed("approved", mid="msg_8"),
             typed("approved", mid="msg_9"), typed("approved", mid=None)]
    o = drive(w, [run_of(w.plugin, steps)])
    check("[CP5-T24] setup: plugin loaded with chat.message and the node process was clean", o.loaded() and o.clean(),
          (o.run(), o.problems))
    ents = log_entries(w)
    check("[CP5-T24] human_log.jsonl holds 4 entries: one for msg_7, one for msg_8, one for msg_9 and one for the "
          "message without an id", len(ents) == 4 and all(e.get("prompt") == "approved" for e in ents), prompts_logged(w))
    check("[CP5-T24] exactly 4 engine calls were made (the repeat of msg_7 was never forwarded)",
          len(o.prompt_calls()) == 4, len(o.prompt_calls()))


def case_t25():
    print("[CP5-T25] a typed message with shell characters, quotes, newlines and unicode is recorded verbatim")
    w = fresh()
    m1, m2 = w.s.work / "pwned-dollar", w.s.work / "pwned-backtick"
    text = f"approved\n$(touch {m1}) `touch {m2}` 'quoted' \"dq\" \u2713 ; rm -rf x"
    long_text = "long reply " + "abcdefghij" * 300
    o = drive(w, [run_of(w.plugin, [typed(text, mid="msg_h"), typed(long_text, mid="msg_l")])])
    check("[CP5-T25] setup: plugin loaded with chat.message and the node process was clean", o.loaded() and o.clean(),
          (o.run(), o.problems))
    got = prompts_logged(w)
    check("[CP5-T25] human_log.jsonl prompt equals the typed text exactly", got[:1] == [text], got[:1])
    check("[CP5-T25] a reply over the engine's 2000-character cap is recorded up to that cap",
          len(got) == 2 and got[1] == long_text[:2000], [len(x) for x in got])
    check("[CP5-T25] the text travelled on stdin, never on a shell command line: neither marker file was created",
          not m1.exists() and not m2.exists(), (m1.exists(), m2.exists()))


def case_t26():
    print("[CP5-T26] every text the plugin sends itself carries the marker, however odd the reason is")
    w = fresh()
    reasons = ["", "x" * 50000, "\nstarts with a newline", f"{PREFIX} sneaky reason"]
    seam_steps = [seam_step("" if i == 0 else (js("'x'.repeat(50000)") if i == 1 else r), f"s{i}")
                  for i, r in enumerate(reasons)]
    chats = [chat([part(var(f"s{i}"), mid=f"msg_p{i}")], mid=f"msg_p{i}") for i in range(len(reasons))]
    o = drive(w, [run_of(w.plugin, seam_steps + chats)])
    check("[CP5-T26] setup: plugin loaded with chat.message and a seam, node process clean",
          o.loaded() and o.seam() is not None and o.clean(), (o.run().get("hookNames"), o.seam(), o.problems))
    texts = [o.step(0, i).get("text") for i in range(len(reasons))]
    for i, reason in enumerate(reasons):
        check(f"[CP5-T26] reason #{i + 1} ({len(reason)} chars): first line is the fixed prefix plus a token, the reason "
              "follows after that line", marked_ok(texts[i], reason), repr(texts[i])[:160])
    toks = {marker_token(t) for t in texts}
    check("[CP5-T26] every result carries the same token (this load's)", len(toks) == 1 and None not in toks, toks)
    for i in range(len(reasons)):
        check(f"[CP5-T26] reason #{i + 1} fed back through chat.message resolves with output.parts unchanged",
              parts_unchanged(o.step(0, len(reasons) + i)), short(o.step(0, len(reasons) + i)))
    check("[CP5-T26] none of them reaches the engine (no `hook prompt` call) and nothing is recorded",
          o.prompt_calls() == [] and log_entries(w) == [], (o.prompts_sent(), prompts_logged(w)))


def case_t27():
    print("[CP5-T27] the guard from the earlier step still works with the prompt handler added")
    w = fresh()
    state = str(w.proj / ".ratchet" / "runs" / "hk" / "state.json")
    steps = [guard_step("write", {"filePath": state, "content": "{}"})]
    missing = w.s.work / "no-such-folder" / "ratchet.py"
    cp, n = retargeted_plugin(w, "t27-broken", missing)
    o = drive(w, [run_of(w.plugin, steps, label="real"), run_of(cp, steps, label="engine broken")])
    check("[CP5-T27] setup: both plugins have the guard handler AND the new chat.message handler, node process clean",
          o.loaded(0, "tool.execute.before") and o.loaded(0) and o.loaded(1, "tool.execute.before") and o.loaded(1)
          and n >= 1 and o.clean(), (o.run(0).get("hookNames"), o.run(1).get("hookNames"), o.problems))
    a = o.step(0, 0)
    check("[CP5-T27] a write of a protected run file throws with Ratchet's reason",
          a.get("status") == "threw" and a.get("isError") is True and "RATCHET" in str(a.get("message")), short(a))
    check("[CP5-T27] with the engine path broken the same tool call resolves (fail open)",
          o.step(1, 0).get("status") == "resolved", short(o.step(1, 0)))


# ------------------------------------------------------------------------------------------ README and docs cases

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
    with _tmp.mkdtemp(prefix="ratchet-ocprompt-probe-") as cwd:
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


def case_t28():
    print("[CP5-T28] the README table says OpenCode records your replies")
    text = README.read_text(encoding="utf-8")
    rows = md_table_rows(first_table(cli_section(text)))
    by_name = {r[0]: r for r in rows[1:] if r}
    cells = by_name.get("OpenCode", [])
    check("[CP5-T28] setup: the per-CLI table has an OpenCode row with five cells", len(cells) == 5, list(by_name))
    check("[CP5-T28] the OpenCode 'Records your replies' cell says Yes", len(cells) == 5 and says_yes(cells[4]),
          cells[4] if len(cells) == 5 else cells)
    enf = host_enforcement()
    oc = (enf or {}).get("opencode") or {}
    check("[CP5-T28] HOST_ENFORCEMENT['opencode'] has replies and guidance true, in step with the cell",
          oc.get("replies") is True and oc.get("guidance") is True, oc)
    check("[CP5-T28] the 'Stop hook' cell still does not claim a hard stop (it does not start with Yes)",
          len(cells) == 5 and not says_yes(cells[3]), cells[3] if len(cells) == 5 else cells)
    check("[CP5-T28] teeth: a 'No, ...' cell and an empty cell are not 'Yes'",
          not says_yes("No, the prompt hook does not record your replies.") and not says_yes("")
          and says_yes("Yes, the prompt hook records your replies."))


def case_t29():
    print("[CP5-T29] the README 'Running the tests' list names the new test file")
    text = README.read_text(encoding="utf-8")
    block = readme_test_block()
    lst = [ln for ln in block if ln.startswith("python3 tests/")]
    check("[CP5-T29] the 'Running the tests' list holds the exact line exactly once, and it appears nowhere else",
          block.count(NEW_TEST_LINE) == 1 and text.splitlines().count(NEW_TEST_LINE) == 1, block.count(NEW_TEST_LINE))
    if NEW_TEST_LINE in lst:
        i = lst.index(NEW_TEST_LINE)
        ctx = lst[max(0, i - 1):i + 2]
        check("[CP5-T29] its previous neighbour in the list sorts before it", i == 0 or lst[i - 1] < NEW_TEST_LINE, ctx)
        check("[CP5-T29] its next neighbour in the list sorts after it", i + 1 >= len(lst) or lst[i + 1] > NEW_TEST_LINE,
              ctx)
        without = [x for x in lst if x != NEW_TEST_LINE]
        check("[CP5-T29] adding the line adds no out-of-order pair to the list",
              inversions(lst) == inversions(without), inversions(lst))
    else:
        for n in ("its previous neighbour in the list sorts before it", "its next neighbour in the list sorts after it",
                  "adding the line adds no out-of-order pair to the list"):
            check(f"[CP5-T29] {n}", False, "line missing")


def schemas_section_10():
    text = SCHEMAS.read_text(encoding="utf-8")
    m = re.search(r"^## 10\. Hooks.*?(?=^## \d+\.)", text, re.S | re.M)
    return m.group(0) if m else ""


def case_t30():
    print("[CP5-T30] the marker is documented where the other host-sent turns are")
    sec = schemas_section_10()
    check("[CP5-T30] setup: schemas.md has section 10 with the 'Turns the agent CLI sends on its own' paragraph",
          "Turns the agent CLI sends on its own" in sec, len(sec))
    check("[CP5-T30] section 10 lists the fixed prefix `[RATCHET PLUGIN TURN - NOT USER INPUT]`", PREFIX in sec)
    check("[CP5-T30] section 10 names the `ratchet_plugin_turn` payload field", "ratchet_plugin_turn" in sec)
    own = THIS_FILE.read_text(encoding="utf-8")
    check("[CP5-T30] this file runs processes and reads README and schemas.md as plain text only: it holds none of the "
          "strings the test inventory looks for, so it needs no inventory entry", inventory_hits(own) == [],
          inventory_hits(own))
    check("[CP5-T30] teeth: the inventory-string finder does spot them in a sample that loads ratchet.py",
          inventory_hits("importlib.util.spec_from" + "_file_location('x', p)") != [])


# ------------------------------------------------------------------------------------------------------ teeth

MUTANT_JS = r"""
import { spawnSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
const E = __ENGINE__;
const PREFIX = '[RATCHET PLUGIN TURN - NOT USER INPUT]';
const TOKEN = __TOKEN__;
const OPTS = __OPTS__;
const seen = new Set();
export const RatchetPlugin = async (ctx) => ({
  'tool.execute.before': async () => {},
  ratchetMarkedText: (reason) => PREFIX + ' ' + TOKEN + '\n' + reason,
  'chat.message': async (input, output) => {
    try {
      const parts = (output && Array.isArray(output.parts)) ? output.parts : [];
      const texts = parts.filter((p) => p && p.type === 'text' && !p.synthetic && typeof p.text === 'string' && p.text !== '').map((p) => p.text);
      if (!texts.length) return;
      const text = texts.join('\n');
      if (OPTS.filter && text.includes(TOKEN)) return;
      if (OPTS.child) {
        let parent = null;
        try {
          const r = await ctx.client.session.get({ path: { id: input.sessionID } });
          if (!r || typeof r !== 'object' || !r.data || typeof r.data !== 'object') return;
          parent = r.data.parentID;
        } catch (e) { return; }
        if (parent) return;
      }
      const mid = input.messageID;
      if (OPTS.dedup && mid) { if (seen.has(mid)) return; seen.add(mid); }
      if (!OPTS.forward) return;
      const r = spawnSync('python3', [E, 'hook', 'prompt', '--host', 'opencode'], { input: JSON.stringify({
        hook_event_name: 'chat.message', session_id: input.sessionID, cwd: ctx.directory, prompt: text }), encoding: 'utf8' });
      if (r.status !== 0) return;
      let reply; try { reply = JSON.parse(r.stdout); } catch (e) { return; }
      if (OPTS.guidance && reply && typeof reply.message === 'string' && reply.message !== '') {
        output.parts.push({ id: 'prt_ratchet_' + Date.now(), sessionID: input.sessionID, messageID: mid, type: 'text', text: reply.message, synthetic: true });
      }
    } catch (e) { void e; }
  },
});
"""
GOOD = {"filter": True, "child": True, "dedup": True, "forward": True, "guidance": True, "static": False}
MUTANTS = {
    "reference": {},
    "records everything": {"filter": False, "child": False, "dedup": False},
    "never records": {"forward": False},
    "forwards the plugin's own turns": {"filter": False},
    "static token": {"static": True},
    "records child sessions": {"child": False},
    "no dedup": {"dedup": False},
    "no guidance": {"guidance": False},
}


def mutant_plugin(w, name):
    opts = {**GOOD, **MUTANTS[name]}
    token = json.dumps("static-token-aaaaaaaaaaaaaaaaaaaa") if opts["static"] else "randomUUID()"
    src = (MUTANT_JS.replace("__ENGINE__", json.dumps(str(ENGINE))).replace("__TOKEN__", token)
           .replace("__OPTS__", json.dumps(opts)))
    d = w.s.work / "mutants"
    d.mkdir(exist_ok=True)
    p = d / f"{w.s.next('mutant')}.js"
    p.write_text(src, encoding="utf-8")
    return p


def mutant_facts(name):
    """Run one mutant (or the reference) through the same driver: a typed message, the plugin's own text, a child
    session's message, a repeated message, and a second load. Returns the facts the cases above rely on."""
    w = fresh()
    p = mutant_plugin(w, name)
    steps = [typed("tooth-typed", mid="msg_a"), seam_step("tooth-reason", "mk"),
             chat([part(var("mk"), mid="msg_p")], mid="msg_p"), typed("tooth-child", sid="ses_child", mid="msg_c"),
             typed("tooth-dup", mid="msg_d"), typed("tooth-dup", mid="msg_d")]
    o = drive(w, [run_of(p, steps, label="A"), run_of(p, [seam_step("tooth-reason")], label="B")])
    logged = prompts_logged(w)
    return {"loaded": o.loaded() and o.loaded(1) and o.seam() is not None and o.clean(),
            "typed_recorded": "tooth-typed" in logged,
            "marker_forwarded": forwarded_marker(o),
            "plugin_turn_recorded": any("tooth-reason" in str(x) for x in logged),
            "child_recorded": "tooth-child" in logged,
            "dup_count": logged.count("tooth-dup"),
            "guidance": guidance_appended(o.step(0, 0), "ses_top", "msg_a") is not None,
            "tokens_differ": (marker_token(o.step(0, 1).get("text")) is not None
                              and marker_token(o.step(0, 1).get("text")) != marker_token(o.step(1, 0).get("text")))}


def case_teeth():
    print("teeth: the predicates reject the wrong behavior (mutant plugins run through the same driver)")
    facts = {name: mutant_facts(name) for name in MUTANTS}
    ref = facts["reference"]
    check("[CP5-T01] teeth: reference plugin: loads, records the typed message, and the typed message gets guidance",
          ref["loaded"] and ref["typed_recorded"] and ref["guidance"], ref)
    check("[CP5-T01] teeth: a plugin that never records fails the typed-message-is-recorded check",
          facts["never records"]["loaded"] and not facts["never records"]["typed_recorded"], facts["never records"])
    check("[CP5-T03] teeth: a plugin that appends no guidance part fails the guidance check",
          facts["no guidance"]["loaded"] and facts["no guidance"]["typed_recorded"]
          and not facts["no guidance"]["guidance"], facts["no guidance"])
    check("[CP5-T03] teeth: the guidance checker rejects a part that is not synthetic, is not last, or is not in place",
          guidance_appended({"status": "resolved", "sameParts": True, "sameFirst": True, "sameMessage": True,
                             "partsBefore": [{"a": 1}], "partsAfter": [{"a": 1}, {"type": "text", "synthetic": False,
                              "id": "x", "sessionID": "s", "messageID": "m", "text": "t"}]}, "s", "m") is None
          and guidance_appended({"status": "resolved", "sameParts": False, "sameFirst": True, "sameMessage": True,
                                 "partsBefore": [{"a": 1}], "partsAfter": [{"a": 1}, {"type": "text", "synthetic": True,
                                  "id": "x", "sessionID": "s", "messageID": "m", "text": "t"}]}, "s", "m") is None
          and guidance_appended({"status": "resolved", "sameParts": True, "sameFirst": True, "sameMessage": True,
                                 "partsBefore": [{"a": 1}], "partsAfter": [{"type": "text", "synthetic": True,
                                  "id": "x", "sessionID": "s", "messageID": "m", "text": "t"}, {"a": 1}]}, "s", "m") is None
          and guidance_appended({"status": "resolved", "sameParts": True, "sameFirst": True, "sameMessage": True,
                                 "partsBefore": [{"a": 1}], "partsAfter": [{"a": 1}, {"type": "text", "synthetic": True,
                                  "id": "x", "sessionID": "s", "messageID": "m", "text": "t"}]}, "s", "m") is not None)
    ref_ok = (ref["loaded"] and not ref["marker_forwarded"] and not ref["plugin_turn_recorded"])
    check("[CP5-T06] teeth: reference plugin never forwards or records its own turn", ref_ok, ref)
    for name in ("forwards the plugin's own turns", "records everything"):
        f = facts[name]
        check(f"[CP5-T06] teeth: '{name}' is caught: its own turn reaches the engine (and, until the engine drops it, "
              "the log)", f["loaded"] and f["marker_forwarded"], f)
    check("[CP5-T09] teeth: reference tokens differ between two loads; a static-token plugin is caught",
          ref["tokens_differ"] and facts["static token"]["loaded"] and not facts["static token"]["tokens_differ"],
          (ref["tokens_differ"], facts["static token"]))
    check("[CP5-T14] teeth: reference plugin does not record a child session's message; a plugin that records child "
          "sessions is caught", ref["loaded"] and not ref["child_recorded"] and facts["records child sessions"]["loaded"]
          and facts["records child sessions"]["child_recorded"] and facts["records everything"]["child_recorded"],
          (ref["child_recorded"], facts["records child sessions"]))
    check("[CP5-T24] teeth: reference plugin records a repeated message once; a plugin without dedup is caught",
          ref["dup_count"] == 1 and facts["no dedup"]["dup_count"] == 2 and facts["records everything"]["dup_count"] == 2,
          (ref["dup_count"], facts["no dedup"]["dup_count"]))
    check("[CP5-T26] teeth: the marked-text predicate accepts a marked text and rejects unmarked or short-token texts",
          marked_ok(f"{PREFIX} {UUID_SAMPLE}\nreason", "reason") and not marked_ok("reason")
          and not marked_ok(f"{PREFIX} short\nreason") and not marked_ok(f"reason\n{PREFIX} {UUID_SAMPLE}")
          and not marked_ok(f"{PREFIX} {UUID_SAMPLE}\nother", "reason") and not marked_ok(None))
    check("[CP5-T15] teeth: the 'output.parts unchanged' predicate rejects an added part, a replaced array and a throw",
          parts_unchanged({"status": "resolved", "partsBefore": [1], "partsAfter": [1], "sameParts": True,
                           "sameFirst": True})
          and not parts_unchanged({"status": "resolved", "partsBefore": [1], "partsAfter": [1, 2], "sameParts": True,
                                   "sameFirst": True})
          and not parts_unchanged({"status": "resolved", "partsBefore": [1], "partsAfter": [1], "sameParts": False,
                                   "sameFirst": True})
          and not parts_unchanged({"status": "threw", "partsBefore": [1], "partsAfter": [1], "sameParts": True,
                                   "sameFirst": True}))
    w = fresh("building")
    stops = (stop_answer(w), stop_answer(w))
    check("[CP5-T11] teeth: the stop-answer reader sees a refused stop (block true) from a live building run",
          all(a.get("block") is True for a in stops), stops)


# ------------------------------------------------------------------------------ review-round cases (CP5-X10..X14)
# Added after the first adversarial round. X10: a failed try is retried (the plugin marks a message id seen only after
# the session was verified and the engine answered; two simultaneous deliveries of one id still go out once). X11: two
# copies of the plugin (user and project folder) deliver one message and it is recorded once, with one guidance part.
# X12: the `opencode run` shell rule counts `opencode` only as the command word. X13: a synthetic text part in the
# top-level session is never read as the user's text. X14: the guidance part's id sorts after every part of the message.

XDRIVER_JS = r"""
import { pathToFileURL } from 'node:url';
import fs from 'node:fs';
const scen = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const problems = [];
process.on('unhandledRejection', (e) => { problems.push('unhandledRejection: ' + ((e && e.message) || String(e))); });
process.on('uncaughtException', (e) => { problems.push('uncaughtException: ' + ((e && e.message) || String(e))); });
const sn = (v) => (v === undefined ? '<undefined>' : JSON.parse(JSON.stringify(v)));
let loads = 0;
const out = [];
for (const run of scen.runs) {
  const calls = [];
  let gets = 0;
  const client = { session: { get: (opts) => {
    gets++;
    calls.push({ fn: 'session.get', opts: sn(opts) });
    if (run.hang) return new Promise(() => {});
    if (gets <= (run.failFirst || 0)) return Promise.reject(new Error('session lookup failed'));
    return Promise.resolve({ data: { id: opts.path.id } });
  } } };
  const mod = await import(pathToFileURL(run.plugin).href + '?load=' + (loads++));
  const hooks = await mod.RatchetPlugin({ client, directory: scen.dir, worktree: scen.dir });
  const steps = [];
  for (const group of run.groups) {
    const results = await Promise.all(group.map(async (d) => {
      const output = JSON.parse(JSON.stringify(d.output));
      const origParts = output.parts;
      const r = { partsBefore: sn(origParts), sameFirst: true, sameMessage: true };
      let t;
      const capped = new Promise((_, rej) => { t = setTimeout(() => rej(new Error('driver cap')), run.capMs || 120000); });
      try { await Promise.race([hooks['chat.message'](d.input, output), capped]); r.status = 'resolved'; }
      catch (e) { r.status = (e && e.message === 'driver cap') ? 'cap' : 'threw'; }
      finally { clearTimeout(t); }
      r.partsAfter = sn(output.parts);
      r.sameParts = output.parts === origParts;
      return r;
    }));
    steps.push(...results);
  }
  out.push({ factory: 'ok', hookNames: Object.keys(hooks), steps, calls });
}
for (let i = 0; i < 25; i++) { await new Promise((r) => setImmediate(r)); await new Promise((r) => setTimeout(r, 1)); }
fs.writeFileSync(scen.out, JSON.stringify({ runs: out, problems }));
process.exit(0);
"""


def drive_x(w, runs):
    """Like drive(), with the small driver above: a session lookup that fails its first `failFirst` calls, and groups of
    deliveries (each group is delivered at the same time, the groups one after another)."""
    need_node()
    s = w.s
    drv = s.work / "xdriver.mjs"
    drv.write_text(XDRIVER_JS, encoding="utf-8")
    n = s.next("xscenario")
    scen, out_path, spy_path = s.work / f"{n}.json", s.work / f"{n}.out.json", s.work / f"{n}.spy.log"
    scen.write_text(json.dumps({"dir": str(w.proj), "out": str(out_path), "runs": runs}), encoding="utf-8")
    env = w.env(PATH=str(s.spybin) + os.pathsep + os.environ.get("PATH", ""), OCSPY_LOG=str(spy_path))
    p = subprocess.run([NODE, str(drv), str(scen)], cwd=str(s.work), env=env, capture_output=True, text=True, timeout=240)
    return Out(p, out_path, spy_path)


def delivery(text, mid, sid="ses_top"):
    c = chat([part(text, sid, mid)], sid, mid)
    return {"input": c["input"], "output": c["output"]}


def failing_once_engine(w, stem, mode):
    """A stand-in engine whose FIRST call fails ('crash': exit 1; 'garbage': exit 0 with text that is not JSON) and
    whose later calls run the real engine on the same input. Returns (path, its call log)."""
    body = (f"n = len(open(LOG).read().splitlines()) if os.path.exists(LOG) else 0\nnote(argv=sys.argv)\n"
            f"data = sys.stdin.read()\nif n < 1:\n    if {mode!r} == 'crash':\n        sys.stderr.write('boom')\n"
            "        sys.exit(1)\n    sys.stdout.write('this is not json')\n    sys.exit(0)\nimport subprocess\n"
            f"p = subprocess.run([{sys.executable!r}, {str(ENGINE)!r}] + sys.argv[1:], input=data, capture_output=True, "
            "text=True)\nsys.stdout.write(p.stdout)\nsys.exit(p.returncode)")
    return stub_engine(w, stem, body)


def case_x10():
    print("[CP5-X10] a failed try is retried: a message id counts as seen only after the session was verified and the "
          "engine answered")
    w = fresh()
    o = drive_x(w, [{"plugin": str(w.plugin), "failFirst": 1, "groups": [[delivery("approved", "msg_r1")],
                                                                            [delivery("approved", "msg_r1")]]}])
    check("[CP5-X10] setup: plugin loaded and the node process was clean", o.loaded() and o.clean(), (o.run(), o.problems))
    check("[CP5-X10] the session lookup fails once, then the same message id arrives again: the first try changes nothing",
          parts_unchanged(o.step(0, 0)) and len(o.gets()) == 2, (short(o.step(0, 0)), len(o.gets())))
    check("[CP5-X10] the retry is recorded once and gets its guidance part",
          prompts_logged(w) == ["approved"] and guidance_appended(o.step(0, 1), "ses_top", "msg_r1") is not None,
          (prompts_logged(w), short(o.step(0, 1))))
    w3 = fresh()
    o3 = drive_x(w3, [{"plugin": str(w3.plugin), "groups": [[delivery("approved", "msg_r3")], [delivery("approved", "msg_r3")],
                                                              [delivery("approved", "msg_r4")]]}])
    check("[CP5-X10] a repeat after success is still dropped: msg_r3 twice and msg_r4 once are two replies",
          o3.clean() and len(prompts_logged(w3)) == 2 and len(o3.prompt_calls()) == 2,
          (prompts_logged(w3), len(o3.prompt_calls())))
    for mode in ("crash", "garbage"):
        w2 = fresh()
        stub, slog = failing_once_engine(w2, f"x10-{mode}", mode)
        cp, n = retargeted_plugin(w2, f"x10-{mode}", stub)
        o2 = drive_x(w2, [{"plugin": str(cp), "groups": [[delivery("approved", "msg_e1")], [delivery("approved", "msg_e1")]]}])
        check(f"[CP5-X10] setup ({mode}): the plugin copy names the stub engine and the node process was clean",
              n >= 1 and o2.loaded() and o2.clean(), (n, o2.problems))
        check(f"[CP5-X10] engine {mode} on the first try, then the same id again: the first try changes nothing",
              parts_unchanged(o2.step(0, 0)) and len(log_lines(slog)) == 2, (short(o2.step(0, 0)), log_lines(slog)))
        check(f"[CP5-X10] engine {mode}: the retry is recorded once, with its guidance part",
              prompts_logged(w2) == ["approved"] and guidance_appended(o2.step(0, 1), "ses_top", "msg_e1") is not None,
              (prompts_logged(w2), short(o2.step(0, 1))))
    wc = fresh()
    oc = drive_x(wc, [{"plugin": str(wc.plugin), "groups": [[delivery("approved", "msg_c1"), delivery("approved", "msg_c1")]]}])
    check("[CP5-X10] two deliveries of one id at the same time: ONE engine call and one recorded reply",
          oc.clean() and len(oc.prompt_calls()) == 1 and prompts_logged(wc) == ["approved"],
          (len(oc.prompt_calls()), prompts_logged(wc)))
    both = [oc.step(0, 0), oc.step(0, 1)]
    check("[CP5-X10] exactly one of the two simultaneous deliveries got the guidance part",
          sum(1 for r in both if guidance_appended(r, "ses_top", "msg_c1") is not None) == 1
          and sum(1 for r in both if parts_unchanged(r)) == 1, [short(r) for r in both])


def case_x11():
    print("[CP5-X11] two copies of the plugin (user and project folder) deliver one message: recorded once, one guidance")
    w = fresh()
    o = drive(w, [run_of(w.plugin, [typed("approved", mid="msg_x1")], label="A"),
                  run_of(w.plugin, [typed("approved", mid="msg_x1"), typed("later words", mid="msg_x2")], label="B")])
    check("[CP5-X11] setup: both loads have chat.message and the node process was clean",
          o.loaded(0) and o.loaded(1) and o.clean(), (o.problems,))
    check("[CP5-X11] every delivery reached the engine (the two copies' dedup sets are separate)",
          len(o.prompt_calls()) == 3, len(o.prompt_calls()))
    ents = log_entries(w)
    check("[CP5-X11] one message delivered by both copies is ONE human_log.jsonl entry, and it carries its message_id",
          [e.get("prompt") for e in ents[:1]] == ["approved"] and ents[0].get("message_id") == "msg_x1"
          and len([e for e in ents if e.get("message_id") == "msg_x1"]) == 1, ents)
    ga, gb = guidance_appended(o.step(0, 0), "ses_top", "msg_x1"), o.step(1, 0)
    check("[CP5-X11] the agent gets exactly one guidance part for it: the first copy's stands, the second adds none",
          ga is not None and parts_unchanged(gb), (short(o.step(0, 0)), short(gb)))
    check("[CP5-X11] a different id from the same session is a second reply",
          [e.get("prompt") for e in ents] == ["approved", "later words"] and ents[1].get("message_id") == "msg_x2"
          and guidance_appended(o.step(1, 1), "ses_top", "msg_x2") is not None, (prompts_logged(w), short(o.step(1, 1))))
    rc, text = approve_plan(w)
    check("[CP5-X11] the log's hash chain is intact and the first reply still approves the plan", rc == 0, (rc, text[-200:]))
    w2 = fresh()
    r1 = engine_prompt(w2, payload(w2, "approved", message_id="m1"))
    r2 = engine_prompt(w2, payload(w2, "approved", message_id="m1"))
    engine_prompt(w2, payload(w2, "approved", sid="ses_other", message_id="m1"))
    engine_prompt(w2, payload(w2, "approved"))
    engine_prompt(w2, payload(w2, "approved"))
    check("[CP5-X11] engine: a repeated (session, message_id) is skipped with exit 0 and an empty message",
          one_json_reply(*r2) and r2[1].get("block") is False and r2[1].get("message") == ""
          and r1[1].get("message", "") != "", (r1[1], r2[1]))
    got = log_entries(w2)
    check("[CP5-X11] engine: the same id from another session, and replies with no id, are each recorded as before",
          [(e.get("session"), e.get("message_id")) for e in got]
          == [("ses_top", "m1"), ("ses_other", "m1"), ("ses_top", None), ("ses_top", None)]
          and "message_id" not in got[2], got)


def case_x12():
    print("[CP5-X12] the `opencode run` shell rule counts `opencode` only as the command word")
    w = fresh()
    allowed = ["bun --cwd packages/opencode run dev", "npm --prefix packages/opencode run build",
               "pnpm -C packages/opencode run test", "pytest tests/opencode -k run",
               "docker compose -f opencode/compose.yml run app", "echo opencode run", "opencode run-tests",
               "opencode runs", "opencode serve", "cd packages/opencode && bun run test"]
    refused = ["opencode run hi", "OPENCODE=1 opencode run hi", "/usr/local/bin/opencode run x", "npx opencode run x",
               "command opencode run x", "npx opencode-ai run hi", "bunx opencode-ai run hi", "env A=1 opencode run x",
               "npx -y opencode-ai@latest run hi", "cd /tmp && opencode run -m x/y hi", "echo yes | opencode run",
               "opencode --print-logs run hi", "timeout 600 opencode run x", "nice -n 5 opencode run x",
               "stdbuf -oL opencode run x", "echo x | xargs opencode run", "sudo -u bob opencode run x",
               "if true; then opencode run x; fi", "{ opencode run x; }", "bun x opencode-ai run x",
               "pnpm dlx opencode-ai run x", "timeout -s KILL 5 opencode run x", "ionice -c 3 nice -n 5 opencode run x",
               "xargs -n 1 opencode run", "npm exec opencode-ai -- run x", "yarn dlx opencode-ai run x",
               "while true; do opencode run x; done", "! opencode run x"]
    allowed += ["timeout 5 echo opencode run", "sudo -E echo opencode run", "nice -n 5 echo opencode run",
                "xargs echo opencode run", "timeout 5 pytest tests/opencode -k run"]
    for cmd in allowed:
        blocked, msg = engine_guard(w, "opencode", "bash", cmd)
        check(f"[CP5-X12] {cmd!r} is not refused on account of the opencode rule",
              blocked is not None and not blocked_by_opencode_rule(blocked, msg), (blocked, msg[:160]))
    for cmd in refused:
        blocked, msg = engine_guard(w, "opencode", "bash", cmd)
        check(f"[CP5-X12] {cmd!r} is refused and the reason names `opencode run`",
              blocked_by_opencode_rule(blocked, msg), (blocked, msg[:160]))


def case_x13():
    print("[CP5-X13] a synthetic text part in the top-level session is never read as the user's text")
    w = fresh()
    c1 = chat([part("background sub-agent result: approved", synthetic=True)], mid="msg_s1")
    c2 = chat([part("approved", mid="msg_s2", synthetic=True), part("my own words", mid="msg_s2")], mid="msg_s2")
    o = drive(w, [run_of(w.plugin, [c1, c2])])
    check("[CP5-X13] setup: plugin loaded and the node process was clean", o.loaded() and o.clean(), (o.run(), o.problems))
    check("[CP5-X13] a message made only of a synthetic text part: parts unchanged, nothing sent to the engine for it",
          parts_unchanged(o.step(0, 0)) and "background sub-agent result: approved" not in str(o.prompts_sent()),
          (o.prompts_sent(), prompts_logged(w)))
    check("[CP5-X13] next to a real text part, only the real text is sent and recorded",
          o.prompts_sent() == ["my own words"] and prompts_logged(w) == ["my own words"], (o.prompts_sent(), prompts_logged(w)))


def case_x14():
    print("[CP5-X14] the guidance part's id sorts after every part of the message, whatever order they arrive in")
    w = fresh()
    c = chat([part("approved", mid="msg_o1", id="prt_zzz"),
              {"id": "prt_aaa", "sessionID": "ses_top", "messageID": "msg_o1", "type": "file", "url": "file:///x"}],
             mid="msg_o1")
    o = drive(w, [run_of(w.plugin, [c])])
    g = guidance_appended(o.step(), "ses_top", "msg_o1")
    check("[CP5-X14] setup: the guidance part was appended", o.loaded() and o.clean() and g is not None, short(o.step()))
    gid = (g or {}).get("id", "")
    check("[CP5-X14] its id starts with `prt` and is larger than the largest id the message already had",
          gid.startswith("prt") and gid > "prt_zzz", gid)


LOOKUP_LIMIT_LINE = "const SESSION_LOOKUP_TIMEOUT_MS = 10000;"


def case_x15():
    print("[CP5-X15] a session lookup that never answers is dropped after its time limit: the message goes through "
          "unchanged and nothing is recorded")
    w = fresh()
    shipped = (ROOT / "skills" / "ratchet-orchestrator" / "assets" / "opencode-plugin.js").read_text(encoding="utf-8")
    check("[CP5-X15] the shipped plugin's lookup limit is exactly 10000 ms, set once (the test copy's retarget cannot hide drift)",
          shipped.count(LOOKUP_LIMIT_LINE) == 1 and len(re.findall(r"SESSION_LOOKUP_TIMEOUT_MS\s*=", shipped)) == 1,
          re.findall(r".*SESSION_LOOKUP_TIMEOUT_MS\s*=.*", shipped)[:3])
    d = w.s.work / "plugins"
    d.mkdir(exist_ok=True)
    copy = d / f"{w.s.next('shortlookup')}.js"
    text = w.plugin.read_text(encoding="utf-8")
    patched = text.replace(LOOKUP_LIMIT_LINE, "const SESSION_LOOKUP_TIMEOUT_MS = 300;")
    check("[CP5-X15] setup: the retarget matched exactly once in this test's own copy",
          text.count(LOOKUP_LIMIT_LINE) == 1 and "SESSION_LOOKUP_TIMEOUT_MS = 300;" in patched, text.count(LOOKUP_LIMIT_LINE))
    copy.write_text(patched, encoding="utf-8")
    o = drive_x(w, [{"plugin": str(copy), "hang": True, "capMs": 5000,
                     "groups": [[delivery("approved", "msg_h1")]]}])
    check("[CP5-X15] setup: plugin loaded and the node process was clean", o.loaded() and o.clean(), (o.run(), o.problems))
    r = o.step(0, 0)
    check("[CP5-X15] the handler resolves (it is not left waiting) and output.parts is unchanged",
          r.get("status") == "resolved" and parts_unchanged(r), short(r))
    check("[CP5-X15] the lookup was made once and nothing was recorded or sent to the engine",
          len(o.gets()) == 1 and prompts_logged(w) == [] and o.prompts_sent() == [], (len(o.gets()), prompts_logged(w)))


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
    plan = [(globals()[f"case_t{n:02d}"], (f"CP5-T{n:02d}",)) for n in range(1, 31)]
    plan.append((case_teeth, ("CP5-T01", "CP5-T03", "CP5-T06", "CP5-T09", "CP5-T11", "CP5-T14", "CP5-T15", "CP5-T24",
                              "CP5-T26")))
    for fn, ids in ((case_x10, ("CP5-X10",)), (case_x11, ("CP5-X11",)), (case_x12, ("CP5-X12",)),
                    (case_x13, ("CP5-X13",)), (case_x14, ("CP5-X14",)), (case_x15, ("CP5-X15",))):
        plan.append((fn, ids))
    for fn, ids in plan:
        guarded(fn, *ids)
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed ({time.time() - started:.0f} s)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
